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
)
from schemas.models import PresetCreateIn

logger = logging.getLogger("dvas")
router = APIRouter(prefix="/api", tags=["Audit Log Explorer"])


@router.get("/audit-logs")
async def list_audit(
    user: dict = Depends(require_roles("senior_officer", "system_administrator")),
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
    user: dict = Depends(require_roles("senior_officer", "system_administrator")),
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
    user: dict = Depends(require_roles("senior_officer", "system_administrator")),
    limit: int = 1000,
):
    """Return overall SHA-256 cryptographic chain integrity summary."""
    return await verify_chain_integrity(limit=limit)


@router.get("/audit-logs/retention-policy")
async def audit_retention_policy(
    user: dict = Depends(require_roles("senior_officer", "system_administrator")),
):
    """Return audit trail statutory retention policy details."""
    return get_retention_policy()


@router.get("/audit-logs/action-types")
async def audit_action_types(
    user: dict = Depends(require_roles("senior_officer", "system_administrator")),
):
    """Return distinct action types for filter dropdown."""
    return await get_distinct_values("action_type")


@router.get("/audit-logs/categories")
async def audit_categories(
    user: dict = Depends(require_roles("senior_officer", "system_administrator")),
):
    """Return distinct categories for filter dropdown."""
    return await get_distinct_values("category")


@router.get("/audit-logs/severities")
async def audit_severities(
    user: dict = Depends(require_roles("senior_officer", "system_administrator")),
):
    """Return distinct severities for filter dropdown."""
    return await get_distinct_values("severity")


@router.get("/audit-logs/target-types")
async def audit_target_types(
    user: dict = Depends(require_roles("senior_officer", "system_administrator")),
):
    """Return distinct target types for filter dropdown."""
    return await get_distinct_values("target_type")


@router.get("/audit-logs/sources")
async def audit_source_modules(
    user: dict = Depends(require_roles("senior_officer", "system_administrator")),
):
    """Return distinct source modules for filter dropdown."""
    return await get_distinct_values("source_module")


@router.get("/audit-logs/anomalies")
async def audit_anomalies(
    user: dict = Depends(require_roles("senior_officer", "system_administrator")),
    hours: int = 24,
):
    """Evaluate and return rule-based security anomaly patterns over the last N hours."""
    return await detect_anomalies(hours=min(max(1, int(hours)), 168))


@router.get("/audit-logs/export/csv")
async def audit_export_csv(
    user: dict = Depends(require_roles("senior_officer", "system_administrator")),
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
    user: dict = Depends(require_roles("senior_officer", "system_administrator")),
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
    user: dict = Depends(require_roles("senior_officer", "system_administrator")),
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
    user: dict = Depends(require_roles("senior_officer", "system_administrator")),
):
    """Retrieve correlated events for timeline view."""
    return await get_related_events(correlation_id=correlation_id, user=user)


@router.get("/audit-logs/{log_id}")
async def audit_single_log(
    log_id: str,
    user: dict = Depends(require_roles("senior_officer", "system_administrator")),
):
    """Retrieve single audit log entry by ID."""
    entry = await get_single_entry(log_id=log_id, user=user)
    if not entry:
        raise HTTPException(404, "Audit log entry not found")
    return entry


# ---------- Filter Presets CRUD ----------

@router.get("/audit-presets")
async def get_audit_presets(
    user: dict = Depends(require_roles("senior_officer", "system_administrator")),
):
    """List saved and shared filter presets for the current user."""
    return await list_presets(user_id=user["id"])


@router.post("/audit-presets")
async def create_audit_preset(
    body: PresetCreateIn,
    user: dict = Depends(require_roles("senior_officer", "system_administrator")),
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
    user: dict = Depends(require_roles("senior_officer", "system_administrator")),
):
    """Delete a saved filter preset owned by the current user."""
    success = await delete_preset(preset_id=preset_id, user_id=user["id"])
    if not success:
        raise HTTPException(404, "Preset not found or not owned by you")
    return {"ok": True}
