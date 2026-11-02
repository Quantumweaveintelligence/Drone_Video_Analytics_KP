"""Role / API audit fixes (docs: DVAS_ROLE_AND_API_AUDIT_COMBINED.md): C1, H2, H3, H8, J1, M1-M8.

Uses the in-memory SQLite + MinIO fakes from test_evidence_pipeline.
Run from DVAS_Dashboard/backend:  python -m pytest tests/test_role_audit.py -q
"""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from test_evidence_pipeline import CRO, OPERATOR, SENIOR, env  # noqa: F401  (fixture)

import audit_service
from core import analytics_client, security
from core.security import get_current_user
from models import AIModelSuggestion, Alert, AuditLog, Drone, Evidence, Incident, Mission
from routers import analytics, audit, drones, edge, incidents, ws
from services import audit_sync
from services.websocket_hub import filter_for

ADMIN = {"id": "a1", "email": "ad@test", "name": "Ad", "role": "system_administrator", "jurisdiction": "",
         "mission_scope": []}
OP2 = dict(OPERATOR, id="o2", email="op2@test", mission_scope=[])


@pytest.fixture
def app_env(env, monkeypatch):
    for mod in (incidents, drones, audit_service, audit_sync):
        monkeypatch.setattr(mod, "async_session_factory", env.factory, raising=False)
    audits = []

    async def _audit(**kw):
        audits.append(kw)
        return {"id": f"audit-{len(audits)}", "hash": "h"}
    for mod in (incidents, drones, analytics, edge):
        monkeypatch.setattr(mod, "write_audit_v2", _audit, raising=False)

    async def _no_broadcast(msg):
        return None
    monkeypatch.setattr(incidents.hub, "broadcast", _no_broadcast)
    env.audits = audits
    return env


def client(user):
    app = FastAPI()
    for r in (incidents.router, drones.router, analytics.router, audit.router, edge.router):
        app.include_router(r)
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def seed(env, *rows):
    async def go():
        async with env.factory() as s:
            for r in rows:
                s.add(r)
            await s.commit()
    env.run(go())


def get(env, model, key):
    async def go():
        async with env.factory() as s:
            return await s.get(model, key)
    return env.run(go())


# ── C1: WebSocket ────────────────────────────────────────────────────────────
def test_websocket_rejects_missing_and_bad_tokens(monkeypatch):
    async def fake_user(token):
        if token != "good":
            raise security.AuthError("Invalid token")
        return dict(CRO, exp=None)
    monkeypatch.setattr(security, "user_from_token", fake_user)
    app = FastAPI()
    app.include_router(ws.router)
    c = TestClient(app)
    for url in ("/api/ws", "/api/ws?token=garbage"):
        with pytest.raises(WebSocketDisconnect) as exc:
            with c.websocket_connect(url) as sock:
                sock.receive_json()
        assert exc.value.code == 4401
    with c.websocket_connect("/api/ws", cookies={"dvas_access_token": "good"}) as sock:
        assert sock.receive_json() == {"type": "welcome", "role": "control_room_officer"}


def test_broadcast_filter_per_role():
    kochi = dict(CRO, jurisdiction="Kochi")
    alert = lambda jur, mission=None: {"type": "alert", "alert": {"jurisdiction": jur, "mission_id": mission}}
    assert filter_for(alert("Kochi"), kochi) and filter_for(alert("Kerala"), kochi)
    assert filter_for(alert("Kannur"), kochi) is None
    assert filter_for(alert("Kannur"), SENIOR)
    op = dict(OPERATOR, mission_scope=["M1"])
    assert filter_for(alert("Kochi", "M1"), op) and filter_for(alert("Kochi", "M2"), op) is None
    audit_msg = {"type": "audit_event", "data": {"actor_email": "x"}}
    assert filter_for(audit_msg, SENIOR) and filter_for(audit_msg, kochi) is None and filter_for(audit_msg, op) is None
    rep = {"type": "report_ready", "requested_by": "cro@test"}
    assert filter_for(rep, CRO) and filter_for(rep, dict(CRO, email="other@test")) is None
    tel = {"type": "telemetry", "drones": [{"id": "D1", "jurisdiction": "Kochi"}, {"id": "D2", "jurisdiction": "Kannur"}]}
    assert [d["id"] for d in filter_for(tel, kochi)["drones"]] == ["D1"]
    assert filter_for({"type": "export_decided"}, op) is None          # operators get only their types


