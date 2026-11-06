"""Detection Models page: GPU profile, live fps and the FPS governor policy (RunPod plan Phase 10).

Uses the in-memory SQLite fakes from test_evidence_pipeline; the analytics gateway is faked.
Run from DVAS_Dashboard/backend:  python -m pytest tests/test_fps_policy.py -q
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_evidence_pipeline import CRO, SENIOR, env  # noqa: F401  (fixture)

from core.security import get_current_user
from routers import infra
from services import fps_policy, settings_service

ADMIN = {"id": "a1", "email": "ad@test", "name": "Ad", "role": "system_administrator", "jurisdiction": "",
         "mission_scope": []}
DO = {**CRO, "id": "do1", "email": "do@test", "role": "drone_operator"}

GOV = {
    "enabled": True, "state": "overloaded", "reason": "GPU 97 %: all analyzers capped at 18 fps",
    "cap_fps": 18, "lagging": ["person/DRN-001"], "gpu_util_pct": 97.0, "updated_at": 1.0,
    "policy": {"target_min": 20, "target_max": 30, "floor": 5},
    "gpu": {"available": True, "name": "NVIDIA L40S", "vram_gb": 44.5, "tier": "large", "fp16": True,
            "max_batch": 4, "compute_capability": "8.9"},
    "services": {
        "person": {"governed": True, "cap_fps": 18, "avg_batch": 2.0, "drones": {
            "DRN-001": {"fps": 12.0, "input_fps": 30.0, "skipped": 40, "infer_ms": 21.0, "lag_ms": 60}}},
        "vehicle": {"governed": True, "cap_fps": 18, "avg_batch": 2.0, "drones": {
            "DRN-001": {"fps": 17.8, "input_fps": 30.0, "skipped": 5, "infer_ms": 6.0, "lag_ms": 40},
            "DRN-002": {"fps": 9.9, "input_fps": 10.0, "skipped": 0, "infer_ms": 6.0, "lag_ms": 40}}},
        "crowd": {"governed": False, "cap_fps": 2, "drones": {
            "DRN-001": {"fps": 2.0, "input_fps": 30.0, "skipped": 900, "infer_ms": 40.0}}},
    },
}


@pytest.fixture
def app_env(env, monkeypatch):
    monkeypatch.setattr(settings_service, "async_session_factory", env.factory)
    monkeypatch.setattr(settings_service, "_cache", {})
    monkeypatch.setattr(settings_service, "_cache_at", 0.0)
    env.audits, env.pushed, env.gov = [], [], dict(GOV)

    async def _audit(**kw):
        env.audits.append(kw)

    async def _status():
        return env.gov

    async def _put(url, payload, **kw):
        env.pushed.append((url, payload))
        return {"policy": payload}

    monkeypatch.setattr(infra, "write_audit_v2", _audit)
    monkeypatch.setattr(fps_policy, "status", _status)
    monkeypatch.setattr(fps_policy, "_analytics_put", _put)
    return env


def client(user):
    app = FastAPI()
    app.state.limiter = infra.limiter
    app.include_router(infra.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_performance_view_roles_and_shape(app_env):
    assert client(DO).get("/api/models/performance").status_code == 403
    body = client(CRO).get("/api/models/performance").json()
    assert body["editable"] is False and body["available"] is True
    assert body["gpu"]["name"] == "NVIDIA L40S" and body["gpu"]["max_batch"] == 4
    assert body["governor"]["state"] == "overloaded" and body["governor"]["cap_fps"] == 18
    rows = {r["service"]: r for r in body["services"]}
    assert set(rows) == {"person", "vehicle", "crowd"}
    states = {d["drone_id"]: d["state"] for d in rows["vehicle"]["drones"]}
    assert states == {"DRN-001": "below_target", "DRN-002": "input_limited"}
    assert rows["crowd"]["drones"][0]["state"] == "capped" and rows["crowd"]["governed"] is False
    assert client(SENIOR).get("/api/models/performance").json()["editable"] is True


def test_policy_update_validates_audits_and_pushes(app_env):
    so, cro = client(SENIOR), client(CRO)
    good = {"target_min": 18, "target_max": 25, "floor": 6}
    assert cro.put("/api/models/performance/policy", json=good).status_code == 403
    for bad in ({"target_min": 30, "target_max": 20, "floor": 5},     # min > max
                {"target_min": 20, "target_max": 30, "floor": 25},    # floor > min
                {"target_min": 0, "target_max": 30, "floor": 1},      # out of range
                {"target_min": 20, "target_max": 30, "floor": 5, "x": 1},   # unknown field
                {"target_min": "20; DROP", "target_max": 30, "floor": 5}):
        assert so.put("/api/models/performance/policy", json=bad).status_code == 422, bad
    r = so.put("/api/models/performance/policy", json=good)
    assert r.status_code == 200 and r.json()["applied"] is True and r.json()["policy"] == good
    assert app_env.pushed[-1][0].endswith("/governor/policy") and app_env.pushed[-1][1] == good
    assert app_env.audits[-1]["action"] == "fps_policy_update"
    assert app_env.run(fps_policy.current()) == good
    # unchanged → no second audit entry
    so.put("/api/models/performance/policy", json=good)
    assert len(app_env.audits) == 1


def test_stale_gateway_policy_is_resent(app_env):
    app_env.gov = {**GOV, "policy": {"target_min": 1, "target_max": 1, "floor": 1}}
    client(SENIOR).get("/api/models/performance")
    assert app_env.pushed and app_env.pushed[-1][1] == {"target_min": 20, "target_max": 30, "floor": 5}


def test_gateway_down_is_reported_not_faked(app_env, monkeypatch):
    app_env.gov = {"error": "service_unavailable", "detail": "Analytics service unavailable"}
    monkeypatch.setattr(infra.runtime, "gpu_name_nvml", lambda: None)
    monkeypatch.setattr(infra.runtime, "gpu_metrics_nvml", lambda: None)
    body = client(CRO).get("/api/models/performance").json()
    assert body["available"] is False and body["services"] == [] and body["governor"] is None
    assert body["detail"] == "Analytics service unavailable"
    assert body["gpu"]["name"] is None


def test_admin_settings_enforce_fps_order(app_env):
    with pytest.raises(ValueError):
        app_env.run(settings_service.update({"analytics_fps_floor": 40}, ADMIN))


def test_live_summary_for_models_list():
    s = infra._live_summary(GOV["services"]["vehicle"])
    assert s["drones"] == 2 and s["fps"] == 13.9 and s["skipped"] == 5
    assert infra._live_summary(None) is None


def test_control_room_officer_can_deploy_models_but_not_tune_fps(app_env, monkeypatch):
    """2026-11-06: CRO starts/stops modules and deploys the shared set (audited); FPS policy stays SO/SA."""
    applied = []

    async def cfg(scope):
        return {"active": ["person"], "updated_at": None, "updated_by": None}

    async def apply(active, user, request=None, reason=""):
        applied.append((active, user["role"]))
        return {"vehicle": "started"}

    async def status():
        return {"person": True, "vehicle": False}

    async def hw():
        return {}

    monkeypatch.setattr(infra, "_get_model_config", cfg)
    monkeypatch.setattr(infra, "apply_model_config", apply)
    monkeypatch.setattr(infra, "_get_service_stream_status", status)
    monkeypatch.setattr(infra, "collect_system_hardware", hw)
    cro = client(CRO)
    assert cro.get("/api/models").json()["read_only"] is False
    r = cro.post("/api/models", json={"active": ["person", "vehicle"]})
    assert r.status_code == 200 and applied == [(["person", "vehicle"], "control_room_officer")]
    assert client(DO).post("/api/models", json={"active": []}).status_code == 403
    assert cro.put("/api/models/performance/policy",
                   json={"target_min": 18, "target_max": 25, "floor": 6}).status_code == 403
