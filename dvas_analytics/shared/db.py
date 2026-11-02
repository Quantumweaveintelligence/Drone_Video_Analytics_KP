# -*- coding: utf-8 -*-
"""
DVAS Analytics — PostgreSQL helpers (asyncpg).
All functions use a shared connection pool.
"""
import json
import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any, Set

import asyncpg

from shared.config import cfg

log = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def _init_conn(conn):
    """Register jsonb codec so asyncpg returns dicts/lists instead of strings."""
    await conn.set_type_codec(
        'jsonb', encoder=json.dumps, decoder=json.loads, schema='pg_catalog'
    )
    await conn.set_type_codec(
        'json', encoder=json.dumps, decoder=json.loads, schema='pg_catalog'
    )


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            cfg.DATABASE_URL,
            min_size=2,
            max_size=10,
            command_timeout=30,
            init=_init_conn,
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
    if tracker_id is not None:
        if mission_id:
            existing = await pool.fetchrow(
                """
                SELECT track_id::text FROM tracks
                WHERE drone_id = $1 AND track_type = $2
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
                WHERE drone_id = $1 AND track_type = $2
                  AND metadata->>'tracker_id' = $3
                  AND last_seen > now() - interval '30 minutes'
                ORDER BY first_seen DESC LIMIT 1
                """,
                drone_id, track_type, str(tracker_id),
            )

        if existing:
            await pool.execute(
                """
                UPDATE tracks 
                SET last_seen = $1,
                    mission_id_str = COALESCE(mission_id_str, $2)
                WHERE track_id = $3::uuid
                """,
                last_seen, mission_id, existing["track_id"],
            )
            return existing["track_id"]

        # Track does not exist yet — insert new track
        new_track_id = str(uuid.uuid4())
        await pool.execute(
            """
            INSERT INTO tracks (track_id, drone_id, track_type, first_seen, last_seen,
                                metadata, mission_id_str)
            VALUES ($1::uuid, $2, $3, $4, $5, $6::jsonb, $7)
            """,
            new_track_id, drone_id, track_type, first_seen, last_seen,
            json.dumps({"tracker_id": tracker_id}),
            mission_id,
        )
        return new_track_id

    # Plain insert (no tracker_id)
    track_id = str(uuid.uuid4())
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
    mission_id: Optional[str] = None,
    zone_ref: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    event_id: Optional[str] = None,
) -> str:
    """Insert one confirmed event (an incident, not a detection — B29).

    event_id: pass the id of the module row (intrusion/anomaly/vessel event) so
    the confirmed event, the module row and the Valkey message share one id.
    track_id must be a tracks.track_id UUID; integer tracker ids go in attributes.
    """
    await ensure_drone(drone_id)      # confirmed_events.drone_id → drones FK
    pool = await get_pool()
    event_id = event_id or str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO confirmed_events
            (event_id, drone_id, event_type, confidence, ts, track_id, attributes,
             mission_id_str, zone_ref, latitude, longitude)
        VALUES ($1::uuid, $2, $3, $4, $5, $6::uuid, $7::jsonb, $8, $9, $10, $11)
        """,
        event_id, drone_id, event_type, max(0.0, min(1.0, float(confidence or 0))), ts,
        track_id, json.dumps(attributes or {}, default=str),
        mission_id or None, zone_ref, latitude, longitude,
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
    mission_id: Optional[str] = None,
) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO vehicle_attributes
            (track_id, event_id, vehicle_type, colour_top3, make_model_top3,
             speed_kph, speed_reliability, direction_label, traffic_state, mission_id_str)
        VALUES
            ($1::uuid, $2::uuid, $3, $4::jsonb, $5::jsonb, $6, $7, $8, $9, $10)
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
        mission_id,
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
    has_plate: Optional[bool] = None,
) -> Dict:
    """
    Query vehicle detection and plate read history scoped directly to a mission_id.
    Returns deduplicated vehicle records per tracker_id with plate reads, vehicle attributes,
    and presigned MinIO image keys.
    """
    pool = await get_pool()
    limit = min(limit, 200)

    conditions = []
    params: List = [mission_id]
    idx = 2

    if plate_search:
        conditions.append(f"v.plate_text ILIKE ${idx}")
        params.append(f"%{plate_search.upper().strip()}%")
        idx += 1

    if vehicle_type and vehicle_type.lower() != "all":
        conditions.append(f"LOWER(v.vehicle_type) = ${idx}")
        params.append(vehicle_type.lower().strip())
        idx += 1

    if has_plate:
        conditions.append("((v.plate_text IS NOT NULL AND TRIM(v.plate_text) != '') OR v.plate_image_key IS NOT NULL)")

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    sort_map = {
        "newest":       "v.created_at DESC",
        "oldest":       "v.created_at ASC",
        "confidence":   "v.ocr_confidence DESC NULLS LAST, v.created_at DESC",
        "plate_az":     "v.plate_text ASC NULLS LAST, v.created_at DESC",
        "plates_first": "CASE WHEN (v.plate_text IS NOT NULL AND TRIM(v.plate_text) != '') OR v.plate_image_key IS NOT NULL THEN 0 ELSE 1 END, v.created_at DESC",
    }
    order_by = sort_map.get(sort, "v.created_at DESC")

    cte_base = """
    WITH ranked_tracks AS (
        SELECT
            t.track_id,
            t.drone_id,
            t.mission_id_str,
            COALESCE(t.metadata->>'tracker_id', t.track_id::text) AS tracker_key,
            t.metadata->>'tracker_id' AS tracker_id,
            t.metadata->>'vehicle_image_key' AS meta_veh_img,
            t.metadata->>'plate_image_key' AS meta_plate_img,
            t.first_seen,
            t.last_seen,
            ROW_NUMBER() OVER (
                PARTITION BY t.drone_id, COALESCE(t.metadata->>'tracker_id', t.track_id::text)
                ORDER BY t.last_seen DESC
            ) as trk_rn
        FROM tracks t
        WHERE t.mission_id_str = $1 AND t.track_type = 'vehicle'
    ),
    best_plates AS (
        SELECT 
            COALESCE(t.drone_id, '') AS drone_id,
            COALESCE(t.metadata->>'tracker_id', pr.track_id::text) AS tracker_key,
            pr.plate_text, pr.ocr_confidence, pr.is_reliable,
            pr.plate_image_key, pr.vehicle_image_key, pr.created_at,
            ROW_NUMBER() OVER (
                PARTITION BY COALESCE(t.drone_id, ''), COALESCE(t.metadata->>'tracker_id', pr.track_id::text)
                ORDER BY pr.ocr_confidence DESC, pr.created_at DESC
            ) as rn
        FROM plate_reads pr
        LEFT JOIN tracks t ON t.track_id = pr.track_id
        WHERE pr.mission_id_str = $1
    ),
    latest_va AS (
        SELECT 
            t.drone_id,
            COALESCE(t.metadata->>'tracker_id', va.track_id::text) AS tracker_key,
            va.vehicle_type, va.colour_top3, va.speed_kph,
            ROW_NUMBER() OVER (
                PARTITION BY t.drone_id, COALESCE(t.metadata->>'tracker_id', va.track_id::text)
                ORDER BY va.id DESC
            ) as rn
        FROM tracks t
        JOIN vehicle_attributes va ON va.track_id = t.track_id
        WHERE t.mission_id_str = $1 AND t.track_type = 'vehicle'
    ),
    best_vpl AS (
        SELECT
            vpl.tracker_id,
            vpl.drone_id,
            vpl.mission_id_str,
            vpl.plate_text,
            vpl.plate_conf,
            vpl.plate_image_key,
            vpl.vehicle_image_key,
            vpl.vehicle_type,
            vpl.colour,
            ROW_NUMBER() OVER (
                PARTITION BY vpl.drone_id, vpl.mission_id_str, vpl.tracker_id
                ORDER BY vpl.id DESC
            ) as rn
        FROM vehicle_plate_links vpl
        WHERE vpl.mission_id_str = $1
    ),
    unified_vehicles AS (
        SELECT 
            rt.track_id,
            rt.tracker_id,
            rt.drone_id,
            rt.mission_id_str,
            COALESCE(bp.plate_text, vpl.plate_text) AS plate_text,
            COALESCE(bp.ocr_confidence, vpl.plate_conf) AS ocr_confidence,
            COALESCE(bp.is_reliable, false) AS is_reliable,
            COALESCE(bp.plate_image_key, vpl.plate_image_key, rt.meta_plate_img) AS plate_image_key,
            COALESCE(bp.vehicle_image_key, vpl.vehicle_image_key, rt.meta_veh_img) AS vehicle_image_key,
            COALESCE(va.vehicle_type, vpl.vehicle_type, 'car') AS vehicle_type,
            va.colour_top3,
            COALESCE(vpl.colour, '') AS colour,
            va.speed_kph,
            rt.first_seen,
            rt.last_seen,
            COALESCE(bp.created_at, rt.first_seen) AS created_at
        FROM ranked_tracks rt
        LEFT JOIN best_plates bp ON bp.drone_id = rt.drone_id 
            AND bp.tracker_key = rt.tracker_key 
            AND bp.rn = 1
        LEFT JOIN best_vpl vpl ON vpl.tracker_id = (CASE WHEN rt.tracker_id ~ '^-?[0-9]+$' THEN rt.tracker_id::bigint ELSE NULL END)
            AND vpl.drone_id = rt.drone_id 
            AND vpl.mission_id_str = rt.mission_id_str
            AND vpl.rn = 1
        LEFT JOIN latest_va va ON va.drone_id = rt.drone_id 
            AND va.tracker_key = rt.tracker_key 
            AND va.rn = 1
        WHERE rt.trk_rn = 1
    )
    """

    count_query = f"""
    {cte_base}
    SELECT 
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE (v.plate_text IS NOT NULL AND TRIM(v.plate_text) != '') OR v.plate_image_key IS NOT NULL) AS total_plates_read
    FROM unified_vehicles v
    {where_clause}
    """
    count_row = await pool.fetchrow(count_query, *params)
    total = count_row["total"] if count_row else 0
    total_plates_read = count_row["total_plates_read"] if count_row else 0

    data_params = list(params)
    data_params.extend([limit, offset])
    data_query = f"""
    {cte_base}
    SELECT v.*
    FROM unified_vehicles v
    {where_clause}
    ORDER BY {order_by}
    LIMIT ${idx} OFFSET ${idx + 1}
    """
    rows = await pool.fetch(data_query, *data_params)

    return {
        "total": total,
        "total_plates_read": total_plates_read,
        "rows": [dict(r) for r in rows],
        "offset": offset,
        "limit": limit,
        "has_more": (offset + limit) < total,
    }



# ─────────────────────────────────────────────────────────────────────────────
# Plate watchlist
# ─────────────────────────────────────────────────────────────────────────────

async def add_to_watchlist(plate_text: str, reason: Optional[str] = None,
                           case_ref: Optional[str] = None) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO plate_watchlist (plate_text, reason, case_ref)
        VALUES ($1, $2, $3)
        ON CONFLICT (plate_text) DO UPDATE SET is_active=true, reason=EXCLUDED.reason,
            case_ref=COALESCE(EXCLUDED.case_ref, plate_watchlist.case_ref)
        """,
        plate_text.upper().strip(),
        reason,
        case_ref or None,
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


