"""
src/video/drone_video_source.py
================================
Task 1.1 — Drone Video Input Abstraction
-----------------------------------------
DroneVideoSource accepts:
  • RTSP stream URL  (primary — most drones use RTSP)
  • USB / HDMI capture-card index  (fallback)
  • Webcam device index  (local laptop/USB camera for testing)

Design decisions:
  • Uses cv2.VideoCapture with explicit RTSP transport hint and optional
    GStreamer pipeline for low-latency hardware decode.
  • A background reader thread calls cap.read() continuously so the main
    consumer never blocks waiting for network I/O.
  • Reconnection uses exponential back-off (base × multiplier^attempt,
    capped at max_delay).  The thread never exits — it will keep retrying
    until stop() is called.
  • Frame delivery: producer puts frames into a bounded queue
    (maxsize=2 so consumers always get the *latest* frame, not a stale one).
  • Heartbeat metrics (FPS, resolution, decode latency) are published to
    Prometheus every <heartbeat_interval> frames.
  • Runtime source switching: call switch_source("rtsp") or
    switch_source("webcam") at any time to swap the active video input
    without restarting the pipeline.  Thread-safe via _switch_event.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import cv2
import numpy as np

from src.metrics.prometheus_metrics import (
    DRONE_DECODE_LATENCY,
    DRONE_FPS,
    DRONE_FRAME_HEIGHT,
    DRONE_FRAME_WIDTH,
    DRONE_FRAMES_TOTAL,
    DRONE_RECONNECT_TOTAL,
    DRONE_STREAM_UP,
)
from src.utils.gpu_utils import GPUInfo, detect_gpu
from src.utils.logger import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public data type delivered to consumers
# ---------------------------------------------------------------------------

@dataclass
class VideoFrame:
    """A single decoded video frame with timing metadata."""

    frame_index: int                    # monotonically increasing frame counter
    utc_timestamp_ms: int               # wall-clock UTC ms when frame was decoded
    frame: np.ndarray                   # BGR image array (H × W × 3, uint8)
    width: int
    height: int
    decode_latency_ms: float            # time from read() call → frame available


# ---------------------------------------------------------------------------
# DroneVideoSource
# ---------------------------------------------------------------------------

class DroneVideoSource:
    """
    Abstraction over RTSP / USB video sources with automatic reconnection
    and Prometheus heartbeat metrics.

    Parameters
    ----------
    drone_id : str
        Logical identifier for this drone (used as a Prometheus label).
    rtsp_url : str
        Full RTSP URL, e.g. ``rtsp://admin:pass@192.168.1.10:554/h264/...``
    usb_device_index : int
        OpenCV device index for USB/HDMI fallback (0 = first camera).
    preferred_source : str
        ``"rtsp"`` or ``"usb"``.  If the preferred source fails to open,
        DroneVideoSource automatically tries the other.
    rtsp_transport : str
        ``"tcp"`` (reliable, higher latency) or ``"udp"`` (lower latency,
        may drop packets).  Passed as ``OPENCV_FFMPEG_CAPTURE_OPTIONS``.
    gstreamer_pipeline : str
        Optional fully-formed GStreamer pipeline string.  When non-empty,
        this is used *instead* of the RTSP URL, allowing GPU-accelerated
        decode pipelines such as ``nvdec`` or ``vaapi``.
        Example for NVIDIA (requires gstreamer-plugins-bad):
          ``"rtspsrc location=rtsp://... ! rtph264depay ! nvh264dec
             ! videoconvert ! appsink"``
    reconnect_base_delay_s : float
        Initial reconnection delay in seconds.
    reconnect_multiplier : float
        Exponential back-off multiplier.
    reconnect_max_delay_s : float
        Upper bound on reconnection delay.
    read_timeout_s : float
        If no valid frame arrives within this window, trigger a reconnect.
    frame_queue_size : int
        Internal queue depth.  Keep at 1–2 so consumers always get the
        latest frame without build-up.
    heartbeat_interval_frames : int
        How often to compute and publish FPS / resolution metrics.
    on_frame : Optional[Callable[[VideoFrame], None]]
        Optional callback invoked (in the reader thread) for every decoded
        frame.  Use this to push frames directly into a RollingFrameBuffer.
    """

    def __init__(
        self,
        drone_id: str = "drone_01",
        rtsp_url: str = "rtsp://admin:Admin12345@10.10.32.23:554/h264/ch1/main/av_stream",
        usb_device_index: int = 0,
        webcam_device_index: int = 0,
        preferred_source: str = "rtsp",
        rtsp_only: bool = True,
        use_gpu: bool = True,
        rtsp_transport: str = "tcp",
        gstreamer_pipeline: str = "",
        reconnect_base_delay_s: float = 0.5,
        reconnect_multiplier: float = 1.5,
        reconnect_max_delay_s: float = 30.0,
        read_timeout_s: float = 8.0,
        frame_queue_size: int = 8,       # larger queue feeds the interpolator
        heartbeat_interval_frames: int = 30,
        on_frame: Optional[Callable[["VideoFrame"], None]] = None,
    ) -> None:
        self.drone_id = drone_id
        self.rtsp_url = rtsp_url
        self.usb_device_index = usb_device_index
        self.webcam_device_index = webcam_device_index
        self.preferred_source = preferred_source
        self.rtsp_only = rtsp_only
        self.use_gpu = use_gpu
        self.rtsp_transport = rtsp_transport
        self.gstreamer_pipeline = gstreamer_pipeline
        self.reconnect_base_delay_s = reconnect_base_delay_s
        self.reconnect_multiplier = reconnect_multiplier
        self.reconnect_max_delay_s = reconnect_max_delay_s
        self.read_timeout_s = read_timeout_s
        self.heartbeat_interval_frames = heartbeat_interval_frames
        self.on_frame = on_frame

        # GPU capability detection (runs once, cached)
        self._gpu_info: GPUInfo = detect_gpu()
        self._using_cuvid: bool = False            # FFMPEG NVDEC hardware decode (RTSP)
        self._using_cuda_processing: bool = False  # cv2.cuda post-process (webcam/USB)

        # ── Runtime source switching ──────────────────────────────────────────
        # active_source_type: "rtsp" or "webcam" — updated by reader thread
        self.active_source_type: str = "webcam" if preferred_source == "webcam" else "rtsp"
        self._requested_source: str = self.active_source_type   # what switch_source() sets
        self._switch_event = threading.Event()  # signals reader loop to swap source

        # Internal state
        self._cap: Optional[cv2.VideoCapture] = None
        self._frame_queue: queue.Queue[VideoFrame] = queue.Queue(maxsize=frame_queue_size)
        self._write_queue: queue.Queue[VideoFrame] = queue.Queue(maxsize=frame_queue_size * 4)
        self._stop_event = threading.Event()
        self._reader_thread: Optional[threading.Thread] = None
        self._write_thread: Optional[threading.Thread] = None

        # Counters (thread-safe — only written by reader thread)
        self._frame_index: int = 0
        self._fps_frame_count: int = 0
        self._fps_window_start: float = time.monotonic()

        # Expose stream-up state for external inspection
        self.is_connected: bool = False
        self.source_fps: float = 0.0   # rolling measured FPS from reader thread

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def switch_source(self, source_type: str) -> None:
        """
        Atomically request a live switch between ``"rtsp"`` and ``"webcam"``.

        Safe to call from any thread (including the main/UI thread).  The
        reader loop picks up the request at the next iteration, releases the
        current capture, and opens the new one — usually within ~1 frame.

        Parameters
        ----------
        source_type : str
            ``"rtsp"`` — reconnect to the configured RTSP URL.
            ``"webcam"`` — open the local webcam (webcam_device_index).
        """
        if source_type not in ("rtsp", "webcam"):
            raise ValueError(f"source_type must be 'rtsp' or 'webcam', got {source_type!r}")
        if source_type == self.active_source_type and not self._switch_event.is_set():
            log.debug("switch_source_noop", requested=source_type)
            return
        log.info(
            "switch_source_requested",
            drone_id=self.drone_id,
            from_source=self.active_source_type,
            to_source=source_type,
        )
        self._requested_source = source_type
        self._switch_event.set()

    def start(self) -> None:
        """Start the background reader + async buffer-writer threads."""
        if self._reader_thread is not None and self._reader_thread.is_alive():
            log.warning("already_running", drone_id=self.drone_id)
            return

        self._stop_event.clear()

        # Async buffer writer — keeps reader thread free for cap.read()
        self._write_thread = threading.Thread(
            target=self._write_worker,
            name=f"DroneWriter-{self.drone_id}",
            daemon=True,
        )
        self._write_thread.start()

        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"DroneReader-{self.drone_id}",
            daemon=True,
        )
        self._reader_thread.start()
        log.info("reader_thread_started", drone_id=self.drone_id)

    def stop(self) -> None:
        """Signal all threads to stop and wait for them to finish."""
        self._stop_event.set()
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=10.0)
        if self._write_thread is not None:
            try:
                self._write_queue.put_nowait(None)  # sentinel to unblock writer
            except queue.Full:
                pass
            self._write_thread.join(timeout=5.0)
        self._release_cap()
        DRONE_STREAM_UP.labels(drone_id=self.drone_id).set(0)
        log.info("reader_thread_stopped", drone_id=self.drone_id)

    def read(self, timeout: float = 1.0) -> Optional[VideoFrame]:
        """
        Blocking read with timeout.

        Returns the next available VideoFrame, or None if no frame arrives
        within *timeout* seconds.  Intended for single-consumer use; for
        multi-consumer patterns prefer the on_frame callback.
        """
        try:
            return self._frame_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_capture(self, force_source: Optional[str] = None) -> Optional[cv2.VideoCapture]:
        """
        Try to open a cv2.VideoCapture from the configured source(s).

        Parameters
        ----------
        force_source : Optional[str]
            When set to ``"rtsp"`` or ``"webcam"``, skips the normal
            preference logic and opens only that source directly.  Used by
            the runtime switch path.

        When rtsp_only=True (default) and no force_source:
          • Only RTSP / GStreamer is attempted.
          • Returns None on failure so the reconnect loop retries with back-off.
          • The USB/webcam fallback is intentionally skipped.

        When rtsp_only=False:
          1. GStreamer pipeline (if configured)
          2. Preferred source (RTSP or USB)
          3. Fallback to the other source
        Returns an opened VideoCapture, or None on total failure.
        """
        # ── 0. Forced source (runtime switch) ─────────────────────────
        if force_source == "webcam":
            cap = self._open_source("webcam", self.webcam_device_index)
            if cap is None:
                log.warning(
                    "webcam_open_failed",
                    drone_id=self.drone_id,
                    device_index=self.webcam_device_index,
                )
            return cap

        if force_source == "rtsp":
            cap = self._open_source("rtsp", self.rtsp_url)
            if cap is None:
                log.warning(
                    "rtsp_connect_failed_after_switch",
                    drone_id=self.drone_id,
                    url=self._sanitise_url(self.rtsp_url),
                )
            return cap

        # ── 1. GStreamer custom pipeline ──────────────────────────────
        if self.gstreamer_pipeline:
            log.info("opening_gstreamer_pipeline", drone_id=self.drone_id)
            cap = cv2.VideoCapture(self.gstreamer_pipeline, cv2.CAP_GSTREAMER)
            if cap.isOpened():
                return cap
            log.warning("gstreamer_pipeline_failed", drone_id=self.drone_id)

        # ── 2. RTSP-only mode ─────────────────────────────────────────
        if self.rtsp_only:
            cap = self._open_source("rtsp", self.rtsp_url)
            if cap is None:
                log.error(
                    "rtsp_connect_failed_rtsp_only_mode",
                    drone_id=self.drone_id,
                    url=self._sanitise_url(self.rtsp_url),
                    hint="USB/webcam fallback is disabled (rtsp_only=True). "
                         "Will retry with exponential back-off.",
                )
            return cap

        # ── 3. Preferred + fallback (rtsp_only=False) ─────────────────
        sources = (
            [("rtsp", self.rtsp_url), ("usb", self.usb_device_index)]
            if self.preferred_source == "rtsp"
            else [("usb", self.usb_device_index), ("rtsp", self.rtsp_url)]
        )

        for source_type, source in sources:
            cap = self._open_source(source_type, source)
            if cap is not None:
                return cap

        return None

    def _open_source(self, source_type: str, source) -> Optional[cv2.VideoCapture]:
        """Open a single source; return VideoCapture or None."""
        try:
            cap: Optional[cv2.VideoCapture] = None

            if source_type == "rtsp":
                # ── GPU path: FFMPEG h264_cuvid (NVDEC hardware decode) ────────
                if self.use_gpu and self._gpu_info.cuvid_available:
                    cap = self._open_rtsp_cuvid(str(source))
                    if cap is None:
                        log.warning(
                            "cuvid_open_failed_falling_back_to_cpu",
                            drone_id=self.drone_id,
                            hint="NVDEC rejected the stream; retrying with software decode.",
                        )

                # ── CPU software decode (fallback or rtsp_only without GPU) ──
                if cap is None:
                    log.info(
                        "opening_rtsp_cpu",
                        drone_id=self.drone_id,
                        url=self._sanitise_url(str(source)),
                        transport=self.rtsp_transport,
                    )
                    cap = cv2.VideoCapture(
                        f"{source}?rtsp_transport={self.rtsp_transport}",
                        cv2.CAP_FFMPEG,
                    )
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    try:
                        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5_000)
                        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(self.read_timeout_s * 1_000))
                    except Exception:
                        pass
                    self._using_cuvid = False
                    self.active_source_type = "rtsp"

            elif source_type == "webcam":
                log.info(
                    "opening_webcam",
                    drone_id=self.drone_id,
                    device_index=source,
                )
                cap = cv2.VideoCapture(int(source), cv2.CAP_DSHOW)
                if not cap.isOpened():
                    # CAP_DSHOW failed, try default backend
                    cap.release()
                    cap = cv2.VideoCapture(int(source))
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                # Request a reasonable resolution so the pipeline doesn't choke
                cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT,  720)
                cap.set(cv2.CAP_PROP_FPS, 30)
                self._using_cuvid = False
                # Enable GPU post-processing if CuPy or OpenCV-CUDA is available
                self._using_cuda_processing = (
                    self._gpu_info.cupy_available or self._gpu_info.opencv_cuda
                )
                self.active_source_type = "webcam"

            else:  # usb (legacy path)
                log.info(
                    "opening_usb",
                    drone_id=self.drone_id,
                    device_index=source,
                )
                cap = cv2.VideoCapture(int(source), cv2.CAP_DSHOW)
                if not cap.isOpened():
                    cap.release()
                    cap = cv2.VideoCapture(int(source))
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                self._using_cuvid = False
                self._using_cuda_processing = (
                    self._gpu_info.cupy_available or self._gpu_info.opencv_cuda
                )
                self.active_source_type = "usb"

            if not cap.isOpened():
                log.warning(
                    "source_open_failed",
                    drone_id=self.drone_id,
                    source_type=source_type,
                )
                cap.release()
                return None

            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps_reported = cap.get(cv2.CAP_PROP_FPS)
            log.info(
                "source_opened",
                drone_id=self.drone_id,
                source_type=source_type,
                decoder="h264_cuvid (NVDEC)" if self._using_cuvid else "software (CPU)",
                width=w,
                height=h,
                reported_fps=fps_reported,
            )
            DRONE_FRAME_WIDTH.labels(drone_id=self.drone_id).set(w)
            DRONE_FRAME_HEIGHT.labels(drone_id=self.drone_id).set(h)
            DRONE_STREAM_UP.labels(drone_id=self.drone_id).set(1)
            self.is_connected = True
            return cap

        except Exception as exc:
            log.error(
                "source_open_exception",
                drone_id=self.drone_id,
                source_type=source_type,
                error=str(exc),
            )
            return None

    def _open_rtsp_cuvid(self, url: str) -> Optional[cv2.VideoCapture]:
        """
        Attempt to open an RTSP stream using FFMPEG's h264_cuvid NVDEC decoder.

        Sets the OPENCV_FFMPEG_CAPTURE_OPTIONS env variable (thread-unsafe but
        acceptable here since captures are only opened from the reader thread)
        then immediately restores the original value.
        """
        log.info(
            "opening_rtsp_cuvid",
            drone_id=self.drone_id,
            url=self._sanitise_url(url),
            transport=self.rtsp_transport,
            decoder="h264_cuvid",
        )
        opts = f"video_codec;h264_cuvid|rtsp_transport;{self.rtsp_transport}"
        prev = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS", "")
        try:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = opts
            cap = cv2.VideoCapture(
                f"{url}?rtsp_transport={self.rtsp_transport}",
                cv2.CAP_FFMPEG,
            )
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            try:
                cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8_000)
                cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(self.read_timeout_s * 1_000))
            except Exception:
                pass
        finally:
            # Always restore the previous env value
            if prev:
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = prev
            else:
                os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)

        if not cap.isOpened():
            cap.release()
            return None

        self._using_cuvid = True
        return cap

    def _gpu_process_frame(self, frame: np.ndarray) -> np.ndarray:
        """
        Apply GPU-accelerated post-processing to a raw webcam/USB frame.

        Priority order:
          1. CuPy (pip-installable, works with any standard CUDA install):
             Upload → resize on GPU → download.  Zero special OpenCV build needed.
          2. cv2.cuda (only if OpenCV was built with CUDA support — rare with pip):
             GpuMat upload → cuda::resize → download.
          3. CPU fallback: plain cv2.resize — still fast (uses SIMD/AVX).

        The HUD shows ``CUDA`` when path 1 or 2 is active, ``CPU`` otherwise.
        """
        if not self._using_cuda_processing:
            # CPU path — skip resize if frame is already the right size
            h, w = frame.shape[:2]
            if w != 1280 or h != 720:
                return cv2.resize(frame, (1280, 720), interpolation=cv2.INTER_LINEAR)
            return frame

        # ── Path 1: CuPy ──────────────────────────────────────────────────
        if self._gpu_info.cupy_available:
            try:
                import cupy as cp
                # Upload BGR frame to GPU as a CuPy array
                gpu_frame = cp.asarray(frame)
                h, w = frame.shape[:2]
                if w != 1280 or h != 720:
                    # Use zoom-based resize on GPU (CuPy ndimage)
                    from cupyx.scipy.ndimage import zoom  # type: ignore
                    fy = 720 / h
                    fx = 1280 / w
                    gpu_frame = zoom(gpu_frame, (fy, fx, 1.0), order=1)
                # Download back to CPU numpy array
                return cp.asnumpy(gpu_frame).astype(np.uint8)
            except Exception as exc:
                log.warning(
                    "cupy_process_frame_failed_falling_back",
                    drone_id=self.drone_id,
                    error=str(exc),
                )
                self._using_cuda_processing = False
                h, w = frame.shape[:2]
                if w != 1280 or h != 720:
                    return cv2.resize(frame, (1280, 720), interpolation=cv2.INTER_LINEAR)
                return frame

        # ── Path 2: cv2.cuda (custom CUDA OpenCV build) ─────────────────────
        if self._gpu_info.opencv_cuda:
            try:
                gpu_mat = cv2.cuda_GpuMat()
                gpu_mat.upload(frame)
                h, w = frame.shape[:2]
                if w != 1280 or h != 720:
                    gpu_mat = cv2.cuda.resize(
                        gpu_mat, (1280, 720),
                        interpolation=cv2.INTER_LINEAR,
                    )
                return gpu_mat.download()
            except Exception as exc:
                log.warning(
                    "cv2_cuda_process_frame_failed_falling_back",
                    drone_id=self.drone_id,
                    error=str(exc),
                )
                self._using_cuda_processing = False

        # ── Path 3: CPU fallback ──────────────────────────────────────────
        h, w = frame.shape[:2]
        if w != 1280 or h != 720:
            return cv2.resize(frame, (1280, 720), interpolation=cv2.INTER_LINEAR)
        return frame

    def _release_cap(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        self.is_connected = False
        DRONE_STREAM_UP.labels(drone_id=self.drone_id).set(0)

    # ------------------------------------------------------------------
    # Background reader loop
    # ------------------------------------------------------------------

    def _reader_loop(self) -> None:
        """
        Main loop running in the reader thread.

        Continuously:
          1. Opens the video source.
          2. Reads frames and publishes them.
          3. On failure, waits with exponential back-off and retries.
          4. When switch_source() is called, breaks the inner read loop
             and reopens with the requested source type.
        """
        reconnect_attempt = 0
        last_frame_time = time.monotonic()

        while not self._stop_event.is_set():
            # ── Check for a pending source switch ─────────────────────
            force = None
            if self._switch_event.is_set():
                self._switch_event.clear()
                force = self._requested_source
                log.info(
                    "switch_source_applying",
                    drone_id=self.drone_id,
                    to_source=force,
                )

            # ── Open capture ─────────────────────────────────────────
            self._cap = self._build_capture(force_source=force)

            if self._cap is None:
                delay = self._backoff_delay(reconnect_attempt)
                reconnect_attempt += 1
                DRONE_RECONNECT_TOTAL.labels(drone_id=self.drone_id).inc()
                log.warning(
                    "reconnect_scheduled",
                    drone_id=self.drone_id,
                    attempt=reconnect_attempt,
                    delay_s=round(delay, 1),
                )
                self._stop_event.wait(timeout=delay)
                continue

            reconnect_attempt = 0
            last_frame_time = time.monotonic()

            # ── Frame read loop ───────────────────────────────────────
            while not self._stop_event.is_set():
                # Break immediately to service a source switch request
                if self._switch_event.is_set():
                    log.info("switch_event_breaking_read_loop", drone_id=self.drone_id)
                    break

                t_read_start = time.monotonic()
                ret, frame = self._cap.read()
                t_read_end = time.monotonic()

                if not ret or frame is None:
                    elapsed_since_last = time.monotonic() - last_frame_time
                    if elapsed_since_last > self.read_timeout_s:
                        log.warning(
                            "read_timeout",
                            drone_id=self.drone_id,
                            elapsed_s=round(elapsed_since_last, 2),
                        )
                        break  # break inner loop → reconnect
                    # Transient read failure — retry immediately
                    time.sleep(0.005)
                    continue

                last_frame_time = time.monotonic()
                decode_latency_ms = (t_read_end - t_read_start) * 1_000.0

                # ── GPU post-processing for webcam/USB frames ────────────
                # RTSP frames are already decoded by NVDEC (or CPU ffmpeg).
                # Webcam frames come out of the driver as raw BGR; we route
                # them through cv2.cuda for resize/normalisation so the GPU
                # pipeline is genuinely used for webcam testing.
                if self.active_source_type in ("webcam", "usb"):
                    frame = self._gpu_process_frame(frame)

                # Build VideoFrame
                h, w = frame.shape[:2]
                utc_ms = int(time.time() * 1_000)
                vf = VideoFrame(
                    frame_index=self._frame_index,
                    utc_timestamp_ms=utc_ms,
                    frame=frame,
                    width=w,
                    height=h,
                    decode_latency_ms=decode_latency_ms,
                )
                self._frame_index += 1
                self._fps_frame_count += 1

                # ── Metrics ──────────────────────────────────────────
                DRONE_DECODE_LATENCY.labels(drone_id=self.drone_id).observe(decode_latency_ms)
                DRONE_FRAMES_TOTAL.labels(drone_id=self.drone_id).inc()

                if self._fps_frame_count >= self.heartbeat_interval_frames:
                    elapsed = time.monotonic() - self._fps_window_start
                    fps = self._fps_frame_count / max(elapsed, 1e-6)
                    self.source_fps = fps   # expose for HUD
                    DRONE_FPS.labels(drone_id=self.drone_id).set(fps)
                    DRONE_FRAME_WIDTH.labels(drone_id=self.drone_id).set(w)
                    DRONE_FRAME_HEIGHT.labels(drone_id=self.drone_id).set(h)
                    log.debug(
                        "heartbeat",
                        drone_id=self.drone_id,
                        fps=round(fps, 1),
                        width=w,
                        height=h,
                        decode_latency_ms=round(decode_latency_ms, 1),
                    )
                    self._fps_frame_count = 0
                    self._fps_window_start = time.monotonic()

                # ── Deliver frame ────────────────────────────────────
                # Async buffer write (non-blocking — writer thread does the I/O)
                if self.on_frame is not None:
                    try:
                        self._write_queue.put_nowait(vf)
                    except queue.Full:
                        # Write queue full — drop oldest to keep reader free
                        try:
                            self._write_queue.get_nowait()
                        except queue.Empty:
                            pass
                        try:
                            self._write_queue.put_nowait(vf)
                        except queue.Full:
                            pass

                # Queue path (for interpolator / pull-based consumers)
                try:
                    self._frame_queue.put_nowait(vf)
                except queue.Full:
                    # Drop the oldest and enqueue the new frame so consumer
                    # always gets the latest — we never block the reader.
                    try:
                        self._frame_queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        self._frame_queue.put_nowait(vf)
                    except queue.Full:
                        pass

            # ── Reconnect / source-switch ─────────────────────────────
            self._release_cap()
            if not self._stop_event.is_set():
                # If the inner loop exited because of a source-switch request,
                # do NOT apply reconnect back-off — switch immediately so the
                # new source appears without a multi-second blank screen.
                if self._switch_event.is_set():
                    log.info(
                        "source_switch_no_backoff",
                        drone_id=self.drone_id,
                        to_source=self._requested_source,
                    )
                    reconnect_attempt = 0   # reset back-off counter on manual switch
                else:
                    delay = self._backoff_delay(reconnect_attempt)
                    reconnect_attempt += 1
                    DRONE_RECONNECT_TOTAL.labels(drone_id=self.drone_id).inc()
                    log.warning(
                        "stream_lost_reconnecting",
                        drone_id=self.drone_id,
                        attempt=reconnect_attempt,
                        delay_s=round(delay, 1),
                    )
                    self._stop_event.wait(timeout=delay)

        log.info("reader_loop_exited", drone_id=self.drone_id)

    # ------------------------------------------------------------------
    # Async buffer writer
    # ------------------------------------------------------------------

    def _write_worker(self) -> None:
        """
        Dedicated thread that drains _write_queue and calls on_frame.
        Decouples disk/memory I/O from the RTSP reader thread so cap.read()
        can immediately proceed to the next frame without blocking on writes.
        """
        log.info("write_worker_started", drone_id=self.drone_id)
        while True:
            try:
                vf = self._write_queue.get(timeout=0.5)
            except queue.Empty:
                if self._stop_event.is_set():
                    break
                continue
            if vf is None:  # sentinel — time to exit
                break
            if self.on_frame is not None:
                try:
                    self.on_frame(vf)
                except Exception as exc:
                    log.error(
                        "on_frame_callback_error",
                        drone_id=self.drone_id,
                        error=str(exc),
                    )
        log.info("write_worker_stopped", drone_id=self.drone_id)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _backoff_delay(self, attempt: int) -> float:
        delay = self.reconnect_base_delay_s * (self.reconnect_multiplier ** attempt)
        return min(delay, self.reconnect_max_delay_s)

    @staticmethod
    def _sanitise_url(url: str) -> str:
        """
        Strip credentials from URL for safe logging.
        ``rtsp://admin:secret@192.168.1.1/…`` → ``rtsp://***@192.168.1.1/…``
        """
        import re
        return re.sub(r"//[^@]+@", "//***@", url)

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "DroneVideoSource":
        self.start()
        return self

    def __exit__(self, *args) -> None:
        self.stop()

    def __repr__(self) -> str:
        return (
            f"DroneVideoSource(drone_id={self.drone_id!r}, "
            f"connected={self.is_connected}, "
            f"frames={self._frame_index})"
        )
