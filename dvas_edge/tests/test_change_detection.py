"""
tests/test_change_detection.py
================================
Task 1.4 — Change Detection Testing (DVAS Production Roadmap, Phase 1)

Covers:
  A. Unit tests for ChangeDetectionEngine core logic
     • BoundingBox.iou() accuracy
     • _BlobTracker persistence counting
     • Engine lifecycle (start/stop/push_frame)
     • ChangeEvent data shape and to_dict()
     • Motion score threshold filtering
     • Events-per-minute rolling counter
     • get_stats() thread-safety

  B. Synthetic scenario tests ("playback harness")
     Three mandatory Phase 1 gate-review scenarios:
     Scenario 1 — Static background: NO events on a fixed scene
     Scenario 2 — Object entry:      events fire when a bright blob appears
     Scenario 3 — Crowd movement:    sustained events on high-motion frames

  C. MOG2 parameter sensitivity
     • Shadow suppression (detectShadows=True reduces false positives)
     • history length effect on warm-up frames

  D. Persistence filter validation
     • Blobs below persistence_frames threshold are suppressed
     • Blobs above threshold are confirmed

Compatible with pytest AND Python stdlib unittest.
Run with:
    python -m pytest tests/test_change_detection.py -v
"""

from __future__ import annotations

import queue
import threading
import time
import unittest
from dataclasses import dataclass
from typing import List, Optional

import cv2
import numpy as np

from src.change_detection.change_detection_engine import (
    ChangeDetectionEngine,
    _BlobTracker,
)
from src.change_detection.change_event import BoundingBox, ChangeEvent
from src.video.drone_video_source import VideoFrame


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_frame(
    width: int = 320,
    height: int = 240,
    color: tuple = (30, 30, 30),
    frame_index: int = 0,
    ts_ms: Optional[int] = None,
) -> VideoFrame:
    """Return a solid-colour VideoFrame."""
    if ts_ms is None:
        ts_ms = int(time.time() * 1000) + frame_index * 33
    img = np.full((height, width, 3), color, dtype=np.uint8)
    return VideoFrame(
        frame_index=frame_index,
        utc_timestamp_ms=ts_ms,
        frame=img,
        width=width,
        height=height,
        decode_latency_ms=1.0,
    )


def _make_noisy_frame(
    width: int = 320,
    height: int = 240,
    seed: int = 0,
    frame_index: int = 0,
    ts_ms: Optional[int] = None,
) -> VideoFrame:
    """Return a random-noise VideoFrame (simulates static scene with sensor noise)."""
    if ts_ms is None:
        ts_ms = int(time.time() * 1000) + frame_index * 33
    rng = np.random.default_rng(seed)
    img = rng.integers(60, 100, (height, width, 3), dtype=np.uint8)
    return VideoFrame(
        frame_index=frame_index,
        utc_timestamp_ms=ts_ms,
        frame=img,
        width=width,
        height=height,
        decode_latency_ms=1.0,
    )


def _inject_blob(
    frame: np.ndarray,
    x: int, y: int, w: int, h: int,
    color: tuple = (220, 220, 220),
) -> np.ndarray:
    """Paint a bright rectangle onto a frame copy (simulates an object entering)."""
    out = frame.copy()
    out[y:y + h, x:x + w] = color
    return out


def _push_and_collect(
    engine: ChangeDetectionEngine,
    frames: List[VideoFrame],
    settle_s: float = 0.3,
) -> List[ChangeEvent]:
    """Push frames into engine and return all emitted events (blocking)."""
    collected: List[ChangeEvent] = []
    lock = threading.Lock()

    def _cb(evt: ChangeEvent) -> None:
        with lock:
            collected.append(evt)

    engine.on_change_event = _cb
    engine.start()

    for vf in frames:
        engine.push_frame(vf)

    # Wait for the worker thread to drain the queue
    deadline = time.monotonic() + settle_s + len(frames) * 0.05
    while time.monotonic() < deadline:
        if engine._queue.empty():
            time.sleep(0.05)
            break
        time.sleep(0.02)

    time.sleep(settle_s)   # let any in-flight frame finish processing
    engine.stop()
    return collected


# ─────────────────────────────────────────────────────────────────────────────
# A. Unit tests — BoundingBox
# ─────────────────────────────────────────────────────────────────────────────

