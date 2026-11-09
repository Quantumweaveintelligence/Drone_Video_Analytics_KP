"""Gateway must not replay one officer's cloud cookie for another client (VAPT V1)."""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))


@pytest.fixture()
def gateway(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    import importlib
    import edge_launcher
    gw = importlib.reload(edge_launcher)
    seen = []

    def cloud(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("cookie"))
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={}, headers={"set-cookie": "dvas_access_token=VICTIM; Path=/; HttpOnly"})
        return httpx.Response(200, json={})

    monkeypatch.setattr(gw, "client", httpx.AsyncClient(transport=httpx.MockTransport(cloud), cookies=gw._NoCookieJar()))
    return TestClient(gw.app, base_url="http://localhost:8765"), seen


def test_gateway_client_never_stores_or_replays_cloud_cookies(gateway) -> None:
    import asyncio
    import edge_launcher as gw
    seen = []

    def cloud(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("cookie"))
        return httpx.Response(200, headers={"set-cookie": "dvas_access_token=VICTIM; Path=/; HttpOnly"})

    async def run():
        c = httpx.AsyncClient(transport=httpx.MockTransport(cloud), cookies=gw._NoCookieJar())
        await c.get("http://cloud/api/auth/login")          # officer A logs in
        await c.get("http://cloud/api/me")                  # next caller through the gateway
        stored = len(c.cookies.jar)
        await c.aclose()
        return stored

    assert asyncio.run(run()) == 0
    assert seen == [None, None]


@pytest.mark.parametrize("headers, method", [
    ({"host": "evil.example"}, "GET"),                                  # DNS rebinding
    ({"origin": "https://evil.example"}, "POST"),                       # cross-site write
])
def test_foreign_host_or_origin_blocked(gateway, headers, method) -> None:
    tc, _ = gateway
    assert tc.request(method, "/api/me", headers=headers).status_code == 403
