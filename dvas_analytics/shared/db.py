# -*- coding: utf-8 -*-
"""
DVAS Analytics — PostgreSQL helpers (asyncpg).
All functions use a shared connection pool.
"""
import json
import logging
import uuid
from typing import Dict, List, Optional, Any

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
        log.info("DB pool created: %s", cfg.DATABASE_URL)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


# ─────────────────────────────────────────────────────────────────────────────
# Drones
# ─────────────────────────────────────────────────────────────────────────────

async def ensure_drone(drone_id: str) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO drones (drone_id, status)
        VALUES ($1, 'active')
        ON CONFLICT (drone_id) DO NOTHING
        """,
        drone_id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tracks
# ─────────────────────────────────────────────────────────────────────────────

async def upsert_track(
    drone_id: str,
    external_id: Optional[str],
    track_type: str,
    first_seen,
    last_seen,
    tracker_id: Optional[int] = None,
) -> str:
    """
    Insert or update a track row. Returns track_id (UUID string).
    Deduplication: if tracker_id is provided, upsert by (drone_id, tracker_id, track_type).
    """
    pool = await get_pool()
    track_id = str(uuid.uuid4())
    if tracker_id is not None:
        row = await pool.fetchrow(
            """
            INSERT INTO tracks (track_id, drone_id, track_type, first_seen, last_seen,
                                metadata)
            VALUES ($1::uuid, $2, $3, $4, $5, $6::jsonb)
            ON CONFLICT DO NOTHING
            RETURNING track_id::text
            """,
            track_id, drone_id, track_type, first_seen, last_seen,
            json.dumps({"tracker_id": tracker_id}),
        )
        if row:
            return str(row["track_id"])
        # Already exists — fetch it
        existing = await pool.fetchrow(
            """
            SELECT track_id::text FROM tracks
            WHERE drone_id=$1 AND track_type=$2
              AND metadata->>'tracker_id' = $3
            ORDER BY first_seen DESC LIMIT 1
            """,
            drone_id, track_type, str(tracker_id),
        )
        if existing:
            # Update last_seen
            await pool.execute(
                "UPDATE tracks SET last_seen=$1 WHERE track_id=$2::uuid",
                last_seen, existing["track_id"],
            )
            return existing["track_id"]
    # Plain insert
    await pool.execute(
        """
        INSERT INTO tracks (track_id, drone_id, track_type, first_seen, last_seen)
        VALUES ($1::uuid, $2, $3, $4, $5)
        ON CONFLICT (track_id) DO UPDATE SET last_seen = EXCLUDED.last_seen
        """,
        track_id, drone_id, track_type, first_seen, last_seen,
    )
    return track_id


# ─────────────────────────────────────────────────────────────────────────────
# Confirmed events
# ─────────────────────────────────────────────────────────────────────────────

async def write_confirmed_event(
    drone_id: str,
    event_type: str,
    confidence: float,
    ts,
    track_id: Optional[str] = None,
    attributes: Optional[Dict] = None,
) -> str:
    pool = await get_pool()
    event_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO confirmed_events
            (event_id, drone_id, event_type, confidence, ts, track_id, attributes)
        VALUES ($1::uuid, $2, $3, $4, $5, $6::uuid, $7::jsonb)
        """,
        event_id, drone_id, event_type, confidence, ts,
        track_id, json.dumps(attributes or {}),
    )
    return event_id


async def get_recent_confirmed_events(
    limit: int = 50,
    drone_id: Optional[str] = None,
) -> List[Dict]:
    pool = await get_pool()
    if drone_id:
        rows = await pool.fetch(
            "SELECT * FROM confirmed_events WHERE drone_id=$1 ORDER BY ts DESC LIMIT $2",
            drone_id, limit,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM confirmed_events ORDER BY ts DESC LIMIT $1", limit
        )
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Crowd density
# ─────────────────────────────────────────────────────────────────────────────

async def write_crowd_reading(
    drone_id: str,
    ts,
    estimated_count: float,
    density_per_sqm: Optional[float] = None,
    zone_id: Optional[str] = None,
    heatmap_object_key: Optional[str] = None,
    zone_densities: Optional[Dict] = None,
) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO crowd_density_readings
            (drone_id, zone_id, ts, estimated_count, density_per_sqm,
             heatmap_object_key, zone_densities)
        VALUES ($1, $2::uuid, $3, $4, $5, $6, $7::jsonb)
        """,
        drone_id,
        zone_id,
        ts,
        estimated_count,
        density_per_sqm,
        heatmap_object_key,
        json.dumps(zone_densities) if zone_densities else None,
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
# Vehicle-plate linkage (DB-level, mirrors Redis)
# ─────────────────────────────────────────────────────────────────────────────

async def write_vehicle_plate_link(
    tracker_id: int,
    drone_id: str,
    plate_text: str,
    plate_conf: float,
    vehicle_type: Optional[str] = None,
    colour: Optional[str] = None,
    plate_image_key: Optional[str] = None,
    vehicle_image_key: Optional[str] = None,
) -> str:
    pool = await get_pool()
    link_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO vehicle_plate_links
            (id, tracker_id, drone_id, plate_text, plate_conf,
             vehicle_type, colour, plate_image_key, vehicle_image_key)
        VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (tracker_id, drone_id, plate_text)
        DO UPDATE SET
            plate_conf        = GREATEST(vehicle_plate_links.plate_conf, EXCLUDED.plate_conf),
            colour            = COALESCE(EXCLUDED.colour, vehicle_plate_links.colour),
            plate_image_key   = COALESCE(EXCLUDED.plate_image_key, vehicle_plate_links.plate_image_key),
            vehicle_image_key = COALESCE(EXCLUDED.vehicle_image_key, vehicle_plate_links.vehicle_image_key),
            linked_at         = now()
        """,
        link_id, tracker_id, drone_id, plate_text, plate_conf,
        vehicle_type, colour, plate_image_key, vehicle_image_key,
    )
    return link_id


