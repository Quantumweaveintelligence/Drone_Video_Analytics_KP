"""PipelineManager in h264_ws mode: video goes over the uplink, the JPEG path stays idle."""
from __future__ import annotations

import asyncio
import importlib
import shutil
import time
from pathlib import Path

import pytest

from tests.conformance.fake_ingest_server import FakeIngestServer
from tests.test_video_uplink import make_clip


def _start_pipeline(tmp_path: Path, monkeypatch, server: FakeIngestServer, start_mission, clip_s: int = 10):
    """Start a file mission with server send on; `start_mission` stands in for ingest POST /ingest/mission/start."""
    data = tmp_path / "data"
    data.mkdir()
    make_clip(data / "flight.mp4", seconds=clip_s)   # long enough to outlast a slow mission/start
    monkeypatch.setenv("DVAS_EDGE_DATA_DIR", str(data))
    monkeypatch.setenv("BUFFER__STORAGE_DIR", str(tmp_path / "rolling"))
    monkeypatch.setenv("INGEST__BASE_URL", f"http://127.0.0.1:{server.port}")
    monkeypatch.delenv("INGEST__PASSWORD", raising=False)
    import config.config as edge_config
    edge_config.reset_config()                      # pick up BUFFER__STORAGE_DIR
    import edge_api_server
    core = importlib.reload(edge_api_server)
    monkeypatch.setattr(core.IngestClient, "start_mission", start_mission)
    pm = core.PipelineManager()
    pm.server_send_enabled = True
    req = core.MissionStartRequest(mission_id="MSN-PIPE", mission_name="t", video_source="flight.mp4",
                                   video_source_type="file", drone_id="DRN-PIPE")
    res = pm.start_mission(req, session_token="session-tok", token_expiry=time.time() + 3600)
    assert res.get("status") == "started"
    assert pm.transport == "h264_ws"
    return pm


@pytest.mark.timeout(90)
def test_mission_streams_video_and_keeps_jpeg_path_idle(tmp_path: Path, monkeypatch) -> None:
    server = FakeIngestServer(token="session-tok").start()
    ws_before_start = []

    async def slow_mission_start(self, **_kw) -> None:
        await asyncio.sleep(1.0)
        ws_before_start.append(server.rec.connections)   # the uplink must not have connected yet

    try:
        pm = _start_pipeline(tmp_path, monkeypatch, server, slow_mission_start)

        deadline = time.time() + 30
        while len(server.rec.metas) < 100 and time.time() < deadline:
            time.sleep(0.1)
        assert ws_before_start == [0]                    # mission/start finished before the WebSocket opened
        assert pm.video_uplink is not None and not pm.video_uplink.fallback_active
        assert server.rec.hellos[0]["mission_id"] == "MSN-PIPE"
        assert len(server.rec.metas) >= 100
        assert pm.sender_thread.buffer_stats()["memory_frames"] == 0      # JPEG path idle
        pm.end_mission()
        deadline = time.time() + 10
        while not any(o.get("type") == "bye" for o in server.rec.other) and time.time() < deadline:
            time.sleep(0.1)
        assert any(o.get("type") == "bye" for o in server.rec.other)
        seqs = [m["seq"] for m in server.rec.metas]
        assert len(seqs) == len(set(seqs))
    finally:
        server.stop()
        shutil.rmtree(tmp_path / "data" / "buffer_storage", ignore_errors=True)


@pytest.mark.timeout(90)
def test_uplink_waits_for_ingest_then_recovers_after_failed_mission_start(tmp_path: Path, monkeypatch) -> None:
    server = FakeIngestServer(token="session-tok").start()
    calls = []

    async def flaky_mission_start(self, **_kw) -> None:
        calls.append(server.rec.connections)        # WebSocket count when each attempt is made
        if len(calls) == 1:
            raise ConnectionError("ingest unreachable")

    try:
        pm = _start_pipeline(tmp_path, monkeypatch, server, flaky_mission_start)
        deadline = time.time() + 20
        while server.rec.connections == 0 and time.time() < deadline:
            time.sleep(0.1)
        assert len(calls) >= 2                           # retried by itself, no operator action
        assert calls[:2] == [0, 0]                       # no WebSocket while ingest lacked the mission
        assert server.rec.connections >= 1               # video started once mission/start succeeded
        assert pm.video_uplink is not None and pm.video_uplink.running
        pm.end_mission()
    finally:
        server.stop()
        shutil.rmtree(tmp_path / "data" / "buffer_storage", ignore_errors=True)


