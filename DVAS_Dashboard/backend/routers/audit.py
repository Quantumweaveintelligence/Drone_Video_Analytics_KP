"""
Audit Log Explorer Router.

Handles operations related to audit logging, searching, exporting, and presets.
"""
import logging
from typing import Optional
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from core.security import require_roles
from audit_service import (
    query_audit_logs, get_audit_summary, get_single_entry, get_related_events,
    verify_chain_integrity, get_distinct_values, get_retention_policy,
    export_audit_logs_csv, export_audit_logs_json,
    generate_audit_section65b_pdf, detect_anomalies,
    check_rate_limit, write_audit_v2,
    save_preset, list_presets, delete_preset,
    get_audit_trends, log_audit_access,
    place_legal_hold, release_legal_hold, get_chain_of_custody,
    dry_run_purge, export_archive_bundle, execute_statutory_purge,
)
from schemas.models import (
    PresetCreateIn,
    LegalHoldCreateIn,
    LegalHoldReleaseIn,
    PurgeDryRunIn,
    PurgeExecuteIn,
    AuditAccessLogIn,
)

logger = logging.getLogger("dvas")
router = APIRouter(prefix="/api", tags=["Audit Log Explorer"])

AUDIT_VIEW_ROLES = ("senior_officer", "system_administrator", "control_room_officer", "drone_operator")


@router.get("/audit-logs")
async def list_audit(
    user: dict = Depends(require_roles(*AUDIT_VIEW_ROLES)),
    q: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    actor_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    action_type: Optional[str] = None,
    category: Optional[str] = None,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    source_module: Optional[str] = None,
    target_type: Optional[str] = None,
    compliance_tag: Optional[str] = None,
    is_legal_hold: Optional[str] = None,
    correlation_id: Optional[str] = None,
    request_id: Optional[str] = None,
    sort_by: str = "ts",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 25,
):
    """Server-side paginated and filtered audit log explorer endpoint."""
    return await query_audit_logs(
        q=q,
        date_from=date_from,
        date_to=date_to,
        actor_id=actor_id,
        actor_role=actor_role,
        action_type=action_type,
        category=category,
        severity=severity,
        status=status,
        source_module=source_module,
        target_type=target_type,
        compliance_tag=compliance_tag,
        is_legal_hold=is_legal_hold,
        correlation_id=correlation_id,
        request_id=request_id,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=page,
        page_size=page_size,
        user=user,
    )


