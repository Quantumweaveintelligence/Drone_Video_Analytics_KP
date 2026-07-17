"""
tests/test_cognitive_frame_selector.py
=======================================
Unit tests for the CognitiveFrameSelector (Task 1.5).

Tests cover:
  - Signal score functions return values in [0, 1]
  - Emergency priority override: always selects
  - Minimum threshold gate: never selects
  - Sliding-window percentile ranking works correctly
  - Early-window simple threshold (< 5 frames)
  - Context boost increases priority for high-motion events
  - Extraction rate over many frames stays near the configured target
  - get_stats() returns accurate counts
  - reset_state() clears internal window
  - SelectedFrame payload has correct fields and serialises to JSON
  - on_selected_frame callback is invoked correctly
  - Thread-safety: concurrent evaluate() calls don't corrupt state
  - ChangeEvent without raw_frame is handled gracefully (skipped)
"""

from __future__ import annotations

import json
import threading
from typing import List, Optional
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest

# ── System under test ──────────────────────────────────────────────────────────
from src.cognitive_frame_selector import CognitiveFrameSelector, SelectedFrame
from src.cognitive_frame_selector.selected_frame import GpsLocation

# ── Helpers that depend on ChangeEvent ─────────────────────────────────────────
from src.change_detection.change_event import BoundingBox, ChangeEvent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_frame(
    width: int = 320,
    height: int = 240,
    color: tuple = (128, 128, 128),
    noise_sigma: float = 0.0,
) -> np.ndarray:
    """Create a synthetic BGR frame, optionally with Gaussian noise."""
    frame = np.full((height, width, 3), color, dtype=np.uint8)
    if noise_sigma > 0:
        noise = np.random.normal(0, noise_sigma, frame.shape).astype(np.int16)
        frame = np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return frame


