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


def _encoder_args(encoder: str) -> list:
    rate = [f"-b:v", f"{SUB_KBPS}k", "-maxrate", f"{SUB_KBPS * 4 // 3}k", "-bufsize", f"{SUB_KBPS * 2 // 3}k",
            "-g", "30", "-bf", "0"]
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ll", "-rc", "cbr", *rate]
    return ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-pix_fmt", "yuv420p",
            "-threads", "2", *rate]


_sub_encoder: Optional[str] = None


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
        log.info("video relay: grid sub-stream encoder %s (%dp, %d kbps)", _sub_encoder, SUB_HEIGHT, SUB_KBPS)
    return _sub_encoder


class Relay:
    def __init__(self, stream_name: str, transcode: bool = False):
        self.name = stream_name
        self.transcode = transcode
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

    async def _spawn(self) -> None:
        self._started_at = time.monotonic()
        codec = (["-vf", f"scale=-2:{SUB_HEIGHT}", *_encoder_args(await sub_encoder())]
                 if self.transcode else ["-c", "copy"])
        self._proc = await asyncio.create_subprocess_exec(
            FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
            "-fflags", "+genpts+nobuffer", "-f", "mpegts", "-i", "pipe:0",
            "-map", "0:v:0", *codec, "-f", "rtsp", "-rtsp_transport", "tcp",
            f"{RTSP_BASE}/{self.name}",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE)
        self._stderr.clear()
        self._stderr_task = asyncio.create_task(self._drain_stderr(self._proc))

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

    def __init__(self, slot: str, want_sub: Optional[Callable[[], Awaitable[bool]]] = None):
        self.slot = slot
        self.main = Relay(slot)
        self.sub: Optional[Relay] = None
        self._want = want_sub if SUBSTREAM else None
        self._next_check = 0.0
        self._sub_on = False

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
        if self._sub_on:
            if self.sub is None:
                self.sub = Relay(f"{self.slot}_sub", transcode=True)
            await self.sub.feed(chunk, starts_key)

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
