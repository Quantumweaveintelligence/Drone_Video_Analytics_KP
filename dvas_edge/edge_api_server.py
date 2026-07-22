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
import asyncio
import threading
import queue
from typing import Optional

# ── Third party ──────────────────────────────────────────────────────────────
import uvicorn
import psutil
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# ── GPU monitoring (optional) ────────────────────────────────────────────────
try:
    import pynvml
    pynvml.nvmlInit()
    _GPU_HANDLE = pynvml.nvmlDeviceGetHandleByIndex(0)
    _GPU_AVAILABLE = True
except Exception:
    _GPU_HANDLE = None
    _GPU_AVAILABLE = False

# ── Pipeline components ──────────────────────────────────────────────────────
from src.telemetry.telemetry_simulator import TelemetrySimulator
from src.video.drone_simulation_source import DroneSimulationSource
from src.buffer.rolling_frame_buffer import RollingFrameBuffer
from src.change_detection.change_detection_engine import ChangeDetectionEngine
from src.change_detection.change_event import ChangeEvent
from src.cognitive_frame_selector import CognitiveFrameSelector
from src.cognitive_frame_selector.selected_frame import SelectedFrame
from ingest.edge_sender import IngestClient

# ---------------------------------------------------------------------------
# Drone ID → video file mapping
# ---------------------------------------------------------------------------
DRONE_VIDEO_MAP = {
    "DRN-001": "DJI_0110.MP4",
    "DRN-002": "DJI_0111.MP4",
    "DRN-003": "DJI_0112.MP4",
    "DRN-004": "DJI_0113.MP4",
}


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------
class MissionStartRequest(BaseModel):
    mission_id: str
    mission_name: str
    drone_id: str
    operator: str = ""
    location: str = ""


class ToggleIngestRequest(BaseModel):
    enabled: bool

