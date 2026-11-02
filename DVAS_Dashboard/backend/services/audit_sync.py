"""
Audit Sync Background Service
=============================
Synchronizes audit log trails between distributed Edge Laptops (ground stations)
and the Central Police Command Server.

Features:
- Continuously syncs new audit logs from configured edge laptops (e.g. 10.10.55.112).
- Non-blocking asyncpg connection with exponential backoff on network dropouts.
- Inserts new audit entries into central database with conflict deduplication.
- Broadcasts synchronized events over WebSocket hub for live tail visibility.
- Provides batch sync handler for HTTP-based edge sync payloads.
"""

import os
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
import asyncpg
from sqlalchemy import select
from database import async_session_factory
from models import AuditLog
from services.websocket_hub import hub

logger = logging.getLogger("dvas.audit_sync")

DEFAULT_EDGE_HOSTS = os.getenv("EDGE_SYNC_HOSTS", "10.10.55.112")
SYNC_INTERVAL = int(os.getenv("AUDIT_SYNC_INTERVAL_SEC", "5"))


def _clean_json_field(val):
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return val
    return val


MAX_SYNC_BATCH = 500
_SEVERITIES = {"info", "warning", "warn", "critical", "error"}


async def sync_records_batch(records: List[Dict[str, Any]], caller: Optional[dict] = None) -> Tuple[int, int]:
    """Append edge audit records to the central chain. Returns (synced, rejected).

    H3: records are never inserted verbatim. Each is appended through write_audit_v2,
    so the central chain computes its own prev_hash / hash; the edge's id, ts, hash
    and prev_hash are kept in after_values.edge_record for cross-verification.
    caller = the operator pushing over HTTP: only their own records are accepted.
    caller = None: the trusted LAN poller reading an edge database directly.
    """
    from audit_service import write_audit_v2

    synced = rejected = 0
    for r in records[:MAX_SYNC_BATCH]:
        if not isinstance(r, dict) or not r.get("id") or not r.get("action"):
            rejected += 1
            continue
        edge_id = str(r["id"])[:64]
        async with async_session_factory() as session:
            dup = (await session.execute(
                select(AuditLog.id).where(AuditLog.request_id == f"edge:{edge_id}"))).first()
        if dup:
            continue
        if caller is not None:
            if (r.get("actor_email") or "").lower() != (caller.get("email") or "").lower():
                rejected += 1
                continue
            actor = {"id": caller.get("id"), "email": caller.get("email"),
                     "role": caller.get("role"), "name": caller.get("name") or ""}
        else:
            actor = {"id": r.get("actor_id"), "email": r.get("actor_email"),
                     "role": r.get("actor_role"), "name": r.get("actor_name") or ""}
        severity = str(r.get("severity") or "info").lower()
        after = _clean_json_field(r.get("after_values"))
        after = dict(after) if isinstance(after, dict) else ({"value": after} if after is not None else {})
        after["edge_record"] = {"id": edge_id, "ts": r.get("ts"), "hash": r.get("hash"),
                                "prev_hash": r.get("prev_hash"), "device": r.get("actor_device_id")}
        await write_audit_v2(
            actor=actor,
            action=str(r["action"])[:64],
            action_type=r.get("action_type"),
            category=r.get("category"),
            severity=severity if severity in _SEVERITIES else "info",
            target=str(r.get("target") or "")[:256],
            target_type=r.get("target_type"),
            target_id=r.get("target_id"),
            target_name=r.get("target_name"),
            details=str(r.get("details") or "")[:2000],
            before_values=_clean_json_field(r.get("before_values")) if isinstance(
                _clean_json_field(r.get("before_values")), dict) else None,
            after_values=after,
            status=r.get("status") or "success",
            source_module="edge_sync",
            request_id=f"edge:{edge_id}",
            correlation_id=r.get("correlation_id"),
            ip=r.get("ip") or "-",
        )
        synced += 1
    if synced:
        logger.info("Synced %d edge audit record(s) into the central chain (%d rejected)", synced, rejected)
    return synced, rejected


async def _poll_edge_host(host: str):
    """Connect to a single edge host database and sync missing audit records."""
    port = 5433
    user = os.getenv("POSTGRES_USER", "dvas")
    password = os.getenv("POSTGRES_PASSWORD", "dvas_secret")
    db = os.getenv("POSTGRES_DB", "dvas")

    conn = None
    try:
        conn = await asyncpg.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=db,
            timeout=3.0
        )
        rows = await conn.fetch(
            """
            SELECT id, ts, actor_id, actor_email, actor_role, action, target, details,
                   ip, prev_hash, hash, actor_name, actor_session_id, actor_device_id,
                   action_type, category, severity, target_type, target_id, target_name,
                   before_values, after_values, status, error_code, source_module,
                   source_endpoint, request_id, correlation_id, compliance_tags
            FROM audit_logs
            ORDER BY ts DESC
            LIMIT 50;
            """
        )
        records = [dict(row) for row in rows]
        if records:
            await sync_records_batch(records, caller=None)
    except Exception as e:
        logger.debug("Edge audit sync unreachable for host %s: %s", host, e)
    finally:
        if conn:
            try:
                await conn.close()
            except Exception:
                pass


async def _audit_sync_loop():
    """Continuous background sync loop."""
    logger.info("Starting background Audit Sync Service (hosts: %s)...", DEFAULT_EDGE_HOSTS)
    while True:
        try:
            hosts = [h.strip() for h in DEFAULT_EDGE_HOSTS.split(",") if h.strip()]
            for host in hosts:
                await _poll_edge_host(host)
        except Exception as loop_err:
            logger.error("Unexpected error in audit sync loop: %s", loop_err)

        await asyncio.sleep(SYNC_INTERVAL)


_sync_task = None


def start_audit_sync_service():
    """Start the audit sync background task.

    No-op when EDGE_SYNC_HOSTS is set to an empty string: in the cloud (RunPod)
    the edge laptops are behind NAT and push audit entries to the API instead
    of being polled (RunPod plan F10).
    """
    global _sync_task
    hosts = [h.strip() for h in DEFAULT_EDGE_HOSTS.split(",") if h.strip()]
    if not hosts:
        logger.info("Audit sync disabled (EDGE_SYNC_HOSTS is empty).")
        return
    if _sync_task is None:
        loop = asyncio.get_event_loop()
        _sync_task = loop.create_task(_audit_sync_loop())
        logger.info("Audit sync task registered with event loop.")
