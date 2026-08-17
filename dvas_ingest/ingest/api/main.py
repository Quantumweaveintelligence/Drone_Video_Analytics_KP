"""
main.py
=======
Kerala Police Drone Ingest Server — Phase 1
FastAPI application with full endpoint suite.

Endpoints
---------
  GET  /health              — Liveness (always returns 200 quickly)
  GET  /health/ready        — Readiness (checks Redis + Keycloak)
  POST /ingest/frame        — Authenticated frame upload  [JWT required]
  GET  /ingest/status       — Aggregate status for all drones [JWT required]
  GET  /ingest/status/{id}  — Per-drone queue + ingest stats  [JWT required]
  POST /ingest/mission/start — Register mission start  [JWT required]
  POST /ingest/mission/end   — Register mission end    [JWT required]
  GET  /ingest/frame/{id}/latest  — Latest JPEG snapshot for a drone
  GET  /ingest/stream/{id}  — Live MJPEG stream for a drone (for dashboard video)
  GET  /metrics              — Prometheus metrics (text/plain)
  WS   /ws/status            — Live status WebSocket for control-room dashboard
  GET  /docs                 — Swagger UI (internal dev only)
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

import httpx
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, JSONResponse, Response, StreamingResponse
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

from auth import (
    extract_username,
    validate_token,
    validate_token_flexible,
    validate_token_string,
)
from config import (
    ALLOWED_TYPES,
    API_DESC,
    API_TITLE,
    API_VERSION,
    KNOWN_DRONES,
    JWKS_URL,
    MAX_FRAME_BYTES,
)
from models import (
    DroneQueueStats,
    DroneStatusResponse,
    HealthResponse,
    IngestResponse,
    IngestStats,
    MissionEndRequest,
    MissionResponse,
    MissionStartRequest,
    ReadinessResponse,
    SystemStatusResponse,
    WSStatusEvent,
    ComponentHealth,
)
import datetime

# ── Queue manager (Redis Streams) ─────────────────────────────────────────────
from queue_manager import get_queue_manager

_qm = None   # initialised in lifespan

async def _publish_frame(drone_id, metadata, data):
    return await _qm.publish_frame(drone_id, data, metadata)

async def _publish_event(event_type, drone_id, payload):
    await _qm.publish_event({"event": event_type, "drone_id": drone_id, **payload})

async def _is_redis_healthy():
    return await _qm.is_healthy() if _qm else False

# In-memory ingest stats (replaces old queue_manager counters)
_ingest_stats: dict = {}   # drone_id → {frames_received, frames_queued, bytes_ingested, ...}
_rejections:   dict = {}   # drone_id → int
_latest_metadata: dict = {}  # drone_id → {lat, lng, battery_pct, speed_mps, altitude_m, ...}

def _record_frame(drone_id, n_bytes, mission_id=None, frames_total=None):
    s = _ingest_stats.setdefault(drone_id, {
        "frames_received": 0, "frames_queued": 0,
        "frames_rejected": 0, "bytes_ingested": 0,
        "frames_total": 0,
        "last_frame_at": None, "mission_id": None,
    })
    s["frames_received"] += 1
    s["frames_queued"]   += 1
    s["bytes_ingested"]  += n_bytes
    if frames_total is not None:
        s["frames_total"] = frames_total
    # Store as ISO 8601 string (model expects Optional[str])
    s["last_frame_at"]    = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    if mission_id:
        s["mission_id"] = mission_id

def _record_rejection(drone_id):
    s = _ingest_stats.setdefault(drone_id, {
        "frames_received": 0, "frames_queued": 0,
        "frames_rejected": 0, "bytes_ingested": 0,
        "last_frame_at": None, "mission_id": None,
    })
    s["frames_received"] += 1
    s["frames_rejected"]  += 1

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger("drone.ingest")

# ── Prometheus metrics ────────────────────────────────────────────────────────
FRAMES_RECEIVED   = Counter("ingest_frames_received_total",  "Frames received",    ["drone_id", "status"])
FRAMES_BYTES      = Counter("ingest_frames_bytes_total",     "Bytes ingested",     ["drone_id"])
INGEST_LATENCY    = Histogram("ingest_frame_latency_seconds","Frame ingest time",  ["drone_id"],
                              buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5])
QUEUE_DEPTH       = Gauge("ingest_queue_depth",              "Queue message count",["drone_id"])
ACTIVE_MISSIONS   = Gauge("ingest_active_missions_total",    "Active missions count")

# ── App startup time ──────────────────────────────────────────────────────────
_startup_time: float = time.monotonic()

# ── WebSocket connection manager ──────────────────────────────────────────────

class _WSManager:
    """Tracks active WebSocket connections for the live status feed."""

    def __init__(self) -> None:
        self._clients: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.append(ws)
        log.info("ws_client_connected", extra={"total": len(self._clients)})

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients = [c for c in self._clients if c is not ws]
        log.info("ws_client_disconnected", extra={"total": len(self._clients)})

    async def broadcast(self, event: dict) -> None:
        """Send JSON event to all connected clients; drop dead connections."""
        if not self._clients:
            return
        payload = json.dumps(event)
        dead: list[WebSocket] = []
        async with self._lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)


ws_manager = _WSManager()

# ── Active missions tracker ───────────────────────────────────────────────────
_active_missions: dict[str, dict] = {}   # drone_id → {mission_id, started_at, operator}


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup → connect Redis + declare streams; Shutdown → close."""
    global _qm
    log.info("ingest_server_starting", extra={"version": API_VERSION})
    _qm = get_queue_manager()
    try:
        await _qm.connect()
        for did in KNOWN_DRONES:
            await _qm.declare_drone_queue(did)
        log.info("ingest_server_ready — redis_streams_declared", extra={"drones": len(KNOWN_DRONES)})
    except Exception as exc:
        log.error("startup_redis_connect_failed", extra={"error": str(exc)})
        # Continue anyway — will reconnect on first frame

    yield

    log.info("ingest_server_shutting_down")
    if _qm:
        await _qm.close()


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title       = API_TITLE,
    version     = API_VERSION,
    description = API_DESC,
    lifespan    = lifespan,
    docs_url    = "/docs",
    redoc_url   = "/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