@router.get("/audit-logs/summary")
async def audit_summary(
    user: dict = Depends(require_roles(*AUDIT_VIEW_ROLES)),
    q: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    """Aggregate summary KPI counts (Total, Failed, Critical, Warning, Info)."""
    return await get_audit_summary(
        q=q,
        date_from=date_from,
        date_to=date_to,
        user=user,
    )


@router.get("/audit-logs/verify")
async def verify_audit_chain_endpoint(
    user: dict = Depends(require_roles(*AUDIT_VIEW_ROLES)),
    limit: int = 1000,
):
    """Return overall SHA-256 cryptographic chain integrity summary."""
    return await verify_chain_integrity(limit=limit)


@router.get("/audit-logs/retention-policy")
async def audit_retention_policy(
    user: dict = Depends(require_roles(*AUDIT_VIEW_ROLES)),
):
    """Return audit trail statutory retention policy details."""
    return get_retention_policy()


@router.get("/audit-logs/action-types")
async def audit_action_types(
    user: dict = Depends(require_roles(*AUDIT_VIEW_ROLES)),
):
    """Return distinct action types for filter dropdown."""
    return await get_distinct_values("action_type")


@router.get("/audit-logs/categories")
async def audit_categories(
    user: dict = Depends(require_roles(*AUDIT_VIEW_ROLES)),
):
    """Return distinct categories for filter dropdown."""
    return await get_distinct_values("category")


@router.get("/audit-logs/severities")
async def audit_severities(
    user: dict = Depends(require_roles(*AUDIT_VIEW_ROLES)),
):
    """Return distinct severities for filter dropdown."""
    return await get_distinct_values("severity")


@router.get("/audit-logs/target-types")
async def audit_target_types(
    user: dict = Depends(require_roles(*AUDIT_VIEW_ROLES)),
):
    """Return distinct target types for filter dropdown."""
    return await get_distinct_values("target_type")


@router.get("/audit-logs/sources")
async def audit_source_modules(
    user: dict = Depends(require_roles(*AUDIT_VIEW_ROLES)),
):
    """Return distinct source modules for filter dropdown."""
    return await get_distinct_values("source_module")


@router.get("/audit-logs/roles")
async def audit_roles(
    user: dict = Depends(require_roles(*AUDIT_VIEW_ROLES)),
):
    """Return distinct user roles for filter dropdown."""
    return await get_distinct_values("actor_role")


@router.get("/audit-logs/compliance-tags")
async def audit_compliance_tags(
    user: dict = Depends(require_roles(*AUDIT_VIEW_ROLES)),
):
    """Return distinct compliance tags for filter dropdown."""
    return await get_distinct_values("compliance_tags")


@router.get("/audit-logs/trends")
async def audit_trends(
    user: dict = Depends(require_roles(*AUDIT_VIEW_ROLES)),
    range: str = "24h",
):
    """Aggregate activity trend histograms and top actor stats."""
    return await get_audit_trends(range_val=range, user=user)


@router.post("/audit-logs/access-log")
async def audit_access_log_endpoint(
    body: AuditAccessLogIn,
    request: Request,
    user: dict = Depends(require_roles(*AUDIT_VIEW_ROLES)),
):
    """Log officer access to the Audit Log Explorer (audit of the auditors)."""
    logged = await log_audit_access(actor=user, request=request, referrer=body.referrer)
    return {"logged": logged}


@router.get("/audit-logs/anomalies")
async def audit_anomalies(
    user: dict = Depends(require_roles(*AUDIT_VIEW_ROLES)),
    hours: int = 24,
):
    """Evaluate and return rule-based security anomaly patterns over the last N hours."""
    return await detect_anomalies(hours=min(max(1, int(hours)), 168))


@router.get("/audit-logs/export/csv")
async def audit_export_csv(
    user: dict = Depends(require_roles(*AUDIT_VIEW_ROLES)),
    q: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    actor_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    action_type: Optional[str] = None,
    category: Optional[str] = None,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    source_module: Optional[str] = None,
    target_type: Optional[str] = None,
    compliance_tag: Optional[str] = None,
    is_legal_hold: Optional[str] = None,
    correlation_id: Optional[str] = None,
    request_id: Optional[str] = None,
    request: Request = None,
):
    """Stream filtered audit log records as a CSV file with rate limiting and security headers."""
    client_id = user.get("id") or user.get("email") or "anon"
    if not check_rate_limit(client_id, max_requests=30, window_sec=60):
        raise HTTPException(status_code=429, detail="Export rate limit exceeded. Please wait 1 minute before requesting more exports.")

    filters = {
        "q": q,
        "date_from": date_from,
        "date_to": date_to,
        "actor_id": actor_id,
        "actor_role": actor_role,
        "action_type": action_type,
        "category": category,
        "severity": severity,
        "status": status,
        "source_module": source_module,
        "target_type": target_type,
        "compliance_tag": compliance_tag,
        "is_legal_hold": is_legal_hold,
        "correlation_id": correlation_id,
        "request_id": request_id,
    }
    csv_content = await export_audit_logs_csv(filters, user)
    await write_audit_v2(
        actor=user,
        action="audit_log_export_csv",
        target="CSV Export",
        target_type="evidence",
        source_endpoint="GET /api/audit-logs/export/csv",
        request=request,
    )
    fname = f"dvas_audit_log_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "Cache-Control": "no-store, no-cache, must-revalidate, private",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/audit-logs/export/json")
