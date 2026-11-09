"""PipelineManager in h264_ws mode: video goes over the uplink, the JPEG path stays idle."""
from __future__ import annotations

import importlib
import shutil
import time
from pathlib import Path

import pytest

from tests.conformance.fake_ingest_server import FakeIngestServer
from tests.test_video_uplink import make_clip


@pytest.mark.timeout(90)
def test_mission_streams_video_and_keeps_jpeg_path_idle(tmp_path: Path, monkeypatch) -> None:
    server = FakeIngestServer(token="session-tok").start()
    try:
        data = tmp_path / "data"
        data.mkdir()
        make_clip(data / "flight.mp4", seconds=4)
        monkeypatch.setenv("DVAS_EDGE_DATA_DIR", str(data))
        monkeypatch.setenv("BUFFER__STORAGE_DIR", str(tmp_path / "rolling"))
        monkeypatch.setenv("INGEST__BASE_URL", f"http://127.0.0.1:{server.port}")
        monkeypatch.delenv("INGEST__PASSWORD", raising=False)
        import config.config as edge_config
        edge_config.reset_config()                      # pick up BUFFER__STORAGE_DIR
        import edge_api_server
        core = importlib.reload(edge_api_server)
        pm = core.PipelineManager()
        pm.server_send_enabled = True
        req = core.MissionStartRequest(mission_id="MSN-PIPE", mission_name="t", video_source="flight.mp4",
                                       video_source_type="file", drone_id="DRN-PIPE")
        res = pm.start_mission(req, session_token="session-tok", token_expiry=time.time() + 3600)
        assert res.get("status") == "started"
        assert pm.transport == "h264_ws"

        deadline = time.time() + 30
        while len(server.rec.metas) < 100 and time.time() < deadline:
            time.sleep(0.1)
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
