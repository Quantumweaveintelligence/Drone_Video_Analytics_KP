# -*- coding: utf-8 -*-
"""
Live relay of a mission's TS stream to go2rtc (RunPod v2 plan R1 item 2).

One ffmpeg per mission copies the stream (no re-encode) into go2rtc's RTSP listener on
127.0.0.1; browsers watch it through nginx /live/api/ws (MSE). The relay is for live
viewing only: when ffmpeg is slow or gone, bytes are dropped up to the next keyframe and
the process is restarted. The recording and the analytics never depend on it.

Grid sub-stream (plan R2 hardening, DVAS_LIVE_SUBSTREAM=1): while a dashboard grid tile asks
for it (Valkey key live:sub_want:<slot>, refreshed by the viewer), a second ffmpeg re-encodes
the stream to 720p ~1.5 Mbps into go2rtc slot <slot>_sub — h264_nvenc when the pod exposes
NVENC, else libx264 ultrafast. Nobody watching a grid tile → no transcode.
"""
from __future__ import annotations

import asyncio
import collections
import logging
import os
import time
from typing import Awaitable, Callable, Optional

log = logging.getLogger(__name__)

ENABLED = os.getenv("DVAS_VIDEO_RELAY", "1").lower() in ("1", "true", "yes")
RTSP_BASE = os.getenv("DVAS_GO2RTC_RTSP", "rtsp://127.0.0.1:8554").rstrip("/")
FFMPEG = os.getenv("DVAS_FFMPEG", "ffmpeg")
SUBSTREAM = os.getenv("DVAS_LIVE_SUBSTREAM", "1").lower() in ("1", "true", "yes")
SUB_HEIGHT = int(os.getenv("DVAS_LIVE_SUB_HEIGHT", "720") or 720)
SUB_KBPS = int(os.getenv("DVAS_LIVE_SUB_KBPS", "1500") or 1500)
SUB_CHECK_S = 2.0                   # how often the relay re-reads whether anyone wants the sub-stream
MAX_BUFFER = 4 * 1024 * 1024        # bytes waiting for ffmpeg before the relay skips to a keyframe
RESTART_MIN_S = 2.0
SKIP_LOG_S = 10.0                   # at most one "behind, skipped to a keyframe" line per relay per 10 s
# go2rtc answers a player that connects before a stream's publisher with "mse: streams: unknown
# error" and sends nothing more. A relay counts as ready (its address goes out) once ffmpeg reports
# written frames (-progress), plus this margin. Measured in the image (13 Nov): with ffmpeg's
# default probing (5 s / 5 MB of input) a stream was playable only 4-6 s after the first bytes.
READY_MARGIN_S = float(os.getenv("DVAS_LIVE_READY_MARGIN_S", "0.3") or 0.3)
# Probe just enough input to know the stream (its first chunk starts at a keyframe with SPS/PPS)
PROBE_ARGS = ["-probesize", "2000000", "-analyzeduration", "1000000"]
# A grid tile waits this long for its sub-stream before it falls back to the source stream
# (8 tiles decoding 4K at once in one browser broke its decoder on 13 Nov)
SUB_WAIT_S = float(os.getenv("DVAS_LIVE_SUB_WAIT_S", "10") or 10)


def _encoder_args(encoder: str) -> list:
    rate = [f"-b:v", f"{SUB_KBPS}k", "-maxrate", f"{SUB_KBPS * 4 // 3}k", "-bufsize", f"{SUB_KBPS * 2 // 3}k",
            "-g", "30", "-bf", "0"]
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ll", "-rc", "cbr", *rate]
    return ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-pix_fmt", "yuv420p",
            "-threads", "2", *rate]


_sub_encoder: Optional[str] = None
# How the sub-stream decodes and scales (R9): "cuda_scale" = decode + scale on the GPU,
# "cuda_decode" = decode on the GPU, scale on the CPU, "cpu" = both on the CPU (≈ 1.5 cores per
# watched 4K tile). Chosen once by trying them in that order on a short sample.
_sub_hw_mode: Optional[str] = None


