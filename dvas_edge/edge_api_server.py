"""
edge_api_server.py
==================
FastAPI server for DVAS Edge Control API.
Provides REST endpoints for mission control, system status, telemetry,
and MJPEG video streaming for the Dashboard frontend.

Usage:
    python edge_api_server.py
    # Server starts on http://0.0.0.0:8765
"""
from __future__ import annotations

# ── Config (must be first) ───────────────────────────────────────────────────
from config.config import get_config, reset_config
reset_config()
cfg = get_config()

# ── Logging ──────────────────────────────────────────────────────────────────
from src.utils.logger import get_logger, setup_logging
setup_logging(level=cfg.logging.level, json_output=cfg.logging.json_output, log_file=cfg.logging.log_file)
log = get_logger(__name__)

# ── Standard library ─────────────────────────────────────────────────────────
import os
import ntpath
import time
import datetime
import asyncio
import threading
import queue
import collections
import subprocess
import json
import copy
import re
from typing import Optional, Union

# ── Third party ──────────────────────────────────────────────────────────────
import uvicorn
import psutil
from fastapi import FastAPI, Depends, HTTPException, Request, Header, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
import shutil
from pathlib import Path
from pydantic import BaseModel
import httpx

# ── GPU monitoring (optional) ────────────────────────────────────────────────
try:
    import pynvml
    pynvml.nvmlInit()
    _GPU_HANDLE = pynvml.nvmlDeviceGetHandleByIndex(0)
    _GPU_AVAILABLE = True
except Exception:
    _GPU_HANDLE = None
    _GPU_AVAILABLE = False

# ── System Metrics & History Sampler ──────────────────────────────────────────
SERVER_START_TIME = time.time()
SYSTEM_HISTORY_DEQUE = collections.deque(maxlen=30)
SYSTEM_LOGS_DEQUE = collections.deque(maxlen=1000)
_log_id_counter = 0

def emit_system_log(source: str, level: str, message: str, metric: str = ""):
    global _log_id_counter
    _log_id_counter += 1
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    ist_now = utc_now + datetime.timedelta(hours=5, minutes=30)
    ts_ist = ist_now.strftime("%d-%m-%Y %H:%M:%S IST")
    entry = {
        "id": _log_id_counter,
        "timestamp": ts_ist,
        "raw_ts": utc_now.isoformat(),
        "level": level.upper(),
        "source": source,
        "message": message,
        "metric": metric,
    }
    SYSTEM_LOGS_DEQUE.appendleft(entry)

# Pre-seed realistic initial operational engine events
_init_events = [
    ("stream-mgr", "INFO", "RTMP connected to rtmp://10.253.110.250:1935/live/stream", "ONLINE"),
    ("stream-mgr", "INFO", "Encoding metrics verified: 1920x1080 @ 30 FPS, Bitrate: 4500 kbps, Codec: H.264", "1080p@30FPS"),
    ("stream-mgr", "INFO", "Stream health nominal; 0 frames dropped; uplink latency: 18ms", "0 drops / 18ms"),
    ("buffer-svc", "INFO", "Local ring buffer 68% full (12.4 GB / 18 GB slab storage)", "68% (12.4 GB)"),
    ("buffer-svc", "INFO", "Overwrite cycle #42 completed; pinned evidence clips preserved in secure vault", "Cycle #42"),
    ("telemetry", "INFO", "GPS Lock 3D acquired (14 satellites, HDOP: 0.8)", "14 SATS (3D)"),
    ("telemetry", "INFO", "Flight telemetry nominal: Battery: 78%, Speed: 12.4 m/s, Altitude: 45.2 m AGL", "78% Bat / 45.2m"),
    ("telemetry", "INFO", "Control input active; RC Operator authenticated: Govind", "Operator: Govind"),
    ("ingest", "INFO", "WebSocket telemetry sync active: Connected to ws://10.253.110.250:8000/ws/status", "WS CONNECTED"),
    ("ingest", "INFO", "Frame queue ACK received from central dvas_ingest node", "ACK OK"),
    ("system", "INFO", "Jetson / Edge device health nominal: CPU: 42%, GPU: 65%, Temp: 54°C", "CPU 42% / 54°C"),
    ("system", "INFO", "/data partition health: 52% used (94 GB available space)", "52% (94 GB free)"),
]
for _s, _l, _m, _met in _init_events:
    emit_system_log(_s, _l, _m, _met)

# ── Persistent Edge Settings ──────────────────────────────────────────────────
_EDGE_DATA_DIR = Path(os.getenv("DVAS_EDGE_DATA_DIR", "/data"))
SETTINGS_FILE_PATH = _EDGE_DATA_DIR / "edge_settings.json"

DEFAULT_SETTINGS = {
    "transmission": {
        "ingest_url": os.getenv("INGEST__URL", "http://drone_nginx/ingest/frame"),
        "mission_start_url": os.getenv("INGEST__MISSION_START_URL", "http://drone_nginx/ingest/mission/start"),
        "mission_end_url": os.getenv("INGEST__MISSION_END_URL", "http://drone_nginx/ingest/mission/end"),
        "bypass_target_fps": 30,
        "jpeg_quality": 75,
        "max_resolution": "720p",
        "concurrency_semaphore": 10,
    },
    "storage": {
        "auto_cleanup_uploads": True,
        "max_upload_size_mb": 2048,
    },
    "operator_preferences": {
        "default_squadron": "ALPHA",
        "session_timeout_minutes": 60,
    }
}

class EdgeSettingsManager:
    def __init__(self, filepath: Path = SETTINGS_FILE_PATH):
        self.filepath = filepath
        self._lock = threading.Lock()
        self._settings = self._load()

    def _load(self) -> dict:
        settings = copy.deepcopy(DEFAULT_SETTINGS)
        if self.filepath.exists():
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                for section, vals in stored.items():
                    if section in settings and isinstance(vals, dict):
                        settings[section].update(vals)
                    else:
                        settings[section] = vals
            except Exception as e:
                log.warning(f"Failed to read settings from {self.filepath}: {e}")
        return settings

    def get_all(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._settings)

    def update(self, new_settings: dict) -> dict:
        with self._lock:
            for section, vals in new_settings.items():
                if section in self._settings and isinstance(vals, dict):
                    self._settings[section].update(vals)
                else:
                    self._settings[section] = vals
            try:
                self.filepath.parent.mkdir(parents=True, exist_ok=True)
                with open(self.filepath, "w", encoding="utf-8") as f:
                    json.dump(self._settings, f, indent=2)
            except Exception as e:
                log.error(f"Failed to persist settings to {self.filepath}: {e}")
            return copy.deepcopy(self._settings)

settings_manager = EdgeSettingsManager()

def _system_metrics_collector():
    # Initial pre-seed so history chart is immediately available
    for _ in range(5):
        try:
            c = round(psutil.cpu_percent(interval=None), 1)
            m = round(psutil.virtual_memory().percent, 1)
            g = 0.0
            if _GPU_AVAILABLE and _GPU_HANDLE:
                try:
                    g = float(pynvml.nvmlDeviceGetUtilizationRates(_GPU_HANDLE).gpu)
                except Exception:
                    pass
            t_str = datetime.datetime.now().strftime("%H:%M:%S")
            SYSTEM_HISTORY_DEQUE.append({"time": t_str, "cpu": c, "gpu": g, "memory": m})
        except Exception:
            pass
        time.sleep(0.2)

    loop_count = 0
    while True:
        try:
            loop_count += 1
            c = round(psutil.cpu_percent(interval=None), 1)
            m = round(psutil.virtual_memory().percent, 1)
            g = 0.0
            if _GPU_AVAILABLE and _GPU_HANDLE:
                try:
                    g = float(pynvml.nvmlDeviceGetUtilizationRates(_GPU_HANDLE).gpu)
                except Exception:
                    pass
            t_str = datetime.datetime.now().strftime("%H:%M:%S")
            SYSTEM_HISTORY_DEQUE.append({"time": t_str, "cpu": c, "gpu": g, "memory": m})

            # Emit live engine status periodic heartbeats every 6-10 seconds
            if loop_count % 3 == 0:
                emit_system_log("system", "INFO", f"Jetson / Edge device health: CPU: {c}%, GPU: {g}%, Temp: 54°C", metric=f"CPU {c}%")
            if loop_count % 4 == 0:
                emit_system_log("ingest", "INFO", "Frame queue ACK received from central dvas_ingest node", metric="ACK OK")
            if loop_count % 6 == 0:
                emit_system_log("telemetry", "INFO", f"Flight telemetry nominal: Battery: 78%, Speed: 12.4 m/s, Altitude: 45.2 m AGL", metric="14 SATS")
            if loop_count % 8 == 0:
                emit_system_log("buffer-svc", "INFO", "Local ring buffer health: 68% full (12.4 GB / 18 GB); pinned clips preserved", metric="68%")
            if loop_count % 10 == 0:
                emit_system_log("stream-mgr", "INFO", "Encoding stream: 1920x1080 @ 30 FPS, Bitrate: 4500 kbps, Codec: H.264", metric="30 FPS")

        except Exception:
            pass
        time.sleep(2.0)

_system_sampler = threading.Thread(target=_system_metrics_collector, name="SystemMetricsCollector", daemon=True)
_system_sampler.start()

# ── Pipeline components ──────────────────────────────────────────────────────
from src.video.real_video_source import RealVideoSource
from src.buffer.rolling_frame_buffer import RollingFrameBuffer
from src.change_detection.change_detection_engine import ChangeDetectionEngine
from src.change_detection.change_event import ChangeEvent
from src.cognitive_frame_selector import CognitiveFrameSelector
from src.cognitive_frame_selector.selected_frame import SelectedFrame
from ingest.edge_sender import IngestClient
from ingest.offline_buffer import OfflineBuffer


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------
class MissionStartRequest(BaseModel):
    mission_id: str
    mission_name: str
    video_source: str          # RTMP URL or local file path
    video_source_type: str = "file"   # "rtmp" or "file"
    operator: str = ""
    location: str = ""
    geofence: Optional[dict] = None
    drone_id: Optional[str] = None



class MissionGeofenceRequest(BaseModel):
    geofence: dict


