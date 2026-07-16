"""
DVAS Analytics — Shared Configuration
Reads all configuration from environment variables with sensible defaults.
"""
import os
from typing import List


class Config:
    # ── Postgres (single instance, two databases) ──────────────────────────
    # Keycloak DB: postgres://keycloak:...@postgres:5432/keycloak  (existing)
    # DVAS analytics DB on the same instance, different DB name
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql://dvas:dvas_secret@postgres:5432/dvas"
    )

    # ── Valkey / Redis ─────────────────────────────────────────────────────
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://valkey:6379/0")

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
    # "valkey" = live stream from Redis stream
    # "file"   = process a local video file (for testing)
    INPUT_SOURCE: str = os.getenv("INPUT_SOURCE", "valkey")
    TEST_VIDEO_PATH: str = os.getenv("TEST_VIDEO_PATH", "")

    # ── Confirmed-event thresholds ─────────────────────────────────────────
    CROWD_ALERT_THRESHOLD: int = int(os.getenv("CROWD_ALERT_THRESHOLD", "50"))
    DETECTION_CONFIDENCE: float = float(os.getenv("DETECTION_CONFIDENCE", "0.25"))

    # ── Evidence storage ───────────────────────────────────────────────────
    EVIDENCE_OUTPUT_DIR: str = os.getenv("EVIDENCE_OUTPUT_DIR", "/data/evidence")

    # ── Logging ────────────────────────────────────────────────────────────
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_JSON: bool = os.getenv("LOG_JSON", "true").lower() == "true"


cfg = Config()
