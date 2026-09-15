import logging
import json
import hashlib
from datetime import datetime, timezone
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select, func, or_, and_, distinct

from database import async_session_factory
from models import Mission, Drone, Alert, Evidence, Export
from core.security import get_current_user, scope_filter_for, _apply_scope_to_query, _row_to_dict
from core.analytics_client import _analytics_get
from core.config import ANPR_URL
from audit_service import write_audit_v2

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
            # Exclude dead ephemeral test drones (offline with no active mission) and legacy mock drones
            if user["role"] in ["senior_officer", "control_room_officer"]:
                active_missions_subq = select(Mission.mission_id).where(
                    Mission.status == "active",
                    ~Mission.mission_id.like("M-10%")
                )
                stmt = stmt.where(
                    and_(
                        ~Drone.id.in_(["DRN-001", "DRN-002", "DRN-003", "DRN-004", "DRN-005"]),
                        or_(
                            Drone.status.in_(["live", "degraded", "draining"]),
                            Drone.mission_id.in_(active_missions_subq)
                        )
                    )
                )
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
    # Synchronize missing missions from ANPR, drone_postgres flight telemetry (tracks), and evidence captures
    try:
        discovered_missions = set()

        # 1. Check ANPR microservice
        try:
            anpr_res = await _analytics_get(f"{ANPR_URL}/missions", timeout=3.0)
            if anpr_res and "missions" in anpr_res:
                discovered_missions.update(anpr_res["missions"])
        except Exception:
            pass

        # 2. Check drone_postgres tracks for all recorded flight missions
        try:
            import asyncpg
            conn = await asyncpg.connect("postgresql://dvas:dvas_secret@drone_postgres:5432/dvas")
            try:
                rows = await conn.fetch(
                    "SELECT DISTINCT mission_id_str FROM tracks WHERE mission_id_str IS NOT NULL AND mission_id_str != ''"
                )
                for r in rows:
                    if r["mission_id_str"]:
                        discovered_missions.add(r["mission_id_str"])
            finally:
                await conn.close()
        except Exception as exc:
            logger.debug(f"Could not read missions from drone_postgres: {exc}")

        async with async_session_factory() as session:
            # 3. Check evidence table for distinct missions
            evi_res = await session.execute(
                select(distinct(Evidence.mission_id)).where(Evidence.mission_id != "", Evidence.mission_id.is_not(None))
            )
            for m in evi_res.scalars().all():
                if m:
                    discovered_missions.add(m)

            if discovered_missions:
                existing = await session.execute(
                    select(Mission.mission_id).where(Mission.mission_id.in_(discovered_missions))
                )
                existing_ids = {row[0] for row in existing.all()}
                
                missing_ids = discovered_missions - existing_ids
                if missing_ids:
                    logger.info(f"Synchronizing {len(missing_ids)} missing missions into catalog")
                    for mid in missing_ids:
                        # Extract timestamp from mission_id like MSN-20260914-1318-14-BETA
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
                        
                        drone_id = f"DRN-{mid}" if mid.startswith("MSN-") else ""
                        session.add(Mission(
                            mission_id=mid,
                            name=f"Mission {mid}",
                            status="completed",
                            drone_id=drone_id,
                            jurisdiction="Thiruvananthapuram",
                            operator_name="System Recovered",
                            operator_ids=[],
                            started_at=started_at
                        ))
                    await session.commit()
    except Exception as e:
        logger.warning(f"Could not synchronize missions: {e}")

    def _enrich_mission_dict(m_dict: dict) -> dict:
        obj_raw = m_dict.get("objective")
        if obj_raw and isinstance(obj_raw, str) and obj_raw.startswith("{"):
            try:
                extra = json.loads(obj_raw)
                if isinstance(extra, dict):
                    for k, v in extra.items():
                        if k not in m_dict or not m_dict[k]:
                            m_dict[k] = v
            except Exception:
                pass
        if not m_dict.get("chain_hash"):
            canonical = f"{m_dict.get('mission_id')}|{m_dict.get('started_at')}|{m_dict.get('operator_name')}"
            m_dict["chain_hash"] = hashlib.sha256(canonical.encode()).hexdigest()
        return m_dict

    async with async_session_factory() as session:
        # All roles (drone_operator, senior_officer, control_room_officer, system_administrator)
        # can view missions — filtered by jurisdiction if set for control_room_officer
        stmt = select(Mission).where(~Mission.mission_id.like("M-10%"))
        if user.get("role") == "control_room_officer":
            jur = user.get("jurisdiction")
            if jur:
                # Show missions in their jurisdiction, State HQ, statewide, or with no jurisdiction set
                stmt = stmt.where(
                    or_(
                        Mission.jurisdiction == jur,
                        Mission.jurisdiction.is_(None),
                        Mission.jurisdiction == "",
                        Mission.jurisdiction.in_(["Kerala", "State HQ", "Thiruvananthapuram"])
                    )
                )
        stmt = stmt.order_by(Mission.started_at.desc()).limit(300)
        result = await session.execute(stmt)
        return [_enrich_mission_dict(_row_to_dict(r)) for r in result.scalars().all()]


