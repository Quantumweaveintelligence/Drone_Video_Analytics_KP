import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select, func, or_

from database import async_session_factory
from models import Mission, Drone, Alert, Evidence, Export
from core.security import get_current_user, scope_filter_for, _apply_scope_to_query, _row_to_dict
from core.analytics_client import _analytics_get
from core.config import ANPR_URL

"""
Drones & Missions Router
Handles listing drones, creating/ending missions, and fetching aggregate KPIs.
"""

logger = logging.getLogger("dvas")
router = APIRouter(prefix="/api", tags=["Drones & Missions"])

@router.get("/drones")
async def list_drones(user: dict = Depends(get_current_user)):
    async with async_session_factory() as session:
        stmt = select(Drone)
        if user["role"] == "drone_operator":
            stmt = stmt.where(Drone.operator_id == user["id"])
        elif user["role"] == "system_administrator":
            pass  # infra view — connection status only
        else:
            f = scope_filter_for(user)
            if "__deny__" in f:
                raise HTTPException(403, "Not permitted")
            stmt = _apply_scope_to_query(stmt, Drone, f)
            if stmt is None:
                raise HTTPException(403, "Not permitted")
        stmt = stmt.limit(200)
        result = await session.execute(stmt)
        rows = result.scalars().all()
        docs = [_row_to_dict(r) for r in rows]
        if user["role"] == "system_administrator":
            for d in docs:
                d.pop("last_analytics", None)
        return docs


@router.get("/missions")
async def list_missions(user: dict = Depends(get_current_user)):
    # Synchronize missing missions from ANPR microservice
    try:
        anpr_res = await _analytics_get(f"{ANPR_URL}/missions", timeout=5.0)
        if anpr_res and "missions" in anpr_res:
            active_mission_ids = set(anpr_res["missions"])
            async with async_session_factory() as session:
                existing = await session.execute(select(Mission.mission_id).where(Mission.mission_id.in_(active_mission_ids)))
                existing_ids = {row[0] for row in existing.all()}
                
                missing_ids = active_mission_ids - existing_ids
                if missing_ids:
                    logger.info(f"Synchronizing {len(missing_ids)} missing missions from ANPR")
                    for mid in missing_ids:
                        # Extract timestamp from mission_id like MSN-20260813-2204-52-ALPHA
                        # Parts: MSN, YYYYMMDD, HHMM, SS, SUFFIX
                        started_at = ""
                        parts = mid.split("-")
                        if len(parts) >= 4 and parts[1].isdigit() and parts[2].isdigit():
                            try:
                                d = parts[1]
                                t = parts[2]
                                s = parts[3] if parts[3].isdigit() else "00"
                                started_at = f"{d[:4]}-{d[4:6]}-{d[6:8]}T{t[:2]}:{t[2:4]}:{s[:2]}+00:00"
                            except Exception:
                                pass
                                
                        session.add(Mission(
                            mission_id=mid,
                            name=mid,  # Fallback name
                            status="recovered",
                            drone_id="",
                            operator_name="System Recovered",
                            operator_ids=[], # Recovered missions will be visible to officers
                            started_at=started_at
                        ))
                    await session.commit()
    except Exception as e:
        logger.warning(f"Could not synchronize missions from ANPR: {e}")

    async with async_session_factory() as session:
        if user["role"] == "drone_operator":
            # operator_ids is JSONB array — use contains operator
            stmt = select(Mission).where(
                Mission.operator_ids.contains([user["id"]])
            ).order_by(Mission.started_at.desc()).limit(100)
            result = await session.execute(stmt)
            return [_row_to_dict(r) for r in result.scalars().all()]

        # All other roles (senior_officer, control_room_officer, system_administrator)
        # can see all missions — filtered by jurisdiction if set
        stmt = select(Mission)
        if user["role"] == "control_room_officer":
            jur = user.get("jurisdiction")
            if jur:
                # Show missions in their jurisdiction OR missions with no jurisdiction set
                stmt = stmt.where(
                    or_(Mission.jurisdiction == jur, Mission.jurisdiction == None, Mission.jurisdiction == "")
                )
        stmt = stmt.order_by(Mission.started_at.desc()).limit(100)
        result = await session.execute(stmt)
        return [_row_to_dict(r) for r in result.scalars().all()]


