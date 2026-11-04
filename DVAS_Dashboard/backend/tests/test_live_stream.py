"""Live annotated video relay: /api/analytics/{service}/annotated/stream (MJPEG).

Run from DVAS_Dashboard/backend:  python -m pytest tests/test_live_stream.py -q
"""
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.security import get_current_user
from routers import analytics

CRO = {"id": "c1", "email": "cro@test", "role": "control_room_officer", "jurisdiction": "", "mission_scope": []}
DO = {**CRO, "id": "d1", "email": "do@test", "role": "drone_operator"}
BODY = (b"--dvasframe\r\nContent-Type: image/jpeg\r\nContent-Length: 4\r\nX-Frame-Seq: 1\r\n\r\n"
        b"\xff\xd8ok\r\n")


@pytest.fixture
def upstream(monkeypatch):
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        if req.url.params.get("drone_id") == "DOWN":
            return httpx.Response(503)
        async def body():
            yield BODY[:20]
            yield BODY[20:]
        return httpx.Response(200, content=body(), headers={
            "content-type": "multipart/x-mixed-replace; boundary=dvasframe", "X-Drone-ID": "DRN-001"})

    real = httpx.AsyncClient
    monkeypatch.setattr(analytics.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    analytics._live_open.clear()
    return seen


def client(user):
    app = FastAPI()
    app.include_router(analytics.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_relays_the_analyzer_mjpeg_with_service_key_and_releases_the_slot(upstream):
    r = client(CRO).get("/api/analytics/intrusion/annotated/stream", params={"drone_id": "DRN-001"})
    assert r.status_code == 200 and r.content == BODY
    assert r.headers["content-type"].startswith("multipart/x-mixed-replace")
    assert upstream[-1].url.path == "/intrusion/annotated/stream"
    assert analytics._live_open == {}                   # counter released after the stream ends


def test_roles_and_validation(upstream):
    assert client(DO).get("/api/analytics/person/annotated/stream", params={"drone_id": "D"}).status_code == 403
    c = client(CRO)
    assert c.get("/api/analytics/crowd/annotated/stream", params={"drone_id": "D"}).status_code == 404
    assert c.get("/api/analytics/person/annotated/stream").status_code == 400
    assert c.get("/api/analytics/person/annotated/stream", params={"drone_id": "x" * 65}).status_code == 400
    assert c.get("/api/analytics/person/annotated/stream", params={"drone_id": "DOWN"}).status_code == 502


def test_per_user_limit(upstream):
    analytics._live_open["c1"] = analytics._LIVE_MAX_PER_USER
    r = client(CRO).get("/api/analytics/vehicle/annotated/stream", params={"drone_id": "D"})
    assert r.status_code == 429