# ═════════════════════════════════════════════════════════════════════════════
# HEALTH ENDPOINTS
# ═════════════════════════════════════════════════════════════════════════════

@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness check",
    tags=["Health"],
)
async def health() -> HealthResponse:
    """
    Returns 200 immediately. Used by Docker HEALTHCHECK and NGINX.
    Does not verify downstream services — use /health/ready for that.
    """
    uptime = time.monotonic() - _startup_time
    return HealthResponse(
        status   = "ok",
        version  = API_VERSION,
        uptime_s = round(uptime, 2),
        components = {
            "api": ComponentHealth(status="ok", detail="running"),
        },
    )


@app.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness check — verifies Redis + Keycloak",
    tags=["Health"],
)
async def health_ready() -> ReadinessResponse:
    """
    Returns 200 when Redis and Keycloak are both reachable.
    Returns 503 if either downstream service is down.
    """
    redis_ok = await _is_redis_healthy()

    kc_ok = False
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r     = await c.get(JWKS_URL)
            kc_ok = r.status_code == 200
    except Exception:
        pass

    ready = redis_ok and kc_ok
    resp  = ReadinessResponse(
        ready    = ready,
        redis    = "connected"   if redis_ok else "disconnected",
        keycloak = "reachable"   if kc_ok    else "unreachable",
    )

    if not ready:
        return JSONResponse(status_code=503, content=resp.model_dump())
    return resp


# ═════════════════════════════════════════════════════════════════════════════
# FRAME INGEST
# ═════════════════════════════════════════════════════════════════════════════

