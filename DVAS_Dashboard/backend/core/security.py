import logging
from typing import Literal
from fastapi import Depends, HTTPException, Request
from jose import jwt

from core.config import KEYCLOAK_URL, KEYCLOAK_REALM
from audit_service import write_audit_v2

logger = logging.getLogger("dvas")

JWKS_CACHE = {}

def fetch_jwks(force: bool = False):
    global JWKS_CACHE
    if JWKS_CACHE and not force:
        return JWKS_CACHE
    url = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs"
    try:
        import requests
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            JWKS_CACHE = resp.json()
            logger.info("Successfully fetched JWKS from Keycloak")
            return JWKS_CACHE
        else:
            logger.error(f"Failed to fetch JWKS: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.error(f"Failed to fetch JWKS: {e}")
    return JWKS_CACHE

def get_jwks():
    global JWKS_CACHE
    if not JWKS_CACHE:
        fetch_jwks()
    return JWKS_CACHE

Role = Literal["drone_operator", "control_room_officer", "senior_officer", "system_administrator"]

ROLE_MAP = {
    "sys-admin": "system_administrator",
    "supervisor": "senior_officer",
    "control-room": "control_room_officer",
    "drone-operator": "drone_operator",
}

async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("dvas_access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        options = {"verify_aud": False}
        payload = jwt.decode(token, get_jwks(), algorithms=["RS256"], options=options)
        
        realm_access = payload.get("realm_access", {})
        roles = realm_access.get("roles", [])
        
        user_role = "drone_operator"
        for kc_role, mapped_role in ROLE_MAP.items():
            if kc_role in roles:
                user_role = mapped_role
                if user_role == "system_administrator":
                    break

        attrs_jurisdiction = payload.get("jurisdiction")
        jurisdiction_val = ""
        if isinstance(attrs_jurisdiction, list) and len(attrs_jurisdiction) > 0:
            jurisdiction_val = str(attrs_jurisdiction[0])
        elif isinstance(attrs_jurisdiction, str):
            jurisdiction_val = attrs_jurisdiction

        mission_scope_val = payload.get("mission_scope", [])
        if isinstance(mission_scope_val, str):
            mission_scope_val = [mission_scope_val]

        return {
            "id": payload.get("sub"),
            "email": payload.get("email", ""),
            "name": payload.get("name") or payload.get("preferred_username", ""),
            "role": user_role,
            "jurisdiction": jurisdiction_val,
            "mission_scope": mission_scope_val,
        }
    except jwt.ExpiredSignatureError:
        logger.error("get_current_user: Token expired")
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.JWTError as e:
        logger.error(f"get_current_user: Invalid token: {e}")
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

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
