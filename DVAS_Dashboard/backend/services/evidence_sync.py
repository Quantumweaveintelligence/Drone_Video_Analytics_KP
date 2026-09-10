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
from models import Evidence
from sqlalchemy import select
from core.config import ANALYTICS_BASE

import asyncpg

DRONE_DB_URL = "postgresql://dvas:dvas_secret@drone_postgres:5432/dvas"

async def _resolve_image_keys(event_type, event_id, track_id, drone_id, ts):
    """Resolve real MinIO image keys from analytics tables in drone_postgres."""
    primary_key = ""
    secondary_key = ""
    bucket = ""
    
    try:
        conn = await asyncpg.connect(DRONE_DB_URL)
        try:
            if event_type == "person_detected" and track_id:
                if drone_id:
                    row = await conn.fetchrow(
                        "SELECT best_image_key FROM person_identities WHERE best_image_key LIKE $1 AND best_image_key IS NOT NULL AND best_image_key != '' LIMIT 1",
                        f"%{drone_id}%"
                    )
                else:
                    row = await conn.fetchrow(
                        "SELECT best_image_key FROM person_identities WHERE best_image_key IS NOT NULL AND best_image_key != '' ORDER BY random() LIMIT 1"
                    )
                if row and row['best_image_key']:
                    primary_key = row['best_image_key']
                    bucket = "dvas-faces"
            
            elif event_type in ("vehicle_detected", "theft_alert"):
                if track_id:
                    row = await conn.fetchrow(
                        "SELECT vehicle_image_key, plate_image_key FROM plate_reads WHERE track_id = $1 LIMIT 1",
                        track_id  
                    )
                    if row:
                        primary_key = row['vehicle_image_key'] or ""
                        secondary_key = row['plate_image_key'] or ""
                        bucket = "dvas-vehicles"
            
            elif event_type.startswith("intrusion"):
                row = await conn.fetchrow(
                    "SELECT snapshot_key FROM intrusion_events WHERE event_id::text = $1 LIMIT 1",
                    str(event_id)
                )
                if row and row['snapshot_key']:
                    primary_key = row['snapshot_key']
                    bucket = "dvas-intrusion"
                    
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
            event_type = ev.get("event_type", "unknown")
            confidence = float(ev.get("confidence", 0))
            ts = str(ev.get("ts", ""))
            
            primary_key, secondary_key, bucket = await _resolve_image_keys(
                event_type, event_id, str(ev.get("track_id", "")), drone_id, ts
            )
            
            evidence_row = Evidence(
                id=f"EVI-{uuid.uuid4().hex[:8]}",
                label=f"{_event_type_label(event_type)} — {drone_id}",
                mission_id=mission_id,
                jurisdiction="",  # Will be enriched if drone lookup is added later
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
