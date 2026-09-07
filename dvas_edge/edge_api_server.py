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
import time
import datetime
import asyncio
import threading
import queue
import collections
import subprocess
from typing import Optional, Union

# ── Third party ──────────────────────────────────────────────────────────────
import uvicorn
import psutil
from fastapi import FastAPI, Depends, HTTPException, Request, Header, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
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

    while True:
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
    def __init__(self, client: IngestClient, max_queue: int = 64) -> None:
        super().__init__(name="EdgeSenderThread", daemon=True)
        self._client   = client
        self._buffer   = OfflineBuffer()
        self._buffer.clear()  # Purge stale frames from previous missions
        self._loop:    Optional[asyncio.AbstractEventLoop] = None
        self._running  = True
        self._mission_name: Optional[str] = None
        self._location:     Optional[str] = None
        self._operator:     Optional[str] = None

    def set_mission_details(self, mission_name: str = None, location: str = None, operator: str = None) -> None:
        """Store mission details so they can be sent at start_mission time."""
        self._mission_name = mission_name
        self._location = location
        self._operator = operator

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
        try:
            await self._client.start_mission(
                mission_name=self._mission_name,
                location=self._location,
                operator=self._operator,
            )
        except Exception:
            pass

        semaphore = asyncio.Semaphore(5)
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
                    
                    if kind == "selected":
                        await self._client.send_frame_bytes(
                            frame_bytes  = jpeg_bytes,
                            change_score = tel.get("change_score", 0.0),
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
                    log.error(f"edge_sender_frame_failed_buffering id={frame_id} error={exc}")
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

    # ── Mission lifecycle ─────────────────────────────────────────────────

    def start_mission(self, req: MissionStartRequest) -> dict:
        with self.lock:
            if self._running:
                return {"error": "Mission already running"}

            start_time_str = datetime.datetime.utcnow().isoformat() + "Z"
            self._start_wall = time.time()
            self._running = True
            self.frames_selected = 0
            self.pipeline_frames_in = 0

            # ── Auto-generate drone_id from mission_id ────────────────────
            drone_id = f"DRN-{req.mission_id}"

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
                    self.frames_selected += 1
                    if self.server_send_enabled and self.sender_thread is not None:
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

                # Pipeline bypass: send frames directly at target FPS (~5 fps)
                if self.pipeline_bypass:
                    self._bypass_frame_counter += 1
                    sample_interval = 6  # 6 = target ~5 fps from a 30 fps source
                    if (self._bypass_frame_counter % sample_interval == 0
                            and self.server_send_enabled
                            and self.sender_thread is not None):
                        try:
                            import cv2 as _cv2
                            
                            # Downscale to 1080p if frame is larger, saving upload bandwidth
                            h, w = vf.frame.shape[:2]
                            if w > 1920:
                                resized_frame = _cv2.resize(vf.frame, (1920, 1080), interpolation=_cv2.INTER_AREA)
                            else:
                                resized_frame = vf.frame
                                
                            ok, buf = _cv2.imencode(".jpg", resized_frame, [_cv2.IMWRITE_JPEG_QUALITY, 80])
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
                import ntpath
                import os
                basename = ntpath.basename(clean_video_source)
                if not basename:
                    basename = os.path.basename(clean_video_source)
                clean_video_source = f"/data/{basename}"

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
            )

            # ── Edge Sender Thread ─────────────────────────────────────────
            client = IngestClient(
                username=cfg.ingest.username,
                password=cfg.ingest.password,
                drone_id=drone_id,
                mission_id=req.mission_id
            )
            self.sender_thread = EdgeSenderThread(client=client)
            self.sender_thread.set_mission_details(
                mission_name=req.mission_name,
                location=req.location,
                operator=req.operator,
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
            }

            # Add / update record in mission_history
            mission_record = {
                "mission_id": req.mission_id,
                "mission_name": req.mission_name,
                "drone_id": drone_id,
                "operator": req.operator,
                "location": req.location,
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

            # ── Clean up physical files if this was a local file upload ────────
            if video_source_type == "file" and video_source_path:
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

            log.info("mission_ended", mission_id=mission_id, frames_processed=frames_processed)
            return {
                "status": "ended",
                "mission_id": mission_id,
                "end_time": end_time_str,
                "frames_processed": frames_processed,
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

    # ── Status / Telemetry ────────────────────────────────────────────────

    def get_status(self) -> dict:
        # Storage usage
        storage_pct = 0.0
        try:
            disk = psutil.disk_usage("/data") if os.path.exists("/data") else psutil.disk_usage("/")
            storage_pct = round(disk.percent, 1)
        except Exception:
            try:
                disk = psutil.disk_usage("/")
                storage_pct = round(disk.percent, 1)
            except Exception:
                storage_pct = 0.0

        if not self._running:
            return {"active": False, "connected": False, "fps": 0, "decode_latency_ms": 0,
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

        fps = round(self.source.source_fps, 1) if self.source else 0.0
        bitrate_mbps = round(fps * 0.14, 1) if fps > 0 else 0.0
        frames_discarded = max(0, self.pipeline_frames_in - self.frames_selected)

        return {
            "active": True,
            "connected": self.source.is_connected if self.source else False,
            "fps": fps,
            "bitrate_mbps": bitrate_mbps,
            "decode_latency_ms": 0.0,
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
    try:
        import httpx
        r = httpx.get(certs_url, timeout=5.0)
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


# ── Edge Security & Authentication Guard ──────────────────────────────────────
EDGE_API_KEY = os.getenv("EDGE_API_KEY", "dvas-edge-field-secret-key")
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
    if x_edge_api_key and x_edge_api_key == EDGE_API_KEY:
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
        if bearer_token == EDGE_API_KEY:
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
                return {"auth_type": "jwt", "user": payload.get("preferred_username") or payload.get("sub")}
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
    """Raw stream upload for large video files directly to disk."""
    filename_encoded = request.headers.get("x-filename", "uploaded_video.mp4")
    import urllib.parse
    raw_name = urllib.parse.unquote(filename_encoded)
    safe_name = os.path.basename(raw_name).strip()
    ALLOWED_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".ts", ".webm", ".srt", ".vtt"}
    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(400, f"Unsupported file extension: {ext}")
        
    upload_dir = Path("/data")
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    video_path = (upload_dir / safe_name).resolve()
    if not str(video_path).startswith(str(upload_dir.resolve())):
        raise HTTPException(400, "Invalid file path traversal detected")
    
    # Stream directly from HTTP request to the final volume on disk
    with open(video_path, "wb") as out_file:
        async for chunk in request.stream():
            out_file.write(chunk)
            
    return {"status": "success", "video_path": str(video_path)}


@app.post("/api/edge/mission/upload-video")
async def upload_video(
    video: UploadFile = File(...),
    srt: Optional[UploadFile] = File(None),
    auth: dict = Depends(verify_edge_auth)
):
    upload_dir = Path("/data")
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    import urllib.parse
    raw_video_name = urllib.parse.unquote(video.filename or "video.mp4")
    safe_video_name = os.path.basename(raw_video_name).strip()
    ALLOWED_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".ts", ".webm", ".srt", ".vtt"}
    video_ext = os.path.splitext(safe_video_name)[1].lower()
    if video_ext not in ALLOWED_EXTS:
        raise HTTPException(400, f"Unsupported file extension: {video_ext}")

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
    upload_dir = Path("/data")
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
    return pipeline.start_mission(req)


@app.post("/api/edge/mission/end")
def end_mission(auth: dict = Depends(verify_edge_auth)):
    return pipeline.end_mission()


@app.post("/api/edge/mission/toggle-ingest")
def toggle_ingest(req: ToggleIngestRequest, auth: dict = Depends(verify_edge_auth)):
    pipeline.server_send_enabled = req.enabled
    return {"server_send_enabled": pipeline.server_send_enabled}


class TogglePipelineRequest(BaseModel):
    enabled: bool


@app.post("/api/edge/mission/toggle-pipeline")
def toggle_pipeline(req: TogglePipelineRequest, auth: dict = Depends(verify_edge_auth)):
    """Toggle change detection + cognitive frame selection pipeline.
    When disabled (bypass), frames are sampled directly at ~1 fps."""
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
            **pipeline.active_mission,
        }
    return {"active": False}


@app.get("/api/edge/mission/history")
def get_edge_mission_history(auth: dict = Depends(verify_edge_auth)):
    return pipeline.get_mission_history()


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
        disk = psutil.disk_usage("/data") if os.path.exists("/data") else psutil.disk_usage("/")
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
INGEST_BASE_URL = os.getenv("INGEST_URL", "http://10.10.55.115/ingest").replace("/frame", "")

@app.get("/api/ingest-server/status")
async def proxy_ingest_status(auth: dict = Depends(verify_edge_auth)):
    async with httpx.AsyncClient(timeout=2.0) as client:
        try:
            resp = await client.get(f"{INGEST_BASE_URL}/status")
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            log.warning(f"Failed to fetch ingest status: {e}")
    return {}


@app.get("/api/ingest-server/status/{drone_id}")
async def proxy_ingest_drone_status(drone_id: str, auth: dict = Depends(verify_edge_auth)):
    async with httpx.AsyncClient(timeout=2.0) as client:
        try:
            resp = await client.get(f"{INGEST_BASE_URL}/status/{drone_id}")
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            log.warning(f"Failed to fetch ingest drone status for {drone_id}: {e}")
    return {}


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765)