async def _try(args: list, timeout: float = 20.0) -> tuple:
    """(ok, last error lines) of one ffmpeg run."""
    try:
        p = await asyncio.create_subprocess_exec(
            FFMPEG, "-hide_banner", "-v", "error", *args,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await asyncio.wait_for(p.communicate(), timeout)
        return p.returncode == 0, " | ".join(err.decode(errors="replace").strip().splitlines()[-3:])
    except Exception as exc:
        return False, repr(exc)


def sub_size(src_w: int = 0, src_h: int = 0) -> tuple:
    """Sub-stream size: SUB_HEIGHT high, even width keeping the source's aspect (16:9 if unknown)."""
    if src_w > 0 and src_h > 0:
        return max(2, round(src_w * SUB_HEIGHT / src_h / 2) * 2), SUB_HEIGHT
    return round(SUB_HEIGHT * 16 / 9 / 2) * 2, SUB_HEIGHT


def _hw_args(mode: str, w: int, h: int) -> tuple:
    """(input options, filter) for a decode mode."""
    if mode == "cuda_scale":
        return ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"], f"scale_cuda={w}:{h}"
    if mode == "cuda_decode":
        return ["-hwaccel", "cuda"], f"scale={w}:{h}"
    return [], f"scale={w}:{h}"


async def _probe_sub_hw() -> str:
    import tempfile  # noqa: PLC0415
    with tempfile.TemporaryDirectory() as d:
        sample = os.path.join(d, "probe.ts")
        ok, err = await _try(["-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30", "-t", "1",
                              "-c:v", "libx264", "-preset", "ultrafast", "-g", "15", "-f", "mpegts", sample])
        if not ok:
            log.warning("video relay: GPU decode probe could not make its sample: %s", err)
            return "cpu"
        w, h = sub_size(1920, 1080)
        for mode in ("cuda_scale", "cuda_decode"):
            hw_in, vf = _hw_args(mode, w, h)
            ok, err = await _try([*hw_in, "-f", "mpegts", "-i", sample, "-vf", vf,
                                  *_encoder_args("h264_nvenc"), "-f", "null", "-"])
            if ok:
                return mode
            log.warning("video relay: sub-stream %s not available here: %s", mode, err or "no error output")
    return "cpu"


async def sub_encoder() -> str:
    """h264_nvenc if it encodes on this machine (the pod must expose NVENC), else libx264."""
    global _sub_encoder
    if _sub_encoder is None:
        want = os.getenv("DVAS_LIVE_SUB_ENCODER", "auto")
        _sub_encoder = want if want in ("h264_nvenc", "libx264") else "libx264"
        if want == "auto":
            try:
                p = await asyncio.create_subprocess_exec(
                    FFMPEG, "-hide_banner", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=1280x720:rate=30", "-t", "0.5", *_encoder_args("h264_nvenc"), "-f", "null", "-",
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                if await asyncio.wait_for(p.wait(), 15) == 0:
                    _sub_encoder = "h264_nvenc"
            except Exception:
                pass
        mode = await sub_hw_mode()
        log.info("video relay: grid sub-stream encoder %s, decode/scale %s (%dp, %d kbps)", _sub_encoder,
                 {"cuda_scale": "GPU decode + GPU scale", "cuda_decode": "GPU decode + CPU scale"}.get(mode, "CPU"),
                 SUB_HEIGHT, SUB_KBPS)
    return _sub_encoder


async def sub_hw_mode() -> str:
    """How the sub-stream decodes / scales (DVAS_LIVE_SUB_HWDEC=0: always on the CPU)."""
    global _sub_hw_mode
    if _sub_hw_mode is None:
        _sub_hw_mode = ("cpu" if os.getenv("DVAS_LIVE_SUB_HWDEC", "1") == "0" or _sub_encoder != "h264_nvenc"
                        else await _probe_sub_hw())
    return _sub_hw_mode


class Relay:
    def __init__(self, stream_name: str, transcode: bool = False, src_size: tuple = (0, 0)):
        self.name = stream_name
        self.transcode = transcode
        self.src_size = src_size
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._started_at = 0.0
        self._wait_key = True             # (re)start only at a keyframe
        self.dropped_bytes = 0
        self.restarts = 0
        # R9 diagnostics: why a live tile drops — ffmpeg exits (with its last error lines) and
        # skips to a keyframe because ffmpeg / go2rtc fell behind
        self.skips = 0
        self._skips_logged = 0
        self._skip_log_at = 0.0
        self._stderr: collections.deque = collections.deque(maxlen=8)
        self._stderr_task: Optional[asyncio.Task] = None
        self._fed_at: Optional[float] = None    # first bytes handed to the current ffmpeg
        self._out_at: Optional[float] = None    # first frames written to go2rtc (ffmpeg -progress)
        self._progress_task: Optional[asyncio.Task] = None

    def ready(self) -> bool:
        """ffmpeg runs and is publishing into go2rtc (it reported written frames)."""
        return (self._alive() and self._out_at is not None
                and time.monotonic() - self._out_at >= READY_MARGIN_S)

    async def _spawn(self) -> None:
        self._started_at = time.monotonic()
        self._fed_at = self._out_at = None
        hw_in, codec = [], ["-c", "copy"]
        if self.transcode:
            enc = await sub_encoder()
            hw_in, vf = _hw_args(await sub_hw_mode(), *sub_size(*self.src_size))
            codec = ["-vf", vf, *_encoder_args(enc)]
        self._proc = await asyncio.create_subprocess_exec(
            FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
            "-progress", "pipe:1", "-stats_period", "0.5",
            *hw_in, *PROBE_ARGS, "-fflags", "+genpts+nobuffer", "-f", "mpegts", "-i", "pipe:0",
            "-map", "0:v:0", *codec, "-f", "rtsp", "-rtsp_transport", "tcp",
            f"{RTSP_BASE}/{self.name}",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        self._stderr.clear()
        self._stderr_task = asyncio.create_task(self._drain_stderr(self._proc))
        self._progress_task = asyncio.create_task(self._watch_progress(self._proc))

    async def _watch_progress(self, proc) -> None:
        """ffmpeg -progress: the first frame=N > 0 means it is publishing into go2rtc."""
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    return
                if self._out_at is None and line.startswith(b"frame="):
                    try:
                        if int(line[6:].strip() or 0) > 0:
                            self._out_at = time.monotonic()
                    except ValueError:
                        pass
        except Exception:
            return

    async def _drain_stderr(self, proc) -> None:
        """Keep ffmpeg's last error lines (read continuously so its pipe never fills)."""
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    return
                self._stderr.append(line.decode(errors="replace").strip()[:200])
        except Exception:
            return

    def _note_exit(self) -> None:
        p = self._proc
        log.warning("video relay %s: ffmpeg exited (code %s) after %.0f s — %s", self.name,
                    p.returncode if p else "?", time.monotonic() - self._started_at,
                    " | ".join(self._stderr) or "no error output")

    def _note_skip(self, n_bytes: int) -> None:
        self.skips += 1
        self.dropped_bytes += n_bytes
        now = time.monotonic()
        if now - self._skip_log_at >= SKIP_LOG_S:
            log.warning("video relay %s: ffmpeg/go2rtc behind (> %d MB queued), skipped to the next "
                        "keyframe %d time(s) since the last report, %d KB dropped in total",
                        self.name, MAX_BUFFER // (1024 * 1024), self.skips - self._skips_logged,
                        self.dropped_bytes // 1024)
            self._skip_log_at, self._skips_logged = now, self.skips

    def stats(self) -> dict:
        return {"restarts": self.restarts, "skips": self.skips, "dropped_kb": self.dropped_bytes // 1024}

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def feed(self, chunk: bytes, starts_key: bool) -> None:
        """Pass one TS chunk on. starts_key: the chunk begins a keyframe."""
        if not ENABLED:
            return
        if not self._alive():
            if self._proc is not None and self._proc.returncode is not None:
                self.restarts += 1
                self._note_exit()
                self._proc = None
                self._wait_key = True
            if not starts_key or time.monotonic() - self._started_at < RESTART_MIN_S:
                self.dropped_bytes += len(chunk)
                return
            try:
                await self._spawn()
            except Exception as exc:                 # ffmpeg missing: live view off, rest unaffected
                log.warning("video relay %s: cannot start ffmpeg: %s", self.name, exc)
                self._proc = None
                return
            self._wait_key = False
        if self._wait_key:
            if not starts_key:
                self.dropped_bytes += len(chunk)
                return
            self._wait_key = False
        stdin = self._proc.stdin
        try:
            if stdin.transport.get_write_buffer_size() > MAX_BUFFER:
                self._wait_key = True                # ffmpeg / go2rtc too slow: skip to a keyframe
                self._note_skip(len(chunk))
                return
            stdin.write(chunk)
            if self._fed_at is None:
                self._fed_at = time.monotonic()
        except (BrokenPipeError, ConnectionResetError, RuntimeError, AttributeError):
            self._wait_key = True
            self.dropped_bytes += len(chunk)

    async def close(self) -> None:
        p, self._proc = self._proc, None
        if p is None or p.returncode is not None:
            return
        try:
            p.stdin.close()
            await asyncio.wait_for(p.wait(), 3.0)
        except Exception:
            try:
                p.kill()
            except ProcessLookupError:
                pass


class LiveRelay:
    """A mission's live outputs: the copied stream (always) and the 720p grid sub-stream
    (only while want_sub() says a viewer wants it)."""

    def __init__(self, slot: str, want_sub: Optional[Callable[[], Awaitable[bool]]] = None,
                 src_size: tuple = (0, 0)):
        self.slot = slot
        self.src_size = src_size
        self.main = Relay(slot)
        self.sub: Optional[Relay] = None
        self._want = want_sub if SUBSTREAM else None
        self._next_check = 0.0
        self._sub_on = False
        self._sub_asked_at: Optional[float] = None   # a viewer asked; the sub-stream is not up yet

    async def feed(self, chunk: bytes, starts_key: bool) -> None:
        await self.main.feed(chunk, starts_key)
        if self._want is None:
            return
        now = time.monotonic()
        if now >= self._next_check:
            self._next_check = now + SUB_CHECK_S
            try:
                self._sub_on = bool(await self._want())
            except Exception:
                self._sub_on = False
            if not self._sub_on and self.sub is not None:
                await self.sub.close()
                self.sub = None
                self._sub_asked_at = None
        if self._sub_on:
            if self.sub is None:
                self.sub = Relay(f"{self.slot}_sub", transcode=True, src_size=self.src_size)
            await self.sub.feed(chunk, starts_key)

    def want_now(self) -> None:
        """A viewer just asked for the sub-stream: check at the next chunk, not in up to 2 s."""
        self._next_check = 0.0
        if self.sub_ready():
            self._sub_asked_at = None
        elif self._sub_asked_at is None:
            self._sub_asked_at = time.monotonic()

    def sub_ready(self) -> bool:
        return self.sub is not None and self.sub.ready()

    def sub_starting(self) -> bool:
        """Asked for less than SUB_WAIT_S ago and not up yet: the viewer should wait for it."""
        return (not self.sub_ready() and self._sub_asked_at is not None
                and time.monotonic() - self._sub_asked_at < SUB_WAIT_S)

    def main_ready(self) -> bool:
        return self.main.ready()

    def stats(self) -> dict:
        """Counters for the mission's video hash (R9): main relay and grid sub-stream."""
        out = {f"relay_{k}": v for k, v in self.main.stats().items()}
        if self.sub is not None:
            out.update({f"sub_{k}": v for k, v in self.sub.stats().items()})
        return out

    async def close(self) -> None:
        await self.main.close()
        if self.sub is not None:
            await self.sub.close()
            self.sub = None