async def get_vehicle_plate_link(
    tracker_id: int,
    drone_id: str,
) -> Optional[Dict]:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT * FROM vehicle_plate_links
        WHERE tracker_id=$1 AND drone_id=$2
        ORDER BY plate_conf DESC LIMIT 1
        """,
        tracker_id, drone_id,
    )
    return dict(row) if row else None


# ─────────────────────────────────────────────────────────────────────────────
# Plate reads
# ─────────────────────────────────────────────────────────────────────────────

async def write_plate_read(
    track_id: str,
    event_id: Optional[str],
    plate_text: str,
    ocr_confidence: float,
    is_reliable: bool,
    vehicle_image_b64: Optional[str] = None,  # deprecated — use vehicle_image_key
    plate_image_key: Optional[str] = None,
    vehicle_image_key: Optional[str] = None,
) -> str:
    pool = await get_pool()
    plate_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO plate_reads
            (id, track_id, event_id, plate_text, ocr_confidence, is_reliable,
             vehicle_image_b64, plate_image_key, vehicle_image_key)
        VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (track_id) DO UPDATE SET
            plate_text        = EXCLUDED.plate_text,
            ocr_confidence    = EXCLUDED.ocr_confidence,
            is_reliable       = EXCLUDED.is_reliable,
            vehicle_image_b64 = COALESCE(EXCLUDED.vehicle_image_b64, plate_reads.vehicle_image_b64),
            plate_image_key   = COALESCE(EXCLUDED.plate_image_key,   plate_reads.plate_image_key),
            vehicle_image_key = COALESCE(EXCLUDED.vehicle_image_key, plate_reads.vehicle_image_key),
            updated_at        = now()
        """,
        plate_id, track_id, event_id, plate_text, ocr_confidence, is_reliable,
        vehicle_image_b64, plate_image_key, vehicle_image_key,
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
    rows = await pool.fetch(
        "SELECT * FROM plate_watchlist WHERE is_active=true ORDER BY added_at DESC"
    )
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
    object_key: Optional[str] = None,
) -> str:
    pool = await get_pool()
    emb_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO face_embeddings
            (id, track_id, image_path, embedding, drone_id, object_key, created_at)
        VALUES ($1::uuid, $2::uuid, $3, $4::float4[], $5, $6, now())
        """,
        emb_id,
        track_id,
        image_path,
        embedding,
        drone_id,
        object_key,
    )
    return emb_id


async def get_all_face_embeddings() -> List[Dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, track_id, image_path, embedding, drone_id, cluster_id,
               is_duplicate, object_key, created_at
        FROM face_embeddings
        ORDER BY created_at
        """
    )
    return [dict(r) for r in rows]


async def delete_face_embeddings(ids: List[str]) -> int:
    pool = await get_pool()
    if not ids:
        return 0
    result = await pool.execute(
        "DELETE FROM face_embeddings WHERE id = ANY($1::uuid[])",
        ids,
    )
    return int(result.split()[-1])


async def update_face_embedding_cluster(
    embedding_id: str,
    cluster_id: int,
    is_duplicate: bool,
) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE face_embeddings
        SET cluster_id=$1, is_duplicate=$2
        WHERE id=$3::uuid
        """,
        cluster_id, is_duplicate, embedding_id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Person identities (Re-ID)
# ─────────────────────────────────────────────────────────────────────────────

async def write_person_identity(
    cluster_id: int,
    best_image_key: Optional[str],
    drone_id: Optional[str],
    embedding_count: int = 1,
) -> str:
    pool = await get_pool()
    identity_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO person_identities
            (identity_id, cluster_id, best_image_key, drone_id, embedding_count)
        VALUES ($1::uuid, $2, $3, $4, $5)
        ON CONFLICT DO NOTHING
        """,
        identity_id, cluster_id, best_image_key, drone_id, embedding_count,
    )
    return identity_id


async def upsert_person_identity(
    cluster_id: int,
    best_image_key: Optional[str],
    drone_id: Optional[str],
    embedding_count: int = 1,
) -> str:
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT identity_id::text FROM person_identities WHERE cluster_id=$1 LIMIT 1",
        cluster_id,
    )
    if row:
        await pool.execute(
            """
            UPDATE person_identities
            SET best_image_key=COALESCE($1, best_image_key),
                embedding_count=$2,
                last_seen=now()
            WHERE cluster_id=$3
            """,
            best_image_key, embedding_count, cluster_id,
        )
        return row["identity_id"]
    return await write_person_identity(cluster_id, best_image_key, drone_id, embedding_count)


async def get_person_identities(limit: int = 50) -> List[Dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM person_identities ORDER BY last_seen DESC LIMIT $1", limit
    )
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Evidence frames (MinIO object references)
# ─────────────────────────────────────────────────────────────────────────────

async def write_evidence_frame(
    object_key: str,
    frame_type: str,
    checksum_sha256: str,
    event_id: Optional[str] = None,
    evidence_id: Optional[str] = None,
    bucket: str = "dvas-evidence",
) -> str:
    pool = await get_pool()
    frame_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO evidence_frames
            (frame_id, event_id, evidence_id, object_key, frame_type,
             checksum_sha256, bucket)
        VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7)
        """,
        frame_id, event_id, evidence_id, object_key, frame_type,
        checksum_sha256, bucket,
    )
    return frame_id