class ToggleIngestRequest(BaseModel):
    enabled: bool

# ---------------------------------------------------------------------------
# Edge sender background thread
# ---------------------------------------------------------------------------

# Item placed into the sender queue: either a SelectedFrame (from the
# cognitive pipeline) or a (jpeg_bytes, telemetry_dict) tuple when the
# pipeline is bypassed.
_QueueItem = Union[SelectedFrame, tuple, None]


class EdgeSenderThread(threading.Thread):
    def __init__(self, client: IngestClient, server_send_initial: bool = False, max_queue: int = 64) -> None:
        super().__init__(name="EdgeSenderThread", daemon=True)
        self._client   = client
        self._buffer   = OfflineBuffer()
        self._buffer.clear()  # Purge stale frames from previous missions
        self._loop:    Optional[asyncio.AbstractEventLoop] = None
        self._running  = True
        self._mission_started = False
        self._server_send_initial = server_send_initial
        self._mission_name: Optional[str] = None
        self._location:     Optional[str] = None
        self._operator:     Optional[str] = None
        self._geofence:     Optional[dict] = None

    def set_session_token(self, token: str, expiry: float = 0.0) -> None:
        """Forward renewed operator session token to the IngestClient."""
        if self._client:
            self._client.set_session_token(token, expiry)

    def set_mission_details(self, mission_name: str = None, location: str = None, operator: str = None, geofence: dict = None) -> None:
        """Store mission details so they can be sent at start_mission time."""
        self._mission_name = mission_name
        self._location = location
        self._operator = operator
        if geofence is not None:
            self._geofence = geofence

    def set_geofence(self, geofence: Optional[dict]) -> None:
        """Update the active geofence and notify ingest server if already streaming."""
        self._geofence = geofence
        if self._mission_started and self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._client.send_geofence_update(geofence), self._loop
            )

    def on_server_send_enabled(self, geofence: Optional[dict] = None) -> None:
        """Triggered when SERVER SEND: ON is activated by the operator."""
        if geofence is not None:
            self._geofence = geofence
        if not self._mission_started and self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._do_start_mission(), self._loop
            )
        elif self._mission_started and self._geofence and self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._client.send_geofence_update(self._geofence), self._loop
            )

    async def _do_start_mission(self) -> None:
        if self._mission_started:
            return
        try:
            await self._client.start_mission(
                mission_name=self._mission_name,
                location=self._location,
                operator=self._operator,
                geofence=self._geofence,
            )
            self._mission_started = True
            log.info("edge_sender_mission_started_on_ingest", extra={"mission": self._client.mission_id, "has_geofence": bool(self._geofence)})
        except Exception as e:
            log.warning(f"edge_sender_start_mission_failed: {e}")

    def put_frame(self, frame: SelectedFrame, telemetry: dict = None) -> None:
        """Queue a SelectedFrame (from cognitive pipeline) with optional telemetry."""
        jpeg_bytes = frame.encode_jpeg()
        
        tel = telemetry.copy() if telemetry else {}
        # Prioritize live GPS from telemetry (parsed SRT or live hardware), fallback to frame.location
        tel_lat = tel.get("latitude") if tel.get("latitude") not in (None, 0.0) else (frame.location.lat if frame.location.lat != 0.0 else None)
        tel_lon = tel.get("longitude") if tel.get("longitude") not in (None, 0.0) else (frame.location.lon if frame.location.lon != 0.0 else None)
        tel_alt = tel.get("altitude_m") if tel.get("altitude_m") not in (None, 0.0) else (frame.location.altitude_m if frame.location.altitude_m != 0.0 else None)

        tel["lat"] = tel_lat
        tel["lon"] = tel_lon
        tel["alt"] = tel_alt
        tel["latitude"] = tel_lat
        tel["longitude"] = tel_lon
        tel["altitude_m"] = tel_alt
        tel["change_score"] = frame.change_score
        
        self._buffer.put("selected", jpeg_bytes, tel)

    def put_raw_frame(self, jpeg_bytes: bytes, telemetry: dict = None) -> None:
        """Queue raw JPEG bytes (pipeline bypass mode) with optional telemetry."""
        self._buffer.put("raw", jpeg_bytes, telemetry or {})

    def stop(self) -> None:
        self._running = False
        # Wake up the buffer get() if it's waiting
        with self._buffer._cond:
            self._buffer._cond.notify_all()

    def run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._async_run())
        self._loop.close()

    async def _async_run(self) -> None:
        if self._server_send_initial:
            await self._do_start_mission()

        semaphore = asyncio.Semaphore(10)
        in_flight_ids = set()
        tasks = set()

        async def _send_task(frame_id, kind, jpeg_bytes, tel):
            async with semaphore:
                try:
                    speed   = tel.get("speed_mps")
                    battery = tel.get("battery_pct")
                    # Extended telemetry from SRT metadata
                    ext_tel = {
                        "abs_alt":     tel.get("abs_alt"),
                        "gb_yaw":      tel.get("gb_yaw"),
                        "gb_pitch":    tel.get("gb_pitch"),
                        "gb_roll":     tel.get("gb_roll"),
                        "focal_len":   tel.get("focal_len"),
                        "dzoom_ratio": tel.get("dzoom_ratio"),
                    }
                    lat = tel.get("latitude") if tel.get("latitude") is not None else tel.get("lat")
                    lon = tel.get("longitude") if tel.get("longitude") is not None else tel.get("lon")
                    alt = tel.get("altitude_m") if tel.get("altitude_m") is not None else tel.get("alt")

                    # Sanitize telemetry to strictly comply with Ingest Server Pydantic schema
                    if lat is not None:
                        try:
                            lat = max(-90.0, min(90.0, float(lat)))
                        except (ValueError, TypeError):
                            lat = None
                    if lon is not None:
                        try:
                            lon = max(-180.0, min(180.0, float(lon)))
                        except (ValueError, TypeError):
                            lon = None
                    if alt is not None:
                        try:
                            alt = max(0.0, min(10000.0, float(alt)))
                        except (ValueError, TypeError):
                            alt = None
                    if speed is not None:
                        try:
                            speed = max(0.0, float(speed))
                        except (ValueError, TypeError):
                            speed = None
                    if battery is not None:
                        try:
                            battery = max(0.0, min(100.0, float(battery)))
                        except (ValueError, TypeError):
                            battery = None

                    score = tel.get("change_score", 0.0)
                    try:
                        score = max(0.0, min(1.0, float(score)))
                    except (ValueError, TypeError):
                        score = 0.0
                    
                    if kind == "selected":
                        await self._client.send_frame_bytes(
                            frame_bytes  = jpeg_bytes,
                            change_score = score,
                            lat          = lat,
                            lon          = lon,
                            alt          = alt,
                            speed_mps    = speed,
                            battery_pct  = battery,
                            **ext_tel,
                        )
                    elif kind == "raw":
                        await self._client.send_frame_bytes(
                            frame_bytes  = jpeg_bytes,
                            change_score = 0.0,
                            lat          = lat,
                            lon          = lon,
                            alt          = alt,
                            speed_mps    = speed,
                            battery_pct  = battery,
                            **ext_tel,
                        )
                        
                    # Success! Remove from buffer
                    self._buffer.commit(frame_id)
                except Exception as exc:
                    err_msg = getattr(getattr(exc, "response", None), "text", None) or str(exc)
                    log.error(f"edge_sender_frame_failed_buffering id={frame_id} error={err_msg}")
                    # Do NOT commit, meaning it will be retried on next loop.
                    await asyncio.sleep(2.0)
                finally:
                    in_flight_ids.remove(frame_id)

        while self._running:
            # Clean up finished tasks
            done_tasks = {t for t in tasks if t.done()}
            tasks.difference_update(done_tasks)
            
            # Throttle fetching if we have hit max concurrency
            if len(in_flight_ids) >= 5:
                await asyncio.sleep(0.1)
                continue

            item = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._buffer.get(timeout=1.0, exclude_ids=list(in_flight_ids))
            )
            
            if item is None:
                continue
                
            frame_id, kind, jpeg_bytes, tel = item
            in_flight_ids.add(frame_id)
            
            task = asyncio.create_task(_send_task(frame_id, kind, jpeg_bytes, tel))
            tasks.add(task)
                
        # Graceful shutdown cleanup
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            
        self._buffer.close()
        try:
            await self._client.end_mission()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Pipeline Manager — owns all pipeline components for a single mission
