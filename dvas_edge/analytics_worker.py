"""
analytics_worker.py
====================
Kerala Police Drone Analytics — Headless Docker Worker Entry Point

This is the Docker-compatible version of main.py. It runs the full pipeline:
  VideoSource → RollingFrameBuffer → ChangeDetectionEngine → CognitiveFrameSelector

WITHOUT the OpenCV GUI window (stream_viewer is disabled for headless operation).

Selected frames are published to Redis Streams (same Redis as the ingest server).
Stream key: drone:analytics:{drone_id}

Environment overrides (all optional):
  SIMULATION__ENABLED=true/false
  DRONE__RTSP_URL=rtsp://...
  METRICS__PROMETHEUS_PORT=9090
  REDIS_URL=redis://redis:6379/0
  DRONE_ID=drone_01
  MISSION_ID=MISSION_DEFAULT
"""

from __future__ import annotations

import json
import os
import signal
import time
from typing import Optional, Union

# ── Config ────────────────────────────────────────────────────────────────────
from config.config import get_config, reset_config

reset_config()
cfg = get_config()

# ── Logging ───────────────────────────────────────────────────────────────────
from src.utils.logger import get_logger, setup_logging

setup_logging(
    level=cfg.logging.level,
    json_output=True,          # Always JSON in Docker
    log_file=None,             # stdout only in containers
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

# ── Redis Streams publisher (optional — non-fatal if unavailable) ─────────────
REDIS_URL  = os.getenv("REDIS_URL",  "redis://redis:6379/0")
DRONE_ID   = os.getenv("DRONE_ID",   "drone_01")
MISSION_ID = os.getenv("MISSION_ID", cfg.cognitive_frame_selector.mission_id)

_redis_client  = None
_redis_available = False


def _init_redis() -> None:
    """Connect to Redis Streams for analytics frame publishing."""
    global _redis_client, _redis_available
    try:
        import redis
        _redis_client   = redis.Redis.from_url(
            REDIS_URL,
            decode_responses=False,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
        )
        _redis_client.ping()
        _redis_available = True
        log.info("redis_connected_for_analytics", extra={"drone_id": DRONE_ID})
    except Exception as exc:
        log.warning("redis_unavailable_for_analytics", extra={"error": str(exc)})
        _redis_available = False


def _publish_selected_frame(frame: SelectedFrame) -> None:
    """Publish a cognitively-selected frame to Redis Streams."""
    if not _redis_available or _redis_client is None:
        return
    try:
        import json as _json
        stream_key = f"drone:analytics:{DRONE_ID}"
        _redis_client.xadd(
            stream_key,
            {
                b"frame":             frame.jpeg_bytes if hasattr(frame, "jpeg_bytes") else b"",
                b"drone_id":          frame.drone_id.encode(),
                b"mission_id":        frame.mission_id.encode(),
                b"frame_id":          str(frame.frame_id).encode(),
                b"cognitive_score":   str(round(frame.cognitive_score, 4)).encode(),
                b"change_score":      str(round(frame.change_score, 4)).encode(),
                b"selection_reason":  frame.selection_reason.encode(),
                b"ts_ms":             str(int(time.time() * 1000)).encode(),
            },
            maxlen=500,
            approximate=True,
        )
    except Exception as exc:
        log.warning("frame_publish_failed", extra={"error": str(exc)})


# ── Frame fan-out ─────────────────────────────────────────────────────────────

class _FrameFanOut:
    def __init__(self):
        self._consumers = []

    def add(self, fn):
        self._consumers.append(fn)

    def __call__(self, video_frame):
        for fn in self._consumers:
            try:
                fn(video_frame)
            except Exception as exc:
                log.error("fanout_error", extra={"error": str(exc)})


# ── Callbacks ─────────────────────────────────────────────────────────────────

def _on_selected_frame(frame: SelectedFrame) -> None:
    log.info(
        "cognitive_frame_selected",
        extra={
            "drone_id":        frame.drone_id,
            "frame_id":        frame.frame_id,
            "cognitive_score": frame.cognitive_score,
            "change_score":    frame.change_score,
            "selection_reason": frame.selection_reason,
        },
    )
    _publish_selected_frame(frame)


def _make_change_event_handler(selector: Optional[CognitiveFrameSelector]):
    def _on_change(event: ChangeEvent) -> None:
        log.info(
            "change_event",
            extra={
                "drone_id":     event.drone_id,
                "frame_index":  event.frame_index,
                "motion_score": round(event.motion_score, 4),
                "region_count": event.region_count,
            },
        )
        if selector is not None:
            selector.evaluate(event)
    return _on_change


# ── Heartbeat ─────────────────────────────────────────────────────────────────

def _heartbeat(source, buffer, change_engine, selector, frame_count: list) -> None:
    frame_count[0] += 1
    if frame_count[0] % 300 == 0:     # Every ~10s at 30fps
        cd  = change_engine.get_stats() if change_engine else {}
        cfs = selector.get_stats()      if selector      else {}
        log.info(
        "heartbeat",
        extra={
            "frames_decoded":    source._frame_index,
            "buffer_frames":     buffer.get_frame_count(),
            "cd_events":         cd.get("events_total", 0),
            "cfs_selected":      cfs.get("frames_selected", 0),
            "source_fps":        round(source.source_fps, 1),
            "redis_available":   _redis_available,
        },
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    gpu_info = detect_gpu()
    sim_cfg  = cfg.simulation
    cd_cfg   = cfg.change_detection

    log.info(
        "analytics_worker_starting",
        extra={
            "mode":       "simulation" if sim_cfg.enabled else "rtsp",
            "gpu":        gpu_info.device_name,
            "drone_id":   DRONE_ID,
            "mission_id": MISSION_ID,
            "redis":      REDIS_URL.split("@")[-1],
        },
    )

    # Try connecting to Redis Streams for selected frame publishing
    _init_redis()

    # Prometheus metrics on a different port from ingest API
    prom_port = int(os.getenv("METRICS__PROMETHEUS_PORT", str(cfg.metrics.prometheus_port)))
    start_metrics_server(port=prom_port)
    log.info("prometheus_started", extra={"port": prom_port})

    # Rolling Frame Buffer
    buffer = RollingFrameBuffer(
        drone_id             = DRONE_ID,
        duration_s           = cfg.buffer.duration_s,
        storage_dir          = cfg.buffer.storage_dir,
        jpeg_quality         = cfg.buffer.jpeg_quality,
        max_frames           = cfg.buffer.max_frames,
        mmap_slab_size_bytes = cfg.buffer.mmap_slab_size_bytes,
    )

    # Cognitive Frame Selector
    cfs_cfg  = cfg.cognitive_frame_selector
    selector: Optional[CognitiveFrameSelector] = None
    if cfs_cfg.enabled and cd_cfg.enabled:
        selector = CognitiveFrameSelector(
            drone_id                  = DRONE_ID,
            mission_id                = MISSION_ID,
            transmission_target       = cfs_cfg.transmission_target,
            window_size               = cfs_cfg.window_size,
            percentile_threshold      = cfs_cfg.percentile_threshold,
            absolute_min_priority     = cfs_cfg.absolute_min_priority,
            emergency_priority        = cfs_cfg.emergency_priority,
            vehicle_boost             = cfs_cfg.vehicle_boost,
            scene_change_boost        = cfs_cfg.scene_change_boost,
            crowd_boost               = cfs_cfg.crowd_boost,
            weights_histogram         = cfs_cfg.weights_histogram,
            weights_motion            = cfs_cfg.weights_motion,
            weights_object            = cfs_cfg.weights_object,
            weights_context           = cfs_cfg.weights_context,
            histogram_bins            = cfs_cfg.histogram_bins,
            motion_threshold_low      = cfs_cfg.motion_threshold_low,
            motion_threshold_med      = cfs_cfg.motion_threshold_med,
            motion_threshold_high     = cfs_cfg.motion_threshold_high,
            object_brightness_threshold = cfs_cfg.object_brightness_threshold,
            edge_threshold_low        = cfs_cfg.edge_threshold_low,
            edge_threshold_high       = cfs_cfg.edge_threshold_high,
            jpeg_quality              = cfs_cfg.jpeg_quality,
            on_selected_frame         = _on_selected_frame,
        )
        log.info("cognitive_selector_ready")

    change_handler = _make_change_event_handler(selector)

    # Change Detection Engine
    change_engine: Optional[ChangeDetectionEngine] = None
    if cd_cfg.enabled:
        change_engine = ChangeDetectionEngine(
            drone_id              = DRONE_ID,
            mog2_history          = cd_cfg.mog2_history,
            mog2_var_threshold    = cd_cfg.mog2_var_threshold,
            detect_shadows        = cd_cfg.detect_shadows,
            min_contour_area      = cd_cfg.min_contour_area,
            motion_score_threshold= cd_cfg.motion_score_threshold,
            persistence_frames    = cd_cfg.persistence_frames,
            max_queue_size        = cd_cfg.max_queue_size,
            on_change_event       = change_handler,
            emit_raw_mask         = cd_cfg.emit_raw_mask,
            emit_raw_frame        = cd_cfg.emit_raw_frame,
        )

    # Fan-out: buffer + change detection
    frame_count = [0]
    fanout = _FrameFanOut()
    fanout.add(buffer.write_video_frame)
    if change_engine:
        fanout.add(change_engine.push_frame)

    def _on_frame_with_heartbeat(vf):
        fanout(vf)
        _heartbeat(source, buffer, change_engine, selector, frame_count)

    # Video source
    use_simulation = sim_cfg.enabled and bool(sim_cfg.video_files)
    source: Union[DroneSimulationSource, DroneVideoSource]

    if use_simulation:
        source = DroneSimulationSource(
            drone_id       = DRONE_ID,
            video_files    = sim_cfg.video_files,
            loop           = sim_cfg.loop,
            playback_speed = sim_cfg.playback_speed,
            frame_queue_size = 8,
            on_frame       = _on_frame_with_heartbeat,
        )
    else:
        source = DroneVideoSource(
            drone_id               = DRONE_ID,
            rtsp_url               = cfg.drone.rtsp_url,
            usb_device_index       = cfg.drone.usb_device_index,
            webcam_device_index    = cfg.drone.webcam_device_index,
            preferred_source       = cfg.drone.preferred_source,
            rtsp_only              = cfg.drone.rtsp_only,
            use_gpu                = cfg.drone.use_gpu,
            rtsp_transport         = cfg.drone.rtsp_transport,
            gstreamer_pipeline     = cfg.drone.gstreamer_pipeline,
            reconnect_base_delay_s = cfg.drone.reconnect_base_delay_s,
            reconnect_multiplier   = cfg.drone.reconnect_multiplier,
            reconnect_max_delay_s  = cfg.drone.reconnect_max_delay_s,
            read_timeout_s         = cfg.drone.read_timeout_s,
            on_frame               = _on_frame_with_heartbeat,
        )

    # Graceful shutdown
    shutdown_requested = [False]

    def _shutdown(signum, frame_):
        shutdown_requested[0] = True
        log.info("shutdown_signal_received", extra={"signal": signum})

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Start pipeline
    if change_engine:
        change_engine.start()
    source.start()

    log.info(
        "analytics_worker_running",
        extra={
            "mode":     "SIMULATION" if use_simulation else "RTSP",
            "headless": True,
        },
    )

    try:
        while not shutdown_requested[0]:
            time.sleep(0.5)

            # Stop when simulation playlist ends (loop=false)
            if (
                use_simulation
                and not sim_cfg.loop
                and not source.is_connected
                and source._frame_index > 0
            ):
                log.info("simulation_playlist_exhausted")
                break

    finally:
        log.info("analytics_worker_stopping")
        source.stop()
        if change_engine:
            change_engine.stop()
        if selector:
            stats = selector.get_stats()
            log.info("final_selector_stats", extra=stats)
        buffer.close()
        log.info("analytics_worker_stopped")


if __name__ == "__main__":
    main()
