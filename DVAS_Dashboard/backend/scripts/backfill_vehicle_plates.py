#!/usr/bin/env python3
"""
Backfill vehicle plate image keys in dvas_postgres.evidence from drone_postgres plate_reads & vehicle_plate_links.
"""
import asyncio
import logging
from datetime import datetime, timezone
import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_vehicle_plates")

DVAS_DB_URL = "postgresql://dvas:dvas_secret@dvas_postgres:5432/dvas"
DRONE_DB_URL = "postgresql://dvas:dvas_secret@drone_postgres:5432/dvas"

async def run_backfill():
    logger.info("Connecting to databases...")
    conn_dvas = await asyncpg.connect(DVAS_DB_URL)
    conn_drone = await asyncpg.connect(DRONE_DB_URL)

    try:
        # 1. Fetch vehicle evidence with empty secondary_image_key
        evs = await conn_dvas.fetch("""
            SELECT id, mission_id, drone_id, primary_image_key, ts 
            FROM evidence 
            WHERE event_type LIKE '%vehicle%' 
              AND (secondary_image_key = '' OR secondary_image_key IS NULL)
            ORDER BY ts DESC
        """)
        logger.info("Found %d vehicle evidence rows with empty secondary_image_key", len(evs))
        if not evs:
            logger.info("All vehicle evidence rows already have secondary_image_key.")
            return

        # 2. Fetch plate_reads and vehicle_plate_links
        pr = await conn_drone.fetch("""
            SELECT vehicle_image_key, plate_image_key, mission_id_str, created_at 
            FROM plate_reads 
            WHERE plate_image_key IS NOT NULL AND plate_image_key != ''
            ORDER BY created_at DESC
        """)
        vpl = await conn_drone.fetch("""
            SELECT vehicle_image_key, plate_image_key, mission_id_str, linked_at 
            FROM vehicle_plate_links 
            WHERE plate_image_key IS NOT NULL AND plate_image_key != ''
            ORDER BY linked_at DESC
        """)

        # Build lookups
        veh_to_plate = {}
        mission_to_plates = {}
        all_plates = []

        for r in pr:
            pk = r['plate_image_key']
            vk = r['vehicle_image_key']
            m = r['mission_id_str']
            dt = r['created_at']
            if vk:
                veh_to_plate[vk] = pk
            if m:
                mission_to_plates.setdefault(m, []).append((dt, pk))
            all_plates.append(pk)

        for r in vpl:
            pk = r['plate_image_key']
            vk = r['vehicle_image_key']
            m = r['mission_id_str']
            dt = r['linked_at']
            if vk and vk not in veh_to_plate:
                veh_to_plate[vk] = pk
            if m:
                mission_to_plates.setdefault(m, []).append((dt, pk))
            all_plates.append(pk)

        fallback_plate = all_plates[0] if all_plates else ""

        # 3. Match and update
        updates = []
        for ev in evs:
            ev_id = ev['id']
            primary_key = ev['primary_image_key'] or ""
            m_id = ev['mission_id'] or ""
            raw_ts = ev['ts']

            ts_dt = None
            if raw_ts:
                try:
                    if isinstance(raw_ts, str):
                        ts_dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                    elif isinstance(raw_ts, datetime):
                        ts_dt = raw_ts
                except Exception:
                    ts_dt = None

            matched_plate = ""
            # A: Exact vehicle_image_key match
            if primary_key in veh_to_plate:
                matched_plate = veh_to_plate[primary_key]
            # B: Closest plate read in the same mission
            elif m_id in mission_to_plates:
                m_plates = mission_to_plates[m_id]
                if ts_dt and m_plates:
                    best = min(m_plates, key=lambda x: abs((x[0] - ts_dt).total_seconds()) if x[0] else 999999)
                    matched_plate = best[1]
                elif m_plates:
                    matched_plate = m_plates[0][1]

            if matched_plate:
                updates.append((matched_plate, ev_id))

        logger.info("Matched %d out of %d vehicle evidence rows with plate images", len(updates), len(evs))

        # 4. Batch update in dvas_postgres
        if updates:
            await conn_dvas.executemany(
                "UPDATE evidence SET secondary_image_key = $1 WHERE id = $2",
                updates
            )
            logger.info("Successfully updated %d rows in dvas_postgres.evidence", len(updates))

    finally:
        await conn_dvas.close()
        await conn_drone.close()

if __name__ == "__main__":
    asyncio.run(run_backfill())
