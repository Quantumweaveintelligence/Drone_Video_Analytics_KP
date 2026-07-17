"""
src/buffer/rolling_frame_buffer.py
====================================
Task 1.2 — Rolling Frame Buffer Implementation
------------------------------------------------
RollingFrameBuffer stores a fixed-duration window of compressed frames
on a local SSD using memory-mapped files, avoiding RAM exhaustion during
long operations.

Architecture
~~~~~~~~~~~~
┌─────────────────────────────────────────────────────────┐
│  Producer thread  (DroneVideoSource.on_frame callback)  │
│      ↓  write_frame(VideoFrame)                         │
│  ┌───────────────────────────────────────────────────┐  │
│  │  RollingFrameBuffer                               │  │
│  │                                                   │  │
│  │  _index: deque[FrameEntry]  ← circular index      │  │
│  │  _slab:  mmap               ← memory-mapped file  │  │
│  │                             on SSD                │  │
│  │  _write_ptr: int            ← rolling write offset│  │
│  └───────────────────────────────────────────────────┘  │
│      ↓  extract_segment(start_ts, end_ts)               │
│  Consumer thread  (Change detection / Evidence gen.)    │
└─────────────────────────────────────────────────────────┘

Storage layout (single slab file)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The slab is a fixed-size memory-mapped file.  Each frame is stored
consecutively.  When the write pointer would exceed the slab end,
it wraps back to offset 0 (ring buffer semantics).  The index
deque records (frame_index, utc_timestamp_ms, slab_offset, data_len).
Old index entries are popped from the left when:
  • they fall outside the time window, OR
  • the index deque is full (max_frames exceeded).

Thread safety
~~~~~~~~~~~~~
A single threading.Lock protects the index deque and write pointer.
The mmap itself is written only by the producer thread and read only
after acquiring the lock to snapshot the relevant offsets.

Compression
~~~~~~~~~~~
JPEG at configurable quality (default 85 %).
  • 1080p frame at 85 % ≈ 20–40 KB
  • 60 s × 30 fps = 1800 frames × 30 KB = ~54 MB — fits easily in 128 MB slab.
"""

from __future__ import annotations

import io
import mmap
import os
import struct
import tempfile
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image

from src.metrics.prometheus_metrics import (
    BUFFER_CAPACITY_FRAMES,
    BUFFER_DROPS_TOTAL,
    BUFFER_USED_FRAMES,
    BUFFER_WRITE_LATENCY,
)
from src.utils.logger import get_logger

log = get_logger(__name__)

# ── On-disk header per frame (packed struct, little-endian) ────────────────
# Format: < q Q I I  →  8 + 8 + 4 + 4 = 24 bytes
#   q  frame_index       (int64)
#   Q  utc_timestamp_ms  (uint64)
#   I  data_len          (uint32)  JPEG bytes
#   I  checksum          (uint32)  simple XOR of data bytes (tamper indicator)
_HEADER_FMT = "<qQII"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)   # 24 bytes


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------

@dataclass
class BufferFrame:
    """A frame retrieved from the rolling buffer."""
    frame_index: int
    utc_timestamp_ms: int
    jpeg_bytes: bytes           # raw JPEG — decode on demand
    width: int = 0
    height: int = 0

    def decode(self) -> np.ndarray:
        """Decompress JPEG → BGR numpy array (compatible with OpenCV)."""
        pil_img = Image.open(io.BytesIO(self.jpeg_bytes)).convert("RGB")
        bgr = np.array(pil_img)[..., ::-1]   # RGB → BGR
        return bgr


@dataclass
class _IndexEntry:
    """In-memory index record for one stored frame."""
    frame_index: int
    utc_timestamp_ms: int
    slab_offset: int            # byte offset of frame header in the slab
    total_bytes: int            # header + JPEG bytes


# ---------------------------------------------------------------------------
# RollingFrameBuffer
# ---------------------------------------------------------------------------

