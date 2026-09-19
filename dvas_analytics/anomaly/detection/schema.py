"""
Shared detection data structures for Drone Suspicious Object Detection System.
This module is deliberately kept free of heavy YOLO/torch imports so it can
be used throughout the codebase (including tests) without triggering GPU
framework initialization.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class Detection:
    """Standardized detection container, decoupled from any specific model."""
    bbox: List[float]      # [x1, y1, x2, y2]
    confidence: float
    class_id: int
    class_name: str
    metadata: Optional[Dict[str, Any]] = None

    @property
    def center(self) -> List[float]:
        return [
            (self.bbox[0] + self.bbox[2]) / 2.0,
            (self.bbox[1] + self.bbox[3]) / 2.0,
        ]

    @property
    def area(self) -> float:
        return max(0.0, self.bbox[2] - self.bbox[0]) * max(0.0, self.bbox[3] - self.bbox[1])
