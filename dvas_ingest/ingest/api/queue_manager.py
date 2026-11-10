"""
queue_manager.py  (Redis Streams edition)
==========================================
Replaces the aio-pika / RabbitMQ queue manager with Redis Streams.
Redis Streams provide the same durable, per-drone message queuing semantics.

Stream key pattern : mission:frames:{sid}       (one stream per mission, STREAM_KEY_MODE=mission)
                     drone:frames:{drone_id}     (one stream per drone, STREAM_KEY_MODE=drone,
                                                  and legacy streams left from before the upgrade)
Events stream      : drone:events:confirmed

Mission registry (STREAM_KEY_MODE=mission, plan Phase 2a; analytics read the same keys,
dvas_analytics/shared/mission_streams.py):
  mission:index:{sid}             HASH drone_id, mission_id, operator_sub, status
                                  (live | draining | ended), started_at, ended_at (epoch s);
                                  kept 7 days (EXPIRE) after its stream is purged
  drone:missions:{drone_id}       ZSET sid → started_at, missions whose stream still exists
  drone:active_mission:{drone_id} sid of the drone's newest mission
  sid = mission id with every character outside [A-Za-z0-9_.:-] replaced by "_"

The per-stream helpers (is_fully_consumed, unread_frames, remaining_frames, purge_stream,
get_queue_depth) take a "stream ref": a full frame-stream key, or a bare drone id meaning
drone:frames:{drone_id} (today's callers). Caches are keyed by that ref.

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
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import redis.asyncio as aioredis

log = logging.getLogger(__name__)

# ── Config (from env) ─────────────────────────────────────────────────────────
REDIS_URL        = os.getenv("REDIS_URL", "redis://redis:6379/0")
QUEUE_MAX_LEN    = int(os.getenv("QUEUE_MAX_LENGTH",  "2000"))
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

# A consumer group counts as a reader of a stream only while one of its consumers has
# touched it within this many seconds. A service that was switched off keeps its consumer
# entry for ever; it must not hold a mission's stream from draining (or the drone's
# "frames left" up). Covers service restarts and model loading with room to spare.
try:
    QUEUE_READER_IDLE_S = max(10.0, float(os.getenv("QUEUE_READER_IDLE_S", "300")))
except ValueError:
    QUEUE_READER_IDLE_S = 300.0

# Phase 2a: one stream per mission (default) or the legacy one stream per drone (rollback).
STREAM_KEY_MODE = (os.getenv("STREAM_KEY_MODE", "mission") or "mission").strip().lower()
if STREAM_KEY_MODE not in ("mission", "drone"):
    log.warning("STREAM_KEY_MODE=%r is not 'mission' or 'drone' — using 'mission'", STREAM_KEY_MODE)
    STREAM_KEY_MODE = "mission"
# Frames for an ended mission are still accepted this long after mission end (in-flight retries)
try:
    MISSION_GRACE_S = max(0.0, float(os.getenv("MISSION_GRACE_S", "30")))
except ValueError:
    MISSION_GRACE_S = 30.0
MISSION_INDEX_TTL_S = 7 * 24 * 3600      # index hash of a purged mission is kept for audit

LEGACY_PREFIX = "drone:frames:"
MISSION_PREFIX = "mission:frames:"
_FRAME_PREFIXES = (LEGACY_PREFIX, MISSION_PREFIX)
_UNSAFE_SID = re.compile(r"[^A-Za-z0-9_.:-]")


def mission_mode() -> bool:
    return STREAM_KEY_MODE == "mission"


def mission_sid(mission_id: str) -> str:
    """Valkey-safe mission id (mission ids may contain spaces)."""
    return _UNSAFE_SID.sub("_", mission_id)


def mission_stream_key(sid: str) -> str:
    return f"{MISSION_PREFIX}{sid}"


def mission_index_key(sid: str) -> str:
    return f"mission:index:{sid}"


def drone_missions_key(drone_id: str) -> str:
    return f"drone:missions:{drone_id}"


def drone_active_key(drone_id: str) -> str:
    return f"drone:active_mission:{drone_id}"


def legacy_stream_key(drone_id: str) -> str:
    return f"{LEGACY_PREFIX}{drone_id}"


def stream_key_for(ref: str) -> str:
    """A full frame-stream key is used as is; a bare drone id means its legacy stream."""
    return ref if ref.startswith(_FRAME_PREFIXES) else legacy_stream_key(ref)


def sid_of_key(key: str) -> Optional[str]:
    return key[len(MISSION_PREFIX):] if key.startswith(MISSION_PREFIX) else None


def _s(v) -> str:
    return v.decode("utf-8", errors="replace") if isinstance(v, bytes) else str(v)


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
        stream_key = stream_key_for(drone_id)
        try:
            info = await self._client.xinfo_groups(stream_key)
            return {g.get("name", "unknown"): g.get("pending", 0) for g in info}
        except Exception:
            return {}

    async def _last_id(self, stream_key: str, ref: str):
        """Id of the newest entry: from our own XADDs (no round trip), else XINFO STREAM."""
        cached = getattr(self, "_last_entry", {}).get(ref)
        # The video decoder (another process) XADDs to mission streams and records its
        # newest id in mission:video:{sid}; take the newer of the two.
        sid = sid_of_key(stream_key)
        video_last = await self._client.hget(f"mission:video:{sid}", "last_entry_id") if sid else None
        ids = [i for i in (_parse_id(cached) if cached else None,
                           _parse_id(_s(video_last)) if video_last else None) if i is not None]
        if ids:
            return max(ids)
        info = await self._client.xinfo_stream(stream_key)
        return _parse_id(info.get("last-generated-id"))

    async def _first_id(self, stream_key: str):
        first = await self._client.xrange(stream_key, min="-", max="+", count=1)
        return _parse_id(first[0][0]) if first else None

    async def is_fully_consumed(self, ref: str) -> bool:
        """
        True only when the stream has no entries, or every analytics consumer group
        (a group that has consumers) has been delivered the stream's last entry and has
        nothing pending.

        Valkey's `lag` is not used: it becomes null once MAXLEN trimming passes a group's
        position (every long mission), and a null lag used to count as "caught up" — the
        stream was deleted while an analyzer still had hundreds of frames to read.
        Groups without consumers (e.g. the placeholder "analytics" group), and groups whose
        consumers have not read for QUEUE_READER_IDLE_S (service switched off), are ignored.
        """
        if not self._connected:
            await self.connect()
        stream_key = stream_key_for(ref)
        try:
            xlen = await self._client.xlen(stream_key)
            if xlen == 0:
                return True
            groups = await self._client.xinfo_groups(stream_key)
            last_id = await self._last_id(stream_key, ref)
            readers = await self._active_groups(stream_key, groups)
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
            log.debug("is_fully_consumed check failed for %s: %s", ref, exc)
            return False

    async def _active_groups(self, stream_key: str, groups: list) -> list:
        """Groups with a consumer that read within QUEUE_READER_IDLE_S."""
        out = []
        for g in groups:
            if int(g.get("consumers", 0) or 0) == 0:
                continue
            name = g.get("name")
            name = name.decode() if isinstance(name, bytes) else str(name)
            try:
                consumers = await self._client.xinfo_consumers(stream_key, name)
                idle_ms = min(int(c.get("idle", 0) or 0) for c in consumers) if consumers else None
            except Exception:
                idle_ms = 0                     # cannot tell: count it (never drain too early)
            if idle_ms is not None and idle_ms <= QUEUE_READER_IDLE_S * 1000:
                out.append(g)
        return out

    async def remaining_frames(self, ref: str) -> int:
        """Frames the slowest analyzer still has to process (stream length if none reads it).

        Cached for 1 s: dashboards poll this for every drone.
        """
        cache = getattr(self, "_remaining_cache", None)
        if cache is None:
            cache = self._remaining_cache = {}
        hit = cache.get(ref)
        now = time.monotonic()
        if hit and now - hit[0] < 1.0:
            return hit[1]
        unread = await self.unread_frames(ref)
        value = max(unread.values()) if unread else await self.get_queue_depth(ref)
        cache[ref] = (now, value)
        return value

    async def unread_frames(self, ref: str) -> Dict[str, int]:
        """{group: frames not yet analysed} for groups still being read (pending + undelivered).

        The stream length alone is not "remaining": it also counts frames every analyzer
        has already processed.
        """
        stream_key = stream_key_for(ref)
        out: Dict[str, int] = {}
        try:
            groups = await self._client.xinfo_groups(stream_key)
            length = int(await self._client.xlen(stream_key) or 0)
            last_id = await self._last_id(stream_key, ref)
            first_id = None
            for g in await self._active_groups(stream_key, groups):
                name = g.get("name")
                name = name.decode() if isinstance(name, bytes) else str(name)
                pending = int(g.get("pending", 0) or 0)
                lag = g.get("lag")
                if lag is None:
                    # Null after MAXLEN trimming passed the group, or after XGROUP SETID
                    delivered = _parse_id(g.get("last-delivered-id"))
                    if delivered is not None and last_id is not None and delivered >= last_id:
                        lag = 0
                    else:
                        if first_id is None:
                            first_id = await self._first_id(stream_key)   # one entry, rare path
                        if delivered is None or (first_id is not None and delivered < first_id):
                            lag = length                                 # everything left is unread
                        else:
                            lag = 1                                      # unknown, but not done
                # Valkey's lag can overstate after MAXLEN trimming; never more than the stream holds
                out[name] = pending + min(int(lag), length)
        except Exception as exc:
            log.debug("unread_frames failed for %s: %s", ref, exc)
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
        stream_key: Optional[str] = None,
        maxlen:     Optional[int] = None,
    ) -> str:
        """
        Publish a frame to the per-drone Redis Stream, or to `stream_key` (a mission stream,
        STREAM_KEY_MODE=mission; the entry then also carries `mission_id`).
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

        key = stream_key or self._stream_key(drone_id)
        ref = stream_key or drone_id          # cache key, same ref the drain checks use
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

        if stream_key:
            fields["mission_id"] = str(metadata.get("mission_id") or "")
        # Video uplink (v2 R1): frames decoded from the recording carry when the server
        # received them, so analytics lag is measured from there, not from the XADD.
        if metadata.get("rx_ts_ms") is not None:
            fields["rx_ts_ms"] = str(int(metadata["rx_ts_ms"]))
            fields["source"] = str(metadata.get("source") or "h264")

        if QUEUE_WRITE_JPEG_B64:
            fields["jpeg_b64"] = base64.b64encode(frame_bytes).decode("ascii")

        if maxlen is None:
            maxlen = QUEUE_MAX_LEN if not await self._memory_tight() else max(10, QUEUE_MAX_LEN // 4)
        entry_id: str = await self._client.xadd(
            key,
            fields,
            maxlen=maxlen,
            approximate=True,
        )
        # Newest id per stream, so drain checks never have to fetch an entry (a 4K frame)
        if not hasattr(self, "_last_entry"):
            self._last_entry = {}
        self._last_entry[ref] = entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)
        if not hasattr(self, "_last_publish"):
            self._last_publish = {}
        self._last_publish[ref] = time.time()   # idle check for orphan / late-frame streams

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

    async def get_queue_depth(self, ref: str) -> int:
        if not self._connected:
            return 0
        try:
            depth = await self._client.xlen(stream_key_for(ref))
            if ref in self._stats:
                self._stats[ref].queue_depth = depth
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

    async def purge_stream(self, ref: str) -> None:
        """Unlinks/deletes the stream from Valkey when queue is drained."""
        if not self._connected:
            await self.connect()
        key = stream_key_for(ref)
        try:
            await self._client.unlink(key)
            getattr(self, "_last_entry", {}).pop(ref, None)
            getattr(self, "_remaining_cache", {}).pop(ref, None)
            getattr(self, "_last_publish", {}).pop(ref, None)
            if ref in self._stats:
                self._stats[ref].queue_depth = 0
            log.info("stream_purged key=%s", key)
        except Exception as exc:
            log.warning("stream_purge_failed key=%s error=%s", key, exc)

    # ── Mission registry (STREAM_KEY_MODE=mission, plan Phase 2a) ───────────────

    async def get_mission_index(self, sid: str) -> Optional[Dict[str, str]]:
        """mission:index:{sid} as a str dict, or None when the mission was never started
        (or its index expired 7 days after the purge)."""
        if not self._connected:
            await self.connect()
        raw = await self._client.hgetall(mission_index_key(sid))
        return {_s(k): _s(v) for k, v in raw.items()} if raw else None

    async def drone_mission_sids(self, drone_id: str) -> List[str]:
        """The drone's missions whose stream still exists, oldest start first."""
        return [_s(m) for m in await self._client.zrange(drone_missions_key(drone_id), 0, -1)]

    async def drone_active_sid(self, drone_id: str) -> Optional[str]:
        v = await self._client.get(drone_active_key(drone_id))
        return _s(v) if v else None

    async def write_mission_start(self, sid: str, drone_id: str, mission_id: str,
                                  operator_sub: str, started_at: float) -> None:
        """Index (status live), registry entry and active pointer, in one transaction."""
        idx = mission_index_key(sid)
        pipe = self._client.pipeline(transaction=True)
        pipe.hset(idx, mapping={
            "drone_id": drone_id, "mission_id": mission_id, "operator_sub": operator_sub,
            "status": "live", "started_at": repr(float(started_at)), "ended_at": "",
        })
        pipe.hdel(idx, "purged_at")
        pipe.persist(idx)                         # a re-started purged mission loses its TTL
        pipe.zadd(drone_missions_key(drone_id), {sid: float(started_at)})
        pipe.set(drone_active_key(drone_id), sid)
        await pipe.execute()

    async def set_mission_draining(self, sid: str, ended_at: Optional[float] = None) -> float:
        """Status draining; ended_at is set once (a repeated mission/end keeps the first)."""
        idx = mission_index_key(sid)
        current = await self._client.hget(idx, "ended_at")
        if current and _s(current):
            try:
                ended_at = float(_s(current))
            except ValueError:
                ended_at = None
        if ended_at is None:
            ended_at = time.time()
        await self._client.hset(idx, mapping={"status": "draining", "ended_at": repr(float(ended_at))})
        return float(ended_at)

    async def register_late_stream(self, sid: str, drone_id: str, started_at: float) -> None:
        """A frame accepted for an ended mission re-created its stream: list it again so the
        analyzers find it and the cleanup purges it (ZADD is idempotent)."""
        await self._client.zadd(drone_missions_key(drone_id), {sid: float(started_at)})

    _DEL_IF_EQUAL = ("if redis.call('GET', KEYS[1]) == ARGV[1] then "
                     "return redis.call('DEL', KEYS[1]) end return 0")

    async def finalize_mission(self, sid: str, drone_id: Optional[str],
                               purged: bool = False) -> bool:
        """Mission stream drained (or purged by an officer): UNLINK the stream, drop it from
        drone:missions, index → ended with a 7-day TTL, and clear drone:active_mission only
        if it still points to this mission. Returns True when the pointer was cleared (the
        drone has no newer mission)."""
        await self.purge_stream(mission_stream_key(sid))
        idx = mission_index_key(sid)
        now = time.time()
        pipe = self._client.pipeline(transaction=True)
        if drone_id:
            pipe.zrem(drone_missions_key(drone_id), sid)
        pipe.hget(idx, "ended_at")
        res = await pipe.execute()
        ended = _s(res[-1]) if res[-1] else ""
        fields = {"status": "ended", "ended_at": ended or repr(now)}
        if purged:
            fields["purged_at"] = repr(now)
        pipe = self._client.pipeline(transaction=True)
        pipe.hset(idx, mapping=fields)
        pipe.expire(idx, MISSION_INDEX_TTL_S)
        await pipe.execute()
        if not drone_id:
            return False
        cleared = await self._client.eval(self._DEL_IF_EQUAL, 1, drone_active_key(drone_id), sid)
        return bool(cleared)

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
