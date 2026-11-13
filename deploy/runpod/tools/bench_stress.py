#!/usr/bin/env python3
"""
GPU / CPU stress test for capacity planning, measured on REAL streams — run INSIDE the pod while
the edge team streams real drone video into ingest. It starts no senders of its own.

    set -a; . /workspace/dvas/secrets/dvas.env; set +a          # ANALYTICS_SERVICE_KEY
    /opt/venvs/ai/bin/python /opt/dvas/deploy/tools/bench_stress.py

Guided phases (it asks you to press Enter before each and shows how many drones are live):
1. Baseline — nothing streaming, every engine on (models loaded): CPU, VRAM, GPU busy.
2. Each engine alone — the edge streams ONE drone; the other engines are stopped for
   --solo-seconds each: ms per frame (batch 1), fps, delay capture → result, GPU busy, VRAM.
   Anomaly, intrusion, ANPR and crowd first.
3. All engines — the edge streams ALL its drones for --seconds: per engine and drone the fps
   analysed, delay capture → result (p95), frames shed; GPU busy, peak VRAM; CPU cores;
   bandwidth; each drone's resolution (the 4K / 1080p mix).
4. The capacity calculations, printed and written with the raw figures to
   /workspace/dvas/bench/stress-<time>.md and .json (the .json after every phase).

Engines are switched through each analytics service's own /stream/stop and /stream/start
(X-Internal-Service-Key from dvas.env) — the calls the Detection Models page makes — and are put
back as they were at the end, also after Ctrl-C or an error. The delay uses the edge's capture
time, so the edge laptop's clock must be NTP-synchronised (edge requirement S9).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import time

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_capacity as cap  # noqa: E402

# Analytics services and their ports (deploy/runpod/etc/supervisord.conf). Face runs inside
# person, ANPR (plates) in anpr. Priority order: the engines not measured before come first.
ENGINES = {"anomaly": 8107, "intrusion": 8105, "anpr": 8104, "crowd": 8101,
           "person": 8102, "vehicle": 8103, "vessel": 8106}
TARGET_FPS = float(os.getenv("DVAS_ANALYTICS_FPS", "20") or 20)
FLEET = 20                         # drones the plan has to serve
LOAD_TARGET = 70.0                 # % GPU busy per GPU, for the sizing


# ── engines ───────────────────────────────────────────────────────────────────
def _key() -> str:
    k = os.getenv("ANALYTICS_SERVICE_KEY", "")
    if not k:
        sys.exit("ANALYTICS_SERVICE_KEY is not set: run  set -a; . /workspace/dvas/secrets/dvas.env; set +a  first")
    return k


def engine_running(name: str) -> bool:
    try:
        r = httpx.get(f"http://127.0.0.1:{ENGINES[name]}/health", timeout=10)
        return bool(r.json().get("stream_running"))
    except Exception:
        return False


def set_engine(name: str, on: bool) -> None:
    path = "start" if on else "stop"
    try:
        r = httpx.get(f"http://127.0.0.1:{ENGINES[name]}/stream/{path}", timeout=20,
                      headers={"X-Internal-Service-Key": _key()})
        if r.status_code >= 400:
            print(f"  ! {name} /stream/{path}: HTTP {r.status_code} {r.text[:120]}")
    except Exception as exc:
        print(f"  ! {name} /stream/{path}: {exc}")


def only(engines) -> None:
    for name in ENGINES:
        set_engine(name, name in engines)
    time.sleep(5)                  # stream loops stop / start


# ── measuring ────────────────────────────────────────────────────────────────
def idle_baseline(seconds: int = 15) -> dict:
    cpu0, t0 = cap.cpu_counters(), time.time()
    busy, mem = [], []
    while time.time() - t0 < seconds:
        g = cap.gpu_sample()
        if g:
            busy.append(g[0])
            mem.append(g[1])
        time.sleep(1)
    cpu1, t1 = cap.cpu_counters(), time.time()
    return {"cpu_cores": round((cpu1[0] - cpu0[0]) / (t1 - t0), 1),
            "gpu_busy": round(sum(busy) / len(busy), 1) if busy else None,
            "vram_mib": max(mem) if mem else None, "vram_per_program_mib": cap.gpu_apps()}


def observe_args(a, seconds: int):
    ns = cap.parser().parse_args([])
    ns.observe, ns.seconds, ns.redis = True, seconds, a.redis
    return ns


def wait_for(a, r, want: str, ok) -> int:
    """Ask the operator to arrange the streams, then wait until ok(live drone count)."""
    print(f"\n>>> {want}")
    if not a.yes:
        input("    press Enter when ready … ")
    shown = None
    while True:
        n = len(cap.live_missions(r))
        if ok(n):
            print(f"    {n} drone(s) live — measuring")
            return n
        if n != shown:
            print(f"    {n} drone(s) live — waiting …")
            shown = n
        time.sleep(3)


def solo_row(name: str, res: dict) -> dict:
    svc = (res.get("services") or {}).get(name) or {}
    return {"engine": name, "ms_per_frame": svc.get("total_ms"), "model_ms": svc.get("model_ms"),
            "fps": svc.get("fps_median"), "e2e_p95_ms": svc.get("e2e_p95_ms"),
            "gpu_busy": res["gpu"]["busy_mean"], "vram_peak_mib": res["gpu"]["vram_peak_mib"],
            "stages": svc.get("stages")}


# ── report ───────────────────────────────────────────────────────────────────
def calculations(base: dict, solos: list, full: dict) -> dict:
    n = full["drones"]
    busy = full["gpu"]["busy_mean"] or 0.0
    gpu_per_drone = max(0.0, busy - (base.get("gpu_busy") or 0.0)) / n
    per_gpu = (LOAD_TARGET / gpu_per_drone) if gpu_per_drone > 0 else None
    cores = (full["cpu"]["cores_mean"] or 0.0) - full["cpu"].get("senders_cores", 0.0)
    cores_per_drone = max(0.0, cores - (base.get("cpu_cores") or 0.0)) / n
    vram_per_drone = max(0.0, (full["gpu"]["vram_peak_mib"] or 0) - (base.get("vram_mib") or 0)) / n
    mbps = list((full.get("mbps_per_drone") or {}).values())
    mbps_drone = sum(mbps) / len(mbps) if mbps else full["kbps"] / 1000
    load = {}
    for row in solos:
        if row["ms_per_frame"] is None:
            continue
        svc = (full.get("services") or {}).get(row["engine"]) or {}
        fps = TARGET_FPS if svc.get("mode", "every") == "every" else (svc.get("fps_median") or row["fps"] or 1.0)
        load[row["engine"]] = {"fps_used": fps,
                               f"load_{n}_drones": round(row["ms_per_frame"] * fps * n / 1000, 2),
                               f"load_{FLEET}_drones": round(row["ms_per_frame"] * fps * FLEET / 1000, 2)}
    return {
        "gpu_pct_per_drone": round(gpu_per_drone, 2),
        "drones_per_gpu_at_70pct": round(per_gpu, 1) if per_gpu else None,
        "gpus_for_20_drones": math.ceil(FLEET / per_gpu) if per_gpu else None,
        "cpu_cores_per_drone": round(cores_per_drone, 2),
        "cpu_cores_per_pod": full["cpu"]["quota"],
        "drones_per_pod_by_cpu": math.floor((full["cpu"]["quota"] - (base.get("cpu_cores") or 0)) / cores_per_drone)
        if cores_per_drone > 0 and full["cpu"]["quota"] else None,
        "vram_mib_per_drone": round(vram_per_drone),
        "mbps_per_drone": round(mbps_drone, 2),
        "bandwidth_mbps_run": round(mbps_drone * n, 1),
        "bandwidth_mbps_20_drones": round(mbps_drone * FLEET, 1),
        "load_per_engine": load,
    }


def report(a, base, solos, full, calc) -> str:
    L = []
    w = L.append
    w(f"# DVAS stress test — {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    w("")
    w("Real streams from the edge (no test senders on the pod).")
    w(f"All-engines run: {full['drones']} drone(s) live (most at once), {a.seconds} s. Engine-alone runs: "
      f"1 drone, {a.solo_seconds} s each.")
    mix = {}
    for v in (full.get("video_per_drone") or {}).values():
        mix[v] = mix.get(v, 0) + 1
    w("Video per drone (as ingest received it): " + ", ".join(f"{n} × {v}" for v, n in sorted(mix.items())))
    w(f"Pod: {full['cpu']['quota']:.0f} vCPU (cgroup quota), GPU VRAM {full['gpu']['vram_total_mib']} MiB.")
    w("")
    w("## 2. Recorded")
    w("")
    w("| | Idle (engines loaded, no drone) | Full run |")
    w("|---|---|---|")
    w(f"| GPU busy % (sm), mean | {base['gpu_busy']} | {full['gpu']['busy_mean']} (p95 {full['gpu']['busy_p95']}) |")
    w(f"| VRAM, peak MiB | {base['vram_mib']} | {full['gpu']['vram_peak_mib']} |")
    w(f"| CPU cores used, mean (of {full['cpu']['quota']:.0f}) | {base['cpu_cores']} | {full['cpu']['cores_mean']} "
      f"(peak {full['cpu']['cores_peak']}) |")
    w(f"| Ingest event-loop stall, max | | {full['ingest_stall_max_s']} s |")
    w("")
    w("Per engine, full run (fps analysed per drone; delay = capture → result, p95):")
    w("")
    w("| Engine | Mode | fps per drone | Delay p95 ms | Frames shed | Highest shed level | ms per frame (in run) |")
    w("|---|---|---|---|---|---|---|")
    for name, s in sorted((full.get("services") or {}).items()):
        fps = " / ".join(f"{v:g}" for v in s["fps_per_drone"].values())
        w(f"| {name} | {s['mode']} | {fps} | {s['e2e_p95_ms']} | {s['frames_shed']} | {s['shed_level_max']} | {s['total_ms']} |")
    w("")
    w("Each engine alone, 1 drone (batch 1):")
    w("")
    w("| Engine | ms per frame | model ms | fps | Delay p95 ms | GPU busy % | VRAM peak MiB |")
    w("|---|---|---|---|---|---|---|")
    for row in solos:
        w(f"| {row['engine']} | {row['ms_per_frame']} | {row['model_ms']} | {row['fps']} | {row['e2e_p95_ms']} | "
          f"{row['gpu_busy']} | {row['vram_peak_mib']} |")
    w("")
    w("VRAM per program at the full run's peak (MiB): " + ", ".join(
        f"{k} {v:.0f}" for k, v in sorted((full['gpu']['vram_per_program_peak_mib'] or {}).items())))
    w("")
    w("## 3. Calculations")
    w("")
    w(f"- GPU % per drone = (GPU busy − idle) / drones = ({full['gpu']['busy_mean']} − {base['gpu_busy']}) / "
      f"{full['drones']} = **{calc['gpu_pct_per_drone']}**")
    w(f"- Drones per GPU at 70 % load = 70 / GPU % per drone = **{calc['drones_per_gpu_at_70pct']}**")
    w(f"- GPUs needed for 20 drones = 20 / drones per GPU, rounded up = **{calc['gpus_for_20_drones']}**")
    w(f"- CPU cores per drone = (cores used − idle) / drones = **{calc['cpu_cores_per_drone']}**; "
      f"cores per pod = {calc['cpu_cores_per_pod']:.0f} → about **{calc['drones_per_pod_by_cpu']}** drones per pod by CPU")
    w(f"- VRAM per drone = (peak − idle) / drones = **{calc['vram_mib_per_drone']} MiB**")
    w(f"- Bandwidth = drones × Mbps per drone ({calc['mbps_per_drone']}) = **{calc['bandwidth_mbps_run']} Mbps** "
      f"this run, **{calc['bandwidth_mbps_20_drones']} Mbps** for 20 drones")
    w(f"- Load per engine = ms per frame (alone) × fps × drones / 1000 (fps = {TARGET_FPS:g} for every-frame "
      "engines, the measured rate for newest-frame ones; > 1.0 means one worker cannot keep up):")
    w("")
    w(f"| Engine | fps used | load, {full['drones']} drones | load, 20 drones |")
    w("|---|---|---|---|")
    n = full["drones"]
    for name, l in calc["load_per_engine"].items():
        w(f"| {name} | {l['fps_used']:g} | {l[f'load_{n}_drones']} | {l[f'load_{FLEET}_drones']} |")
    w("")
    w("Notes: GPU busy % is nvidia-smi utilization.gpu (time a kernel ran — the dmon `sm` view); "
      "the delay uses the edge's capture time, so it is only right when the edge laptop's clock is "
      "NTP-synchronised. Engines in 'newest' mode analyse only the newest frame by design.")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=int, default=300, help="all-engines run")
    ap.add_argument("--solo-seconds", type=int, default=60, help="each engine-alone run")
    ap.add_argument("--engines", default=",".join(ENGINES), help="engines for the alone runs, in order")
    ap.add_argument("--skip-baseline", action="store_true")
    ap.add_argument("--skip-solo", action="store_true")
    ap.add_argument("--yes", action="store_true", help="do not ask; wait for the right number of live drones")
    ap.add_argument("--redis", default=os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"))
    a = ap.parse_args()
    _key()
    r = cap.redis.Redis.from_url(a.redis)
    engines = [e.strip() for e in a.engines.split(",") if e.strip() in ENGINES]
    out_dir = "/workspace/dvas/bench"
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    json_path = os.path.join(out_dir, f"stress-{stamp}.json")
    data: dict = {"baseline": {}, "solo": [], "full": None}

    def save():
        with open(json_path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    was = {name: engine_running(name) for name in ENGINES}
    print("engines now: " + ", ".join(f"{n} {'on' if v else 'off'}" for n, v in was.items()))
    try:
        if not a.skip_baseline:
            wait_for(a, r, "1. BASELINE — ask the edge team to stream NOTHING", lambda n: n == 0)
            only(ENGINES)
            data["baseline"] = idle_baseline()
            print(f"    {data['baseline']}")
            save()

        if not a.skip_solo:
            wait_for(a, r, "2. EACH ENGINE ALONE — ask the edge team to stream exactly ONE drone",
                     lambda n: n == 1)
            for name in engines:
                print(f"\n== {name} alone, 1 drone, {a.solo_seconds} s")
                only([name])
                time.sleep(10)                     # its first rounds / warm-up out of the figures
                res = cap.run(observe_args(a, a.solo_seconds))
                data["solo"].append(solo_row(name, res))
                print(f"    → {data['solo'][-1]}")
                save()

        wait_for(a, r, "3. ALL ENGINES — ask the edge team to stream ALL their drones", lambda n: n >= 1)
        only(ENGINES)
        time.sleep(10)
        data["full"] = cap.run(observe_args(a, a.seconds))
        save()

        calc = calculations(data["baseline"] or {"cpu_cores": 0, "gpu_busy": 0, "vram_mib": 0},
                            data["solo"], data["full"])
        data["calculations"] = calc
        save()
        md = report(a, data["baseline"] or {"cpu_cores": None, "gpu_busy": None, "vram_mib": None},
                    data["solo"], data["full"], calc)
        with open(os.path.join(out_dir, f"stress-{stamp}.md"), "w") as f:
            f.write(md)
        print("\n" + md)
        print(f"written: {out_dir}/stress-{stamp}.md and .json")
    finally:
        print("\nputting the engines back as they were …")
        for name, on in was.items():
            set_engine(name, on)
    return 0


if __name__ == "__main__":
    sys.exit(main())
