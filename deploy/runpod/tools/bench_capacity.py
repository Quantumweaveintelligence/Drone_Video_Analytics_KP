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

--observe: start no senders; measure whatever drones are streaming (e.g. the edge team's real
video) for --seconds.
--file <video>: stream a real recording instead of the synthetic clip. It is re-encoded once
(same resolution and frame rate, H.264, 1 s keyframes, no B-frames, drone-like bitrate) and
looped for --seconds. run() returns every figure as a dict (bench_stress.py builds on it).
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import resource
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


def _probe(path: str) -> dict:
    """width, height, fps, duration of a video's first video stream (ffprobe)."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,avg_frame_rate:format=duration", "-of", "json", path],
                         capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    st = (d.get("streams") or [{}])[0]
    num, _, den = str(st.get("avg_frame_rate") or "30/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    return {"width": int(st.get("width") or 0), "height": int(st.get("height") or 0),
            "fps": round(fps, 2), "duration": float((d.get("format") or {}).get("duration") or 0)}


def prepare_file(src: str, out: str, kbps: int = 0) -> dict:
    """A real recording as the uplink expects it: H.264 TS, keyframe every second, no B-frames,
    no audio, at most 60 fps; resolution kept. Returns its probe + the bitrate used."""
    info = _probe(src)
    fps = min(60.0, info["fps"] or 30.0)
    kbps = kbps or (8000 if info["height"] >= 2000 else 4000 if info["height"] >= 1000 else 2500)
    g = max(1, round(fps))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-an", "-r", f"{fps:g}", "-c:v", "libx264",
                    "-preset", "veryfast", "-g", str(g), "-keyint_min", str(g), "-sc_threshold", "0", "-bf", "0",
                    "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.25)}k", "-bufsize", f"{int(kbps * 1.25)}k",
                    "-pix_fmt", "yuv420p", "-f", "mpegts", out], check=True)
    info = {**_probe(out), "kbps": kbps}
    return info


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


def live_missions(r: redis.Redis) -> dict:
    """{drone_id: {"sid", "width", "height", "fps"}} of the missions receiving video now."""
    out = {}
    for key in r.scan_iter("mission:video:*"):
        h = {s(k): s(v) for k, v in r.hgetall(key).items()}
        if h.get("status") == "receiving" and h.get("drone_id"):
            out[h["drone_id"]] = {"sid": s(key).split(":", 2)[2], "width": int(float(h.get("width") or 0)),
                                  "height": int(float(h.get("height") or 0)), "fps": float(h.get("fps") or 0)}
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


def gpu_sample():
    """(busy %, VRAM used MiB, VRAM total MiB) of GPU 0, or None. Busy % is nvidia-smi's
    utilization.gpu: the share of time a kernel ran (the `sm` column of `nvidia-smi dmon`)."""
    try:
        line = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
                               "--format=csv,noheader,nounits"], capture_output=True, text=True,
                              timeout=5).stdout.strip().splitlines()[0]
        u, m, t = (float(x) for x in line.split(","))
        return u, m, t
    except Exception:
        return None


def gpu_apps() -> dict:
    """{program: VRAM MiB} of the processes on the GPU, named by what they run."""
    out: dict = {}
    try:
        rows = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,used_memory",
                               "--format=csv,noheader,nounits"], capture_output=True, text=True,
                              timeout=5).stdout.strip().splitlines()
    except Exception:
        return out
    for row in rows:
        try:
            pid, mem = (x.strip() for x in row.split(","))
            cmd = _read(f"/proc/{pid}/cmdline").replace("\0", " ")
        except ValueError:
            continue
        name = next((w.split(".")[0] for w in cmd.split() if w.endswith(".main:app")), None)
        name = name or ("video-decoder" if "video_decoder" in cmd else "ffmpeg" if "ffmpeg" in cmd else f"pid {pid}")
        out[name] = out.get(name, 0.0) + float(mem)
    return out


def gpu_util() -> str:
    try:
        return subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used",
                               "--format=csv,noheader,nounits"], capture_output=True, text=True,
                              timeout=5).stdout.strip().replace(", ", "% ") + " MiB"
    except Exception:
        return "?"


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--token", default=os.getenv("TOKEN", ""))
    ap.add_argument("--token-file", default="", help="senders re-read the token from this file (long runs)")
    ap.add_argument("--drones", type=int, default=2)
    ap.add_argument("--size", default="3840x2160", help="synthetic clip: 3840x2160 (default) or 1920x1080")
    ap.add_argument("--file", default="", help="stream this real recording instead of a synthetic clip")
    ap.add_argument("--kbps", type=int, default=0, help="per drone; default 8000 at 4K, 4000 at 1080p")
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--url", default="http://127.0.0.1:8000", help="ingest, bypassing nginx")
    ap.add_argument("--redis", default=os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"))
    ap.add_argument("--observe", action="store_true",
                    help="start no senders: measure the drones that are streaming now, for --seconds")
    return ap


def main() -> int:
    a = parser().parse_args()
    if not a.observe and not a.token and not a.token_file:
        parser().error("--token (or TOKEN in the environment) is required")
    run(a)
    return 0


def run(a, clip_info: dict = None) -> dict:
    """One run; prints as it goes and returns the figures. clip_info: a clip prepared earlier
    ({"path", "width", "height", "fps", "duration", "kbps"}) to reuse instead of making one."""
    r = redis.Redis.from_url(a.redis)
    tmp = tempfile.mkdtemp(prefix="cap-")
    observe = bool(getattr(a, "observe", False))
    seen: dict = {}             # observe: drone → its video (resolution, fps) as ingest reports it
    if observe:
        clip_info = {"path": "", "width": 0, "height": 0, "fps": 0.0, "duration": float(a.seconds), "kbps": 0}
    elif clip_info:
        clip = clip_info["path"]
    elif getattr(a, "file", ""):
        clip = os.path.join(tmp, "clip.ts")
        print(f"preparing {a.file} (H.264, 1 s keyframes, no B-frames) …")
        clip_info = {**prepare_file(a.file, clip, a.kbps), "path": clip}
    else:
        kbps = a.kbps or (8000 if a.size.startswith("3840") else 4000)
        clip = os.path.join(tmp, "clip.ts")
        print(f"making a 30 s {a.size} clip at {kbps} kbps …")
        make_clip(clip, a.size, kbps)
        w, _, h = a.size.partition("x")
        clip_info = {"path": clip, "width": int(w), "height": int(h), "fps": 30.0, "duration": 30.0, "kbps": kbps}
    a.size = f"{clip_info['width']}x{clip_info['height']}"
    clip_s = max(1.0, clip_info["duration"])

    run_id = time.strftime("%H%M%S")
    drones = [(f"DRN-CAP-{i + 1:02d}", f"MSN-CAP-{run_id}-{i + 1:02d}") for i in range(0 if observe else a.drones)]
    loops = 1 if observe else max(1, int(-(-a.seconds // clip_s)))
    logs: dict = {}
    procs = []
    # CPU the senders themselves use (they run on the pod): subtract it from the pod's figure
    ru0, t_send0 = resource.getrusage(resource.RUSAGE_CHILDREN), time.time()
    for drone, mission in drones:
        log = open(os.path.join(tmp, f"{drone}.log"), "w+")
        logs[drone] = log
        procs.append(subprocess.Popen(
            [sys.executable, os.path.join(TOOLS, "send_ts.py"), "--url", a.url, "--token", a.token,
             *(["--token-file", a.token_file] if getattr(a, "token_file", "") else []),
             "--drone", drone, "--mission", mission, "--file", clip, "--loop", str(loops)],
            stdout=log, stderr=subprocess.STDOUT))
        time.sleep(0.5)                                  # stagger the keyframes a little

    if observe:
        seen.update(live_missions(r))
        drones = [(d, v["sid"]) for d, v in sorted(seen.items())]
        print(f"\nwatching the drones streaming now ({len(drones)} at the start) for {a.seconds} s — sampling every 5 s")
    else:
        print(f"\n{a.drones} drone(s) × {a.size} @ {clip_info['fps']:g} fps, {clip_info['kbps']} kbps, "
              f"for {loops * clip_s:.0f} s — sampling every 5 s")
    # More than 6 drones (R9), or drones that come and go (observe): one summary line per sample
    compact = observe or len(drones) > 6
    if compact:
        # rx = video frames ingest received (works with every service off); pub = frames the
        # decoder published for analytics (0 when no service is selected, R6.2)
        print("  t    recv  slots  rx fps min/med  pub fps min/med  an fps min/med  worst behind   most backlog (frames)   GPU")
    else:
        print("  t    " + "  ".join(f"{d[-2:]}: pub/ an  behind" for d, _ in drones) + "   most backlog (frames)   GPU")
    worst_behind = collections.defaultdict(float)
    fps_samples = collections.defaultdict(list)
    an_samples = collections.defaultdict(list)
    max_shed: dict = {}
    svc_fps: dict = {}          # service → drone → analysed fps samples while receiving
    rx_prev: dict = {}          # drone → (last_seq_rx, time) at the previous sample
    rx_samples = collections.defaultdict(list)
    live_counts = []
    relay_last: dict = {}       # drone → the mission's live relay counters at the last sample (R9)
    svc_stages: dict = {}       # service → last stage ms (EMA) of any drone
    worst_backlog: dict = {}
    gpu_busy, gpu_mem, gpu_total = [], [], 0.0     # per sample
    vram_peak: dict = {}        # program → peak VRAM MiB
    cpu_samples = []
    e2e_samples: dict = {}      # service → [p95 ms of a drone at a sample]
    shed_last: dict = {}        # service → drone → frames shed so far
    model_ms: dict = {}         # service → [model ms of a drone at a sample]
    total_ms: dict = {}         # service → [whole per-frame ms of a drone at a sample]
    bytes_first: dict = {}      # drone → (bytes received, time) at its first sample
    bytes_last: dict = {}
    quota = cpu_quota()
    print(f"CPU limit of this pod: {quota:.0f} cores" if quota else "CPU: no pod limit found")
    t0 = time.time()
    cpu_prev, cpu_t = cpu_counters(), time.time()
    worst_cpu = 0.0
    worst_lag = 0
    while (time.time() - t0 < a.seconds) if observe else any(p.poll() is None for p in procs):
        time.sleep(5)
        if observe:
            now_live = live_missions(r)
            seen.update(now_live)
            live_counts.append(len(now_live))
            drones = [(d, v["sid"]) for d, v in sorted(seen.items())]
        cpu_now, now = cpu_counters(), time.time()
        cores = (cpu_now[0] - cpu_prev[0]) / max(0.1, now - cpu_t)
        thr = (cpu_now[1] - cpu_prev[1]) / max(0.1, now - cpu_t)
        cpu_prev, cpu_t = cpu_now, now
        worst_cpu = max(worst_cpu, cores)
        cpu_samples.append(cores)
        g = gpu_sample()
        if g:
            gpu_busy.append(g[0])
            gpu_mem.append(g[1])
            gpu_total = g[2]
        for name, mib in gpu_apps().items():
            vram_peak[name] = max(vram_peak.get(name, 0.0), mib)
        cpu_txt = f"CPU {cores:4.1f}" + (f"/{quota:.0f}" if quota else "") + (f" thr {thr:.0%}" if thr > 0.005 else "")
        # R9: ingest's event loop carries every feed; a stall there stops them all at once
        try:
            lag = json.loads(s(r.get("ingest:loop_lag")) or "{}").get("max_ms")
        except ValueError:
            lag = None
        if lag is not None:
            worst_lag = max(worst_lag, int(lag))
            if lag >= 200:
                cpu_txt += f"  ingest stall {lag / 1000:.1f}s"
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
            if any(k.startswith(("relay_", "sub_")) for k in h):
                relay_last[drone] = {k: int(float(v)) for k, v in h.items() if k.startswith(("relay_", "sub_"))}
            seq_rx, t_rx = float(h.get("last_seq_rx") or 0), time.time()
            if drone in rx_prev and h.get("status") == "receiving":
                rx = max(0.0, (seq_rx - rx_prev[drone][0]) / max(0.1, t_rx - rx_prev[drone][1]))
                rxs.append(rx)
                rx_samples[drone].append(rx)
            rx_prev[drone] = (seq_rx, t_rx)
            an = f"{slow[0]:4.1f}" if slow else "   -"
            mark = f"L{slow[2]}" if slow and slow[2] else "  "
            if h.get("bytes"):
                b = (float(h["bytes"]), time.time())
                bytes_first.setdefault(drone, b)
                bytes_last[drone] = b
            if h.get("status") == "receiving":
                for svc, v in stats.items():
                    if drone in v:
                        svc_fps.setdefault(svc, {}).setdefault(drone, []).append(float(v[drone].get("fps") or 0))
                        dv = v[drone]
                        if dv.get("e2e_p95_ms") is not None:
                            e2e_samples.setdefault(svc, []).append(float(dv["e2e_p95_ms"]))
                        shed_last.setdefault(svc, {})[drone] = int(dv.get("frames_shed") or 0)
                        st_ms = dv.get("stages") or {}
                        if st_ms.get("model") is not None:
                            model_ms.setdefault(svc, []).append(float(st_ms["model"]))
                        if st_ms.get("total") is not None:
                            total_ms.setdefault(svc, []).append(float(st_ms["total"]))
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

    ru1, t_send1 = resource.getrusage(resource.RUSAGE_CHILDREN), time.time()
    sender_cores = ((ru1.ru_utime + ru1.ru_stime) - (ru0.ru_utime + ru0.ru_stime)) / max(1.0, t_send1 - t_send0)
    if not observe:
        print("\n== waiting for analytics to finish the backlog …")
    deadline = time.time() + (0 if observe else 300)
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
    print("\nlive relay into go2rtc (restarts / keyframe skips / KB dropped; sub = grid 720p sub-stream):")
    for drone, _ in drones:
        c = relay_last.get(drone)
        if not c:
            print(f"  {drone:<12} -")
            continue
        sub = (f"   sub {c.get('sub_restarts', 0)} / {c.get('sub_skips', 0)} / {c.get('sub_dropped_kb', 0)}"
               if "sub_restarts" in c else "   sub not running")
        print(f"  {drone:<12} main {c.get('relay_restarts', 0)} / {c.get('relay_skips', 0)} / "
              f"{c.get('relay_dropped_kb', 0)}{sub}")
    print(f"\ningest event loop: longest stall {worst_lag / 1000:.2f} s (every feed waits while it is blocked)")
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

    def pct(v, q):
        v = sorted(v)
        return v[min(len(v) - 1, int(len(v) * q))] if v else None

    mbps = {d: round((bytes_last[d][0] - bytes_first[d][0]) * 8 / 1e6 / max(1.0, bytes_last[d][1] - bytes_first[d][1]), 2)
            for d in bytes_last if d in bytes_first and bytes_last[d][1] > bytes_first[d][1]}
    services = {}
    for svc in sorted(svc_fps):
        per = [med(v) for v in svc_fps[svc].values()]
        services[svc] = {
            "mode": "newest" if svc in NEWEST else "every",
            "fps_per_drone": {d: round(med(v), 1) for d, v in svc_fps[svc].items()},
            "fps_median": round(med(per), 1),
            "shed_level_max": max_shed.get(svc, 0),
            "frames_shed": sum((shed_last.get(svc) or {}).values()),
            "e2e_p95_ms": pct(e2e_samples.get(svc, []), 0.5),     # median of the drones' p95s
            "model_ms": pct(model_ms.get(svc, []), 0.5),
            "total_ms": pct(total_ms.get(svc, []), 0.5),
            "stages": svc_stages.get(svc) or {},
        }
    return {
        "drones": (max(live_counts) if live_counts else 0) if observe else a.drones,
        "observed": observe,
        "video_per_drone": {d: f"{v['width']}x{v['height']} @ {v['fps']:g} fps" for d, v in sorted(seen.items())},
        "size": a.size, "fps_source": clip_info["fps"], "kbps": clip_info["kbps"],
        "seconds": round(loops * clip_s), "file": getattr(a, "file", "") or "synthetic",
        "received_fps": {d: round(med(v), 1) for d, v in rx_samples.items()},
        "mbps_per_drone": mbps,
        "gpu": {"busy_mean": round(sum(gpu_busy) / len(gpu_busy), 1) if gpu_busy else None,
                "busy_p95": pct(gpu_busy, 0.95), "vram_peak_mib": max(gpu_mem) if gpu_mem else None,
                "vram_total_mib": gpu_total or None, "vram_per_program_peak_mib": vram_peak},
        "cpu": {"cores_mean": round(sum(cpu_samples) / len(cpu_samples), 1) if cpu_samples else None,
                "cores_peak": round(worst_cpu, 1), "quota": quota,
                "senders_cores": round(sender_cores, 2)},
        "ingest_stall_max_s": round(worst_lag / 1000, 2),
        "services": services,
        "failed_senders": failed,
        "clip": clip_info,
        "logs": tmp,
    }


if __name__ == "__main__":
    sys.exit(main())
