"""Vessel proximity alerts: one per drone per cooldown (closest pair), moored pairs never,
the same gathering again only after the repeat time. Each alert stores a full frame and a
vault copy, so per-pair alerts filled the volume. DB/MinIO/engine stubbed.

Run from dvas_analytics/:  python -m pytest vessel/tests -q
"""
import importlib
import sys
import types

import pytest


@pytest.fixture
def vessel(monkeypatch):
    async def _noop(*a, **k):
        return None

    stubs = {}
    for name in ("shared.db", "shared.redis_client", "shared.minio_client"):
        m = types.ModuleType(name)
        m.__getattr__ = lambda attr: _noop
        stubs[name] = m
    eng = types.ModuleType("seadrone.engine")
    eng.MaritimePerceptionEngine = object
    tax = types.ModuleType("seadrone.taxonomy")
    tax.VESSEL_CLASSES, tax.VESSEL_PALETTE = {}, {}
    stubs.update({"seadrone": types.ModuleType("seadrone"), "seadrone.engine": eng,
                  "seadrone.taxonomy": tax})
    for name, mod in stubs.items():
        monkeypatch.setitem(sys.modules, name, mod)
    import shared
    monkeypatch.setattr(shared, "minio_client", stubs["shared.minio_client"], raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))   # main adds /analytics (an installed copy)
    monkeypatch.delitem(sys.modules, "vessel.main", raising=False)
    main = importlib.import_module("vessel.main")
    main._proximity_last.clear()
    yield main
    sys.modules.pop("vessel.main", None)


def boat(tid, x, y=100, still=False):
    return {"track_id": tid, "bbox_xywh": (x, y, 20, 20), "is_stationary": still}


def test_one_alert_per_drone_for_the_closest_pair(vessel):
    boats = [boat(1, 0), boat(2, 50), boat(3, 80), boat(4, 500)]   # 1-2 50 px, 2-3 30 px, 1-3 80 px
    alerts = vessel._check_proximity_clusters("D1", boats, now=0)
    assert [(a["track_id"], b["track_id"], round(d)) for a, b, d in alerts] == [(2, 3, 30)]


def test_far_apart_vessels_never_alert(vessel):
    assert vessel._check_proximity_clusters("D1", [boat(1, 0), boat(2, 400)], now=0) == []


def test_moored_pair_never_alerts_but_a_boat_coming_alongside_does(vessel):
    assert vessel._check_proximity_clusters("D1", [boat(1, 0, still=True), boat(2, 40, still=True)], now=0) == []
    alerts = vessel._check_proximity_clusters(
        "D1", [boat(1, 0, still=True), boat(2, 40, still=True), boat(3, 300, 300), boat(4, 330, 300)], now=1)
    assert [(a["track_id"], b["track_id"]) for a, b, _ in alerts] == [(3, 4)]


def test_same_gathering_waits_the_repeat_time_even_with_new_track_ids(vessel):
    c = vessel._check_proximity_clusters
    assert c("D1", [boat(1, 0), boat(2, 40)], now=0)
    assert c("D1", [boat(1, 0), boat(2, 40)], now=31) == []                 # was a new alert per pair
    assert c("D1", [boat(1, 0), boat(9, 40)], now=60) == []                 # tracker gave boat 2 a new id
    assert c("D1", [boat(1, 0), boat(2, 40)], now=vessel.PROXIMITY_REPEAT_S + 1)


def test_a_different_gathering_alerts_after_the_cooldown(vessel):
    c = vessel._check_proximity_clusters
    assert c("D1", [boat(1, 0), boat(2, 40)], now=0)
    assert c("D1", [boat(5, 0, 400), boat(6, 40, 400)], now=10) == []      # within the cooldown
    assert c("D1", [boat(5, 0, 400), boat(6, 40, 400)], now=vessel.PROXIMITY_COOLDOWN_S + 1)


def test_drones_are_independent(vessel):
    assert vessel._check_proximity_clusters("D1", [boat(1, 0), boat(2, 40)], now=0)
    assert vessel._check_proximity_clusters("D2", [boat(1, 0), boat(2, 40)], now=0)
