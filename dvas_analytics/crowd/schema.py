"""
Crowd Analyzer — Pydantic schemas.
Updated for CSRNet dual-model support (Part A / Part B).
"""
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict


class CrowdAnalysisResult(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    drone_id: str
    ts: datetime
    estimated_count: float
    density_label: str
    density_colour: List[int]
    density_per_sqm: Optional[float] = None
    heatmap_b64: Optional[str] = None
    heatmap_url: Optional[str] = None   # presigned MinIO URL
    alert_triggered: bool = False
    inference_ms: float = 0.0
    model_type: str = "csrnet_parta"


class CrowdHistoryItem(BaseModel):
    id: int
    drone_id: str
    ts: datetime
    estimated_count: float
    density_per_sqm: Optional[float] = None
    zone_id: Optional[str] = None
    heatmap_object_key: Optional[str] = None


class StreamStatus(BaseModel):
    running: bool
    drone_ids: List[str]
    frames_processed: int = 0


class CrowdTimelineItem(BaseModel):
    ts: datetime
    estimated_count: float
    density_label: Optional[str] = None
    drone_id: str


class ModelSwitchRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_type: str  # "csrnet_parta" or "csrnet_partb"


class ModelInfo(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_type: str
    model_path: str
    device: str
    status: str  # "loaded" or "error"


class SessionSummary(BaseModel):
    total_frames: int = 0
    duration_seconds: float = 0.0
    mean_count: float = 0.0
    min_count: float = 0.0
    max_count: float = 0.0
    std_dev: float = 0.0
    peak_count: float = 0.0
    peak_timestamp: Optional[str] = None
    peak_frame: Optional[Any] = None
    density_distribution: Dict[str, float] = {}
    alert_timestamps: List[str] = []
