import logging
import os
import httpx
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

async def _analytics_get(url: str, timeout: float = 8.0) -> dict:
    """Forward GET to analytics service; return response JSON or error envelope."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=timeout, headers=_service_headers())
            if r.status_code == 200:
                return r.json()
            return {"error": f"upstream {r.status_code}", "detail": r.text[:200]}
    except httpx.ConnectError:
        return {"error": "service_unavailable", "detail": f"Cannot reach {url}"}
    except httpx.TimeoutException:
        return {"error": "timeout", "detail": f"Timeout reaching {url}"}
    except Exception as exc:
        return {"error": "proxy_error", "detail": str(exc)}

async def _analytics_post(url: str, payload: dict, timeout: float = 8.0) -> dict:
    """Forward POST to analytics service."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, timeout=timeout, headers=_service_headers())
            if r.status_code in (200, 201):
                return r.json()
            return {"error": f"upstream {r.status_code}", "detail": r.text[:200]}
    except httpx.ConnectError:
        return {"error": "service_unavailable", "detail": f"Cannot reach {url}"}
    except Exception as exc:
        return {"error": "proxy_error", "detail": str(exc)}

async def _analytics_put(url: str, payload: dict, timeout: float = 8.0) -> dict:
    """Forward PUT to analytics service."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.put(url, json=payload, timeout=timeout, headers=_service_headers())
            if r.status_code in (200, 201):
                return r.json()
            return {"error": f"upstream {r.status_code}", "detail": r.text[:200]}
    except httpx.ConnectError:
        return {"error": "service_unavailable", "detail": f"Cannot reach {url}"}
    except Exception as exc:
        return {"error": "proxy_error", "detail": str(exc)}

async def _analytics_delete(url: str, timeout: float = 8.0) -> dict:
    """Forward DELETE to analytics service."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.delete(url, timeout=timeout, headers=_service_headers())
            if r.status_code in (200, 204):
                return r.json() if r.content else {"ok": True}
            return {"error": f"upstream {r.status_code}", "detail": r.text[:200]}
    except httpx.ConnectError:
        return {"error": "service_unavailable", "detail": f"Cannot reach {url}"}
    except Exception as exc:
        return {"error": "proxy_error", "detail": str(exc)}
