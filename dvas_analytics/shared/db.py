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
    mission_id: Optional[str] = None,
) -> str:
    """
    Insert or update a track row. Returns track_id (UUID string).
    Deduplication: if tracker_id is provided, upsert by (drone_id, tracker_id, track_type).
    mission_id is stored as TEXT in mission_id_str column for string-format mission IDs
    (e.g. MSN-20260813-1530-01-ALPHA).
    """
    await ensure_drone(drone_id)
    pool = await get_pool()
    track_id = str(uuid.uuid4())
    if tracker_id is not None:
        row = await pool.fetchrow(
            """
            INSERT INTO tracks (track_id, drone_id, track_type, first_seen, last_seen,
                                metadata, mission_id_str)
            VALUES ($1::uuid, $2, $3, $4, $5, $6::jsonb, $7)
            ON CONFLICT DO NOTHING
            RETURNING track_id::text
            """,
            track_id, drone_id, track_type, first_seen, last_seen,
            json.dumps({"tracker_id": tracker_id}),
            mission_id,
        )
        if row:
            return str(row["track_id"])
        # Already exists — fetch it and update mission_id_str if now available
        if mission_id:
            existing = await pool.fetchrow(
                """
                SELECT track_id::text FROM tracks
                WHERE drone_id=$1 AND track_type=$2
                  AND metadata->>'tracker_id' = $3
                  AND mission_id_str = $4
                ORDER BY first_seen DESC LIMIT 1
                """,
                drone_id, track_type, str(tracker_id), mission_id,
            )
        else:
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
            # Update last_seen and mission_id_str
            await pool.execute(
                """
                UPDATE tracks SET last_seen=$1,
                    mission_id_str = COALESCE(mission_id_str, $2)
                WHERE track_id=$3::uuid
                """,
                last_seen, mission_id, existing["track_id"],
            )
            return existing["track_id"]
    # Plain insert
    await pool.execute(
        """
        INSERT INTO tracks (track_id, drone_id, track_type, first_seen, last_seen, mission_id_str)
        VALUES ($1::uuid, $2, $3, $4, $5, $6)
        ON CONFLICT (track_id) DO UPDATE SET
            last_seen = EXCLUDED.last_seen,
            mission_id_str = COALESCE(tracks.mission_id_str, EXCLUDED.mission_id_str)
        """,
        track_id, drone_id, track_type, first_seen, last_seen, mission_id,
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
        from shared.config import get_drone_aliases
        aliases = get_drone_aliases(drone_id)
        rows = await pool.fetch(
            "SELECT * FROM crowd_density_readings WHERE drone_id = ANY($1::text[]) ORDER BY ts DESC LIMIT $2",
            aliases, limit,
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
    mission_id: Optional[str] = None,
) -> str:
    pool = await get_pool()
    link_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO vehicle_plate_links
            (id, tracker_id, drone_id, plate_text, plate_conf,
             vehicle_type, colour, plate_image_key, vehicle_image_key, mission_id_str)
        VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (tracker_id, drone_id, plate_text)
        DO UPDATE SET
            plate_conf        = GREATEST(vehicle_plate_links.plate_conf, EXCLUDED.plate_conf),
            colour            = COALESCE(EXCLUDED.colour, vehicle_plate_links.colour),
            plate_image_key   = COALESCE(EXCLUDED.plate_image_key, vehicle_plate_links.plate_image_key),
            vehicle_image_key = COALESCE(EXCLUDED.vehicle_image_key, vehicle_plate_links.vehicle_image_key),
            mission_id_str    = COALESCE(EXCLUDED.mission_id_str, vehicle_plate_links.mission_id_str),
            linked_at         = now()
        """,
        link_id, tracker_id, drone_id, plate_text, plate_conf,
        vehicle_type, colour, plate_image_key, vehicle_image_key, mission_id,
    )
    return link_id


async def get_vehicle_plate_link(
    tracker_id: int,
    drone_id: str,
    mission_id: Optional[str] = None,
) -> Optional[Dict]:
    pool = await get_pool()
    if mission_id:
        row = await pool.fetchrow(
            """
            SELECT * FROM vehicle_plate_links
            WHERE tracker_id=$1 AND drone_id=$2 AND mission_id_str=$3
            ORDER BY plate_conf DESC LIMIT 1
            """,
            tracker_id, drone_id, mission_id,
        )
    else:
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
    mission_id: Optional[str] = None,
) -> str:
    pool = await get_pool()
    plate_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO plate_reads
            (id, track_id, event_id, plate_text, ocr_confidence, is_reliable,
             vehicle_image_b64, plate_image_key, vehicle_image_key, mission_id_str)
        VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (track_id) DO UPDATE SET
            plate_text        = EXCLUDED.plate_text,
            ocr_confidence    = EXCLUDED.ocr_confidence,
            is_reliable       = EXCLUDED.is_reliable,
            vehicle_image_b64 = COALESCE(EXCLUDED.vehicle_image_b64, plate_reads.vehicle_image_b64),
            plate_image_key   = COALESCE(EXCLUDED.plate_image_key,   plate_reads.plate_image_key),
            vehicle_image_key = COALESCE(EXCLUDED.vehicle_image_key, plate_reads.vehicle_image_key),
            mission_id_str    = COALESCE(EXCLUDED.mission_id_str, plate_reads.mission_id_str),
            updated_at        = now()
        """,
        plate_id, track_id, event_id, plate_text, ocr_confidence, is_reliable,
        vehicle_image_b64, plate_image_key, vehicle_image_key, mission_id,
    )
    return plate_id


