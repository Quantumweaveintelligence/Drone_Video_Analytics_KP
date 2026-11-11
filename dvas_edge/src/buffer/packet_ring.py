"""
src/buffer/packet_ring.py
=========================
The last N seconds of *compressed* video packets (H.264), GOP-aligned.

Used for:
- resume after a reconnect: replay from the keyframe at or before the first
  unsent sequence, while the packet remains in the ring;
- evidence clips: remux packets to MP4 without decoding or re-encoding (source quality);
- native-resolution CFS stills when the decoded frame is no longer in memory.

Capped by duration and by bytes. Eviction always removes a whole GOP from the front, so the
ring always starts at a keyframe and every packet in it is decodable.
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional


@dataclass
class RingPacket:
    seq: int
    pts: Optional[int]          # in the source stream's time base
    dts: Optional[int]
    capture_ts: float           # wall clock (epoch s) when the packet arrived / was captured
    key: bool
    data: bytes
    telemetry: dict = field(default_factory=dict)


class PacketRing:
    def __init__(self, max_seconds: float = 60.0, max_bytes: int = 256 * 1024 * 1024) -> None:
        self._max_s = max_seconds
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._packets: Deque[RingPacket] = deque()
        self._bytes = 0
        self.evicted_gops = 0

    def add(self, pkt: RingPacket) -> None:
        with self._lock:
            if not self._packets and not pkt.key:
                return                      # never start the ring with an undecodable packet
            self._packets.append(pkt)
            self._bytes += len(pkt.data)
            self._evict()

    def _span_s(self) -> float:
        if len(self._packets) < 2:
            return 0.0
        return (self._packets[-1].capture_ts - self._packets[0].capture_ts)

    def _evict(self) -> None:
        while (self._bytes > self._max_bytes or self._span_s() > self._max_s) and self._gop_count() > 1:
            first = self._packets.popleft()
            self._bytes -= len(first.data)
            while self._packets and not self._packets[0].key:
                self._bytes -= len(self._packets.popleft().data)
            self.evicted_gops += 1

    def _gop_count(self) -> int:
        return sum(1 for p in self._packets if p.key)

    def from_last_keyframe(self) -> List[RingPacket]:
        """Packets from the newest keyframe to the end (what a reconnect resends first)."""
        with self._lock:
            for i in range(len(self._packets) - 1, -1, -1):
                if self._packets[i].key:
                    return list(self._packets)[i:]
            return []

    def from_first_keyframe(self) -> List[RingPacket]:
        """All retained packets from the oldest keyframe (used for a delayed first connect)."""
        with self._lock:
            for i, pkt in enumerate(self._packets):
                if pkt.key:
                    return list(self._packets)[i:]
            return []

    def from_keyframe_before(self, seq: int) -> List[RingPacket]:
        """Packets from the keyframe at or before `seq` to the end; [] if `seq` left the ring."""
        with self._lock:
            pkts = list(self._packets)
        start: Optional[int] = None
        for i, p in enumerate(pkts):
            if p.seq > seq:
                break
            if p.key:
                start = i
        return pkts[start:] if start is not None else []

    def between(self, start_ts: float, end_ts: float) -> List[RingPacket]:
        """Packets covering [start_ts, end_ts] (capture time), starting at the keyframe at or before start_ts."""
        with self._lock:
            pkts = list(self._packets)
        start = 0
        for i, p in enumerate(pkts):
            if p.key and p.capture_ts <= start_ts:
                start = i
        return [p for p in pkts[start:] if p.capture_ts <= end_ts]

    def gop_for_seq(self, seq: int) -> List[RingPacket]:
        """Keyframe at or before `seq` through `seq` (enough to decode that frame)."""
        with self._lock:
            pkts = list(self._packets)
        start: Optional[int] = None
        for i, p in enumerate(pkts):
            if p.seq > seq:
                break
            if p.key:
                start = i
        if start is None:
            return []
        return [p for p in pkts[start:] if p.seq <= seq]

    def stats(self) -> dict:
        with self._lock:
            return {
                "packets": len(self._packets),
                "bytes": self._bytes,
                "seconds": round(self._span_s(), 2),
                "evicted_gops": self.evicted_gops,
            }
