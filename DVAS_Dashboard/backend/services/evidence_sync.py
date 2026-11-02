"""
Evidence sync — periodically polls the analytics gateway for confirmed events
and creates Evidence rows in the dashboard database.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta

import httpx

from database import async_session_factory
from models import Evidence, Mission
from sqlalchemy import select
from core.config import ANALYTICS_BASE

import asyncpg

from core.config import ANALYTICS_DATABASE_URL

_warned_no_analytics_db = False


async def _resolve_image_keys(event_type, event_id, track_id, drone_id, ts, mission_id=""):
    """Resolve real MinIO image keys from the analytics DB.

    Exact matches (event_id / track_id) first. A nearest-in-time match from the
    same drone or mission is accepted only within ±10 s; there is no "latest image"
    fallback, so an event with no matching image gets none (B2, until E1 stores the
    exact key with the event).
    """
    global _warned_no_analytics_db
    primary_key = ""
    secondary_key = ""
    bucket = ""
    if not ANALYTICS_DATABASE_URL:
        if not _warned_no_analytics_db:
            logger.error("ANALYTICS_DATABASE_URL is not set; evidence images will not be resolved")
            _warned_no_analytics_db = True
        return primary_key, secondary_key, bucket
    
    ts_dt = None
    if isinstance(ts, str) and ts:
        try:
            ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            ts_dt = None
    elif isinstance(ts, datetime):
        ts_dt = ts
    if not ts_dt:
        ts_dt = datetime.now(timezone.utc)
        
    try:
        conn = await asyncpg.connect(ANALYTICS_DATABASE_URL)
        try:
            if event_type == "person_detected":
                # Tier 1: Check person_embeddings or face_embeddings by track_id
                if track_id:
                    try:
                        row = await conn.fetchrow(
                            "SELECT object_key FROM person_embeddings WHERE track_id = $1::uuid AND object_key IS NOT NULL AND object_key != '' LIMIT 1",
                            track_id
                        )
                        if row and row['object_key']:
                            primary_key = row['object_key']
                            bucket = "dvas-faces"
                    except Exception:
                        pass
                    
                    if not primary_key:
                        try:
                            frow = await conn.fetchrow(
                                "SELECT object_key, person_object_key FROM face_embeddings WHERE track_id = $1::uuid LIMIT 1",
                                track_id
                            )
                            if frow:
                                primary_key = frow['person_object_key'] or frow['object_key'] or ""
                                secondary_key = frow['object_key'] if frow['person_object_key'] else ""
                                bucket = "dvas-faces"
                        except Exception:
                            pass

                # Tier 2: Check person_identities for matching drone_id, ordered by time proximity to ts_dt
                if not primary_key:
                    if drone_id:
                        prow = await conn.fetchrow(
                            """SELECT best_person_image_key, best_image_key 
                               FROM person_identities 
                               WHERE drone_id = $1 AND ((best_person_image_key IS NOT NULL AND best_person_image_key != '') OR (best_image_key IS NOT NULL AND best_image_key != ''))
                               AND abs(extract(epoch from (last_seen - $2))) <= 10
                               ORDER BY abs(extract(epoch from (last_seen - $2))) ASC LIMIT 1""",
                            drone_id, ts_dt
                        )
                    else:
                        prow = None
                        
                    if prow:
                        primary_key = prow['best_person_image_key'] or prow['best_image_key'] or ""
                        secondary_key = prow['best_image_key'] if prow['best_person_image_key'] and prow['best_image_key'] else ""
                        bucket = "dvas-faces"


            elif event_type in ("vehicle_detected", "theft_alert", "vehicle_anomaly", "wrong_way"):
                bucket = "dvas-vehicles"
                
                # 1. Try track_id direct lookup in plate_reads (provides both vehicle & plate)
                if track_id:
                    try:
                        p_row = await conn.fetchrow(
                            "SELECT vehicle_image_key, plate_image_key FROM plate_reads WHERE track_id = $1::uuid LIMIT 1",
                            track_id
                        )
                        if p_row:
                            if not primary_key and p_row['vehicle_image_key']:
                                primary_key = p_row['vehicle_image_key']
                            if not secondary_key and p_row['plate_image_key']:
                                secondary_key = p_row['plate_image_key']
                    except Exception:
                        pass

                # 2. Try tracks table for vehicle_image_key
                if track_id and not primary_key:
                    try:
                        trow = await conn.fetchrow(
                            "SELECT metadata->>'vehicle_image_key' AS vk FROM tracks WHERE track_id = $1::uuid AND metadata->>'vehicle_image_key' IS NOT NULL AND metadata->>'vehicle_image_key' != '' LIMIT 1",
                            track_id
                        )
                        if trow and trow['vk']:
                            primary_key = trow['vk']
                    except Exception:
                        pass

                # 3. If primary_key is known, look up vehicle_plate_links or plate_reads by vehicle_image_key for plate
                if primary_key and not secondary_key:
                    try:
                        vlink = await conn.fetchrow(
                            "SELECT plate_image_key FROM vehicle_plate_links WHERE vehicle_image_key = $1 AND plate_image_key IS NOT NULL AND plate_image_key != '' LIMIT 1",
                            primary_key
                        )
                        if vlink and vlink['plate_image_key']:
                            secondary_key = vlink['plate_image_key']
                    except Exception:
                        pass

                    if not secondary_key:
                        try:
                            pr_row = await conn.fetchrow(
                                "SELECT plate_image_key FROM plate_reads WHERE vehicle_image_key = $1 AND plate_image_key IS NOT NULL AND plate_image_key != '' LIMIT 1",
                                primary_key
                            )
                            if pr_row and pr_row['plate_image_key']:
                                secondary_key = pr_row['plate_image_key']
                        except Exception:
                            pass

                # 4. Check event_id in plate_reads
                if (not primary_key or not secondary_key) and event_id:
                    try:
                        erow = await conn.fetchrow(
                            "SELECT vehicle_image_key, plate_image_key FROM plate_reads WHERE event_id = $1::uuid LIMIT 1",
                            event_id
                        )
                        if erow:
                            if not primary_key and erow['vehicle_image_key']:
                                primary_key = erow['vehicle_image_key']
                            if not secondary_key and erow['plate_image_key']:
                                secondary_key = erow['plate_image_key']
                    except Exception:
                        pass

                # 5. Check mission_id & time proximity in plate_reads
                if (not primary_key or not secondary_key) and mission_id:
                    try:
                        mrow = await conn.fetchrow(
                            """SELECT vehicle_image_key, plate_image_key 
                               FROM plate_reads 
                               WHERE mission_id_str = $1 
                               AND abs(extract(epoch from (created_at - $2))) <= 10
                               ORDER BY abs(extract(epoch from (created_at - $2))) ASC LIMIT 1""",
                            mission_id, ts_dt
                        )
                        if mrow:
                            if not primary_key and mrow['vehicle_image_key']:
                                primary_key = mrow['vehicle_image_key']
                            if not secondary_key and mrow['plate_image_key']:
                                secondary_key = mrow['plate_image_key']
                    except Exception:
                        pass



            elif event_type.startswith("intrusion") or event_type == "perimeter_intrusion":
                irow = None
                if event_id:
                    try:
                        irow = await conn.fetchrow(
                            "SELECT snapshot_key FROM intrusion_events WHERE event_id = $1::uuid LIMIT 1",
                            event_id
                        )
                    except Exception:
                        pass
                if not irow and drone_id:
                    irow = await conn.fetchrow(
                        """SELECT snapshot_key FROM intrusion_events 
                           WHERE drone_id = $1 AND snapshot_key IS NOT NULL AND snapshot_key != '' 
                           AND abs(extract(epoch from (ts - $2))) <= 10
                           ORDER BY abs(extract(epoch from (ts - $2))) ASC LIMIT 1""",
                        drone_id, ts_dt
                    )
                if irow and irow['snapshot_key']:
                    primary_key = irow['snapshot_key']
                    bucket = "dvas-intrusion"

            elif event_type.startswith("vessel") or event_type in ("vessel_detected", "vessel_proximity", "vessel_loitering"):
                v_row = None
                if event_id:
                    try:
                        v_row = await conn.fetchrow(
                            "SELECT snapshot_key FROM vessel_events WHERE event_id = $1::uuid LIMIT 1",
                            event_id
                        )
                    except Exception:
                        pass
                if not v_row and track_id:
                    try:
                        t_row = await conn.fetchrow(
                            "SELECT thumbnail_key FROM vessel_tracks WHERE tracker_id = $1::int LIMIT 1",
                            int(track_id)
                        )
                        if t_row and t_row['thumbnail_key']:
                            primary_key = t_row['thumbnail_key']
                            bucket = "dvas-vessels-maritime"
                    except Exception:
                        pass
                if not v_row and not primary_key and drone_id:
                    v_row = await conn.fetchrow(
                        """SELECT snapshot_key FROM vessel_events 
                           WHERE drone_id = $1 AND snapshot_key IS NOT NULL AND snapshot_key != '' 
                           AND abs(extract(epoch from (ts - $2))) <= 10
                           ORDER BY abs(extract(epoch from (ts - $2))) ASC LIMIT 1""",
                        drone_id, ts_dt
                    )
                if v_row and v_row['snapshot_key']:
                    primary_key = v_row['snapshot_key']
                    bucket = "dvas-vessels-maritime"

            elif event_type in ("crowd_threshold", "crowd_surge"):
                crow = None
                if drone_id:
                    crow = await conn.fetchrow(
                        """SELECT heatmap_object_key FROM crowd_density_readings 
                           WHERE drone_id = $1 AND heatmap_object_key IS NOT NULL AND heatmap_object_key != ''
                           AND abs(extract(epoch from (ts - $2))) <= 10
                           ORDER BY abs(extract(epoch from (ts - $2))) ASC LIMIT 1""",
                        drone_id, ts_dt
                    )
                if crow and crow['heatmap_object_key']:
                    primary_key = crow['heatmap_object_key']
                    bucket = "dvas-heatmaps"

            elif event_type in (
                "abandoned_object", "suspicious_loitering", "person_fall",
                "suspicious_following", "crowd_dispersal", "vehicle_wrong_way",
                "vehicle_abrupt_stop", "suspicious_dropoff", "weapon_aiming",
                "anomaly_alert", "suspicious_activity"
            ) or event_type.startswith("anomaly_"):
                a_row = None
                if event_id:
                    try:
                        a_row = await conn.fetchrow(
                            "SELECT snapshot_key FROM anomaly_events WHERE event_id = $1::uuid LIMIT 1",
                            event_id
                        )
                    except Exception:
                        pass
                if not a_row and track_id:
                    try:
                        t_row = await conn.fetchrow(
                            "SELECT snapshot_key FROM anomaly_tracks WHERE track_id = $1::int LIMIT 1",
                            int(track_id)
                        )
                        if t_row and t_row['snapshot_key']:
                            primary_key = t_row['snapshot_key']
                            bucket = "dvas-anomaly"
                    except Exception:
                        pass
                if not a_row and not primary_key and drone_id:
                    a_row = await conn.fetchrow(
                        """SELECT snapshot_key FROM anomaly_events 
                           WHERE drone_id = $1 AND snapshot_key IS NOT NULL AND snapshot_key != '' 
                           AND abs(extract(epoch from (ts - $2))) <= 10
                           ORDER BY abs(extract(epoch from (ts - $2))) ASC LIMIT 1""",
                        drone_id, ts_dt
                    )
                if a_row and a_row['snapshot_key']:
                    primary_key = a_row['snapshot_key']
                    bucket = "dvas-anomaly"

        finally:
            await conn.close()
    except Exception as exc:
        logger.debug("Image key resolution failed for %s/%s: %s", event_type, event_id, exc)
    
    return primary_key, secondary_key, bucket

logger = logging.getLogger("dvas.evidence_sync")

_last_sync_ts: str = ""
_SYNC_INTERVAL_S = 30


def _event_type_label(event_type: str) -> str:
    """Human-readable label for confirmed event types."""
    labels = {
        "crowd_threshold": "Crowd surge detected",
        "theft_alert": "Stolen/watchlisted vehicle",
        "intrusion_alert": "Perimeter intrusion",
        "vehicle_detected": "Vehicle of interest",
        "abandoned_object": "Unattended / Abandoned Object",
        "suspicious_loitering": "Suspicious Loitering",
        "person_fall": "Person Collapse / Fall",
        "suspicious_following": "Suspicious Following Pattern",
        "crowd_dispersal": "Rapid Crowd Dispersal",
        "vehicle_wrong_way": "Vehicle Wrong-Way Flow",
        "vehicle_abrupt_stop": "Vehicle Abrupt Stop",
        "suspicious_dropoff": "Suspicious Vehicle Dropoff",
        "weapon_aiming": "Weapon / Arm Aiming Gesture",
    }
    return labels.get(event_type, f"Confirmed {event_type.replace('_', ' ')}")


async def _sync_once():
    global _last_sync_ts
    
    since = _last_sync_ts or (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{ANALYTICS_BASE}/events/confirmed",
                params={"since": since, "limit": 100}
            )
            if resp.status_code != 200:
                logger.warning("Evidence sync: gateway returned %d", resp.status_code)
                return
            data = resp.json()
    except Exception as exc:
        logger.debug("Evidence sync: gateway unreachable: %s", exc)
        return
    
    events = data.get("events", [])
    if not events:
        return
    
    created = 0
    async with async_session_factory() as session:
        for ev in events:
            event_id = str(ev.get("event_id", ""))
            if not event_id:
                continue
            
            # Check if already synced (dedup on source_event_id)
            existing = await session.execute(
                select(Evidence).where(Evidence.source_event_id == event_id)
            )
            if existing.scalar_one_or_none():
                continue
            
            # Determine thumbnail from attributes
            attrs = ev.get("attributes") or {}
            if isinstance(attrs, str):
                try:
                    import json
                    attrs = json.loads(attrs)
                except Exception:
                    attrs = {}
            
            drone_id = ev.get("drone_id", "")
            mission_id = str(ev.get("mission_id", "")) if ev.get("mission_id") else ""
            if not mission_id and drone_id.startswith("DRN-MSN-"):
                mission_id = drone_id[4:]
            
            # Enrich jurisdiction from mission or default to Thiruvananthapuram
            jurisdiction = "Thiruvananthapuram"
            if mission_id:
                msn_res = await session.execute(
                    select(Mission.jurisdiction).where(Mission.mission_id == mission_id)
                )
                m_jur = msn_res.scalar_one_or_none()
                if m_jur:
                    jurisdiction = m_jur
            
            event_type = ev.get("event_type", "unknown")
            confidence = float(ev.get("confidence", 0))
            ts = str(ev.get("ts", ""))
            
            primary_key, secondary_key, bucket = await _resolve_image_keys(
                event_type, event_id, str(ev.get("track_id", "")), drone_id, ts, mission_id
            )
            
            evidence_row = Evidence(
                id=f"EVI-{uuid.uuid4().hex[:8]}",
                label=f"{_event_type_label(event_type)} — {drone_id}",
                mission_id=mission_id,
                jurisdiction=jurisdiction,
                drone_id=drone_id,
                event_type=event_type,
                confidence=confidence,
                duration_s=int(attrs.get("duration_s", 0)),
                primary_image_key=primary_key,
                image_bucket=bucket,
                secondary_image_key=secondary_key,
                thumbnail=primary_key,
                ts=ts,
                legal_hold=False,
                retention_until=(datetime.now(timezone.utc) + timedelta(days=90)).isoformat(),
                source_event_id=event_id,
            )
            session.add(evidence_row)
            created += 1
        
        if created:
            await session.commit()
            logger.info("Evidence sync: created %d new evidence rows", created)
    
    # Update last sync timestamp to the newest event's ts
    newest_ts = max((e.get("ts", "") for e in events), default="")
    if newest_ts:
        _last_sync_ts = str(newest_ts)


async def _evidence_sync_loop():
    """Background loop that runs evidence sync every 30s."""
    logger.info("Evidence sync started (interval=%ds)", _SYNC_INTERVAL_S)
    while True:
        try:
            await _sync_once()
        except Exception as exc:
            logger.error("Evidence sync error: %s", exc)
        await asyncio.sleep(_SYNC_INTERVAL_S)


def start_evidence_sync():
    """Launch the evidence sync background task."""
    asyncio.create_task(_evidence_sync_loop())
