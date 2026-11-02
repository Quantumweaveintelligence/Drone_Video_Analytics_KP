import logging
import json
import hashlib
from datetime import datetime, timezone
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select, func, or_, and_, distinct

from database import async_session_factory
from models import Mission, Drone, Alert, Evidence, Export
from core.security import get_current_user, jurisdiction_clause, require_roles, _row_to_dict
from core.analytics_client import _analytics_get
from core.config import ANPR_URL, INTRUSION_URL, ANALYTICS_DATABASE_URL
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
            # Same jurisdiction rule as alerts / evidence / missions (J1, J3)
            jur = jurisdiction_clause(Drone.jurisdiction, user)
            if jur is not None:
                stmt = stmt.where(jur)
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


async def _discover_mission_ids() -> set:
    """Mission ids seen by ANPR, intrusion, analytics tracks, drones and evidence."""
    discovered_missions = set()

    # 1. Check ANPR microservice
    try:
        anpr_res = await _analytics_get(f"{ANPR_URL}/missions", timeout=3.0)
        if anpr_res and "missions" in anpr_res:
            discovered_missions.update(anpr_res["missions"])
    except Exception:
        pass

    # 2. Check Intrusion microservice
    try:
        intrusion_res = await _analytics_get(f"{INTRUSION_URL}/intrusion/missions", timeout=3.0)
        if intrusion_res and "missions" in intrusion_res:
            for im in intrusion_res["missions"]:
                if isinstance(im, dict) and im.get("mission_id"):
                    discovered_missions.add(im["mission_id"])
                elif isinstance(im, str):
                    discovered_missions.add(im)
    except Exception:
        pass

    # 3. Analytics DB tracks for all recorded flight missions
    if ANALYTICS_DATABASE_URL:
        try:
            import asyncpg
            conn = await asyncpg.connect(ANALYTICS_DATABASE_URL)
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
            logger.debug(f"Could not read missions from analytics DB: {exc}")

    async with async_session_factory() as session:
        # 4. Check drones table for distinct missions
        drone_missions = await session.execute(
            select(distinct(Drone.mission_id)).where(Drone.mission_id != "", Drone.mission_id.is_not(None))
        )
        discovered_missions.update(m for m in drone_missions.scalars().all() if m)

        # 5. Check evidence table for distinct missions
        evi_res = await session.execute(
            select(distinct(Evidence.mission_id)).where(Evidence.mission_id != "", Evidence.mission_id.is_not(None))
        )
        discovered_missions.update(m for m in evi_res.scalars().all() if m)
    return discovered_missions


@router.get("/missions/untracked")
async def list_untracked_missions(user: dict = Depends(require_roles("system_administrator"))):
    """Mission ids that have analytics data or evidence but no missions row.

    Read-only (B26): GET /missions used to insert these with a made-up name,
    operator and jurisdiction. An admin now decides what to do with them.
    """
    discovered = await _discover_mission_ids()
    if not discovered:
        return {"untracked_mission_ids": []}
    async with async_session_factory() as session:
        existing = await session.execute(
            select(Mission.mission_id).where(Mission.mission_id.in_(discovered))
        )
        existing_ids = {row[0] for row in existing.all()}
    return {"untracked_mission_ids": sorted(discovered - existing_ids)}


@router.get("/missions")
async def list_missions(user: dict = Depends(get_current_user)):
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
        if user.get("role") == "drone_operator":
            # Missions this operator flew (recorded at start) or is assigned to
            conds = [Mission.operator_ids.contains([user.get("id") or ""])]
            if user.get("mission_scope"):
                conds.append(Mission.mission_id.in_(user["mission_scope"]))
            op_email = (user.get("email") or "").strip()
            if op_email:   # missions started before operator_ids was recorded
                conds.append(Mission.operator_name.contains(op_email, autoescape=True))
            stmt = stmt.where(or_(*conds))
        else:
            jur = jurisdiction_clause(Mission.jurisdiction, user)
            if jur is not None:
                stmt = stmt.where(jur)
        stmt = stmt.order_by(Mission.started_at.desc()).limit(300)
        result = await session.execute(stmt)
        return [_enrich_mission_dict(_row_to_dict(r)) for r in result.scalars().all()]


# ---------- Mission create / end (wired to Edge Mission Control) ----------
@router.post("/missions")
async def create_mission(
    payload: dict = Body(...),
    user: dict = Depends(require_roles("drone_operator", "senior_officer")),
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
    video_source = payload.get("video_source", "")
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
    user: dict = Depends(require_roles("drone_operator", "senior_officer")),
    request: Request = None,
):
    """Mark a mission as completed: the operator who flies it, or a senior officer (J6)."""
    async with async_session_factory() as session:
        mission = await session.get(Mission, mission_id)
        if not mission:
            raise HTTPException(404, f"Mission {mission_id} not found")
        if user["role"] == "drone_operator" and not (
                user.get("id") in (mission.operator_ids or [])
                or mission_id in (user.get("mission_scope") or [])):
            raise HTTPException(403, f"Not permitted to end mission {mission_id}")
        if mission.status == "completed":
            return {"status": "already_ended", "mission_id": mission_id}
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


