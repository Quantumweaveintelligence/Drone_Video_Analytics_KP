"""
Read-only access to the analytics DB for reports (R2 / Q6).

Connects as the SELECT-only role (ANALYTICS_REPORT_DATABASE_URL). Every session is
also forced read-only with a 30 s statement limit, so even the fallback URL
(ANALYTICS_DATABASE_URL, local compose) cannot write through this module.
"""
import logging
from typing import Any, Dict, List, Optional

import asyncpg

from core.config import ANALYTICS_DATABASE_URL, ANALYTICS_REPORT_DATABASE_URL

logger = logging.getLogger("dvas.analytics_ro")

_SESSION = {"default_transaction_read_only": "on", "statement_timeout": "30000",
            "application_name": "dvas-reports"}
_pool: Optional[asyncpg.Pool] = None


class AnalyticsUnavailable(RuntimeError):
    pass


def _dsn() -> str:
    return ANALYTICS_REPORT_DATABASE_URL or ANALYTICS_DATABASE_URL


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = _dsn()
        if not dsn:
            raise AnalyticsUnavailable("no analytics database configured")
        try:
            _pool = await asyncpg.create_pool(dsn, min_size=0, max_size=3, server_settings=_SESSION)
        except Exception as exc:
            raise AnalyticsUnavailable(f"analytics database unreachable: {exc}") from exc
    return _pool


async def fetch(sql: str, *args: Any) -> List[Dict[str, Any]]:
    """Run one read-only query; rows as dicts."""
    pool = await _get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
    except (OSError, asyncpg.PostgresConnectionError) as exc:
        raise AnalyticsUnavailable(str(exc)) from exc
    return [dict(r) for r in rows]
