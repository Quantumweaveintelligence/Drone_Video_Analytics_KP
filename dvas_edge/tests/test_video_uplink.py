"""Continuous video uplink against the fake ingest server (contract §3, plan Phase E2)."""
from __future__ import annotations

import io
import threading
import time
from pathlib import Path

import av
import numpy as np
import pytest

from ingest.seq_store import SeqStore
from ingest.video_uplink import StreamInfo, VideoUplink
from src.buffer.packet_ring import PacketRing, RingPacket
from src.video.uplink_source import UplinkVideoSource
from tests.conformance.fake_ingest_server import REQUIRED_KEYS, FakeIngestServer

FPS = 30


def make_clip(path: Path, seconds: float = 3.0, gop: int = 30, bframes: int = 2) -> int:
    """Synthetic H.264 test clip (libx264 is used only to create the fixture)."""
    out = av.open(str(path), "w")
    st = out.add_stream("libx264", rate=FPS)
    st.width, st.height, st.pix_fmt = 640, 360, "yuv420p"
    st.options = {"g": str(gop), "bf": str(bframes)}
    n = int(seconds * FPS)
    for i in range(n):
        img = np.zeros((360, 640, 3), np.uint8)
        img[:, (i * 8) % 600:(i * 8) % 600 + 40] = 255
        for p in st.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
            out.mux(p)
    for p in st.encode():
        out.mux(p)
    out.close()
    return n


class FakeClient:
    mission_id = "MSN-TEST"

    def __init__(self, token: str = "tok", lifetime: float = 3600.0) -> None:
        self.token = token
        self.token_issued = time.time()
        self.token_expiry = self.token_issued + lifetime
        self.refreshes = 0
        self.mission_starts = 0

    async def get_token(self) -> str:
        return self.token

    async def refresh_token(self, force: bool = False) -> str:
        self.refreshes += 1
        self.token_issued = time.time()
        self.token_expiry = self.token_issued + 3600
        return self.token + f"-r{self.refreshes}"

    async def start_mission(self, **_kw) -> None:
        self.mission_starts += 1


def run_mission(tmp_path: Path, server: FakeIngestServer, clip: Path, client=None, speed: float = 4.0,
                wait_s: float = 20.0, **uplink_kw):
    """Play `clip` through UplinkVideoSource + VideoUplink + LocalDecoder; return (uplink, frames, fallback events)."""
    seq = SeqStore(tmp_path / "missions", "MSN-TEST")
    frames, fallbacks, done = [], [], threading.Event()
    holder = {}
    client = client or FakeClient()

    def on_ready(stream, fps):
        if "uplink" in holder:
            return
        up = VideoUplink(url=server.url, client=client, drone_id="DRN-T", mission_id="MSN-TEST", laptop_id="LAP",
                         info=StreamInfo("h264", stream.width, stream.height, fps, "file"), template_stream=stream,
                         ring=src.ring, on_fallback=fallbacks.append, **uplink_kw)
        src.add_packet_listener(up.push)
        holder["uplink"] = up
        up.start()

    src = UplinkVideoSource("DRN-T", str(clip), seq, on_frame=frames.append, on_complete=done.set,
                            on_stream_ready=on_ready, playback_speed=speed)
    src.start()
    assert done.wait(wait_s), "clip did not finish"
    up = holder["uplink"]
    deadline = time.time() + wait_s
    while (up._queue or not up._connected_ws) and time.time() < deadline and not up.fallback_active:
        time.sleep(0.05)
    time.sleep(0.3)
    up.stop()
    src.stop()
    return up, frames, fallbacks, src


@pytest.fixture()
def clip(tmp_path: Path) -> tuple:
    path = tmp_path / "clip.mp4"
    n = make_clip(path)
    return path, n


@pytest.mark.timeout(60)
def test_every_frame_arrives_in_order_with_meta_first(tmp_path: Path, clip) -> None:
    path, n_frames = clip
    server = FakeIngestServer().start()
    try:
        up, frames, fallbacks, src = run_mission(tmp_path, server, path)
    finally:
        server.stop()
    rec = server.rec
    hello = rec.hellos[0]
    assert REQUIRED_KEYS["hello"] <= set(hello) and hello["codec"] == "h264" and hello["container"] == "mpegts"
    seqs = [m["seq"] for m in rec.metas]
    assert len(seqs) == n_frames and seqs == sorted(seqs) and len(set(seqs)) == n_frames
    assert hello["next_seq"] == seqs[0]
    assert all(REQUIRED_KEYS["meta"] <= set(m) for m in rec.metas)
    assert rec.order_errors == 0                                   # every binary chunk follows its meta
    # B-frames: packets arrive in decode order; capture time follows presentation order
    by_pts = sorted(rec.metas, key=lambda m: m["pts_ms"])
    capture = [m["capture_ts"] for m in by_pts]
    assert capture == sorted(capture)
    assert all(len(c) % 188 == 0 for c in rec.ts_chunks)
    decoded = sum(1 for _ in av.open(io.BytesIO(b"".join(rec.ts_chunks)), format="mpegts").decode(video=0))
    assert decoded == n_frames                                      # the TS stream is valid and complete
    assert any(o.get("type") == "bye" for o in rec.other)
    assert not fallbacks and up.dropped_frames == 0
    # LocalDecoder: 10 fps of video time (3 s clip -> ~30 frames, whatever the playback speed)
    assert 27 <= len(frames) <= 31
    assert all(f.width == 960 and f.frame_index in set(seqs) for f in frames)


