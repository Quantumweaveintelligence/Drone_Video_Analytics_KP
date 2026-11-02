"""Evidence plan R2–R5: report data, PDF engine, jobs, report types.

Uses the in-memory SQLite + MinIO fakes from test_evidence_pipeline.
Run from DVAS_Dashboard/backend:  python -m pytest tests/test_reports.py -q
"""
import asyncio
import io
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from test_evidence_pipeline import CRO, OPERATOR, SENIOR, client_for, env  # noqa: F401  (fixture)

from models import Alert, Case, CaseEvidence, Evidence, Incident, Mission, Report
from routers import reports as reports_router
from services import analytics_ro, report_data as rd, report_jobs
from services.evidence_integrity import sha256_hex
from services.pdf import mapimg
from services.report_types import REPORT_TYPES, render

pypdf = pytest.importorskip("pypdf")


@pytest.fixture(autouse=True)
def no_analytics_db(monkeypatch):
    """Analytics DB is not reachable in unit tests unless a test provides rows."""
    async def _down(sql, *args):
        raise analytics_ro.AnalyticsUnavailable("not configured in tests")
    monkeypatch.setattr(analytics_ro, "fetch", _down)


def params_for(report_type, raw, user=SENIOR):
    return rd.normalize_params(report_type, raw, user)


def seed(env, *rows):
    async def go():
        async with env.factory() as s:
            for r in rows:
                s.add(r)
            await s.commit()
    env.run(go())


def collect(env, report_type, params):
    async def go():
        async with env.factory() as s:
            return await REPORT_TYPES[report_type]["collect"](s, params)
    return env.run(go())


def pdf_text(data: bytes) -> str:
    return "\n".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(data)).pages)


# ── R2: parameters ───────────────────────────────────────────────────────────
def test_params_ist_periods():
    p = params_for("daily_sitrep", {"date": "2026-11-02"})
    assert (p["start_utc"], p["end_utc"]) == ("2026-11-01T18:30:00+00:00", "2026-11-02T18:30:00+00:00")
    w = params_for("weekly_response", {"week_start": "2026-11-02"})
    assert w["end_day"] == "2026-11-08"
    r = params_for("drone_utilisation", {"period_days": 7, "end_date": "2026-11-08"})
    assert (r["start_day"], r["end_day"]) == ("2026-11-02", "2026-11-08")
    with pytest.raises(rd.ReportInputError):
        params_for("anpr", {"start_date": "2026-11-08", "end_date": "2026-11-01"})
    with pytest.raises(rd.ReportInputError):
        params_for("mission_after_action", {})
    assert p["scope"] == {"role": "senior_officer", "jurisdiction": None}


# ── R2: data ─────────────────────────────────────────────────────────────────
def test_daily_sitrep_respects_ist_midnight_and_scope(env):
    seed(env,
         Alert(id="A-before", category="ai", type="crowd_threshold", severity="high", jurisdiction="Kochi",
               ts="2026-11-01T18:29:00Z"),                          # 23:59 IST on 1 Nov
         Alert(id="A-in", category="ai", type="intrusion_breach", severity="critical", jurisdiction="Kochi",
               ts="2026-11-01T18:31:00Z", acknowledged_at="2026-11-01T18:41:00Z"),   # 00:01 IST on 2 Nov
         Alert(id="A-other", category="ai", type="theft_alert", severity="critical", jurisdiction="Kannur",
               ts="2026-11-02T05:00:00Z"),
         Alert(id="INC-1", category="incident", type="x", ts="2026-11-02T05:00:00Z"))
    data = collect(env, "daily_sitrep", params_for("daily_sitrep", {"date": "2026-11-02"}))
    assert {a["id"] for a in data["alerts"]} == {"A-in", "A-other"}          # no incident copy, IST day
    assert data["ack"]["median_minutes_to_ack"] == 10.0
    cro = collect(env, "daily_sitrep", params_for("daily_sitrep", {"date": "2026-11-02"}, CRO))
    assert {a["id"] for a in cro["alerts"]} == {"A-in"}                      # Kochi only
    assert "error" in data["analytics"]["plates"]                            # analytics down → noted, not fatal


