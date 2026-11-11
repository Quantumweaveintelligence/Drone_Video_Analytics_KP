"""Decode and re-encode source packets to low-latency H.264 for the video uplink."""
from __future__ import annotations

import logging
from fractions import Fraction
from typing import Dict, List, Optional

import av

from src.buffer.packet_ring import RingPacket
from ingest.ts_muxer import TemplateParams

log = logging.getLogger("h264_transcoder")

_AUTO_ENCODERS = ("h264_nvenc", "h264_qsv", "h264_amf", "libx264")


class H264Transcoder:
    """H.264 encoder with actual-open probing and a CPU fallback.

    B-frames are disabled and GOPs are one second so reconnects can begin promptly at an IDR.
    The input decoder still decodes every packet; output frames are resized only on this path.
    """

    def __init__(self, input_stream, seq_store, fps: float, max_kbps: int = 8000,
                 max_width: int = 1920, encoder: str = "auto") -> None:
        self._seq = seq_store
        self._input_tb = input_stream.time_base or Fraction(1, 90000)
        self._meta_by_pts: Dict[int, RingPacket] = {}
        self._decoder = av.CodecContext.create(input_stream.codec_context.name, "r")
        self._decoder.extradata = bytes(input_stream.codec_context.extradata or b"")
        self._decoder.width = input_stream.width
        self._decoder.height = input_stream.height
        self._decoder.open()

        scale = min(1.0, max_width / max(1, input_stream.width))
        self.width = max(2, int(input_stream.width * scale) // 2 * 2)
        self.height = max(2, int(input_stream.height * scale) // 2 * 2)
        self.fps = max(1.0, float(fps))
        self.time_base = self._input_tb
        self.target_kbps = max(250, int(max_kbps))
        self.encoder_name, self._encoder = self._open_encoder(encoder)
        self.template = TemplateParams(codec="h264", extradata=bytes(self._encoder.extradata or b""),
                                       width=self.width, height=self.height, time_base=self.time_base)
        log.info("h264_transcoder_started encoder=%s output=%dx%d fps=%.2f bitrate_kbps=%d source=%s",
                 self.encoder_name, self.width, self.height, self.fps, self.target_kbps,
                 input_stream.codec_context.name)

    def _open_encoder(self, preference: str):
        names = _AUTO_ENCODERS if preference == "auto" else (preference, "libx264") if preference != "libx264" else (preference,)
        failures = []
        for name in names:
            try:
                ctx = av.CodecContext.create(name, "w")
                ctx.width, ctx.height, ctx.pix_fmt = self.width, self.height, "yuv420p"
                ctx.time_base = self.time_base
                ctx.framerate = Fraction(max(1, round(self.fps * 1000)), 1000)
                ctx.bit_rate = self.target_kbps * 1000
                gop = max(1, round(self.fps))
                base = {"bf": "0", "g": str(gop), "keyint_min": str(gop),
                        "b": f"{self.target_kbps}k", "maxrate": f"{self.target_kbps}k",
                        "bufsize": f"{self.target_kbps * 2}k"}
                if name == "libx264":
                    base.update({"preset": "veryfast", "tune": "zerolatency", "sc_threshold": "0"})
                elif name == "h264_nvenc":
                    base.update({"preset": "p4", "tune": "ull", "rc": "cbr", "zerolatency": "1"})
                elif name == "h264_qsv":
                    base.update({"preset": "veryfast", "async_depth": "1"})
                elif name == "h264_amf":
                    base.update({"quality": "speed", "usage": "lowlatency", "rc": "cbr"})
                ctx.options = base
                ctx.open()
                return name, ctx
            except (av.FFmpegError, NotImplementedError, ValueError) as exc:
                failures.append(f"{name}: {type(exc).__name__}: {exc}")
                log.warning("h264_encoder_unavailable encoder=%s error=%s", name, exc)
        raise RuntimeError("No usable H.264 encoder: " + "; ".join(failures))

    def process(self, source: RingPacket) -> List[RingPacket]:
        """Decode one source packet and return zero or more H.264 packets with source identity."""
        packet = av.Packet(source.data)
        packet.pts, packet.dts, packet.time_base = source.pts, source.dts, self._input_tb
        if source.pts is not None:
            self._meta_by_pts[source.pts] = source
        try:
            frames = self._decoder.decode(packet)
        except av.FFmpegError as exc:
            log.warning("h264_transcode_decode_failed seq=%s error=%s", source.seq, exc)
            return []
        return self._encode_frames(frames, source)

    def flush(self) -> List[RingPacket]:
        output: List[RingPacket] = []
        try:
            output.extend(self._encode_frames(self._decoder.decode(None), None))
            for packet in self._encoder.encode(None):
                source = self._meta_by_pts.pop(packet.pts, None) if packet.pts is not None else None
                if source is not None:
                    output.append(self._encoded_packet(packet, source))
        except av.FFmpegError as exc:
            log.warning("h264_transcode_flush_failed: %s", exc)
        return output

    def _encode_frames(self, frames, fallback: Optional[RingPacket]) -> List[RingPacket]:
        output: List[RingPacket] = []
        for frame in frames:
            source = self._meta_by_pts.get(frame.pts) if frame.pts is not None else fallback
            if source is None:
                continue
            converted = frame.reformat(width=self.width, height=self.height, format="yuv420p")
            converted.pts = frame.pts
            converted.time_base = self.time_base
            try:
                packets = self._encoder.encode(converted)
            except av.FFmpegError as exc:
                log.error("h264_transcode_encode_failed seq=%s encoder=%s error=%s", source.seq,
                          self.encoder_name, exc)
                raise
            for packet in packets:
                src = self._meta_by_pts.pop(packet.pts, source) if packet.pts is not None else source
                output.append(self._encoded_packet(packet, src))
        return output

    def _encoded_packet(self, packet, source: RingPacket) -> RingPacket:
        return RingPacket(seq=self._seq.next(), pts=packet.pts, dts=packet.dts,
                          capture_ts=source.capture_ts, key=bool(packet.is_keyframe),
                          data=bytes(packet), telemetry=dict(source.telemetry or {}))
