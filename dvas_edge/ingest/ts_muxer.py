"""
ts_muxer.py
===========
Remux H.264 packets into MPEG-TS bytes in memory (no decode, no re-encode).

One muxer per uplink connection: every connection starts with a fresh TS stream
(PAT/PMT, then SPS/PPS + keyframe), timestamps rebased so the first packet is 0.
FFmpeg's mpegts muxer converts MP4-style (AVCC) packets to Annex B on its own.

The muxer is built from a `TemplateParams` snapshot (codec name, extradata, size,
time base) captured once from the source stream, never from the live stream object.
A live `av.Stream` belongs to the reader thread's container; building a muxer from it
on the uplink thread races libav and crashes the process (native access violation).
The snapshot is plain data, so any number of muxers can be built from it, on any
thread, after the source has closed.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from fractions import Fraction
from typing import Optional

import av

TS_PACKET = 188


@dataclass(frozen=True)
class TemplateParams:
    """Thread-safe snapshot of a video stream's codec parameters for remuxing."""
    codec: str
    extradata: bytes
    width: int
    height: int
    time_base: Fraction

    @classmethod
    def from_stream(cls, stream: "av.video.stream.VideoStream") -> "TemplateParams":
        cc = stream.codec_context
        return cls(
            codec=cc.name,
            extradata=bytes(cc.extradata or b""),
            width=cc.width,
            height=cc.height,
            time_base=stream.time_base or Fraction(1, 90000),
        )


class _Sink(io.RawIOBase):
    def __init__(self) -> None:
        self.buf = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:
        self.buf += b
        return len(b)


class TsMuxer:
    def __init__(self, template: TemplateParams) -> None:
        self._sink = _Sink()
        self._out = av.open(self._sink, "w", format="mpegts", options={"flush_packets": "1"})
        self._stream = self._out.add_stream(template.codec)
        ctx = self._stream.codec_context
        ctx.width = template.width
        ctx.height = template.height
        if template.extradata:
            ctx.extradata = template.extradata       # SPS/PPS: lets FFmpeg convert AVCC -> Annex B
        self._stream.time_base = template.time_base
        ctx.time_base = template.time_base
        self._time_base: Fraction = template.time_base
        self._base: Optional[int] = None

    def mux(self, data: bytes, pts: Optional[int], dts: Optional[int], key: bool) -> bytes:
        """Mux one packet; returns the TS bytes it produced (a multiple of 188, possibly empty)."""
        if self._base is None:
            self._base = dts if dts is not None else (pts or 0)
        pkt = av.Packet(data)
        pkt.stream = self._stream
        pkt.time_base = self._time_base
        pkt.pts = None if pts is None else pts - self._base
        pkt.dts = None if dts is None else dts - self._base
        pkt.is_keyframe = key
        self._out.mux(pkt)
        return self._take()

    def rebased_ms(self, pts: Optional[int]) -> float:
        if pts is None or self._base is None:
            return 0.0
        return float((pts - self._base) * self._time_base * 1000)

    def close(self) -> bytes:
        self._out.close()
        return self._take()

    def _take(self) -> bytes:
        out = bytes(self._sink.buf)
        self._sink.buf.clear()
        return out
