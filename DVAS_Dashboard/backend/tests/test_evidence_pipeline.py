"""Evidence plan E1–E5: sync, vault, integrity, case export, access API.

Runs the real ORM on in-memory SQLite (JSONB compiled as JSON) and an in-memory
MinIO fake that enforces object lock and legal hold. No Postgres/MinIO needed.
Run from DVAS_Dashboard/backend:
    python -m pytest tests/test_evidence_pipeline.py -q
"""
import asyncio
import io
import json
import os
import zipfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/test")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

import database
from core.security import get_current_user
from models import Base, Case, CaseEvidence, Evidence, EvidenceSyncState, Export
from routers import alerts, cases, downloads, infra, reports
from services import evidence_sync, evidence_vault, exports as exports_svc, retention
from services.evidence_integrity import canonical_json, record_sha256, sha256_hex


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw):
    return "JSON"


SENIOR = {"id": "s1", "email": "sr@test", "name": "Sr", "role": "senior_officer", "jurisdiction": "", "mission_scope": []}
CRO = {"id": "c1", "email": "cro@test", "name": "Cro", "role": "control_room_officer", "jurisdiction": "Kochi",
       "mission_scope": []}
ADMIN = {"id": "a1", "email": "ad@test", "name": "Ad", "role": "system_administrator", "jurisdiction": "",
         "mission_scope": []}
OPERATOR = {"id": "o1", "email": "op@test", "name": "Op", "role": "drone_operator", "jurisdiction": "",
            "mission_scope": ["M1"]}


