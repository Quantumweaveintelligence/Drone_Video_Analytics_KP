"""
main.py
========
Kerala Police Drone Video Analytics — Unified Edge Layer Entry Point
Tasks 1.1 + 1.2 + 1.3 + 1.4 + 1.5

This single entry point runs the COMPLETE pipeline in one of two modes,
determined automatically from config/settings.yaml:

  ┌─────────────────────────────────────────────────────────────────┐
  │  simulation.enabled: true  →  MP4 file playback (default)       │
  │  simulation.enabled: false →  Live RTSP / webcam stream         │
  └─────────────────────────────────────────────────────────────────┘

Full pipeline (both modes):
───────────────────────────
  VideoSource (Simulation or RTSP)
        │
        ├──► [on_frame fan-out]
        │         ├──► RollingFrameBuffer   (60 s mmap slab, SSD-backed)
        │         └──► ChangeDetectionEngine  (MOG2 + contour persistence)
        │                      │
        │             on_change_event ──► CognitiveFrameSelector
        │                                        │
        │                             on_selected_frame ──► structured log
        │                                               (+ future Kafka publish)
        │
        └──► [frame_queue] ──► FrameInterpolator ──► StreamViewer
                                                      HUD panels:
                                                       • Source / FPS / latency
                                                       • Buffer fill %
                                                       • GPU stats
                                                       • CHANGE DETECTION panel
                                                       • Motion bounding boxes
                                                       • MOTION DETECTED badge

Usage
─────
    python main.py

    # Override simulation mode via env-var (no YAML edit needed):
    set SIMULATION__ENABLED=false && python main.py   # force RTSP mode
    set SIMULATION__PLAYBACK_SPEED=2.0 && python main.py  # 2× speed

Prometheus metrics : http://localhost:8000/metrics   (edge laptop — scrape from Ubuntu Prometheus via VPN)
Live viewer window : opens automatically  (press Esc to quit)
                                          (press W  to toggle RTSP ↔ webcam in RTSP mode)
"""

from __future__ import annotations

import asyncio
import os
import queue
import signal
import threading
import time
from typing import Callable, Optional, Union

# ── Config (must be first — other modules read the cache) ────────────────────
from config.config import get_config, reset_config

reset_config()          # ensure a clean load on every run
cfg = get_config()

# ── Logging ──────────────────────────────────────────────────────────────────
from src.utils.logger import get_logger, setup_logging

setup_logging(
    level=cfg.logging.level,
    json_output=cfg.logging.json_output,
    log_file=cfg.logging.log_file,
)
log = get_logger(__name__)

# ── Pipeline imports ──────────────────────────────────────────────────────────
from src.buffer.rolling_frame_buffer import RollingFrameBuffer
from src.change_detection.change_detection_engine import ChangeDetectionEngine
from src.change_detection.change_event import ChangeEvent
from src.cognitive_frame_selector import CognitiveFrameSelector
from src.cognitive_frame_selector.selected_frame import SelectedFrame
from src.metrics.prometheus_metrics import start_metrics_server
from src.utils.gpu_utils import detect_gpu
from src.video.drone_simulation_source import DroneSimulationSource
from src.video.drone_video_source import DroneVideoSource
from src.video.frame_interpolator import FrameInterpolator
from src.video.stream_viewer import StreamViewer
from core.camera_source import VideoFrame
from core.models import SelectedFrame, LocationData
from ingest.edge_sender import IngestClient
from ingest.offline_buffer import OfflineBuffer


# ---------------------------------------------------------------------------
# Edge sender background thread
# ---------------------------------------------------------------------------

