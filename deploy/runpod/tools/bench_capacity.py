#!/usr/bin/env python3
"""
Multi-drone capacity benchmark (v2 plan R5.1) — run INSIDE the pod:

    /opt/venvs/ai/bin/python /opt/dvas/deploy/tools/bench_capacity.py --token "$TOKEN" --drones 2
    ... --drones 4 --size 1920x1080 --seconds 90

Streams N simulated drones at once through the v2 uplink (one tools/send_ts.py per drone,
real time, 30 fps H.264 at a drone-like bitrate) and reports what analytics kept up with:
per drone the analytics fps and how far behind live it fell, per analytics service the
largest backlog, and GPU utilisation. Uses its own drones DRN-CAP-01.. and ends their
missions at the end.

Talks to ingest directly (127.0.0.1:8000), not through nginx: nginx allows 4 uplinks per
token. Ingest still caps video connections at DVAS_MAX_LIVE_STREAMS (default 8); drones over
the cap are refused (their log tail is printed at the end).

A run holds 20 fps when every drone shows ≈ 20 analytics fps and "behind" stays ≈ 1 s.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time

import redis

TOOLS = os.path.dirname(os.path.abspath(__file__))


def make_clip(path: str, size: str, kbps: int) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30",
                    "-t", "30", "-c:v", "libx264", "-preset", "veryfast", "-g", "30", "-bf", "0",
                    "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.25)}k", "-bufsize", f"{int(kbps * 1.25)}k",
                    "-pix_fmt", "yuv420p", path], check=True)


def s(v) -> str:
    return v.decode() if isinstance(v, bytes) else ("" if v is None else str(v))


def groups(r: redis.Redis, sid: str) -> dict:
    """{group: lag + pending} of the mission's analytics stream."""
    out = {}
    try:
        for g in r.xinfo_groups(f"mission:frames:{sid}"):
            name = s(g.get("name") or g.get(b"name")).replace("-group", "")
            lag = g.get("lag") if g.get("lag") is not None else g.get(b"lag")
            out[name] = int(lag or 0) + int(g.get("pending") or g.get(b"pending") or 0)
    except redis.ResponseError:
        pass
    return out


def gpu_util() -> str:
    try:
        return subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used",
                               "--format=csv,noheader,nounits"], capture_output=True, text=True,
                              timeout=5).stdout.strip().replace(", ", "% ") + " MiB"
    except Exception:
        return "?"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--token", default=os.getenv("TOKEN", ""), required=not os.getenv("TOKEN"))
    ap.add_argument("--drones", type=int, default=2)
    ap.add_argument("--size", default="3840x2160", help="3840x2160 (default) or 1920x1080")
    ap.add_argument("--kbps", type=int, default=0, help="per drone; default 8000 at 4K, 4000 at 1080p")
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--url", default="http://127.0.0.1:8000", help="ingest, bypassing nginx")
    ap.add_argument("--redis", default=os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"))
    a = ap.parse_args()
    kbps = a.kbps or (8000 if a.size.startswith("3840") else 4000)
    r = redis.Redis.from_url(a.redis)

    tmp = tempfile.mkdtemp(prefix="cap-")
    clip = os.path.join(tmp, "clip.ts")
    print(f"making a 30 s {a.size} clip at {kbps} kbps …")
    make_clip(clip, a.size, kbps)

    run = time.strftime("%H%M%S")
    drones = [(f"DRN-CAP-{i + 1:02d}", f"MSN-CAP-{run}-{i + 1:02d}") for i in range(a.drones)]
    loops = max(1, -(-a.seconds // 30))
    logs: dict = {}
    procs = []
    for drone, mission in drones:
        log = open(os.path.join(tmp, f"{drone}.log"), "w+")
        logs[drone] = log
        procs.append(subprocess.Popen(
            [sys.executable, os.path.join(TOOLS, "send_ts.py"), "--url", a.url, "--token", a.token,
             "--drone", drone, "--mission", mission, "--file", clip, "--loop", str(loops)],
            stdout=log, stderr=subprocess.STDOUT))
        time.sleep(0.5)                                  # stagger the keyframes a little

    print(f"\n{a.drones} drone(s) × {a.size} @ 30 fps for {loops * 30} s — sampling every 5 s")
    print("  t    " + "  ".join(f"{d[-2:]}:fps/behind" for d, _ in drones) + "   slowest service (backlog)   GPU")
    worst_behind = {d: 0.0 for d, _ in drones}
    fps_samples = {d: [] for d, _ in drones}
    worst_backlog: dict = {}
    t0 = time.time()
    while any(p.poll() is None for p in procs):
        time.sleep(5)
        cells, backlog = [], {}
        for drone, sid in drones:
            h = {s(k): s(v) for k, v in r.hgetall(f"mission:video:{sid}").items()}
            if not h:
                cells.append(f"{'no video':>13}")       # refused or not started: see the log tail
                continue
            fps, behind = float(h.get("pub_fps") or 0), float(h.get("behind_live_s") or 0)
            if h.get("status") == "receiving":
                fps_samples[drone].append(fps)
            worst_behind[drone] = max(worst_behind[drone], behind)
            cells.append(f"{fps:5.1f}/{behind:5.1f}s")
            for g, n in groups(r, sid).items():
                backlog[g] = max(backlog.get(g, 0), n)
                worst_backlog[g] = max(worst_backlog.get(g, 0), n)
        slow = max(backlog.items(), key=lambda kv: kv[1]) if backlog else ("-", 0)
        print(f"{time.time() - t0:5.0f}  " + "  ".join(cells) + f"   {slow[0]:>10} ({slow[1]:>4})   {gpu_util()}")

    print("\n== waiting for analytics to finish the backlog …")
    deadline = time.time() + 300
    while time.time() < deadline:
        if all(s(r.hget(f"mission:video:{sid}", "decoder_done")) in ("1", "") for _, sid in drones):
            break
        time.sleep(2)

    print("\n== result")
    print(f"{'drone':<12} {'analytics fps (median while live)':>34} {'max behind live':>16} {'frames':>8}")
    for drone, sid in drones:
        f = sorted(fps_samples[drone])
        med = f[len(f) // 2] if f else 0.0
        n = s(r.hget(f"mission:video:{sid}", "analytics_seq")) or "-"
        print(f"{drone:<12} {med:>34.1f} {worst_behind[drone]:>15.1f}s {n:>8}")
    print("\nlargest backlog per service (frames; 20 fps × seconds behind):")
    for g, n in sorted(worst_backlog.items(), key=lambda kv: -kv[1]):
        print(f"  {g:<12} {n}")
    failed = [d for (d, _), p in zip(drones, procs) if p.returncode]
    if failed:
        print("\nsenders that stopped with an error (log tail):")
        for d in failed:
            print(f"  {d}: " + " | ".join(open(logs[d].name).read().strip().splitlines()[-3:]))
    print(f"\nlogs: {tmp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