def test_utilisation_clips_hours_and_separates_open_missions(env):
    seed(env,
         Mission(mission_id="M1", name="m", status="completed", drone_id="D1",
                 started_at="2026-11-01T17:30:00Z", ended_at="2026-11-01T20:30:00Z"),   # 1 h before, 2 h inside
         Mission(mission_id="M2", name="m", status="active", drone_id="D1", started_at="2026-11-02T10:00:00Z"),
         Mission(mission_id="M3", name="m", status="completed", drone_id="D2",
                 started_at="2026-10-20T10:00:00Z", ended_at="2026-10-20T11:00:00Z"))   # outside
    p = params_for("drone_utilisation", {"start_date": "2026-11-02", "end_date": "2026-11-02"})
    p["data_cutoff"] = "2026-11-02T12:00:00+00:00"
    data = collect(env, "drone_utilisation", p)
    d1 = next(d for d in data["per_drone"] if d["drone_id"] == "D1")
    assert d1["closed_hours"] == 2.0 and d1["open_missions"] == 1
    assert {m["mission_id"] for m in data["missions"]} == {"M1", "M2"}


def test_weekly_response_counts_only_that_week(env):
    seed(env, *[Alert(id=f"W{i}", category="ai", type="x", severity="high",
                      ts=(datetime(2026, 11, 1, 18, 30, tzinfo=timezone.utc) + timedelta(days=i)).isoformat())
                for i in range(-1, 9)])
    p = params_for("weekly_response", {"week_start": "2026-11-02"})
    p["data_cutoff"] = "2027-01-01T00:00:00+00:00"           # the week is in the future when tests run
    data = collect(env, "weekly_response", p)
    assert len(data["alerts"]) == 7 and [d["total"] for d in data["per_day"]] == [1] * 7


def test_aar_uses_only_mission_data_and_lists_window_alerts_apart(env):
    seed(env,
         Mission(mission_id="M1", name="Pongala", status="completed", drone_id="D1",
                 started_at="2026-11-02T04:00:00Z", ended_at="2026-11-02T06:00:00Z", operator_name="രവി മേനോൻ"),
         Alert(id="T1", category="ai", mission_id="M1", drone_id="D1", type="crowd_threshold", ts="2026-11-02T05:00:00Z"),
         Alert(id="U1", category="ai", mission_id=None, drone_id="D1", type="x", ts="2026-11-02T05:30:00Z"),
         Alert(id="O1", category="ai", mission_id="M9", drone_id="D1", type="x", ts="2026-11-02T05:30:00Z"))
    data = collect(env, "mission_after_action", params_for("mission_after_action", {"mission_id": "M1"}))
    assert [a["id"] for a in data["alerts"]] == ["T1"]
    assert [a["id"] for a in data["window_alerts"]] == ["U1"]
    assert data["mission"]["hours"] == 2.0
    with pytest.raises(rd.ReportInputError):
        collect(env, "mission_after_action", params_for("mission_after_action", {"mission_id": "nope"}))


def test_analytics_rows_flow_into_report(env, monkeypatch):
    async def fake(sql, *args):
        if "FROM plate_reads" in sql and "GROUP BY" not in sql:
            return [{"reads": 12, "plates": 3}]
        if "FROM plate_reads" in sql:
            return [{"plate_text": "KL07AB1234", "reads": 9, "best_conf": 0.93,
                     "first_seen": datetime(2026, 11, 2, 5, tzinfo=timezone.utc), "last_seen": None}]
        return []
    monkeypatch.setattr(analytics_ro, "fetch", fake)
    data = collect(env, "anpr", params_for("anpr", {"start_date": "2026-11-02"}))
    assert data["analytics"]["plates"]["reads"] == 12
    assert data["analytics"]["plates"]["top"][0]["first_seen"] == "2026-11-02T05:00:00+00:00"