# ---------------------------------------------------------------------------
# Edge sender background thread
# ---------------------------------------------------------------------------
class EdgeSenderThread(threading.Thread):
    def __init__(self, client: IngestClient, max_queue: int = 64) -> None:
        super().__init__(name="EdgeSenderThread", daemon=True)
        self._client   = client
        self._queue:   queue.Queue[Optional[SelectedFrame]] = queue.Queue(maxsize=max_queue)
        self._loop:    Optional[asyncio.AbstractEventLoop] = None
        self._running  = True

    def put_frame(self, frame: SelectedFrame) -> None:
        try:
            self._queue.put_nowait(frame)
        except queue.Full:
            pass

    def stop(self) -> None:
        self._running = False
        self._queue.put_nowait(None)

    def run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._async_run())
        self._loop.close()

    async def _async_run(self) -> None:
        try:
            await self._client.start_mission()
        except Exception:
            pass

        while True:
            try:
                frame = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self._queue.get(timeout=1.0)
                )
            except queue.Empty:
                if not self._running:
                    break
                continue
            if frame is None:
                break
            try:
                jpeg_bytes = frame.encode_jpeg()
                await self._client.send_frame_bytes(
                    frame_bytes  = jpeg_bytes,
                    change_score = frame.change_score,
                    lat          = frame.location.lat   if frame.location.lat   != 0.0 else None,
                    lon          = frame.location.lon   if frame.location.lon   != 0.0 else None,
                    alt          = frame.location.altitude_m if frame.location.altitude_m != 0.0 else None,
                )
            except Exception:
                pass
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
        self.telemetry_simulator: Optional[TelemetrySimulator] = None
        self.source: Optional[DroneSimulationSource] = None
        self.buffer: Optional[RollingFrameBuffer] = None
        self.change_engine: Optional[ChangeDetectionEngine] = None
        self.cognitive_selector: Optional[CognitiveFrameSelector] = None
        self.sender_thread: Optional[EdgeSenderThread] = None
        self.frames_selected: int = 0
        self.server_send_enabled: bool = False
        self._start_wall: float = 0

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_video_file(drone_id: str) -> str:
        """Resolve the video file path for a drone ID."""
        filename = DRONE_VIDEO_MAP.get(drone_id, "DJI_0110.MP4")
        video_dir = os.environ.get("VIDEO_DIR", "")
        if video_dir:
            path = os.path.join(video_dir, filename)
            if os.path.exists(path):
                return path
        # Fallback: look in the script directory
        script_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(script_dir, filename)
        if os.path.exists(path):
            return path
        # Last resort: fallback to DJI_0110.MP4
        for fallback_name in ["DJI_0110.MP4", "DJI_0111.MP4"]:
            for d in [video_dir, script_dir]:
                fb = os.path.join(d, fallback_name) if d else fallback_name
                if os.path.exists(fb):
                    log.warning("video_file_fallback", requested=filename, using=fb)
                    return fb
        return filename  # will fail at open time with a clear error

    # ── Mission lifecycle ─────────────────────────────────────────────────

    def start_mission(self, req: MissionStartRequest) -> dict:
        with self.lock:
            if self._running:
                return {"error": "Mission already running"}

            start_time_str = time.strftime("%H:%M:%S")
            self._start_wall = time.time()
            self._running = True
            self.frames_selected = 0

            # ── Telemetry simulator ───────────────────────────────────────
            self.telemetry_simulator = TelemetrySimulator()

            # ── Rolling frame buffer ──────────────────────────────────────
            self.buffer = RollingFrameBuffer(
                drone_id=req.drone_id,
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

                self.change_engine = ChangeDetectionEngine(
                    drone_id=req.drone_id,
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
                        self.sender_thread.put_frame(frame)

                self.cognitive_selector = CognitiveFrameSelector(
                    drone_id=req.drone_id,
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
                if self.change_engine is not None:
                    try:
                        self.change_engine.push_frame(vf)
                    except Exception:
                        pass

            # ── Video source ──────────────────────────────────────────────
            video_file = self._resolve_video_file(req.drone_id)
            self.source = DroneSimulationSource(
                drone_id=req.drone_id,
                video_files=[video_file],
                loop=True,
                playback_speed=1.0,
                on_frame=_on_frame,
                telemetry_simulator=self.telemetry_simulator,
            )

            # ── Edge Sender Thread ─────────────────────────────────────────
            client = IngestClient(
                username=cfg.ingest.username,
                password=cfg.ingest.password,
                drone_id=req.drone_id,
                mission_id=req.mission_id
            )
            self.sender_thread = EdgeSenderThread(client=client)
            self.sender_thread.start()

            # ── Start all ─────────────────────────────────────────────────
            if self.change_engine is not None:
                self.change_engine.start()
            self.source.start()

            self.active_mission = {
                "mission_id": req.mission_id,
                "mission_name": req.mission_name,
                "drone_id": req.drone_id,
                "operator": req.operator,
                "location": req.location,
                "start_time": start_time_str,
            }

            log.info("mission_started", **self.active_mission, video_file=video_file)
            return {"status": "started", **self.active_mission}

    def end_mission(self) -> dict:
        with self.lock:
            if not self._running:
                return {"error": "No active mission"}

            end_time_str = time.strftime("%H:%M:%S")
            frames_processed = self.source._frame_index if self.source else 0
            mission_id = self.active_mission.get("mission_id", "") if self.active_mission else ""

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

            self._running = False
            self.active_mission = None
            self.source = None
            self.buffer = None
            self.change_engine = None
            self.cognitive_selector = None
            self.telemetry_simulator = None
            self.sender_thread = None
            self.server_send_enabled = False

            log.info("mission_ended", mission_id=mission_id, frames_processed=frames_processed)
            return {
                "status": "ended",
                "mission_id": mission_id,
                "end_time": end_time_str,
                "frames_processed": frames_processed,
            }

    # ── Status / Telemetry ────────────────────────────────────────────────

    def get_status(self) -> dict:
        if not self._running:
            return {"active": False, "connected": False, "fps": 0, "decode_latency_ms": 0,
                    "buffer_fill_pct": 0, "buffer_frames": 0, "buffer_max_frames": 0,
                    "cpu_pct": 0, "gpu_pct": 0, "gpu_mem_used_mb": 0, "gpu_mem_total_mb": 0,
                    "upload_queue": 0, "frames_selected": 0, "frames_total": 0,
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

        return {
            "active": True,
            "connected": self.source.is_connected if self.source else False,
            "fps": round(self.source.source_fps, 1) if self.source else 0.0,
            "decode_latency_ms": 0.0,
            "buffer_fill_pct": round(buf_frames / max(max_frames, 1) * 100, 1),
            "buffer_frames": buf_frames,
            "buffer_max_frames": max_frames,
            "cpu_pct": round(psutil.cpu_percent(), 1),
            "gpu_pct": gpu_pct,
            "gpu_mem_used_mb": gpu_mem_used,
            "gpu_mem_total_mb": gpu_mem_total,
            "upload_queue": 0,
            "frames_selected": self.frames_selected,
            "frames_total": self.source._frame_index if self.source else 0,
            "change_events_total": cd_events,
        }

    def get_telemetry(self) -> dict:
        if not self._running or self.telemetry_simulator is None:
            return {
                "latitude": 0, "longitude": 0, "altitude_m": 0,
                "speed_mps": 0, "heading_deg": 0, "battery_pct": 0,
                "signal_pct": 0, "gps_lock": False,
            }
        return self.telemetry_simulator.get_current()

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
pipeline = PipelineManager()

app = FastAPI(title="DVAS Edge Control API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/edge/mission/start")
def start_mission(req: MissionStartRequest):
    return pipeline.start_mission(req)


@app.post("/api/edge/mission/end")
def end_mission():
    return pipeline.end_mission()

@app.post("/api/edge/mission/toggle-ingest")
def toggle_ingest(req: ToggleIngestRequest):
    pipeline.server_send_enabled = req.enabled
    return {"server_send_enabled": pipeline.server_send_enabled}

@app.get("/api/edge/mission/active")
def get_active_mission():
    if pipeline.active_mission:
        return {"active": True, "server_send_enabled": pipeline.server_send_enabled, **pipeline.active_mission}
    return {"active": False}


@app.get("/api/edge/status")
def get_status():
    return pipeline.get_status()


@app.get("/api/edge/telemetry")
def get_telemetry():
    return pipeline.get_telemetry()


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
async def stream_live():
    return StreamingResponse(
        mjpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765)