async def audit_export_json(
    user: dict = Depends(require_roles(*AUDIT_VIEW_ROLES)),
    q: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    actor_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    action_type: Optional[str] = None,
    category: Optional[str] = None,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    source_module: Optional[str] = None,
    target_type: Optional[str] = None,
    compliance_tag: Optional[str] = None,
    is_legal_hold: Optional[str] = None,
    correlation_id: Optional[str] = None,
    request_id: Optional[str] = None,
    request: Request = None,
):
    """Export filtered audit log records as JSON array."""
    client_id = user.get("id") or user.get("email") or "anon"
    if not check_rate_limit(client_id, max_requests=30, window_sec=60):
        raise HTTPException(status_code=429, detail="Export rate limit exceeded. Please wait 1 minute before requesting more exports.")

    filters = {
        "q": q,
        "date_from": date_from,
        "date_to": date_to,
        "actor_id": actor_id,
        "actor_role": actor_role,
        "action_type": action_type,
        "category": category,
        "severity": severity,
        "status": status,
        "source_module": source_module,
        "target_type": target_type,
        "compliance_tag": compliance_tag,
        "is_legal_hold": is_legal_hold,
        "correlation_id": correlation_id,
        "request_id": request_id,
    }
    result = await export_audit_logs_json(filters, user)
    await write_audit_v2(
        actor=user,
        action="audit_log_export_json",
        target="JSON Export",
        target_type="evidence",
        source_endpoint="GET /api/audit-logs/export/json",
        request=request,
    )
    return result


@router.get("/audit-logs/export/pdf")
async def audit_export_pdf(
    user: dict = Depends(require_roles(*AUDIT_VIEW_ROLES)),
    q: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    actor_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    action_type: Optional[str] = None,
    category: Optional[str] = None,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    source_module: Optional[str] = None,
    target_type: Optional[str] = None,
    compliance_tag: Optional[str] = None,
    is_legal_hold: Optional[str] = None,
    request: Request = None,
):
    """Stream Section 65B Certified PDF Evidence Certificate for filtered audit records."""
    client_id = user.get("id") or user.get("email") or "anon"
    if not check_rate_limit(client_id, max_requests=20, window_sec=60):
        raise HTTPException(status_code=429, detail="Certificate generation rate limit exceeded. Please wait 1 minute before requesting another PDF.")

    filters = {
        "q": q,
        "date_from": date_from,
        "date_to": date_to,
        "actor_id": actor_id,
        "actor_role": actor_role,
        "action_type": action_type,
        "category": category,
        "severity": severity,
        "status": status,
        "source_module": source_module,
        "target_type": target_type,
        "compliance_tag": compliance_tag,
        "is_legal_hold": is_legal_hold,
    }
    res = await query_audit_logs(
        **filters,
        page=1,
        page_size=200,
        sort_by="ts",
        sort_dir="desc",
        user=user,
    )
    items = res.get("items", [])
    filter_summary = f"q={q or 'none'}, date={date_from or 'any'} to {date_to or 'any'}"
    pdf_bytes = generate_audit_section65b_pdf(items, user, filter_desc=filter_summary)

    await write_audit_v2(
        actor=user,
        action="audit_log_export_pdf",
        target=f"Section 65B PDF ({len(items)} rows)",
        target_type="evidence",
        details=filter_summary,
        source_endpoint="GET /api/audit-logs/export/pdf",
        request=request,
    )

    fname = f"dvas_section65b_audit_certificate_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "Cache-Control": "no-store, no-cache, must-revalidate, private",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/audit-logs/related/{correlation_id}")
async def audit_related_events(
    correlation_id: str,
    user: dict = Depends(require_roles(*AUDIT_VIEW_ROLES)),
):
    """Retrieve correlated events for timeline view."""
    return await get_related_events(correlation_id=correlation_id, user=user)


@router.get("/audit-logs/{log_id}")
async def audit_single_log(
    log_id: str,
    user: dict = Depends(require_roles(*AUDIT_VIEW_ROLES)),
):
    """Retrieve single audit log entry by ID."""
    entry = await get_single_entry(log_id=log_id, user=user)
    if not entry:
        raise HTTPException(404, "Audit log entry not found")
    return entry


# ---------- Filter Presets CRUD ----------

@router.get("/audit-presets")
async def get_audit_presets(
    user: dict = Depends(require_roles(*AUDIT_VIEW_ROLES)),
):
    """List saved and shared filter presets for the current user."""
    return await list_presets(user_id=user["id"])