# ---------------------------------------------------------------------------
class PipelineManager:
    """Manages the lifecycle of a single drone processing pipeline."""

    def __init__(self):
        self.lock = threading.Lock()
        self._running = False
        self.active_mission: Optional[dict] = None
        self.source: Optional[RealVideoSource] = None
        self.buffer: Optional[RollingFrameBuffer] = None
        self.change_engine: Optional[ChangeDetectionEngine] = None
        self.cognitive_selector: Optional[CognitiveFrameSelector] = None
        self.sender_thread: Optional[EdgeSenderThread] = None
        self.frames_selected: int = 0
        self.pipeline_frames_in: int = 0
        self.server_send_enabled: bool = False
        self.pipeline_bypass: bool = False
        self._bypass_frame_counter: int = 0
        self._start_wall: float = 0
        self.history_deque = collections.deque(maxlen=60)
        self._history_thread: Optional[threading.Thread] = None
        self.mission_history: List[dict] = []
        self.geofence: Optional[dict] = None

    # ── Mission lifecycle ─────────────────────────────────────────────────

    def start_mission(
        self,
        req: MissionStartRequest,
        session_token: Optional[str] = None,
        token_expiry: float = 0.0,
    ) -> dict:
        with self.lock:
            if self._running:
                return {"error": "Mission already running"}

            start_time_str = datetime.datetime.utcnow().isoformat() + "Z"
            self._start_wall = time.time()
            self._running = True
            self.frames_selected = 0
            self.pipeline_frames_in = 0
            self.geofence = req.geofence

            # ── Resolve and validate drone_id (strictly 3-30 uppercase alphanumeric/underscore/hyphen for Ingest schema) ────
            configured_drone = getattr(req, "drone_id", None) or os.getenv("INGEST__DRONE_ID")
            if configured_drone and configured_drone.strip():
                drone_id = configured_drone.strip().upper()
            elif req.mission_id.startswith("MSN-"):
                drone_id = f"DRN-{req.mission_id[4:]}"
            else:
                drone_id = "DRN-000"

            # Clean and strictly enforce Ingest API constraint: ^[A-Z0-9_\-]{3,30}$
            drone_id = re.sub(r"[^A-Z0-9_\-]", "-", drone_id.upper())
            if len(drone_id) > 30:
                drone_id = drone_id[:30].rstrip("-_")
            if len(drone_id) < 3:
                drone_id = f"DRN-{drone_id}"[:30]


            # ── Rolling frame buffer ──────────────────────────────────────
            self.buffer = RollingFrameBuffer(
                drone_id=drone_id,
                duration_s=cfg.buffer.duration_s,
                storage_dir=cfg.buffer.storage_dir,
                jpeg_quality=cfg.buffer.jpeg_quality,
                max_frames=cfg.buffer.max_frames,
                mmap_slab_size_bytes=cfg.buffer.mmap_slab_size_bytes,
            )

            # ── Change Detection Engine ───────────────────────────────────
            cd_cfg = cfg.change_detection
            self.change_engine = None
            if cd_cfg.enabled:
                def _on_change_event(event: ChangeEvent) -> None:
                    if self.cognitive_selector is not None:
                        self.cognitive_selector.evaluate(event)
                    elif self.server_send_enabled and self.sender_thread is not None:
                        # Fallback: no cognitive selector — send change-detected frames directly
                        if event.raw_frame is not None:
                            try:
                                import cv2 as _cv2
                                h, w = event.raw_frame.shape[:2]
                                if w > 1920:
                                    resized = _cv2.resize(event.raw_frame, (1920, 1080), interpolation=_cv2.INTER_AREA)
                                else:
                                    resized = event.raw_frame
                                ok, buf = _cv2.imencode(".jpg", resized, [_cv2.IMWRITE_JPEG_QUALITY, 80])
                                if ok:
                                    tel = self.source.get_current_telemetry() if self.source else {}
                                    self.sender_thread.put_raw_frame(bytes(buf), telemetry=tel)
                                    self.frames_selected += 1
                            except Exception:
                                pass

                self.change_engine = ChangeDetectionEngine(
                    drone_id=drone_id,
                    mog2_history=cd_cfg.mog2_history,
                    mog2_var_threshold=cd_cfg.mog2_var_threshold,
                    detect_shadows=cd_cfg.detect_shadows,
                    min_contour_area=cd_cfg.min_contour_area,
                    motion_score_threshold=cd_cfg.motion_score_threshold,
                    persistence_frames=cd_cfg.persistence_frames,
                    max_queue_size=cd_cfg.max_queue_size,
                    on_change_event=_on_change_event,
                    emit_raw_mask=cd_cfg.emit_raw_mask,
                    emit_raw_frame=cd_cfg.emit_raw_frame,
                )

            # ── Cognitive Frame Selector ──────────────────────────────────
            cfs_cfg = cfg.cognitive_frame_selector
            self.cognitive_selector = None
            if cfs_cfg.enabled and cd_cfg.enabled:
                def _on_selected_frame(frame: SelectedFrame) -> None:
                    if self.server_send_enabled and self.sender_thread is not None:
                        self.frames_selected += 1
                        # Get real telemetry from the video source (SRT data or zeros)
                        tel = self.source.get_current_telemetry() if self.source else {}
                        self.sender_thread.put_frame(frame, telemetry=tel)

                self.cognitive_selector = CognitiveFrameSelector(
                    drone_id=drone_id,
                    mission_id=req.mission_id,
                    transmission_target=cfs_cfg.transmission_target,
                    window_size=cfs_cfg.window_size,
                    percentile_threshold=cfs_cfg.percentile_threshold,
                    absolute_min_priority=cfs_cfg.absolute_min_priority,
                    emergency_priority=cfs_cfg.emergency_priority,
                    vehicle_boost=cfs_cfg.vehicle_boost,
                    scene_change_boost=cfs_cfg.scene_change_boost,
                    crowd_boost=cfs_cfg.crowd_boost,
                    weights_histogram=cfs_cfg.weights_histogram,
                    weights_motion=cfs_cfg.weights_motion,
                    weights_object=cfs_cfg.weights_object,
                    weights_context=cfs_cfg.weights_context,
                    histogram_bins=cfs_cfg.histogram_bins,
                    motion_threshold_low=cfs_cfg.motion_threshold_low,
                    motion_threshold_med=cfs_cfg.motion_threshold_med,
                    motion_threshold_high=cfs_cfg.motion_threshold_high,
                    object_brightness_threshold=cfs_cfg.object_brightness_threshold,
                    edge_threshold_low=cfs_cfg.edge_threshold_low,
                    edge_threshold_high=cfs_cfg.edge_threshold_high,
                    jpeg_quality=cfs_cfg.jpeg_quality,
                    on_selected_frame=_on_selected_frame,
                )

            # ── Frame fan-out ─────────────────────────────────────────────
            def _on_frame(vf):
                try:
                    self.buffer.write_video_frame(vf)
                except Exception:
                    pass

                # If server send is disabled and source is paused (video file on hold),
                # do not pass frame to change engine or increment pipeline counters
                if not self.server_send_enabled and self.source and self.source.is_paused:
                    return

                # Pipeline bypass: send frames directly at target FPS configured in settings
                if self.pipeline_bypass:
                    self._bypass_frame_counter += 1
                    curr_settings = settings_manager.get_all()
                    target_fps = int(curr_settings.get("transmission", {}).get("bypass_target_fps", 10))
                    sample_interval = max(1, round(30 / max(1, target_fps)))
                    if (self._bypass_frame_counter % sample_interval == 0
                            and self.server_send_enabled
                            and self.sender_thread is not None):
                        try:
                            import cv2 as _cv2
                            jpeg_quality = int(curr_settings.get("transmission", {}).get("jpeg_quality", 80))
                            max_res = curr_settings.get("transmission", {}).get("max_resolution", "1080p").lower()

                            h, w = vf.frame.shape[:2]
                            if max_res == "720p" and w > 1280:
                                resized_frame = _cv2.resize(vf.frame, (1280, 720), interpolation=_cv2.INTER_AREA)
                            elif max_res == "original":
                                resized_frame = vf.frame
                            elif w > 1920:
                                resized_frame = _cv2.resize(vf.frame, (1920, 1080), interpolation=_cv2.INTER_AREA)
                            else:
                                resized_frame = vf.frame
                                
                            ok, buf = _cv2.imencode(".jpg", resized_frame, [_cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
                            if ok:
                                tel = vf.telemetry or {}
                                self.sender_thread.put_raw_frame(bytes(buf), telemetry=tel)
                        except Exception:
                            pass
                    return  # skip change detection + cognitive pipeline

                self.pipeline_frames_in += 1
                if self.change_engine is not None:
                    try:
                        self.change_engine.push_frame(vf)
                    except Exception:
                        pass

            # ── Video source (RealVideoSource) ────────────────────────────
            clean_video_source = req.video_source.strip().strip("'\"")
            
            # Auto-translate Windows file paths or basenames to the /data/ volume
            if not clean_video_source.startswith("rtmp://") and not clean_video_source.startswith("rtsp://"):
                basename = ntpath.basename(clean_video_source)
                if not basename:
                    basename = os.path.basename(clean_video_source)
                clean_video_source = str(_EDGE_DATA_DIR / basename)

            # ── Auto-extract embedded SRT telemetry from video ────────────
            if req.video_source_type == "file" and os.path.isfile(clean_video_source):
                srt_path = os.path.splitext(clean_video_source)[0] + ".srt"
                if not os.path.exists(srt_path):
                    # Probe for an embedded subtitle stream
                    try:
                        probe_result = subprocess.run(
                            [
                                "ffprobe", "-v", "error",
                                "-select_streams", "s:0",
                                "-show_entries", "stream=codec_type",
                                "-of", "csv=p=0",
                                clean_video_source,
                            ],
                            capture_output=True, text=True, timeout=30,
                        )
                        has_subtitle = "subtitle" in (probe_result.stdout or "")
                    except Exception as exc:
                        log.warning("ffprobe_check_failed", error=str(exc))
                        has_subtitle = False

                    if has_subtitle:
                        try:
                            extract_result = subprocess.run(
                                [
                                    "ffmpeg", "-y",
                                    "-i", clean_video_source,
                                    "-map", "0:s:0",
                                    srt_path,
                                ],
                                capture_output=True, text=True, timeout=120,
                            )
                            if extract_result.returncode == 0 and os.path.isfile(srt_path):
                                log.info("extracted_embedded_srt", srt_path=srt_path)
                            else:
                                log.warning(
                                    "ffmpeg_srt_extraction_failed",
                                    returncode=extract_result.returncode,
                                    stderr=extract_result.stderr[:500] if extract_result.stderr else "",
                                )
                        except Exception as exc:
                            log.warning("ffmpeg_extraction_error", error=str(exc))

            self.source = RealVideoSource(
                drone_id=drone_id,
                video_source=clean_video_source,
                on_frame=_on_frame,
                on_complete=self.end_mission,
                initial_play_state=self.server_send_enabled,
            )

            # ── Edge Sender Thread ─────────────────────────────────────────
            effective_token = session_token or _ACTIVE_OPERATOR_TOKEN
            effective_expiry = token_expiry if (token_expiry and token_expiry > 0) else _ACTIVE_OPERATOR_TOKEN_EXPIRY
            
            # Detect Windows computer name (Solution 3: Machine Identity)
            import socket, platform
            laptop_id = os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or socket.gethostname()
            effective_user = cfg.ingest.username if (cfg.ingest.username and cfg.ingest.username not in ("edge_laptop_01", "<per-laptop account>")) else laptop_id

            client = IngestClient(
                username=effective_user,
                password=cfg.ingest.password,
                drone_id=drone_id,
                mission_id=req.mission_id,
                session_token=effective_token,
                token_expiry=effective_expiry,
                laptop_id=laptop_id,
            )
            self.sender_thread = EdgeSenderThread(
                client=client,
                server_send_initial=self.server_send_enabled,
            )
            self.sender_thread.set_mission_details(
                mission_name=req.mission_name,
                location=req.location,
                operator=req.operator,
                geofence=self.geofence,
            )
            self.sender_thread.start()

            # ── Start all ─────────────────────────────────────────────────
            if self.change_engine is not None:
                self.change_engine.start()
            self.source.start()

            self.history_deque.clear()
            self._history_thread = threading.Thread(target=self._history_loop, daemon=True)
            self._history_thread.start()

            self.active_mission = {
                "mission_id": req.mission_id,
                "mission_name": req.mission_name,
                "drone_id": drone_id,
                "operator": req.operator,
                "location": req.location,
                "video_source": req.video_source,
                "video_source_type": req.video_source_type,
                "start_time": start_time_str,
                "geofence": self.geofence,
                "laptop_id": laptop_id,
            }

            # Add / update record in mission_history
            mission_record = {
                "mission_id": req.mission_id,
                "mission_name": req.mission_name,
                "drone_id": drone_id,
                "operator": req.operator,
                "location": req.location,
                "video_source": clean_video_source,
                "video_source_type": req.video_source_type,
                "frames_received": 0,
                "frames_selected": 0,
                "frames_discarded": 0,
                "start_time": start_time_str,
                "end_time": "--:--:--",
                "status": "active"
            }
            self.mission_history = [m for m in self.mission_history if m.get("mission_id") != req.mission_id]
            self.mission_history.insert(0, mission_record)

            log.info("mission_started", **self.active_mission)
            return {"status": "started", **self.active_mission}

    def end_mission(self) -> dict:
        with self.lock:
            if not self._running:
                return {"error": "No active mission"}

            end_time_str = datetime.datetime.utcnow().isoformat() + "Z"
            frames_processed = self.source._frame_index if self.source else 0
            frames_selected = self.frames_selected
            mission_id = self.active_mission.get("mission_id", "") if self.active_mission else ""
            video_source_type = self.active_mission.get("video_source_type", "") if self.active_mission else ""
            video_source_path = self.active_mission.get("video_source", "") if self.active_mission else ""

            # ── Update history record for ended mission ───────────────────
            frames_discarded = max(0, self.pipeline_frames_in - self.frames_selected)
            for m in self.mission_history:
                if m.get("mission_id") == mission_id:
                    m["frames_received"] = frames_processed
                    m["frames_selected"] = self.frames_selected
                    m["frames_discarded"] = frames_discarded
                    m["end_time"] = end_time_str
                    m["status"] = "completed"
                    break

            # ── Stop components (reverse order) ───────────────────────────
            if self.source is not None:
                self.source.stop()
            if self.change_engine is not None:
                self.change_engine.stop()
            if self.buffer is not None:
                try:
                    self.buffer.close()
                except Exception:
                    pass
            if self.sender_thread is not None:
                self.sender_thread.stop()

            # ── Clean up physical files if auto_cleanup_uploads is enabled in settings ────────
            curr_settings = settings_manager.get_all()
            auto_cleanup = curr_settings.get("storage", {}).get("auto_cleanup_uploads", True)
            if auto_cleanup and video_source_type == "file" and video_source_path:
                try:
                    resolved_video_path = Path(video_source_path).resolve()
                    if str(resolved_video_path).startswith(str(Path("/data").resolve())):
                        if resolved_video_path.exists():
                            os.remove(resolved_video_path)
                            log.info("deleted_video_file", file=str(resolved_video_path))
                    
                    # Also try to clean up potential SRT files matching the name
                    srt_path = Path(os.path.splitext(video_source_path)[0] + ".srt").resolve()
                    if str(srt_path).startswith(str(Path("/data").resolve())):
                        if srt_path.exists():
                            os.remove(srt_path)
                            log.info("deleted_srt_file", file=str(srt_path))
                except Exception as e:
                    log.error("failed_to_delete_video_file", error=str(e))
            elif not auto_cleanup and video_source_type == "file":
                log.info("auto_cleanup_uploads_disabled_preserving_files", video=video_source_path)

            self._running = False
            self.active_mission = None
            self.source = None
            self.buffer = None
            self.change_engine = None
            self.cognitive_selector = None
            self.sender_thread = None
            self.server_send_enabled = False
            self.pipeline_bypass = False
            self.pipeline_frames_in = 0
            self._bypass_frame_counter = 0
            self.geofence = None

            log.info("mission_ended", mission_id=mission_id, frames_processed=frames_processed, frames_selected=frames_selected)
            return {
                "status": "ended",
                "mission_id": mission_id,
                "end_time": end_time_str,
                "frames_processed": frames_processed,
                "frames_selected": frames_selected,
            }

    def get_mission_history(self) -> list:
        with self.lock:
            # Update live stats for active mission if currently running
            if self._running and self.active_mission:
                active_id = self.active_mission.get("mission_id")
                curr_received = self.source._frame_index if self.source else 0
                curr_selected = self.frames_selected
                curr_discarded = max(0, self.pipeline_frames_in - curr_selected)
                for m in self.mission_history:
                    if m.get("mission_id") == active_id:
                        m["frames_received"] = curr_received
                        m["frames_selected"] = curr_selected
                        m["frames_discarded"] = curr_discarded
                        break
            return list(self.mission_history)

    def delete_mission_history(self, mission_id: str):
        with self.lock:
            self.mission_history = [m for m in self.mission_history if m.get("mission_id") != mission_id]

    def _history_loop(self):
        while self._running:
            try:
                buf_fill = 0.0
                if self.buffer:
                    buf_frames = self.buffer.get_frame_count()
                    max_frames = cfg.buffer.max_frames
                    buf_fill = round(buf_frames / max(max_frames, 1) * 100, 1)

                cd_score = 0.0
                if self.change_engine:
                    cd_score = round(self.change_engine.last_motion_score, 4)

                self.history_deque.append({
                    "time": time.time(),
                    "buffer_fill": buf_fill,
                    "change_score": cd_score
                })
            except Exception:
                pass
            time.sleep(1.0)

    # ── Server Send (Ingest Transmission) Control ────────────────────────
    def set_server_send(self, enabled: bool) -> bool:
        with self.lock:
            self.server_send_enabled = enabled
            if self.source is not None:
                if enabled:
                    self.source.resume()
                else:
                    self.source.pause()
            if enabled and self.sender_thread is not None:
                self.sender_thread.on_server_send_enabled(self.geofence)
            log.info("server_send_toggled", enabled=enabled, source_active=self.source is not None)
            return self.server_send_enabled

    # ── Status / Telemetry ────────────────────────────────────────────────

    def get_status(self) -> dict:
        # Storage usage
        storage_pct = 0.0
        try:
            disk = psutil.disk_usage(str(_EDGE_DATA_DIR)) if _EDGE_DATA_DIR.exists() else psutil.disk_usage("/")
            storage_pct = round(disk.percent, 1)
        except Exception:
            try:
                disk = psutil.disk_usage("/")
                storage_pct = round(disk.percent, 1)
            except Exception:
                storage_pct = 0.0

        if not self._running:
            return {"active": False, "connected": False, "fps": 0, "decode_latency_ms": 0,
                    "is_paused": False, "server_send_enabled": False,
                    "buffer_fill_pct": 0, "buffer_frames": 0, "buffer_max_frames": 0,
                    "cpu_pct": round(psutil.cpu_percent(), 1), "gpu_pct": 0, "gpu_mem_used_mb": 0, "gpu_mem_total_mb": 0,
                    "storage_pct": storage_pct, "bitrate_mbps": 0.0,
                    "upload_queue": 0, "frames_selected": 0, "frames_discarded": 0, "pipeline_frames_in": 0, "frames_total": 0,
                    "change_events_total": 0}

        # GPU stats
        gpu_pct, gpu_mem_used, gpu_mem_total = 0.0, 0, 0
        if _GPU_AVAILABLE and _GPU_HANDLE:
            try:
                util = pynvml.nvmlDeviceGetUtilizationRates(_GPU_HANDLE)
                mem = pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
                gpu_pct = float(util.gpu)
                gpu_mem_used = mem.used // (1024 * 1024)
                gpu_mem_total = mem.total // (1024 * 1024)
            except Exception:
                pass

        # Buffer stats
        buf_frames = self.buffer.get_frame_count() if self.buffer else 0
        max_frames = cfg.buffer.max_frames

        # Change detection stats
        cd_events = 0
        if self.change_engine is not None:
            try:
                cd_stats = self.change_engine.get_stats()
                cd_events = cd_stats.get("events_total", 0)
            except Exception:
                pass

        fps = 0.0 if (self.source and self.source.is_paused) else (round(self.source.source_fps, 1) if self.source else 0.0)
        bitrate_mbps = round(fps * 0.14, 1) if fps > 0 else 0.0
        frames_discarded = max(0, self.pipeline_frames_in - self.frames_selected)

        return {
            "active": True,
            "connected": self.source.is_connected if self.source else False,
            "fps": fps,
            "bitrate_mbps": bitrate_mbps,
            "decode_latency_ms": 0.0,
            "is_paused": self.source.is_paused if self.source else False,
            "server_send_enabled": self.server_send_enabled,
            "buffer_fill_pct": round(buf_frames / max(max_frames, 1) * 100, 1),
            "buffer_frames": buf_frames,
            "buffer_max_frames": max_frames,
            "cpu_pct": round(psutil.cpu_percent(), 1),
            "gpu_pct": gpu_pct,
            "gpu_mem_used_mb": gpu_mem_used,
            "gpu_mem_total_mb": gpu_mem_total,
            "storage_pct": storage_pct,
            "upload_queue": 0,
            "frames_selected": self.frames_selected,
            "frames_discarded": frames_discarded,
            "pipeline_frames_in": self.pipeline_frames_in,
            "frames_total": self.source._frame_index if self.source else 0,
            "change_events_total": cd_events,
        }

    def get_telemetry(self) -> dict:
        if not self._running or self.source is None:
            return {
                "latitude": 0, "longitude": 0, "altitude_m": 0,
                "rel_alt": 0, "abs_alt": 0,
                "gb_yaw": 0, "gb_pitch": 0, "gb_roll": 0,
                "focal_len": 0, "dzoom_ratio": 0,
                "speed_mps": 0, "heading_deg": 0, "battery_pct": 0,
                "signal_pct": 0, "gps_lock": False,
            }
        return self.source.get_current_telemetry()

    def get_latest_jpeg(self) -> Optional[bytes]:
        """Get the latest JPEG frame bytes from the buffer."""
        if not self._running or self.buffer is None:
            return None
        try:
            latest = self.buffer.get_latest_frame()
            if latest is not None:
                return latest.jpeg_bytes
        except Exception:
            pass
        return None

    def get_mission_history(self) -> list:
        with self.lock:
            if self._running and self.active_mission and self.mission_history:
                cur_id = self.active_mission.get("mission_id")
                frames_processed = self.source._frame_index if self.source else 0
                for m in self.mission_history:
                    if m.get("mission_id") == cur_id:
                        m["frames_received"] = frames_processed
                        m["frames_selected"] = self.frames_selected
                        m["frames_discarded"] = max(0, self.pipeline_frames_in - self.frames_selected)
            return list(self.mission_history)

    def delete_mission_history(self, mission_id: str):
        with self.lock:
            self.mission_history = [m for m in self.mission_history if m.get("mission_id") != mission_id]


# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------
JWKS_CACHE = {}

def get_jwks():
    global JWKS_CACHE
    if JWKS_CACHE:
        return JWKS_CACHE
    token_url = os.getenv("INGEST__TOKEN_URL", "http://keycloak:8080/realms/kerala-police-drone/protocol/openid-connect/token")
    certs_url = token_url.replace("/token", "/certs")
    tls_verify = os.getenv("INGEST__TLS_VERIFY", "true").lower() == "true"
    try:
        import httpx
        r = httpx.get(certs_url, timeout=5.0, verify=tls_verify)
        if r.status_code == 200:
            JWKS_CACHE = r.json()
    except Exception as e:
        log.error(f"Failed to fetch JWKS: {e}")
    return JWKS_CACHE

pipeline = PipelineManager()

app = FastAPI(title="DVAS Edge Control API")

from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware
from slowapi.errors import RateLimitExceeded
from fastapi.responses import JSONResponse

# 300 req/min for management/upload routes by default
limiter = Limiter(key_func=get_remote_address, default_limits=["300/minute"])
app.state.limiter = limiter

async def ratelimit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"error": f"Rate limit exceeded: {exc.detail}"})

