"""One ingest URL -> every endpoint (user request: enter the RunPod URL once, as in v1)."""
from __future__ import annotations

import pytest

from ingest.endpoints import IngestEndpoints, InvalidIngestURL, normalize_base

POD = "https://abc123-80.proxy.runpod.net"


@pytest.mark.parametrize("entered", [POD, POD + "/", POD + "/ingest", POD + "/ingest/", POD + "/ingest/frame", "  " + POD + "  "])
def test_accepted_forms_normalize_to_host(entered: str) -> None:
    assert normalize_base(entered) == POD


def test_all_endpoints_derived() -> None:
    ep = IngestEndpoints.from_base(POD + "/ingest/frame")
    assert ep.frame == POD + "/ingest/frame"
    assert ep.mission_start == POD + "/ingest/mission/start"
    assert ep.mission_end == POD + "/ingest/mission/end"
    assert ep.mission_geofence == POD + "/ingest/mission/geofence"
    assert ep.token == POD + "/realms/kerala-police-drone/protocol/openid-connect/token"
    assert ep.video_ws == "wss://abc123-80.proxy.runpod.net/ingest/video/ws"
    assert ep.tls


def test_lan_http_gives_plain_ws() -> None:
    ep = IngestEndpoints.from_base("http://10.0.0.5:8000")
    assert ep.video_ws == "ws://10.0.0.5:8000/ingest/video/ws" and not ep.tls


@pytest.mark.parametrize("bad", ["", "ftp://x", "abc123-80.proxy.runpod.net", "https://", "https://u:p@host",
                                 "https://host/?a=1", "https://host/#x", "https://host/other/path", "javascript:alert(1)"])
def test_rejected_urls(bad: str) -> None:
    with pytest.raises(InvalidIngestURL):
        normalize_base(bad)


def test_from_env_prefers_base_and_honours_token_override(monkeypatch) -> None:
    monkeypatch.setenv("INGEST__BASE_URL", POD)
    monkeypatch.setenv("INGEST__URL", "http://old-host/ingest/frame")
    monkeypatch.setenv("INGEST__TOKEN_URL", "http://10.0.0.5:8080/realms/x/token")
    ep = IngestEndpoints.from_env()
    assert ep.base == POD and ep.token == "http://10.0.0.5:8080/realms/x/token"


def test_from_env_v1_key_and_unset(monkeypatch) -> None:
    for k in ("INGEST__BASE_URL", "INGEST__URL", "INGEST_URL", "INGEST__TOKEN_URL"):
        monkeypatch.delenv(k, raising=False)
    assert IngestEndpoints.from_env() is None
    monkeypatch.setenv("INGEST__URL", POD + "/ingest/frame")
    assert IngestEndpoints.from_env().base == POD


def test_plain_http_refused_unless_lan_flag(monkeypatch) -> None:
    monkeypatch.setenv("DVAS_ALLOW_HTTP_INGEST", "false")
    with pytest.raises(InvalidIngestURL):
        normalize_base("http://attacker.example")
    assert normalize_base(POD) == POD


def test_install_config_may_use_http_without_flag(monkeypatch) -> None:
    monkeypatch.setenv("DVAS_ALLOW_HTTP_INGEST", "false")
    monkeypatch.setenv("INGEST__BASE_URL", "http://10.0.0.5:8000")
    assert IngestEndpoints.from_env().base == "http://10.0.0.5:8000"     # LAN install upgrade keeps working
