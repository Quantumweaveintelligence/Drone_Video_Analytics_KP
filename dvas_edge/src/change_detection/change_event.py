"""
src/change_detection/change_event.py
=====================================
Data types produced by the ChangeDetectionEngine.

ChangeEvent is the public output of Task 1.3 — Change Detection Pipeline.
It carries:
  • A list of detected motion regions (BoundingBox)
  • A composite motion_score (0.0–1.0) representing the fraction of the
    frame that changed, weighted by region size and motion magnitude.
  • Raw foreground mask (optional) — useful for HUD overlay drawing.
  • Raw BGR frame (optional) — required by the CognitiveFrameSelector
    (Task 1.5) to run histogram, motion, and object-presence scoring.
    Enable by setting ``emit_raw_frame=True`` in ChangeDetectionEngine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np


# ---------------------------------------------------------------------------
# BoundingBox
# ---------------------------------------------------------------------------

@dataclass
class BoundingBox:
    """
    A detected motion region within a video frame.

    Coordinates are in raw pixel space (not normalised).

    Attributes
    ----------
    x, y : int
        Top-left corner of the bounding box.
    w, h : int
        Width and height of the bounding box.
    area : int
        Pixel area of the contour (from cv2.contourArea).
    motion_magnitude : float
        Mean foreground pixel intensity within this region (0–255).
        High values indicate strong / fast motion.
    persistence : int
        Number of consecutive frames this blob has been present.
        Used to filter transient noise (wind, JPEG artefacts).
    """
    x: int
    y: int
    w: int
    h: int
    area: int
    motion_magnitude: float
    persistence: int = 1

    @property
    def cx(self) -> int:
        """Centre X."""
        return self.x + self.w // 2

    @property
    def cy(self) -> int:
        """Centre Y."""
        return self.y + self.h // 2

    def as_tuple(self):
        """Return (x, y, w, h) tuple for cv2.rectangle calls."""
        return (self.x, self.y, self.w, self.h)

    def iou(self, other: "BoundingBox") -> float:
        """
        Intersection-over-Union with another BoundingBox.
        Used for blob-persistence tracking across frames.
        """
        x1 = max(self.x, other.x)
        y1 = max(self.y, other.y)
        x2 = min(self.x + self.w, other.x + other.w)
        y2 = min(self.y + self.h, other.y + other.h)

        inter_w = max(0, x2 - x1)
        inter_h = max(0, y2 - y1)
        inter_area = inter_w * inter_h

        if inter_area == 0:
            return 0.0

        union_area = self.area + other.area - inter_area
        return inter_area / max(union_area, 1)


# ---------------------------------------------------------------------------
# ChangeEvent
# ---------------------------------------------------------------------------

@dataclass
class ChangeEvent:
    """
    Output of one frame's change detection analysis.

    Emitted by ChangeDetectionEngine when motion_score exceeds the
    configured threshold AND at least one region has survived the
    persistence filter.

    Parameters
    ----------
    timestamp_ms : int
        UTC millisecond wall-clock timestamp of the source frame.
    drone_id : str
        Logical identifier of the originating drone.
    frame_index : int
        Monotonically increasing frame counter from the video source.
    regions : List[BoundingBox]
        Detected motion regions, sorted by area (largest first).
    motion_score : float
        Fraction of frame pixels that changed, weighted by region area.
        Range: 0.0 (no motion) → 1.0 (entire frame changed).
    raw_mask : Optional[np.ndarray]
        Binary foreground mask (H × W, uint8, values 0 or 255).
        None when the caller opted out for performance reasons.
    raw_frame : Optional[np.ndarray]
        Original BGR frame (H × W × 3, uint8) at the moment of detection.
        Required by CognitiveFrameSelector (Task 1.5) for signal scoring.
        None when ``emit_raw_frame=False`` in ChangeDetectionEngine.
    stage1_pixel_changed_pct : float
        Raw percentage of changed pixels before contour filtering (Stage 1).
    stage2_blobs_detected : int
        Number of contours found before persistence filtering (Stage 2).
    """
    timestamp_ms: int
    drone_id: str
    frame_index: int
    regions: List[BoundingBox]
    motion_score: float
    raw_mask: Optional[np.ndarray] = field(default=None, repr=False)
    raw_frame: Optional[np.ndarray] = field(default=None, repr=False)
    stage1_pixel_changed_pct: float = 0.0
    stage2_blobs_detected: int = 0

    @property
    def region_count(self) -> int:
        """Number of confirmed motion regions."""
        return len(self.regions)

    @property
    def largest_region(self) -> Optional[BoundingBox]:
        """The region with the biggest contour area, or None."""
        return self.regions[0] if self.regions else None

    def to_dict(self) -> dict:
        """Serialise to a plain dict (for JSON / Kafka payload)."""
        return {
            "timestamp_ms": self.timestamp_ms,
            "drone_id": self.drone_id,
            "frame_index": self.frame_index,
            "motion_score": round(self.motion_score, 4),
            "region_count": self.region_count,
            "stage1_pixel_changed_pct": round(self.stage1_pixel_changed_pct, 4),
            "stage2_blobs_detected": self.stage2_blobs_detected,
            "regions": [
                {
                    "x": r.x, "y": r.y, "w": r.w, "h": r.h,
                    "area": r.area,
                    "motion_magnitude": round(r.motion_magnitude, 2),
                    "persistence": r.persistence,
                }
                for r in self.regions
            ],
        }