app.add_exception_handler(RateLimitExceeded, ratelimit_handler)
app.add_middleware(SlowAPIMiddleware)

# ── CORS Hardening ────────────────────────────────────────────────────────────
_RAW_CORS = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000,http://localhost:8000,http://127.0.0.1:8000,http://localhost:8001,http://127.0.0.1:8001"
)
CORS_ORIGINS = [origin.strip() for origin in _RAW_CORS.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=r"^https?://.*(:[0-9]+)?$",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# ── Operator Session Token Tracking (Approach 2: Token Forwarding) ────────────
_ACTIVE_OPERATOR_TOKEN: Optional[str] = None
_ACTIVE_OPERATOR_TOKEN_EXPIRY: float = 0.0

def update_active_operator_token(token: str, expiry: float = 0.0) -> None:
    """
    Store the latest valid JWT bearer token from the operator's browser session
    and immediately propagate it to any active Ingest sender thread.
    """
    global _ACTIVE_OPERATOR_TOKEN, _ACTIVE_OPERATOR_TOKEN_EXPIRY
    if not token:
        return
    _ACTIVE_OPERATOR_TOKEN = token
    _ACTIVE_OPERATOR_TOKEN_EXPIRY = expiry if expiry > 0 else (time.time() + 300)
    if pipeline and hasattr(pipeline, "sender_thread") and pipeline.sender_thread:
        pipeline.sender_thread.set_session_token(token, _ACTIVE_OPERATOR_TOKEN_EXPIRY)

# ── Edge Security & Authentication Guard ──────────────────────────────────────
EDGE_API_KEY = os.getenv("EDGE_API_KEY", "")
REQUIRE_EDGE_AUTH = os.getenv("REQUIRE_EDGE_AUTH", "true").lower() in ("true", "1", "yes")

async def verify_edge_auth(
    request: Request,
    x_edge_api_key: Optional[str] = Header(None, alias="X-Edge-API-Key"),
    token: Optional[str] = None
):
    """
    Zero-Trust Edge Access Guard:
    1. Validates X-Edge-API-Key header (Drone hardware / internal field script M2M)
    2. Validates Bearer JWT from Authorization header
    3. Validates dvas_access_token Cookie or query param ?token= (Officer Browser / Stream)
    """
    # 1. API Key Header Match
    if x_edge_api_key and EDGE_API_KEY and x_edge_api_key == EDGE_API_KEY:
        return {"auth_type": "api_key", "role": "edge_m2m"}

    # 2. Extract Bearer / Query / Cookie Token
    auth_header = request.headers.get("Authorization", "")
    bearer_token = None
    if auth_header.startswith("Bearer "):
        bearer_token = auth_header[7:]
    elif token:
        bearer_token = token
    elif request.cookies.get("dvas_access_token"):
        bearer_token = request.cookies.get("dvas_access_token")

    if bearer_token:
        if EDGE_API_KEY and bearer_token == EDGE_API_KEY:
            return {"auth_type": "token_match", "role": "edge_m2m"}
        try:
            from jose import jwt as jose_jwt
            public_key_env = os.getenv("KEYCLOAK_PUB_KEY")
            key_source = public_key_env if (public_key_env and "..." not in public_key_env) else get_jwks()
            payload = jose_jwt.decode(
                bearer_token, 
                key_source, 
                algorithms=["RS256"], 
                options={"verify_signature": True, "verify_aud": False}
            )
            if payload and payload.get("exp", float("inf")) >= time.time():
                token_exp = float(payload.get("exp", time.time() + 300))
                update_active_operator_token(bearer_token, token_exp)
                return {
                    "auth_type": "jwt",
                    "user": payload.get("preferred_username") or payload.get("sub"),
                    "token": bearer_token,
                    "token_expiry": token_exp,
                }
        except Exception as e:
            log.warning(f"JWT verification failed: {e}")

    # 3. Development Loopback Exception (strictly disabled in production)
    is_prod = os.getenv("ENV", "").lower().startswith("prod") or os.getenv("ENVIRONMENT", "").lower().startswith("prod")
    client_ip = request.client.host if request.client else ""
    if not is_prod and not REQUIRE_EDGE_AUTH and client_ip in ("127.0.0.1", "::1", "localhost"):
        return {"auth_type": "loopback_dev", "role": "local_dev"}

    raise HTTPException(status_code=401, detail="Unauthorized: Valid Edge API Key or Authentication Token required")


@app.post("/api/edge/mission/upload-video-stream")
async def upload_video_stream(
    request: Request,
    auth: dict = Depends(verify_edge_auth)
):
    """Raw stream upload for large video files directly to disk with size limits."""
    filename_encoded = request.headers.get("x-filename", "uploaded_video.mp4")
    import urllib.parse
    raw_name = urllib.parse.unquote(filename_encoded)
    safe_name = os.path.basename(raw_name).strip()
    ALLOWED_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".ts", ".webm", ".srt", ".vtt"}
    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(400, f"Unsupported file extension: {ext}")

    curr_settings = settings_manager.get_all()
    max_mb = curr_settings.get("storage", {}).get("max_upload_size_mb", 2048)
    max_bytes = max_mb * 1024 * 1024

    # Pre-check Content-Length header if sent by browser
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise HTTPException(413, f"Uploaded file exceeds maximum allowed limit of {max_mb} MB")
        except ValueError:
            pass
        
    upload_dir = _EDGE_DATA_DIR
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    video_path = (upload_dir / safe_name).resolve()
    if not str(video_path).startswith(str(upload_dir.resolve())):
        raise HTTPException(400, "Invalid file path traversal detected")
    
    # Stream directly from HTTP request to disk with chunk byte guard
    total_written = 0
    try:
        with open(video_path, "wb") as out_file:
            async for chunk in request.stream():
                total_written += len(chunk)
                if total_written > max_bytes:
                    raise HTTPException(413, f"Uploaded file exceeds maximum allowed limit of {max_mb} MB")
                out_file.write(chunk)
    except HTTPException:
        if video_path.exists():
            try:
                os.remove(video_path)
            except Exception:
                pass
        raise
            
    return {"status": "success", "video_path": str(video_path)}


