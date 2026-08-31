import random
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, or_
from jose import jwt

from database import async_session_factory
from models import Drone, Mission, AuditLog
from core.security import get_current_user, get_jwks, require_roles, _row_to_dict
from audit_service import write_audit_v2
from services.pdf_service import _make_shift_pdf

logger = logging.getLogger("dvas")
router = APIRouter(prefix="/api", tags=["Edge Operator"])

@router.get("/edge/shift-summary")
async def edge_shift_summary(request: Request, token: str = ""):
    # Support Authorization header, cookie, OR query token for direct <a> downloads
    if token:
        try:
            options = {"verify_aud": False}
            payload = jwt.decode(token, get_jwks(), algorithms=["RS256"], options=options)
            user = {
                "id": payload.get("sub"),
                "email": payload.get("email", ""),
                "role": "control_room_officer", # Assuming higher role for exports
            }
        except jwt.JWTError:
            raise HTTPException(401, "Invalid token")
    else:
        user = await get_current_user(request)
    if user.get("role") != "drone_operator":
        raise HTTPException(403, "Only drone operators may generate shift summaries")
    # Look up missions in operator's scope
    missions = []
    if user.get("mission_scope"):
        async with async_session_factory() as session:
            result = await session.execute(
                select(Mission).where(Mission.mission_id.in_(user["mission_scope"]))
            )
            missions = [_row_to_dict(r) for r in result.scalars().all()]
    # Look up own audit entries in last 12h to summarise anomalies reported
    since = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
    async with async_session_factory() as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.actor_id == user["id"],
                AuditLog.ts >= since,
                or_(
                    AuditLog.action.ilike("%anomaly%"),
                    AuditLog.action.ilike("%malfunction%"),
                ),
            )
        )
        own = [_row_to_dict(r) for r in result.scalars().all()]
    anomalies = [
        {
            "ts": e.get("ts", ""),
            "type": e.get("action", "-"),
            "note": e.get("details", "-"),
        }
        for e in own
    ]
    buffer_stats = {
        "avg_pct": random.randint(60, 92),
        "overwrite_cycles": random.randint(40, 220),
        "drops": random.randint(0, 3),
    }
    pdf_bytes = _make_shift_pdf(user, missions, buffer_stats, anomalies)
    await write_audit_v2(
        actor=user,
        action="shift_summary_download",
        target="-",
        target_type="system",
        details=f"missions={len(missions)}",
        source_endpoint="GET /api/edge/shift-summary",
        request=request,
    )
    fname = f"shift_summary_{user['id'][:8]}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/edge/status")
async def edge_status(user: dict = Depends(require_roles("drone_operator"))):
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        result = await session.execute(
            select(Drone).where(Drone.operator_id == user["id"])
        )
        drone_obj = result.scalar_one_or_none()
        drone = _row_to_dict(drone_obj) if drone_obj else None
    return {
        "drone": drone,
        "buffer_pct": random.randint(55, 92),
        "buffer_seconds_oldest": 60,
        "buffer_seconds_newest": random.randint(0, 3),
        "flagged_last_minute": random.randint(2, 18),
        "selected_last_minute": random.randint(0, 5),
        "clip_status": random.choice(["idle", "uploading", "upload_complete"]),
        "vpn": "connected",
        "ingest_reachable": True,
        "gps_lock": True,
        "battery_pct": random.randint(48, 96),
        "signal_pct": random.randint(65, 98),
        "fps": 28,
        "resolution": "1920x1080",
        "server_time": now.isoformat(),
    }
