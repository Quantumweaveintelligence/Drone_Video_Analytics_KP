"""
Evidence vault (evidence plan E2/E3): immutable copies of evidence objects.

Every object an analyzer recorded for a confirmed event is copied into the
`dvas-evidence-vault` bucket at evidence/<evidence_id>/<role>.<ext>. The bucket
is created with MinIO object lock (GOVERNANCE, EVIDENCE_RETENTION_DAYS) and
versioning, so the copy outlives the 3/14-day expiry of the analytics buckets
and cannot be overwritten or deleted before its retention date. Legal hold is a
per-object S3 legal hold on top of that.

The analyzer's SHA-256 (x-amz-meta-sha256 at upload) is re-checked when the
bytes are read for the copy; a difference marks the evidence `mismatch`.

minio-py is synchronous: call these from async code with asyncio.to_thread.
"""
import io
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from core.analytics_client import _get_minio_client
from core.config import EVIDENCE_RETENTION_DAYS, EVIDENCE_VAULT_BUCKET
from services.evidence_integrity import sha256_hex

logger = logging.getLogger("dvas.evidence_vault")

VAULT = EVIDENCE_VAULT_BUCKET
_vault_locked: Optional[bool] = None     # None = not checked yet


class VaultUnavailable(RuntimeError):
    pass


def _client():
    client = _get_minio_client()
    if client is None:
        raise VaultUnavailable("MinIO client not available")
    return client


def ensure_vault() -> bool:
    """Create the vault with object lock if missing. Returns True when object lock is on."""
    global _vault_locked
    if _vault_locked is not None:
        return _vault_locked
    from minio.commonconfig import GOVERNANCE
    from minio.objectlockconfig import DAYS, ObjectLockConfig

    client = _client()
    if not client.bucket_exists(VAULT):
        client.make_bucket(VAULT, object_lock=True)          # also enables versioning
        client.set_object_lock_config(VAULT, ObjectLockConfig(GOVERNANCE, EVIDENCE_RETENTION_DAYS, DAYS))
        logger.info("Evidence vault %s created with object lock (%d days)", VAULT, EVIDENCE_RETENTION_DAYS)
    from minio.error import S3Error
    try:
        client.get_object_lock_config(VAULT)
        _vault_locked = True
    except S3Error as exc:
        if exc.code != "ObjectLockConfigurationNotFoundError":
            raise                     # transient: check again next time
        # Object lock can only be enabled when a bucket is created
        logger.error("Evidence vault %s exists WITHOUT object lock — copies are not immutable", VAULT)
        _vault_locked = False
    return _vault_locked


def _ext(key: str, content_type: str = "") -> str:
    base = os.path.basename(key or "")
    if "." in base:
        return base.rsplit(".", 1)[1].lower()[:5]
    return {"image/png": "png", "image/jpeg": "jpg", "application/json": "json"}.get(content_type, "bin")


