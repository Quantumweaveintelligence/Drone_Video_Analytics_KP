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

Per drone each sample shows what the decoder published (pub), what the slowest analytics
service really analysed (an — from its dvas:svc_stats, with its shedding level when it sheds)
and how far the decoder is behind live. A shedding service analyses fewer frames than are
published, so pub alone is not the analytics rate.

A run holds 20 fps when every drone shows an ≈ 20, no shedding level, and "behind" ≈ 1 s.
"""
from __future__ import annotations

import argparse
import json
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


# Services that analyse only the newest frame (crowd, vessel by default, R6.1): their low fps is
# by design, so they are not "the slowest service" of a drone.
NEWEST: set = set()


def svc_drones(r: redis.Redis) -> dict:
    """{service: {drone: {"fps", "shed_level", "stages"}}} from the services' published stats."""
    out = {}
    for key in r.scan_iter("dvas:svc_stats:*"):
        try:
            d = json.loads(s(r.get(key)) or "{}")
        except ValueError:
            continue
        if time.time() - float(d.get("updated_at") or 0) > 10:
            continue                                     # stopped service
        svc = d.get("service") or s(key).rsplit(":", 1)[-1]
        out[svc] = d.get("drones") or {}
        if d.get("mode") == "newest":
            NEWEST.add(svc)
    return out


def slowest(stats: dict, drone: str):
    """(fps, service, shed level) of the every-frame service that analysed the fewest frames of
    this drone (newest-frame services are left out: they skip frames by design)."""
    rows = [(float(v[drone].get("fps") or 0), svc, int(v[drone].get("shed_level") or 0))
            for svc, v in stats.items() if drone in v and svc not in NEWEST]
    return min(rows) if rows else None


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def cpu_quota() -> float:
    """The pod's CPU limit in cores (cgroup v2 cpu.max, else v1), 0 when unlimited.
    nproc and top show the whole host; the pod only gets this much."""
    q = _read("/sys/fs/cgroup/cpu.max").split()
    if len(q) == 2 and q[0].isdigit():
        return int(q[0]) / int(q[1])
    q, per = _read("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").strip(), _read("/sys/fs/cgroup/cpu/cpu.cfs_period_us").strip()
    if q.lstrip("-").isdigit() and int(q) > 0 and per.isdigit():
        return int(q) / int(per)
    return 0.0


