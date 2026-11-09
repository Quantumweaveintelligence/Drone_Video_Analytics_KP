"""
video_uplink.py
===============
Continuous H.264 video uplink to the ingest server over WebSocket (contract §3 v1).

    edge -> server   hello, then for every frame: text `meta` followed by binary MPEG-TS bytes;
                     `auth` (token refresh), `control` (AI toggle), `stats_edge`, `bye`
    server -> edge   ready | error, then `stats`, `slow_down`, `auth_ok`

Packets are pushed from the reader thread with push(); one asyncio thread owns the socket.
Back-pressure: when more than BACKLOG_S of video is queued, whole GOPs are dropped (the
view stays live; the packets remain in the PacketRing for evidence).
Reconnect: back-off 0.5 -> 8 s with jitter; every connection restarts at the newest
keyframe in the PacketRing and continues the mission's seq numbering.
Fallback: HTTP 404 on upgrade (old server) or 5 failed connects in a row -> on_fallback(True)
so the JPEG path takes over; the socket is retried every 60 s.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Callable, Deque, Optional
from urllib.parse import urlencode

import websockets
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from ingest.ts_muxer import TsMuxer
from src.buffer.packet_ring import PacketRing, RingPacket

log = logging.getLogger("video_uplink")

CONTRACT_VERSION = 1
BACKLOG_S = 2.0
PING_S = 15.0
STATS_S = 2.0
READY_TIMEOUT_S = 10.0
FAILS_BEFORE_FALLBACK = 5
FALLBACK_RETRY_S = 60.0
AUTH_REFRESH_AT = 0.8          # share of the token lifetime after which `auth` is sent

# Close codes (contract §3.6)
CLOSE_AUTH = 4401
CLOSE_NOT_OWNER = 4403
CLOSE_NOT_LIVE = 4409
CLOSE_TOO_FAST = 4413
CLOSE_CODEC = 4415


@dataclass
class StreamInfo:
    codec: str
    width: int
    height: int
    fps: float
    source: str                 # rtsp | rtmp | file | usb


class UplinkStopped(Exception):
    """The server refused the stream for a reason a retry cannot fix (4403 / 4415)."""


class VideoUplink:
    def __init__(
        self,
        url: str,
        client,                                  # IngestClient: token + mission/start
        drone_id: str,
        mission_id: str,
        laptop_id: str,
        info: StreamInfo,
        template_stream,
        ring: PacketRing,
        analytics: bool = True,
        on_fallback: Callable[[bool], None] = lambda _active: None,
        on_error: Callable[[str], None] = lambda _msg: None,
    ) -> None:
        self._url = f"{url}?{urlencode({'drone_id': drone_id, 'mission_id': mission_id})}"
        self._client = client
        self._drone_id = drone_id
        self._mission_id = mission_id
        self._laptop_id = laptop_id
        self._info = info
        self._template = template_stream
        self._ring = ring
        self._analytics = analytics
        self._on_fallback = on_fallback
        self._on_error = on_error

        self._lock = threading.Lock()
        self._queue: Deque[RingPacket] = collections.deque()
        self._queued_bytes = 0
        self._dropping = False
        self._rate_bps = 4_000_000 / 8           # bytes/s estimate, updated from the input
        self._rate_t = time.monotonic()
        self._rate_bytes = 0

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._wake: Optional[asyncio.Event] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._connected_ws = None

        self.fallback_active = False
        self.clock_offset_ms = 0.0
        self.server_stats: dict = {}
        self.dropped_frames = 0
        self.reconnects = 0
        self.sent_frames = 0
        self.sent_bytes = 0
        self.last_error = ""

    # ── reader-thread side ────────────────────────────────────────────────────

    def push(self, pkt: RingPacket) -> None:
        """Queue one packet for sending (called for every packet, in capture order).

        Only while running: when stopped (Server Send OFF, or refused by the server) nothing is
        queued; the next start resumes from the PacketRing's newest keyframe instead.
        """
        if not self._running:
            return
        with self._lock:
            self._update_rate(len(pkt.data))
            if self._dropping and not pkt.key:
                self.dropped_frames += 1
                return
            if self._queued_bytes > self._rate_bps * BACKLOG_S:
                if not self._dropping:
                    log.warning("uplink_backlog_dropping_gops queued_bytes=%d", self._queued_bytes)
                self._dropping = True
                self.dropped_frames += 1
                return
            self._dropping = False
            self._queue.append(pkt)
            self._queued_bytes += len(pkt.data)
        self._wake_loop()

    def _wake_loop(self) -> None:
        loop = self._loop
        if loop is not None and self._wake is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._wake.set)
            except RuntimeError:          # loop closed between the check and the call: nothing to wake
                pass

    def _update_rate(self, n: int) -> None:
        self._rate_bytes += n
        now = time.monotonic()
        if now - self._rate_t >= 1.0:
            measured = self._rate_bytes / (now - self._rate_t)
            self._rate_bps = 0.7 * self._rate_bps + 0.3 * measured
            self._rate_t, self._rate_bytes = now, 0

    def set_analytics(self, enabled: bool) -> None:
        self._analytics = enabled
        ws = self._connected_ws
        if ws is not None and self._loop is not None:
            asyncio.run_coroutine_threadsafe(
                ws.send(json.dumps({"type": "control", "analytics": enabled})), self._loop)

    def behind_s(self) -> float:
        return self._queued_bytes / max(self._rate_bps, 1.0)

    def stats(self) -> dict:
        return {
            "transport": "jpeg_http" if self.fallback_active else "h264_ws",
            "connected": self._connected_ws is not None,
            "behind_s": round(self.behind_s(), 2),
            "uplink_kbps": round(self._rate_bps * 8 / 1000, 1),
            "sent_frames": self.sent_frames,
            "dropped_frames": self.dropped_frames,
            "reconnects": self.reconnects,
            "clock_offset_ms": round(self.clock_offset_ms, 1),
            "server": self.server_stats,
            "last_error": self.last_error,
        }

    # ── lifecycle ─────────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, name="VideoUplink", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._running = False
        self._wake_loop()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._wake = asyncio.Event()
        try:
            self._loop.run_until_complete(self._connect_loop())
        finally:
            self._loop.close()

    # ── connection loop ───────────────────────────────────────────────────────

    async def _connect_loop(self) -> None:
        failures = 0
        while self._running:
            try:
                await self._session()
                failures = 0
            except UplinkStopped as exc:
                self.last_error = str(exc)
                self._running = False
                self._on_error(str(exc))
                self._set_fallback(True)
                return
            except InvalidStatus as exc:
                status = exc.response.status_code
                self.last_error = f"upgrade refused: HTTP {status}"
                failures = FAILS_BEFORE_FALLBACK if status == 404 else failures + 1
                if status == 401:
                    await self._refresh_token(force=True)
            except ConnectionClosed as exc:
                failures, done = await self._handle_close(exc, failures)
                if done:
                    return
            except (OSError, asyncio.TimeoutError, websockets.WebSocketException) as exc:
                failures += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
            if not self._running:
                return
            if failures >= FAILS_BEFORE_FALLBACK:
                self._set_fallback(True)
                await self._sleep(FALLBACK_RETRY_S)
            elif failures:
                self.reconnects += 1
                await self._sleep(min(8.0, 0.5 * 2 ** (failures - 1)) * random.uniform(0.8, 1.2))

    async def _handle_close(self, exc: ConnectionClosed, failures: int) -> tuple:
        code = exc.rcvd.code if exc.rcvd else 1006
        reason = exc.rcvd.reason if exc.rcvd else ""
        self.last_error = f"closed {code} {reason}".strip()
        if code == 1000 and not self._running:
            return 0, True
        if code == CLOSE_NOT_OWNER:
            raise UplinkStopped("Not the mission owner (4403): stream refused by the server")
        if code == CLOSE_CODEC:
            raise UplinkStopped("Codec not supported by the server (4415)")
        if code == CLOSE_AUTH:
            await self._refresh_token(force=True)
        elif code == CLOSE_NOT_LIVE:
            try:
                await self._client.start_mission()
            except Exception as start_exc:    # reported, then retried with back-off
                self.last_error = f"mission/start failed: {start_exc}"
        return failures + 1, False

    async def _session(self) -> None:
        token = await self._client.get_token()
        async with connect(
            self._url,
            additional_headers={"Authorization": f"Bearer {token}"},
            ping_interval=PING_S,
            ping_timeout=PING_S * 2,
            open_timeout=10,
            max_size=1 << 20,
        ) as ws:
            first = self._resume_point()
            if first is None:
                await self._wait_for_keyframe()
                first = self._resume_point()
                if first is None:
                    return
            t_edge = time.time()
            await ws.send(json.dumps({
                "type": "hello", "v": CONTRACT_VERSION, "drone_id": self._drone_id,
                "mission_id": self._mission_id, "laptop_id": self._laptop_id,
                "codec": self._info.codec, "container": "mpegts", "width": self._info.width,
                "height": self._info.height, "fps": self._info.fps, "source": self._info.source,
                "next_seq": first.seq, "analytics": self._analytics, "t_edge": t_edge,
            }))
            ready = json.loads(await asyncio.wait_for(ws.recv(), READY_TIMEOUT_S))
            if ready.get("type") == "error":
                raise UplinkStopped(f"server refused: {ready.get('code')} {ready.get('detail', '')}")
            if ready.get("type") != "ready":
                raise websockets.WebSocketException(f"unexpected reply {ready.get('type')!r}")
            t_recv = time.time()
            if "t_server" in ready:
                self.clock_offset_ms = (float(ready["t_server"]) - (t_edge + t_recv) / 2) * 1000
            self._set_fallback(False)
            self._connected_ws = ws
            log.info("uplink_connected next_seq=%s server_last_seq=%s offset_ms=%.0f",
                     first.seq, ready.get("last_seq"), self.clock_offset_ms)
            try:
                await self._stream(ws, first)
            finally:
                self._connected_ws = None

    def _resume_point(self) -> Optional[RingPacket]:
        """Where a (re)connection starts sending.

        After a short break: the keyframe at or before the first frame not yet sent, so nothing
        is lost (the server ignores frames it already has). After a long break, or on the first
        connection: the newest keyframe, so the live view does not lag behind.
        """
        resend: list = []
        if self._last_sent_seq:
            resend = self._ring.from_keyframe_before(self._last_sent_seq + 1)
            if sum(len(p.data) for p in resend) > self._rate_bps * BACKLOG_S:
                resend = []
        if not resend:
            resend = self._ring.from_last_keyframe()
        if not resend:
            return None
        with self._lock:
            newest_key = resend[0].seq
            self._queue = collections.deque(resend + [p for p in self._queue if p.seq > resend[-1].seq])
            self._queued_bytes = sum(len(p.data) for p in self._queue)
            self._dropping = False
        return self._queue[0] if self._queue and self._queue[0].seq == newest_key else None

    async def _wait_for_keyframe(self) -> None:
        while self._running and not self._ring.from_last_keyframe():
            await self._sleep(0.1)

    async def _stream(self, ws, first: RingPacket) -> None:
        muxer = TsMuxer(self._template)
        recv_task = asyncio.create_task(self._receive(ws))
        next_stats = time.monotonic() + STATS_S
        try:
            while self._running:
                if recv_task.done():
                    recv_task.result()                # re-raise the close / error
                    return
                pkt = self._pop()
                if pkt is None:
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=0.5)
                    except asyncio.TimeoutError:
                        pass
                else:
                    ts = muxer.mux(pkt.data, pkt.pts, pkt.dts, pkt.key)
                    await ws.send(json.dumps(self._meta(pkt, muxer.rebased_ms(pkt.pts))))
                    if ts:
                        await ws.send(ts)
                    self.sent_frames += 1
                    self.sent_bytes += len(ts)
                now = time.monotonic()
                if now >= next_stats:
                    next_stats = now + STATS_S
                    await ws.send(json.dumps({
                        "type": "stats_edge", "level": "L0", "behind_s": round(self.behind_s(), 2),
                        "clock_offset_ms": round(self.clock_offset_ms, 1), "dropped_frames": self.dropped_frames,
                    }))
                    await self._maybe_refresh_auth(ws)
            await ws.send(json.dumps({"type": "bye", "last_seq": self._last_sent_seq}))
            await ws.close(1000)
        finally:
            recv_task.cancel()
            tail = muxer.close()
            if tail:
                log.debug("uplink_muxer_tail_bytes=%d", len(tail))

    _last_sent_seq = 0

    def _pop(self) -> Optional[RingPacket]:
        with self._lock:
            if not self._queue:
                return None
            pkt = self._queue.popleft()
            self._queued_bytes -= len(pkt.data)
            self._last_sent_seq = pkt.seq
            return pkt

    def _meta(self, pkt: RingPacket, pts_ms: float) -> dict:
        tel = pkt.telemetry or {}
        return {
            "type": "meta", "seq": pkt.seq, "pts_ms": round(pts_ms, 3), "capture_ts": pkt.capture_ts,
            "key": pkt.key, "change_score": tel.get("change_score"),
            "cognitive_score": tel.get("cognitive_score"), "cfs_selected": tel.get("cfs_selected"),
            "tel": {k: tel.get(k) for k in (
                "latitude", "longitude", "altitude_m", "abs_alt", "speed_mps", "battery_pct",
                "gb_yaw", "gb_pitch", "gb_roll", "focal_len", "dzoom_ratio")},
        }

    async def _receive(self, ws) -> None:
        async for raw in ws:
            if isinstance(raw, bytes):
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                log.warning("uplink_bad_server_message")
                continue
            kind = msg.get("type")
            if kind == "stats":
                self.server_stats = msg
            elif kind == "slow_down":
                log.warning("uplink_slow_down max_kbps=%s", msg.get("max_kbps"))
                self.server_stats["slow_down_kbps"] = msg.get("max_kbps")
            elif kind == "auth_ok":
                log.info("uplink_auth_refreshed")
            elif kind == "error":
                self.last_error = f"{msg.get('code')}: {msg.get('detail', '')}"

    async def _maybe_refresh_auth(self, ws) -> None:
        expiry = getattr(self._client, "token_expiry", 0.0)
        issued = getattr(self._client, "token_issued", 0.0)
        if not expiry or not issued:
            return
        if time.time() < issued + (expiry - issued) * AUTH_REFRESH_AT:
            return
        old = self._client.token
        token = await self._refresh_token(force=True)
        if token and token != old:
            await ws.send(json.dumps({"type": "auth", "token": token}))

    async def _refresh_token(self, force: bool) -> Optional[str]:
        try:
            return await self._client.refresh_token(force=force)
        except Exception as exc:            # logged; the server closes with 4401 if it matters
            self.last_error = f"token refresh failed: {exc}"
            log.warning("uplink_token_refresh_failed: %s", exc)
            return None

    def _set_fallback(self, active: bool) -> None:
        if active != self.fallback_active:
            self.fallback_active = active
            log.warning("uplink_fallback_%s", "on (JPEG path)" if active else "off (video)")
            self._on_fallback(active)

    async def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while self._running and time.monotonic() < end:
            await asyncio.sleep(min(0.25, end - time.monotonic()))