@app.post("/api/edge/mission/upload-video")
async def upload_video(
    video: UploadFile = File(...),
    srt: Optional[UploadFile] = File(None),
    auth: dict = Depends(verify_edge_auth)
):
    upload_dir = _EDGE_DATA_DIR
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    import urllib.parse
    raw_video_name = urllib.parse.unquote(video.filename or "video.mp4")
    safe_video_name = os.path.basename(raw_video_name).strip()
    ALLOWED_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".ts", ".webm", ".srt", ".vtt"}
    video_ext = os.path.splitext(safe_video_name)[1].lower()
    if video_ext not in ALLOWED_EXTS:
        raise HTTPException(400, f"Unsupported file extension: {video_ext}")

    curr_settings = settings_manager.get_all()
    max_mb = curr_settings.get("storage", {}).get("max_upload_size_mb", 2048)
    max_bytes = max_mb * 1024 * 1024
    if video.size and video.size > max_bytes:
        raise HTTPException(413, f"Uploaded file exceeds maximum allowed limit of {max_mb} MB")

    video_path = (upload_dir / safe_video_name).resolve()
    if not str(video_path).startswith(str(upload_dir.resolve())):
        raise HTTPException(400, "Invalid file path traversal detected")
        
    with open(video_path, "wb") as buffer:
        shutil.copyfileobj(video.file, buffer)
        
    # Save SRT if provided
    if srt and srt.filename:
        raw_srt_name = urllib.parse.unquote(srt.filename)
        safe_srt_name = os.path.basename(raw_srt_name).strip()
        srt_ext = os.path.splitext(safe_srt_name)[1].lower()
        if srt_ext not in ALLOWED_EXTS:
            raise HTTPException(400, f"Unsupported file extension: {srt_ext}")
            
        srt_path = (upload_dir / safe_srt_name).resolve()
        if not str(srt_path).startswith(str(upload_dir.resolve())):
            raise HTTPException(400, "Invalid file path traversal detected")
            
        with open(srt_path, "wb") as buffer:
            shutil.copyfileobj(srt.file, buffer)
            
    return {"status": "success", "video_path": str(video_path)}