async def get_recent_plates(limit: int = 50, drone_id: Optional[str] = None, mission_id: Optional[str] = None) -> List[Dict]:
    pool = await get_pool()
    conditions = []
    params: List = []
    idx = 1
    if drone_id:
        conditions.append(f"t.drone_id = ${idx}")
        params.append(drone_id)
        idx += 1
    if mission_id:
        conditions.append(f"pr.mission_id_str = ${idx}")
        params.append(mission_id)
        idx += 1
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(min(limit, 200))
    rows = await pool.fetch(
        f"""
        SELECT pr.*, t.drone_id, t.first_seen, t.last_seen, t.mission_id_str AS track_mission_id
        FROM plate_reads pr
        LEFT JOIN tracks t ON pr.track_id = t.track_id
        {where}
        ORDER BY pr.created_at DESC
        LIMIT ${idx}
        """,
        *params,
    )
    return [dict(r) for r in rows]


async def get_all_missions() -> List[str]:
    """Get all distinct mission IDs from the plate_reads table."""
    pool = await get_pool()
    query = """
        SELECT DISTINCT mission_id_str 
        FROM plate_reads 
        WHERE mission_id_str IS NOT NULL 
        ORDER BY mission_id_str DESC
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query)
        return [r["mission_id_str"] for r in rows]

async def get_plates_by_mission(
    mission_id: str,
    plate_search: str = "",
    vehicle_type: str = "",
    sort: str = "newest",
    limit: int = 24,
    offset: int = 0,
) -> Dict:
    """
    Query historical plate reads scoped directly to a mission_id string.
    Returns pagination metadata + vehicles with presigned image URLs.
    """
    pool = await get_pool()
    limit = min(limit, 200)

    conditions = ["pr.mission_id_str = $1"]
    params: List = [mission_id]
    idx = 2

    if plate_search:
        conditions.append(f"pr.plate_text ILIKE ${idx}")
        params.append(f"%{plate_search.upper().strip()}%")
        idx += 1

    if vehicle_type and vehicle_type.lower() != "all":
        conditions.append(f"LOWER(va.vehicle_type) = ${idx}")
        params.append(vehicle_type.lower().strip())
        idx += 1

    where_clause = "WHERE " + " AND ".join(conditions)

    sort_map = {
        "newest":     "pr.created_at DESC",
        "oldest":     "pr.created_at ASC",
        "confidence": "pr.ocr_confidence DESC NULLS LAST",
        "plate_az":   "pr.plate_text ASC NULLS LAST",
    }
    order_by = sort_map.get(sort, "pr.created_at DESC")

    count_row = await pool.fetchrow(
        f"""
        SELECT COUNT(*) AS total
        FROM plate_reads pr
        JOIN tracks t ON t.track_id = pr.track_id
        LEFT JOIN vehicle_attributes va ON va.track_id = t.track_id
        {where_clause}
        """,
        *params,
    )
    total = count_row["total"] if count_row else 0

    params.extend([limit, offset])
    rows = await pool.fetch(
        f"""
        SELECT
            pr.id, pr.plate_text, pr.ocr_confidence, pr.is_reliable,
            pr.plate_image_key, pr.vehicle_image_key, pr.created_at, pr.mission_id_str,
            t.drone_id, t.first_seen, t.last_seen,
            t.metadata->>'tracker_id' AS tracker_id,
            va.vehicle_type, va.colour_top3, va.speed_kph
        FROM plate_reads pr
        JOIN tracks t ON t.track_id = pr.track_id
        LEFT JOIN vehicle_attributes va ON va.track_id = t.track_id
        {where_clause}
        ORDER BY {order_by}
        LIMIT ${idx} OFFSET ${idx + 1}
        """,
        *params,
    )
    return {
        "total": total,
        "rows": [dict(r) for r in rows],
        "offset": offset,
        "limit": limit,
        "has_more": (offset + limit) < total,
    }


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
    mission_id: Optional[str] = None,
    person_object_key: Optional[str] = None,
) -> str:
    from shared.crypto import encrypt_vector
    pool = await get_pool()
    emb_id = str(uuid.uuid4())
    encrypted_emb = encrypt_vector(embedding)
    await pool.execute(
        """
        INSERT INTO face_embeddings
            (id, track_id, image_path, encrypted_embedding, drone_id, object_key, mission_id, person_object_key, created_at)
        VALUES ($1::uuid, $2::uuid, $3, $4::bytea, $5, $6, $7, $8, now())
        """,
        emb_id,
        track_id,
        image_path,
        encrypted_emb,
        drone_id,
        object_key,
        mission_id,
        person_object_key,
    )
    return emb_id


async def get_all_face_embeddings() -> List[Dict]:
    from shared.crypto import decrypt_vector
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, track_id, image_path, encrypted_embedding, drone_id, cluster_id,
               is_duplicate, object_key, person_object_key, mission_id, created_at
        FROM face_embeddings
        ORDER BY created_at
        """
    )
    results = []
    for r in rows:
        d = dict(r)
        raw = d.pop("encrypted_embedding", None)
        if raw:
            d["embedding"] = decrypt_vector(raw)
        else:
            d["embedding"] = []
        results.append(d)
    return results


