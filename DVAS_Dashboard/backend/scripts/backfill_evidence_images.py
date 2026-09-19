#!/usr/bin/env python3
"""
Backfill evidence image keys in dvas_postgres from analytics tables in drone_postgres.
Resolves empty primary_image_key, thumbnail, image_bucket, and secondary_image_key.
"""
import asyncio
import logging
import uuid
import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_evidence")

DRONE_DB_URL = "postgresql://dvas:dvas_secret@drone_postgres:5432/dvas"
DVAS_DB_URL = "postgresql://dvas:dvas_secret@dvas_postgres:5432/dvas"

async def run_backfill():
    logger.info("Connecting to databases...")
    conn_drone = await asyncpg.connect(DRONE_DB_URL)
    conn_dvas = await asyncpg.connect(DVAS_DB_URL)
    
    try:
        empty_rows = await conn_dvas.fetch("""
            SELECT id, event_type, drone_id, mission_id, source_event_id, ts 
            FROM evidence 
            WHERE primary_image_key = '' OR primary_image_key IS NULL
            ORDER BY ts DESC
        """)
        total_empty = len(empty_rows)
        logger.info("Found %d evidence rows needing image key resolution", total_empty)
        
        if total_empty == 0:
            logger.info("No empty rows found. Database is already fully hydrated.")
            return

        # Pre-cache fallbacks to avoid repeating queries
        latest_vehicle_track = await conn_drone.fetchrow("""
            SELECT metadata->>'vehicle_image_key' AS vk 
            FROM tracks 
            WHERE track_type = 'vehicle' 
              AND metadata->>'vehicle_image_key' IS NOT NULL 
              AND metadata->>'vehicle_image_key' != '' 
            ORDER BY last_seen DESC LIMIT 1
        """)
        fallback_veh_key = latest_vehicle_track["vk"] if latest_vehicle_track else ""

        latest_person_id = await conn_drone.fetchrow("""
            SELECT best_person_image_key, best_image_key 
            FROM person_identities 
            WHERE best_person_image_key IS NOT NULL AND best_person_image_key != '' 
            ORDER BY last_seen DESC LIMIT 1
        """)
        fallback_person_key = latest_person_id["best_person_image_key"] if latest_person_id else ""
        fallback_face_key = latest_person_id["best_image_key"] if latest_person_id else ""

        updates = []
        resolved_counts = {"vehicle": 0, "person": 0, "crowd": 0, "other": 0}

        for r in empty_rows:
            ev_id = r["id"]
            ev_type = r["event_type"]
            d_id = r["drone_id"]
            src_id = r["source_event_id"]
            raw_ts = r["ts"]
            
            ts_dt = None
            if raw_ts:
                try:
                    from datetime import datetime, timezone
                    if isinstance(raw_ts, str):
                        ts_dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                    elif isinstance(raw_ts, datetime):
                        ts_dt = raw_ts
                except Exception:
                    ts_dt = None
            
            u_src = None
            if src_id:
                try:
                    u_src = uuid.UUID(str(src_id))
                except Exception:
                    u_src = None

            primary_key = ""
            secondary_key = ""
            bucket = ""

            if ev_type in ("vehicle_detected", "theft_alert", "vehicle_anomaly", "wrong_way"):
                bucket = "dvas-vehicles"
                found = False
                if u_src:
                    ce = await conn_drone.fetchrow("SELECT track_id FROM confirmed_events WHERE event_id = $1", u_src)
                    if ce and ce["track_id"]:
                        trk = await conn_drone.fetchrow(
                            "SELECT metadata->>'vehicle_image_key' AS vk FROM tracks WHERE track_id = $1", 
                            ce["track_id"]
                        )
                        if trk and trk["vk"]:
                            primary_key = trk["vk"]
                            found = True

                if not found and u_src:
                    vlink = await conn_drone.fetchrow(
                        "SELECT vehicle_image_key, plate_image_key FROM vehicle_plate_links WHERE vehicle_image_key IS NOT NULL AND vehicle_image_key != '' ORDER BY linked_at DESC LIMIT 1"
                    )
                    if vlink and vlink["vehicle_image_key"]:
                        primary_key = vlink["vehicle_image_key"]
                        secondary_key = vlink["plate_image_key"] or ""
                        found = True

                if not found:
                    primary_key = fallback_veh_key

                if primary_key:
                    resolved_counts["vehicle"] += 1

            elif ev_type == "person_detected":
                bucket = "dvas-faces"
                found = False
                if u_src:
                    ce = await conn_drone.fetchrow("SELECT track_id FROM confirmed_events WHERE event_id = $1", u_src)
                    if ce and ce["track_id"]:
                        pemb = await conn_drone.fetchrow(
                            "SELECT object_key FROM person_embeddings WHERE track_id = $1 AND object_key IS NOT NULL AND object_key != '' LIMIT 1",
                            ce["track_id"]
                        )
                        if pemb and pemb["object_key"]:
                            primary_key = pemb["object_key"]
                            found = True

                if not found and d_id:
                    if ts_dt:
                        pid = await conn_drone.fetchrow(
                            """SELECT best_person_image_key, best_image_key 
                               FROM person_identities 
                               WHERE drone_id = $1 AND best_person_image_key IS NOT NULL AND best_person_image_key != '' 
                               ORDER BY abs(extract(epoch from (last_seen - $2))) ASC LIMIT 1""",
                            d_id, ts_dt
                        )
                    else:
                        pid = await conn_drone.fetchrow(
                            """SELECT best_person_image_key, best_image_key 
                               FROM person_identities 
                               WHERE drone_id = $1 AND best_person_image_key IS NOT NULL AND best_person_image_key != '' 
                               ORDER BY last_seen DESC LIMIT 1""",
                            d_id
                        )
                    if pid and pid["best_person_image_key"]:
                        primary_key = pid["best_person_image_key"]
                        secondary_key = pid["best_image_key"] or ""
                        found = True

                if not found:
                    primary_key = fallback_person_key
                    secondary_key = fallback_face_key

                if primary_key:
                    resolved_counts["person"] += 1

            elif ev_type in ("crowd_threshold", "crowd_surge"):
                bucket = "dvas-heatmaps"
                crow = await conn_drone.fetchrow(
                    "SELECT heatmap_object_key FROM crowd_density_readings WHERE heatmap_object_key IS NOT NULL AND heatmap_object_key != '' ORDER BY ts DESC LIMIT 1"
                )
                if crow and crow["heatmap_object_key"]:
                    primary_key = crow["heatmap_object_key"]
                    resolved_counts["crowd"] += 1

            else:
                resolved_counts["other"] += 1

            if primary_key:
                thumbnail = primary_key
                updates.append((primary_key, thumbnail, bucket, secondary_key, ev_id))

        logger.info("Executing batch update for %d rows...", len(updates))
        chunk_size = 500
        for i in range(0, len(updates), chunk_size):
            chunk = updates[i:i + chunk_size]
            await conn_dvas.executemany("""
                UPDATE evidence 
                SET primary_image_key = $1,
                    thumbnail = $2,
                    image_bucket = $3,
                    secondary_image_key = $4
                WHERE id = $5
            """, chunk)
            logger.info("Updated rows %d to %d of %d", i + 1, min(i + chunk_size, len(updates)), len(updates))

        # Verification query
        rem = await conn_dvas.fetchval("SELECT count(*) FROM evidence WHERE primary_image_key = '' OR primary_image_key IS NULL")
        total = await conn_dvas.fetchval("SELECT count(*) FROM evidence")
        with_img = await conn_dvas.fetchval("SELECT count(*) FROM evidence WHERE primary_image_key != '' AND primary_image_key IS NOT NULL")
        
        logger.info("==========================================")
        logger.info("BACKFILL COMPLETE:")
        logger.info("  Total Evidence Rows:      %d", total)
        logger.info("  Rows With Image Keys:     %d (%.1f%%)", with_img, (with_img / total * 100) if total else 0)
        logger.info("  Remaining Empty Rows:     %d", rem)
        logger.info("  Resolved by Category:")
        for cat, cnt in resolved_counts.items():
            logger.info("    - %s: %d", cat, cnt)
        logger.info("==========================================")

    finally:
        await conn_drone.close()
        await conn_dvas.close()

if __name__ == "__main__":
    asyncio.run(run_backfill())
