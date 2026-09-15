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

DRONE_DB_URL = "postgresql://dvas:dvas_secret@drone_postgres:5432/dvas"

async def _resolve_image_keys(event_type, event_id, track_id, drone_id, ts, mission_id=""):
    """Resolve real MinIO image keys from analytics tables in drone_postgres."""
    primary_key = ""
    secondary_key = ""
    bucket = ""
    
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
        conn = await asyncpg.connect(DRONE_DB_URL)
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
                               ORDER BY abs(extract(epoch from (last_seen - $2))) ASC LIMIT 1""",
                            drone_id, ts_dt
                        )
                    else:
                        prow = None
                        
                    if prow:
                        primary_key = prow['best_person_image_key'] or prow['best_image_key'] or ""
                        secondary_key = prow['best_image_key'] if prow['best_person_image_key'] and prow['best_image_key'] else ""
                        bucket = "dvas-faces"

                # Tier 3: General person identity fallback
                if not primary_key:
                    prow = await conn.fetchrow(
                        """SELECT best_person_image_key, best_image_key 
                           FROM person_identities 
                           WHERE best_person_image_key IS NOT NULL AND best_person_image_key != '' 
                           ORDER BY last_seen DESC LIMIT 1"""
                    )
                    if prow:
                        primary_key = prow['best_person_image_key'] or ""
                        secondary_key = prow['best_image_key'] or ""
                        bucket = "dvas-faces"

            elif event_type in ("vehicle_detected", "theft_alert", "vehicle_anomaly", "wrong_way"):
                vrow = None
                # Tier 1: Check plate_reads by track_id
                if track_id:
                    try:
                        vrow = await conn.fetchrow(
                            "SELECT vehicle_image_key, plate_image_key FROM plate_reads WHERE track_id = $1::uuid LIMIT 1",
                            track_id
                        )
                    except Exception:
                        pass

                # Tier 2: Check plate_reads by event_id
                if not vrow and event_id:
                    try:
                        vrow = await conn.fetchrow(
                            "SELECT vehicle_image_key, plate_image_key FROM plate_reads WHERE event_id = $1::uuid LIMIT 1",
                            event_id
                        )
                    except Exception:
                        pass

                # Tier 3: Check plate_reads by mission_id & time proximity
                if not vrow and mission_id:
                    vrow = await conn.fetchrow(
                        """SELECT vehicle_image_key, plate_image_key 
                           FROM plate_reads 
                           WHERE mission_id_str = $1 
                           ORDER BY abs(extract(epoch from (created_at - $2))) ASC LIMIT 1""",
                        mission_id, ts_dt
                    )

                # Tier 4: Check plate_reads by time proximity to ts_dt
                if not vrow:
                    vrow = await conn.fetchrow(
                        """SELECT vehicle_image_key, plate_image_key 
                           FROM plate_reads 
                           ORDER BY abs(extract(epoch from (created_at - $1))) ASC LIMIT 1""",
                        ts_dt
                    )

                # Tier 5: General plate_reads fallback
                if not vrow:
                    vrow = await conn.fetchrow(
                        "SELECT vehicle_image_key, plate_image_key FROM plate_reads ORDER BY created_at DESC LIMIT 1"
                    )

                if vrow:
                    primary_key = vrow['vehicle_image_key'] or ""
                    secondary_key = vrow['plate_image_key'] or ""
                    bucket = "dvas-vehicles"

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
                           ORDER BY abs(extract(epoch from (ts - $2))) ASC LIMIT 1""",
                        drone_id, ts_dt
                    )
                if not irow:
                    irow = await conn.fetchrow(
                        "SELECT snapshot_key FROM intrusion_events WHERE snapshot_key IS NOT NULL AND snapshot_key != '' ORDER BY ts DESC LIMIT 1"
                    )
                if irow and irow['snapshot_key']:
                    primary_key = irow['snapshot_key']
                    bucket = "dvas-intrusion"

            elif event_type in ("crowd_threshold", "crowd_surge"):
                crow = None
                if drone_id:
                    crow = await conn.fetchrow(
                        """SELECT heatmap_object_key FROM crowd_density_readings 
                           WHERE drone_id = $1 AND heatmap_object_key IS NOT NULL AND heatmap_object_key != ''
                           ORDER BY abs(extract(epoch from (ts - $2))) ASC LIMIT 1""",
                        drone_id, ts_dt
                    )
                if not crow:
                    crow = await conn.fetchrow(
                        """SELECT heatmap_object_key FROM crowd_density_readings 
                           WHERE heatmap_object_key IS NOT NULL AND heatmap_object_key != ''
                           ORDER BY abs(extract(epoch from (ts - $1))) ASC LIMIT 1""",
                        ts_dt
                    )
                if not crow:
                    crow = await conn.fetchrow(
                        "SELECT heatmap_object_key FROM crowd_density_readings WHERE heatmap_object_key IS NOT NULL AND heatmap_object_key != '' ORDER BY ts DESC LIMIT 1"
                    )
                if crow and crow['heatmap_object_key']:
                    primary_key = crow['heatmap_object_key']
                    bucket = "dvas-heatmaps"

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