@app.post(
    "/ingest/frame",
    response_model = IngestResponse,
    status_code    = status.HTTP_202_ACCEPTED,
    summary        = "Upload a single drone frame (JWT required)",
    tags           = ["Ingest"],
)
async def ingest_frame(
    frame:         UploadFile = File(...,  description="JPEG or PNG frame image"),
    metadata_json: str        = Form(...,  description="JSON string of FrameMetadata"),
    claims:        dict       = Depends(validate_token),
) -> IngestResponse:
    """
    Accepts a JPEG/PNG frame from an authenticated drone operator.

    **Validation rules:**
    - `Authorization: Bearer <JWT>` required with `drone-operator` or `sys-admin` role
    - Content-Type of `frame` file must be `image/jpeg` or `image/png`
    - Frame size ≤ MAX_FRAME_MB (default 5 MB)
    - `metadata_json` must deserialise to a valid `FrameMetadata` object

    **On success:**  HTTP 202, frame enqueued to `drone.frames.<drone_id>`

    **Error codes:**
    - 401 — missing / invalid / expired JWT
    - 403 — valid JWT but wrong role
    - 413 — frame too large
    - 415 — unsupported content type
    - 422 — invalid metadata JSON
    - 503 — RabbitMQ unavailable
    """
    t_start = time.monotonic()

    # ── Parse metadata ────────────────────────────────────────────────────────
    try:
        from models import FrameMetadata
        meta = FrameMetadata(**json.loads(metadata_json))
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Bad metadata: {exc}")

    drone_id   = meta.drone_id
    mission_id = meta.mission_id

    # ── MIME type check ───────────────────────────────────────────────────────
    content_type = frame.content_type or ""
    if content_type not in ALLOWED_TYPES:
        _record_rejection(drone_id)
        FRAMES_RECEIVED.labels(drone_id=drone_id, status="rejected_mime").inc()
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Only JPEG/PNG accepted — got '{content_type}'",
        )

    # ── Read + size-check ─────────────────────────────────────────────────────
    data = await frame.read()
    if len(data) > MAX_FRAME_BYTES:
        _record_rejection(drone_id)
        FRAMES_RECEIVED.labels(drone_id=drone_id, status="rejected_size").inc()
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Frame {len(data)//1024} KB exceeds limit {MAX_FRAME_BYTES//1024//1024} MB",
        )

    # ── Publish to Redis Stream ───────────────────────────────────────────────
    try:
        msg_id = await _publish_frame(drone_id, meta.model_dump(), data)
    except Exception as exc:
        log.error("publish_failed", extra={"drone_id": drone_id, "error": str(exc)})
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Queue service temporarily unavailable",
        )

    # ── Record stats ──────────────────────────────────────────────────────────
    _record_frame(drone_id, len(data), mission_id, meta.frames_total)
    FRAMES_RECEIVED.labels(drone_id=drone_id, status="queued").inc()
    FRAMES_BYTES.labels(drone_id=drone_id).inc(len(data))
    elapsed = time.monotonic() - t_start
    INGEST_LATENCY.labels(drone_id=drone_id).observe(elapsed)

    # ── Cache latest telemetry ────────────────────────────────────────────────
    active_m = _active_missions.get(drone_id, {})
    _latest_metadata[drone_id] = {
        "drone_id":    drone_id,
        "mission_id":  mission_id,
        "mission_name": active_m.get("mission_name"),
        "operator":    active_m.get("operator", extract_username(claims)),
        "location":    active_m.get("location"),
        "latitude":    meta.latitude,
        "longitude":   meta.longitude,
        "altitude_m":  meta.altitude_m,
        "battery_pct": meta.battery_pct,
        "speed_mps":   meta.speed_mps,
        "heading_deg": meta.heading_deg,
        "change_score":meta.change_score,
        "frame_sequence": meta.frame_sequence,
        "timestamp":   meta.timestamp,
        "last_seen":   time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
    }

    queue_name = f"drone:frames:{drone_id}"
    log.info(
        "frame_queued",
        extra={
            "drone_id":  drone_id,
            "mission":   mission_id,
            "seq":       meta.frame_sequence,
            "score":     round(meta.change_score, 3),
            "bytes":     len(data),
            "latency_ms": round(elapsed * 1000, 1),
            "operator":  extract_username(claims),
        },
    )

    # ── Broadcast to WS clients ───────────────────────────────────────────────
    asyncio.create_task(
        ws_manager.broadcast({
            "event":          "frame_queued",
            "drone_id":       drone_id,
            "mission_id":     mission_id,
            "frame_sequence": meta.frame_sequence,
            "change_score":   meta.change_score,
            "timestamp":      meta.timestamp,
            "bytes":          len(data),
            "latency_ms":     round(elapsed * 1000, 1),
            "latitude":       meta.latitude,
            "longitude":      meta.longitude,
            "altitude_m":     meta.altitude_m,
            "battery_pct":    meta.battery_pct,
            "speed_mps":      meta.speed_mps,
        })
    )

    return IngestResponse(
        status         = "queued",
        drone_id       = drone_id,
        frame_sequence = meta.frame_sequence,
        queue_name     = queue_name,
        message_id     = msg_id,
    )


