import asyncio
import logging
import time
from typing import Literal, Optional

import httpx
from fastapi import Depends, HTTPException, Request
from jose import jwt

from core.config import KEYCLOAK_URL, KEYCLOAK_REALM
from audit_service import write_audit_v2

logger = logging.getLogger("dvas")

# ── Keycloak signing keys (M5) ───────────────────────────────────────────────
# Cached for JWKS_TTL_S and re-fetched when a token names an unknown key (key
# rotation), with a short cool-down so a flood of bad tokens cannot hammer Keycloak.
JWKS_CACHE: dict = {}
JWKS_TTL_S = 3600
_jwks_fetched_at = 0.0
_jwks_lock = asyncio.Lock()
_JWKS_URL = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs"


def fetch_jwks(force: bool = False):
    """Synchronous fetch, used once at start-up."""
    global JWKS_CACHE, _jwks_fetched_at
    if JWKS_CACHE and not force:
        return JWKS_CACHE
    try:
        import requests
        resp = requests.get(_JWKS_URL, timeout=5)
        if resp.status_code == 200:
            JWKS_CACHE, _jwks_fetched_at = resp.json(), time.monotonic()
            logger.info("Fetched JWKS from Keycloak")
        else:
            logger.error("Failed to fetch JWKS: HTTP %s", resp.status_code)
    except Exception as e:
        logger.error("Failed to fetch JWKS: %s", e)
    return JWKS_CACHE


async def _jwks(kid: Optional[str]) -> dict:
    global JWKS_CACHE, _jwks_fetched_at
    known = {k.get("kid") for k in JWKS_CACHE.get("keys", [])}
    age = time.monotonic() - _jwks_fetched_at
    if JWKS_CACHE and age < JWKS_TTL_S and (kid is None or kid in known):
        return JWKS_CACHE
    if JWKS_CACHE and kid not in known and age < 30:
        return JWKS_CACHE                       # cool-down: unknown kid fetched < 30 s ago
    async with _jwks_lock:
        if time.monotonic() - _jwks_fetched_at < 1 and JWKS_CACHE:
            return JWKS_CACHE
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(_JWKS_URL)
            if r.status_code == 200:
                JWKS_CACHE, _jwks_fetched_at = r.json(), time.monotonic()
            else:
                logger.error("JWKS refresh failed: HTTP %s", r.status_code)
        except Exception as e:
            logger.error("JWKS refresh failed: %s", e)
    return JWKS_CACHE


Role = Literal["drone_operator", "control_room_officer", "senior_officer", "system_administrator"]

# Keycloak realm role → DVAS role, in priority order: a user holding several
# realm roles always gets the highest one (the old loop let the last match win).
ROLE_PRIORITY = (
    ("sys-admin", "system_administrator"),
    ("supervisor", "senior_officer"),
    ("control-room", "control_room_officer"),
    ("drone-operator", "drone_operator"),
)
ROLE_MAP = dict(ROLE_PRIORITY)
DVAS_ROLES = tuple(r for _, r in ROLE_PRIORITY)


def derive_role(realm_roles) -> Optional[str]:
    roles = set(realm_roles or [])
    for kc_role, dvas_role in ROLE_PRIORITY:
        if kc_role in roles:
            return dvas_role
    return None


class AuthError(Exception):
    pass


async def decode_token(token: str) -> dict:
    """Verify signature, expiry and issuer. Raises AuthError with a generic message."""
    try:
        header = jwt.get_unverified_header(token)
    except Exception:
        raise AuthError("Invalid token")
    keys = await _jwks(header.get("kid"))
    try:
        payload = jwt.decode(token, keys, algorithms=["RS256"], options={"verify_aud": False})
    except jwt.ExpiredSignatureError:
        raise AuthError("Token expired")
    except Exception:
        raise AuthError("Invalid token")
    # Issuer: this realm (the host differs between the pod URL and 127.0.0.1)
    if not str(payload.get("iss", "")).rstrip("/").endswith(f"/realms/{KEYCLOAK_REALM}"):
        raise AuthError("Invalid token")
    return payload


async def user_from_payload(payload: dict) -> dict:
    """DVAS user from a verified token. Keycloak's realm roles are authoritative for the
    role; the users table only fills in name / jurisdiction / mission scope."""
    role = derive_role((payload.get("realm_access") or {}).get("roles"))
    if role is None:
        raise AuthError("No DVAS role assigned")
    jur = payload.get("jurisdiction")
    jurisdiction = str(jur[0]) if isinstance(jur, list) and jur else (jur if isinstance(jur, str) else "")
    scope = payload.get("mission_scope") or []
    if isinstance(scope, str):
        scope = [scope]
    email = payload.get("email", "")
    name = payload.get("name") or payload.get("preferred_username", "")
    if email:
        try:
            from database import async_session_factory
            from models import User
            from sqlalchemy import select
            async with async_session_factory() as session:
                row = (await session.execute(
                    select(User.name, User.jurisdiction, User.mission_scope, User.role)
                    .where(User.email == email))).first()
            if row:
                name = row[0] or name
                jurisdiction = jurisdiction or row[1] or ""
                scope = scope or row[2] or []
                if row[3] and row[3] != role:
                    logger.warning("Users-table role %s differs from Keycloak role %s for %s; using Keycloak",
                                   row[3], role, email)
        except Exception as e:
            logger.debug("Could not hydrate user attributes from DB: %s", e)
    return {"id": payload.get("sub"), "email": email, "name": name, "role": role,
            "jurisdiction": jurisdiction, "mission_scope": scope, "exp": payload.get("exp")}


