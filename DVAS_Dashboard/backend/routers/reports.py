import asyncio
import uuid
import logging
from datetime import datetime, timezone
import io
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from jose import jwt
from sqlalchemy import select, update, desc, func, or_

from database import async_session_factory
from models import Report, Alert, Evidence, Mission, Drone, User
from core.security import get_current_user, get_jwks, require_roles, scope_filter_for, _apply_scope_to_query, _row_to_dict
from audit_service import write_audit_v2
from schemas.reports import ReportGenerateIn
from services.pdf_service import (
    _make_daily_incident_pdf,
    _make_weekly_response_pdf,
    _make_drone_utilisation_pdf,
    _make_mission_aar_pdf,
)
from core.analytics_client import _get_minio_client
from services.evidence_integrity import sha256_hex

router = APIRouter(prefix="/api", tags=["Reports"])
logger = logging.getLogger("dvas")

_report_cache: dict[str, bytes] = {}


async def _load_aar_data(session, mission: dict):
    """Alerts/evidence tagged to the mission, plus untagged alerts from the same drone
    inside the mission window (kept separate, never mixed in — B12)."""
    mission_id = mission["mission_id"]
    alerts_res = await session.execute(select(Alert).where(Alert.mission_id == mission_id))
    alerts = [_row_to_dict(a) for a in alerts_res.scalars().all()]
    ev_res = await session.execute(select(Evidence).where(Evidence.mission_id == mission_id))
    evidence = [_row_to_dict(e) for e in ev_res.scalars().all()]

    window_alerts = []
    if mission.get("drone_id") and mission.get("started_at") and mission.get("ended_at"):
        # ts columns are ISO-8601 UTC strings, so string order is time order
        win_res = await session.execute(
            select(Alert).where(
                Alert.drone_id == mission["drone_id"],
                or_(Alert.mission_id.is_(None), Alert.mission_id == ""),
                Alert.ts >= mission["started_at"],
                Alert.ts <= mission["ended_at"],
            ).order_by(Alert.ts)
        )
        window_alerts = [_row_to_dict(a) for a in win_res.scalars().all()]
    return alerts, evidence, window_alerts

