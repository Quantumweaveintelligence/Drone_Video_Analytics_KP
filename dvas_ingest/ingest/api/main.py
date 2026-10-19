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
import os
from pathlib import Path
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

import httpx
from fastapi import (
    Body,
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
    GeofenceUpdateRequest,
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
_active_missions: dict[str, dict] = {}
_drain_tasks: dict = {}   # drone_id → {mission_id, started_at, operator}


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
        asyncio.create_task(_auto_cleanup_consumed_streams())
        log.info("ingest_server_ready — redis_streams_declared", extra={"drones": len(KNOWN_DRONES)})

        # Restore saved geofences from disk to Redis on startup
        try:
            geofence_dir = Path("/data/geofences") if os.path.exists("/data/geofences") else Path("data/geofences")
            if geofence_dir.exists():
                redis_client = getattr(_qm, "_client", None) or getattr(_qm, "_redis", None)
                if redis_client:
                    for gfile in geofence_dir.glob("*_geofence.json"):
                        try:
                            gdata = json.loads(gfile.read_text(encoding="utf-8"))
                            mid = gfile.stem.replace("_geofence", "")
                            raw_geo = json.dumps(gdata)
                            await redis_client.set(f"drone:geofence:{mid}", raw_geo)
                            await redis_client.set(f"drone:geofence:DRN-{mid}", raw_geo)
                            feats = gdata.get("features", [])
                            if feats:
                                props = feats[0].get("properties", {})
                                ref_did = props.get("reference_metadata", {}).get("drone_id")
                                if ref_did:
                                    await redis_client.set(f"drone:geofence:{ref_did}", raw_geo)
                        except Exception:
                            pass
        except Exception as geofence_restore_err:
            log.warning("geofence_startup_restore_warning", extra={"error": str(geofence_restore_err)})

        # Restore active missions from Valkey
        try:
            valkey = getattr(_qm, "_client", None) or getattr(_qm, "_redis", None)
            if valkey:
                cursor = 0
                while True:
                    cursor, keys = await valkey.scan(cursor, match="mission:active:*", count=100)
                    for key in keys:
                        data = await valkey.hgetall(key)
                        if not data:
                            continue
                        decoded = {}
                        for k, v in data.items():
                            k_str = k.decode() if isinstance(k, bytes) else k
                            v_str = v.decode() if isinstance(v, bytes) else v
                            decoded[k_str] = v_str
                        key_str = key.decode() if isinstance(key, bytes) else key
                        drone_id = key_str.split(":")[-1]
                        _active_missions[drone_id] = decoded
                        
                        _latest_metadata.setdefault(drone_id, {}).update({
                            "mission_id": decoded.get("mission_id"),
                            "mission_name": decoded.get("mission_name"),
                            "operator": decoded.get("operator"),
                            "location": decoded.get("location"),
                            "mission_status": decoded.get("status", "active"),
                        })
                        log.info(f"[RECOVERY] Restored mission for {drone_id}: {decoded.get('mission_id')}")
                    if cursor == 0:
                        break
        except Exception as e:
            log.warning(f"Failed to restore missions from Valkey: {e}")

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

    # ── Binary magic-byte verification (Issue #9) ────────────────────────────
    # Validates actual file content regardless of Content-Type header to prevent
    # malformed payloads from crashing downstream OpenCV analytics workers.
    _JPEG_MAGIC = b"\xFF\xD8\xFF"
    _PNG_MAGIC  = b"\x89PNG\r\n\x1a\n"
    if not (data.startswith(_JPEG_MAGIC) or data.startswith(_PNG_MAGIC)):
        _record_rejection(drone_id)
        FRAMES_RECEIVED.labels(drone_id=drone_id, status="rejected_magic").inc()
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Invalid image: binary header does not match JPEG or PNG magic bytes",
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
        "abs_alt":     getattr(meta, "abs_alt", None),
        "gb_yaw":      getattr(meta, "gb_yaw", None),
        "gb_pitch":    getattr(meta, "gb_pitch", None),
        "gb_roll":     getattr(meta, "gb_roll", None),
        "focal_len":   getattr(meta, "focal_len", None),
        "dzoom_ratio": getattr(meta, "dzoom_ratio", None),
        "gps_lock":    getattr(meta, "gps_lock", None) if getattr(meta, "gps_lock", None) is not None else (meta.latitude is not None and meta.longitude is not None and (meta.latitude != 0.0 or meta.longitude != 0.0)),
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
    # Cancel any pending drain from a previous mission on the same drone
    if req.drone_id in _drain_tasks:
        old_task = _drain_tasks.pop(req.drone_id)
        old_task.cancel()
        log.info(f"[MISSION-START] Cancelled pending drain for {req.drone_id} (new mission starting)")

    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

    # Reset ingest statistics for the new mission
    if req.drone_id in _ingest_stats:
        del _ingest_stats[req.drone_id]

    _active_missions[req.drone_id] = {
        "mission_id":   req.mission_id,
        "mission_name": req.mission_name,
        "status":       "active",
        "started_at":   ts,
        "operator":     req.operator or extract_username(claims),
        "location":     req.location,
        "latitude":     req.latitude,
        "longitude":    req.longitude,
        "geofence":     req.geofence,
    }
    if _qm:
        _qm.start_mission(req.drone_id, req.mission_id)

    if req.drone_id not in _latest_metadata:
        _latest_metadata[req.drone_id] = {}
    _latest_metadata[req.drone_id].update({
        "drone_id":       req.drone_id,
        "mission_id":     req.mission_id,
        "mission_name":   req.mission_name,
        "mission_status": "active",
        "status":         "live",
        "operator":       req.operator or extract_username(claims),
        "location":       req.location,
        "last_seen":      ts,
    })

    try:
        valkey = getattr(_qm, "_client", None) or getattr(_qm, "_redis", None)
        if valkey:
            await valkey.hset(f"mission:active:{req.drone_id}", mapping={
                "mission_id": str(req.mission_id),
                "mission_name": str(req.mission_name or ""),
                "operator": str(req.operator or extract_username(claims) or ""),
                "location": str(req.location or ""),
                "status": "active",
                "started_at": ts
            })
    except Exception as e:
        log.warning(f"Failed to persist mission to Valkey: {e}")

    # Persist geofence to disk and Redis if provided
    if req.geofence:
        try:
            geofence_dir = Path("/data/geofences") if os.path.exists("/data") else Path("data/geofences")
            geofence_dir.mkdir(parents=True, exist_ok=True)
            geofence_path = geofence_dir / f"{req.mission_id}_geofence.json"
            geofence_path.write_text(json.dumps(req.geofence, indent=2), encoding="utf-8")
            log.info("geofence_persisted_disk", extra={"path": str(geofence_path), "mission": req.mission_id})
        except Exception as e:
            log.warning("geofence_disk_save_failed", extra={"error": str(e)})

        redis_client = getattr(_qm, "_client", None) or getattr(_qm, "_redis", None)
        if redis_client:
            try:
                raw_geo = json.dumps(req.geofence)
                keys_to_set = {f"drone:geofence:{req.drone_id}"}
                if req.mission_id:
                    keys_to_set.add(f"drone:geofence:{req.mission_id}")
                    keys_to_set.add(f"drone:geofence:DRN-{req.mission_id}")
                if req.drone_id.startswith("DRN-"):
                    keys_to_set.add(f"drone:geofence:DRONE_KL_{req.drone_id[4:]}")
                elif req.drone_id.startswith("DRONE_KL_"):
                    keys_to_set.add(f"drone:geofence:DRN-{req.drone_id[9:]}")
                features = req.geofence.get("features", [])
                if features:
                    ref_meta = features[0].get("properties", {}).get("reference_metadata", {})
                    ref_did = ref_meta.get("drone_id")
                    if ref_did:
                        keys_to_set.add(f"drone:geofence:{ref_did}")
                for k in keys_to_set:
                    await redis_client.set(k, raw_geo)
                log.info("geofence_saved_redis", extra={"drone_id": req.drone_id, "keys": list(keys_to_set)})
            except Exception as e:
                log.warning("geofence_redis_save_failed", extra={"error": str(e)})

    if _qm:
        _qm.start_mission(req.drone_id, req.mission_id)
    ACTIVE_MISSIONS.set(len(_active_missions))

    await _publish_event("mission_start", req.drone_id, {
        "mission_id":   req.mission_id,
        "mission_name": req.mission_name,
        "operator":     req.operator or extract_username(claims),
        "location":     req.location,
        "notes":        req.notes,
        "has_geofence": bool(req.geofence),
        "geofence":     req.geofence,
    })

    asyncio.create_task(ws_manager.broadcast({
        "event":        "mission_start",
        "drone_id":     req.drone_id,
        "mission_id":   req.mission_id,
        "mission_name": req.mission_name,
        "operator":     req.operator or extract_username(claims),
        "location":     req.location,
        "timestamp":    ts,
        "has_geofence": bool(req.geofence),
        "geofence":     req.geofence,
        "data":         {"latitude": req.latitude, "longitude": req.longitude},
    }))

    log.info(
        "mission_started",
        extra={
            "drone_id":     req.drone_id,
            "mission_id":   req.mission_id,
            "mission_name": req.mission_name,
            "operator":     req.operator or extract_username(claims),
            "location":     req.location,
            "has_geofence": bool(req.geofence),
        },
    )
    return MissionResponse(
        status     = "registered",
        drone_id   = req.drone_id,
        mission_id = req.mission_id,
        timestamp  = ts,
    )


@app.post(
    "/ingest/mission/geofence",
    status_code = status.HTTP_200_OK,
    summary     = "Update active geofence during mission pause",
    tags        = ["Mission"],
)
async def update_mission_geofence(
    req:    GeofenceUpdateRequest,
    claims: dict = Depends(validate_token),
) -> dict:
    """Updates the active geofence when a mission is paused mid-flight."""
    if req.drone_id in _active_missions:
        _active_missions[req.drone_id]["geofence"] = req.geofence

    try:
        geofence_dir = Path("/data/geofences") if os.path.exists("/data") else Path("data/geofences")
        geofence_dir.mkdir(parents=True, exist_ok=True)
        geofence_path = geofence_dir / f"{req.mission_id}_geofence.json"
        geofence_path.write_text(json.dumps(req.geofence, indent=2), encoding="utf-8")
        log.info("geofence_updated_disk", extra={"path": str(geofence_path), "mission": req.mission_id})
    except Exception as e:
        log.warning("geofence_disk_save_failed", extra={"error": str(e)})

    redis_client = getattr(_qm, "_client", None) or getattr(_qm, "_redis", None)
    if redis_client:
        try:
            raw_geo = json.dumps(req.geofence)
            keys_to_set = {f"drone:geofence:{req.drone_id}"}
            if req.mission_id:
                keys_to_set.add(f"drone:geofence:{req.mission_id}")
                keys_to_set.add(f"drone:geofence:DRN-{req.mission_id}")
            if req.drone_id.startswith("DRN-"):
                keys_to_set.add(f"drone:geofence:DRONE_KL_{req.drone_id[4:]}")
            elif req.drone_id.startswith("DRONE_KL_"):
                keys_to_set.add(f"drone:geofence:DRN-{req.drone_id[9:]}")
            features = req.geofence.get("features", [])
            if features:
                ref_meta = features[0].get("properties", {}).get("reference_metadata", {})
                ref_did = ref_meta.get("drone_id")
                if ref_did:
                    keys_to_set.add(f"drone:geofence:{ref_did}")
            for k in keys_to_set:
                await redis_client.set(k, raw_geo)
            log.info("geofence_updated_redis", extra={"drone_id": req.drone_id, "keys": list(keys_to_set)})
        except Exception as e:
            log.warning("geofence_redis_save_failed", extra={"error": str(e)})

    await _publish_event("geofence_updated", req.drone_id, {
        "mission_id": req.mission_id,
        "geofence":   req.geofence,
    })

    asyncio.create_task(ws_manager.broadcast({
        "event":      "geofence_updated",
        "drone_id":   req.drone_id,
        "mission_id": req.mission_id,
        "geofence":   req.geofence,
        "timestamp":  time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
    }))

    return {"status": "updated", "drone_id": req.drone_id, "mission_id": req.mission_id}


@app.post(
    "/ingest/incident",
    status_code = status.HTTP_201_CREATED,
    summary     = "Report an operational incident from edge station",
    tags        = ["Incident"],
)
async def ingest_incident(
    req:    dict = Body(...),
    claims: dict = Depends(validate_token_flexible),
) -> dict:
    """
    Accepts an edge incident report, logs it, and broadcasts an incident event to WebSocket clients.
    """
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    inc_id = req.get("id") or f"INC-{int(time.time())}"

    event_payload = {
        "event_type": "edge_incident",
        "incident_id": inc_id,
        "title": req.get("title", "Operational Incident"),
        "severity": req.get("severity", "MEDIUM"),
        "category": req.get("category", "hardware"),
        "location": req.get("location", "Edge Station"),
        "created_by": req.get("created_by", "Edge Operator"),
        "timestamp": req.get("timestamp", ts),
        "description": req.get("description", ""),
        "drone_id": req.get("drone_id"),
        "mission_id": req.get("mission_id"),
    }

    asyncio.create_task(ws_manager.broadcast({
        "event": "edge_incident",
        "incident": event_payload,
        "timestamp": ts,
    }))

    log.info("edge_incident_reported", extra={"incident_id": inc_id, "severity": event_payload["severity"]})
    return {"status": "ok", "incident_id": inc_id, "received_at": ts}


@app.post(
    "/ingest/model-suggestion",
    status_code = status.HTTP_201_CREATED,
    summary     = "Receive AI model suggestion from edge station",
    tags        = ["Analytics"],
)
async def ingest_model_suggestion(
    req:    dict = Body(...),
    claims: dict = Depends(validate_token_flexible),
) -> dict:
    """
    Accepts an edge operator AI model recommendation, logs it, and broadcasts
    a model_suggestion event to WebSocket clients (including GPU dashboard).
    """
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    sugg_id = req.get("id") or f"SUGG-{int(time.time())}"

    event_payload = {
        "event_type": "model_suggestion",
        "suggestion_id": sugg_id,
        "models": req.get("models", []),
        "model_names": req.get("model_names", []),
        "priority": req.get("priority", "normal"),
        "reason": req.get("reason", ""),
        "location": req.get("location", "Edge Ground Station"),
        "suggested_by": req.get("suggested_by", "Edge Operator"),
        "timestamp": req.get("timestamp", ts),
        "status": req.get("status", "PENDING"),
    }

    asyncio.create_task(ws_manager.broadcast({
        "event": "model_suggestion",
        "suggestion": event_payload,
        "timestamp": ts,
    }))

    log.info("edge_model_suggestion_received", extra={"suggestion_id": sugg_id, "models": event_payload["models"]})
    return {"status": "ok", "suggestion_id": sugg_id, "received_at": ts}


async def _auto_cleanup_consumed_streams(min_drain_sec: float = 20.0):
    """Periodically check for fully-consumed draining streams and auto-clean after grace period."""
    while True:
        await asyncio.sleep(15)
        try:
            for drone_id in list(_active_missions.keys()):
                mission = _active_missions[drone_id]
                if mission.get("status") != "draining":
                    continue
                drain_start = mission.get("drain_started_at")
                if not drain_start and drone_id in _qm._stats:
                    drain_start = _qm._stats[drone_id].drain_started_at
                
                # Enforce minimum grace period so operator/dashboard can observe draining
                if drain_start and (time.time() - drain_start) < min_drain_sec:
                    continue

                if await _qm.is_fully_consumed(drone_id):
                    log.info(f"[AUTO-CLEANUP] {drone_id}: Fully consumed — purging")
                    await _qm.purge_stream(drone_id)
                    try:
                        valkey = getattr(_qm, "_client", None) or getattr(_qm, "_redis", None)
                        if valkey:
                            await valkey.delete(f"mission:active:{drone_id}")
                            await valkey.delete(f"drone:frames:{drone_id}")
                    except Exception as e:
                        log.warning(f"Failed to clean mission from Valkey: {e}")
                    mid = mission.get("mission_id", "unknown")
                    _active_missions.pop(drone_id, None)
                    _latest_metadata.pop(drone_id, None)
                    _ingest_stats.pop(drone_id, None)
                    if drone_id in _qm._stats:
                        del _qm._stats[drone_id]
                    await ws_manager.broadcast({"event": "mission_drained", "drone_id": drone_id, "mission_id": mid})
        except Exception as e:
            log.warning(f"[AUTO-CLEANUP] Error: {e}")


async def _monitor_queue_drain(drone_id: str, mission_id: str, max_wait_sec: float = 300.0, min_drain_sec: float = 20.0):
    """
    Consumer-group-aware drain. Waits until all analytics consumers have ACKed
    all pending entries AND min_drain_sec (20s) has elapsed, OR until max_wait_sec (5 min).
    Does NOT auto-purge on timeout — stream preserved for manual officer purge.
    """
    start = time.monotonic()
    while (time.monotonic() - start) < max_wait_sec:
        try:
            elapsed = time.monotonic() - start
            if elapsed >= min_drain_sec and await _qm.is_fully_consumed(drone_id):
                current_mission = _active_missions.get(drone_id, {})
                if current_mission.get("mission_id") != mission_id:
                    log.info(f"[DRAIN] {drone_id}: Mission {mission_id} superseded by {current_mission.get('mission_id')} — aborting drain cleanup")
                    _drain_tasks.pop(drone_id, None)
                    return

                log.info(f"[DRAIN] {drone_id}/{mission_id}: All consumers finished and grace period satisfied — auto-cleaning")
                await _qm.purge_stream(drone_id)
                try:
                    valkey = getattr(_qm, "_client", None) or getattr(_qm, "_redis", None)
                    if valkey:
                        await valkey.delete(f"mission:active:{drone_id}")
                        await valkey.delete(f"drone:frames:{drone_id}")
                except Exception as e:
                    log.warning(f"Failed to clean mission from Valkey: {e}")
                _active_missions.pop(drone_id, None)
                _latest_metadata.pop(drone_id, None)
                _ingest_stats.pop(drone_id, None)
                if drone_id in _qm._stats:
                    del _qm._stats[drone_id]
                await ws_manager.broadcast({
                    "event": "mission_drained",
                    "drone_id": drone_id,
                    "mission_id": mission_id
                })
                _drain_tasks.pop(drone_id, None)
                return
        except asyncio.CancelledError:
            log.info(f"[DRAIN] {drone_id}/{mission_id}: Drain cancelled (new mission started?)")
            _drain_tasks.pop(drone_id, None)
            return
        except Exception as e:
            log.warning(f"[DRAIN] {drone_id}: Error checking consumer lag: {e}")
        await asyncio.sleep(2.0)
    log.warning(f"[DRAIN] {drone_id}/{mission_id}: Drain timeout after {max_wait_sec}s — stream preserved for manual purge")
    _drain_tasks.pop(drone_id, None)

@app.post(
    "/ingest/mission/end",
    response_model = MissionResponse,
    summary        = "Mark a drone mission as ended and initiate queue drain",
    tags           = ["Mission"],
)
async def mission_end(
    req:    MissionEndRequest,
    claims: dict = Depends(validate_token),
) -> MissionResponse:
    """
    Marks a mission as draining/ended. Begins background queue drain monitoring
    so in-flight frames are processed before stream cleanup.
    """
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

    if _qm:
        _qm.mark_mission_draining(req.drone_id, req.mission_id, req.frames_sent)
    if req.drone_id in _active_missions:
        _active_missions[req.drone_id]["status"] = "draining"
        _active_missions[req.drone_id]["drain_started_at"] = time.time()
    else:
        _active_missions[req.drone_id] = {
            "mission_id": req.mission_id,
            "status": "draining",
            "drone_id": req.drone_id,
            "drain_started_at": time.time(),
        }

    try:
        valkey = getattr(_qm, "_client", None) or getattr(_qm, "_redis", None)
        if valkey:
            await valkey.hset(f"mission:active:{req.drone_id}", "status", "draining")
    except Exception as e:
        log.warning(f"Failed to update mission status in Valkey: {e}")
    
    if req.drone_id in _latest_metadata:
        _latest_metadata[req.drone_id]["mission_status"] = "draining"
        _latest_metadata[req.drone_id]["status"] = "draining"
        _latest_metadata[req.drone_id]["drain_started_at"] = ts

    if req.drone_id in _drain_tasks:
        _drain_tasks[req.drone_id].cancel()
    _drain_tasks[req.drone_id] = asyncio.create_task(_monitor_queue_drain(req.drone_id, req.mission_id))

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
        "data":        {"frames_sent": req.frames_sent, "status": "draining"},
    }))

    log.info(
        "mission_ending_draining",
        extra={
            "drone_id":    req.drone_id,
            "mission_id":  req.mission_id,
            "frames_sent": req.frames_sent,
        },
    )
    return MissionResponse(
        status     = "draining",
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
    drone IDs that have active frames or are currently draining.
    """
    if not _qm:
        return {"drones": [], "count": 0}
    try:
        client = _qm._client
        pattern = "drone:frames:*"
        cursor = 0
        found_keys = []
        for _ in range(200):
            cursor, keys = await client.scan(cursor=cursor, match=pattern, count=100)
            found_keys.extend(keys)
            if cursor == 0:
                break

        found_key_strs = {k.decode() if isinstance(k, bytes) else k for k in found_keys}
        for d in _active_missions:
            found_key_strs.add(f"drone:frames:{d}")

        drones = []
        for key_str in found_key_strs:
            parts = key_str.split(":", 2)
            if len(parts) < 3:
                continue
            drone_id = parts[2]
            try:
                frame_count = await client.xlen(key_str)
            except Exception:
                frame_count = 0

            meta = _latest_metadata.get(drone_id) or {}
            last_seen_str = meta.get("last_seen")
            
            # Determine live vs draining vs offline
            is_active_mission = drone_id in _active_missions
            active_mission_info = _active_missions.get(drone_id, {})
            active_mission_status = active_mission_info.get("status")
            mission_stat = active_mission_status or (_qm._stats.get(drone_id).mission_status if drone_id in _qm._stats else "idle")
            
            # Check if we received a frame recently (within 15s) — strong live signal
            recently_seen = False
            if last_seen_str:
                try:
                    import datetime
                    last_seen_dt = datetime.datetime.strptime(last_seen_str, "%Y-%m-%dT%H:%M:%S.%fZ")
                    age_sec = (datetime.datetime.utcnow() - last_seen_dt).total_seconds()
                    # Strictly ensure age is non-negative and within 15 seconds
                    recently_seen = 0.0 <= age_sec < 15.0
                except Exception:
                    pass
            
            # If 0 frames exist in stream, skip reporting as active UNLESS mission is draining or active
            if frame_count == 0 and not is_active_mission and not recently_seen:
                if drone_id not in KNOWN_DRONES:
                    try:
                        await client.delete(key_str)
                    except Exception:
                        pass
                continue

            stream_status = "live"
            if mission_stat == "draining":
                stream_status = "draining"
            elif not is_active_mission and not recently_seen:
                stream_status = "draining" if frame_count > 0 else "offline"
            elif frame_count == 0 and not recently_seen:
                stream_status = "offline"

            # Do not report offline streams with 0 frames in the active streams list
            if stream_status == "offline" and frame_count == 0:
                if drone_id not in KNOWN_DRONES:
                    try:
                        await client.delete(key_str)
                    except Exception:
                        pass
                continue

            drones.append({
                "drone_id":        drone_id,
                "stream_key":      key_str,
                "frame_count":     frame_count,
                "frames_received": frame_count,
                "messages_ready":  frame_count,
                "status":          stream_status,
                "mission_status":  mission_stat,
                "mission_id":      meta.get("mission_id") or _active_missions.get(drone_id, {}).get("mission_id"),
                "mission_name":    meta.get("mission_name") or _active_missions.get(drone_id, {}).get("mission_name"),
                "operator":        meta.get("operator") or _active_missions.get(drone_id, {}).get("operator"),
                "location":        meta.get("location") or _active_missions.get(drone_id, {}).get("location"),
                "last_seen":       last_seen_str,
                "latitude":        meta.get("latitude"),
                "longitude":       meta.get("longitude"),
                "battery_pct":     meta.get("battery_pct"),
                "altitude_m":      meta.get("altitude_m"),
                "abs_alt":         meta.get("abs_alt"),
                "gb_yaw":          meta.get("gb_yaw"),
                "gb_pitch":        meta.get("gb_pitch"),
                "gb_roll":         meta.get("gb_roll"),
                "focal_len":       meta.get("focal_len"),
                "dzoom_ratio":     meta.get("dzoom_ratio"),
                "gps_lock":        meta.get("gps_lock", True),
            })

        # Sort: live first, then most frames
        drones.sort(key=lambda d: (d["status"] == "live", d["frame_count"]), reverse=True)
        return {"drones": drones, "count": len(drones)}

    except Exception as exc:
        log.warning("streams_active_error: %s", exc)
        return {"drones": [], "count": 0}



@app.get(
    "/ingest/telemetry",
    summary = "Latest telemetry for all active drones",
    tags    = ["Telemetry"],
)
async def get_telemetry(claims: dict = Depends(validate_token_flexible)):
    """
    Returns cached latest telemetry (GPS, battery, speed, etc.) for every drone
    that has sent at least one frame since server startup.
    Requires valid JWT with an authorized role (drone-operator or sys-admin).
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
    Reads the most recent entry from the Valkey stream for the given drone.
    Returns 404 if no frame exists or if stream has ended/drained.
    """
    if not _qm:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Queue unavailable")
    try:
        stream_key = f"drone:frames:{drone_id}"
        entries = await _qm._client.xrevrange(stream_key, max="+", min="-", count=1)
        if not entries:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No frames available for drone {drone_id}")

        meta = _latest_metadata.get(drone_id) or {}
        mission_id = meta.get("mission_id") or ""

        # Determine if stream is truly live based on active mission OR recent frames
        _is_live = drone_id in _active_missions
        if not _is_live:
            try:
                import datetime
                ls = meta.get("last_seen", "")
                if ls:
                    age = (datetime.datetime.utcnow() - datetime.datetime.strptime(ls, "%Y-%m-%dT%H:%M:%S.%fZ")).total_seconds()
                    _is_live = 0.0 <= age < 15.0
            except Exception:
                pass

        _entry_id, fields = entries[0]
        raw_jpeg = fields[b"frame"] if b"frame" in fields else fields.get("frame")
        if isinstance(raw_jpeg, str):
            raw_jpeg = raw_jpeg.encode("latin-1")

        return Response(
            content      = raw_jpeg,
            media_type   = "image/jpeg",
            headers      = {
                "Cache-Control":    "no-store, no-cache, must-revalidate",
                "X-Drone-ID":       drone_id,
                "X-Mission-ID":     mission_id,
                "X-Stream-Status":  "live" if _is_live else "draining",
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.error("latest_frame_error", extra={"drone_id": drone_id, "error": str(exc)})
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Frame retrieval failed")


@app.post(
    "/ingest/streams/{drone_id}/purge",
    summary = "Immediately purge a drone stream queue and take offline",
    tags    = ["Mission"],
)
@app.delete(
    "/ingest/streams/{drone_id}",
    summary = "Immediately delete a drone stream queue and take offline",
    tags    = ["Mission"],
)
async def purge_drone_stream(
    drone_id: str,
    claims: dict = Depends(validate_token_flexible),
):
    """
    Forcefully purges the Valkey stream queue for the given drone,
    terminating any ongoing drain process and taking the stream offline immediately.
    """
    if not _qm:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Queue unavailable")
    
    await _qm.purge_stream(drone_id)
    try:
        valkey = getattr(_qm, "_client", None) or getattr(_qm, "_redis", None)
        if valkey:
            await valkey.delete(f"mission:active:{drone_id}")
            await valkey.delete(f"drone:frames:{drone_id}")
    except Exception as e:
        log.warning(f"Failed to clean mission from Valkey: {e}")
    _qm.end_mission(drone_id, "")
    if drone_id in _qm._stats:
        del _qm._stats[drone_id]
    _active_missions.pop(drone_id, None)
    _latest_metadata.pop(drone_id, None)
    _ingest_stats.pop(drone_id, None)
    if drone_id in _drain_tasks:
        try:
            _drain_tasks.pop(drone_id).cancel()
        except Exception:
            pass
    
    ACTIVE_MISSIONS.set(len(_active_missions))
    await ws_manager.broadcast({
        "event":       "mission_drained",
        "drone_id":    drone_id,
        "timestamp":   time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
    })
    await ws_manager.broadcast({
        "event":       "drone_offline",
        "drone_id":    drone_id,
        "timestamp":   time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
    })
    log.info("stream_manually_purged", extra={"drone_id": drone_id})
    return {"status": "purged", "drone_id": drone_id}



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
