#!/usr/bin/env python3
"""
v2 R0 pod check — run INSIDE the pod after setting a v2 image (no change to the pod):

    /opt/venvs/ai/bin/python /opt/dvas/deploy/tools/v2_pod_check.py
    /opt/venvs/ai/bin/python /opt/dvas/deploy/tools/v2_pod_check.py --e2e --token "$TOKEN"   # + uplink test

Answers the v2 plan's R0 questions with numbers:
  1. go2rtc running (version, slots, API locked to /api/ws)
  2. decode: NVDEC usable? CPU H.264 decode speed at 1080p / 4K (PyAV, threads)
  3. JPEG encode for analytics frames: cv2 vs nvJPEG at 1920 / 3840 px
  4. /dev/shm size, recording volume, disk guard
  5. --e2e: streams a synthetic 4K clip through /ingest/video/ws on this pod (needs an
     operator token) and reports what the decoder fed to the analytics stream
Paste the whole output into the chat / the v2 plan R0 section.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

OK, WARN, BAD = "OK  ", "WARN", "FAIL"


def sh(cmd: str, timeout: int = 60) -> str:
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception as exc:
        return f"<{exc}>"


def line(tag: str, what: str, detail: str = "") -> None:
    print(f"[{tag}] {what}" + (f" — {detail}" if detail else ""))


def check_go2rtc() -> None:
    print("\n== 1. go2rtc")
    ver = sh("/usr/local/bin/go2rtc -version 2>&1 | head -1")
    line(OK if "go2rtc" in ver.lower() or ver[:1] == "1" else BAD, "binary", ver or "missing")
    state = sh("supervisorctl -c /etc/dvas/supervisord.conf status go2rtc video-decoder")
    line(OK if state.count("RUNNING") == 2 else BAD, "programs", state.replace("\n", " | "))
    for path, want in (("/api/streams", 403), ("/api/config", 403)):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:1984{path}", timeout=3)
            code = 200
        except urllib.error.HTTPError as e:
            code = e.code
        except Exception as e:
            code = str(e)
        line(OK if code in (401, 403, 404) else BAD, f"API {path} locked", f"HTTP {code}")
    print("      live slots in use:", sh("valkey-cli -h 127.0.0.1 --scan --pattern 'live:slot:*' | wc -l"))


def make_clip(path: str, size: str, secs: int = 5) -> bool:
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30",
                        "-t", str(secs), "-c:v", "libx264", "-preset", "veryfast", "-g", "30", "-bf", "0",
                        "-pix_fmt", "yuv420p", "-f", "mpegts", path], capture_output=True, text=True)
    return r.returncode == 0


def check_decode(tmp: str) -> None:
    print("\n== 2. H.264 decode (video-decoder)")
    print("      NVIDIA_DRIVER_CAPABILITIES =", os.getenv("NVIDIA_DRIVER_CAPABILITIES", "<unset>"))
    cuvid = sh("ls /usr/lib/x86_64-linux-gnu/libnvcuvid.so* 2>/dev/null | head -1")
    line(OK if cuvid else WARN, "libnvcuvid (NVDEC)", cuvid or "not mounted: CPU decode only (fine)")
    print("      ffmpeg hwaccels:", sh("ffmpeg -hide_banner -hwaccels | tail -n +2 | tr '\\n' ' '"))
    import av  # noqa: PLC0415
    for size in ("1920x1080", "3840x2160"):
        clip = os.path.join(tmp, f"c_{size}.ts")
        if not make_clip(clip, size):
            line(BAD, f"test clip {size}", "ffmpeg libx264 failed")
            continue
        with av.open(clip) as c:
            s = c.streams.video[0]
            s.thread_type = "AUTO"
            t0, n = time.perf_counter(), 0
            for f in c.decode(s):
                f.to_ndarray(format="bgr24")
                n += 1
            dt = time.perf_counter() - t0
        fps = n / dt
        line(OK if fps >= 30 else WARN, f"CPU decode {size} (+BGR)", f"{fps:.0f} fps ({1000 / fps:.1f} ms/frame)")


def check_jpeg() -> None:
    print("\n== 3. JPEG encode of analytics frames (q90)")
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    rng = np.random.default_rng(0)
    for w, h in ((1920, 1080), (3840, 2160)):
        img = cv2.GaussianBlur(rng.integers(0, 255, (h, w, 3), dtype=np.uint8), (9, 9), 3)
        t0 = time.perf_counter()
        for _ in range(10):
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        ms = (time.perf_counter() - t0) * 100
        line(OK if ms < 25 else WARN, f"cv2 {w}x{h}", f"{ms:.1f} ms, {len(buf) / 1e6:.2f} MB")
        try:
            import torch  # noqa: PLC0415
            from torchvision.io import encode_jpeg  # noqa: PLC0415
            t = torch.from_numpy(img[:, :, ::-1].copy()).permute(2, 0, 1).contiguous().cuda()
            encode_jpeg(t, quality=90)
            torch.cuda.synchronize()
            t0 = time.perf_counter()
            for _ in range(10):
                encode_jpeg(t, quality=90)
            torch.cuda.synchronize()
            line(OK, f"nvJPEG {w}x{h}", f"{(time.perf_counter() - t0) * 100:.1f} ms (+ upload)")
        except Exception as exc:
            line(WARN, f"nvJPEG {w}x{h}", f"not usable: {str(exc)[:80]}")


def check_disk() -> None:
    print("\n== 4. Memory / disk")
    print("      /dev/shm:", sh("df -h /dev/shm | tail -1"))
    rec = os.getenv("DVAS_RECORD_DIR", "/workspace/dvas/recordings")
    print("      recordings:", rec, sh(f"du -sh {rec} 2>/dev/null | cut -f1") or "(empty)")
    guard = "/run/dvas/disk_guard.json"
    if os.path.exists(guard):
        line(OK, "disk guard", open(guard).read().strip())
    else:
        line(WARN, "disk guard", f"{guard} missing: set DVAS_VOLUME_GB in the template (the backup loop writes it)")
    print("      DVAS_VOLUME_GB =", os.getenv("DVAS_VOLUME_GB", "<unset>"))


def e2e(tmp: str, token: str, url: str) -> None:
    print("\n== 5. End-to-end uplink (synthetic 4K, 20 s)")
    clip = os.path.join(tmp, "e2e.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=3840x2160:rate=30",
                    "-t", "20", "-c:v", "libx264", "-preset", "veryfast", "-g", "30", "-bf", "0",
                    "-pix_fmt", "yuv420p", clip], check=True)
    mission = f"MSN-V2CHECK-{int(time.time())}"
    tool = os.path.join(os.path.dirname(os.path.abspath(__file__)), "send_ts.py")
    t0 = time.time()
    r = subprocess.run([sys.executable, tool, "--url", url, "--token", token, "--drone", "DRN-V2CHECK",
                        "--mission", mission, "--file", clip], capture_output=True, text=True, timeout=300)
    print("      sender:", "\n      ".join((r.stdout + r.stderr).strip().splitlines()[-8:]))
    sid = mission
    for _ in range(60):
        done = sh(f"valkey-cli -h 127.0.0.1 HGET mission:video:{sid} decoder_done")
        if done == "1":
            break
        time.sleep(1)
    h = sh(f"valkey-cli -h 127.0.0.1 HGETALL mission:video:{sid} | paste - - | tr '\\t' '=' | tr '\\n' ' '")
    print("      video state:", h)
    print(f"      wall time {time.time() - t0:.0f} s for 20 s of 4K; expect ≈ 400 analytics frames (20 fps)")
    print("      recording:", sh(f"ls -1 {os.getenv('DVAS_RECORD_DIR', '/workspace/dvas/recordings')}/{sid} | wc -l"), "files")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--e2e", action="store_true")
    ap.add_argument("--token", default=os.getenv("TOKEN", ""))
    ap.add_argument("--url", default="http://127.0.0.1")
    a = ap.parse_args()
    print("DVAS v2 pod check", time.strftime("%Y-%m-%d %H:%M:%S"), "GPU:",
          sh("nvidia-smi --query-gpu=name,driver_version --format=csv,noheader"))
    tmp = tempfile.mkdtemp(prefix="v2check-")
    try:
        check_go2rtc()
        check_decode(tmp)
        check_jpeg()
        check_disk()
        if a.e2e:
            if not a.token:
                print("\n--e2e needs --token (an operator's access token)")
            else:
                e2e(tmp, a.token, a.url)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nLive view check (browser): Multi-Drone Grid while a drone streams video — the tile should "
          "play smoothly; DevTools → Network → WS /live/api/ws shows binary frames.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