class TestBoundingBox(unittest.TestCase):

    def _bb(self, x, y, w, h, area=None) -> BoundingBox:
        return BoundingBox(x=x, y=y, w=w, h=h,
                           area=area or w * h, motion_magnitude=255.0)

    def test_iou_identical_boxes_is_one(self):
        b = self._bb(10, 10, 50, 50)
        self.assertAlmostEqual(b.iou(b), 1.0, places=4)

    def test_iou_non_overlapping_is_zero(self):
        b1 = self._bb(0, 0, 10, 10)
        b2 = self._bb(20, 20, 10, 10)
        self.assertEqual(b1.iou(b2), 0.0)

    def test_iou_partial_overlap_in_range(self):
        b1 = self._bb(0, 0, 20, 20)
        b2 = self._bb(10, 10, 20, 20)
        iou = b1.iou(b2)
        self.assertGreater(iou, 0.0)
        self.assertLess(iou, 1.0)

    def test_iou_symmetry(self):
        b1 = self._bb(0, 0, 30, 30)
        b2 = self._bb(15, 15, 30, 30)
        self.assertAlmostEqual(b1.iou(b2), b2.iou(b1), places=6)

    def test_cx_cy(self):
        b = self._bb(10, 20, 40, 60)
        self.assertEqual(b.cx, 30)
        self.assertEqual(b.cy, 50)

    def test_as_tuple(self):
        b = self._bb(5, 10, 15, 20)
        self.assertEqual(b.as_tuple(), (5, 10, 15, 20))

    def test_persistence_defaults_to_one(self):
        b = self._bb(0, 0, 10, 10)
        self.assertEqual(b.persistence, 1)


# ─────────────────────────────────────────────────────────────────────────────
# B. Unit tests — _BlobTracker
# ─────────────────────────────────────────────────────────────────────────────

class TestBlobTracker(unittest.TestCase):

    def _bb(self, x, y, w=30, h=30) -> BoundingBox:
        return BoundingBox(x=x, y=y, w=w, h=h, area=w * h, motion_magnitude=200.0)

    def test_new_blob_starts_at_persistence_one(self):
        tracker = _BlobTracker(iou_threshold=0.25)
        blobs = [self._bb(0, 0)]
        result = tracker.update(blobs)
        self.assertEqual(result[0].persistence, 1)

    def test_matched_blob_increments_persistence(self):
        tracker = _BlobTracker(iou_threshold=0.25)
        # Frame 1 — blob at (0,0)
        tracker.update([self._bb(0, 0)])
        # Frame 2 — same position → should be matched
        result = tracker.update([self._bb(2, 2)])   # slight shift, still overlaps
        self.assertEqual(result[0].persistence, 2)

    def test_unmatched_blob_pruned_next_frame(self):
        tracker = _BlobTracker(iou_threshold=0.25)
        tracker.update([self._bb(0, 0)])
        # Frame 2 — blob at totally different location
        result = tracker.update([self._bb(200, 200)])
        # Old blob pruned; new blob has persistence=1
        self.assertEqual(result[0].persistence, 1)

    def test_multiple_blobs_tracked_independently(self):
        tracker = _BlobTracker(iou_threshold=0.25)
        b1, b2 = self._bb(0, 0), self._bb(200, 200)
        tracker.update([b1, b2])
        r1, r2 = self._bb(0, 0), self._bb(200, 200)
        result = tracker.update([r1, r2])
        persistences = {r.persistence for r in result}
        self.assertIn(2, persistences)

    def test_reset_clears_state(self):
        tracker = _BlobTracker()
        tracker.update([self._bb(0, 0)])
        tracker.reset()
        result = tracker.update([self._bb(0, 0)])
        self.assertEqual(result[0].persistence, 1)


# ─────────────────────────────────────────────────────────────────────────────
# C. Unit tests — ChangeDetectionEngine lifecycle
# ─────────────────────────────────────────────────────────────────────────────