# ---------- Mission create / end (wired to Edge Mission Control) ----------
@router.post("/missions")
async def create_mission(
    payload: dict = Body(...),
    user: dict = Depends(get_current_user),
    request: Request = None,
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
    video_source = payload.get("video_source", "rtmp://10.10.55.115:1935/live/stream")
    login_ts_ist = payload.get("login_ts_ist") or datetime.now(timezone.utc).isoformat()
    frames_sent = payload.get("frames_sent", 0)
    frames_ingested = payload.get("frames_ingested", 0)

    canonical = f"{mission_id}|{started_at}|{operator_name}|{login_ts_ist}"
    chain_hash = payload.get("chain_hash") or hashlib.sha256(canonical.encode()).hexdigest()

    meta = {
        "video_source": video_source,
        "login_ts_ist": login_ts_ist,
        "frames_sent": frames_sent,
        "frames_ingested": frames_ingested,
        "chain_hash": chain_hash,
    }

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
            jurisdiction=user.get("jurisdiction", ""),
            objective=json.dumps(meta),
        )
        session.add(mission)
        await session.commit()

    # Log to tamper-evident audit trail
    try:
        await write_audit_v2(
            actor=user,
            action="START_MISSION",
            action_type="create",
            category="operations",
            severity="info",
            target=mission_id,
            target_type="mission",
            target_id=mission_id,
            target_name=name,
            details=f"Mission started; Video source: {video_source}; Login Date: {login_ts_ist[:10]}",
            source_module="edge",
            request=request,
        )
    except Exception as audit_err:
        logger.warning(f"Could not write audit for START_MISSION: {audit_err}")

    return {"status": "saved", "mission_id": mission_id, "chain_hash": chain_hash}


@router.patch("/missions/{mission_id}/end")
async def end_mission_record(
    mission_id: str,
    payload: dict = Body(default={}),
    user: dict = Depends(get_current_user),
    request: Request = None,
):
    """Mark a mission as completed and record the end time."""
    async with async_session_factory() as session:
        mission = await session.get(Mission, mission_id)
        if not mission:
            raise HTTPException(404, f"Mission {mission_id} not found")
        # Check jurisdiction / mission scope if scoped
        if user.get("mission_scope") and mission_id not in user["mission_scope"]:
            raise HTTPException(403, f"Not permitted to end mission {mission_id}")
        mission.status = "completed"
        ended_at_str = datetime.now(timezone.utc).isoformat()
        mission.ended_at = ended_at_str

        frames_processed = payload.get("frames_processed") or payload.get("frames_sent") or 0
        frames_ingested = payload.get("frames_ingested") if payload.get("frames_ingested") is not None else (
            payload.get("frames_selected") if payload.get("frames_selected") is not None else max(0, frames_processed - 8)
        )
        video_source = payload.get("video_source")

        extra = {}
        if mission.objective and mission.objective.startswith("{"):
            try:
                extra = json.loads(mission.objective)
            except Exception:
                extra = {}

        if frames_processed or "frames_sent" in payload or "frames_processed" in payload:
            extra["frames_sent"] = frames_processed
            extra["frames_ingested"] = frames_ingested
        if video_source:
            extra["video_source"] = video_source
        extra["ended_at"] = ended_at_str
        mission.objective = json.dumps(extra)

        await session.commit()

    try:
        await write_audit_v2(
            actor=user,
            action="END_MISSION",
            action_type="update",
            category="operations",
            severity="info",
            target=mission_id,
            target_type="mission",
            target_id=mission_id,
            target_name=mission.name,
            details=f"Mission concluded; Total frames processed: {frames_processed}",
            source_module="edge",
            request=request,
        )
    except Exception as audit_err:
        logger.warning(f"Could not write audit for END_MISSION: {audit_err}")

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
