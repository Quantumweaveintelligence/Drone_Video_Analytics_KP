# -*- coding: utf-8 -*-
"""
Video recording of a mission (RunPod v2 plan R1, items 3–4).

The edge streams H.264 in MPEG-TS over /ingest/video/ws. Every byte is written here, as
received, into segments of about SEGMENT_S seconds that start at a keyframe:

  {RECORD_ROOT}/{sid}/seg-000001.ts           the TS bytes
  {RECORD_ROOT}/{sid}/seg-000001.meta.jsonl   one line per frame meta (+ rx_ts), first line
                                              {"t":"seg","seg":1,"conn":N} names the connection
  {RECORD_ROOT}/{sid}/manifest.jsonl          one line per closed segment: SHA-256 of both
                                              files and a chain hash over the previous line

The recording is the evidence copy of the stream and also the analytics backlog: the
decoder (video_decoder.py) reads the segments at the pace the analytics consume, so a
service that falls behind delays its results but never loses a frame.

Shared state of a mission's video, Valkey HASH mission:video:{sid}:
  status         receiving (a connection is open) | paused (none) | ended (no more data)
  conn           number of the last connection;  cur_seg  segment being written
  last_seq_rx    newest frame seq received;       rx_ts_last  when (server clock)
  width height fps codec started_at ended_at bytes
  decoder_seg decoder_seq decoder_done last_entry_id pub_fps behind_live_s  (written by the decoder)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def _float_env(name: str, default: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(os.getenv(name, str(default)))))
    except ValueError:
        return default


RECORD_ROOT = Path(os.getenv("DVAS_RECORD_DIR", "/workspace/dvas/recordings"))
SEGMENT_S = _float_env("DVAS_RECORD_SEGMENT_S", 10.0, 2.0, 60.0)
# Below this much free space on the recording disk new video connections are refused
MIN_FREE_PCT = _float_env("DVAS_RECORD_MIN_FREE_PCT", 10.0, 1.0, 50.0)
MIN_FREE_GB = _float_env("DVAS_RECORD_MIN_FREE_GB", 5.0, 0.5, 1000.0)
# RunPod: `df /workspace` shows the whole storage cluster, so the volume's own usage comes
# from the backup loop's disk guard (du against DVAS_VOLUME_GB, every 10 min).
DISK_GUARD_FILE = Path(os.getenv("DVAS_DISK_GUARD_FILE", "/run/dvas/disk_guard.json"))
MAX_USED_PCT = _float_env("DVAS_RECORD_MAX_USED_PCT", 90.0, 50.0, 99.0)

_SEG_RE = re.compile(r"^seg-(\d{6})\.ts$")
_GENESIS = "0" * 64


def video_key(sid: str) -> str:
    return f"mission:video:{sid}"


def mission_dir(sid: str) -> Path:
    return RECORD_ROOT / sid


def seg_path(sid: str, idx: int) -> Path:
    return mission_dir(sid) / f"seg-{idx:06d}.ts"


def meta_path(sid: str, idx: int) -> Path:
    return mission_dir(sid) / f"seg-{idx:06d}.meta.jsonl"


def segments(sid: str) -> list:
    """Indexes of the mission's segment files, ascending."""
    d = mission_dir(sid)
    try:
        names = os.listdir(d)
    except FileNotFoundError:
        return []
    return sorted(int(m.group(1)) for m in (_SEG_RE.match(n) for n in names) if m)


def disk_ok(path: Optional[Path] = None) -> tuple:
    """(ok, free_gb, free_pct) for the recording disk."""
    path = path or RECORD_ROOT
    try:
        guard = json.loads(DISK_GUARD_FILE.read_text())
        if time.time() - float(guard.get("at", 0)) < 1800 and guard.get("volume_gb"):
            pct = float(guard["pct"])
            free_gb = max(0.0, float(guard["volume_gb"]) * (100 - pct) / 100)
            return pct < MAX_USED_PCT, round(free_gb, 1), round(100 - pct, 1)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        path.mkdir(parents=True, exist_ok=True)
        u = shutil.disk_usage(path)
    except OSError as exc:
        log.warning("recording disk check failed: %s", exc)
        return False, 0.0, 0.0
    free_gb = u.free / 1e9
    free_pct = 100.0 * u.free / u.total if u.total else 0.0
    return (free_pct >= MIN_FREE_PCT and free_gb >= MIN_FREE_GB), round(free_gb, 1), round(free_pct, 1)