# ── H2: incidents ────────────────────────────────────────────────────────────
def _incident(i, by):
    return Incident(id=i, title="t", severity="LOW", category="hardware", created_by=f"Op ({by})",
                    timestamp="2026-11-02T05:00:00Z", status="OPEN", sent_to_ingest=False)


def test_incident_delete_and_clear_are_owner_scoped(app_env):
    seed(app_env, _incident("INC-1", "op@test"), _incident("INC-2", "op2@test"), _incident("INC-3", "op@test"))
    assert client(OP2).delete("/api/incidents/INC-1").status_code == 403
    assert client(OPERATOR).delete("/api/incidents/INC-1").status_code == 200
    r = client(OP2).delete("/api/incidents")
    assert r.json()["deleted_count"] == 1                              # only INC-2
    assert get(app_env, Incident, "INC-3") is not None
    assert client(SENIOR).delete("/api/incidents").json()["deleted_count"] == 1
    assert client(ADMIN).post("/api/incidents", json={"title": "x"}).status_code == 403
    assert [i["id"] for i in client(OPERATOR).get("/api/incidents").json()] == []


# ── H2 / J6: missions ────────────────────────────────────────────────────────
def test_mission_end_and_delete_rules(app_env):
    seed(app_env,
         Mission(mission_id="M1", name="m", status="active", operator_ids=["o1"], jurisdiction="Kochi",
                 started_at="2026-11-02T04:00:00Z"),
         Mission(mission_id="M2", name="m", status="completed", operator_ids=["o1"], jurisdiction="Kochi"),
         Evidence(id="E1", mission_id="M2", ts="2026-11-02T05:00:00Z", attributes={}, objects=[],
                  image_match="none", status="active", integrity="unverified"))
    assert client(OP2).patch("/api/missions/M1/end", json={}).status_code == 403
    assert client(CRO).patch("/api/missions/M1/end", json={}).status_code == 403
    assert client(OPERATOR).patch("/api/missions/M1/end", json={}).json()["status"] == "ended"
    assert client(OPERATOR).delete("/api/missions/M1").status_code == 403
    assert client(SENIOR).delete("/api/missions/M2").status_code == 409       # evidence refers to it
    assert client(SENIOR).delete("/api/missions/M1").status_code == 200
    assert client(CRO).post("/api/missions", json={"mission_id": "M3"}).status_code == 403


def test_drone_list_uses_shared_jurisdiction_rule(app_env):
    seed(app_env,
         Drone(id="DK", status="live", jurisdiction="Kochi"),
         Drone(id="DS", status="live", jurisdiction="Kerala"),
         Drone(id="DN", status="live", jurisdiction="Kannur"))
    ids = {d["id"] for d in client(dict(CRO, jurisdiction="Kochi")).get("/api/drones").json()}
    assert ids == {"DK", "DS"}


# ── Suggestions + analytics policy (H2, M2, M3) ──────────────────────────────
def test_suggestions_owner_scoped_and_deploy_needs_senior(app_env):
    seed(app_env,
         AIModelSuggestion(id="S1", models=["crowd"], suggested_by="Op (op@test)", timestamp="2026-11-02T05:00:00Z"),
         AIModelSuggestion(id="S2", models=["crowd"], suggested_by="Op2 (op2@test)", timestamp="2026-11-02T05:00:00Z"))
    assert [s["id"] for s in client(OPERATOR).get("/api/analytics/suggestions").json()] == ["S1"]
    assert client(OPERATOR).delete("/api/analytics/suggestions/S2").status_code == 403
    assert client(OPERATOR).delete("/api/analytics/suggestions").json()["deleted_count"] == 1
    assert client(CRO).post("/api/analytics/suggestions/S2/status", json={"status": "DEPLOYED_TO_GPU"}).status_code == 403
    assert client(OPERATOR).post("/api/analytics/suggestions/S2/status", json={"status": "ACKNOWLEDGED"}).status_code == 403


def test_analytics_policy_and_post_only_stream_control(app_env):
    assert client(OPERATOR).get("/api/analytics/crowd").status_code == 403
    assert client(ADMIN).get("/api/analytics/crowd").status_code == 403
    assert client(CRO).get("/api/analytics/crowd/stream/start").status_code == 405      # GET no longer changes state


