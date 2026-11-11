"""Anomaly tracker in seconds of capture time (v2 plan R6.5).

A lost track expires after max_lost_frames at 20 fps worth of time: at 20 fps on exactly the
same frame as the old frame count (the 31st miss), at other rates after the same 1.5 s.

Run from dvas_analytics:  python -m pytest anomaly/tests/test_time_based_rules.py -q
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))      # anomaly's own imports

from detection.schema import Detection          # noqa: E402
from tracking.tracker import DroneMultiObjectTracker  # noqa: E402


def misses_until_gone(fps):
    trk = DroneMultiObjectTracker(max_lost_frames=30)
    trk.update([Detection([100, 100, 140, 200], 0.9, 0, "person")], timestamp=0.0)
    assert len(trk.tracks) == 1
    for i in range(1, 400):
        trk.update([], timestamp=i / fps)
        if not trk.tracks:
            return i
    return None


def test_same_expiry_as_the_frame_count_at_20fps():
    assert misses_until_gone(20.0) == 31          # the old rule: count > 30


def test_same_time_at_other_rates():
    # the first miss whose time is ≥ 1.5 s after the first miss
    assert misses_until_gone(10.0) == 16          # first miss 0.1 s, gone at 1.6 s
    assert misses_until_gone(5.0) == 9            # first miss 0.2 s, gone at 1.8 s
