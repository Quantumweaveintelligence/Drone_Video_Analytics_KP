import asyncio
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select, update, func, or_, distinct, case, false

from database import async_session_factory
from models import Alert, Case, CaseEvidence, Evidence, Export
from core.security import get_current_user, scope_filter_for, _apply_scope_to_query, _row_to_dict, require_roles
from audit_service import write_audit_v2
from schemas.exports import ExportRequestIn, ExportDecisionIn
from schemas.evidence import AckIn, LegalHoldIn, LegalHoldReleaseIn, DirectExportIn, DisposeIn
from core.config import PII_VIEW_ROLES
from services import evidence_vault
from services.evidence_integrity import record_sha256
from services.export_bundle import BundleIntegrityError
from services.exports import ExportError, finalize_export, read_bundle

logger = logging.getLogger("dvas")
router = APIRouter(prefix="/api", tags=["Alerts, Evidence & Exports"])


ALERT_CATEGORIES = ("ai", "manual", "incident", "suggestion")


@router.get("/alerts")
async def list_alerts(category: str = "ai,manual", user: dict = Depends(get_current_user)):
    """Alerts feed. Default: AI detections + manual alerts. Incidents and suggestions have their
    own endpoints; their pre-R1 copies here are category incident/suggestion (B16)."""
    role = user.get("role")
    cats = [c for c in (x.strip() for x in category.split(",")) if c in ALERT_CATEGORIES] or ["ai", "manual"]
    cat_filter = or_(Alert.category.in_(cats), Alert.category.is_(None)) if "manual" in cats \
        else Alert.category.in_(cats)
    async with async_session_factory() as session:
        base = select(Alert).where(cat_filter)
        if role in ("system_administrator", "senior_officer"):
            stmt = base.order_by(Alert.ts.desc()).limit(200)
        elif role == "control_room_officer":
            f = scope_filter_for(user)
            jur = f.get("jurisdiction") if "__deny__" not in f else None
            if jur:
                stmt = base.where(or_(
                    Alert.jurisdiction == jur,
                    Alert.jurisdiction == "Kerala",
                    Alert.jurisdiction == "State HQ",
                    Alert.jurisdiction.is_(None)
                )).order_by(Alert.ts.desc()).limit(200)
            else:
                stmt = base.order_by(Alert.ts.desc()).limit(200)
        elif role == "drone_operator":
            scope = user.get("mission_scope") or []
            stmt = base.where(Alert.mission_id.in_(scope) if scope else false()) \
                .order_by(Alert.ts.desc()).limit(100)
        else:
            raise HTTPException(403, "Not permitted")
        result = await session.execute(stmt)
        return [_row_to_dict(r) for r in result.scalars().all()]


@router.post("/alerts/{alert_id}/ack")
async def ack_alert(
    alert_id: str, body: Optional[AckIn] = Body(default=None),
    user: dict = Depends(get_current_user), request: Request = None
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
                # R1: the basis for the detection-quality report
                **({"disposition": body.disposition, "disposition_note": body.note} if body else {}),
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
        details="acknowledged" + (f" ({body.disposition})" if body and body.disposition else ""),
        before_values={"acknowledged_by": None},
        after_values={"acknowledged_by": user.get("email") or user.get("name"),
                      "disposition": body.disposition if body else None},
        source_endpoint="POST /api/alerts/{id}/ack",
        request=request,
    )
    return {"ok": True}


PERSON_EVENT_TYPES = ["person_detected", "perimeter_intrusion", "intrusion_alert", "intrusion_breach",
                      "face_watchlist_match"]
VEHICLE_EVENT_TYPES = ["vehicle_detected", "vehicle_anomaly", "wrong_way", "theft_alert", "vessel_detected", "vessel_proximity", "vessel_loitering", "proximity_alert"]
CROWD_EVENT_TYPES = ["crowd_threshold", "crowd_surge", "unattended_object"]


def _case_visible(case, user: dict) -> bool:
    """Case access by jurisdiction (E4): seniors/admins all; CROs their jurisdiction or statewide."""
    if user.get("role") in ("senior_officer", "system_administrator"):
        return True
    if user.get("role") == "control_room_officer":
        jur = user.get("jurisdiction")
        return not jur or case.jurisdiction in (jur, None, "", "Kerala", "State HQ")
    return False


