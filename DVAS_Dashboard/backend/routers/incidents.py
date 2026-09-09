"""
routers/incidents.py
====================
Handles reporting, listing, acknowledging, and re-transmitting edge incidents.
Persists incidents to PostgreSQL, relays them to the Ingest Server, and broadcasts
live WebSocket notifications to all active dashboards.
"""
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Request, Body
from sqlalchemy import select, update, desc
import httpx

from database import async_session_factory
from models import Incident
from core.security import get_current_user, _row_to_dict
from audit_service import write_audit_v2
from services.websocket_hub import hub
from ingest_proxy import _get_service_token, INGEST_SERVER_URL

logger = logging.getLogger("dvas.incidents")
router = APIRouter(prefix="/api/incidents", tags=["Incidents"])


async def _forward_incident_to_ingest(incident_dict: dict) -> bool:
    """Attempt to forward the reported incident to the remote Ingest Server."""
    try:
        token = await _get_service_token()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        url = f"{INGEST_SERVER_URL}/ingest/incident"
        async with httpx.AsyncClient(timeout=3.5) as client:
            resp = await client.post(url, json=incident_dict, headers=headers)
            if resp.is_success:
                logger.info(f"Incident {incident_dict.get('id')} forwarded to Ingest Server: {resp.status_code}")
                return True
            logger.warning(f"Ingest Server rejected incident {incident_dict.get('id')}: {resp.status_code}")
            return False
    except Exception as exc:
        logger.warning(f"Could not forward incident to Ingest Server ({INGEST_SERVER_URL}): {exc}")
        return False


@router.get("")
async def list_incidents(
    limit: int = 100,
    user: dict = Depends(get_current_user)
):
    """List operational incidents ordered by latest timestamp."""
    async with async_session_factory() as session:
        stmt = select(Incident).order_by(desc(Incident.timestamp)).limit(limit)
        result = await session.execute(stmt)
        return [_row_to_dict(r) for r in result.scalars().all()]


@router.post("")
async def create_incident(
    payload: dict = Body(...),
    user: dict = Depends(get_current_user),
    request: Request = None,
):
    """
    Create a new incident report from the edge station.
    Persists to PostgreSQL, relays to Ingest Server, and broadcasts to live dashboards.
    """
    title = payload.get("title", "").strip()
    if not title:
        raise HTTPException(400, "Incident title is required")

    severity = payload.get("severity", "LOW").upper()
    if severity not in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
        severity = "MEDIUM"

    category = payload.get("category", "hardware").lower()
    description = payload.get("description", "")
    location = payload.get("location") or payload.get("place") or "Edge Station"
    timestamp = payload.get("timestamp") or datetime.now(timezone.utc).isoformat()
    mission_id = payload.get("mission_id")
    drone_id = payload.get("drone_id")

    # Created By: operator name / email from auth
    created_by = (
        user.get("name")
        or (f"{user.get('first_name', '')} {user.get('last_name', '')}".strip())
        or user.get("email")
        or "Edge Operator"
    )
    if user.get("email") and f"({user.get('email')})" not in created_by:
        created_by = f"{created_by} ({user.get('email')})"

    incident_id = f"INC-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

    incident_record = {
        "id": incident_id,
        "title": title,
        "description": description,
        "severity": severity,
        "category": category,
        "created_by": created_by,
        "location": location,
        "timestamp": timestamp,
        "mission_id": mission_id,
        "drone_id": drone_id,
        "status": "OPEN",
    }

    # Relay to Ingest Server
    sent_to_ingest = await _forward_incident_to_ingest(incident_record)
    incident_record["sent_to_ingest"] = sent_to_ingest

    # Persist to database
    async with async_session_factory() as session:
        new_inc = Incident(
            id=incident_id,
            title=title,
            description=description,
            severity=severity,
            category=category,
            created_by=created_by,
            location=location,
            timestamp=timestamp,
            mission_id=mission_id,
            drone_id=drone_id,
            status="OPEN",
            sent_to_ingest=sent_to_ingest,
        )
        session.add(new_inc)
        await session.commit()

    # Log to audit trail
    try:
        await write_audit_v2(
            actor=user,
            action="REPORT_INCIDENT",
            action_type="create",
            category="operations",
            severity="warn" if severity in ("HIGH", "CRITICAL") else "info",
            target=incident_id,
            target_type="incident",
            target_id=incident_id,
            target_name=title,
            details=f"Incident '{title}' ({severity}) logged at {location}; Transmitted to ingest: {sent_to_ingest}",
            source_module="edge",
            request=request,
        )
    except Exception as audit_err:
        logger.warning(f"Could not write audit for REPORT_INCIDENT: {audit_err}")

    # Broadcast real-time incident event
    ws_incident = {
        "type": "incident",
        "incident": incident_record,
        "title": title,
        "severity": severity,
        "location": location,
        "created_by": created_by,
        "timestamp": timestamp,
    }
    await hub.broadcast(ws_incident)

    # Also broadcast standard alert event so NotificationBell captures it
    alert_severity = (
        "critical" if severity == "CRITICAL"
        else ("warning" if severity == "HIGH" else "info")
    )
    ws_alert = {
        "type": "alert",
        "alert": {
            "id": incident_id,
            "title": f"Incident: {title}",
            "type": title,
            "severity": alert_severity,
            "drone_id": drone_id or "EDGE-STATION",
            "zone": location,
            "ts": timestamp,
        }
    }
    await hub.broadcast(ws_alert)

    return {
        "status": "ok",
        "incident": incident_record,
        "sent_to_ingest": sent_to_ingest,
    }


@router.post("/{incident_id}/ack")
async def acknowledge_incident(
    incident_id: str,
    user: dict = Depends(get_current_user),
    request: Request = None,
):
    """Mark an incident as acknowledged."""
    async with async_session_factory() as session:
        result = await session.execute(
            update(Incident)
            .where(Incident.id == incident_id)
            .values(status="ACKNOWLEDGED")
        )
        if result.rowcount == 0:
            raise HTTPException(404, "Incident not found")
        await session.commit()

    try:
        await write_audit_v2(
            actor=user,
            action="ACKNOWLEDGE_INCIDENT",
            action_type="update",
            category="operations",
            severity="info",
            target=incident_id,
            target_type="incident",
            target_id=incident_id,
            details=f"Incident {incident_id} marked as ACKNOWLEDGED",
            source_module="edge",
            request=request,
        )
    except Exception as audit_err:
        logger.warning(f"Could not write audit for ACKNOWLEDGE_INCIDENT: {audit_err}")

    await hub.broadcast({
        "type": "incident_ack",
        "incident_id": incident_id,
        "status": "ACKNOWLEDGED",
    })
    return {"status": "ok", "incident_id": incident_id}


@router.post("/{incident_id}/resend")
async def resend_incident(
    incident_id: str,
    user: dict = Depends(get_current_user),
):
    """Retry sending an unsent incident to the Ingest Server."""
    async with async_session_factory() as session:
        inc = await session.get(Incident, incident_id)
        if not inc:
            raise HTTPException(404, "Incident not found")

        inc_dict = _row_to_dict(inc)
        sent = await _forward_incident_to_ingest(inc_dict)
        inc.sent_to_ingest = sent
        await session.commit()
        return {"status": "ok", "incident_id": incident_id, "sent_to_ingest": sent}
