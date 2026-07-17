"""
src/cognitive_frame_selector/selected_frame.py
===============================================
Task 1.5 — Cognitive Frame Selector output payload.

SelectedFrame is the data object produced when CognitiveFrameSelector
decides that a frame is worth packaging for transmission to the GPU
analytics cluster.

The field layout matches the JSON schema defined in Task 1.6 of the
DVAS Production Roadmap (Frame Packager), enabling a future Kafka
publisher to serialise this directly:

    {
        "frame_id":        "uuid",
        "drone_id":        "DRONE_001",
        "mission_id":      "MISSION_2025_001",
        "utc_timestamp_ms": 1710000000000,
        "location":        {"lat": 8.5241, "lon": 76.9366, "altitude_m": 50},
        "frame_jpeg_b64":  "...",
        "change_score":    0.72,
        "cognitive_score": 0.85,
        "motion_regions":  [...]
    }
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import cv2
import numpy as np


@dataclass
class GpsLocation:
    """
    GPS fix attached to a selected frame.

    lat / lon : float
        WGS-84 latitude / longitude in decimal degrees.
    altitude_m : float
        Altitude above sea level in metres.
    """
    lat: float = 0.0
    lon: float = 0.0
    altitude_m: float = 0.0

    def to_dict(self) -> Dict[str, float]:
        return {
            "lat": self.lat,
            "lon": self.lon,
            "altitude_m": self.altitude_m,
        }


@dataclass
class SelectedFrame:
    """
    Output of CognitiveFrameSelector for one selected key frame.

    Parameters
    ----------
    frame_id : str
        UUID v4 uniquely identifying this selected frame.
    drone_id : str
        Logical identifier of the originating drone.
    mission_id : str
        Active mission identifier.
    utc_timestamp_ms : int
        UTC millisecond wall-clock timestamp of the source frame.
    frame_index : int
        Monotonically increasing frame counter from the video source.
    raw_frame : np.ndarray
        Original BGR frame (H × W × 3, uint8).
        Stored here for downstream use; not serialised to JSON directly.
    change_score : float
        Motion score from ChangeDetectionEngine (0.0–1.0).
    cognitive_score : float
        Priority score computed by CognitiveFrameSelector (0.0–1.0).
    selection_reason : str
        Human-readable reason why this frame was selected
        (e.g. "emergency_high_priority", "top_12_percentile").
    location : GpsLocation
        GPS fix at the time of capture (defaults to zero).
    motion_regions : List[Dict]
        Serialised BoundingBox list from the originating ChangeEvent.
    jpeg_quality : int
        Quality used when encoding the JPEG thumbnail (1–100).
    """

    frame_id: str
    drone_id: str
    mission_id: str
    utc_timestamp_ms: int
    frame_index: int
    raw_frame: np.ndarray = field(repr=False)
    change_score: float = 0.0
    cognitive_score: float = 0.0
    selection_reason: str = ""
    location: GpsLocation = field(default_factory=GpsLocation)
    motion_regions: List[Dict[str, Any]] = field(default_factory=list)
    jpeg_quality: int = 85

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        drone_id: str,
        mission_id: str,
        utc_timestamp_ms: int,
        frame_index: int,
        raw_frame: np.ndarray,
        change_score: float,
        cognitive_score: float,
        selection_reason: str,
        location: Optional[GpsLocation] = None,
        motion_regions: Optional[List[Dict[str, Any]]] = None,
        jpeg_quality: int = 85,
    ) -> "SelectedFrame":
        """
        Convenience factory that auto-generates a UUID frame_id.
        """
        return cls(
            frame_id=str(uuid.uuid4()),
            drone_id=drone_id,
            mission_id=mission_id,
            utc_timestamp_ms=utc_timestamp_ms,
            frame_index=frame_index,
            raw_frame=raw_frame,
            change_score=change_score,
            cognitive_score=cognitive_score,
            selection_reason=selection_reason,
            location=location or GpsLocation(),
            motion_regions=motion_regions or [],
            jpeg_quality=jpeg_quality,
        )

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def encode_jpeg(self) -> bytes:
        """
        Encode raw_frame to JPEG bytes at the configured quality.
        """
        ok, buf = cv2.imencode(
            ".jpg",
            self.raw_frame,
            [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality],
        )
        if not ok:
            raise RuntimeError("cv2.imencode failed for SelectedFrame JPEG encoding")
        return bytes(buf)

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialise to a plain dict matching the Task 1.6 JSON schema.
        The raw frame is encoded as a base-64 JPEG string.
        """
        jpeg_bytes = self.encode_jpeg()
        frame_b64 = base64.b64encode(jpeg_bytes).decode("ascii")

        return {
            "frame_id": self.frame_id,
            "drone_id": self.drone_id,
            "mission_id": self.mission_id,
            "utc_timestamp_ms": self.utc_timestamp_ms,
            "frame_index": self.frame_index,
            "location": self.location.to_dict(),
            "frame_jpeg_b64": frame_b64,
            "change_score": round(self.change_score, 4),
            "cognitive_score": round(self.cognitive_score, 4),
            "selection_reason": self.selection_reason,
            "motion_regions": self.motion_regions,
        }

    def to_json(self, **kwargs) -> str:
        """Serialise to JSON string."""
        return json.dumps(self.to_dict(), **kwargs)