# ═════════════════════════════════════════════════════════════════════════════
# STATUS ENDPOINTS
# ═════════════════════════════════════════════════════════════════════════════

@app.get(
    "/ingest/status",
    response_model = SystemStatusResponse,
    summary        = "Aggregate status for all drones",
    tags           = ["Status"],
)
async def system_status() -> SystemStatusResponse:
    """
    Returns ingest counters and RabbitMQ queue depth for all tracked drones.
    Queue depths are fetched from RabbitMQ Management API (best-effort).
    """
    drone_responses: list[DroneStatusResponse] = []
    total_queued = 0

    known_ids = set(KNOWN_DRONES) | set(_ingest_stats.keys())

    for drone_id in sorted(known_ids):
        stats      = _ingest_stats.get(drone_id, {})
        depth      = await _qm.get_queue_depth(drone_id) if _qm else 0
        total_queued += depth
        QUEUE_DEPTH.labels(drone_id=drone_id).set(depth)

        q_stats = DroneQueueStats(
            queue_name     = f"drone:frames:{drone_id}",
            messages_ready = depth,
            consumers      = 0,
        )

        ingest_st = IngestStats(
            frames_received = stats.get("frames_received", 0),
            frames_queued   = stats.get("frames_queued",   0),
            frames_rejected = stats.get("frames_rejected", 0),
            bytes_ingested  = stats.get("bytes_ingested",  0),
            last_frame_at   = stats.get("last_frame_at"),
        )
        drone_responses.append(DroneStatusResponse(
            drone_id     = drone_id,
            queue        = q_stats,
            ingest_stats = ingest_st,
            mission_id   = stats.get("mission_id") or _active_missions.get(drone_id, {}).get("mission_id"),
            online       = stats.get("frames_received", 0) > 0,
        ))

    redis_ok = await _is_redis_healthy()
    return SystemStatusResponse(
        total_drones_tracked  = len(drone_responses),
        total_frames_queued   = total_queued,
        total_frames_ingested = sum(s.get("frames_queued", 0) for s in _ingest_stats.values()),
        drones                = drone_responses,
        redis_connected       = redis_ok,
    )