# ── In-memory MinIO with object lock ─────────────────────────────────────────
class FakeS3Error(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class _Resp:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data

    def close(self):
        pass

    def release_conn(self):
        pass


class _Listed:
    def __init__(self, name, version):
        self.object_name, self.version_id = name, version


class FakeMinio:
    def __init__(self):
        self.buckets = {}          # name -> {"lock": bool, "objects": {key: [version dicts]}}
        self.version = 0

    def bucket_exists(self, b):
        return b in self.buckets

    def make_bucket(self, b, object_lock=False):
        self.buckets[b] = {"lock": object_lock, "objects": {}}

    def set_object_lock_config(self, b, cfg):
        pass

    def get_object_lock_config(self, b):
        if not self.buckets[b]["lock"]:
            from minio.error import S3Error
            raise S3Error("ObjectLockConfigurationNotFoundError", "no lock", "", "", "", None)
        return object()

    def put(self, bucket, key, data, **meta):
        self.buckets.setdefault(bucket, {"lock": False, "objects": {}})
        self.version += 1
        self.buckets[bucket]["objects"].setdefault(key, []).append(
            {"data": data, "version": f"v{self.version}", "hold": False, "until": None, **meta})

    def put_object(self, bucket, key, stream, length, content_type="", metadata=None, retention=None,
                   legal_hold=False):
        until = retention.retain_until_date if retention is not None else None
        self.put(bucket, key, stream.read(), hold=legal_hold, until=until, metadata=metadata or {})

    def _latest(self, bucket, key):
        try:
            return self.buckets[bucket]["objects"][key][-1]
        except KeyError:
            raise FakeS3Error("NoSuchKey")

    def get_object(self, bucket, key):
        return _Resp(self._latest(bucket, key)["data"])

    def enable_object_legal_hold(self, bucket, key):
        self._latest(bucket, key)["hold"] = True

    def disable_object_legal_hold(self, bucket, key):
        self._latest(bucket, key)["hold"] = False

    def list_objects(self, bucket, prefix="", recursive=True, include_version=False):
        out = []
        for key, versions in self.buckets.get(bucket, {}).get("objects", {}).items():
            if key.startswith(prefix):
                out += [_Listed(key, v["version"]) for v in versions]
        return out

    def remove_object(self, bucket, key, version_id=None):
        versions = self.buckets[bucket]["objects"][key]
        v = next(v for v in versions if v["version"] == version_id)
        if v["hold"] or (v["until"] and v["until"] > datetime.now(timezone.utc)):
            raise FakeS3Error("AccessDenied: object is WORM protected")
        versions.remove(v)
        if not versions:
            del self.buckets[bucket]["objects"][key]


# ── Fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture
def env(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(_create())

    for mod in (database, alerts, cases, reports, evidence_sync, exports_svc, retention):
        monkeypatch.setattr(mod, "async_session_factory", factory)
    audit = []

    async def _audit(**kw):
        audit.append(kw)
    for mod in (alerts, cases, reports, evidence_sync, exports_svc, retention):
        monkeypatch.setattr(mod, "write_audit_v2", _audit)

    s3 = FakeMinio()
    monkeypatch.setattr(evidence_vault, "_get_minio_client", lambda: s3)
    monkeypatch.setattr(evidence_vault, "_vault_locked", None)
    monkeypatch.setattr(infra, "_get_minio_client", lambda: s3)
    from minio import error as minio_error
    monkeypatch.setattr(minio_error, "S3Error", FakeS3Error)
    monkeypatch.setattr(alerts, "_last_view_audit", {})

    class Env:
        pass
    e = Env()
    e.engine, e.factory, e.audit, e.s3 = engine, factory, audit, s3
    e.run = lambda coro: asyncio.run(coro)
    yield e
    asyncio.run(engine.dispose())


def client_for(user=None):
    app = FastAPI()
    for r in (alerts.router, cases.router, downloads.router, infra.router, reports.router):
        app.include_router(r)
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def source_upload(s3, bucket, key, data):
    s3.put(bucket, key, data, metadata={"sha256": sha256_hex(data)})
    return {"bucket": bucket, "key": key, "content_type": "image/jpeg", "sha256": sha256_hex(data),
            "bytes": len(data), "kind": "plate_crop" if "plate" in key else "source_frame"}


def make_event(s3, event_id="11111111-1111-1111-1111-111111111111", ts="2026-11-02T10:00:00+00:00",
               mission="M1", with_block=True, plate=False):
    objs = [source_upload(s3, "dvas-intrusion", f"intrusion/D1/{event_id}_src.jpg", b"frame-" + event_id.encode())]
    if plate:
        objs.append(dict(source_upload(s3, "dvas-plates", f"plates/{event_id}.jpg", b"plate"), role="secondary"))
    attrs = {"zone_id": "Z1", "mission_id": mission}
    if with_block:
        attrs["evidence"] = {"schema": 1, "objects": [dict(o, role=o.get("role", "primary")) for o in objs],
                             "frame": {"frame_sequence": 7, "capture_ts": 1793613600.5},
                             "geo": {"lat": 9.9, "lon": 76.2}, "detection": {"track_id": 3},
                             "model": {"service": "intrusion", "weights_sha256": "w" * 64}}
    return {"event_id": event_id, "mission_id": mission, "drone_id": "D1", "track_id": None,
            "event_type": "intrusion_breach", "confidence": 0.9, "ts": ts, "latitude": 9.9, "longitude": 76.2,
            "attributes": attrs}


async def _add_evidence(env, ev):
    async with env.factory() as session:
        row = await evidence_sync.create_evidence(session, ev)
        await session.commit()
        return row


async def _get(env, model, key):
    async with env.factory() as session:
        return await session.get(model, key)


# ── E1: exact capture + sync cursor ──────────────────────────────────────────
def test_event_with_block_becomes_exact_evidence_in_vault(env):
    row = env.run(_add_evidence(env, make_event(env.s3, plate=True)))
    assert row.image_match == "exact" and row.vault_status == "copied" and row.integrity == "ok"
    assert row.capture_ts.startswith("2026-11-02T10:00:00")
    assert (row.lat, row.lon) == (9.9, 76.2)
    assert row.primary_image_key == f"evidence/{row.id}/primary.jpg"
    assert row.image_bucket == evidence_vault.VAULT
    vault = env.s3.buckets[evidence_vault.VAULT]["objects"]
    assert set(vault) == {f"evidence/{row.id}/primary.jpg", f"evidence/{row.id}/secondary.jpg"}
    assert row.objects[0]["source"]["bucket"] == "dvas-intrusion"
    assert row.record_sha256 == record_sha256({c.name: getattr(row, c.name) for c in row.__table__.columns})


def test_legacy_event_without_match_gets_no_image(env, monkeypatch):
    monkeypatch.setattr(evidence_sync, "ANALYTICS_DATABASE_URL", "")
    row = env.run(_add_evidence(env, make_event(env.s3, with_block=False)))
    assert row.image_match == "none" and row.objects == [] and row.primary_image_key == ""
    assert row.vault_status == "none"


def test_source_hash_mismatch_is_flagged(env):
    ev = make_event(env.s3)
    env.s3.put("dvas-intrusion", ev["attributes"]["evidence"]["objects"][0]["key"], b"tampered")
    row = env.run(_add_evidence(env, ev))
    assert row.integrity == "mismatch" and row.objects[0]["hash_check"] == "mismatch"


class FakeGateway:
    """Serves /events/confirmed with (ts, event_id) cursor semantics."""

    def __init__(self, events):
        self.events = sorted(events, key=lambda e: (e["ts"], e["event_id"]))
        self.calls = []

    async def get(self, url, params=None, headers=None):
        self.calls.append((params, headers))
        after = (params["after_ts"], params["after_id"] or "")
        out = [e for e in self.events
               if (datetime.fromisoformat(e["ts"]), e["event_id"]) > (datetime.fromisoformat(after[0]), after[1])]

        class R:
            status_code = 200

            def json(self_inner):
                return {"events": out[: params["limit"]]}
        return R()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def test_sync_cursor_pages_identical_ts_exactly_once(env, monkeypatch):
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    events = [make_event(env.s3, event_id=f"00000000-0000-0000-0000-{i:012d}", ts=ts) for i in range(250)]
    gw = FakeGateway(events)
    monkeypatch.setattr(evidence_sync.httpx, "AsyncClient", lambda **kw: gw)
    monkeypatch.setattr(evidence_sync, "_service_headers", lambda: {"X-Internal-Service-Key": "k"})

    assert env.run(evidence_sync._sync_once()) == 250
    assert all(p["order"] == "asc" for p, _ in gw.calls)
    assert gw.calls[0][1] == {"X-Internal-Service-Key": "k"}
    # restart: resumes from the stored cursor, nothing duplicated
    assert env.run(evidence_sync._sync_once()) == 0
    state = env.run(_get(env, EvidenceSyncState, 1))
    assert state.last_event_id == events[-1]["event_id"]

    async def count():
        from sqlalchemy import func, select
        async with env.factory() as s:
            return (await s.execute(select(func.count()).select_from(Evidence))).scalar()
    assert env.run(count()) == 250
    assert len([a for a in env.audit if a["action"] == "evidence_create"]) == 250


def test_late_event_inside_overlap_is_picked_up(env, monkeypatch):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    first = make_event(env.s3, event_id="00000000-0000-0000-0000-000000000002", ts=now.isoformat())
    gw = FakeGateway([first])
    monkeypatch.setattr(evidence_sync.httpx, "AsyncClient", lambda **kw: gw)
    env.run(evidence_sync._sync_once())
    # committed later, but with an earlier ts (another analyzer process)
    gw.events.append(make_event(env.s3, event_id="00000000-0000-0000-0000-000000000001",
                                ts=(now - timedelta(seconds=30)).isoformat()))
    gw.events.sort(key=lambda e: (e["ts"], e["event_id"]))
    assert env.run(evidence_sync._sync_once()) == 1


# ── E2: legal hold, disposal, retention ──────────────────────────────────────
def test_legal_hold_reaches_vault_and_blocks_removal(env):
    row = env.run(_add_evidence(env, make_event(env.s3)))
    c = client_for(SENIOR)
    assert c.post(f"/api/evidence/{row.id}/legal-hold", json={"reason": "court"}).status_code == 200
    key = f"evidence/{row.id}/primary.jpg"
    assert env.s3._latest(evidence_vault.VAULT, key)["hold"] is True
    assert evidence_vault.remove_prefix(f"evidence/{row.id}/")          # refused while held
    assert c.request("DELETE", f"/api/evidence/{row.id}/legal-hold", json={"reason": "case closed"}).status_code == 200
    assert env.s3._latest(evidence_vault.VAULT, key)["hold"] is False


def test_delete_is_disposal_not_hard_delete(env):
    row = env.run(_add_evidence(env, make_event(env.s3)))
    r = client_for(SENIOR).request("DELETE", f"/api/evidence/{row.id}",
                                   json={"reason": "Duplicate capture confirmed by the officer"})
    assert r.status_code == 200 and r.json()["status"] == "disposed"
    stored = env.run(_get(env, Evidence, row.id))
    assert stored.status == "disposed" and stored.disposed_by == "sr@test"
    assert f"evidence/{row.id}/primary.jpg" in env.s3.buckets[evidence_vault.VAULT]["objects"]
    listed = client_for(SENIOR).get("/api/evidence?dedup=false").json()
    assert listed["total"] == 0


def _expire(env, ev_id, hold=False):
    async def go():
        async with env.factory() as s:
            e = await s.get(Evidence, ev_id)
            e.retention_until = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            e.legal_hold = hold
            await s.commit()
    env.run(go())
    for versions in env.s3.buckets[evidence_vault.VAULT]["objects"].values():
        for v in versions:
            v["until"] = datetime.now(timezone.utc) - timedelta(days=1)


def test_retention_dry_run_and_run(env):
    expired = env.run(_add_evidence(env, make_event(env.s3, event_id="00000000-0000-0000-0000-00000000000a")))
    held = env.run(_add_evidence(env, make_event(env.s3, event_id="00000000-0000-0000-0000-00000000000b")))
    fresh = env.run(_add_evidence(env, make_event(env.s3, event_id="00000000-0000-0000-0000-00000000000c")))
    _expire(env, expired.id)
    _expire(env, held.id, hold=True)

    preview = env.run(retention.run_retention(dry_run=True))
    assert preview["dispose"] == [expired.id]
    assert env.run(_get(env, Evidence, expired.id)).status == "active"      # dry run changed nothing

    env.run(retention.run_retention())
    assert env.run(_get(env, Evidence, expired.id)).status == "disposed"
    assert env.run(_get(env, Evidence, held.id)).status == "active"
    assert env.run(_get(env, Evidence, fresh.id)).status == "active"
    env.run(retention.run_retention())                                       # second pass removes objects
    assert env.run(_get(env, Evidence, expired.id)).vault_status == "removed"
    assert not any(k.startswith(f"evidence/{expired.id}/") for k in env.s3.buckets[evidence_vault.VAULT]["objects"])


# ── E3: integrity ────────────────────────────────────────────────────────────
def test_canonical_json_is_key_order_independent():
    assert canonical_json({"b": 1, "a": {"y": 2, "x": "ü"}}) == canonical_json({"a": {"x": "ü", "y": 2}, "b": 1})
    base = {"id": "E1", "objects": [], "attributes": {"k": 1}}
    assert record_sha256(base) == record_sha256(dict(base, legal_hold=True, status="disposed"))
    assert record_sha256(base) != record_sha256(dict(base, attributes={"k": 2}))


def test_verify_detects_tampered_vault_object(env):
    row = env.run(_add_evidence(env, make_event(env.s3)))
    c = client_for(SENIOR)
    assert c.get(f"/api/evidence/{row.id}/verify").json()["integrity"] == "ok"
    env.s3.put(evidence_vault.VAULT, f"evidence/{row.id}/primary.jpg", b"overwritten")
    res = c.get(f"/api/evidence/{row.id}/verify").json()
    assert res["integrity"] == "mismatch" and not res["objects"][0]["ok"]
    assert client_for(CRO).get(f"/api/evidence/{row.id}/verify").status_code == 403


# ── E4: cases, exports, bundle ───────────────────────────────────────────────
def _case_with(env, *ev_ids):
    c = client_for(CRO)
    case = c.post("/api/cases", json={"case_number": "CR-12/2026", "fir_number": "FIR 77"}).json()
    for ev_id in ev_ids:
        assert c.post(f"/api/cases/{case['id']}/evidence", json={"evidence_id": ev_id}).status_code == 201
    return case


def test_case_export_bundle_built_once_and_verifiable(env):
    e1 = env.run(_add_evidence(env, make_event(env.s3, event_id="00000000-0000-0000-0000-0000000000e1", plate=True)))
    e2 = env.run(_add_evidence(env, make_event(env.s3, event_id="00000000-0000-0000-0000-0000000000e2")))
    case = _case_with(env, e1.id, e2.id)
    exp = client_for(CRO).post("/api/exports", json={"case_id": case["id"], "justification": "court"}).json()
    assert exp["status"] == "pending"

    r = client_for(SENIOR).post(f"/api/exports/{exp['id']}/decision", json={"decision": "approved"})
    assert r.status_code == 200
    stored = env.run(_get(env, Export, exp["id"]))
    assert stored.evidence_ids == [e1.id, e2.id] and stored.bundle_sha256

    d1 = client_for(CRO).get(f"/api/exports/{exp['id']}/bundle")
    d2 = client_for(SENIOR).get(f"/api/exports/{exp['id']}/bundle")
    assert d1.status_code == d2.status_code == 200
    assert d1.content == d2.content and sha256_hex(d1.content) == stored.bundle_sha256
    assert d1.headers["X-Bundle-SHA256"] == stored.bundle_sha256

    z = zipfile.ZipFile(io.BytesIO(d1.content))
    manifest = json.loads(z.read("manifest.json"))
    for f in manifest["files"]:
        assert sha256_hex(z.read(f["path"])) == f["sha256"]
    for line in z.read("SHA256SUMS.txt").decode().splitlines():
        digest, name = line.split("  ", 1)
        assert sha256_hex(z.read(name)) == digest
    assert {e["id"] for e in manifest["evidence"]} == {e1.id, e2.id}
    assert manifest["export"]["self_approved"] is False
    assert z.read("certificate_BSA63.pdf").startswith(b"%PDF")
    assert env.s3._latest(evidence_vault.VAULT, stored.bundle_key)["hold"] is True

    # B15: a decision is final; the case is frozen once exported
    again = client_for(SENIOR).post(f"/api/exports/{exp['id']}/decision", json={"decision": "rejected"})
    assert again.status_code == 409
    e3 = env.run(_add_evidence(env, make_event(env.s3, event_id="00000000-0000-0000-0000-0000000000e3")))
    assert client_for(CRO).post(f"/api/cases/{case['id']}/evidence", json={"evidence_id": e3.id}).status_code == 409


def test_bundle_refused_when_vault_object_changed(env):
    e1 = env.run(_add_evidence(env, make_event(env.s3)))
    env.s3.put(evidence_vault.VAULT, f"evidence/{e1.id}/primary.jpg", b"overwritten")
    r = client_for(SENIOR).post(f"/api/evidence/{e1.id}/direct-export",
                                json={"justification": "court", "case_number": "CR-1"})
    assert r.status_code == 200 and "mismatch" in r.json()["bundle_error"]
    assert client_for(SENIOR).get(f"/api/exports/{r.json()['id']}/bundle").status_code == 409


def test_direct_export_needs_case_number_and_is_flagged(env):
    e1 = env.run(_add_evidence(env, make_event(env.s3)))
    c = client_for(SENIOR)
    assert c.post(f"/api/evidence/{e1.id}/direct-export", json={"justification": "court"}).status_code == 422
    r = c.post(f"/api/evidence/{e1.id}/direct-export", json={"justification": "court", "case_number": "CR-9"})
    doc = r.json()
    assert doc["self_approved"] is True and doc["bundle_sha256"]
    z = zipfile.ZipFile(io.BytesIO(c.get(f"/api/exports/{doc['id']}/bundle").content))
    assert json.loads(z.read("manifest.json"))["export"]["self_approved"] is True


def test_disposed_evidence_cannot_join_a_case(env):
    e1 = env.run(_add_evidence(env, make_event(env.s3)))
    client_for(SENIOR).request("DELETE", f"/api/evidence/{e1.id}", json={"reason": "x" * 25})
    c = client_for(CRO)
    case = c.post("/api/cases", json={"case_number": "CR-2"}).json()
    assert c.post(f"/api/cases/{case['id']}/evidence", json={"evidence_id": e1.id}).status_code == 409


def test_case_access_by_jurisdiction(env):
    case = client_for(CRO).post("/api/cases", json={"case_number": "CR-3"}).json()
    assert case["jurisdiction"] == "Kochi"
    other = dict(CRO, email="cro2@test", jurisdiction="Kannur")
    assert client_for(other).get(f"/api/cases/{case['id']}").status_code == 404
    assert client_for(SENIOR).get(f"/api/cases/{case['id']}").status_code == 200
    assert client_for(OPERATOR).get("/api/cases").status_code == 403


# ── E5: evidence objects, proxy, signed links ────────────────────────────────
def test_object_endpoint_scope_and_pii(env):
    row = env.run(_add_evidence(env, make_event(env.s3, plate=True)))
    r = client_for(CRO).get(f"/api/evidence/{row.id}/objects/primary")
    assert r.status_code == 200 and r.headers["cache-control"] == "private, no-store"
    assert r.headers["X-Content-SHA256"] == sha256_hex(r.content)
    out_of_scope = dict(CRO, jurisdiction="Kannur")

    async def set_jur():
        async with env.factory() as s:
            (await s.get(Evidence, row.id)).jurisdiction = "Kochi"
            await s.commit()
    env.run(set_jur())
    assert client_for(out_of_scope).get(f"/api/evidence/{row.id}/objects/primary").status_code == 404
    assert client_for(ADMIN).get(f"/api/evidence/{row.id}/objects/primary").status_code == 200
    assert client_for(ADMIN).get(f"/api/evidence/{row.id}/objects/secondary").status_code == 403   # plate = PII
    assert client_for(OPERATOR).get(f"/api/evidence/{row.id}/objects/primary").status_code == 403
    views = [a for a in env.audit if a["action"] == "evidence_view"]
    client_for(CRO).get(f"/api/evidence/{row.id}/objects/primary")
    assert len([a for a in env.audit if a["action"] == "evidence_view"]) == len(views)   # once per hour


def test_proxy_restricts_pii_buckets_and_vault(env):
    env.s3.put("dvas-watchlist", "watchlist/a.jpg", b"x")
    env.s3.put("dvas-heatmaps", "heatmaps/a.png", b"y")
    assert client_for(OPERATOR).get("/api/minio/image?bucket=dvas-watchlist&key=watchlist/a.jpg").status_code == 403
    assert client_for(OPERATOR).get(f"/api/minio/image?bucket={evidence_vault.VAULT}&key=x").status_code == 403
    r = client_for(CRO).get("/api/minio/image?bucket=dvas-heatmaps&key=heatmaps/a.png")
    assert r.status_code == 200 and r.headers["cache-control"] == "private, max-age=60"


def test_signed_link_single_use_and_expiry(env, monkeypatch):
    e1 = env.run(_add_evidence(env, make_event(env.s3)))
    c = client_for(CRO)
    link = c.post("/api/downloads", json={"kind": "evidence_object", "id": e1.id, "role": "primary"}).json()
    assert link["expires_in"] == 60 and "token" not in link["url"]
    anon = client_for()           # no auth on the link itself
    assert anon.get(link["url"]).status_code == 200
    assert anon.get(link["url"]).status_code == 404          # single use
    link2 = c.post("/api/downloads", json={"kind": "evidence_object", "id": e1.id, "role": "primary"}).json()
    opaque = link2["url"].rsplit("/", 1)[1]
    downloads._links[opaque] = (downloads.time.time() - 1,) + downloads._links[opaque][1:]
    assert anon.get(link2["url"]).status_code == 404         # expired
    # the stored user's rights are re-checked: an operator's link is still refused
    op_link = client_for(OPERATOR).post("/api/downloads",
                                        json={"kind": "evidence_object", "id": e1.id, "role": "primary"}).json()
    assert client_for().get(op_link["url"]).status_code == 403


def test_proxy_image_authenticates_with_cookie_only(env, monkeypatch):
    """Analytics pages no longer put the JWT in image URLs; the httpOnly cookie is enough."""
    from core import security
    env.s3.put("dvas-plates", "plates/a.jpg", b"plate")
    monkeypatch.setattr(security, "get_jwks", lambda: {})
    monkeypatch.setattr(security.jwt, "decode", lambda token, *a, **k: {
        "sub": "c1", "email": "cro@test", "realm_access": {"roles": ["control-room"]}} if token == "cookie-jwt"
        else (_ for _ in ()).throw(security.jwt.JWTError("bad")))
    c = client_for()
    url = "/api/minio/image?bucket=dvas-plates&key=plates/a.jpg"
    assert c.get(url).status_code == 401                         # nothing sent
    c.cookies.set("dvas_access_token", "cookie-jwt")
    r = c.get(url)
    assert r.status_code == 200 and r.content == b"plate"
