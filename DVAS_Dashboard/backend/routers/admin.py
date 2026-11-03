"""
Admin router (Admin page tabs: Token & Sessions, Policy, System Settings, Compliance).

Replaces the hard-coded tabs (ADM-01, 03-07): sessions come from Keycloak and can
be revoked; settings are stored and enforced (services/settings_service.py) with
every change audited; policy shows the values and rules the code really applies;
compliance figures are computed from the audit log, exports and evidence.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import func, select

from audit_service import write_audit_v2
from core.config import PII_VIEW_ROLES
from core.security import STATEWIDE_JURISDICTIONS, require_roles
from database import async_session_factory
from keycloak_service import list_active_sessions, logout_user_everywhere, realm_token_settings, revoke_session
from models import AuditLegalHold, AuditLog, Evidence, Export
from services import fps_policy, settings_service

router = APIRouter(prefix="/api/admin", tags=["Admin"])

ADMIN = ("system_administrator",)
POLICY_READERS = ("system_administrator", "senior_officer")


# ── Sessions ─────────────────────────────────────────────────────────────────
@router.get("/sessions")
async def sessions(user: dict = Depends(require_roles(*ADMIN))):
    return await list_active_sessions()


@router.delete("/sessions/{session_id}")
async def revoke(session_id: str, request: Request, user: dict = Depends(require_roles(*ADMIN))):
    await revoke_session(session_id)
    await write_audit_v2(actor=user, action="session_revoke", category="security", severity="warning",
                         target=session_id, target_type="session", target_id=session_id,
                         details="Session revoked by administrator", source_endpoint="DELETE /api/admin/sessions/{id}",
                         request=request)
    return {"ok": True, "revoked": session_id}


@router.post("/users/{user_id}/logout")
async def logout_everywhere(user_id: str, request: Request, user: dict = Depends(require_roles(*ADMIN))):
    await logout_user_everywhere(user_id)
    await write_audit_v2(actor=user, action="session_revoke_all", category="security", severity="warning",
                         target=user_id, target_type="user", target_id=user_id,
                         details="All sessions of the user revoked", request=request)
    return {"ok": True}


# ── Settings ─────────────────────────────────────────────────────────────────
@router.get("/settings")
async def get_settings(user: dict = Depends(require_roles(*POLICY_READERS))):
    values = await settings_service.get_all()
    return {
        "editable": user["role"] in ADMIN,
        "settings": [dict(key=k, value=values[k], **{x: d[x] for x in ("label", "type", "enforced_by")},
                          min=d.get("min"), max=d.get("max"))
                     for k, d in settings_service.DEFINITIONS.items()],
    }


@router.put("/settings")
async def put_settings(request: Request, changes: Dict[str, Any] = Body(...),
                       user: dict = Depends(require_roles(*ADMIN))):
    try:
        diff = await settings_service.update(changes, user)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if diff:
        await write_audit_v2(
            actor=user, action="system_settings_update", category="configuration", severity="warning",
            target="system_settings", target_type="configuration",
            details="; ".join(f"{k}: {v['before']} → {v['after']}" for k, v in diff.items()),
            before_values={k: v["before"] for k, v in diff.items()},
            after_values={k: v["after"] for k, v in diff.items()},
            source_endpoint="PUT /api/admin/settings", request=request)
        if any(k in settings_service.FPS_KEYS for k in diff):
            await fps_policy.push()          # the analytics governor applies it within 2 s
    return {"changed": diff, "settings": await settings_service.get_all()}


# ── Policy (read-only view of what is enforced) ─────────────────────────────
@router.get("/policy")
async def policy(user: dict = Depends(require_roles(*POLICY_READERS))):
    try:
        tokens = await realm_token_settings()
    except Exception:
        tokens = {}
    return {
        "settings": await settings_service.get_all(),
        "identity": {
            "source": "Keycloak realm (edit in the Keycloak admin console)",
            "access_token_lifespan_s": tokens.get("access_token_lifespan_s"),
            "sso_session_idle_s": tokens.get("sso_session_idle_s"),
            "sso_session_max_s": tokens.get("sso_session_max_s"),
        },
        "transport": {
            "tls": "Terminated at the RunPod / Cloudflare edge; not configurable in DVAS",
        },
        "rules": [
            {"name": "jurisdiction_scope",
             "rule": "Control room officers see their own jurisdiction, statewide rows ("
                     + ", ".join(STATEWIDE_JURISDICTIONS) + ") and rows without a jurisdiction; "
                     "senior officers see all districts"},
            {"name": "operator_mission_scope",
             "rule": "Drone operators see evidence and alerts of their assigned missions only; none if unassigned"},
            {"name": "pii_view", "rule": "Face and number-plate images: " + ", ".join(sorted(PII_VIEW_ROLES))},
            {"name": "admin_content_wall",
             "rule": "System administrators manage infrastructure and users; they cannot view evidence images"},
            {"name": "export_approval",
             "rule": "Exports need a senior officer's approval; direct exports are marked self-approved"},
            {"name": "audit_visibility",
             "rule": "Senior officers and administrators read the full audit log; others their own entries"},
        ],
    }


# ── Compliance figures (ADM-07) ──────────────────────────────────────────────
@router.get("/compliance")
async def compliance(user: dict = Depends(require_roles(*POLICY_READERS))):
    now = datetime.now(timezone.utc)
    d7, d14, h48 = (now - timedelta(days=7)).isoformat(), (now - timedelta(days=14)).isoformat(), \
        (now - timedelta(hours=48)).isoformat()

    async def count(stmt):
        return (await session.execute(stmt)).scalar() or 0

    async with async_session_factory() as session:
        events_7d = await count(select(func.count()).select_from(AuditLog).where(AuditLog.ts >= d7))
        events_prev = await count(select(func.count()).select_from(AuditLog)
                                  .where(AuditLog.ts >= d14, AuditLog.ts < d7))
        failed_7d = await count(select(func.count()).select_from(AuditLog)
                                .where(AuditLog.ts >= d7, AuditLog.status != "success"))
        config_7d = await count(select(func.count()).select_from(AuditLog)
                                .where(AuditLog.ts >= d7, AuditLog.category == "configuration"))
        exports_late = await count(select(func.count()).select_from(Export)
                                   .where(Export.status == "pending", Export.requested_at < h48))
        integrity = await count(select(func.count()).select_from(Evidence)
                                .where(Evidence.integrity.in_(("mismatch", "missing"))))
        ev_holds = await count(select(func.count()).select_from(Evidence).where(Evidence.legal_hold.is_(True)))
        audit_holds = await count(select(func.count()).select_from(AuditLegalHold)
                                  .where(AuditLegalHold.active.is_(True)))
    change = round(100 * (events_7d - events_prev) / events_prev, 1) if events_prev else None
    return {
        "generated_at": now.isoformat(),
        "cards": [
            {"key": "audit_events_7d", "label": "Audited actions (7 days)", "value": events_7d,
             "note": f"{change:+.1f}% vs previous 7 days" if change is not None else "no events in the previous 7 days"},
            {"key": "failed_actions_7d", "label": "Failed or denied actions (7 days)", "value": failed_7d,
             "note": "audit entries with a non-success status"},
            {"key": "config_changes_7d", "label": "Configuration changes (7 days)", "value": config_7d,
             "note": "model, settings and policy changes"},
            {"key": "exports_late", "label": "Export requests waiting > 48 h", "value": exports_late,
             "note": "pending a senior officer's decision"},
            {"key": "integrity_issues", "label": "Evidence integrity issues", "value": integrity,
             "note": "hash mismatch or missing stored object"},
            {"key": "legal_holds", "label": "Active legal holds", "value": ev_holds + audit_holds,
             "note": f"{ev_holds} evidence · {audit_holds} audit entries"},
        ],
    }
