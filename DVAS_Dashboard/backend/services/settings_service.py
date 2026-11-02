"""
Run-time settings the backend enforces (Admin → Policy / System Settings).

Only settings with an enforcement path are editable here; each definition names
the code that reads it. Values owned elsewhere (Keycloak token lifetime, TLS at
the RunPod / Cloudflare edge) are reported read-only by routers/admin.py.
"""
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import select

from core.config import EVIDENCE_RETENTION_DAYS
from database import async_session_factory
from models import SystemSetting

logger = logging.getLogger("dvas.settings")

AUDIT_STATUTORY_MIN_DAYS = 365

DEFINITIONS: Dict[str, dict] = {
    "evidence_retention_days": {
        "label": "Evidence retention (days)", "type": "int", "min": 30, "max": 3650,
        "default": EVIDENCE_RETENTION_DAYS,
        "enforced_by": "New evidence: retention date and vault object lock; retention job disposal",
    },
    "report_retention_days": {
        "label": "Report PDF retention (days)", "type": "int", "min": 30, "max": 3650, "default": 365,
        "enforced_by": "Retention job deletes stored report PDFs older than this",
    },
    "audit_retention_days": {
        "label": "Audit log retention (days)", "type": "int", "min": AUDIT_STATUTORY_MIN_DAYS, "max": 3650,
        "default": AUDIT_STATUTORY_MIN_DAYS,
        "enforced_by": "Minimum age for the audit archive & purge (statutory floor 365)",
    },
    "retention_job_time_ist": {
        "label": "Retention job runs at (IST, HH:MM)", "type": "time", "default": "02:30",
        "enforced_by": "Daily evidence / report retention job",
    },
}

_cache: Dict[str, Any] = {}
_cache_at = 0.0
_CACHE_S = 30


def validate(key: str, value: Any) -> Any:
    d = DEFINITIONS.get(key)
    if d is None:
        raise ValueError(f"Unknown setting {key}")
    if d["type"] == "int":
        try:
            v = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{d['label']}: a whole number is required")
        if not d["min"] <= v <= d["max"]:
            raise ValueError(f"{d['label']}: must be between {d['min']} and {d['max']}")
        return v
    if d["type"] == "time":
        if not isinstance(value, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError(f"{d['label']}: use HH:MM (24 h)")
        return value
    return value


async def get_all() -> Dict[str, Any]:
    """All settings with defaults applied (cached 30 s)."""
    global _cache, _cache_at
    if _cache and time.monotonic() - _cache_at < _CACHE_S:
        return dict(_cache)
    values = {k: d["default"] for k, d in DEFINITIONS.items()}
    try:
        async with async_session_factory() as session:
            for row in (await session.execute(select(SystemSetting))).scalars().all():
                if row.key in DEFINITIONS:
                    try:
                        values[row.key] = validate(row.key, row.value)
                    except ValueError:
                        logger.error("Stored setting %s is invalid; using the default", row.key)
    except Exception as exc:
        logger.warning("Settings unavailable, using defaults: %s", exc)
        return values
    _cache, _cache_at = values, time.monotonic()
    return dict(values)


async def get(key: str) -> Any:
    return (await get_all())[key]


async def update(changes: Dict[str, Any], user: dict) -> Dict[str, Dict[str, Any]]:
    """Validate and store changes. Returns {key: {"before", "after"}} for the audit entry."""
    global _cache_at
    clean = {k: validate(k, v) for k, v in changes.items()}
    current = await get_all()
    diff = {k: {"before": current[k], "after": v} for k, v in clean.items() if current[k] != v}
    if not diff:
        return {}
    now = datetime.now(timezone.utc).isoformat()
    async with async_session_factory() as session:
        for k, v in clean.items():
            row = await session.get(SystemSetting, k)
            if row is None:
                session.add(SystemSetting(key=k, value=v, updated_by=user.get("email"), updated_at=now))
            else:
                row.value, row.updated_by, row.updated_at = v, user.get("email"), now
        await session.commit()
    _cache_at = 0.0
    return diff
