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
