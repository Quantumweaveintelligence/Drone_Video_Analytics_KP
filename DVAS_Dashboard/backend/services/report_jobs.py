"""
Report jobs (evidence plan R4).

A report row is a job: requested (CRO, waits for a senior) → queued → running →
ready | failed (or rejected). One in-process worker claims queued jobs with
SELECT … FOR UPDATE SKIP LOCKED, so a second worker (or process) could share
the table safely. Each job has an 80 s limit; jobs found 'running' for more than
5 minutes at start-up were interrupted and are marked failed, never left stuck.
The PDF is stored in the dvas-reports bucket with its SHA-256; requesters get a
report_ready / report_failed WebSocket message.
"""
import asyncio
import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update

from audit_service import write_audit_v2
from core.analytics_client import _get_minio_client
from core.config import REPORTS_BUCKET
from database import async_session_factory
from models import Report
from services.evidence_integrity import sha256_hex
from services.report_types import REPORT_TYPES, render

logger = logging.getLogger("dvas.report_jobs")

JOB_TIMEOUT_S = 80                 # below Cloudflare's 100 s, though downloads are separate
STALE_AFTER = timedelta(minutes=5)
POLL_S = 2.0
SYSTEM_ACTOR = {"id": "system:reports", "email": "system@dvas", "role": "system", "name": "DVAS report worker"}


async def recover_interrupted() -> int:
    cutoff = (datetime.now(timezone.utc) - STALE_AFTER).isoformat()
    async with async_session_factory() as session:
        res = await session.execute(
            update(Report).where(Report.status == "running",
                                 (Report.started_at.is_(None)) | (Report.started_at < cutoff))
            .values(status="failed", error_message="interrupted (backend restarted during generation)",
                    finished_at=datetime.now(timezone.utc).isoformat()))
        await session.commit()
        return res.rowcount or 0


async def claim_next() -> Optional[str]:
    async with async_session_factory() as session:
        row = (await session.execute(
            select(Report).where(Report.status == "queued").order_by(Report.requested_at)
            .limit(1).with_for_update(skip_locked=True))).scalar_one_or_none()
        if row is None:
            return None
        row.status = "running"
        row.started_at = datetime.now(timezone.utc).isoformat()
        row.attempts = (row.attempts or 0) + 1
        await session.commit()
        return row.id


def _store(report_id: str, pdf: bytes) -> str:
    client = _get_minio_client()
    if client is None:
        raise RuntimeError("MinIO client not available")
    if not client.bucket_exists(REPORTS_BUCKET):
        client.make_bucket(REPORTS_BUCKET)
    key = f"{report_id}.pdf"
    client.put_object(REPORTS_BUCKET, key, io.BytesIO(pdf), len(pdf), content_type="application/pdf",
                      metadata={"sha256": sha256_hex(pdf)})
    return key


async def _broadcast(msg: dict) -> None:
    try:
        from services.websocket_hub import hub
        await hub.broadcast(msg)
    except Exception as exc:
        logger.debug("Report broadcast failed: %s", exc)


async def _generate(report: Report) -> bytes:
    spec = REPORT_TYPES[report.report_type]
    async with async_session_factory() as session:
        data = await spec["collect"](session, dict(report.params or {}))
    return await asyncio.to_thread(render, report.report_type, data, dict(report.params or {}),
                                   report.id, report.watermark_text or "")


async def run_job(report_id: str) -> None:
    async with async_session_factory() as session:
        report = await session.get(Report, report_id)
    try:
        pdf = await asyncio.wait_for(_generate(report), timeout=JOB_TIMEOUT_S)
        key = await asyncio.to_thread(_store, report_id, pdf)
    except Exception as exc:
        msg = "timed out" if isinstance(exc, asyncio.TimeoutError) else str(exc)[:300] or type(exc).__name__
        logger.error("Report %s failed: %s", report_id, msg)
        async with async_session_factory() as session:
            await session.execute(update(Report).where(Report.id == report_id).values(
                status="failed", error_message=msg, finished_at=datetime.now(timezone.utc).isoformat()))
            await session.commit()
        await _broadcast({"type": "report_failed", "report_id": report_id,
                          "requested_by": report.requested_by, "error": msg})
        return

    digest = sha256_hex(pdf)
    now = datetime.now(timezone.utc).isoformat()
    async with async_session_factory() as session:
        await session.execute(update(Report).where(Report.id == report_id).values(
            status="ready", file_bucket=REPORTS_BUCKET, file_key=key, file_size_bytes=len(pdf), sha256=digest,
            generated_at=now, finished_at=now, error_message=None))
        await session.commit()
    await write_audit_v2(actor=SYSTEM_ACTOR, action="report_generated", category="data", target=report_id,
                         target_type="report", target_id=report_id, target_name=report.title or "",
                         after_values={"sha256": digest, "bytes": len(pdf), "params": report.params},
                         source_module="report_jobs")
    await _broadcast({"type": "report_ready", "report_id": report_id, "requested_by": report.requested_by,
                      "title": report.title, "sha256": digest})


async def _worker_loop():
    try:
        n = await recover_interrupted()
        if n:
            logger.warning("Marked %d interrupted report job(s) as failed", n)
    except Exception as exc:
        logger.error("Report job recovery failed: %s", exc)
    while True:
        try:
            report_id = await claim_next()
            if report_id:
                await run_job(report_id)
                continue
        except Exception as exc:
            logger.error("Report worker error: %s", exc)
        await asyncio.sleep(POLL_S)


def start_report_worker():
    asyncio.create_task(_worker_loop())