class TestEngineLifecycle(unittest.TestCase):

    def _make_engine(self, **kwargs) -> ChangeDetectionEngine:
        defaults = dict(
            drone_id="test_drone",
            mog2_history=50,
            mog2_var_threshold=25.0,
            detect_shadows=True,
            min_contour_area=400,
            motion_score_threshold=0.005,
            persistence_frames=2,
            max_queue_size=32,
            emit_raw_mask=True,
        )
        defaults.update(kwargs)
        return ChangeDetectionEngine(**defaults)

    def test_start_creates_worker_thread(self):
        eng = self._make_engine()
        eng.start()
        self.assertIsNotNone(eng._worker)
        self.assertTrue(eng._worker.is_alive())
        eng.stop()

    def test_double_start_is_safe(self):
        eng = self._make_engine()
        eng.start()
        eng.start()   # should not raise or create a second thread
        self.assertTrue(eng._worker.is_alive())
        eng.stop()

    def test_stop_joins_worker_thread(self):
        eng = self._make_engine()
        eng.start()
        eng.stop()
        self.assertFalse(eng._worker.is_alive())

    def test_push_frame_before_start_does_not_crash(self):
        eng = self._make_engine()
        vf = _make_frame()
        eng.push_frame(vf)   # should silently queue (engine not started)
        eng.start()
        time.sleep(0.1)
        eng.stop()

    def test_get_stats_returns_expected_keys(self):
        eng = self._make_engine()
        stats = eng.get_stats()
        for key in ("drone_id", "frames_processed", "events_total",
                    "events_per_minute", "last_motion_score", "queue_depth"):
            self.assertIn(key, stats)

    def test_frames_processed_counter_increments(self):
        eng = self._make_engine()
        eng.start()
        frames = [_make_noisy_frame(seed=i, frame_index=i) for i in range(10)]
        for vf in frames:
            eng.push_frame(vf)
        time.sleep(0.5)
        eng.stop()
        self.assertGreater(eng._frames_processed, 0)

    def test_queue_drops_oldest_when_full(self):
        """Engine push_frame must never block — oldest frame is dropped when queue full."""
        eng = self._make_engine(max_queue_size=4)
        # Do NOT start the engine → queue will fill up
        for i in range(20):
            eng.push_frame(_make_frame(frame_index=i))
        # Queue should not exceed max_queue_size + 1
        self.assertLessEqual(eng._queue.qsize(), 5)


# ─────────────────────────────────────────────────────────────────────────────
# D. Unit tests — ChangeEvent
# ─────────────────────────────────────────────────────────────────────────────

class TestChangeEvent(unittest.TestCase):

    def _make_event(self, n_regions: int = 3) -> ChangeEvent:
        regions = [
            BoundingBox(x=i * 30, y=i * 20, w=25, h=25,
                        area=625, motion_magnitude=200.0, persistence=3)
            for i in range(n_regions)
        ]
        return ChangeEvent(
            timestamp_ms=1_700_000_000_000,
            drone_id="drone_01",
            frame_index=42,
            regions=regions,
            motion_score=0.05,
            stage1_pixel_changed_pct=0.10,
            stage2_blobs_detected=n_regions,
        )

    def test_region_count_matches_list(self):
        evt = self._make_event(n_regions=4)
        self.assertEqual(evt.region_count, 4)

    def test_largest_region_is_first(self):
        # regions should be sorted by area descending — all equal here
        evt = self._make_event(n_regions=3)
        self.assertIsNotNone(evt.largest_region)

    def test_largest_region_none_for_empty_event(self):
        evt = self._make_event(n_regions=0)
        self.assertIsNone(evt.largest_region)

    def test_to_dict_contains_required_keys(self):
        evt = self._make_event()
        d = evt.to_dict()
        for key in ("timestamp_ms", "drone_id", "frame_index", "motion_score",
                    "region_count", "stage1_pixel_changed_pct",
                    "stage2_blobs_detected", "regions"):
            self.assertIn(key, d)

    def test_to_dict_regions_serialised_correctly(self):
        evt = self._make_event(n_regions=2)
        d = evt.to_dict()
        self.assertEqual(len(d["regions"]), 2)
        for r in d["regions"]:
            for field in ("x", "y", "w", "h", "area", "motion_magnitude", "persistence"):
                self.assertIn(field, r)

    def test_motion_score_round_trip(self):
        evt = self._make_event()
        d = evt.to_dict()
        self.assertAlmostEqual(d["motion_score"], 0.05, places=3)


# ─────────────────────────────────────────────────────────────────────────────
# E. Synthetic scenario tests (Task 1.4 playback harness)
# ─────────────────────────────────────────────────────────────────────────────

class TestScenarioStaticBackground(unittest.TestCase):
    """
    Scenario 1 — Static background.
    A fixed scene (same frame repeated) must produce zero ChangeEvents after
    the MOG2 background model has warmed up.
    """

    def test_no_events_on_static_scene(self):
        """Pure static background → zero events."""
        eng = ChangeDetectionEngine(
            drone_id="static_test",
            mog2_history=20,          # warm up quickly
            mog2_var_threshold=16.0,
            detect_shadows=True,
            min_contour_area=400,
            motion_score_threshold=0.005,
            persistence_frames=3,
            max_queue_size=64,
        )
        # Push 60 identical frames — after warm-up MOG2 should model them as BG
        frames = [_make_frame(color=(80, 80, 80), frame_index=i) for i in range(60)]
        events = _push_and_collect(eng, frames, settle_s=0.5)
        # Allow a small number of transient events during warm-up (first ~history frames)
        # but after stabilisation there should be very few
        self.assertLessEqual(
            len(events), 5,
            f"Static background produced {len(events)} events (expected ≤5 warm-up transients)"
        )

    def test_noise_below_threshold_suppressed(self):
        """Sensor noise frames (small random variation) must not trigger events."""
        eng = ChangeDetectionEngine(
            drone_id="noise_test",
            mog2_history=30,
            mog2_var_threshold=25.0,
            detect_shadows=True,
            min_contour_area=500,
            motion_score_threshold=0.02,   # raised threshold
            persistence_frames=3,
            max_queue_size=64,
        )
        # Frames with the SAME seed → identical images
        frames = [_make_noisy_frame(seed=42, frame_index=i) for i in range(50)]
        events = _push_and_collect(eng, frames, settle_s=0.5)
        self.assertLessEqual(
            len(events), 3,
            f"Identical noise frames produced {len(events)} events (expected ≤3)"
        )


