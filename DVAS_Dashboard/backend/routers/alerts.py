import io
import json
import uuid
import zipfile
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update, func, or_, distinct, case
from jose import jwt

from database import async_session_factory
from models import Alert, Evidence, Export
from core.security import get_current_user, get_jwks, scope_filter_for, _apply_scope_to_query, _row_to_dict, require_roles
from audit_service import write_audit_v2
from schemas.exports import ExportRequestIn, ExportDecisionIn
from schemas.evidence import LegalHoldIn, LegalHoldReleaseIn, DirectExportIn
from services.pdf_service import _make_65b_pdf
from core.analytics_client import _get_minio_client

logger = logging.getLogger("dvas")
router = APIRouter(prefix="/api", tags=["Alerts, Evidence & Exports"])


@router.get("/alerts")
async def list_alerts(user: dict = Depends(get_current_user)):
    role = user.get("role")
    async with async_session_factory() as session:
        if role in ("system_administrator", "senior_officer"):
            stmt = select(Alert).order_by(Alert.ts.desc()).limit(200)
        elif role == "control_room_officer":
            f = scope_filter_for(user)
            if "__deny__" in f:
                stmt = select(Alert).order_by(Alert.ts.desc()).limit(200)
            else:
                jur = f.get("jurisdiction")
                if jur:
                    stmt = select(Alert).where(or_(
                        Alert.jurisdiction == jur,
                        Alert.jurisdiction == "Kerala",
                        Alert.jurisdiction == "State HQ",
                        Alert.jurisdiction.is_(None)
                    )).order_by(Alert.ts.desc()).limit(200)
                else:
                    stmt = select(Alert).order_by(Alert.ts.desc()).limit(200)
        elif role == "drone_operator":
            stmt = select(Alert).order_by(Alert.ts.desc()).limit(100)
        else:
            raise HTTPException(403, "Not permitted")
        result = await session.execute(stmt)
        return [_row_to_dict(r) for r in result.scalars().all()]


