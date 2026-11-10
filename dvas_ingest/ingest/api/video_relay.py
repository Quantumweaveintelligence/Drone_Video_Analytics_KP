# -*- coding: utf-8 -*-
"""
Live relay of a mission's TS stream to go2rtc (RunPod v2 plan R1 item 2).

One ffmpeg per mission copies the stream (no re-encode) into go2rtc's RTSP listener on
127.0.0.1; browsers watch it through nginx /live/api/ws (MSE). The relay is for live
viewing only: when ffmpeg is slow or gone, bytes are dropped up to the next keyframe and
the process is restarted. The recording and the analytics never depend on it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

log = logging.getLogger(__name__)

ENABLED = os.getenv("DVAS_VIDEO_RELAY", "1").lower() in ("1", "true", "yes")
RTSP_BASE = os.getenv("DVAS_GO2RTC_RTSP", "rtsp://127.0.0.1:8554").rstrip("/")
FFMPEG = os.getenv("DVAS_FFMPEG", "ffmpeg")
MAX_BUFFER = 4 * 1024 * 1024        # bytes waiting for ffmpeg before the relay skips to a keyframe
RESTART_MIN_S = 2.0


class Relay:
    def __init__(self, stream_name: str):
        self.name = stream_name
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._started_at = 0.0
        self._wait_key = True             # (re)start only at a keyframe
        self.dropped_bytes = 0
        self.restarts = 0

    async def _spawn(self) -> None:
        self._started_at = time.monotonic()
        self._proc = await asyncio.create_subprocess_exec(
            FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
            "-fflags", "+genpts+nobuffer", "-f", "mpegts", "-i", "pipe:0",
            "-map", "0:v:0", "-c", "copy", "-f", "rtsp", "-rtsp_transport", "tcp",
            f"{RTSP_BASE}/{self.name}",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL)

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def feed(self, chunk: bytes, starts_key: bool) -> None:
        """Pass one TS chunk on. starts_key: the chunk begins a keyframe."""
        if not ENABLED:
            return
        if not self._alive():
            if self._proc is not None and self._proc.returncode is not None:
                self.restarts += 1
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
                self.dropped_bytes += len(chunk)
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
