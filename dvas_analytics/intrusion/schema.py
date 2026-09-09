# -*- coding: utf-8 -*-
"""Pydantic schemas for Intrusion Detection Analytics."""
from enum import Enum
from typing import Dict, List, Optional, Tuple
from pydantic import BaseModel, Field
from datetime import datetime


class IntrusionStateEnum(str, Enum):
    OUTSIDE = "OUTSIDE"
    ENTERING = "ENTERING"
    INSIDE = "INSIDE"
    DWELLING = "DWELLING"
    EXITING = "EXITING"
    EXITED = "EXITED"


class MonitoringMode(str, Enum):
    STANDBY = "STANDBY"
    MONITORING = "MONITORING"
    DETECTION_ONLY = "DETECTION_ONLY"
    IDLE = "IDLE"  # No geofence loaded, service idle


class BBox(BaseModel):
    x1: int
    y1: int
    x2: int
    y2: int


class IntrusionDetection(BaseModel):
    track_id: int
    class_label: str
    confidence: float
    bbox: BBox
    ground_contact_pixel: Tuple[int, int] = (0, 0)
    wgs84_lat: Optional[float] = None
    wgs84_lon: Optional[float] = None
    intrusion_state: IntrusionStateEnum = IntrusionStateEnum.OUTSIDE
    is_intruding: bool = False
    dwell_duration_s: float = 0.0


class TelemetryHUD(BaseModel):
    drone_lat: float = 0.0
    drone_lon: float = 0.0
    altitude_m: float = 0.0
    gimbal_yaw: float = 0.0
    gimbal_pitch: float = 0.0
    gimbal_roll: float = 0.0
    frame_index: int = 0
    active_tracks: int = 0
    intrusion_count: int = 0


class GeofenceOverlay(BaseModel):
    """Projected geofence polygon data for client-side canvas rendering."""
    pixel_coords: List[Tuple[float, float]] = []
    visibility_flags: List[bool] = []
    vertex_count: int = 0


class IntrusionFrameResult(BaseModel):
    drone_id: str
    mission_id: Optional[str] = None
    frame_ts: Optional[str] = None
    frame_sequence: int = 0
    detections: List[IntrusionDetection] = []
    geofence_overlay: Optional[GeofenceOverlay] = None
    telemetry_hud: Optional[TelemetryHUD] = None
    monitoring_mode: MonitoringMode = MonitoringMode.IDLE
    active_intrusion_count: int = 0


class IntrusionEvent(BaseModel):
    """State transition record for audit trail and confirmed events."""
    event_id: Optional[str] = None
    track_id: int
    track_class: str
    state_from: str
    state_to: str
    confidence: float
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    entry_ts: Optional[str] = None
    exit_ts: Optional[str] = None
    dwell_duration_s: float = 0.0
    frame_sequence: int = 0
    zone_id: Optional[str] = None
    drone_id: str = ""
    mission_id: Optional[str] = None
    ts: Optional[str] = None


class IntrusionConfig(BaseModel):
    """Runtime configuration for intrusion detection."""
    preset: str = "default"
    excluded_classes: List[str] = []
    confirm_frames: int = 3
    dwell_threshold_s: float = 3.0
    min_confidence: float = 0.25
    motorcycle_min_confidence: float = 0.75
    imgsz: int = 1920


class GeofenceStatus(BaseModel):
    zone_id: Optional[str] = None
    name: Optional[str] = None
    vertex_count: int = 0
    monitoring_state: MonitoringMode = MonitoringMode.IDLE
    is_loaded: bool = False
    coordinates: Optional[List[List[float]]] = None


class CameraConfig(BaseModel):
    """Camera intrinsics for photogrammetric projection."""
    focal_length_mm: float = 24.0
    sensor_width_mm: float = 36.0
    img_width: int = 3840
    img_height: int = 2160
    hfov_deg: Optional[float] = None
