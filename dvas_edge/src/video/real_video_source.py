"""
src/video/real_video_source.py
================================
Real Video Source — RTMP Stream & Local File Playback with SRT Telemetry

Reads live video from an RTMP URL or a local video file (.mp4, .avi, .mov)
using OpenCV. If the video has an associated .srt subtitle file containing
DJI-style telemetry metadata, it extracts and syncs that data per frame.

Design goals
-------------
• Drop-in replacement for DroneSimulationSource:
    – Same public API: start(), stop(), read(), is_connected, source_fps
    – Same on_frame callback contract
    – Same VideoFrame datatype
• SRT telemetry parsing: automatically detects a sidecar .srt file
  (same basename, .srt extension) or extracts an embedded subtitle track
  via ffprobe/ffmpeg subprocess. Parsed data is synced to frames by index.
• RTMP resilience: reconnects automatically on stream drops (up to 10 retries).
• Local files: play through once and stop (no looping).

Usage
------
    from src.video.real_video_source import RealVideoSource
    source = RealVideoSource(
        drone_id="DRN-MSN-001",
        video_source="rtmp://your-stream/live",
        on_frame=buffer.write_video_frame,
    )
    source.start()
    frame = source.read(timeout=1.0)   # VideoFrame or None
    source.stop()
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

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
from src.utils.logger import get_logger
from src.video.drone_video_source import VideoFrame

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# SRT Telemetry Parser
# ---------------------------------------------------------------------------

def _parse_srt_telemetry(srt_content: str) -> Dict[int, dict]:
    """
    Parse DJI-style SRT subtitle content into a dict keyed by FrameCnt.

    Each SRT block looks like:
        1
        00:00:00,000 --> 00:00:01,000
        FrameCnt: 0
        Timestamp: 2026-08-18 09:55:09.187
        latitude:   9.411869
        longitude: 77.073732
        rel_alt: 760.146
        abs_alt: 852.991
        gb_yaw:   -61.1
        gb_pitch: -61.1
        gb_roll:    0.0
        focal_len:   24.00
        dzoom_ratio: 1.00

    Returns a dict: {frame_cnt_int: {telemetry_dict}}
    """
    result: Dict[int, dict] = {}

    # Split into SRT blocks (separated by blank lines)
    blocks = re.split(r'\n\s*\n', srt_content.strip())

    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue

        telemetry: dict = {}
        frame_cnt: Optional[int] = None

        for line in lines:
            line = line.strip()
            # Skip the SRT index line (just a number) and timestamp line
            if re.match(r'^\d+$', line):
                continue
            if '-->' in line:
                continue

            # Parse key: value pairs
            match = re.match(r'^(\w[\w_]*):\s*(.+)$', line)
            if match:
                key = match.group(1).strip().lower()
                value_str = match.group(2).strip()

                if key == 'framecnt':
                    try:
                        frame_cnt = int(value_str)
                        telemetry['frame_cnt'] = frame_cnt
                    except ValueError:
                        pass
                elif key == 'timestamp':
                    telemetry['srt_timestamp'] = value_str
                elif key in ('latitude', 'longitude', 'rel_alt', 'abs_alt',
                             'gb_yaw', 'gb_pitch', 'gb_roll', 'focal_len',
                             'dzoom_ratio'):
                    try:
                        telemetry[key] = float(value_str)
                    except ValueError:
                        telemetry[key] = 0.0

        if frame_cnt is not None:
            # Map rel_alt -> altitude_m for downstream compatibility
            telemetry['altitude_m'] = telemetry.get('rel_alt', 0.0)
            result[frame_cnt] = telemetry

    return result


def _load_srt_from_sidecar(video_path: str) -> Optional[str]:
    """Check for a sidecar .srt file with the same basename as the video."""
    p = Path(video_path)
    srt_path = p.with_suffix('.srt')
    # Also check uppercase
    if not srt_path.exists():
        srt_path = p.with_suffix('.SRT')
    if srt_path.exists():
        try:
            return srt_path.read_text(encoding='utf-8', errors='replace')
        except Exception as exc:
            log.warning("srt_sidecar_read_failed", path=str(srt_path), error=str(exc))
    return None


def _extract_srt_from_video(video_path: str) -> Optional[str]:
    """
    Attempt to extract an embedded subtitle track from the video using ffprobe/ffmpeg.
    Returns the SRT content as a string, or None if no subtitle track exists.
    """
    try:
        # First, check if ffprobe is available and if there is a subtitle stream
        probe_result = subprocess.run(
            [
                'ffprobe', '-v', 'error',
                '-select_streams', 's:0',
                '-show_entries', 'stream=codec_type',
                '-of', 'csv=p=0',
                video_path,
            ],
            capture_output=True, text=True, timeout=10,
        )
        if 'subtitle' not in probe_result.stdout.lower():
            return None

        # Extract subtitle track to a temp file
        with tempfile.NamedTemporaryFile(suffix='.srt', delete=False, mode='w') as tmp:
            tmp_path = tmp.name

        subprocess.run(
            [
                'ffmpeg', '-y', '-v', 'error',
                '-i', video_path,
                '-map', '0:s:0',
                '-c:s', 'srt',
                tmp_path,
            ],
            capture_output=True, text=True, timeout=30,
        )

        if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
            content = Path(tmp_path).read_text(encoding='utf-8', errors='replace')
            os.unlink(tmp_path)
            return content
        else:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            return None

    except FileNotFoundError:
        log.debug("ffprobe_not_found", hint="ffprobe/ffmpeg not installed; skipping embedded SRT extraction")
        return None
    except subprocess.TimeoutExpired:
        log.warning("srt_extraction_timeout", video=video_path)
        return None
    except Exception as exc:
        log.warning("srt_extraction_failed", video=video_path, error=str(exc))
        return None


def _load_telemetry(video_source: str) -> Dict[int, dict]:
    """
    Load SRT telemetry from a sidecar file or embedded subtitle track.
    Returns an empty dict if no telemetry is found.
    """
    # Only attempt for local files, not RTMP streams
    if video_source.startswith('rtmp://') or video_source.startswith('rtsp://'):
        return {}

    # Try sidecar .srt first
    srt_content = _load_srt_from_sidecar(video_source)
    if srt_content:
        log.info("srt_sidecar_loaded", video=video_source, size=len(srt_content))
        parsed = _parse_srt_telemetry(srt_content)
        log.info("srt_telemetry_parsed", frames_with_telemetry=len(parsed))
        return parsed

    # Try extracting embedded subtitle track
    srt_content = _extract_srt_from_video(video_source)
    if srt_content:
        log.info("srt_embedded_extracted", video=video_source, size=len(srt_content))
        parsed = _parse_srt_telemetry(srt_content)
        log.info("srt_telemetry_parsed", frames_with_telemetry=len(parsed))
        return parsed

    log.info("no_srt_telemetry_found", video=video_source)
    return {}


# ---------------------------------------------------------------------------
# Default (empty) telemetry dict
# ---------------------------------------------------------------------------

def _empty_telemetry() -> dict:
    """Return a telemetry dict with all values zeroed out."""
    return {
        "latitude": 0.0,
        "longitude": 0.0,
        "altitude_m": 0.0,
        "rel_alt": 0.0,
        "abs_alt": 0.0,
        "gb_yaw": 0.0,
        "gb_pitch": 0.0,
        "gb_roll": 0.0,
        "focal_len": 0.0,
        "dzoom_ratio": 0.0,
        "speed_mps": 0.0,
        "battery_pct": 0.0,
        "heading_deg": 0.0,
        "signal_pct": 0.0,
        "gps_lock": False,
    }


# ---------------------------------------------------------------------------
# RealVideoSource
# ---------------------------------------------------------------------------

class RealVideoSource:
    """
    Real video source that reads from an RTMP stream or local video file.

    Presents the same interface as DroneSimulationSource so every downstream
    component (RollingFrameBuffer, ChangeDetectionEngine, CognitiveFrameSelector)
    can consume it without any changes.

    Parameters
    ----------
    drone_id : str
        Logical drone identifier (Prometheus label + log context).
    video_source : str
        RTMP URL (e.g., 'rtmp://host/live/stream') or local file path.
    frame_queue_size : int
        Internal queue depth for the viewer/interpolator consumer.
    on_frame : Optional[Callable[[VideoFrame], None]]
        Callback invoked for every decoded frame (in the reader thread).
    heartbeat_interval_frames : int
        How often to log FPS / resolution metrics.
    """

    def __init__(
        self,
        drone_id: str = "DRN-MISSION",
        video_source: str = "",
        frame_queue_size: int = 8,
        on_frame: Optional[Callable[[VideoFrame], None]] = None,
        heartbeat_interval_frames: int = 60,
    ) -> None:
        self.drone_id = drone_id
        self.video_source = video_source
        self.on_frame = on_frame
        self.heartbeat_interval_frames = heartbeat_interval_frames

        # Determine source type
        self._is_rtmp = video_source.lower().startswith('rtmp://') or video_source.lower().startswith('rtsp://')

        # Load SRT telemetry data (only for local files)
        self._srt_telemetry: Dict[int, dict] = _load_telemetry(video_source)
        # Build sorted frame count keys for closest-match lookup
        self._srt_frame_keys: List[int] = sorted(self._srt_telemetry.keys())
        # Cache the latest telemetry for the /api/edge/telemetry endpoint
        self._latest_telemetry: dict = _empty_telemetry()

        # ── State ────────────────────────────────────────────────────────────
        self._frame_queue: queue.Queue[VideoFrame] = queue.Queue(maxsize=frame_queue_size)
        self._write_queue: queue.Queue[VideoFrame] = queue.Queue(maxsize=frame_queue_size * 4)
        self._stop_event = threading.Event()
        self._reader_thread: Optional[threading.Thread] = None
        self._write_thread: Optional[threading.Thread] = None

        # Counters
        self._frame_index: int = 0
        self._fps_frame_count: int = 0
        self._fps_window_start: float = time.monotonic()

        # Public state (same attributes as DroneVideoSource)
        self.is_connected: bool = False
        self.source_fps: float = 0.0
        self.active_source_type: str = "rtmp" if self._is_rtmp else "file"

        # Compatibility attributes
        self._using_cuvid: bool = False
        self._using_cuda_processing: bool = False
        self.rtsp_url: str = video_source if self._is_rtmp else ""
        self.webcam_device_index: int = 0

        log.info(
            "real_video_source_created",
            drone_id=self.drone_id,
            video_source=video_source,
            source_type=self.active_source_type,
            srt_frames_loaded=len(self._srt_telemetry),
        )

    # ------------------------------------------------------------------
    # Telemetry lookup
    # ------------------------------------------------------------------

    def _get_telemetry_for_frame(self, frame_index: int) -> dict:
        """
        Look up the closest SRT telemetry entry for a given frame index.
        Returns an empty telemetry dict if no SRT data is available.
        """
        if not self._srt_telemetry or not self._srt_frame_keys:
            return _empty_telemetry()

        # Binary search for closest frame count
        import bisect
        pos = bisect.bisect_left(self._srt_frame_keys, frame_index)

        # Find the closest key
        if pos == 0:
            closest_key = self._srt_frame_keys[0]
        elif pos >= len(self._srt_frame_keys):
            closest_key = self._srt_frame_keys[-1]
        else:
            before = self._srt_frame_keys[pos - 1]
            after = self._srt_frame_keys[pos]
            closest_key = before if (frame_index - before) <= (after - frame_index) else after

        srt_data = self._srt_telemetry[closest_key]

        # Build the full telemetry dict with SRT data
        tel = _empty_telemetry()
        tel["latitude"] = srt_data.get("latitude", 0.0)
        tel["longitude"] = srt_data.get("longitude", 0.0)
        tel["altitude_m"] = srt_data.get("altitude_m", 0.0)  # Already mapped from rel_alt
        tel["rel_alt"] = srt_data.get("rel_alt", 0.0)
        tel["abs_alt"] = srt_data.get("abs_alt", 0.0)
        tel["gb_yaw"] = srt_data.get("gb_yaw", 0.0)
        tel["gb_pitch"] = srt_data.get("gb_pitch", 0.0)
        tel["gb_roll"] = srt_data.get("gb_roll", 0.0)
        tel["focal_len"] = srt_data.get("focal_len", 0.0)
        tel["dzoom_ratio"] = srt_data.get("dzoom_ratio", 0.0)
        tel["gps_lock"] = tel["latitude"] != 0.0 or tel["longitude"] != 0.0

        return tel

    def get_current_telemetry(self) -> dict:
        """Return the most recently synced telemetry data."""
        return self._latest_telemetry.copy()

    # ------------------------------------------------------------------
    # Public API (matches DroneSimulationSource)
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background reader and writer threads."""
        if self._reader_thread is not None and self._reader_thread.is_alive():
            log.warning("real_source_already_running", drone_id=self.drone_id)
            return
        self._stop_event.clear()

        self._write_thread = threading.Thread(
            target=self._write_worker,
            name=f"RealWriter-{self.drone_id}",
            daemon=True,
        )
        self._write_thread.start()

        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"RealReader-{self.drone_id}",
            daemon=True,
        )
        self._reader_thread.start()
        log.info("real_source_reader_started", drone_id=self.drone_id)

    def stop(self) -> None:
        """Signal the threads to stop and wait for them."""
        self._stop_event.set()
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=10.0)
        if self._write_thread is not None:
            # Drain the write queue so the sentinel can be enqueued
            while True:
                try:
                    self._write_queue.get_nowait()
                except queue.Empty:
                    break
            try:
                self._write_queue.put(None, timeout=2.0)  # sentinel
            except queue.Full:
                pass
            self._write_thread.join(timeout=5.0)
        self.is_connected = False
        DRONE_STREAM_UP.labels(drone_id=self.drone_id).set(0)
        log.info(
            "real_source_reader_stopped",
            drone_id=self.drone_id,
            total_frames_delivered=self._frame_index,
        )

    def read(self, timeout: float = 1.0) -> Optional[VideoFrame]:
        """
        Blocking read with timeout.
        Returns the next VideoFrame, or None on timeout.
        """
        try:
            return self._frame_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def switch_source(self, source_type: str) -> None:
        """No-op compatibility shim."""
        log.debug(
            "real_source_switch_noop",
            drone_id=self.drone_id,
            requested=source_type,
        )

    # ------------------------------------------------------------------
    # Background reader loop
    # ------------------------------------------------------------------

    def _reader_loop(self) -> None:
        """
        Main loop:
        1. Open the video source (RTMP URL or local file).
        2. Read frames continuously.
        3. For RTMP: reconnect on failure (up to 10 retries).
        4. For files: play through once and exit.
        """
        reconnect_attempts = 0
        max_reconnect_attempts = 10
        reconnect_delay_s = 2.0

        while not self._stop_event.is_set():
            log.info(
                "real_source_opening",
                drone_id=self.drone_id,
                source=self.video_source,
                source_type=self.active_source_type,
            )

            cap = cv2.VideoCapture(self.video_source)

            if not cap.isOpened():
                log.error(
                    "real_source_open_failed",
                    drone_id=self.drone_id,
                    source=self.video_source,
                )
                DRONE_RECONNECT_TOTAL.labels(drone_id=self.drone_id).inc()

                if self._is_rtmp:
                    reconnect_attempts += 1
                    if reconnect_attempts > max_reconnect_attempts:
                        log.error("real_source_max_reconnects_exceeded", drone_id=self.drone_id)
                        break
                    log.info(
                        "real_source_reconnecting",
                        drone_id=self.drone_id,
                        attempt=reconnect_attempts,
                        max_attempts=max_reconnect_attempts,
                    )
                    self._stop_event.wait(timeout=reconnect_delay_s)
                    continue
                else:
                    # Local file failed to open — no retry
                    break

            # Reset reconnect counter on successful connection
            reconnect_attempts = 0

            # ── Query source properties ───────────────────────────────────
            src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

            # For files, throttle to real-time; for RTMP, read as fast as they arrive
            frame_interval_s = (1.0 / src_fps) if not self._is_rtmp else 0.0

            self.source_fps = src_fps
            self.is_connected = True

            DRONE_FRAME_WIDTH.labels(drone_id=self.drone_id).set(w)
            DRONE_FRAME_HEIGHT.labels(drone_id=self.drone_id).set(h)
            DRONE_STREAM_UP.labels(drone_id=self.drone_id).set(1)

            log.info(
                "real_source_opened",
                drone_id=self.drone_id,
                source=self.video_source,
                width=w,
                height=h,
                src_fps=round(src_fps, 2),
                total_frames=total_frames,
                srt_telemetry_available=len(self._srt_telemetry) > 0,
            )

            # ── Per-source frame read loop ──────────────────────────────────
            file_frame_count = 0

            while not self._stop_event.is_set():
                t_frame_start = time.monotonic()

                ret, frame = cap.read()
                if not ret or frame is None:
                    if self._is_rtmp:
                        log.warning(
                            "real_source_rtmp_read_failed",
                            drone_id=self.drone_id,
                        )
                        # Break inner loop to trigger reconnect
                        break
                    else:
                        # End of file
                        log.info(
                            "real_source_file_exhausted",
                            drone_id=self.drone_id,
                            frames_played=file_frame_count,
                        )
                        break

                t_decoded = time.monotonic()
                decode_latency_ms = (t_decoded - t_frame_start) * 1_000.0
                utc_ms = int(time.time() * 1_000)

                # Get telemetry for this frame
                telemetry_data = self._get_telemetry_for_frame(self._frame_index)
                self._latest_telemetry = telemetry_data

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

                # 2. Frame queue (consumer pull)
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
                        "real_source_heartbeat",
                        drone_id=self.drone_id,
                        measured_fps=round(measured_fps, 1),
                        total_frames=self._frame_index,
                    )
                    self._fps_frame_count = 0
                    self._fps_window_start = time.monotonic()

                # ── Throttle for local files ──────────────────────────────
                if not self._is_rtmp and frame_interval_s > 0:
                    elapsed_this_frame = time.monotonic() - t_frame_start
                    sleep_s = frame_interval_s - elapsed_this_frame
                    if sleep_s > 0:
                        self._stop_event.wait(timeout=sleep_s)

            cap.release()

            # For local files, stop after playback; for RTMP, try reconnecting
            if not self._is_rtmp:
                break
            else:
                # RTMP stream dropped — attempt reconnect
                reconnect_attempts += 1
                if reconnect_attempts > max_reconnect_attempts:
                    log.error("real_source_max_reconnects_exceeded", drone_id=self.drone_id)
                    break
                DRONE_RECONNECT_TOTAL.labels(drone_id=self.drone_id).inc()
                log.info(
                    "real_source_reconnecting",
                    drone_id=self.drone_id,
                    attempt=reconnect_attempts,
                    delay_s=reconnect_delay_s,
                )
                self._stop_event.wait(timeout=reconnect_delay_s)

        self.is_connected = False
        DRONE_STREAM_UP.labels(drone_id=self.drone_id).set(0)
        log.info("real_source_reader_loop_exited", drone_id=self.drone_id)

    # ------------------------------------------------------------------
    # Async buffer writer
    # ------------------------------------------------------------------

    def _write_worker(self) -> None:
        """
        Dedicated thread that drains _write_queue and calls on_frame.
        Decouples CPU-heavy JPEG encoding from the reader thread.
        """
        log.info("real_source_write_worker_started", drone_id=self.drone_id)
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
                        "real_source_on_frame_callback_error",
                        drone_id=self.drone_id,
                        error=str(exc),
                    )
        log.info("real_source_write_worker_stopped", drone_id=self.drone_id)
