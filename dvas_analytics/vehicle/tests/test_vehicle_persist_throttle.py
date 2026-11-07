"""Vehicle routine writes per track (plan B1, §3): a vehicle seen for 3 s at 20 fps gives
3 track rows (incl. the final one), 4 crops (first, two periodic, final) and 1 attribute
row instead of 60 of each; every crop is reachable from the track row. DB/MinIO stubbed.

Run from dvas_analytics/:  python -m pytest vehicle/tests -q
"""
import asyncio
import importlib
import sys
import types
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def vehicle(monkeypatch):
    calls = {"rows": [], "crops": [], "attrs": 0, "image_key": []}

    async def upsert_track(drone_id, ext, kind, first, last, tracker_id=None, mission_id=None):
        calls["rows"].append(last)
        return "11111111-1111-1111-1111-111111111111"

    async def write_vehicle_attributes(**kw):
        calls["attrs"] += 1

    class Pool:
        async def execute(self, sql, key, track_id):
            calls["image_key"].append(key)

    async def get_pool():
        return Pool()

    async def upload_bytes(bucket, key, data, ctype):
        calls["crops"].append(data)
        return key

    async def _noop(*a, **k):
        return None

    db = types.ModuleType("shared.db")
    db.upsert_track, db.write_vehicle_attributes, db.get_pool = upsert_track, write_vehicle_attributes, get_pool
    db.__getattr__ = lambda attr: _noop
    rc = types.ModuleType("shared.redis_client")
    rc.__getattr__ = lambda attr: _noop
    mc = types.ModuleType("shared.minio_client")
    mc.upload_bytes = upload_bytes
    mc.make_key = lambda kind, drone, name, mission_id="": f"{kind}/{drone}/{name}"
    mc.__getattr__ = lambda attr: _noop
    an = types.ModuleType("vehicle.analyzer")
    an.VehicleAnalyzer = object
    for name, mod in {"shared.db": db, "shared.redis_client": rc, "shared.minio_client": mc,
                      "vehicle.analyzer": an}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    import shared
    monkeypatch.setattr(shared, "minio_client", mc, raising=False)   # `from shared import minio_client`
    monkeypatch.delitem(sys.modules, "vehicle.main", raising=False)
    main = importlib.import_module("vehicle.main")
    clock = Clock()
    main._gate = main.persist_gate.TrackGate(track_every_s=2, crop_every_s=1, attr_every_s=5,
                                             end_s=3, clock=clock)
    monkeypatch.setattr(main, "_live_vehicle_jpeg", lambda *a: b"jpeg")
    yield main, calls, clock
    sys.modules.pop("vehicle.main", None)


def test_one_vehicle_for_three_seconds(vehicle):
    main, calls, clock = vehicle
    frame = np.random.default_rng(0).integers(0, 255, (720, 1280, 3), dtype=np.uint8)
    t0 = datetime(2026, 11, 7, tzinfo=timezone.utc)

    async def go():
        for i in range(60):
            clock.t = i / 20
            result = {"vehicles": [{"tracker_id": 7, "vehicle_type": "car", "bbox": [100, 100, 300, 250],
                                    "colour_top3": [{"colour": "red"}], "speed_info": {"kph": 30},
                                    "crop_bytes": b"crop%d" % i}]}
            main.datetime = types.SimpleNamespace(now=lambda tz=None, i=i: t0 + timedelta(seconds=i / 20))
            await main._persist_vehicle_frame("D", {"metadata": '{"mission_id": "M1"}'}, frame, result)
        clock.t = 10.0
        await main._flush_ended_tracks()

    asyncio.run(go())
    assert len(calls["rows"]) == 3 and calls["rows"][-1] == t0 + timedelta(seconds=59 / 20)
    assert calls["crops"] == [b"crop0", b"crop20", b"crop40", b"crop59"]   # first, periodic ×2, final
    assert calls["attrs"] == 1
    assert len(calls["image_key"]) == 4 and len(main._gate) == 0
