"""Anomaly camera motion with ORB features prepared in the decode workers (v2 plan R6.9).

Run from dvas_analytics:  python -m pytest anomaly/tests/test_motion_prepared.py -q
"""
import gc
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))      # anomaly's own imports

from motion import camera_motion as cm  # noqa: E402


def _frames(n=8):
    rng = np.random.default_rng(3)
    base = cv2.GaussianBlur(rng.integers(0, 255, (1200, 2100, 3), dtype=np.uint8), (0, 0), 2)
    return [np.ascontiguousarray(base[t * 4:t * 4 + 1080, t * 6:t * 6 + 1920]) for t in range(n)]


def test_prepared_features_give_the_same_motion():
    plain, prepared = cm.CameraMotionEstimator(max_features=800), cm.CameraMotionEstimator(max_features=800)
    used = 0
    for f in _frames():
        a = plain.estimate_motion(f.copy())          # a copy: never prepared
        cm.prepare(f, 800)
        used += id(f) in cm._PREPARED
        b = prepared.estimate_motion(f)
        np.testing.assert_array_equal(a.matrix, b.matrix)
        assert (a.dx, a.dy, a.rotation_deg, a.scale, a.inliers_count, a.is_valid) == \
               (b.dx, b.dy, b.rotation_deg, b.scale, b.inliers_count, b.is_valid)
    assert used == 8
    np.testing.assert_array_equal(plain.accumulated_H, prepared.accumulated_H)


def test_other_feature_counts_are_not_used_and_entries_are_freed():
    f = _frames(1)[0]
    cm.prepare(f, 300)
    est = cm.CameraMotionEstimator(max_features=800)
    est.estimate_motion(f)                            # computes its own 800 features
    assert est.prev_kp is not cm._PREPARED[id(f)][2]
    key = id(f)
    del f
    gc.collect()
    assert key not in cm._PREPARED
