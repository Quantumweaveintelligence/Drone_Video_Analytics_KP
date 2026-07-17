"""
config/config.py
================
Configuration loader — pydantic-settings when available, plain dataclasses otherwise.
Priority: env-vars > settings.yaml > hard-coded defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml

# ── Optional pydantic ────────────────────────────────────────────────────────
try:
    from pydantic import field_validator
    from pydantic_settings import BaseSettings, SettingsConfigDict
    _PYDANTIC = True
except ImportError:
    _PYDANTIC = False


# ── Plain-dataclass config (always available) ────────────────────────────────

@dataclass
class DroneConfig:
    rtsp_url: str = "rtsp://admin:Admin12345@10.10.32.23:554/h264/ch1/main/av_stream"
    usb_device_index: int = 0
    webcam_device_index: int = 0   # laptop webcam for testing (W key toggle)
    preferred_source: str = "rtsp"
    rtsp_only: bool = True   # when True, never fall back to USB/webcam
    use_gpu: bool = True     # use FFMPEG h264_cuvid NVDEC hardware decode
    rtsp_transport: str = "tcp"
    gstreamer_pipeline: str = ""
    reconnect_base_delay_s: float = 1.0
    reconnect_multiplier: float = 2.0
    reconnect_max_delay_s: float = 60.0
    read_timeout_s: float = 5.0


@dataclass
class BufferConfig:
    duration_s: int = 60
    storage_dir: str = "./buffer_storage"
    jpeg_quality: int = 85
    max_frames: int = 2200
    mmap_slab_size_bytes: int = 128 * 1024 * 1024


@dataclass
class MetricsConfig:
    prometheus_port: int = 8000
    heartbeat_log_interval_s: int = 10


@dataclass
class LoggingConfig:
    level: str = "INFO"
    json_output: bool = False
    log_file: str = "./logs/edge.log"


@dataclass
class SimulationConfig:
    """Configuration for the DroneSimulationSource (MP4 file playback)."""
    enabled: bool = True
    video_files: List[str] = field(default_factory=list)
    loop: bool = True
    playback_speed: float = 1.0


@dataclass
class ChangeDetectionConfig:
    """Configuration for the ChangeDetectionEngine (Task 1.3)."""
    enabled: bool = True
    mog2_history: int = 500
    mog2_var_threshold: float = 25.0
    detect_shadows: bool = True
    min_contour_area: int = 400
    motion_score_threshold: float = 0.02
    persistence_frames: int = 3
    max_queue_size: int = 64
    emit_raw_mask: bool = True
    emit_raw_frame: bool = True   # required by CognitiveFrameSelector


@dataclass
class CognitiveFrameSelectorConfig:
    """
    Configuration for the CognitiveFrameSelector (Task 1.5).

    All float knobs correspond directly to the parameters described in
    final_key_frame_extraction.py and the DVAS Production Roadmap Week 8.
    """
    enabled: bool = True
    transmission_target: float = 0.35   # target fraction of frames to select
    window_size: int = 15               # sliding window depth for percentile ranking
    percentile_threshold: float = 0.65  # select if priority >= this percentile
    absolute_min_priority: float = 0.05 # never select below this
    emergency_priority: float = 0.60    # always select at or above this
    vehicle_boost: float = 0.30
    scene_change_boost: float = 0.30
    crowd_boost: float = 0.30
    weights_histogram: float = 0.20
    weights_motion: float = 0.40
    weights_object: float = 0.30
    weights_context: float = 0.10
    histogram_bins: int = 16
    motion_threshold_low: int = 10
    motion_threshold_med: int = 20
    motion_threshold_high: int = 30
    object_brightness_threshold: int = 150
    edge_threshold_low: int = 30
    edge_threshold_high: int = 100
    max_queue_size: int = 32
    jpeg_quality: int = 85
    mission_id: str = "MISSION_DEFAULT"


@dataclass
class IngestConfig:
    """Connection settings for the Ubuntu ingest server."""
    url: str = "https://ingest.dvas.internal/ingest/frame"
    mission_start_url: str = "https://ingest.dvas.internal/ingest/mission/start"
    mission_end_url: str = "https://ingest.dvas.internal/ingest/mission/end"
    token_url: str = "http://10.10.20.1:8080/realms/kerala-police-drone/protocol/openid-connect/token"
    client_id: str = "drone-ingest-client"
    client_secret: str = ""
    username: str = "operator_001"
    password: str = ""
    drone_id: str = "DRONE_KL_001"
    mission_id: str = "MISSION_DEFAULT"
    tls_verify: bool = False
    ca_cert: str = ""   # path to CA cert file; used when tls_verify=True
    enabled: bool = True


@dataclass
class AppConfig:
    drone: DroneConfig = field(default_factory=DroneConfig)
    buffer: BufferConfig = field(default_factory=BufferConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    simulation: SimulationConfig = field(default_factory=SimulationConfig)
    change_detection: ChangeDetectionConfig = field(default_factory=ChangeDetectionConfig)
    cognitive_frame_selector: CognitiveFrameSelectorConfig = field(
        default_factory=CognitiveFrameSelectorConfig
    )
    ingest: IngestConfig = field(default_factory=IngestConfig)


# ── Loader ───────────────────────────────────────────────────────────────────

_config_cache: Optional[AppConfig] = None


def _env(key: str, default):
    """Read an env-var with double-underscore namespace, e.g. DRONE__RTSP_URL."""
    return os.environ.get(key, default)


def _load_yaml(path: Path) -> dict:
    if path.exists():
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    return {}


def get_config(yaml_path: Optional[str] = None) -> AppConfig:
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    if yaml_path is None:
        yaml_path = str(Path(__file__).parent / "settings.yaml")

    raw = _load_yaml(Path(yaml_path))
    d  = raw.get("drone", {})
    b  = raw.get("buffer", {})
    m  = raw.get("metrics", {})
    l  = raw.get("logging", {})
    s  = raw.get("simulation", {})
    cd = raw.get("change_detection", {})
    cfs = raw.get("cognitive_frame_selector", {})
    ing = raw.get("ingest", {})

    # env-var overrides (DRONE__RTSP_URL etc.)
    _rtsp_only_raw = _env("DRONE__RTSP_ONLY", d.get("rtsp_only", True))
    _rtsp_only = _rtsp_only_raw if isinstance(_rtsp_only_raw, bool) else str(_rtsp_only_raw).lower() not in ("false", "0", "no")
    _use_gpu_raw = _env("DRONE__USE_GPU", d.get("use_gpu", True))
    _use_gpu = _use_gpu_raw if isinstance(_use_gpu_raw, bool) else str(_use_gpu_raw).lower() not in ("false", "0", "no")
    drone = DroneConfig(
        rtsp_url=_env("DRONE__RTSP_URL", d.get("rtsp_url", DroneConfig.rtsp_url)),
        usb_device_index=int(_env("DRONE__USB_DEVICE_INDEX", d.get("usb_device_index", 0))),
        webcam_device_index=int(_env("DRONE__WEBCAM_DEVICE_INDEX", d.get("webcam_device_index", 0))),
        preferred_source=_env("DRONE__PREFERRED_SOURCE", d.get("preferred_source", "rtsp")),
        rtsp_only=_rtsp_only,
        use_gpu=_use_gpu,
        rtsp_transport=_env("DRONE__RTSP_TRANSPORT", d.get("rtsp_transport", "tcp")),
        gstreamer_pipeline=_env("DRONE__GSTREAMER_PIPELINE", d.get("gstreamer_pipeline", "")),
        reconnect_base_delay_s=float(_env("DRONE__RECONNECT_BASE_DELAY_S", d.get("reconnect_base_delay_s", 1.0))),
        reconnect_multiplier=float(_env("DRONE__RECONNECT_MULTIPLIER", d.get("reconnect_multiplier", 2.0))),
        reconnect_max_delay_s=float(_env("DRONE__RECONNECT_MAX_DELAY_S", d.get("reconnect_max_delay_s", 60.0))),
        read_timeout_s=float(_env("DRONE__READ_TIMEOUT_S", d.get("read_timeout_s", 5.0))),
    )
    buffer = BufferConfig(
        duration_s=int(_env("BUFFER__DURATION_S", b.get("duration_s", 60))),
        storage_dir=_env("BUFFER__STORAGE_DIR", b.get("storage_dir", "./buffer_storage")),
        jpeg_quality=int(_env("BUFFER__JPEG_QUALITY", b.get("jpeg_quality", 85))),
        max_frames=int(_env("BUFFER__MAX_FRAMES", b.get("max_frames", 2200))),
        mmap_slab_size_bytes=int(_env("BUFFER__MMAP_SLAB_SIZE_BYTES", b.get("mmap_slab_size_bytes", 128*1024*1024))),
    )
    metrics = MetricsConfig(
        prometheus_port=int(_env("METRICS__PROMETHEUS_PORT", m.get("prometheus_port", 8000))),
        heartbeat_log_interval_s=int(_env("METRICS__HEARTBEAT_LOG_INTERVAL_S", m.get("heartbeat_log_interval_s", 10))),
    )
    logging_cfg = LoggingConfig(
        level=_env("LOGGING__LEVEL", l.get("level", "INFO")),
        json_output=bool(_env("LOGGING__JSON_OUTPUT", l.get("json_output", False))),
        log_file=_env("LOGGING__LOG_FILE", l.get("log_file", "./logs/edge.log")),
    )

    sim_enabled_raw = _env("SIMULATION__ENABLED", s.get("enabled", True))
    sim_enabled = sim_enabled_raw if isinstance(sim_enabled_raw, bool) else str(sim_enabled_raw).lower() not in ("false", "0", "no")
    sim_loop_raw = _env("SIMULATION__LOOP", s.get("loop", True))
    sim_loop = sim_loop_raw if isinstance(sim_loop_raw, bool) else str(sim_loop_raw).lower() not in ("false", "0", "no")
    sim_files_raw = s.get("video_files", [])
    simulation = SimulationConfig(
        enabled=sim_enabled,
        video_files=list(sim_files_raw),
        loop=sim_loop,
        playback_speed=float(_env("SIMULATION__PLAYBACK_SPEED", s.get("playback_speed", 1.0))),
    )

    cd_enabled_raw = _env("CHANGE_DETECTION__ENABLED", cd.get("enabled", True))
    cd_enabled = cd_enabled_raw if isinstance(cd_enabled_raw, bool) else str(cd_enabled_raw).lower() not in ("false", "0", "no")
    cd_shadows_raw = _env("CHANGE_DETECTION__DETECT_SHADOWS", cd.get("detect_shadows", True))
    cd_shadows = cd_shadows_raw if isinstance(cd_shadows_raw, bool) else str(cd_shadows_raw).lower() not in ("false", "0", "no")
    cd_mask_raw = _env("CHANGE_DETECTION__EMIT_RAW_MASK", cd.get("emit_raw_mask", True))
    cd_mask = cd_mask_raw if isinstance(cd_mask_raw, bool) else str(cd_mask_raw).lower() not in ("false", "0", "no")
    cd_raw_frame_raw = _env("CHANGE_DETECTION__EMIT_RAW_FRAME", cd.get("emit_raw_frame", True))
    cd_raw_frame = cd_raw_frame_raw if isinstance(cd_raw_frame_raw, bool) else str(cd_raw_frame_raw).lower() not in ("false", "0", "no")
    change_detection = ChangeDetectionConfig(
        enabled=cd_enabled,
        mog2_history=int(_env("CHANGE_DETECTION__MOG2_HISTORY", cd.get("mog2_history", 500))),
        mog2_var_threshold=float(_env("CHANGE_DETECTION__MOG2_VAR_THRESHOLD", cd.get("mog2_var_threshold", 25.0))),
        detect_shadows=cd_shadows,
        min_contour_area=int(_env("CHANGE_DETECTION__MIN_CONTOUR_AREA", cd.get("min_contour_area", 400))),
        motion_score_threshold=float(_env("CHANGE_DETECTION__MOTION_SCORE_THRESHOLD", cd.get("motion_score_threshold", 0.02))),
        persistence_frames=int(_env("CHANGE_DETECTION__PERSISTENCE_FRAMES", cd.get("persistence_frames", 3))),
        max_queue_size=int(_env("CHANGE_DETECTION__MAX_QUEUE_SIZE", cd.get("max_queue_size", 64))),
        emit_raw_mask=cd_mask,
        emit_raw_frame=cd_raw_frame,
    )

    # ── Cognitive Frame Selector ──────────────────────────────────────────
    cfs_enabled_raw = _env("COGNITIVE__ENABLED", cfs.get("enabled", True))
    cfs_enabled = cfs_enabled_raw if isinstance(cfs_enabled_raw, bool) else str(cfs_enabled_raw).lower() not in ("false", "0", "no")
    cognitive_frame_selector = CognitiveFrameSelectorConfig(
        enabled=cfs_enabled,
        transmission_target=float(_env("COGNITIVE__TRANSMISSION_TARGET",   cfs.get("transmission_target", 0.35))),
        window_size=int(_env("COGNITIVE__WINDOW_SIZE",                     cfs.get("window_size", 15))),
        percentile_threshold=float(_env("COGNITIVE__PERCENTILE_THRESHOLD", cfs.get("percentile_threshold", 0.65))),
        absolute_min_priority=float(_env("COGNITIVE__ABS_MIN_PRIORITY",    cfs.get("absolute_min_priority", 0.05))),
        emergency_priority=float(_env("COGNITIVE__EMERGENCY_PRIORITY",     cfs.get("emergency_priority", 0.60))),
        vehicle_boost=float(_env("COGNITIVE__VEHICLE_BOOST",               cfs.get("vehicle_boost", 0.30))),
        scene_change_boost=float(_env("COGNITIVE__SCENE_CHANGE_BOOST",     cfs.get("scene_change_boost", 0.30))),
        crowd_boost=float(_env("COGNITIVE__CROWD_BOOST",                   cfs.get("crowd_boost", 0.30))),
        weights_histogram=float(_env("COGNITIVE__WEIGHT_HISTOGRAM",        cfs.get("weights_histogram", 0.20))),
        weights_motion=float(_env("COGNITIVE__WEIGHT_MOTION",              cfs.get("weights_motion", 0.40))),
        weights_object=float(_env("COGNITIVE__WEIGHT_OBJECT",              cfs.get("weights_object", 0.30))),
        weights_context=float(_env("COGNITIVE__WEIGHT_CONTEXT",            cfs.get("weights_context", 0.10))),
        histogram_bins=int(_env("COGNITIVE__HISTOGRAM_BINS",               cfs.get("histogram_bins", 16))),
        motion_threshold_low=int(_env("COGNITIVE__MOTION_THRESH_LOW",      cfs.get("motion_threshold_low", 10))),
        motion_threshold_med=int(_env("COGNITIVE__MOTION_THRESH_MED",      cfs.get("motion_threshold_med", 20))),
        motion_threshold_high=int(_env("COGNITIVE__MOTION_THRESH_HIGH",    cfs.get("motion_threshold_high", 30))),
        object_brightness_threshold=int(_env("COGNITIVE__OBJ_BRIGHTNESS",  cfs.get("object_brightness_threshold", 150))),
        edge_threshold_low=int(_env("COGNITIVE__EDGE_THRESH_LOW",          cfs.get("edge_threshold_low", 30))),
        edge_threshold_high=int(_env("COGNITIVE__EDGE_THRESH_HIGH",        cfs.get("edge_threshold_high", 100))),
        max_queue_size=int(_env("COGNITIVE__MAX_QUEUE_SIZE",               cfs.get("max_queue_size", 32))),
        jpeg_quality=int(_env("COGNITIVE__JPEG_QUALITY",                   cfs.get("jpeg_quality", 85))),
        mission_id=str(_env("COGNITIVE__MISSION_ID",                       cfs.get("mission_id", "MISSION_DEFAULT"))),
    )

    # ── Ingest server connection ──────────────────────────────────────────
    _tls_raw = _env("INGEST__TLS_VERIFY", ing.get("tls_verify", False))
    _tls_verify = _tls_raw if isinstance(_tls_raw, bool) else str(_tls_raw).lower() in ("true", "1", "yes")
    _ing_enabled_raw = _env("INGEST__ENABLED", ing.get("enabled", True))
    _ing_enabled = _ing_enabled_raw if isinstance(_ing_enabled_raw, bool) else str(_ing_enabled_raw).lower() not in ("false", "0", "no")
    ingest = IngestConfig(
        url=_env("INGEST__URL", ing.get("url", IngestConfig.url)),
        mission_start_url=_env("INGEST__MISSION_START_URL", ing.get("mission_start_url", IngestConfig.mission_start_url)),
        mission_end_url=_env("INGEST__MISSION_END_URL", ing.get("mission_end_url", IngestConfig.mission_end_url)),
        token_url=_env("INGEST__TOKEN_URL", ing.get("token_url", IngestConfig.token_url)),
        client_id=_env("INGEST__CLIENT_ID", ing.get("client_id", IngestConfig.client_id)),
        client_secret=_env("INGEST__CLIENT_SECRET", ing.get("client_secret", IngestConfig.client_secret)),
        username=_env("INGEST__USERNAME", ing.get("username", IngestConfig.username)),
        password=_env("INGEST__PASSWORD", ing.get("password", IngestConfig.password)),
        drone_id=_env("INGEST__DRONE_ID", ing.get("drone_id", IngestConfig.drone_id)),
        mission_id=_env("INGEST__MISSION_ID", ing.get("mission_id", IngestConfig.mission_id)),
        tls_verify=_tls_verify,
        ca_cert=str(_env("INGEST__CA_CERT", ing.get("ca_cert", ""))),
        enabled=_ing_enabled,
    )

    _config_cache = AppConfig(
        drone=drone,
        buffer=buffer,
        metrics=metrics,
        logging=logging_cfg,
        simulation=simulation,
        change_detection=change_detection,
        cognitive_frame_selector=cognitive_frame_selector,
        ingest=ingest,
    )
    return _config_cache


def reset_config() -> None:
    global _config_cache
    _config_cache = None
