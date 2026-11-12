"""Person/face analyzer: native-resolution face tiles, input size by GPU, temporary CPU fallback.

Run from dvas_analytics/:  python -m pytest person/tests -q
"""
import threading
import types

import numpy as np
import pytest
import torch

from person import analyzer as pa
from shared import gpu_profile


@pytest.mark.parametrize("h,w,n", [(2160, 3840, 28), (1080, 1920, 8), (480, 640, 1), (300, 500, 1)])
def test_face_tiles_cover_the_frame_with_equal_native_tiles(h, w, n):
    tiles = pa.face_tiles(h, w, 640, 96)
    assert len(tiles) == n
    sizes = {(x2 - x1, y2 - y1) for x1, y1, x2, y2 in tiles}
    assert len(sizes) == 1                                   # one shape → one GPU batch
    assert max(x2 for *_, x2, _ in tiles) == w and max(y2 for *_, y2 in tiles) == h
    for x1, y1, x2, y2 in tiles:                             # never upscaled or cropped off-frame
        assert 0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h and x2 - x1 <= 640


def test_partial_duplicates_at_tile_edges_are_dropped():
    full = (100, 100, 160, 170, 0.9)
    cut = (100, 100, 140, 170, 0.8)          # same face, cut by a tile edge
    other = (400, 100, 460, 170, 0.7)
    assert pa._drop_contained([cut, full, other]) == [full, other]


@pytest.mark.parametrize("tier,size", [("xlarge", 1920), ("large", 1280), ("medium", 640), ("small", 640)])
def test_person_input_size_follows_gpu(monkeypatch, tier, size):
    monkeypatch.delenv("PERSON_IMGSZ", raising=False)
    monkeypatch.setattr(gpu_profile, "detect", lambda: types.SimpleNamespace(tier=tier))
    assert pa.person_imgsz() == size
    monkeypatch.setenv("PERSON_IMGSZ", "1000")
    assert pa.person_imgsz() == 992                          # multiple of 32


class FakeModel:
    def __init__(self):
        self.device = "cuda:0"

    def to(self, d):
        self.device = d


def test_cpu_fallback_is_temporary_and_reported(monkeypatch):
    an = pa.PersonFaceAnalyzer.__new__(pa.PersonFaceAnalyzer)
    an.person_model, an.face_model = FakeModel(), FakeModel()
    an.tracker = types.SimpleNamespace(to=an.person_model.to)    # BatchTracker.to moves the person model
    an.face_lock = threading.RLock()
    an.device, an.imgsz, an.cpu_since, an.cpu_reason = 0, 1920, None, ""
    clock = [1000.0]
    monkeypatch.setattr(pa.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(pa.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(pa.torch.cuda, "empty_cache", lambda: None)

    an._fall_back_to_cpu(RuntimeError("CUDA error: an illegal memory access was encountered"))
    info = an.device_info()
    assert info["device"] == "cpu" and info["cpu_fallback"] and "illegal memory" in info["cpu_reason"]
    assert an.person_model.device == "cpu" and an.face_model.device == "cpu"

    clock[0] += pa.GPU_RETRY_S - 1
    an._maybe_back_to_gpu()
    assert an.device == "cpu"                                # not yet
    clock[0] += 2
    an._maybe_back_to_gpu()
    assert an.device == 0 and an.person_model.device == "cuda:0" and not an.device_info()["cpu_fallback"]


def test_face_model_calls_are_serialised(monkeypatch):
    """Endpoints and the stream share one lock on the face model (plan A1)."""
    an = pa.PersonFaceAnalyzer.__new__(pa.PersonFaceAnalyzer)
    an.face_lock, an.device, an.face_model = threading.RLock(), 0, object()
    held = []

    def fake_tiles(model, frame, device=0, **kw):
        # RLock owned by this thread ⇔ a second acquire from another thread would block
        held.append(an.face_lock._is_owned())
        return []
    monkeypatch.setattr(pa, "detect_faces_on_tiles", fake_tiles)
    assert an.detect_faces(None) == [] and held == [True]


class TileModel:
    """Fake face model: one box per tile wherever the tile has a bright pixel, so the
    result depends only on the tile's pixels. Records how many tiles each call got."""

    def __init__(self):
        self.calls = []

    def predict(self, crops, **kw):
        self.calls.append(len(crops))
        out = []
        for c in crops:
            ys, xs = np.nonzero(c.max(axis=2) > 200)
            data = (torch.tensor([[xs.min(), ys.min(), xs.max() + 1, ys.max() + 1, 0.9, 0]], dtype=torch.float32)
                    if len(xs) else torch.zeros((0, 6)))
            out.append(types.SimpleNamespace(boxes=types.SimpleNamespace(data=data)))
        return out


def _frame(spots):
    f = np.zeros((1080, 1920, 3), np.uint8)
    for x, y in spots:
        f[y:y + 40, x:x + 30] = 255
    return f


def test_round_faces_match_each_frame_on_its_own(monkeypatch):
    """v2 R6.7: the tiles of all drones in shared batches give each drone the same faces."""
    monkeypatch.setattr(gpu_profile, "predict_kwargs", lambda: {"half": False})
    frames = {"A": _frame([(100, 100), (900, 500)]), "B": _frame([(1500, 900)]), "C": _frame([])}
    alone = {k: pa.detect_faces_on_tiles(TileModel(), f, device="cpu") for k, f in frames.items()}
    model = TileModel()
    together = pa.detect_faces_round(model, frames, device="cpu", batch=128)
    assert together == alone
    assert len(alone["A"]) == 2 and len(alone["B"]) == 1 and alone["C"] == []
    assert model.calls == [24]                       # 3 frames x 8 tiles in one call


def test_round_batch_follows_gpu(monkeypatch):
    monkeypatch.delenv("PERSON_FACE_ROUND_BATCH", raising=False)
    monkeypatch.setattr(gpu_profile, "detect", lambda: types.SimpleNamespace(tier="large"))
    assert pa.face_round_batch() == 128
    monkeypatch.setattr(gpu_profile, "detect", lambda: types.SimpleNamespace(tier="small"))
    assert pa.face_round_batch() == pa.FACE_TILE_BATCH
    monkeypatch.setenv("PERSON_FACE_ROUND_BATCH", "64")
    assert pa.face_round_batch() == 64
