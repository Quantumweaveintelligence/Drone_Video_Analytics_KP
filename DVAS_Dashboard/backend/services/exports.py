"""
Export finalisation (evidence plan E4): freeze the evidence list and build the
bundle once, at approval. Downloads then stream the stored bytes.
"""
import asyncio
import logging
from typing import Dict, List, Optional

from sqlalchemy import select

from audit_service import write_audit_v2
from database import async_session_factory
from models import AuditLog, Case, CaseEvidence, Evidence, Export
from core.security import _row_to_dict
from services import evidence_vault
from services.export_bundle import BundleIntegrityError, build_bundle

logger = logging.getLogger("dvas.exports")


class ExportError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


async def evidence_ids_for(session, exp: Export) -> List[str]:
    """The export's evidence list: frozen list, else the case's items, else the single evidence."""
    if exp.evidence_ids:
        return list(exp.evidence_ids)
    if exp.case_id:
        res = await session.execute(select(CaseEvidence.evidence_id).where(CaseEvidence.case_id == exp.case_id)
                                    .order_by(CaseEvidence.added_at))
        return [r for r in res.scalars().all()]
    return [exp.evidence_id] if exp.evidence_id else []


async def finalize_export(export_id: str, approver: Dict, request=None) -> Dict:
    """Build and store the bundle for an approved export (idempotent: an existing bundle is kept)."""
    async with async_session_factory() as session:
        exp = (await session.execute(select(Export).where(Export.id == export_id))).scalar_one_or_none()
        if exp is None:
            raise ExportError(404, "Export not found")
        if exp.status != "approved":
            raise ExportError(400, "Only approved exports have a bundle")
        if exp.bundle_key:
            return _row_to_dict(exp)
        ids = await evidence_ids_for(session, exp)
        if not ids:
            raise ExportError(409, "Export has no evidence")
        rows = (await session.execute(select(Evidence).where(Evidence.id.in_(ids)))).scalars().all()
        by_id = {r.id: _row_to_dict(r) for r in rows}
        missing = [i for i in ids if i not in by_id]
        disposed = [i for i in ids if i in by_id and by_id[i].get("status") == "disposed"]
        if missing or disposed:
            raise ExportError(409, f"Evidence not available: {', '.join(missing + disposed)}")
        evidence = [by_id[i] for i in ids]
        case = {}
        if exp.case_id:
            c = await session.get(Case, exp.case_id)
            case = _row_to_dict(c) if c else {}
        audit = (await session.execute(
            select(AuditLog).where(AuditLog.target_id.in_(ids)).order_by(AuditLog.ts))).scalars().all()
        audit_rows = [{"ts": a.ts, "action": a.action, "actor_email": a.actor_email,
                       "target_id": a.target_id, "hash": a.hash} for a in audit]
        export_dict = _row_to_dict(exp)

    try:
        data, info = await asyncio.to_thread(build_bundle, export_dict, case, evidence, audit_rows, approver)
        from services.settings_service import get as get_setting
        retain = int(await get_setting("evidence_retention_days"))
        stored = await asyncio.to_thread(evidence_vault.put_bundle, export_id, data, "bundle.zip", retain)
    except BundleIntegrityError as exc:
        await _record_error(export_id, str(exc))
        await write_audit_v2(actor=approver, action="export_bundle_failed", category="evidence",
                             severity="critical", target=export_id, target_type="export",
                             target_id=export_id, details=str(exc), status="failure", request=request)
        raise ExportError(409, f"Bundle not built: {exc}")
    except Exception as exc:
        logger.error("Bundle build/store failed for %s: %s", export_id, exc)
        await _record_error(export_id, f"storage: {exc}")
        raise ExportError(503, "Bundle storage unavailable; retry the download later")

    async with async_session_factory() as session:
        exp = await session.get(Export, export_id)
        exp.evidence_ids = ids
        exp.bundle_key = stored["key"]
        exp.bundle_sha256 = info["sha256"]
        exp.bundle_bytes = stored["bytes"]
        exp.certificate_sha256 = info["certificate_sha256"]
        exp.bundle_error = None
        await session.commit()
        result = _row_to_dict(exp)
    await write_audit_v2(
        actor=approver, action="export_bundle_built", category="evidence",
        target=export_id, target_type="export", target_id=export_id,
        details=f"Bundle with {len(ids)} evidence item(s), {len(info['files'])} file(s)",
        after_values={"bundle_sha256": info["sha256"], "certificate_sha256": info["certificate_sha256"],
                      "manifest_sha256": info["manifest_sha256"], "evidence_ids": ids,
                      "files": [{"path": f["path"], "sha256": f["sha256"]} for f in info["files"]]},
        compliance_tags=["BSA_63"], request=request,
    )
    return result


async def _record_error(export_id: str, message: str) -> None:
    async with async_session_factory() as session:
        exp = await session.get(Export, export_id)
        if exp is not None:
            exp.bundle_error = message[:500]
            await session.commit()


def read_bundle(exp: Dict) -> bytes:
    data = evidence_vault.read_object(evidence_vault.VAULT, exp["bundle_key"])
    from services.evidence_integrity import sha256_hex
    if exp.get("bundle_sha256") and sha256_hex(data) != exp["bundle_sha256"]:
        raise BundleIntegrityError("stored bundle does not match its recorded SHA-256")
    return data