def _scope_evidence(stmt, user: dict):
    """Restrict an Evidence select to what the caller may see (B8).

    Applied before any drone/mission filter, so filters can only narrow the scope.
    Operators see only their mission_scope; an empty scope sees nothing.
    """
    role = user.get("role")
    if role in ("system_administrator", "senior_officer"):
        return stmt  # State-wide aggregate access
    if role == "control_room_officer":
        jur = user.get("jurisdiction")
        if not jur:
            return stmt
        return stmt.where(or_(
            Evidence.jurisdiction == jur,
            Evidence.jurisdiction.is_(None),
            Evidence.jurisdiction == "",
            Evidence.jurisdiction.in_(["Kerala", "State HQ", "Thiruvananthapuram"])
        ))
    if role == "drone_operator":
        scope = user.get("mission_scope") or []
        return stmt.where(Evidence.mission_id.in_(scope)) if scope else stmt.where(false())
    f = scope_filter_for(user)
    stmt = _apply_scope_to_query(stmt, Evidence, f)
    if stmt is None:
        raise HTTPException(403, "Not permitted")
    return stmt


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
    include_disposed: bool = False,
    user: dict = Depends(get_current_user)
):
    async with async_session_factory() as session:
        stmt = _scope_evidence(select(Evidence), user)
        if not (include_disposed and user.get("role") in ("senior_officer", "system_administrator")):
            stmt = stmt.where(Evidence.status != "disposed")

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
    def scoped(*cols):
        return _scope_evidence(select(*cols), user).where(Evidence.status != "disposed")

    async with async_session_factory() as session:
        drones = (await session.execute(
            scoped(distinct(Evidence.drone_id)).where(Evidence.drone_id != "", Evidence.drone_id.is_not(None))
        )).scalars().all()
        missions = (await session.execute(
            scoped(distinct(Evidence.mission_id)).where(Evidence.mission_id != "", Evidence.mission_id.is_not(None))
        )).scalars().all()
        types = (await session.execute(
            scoped(distinct(Evidence.event_type)).where(Evidence.event_type != "", Evidence.event_type.is_not(None))
        )).scalars().all()

        dedup_key = case(
            (func.nullif(Evidence.primary_image_key, '') != None, Evidence.primary_image_key),
            else_=Evidence.id
        )
        n_all, n_dedup = func.count(Evidence.id), func.count(distinct(dedup_key))

        counts = {
            "all": (await session.scalar(scoped(n_all))) or 0,
            "all_dedup": (await session.scalar(scoped(n_dedup))) or 0,
        }
        for name, types_ in (("person", PERSON_EVENT_TYPES), ("vehicle", VEHICLE_EVENT_TYPES),
                             ("crowd", CROWD_EVENT_TYPES)):
            in_cat = Evidence.event_type.in_(types_)
            counts[name] = (await session.scalar(scoped(n_all).where(in_cat))) or 0
            counts[f"{name}_dedup"] = (await session.scalar(scoped(n_dedup).where(in_cat))) or 0

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
        result = await session.execute(
            _scope_evidence(select(Evidence), user).where(Evidence.id == evidence_id)
        )
        ev_obj = result.scalar_one_or_none()
        if not ev_obj:
            raise HTTPException(404, "Evidence not found")
        ev = _row_to_dict(ev_obj)
        
        exports_result = await session.execute(select(Export).where(Export.evidence_id == evidence_id))
        exports = [_row_to_dict(r) for r in exports_result.scalars().all()]
        
        return {"evidence": ev, "exports": exports}


async def _load_evidence(session, evidence_id: str) -> Evidence:
    ev_obj = (await session.execute(select(Evidence).where(Evidence.id == evidence_id))).scalar_one_or_none()
    if not ev_obj:
        raise HTTPException(404, "Evidence not found")
    return ev_obj


async def _vault_hold(ev_obj: Evidence, on: bool) -> None:
    """Apply/clear the S3 legal hold on the vault copies first; refuse if MinIO refuses (E2)."""
    objects = [o for o in (ev_obj.objects or []) if o.get("bucket") == evidence_vault.VAULT]
    if not objects:
        return
    try:
        failures = await asyncio.to_thread(evidence_vault.set_legal_hold, objects, on)
    except evidence_vault.VaultUnavailable:
        failures = len(objects)
    if failures:
        raise HTTPException(502, "Legal hold could not be applied to the stored evidence objects; nothing changed")


@router.post("/evidence/{evidence_id}/legal-hold")
async def set_legal_hold(
    evidence_id: str,
    body: LegalHoldIn,
    user: dict = Depends(require_roles("senior_officer")),
    request: Request = None
):
    async with async_session_factory() as session:
        ev_obj = await _load_evidence(session, evidence_id)
        if ev_obj.legal_hold:
            raise HTTPException(400, "Evidence already on legal hold")
        if ev_obj.status == "disposed":
            raise HTTPException(409, "Evidence has been disposed")
        await _vault_hold(ev_obj, True)

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
        ev_obj = await _load_evidence(session, evidence_id)
        if not ev_obj.legal_hold:
            raise HTTPException(400, "Evidence not on legal hold")
        await _vault_hold(ev_obj, False)

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