class EdgeSenderThread(threading.Thread):
    """
    Background daemon thread that owns a single asyncio event loop and
    sends SelectedFrame objects to the Ubuntu ingest server via IngestClient.

    Call start() once, enqueue frames via put_frame(), call stop() on shutdown.
    The thread starts a mission on startup and ends it on shutdown.
    Frames are JPEG-encoded in memory — no temp files are written.
    """

    def __init__(
        self,
        client: IngestClient,
        max_queue: int = 64,
        mission_name: Optional[str] = None,
        operator: Optional[str] = None,
        location: Optional[str] = None,
    ) -> None:
        super().__init__(name="EdgeSenderThread", daemon=True)
        self._client   = client
        self._mission_name = mission_name
        self._operator = operator
        self._location = location
        self._buffer   = OfflineBuffer()
        self._loop:    Optional[asyncio.AbstractEventLoop] = None
        self._started  = threading.Event()
        self._running  = True

    def put_frame(self, frame: SelectedFrame) -> None:
        """Enqueue a SelectedFrame for transmission."""
        jpeg_bytes = frame.encode_jpeg()
        tel = {
            "lat": frame.location.lat if frame.location.lat != 0.0 else None,
            "lon": frame.location.lon if frame.location.lon != 0.0 else None,
            "alt": frame.location.altitude_m if frame.location.altitude_m != 0.0 else None,
            "change_score": frame.change_score,
            "frames_total": frame.frame_index
        }
        self._buffer.put("selected", jpeg_bytes, tel)

    def stop(self) -> None:
        """Signal the thread to drain remaining frames and exit."""
        self._running = False
        with self._buffer._cond:
            self._buffer._cond.notify_all()

    def run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._async_run())
        self._loop.close()

    async def _async_run(self) -> None:
        # Start mission lifecycle
        try:
            await self._client.start_mission(
                mission_name=self._mission_name,
                operator=self._operator,
                location=self._location,
            )
        except Exception as exc:
            log.error("edge_sender_mission_start_failed", error=str(exc))
            # Continue anyway — individual frames will still be attempted

        self._started.set()

        while self._running:
            item = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._buffer.get(timeout=1.0)
            )

            if item is None:
                continue

            frame_id, kind, jpeg_bytes, tel = item

            try:
                await self._client.send_frame_bytes(
                    frame_bytes  = jpeg_bytes,
                    change_score = tel.get("change_score", 0.0),
                    lat          = tel.get("lat"),
                    lon          = tel.get("lon"),
                    alt          = tel.get("alt"),
                    frames_total = tel.get("frames_total")
                )
                self._buffer.commit(frame_id)
            except Exception as exc:
                log.error("edge_sender_frame_failed",
                          frame_id=frame_id, error=str(exc))
                await asyncio.sleep(2.0)
                
        self._buffer.close()

        # End mission lifecycle
        try:
            await self._client.end_mission()
        except Exception as exc:
            log.error("edge_sender_mission_end_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Frame fan-out: delivers each VideoFrame to multiple consumers
# ---------------------------------------------------------------------------

class _FrameFanOut:
    """
    Sends each VideoFrame to every registered consumer callback in order.
    All calls are synchronous in the source's writer thread — keep them fast.
    Used when both the RollingFrameBuffer and ChangeDetectionEngine need
    to receive every frame from the same source.
    """
    def __init__(self):
        self._consumers = []

    def add(self, fn) -> None:
        self._consumers.append(fn)

    def __call__(self, video_frame) -> None:
        for fn in self._consumers:
            try:
                fn(video_frame)
            except Exception as exc:
                log.error("fanout_consumer_error", consumer=getattr(fn, "__name__", str(fn)), error=str(exc))


# ---------------------------------------------------------------------------
# Selected frame callback
# ---------------------------------------------------------------------------

def _on_selected_frame(frame: SelectedFrame) -> None:
    """
    Invoked by CognitiveFrameSelector for every frame that passes the
    priority threshold.

    Logs the selection and enqueues the frame for transmission to the
    Ubuntu ingest server via EdgeSenderThread (set by main() at startup).
    """
    log.info(
        "cognitive_frame_selected",
        drone_id=frame.drone_id,
        mission_id=frame.mission_id,
        frame_id=frame.frame_id,
        frame_index=frame.frame_index,
        utc_timestamp_ms=frame.utc_timestamp_ms,
        cognitive_score=frame.cognitive_score,
        change_score=frame.change_score,
        selection_reason=frame.selection_reason,
        motion_region_count=len(frame.motion_regions),
    )
    # Transmit to ingest server if the sender thread is running
    if _edge_sender_thread is not None:
        _edge_sender_thread.put_frame(frame)


# Module-level reference set by main() before threads start
_edge_sender_thread: Optional[EdgeSenderThread] = None


# ---------------------------------------------------------------------------
# Change event callback
# ---------------------------------------------------------------------------

def _make_change_event_handler(
    cognitive_selector: Optional[CognitiveFrameSelector],
) -> "Callable[[ChangeEvent], None]":
    """
    Returns the on_change_event callback, closed over the CognitiveFrameSelector
    instance.  This avoids a global variable while keeping the callback signature
    compatible with ChangeDetectionEngine.
    """
    def _on_change_event(event: ChangeEvent) -> None:
        # ── Always log the change event (Task 1.3 requirement) ───────────────
        log.info(
            "change_event_confirmed",
            drone_id=event.drone_id,
            frame_index=event.frame_index,
            timestamp_ms=event.timestamp_ms,
            motion_score=round(event.motion_score, 4),
            region_count=event.region_count,
            stage1_pixel_pct=round(event.stage1_pixel_changed_pct, 4),
            stage2_blobs=event.stage2_blobs_detected,
            largest_region=(
                {
                    "x": event.largest_region.x,
                    "y": event.largest_region.y,
                    "w": event.largest_region.w,
                    "h": event.largest_region.h,
                    "area": event.largest_region.area,
                    "persistence": event.largest_region.persistence,
                }
                if event.largest_region else None
            ),
        )

        # ── Pass to CognitiveFrameSelector (Task 1.5) if enabled ───────────
        if cognitive_selector is not None:
            cognitive_selector.evaluate(event)
        else:
            # Bypass mode: send all change events directly
            from src.cognitive_frame_selector.selected_frame import SelectedFrame, GpsLocation
            frame = SelectedFrame.create(
                drone_id=event.drone_id,
                mission_id=cfg.ingest.mission_id,
                utc_timestamp_ms=event.timestamp_ms,
                frame_index=event.frame_index,
                raw_frame=event.frame,
                change_score=event.motion_score,
                cognitive_score=1.0,
                selection_reason="bypass_cfs",
                location=GpsLocation(lat=0.0, lon=0.0, altitude_m=0.0),
                motion_regions=[
                    {
                        "x": event.largest_region.x,
                        "y": event.largest_region.y,
                        "w": event.largest_region.w,
                        "h": event.largest_region.h,
                        "area": event.largest_region.area,
                        "persistence": event.largest_region.persistence,
                    }
                ] if event.largest_region else []
            )
            _on_selected_frame(frame)

    return _on_change_event

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    global _edge_sender_thread

    gpu_info = detect_gpu()

    sim_cfg = cfg.simulation
    cd_cfg  = cfg.change_detection

    # ── Decide source mode ────────────────────────────────────────────────
    use_simulation = sim_cfg.enabled and bool(sim_cfg.video_files)

    if use_simulation:
        mode_label = "SIMULATION (MP4)"
        log.info(
            "pipeline_starting",
            mode=mode_label,
            video_files=sim_cfg.video_files,
            playback_speed=sim_cfg.playback_speed,
            loop=sim_cfg.loop,
            change_detection=cd_cfg.enabled,
            buffer_duration_s=cfg.buffer.duration_s,
            prometheus_port=cfg.metrics.prometheus_port,
            gpu_device=gpu_info.device_name,
        )
    else:
        mode_label = "RTSP / WEBCAM"
        log.info(
            "pipeline_starting",
            mode=mode_label,
            rtsp_url=DroneVideoSource._sanitise_url(cfg.drone.rtsp_url),
            rtsp_only=cfg.drone.rtsp_only,
            use_gpu=cfg.drone.use_gpu,
            change_detection=cd_cfg.enabled,
            buffer_duration_s=cfg.buffer.duration_s,
            prometheus_port=cfg.metrics.prometheus_port,
            gpu_device=gpu_info.device_name,
            gpu_cuvid=gpu_info.cuvid_available,
        )

    # ── Prometheus metrics server ─────────────────────────────────────────
    start_metrics_server(port=cfg.metrics.prometheus_port)
    log.info("prometheus_started", port=cfg.metrics.prometheus_port)

    # ── Edge Sender Thread (transmit cognitive-selected frames to Ubuntu) ──
    ing_cfg = cfg.ingest
    # Propagate config values into edge_sender module-level vars at import time
    # so that IngestClient picks them up correctly
    import ingest.edge_sender as _es_mod
    _es_mod.INGEST_URL         = ing_cfg.url
    _es_mod.TOKEN_URL          = ing_cfg.token_url
    _es_mod.CLIENT_ID          = ing_cfg.client_id
    _es_mod.CLIENT_SEC         = ing_cfg.client_secret
    _es_mod.TLS_VERIFY         = ing_cfg.tls_verify
    _es_mod.TLS_CA_CERT        = ing_cfg.ca_cert

    if ing_cfg.enabled:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        ingest_client = IngestClient(
            drone_id   = ing_cfg.drone_id,
            mission_id = ing_cfg.mission_id,
            username   = ing_cfg.username,
            password   = ing_cfg.password,
        )
        
        # Check if the config has these new fields (backward compatibility if missing)
        mission_name = getattr(ing_cfg, "mission_name", "Vehicle Tracking")
        operator = getattr(ing_cfg, "operator", "K Rajan")
        location = getattr(ing_cfg, "location", "Trivandrum")
        
        _edge_sender_thread = EdgeSenderThread(
            client=ingest_client, 
            max_queue=64,
            mission_name=mission_name,
            operator=operator,
            location=location,
        )
        _edge_sender_thread.start()
        log.info(
            "edge_sender_started",
            ingest_url  = ing_cfg.url,
            drone_id    = ing_cfg.drone_id,
            mission_id  = ing_cfg.mission_id,
            mission_name= mission_name,
            operator    = operator,
            tls_verify  = ing_cfg.tls_verify,
        )
    else:
        log.info("edge_sender_disabled_in_config")

    # ── Rolling Frame Buffer (always active) ──────────────────────────────
    buffer = RollingFrameBuffer(
        drone_id="drone_01",
        duration_s=cfg.buffer.duration_s,
        storage_dir=cfg.buffer.storage_dir,
        jpeg_quality=cfg.buffer.jpeg_quality,
        max_frames=cfg.buffer.max_frames,
        mmap_slab_size_bytes=cfg.buffer.mmap_slab_size_bytes,
    )

    cfs_cfg = cfg.cognitive_frame_selector

    # -- Cognitive Frame Selector (Task 1.5) ----------------------------------
    cognitive_selector: Optional[CognitiveFrameSelector] = None
    if cfs_cfg.enabled and cd_cfg.enabled:
        cognitive_selector = CognitiveFrameSelector(
            drone_id="drone_01",
            mission_id=cfs_cfg.mission_id,
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
        log.info(
            "cognitive_frame_selector_configured",
            transmission_target=cfs_cfg.transmission_target,
            window_size=cfs_cfg.window_size,
            emergency_priority=cfs_cfg.emergency_priority,
            mission_id=cfs_cfg.mission_id,
        )
    elif not cfs_cfg.enabled:
        log.info("cognitive_frame_selector_disabled_in_config")
    else:
        log.info("cognitive_frame_selector_skipped_change_detection_disabled")

    # Build the change-event handler, closed over the (possibly None) selector
    change_event_handler = _make_change_event_handler(cognitive_selector)

    # -- Change Detection Engine -----------------------------------------------
    change_engine: Optional[ChangeDetectionEngine] = None
    if cd_cfg.enabled:
        change_engine = ChangeDetectionEngine(
            drone_id="drone_01",
            mog2_history=cd_cfg.mog2_history,
            mog2_var_threshold=cd_cfg.mog2_var_threshold,
            detect_shadows=cd_cfg.detect_shadows,
            min_contour_area=cd_cfg.min_contour_area,
            motion_score_threshold=cd_cfg.motion_score_threshold,
            persistence_frames=cd_cfg.persistence_frames,
            max_queue_size=cd_cfg.max_queue_size,
            on_change_event=change_event_handler,
            emit_raw_mask=cd_cfg.emit_raw_mask,
            emit_raw_frame=cd_cfg.emit_raw_frame,
        )
        log.info(
            "change_detection_engine_configured",
            mog2_history=cd_cfg.mog2_history,
            mog2_var_threshold=cd_cfg.mog2_var_threshold,
            motion_score_threshold=cd_cfg.motion_score_threshold,
            persistence_frames=cd_cfg.persistence_frames,
            emit_raw_frame=cd_cfg.emit_raw_frame,
        )
    else:
        log.info("change_detection_disabled_in_config")

    # ── Frame fan-out: buffer + change detection both receive every frame ─
    fanout = _FrameFanOut()
    fanout.add(buffer.write_video_frame)
    if change_engine is not None:
        fanout.add(change_engine.push_frame)

    # ── Video source ──────────────────────────────────────────────────────
    source: Union[DroneSimulationSource, DroneVideoSource]

    if use_simulation:
        source = DroneSimulationSource(
            drone_id="drone_01",
            video_files=sim_cfg.video_files,
            loop=sim_cfg.loop,
            playback_speed=sim_cfg.playback_speed,
            frame_queue_size=8,
            on_frame=fanout,
        )
    else:
        source = DroneVideoSource(
            drone_id="drone_01",
            rtsp_url=cfg.drone.rtsp_url,
            usb_device_index=cfg.drone.usb_device_index,
            webcam_device_index=cfg.drone.webcam_device_index,
            preferred_source=cfg.drone.preferred_source,
            rtsp_only=cfg.drone.rtsp_only,
            use_gpu=cfg.drone.use_gpu,
            rtsp_transport=cfg.drone.rtsp_transport,
            gstreamer_pipeline=cfg.drone.gstreamer_pipeline,
            reconnect_base_delay_s=cfg.drone.reconnect_base_delay_s,
            reconnect_multiplier=cfg.drone.reconnect_multiplier,
            reconnect_max_delay_s=cfg.drone.reconnect_max_delay_s,
            read_timeout_s=cfg.drone.read_timeout_s,
            on_frame=fanout,
        )

    # ── Frame interpolator: doubles effective FPS for the viewer ──────────
    interpolator = FrameInterpolator(
        source=source,
        target_fps=25.0,
    )

    # ── Live viewer window (must run on main thread) ───────────────────────
    viewer = StreamViewer(
        source=source,
        buffer=buffer,
        buffer_capacity=cfg.buffer.max_frames,
        interpolator=interpolator,
        change_engine=change_engine,
    )

    # ── Graceful shutdown ──────────────────────────────────────────────────
    shutdown_requested = [False]

    def _shutdown(signum, frame):
        shutdown_requested[0] = True
        log.info("shutdown_signal_received", signal=signum)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # ── Start all background threads ──────────────────────────────────────
    if change_engine is not None:
        change_engine.start()

    source.start()
    interpolator.start()
    viewer.start()   # starts background preprocess/resize thread

    log.info(
        "pipeline_running",
        mode=mode_label,
        hint="Press Esc in the video window to quit."
             + ("" if use_simulation else "  Press W to toggle RTSP/Webcam."),
    )

    # ── Main loop (drives OpenCV display — must be on main thread) ────────
    heartbeat_interval = cfg.metrics.heartbeat_log_interval_s
    last_heartbeat     = time.monotonic()

    try:
        while not shutdown_requested[0]:
            if not viewer.tick(wait_ms=1):
                log.info("viewer_window_closed_initiating_shutdown")
                break

            # When simulation playlist ends (loop=false), stop cleanly
            if (
                use_simulation
                and not sim_cfg.loop
                and not source.is_connected
                and source._frame_index > 0
            ):
                log.info("simulation_playlist_exhausted_stopping")
                break

            now = time.monotonic()
            if now - last_heartbeat >= heartbeat_interval:
                _log_heartbeat(source, buffer, interpolator, change_engine, cognitive_selector)
                last_heartbeat = now

    finally:
        log.info("shutting_down_all_components")
        viewer.stop()          # stops preprocess thread + destroys window
        interpolator.stop()
        source.stop()
        if change_engine is not None:
            change_engine.stop()
        if cognitive_selector is not None:
            cfs_stats = cognitive_selector.get_stats()
            log.info(
                "cognitive_frame_selector_final_stats",
                frames_evaluated=cfs_stats["frames_evaluated"],
                frames_selected=cfs_stats["frames_selected"],
                selection_rate=cfs_stats["selection_rate"],
                target_rate=cfs_stats["target_rate"],
            )
        if _edge_sender_thread is not None:
            _edge_sender_thread.stop()
            _edge_sender_thread.join(timeout=15.0)
            log.info("edge_sender_stopped")
        buffer.close()
        log.info("pipeline_fully_stopped")


# ---------------------------------------------------------------------------
# Heartbeat logger
# ---------------------------------------------------------------------------

def _log_heartbeat(
    source,
    buffer: RollingFrameBuffer,
    interpolator: FrameInterpolator,
    change_engine: Optional[ChangeDetectionEngine],
    cognitive_selector: Optional[CognitiveFrameSelector] = None,
) -> None:
    """Emit a structured status log every heartbeat interval."""
    frame_count = buffer.get_frame_count()
    oldest_ts   = buffer.get_oldest_timestamp_ms()
    newest_ts   = buffer.get_newest_timestamp_ms()

    window_s: float = 0.0
    if oldest_ts is not None and newest_ts is not None:
        window_s = (newest_ts - oldest_ts) / 1000.0

    cd  = change_engine.get_stats()       if change_engine       is not None else {}
    cfs = cognitive_selector.get_stats()  if cognitive_selector  is not None else {}

    # Detect source type for decoder info
    using_cuvid = getattr(source, "_using_cuvid", False)
    src_type    = getattr(source, "active_source_type", "unknown")

    log.info(
        "heartbeat",
        mode                        = src_type,
        drone_connected             = source.is_connected,
        source_fps                  = round(source.source_fps, 1),
        effective_fps               = round(interpolator.effective_fps, 1),
        synthetic_frames            = interpolator._synthetic_total,
        total_frames_decoded        = source._frame_index,
        decoder                     = "h264_cuvid (NVDEC)" if using_cuvid else "software/file",
        buffer_frames               = frame_count,
        buffer_window_s             = round(window_s, 1),
        cd_enabled                  = change_engine is not None,
        cd_events_total             = cd.get("events_total", 0),
        cd_events_per_minute        = round(cd.get("events_per_minute", 0.0), 1),
        cd_last_motion_score        = round(cd.get("last_motion_score", 0.0), 4),
        cd_frames_processed         = cd.get("frames_processed", 0),
        cd_queue_depth              = cd.get("queue_depth", 0),
        cfs_enabled                 = cognitive_selector is not None,
        cfs_frames_evaluated        = cfs.get("frames_evaluated", 0),
        cfs_frames_selected         = cfs.get("frames_selected", 0),
        cfs_selection_rate          = round(cfs.get("selection_rate", 0.0), 3),
        cfs_target_rate             = cfs.get("target_rate", 0.0),
    )


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    main()