async def get_face_embeddings_by_mission(mission_id: str) -> List[Dict]:
    """Get face embeddings scoped to a specific mission."""
    from shared.crypto import decrypt_vector
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, track_id, image_path, encrypted_embedding, drone_id, cluster_id,
               is_duplicate, object_key, person_object_key, mission_id, created_at
        FROM face_embeddings
        WHERE mission_id = $1
        ORDER BY created_at
        """,
        mission_id,
    )
    results = []
    for r in rows:
        d = dict(r)
        raw = d.pop("encrypted_embedding", None)
        if raw:
            d["embedding"] = decrypt_vector(raw)
        else:
            d["embedding"] = []
        results.append(d)
    return results


async def get_distinct_missions_face_embeddings() -> List[Dict]:
    """Get distinct missions with face embedding counts and dedup stats, joined with dedup_runs."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT f.mission_id,
               COUNT(f.id) AS total_faces,
               COUNT(f.id) FILTER (WHERE f.is_duplicate = true) AS duplicates,
               COUNT(f.id) FILTER (WHERE f.cluster_id IS NOT NULL) AS clustered,
               MIN(f.created_at) AS first_seen,
               MAX(f.created_at) AS last_seen,
               r.status AS dedup_status,
               r.unique_found,
               r.duplicates_removed,
               r.completed_at AS dedup_completed_at
        FROM face_embeddings f
        LEFT JOIN dedup_runs r ON f.mission_id = r.mission_id
        WHERE f.mission_id IS NOT NULL
        GROUP BY f.mission_id, r.status, r.unique_found, r.duplicates_removed, r.completed_at
        ORDER BY MAX(f.created_at) DESC
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
# Person embeddings (Whole-body Person Re-ID)
# ─────────────────────────────────────────────────────────────────────────────