@router.delete("/missions/{mission_id}")
async def delete_mission_record(
    mission_id: str,
    user: dict = Depends(require_roles("senior_officer", "system_administrator")),
    request: Request = None,
):
    """Delete a mission record (seniors / admins, H2). Refused while evidence or alerts
    refer to it — deleting it would orphan them."""
    mission_name = mission_id
    async with async_session_factory() as session:
        mission = await session.get(Mission, mission_id)
        if not mission:
            raise HTTPException(404, f"Mission {mission_id} not found")
        refs = (await session.execute(select(func.count()).select_from(Evidence)
                                      .where(Evidence.mission_id == mission_id))).scalar() or 0
        refs += (await session.execute(select(func.count()).select_from(Alert)
                                       .where(Alert.mission_id == mission_id))).scalar() or 0
        if refs:
            raise HTTPException(409, f"Mission {mission_id} has {refs} alert/evidence record(s); it cannot be deleted")
        mission_name = mission.name
        await session.delete(mission)
        await session.commit()

    try:
        await write_audit_v2(
            actor=user,
            action="DELETE_MISSION",
            action_type="delete",
            category="operations",
            severity="warn",
            target=mission_id,
            target_type="mission",
            target_id=mission_id,
            target_name=mission_name,
            details=f"Mission record '{mission_name}' ({mission_id}) deleted",
            source_module="edge",
            request=request,
        )
    except Exception as audit_err:
        logger.warning(f"Could not write audit for DELETE_MISSION: {audit_err}")

    return {"status": "deleted", "mission_id": mission_id}


# ---------- Command dashboard: aggregate KPIs ----------
@router.get("/command/kpis")
async def command_kpis(user: dict = Depends(get_current_user)):
    """KPI tiles with the same scope and definitions as the lists they summarise (U1)."""
    from datetime import timedelta
    from routers.alerts import _scope_evidence
    if user["role"] not in ("control_room_officer", "senior_officer"):
        raise HTTPException(403, "Not permitted")

    def scoped(column):
        return jurisdiction_clause(column, user)

    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    async with async_session_factory() as session:
        q = select(func.count()).select_from(Mission).where(Mission.status == "active")
        if scoped(Mission.jurisdiction) is not None:
            q = q.where(scoped(Mission.jurisdiction))
        active_missions = (await session.execute(q)).scalar() or 0

        q = select(func.count()).select_from(Alert).where(
            Alert.ts >= since, or_(Alert.category.in_(["ai", "manual"]), Alert.category.is_(None)))
        if scoped(Alert.jurisdiction) is not None:
            q = q.where(scoped(Alert.jurisdiction))
        alerts_24h = (await session.execute(q)).scalar() or 0

        q = _scope_evidence(select(func.count()).select_from(Evidence), user).where(Evidence.status != "disposed")
        evidence_total = (await session.execute(q)).scalar() or 0

        q = select(func.count()).select_from(Export).where(Export.status == "pending")
        if user["role"] == "control_room_officer":
            q = q.where(Export.requested_by == user["email"])      # what /api/exports shows a CRO
        exports_pending = (await session.execute(q)).scalar() or 0

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


# ---------- Command dashboard: per-district rollup (senior officers, CMD-05 / P4) ----------
@router.get("/command/districts")
async def command_districts(user: dict = Depends(require_roles("senior_officer"))):
    """Counts per jurisdiction: alerts in the last 24 h, active missions, live drones, evidence."""
    from datetime import timedelta
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    rows: dict = {}

    def bucket(name):
        key = name or "Unassigned"
        return rows.setdefault(key, {"jurisdiction": key, "alerts_24h": 0, "active_missions": 0,
                                     "drones_live": 0, "evidence": 0})

    async with async_session_factory() as session:
        for jur, n in (await session.execute(
                select(Alert.jurisdiction, func.count()).where(
                    Alert.ts >= since, or_(Alert.category.in_(["ai", "manual"]), Alert.category.is_(None)))
                .group_by(Alert.jurisdiction))).all():
            bucket(jur)["alerts_24h"] = n
        for jur, n in (await session.execute(
                select(Mission.jurisdiction, func.count()).where(Mission.status == "active")
                .group_by(Mission.jurisdiction))).all():
            bucket(jur)["active_missions"] = n
        for jur, n in (await session.execute(
                select(Drone.jurisdiction, func.count()).where(Drone.status == "live")
                .group_by(Drone.jurisdiction))).all():
            bucket(jur)["drones_live"] = n
        for jur, n in (await session.execute(
                select(Evidence.jurisdiction, func.count()).where(Evidence.status != "disposed")
                .group_by(Evidence.jurisdiction))).all():
            bucket(jur)["evidence"] = n
    return {"generated_at": datetime.now(timezone.utc).isoformat(),
            "districts": sorted(rows.values(), key=lambda r: (-r["alerts_24h"], r["jurisdiction"]))}
