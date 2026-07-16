from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class PlateRead(BaseModel):
    track_id: Optional[str] = None
    vehicle_type: str
    plate_text: str
    ocr_confidence: float
    is_reliable: bool
    times_read: int = 1
    is_watchlisted: bool = False
    vehicle_image_b64: Optional[str] = None  # base64 JPEG of vehicle crop
    first_seen_frame: Optional[int] = None
    last_seen_frame: Optional[int] = None


class ANPRFrameResult(BaseModel):
    drone_id: str
    ts: datetime
    plate_reads: List[PlateRead]
    total_vehicles: int
    plates_read: int
    theft_alerts: int
    inference_ms: float


class WatchlistEntry(BaseModel):
    plate_text: str
    reason: Optional[str] = None
    case_ref: Optional[str] = None
    is_active: bool = True


class TheftAlert(BaseModel):
    plate_text: str
    track_id: Optional[str] = None
    drone_id: str
    detected_at: datetime
    vehicle_type: str
    confidence: float
