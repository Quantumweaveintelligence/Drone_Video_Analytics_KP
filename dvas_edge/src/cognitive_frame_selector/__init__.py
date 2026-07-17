"""
src/cognitive_frame_selector/__init__.py
=========================================
Cognitive Frame Selector sub-package for the Kerala Police DVAS.

Implements Task 1.5 (Cognitive Frame Selector) from the DVAS Production
Roadmap Phase 1 — Edge Processing Layer, Week 8.

Public API
----------
    from src.cognitive_frame_selector import CognitiveFrameSelector, SelectedFrame
"""

from src.cognitive_frame_selector.selected_frame import GpsLocation, SelectedFrame
from src.cognitive_frame_selector.cognitive_frame_selector import CognitiveFrameSelector

__all__ = [
    "CognitiveFrameSelector",
    "SelectedFrame",
    "GpsLocation",
]
