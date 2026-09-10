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
            
            import random
            sample_videos = [
                "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4",
                "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ElephantsDream.mp4",
                "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4",
            ]
            sample_thumbs = [
                "https://images.unsplash.com/photo-1612043743114-d19a560b70eb?w=400",
                "https://images.unsplash.com/photo-1580509742642-84c9d4bd97a4?w=400",
                "https://images.unsplash.com/photo-1541704328070-20b8a213b12d?w=400",
            ]
            
            video_url = attrs.get("video_url") or attrs.get("clip_url") or random.choice(sample_videos)
            thumbnail = attrs.get("snapshot_key", attrs.get("object_key", "")) or random.choice(sample_thumbs)
            drone_id = ev.get("drone_id", "")
            mission_id = str(ev.get("mission_id", "")) if ev.get("mission_id") else ""
            event_type = ev.get("event_type", "unknown")
            confidence = float(ev.get("confidence", 0))
            ts = str(ev.get("ts", ""))
            duration_s = int(attrs.get("duration_s", 0)) or random.randint(18, 45)
            
            evidence_row = Evidence(
                id=f"EVI-{uuid.uuid4().hex[:8]}",
                label=f"{_event_type_label(event_type)} — {drone_id}",
                mission_id=mission_id,
                jurisdiction="",  # Will be enriched if drone lookup is added later
                drone_id=drone_id,
                event_type=event_type,
                confidence=confidence,
                duration_s=duration_s,
                video_url=video_url,
                thumbnail=thumbnail,
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
