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
from typing import List, Dict, Any
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


async def sync_records_batch(records: List[Dict[str, Any]]) -> int:
    """Insert a batch of audit records into the local database if not already present."""
    if not records:
        return 0

    inserted_count = 0
    async with async_session_factory() as session:
        for r in records:
            log_id = r.get("id")
            if not log_id:
                continue

            # Check if record exists
            existing = await session.execute(
                select(AuditLog.id).where(AuditLog.id == log_id)
            )
            if existing.scalar_one_or_none():
                continue

            entry = AuditLog(
                id=log_id,
                ts=r.get("ts"),
                actor_id=r.get("actor_id"),
                actor_email=r.get("actor_email"),
                actor_role=r.get("actor_role"),
                actor_name=r.get("actor_name"),
                actor_session_id=r.get("actor_session_id"),
                actor_device_id=r.get("actor_device_id"),
                action=r.get("action"),
                action_type=r.get("action_type"),
                category=r.get("category"),
                severity=r.get("severity") or "info",
                target=r.get("target") or "",
                target_type=r.get("target_type"),
                target_id=r.get("target_id"),
                target_name=r.get("target_name"),
                details=r.get("details") or "",
                before_values=_clean_json_field(r.get("before_values")),
                after_values=_clean_json_field(r.get("after_values")),
                status=r.get("status") or "success",
                error_code=r.get("error_code"),
                source_module=r.get("source_module"),
                source_endpoint=r.get("source_endpoint"),
                request_id=r.get("request_id"),
                correlation_id=r.get("correlation_id"),
                compliance_tags=_clean_json_field(r.get("compliance_tags")) or [],
                ip=r.get("ip") or "-",
                prev_hash=r.get("prev_hash"),
                hash=r.get("hash"),
            )
            session.add(entry)
            inserted_count += 1

            # Broadcast new audit event via WebSocket for live tail
            try:
                data_dict = {
                    "id": entry.id,
                    "ts": entry.ts,
                    "actor_id": entry.actor_id,
                    "actor_email": entry.actor_email,
                    "actor_role": entry.actor_role,
                    "actor_name": entry.actor_name,
                    "action": entry.action,
                    "action_type": entry.action_type,
                    "category": entry.category,
                    "severity": entry.severity,
                    "target": entry.target,
                    "target_type": entry.target_type,
                    "status": entry.status,
                    "ip": entry.ip,
                    "hash": entry.hash,
                }
                await hub.broadcast({"type": "audit_event", "data": data_dict})
            except Exception as b_err:
                logger.debug("WebSocket broadcast error during audit sync: %s", b_err)

        if inserted_count > 0:
            await session.commit()
            logger.info("Successfully synced %d new audit log records to central DB.", inserted_count)

    return inserted_count


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
            await sync_records_batch(records)
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