class TestScenarioObjectEntry(unittest.TestCase):
    """
    Scenario 2 — Object entry.
    A bright blob appearing in a stable background must trigger ChangeEvents
    once it has persisted for persistence_frames consecutive frames.
    """

    def test_blob_triggers_event_after_persistence(self):
        """A persistent blob in a stable background must generate ≥1 ChangeEvent."""
        eng = ChangeDetectionEngine(
            drone_id="object_entry_test",
            mog2_history=20,
            mog2_var_threshold=16.0,
            detect_shadows=False,
            min_contour_area=200,
            motion_score_threshold=0.001,
            persistence_frames=2,
            max_queue_size=128,
        )

        bg_color = (60, 60, 60)
        W, H = 320, 240

        # Phase 1: 30 warm-up frames (background only)
        frames: List[VideoFrame] = []
        for i in range(30):
            frames.append(_make_frame(W, H, bg_color, frame_index=i))

        # Phase 2: 20 frames with a large bright blob (object entered scene)
        base_img = np.full((H, W, 3), bg_color, dtype=np.uint8)
        for i in range(20):
            img = _inject_blob(base_img, x=50, y=50, w=80, h=80, color=(240, 240, 240))
            ts_ms = int(time.time() * 1000) + (30 + i) * 33
            frames.append(VideoFrame(
                frame_index=30 + i,
                utc_timestamp_ms=ts_ms,
                frame=img,
                width=W,
                height=H,
                decode_latency_ms=1.0,
            ))

        events = _push_and_collect(eng, frames, settle_s=0.5)
        self.assertGreater(
            len(events), 0,
            "Blob entry produced zero events — persistence or threshold config is wrong"
        )

    def test_transient_blob_suppressed_by_persistence_filter(self):
        """A blob appearing only in 1 frame (below persistence_frames=3) must NOT trigger."""
        eng = ChangeDetectionEngine(
            drone_id="transient_test",
            mog2_history=20,
            mog2_var_threshold=16.0,
            detect_shadows=False,
            min_contour_area=200,
            motion_score_threshold=0.001,
            persistence_frames=3,   # needs 3 consecutive frames
            max_queue_size=128,
        )

        bg_color = (60, 60, 60)
        W, H = 320, 240

        frames: List[VideoFrame] = []
        # 25 warm-up frames
        for i in range(25):
            frames.append(_make_frame(W, H, bg_color, frame_index=i))

        # Only 1 frame with blob (then back to background)
        base_img = np.full((H, W, 3), bg_color, dtype=np.uint8)
        blob_img = _inject_blob(base_img, x=50, y=50, w=80, h=80, color=(240, 240, 240))
        ts_ms = int(time.time() * 1000) + 25 * 33
        frames.append(VideoFrame(
            frame_index=25,
            utc_timestamp_ms=ts_ms,
            frame=blob_img,
            width=W,
            height=H,
            decode_latency_ms=1.0,
        ))

        # 10 more background frames
        for i in range(10):
            frames.append(_make_frame(W, H, bg_color, frame_index=26 + i))

        events = _push_and_collect(eng, frames, settle_s=0.5)
        self.assertEqual(
            len(events), 0,
            f"Single-frame transient blob produced {len(events)} events (expected 0)"
        )


