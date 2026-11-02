"""
Reports router (evidence plan R2–R5).

* GET  /api/reports/types                    — report types this role may generate / request
* POST /api/reports/generate                 — senior: queued; CRO: requested (a senior approves)
* POST /api/reports/{id}/decision            — senior approves / rejects a CRO request
* GET  /api/reports, /api/reports/{id}       — senior: all; CRO: own
* GET  /api/reports/{id}/download            — stored PDF, hash-checked (signed links: /api/downloads)
* GET  /api/reports/{type}/preview-data      — the same data the PDF is built from
Generation runs in the report worker (services/report_jobs.py), not in the request.
"""
import asyncio
import io
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, select

from audit_service import write_audit_v2
from core.analytics_client import _get_minio_client
from core.security import _row_to_dict, get_current_user, require_roles
from database import async_session_factory
from models import Report
from schemas.reports import ReportDecisionIn, ReportGenerateIn
from services.evidence_integrity import sha256_hex
from services.report_data import ReportInputError, normalize_params
from services.report_types import GENERATORS, REPORT_TYPES, REQUESTERS, public_catalog, title_for

router = APIRouter(prefix="/api", tags=["Reports"])
logger = logging.getLogger("dvas")

REPORT_ROLES = GENERATORS + REQUESTERS


@router.get("/reports/types")
async def report_types(user: dict = Depends(require_roles(*REPORT_ROLES))):
    return public_catalog(user["role"])


@router.post("/reports/generate")
async def generate_report(
    body: ReportGenerateIn,
    user: dict = Depends(require_roles(*REPORT_ROLES)),
    request: Request = None,
):
    raw = dict(body.params or {})
    if getattr(body, "__pydantic_extra__", None):
        raw.update(body.__pydantic_extra__)
    try:
        params = normalize_params(body.report_type, raw, user)
    except ReportInputError as exc:
        raise HTTPException(400, str(exc))

    # Validate the target now, so a bad or out-of-scope mission / case fails here, not in the worker
    spec = REPORT_TYPES[body.report_type]
    if spec["period"] in ("mission", "case"):
        async with async_session_factory() as session:
            try:
                await spec["collect"](session, params)
            except ReportInputError as exc:
                raise HTTPException(404, str(exc))

    status = "queued" if user["role"] in GENERATORS else "requested"
    report_id = f"RPT-{uuid.uuid4().hex[:10]}"
    officer = user.get("name") or user.get("email", "")
    row = Report(
        id=report_id, report_type=body.report_type, title=title_for(body.report_type, params),
        requested_by=user["email"], requested_at=datetime.now(timezone.utc).isoformat(),
        jurisdiction=user.get("jurisdiction"), status=status, params=params,
        watermark_text=f"CONFIDENTIAL — {officer} ({user['email']})", attempts=0,
    )
    async with async_session_factory() as session:
        session.add(row)
        await session.commit()
    await write_audit_v2(
        actor=user, action="report_generate" if status == "queued" else "report_request",
        target=report_id, target_type="report", target_id=report_id, target_name=row.title,
        details=f"Type: {body.report_type}", after_values={"status": status, "params": params},
        source_endpoint="POST /api/reports/generate", request=request,
    )
    return {"id": report_id, "status": status, "title": row.title}


@router.post("/reports/{report_id}/decision")
async def decide_report(report_id: str, body: ReportDecisionIn,
                        user: dict = Depends(require_roles(*GENERATORS)), request: Request = None):
    async with async_session_factory() as session:
        row = await session.get(Report, report_id)
        if row is None:
            raise HTTPException(404, "Report not found")
        if row.status != "requested":
            raise HTTPException(409, f"Report is {row.status}")
        row.status = "queued" if body.decision == "approved" else "rejected"
        row.decided_by = user["email"]
        row.decided_at = datetime.now(timezone.utc).isoformat()
        if body.decision == "rejected":
            row.error_message = body.comment or "rejected"
        await session.commit()
        doc = _row_to_dict(row)
    await write_audit_v2(actor=user, action=f"report_{body.decision}", target=report_id, target_type="report",
                         target_id=report_id, details=body.comment, after_values={"status": doc["status"]},
                         source_endpoint="POST /api/reports/{id}/decision", request=request)
    return doc