def _last_manifest_hash(sid: str) -> str:
    p = mission_dir(sid) / "manifest.jsonl"
    try:
        with open(p, "rb") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return _GENESIS
    for raw in reversed(lines):
        try:
            return json.loads(raw)["chain"]
        except (ValueError, KeyError):
            continue
    return _GENESIS


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify_manifest(sid: str) -> tuple:
    """(ok, segments checked, first problem or "") — recompute every hash and the chain."""
    prev = _GENESIS
    n = 0
    try:
        with open(mission_dir(sid) / "manifest.jsonl", "rb") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return False, 0, "no manifest"
    for raw in lines:
        e = json.loads(raw)
        if e["prev"] != prev:
            return False, n, f"seg {e['seg']}: chain broken"
        if _sha256_file(seg_path(sid, e["seg"])) != e["sha256_ts"]:
            return False, n, f"seg {e['seg']}: TS hash differs"
        if _sha256_file(meta_path(sid, e["seg"])) != e["sha256_meta"]:
            return False, n, f"seg {e['seg']}: meta hash differs"
        chain = hashlib.sha256((prev + e["sha256_ts"] + e["sha256_meta"]).encode()).hexdigest()
        if chain != e["chain"]:
            return False, n, f"seg {e['seg']}: chain hash differs"
        prev = chain
        n += 1
    return True, n, ""


class SegmentRecorder:
    """Writes one connection's stream into the mission's segments (synchronous, small writes).

    A new segment starts at the first keyframe after SEGMENT_S seconds of stream time and
    at every new connection, so each segment begins with a keyframe and belongs to one
    connection (PTS restart between connections).
    """

    def __init__(self, sid: str, conn: int):
        self.sid = sid
        self.conn = conn
        mission_dir(sid).mkdir(parents=True, exist_ok=True)
        existing = segments(sid)
        self.next_idx = (existing[-1] + 1) if existing else 1
        self.prev_hash = _last_manifest_hash(sid)
        self.idx: Optional[int] = None
        self._ts = None
        self._meta = None
        self._h_ts = None
        self._h_meta = None
        self._first_pts: Optional[float] = None
        self._seq_range = [None, None]
        self._cap_range = [None, None]
        self._bytes = 0
        self.total_bytes = 0

    # ── writing ───────────────────────────────────────────────────────────────
    def meta(self, item: dict, rx_ts: float) -> bool:
        """Record one frame's meta. Returns True when a new segment was opened."""
        rotated = False
        pts = item.get("pts_ms")
        if self.idx is None or (item.get("key") and self._first_pts is not None
                                and pts is not None and pts - self._first_pts >= SEGMENT_S * 1000):
            self._rotate()
            rotated = True
        if self._first_pts is None and pts is not None:
            self._first_pts = pts
        line = dict(item)
        line["rx_ts"] = round(rx_ts, 3)
        self._write_meta(line)
        seq, cap = item.get("seq"), item.get("capture_ts")
        if seq is not None:
            self._seq_range[0] = seq if self._seq_range[0] is None else self._seq_range[0]
            self._seq_range[1] = seq
        if cap is not None:
            self._cap_range[0] = cap if self._cap_range[0] is None else self._cap_range[0]
            self._cap_range[1] = cap
        return rotated

    def data(self, chunk: bytes) -> bool:
        """Append TS bytes. Returns True when a segment had to be opened for them."""
        opened = False
        if self.idx is None:
            self._rotate()
            opened = True
        self._ts.write(chunk)
        self._ts.flush()                  # visible to the decoder at once (no fsync per chunk)
        self._h_ts.update(chunk)
        self._bytes += len(chunk)
        self.total_bytes += len(chunk)
        return opened

    def close(self) -> None:
        self._close_segment()

    # ── segments ──────────────────────────────────────────────────────────────
    def _write_meta(self, line: dict) -> None:
        raw = (json.dumps(line, separators=(",", ":")) + "\n").encode()
        self._meta.write(raw)
        self._meta.flush()
        self._h_meta.update(raw)

    def _rotate(self) -> None:
        self._close_segment()
        self.idx = self.next_idx
        self.next_idx += 1
        self._ts = open(seg_path(self.sid, self.idx), "ab")
        self._meta = open(meta_path(self.sid, self.idx), "ab")
        self._h_ts = hashlib.sha256()
        self._h_meta = hashlib.sha256()
        self._first_pts = None
        self._seq_range = [None, None]
        self._cap_range = [None, None]
        self._bytes = 0
        self._write_meta({"t": "seg", "seg": self.idx, "conn": self.conn})

    def _close_segment(self) -> None:
        if self.idx is None:
            return
        for f in (self._ts, self._meta):
            try:
                f.flush()
                os.fsync(f.fileno())
            except OSError as exc:
                log.warning("recording fsync failed (%s seg %d): %s", self.sid, self.idx, exc)
            f.close()
        sha_ts, sha_meta = self._h_ts.hexdigest(), self._h_meta.hexdigest()
        chain = hashlib.sha256((self.prev_hash + sha_ts + sha_meta).encode()).hexdigest()
        entry = {"seg": self.idx, "conn": self.conn, "bytes": self._bytes,
                 "seq_first": self._seq_range[0], "seq_last": self._seq_range[1],
                 "capture_first": self._cap_range[0], "capture_last": self._cap_range[1],
                 "sha256_ts": sha_ts, "sha256_meta": sha_meta, "prev": self.prev_hash,
                 "chain": chain, "closed_at": round(time.time(), 3)}
        with open(mission_dir(self.sid) / "manifest.jsonl", "ab") as f:
            f.write((json.dumps(entry, separators=(",", ":")) + "\n").encode())
            f.flush()
            os.fsync(f.fileno())
        self.prev_hash = chain
        self.idx = None
        self._ts = self._meta = None


