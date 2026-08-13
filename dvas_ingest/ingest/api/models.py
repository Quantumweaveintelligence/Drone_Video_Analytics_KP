"""
models.py
=========
Pydantic data models for the Kerala Police Drone Ingest API.
"""

from __future__ import annotations

import time
from typing import Optional
from pydantic import BaseModel, Field, field_validator
import re


# ── Ingest Request ────────────────────────────────────────────────────────────

class FrameMetadata(BaseModel):
    """
    Metadata attached alongside every frame upload.
    Sent as a JSON string in the multipart `metadata_json` field.
    """
    drone_id:       str   = Field(..., description="Drone identifier e.g. DRONE_KL_001")
    mission_id:     str   = Field(..., description="Mission identifier e.g. MISSION_20250527_BEACH")
    timestamp:      str   = Field(..., description="ISO 8601 UTC timestamp of frame capture")
    frame_sequence: int   = Field(..., ge=0, description="Incremental counter per session")
    change_score:   float = Field(..., ge=0.0, le=1.0, description="Edge change-detection score")

    # Optional GPS telemetry
    latitude:    Optional[float] = Field(None, ge=-90.0,  le=90.0)
    longitude:   Optional[float] = Field(None, ge=-180.0, le=180.0)
    altitude_m:  Optional[float] = Field(None, ge=0.0,    le=10000.0)

    # Optional sensor metadata
    camera_id:    Optional[str]   = None
    heading_deg:  Optional[float] = Field(None, ge=0.0, le=360.0)
    speed_kmh:    Optional[float] = Field(None, ge=0.0)
    speed_mps:    Optional[float] = Field(None, ge=0.0)          # m/s from edge device
    battery_pct:  Optional[float] = Field(None, ge=0.0, le=100.0)  # 0–100% from edge device
    frames_total: Optional[int]   = Field(None, ge=0)            # Total frames processed by edge

    @field_validator("drone_id")
    @classmethod
    def validate_drone_id(cls, v: str) -> str:
        if not re.match(r"^[A-Z0-9_\-]{3,30}$", v):
            raise ValueError(
                "drone_id must be 3-30 uppercase alphanumeric/underscore characters"
            )
        return v

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v: str) -> str:
        # Basic ISO 8601 check
        if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", v):
            raise ValueError("timestamp must be ISO 8601 format: YYYY-MM-DDTHH:MM:SS...")
        return v


# ── Ingest Response ───────────────────────────────────────────────────────────

class IngestResponse(BaseModel):
    status:         str  = Field(..., description="'queued' or 'rejected'")
    drone_id:       str
    frame_sequence: int
    queue_name:     str
    message_id:     Optional[str] = None


# ── Health Models ─────────────────────────────────────────────────────────────

class ComponentHealth(BaseModel):
    status:  str           # "ok" | "degraded" | "down"
    latency_ms: Optional[float] = None
    detail:  Optional[str] = None


class HealthResponse(BaseModel):
    status:    str     # "ok" | "degraded" | "down"
    version:   str
    uptime_s:  float
    components: dict[str, ComponentHealth]


class ReadinessResponse(BaseModel):
    ready:    bool
    redis:    str  # "connected" | "disconnected"
    keycloak: str  # "reachable" | "unreachable"


# ── Drone Status ──────────────────────────────────────────────────────────────

class DroneQueueStats(BaseModel):
    queue_name:       str
    messages_ready:   int
    consumers:        int


class IngestStats(BaseModel):
    frames_received:   int
    frames_queued:     int
    frames_rejected:   int
    bytes_ingested:    int
    frames_total:      Optional[int] = 0
    last_frame_at:     Optional[str] = None   # ISO 8601 string or None


class DroneStatusResponse(BaseModel):
    drone_id:     str
    queue:        Optional[DroneQueueStats] = None
    ingest_stats: IngestStats
    mission_id:   Optional[str] = None
    online:       bool


class SystemStatusResponse(BaseModel):
    total_drones_tracked: int
    total_frames_queued:  int
    total_frames_ingested: int
    drones:               list[DroneStatusResponse]
    redis_connected:      bool   # renamed from rabbitmq_connected


# ── Mission Models ────────────────────────────────────────────────────────────

class MissionStartRequest(BaseModel):
    drone_id:   str   = Field(..., description="Drone ID starting the mission")
    mission_id: str   = Field(..., description="Unique mission identifier")
    mission_name: Optional[str] = None
    location:   Optional[str] = None
    operator:   Optional[str] = None
    latitude:   Optional[float] = None
    longitude:  Optional[float] = None
    notes:      Optional[str]  = None


class MissionEndRequest(BaseModel):
    drone_id:   str
    mission_id: str
    frames_sent: Optional[int] = None
    notes:       Optional[str] = None


class MissionResponse(BaseModel):
    status:     str
    drone_id:   str
    mission_id: str
    timestamp:  str


# ── WebSocket ─────────────────────────────────────────────────────────────────

class WSStatusEvent(BaseModel):
    event:     str      # "frame_queued" | "drone_online" | "drone_offline" | "mission_start" | "mission_end"
    drone_id:  str
    timestamp: str
    data:      dict