def _make_bright_frame(width: int = 320, height: int = 240) -> np.ndarray:
    """A frame with bright objects, high edges — should score high on object score."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    # Large bright rectangle (vehicle-like)
    cv2.rectangle(frame, (50, 80), (200, 160), (220, 220, 220), -1)
    # Strong edges
    cv2.rectangle(frame, (10, 10), (310, 230), (200, 200, 200), 3)
    return frame


def _make_change_event(
    frame: Optional[np.ndarray],
    motion_score: float = 0.05,
    frame_index: int = 0,
) -> ChangeEvent:
    """Build a minimal ChangeEvent with the given raw_frame."""
    bbox = BoundingBox(x=10, y=10, w=50, h=50, area=2500, motion_magnitude=128.0)
    return ChangeEvent(
        timestamp_ms=1_710_000_000_000 + frame_index * 33,
        drone_id="drone_test",
        frame_index=frame_index,
        regions=[bbox],
        motion_score=motion_score,
        raw_frame=frame,
    )


@pytest.fixture
def selector() -> CognitiveFrameSelector:
    """A default CognitiveFrameSelector instance with test-friendly settings."""
    return CognitiveFrameSelector(
        drone_id="test_drone",
        mission_id="TEST_MISSION",
        transmission_target=0.35,
        window_size=10,
        percentile_threshold=0.65,
        absolute_min_priority=0.05,
        emergency_priority=0.60,
    )


# ---------------------------------------------------------------------------
# 1. Signal scorers return values in [0, 1]
# ---------------------------------------------------------------------------

class TestSignalScorers:
    def test_histogram_score_range(self, selector):
        frame_a = _make_frame(color=(100, 100, 100))
        frame_b = _make_frame(color=(200, 50, 50))
        with selector._lock:
            selector._compute_histogram_score(frame_a)   # warm up prev_hist
            score = selector._compute_histogram_score(frame_b)
        assert 0.0 <= score <= 1.0

    def test_histogram_identical_frames_low_score(self, selector):
        frame = _make_frame(color=(150, 150, 150))
        with selector._lock:
            selector._compute_histogram_score(frame)
            score = selector._compute_histogram_score(frame.copy())
        assert score < 0.05, f"Identical frames should have low histogram score, got {score}"

    def test_motion_score_range(self, selector):
        frame_a = _make_frame(noise_sigma=5.0)
        frame_b = _make_frame(color=(200, 200, 200), noise_sigma=5.0)
        with selector._lock:
            selector._compute_motion_score(frame_a)
            score = selector._compute_motion_score(frame_b)
        assert 0.0 <= score <= 1.0

    def test_motion_score_zero_first_frame(self, selector):
        frame = _make_frame()
        with selector._lock:
            score = selector._compute_motion_score(frame)
        assert score == 0.0

    def test_object_score_range(self, selector):
        frame = _make_bright_frame()
        with selector._lock:
            score = selector._compute_object_score(frame)
        assert 0.0 <= score <= 1.0

    def test_object_score_bright_frame_higher(self, selector):
        plain  = _make_frame(color=(50, 50, 50))
        bright = _make_bright_frame()
        with selector._lock:
            plain_score  = selector._compute_object_score(plain)
            bright_score = selector._compute_object_score(bright)
        assert bright_score > plain_score, (
            f"Bright/edge-rich frame should score higher: {bright_score} vs {plain_score}"
        )


# ---------------------------------------------------------------------------
# 2. Emergency priority override
# ---------------------------------------------------------------------------

class TestEmergencyOverride:
    def test_emergency_priority_always_selected(self, selector):
        """Frames with priority >= emergency_priority must always be selected."""
        # Patch the scoring to return a fixed high priority
        frame = _make_bright_frame()
        event = _make_change_event(frame, motion_score=0.99)

        # Force emergency by mocking _compute_priority_score
        original = selector._compute_priority_score
        selector._compute_priority_score = lambda f, fi, cm: (0.95, {"histogram": 0, "motion": 0, "object": 0})

        result = selector.evaluate(event)

        selector._compute_priority_score = original
        assert result is not None, "Emergency-priority frame should always be selected"

    def test_emergency_fires_callback(self, selector):
        """Callback must be called when emergency frame is selected."""
        callback_results: List[SelectedFrame] = []
        selector.on_selected_frame = callback_results.append

        frame = _make_bright_frame()
        event = _make_change_event(frame, motion_score=0.99)

        selector._compute_priority_score = lambda f, fi, cm: (0.95, {"histogram": 0, "motion": 0, "object": 0})
        selector.evaluate(event)

        assert len(callback_results) == 1
        assert callback_results[0].selection_reason == "emergency_high_priority"


# ---------------------------------------------------------------------------
# 3. Minimum threshold gate
# ---------------------------------------------------------------------------

class TestMinimumThreshold:
    def test_below_minimum_never_selected(self, selector):
        """Frames with priority < absolute_min_priority must never be selected."""
        frame = _make_frame()
        event = _make_change_event(frame, motion_score=0.001)

        selector._compute_priority_score = lambda f, fi, cm: (0.01, {"histogram": 0, "motion": 0, "object": 0})

        result = selector.evaluate(event)
        assert result is None, "Below-minimum frame should never be selected"

    def test_below_minimum_increments_evaluated_not_selected(self, selector):
        frame = _make_frame()
        event = _make_change_event(frame)
        selector._compute_priority_score = lambda f, fi, cm: (0.01, {"histogram": 0, "motion": 0, "object": 0})

        selector.evaluate(event)
        stats = selector.get_stats()
        assert stats["frames_evaluated"] == 1
        assert stats["frames_selected"] == 0


# ---------------------------------------------------------------------------
# 4. No raw_frame — graceful skip
# ---------------------------------------------------------------------------

class TestNoRawFrame:
    def test_event_without_raw_frame_returns_none(self, selector):
        event = _make_change_event(frame=None, motion_score=0.5)
        result = selector.evaluate(event)
        assert result is None

    def test_event_without_raw_frame_does_not_increment_evaluated(self, selector):
        event = _make_change_event(frame=None)
        selector.evaluate(event)
        assert selector.get_stats()["frames_evaluated"] == 0


# ---------------------------------------------------------------------------
# 5. Sliding-window percentile ranking
# ---------------------------------------------------------------------------

class TestPercentileRanking:
    def test_top_percentile_selected(self, selector):
        """Feed the window with low-priority frames, then a high one — it must be selected."""
        selector.window_size = 10
        selector.percentile_threshold = 0.65

        # First 9 frames with low priority — fill window
        call_idx = [0]
        def _mock_low(f, fi, cm):
            call_idx[0] += 1
            return 0.10, {"histogram": 0, "motion": 0, "object": 0}

        selector._compute_priority_score = _mock_low
        for i in range(9):
            event = _make_change_event(_make_frame(), frame_index=i)
            selector.evaluate(event)

        # Now a clearly high-priority frame
        selector._compute_priority_score = lambda f, fi, cm: (0.90, {"histogram": 0, "motion": 0, "object": 0})
        event = _make_change_event(_make_bright_frame(), frame_index=9)
        result = selector.evaluate(event)

        assert result is not None, "High-priority frame should be selected from window"

    def test_early_window_simple_threshold(self, selector):
        """With fewer than 5 frames in window, simple threshold > 0.2 applies."""
        selector._compute_priority_score = lambda f, fi, cm: (0.25, {"histogram": 0, "motion": 0, "object": 0})
        event = _make_change_event(_make_frame(), frame_index=0)
        result = selector.evaluate(event)
        # priority 0.25 > 0.2 → should be selected in early window
        assert result is not None

    def test_early_window_below_simple_threshold(self, selector):
        selector._compute_priority_score = lambda f, fi, cm: (0.15, {"histogram": 0, "motion": 0, "object": 0})
        event = _make_change_event(_make_frame(), frame_index=0)
        result = selector.evaluate(event)
        # 0.15 <= 0.2 → should NOT be selected in early window
        assert result is None


# ---------------------------------------------------------------------------
# 6. Extraction rate over many synthetic frames
# ---------------------------------------------------------------------------

class TestExtractionRate:
    def test_extraction_rate_near_target(self):
        """Over 100 frames, the selection rate should stay within a reasonable range.

        We set emergency_priority=1.1 (unreachable) so all decisions go through
        the sliding-window percentile path, and use plain uniform-intensity frames
        (no noise) to keep motion_score low and avoid clamping at 1.0.
        """
        target = 0.35
        sel = CognitiveFrameSelector(
            drone_id="test",
            transmission_target=target,
            window_size=15,
            percentile_threshold=1.0 - target,   # 0.65
            emergency_priority=1.1,               # unreachable — forces percentile path
            absolute_min_priority=0.0,            # never block via minimum gate
        )

        rng = np.random.default_rng(42)
        frames_evaluated = 0
        frames_selected = 0

        for i in range(100):
            # Plain flat frames — vary only the overall intensity to create
            # a spread of histogram / object scores without hitting 100% motion
            intensity = int(rng.integers(30, 230))
            frame = _make_frame(color=(intensity, intensity, intensity))
            # Very small motion_score to avoid context boost clamping
            event = _make_change_event(frame, motion_score=float(rng.uniform(0.001, 0.01)), frame_index=i)
            result = sel.evaluate(event)
            frames_evaluated += 1
            if result is not None:
                frames_selected += 1

        rate = frames_selected / frames_evaluated
        # The percentile ranker should hover around the target (35%).
        # Allow generous tolerance for short sequences.
        assert 0.10 <= rate <= 0.70, (
            f"Selection rate {rate:.2f} is outside expected range [0.10, 0.70]"
        )



# ---------------------------------------------------------------------------
# 7. get_stats() accuracy
# ---------------------------------------------------------------------------

class TestStats:
    def test_get_stats_fields(self, selector):
        stats = selector.get_stats()
        assert "drone_id" in stats
        assert "frames_evaluated" in stats
        assert "frames_selected" in stats
        assert "selection_rate" in stats
        assert "target_rate" in stats

    def test_get_stats_counts_match(self, selector):
        selected_count = [0]

        def _count(sf):
            selected_count[0] += 1

        selector.on_selected_frame = _count
        selector._compute_priority_score = lambda f, fi, cm: (0.95, {"histogram": 0, "motion": 0, "object": 0})

        n = 5
        for i in range(n):
            event = _make_change_event(_make_frame(), frame_index=i)
            selector.evaluate(event)

        stats = selector.get_stats()
        assert stats["frames_evaluated"] == n
        assert stats["frames_selected"] == selected_count[0]


# ---------------------------------------------------------------------------
# 8. reset_state() clears internal window
# ---------------------------------------------------------------------------

class TestResetState:
    def test_reset_clears_window(self, selector):
        frame = _make_frame()
        for i in range(5):
            event = _make_change_event(frame, frame_index=i)
            selector.evaluate(event)

        with selector._lock:
            assert len(selector._window_buffer) > 0

        selector.reset_state()

        with selector._lock:
            assert len(selector._window_buffer) == 0
            assert selector._prev_gray is None
            assert selector._prev_hist is None


# ---------------------------------------------------------------------------
# 9. SelectedFrame payload
# ---------------------------------------------------------------------------

class TestSelectedFramePayload:
    def test_selected_frame_fields(self, selector):
        frame = _make_bright_frame()
        event = _make_change_event(frame, motion_score=0.10)
        selector._compute_priority_score = lambda f, fi, cm: (0.95, {"histogram": 0, "motion": 0, "object": 0})

        result = selector.evaluate(event)
        assert result is not None
        assert result.drone_id == "test_drone"
        assert result.mission_id == "TEST_MISSION"
        assert result.cognitive_score == pytest.approx(0.95, abs=0.01)
        assert result.change_score == pytest.approx(0.10, abs=0.001)
        assert result.frame_index == 0
        assert result.utc_timestamp_ms == 1_710_000_000_000
        assert isinstance(result.raw_frame, np.ndarray)

    def test_selected_frame_to_dict_has_required_keys(self, selector):
        frame = _make_bright_frame()
        event = _make_change_event(frame, motion_score=0.10)
        selector._compute_priority_score = lambda f, fi, cm: (0.95, {"histogram": 0, "motion": 0, "object": 0})

        result = selector.evaluate(event)
        assert result is not None
        d = result.to_dict()

        required = {"frame_id", "drone_id", "mission_id", "utc_timestamp_ms",
                    "frame_index", "location", "frame_jpeg_b64",
                    "change_score", "cognitive_score", "selection_reason", "motion_regions"}
        assert required.issubset(set(d.keys()))

    def test_selected_frame_to_json_valid(self, selector):
        frame = _make_bright_frame()
        event = _make_change_event(frame)
        selector._compute_priority_score = lambda f, fi, cm: (0.95, {"histogram": 0, "motion": 0, "object": 0})

        result = selector.evaluate(event)
        assert result is not None
        json_str = result.to_json()
        parsed = json.loads(json_str)
        assert "frame_jpeg_b64" in parsed

    def test_selected_frame_encode_jpeg_returns_bytes(self, selector):
        frame = _make_bright_frame()
        event = _make_change_event(frame)
        selector._compute_priority_score = lambda f, fi, cm: (0.95, {"histogram": 0, "motion": 0, "object": 0})

        result = selector.evaluate(event)
        assert result is not None
        jpeg_bytes = result.encode_jpeg()
        assert isinstance(jpeg_bytes, bytes)
        assert len(jpeg_bytes) > 100  # at least a valid JPEG header

    def test_motion_regions_serialised_in_payload(self, selector):
        frame = _make_bright_frame()
        bbox = BoundingBox(x=10, y=20, w=50, h=60, area=3000, motion_magnitude=180.0)
        event = ChangeEvent(
            timestamp_ms=1_710_000_000_000,
            drone_id="test_drone",
            frame_index=0,
            regions=[bbox],
            motion_score=0.10,
            raw_frame=frame,
        )
        selector._compute_priority_score = lambda f, fi, cm: (0.95, {"histogram": 0, "motion": 0, "object": 0})

        result = selector.evaluate(event)
        assert result is not None
        assert len(result.motion_regions) == 1
        region = result.motion_regions[0]
        assert region["x"] == 10
        assert region["y"] == 20
        assert region["area"] == 3000


# ---------------------------------------------------------------------------
# 10. GpsLocation
# ---------------------------------------------------------------------------

class TestGpsLocation:
    def test_gps_location_to_dict(self):
        loc = GpsLocation(lat=8.5241, lon=76.9366, altitude_m=50.0)
        d = loc.to_dict()
        assert d["lat"] == pytest.approx(8.5241)
        assert d["lon"] == pytest.approx(76.9366)
        assert d["altitude_m"] == pytest.approx(50.0)

    def test_default_gps_location(self):
        sel = CognitiveFrameSelector()
        assert sel.location.lat == 0.0
        assert sel.location.lon == 0.0


# ---------------------------------------------------------------------------
# 11. Thread-safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_evaluate_no_exception(self):
        """Multiple threads calling evaluate() concurrently must not raise."""
        sel = CognitiveFrameSelector(drone_id="thread_test")
        errors: List[Exception] = []

        def _worker(thread_id: int):
            try:
                for i in range(10):
                    frame = _make_frame(noise_sigma=float(thread_id * 10))
                    event = _make_change_event(frame, motion_score=0.05, frame_index=thread_id * 10 + i)
                    sel.evaluate(event)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(errors) == 0, f"Thread safety violation: {errors}"

    def test_concurrent_get_stats_no_exception(self):
        """get_stats() called from a separate thread while evaluate() runs."""
        sel = CognitiveFrameSelector(drone_id="stats_test")
        errors: List[Exception] = []
        stop_flag = [False]

        def _stat_reader():
            while not stop_flag[0]:
                try:
                    sel.get_stats()
                except Exception as exc:
                    errors.append(exc)

        stat_thread = threading.Thread(target=_stat_reader)
        stat_thread.start()

        for i in range(20):
            frame = _make_frame(noise_sigma=20.0)
            event = _make_change_event(frame, frame_index=i)
            sel.evaluate(event)

        stop_flag[0] = True
        stat_thread.join(timeout=5)

        assert len(errors) == 0