@app.get(
    "/ingest/status/{drone_id}",
    response_model = DroneStatusResponse,
    summary        = "Per-drone queue and ingest stats",
    tags           = ["Status"],
)
async def drone_status(
    drone_id: str,
) -> DroneStatusResponse:
    """
    Returns detailed ingest counters and live RabbitMQ queue depth for
    a single drone.
    """
    stats = _ingest_stats.get(drone_id, {})
    depth = await _qm.get_queue_depth(drone_id) if _qm else 0
    QUEUE_DEPTH.labels(drone_id=drone_id).set(depth)

    q_stats = DroneQueueStats(
        queue_name     = f"drone:frames:{drone_id}",
        messages_ready = depth,
        consumers      = 0,
    )  # consumers field: 0 for Redis Streams (no AMQP consumer concept)
    ingest_st = IngestStats(
        frames_received = stats.get("frames_received", 0),
        frames_queued   = stats.get("frames_queued",   0),
        frames_rejected = stats.get("frames_rejected", 0),
        bytes_ingested  = stats.get("bytes_ingested",  0),
        last_frame_at   = stats.get("last_frame_at"),
    )
    return DroneStatusResponse(
        drone_id     = drone_id,
        queue        = q_stats,
        ingest_stats = ingest_st,
        mission_id   = stats.get("mission_id") or _active_missions.get(drone_id, {}).get("mission_id"),
        online       = stats.get("frames_received", 0) > 0,
    )


# ═════════════════════════════════════════════════════════════════════════════
# MISSION ENDPOINTS
# ═════════════════════════════════════════════════════════════════════════════

@app.post(
    "/ingest/mission/start",
    response_model = MissionResponse,
    status_code    = status.HTTP_201_CREATED,
    summary        = "Register a new drone mission",
    tags           = ["Mission"],
)
async def mission_start(
    req:    MissionStartRequest,
    claims: dict = Depends(validate_token),
) -> MissionResponse:
    """
    Registers a new mission for a drone. Publishes a `mission_start` event
    to the `drone.events.confirmed` queue and broadcasts to WS clients.
    """
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

    # Reset ingest statistics for the new mission
    if req.drone_id in _ingest_stats:
        del _ingest_stats[req.drone_id]

    _active_missions[req.drone_id] = {
        "mission_id":  req.mission_id,
        "mission_name": req.mission_name,
        "started_at":  ts,
        "operator":    req.operator or extract_username(claims),
        "location":    req.location,
        "latitude":    req.latitude,
        "longitude":   req.longitude,
    }
    if _qm:
        _qm.start_mission(req.drone_id, req.mission_id)
    ACTIVE_MISSIONS.set(len(_active_missions))

    await _publish_event("mission_start", req.drone_id, {
        "mission_id": req.mission_id,
        "mission_name": req.mission_name,
        "operator":   req.operator or extract_username(claims),
        "location":   req.location,
        "notes":      req.notes,
    })

    asyncio.create_task(ws_manager.broadcast({
        "event":      "mission_start",
        "drone_id":   req.drone_id,
        "mission_id": req.mission_id,
        "mission_name": req.mission_name,
        "operator":   req.operator or extract_username(claims),
        "location":   req.location,
        "timestamp":  ts,
        "data":       {"latitude": req.latitude, "longitude": req.longitude},
    }))

    log.info(
        "mission_started",
        extra={
            "drone_id":   req.drone_id,
            "mission_id": req.mission_id,
            "mission_name": req.mission_name,
            "operator":   req.operator or extract_username(claims),
            "location":   req.location,
        },
    )
    return MissionResponse(
        status     = "registered",
        drone_id   = req.drone_id,
        mission_id = req.mission_id,
        timestamp  = ts,
    )


@app.post(
    "/ingest/mission/end",
    response_model = MissionResponse,
    summary        = "Mark a drone mission as ended",
    tags           = ["Mission"],
)
async def mission_end(
    req:    MissionEndRequest,
    claims: dict = Depends(validate_token),
) -> MissionResponse:
    """
    Marks a mission as ended. Removes it from the active missions tracker
    and publishes a `mission_end` event.
    """
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

    _active_missions.pop(req.drone_id, None)
    if req.drone_id in _latest_metadata:
        _latest_metadata[req.drone_id].pop("mission_id", None)
        _latest_metadata[req.drone_id].pop("mission_name", None)
        _latest_metadata[req.drone_id].pop("operator", None)
        _latest_metadata[req.drone_id].pop("location", None)

    if _qm:
        _qm.end_mission(req.drone_id, req.mission_id)
    ACTIVE_MISSIONS.set(len(_active_missions))

    await _publish_event("mission_end", req.drone_id, {
        "mission_id":  req.mission_id,
        "frames_sent": req.frames_sent,
        "notes":       req.notes,
    })

    asyncio.create_task(ws_manager.broadcast({
        "event":       "mission_end",
        "drone_id":    req.drone_id,
        "mission_id":  req.mission_id,
        "timestamp":   ts,
        "data":        {"frames_sent": req.frames_sent},
    }))

    log.info(
        "mission_ended",
        extra={
            "drone_id":    req.drone_id,
            "mission_id":  req.mission_id,
            "frames_sent": req.frames_sent,
        },
    )
    return MissionResponse(
        status     = "ended",
        drone_id   = req.drone_id,
        mission_id = req.mission_id,
        timestamp  = ts,
    )


