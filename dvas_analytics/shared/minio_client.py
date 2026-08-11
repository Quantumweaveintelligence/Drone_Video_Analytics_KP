# -*- coding: utf-8 -*-
"""
DVAS Analytics — Async MinIO Client
Provides upload, presigned-URL generation, and bucket management.
Uses miniopy-async (S3-compatible API).
"""
import asyncio
import io
import logging
import time
from typing import Optional

log = logging.getLogger(__name__)

try:
    from miniopy_async import Minio
    _MINIO_AVAILABLE = True
except ImportError:
    log.warning("miniopy-async not installed — MinIO uploads disabled")
    _MINIO_AVAILABLE = False

from shared.config import cfg

_client: Optional["Minio"] = None
_buckets_ensured: set = set()


def get_client() -> Optional["Minio"]:
    """Return (or create) the global async MinIO client."""
    global _client
    if not _MINIO_AVAILABLE:
        return None
    if _client is None:
        _client = Minio(
            cfg.MINIO_ENDPOINT,
            access_key=cfg.MINIO_ACCESS_KEY,
            secret_key=cfg.MINIO_SECRET_KEY,
            secure=cfg.MINIO_SECURE,
        )
    return _client


async def ensure_bucket(bucket: str) -> None:
    """Create bucket if it does not exist (idempotent)."""
    if bucket in _buckets_ensured:
        return
    client = get_client()
    if client is None:
        return
    try:
        exists = await client.bucket_exists(bucket)
        if not exists:
            await client.make_bucket(bucket)
            log.info("MinIO bucket created: %s", bucket)
        _buckets_ensured.add(bucket)
    except Exception as exc:
        log.error("MinIO ensure_bucket(%s) failed: %s", bucket, exc)


async def ensure_all_buckets() -> None:
    """Create all DVAS buckets on startup."""
    for bucket in [
        cfg.BUCKET_EVIDENCE,
        cfg.BUCKET_FACES,
        cfg.BUCKET_PLATES,
        cfg.BUCKET_HEATMAPS,
        cfg.BUCKET_VEHICLES,
    ]:
        await ensure_bucket(bucket)


async def upload_bytes(
    bucket: str,
    key: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> Optional[str]:
    """
    Upload raw bytes to MinIO.
    Returns the object key on success, None on failure.
    """
    client = get_client()
    if client is None:
        log.debug("MinIO not available — skipping upload for %s/%s", bucket, key)
        return None
    try:
        await ensure_bucket(bucket)
        stream = io.BytesIO(data)
        await client.put_object(
            bucket_name=bucket,
            object_name=key,
            data=stream,
            length=len(data),
            content_type=content_type,
        )
        log.debug("MinIO upload OK: %s/%s (%d bytes)", bucket, key, len(data))
        return key
    except Exception as exc:
        log.error("MinIO upload failed (%s/%s): %s", bucket, key, exc)
        return None


async def get_presigned_url(
    bucket: str,
    key: str,
    expires: int = 3600,
) -> Optional[str]:
    """
    Generate a presigned GET URL for an object.
    Returns URL string on success, None on failure.
    Rewrites internal Docker hostname to public-facing URL if MINIO_PUBLIC_URL is set.
    """
    client = get_client()
    if client is None:
        return None
    try:
        from datetime import timedelta
        url = await client.presigned_get_object(
            bucket_name=bucket,
            object_name=key,
            expires=timedelta(seconds=expires),
        )
        # Rewrite internal Docker hostname (e.g. minio:9000) to public-facing URL
        # so the browser can actually reach the MinIO server.
        import os
        public_url = os.getenv("MINIO_PUBLIC_URL", "")
        if public_url and url:
            internal = f"http://{cfg.MINIO_ENDPOINT}"
            if url.startswith(internal):
                url = url.replace(internal, public_url, 1)
        return url
    except Exception as exc:
        log.warning("MinIO presigned URL failed (%s/%s): %s", bucket, key, exc)
        return None


async def delete_object(bucket: str, key: str) -> bool:
    """Delete an object from MinIO. Returns True on success."""
    client = get_client()
    if client is None:
        return False
    try:
        await client.remove_object(bucket, key)
        return True
    except Exception as exc:
        log.warning("MinIO delete failed (%s/%s): %s", bucket, key, exc)
        return False


def make_key(prefix: str, drone_id: str, suffix: str) -> str:
    """
    Build a consistent MinIO object key.
    Format: {prefix}/{drone_id}/{timestamp_ms}/{suffix}
    Example: faces/DRONE_KL_001/1722245000123/face_001.jpg
    """
    ts_ms = int(time.time() * 1000)
    return f"{prefix}/{drone_id}/{ts_ms}/{suffix}"
