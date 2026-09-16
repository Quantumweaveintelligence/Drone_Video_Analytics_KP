# -*- coding: utf-8 -*-
"""
Spectral Water-Domain Masking (WDM) Module.
Uses physical colour signatures of maritime harbour water to eliminate all land clutter,
asphalt roads, shipping trucks, tetrapod breakwaters, and concrete piers.
"""

import cv2
import numpy as np

def get_water_domain_mask(img: np.ndarray):
    """
    Computes navigable ocean/harbour water mask based on spectral signature:
    (B > 80) & (G > 75) & (B > R + 8) & (G >= R).
    Returns (raw_water_mask, dilated_water_mask).
    """
    b, g, r = img[:, :, 0], img[:, :, 1], img[:, :, 2]
    raw_water = (b > 80) & (g > 75) & (b.astype(int) > r.astype(int) + 8) & (g.astype(int) >= r.astype(int))
    water_u8 = (raw_water * 255).astype(np.uint8)

    # Morphological closing to seal boat wakes, waves, and ripples
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (35, 35))
    closed = cv2.morphologyEx(water_u8, cv2.MORPH_CLOSE, kernel)

    # Connected component filtering to remove small puddles or blue tarps (< 15,000 px)
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(closed)
    mask = np.zeros_like(closed)
    for i in range(1, num_labels):
        if stats[i, cv2.CC_STAT_AREA] > 15000:
            mask[labels == i] = 255

    # Dilation so hulls moored at dock edges or hulls covering water underneath are properly included
    dilate_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25))
    dilated_mask = cv2.dilate(mask, dilate_kernel)
    return mask, dilated_mask

def generate_water_slices(img_h: int, img_w: int, water_mask: np.ndarray, slice_size: int = 640, overlap_ratio: float = 0.2, min_water_pct: float = 0.15):
    """
    Generates tile slices strictly over navigable water bodies, skipping all inland tiles.
    """
    step = int(slice_size * (1 - overlap_ratio))
    slices = []
    for ymin in range(0, img_h - slice_size + 1, step):
        for xmin in range(0, img_w - slice_size + 1, step):
            xmax = min(img_w, xmin + slice_size)
            ymax = min(img_h, ymin + slice_size)
            tile_mask = water_mask[ymin:ymax, xmin:xmax]
            water_ratio = np.sum(tile_mask > 0) / float(tile_mask.size)
            if water_ratio >= min_water_pct:
                slices.append((xmin, ymin, xmax, ymax))
    return slices
