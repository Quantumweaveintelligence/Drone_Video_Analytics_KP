"""
src/video/stream_viewer.py
===========================
StreamViewer — live OpenCV display window with HUD overlay.

Runs on the MAIN thread (OpenCV imshow must be called from the thread
that called namedWindow / waitKey).  The main loop in main.py drives it
by calling  viewer.tick()  once per iteration.

Smoothness fix (4K source)
--------------------------
A dedicated background preprocessing thread reads raw frames from the
FrameInterpolator, GPU-resizes them to the display resolution (default
1280×720) using CUDA if available or CPU INTER_AREA, then drops the
result into a bounded display queue.  The main thread only ever pops
an already-downscaled frame and draws the HUD — all expensive operations
(resize, copy) are off the main thread.

HUD layout  (all text tuned for 1280×720 display)
--------------------------------------------------
Top-left     : Drone ID · source type · resolution · decoder
Top-right    : EFF fps · SRC fps · decode latency
Centre-top   : GPU util · VRAM · temp
Bottom-left  : UTC timestamp · frame index
Bottom-right : Buffer fill · LIVE / NO SIGNAL badge
Right-centre : CHANGE DETECTION panel (score, EPM, total)
Overlay      : Motion bounding boxes · MOTION DETECTED badge (pulsing)
"""

from __future__ import annotations

import datetime
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

import cv2
import numpy as np

from src.utils.gpu_utils import query_gpu_stats
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.buffer.rolling_frame_buffer import RollingFrameBuffer
    from src.change_detection.change_event import ChangeEvent
    from src.change_detection.change_detection_engine import ChangeDetectionEngine
    from src.video.drone_video_source import DroneVideoSource, VideoFrame
    from src.video.frame_interpolator import FrameInterpolator

log = get_logger(__name__)

# ── Display resolution (all HUD work happens at this resolution) ─────────────
_DISPLAY_W = 1280
_DISPLAY_H = 720

# ── Try CUDA GPU resize (requires OpenCV built with CUDA) ────────────────────
try:
    _test = cv2.cuda_GpuMat()
    _test.upload(np.zeros((2, 2, 3), dtype=np.uint8))
    cv2.cuda.resize(_test, (2, 2))
    _OPENCV_CUDA = True
    log.info("stream_viewer_gpu_resize", backend="OpenCV CUDA")
except Exception:
    _OPENCV_CUDA = False

# ── Try CuPy for GPU-side resize (fallback) ──────────────────────────────────
try:
    import cupy as _cp          # noqa: F401
    _CUPY_AVAILABLE = True
except ImportError:
    _CUPY_AVAILABLE = False

# ── Colour palette (BGR) ─────────────────────────────────────────────────────
_GREEN  = (0,   220,  80)
_RED    = (0,    40, 220)
_YELLOW = (0,   200, 220)
_WHITE  = (255, 255, 255)
_BLACK  = (0,     0,   0)
_PANEL  = (18,   18,  24)
_ACCENT = (60,  180, 255)

# ── HUD geometry (tuned for 1280×720 display) ────────────────────────────────
_PAD  = 14    # outer padding px
_LH   = 32    # line height px
_SC   = 0.78  # body text scale  (was 0.52 — doubled for 4K sources)
_SCB  = 1.00  # bold/title scale (was 0.62)
_SCS  = 0.65  # small/hint scale


# ── Drawing helpers ──────────────────────────────────────────────────────────

