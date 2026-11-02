"""
routers/push.py
===============
Peer-to-peer notification push endpoint.

Receives incidents and AI model suggestions forwarded from the edge-operator
laptop and:
  1. Saves them to the local PostgreSQL DB (so dashboard polling picks them up).
  2. Broadcasts real-time WebSocket events to all connected command-side clients.

Security: requests must include the X-DVAS-Service-Key header matching the
shared DVAS_SERVICE_KEY env var (same value on both laptops).
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from database import async_session_factory
from core.config import DVAS_SERVICE_KEY
from services.websocket_hub import hub

logger = logging.getLogger("dvas.push")
router = APIRouter(prefix="/api/push", tags=["Peer Push"])


def _check_service_key(request: Request):
    key = request.headers.get("X-DVAS-Service-Key", "")
    if not key or key != DVAS_SERVICE_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing service key")


@router.post("/receive")
async def receive_notification(payload: dict, request: Request):
    """
    Receive an incident or AI model suggestion from the peer DVAS backend.
    Saves to local DB and broadcasts via WebSocket so the bell shows immediately.
    """
    _check_service_key(request)
    notification_type = payload.get("type")
    data = payload.get("data", {})
    if notification_type not in ("incident", "model_suggestion"):
        raise HTTPException(400, f"Unknown notification type: {notification_type}")
    if notification_type == "incident":
        await _receive_incident(data)
    else:
        await _receive_suggestion(data)
    return {"status": "ok", "type": notification_type, "id": data.get("id")}


async def _receive_incident(data: dict):
    from models import Incident
    from sqlalchemy import select

    incident_id = data.get("id")
    if not incident_id:
        logger.warning("Received incident push with no ID - skipping")
        return

    async with async_session_factory() as session:
        existing = await session.execute(select(Incident).where(Incident.id == incident_id))
        if existing.scalar_one_or_none() is not None:
            logger.debug(f"Incident {incident_id} already in DB - skipping duplicate")
            return

        title = data.get("title", "Operational Incident")
        severity = data.get("severity", "MEDIUM").upper()
        mission_id = data.get("mission_id")
        drone_id = data.get("drone_id")
        location = data.get("location", "Edge Station")
        timestamp = data.get("timestamp") or datetime.now(timezone.utc).isoformat()
        created_by = data.get("created_by", "Edge Operator")
        description = data.get("description", "")
        category = data.get("category", "hardware")
        display_title = f"{mission_id + ': ' if mission_id else ''}{title}"

        session.add(Incident(
            id=incident_id, title=title, description=description,
            severity=severity, category=category, created_by=created_by,
            location=location, timestamp=timestamp, mission_id=mission_id,
            drone_id=drone_id, status=data.get("status", "OPEN"), sent_to_ingest=True,
        ))
        # No Alert copy (B16): the feed reads incidents from /api/incidents
        await session.commit()

    logger.info(f"Stored forwarded incident {incident_id} from peer")
    incident_record = {**data, "display_title": display_title}
    await hub.broadcast({
        "type": "incident", "incident": incident_record,
        "title": display_title, "message": display_title,
        "severity": data.get("severity", "MEDIUM"), "location": location,
        "created_by": created_by, "timestamp": timestamp,
        "mission_id": mission_id, "drone_id": drone_id,
    })


async def _receive_suggestion(data: dict):
    from models import AIModelSuggestion
    from sqlalchemy import select

    sugg_id = data.get("id")
    if not sugg_id:
        logger.warning("Received suggestion push with no ID - skipping")
        return

    async with async_session_factory() as session:
        existing = await session.execute(select(AIModelSuggestion).where(AIModelSuggestion.id == sugg_id))
        if existing.scalar_one_or_none() is not None:
            logger.debug(f"Suggestion {sugg_id} already in DB - skipping duplicate")
            return

        models = data.get("models", [])
        model_names = data.get("model_names", [])
        priority = data.get("priority", "normal")
        reason = data.get("reason", "")
        location = data.get("location", "Edge Ground Station")
        suggested_by = data.get("suggested_by", "Edge Operator")
        timestamp = data.get("timestamp") or datetime.now(timezone.utc).isoformat()
        mission_id = data.get("mission_id")
        drone_id = data.get("drone_id")

        session.add(AIModelSuggestion(
            id=sugg_id, models=models, model_names=model_names,
            priority=priority, reason=reason, location=location,
            suggested_by=suggested_by, timestamp=timestamp,
            status="PENDING", drone_id=drone_id, mission_id=mission_id,
        ))

        models_str = ", ".join(model_names) if model_names else "AI Models"
        mission_tag = mission_id or (drone_id if drone_id else "Edge Mission")
        display_title = f"{mission_tag}: Suggests {models_str}"
        # No Alert copy (B16): the feed reads suggestions from /api/analytics/suggestions
        await session.commit()

    logger.info(f"Stored forwarded AI suggestion {sugg_id} from peer")

    await hub.broadcast({
        "type": "model_suggestion", "suggestion": {**data},
        "title": display_title, "message": display_title,
        "mission_id": mission_id, "drone_id": drone_id,
        "models": models, "model_names": model_names,
        "priority": priority, "location": location,
        "suggested_by": suggested_by, "timestamp": timestamp,
    })