async def _finalize_or_error(export_id: str, user: dict, request) -> dict:
    """Build the bundle; on failure keep the approval and report the problem."""
    try:
        return await finalize_export(export_id, user, request=request)
    except ExportError as exc:
        async with async_session_factory() as session:
            exp = await session.get(Export, export_id)
            doc = _row_to_dict(exp) if exp else {"id": export_id}
        doc["bundle_error"] = exc.detail
        return doc


@router.post("/evidence/{evidence_id}/direct-export")
async def direct_export(
    evidence_id: str,
    body: DirectExportIn,
    user: dict = Depends(require_roles("senior_officer")),
    request: Request = None
):
    async with async_session_factory() as session:
        ev_obj = await _load_evidence(session, evidence_id)
        if ev_obj.status == "disposed":
            raise HTTPException(409, "Evidence has been disposed")
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
            "decision_comment": "Direct export by Senior Officer",
            "case_number": body.case_number.strip(),
            "evidence_ids": [evidence_id],
            "self_approved": True,          # E4: shown as such in the certificate and the UI
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
        compliance_tags=["BSA_63"],
        source_endpoint="POST /api/evidence/{id}/direct-export",
        request=request
    )
    return await _finalize_or_error(exp_id, user, request)


def _retention_expired(retention_until: Optional[str]) -> bool:
    if not retention_until:
        return False
    try:
        until = datetime.fromisoformat(str(retention_until).replace("Z", "+00:00"))
    except ValueError:
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until < datetime.now(timezone.utc)


@router.delete("/evidence/{evidence_id}")
async def delete_evidence(
    evidence_id: str,
    body: Optional[DisposeIn] = Body(default=None),
    user: dict = Depends(require_roles("senior_officer")),
    request: Request = None,
):
    """Dispose of evidence (E2): status change + audit. The vault objects stay until their
    object-lock retention ends; the retention job removes them then. No hard delete here."""
    reason = body.reason.strip() if body else ""
    async with async_session_factory() as session:
        ev_obj = await _load_evidence(session, evidence_id)
        if ev_obj.legal_hold:
            raise HTTPException(400, "Cannot delete evidence under active legal hold")
        if ev_obj.status == "disposed":
            raise HTTPException(409, "Evidence already disposed")
        if not reason and not _retention_expired(ev_obj.retention_until):
            raise HTTPException(409, "Retention period not over; a disposal reason is required")
        in_open_case = (await session.execute(
            select(CaseEvidence.case_id).join(Case, Case.id == CaseEvidence.case_id)
            .where(CaseEvidence.evidence_id == evidence_id, Case.status == "open")
        )).first()
        if in_open_case:
            raise HTTPException(409, f"Evidence is part of open case {in_open_case[0]}")

        ev_dict = _row_to_dict(ev_obj)
        ev_obj.status = "disposed"
        ev_obj.disposed_at = datetime.now(timezone.utc).isoformat()
        ev_obj.disposed_by = user["email"]
        ev_obj.disposal_reason = reason or "retention expired"
        await session.commit()

    await write_audit_v2(
        actor=user,
        action="evidence_delete",
        target=evidence_id,
        target_type="evidence",
        target_id=evidence_id,
        details=f"Evidence disposed: {ev_dict.get('label', '')}"
                + (f" — reason: {reason}" if reason else " — retention expired"),
        before_values=ev_dict,
        after_values={"status": "disposed", "disposal_reason": reason or "retention expired"},
        source_endpoint="DELETE /api/evidence/{id}",
        request=request,
    )
    return {"ok": True, "deleted_id": evidence_id, "status": "disposed"}