def test_detection_quality_precision(env):
    rows = [("true_positive", 0.9)] * 3 + [("false_positive", 0.6)] + [(None, 0.95)]
    seed(env, *[Alert(id=f"Q{i}", category="ai", type="intrusion_breach", rule="intrusion_breach",
                      confidence=c, disposition=d, ts="2026-11-02T05:00:00Z") for i, (d, c) in enumerate(rows)])
    data = collect(env, "detection_quality", params_for("detection_quality", {"start_date": "2026-11-02"}))
    assert data["overall"] == {"alerts": 5, "reviewed": 4, "true_positive": 3, "false_positive": 1,
                               "precision_pct": 75.0}


def test_evidence_register_custody(env):
    seed(env, Case(id="C1", case_number="CR-1", created_by="cro@test", created_at="2026-11-02T05:00:00Z",
                   jurisdiction="Kochi"),
         Evidence(id="E1", event_type="theft_alert", ts="2026-11-02T05:00:00Z", jurisdiction="Kochi",
                  record_sha256="ab" * 32, attributes={}, objects=[], image_match="none", status="active",
                  integrity="ok"),
         CaseEvidence(case_id="C1", evidence_id="E1", added_by="cro@test", added_at="2026-11-02T05:01:00Z"))
    data = collect(env, "evidence_register", params_for("evidence_register", {"case_id": "C1"}))
    assert [e["id"] for e in data["evidence"]] == ["E1"] and data["case"]["case_number"] == "CR-1"
    other = dict(CRO, jurisdiction="Kannur")
    with pytest.raises(rd.ReportInputError):
        collect(env, "evidence_register", params_for("evidence_register", {"case_id": "C1"}, other))


# ── R3: PDF engine ───────────────────────────────────────────────────────────
def test_pdf_keeps_malayalam_and_never_truncates(env):
    seed(env, Mission(mission_id="M1", name="Pongala", status="completed", drone_id="D1",
                      started_at="2026-11-02T04:00:00Z", ended_at="2026-11-02T06:00:00Z", operator_name="രവി മേനോൻ"),
         *[Alert(id=f"A{i:03d}", category="ai", mission_id="M1", type="crowd_threshold", severity="high",
                 lat=9.9 + i / 10000, lon=76.2, ts=(datetime(2026, 11, 2, 4, tzinfo=timezone.utc)
                                                    + timedelta(seconds=10 * i)).isoformat()) for i in range(500)])
    params = params_for("mission_after_action", {"mission_id": "M1"})
    data = collect(env, "mission_after_action", params)
    pdf = render("mission_after_action", data, params, "RPT-T", "CONFIDENTIAL — test")
    text = pdf_text(pdf)
    assert "രവി" in text                                       # Malayalam survives (font + shaping)
    assert "Showing 25 of 500" in text and "Annex" in text      # no silent truncation
    assert "02-11-2026 10:53" in text                         # the last alert (05:23 UTC) is in an annex
    assert "Page 1 of" in text and "IST" in text


def test_map_offline_and_with_mbtiles(tmp_path, monkeypatch):
    pts = [{"lat": 8.5241, "lon": 76.9366, "kind": "evidence", "label": "x"},
           {"lat": 8.5300, "lon": 76.9400, "kind": "alert", "label": "y"}]
    png, caption = mapimg.render_map(pts)
    assert png.startswith(b"\x89PNG") and "no base map" in caption
    assert mapimg.render_map([]) == (None, "")
    # a one-tile MBTiles file covering the points at zoom 10
    from PIL import Image
    db = tmp_path / "kerala.mbtiles"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE tiles (zoom_level INT, tile_column INT, tile_row INT, tile_data BLOB)")
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), (200, 220, 200)).save(buf, "PNG")
    x, y = mapimg._merc(8.527, 76.938)
    for tx in range(int(x * 1024) - 1, int(x * 1024) + 2):
        for ty in range(int(y * 1024) - 1, int(y * 1024) + 2):
            con.execute("INSERT INTO tiles VALUES (10, ?, ?, ?)", (tx, 1023 - ty, buf.getvalue()))
    con.commit()
    con.close()
    monkeypatch.setattr(mapimg, "REPORT_BASEMAP_MBTILES", str(db))
    png2, caption2 = mapimg.render_map(pts)
    assert png2 and "offline base map" in caption2


