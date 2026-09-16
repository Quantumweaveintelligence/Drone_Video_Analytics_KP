# -*- coding: utf-8 -*-
"""
Deterministic Physical Maritime Classifier.
Eliminates all hallucinatory zero-shot predictions by resolving vessels into 9 taxonomy
classes using spatial bounding box scale, aspect ratio, horizon elevation, and spectral signatures.
"""

import numpy as np
from .taxonomy import VESSEL_CLASSES

def classify_maritime_object(box, orig_class_name: str, frame_h: int, frame_w: int, crop: np.ndarray = None):
    """
    Deterministic physical scale, aspect-ratio, and spectral classifier for 9 marine classes:
    0: Fishing Boat / Trawler
    1: Cargo / Merchant Ship
    2: Container Ship
    3: Passenger Ship / Ferry
    4: Speedboat / Jet Ski
    5: Sailboat / Yacht
    6: Small Boat
    7: Person
    8: Life Vest / Floater / Buoy
    """
    orig_lower = orig_class_name.lower()
    x1, y1, x2, y2 = map(int, box[:4])
    bw = max(1, x2 - x1)
    bh = max(1, y2 - y1)
    area = bw * bh
    aspect = float(bw) / float(bh)

    # Spectral color check for international safety orange / yellow / buoy colors
    has_safety_orange = False
    if crop is not None and crop.size > 0:
        b_mean = float(np.mean(crop[:, :, 0]))
        g_mean = float(np.mean(crop[:, :, 1]))
        r_mean = float(np.mean(crop[:, :, 2]))
        # Orange / Red / High-vis yellow on water: R channel dominant over Blue
        if (r_mean > 130 and r_mean > b_mean + 30 and g_mean > b_mean) or (r_mean > 160 and g_mean > 140 and b_mean < 110):
            has_safety_orange = True

    # 1. Person / Swimmer in water
    if orig_lower in ["person", "swimmer"]:
        fine_id = 7  # Person

    # 2. Life Vest / Floater / Buoy (compact tiny object or high-vis safety color)
    elif orig_lower in ["life vest", "floater", "buoy", "surfboard", "sports ball"] or (area < 2500 and 0.5 <= aspect <= 1.8) or (area < 4500 and has_safety_orange):
        fine_id = 8  # Life Vest / Floater / Buoy

    # 3. Cargo / Merchant Ship on distant horizon
    elif y1 < int(frame_h * 0.14) and aspect > 1.8:
        fine_id = 1  # Cargo / Merchant Ship

    # 4. Massive Commercial Vessel -> Container Ship
    elif area > 75000:
        fine_id = 2  # Container Ship

    # 5. Large Multi-Tiered Vessel -> Passenger Ship / Ferry
    elif 38000 < area <= 75000 and 1.2 <= aspect <= 2.5:
        fine_id = 3  # Passenger Ship / Ferry

    # 6. Sailboat / Yacht (tall vertical profile or mast)
    elif area > 3500 and aspect < 1.15:
        fine_id = 5  # Sailboat / Yacht

    # 7. Speedboat / Jet Ski (sleek, fast, low elongated profile)
    elif 6000 <= area < 25000 and 1.8 <= aspect <= 3.2:
        fine_id = 4  # Speedboat / Jet Ski

    # 8. Small Boat (compact dinghy, skiff, tender)
    elif area < 7500 or (aspect > 2.0 and area < 18000):
        fine_id = 6  # Small Boat

    # 9. Standard Fishing Boat / Trawler
    else:
        fine_id = 0  # Fishing Boat / Trawler

    return fine_id, VESSEL_CLASSES[fine_id]