@router.get("/evidence/{evidence_id}/verify")
async def verify_evidence(
    evidence_id: str,
    user: dict = Depends(require_roles("senior_officer", "system_administrator")),
    request: Request = None,
):
    """E3: re-hash the stored objects and the record; store and audit the result."""
    async with async_session_factory() as session:
        ev_obj = await _load_evidence(session, evidence_id)
        objects = list(ev_obj.objects or [])
        try:
            results = await asyncio.to_thread(evidence_vault.verify_objects, objects)
        except evidence_vault.VaultUnavailable:
            raise HTTPException(503, "Evidence storage unavailable")
        record_ok = (ev_obj.record_sha256 is None
                     or record_sha256(_row_to_dict(ev_obj)) == ev_obj.record_sha256)
        if not objects:
            integrity = "unverified"
        elif all(r["ok"] for r in results) and record_ok:
            integrity = "ok"
        elif any(r.get("error") for r in results):
            integrity = "missing"
        else:
            integrity = "mismatch"
        ev_obj.integrity = integrity
        ev_obj.integrity_checked_at = datetime.now(timezone.utc).isoformat()
        await session.commit()
    await write_audit_v2(
        actor=user, action="evidence_verify", category="evidence",
        severity="critical" if integrity in ("mismatch", "missing") else "info",
        target=evidence_id, target_type="evidence", target_id=evidence_id,
        details=f"Integrity check: {integrity}",
        after_values={"integrity": integrity, "record_ok": record_ok,
                      "objects": [{k: r.get(k) for k in ("role", "key", "ok", "actual")} for r in results]},
        source_endpoint="GET /api/evidence/{id}/verify", request=request,
    )
    return {"evidence_id": evidence_id, "integrity": integrity, "record_ok": record_ok, "objects": results}


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
        case = None
        if body.case_id:
            case = await session.get(Case, body.case_id)
            if case is None or not _case_visible(case, user):
                raise HTTPException(404, "Case not found")
            if case.status != "open":
                raise HTTPException(409, "Case is closed")
            n_items = (await session.execute(
                select(func.count()).select_from(CaseEvidence).where(CaseEvidence.case_id == case.id))).scalar()
            if not n_items:
                raise HTTPException(409, "Case has no evidence")
            target_id, label, jurisdiction = case.id, f"Case {case.case_number}", case.jurisdiction
        else:
            result = await session.execute(
                _scope_evidence(select(Evidence), user).where(Evidence.id == body.evidence_id)
            )
            ev_obj = result.scalar_one_or_none()
            if not ev_obj:
                raise HTTPException(404, "Evidence not found")
            if ev_obj.status == "disposed":
                raise HTTPException(409, "Evidence has been disposed")
            target_id, label, jurisdiction = ev_obj.id, ev_obj.label, ev_obj.jurisdiction
        doc = {
            "id": str(uuid.uuid4()),
            "evidence_id": body.evidence_id,
            "case_id": body.case_id,
            "case_number": (case.case_number if case else (body.case_number or None)),
            "evidence_label": label,
            "jurisdiction": jurisdiction,
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
        target=target_id, target_type="case" if body.case_id else "evidence",
        target_id=target_id, target_name=label or "",
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
        exp_obj = await session.get(Export, export_id)
        if not exp_obj:
            raise HTTPException(404, "Export not found")
        # B15: pending → approved | rejected only; a decision is final
        if exp_obj.status != "pending":
            raise HTTPException(409, f"Export already {exp_obj.status}")
        before_status = exp_obj.status
        exp_obj.status = body.decision
        exp_obj.decision_by = user["email"]
        exp_obj.decision_at = datetime.now(timezone.utc).isoformat()
        exp_obj.decision_comment = body.comment
        if body.decision == "approved":
            # Freeze the evidence list at approval (E4)
            from services.exports import evidence_ids_for
            exp_obj.evidence_ids = await evidence_ids_for(session, exp_obj)
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
    if body.decision == "approved":
        doc = await _finalize_or_error(export_id, user, request)
        return {"ok": True, "export": doc}
    return {"ok": True}


@router.get("/exports/{export_id}/bundle")
async def download_65b_bundle(export_id: str, request: Request, user: dict = Depends(get_current_user)):
    # get_current_user also reads ?token= (old direct <a> links) and maps Keycloak
    # roles the same way as every other endpoint (B9). New UI uses POST /api/downloads.
    return await _stream_bundle(export_id, user, request)


async def _stream_bundle(export_id: str, user: dict, request) -> StreamingResponse:
    async with async_session_factory() as session:
        exp_obj = await session.get(Export, export_id)
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

    if not exp.get("bundle_key"):
        # Approved before E4 (or the build failed): build once now, then always these bytes
        try:
            exp = await finalize_export(export_id, user, request=request)
        except ExportError as exc:
            raise HTTPException(exc.status, exc.detail)
    try:
        data = await asyncio.to_thread(read_bundle, exp)
    except BundleIntegrityError as exc:
        await write_audit_v2(actor=user, action="bundle_download", category="evidence", severity="critical",
                             target=export_id, target_type="export", target_id=export_id,
                             details=str(exc), status="failure", request=request)
        raise HTTPException(409, "Stored bundle failed its integrity check")
    except Exception:
        raise HTTPException(503, "Evidence storage unavailable")

    await write_audit_v2(
        actor=user,
        action="bundle_download",
        target=exp["id"],
        target_type="export",
        target_id=exp["id"],
        target_name=exp.get("evidence_label", ""),
        details=f"Bundle {exp['bundle_sha256']}",
        after_values={"bundle_sha256": exp["bundle_sha256"]},
        source_endpoint="GET /api/exports/{id}/bundle",
        request=request,
    )
    name = f"{exp.get('case_number') or exp['id']}_evidence_bundle.zip".replace("/", "-").replace(" ", "_")
    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Bundle-SHA256": exp["bundle_sha256"],
            "Cache-Control": "private, no-store",
        },
    )


