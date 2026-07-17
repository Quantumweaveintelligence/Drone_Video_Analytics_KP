"""
src/change_detection/__init__.py
=================================
Change Detection sub-package for the Kerala Police Drone Analytics System.

Implements Task 1.3 (Change Detection Pipeline) and Task 1.4 (Testing Harness)
from the DVAS Production Roadmap Phase 1 — Edge Processing Layer.

Public API
----------
    from src.change_detection import ChangeDetectionEngine, ChangeEvent, BoundingBox
"""

from src.change_detection.change_event import BoundingBox, ChangeEvent
from src.change_detection.change_detection_engine import ChangeDetectionEngine

__all__ = [
    "ChangeDetectionEngine",
    "ChangeEvent",
    "BoundingBox",
]
