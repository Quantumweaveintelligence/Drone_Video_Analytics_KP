"""
tests/test_drone_video_source.py
==================================
Unit tests for Task 1.1 — DroneVideoSource
All tests mock cv2.VideoCapture — no real camera/RTSP needed.
Compatible with pytest AND Python stdlib unittest.
"""

from __future__ import annotations

import time
import threading
import unittest
from typing import List
from unittest.mock import MagicMock, patch

from src.video.drone_video_source import DroneVideoSource, VideoFrame
from tests.conftest import make_bgr_frame, make_mock_cap


# ── 1. URL sanitisation ──────────────────────────────────────────────────────

class TestURLSanitisation(unittest.TestCase):

    def test_credentials_stripped(self):
        result = DroneVideoSource._sanitise_url(
            "rtsp://admin:Admin12345@10.10.32.23:554/h264/ch1/main/av_stream"
        )
        self.assertNotIn("Admin12345", result)
        self.assertNotIn("admin", result)
        self.assertIn("10.10.32.23", result)

    def test_url_without_credentials_unchanged(self):
        url = "rtsp://10.10.32.23:554/stream"
        self.assertIn("10.10.32.23", DroneVideoSource._sanitise_url(url))


# ── 2. Back-off calculation ──────────────────────────────────────────────────

class TestBackoff(unittest.TestCase):

    def setUp(self):
        self.src = DroneVideoSource(
            reconnect_base_delay_s=1.0,
            reconnect_multiplier=2.0,
            reconnect_max_delay_s=30.0,
        )

    def test_exponential_growth(self):
        self.assertAlmostEqual(self.src._backoff_delay(0), 1.0)
        self.assertAlmostEqual(self.src._backoff_delay(1), 2.0)
        self.assertAlmostEqual(self.src._backoff_delay(2), 4.0)
        self.assertAlmostEqual(self.src._backoff_delay(3), 8.0)

    def test_capped_at_max(self):
        self.assertAlmostEqual(self.src._backoff_delay(100), 30.0)


# ── 3. Frame delivery via callback ───────────────────────────────────────────

class TestFrameDelivery(unittest.TestCase):

    def test_on_frame_callback_invoked(self):
        cap = make_mock_cap(n_frames=200)
        received: List[VideoFrame] = []

        with patch("src.video.drone_video_source.cv2.VideoCapture", return_value=cap):
            src = DroneVideoSource(drone_id="cb_test", on_frame=received.append)
            src.start()
            time.sleep(0.4)
            src.stop()

        self.assertGreater(len(received), 0)
        self.assertIsInstance(received[0], VideoFrame)

    def test_frame_index_monotonically_increasing(self):
        cap = make_mock_cap(n_frames=500)
        received: List[VideoFrame] = []

        with patch("src.video.drone_video_source.cv2.VideoCapture", return_value=cap):
            src = DroneVideoSource(drone_id="mono_test", on_frame=received.append)
            src.start()
            time.sleep(0.4)
            src.stop()

        indices = [f.frame_index for f in received]
        self.assertEqual(indices, sorted(set(indices)))

    def test_read_returns_video_frame(self):
        cap = make_mock_cap(n_frames=500)
        with patch("src.video.drone_video_source.cv2.VideoCapture", return_value=cap):
            src = DroneVideoSource(drone_id="read_test")
            src.start()
            frame = src.read(timeout=3.0)
            src.stop()

        self.assertIsNotNone(frame)
        self.assertIsInstance(frame, VideoFrame)
        self.assertGreater(frame.utc_timestamp_ms, 0)

    def test_read_none_when_no_stream(self):
        cap = make_mock_cap(opened=False)
        with patch("src.video.drone_video_source.cv2.VideoCapture", return_value=cap):
            src = DroneVideoSource(
                drone_id="no_stream",
                reconnect_base_delay_s=10.0,
                reconnect_max_delay_s=10.0,
            )
            src.start()
            result = src.read(timeout=0.3)
            src.stop()

        self.assertIsNone(result)


