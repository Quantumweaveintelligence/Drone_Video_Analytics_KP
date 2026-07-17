"""
src/change_detection/change_detection_engine.py
================================================
Task 1.3 — Change Detection Pipeline (DVAS Production Roadmap, Phase 1)

Two-stage change detection running in a dedicated background thread:

Stage 1 — Frame Differencing (CPU, fast)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
• Convert incoming frames to grayscale.
• Feed into OpenCV MOG2 background subtractor
  (Gaussian Mixture Model — robust to gradual lighting shifts).
• Threshold the foreground mask → binary mask.
• Compute raw changed-pixel percentage (Stage 1 metric).

Stage 2 — Region-of-Change Scoring
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
• cv2.findContours on the binary mask.
• Filter contours below min_contour_area (removes noise, vibration, JPEG artefacts).
• Track blob persistence: a blob is only confirmed if it appears in
  ≥ persistence_frames consecutive frames (IoU-matched across frames).
• Score each confirmed region by: area + motion_magnitude (mean mask intensity).
• Compute composite motion_score = Σ(blob_area) / (W × H).
• Emit ChangeEvent only when motion_score > motion_score_threshold.

Threading
~~~~~~~~~
ChangeDetectionEngine runs a single daemon thread that:
  1. Pulls VideoFrame objects from an internal queue fed by the source's on_frame
     callback (or by direct push via push_frame()).
  2. Processes them in the two-stage pipeline.
  3. Calls on_change_event(ChangeEvent) in the same worker thread.
     ↳ If you need cross-thread delivery, wrap your callback with a Queue.

Prometheus metrics emitted
~~~~~~~~~~~~~~~~~~~~~~~~~~~
  change_events_total          Counter
  change_events_per_minute     Gauge
  change_motion_score          Gauge
  change_detection_latency_ms  Histogram
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

from src.change_detection.change_event import BoundingBox, ChangeEvent
from src.metrics.prometheus_metrics import (
    CHANGE_DETECTION_LATENCY,
    CHANGE_EVENTS_PER_MINUTE,
    CHANGE_EVENTS_TOTAL,
    CHANGE_MOTION_SCORE,
    FALSE_POSITIVE_RATE_EST,
)
from src.utils.logger import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal blob tracker (persistence across frames)
# ---------------------------------------------------------------------------

class _BlobTracker:
    """
    Lightweight tracker that counts how many consecutive frames each
    detected blob (bounding box) has been present.

    Uses IoU matching: a new blob is "the same" as a tracked blob if
    their IoU exceeds iou_threshold.  Unmatched blobs start at count=1.
    Matched blobs increment their counter.

    This simple O(N²) matcher is sufficient for edge-layer change detection
    where N (blobs per frame) is typically < 20.
    """

    def __init__(self, iou_threshold: float = 0.25) -> None:
        self._iou_threshold = iou_threshold
        # {blob_id: (BoundingBox, persistence_count)}
        self._tracked: Dict[int, Tuple[BoundingBox, int]] = {}
        self._next_id: int = 0

    def update(self, blobs: List[BoundingBox]) -> List[BoundingBox]:
        """
        Match *blobs* against the current tracked set.

        Returns the same list with updated ``persistence`` counters.
        Tracked blobs with no match this frame are pruned.
        """
        new_tracked: Dict[int, Tuple[BoundingBox, int]] = {}
        matched_old_ids: set = set()

        for blob in blobs:
            best_id = None
            best_iou = self._iou_threshold

            for bid, (old_blob, _) in self._tracked.items():
                if bid in matched_old_ids:
                    continue
                iou = blob.iou(old_blob)
                if iou >= best_iou:
                    best_iou = iou
                    best_id = bid

            if best_id is not None:
                _, old_count = self._tracked[best_id]
                blob.persistence = old_count + 1
                new_tracked[best_id] = (blob, blob.persistence)
                matched_old_ids.add(best_id)
            else:
                # New blob
                blob.persistence = 1
                new_tracked[self._next_id] = (blob, 1)
                self._next_id += 1

        self._tracked = new_tracked
        return blobs

    def reset(self) -> None:
        self._tracked.clear()
        self._next_id = 0


# ---------------------------------------------------------------------------
# ChangeDetectionEngine
# ---------------------------------------------------------------------------

class ChangeDetectionEngine:
    """
    Two-stage background change detection engine.

    Parameters
    ----------
    drone_id : str
        Used for logging and Prometheus labels.
    mog2_history : int
        Number of frames used to build the MOG2 background model.
        Larger → more stable against slow lighting changes.
    mog2_var_threshold : float
        Mahalanobis distance squared threshold for foreground classification.
        Lower → more sensitive (more false positives).
        Higher → less sensitive (may miss slow motion).
    detect_shadows : bool
        If True, MOG2 marks shadow pixels as 127 (excluded from mask).
    min_contour_area : int
        Blobs smaller than this (px²) are discarded as noise.
    motion_score_threshold : float
        Minimum motion_score to emit a ChangeEvent (range 0.0–1.0).
    persistence_frames : int
        Minimum consecutive frames a blob must appear before being confirmed.
    max_queue_size : int
        Maximum frames buffered before the oldest is dropped.
    on_change_event : Optional[Callable[[ChangeEvent], None]]
        Callback invoked for every confirmed ChangeEvent.
        Called from the worker thread — must be thread-safe.
    emit_raw_mask : bool
        If True, attach the binary mask to ChangeEvent.raw_mask.
        Costs a numpy copy per event; disable in production.
    emit_raw_frame : bool
        If True, attach the full BGR frame to ChangeEvent.raw_frame.
        Required by CognitiveFrameSelector (Task 1.5).  Costs one numpy
        copy per event; safe to leave enabled at edge-layer rates.
    """

    def __init__(
        self,
        drone_id: str = "drone_01",
        mog2_history: int = 500,
        mog2_var_threshold: float = 25.0,
        detect_shadows: bool = True,
        min_contour_area: int = 400,
        motion_score_threshold: float = 0.02,
        persistence_frames: int = 3,
        max_queue_size: int = 64,
        on_change_event: Optional[Callable[[ChangeEvent], None]] = None,
        emit_raw_mask: bool = True,
        emit_raw_frame: bool = True,
    ) -> None:
        self.drone_id = drone_id
        self.mog2_history = mog2_history
        self.mog2_var_threshold = mog2_var_threshold
        self.detect_shadows = detect_shadows
        self.min_contour_area = min_contour_area
        self.motion_score_threshold = motion_score_threshold
        self.persistence_frames = persistence_frames
        self.on_change_event = on_change_event
        self.emit_raw_mask = emit_raw_mask
        self.emit_raw_frame = emit_raw_frame

        # ── Internal queue (producer: push_frame / on_frame callback) ────────
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue_size)

        # ── Thread control ───────────────────────────────────────────────────
        self._stop_event = threading.Event()
        self._worker: Optional[threading.Thread] = None

        # ── MOG2 background subtractor (created in worker thread) ────────────
        self._mog2: Optional[cv2.BackgroundSubtractorMOG2] = None

        # ── Blob tracker ─────────────────────────────────────────────────────
        self._tracker = _BlobTracker(iou_threshold=0.25)

        # ── Rolling events-per-minute counter ────────────────────────────────
        self._event_timestamps: List[float] = []   # monotonic times of recent events
        self._epm_window_s: float = 60.0

        # ── Public statistics (read externally for HUD / heartbeat) ──────────
        self.events_total: int = 0
        self.last_motion_score: float = 0.0
        self.last_event: Optional[ChangeEvent] = None
        self._frames_processed: int = 0

        # ── False-positive rate estimation ────────────────────────────────────
        # Events whose motion_score is just above the threshold are likely
        # caused by wind, shadows, or JPEG artefacts rather than real activity.
        # Heuristic: score < threshold × 2.0 → FP candidate.
        self._fp_candidate_count: int = 0

        # ── Latest ChangeEvent queue for HUD consumption ─────────────────────
        # The StreamViewer pulls the most-recent event from this bounded queue
        # so it can draw bounding boxes without blocking the detection thread.
        self.event_display_queue: queue.Queue[ChangeEvent] = queue.Queue(maxsize=4)

        log.info(
            "change_detection_engine_created",
            drone_id=self.drone_id,
            mog2_history=self.mog2_history,
            mog2_var_threshold=self.mog2_var_threshold,
            min_contour_area=self.min_contour_area,
            motion_score_threshold=self.motion_score_threshold,
            persistence_frames=self.persistence_frames,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background change detection worker thread."""
        if self._worker is not None and self._worker.is_alive():
            log.warning("change_detection_engine_already_running", drone_id=self.drone_id)
            return
        self._stop_event.clear()
        self._worker = threading.Thread(
            target=self._worker_loop,
            name=f"ChangeDetector-{self.drone_id}",
            daemon=True,
        )
        self._worker.start()
        log.info("change_detection_engine_started", drone_id=self.drone_id)

    def stop(self) -> None:
        """Signal the worker to stop and wait for it to finish."""
        self._stop_event.set()
        if self._worker is not None:
            self._worker.join(timeout=10.0)
        log.info(
            "change_detection_engine_stopped",
            drone_id=self.drone_id,
            events_total=self.events_total,
            frames_processed=self._frames_processed,
        )

    def push_frame(self, video_frame) -> None:
        """
        Push a VideoFrame into the detection queue (non-blocking).

        If the queue is full, the oldest frame is dropped so the detector
        never falls behind more than max_queue_size frames.

        This method is safe to call from any thread (typically the
        DroneSimulationSource writer thread via its on_frame callback).
        """
        try:
            self._queue.put_nowait(video_frame)
        except queue.Full:
            try:
                self._queue.get_nowait()   # drop oldest
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(video_frame)
            except queue.Full:
                pass

    def get_stats(self) -> dict:
        """Thread-safe snapshot of engine statistics."""
        return {
            "drone_id": self.drone_id,
            "frames_processed": self._frames_processed,
            "events_total": self.events_total,
            "events_per_minute": self._compute_epm(),
            "last_motion_score": round(self.last_motion_score, 4),
            "queue_depth": self._queue.qsize(),
        }

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    def _worker_loop(self) -> None:
        """Main detection loop (runs in dedicated thread)."""
        # Initialise MOG2 in the worker thread
        self._mog2 = cv2.createBackgroundSubtractorMOG2(
            history=self.mog2_history,
            varThreshold=self.mog2_var_threshold,
            detectShadows=self.detect_shadows,
        )
        log.info(
            "mog2_initialised",
            drone_id=self.drone_id,
            history=self.mog2_history,
            varThreshold=self.mog2_var_threshold,
            detectShadows=self.detect_shadows,
        )

        while not self._stop_event.is_set():
            try:
                vf = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if vf is None:   # sentinel
                break

            t0 = time.monotonic()
            try:
                event = self._process_frame(vf)
                if event is not None:
                    self._handle_event(event)
            except Exception as exc:
                log.error(
                    "change_detection_frame_error",
                    drone_id=self.drone_id,
                    error=str(exc),
                )

            latency_ms = (time.monotonic() - t0) * 1_000.0
            CHANGE_DETECTION_LATENCY.labels(drone_id=self.drone_id).observe(latency_ms)
            self._frames_processed += 1

        log.info("change_detection_worker_exited", drone_id=self.drone_id)

    # ------------------------------------------------------------------
    # Stage 1 + 2 processing
    # ------------------------------------------------------------------

    def _process_frame(self, vf) -> Optional[ChangeEvent]:
        """
        Run both detection stages on a single VideoFrame.
        Returns a ChangeEvent if motion is confirmed, None otherwise.
        """
        frame = vf.frame
        h, w = frame.shape[:2]
        total_pixels = h * w

        # ── Stage 1: MOG2 foreground mask ────────────────────────────────
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # Apply slight Gaussian blur to reduce JPEG compression noise
        gray = cv2.GaussianBlur(gray, (5, 5), 0)

        fg_mask = self._mog2.apply(gray)

        # Remove shadow pixels (marked as 127 by MOG2 when detectShadows=True)
        # Only keep definite foreground (255)
        _, binary_mask = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)

        # Morphological cleanup: close small holes, remove isolated pixels
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        binary_mask = cv2.morphologyEx(binary_mask, cv2.MORPH_CLOSE, kernel)
        binary_mask = cv2.morphologyEx(binary_mask, cv2.MORPH_OPEN, kernel)

        changed_pixels = int(np.count_nonzero(binary_mask))
        stage1_pct = changed_pixels / max(total_pixels, 1)

        # ── Stage 2: Contour extraction and scoring ──────────────────────
        contours, _ = cv2.findContours(
            binary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        # Filter and score blobs
        raw_blobs: List[BoundingBox] = []
        for cnt in contours:
            area = int(cv2.contourArea(cnt))
            if area < self.min_contour_area:
                continue   # too small — noise / camera vibration

            x, y, bw, bh = cv2.boundingRect(cnt)

            # Motion magnitude: mean mask intensity in the blob's bounding box
            roi = binary_mask[y: y + bh, x: x + bw]
            motion_mag = float(np.mean(roi))

            raw_blobs.append(BoundingBox(
                x=x, y=y, w=bw, h=bh,
                area=area,
                motion_magnitude=motion_mag,
            ))

        # ── Blob persistence tracking ─────────────────────────────────────
        confirmed_blobs = self._tracker.update(raw_blobs)

        # Keep only blobs that have survived persistence_frames consecutive frames
        persistent_blobs = [
            b for b in confirmed_blobs
            if b.persistence >= self.persistence_frames
        ]

        # Sort by area descending (largest motion region first)
        persistent_blobs.sort(key=lambda b: b.area, reverse=True)

        # ── Motion score ──────────────────────────────────────────────────
        confirmed_area = sum(b.area for b in persistent_blobs)
        motion_score = confirmed_area / max(total_pixels, 1)
        self.last_motion_score = motion_score

        CHANGE_MOTION_SCORE.labels(drone_id=self.drone_id).set(motion_score)

        # ── Threshold check ───────────────────────────────────────────────
        if motion_score < self.motion_score_threshold or not persistent_blobs:
            return None

        # ── Build ChangeEvent ─────────────────────────────────────────────
        raw_mask_out  = binary_mask.copy() if self.emit_raw_mask  else None
        raw_frame_out = frame.copy()       if self.emit_raw_frame else None

        event = ChangeEvent(
            timestamp_ms=vf.utc_timestamp_ms,
            drone_id=self.drone_id,
            frame_index=vf.frame_index,
            regions=persistent_blobs,
            motion_score=motion_score,
            raw_mask=raw_mask_out,
            raw_frame=raw_frame_out,
            stage1_pixel_changed_pct=stage1_pct,
            stage2_blobs_detected=len(contours),
        )
        return event

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    def _handle_event(self, event: ChangeEvent) -> None:
        """Dispatch a confirmed ChangeEvent to all consumers."""
        self.events_total += 1
        self.last_event = event

        # ── Metrics ──────────────────────────────────────────────────────
        CHANGE_EVENTS_TOTAL.labels(drone_id=self.drone_id).inc()
        now = time.monotonic()
        self._event_timestamps.append(now)
        # Prune timestamps older than the EPM window
        cutoff = now - self._epm_window_s
        self._event_timestamps = [t for t in self._event_timestamps if t >= cutoff]
        epm = self._compute_epm()
        CHANGE_EVENTS_PER_MINUTE.labels(drone_id=self.drone_id).set(epm)

        # ── False-positive rate estimate ──────────────────────────────────────
        # Events barely above the threshold are likely noise (wind / lighting).
        if event.motion_score < self.motion_score_threshold * 2.0:
            self._fp_candidate_count += 1
        fp_rate = self._fp_candidate_count / max(self.events_total, 1)
        FALSE_POSITIVE_RATE_EST.labels(drone_id=self.drone_id).set(fp_rate)

        # ── HUD display queue (latest event for StreamViewer) ─────────────
        try:
            self.event_display_queue.put_nowait(event)
        except queue.Full:
            try:
                self.event_display_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.event_display_queue.put_nowait(event)
            except queue.Full:
                pass

        # ── User callback ─────────────────────────────────────────────────
        if self.on_change_event is not None:
            try:
                self.on_change_event(event)
            except Exception as exc:
                log.error(
                    "on_change_event_callback_error",
                    drone_id=self.drone_id,
                    error=str(exc),
                )

        log.debug(
            "change_event_emitted",
            drone_id=self.drone_id,
            frame_index=event.frame_index,
            motion_score=round(event.motion_score, 4),
            region_count=event.region_count,
            events_total=self.events_total,
            epm=round(epm, 1),
        )

    def _compute_epm(self) -> float:
        """Rolling events-per-minute rate over the last 60 seconds."""
        if not self._event_timestamps:
            return 0.0
        return len(self._event_timestamps) * (60.0 / self._epm_window_s)
