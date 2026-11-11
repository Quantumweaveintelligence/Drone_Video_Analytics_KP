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
from ingest.video_uplink import LOG_TEXT_MAX, StreamInfo, VideoUplink, _clip
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
                wait_s: float = 20.0, source_kw: dict | None = None, **uplink_kw):
    """Play `clip` through UplinkVideoSource + VideoUplink + LocalDecoder; return (uplink, frames, fallback events)."""
    seq = SeqStore(tmp_path / "missions", "MSN-TEST")
    frames, fallbacks, done = [], [], threading.Event()
    holder = {}
    client = client or FakeClient()

    def on_ready(stream, fps):
        if "uplink" in holder:
            return
        src = holder["source"]
        template = src.uplink_template
        up = VideoUplink(url=server.url, client=client, drone_id="DRN-T", mission_id="MSN-TEST", laptop_id="LAP",
                         info=StreamInfo(src.uplink_codec, src.uplink_width, src.uplink_height, fps, "file"),
                         template_stream=template, ring=src.ring, on_fallback=fallbacks.append, **uplink_kw)
        src.add_packet_listener(up.push)
        holder["uplink"] = up
        up.start()

    src = UplinkVideoSource("DRN-T", str(clip), seq, on_frame=frames.append, on_complete=done.set,
                            on_stream_ready=on_ready, playback_speed=speed, **(source_kw or {}))
    holder["source"] = src
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


@pytest.mark.timeout(120)
def test_repeated_mid_stream_drops_never_fall_back_to_jpeg(tmp_path: Path, monkeypatch) -> None:
    """A link that drops and reconnects must keep streaming video: reaching 'ready' resets the
    strike count, so repeated mid-stream drops never reach the JPEG fallback. The limit is lowered
    so that the reliably-produced handful of drops already exceeds it (a revert would fall back)."""
    import ingest.video_uplink as vu
    monkeypatch.setattr(vu, "FAILS_BEFORE_FALLBACK", 2)
    path = tmp_path / "long.mp4"
    make_clip(path, seconds=12.0)                       # keeps producing frames across several drops
    server = FakeIngestServer(close_after_frames=8, close_code=1011, close_conns=20).start()
    try:
        up, _, fallbacks, _ = run_mission(tmp_path, server, path, speed=2.0, wait_s=40)
    finally:
        server.stop()
    assert server.rec.connections >= 3                 # more drops than the (lowered) 2-strike limit
    assert fallbacks == [] and not up.fallback_active   # the reset keeps it on video, never JPEG


def test_count_failure_resets_after_a_working_connection() -> None:
    from ingest.video_uplink import FAILS_BEFORE_FALLBACK, StreamInfo, VideoUplink
    up = VideoUplink(url="ws://x", client=FakeClient(), drone_id="D", mission_id="M", laptop_id="L",
                     info=StreamInfo("h264", 640, 360, 30.0, "file"), template_stream=None,
                     ring=PacketRing())
    up._session_connected = False
    assert up._count_failure(3) == 4                    # never connected: strikes accumulate
    up._session_connected = True
    assert up._count_failure(FAILS_BEFORE_FALLBACK - 1) == 1   # a drop after connecting is strike 1


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
def test_server_close_code_and_fallback_reason_are_logged(tmp_path: Path, clip, caplog) -> None:
    path, _ = clip
    client = FakeClient(token="s3cr3t-value")
    server = FakeIngestServer(token="s3cr3t-value", close_after_frames=40, close_code=4409).start()
    try:
        with caplog.at_level("WARNING", logger="video_uplink"):
            run_mission(tmp_path, server, path, client=client, speed=2.0)
    finally:
        server.stop()
    assert any("uplink_closed code=4409 reason=test close" in r.getMessage() for r in caplog.records)
    assert client.mission_starts >= 1                              # 4409: mission/start, then reconnect
    assert not any("s3cr3t" in r.getMessage() for r in caplog.records)  # no token in the log


