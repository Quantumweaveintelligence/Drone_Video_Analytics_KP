"""
src/video/drone_simulation_source.py
======================================
Drone Feed Simulation Source — MP4 File Playback

Simulates a live drone RTSP feed by reading from local MP4 files,
throttled to the source's real FPS so the rest of the pipeline sees
a realistic, time-accurate frame stream.

Design goals
-------------
• Drop-in replacement for DroneVideoSource:
    – Same public API: start(), stop(), read(), is_connected, source_fps
    – Same on_frame callback contract
    – Same VideoFrame datatype
• Multi-file playlist: accepts a list of MP4 paths.  Plays them
  sequentially; when the last file ends, loops back to the first.
• Playback speed control: ``playback_speed=1.0`` = real-time;
  ``playback_speed=2.0`` = 2× faster.
• Thread-safe: a single background reader thread drives the pipeline;
  the main/viewer thread calls read() to pull frames.
• Prometheus metrics: reuses existing drone metric gauges/counters so
  Grafana dashboards work identically in simulation mode.
• Switch source noop: ``switch_source()`` is accepted without error
  (required for StreamViewer [W] key compatibility).

Usage
------
    from src.video.drone_simulation_source import DroneSimulationSource
    source = DroneSimulationSource(
        drone_id="drone_sim_01",
        video_files=["DJI_0110.MP4", "DJI_0111.MP4"],
        loop=True,
        on_frame=buffer.write_video_frame,
    )
    source.start()
    frame = source.read(timeout=1.0)   # VideoFrame or None
    source.stop()
"""

from __future__ import annotations

import queue
import threading
import time
import os
from pathlib import Path
from typing import Callable, List, Optional

import cv2
import numpy as np

from src.utils.gpu_utils import GPUInfo, detect_gpu

from src.metrics.prometheus_metrics import (
    DRONE_DECODE_LATENCY,
    DRONE_FPS,
    DRONE_FRAME_HEIGHT,
    DRONE_FRAME_WIDTH,
    DRONE_FRAMES_TOTAL,
    DRONE_RECONNECT_TOTAL,
    DRONE_STREAM_UP,
)
from src.utils.logger import get_logger
from src.video.drone_video_source import VideoFrame

log = get_logger(__name__)