def _put(img: np.ndarray, text: str, x: int, y: int,
         color=_WHITE, scale: float = _SC, thickness: int = 1) -> None:
    """Draw text with a drop-shadow for legibility on any background."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(img, text, (x + 1, y + 1), font, scale, _BLACK, thickness + 1, cv2.LINE_AA)
    cv2.putText(img, text, (x,     y    ), font, scale, color,  thickness,     cv2.LINE_AA)


def _text_size(text: str, scale: float = _SC, thickness: int = 1):
    (w, h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    return w, h


def _draw_panel(img: np.ndarray, x1: int, y1: int, x2: int, y2: int,
                alpha: float = 0.60) -> None:
    """Semi-transparent dark panel."""
    x1 = max(0, x1); y1 = max(0, y1)
    x2 = min(img.shape[1] - 1, x2); y2 = min(img.shape[0] - 1, y2)
    if x2 <= x1 or y2 <= y1:
        return
    overlay = img.copy()
    cv2.rectangle(overlay, (x1, y1), (x2, y2), _PANEL, -1)
    cv2.addWeighted(overlay, alpha, img, 1 - alpha, 0, img)


# ── Pre-processed display frame ───────────────────────────────────────────────

@dataclass
class _DisplayFrame:
    """A frame already resized to display resolution, plus source metadata."""
    frame: np.ndarray          # HxW=720x1280 BGR uint8
    frame_index: int
    utc_timestamp_ms: int
    src_w: int                 # original source width (for bbox scaling)
    src_h: int                 # original source height
    decode_latency_ms: float


# ── StreamViewer ──────────────────────────────────────────────────────────────

class StreamViewer:
    """
    Live drone video viewer with HUD overlay.

    Architecture
    ~~~~~~~~~~~~
    • _preprocess_thread:  reads from FrameInterpolator → GPU/CPU resize to
      1280×720 → pushes _DisplayFrame into _display_queue (maxsize=3).
    • Main thread tick():  pops _DisplayFrame (non-blocking) → draws HUD on
      already-downscaled frame → cv2.imshow().

    All slow operations (frame copy, 4K→720p resize, blending) are off the
    main thread so imshow() is never starved.

    Parameters
    ----------
    source : DroneVideoSource | DroneSimulationSource
        Used for connection state, metadata, and W-key source switch.
    buffer : Optional[RollingFrameBuffer]
        Buffer fill statistics for the HUD.
    buffer_capacity : int
        Max frames the buffer holds (for fill % calculation).
    interpolator : Optional[FrameInterpolator]
        If provided, the preprocess thread reads from it instead of source.
    change_engine : Optional[ChangeDetectionEngine]
        Change detection engine — overlay bboxes and MOTION DETECTED badge.
    display_w / display_h : int
        Target display resolution. Default 1280×720.
    """

    WINDOW = "Kerala Police — Drone Analytics"

    def __init__(
        self,
        source: "DroneVideoSource",
        buffer: Optional["RollingFrameBuffer"] = None,
        buffer_capacity: int = 2200,
        interpolator: Optional["FrameInterpolator"] = None,
        change_engine: Optional["ChangeDetectionEngine"] = None,
        display_w: int = _DISPLAY_W,
        display_h: int = _DISPLAY_H,
        scale: float = 1.0,         # kept for API compat — use display_w/h instead
    ) -> None:
        self._source          = source
        self._buffer          = buffer
        self._buffer_capacity = buffer_capacity
        self._interpolator    = interpolator
        self._change_engine   = change_engine
        self._display_w       = display_w
        self._display_h       = display_h

        # ── Display frame queue (preprocess thread → main thread) ─────────
        self._display_queue: queue.Queue[_DisplayFrame] = queue.Queue(maxsize=3)

        # ── Preprocess thread ─────────────────────────────────────────────
        self._stop_event      = threading.Event()
        self._preprocess_thread: Optional[threading.Thread] = None

        # ── Rolling display FPS ───────────────────────────────────────────
        self._fps_frame_count: int    = 0
        self._fps_window_start: float = time.monotonic()
        self._fps_display: float      = 0.0

        # ── Latest display frame ──────────────────────────────────────────
        self._last_dframe: Optional[_DisplayFrame] = None

        # ── Toast notification ────────────────────────────────────────────
        self._toast_text:  str   = ""
        self._toast_until: float = 0.0

        # ── Change detection overlay ──────────────────────────────────────
        self._last_change_event: Optional["ChangeEvent"] = None
        self._change_event_until: float = 0.0
        self._change_overlay_duration_s: float = 2.5

        self._stopped = False

        # ── Create OpenCV window ──────────────────────────────────────────
        cv2.namedWindow(self.WINDOW, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self.WINDOW, display_w, display_h)

        log.info(
            "stream_viewer_created",
            display=f"{display_w}x{display_h}",
            opencv_cuda=_OPENCV_CUDA,
            cupy=_CUPY_AVAILABLE,
        )

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background preprocessing thread."""
        self._stop_event.clear()
        self._preprocess_thread = threading.Thread(
            target=self._preprocess_worker,
            name="ViewerPreprocess",
            daemon=True,
        )
        self._preprocess_thread.start()
        log.info("stream_viewer_preprocess_thread_started")

    def stop(self) -> None:
        if not self._stopped:
            self._stopped = True
            self._stop_event.set()
            if self._preprocess_thread is not None:
                self._preprocess_thread.join(timeout=3.0)
            try:
                cv2.destroyWindow(self.WINDOW)
            except Exception:
                pass
            log.info("stream_viewer_stopped")

    # ── Background preprocessing thread ─────────────────────────────────────

    def _resize_to_display(self, frame: np.ndarray) -> np.ndarray:
        """
        Resize a source frame to display resolution.
        Uses OpenCV CUDA if available, falls back to CPU INTER_AREA.
        INTER_AREA gives the best quality for downscaling.
        """
        src_h, src_w = frame.shape[:2]
        if src_w == self._display_w and src_h == self._display_h:
            return frame

        if _OPENCV_CUDA:
            try:
                gpu_mat = cv2.cuda_GpuMat()
                gpu_mat.upload(frame)
                gpu_out = cv2.cuda.resize(
                    gpu_mat,
                    (self._display_w, self._display_h),
                    interpolation=cv2.INTER_LINEAR,
                )
                return gpu_out.download()
            except Exception:
                pass

        # CPU fallback — INTER_AREA is highest quality for downscaling
        return cv2.resize(
            frame,
            (self._display_w, self._display_h),
            interpolation=cv2.INTER_AREA,
        )

    def _preprocess_worker(self) -> None:
        """
        Background thread:
        1. Pull VideoFrame from FrameInterpolator (or source directly).
        2. Resize to display resolution (GPU or CPU).
        3. Wrap in _DisplayFrame and push to display queue.

        This runs entirely off the main thread so the main thread never
        blocks on a slow resize/copy of a 4K frame.
        """
        log.info("preprocess_worker_started")
        while not self._stop_event.is_set():
            try:
                if self._interpolator is not None:
                    vf = self._interpolator.read(timeout=0.04)
                else:
                    vf = self._source.read(timeout=0.04)

                if vf is None:
                    continue

                src_h, src_w = vf.frame.shape[:2]

                # GPU/CPU resize to display resolution
                display_frame = self._resize_to_display(vf.frame)

                df = _DisplayFrame(
                    frame=display_frame,
                    frame_index=vf.frame_index,
                    utc_timestamp_ms=vf.utc_timestamp_ms,
                    src_w=vf.width,
                    src_h=vf.height,
                    decode_latency_ms=vf.decode_latency_ms,
                )

                # Non-blocking put: drop oldest if full
                try:
                    self._display_queue.put_nowait(df)
                except queue.Full:
                    try:
                        self._display_queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        self._display_queue.put_nowait(df)
                    except queue.Full:
                        pass

            except Exception as exc:
                if not self._stop_event.is_set():
                    log.error("preprocess_worker_error", error=str(exc))

        log.info("preprocess_worker_exited")

    # ── Main thread tick ─────────────────────────────────────────────────────

    def tick(self, wait_ms: int = 1) -> bool:
        """
        Call once per main-loop iteration (main thread only).

        Pops a pre-downscaled frame from the display queue (non-blocking),
        draws the HUD at display resolution, calls imshow.
        Returns False if the user closed the window.
        """
        if self._stopped:
            return False

        # ── Pull pre-scaled display frame (non-blocking) ─────────────────
        try:
            df = self._display_queue.get_nowait()
            self._last_dframe = df
            self._fps_frame_count += 1
            elapsed = time.monotonic() - self._fps_window_start
            if elapsed >= 1.0:
                self._fps_display = self._fps_frame_count / elapsed
                self._fps_frame_count = 0
                self._fps_window_start = time.monotonic()
        except queue.Empty:
            pass  # use last cached frame

        # ── Pull latest change event ──────────────────────────────────────
        if self._change_engine is not None:
            try:
                evt = self._change_engine.event_display_queue.get_nowait()
                self._last_change_event = evt
                self._change_event_until = time.monotonic() + self._change_overlay_duration_s
            except queue.Empty:
                pass

        # ── Build display frame ───────────────────────────────────────────
        if self._last_dframe is not None:
            display = self._last_dframe.frame.copy()
        else:
            display = self._no_signal_frame()

        # ── Draw HUD (all at display resolution — fast) ───────────────────
        self._draw_hud(display)

        cv2.imshow(self.WINDOW, display)

        # ── Key handling ──────────────────────────────────────────────────
        key = cv2.waitKey(wait_ms) & 0xFF
        if key == 27:
            log.info("stream_viewer_esc_pressed")
            self.stop()
            return False

        if key in (ord('w'), ord('W')):
            current = getattr(self._source, "active_source_type", "rtsp")
            next_src = "webcam" if current == "rtsp" else "rtsp"
            self._source.switch_source(next_src)
            label = "WEBCAM (laptop)" if next_src == "webcam" else "RTSP (drone)"
            self._toast_text  = f"Switching to {label}  [W]"
            self._toast_until = time.monotonic() + 2.5
            log.info("source_switch_hotkey", new_source=next_src)

        # ── Detect window close ───────────────────────────────────────────
        try:
            if cv2.getWindowProperty(self.WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                log.info("stream_viewer_window_closed")
                self.stop()
                return False
        except Exception:
            pass

        return True

    # ── HUD drawing (all coordinates are in 1280×720 display space) ─────────

    def _draw_hud(self, img: np.ndarray) -> None:
        h, w = img.shape[:2]
        connected = self._source.is_connected
        df        = self._last_dframe

        # ── Buffer stats ──────────────────────────────────────────────────
        buf_frames: int = 0
        buf_window_s: float = 0.0
        if self._buffer is not None:
            try:
                buf_frames  = self._buffer.get_frame_count()
                oldest = self._buffer.get_oldest_timestamp_ms()
                newest = self._buffer.get_newest_timestamp_ms()
                if oldest is not None and newest is not None:
                    buf_window_s = (newest - oldest) / 1000.0
            except Exception:
                pass

        # ── TOP-LEFT panel ────────────────────────────────────────────────
        drone_id   = getattr(self._source, "drone_id", "drone_01")
        active_src = getattr(self._source, "active_source_type", "rtsp").upper()
        src_badge_col = (_ACCENT if active_src == "RTSP"
                         else (255, 80, 220) if active_src == "WEBCAM"
                         else _YELLOW)
        res_str    = (f"{df.src_w}x{df.src_h}" if df else "-- x --")
        _cuvid     = getattr(self._source, "_using_cuvid", False)
        _cuda_pp   = getattr(self._source, "_using_cuda_processing", False)
        if _cuvid:
            decoder, dec_col = "NVDEC", _ACCENT
        elif _cuda_pp:
            decoder, dec_col = "CUDA", _ACCENT
        else:
            decoder, dec_col = "CPU",  _YELLOW

        tl_lines = [
            f"  {drone_id}",
            f"  Source  : {active_src}",
            f"  Res     : {res_str}",
            f"  Decoder : {decoder}",
            f"  [W] Toggle source",
        ]
        tl_w = max(_text_size(l, _SC)[0] for l in tl_lines) + _PAD * 2
        tl_h = _LH * len(tl_lines) + _PAD
        _draw_panel(img, _PAD, _PAD, _PAD + tl_w, _PAD + tl_h)
        colors = [_WHITE, src_badge_col, _WHITE, dec_col, (110, 110, 110)]
        for i, (line, col) in enumerate(zip(tl_lines, colors)):
            _put(img, line, _PAD, _PAD + _LH * (i + 1), col, _SC)

        # ── Toast ─────────────────────────────────────────────────────────
        if self._toast_text and time.monotonic() < self._toast_until:
            tw, th = _text_size(self._toast_text, 1.0, 2)
            tx = (w - tw) // 2
            ty = h // 2
            _draw_panel(img, tx - _PAD, ty - th - _PAD, tx + tw + _PAD, ty + _PAD, 0.80)
            _put(img, self._toast_text, tx, ty, _YELLOW, 1.0, 2)

        # ── TOP-RIGHT panel ───────────────────────────────────────────────
        latency  = df.decode_latency_ms if df else 0.0
        src_fps  = getattr(self._source, "source_fps", 0.0)
        eff_fps  = (self._interpolator.effective_fps
                    if self._interpolator is not None else self._fps_display)
        fps_col  = _GREEN if eff_fps >= 20 else _YELLOW if eff_fps >= 12 else _RED
        src_col  = _GREEN if src_fps >= 15 else _YELLOW if src_fps >= 8  else _RED
        tr_lines = [
            f"  EFF : {eff_fps:5.1f} fps  ",
            f"  SRC : {src_fps:5.1f} fps  ",
            f"  LAT : {latency:5.1f} ms   ",
        ]
        tr_w  = max(_text_size(l, _SCB)[0] for l in tr_lines) + _PAD * 2
        tr_h  = _LH * len(tr_lines) + _PAD
        tr_x1 = w - tr_w - _PAD
        _draw_panel(img, tr_x1, _PAD, w - _PAD, _PAD + tr_h)
        _put(img, tr_lines[0], tr_x1, _PAD + _LH,     fps_col, _SCB)
        _put(img, tr_lines[1], tr_x1, _PAD + _LH * 2, src_col, _SC)
        _put(img, tr_lines[2], tr_x1, _PAD + _LH * 3, _WHITE,  _SC)

        # ── CENTRE-TOP GPU panel ──────────────────────────────────────────
        gpu = query_gpu_stats()
        if gpu is not None:
            util_col = (_GREEN  if gpu.gpu_util_pct < 70
                        else _YELLOW if gpu.gpu_util_pct < 90 else _RED)
            temp_col = (_GREEN  if gpu.temp_c < 70
                        else _YELLOW if gpu.temp_c < 85       else _RED)
            vram_pct = int(gpu.vram_used_mb / max(gpu.vram_total_mb, 1) * 100)
            dec_badge = ("[NVDEC ON]" if _cuvid
                         else "[CUDA ON]"  if _cuda_pp
                         else "[CPU]")
            dec_col2  = _ACCENT if (_cuvid or _cuda_pp) else _YELLOW
            gpu_lines = [
                f"  GPU : {gpu.gpu_util_pct:3d}%",
                f"  VRAM: {gpu.vram_used_mb}/{gpu.vram_total_mb} MB ({vram_pct}%)",
                f"  Temp: {gpu.temp_c}C   {dec_badge}",
            ]
            gw  = max(_text_size(l, _SC)[0] for l in gpu_lines) + _PAD * 2
            gh  = _LH * len(gpu_lines) + _PAD
            gx  = (w - gw) // 2
            _draw_panel(img, gx, _PAD, gx + gw, _PAD + gh)
            _put(img, gpu_lines[0], gx, _PAD + _LH,     util_col, _SC)
            _put(img, gpu_lines[1], gx, _PAD + _LH * 2, _WHITE,   _SC)
            _put(img, gpu_lines[2], gx, _PAD + _LH * 3, dec_col2, _SC)
        else:
            dec_badge = "NVDEC [h264_cuvid]" if _cuvid else "SW decode (CPU)"
            dec_col2  = _ACCENT if _cuvid else _YELLOW
            badge_w   = _text_size(f"  {dec_badge}  ", _SC)[0] + _PAD * 2
            gx = (w - badge_w) // 2
            _draw_panel(img, gx, _PAD, gx + badge_w, _PAD + _LH + _PAD)
            _put(img, f"  {dec_badge}  ", gx, _PAD + _LH, dec_col2, _SC)

        # ── BOTTOM-LEFT panel ─────────────────────────────────────────────
        frame_idx = df.frame_index if df else 0
        utc_ms    = df.utc_timestamp_ms if df else None
        if utc_ms:
            ts_str = datetime.datetime.utcfromtimestamp(utc_ms / 1000).strftime(
                "%Y-%m-%d  %H:%M:%S UTC"
            )
        else:
            ts_str = datetime.datetime.utcnow().strftime("%Y-%m-%d  %H:%M:%S UTC")
        bl_lines = [f"  {ts_str}", f"  Frame : #{frame_idx:,}"]
        bl_w = max(_text_size(l, _SC)[0] for l in bl_lines) + _PAD * 2
        bl_h = _LH * len(bl_lines) + _PAD
        _draw_panel(img, _PAD, h - bl_h - _PAD, _PAD + bl_w, h - _PAD)
        for i, line in enumerate(bl_lines):
            _put(img, line, _PAD, h - bl_h - _PAD + _LH * (i + 1), _ACCENT, _SC)

        # ── BOTTOM-RIGHT status badge ─────────────────────────────────────
        buf_pct    = int(buf_frames / max(self._buffer_capacity, 1) * 100)
        buf_str    = f"  BUF : {buf_frames:,} fr  ({buf_pct}%  {buf_window_s:.0f}s)"
        status_str = "  LIVE" if (connected and df is not None) else "  NO SIGNAL"
        status_col = _GREEN   if (connected and df is not None) else _RED
        br_lines   = [buf_str, status_str]
        br_w  = max(_text_size(l, _SC)[0] for l in br_lines) + _PAD * 2
        br_h  = _LH * len(br_lines) + _PAD
        br_x1 = w - br_w - _PAD
        _draw_panel(img, br_x1, h - br_h - _PAD, w - _PAD, h - _PAD)
        _put(img, buf_str,    br_x1, h - br_h - _PAD + _LH,     _WHITE,     _SC)
        _put(img, status_str, br_x1, h - br_h - _PAD + _LH * 2, status_col, _SCB)

        # ── Frame border ──────────────────────────────────────────────────
        border_col = _GREEN if connected else _RED
        cv2.rectangle(img, (0, 0), (w - 1, h - 1), border_col, 3)

        # ── Change detection overlay ──────────────────────────────────────
        self._draw_change_overlay(img)

    # ── Change detection overlay ──────────────────────────────────────────────

    def _draw_change_overlay(self, img: np.ndarray) -> None:
        """Draw the change detection stats panel, bounding boxes, and MOTION badge."""
        h, w = img.shape[:2]
        now  = time.monotonic()

        # ── CHANGE DETECTION stats panel (right side, always visible) ─────
        if self._change_engine is not None:
            stats        = self._change_engine.get_stats()
            motion_score = stats.get("last_motion_score", 0.0)
            epm          = stats.get("events_per_minute", 0.0)
            events_total = stats.get("events_total", 0)
            q_depth      = stats.get("queue_depth", 0)

            score_col = (_GREEN  if motion_score < 0.05
                         else _YELLOW if motion_score < 0.15 else _RED)
            cd_lines = [
                "  CHANGE DETECTION",
                f"  Score : {motion_score:.4f}",
                f"  EPM   : {epm:.1f}",
                f"  Total : {events_total:,}",
                f"  Queue : {q_depth}",
            ]
            cd_w  = max(_text_size(l, _SC)[0] for l in cd_lines) + _PAD * 2
            cd_h  = _LH * len(cd_lines) + _PAD
            cd_x1 = w - cd_w - _PAD
            cd_y1 = h // 2 - cd_h // 2
            _draw_panel(img, cd_x1, cd_y1, w - _PAD, cd_y1 + cd_h)
            _put(img, cd_lines[0], cd_x1, cd_y1 + _LH,       _ACCENT,   _SCB)
            _put(img, cd_lines[1], cd_x1, cd_y1 + _LH * 2,   score_col, _SC)
            _put(img, cd_lines[2], cd_x1, cd_y1 + _LH * 3,   _WHITE,    _SC)
            _put(img, cd_lines[3], cd_x1, cd_y1 + _LH * 4,   _WHITE,    _SC)
            _put(img, cd_lines[4], cd_x1, cd_y1 + _LH * 5,   _YELLOW,   _SCS)

        # ── Motion bounding boxes + MOTION DETECTED badge ─────────────────
        if self._last_change_event is not None and now < self._change_event_until:
            evt = self._last_change_event

            # Scale bbox coords from source resolution to display resolution
            src_w = self._last_dframe.src_w if self._last_dframe else w
            src_h = self._last_dframe.src_h if self._last_dframe else h
            sx = w / max(src_w, 1)
            sy = h / max(src_h, 1)

            for region in evt.regions:
                bx = int(region.x * sx)
                by = int(region.y * sy)
                bw = int(region.w * sx)
                bh = int(region.h * sy)

                intensity = min(int(region.motion_magnitude * 1.5), 255)
                box_col   = (0, intensity, 255 - intensity // 2)

                # Glow border
                cv2.rectangle(img,
                               (bx - 3, by - 3), (bx + bw + 3, by + bh + 3),
                               (box_col[0]//4, box_col[1]//4, box_col[2]//4), 4)
                # Main box
                cv2.rectangle(img, (bx, by), (bx + bw, by + bh), box_col, 2)
                # Persistence label
                _put(img, f"P:{region.persistence}", bx + 3, by + 20, box_col, 0.55)

            # ── MOTION DETECTED badge (pulsing) ────────────────────────────
            elapsed_since_event = now - (self._change_event_until - self._change_overlay_duration_s)
            pulse = 0.5 + 0.5 * abs(np.sin(elapsed_since_event * 4))

            badge_text  = "  MOTION DETECTED  "
            badge_scale = 1.3
            bw_txt, bh_txt = _text_size(badge_text, badge_scale, 2)
            bx_badge = (w - bw_txt) // 2
            by_badge = int(h * 0.10)

            overlay = img.copy()
            cv2.rectangle(
                overlay,
                (bx_badge - _PAD, by_badge - bh_txt - _PAD),
                (bx_badge + bw_txt + _PAD, by_badge + _PAD),
                (0, 20, 180), -1,
            )
            cv2.addWeighted(overlay, 0.55 + 0.35 * pulse, img, 1 - (0.55 + 0.35 * pulse), 0, img)

            badge_col = (
                int(255 * pulse),
                int(60 * pulse),
                int(60 * (1 - pulse)),
            )
            _put(img, badge_text, bx_badge, by_badge, badge_col, badge_scale, 2)

            # Score / region count sub-line
            score_str = (
                f"  score={evt.motion_score:.4f}   "
                f"regions={evt.region_count}   "
                f"frame=#{evt.frame_index:,}"
            )
            sx_txt = (w - _text_size(score_str, _SCS)[0]) // 2
            _put(img, score_str, sx_txt, by_badge + _LH, _YELLOW, _SCS)

    # ── No-signal placeholder ─────────────────────────────────────────────────

    def _no_signal_frame(self) -> np.ndarray:
        """Dark placeholder while waiting for the first frame."""
        canvas = np.full((_DISPLAY_H, _DISPLAY_W, 3), 15, dtype=np.uint8)
        t    = time.monotonic()
        dots = "." * (int(t * 2) % 4)

        active_src = getattr(self._source, "active_source_type", "rtsp")
        if active_src == "simulation":
            msg = f"Loading video files{dots}"
            sub = "Drone Feed Simulation Mode"
        elif active_src == "webcam":
            msg = f"Opening webcam{dots}"
            sub = "Press  W  to switch back to RTSP"
        else:
            msg = f"Connecting to RTSP stream{dots}"
            sub = getattr(self._source, "rtsp_url", "")

        _put(canvas, msg, 280, 330, _YELLOW, 1.1, 2)
        _put(canvas, sub, 340, 375, _ACCENT, 0.72, 1)
        _put(canvas, "Press  [W]  to toggle source  |  Esc to quit",
             260, 420, (100, 100, 100), 0.60, 1)

        # Animated spinner
        cx, cy, r = 640, 230, 55
        angle = (t * 180) % 360
        for i in range(12):
            a   = np.radians(angle + i * 30)
            val = int(255 * (i / 12))
            col = (val // 3, val // 2, val)
            px  = int(cx + r * np.cos(a))
            py  = int(cy + r * np.sin(a))
            cv2.circle(canvas, (px, py), 6, col, -1)

        cv2.rectangle(canvas, (0, 0), (_DISPLAY_W - 1, _DISPLAY_H - 1), _RED, 3)
        return canvas
