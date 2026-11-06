"""Person/face analyzer: native-resolution face tiles, input size by GPU, temporary CPU fallback.

Run from dvas_analytics/:  python -m pytest person/tests -q
"""
import types

import pytest

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
