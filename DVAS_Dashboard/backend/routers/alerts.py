import io
import json
import uuid
import zipfile
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update
from jose import jwt

from database import async_session_factory
from models import Alert, Evidence, Export
from core.security import get_current_user, get_jwks, scope_filter_for, _apply_scope_to_query, _row_to_dict
from audit_service import write_audit_v2
from schemas.exports import ExportRequestIn, ExportDecisionIn
from services.pdf_service import _make_65b_pdf

logger = logging.getLogger("dvas")
router = APIRouter(prefix="/api", tags=["Alerts, Evidence & Exports"])


@router.get("/alerts")
async def list_alerts(user: dict = Depends(get_current_user)):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    f = scope_filter_for(user)
    if "__deny__" in f:
        raise HTTPException(403, "Not permitted")
    async with async_session_factory() as session:
        stmt = select(Alert)
        stmt = _apply_scope_to_query(stmt, Alert, f)
        if stmt is None:
            raise HTTPException(403, "Not permitted")
        stmt = stmt.order_by(Alert.ts.desc()).limit(200)
        result = await session.execute(stmt)
        return [_row_to_dict(r) for r in result.scalars().all()]


@router.post("/alerts/{alert_id}/ack")
async def ack_alert(
    alert_id: str, user: dict = Depends(get_current_user), request: Request = None
):
    # CRO: inherits field_supervisor ack capability; SR: inherits supervisory_officer ack capability
    if user["role"] not in ("control_room_officer", "senior_officer"):
        raise HTTPException(403, "Not permitted")
    async with async_session_factory() as session:
        result = await session.execute(
            update(Alert)
            .where(Alert.id == alert_id)
            .values(
                acknowledged_by=user["email"],
                acknowledged_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        if result.rowcount == 0:
            raise HTTPException(404, "Alert not found")
        await session.commit()
    await write_audit_v2(
        actor=user, action="alert_ack",
        target=alert_id, target_type="alert",
        target_id=alert_id,
        details="acknowledged",
        before_values={"acknowledged_by": None},
        after_values={"acknowledged_by": user["email"]},
        source_endpoint="POST /api/alerts/{id}/ack",
        request=request,
    )
    return {"ok": True}


@router.get("/evidence")
async def list_evidence(user: dict = Depends(get_current_user)):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    f = scope_filter_for(user)
    if "__deny__" in f:
        raise HTTPException(403, "Not permitted")
    async with async_session_factory() as session:
        stmt = select(Evidence)
        stmt = _apply_scope_to_query(stmt, Evidence, f)
        if stmt is None:
            raise HTTPException(403, "Not permitted")
        stmt = stmt.order_by(Evidence.ts.desc()).limit(200)
        result = await session.execute(stmt)
        return [_row_to_dict(r) for r in result.scalars().all()]


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
    export_id: str, body: ExportDecisionIn, user: dict = Depends(get_current_user),
    request: Request = None,
):
    # Senior Officer inherits full export approve/reject authority (former supervisory_officer)
    if user["role"] != "senior_officer":
        raise HTTPException(403, "Only Senior Officer may approve/reject exports")
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
            user = {
                "id": payload.get("sub"),
                "email": payload.get("email", ""),
                "role": "control_room_officer", # Assuming higher role for exports
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
        z.writestr(
            "video_url.txt",
            f"Original clip URL (secure, time-limited):\n{ev['video_url']}\n\n"
            "Actual video file bytes are streamed from the DVAS evidence vault under "
            "separate signed session and are not embedded in this bundle for size reasons.",
        )
        z.writestr(
            "README.txt",
            f"DVAS Section 65B Evidence Bundle\nExport {exp['id']}\nEvidence {ev['id']}\n"
            f"Approved by {exp.get('decision_by', '-')} on {exp.get('decision_at', '-')}\n",
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
