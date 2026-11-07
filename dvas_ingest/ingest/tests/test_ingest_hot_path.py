"""Ingest hot path (plan §2.D D4/D5/D7): coalesced frame_queued broadcasts, per-client send
timeout, per-drone log summary, drone/mission id validation.

Run from dvas_ingest/ingest:  python -m pytest tests -q
"""
import asyncio
import json
import logging
import os
import re
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import main  # noqa: E402
import models  # noqa: E402


def run(c):
    return asyncio.run(c)


# ── D4: frame_queued coalescing ───────────────────────────────────────────────

class RecordingManager:
    def __init__(self, clients=True):
        self.sent = []          # (monotonic time, event)
        self.clients = clients

    @property
    def has_clients(self):
        return self.clients

    async def broadcast(self, event):
        self.sent.append((time.monotonic(), event))


def _frame(drone, seq):
    return {"event": "frame_queued", "drone_id": drone, "frame_sequence": seq}


def test_frame_events_coalesced_to_two_per_second_per_drone_latest_payload_kept():
    async def scenario():
        mgr = RecordingManager()
        co = main._FrameEventCoalescer(mgr, interval_s=0.5)
        t0 = time.monotonic()
        for seq in range(15):                       # 15 frames per drone within ~0.3 s
            co.offer("DRN-A", _frame("DRN-A", seq))
            co.offer("DRN-B", _frame("DRN-B", seq))
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.9)                   # past the trailing flush
        return mgr, t0

    mgr, t0 = run(scenario())
    for drone in ("DRN-A", "DRN-B"):
        sends = [(t, e) for t, e in mgr.sent if e["drone_id"] == drone]
        assert len(sends) == 2, sends               # leading + one trailing send
        assert sum(1 for t, _ in sends if t - t0 <= 1.0) <= 2
        assert sends[0][1]["frame_sequence"] == 0   # first frame goes out at once
        assert sends[-1][1]["frame_sequence"] == 14  # the latest payload is the one kept
        assert sends[1][0] - sends[0][0] >= 0.45    # spaced by the interval
        assert set(sends[-1][1]) == {"event", "drone_id", "frame_sequence"}  # shape unchanged


def test_frame_events_skipped_without_clients_and_forget_cancels_pending():
    async def scenario():
        mgr = RecordingManager(clients=False)
        co = main._FrameEventCoalescer(mgr, interval_s=0.2)
        for seq in range(5):
            co.offer("DRN-A", _frame("DRN-A", seq))
        await asyncio.sleep(0.05)
        none_sent = list(mgr.sent)

        mgr.clients = True
        co.offer("DRN-A", _frame("DRN-A", 10))     # sent at once
        co.offer("DRN-A", _frame("DRN-A", 11))     # pending
        co.forget("DRN-A")                          # e.g. stream purged
        await asyncio.sleep(0.35)
        return none_sent, mgr.sent

    none_sent, sent = run(scenario())
    assert none_sent == []
    assert [e["frame_sequence"] for _, e in sent] == [10]


# ── D4: broadcast with per-client timeout, concurrent sends ──────────────────

class FastWS:
    def __init__(self):
        self.got = []
        self.closed = False

    async def send_text(self, payload):
        self.got.append((time.monotonic(), json.loads(payload)))

    async def close(self, code=1000):
        self.closed = True


class SlowWS(FastWS):
    async def send_text(self, payload):
        await asyncio.sleep(30)


class BrokenWS(FastWS):
    async def send_text(self, payload):
        raise RuntimeError("connection reset")


def test_slow_client_is_dropped_without_delaying_fast_client():
    async def scenario():
        mgr = main._WSManager(send_timeout_s=0.3)
        slow, fast, broken = SlowWS(), FastWS(), BrokenWS()
        mgr._clients = [slow, fast, broken]          # slow first: sequential sends would block fast
        t0 = time.monotonic()
        await mgr.broadcast({"event": "mission_start", "drone_id": "DRN-A"})
        took = time.monotonic() - t0
        await asyncio.sleep(0.05)                    # let the background close run
        await mgr.broadcast({"event": "mission_end", "drone_id": "DRN-A"})
        return mgr, slow, fast, broken, t0, took

    mgr, slow, fast, broken, t0, took = run(scenario())
    assert fast.got[0][0] - t0 < 0.1                 # not held up by the slow client
    assert took < 1.0                                # bounded by the send timeout, not 30 s
    assert mgr._clients == [fast]                    # slow and broken clients dropped
    assert slow.closed
    assert [e["event"] for _, e in fast.got] == ["mission_start", "mission_end"]


