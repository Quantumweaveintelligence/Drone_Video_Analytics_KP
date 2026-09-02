"""
auth.py
=======
Keycloak JWT validation for the Kerala Police Drone Ingest API.

Flow:
  1. Client sends  Authorization: Bearer <JWT>
  2. We fetch Keycloak JWKS (RS256 public keys) — cached for JWKS_CACHE_TTL_S
  3. python-jose decodes + verifies the signature
  4. We check the realm_access.roles claim for an allowed role
  5. Return decoded payload dict (available via Depends(validate_token))
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import httpx
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from config import (
    ALLOWED_ROLES,
    CLIENT_ID,
    JWKS_CACHE_TTL_S,
    JWKS_URL,
)

log = logging.getLogger(__name__)

# ── JWKS cache ────────────────────────────────────────────────────────────────
_jwks_cache:    Optional[dict] = None
_jwks_fetched:  float          = 0.0


async def get_jwks() -> dict:
    """
    Fetch Keycloak RS256 public keys.
    Returns cached copy if younger than JWKS_CACHE_TTL_S seconds.
    """
    global _jwks_cache, _jwks_fetched

    if _jwks_cache and (time.monotonic() - _jwks_fetched) < JWKS_CACHE_TTL_S:
        return _jwks_cache

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(JWKS_URL)
            resp.raise_for_status()
            _jwks_cache   = resp.json()
            _jwks_fetched = time.monotonic()
            log.info("jwks_cache_refreshed", extra={"jwks_url": JWKS_URL})
            return _jwks_cache
    except Exception as exc:
        log.error("jwks_fetch_failed", extra={"error": str(exc)})
        # Return stale cache rather than failing all requests
        if _jwks_cache:
            log.warning("jwks_using_stale_cache")
            return _jwks_cache
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth service temporarily unavailable — cannot fetch JWKS",
        )


async def invalidate_jwks_cache() -> None:
    """Force a JWKS refresh on next request (call after Keycloak key rotation)."""
    global _jwks_cache, _jwks_fetched
    _jwks_cache   = None
    _jwks_fetched = 0.0


# ── Security scheme ───────────────────────────────────────────────────────────
_bearer = HTTPBearer(
    scheme_name="Keycloak JWT",
    description="Bearer token issued by Keycloak realm `kerala-police-drone`.",
)


async def validate_token_string(token: str) -> dict:
    """Validate a raw JWT token string against Keycloak JWKS and verify role."""
    try:
        jwks = await get_jwks()
        payload = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            options={"verify_exp": True, "verify_aud": False},
        )
    except JWTError as exc:
        log.error("jwt_validation_failed details: %s", str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    realm_roles: list[str] = payload.get("realm_access", {}).get("roles", [])
    if not ALLOWED_ROLES.intersection(realm_roles):
        log.warning(
            "jwt_role_denied",
            extra={"sub": payload.get("sub"), "roles": realm_roles},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role required: one of {sorted(ALLOWED_ROLES)}",
        )

    return payload


async def validate_token(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """FastAPI dependency — validates the Bearer JWT and enforces role membership."""
    return await validate_token_string(creds.credentials)


async def validate_token_flexible(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False)),
) -> dict:
    """
    FastAPI dependency for endpoints (streams, snapshots) that accept authentication
    via Bearer header, ?token= query parameter, or dvas_access_token cookie.
    """
    token: Optional[str] = None
    if creds and creds.credentials:
        token = creds.credentials
    elif "token" in request.query_params:
        token = request.query_params["token"]
    elif "dvas_access_token" in request.cookies:
        token = request.cookies["dvas_access_token"]

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token required via Bearer header, query parameter, or cookie",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await validate_token_string(token)


async def validate_token_optional(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(
        HTTPBearer(auto_error=False)
    ),
) -> Optional[dict]:
    """Like validate_token but returns None if no token is provided."""
    if creds is None:
        return None
    return await validate_token(creds)


def extract_username(payload: dict) -> str:
    """Return the preferred_username or sub from a decoded JWT payload."""
    return payload.get("preferred_username") or payload.get("sub", "unknown")
