"""
src/video/local_decoder.py
==========================
Decodes the uplink's H.264 packets for the local pipeline (change detection, CFS, preview).

H.264 P/B-frames reference earlier frames, so every packet must be decoded even though only
`local_fps` frames per second are emitted. Hardware decode (d3d11va, then cuda) is used when
available, else software. Emitted frames are scaled to `local_width` and carry the video
`seq` as frame_index, so a CFS selection maps to exactly one uplinked video frame.
"""
from __future__ import annotations

import collections
import logging
import threading
import time
from typing import Callable, Deque, Dict, Optional

import av

from ingest.ts_muxer import TemplateParams
from src.buffer.packet_ring import RingPacket
from src.video.drone_video_source import VideoFrame

log = logging.getLogger("local_decoder")

_HW_ORDER = ("d3d11va", "cuda")
_MAX_QUEUE = 240            # packets (~8 s at 30 fps); beyond this, skip to the next keyframe


def _open_codec(template, prefer_hw: bool):
    name = template.codec if isinstance(template, TemplateParams) else template.codec_context.name
    extradata = template.extradata if isinstance(template, TemplateParams) else template.codec_context.extradata
    if prefer_hw:
        try:
            from av.codec.hwaccel import HWAccel
            available = set(av.codec.hwaccel.hwdevices_available())
            for device in _HW_ORDER:
                if device in available:
                    ctx = av.CodecContext.create(name, "r", hwaccel=HWAccel(device_type=device, allow_software_fallback=True))
                    ctx.extradata = extradata
                    return ctx, device
        except (ImportError, av.FFmpegError, ValueError) as exc:
            log.warning("hw_decode_unavailable: %s", exc)
    ctx = av.CodecContext.create(name, "r")
    ctx.extradata = extradata
    return ctx, "software"


class LocalDecoder:
    def __init__(
        self,
        template,
        on_frame: Callable[[VideoFrame], None],
        local_fps: float = 10.0,
        local_width: int = 960,
        prefer_hw: bool = True,
    ) -> None:
        self._template = template
        self._time_base = template.time_base
        self._on_frame = on_frame
        self._interval = 1.0 / max(0.1, local_fps)
        self._width = local_width
        self._prefer_hw = prefer_hw
        self._queue: Deque[RingPacket] = collections.deque()
        self._cond = threading.Condition()
        self._by_pts: Dict[int, RingPacket] = {}
        self._skipping = False
        self._next_emit = 0.0
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.mode = "not started"
        self.decoded_frames = 0
        self.emitted_frames = 0
        self.skipped_packets = 0

    def push(self, pkt: RingPacket) -> None:
        with self._cond:
            if self._skipping and not pkt.key:
                self.skipped_packets += 1
                return
            if len(self._queue) >= _MAX_QUEUE:
                self._skipping = True                     # decoder cannot keep up
                self.skipped_packets += 1
                return
            self._skipping = False
            self._queue.append(pkt)
            self._cond.notify()

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, name="LocalDecoder", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        with self._cond:
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        ctx, self.mode = _open_codec(self._template, self._prefer_hw)
        log.info("local_decoder_started mode=%s", self.mode)
        while self._running:
            with self._cond:
                while self._running and not self._queue:
                    self._cond.wait(timeout=0.5)
                if not self._running:
                    break
                pkt = self._queue.popleft()
            t0 = time.monotonic()
            av_pkt = av.Packet(pkt.data)
            av_pkt.pts, av_pkt.dts, av_pkt.time_base = pkt.pts, pkt.dts, self._time_base
            if pkt.pts is not None:
                self._by_pts[pkt.pts] = pkt
            try:
                frames = ctx.decode(av_pkt)
            except av.FFmpegError as exc:
                log.warning("local_decode_error seq=%s: %s", pkt.seq, exc)
                continue
            for frame in frames:
                self.decoded_frames += 1
                self._emit(frame, (time.monotonic() - t0) * 1000.0)
            if len(self._by_pts) > 512:                  # B-frame reorder window is small
                for k in sorted(self._by_pts)[:256]:
                    del self._by_pts[k]

    def _emit(self, frame: av.VideoFrame, latency_ms: float) -> None:
        src = self._by_pts.pop(frame.pts, None) if frame.pts is not None else None
        if src is None:
            return
        if src.capture_ts < self._next_emit:
            return
        self._next_emit += self._interval
        if self._next_emit <= src.capture_ts:          # first frame, or after a pause: restart the grid
            self._next_emit = src.capture_ts + self._interval
        h = max(2, int(round(frame.height * self._width / frame.width / 2)) * 2)
        img = frame.reformat(width=self._width, height=h, format="bgr24").to_ndarray()
        self.emitted_frames += 1
        self._on_frame(VideoFrame(
            frame_index=src.seq,
            utc_timestamp_ms=int(src.capture_ts * 1000),
            frame=img,
            width=self._width,
            height=h,
            decode_latency_ms=latency_ms,
            telemetry=dict(src.telemetry or {}),
        ))

    def stats(self) -> dict:
        return {
            "mode": self.mode,
            "queue": len(self._queue),
            "decoded_frames": self.decoded_frames,
            "emitted_frames": self.emitted_frames,
            "skipped_packets": self.skipped_packets,
        }
