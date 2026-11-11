"""
src/video/uplink_source.py
==========================
Video source for the h264_ws transport. Same public interface as RealVideoSource, so
PipelineManager, the status endpoints and the telemetry endpoint work unchanged.

One reader owns the source (many RTSP cameras and DJI remotes allow one session):
    PyAV demux (packets, no decode) -> seq + capture time + telemetry -> PacketRing
                                                                    -> packet listeners (VideoUplink)
                                                                    -> LocalDecoder -> on_frame
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, List, Optional

import av

from src.buffer.packet_ring import PacketRing, RingPacket
from src.video.h264_transcoder import H264Transcoder
from src.video.local_decoder import LocalDecoder
from src.video.real_video_source import _empty_telemetry, _load_telemetry, telemetry_for_frame

log = logging.getLogger("uplink_source")

_RECONNECT_MAX_S = 30.0


def _source_kind(url: str) -> str:
    u = url.lower()
    if u.startswith("rtsp://"):
        return "rtsp"
    if u.startswith("rtmp://"):
        return "rtmp"
    return "file"


def _open_options(kind: str) -> dict:
    if kind == "rtsp":
        return {"rtsp_transport": "tcp", "timeout": "8000000"}
    if kind == "rtmp":
        return {"rw_timeout": "8000000"}
    return {}


class UplinkVideoSource:
    def __init__(
        self,
        drone_id: str,
        video_source: str,
        seq_store,
        on_frame: Callable,
        on_complete: Optional[Callable[[], None]] = None,
        on_stream_ready: Optional[Callable] = None,
        initial_play_state: bool = True,
        local_fps: float = 10.0,
        local_width: int = 960,
        playback_speed: float = 1.0,
        ring: Optional[PacketRing] = None,
        transcode_enabled: bool = True,
        transcode_max_kbps: int = 8000,
        transcode_max_width: int = 1920,
        transcode_encoder: str = "auto",
    ) -> None:
        self.drone_id = drone_id
        self.video_source = video_source
        self.on_frame = on_frame
        self.on_complete = on_complete
        self._on_stream_ready = on_stream_ready
        self._seq = seq_store
        self._kind = _source_kind(video_source)
        self._live = self._kind != "file"
        self._local_fps = local_fps
        self._local_width = local_width
        self._speed = max(0.1, playback_speed)
        self.ring = ring or PacketRing()
        self._transcode_enabled = transcode_enabled
        self._transcode_max_kbps = max(250, int(transcode_max_kbps))
        self._transcode_max_width = max(2, int(transcode_max_width))
        self._transcode_encoder = transcode_encoder
        self._transcoder: Optional[H264Transcoder] = None
        self.uplink_codec = ""
        self.uplink_width = 0
        self.uplink_height = 0
        self.uplink_template = None
        self.source_bitrate = 0
        self._listeners: List[Callable[[RingPacket], None]] = []
        self.decoder: Optional[LocalDecoder] = None

        self._stop = threading.Event()
        self._play = threading.Event()
        if self._live or initial_play_state:
            self._play.set()
        self._thread: Optional[threading.Thread] = None
        self._latest_tel = _empty_telemetry()
        self._srt: dict = {}
        self._srt_keys: List[int] = []

        # RealVideoSource-compatible public state
        self._frame_index = 0
        self.is_connected = False
        self.source_fps = 0.0
        self.active_source_type = self._kind
        self.codec = ""
        self._using_cuvid = False

    # ── RealVideoSource-compatible API ────────────────────────────────────────

    def add_packet_listener(self, fn: Callable[[RingPacket], None]) -> None:
        self._listeners.append(fn)

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"UplinkReader-{self.drone_id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._play.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=10)
        if self.decoder is not None:
            self.decoder.stop()
        self.is_connected = False

    def pause(self) -> None:
        if not self._live:
            self._play.clear()

    def resume(self) -> None:
        self._play.set()

    @property
    def is_paused(self) -> bool:
        return not self._live and not self._play.is_set()

    def get_current_telemetry(self) -> dict:
        return dict(self._latest_tel)

    # ── reader ────────────────────────────────────────────────────────────────

    def _run(self) -> None:
        delay = 0.5
        while not self._stop.is_set():
            try:
                finished = self._read_once()
                delay = 0.5
                if finished:                            # file played to the end
                    if self.on_complete is not None:
                        threading.Thread(target=self.on_complete, daemon=True).start()
                    return
            except (av.FFmpegError, OSError) as exc:
                self.is_connected = False
                log.warning("uplink_source_read_error source=%s error=%s", self._kind, exc)
                if not self._live:
                    return
            if self._stop.wait(delay):
                return
            delay = min(_RECONNECT_MAX_S, delay * 1.5)

    def _read_once(self) -> bool:
        container = av.open(self.video_source, options=_open_options(self._kind))
        try:
            stream = container.streams.video[0]
            fps = float(stream.average_rate or stream.guessed_rate or 30.0)
            self.codec = stream.codec_context.name
            self.source_bitrate = int(stream.codec_context.bit_rate or container.bit_rate or 0)
            needs_transcode = self._transcode_enabled and (
                self.codec.lower() not in ("h264", "avc")
                or self.source_bitrate > self._transcode_max_kbps * 1000
            )
            self._transcoder = None
            if needs_transcode:
                try:
                    self._transcoder = H264Transcoder(
                        stream, self._seq, fps, self._transcode_max_kbps, self._transcode_max_width,
                        self._transcode_encoder
                    )
                except (av.FFmpegError, RuntimeError, ValueError) as exc:
                    log.error("uplink_transcode_unavailable codec=%s bitrate=%d error=%s",
                              self.codec, self.source_bitrate, exc)
            self.uplink_codec = "h264" if self._transcoder is not None else self.codec.lower()
            self.uplink_width = self._transcoder.width if self._transcoder is not None else stream.width
            self.uplink_height = self._transcoder.height if self._transcoder is not None else stream.height
            self.uplink_template = self._transcoder.template if self._transcoder is not None else stream
            if not self._live and not self._srt:
                self._srt = _load_telemetry(self.video_source, fps)
                self._srt_keys = sorted(self._srt)
            if self.decoder is None:
                decoder_template = self._transcoder.template if self._transcoder is not None else stream
                self.decoder = LocalDecoder(decoder_template, self.on_frame, self._local_fps, self._local_width)
                self.decoder.start()
            if self._on_stream_ready is not None:
                self._on_stream_ready(stream, fps)
            self.is_connected = True
            log.info("uplink_source_open kind=%s codec=%s %dx%d fps=%.2f",
                     self._kind, self.codec, stream.width, stream.height, fps)
            self._demux(container, stream, fps)
            return not self._live and not self._stop.is_set()
        finally:
            if self._transcoder is not None:
                for encoded in self._transcoder.flush():
                    self._dispatch(encoded)
                    if self.decoder is not None:
                        self.decoder.push(encoded)
            container.close()

    def _demux(self, container, stream, fps: float) -> None:
        tb = stream.time_base
        wall0: Optional[float] = None
        t0: Optional[float] = None
        mission_wall = time.time()
        window_start, window_frames = time.monotonic(), 0
        for pkt in container.demux(stream):
            if self._stop.is_set():
                return
            if pkt.dts is None and pkt.pts is None:
                continue                                 # demuxer flush packet
            if not self._play.is_set():
                paused_at = time.monotonic()
                self._play.wait()
                if wall0 is not None:
                    wall0 += time.monotonic() - paused_at  # keep pacing after a pause
            # Pace on decode time (monotonic, B-frames included); stamp on presentation time
            t_dec = float((pkt.dts if pkt.dts is not None else pkt.pts) * tb)
            t_s = float((pkt.pts if pkt.pts is not None else pkt.dts) * tb)
            if not self._live:
                if wall0 is None:
                    wall0, t0 = time.monotonic(), t_dec
                wait = wall0 + (t_dec - t0) / self._speed - time.monotonic()
                if wait > 0 and self._stop.wait(wait):
                    return
                capture_ts = mission_wall + (t_s - t0)
                tel = telemetry_for_frame(self._srt, self._srt_keys, int(round(t_s * fps)))
            else:
                capture_ts = time.time()
                tel = _empty_telemetry()
            self._latest_tel = tel
            rp = RingPacket(seq=0 if self._transcoder is not None else self._seq.next(),
                            pts=pkt.pts, dts=pkt.dts, capture_ts=capture_ts,
                            key=bool(pkt.is_keyframe), data=bytes(pkt), telemetry=tel)
            if self._transcoder is not None:
                for encoded in self._transcoder.process(rp):
                    self._dispatch(encoded)
                    self.decoder.push(encoded)
            else:
                self._dispatch(rp)
                self.decoder.push(rp)
            self._frame_index += 1
            window_frames += 1
            now = time.monotonic()
            if now - window_start >= 2.0:
                self.source_fps = window_frames / (now - window_start)
                window_start, window_frames = now, 0

    def _dispatch(self, pkt: RingPacket) -> None:
        self.ring.add(pkt)
        for listener in self._listeners:
            listener(pkt)