async def _generate_report_task(report_id: str, report_type: str, params: dict, user: dict):
    try:
        async with async_session_factory() as session:
            # Hydrate full user details from DB
            u_res = await session.execute(select(User).where(User.email == user.get("email", "")))
            u_obj = u_res.scalar_one_or_none()
            if u_obj:
                user["name"] = u_obj.name or user.get("name", "")
                user["role"] = u_obj.role or user.get("role", "")
                user["jurisdiction"] = u_obj.jurisdiction or user.get("jurisdiction", "")

            # Gather data based on report_type
            if report_type == "daily_incident":
                target_date = params.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
                alerts_res = await session.execute(select(Alert).where(Alert.ts.like(f"{target_date}%")))
                alerts = [_row_to_dict(a) for a in alerts_res.scalars().all()]
                
                ev_res = await session.execute(select(Evidence).where(Evidence.ts.like(f"{target_date}%")))
                evidence = [_row_to_dict(e) for e in ev_res.scalars().all()]
                
                miss_res = await session.execute(select(Mission))
                missions = [_row_to_dict(m) for m in miss_res.scalars().all()]
                
                pdf_bytes = _make_daily_incident_pdf(user, alerts, evidence, missions, target_date)
                
            elif report_type == "weekly_response":
                week_start = params.get("week_start", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
                alerts_res = await session.execute(select(Alert).order_by(desc(Alert.ts)).limit(500))
                alerts = [_row_to_dict(a) for a in alerts_res.scalars().all()]
                
                miss_res = await session.execute(select(Mission))
                missions = [_row_to_dict(m) for m in miss_res.scalars().all()]
                
                pdf_bytes = _make_weekly_response_pdf(user, alerts, missions, week_start)
                
            elif report_type == "drone_utilisation":
                period_days = int(params.get("period_days", 30))
                miss_res = await session.execute(select(Mission))
                missions = [_row_to_dict(m) for m in miss_res.scalars().all()]
                
                drone_res = await session.execute(select(Drone))
                drones = [_row_to_dict(d) for d in drone_res.scalars().all()]
                
                pdf_bytes = _make_drone_utilisation_pdf(user, missions, drones, period_days)
                
            elif report_type == "mission_after_action":
                from sqlalchemy import text
                mission_id = params.get("mission_id")
                miss_res = await session.execute(select(Mission).where(Mission.mission_id == mission_id))
                mission_obj = miss_res.scalar_one_or_none()
                if not mission_obj:
                    raise ValueError("Mission not found")
                mission = _row_to_dict(mission_obj)
                alerts, evidence, window_alerts = await _load_aar_data(session, mission)
                pdf_bytes = _make_mission_aar_pdf(user, mission, alerts, evidence, window_alerts)
            else:
                raise ValueError("Unknown report type")

            # Try to upload to MinIO
            file_key = f"reports/{report_id}.pdf"
            minio_client = _get_minio_client()
            uploaded = False
            if minio_client:
                try:
                    minio_client.put_object(
                        "dvas-evidence",
                        file_key,
                        io.BytesIO(pdf_bytes),
                        length=len(pdf_bytes),
                        content_type="application/pdf"
                    )
                    uploaded = True
                except Exception as e:
                    logger.error(f"MinIO upload failed: {e}")
            
            if not uploaded:
                _report_cache[report_id] = pdf_bytes
                
            # Update Report row
            now_str = datetime.now(timezone.utc).isoformat()
            await session.execute(
                update(Report)
                .where(Report.id == report_id)
                .values(
                    status="ready",
                    file_key=file_key,
                    file_size_bytes=len(pdf_bytes),
                    sha256=sha256_hex(pdf_bytes),        # E3
                    generated_at=now_str
                )
            )
            await session.commit()
            
    except Exception as exc:
        logger.error(f"Report generation failed: {exc}")
        async with async_session_factory() as session:
            await session.execute(
                update(Report)
                .where(Report.id == report_id)
                .values(
                    status="failed",
                    error_message=str(exc)
                )
            )
            await session.commit()


@router.post("/reports/generate")
async def generate_report(
    body: ReportGenerateIn,
    user: dict = Depends(require_roles("senior_officer")),
    request: Request = None
):
    report_id = f"RPT-{uuid.uuid4().hex[:8]}"
    now_str = datetime.now(timezone.utc).isoformat()
    
    # Merge any extra fields provided at top-level into params
    effective_params = dict(body.params or {})
    if hasattr(body, "__pydantic_extra__") and body.__pydantic_extra__:
        effective_params.update(body.__pydantic_extra__)
    
    title = f"{body.report_type.replace('_', ' ').title()}"
    async with async_session_factory() as session:
        if body.report_type == "mission_after_action" and effective_params.get("mission_id"):
            m_res = await session.execute(select(Mission.name).where(Mission.mission_id == effective_params["mission_id"]))
            m_row = m_res.first()
            if m_row and m_row[0]:
                title = f"Mission After-Action: {m_row[0]} ({effective_params['mission_id']})"
            else:
                title = f"Mission After-Action: {effective_params['mission_id']}"
        elif effective_params:
            p_vals = list(body.params.values())
            if p_vals:
                title += f" - {p_vals[0]}"

        officer_name = user.get("name") or user.get("email", "")
        new_rep = Report(
            id=report_id,
            title=title,
            report_type=body.report_type,
            status="generating",
            requested_at=now_str,
            requested_by=user["email"],
            watermark_text=f"CONFIDENTIAL — {officer_name} ({user['email']})"
        )
        session.add(new_rep)
        await session.commit()
        
    asyncio.create_task(_generate_report_task(report_id, body.report_type, effective_params, user))
    
    await write_audit_v2(
        actor=user,
        action="report_generate",
        target=report_id,
        target_type="report",
        target_id=report_id,
        details=f"Type: {body.report_type}",
        source_endpoint="POST /api/reports/generate",
        request=request
    )
    
    return {"id": report_id, "status": "generating", "title": title}


@router.get("/reports")
async def list_reports(user: dict = Depends(require_roles("control_room_officer", "senior_officer"))):
    async with async_session_factory() as session:
        stmt = select(Report)
        if user["role"] == "control_room_officer":
            stmt = stmt.where(Report.requested_by == user["email"])
        stmt = stmt.order_by(desc(Report.requested_at)).limit(100)
        
        result = await session.execute(stmt)
        reports = [_row_to_dict(r) for r in result.scalars().all()]
        
    return reports


@router.get("/reports/{report_id}")
async def get_report(report_id: str, user: dict = Depends(require_roles("control_room_officer", "senior_officer"))):
    async with async_session_factory() as session:
        result = await session.execute(select(Report).where(Report.id == report_id))
        r_obj = result.scalar_one_or_none()
        
    if not r_obj:
        raise HTTPException(404, "Report not found")
        
    rep = _row_to_dict(r_obj)
    if user["role"] == "control_room_officer" and rep["requested_by"] != user["email"]:
        raise HTTPException(403, "Not your report")
        
    return rep


@router.get("/reports/{report_id}/download")
async def download_report(report_id: str, request: Request):
    user = await get_current_user(request)
    return await stream_report(report_id, user, request)


async def stream_report(report_id: str, user: dict, request) -> StreamingResponse:
    # Only senior_officer can download reports directly
    if user.get("role") not in ("senior_officer", "system_administrator"):
        raise HTTPException(403, "Only Senior Officers can download reports directly")

    async with async_session_factory() as session:
        result = await session.execute(select(Report).where(Report.id == report_id))
        r_obj = result.scalar_one_or_none()
        
    if not r_obj:
        raise HTTPException(404, "Report not found")
        
    rep = _row_to_dict(r_obj)
    if rep["status"] != "ready":
        raise HTTPException(400, "Report is not ready")
        
    pdf_bytes = None
    minio_client = _get_minio_client()
    if minio_client and rep.get("file_key"):
        try:
            response = minio_client.get_object("dvas-evidence", rep["file_key"])
            pdf_bytes = response.read()
            response.close()
            response.release_conn()
        except Exception:
            pass
            
    if not pdf_bytes:
        pdf_bytes = _report_cache.get(report_id)
        
    if not pdf_bytes:
        raise HTTPException(404, "Report file not found")

    digest = sha256_hex(pdf_bytes)
    if rep.get("sha256") and rep["sha256"] != digest:
        await write_audit_v2(actor=user, action="report_download", severity="critical", target=report_id,
                             target_type="report", target_id=report_id, status="failure",
                             details="Stored report does not match its SHA-256", request=request)
        raise HTTPException(409, "Stored report failed its integrity check")
    await write_audit_v2(
        actor=user,
        action="report_download",
        target=report_id,
        target_type="report",
        target_id=report_id,
        details=f"Downloaded PDF {digest}",
        after_values={"sha256": digest},
        source_endpoint="GET /api/reports/{id}/download",
        request=request
    )

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{report_id}.pdf"',
                 "X-Content-SHA256": digest, "Cache-Control": "private, no-store"}
    )


