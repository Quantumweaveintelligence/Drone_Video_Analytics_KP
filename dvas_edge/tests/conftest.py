"""
tests/conftest.py
==================
Shared test helpers — works with both pytest and stdlib unittest.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import List
from unittest.mock import MagicMock

import numpy as np
import pytest

from src.video.drone_video_source import VideoFrame


def make_bgr_frame(width: int = 640, height: int = 480, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, (height, width, 3), dtype=np.uint8)


def make_video_frame(
    frame_index: int = 0,
    utc_timestamp_ms: int = 0,
    width: int = 640,
    height: int = 480,
) -> VideoFrame:
    if utc_timestamp_ms == 0:
        utc_timestamp_ms = int(time.time() * 1_000)
    return VideoFrame(
        frame_index=frame_index,
        utc_timestamp_ms=utc_timestamp_ms,
        frame=make_bgr_frame(width, height, seed=frame_index),
        width=width,
        height=height,
        decode_latency_ms=1.0,
    )


def make_mock_cap(
    opened: bool = True,
    width: float = 1280.0,
    height: float = 720.0,
    fps: float = 25.0,
    n_frames: int = 5000,
) -> MagicMock:
    cap = MagicMock()
    cap.isOpened.return_value = opened
    cap.get.side_effect = lambda prop: {3: width, 4: height, 5: fps}.get(prop, 0.0)
    call_idx = [0]

    def _read():
        i = call_idx[0]
        call_idx[0] += 1
        if i < n_frames:
            return True, make_bgr_frame(seed=i % 8)
        return False, None

    cap.read.side_effect = _read
    return cap


@pytest.fixture(autouse=True)
def _allow_http_test_servers(monkeypatch):
    """Test servers run on plain http://127.0.0.1; production requires https (see test_endpoints)."""
    monkeypatch.setenv("DVAS_ALLOW_HTTP_INGEST", "true")
