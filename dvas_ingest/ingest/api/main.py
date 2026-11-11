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
import re
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
    CORS_ALLOW_ORIGINS,
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
import weakref

# ── Queue manager (Redis Streams) ─────────────────────────────────────────────
import queue_manager as _qmod
import video_relay
import video_store
from queue_manager import (
    get_queue_manager,
    legacy_stream_key,
    mission_sid,
    mission_stream_key,
    sid_of_key,
)

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

WS_SEND_TIMEOUT_S = 2.0         # a client that cannot take one message in this time is dropped
WS_FRAME_EVENT_INTERVAL_S = 0.5  # frame_queued is sent at most this often per drone (2 Hz)
FRAME_LOG_INTERVAL_S = 1.0      # frame_queued is logged as a per-drone summary at most this often


class _WSManager:
    """Tracks active WebSocket connections for the live status feed."""

    def __init__(self, send_timeout_s: float = WS_SEND_TIMEOUT_S) -> None:
        self._clients: list[WebSocket] = []
        self._lock = asyncio.Lock()
        self._send_timeout_s = send_timeout_s

    @property
    def has_clients(self) -> bool:
        return bool(self._clients)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.append(ws)
        log.info("ws_client_connected", extra={"total": len(self._clients)})

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            before = len(self._clients)
            self._clients = [c for c in self._clients if c is not ws]
            removed = len(self._clients) != before
        if removed:
            log.info("ws_client_disconnected", extra={"total": len(self._clients)})

    async def _send(self, ws: WebSocket, payload: str) -> bool:
        try:
            await asyncio.wait_for(ws.send_text(payload), timeout=self._send_timeout_s)
            return True
        except asyncio.TimeoutError:
            log.warning("ws_client_send_timeout — dropping client (timeout %.1fs)", self._send_timeout_s)
            return False
        except Exception:
            return False

    async def _close_quietly(self, ws: WebSocket) -> None:
        try:
            await asyncio.wait_for(ws.close(code=1013), timeout=1.0)   # 1013 = try again later
        except Exception:
            pass

    async def broadcast(self, event: dict) -> None:
        """Send JSON event to all connected clients concurrently. A client whose send fails
        or takes longer than the send timeout is dropped, so one slow client delays neither
        the others nor the event loop for long."""
        if not self._clients:
            return
        payload = json.dumps(event)
        async with self._lock:
            clients = list(self._clients)
        results = await asyncio.gather(*(self._send(ws, payload) for ws in clients))
        for ws, ok in zip(clients, results):
            if not ok:
                await self.disconnect(ws)
                asyncio.create_task(self._close_quietly(ws))


ws_manager = _WSManager()


class _FrameEventCoalescer:
    """At most one `frame_queued` broadcast per drone per interval (default 2 Hz).

    The first frame after a quiet period is sent at once; frames arriving within the
    interval only replace the pending payload, which is sent when the interval ends.
    So the dashboard always ends up with the latest frame's numbers, and the message
    shape is unchanged. Other events are broadcast directly and never throttled."""

    def __init__(self, manager: "_WSManager", interval_s: float = WS_FRAME_EVENT_INTERVAL_S,
                 clock=time.monotonic) -> None:
        self._ws = manager
        self._interval = interval_s
        self._clock = clock
        self._last_sent: dict[str, float] = {}
        self._pending: dict[str, dict] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._tasks: set = set()

    def offer(self, drone_id: str, event: dict) -> None:
        if not self._ws.has_clients:
            self._pending.pop(drone_id, None)
            return
        now = self._clock()
        due = self._last_sent.get(drone_id, float("-inf")) + self._interval
        if now >= due and drone_id not in self._timers:
            self._send(drone_id, event, now)
            return
        self._pending[drone_id] = event
        if drone_id not in self._timers:
            loop = asyncio.get_running_loop()
            self._timers[drone_id] = loop.call_later(max(0.0, due - now), self._flush, drone_id)

    def _flush(self, drone_id: str) -> None:
        self._timers.pop(drone_id, None)
        event = self._pending.pop(drone_id, None)
        if event is not None:
            self._send(drone_id, event, self._clock())

    def _send(self, drone_id: str, event: dict, now: float) -> None:
        self._last_sent[drone_id] = now
        task = asyncio.create_task(self._ws.broadcast(event))
        self._tasks.add(task)                       # keep a reference until it finishes
        task.add_done_callback(self._tasks.discard)

    def forget(self, drone_id: str) -> None:
        """Drop per-drone state (after the drone's stream is purged)."""
        t = self._timers.pop(drone_id, None)
        if t is not None:
            t.cancel()
        self._pending.pop(drone_id, None)
        self._last_sent.pop(drone_id, None)


frame_events = _FrameEventCoalescer(ws_manager)


class _FrameLogSummary:
    """Replaces the per-frame `frame_queued` INFO line: one summary line per drone at most
    every FRAME_LOG_INTERVAL_S (frames, bytes, mean / max ingest latency since the last
    summary). Rejections and errors are still logged one by one where they happen."""

    def __init__(self, logger: logging.Logger, interval_s: float = FRAME_LOG_INTERVAL_S,
                 clock=time.monotonic) -> None:
        self._log = logger
        self._interval = interval_s
        self._clock = clock
        self._acc: dict[str, dict] = {}

    def record(self, drone_id: str, mission_id: Optional[str], n_bytes: int,
               latency_s: float, seq: Optional[int] = None) -> None:
        now = self._clock()
        a = self._acc.get(drone_id)
        if a is None:
            a = self._acc[drone_id] = {"since": now, "frames": 0, "bytes": 0,
                                       "lat_sum": 0.0, "lat_max": 0.0}
        a["frames"] += 1
        a["bytes"] += n_bytes
        a["lat_sum"] += latency_s
        a["lat_max"] = max(a["lat_max"], latency_s)
        a["mission"], a["seq"] = mission_id, seq
        if now - a["since"] >= self._interval:
            self._emit(drone_id, now)

    def flush(self, drone_id: Optional[str] = None, idle_only: bool = False) -> None:
        """Log what has accumulated (one drone, or all). With idle_only, only drones whose
        window is older than the interval — used by a periodic task so the last frames of
        a mission are not left unlogged."""
        now = self._clock()
        for did in ([drone_id] if drone_id is not None else list(self._acc)):
            a = self._acc.get(did)
            if a is None or (idle_only and now - a["since"] < self._interval):
                continue
            self._emit(did, now)

    def _emit(self, drone_id: str, now: float) -> None:
        a = self._acc.pop(drone_id)
        if not a["frames"]:
            return
        window = max(now - a["since"], 1e-9)
        mean_ms = a["lat_sum"] / a["frames"] * 1000.0
        max_ms = a["lat_max"] * 1000.0
        # The log format prints only the message, so the numbers are in the text as well.
        self._log.info(
            "frame_queued_summary drone=%s mission=%s frames=%d bytes=%d window_s=%.1f "
            "latency_ms_mean=%.1f latency_ms_max=%.1f last_seq=%s",
            drone_id, a["mission"], a["frames"], a["bytes"], window, mean_ms, max_ms, a["seq"],
            extra={"drone_id": drone_id, "mission": a["mission"], "frames": a["frames"],
                   "bytes": a["bytes"], "window_s": round(window, 2),
                   "latency_ms_mean": round(mean_ms, 1), "latency_ms_max": round(max_ms, 1),
                   "last_seq": a["seq"]},
        )


frame_log = _FrameLogSummary(log)

# ── Active missions tracker ───────────────────────────────────────────────────
_active_missions: dict[str, dict] = {}
_drain_tasks: dict = {}   # drone_id → {mission_id, started_at, operator}


# ═════════════════════════════════════════════════════════════════════════════
# MISSION-KEYED STREAMS (plan Phase 2a, STREAM_KEY_MODE=mission)
# ═════════════════════════════════════════════════════════════════════════════
# Every mission has its own stream mission:frames:{sid}, an index hash with its owner
# (the token `sub` that started it) and its drone, and is listed in drone:missions:{drone}.
# Keys and fields: queue_manager.py header. With STREAM_KEY_MODE=drone none of this runs.

DRAIN_MIN_S = 20.0      # a drained mission stays visible ("draining") at least this long
DRAIN_MAX_S = 300.0     # the drain monitor gives up after this; the cleanup loop keeps checking
ORPHAN_IDLE_S = 30.0    # a stream nobody writes to is purged only after this long without frames
LIVE_STATES = ("live", "draining")
END_ROLES = {"supervisor", "sys-admin"}

_mission_drain_tasks: dict[str, asyncio.Task] = {}      # sid → drain monitor
_drone_active_sid: dict[str, str] = {}                  # drone → sid (mirror of drone:active_mission)
_drone_locks: "weakref.WeakValueDictionary[str, asyncio.Lock]" = weakref.WeakValueDictionary()
_sid_locks: "weakref.WeakValueDictionary[str, asyncio.Lock]" = weakref.WeakValueDictionary()
_reject_log_at: dict = {}


def _mission_mode() -> bool:
    return _qmod.mission_mode()


