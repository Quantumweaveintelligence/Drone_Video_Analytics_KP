from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class BoundingBox(BaseModel):
    x1: int
    y1: int
    x2: int
    y2: int


class PersonDetection(BaseModel):
    bbox: BoundingBox
    confidence: float
    track_id: Optional[str] = None
    crop_b64: Optional[str] = None


class FaceDetection(BaseModel):
    bbox: BoundingBox
    confidence: float
    track_id: Optional[str] = None
    crop_b64: Optional[str] = None
    embedding_id: Optional[str] = None


class FrameAnalysisResult(BaseModel):
    drone_id: str
    ts: datetime
    persons: List[PersonDetection]
    faces: List[FaceDetection]
    person_count: int
    face_count: int
    inference_ms: float


class UniquePersonResult(BaseModel):
    cluster_id: int
    best_image_path: str
    count_in_cluster: int
    is_duplicate: bool


class DeduplicationResult(BaseModel):
    total_embeddings: int
    unique_persons: int
    duplicates_found: int
    duplicates_deleted: int


# ── Face Recognition / Watchlist models ──────────────────────────────────

class WatchlistEntryIn(BaseModel):
    """Input schema for watchlist enrollment (non-file fields)."""
    name: str
    category: str = "wanted"  # 'wanted' | 'missing' | 'vip' | 'poi'
    case_ref: Optional[str] = None
    description: Optional[str] = None
    expires_at: Optional[datetime] = None


class WatchlistEntryOut(BaseModel):
    """Output schema for a watchlist entry."""
    id: str
    name: str
    category: str
    case_ref: Optional[str] = None
    description: Optional[str] = None
    reference_image_url: Optional[str] = None
    embedding_count: int = 0
    added_at: Optional[str] = None
    updated_at: Optional[str] = None
    is_active: bool = True


class WatchlistAlertOut(BaseModel):
    """Output schema for a watchlist alert."""
    id: str
    watchlist_id: str
    name: str
    category: str
    drone_id: Optional[str] = None
    mission_id: Optional[str] = None
    tracker_id: Optional[int] = None
    confidence: float
    face_crop_url: Optional[str] = None
    frame_crop_url: Optional[str] = None
    detected_at: Optional[str] = None
    is_confirmed: bool = False
    notes: Optional[str] = None
    case_ref: Optional[str] = None


class MatchResultOut(BaseModel):
    """Output schema for a face match result."""
    watchlist_id: str
    name: str
    category: str
    similarity: float
    rank: int = 1


# Type aliases matching implementation plan specifications
WatchlistEntry = WatchlistEntryOut
WatchlistAlert = WatchlistAlertOut
MatchResult = MatchResultOut
