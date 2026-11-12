"""Per-drone tracker updates in parallel threads (v2 plan R6.7).

Run from dvas_analytics/:  python -m pytest shared/tests/test_parallel_tracking.py -q
"""
import threading

import numpy as np
import pytest
import torch

pytest.importorskip("ultralytics")
from ultralytics.engine.results import Results  # noqa: E402
from ultralytics.trackers.basetrack import BaseTrack  # noqa: E402

from shared import batch_tracker as bt  # noqa: E402

DRONES = [f"DRN-{i}" for i in range(4)]
FRAMES = 12


def _result(drone: int, t: int) -> Results:
    """Three people per drone walking right; the image is textured so BoT-SORT's
    optical flow has corners to follow."""
    rng = np.random.default_rng(drone * 1000 + t)
    img = rng.integers(0, 255, (360, 640, 3), dtype=np.uint8)
    rows = []
    for k in range(3):
        x = 40 + 150 * k + 6 * t + 3 * drone
        y = 60 + 70 * k
        rows.append([x, y, x + 40, y + 100, 0.9 - 0.1 * k, 0])
    return Results(img, path="", names={0: "person"}, boxes=torch.tensor(rows, dtype=torch.float32))


def _run(monkeypatch, threads: int):
    monkeypatch.setattr(bt, "TRACK_THREADS", threads)
    monkeypatch.setattr(bt, "_track_pool", None)
    tracker = bt.BatchTracker(model=None, tracker="botsort.yaml")
    frames = {d: np.zeros((360, 640, 3), np.uint8) for d in DRONES}
    out = {d: [] for d in DRONES}
    for t in range(FRAMES):
        prepared = {d: _result(i, t) for i, d in enumerate(DRONES)}
        monkeypatch.setattr(tracker, "predict_batch", lambda fr, **kw: prepared)
        for d, r in tracker.track_batch(frames).items():
            out[d].append(r.boxes.data.numpy() if r.boxes is not None else np.zeros((0, 7)))
    return out


def _ids_by_first_seen(rows):
    """Track ids of one drone renamed 0, 1, 2 … in order of first appearance."""
    names, seq = {}, []
    for data in rows:
        if data.shape[1] < 7:
            seq.append([])
            continue
        seq.append([names.setdefault(int(i), len(names)) for i in data[:, 4]])
    return seq


def test_parallel_updates_track_every_drone_as_one_at_a_time(monkeypatch):
    BaseTrack.reset_id()
    serial = _run(monkeypatch, 1)
    BaseTrack.reset_id()
    parallel = _run(monkeypatch, 8)
    for d in DRONES:
        assert len(serial[d]) == len(parallel[d]) == FRAMES
        for a, b in zip(serial[d], parallel[d]):
            np.testing.assert_allclose(np.delete(a, 4, axis=1) if a.shape[1] == 7 else a,
                                       np.delete(b, 4, axis=1) if b.shape[1] == 7 else b, atol=1e-4)
        # same tracks with the same history; only the global id numbers may differ
        assert _ids_by_first_seen(serial[d]) == _ids_by_first_seen(parallel[d])


def test_track_ids_stay_unique_across_drones(monkeypatch):
    BaseTrack.reset_id()
    parallel = _run(monkeypatch, 8)
    owner = {}
    for d in DRONES:
        for data in parallel[d]:
            if data.shape[1] == 7:
                for i in data[:, 4]:
                    assert owner.setdefault(int(i), d) == d     # an id belongs to one drone


def test_id_counter_is_atomic_under_threads():
    bt._lock_track_ids()
    BaseTrack.reset_id()
    got, lock = [], threading.Lock()

    def take():
        ids = [BaseTrack.next_id() for _ in range(2000)]
        with lock:
            got.extend(ids)
    workers = [threading.Thread(target=take) for _ in range(8)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert len(got) == len(set(got)) == 16000
