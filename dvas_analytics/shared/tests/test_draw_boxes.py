"""Live view draws the detector's box on its frame, not the tracker's estimate (16 Nov).

The tracked box (boxes.xyxy) is BoT-SORT's Kalman estimate and stays what analytics uses;
draw_boxes() gives, per tracked box, the detection it was matched to on this frame.

Run from dvas_analytics/:  python -m pytest shared/tests/test_draw_boxes.py -q
"""
import numpy as np
import pytest
import torch

pytest.importorskip("ultralytics")
from ultralytics.engine.results import Results  # noqa: E402
from ultralytics.trackers.basetrack import BaseTrack  # noqa: E402

from shared import batch_tracker as bt  # noqa: E402
from shared.annotated_hub import drawn  # noqa: E402

FRAMES = 20


def _result(t: int) -> Results:
    """Three people speeding up to the right (x grows with t²): the Kalman estimate trails."""
    rng = np.random.default_rng(t)
    img = rng.integers(0, 255, (360, 640, 3), dtype=np.uint8)
    rows = []
    for k in range(3):
        x = 20 + 0.6 * t * t + 5 * k
        y = 40 + 100 * k
        rows.append([x, y, x + 30, y + 80, 0.9 - 0.1 * k, 0])
    return Results(img, path="", names={0: "person"}, boxes=torch.tensor(rows, dtype=torch.float32))


def _run(monkeypatch):
    monkeypatch.setattr(bt, "TRACK_THREADS", 1)
    monkeypatch.setattr(bt, "_track_pool", None)
    BaseTrack.reset_id()
    tracker = bt.BatchTracker(model=None, tracker="botsort.yaml")
    frames = {"D": np.zeros((360, 640, 3), np.uint8)}
    out = []
    for t in range(FRAMES):
        det = _result(t)
        dets = det.boxes.xyxy.numpy().copy()
        monkeypatch.setattr(tracker, "predict_batch", lambda fr, **kw: {"D": det})
        r = tracker.track_batch(frames)["D"]
        out.append((dets, r))
    return out


def test_drawn_box_is_the_frames_detection_and_tracked_box_is_unchanged(monkeypatch):
    worst = 0.0
    for dets, r in _run(monkeypatch):
        if r.boxes is None or r.boxes.id is None:
            continue
        d = bt.draw_boxes(r)
        assert d.shape == (len(r.boxes), 4)
        for row in d:      # every drawn box is exactly one of this frame's detections
            assert np.abs(dets - row).max(axis=1).min() < 1e-4
        # the tracked boxes are still the tracker's own output (ids, Kalman boxes)
        assert r.boxes.data.shape[1] == 7
        worst = max(worst, float(np.abs(r.boxes.xyxy.numpy() - d).max()))
    assert worst > 1.0     # the clip really separates the two (else this test proves nothing)


def test_untracked_result_draws_its_own_boxes():
    r = _result(0)
    np.testing.assert_allclose(bt.draw_boxes(r), r.boxes.xyxy.numpy())


def test_drawn_swaps_only_the_display_copy():
    items = [{"bbox": [0, 0, 10, 10], "draw_bbox": [1, 1, 11, 11]}, {"bbox": [5, 5, 6, 6]}]
    out = drawn(items)
    assert out[0]["bbox"] == [1, 1, 11, 11] and out[1]["bbox"] == [5, 5, 6, 6]
    assert items[0]["bbox"] == [0, 0, 10, 10]           # analytics dict untouched
    pair = drawn([{"vehicle_bbox": [0, 0, 2, 2], "draw_vehicle_bbox": [1, 1, 3, 3],
                   "plate_bbox": None, "draw_plate_bbox": None}], ("vehicle_bbox", "plate_bbox"))
    assert pair[0]["vehicle_bbox"] == [1, 1, 3, 3] and pair[0]["plate_bbox"] is None
