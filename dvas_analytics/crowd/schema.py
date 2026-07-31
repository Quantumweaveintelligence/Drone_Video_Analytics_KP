"""
Crowd Analyzer — Pydantic schemas.
"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class CrowdAnalysisResult(BaseModel):
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