@pytest.mark.timeout(60)
def test_reconnect_resumes_at_keyframe_without_losing_numbers(tmp_path: Path, clip) -> None:
    path, n_frames = clip
    server = FakeIngestServer(close_after_frames=40, close_code=1011).start()
    try:
        up, _, fallbacks, _ = run_mission(tmp_path, server, path, speed=2.0)
    finally:
        server.stop()
    rec = server.rec
    assert rec.connections >= 2 and up.reconnects >= 1
    second_hello = rec.hellos[1]
    assert second_hello["next_seq"] <= 41                          # resumed at/before the first unsent frame
    assert rec.unique_seqs == set(range(min(rec.unique_seqs), max(rec.unique_seqs) + 1))
    assert len(rec.unique_seqs) == n_frames                        # nothing lost, nothing counted twice
    assert not fallbacks


@pytest.mark.timeout(60)
def test_old_server_404_switches_to_jpeg_fallback(tmp_path: Path, clip) -> None:
    path, _ = clip
    server = FakeIngestServer(reject_404=True).start()
    try:
        up, _, fallbacks, _ = run_mission(tmp_path, server, path, wait_s=10)
    finally:
        server.stop()
    assert fallbacks and fallbacks[0] is True and up.fallback_active
    assert "404" in up.last_error


@pytest.mark.timeout(60)
def test_not_mission_owner_stops_and_reports(tmp_path: Path, clip) -> None:
    path, _ = clip
    errors = []
    server = FakeIngestServer(refuse_owner=True).start()
    try:
        up, _, fallbacks, _ = run_mission(tmp_path, server, path, wait_s=10, on_error=errors.append)
    finally:
        server.stop()
    assert errors and ("not_mission_operator" in errors[0] or "4403" in errors[0])
    assert fallbacks == [True]
    assert server.rec.connections == 1                             # no retry loop against a refusal


@pytest.mark.timeout(60)
def test_token_refreshed_in_band_before_expiry(tmp_path: Path) -> None:
    path = tmp_path / "long.mp4"
    make_clip(path, seconds=6)
    client = FakeClient(lifetime=3.0)                              # 80 % of 3 s -> refresh after 2.4 s
    server = FakeIngestServer().start()
    try:
        run_mission(tmp_path, server, path, client=client, speed=1.0)
    finally:
        server.stop()
    auth = [o for o in server.rec.other if o.get("type") == "auth"]
    assert auth and auth[0]["token"].startswith("tok-r")
    assert server.rec.connections == 1                             # no reconnect needed


def test_backlog_drops_whole_gops_then_recovers() -> None:
    ring = PacketRing()
    up = VideoUplink(url="ws://unused", client=FakeClient(), drone_id="D", mission_id="M", laptop_id="L",
                     info=StreamInfo("h264", 1, 1, 30, "file"), template_stream=None, ring=ring)
    up._running = True                        # push() queues only while the uplink runs
    up._rate_bps = 1000                       # 1 kB/s -> 2 s backlog = 2 kB
    big = b"x" * 1500
    up.push(RingPacket(1, 0, 0, 0.0, True, big))
    up.push(RingPacket(2, 1, 1, 0.0, False, big))
    up.push(RingPacket(3, 2, 2, 0.0, False, big))       # over the backlog: dropping starts
    up.push(RingPacket(4, 3, 3, 0.0, False, big))       # still dropping (not a keyframe)
    assert [p.seq for p in up._queue] == [1, 2] and up.dropped_frames == 2
    up._queue.clear()
    up._queued_bytes = 0
    up.push(RingPacket(5, 4, 4, 0.0, False, big))       # below backlog but mid-GOP: still dropped
    up.push(RingPacket(6, 5, 5, 0.0, True, big))        # next keyframe: sending resumes
    assert [p.seq for p in up._queue] == [6]


def test_packet_ring_is_gop_aligned_and_capped() -> None:
    ring = PacketRing(max_seconds=2.0, max_bytes=10_000)
    ring.add(RingPacket(1, 0, 0, 0.0, False, b"p"))     # never starts with a non-keyframe
    assert ring.stats()["packets"] == 0
    for i in range(1, 91):                               # 3 s at 30 fps, keyframe every 30
        ring.add(RingPacket(i, i, i, i / 30, i % 30 == 1, b"x" * 50))
    pk = ring.from_last_keyframe()
    assert pk[0].key and pk[0].seq == 61
    first = ring.between(0, 99)[0]
    assert first.key                                     # always starts decodable
    assert ring.stats()["seconds"] <= 2.0 + 1.0          # within cap + one GOP
    assert [p.seq for p in ring.gop_for_seq(75)][0] == 61 and ring.gop_for_seq(75)[-1].seq == 75
