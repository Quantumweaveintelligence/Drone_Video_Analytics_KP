"""
tests/test_rolling_frame_buffer.py
====================================
Unit tests for Task 1.2 — RollingFrameBuffer

Compatible with pytest AND Python stdlib unittest.
Run with:  python -m pytest tests/  -OR-  python -m unittest discover tests/
"""

from __future__ import annotations

import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import List

import numpy as np

from src.buffer.rolling_frame_buffer import (
    BufferFrame,
    RollingFrameBuffer,
    _HEADER_FMT,
    _HEADER_SIZE,
    _xor_checksum,
)
from tests.conftest import make_bgr_frame, make_video_frame


# ── Helper ───────────────────────────────────────────────────────────────────

def _make_buf(tmp_dir: str, duration_s: int = 5, max_frames: int = 200) -> RollingFrameBuffer:
    return RollingFrameBuffer(
        drone_id="test_drone",
        duration_s=duration_s,
        storage_dir=tmp_dir,
        jpeg_quality=85,
        max_frames=max_frames,
        mmap_slab_size_bytes=16 * 1024 * 1024,
    )


# ── 1. Basic write / read ────────────────────────────────────────────────────

class TestBasicWriteRead(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.buf = _make_buf(self._tmpdir)

    def tearDown(self):
        self.buf.delete_slab()

    def test_write_single_frame_increases_count(self):
        self.assertEqual(self.buf.get_frame_count(), 0)
        self.buf.write_frame(0, int(time.time() * 1000), make_bgr_frame())
        self.assertEqual(self.buf.get_frame_count(), 1)

    def test_write_and_extract_returns_correct_frame_index(self):
        ts = int(time.time() * 1000)
        self.buf.write_frame(42, ts, make_bgr_frame(seed=42))
        frames = self.buf.extract_segment(ts - 10, ts + 10)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].frame_index, 42)

    def test_jpeg_roundtrip_preserves_shape(self):
        original = make_bgr_frame(width=320, height=240, seed=7)
        ts = int(time.time() * 1000)
        self.buf.write_frame(0, ts, original)
        frames = self.buf.extract_segment(ts - 1, ts + 1)
        self.assertEqual(len(frames), 1)
        decoded = frames[0].decode()
        self.assertEqual(decoded.shape, original.shape)

    def test_get_latest_frame_returns_last_written(self):
        base_ts = int(time.time() * 1000)
        for i in range(10):
            self.buf.write_frame(i, base_ts + i * 33, make_bgr_frame(seed=i))
        latest = self.buf.get_latest_frame()
        self.assertIsNotNone(latest)
        self.assertEqual(latest.frame_index, 9)

    def test_empty_buffer_returns_none_for_latest(self):
        self.assertIsNone(self.buf.get_latest_frame())

    def test_empty_buffer_extract_returns_empty_list(self):
        ts = int(time.time() * 1000)
        self.assertEqual(self.buf.extract_segment(ts - 5000, ts), [])

    def test_write_video_frame_convenience_wrapper(self):
        vf = make_video_frame(frame_index=99)
        self.buf.write_video_frame(vf)
        self.assertEqual(self.buf.get_frame_count(), 1)
        frames = self.buf.extract_segment(
            vf.utc_timestamp_ms - 10, vf.utc_timestamp_ms + 10
        )
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].frame_index, 99)


# ── 2. Buffer overflow behaviour ─────────────────────────────────────────────