async def get_active_watchlist_plates() -> Set[str]:
    """Plates the ANPR matcher should flag: active and not past expires_at."""
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT plate_text FROM plate_watchlist "
        "WHERE is_active=true AND (expires_at IS NULL OR expires_at > now())"
    )
    return {r["plate_text"] for r in rows}


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
    arcface_embedding: Optional[List[float]] = None,
) -> str:
    from shared.crypto import encrypt_vector
    pool = await get_pool()
    emb_id = str(uuid.uuid4())
    encrypted_emb = encrypt_vector(embedding)
    encrypted_arcface = encrypt_vector(arcface_embedding) if arcface_embedding else None
    await pool.execute(
        """
        INSERT INTO face_embeddings
            (id, track_id, image_path, encrypted_embedding, drone_id, object_key, mission_id, person_object_key, arcface_embedding, created_at)
        VALUES ($1::uuid, $2::uuid, $3, $4::bytea, $5, $6, $7, $8, $9::bytea, now())
        """,
        emb_id,
        track_id,
        image_path,
        encrypted_emb,
        drone_id,
        object_key,
        mission_id,
        person_object_key,
        encrypted_arcface,
    )
    return emb_id


async def get_all_face_embeddings() -> List[Dict]:
    from shared.crypto import decrypt_vector
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, track_id, image_path, encrypted_embedding, arcface_embedding, drone_id, cluster_id,
               is_duplicate, object_key, person_object_key, mission_id, created_at
        FROM face_embeddings
        ORDER BY created_at DESC
        LIMIT 50000
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
        raw_af = d.pop("arcface_embedding", None)
        if raw_af:
            d["arcface_embedding"] = decrypt_vector(raw_af)
        else:
            d["arcface_embedding"] = None
        results.append(d)
    return results


