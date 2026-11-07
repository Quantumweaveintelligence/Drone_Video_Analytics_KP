"""Person routine writes (plan B1/B3, §3): a person seen for 3 s at 20 fps gives 3 track
rows (incl. the final one) and 4 whole-body crops + appearance embeddings (first, two
periodic, final) instead of 60; face crops / embeddings stay exactly as before (every crop
the analyzer hands over). DB/MinIO stubbed; ResNet replaced by a tiny model.

Run from dvas_analytics/:  python -m pytest person/tests -q
"""
import asyncio
import importlib
import sys
import types
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("torchvision")


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def person(monkeypatch):
    calls = {"rows": [], "person_emb": [], "face_emb": 0, "uploads": []}

    async def upsert_track(drone_id, ext, kind, first, last, tracker_id=None, mission_id=None):
        calls["rows"].append(last)
        return "11111111-1111-1111-1111-111111111111"

    async def write_person_embedding(**kw):
        calls["person_emb"].append((kw["tracker_id"], len(kw["embedding"])))

    async def write_face_embedding(**kw):
        calls["face_emb"] += 1

    async def upload_bytes(bucket, key, data, ctype):
        calls["uploads"].append(key)
        return key

    async def _noop(*a, **k):
        return None

    db = types.ModuleType("shared.db")
    db.upsert_track, db.write_person_embedding, db.write_face_embedding = upsert_track, write_person_embedding, write_face_embedding
    db.__getattr__ = lambda attr: _noop
    rc = types.ModuleType("shared.redis_client")
    rc.__getattr__ = lambda attr: _noop
    mc = types.ModuleType("shared.minio_client")
    mc.upload_bytes = upload_bytes
    mc.make_key = lambda kind, drone, name, mission_id="": f"{kind}/{drone}/{name}"
    mc.__getattr__ = lambda attr: _noop
    an = types.ModuleType("person.analyzer")
    an.PersonFaceAnalyzer = object
    skc = types.ModuleType("sklearn.cluster")
    skc.AgglomerativeClustering = object
    for name, mod in {"shared.db": db, "shared.redis_client": rc, "shared.minio_client": mc,
                      "person.analyzer": an, "sklearn": types.ModuleType("sklearn"),
                      "sklearn.cluster": skc}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    import shared
    monkeypatch.setattr(shared, "minio_client", mc, raising=False)
    monkeypatch.delitem(sys.modules, "person.main", raising=False)
    monkeypatch.delitem(sys.modules, "person.deduplicator", raising=False)
    main = importlib.import_module("person.main")
    clock = Clock()
    main._gate = main.persist_gate.TrackGate(track_every_s=2, crop_every_s=1, attr_every_s=5,
                                             end_s=3, clock=clock)
    main.resnet_model = torch.nn.Sequential(torch.nn.AdaptiveAvgPool2d(1), torch.nn.Flatten()).eval()
    main.resnet_device = torch.device("cpu")
    main.arcface_recognizer = None
    monkeypatch.setattr(main, "_render_person_live", lambda *a: b"jpeg")
    monkeypatch.setattr(main, "_update_live_person_buffer", lambda *a, **k: None)
    yield main, calls, clock
    sys.modules.pop("person.main", None)
    sys.modules.pop("person.deduplicator", None)


def test_one_person_for_three_seconds(person):
    main, calls, clock = person
    frame = np.random.default_rng(0).integers(0, 255, (720, 1280, 3), dtype=np.uint8)
    face_jpeg = bytes(__import__("cv2").imencode(".jpg", frame[100:140, 120:160])[1])
    t0 = datetime(2026, 11, 7, tzinfo=timezone.utc)

    async def go():
        for i in range(60):
            clock.t = i / 20
            bb = {"x1": 100, "y1": 100, "x2": 200, "y2": 330}
            result = {"persons": [{"tracker_id": 5, "confidence": 0.9, "bbox": bb,
                                   "crop_bytes": face_jpeg if i % 5 == 0 else None}],
                      "faces": [{"tracker_id": 5, "bbox": {"x1": 120, "y1": 100, "x2": 160, "y2": 140},
                                 "crop_bytes": face_jpeg if i % 5 == 0 else None}]}
            main.datetime = types.SimpleNamespace(now=lambda tz=None, i=i: t0 + timedelta(seconds=i / 20))
            await main._persist_person_frame("D", {}, frame, result, "M1")
        clock.t = 10.0
        await main._flush_ended_tracks()

    asyncio.run(go())
    assert len(calls["rows"]) == 3 and calls["rows"][-1] == t0 + timedelta(seconds=59 / 20)
    assert calls["person_emb"] == [(5, 3)] * 4          # first, periodic ×2, final
    assert calls["face_emb"] == 12                      # every face crop, as before