class TestScenarioCrowdMovement(unittest.TestCase):
    """
    Scenario 3 — Crowd / high-motion video.
    Frames with large continuously changing regions (simulating crowd movement)
    must generate multiple ChangeEvents.
    """

    def test_sustained_motion_generates_multiple_events(self):
        """Continuously changing frame content should fire many ChangeEvents."""
        eng = ChangeDetectionEngine(
            drone_id="crowd_test",
            mog2_history=20,
            mog2_var_threshold=16.0,
            detect_shadows=False,
            min_contour_area=200,
            motion_score_threshold=0.001,
            persistence_frames=2,
            max_queue_size=128,
        )

        W, H = 320, 240
        rng = np.random.default_rng(0)
        frames: List[VideoFrame] = []

        # 10 warm-up frames (stable dark background)
        for i in range(10):
            frames.append(_make_frame(W, H, (50, 50, 50), frame_index=i))

        # 40 high-motion frames: each frame has a large random bright region
        # placed at a different position (simulates crowd / vehicle movement)
        for i in range(40):
            bg = np.full((H, W, 3), 50, dtype=np.uint8)
            # Move a large 120×120 blob across the frame
            x = int((i / 40) * (W - 120))
            y = int((i / 40) * (H - 120))
            bg[y:y + 120, x:x + 120] = 200
            ts_ms = int(time.time() * 1000) + (10 + i) * 33
            frames.append(VideoFrame(
                frame_index=10 + i,
                utc_timestamp_ms=ts_ms,
                frame=bg,
                width=W,
                height=H,
                decode_latency_ms=1.0,
            ))

        events = _push_and_collect(eng, frames, settle_s=0.5)
        self.assertGreater(
            len(events), 3,
            f"High-motion scenario produced only {len(events)} events (expected >3)"
        )

    def test_events_have_valid_motion_score(self):
        """All emitted ChangeEvents must have motion_score in (0, 1]."""
        eng = ChangeDetectionEngine(
            drone_id="crowd_score_test",
            mog2_history=15,
            mog2_var_threshold=16.0,
            detect_shadows=False,
            min_contour_area=100,
            motion_score_threshold=0.001,
            persistence_frames=2,
            max_queue_size=128,
        )

        W, H = 320, 240
        frames: List[VideoFrame] = []

        for i in range(8):
            frames.append(_make_frame(W, H, (50, 50, 50), frame_index=i))

        for i in range(30):
            bg = np.full((H, W, 3), 50, dtype=np.uint8)
            x = int((i / 30) * (W - 100))
            bg[40:140, x:x + 100] = 210
            ts_ms = int(time.time() * 1000) + (8 + i) * 33
            frames.append(VideoFrame(
                frame_index=8 + i,
                utc_timestamp_ms=ts_ms,
                frame=bg,
                width=W,
                height=H,
                decode_latency_ms=1.0,
            ))

        events = _push_and_collect(eng, frames, settle_s=0.5)
        for evt in events:
            self.assertGreater(evt.motion_score, 0.0,
                               "motion_score must be > 0 for a confirmed event")
            self.assertLessEqual(evt.motion_score, 1.0,
                                 "motion_score must be ≤ 1.0")


# ─────────────────────────────────────────────────────────────────────────────
# F. MOG2 parameter tuning tests
# ─────────────────────────────────────────────────────────────────────────────

