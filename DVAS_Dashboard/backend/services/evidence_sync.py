"""
Evidence sync — turns analytics confirmed events into Evidence rows (evidence plan E1–E3).

* Pages the gateway's /events/confirmed with a persistent (ts, event_id) cursor
  (table evidence_sync_state), oldest first, re-reading a short overlap window so
  events committed late are not lost; source_event_id makes it idempotent.
* Copies the analyzer's `evidence` block verbatim (exact objects, frame, GPS, model).
  Events without a block (legacy / single-frame HTTP analysis) get images only from
  an exact match (event_id or track_id); nothing is guessed (B2).
* Copies every object into the object-locked evidence vault, re-checking the
  analyzer's SHA-256, then stores the record hash and writes an audit entry.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

import asyncpg
import httpx
from sqlalchemy import select

from audit_service import write_audit_v2
from core.analytics_client import _service_headers
from core.config import ANALYTICS_BASE, ANALYTICS_DATABASE_URL, EVIDENCE_RETENTION_DAYS
from database import async_session_factory
from models import Alert, Evidence, EvidenceSyncState, Mission
from services import evidence_vault
from services.evidence_integrity import record_sha256
from core.timeutil import iso_utc

logger = logging.getLogger("dvas.evidence_sync")

_SYNC_INTERVAL_S = 30
_PAGE_SIZE = 200
_MAX_PAGES_PER_RUN = 20
_OVERLAP = timedelta(minutes=2)          # analyzers commit out of ts order; re-read, dedup on source_event_id
_INITIAL_LOOKBACK = timedelta(hours=24)
_VAULT_RETRY_BATCH = 20

SYSTEM_ACTOR = {"id": "system:evidence-sync", "email": "system@dvas", "role": "system",
                "name": "DVAS evidence sync"}

_warned_no_analytics_db = False

# Severity when the analyzer gives none (R1 refines this per rule)
_DEFAULT_SEVERITY = {
    "intrusion_breach": "critical", "theft_alert": "critical", "face_watchlist_match": "critical",
    "crowd_threshold": "high", "vessel_proximity": "high",
}


# ── Legacy events: exact image match only ─────────────────────────────────────
async def _legacy_objects(event_type: str, event_id: str, track_id: str) -> List[Dict]:
    """Objects for an event without an `evidence` block: event_id / track_id matches only."""
    global _warned_no_analytics_db
    if not ANALYTICS_DATABASE_URL:
        if not _warned_no_analytics_db:
            logger.error("ANALYTICS_DATABASE_URL is not set; legacy evidence images will not be resolved")
            _warned_no_analytics_db = True
        return []

    found: List[Tuple[str, str, str]] = []      # (role, bucket, key)

    async def one(conn, sql, *args):
        try:
            return await conn.fetchrow(sql, *args)
        except Exception:
            return None

    try:
        conn = await asyncpg.connect(ANALYTICS_DATABASE_URL)
    except Exception as exc:
        logger.debug("Legacy image lookup: analytics DB unreachable: %s", exc)
        return []
    try:
        track_uuid = track_id if track_id and len(track_id) == 36 else ""
        if event_type == "person_detected" and track_uuid:
            row = await one(conn, "SELECT object_key FROM person_embeddings WHERE track_id = $1::uuid "
                                  "AND COALESCE(object_key, '') <> '' LIMIT 1", track_uuid)
            if row:
                found.append(("primary", "dvas-faces", row["object_key"]))
            else:
                row = await one(conn, "SELECT object_key, person_object_key FROM face_embeddings "
                                      "WHERE track_id = $1::uuid LIMIT 1", track_uuid)
                if row and (row["person_object_key"] or row["object_key"]):
                    found.append(("primary", "dvas-faces", row["person_object_key"] or row["object_key"]))
                    if row["person_object_key"] and row["object_key"]:
                        found.append(("secondary", "dvas-faces", row["object_key"]))
        elif event_type in ("vehicle_detected", "theft_alert", "vehicle_anomaly", "wrong_way"):
            row = None
            if track_uuid:
                row = await one(conn, "SELECT vehicle_image_key, plate_image_key FROM plate_reads "
                                      "WHERE track_id = $1::uuid LIMIT 1", track_uuid)
            if not row and event_id:
                row = await one(conn, "SELECT vehicle_image_key, plate_image_key FROM plate_reads "
                                      "WHERE event_id = $1::uuid LIMIT 1", event_id)
            vehicle_key = row["vehicle_image_key"] if row else None
            plate_key = row["plate_image_key"] if row else None
            if not vehicle_key and track_uuid:
                trow = await one(conn, "SELECT metadata->>'vehicle_image_key' AS vk FROM tracks "
                                       "WHERE track_id = $1::uuid", track_uuid)
                vehicle_key = trow["vk"] if trow else None
            if vehicle_key:
                found.append(("primary", "dvas-vehicles", vehicle_key))
            if plate_key:
                found.append(("secondary" if vehicle_key else "primary", "dvas-plates", plate_key))
        elif event_type.startswith("intrusion") and event_id:
            row = await one(conn, "SELECT snapshot_key FROM intrusion_events WHERE event_id = $1::uuid LIMIT 1",
                            event_id)
            if row and row["snapshot_key"]:
                found.append(("primary", "dvas-intrusion", row["snapshot_key"]))
        elif event_type.startswith("vessel") and event_id:
            row = await one(conn, "SELECT snapshot_key FROM vessel_events WHERE event_id = $1::uuid LIMIT 1",
                            event_id)
            if row and row["snapshot_key"]:
                found.append(("primary", "dvas-vessels-maritime", row["snapshot_key"]))
        elif event_id:
            row = await one(conn, "SELECT snapshot_key FROM anomaly_events WHERE event_id = $1::uuid LIMIT 1",
                            event_id)
            if row and row["snapshot_key"]:
                found.append(("primary", "dvas-anomaly", row["snapshot_key"]))
    finally:
        await conn.close()
    return [{"role": r, "bucket": b, "key": k, "content_type": "image/jpeg", "kind": "legacy_lookup"}
            for r, b, k in found if k]


# ── Event → evidence ──────────────────────────────────────────────────────────
def _event_type_label(event_type: str) -> str:
    """Human-readable label for confirmed event types."""
    labels = {
        "crowd_threshold": "Crowd surge detected",
        "theft_alert": "Stolen/watchlisted vehicle",
        "intrusion_alert": "Perimeter intrusion",
        "intrusion_breach": "Perimeter intrusion",
        "face_watchlist_match": "Face watchlist match",
        "vessel_proximity": "Vessel convergence",
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


def _epoch_to_iso(value) -> Optional[str]:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _parse_attrs(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def build_evidence_fields(ev: dict) -> dict:
    """Evidence column values from one confirmed event (no I/O). `objects` are source objects."""
    attrs = _parse_attrs(ev.get("attributes"))
    block = attrs.get("evidence") if isinstance(attrs.get("evidence"), dict) else None
    drone_id = ev.get("drone_id", "") or ""
    mission_id = str(ev.get("mission_id") or attrs.get("mission_id") or "")
    if not mission_id and drone_id.startswith("DRN-MSN-"):
        mission_id = drone_id[4:]
    event_type = ev.get("event_type", "unknown") or "unknown"
    # Canonical UTC form = what the timestamptz column returns, so record_sha256 stays verifiable
    ts = iso_utc(ev.get("ts")) or ""
    geo = (block or {}).get("geo") or {}
    frame = (block or {}).get("frame") or {}
    return {
        "event_type": event_type,
        "drone_id": drone_id,
        "mission_id": mission_id,
        "confidence": float(ev.get("confidence") or 0),
        "ts": ts,
        "capture_ts": _epoch_to_iso(frame.get("capture_ts")) or ts,
        "lat": ev.get("latitude") if ev.get("latitude") is not None else geo.get("lat"),
        "lon": ev.get("longitude") if ev.get("longitude") is not None else geo.get("lon"),
        "severity": (str(attrs["severity"]).lower() if attrs.get("severity")
                     else _DEFAULT_SEVERITY.get(event_type)),
        "model_info": (block or {}).get("model") or None,
        "attributes": attrs,
        "objects": list((block or {}).get("objects") or []),
        "has_block": block is not None,
        "duration_s": int(attrs.get("duration_s") or attrs.get("dwell_duration_s") or 0),
    }


async def _jurisdiction_for(session, mission_id: str) -> str:
    if mission_id:
        res = await session.execute(select(Mission.jurisdiction).where(Mission.mission_id == mission_id))
        jur = res.scalar_one_or_none()
        if jur:
            return jur
    return "Thiruvananthapuram"


def _apply_vault_result(row: Evidence, vault_objects: List[Dict], status: str, integrity: str) -> None:
    row.objects = vault_objects
    row.vault_status = status
    row.integrity = integrity
    row.integrity_checked_at = datetime.now(timezone.utc).isoformat()
    primary = next((o for o in vault_objects if o.get("role") == "primary"), None)
    secondary = next((o for o in vault_objects if o.get("role") == "secondary"), None)
    # Old columns kept so the current UI keeps working (filled from objects)
    row.primary_image_key = primary["key"] if primary else ""
    row.thumbnail = row.primary_image_key
    row.image_bucket = primary["bucket"] if primary else ""
    row.secondary_image_key = secondary["key"] if secondary else ""


def _evidence_dict(row: Evidence) -> dict:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


async def create_evidence(session, ev: dict) -> Evidence:
    """Create (but not commit) the Evidence row for one event, with its vault copy."""
    event_id = str(ev.get("event_id", ""))
    f = build_evidence_fields(ev)
    objects = f["objects"]
    if not f["has_block"]:
        objects = await _legacy_objects(f["event_type"], event_id, str(ev.get("track_id") or ""))
    image_match = "exact" if objects else "none"

    evidence_id = f"EVI-{uuid.uuid4().hex[:12]}"
    retain_until = datetime.now(timezone.utc) + timedelta(days=EVIDENCE_RETENTION_DAYS)
    row = Evidence(
        id=evidence_id,
        label=f"{_event_type_label(f['event_type'])} — {f['drone_id']}",
        mission_id=f["mission_id"],
        jurisdiction=await _jurisdiction_for(session, f["mission_id"]),
        drone_id=f["drone_id"],
        event_type=f["event_type"],
        confidence=f["confidence"],
        duration_s=f["duration_s"],
        ts=f["ts"],
        capture_ts=f["capture_ts"],
        lat=f["lat"],
        lon=f["lon"],
        severity=f["severity"],
        model_info=f["model_info"],
        attributes=f["attributes"],
        image_match=image_match,
        legal_hold=False,
        retention_until=retain_until.isoformat(),
        source_event_id=event_id,
        status="active",
    )
    try:
        vault_objects, status, integrity = await asyncio.to_thread(
            evidence_vault.copy_into_vault, evidence_id, objects, retain_until, False)
    except Exception as exc:
        logger.error("Vault copy for %s failed: %s", evidence_id, exc)
        vault_objects = [dict(o, copy="pending", source={"bucket": o.get("bucket"), "key": o.get("key"),
                                                          "sha256": o.get("sha256")}) for o in objects]
        status, integrity = ("failed" if objects else "none"), "unverified"
    _apply_vault_result(row, vault_objects, status, integrity)
    row.record_sha256 = record_sha256(_evidence_dict(row))
    session.add(row)
    session.add(alert_for(row))
    return row


def alert_for(row: Evidence) -> Alert:
    """R1: every confirmed AI event is also an alert (B10), linked to its evidence."""
    attrs = row.attributes or {}
    return Alert(
        id=f"AI-{uuid.uuid4().hex[:12]}",
        category="ai",
        source_event_id=row.source_event_id,
        evidence_id=row.id,
        mission_id=row.mission_id,
        jurisdiction=row.jurisdiction,
        drone_id=row.drone_id,
        type=row.event_type,
        rule=row.event_type,
        title=_event_type_label(row.event_type),
        severity=row.severity or "medium",
        confidence=row.confidence,
        zone=str(attrs.get("zone_id") or attrs.get("zone") or "") or None,
        lat=row.lat,
        lon=row.lon,
        ts=row.ts,
    )


async def _broadcast_created(rows: List[Evidence]) -> None:
    """Live feed + evidence screen refresh (U1) without waiting for the poll."""
    try:
        from services.websocket_hub import hub
    except Exception:
        return
    for row in rows:
        try:
            await hub.broadcast({"type": "alert", "alert": {
                "id": f"evidence:{row.id}", "category": "ai", "type": row.event_type,
                "title": _event_type_label(row.event_type), "severity": row.severity or "medium",
                "drone_id": row.drone_id, "mission_id": row.mission_id, "evidence_id": row.id,
                "confidence": row.confidence, "ts": row.ts}})
            await hub.broadcast({"type": "evidence_created", "evidence_id": row.id,
                                 "event_type": row.event_type, "mission_id": row.mission_id})
        except Exception as exc:
            logger.debug("Broadcast failed: %s", exc)


async def _audit_evidence(row: Evidence, action: str, details: str) -> None:
    await write_audit_v2(
        actor=SYSTEM_ACTOR,
        action=action,
        category="evidence",
        target=row.id, target_type="evidence", target_id=row.id, target_name=row.label or "",
        details=details,
        after_values={
            "record_sha256": row.record_sha256,
            "source_event_id": row.source_event_id,
            "objects": [{"role": o.get("role"), "key": o.get("key"), "sha256": o.get("sha256")}
                        for o in (row.objects or [])],
            "vault_status": row.vault_status,
            "integrity": row.integrity,
        },
        source_module="evidence_sync",
    )


# ── Cursor + paging ───────────────────────────────────────────────────────────
async def _load_cursor(session) -> Tuple[str, str]:
    state = await session.get(EvidenceSyncState, 1)
    if state and state.last_ts:
        return state.last_ts, state.last_event_id or ""
    return (datetime.now(timezone.utc) - _INITIAL_LOOKBACK).isoformat(), ""


async def _save_cursor(session, ts: str, event_id: str) -> None:
    state = await session.get(EvidenceSyncState, 1)
    now = datetime.now(timezone.utc).isoformat()
    if state is None:
        session.add(EvidenceSyncState(id=1, last_ts=ts, last_event_id=event_id, updated_at=now))
    else:
        state.last_ts, state.last_event_id, state.updated_at = ts, event_id, now


def _ts_key(ts: str) -> datetime:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


async def _fetch_page(client: httpx.AsyncClient, after_ts: str, after_id: str) -> Optional[List[dict]]:
    resp = await client.get(
        f"{ANALYTICS_BASE}/events/confirmed",
        params={"order": "asc", "after_ts": after_ts, "after_id": after_id, "limit": _PAGE_SIZE},
        headers=_service_headers(),
    )
    if resp.status_code != 200:
        logger.warning("Evidence sync: gateway returned %d", resp.status_code)
        return None
    return resp.json().get("events", [])


async def _sync_once() -> int:
    """One sync pass. Returns the number of evidence rows created."""
    async with async_session_factory() as session:
        cursor_ts, cursor_id = await _load_cursor(session)

    after_ts = (_ts_key(cursor_ts) - _OVERLAP).isoformat()
    after_id = ""
    newest = (cursor_ts, cursor_id)
    created_rows: List[Evidence] = []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            for _ in range(_MAX_PAGES_PER_RUN):
                events = await _fetch_page(client, after_ts, after_id)
                if not events:
                    break
                async with async_session_factory() as session:
                    for ev in events:
                        event_id = str(ev.get("event_id", ""))
                        if not event_id:
                            continue
                        exists = await session.execute(
                            select(Evidence.id).where(Evidence.source_event_id == event_id))
                        if exists.scalar_one_or_none() is None:
                            created_rows.append(await create_evidence(session, ev))
                        key = (str(ev.get("ts", "")), event_id)
                        if (_ts_key(key[0]), key[1]) > (_ts_key(newest[0]), newest[1]):
                            newest = key
                    await _save_cursor(session, newest[0], newest[1])
                    await session.commit()
                last = events[-1]
                after_ts, after_id = str(last.get("ts", "")), str(last.get("event_id", ""))
                if len(events) < _PAGE_SIZE:
                    break
    except httpx.HTTPError as exc:
        logger.debug("Evidence sync: gateway unreachable: %s", exc)

    for row in created_rows:
        try:
            await _audit_evidence(row, "evidence_create",
                                  f"From confirmed event {row.source_event_id} ({row.event_type}); "
                                  f"image_match={row.image_match}")
        except Exception as exc:
            logger.error("Audit entry for %s failed: %s", row.id, exc)
    if created_rows:
        logger.info("Evidence sync: created %d evidence rows (+ alerts)", len(created_rows))
        await _broadcast_created(created_rows)
    return len(created_rows)


async def _retry_vault_copies() -> None:
    """Retry vault copies that failed (MinIO briefly down). Source objects expire in 3–14 days."""
    retried = []
    async with async_session_factory() as session:
        res = await session.execute(
            select(Evidence).where(Evidence.vault_status == "failed", Evidence.status == "active")
            .order_by(Evidence.ts.desc()).limit(_VAULT_RETRY_BATCH))
        for row in res.scalars().all():
            sources = [dict(o.get("source") or {}, role=o.get("role"), kind=o.get("kind"),
                            content_type=o.get("content_type")) for o in (row.objects or [])]
            sources = [s for s in sources if s.get("bucket") and s.get("key")]
            try:
                vault_objects, status, integrity = await asyncio.to_thread(
                    evidence_vault.copy_into_vault, row.id, sources,
                    _ts_key(row.retention_until or ""), bool(row.legal_hold))
            except Exception as exc:
                logger.debug("Vault retry for %s failed: %s", row.id, exc)
                continue
            if status == "failed":
                continue
            _apply_vault_result(row, vault_objects, status, integrity)
            row.record_sha256 = record_sha256(_evidence_dict(row))
            retried.append(row)
        if retried:
            await session.commit()
    for row in retried:
        await _audit_evidence(row, "evidence_vault_copy", f"Vault copy completed on retry: {row.vault_status}")


async def _evidence_sync_loop():
    """Background loop that runs evidence sync every 30s."""
    logger.info("Evidence sync started (interval=%ds)", _SYNC_INTERVAL_S)
    try:
        await asyncio.to_thread(evidence_vault.ensure_vault)
    except Exception as exc:
        logger.error("Evidence vault not ready (will retry per copy): %s", exc)
    while True:
        try:
            await _sync_once()
            await _retry_vault_copies()
        except Exception as exc:
            logger.error("Evidence sync error: %s", exc)
        await asyncio.sleep(_SYNC_INTERVAL_S)


def start_evidence_sync():
    """Launch the evidence sync background task."""
    asyncio.create_task(_evidence_sync_loop())