def _named_lock(table, name: str) -> asyncio.Lock:
    """One asyncio lock per name (ingest runs one uvicorn worker). Weak values: a lock
    disappears once nobody holds or waits for it."""
    lk = table.get(name)
    if lk is None:
        lk = asyncio.Lock()
        table[name] = lk
    return lk


def _token_sub(claims: dict) -> str:
    return str(claims.get("sub") or claims.get("preferred_username") or "")


def _realm_roles(claims: dict) -> set:
    return set((claims.get("realm_access") or {}).get("roles") or [])


def _is_service_account(claims: dict) -> bool:
    return str(claims.get("preferred_username", "")).startswith("service-account-")


def _mission_http_error(code: int, error: str, message: str) -> HTTPException:
    return HTTPException(code, {"error": error, "message": message})


def _log_reject(drone_id: str, reason: str, message: str) -> None:
    """Rejected uploads are logged, but at most once per 5 s per drone and reason (an edge
    that uploads without mission/start would otherwise log 30 lines a second)."""
    now = time.monotonic()
    k = (drone_id, reason)
    if now - _reject_log_at.get(k, -1e9) >= 5.0:
        _reject_log_at[k] = now
        log.warning("frame_rejected drone=%s reason=%s %s", drone_id, reason, message)


def _f(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _iso(epoch: float) -> Optional[str]:
    if not epoch:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoch))


async def _check_frame_mission(drone_id: str, mission_id: str, claims: dict):
    """Mission-mode admission of one frame: (sid, index). Raises 409 / 403 / 503."""
    if _qm is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Queue service temporarily unavailable")
    sid = mission_sid(mission_id)
    try:
        idx = await _qm.get_mission_index(sid)
    except Exception as exc:
        log.error("mission_index_read_failed sid=%s error=%s", sid, exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Queue service temporarily unavailable")
    if not idx:
        raise _mission_http_error(409, "mission_not_started",
                                  f"Mission {mission_id!r} was not started (POST /ingest/mission/start first)")
    if idx.get("operator_sub") != _token_sub(claims):
        raise _mission_http_error(403, "not_mission_operator",
                                  "Only the operator who started the mission may upload its frames")
    if idx.get("drone_id") != drone_id:
        raise _mission_http_error(409, "drone_mismatch",
                                  f"Mission {mission_id!r} belongs to drone {idx.get('drone_id')!r}")
    if idx.get("status") in LIVE_STATES:
        return sid, idx
    # ended (stream drained, or purged by an officer): only in-flight retries, within the grace
    ended_at = _f(idx.get("ended_at"))
    if idx.get("purged_at") or not ended_at or time.time() - ended_at > _qmod.MISSION_GRACE_S:
        raise _mission_http_error(409, "mission_ended", f"Mission {mission_id!r} has ended")
    return sid, idx


async def _start_mission_registry(req: MissionStartRequest, claims: dict) -> dict:
    """Mission-mode part of mission/start: ownership checks, then index / registry / pointer.

    409 when the mission id belongs to another operator or drone, or when the drone is
    flying (live/draining) another operator's mission. The same operator re-sending start
    is idempotent (a draining/ended mission becomes live again). A new mission by the same
    operator puts their previous live mission on that drone into draining."""
    sub = _token_sub(claims)
    if not sub:
        raise _mission_http_error(403, "no_subject", "Token carries no subject")
    sid = mission_sid(req.mission_id)
    superseded: list = []
    async with _named_lock(_drone_locks, req.drone_id):
        async with _named_lock(_sid_locks, sid):
            idx = await _qm.get_mission_index(sid)
            if idx and (idx.get("operator_sub") != sub or idx.get("drone_id") != req.drone_id):
                raise _mission_http_error(
                    409, "mission_id_taken",
                    f"Mission id {req.mission_id!r} is registered to another operator or drone")
            others = []
            for other in await _qm.drone_mission_sids(req.drone_id):
                if other == sid:
                    continue
                oi = await _qm.get_mission_index(other)
                if not oi or oi.get("status") not in LIVE_STATES:
                    continue
                if oi.get("operator_sub") != sub:
                    raise _mission_http_error(
                        409, "drone_in_use",
                        f"Drone {req.drone_id!r} is flying mission {oi.get('mission_id')!r} "
                        "of another operator")
                others.append((other, oi))
            resumed = bool(idx)
            started_at = _f(idx.get("started_at")) if idx else 0.0
            started_at = started_at or time.time()
            await _qm.write_mission_start(sid, req.drone_id, req.mission_id, sub, started_at)
            _drone_active_sid[req.drone_id] = sid
        task = _mission_drain_tasks.pop(sid, None)     # resumed while draining
        if task:
            task.cancel()
        for other, oi in others:
            if oi.get("status") == "live":
                await _qm.set_mission_draining(other)
                await _video_mission_stopped(other)
                _start_mission_drain(other, req.drone_id, oi.get("mission_id") or other)
                superseded.append(oi.get("mission_id") or other)
                log.info("mission_superseded drone=%s old=%s new=%s",
                         req.drone_id, oi.get("mission_id"), req.mission_id)
    return {"sid": sid, "resumed": resumed, "superseded": superseded, "started_at": started_at}


def _start_mission_drain(sid: str, drone_id: str, mission_id: str) -> None:
    old = _mission_drain_tasks.pop(sid, None)
    if old:
        old.cancel()
    _mission_drain_tasks[sid] = asyncio.create_task(_monitor_mission_drain(sid, drone_id, mission_id))


async def _forget_drone(drone_id: str) -> None:
    """In-memory state of a drone that has no mission and no stream left."""
    _drone_active_sid.pop(drone_id, None)
    _active_missions.pop(drone_id, None)
    _latest_metadata.pop(drone_id, None)
    _ingest_stats.pop(drone_id, None)
    frame_events.forget(drone_id)
    if _qm is not None:
        _qm._stats.pop(drone_id, None)
    ACTIVE_MISSIONS.set(len(_active_missions))


async def _try_finalize_mission(sid: str, min_age_s: float, idle_s: float) -> Optional[str]:
    """Purge a mission stream once it is safe. Returns "finalized", "live" (mission is live
    again: stop draining it), "gone" (no index), or None (not yet).

    draining: ended at least min_age_s ago and every analyzer has read every frame.
    ended:    a late retry re-created the stream: no frame for idle_s and fully consumed.
    live:     never purged."""
    key = mission_stream_key(sid)
    async with _named_lock(_sid_locks, sid):          # no frame is added between check and purge
        idx = await _qm.get_mission_index(sid)
        if not idx:
            return "gone"
        st = idx.get("status")
        if st == "live":
            return "live"
        now = time.time()
        if now - _f(idx.get("ended_at")) < min_age_s:
            return None
        if now - getattr(_qm, "_last_publish", {}).get(key, 0.0) < idle_s:
            return None
        if sid in _video_conns:
            return None                                 # still receiving video
        vdone = await _qm._client.hget(video_store.video_key(sid), "decoder_done")
        if vdone is not None and vdone not in (b"1", "1"):
            return None                                 # the decoder has not fed every frame yet
        existed = bool(await _qm._client.exists(key))
        if existed and not await _qm.is_fully_consumed(key):
            return None
        drone_id = idx.get("drone_id") or ""
        await _qm.finalize_mission(sid, drone_id)
    mission_id = idx.get("mission_id") or sid
    log.info("[DRAIN] mission %s (drone %s): fully consumed — stream purged", mission_id, drone_id)
    await _after_mission_finalized(drone_id, mission_id, announce=(st != "ended" or existed))
    return "finalized"


async def _after_mission_finalized(drone_id: str, mission_id: Optional[str], announce: bool = True) -> None:
    still_active = bool(drone_id) and (await _qm.drone_active_sid(drone_id)) is not None
    if drone_id and not still_active:
        try:
            await _qm._client.delete(f"mission:active:{drone_id}")
        except Exception as e:
            log.warning(f"Failed to clean mission from Valkey: {e}")
        await _forget_drone(drone_id)
    if announce and drone_id:
        # drone_still_active: a newer mission of this drone is still registered, so the
        # drone itself is not offline (the event shape is otherwise unchanged)
        await ws_manager.broadcast({"event": "mission_drained", "drone_id": drone_id,
                                    "mission_id": mission_id, "drone_still_active": still_active})


async def _monitor_mission_drain(sid: str, drone_id: str, mission_id: str) -> None:
    """Purge the mission's stream once every analyzer has read it (after DRAIN_MIN_S).
    Gives up after DRAIN_MAX_S; the periodic cleanup keeps checking after that."""
    start = time.monotonic()
    try:
        while time.monotonic() - start < DRAIN_MAX_S:
            try:
                res = await _try_finalize_mission(sid, DRAIN_MIN_S, 0.0)
                if res is not None:
                    return
            except Exception as e:
                log.warning(f"[DRAIN] {drone_id}/{mission_id}: error checking consumers: {e}")
            await asyncio.sleep(2.0)
        log.warning(f"[DRAIN] {drone_id}/{mission_id}: not consumed after {DRAIN_MAX_S}s — "
                    "left to the periodic cleanup / manual purge")
    except asyncio.CancelledError:
        pass
    finally:
        if _mission_drain_tasks.get(sid) is asyncio.current_task():
            _mission_drain_tasks.pop(sid, None)


async def _scan_keys(pattern: str) -> list[str]:
    client = _qm._client
    cursor, out = 0, []
    for _ in range(200):
        cursor, batch = await client.scan(cursor=cursor, match=pattern, count=200)
        out.extend(k.decode() if isinstance(k, bytes) else k for k in batch)
        if cursor == 0:
            break
    return out


async def _registry_drones() -> list[str]:
    return [k.split(":", 2)[2] for k in await _scan_keys("drone:missions:*") if k.count(":") >= 2]


async def _mission_cleanup_pass() -> None:
    """Restart-safe drain: every mission stream / registry entry that is not live is purged
    once consumed (draining: DRAIN_MIN_S after its end; ended with late frames: after
    ORPHAN_IDLE_S without frames). Streams without an index are purged once consumed and idle."""
    sids: dict[str, None] = {}
    for key in await _scan_keys("mission:frames:*"):
        sids[sid_of_key(key)] = None
    for drone_id in await _registry_drones():
        for sid in await _qm.drone_mission_sids(drone_id):
            sids[sid] = None
    for sid in sids:
        if not sid or sid in _mission_drain_tasks:
            continue                                   # its drain monitor is on it
        try:
            idx = await _qm.get_mission_index(sid)
            if idx is None:
                key = mission_stream_key(sid)
                idle = time.time() - getattr(_qm, "_last_publish", {}).get(key, 0.0) >= ORPHAN_IDLE_S
                if idle and await _qm.is_fully_consumed(key):
                    async with _named_lock(_sid_locks, sid):
                        await _qm.purge_stream(key)
                    log.info("[AUTO-CLEANUP] mission stream %s without an index fully consumed — purged", sid)
                continue
            if idx.get("status") == "draining":
                await _try_finalize_mission(sid, DRAIN_MIN_S, 0.0)
            elif idx.get("status") == "ended":
                await _try_finalize_mission(sid, 0.0, ORPHAN_IDLE_S)
        except Exception as e:
            log.warning(f"[AUTO-CLEANUP] mission {sid}: {e}")


async def _drone_stream_entries(drone_id: str, extra_sids=()) -> list[tuple]:
    """(stream key, sid or None, index or None) for every stream of the drone: legacy first,
    then its missions oldest first (the active one included even before its first frame)."""
    client = _qm._client
    out = []
    legacy = legacy_stream_key(drone_id)
    if await client.exists(legacy):
        out.append((legacy, None, None))
    sids = list(await _qm.drone_mission_sids(drone_id))
    active = _drone_active_sid.get(drone_id) or await _qm.drone_active_sid(drone_id)
    for s in list(extra_sids) + ([active] if active else []):
        if s and s not in sids:
            sids.append(s)
    for sid in sids:
        out.append((mission_stream_key(sid), sid, await _qm.get_mission_index(sid)))
    return out


async def _drone_view(drone_id: str, extra_sids=()) -> dict:
    """Per-drone aggregate of all its streams (mission mode)."""
    client = _qm._client
    active_sid = await _qm.drone_active_sid(drone_id)
    missions, total, remaining = [], 0, 0
    any_live = any_draining = False
    active_idx = None
    for key, sid, idx in await _drone_stream_entries(drone_id, extra_sids):
        try:
            n = int(await client.xlen(key) or 0)
        except Exception:
            n = 0
        try:
            rem = await _qm.remaining_frames(key) if n else 0
        except Exception:
            rem = n
        total += n
        remaining += rem
        st = (idx or {}).get("status") or ("legacy" if sid is None else "ended")
        any_live |= st == "live"
        any_draining |= st == "draining"
        if sid is not None and sid == active_sid:
            active_idx = idx
        missions.append({
            "mission_id":  (idx or {}).get("mission_id") or (None if sid is None else sid),
            "status":      st,
            "frame_count": n,
            "remaining":   rem,
            "stream_key":  key,
            "started_at":  _iso(_f((idx or {}).get("started_at"))),
        })
    if any_live:
        mission_stat = "active"
    elif any_draining:
        mission_stat = "draining"
    else:
        mission_stat = "ended" if missions else "idle"
    return {
        "missions": missions, "frame_count": total, "remaining": remaining,
        "active_sid": active_sid, "active_idx": active_idx,
        "mission_status": mission_stat, "is_active": any_live or any_draining,
        "stream_key": (mission_stream_key(active_sid) if active_sid
                       else (missions[-1]["stream_key"] if missions else legacy_stream_key(drone_id))),
    }


async def _drone_remaining(drone_id: str) -> tuple[int, str]:
    """(frames the slowest analyzer still has to read, queue name) for a drone, both modes."""
    if not _qm:
        return 0, legacy_stream_key(drone_id)
    if not _mission_mode():
        return await _qm.remaining_frames(drone_id), f"drone:frames:{drone_id}"
    try:
        v = await _drone_view(drone_id)
        return v["remaining"], v["stream_key"]
    except Exception as exc:
        log.debug("drone_remaining failed for %s: %s", drone_id, exc)
        return 0, legacy_stream_key(drone_id)


async def _resolve_drone_stream(drone_id: str, for_live: bool = False) -> Optional[str]:
    """Stream with the drone's newest frames: active mission, else newest mission whose
    stream exists, else legacy. for_live: the active mission's key even before its first
    frame (an MJPEG reader blocks on it until frames arrive)."""
    client = _qm._client
    if not _mission_mode():
        return legacy_stream_key(drone_id)
    active = await _qm.drone_active_sid(drone_id)
    if active and await client.exists(mission_stream_key(active)):
        return mission_stream_key(active)
    for sid in reversed(await _qm.drone_mission_sids(drone_id)):
        if await client.exists(mission_stream_key(sid)):
            return mission_stream_key(sid)
    legacy = legacy_stream_key(drone_id)
    if await client.exists(legacy):
        return legacy
    if for_live:
        return mission_stream_key(active) if active else legacy
    return None


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup → connect Redis + declare streams; Shutdown → close."""
    global _qm
    log.info("ingest_server_starting", extra={"version": API_VERSION})
    _qm = get_queue_manager()
    try:
        await _qm.connect()
        if not _mission_mode():
            for did in KNOWN_DRONES:
                await _qm.declare_drone_queue(did)
        # mission mode: no stream before mission/start; analytics create their own groups
        asyncio.create_task(_auto_cleanup_consumed_streams())
        log.info("ingest_server_ready — redis_streams_declared stream_key_mode=%s",
                 _qmod.STREAM_KEY_MODE, extra={"drones": len(KNOWN_DRONES)})

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
                        drone_id = key_str.split(":", 2)[-1]   # ids may contain ":"
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

        if _mission_mode():
            try:
                await _restore_mission_registry()
            except Exception as e:
                log.warning(f"Failed to restore the mission registry from Valkey: {e}")
            try:
                await _video_restore()
            except Exception as e:
                log.warning(f"Failed to restore video ingest state: {e}")

    except Exception as exc:
        log.error("startup_redis_connect_failed", extra={"error": str(exc)})
        # Continue anyway — will reconnect on first frame

    yield

    log.info("ingest_server_shutting_down")
    if _qm:
        await _qm.close()


async def _restore_mission_registry() -> None:
    """Mission mode, after a restart: active-mission pointers into memory, and an
    _active_missions entry for drones whose active mission is still live/draining but had
    no mission:active hash. Draining missions are drained by the periodic cleanup."""
    for key in await _scan_keys("drone:active_mission:*"):
        drone_id = key.split(":", 2)[2]
        sid = await _qm.drone_active_sid(drone_id)
        if not sid:
            continue
        _drone_active_sid[drone_id] = sid
        idx = await _qm.get_mission_index(sid) or {}
        if idx.get("status") in LIVE_STATES and drone_id not in _active_missions:
            _active_missions[drone_id] = {
                "mission_id": idx.get("mission_id"),
                "status":     "active" if idx.get("status") == "live" else "draining",
                "started_at": _iso(_f(idx.get("started_at"))),
            }
            _latest_metadata.setdefault(drone_id, {}).update({
                "mission_id": idx.get("mission_id"),
                "mission_status": _active_missions[drone_id]["status"],
            })
        log.info(f"[RECOVERY] drone {drone_id}: active mission {idx.get('mission_id') or sid}")
    ACTIVE_MISSIONS.set(len(_active_missions))


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
    allow_origins=CORS_ALLOW_ORIGINS,
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
    - 503 — Valkey unavailable

    STREAM_KEY_MODE=mission (plan Phase 2a): the frame goes to `mission:frames:{sid}`.
    - 409 `mission_not_started` — no mission/start for this mission id
    - 403 `not_mission_operator` — token subject is not the mission's operator
    - 409 `drone_mismatch` — metadata drone_id differs from the mission's drone
    - 409 `mission_ended` — mission ended more than MISSION_GRACE_S ago (or was purged)
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
    stream_key = None
    is_active_stream = True     # the drone's current mission (telemetry, live events)
    if _mission_mode():
        # Phase 2a: the frame goes to its own mission's stream, only for the operator who
        # started that mission; a frame for an ended mission only within the grace period.
        sid = mission_sid(mission_id)
        async with _named_lock(_sid_locks, sid):     # never between a drain check and its purge
            try:
                sid, idx = await _check_frame_mission(drone_id, mission_id, claims)
            except HTTPException as exc:
                if exc.status_code in (403, 409):
                    reason = (exc.detail or {}).get("error", "rejected") if isinstance(exc.detail, dict) else "rejected"
                    _record_rejection(drone_id)
                    FRAMES_RECEIVED.labels(drone_id=drone_id, status="rejected_mission").inc()
                    _log_reject(drone_id, reason, f"mission={mission_id!r} seq={meta.frame_sequence}")
                raise
            stream_key = mission_stream_key(sid)
            try:
                msg_id = await _qm.publish_frame(drone_id, data, meta.model_dump(), stream_key=stream_key)
                if idx.get("status") == "ended":
                    # late retry: its stream may have been purged — list it again for the
                    # analyzers and the cleanup
                    await _qm.register_late_stream(sid, drone_id, _f(idx.get("started_at")) or time.time())
            except Exception as exc:
                log.error("publish_failed", extra={"drone_id": drone_id, "error": str(exc)})
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Queue service temporarily unavailable",
                )
        is_active_stream = _drone_active_sid.get(drone_id, sid) == sid
    else:
        try:
            msg_id = await _publish_frame(drone_id, meta.model_dump(), data)
        except Exception as exc:
            log.error("publish_failed", extra={"drone_id": drone_id, "error": str(exc)})
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Queue service temporarily unavailable",
            )

    # ── Record stats ──────────────────────────────────────────────────────────
    _record_frame(drone_id, len(data), mission_id if is_active_stream else None, meta.frames_total)
    FRAMES_RECEIVED.labels(drone_id=drone_id, status="queued").inc()
    FRAMES_BYTES.labels(drone_id=drone_id).inc(len(data))
    elapsed = time.monotonic() - t_start
    INGEST_LATENCY.labels(drone_id=drone_id).observe(elapsed)

    queue_name = stream_key or f"drone:frames:{drone_id}"
    frame_log.record(drone_id, mission_id, len(data), elapsed, meta.frame_sequence)
    if not is_active_stream:
        # A late retry for an earlier mission of this drone: queued in that mission's
        # stream, but the drone's telemetry and live events stay on its current mission.
        return IngestResponse(status="queued", drone_id=drone_id, frame_sequence=meta.frame_sequence,
                              queue_name=queue_name, message_id=msg_id)

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

    # ── Broadcast to WS clients (coalesced to ≤ 2 Hz per drone) ──────────────
    frame_events.offer(drone_id, {
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

    return IngestResponse(
        status         = "queued",
        drone_id       = drone_id,
        frame_sequence = meta.frame_sequence,
        queue_name     = queue_name,
        message_id     = msg_id,
    )


# ═════════════════════════════════════════════════════════════════════════════
# VIDEO UPLINK (RunPod v2 plan R1) — H.264 MPEG-TS over WebSocket
# ═════════════════════════════════════════════════════════════════════════════
#
# The edge streams the whole video plus one `meta` message per frame (contract: v2 plan §3).
# This process only receives: it records the bytes (video_store), relays them to go2rtc for
# live viewing (video_relay), and keeps the drone's live telemetry/status current. Frames for
# the analytics are decoded from the recording by the separate video-decoder process, at
# the pace the analytics consume (no frame is lost when they fall behind).

VIDEO_INGEST = os.getenv("DVAS_VIDEO_INGEST", "1").lower() in ("1", "true", "yes")
VIDEO_MAX_KBPS = max(500, int(os.getenv("DVAS_VIDEO_MAX_KBPS", "12000") or 12000))
VIDEO_MAX_STREAMS = max(1, int(os.getenv("DVAS_MAX_LIVE_STREAMS", "8") or 8))
VIDEO_MAX_MSG = 256 * 1024
VIDEO_HELLO_TIMEOUT_S = 10.0
VIDEO_IDLE_TIMEOUT_S = 120.0        # no message this long (pings do not count): the edge is gone; Cloudflare drops at 100 s
VIDEO_FINISH_TIMEOUT_S = 60.0       # sealing the recording may take this long before we move on
VIDEO_STATS_EVERY_S = 2.0
VIDEO_LIVE_UPDATE_S = 0.5          # drone telemetry / dashboard events at most this often
_VIDEO_TEL_KEYS = ("latitude", "longitude", "altitude_m", "abs_alt", "speed_mps", "battery_pct",
                   "heading_deg", "gb_yaw", "gb_pitch", "gb_roll", "focal_len", "dzoom_ratio")
_VIDEO_TEL_RANGE = {"latitude": (-90.0, 90.0), "longitude": (-180.0, 180.0),
                    "altitude_m": (0.0, 10000.0), "battery_pct": (0.0, 100.0), "speed_mps": (0.0, 500.0)}


class _VideoClose(Exception):
    def __init__(self, code: int, error: str, detail: str = ""):
        super().__init__(f"{code} {error}")
        self.code, self.error, self.detail = code, error, detail


class _VideoConn:
    """One edge connection streaming one mission's video."""

    def __init__(self, ws: WebSocket, sid: str, drone_id: str, mission_id: str):
        self.ws, self.sid, self.drone_id, self.mission_id = ws, sid, drone_id, mission_id
        self.closed = asyncio.Event()
        self.close_code: Optional[int] = None
        self.frames = 0
        self.bytes = 0
        self.dup = 0
        self.last_seq = 0
        self.live_slot: Optional[str] = None       # go2rtc stream this mission is relayed to
        self.pending_meta: Optional[dict] = None   # newest frame meta not yet in the live state
        self.win_frames = 0
        self.win_bytes = 0
        self._win: list = []                   # (t, bytes) for the bitrate window
        self._over = 0
        self.stopping = False                  # the server closed it (mission ended, replaced)
        self.rx: Optional[asyncio.Future] = None   # the receive in progress

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if self.close_code != -1:              # after `bye` the mission's video stays ended
            self.close_code = code
        self.stopping = True
        try:
            await self.ws.close(code=code, reason=reason[:120])
        except Exception:
            pass
        # The handler may be waiting in receive() for a message that never comes once the
        # socket is closed from here: end that wait, so it seals the recording and frees the
        # mission's live slot and its place in the stream limit.
        if self.rx is not None and not self.rx.done():
            self.rx.cancel()

    def note_bytes(self, n: int, now: float) -> Optional[float]:
        """Add received bytes; every 5 s returns the window's kbps (else None)."""
        self._win.append((now, n))
        if now - self._win[0][0] < 5.0:
            return None
        span = max(1e-3, now - self._win[0][0])
        kbps = sum(b for _, b in self._win) * 8 / 1000 / span
        self._win = []
        return kbps


_video_conns: dict[str, _VideoConn] = {}      # sid → open connection

# go2rtc accepts pushes only into streams declared in its config (etc/go2rtc.yaml): a fixed
# pool of slots, mapped to missions in Valkey (live:slot:<name> = sid) while they stream.
LIVE_SLOTS = [f"live{n:02d}" for n in range(1, 17)]
# A freed slot is not reused for this long: nginx closes a viewer's socket after 30 s without
# video, so no viewer of the last mission can still be attached when another mission gets it.
LIVE_SLOT_COOLDOWN_S = 90
# A grid tile asks for the 720p sub-stream through /ingest/video/live?sub=1 and repeats the ask
# while it plays; the relay transcodes only while this key exists.
LIVE_SUB_WANT_S = 45
_DEL_IF_EQUAL = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0"


async def _live_slot_acquire(sid: str) -> Optional[str]:
    client = _qm._client
    for name in LIVE_SLOTS:
        cur = await client.get(f"live:slot:{name}")
        if cur is not None and (cur.decode() if isinstance(cur, bytes) else cur) == sid:
            return name
    for name in LIVE_SLOTS:
        if await client.exists(f"live:cooldown:{name}"):
            continue          # viewers of its last mission may still be attached (≤ nginx idle timeout)
        if await client.set(f"live:slot:{name}", sid, nx=True):
            return name
    return None


async def _live_slot_release(sid: str, name: Optional[str]) -> None:
    if name:
        try:
            if await _qm._client.eval(_DEL_IF_EQUAL, 1, f"live:slot:{name}", sid):
                await _qm._client.set(f"live:cooldown:{name}", sid, ex=LIVE_SLOT_COOLDOWN_S)
            await _qm._client.hdel(video_store.video_key(sid), "live_slot")
        except Exception as exc:
            log.debug("live slot release failed (%s %s): %s", sid, name, exc)


def _clean_meta(m: dict) -> Optional[dict]:
    """A frame meta from the edge, validated (None when unusable)."""
    try:
        seq = int(m["seq"])
        pts = float(m["pts_ms"])
    except (KeyError, TypeError, ValueError):
        return None
    if seq < 1 or seq > 10**12:
        return None
    out = {"seq": seq, "pts_ms": round(pts, 3), "key": bool(m.get("key"))}
    cap = _f(m.get("capture_ts"), 0.0)
    out["capture_ts"] = cap if 1e9 < cap < 1e10 else None
    try:
        out["change_score"] = max(0.0, min(1.0, float(m.get("change_score") or 0.0)))
    except (TypeError, ValueError):
        out["change_score"] = 0.0
    tel_in = m.get("tel") if isinstance(m.get("tel"), dict) else {}
    tel = {}
    for k in _VIDEO_TEL_KEYS:
        v = tel_in.get(k)
        if v is None:
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if v != v:                                   # NaN
            continue
        lo, hi = _VIDEO_TEL_RANGE.get(k, (-1e7, 1e7))
        tel[k] = max(lo, min(hi, v))
    if "gps_lock" in tel_in and isinstance(tel_in.get("gps_lock"), bool):
        tel["gps_lock"] = tel_in["gps_lock"]
    out["tel"] = tel
    return out


def _video_live_update(conn: _VideoConn, meta: dict, claims: dict, n_bytes: int) -> None:
    """Drone telemetry, ingest stats and dashboard events from the newest frame meta
    (the same state /ingest/frame keeps current for JPEG uploads)."""
    drone_id, mission_id = conn.drone_id, conn.mission_id
    if _drone_active_sid.get(drone_id, conn.sid) != conn.sid:
        return
    tel = meta["tel"]
    ts_iso = _iso(meta["capture_ts"] or time.time())
    active_m = _active_missions.get(drone_id, {})
    lat, lon = tel.get("latitude"), tel.get("longitude")
    _latest_metadata[drone_id] = {
        **_latest_metadata.get(drone_id, {}),
        "drone_id": drone_id, "mission_id": mission_id,
        "mission_name": active_m.get("mission_name"),
        "operator": active_m.get("operator", extract_username(claims)),
        "location": active_m.get("location"),
        **{k: tel.get(k) for k in _VIDEO_TEL_KEYS},
        "change_score": meta["change_score"], "frame_sequence": meta["seq"], "timestamp": ts_iso,
        "last_seen": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "gps_lock": tel.get("gps_lock") if "gps_lock" in tel else (
            lat is not None and lon is not None and (lat != 0.0 or lon != 0.0)),
        "transport": "h264_ws",
    }
    frame_events.offer(drone_id, {
        "event": "frame_queued", "drone_id": drone_id, "mission_id": mission_id,
        "frame_sequence": meta["seq"], "change_score": meta["change_score"], "timestamp": ts_iso,
        "bytes": n_bytes, "latency_ms": 0.0, "latitude": lat, "longitude": lon,
        "altitude_m": tel.get("altitude_m"), "battery_pct": tel.get("battery_pct"),
        "speed_mps": tel.get("speed_mps"), "transport": "h264_ws",
    })


async def _video_hash(sid: str) -> dict:
    raw = await _qm._client.hgetall(video_store.video_key(sid))
    return {(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in (raw or {}).items()}


async def _video_stats(conn: _VideoConn, rx_frames: int, rx_bytes: int, span: float) -> dict:
    h = await _video_hash(conn.sid)
    last_rx = int(_f(h.get("last_seq_rx"), 0))
    try:
        window = max((await _qm.unread_frames(mission_stream_key(conn.sid))).values() or [0])
    except Exception:
        window = 0
    pub_fps = round(_f(h.get("pub_fps"), 0.0), 1)
    behind = _f(h.get("behind_live_s"), 0.0)
    return {"type": "stats", "rx_fps": round(rx_frames / span, 1) if span > 0 else 0.0,
            "rx_kbps": round(rx_bytes * 8 / 1000 / span) if span > 0 else 0,
            "decoded_fps": pub_fps, "analytics_fps": pub_fps,
            "analytics_lag_frames": int(window + behind * _video_analytics_fps()),
            "behind_live_s": round(behind, 1),
            "last_seq": last_rx}


async def _video_mission_stopped(sid: str) -> None:
    """The mission stopped taking frames (mission/end, superseded, purged): close its video
    connection. The decoder finishes the recording, then the drain can complete."""
    conn = _video_conns.get(sid)
    if conn is not None:
        await conn.close(1000, "mission ended")


async def _video_restore() -> None:
    """After a restart no connection is open: receiving → paused, live slots free."""
    for key in await _scan_keys("live:slot:*"):
        await _qm._client.delete(key)
    for key in await _scan_keys("mission:video:*"):
        try:
            await _qm._client.hdel(key, "live_slot")
        except Exception:
            pass
        try:
            if (await _qm._client.hget(key, "status")) in (b"receiving", "receiving"):
                await _qm._client.hset(key, "status", "paused")
        except Exception as exc:
            log.warning("video state restore failed for %s: %s", key, exc)


async def _video_handshake(ws: WebSocket, drone_id: str, mission_id: str):
    """Auth, hello, mission ownership and limits. Returns (claims, sid, hello)."""
    auth_h = ws.headers.get("authorization") or ""
    if not auth_h.lower().startswith("bearer "):
        raise _VideoClose(4401, "unauthorized", "Authorization: Bearer <token> required")
    try:
        claims = await validate_token_string(auth_h.split(" ", 1)[1].strip())
    except HTTPException as exc:
        raise _VideoClose(4401 if exc.status_code == 401 else 4403, "unauthorized", str(exc.detail))
    try:
        from models import check_drone_id, check_mission_id   # noqa: PLC0415
        check_drone_id(drone_id)
        check_mission_id(mission_id)
    except ValueError as exc:
        raise _VideoClose(1008, "bad_request", str(exc))
    try:
        raw = await asyncio.wait_for(ws.receive_text(), VIDEO_HELLO_TIMEOUT_S)
        hello = json.loads(raw)
    except asyncio.TimeoutError:
        raise _VideoClose(1008, "bad_request", "hello expected first")
    except (ValueError, KeyError, RuntimeError):
        raise _VideoClose(1008, "bad_request", "hello must be a JSON text message")
    if not isinstance(hello, dict) or hello.get("type") != "hello" or hello.get("v") != 1:
        raise _VideoClose(1008, "bad_request", "expected {\"type\":\"hello\",\"v\":1,...}")
    if hello.get("drone_id") != drone_id or hello.get("mission_id") != mission_id:
        raise _VideoClose(1008, "bad_request", "hello drone_id / mission_id differ from the URL")
    if str(hello.get("codec", "")).lower() != "h264":
        raise _VideoClose(4415, "unsupported_codec", "only H.264 is accepted")
    if str(hello.get("container", "")).lower() != "mpegts":
        raise _VideoClose(4415, "unsupported_codec", "only MPEG-TS is accepted")
    w, h, fps = int(_f(hello.get("width"))), int(_f(hello.get("height"))), _f(hello.get("fps"))
    if not (16 <= w <= 7680 and 16 <= h <= 4320 and 1 <= fps <= 120):
        raise _VideoClose(1008, "bad_request", "width/height/fps out of range")
    try:
        sid, idx = await _check_frame_mission(drone_id, mission_id, claims)
    except HTTPException as exc:
        err = exc.detail.get("error", "") if isinstance(exc.detail, dict) else ""
        if exc.status_code == 403:
            raise _VideoClose(4403, err or "forbidden", str(exc.detail))
        if exc.status_code == 409:
            raise _VideoClose(4409, err or "mission_not_live", str(exc.detail))
        raise _VideoClose(1011, "server_error", "queue unavailable")
    if idx.get("status") != "live":
        raise _VideoClose(4409, "mission_not_live", f"mission {mission_id!r} is {idx.get('status')}")
    ok, free_gb, free_pct = video_store.disk_ok()
    if not ok:
        log.error("video_refused_disk_low sid=%s free_gb=%s free_pct=%s", sid, free_gb, free_pct)
        raise _VideoClose(1013, "disk_full", f"recording disk low ({free_gb} GB, {free_pct} % free)")
    others = [s for s in _video_conns if s != sid]
    if len(others) >= VIDEO_MAX_STREAMS:
        raise _VideoClose(1013, "too_many_streams", f"{VIDEO_MAX_STREAMS} live streams already")
    return claims, sid, hello


if VIDEO_INGEST:
    @app.websocket("/ingest/video/ws")
    async def ingest_video_ws(ws: WebSocket, drone_id: str = "", mission_id: str = "") -> None:
        """Continuous H.264 (MPEG-TS) uplink of one mission — contract in v2 plan §3."""
        await ws.accept()
        conn: Optional[_VideoConn] = None
        rec = relay = None
        try:
            claims, sid, hello = await _video_handshake(ws, drone_id, mission_id)
            old = _video_conns.get(sid)
            if old is not None:                         # a newer connection replaces the older
                await old.close(1000, "replaced by a newer connection")
                try:
                    await asyncio.wait_for(old.closed.wait(), 5.0)
                except asyncio.TimeoutError:
                    raise _VideoClose(1013, "busy", "previous connection still closing")
            conn = _VideoConn(ws, sid, drone_id, mission_id)
            _video_conns[sid] = conn
            vkey = video_store.video_key(sid)
            h = await _video_hash(sid)
            conn_no = int(_f(h.get("conn"), 0)) + 1
            conn.last_seq = int(_f(h.get("last_seq_rx"), 0))
            rec = await asyncio.to_thread(video_store.SegmentRecorder, sid, conn_no)
            conn.live_slot = await _live_slot_acquire(sid) if video_relay.ENABLED else None
            relay = video_relay.LiveRelay(
                conn.live_slot, want_sub=lambda slot=conn.live_slot: _qm._client.exists(f"live:sub_want:{slot}"),
            ) if conn.live_slot else None
            await _qm._client.hset(vkey, mapping={
                "status": "receiving", "conn": conn_no, "drone_id": drone_id, "mission_id": mission_id,
                "width": int(_f(hello.get("width"))), "height": int(_f(hello.get("height"))),
                "fps": _f(hello.get("fps")), "codec": "h264", "decoder_done": 0,
                **({"live_slot": conn.live_slot} if conn.live_slot else {}),
                **({} if h.get("started_at") else {"started_at": repr(time.time())})})
            await _qm._client.persist(vkey)
            await ws.send_text(json.dumps({"type": "ready", "v": 1, "analytics_fps": _video_analytics_fps(),
                                           "max_kbps": VIDEO_MAX_KBPS, "last_seq": conn.last_seq}))
            log.info("video_connected drone=%s mission=%s conn=%d %sx%s@%s last_seq=%d",
                     drone_id, mission_id, conn_no, hello.get("width"), hello.get("height"),
                     hello.get("fps"), conn.last_seq)
            await _video_receive_loop(conn, claims, rec, relay, vkey)
        except _VideoClose as exc:
            log.warning("video_refused drone=%s mission=%s code=%d %s %s",
                        drone_id, mission_id, exc.code, exc.error, exc.detail)
            try:
                await ws.send_text(json.dumps({"type": "error", "code": exc.error, "detail": exc.detail}))
            except Exception:
                pass
            try:
                await ws.close(code=exc.code, reason=exc.error[:120])
            except Exception:
                pass
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            log.error("video_connection_error drone=%s mission=%s: %s", drone_id, mission_id, exc)
            try:
                await ws.close(code=1011)
            except Exception:
                pass
        finally:
            # In its own task: a cancelled handler (client gone, shutdown) still seals the
            # recording and frees the mission for the next connection.
            done = asyncio.ensure_future(_video_finish(ws, conn, rec, relay))
            try:
                await asyncio.shield(done)
            except BaseException:
                pass


async def _video_finish(ws: WebSocket, conn: Optional[_VideoConn], rec, relay) -> None:
    if conn is not None:
        log.info("video_closing drone=%s mission=%s close_code=%s", conn.drone_id, conn.mission_id,
                 conn.close_code)
    try:
        if conn is not None and rec is not None:
            try:
                await _qm._client.hset(video_store.video_key(conn.sid), mapping={
                    "last_seq_rx": conn.last_seq, "rx_ts_last": repr(time.time()),
                    "bytes": rec.total_bytes})
            except Exception:
                pass
        if rec is not None:
            try:
                await asyncio.wait_for(asyncio.to_thread(rec.close), VIDEO_FINISH_TIMEOUT_S)
            except Exception as exc:
                log.error("video recording close failed sid=%s: %s", conn.sid if conn else "?", exc)
        if relay is not None:
            await relay.close()
        if conn is not None:
            await _live_slot_release(conn.sid, conn.live_slot)
            try:
                await _qm._client.hset(video_store.video_key(conn.sid), "status",
                                       "ended" if conn.close_code == -1 else "paused")
            except Exception:
                pass
            log.info("video_disconnected drone=%s mission=%s frames=%d bytes=%d dup=%d",
                     conn.drone_id, conn.mission_id, conn.frames, conn.bytes, conn.dup)
    finally:
        if conn is not None:
            if _video_conns.get(conn.sid) is conn:
                _video_conns.pop(conn.sid, None)
            conn.closed.set()
            if conn.close_code == -1:                  # after `bye`: closed once the recording is sealed
                try:
                    await ws.close(code=1000)
                except Exception:
                    pass


@app.get("/ingest/video/live/{drone_id}", summary="Live video of a drone's current mission (internal)",
         tags=["Video"])
async def video_live_info(drone_id: str, sub: int = 0) -> dict:
    """go2rtc stream slot and state of the drone's current mission video — for the dashboard
    backend. No JWT, like /ingest/status: ingest listens on 127.0.0.1 and nginx refuses this
    path from outside (the backend's service token has no DVAS role). 404: no video now."""
    if _qm is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Queue unavailable")
    sid = _drone_active_sid.get(drone_id) or await _qm.drone_active_sid(drone_id)
    h = await _video_hash(sid) if sid else {}
    slot = h.get("live_slot")
    if not slot or h.get("status") != "receiving" or sid not in _video_conns:
        raise HTTPException(404, "No live video for this drone")
    src = slot
    if sub and video_relay.SUBSTREAM:
        await _qm._client.set(f"live:sub_want:{slot}", sid, ex=LIVE_SUB_WANT_S)
        src = f"{slot}_sub"
    return {"drone_id": drone_id, "mission_id": h.get("mission_id"), "sid": sid, "src": src, "main_src": slot,
            "sub": src != slot,
            "width": int(_f(h.get("width"))), "height": int(_f(h.get("height"))), "fps": _f(h.get("fps")),
            "analytics_fps": _f(h.get("pub_fps")), "behind_live_s": _f(h.get("behind_live_s"))}


@app.get("/ingest/video/slot/{slot}", summary="Which mission a live stream slot carries (internal)",
         tags=["Video"])
async def video_slot_owner(slot: str) -> dict:                # internal, like video_live_info
    base = slot[:-4] if slot.endswith("_sub") else slot          # liveNN_sub belongs to liveNN's mission
    if base not in LIVE_SLOTS:
        raise HTTPException(404, "Unknown slot")
    raw = await _qm._client.get(f"live:slot:{base}")
    sid = (raw.decode() if isinstance(raw, bytes) else raw) if raw else None
    h = await _video_hash(sid) if sid else {}
    if not sid or not h.get("drone_id"):
        raise HTTPException(404, "Slot is free")
    return {"src": slot, "sid": sid, "drone_id": h.get("drone_id"), "mission_id": h.get("mission_id")}


def _video_analytics_fps() -> float:
    try:
        return max(1.0, min(60.0, float(os.getenv("DVAS_ANALYTICS_FPS", "20"))))
    except ValueError:
        return 20.0


async def _video_receive_loop(conn: _VideoConn, claims: dict, rec, relay, vkey: str) -> None:
    ticker = asyncio.create_task(_video_ticker(conn, claims, rec, vkey))
    try:
        await _video_receive(conn, claims, rec, relay, vkey)
    finally:
        ticker.cancel()
        try:
            await ticker
        except BaseException:
            pass
        if conn.pending_meta is not None:
            _video_live_update(conn, conn.pending_meta, claims, conn.win_bytes)


async def _video_ticker(conn: _VideoConn, claims: dict, rec, vkey: str) -> None:
    """Every VIDEO_LIVE_UPDATE_S: drone live state and receive progress; every
    VIDEO_STATS_EVERY_S: a `stats` message to the edge — also while it sends nothing."""
    win_start = last_stats = time.monotonic()
    saved_seq = None
    while True:
        await asyncio.sleep(VIDEO_LIVE_UPDATE_S)
        now = time.monotonic()
        try:
            if conn.pending_meta is not None:
                _video_live_update(conn, conn.pending_meta, claims, conn.win_bytes)
                conn.pending_meta = None
            if saved_seq != conn.last_seq:
                await _qm._client.hset(vkey, mapping={
                    "last_seq_rx": conn.last_seq, "rx_ts_last": repr(time.time()),
                    "cur_seg": rec.idx or 0, "bytes": rec.total_bytes})
                saved_seq = conn.last_seq
            if now - last_stats >= VIDEO_STATS_EVERY_S:
                stats = await _video_stats(conn, conn.win_frames, conn.win_bytes, now - win_start)
                conn.win_frames = conn.win_bytes = 0
                win_start = last_stats = now
                await conn.ws.send_text(json.dumps(stats))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.debug("video ticker %s: %s", conn.sid, exc)


async def _video_receive(conn: _VideoConn, claims: dict, rec, relay, vkey: str) -> None:
    ws = conn.ws
    next_key = False                  # the next binary chunk starts a keyframe
    bytes_since_meta = 0
    while True:
        conn.rx = asyncio.ensure_future(ws.receive())
        try:
            msg = await asyncio.wait_for(conn.rx, VIDEO_IDLE_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise _VideoClose(1001, "idle", f"nothing received for {VIDEO_IDLE_TIMEOUT_S:.0f} s")
        except asyncio.CancelledError:
            if conn.stopping:
                return                                 # closed by the server (conn.close)
            raise
        finally:
            conn.rx = None
        if msg.get("type") == "websocket.disconnect":
            return
        data = msg.get("bytes")
        if data is not None:
            if len(data) > VIDEO_MAX_MSG or len(data) % 188:
                raise _VideoClose(1009 if len(data) > VIDEO_MAX_MSG else 1003, "bad_chunk",
                                  "binary messages carry whole TS packets, at most 256 KiB")
            rec.data(data)
            if relay is not None:
                await relay.feed(data, next_key)
            next_key = False
            conn.bytes += len(data)
            conn.win_bytes += len(data)
            bytes_since_meta += len(data)
            kbps = conn.note_bytes(len(data), time.monotonic())
            if kbps is not None:
                conn._over = conn._over + 1 if kbps > VIDEO_MAX_KBPS * 1.25 else 0
                if conn._over >= 2:
                    raise _VideoClose(4413, "over_bitrate", f"{kbps:.0f} kbps > {VIDEO_MAX_KBPS}")
            continue
        try:
            doc = json.loads(msg.get("text") or "")
        except ValueError:
            raise _VideoClose(1003, "bad_message", "text messages must be JSON")
        kind = doc.get("type") if isinstance(doc, dict) else None
        items = (doc.get("items") or []) if kind == "meta_batch" else [doc] if kind == "meta" else []
        if kind == "bye":
            conn.close_code = -1                       # clean end: no more data for this mission
            await _qm._client.hset(vkey, "status", "ended")
            return
        if kind not in ("meta", "meta_batch") or not isinstance(items, list) or len(items) > 10:
            continue                                   # unknown / malformed: ignored
        for raw in items:
            m = _clean_meta(raw) if isinstance(raw, dict) else None
            if m is None:
                continue
            rx = time.time()
            if m["key"]:
                next_key = True
                await asyncio.to_thread(rec.meta, m, rx)   # may close a segment (fsync)
            else:
                rec.meta(m, rx)
            if m["seq"] <= conn.last_seq:
                conn.dup += 1                          # resent after a reconnect: kept in the recording
                continue
            conn.last_seq = m["seq"]
            conn.frames += 1
            conn.win_frames += 1
            conn.pending_meta = m
            _record_frame(conn.drone_id, bytes_since_meta, conn.mission_id)
            FRAMES_RECEIVED.labels(drone_id=conn.drone_id, status="video").inc()
            FRAMES_BYTES.labels(drone_id=conn.drone_id).inc(bytes_since_meta)
            frame_log.record(conn.drone_id, conn.mission_id, bytes_since_meta, 0.0, m["seq"])
            bytes_since_meta = 0


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
    if _qm and _mission_mode():
        # every drone with a registered mission stream, and legacy streams left in place
        try:
            known_ids |= set(await _registry_drones()) | set(_active_missions.keys())
            known_ids |= {k.split(":", 2)[2] for k in await _scan_keys("drone:frames:*") if k.count(":") >= 2}
        except Exception as exc:
            log.debug("status registry scan failed: %s", exc)

    for drone_id in sorted(known_ids):
        stats      = _ingest_stats.get(drone_id, {})
        depth, queue_name = await _drone_remaining(drone_id)
        total_queued += depth
        QUEUE_DEPTH.labels(drone_id=drone_id).set(depth)

        q_stats = DroneQueueStats(
            queue_name     = queue_name,
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
    depth, queue_name = await _drone_remaining(drone_id)
    QUEUE_DEPTH.labels(drone_id=drone_id).set(depth)

    q_stats = DroneQueueStats(
        queue_name     = queue_name,
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
    if _mission_mode():
        # Phase 2a: ownership checks + registry (409 on conflicts). Earlier missions of the
        # drone keep their own streams and drains.
        if _qm is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Queue service temporarily unavailable")
        reg = await _start_mission_registry(req, claims)
        log.info("mission_registered drone=%s mission=%s sid=%s resumed=%s superseded=%s",
                 req.drone_id, req.mission_id, reg["sid"], reg["resumed"], reg["superseded"])
    # Cancel any pending drain from a previous mission on the same drone
    elif req.drone_id in _drain_tasks:
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
        frame_log.flush(idle_only=True)        # summary for drones that stopped sending
        if _mission_mode():
            try:
                await _mission_cleanup_pass()
                await _cleanup_orphan_streams()
            except Exception as e:
                log.warning(f"[AUTO-CLEANUP] Error: {e}")
            continue
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
                    frame_events.forget(drone_id)
                    if drone_id in _qm._stats:
                        del _qm._stats[drone_id]
                    await ws_manager.broadcast({"event": "mission_drained", "drone_id": drone_id, "mission_id": mid})
            await _cleanup_orphan_streams()
        except Exception as e:
            log.warning(f"[AUTO-CLEANUP] Error: {e}")




async def _cleanup_orphan_streams() -> None:
    """Streams that belong to no mission (frames that arrived after a mission was drained and
    its stream deleted re-create it) are removed once every analyzer has read them and no
    frame has arrived for ORPHAN_IDLE_S. Without this they stayed on the dashboard as
    "DRAINING — 1 frames remaining" forever."""
    client = getattr(_qm, "_client", None)
    if client is None:
        return
    cursor, keys = 0, []
    for _ in range(200):
        cursor, batch = await client.scan(cursor=cursor, match="drone:frames:*", count=100)
        keys.extend(batch)
        if cursor == 0:
            break
    now = time.time()
    for key in keys:
        key = key.decode() if isinstance(key, bytes) else key
        drone_id = key.split(":", 2)[-1]
        if not drone_id:
            continue
        if _mission_mode():
            # Legacy per-drone stream (written before the upgrade): nothing writes to it any
            # more, so it is purged as soon as every analyzer has read it — even while the
            # drone flies a new mission in its own stream.
            if now - getattr(_qm, "_last_publish", {}).get(key, 0.0) < ORPHAN_IDLE_S:
                continue
            if not await _qm.is_fully_consumed(key):
                continue
            log.info(f"[AUTO-CLEANUP] {drone_id}: legacy stream fully consumed — purging")
            await _qm.purge_stream(key)
            await _after_mission_finalized(drone_id, None)
            continue
        if drone_id in _active_missions:
            continue
        st = _qm._stats.get(drone_id)
        last_frame = st.last_frame_ts if st else 0.0
        if now - last_frame < ORPHAN_IDLE_S:
            continue                                   # still receiving: not an orphan yet
        if not await _qm.is_fully_consumed(drone_id):
            continue                                   # analyzers are still working it off
        log.info(f"[AUTO-CLEANUP] {drone_id}: stream without a mission fully consumed — purging")
        await _qm.purge_stream(drone_id)
        _latest_metadata.pop(drone_id, None)
        _ingest_stats.pop(drone_id, None)
        frame_events.forget(drone_id)
        _qm._stats.pop(drone_id, None)
        await ws_manager.broadcast({"event": "mission_drained", "drone_id": drone_id, "mission_id": None})


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
                frame_events.forget(drone_id)
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

    if _mission_mode():
        return await _mission_end_registry(req, claims, ts)

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
    frame_log.flush(req.drone_id)      # last partial summary of this mission

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


async def _mission_end_registry(req: MissionEndRequest, claims: dict, ts: str) -> MissionResponse:
    """Mission mode mission/end: only the mission's operator, a supervisor / sys-admin, or
    the backend service account. Marks the mission draining (ended_at = now, kept on a
    repeat) and starts its drain monitor."""
    if _qm is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Queue service temporarily unavailable")
    sid = mission_sid(req.mission_id)
    idx = await _qm.get_mission_index(sid)
    if not idx:
        raise _mission_http_error(409, "mission_not_started", f"Mission {req.mission_id!r} was not started")
    if idx.get("drone_id") != req.drone_id:
        raise _mission_http_error(409, "drone_mismatch",
                                  f"Mission {req.mission_id!r} belongs to drone {idx.get('drone_id')!r}")
    if not (idx.get("operator_sub") == _token_sub(claims)
            or _realm_roles(claims) & END_ROLES or _is_service_account(claims)):
        raise _mission_http_error(403, "not_mission_operator",
                                  "Only the mission's operator, a supervisor or an administrator may end it")

    async with _named_lock(_sid_locks, sid):
        idx = await _qm.get_mission_index(sid) or idx
        state = idx.get("status")
        if state in LIVE_STATES:
            await _qm.set_mission_draining(sid)
            state = "draining"
    await _video_mission_stopped(sid)
    if state == "draining" and sid not in _mission_drain_tasks:
        _start_mission_drain(sid, req.drone_id, req.mission_id)

    is_current = (_drone_active_sid.get(req.drone_id) or await _qm.drone_active_sid(req.drone_id)) == sid
    if is_current and state == "draining":
        _qm.mark_mission_draining(req.drone_id, req.mission_id, req.frames_sent)
        m = _active_missions.setdefault(req.drone_id, {"mission_id": req.mission_id, "drone_id": req.drone_id})
        m["status"] = "draining"
        m["drain_started_at"] = time.time()
        try:
            await _qm._client.hset(f"mission:active:{req.drone_id}", "status", "draining")
        except Exception as e:
            log.warning(f"Failed to update mission status in Valkey: {e}")
        if req.drone_id in _latest_metadata:
            _latest_metadata[req.drone_id]["mission_status"] = "draining"
            _latest_metadata[req.drone_id]["status"] = "draining"
            _latest_metadata[req.drone_id]["drain_started_at"] = ts
        frame_log.flush(req.drone_id)      # last partial summary of this mission

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
        "data":        {"frames_sent": req.frames_sent, "status": state},
    }))
    log.info("mission_ending_draining drone=%s mission=%s frames_sent=%s status=%s ended_by=%s",
             req.drone_id, req.mission_id, req.frames_sent, state, extract_username(claims))
    return MissionResponse(status=state or "draining", drone_id=req.drone_id,
                           mission_id=req.mission_id, timestamp=ts)


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
    if _mission_mode():
        try:
            return await _active_streams_mission_mode()
        except Exception as exc:
            log.warning("streams_active_error: %s", exc)
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

            # Frames the slowest analyzer still has to read (frame_count also counts
            # frames that were already analysed and wait only for cleanup)
            try:
                remaining = await _qm.remaining_frames(drone_id) if frame_count else 0
            except Exception:
                remaining = frame_count
            drones.append({
                "drone_id":        drone_id,
                "stream_key":      key_str,
                "frame_count":     frame_count,
                "frames_received": frame_count,
                "messages_ready":  remaining,
                "status":          stream_status,
                "mission_status":  mission_stat,
                "mission_id":      meta.get("mission_id") or _active_missions.get(drone_id, {}).get("mission_id"),
                "mission_name":    meta.get("mission_name") or _active_missions.get(drone_id, {}).get("mission_name"),
                "operator":        meta.get("operator") or _active_missions.get(drone_id, {}).get("operator"),
                "location":        meta.get("location") or _active_missions.get(drone_id, {}).get("location"),
                "last_seen":       last_seen_str,
                "started_at":      active_mission_info.get("started_at"),   # mission start (dashboard uptime)
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



def _recently_seen(last_seen_str: Optional[str]) -> bool:
    """A frame within the last 15 s — strong live signal."""
    if not last_seen_str:
        return False
    try:
        last_seen_dt = datetime.datetime.strptime(last_seen_str, "%Y-%m-%dT%H:%M:%S.%fZ")
        age_sec = (datetime.datetime.utcnow() - last_seen_dt).total_seconds()
        return 0.0 <= age_sec < 15.0
    except Exception:
        return False


async def _active_streams_mission_mode() -> dict:
    """streams/active with STREAM_KEY_MODE=mission: one entry per DRONE, aggregating its
    mission streams and any legacy drone:frames stream. frame_count = total XLEN,
    messages_ready = sum of what the slowest analyzer still has to read per stream,
    mission_id = the drone's active mission, missions = per-stream breakdown."""
    client = _qm._client
    drone_ids: dict[str, list] = {d: [] for d in await _registry_drones()}
    for key in await _scan_keys("drone:frames:*"):
        if key.count(":") >= 2:
            drone_ids.setdefault(key.split(":", 2)[2], [])
    for d in _active_missions:
        drone_ids.setdefault(d, [])
    # mission streams missing from their drone's registry (should not happen; never hide frames)
    listed = set()
    for d in list(drone_ids):
        listed.update(await _qm.drone_mission_sids(d))
    for key in await _scan_keys("mission:frames:*"):
        sid = sid_of_key(key)
        if sid and sid not in listed:
            idx = await _qm.get_mission_index(sid)
            if idx and idx.get("drone_id"):
                drone_ids.setdefault(idx["drone_id"], []).append(sid)

    drones = []
    for drone_id, extra in drone_ids.items():
        v = await _drone_view(drone_id, extra)
        frame_count = v["frame_count"]
        meta = _latest_metadata.get(drone_id) or {}
        active_info = _active_missions.get(drone_id, {})
        last_seen_str = meta.get("last_seen")
        recently_seen = _recently_seen(last_seen_str)
        is_active_mission = v["is_active"]
        mission_stat = v["mission_status"]

        if frame_count == 0 and not is_active_mission and not recently_seen:
            legacy = legacy_stream_key(drone_id)
            if drone_id not in KNOWN_DRONES and await client.exists(legacy):
                try:
                    await client.delete(legacy)          # empty legacy stream (as before)
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
        if stream_status == "offline" and frame_count == 0:
            continue

        aidx = v["active_idx"] or {}
        drones.append({
            "drone_id":        drone_id,
            "stream_key":      v["stream_key"],
            "frame_count":     frame_count,
            "frames_received": frame_count,
            "messages_ready":  v["remaining"],
            "status":          stream_status,
            "mission_status":  mission_stat,
            "mission_id":      aidx.get("mission_id") or meta.get("mission_id") or active_info.get("mission_id"),
            "mission_name":    meta.get("mission_name") or active_info.get("mission_name"),
            "operator":        meta.get("operator") or active_info.get("operator"),
            "location":        meta.get("location") or active_info.get("location"),
            "last_seen":       last_seen_str,
            "started_at":      active_info.get("started_at") or _iso(_f(aidx.get("started_at"))),
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
            "missions":        v["missions"],
        })
    drones.sort(key=lambda d: (d["status"] == "live", d["frame_count"]), reverse=True)
    return {"drones": drones, "count": len(drones)}


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
        # mission mode: the drone's active mission stream (else its newest, else legacy)
        stream_key = await _resolve_drone_stream(drone_id)
        entries = (await _qm._client.xrevrange(stream_key, max="+", min="-", count=1)
                   if stream_key else [])
        if not entries:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No frames available for drone {drone_id}")

        meta = _latest_metadata.get(drone_id) or {}
        mission_id = meta.get("mission_id") or ""

        # Determine if stream is truly live based on active mission OR recent frames
        _is_live = drone_id in _active_missions
        sid = sid_of_key(stream_key)
        if sid is not None:
            f_mid = entries[0][1].get(b"mission_id") or entries[0][1].get("mission_id")
            idx = await _qm.get_mission_index(sid) or {}
            mission_id = (f_mid.decode() if isinstance(f_mid, bytes) else f_mid) or idx.get("mission_id") or mission_id
            _is_live = idx.get("status") == "live"
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

    Only the dashboard backend (its Keycloak service account) or a control room officer /
    senior officer / administrator token may purge; an operator token may not (H8;
    control room added 2026-11-06 — the dashboard forwards the officer's own token).
    """
    realm_roles = set((claims.get("realm_access") or {}).get("roles") or [])
    is_service = str(claims.get("preferred_username", "")).startswith("service-account-")
    if not (is_service or realm_roles & {"sys-admin", "supervisor", "control-room"}):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not permitted to purge streams")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", drone_id or ""):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid drone id")
    if not _qm:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Queue unavailable")

    purged_missions: list = []
    if _mission_mode():
        purged_missions = await _purge_drone_missions(drone_id)
        await _qm.purge_stream(legacy_stream_key(drone_id))
    await _qm.purge_stream(drone_id)
    try:
        valkey = getattr(_qm, "_client", None) or getattr(_qm, "_redis", None)
        if valkey:
            await valkey.delete(f"mission:active:{drone_id}")
            await valkey.delete(f"drone:frames:{drone_id}")
    except Exception as e:
        log.warning(f"Failed to clean mission from Valkey: {e}")
    _drone_active_sid.pop(drone_id, None)
    _qm.end_mission(drone_id, "")
    if drone_id in _qm._stats:
        del _qm._stats[drone_id]
    _active_missions.pop(drone_id, None)
    _latest_metadata.pop(drone_id, None)
    _ingest_stats.pop(drone_id, None)
    frame_events.forget(drone_id)
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
    log.info("stream_manually_purged drone=%s missions=%s by=%s", drone_id, purged_missions,
             extract_username(claims), extra={"drone_id": drone_id})
    resp = {"status": "purged", "drone_id": drone_id}
    if _mission_mode():
        resp["missions"] = purged_missions
    return resp


async def _purge_drone_missions(drone_id: str) -> list:
    """Officer purge (mission mode): every mission stream of the drone is unlinked, its
    index set to ended (+ purged_at, so the edge's further uploads are refused at once)
    with the 7-day TTL, and the drone's registry and active pointer are removed."""
    client = _qm._client
    sids = list(await _qm.drone_mission_sids(drone_id))
    active = await _qm.drone_active_sid(drone_id)
    if active and active not in sids:
        sids.append(active)
    purged = []
    for sid in sids:
        task = _mission_drain_tasks.pop(sid, None)
        if task:
            task.cancel()
        async with _named_lock(_sid_locks, sid):
            idx = await _qm.get_mission_index(sid)
            if idx and idx.get("drone_id") not in (None, "", drone_id):
                continue                     # not this drone's mission (stale pointer)
            await _qm.finalize_mission(sid, drone_id, purged=True)
        await _video_mission_stopped(sid)        # the decoder sees purged_at and stops
        purged.append((idx or {}).get("mission_id") or sid)
    await client.delete(f"drone:missions:{drone_id}", f"drone:active_mission:{drone_id}")
    return purged



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
    resolved_at = 0.0

    BOUNDARY = b"--frame\r\n"
    KEEPALIVE = b"--frame\r\nContent-Type: text/plain\r\n\r\n\r\n"

    while True:
        if _mission_mode() and time.monotonic() - resolved_at >= BLOCK_MS / 1000.0:
            # follow the drone's active mission (a new mission is a new stream)
            resolved_at = time.monotonic()
            try:
                key = await _resolve_drone_stream(drone_id, for_live=True)
            except Exception:
                key = None
            if key and key != stream_key:
                stream_key, last_id = key, "$"
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