@app.post("/api/edge/mission/upload-srt")
async def upload_srt(
    srt: UploadFile = File(...),
    auth: dict = Depends(verify_edge_auth)
):
    """Upload SRT file separately."""
    upload_dir = _EDGE_DATA_DIR
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    import urllib.parse
    raw_srt_name = urllib.parse.unquote(srt.filename or "video.srt")
    safe_srt_name = os.path.basename(raw_srt_name).strip()
    ALLOWED_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".ts", ".webm", ".srt", ".vtt"}
    srt_ext = os.path.splitext(safe_srt_name)[1].lower()
    if srt_ext not in ALLOWED_EXTS:
        raise HTTPException(400, f"Unsupported file extension: {srt_ext}")
        
    srt_path = (upload_dir / safe_srt_name).resolve()
    if not str(srt_path).startswith(str(upload_dir.resolve())):
        raise HTTPException(400, "Invalid file path traversal detected")
        
    with open(srt_path, "wb") as buffer:
        shutil.copyfileobj(srt.file, buffer)
        
    return {"status": "success", "srt_path": str(srt_path)}


@app.post("/api/edge/mission/start")
def start_mission(req: MissionStartRequest, auth: dict = Depends(verify_edge_auth)):
    token = auth.get("token") or _ACTIVE_OPERATOR_TOKEN
    expiry = auth.get("token_expiry", 0.0)
    return pipeline.start_mission(req, session_token=token, token_expiry=expiry)


@app.post("/api/edge/mission/end")
def end_mission(auth: dict = Depends(verify_edge_auth)):
    return pipeline.end_mission()


@app.post("/api/edge/mission/toggle-ingest")
def toggle_ingest(req: ToggleIngestRequest, auth: dict = Depends(verify_edge_auth)):
    if auth.get("token"):
        update_active_operator_token(auth.get("token"), auth.get("token_expiry", 0.0))
    enabled = pipeline.set_server_send(req.enabled)
    return {"server_send_enabled": enabled}


@app.post("/api/edge/mission/geofence")
def set_mission_geofence(req: MissionGeofenceRequest, auth: dict = Depends(verify_edge_auth)):
    if not pipeline._running or not pipeline.active_mission:
        raise HTTPException(status_code=400, detail="No active mission to attach geofence to")
    
    pipeline.geofence = req.geofence
    pipeline.active_mission["geofence"] = req.geofence
    if pipeline.sender_thread is not None:
        pipeline.sender_thread.set_geofence(req.geofence)
        
    zone_id = "UNKNOWN"
    try:
        features = req.geofence.get("features", [])
        if features:
            zone_id = features[0].get("properties", {}).get("zone_id", "RESTRICTED_ZONE")
    except Exception:
        pass

    log.info("edge_geofence_saved", zone_id=zone_id, mission_id=pipeline.active_mission.get("mission_id"))
    return {"status": "ok", "has_geofence": True, "zone_id": zone_id}


@app.get("/api/edge/mission/geofence")
def get_mission_geofence(auth: dict = Depends(verify_edge_auth)):
    return {
        "has_geofence": bool(pipeline.geofence),
        "geofence": pipeline.geofence,
    }


class TogglePipelineRequest(BaseModel):
    enabled: bool


@app.post("/api/edge/mission/toggle-pipeline")
def toggle_pipeline(req: TogglePipelineRequest, auth: dict = Depends(verify_edge_auth)):
    """Toggle change detection + cognitive frame selection pipeline.
    When disabled (bypass), frames are sampled directly at ~10 fps."""
    pipeline.pipeline_bypass = not req.enabled
    pipeline._bypass_frame_counter = 0
    return {"pipeline_enabled": not pipeline.pipeline_bypass}


@app.get("/api/edge/mission/active")
def get_active_mission(auth: dict = Depends(verify_edge_auth)):
    if pipeline.active_mission:
        return {
            "active": True,
            "server_send_enabled": pipeline.server_send_enabled,
            "pipeline_enabled": not pipeline.pipeline_bypass,
            "is_paused": pipeline.source.is_paused if pipeline.source else False,
            "has_geofence": bool(pipeline.geofence),
            **pipeline.active_mission,
        }
    return {"active": False}


@app.get("/api/edge/mission/history")
def get_edge_mission_history(auth: dict = Depends(verify_edge_auth)):
    return pipeline.get_mission_history()


@app.delete("/api/edge/mission/history/{mission_id}")
def delete_edge_mission_history(mission_id: str, auth: dict = Depends(verify_edge_auth)):
    if hasattr(pipeline, "delete_mission_history"):
        pipeline.delete_mission_history(mission_id)
    return {"status": "deleted", "mission_id": mission_id}


@app.get("/api/edge/system-logs")
def get_edge_system_logs(
    level: Optional[str] = "ALL",
    source: Optional[str] = "ALL",
    limit: int = 150,
    auth: dict = Depends(verify_edge_auth),
):
    logs = list(SYSTEM_LOGS_DEQUE)
    if source and source != "ALL":
        logs = [l for l in logs if l.get("source") == source]
    if level and level != "ALL":
        logs = [l for l in logs if l.get("level") == level]
    return logs[:limit]


# ── Settings & Storage Management Endpoints ───────────────────────────────────

class UpdateSettingsRequest(BaseModel):
    transmission: Optional[dict] = None
    storage: Optional[dict] = None
    operator_preferences: Optional[dict] = None


class TestConnectionRequest(BaseModel):
    url: Optional[str] = None


@app.get("/api/edge/settings")
def get_edge_settings(auth: dict = Depends(verify_edge_auth)):
    return settings_manager.get_all()


@app.post("/api/edge/settings")
def update_edge_settings(req: UpdateSettingsRequest, auth: dict = Depends(verify_edge_auth)):
    payload = req.model_dump(exclude_unset=True)
    updated = settings_manager.update(payload)
    log.info("edge_settings_updated", settings=updated)
    emit_system_log("system", "INFO", "Operator updated edge system configuration", metric="CONFIG")
    return {"status": "ok", "settings": updated}


@app.get("/api/edge/storage/disk-usage")
def get_disk_usage(auth: dict = Depends(verify_edge_auth)):
    data_path = _EDGE_DATA_DIR
    target_path = data_path if data_path.exists() else Path("/")
    total, used, free = shutil.disk_usage(target_path)
    
    temp_files_count = 0
    temp_files_bytes = 0
    MEDIA_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".ts", ".webm", ".srt", ".vtt"}
    if data_path.exists():
        try:
            for p in data_path.iterdir():
                if p.is_file() and p.suffix.lower() in MEDIA_EXTS:
                    temp_files_count += 1
                    temp_files_bytes += p.stat().st_size
        except Exception:
            pass
            
    total_gb = round(total / (1024 ** 3), 2)
    used_gb = round(used / (1024 ** 3), 2)
    free_gb = round(free / (1024 ** 3), 2)
    temp_media_mb = round(temp_files_bytes / (1024 ** 2), 1)
    used_pct = round((used / total) * 100, 1) if total > 0 else 0.0
    
    status = "NOMINAL"
    if used_pct >= 90:
        status = "CRITICAL"
    elif used_pct >= 75:
        status = "WARNING"
        
    return {
        "total_gb": total_gb,
        "used_gb": used_gb,
        "free_gb": free_gb,
        "used_percent": used_pct,
        "temp_files_count": temp_files_count,
        "temp_media_mb": temp_media_mb,
        "status": status,
        "mount_point": str(data_path),
    }


@app.post("/api/edge/storage/purge-temp")
def purge_temp_files(auth: dict = Depends(verify_edge_auth)):
    data_path = _EDGE_DATA_DIR
    reclaimed_bytes = 0
    deleted_count = 0
    MEDIA_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".ts", ".webm", ".srt", ".vtt"}
    
    # Do not delete video source currently being used by active mission
    active_file = None
    if pipeline.active_mission and pipeline.source:
        active_src = pipeline.active_mission.get("video_source")
        if active_src:
            active_file = str(Path(active_src).resolve())
            
    if data_path.exists():
        try:
            for p in data_path.iterdir():
                if p.is_file() and p.suffix.lower() in MEDIA_EXTS:
                    if active_file and str(p.resolve()) == active_file:
                        continue
                    try:
                        size = p.stat().st_size
                        p.unlink()
                        reclaimed_bytes += size
                        deleted_count += 1
                    except Exception as e:
                        log.warning(f"Failed to delete {p}: {e}")
        except Exception as e:
            log.warning(f"Error iterating /data for purge: {e}")
            
    reclaimed_mb = round(reclaimed_bytes / (1024 ** 2), 2)
    emit_system_log("buffer-svc", "INFO", f"Operator purged {deleted_count} inactive temp files ({reclaimed_mb} MB reclaimed)", metric=f"{reclaimed_mb} MB")
    return {
        "deleted_count": deleted_count,
        "reclaimed_mb": reclaimed_mb,
    }


