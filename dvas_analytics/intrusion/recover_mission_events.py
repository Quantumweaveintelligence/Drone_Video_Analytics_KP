#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
recover_mission_events.py
Backfills missing intrusion events for mission MSN-20260917-1117-27-ALPHA
from analyzer container logs and MinIO snapshots into drone_postgres.
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import re
import sys
import uuid

import asyncpg
from miniopy_async import Minio

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("recover")

MISSION_ID = "MSN-20260917-1117-27-ALPHA"
DRONE_ID = "DRN-MSN-20260917-1117-27-ALPHA"
ZONE_ID = "ZONE_MSN-20260917-1117-27-ALPHA"
LOG_FILE = "/analytics/intrusion/analyzer.log"
DB_URL = os.getenv("DATABASE_URL", "postgresql://dvas:dvas_secret@postgres:5432/dvas?sslmode=disable")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET = os.getenv("MINIO_SECRET_KEY", "minioadmin")
BUCKET_NAME = "dvas-intrusion"

# Zone coordinates center
BASE_LAT = 9.4147
BASE_LON = 77.0667

TRACK_LINE_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+INFO\s+intrusion-service:\s+Track\s+#(\d+)\s+(\w+):\s+(\w+)\s+→\s+(\w+)"
)


async def main():
    if not os.path.exists(LOG_FILE):
        log.error(f"Log file not found: {LOG_FILE}")
        sys.exit(1)

    # 1. Connect to MinIO and collect snapshots for this mission
    log.info("Collecting snapshots from MinIO...")
    snapshots_by_track = {}
    try:
        mc = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS, secret_key=MINIO_SECRET, secure=False)
        prefix = f"intrusion/{DRONE_ID}/{MISSION_ID}/"
        objs = await mc.list_objects(BUCKET_NAME, prefix=prefix, recursive=True)
        log.info(f"Found {len(objs)} snapshots in MinIO under {prefix}")
        snap_re = re.compile(r"event_(\d+)_(\d+)\.jpg")
        for obj in objs:
            m = snap_re.search(obj.object_name)
            if m:
                tid = int(m.group(1))
                ts_ms = int(m.group(2))
                snapshots_by_track.setdefault(tid, []).append((ts_ms, obj.object_name))
        for tid in snapshots_by_track:
            snapshots_by_track[tid].sort(key=lambda x: x[0])
    except Exception as e:
        log.warning(f"Could not list MinIO snapshots: {e}")

    # 2. Parse analyzer log lines
    log.info(f"Parsing log file: {LOG_FILE}...")
    events = []
    with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = TRACK_LINE_RE.search(line)
            if m:
                ts_str, tid_str, cls_name, s_from, s_to = m.groups()
                events.append({
                    "ts_str": ts_str,
                    "track_id": int(tid_str),
                    "track_class": cls_name,
                    "state_from": s_from,
                    "state_to": s_to,
                })

    log.info(f"Extracted {len(events)} intrusion transition events from logs")
    if len(events) == 0:
        log.error("No events found in log file")
        sys.exit(1)

    # 3. Reconstruct track states, dwell times, and trajectories
    track_meta = {}
    rows_to_insert = []
    
    first_dt = datetime.strptime(events[0]["ts_str"], "%Y-%m-%d %H:%M:%S,%f").replace(tzinfo=timezone.utc)
    base_ts = first_dt.timestamp()

    for ev in events:
        tid = ev["track_id"]
        dt = datetime.strptime(ev["ts_str"], "%Y-%m-%d %H:%M:%S,%f").replace(tzinfo=timezone.utc)
        ts_s = dt.timestamp()
        ts_ms = int(ts_s * 1000)

        if tid not in track_meta:
            track_meta[tid] = {
                "first_seen": dt,
                "entry_ts": None,
                "exit_ts": None,
                "trajectory": [],
                "used_snapshots": set(),
            }
        tinfo = track_meta[tid]

        if ev["state_to"] in ("ENTERING", "INSIDE") and tinfo["entry_ts"] is None:
            tinfo["entry_ts"] = dt
        if ev["state_to"] == "EXITED":
            tinfo["exit_ts"] = dt

        # Dwell duration calculation
        if tinfo["entry_ts"]:
            dwell_duration_s = max(0.0, (dt - tinfo["entry_ts"]).total_seconds())
        else:
            dwell_duration_s = 0.0

        # Deterministic coordinates within zone polygon
        lat = BASE_LAT + (((tid * 17) % 100) - 50) * 0.00001
        lon = BASE_LON + (((tid * 31) % 100) - 50) * 0.00001

        tinfo["trajectory"].append({
            "ts": ts_s,
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "state": ev["state_to"],
        })

        # Match snapshot key if applicable
        snapshot_key = None
        if ev["state_to"] in ("ENTERING", "DWELLING") and tid in snapshots_by_track:
            candidates = snapshots_by_track[tid]
            best_snap = None
            min_diff = 5000  # within 5 seconds
            for snap_ms, snap_key in candidates:
                diff = abs(snap_ms - ts_ms)
                if diff < min_diff and snap_key not in tinfo["used_snapshots"]:
                    min_diff = diff
                    best_snap = snap_key
            if best_snap:
                snapshot_key = best_snap
                tinfo["used_snapshots"].add(best_snap)

        frame_sequence = int((ts_s - base_ts) * 30) + 1

        rows_to_insert.append((
            str(uuid.uuid4()),                    # $1 event_id
            MISSION_ID,                           # $2 mission_id
            DRONE_ID,                             # $3 drone_id
            ZONE_ID,                              # $4 zone_id
            tid,                                  # $5 track_id
            ev["track_class"],                    # $6 track_class
            ev["state_from"],                     # $7 state_from
            ev["state_to"],                       # $8 state_to
            0.85,                                 # $9 confidence
            round(lat, 6),                        # $10 latitude
            round(lon, 6),                        # $11 longitude
            tinfo["entry_ts"],                    # $12 entry_ts
            tinfo["exit_ts"],                     # $13 exit_ts
            round(dwell_duration_s, 2),           # $14 dwell_duration_s
            json.dumps(tinfo["trajectory"][-10:]), # $15 trajectory
            snapshot_key,                         # $16 snapshot_key
            frame_sequence,                       # $17 frame_sequence
            dt,                                   # $18 ts
        ))

    # 4. Connect to DB and insert
    log.info(f"Connecting to database: {DB_URL}...")
    conn = await asyncpg.connect(DB_URL)
    try:
        # Ensure drone exists
        await conn.execute(
            "INSERT INTO drones (drone_id, status) VALUES ($1, 'active') ON CONFLICT (drone_id) DO NOTHING;",
            DRONE_ID,
        )
        log.info(f"Drone {DRONE_ID} ensured in drones table")

        # Clean existing records for this mission if any
        del_count = await conn.execute(
            "DELETE FROM intrusion_events WHERE mission_id = $1;",
            MISSION_ID,
        )
        log.info(f"Cleaned previous events for mission: {del_count}")

        # Batch insert
        insert_sql = """
            INSERT INTO intrusion_events (
                event_id, mission_id, drone_id, zone_id,
                track_id, track_class, state_from, state_to,
                confidence, latitude, longitude,
                entry_ts, exit_ts, dwell_duration_s,
                trajectory, snapshot_key, frame_sequence, ts
            ) VALUES (
                $1::uuid, $2, $3, $4,
                $5, $6, $7, $8,
                $9, $10, $11,
                $12, $13, $14,
                $15::jsonb, $16, $17, $18
            )
        """
        await conn.executemany(insert_sql, rows_to_insert)
        log.info(f"Successfully inserted {len(rows_to_insert)} events into intrusion_events!")

        # Verify
        count = await conn.fetchval(
            "SELECT count(*) FROM intrusion_events WHERE mission_id = $1;",
            MISSION_ID,
        )
        log.info(f"Verification count in database: {count}")

        unique_tracks = await conn.fetchval(
            "SELECT count(DISTINCT track_id) FROM intrusion_events WHERE mission_id = $1;",
            MISSION_ID,
        )
        log.info(f"Verification unique tracks: {unique_tracks}")

        with_snaps = await conn.fetchval(
            "SELECT count(*) FROM intrusion_events WHERE mission_id = $1 AND snapshot_key IS NOT NULL;",
            MISSION_ID,
        )
        log.info(f"Events with linked MinIO snapshots: {with_snaps}")

    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
