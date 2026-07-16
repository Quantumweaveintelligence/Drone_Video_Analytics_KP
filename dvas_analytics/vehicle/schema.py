from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class ColourPrediction(BaseModel):
    colour: str
    prob: float


class MakePrediction(BaseModel):
    make: str
    model: str
    prob: float


class SpeedInfo(BaseModel):
    kph: Optional[float] = None
    reliability: Optional[str] = None
    direction_label: Optional[str] = None


class VehicleDetection(BaseModel):
    vehicle_id: int
    tracker_id: Optional[int] = None
    track_id: Optional[str] = None    # Postgres track UUID
    vehicle_type: str
    confidence: float
    bbox_x: float
    bbox_y: float
    bbox_w: float
    bbox_h: float
    speed_info: Optional[SpeedInfo] = None
    colour_top3: List[ColourPrediction] = []
    make_model_top3: List[MakePrediction] = []
    traffic_state: Optional[str] = None
    crop_b64: Optional[str] = None


class VehicleFrameResult(BaseModel):
    drone_id: str
    ts: datetime
    frame_timestamp: str
    vehicles: List[VehicleDetection]
    vehicle_count: int
    inference_ms: float


class VehicleStats(BaseModel):
    total_vehicles: int
    by_type: Dict[str, int]
    avg_speed_kph: Optional[float] = None