class RecorderThread:
    """A SegmentRecorder whose writes run in one worker thread, in order, off the event loop.

    v2 R9: ingest wrote every TS chunk (write + flush) on its event loop, and recordings live on
    the pod's network volume. When a write there blocked, ingest's one event loop stopped and
    every feed stalled together — receive, the live relay, the dashboard tiles (pod runs 13 Nov:
    all drones' received fps dropped to 10–12 then burst to 46–50, about once a minute).
    Now a slow disk only delays this connection, and only once more than MAX_PENDING bytes
    wait for the disk (backpressure on this sender; the recording stays complete).
    A write error is raised at the next call, as the synchronous writes did.
    """

    MAX_PENDING = 32 * 1024 * 1024

    def __init__(self, rec: SegmentRecorder):
        self.rec = rec
        self.sid = rec.sid
        self._pool = ThreadPoolExecutor(1, thread_name_prefix="recorder")
        self._lock = threading.Lock()
        self._pending = 0
        self._last: Optional[asyncio.Future] = None
        self._error: Optional[BaseException] = None
        self.total_bytes = rec.total_bytes        # bytes handed over (written or about to be)

    @property
    def idx(self) -> Optional[int]:
        return self.rec.idx

    def _run(self, fn, n: int, *args) -> None:
        try:
            if self._error is None:
                fn(*args)
        except BaseException as exc:              # kept for the next call on the event loop
            self._error = exc
        finally:
            with self._lock:
                self._pending -= n

    async def _submit(self, fn, n: int, *args) -> None:
        if self._error is not None:
            raise self._error
        with self._lock:
            self._pending += n
            pending = self._pending
        self._last = asyncio.get_running_loop().run_in_executor(self._pool, self._run, fn, n, *args)
        if pending > self.MAX_PENDING:
            await self._last                        # the disk is behind: wait for it (this sender only)

    async def data(self, chunk: bytes) -> None:
        self.total_bytes += len(chunk)
        await self._submit(self.rec.data, len(chunk), chunk)

    async def meta(self, item: dict, rx_ts: float) -> None:
        await self._submit(self.rec.meta, 0, item, rx_ts)

    async def close(self) -> None:
        """Every queued write, then the segment close (fsync, manifest)."""
        try:
            await asyncio.get_running_loop().run_in_executor(self._pool, self.rec.close)
        finally:
            self._pool.shutdown(wait=False)
        if self._error is not None:
            raise self._error