async def write_person_embedding(
    track_id: Optional[str],
    image_path: str,
    embedding: List[float],
    drone_id: Optional[str] = None,
    object_key: Optional[str] = None,
    mission_id: Optional[str] = None,
    tracker_id: Optional[int] = None,
    sharpness_score: float = 0.0,
) -> str:
    from shared.crypto import encrypt_vector
    pool = await get_pool()
    emb_id = str(uuid.uuid4())
    encrypted_emb = encrypt_vector(embedding)
    await pool.execute(
        """
        INSERT INTO person_embeddings
            (id, track_id, image_path, encrypted_embedding, drone_id, object_key, mission_id, tracker_id, sharpness_score, created_at)
        VALUES ($1::uuid, $2::uuid, $3, $4::bytea, $5, $6, $7, $8, $9, now())
        """,
        emb_id,
        track_id,
        image_path,
        encrypted_emb,
        drone_id,
        object_key,
        mission_id,
        tracker_id,
        sharpness_score,
    )
    return emb_id


async def get_all_person_embeddings() -> List[Dict]:
    from shared.crypto import decrypt_vector
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, track_id, tracker_id, image_path, encrypted_embedding, drone_id, cluster_id,
               is_duplicate, object_key, mission_id, sharpness_score, created_at
        FROM person_embeddings
        ORDER BY created_at
        """
    )
    results = []
    for r in rows:
        d = dict(r)
        raw = d.pop("encrypted_embedding", None)
        if raw:
            d["embedding"] = decrypt_vector(raw)
        else:
            d["embedding"] = []
        results.append(d)
    return results


async def get_person_embeddings_by_mission(mission_id: str) -> List[Dict]:
    """Get person embeddings scoped to a specific mission."""
    from shared.crypto import decrypt_vector
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, track_id, tracker_id, image_path, encrypted_embedding, drone_id, cluster_id,
               is_duplicate, object_key, mission_id, sharpness_score, created_at
        FROM person_embeddings
        WHERE mission_id = $1
        ORDER BY created_at
        """,
        mission_id,
    )
    results = []
    for r in rows:
        d = dict(r)
        raw = d.pop("encrypted_embedding", None)
        if raw:
            d["embedding"] = decrypt_vector(raw)
        else:
            d["embedding"] = []
        results.append(d)
    return results


async def update_person_embedding_cluster(
    embedding_id: str,
    cluster_id: int,
    is_duplicate: bool,
) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE person_embeddings
        SET cluster_id=$1, is_duplicate=$2
        WHERE id=$3::uuid
        """,
        cluster_id, is_duplicate, embedding_id,
    )


async def get_distinct_missions_person_embeddings() -> List[Dict]:
    """Get distinct missions with person embedding counts and dedup stats, joined with dedup_runs."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT p.mission_id,
               COUNT(p.id) AS total_persons,
               COUNT(p.id) FILTER (WHERE p.is_duplicate = true) AS duplicates,
               COUNT(p.id) FILTER (WHERE p.cluster_id IS NOT NULL) AS clustered,
               MIN(p.created_at) AS first_seen,
               MAX(p.created_at) AS last_seen,
               r.status AS dedup_status,
               r.unique_found,
               r.duplicates_removed,
               r.completed_at AS dedup_completed_at
        FROM person_embeddings p
        LEFT JOIN dedup_runs r ON p.mission_id = r.mission_id
        WHERE p.mission_id IS NOT NULL
        GROUP BY p.mission_id, r.status, r.unique_found, r.duplicates_removed, r.completed_at
        ORDER BY MAX(p.created_at) DESC
        """
    )
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Mission Deduplication Runs
# ─────────────────────────────────────────────────────────────────────────────

async def upsert_dedup_run(
    mission_id: str,
    status: str = "pending",
    total_embeddings: int = 0,
    unique_found: int = 0,
    duplicates_removed: int = 0,
    progress_pct: float = 0.0,
    started_at = None,
    completed_at = None,
    error_message: Optional[str] = None,
) -> None:
    """Insert or update a mission deduplication run record."""
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO dedup_runs (
            mission_id, status, total_embeddings, unique_found,
            duplicates_removed, progress_pct, started_at, completed_at, error_message
        )
        VALUES ($1, $2, $3, $4, $5, $6, COALESCE($7, now()), $8, $9)
        ON CONFLICT (mission_id) DO UPDATE SET
            status = EXCLUDED.status,
            total_embeddings = EXCLUDED.total_embeddings,
            unique_found = EXCLUDED.unique_found,
            duplicates_removed = EXCLUDED.duplicates_removed,
            progress_pct = EXCLUDED.progress_pct,
            completed_at = COALESCE(EXCLUDED.completed_at, dedup_runs.completed_at),
            error_message = EXCLUDED.error_message
        """,
        mission_id, status, total_embeddings, unique_found,
        duplicates_removed, float(progress_pct), started_at, completed_at, error_message,
    )