def test_other_events_are_not_throttled():
    async def scenario():
        mgr = main._WSManager(send_timeout_s=0.5)
        fast = FastWS()
        mgr._clients = [fast]
        for ev in ("mission_start", "mission_end", "mission_drained", "geofence_updated"):
            await mgr.broadcast({"event": ev, "drone_id": "DRN-A"})
        return fast

    assert len(run(scenario()).got) == 4


# ── D5: per-drone log summary ────────────────────────────────────────────────

class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_frame_log_is_one_summary_per_drone_per_second(caplog):
    clock = FakeClock()
    logger = logging.getLogger("test.frame_summary")
    fl = main._FrameLogSummary(logger, interval_s=1.0, clock=clock)
    with caplog.at_level(logging.INFO, logger="test.frame_summary"):
        for i in range(30):                          # 30 fps for just under one second
            clock.t = 100.0 + i / 30.0
            fl.record("DRN-A", "MSN-1", 1000, 0.010 if i else 0.050, seq=i)
            fl.record("DRN-B", "MSN-2", 500, 0.002, seq=i)
        assert caplog.records == []                  # nothing logged per frame
        clock.t = 101.0
        fl.record("DRN-A", "MSN-1", 1000, 0.010, seq=30)

    assert len(caplog.records) == 1
    r = caplog.records[0]
    assert r.drone_id == "DRN-A" and r.frames == 31 and r.bytes == 31000
    assert r.latency_ms_max == 50.0
    assert r.latency_ms_mean == pytest.approx((0.050 + 30 * 0.010) / 31 * 1000, abs=0.1)
    assert r.last_seq == 30
    msg = r.getMessage()
    assert "frames=31" in msg and "latency_ms_max=50.0" in msg   # readable without JSON logging

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="test.frame_summary"):
        fl.flush(idle_only=True)                     # DRN-B's window started at 100.0 → due
        fl.flush(idle_only=True)                     # nothing left
    assert [(r.drone_id, r.frames) for r in caplog.records] == [("DRN-B", 30)]


def test_frame_log_flush_on_mission_end_logs_partial_window(caplog):
    clock = FakeClock()
    logger = logging.getLogger("test.frame_summary2")
    fl = main._FrameLogSummary(logger, interval_s=1.0, clock=clock)
    with caplog.at_level(logging.INFO, logger="test.frame_summary2"):
        fl.record("DRN-A", "MSN-1", 10, 0.001, seq=1)
        fl.flush(idle_only=True)                     # window too young
        assert caplog.records == []
        fl.flush("DRN-A")
    assert [r.frames for r in caplog.records] == [1]


# ── D7: drone / mission id validation ────────────────────────────────────────

def _edge_drone_id(mission_id, configured=None):
    """Copy of dvas_edge/edge_api_server.py start_mission drone-id derivation."""
    if configured and configured.strip():
        drone_id = configured.strip().upper()
    elif mission_id.startswith("MSN-"):
        drone_id = f"DRN-{mission_id[4:]}"
    else:
        drone_id = f"DRN-{mission_id}"
    drone_id = re.sub(r"[^A-Z0-9_\-]", "-", drone_id.upper())
    if len(drone_id) > 30:
        drone_id = drone_id[:30].rstrip("-_")
    if len(drone_id) < 3:
        drone_id = f"DRN-{drone_id}"[:30]
    return drone_id