async def get_face_embeddings_by_mission(mission_id: str) -> List[Dict]:
    """Get face embeddings scoped to a specific mission."""
    from shared.crypto import decrypt_vector
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, track_id, image_path, encrypted_embedding, arcface_embedding, drone_id, cluster_id,
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
        raw_af = d.pop("arcface_embedding", None)
        if raw_af:
            d["arcface_embedding"] = decrypt_vector(raw_af)
        else:
            d["arcface_embedding"] = None
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
        ORDER BY created_at DESC
        LIMIT 50000
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

    # If person_identities has 0 rows for the filter, fall back to person_embeddings
    # directly to guarantee 100% data visibility for all previous and current missions
    if len(results) == 0:
        conds = ["p.object_key IS NOT NULL"]
        p_params = []
        p_idx = 1
        if mission_id:
            conds.append(f"p.mission_id = ${p_idx}")
            p_params.append(mission_id)
            p_idx += 1
        elif drone_id:
            conds.append(f"p.drone_id = ${p_idx}")
            p_params.append(drone_id)
            p_idx += 1
        p_params.append(limit)

        # First attempt: deduplicated rows
        w_dedup = " AND ".join(conds + ["p.is_duplicate = false"])
        emb_rows = await pool.fetch(
            f"""
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
            WHERE {w_dedup}
            ORDER BY p.created_at DESC
            LIMIT ${p_idx}
            """,
            *p_params,
        )
        results = [dict(r) for r in emb_rows]

        # Second attempt: if no is_duplicate=false rows exist, load all detected crops
        if len(results) == 0:
            w_all = " AND ".join(conds)
            emb_rows = await pool.fetch(
                f"""
                SELECT 
                    p.id::text AS identity_id,
                    COALESCE(p.cluster_id, 0) AS cluster_id,
                    NULL AS best_image_key,
                    p.object_key AS best_person_image_key,
                    p.drone_id,
                    p.mission_id,
                    1 AS embedding_count,
                    p.created_at AS last_seen,
                    p.created_at
                FROM person_embeddings p
                WHERE {w_all}
                ORDER BY p.created_at DESC
                LIMIT ${p_idx}
                """,
                *p_params,
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


# ─────────────────────────────────────────────────────────────────────────────
# Person Watchlist & Alerts
# ─────────────────────────────────────────────────────────────────────────────

async def add_to_person_watchlist(
    name: str,
    category: str = "wanted",
    case_ref: Optional[str] = None,
    description: Optional[str] = None,
    reference_image_key: Optional[str] = None,
    reference_image_sha256: Optional[str] = None,
    added_by: Optional[str] = None,
    expires_at: Optional[datetime] = None,
) -> str:
    """Insert or reactivate a person in the face watchlist. Returns watchlist entry ID."""
    pool = await get_pool()
    watchlist_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO person_watchlist (
            id, name, category, case_ref, description,
            reference_image_key, reference_image_sha256,
            added_by, added_at, updated_at, expires_at, is_active
        )
        VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8::uuid, now(), now(), $9, true)
        """,
        watchlist_id,
        name.strip(),
        category.strip().lower(),
        case_ref.strip() if case_ref else None,
        description.strip() if description else None,
        reference_image_key,
        reference_image_sha256,
        added_by,
        expires_at,
    )
    return watchlist_id


async def get_person_watchlist(
    category: Optional[str] = None,
    is_active: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict]:
    """Get watchlist entries, optionally filtered by category and active status."""
    pool = await get_pool()
    conditions = ["is_active = $1"]
    params: List = [is_active]
    idx = 2

    if category:
        conditions.append(f"LOWER(category) = ${idx}")
        params.append(category.strip().lower())
        idx += 1

    where = "WHERE " + " AND ".join(conditions)
    params.extend([limit, offset])

    rows = await pool.fetch(
        f"""
        SELECT pw.*,
               COUNT(we.id) AS embedding_count
        FROM person_watchlist pw
        LEFT JOIN watchlist_embeddings we ON we.watchlist_id = pw.id
        {where}
        GROUP BY pw.id
        ORDER BY pw.added_at DESC
        LIMIT ${idx} OFFSET ${idx + 1}
        """,
        *params,
    )
    return [dict(r) for r in rows]


async def get_person_watchlist_entry(watchlist_id: str) -> Optional[Dict]:
    """Get a single watchlist entry by ID with embedding count."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT pw.*,
               COUNT(we.id) AS embedding_count
        FROM person_watchlist pw
        LEFT JOIN watchlist_embeddings we ON we.watchlist_id = pw.id
        WHERE pw.id = $1::uuid
        GROUP BY pw.id
        """,
        watchlist_id,
    )
    return dict(row) if row else None


async def update_person_watchlist(watchlist_id: str, **fields) -> None:
    """Update mutable fields of a watchlist entry."""
    pool = await get_pool()
    allowed = {"name", "category", "case_ref", "description", "expires_at", "is_active", "reference_image_key", "reference_image_sha256"}
    set_clauses = []
    params: List = [watchlist_id]
    idx = 2

    for k, v in fields.items():
        if k in allowed:
            set_clauses.append(f"{k} = ${idx}")
            params.append(v)
            idx += 1

    if not set_clauses:
        return

    set_clauses.append("updated_at = now()")
    sql = f"UPDATE person_watchlist SET {', '.join(set_clauses)} WHERE id = $1::uuid"
    await pool.execute(sql, *params)


async def remove_from_person_watchlist(watchlist_id: str) -> None:
    """Soft-delete a watchlist entry (deactivate)."""
    pool = await get_pool()
    await pool.execute(
        "UPDATE person_watchlist SET is_active = false, updated_at = now() WHERE id = $1::uuid",
        watchlist_id,
    )


async def delete_person_watchlist(watchlist_id: str) -> None:
    """Permanently delete a watchlist entry and all cascade associations."""
    pool = await get_pool()
    await pool.execute(
        "DELETE FROM person_watchlist WHERE id = $1::uuid",
        watchlist_id,
    )


async def write_watchlist_embedding(
    watchlist_id: str,
    embedding: List[float],
    augmentation_type: str = "original",
    quality_score: float = 0.0,
) -> str:
    """Encrypt and store an ArcFace 512-dim embedding for a watchlist entry."""
    from shared.crypto import encrypt_vector
    pool = await get_pool()
    emb_id = str(uuid.uuid4())
    encrypted_emb = encrypt_vector(embedding)
    await pool.execute(
        """
        INSERT INTO watchlist_embeddings
            (id, watchlist_id, encrypted_embedding, augmentation_type, quality_score, created_at)
        VALUES ($1::uuid, $2::uuid, $3::bytea, $4, $5, now())
        """,
        emb_id, watchlist_id, encrypted_emb, augmentation_type, quality_score,
    )
    return emb_id


async def get_watchlist_embeddings(watchlist_id: str) -> List[Dict]:
    """Retrieve all decrypted embeddings for a watchlist entry."""
    from shared.crypto import decrypt_vector
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, watchlist_id, encrypted_embedding, augmentation_type, quality_score, created_at
        FROM watchlist_embeddings
        WHERE watchlist_id = $1::uuid
        ORDER BY created_at ASC
        """,
        watchlist_id,
    )
    results = []
    for r in rows:
        d = dict(r)
        raw = d.pop("encrypted_embedding", None)
        if raw:
            d["embedding"] = decrypt_vector(raw, expected_dim=512)
        else:
            d["embedding"] = []
        results.append(d)
    return results


