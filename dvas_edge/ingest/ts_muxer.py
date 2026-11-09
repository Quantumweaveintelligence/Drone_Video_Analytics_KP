"""
ts_muxer.py
===========
Remux H.264 packets into MPEG-TS bytes in memory (no decode, no re-encode).

One muxer per uplink connection: every connection starts with a fresh TS stream
(PAT/PMT, then SPS/PPS + keyframe), timestamps rebased so the first packet is 0.
FFmpeg's mpegts muxer converts MP4-style (AVCC) packets to Annex B on its own.
"""
from __future__ import annotations

import io
from fractions import Fraction
from typing import Optional

import av

TS_PACKET = 188


class _Sink(io.RawIOBase):
    def __init__(self) -> None:
        self.buf = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:
        self.buf += b
        return len(b)


class TsMuxer:
    def __init__(self, template: av.video.stream.VideoStream) -> None:
        self._sink = _Sink()
        self._out = av.open(self._sink, "w", format="mpegts", options={"flush_packets": "1"})
        self._stream = self._out.add_stream_from_template(template)
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