# ── E5: evidence objects, scoped ─────────────────────────────────────────────
PII_BUCKETS = frozenset({"dvas-faces", "dvas-plates", "dvas-watchlist"})
PII_KINDS = frozenset({"face_crop", "plate_crop"})
_VIEW_AUDIT_WINDOW_S = 3600
_last_view_audit: dict = {}            # (user_id, evidence_id) -> epoch of last evidence_view audit


def _is_pii(obj: dict) -> bool:
    src = obj.get("source") or {}
    return (obj.get("kind") in PII_KINDS or obj.get("bucket") in PII_BUCKETS
            or src.get("bucket") in PII_BUCKETS)


def _object_for_role(ev: Evidence, role: str) -> Optional[dict]:
    for obj in ev.objects or []:
        if obj.get("role") == role and obj.get("bucket") and obj.get("key"):
            return obj
    # Rows created before E1 only have the old key columns
    if not ev.objects:
        if role == "primary" and ev.primary_image_key:
            return {"role": "primary", "bucket": ev.image_bucket or "dvas-faces", "key": ev.primary_image_key}
        if role == "secondary" and ev.secondary_image_key:
            bucket = "dvas-plates" if ev.secondary_image_key.startswith("plates/") else "dvas-faces"
            return {"role": "secondary", "bucket": bucket, "key": ev.secondary_image_key}
    return None


async def stream_evidence_object(evidence_id: str, role: str, user: dict, request) -> Response:
    if user.get("role") not in ("control_room_officer", "senior_officer", "system_administrator"):
        raise HTTPException(403, "Not permitted")
    async with async_session_factory() as session:
        ev = (await session.execute(
            _scope_evidence(select(Evidence), user).where(Evidence.id == evidence_id))).scalar_one_or_none()
    # Out of scope and missing look the same (no existence leak)
    if ev is None or (ev.status == "disposed" and user.get("role") != "senior_officer"):
        raise HTTPException(404, "Evidence not found")
    obj = _object_for_role(ev, role)
    if obj is None:
        raise HTTPException(404, "No such evidence object")
    if _is_pii(obj) and user.get("role") not in PII_VIEW_ROLES:
        raise HTTPException(403, "Face and plate images are restricted for this role")
    try:
        data = await asyncio.to_thread(evidence_vault.read_object, obj["bucket"], obj["key"])
    except evidence_vault.VaultUnavailable:
        raise HTTPException(503, "Evidence storage unavailable")
    except Exception:
        raise HTTPException(404, "Evidence object not found in storage")

    key = (user.get("id"), evidence_id)
    now = datetime.now(timezone.utc).timestamp()
    if now - _last_view_audit.get(key, 0) >= _VIEW_AUDIT_WINDOW_S:
        _last_view_audit[key] = now
        await write_audit_v2(actor=user, action="evidence_view", category="evidence", target=evidence_id,
                             target_type="evidence", target_id=evidence_id, target_name=ev.label or "",
                             details=f"Viewed {role}", source_endpoint="GET /api/evidence/{id}/objects/{role}",
                             request=request)
    headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
    if obj.get("sha256"):
        headers["X-Content-SHA256"] = obj["sha256"]
    return Response(content=data, media_type=obj.get("content_type") or "image/jpeg", headers=headers)


@router.get("/evidence/{evidence_id}/objects/{role}")
async def get_evidence_object(evidence_id: str, role: str, request: Request,
                              user: dict = Depends(get_current_user)):
    return await stream_evidence_object(evidence_id, role, user, request)


@router.get("/retention/preview")
async def retention_preview(user: dict = Depends(require_roles("senior_officer", "system_administrator"))):
    """E2: what the next retention run would dispose / remove (no changes)."""
    from services.retention import run_retention
    return await run_retention(dry_run=True)