async def get_all_active_watchlist_with_embeddings() -> List[Dict]:
    """
    Fetch all active watchlist entries along with their decrypted embeddings.
    Used by WatchlistMatcher for atomic double-buffered in-memory gallery loading.
    """
    from shared.crypto import decrypt_vector
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT pw.id AS watchlist_id, pw.name, pw.category, pw.case_ref,
               we.id AS embedding_id, we.encrypted_embedding, we.augmentation_type, we.quality_score
        FROM person_watchlist pw
        JOIN watchlist_embeddings we ON we.watchlist_id = pw.id
        WHERE pw.is_active = true
          AND (pw.expires_at IS NULL OR pw.expires_at > now())
        ORDER BY pw.id, we.created_at ASC
        """
    )
    entries: Dict[str, Dict] = {}
    for r in rows:
        wid = str(r["watchlist_id"])
        if wid not in entries:
            entries[wid] = {
                "watchlist_id": wid,
                "name": r["name"],
                "category": r["category"],
                "case_ref": r["case_ref"],
                "embeddings": [],
            }
        raw = r["encrypted_embedding"]
        if raw:
            try:
                emb = decrypt_vector(raw, expected_dim=512)
                entries[wid]["embeddings"].append({
                    "embedding_id": str(r["embedding_id"]),
                    "embedding": emb,
                    "augmentation_type": r["augmentation_type"],
                    "quality_score": r["quality_score"],
                })
            except Exception as exc:
                log.warning("Failed to decrypt watchlist embedding %s: %s", r["embedding_id"], exc)
    return list(entries.values())


async def write_person_watchlist_alert(
    watchlist_id: str,
    name: str,
    category: str,
    confidence: float,
    drone_id: Optional[str] = None,
    mission_id: Optional[str] = None,
    tracker_id: Optional[int] = None,
    face_crop_key: Optional[str] = None,
    frame_crop_key: Optional[str] = None,
    face_crop_sha256: Optional[str] = None,
    frame_sha256: Optional[str] = None,
    evidence_packet_key: Optional[str] = None,
    frame_ts: Optional[datetime] = None,
) -> str:
    """Record a watchlist match alert with Section 65B evidence chain fields."""
    pool = await get_pool()
    alert_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO person_watchlist_alerts (
            id, watchlist_id, name, category, drone_id, mission_id, tracker_id,
            confidence, face_crop_key, frame_crop_key, face_crop_sha256, frame_sha256,
            evidence_packet_key, detected_at, frame_ts, is_confirmed
        )
        VALUES (
            $1::uuid, $2::uuid, $3, $4, $5, $6, $7,
            $8, $9, $10, $11, $12,
            $13, now(), $14, false
        )
        """,
        alert_id, watchlist_id, name, category, drone_id, mission_id, tracker_id,
        confidence, face_crop_key, frame_crop_key, face_crop_sha256, frame_sha256,
        evidence_packet_key, frame_ts,
    )
    return alert_id


