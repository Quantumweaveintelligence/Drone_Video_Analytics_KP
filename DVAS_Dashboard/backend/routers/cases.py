"""
Cases router (evidence plan E4): group evidence under a case number, then export
the case as one bundle (POST /api/exports with case_id).
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from audit_service import write_audit_v2
from core.security import _row_to_dict, get_current_user, require_roles
from database import async_session_factory
from models import Case, CaseEvidence, Evidence, Export
from routers.alerts import _case_visible, _scope_evidence
from schemas.cases import CaseCreateIn, CaseEvidenceIn

router = APIRouter(prefix="/api", tags=["Cases"])

CASE_ROLES = ("control_room_officer", "senior_officer")


async def _visible_case(session, case_id: str, user: dict) -> Case:
    case = await session.get(Case, case_id)
    if case is None or not _case_visible(case, user):
        raise HTTPException(404, "Case not found")      # no existence leak
    return case


@router.post("/cases", status_code=201)
async def create_case(body: CaseCreateIn, user: dict = Depends(require_roles(*CASE_ROLES)),
                      request: Request = None):
    number = body.case_number.strip()
    jurisdiction = body.jurisdiction if user["role"] == "senior_officer" and body.jurisdiction \
        else (user.get("jurisdiction") or body.jurisdiction)
    async with async_session_factory() as session:
        if (await session.execute(select(Case.id).where(Case.case_number == number))).first():
            raise HTTPException(409, f"Case {number} already exists")
        case = Case(id=f"CASE-{uuid.uuid4().hex[:10]}", case_number=number, fir_number=body.fir_number,
                    police_station=body.police_station, title=body.title, jurisdiction=jurisdiction,
                    created_by=user["email"], created_at=datetime.now(timezone.utc).isoformat(), status="open")
        session.add(case)
        await session.commit()
        doc = _row_to_dict(case)
    await write_audit_v2(actor=user, action="case_create", category="evidence", target=doc["id"],
                         target_type="case", target_id=doc["id"], target_name=number,
                         after_values=doc, source_endpoint="POST /api/cases", request=request)
    return doc


@router.get("/cases")
async def list_cases(user: dict = Depends(require_roles(*CASE_ROLES, "system_administrator"))):
    async with async_session_factory() as session:
        rows = (await session.execute(select(Case).order_by(Case.created_at.desc()).limit(500))).scalars().all()
        return [_row_to_dict(c) for c in rows if _case_visible(c, user)]


@router.get("/cases/{case_id}")
async def get_case(case_id: str, user: dict = Depends(require_roles(*CASE_ROLES, "system_administrator"))):
    async with async_session_factory() as session:
        case = await _visible_case(session, case_id, user)
        links = (await session.execute(
            select(CaseEvidence, Evidence).join(Evidence, Evidence.id == CaseEvidence.evidence_id)
            .where(CaseEvidence.case_id == case_id).order_by(CaseEvidence.added_at))).all()
        exports = (await session.execute(select(Export).where(Export.case_id == case_id)
                                         .order_by(Export.requested_at.desc()))).scalars().all()
        return {
            "case": _row_to_dict(case),
            "evidence": [dict(_row_to_dict(ev), added_by=link.added_by, added_at=link.added_at)
                         for link, ev in links],
            "exports": [_row_to_dict(x) for x in exports],
        }


async def _case_locked(session, case_id: str) -> bool:
    """A case with a pending or approved export keeps its evidence list."""
    return (await session.execute(select(Export.id).where(
        Export.case_id == case_id, Export.status.in_(("pending", "approved"))))).first() is not None


@router.post("/cases/{case_id}/evidence", status_code=201)
async def add_case_evidence(case_id: str, body: CaseEvidenceIn,
                            user: dict = Depends(require_roles(*CASE_ROLES)), request: Request = None):
    async with async_session_factory() as session:
        case = await _visible_case(session, case_id, user)
        if case.status != "open":
            raise HTTPException(409, "Case is closed")
        ev = (await session.execute(_scope_evidence(select(Evidence), user)
                                    .where(Evidence.id == body.evidence_id))).scalar_one_or_none()
        if ev is None:
            raise HTTPException(404, "Evidence not found")
        if ev.status == "disposed":
            raise HTTPException(409, "Disposed evidence cannot be added to a case")
        if await session.get(CaseEvidence, (case_id, ev.id)):
            raise HTTPException(409, "Evidence already in this case")
        if await _case_locked(session, case_id):
            raise HTTPException(409, "Case has a pending or approved export; create a new case")
        session.add(CaseEvidence(case_id=case_id, evidence_id=ev.id, added_by=user["email"],
                                 added_at=datetime.now(timezone.utc).isoformat()))
        await session.commit()
    await write_audit_v2(actor=user, action="case_evidence_add", category="evidence", target=case_id,
                         target_type="case", target_id=case_id, target_name=case.case_number,
                         details=f"Added {ev.id}", after_values={"evidence_id": ev.id,
                                                                 "record_sha256": ev.record_sha256},
                         source_endpoint="POST /api/cases/{id}/evidence", request=request)
    return {"ok": True, "case_id": case_id, "evidence_id": ev.id}


@router.delete("/cases/{case_id}/evidence/{evidence_id}")
async def remove_case_evidence(case_id: str, evidence_id: str,
                               user: dict = Depends(require_roles(*CASE_ROLES)), request: Request = None):
    async with async_session_factory() as session:
        case = await _visible_case(session, case_id, user)
        link = await session.get(CaseEvidence, (case_id, evidence_id))
        if link is None:
            raise HTTPException(404, "Evidence not in this case")
        if await _case_locked(session, case_id):
            raise HTTPException(409, "Case has a pending or approved export")
        await session.delete(link)
        await session.commit()
    await write_audit_v2(actor=user, action="case_evidence_remove", category="evidence", target=case_id,
                         target_type="case", target_id=case_id, target_name=case.case_number,
                         details=f"Removed {evidence_id}", source_endpoint="DELETE /api/cases/{id}/evidence/{eid}",
                         request=request)
    return {"ok": True}


@router.post("/cases/{case_id}/close")
async def close_case(case_id: str, user: dict = Depends(require_roles("senior_officer")),
                     request: Request = None):
    async with async_session_factory() as session:
        case = await _visible_case(session, case_id, user)
        if case.status == "closed":
            raise HTTPException(409, "Case already closed")
        case.status = "closed"
        await session.commit()
    await write_audit_v2(actor=user, action="case_close", category="evidence", target=case_id,
                         target_type="case", target_id=case_id, target_name=case.case_number,
                         before_values={"status": "open"}, after_values={"status": "closed"},
                         source_endpoint="POST /api/cases/{id}/close", request=request)
    return {"ok": True}
