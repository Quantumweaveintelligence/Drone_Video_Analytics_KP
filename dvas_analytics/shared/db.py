"""
DVAS Analytics — Postgres Database Client
Async connection pool using asyncpg. Single Postgres instance, 'dvas' database.
"""
import uuid
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg

from shared.config import cfg

log = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            cfg.DATABASE_URL,
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
        log.info("Postgres connection pool established")
    return _pool


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


# ─────────────────────────────────────────────────────────────────────────────
# Drone helpers
# ─────────────────────────────────────────────────────────────────────────────

async def ensure_drone(drone_id: str) -> None:
    """Insert drone row if it doesn't exist (idempotent)."""
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO drones (drone_id, label, status)
        VALUES ($1, $1, 'active')
        ON CONFLICT (drone_id) DO NOTHING
        """,
        drone_id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Track management
# ─────────────────────────────────────────────────────────────────────────────

async def upsert_track(
    drone_id: str,
    mission_id: Optional[str],
    track_type: str,  # 'person' | 'vehicle' | 'vessel'
    first_seen: datetime,
    last_seen: datetime,
    metadata: Optional[Dict] = None,
) -> str:
    """Create or update a track, return track_id (UUID string)."""
    pool = await get_pool()
    track_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO tracks (track_id, drone_id, mission_id, track_type, first_seen, last_seen, metadata)
        VALUES ($1, $2, $3::uuid, $4, $5, $6, $7::jsonb)
        """,
        track_id,
        drone_id,
        mission_id,
        first_seen,
        last_seen,
        json.dumps(metadata or {}),
    )
    return track_id


async def update_track_last_seen(track_id: str, last_seen: datetime) -> None:
    pool = await get_pool()
    await pool.execute(
        "UPDATE tracks SET last_seen=$1 WHERE track_id=$2::uuid",
        last_seen,
        track_id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Confirmed events
# ─────────────────────────────────────────────────────────────────────────────

async def write_confirmed_event(
    drone_id: str,
    event_type: str,
    confidence: float,
    ts: datetime,
    track_id: Optional[str] = None,
    mission_id: Optional[str] = None,
    zone_id: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    attributes: Optional[Dict] = None,
) -> str:
    """Insert a confirmed event, return event_id."""
    pool = await get_pool()
    event_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO confirmed_events
            (event_id, drone_id, mission_id, zone_id, track_id,
             event_type, confidence, ts, latitude, longitude, attributes)
        VALUES
            ($1::uuid, $2, $3::uuid, $4::uuid, $5::uuid,
             $6, $7, $8, $9, $10, $11::jsonb)
        """,
        event_id,
        drone_id,
        mission_id,
        zone_id,
        track_id,
        event_type,
        confidence,
        ts,
        latitude,
        longitude,
        json.dumps(attributes or {}),
    )
    return event_id


# ─────────────────────────────────────────────────────────────────────────────
# Crowd density
# ─────────────────────────────────────────────────────────────────────────────

async def write_crowd_reading(
    drone_id: str,
    ts: datetime,
    estimated_count: float,
    density_per_sqm: Optional[float] = None,
    zone_id: Optional[str] = None,
    heatmap_object_key: Optional[str] = None,
) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO crowd_density_readings
            (drone_id, zone_id, ts, estimated_count, density_per_sqm, heatmap_object_key)
        VALUES ($1, $2::uuid, $3, $4, $5, $6)
        """,
        drone_id,
        zone_id,
        ts,
        estimated_count,
        density_per_sqm,
        heatmap_object_key,
    )


async def get_crowd_history(
    drone_id: Optional[str] = None,
    limit: int = 100,
) -> List[Dict]:
    pool = await get_pool()
    if drone_id:
        rows = await pool.fetch(
            "SELECT * FROM crowd_density_readings WHERE drone_id=$1 ORDER BY ts DESC LIMIT $2",
            drone_id, limit,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM crowd_density_readings ORDER BY ts DESC LIMIT $1",
            limit,
        )
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Vehicle attributes
# ─────────────────────────────────────────────────────────────────────────────