# ── 4. Reconnection after failure ────────────────────────────────────────────

class TestReconnection(unittest.TestCase):

    def test_reconnects_after_open_failure(self):
        cap_fail = make_mock_cap(opened=False)
        cap_ok   = make_mock_cap(n_frames=500)
        open_count = [0]

        def fake_cap(src_arg, *a, **kw):
            open_count[0] += 1
            return cap_fail if open_count[0] <= 1 else cap_ok

        received: List[VideoFrame] = []

        with patch("src.video.drone_video_source.cv2.VideoCapture", side_effect=fake_cap):
            src = DroneVideoSource(
                drone_id="reconnect",
                reconnect_base_delay_s=0.05,
                reconnect_max_delay_s=0.1,
                on_frame=received.append,
            )
            src.start()
            time.sleep(1.0)
            src.stop()

        self.assertGreater(len(received), 0)
        self.assertGreaterEqual(open_count[0], 2)

    def test_reconnects_after_read_timeout(self):
        # First cap: reads always return (False, None)
        cap_fail = make_mock_cap(n_frames=0)   # n_frames=0 → always (False, None)
        cap_ok   = make_mock_cap(n_frames=500)
        open_count = [0]

        def fake_cap(src_arg, *a, **kw):
            open_count[0] += 1
            return cap_fail if open_count[0] == 1 else cap_ok

        received: List[VideoFrame] = []

        with patch("src.video.drone_video_source.cv2.VideoCapture", side_effect=fake_cap):
            src = DroneVideoSource(
                drone_id="timeout_reconnect",
                read_timeout_s=0.2,
                reconnect_base_delay_s=0.05,
                reconnect_max_delay_s=0.1,
                on_frame=received.append,
            )
            src.start()
            time.sleep(1.5)
            src.stop()

        self.assertGreaterEqual(open_count[0], 2)
        self.assertGreater(len(received), 0)


# ── 5. is_connected state ────────────────────────────────────────────────────

class TestConnectedState(unittest.TestCase):

    def test_connected_after_start(self):
        cap = make_mock_cap()
        with patch("src.video.drone_video_source.cv2.VideoCapture", return_value=cap):
            src = DroneVideoSource(drone_id="conn_test")
            src.start()
            time.sleep(0.2)
            self.assertTrue(src.is_connected)
            src.stop()

    def test_disconnected_after_stop(self):
        cap = make_mock_cap()
        with patch("src.video.drone_video_source.cv2.VideoCapture", return_value=cap):
            src = DroneVideoSource(drone_id="disc_test")
            src.start()
            time.sleep(0.2)
            src.stop()
        self.assertFalse(src.is_connected)


# ── 6. Context manager ───────────────────────────────────────────────────────

class TestContextManager(unittest.TestCase):

    def test_context_manager_starts_and_stops(self):
        cap = make_mock_cap()
        received: List[VideoFrame] = []

        with patch("src.video.drone_video_source.cv2.VideoCapture", return_value=cap):
            with DroneVideoSource(drone_id="ctx_cm", on_frame=received.append) as src:
                time.sleep(0.3)
                self.assertTrue(src.is_connected)

        self.assertFalse(src._reader_thread.is_alive())

    def test_double_stop_is_safe(self):
        cap = make_mock_cap()
        with patch("src.video.drone_video_source.cv2.VideoCapture", return_value=cap):
            src = DroneVideoSource(drone_id="dbl_stop")
            src.start()
            time.sleep(0.1)
            src.stop()
            src.stop()  # must not raise


# ── 7. Repr ──────────────────────────────────────────────────────────────────

class TestRepr(unittest.TestCase):

    def test_repr_contains_drone_id(self):
        src = DroneVideoSource(drone_id="repr_test")
        self.assertIn("repr_test", repr(src))


if __name__ == "__main__":
    unittest.main()
