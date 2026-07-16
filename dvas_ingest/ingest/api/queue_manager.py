"""
queue_manager.py  (Redis Streams edition)
==========================================
Replaces the aio-pika / RabbitMQ queue manager with Redis Streams.
Redis Streams provide the same durable, per-drone message queuing semantics.

Stream key pattern : drone:frames:{drone_id}    (one stream per drone)
Events stream      : drone:events:confirmed

All behaviour (publish, declare, stats) is identical to the RabbitMQ version
from the consumer's perspective.
"""
from __future__ import annotations

import asyncio
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
QUEUE_MAX_LEN    = int(os.getenv("QUEUE_MAX_LENGTH",  "1000"))
QUEUE_MSG_TTL_MS = int(os.getenv("QUEUE_MSG_TTL_MS",  "30000"))


# ── Per-drone stats ────────────────────────────────────────────────────────────
@dataclass
class DroneQueueStats:
    drone_id:        str
    frames_queued:   int   = 0
    frames_total:    int   = 0
    last_frame_ts:   float = 0.0
    mission_id:      Optional[str] = None
    mission_started: Optional[float] = None
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
        if drone_id not in self._stats:
            self._stats[drone_id] = DroneQueueStats(drone_id=drone_id)
        log.debug("stream_declared key=%s maxlen=%d", key, QUEUE_MAX_LEN)

    # ── Frame publishing ──────────────────────────────────────────────────────

    async def publish_frame(
        self,
        drone_id:   str,
        frame_bytes: bytes,
        metadata:   Dict[str, Any],
    ) -> str:
        """
        Publish a frame to the per-drone Redis Stream.
        Returns the Redis stream entry ID (acts as message_id).
        """
        if not self._connected:
            await self.connect()

        key = self._stream_key(drone_id)
        fields: Dict[bytes, Any] = {
            b"frame":    frame_bytes,
            b"metadata": json.dumps(metadata).encode(),
            b"ts":       str(int(time.time() * 1000)).encode(),
        }

        entry_id: str = await self._client.xadd(
            key,
            fields,
            maxlen=QUEUE_MAX_LEN,
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
        self._stats[drone_id].mission_id      = mission_id
        self._stats[drone_id].mission_started = time.time()

    def end_mission(self, drone_id: str, mission_id: str) -> None:
        if drone_id in self._stats:
            self._stats[drone_id].mission_id = None

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
