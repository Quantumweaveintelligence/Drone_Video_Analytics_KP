import logging
import os
import httpx
from fastapi import HTTPException
from core.config import MINIO_PROXY_ENDPOINT, MINIO_PROXY_ACCESS, MINIO_PROXY_SECRET, MINIO_PROXY_SECURE

logger = logging.getLogger("dvas")

# ── Analytics Service Key (Issues #22, #23) ───────────────────────────────────
# Sent as X-Internal-Service-Key header on all outbound analytics HTTP calls.
_ANALYTICS_SERVICE_KEY = os.environ.get("ANALYTICS_SERVICE_KEY", "")

def _service_headers() -> dict:
    """Return headers for internal service-to-service calls to analytics."""
    headers = {}
    if _ANALYTICS_SERVICE_KEY:
        headers["X-Internal-Service-Key"] = _ANALYTICS_SERVICE_KEY
    return headers

_minio_proxy_client = None

def _get_minio_client():
    global _minio_proxy_client
    if _minio_proxy_client is None:
        try:
            from minio import Minio
            _minio_proxy_client = Minio(
                MINIO_PROXY_ENDPOINT,
                access_key=MINIO_PROXY_ACCESS,
                secret_key=MINIO_PROXY_SECRET,
                secure=MINIO_PROXY_SECURE,
            )
            logger.info("MinIO proxy client initialised: %s", MINIO_PROXY_ENDPOINT)
        except Exception as exc:
            logger.error("MinIO proxy client init failed: %s", exc)
            return None
    return _minio_proxy_client

class _Upstream(Exception):
    def __init__(self, status: int, error: str, detail: str):
        super().__init__(detail)
        self.status, self.error, self.detail = status, error, detail


def _fail(exc: "_Upstream", soft: bool) -> dict:
    """soft=True: the old error envelope (for aggregations / callers that audit failures).
    Otherwise a real HTTP error, so the UI never renders an upstream failure as empty data."""
    if soft:
        return {"error": exc.error, "detail": exc.detail}
    raise HTTPException(exc.status, exc.detail)


async def _call(method: str, url: str, timeout: float, ok: tuple, **kw) -> dict:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.request(method, url, timeout=timeout, headers=_service_headers(), **kw)
    except httpx.ConnectError:
        raise _Upstream(503, "service_unavailable", "Analytics service unavailable")
    except httpx.TimeoutException:
        raise _Upstream(504, "timeout", "Analytics service timed out")
    except Exception as exc:
        logger.warning("Analytics proxy error for %s: %s", url, exc)
        raise _Upstream(502, "proxy_error", "Analytics proxy error")
    if r.status_code in ok:
        return r.json() if r.content else {"ok": True}
    # upstream 4xx are passed through (bad input); 5xx become 502
    status = r.status_code if 400 <= r.status_code < 500 else 502
    raise _Upstream(status, f"upstream {r.status_code}", r.text[:200])


async def _analytics_get(url: str, timeout: float = 8.0, soft: bool = False) -> dict:
    """Forward GET to an analytics service."""
    try:
        return await _call("GET", url, timeout, (200,))
    except _Upstream as exc:
        return _fail(exc, soft)


async def _analytics_post(url: str, payload: dict, timeout: float = 8.0, soft: bool = False) -> dict:
    """Forward POST to an analytics service."""
    try:
        return await _call("POST", url, timeout, (200, 201), json=payload)
    except _Upstream as exc:
        return _fail(exc, soft)


async def _analytics_put(url: str, payload: dict, timeout: float = 8.0, soft: bool = False) -> dict:
    """Forward PUT to an analytics service."""
    try:
        return await _call("PUT", url, timeout, (200, 201), json=payload)
    except _Upstream as exc:
        return _fail(exc, soft)


async def _analytics_delete(url: str, timeout: float = 8.0, soft: bool = False) -> dict:
    """Forward DELETE to an analytics service."""
    try:
        return await _call("DELETE", url, timeout, (200, 204))
    except _Upstream as exc:
        return _fail(exc, soft)
