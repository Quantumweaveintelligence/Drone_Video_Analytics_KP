"""Anomaly round: per-drone stage timings (v2 plan R6.9).

Run from dvas_analytics:  python -m pytest anomaly/tests/test_round_stages.py -q
"""
import os
import sys
import threading
import time
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))      # anomaly's own imports

import analyzer as an_mod  # noqa: E402


class FakeAnalyzer:
    def __init__(self, motion_s, pipeline_s, detector):
        self.lock = threading.Lock()
        self.detector = detector
        self.motion_estimator = types.SimpleNamespace(
            estimate_motion=lambda frame: time.sleep(motion_s) or "motion")
        self._pipeline_s = pipeline_s

    def process_detections(self, frame, dets, meta, ts, motion=None, t0=None):
        time.sleep(self._pipeline_s)
        assert motion == "motion"
        return "annotated", [], []


def test_each_drone_gets_its_own_motion_and_pipeline_time():
    detector = types.SimpleNamespace(detect=lambda f: [])
    slow, fast = FakeAnalyzer(0.05, 0.03, detector), FakeAnalyzer(0.0, 0.0, detector)
    out = an_mod.analyze_round([(slow, "f1", {}, 1.0), (fast, "f2", {}, 1.0)], max_batch=4)
    assert [o[0] for o in out] == ["annotated", "annotated"]
    assert slow.stage_ms["motion"] >= 45 and slow.stage_ms["pipeline"] >= 25
    assert fast.stage_ms["motion"] < 20 and fast.stage_ms["pipeline"] < 20
