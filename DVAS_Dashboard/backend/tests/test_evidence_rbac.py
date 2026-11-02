"""P0 evidence/report hot-fixes (docs/evidence-reports/02 §P0): RBAC, scope, AAR data, missions.

The DB is replaced by a fake session that records every statement, so these run
without Postgres. Run from DVAS_Dashboard/backend:
    python -m pytest tests/test_evidence_rbac.py -q
"""
import asyncio
import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/test")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

import database
from core import security
from core.security import get_current_user
from models import Evidence, Export, Mission
from routers import alerts, drones, reports

SENIOR = {"id": "s1", "email": "sr@test", "name": "Sr", "role": "senior_officer",
          "jurisdiction": "", "mission_scope": []}
CRO = {"id": "c1", "email": "cro@test", "name": "Cro", "role": "control_room_officer",
       "jurisdiction": "Kochi", "mission_scope": []}
OPERATOR = {"id": "o1", "email": "op@test", "name": "Op", "role": "drone_operator",
            "jurisdiction": "", "mission_scope": []}


# ── Fake DB ───────────────────────────────────────────────────────────────────
class FakeResult:
    def __init__(self, rows):
        self.rows = list(rows)

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None

    def mappings(self):
        return self


class FakeSession:
    """Answers execute() from a queue of row lists; records statements and writes."""

    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        self.db.statements.append(stmt)
        return FakeResult(self.db.results.pop(0) if self.db.results else [])

    async def scalar(self, stmt):
        self.db.statements.append(stmt)
        return 0

    def add(self, obj):
        self.db.added.append(obj)

    async def delete(self, obj):
        self.db.deleted.append(obj)

    async def commit(self):
        self.db.commits += 1


class FakeDB:
    def __init__(self):
        self.results, self.statements, self.added, self.deleted = [], [], [], []
        self.commits = 0

    def __call__(self):
        return FakeSession(self)

    def sql(self, i=-1):
        return str(self.statements[i].compile(dialect=postgresql.dialect()))

    def where(self, i=-1):
        """WHERE clause only (select(Evidence) lists every column, incl. jurisdiction)."""
        return self.sql(i).split("WHERE", 1)[1] if "WHERE" in self.sql(i) else ""


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    for mod in (alerts, drones, reports, database):
        monkeypatch.setattr(mod, "async_session_factory", fake)
    return fake


@pytest.fixture
def audit(monkeypatch):
    calls = []

    async def _audit(**kw):
        calls.append(kw)

    monkeypatch.setattr(alerts, "write_audit_v2", _audit)
    return calls


def make_client(user=None):
    app = FastAPI()
    for r in (alerts.router, drones.router, reports.router):
        app.include_router(r)
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def evidence(**kw):
    base = dict(id="EV1", label="Person", mission_id="M1", jurisdiction="Kochi", drone_id="D1",
                event_type="person_detected", legal_hold=False,
                retention_until="2099-01-01T00:00:00+00:00", primary_image_key="")
    base.update(kw)
    return Evidence(**base)


# ── B6: evidence delete ───────────────────────────────────────────────────────
@pytest.mark.parametrize("user", [OPERATOR, CRO])
def test_delete_evidence_forbidden_below_senior(db, audit, user):
    r = make_client(user).request("DELETE", "/api/evidence/EV1", json={"reason": "x" * 30})
    assert r.status_code == 403
    assert db.deleted == [] and audit == []


def test_delete_evidence_needs_reason_inside_retention(db, audit):
    db.results = [[evidence()]]
    r = make_client(SENIOR).request("DELETE", "/api/evidence/EV1")
    assert r.status_code == 409
    assert db.deleted == []


def test_delete_evidence_rejects_short_reason(db, audit):
    r = make_client(SENIOR).request("DELETE", "/api/evidence/EV1", json={"reason": "too short"})
    assert r.status_code == 422


def test_delete_evidence_with_reason_is_audited(db, audit):
    db.results = [[evidence()]]
    reason = "Duplicate capture confirmed by the investigating officer"
    r = make_client(SENIOR).request("DELETE", "/api/evidence/EV1", json={"reason": reason})
    assert r.status_code == 200
    assert len(db.deleted) == 1
    assert audit[0]["action"] == "evidence_delete"
    assert reason in audit[0]["details"]
    assert audit[0]["before_values"]["id"] == "EV1"


def test_delete_evidence_after_retention_without_reason(db, audit):
    db.results = [[evidence(retention_until="2020-01-01T00:00:00+00:00")]]
    r = make_client(SENIOR).request("DELETE", "/api/evidence/EV1")
    assert r.status_code == 200


def test_delete_evidence_legal_hold_still_blocked(db, audit):
    db.results = [[evidence(legal_hold=True)]]
    r = make_client(SENIOR).request("DELETE", "/api/evidence/EV1", json={"reason": "x" * 30})
    assert r.status_code == 400


# ── B9: bundle download with ?token= ──────────────────────────────────────────
@pytest.fixture
def token_roles(monkeypatch):
    """Make the real get_current_user decode ?token= into the given Keycloak roles."""
    holder = {}
    monkeypatch.setattr(security, "get_jwks", lambda: {})
    monkeypatch.setattr(security.jwt, "decode", lambda *a, **k: {
        "sub": "u1", "email": holder["email"], "realm_access": {"roles": holder["roles"]}})

    def _set(roles, email):
        holder.update(roles=roles, email=email)
    return _set


def _approved_export():
    return Export(id="X1", evidence_id="EV1", evidence_label="Person", jurisdiction="Kochi",
                  requested_by="cro@test", requested_at="2026-11-01T00:00:00+00:00",
                  justification="court", status="approved", decision_by="sr@test",
                  decision_at="2026-11-01T01:00:00+00:00")