async def write_vehicle_attributes(
    track_id: str,
    event_id: Optional[str],
    vehicle_type: str,
    colour_top3: Optional[List] = None,
    make_model_top3: Optional[List] = None,
    speed_kph: Optional[float] = None,
    speed_reliability: Optional[str] = None,
    direction_label: Optional[str] = None,
    traffic_state: Optional[str] = None,
) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO vehicle_attributes
            (track_id, event_id, vehicle_type, colour_top3, make_model_top3,
             speed_kph, speed_reliability, direction_label, traffic_state)
        VALUES
            ($1::uuid, $2::uuid, $3, $4::jsonb, $5::jsonb, $6, $7, $8, $9)
        """,
        track_id,
        event_id,
        vehicle_type,
        json.dumps(colour_top3 or []),
        json.dumps(make_model_top3 or []),
        speed_kph,
        speed_reliability,
        direction_label,
        traffic_state,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Plate reads
# ─────────────────────────────────────────────────────────────────────────────

async def write_plate_read(
    track_id: str,
    event_id: Optional[str],
    plate_text: str,
    ocr_confidence: float,
    is_reliable: bool,
    vehicle_image_b64: Optional[str] = None,
) -> str:
    """Insert plate read, return plate record id. Deduplication is by track_id."""
    pool = await get_pool()
    plate_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO plate_reads
            (id, track_id, event_id, plate_text, ocr_confidence, is_reliable, vehicle_image_b64)
        VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7)
        ON CONFLICT (track_id) DO UPDATE SET
            plate_text = EXCLUDED.plate_text,
            ocr_confidence = EXCLUDED.ocr_confidence,
            is_reliable = EXCLUDED.is_reliable,
            vehicle_image_b64 = EXCLUDED.vehicle_image_b64,
            updated_at = now()
        """,
        plate_id,
        track_id,
        event_id,
        plate_text,
        ocr_confidence,
        is_reliable,
        vehicle_image_b64,
    )
    return plate_id


async def get_recent_plates(limit: int = 50) -> List[Dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT pr.*, t.drone_id, t.first_seen, t.last_seen
        FROM plate_reads pr
        LEFT JOIN tracks t ON pr.track_id = t.track_id
        ORDER BY pr.created_at DESC
        LIMIT $1
        """,
        limit,
    )
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Plate watchlist
# ─────────────────────────────────────────────────────────────────────────────

async def add_to_watchlist(plate_text: str, reason: Optional[str] = None) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO plate_watchlist (plate_text, reason)
        VALUES ($1, $2)
        ON CONFLICT (plate_text) DO UPDATE SET is_active=true, reason=EXCLUDED.reason
        """,
        plate_text.upper().strip(),
        reason,
    )


async def remove_from_watchlist(plate_text: str) -> None:
    pool = await get_pool()
    await pool.execute(
        "UPDATE plate_watchlist SET is_active=false WHERE plate_text=$1",
        plate_text.upper().strip(),
    )


async def get_watchlist() -> List[Dict]:
    pool = await get_pool()
    rows = await pool.fetch("SELECT * FROM plate_watchlist WHERE is_active=true ORDER BY added_at DESC")
    return [dict(r) for r in rows]


async def is_watchlisted(plate_text: str) -> bool:
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT 1 FROM plate_watchlist WHERE plate_text=$1 AND is_active=true",
        plate_text.upper().strip(),
    )
    return row is not None


# ─────────────────────────────────────────────────────────────────────────────
# Face embeddings
# ─────────────────────────────────────────────────────────────────────────────

async def write_face_embedding(
    track_id: Optional[str],
    image_path: str,
    embedding: List[float],
    drone_id: Optional[str] = None,
) -> str:
    pool = await get_pool()
    emb_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO face_embeddings (id, track_id, image_path, embedding, drone_id, created_at)
        VALUES ($1::uuid, $2::uuid, $3, $4::float4[], $5, now())
        """,
        emb_id,
        track_id,
        image_path,
        embedding,
        drone_id,
    )
    return emb_id


async def get_all_face_embeddings() -> List[Dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT id, track_id, image_path, embedding, drone_id, created_at FROM face_embeddings ORDER BY created_at"
    )
    return [dict(r) for r in rows]


async def delete_face_embeddings(ids: List[str]) -> int:
    """Delete duplicate embedding rows, return count deleted."""
    pool = await get_pool()
    if not ids:
        return 0
    result = await pool.execute(
        "DELETE FROM face_embeddings WHERE id = ANY($1::uuid[])",
        ids,
    )
    # result is like "DELETE N"
    return int(result.split()[-1])


async def get_recent_confirmed_events(limit: int = 50, drone_id: Optional[str] = None) -> List[Dict]:
    pool = await get_pool()
    if drone_id:
        rows = await pool.fetch(
            "SELECT * FROM confirmed_events WHERE drone_id=$1 ORDER BY ts DESC LIMIT $2",
            drone_id, limit
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM confirmed_events ORDER BY ts DESC LIMIT $1", limit
        )
    return [dict(r) for r in rows]
