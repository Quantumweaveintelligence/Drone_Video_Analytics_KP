"""Drone status when a stream ends (analytics plan A5).

ingest's mission_drained / drone_offline (its /ws/status) and the dashboard's own purge take
the Drone row offline at once; the telemetry pump treats an empty-but-successful active-stream
list as "no streams" instead of skipping stale detection, so the last drone to drain goes
offline too. Real ORM on in-memory SQLite (fixture from test_evidence_pipeline).

Run from DVAS_Dashboard/backend:  python -m pytest tests/test_drone_status.py -q
"""
import json

import httpx
import pytest
from sqlalchemy import select

from test_evidence_pipeline import env  # noqa: F401  (fixture)

import ingest_proxy
from models import Drone
from services import drone_status, telemetry_pump
from services.websocket_hub import hub


@pytest.fixture
def st(env, monkeypatch):
    monkeypatch.setattr(drone_status, "async_session_factory", env.factory)
    monkeypatch.setattr(telemetry_pump, "async_session_factory", env.factory)
    monkeypatch.setattr(telemetry_pump, "_offline_countdown", {})
    env.sent = []

    async def _broadcast(msg):
        env.sent.append(msg)
    monkeypatch.setattr(hub, "broadcast", _broadcast)

    async def _seed():
        async with env.factory() as s:
            s.add_all([Drone(id="DRN-010", status="draining", mission_id="M1", jurisdiction="Kochi"),
                       Drone(id="DRN-011", status="live", mission_id="M2", jurisdiction="Kochi"),
                       Drone(id="DRN-012", status="offline")])
            await s.commit()
    env.run(_seed())

    async def _status():
        async with env.factory() as s:
            return {d.id: d.status for d in (await s.execute(select(Drone))).scalars()}
    env.status = lambda: env.run(_status())
    return env


def test_mark_offline_is_idempotent_and_broadcasts_once(st):
    assert st.run(drone_status.mark_drone_offline("DRN-010", "stream_purged")) is True
    assert st.run(drone_status.mark_drone_offline("DRN-010", "ingest:mission_drained")) is False
    assert st.status()["DRN-010"] == "offline" and st.status()["DRN-011"] == "live"
    assert [(m["type"], m["drone_id"], m["reason"]) for m in st.sent] == [("drone_offline", "DRN-010", "stream_purged")]


def test_unknown_or_offline_drone_changes_nothing(st):
    assert st.run(drone_status.mark_drone_offline("DRN-999", "stream_purged")) is False
    assert st.run(drone_status.mark_drone_offline("DRN-012", "stream_purged")) is False
    assert st.run(drone_status.mark_drone_offline("", "stream_purged")) is False
    assert st.sent == []


def test_mark_offline_clears_the_pump_grace_countdown(st):
    telemetry_pump._offline_countdown["DRN-011"] = 2
    st.run(drone_status.mark_drone_offline("DRN-011", "stream_purged"))
    assert "DRN-011" not in telemetry_pump._offline_countdown


def test_ingest_stream_end_events(st):
    assert st.run(drone_status.handle_ingest_event(
        json.dumps({"event": "mission_drained", "drone_id": "DRN-010", "mission_id": "M1"}))) is True
    assert st.run(drone_status.handle_ingest_event({"event": "drone_offline", "drone_id": "DRN-011"})) is True
    assert st.status() == {"DRN-010": "offline", "DRN-011": "offline", "DRN-012": "offline"}
    assert st.sent[0]["mission_id"] == "M1" and st.sent[0]["reason"] == "ingest:mission_drained"


@pytest.mark.parametrize("msg", [
    {"event": "keepalive"},
    {"event": "frame_received", "drone_id": "DRN-011"},
    {"event": "mission_drained"},
    "not json",
    b"\xff",
    ["mission_drained"],
])
def test_other_ingest_messages_are_ignored(st, msg):
    assert st.run(drone_status.handle_ingest_event(msg)) is False
    assert st.status()["DRN-011"] == "live" and st.sent == []


def test_db_failure_never_raises(st, monkeypatch):
    def broken():
        raise RuntimeError("db down")
    monkeypatch.setattr(drone_status, "async_session_factory", broken)
    assert st.run(drone_status.mark_drone_offline("DRN-011", "stream_purged")) is False


# ── telemetry pump: an empty active list is "no streams" ──────────────────────
class _Stop(Exception):
    pass


def _run_pump(st, monkeypatch, handler, polls):
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))

    async def svc():
        return ""
    monkeypatch.setattr(ingest_proxy, "_get_service_token", svc)
    calls = {"n": 0}

    async def sleep(_s):
        calls["n"] += 1
        if calls["n"] > polls:
            raise _Stop()

    class _Asyncio:
        pass
    fake = _Asyncio()
    fake.sleep = sleep
    monkeypatch.setattr(telemetry_pump, "asyncio", fake)
    with pytest.raises(_Stop):
        st.run(telemetry_pump._telemetry_pump())


def test_pump_takes_the_last_drone_offline_when_no_stream_is_left(st, monkeypatch):
    _run_pump(st, monkeypatch, lambda req: httpx.Response(200, json={"drones": [], "count": 0}),
              polls=telemetry_pump._GRACE_POLLS + 1)
    assert st.status() == {"DRN-010": "offline", "DRN-011": "offline", "DRN-012": "offline"}
    assert sorted(m["drone_id"] for m in st.sent if m["type"] == "drone_offline") == ["DRN-010", "DRN-011"]


def test_pump_keeps_the_grace_period_for_an_empty_list(st, monkeypatch):
    _run_pump(st, monkeypatch, lambda req: httpx.Response(200, json={"drones": [], "count": 0}),
              polls=telemetry_pump._GRACE_POLLS)
    assert st.status()["DRN-011"] == "live"


def test_pump_skips_stale_detection_when_ingest_does_not_answer(st, monkeypatch):
    _run_pump(st, monkeypatch, lambda req: httpx.Response(503, json={"detail": "down"}),
              polls=telemetry_pump._GRACE_POLLS + 3)
    assert st.status()["DRN-010"] == "draining" and st.status()["DRN-011"] == "live"
    assert st.sent == []