@pytest.fixture
def bundle_deps(monkeypatch, audit):
    monkeypatch.setattr(alerts, "_make_65b_pdf", lambda *a: b"%PDF-1.4 test")
    monkeypatch.setattr(alerts, "_get_minio_client", lambda: None)


def test_bundle_senior_via_query_token(db, bundle_deps, token_roles):
    token_roles(["supervisor"], "sr@test")
    db.results = [[], [_approved_export()], [evidence()]]   # [] = user hydration lookup
    r = make_client().get("/api/exports/X1/bundle?token=t")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"


def test_bundle_operator_via_query_token_forbidden(db, bundle_deps, token_roles):
    token_roles(["drone-operator"], "op@test")
    db.results = [[], [_approved_export()]]
    r = make_client().get("/api/exports/X1/bundle?token=t")
    assert r.status_code == 403


def test_bundle_other_cro_forbidden(db, bundle_deps, token_roles):
    token_roles(["control-room"], "someone-else@test")
    db.results = [[], [_approved_export()]]
    r = make_client().get("/api/exports/X1/bundle?token=t")
    assert r.status_code == 403


# ── B8: evidence scope ────────────────────────────────────────────────────────
def test_cro_drone_filter_keeps_jurisdiction_scope(db):
    r = make_client(CRO).get("/api/evidence?drone_id=D9&dedup=false")
    assert r.status_code == 200
    where = db.where()
    assert "evidence.jurisdiction" in where and "evidence.drone_id" in where


def test_operator_with_empty_scope_sees_nothing(db):
    r = make_client(OPERATOR).get("/api/evidence?dedup=false")
    assert r.status_code == 200
    assert "false" in db.where().lower()


def test_operator_scope_limits_missions(db):
    op = dict(OPERATOR, mission_scope=["M1"])
    make_client(op).get("/api/evidence?mission_id=M2&dedup=false")
    where = db.where()
    assert "evidence.mission_id IN" in where and where.count("evidence.mission_id") >= 2


def test_evidence_meta_is_scoped(db):
    make_client(CRO).get("/api/evidence/meta")
    assert db.statements and all("evidence.jurisdiction" in db.where(i) for i in range(len(db.statements)))


# ── B12: AAR data ─────────────────────────────────────────────────────────────
def test_aar_without_window_does_not_borrow_drone_data(db):
    mission = {"mission_id": "M1", "drone_id": "D1", "started_at": "2026-11-01T00:00:00+00:00",
               "ended_at": None}
    alerts_, ev, window = asyncio.run(reports._load_aar_data(FakeSession(db), mission))
    assert (alerts_, ev, window) == ([], [], [])
    assert len(db.statements) == 2
    assert all("drone_id" not in db.where(i) for i in range(2))


def test_aar_window_alerts_are_untagged_same_drone_in_window(db):
    mission = {"mission_id": "M1", "drone_id": "D1", "started_at": "2026-11-01T00:00:00+00:00",
               "ended_at": "2026-11-01T02:00:00+00:00"}
    asyncio.run(reports._load_aar_data(FakeSession(db), mission))
    assert len(db.statements) == 3
    where = db.where(2)
    assert "alerts.drone_id" in where and "alerts.mission_id IS NULL" in where
    assert "alerts.ts >=" in where and "alerts.ts <=" in where


# ── B26: GET /missions is read-only ──────────────────────────────────────────
def test_list_missions_inserts_nothing(db, monkeypatch):
    async def _no_upstream(*a, **k):
        raise AssertionError("GET /missions must not call analytics services")
    monkeypatch.setattr(drones, "_analytics_get", _no_upstream)
    r = make_client(SENIOR).get("/api/missions")
    assert r.status_code == 200
    assert db.added == [] and db.commits == 0


@pytest.mark.parametrize("user", [SENIOR, CRO, OPERATOR])
def test_untracked_missions_admin_only(db, user):
    assert make_client(user).get("/api/missions/untracked").status_code == 403


def test_untracked_missions_lists_without_writing(db, monkeypatch):
    async def _anpr(url, timeout=0):
        return {"missions": ["M1", "M-NEW"]} if "/missions" in url and "intrusion" not in url else {}
    monkeypatch.setattr(drones, "_analytics_get", _anpr)
    monkeypatch.setattr(drones, "ANALYTICS_DATABASE_URL", "")
    db.results = [[], [], [("M1",)]]   # drones, evidence, existing missions
    admin = dict(SENIOR, role="system_administrator")
    r = make_client(admin).get("/api/missions/untracked")
    assert r.json() == {"untracked_mission_ids": ["M-NEW"]}
    assert db.added == [] and db.commits == 0


# ── B1: no hard-coded analytics DB URL ────────────────────────────────────────
def test_image_resolution_skipped_without_analytics_db_url(monkeypatch, caplog):
    from services import evidence_sync

    async def _never(*a, **k):
        raise AssertionError("must not connect without ANALYTICS_DATABASE_URL")
    monkeypatch.setattr(evidence_sync, "ANALYTICS_DATABASE_URL", "")
    monkeypatch.setattr(evidence_sync, "_warned_no_analytics_db", False)
    monkeypatch.setattr(evidence_sync.asyncpg, "connect", _never)
    for _ in range(2):
        keys = asyncio.run(evidence_sync._resolve_image_keys(
            "person_detected", "e1", "", "D1", "2026-11-01T00:00:00+00:00"))
        assert keys == ("", "", "")
    assert sum("ANALYTICS_DATABASE_URL" in r.message for r in caplog.records) == 1
