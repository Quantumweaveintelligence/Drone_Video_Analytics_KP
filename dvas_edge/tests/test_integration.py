"""
tests/test_integration.py
===========================
Integration test: DroneVideoSource → RollingFrameBuffer pipeline.
Compatible with pytest AND Python stdlib unittest.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from typing import List
from unittest.mock import patch

import numpy as np

from src.buffer.rolling_frame_buffer import RollingFrameBuffer
from src.video.drone_video_source import VideoFrame
from tests.conftest import make_mock_cap


class TestSourceToBufferPipeline(unittest.TestCase):

    def _make_buf(self, tmp: str) -> RollingFrameBuffer:
        return RollingFrameBuffer(
            drone_id="integ",
            duration_s=10,
            storage_dir=tmp,
            max_frames=500,
            mmap_slab_size_bytes=32 * 1024 * 1024,
        )

    def test_frames_arrive_in_buffer_via_callback(self):
        with tempfile.TemporaryDirectory() as tmp:
            buf = self._make_buf(tmp)
            cap = make_mock_cap(n_frames=300)

            with patch("src.video.drone_video_source.cv2.VideoCapture", return_value=cap):
                from src.video.drone_video_source import DroneVideoSource
                src = DroneVideoSource(
                    drone_id="integ",
                    on_frame=buf.write_video_frame,
                    reconnect_base_delay_s=5.0,
                )
                src.start()
                time.sleep(0.5)
                src.stop()

            self.assertGreater(buf.get_frame_count(), 0)
            buf.delete_slab()

    def test_extract_segment_returns_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            buf = self._make_buf(tmp)
            cap = make_mock_cap(n_frames=100)
            t_before = int(time.time() * 1000)

            with patch("src.video.drone_video_source.cv2.VideoCapture", return_value=cap):
                from src.video.drone_video_source import DroneVideoSource
                src = DroneVideoSource(
                    drone_id="integ2",
                    on_frame=buf.write_video_frame,
                    reconnect_base_delay_s=5.0,
                )
                src.start()
                time.sleep(0.4)
                src.stop()

            t_after = int(time.time() * 1000)
            frames = buf.extract_segment(t_before - 100, t_after + 100)
            self.assertGreater(len(frames), 0)
            buf.delete_slab()

    def test_decoded_frames_are_valid_bgr_arrays(self):
        with tempfile.TemporaryDirectory() as tmp:
            buf = self._make_buf(tmp)
            cap = make_mock_cap(n_frames=30)
            t_before = int(time.time() * 1000)

            with patch("src.video.drone_video_source.cv2.VideoCapture", return_value=cap):
                from src.video.drone_video_source import DroneVideoSource
                src = DroneVideoSource(
                    drone_id="integ3",
                    on_frame=buf.write_video_frame,
                    reconnect_base_delay_s=5.0,
                )
                src.start()
                time.sleep(0.3)
                src.stop()

            t_after = int(time.time() * 1000)
            frames = buf.extract_segment(t_before - 100, t_after + 100)
            self.assertGreater(len(frames), 0)

            for bf in frames[:3]:
                decoded = bf.decode()
                self.assertEqual(decoded.ndim, 3)
                self.assertEqual(decoded.shape[2], 3)
                self.assertEqual(decoded.dtype, np.uint8)

            buf.delete_slab()


if __name__ == "__main__":
    unittest.main()
