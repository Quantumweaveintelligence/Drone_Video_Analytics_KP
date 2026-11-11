#!/usr/bin/env python3
"""
Test sender for the v2 video uplink (plan §3) — stands in for the edge (E2) until it ships.

Streams a video file to /ingest/video/ws as H.264 MPEG-TS with one `meta` message per frame
(synthetic telemetry), paced in real time, after starting the mission over HTTP.

  # from a machine that can reach the pod; TOKEN = an operator's access token
  python send_ts.py --url https://<pod>-80.proxy.runpod.net --token "$TOKEN" \
      --drone DRN-900 --mission MSN-TEST-001 --file clip.mp4 [--loop 3] [--transcode]

  --transcode   re-encode to H.264 (libx264, keyframe every second, no B-frames) — needed
                for HEVC sources; H.264 sources are copied as they are.
  --no-start    the mission was started elsewhere;  --no-end  leave it running.

Prints the server's `stats` messages (rx fps, analytics fps, lag) as they arrive.
Needs: pip install av websockets httpx
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import time
from typing import Iterator, Tuple


class _Sink(io.RawIOBase):
    def __init__(self):
        self.buf = bytearray()

    def writable(self):
        return True

    def write(self, b):
        self.buf += b
        return len(b)


def ts_chunks(path: str, transcode: bool = False, first_seq: int = 1,
              capture_t0: float = None, pts_offset_s: float = 0.0) -> Iterator[Tuple[dict, bytes, float]]:
    """(meta, ts_bytes, pts_s) per video frame, in send order. ts_bytes is a whole number of
    188-byte TS packets; a keyframe's bytes start a new chunk (meta first, then bytes).
    pts_offset_s shifts every timestamp: with --loop each pass continues after the previous
    one, because PTS must keep increasing within a connection (contract §3)."""
    import av  # noqa: PLC0415
    src = av.open(path)
    vin = src.streams.video[0]
    sink = _Sink()
    out = av.open(sink, mode="w", format="mpegts", options={"flush_packets": "1"})
    if transcode or vin.codec_context.name != "h264":
        vout = out.add_stream("libx264", rate=vin.average_rate or 30)
        vout.width, vout.height = vin.codec_context.width, vin.codec_context.height
        vout.pix_fmt = "yuv420p"
        fps = float(vin.average_rate or 30)
        vout.options = {"g": str(max(1, round(fps))), "bf": "0", "tune": "zerolatency", "preset": "veryfast"}
        packets = (p for f in src.decode(vin) for p in vout.encode(f))
        tail = lambda: vout.encode(None)  # noqa: E731
    else:
        vout = out.add_stream(template=vin)
        packets = (p for p in src.demux(vin) if p.size)
        tail = lambda: []  # noqa: E731
    fps = float(vin.average_rate or 30)
    t0 = time.time() if capture_t0 is None else capture_t0
    seq = first_seq
    carry = bytearray()
    first_pts = None

    def emit(pkt):
        nonlocal seq, first_pts, carry
        tb = float(pkt.time_base or vout.time_base)
        if pts_offset_s:
            shift = round(pts_offset_s / tb)
            if pkt.pts is not None:
                pkt.pts += shift
            if pkt.dts is not None:
                pkt.dts += shift
        pts_s = (pkt.pts if pkt.pts is not None else pkt.dts) * tb
        if first_pts is None:
            first_pts = pts_s
        meta = {"type": "meta", "seq": seq, "pts_ms": round(pts_s * 1000.0, 3),
                "capture_ts": round(t0 + (pts_s - first_pts), 3), "key": bool(pkt.is_keyframe),
                "change_score": 0.1,
                "tel": {"latitude": 8.5241 + seq * 1e-6, "longitude": 76.9366, "altitude_m": 80.0,
                        "gb_yaw": 140.0, "gb_pitch": -35.0, "gb_roll": 0.0, "focal_len": 24.0}}
        seq += 1
        pkt.stream = vout
        out.mux(pkt)
        carry += sink.buf
        sink.buf.clear()
        n = len(carry) - len(carry) % 188
        data = bytes(carry[:n])
        del carry[:n]
        return meta, data, pts_s - first_pts

    for pkt in packets:
        yield emit(pkt)
    for pkt in tail():
        yield emit(pkt)
    out.close()
    carry += sink.buf
    n = len(carry) - len(carry) % 188
    if n:
        yield None, bytes(carry[:n]), None
    src.close()


async def stream(args) -> int:
    import httpx  # noqa: PLC0415
    import websockets  # noqa: PLC0415
    base = args.url.rstrip("/")
    hdr = {"Authorization": f"Bearer {args.token}"}
    if not args.no_start:
        r = httpx.post(f"{base}/ingest/mission/start", headers=hdr, timeout=20, verify=not args.insecure,
                       json={"drone_id": args.drone, "mission_id": args.mission, "mission_name": "v2 test"})
        print("mission/start", r.status_code, r.text[:200])
        if r.status_code >= 300:
            return 1
    import av  # noqa: PLC0415
    with av.open(args.file) as probe:
        v = probe.streams.video[0]
        w, h, fps = v.codec_context.width, v.codec_context.height, float(v.average_rate or 30)
    ws_url = base.replace("https://", "wss://").replace("http://", "ws://") + \
        f"/ingest/video/ws?drone_id={args.drone}&mission_id={args.mission}"
    seq = 1
    try:
        conn = websockets.connect(ws_url, additional_headers=hdr, max_size=None, ping_interval=15)
    except TypeError:                                   # websockets < 13
        conn = websockets.connect(ws_url, extra_headers=hdr, max_size=None, ping_interval=15)
    async with conn as ws:
        await ws.send(json.dumps({"type": "hello", "v": 1, "drone_id": args.drone, "mission_id": args.mission,
                                  "laptop_id": "send_ts", "codec": "h264", "container": "mpegts",
                                  "width": w, "height": h, "fps": fps, "source": "file", "next_seq": seq}))
        ready = json.loads(await ws.recv())
        print("server:", ready)
        if ready.get("type") != "ready":
            if not args.no_start and not args.no_end:     # do not leave the test mission running
                r = httpx.post(f"{base}/ingest/mission/end", headers=hdr, timeout=20, verify=not args.insecure,
                               json={"drone_id": args.drone, "mission_id": args.mission})
                print("mission/end", r.status_code, r.text[:200])
            return 1
        seq = int(ready.get("last_seq") or 0) + 1

        async def show():
            try:
                async for msg in ws:
                    if isinstance(msg, str):
                        print("server:", msg)
            except websockets.ConnectionClosed as exc:
                print("server closed:", exc)
        printer = asyncio.create_task(show())
        start = time.monotonic()
        offset = 0.0
        rc = 0
        try:
            for _ in range(max(1, args.loop)):
                last = 0.0
                for meta, data, t in ts_chunks(args.file, args.transcode, first_seq=seq, pts_offset_s=offset):
                    if meta is not None:
                        wait = start + offset + t - time.monotonic()
                        if wait > 0:
                            await asyncio.sleep(wait)
                        await ws.send(json.dumps(meta))
                        seq = meta["seq"] + 1
                        last = t
                    for i in range(0, len(data), 188 * 1300):
                        await ws.send(data[i:i + 188 * 1300])
                offset += last + 1.0 / fps
            await ws.send(json.dumps({"type": "bye", "last_seq": seq - 1}))
        except websockets.ConnectionClosed as exc:  # refused mid-stream: still end the test mission below
            print("stream stopped:", exc)
            rc = 1
        printer.cancel()
    if not args.no_end:
        r = httpx.post(f"{base}/ingest/mission/end", headers=hdr, timeout=20, verify=not args.insecure,
                       json={"drone_id": args.drone, "mission_id": args.mission})
        print("mission/end", r.status_code, r.text[:200])
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--drone", required=True)
    ap.add_argument("--mission", required=True)
    ap.add_argument("--file", required=True)
    ap.add_argument("--loop", type=int, default=1)
    ap.add_argument("--transcode", action="store_true")
    ap.add_argument("--no-start", action="store_true")
    ap.add_argument("--no-end", action="store_true")
    ap.add_argument("--insecure", action="store_true", help="do not verify TLS (LAN test only)")
    return asyncio.run(stream(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