@router.post("/alerts/{alert_id}/ack")
async def ack_alert(
    alert_id: str, user: dict = Depends(get_current_user), request: Request = None
):
    # CRO, SR, and sysadmin can acknowledge alerts
    if user["role"] not in ("control_room_officer", "senior_officer", "system_administrator"):
        raise HTTPException(403, "Not permitted")
    async with async_session_factory() as session:
        result = await session.execute(
            update(Alert)
            .where(Alert.id == alert_id)
            .values(
                acknowledged_by=user.get("email") or user.get("name") or "Operator",
                acknowledged_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        if result.rowcount == 0:
            raise HTTPException(404, "Alert not found")

        # Also sync Incident if matching
        try:
            from models import Incident
            await session.execute(
                update(Incident)
                .where(Incident.id == alert_id)
                .values(status="ACKNOWLEDGED")
            )
        except Exception:
            pass

        # Also sync AIModelSuggestion if matching
        try:
            from models import AIModelSuggestion
            await session.execute(
                update(AIModelSuggestion)
                .where(AIModelSuggestion.id == alert_id)
                .values(status="ACKNOWLEDGED")
            )
        except Exception:
            pass

        await session.commit()

    # Broadcast websocket acknowledgments so all live dashboards update
    try:
        from services.websocket_hub import hub
        await hub.broadcast({
            "type": "incident_ack",
            "incident_id": alert_id,
            "status": "ACKNOWLEDGED",
        })
        await hub.broadcast({
            "type": "model_suggestion_status",
            "suggestion_id": alert_id,
            "status": "ACKNOWLEDGED",
        })
    except Exception as ws_err:
        logger.debug(f"Could not broadcast ack: {ws_err}")

    await write_audit_v2(
        actor=user, action="alert_ack",
        target=alert_id, target_type="alert",
        target_id=alert_id,
        details="acknowledged",
        before_values={"acknowledged_by": None},
        after_values={"acknowledged_by": user.get("email") or user.get("name")},
        source_endpoint="POST /api/alerts/{id}/ack",
        request=request,
    )
    return {"ok": True}


PERSON_EVENT_TYPES = ["person_detected", "perimeter_intrusion", "intrusion_alert"]
VEHICLE_EVENT_TYPES = ["vehicle_detected", "vehicle_anomaly", "wrong_way", "theft_alert", "vessel_detected", "vessel_proximity", "vessel_loitering", "proximity_alert"]
CROWD_EVENT_TYPES = ["crowd_threshold", "crowd_surge", "unattended_object"]


@router.get("/evidence")
async def list_evidence(
    drone_id: str = "",
    mission_id: str = "",
    event_type: str = "",
    category: str = "",
    dedup: bool = True,
    from_ts: str = "",
    to_ts: str = "",
    legal_hold_only: bool = False,
    search: str = "",
    limit: int = 60,
    offset: int = 0,
    user: dict = Depends(get_current_user)
):
    role = user.get("role")
    async with async_session_factory() as session:
        stmt = select(Evidence)
        if role in ("system_administrator", "senior_officer"):
            pass  # State-wide aggregate access
        elif role == "control_room_officer":
            jur = user.get("jurisdiction")
            if jur and not (drone_id or mission_id):
                stmt = stmt.where(or_(
                    Evidence.jurisdiction == jur,
                    Evidence.jurisdiction.is_(None),
                    Evidence.jurisdiction == "",
                    Evidence.jurisdiction.in_(["Kerala", "State HQ", "Thiruvananthapuram"])
                ))
        elif role == "drone_operator":
            if user.get("mission_scope"):
                stmt = stmt.where(Evidence.mission_id.in_(user["mission_scope"]))
        else:
            f = scope_filter_for(user)
            if "__deny__" in f:
                raise HTTPException(403, "Not permitted")
            stmt = _apply_scope_to_query(stmt, Evidence, f)
            if stmt is None:
                raise HTTPException(403, "Not permitted")
        
        # High-level category filtering
        if category == "person":
            stmt = stmt.where(Evidence.event_type.in_(PERSON_EVENT_TYPES))
        elif category == "vehicle":
            stmt = stmt.where(Evidence.event_type.in_(VEHICLE_EVENT_TYPES))
        elif category == "crowd":
            stmt = stmt.where(Evidence.event_type.in_(CROWD_EVENT_TYPES))

        if drone_id:
            stmt = stmt.where(Evidence.drone_id == drone_id)
        if mission_id:
            stmt = stmt.where(Evidence.mission_id == mission_id)
        if event_type:
            stmt = stmt.where(Evidence.event_type == event_type)
        if from_ts:
            stmt = stmt.where(Evidence.ts >= from_ts)
        if to_ts:
            stmt = stmt.where(Evidence.ts <= to_ts)
        if legal_hold_only:
            stmt = stmt.where(Evidence.legal_hold == True)
        if search:
            stmt = stmt.where(or_(
                Evidence.label.ilike(f"%{search}%"),
                Evidence.id.ilike(f"%{search}%"),
                Evidence.mission_id.ilike(f"%{search}%"),
                Evidence.drone_id.ilike(f"%{search}%"),
            ))
        
        if dedup:
            dedup_key = case(
                (func.nullif(Evidence.primary_image_key, '') != None, Evidence.primary_image_key),
                else_=Evidence.id
            ).label('dedup_key')
            dup_count = func.count().over(partition_by=dedup_key).label('duplicate_count')

            inner = (
                stmt.add_columns(dup_count)
                .distinct(dedup_key)
                .order_by(dedup_key, Evidence.ts.desc())
                .subquery('deduped')
            )

            count_stmt = select(func.count()).select_from(inner)
            total = await session.scalar(count_stmt)

            outer = select(inner).order_by(inner.c.ts.desc()).offset(offset).limit(limit)
            result = await session.execute(outer)
            items = [dict(r) for r in result.mappings().all()]

            return {
                "items": items,
                "total": total or 0
            }
        else:
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = await session.scalar(count_stmt)

            stmt = stmt.order_by(Evidence.ts.desc()).offset(offset).limit(limit)
            result = await session.execute(stmt)
            items = []
            for r in result.scalars().all():
                d = _row_to_dict(r)
                d["duplicate_count"] = 1
                items.append(d)

            return {
                "items": items,
                "total": total or 0
            }


@router.get("/evidence/meta")
async def get_evidence_meta(user: dict = Depends(get_current_user)):
    async with async_session_factory() as session:
        drones = (await session.execute(
            select(distinct(Evidence.drone_id)).where(Evidence.drone_id != "", Evidence.drone_id.is_not(None))
        )).scalars().all()
        missions = (await session.execute(
            select(distinct(Evidence.mission_id)).where(Evidence.mission_id != "", Evidence.mission_id.is_not(None))
        )).scalars().all()
        types = (await session.execute(
            select(distinct(Evidence.event_type)).where(Evidence.event_type != "", Evidence.event_type.is_not(None))
        )).scalars().all()

        dedup_key = case(
            (func.nullif(Evidence.primary_image_key, '') != None, Evidence.primary_image_key),
            else_=Evidence.id
        )

        counts = {
            "all": (await session.scalar(select(func.count(Evidence.id)))) or 0,
            "all_dedup": (await session.scalar(select(func.count(distinct(dedup_key))))) or 0,
            "person": (await session.scalar(select(func.count(Evidence.id)).where(Evidence.event_type.in_(PERSON_EVENT_TYPES)))) or 0,
            "person_dedup": (await session.scalar(select(func.count(distinct(dedup_key))).where(Evidence.event_type.in_(PERSON_EVENT_TYPES)))) or 0,
            "vehicle": (await session.scalar(select(func.count(Evidence.id)).where(Evidence.event_type.in_(VEHICLE_EVENT_TYPES)))) or 0,
            "vehicle_dedup": (await session.scalar(select(func.count(distinct(dedup_key))).where(Evidence.event_type.in_(VEHICLE_EVENT_TYPES)))) or 0,
            "crowd": (await session.scalar(select(func.count(Evidence.id)).where(Evidence.event_type.in_(CROWD_EVENT_TYPES)))) or 0,
            "crowd_dedup": (await session.scalar(select(func.count(distinct(dedup_key))).where(Evidence.event_type.in_(CROWD_EVENT_TYPES)))) or 0,
        }

        return {
            "drones": sorted([d for d in drones if d]),
            "missions": sorted([m for m in missions if m]),
            "event_types": sorted([t for t in types if t]),
            "counts": counts
        }



@router.get("/evidence/{evidence_id}")
async def get_evidence(evidence_id: str, user: dict = Depends(get_current_user)):
    if user["role"] not in ("control_room_officer", "senior_officer", "system_administrator"):
        raise HTTPException(403, "Not permitted")
    async with async_session_factory() as session:
        result = await session.execute(select(Evidence).where(Evidence.id == evidence_id))
        ev_obj = result.scalar_one_or_none()
        if not ev_obj:
            raise HTTPException(404, "Evidence not found")
        ev = _row_to_dict(ev_obj)
        
        exports_result = await session.execute(select(Export).where(Export.evidence_id == evidence_id))
        exports = [_row_to_dict(r) for r in exports_result.scalars().all()]
        
        return {"evidence": ev, "exports": exports}


@router.post("/evidence/{evidence_id}/legal-hold")
async def set_legal_hold(
    evidence_id: str,
    body: LegalHoldIn,
    user: dict = Depends(require_roles("senior_officer")),
    request: Request = None
):
    async with async_session_factory() as session:
        result = await session.execute(select(Evidence).where(Evidence.id == evidence_id))
        ev_obj = result.scalar_one_or_none()
        if not ev_obj:
            raise HTTPException(404, "Evidence not found")
        if ev_obj.legal_hold:
            raise HTTPException(400, "Evidence already on legal hold")
            
        now_str = datetime.now(timezone.utc).isoformat()
        await session.execute(
            update(Evidence)
            .where(Evidence.id == evidence_id)
            .values(
                legal_hold=True,
                legal_hold_reason=body.reason,
                legal_hold_by=user["email"],
                legal_hold_at=now_str
            )
        )
        await session.commit()
        
    await write_audit_v2(
        actor=user,
        action="evidence_legal_hold",
        target=evidence_id,
        target_type="evidence",
        target_id=evidence_id,
        details=body.reason,
        before_values={"legal_hold": False},
        after_values={"legal_hold": True, "reason": body.reason},
        compliance_tags=["LEGAL_HOLD"],
        source_endpoint="POST /api/evidence/{id}/legal-hold",
        request=request
    )
    return {"ok": True}


@router.delete("/evidence/{evidence_id}/legal-hold")
async def release_legal_hold(
    evidence_id: str,
    body: LegalHoldReleaseIn,
    user: dict = Depends(require_roles("senior_officer")),
    request: Request = None
):
    async with async_session_factory() as session:
        result = await session.execute(select(Evidence).where(Evidence.id == evidence_id))
        ev_obj = result.scalar_one_or_none()
        if not ev_obj:
            raise HTTPException(404, "Evidence not found")
        if not ev_obj.legal_hold:
            raise HTTPException(400, "Evidence not on legal hold")
            
        prev_reason = ev_obj.legal_hold_reason
        await session.execute(
            update(Evidence)
            .where(Evidence.id == evidence_id)
            .values(
                legal_hold=False,
                legal_hold_reason=None,
                legal_hold_by=None,
                legal_hold_at=None
            )
        )
        await session.commit()
        
    await write_audit_v2(
        actor=user,
        action="evidence_legal_hold_release",
        target=evidence_id,
        target_type="evidence",
        target_id=evidence_id,
        details=body.reason,
        before_values={"legal_hold": True, "reason": prev_reason},
        after_values={"legal_hold": False},
        compliance_tags=["LEGAL_HOLD"],
        source_endpoint="DELETE /api/evidence/{id}/legal-hold",
        request=request
    )
    return {"ok": True}


@router.post("/evidence/{evidence_id}/direct-export")
async def direct_export(
    evidence_id: str,
    body: DirectExportIn,
    user: dict = Depends(require_roles("senior_officer")),
    request: Request = None
):
    async with async_session_factory() as session:
        result = await session.execute(select(Evidence).where(Evidence.id == evidence_id))
        ev_obj = result.scalar_one_or_none()
        if not ev_obj:
            raise HTTPException(404, "Evidence not found")
        ev = _row_to_dict(ev_obj)
        
        now_str = datetime.now(timezone.utc).isoformat()
        exp_id = str(uuid.uuid4())
        doc = {
            "id": exp_id,
            "evidence_id": evidence_id,
            "evidence_label": ev.get("label", ""),
            "jurisdiction": ev.get("jurisdiction", ""),
            "requested_by": user["email"],
            "requested_at": now_str,
            "justification": body.justification,
            "status": "approved",
            "decision_by": user["email"],
            "decision_at": now_str,
            "decision_comment": "Direct export by Senior Officer"
        }
        session.add(Export(**doc))
        await session.commit()
        
    await write_audit_v2(
        actor=user,
        action="direct_export",
        target=evidence_id,
        target_type="evidence",
        target_id=evidence_id,
        details=body.justification,
        before_values={},
        after_values=doc,
        source_endpoint="POST /api/evidence/{id}/direct-export",
        request=request
    )
    return doc


@router.delete("/evidence/{evidence_id}")
async def delete_evidence(
    evidence_id: str,
    user: dict = Depends(get_current_user),
    request: Request = None,
):
    async with async_session_factory() as session:
        result = await session.execute(select(Evidence).where(Evidence.id == evidence_id))
        ev_obj = result.scalar_one_or_none()
        if not ev_obj:
            raise HTTPException(404, "Evidence not found")
        if ev_obj.legal_hold:
            raise HTTPException(400, "Cannot delete evidence under active legal hold")
        
        ev_dict = _row_to_dict(ev_obj)
        await session.delete(ev_obj)
        await session.commit()

    await write_audit_v2(
        actor=user,
        action="evidence_delete",
        target=evidence_id,
        target_type="evidence",
        target_id=evidence_id,
        details=f"Evidence deleted: {ev_dict.get('label', '')}",
        before_values=ev_dict,
        after_values=None,
        source_endpoint="DELETE /api/evidence/{id}",
        request=request,
    )
    return {"ok": True, "deleted_id": evidence_id}


@router.get("/exports")
async def list_exports(user: dict = Depends(get_current_user)):
    # CRO (former field_supervisor): sees own submitted requests
    # Senior Officer (former supervisory_officer): sees all exports (jurisdiction-wide authority)
    if user["role"] not in ("control_room_officer", "senior_officer"):
        raise HTTPException(403, "Not permitted")
    async with async_session_factory() as session:
        stmt = select(Export)
        if user["role"] == "control_room_officer":
            stmt = stmt.where(Export.requested_by == user["email"])
        # senior_officer: no filter — full jurisdiction-wide access to all exports
        stmt = stmt.order_by(Export.requested_at.desc()).limit(200)
        result = await session.execute(stmt)
        return [_row_to_dict(r) for r in result.scalars().all()]


@router.post("/exports")
async def request_export(
    body: ExportRequestIn,
    user: dict = Depends(get_current_user),
    request: Request = None,
):
    # Control Room Officer inherits the former field_supervisor export-request capability
    if user["role"] != "control_room_officer":
        raise HTTPException(403, "Only Control Room Officer may request exports")
    async with async_session_factory() as session:
        result = await session.execute(
            select(Evidence).where(Evidence.id == body.evidence_id)
        )
        ev_obj = result.scalar_one_or_none()
        if not ev_obj:
            raise HTTPException(404, "Evidence not found")
        ev = _row_to_dict(ev_obj)
        doc = {
            "id": str(uuid.uuid4()),
            "evidence_id": body.evidence_id,
            "evidence_label": ev["label"],
            "jurisdiction": ev["jurisdiction"],
            "requested_by": user["email"],
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "justification": body.justification,
            "status": "pending",
            "decision_by": None,
            "decision_at": None,
            "decision_comment": None,
        }
        session.add(Export(**doc))
        await session.commit()
    await write_audit_v2(
        actor=user, action="export_request",
        target=ev["id"], target_type="evidence",
        target_id=ev["id"], target_name=ev.get("label", ""),
        details=body.justification,
        after_values={"status": "pending", "justification": body.justification, "export_id": doc["id"]},
        source_endpoint="POST /api/exports",
        request=request,
    )
    return doc


@router.post("/exports/{export_id}/decision")
async def decide_export(
    export_id: str, body: ExportDecisionIn,
    user: dict = Depends(require_roles("senior_officer")),
    request: Request = None,
):
    async with async_session_factory() as session:
        result = await session.execute(
            select(Export).where(Export.id == export_id)
        )
        exp_obj = result.scalar_one_or_none()
        if not exp_obj:
            raise HTTPException(404, "Export not found")
        # Capture before state for diff
        before_status = exp_obj.status
        # Senior Officer has jurisdiction-wide authority — no jurisdiction restriction
        await session.execute(
            update(Export)
            .where(Export.id == export_id)
            .values(
                status=body.decision,
                decision_by=user["email"],
                decision_at=datetime.now(timezone.utc).isoformat(),
                decision_comment=body.comment,
            )
        )
        await session.commit()
    await write_audit_v2(
        actor=user, action=f"export_{body.decision}",
        target=export_id, target_type="export",
        target_id=export_id,
        details=body.comment,
        before_values={"status": before_status},
        after_values={"status": body.decision, "decision_by": user["email"], "comment": body.comment},
        source_endpoint="POST /api/exports/{id}/decision",
        request=request,
    )
    return {"ok": True}


@router.get("/exports/{export_id}/bundle")
async def download_65b_bundle(export_id: str, request: Request, token: str = ""):
    # Support Authorization header, cookie, OR query token for direct <a> downloads
    if token:
        try:
            options = {"verify_aud": False}
            payload = jwt.decode(token, get_jwks(), algorithms=["RS256"], options=options)
            # Extract real role from JWT claims instead of hardcoding
            realm_roles = payload.get("realm_access", {}).get("roles", [])
            role = "control_room_officer"  # default
            for r in ("senior_officer", "system_administrator", "control_room_officer", "drone_operator"):
                if r in realm_roles:
                    role = r
                    break
            user = {
                "id": payload.get("sub"),
                "email": payload.get("email", ""),
                "role": role,
            }
        except jwt.JWTError:
            raise HTTPException(401, "Invalid token")
    else:
        user = await get_current_user(request)

    async with async_session_factory() as session:
        result = await session.execute(
            select(Export).where(Export.id == export_id)
        )
        exp_obj = result.scalar_one_or_none()
    if not exp_obj:
        raise HTTPException(404, "Export not found")
    exp = _row_to_dict(exp_obj)
    if exp["status"] != "approved":
        raise HTTPException(400, "Only approved exports can be bundled")
    # Scope: CRO (former field_supervisor) who raised the request, Senior Officer (full authority)
    role = user["role"]
    if role == "control_room_officer" and exp["requested_by"] != user["email"]:
        raise HTTPException(403, "Not your request")
    if role not in ("control_room_officer", "senior_officer"):
        raise HTTPException(403, "Not permitted")

    async with async_session_factory() as session:
        result = await session.execute(
            select(Evidence).where(Evidence.id == exp["evidence_id"])
        )
        ev_obj = result.scalar_one_or_none()
    if not ev_obj:
        raise HTTPException(404, "Evidence missing")
    ev = _row_to_dict(ev_obj)

    pdf_bytes = _make_65b_pdf(ev, exp, exp.get("decision_by", "-"))
    metadata = {
        "evidence": ev,
        "export": exp,
        "bundle_generated_at": datetime.now(timezone.utc).isoformat(),
        "downloaded_by": user["email"],
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{exp['id']}_certificate_65B.pdf", pdf_bytes)
        z.writestr("metadata.json", json.dumps(metadata, indent=2, default=str))
        # Include real evidence images from MinIO instead of fake video_url.txt
        minio_client = _get_minio_client()
        image_count = 0
        for key_field, bucket_fallback, filename in [
            ("primary_image_key", ev.get("image_bucket", "dvas-faces"), "evidence_image_1.jpg"),
            ("thumbnail", ev.get("image_bucket", "dvas-faces"), "evidence_thumbnail.jpg"),
            ("secondary_image_key", "dvas-plates", "evidence_image_2.jpg"),
        ]:
            img_key = ev.get(key_field, "")
            if not img_key or img_key.startswith("http"):
                continue
            bucket = bucket_fallback or "dvas-faces"
            if minio_client:
                try:
                    resp_obj = minio_client.get_object(bucket, img_key)
                    img_data = resp_obj.read()
                    resp_obj.close()
                    resp_obj.release_conn()
                    z.writestr(filename, img_data)
                    image_count += 1
                except Exception:
                    pass
        if image_count == 0:
            z.writestr(
                "evidence_note.txt",
                "No image evidence available for this event.\n"
                "The event was recorded without visual capture, or the image has expired from storage.\n",
            )
        z.writestr(
            "README.txt",
            f"DVAS Section 65B Evidence Bundle\nExport {exp['id']}\nEvidence {ev['id']}\n"
            f"Approved by {exp.get('decision_by', '-')} on {exp.get('decision_at', '-')}\n"
            f"Evidence images included: {image_count}\n",
        )
    buf.seek(0)
    await write_audit_v2(
        actor=user,
        action="bundle_download",
        target=exp["id"],
        target_type="export",
        target_id=exp["id"],
        target_name=exp.get("evidence_label", ""),
        details=f"65B bundle for {ev['id']}",
        source_endpoint="GET /api/exports/{id}/bundle",
        request=request,
    )
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{exp["id"]}_65B_bundle.zip"'
        },
    )
