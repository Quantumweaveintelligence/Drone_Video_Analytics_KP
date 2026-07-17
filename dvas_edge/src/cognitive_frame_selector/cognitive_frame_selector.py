"""
src/cognitive_frame_selector/cognitive_frame_selector.py
=========================================================
Task 1.5 — Cognitive Frame Selector (DVAS Production Roadmap, Phase 1, Week 8)

This is the intelligent filter that sits downstream of the ChangeDetectionEngine.

  ChangeDetectionEngine answers: "Did anything change in this frame?"
  CognitiveFrameSelector answers: "Of all frames that changed, which ones
                                   are worth packaging for GPU analysis?"

Scoring pipeline (per candidate frame)
---------------------------------------
  Score 1 — Histogram Score:
      Grayscale histogram correlation vs. previous processed frame.
      High difference → new scene information.
      Weight: 20%

  Score 2 — Motion Quality Score:
      Multi-threshold frame differencing in the changed regions.
      Distinguishes real motion from lighting flicker.
      Weight: 40%

  Score 3 — Object Presence Score:
      Composite of bright-object density, edge density, and texture
      variance. Detects vehicles, people, and complex scenes.
      Weight: 30%

  Score 4 — Context Boost:
      Temporal diversity boost (if nothing selected recently) and a
      motion-score-proportional event boost (high motion_score from
      ChangeDetectionEngine → treat as an event zone).
      Weight multiplier: 10% of base score

Selection decision
------------------
  1. Emergency override: priority >= emergency_priority → always select.
  2. Minimum gate:       priority <  absolute_min_priority → never select.
  3. Percentile ranking: maintain a sliding window of recent priorities;
                         select if current frame is in the top
                         (1 - percentile_threshold) fraction.
     Early frames (window < 5): simple threshold > 0.2.

Thread safety
-------------
All internal state mutations are protected by a threading.Lock so that
push_frame() and get_stats() can be called from different threads safely.

Prometheus metrics
------------------
  cognitive_frames_selected_total     Counter
  cognitive_frames_evaluated_total    Counter
  cognitive_selection_rate            Gauge  (selected / evaluated)
  cognitive_selector_latency_ms       Histogram
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

from src.change_detection.change_event import ChangeEvent
from src.cognitive_frame_selector.selected_frame import GpsLocation, SelectedFrame
from src.metrics.prometheus_metrics import (
    COGNITIVE_FRAMES_EVALUATED_TOTAL,
    COGNITIVE_FRAMES_SELECTED_TOTAL,
    COGNITIVE_SELECTION_RATE,
    COGNITIVE_SELECTOR_LATENCY,
)
from src.utils.logger import get_logger

log = get_logger(__name__)


class CognitiveFrameSelector:
    """
    Priority-based frame selector that filters ChangeEvents down to a
    configurable target extraction rate for transmission.

    Parameters
    ----------
    drone_id : str
        Used for logging and Prometheus labels.
    mission_id : str
        Active mission identifier embedded in selected-frame payloads.
    transmission_target : float
        Fraction of candidate frames to extract (default 0.35 = 35%).
    window_size : int
        Number of recent frames kept in the sliding window for percentile
        ranking (default 15).
    percentile_threshold : float
        A frame is selected if its priority is at or above this percentile
        within the sliding window.  Set to (1 - transmission_target).
    absolute_min_priority : float
        Frames below this priority are never selected regardless of the window.
    emergency_priority : float
        Frames at or above this priority are always selected immediately.
    vehicle_boost : float
        Context boost multiplier when the ChangeEvent motion score indicates
        a vehicle-like high-motion event.
    scene_change_boost : float
        Context boost for sudden histogram change (scene cut).
    crowd_boost : float
        Context boost when the object score is high (many edges/textures).
    weights_histogram : float
        Weight of the histogram-difference signal.
    weights_motion : float
        Weight of the motion-density signal.
    weights_object : float
        Weight of the object-presence signal.
    weights_context : float
        Additional context weight applied via boost multiplier.
    histogram_bins : int
        Number of grayscale histogram bins (default 16).
    motion_threshold_low/med/high : int
        Pixel-difference thresholds for the three-level motion score.
    object_brightness_threshold : int
        Grayscale threshold for bright-object detection.
    edge_threshold_low/high : int
        Canny edge thresholds for the structural-object score.
    on_selected_frame : callable, optional
        Callback invoked (in the caller's thread) for every selected frame.
        Signature: ``on_selected_frame(frame: SelectedFrame) -> None``.
    jpeg_quality : int
        JPEG quality for frame encoding in SelectedFrame (1–100).
    location : GpsLocation, optional
        Default GPS location to embed in selected frames. In a live
        mission this would come from the drone's telemetry.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(
        self,
        drone_id: str = "drone_01",
        mission_id: str = "MISSION_DEFAULT",
        transmission_target: float = 0.35,
        window_size: int = 15,
        percentile_threshold: float = 0.65,
        absolute_min_priority: float = 0.05,
        emergency_priority: float = 0.60,
        vehicle_boost: float = 0.30,
        scene_change_boost: float = 0.30,
        crowd_boost: float = 0.30,
        weights_histogram: float = 0.20,
        weights_motion: float = 0.40,
        weights_object: float = 0.30,
        weights_context: float = 0.10,
        histogram_bins: int = 16,
        motion_threshold_low: int = 10,
        motion_threshold_med: int = 20,
        motion_threshold_high: int = 30,
        object_brightness_threshold: int = 150,
        edge_threshold_low: int = 30,
        edge_threshold_high: int = 100,
        on_selected_frame: Optional[Callable[[SelectedFrame], None]] = None,
        jpeg_quality: int = 85,
        location: Optional[GpsLocation] = None,
    ) -> None:
        self.drone_id = drone_id
        self.mission_id = mission_id
        self.transmission_target = transmission_target
        self.window_size = window_size
        self.percentile_threshold = percentile_threshold
        self.absolute_min_priority = absolute_min_priority
        self.emergency_priority = emergency_priority
        self.vehicle_boost = vehicle_boost
        self.scene_change_boost = scene_change_boost
        self.crowd_boost = crowd_boost
        self.jpeg_quality = jpeg_quality
        self.on_selected_frame = on_selected_frame
        self.location = location or GpsLocation()

        # Signal weights
        self._weights: Dict[str, float] = {
            "histogram": weights_histogram,
            "motion": weights_motion,
            "object": weights_object,
            "context": weights_context,
        }

        # Feature detection parameters
        self._histogram_bins = histogram_bins
        self._motion_threshold_low = motion_threshold_low
        self._motion_threshold_med = motion_threshold_med
        self._motion_threshold_high = motion_threshold_high
        self._object_brightness_threshold = object_brightness_threshold
        self._edge_threshold_low = edge_threshold_low
        self._edge_threshold_high = edge_threshold_high

        # ── Internal state (protected by lock) ────────────────────────────
        self._lock = threading.Lock()

        # Previous-frame state for differential signals
        self._prev_gray: Optional[np.ndarray] = None
        self._prev_hist: Optional[np.ndarray] = None

        # Sliding window of (priority, frame_index) tuples
        self._window_buffer: List[Tuple[float, int]] = []

        # Counters
        self._frames_evaluated: int = 0
        self._frames_selected: int = 0

        # Recent selection log (used for temporal diversity boost)
        self._recent_selections: List[float] = []   # monotonic timestamps
        self._recent_window_s: float = 10.0

        log.info(
            "cognitive_frame_selector_created",
            drone_id=self.drone_id,
            mission_id=self.mission_id,
            transmission_target=self.transmission_target,
            window_size=self.window_size,
            emergency_priority=self.emergency_priority,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(self, event: ChangeEvent) -> Optional[SelectedFrame]:
        """
        Evaluate a ChangeEvent and decide whether its frame should be
        selected for transmission.

        Parameters
        ----------
        event : ChangeEvent
            A confirmed ChangeEvent from the ChangeDetectionEngine.
            The event must carry ``raw_frame`` (set ``emit_raw_frame=True``
            in ChangeDetectionEngine).  If ``raw_frame`` is None, the frame
            is skipped and a warning is logged.

        Returns
        -------
        SelectedFrame or None
            Returns a SelectedFrame if the frame is selected; None otherwise.
            Also calls ``on_selected_frame`` callback if provided.
        """
        if event.raw_frame is None:
            log.warning(
                "cognitive_selector_skipped_no_raw_frame",
                drone_id=self.drone_id,
                frame_index=event.frame_index,
                hint="Set emit_raw_frame=True in ChangeDetectionEngine.",
            )
            return None

        t0 = time.monotonic()

        with self._lock:
            priority, signals = self._compute_priority_score(
                event.raw_frame,
                event.frame_index,
                event.motion_score,
            )
            selected, decision = self._should_select_frame(priority, event.frame_index)
            self._frames_evaluated += 1

        latency_ms = (time.monotonic() - t0) * 1_000.0
        COGNITIVE_SELECTOR_LATENCY.labels(drone_id=self.drone_id).observe(latency_ms)
        COGNITIVE_FRAMES_EVALUATED_TOTAL.labels(drone_id=self.drone_id).inc()

        if selected:
            with self._lock:
                self._frames_selected += 1
                self._recent_selections.append(time.monotonic())
                # Prune old entries
                cutoff = time.monotonic() - self._recent_window_s
                self._recent_selections = [
                    t for t in self._recent_selections if t >= cutoff
                ]

            COGNITIVE_FRAMES_SELECTED_TOTAL.labels(drone_id=self.drone_id).inc()
            with self._lock:
                rate = self._frames_selected / max(self._frames_evaluated, 1)
            COGNITIVE_SELECTION_RATE.labels(drone_id=self.drone_id).set(rate)

            # Serialise motion regions from the ChangeEvent
            motion_regions = [
                {
                    "x": r.x,
                    "y": r.y,
                    "w": r.w,
                    "h": r.h,
                    "area": r.area,
                    "motion_magnitude": round(r.motion_magnitude, 2),
                    "persistence": r.persistence,
                }
                for r in event.regions
            ]

            sf = SelectedFrame.create(
                drone_id=self.drone_id,
                mission_id=self.mission_id,
                utc_timestamp_ms=event.timestamp_ms,
                frame_index=event.frame_index,
                raw_frame=event.raw_frame,
                change_score=round(event.motion_score, 4),
                cognitive_score=round(priority, 4),
                selection_reason=decision["reason"],
                location=self.location,
                motion_regions=motion_regions,
                jpeg_quality=self.jpeg_quality,
            )

            log.info(
                "cognitive_frame_selected",
                drone_id=self.drone_id,
                frame_index=event.frame_index,
                cognitive_score=round(priority, 4),
                change_score=round(event.motion_score, 4),
                reason=decision["reason"],
                selection_rate=round(decision["current_rate"], 3),
                latency_ms=round(latency_ms, 2),
                histogram=round(signals["histogram"], 4),
                motion=round(signals["motion"], 4),
                object=round(signals["object"], 4),
            )

            if self.on_selected_frame is not None:
                try:
                    self.on_selected_frame(sf)
                except Exception as exc:
                    log.error(
                        "on_selected_frame_callback_error",
                        drone_id=self.drone_id,
                        error=str(exc),
                    )

            return sf

        else:
            log.debug(
                "cognitive_frame_rejected",
                drone_id=self.drone_id,
                frame_index=event.frame_index,
                priority=round(priority, 4),
                reason=decision["reason"],
                latency_ms=round(latency_ms, 2),
            )
            return None

    def get_stats(self) -> Dict[str, Any]:
        """Thread-safe snapshot of selector statistics."""
        with self._lock:
            evaluated = self._frames_evaluated
            selected = self._frames_selected
        rate = selected / max(evaluated, 1)
        return {
            "drone_id": self.drone_id,
            "frames_evaluated": evaluated,
            "frames_selected": selected,
            "selection_rate": round(rate, 4),
            "target_rate": self.transmission_target,
        }

    def reset_state(self) -> None:
        """
        Reset all internal sliding-window and differential state.
        Call this when a new video file or mission starts.
        """
        with self._lock:
            self._prev_gray = None
            self._prev_hist = None
            self._window_buffer.clear()
            self._recent_selections.clear()
        log.info("cognitive_frame_selector_state_reset", drone_id=self.drone_id)

    # ------------------------------------------------------------------
    # Signal scorers (called inside _lock)
    # ------------------------------------------------------------------

    def _compute_histogram_score(self, frame: np.ndarray) -> float:
        """
        Grayscale histogram correlation vs. the previous evaluated frame.

        Returns
        -------
        float
            Score in [0, 1]. Higher = more histogram difference (new info).
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        hist = cv2.calcHist([gray], [0], None, [self._histogram_bins], [0, 256])
        cv2.normalize(hist, hist)

        if self._prev_hist is not None:
            correlation = cv2.compareHist(
                self._prev_hist, hist, cv2.HISTCMP_CORREL
            )
            score = 1.0 - max(0.0, float(correlation))
        else:
            score = 0.0

        self._prev_hist = hist
        return score

    def _compute_motion_score(self, frame: np.ndarray) -> float:
        """
        Multi-threshold frame differencing motion density score.

        Returns
        -------
        float
            Score in [0, 1]. Higher = more motion pixels.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape

        if self._prev_gray is None:
            self._prev_gray = gray
            return 0.0

        diff = cv2.absdiff(gray, self._prev_gray)

        _, tlow  = cv2.threshold(diff, self._motion_threshold_low,  255, cv2.THRESH_BINARY)
        _, tmed  = cv2.threshold(diff, self._motion_threshold_med,  255, cv2.THRESH_BINARY)
        _, thigh = cv2.threshold(diff, self._motion_threshold_high, 255, cv2.THRESH_BINARY)

        pixels = h * w
        mlow  = np.sum(tlow  > 0) / pixels
        mmed  = np.sum(tmed  > 0) / pixels
        mhigh = np.sum(thigh > 0) / pixels

        # Weighted combination: low gets most weight to catch subtle motion
        score = min((mlow * 0.4 + mmed * 0.4 + mhigh * 0.2) * 30.0, 1.0)

        self._prev_gray = gray
        return float(score)

    def _compute_object_score(self, frame: np.ndarray) -> float:
        """
        Object presence score: vehicles/lights + structural edges + texture.

        Returns
        -------
        float
            Score in [0, 1]. Higher = more objects of interest present.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        pixels = h * w

        # Bright spots: vehicles, lights
        _, bright = cv2.threshold(
            gray, self._object_brightness_threshold, 255, cv2.THRESH_BINARY
        )
        bright_density = np.sum(bright > 0) / pixels

        # Structural edges
        edges = cv2.Canny(gray, self._edge_threshold_low, self._edge_threshold_high)
        edge_density = np.sum(edges > 0) / pixels

        # Texture variance: crowd, foliage, complex background
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        variance = np.mean((gray.astype(float) - blur.astype(float)) ** 2) / 1000.0
        variance_score = min(variance / 50.0, 1.0)

        score = min(
            bright_density * 15.0 +
            edge_density   * 20.0 +
            variance_score * 15.0,
            1.0,
        )
        return float(score)

    def _compute_context_boost(
        self,
        change_motion_score: float,
        object_score: float,
        histogram_score: float,
    ) -> float:
        """
        Context-based priority multiplier.

        Boosts are applied when:
        - High change_motion_score → vehicle / fast-moving object event
        - High histogram_score → scene cut / scene change
        - High object_score → crowd / complex scene
        - No recent selections → temporal diversity boost

        Returns
        -------
        float
            Multiplier >= 1.0 (1.0 = no boost, 1.3 = 30% boost).
        """
        boost = 1.0

        # Vehicle / fast motion event
        if change_motion_score > 0.05:
            boost *= (1.0 + self.vehicle_boost * min(change_motion_score / 0.10, 1.0))

        # Scene change / histogram shift
        if histogram_score > 0.3:
            boost *= (1.0 + self.scene_change_boost * min(histogram_score / 0.5, 1.0))

        # Crowd / complex scene
        if object_score > 0.5:
            boost *= (1.0 + self.crowd_boost * min(object_score / 0.8, 1.0))

        # Temporal diversity: boost if nothing selected recently
        cutoff = time.monotonic() - self._recent_window_s
        recent = [t for t in self._recent_selections if t >= cutoff]
        if len(recent) == 0:
            boost *= 1.2   # 20% boost when selection is overdue

        return boost

    # ------------------------------------------------------------------
    # Priority computation & selection decision (called inside _lock)
    # ------------------------------------------------------------------

    def _compute_priority_score(
        self,
        frame: np.ndarray,
        frame_index: int,
        change_motion_score: float,
    ) -> Tuple[float, Dict[str, float]]:
        """
        Compute the composite priority score for a frame.

        Returns
        -------
        (priority, signals)
            priority : float in [0, 1]
            signals  : dict with individual signal values for logging
        """
        hist_score   = self._compute_histogram_score(frame)
        motion_score = self._compute_motion_score(frame)
        object_score = self._compute_object_score(frame)

        signals = {
            "histogram": hist_score,
            "motion":    motion_score,
            "object":    object_score,
        }

        # Weighted base priority
        base = (
            self._weights["histogram"] * hist_score   +
            self._weights["motion"]    * motion_score +
            self._weights["object"]    * object_score
        )

        # Context boost
        boost    = self._compute_context_boost(change_motion_score, object_score, hist_score)
        priority = min(base * boost, 1.0)

        return priority, signals

    def _should_select_frame(
        self,
        priority: float,
        frame_index: int,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Decide whether to select a frame based on its priority score.

        Uses a sliding-window percentile ranker:
        - Select if the frame is in the top (1 - percentile_threshold)
          fraction of priorities seen in the last ``window_size`` frames.
        - Emergency override for very high priority frames.
        - Minimum threshold gate for very low priority frames.

        Returns
        -------
        (select, decision_dict)
        """
        # Update sliding window
        self._window_buffer.append((priority, frame_index))
        if len(self._window_buffer) > self.window_size:
            self._window_buffer.pop(0)

        select = False
        reason = ""

        # 1. Emergency override
        if priority >= self.emergency_priority:
            select = True
            reason = "emergency_high_priority"

        # 2. Minimum gate
        elif priority < self.absolute_min_priority:
            select = False
            reason = "below_minimum_threshold"

        # 3. Sliding-window percentile ranking
        else:
            if len(self._window_buffer) >= 5:
                window_priorities = sorted(p for p, _ in self._window_buffer)
                try:
                    position  = window_priorities.index(priority)
                    percentile = position / len(window_priorities)
                except ValueError:
                    percentile = 0.5

                if percentile >= self.percentile_threshold:
                    select = True
                    top_pct = int((1.0 - percentile) * 100)
                    reason  = f"top_{top_pct}_percentile"
                else:
                    select = False
                    reason = f"bottom_{int(percentile * 100)}_percentile"
            else:
                # Window too small — use a simple threshold
                select = priority > 0.2
                reason = "early_window_threshold"

        if select:
            self._frames_selected += 1   # pre-increment so current_rate is accurate
            # NOTE: the caller increments again — we handle that with a decrement
            # Actually, let the caller handle the increment to avoid double-counting.
            # Revert:
            self._frames_selected -= 1

        decision: Dict[str, Any] = {
            "frame_index":   frame_index,
            "priority":      priority,
            "select":        select,
            "reason":        reason,
            "window_size":   len(self._window_buffer),
            "current_rate":  (self._frames_selected + int(select)) / max(self._frames_evaluated + 1, 1),
        }

        return select, decision