class RollingFrameBuffer:
    """
    A thread-safe, fixed-duration circular video buffer backed by a
    memory-mapped file on local SSD.

    Parameters
    ----------
    drone_id : str
        Used for Prometheus label and log context.
    duration_s : int
        How many seconds of video to retain (default 60).
    storage_dir : str | Path
        Directory for the mmap slab file.  Must be on a fast local disk.
    jpeg_quality : int
        JPEG compression quality 1–100 (default 85).
    max_frames : int
        Safety ceiling on index size.  At 30 fps × 60 s = 1800 frames;
        default 2200 gives ~20 % headroom.
    mmap_slab_size_bytes : int
        Size of the backing mmap file in bytes (default 128 MiB).
    """

    def __init__(
        self,
        drone_id: str = "drone_01",
        duration_s: int = 60,
        storage_dir: str = "./buffer_storage",
        jpeg_quality: int = 85,
        max_frames: int = 2200,
        mmap_slab_size_bytes: int = 128 * 1024 * 1024,
    ) -> None:
        self.drone_id = drone_id
        self.duration_s = duration_s
        self.jpeg_quality = jpeg_quality
        self.max_frames = max_frames
        self.mmap_slab_size_bytes = mmap_slab_size_bytes

        # Ensure storage directory exists
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)

        # ── mmap slab ────────────────────────────────────────────────
        self._slab_path = self._storage_dir / f"buffer_{drone_id}.mmap"
        self._slab_file, self._slab = self._open_slab()

        # ── State protected by lock ───────────────────────────────────
        self._lock = threading.Lock()
        self._index: deque[_IndexEntry] = deque()
        self._write_ptr: int = 0          # byte offset for next write
        self._frame_count: int = 0        # total frames ever written

        # ── Prometheus initialisation ─────────────────────────────────
        BUFFER_CAPACITY_FRAMES.labels(drone_id=self.drone_id).set(self.max_frames)
        BUFFER_USED_FRAMES.labels(drone_id=self.drone_id).set(0)

        log.info(
            "buffer_initialised",
            drone_id=self.drone_id,
            duration_s=self.duration_s,
            slab_path=str(self._slab_path),
            slab_size_mb=round(mmap_slab_size_bytes / 1024 / 1024, 1),
            max_frames=self.max_frames,
        )

    # ------------------------------------------------------------------
    # Slab management
    # ------------------------------------------------------------------

    def _open_slab(self) -> Tuple[object, mmap.mmap]:
        """
        Create (or reuse) the slab file and return (file_handle, mmap).
        The slab is pre-allocated to mmap_slab_size_bytes so the kernel
        won't need to extend the file during operation.
        """
        mode = "r+b" if self._slab_path.exists() else "w+b"
        f = open(self._slab_path, mode)
        if mode == "w+b":
            # Pre-allocate by seeking to the end and writing a zero byte
            f.seek(self.mmap_slab_size_bytes - 1)
            f.write(b"\x00")
            f.flush()
            f.seek(0)
        mm = mmap.mmap(f.fileno(), 0)   # 0 = map entire file
        return f, mm

    def _close_slab(self) -> None:
        try:
            self._slab.close()
        except Exception:
            pass
        try:
            self._slab_file.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Write path (producer — called from DroneVideoSource.on_frame)
    # ------------------------------------------------------------------

    def write_frame(self, frame_index: int, utc_timestamp_ms: int, frame: np.ndarray) -> None:
        """
        Compress *frame* to JPEG and write it into the mmap slab.

        This is the hot path.  It must complete quickly so the producer
        thread is not delayed.

        Parameters
        ----------
        frame_index : int
            Monotonically increasing frame counter from the video source.
        utc_timestamp_ms : int
            Wall-clock UTC ms at the time the frame was decoded.
        frame : np.ndarray
            BGR image array (H × W × 3, uint8) as delivered by OpenCV.
        """
        import time
        t0 = time.monotonic()

        # ── 1. JPEG compress ─────────────────────────────────────────
        jpeg_bytes = self._compress_jpeg(frame)
        data_len = len(jpeg_bytes)
        total_bytes = _HEADER_SIZE + data_len

        if total_bytes > self.mmap_slab_size_bytes:
            log.error(
                "frame_too_large_for_slab",
                drone_id=self.drone_id,
                frame_index=frame_index,
                data_len=data_len,
            )
            return

        # ── 2. Compute simple checksum ───────────────────────────────
        checksum = _xor_checksum(jpeg_bytes)

        # ── 3. Acquire lock and write to slab ────────────────────────
        with self._lock:
            # Evict old entries before deciding on write position
            self._evict_expired(utc_timestamp_ms)

            # If full on index capacity, force-evict the oldest entry
            while len(self._index) >= self.max_frames:
                self._index.popleft()
                BUFFER_DROPS_TOTAL.labels(drone_id=self.drone_id).inc()
                log.warning("buffer_index_full_drop", drone_id=self.drone_id)

            # Wrap write pointer if the current frame doesn't fit at the end
            if self._write_ptr + total_bytes > self.mmap_slab_size_bytes:
                self._write_ptr = 0

            offset = self._write_ptr

            # Write header
            header = struct.pack(
                _HEADER_FMT,
                frame_index,
                utc_timestamp_ms,
                data_len,
                checksum,
            )
            self._slab[offset: offset + _HEADER_SIZE] = header

            # Write JPEG payload
            payload_start = offset + _HEADER_SIZE
            self._slab[payload_start: payload_start + data_len] = jpeg_bytes

            # Advance write pointer
            self._write_ptr = offset + total_bytes

            # Update index
            entry = _IndexEntry(
                frame_index=frame_index,
                utc_timestamp_ms=utc_timestamp_ms,
                slab_offset=offset,
                total_bytes=total_bytes,
            )
            self._index.append(entry)
            self._frame_count += 1

            # ── Metrics ──────────────────────────────────────────────
            used = len(self._index)
            BUFFER_USED_FRAMES.labels(drone_id=self.drone_id).set(used)

        write_latency_ms = (time.monotonic() - t0) * 1_000.0
        BUFFER_WRITE_LATENCY.labels(drone_id=self.drone_id).observe(write_latency_ms)

    def write_video_frame(self, video_frame) -> None:
        """
        Convenience wrapper accepting a VideoFrame object
        (from src.video.drone_video_source).
        """
        self.write_frame(
            frame_index=video_frame.frame_index,
            utc_timestamp_ms=video_frame.utc_timestamp_ms,
            frame=video_frame.frame,
        )

    # ------------------------------------------------------------------
    # Read path (consumer — change detection / evidence extraction)
    # ------------------------------------------------------------------

    def extract_segment(
        self,
        start_ts_ms: int,
        end_ts_ms: int,
    ) -> List[BufferFrame]:
        """
        Return all frames whose UTC timestamp falls within
        [start_ts_ms, end_ts_ms] (inclusive).

        This is the evidence clip extraction path.  It copies data out of
        the mmap into Python bytes objects so the caller owns the data
        independently of future buffer writes.

        Parameters
        ----------
        start_ts_ms : int
            Clip start in UTC milliseconds.
        end_ts_ms : int
            Clip end in UTC milliseconds.

        Returns
        -------
        List[BufferFrame]
            Ordered (oldest → newest) list of frames in the time window.
            Empty list if no matching frames exist.
        """
        frames: List[BufferFrame] = []

        with self._lock:
            # Snapshot entries that fall in the requested window.
            # deque iteration is safe under lock.
            candidates: List[_IndexEntry] = [
                e for e in self._index
                if start_ts_ms <= e.utc_timestamp_ms <= end_ts_ms
            ]

        # Read data from slab outside the lock (readers don't need to block writers
        # once they have a snapshot of the entry metadata — the slab positions
        # for old frames won't be overwritten while we read them as long as
        # extract_segment completes faster than the slab wraps around,
        # which at 30 fps × 60 s × ~30 KB frame = ~54 MB easily fits in 128 MB).
        for entry in candidates:
            try:
                buf_frame = self._read_entry(entry)
                if buf_frame is not None:
                    frames.append(buf_frame)
            except Exception as exc:
                log.error(
                    "extract_segment_read_error",
                    drone_id=self.drone_id,
                    frame_index=entry.frame_index,
                    error=str(exc),
                )

        log.debug(
            "extract_segment_complete",
            drone_id=self.drone_id,
            start_ts_ms=start_ts_ms,
            end_ts_ms=end_ts_ms,
            frames_found=len(frames),
        )
        return frames

    def get_latest_frame(self) -> Optional[BufferFrame]:
        """Return the most recently written frame, or None if buffer is empty."""
        with self._lock:
            if not self._index:
                return None
            entry = self._index[-1]

        return self._read_entry(entry)

    def get_frame_count(self) -> int:
        """Current number of frames in the buffer (thread-safe)."""
        with self._lock:
            return len(self._index)

    def get_oldest_timestamp_ms(self) -> Optional[int]:
        with self._lock:
            return self._index[0].utc_timestamp_ms if self._index else None

    def get_newest_timestamp_ms(self) -> Optional[int]:
        with self._lock:
            return self._index[-1].utc_timestamp_ms if self._index else None

    def get_segment_by_frame_range(
        self,
        start_frame_index: int,
        end_frame_index: int,
    ) -> List[BufferFrame]:
        """
        Return all frames whose *frame_index* falls within
        [start_frame_index, end_frame_index] (inclusive).

        Complementary to ``extract_segment`` which works on UTC timestamps.
        Useful when the caller knows exact frame indices rather than wall-clock time.
        """
        frames: List[BufferFrame] = []

        with self._lock:
            candidates: List[_IndexEntry] = [
                e for e in self._index
                if start_frame_index <= e.frame_index <= end_frame_index
            ]

        for entry in candidates:
            try:
                buf_frame = self._read_entry(entry)
                if buf_frame is not None:
                    frames.append(buf_frame)
            except Exception as exc:
                log.error(
                    "segment_by_frame_range_read_error",
                    drone_id=self.drone_id,
                    frame_index=entry.frame_index,
                    error=str(exc),
                )

        log.debug(
            "get_segment_by_frame_range_complete",
            drone_id=self.drone_id,
            start=start_frame_index,
            end=end_frame_index,
            found=len(frames),
        )
        return frames

    def get_buffer_stats(self) -> dict:
        """Return a snapshot of buffer health metrics (thread-safe)."""
        with self._lock:
            count = len(self._index)
            oldest = self._index[0].utc_timestamp_ms if self._index else None
            newest = self._index[-1].utc_timestamp_ms if self._index else None

        window_s = 0.0
        if oldest is not None and newest is not None:
            window_s = (newest - oldest) / 1_000.0

        slab_used_bytes = self._write_ptr
        slab_pct = round(slab_used_bytes / max(self.mmap_slab_size_bytes, 1) * 100, 1)

        return {
            "drone_id":          self.drone_id,
            "frames_in_buffer":  count,
            "max_frames":        self.max_frames,
            "fill_pct":          round(count / max(self.max_frames, 1) * 100, 1),
            "window_s":          round(window_s, 1),
            "oldest_ts_ms":      oldest,
            "newest_ts_ms":      newest,
            "slab_used_bytes":   slab_used_bytes,
            "slab_total_bytes":  self.mmap_slab_size_bytes,
            "slab_used_pct":     slab_pct,
            "total_written":     self._frame_count,
        }

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Flush and close the mmap slab. Call when shutting down."""
        with self._lock:
            try:
                self._slab.flush()
            except Exception:
                pass
            self._close_slab()
        log.info("buffer_closed", drone_id=self.drone_id)

    def delete_slab(self) -> None:
        """Close and delete the slab file from disk (useful in tests)."""
        self.close()
        try:
            self._slab_path.unlink(missing_ok=True)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evict_expired(self, current_ts_ms: int) -> None:
        """
        Remove index entries older than (current_ts_ms - duration_s * 1000).
        Must be called under self._lock.
        """
        cutoff_ms = current_ts_ms - (self.duration_s * 1_000)
        while self._index and self._index[0].utc_timestamp_ms < cutoff_ms:
            self._index.popleft()

    def _compress_jpeg(self, frame: np.ndarray) -> bytes:
        """
        Compress a BGR numpy array to JPEG bytes.

        Primary path: cv2.imencode — significantly faster than Pillow because it
        avoids the BGR→RGB conversion and uses OpenCV's built-in libjpeg/libjpeg-turbo.
        Estimated ~3–8 ms per 1280×720 frame at quality=85.

        Fallback: Pillow — used when cv2 is unavailable (e.g. in minimal test envs).
        """
        try:
            import cv2 as _cv2
            ok, buf = _cv2.imencode(
                ".jpg", frame,
                [_cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality],
            )
            if ok:
                return buf.tobytes()
        except Exception:
            pass

        # Pillow fallback (BGR → RGB required)
        rgb = frame[..., ::-1]
        pil_img = Image.fromarray(rgb)
        buf = io.BytesIO()
        pil_img.save(buf, format="JPEG", quality=self.jpeg_quality, optimize=False)
        return buf.getvalue()

    def _read_entry(self, entry: _IndexEntry) -> Optional[BufferFrame]:
        """
        Read header + payload from the slab for *entry*.
        Validates checksum and returns a BufferFrame, or None on error.
        The lock is NOT required — see extract_segment for the rationale.
        """
        try:
            offset = entry.slab_offset
            # Read header
            raw_header = bytes(self._slab[offset: offset + _HEADER_SIZE])
            if len(raw_header) < _HEADER_SIZE:
                return None

            frame_index, utc_ms, data_len, stored_checksum = struct.unpack(
                _HEADER_FMT, raw_header
            )

            # Sanity-check that this slot was not overwritten
            if frame_index != entry.frame_index:
                log.warning(
                    "slab_slot_overwritten",
                    drone_id=self.drone_id,
                    expected_frame_index=entry.frame_index,
                    found_frame_index=frame_index,
                )
                return None

            # Read JPEG payload
            payload_start = offset + _HEADER_SIZE
            jpeg_bytes = bytes(self._slab[payload_start: payload_start + data_len])

            # Verify checksum
            actual_checksum = _xor_checksum(jpeg_bytes)
            if actual_checksum != stored_checksum:
                log.warning(
                    "checksum_mismatch",
                    drone_id=self.drone_id,
                    frame_index=frame_index,
                )
                return None

            return BufferFrame(
                frame_index=frame_index,
                utc_timestamp_ms=utc_ms,
                jpeg_bytes=jpeg_bytes,
            )

        except Exception as exc:
            log.error(
                "read_entry_error",
                drone_id=self.drone_id,
                frame_index=entry.frame_index,
                error=str(exc),
            )
            return None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "RollingFrameBuffer":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def __repr__(self) -> str:
        with self._lock:
            count = len(self._index)
        return (
            f"RollingFrameBuffer(drone_id={self.drone_id!r}, "
            f"duration_s={self.duration_s}, "
            f"frames_in_buffer={count})"
        )


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _xor_checksum(data: bytes) -> int:
    """
    Fast XOR-fold checksum → uint32.
    Not cryptographic — used only as a lightweight slab-overwrite indicator.
    """
    result = 0
    # Process 4 bytes at a time
    i = 0
    while i + 4 <= len(data):
        chunk, = struct.unpack_from("<I", data, i)
        result ^= chunk
        i += 4
    # Remaining bytes
    while i < len(data):
        result ^= data[i]
        i += 1
    return result & 0xFFFFFFFF