async def get_person_watchlist_alerts(
    limit: int = 50,
    offset: int = 0,
    mission_id: Optional[str] = None,
    drone_id: Optional[str] = None,
    category: Optional[str] = None,
    is_confirmed: Optional[bool] = None,
) -> List[Dict]:
    """Retrieve recent watchlist alerts with optional filtering."""
    pool = await get_pool()
    conditions = []
    params: List = []
    idx = 1

    if mission_id:
        conditions.append(f"pwa.mission_id = ${idx}")
        params.append(mission_id)
        idx += 1
    if drone_id:
        conditions.append(f"pwa.drone_id = ${idx}")
        params.append(drone_id)
        idx += 1
    if category:
        conditions.append(f"LOWER(pwa.category) = ${idx}")
        params.append(category.strip().lower())
        idx += 1
    if is_confirmed is not None:
        conditions.append(f"pwa.is_confirmed = ${idx}")
        params.append(is_confirmed)
        idx += 1
    # Exclude dismissed alerts by default
    conditions.append("COALESCE(pwa.is_dismissed, false) = false AND (pwa.notes IS NULL OR LOWER(pwa.notes) NOT LIKE '%dismiss%')")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.extend([min(limit, 200), offset])

    rows = await pool.fetch(
        f"""
        SELECT pwa.*,
               pw.case_ref, pw.description AS suspect_notes, pw.reference_image_key
        FROM person_watchlist_alerts pwa
        LEFT JOIN person_watchlist pw ON pw.id = pwa.watchlist_id
        {where}
        ORDER BY pwa.detected_at DESC
        LIMIT ${idx} OFFSET ${idx + 1}
        """,
        *params,
    )
    return [dict(r) for r in rows]


async def confirm_watchlist_alert(
    alert_id: str,
    user_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> None:
    """Confirm a watchlist match alert."""
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE person_watchlist_alerts
        SET is_confirmed = true,
            confirmed_by = $2::uuid,
            confirmed_at = now(),
            notes = COALESCE($3, notes)
        WHERE id = $1::uuid
        """,
        alert_id, user_id, notes,
    )


async def dismiss_watchlist_alert(alert_id: str, notes: Optional[str] = None) -> None:
    """Dismiss a false-positive alert."""
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE person_watchlist_alerts
        SET is_confirmed = false,
            is_dismissed = true,
            dismissed_at = now(),
            notes = COALESCE($2, 'Dismissed by operator')
        WHERE id = $1::uuid
        """,
        alert_id, notes,
    )



# ═══════════════════════════════════════════════════════════════════════════
# Maritime Vessel Tracking DB Helpers
# ═══════════════════════════════════════════════════════════════════════════

async def upsert_vessel_track(
    pool,
    drone_id: str,
    mission_id: Optional[str],
    tracker_id: int,
    class_id: int,
    class_name: str,
    confidence: float,
    bbox_xyxy: list,
    size_bucket: str,
    direction: str,
    is_stationary: bool,
    trajectory: list,
    thumbnail_key: Optional[str] = None,
) -> None:
    """Insert or update a tracked maritime vessel in vessel_tracks."""
    import json
    bbox_json = json.dumps(bbox_xyxy)
    traj_json = json.dumps(trajectory)
    query = """
    INSERT INTO vessel_tracks (
        drone_id, mission_id, tracker_id, class_id, class_name,
        size_bucket, direction, is_stationary, confidence,
        first_seen, last_seen, duration_s, bbox_xyxy, trajectory, thumbnail_key
    ) VALUES (
        $1, $2, $3, $4, $5,
        $6, $7, $8, $9,
        now(), now(), 0, $10::jsonb, $11::jsonb, $12
    )
    ON CONFLICT (drone_id, tracker_id, COALESCE(mission_id, ''))
    DO UPDATE SET
        class_id = EXCLUDED.class_id,
        class_name = EXCLUDED.class_name,
        size_bucket = EXCLUDED.size_bucket,
        direction = EXCLUDED.direction,
        is_stationary = EXCLUDED.is_stationary,
        confidence = EXCLUDED.confidence,
        last_seen = now(),
        duration_s = EXTRACT(EPOCH FROM (now() - vessel_tracks.first_seen)),
        bbox_xyxy = EXCLUDED.bbox_xyxy,
        trajectory = EXCLUDED.trajectory,
        thumbnail_key = COALESCE(EXCLUDED.thumbnail_key, vessel_tracks.thumbnail_key)
    """
    await pool.execute(
        query,
        drone_id, mission_id, tracker_id, class_id, class_name,
        size_bucket, direction, is_stationary, float(confidence),
        bbox_json, traj_json, thumbnail_key
    )