async def get_dedup_run(mission_id: str) -> Optional[Dict]:
    """Get a single mission deduplication run record."""
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM dedup_runs WHERE mission_id = $1",
        mission_id,
    )
    return dict(row) if row else None


async def get_dedup_runs() -> List[Dict]:
    """Get all mission deduplication run records."""
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM dedup_runs ORDER BY started_at DESC"
    )
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Person identities (Re-ID)
# ─────────────────────────────────────────────────────────────────────────────

async def write_person_identity(
    cluster_id: int,
    best_image_key: Optional[str],
    drone_id: Optional[str],
    embedding_count: int = 1,
    best_person_image_key: Optional[str] = None,
    mission_id: Optional[str] = None,
) -> str:
    pool = await get_pool()
    identity_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO person_identities
            (identity_id, cluster_id, best_image_key, best_person_image_key, drone_id, embedding_count, mission_id)
        VALUES ($1::uuid, $2, $3, $4, $5, $6, $7)
        ON CONFLICT DO NOTHING
        """,
        identity_id, cluster_id, best_image_key, best_person_image_key, drone_id, embedding_count, mission_id,
    )
    return identity_id


async def upsert_person_identity(
    cluster_id: int,
    best_image_key: Optional[str],
    drone_id: Optional[str],
    embedding_count: int = 1,
    best_person_image_key: Optional[str] = None,
    mission_id: Optional[str] = None,
) -> str:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT identity_id::text FROM person_identities 
        WHERE COALESCE(mission_id, '') = COALESCE($1, '') AND cluster_id = $2 
        LIMIT 1
        """,
        mission_id, cluster_id,
    )
    if row:
        await pool.execute(
            """
            UPDATE person_identities
            SET best_image_key=COALESCE($1, best_image_key),
                best_person_image_key=COALESCE($2, best_person_image_key),
                embedding_count=$3,
                drone_id=COALESCE($4, drone_id),
                last_seen=now()
            WHERE COALESCE(mission_id, '') = COALESCE($5, '') AND cluster_id = $6
            """,
            best_image_key, best_person_image_key, embedding_count, drone_id, mission_id, cluster_id,
        )
        return row["identity_id"]
    return await write_person_identity(
        cluster_id, best_image_key, drone_id, embedding_count,
        best_person_image_key=best_person_image_key, mission_id=mission_id,
    )


async def get_person_identities(
    limit: int = 50,
    drone_id: Optional[str] = None,
    mission_id: Optional[str] = None,
) -> List[Dict]:
    pool = await get_pool()
    conditions = []
    params = []
    idx = 1
    if mission_id:
        conditions.append(f"mission_id = ${idx}")
        params.append(mission_id)
        idx += 1
    elif drone_id:
        conditions.append(f"drone_id = ${idx}")
        params.append(drone_id)
        idx += 1
    params.append(limit)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = await pool.fetch(
        f"SELECT * FROM person_identities {where} ORDER BY last_seen DESC LIMIT ${idx}",
        *params,
    )
    results = [dict(r) for r in rows]

    # If querying by mission_id and person_identities has fewer rows or is missing,
    # fall back to person_embeddings directly to guarantee 100% data visibility
    if mission_id and len(results) == 0:
        emb_rows = await pool.fetch(
            """
            SELECT 
                p.id::text AS identity_id,
                COALESCE(p.cluster_id, 0) AS cluster_id,
                NULL AS best_image_key,
                p.object_key AS best_person_image_key,
                p.drone_id,
                p.mission_id,
                COALESCE((
                    SELECT COUNT(*) FROM person_embeddings p2 
                    WHERE p2.mission_id = p.mission_id 
                      AND p2.cluster_id = p.cluster_id 
                      AND p.cluster_id IS NOT NULL
                ), 1) AS embedding_count,
                p.created_at AS last_seen,
                p.created_at
            FROM person_embeddings p
            WHERE p.mission_id = $1 AND p.is_duplicate = false AND p.object_key IS NOT NULL
            ORDER BY p.created_at DESC
            LIMIT $2
            """,
            mission_id, limit,
        )
        results = [dict(r) for r in emb_rows]

    return results


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