@pytest.mark.timeout(60)
def test_refusal_reason_is_in_the_fallback_log(tmp_path: Path, clip, caplog) -> None:
    path, _ = clip
    server = FakeIngestServer(refuse_owner=True).start()
    try:
        with caplog.at_level("WARNING", logger="video_uplink"):
            run_mission(tmp_path, server, path, wait_s=10)
    finally:
        server.stop()
    lines = [r.getMessage() for r in caplog.records if "uplink_fallback_on" in r.getMessage()]
    assert lines and "reason=" in lines[0] and "reason=-" not in lines[0]


def test_big_frames_are_split_into_contract_sized_chunks() -> None:
    from ingest.video_uplink import MAX_CHUNK
    assert MAX_CHUNK <= 256 * 1024 and MAX_CHUNK % 188 == 0
    ts = bytes(188 * 3000)                                   # 564 KB, like a 1080p60 DJI keyframe
    chunks = [ts[o:o + MAX_CHUNK] for o in range(0, len(ts), MAX_CHUNK)]
    assert b"".join(chunks) == ts
    assert all(len(c) <= 256 * 1024 and len(c) % 188 == 0 for c in chunks)


def test_server_text_cannot_forge_log_lines() -> None:
    hostile = "bye\r\n2026-11-10 ERROR forged line\n" + "x" * 1000
    clipped = _clip(hostile)
    assert "\n" not in clipped and "\r" not in clipped
    assert len(clipped) == LOG_TEXT_MAX


def test_disk_spool_replays_in_sequence_and_rewinds_to_server_ack(tmp_path: Path) -> None:
    spool_path = tmp_path / "packets.sqlite3"
    up = VideoUplink(url="ws://x", client=FakeClient(), drone_id="D", mission_id="M", laptop_id="L",
                     info=StreamInfo("h264", 640, 360, 30.0, "file"), template_stream=None,
                     ring=PacketRing(), spool_path=spool_path, spool_max_bytes=1024)
    up._running = True
    packets = [RingPacket(10, 0, 0, 1.0, True, bytes(188)),
               RingPacket(11, 1, 1, 1.03, False, bytes(188))]
    for pkt in packets:
        up.push(pkt)
    assert up._spool.stats()["packets"] == 2
    assert up._resume_point().seq == 10
    assert [up._pop().seq, up._pop().seq] == [10, 11]
    # A connection can fail after local send; reconnect from the server's durable floor.
    up._spool_cursor = 11
    up._connected_ws = object()
    live = [RingPacket(12, 2, 2, 1.06, False, bytes(188)),
            RingPacket(13, 3, 3, 1.10, False, bytes(188))]
    for pkt in live:
        up.push(pkt)
    assert [up._pop().seq, up._pop().seq] == [12, 13]  # new capture flows while replay catches up
    up._spool_cursor = 10                   # server ack was lower than the local send cursor
    assert up._spool.first(up._spool_cursor).seq == 11
    up._spool.acknowledge(10)
    assert up._spool.stats()["first_seq"] == 11
    up.close_spool()
    from ingest.packet_spool import PacketSpool
    reopened = PacketSpool(spool_path, max_bytes=1024)
    assert reopened.first().seq == 11               # disk rows survive process/uplink recreation
    reopened.close()


def test_disk_spool_cap_is_enforced_without_partial_packets(tmp_path: Path) -> None:
    from ingest.packet_spool import PacketSpool
    spool = PacketSpool(tmp_path / "bounded.sqlite3", max_bytes=187)
    assert not spool.put(RingPacket(1, 0, 0, 1.0, True, bytes(188)))
    assert spool.stats()["packets"] == 0 and spool.stats()["bytes"] == 0
    spool.close()


def test_first_connection_starts_at_oldest_retained_keyframe() -> None:
    ring = PacketRing()
    for seq, key in ((1, True), (2, False), (3, True), (4, False)):
        ring.add(RingPacket(seq, seq, seq, float(seq), key, bytes(188)))
    up = VideoUplink(url="ws://x", client=FakeClient(), drone_id="D", mission_id="M", laptop_id="L",
                     info=StreamInfo("h264", 640, 360, 30.0, "file"), template_stream=None, ring=ring)
    assert up._resume_point().seq == 1


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