class TestBufferOverflow(unittest.TestCase):

    def test_index_does_not_exceed_max_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            buf = RollingFrameBuffer(
                drone_id="overflow",
                duration_s=3600,
                storage_dir=tmp,
                max_frames=50,
                mmap_slab_size_bytes=16 * 1024 * 1024,
            )
            base_ts = int(time.time() * 1000)
            try:
                for i in range(100):
                    buf.write_frame(i, base_ts + i * 33, make_bgr_frame(seed=i))
                self.assertLessEqual(buf.get_frame_count(), 50)
            finally:
                buf.delete_slab()

    def test_old_frames_evicted_by_time_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            buf = _make_buf(tmp, duration_s=5)
            base_ts = 1_000_000_000_000
            for i in range(30):
                buf.write_frame(i, base_ts + i * 33, make_bgr_frame(seed=i))
            # Jump 6 seconds forward — all old frames should expire
            future_ts = base_ts + 6_000
            buf.write_frame(30, future_ts, make_bgr_frame(seed=30))
            self.assertEqual(buf.get_frame_count(), 1)
            buf.delete_slab()

    def test_extract_after_eviction_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            buf = _make_buf(tmp, duration_s=5)
            base_ts = 2_000_000_000_000
            for i in range(10):
                buf.write_frame(i, base_ts + i * 100, make_bgr_frame(seed=i))
            future_ts = base_ts + 6_000
            buf.write_frame(10, future_ts, make_bgr_frame(seed=10))
            frames = buf.extract_segment(base_ts, base_ts + 900)
            self.assertEqual(len(frames), 0)
            buf.delete_slab()


# ── 3. Timestamp accuracy ─────────────────────────────────────────────────────

