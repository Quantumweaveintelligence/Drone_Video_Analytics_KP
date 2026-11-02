# -*- coding: utf-8 -*-
"""
DVAS Analytics — Internal Service Key Authentication
=====================================================
Validates the X-Internal-Service-Key header on incoming requests.
Used to protect analytics endpoints from unauthenticated access.

The key is shared between:
  - Analytics microservices (this module)
  - Dashboard backend (sends key in HTTP headers)

Key is injected via ANALYTICS_SERVICE_KEY environment variable.

Security Issue References: #22, #23
"""
import hmac
import logging
import os

from fastapi import HTTPException, Request, status

log = logging.getLogger(__name__)

_SERVICE_KEY: str = os.environ.get("ANALYTICS_SERVICE_KEY", "")

if not _SERVICE_KEY:
    log.warning(
        "ANALYTICS_SERVICE_KEY not set — all service-key-protected endpoints "
        "will reject requests. Set this env var in docker-compose.yml."
    )


async def verify_service_key(request: Request) -> None:
    """
    FastAPI dependency that validates the X-Internal-Service-Key header.
    Uses constant-time comparison to prevent timing attacks.

    Usage:
        @app.get("/endpoint", dependencies=[Depends(verify_service_key)])
        async def my_endpoint():
            ...
    """
    if not _SERVICE_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service authentication not configured (ANALYTICS_SERVICE_KEY missing)",
        )

    provided_key = request.headers.get("X-Internal-Service-Key", "")
    if not provided_key or not hmac.compare_digest(provided_key, _SERVICE_KEY):
        log.warning(
            "service_key_rejected: remote=%s path=%s",
            request.client.host if request.client else "unknown",
            request.url.path,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing internal service key",
        )


# ── Service-wide guard (M1) ──────────────────────────────────────────────────
# Every analytics route requires X-Internal-Service-Key except liveness. The
# services bind to 127.0.0.1, but without this any local process (or an SSRF in
# another component) could start/stop streams, change thresholds or enrol faces.
GUARD_EXEMPT_PATHS = frozenset({"/health"})


def install_service_guard(app) -> None:
    from starlette.responses import JSONResponse

    @app.middleware("http")
    async def _service_key_guard(request: Request, call_next):
        if request.url.path in GUARD_EXEMPT_PATHS:
            return await call_next(request)
        if not _SERVICE_KEY:
            return JSONResponse({"detail": "Service authentication not configured"}, status_code=503)
        provided = request.headers.get("X-Internal-Service-Key", "")
        if not provided or not hmac.compare_digest(provided, _SERVICE_KEY):
            log.warning("service_key_rejected: remote=%s path=%s",
                        request.client.host if request.client else "unknown", request.url.path)
            return JSONResponse({"detail": "Invalid or missing internal service key"}, status_code=401)
        return await call_next(request)