def read_object(bucket: str, key: str) -> bytes:
    resp = _client().get_object(bucket, key)
    try:
        return resp.read()
    finally:
        resp.close()
        resp.release_conn()


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def copy_into_vault(
    evidence_id: str,
    objects: List[Dict],
    retain_until: datetime,
    legal_hold: bool = False,
) -> Tuple[List[Dict], str, str]:
    """Copy source objects into the vault.

    Returns (vault_objects, vault_status, integrity):
      vault_status: copied | partial | failed | none
      integrity:    ok (every analyzer hash matched) | mismatch | missing | unverified
    vault_objects keep the source reference: {"source": {"bucket", "key", "sha256"}}.
    """
    if not objects:
        return [], "none", "unverified"
    from minio.commonconfig import GOVERNANCE
    from minio.retention import Retention

    locked = ensure_vault()
    client = _client()
    out, copied, missing, mismatch, unverified = [], 0, 0, 0, 0
    used_names = set()
    for obj in objects:
        role = obj.get("role") or "primary"
        src_bucket, src_key = obj.get("bucket"), obj.get("key")
        declared = obj.get("sha256")
        entry = {
            "role": role, "kind": obj.get("kind"), "content_type": obj.get("content_type") or "image/jpeg",
            "source": {"bucket": src_bucket, "key": src_key, "sha256": declared},
        }
        try:
            data = read_object(src_bucket, src_key)
        except Exception as exc:
            logger.warning("Vault copy: source %s/%s unreadable: %s", src_bucket, src_key, exc)
            missing += 1
            entry.update(bucket=src_bucket, key=src_key, sha256=declared, bytes=None, copy="missing")
            out.append(entry)
            continue
        digest = sha256_hex(data)
        if declared and declared != digest:
            mismatch += 1
            entry["hash_check"] = "mismatch"
        elif declared:
            entry["hash_check"] = "match"
        else:
            unverified += 1
            entry["hash_check"] = "no_source_hash"
        name = role
        n = 2
        while name in used_names:
            name, n = f"{role}-{n}", n + 1
        used_names.add(name)
        dest = f"evidence/{evidence_id}/{name}.{_ext(src_key, entry['content_type'])}"
        kwargs = {"metadata": {"sha256": digest, "source-bucket": src_bucket, "source-key": src_key}}
        if locked:
            kwargs["retention"] = Retention(GOVERNANCE, _as_utc(retain_until))
            kwargs["legal_hold"] = legal_hold
        try:
            client.put_object(VAULT, dest, io.BytesIO(data), len(data),
                              content_type=entry["content_type"], **kwargs)
        except Exception as exc:
            logger.error("Vault copy failed for %s → %s: %s", src_key, dest, exc)
            missing += 1
            entry.update(bucket=src_bucket, key=src_key, sha256=digest, bytes=len(data), copy="failed")
            out.append(entry)
            continue
        copied += 1
        entry.update(bucket=VAULT, key=dest, sha256=digest, bytes=len(data), copy="ok")
        out.append(entry)

    status = "copied" if copied == len(objects) else ("partial" if copied else "failed")
    if mismatch:
        integrity = "mismatch"
    elif missing:
        integrity = "missing"
    elif unverified:
        integrity = "unverified"
    else:
        integrity = "ok"
    return out, status, integrity


def set_legal_hold(objects: List[Dict], on: bool) -> int:
    """Set/clear the S3 legal hold on every vault object. Returns the number of failures."""
    failures = 0
    client = _client()
    for obj in objects or []:
        if obj.get("bucket") != VAULT or not obj.get("key"):
            continue
        try:
            if on:
                client.enable_object_legal_hold(VAULT, obj["key"])
            else:
                client.disable_object_legal_hold(VAULT, obj["key"])
        except Exception as exc:
            failures += 1
            logger.error("Legal hold %s failed for %s: %s", "set" if on else "clear", obj["key"], exc)
    return failures


def verify_objects(objects: List[Dict]) -> List[Dict]:
    """Re-hash stored objects. Each result: role, key, expected, actual, ok."""
    results = []
    for obj in objects or []:
        res = {"role": obj.get("role"), "bucket": obj.get("bucket"), "key": obj.get("key"),
               "expected": obj.get("sha256"), "actual": None, "ok": False}
        try:
            res["actual"] = sha256_hex(read_object(obj["bucket"], obj["key"]))
            res["ok"] = bool(res["expected"]) and res["actual"] == res["expected"]
        except Exception as exc:
            res["error"] = str(exc)[:200]
        results.append(res)
    return results


def remove_prefix(prefix: str) -> List[str]:
    """Delete every version under a vault prefix (retention job).

    The vault is versioned: a plain delete only adds a delete marker, so each
    version is removed explicitly. Object lock refuses versions whose retention
    or legal hold is still active; those come back as errors.
    """
    errors = []
    client = _client()
    for obj in client.list_objects(VAULT, prefix=prefix, recursive=True, include_version=True):
        try:
            client.remove_object(VAULT, obj.object_name, version_id=obj.version_id)
        except Exception as exc:
            errors.append(f"{obj.object_name}@{obj.version_id}: {exc}")
    return errors


def put_bundle(export_id: str, data: bytes, filename: str = "bundle.zip",
               retain_days: int = EVIDENCE_RETENTION_DAYS) -> Dict:
    """Store an export bundle in the vault under legal hold (court material)."""
    from minio.commonconfig import GOVERNANCE
    from minio.retention import Retention

    locked = ensure_vault()
    digest = sha256_hex(data)
    key = f"exports/{export_id}/{filename}"
    kwargs = {"metadata": {"sha256": digest}}
    if locked:
        kwargs["retention"] = Retention(
            GOVERNANCE, datetime.now(timezone.utc) + timedelta(days=retain_days))
        kwargs["legal_hold"] = True
    _client().put_object(VAULT, key, io.BytesIO(data), len(data), content_type="application/zip", **kwargs)
    return {"bucket": VAULT, "key": key, "sha256": digest, "bytes": len(data)}