@router.post("/audit-presets")
async def create_audit_preset(
    body: PresetCreateIn,
    user: dict = Depends(require_roles(*AUDIT_VIEW_ROLES)),
):
    """Save a new filter preset."""
    return await save_preset(
        name=body.name,
        filters=body.filters,
        owner_id=user["id"],
        is_shared=body.is_shared,
    )


@router.delete("/audit-presets/{preset_id}")
async def delete_audit_preset(
    preset_id: str,
    user: dict = Depends(require_roles(*AUDIT_VIEW_ROLES)),
):
    """Delete a saved filter preset owned by the current user."""
    success = await delete_preset(preset_id=preset_id, user_id=user["id"])
    if not success:
        raise HTTPException(404, "Preset not found or not owned by you")
    return {"ok": True}


# ---------- Statutory Legal Holds & Forensic Chain-of-Custody ----------

@router.post("/audit-logs/{log_id}/legal-hold")
async def place_legal_hold_endpoint(
    log_id: str,
    body: LegalHoldCreateIn,
    user: dict = Depends(require_roles("senior_officer", "system_administrator")),
):
    """Place a statutory legal hold on an audit record, preventing deletion under retention policies."""
    try:
        return await place_legal_hold(
            audit_log_id=log_id,
            case_number=body.case_number,
            reason=body.hold_reason,
            actor=user,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/audit-logs/{log_id}/legal-hold")
async def release_legal_hold_endpoint(
    log_id: str,
    body: LegalHoldReleaseIn,
    user: dict = Depends(require_roles("senior_officer", "system_administrator")),
):
    """Release an active statutory legal hold on an audit record."""
    try:
        return await release_legal_hold(
            audit_log_id=log_id,
            release_reason=body.release_reason,
            actor=user,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/audit-logs/{log_id}/chain-of-custody")
async def audit_chain_of_custody_endpoint(
    log_id: str,
    user: dict = Depends(require_roles(*AUDIT_VIEW_ROLES)),
):
    """Generate complete cryptographic provenance and chain-of-custody envelope."""
    try:
        return await get_chain_of_custody(log_id=log_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------- Statutory Archive & Purge (Super-Admin Restricted) ----------

@router.post("/audit-logs/archive/dry-run")
async def purge_dry_run_endpoint(
    body: PurgeDryRunIn,
    user: dict = Depends(require_roles("system_administrator")),
):
    """Dry-run calculation of purgeable records older than retention threshold (min 365 days)."""
    try:
        return await dry_run_purge(older_than_days=body.older_than_days, user=user)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/audit-logs/archive/export")
async def purge_export_endpoint(
    older_than_days: int = 365,
    user: dict = Depends(require_roles("system_administrator")),
):
    """Stream verifiable JSON archive bundle with SHA-256 manifest of records to be purged."""
    try:
        import json
        manifest = await export_archive_bundle(older_than_days=older_than_days, user=user)
        fname = f"dvas_audit_archive_{older_than_days}d_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        content = json.dumps(manifest, indent=2)
        return StreamingResponse(
            iter([content]),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{fname}"',
                "Cache-Control": "no-store, no-cache, must-revalidate, private",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/audit-logs/archive/execute")
async def purge_execute_endpoint(
    body: PurgeExecuteIn,
    user: dict = Depends(require_roles("system_administrator")),
):
    """Execute controlled statutory purge of expired records that are NOT under legal hold."""
    try:
        return await execute_statutory_purge(
            older_than_days=body.older_than_days,
            confirmation_phrase=body.confirmation_phrase,
            actor=user,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


# ---------- Edge Ground Station Audit Sync Endpoint ----------

@router.post("/audit-logs/sync")
async def audit_sync_endpoint(
    payload: dict,
    request: Request,
    user: dict = Depends(require_roles("senior_officer", "system_administrator", "drone_operator")),
):
    """Ingest a batch of audit log entries pushed from an edge ground station."""
    from services.audit_sync import sync_records_batch
    records = payload.get("records", [])
    if not isinstance(records, list):
        raise HTTPException(status_code=400, detail="records must be a list of audit objects")
    synced = await sync_records_batch(records)
    return {"synced": synced, "received": len(records)}


