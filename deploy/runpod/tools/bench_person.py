#!/usr/bin/env python3
"""
Person & face pipeline benchmark — run ON THE POD to see where per-frame time goes.

    cd /analytics && /opt/venvs/ai/bin/python /opt/dvas/deploy/tools/bench_person.py [--frames 20]

Read-only: loads its own copy of the models (≈1 GB VRAM), never touches Valkey streams,
the database or the running services. Uses the newest frame of a live drone stream when one
exists (--drone), else a synthetic 3840x2160 frame.

Prints:
  * PyTorch / CUDA / cuDNN versions and the GPU architectures PyTorch was BUILT for. If the
    GPU's own architecture (Blackwell RTX PRO 6000 = sm_120) is missing, kernels run through
    PTX JIT or slow fallbacks — "slow while the GPU is idle".
  * Per stage (GPU-synchronised): JPEG decode, person predict, tracker update, face tiles.
  * The top CPU operators of one frame (torch.profiler), to name what is not GPU work.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.getenv("DVAS_ANALYTICS_DIR", "/analytics"))


def sync():
    import torch
    if torch.cuda.is_available():
        torch.cuda.synchronize()


def timed(fn, n):
    fn()                                    # warm-up (cuDNN autotune, lazy init)
    sync()
    out = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        sync()
        out.append((time.perf_counter() - t) * 1000)
    out.sort()
    return out[len(out) // 2], out[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=20)
    ap.add_argument("--drone", default="", help="use the newest frame of this drone's stream")
    ap.add_argument("--profile", action="store_true", default=True)
    a = ap.parse_args()

    import cv2
    import numpy as np
    import torch

    print("== environment")
    print("torch", torch.__version__, "| CUDA", torch.version.cuda, "| cuDNN", torch.backends.cudnn.version())
    if not torch.cuda.is_available():
        print("CUDA NOT AVAILABLE in this process — everything below runs on the CPU")
    else:
        p = torch.cuda.get_device_properties(0)
        cc = f"sm_{p.major}{p.minor}"
        archs = torch.cuda.get_arch_list()
        print("GPU", p.name, cc, f"{p.total_memory / 1024**3:.0f} GB")
        print("PyTorch built for:", " ".join(archs))
        print("  ->", "native kernels for this GPU" if cc in archs else
              f"!! {cc} NOT in the build list: kernels come from PTX JIT / compatibility paths")
    print("threads: torch", torch.get_num_threads(), "| cv2", cv2.getNumThreads(),
          "| OMP_NUM_THREADS", os.getenv("OMP_NUM_THREADS"), "| DVAS_CPUS", os.getenv("DVAS_CPUS"))
    print("cudnn.benchmark", torch.backends.cudnn.benchmark, "| DVAS_FP16", os.getenv("DVAS_FP16", "auto"),
          "| CUDA_LAUNCH_BLOCKING", os.getenv("CUDA_LAUNCH_BLOCKING"))

    # GPU sanity: a large FP16 matmul should take well under 5 ms on any data-centre GPU
    if torch.cuda.is_available():
        x = torch.randn(8192, 8192, device="cuda", dtype=torch.float16)
        med, _ = timed(lambda: x @ x, 5)
        print(f"FP16 8192² matmul: {med:.1f} ms  (~{2 * 8192**3 / med / 1e9:.1f} TFLOP/s)")

    from shared import gpu_profile
    gpu_profile.apply_torch_settings()
    from person import analyzer as pa

    jpeg = None
    if a.drone:
        import redis
        r = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"))
        # The drone's active mission stream, else its legacy per-drone stream
        sid = r.get(f"drone:active_mission:{a.drone}")
        key = f"mission:frames:{sid.decode()}" if sid else f"drone:frames:{a.drone}"
        last = r.xrevrange(key, count=1) or r.xrevrange(f"drone:frames:{a.drone}", count=1)
        if last:
            jpeg = last[0][1].get(b"frame")
            print(f"using the newest frame of {a.drone} ({len(jpeg) // 1024} KB)")
    if jpeg is None:
        img = np.random.randint(0, 255, (2160, 3840, 3), np.uint8)
        img = cv2.GaussianBlur(img, (9, 9), 0)
        jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()
        print("using a synthetic 3840x2160 frame")

    print("\n== stages (median / worst of", a.frames, "frames, ms)")
    frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    print("frame", frame.shape[1], "x", frame.shape[0])
    print("decode (CPU)          %8.1f / %.1f" % timed(lambda: cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR), a.frames))

    an = pa.PersonFaceAnalyzer(os.getenv("PERSON_MODEL", "/analytics/person/models/yolov8m.pt"),
                               os.getenv("FACE_MODEL", "/analytics/person/models/yolov8n-face.pt"))
    print(f"person imgsz {an.imgsz}, face tiles {pa.FACE_TILE}px x {len(pa.face_tiles(*frame.shape[:2]))}")
    kw = an._track_kwargs()
    print("person predict        %8.1f / %.1f" % timed(lambda: an.tracker.predict_batch({"bench": frame}, **kw), a.frames))
    print("person predict+track  %8.1f / %.1f" % timed(lambda: an.tracker.track_batch({"bench": frame}, **kw), a.frames))
    print("face tiles (batched)  %8.1f / %.1f" % timed(lambda: pa.detect_faces_on_tiles(an.face_model, frame, device=an.device), a.frames))
    print("full analyze_batch    %8.1f / %.1f" % timed(lambda: an.analyze_batch({"bench": frame}, save_crops=True), a.frames))
    print("device after run:", an.device_info())

    # C3: FP16 vs FP32 face recall on this GPU (plan Phase 1 item 7)
    if torch.cuda.is_available():
        from shared import gpu_profile as gp
        def faces(half):
            orig = gp.predict_kwargs
            gp.predict_kwargs = lambda device=None: {**orig(device), "half": half}
            try:
                an.face_model.predictor = None          # rebuild for this precision
                return pa.detect_faces_on_tiles(an.face_model, frame, device=an.device)
            finally:
                gp.predict_kwargs = orig
                an.face_model.predictor = None
        f32, f16 = faces(False), faces(True)
        def matched(a, b):
            n = 0
            for x in a:
                for y in b:
                    ix = max(0, min(x[2], y[2]) - max(x[0], y[0])); iy = max(0, min(x[3], y[3]) - max(x[1], y[1]))
                    i = ix * iy
                    if i / ((x[2]-x[0])*(x[3]-x[1]) + (y[2]-y[0])*(y[3]-y[1]) - i + 1e-6) > 0.5:
                        n += 1
                        break
            return n
        print(f"\n== FP16 face check: FP32 found {len(f32)}, FP16 found {len(f16)}, "
              f"FP32 faces also found in FP16: {matched(f32, f16)}/{len(f32)}")
        if f32 and matched(f32, f16) < len(f32):
            print("   !! FP16 misses faces here — set DVAS_FP16=false for the person service")

    if a.profile and torch.cuda.is_available():
        from torch.profiler import ProfilerActivity, profile
        with profile(activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA]) as prof:
            an.analyze_batch({"bench": frame}, save_crops=True)
            sync()
        print("\n== top operators of one frame (by CPU time)")
        print(prof.key_averages().table(sort_by="cpu_time_total", row_limit=15))
        print("== top operators (by CUDA time)")
        print(prof.key_averages().table(sort_by="cuda_time_total", row_limit=10))


if __name__ == "__main__":
    main()