def _visible(rep: dict, user: dict) -> bool:
    return user["role"] in ("senior_officer", "system_administrator") or rep["requested_by"] == user["email"]


@router.get("/reports")
async def list_reports(user: dict = Depends(require_roles(*REPORT_ROLES))):
    async with async_session_factory() as session:
        stmt = select(Report)
        if user["role"] == "control_room_officer":
            stmt = stmt.where(Report.requested_by == user["email"])
        rows = (await session.execute(stmt.order_by(desc(Report.requested_at)).limit(200))).scalars().all()
        return [_row_to_dict(r) for r in rows]


@router.get("/reports/{report_id}")
async def get_report(report_id: str, user: dict = Depends(require_roles(*REPORT_ROLES))):
    async with async_session_factory() as session:
        r_obj = await session.get(Report, report_id)
    if not r_obj:
        raise HTTPException(404, "Report not found")
    rep = _row_to_dict(r_obj)
    if not _visible(rep, user):
        raise HTTPException(404, "Report not found")
    return rep


@router.get("/reports/{report_id}/download")
async def download_report(report_id: str, request: Request, user: dict = Depends(get_current_user)):
    return await stream_report(report_id, user, request)


def _read_pdf(rep: dict) -> bytes:
    client = _get_minio_client()
    if client is None:
        raise RuntimeError("MinIO client not available")
    # Reports made before R4 live in dvas-evidence/reports/…
    bucket = rep.get("file_bucket") or "dvas-evidence"
    resp = client.get_object(bucket, rep["file_key"])
    try:
        return resp.read()
    finally:
        resp.close()
        resp.release_conn()


async def stream_report(report_id: str, user: dict, request) -> StreamingResponse:
    if user.get("role") not in ("senior_officer", "system_administrator", "control_room_officer"):
        raise HTTPException(403, "Not permitted")
    async with async_session_factory() as session:
        r_obj = await session.get(Report, report_id)
    if not r_obj:
        raise HTTPException(404, "Report not found")
    rep = _row_to_dict(r_obj)
    if not _visible(rep, user):
        raise HTTPException(404, "Report not found")
    if rep["status"] != "ready" or not rep.get("file_key"):
        raise HTTPException(400, "Report is not ready")
    try:
        pdf_bytes = await asyncio.to_thread(_read_pdf, rep)
    except Exception:
        raise HTTPException(404, "Report file not found")

    digest = sha256_hex(pdf_bytes)
    if rep.get("sha256") and rep["sha256"] != digest:
        await write_audit_v2(actor=user, action="report_download", severity="critical", target=report_id,
                             target_type="report", target_id=report_id, status="failure",
                             details="Stored report does not match its SHA-256", request=request)
        raise HTTPException(409, "Stored report failed its integrity check")
    await write_audit_v2(
        actor=user, action="report_download", target=report_id, target_type="report", target_id=report_id,
        details=f"Downloaded PDF {digest}", after_values={"sha256": digest},
        source_endpoint="GET /api/reports/{id}/download", request=request,
    )
    return StreamingResponse(
        io.BytesIO(pdf_bytes), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{report_id}.pdf"',
                 "X-Content-SHA256": digest, "Cache-Control": "private, no-store"},
    )


@router.get("/reports/{report_type}/preview-data")
async def preview_report_data(report_type: str, request: Request,
                              user: dict = Depends(require_roles(*REPORT_ROLES))):
    """The data the PDF would be built from (same function, same numbers)."""
    if report_type not in REPORT_TYPES:
        raise HTTPException(400, "Unknown report type")
    try:
        params = normalize_params(report_type, dict(request.query_params), user)
        async with async_session_factory() as session:
            data = await REPORT_TYPES[report_type]["collect"](session, params)
    except ReportInputError as exc:
        raise HTTPException(400, str(exc))
    return {"params": params, "title": title_for(report_type, params), "data": data}
