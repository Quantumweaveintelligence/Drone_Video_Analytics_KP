# -*- coding: utf-8 -*-
"""
DVAS Analytics — frame sampler and live FPS stats (RunPod plan 9.1 / 9.2 / 10.1).

Every analyzer loop reads frames through FrameSampler instead of calling
XREADGROUP per drone:

  * One XREADGROUP covers all drones that are due (BLOCK when nothing is new).
  * mode="ordered" (default, every analyzer but crowd): every frame is analysed,
    in order, one per drone per round. If the GPU falls behind the backlog stays
    in the queue and is worked off — also after the edge stops streaming. Frames
    are lost only when the queue overflows (MAXLEN); they are counted as `skipped`
    from gaps in `frame_seq`. (Video missions never overflow: the video decoder feeds
    a small window and waits for the slowest active service — v2 plan R1.)
  * Acknowledged after analysis (v2 R1 4a): a frame is XACKed when the service asks for
    its next round, i.e. once it has finished with it. Frames waiting in the re-order
    buffer or the prefetched round stay pending, so a restart re-reads them (at most one
    frame per drone is analysed twice after a crash).
  * mode="newest" (crowd): only the newest entry per drone is analysed; older ones
    are XACKed unread and counted as `skipped`.
  * A per-drone rate cap (the governor's `dvas:fps_cap:<service>`, else
    DVAS_FPS_MAX) keeps one service from taking the whole GPU.
  * Live stats (input fps, analysed fps, skipped, inference ms, lag) are
    published every 2 s to `dvas:svc_stats:<service>` for the governor and the
    Detection Models page.

Frame bytes come from the raw `frame` field; `jpeg_b64` is only a fallback
for entries written by an older ingest.

Streams (plan Phase 2a): each mission has its own stream (shared/mission_streams.py).
The drone id stays the key everywhere here; per drone the sampler reads its streams
oldest first and moves to the next one when the current mission has ended and this
service has read all of it. At that boundary the drone's trackers and state are reset,
so a new mission never continues the last one's tracks.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, Iterable, List, Optional, Tuple

from shared import mission_streams

log = logging.getLogger(__name__)

STATS_KEY = "dvas:svc_stats:{service}"
CAP_KEY = "dvas:fps_cap:{service}"
STATS_TTL_S = 10
STATS_EVERY_S = 2.0
CAP_REFRESH_S = 2.0
READ_CHUNK = 32
# Re-ordering (plan D1): the edge uploads several frames at once and retries failures, so
# frames reach the stream out of order. Frames are held this long to be analysed in
# frame_seq order; one arriving after a newer frame was analysed is "late".
REORDER_MS = max(0, int(os.getenv("DVAS_REORDER_MS", "300") or 0))
REORDER_READ = 16          # entries read per drone per round while re-ordering
SEQ_RESET = 1000           # a jump back by more than this is a new sequence, not a late frame
# A drone that sends nothing for this long gets fresh trackers / state (plan A3): the next
# frames belong to a new flight, and lost tracks must not re-match across missions.
try:
    IDLE_RESET_S = max(10.0, float(os.getenv("DVAS_IDLE_RESET_S", "120")))
except ValueError:
    IDLE_RESET_S = 120.0
GROUP_RETRY_S = 5.0
RESOLVE_EVERY_S = 1.0      # how often a drone's current stream is re-checked
_FPS_WINDOW_S = 5.0
# Hard limits for a cap coming from Valkey (defence in depth: the key is
# written by the gateway, but never trust a number that throttles everything)
CAP_MIN, CAP_MAX = 0.5, 60.0


def default_cap() -> float:
    try:
        return max(CAP_MIN, min(CAP_MAX, float(os.getenv("DVAS_FPS_MAX", "30"))))
    except ValueError:
        return 30.0


def stream_key(drone_id: str) -> str:
    """Legacy per-drone stream (STREAM_KEY_MODE=drone and pre-upgrade data)."""
    return mission_streams.legacy_key(drone_id)


def _g(d: dict, k: str):
    """Field of an XINFO reply from either a decoding or a raw client."""
    return d.get(k, d.get(k.encode()))


def _s(v) -> str:
    return v.decode("utf-8", errors="replace") if isinstance(v, bytes) else str(v)


def decode_fields(fields: dict, drone_id: str, entry_id: str) -> dict:
    """Text fields as str; the raw JPEG `frame` field stays bytes."""
    out = {"entry_id": entry_id, "drone_id": drone_id}
    for k, v in fields.items():
        key = _s(k)
        if key == "frame":
            out["frame"] = v if isinstance(v, (bytes, bytearray)) else _s(v).encode("latin-1")
            continue
        if isinstance(v, bytes):
            try:
                out[key] = v.decode("utf-8")
            except UnicodeDecodeError:
                continue
        else:
            out[key] = v
    return out


def frame_bytes(fields: dict) -> Optional[bytes]:
    """JPEG bytes of an entry: raw `frame` first, then the legacy `jpeg_b64`."""
    raw = fields.get("frame")
    if raw:
        return bytes(raw)
    b64 = fields.get("jpeg_b64")
    if b64:
        try:
            return base64.b64decode(b64, validate=True) or None
        except (ValueError, TypeError):
            return None
    return None


# Per-process decode time per drone, for the stage split (plan A4)
DECODE_MS: Dict[str, float] = {}
_LateFrame = None


def _late_cls():
    global _LateFrame
    if _LateFrame is None:
        import numpy as np  # noqa: PLC0415

        class LateFrame(np.ndarray):
            """A frame that arrived after a newer one was analysed (plan D1)."""
        _LateFrame = LateFrame
    return _LateFrame


def is_late(frame) -> bool:
    return _LateFrame is not None and isinstance(frame, _LateFrame)


def decode_frame(fields: dict):
    """BGR ndarray of an entry, or None when missing / undecodable.

    Late frames come back as a LateFrame view (same pixels) so trackers can tell.
    """
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    data = frame_bytes(fields)
    if not data:
        return None
    t = time.perf_counter()
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if fields.get("drone_id"):
        DECODE_MS[fields["drone_id"]] = (time.perf_counter() - t) * 1000
    if img is not None and fields.get("_late") == "1":
        img = img.view(_late_cls())
    return img


def _decode_quietly(fields: dict):
    try:
        return decode_frame(fields)
    except Exception as exc:
        log.debug("frame decode failed for %s: %s", fields.get("drone_id"), exc)
        return None


def _seq_of(fields: dict) -> Optional[int]:
    try:
        return int(fields.get("frame_seq") or fields.get("frame_sequence") or "")
    except (TypeError, ValueError):
        return None


def _seq_gap(older: dict, newer: dict) -> int:
    """Frames between two entries by frame_seq (0 when unknown)."""
    try:
        a = int(_s(older.get(b"frame_seq") or older.get("frame_seq") or ""))
        b = int(_s(newer.get(b"frame_seq") or newer.get("frame_seq") or ""))
        return max(0, b - a - 1)
    except (TypeError, ValueError):
        return 0


class RateCap:
    """Minimum interval between analysed frames, per drone."""

    def __init__(self, fps: float, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._last: Dict[str, float] = {}
        self.fps = fps

    @property
    def fps(self) -> float:
        return self._fps

    @fps.setter
    def fps(self, value: float) -> None:
        self._fps = max(CAP_MIN, min(CAP_MAX, float(value)))
        # 2 % slack so a 30 fps source is not halved by timer jitter at a 30 fps cap
        self._interval = 0.98 / self._fps

    def due(self, drone_id: str) -> bool:
        last = self._last.get(drone_id)
        return last is None or self._clock() - last >= self._interval

    def wait_s(self, drone_ids: Iterable[str]) -> float:
        now = self._clock()
        waits = [self._last.get(d, 0.0) + self._interval - now for d in drone_ids]
        return max(0.0, min(waits)) if waits else 0.0

    def mark(self, drone_id: str) -> None:
        self._last[drone_id] = self._clock()

    def forget(self, drone_id: str) -> None:
        self._last.pop(drone_id, None)


@dataclass
class DroneStats:
    processed: Deque[float] = field(default_factory=deque)   # monotonic ts of analysed frames
    received: Deque[float] = field(default_factory=deque)    # monotonic ts of every entry read
    skipped: int = 0                    # frames lost (gaps in frame_seq; never arrived)
    late: int = 0                       # arrived after a newer frame was analysed
    infer_ms: Optional[float] = None    # EMA of model time per frame
    total_ms: Optional[float] = None    # EMA of whole per-frame handling
    lag_ms: Optional[float] = None      # ingest → analysis start of the last frame
    stages: Dict[str, float] = field(default_factory=dict)   # EMA ms per pipeline stage

    @staticmethod
    def _ema(old: Optional[float], new: float, a: float = 0.2) -> float:
        return new if old is None else old + a * (new - old)

    @staticmethod
    def _rate(q: Deque[float], now: float) -> float:
        while q and now - q[0] > _FPS_WINDOW_S:
            q.popleft()
        if len(q) < 2:
            return float(len(q)) / _FPS_WINDOW_S
        span = max(now - q[0], 1e-3)
        return (len(q) - 1) / span if span >= 1.0 else len(q) / _FPS_WINDOW_S


class FrameSampler:
    """Frame reader (in order, or newest only) + stats publisher for one analyzer service."""

    def __init__(self, service: str, group: str, consumer: str, *,
                 mode: str = "ordered", late_policy: str = "analyse",
                 governed: bool = True, cap_fps: Optional[float] = None,
                 block_ms: int = 50, redis_factory: Optional[Callable] = None,
                 clock: Callable[[], float] = time.monotonic, stream_mode: Optional[str] = None):
        if mode not in ("ordered", "newest"):
            raise ValueError(f"mode must be 'ordered' or 'newest', not {mode!r}")
        if late_policy not in ("analyse", "skip"):
            raise ValueError(f"late_policy must be 'analyse' or 'skip', not {late_policy!r}")
        self.service = service
        self.mode = mode
        # "analyse": late frames are handed over flagged (fields["_late"]); services whose
        # tracker can take them without moving backwards (BatchTracker). "skip": counted only.
        self.late_policy = late_policy
        self.group = group
        self.consumer = consumer
        self.governed = governed
        self.block_ms = block_ms
        self._clock = clock
        self._redis_factory = redis_factory
        self._static_cap = cap_fps if cap_fps is not None else default_cap()
        self.cap = RateCap(self._static_cap, clock)
        self.cap_source = "env"
        self.stats: Dict[str, DroneStats] = {}
        self.stream_mode = stream_mode or mission_streams.MODE
        self._streams: Dict[str, str] = {}        # drone → stream being read
        self._cands: Dict[str, List[str]] = {}    # drone → its streams, oldest first
        self._resolved_at: Dict[str, float] = {}
        self._groups_ok: set = set()              # stream keys with this service's group
        self._group_retry_at: Dict[str, float] = {}
        self._last_publish = 0.0
        self._last_cap_read = 0.0
        self._batch_sizes: Deque[int] = deque(maxlen=50)
        self._pel_done: set = set()          # stream keys whose own pending entries were re-read
        self._pel_pos: Dict[str, str] = {}   # stream key → last pending id re-read (restart)
        self._returned: List[Tuple[str, str]] = []   # (stream, id) handed out last round: ack next round
        self._ack_now: Dict[str, List[str]] = {}     # entries finished without analysis (trimmed, dropped)
        self._last_seq: Dict[str, int] = {}  # last analysed frame_seq per drone (gap = lost)
        self._held: Dict[str, List[tuple]] = {}   # drone → [(seq, arrived_mono, fields)] re-order buffer
        self._gaps: Dict[str, set] = {}           # drone → seqs counted as lost (a late arrival refunds)
        self._last_frame_at: Dict[str, float] = {}
        self._last_idle_check = 0.0
        # Called with the drone id when it goes idle or starts a new mission (vessel / anomaly
        # drop their own state); BatchTracker trackers are reset automatically.
        self.on_idle: List[Callable[[str], None]] = []
        self._queued: Dict[str, Optional[int]] = {}
        self._prefetch: Optional[asyncio.Task] = None   # the next round, read + decoded (B2)
        # Set by the service: {"device": "cuda"|"cpu", "cpu_fallback": bool, "cpu_reason": str, ...}
        self.device_info: Optional[dict] = None

    # ── Valkey ────────────────────────────────────────────────────────────────
    def _redis(self):
        if self._redis_factory is not None:
            return self._redis_factory()
        from shared.redis_client import _get_raw_client  # noqa: PLC0415
        return _get_raw_client()

    def _key(self, d: str) -> str:
        return self._streams.get(d) or stream_key(d)

    async def _ensure_groups(self, drones: Iterable[str]) -> None:
        client = self._redis()
        now = self._clock()
        for d in drones:
            key = self._key(d)
            if key in self._groups_ok or self._group_retry_at.get(key, 0.0) > now:
                continue
            try:
                # ordered: from the start of the queue (every frame); newest: from the end.
                # Existing stream only: a purged stream must not be recreated.
                await client.xgroup_create(key, self.group,
                                           id="0" if self.mode == "ordered" else "$", mkstream=False)
                self._groups_ok.add(key)
            except Exception as exc:
                if "BUSYGROUP" in str(exc):
                    self._groups_ok.add(key)
                else:
                    # "no such key": the drone is not streaming yet — retry in a few seconds
                    self._group_retry_at[key] = now + GROUP_RETRY_S

    # ── Which stream (plan Phase 2a) ──────────────────────────────────────────
    async def _resolve(self, client, drones: Iterable[str]) -> None:
        now = self._clock()
        for d in drones:
            if d in self._streams and now - self._resolved_at.get(d, -1e9) < RESOLVE_EVERY_S:
                continue
            self._resolved_at[d] = now
            try:
                key = await self._pick_stream(client, d)
            except Exception as exc:
                log.debug("[%s] stream lookup failed for %s: %s", self.service, d, exc)
                continue
            old = self._streams.get(d)
            if key is None or key == old:
                continue
            self._streams[d] = key
            if old is not None:
                log.info("[%s] %s: %s finished — reading %s with fresh trackers",
                         self.service, d, old, key)
                self._last_seq.pop(d, None)
                self._drop_held(d)
                self._gaps.pop(d, None)
                self._reset_state(d)

    async def _pick_stream(self, client, d: str) -> Optional[str]:
        """The stream to read for a drone: the oldest one this service has not finished.

        A stream is finished when its mission no longer takes frames and this group has
        read every entry. The newest stream is read when all are finished.
        """
        if self.mode == "newest":
            key = await mission_streams.latest_stream(client, d, self.stream_mode)
            self._cands[d] = [key] if key else []
            return key
        cands = await mission_streams.drone_streams(client, d, self.stream_mode)
        self._cands[d] = cands
        if not cands:
            return None
        cur = self._streams.get(d)
        if cur in cands and self._held.get(d):
            return cur                          # frames of it are still in the re-order buffer
        for key in cands[:-1]:
            if await self._unread(client, key) or await mission_streams.is_live(client, key):
                return key
        return cands[-1]

    async def _unread(self, client, key: str) -> int:
        """Entries of a stream this service's group has not read yet (0 when gone)."""
        try:
            groups = await client.xinfo_groups(key)
        except Exception:
            return 0                            # no such stream (purged)
        g = next((x for x in groups if _s(_g(x, "name") or "") == self.group), None)
        if g is None:
            return int(await client.xlen(key))
        lag = _g(g, "lag")
        if lag is None:
            # Valkey cannot count the lag (entries deleted mid-stream): compare ids instead
            info = await client.xinfo_stream(key)
            done = _s(_g(g, "last-delivered-id") or "") == _s(_g(info, "last-generated-id") or "")
            return 0 if done else 1
        return int(lag)

    async def _refresh_cap(self) -> None:
        now = self._clock()
        if not self.governed or now - self._last_cap_read < CAP_REFRESH_S:
            return
        self._last_cap_read = now
        try:
            raw = await self._redis().get(CAP_KEY.format(service=self.service))
            if raw:
                doc = json.loads(_s(raw))
                cap = float(doc["cap"])
                if CAP_MIN <= cap <= CAP_MAX:
                    if abs(cap - self.cap.fps) >= 0.5:
                        log.info("[%s] governor cap %.1f → %.1f fps", self.service, self.cap.fps, cap)
                    self.cap.fps = cap
                    self.cap_source = "governor"
                    return
            self.cap.fps = self._static_cap
            self.cap_source = "env"
        except Exception as exc:
            log.debug("[%s] fps cap read failed: %s", self.service, exc)

    # ── Reading ───────────────────────────────────────────────────────────────
    async def next_round(self, drones: List[str]) -> Dict[str, dict]:
        """Next due frame per drone: {drone_id: fields}. Empty dict when none.

        Ordered mode: the frames handed out by the previous call are acknowledged now (the
        service has finished them). Newest mode: older entries read in the same round are
        XACKed and counted as skipped.
        """
        await self._ack_returned()
        out = await self._round(drones)
        self._remember(out.values())
        return out

    async def done(self) -> None:
        """The service stopped reading: acknowledge the frames it has finished."""
        await self._ack_returned()

    def _remember(self, fields_list) -> None:
        self._returned = [(f["_stream"], f["entry_id"]) for f in fields_list
                          if isinstance(f, dict) and f.get("_stream") and f.get("entry_id")]

    def _finished(self, fields: dict) -> None:
        """A read entry that will not be handed out (dropped, undecodable): ack it."""
        key, eid = fields.get("_stream"), fields.get("entry_id")
        if key and eid:
            self._ack_now.setdefault(key, []).append(eid)

    async def _ack_returned(self) -> None:
        todo, self._returned = self._returned, []
        for key, eid in todo:
            self._ack_now.setdefault(key, []).append(eid)
        await self._flush_acks()

    async def _flush_acks(self) -> None:
        if not self._ack_now:
            return
        todo, self._ack_now = self._ack_now, {}
        client = self._redis()
        for key, ids in todo.items():
            try:
                await client.xack(key, self.group, *ids)
            except Exception as exc:                # stream purged (NOGROUP): nothing to ack
                log.debug("[%s] xack on %s failed: %s", self.service, key, exc)

    async def _round(self, drones: List[str]) -> Dict[str, dict]:
        await self._refresh_cap()
        self._reset_idle()
        if not drones:
            await asyncio.sleep(self.block_ms / 1000)
            return {}
        due = [d for d in drones if self.cap.due(d)]
        if not due:
            await asyncio.sleep(min(self.cap.wait_s(drones), 0.25))
            return {}
        client = self._redis()
        await self._resolve(client, due)
        await self._ensure_groups(due)
        due = [d for d in due if self._key(d) in self._groups_ok]
        if not due:
            await asyncio.sleep(self.block_ms / 1000)
            return {}

        if self.mode == "ordered":
            return await self._next_ordered(client, due)
        by_key = {self._key(d): d for d in due}
        try:
            res = await client.xreadgroup(
                groupname=self.group, consumername=self.consumer,
                streams={k: ">" for k in by_key},
                count=READ_CHUNK, block=self.block_ms,
            )
        except Exception as exc:
            if "NOGROUP" in str(exc):
                self._groups_ok.difference_update(by_key)   # stream purged → recreate next round
                return {}
            raise

        out: Dict[str, dict] = {}
        now = self._clock()
        for stream_name, entries in res or []:
            drone_id = by_key.get(_s(stream_name))
            if drone_id is None or not entries:
                continue
            ids = [_s(e[0]) for e in entries]
            newest = entries[-1]
            st = self.stats.setdefault(drone_id, DroneStats())
            try:
                await client.xack(self._key(drone_id), self.group, *ids)
                if len(entries) == READ_CHUNK:
                    # Backlog (first start on an old group / after a stall): jump the group to
                    # the newest entry instead of transferring every queued frame
                    jumped = await self._jump_to_newest(client, drone_id)
                    if jumped is not None and _s(jumped[0]) != ids[-1]:
                        st.skipped += _seq_gap(newest[1], jumped[1]) + len(ids)
                        newest = jumped
                        ids = ids[-1:]          # count the backlog as skipped, not as input rate
                    else:
                        st.skipped += len(ids) - 1
                else:
                    st.skipped += len(ids) - 1
            except Exception as exc:
                if "NOGROUP" in str(exc):
                    self._groups_ok.discard(self._key(drone_id))
                    continue
                raise
            st.received.extend([now] * len(ids))
            fields = decode_fields(newest[1], drone_id, _s(newest[0]))
            ts_ms = fields.get("ts")
            try:
                st.lag_ms = max(0.0, time.time() * 1000 - float(ts_ms)) if ts_ms else None
            except ValueError:
                st.lag_ms = None
            out[drone_id] = fields
            self.cap.mark(drone_id)
        return out

    async def next_decoded(self, drones: List[str]) -> Dict[str, Tuple[dict, object]]:
        """Next round with every frame decoded: {drone_id: (fields, BGR ndarray)} (plan B2).

        The drones of a round are decoded in parallel worker threads, and the round after
        is read and decoded in the background while the caller runs its models on this
        one. Frames that cannot be decoded are left out. A prefetched round survives a
        stream stop (the sampler lives as long as the process) and is returned first on
        the next start, so no frame is skipped.
        """
        await self._ack_returned()
        task, self._prefetch = self._prefetch, None
        got = await task if task is not None else await self._read_decoded(list(drones))
        self._remember(f for f, _img in got.values())
        self._prefetch = asyncio.create_task(self._read_decoded(list(drones)))
        return got

    async def _read_decoded(self, drones: List[str]) -> Dict[str, Tuple[dict, object]]:
        frames = await self._round(drones)
        if not frames:
            return {}
        imgs = await asyncio.gather(*(asyncio.to_thread(_decode_quietly, f) for f in frames.values()))
        for f, img in zip(frames.values(), imgs):
            if img is None:
                self._finished(f)                # undecodable: never handed out, so ack it
        return {d: (f, img) for (d, f), img in zip(frames.items(), imgs) if img is not None}

    async def _next_ordered(self, client, due: List[str]) -> Dict[str, dict]:
        """Next frame per due drone in frame_seq order (one per drone per round).

        Entries this consumer was given but never acknowledged (crash / restart) are
        re-read first ("0"), then new ones (">"). Entries are XACKed when read and kept in
        a short re-order buffer (REORDER_MS) so out-of-order uploads are analysed in order.
        """
        now = self._clock()
        # Drones whose buffer already holds a frame that may go out now need no read wait
        ready_now = any(self._ready(d, now) is not None for d in due)
        keys = {d: self._key(d) for d in due}
        by_key = {k: d for d, k in keys.items()}
        await self._flush_acks()
        # own pending entries first (after a restart), continuing after the last one re-read
        streams = {k: (self._pel_pos.get(k, "0") if k not in self._pel_done else ">") for k in by_key}
        if any(v != ">" for v in streams.values()) or ready_now:
            block = None
        else:
            wait = self._hold_wait(due, now)
            block = self.block_ms if wait is None else max(1, min(self.block_ms, int(wait * 1000)))
        count = REORDER_READ if REORDER_MS else 1
        try:
            res = await client.xreadgroup(groupname=self.group, consumername=self.consumer,
                                          streams=streams, count=count, block=block)
        except Exception as exc:
            if "NOGROUP" in str(exc):
                self._groups_ok.difference_update(by_key)
                self._pel_done.difference_update(by_key)
                for k in by_key:
                    self._pel_pos.pop(k, None)
                return {}
            raise
        got = {}
        for stream_name, entries in res or []:
            d = by_key.get(_s(stream_name))
            if d is not None:
                got[d] = entries
        now = self._clock()
        for d in due:
            entries = got.get(d) or []
            if streams[keys[d]] != ">":
                if not entries:
                    self._pel_done.add(keys[d])     # own pending list re-read: switch to new entries
                    self._pel_pos.pop(keys[d], None)
                else:
                    self._pel_pos[keys[d]] = _s(entries[-1][0])
            st = self.stats.setdefault(d, DroneStats())
            for eid, raw in entries:
                if raw is None or not raw:      # pending id whose entry was trimmed away
                    self._ack_now.setdefault(keys[d], []).append(_s(eid))
                    continue
                st.received.append(now)
                fields = decode_fields(raw, d, _s(eid))
                fields["_stream"] = keys[d]
                self._held.setdefault(d, []).append((_seq_of(fields), now, fields))

        out: Dict[str, dict] = {}
        for d in due:
            fields = self._release(d, now)
            if fields is None:
                continue
            st = self.stats[d]
            # video frames: from when the server received them (they may wait on disk first)
            ts_ms = fields.get("rx_ts_ms") or fields.get("ts")
            try:
                st.lag_ms = max(0.0, time.time() * 1000 - float(ts_ms)) if ts_ms else None
            except ValueError:
                st.lag_ms = None
            out[d] = fields
            self.cap.mark(d)
            self._last_frame_at[d] = now
        return out

    def _reset_idle(self) -> None:
        now = self._clock()
        if now - self._last_idle_check < 10.0:
            return
        self._last_idle_check = now
        for d, at in list(self._last_frame_at.items()):
            if now - at < IDLE_RESET_S:
                continue
            log.info("[%s] %s idle for %.0fs — resetting its trackers and state", self.service, d, now - at)
            self._last_frame_at.pop(d, None)
            self.forget(d)
            self._reset_state(d)

    def _reset_state(self, d: str) -> None:
        """Fresh trackers and per-drone analyzer state (idle drone, or a new mission)."""
        bt = sys.modules.get("shared.batch_tracker")
        if bt is not None:
            bt.reset_everywhere(d)
        for cb in self.on_idle:
            try:
                cb(d)
            except Exception as exc:
                log.warning("[%s] state reset hook failed for %s: %s", self.service, d, exc)

    # ── Re-order buffer ───────────────────────────────────────────────────────
    def _ready(self, d: str, now: float):
        """Index of the frame that may be released now, or None (hold for re-ordering)."""
        held = self._held.get(d)
        if not held:
            return None
        i = min(range(len(held)), key=lambda k: (held[k][0] is None, held[k][0] or 0, held[k][1]))
        seq, arrived, _ = held[i]
        last = self._last_seq.get(d)
        if (seq is None or last is None or seq <= last + 1 or not REORDER_MS
                or (now - min(h[1] for h in held)) * 1000 >= REORDER_MS
                or len(held) >= REORDER_READ):
            return i
        return None

    def _hold_wait(self, due: List[str], now: float) -> Optional[float]:
        waits = [REORDER_MS / 1000 - (now - min(h[1] for h in self._held[d]))
                 for d in due if self._held.get(d)]
        return max(0.0, min(waits)) if waits else None

    def _release(self, d: str, now: float) -> Optional[dict]:
        while True:
            i = self._ready(d, now)
            if i is None:
                return None
            seq, _, fields = self._held[d].pop(i)
            st = self.stats[d]
            last = self._last_seq.get(d)
            if seq is None:
                return fields
            if last is not None and seq == last:
                self._finished(fields)
                continue                        # duplicate upload (edge retried after success)
            if last is not None and seq < last and last - seq <= SEQ_RESET:
                # Arrived after a newer frame was analysed
                st.late += 1
                gaps = self._gaps.setdefault(d, set())
                if seq in gaps:                 # it had been counted as lost: refund
                    gaps.discard(seq)
                    st.skipped = max(0, st.skipped - 1)
                if self.late_policy == "skip":
                    self._finished(fields)
                    continue
                fields["_late"] = "1"
                return fields
            if last is not None and last + 1 < seq < last + SEQ_RESET:
                missing = range(last + 1, seq)
                st.skipped += len(missing)
                gaps = self._gaps.setdefault(d, set())
                gaps.update(missing)
                if len(gaps) > 4096:            # bound memory: forget the oldest gaps
                    for g in sorted(gaps)[:len(gaps) - 4096]:
                        gaps.discard(g)
            self._last_seq[d] = seq
            return fields

    async def _refresh_queued(self) -> None:
        """Frames still waiting for this service, per drone (for the Models page)."""
        client = self._redis()
        for d in list(self.stats):
            try:
                # Every stream of the drone counts: a mission still being worked off and the next
                self._queued[d] = sum([await self._unread(client, k)
                                       for k in self._cands.get(d) or [self._key(d)]])
            except Exception:
                self._queued[d] = None

    async def _jump_to_newest(self, client, drone_id: str):
        """Move the group's last-delivered id to the newest entry and return that entry."""
        key = self._key(drone_id)
        latest = await client.xrevrange(key, max="+", min="-", count=1)
        if not latest:
            return None
        eid = _s(latest[0][0])
        await client.xgroup_setid(key, self.group, eid)
        return latest[0]

    # ── Stats ─────────────────────────────────────────────────────────────────
    def record(self, drone_id: str, infer_ms: Optional[float] = None,
               total_ms: Optional[float] = None, **stage_ms: Optional[float]) -> None:
        """Call once per analysed frame (after inference / after all handling).

        stage_ms: per-stage wall times in ms (decode, model, track, post, persist, …),
        shown as an EMA split on the Detection Models page (plan A4).
        """
        st = self.stats.setdefault(drone_id, DroneStats())
        st.processed.append(self._clock())
        if infer_ms is not None:
            st.infer_ms = DroneStats._ema(st.infer_ms, float(infer_ms))
        if total_ms is not None:
            st.total_ms = DroneStats._ema(st.total_ms, float(total_ms))
        # Automatic stages: decode (decode_frame) and detect/track (BatchTracker), if used
        auto = {"decode": DECODE_MS.get(drone_id)}
        bt = sys.modules.get("shared.batch_tracker")
        if bt is not None:
            auto.update(bt.stage_ms(drone_id))
        for name, ms in {**auto, **stage_ms}.items():
            if ms is not None:
                st.stages[name] = DroneStats._ema(st.stages.get(name), float(ms))

    def record_batch(self, size: int) -> None:
        self._batch_sizes.append(size)

    def forget(self, drone_id: str) -> None:
        self.stats.pop(drone_id, None)
        key = self._streams.pop(drone_id, None) or stream_key(drone_id)
        self._pel_done.discard(key)
        self._groups_ok.discard(key)
        self._cands.pop(drone_id, None)
        self._resolved_at.pop(drone_id, None)
        self._last_seq.pop(drone_id, None)
        self._drop_held(drone_id)
        self._gaps.pop(drone_id, None)
        self.cap.forget(drone_id)

    def _drop_held(self, drone_id: str) -> None:
        for _seq, _at, fields in self._held.pop(drone_id, None) or []:
            self._finished(fields)

    def snapshot(self) -> dict:
        from shared import gpu_profile  # noqa: PLC0415
        now = self._clock()
        drones = {}
        for d, st in list(self.stats.items()):
            fps = DroneStats._rate(st.processed, now)
            inp = DroneStats._rate(st.received, now)
            if not st.processed and not st.received:
                continue
            drones[d] = {
                "fps": round(fps, 1),
                "input_fps": round(inp, 1),
                "skipped": st.skipped,
                "infer_ms": None if st.infer_ms is None else round(st.infer_ms, 1),
                "total_ms": None if st.total_ms is None else round(st.total_ms, 1),
                "lag_ms": None if st.lag_ms is None else round(st.lag_ms),
                "queued": self._queued.get(d),
                "late": st.late,
                "stages": {**{k: round(v, 1) for k, v in st.stages.items()},
                           **({"model": round(st.infer_ms, 1)} if st.infer_ms is not None else {}),
                           **({"total": round(st.total_ms, 1)} if st.total_ms is not None else {})},
            }
        batches = list(self._batch_sizes)
        return {
            "service": self.service,
            "pid": os.getpid(),
            "cap_fps": round(self.cap.fps, 1),
            "cap_source": self.cap_source,
            "governed": self.governed,
            "mode": self.mode,
            "stream_mode": self.stream_mode,
            "device_info": self.device_info,
            "gpu": gpu_profile.detect().as_dict(),
            "avg_batch": round(sum(batches) / len(batches), 2) if batches else None,
            "drones": drones,
            "updated_at": time.time(),
        }

    def _warn_slow(self, now: float) -> None:
        """Log (≤ once a minute per drone) when frames take more than twice the time the
        cap allows, with the stage split, so a slowdown shows up in the logs (plan A4)."""
        budget_ms = 1000.0 / max(self.cap.fps, 0.5)
        warned = getattr(self, "_warned_at", None)
        if warned is None:
            warned = self._warned_at = {}
        for d, st in self.stats.items():
            if st.total_ms is None or st.total_ms < 2 * budget_ms or now - warned.get(d, -1e9) < 60:
                continue
            warned[d] = now
            split = ", ".join(f"{k} {v:.0f} ms" for k, v in st.stages.items())
            log.warning("[%s] %s: %.0f ms per frame (budget %.0f ms at %.0f fps) — %s",
                        self.service, d, st.total_ms, budget_ms, self.cap.fps, split or "no stage data")

    async def maybe_publish(self) -> None:
        now = self._clock()
        if now - self._last_publish < STATS_EVERY_S:
            return
        self._last_publish = now
        self._warn_slow(now)
        if self.mode == "ordered":
            await self._refresh_queued()
        try:
            await self._redis().set(STATS_KEY.format(service=self.service),
                                    json.dumps(self.snapshot()), ex=STATS_TTL_S)
        except Exception as exc:
            log.debug("[%s] stats publish failed: %s", self.service, exc)


class Timer:
    """`with Timer() as t: ...; t.ms` — wall time of a block in ms."""

    def __enter__(self):
        self._t0 = time.perf_counter()
        self.ms = 0.0
        return self

    def __exit__(self, *exc):
        self.ms = (time.perf_counter() - self._t0) * 1000
        return False


def chunks(items: List, size: int) -> List[List]:
    size = max(1, size)
    return [items[i:i + size] for i in range(0, len(items), size)]


def ordered(frames: Dict[str, dict]) -> List[Tuple[str, dict]]:
    """Stable drone order so batch slots don't shuffle between rounds."""
    return sorted(frames.items())