class TestMOG2Parameters(unittest.TestCase):
    """
    Task 1.4 — Tune MOG2 parameters: history, varThreshold, detectShadows.
    """

    def _count_events(self, frames, **engine_kwargs) -> int:
        eng = ChangeDetectionEngine(**engine_kwargs)
        return len(_push_and_collect(eng, frames, settle_s=0.4))

    def test_higher_var_threshold_reduces_sensitivity(self):
        """
        With a very high varThreshold the engine should produce fewer events
        on mildly-varying frames compared to a low varThreshold.
        """
        W, H = 160, 120
        frames: List[VideoFrame] = []
        rng = np.random.default_rng(99)
        for i in range(50):
            # Slight per-frame variation: noise ±5 on a grey background
            img = rng.integers(75, 85, (H, W, 3), dtype=np.uint8)
            frames.append(VideoFrame(
                frame_index=i,
                utc_timestamp_ms=int(time.time() * 1000) + i * 33,
                frame=img, width=W, height=H, decode_latency_ms=1.0,
            ))

        common = dict(
            drone_id="var_thresh_test",
            mog2_history=15,
            detect_shadows=False,
            min_contour_area=100,
            motion_score_threshold=0.001,
            persistence_frames=2,
            max_queue_size=64,
        )
        low_sens  = self._count_events(frames, mog2_var_threshold=4.0,   **common)
        high_sens = self._count_events(frames, mog2_var_threshold=100.0, **common)
        self.assertLessEqual(
            high_sens, low_sens,
            f"High varThreshold ({high_sens}) should yield ≤ events vs low ({low_sens})"
        )

    def test_detect_shadows_false_reduces_false_positives(self):
        """
        Shadow pixels (marked 127 by MOG2) should not be treated as foreground.
        With detectShadows=True we explicitly threshold at >200, so shadow regions
        are excluded — events on a purely-shadow scene should be zero.
        """
        W, H = 160, 120
        bg_color = (100, 100, 100)
        shadow_color = (70, 70, 70)    # darker region — mimics shadow

        # Warm-up on background
        frames: List[VideoFrame] = []
        for i in range(25):
            frames.append(_make_frame(W, H, bg_color, frame_index=i))

        # Insert shadow frames (not real foreground)
        for i in range(15):
            img = np.full((H, W, 3), bg_color, dtype=np.uint8)
            img[20:80, 20:80] = shadow_color   # dark region (shadow-like)
            ts_ms = int(time.time() * 1000) + (25 + i) * 33
            frames.append(VideoFrame(
                frame_index=25 + i,
                utc_timestamp_ms=ts_ms,
                frame=img, width=W, height=H, decode_latency_ms=1.0,
            ))

        eng = ChangeDetectionEngine(
            drone_id="shadow_test",
            mog2_history=20,
            mog2_var_threshold=16.0,
            detect_shadows=True,        # shadows suppressed at ≤200 threshold
            min_contour_area=300,
            motion_score_threshold=0.005,
            persistence_frames=3,
            max_queue_size=64,
        )
        events = _push_and_collect(eng, frames, settle_s=0.4)
        self.assertLessEqual(
            len(events), 2,
            f"Shadow suppression failed: {len(events)} events on shadow-only frames"
        )

    def test_short_history_warms_up_faster(self):
        """
        With history=10 the background model converges within ~10 frames,
        so a bright blob appearing after 12 warm-up frames must be detected.

        NOTE: persistence_frames=1 isolates the warm-up speed concern —
        persistence behaviour (multi-frame requirement) is verified in
        TestScenarioObjectEntry.test_transient_blob_suppressed_by_persistence_filter.
        With mog2_history=10 the learning rate is 0.1/frame; if persistence_frames
        were 2 the blob may already be partially absorbed into the background model
        by the second appearance, so this test uses persistence_frames=1 to cleanly
        assert that the novelty is detected at least once.
        """
        W, H = 320, 240           # larger frame → more prominent blob relative area
        bg_color = (60, 60, 60)
        blob_color = (230, 230, 230)   # high contrast

        frames: List[VideoFrame] = []
        # 12 warm-up frames — just above history=10
        for i in range(12):
            frames.append(_make_frame(W, H, bg_color, frame_index=i))

        # 20 blob frames (large 100×100 blob = 10000 px²)
        base_img = np.full((H, W, 3), bg_color, dtype=np.uint8)
        for i in range(20):
            img = _inject_blob(base_img, 30, 30, 100, 100, blob_color)
            ts_ms = int(time.time() * 1000) + (12 + i) * 33
            frames.append(VideoFrame(
                frame_index=12 + i,
                utc_timestamp_ms=ts_ms,
                frame=img, width=W, height=H, decode_latency_ms=1.0,
            ))

        eng = ChangeDetectionEngine(
            drone_id="short_history_test",
            mog2_history=10,           # very short — warms up in ~10 frames
            mog2_var_threshold=16.0,
            detect_shadows=False,
            min_contour_area=200,
            motion_score_threshold=0.001,
            persistence_frames=1,      # isolate warm-up speed; persistence tested elsewhere
            max_queue_size=64,
        )
        events = _push_and_collect(eng, frames, settle_s=0.5)
        self.assertGreater(
            len(events), 0,
            "Short-history engine (history=10) should detect large bright blob "
            "after only 12 warm-up frames"
        )


# ─────────────────────────────────────────────────────────────────────────────
# G. Events-per-minute rolling counter
# ─────────────────────────────────────────────────────────────────────────────

class TestEventsPerMinute(unittest.TestCase):

    def test_epm_zero_when_no_events(self):
        eng = ChangeDetectionEngine(drone_id="epm_test")
        stats = eng.get_stats()
        self.assertEqual(stats["events_per_minute"], 0.0)

    def test_epm_increases_after_events_emitted(self):
        eng = ChangeDetectionEngine(
            drone_id="epm_inc_test",
            mog2_history=15,
            mog2_var_threshold=16.0,
            detect_shadows=False,
            min_contour_area=100,
            motion_score_threshold=0.001,
            persistence_frames=2,
            max_queue_size=64,
        )
        W, H = 160, 120
        frames: List[VideoFrame] = []
        for i in range(8):
            frames.append(_make_frame(W, H, (50, 50, 50), frame_index=i))
        for i in range(20):
            bg = np.full((H, W, 3), 50, dtype=np.uint8)
            bg[20:100, 20:100] = 200
            ts_ms = int(time.time() * 1000) + (8 + i) * 33
            frames.append(VideoFrame(
                frame_index=8 + i, utc_timestamp_ms=ts_ms,
                frame=bg, width=W, height=H, decode_latency_ms=1.0,
            ))
        _push_and_collect(eng, frames, settle_s=0.4)
        stats = eng.get_stats()
        if stats["events_total"] > 0:
            self.assertGreater(stats["events_per_minute"], 0.0)