EDGE_MISSIONS = [
    "MSN-20261106-2215-08-01",
    "MSN-20261106-2215-08-ALPHA",
    "MSN-20261107-0001-59-KOCHI CITY",     # squadron is free text in MissionControl.js
    "SMOKE-20261106-221500",                # deploy/runpod/tools/smoke_public.py
    "MISSION_20250527_BEACH",
]
EDGE_DRONES = ["DRONE_KL_001", "DRN-SMOKE", "DRN-010", "DRN-0111111111", "drone_kl_002", "DRN-1.2:3"]
BAD_DRONES = ["", "x" * 65, "DRN 1", "DRN/../x", "DRN-1\n", "DRN;rm", "DRNé", None, 7]
BAD_MISSIONS = ["", "m" * 81, "../../etc/passwd", "MSN/1", "MSN-1\n", "MSN-1\x00", "MSN\\1"]


def _meta(drone, mission):
    return {"drone_id": drone, "mission_id": mission, "timestamp": "2026-11-07T10:00:00Z",
            "frame_sequence": 1, "change_score": 0.5}


@pytest.mark.parametrize("mission", EDGE_MISSIONS)
def test_edge_generated_ids_are_accepted(mission):
    for drone in [_edge_drone_id(mission), _edge_drone_id(mission, "drn-kl 7")] + EDGE_DRONES:
        models.FrameMetadata(**_meta(drone, mission))
        models.MissionStartRequest(drone_id=drone, mission_id=mission)
        models.MissionEndRequest(drone_id=drone, mission_id=mission)
        models.GeofenceUpdateRequest(drone_id=drone, mission_id=mission, geofence={})


@pytest.mark.parametrize("drone", BAD_DRONES)
def test_bad_drone_ids_are_rejected(drone):
    for build in (lambda: models.FrameMetadata(**_meta(drone, "MSN-1")),
                  lambda: models.MissionStartRequest(drone_id=drone, mission_id="MSN-1"),
                  lambda: models.MissionEndRequest(drone_id=drone, mission_id="MSN-1"),
                  lambda: models.GeofenceUpdateRequest(drone_id=drone, mission_id="MSN-1", geofence={})):
        with pytest.raises(Exception):
            build()


@pytest.mark.parametrize("mission", BAD_MISSIONS)
def test_bad_mission_ids_are_rejected(mission):
    for build in (lambda: models.FrameMetadata(**_meta("DRN-1", mission)),
                  lambda: models.MissionStartRequest(drone_id="DRN-1", mission_id=mission),
                  lambda: models.MissionEndRequest(drone_id="DRN-1", mission_id=mission)):
        with pytest.raises(Exception):
            build()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    main.app.dependency_overrides[main.validate_token] = lambda: {"preferred_username": "t"}
    try:
        yield TestClient(main.app, raise_server_exceptions=False)
    finally:
        main.app.dependency_overrides.clear()


def test_endpoints_return_422_for_bad_ids(client):
    jpeg = b"\xff\xd8\xff\xe0" + b"0" * 64
    r = client.post("/ingest/frame",
                    files={"frame": ("f.jpg", jpeg, "image/jpeg")},
                    data={"metadata_json": json.dumps(_meta("DRN/../x", "MSN-1"))})
    assert r.status_code == 422, r.text
    r = client.post("/ingest/frame",
                    files={"frame": ("f.jpg", jpeg, "image/jpeg")},
                    data={"metadata_json": json.dumps(_meta("DRN-1", "../MSN"))})
    assert r.status_code == 422, r.text
    # Valid ids get past validation (no queue in this test → 503 from the publish step).
    r = client.post("/ingest/frame",
                    files={"frame": ("f.jpg", jpeg, "image/jpeg")},
                    data={"metadata_json": json.dumps(_meta("DRN-20261106-2215-08-01",
                                                            "MSN-20261106-2215-08-01"))})
    assert r.status_code == 503, r.text
    r = client.post("/ingest/mission/start", json={"drone_id": "x" * 65, "mission_id": "MSN-1"})
    assert r.status_code == 422, r.text
    r = client.post("/ingest/mission/end", json={"drone_id": "DRN-1", "mission_id": "MSN/1"})
    assert r.status_code == 422, r.text