def test_upstream_errors_are_real_http_errors(monkeypatch):
    def handler(request):
        return httpx.Response(500, text="boom")
    real = httpx.AsyncClient

    class Fake(real):
        def __init__(self, *a, **k):
            super().__init__(*a, transport=httpx.MockTransport(handler), **k)
    monkeypatch.setattr(analytics_client.httpx, "AsyncClient", Fake)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(analytics_client._analytics_get("http://svc/x"))
    assert exc.value.status_code == 502
    assert asyncio.run(analytics_client._analytics_get("http://svc/x", soft=True))["error"] == "upstream 500"


# ── M4 / H3: audit ───────────────────────────────────────────────────────────
def _audit_row(i, email, ts):
    return AuditLog(id=i, ts=ts, actor_email=email, actor_role="drone_operator", action="x", hash=f"h{i}",
                    prev_hash="GENESIS", compliance_tags=[])


def test_audit_log_scoped_to_own_entries_for_non_seniors(app_env):
    seed(app_env, _audit_row("A1", "op@test", "2026-11-02T05:00:00+00:00"),
         _audit_row("A2", "sr@test", "2026-11-02T05:01:00+00:00"))
    own = client(OPERATOR).get("/api/audit-logs").json()
    assert [i["id"] for i in own["items"]] == ["A1"]
    assert len(client(SENIOR).get("/api/audit-logs").json()["items"]) == 2
    assert client(OPERATOR).get("/api/audit-logs/trends").status_code == 403
    assert client(CRO).get("/api/audit-logs/A2").status_code == 403


def test_audit_sync_accepts_only_own_rows_and_rechains(app_env, monkeypatch):
    written = []

    async def capture(**kw):
        written.append(kw)
        return {"id": "new"}
    monkeypatch.setattr(audit_service, "write_audit_v2", capture)
    recs = [{"id": "E1", "action": "EDGE_TEST", "actor_email": "op@test", "hash": "forged", "prev_hash": "x",
             "ts": "2020-01-01T00:00:00Z", "severity": "critical"},
            {"id": "E2", "action": "EDGE_TEST", "actor_email": "someone@else"}]
    r = client(OPERATOR).post("/api/audit-logs/sync", json={"records": recs})
    assert r.json() == {"synced": 1, "rejected": 1, "received": 2}
    w = written[0]
    assert w["actor"]["email"] == "op@test" and w["request_id"] == "edge:E1"
    assert w["after_values"]["edge_record"]["hash"] == "forged"      # kept for cross-check, not trusted
    assert "prev_hash" not in w and "hash" not in w
    assert client(SENIOR).post("/api/audit-logs/sync", json={"records": []}).status_code == 403
    assert client(OPERATOR).post("/api/audit-logs/sync",
                                 json={"records": [{"id": str(i)} for i in range(501)]}).status_code == 413


def test_edge_action_log_refuses_reserved_actions(app_env):
    assert client(OPERATOR).post("/api/edge/action-log", json={"action": "evidence_delete"}).status_code == 400
    assert client(CRO).post("/api/edge/action-log", json={"action": "EDGE_CLIP"}).status_code == 403
    assert client(OPERATOR).post("/api/edge/action-log", json={"action": "EDGE_CLIP"}).status_code == 200


# ── H8: per-drone access in the ingest proxy ────────────────────────────────
def test_ingest_drone_access(app_env, monkeypatch):
    import ingest_proxy
    import database
    seed(app_env, Drone(id="D1", status="live", operator_id="o1", jurisdiction="Kochi"),
         Drone(id="D2", status="live", operator_id="o9", jurisdiction="Kannur"))
    run = app_env.run
    with pytest.raises(HTTPException) as exc:
        run(ingest_proxy._check_drone_access("../../etc", SENIOR))
    assert exc.value.status_code == 400
    run(ingest_proxy._check_drone_access("D1", OPERATOR))
    with pytest.raises(HTTPException):
        run(ingest_proxy._check_drone_access("D2", OPERATOR))
    with pytest.raises(HTTPException):
        run(ingest_proxy._check_drone_access("D2", dict(CRO, jurisdiction="Kochi")))
    run(ingest_proxy._check_drone_access("D2", SENIOR))