async def write_vessel_event(
    pool,
    drone_id: str,
    mission_id: Optional[str],
    tracker_id: int,
    event_type: str,
    class_name: str,
    confidence: float,
    snapshot_key: Optional[str] = None,
    details: Optional[dict] = None,
) -> str:
    """Write a confirmed vessel alert / event to vessel_events."""
    import json
    details_json = json.dumps(details or {})
    row = await pool.fetchrow(
        """
        INSERT INTO vessel_events (
            drone_id, mission_id, tracker_id, event_type,
            class_name, confidence, snapshot_key, details, ts
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8::jsonb, now()
        )
        RETURNING event_id::text
        """,
        drone_id, mission_id, tracker_id, event_type,
        class_name, float(confidence), snapshot_key, details_json
    )
    return row["event_id"] if row else ""


async def get_active_vessel_tracks(
    pool,
    drone_id: Optional[str] = None,
    mission_id: Optional[str] = None,
    limit: int = 100,
    active_window_minutes: int = 60,
) -> List[dict]:
    """Retrieve active or recently seen vessel tracks (default last 60 minutes)."""
    where = f"WHERE last_seen > now() - interval '{int(active_window_minutes)} minutes'"
    params = []
    if drone_id:
        params.append(drone_id)
        where += f" AND drone_id = ${len(params)}"
    if mission_id:
        params.append(mission_id)
        where += f" AND mission_id = ${len(params)}"
    params.append(min(limit, 200))
    limit_clause = f" LIMIT ${len(params)}"

    rows = await pool.fetch(
        f"""
        SELECT id, drone_id, mission_id, tracker_id, class_id, class_name,
               size_bucket, direction, is_stationary, confidence,
               first_seen, last_seen, duration_s, bbox_xyxy, trajectory, thumbnail_key
        FROM vessel_tracks
        {where}
        ORDER BY last_seen DESC
        {limit_clause}
        """,
        *params
    )
    return [dict(r) for r in rows]


async def get_vessel_class_stats(
    pool,
    drone_id: Optional[str] = None,
    mission_id: Optional[str] = None,
) -> List[dict]:
    """Aggregate unique vessel counts grouped by class."""
    conditions = []
    params = []
    if drone_id:
        params.append(drone_id)
        conditions.append(f"drone_id = ${len(params)}")
    if mission_id:
        params.append(mission_id)
        conditions.append(f"mission_id = ${len(params)}")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = await pool.fetch(
        f"""
        SELECT class_name, COUNT(*) as count,
               ROUND(AVG(confidence)::numeric, 2) as avg_confidence
        FROM vessel_tracks
        {where}
        GROUP BY class_name
        ORDER BY count DESC
        """,
        *params
    )
    return [dict(r) for r in rows]


async def get_vessel_events(
    pool,
    drone_id: Optional[str] = None,
    mission_id: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = 50,
) -> List[dict]:
    """Retrieve recent vessel events."""
    conditions = []
    params = []
    if drone_id:
        params.append(drone_id)
        conditions.append(f"drone_id = ${len(params)}")
    if mission_id:
        params.append(mission_id)
        conditions.append(f"mission_id = ${len(params)}")
    if event_type:
        params.append(event_type)
        conditions.append(f"event_type = ${len(params)}")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(min(limit, 200))
    limit_idx = len(params)

    rows = await pool.fetch(
        f"""
        SELECT event_id::text, drone_id, mission_id, tracker_id,
               event_type, class_name, confidence, snapshot_key,
               details, ts
        FROM vessel_events
        {where}
        ORDER BY ts DESC
        LIMIT ${limit_idx}
        """,
        *params
    )
    return [dict(r) for r in rows]