# ---------- Mission create / end (wired to Edge Mission Control) ----------
@router.post("/missions")
async def create_mission(
    payload: dict = Body(...),
    user: dict = Depends(get_current_user)
):
    """
    Save a new mission record to the database.
    Called by the frontend after the edge node confirms mission start.
    """
    mission_id = payload.get("mission_id")
    name = payload.get("name") or payload.get("mission_name", "Unnamed Mission")
    drone_id = payload.get("drone_id", "")
    operator_name = payload.get("operator_name") or payload.get("operator", "")
    location = payload.get("location", "")
    started_at = payload.get("started_at", "")

    if not mission_id:
        raise HTTPException(400, "mission_id is required")

    async with async_session_factory() as session:
        # Check for duplicate
        existing = await session.get(Mission, mission_id)
        if existing:
            return {"status": "already_exists", "mission_id": mission_id}

        mission = Mission(
            mission_id=mission_id,
            name=name,
            status="active",
            drone_id=drone_id,
            operator_name=operator_name,
            zone=location,
            started_at=started_at,
            operator_ids=[user["id"]],  # IMPORTANT: save current user ID so they can see this mission
            jurisdiction=user.get("jurisdiction", "")
        )
        session.add(mission)
        await session.commit()

    return {"status": "saved", "mission_id": mission_id}


@router.patch("/missions/{mission_id}/end")
async def end_mission_record(mission_id: str, user: dict = Depends(get_current_user)):
    """Mark a mission as completed and record the end time."""
    async with async_session_factory() as session:
        mission = await session.get(Mission, mission_id)
        if not mission:
            raise HTTPException(404, f"Mission {mission_id} not found")
        # Check jurisdiction / mission scope if scoped
        if user.get("mission_scope") and mission_id not in user["mission_scope"]:
            raise HTTPException(403, f"Not permitted to end mission {mission_id}")
        mission.status = "completed"
        mission.ended_at = datetime.now(timezone.utc).isoformat()
        await session.commit()
    return {"status": "ended", "mission_id": mission_id}


# ---------- Command dashboard: aggregate KPIs ----------
@router.get("/command/kpis")
async def command_kpis(user: dict = Depends(get_current_user)):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    f = scope_filter_for(user)
    if "__deny__" in f:
        raise HTTPException(403, "Not permitted")

    async with async_session_factory() as session:
        # Active missions count
        missions_stmt = select(func.count()).select_from(Mission).where(Mission.status == "active")
        if user["role"] != "senior_officer":
            missions_stmt = missions_stmt.where(Mission.jurisdiction == user.get("jurisdiction"))
        active_missions = (await session.execute(missions_stmt)).scalar() or 0

        # Alerts count
        alerts_stmt = select(func.count()).select_from(Alert)
        alerts_stmt = _apply_scope_to_query(alerts_stmt, Alert, f) if "__deny__" not in f else alerts_stmt
        alerts_24h = (await session.execute(alerts_stmt)).scalar() or 0

        # Evidence total
        evidence_stmt = select(func.count()).select_from(Evidence)
        evidence_stmt = _apply_scope_to_query(evidence_stmt, Evidence, f) if "__deny__" not in f else evidence_stmt
        evidence_total = (await session.execute(evidence_stmt)).scalar() or 0

        # Exports pending
        exports_stmt = select(func.count()).select_from(Export).where(Export.status == "pending")
        if user["role"] != "senior_officer":
            exports_stmt = exports_stmt.where(Export.jurisdiction == user.get("jurisdiction"))
        exports_pending = (await session.execute(exports_stmt)).scalar() or 0

        # Drones online
        drones_online = (await session.execute(
            select(func.count()).select_from(Drone).where(Drone.status == "live")
        )).scalar() or 0

    return {
        "active_missions": active_missions,
        "alerts_24h": alerts_24h,
        "evidence_total": evidence_total,
        "exports_pending": exports_pending,
        "drones_online": drones_online,
    }