class DroneSimulationSource:
    """
    MP4-file-based drone feed simulator.

    Presents the same interface as DroneVideoSource so every downstream
    component (RollingFrameBuffer, FrameInterpolator, StreamViewer,
    ChangeDetectionEngine) can consume it without any changes.

    Parameters
    ----------
    drone_id : str
        Logical drone identifier (Prometheus label + log context).
    video_files : List[str]
        Ordered list of MP4 file paths.  Played sequentially, looping
        back to index 0 when the last file ends (if loop=True).
    loop : bool
        If True, replay the playlist indefinitely.
        If False, stop after the last file is exhausted.
    playback_speed : float
        1.0 = real-time.  2.0 = twice as fast (frames delivered at 2×
        the source frame interval).  Fractional speeds slow it down.
    frame_queue_size : int
        Internal queue depth for the viewer/interpolator consumer.
        Keep small (4–8) so the consumer always sees the latest frame.
    on_frame : Optional[Callable[[VideoFrame], None]]
        Callback invoked for every decoded frame (in the reader thread).
        Used to push frames into the RollingFrameBuffer asynchronously.
    heartbeat_interval_frames : int
        How often to log FPS / resolution metrics.
    """

    def __init__(
        self,
        drone_id: str = "drone_sim_01",
        video_files: Optional[List[str]] = None,
        loop: bool = True,
        playback_speed: float = 1.0,
        frame_queue_size: int = 8,
        on_frame: Optional[Callable[[VideoFrame], None]] = None,
        heartbeat_interval_frames: int = 60,
        telemetry_simulator=None,
    ) -> None:
        self.drone_id = drone_id
        self.video_files: List[Path] = []
        self.loop = loop
        self.playback_speed = max(0.01, playback_speed)
        self.on_frame = on_frame
        self.heartbeat_interval_frames = heartbeat_interval_frames
        self.telemetry_simulator = telemetry_simulator

        # Validate and store video file paths
        for p in (video_files or []):
            path = Path(p)
            if not path.exists():
                log.warning(
                    "simulation_video_file_not_found",
                    drone_id=self.drone_id,
                    path=str(path),
                )
            self.video_files.append(path)

        if not self.video_files:
            log.warning(
                "simulation_no_video_files_configured",
                drone_id=self.drone_id,
                hint="DroneSimulationSource will produce no frames.",
            )

        # ── State ────────────────────────────────────────────────────────────
        self._frame_queue: queue.Queue[VideoFrame] = queue.Queue(maxsize=frame_queue_size)
        self._write_queue: queue.Queue[VideoFrame] = queue.Queue(maxsize=frame_queue_size * 4)
        self._stop_event = threading.Event()
        self._reader_thread: Optional[threading.Thread] = None
        self._write_thread: Optional[threading.Thread] = None
        
        self._gpu_info: GPUInfo = detect_gpu()

        # Counters
        self._frame_index: int = 0
        self._fps_frame_count: int = 0
        self._fps_window_start: float = time.monotonic()

        # Public state (same attributes as DroneVideoSource)
        self.is_connected: bool = False
        self.source_fps: float = 0.0
        self.active_source_type: str = "simulation"

        # Required by DroneVideoSource-compatible callers that read private attrs
        self._using_cuvid: bool = False
        self._using_cuda_processing: bool = False
        self.rtsp_url: str = ""
        self.webcam_device_index: int = 0

        log.info(
            "drone_simulation_source_created",
            drone_id=self.drone_id,
            file_count=len(self.video_files),
            loop=self.loop,
            playback_speed=self.playback_speed,
            files=[str(p) for p in self.video_files],
        )

    # ------------------------------------------------------------------
    # Public API (matches DroneVideoSource)
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background simulation reader and writer threads."""
        if self._reader_thread is not None and self._reader_thread.is_alive():
            log.warning("simulation_already_running", drone_id=self.drone_id)
            return
        self._stop_event.clear()
        
        self._write_thread = threading.Thread(
            target=self._write_worker,
            name=f"SimWriter-{self.drone_id}",
            daemon=True,
        )
        self._write_thread.start()
        
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"SimReader-{self.drone_id}",
            daemon=True,
        )
        self._reader_thread.start()
        log.info("simulation_reader_started", drone_id=self.drone_id)

    def stop(self) -> None:
        """Signal the threads to stop and wait for them."""
        self._stop_event.set()
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=10.0)
        if self._write_thread is not None:
            # Drain the write queue so the sentinel can always be enqueued,
            # even if the queue was full when stop() was called.
            while True:
                try:
                    self._write_queue.get_nowait()
                except queue.Empty:
                    break
            try:
                self._write_queue.put(None, timeout=2.0)  # sentinel to unblock writer
            except queue.Full:
                pass  # writer will exit via _stop_event anyway
            self._write_thread.join(timeout=5.0)
        self.is_connected = False
        DRONE_STREAM_UP.labels(drone_id=self.drone_id).set(0)
        log.info(
            "simulation_reader_stopped",
            drone_id=self.drone_id,
            total_frames_delivered=self._frame_index,
        )

    def read(self, timeout: float = 1.0) -> Optional[VideoFrame]:
        """
        Blocking read with timeout.
        Returns the next VideoFrame, or None on timeout.
        Compatible with DroneVideoSource.read().
        """
        try:
            return self._frame_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def switch_source(self, source_type: str) -> None:
        """
        No-op compatibility shim for StreamViewer's [W] key handler.
        Simulation mode has no alternate source to switch to.
        """
        log.debug(
            "simulation_switch_source_noop",
            drone_id=self.drone_id,
            requested=source_type,
        )

    @staticmethod
    def _sanitise_url(url: str) -> str:
        """Compatibility shim — simulation has no URL to sanitise."""
        return url or "(simulation)"

    # ------------------------------------------------------------------
    # Background reader loop
    # ------------------------------------------------------------------

    def _reader_loop(self) -> None:
        """
        Main loop:
        1. Open the next MP4 file in the playlist.
        2. Read frames, throttle to real-time based on source FPS.
        3. Deliver frames via queue + on_frame callback.
        4. On file exhaustion: advance to next file; loop if configured.
        """
        file_index: int = 0

        while not self._stop_event.is_set():
            if not self.video_files:
                self._stop_event.wait(timeout=1.0)
                continue

            # Pick the current file
            video_path = self.video_files[file_index % len(self.video_files)]

            if not video_path.exists():
                log.error(
                    "simulation_file_missing",
                    drone_id=self.drone_id,
                    path=str(video_path),
                )
                DRONE_RECONNECT_TOTAL.labels(drone_id=self.drone_id).inc()
                file_index += 1
                if not self.loop and file_index >= len(self.video_files):
                    break
                file_index %= len(self.video_files)
                self._stop_event.wait(timeout=1.0)
                continue

            log.info(
                "simulation_opening_file",
                drone_id=self.drone_id,
                path=str(video_path),
                file_index=file_index % len(self.video_files),
                playlist_size=len(self.video_files),
            )

            # Optional NVDEC GPU hardware decode hack for local files
            using_nvdec = False
            if self._gpu_info.cuvid_available:
                prev = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS", "")
                try:
                    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "video_codec;h264_cuvid"
                    cap = cv2.VideoCapture(str(video_path), cv2.CAP_FFMPEG)
                    if cap.isOpened():
                        using_nvdec = True
                finally:
                    if prev:
                        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = prev
                    else:
                        os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)
            
            # Fallback to default CPU decode if NVDEC failed or is disabled
            if not using_nvdec or not cap.isOpened():
                cap = cv2.VideoCapture(str(video_path))

            if not cap.isOpened():
                log.error(
                    "simulation_file_open_failed",
                    drone_id=self.drone_id,
                    path=str(video_path),
                )
                DRONE_RECONNECT_TOTAL.labels(drone_id=self.drone_id).inc()
                file_index += 1
                if not self.loop and file_index >= len(self.video_files):
                    break
                file_index %= len(self.video_files)
                continue

            # ── Query source properties ───────────────────────────────────
            src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

            # Frame delivery interval in wall-clock seconds
            frame_interval_s = (1.0 / src_fps) / self.playback_speed

            self.source_fps = src_fps
            self.is_connected = True

            DRONE_FRAME_WIDTH.labels(drone_id=self.drone_id).set(w)
            DRONE_FRAME_HEIGHT.labels(drone_id=self.drone_id).set(h)
            DRONE_STREAM_UP.labels(drone_id=self.drone_id).set(1)

            log.info(
                "simulation_file_opened",
                drone_id=self.drone_id,
                path=str(video_path),
                decoder="h264_cuvid (NVDEC)" if using_nvdec else "software (CPU)",
                width=w,
                height=h,
                src_fps=round(src_fps, 2),
                total_frames=total_frames,
                effective_fps=round(src_fps * self.playback_speed, 2),
                frame_interval_s=round(frame_interval_s, 4),
            )

            # ── Per-file frame read loop ──────────────────────────────────
            file_frame_count = 0
            loop_start = time.monotonic()

            while not self._stop_event.is_set():
                t_frame_start = time.monotonic()

                ret, frame = cap.read()
                if not ret or frame is None:
                    # End of file
                    log.info(
                        "simulation_file_exhausted",
                        drone_id=self.drone_id,
                        path=str(video_path),
                        frames_played=file_frame_count,
                    )
                    break

                t_decoded = time.monotonic()
                decode_latency_ms = (t_decoded - t_frame_start) * 1_000.0
                utc_ms = int(time.time() * 1_000)

                telemetry_data = None
                if self.telemetry_simulator is not None:
                    telemetry_data = self.telemetry_simulator.update(self._frame_index, utc_ms)

                vf = VideoFrame(
                    frame_index=self._frame_index,
                    utc_timestamp_ms=utc_ms,
                    frame=frame,
                    width=w,
                    height=h,
                    decode_latency_ms=decode_latency_ms,
                    telemetry=telemetry_data,
                )
                self._frame_index += 1
                self._fps_frame_count += 1
                file_frame_count += 1

                # ── Deliver frame ─────────────────────────────────────────
                # 1. Async buffer write via on_frame callback
                if self.on_frame is not None:
                    try:
                        self._write_queue.put_nowait(vf)
                    except queue.Full:
                        try:
                            self._write_queue.get_nowait()
                        except queue.Empty:
                            pass
                        try:
                            self._write_queue.put_nowait(vf)
                        except queue.Full:
                            pass

                # 2. Frame queue (→ FrameInterpolator / StreamViewer pull)
                try:
                    self._frame_queue.put_nowait(vf)
                except queue.Full:
                    try:
                        self._frame_queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        self._frame_queue.put_nowait(vf)
                    except queue.Full:
                        pass

                # ── Prometheus ────────────────────────────────────────────
                DRONE_DECODE_LATENCY.labels(drone_id=self.drone_id).observe(decode_latency_ms)
                DRONE_FRAMES_TOTAL.labels(drone_id=self.drone_id).inc()

                # ── Heartbeat FPS ─────────────────────────────────────────
                if self._fps_frame_count >= self.heartbeat_interval_frames:
                    elapsed = time.monotonic() - self._fps_window_start
                    measured_fps = self._fps_frame_count / max(elapsed, 1e-6)
                    self.source_fps = measured_fps
                    DRONE_FPS.labels(drone_id=self.drone_id).set(measured_fps)
                    log.debug(
                        "simulation_heartbeat",
                        drone_id=self.drone_id,
                        measured_fps=round(measured_fps, 1),
                        total_frames=self._frame_index,
                        file=str(video_path.name),
                    )
                    self._fps_frame_count = 0
                    self._fps_window_start = time.monotonic()

                # ── Throttle to real-time ─────────────────────────────────
                # Compute how long we should sleep so frames arrive at
                # the correct wall-clock rate (accounting for decode time).
                elapsed_this_frame = time.monotonic() - t_frame_start
                sleep_s = frame_interval_s - elapsed_this_frame
                if sleep_s > 0:
                    self._stop_event.wait(timeout=sleep_s)

            cap.release()

            # ── Advance to next file ──────────────────────────────────────
            file_index += 1
            if not self.loop and file_index >= len(self.video_files):
                log.info(
                    "simulation_playlist_finished",
                    drone_id=self.drone_id,
                    total_frames=self._frame_index,
                )
                self.is_connected = False
                DRONE_STREAM_UP.labels(drone_id=self.drone_id).set(0)
                break

            # Wrap playlist index for loop mode
            if self.loop:
                file_index %= len(self.video_files)

        self.is_connected = False
        DRONE_STREAM_UP.labels(drone_id=self.drone_id).set(0)
        log.info("simulation_reader_loop_exited", drone_id=self.drone_id)

    # ------------------------------------------------------------------
    # Async buffer writer
    # ------------------------------------------------------------------

    def _write_worker(self) -> None:
        """
        Dedicated thread that drains _write_queue and calls on_frame.
        Decouples CPU-heavy JPEG encoding from the simulation reader thread.
        """
        log.info("simulation_write_worker_started", drone_id=self.drone_id)
        while True:
            try:
                vf = self._write_queue.get(timeout=0.5)
            except queue.Empty:
                if self._stop_event.is_set():
                    break
                continue
            if vf is None:  # sentinel
                break
            if self.on_frame is not None:
                try:
                    self.on_frame(vf)
                except Exception as exc:
                    log.error(
                        "simulation_on_frame_callback_error",
                        drone_id=self.drone_id,
                        error=str(exc),
                    )
        log.info("simulation_write_worker_stopped", drone_id=self.drone_id)