# ═════════════════════════════════════════════════════════════════════════════
# TELEMETRY & FRAME RETRIEVAL ENDPOINTS
# ═════════════════════════════════════════════════════════════════════════════


@app.get(
    "/ingest/streams/active",
    summary = "Active drone stream keys from Valkey (restart-safe)",
    tags    = ["Telemetry"],
)
async def get_active_streams():
    """
    Scans Valkey for ``drone:frames:*`` stream keys and returns the list of
    drone IDs that have frames stored.  Unlike ``/ingest/telemetry`` this
    survives server restarts because it reads directly from the persistent
    Valkey store rather than the in-memory ``_latest_metadata`` dict.

    Returns::

        {
          "drones": [
            {
              "drone_id": "DRN-004",
              "stream_key": "drone:frames:DRN-004",
              "frame_count": 1234,
              "mission_id": "MSN-3705-FOXTROT",   // from in-memory cache or null
              "last_seen": "2026-08-06T07:40:00.000Z" // from in-memory or null
            }
          ],
          "count": 1
        }
    """
    if not _qm:
        return {"drones": [], "count": 0}
    try:
        client = _qm._client
        # SCAN for all drone:frames:* keys.
        # redis-py async returns integer cursor (0 = iteration complete),
        # NOT bytes b"0". Use integer 0 for init and termination check.
        pattern = "drone:frames:*"
        cursor = 0          # ← integer, not b"0"
        found_keys = []
        for _ in range(200):    # safety cap — avoids infinite loop on large keyspaces
            cursor, keys = await client.scan(cursor=cursor, match=pattern, count=100)
            found_keys.extend(keys)
            if cursor == 0:     # ← integer comparison (redis-py returns int)
                break

        drones = []
        for key in found_keys:
            key_str = key.decode() if isinstance(key, bytes) else key
            # key_str = "drone:frames:DRN-003"
            parts = key_str.split(":", 2)
            if len(parts) < 3:
                continue
            drone_id = parts[2]
            try:
                frame_count = await client.xlen(key_str)
            except Exception:
                frame_count = 0
            meta = _latest_metadata.get(drone_id) or {}
            drones.append({
                "drone_id":        drone_id,
                "stream_key":      key_str,
                "frame_count":     frame_count,
                "frames_received": frame_count,   # alias for frontend compatibility
                "mission_id":      meta.get("mission_id") or _active_missions.get(drone_id, {}).get("mission_id"),
                "last_seen":       meta.get("last_seen"),
                "latitude":        meta.get("latitude"),
                "longitude":       meta.get("longitude"),
                "battery_pct":     meta.get("battery_pct"),
                "altitude_m":      meta.get("altitude_m"),
            })

        # Sort: most frames first (i.e. most active drone at top)
        drones.sort(key=lambda d: d["frame_count"], reverse=True)
        return {"drones": drones, "count": len(drones)}

    except Exception as exc:
        log.warning("streams_active_error: %s", exc)
        return {"drones": [], "count": 0}



