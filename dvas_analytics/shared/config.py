"""
DVAS Analytics — Shared Configuration
Reads all configuration from environment variables with sensible defaults.
"""
import os
from typing import List


class Config:
    # ── Postgres ───────────────────────────────────────────────────────────
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql://dvas:dvas_secret@postgres:5432/dvas"
    )

    # ── Valkey / Redis ─────────────────────────────────────────────────────
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://valkey:6379/0")

    # ── MinIO / S3-compatible object storage ───────────────────────────────
    MINIO_ENDPOINT:   str  = os.getenv("MINIO_ENDPOINT",   "minio:9000")
    MINIO_ACCESS_KEY: str  = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
    MINIO_SECRET_KEY: str  = os.getenv("MINIO_SECRET_KEY", "minioadmin")
    MINIO_SECURE:     bool = os.getenv("MINIO_SECURE", "false").lower() == "true"

    # Bucket names
    BUCKET_EVIDENCE:  str = os.getenv("BUCKET_EVIDENCE",  "dvas-evidence")
    BUCKET_FACES:     str = os.getenv("BUCKET_FACES",     "dvas-faces")
    BUCKET_PLATES:    str = os.getenv("BUCKET_PLATES",    "dvas-plates")
    BUCKET_HEATMAPS:  str = os.getenv("BUCKET_HEATMAPS",  "dvas-heatmaps")
    BUCKET_VEHICLES:  str = os.getenv("BUCKET_VEHICLES",  "dvas-vehicles")

    # Presigned URL expiry (seconds) for dashboard image display
    MINIO_URL_EXPIRY: int = int(os.getenv("MINIO_URL_EXPIRY", "3600"))
    MINIO_PUBLIC_URL: str = os.getenv("MINIO_PUBLIC_URL", "")  # e.g. http://10.51.27.250:9000

    # ── Drone registry ─────────────────────────────────────────────────────
    DRONE_IDS: List[str] = [
        d.strip()
        for d in os.getenv(
            "DRONE_IDS",
            "DRONE_KL_001,DRONE_KL_002,DRONE_KL_003,DRONE_KL_004,DRONE_KL_005"
        ).split(",")
        if d.strip()
    ]

    # ── Input mode ─────────────────────────────────────────────────────────
    INPUT_SOURCE:    str = os.getenv("INPUT_SOURCE",    "valkey")
    TEST_VIDEO_PATH: str = os.getenv("TEST_VIDEO_PATH", "")

    # ── Confirmed-event thresholds ─────────────────────────────────────────
    CROWD_ALERT_THRESHOLD: int   = int(os.getenv("CROWD_ALERT_THRESHOLD", "50"))
    DETECTION_CONFIDENCE:  float = float(os.getenv("DETECTION_CONFIDENCE", "0.25"))

    # ── Re-ID / deduplication ──────────────────────────────────────────────
    REID_CLUSTER_INTERVAL: int   = int(os.getenv("REID_CLUSTER_INTERVAL", "50"))   # re-cluster every N frames
    REID_DISTANCE_THRESHOLD: float = float(os.getenv("REID_DISTANCE_THRESHOLD", "0.25"))
    USE_PGVECTOR:          bool  = os.getenv("USE_PGVECTOR", "false").lower() == "true"

    # ── Vehicle-ANPR Redis linkage ─────────────────────────────────────────
    VEHICLE_TRACK_KEY_PREFIX: str = os.getenv("VEHICLE_TRACK_KEY_PREFIX", "vehicle:track")
    VEHICLE_TRACK_TTL:        int = int(os.getenv("VEHICLE_TRACK_TTL", "300"))  # 5 min TTL

    # ── Evidence storage (local fallback) ──────────────────────────────────
    EVIDENCE_OUTPUT_DIR: str = os.getenv("EVIDENCE_OUTPUT_DIR", "/data/evidence")

    # ── Gateway URLs (for health polling) ─────────────────────────────────
    CROWD_URL:   str = os.getenv("CROWD_URL",   "http://crowd-analyzer:8101")
    PERSON_URL:  str = os.getenv("PERSON_URL",  "http://person-analyzer:8102")
    VEHICLE_URL: str = os.getenv("VEHICLE_URL", "http://vehicle-analyzer:8103")
    ANPR_URL:    str = os.getenv("ANPR_URL",    "http://anpr-analyzer:8104")

    # ── Logging ────────────────────────────────────────────────────────────
    LOG_LEVEL: str  = os.getenv("LOG_LEVEL", "INFO")
    LOG_JSON:  bool = os.getenv("LOG_JSON", "true").lower() == "true"


cfg = Config()