def cpu_counters():
    """(cpu seconds used, seconds throttled) of the pod so far."""
    st = dict(l.split() for l in _read("/sys/fs/cgroup/cpu.stat").splitlines() if len(l.split()) == 2)
    if "usage_usec" in st:
        return int(st["usage_usec"]) / 1e6, int(st.get("throttled_usec", 0)) / 1e6
    used = _read("/sys/fs/cgroup/cpuacct/cpuacct.usage").strip()
    st = dict(l.split() for l in _read("/sys/fs/cgroup/cpu/cpu.stat").splitlines() if len(l.split()) == 2)
    return (int(used) / 1e9 if used.isdigit() else 0.0), int(st.get("throttled_time", 0)) / 1e9


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
    # More than 6 drones (R9): one summary per sample instead of a column per drone
    compact = len(drones) > 6
    if compact:
        # rx = video frames ingest received (works with every service off); pub = frames the
        # decoder published for analytics (0 when no service is selected, R6.2)
        print("  t    recv  slots  rx fps min/med  pub fps min/med  an fps min/med  worst behind   most backlog (frames)   GPU")
    else:
        print("  t    " + "  ".join(f"{d[-2:]}: pub/ an  behind" for d, _ in drones) + "   most backlog (frames)   GPU")
    worst_behind = {d: 0.0 for d, _ in drones}
    fps_samples = {d: [] for d, _ in drones}
    an_samples = {d: [] for d, _ in drones}
    max_shed: dict = {}
    svc_fps: dict = {}          # service → drone → analysed fps samples while receiving
    rx_prev: dict = {}          # drone → (last_seq_rx, time) at the previous sample
    rx_samples = {d: [] for d, _ in drones}
    svc_stages: dict = {}       # service → last stage ms (EMA) of any drone
    worst_backlog: dict = {}
    quota = cpu_quota()
    print(f"CPU limit of this pod: {quota:.0f} cores" if quota else "CPU: no pod limit found")
    t0 = time.time()
    cpu_prev, cpu_t = cpu_counters(), time.time()
    worst_cpu = 0.0
    while any(p.poll() is None for p in procs):
        time.sleep(5)
        cpu_now, now = cpu_counters(), time.time()
        cores = (cpu_now[0] - cpu_prev[0]) / max(0.1, now - cpu_t)
        thr = (cpu_now[1] - cpu_prev[1]) / max(0.1, now - cpu_t)
        cpu_prev, cpu_t = cpu_now, now
        worst_cpu = max(worst_cpu, cores)
        cpu_txt = f"CPU {cores:4.1f}" + (f"/{quota:.0f}" if quota else "") + (f" thr {thr:.0%}" if thr > 0.005 else "")
        cells, backlog = [], {}
        recv, slots, pubs, ans, behinds, rxs = 0, 0, [], [], [], []
        stats = svc_drones(r)
        for drone, sid in drones:
            h = {s(k): s(v) for k, v in r.hgetall(f"mission:video:{sid}").items()}
            if not h:
                cells.append(f"{'no video':>18}")       # refused or not started: see the log tail
                continue
            fps, behind = float(h.get("pub_fps") or 0), float(h.get("behind_live_s") or 0)
            slow = slowest(stats, drone)
            if h.get("status") == "receiving":
                recv += 1
                pubs.append(fps)
                behinds.append(behind)
                if slow:
                    ans.append(slow[0])
            if h.get("live_slot"):
                slots += 1
            seq_rx, t_rx = float(h.get("last_seq_rx") or 0), time.time()
            if drone in rx_prev and h.get("status") == "receiving":
                rx = max(0.0, (seq_rx - rx_prev[drone][0]) / max(0.1, t_rx - rx_prev[drone][1]))
                rxs.append(rx)
                rx_samples[drone].append(rx)
            rx_prev[drone] = (seq_rx, t_rx)
            an = f"{slow[0]:4.1f}" if slow else "   -"
            mark = f"L{slow[2]}" if slow and slow[2] else "  "
            if h.get("status") == "receiving":
                for svc, v in stats.items():
                    if drone in v:
                        svc_fps.setdefault(svc, {}).setdefault(drone, []).append(float(v[drone].get("fps") or 0))
                        lvl = int(v[drone].get("shed_level") or 0)
                        if lvl:
                            max_shed[svc] = max(max_shed.get(svc, 0), lvl)
                        if v[drone].get("stages"):
                            svc_stages[svc] = v[drone]["stages"]
                fps_samples[drone].append(fps)
                if slow:
                    an_samples[drone].append(slow[0])
            worst_behind[drone] = max(worst_behind[drone], behind)
            cells.append(f"{fps:4.1f}/{an}{mark}{behind:4.1f}s")
            for g, n in groups(r, sid).items():
                backlog[g] = max(backlog.get(g, 0), n)
                worst_backlog[g] = max(worst_backlog.get(g, 0), n)
        slow = max(backlog.items(), key=lambda kv: kv[1]) if backlog else ("-", 0)
        if compact:
            mm = lambda v: (f"{min(v):5.1f}/{sorted(v)[len(v) // 2]:5.1f}" if v else "    -/    -")  # noqa: E731
            print(f"{time.time() - t0:5.0f}  {recv:>3}/{len(drones):<3} {slots:>4}  {mm(rxs):>14}  {mm(pubs):>15}  {mm(ans):>14}"
                  f"  {max(behinds, default=0.0):10.1f}s   {slow[0]:>10} ({slow[1]:>4})   {gpu_util()}   {cpu_txt}")
        else:
            print(f"{time.time() - t0:5.0f}  " + "  ".join(cells) + f"   {slow[0]:>10} ({slow[1]:>4})   {gpu_util()}   {cpu_txt}")

    print("\n== waiting for analytics to finish the backlog …")
    deadline = time.time() + 300
    while time.time() < deadline:
        if all(s(r.hget(f"mission:video:{sid}", "decoder_done")) in ("1", "") for _, sid in drones):
            break
        time.sleep(2)

    print("\n== result")
    print(f"{'drone':<12} {'received fps':>13} {'published fps':>14} {'analysed fps (slowest every-frame service)':>43} "
          f"{'max behind live':>16} {'frames':>8}")
    med = lambda v: sorted(v)[len(v) // 2] if v else 0.0  # noqa: E731
    for drone, sid in drones:
        n = s(r.hget(f"mission:video:{sid}", "analytics_seq")) or "-"
        print(f"{drone:<12} {med(rx_samples[drone]):>13.1f} {med(fps_samples[drone]):>14.1f} {med(an_samples[drone]):>43.1f} "
              f"{worst_behind[drone]:>15.1f}s {n:>8}")
    print("\nanalysed fps per service (median over the run, per drone; newest = newest frame only):")
    for svc in sorted(svc_fps):
        per = [med(v) for v in svc_fps[svc].values()]
        stages = " ".join(f"{k} {float(ms):.0f}" for k, ms in (svc_stages.get(svc) or {}).items())
        print(f"  {svc:<10} {'newest' if svc in NEWEST else 'every ':<7} "
              f"{' '.join(f'{x:5.1f}' for x in per)}" + (f"   stages ms: {stages}" if stages else ""))
    print(f"\nCPU: up to {worst_cpu:.1f} cores used" + (f" of the pod's {quota:.0f}" if quota else ""))
    if max_shed:
        print("\nshedding (highest level per service; 1 secondary off, 2 drop-oldest, 3 lower cadence):")
        for svc, lvl in sorted(max_shed.items()):
            print(f"  {svc:<12} level {lvl}")
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