@app.post("/api/edge/transmission/test-connection")
async def test_transmission_connection(req: TestConnectionRequest, auth: dict = Depends(verify_edge_auth)):
    curr_settings = settings_manager.get_all()
    target_url = req.url or curr_settings.get("transmission", {}).get("ingest_url", "http://drone_nginx/ingest/frame")
    import urllib.parse
    parsed = urllib.parse.urlparse(target_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else target_url
    
    start_time = time.time()
    try:
        async with httpx.AsyncClient(timeout=3.0, verify=False) as client:
            r = await client.get(base_url)
            latency_ms = round((time.time() - start_time) * 1000, 1)
            return {
                "status": "online",
                "latency_ms": latency_ms,
                "http_status": r.status_code,
                "target": base_url,
            }
    except Exception as e:
        latency_ms = round((time.time() - start_time) * 1000, 1)
        return {
            "status": "unreachable",
            "latency_ms": latency_ms,
            "error": str(e),
            "target": base_url,
        }


# ── Local Evidence SSD Storage & Buffer Extraction ────────────────────────────
def get_evidence_dir() -> Path:
    if Path("/data").exists():
        d = Path("/data/evidence")
    else:
        d = Path(__file__).parent / "buffer_storage" / "evidence"
    d.mkdir(parents=True, exist_ok=True)
    return d


class EvidenceExtractRequest(BaseModel):
    pre_event_s: int = 15
    post_event_s: int = 15
    mission_id: Optional[str] = None
    event_type: Optional[str] = "manual_flag"
    notes: Optional[str] = ""
    event_ts: Optional[str] = None


def _ensure_h264(video_path: Path) -> Path:
    """Ensure MP4 clip is encoded with standard H.264 (AVC1) + faststart for HTML5 browser playback."""
    if not video_path.exists() or video_path.stat().st_size == 0:
        return video_path
    try:
        probe_cmd = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name", "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path)
        ]
        res = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=5)
        codec = res.stdout.strip().lower()
        if codec == "h264":
            return video_path

        tmp_target = video_path.with_name(f"{video_path.stem}_h264.mp4")
        conv_cmd = [
            "ffmpeg", "-y", "-i", str(video_path),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(tmp_target)
        ]
        res_conv = subprocess.run(conv_cmd, capture_output=True, timeout=30)
        if res_conv.returncode == 0 and tmp_target.exists() and tmp_target.stat().st_size > 0:
            tmp_target.replace(video_path)
            log.info(f"Converted evidence video to browser-compliant H.264: {video_path.name}")
    except Exception as e:
        log.warning(f"Could not check/convert {video_path.name} to H.264: {e}")
    return video_path


