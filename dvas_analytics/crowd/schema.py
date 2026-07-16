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
    density_label: str           # Empty | Sparse | Moderate | Dense | Very Dense | Extreme
    density_colour: List[int]    # BGR colour for the label
    density_per_sqm: Optional[float] = None
    heatmap_b64: Optional[str] = None   # base64 PNG of the density heatmap
    alert_triggered: bool = False       # True if count >= CROWD_ALERT_THRESHOLD
    inference_ms: float = 0.0


class CrowdHistoryItem(BaseModel):
    id: int
    drone_id: str
    ts: datetime
    estimated_count: float
    density_per_sqm: Optional[float] = None
    zone_id: Optional[str] = None


class StreamStatus(BaseModel):
    running: bool
    drone_ids: List[str]
    frames_processed: int = 0