async def get_vessel_summary_stats(
    pool,
    drone_id: Optional[str] = None,
    mission_id: Optional[str] = None,
) -> dict:
    """Retrieve summary metrics for maritime analytics dashboard."""
    params = []
    filters = []
    if drone_id:
        params.append(drone_id)
        filters.append(f"drone_id = ${len(params)}")
    if mission_id:
        params.append(mission_id)
        filters.append(f"mission_id = ${len(params)}")

    extra_and = (" AND " + " AND ".join(filters)) if filters else ""

    live_row = await pool.fetchrow(
        f"""
        SELECT COUNT(*) as live_count
        FROM vessel_tracks
        WHERE last_seen > now() - interval '15 seconds' {extra_and}
        """,
        *params
    )
    total_row = await pool.fetchrow(
        f"""
        SELECT COUNT(*) as total_unique,
               COUNT(*) FILTER (WHERE is_stationary = true) as stationary_count,
               COUNT(*) FILTER (WHERE is_stationary = false) as moving_count
        FROM vessel_tracks
        WHERE 1=1 {extra_and}
        """,
        *params
    )
    events_row = await pool.fetchrow(
        f"""
        SELECT COUNT(*) as proximity_alerts
        FROM vessel_events
        WHERE event_type = 'proximity_alert'
          AND ts > now() - interval '24 hours' {extra_and}
        """,
        *params
    )
    return {
        "live_count": live_row["live_count"] if live_row else 0,
        "cumulative_count": total_row["total_unique"] if total_row else 0,
        "stationary_count": total_row["stationary_count"] if total_row else 0,
        "moving_count": total_row["moving_count"] if total_row else 0,
        "proximity_alerts_24h": events_row["proximity_alerts"] if events_row else 0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Anomaly & Suspicious Activity
# ─────────────────────────────────────────────────────────────────────────────

async def upsert_anomaly_track(
    pool,
    drone_id: str,
    track_id: int,
    class_name: str,
    class_category: str,
    suspicion_level: str,
    suspicion_score: float,
    pipeline_stage: str,
    duration_s: float,
    trajectory: list,
    ground_trajectory: list,
    attributes: dict,
    bbox_xyxy: list,
    is_stationary: bool = False,
    stationary_seconds: float = 0.0,
    owner_track_id: Optional[int] = None,
    owner_distance_px: Optional[float] = None,
    snapshot_key: str = "",
    mission_id: Optional[str] = None,
) -> None:
    """Upsert an anomaly track record."""
    await pool.execute(
        """
        INSERT INTO anomaly_tracks (
            drone_id, track_id, mission_id, class_name, class_category,
            suspicion_level, suspicion_score, pipeline_stage, duration_s,
            trajectory, ground_trajectory, attributes, bbox_xyxy,
            is_stationary, stationary_seconds, owner_track_id, owner_distance_px,
            snapshot_key, first_seen, last_seen
        )
        VALUES (
            $1, $2, $3, $4, $5,
            $6, $7, $8, $9,
            $10, $11, $12, $13,
            $14, $15, $16, $17,
            $18, now(), now()
        )
        ON CONFLICT (drone_id, track_id, COALESCE(mission_id, ''))
        DO UPDATE SET
            class_name = EXCLUDED.class_name,
            class_category = EXCLUDED.class_category,
            suspicion_level = EXCLUDED.suspicion_level,
            suspicion_score = EXCLUDED.suspicion_score,
            pipeline_stage = EXCLUDED.pipeline_stage,
            duration_s = EXCLUDED.duration_s,
            trajectory = EXCLUDED.trajectory,
            ground_trajectory = EXCLUDED.ground_trajectory,
            attributes = EXCLUDED.attributes,
            bbox_xyxy = EXCLUDED.bbox_xyxy,
            is_stationary = EXCLUDED.is_stationary,
            stationary_seconds = EXCLUDED.stationary_seconds,
            owner_track_id = COALESCE(EXCLUDED.owner_track_id, anomaly_tracks.owner_track_id),
            owner_distance_px = COALESCE(EXCLUDED.owner_distance_px, anomaly_tracks.owner_distance_px),
            snapshot_key = CASE WHEN EXCLUDED.snapshot_key != '' THEN EXCLUDED.snapshot_key ELSE anomaly_tracks.snapshot_key END,
            last_seen = now()
        """,
        drone_id, track_id, mission_id, class_name, class_category,
        suspicion_level, suspicion_score, pipeline_stage, duration_s,
        trajectory, ground_trajectory, attributes, bbox_xyxy,
        is_stationary, stationary_seconds, owner_track_id, owner_distance_px,
        snapshot_key
    )


async def write_anomaly_event(
    pool,
    drone_id: str,
    track_id: int,
    event_type: str,
    severity: str,
    class_name: str,
    confidence_detector: float,
    confidence_rule: float,
    confidence_event: float,
    confidence_alert: float,
    evidence_groups: list,
    reasons: list,
    summary: str,
    snapshot_key: str,
    bbox: list,
    owner_track_id: Optional[int] = None,
    owner_status: Optional[str] = None,
    temporal: Optional[dict] = None,
    gps_lat: Optional[float] = None,
    gps_lon: Optional[float] = None,
    details: Optional[dict] = None,
    mission_id: Optional[str] = None,
) -> str:
    """Write an anomaly event. Returns event_id (UUID string)."""
    row = await pool.fetchrow(
        """
        INSERT INTO anomaly_events (
            drone_id, track_id, mission_id, event_type, severity, class_name,
            confidence_detector, confidence_rule, confidence_event, confidence_alert,
            evidence_groups, reasons, summary, snapshot_key, bbox,
            owner_track_id, owner_status, temporal, gps_lat, gps_lon, details, ts
        )
        VALUES (
            $1, $2, $3, $4, $5, $6,
            $7, $8, $9, $10,
            $11, $12, $13, $14, $15,
            $16, $17, $18, $19, $20, $21, now()
        )
        RETURNING event_id::text
        """,
        drone_id, track_id, mission_id, event_type, severity, class_name,
        confidence_detector, confidence_rule, confidence_event, confidence_alert,
        evidence_groups or [], reasons or [], summary or "", snapshot_key or "", bbox or [],
        owner_track_id, owner_status, temporal or {}, gps_lat, gps_lon, details or {}
    )
    return row["event_id"] if row else ""


async def get_active_anomaly_tracks(
    pool,
    drone_id: Optional[str] = None,
    limit: int = 100,
) -> List[dict]:
    """Retrieve active anomaly tracks seen in the last 15 seconds."""
    params = []
    where = "WHERE last_seen > now() - interval '15 seconds'"
    if drone_id:
        params.append(drone_id)
        where += f" AND drone_id = ${len(params)}"
    params.append(min(limit, 200))
    limit_idx = len(params)
    rows = await pool.fetch(
        f"""
        SELECT id, track_id, drone_id, mission_id, class_name, class_category,
               suspicion_level, suspicion_score, pipeline_stage, duration_s,
               trajectory, ground_trajectory, attributes, bbox_xyxy,
               is_stationary, stationary_seconds, owner_track_id, owner_distance_px,
               snapshot_key, first_seen, last_seen
        FROM anomaly_tracks
        {where}
        ORDER BY suspicion_score DESC, last_seen DESC
        LIMIT ${limit_idx}
        """,
        *params
    )
    return [dict(r) for r in rows]


async def get_anomaly_events(
    pool,
    drone_id: Optional[str] = None,
    mission_id: Optional[str] = None,
    event_type: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 50,
) -> List[dict]:
    """Retrieve recent anomaly events."""
    conditions = []
    params = []
    if drone_id:
        params.append(drone_id)
        conditions.append(f"drone_id = ${len(params)}")
    if mission_id:
        params.append(mission_id)
        conditions.append(f"mission_id = ${len(params)}")
    if event_type:
        params.append(event_type)
        conditions.append(f"event_type = ${len(params)}")
    if severity:
        params.append(severity)
        conditions.append(f"severity = ${len(params)}")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(min(limit, 200))
    limit_idx = len(params)

    rows = await pool.fetch(
        f"""
        SELECT event_id::text, drone_id, mission_id, track_id,
               event_type, severity, class_name,
               confidence_detector, confidence_rule, confidence_event, confidence_alert,
               evidence_groups, reasons, summary, snapshot_key, bbox,
               owner_track_id, owner_status, temporal, gps_lat, gps_lon, details, ts
        FROM anomaly_events
        {where}
        ORDER BY ts DESC
        LIMIT ${limit_idx}
        """,
        *params
    )
    return [dict(r) for r in rows]


async def get_anomaly_summary_stats(
    pool,
    drone_id: Optional[str] = None,
    mission_id: Optional[str] = None,
) -> dict:
    """Retrieve summary metrics for anomaly monitoring dashboard."""
    params = []
    filters = []
    if drone_id:
        params.append(drone_id)
        filters.append(f"drone_id = ${len(params)}")
    if mission_id:
        params.append(mission_id)
        filters.append(f"mission_id = ${len(params)}")

    extra_and = (" AND " + " AND ".join(filters)) if filters else ""

    live_row = await pool.fetchrow(
        f"""
        SELECT COUNT(*) as live_count,
               COUNT(*) FILTER (WHERE class_category = 'person') as live_persons,
               COUNT(*) FILTER (WHERE class_category = 'vehicle') as live_vehicles,
               COUNT(*) FILTER (WHERE class_category = 'object') as live_objects,
               COUNT(*) FILTER (WHERE suspicion_level IN ('MEDIUM', 'HIGH', 'CRITICAL')) as suspicious_count
        FROM anomaly_tracks
        WHERE last_seen > now() - interval '15 seconds' {extra_and}
        """,
        *params
    )
    total_row = await pool.fetchrow(
        f"""
        SELECT COUNT(*) as total_unique
        FROM anomaly_tracks
        WHERE 1=1 {extra_and}
        """,
        *params
    )
    events_row = await pool.fetchrow(
        f"""
        SELECT COUNT(*) as total_alerts,
               COUNT(*) FILTER (WHERE severity = 'CRITICAL') as critical_alerts,
               COUNT(*) FILTER (WHERE severity = 'HIGH') as high_alerts,
               COUNT(*) FILTER (WHERE severity = 'MEDIUM') as medium_alerts,
               ROUND(AVG(confidence_alert)::numeric, 2) as avg_confidence
        FROM anomaly_events
        WHERE ts > now() - interval '24 hours' {extra_and}
        """,
        *params
    )
    return {
        "live_count": live_row["live_count"] if live_row else 0,
        "live_persons": live_row["live_persons"] if live_row else 0,
        "live_vehicles": live_row["live_vehicles"] if live_row else 0,
        "live_objects": live_row["live_objects"] if live_row else 0,
        "suspicious_count": live_row["suspicious_count"] if live_row else 0,
        "cumulative_count": total_row["total_unique"] if total_row else 0,
        "total_alerts_24h": events_row["total_alerts"] if events_row else 0,
        "critical_alerts_24h": events_row["critical_alerts"] if events_row else 0,
        "high_alerts_24h": events_row["high_alerts"] if events_row else 0,
        "medium_alerts_24h": events_row["medium_alerts"] if events_row else 0,
        "avg_alert_confidence": float(events_row["avg_confidence"]) if events_row and events_row["avg_confidence"] else 0.0,
    }


async def get_anomaly_stats_by_type(
    pool,
    drone_id: Optional[str] = None,
    mission_id: Optional[str] = None,
) -> List[dict]:
    """Retrieve counts by anomaly event type."""
    params = []
    conditions = []
    if drone_id:
        params.append(drone_id)
        conditions.append(f"drone_id = ${len(params)}")
    if mission_id:
        params.append(mission_id)
        conditions.append(f"mission_id = ${len(params)}")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = await pool.fetch(
        f"""
        SELECT event_type, COUNT(*) as count,
               ROUND(AVG(confidence_alert)::numeric, 2) as avg_confidence
        FROM anomaly_events
        {where}
        GROUP BY event_type
        ORDER BY count DESC
        """,
        *params
    )
    return [dict(r) for r in rows]
