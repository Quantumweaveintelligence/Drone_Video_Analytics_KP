"""v2 core endpoints: snapshot/SSE auth and limits, URL validation, internal shutdown guard."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

KEY = "test-edge-key"
HDR = {"X-Edge-API-Key": KEY}


@pytest.fixture()
def core(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DVAS_EDGE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EDGE_API_KEY", KEY)
    monkeypatch.setenv("REQUIRE_EDGE_AUTH", "true")
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("DVAS_LAUNCHER_SECRET", "launcher-secret")
    monkeypatch.delenv("INGEST__BASE_URL", raising=False)
    monkeypatch.delenv("INGEST__URL", raising=False)
    import edge_api_server
    mod = importlib.reload(edge_api_server)
    return mod, TestClient(mod.app)


@pytest.mark.parametrize("path", ["/api/edge/snapshot", "/api/edge/events"])
def test_live_endpoints_require_auth(core, path: str) -> None:
    _, client = core
    assert client.get(path).status_code == 401


def test_snapshot_shape(core) -> None:
    _, client = core
    body = client.get("/api/edge/snapshot", headers=HDR).json()
    assert set(body) == {"status", "telemetry", "mission", "analytics", "uplink"}
    assert body["mission"] == {"active": False}


def test_sse_connection_limit(core) -> None:
    mod, client = core
    mod._sse_clients = mod._SSE_MAX_CLIENTS
    try:
        assert client.get("/api/edge/events", headers=HDR).status_code == 429
    finally:
        mod._sse_clients = 0


def test_settings_reject_bad_ingest_url_and_normalise_good_one(core) -> None:
    _, client = core
    bad = client.post("/api/edge/settings", headers=HDR, json={"transmission": {"ingest_base_url": "file:///etc/passwd"}})
    assert bad.status_code == 422
    good = client.post("/api/edge/settings", headers=HDR,
                       json={"transmission": {"ingest_base_url": "https://pod-80.proxy.runpod.net/ingest/frame"}})
    assert good.status_code == 200
    assert good.json()["settings"]["transmission"]["ingest_base_url"] == "https://pod-80.proxy.runpod.net"


def test_configured_url_drives_endpoints(core) -> None:
    mod, client = core
    client.post("/api/edge/settings", headers=HDR, json={"transmission": {"ingest_base_url": "https://pod-80.proxy.runpod.net"}})
    ep = mod._ingest_endpoints()
    assert ep.frame == "https://pod-80.proxy.runpod.net/ingest/frame"
    assert ep.video_ws == "wss://pod-80.proxy.runpod.net/ingest/video/ws"


def test_test_connection_rejects_arbitrary_urls(core) -> None:
    _, client = core
    r = client.post("/api/edge/transmission/test-connection", headers=HDR, json={"url": "gopher://169.254.169.254/"})
    assert r.status_code == 422


def test_ingest_status_proxy_validates_drone_id(core) -> None:
    _, client = core
    assert client.get("/api/ingest-server/status/..", headers=HDR).status_code in (404, 422)
    assert client.get("/api/ingest-server/status/bad%20id", headers=HDR).status_code == 422


def test_internal_shutdown_needs_launcher_secret(core) -> None:
    _, client = core
    # TestClient's client host is "testclient", not loopback: refused even with the right secret
    assert client.post("/internal/shutdown", headers={"X-Launcher-Secret": "launcher-secret"}).status_code == 403
    assert client.post("/internal/shutdown", headers={"X-Launcher-Secret": "wrong"}).status_code == 403


def test_no_fabricated_system_logs(core) -> None:
    mod, _ = core
    messages = " ".join(e["message"] for e in mod.SYSTEM_LOGS_DEQUE)
    for fake in ("RTMP connected", "Battery: 78%", "Temp: 54", "ACK received", "14 satellites"):
        assert fake not in messages


def test_token_url_cannot_be_redirected_by_settings(core, monkeypatch) -> None:
    mod, client = core
    monkeypatch.setenv("INGEST__BASE_URL", "https://installed-pod-80.proxy.runpod.net")
    client.post("/api/edge/settings", headers=HDR, json={"transmission": {"ingest_base_url": "https://other-host.example"}})
    ep = mod._ingest_endpoints()
    assert ep.frame.startswith("https://other-host.example")
    assert ep.token.startswith("https://installed-pod-80.proxy.runpod.net/realms/")


def test_cors_rejects_foreign_origin(core) -> None:
    _, client = core
    r = client.get("/api/edge/snapshot", headers={**HDR, "Origin": "https://evil.example"})
    assert r.headers.get("access-control-allow-origin") is None


def test_no_token_url_without_install_config(core, monkeypatch) -> None:
    mod, client = core
    client.post("/api/edge/settings", headers=HDR, json={"transmission": {"ingest_base_url": "https://other-host.example"}})
    assert mod._ingest_endpoints().token == ""