async def user_from_token(token: str) -> dict:
    return await user_from_payload(await decode_token(token))


async def get_current_user(request: Request) -> dict:
    """Bearer header (edge app, services) or the httpOnly cookie (browser).
    Tokens in URLs are not accepted here (H5); WebSockets use authenticate_ws()."""
    token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    if not token:
        token = request.cookies.get("dvas_access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return await user_from_token(token)
    except AuthError as e:
        msg = str(e)
        raise HTTPException(status_code=403 if msg == "No DVAS role assigned" else 401, detail=msg)


async def authenticate_ws(websocket) -> Optional[dict]:
    """WebSocket handshake: browsers send the cookie; the edge app (other origin) may
    send ?token=. Returns the user, or None (caller closes with 4401)."""
    token = websocket.query_params.get("token") or websocket.cookies.get("dvas_access_token", "")
    if not token:
        return None
    try:
        return await user_from_token(token)
    except AuthError:
        return None


def _user_to_dict(u, exclude_password: bool = False) -> dict:
    """Convert a User ORM object to a plain dict."""
    d = {
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "role": u.role,
        "jurisdiction": u.jurisdiction,
        "mission_scope": u.mission_scope or [],
        "suspended": u.suspended,
        "created_at": u.created_at,
    }
    if not exclude_password:
        d["password_hash"] = u.password_hash
    return d

def _row_to_dict(obj) -> dict:
    """Generic ORM row → dict (excludes SQLAlchemy internals)."""
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}

def require_roles(*roles: str):
    async def _dep(user: dict = Depends(get_current_user)):
        if user["role"] not in roles:
            raise HTTPException(
                status_code=403, detail=f"Role '{user['role']}' not permitted"
            )
        return user

    return _dep

async def write_audit(actor: dict, action: str, target: str = "", details: str = "", ip: str = "-"):
    """
    LEGACY WRAPPER — delegates to write_audit_v2() from audit_service.py.
    Kept for backward compatibility during Phase 2 migration.
    All new code should call write_audit_v2() directly.
    """
    await write_audit_v2(
        actor=actor,
        action=action,
        target=target,
        details=details,
        ip=ip,
        source_module="dashboard",
    )

def scope_filter_for(user: dict):
    """Return scope info dict based on ABAC (jurisdiction / mission_scope).

    4-role model:
      senior_officer       – jurisdiction-wide + cross-jurisdiction aggregate access
      control_room_officer – active mission scope only (former field_supervisor scope merged in)
      others               – deny
    """
    role = user["role"]
    if role == "senior_officer":
        return {}  # full jurisdiction-wide + cross-jurisdiction aggregate access
    if role == "control_room_officer":
        # jurisdiction-scoped access (covers former field_supervisor capability)
        return {"jurisdiction": user.get("jurisdiction")}
    return {"__deny__": True}  # everyone else: no direct data access

def _apply_scope_to_query(stmt, model, scope: dict):
    """Apply scope_filter_for result to a SQLAlchemy select statement."""
    if "__deny__" in scope:
        return None  # caller should raise 403
    if "jurisdiction" in scope:
        stmt = stmt.where(model.jurisdiction == scope["jurisdiction"])
    if "mission_id" in scope:
        in_list = scope["mission_id"].get("$in", [])
        if in_list:
            stmt = stmt.where(model.mission_id.in_(in_list))
        else:
            # empty mission scope → no results
            stmt = stmt.where(model.mission_id == "__NONE__")
    return stmt


# ── One jurisdiction rule for every list, count and live push (J1) ──────────
# A control room officer sees rows of their own jurisdiction, statewide rows and
# rows with no jurisdiction yet. Seniors and admins see everything. A CRO with no
# jurisdiction configured is treated as state-level.
STATEWIDE_JURISDICTIONS = ("Kerala", "State HQ")
STATEWIDE_ROLES = ("senior_officer", "system_administrator")


def jurisdiction_clause(column, user: dict):
    """SQLAlchemy filter for `column`, or None when the user is unrestricted."""
    from sqlalchemy import or_
    if user.get("role") in STATEWIDE_ROLES:
        return None
    jur = user.get("jurisdiction")
    if not jur:
        return None
    return or_(column == jur, column.is_(None), column == "", column.in_(STATEWIDE_JURISDICTIONS))


def jurisdiction_visible(value, user: dict) -> bool:
    """Same rule for a single value (live messages, single-row checks)."""
    if user.get("role") in STATEWIDE_ROLES or not user.get("jurisdiction"):
        return True
    return value in (None, "", user["jurisdiction"]) or value in STATEWIDE_JURISDICTIONS