@app.get(
    "/ingest/telemetry",
    summary = "Latest telemetry for all active drones",
    tags    = ["Telemetry"],
)
async def get_telemetry():
    """
    Returns cached latest telemetry (GPS, battery, speed, etc.) for every drone
    that has sent at least one frame since server startup.
    No auth required — used internally by the dashboard backend telemetry pump.
    """
    return {"drones": list(_latest_metadata.values()), "count": len(_latest_metadata)}


@app.get(
    "/ingest/frame/{drone_id}/latest",
    summary = "Latest raw JPEG frame for a drone (JWT required)",
    tags    = ["Telemetry"],
)
async def get_latest_frame(
    drone_id: str,
    claims: dict = Depends(validate_token_flexible),
):
    """
    Reads the most recent entry from the Valkey stream for the given drone
    and returns the raw JPEG bytes. Used by the command dashboard multi-drone
    view to display live frames.
    """
    if not _qm:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Queue unavailable")
    try:
        stream_key = f"drone:frames:{drone_id}"
        entries = await _qm._client.xrevrange(stream_key, max="+", min="-", count=1)
        if not entries:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No frames yet for drone {drone_id}")
        _entry_id, fields = entries[0]
        raw_jpeg = fields[b"frame"]
        return Response(
            content      = raw_jpeg,
            media_type   = "image/jpeg",
            headers      = {
                "Cache-Control": "no-store",
                "X-Drone-ID":   drone_id,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.error("latest_frame_error", extra={"drone_id": drone_id, "error": str(exc)})
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Frame retrieval failed")


# ═════════════════════════════════════════════════════════════════════════════
# LIVE MJPEG STREAM  — /ingest/stream/{drone_id}
# ═════════════════════════════════════════════════════════════════════════════

async def _mjpeg_frame_generator(drone_id: str) -> AsyncGenerator[bytes, None]:
    """
    Async generator that reads JPEG frames live from the Valkey stream for
    the given drone and yields them as MJPEG multipart chunks.

    Protocol: multipart/x-mixed-replace; boundary=frame
    Each part:
        --frame\\r\\n
        Content-Type: image/jpeg\\r\\n
        Content-Length: <n>\\r\\n
        \\r\\n
        <jpeg bytes>
        \\r\\n

    Keepalive: when no frame arrives within the block window, a boundary
    chunk with an empty body is sent so the connection stays alive.

    The stream cursor starts at the CURRENT position ("$") so only NEW
    frames (received after the HTTP connection opens) are streamed — no
    replay of old frames.
    """
    if not _qm or not _qm._connected:
        return

    stream_key = f"drone:frames:{drone_id}"
    # Start at current position — do NOT replay old frames
    last_id = "$"
    BLOCK_MS = 2000   # 2-second long-poll per XREAD call

    BOUNDARY = b"--frame\r\n"
    KEEPALIVE = b"--frame\r\nContent-Type: text/plain\r\n\r\n\r\n"

    while True:
        try:
            results = await _qm._client.xread(
                {stream_key: last_id},
                count=1,
                block=BLOCK_MS,
            )
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.warning("mjpeg_stream_error drone=%s error=%s", drone_id, exc)
            await asyncio.sleep(1.0)
            continue

        if not results:
            # No new frame within block window — send keepalive boundary
            yield KEEPALIVE
            continue

        for _stream, entries in results:
            for entry_id_raw, fields in entries:
                entry_id = (
                    entry_id_raw.decode()
                    if isinstance(entry_id_raw, bytes)
                    else entry_id_raw
                )
                last_id = entry_id

                # Prefer raw bytes field (stored by queue_manager);
                # fall back to decoding jpeg_b64 if only the b64 field exists.
                raw_jpeg: Optional[bytes] = None
                if b"frame" in fields:
                    raw_jpeg = fields[b"frame"]
                elif "frame" in fields:
                    v = fields["frame"]
                    raw_jpeg = v if isinstance(v, bytes) else v.encode("latin-1")
                elif b"jpeg_b64" in fields:
                    raw_jpeg = base64.b64decode(fields[b"jpeg_b64"])
                elif "jpeg_b64" in fields:
                    raw_jpeg = base64.b64decode(fields["jpeg_b64"])

                if raw_jpeg:
                    chunk = (
                        BOUNDARY
                        + b"Content-Type: image/jpeg\r\n"
                        + b"Content-Length: " + str(len(raw_jpeg)).encode() + b"\r\n"
                        + b"X-Frame-ID: " + entry_id.encode() + b"\r\n"
                        + b"\r\n"
                        + raw_jpeg
                        + b"\r\n"
                    )
                    yield chunk


@app.get(
    "/ingest/stream/{drone_id}",
    summary="Live MJPEG frame stream for a drone (JWT required)",
    tags=["Streaming"],
    response_class=StreamingResponse,
)
async def live_stream(
    drone_id: str,
    claims: dict = Depends(validate_token_flexible),
) -> StreamingResponse:
    """
    Live MJPEG stream that pumps real-time drone frames from the Valkey
    queue directly to the browser or dashboard.

    Authentication:
    - Bearer JWT in Authorization header, ?token=<JWT> query parameter,
      or dvas_access_token HttpOnly cookie.
    """
    if not _qm:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Queue unavailable")

    return StreamingResponse(
        _mjpeg_frame_generator(drone_id),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma":        "no-cache",
            "X-Drone-ID":    drone_id,
        },
    )


# ═════════════════════════════════════════════════════════════════════════════
# PROMETHEUS METRICS
# ═════════════════════════════════════════════════════════════════════════════

@app.get(
    "/metrics",
    summary     = "Prometheus metrics (text/plain)",
    tags        = ["Monitoring"],
    response_class = PlainTextResponse,
)
async def metrics() -> PlainTextResponse:
    """
    Exposes Prometheus-format metrics for scraping.

    Metrics exposed:
    - `ingest_frames_received_total{drone_id, status}` — frame counter by outcome
    - `ingest_frames_bytes_total{drone_id}` — bytes ingested
    - `ingest_frame_latency_seconds{drone_id}` — latency histogram
    - `ingest_queue_depth{drone_id}` — current queue depth (from management API)
    - `ingest_active_missions_total` — active mission count
    """
    return PlainTextResponse(
        content      = generate_latest().decode("utf-8"),
        media_type   = CONTENT_TYPE_LATEST,
    )


# ═════════════════════════════════════════════════════════════════════════════
# WEBSOCKET — LIVE STATUS FEED
# ═════════════════════════════════════════════════════════════════════════════

@app.websocket("/ws/status")
async def ws_status(ws: WebSocket) -> None:
    """
    WebSocket endpoint for the control-room live status dashboard.

    Authenticates token from query param (?token=<JWT>) before accepting connection.
    """
    token = ws.query_params.get("token")
    if not token:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        claims = await validate_token_string(token)
    except Exception as exc:
        log.warning("ws_auth_rejected: %s", exc)
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await ws_manager.connect(ws)
    try:
        # Send initial snapshot
        await ws.send_text(json.dumps({
            "event":     "connected",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            "data": {
                "known_drones":    KNOWN_DRONES,
                "active_missions": len(_active_missions),
                "api_version":     API_VERSION,
            },
        }))

        # Keep alive — handle pings
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=30.0)
                if msg.strip().lower() == "ping":
                    await ws.send_text(json.dumps({"event": "pong"}))
            except asyncio.TimeoutError:
                # Send keepalive
                await ws.send_text(json.dumps({"event": "keepalive"}))

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.warning("ws_unexpected_error", extra={"error": str(exc)})
    finally:
        await ws_manager.disconnect(ws)


# ═════════════════════════════════════════════════════════════════════════════
# GLOBAL EXCEPTION HANDLER
# ═════════════════════════════════════════════════════════════════════════════

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.error(
        "unhandled_exception",
        extra={"path": request.url.path, "error": str(exc)},
        exc_info=exc,
    )
    return JSONResponse(
        status_code = 500,
        content     = {"detail": "Internal server error"},
    )