@router.get("/reports/{report_type}/preview-data")
async def preview_report_data(
    report_type: str,
    request: Request,
    user: dict = Depends(require_roles("senior_officer", "system_administrator"))
):
    params = dict(request.query_params)
    try:
        async with async_session_factory() as session:
            u_res = await session.execute(select(User).where(User.email == user.get("email", "")))
            u_obj = u_res.scalar_one_or_none()
            if u_obj:
                user["name"] = u_obj.name or user.get("name", "")
                user["role"] = u_obj.role or user.get("role", "")
                user["jurisdiction"] = u_obj.jurisdiction or user.get("jurisdiction", "")

            if report_type == "daily_incident":
                target_date = params.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
                alerts_res = await session.execute(select(Alert).where(Alert.ts.like(f"{target_date}%")))
                alerts = [_row_to_dict(a) for a in alerts_res.scalars().all()]
                ev_res = await session.execute(select(Evidence).where(Evidence.ts.like(f"{target_date}%")))
                evidence = [_row_to_dict(e) for e in ev_res.scalars().all()]
                miss_res = await session.execute(select(Mission))
                missions = [_row_to_dict(m) for m in miss_res.scalars().all()]
                return {
                    "user": user,
                    "alerts": alerts,
                    "evidence_items": evidence,
                    "missions": missions,
                    "target_date": target_date
                }
                
            elif report_type == "weekly_response":
                week_start = params.get("week_start", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
                alerts_res = await session.execute(select(Alert).order_by(desc(Alert.ts)).limit(500))
                alerts = [_row_to_dict(a) for a in alerts_res.scalars().all()]
                miss_res = await session.execute(select(Mission))
                missions = [_row_to_dict(m) for m in miss_res.scalars().all()]
                return {
                    "user": user,
                    "alerts": alerts,
                    "missions": missions,
                    "week_start_date": week_start
                }
                
            elif report_type == "drone_utilisation":
                period_days = int(params.get("period_days", 30))
                miss_res = await session.execute(select(Mission))
                missions = [_row_to_dict(m) for m in miss_res.scalars().all()]
                drone_res = await session.execute(select(Drone))
                drones = [_row_to_dict(d) for d in drone_res.scalars().all()]
                return {
                    "user": user,
                    "missions": missions,
                    "drones": drones,
                    "period_days": period_days
                }
                
            elif report_type == "mission_after_action":
                from sqlalchemy import text
                mission_id = params.get("mission_id")
                miss_res = await session.execute(select(Mission).where(Mission.mission_id == mission_id))
                mission_obj = miss_res.scalar_one_or_none()
                if not mission_obj:
                    raise HTTPException(404, "Mission not found")
                mission = _row_to_dict(mission_obj)
                alerts, evidence, window_alerts = await _load_aar_data(session, mission)
                return {
                    "user": user,
                    "mission": mission,
                    "alerts": alerts,
                    "evidence_items": evidence,
                    "mission_window_events": window_alerts,
                }
            else:
                raise HTTPException(400, "Unknown report type")
                
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Preview generation failed: {exc}")
        raise HTTPException(500, str(exc))
