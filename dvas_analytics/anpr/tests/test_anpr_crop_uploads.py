"""ANPR stores a track's best plate / vehicle crop once, not on every frame (the analyzer
sends the best crops with every frame; per-frame copies filled the pod volume, 25 Nov).
torch/cv2/DB/Valkey/MinIO are stubbed.

Run from dvas_analytics/:  python -m pytest anpr/tests -q
"""
import asyncio
import importlib
import sys
import types
from datetime import datetime, timezone

import numpy as np
import pytest


@pytest.fixture
def anpr(monkeypatch):
    calls = {"uploads": [], "reads": [], "meta": 0}

    async def _noop(*a, **k):
        return None

    async def upsert_track(*a, **k):
        return "11111111-1111-1111-1111-111111111111"

    async def write_plate_read(**kw):
        calls["reads"].append(kw)

    class Pool:
        async def execute(self, *a):
            calls["meta"] += 1

    async def get_pool():
        return Pool()

    async def upload_object(bucket, key, data, ctype, kind=""):
        calls["uploads"].append((bucket, kind, data))
        return {"key": key}

    async def hget_vehicle_track(*a):
        return {}

    db = types.ModuleType("shared.db")
    db.upsert_track, db.write_plate_read, db.get_pool = upsert_track, write_plate_read, get_pool
    db.__getattr__ = lambda attr: _noop
    rc = types.ModuleType("shared.redis_client")
    rc.hget_vehicle_track = hget_vehicle_track
    rc.__getattr__ = lambda attr: _noop
    mc = types.ModuleType("shared.minio_client")
    mc.upload_object = upload_object
    mc.make_key = lambda kind, drone, name, mission_id="": f"{kind}/{drone}/{name}"
    mc.__getattr__ = lambda attr: _noop
    an = types.ModuleType("anpr.analyzer")
    an.ANPRAnalyzer = object
    for name, mod in {"cv2": types.ModuleType("cv2"), "shared.db": db, "shared.redis_client": rc,
                      "shared.minio_client": mc, "anpr.analyzer": an}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    import shared
    monkeypatch.setattr(shared, "minio_client", mc, raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))   # main adds /analytics (an installed copy)
    monkeypatch.delitem(sys.modules, "anpr.main", raising=False)
    main = importlib.import_module("anpr.main")
    monkeypatch.setattr(main, "_live_anpr_jpeg", lambda *a: b"jpeg")
    main._gate = main.persist_gate.TrackGate()
    yield main, calls
    sys.modules.pop("anpr.main", None)


def _frame(main, plate_b64, veh_b64, text="KL07AB1234", tid=7):
    read = {"tracker_id": tid, "vehicle_type": "car", "plate_text": text, "ocr_confidence": 0.9,
            "is_reliable": True, "is_watchlisted": False,
            "plate_crop_b64": plate_b64, "vehicle_image_b64": veh_b64}
    asyncio.run(main._persist_anpr_frame("DRN-1", {}, np.zeros((4, 4, 3), np.uint8),
                                         {"plate_reads": [read]}, "M1"))


def test_unchanged_best_crops_are_stored_once(anpr):
    main, calls = anpr
    for _ in range(40):                                   # 2 s of one vehicle at 20 fps
        _frame(main, "cGxhdGUx", "dmVoMQ==")
    kinds = [k for _, k, _ in calls["uploads"]]
    assert kinds.count("plate_crop") == 1 and kinds.count("vehicle_crop") == 1
    # every frame still updates the plate read, with the stored keys
    assert len(calls["reads"]) == 40
    assert {r["plate_image_key"] for r in calls["reads"]} == {calls["reads"][0]["plate_image_key"]}
    assert calls["meta"] == 1


def test_a_better_crop_is_stored_again(anpr):
    main, calls = anpr
    _frame(main, "cGxhdGUx", "dmVoMQ==")
    _frame(main, "cGxhdGUy", "dmVoMQ==")                  # sharper plate crop, same vehicle crop
    _frame(main, "cGxhdGUy", "dmVoMg==")                  # better vehicle crop
    kinds = [k for _, k, _ in calls["uploads"]]
    assert kinds.count("plate_crop") == 2 and kinds.count("vehicle_crop") == 2


def test_each_vehicle_gets_its_own_crops(anpr):
    main, calls = anpr
    _frame(main, "cGxhdGUx", "dmVoMQ==", tid=1)
    _frame(main, "cGxhdGUx", "dmVoMQ==", tid=2)
    assert len(calls["uploads"]) == 4
