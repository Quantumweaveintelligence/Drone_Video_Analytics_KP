"""
audit_service.py — Centralized Audit Logging Service for DVAS
=============================================================

Phases 1, 2, 3 & 5: Backend Service Layer, Enriched Logging, Query & Verification Engine

Responsibilities:
  1. write_audit_v2()             — enriched audit writer with all new fields & SHA-256 hash chaining
  2. query_audit_logs()           — server-side filtering, sorting, pagination, RBAC scoping
  3. get_audit_summary()          — KPI summary counts (total, failed, critical, warning, info)
  4. get_single_entry()           — detailed single-log lookup with verification
  5. get_related_events()         — correlation ID / session timeline lookup
  6. verify_chain_integrity()     — cryptographic hash-chain verification
  7. get_distinct_values()        — distinct values for filter dropdown menus
  8. get_retention_policy()       — statutory retention policy metadata
  9. export_audit_logs_csv()      — filtered CSV export generation
  10. export_audit_logs_json()    — filtered JSON export generation
  11. Preset CRUD operations      — save, list, and delete user filter presets
"""

import hashlib
import uuid
import logging
import io
import csv
import json
import math
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Union
from fpdf import FPDF

from sqlalchemy import select, func, or_, and_, desc, asc, delete, text
from database import async_session_factory
from models import AuditLog, AuditFilterPreset, AuditLegalHold

logger = logging.getLogger("dvas.audit")


# ─────────────────────────────────────────────────────────────────────────────
# Action classification lookup
# ─────────────────────────────────────────────────────────────────────────────

_ACTION_MAP = {
    # Authentication
    "login":                    ("login",   "authentication",   "info"),
    "logout":                   ("logout",  "authentication",   "info"),

    # User management
    "user_create":              ("create",  "user_mgmt",        "critical"),
    "user_suspend":             ("delete",  "user_mgmt",        "critical"),
    "user_patch":               ("update",  "user_mgmt",        "critical"),
    "permission_change":        ("update",  "security",         "critical"),

    # Alerts / operations
    "alert_ack":                ("update",  "operations",       "info"),
    "shift_summary_download":   ("export",  "operations",       "info"),

    # Evidence / exports
    "export_request":           ("export",  "evidence",         "warning"),
    "export_approve":           ("update",  "evidence",         "critical"),
    "export_deny":              ("update",  "evidence",         "critical"),
    "bundle_download":          ("export",  "evidence",         "warning"),

    # Configuration
    "model_config_update":      ("update",  "configuration",    "warning"),

    # Watchlist / ANPR
    "watchlist_add":            ("create",  "operations",       "warning"),
    "watchlist_remove":         ("delete",  "operations",       "warning"),

    # Audit log meta-actions & compliance
    "audit_log_view":           ("read",    "security",         "info"),
    "audit_log_export_csv":     ("export",  "security",         "info"),
    "audit_log_export_json":    ("export",  "security",         "info"),
    "audit_log_export_pdf":     ("export",  "security",         "warning"),
    "audit_chain_verify":       ("read",    "security",         "info"),
    "audit_preset_create":      ("create",  "security",         "info"),
    "audit_preset_delete":      ("delete",  "security",         "info"),
    "audit_legal_hold_place":   ("create",  "compliance",       "warning"),
    "audit_legal_hold_release": ("delete",  "compliance",       "warning"),
    "audit_purge":              ("delete",  "compliance",       "critical"),
    "audit_archive_export":     ("export",  "compliance",       "warning"),
}

_ENDPOINT_MAP = {
    "login":                    "POST /api/auth/login",
    "logout":                   "POST /api/auth/logout",
    "alert_ack":                "POST /api/alerts/{id}/ack",
    "export_request":           "POST /api/exports",
    "export_approve":           "POST /api/exports/{id}/decision",
    "export_deny":              "POST /api/exports/{id}/decision",
    "user_create":              "POST /api/users",
    "user_suspend":             "DELETE /api/users/{id}",
    "user_patch":               "PATCH /api/users/{id}",
    "permission_change":        "PATCH /api/users/{id}",
    "model_config_update":      "POST /api/models",
    "watchlist_add":            "POST /api/analytics/watchlist",
    "watchlist_remove":         "DELETE /api/analytics/watchlist/{plate}",
    "shift_summary_download":   "GET /api/edge/shift-summary",
    "bundle_download":          "GET /api/exports/{id}/bundle",
    "audit_log_view":           "GET /api/audit-logs",
    "audit_log_export_csv":     "GET /api/audit-logs/export/csv",
    "audit_log_export_json":    "GET /api/audit-logs/export/json",
    "audit_log_export_pdf":     "GET /api/audit-logs/export/pdf",
    "audit_chain_verify":       "GET /api/audit-logs/verify",
    "audit_preset_create":      "POST /api/audit-presets",
    "audit_preset_delete":      "DELETE /api/audit-presets/{id}",
    "audit_legal_hold_place":   "POST /api/audit-logs/{id}/legal-hold",
    "audit_legal_hold_release": "DELETE /api/audit-logs/{id}/legal-hold",
    "audit_purge":              "POST /api/audit-logs/archive/execute",
    "audit_archive_export":     "GET /api/audit-logs/archive/export",
}


def _infer_compliance_tags(action: str, action_type: str, category: str, target_type: Optional[str] = None) -> List[str]:
    """Infers statutory compliance tags (IEA_65B, DPDPA, IT_ACT_2000, GDPR, LEGAL_HOLD)."""
    tags = set()
    a = (action or "").lower()
    cat = (category or "").lower()
    tgt = (target_type or "").lower()

    if "export" in a or "download" in a or cat == "evidence" or tgt in ("evidence", "export"):
        tags.add("IEA_65B")
    if cat in ("user_mgmt", "authentication") or tgt == "user" or "user" in a or "login" in a:
        tags.add("DPDPA")
        tags.add("IT_ACT_2000")
    if cat in ("security", "configuration") or "verify" in a or "preset" in a or "patch" in a:
        tags.add("IT_ACT_2000")
    if "hold" in a:
        tags.add("IEA_65B")
        tags.add("LEGAL_HOLD")
    if not tags:
        tags.add("IT_ACT_2000")
    return sorted(list(tags))


def _classify_action(action: str) -> tuple:
    """Return (action_type, category, severity) for a given action string."""
    if action in _ACTION_MAP:
        return _ACTION_MAP[action]
    if action.startswith("export_"):
        return ("update", "evidence", "critical")
    if action.startswith("user_"):
        return ("update", "user_mgmt", "critical")
    if action.startswith("watchlist_"):
        return ("update", "operations", "warning")
    if action.startswith("model_config"):
        return ("update", "configuration", "warning")
    if action.startswith("audit_"):
        return ("read", "security", "info")
    return ("system", "operations", "info")


def _get_endpoint(action: str) -> Optional[str]:
    """Return the inferred API endpoint for a given action."""
    return _ENDPOINT_MAP.get(action)


# ─────────────────────────────────────────────────────────────────────────────
# Request context helpers
# ─────────────────────────────────────────────────────────────────────────────

def extract_session_id(request) -> Optional[str]:
    """Extract Keycloak session ID (`sid` claim) from JWT token in request."""
    if request is None:
        return None
    try:
        from jose import jwt as jose_jwt
        token = None
        if hasattr(request, "cookies"):
            token = request.cookies.get("dvas_access_token")
        if not token:
            auth_header = getattr(request, "headers", {}).get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
        if not token:
            return None
        payload = jose_jwt.get_unverified_claims(token)
        return payload.get("sid") or payload.get("session_state")
    except Exception:
        return None