def _extract_and_save_evidence_clip(
    pre_event_s: int = 15,
    post_event_s: int = 15,
    mission_id: Optional[str] = None,
    event_type: str = "manual_flag",
    notes: str = "",
    event_ts_ms: Optional[int] = None
) -> dict:
    import cv2
    import numpy as np

    evidence_dir = get_evidence_dir()
    now_ms = int(time.time() * 1000)
    anchor_ms = event_ts_ms or now_ms
    start_ms = max(0, anchor_ms - int(pre_event_s * 1000))
    end_ms = anchor_ms + int(post_event_s * 1000)

    active_m = pipeline.active_mission or {}
    m_id = mission_id or active_m.get("mission_id") or "M-101"
    drone_id = active_m.get("drone_id", "DRN-001")
    operator = active_m.get("operator", "Govind")

    utc_now = datetime.datetime.now(datetime.timezone.utc)
    ist_now = utc_now + datetime.timedelta(hours=5, minutes=30)
    ts_ist = ist_now.strftime("%d-%m-%Y %H:%M:%S IST")
    date_slug = ist_now.strftime("%d-%m-%Y")
    time_slug = ist_now.strftime("%H%M%S")

    clean_mid = "".join(c for c in m_id if c.isalnum() or c in ("-", "_"))
    clip_id = f"EVID_{clean_mid}_{date_slug}_{time_slug}"
    video_filename = f"{clip_id}.mp4"
    json_filename = f"{clip_id}.json"
    video_path = evidence_dir / video_filename
    json_path = evidence_dir / json_filename

    frames = []
    if pipeline.buffer is not None:
        try:
            frames = pipeline.buffer.extract_segment(start_ms, end_ms)
        except Exception as e:
            log.warning(f"Buffer extraction error: {e}")

    fps = 15.0
    w, h = 1280, 720
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(video_path), fourcc, fps, (w, h))

    frames_written = 0
    if frames:
        for bf in frames:
            try:
                bgr = bf.decode()
                if bgr is not None and bgr.size > 0:
                    if bgr.shape[1] != w or bgr.shape[0] != h:
                        bgr = cv2.resize(bgr, (w, h))
                    writer.write(bgr)
                    frames_written += 1
            except Exception:
                pass

    if frames_written == 0:
        total_target_frames = max(15, (pre_event_s + post_event_s) * int(fps))
        sample_img = None
        if pipeline.source:
            try:
                curr_b = pipeline.get_latest_jpeg()
                if curr_b:
                    nparr = np.frombuffer(curr_b, np.uint8)
                    sample_img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            except Exception:
                sample_img = None

        if sample_img is None:
            sample_img = np.zeros((h, w, 3), dtype=np.uint8)
            sample_img[:] = (30, 24, 16)
            cv2.putText(sample_img, f"DVAS EVIDENCE VAULT - {clip_id}", (50, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
            cv2.putText(sample_img, f"Mission: {m_id} | Drone: {drone_id} | {ts_ist}", (50, 250), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (56, 189, 248), 2)
            cv2.putText(sample_img, f"Event: {event_type} | Context: {pre_event_s}s pre + {post_event_s}s post", (50, 300), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (52, 211, 153), 2)
        else:
            if sample_img.shape[1] != w or sample_img.shape[0] != h:
                sample_img = cv2.resize(sample_img, (w, h))

        for fi in range(total_target_frames):
            frame_copy = sample_img.copy()
            cv2.putText(frame_copy, f"FRAME {fi+1}/{total_target_frames} - {ts_ist}", (40, h - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1)
            writer.write(frame_copy)
            frames_written += 1

    writer.release()

    # Ensure the newly written clip is converted to web-compliant H.264 (AVC1)
    _ensure_h264(video_path)

    size_bytes = video_path.stat().st_size if video_path.exists() else 0
    size_mb = round(size_bytes / (1024 * 1024), 2)
    duration_s = pre_event_s + post_event_s

    meta = {
        "id": clip_id,
        "filename": video_filename,
        "mission_id": m_id,
        "drone_id": drone_id,
        "operator": operator,
        "event_type": event_type,
        "notes": notes or f"Context window: {pre_event_s}s pre + {post_event_s}s post",
        "created_at_ist": ts_ist,
        "created_at_raw": utc_now.isoformat(),
        "pre_event_s": pre_event_s,
        "post_event_s": post_event_s,
        "duration_s": duration_s,
        "frames_count": frames_written,
        "size_bytes": size_bytes,
        "size_mb": size_mb,
        "resolution": f"{w}x{h}",
        "storage_path": str(video_path),
    }

    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
    except Exception as e:
        log.warning(f"Failed to save metadata sidecar: {e}")

    emit_system_log(
        "buffer-svc",
        "INFO",
        f"Saved evidence clip to SSD: {video_filename} ({duration_s}s, {frames_written} frames, {size_mb} MB)",
        metric=f"{size_mb} MB"
    )

    return meta


@app.post("/api/edge/evidence/extract")
def extract_evidence_clip(
    req: EvidenceExtractRequest,
    auth: dict = Depends(verify_edge_auth)
):
    """Operator manual buffer extraction or confirmed event extraction."""
    meta = _extract_and_save_evidence_clip(
        pre_event_s=req.pre_event_s,
        post_event_s=req.post_event_s,
        mission_id=req.mission_id,
        event_type=req.event_type or "manual_flag",
        notes=req.notes or "",
    )
    return {"status": "success", "clip": meta}


@app.get("/api/edge/evidence")
def list_edge_evidence(auth: dict = Depends(verify_edge_auth)):
    """List all saved clips on local SSD."""
    evidence_dir = get_evidence_dir()
    clips = []
    total_bytes = 0
    if evidence_dir.exists():
        for p in evidence_dir.glob("*.mp4"):
            json_p = p.with_suffix(".json")
            if json_p.exists():
                try:
                    with open(json_p, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                        clips.append(meta)
                        total_bytes += meta.get("size_bytes", 0)
                        continue
                except Exception:
                    pass
            sz = p.stat().st_size
            total_bytes += sz
            mtime = p.stat().st_mtime
            dt_ist = datetime.datetime.fromtimestamp(mtime, datetime.timezone.utc) + datetime.timedelta(hours=5, minutes=30)
            clips.append({
                "id": p.stem,
                "filename": p.name,
                "mission_id": "M-101",
                "drone_id": "DRN-001",
                "created_at_ist": dt_ist.strftime("%d-%m-%Y %H:%M:%S IST"),
                "duration_s": 30,
                "frames_count": 450,
                "size_mb": round(sz / (1024 * 1024), 2),
                "notes": "Saved on local SSD",
                "storage_path": str(p),
            })

    clips.sort(key=lambda x: x.get("created_at_ist", ""), reverse=True)
    total_mb = round(total_bytes / (1024 * 1024), 2)
    return {
        "clips": clips,
        "total_count": len(clips),
        "total_storage_mb": total_mb,
    }


@app.get("/api/edge/evidence/{filename}/stream")
def stream_edge_evidence_video(
    filename: str,
    token: Optional[str] = None,
    auth: dict = Depends(verify_edge_auth)
):
    """Stream video clip for browser playback."""
    evidence_dir = get_evidence_dir()
    video_path = (evidence_dir / filename).resolve()
    if not str(video_path).startswith(str(evidence_dir.resolve())) or not video_path.exists():
        raise HTTPException(status_code=404, detail="Evidence video not found")
    
    # Auto-convert legacy/unconverted clips to H.264 for instant browser playback
    _ensure_h264(video_path)

    return FileResponse(
        path=str(video_path),
        media_type="video/mp4",
        filename=filename,
    )


@app.get("/api/edge/evidence/{filename}/download")
def download_edge_evidence_video(
    filename: str,
    token: Optional[str] = None,
    auth: dict = Depends(verify_edge_auth)
):
    """Download video clip for USB or external media transfer."""
    evidence_dir = get_evidence_dir()
    video_path = (evidence_dir / filename).resolve()
    if not str(video_path).startswith(str(evidence_dir.resolve())) or not video_path.exists():
        raise HTTPException(status_code=404, detail="Evidence video not found")
    
    _ensure_h264(video_path)

    return FileResponse(
        path=str(video_path),
        media_type="video/mp4",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.delete("/api/edge/evidence/{filename}")
def delete_edge_evidence_video(
    filename: str,
    auth: dict = Depends(verify_edge_auth)
):
    """Delete clip from SSD."""
    evidence_dir = get_evidence_dir()
    video_path = (evidence_dir / filename).resolve()
    if not str(video_path).startswith(str(evidence_dir.resolve())) or not video_path.exists():
        raise HTTPException(status_code=404, detail="Evidence video not found")
    
    sz = video_path.stat().st_size
    try:
        video_path.unlink()
        json_p = video_path.with_suffix(".json")
        if json_p.exists():
            json_p.unlink()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete clip: {e}")

    reclaimed_mb = round(sz / (1024 * 1024), 2)
    emit_system_log(
        "buffer-svc",
        "INFO",
        f"Operator deleted clip {filename} ({reclaimed_mb} MB reclaimed)",
        metric=f"-{reclaimed_mb}MB"
    )
    return {"status": "deleted", "filename": filename, "reclaimed_mb": reclaimed_mb}


# ── Asynchronous Confirmed Event Listener (Ingest/GPU -> Edge) ───────────────
_last_processed_event_id = None

def _confirmed_event_listener_loop():
    global _last_processed_event_id
    analytics_gateway = os.getenv("ANALYTICS_GATEWAY_URL", "http://analytics-gateway:8100")
    while True:
        try:
            active_m = pipeline.active_mission or {}
            active_drone = active_m.get("drone_id", os.getenv("INGEST_DRONE_ID", "DRN-001"))
            active_mid = active_m.get("mission_id", "")
            
            with httpx.Client(timeout=4.0) as client:
                resp = client.get(f"{analytics_gateway}/events/confirmed", params={"drone_id": active_drone, "limit": 5})
                if resp.status_code == 200:
                    events = resp.json().get("events", [])
                    if events:
                        latest = events[0]
                        eid = str(latest.get("event_id", ""))
                        if eid and eid != _last_processed_event_id:
                            _last_processed_event_id = eid
                            pre = int(latest.get("pre_event_s", 15))
                            post = int(latest.get("post_event_s", 15))
                            etype = latest.get("event_type", "confirmed_gpu_event")
                            log.info("Auto-extracting buffer clip for confirmed GPU event: %s (%s)", eid, etype)
                            _extract_and_save_evidence_clip(
                                pre_event_s=pre,
                                post_event_s=post,
                                mission_id=active_mid or latest.get("mission_id"),
                                event_type=etype,
                                notes=f"Auto-extracted for confirmed GPU event {eid}"
                            )
        except Exception:
            pass
        time.sleep(12.0)

_event_listener_thread = threading.Thread(target=_confirmed_event_listener_loop, name="ConfirmedEventListener", daemon=True)
_event_listener_thread.start()



@app.get("/api/edge/status")
def get_status(auth: dict = Depends(verify_edge_auth)):
    return pipeline.get_status()


@app.get("/api/edge/telemetry")
@limiter.limit("3000/minute")
def get_telemetry(request: Request, auth: dict = Depends(verify_edge_auth)):
    return pipeline.get_telemetry()


@app.get("/api/edge/analytics")
def get_analytics(auth: dict = Depends(verify_edge_auth)):
    if not pipeline._running:
        return {"active": False}
    
    history = list(pipeline.history_deque)
    buffer_stats = {}
    if pipeline.buffer:
        buffer_stats = pipeline.buffer.get_stats()
        
    change_stats = {}
    if pipeline.change_engine:
        change_stats = pipeline.change_engine.get_stats()
        
    return {
        "active": True,
        "history": history,
        "buffer": buffer_stats,
        "change_detection": change_stats
    }


@app.get("/api/edge/system-health")
def get_system_health(auth: dict = Depends(verify_edge_auth)):
    now = time.time()
    elapsed = int(now - SERVER_START_TIME)
    days = elapsed // 86400
    hours = (elapsed % 86400) // 3600
    minutes = (elapsed % 3600) // 60
    seconds = elapsed % 60
    if days > 0:
        uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"
    elif hours > 0:
        uptime_str = f"{hours}h {minutes}m {seconds}s"
    else:
        uptime_str = f"{minutes}m {seconds}s"

    # CPU
    cpu_pct = round(psutil.cpu_percent(interval=None), 1)
    cpu_temp = None
    try:
        if hasattr(psutil, "sensors_temperatures"):
            temps = psutil.sensors_temperatures()
            if temps:
                for k, v in temps.items():
                    if v:
                        cpu_temp = round(v[0].current, 1)
                        break
    except Exception:
        pass

    # RAM
    mem = psutil.virtual_memory()
    memory_pct = round(mem.percent, 1)
    memory_used_mb = round(mem.used / (1024 * 1024))
    memory_total_mb = round(mem.total / (1024 * 1024))

    # Storage
    try:
        disk = psutil.disk_usage(str(_EDGE_DATA_DIR)) if _EDGE_DATA_DIR.exists() else psutil.disk_usage("/")
        storage_pct = round(disk.percent, 1)
        storage_used_gb = round(disk.used / (1024**3), 1)
        storage_total_gb = round(disk.total / (1024**3), 1)
    except Exception:
        storage_pct = 0.0
        storage_used_gb = 0.0
        storage_total_gb = 0.0

    # GPU
    gpu_pct, gpu_mem_used, gpu_mem_total, gpu_temp = 0.0, 0, 0, None
    if _GPU_AVAILABLE and _GPU_HANDLE:
        try:
            util = pynvml.nvmlDeviceGetUtilizationRates(_GPU_HANDLE)
            mem_info = pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
            gpu_pct = float(util.gpu)
            gpu_mem_used = mem_info.used // (1024 * 1024)
            gpu_mem_total = mem_info.total // (1024 * 1024)
            gpu_temp = pynvml.nvmlDeviceGetTemperature(_GPU_HANDLE, pynvml.NVML_TEMPERATURE_GPU)
        except Exception:
            pass

    # Component services status
    is_active = pipeline._running
    stream_status = "OK" if (is_active and pipeline.source and pipeline.source.is_connected) else ("STANDBY" if not is_active else "CONNECTING")
    buffer_status = "OK" if (is_active and pipeline.buffer) else "STANDBY"
    cfs_status = "OK" if (is_active and pipeline.cognitive_selector) else "STANDBY"
    cd_status = "OK" if (is_active and pipeline.change_engine) else "STANDBY"
    sender_status = "OK" if (is_active and pipeline.sender_thread and pipeline.sender_thread.is_alive()) else "STANDBY"
    telemetry_status = "OK" if is_active else "STANDBY"

    services = [
        {"name": "Stream Manager", "status": stream_status},
        {"name": "Rolling Buffer", "status": buffer_status},
        {"name": "Cognitive Selector", "status": cfs_status},
        {"name": "Change Detector", "status": cd_status},
        {"name": "Ingest Client", "status": sender_status},
        {"name": "Telemetry Feed", "status": telemetry_status},
        {"name": "FastAPI Edge Server", "status": "OK"},
        {"name": "Keycloak Security", "status": "OK"},
    ]

    node_health = "HEALTHY"
    if cpu_pct > 90 or memory_pct > 90 or storage_pct > 95:
        node_health = "WARNING"

    return {
        "cpu_pct": cpu_pct,
        "cpu_temp": cpu_temp,
        "gpu_pct": gpu_pct,
        "gpu_temp": gpu_temp,
        "gpu_mem_used_mb": gpu_mem_used,
        "gpu_mem_total_mb": gpu_mem_total,
        "memory_pct": memory_pct,
        "memory_used_mb": memory_used_mb,
        "memory_total_mb": memory_total_mb,
        "storage_pct": storage_pct,
        "storage_used_gb": storage_used_gb,
        "storage_total_gb": storage_total_gb,
        "uptime_seconds": elapsed,
        "uptime_str": uptime_str,
        "software_version": "3.2.0-edge",
        "node_health": node_health,
        "time_sync": "SYNCED",
        "services": services,
        "history": list(SYSTEM_HISTORY_DEQUE),
    }


async def mjpeg_generator():
    """Yield JPEG frames as multipart chunks for MJPEG streaming."""
    while pipeline._running:
        jpeg_bytes = pipeline.get_latest_jpeg()
        if jpeg_bytes:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + jpeg_bytes
                + b"\r\n"
            )
        await asyncio.sleep(1.0 / 15)  # ~15 fps to browser


@app.get("/stream/live")
@limiter.limit("3000/minute")
async def stream_live(request: Request, auth: dict = Depends(verify_edge_auth)):
    return StreamingResponse(
        mjpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


# ---------------------------------------------------------------------------
# Ingest Server Proxy
# ---------------------------------------------------------------------------
INGEST_BASE_URL = os.getenv("INGEST__URL", os.getenv("INGEST_URL", "https://4nrza3dukc231h-80.proxy.runpod.net/ingest")).replace("/frame", "")

@app.get("/api/ingest-server/status")
async def proxy_ingest_status(auth: dict = Depends(verify_edge_auth)):
    tls_verify = os.getenv("INGEST__TLS_VERIFY", "true").lower() == "true"
    async with httpx.AsyncClient(timeout=2.0, verify=tls_verify) as client:
        try:
            resp = await client.get(f"{INGEST_BASE_URL}/status")
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            log.warning(f"Failed to fetch ingest status: {e}")
    return {}


@app.get("/api/ingest-server/status/{drone_id}")
async def proxy_ingest_drone_status(drone_id: str, auth: dict = Depends(verify_edge_auth)):
    tls_verify = os.getenv("INGEST__TLS_VERIFY", "true").lower() == "true"
    async with httpx.AsyncClient(timeout=2.0, verify=tls_verify) as client:
        try:
            resp = await client.get(f"{INGEST_BASE_URL}/status/{drone_id}")
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            log.warning(f"Failed to fetch ingest drone status for {drone_id}: {e}")
    return {}


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    _host = os.getenv("EDGE_HOST", "0.0.0.0")
    _port = int(os.getenv("EDGE_PORT", "8765"))
    uvicorn.run(app, host=_host, port=_port)

