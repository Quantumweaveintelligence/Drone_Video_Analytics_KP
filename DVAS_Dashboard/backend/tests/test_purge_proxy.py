"""Stream purge relay: /api/ingest-server/streams/{id}/purge (Multi-Drone View).

The officer's own token goes to ingest first (the backend service account may carry no
DVAS realm role, which ingest rejects); the service token is only a fallback, and ingest's
reason is passed back instead of a bare "Failed to purge stream".

Run from DVAS_Dashboard/backend:  python -m pytest tests/test_purge_proxy.py -q
"""
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.security as security
import ingest_proxy
from services import drone_status

CRO = {"id": "c1", "email": "cro@test", "role": "control_room_officer", "jurisdiction": "", "mission_scope": []}
DO = {**CRO, "id": "d1", "role": "drone_operator"}


@pytest.fixture
def ingest(monkeypatch):
    state = {"calls": [], "accept": {"Bearer user-tok"}, "svc": "svc-tok", "offline": []}

    def handler(req: httpx.Request):
        auth = req.headers.get("Authorization", "")
        state["calls"].append(auth)
        if auth in state["accept"]:
            return httpx.Response(200, json={"status": "purged"})
        return httpx.Response(403, json={"detail": "Role required: one of ['control-room', 'supervisor']"})

    real = httpx.AsyncClient
    monkeypatch.setattr(ingest_proxy.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))

    async def svc():
        return state["svc"]
    monkeypatch.setattr(ingest_proxy, "_get_service_token", svc)

    async def offline(drone_id, reason, mission_id=None):
        state["offline"].append((drone_id, reason))
        return True
    monkeypatch.setattr(drone_status, "mark_drone_offline", offline)
    return state


def client(user, monkeypatch):
    async def current(request):
        return user
    monkeypatch.setattr(security, "get_current_user", current)
    app = FastAPI()
    app.include_router(ingest_proxy.ingest_api)
    return TestClient(app)


def test_officer_token_is_forwarded_first(ingest, monkeypatch):
    c = client(CRO, monkeypatch)
    r = c.post("/api/ingest-server/streams/DRN-0111111111/purge", cookies={"dvas_access_token": "user-tok"})
    assert r.status_code == 200 and r.json() == {"status": "purged"}
    assert ingest["calls"] == ["Bearer user-tok"]
    # A5: a purged stream takes the drone offline right away
    assert ingest["offline"] == [("DRN-0111111111", "stream_purged")]


def test_service_token_is_the_fallback(ingest, monkeypatch):
    ingest["accept"] = {"Bearer svc-tok"}
    r = client(CRO, monkeypatch).post("/api/ingest-server/streams/D1/purge",
                                      headers={"Authorization": "Bearer user-tok"})
    assert r.status_code == 200 and ingest["calls"] == ["Bearer user-tok", "Bearer svc-tok"]


def test_ingest_reason_is_reported(ingest, monkeypatch):
    ingest["accept"] = set()
    r = client(CRO, monkeypatch).post("/api/ingest-server/streams/D1/purge", cookies={"dvas_access_token": "x"})
    assert r.status_code == 403 and "Role required" in r.json()["detail"]
    assert ingest["offline"] == []          # a failed purge leaves the drone alone


def test_operator_and_bad_ids_are_refused_before_ingest(ingest, monkeypatch):
    assert client(DO, monkeypatch).post("/api/ingest-server/streams/D1/purge").status_code == 403
    assert client(CRO, monkeypatch).post("/api/ingest-server/streams/D%20X/purge").status_code == 400
    assert ingest["calls"] == []