@pytest.mark.timeout(60)
def test_server_send_off_stops_mission_start_retries(tmp_path: Path, monkeypatch) -> None:
    server = FakeIngestServer(token="session-tok").start()
    calls = []

    async def failing_mission_start(self, **_kw) -> None:
        calls.append(time.time())
        raise ConnectionError("ingest unreachable")

    try:
        pm = _start_pipeline(tmp_path, monkeypatch, server, failing_mission_start)
        time.sleep(1.0)
        pm.set_server_send(False)
        settled = len(calls)
        time.sleep(5.0)                                  # past the 2 s and 4 s back-off steps
        assert len(calls) == settled
        assert server.rec.connections == 0
        pm.end_mission()
    finally:
        server.stop()
        shutil.rmtree(tmp_path / "data" / "buffer_storage", ignore_errors=True)


@pytest.mark.timeout(60)
def test_server_send_off_then_on_restarts_video_without_blocking(tmp_path: Path, monkeypatch) -> None:
    server = FakeIngestServer(token="session-tok").start()

    async def mission_start(self, **_kw) -> None:
        return None

    try:
        pm = _start_pipeline(tmp_path, monkeypatch, server, mission_start, clip_s=30)
        deadline = time.time() + 15
        while not (pm.video_uplink and pm.video_uplink.running) and time.time() < deadline:
            time.sleep(0.1)
        assert pm.video_uplink.running
        pm.set_server_send(False)
        deadline = time.time() + 10
        while pm.video_uplink.running and time.time() < deadline:
            time.sleep(0.1)
        t0 = time.time()
        pm.set_server_send(True)                         # mission already started: must not self-deadlock
        assert time.time() - t0 < 2.0
        deadline = time.time() + 10
        while not pm.video_uplink.running and time.time() < deadline:
            time.sleep(0.1)
        assert pm.video_uplink.running
        pm.end_mission()
    finally:
        server.stop()
        shutil.rmtree(tmp_path / "data" / "buffer_storage", ignore_errors=True)


@pytest.mark.timeout(60)
def test_concurrent_send_on_requests_post_mission_start_once(tmp_path: Path, monkeypatch) -> None:
    server = FakeIngestServer(token="session-tok").start()
    calls = []

    async def slow_mission_start(self, **_kw) -> None:
        calls.append(time.time())
        await asyncio.sleep(1.5)

    try:
        pm = _start_pipeline(tmp_path, monkeypatch, server, slow_mission_start)
        time.sleep(0.3)                                  # first start in flight
        pm.sender_thread.on_server_send_enabled()
        pm.sender_thread.on_server_send_enabled()
        time.sleep(3.0)
        assert len(calls) == 1
        pm.end_mission()
    finally:
        server.stop()
        shutil.rmtree(tmp_path / "data" / "buffer_storage", ignore_errors=True)


def test_snapshot_is_rebuilt_after_invalidation(monkeypatch) -> None:
    import edge_api_server as core
    builds = []
    monkeypatch.setattr(core, "_active_mission_view", lambda: builds.append(1) or {"active": len(builds) > 1})
    for name in ("get_status", "get_telemetry"):
        monkeypatch.setattr(core.pipeline, name, lambda: {})
    monkeypatch.setattr(core, "_analytics_view", lambda: {})
    monkeypatch.setattr(core, "_uplink_view", lambda: {})
    core.invalidate_snapshot()
    assert core.build_snapshot()["mission"]["active"] is False
    core.invalidate_snapshot()                                   # a mission started
    assert core.build_snapshot()["mission"]["active"] is True
    core.invalidate_snapshot()                                   # leave no fake data for later tests