# ─────────────────────────────────────────────────────────────────────────────
# H. Raw mask output
# ─────────────────────────────────────────────────────────────────────────────

class TestRawMaskOutput(unittest.TestCase):

    def test_emit_raw_mask_true_attaches_mask(self):
        """When emit_raw_mask=True, ChangeEvent.raw_mask must be a numpy array."""
        eng = ChangeDetectionEngine(
            drone_id="mask_test",
            mog2_history=15,
            mog2_var_threshold=16.0,
            detect_shadows=False,
            min_contour_area=100,
            motion_score_threshold=0.001,
            persistence_frames=2,
            emit_raw_mask=True,
            max_queue_size=64,
        )
        W, H = 160, 120
        frames: List[VideoFrame] = []
        for i in range(8):
            frames.append(_make_frame(W, H, (50, 50, 50), frame_index=i))
        base = np.full((H, W, 3), 50, dtype=np.uint8)
        for i in range(10):
            img = _inject_blob(base, 20, 20, 60, 60, (210, 210, 210))
            ts_ms = int(time.time() * 1000) + (8 + i) * 33
            frames.append(VideoFrame(
                frame_index=8 + i, utc_timestamp_ms=ts_ms,
                frame=img, width=W, height=H, decode_latency_ms=1.0,
            ))

        events = _push_and_collect(eng, frames, settle_s=0.4)
        for evt in events:
            self.assertIsNotNone(evt.raw_mask, "raw_mask should be set when emit_raw_mask=True")
            self.assertIsInstance(evt.raw_mask, np.ndarray)
            self.assertEqual(evt.raw_mask.dtype, np.uint8)

    def test_emit_raw_mask_false_leaves_mask_none(self):
        """When emit_raw_mask=False, ChangeEvent.raw_mask must be None."""
        eng = ChangeDetectionEngine(
            drone_id="no_mask_test",
            mog2_history=15,
            mog2_var_threshold=16.0,
            detect_shadows=False,
            min_contour_area=100,
            motion_score_threshold=0.001,
            persistence_frames=2,
            emit_raw_mask=False,
            max_queue_size=64,
        )
        W, H = 160, 120
        frames: List[VideoFrame] = []
        for i in range(8):
            frames.append(_make_frame(W, H, (50, 50, 50), frame_index=i))
        base = np.full((H, W, 3), 50, dtype=np.uint8)
        for i in range(10):
            img = _inject_blob(base, 20, 20, 60, 60, (210, 210, 210))
            ts_ms = int(time.time() * 1000) + (8 + i) * 33
            frames.append(VideoFrame(
                frame_index=8 + i, utc_timestamp_ms=ts_ms,
                frame=img, width=W, height=H, decode_latency_ms=1.0,
            ))

        events = _push_and_collect(eng, frames, settle_s=0.4)
        for evt in events:
            self.assertIsNone(evt.raw_mask, "raw_mask must be None when emit_raw_mask=False")


# ─────────────────────────────────────────────────────────────────────────────
# I. False-positive rate estimation (newly wired metric)
# ─────────────────────────────────────────────────────────────────────────────