class TestTimestampAccuracy(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.buf = _make_buf(self._tmpdir)

    def tearDown(self):
        self.buf.delete_slab()

    def test_stored_timestamp_matches_input(self):
        exact_ts = 1_700_000_000_123
        self.buf.write_frame(0, exact_ts, make_bgr_frame())
        latest = self.buf.get_latest_frame()
        self.assertIsNotNone(latest)
        self.assertEqual(latest.utc_timestamp_ms, exact_ts)

    def test_extract_includes_boundary_timestamps(self):
        base_ts = 1_700_000_000_000
        for i in range(5):
            self.buf.write_frame(i, base_ts + i * 1000, make_bgr_frame(seed=i))
        frames = self.buf.extract_segment(base_ts, base_ts + 4_000)
        self.assertEqual(len(frames), 5)

    def test_extract_excludes_out_of_range(self):
        base_ts = 1_700_000_000_000
        for i in range(5):
            self.buf.write_frame(i, base_ts + i * 1000, make_bgr_frame(seed=i))
        frames = self.buf.extract_segment(base_ts + 1_000, base_ts + 3_000)
        self.assertEqual(len(frames), 3)
        self.assertEqual(frames[0].frame_index, 1)
        self.assertEqual(frames[-1].frame_index, 3)

    def test_frames_returned_in_chronological_order(self):
        base_ts = 1_700_000_000_000
        for seq in range(8):
            self.buf.write_frame(seq, base_ts + seq * 100, make_bgr_frame(seed=seq))
        frames = self.buf.extract_segment(base_ts, base_ts + 10_000)
        timestamps = [f.utc_timestamp_ms for f in frames]
        self.assertEqual(timestamps, sorted(timestamps))


# ── 4. Concurrent read/write safety ──────────────────────────────────────────

class TestConcurrentReadWrite(unittest.TestCase):

    def test_concurrent_writes_no_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            buf = RollingFrameBuffer(
                drone_id="concurrent",
                duration_s=60,
                storage_dir=tmp,
                max_frames=2000,
                mmap_slab_size_bytes=32 * 1024 * 1024,
            )
            errors: List[str] = []
            base_ts = int(time.time() * 1000)

            def producer(tid: int):
                for i in range(50):
                    try:
                        buf.write_frame(
                            tid * 10_000 + i, base_ts + i * 33, make_bgr_frame(seed=i)
                        )
                    except Exception as e:
                        errors.append(f"T{tid}: {e}")

            threads = [threading.Thread(target=producer, args=(t,)) for t in range(4)]
            for t in threads: t.start()
            for t in threads: t.join(timeout=30)

            self.assertEqual(errors, [])
            self.assertGreater(buf.get_frame_count(), 0)
            buf.delete_slab()

    def test_concurrent_reader_and_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            buf = RollingFrameBuffer(
                drone_id="rw", duration_s=10, storage_dir=tmp,
                max_frames=1000, mmap_slab_size_bytes=32 * 1024 * 1024,
            )
            base_ts = int(time.time() * 1000)
            done = threading.Event()
            errors: List[str] = []

            def writer():
                for i in range(100):
                    buf.write_frame(i, base_ts + i * 33, make_bgr_frame(seed=i % 5))
                    time.sleep(0.001)
                done.set()

            def reader():
                while not done.is_set():
                    try:
                        buf.extract_segment(base_ts, base_ts + 5_000)
                    except Exception as e:
                        errors.append(str(e))
                    time.sleep(0.005)

            wt = threading.Thread(target=writer)
            rt = threading.Thread(target=reader)
            wt.start(); rt.start()
            wt.join(timeout=15); rt.join(timeout=15)
            self.assertEqual(errors, [])
            buf.delete_slab()


# ── 5. Checksum / integrity ──────────────────────────────────────────────────

class TestChecksumIntegrity(unittest.TestCase):

    def test_xor_checksum_deterministic(self):
        data = b"KeralaPoliceEdgeBuffer_Integrity"
        self.assertEqual(_xor_checksum(data), _xor_checksum(data))

    def test_xor_checksum_detects_bit_flip(self):
        data = bytearray(b"PayloadBytes12345678")
        orig = _xor_checksum(bytes(data))
        data[5] ^= 0xFF
        self.assertNotEqual(_xor_checksum(bytes(data)), orig)

    def test_xor_checksum_empty(self):
        self.assertEqual(_xor_checksum(b""), 0)

    def test_corrupted_header_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            buf = _make_buf(tmp)
            ts = int(time.time() * 1000)
            buf.write_frame(0, ts, make_bgr_frame(seed=0))
            # Corrupt frame_index in the header
            with buf._lock:
                entry = buf._index[0]
                offset = entry.slab_offset
                bad = struct.pack(_HEADER_FMT, 999, ts, 100, 0)
                buf._slab[offset: offset + _HEADER_SIZE] = bad
            frames = buf.extract_segment(ts - 1, ts + 1)
            self.assertEqual(frames, [])
            buf.delete_slab()


# ── 6. Slab wrap-around ──────────────────────────────────────────────────────

class TestSlabWrapAround(unittest.TestCase):

    def test_write_ptr_wraps_and_buffer_remains_functional(self):
        with tempfile.TemporaryDirectory() as tmp:
            buf = RollingFrameBuffer(
                drone_id="wrap",
                duration_s=3600,
                storage_dir=tmp,
                max_frames=5000,
                mmap_slab_size_bytes=2 * 1024 * 1024,
            )
            base_ts = int(time.time() * 1000)
            try:
                for i in range(200):
                    frame = np.zeros((64, 64, 3), dtype=np.uint8)
                    frame[..., i % 3] = i % 256
                    buf.write_frame(i, base_ts + i * 33, frame)
                self.assertGreater(buf.get_frame_count(), 0)
                self.assertIsNotNone(buf.get_latest_frame())
            finally:
                buf.delete_slab()


# ── 7. Context manager ───────────────────────────────────────────────────────

class TestContextManager(unittest.TestCase):

    def test_context_manager_closes_mmap(self):
        with tempfile.TemporaryDirectory() as tmp:
            buf = RollingFrameBuffer(
                drone_id="ctx", duration_s=5, storage_dir=tmp,
                max_frames=50, mmap_slab_size_bytes=4 * 1024 * 1024,
            )
            with buf:
                buf.write_frame(0, int(time.time() * 1000), make_bgr_frame())
            # After __exit__ mmap is closed → access raises ValueError or OSError
            with self.assertRaises((ValueError, OSError)):
                _ = buf._slab[0]

    def test_slab_file_persists_after_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            buf = RollingFrameBuffer(
                drone_id="persist", duration_s=5, storage_dir=tmp,
                max_frames=50, mmap_slab_size_bytes=4 * 1024 * 1024,
            )
            slab_path = buf._slab_path
            with buf:
                pass
            self.assertTrue(slab_path.exists())


# ── 8. Edge cases ────────────────────────────────────────────────────────────

class TestEdgeCases(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.buf = _make_buf(self._tmpdir)

    def tearDown(self):
        self.buf.delete_slab()

    def test_single_frame_exact_boundary_extract(self):
        ts = 1_700_000_000_000
        self.buf.write_frame(0, ts, make_bgr_frame())
        frames = self.buf.extract_segment(ts, ts)
        self.assertEqual(len(frames), 1)

    def test_oldest_and_newest_timestamps(self):
        base_ts = 1_700_000_000_000
        for i in range(5):
            self.buf.write_frame(i, base_ts + i * 100, make_bgr_frame(seed=i))
        self.assertEqual(self.buf.get_oldest_timestamp_ms(), base_ts)
        self.assertEqual(self.buf.get_newest_timestamp_ms(), base_ts + 400)

    def test_repr_contains_class_name(self):
        self.assertIn("RollingFrameBuffer", repr(self.buf))

    def test_various_resolutions(self):
        base_ts = int(time.time() * 1000)
        for i, (w, h) in enumerate([(320, 240), (640, 480), (1280, 720)]):
            self.buf.write_frame(i, base_ts + i * 100, make_bgr_frame(w, h, seed=i))
        self.assertEqual(self.buf.get_frame_count(), 3)

    def test_extract_empty_window_returns_empty(self):
        base_ts = 1_700_000_000_000
        self.buf.write_frame(0, base_ts, make_bgr_frame())
        frames = self.buf.extract_segment(base_ts + 100_000, base_ts + 200_000)
        self.assertEqual(len(frames), 0)


# ── 9. get_segment_by_frame_range ────────────────────────────────────────────

class TestSegmentByFrameRange(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.buf = _make_buf(self._tmpdir, duration_s=3600)

    def tearDown(self):
        self.buf.delete_slab()

    def test_exact_range_returns_correct_frames(self):
        base_ts = int(time.time() * 1000)
        for i in range(10):
            self.buf.write_frame(i, base_ts + i * 33, make_bgr_frame(seed=i))
        frames = self.buf.get_segment_by_frame_range(2, 5)
        self.assertEqual(len(frames), 4)
        self.assertEqual(frames[0].frame_index, 2)
        self.assertEqual(frames[-1].frame_index, 5)

    def test_single_frame_range(self):
        base_ts = int(time.time() * 1000)
        self.buf.write_frame(7, base_ts, make_bgr_frame(seed=7))
        frames = self.buf.get_segment_by_frame_range(7, 7)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].frame_index, 7)

    def test_out_of_range_returns_empty(self):
        base_ts = int(time.time() * 1000)
        for i in range(5):
            self.buf.write_frame(i, base_ts + i * 33, make_bgr_frame(seed=i))
        frames = self.buf.get_segment_by_frame_range(100, 200)
        self.assertEqual(frames, [])

    def test_range_inclusive_boundaries(self):
        base_ts = int(time.time() * 1000)
        for i in range(10):
            self.buf.write_frame(i, base_ts + i * 33, make_bgr_frame(seed=i))
        frames = self.buf.get_segment_by_frame_range(0, 9)
        self.assertEqual(len(frames), 10)

    def test_partial_overlap_returns_subset(self):
        base_ts = int(time.time() * 1000)
        for i in range(10):
            self.buf.write_frame(i, base_ts + i * 33, make_bgr_frame(seed=i))
        frames = self.buf.get_segment_by_frame_range(8, 20)   # 8 and 9 match
        self.assertEqual(len(frames), 2)


# ── 10. get_buffer_stats ──────────────────────────────────────────────────────

class TestBufferStats(unittest.TestCase):

    def test_empty_buffer_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            buf = _make_buf(tmp)
            stats = buf.get_buffer_stats()
            self.assertEqual(stats["frames_in_buffer"], 0)
            self.assertIsNone(stats["oldest_ts_ms"])
            self.assertIsNone(stats["newest_ts_ms"])
            self.assertEqual(stats["fill_pct"], 0.0)
            buf.delete_slab()

    def test_stats_after_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            buf = _make_buf(tmp, duration_s=3600, max_frames=200)
            base_ts = 1_700_000_000_000
            for i in range(10):
                buf.write_frame(i, base_ts + i * 100, make_bgr_frame(seed=i))
            stats = buf.get_buffer_stats()
            self.assertEqual(stats["frames_in_buffer"], 10)
            self.assertEqual(stats["oldest_ts_ms"], base_ts)
            self.assertEqual(stats["newest_ts_ms"], base_ts + 900)
            self.assertGreater(stats["slab_used_bytes"], 0)
            self.assertIn("fill_pct", stats)
            self.assertIn("window_s", stats)
            self.assertEqual(stats["total_written"], 10)
            buf.delete_slab()

    def test_stats_keys_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            buf = _make_buf(tmp)
            stats = buf.get_buffer_stats()
            required_keys = {
                "drone_id", "frames_in_buffer", "max_frames", "fill_pct",
                "window_s", "oldest_ts_ms", "newest_ts_ms",
                "slab_used_bytes", "slab_total_bytes", "slab_used_pct",
                "total_written",
            }
            self.assertTrue(required_keys.issubset(stats.keys()))
            buf.delete_slab()


# ── 11. JPEG compression path ─────────────────────────────────────────────────

class TestJpegCompression(unittest.TestCase):
    """Verify that both the cv2.imencode and Pillow paths produce valid JPEG."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.buf = _make_buf(self._tmpdir)

    def tearDown(self):
        self.buf.delete_slab()

    def test_compressed_bytes_are_valid_jpeg(self):
        frame = make_bgr_frame(width=640, height=480, seed=1)
        jpeg_bytes = self.buf._compress_jpeg(frame)
        # Valid JPEG starts with SOI marker 0xFFD8
        self.assertTrue(jpeg_bytes[:2] == b"\xff\xd8",
                         f"Not a JPEG SOI marker: {jpeg_bytes[:4].hex()}")

    def test_roundtrip_shape_preserved(self):
        original = make_bgr_frame(width=320, height=240, seed=5)
        jpeg_bytes = self.buf._compress_jpeg(original)
        from PIL import Image
        import io
        pil_img = Image.open(io.BytesIO(jpeg_bytes))
        self.assertEqual(pil_img.size, (320, 240))

    def test_quality_affects_size(self):
        frame = make_bgr_frame(width=640, height=480, seed=2)
        with tempfile.TemporaryDirectory() as tmp:
            buf_hq = RollingFrameBuffer(
                drone_id="hq", duration_s=60, storage_dir=tmp,
                jpeg_quality=95, max_frames=50, mmap_slab_size_bytes=16*1024*1024,
            )
            buf_lq = RollingFrameBuffer(
                drone_id="lq", duration_s=60, storage_dir=tmp,
                jpeg_quality=30, max_frames=50, mmap_slab_size_bytes=16*1024*1024,
            )
            hq_bytes = buf_hq._compress_jpeg(frame)
            lq_bytes = buf_lq._compress_jpeg(frame)
            self.assertGreater(len(hq_bytes), len(lq_bytes))
            buf_hq.delete_slab()
            buf_lq.delete_slab()

    def test_empty_frame_does_not_crash(self):
        frame = np.zeros((1, 1, 3), dtype=np.uint8)
        jpeg_bytes = self.buf._compress_jpeg(frame)
        self.assertGreater(len(jpeg_bytes), 0)


if __name__ == "__main__":
    unittest.main()