def compute_device_id(request) -> Optional[str]:
    """Compute SHA-256 fingerprint (prefix 16 chars) of User-Agent."""
    if request is None:
        return None
    try:
        ua = getattr(request, "headers", {}).get("User-Agent", "")
        if not ua:
            return None
        return hashlib.sha256(ua.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return None


def get_client_ip(request) -> str:
    """Extract client IP checking X-Forwarded-For first."""
    if request is None:
        return "-"
    forwarded = getattr(request, "headers", {}).get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if hasattr(request, "client") and request.client:
        return request.client.host
    return "-"


def get_request_id(request) -> Optional[str]:
    """Get the request ID from request state."""
    if request is None:
        return None
    return getattr(getattr(request, "state", None), "request_id", None)


# ─────────────────────────────────────────────────────────────────────────────
# Hash chain
# ─────────────────────────────────────────────────────────────────────────────

_HASH_FIELDS = (
    "id",
    "ts",
    "actor_id",
    "actor_email",
    "actor_role",
    "action",
    "target",
    "details",
    "ip",
    "prev_hash",
)


def _audit_hash(entry: dict) -> str:
    """Deterministic SHA-256 of canonical fields + prev_hash."""
    canonical = "|".join(
        str(entry.get(k, "") or "")
        for k in _HASH_FIELDS
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _row_to_dict(r: AuditLog, legal_hold: Optional[dict] = None) -> dict:
    """Serialize AuditLog ORM object to plain dictionary, attaching legal hold & compliance tags."""
    if r is None:
        return {}
    return {
        "id": r.id,
        "ts": r.ts,
        # Actor
        "actor_id": r.actor_id,
        "actor_email": r.actor_email,
        "actor_role": r.actor_role,
        "actor_name": r.actor_name or "",
        "actor_session_id": r.actor_session_id,
        "actor_device_id": r.actor_device_id,
        "ip": r.ip or "-",
        # Action
        "action": r.action,
        "action_type": r.action_type or "system",
        "category": r.category or "operations",
        "severity": r.severity or "info",
        # Target
        "target": r.target or "",
        "target_type": r.target_type or "",
        "target_id": r.target_id or "",
        "target_name": r.target_name or "",
        # Diff
        "details": r.details or "",
        "before_values": r.before_values,
        "after_values": r.after_values,
        # Result
        "status": r.status or "success",
        "error_code": r.error_code,
        # Tracing
        "source_module": r.source_module or "dashboard",
        "source_endpoint": r.source_endpoint or "",
        "request_id": r.request_id or "",
        "correlation_id": r.correlation_id or "",
        # Compliance & Legal Holds
        "compliance_tags": r.compliance_tags or [],
        "is_legal_hold": legal_hold is not None,
        "legal_hold": legal_hold,
        # Chain
        "prev_hash": r.prev_hash,
        "hash": r.hash,
    }


_RATE_LIMIT_BUCKET: Dict[str, list] = {}

def check_rate_limit(client_id: str, max_requests: int = 30, window_sec: int = 60) -> bool:
    """Sliding-window in-memory rate limiter for heavy audit export operations (0 ms overhead)."""
    now = datetime.now(timezone.utc).timestamp()
    timestamps = _RATE_LIMIT_BUCKET.setdefault(client_id, [])
    _RATE_LIMIT_BUCKET[client_id] = [t for t in timestamps if now - t < window_sec]
    if len(_RATE_LIMIT_BUCKET[client_id]) >= max_requests:
        return False
    _RATE_LIMIT_BUCKET[client_id].append(now)
    return True


_SENSITIVE_KEY_PATTERNS = (
    "password", "secret", "token", "access_token", "refresh_token",
    "api_key", "client_secret", "private_key", "authorization",
    "auth_header", "credential", "pin", "admin_secret",
)

def _sanitize_sensitive_data(data: Any) -> Any:
    """Recursively redact sensitive credential keys from before/after payloads."""
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            k_str = str(k).lower()
            if any(p in k_str for p in _SENSITIVE_KEY_PATTERNS):
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = _sanitize_sensitive_data(v)
        return sanitized
    elif isinstance(data, list):
        return [_sanitize_sensitive_data(item) for item in data]
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Core write function
# ─────────────────────────────────────────────────────────────────────────────

async def write_audit_v2(
    *,
    actor: dict,
    action: str,
    action_type: Optional[str] = None,
    category: Optional[str] = None,
    severity: Optional[str] = None,
    target: str = "",
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    target_name: Optional[str] = None,
    details: str = "",
    before_values: Optional[dict] = None,
    after_values: Optional[dict] = None,
    status: str = "success",
    error_code: Optional[str] = None,
    source_module: str = "dashboard",
    source_endpoint: Optional[str] = None,
    request_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    compliance_tags: Optional[List[str]] = None,
    ip: str = "-",
    session_id: Optional[str] = None,
    device_id: Optional[str] = None,
    request=None,
) -> dict:
    """Append-only tamper-evident audit logger with compliance tagging and live WebSocket broadcast."""
    inferred_type, inferred_cat, inferred_sev = _classify_action(action)
    if action_type is None:
        action_type = inferred_type
    if category is None:
        category = inferred_cat
    if severity is None:
        severity = inferred_sev

    if compliance_tags is None:
        compliance_tags = _infer_compliance_tags(action, action_type, category, target_type)

    if request is not None:
        if ip == "-":
            ip = get_client_ip(request)
        if session_id is None:
            session_id = extract_session_id(request)
        if device_id is None:
            device_id = compute_device_id(request)
        if request_id is None:
            request_id = get_request_id(request)

    if source_endpoint is None:
        source_endpoint = _get_endpoint(action)

    # Automatically redact any secrets/credentials before saving
    safe_before = _sanitize_sensitive_data(before_values) if before_values else None
    safe_after = _sanitize_sensitive_data(after_values) if after_values else None

    async with async_session_factory() as session:
        result = await session.execute(
            select(AuditLog.hash).order_by(AuditLog.ts.desc()).limit(1)
        )
        last_hash = result.scalar_one_or_none()
        prev_hash = last_hash if last_hash else "GENESIS"

        entry_dict = {
            "id": str(uuid.uuid4()),
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor_id": actor.get("id"),
            "actor_email": actor.get("email"),
            "actor_role": actor.get("role"),
            "actor_name": actor.get("name") or "",
            "actor_session_id": session_id,
            "actor_device_id": device_id,
            "ip": ip,
            "action": action,
            "action_type": action_type,
            "category": category,
            "severity": severity,
            "target": target,
            "target_type": target_type,
            "target_id": target_id,
            "target_name": target_name,
            "details": details,
            "before_values": safe_before,
            "after_values": safe_after,
            "status": status,
            "error_code": error_code,
            "source_module": source_module,
            "source_endpoint": source_endpoint,
            "request_id": request_id,
            "correlation_id": correlation_id,
            "compliance_tags": compliance_tags,
            "prev_hash": prev_hash,
        }

        entry_dict["hash"] = _audit_hash(entry_dict)
        session.add(AuditLog(**entry_dict))
        await session.commit()

        logger.debug("audit_v2 logged %s (id=%s)", action, entry_dict["id"])

        # Broadcast asynchronously to active WebSocket clients (e.g. Live Tail explorer)
        try:
            import asyncio
            from services.websocket_hub import hub
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(hub.broadcast({
                    "type": "audit_event",
                    "data": {
                        "id": entry_dict["id"],
                        "ts": entry_dict["ts"],
                        "actor_id": entry_dict["actor_id"],
                        "actor_email": entry_dict["actor_email"],
                        "actor_name": entry_dict["actor_name"],
                        "actor_role": entry_dict["actor_role"],
                        "actor_session_id": entry_dict.get("actor_session_id"),
                        "actor_device_id": entry_dict.get("actor_device_id"),
                        "action": entry_dict["action"],
                        "action_type": entry_dict["action_type"],
                        "category": entry_dict["category"],
                        "severity": entry_dict["severity"],
                        "target": entry_dict["target"],
                        "target_type": entry_dict["target_type"],
                        "target_id": entry_dict.get("target_id"),
                        "target_name": entry_dict["target_name"],
                        "details": entry_dict.get("details", ""),
                        "before_values": safe_before,
                        "after_values": safe_after,
                        "status": entry_dict["status"],
                        "error_code": entry_dict["error_code"],
                        "source_module": entry_dict.get("source_module"),
                        "source_endpoint": entry_dict.get("source_endpoint"),
                        "request_id": entry_dict.get("request_id"),
                        "correlation_id": entry_dict.get("correlation_id"),
                        "ip": entry_dict["ip"],
                        "prev_hash": entry_dict.get("prev_hash"),
                        "hash": entry_dict.get("hash"),
                        "compliance_tags": compliance_tags,
                        "is_legal_hold": False,
                    }
                }))
            except RuntimeError:
                pass
        except Exception as ws_err:
            logger.debug("Live WebSocket audit broadcast skipped: %s", ws_err)

        return entry_dict


# ─────────────────────────────────────────────────────────────────────────────
# Query & Filter Engine
# ─────────────────────────────────────────────────────────────────────────────

def _build_filter_clauses(
    *,
    q: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    actor_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    action_type: Optional[Union[str, List[str]]] = None,
    category: Optional[Union[str, List[str]]] = None,
    severity: Optional[Union[str, List[str]]] = None,
    status: Optional[Union[str, List[str]]] = None,
    source_module: Optional[Union[str, List[str]]] = None,
    target_type: Optional[Union[str, List[str]]] = None,
    compliance_tag: Optional[Union[str, List[str]]] = None,
    is_legal_hold: Optional[Union[str, bool]] = None,
    correlation_id: Optional[str] = None,
    request_id: Optional[str] = None,
    actor_session_id: Optional[str] = None,
    user: Optional[dict] = None,
) -> list:
    """Build list of SQLAlchemy filter clauses from query parameters and user role."""
    clauses = []

    # Free text search across multiple columns (sanitized, clamped to 128 chars, control chars stripped)
    if q:
        clean_q = "".join(ch for ch in str(q)[:128] if ch.isprintable() or ch in ('\n', '\t')).strip()
        if clean_q:
            term = f"%{clean_q}%"
            clauses.append(
                or_(
                    AuditLog.actor_email.ilike(term),
                    AuditLog.actor_name.ilike(term),
                    AuditLog.action.ilike(term),
                    AuditLog.action_type.ilike(term),
                    AuditLog.target.ilike(term),
                    AuditLog.target_name.ilike(term),
                    AuditLog.details.ilike(term),
                    AuditLog.ip.ilike(term),
                    AuditLog.request_id.ilike(term),
                    AuditLog.correlation_id.ilike(term),
                )
            )

    # Date range (validated length)
    if date_from:
        clauses.append(AuditLog.ts >= str(date_from)[:32])
    if date_to:
        clauses.append(AuditLog.ts <= str(date_to)[:32])

    # Actor filters
    if actor_id:
        clauses.append(AuditLog.actor_id == str(actor_id)[:64])
    if actor_role:
        clauses.append(AuditLog.actor_role == str(actor_role)[:64])

    # Multi-value or single-value filter helper
    def _apply_in_clause(col, val):
        if not val:
            return
        if isinstance(val, str):
            vals = [str(v).strip()[:64] for v in val.split(",") if v.strip()]
        else:
            vals = [str(v).strip()[:64] for v in list(val) if str(v).strip()]
        if vals:
            clauses.append(col.in_(vals))

    _apply_in_clause(AuditLog.action_type, action_type)
    _apply_in_clause(AuditLog.category, category)
    _apply_in_clause(AuditLog.severity, severity)
    _apply_in_clause(AuditLog.status, status)
    _apply_in_clause(AuditLog.source_module, source_module)
    _apply_in_clause(AuditLog.target_type, target_type)

    # Compliance tag filter (checks JSONB array)
    if compliance_tag:
        if isinstance(compliance_tag, str):
            c_tags = [t.strip() for t in compliance_tag.split(",") if t.strip()]
        else:
            c_tags = [str(t).strip() for t in list(compliance_tag) if str(t).strip()]
        if c_tags:
            tag_clauses = [AuditLog.compliance_tags.contains([ct]) for ct in c_tags]
            clauses.append(or_(*tag_clauses))

    # Legal hold filter
    if is_legal_hold is not None and str(is_legal_hold).strip() != "":
        subq = select(AuditLegalHold.audit_log_id).where(AuditLegalHold.active == True)
        val_str = str(is_legal_hold).lower()
        if val_str in ("true", "1", "yes", "held"):
            clauses.append(AuditLog.id.in_(subq))
        elif val_str in ("false", "0", "no", "unheld"):
            clauses.append(AuditLog.id.notin_(subq))

    if correlation_id:
        clauses.append(AuditLog.correlation_id == str(correlation_id)[:64])
    if request_id:
        clauses.append(AuditLog.request_id == str(request_id)[:64])
    if actor_session_id:
        clauses.append(AuditLog.actor_session_id == str(actor_session_id)[:128])

    # M4: only seniors / admins read the whole log; everyone else their own entries
    if user is not None and user.get("role") not in ("senior_officer", "system_administrator"):
        clauses.append(AuditLog.actor_email == (user.get("email") or "\x00"))

    return clauses


async def query_audit_logs(
    *,
    q: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    actor_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    action_type: Optional[Union[str, List[str]]] = None,
    category: Optional[Union[str, List[str]]] = None,
    severity: Optional[Union[str, List[str]]] = None,
    status: Optional[Union[str, List[str]]] = None,
    source_module: Optional[Union[str, List[str]]] = None,
    target_type: Optional[Union[str, List[str]]] = None,
    compliance_tag: Optional[Union[str, List[str]]] = None,
    is_legal_hold: Optional[Union[str, bool]] = None,
    correlation_id: Optional[str] = None,
    request_id: Optional[str] = None,
    actor_session_id: Optional[str] = None,
    sort_by: str = "ts",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 25,
    user: Optional[dict] = None,
) -> dict:
    """
    Execute server-side filtered, paginated query on audit_logs table with legal holds attached.
    """
    page = max(1, page)
    page_size = max(1, min(page_size, 500))

    clauses = _build_filter_clauses(
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
        actor_session_id=actor_session_id,
        user=user,
    )

    async with async_session_factory() as session:
        # Count total matching rows
        count_stmt = select(func.count(AuditLog.id))
        if clauses:
            count_stmt = count_stmt.where(and_(*clauses))
        total_result = await session.execute(count_stmt)
        total = total_result.scalar_one()

        # Sort column map
        sort_col_map = {
            "ts": AuditLog.ts,
            "actor_email": AuditLog.actor_email,
            "actor_name": AuditLog.actor_name,
            "action": AuditLog.action,
            "action_type": AuditLog.action_type,
            "category": AuditLog.category,
            "severity": AuditLog.severity,
            "status": AuditLog.status,
            "target": AuditLog.target,
        }
        order_col = sort_col_map.get(sort_by, AuditLog.ts)
        order_expr = desc(order_col) if sort_dir.lower() == "desc" else asc(order_col)

        # Query page slice
        query_stmt = select(AuditLog)
        if clauses:
            query_stmt = query_stmt.where(and_(*clauses))
        query_stmt = query_stmt.order_by(order_expr).offset((page - 1) * page_size).limit(page_size)

        rows_result = await session.execute(query_stmt)
        rows = list(rows_result.scalars().all())

        # Efficient batch lookup of active legal holds for this page
        log_ids = [r.id for r in rows]
        hold_map = {}
        if log_ids:
            holds_stmt = select(AuditLegalHold).where(
                and_(AuditLegalHold.audit_log_id.in_(log_ids), AuditLegalHold.active == True)
            )
            holds_res = await session.execute(holds_stmt)
            for h in holds_res.scalars().all():
                hold_map[h.audit_log_id] = {
                    "id": h.id,
                    "case_number": h.case_number,
                    "hold_reason": h.hold_reason,
                    "legal_authority": h.legal_authority,
                    "contact_email": h.contact_email,
                    "placed_by": h.placed_by,
                    "placed_by_name": h.placed_by_name,
                    "placed_at": h.placed_at,
                }

        items = [_row_to_dict(r, legal_hold=hold_map.get(r.id)) for r in rows]
        total_pages = math.ceil(total / page_size) if total > 0 else 1

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "filters_applied": {
                k: v for k, v in {
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
                }.items() if v is not None
            }
        }


# ─────────────────────────────────────────────────────────────────────────────
# Summary KPI Aggregates
# ─────────────────────────────────────────────────────────────────────────────

async def get_audit_summary(*, user: Optional[dict] = None, **filters) -> dict:
    """
    Aggregate counts for the summary KPI cards (Total, Failed, Critical, Warning, Info).
    Accepts the same filters as query_audit_logs, so the cards match the visible list.
    """
    clauses = _build_filter_clauses(user=user, **filters)

    async with async_session_factory() as session:
        # Base count
        base_where = and_(*clauses) if clauses else True

        total_stmt = select(func.count(AuditLog.id)).where(base_where)
        total = (await session.execute(total_stmt)).scalar_one()

        failed_stmt = select(func.count(AuditLog.id)).where(
            and_(base_where, AuditLog.status != "success")
        )
        failed = (await session.execute(failed_stmt)).scalar_one()

        critical_stmt = select(func.count(AuditLog.id)).where(
            and_(base_where, AuditLog.severity == "critical")
        )
        critical = (await session.execute(critical_stmt)).scalar_one()

        warning_stmt = select(func.count(AuditLog.id)).where(
            and_(base_where, AuditLog.severity == "warning")
        )
        warning = (await session.execute(warning_stmt)).scalar_one()

        info_stmt = select(func.count(AuditLog.id)).where(
            and_(base_where, AuditLog.severity == "info")
        )
        info = (await session.execute(info_stmt)).scalar_one()

        # Count active legal holds
        holds_stmt = select(func.count(AuditLegalHold.id)).where(AuditLegalHold.active == True)
        active_legal_holds = (await session.execute(holds_stmt)).scalar_one()

        # Compute anomalies in the last 24h
        anom_res = await detect_anomalies(hours=24)
        anomalies_count = anom_res.get("anomaly_count", 0)

        return {
            "total_events": total,
            "failed_actions": failed,
            "critical_events": critical,
            "warnings": warning,
            "info_events": info,
            "anomalies_detected": anomalies_count,
            "active_legal_holds": active_legal_holds,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Detail & Related Events
# ─────────────────────────────────────────────────────────────────────────────

async def get_single_entry(log_id: str, user: Optional[dict] = None) -> Optional[dict]:
    """Look up a single AuditLog by ID."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(AuditLog).where(AuditLog.id == log_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            return None
        doc = _row_to_dict(row)

        # Quick hash integrity check on single row
        expected_hash = _audit_hash(doc)
        doc["is_hash_valid"] = (doc.get("hash") == expected_hash)
        return doc


async def get_related_events(
    *,
    correlation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    limit: int = 50,
    user: Optional[dict] = None,
) -> List[dict]:
    """Retrieve related audit entries grouped by correlation_id or session_id."""
    if not correlation_id and not session_id and not actor_id:
        return []

    conditions = []
    if correlation_id:
        conditions.append(AuditLog.correlation_id == correlation_id)
    if session_id:
        conditions.append(AuditLog.actor_session_id == session_id)
    if not conditions and actor_id:
        conditions.append(AuditLog.actor_id == actor_id)

    async with async_session_factory() as session:
        stmt = (
            select(AuditLog)
            .where(or_(*conditions))
            .order_by(AuditLog.ts.asc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return [_row_to_dict(r) for r in result.scalars().all()]


# ─────────────────────────────────────────────────────────────────────────────
# Chain Integrity Verification
# ─────────────────────────────────────────────────────────────────────────────

async def verify_chain_integrity(limit: int = 1000) -> dict:
    """
    Graph-aware cryptographic hash-chain integrity verification.

    Validates two properties for each audit record:
      1. Self-integrity: SHA-256(canonical fields) == stored hash.
      2. Parent-link integrity: prev_hash is either 'GENESIS' or matches a
         known hash that exists in the database — accommodating multiple
         genesis roots, timestamp collisions, and edge-synced branches.
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(AuditLog).order_by(AuditLog.ts.asc()).limit(limit)
        )
        chain = [_row_to_dict(r) for r in result.scalars().all()]

    # Build a set of all known hashes in the checked window
    known_hashes = {entry.get("hash") for entry in chain if entry.get("hash")}

    tampered_ids = []
    verified_count = 0

    for entry in chain:
        stored_hash = entry.get("hash")
        prev = entry.get("prev_hash")

        # 1. Self-integrity: recompute hash from canonical fields
        if not stored_hash:
            tampered_ids.append({
                "id": entry["id"],
                "ts": entry["ts"],
                "action": entry.get("action"),
                "reason": "missing_hash",
            })
            continue

        expected_hash = _audit_hash(entry)
        if stored_hash != expected_hash:
            tampered_ids.append({
                "id": entry["id"],
                "ts": entry["ts"],
                "action": entry.get("action"),
                "reason": "hash_mismatch",
                "expected_hash": expected_hash,
                "actual_hash": stored_hash,
            })
            continue

        # 2. Parent-link integrity: prev_hash must be GENESIS or a known hash
        if prev != "GENESIS" and prev not in known_hashes:
            tampered_ids.append({
                "id": entry["id"],
                "ts": entry["ts"],
                "action": entry.get("action"),
                "reason": "broken_parent_link",
                "prev_hash": prev,
            })
            continue

        verified_count += 1

    return {
        "total_checked": len(chain),
        "verified_count": verified_count,
        "tampered_count": len(tampered_ids),
        "is_chain_valid": len(tampered_ids) == 0,
        "tampered_entries": tampered_ids,
        "last_verified_at": datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Metadata & Distinct Values
# ─────────────────────────────────────────────────────────────────────────────

async def get_distinct_values(column_name: str) -> List[str]:
    """Retrieve distinct non-null values for a column (for filter dropdowns)."""
    if column_name == "compliance_tags":
        return ["DPDPA", "IEA_65B", "IT_ACT_2000", "LEGAL_HOLD", "GDPR"]

    col_map = {
        "action_type": AuditLog.action_type,
        "category": AuditLog.category,
        "severity": AuditLog.severity,
        "status": AuditLog.status,
        "target_type": AuditLog.target_type,
        "source_module": AuditLog.source_module,
        "actor_role": AuditLog.actor_role,
    }
    col = col_map.get(column_name)
    if col is None:
        return []

    async with async_session_factory() as session:
        stmt = (
            select(col)
            .where(col.isnot(None))
            .distinct()
            .order_by(col.asc())
        )
        result = await session.execute(stmt)
        return [r for r in result.scalars().all() if r]


async def _audit_min_days() -> int:
    from services.settings_service import AUDIT_STATUTORY_MIN_DAYS, get
    return max(AUDIT_STATUTORY_MIN_DAYS, int(await get("audit_retention_days")))


async def get_retention_policy() -> dict:
    """Audit retention policy as enforced (Admin → Policy setting, statutory floor 365 days).
    Purges are manual (Archive & Purge); there is no scheduled purge."""
    async with async_session_factory() as session:
        last = (await session.execute(
            select(AuditLog.ts).where(AuditLog.action == "audit_purge")
            .order_by(AuditLog.ts.desc()).limit(1))).scalar_one_or_none()
    return {
        "policy_name": "Kerala Police Statutory Audit Trail Policy",
        "retention_period_days": await _audit_min_days(),
        "immutable": True,
        "hash_algorithm": "SHA-256 hash chain",
        "legal_compliance": ["Bharatiya Sakshya Adhiniyam 2023, Section 63", "IT Act 2000 (Section 43A / 79)",
                             "Digital Personal Data Protection Act (DPDPA) 2023"],
        "last_purge_date": last,
        "next_scheduled_purge": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Exports (CSV & JSON)
# ─────────────────────────────────────────────────────────────────────────────

async def export_audit_logs_csv(filters: dict, user: Optional[dict] = None) -> str:
    """Generate CSV string of filtered audit logs."""
    res = await query_audit_logs(
        **filters,
        page=1,
        page_size=5000,
        sort_by="ts",
        sort_dir="desc",
        user=user,
    )
    items = res.get("items", [])

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Timestamp (UTC)",
        "Actor Email",
        "Actor Name",
        "Actor Role",
        "Action",
        "Action Type",
        "Category",
        "Severity",
        "Status",
        "Target Type",
        "Target Name",
        "Target",
        "IP Address",
        "Source Module",
        "Source Endpoint",
        "Request ID",
        "Legal Hold",
        "Compliance Tags",
        "SHA-256 Hash",
    ])

    for item in items:
        writer.writerow([
            item.get("ts", ""),
            item.get("actor_email", ""),
            item.get("actor_name", ""),
            item.get("actor_role", ""),
            item.get("action", ""),
            item.get("action_type", ""),
            item.get("category", ""),
            item.get("severity", ""),
            item.get("status", ""),
            item.get("target_type", ""),
            item.get("target_name", ""),
            item.get("target", ""),
            item.get("ip", ""),
            item.get("source_module", ""),
            item.get("source_endpoint", ""),
            item.get("request_id", ""),
            "YES" if item.get("is_legal_hold") else "NO",
            ", ".join(item.get("compliance_tags", [])),
            item.get("hash", ""),
        ])

    return output.getvalue()


async def export_audit_logs_json(filters: dict, user: Optional[dict] = None) -> List[dict]:
    """Generate JSON list of filtered audit logs."""
    res = await query_audit_logs(
        **filters,
        page=1,
        page_size=5000,
        sort_by="ts",
        sort_dir="desc",
        user=user,
    )
    return res.get("items", [])


# ─────────────────────────────────────────────────────────────────────────────
# Filter Preset CRUD
# ─────────────────────────────────────────────────────────────────────────────

async def save_preset(
    name: str,
    filters: dict,
    owner_id: str,
    is_shared: bool = False,
) -> dict:
    """Save or update an audit filter preset."""
    preset_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    entry = {
        "id": preset_id,
        "name": name,
        "owner_id": owner_id,
        "filters": filters,
        "is_shared": is_shared,
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    async with async_session_factory() as session:
        session.add(AuditFilterPreset(**entry))
        await session.commit()
    return entry


async def list_presets(user_id: str) -> List[dict]:
    """List presets owned by the user or marked as shared."""
    async with async_session_factory() as session:
        stmt = (
            select(AuditFilterPreset)
            .where(
                or_(
                    AuditFilterPreset.owner_id == user_id,
                    AuditFilterPreset.is_shared == True,
                )
            )
            .order_by(AuditFilterPreset.created_at.desc())
        )
        result = await session.execute(stmt)
        return [
            {
                "id": p.id,
                "name": p.name,
                "owner_id": p.owner_id,
                "filters": p.filters,
                "is_shared": p.is_shared,
                "created_at": p.created_at,
                "updated_at": p.updated_at,
            }
            for p in result.scalars().all()
        ]


async def delete_preset(preset_id: str, user_id: str) -> bool:
    """Delete a preset owned by the user."""
    async with async_session_factory() as session:
        stmt = (
            delete(AuditFilterPreset)
            .where(
                and_(
                    AuditFilterPreset.id == preset_id,
                    AuditFilterPreset.owner_id == user_id,
                )
            )
        )
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount > 0


# ─────────────────────────────────────────────────────────────────────────────
# Rule-Based Anomaly Detection (8 Patterns)
# ─────────────────────────────────────────────────────────────────────────────

async def detect_anomalies(hours: int = 24) -> dict:
    """
    Evaluate 8 rule-based security anomaly patterns over the last N hours.
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    async with async_session_factory() as session:
        result = await session.execute(
            select(AuditLog).where(AuditLog.ts >= since).order_by(AuditLog.ts.asc())
        )
        logs = [_row_to_dict(r) for r in result.scalars().all()]

    anomalies = []

    # Pattern 1: Multiple Failed Logins (> 3 failures within 10 min for same actor/IP)
    failed_logins = [l for l in logs if l.get("status") != "success" or l.get("action") in ("login_failed", "login_failure")]
    by_actor_fail = {}
    for f in failed_logins:
        key = f.get("actor_email") or f.get("ip") or "unknown"
        by_actor_fail.setdefault(key, []).append(f)
    for key, fails in by_actor_fail.items():
        if len(fails) >= 3:
            anomalies.append({
                "id": f"anom-login-{key}-{len(fails)}",
                "pattern": "brute_force_or_failed_logins",
                "severity": "critical",
                "title": f"Multiple Failed Logins ({len(fails)} attempts)",
                "description": f"Repeated authentication failures detected for identifier '{key}'.",
                "actor": key,
                "first_seen": fails[0].get("ts"),
                "last_seen": fails[-1].get("ts"),
                "event_ids": [f["id"] for f in fails[:5]],
            })

    # Pattern 2: Off-Hours Sensitive Actions (Critical actions between 22:00 - 05:00 IST = UTC 16:30 - 23:30)
    for l in logs:
        if l.get("severity") == "critical":
            try:
                dt = datetime.fromisoformat(l["ts"].replace("Z", "+00:00"))
                # IST is UTC+5:30
                ist_hour = (dt.hour + 5 + (dt.minute + 30) // 60) % 24
                if ist_hour >= 22 or ist_hour < 5:
                    anomalies.append({
                        "id": f"anom-offhours-{l['id']}",
                        "pattern": "off_hours_critical_action",
                        "severity": "warning",
                        "title": f"Off-Hours Critical Operation ({l['action']})",
                        "description": f"Critical action '{l['action']}' executed at {ist_hour:02d}:00 IST by {l.get('actor_email')}.",
                        "actor": l.get("actor_email"),
                        "first_seen": l.get("ts"),
                        "last_seen": l.get("ts"),
                        "event_ids": [l["id"]],
                    })
            except Exception:
                pass

    # Pattern 3: Mass Export Volume (>= 3 exports in 1 hour)
    exports = [l for l in logs if l.get("action_type") == "export" or "export" in l.get("action", "")]
    by_actor_export = {}
    for ex in exports:
        act = ex.get("actor_email") or "unknown"
        by_actor_export.setdefault(act, []).append(ex)
    for act, ex_list in by_actor_export.items():
        if len(ex_list) >= 3:
            anomalies.append({
                "id": f"anom-export-surge-{act}",
                "pattern": "mass_export_volume",
                "severity": "warning",
                "title": f"High Volume Export Activity ({len(ex_list)} exports)",
                "description": f"Officer '{act}' initiated {len(ex_list)} data/evidence exports in monitored window.",
                "actor": act,
                "first_seen": ex_list[0].get("ts"),
                "last_seen": ex_list[-1].get("ts"),
                "event_ids": [e["id"] for e in ex_list[:5]],
            })

    # Pattern 4: Multi-IP Travel Anomaly (same actor with > 1 distinct IP)
    by_actor_ips = {}
    for l in logs:
        act = l.get("actor_email")
        ip = l.get("ip")
        if act and ip and ip != "-":
            by_actor_ips.setdefault(act, set()).add(ip)
    for act, ips in by_actor_ips.items():
        if len(ips) >= 2:
            anomalies.append({
                "id": f"anom-ip-travel-{act}",
                "pattern": "concurrent_multi_ip_access",
                "severity": "warning",
                "title": f"Multiple Source IPs ({len(ips)} distinct IPs)",
                "description": f"Actor '{act}' accessed system from multiple IP addresses: {', '.join(ips)}.",
                "actor": act,
                "first_seen": logs[0].get("ts") if logs else "",
                "last_seen": logs[-1].get("ts") if logs else "",
                "event_ids": [],
            })

    # Pattern 5: Watchlist Surge (>= 5 watchlist actions)
    watchlist_acts = [l for l in logs if "watchlist" in l.get("action", "")]
    if len(watchlist_acts) >= 5:
        anomalies.append({
            "id": "anom-watchlist-surge",
            "pattern": "watchlist_surge",
            "severity": "info",
            "title": f"Watchlist Modification Surge ({len(watchlist_acts)} actions)",
            "description": f"High rate of watchlist updates ({len(watchlist_acts)} changes) detected.",
            "actor": "Various",
            "first_seen": watchlist_acts[0].get("ts"),
            "last_seen": watchlist_acts[-1].get("ts"),
            "event_ids": [w["id"] for w in watchlist_acts[:5]],
        })

    # Pattern 6: Privilege Escalation Attempt (user role change to administrator or permission modification)
    priv_escalations = [
        l for l in logs
        if (l.get("action") in ("user_patch", "user_create", "permission_change") and
            "system_administrator" in str(l.get("after_values", {})))
        or (l.get("action") == "permission_change" and l.get("status") != "success")
    ]
    for pe in priv_escalations:
        anomalies.append({
            "id": f"anom-priv-esc-{pe['id']}",
            "pattern": "privilege_escalation_attempt",
            "severity": "critical",
            "title": f"Privilege Escalation Detected ({pe['action']})",
            "description": f"Actor '{pe.get('actor_email')}' modified administrative permissions or escalated roles.",
            "actor": pe.get("actor_email"),
            "first_seen": pe.get("ts"),
            "last_seen": pe.get("ts"),
            "event_ids": [pe["id"]],
        })

    # Pattern 7: Mass Deletions (> 3 delete actions within 15 min)
    deletes = [l for l in logs if l.get("action_type") == "delete" or "delete" in l.get("action", "") or "suspend" in l.get("action", "")]
    if len(deletes) >= 3:
        anomalies.append({
            "id": "anom-mass-deletes",
            "pattern": "mass_deletion_surge",
            "severity": "critical",
            "title": f"Mass Deletion Surge ({len(deletes)} records)",
            "description": f"High volume of destructive delete/suspend actions ({len(deletes)} events) detected in monitored timeframe.",
            "actor": "Various",
            "first_seen": deletes[0].get("ts"),
            "last_seen": deletes[-1].get("ts"),
            "event_ids": [d["id"] for d in deletes[:5]],
        })

    # Pattern 8: Cryptographic Chain Discontinuity (fast probe)
    try:
        chain_probe = await verify_chain_integrity(limit=100)
        if not chain_probe.get("is_chain_valid", True):
            anomalies.append({
                "id": "anom-chain-breach",
                "pattern": "cryptographic_chain_breach",
                "severity": "critical",
                "title": "Cryptographic Hash Chain Discontinuity Detected!",
                "description": f"Tamper evidence alert: {chain_probe.get('tampered_count', 0)} block(s) failed sequential verification.",
                "actor": "System Integrity Monitor",
                "first_seen": datetime.now(timezone.utc).isoformat(),
                "last_seen": datetime.now(timezone.utc).isoformat(),
                "event_ids": [t.get("id") for t in chain_probe.get("tampered_entries", [])[:5]],
            })
    except Exception as e:
        logger.debug("Chain probe skipped: %s", e)

    return {
        "anomalies": anomalies,
        "anomaly_count": len(anomalies),
        "scanned_period_hours": hours,
        "scanned_events_count": len(logs),
    }

    return {
        "anomalies": anomalies,
        "anomaly_count": len(anomalies),
        "scanned_period_hours": hours,
        "scanned_events_count": len(logs),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Section 65B Certified PDF Export Generator
# ─────────────────────────────────────────────────────────────────────────────

def generate_audit_section65b_pdf(items: List[dict], user: dict, filter_desc: str = "") -> bytes:
    """
    Generate statutory Electronic Evidence Certificate pursuant to Section 65B(4)
    of the Indian Evidence Act, 1872 for certified audit log slices.
    """
    def _s(x):
        return str(x or "").encode("latin-1", "replace").decode("latin-1")

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Header
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, "CERTIFICATE UNDER SECTION 65B(4)", ln=1, align="C")
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 5, "THE INDIAN EVIDENCE ACT, 1872", ln=1, align="C")
    pdf.set_font("Helvetica", "I", 8.5)
    pdf.cell(0, 5, "Official Statutory Electronic Record Certification - Kerala Police DVAS", ln=1, align="C")
    pdf.ln(3)

    pdf.set_draw_color(100, 100, 100)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(4)

    # Authority Box
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(0, 5, "1. CERTIFYING AUTHORITY & CUSTODY", ln=1)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.multi_cell(
        0, 4.2,
        "Kerala Police Department - Drone Video Analytics System (DVAS)\n"
        "State Police Command & Control Centre, Police Headquarters, Thiruvananthapuram\n"
        f"Certifying Officer: {_s(user.get('name') or user.get('email'))} ({_s(user.get('role', 'Authorized Officer'))})\n"
        f"Jurisdiction Scope: {_s(user.get('jurisdiction') or 'State HQ')}\n"
        f"Certification Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )
    pdf.ln(3)

    # Statutory Declaration
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(0, 5, "2. STATUTORY DECLARATION PURSUANT TO SECTION 65B(4)", ln=1)
    pdf.set_font("Helvetica", "", 8)
    pdf.multi_cell(
        0, 4,
        "I, the undersigned certifying officer, do hereby solemnly certify and state as follows:\n"
        "1. The electronic audit records produced herein were generated and stored by the DVAS computer system during the ordinary course of official Kerala Police activities.\n"
        "2. Throughout the material period, the computer system and its cryptographic SHA-256 hash-chaining mechanism operated properly and securely without unauthorized interference.\n"
        "3. The audit entries reproduced in this document represent a true and faithful copy of the electronic record stored in the secure, tamper-evident evidence database.\n"
        f"4. Total Records Certified: {len(items)} entries. Filter Context: {_s(filter_desc or 'Standard Statutory Slice')}"
    )
    pdf.ln(3)

    # Cryptographic Hash-Chain Proof
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(0, 5, "3. CRYPTOGRAPHIC INTEGRITY PROOF (SHA-256 CHAIN)", ln=1)
    pdf.set_font("Helvetica", "", 8)
    if items:
        first_hash = items[-1].get("hash", "GENESIS")
        last_hash = items[0].get("hash", "-")
        pdf.multi_cell(
            0, 3.8,
            f"Chain Anchor (Oldest in Slice): {_s(first_hash)}\n"
            f"Chain Terminal (Latest in Slice): {_s(last_hash)}\n"
            "Integrity Status: Cryptographically verified against Merkle hash chain."
        )
    else:
        pdf.cell(0, 5, "No records in selected slice.", ln=1)
    pdf.ln(3)

    # Event Table
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(0, 5, f"4. CERTIFIED AUDIT LOG ENTRIES (Sample {min(len(items), 25)} of {len(items)})", ln=1)
    
    # Table Header
    pdf.set_fill_color(230, 235, 245)
    pdf.set_font("Helvetica", "B", 7.5)
    pdf.cell(32, 5.5, "Timestamp (UTC)", 1, 0, "L", fill=True)
    pdf.cell(42, 5.5, "Actor", 1, 0, "L", fill=True)
    pdf.cell(35, 5.5, "Action", 1, 0, "L", fill=True)
    pdf.cell(36, 5.5, "Target / Resource", 1, 0, "L", fill=True)
    pdf.cell(20, 5.5, "Status", 1, 0, "C", fill=True)
    pdf.cell(25, 5.5, "IP Address", 1, 1, "C", fill=True)

    pdf.set_font("Helvetica", "", 7)
    for row in items[:25]:
        ts_str = _s(row.get("ts", "")[:19])
        actor_str = _s((row.get("actor_name") or row.get("actor_email") or "")[:24])
        action_str = _s(row.get("action", "")[:20])
        target_str = _s((row.get("target_name") or row.get("target") or "")[:20])
        status_str = _s(row.get("status", "success")[:10])
        ip_str = _s(row.get("ip", "-")[:15])

        pdf.cell(32, 5, ts_str, 1, 0, "L")
        pdf.cell(42, 5, actor_str, 1, 0, "L")
        pdf.cell(35, 5, action_str, 1, 0, "L")
        pdf.cell(36, 5, target_str, 1, 0, "L")
        pdf.cell(20, 5, status_str, 1, 0, "C")
        pdf.cell(25, 5, ip_str, 1, 1, "C")

    pdf.ln(5)

    # Signature & Digital Verification Block
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.cell(90, 4.5, "___________________________________", ln=0)
    pdf.cell(0, 4.5, "___________________________________", ln=1, align="R")
    pdf.cell(90, 4, "Signature of Certifying Officer", ln=0)
    pdf.cell(0, 4, "Digital Verification & Hash Seal", ln=1, align="R")
    pdf.set_font("Helvetica", "", 7.5)
    pdf.cell(90, 3.5, f"{_s(user.get('name') or user.get('email'))}", ln=0)
    pdf.cell(0, 3.5, f"DVAS-65B-CERT-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}", ln=1, align="R")

    return bytes(pdf.output(dest="S"))


# ─────────────────────────────────────────────────────────────────────────────
# Activity Trend & Analytics
# ─────────────────────────────────────────────────────────────────────────────

async def get_audit_trends(range_val: str = "24h", user: Optional[dict] = None) -> dict:
    """Compute hourly or daily activity trend histograms and top actor stats.

    range_val: "today" (since 00:00 IST), "24h", "7d" or "30d".
    """
    if range_val not in ("today", "24h", "7d", "30d"):
        raise ValueError("range must be one of today, 24h, 7d, 30d")
    hours = {"today": 24, "24h": 24, "7d": 24 * 7, "30d": 24 * 30}[range_val]

    now_utc = datetime.now(timezone.utc)
    if range_val == "today":
        ist = timezone(timedelta(hours=5, minutes=30))
        since_dt = now_utc.astimezone(ist).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        since_dt = now_utc - timedelta(hours=hours)
    since = since_dt.astimezone(timezone.utc).isoformat()
    clauses = [AuditLog.ts >= since]

    async with async_session_factory() as session:
        stmt = (
            select(AuditLog)
            .where(and_(*clauses))
            .order_by(AuditLog.ts.asc())
        )
        result = await session.execute(stmt)
        logs = [_row_to_dict(r) for r in result.scalars().all()]

    timeline_map = {}
    actors_map = {}
    action_types_map = {}
    severity_map = {"critical": 0, "warning": 0, "info": 0}
    status_map = {"success": 0, "failure": 0}

    is_daily = hours > 24

    for l in logs:
        ts = l.get("ts", "")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            bucket_key = dt.strftime("%d %b") if is_daily else dt.strftime("%H:00")
        except Exception:
            bucket_key = "Other"

        bucket = timeline_map.setdefault(bucket_key, {"time": bucket_key, "total": 0, "failed": 0, "critical": 0})
        bucket["total"] += 1

        status = (l.get("status") or "success").lower()
        if status != "success":
            bucket["failed"] += 1
            status_map["failure"] += 1
        else:
            status_map["success"] += 1

        sev = (l.get("severity") or "info").lower()
        if sev == "critical":
            bucket["critical"] += 1
        severity_map[sev] = severity_map.get(sev, 0) + 1

        act_name = l.get("actor_name") or l.get("actor_email") or "System"
        act_role = l.get("actor_role") or "internal"
        actors_map.setdefault(act_name, {"actor": act_name, "role": act_role, "count": 0})["count"] += 1

        at = l.get("action_type") or "system"
        action_types_map[at] = action_types_map.get(at, 0) + 1

    top_actors = sorted(actors_map.values(), key=lambda x: x["count"], reverse=True)[:5]
    timeline = list(timeline_map.values())
    total_scanned = len(logs)
    failed_rate = round((status_map["failure"] / total_scanned * 100), 1) if total_scanned > 0 else 0.0

    return {
        "range": range_val,
        "total_scanned": total_scanned,
        "timeline": timeline,
        "top_actors": top_actors,
        "by_action_type": action_types_map,
        "by_severity": severity_map,
        "by_status": status_map,
        "failed_rate_percent": failed_rate,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Access & Meta-Audit Logging (Audit of the Auditors)
# ─────────────────────────────────────────────────────────────────────────────

_ACCESS_LOG_BUCKET: Dict[str, float] = {}

async def log_audit_access(actor: dict, request=None, referrer: Optional[str] = None) -> bool:
    """Log access to the Audit Log Explorer (audit of the auditors), debounced to 5 mins per actor."""
    actor_id = actor.get("id") or actor.get("email") or "anon"
    now = datetime.now(timezone.utc).timestamp()
    last_logged = _ACCESS_LOG_BUCKET.get(actor_id, 0)

    # 5-minute debounce per user session to avoid flooding the log
    if now - last_logged < 300:
        return False

    _ACCESS_LOG_BUCKET[actor_id] = now
    ref_desc = f" (Referrer: {referrer})" if referrer else ""
    await write_audit_v2(
        actor=actor,
        action="audit_log_view",
        action_type="read",
        category="security",
        severity="info",
        target="Audit Log Explorer",
        target_type="system",
        target_id="audit_explorer",
        target_name="Audit Log Explorer UI",
        details=f"Officer accessed full Audit Log Explorer{ref_desc}",
        source_module="dashboard",
        source_endpoint="GET /api/audit-logs",
        request=request,
    )
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Legal Hold & Forensic Chain-of-Custody
# ─────────────────────────────────────────────────────────────────────────────

async def place_legal_hold(*, audit_log_id: str, case_number: str, reason: str, actor: dict,
                           legal_authority: Optional[str] = None, contact_email: Optional[str] = None) -> dict:
    """Place a statutory legal hold on an audit entry, preventing deletion under retention policies."""
    async with async_session_factory() as session:
        log_res = await session.execute(select(AuditLog).where(AuditLog.id == audit_log_id))
        log_entry = log_res.scalar_one_or_none()
        if not log_entry:
            raise ValueError(f"Audit log entry {audit_log_id} not found")

        existing_res = await session.execute(
            select(AuditLegalHold).where(
                and_(AuditLegalHold.audit_log_id == audit_log_id, AuditLegalHold.active == True)
            )
        )
        if existing_res.scalar_one_or_none():
            raise ValueError("Audit log entry already has an active legal hold")

        hold_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()
        hold = AuditLegalHold(
            id=hold_id,
            audit_log_id=audit_log_id,
            case_number=case_number,
            hold_reason=reason,
            placed_by=actor.get("id") or "unknown",
            placed_by_name=actor.get("name") or actor.get("email") or "Officer",
            placed_at=now_iso,
            active=True,
            legal_authority=legal_authority,
            contact_email=contact_email,
        )
        session.add(hold)
        await session.commit()

    await write_audit_v2(
        actor=actor,
        action="audit_legal_hold_place",
        action_type="create",
        category="compliance",
        severity="warning",
        target=f"Audit Log {audit_log_id[:8]}",
        target_type="audit_log",
        target_id=audit_log_id,
        target_name=f"Legal Hold (Case {case_number})",
        details=f"Legal hold placed under case {case_number}"
                + (f" (authority: {legal_authority})" if legal_authority else "") + f". Reason: {reason}",
        after_values={"case_number": case_number, "hold_reason": reason,
                      "legal_authority": legal_authority, "contact_email": contact_email},
        source_module="dashboard",
        source_endpoint=f"POST /api/audit-logs/{audit_log_id}/legal-hold",
    )
    return {
        "id": hold_id,
        "audit_log_id": audit_log_id,
        "case_number": case_number,
        "hold_reason": reason,
        "legal_authority": legal_authority,
        "contact_email": contact_email,
        "placed_by": actor.get("id"),
        "placed_at": now_iso,
        "active": True,
    }


async def release_legal_hold(*, audit_log_id: str, release_reason: str, actor: dict) -> dict:
    """Release an active legal hold on an audit entry."""
    async with async_session_factory() as session:
        res = await session.execute(
            select(AuditLegalHold).where(
                and_(AuditLegalHold.audit_log_id == audit_log_id, AuditLegalHold.active == True)
            )
        )
        hold = res.scalar_one_or_none()
        if not hold:
            raise ValueError("No active legal hold found for this audit log entry")

        now_iso = datetime.now(timezone.utc).isoformat()
        hold.active = False
        hold.released_by = actor.get("id")
        hold.released_at = now_iso
        hold.release_reason = release_reason
        await session.commit()

    await write_audit_v2(
        actor=actor,
        action="audit_legal_hold_release",
        action_type="delete",
        category="compliance",
        severity="warning",
        target=f"Audit Log {audit_log_id[:8]}",
        target_type="audit_log",
        target_id=audit_log_id,
        target_name="Legal Hold Released",
        details=f"Legal hold released. Justification: {release_reason}",
        source_module="dashboard",
        source_endpoint=f"DELETE /api/audit-logs/{audit_log_id}/legal-hold",
    )
    return {"ok": True, "released_at": now_iso}


async def get_chain_of_custody(log_id: str) -> dict:
    """Generate cryptographic provenance and chain-of-custody envelope for legal/forensic use."""
    async with async_session_factory() as session:
        log_res = await session.execute(select(AuditLog).where(AuditLog.id == log_id))
        log = log_res.scalar_one_or_none()
        if not log:
            raise ValueError("Audit log entry not found")

        hold_res = await session.execute(
            select(AuditLegalHold).where(
                and_(AuditLegalHold.audit_log_id == log_id, AuditLegalHold.active == True)
            )
        )
        hold = hold_res.scalar_one_or_none()

    doc = _row_to_dict(log, legal_hold={
        "case_number": hold.case_number,
        "hold_reason": hold.hold_reason,
        "placed_by": hold.placed_by_name or hold.placed_by,
        "placed_at": hold.placed_at,
    } if hold else None)

    expected_hash = _audit_hash(doc)
    is_valid = (doc.get("hash") == expected_hash)

    return {
        "log_id": log.id,
        "timestamp_utc": log.ts,
        "actor": {
            "id": log.actor_id,
            "name": log.actor_name,
            "email": log.actor_email,
            "role": log.actor_role,
            "ip": log.ip,
            "session_id": log.actor_session_id,
            "device_id": log.actor_device_id,
        },
        "action": log.action,
        "action_type": log.action_type,
        "target": {
            "type": log.target_type,
            "id": log.target_id,
            "name": log.target_name or log.target,
        },
        "cryptographic_proof": {
            "current_hash_sha256": log.hash,
            "previous_hash_sha256": log.prev_hash,
            "expected_hash_sha256": expected_hash,
            "hash_matches_content": is_valid,
            "chain_algorithm": "SHA-256 Merkle Chain",
            "statutory_compliance": "Indian Evidence Act Section 65B(4)",
            "certifying_authority": "Kerala Police Department - DVAS Command Centre",
        },
        "legal_hold": doc.get("legal_hold"),
        "compliance_tags": doc.get("compliance_tags", []),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Statutory Archive & Purge Engine
# ─────────────────────────────────────────────────────────────────────────────

async def dry_run_purge(older_than_days: int = 365, user: Optional[dict] = None) -> dict:
    """Dry-run calculation of purgeable records older than retention threshold."""
    min_days = await _audit_min_days()
    if older_than_days < min_days:
        raise ValueError(f"Retention policy mandates at least {min_days} days before purge.")

    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
    async with async_session_factory() as session:
        tot_res = await session.execute(
            select(func.count(AuditLog.id)).where(AuditLog.ts <= cutoff)
        )
        total_matching = tot_res.scalar_one()

        subq = select(AuditLegalHold.audit_log_id).where(AuditLegalHold.active == True)
        held_res = await session.execute(
            select(func.count(AuditLog.id)).where(
                and_(AuditLog.ts <= cutoff, AuditLog.id.in_(subq))
            )
        )
        legal_held_count = held_res.scalar_one()
        purgeable_count = max(0, total_matching - legal_held_count)

    return {
        "retention_period_days": older_than_days,
        "cutoff_timestamp": cutoff,
        "total_records_in_window": total_matching,
        "legal_held_records_preserved": legal_held_count,
        "purgeable_records_count": purgeable_count,
        "can_purge": purgeable_count > 0,
        "statutory_note": f"Records younger than {cutoff} or marked under Legal Hold will be strictly preserved.",
    }


async def export_archive_bundle(older_than_days: int = 365, user: Optional[dict] = None) -> dict:
    """Generate downloadable JSON archive bundle of records to be purged, with SHA-256 checksum."""
    min_days = await _audit_min_days()
    if older_than_days < min_days:
        raise ValueError(f"Minimum retention is {min_days} days.")

    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
    async with async_session_factory() as session:
        subq = select(AuditLegalHold.audit_log_id).where(AuditLegalHold.active == True)
        stmt = (
            select(AuditLog)
            .where(and_(AuditLog.ts <= cutoff, AuditLog.id.notin_(subq)))
            .order_by(AuditLog.ts.asc())
        )
        res = await session.execute(stmt)
        rows = res.scalars().all()
        items = [_row_to_dict(r) for r in rows]

    serialized = json.dumps(items, sort_keys=True)
    bundle_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    archive_manifest = {
        "archive_id": str(uuid.uuid4()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": user.get("email") if user else "system_administrator",
        "retention_policy_days": older_than_days,
        "cutoff_timestamp": cutoff,
        "record_count": len(items),
        "manifest_sha256": bundle_hash,
        "records": items,
    }
    return archive_manifest


async def execute_statutory_purge(*, older_than_days: int = 365, confirmation_phrase: str, actor: dict) -> dict:
    """Execute controlled statutory purge of expired audit logs that are NOT under legal hold."""
    if confirmation_phrase != "CONFIRM STATUTORY PURGE":
        raise ValueError("Invalid confirmation phrase. Must be exactly 'CONFIRM STATUTORY PURGE'.")

    if actor.get("role") != "system_administrator":
        raise PermissionError("Only system_administrator can execute statutory purge.")

    min_days = await _audit_min_days()
    if older_than_days < min_days:
        raise ValueError(f"Cannot purge records within the {min_days}-day retention period.")

    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()

    async with async_session_factory() as session:
        await session.execute(text("SET LOCAL dvas.allow_audit_purge = 'true';"))

        subq = select(AuditLegalHold.audit_log_id).where(AuditLegalHold.active == True)
        del_stmt = (
            delete(AuditLog)
            .where(and_(AuditLog.ts <= cutoff, AuditLog.id.notin_(subq)))
        )
        res = await session.execute(del_stmt)
        deleted_count = res.rowcount
        await session.commit()

    await write_audit_v2(
        actor=actor,
        action="audit_purge",
        action_type="delete",
        category="compliance",
        severity="critical",
        target=f"Statutory Purge ({deleted_count} records)",
        target_type="system",
        target_name="Audit Log Purge",
        details=f"Statutory purge executed by {actor.get('email')}. Deleted {deleted_count} logs older than {cutoff}. Legal holds preserved.",
        source_module="dashboard",
        source_endpoint="POST /api/audit-logs/archive/execute",
    )

    return {
        "success": True,
        "purged_count": deleted_count,
        "cutoff_timestamp": cutoff,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }

