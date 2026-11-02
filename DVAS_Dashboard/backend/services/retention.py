"""
Evidence retention job (evidence plan E2). Runs daily at the time set in
Admin → System Settings (retention_job_time_ist).

1. Active evidence past retention_until, not on legal hold, not in an open case
   and not in a pending export → status 'disposed' (reason 'retention'), audited.
2. Disposed evidence whose retention has passed → its vault copies are deleted
   (every object version). Object lock refuses while retention or a legal hold is
   active, so nothing is removed early even if this logic were wrong.
Evidence is never deleted from the database: the disposed row and its audit
trail remain.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List

from sqlalchemy import select

from audit_service import write_audit_v2
from database import async_session_factory
from models import Case, CaseEvidence, Evidence, Export
from services import evidence_vault

logger = logging.getLogger("dvas.retention")

SYSTEM_ACTOR = {"id": "system:retention", "email": "system@dvas", "role": "system", "name": "DVAS retention job"}


def _past(iso) -> bool:
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt < datetime.now(timezone.utc)


async def _protected_ids(session) -> set:
    """Evidence in an open case or a pending export."""
    in_cases = (await session.execute(
        select(CaseEvidence.evidence_id).join(Case, Case.id == CaseEvidence.case_id)
        .where(Case.status == "open"))).scalars().all()
    pending = (await session.execute(select(Export).where(Export.status == "pending"))).scalars().all()
    ids = set(in_cases)
    for exp in pending:
        if exp.evidence_id:
            ids.add(exp.evidence_id)
        ids.update(exp.evidence_ids or [])
        if exp.case_id:
            ids.update((await session.execute(select(CaseEvidence.evidence_id)
                                              .where(CaseEvidence.case_id == exp.case_id))).scalars().all())
    return ids


async def run_retention(dry_run: bool = False) -> Dict[str, List[str]]:
    """Returns {"dispose": [...], "remove_objects": [...], "errors": [...]}."""
    result = {"dispose": [], "remove_objects": [], "errors": []}
    async with async_session_factory() as session:
        protected = await _protected_ids(session)
        active = (await session.execute(select(Evidence).where(
            Evidence.status == "active", Evidence.legal_hold.is_not(True)))).scalars().all()
        to_dispose = [e for e in active if _past(e.retention_until) and e.id not in protected]
        disposed = (await session.execute(select(Evidence).where(
            Evidence.status == "disposed", Evidence.legal_hold.is_not(True),
            Evidence.vault_status.in_(("copied", "partial"))))).scalars().all()
        to_remove = [e for e in disposed if _past(e.retention_until)]
        result["dispose"] = [e.id for e in to_dispose]
        result["remove_objects"] = [e.id for e in to_remove]
        if dry_run:
            return result

        now = datetime.now(timezone.utc).isoformat()
        for e in to_dispose:
            e.status, e.disposed_at, e.disposed_by, e.disposal_reason = "disposed", now, SYSTEM_ACTOR["id"], "retention"
        await session.commit()

    for ev_id in result["dispose"]:
        await write_audit_v2(actor=SYSTEM_ACTOR, action="evidence_dispose", category="evidence",
                             target=ev_id, target_type="evidence", target_id=ev_id,
                             details="Retention period over", after_values={"status": "disposed"},
                             compliance_tags=["RETENTION"], source_module="retention")

    for ev_id in result["remove_objects"]:
        try:
            errors = await asyncio.to_thread(evidence_vault.remove_prefix, f"evidence/{ev_id}/")
        except Exception as exc:
            errors = [str(exc)]
        if errors:
            result["errors"].extend(f"{ev_id}: {e}" for e in errors)
            continue
        async with async_session_factory() as session:
            ev = await session.get(Evidence, ev_id)
            ev.vault_status = "removed"
            await session.commit()
        await write_audit_v2(actor=SYSTEM_ACTOR, action="evidence_objects_removed", category="evidence",
                             target=ev_id, target_type="evidence", target_id=ev_id,
                             details="Vault copies deleted after retention", compliance_tags=["RETENTION"],
                             source_module="retention")
    if result["dispose"] or result["remove_objects"] or result["errors"]:
        logger.info("Retention: %d disposed, %d with vault copies to remove, %d errors",
                    len(result["dispose"]), len(result["remove_objects"]), len(result["errors"]))
    return result


async def run_report_retention() -> int:
    """Delete stored report PDFs older than report_retention_days; the row stays
    (status 'expired') so the request and its hash remain auditable."""
    from datetime import timedelta
    from core.analytics_client import _get_minio_client
    from models import Report
    from services.settings_service import get as get_setting
    days = int(await get_setting("report_retention_days"))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    removed = 0
    client = _get_minio_client()
    async with async_session_factory() as session:
        rows = (await session.execute(select(Report).where(
            Report.status == "ready", Report.generated_at < cutoff))).scalars().all()
        for r in rows:
            if client is not None and r.file_key:
                try:
                    await asyncio.to_thread(client.remove_object, r.file_bucket or "dvas-evidence", r.file_key)
                except Exception as exc:
                    logger.warning("Could not delete report %s: %s", r.id, exc)
                    continue
            r.status, r.error_message = "expired", f"PDF deleted after {days} days (retention)"
            removed += 1
        await session.commit()
    if removed:
        await write_audit_v2(actor=SYSTEM_ACTOR, action="report_retention", category="data",
                             target="reports", target_type="report",
                             details=f"{removed} report PDF(s) older than {days} days deleted",
                             compliance_tags=["RETENTION"], source_module="retention")
    return removed


def _due(now_ist: datetime, hhmm: str, last_run_day) -> bool:
    h, m = (int(x) for x in hhmm.split(":"))
    return now_ist.date() != last_run_day and (now_ist.hour, now_ist.minute) >= (h, m)


async def _retention_loop():
    """Runs once a day at the configured IST time (Admin → System Settings)."""
    from core.timeutil import IST
    from services.settings_service import get as get_setting
    last_run_day = None
    await asyncio.sleep(60)
    while True:
        try:
            now_ist = datetime.now(IST)
            if _due(now_ist, await get_setting("retention_job_time_ist"), last_run_day):
                last_run_day = now_ist.date()
                await run_retention()
                await run_report_retention()
        except Exception as exc:
            logger.error("Retention job failed: %s", exc)
        await asyncio.sleep(300)


def start_retention_job():
    asyncio.create_task(_retention_loop())