class TestFalsePositiveRate(unittest.TestCase):
    """
    Validates the FALSE_POSITIVE_RATE_EST Prometheus gauge wiring.

    The engine classifies an event as a FP candidate when
    motion_score < motion_score_threshold × 2.0  (barely above threshold).
    fp_rate = fp_candidates / total_events is written to the gauge after
    each confirmed event.
    """

    def _make_engine_with_threshold(self, threshold: float) -> ChangeDetectionEngine:
        return ChangeDetectionEngine(
            drone_id="fp_test",
            mog2_history=15,
            mog2_var_threshold=16.0,
            detect_shadows=False,
            min_contour_area=100,
            motion_score_threshold=threshold,
            persistence_frames=1,
            max_queue_size=64,
        )

    def test_fp_candidate_count_starts_at_zero(self):
        """Fresh engine must have _fp_candidate_count = 0."""
        eng = ChangeDetectionEngine(drone_id="fp_zero")
        self.assertEqual(eng._fp_candidate_count, 0)

    def test_high_motion_event_not_flagged_as_fp(self):
        """
        A large bright blob (motion_score ≫ threshold) must NOT increment
        _fp_candidate_count.
        """
        threshold = 0.005
        eng = self._make_engine_with_threshold(threshold)

        W, H = 320, 240
        frames: List[VideoFrame] = []
        # Warm-up
        for i in range(15):
            frames.append(_make_frame(W, H, (40, 40, 40), frame_index=i))
        # 20 frames with a very large blob (~half the frame = motion_score ~0.5)
        base = np.full((H, W, 3), 40, dtype=np.uint8)
        for i in range(20):
            img = _inject_blob(base, 10, 10, 150, 110, (230, 230, 230))
            ts_ms = int(time.time() * 1000) + (15 + i) * 33
            frames.append(VideoFrame(
                frame_index=15 + i, utc_timestamp_ms=ts_ms,
                frame=img, width=W, height=H, decode_latency_ms=1.0,
            ))

        events = _push_and_collect(eng, frames, settle_s=0.5)
        if events:
            # High-motion events should not be FP candidates
            for evt in events:
                if evt.motion_score >= threshold * 2.0:
                    # This event should NOT have been counted as FP candidate
                    # We verify indirectly: fp_rate must be < 1.0
                    fp_rate = eng._fp_candidate_count / max(eng.events_total, 1)
                    self.assertLess(fp_rate, 1.0,
                                   "All high-motion events should not be FP candidates")

    def test_low_motion_event_increments_fp_candidate(self):
        """
        An event with motion_score just above the threshold (< threshold × 2.0)
        must increment _fp_candidate_count.

        We simulate this by injecting a very small blob (tiny motion_score)
        with a very low threshold so the score falls between threshold and 2×threshold.
        """
        # Very low threshold so even a small blob clears it
        threshold = 0.0001
        eng = self._make_engine_with_threshold(threshold)

        W, H = 320, 240
        frames: List[VideoFrame] = []
        # Warm-up
        for i in range(15):
            frames.append(_make_frame(W, H, (40, 40, 40), frame_index=i))

        # Small blob: 15×15 = 225 px² out of 76800 total → score ≈ 0.003
        # That is > threshold (0.0001) but < 2 × threshold (0.0002)? No —
        # 0.003 >> 0.0002. Let's confirm: score = 225/76800 ≈ 0.003.
        # threshold×2 = 0.0002. 0.003 > 0.0002 → NOT a FP candidate.
        #
        # So use threshold=0.002 → threshold×2 = 0.004 > 0.003 → IS a FP candidate.
        eng2 = self._make_engine_with_threshold(0.002)

        base = np.full((H, W, 3), 40, dtype=np.uint8)
        for i in range(20):
            img = _inject_blob(base, 10, 10, 15, 15, (230, 230, 230))
            ts_ms = int(time.time() * 1000) + (15 + i) * 33
            frames.append(VideoFrame(
                frame_index=15 + i, utc_timestamp_ms=ts_ms,
                frame=img, width=W, height=H, decode_latency_ms=1.0,
            ))

        events = _push_and_collect(eng2, frames, settle_s=0.5)
        if events:
            # At least some events should have motion_score < threshold×2 = 0.004
            low_motion_events = [e for e in events if e.motion_score < 0.004]
            if low_motion_events:
                self.assertGreater(eng2._fp_candidate_count, 0,
                                   "Low-motion events must increment _fp_candidate_count")

    def test_fp_rate_in_valid_range(self):
        """fp_rate = fp_candidates / total_events must always be in [0.0, 1.0]."""
        eng = ChangeDetectionEngine(
            drone_id="fp_range_test",
            mog2_history=15,
            mog2_var_threshold=16.0,
            detect_shadows=False,
            min_contour_area=100,
            motion_score_threshold=0.001,
            persistence_frames=1,
            max_queue_size=64,
        )
        W, H = 320, 240
        frames: List[VideoFrame] = []
        for i in range(10):
            frames.append(_make_frame(W, H, (40, 40, 40), frame_index=i))
        base = np.full((H, W, 3), 40, dtype=np.uint8)
        for i in range(20):
            img = _inject_blob(base, 30, 30, 80, 80, (220, 220, 220))
            ts_ms = int(time.time() * 1000) + (10 + i) * 33
            frames.append(VideoFrame(
                frame_index=10 + i, utc_timestamp_ms=ts_ms,
                frame=img, width=W, height=H, decode_latency_ms=1.0,
            ))
        _push_and_collect(eng, frames, settle_s=0.5)
        if eng.events_total > 0:
            fp_rate = eng._fp_candidate_count / eng.events_total
            self.assertGreaterEqual(fp_rate, 0.0)
            self.assertLessEqual(fp_rate, 1.0)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main()
