# -*- coding: utf-8 -*-
"""
SeaDrone Maritime Perception & Tracking Package (`seadrone`).
High-Accuracy Context-Aware Drone Vision with Spectral Water-Domain Masking (WDM)
and Zero-Flicker Maritime Object Tracking.
"""

__version__ = "1.0.0"

from .taxonomy import VESSEL_CLASSES, VESSEL_PALETTE
from .water_domain import get_water_domain_mask, generate_water_slices
from .classifier import classify_maritime_object
from .tracker import MaritimeVesselTracker
from .engine import MaritimePerceptionEngine, run_pipeline

__all__ = [
    "VESSEL_CLASSES",
    "VESSEL_PALETTE",
    "get_water_domain_mask",
    "generate_water_slices",
    "classify_maritime_object",
    "MaritimeVesselTracker",
    "MaritimePerceptionEngine",
    "run_pipeline",
]
