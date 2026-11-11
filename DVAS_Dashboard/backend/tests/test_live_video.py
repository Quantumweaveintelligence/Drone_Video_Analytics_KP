"""Live video (v2 plan R2): which go2rtc slot carries a drone's video, and the nginx
auth_request check for the player socket — logged-in user, slot's current drone within
the user's access, nothing else.

Run from DVAS_Dashboard/backend:  python -m pytest tests/test_live_video.py -q
"""
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from core.security import AuthError
from routers import live

USERS = {"tok-op": {"id": "u1", "role": "drone_operator"},
         "tok-cro": {"id": "u2", "role": "control_room_officer"}}
SLOTS = {"live03": {"src": "live03", "sid": "MSN-1", "drone_id": "DRN-1", "mission_id": "MSN-1"},
         "live03_sub": {"src": "live03_sub", "sid": "MSN-1", "drone_id": "DRN-1", "mission_id": "MSN-1"}}
ALLOWED = {("u1", "DRN-1")}


@pytest.fixture
def client(monkeypatch):
    async def user_from_token(tok):
        if tok not in USERS:
            raise AuthError("bad token")
        return USERS[tok]

    async def proxy_get(path, timeout=5.0):
        if path.startswith("/ingest/video/slot/"):
            slot = path.rsplit("/", 1)[1]
            if slot not in SLOTS:
                raise HTTPException(404, "Slot is free")
            return SLOTS[slot]
        if path in ("/ingest/video/live/DRN-1", "/ingest/video/live/DRN-1?sub=1"):
            sub = path.endswith("?sub=1")
            return {"src": "live03_sub" if sub else "live03", "main_src": "live03", "sub": sub,
                    "mission_id": "MSN-1", "width": 3840, "height": 2160, "fps": 30.0,
                    "analytics_fps": 20.0, "behind_live_s": 0.4}
        raise HTTPException(404, "No live video for this drone")

    async def check(drone_id, user):
        if (user["id"], drone_id) not in ALLOWED:
            raise HTTPException(404, "Drone not found")

    async def any_user(request: Request):
        try:
            return await user_from_token(request.cookies.get("dvas_access_token"))
        except AuthError:
            raise HTTPException(401, "Not authenticated")

    monkeypatch.setattr(live, "user_from_token", user_from_token)
    monkeypatch.setattr(live, "_proxy_get", proxy_get)
    monkeypatch.setattr(live, "_check_drone_access", check)
    app = FastAPI()
    app.include_router(live.router)
    app.dependency_overrides[live._any_user] = any_user
    return TestClient(app)


def auth(c, uri, cookie=None):
    if cookie:
        c.cookies.set("dvas_access_token", cookie)
    else:
        c.cookies.clear()
    return c.get("/api/live/authorize", headers={"X-Original-URI": uri}).status_code


def test_authorize_allows_only_the_slots_current_drone_for_permitted_users(client):
    assert auth(client, "/live/api/ws?src=live03", "tok-op") == 204
    assert auth(client, "/live/api/ws?src=live03", "tok-cro") == 403        # drone outside access
    assert auth(client, "/live/api/ws?src=live03") == 401                   # not logged in
    assert auth(client, "/live/api/ws?src=live03", "nope") == 401
    assert auth(client, "/live/api/ws?src=live07", "tok-op") == 403        # free slot
    assert auth(client, "/live/api/ws?src=../api/streams", "tok-op") == 403
    assert auth(client, "/live/api/streams?src=live03", "tok-op") == 403   # only the player socket
    assert auth(client, "/live/api/ws?src=live03&token=tok-op") == 204     # edge app: ?token=
    assert auth(client, "/live/api/ws?src=live03_sub", "tok-op") == 204    # grid sub-stream
    assert auth(client, "/live/api/ws?src=live03_sub", "tok-cro") == 403
    assert auth(client, "/live/api/ws?src=live03_hd", "tok-op") == 403


def test_drone_lookup_returns_the_player_path(client):
    client.cookies.set("dvas_access_token", "tok-op")
    r = client.get("/api/live/drone/DRN-1")
    assert r.status_code == 200
    assert r.json()["ws_path"] == "/live/api/ws?src=live03" and r.json()["width"] == 3840
    assert client.get("/api/live/drone/DRN-2").status_code == 404


def test_grid_tiles_get_the_sub_stream(client):
    client.cookies.set("dvas_access_token", "tok-op")
    r = client.get("/api/live/drone/DRN-1?sub=1").json()
    assert r["ws_path"] == "/live/api/ws?src=live03_sub" and r["sub"] is True
    assert client.get("/api/live/drone/DRN-1").json()["sub"] is False
