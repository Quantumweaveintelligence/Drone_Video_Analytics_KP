# -*- coding: utf-8 -*-
"""
DVAS Analytics — HSV Vehicle Colour Classifier
Replaces the TensorFlow 1.x MobileNet .pb model.
Pure OpenCV + NumPy, zero extra dependencies, ~2 ms per crop.

Supports 12 vehicle colours:
    Black, White, Silver, Grey, Red, Blue, Dark Blue,
    Green, Yellow, Orange, Brown/Maroon, Gold/Beige
"""
import logging
from typing import List, Dict

import cv2
import numpy as np

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HSV range table  (H in OpenCV: 0-179, S: 0-255, V: 0-255)
# Each entry: (name, [(h_lo, h_hi, s_lo, s_hi, v_lo, v_hi), ...])
# ---------------------------------------------------------------------------
_COLOUR_RANGES: List[tuple] = [
    ("Red",        [(0,   10,  80, 255, 60, 255),
                    (165, 179, 80, 255, 60, 255)]),   # red wraps at 180
    ("Orange",     [(11,  20,  80, 255, 80, 255)]),
    ("Yellow",     [(21,  35,  80, 255, 80, 255)]),
    ("Green",      [(36,  80,  40, 255, 40, 255)]),
    ("Blue",       [(100, 130, 60, 255, 40, 255)]),
    ("Dark Blue",  [(100, 130, 60, 255, 10, 100)]),
    ("Purple",     [(131, 155, 50, 255, 40, 255)]),
    ("Brown",      [(10,  20,  60, 200, 20, 150)]),
    ("Gold/Beige", [(20,  35,  30, 150, 130, 255)]),
    ("White",      [(0,   179, 0,   60, 180, 255)]),
    ("Silver",     [(0,   179, 0,   50, 130, 179)]),
    ("Grey",       [(0,   179, 0,   40,  60, 129)]),
    ("Black",      [(0,   179, 0,  255,  0,   59)]),
]


def _pixel_fraction(hsv: np.ndarray, ranges: List[tuple]) -> float:
    """Fraction of pixels in the image that fall within any of the HSV ranges."""
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    mask = np.zeros(h.shape, dtype=bool)
    for (h_lo, h_hi, s_lo, s_hi, v_lo, v_hi) in ranges:
        mask |= (
            (h >= h_lo) & (h <= h_hi) &
            (s >= s_lo) & (s <= s_hi) &
            (v >= v_lo) & (v <= v_hi)
        )
    return float(mask.sum()) / float(mask.size)


def classify_colour(
    bgr_crop: np.ndarray,
    top_k: int = 3,
) -> List[Dict]:
    """
    Classify vehicle colour from a BGR crop.

    Args:
        bgr_crop: BGR image (numpy array) of the vehicle.
        top_k:    Number of top colours to return.

    Returns:
        List of {colour: str, prob: float} sorted by prob descending,
        length = top_k. Returns empty list on failure.
    """
    try:
        if bgr_crop is None or bgr_crop.size == 0:
            return []

        # Resize to small thumbnail for speed (keep aspect ratio)
        h, w = bgr_crop.shape[:2]
        if max(h, w) > 128:
            scale = 128 / max(h, w)
            bgr_crop = cv2.resize(
                bgr_crop,
                (max(1, int(w * scale)), max(1, int(h * scale))),
                interpolation=cv2.INTER_AREA,
            )

        # Crop centre 60% to avoid background contamination
        ch, cw = bgr_crop.shape[:2]
        y1 = int(ch * 0.20); y2 = int(ch * 0.80)
        x1 = int(cw * 0.10); x2 = int(cw * 0.90)
        centre = bgr_crop[y1:y2, x1:x2]
        if centre.size == 0:
            centre = bgr_crop

        hsv = cv2.cvtColor(centre, cv2.COLOR_BGR2HSV)

        scores: Dict[str, float] = {}
        for (name, ranges) in _COLOUR_RANGES:
            scores[name] = _pixel_fraction(hsv, ranges)

        # Normalise to sum = 1 (softmax-like)
        total = sum(scores.values()) or 1e-9
        scores = {k: v / total for k, v in scores.items()}

        # Sort and return top_k
        sorted_colours = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [
            {"colour": name, "prob": round(prob, 4)}
            for name, prob in sorted_colours[:top_k]
        ]

    except Exception as exc:
        log.warning("Colour classification failed: %s", exc)
        return []
