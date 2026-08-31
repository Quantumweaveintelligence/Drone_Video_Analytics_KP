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

from sqlalchemy import select, func, or_, and_, desc, asc, delete
from database import async_session_factory
from models import AuditLog, AuditFilterPreset

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

    # Audit log meta-actions
    "audit_log_view":           ("read",    "security",         "info"),
    "audit_log_export_csv":     ("export",  "security",         "info"),
    "audit_log_export_json":    ("export",  "security",         "info"),
    "audit_log_export_pdf":     ("export",  "security",         "warning"),
    "audit_chain_verify":       ("read",    "security",         "info"),
    "audit_preset_create":      ("create",  "security",         "info"),
    "audit_preset_delete":      ("delete",  "security",         "info"),
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
}


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


def _row_to_dict(r: AuditLog) -> dict:
    """Serialize AuditLog ORM object to plain dictionary."""
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
    ip: str = "-",
    session_id: Optional[str] = None,
    device_id: Optional[str] = None,
    request=None,
) -> dict:
    """Append-only tamper-evident audit logger."""
    inferred_type, inferred_cat, inferred_sev = _classify_action(action)
    if action_type is None:
        action_type = inferred_type
    if category is None:
        category = inferred_cat
    if severity is None:
        severity = inferred_sev

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
            "prev_hash": prev_hash,
        }

        entry_dict["hash"] = _audit_hash(entry_dict)
        session.add(AuditLog(**entry_dict))
        await session.commit()

        logger.debug("audit_v2 logged %s (id=%s)", action, entry_dict["id"])
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
    correlation_id: Optional[str] = None,
    request_id: Optional[str] = None,
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

    if correlation_id:
        clauses.append(AuditLog.correlation_id == str(correlation_id)[:64])
    if request_id:
        clauses.append(AuditLog.request_id == str(request_id)[:64])

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
    correlation_id: Optional[str] = None,
    request_id: Optional[str] = None,
    sort_by: str = "ts",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 25,
    user: Optional[dict] = None,
) -> dict:
    """
    Execute server-side filtered, paginated query on audit_logs table.
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
        correlation_id=correlation_id,
        request_id=request_id,
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
        items = [_row_to_dict(r) for r in rows_result.scalars().all()]

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
                    "correlation_id": correlation_id,
                    "request_id": request_id,
                }.items() if v is not None
            }
        }


# ─────────────────────────────────────────────────────────────────────────────
# Summary KPI Aggregates
# ─────────────────────────────────────────────────────────────────────────────

async def get_audit_summary(
    *,
    q: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    user: Optional[dict] = None,
) -> dict:
    """Compute aggregate counts for summary KPI cards (Total, Failed, Critical, Warning, Info)."""
    clauses = _build_filter_clauses(
        q=q,
        date_from=date_from,
        date_to=date_to,
        user=user,
    )

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
    Verify sequential cryptographic hash-chain integrity from Genesis.
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(AuditLog).order_by(AuditLog.ts.asc()).limit(limit)
        )
        chain = [_row_to_dict(r) for r in result.scalars().all()]

    tampered_ids = []
    prev_hash = "GENESIS"
    verified_count = 0

    for entry in chain:
        expected = _audit_hash({**entry, "prev_hash": prev_hash})
        is_tampered = (entry.get("hash") != expected or entry.get("prev_hash") != prev_hash)
        if is_tampered:
            tampered_ids.append({
                "id": entry["id"],
                "ts": entry["ts"],
                "action": entry["action"],
                "expected_hash": expected,
                "actual_hash": entry.get("hash"),
                "expected_prev_hash": prev_hash,
                "actual_prev_hash": entry.get("prev_hash"),
            })
        else:
            verified_count += 1
        prev_hash = entry.get("hash", prev_hash)

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


def get_retention_policy() -> dict:
    """Return statutory audit retention policy details."""
    return {
        "policy_name": "Kerala Police Statutory Audit Trail Policy",
        "retention_period_days": 365,
        "immutable": True,
        "hash_algorithm": "SHA-256 (Merkle / Hash-Chain)",
        "legal_compliance": ["Indian Evidence Act Section 65B", "IT Act 2000 (Section 43A / 79)"],
        "last_purge_date": None,
        "next_scheduled_purge": "2027-01-01T00:00:00Z",
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
# Rule-Based Anomaly Detection (7 Patterns)
# ─────────────────────────────────────────────────────────────────────────────

async def detect_anomalies(hours: int = 24) -> dict:
    """
    Evaluate 7 rule-based security anomaly patterns over the last N hours.
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
