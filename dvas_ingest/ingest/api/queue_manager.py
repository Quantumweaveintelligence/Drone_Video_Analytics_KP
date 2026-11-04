"""
queue_manager.py  (Redis Streams edition)
==========================================
Replaces the aio-pika / RabbitMQ queue manager with Redis Streams.
Redis Streams provide the same durable, per-drone message queuing semantics.

Stream key pattern : drone:frames:{drone_id}    (one stream per drone)
Events stream      : drone:events:confirmed

All behaviour (publish, declare, stats) is identical to the RabbitMQ version
from the consumer's perspective.

Field layout in every stream entry
-----------------------------------
  frame      : raw JPEG bytes              — read by analytics and ingest /latest
  jpeg_b64   : base64 copy, only when QUEUE_WRITE_JPEG_B64=true (legacy readers)
  metadata   : JSON string of full FrameMetadata
  drone_id   : plain string copy           — analytics fast-path
  change_score: plain float string         — analytics fast-path
  ts         : epoch milliseconds string
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import redis.asyncio as aioredis

log = logging.getLogger(__name__)

# ── Config (from env) ─────────────────────────────────────────────────────────
REDIS_URL        = os.getenv("REDIS_URL", "redis://redis:6379/0")
QUEUE_MAX_LEN    = int(os.getenv("QUEUE_MAX_LENGTH",  "2000"))
QUEUE_MSG_TTL_MS = int(os.getenv("QUEUE_MSG_TTL_MS",  "30000"))
# Analytics read the raw `frame` field (RunPod plan 9.1); the base64 copy doubled
# Valkey memory per frame. Re-enable only for an analytics build older than 9.1.
QUEUE_WRITE_JPEG_B64 = os.getenv("QUEUE_WRITE_JPEG_B64", "false").lower() == "true"
# Streams carry no TTL, so Valkey can't evict them: if a large queue × large frames
# approaches maxmemory, every XADD would fail. Above this share of maxmemory the
# publishing drone's stream is trimmed to a quarter of QUEUE_MAX_LEN.
try:
    QUEUE_MEM_GUARD_PCT = min(100.0, max(10.0, float(os.getenv("QUEUE_MEM_GUARD_PCT", "85"))))
except ValueError:          # a bad template value must not stop ingest
    QUEUE_MEM_GUARD_PCT = 85.0
_MEM_CHECK_S = 2.0


def _parse_id(raw) -> Optional[tuple]:
    """Stream id "1700000000000-3" → (1700000000000, 3) for ordering; None if absent."""
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        ms, _, seq = str(raw).partition("-")
        return int(ms), int(seq or 0)
    except ValueError:
        return None


# ── Per-drone stats ────────────────────────────────────────────────────────────
@dataclass
class DroneQueueStats:
    drone_id:        str
    frames_queued:   int   = 0
    frames_total:    int   = 0
    last_frame_ts:   float = 0.0
    mission_id:      Optional[str] = None
    mission_started: Optional[float] = None
    mission_status:  str   = "idle"  # "active", "draining", "ended", "idle"
    drain_target_seq: Optional[int] = None
    drain_started_at: Optional[float] = None
    queue_depth:     int   = 0
    errors:          int   = 0


# ── Singleton manager ─────────────────────────────────────────────────────────
class QueueManager:
    """
    Async Redis Streams queue manager.
    Drop-in replacement for the previous aio-pika RabbitMQ manager.
    """

    def __init__(self) -> None:
        self._client:    Optional[aioredis.Redis] = None
        self._lock:      asyncio.Lock             = asyncio.Lock()
        self._connected: bool                     = False
        self._stats:     Dict[str, DroneQueueStats] = {}

    @property
    def _redis(self) -> Optional[aioredis.Redis]:
        return self._client

    # ── Connection lifecycle ──────────────────────────────────────────────────

    async def connect(self) -> None:
        async with self._lock:
            if self._connected:
                return
            try:
                self._client = aioredis.from_url(
                    REDIS_URL,
                    encoding="utf-8",
                    decode_responses=False,   # we want raw bytes for frame data
                    socket_connect_timeout=5,
                    socket_timeout=5,
                    retry_on_timeout=True,
                )
                # Verify connection
                await self._client.ping()
                self._connected = True
                log.info("redis_connected url=%s", REDIS_URL.split("@")[-1])
            except Exception as exc:
                self._connected = False
                log.error("redis_connect_failed error=%s", exc)
                raise

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._connected = False
            log.info("redis_disconnected")

    async def is_healthy(self) -> bool:
        try:
            if self._client:
                await self._client.ping()
                return True
        except Exception:
            pass
        return False

    # ── Stream key helpers ────────────────────────────────────────────────────

    @staticmethod
    def _stream_key(drone_id: str) -> str:
        return f"drone:frames:{drone_id}"

    @staticmethod
    def _events_key() -> str:
        return "drone:events:confirmed"

    # ── Queue declaration (no-op for Redis — streams auto-create) ────────────

    async def declare_drone_queue(self, drone_id: str) -> None:
        """Ensure the stream exists (create with MAXLEN cap if not)."""
        if not self._connected:
            await self.connect()
        key = self._stream_key(drone_id)
        # Add a sentinel entry to create the stream, then delete it
        try:
            eid = await self._client.xadd(
                key,
                {"_init": "1"},
                maxlen=QUEUE_MAX_LEN,
                approximate=True,
            )
            await self._client.xdel(key, eid)
        except Exception:
            pass   # stream may already exist
            
        await self.ensure_consumer_group(drone_id)
            
        if drone_id not in self._stats:
            self._stats[drone_id] = DroneQueueStats(drone_id=drone_id)
        log.debug("stream_declared key=%s maxlen=%d", key, QUEUE_MAX_LEN)

    async def ensure_consumer_group(self, drone_id: str, group_name: str = "analytics"):
        """Create consumer group if it doesn't exist. Called on stream creation."""
        stream_key = f"drone:frames:{drone_id}"
        try:
            await self._client.xgroup_create(stream_key, group_name, id="0", mkstream=True)
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                log.warning(f"Consumer group create error for {drone_id}: {e}")

    async def get_consumer_lag(self, drone_id: str) -> dict:
        """Returns {consumer_name: pending_count} for all registered consumers."""
        stream_key = f"drone:frames:{drone_id}"
        try:
            info = await self._client.xinfo_groups(stream_key)
            return {g.get("name", "unknown"): g.get("pending", 0) for g in info}
        except Exception:
            return {}

    async def is_fully_consumed(self, drone_id: str) -> bool:
        """
        True only when the stream has no entries, or every analytics consumer group
        (a group that has consumers) has been delivered the stream's last entry and has
        nothing pending.

        Valkey's `lag` is not used: it becomes null once MAXLEN trimming passes a group's
        position (every long mission), and a null lag used to count as "caught up" — the
        stream was deleted while an analyzer still had hundreds of frames to read.
        Groups without consumers (e.g. the placeholder "analytics" group) are ignored.
        """
        if not self._connected:
            await self.connect()
        stream_key = f"drone:frames:{drone_id}"
        try:
            xlen = await self._client.xlen(stream_key)
            if xlen == 0:
                return True
            groups = await self._client.xinfo_groups(stream_key)
            info = await self._client.xinfo_stream(stream_key)
            last_id = _parse_id(info.get("last-generated-id"))
            readers = [g for g in groups if int(g.get("consumers", 0) or 0) > 0]
            if not readers or last_id is None:
                return False
            for g in readers:
                if int(g.get("pending", 0) or 0) > 0:
                    return False
                delivered = _parse_id(g.get("last-delivered-id"))
                if delivered is None or delivered < last_id:
                    return False
            return True
        except Exception as exc:
            log.debug("is_fully_consumed check failed for %s: %s", drone_id, exc)
            return False

    async def remaining_frames(self, drone_id: str) -> int:
        """Frames the slowest analyzer still has to process (stream length if none reads it)."""
        unread = await self.unread_frames(drone_id)
        if unread:
            return max(unread.values())
        return await self.get_queue_depth(drone_id)

    async def unread_frames(self, drone_id: str) -> Dict[str, int]:
        """{group: frames not yet analysed} for groups with consumers (pending + undelivered).

        The stream length alone is not "remaining": it also counts frames every analyzer
        has already processed.
        """
        stream_key = f"drone:frames:{drone_id}"
        out: Dict[str, int] = {}
        try:
            groups = await self._client.xinfo_groups(stream_key)
            info = await self._client.xinfo_stream(stream_key)
            last_id = _parse_id(info.get("last-generated-id"))
            for g in groups:
                if int(g.get("consumers", 0) or 0) == 0:
                    continue
                name = g.get("name")
                name = name.decode() if isinstance(name, bytes) else str(name)
                pending = int(g.get("pending", 0) or 0)
                lag = g.get("lag")
                if lag is None:
                    # Null after MAXLEN trimming passed the group, or after XGROUP SETID
                    delivered = _parse_id(g.get("last-delivered-id"))
                    first = info.get("first-entry")
                    first_id = _parse_id(first[0]) if first else None
                    if delivered is not None and last_id is not None and delivered >= last_id:
                        lag = 0
                    elif delivered is None or (first_id is not None and delivered < first_id):
                        lag = int(info.get("length", 0) or 0)     # everything left is unread
                    else:
                        lag = 1                                   # unknown, but not done
                # Valkey's lag can overstate after MAXLEN trimming; never more than the stream holds
                out[name] = pending + min(int(lag), int(info.get("length", 0) or 0))
        except Exception as exc:
            log.debug("unread_frames failed for %s: %s", drone_id, exc)
        return out

    # ── Frame publishing ──────────────────────────────────────────────────────

    async def _memory_tight(self) -> bool:
        """True while Valkey memory is above QUEUE_MEM_GUARD_PCT of maxmemory (checked every 2 s)."""
        now = time.monotonic()
        if now - getattr(self, "_mem_checked_at", 0.0) < _MEM_CHECK_S:
            return getattr(self, "_mem_tight", False)
        self._mem_checked_at = now
        tight = False
        try:
            info = await self._client.info("memory")
            used, limit = int(info.get("used_memory", 0)), int(info.get("maxmemory", 0))
            tight = limit > 0 and used * 100 >= limit * QUEUE_MEM_GUARD_PCT
        except Exception as exc:
            log.debug("valkey memory check failed: %s", exc)
        if tight and not getattr(self, "_mem_tight", False):
            log.warning("valkey_memory_high: frame streams trimmed to %d entries per drone",
                        max(10, QUEUE_MAX_LEN // 4))
        elif not tight and getattr(self, "_mem_tight", False):
            log.info("valkey_memory_ok: frame streams back to %d entries per drone", QUEUE_MAX_LEN)
        self._mem_tight = tight
        return tight

    async def publish_frame(
        self,
        drone_id:   str,
        frame_bytes: bytes,
        metadata:   Dict[str, Any],
    ) -> str:
        """
        Publish a frame to the per-drone Redis Stream.
        Returns the Redis stream entry ID (acts as message_id).

        Field layout
        ------------
        frame        — raw JPEG bytes     (analytics + ingest /latest read this)
        jpeg_b64     — base64 copy, only with QUEUE_WRITE_JPEG_B64=true
        metadata     — full JSON metadata string
        drone_id     — plain string, analytics fast-path
        change_score — plain float string, analytics fast-path
        timestamp    — ISO-8601 timestamp string
        frame_sequence — integer as string
        ts           — epoch milliseconds string
        """
        if not self._connected:
            await self.connect()

        key = self._stream_key(drone_id)
        fields: Dict[str, Any] = {
            "frame":          frame_bytes,
            "metadata":       json.dumps(metadata),
            # Individual fields for analytics fast-path (no JSON parsing needed)
            "drone_id":       drone_id,
            "change_score":   str(metadata.get("change_score", 0.0)),
            "timestamp":      metadata.get("timestamp", ""),
            "frame_sequence": str(metadata.get("frame_sequence", 0)),
            "ts":             str(int(time.time() * 1000)),
            # Ordering hints for the latest-frame-wins sampler (RunPod plan 9.2)
            "frame_seq":      str(metadata.get("frame_sequence", 0)),
            "capture_ts":     str(metadata.get("capture_ts") or ""),
        }

        if QUEUE_WRITE_JPEG_B64:
            fields["jpeg_b64"] = base64.b64encode(frame_bytes).decode("ascii")

        maxlen = QUEUE_MAX_LEN if not await self._memory_tight() else max(10, QUEUE_MAX_LEN // 4)
        entry_id: str = await self._client.xadd(
            key,
            fields,
            maxlen=maxlen,
            approximate=True,
        )

        # Update stats
        if drone_id not in self._stats:
            self._stats[drone_id] = DroneQueueStats(drone_id=drone_id)
        s = self._stats[drone_id]
        s.frames_queued += 1
        s.frames_total  += 1
        s.last_frame_ts  = time.time()
        s.mission_id     = metadata.get("mission_id")

        log.debug("frame_published key=%s entry_id=%s", key, entry_id)
        return entry_id

    # ── Queue depth stats ─────────────────────────────────────────────────────

    async def get_queue_depth(self, drone_id: str) -> int:
        if not self._connected:
            return 0
        try:
            depth = await self._client.xlen(self._stream_key(drone_id))
            if drone_id in self._stats:
                self._stats[drone_id].queue_depth = depth
            return depth
        except Exception:
            return 0

    async def get_all_stats(self) -> Dict[str, DroneQueueStats]:
        for drone_id in list(self._stats.keys()):
            self._stats[drone_id].queue_depth = await self.get_queue_depth(drone_id)
        return dict(self._stats)

    async def get_drone_stats(self, drone_id: str) -> Optional[DroneQueueStats]:
        if drone_id not in self._stats:
            return None
        self._stats[drone_id].queue_depth = await self.get_queue_depth(drone_id)
        return self._stats[drone_id]

    # ── Mission tracking ──────────────────────────────────────────────────────

    def start_mission(self, drone_id: str, mission_id: str) -> None:
        if drone_id not in self._stats:
            self._stats[drone_id] = DroneQueueStats(drone_id=drone_id)
        self._stats[drone_id].mission_id       = mission_id
        self._stats[drone_id].mission_started  = time.time()
        self._stats[drone_id].mission_status   = "active"
        self._stats[drone_id].drain_target_seq = None

    def mark_mission_draining(self, drone_id: str, mission_id: str, last_seq: Optional[int] = None) -> None:
        if drone_id not in self._stats:
            self._stats[drone_id] = DroneQueueStats(drone_id=drone_id)
        self._stats[drone_id].mission_status   = "draining"
        self._stats[drone_id].drain_target_seq = last_seq
        self._stats[drone_id].drain_started_at = time.time()

    def end_mission(self, drone_id: str, mission_id: str) -> None:
        if drone_id in self._stats:
            self._stats[drone_id].mission_id     = None
            self._stats[drone_id].mission_status = "ended"

    async def purge_stream(self, drone_id: str) -> None:
        """Unlinks/deletes the stream from Valkey when queue is drained."""
        if not self._connected:
            await self.connect()
        key = self._stream_key(drone_id)
        try:
            await self._client.unlink(key)
            if drone_id in self._stats:
                self._stats[drone_id].queue_depth = 0
            log.info("stream_purged key=%s", key)
        except Exception as exc:
            log.warning("stream_purge_failed key=%s error=%s", key, exc)

    # ── Events ────────────────────────────────────────────────────────────────

    async def publish_event(self, event_data: Dict[str, Any]) -> None:
        if not self._connected:
            return
        try:
            await self._client.xadd(
                self._events_key(),
                {b"data": json.dumps(event_data).encode()},
                maxlen=500,
            )
        except Exception as exc:
            log.warning("event_publish_failed error=%s", exc)


# ── Singleton ─────────────────────────────────────────────────────────────────
_manager: Optional[QueueManager] = None


def get_queue_manager() -> QueueManager:
    global _manager
    if _manager is None:
        _manager = QueueManager()
    return _manager
