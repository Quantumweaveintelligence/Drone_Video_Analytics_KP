"""Admin settings / sessions / compliance, audit API additions (AUD-01/03/04/10/12),
report retention and the de-mocked NOC telemetry (ING-03…10).

Uses the in-memory SQLite + MinIO fakes from test_evidence_pipeline.
Run from DVAS_Dashboard/backend:  python -m pytest tests/test_admin_settings.py -q
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_evidence_pipeline import CRO, SENIOR, env  # noqa: F401  (fixture)

import audit_service
from core import analytics_client
from core.security import get_current_user
from models import AuditLog, Drone, Report
from routers import admin, audit
from services import retention, settings_service, system_telemetry

ADMIN = {"id": "a1", "email": "ad@test", "name": "Ad", "role": "system_administrator", "jurisdiction": "",
         "mission_scope": []}


@pytest.fixture
def app_env(env, monkeypatch):
    for mod in (admin, audit_service, settings_service, system_telemetry):
        monkeypatch.setattr(mod, "async_session_factory", env.factory, raising=False)
    monkeypatch.setattr(settings_service, "_cache", {})
    monkeypatch.setattr(settings_service, "_cache_at", 0.0)
    audits = []

    async def _audit(**kw):
        audits.append(kw)
        return {"id": f"audit-{len(audits)}", "hash": "h"}
    for mod in (admin, audit_service):
        monkeypatch.setattr(mod, "write_audit_v2", _audit, raising=False)
    env.audits = audits
    return env


def client(user):
    app = FastAPI()
    for r in (admin.router, audit.router):
        app.include_router(r)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def seed(env, *rows):
    async def go():
        async with env.factory() as s:
            for r in rows:
                s.add(r)
            await s.commit()
    env.run(go())


def log_row(i, **kw):
    base = dict(id=f"log-{i}", ts=datetime.now(timezone.utc).isoformat(), actor_email="so@test",
                actor_role="senior_officer", action="x", category="data", severity="info", status="success",
                hash=f"h{i}", prev_hash="")
    base.update(kw)
    return AuditLog(**base)


# ── Settings ─────────────────────────────────────────────────────────────────
def test_settings_validation_roles_and_audit(app_env):
    so, sa = client(SENIOR), client(ADMIN)
    body = so.get("/api/admin/settings").json()
    assert body["editable"] is False and {s["key"] for s in body["settings"]} >= {"evidence_retention_days"}
    assert client(CRO).get("/api/admin/settings").status_code == 403
    assert so.put("/api/admin/settings", json={"report_retention_days": 90}).status_code == 403

    assert sa.put("/api/admin/settings", json={"audit_retention_days": 100}).status_code == 400   # statutory floor
    assert sa.put("/api/admin/settings", json={"retention_job_time_ist": "25:00"}).status_code == 400
    assert sa.put("/api/admin/settings", json={"no_such_key": 1}).status_code == 400

    r = sa.put("/api/admin/settings", json={"report_retention_days": 90, "retention_job_time_ist": "03:15"})
    assert r.status_code == 200 and r.json()["changed"]["report_retention_days"]["after"] == 90
    assert app_env.run(settings_service.get("report_retention_days")) == 90
    entry = app_env.audits[-1]
    assert entry["action"] == "system_settings_update" and entry["after_values"]["retention_job_time_ist"] == "03:15"

    n = len(app_env.audits)
    sa.put("/api/admin/settings", json={"report_retention_days": 90})        # no change → no audit entry
    assert len(app_env.audits) == n


def test_compliance_cards_are_computed(app_env):
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    seed(app_env, log_row(1), log_row(2, status="failure"), log_row(3, category="configuration"),
         log_row(4, ts=old))
    cards = {c["key"]: c for c in client(SENIOR).get("/api/admin/compliance").json()["cards"]}
    assert cards["audit_events_7d"]["value"] == 3
    assert cards["failed_actions_7d"]["value"] == 1
    assert cards["config_changes_7d"]["value"] == 1
    assert client(CRO).get("/api/admin/compliance").status_code == 403


def test_sessions_are_admin_only(app_env, monkeypatch):
    async def fake_sessions():
        return [{"id": "s1", "username": "x"}]
    monkeypatch.setattr(admin, "list_active_sessions", fake_sessions)
    assert client(SENIOR).get("/api/admin/sessions").status_code == 403
    assert client(ADMIN).get("/api/admin/sessions").json() == [{"id": "s1", "username": "x"}]


# ── Audit API additions ──────────────────────────────────────────────────────
def test_legal_hold_accepts_ui_fields_and_release_needs_reason(app_env):
    seed(app_env, log_row(1))
    so = client(SENIOR)
    r = so.post("/api/audit-logs/log-1/legal-hold", json={
        "case_number": "CR-12/2026", "justification": "Court order", "legal_authority": "CrPC s.91",
        "contact_email": "io@keralapolice.gov.in"})
    assert r.status_code == 200, r.text
    assert r.json()["legal_authority"] == "CrPC s.91" and r.json()["hold_reason"] == "Court order"
    assert so.post("/api/audit-logs/log-1/legal-hold", json={"case_number": "X"}).status_code == 422

    held = so.get("/api/audit-logs", params={"is_legal_hold": "true"}).json()["items"]
    assert held[0]["legal_hold"]["contact_email"] == "io@keralapolice.gov.in"

    assert so.request("DELETE", "/api/audit-logs/log-1/legal-hold", json={}).status_code == 422
    r = so.request("DELETE", "/api/audit-logs/log-1/legal-hold", json={"release_notes": "Case closed"})
    assert r.status_code == 200
    assert any(a["action"] == "audit_legal_hold_release" and "Case closed" in a["details"] for a in app_env.audits)


def test_summary_honours_filters_and_session_filter(app_env):
    seed(app_env, log_row(1, severity="critical", actor_session_id="sess-a"), log_row(2), log_row(3))
    so = client(SENIOR)
    assert so.get("/api/audit-logs/summary").json()["total_events"] == 3
    s = so.get("/api/audit-logs/summary", params={"severity": "critical"}).json()
    assert s["total_events"] == 1 and s["critical_events"] == 1
    assert so.get("/api/audit-logs", params={"actor_session_id": "sess-a"}).json()["total"] == 1
    assert so.get("/api/audit-logs", params={"actor_session_id": "nope"}).json()["total"] == 0


def test_trends_range_alias_today_and_invalid(app_env):
    so = client(SENIOR)
    assert so.get("/api/audit-logs/trends", params={"range_val": "7d"}).json()["range"] == "7d"
    assert so.get("/api/audit-logs/trends", params={"range": "today"}).json()["range"] == "today"
    assert so.get("/api/audit-logs/trends", params={"range": "1y"}).status_code == 400


# ── Report retention ─────────────────────────────────────────────────────────
def test_report_retention_expires_old_pdfs(app_env, monkeypatch):
    now = datetime.now(timezone.utc)
    app_env.s3.put("dvas-evidence", "reports/old.pdf", b"%PDF")
    seed(app_env,
         Report(id="r-old", report_type="daily_summary", title="t", requested_by="so@test", requested_at=now, status="ready", file_bucket="dvas-evidence",
                file_key="reports/old.pdf", generated_at=now - timedelta(days=400)),
         Report(id="r-new", report_type="daily_summary", title="t", requested_by="so@test", requested_at=now, status="ready", file_bucket="dvas-evidence",
                file_key="reports/new.pdf", generated_at=now - timedelta(days=1)))
    monkeypatch.setattr(analytics_client, "_get_minio_client", lambda: app_env.s3)
    assert app_env.run(retention.run_report_retention()) == 1

    async def statuses():
        async with app_env.factory() as s:
            return (await s.get(Report, "r-old")).status, (await s.get(Report, "r-new")).status
    assert app_env.run(statuses()) == ("expired", "ready")
    assert retention._due(datetime(2026, 1, 2, 2, 31), "02:30", None)
    assert not retention._due(datetime(2026, 1, 2, 2, 29), "02:30", None)


# ── NOC telemetry: measured values only ──────────────────────────────────────
def test_stream_overview_has_no_nominal_defaults(app_env, monkeypatch):
    seed(app_env, Drone(id="D1", status="idle", lat=10.0, lng=76.0, battery_pct=80),
         Drone(id="D2", status="idle", lat=0.0, lng=0.0, battery_pct=180))
    fresh = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    counters = {"frames_received": 100, "frames_rejected": 1, "bytes_ingested": 1_000_000, "last_frame_at": fresh}
    payload = {"total_frames_ingested": 99,
               "drones": [{"drone_id": "D1", "ingest_stats": counters, "queue": {"messages_ready": 4}}]}

    async def fake_get(path, timeout=5.0):
        return payload
    monkeypatch.setattr(system_telemetry, "_proxy_get", fake_get)
    for name in ("_PREV_COUNTERS", "_RATES"):
        monkeypatch.setattr(system_telemetry, name, {})
    monkeypatch.setattr(system_telemetry, "_status_cache", {"ts": 0.0, "data": None})

    streams, stats = app_env.run(system_telemetry.collect_stream_overview())
    by_id = {s["drone_id"]: s for s in streams}
    assert by_id["D1"]["status"] == "live" and by_id["D1"]["fps"] is None      # first sample: not measured yet
    assert by_id["D1"]["operator"] is None and by_id["D1"]["mission_id"] is None
    assert by_id["D1"]["latency_ms"] is None and by_id["D2"]["status"] == "idle"

    # second poll: rates come from counter deltas
    system_telemetry._PREV_COUNTERS["D1"] = (system_telemetry._PREV_COUNTERS["D1"][0] - 2.0, 50, 500_000)
    monkeypatch.setattr(system_telemetry, "_status_cache", {"ts": 0.0, "data": None})
    streams, stats = app_env.run(system_telemetry.collect_stream_overview())
    d1 = {s["drone_id"]: s for s in streams}["D1"]
    assert d1["fps"] == 25.0 and d1["bitrate_mbps"] == 2.0 and stats["avg_fps"] == 25.0

    frames = app_env.run(system_telemetry.collect_frame_ingestion())
    assert frames["total_frames"] == 100 and frames["kept_frames"] == 99 and frames["dropped_frames"] == 1

    meta = app_env.run(system_telemetry.collect_metadata_validation())
    assert meta["records_checked"] == 2 and meta["gps_valid_pct"] == 50.0 and meta["sensor_valid_pct"] == 50.0
    assert meta["timestamp_valid_pct"] == 100.0

    queues = {q["name"]: q for q in app_env.run(system_telemetry.collect_queue_metrics())}
    assert queues["ingest"]["depth"] == 4 and queues["detection"]["depth"] is None


def test_unreachable_ingest_reports_unknown_not_defaults(app_env, monkeypatch):
    async def down(path, timeout=5.0):
        raise RuntimeError("down")
    monkeypatch.setattr(system_telemetry, "_proxy_get", down)
    monkeypatch.setattr(system_telemetry, "_status_cache", {"ts": 0.0, "data": None})
    frames = app_env.run(system_telemetry.collect_frame_ingestion())
    assert frames["available"] is False and frames["total_frames"] is None
    trend = system_telemetry.update_trend_history(None, None)
    assert trend[-1]["cpu"] is None and len(trend) <= 24
