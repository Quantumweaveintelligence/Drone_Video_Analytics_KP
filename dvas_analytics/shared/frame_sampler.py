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
    from gaps in `frame_seq`.
  * mode="newest" (crowd): only the newest entry per drone is analysed; older ones
    are XACKed unread and counted as `skipped`.
  * A per-drone rate cap (the governor's `dvas:fps_cap:<service>`, else
    DVAS_FPS_MAX) keeps one service from taking the whole GPU.
  * Live stats (input fps, analysed fps, skipped, inference ms, lag) are
    published every 2 s to `dvas:svc_stats:<service>` for the governor and the
    Detection Models page.

Frame bytes come from the raw `frame` field; `jpeg_b64` is only a fallback
for entries written by an older ingest.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger(__name__)

STATS_KEY = "dvas:svc_stats:{service}"
CAP_KEY = "dvas:fps_cap:{service}"
STATS_TTL_S = 10
STATS_EVERY_S = 2.0
CAP_REFRESH_S = 2.0
READ_CHUNK = 32
GROUP_RETRY_S = 5.0
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
    return f"drone:frames:{drone_id}"


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


def decode_frame(fields: dict):
    """BGR ndarray of an entry, or None when missing / undecodable."""
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    data = frame_bytes(fields)
    if not data:
        return None
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


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
    skipped: int = 0
    infer_ms: Optional[float] = None    # EMA of model time per frame
    total_ms: Optional[float] = None    # EMA of whole per-frame handling
    lag_ms: Optional[float] = None      # ingest → analysis start of the last frame

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
                 mode: str = "ordered",
                 governed: bool = True, cap_fps: Optional[float] = None,
                 block_ms: int = 50, redis_factory: Optional[Callable] = None,
                 clock: Callable[[], float] = time.monotonic):
        if mode not in ("ordered", "newest"):
            raise ValueError(f"mode must be 'ordered' or 'newest', not {mode!r}")
        self.service = service
        self.mode = mode
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
        self._groups_ok: set = set()
        self._group_retry_at: Dict[str, float] = {}
        self._last_publish = 0.0
        self._last_cap_read = 0.0
        self._batch_sizes: Deque[int] = deque(maxlen=50)
        self._pel_done: set = set()          # drones whose own pending entries were re-read
        self._last_seq: Dict[str, int] = {}  # last analysed frame_seq per drone (gap = lost)
        self._queued: Dict[str, Optional[int]] = {}
        # Set by the service: {"device": "cuda"|"cpu", "cpu_fallback": bool, "cpu_reason": str, ...}
        self.device_info: Optional[dict] = None

    # ── Valkey ────────────────────────────────────────────────────────────────
    def _redis(self):
        if self._redis_factory is not None:
            return self._redis_factory()
        from shared.redis_client import _get_raw_client  # noqa: PLC0415
        return _get_raw_client()

    async def _ensure_groups(self, drones: Iterable[str]) -> None:
        client = self._redis()
        now = self._clock()
        for d in drones:
            if d in self._groups_ok or self._group_retry_at.get(d, 0.0) > now:
                continue
            try:
                # ordered: from the start of the queue (every frame); newest: from the end.
                # Existing stream only: a purged stream must not be recreated.
                await client.xgroup_create(stream_key(d), self.group,
                                           id="0" if self.mode == "ordered" else "$", mkstream=False)
                self._groups_ok.add(d)
            except Exception as exc:
                if "BUSYGROUP" in str(exc):
                    self._groups_ok.add(d)
                else:
                    # "no such key": the drone is not streaming yet — retry in a few seconds
                    self._group_retry_at[d] = now + GROUP_RETRY_S

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
        """Newest due frame per drone: {drone_id: fields}. Empty dict when none.

        Older entries read in the same round are XACKed and counted as skipped.
        """
        await self._refresh_cap()
        if not drones:
            await asyncio.sleep(self.block_ms / 1000)
            return {}
        due = [d for d in drones if self.cap.due(d)]
        if not due:
            await asyncio.sleep(min(self.cap.wait_s(drones), 0.25))
            return {}
        await self._ensure_groups(due)
        due = [d for d in due if d in self._groups_ok]
        if not due:
            await asyncio.sleep(self.block_ms / 1000)
            return {}

        client = self._redis()
        if self.mode == "ordered":
            return await self._next_ordered(client, due)
        try:
            res = await client.xreadgroup(
                groupname=self.group, consumername=self.consumer,
                streams={stream_key(d): ">" for d in due},
                count=READ_CHUNK, block=self.block_ms,
            )
        except Exception as exc:
            if "NOGROUP" in str(exc):
                self._groups_ok.difference_update(due)   # stream purged → recreate next round
                return {}
            raise

        out: Dict[str, dict] = {}
        now = self._clock()
        for stream_name, entries in res or []:
            drone_id = _s(stream_name).split("drone:frames:", 1)[-1]
            if not entries:
                continue
            ids = [_s(e[0]) for e in entries]
            newest = entries[-1]
            st = self.stats.setdefault(drone_id, DroneStats())
            try:
                await client.xack(stream_key(drone_id), self.group, *ids)
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
                    self._groups_ok.discard(drone_id)
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

    async def _next_ordered(self, client, due: List[str]) -> Dict[str, dict]:
        """Next unread frame per due drone, in stream order (one per drone per round).

        Entries this consumer was given but never acknowledged (crash / restart) are
        re-read first ("0"), then new ones (">"). Each entry is XACKed when handed
        to the analyzer.
        """
        streams = {stream_key(d): ("0" if d not in self._pel_done else ">") for d in due}
        block = None if any(v == "0" for v in streams.values()) else self.block_ms
        try:
            res = await client.xreadgroup(groupname=self.group, consumername=self.consumer,
                                          streams=streams, count=1, block=block)
        except Exception as exc:
            if "NOGROUP" in str(exc):
                self._groups_ok.difference_update(due)
                self._pel_done.difference_update(due)
                return {}
            raise
        got = {}
        for stream_name, entries in res or []:
            got[_s(stream_name).split("drone:frames:", 1)[-1]] = entries
        out: Dict[str, dict] = {}
        now = self._clock()
        for d in due:
            entries = got.get(d) or []
            if streams[stream_key(d)] == "0" and not entries:
                self._pel_done.add(d)           # own pending list is empty: switch to new entries
                continue
            if not entries:
                continue
            eid, raw = entries[0]
            if raw is None or not raw:          # pending id whose entry was trimmed away
                await client.xack(stream_key(d), self.group, _s(eid))
                continue
            await client.xack(stream_key(d), self.group, _s(eid))
            st = self.stats.setdefault(d, DroneStats())
            st.received.append(now)
            fields = decode_fields(raw, d, _s(eid))
            try:
                seq = int(fields.get("frame_seq") or fields.get("frame_sequence") or "")
                last = self._last_seq.get(d)
                if last is not None and seq > last + 1 and seq - last < 1_000_000:
                    st.skipped += seq - last - 1    # overwritten by MAXLEN before we got to them
                self._last_seq[d] = seq
            except ValueError:
                pass
            ts_ms = fields.get("ts")
            try:
                st.lag_ms = max(0.0, time.time() * 1000 - float(ts_ms)) if ts_ms else None
            except ValueError:
                st.lag_ms = None
            out[d] = fields
            self.cap.mark(d)
        return out

    async def _refresh_queued(self) -> None:
        """Frames still waiting for this service, per drone (for the Models page)."""
        client = self._redis()
        for d in list(self.stats):
            try:
                groups = await client.xinfo_groups(stream_key(d))
                g = next((x for x in groups if _s(x.get("name") or x.get(b"name") or "") == self.group), None)
                lag = None if g is None else g.get("lag")
                self._queued[d] = None if lag is None else int(lag)
            except Exception:
                self._queued[d] = None

    async def _jump_to_newest(self, client, drone_id: str):
        """Move the group's last-delivered id to the newest entry and return that entry."""
        latest = await client.xrevrange(stream_key(drone_id), max="+", min="-", count=1)
        if not latest:
            return None
        eid = _s(latest[0][0])
        await client.xgroup_setid(stream_key(drone_id), self.group, eid)
        return latest[0]

    # ── Stats ─────────────────────────────────────────────────────────────────
    def record(self, drone_id: str, infer_ms: Optional[float] = None,
               total_ms: Optional[float] = None) -> None:
        """Call once per analysed frame (after inference / after all handling)."""
        st = self.stats.setdefault(drone_id, DroneStats())
        st.processed.append(self._clock())
        if infer_ms is not None:
            st.infer_ms = DroneStats._ema(st.infer_ms, float(infer_ms))
        if total_ms is not None:
            st.total_ms = DroneStats._ema(st.total_ms, float(total_ms))

    def record_batch(self, size: int) -> None:
        self._batch_sizes.append(size)

    def forget(self, drone_id: str) -> None:
        self.stats.pop(drone_id, None)
        self._pel_done.discard(drone_id)
        self._last_seq.pop(drone_id, None)
        self.cap.forget(drone_id)
        self._groups_ok.discard(drone_id)

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
            }
        batches = list(self._batch_sizes)
        return {
            "service": self.service,
            "pid": os.getpid(),
            "cap_fps": round(self.cap.fps, 1),
            "cap_source": self.cap_source,
            "governed": self.governed,
            "mode": self.mode,
            "device_info": self.device_info,
            "gpu": gpu_profile.detect().as_dict(),
            "avg_batch": round(sum(batches) / len(batches), 2) if batches else None,
            "drones": drones,
            "updated_at": time.time(),
        }

    async def maybe_publish(self) -> None:
        now = self._clock()
        if now - self._last_publish < STATS_EVERY_S:
            return
        self._last_publish = now
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