# ── R4: jobs ─────────────────────────────────────────────────────────────────
@pytest.fixture
def jobs(env, monkeypatch):
    monkeypatch.setattr(report_jobs, "async_session_factory", env.factory)
    monkeypatch.setattr(reports_router, "async_session_factory", env.factory)
    monkeypatch.setattr(report_jobs, "_get_minio_client", lambda: env.s3)
    monkeypatch.setattr(reports_router, "_get_minio_client", lambda: env.s3)
    audit = []

    async def _audit(**kw):
        audit.append(kw)
    monkeypatch.setattr(report_jobs, "write_audit_v2", _audit)
    monkeypatch.setattr(reports_router, "write_audit_v2", _audit)
    return env


def _report(env, rid):
    async def go():
        async with env.factory() as s:
            return await s.get(Report, rid)
    return env.run(go())


def test_job_generates_stores_and_hashes(jobs):
    c = client_for(SENIOR)
    r = c.post("/api/reports/generate", json={"report_type": "daily_sitrep", "params": {"date": "2026-11-02"}})
    assert r.status_code == 200 and r.json()["status"] == "queued"
    rid = r.json()["id"]
    claimed = jobs.run(report_jobs.claim_next())
    assert claimed == rid and _report(jobs, rid).status == "running"
    jobs.run(report_jobs.run_job(rid))
    rep = _report(jobs, rid)
    assert rep.status == "ready" and rep.file_bucket == "dvas-reports"
    stored = jobs.s3._latest("dvas-reports", rep.file_key)["data"]
    assert sha256_hex(stored) == rep.sha256
    d = c.get(f"/api/reports/{rid}/download")
    assert d.status_code == 200 and d.headers["X-Content-SHA256"] == rep.sha256


def test_cro_request_needs_senior_approval(jobs):
    r = client_for(CRO).post("/api/reports/generate",
                             json={"report_type": "intrusion", "params": {"start_date": "2026-11-02"}})
    rid = r.json()["id"]
    assert r.json()["status"] == "requested"
    assert jobs.run(report_jobs.claim_next()) is None                  # not queued yet
    assert client_for(CRO).post(f"/api/reports/{rid}/decision", json={"decision": "approved"}).status_code == 403
    assert client_for(SENIOR).post(f"/api/reports/{rid}/decision", json={"decision": "approved"}).json()["status"] == "queued"
    assert client_for(SENIOR).post(f"/api/reports/{rid}/decision", json={"decision": "rejected"}).status_code == 409
    assert client_for(OPERATOR).post("/api/reports/generate", json={"report_type": "crowd"}).status_code == 403
    assert [t["mode"] for t in client_for(CRO).get("/api/reports/types").json()][0] == "request"


def test_interrupted_job_is_failed_not_stuck(jobs):
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    seed(jobs, Report(id="R-stuck", report_type="daily_sitrep", title="t", requested_by="sr@test",
                      requested_at=old, status="running", started_at=old, params={}, attempts=1))
    assert jobs.run(report_jobs.recover_interrupted()) == 1
    rep = _report(jobs, "R-stuck")
    assert rep.status == "failed" and "interrupted" in rep.error_message


def test_failed_generation_is_recorded(jobs, monkeypatch):
    seed(jobs, Report(id="R-bad", report_type="daily_sitrep", title="t", requested_by="sr@test",
                      requested_at=datetime.now(timezone.utc).isoformat(), status="queued",
                      params={"broken": True}, attempts=0))
    rid = jobs.run(report_jobs.claim_next())
    jobs.run(report_jobs.run_job(rid))
    assert _report(jobs, "R-bad").status == "failed"


def test_preview_matches_report_data(jobs):
    seed(jobs, Alert(id="P1", category="ai", type="x", severity="low", ts="2026-11-02T05:00:00Z"))
    prev = client_for(SENIOR).get("/api/reports/daily_sitrep/preview-data?date=2026-11-02").json()
    assert [a["id"] for a in prev["data"]["alerts"]] == ["P1"]
    assert prev["params"]["start_utc"] == "2026-11-01T18:30:00+00:00"
    assert client_for(SENIOR).get("/api/reports/nope/preview-data").status_code == 400
