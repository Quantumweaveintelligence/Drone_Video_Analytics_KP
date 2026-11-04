# -*- coding: utf-8 -*-
"""
DVAS Analytics — live annotated video for the dashboard (MJPEG).

The dashboard used to download one annotated JPEG at a time (a poll every 0.6–1.5 s
plus the download), which capped the live view at about 1 fps whatever the GPU did,
and sent full-resolution frames (1–2 MB at 4K).

Each analyzer now:
  * draws annotations on the FULL-resolution frame (detections, face tiles and plate
    crops are unaffected; evidence keeps full resolution),
  * hands the annotated frame to `AnnotatedHub.put_frame`, which encodes a display
    copy no wider than DVAS_LIVE_MAX_WIDTH (default 1920 px, JPEG q DVAS_LIVE_JPEG_Q),
  * serves `GET /annotated/stream?drone_id=` — multipart/x-mixed-replace, one part per
    new frame — and `GET /annotated/latest` from the same display copy.

Part headers carry X-Frame-Seq and X-Proc-Fps (the analyzer's real frame rate).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from typing import AsyncIterator, Deque, Dict, Iterable, Optional, Tuple

log = logging.getLogger(__name__)

BOUNDARY = "dvasframe"
MEDIA_TYPE = f"multipart/x-mixed-replace; boundary={BOUNDARY}"


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


LIVE_MAX_WIDTH = _int_env("DVAS_LIVE_MAX_WIDTH", 1920, 320, 7680)
LIVE_JPEG_Q = _int_env("DVAS_LIVE_JPEG_Q", 80, 40, 95)
STREAM_MAX_FPS = _int_env("DVAS_LIVE_MAX_FPS", 30, 1, 60)
STREAM_MAX_S = _int_env("DVAS_LIVE_STREAM_MAX_S", 600, 30, 3600)   # client reconnects after this
IDLE_TIMEOUT_S = 15.0                                              # end a stream with no new frames


def display_jpeg(img_bgr, max_width: int = LIVE_MAX_WIDTH, quality: int = LIVE_JPEG_Q) -> Optional[bytes]:
    """JPEG for the live view: downscaled to max_width (aspect kept), never upscaled."""
    import cv2  # noqa: PLC0415
    if img_bgr is None:
        return None
    h, w = img_bgr.shape[:2]
    if w > max_width:
        img_bgr = cv2.resize(img_bgr, (max_width, max(1, round(h * max_width / w))),
                             interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img_bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else None


def to_display(img_bgr, max_width: int = LIVE_MAX_WIDTH):
    """(copy of the frame no wider than max_width, scale factor applied).

    Overlays are drawn on this copy with coordinates × factor, so labels and line
    widths tuned for ~1080p stay readable when the drone sends 4K.
    """
    import cv2  # noqa: PLC0415
    h, w = img_bgr.shape[:2]
    if w <= max_width:
        return img_bgr.copy(), 1.0
    f = max_width / w
    return cv2.resize(img_bgr, (max_width, max(1, round(h * f))), interpolation=cv2.INTER_AREA), f


def _scale_box(box, f: float):
    if isinstance(box, dict):
        return {k: (int(round(v * f)) if k in ("x1", "y1", "x2", "y2", "x", "y", "w", "h")
                    and isinstance(v, (int, float)) else v) for k, v in box.items()}
    if isinstance(box, (list, tuple)):
        return [v * f if isinstance(v, (int, float)) else v for v in box]
    return box


def scale_items(items, keys, f: float) -> list:
    """Shallow copies of detection dicts with the given box fields scaled by f."""
    if f == 1.0:
        return list(items or [])
    out = []
    for it in items or []:
        c = dict(it)
        for k in keys:
            if c.get(k) is not None:
                c[k] = _scale_box(c[k], f)
        out.append(c)
    return out


def display_from_jpeg(jpeg: bytes, max_width: int = LIVE_MAX_WIDTH) -> Optional[bytes]:
    """Display copy of an already-encoded JPEG: as is when narrow enough, else downscaled."""
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    if not jpeg:
        return None
    width = _jpeg_width(jpeg)
    if 0 < width <= max_width:
        return jpeg
    # Decode at half size when that is still at least max_width wide (much faster at 4K)
    flag = cv2.IMREAD_REDUCED_COLOR_2 if width >= 2 * max_width else cv2.IMREAD_COLOR
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), flag)
    return display_jpeg(img, max_width) if img is not None else None


def _jpeg_width(jpeg: bytes) -> int:
    """Width from the JPEG SOF header (0 when not found) — avoids a decode for small frames."""
    i, n = 2, len(jpeg)
    while i + 9 < n:
        if jpeg[i] != 0xFF:
            i += 1
            continue
        marker = jpeg[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            return (jpeg[i + 7] << 8) | jpeg[i + 8]
        seg = (jpeg[i + 2] << 8) | jpeg[i + 3]
        i += 2 + seg
    return 0


class AnnotatedHub:
    """Latest display frame per drone + wake-ups for MJPEG streams."""

    def __init__(self, service: str):
        self.service = service
        self._frames: Dict[str, Tuple[int, bytes, float]] = {}    # drone → (seq, jpeg, wall ts)
        self._events: Dict[str, asyncio.Event] = {}
        self._times: Dict[str, Deque[float]] = {}
        self._seq = 0

    # ── producers (event loop) ────────────────────────────────────────────────
    def put(self, drone_id: str, jpeg: Optional[bytes]) -> None:
        """Store an encoded display JPEG and wake every stream watching this drone."""
        if not jpeg:
            return
        self._seq += 1
        now = time.time()
        self._frames[drone_id] = (self._seq, jpeg, now)
        q = self._times.setdefault(drone_id, deque(maxlen=60))
        q.append(time.monotonic())
        ev = self._events.pop(drone_id, None)
        if ev is not None:
            ev.set()

    async def put_frame(self, drone_id: str, img_bgr) -> Optional[bytes]:
        """Encode the display copy off the event loop, store it, return the bytes."""
        jpeg = await asyncio.to_thread(display_jpeg, img_bgr)
        self.put(drone_id, jpeg)
        return jpeg

    async def put_jpeg(self, drone_id: str, jpeg: bytes) -> Optional[bytes]:
        """Same, for analyzers that already produce a full-resolution annotated JPEG."""
        small = await asyncio.to_thread(display_from_jpeg, jpeg)
        self.put(drone_id, small)
        return small

    # ── consumers ─────────────────────────────────────────────────────────────
    def resolve(self, candidates: Iterable[str]) -> Optional[str]:
        """First drone id (e.g. aliases DRN-001 / DRONE_KL_001) that has frames."""
        for d in candidates:
            if d in self._frames:
                return d
        return None

    def latest(self, drone_id: str) -> Optional[Tuple[int, bytes, float]]:
        return self._frames.get(drone_id)

    def fps(self, drone_id: str) -> float:
        q = self._times.get(drone_id)
        if not q or len(q) < 2:
            return 0.0
        now = time.monotonic()
        recent = [t for t in q if now - t <= 5.0]
        if len(recent) < 2:
            return 0.0
        return round((len(recent) - 1) / max(recent[-1] - recent[0], 1e-3), 1)

    async def wait_next(self, drone_id: str, after_seq: int, timeout: float):
        cur = self._frames.get(drone_id)
        if cur and cur[0] > after_seq:
            return cur
        ev = self._events.setdefault(drone_id, asyncio.Event())
        try:
            await asyncio.wait_for(ev.wait(), timeout)
        except asyncio.TimeoutError:
            return None
        cur = self._frames.get(drone_id)
        return cur if cur and cur[0] > after_seq else None

    async def mjpeg(self, drone_id: str, max_fps: int = STREAM_MAX_FPS,
                    max_seconds: int = STREAM_MAX_S) -> AsyncIterator[bytes]:
        """multipart/x-mixed-replace body: each new frame once, at most max_fps."""
        min_gap = 1.0 / max(1, max_fps)
        started = time.monotonic()
        last_sent, seq = 0.0, 0
        cur = self._frames.get(drone_id)
        if cur:                                   # show the current frame immediately
            seq = cur[0]
            yield self._part(drone_id, cur)
            last_sent = time.monotonic()
        while time.monotonic() - started < max_seconds:
            nxt = await self.wait_next(drone_id, seq, IDLE_TIMEOUT_S)
            if nxt is None:
                return                            # no new frame for a while: client retries
            wait = min_gap - (time.monotonic() - last_sent)
            if wait > 0:
                await asyncio.sleep(wait)
                nxt = self._frames.get(drone_id) or nxt   # skip to the newest after the pause
            seq = nxt[0]
            yield self._part(drone_id, nxt)
            last_sent = time.monotonic()

    def _part(self, drone_id: str, frame: Tuple[int, bytes, float]) -> bytes:
        seq, jpeg, ts = frame
        head = (f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n"
                f"X-Frame-Seq: {seq}\r\nX-Proc-Fps: {self.fps(drone_id)}\r\n"
                f"X-Frame-Age: {max(0.0, time.time() - ts):.2f}\r\n\r\n")
        return head.encode() + jpeg + b"\r\n"


def install_stream_route(app, hub: AnnotatedHub, path: str = "/annotated/stream") -> None:
    """GET <path>?drone_id= — MJPEG of the hub's display frames (service-key guarded)."""
    from fastapi import HTTPException  # noqa: PLC0415
    from fastapi.responses import StreamingResponse  # noqa: PLC0415
    from shared.config import get_drone_aliases  # noqa: PLC0415

    @app.get(path)
    async def annotated_stream(drone_id: str = ""):
        if not drone_id or len(drone_id) > 64:
            raise HTTPException(400, "drone_id is required")
        target = hub.resolve(get_drone_aliases(drone_id)) or drone_id
        return StreamingResponse(
            hub.mjpeg(target), media_type=MEDIA_TYPE,
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "X-Drone-ID": target},
        )
