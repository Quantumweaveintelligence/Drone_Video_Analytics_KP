from __future__ import annotations

import io
from pathlib import Path

import av
import numpy as np
from ingest.seq_store import SeqStore
from ingest.ts_muxer import TsMuxer
from src.buffer.packet_ring import RingPacket
from src.video.h264_transcoder import H264Transcoder
from tests.test_video_uplink import FakeIngestServer, make_clip, run_mission


def test_transcoder_emits_decodable_h264_with_monotonic_identity(tmp_path: Path) -> None:
    source_path = tmp_path / "source.mp4"
    frame_count = make_clip(source_path, seconds=2.0, bframes=2)
    container = av.open(str(source_path))
    stream = container.streams.video[0]
    transcoder = H264Transcoder(stream, SeqStore(tmp_path / "seq", "MISSION-T"), 30.0,
                                max_kbps=1000, max_width=320, encoder="libx264")
    encoded = []
    for seq, packet in enumerate(container.demux(stream), 1):
        if packet.pts is None and packet.dts is None:
            continue
        source = RingPacket(seq, packet.pts, packet.dts, seq / 30.0, bool(packet.is_keyframe), bytes(packet))
        encoded.extend(transcoder.process(source))
    encoded.extend(transcoder.flush())
    container.close()

    assert transcoder.width == 320 and transcoder.height == 180
    assert transcoder.encoder_name == "libx264"
    assert len(encoded) == frame_count
    assert [packet.seq for packet in encoded] == sorted(packet.seq for packet in encoded)
    assert encoded[0].key

    muxer = TsMuxer(transcoder.template)
    chunks = [muxer.mux(p.data, p.pts, p.dts, p.key) for p in encoded]
    chunks.append(muxer.close())
    ts = b"".join(chunks)
    decoded = sum(1 for _ in av.open(io.BytesIO(ts), format="mpegts").decode(video=0))
    assert decoded == frame_count


def test_high_bitrate_source_is_transcoded_before_websocket_uplink(tmp_path: Path) -> None:
    source_path = tmp_path / "high-bitrate.mp4"
    out = av.open(str(source_path), "w")
    stream = out.add_stream("libx264", rate=30)
    stream.width, stream.height, stream.pix_fmt = 640, 360, "yuv420p"
    stream.options = {"preset": "ultrafast", "tune": "zerolatency", "crf": "10", "bf": "0", "g": "30"}
    rng = np.random.default_rng(9)
    frame_count = 30
    for _ in range(frame_count):
        image = rng.integers(0, 256, size=(360, 640, 3), dtype=np.uint8)
        for packet in stream.encode(av.VideoFrame.from_ndarray(image, format="rgb24")):
            out.mux(packet)
    for packet in stream.encode():
        out.mux(packet)
    out.close()

    server = FakeIngestServer().start()
    try:
        up, _, fallbacks, source = run_mission(
            tmp_path, server, source_path, speed=4.0, wait_s=30,
            source_kw={"transcode_max_kbps": 250, "transcode_max_width": 320,
                       "transcode_encoder": "libx264"},
        )
    finally:
        server.stop()
    assert source.source_bitrate > 250_000
    assert source._transcoder is not None and source._transcoder.encoder_name == "libx264"
    assert server.rec.hellos[0]["width"] == 320 and server.rec.hellos[0]["height"] == 180
    seqs = [meta["seq"] for meta in server.rec.metas]
    assert seqs == sorted(seqs) and len(seqs) == frame_count
    assert not fallbacks and up.dropped_frames == 0
    decoded = sum(1 for _ in av.open(io.BytesIO(b"".join(server.rec.ts_chunks)), format="mpegts").decode(video=0))
    assert decoded == frame_count

