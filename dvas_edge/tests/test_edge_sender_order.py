"""Capture-time identity and ordered sending (plan E1 items 1-2, findings E-F2 and E-F3)."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import httpx
import pytest


@pytest.fixture()
def edge_server(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DVAS_EDGE_DATA_DIR", str(tmp_path))
    import importlib
    import edge_api_server
    importlib.reload(edge_api_server)          # pick up the temp data dir
    return edge_api_server


class _FakeClient:
    """Stands in for IngestClient; fails the first attempt of chosen seqs."""

    def __init__(self, fail_once: set) -> None:
        self.mission_id = "MSN-ORDER"
        self.token_expiry = 0.0
        self._fail_once = set(fail_once)
        self.attempts: list = []
        self.delivered: list = []
        self._lock = threading.Lock()

    async def send_frame_bytes(self, frame_bytes, change_score, seq=None, capture_ts=None, **_tel):
        await asyncio.sleep(0.01)
        with self._lock:
            self.attempts.append(seq)
            if seq in self._fail_once:
                self._fail_once.discard(seq)
                raise httpx.ConnectError("injected failure")
            self.delivered.append((seq, capture_ts))
        return {"status": "queued"}

    async def end_mission(self):
        return None

    async def start_mission(self, **_kw):
        return None


def test_retried_frames_keep_seq_and_capture_ts(edge_server) -> None:
    client = _FakeClient(fail_once={3, 7})
    sender = edge_server.EdgeSenderThread(client=client, server_send_initial=False)
    captured = {}
    for i in range(10):
        ts_ms = 1_762_671_000_000 + i * 40
        sender.put_raw_frame(b"\xff\xd8\xff" + bytes([i]), telemetry={}, capture_ts_ms=ts_ms)
        captured[i + 1] = ts_ms / 1000.0
    sender.start()
    deadline = time.time() + 20
    while len(client.delivered) < 10 and time.time() < deadline:
        time.sleep(0.05)
    sender.stop()
    sender.join(timeout=10)

    seqs = [s for s, _ in client.delivered]
    assert sorted(seqs) == list(range(1, 11))          # every frame exactly once
    assert len(seqs) == len(set(seqs))
    for seq, ts in client.delivered:
        assert ts == captured[seq]                     # original capture time, not send time
    assert client.attempts.count(3) == 2 and client.attempts.count(7) == 2


def test_permanent_4xx_is_discarded_not_retried_forever(edge_server) -> None:
    class _Reject(_FakeClient):
        async def send_frame_bytes(self, frame_bytes, change_score, seq=None, capture_ts=None, **_tel):
            self.attempts.append(seq)
            if seq == 2:
                req = httpx.Request("POST", "http://ingest/ingest/frame")
                raise httpx.HTTPStatusError("409", request=req, response=httpx.Response(409, request=req))
            self.delivered.append((seq, capture_ts))
            return {}

    client = _Reject(fail_once=set())
    sender = edge_server.EdgeSenderThread(client=client, server_send_initial=False)
    for i in range(3):
        sender.put_raw_frame(b"\xff\xd8\xff", telemetry={}, capture_ts_ms=1_000 + i)
    sender.start()
    deadline = time.time() + 10
    while len(client.attempts) < 3 and time.time() < deadline:
        time.sleep(0.05)
    time.sleep(0.3)
    stats = sender.buffer_stats()
    sender.stop()
    sender.join(timeout=10)
    assert client.attempts.count(2) == 1
    assert stats["rejected_total"] == 1


def test_send_window_and_backoff_bounds(edge_server) -> None:
    assert edge_server._send_window(20, 0.5) == 15
    assert edge_server._send_window(1, 0.01) == 2
    assert edge_server._send_window(60, 2.0) == 24
    for n in range(1, 10):
        assert 0.4 <= edge_server._backoff_s(n) <= 9.6


def test_ingest_client_sends_given_identity(monkeypatch) -> None:
    import ingest.edge_sender as es

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode("latin-1")
        start = body.index("{", body.index('name="metadata_json"'))
        seen.append(json.loads(body[start: body.index("}", start) + 1]))
        return httpx.Response(202, json={"status": "queued", "message_id": "1-0"})

    async def run():
        client = es.IngestClient(drone_id="DRN-001", mission_id="MSN-1",
                                 session_token="tok", token_expiry=time.time() + 600)
        client._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        await client.send_frame_bytes(b"\xff\xd8\xff", 0.5, seq=42, capture_ts=1_762_671_000.123)
        await client._http_client.aclose()

    monkeypatch.setattr(es, "INGEST_URL", "http://ingest.test/ingest/frame")
    asyncio.run(run())
    meta = seen[0]
    assert meta["frame_sequence"] == 42 and meta["frame_seq"] == 42
    assert meta["capture_ts"] == 1_762_671_000.123
    assert meta["timestamp"] == "2025-11-09T06:50:00.123Z"
