from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import os
import uuid
import logging
import random
import hashlib
import bcrypt
from jose import jwt
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Literal
import asyncio
import io
import json
import zipfile
from fastapi import (
    FastAPI,
    APIRouter,
    Body,
    Depends,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import StreamingResponse
import httpx
from starlette.middleware.cors import CORSMiddleware
from sqlalchemy import select, update, delete, func, or_, text
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, EmailStr
from fpdf import FPDF

from database import engine, async_session_factory, get_db
from models import Base, AuditLog, Mission, Drone, Alert, Evidence, Export, ModelConfig
from ingest_proxy import ingest_api, _proxy_get

# ---------- Keycloak Setup ----------
KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL", "http://10.123.225.250:8080")
KEYCLOAK_REALM = os.environ.get("KC_REALM", "kerala-police-drone")
KEYCLOAK_CLIENT_ID = os.environ.get("KC_CLIENT_ID", "dvas-dashboard-client")
KEYCLOAK_CLIENT_SECRET = os.environ.get("KC_CLIENT_SECRET", "yNa0AfA0mToUmhQHSiM2vjAyYud6NSMn")
KEYCLOAK_ADMIN = os.environ.get("KEYCLOAK_ADMIN", "admin")
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


app = FastAPI(title="DVAS API")
api = APIRouter(prefix="/api")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dvas")

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Initialize the limiter using the client's IP address
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.on_event("startup")
async def startup_event():
    fetch_jwks()



# ---------- Auth helpers ----------
# RBAC: 4-role model (field_supervisor → control_room_officer,
#        supervisory_officer → senior_officer — see migrate_roles.py)
Role = Literal[
    "drone_operator",
    "control_room_officer",
    "senior_officer",
    "system_administrator",
]


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


async def write_audit(
    actor: dict, action: str, target: str = "", details: str = "", ip: str = "-"
):
    """Append-only audit log with SHA-256 hash chain (tamper-evident)."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(AuditLog.hash).order_by(AuditLog.ts.desc()).limit(1)
        )
        last_row = result.scalar_one_or_none()
        prev_hash = last_row if last_row else "GENESIS"
        entry = {
            "id": str(uuid.uuid4()),
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor_id": actor.get("id"),
            "actor_email": actor.get("email"),
            "actor_role": actor.get("role"),
            "action": action,
            "target": target,
            "details": details,
            "ip": ip,
            "prev_hash": prev_hash,
        }
        entry["hash"] = _audit_hash(entry)
        session.add(AuditLog(**entry))
        await session.commit()


def _audit_hash(entry: dict) -> str:
    """Deterministic SHA-256 of the canonical fields + prev_hash."""
    canonical = "|".join(
        str(entry.get(k, ""))
        for k in (
            "id",
            "ts",
            "actor_id",
            "actor_email",
            "actor_role",
            "action",
            "target",
            "details",
            "ip",
            "prev_hash",
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------- Models ----------
class LoginIn(BaseModel):
    email: EmailStr
    password: str
    role: str


class ExportRequestIn(BaseModel):
    evidence_id: str
    justification: str


class ExportDecisionIn(BaseModel):
    decision: Literal["approved", "rejected"]
    comment: str = ""


# ---------- Auth endpoints ----------
# ---------- Auth endpoints ----------
class LoginIn(BaseModel):
    email: EmailStr
    password: str

@api.post("/auth/login")
@limiter.limit("5/minute")
async def login(body: LoginIn, response: Response, request: Request):
    email = body.email.lower()
    
    url = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"
    
    USERNAME_MAP = {
        "admin@dvas.kp": "admin_dvas",
        "control@dvas.kp": "control_001",
        "senior@dvas.kp": "senior_001",
        "operator@dvas.kp": "operator_001"
    }
    # Keycloak username from email mapping, or fallback to email itself
    username = USERNAME_MAP.get(email, email.split("@")[0] if "@" in email else email)
    
    data = {
        "client_id": KEYCLOAK_CLIENT_ID,
        "client_secret": KEYCLOAK_CLIENT_SECRET,
        "grant_type": "password",
        "username": username,
        "password": body.password
    }
    
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(url, data=data, timeout=5)
            if r.status_code != 200:
                raise HTTPException(status_code=401, detail="Invalid email or password")
            
            token_data = r.json()
            access_token = token_data["access_token"]
            
            options = {"verify_aud": False}
            payload = jwt.decode(access_token, get_jwks(), algorithms=["RS256"], options=options)
            
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

            user_dict = {
                "id": payload.get("sub"),
                "email": payload.get("email", email),
                "name": payload.get("name") or payload.get("preferred_username", ""),
                "role": user_role,
                "jurisdiction": jurisdiction_val,
                "mission_scope": mission_scope_val,
            }
            
            is_secure = (request.url.scheme == "https" or os.environ.get("ENV") == "production" or os.environ.get("SECURE_COOKIES", "false").lower() == "true")
            response.set_cookie(
                "dvas_access_token",
                access_token,
                httponly=True,
                secure=is_secure,
                samesite="lax",
                max_age=token_data.get("expires_in", 3600),
                path="/"
            )
            if "refresh_token" in token_data:
                response.set_cookie(
                    "dvas_refresh_token",
                    token_data["refresh_token"],
                    httponly=True,
                    secure=is_secure,
                    samesite="lax",
                    max_age=token_data.get("refresh_expires_in", 86400),
                    path="/"
                )
            
            await write_audit(user_dict, "login", email, "login success via Keycloak", request.client.host if request.client else "-")
            
            user_dict["access_token"] = access_token
            return user_dict
            
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="Authentication server unreachable")

@api.post("/auth/refresh")
async def refresh_token(request: Request, response: Response):
    """Silent token refresh using HttpOnly refresh_token cookie."""
    refresh_tok = request.cookies.get("dvas_refresh_token")
    if not refresh_tok:
        raise HTTPException(status_code=401, detail="No refresh token found")

    url = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"
    data = {
        "client_id": KEYCLOAK_CLIENT_ID,
        "client_secret": KEYCLOAK_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": refresh_tok,
    }
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(url, data=data, timeout=5)
            if r.status_code != 200:
                raise HTTPException(status_code=401, detail="Session expired, please login again")
            token_data = r.json()
            new_access_token = token_data["access_token"]
            
            options = {"verify_aud": False}
            payload = jwt.decode(new_access_token, get_jwks(), algorithms=["RS256"], options=options)
            
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

            user_dict = {
                "id": payload.get("sub"),
                "email": payload.get("email", ""),
                "name": payload.get("name") or payload.get("preferred_username", ""),
                "role": user_role,
                "jurisdiction": jurisdiction_val,
                "mission_scope": mission_scope_val,
            }

            is_secure = (request.url.scheme == "https" or os.environ.get("ENV") == "production" or os.environ.get("SECURE_COOKIES", "false").lower() == "true")
            response.set_cookie(
                "dvas_access_token",
                new_access_token,
                httponly=True,
                secure=is_secure,
                samesite="lax",
                max_age=token_data.get("expires_in", 3600),
                path="/"
            )
            if "refresh_token" in token_data:
                response.set_cookie(
                    "dvas_refresh_token",
                    token_data["refresh_token"],
                    httponly=True,
                    secure=is_secure,
                    samesite="lax",
                    max_age=token_data.get("refresh_expires_in", 86400),
                    path="/"
                )
            user_dict["access_token"] = new_access_token
            return user_dict
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="Authentication server unreachable")

@api.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return user


@api.post("/auth/logout")
async def logout(
    response: Response, user: dict = Depends(get_current_user), request: Request = None
):
    response.delete_cookie("dvas_access_token", path="/")
    response.delete_cookie("dvas_refresh_token", path="/")
    await write_audit(
        user,
        "logout",
        user["email"],
        "",
        request.client.host if request and request.client else "-",
    )
    return {"ok": True}


# ---------- Data endpoints ----------
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


@api.get("/drones")
async def list_drones(user: dict = Depends(get_current_user)):
    async with async_session_factory() as session:
        stmt = select(Drone)
        if user["role"] == "drone_operator":
            stmt = stmt.where(Drone.operator_id == user["id"])
        elif user["role"] == "system_administrator":
            pass  # infra view — connection status only
        else:
            f = scope_filter_for(user)
            if "__deny__" in f:
                raise HTTPException(403, "Not permitted")
            stmt = _apply_scope_to_query(stmt, Drone, f)
            if stmt is None:
                raise HTTPException(403, "Not permitted")
        stmt = stmt.limit(200)
        result = await session.execute(stmt)
        rows = result.scalars().all()
        docs = [_row_to_dict(r) for r in rows]
        if user["role"] == "system_administrator":
            for d in docs:
                d.pop("last_analytics", None)
        return docs


@api.get("/alerts")
async def list_alerts(user: dict = Depends(get_current_user)):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    f = scope_filter_for(user)
    if "__deny__" in f:
        raise HTTPException(403, "Not permitted")
    async with async_session_factory() as session:
        stmt = select(Alert)
        stmt = _apply_scope_to_query(stmt, Alert, f)
        if stmt is None:
            raise HTTPException(403, "Not permitted")
        stmt = stmt.order_by(Alert.ts.desc()).limit(200)
        result = await session.execute(stmt)
        return [_row_to_dict(r) for r in result.scalars().all()]


@api.post("/alerts/{alert_id}/ack")
async def ack_alert(
    alert_id: str, user: dict = Depends(get_current_user), request: Request = None
):
    # CRO: inherits field_supervisor ack capability; SR: inherits supervisory_officer ack capability
    if user["role"] not in ("control_room_officer", "senior_officer"):
        raise HTTPException(403, "Not permitted")
    async with async_session_factory() as session:
        result = await session.execute(
            update(Alert)
            .where(Alert.id == alert_id)
            .values(
                acknowledged_by=user["email"],
                acknowledged_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        if result.rowcount == 0:
            raise HTTPException(404, "Alert not found")
        await session.commit()
    await write_audit(user, "alert_ack", alert_id, "acknowledged")
    return {"ok": True}


@api.get("/evidence")
async def list_evidence(user: dict = Depends(get_current_user)):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    f = scope_filter_for(user)
    if "__deny__" in f:
        raise HTTPException(403, "Not permitted")
    async with async_session_factory() as session:
        stmt = select(Evidence)
        stmt = _apply_scope_to_query(stmt, Evidence, f)
        if stmt is None:
            raise HTTPException(403, "Not permitted")
        stmt = stmt.order_by(Evidence.ts.desc()).limit(200)
        result = await session.execute(stmt)
        return [_row_to_dict(r) for r in result.scalars().all()]


@api.get("/missions")
async def list_missions(user: dict = Depends(get_current_user)):
    # Synchronize missing missions from ANPR microservice
    try:
        anpr_res = await _analytics_get(f"{ANPR_URL}/missions", timeout=5.0)
        if anpr_res and "missions" in anpr_res:
            active_mission_ids = set(anpr_res["missions"])
            async with async_session_factory() as session:
                existing = await session.execute(select(Mission.mission_id).where(Mission.mission_id.in_(active_mission_ids)))
                existing_ids = {row[0] for row in existing.all()}
                
                missing_ids = active_mission_ids - existing_ids
                if missing_ids:
                    logger.info(f"Synchronizing {len(missing_ids)} missing missions from ANPR")
                    for mid in missing_ids:
                        # Extract timestamp from mission_id like MSN-20260813-2204-52-ALPHA
                        # Parts: MSN, YYYYMMDD, HHMM, SS, SUFFIX
                        started_at = ""
                        parts = mid.split("-")
                        if len(parts) >= 4 and parts[1].isdigit() and parts[2].isdigit():
                            try:
                                d = parts[1]
                                t = parts[2]
                                s = parts[3] if parts[3].isdigit() else "00"
                                started_at = f"{d[:4]}-{d[4:6]}-{d[6:8]}T{t[:2]}:{t[2:4]}:{s[:2]}+00:00"
                            except Exception:
                                pass
                                
                        session.add(Mission(
                            mission_id=mid,
                            name=mid,  # Fallback name
                            status="recovered",
                            drone_id="",
                            operator_name="System Recovered",
                            operator_ids=[], # Recovered missions will be visible to officers
                            started_at=started_at
                        ))
                    await session.commit()
    except Exception as e:
        logger.warning(f"Could not synchronize missions from ANPR: {e}")

    async with async_session_factory() as session:
        if user["role"] == "drone_operator":
            # operator_ids is JSONB array — use contains operator
            stmt = select(Mission).where(
                Mission.operator_ids.contains([user["id"]])
            ).order_by(Mission.started_at.desc()).limit(100)
            result = await session.execute(stmt)
            return [_row_to_dict(r) for r in result.scalars().all()]

        # All other roles (senior_officer, control_room_officer, system_administrator)
        # can see all missions — filtered by jurisdiction if set
        stmt = select(Mission)
        if user["role"] == "control_room_officer":
            jur = user.get("jurisdiction")
            if jur:
                # Show missions in their jurisdiction OR missions with no jurisdiction set
                stmt = stmt.where(
                    or_(Mission.jurisdiction == jur, Mission.jurisdiction == None, Mission.jurisdiction == "")
                )
        stmt = stmt.order_by(Mission.started_at.desc()).limit(100)
        result = await session.execute(stmt)
        return [_row_to_dict(r) for r in result.scalars().all()]


# ---------- Mission create / end (wired to Edge Mission Control) ----------
@api.post("/missions")
async def create_mission(
    payload: dict = Body(...),
    user: dict = Depends(get_current_user)
):
    """
    Save a new mission record to the database.
    Called by the frontend after the edge node confirms mission start.
    """
    mission_id = payload.get("mission_id")
    name = payload.get("name") or payload.get("mission_name", "Unnamed Mission")
    drone_id = payload.get("drone_id", "")
    operator_name = payload.get("operator_name") or payload.get("operator", "")
    location = payload.get("location", "")
    started_at = payload.get("started_at", "")

    if not mission_id:
        raise HTTPException(400, "mission_id is required")

    async with async_session_factory() as session:
        # Check for duplicate
        existing = await session.get(Mission, mission_id)
        if existing:
            return {"status": "already_exists", "mission_id": mission_id}

        mission = Mission(
            mission_id=mission_id,
            name=name,
            status="active",
            drone_id=drone_id,
            operator_name=operator_name,
            zone=location,
            started_at=started_at,
            operator_ids=[user["id"]],  # IMPORTANT: save current user ID so they can see this mission
            jurisdiction=user.get("jurisdiction", "")
        )
        session.add(mission)
        await session.commit()

    return {"status": "saved", "mission_id": mission_id}


@api.patch("/missions/{mission_id}/end")
async def end_mission_record(mission_id: str, user: dict = Depends(get_current_user)):
    """Mark a mission as completed and record the end time."""
    async with async_session_factory() as session:
        mission = await session.get(Mission, mission_id)
        if not mission:
            raise HTTPException(404, f"Mission {mission_id} not found")
        # Check jurisdiction / mission scope if scoped
        if user.get("mission_scope") and mission_id not in user["mission_scope"]:
            raise HTTPException(403, f"Not permitted to end mission {mission_id}")
        mission.status = "completed"
        mission.ended_at = datetime.now(timezone.utc).isoformat()
        await session.commit()
    return {"status": "ended", "mission_id": mission_id}


# ---------- Export approval workflow ----------
@api.get("/exports")
async def list_exports(user: dict = Depends(get_current_user)):
    # CRO (former field_supervisor): sees own submitted requests
    # Senior Officer (former supervisory_officer): sees all exports (jurisdiction-wide authority)
    if user["role"] not in ("control_room_officer", "senior_officer"):
        raise HTTPException(403, "Not permitted")
    async with async_session_factory() as session:
        stmt = select(Export)
        if user["role"] == "control_room_officer":
            stmt = stmt.where(Export.requested_by == user["email"])
        # senior_officer: no filter — full jurisdiction-wide access to all exports
        stmt = stmt.order_by(Export.requested_at.desc()).limit(200)
        result = await session.execute(stmt)
        return [_row_to_dict(r) for r in result.scalars().all()]


@api.post("/exports")
async def request_export(
    body: ExportRequestIn,
    user: dict = Depends(get_current_user),
    request: Request = None,
):
    # Control Room Officer inherits the former field_supervisor export-request capability
    if user["role"] != "control_room_officer":
        raise HTTPException(403, "Only Control Room Officer may request exports")
    async with async_session_factory() as session:
        result = await session.execute(
            select(Evidence).where(Evidence.id == body.evidence_id)
        )
        ev_obj = result.scalar_one_or_none()
        if not ev_obj:
            raise HTTPException(404, "Evidence not found")
        ev = _row_to_dict(ev_obj)
        doc = {
            "id": str(uuid.uuid4()),
            "evidence_id": body.evidence_id,
            "evidence_label": ev["label"],
            "jurisdiction": ev["jurisdiction"],
            "requested_by": user["email"],
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "justification": body.justification,
            "status": "pending",
            "decision_by": None,
            "decision_at": None,
            "decision_comment": None,
        }
        session.add(Export(**doc))
        await session.commit()
    await write_audit(user, "export_request", ev["id"], body.justification)
    return doc


@api.post("/exports/{export_id}/decision")
async def decide_export(
    export_id: str, body: ExportDecisionIn, user: dict = Depends(get_current_user)
):
    # Senior Officer inherits full export approve/reject authority (former supervisory_officer)
    if user["role"] != "senior_officer":
        raise HTTPException(403, "Only Senior Officer may approve/reject exports")
    async with async_session_factory() as session:
        result = await session.execute(
            select(Export).where(Export.id == export_id)
        )
        exp_obj = result.scalar_one_or_none()
        if not exp_obj:
            raise HTTPException(404, "Export not found")
        # Senior Officer has jurisdiction-wide authority — no jurisdiction restriction
        await session.execute(
            update(Export)
            .where(Export.id == export_id)
            .values(
                status=body.decision,
                decision_by=user["email"],
                decision_at=datetime.now(timezone.utc).isoformat(),
                decision_comment=body.comment,
            )
        )
        await session.commit()
    await write_audit(user, f"export_{body.decision}", export_id, body.comment)
    return {"ok": True}


# ---------- Audit logs ----------
@api.get("/audit-logs")
async def list_audit(user: dict = Depends(get_current_user), q: Optional[str] = None):
    # Senior Officer now holds BOTH jurisdiction-detail AND cross-jurisdiction aggregate audit access
    # (former supervisory_officer unit-scope view merged into senior_officer)
    if user["role"] not in ("senior_officer", "system_administrator"):
        raise HTTPException(403, "Not permitted")
    async with async_session_factory() as session:
        stmt = select(AuditLog)
        if user["role"] == "system_administrator":
            # Admin sees only own actions
            stmt = stmt.where(AuditLog.actor_id == user["id"])
        if q:
            pattern = f"%{q}%"
            stmt = stmt.where(
                or_(
                    AuditLog.action.ilike(pattern),
                    AuditLog.actor_email.ilike(pattern),
                    AuditLog.target.ilike(pattern),
                )
            )
        stmt = stmt.order_by(AuditLog.ts.desc()).limit(300)
        result = await session.execute(stmt)
        docs = [_row_to_dict(r) for r in result.scalars().all()]

        # Verify hash-chain integrity for each row (chronological order = reverse).
        # We must verify against the FULL chain (unfiltered) but return only filtered rows
        # with a per-row `tamper` flag.
        chain_result = await session.execute(
            select(AuditLog).order_by(AuditLog.ts.asc())
        )
        all_chain = [_row_to_dict(r) for r in chain_result.scalars().all()]
        tampered_ids = set()
        prev_hash = "GENESIS"
        for e in all_chain:
            expected = _audit_hash({**e, "prev_hash": prev_hash})
            if e.get("hash") != expected or e.get("prev_hash") != prev_hash:
                tampered_ids.add(e["id"])
            prev_hash = e.get("hash", prev_hash)
        for d in docs:
            d["tamper"] = d["id"] in tampered_ids
        return docs


@api.get("/audit-logs/verify")
async def verify_audit_chain(
    user: dict = Depends(
        require_roles("senior_officer", "system_administrator")
    ),
):
    """Return overall chain integrity summary."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(AuditLog).order_by(AuditLog.ts.asc())
        )
        chain = [_row_to_dict(r) for r in result.scalars().all()]
    tampered = []
    prev_hash = "GENESIS"
    for e in chain:
        expected = _audit_hash({**e, "prev_hash": prev_hash})
        if e.get("hash") != expected or e.get("prev_hash") != prev_hash:
            tampered.append(e["id"])
        prev_hash = e.get("hash", prev_hash)
    return {
        "total": len(chain),
        "tampered_count": len(tampered),
        "tampered_ids": tampered[:20],
        "integrity_ok": len(tampered) == 0,
    }


# ---------- Edge Operator: End-of-Shift PDF summary ----------
def _make_shift_pdf(
    user: dict, missions: list, buffer_stats: dict, anomalies: list
) -> bytes:
    def _s(x):
        return str(x).encode("latin-1", "replace").decode("latin-1")

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, "END-OF-SHIFT SUMMARY", ln=1, align="C")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, "Drone Edge Operator - Session Handover Report", ln=1, align="C")
    pdf.ln(3)
    pdf.set_draw_color(120, 120, 120)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(3)

    def kv(k, v):
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(55, 6, _s(f"{k}:"), ln=0)
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(135, 6, _s(v))

    kv("Operator", f"{user.get('name', '-')} ({user.get('email', '-')})")
    kv("Role", user.get("role", "-"))
    kv("Jurisdiction", user.get("jurisdiction", "-"))
    kv("Shift ended (UTC)", datetime.now(timezone.utc).isoformat())
    kv("Missions covered", ", ".join(m.get("name", "-") for m in missions) or "—")
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Buffer & Stream Metrics", ln=1)
    pdf.set_font("Helvetica", "", 10)
    kv("Buffer utilisation (avg)", f"{buffer_stats.get('avg_pct', '—')}%")
    kv("Overwrite cycles", str(buffer_stats.get("overwrite_cycles", 0)))
    kv("Stream drops", str(buffer_stats.get("drops", 0)))
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Anomalies / Malfunctions reported", ln=1)
    pdf.set_font("Helvetica", "", 9)
    if not anomalies:
        pdf.multi_cell(0, 5, "None during this shift.")
    else:
        for a in anomalies:
            pdf.multi_cell(
                0,
                5,
                _s(f"- [{a.get('ts', '')}] {a.get('type', '-')}: {a.get('note', '-')}"),
            )
    pdf.ln(3)

    pdf.set_font("Helvetica", "", 8)
    pdf.multi_cell(
        0,
        4,
        "This handover is generated at the edge and relayed to Central Ingest. "
        "No decisions are taken on device. All operator actions during the shift are recorded in the tamper-evident DVAS audit log.",
    )
    pdf.ln(6)
    pdf.cell(90, 6, "_______________________", ln=0)
    pdf.cell(0, 6, "_______________________", ln=1)
    pdf.cell(90, 5, "Operator Signature", ln=0)
    pdf.cell(0, 5, "Ingest Supervisor Ack.", ln=1)
    return bytes(pdf.output(dest="S"))


@api.get("/edge/shift-summary")
async def edge_shift_summary(request: Request, token: str = ""):
    # Support Authorization header, cookie, OR query token for direct <a> downloads
    if token:
        try:
            options = {"verify_aud": False}
            payload = jwt.decode(token, get_jwks(), algorithms=["RS256"], options=options)
            user = {
                "id": payload.get("sub"),
                "email": payload.get("email", ""),
                "role": "control_room_officer", # Assuming higher role for exports
            }
        except jwt.JWTError:
            raise HTTPException(401, "Invalid token")
    else:
        user = await get_current_user(request)
    if user.get("role") != "drone_operator":
        raise HTTPException(403, "Only drone operators may generate shift summaries")
    # Look up missions in operator's scope
    missions = []
    if user.get("mission_scope"):
        async with async_session_factory() as session:
            result = await session.execute(
                select(Mission).where(Mission.mission_id.in_(user["mission_scope"]))
            )
            missions = [_row_to_dict(r) for r in result.scalars().all()]
    # Look up own audit entries in last 12h to summarise anomalies reported
    since = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
    async with async_session_factory() as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.actor_id == user["id"],
                AuditLog.ts >= since,
                or_(
                    AuditLog.action.ilike("%anomaly%"),
                    AuditLog.action.ilike("%malfunction%"),
                ),
            )
        )
        own = [_row_to_dict(r) for r in result.scalars().all()]
    anomalies = [
        {
            "ts": e.get("ts", ""),
            "type": e.get("action", "-"),
            "note": e.get("details", "-"),
        }
        for e in own
    ]
    buffer_stats = {
        "avg_pct": random.randint(60, 92),
        "overwrite_cycles": random.randint(40, 220),
        "drops": random.randint(0, 3),
    }
    pdf_bytes = _make_shift_pdf(user, missions, buffer_stats, anomalies)
    await write_audit(user, "shift_summary_download", "-", f"missions={len(missions)}")
    fname = f"shift_summary_{user['id'][:8]}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ---------- Users (admin) — Keycloak-backed ----------
from keycloak_service import (
    list_users_from_keycloak,
    provision_user_in_keycloak,
    disable_user_in_keycloak,
)


@api.get("/users")
async def list_users(user: dict = Depends(require_roles("system_administrator"))):
    return await list_users_from_keycloak()


class UserCreateIn(BaseModel):
    email: EmailStr
    name: str
    role: Role
    jurisdiction: str = ""
    mission_scope: List[str] = []
    password: str = "dvas1234"


class UserPatchIn(BaseModel):
    name: Optional[str] = None
    role: Optional[Role] = None
    jurisdiction: Optional[str] = None
    mission_scope: Optional[List[str]] = None
    suspended: Optional[bool] = None


@api.post("/users")
async def create_user(
    body: UserCreateIn, user: dict = Depends(require_roles("system_administrator")),
    request: Request = None,
):
    # Password complexity check (min 8 chars)
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    result = await provision_user_in_keycloak(
        name=body.name,
        email=body.email.lower(),
        role=body.role,
        jurisdiction=body.jurisdiction,
        mission_scope=body.mission_scope,
        password=body.password,
    )
    await write_audit(
        user, "user_create", body.email.lower(),
        f"role={body.role}, jurisdiction={body.jurisdiction}",
        request.client.host if request and request.client else "-",
    )
    return result


@api.patch("/users/{user_id}")
async def patch_user(
    user_id: str,
    body: UserPatchIn,
    user: dict = Depends(require_roles("system_administrator")),
):
    raise HTTPException(501, "User modification is managed via Keycloak Admin Console.")


@api.delete("/users/{user_id}")
async def delete_user(
    user_id: str, user: dict = Depends(require_roles("system_administrator")),
    request: Request = None,
):
    await disable_user_in_keycloak(user_id)
    await write_audit(
        user, "user_suspend", user_id, "disabled via Keycloak",
        request.client.host if request and request.client else "-",
    )
    return {"ok": True}


# ---------- Ingest / infra health ----------
@api.get("/infra/health")
async def infra_health(user: dict = Depends(require_roles("system_administrator"))):
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        result = await session.execute(select(Drone).limit(50))
        drones = [_row_to_dict(r) for r in result.scalars().all()]
    feeds = [
        {
            "drone_id": d["id"],
            "operator": d["operator_name"],
            "mission_id": d["mission_id"],
            "status": d["status"],
            "last_heartbeat": (
                now - timedelta(seconds=random.randint(0, 30))
            ).isoformat(),
            "fps": random.randint(24, 30),
            "bitrate_mbps": round(random.uniform(3.4, 8.1), 2),
        }
        for d in drones
    ]
    gpu = [
        {
            "node": f"gpu-{i}",
            "util_pct": random.randint(45, 92),
            "batch": random.choice([8, 16, 32]),
            "inference_ms": random.randint(18, 65),
        }
        for i in range(1, 5)
    ]
    queue = [
        {
            "partition": f"p-{i}",
            "depth": random.randint(0, 240),
            "throughput_fps": random.randint(60, 220),
        }
        for i in range(1, 9)
    ]
    alerts_infra = [
        {
            "id": "i-1",
            "severity": "warn",
            "text": "Ingest node 2 CPU 91%",
            "ts": now.isoformat(),
        },
        {
            "id": "i-2",
            "severity": "info",
            "text": "Stream partition 7 backlog cleared",
            "ts": (now - timedelta(minutes=6)).isoformat(),
        },
        {
            "id": "i-3",
            "severity": "crit",
            "text": "GPU-3 inference latency spiked",
            "ts": (now - timedelta(minutes=14)).isoformat(),
        },
    ]
    return {"feeds": feeds, "gpu": gpu, "queue": queue, "alerts": alerts_infra}


# ---------- Detection Models (GPU) ----------

# Maps each model key → (service_base_url, stream_key_used_in_health)
# Note: 'face' shares the person-analyzer service (same container)
# Note: 'plate' is the anpr-analyzer service
SERVICE_MAP: dict = {}  # populated after URL constants are defined (see below)

AVAILABLE_MODELS = [
    {"key": "person",       "label": "Person Detection",             "gpu_pct": 18, "throughput_fps": 90, "default": True},
    {"key": "face",         "label": "Face Detection",               "gpu_pct": 14, "throughput_fps": 72, "default": False},
    {"key": "vehicle",      "label": "Vehicle Detection & Tracking", "gpu_pct": 22, "throughput_fps": 68, "default": True},
    {"key": "crowd",        "label": "Crowd Count & Heatmap",        "gpu_pct": 26, "throughput_fps": 45, "default": True},
    {"key": "plate",        "label": "Number-Plate (ANPR)",          "gpu_pct": 20, "throughput_fps": 40, "default": False},
]


def _build_service_map():
    """Build SERVICE_MAP after analytics URL constants are defined."""
    return {
        "person":  PERSON_URL,
        "face":    PERSON_URL,   # face detection runs inside person-analyzer
        "vehicle": VEHICLE_URL,
        "crowd":   CROWD_URL,
        "plate":   ANPR_URL,
    }


async def _get_service_stream_status() -> dict:
    """
    Query each analytics service health endpoint and return
    {model_key: stream_running bool} for all 4 services.
    face shares person-analyzer status.
    """
    urls = {
        "crowd":   f"{CROWD_URL}/health",
        "person":  f"{PERSON_URL}/health",
        "vehicle": f"{VEHICLE_URL}/health",
        "plate":   f"{ANPR_URL}/health",
    }
    status = {}
    async with httpx.AsyncClient(timeout=4.0) as client:
        for key, url in urls.items():
            try:
                r = await client.get(url)
                data = r.json() if r.status_code == 200 else {}
                status[key] = bool(data.get("stream_running", False))
            except Exception:
                status[key] = False
    status["face"] = status.get("person", False)  # face shares person-analyzer
    return status


async def _apply_stream_changes(requested_active: list) -> dict:
    """
    Start/stop analytics stream loops based on the requested active model list.
    Returns dict of {model_key: action_taken} e.g. {"crowd": "started", "plate": "already_stopped"}
    """
    # Unique services to act on (face→person, plate→anpr, deduplicate)
    service_keys = {"crowd", "person", "vehicle", "plate"}
    # face uses person-analyzer — if face OR person is active, person runs
    effective_active = set(requested_active)
    if "face" in effective_active:
        effective_active.add("person")

    current_status = await _get_service_stream_status()
    results = {}

    smap = {
        "crowd":   CROWD_URL,
        "person":  PERSON_URL,
        "vehicle": VEHICLE_URL,
        "plate":   ANPR_URL,
    }

    async with httpx.AsyncClient(timeout=8.0) as client:
        for svc_key, base_url in smap.items():
            should_run = svc_key in effective_active
            is_running  = current_status.get(svc_key, False)

            if should_run and not is_running:
                try:
                    r = await client.get(f"{base_url}/stream/start")
                    results[svc_key] = "started" if r.status_code == 200 else f"start_failed({r.status_code})"
                except Exception as e:
                    results[svc_key] = f"start_error({e})"
            elif not should_run and is_running:
                try:
                    r = await client.get(f"{base_url}/stream/stop")
                    results[svc_key] = "stopped" if r.status_code == 200 else f"stop_failed({r.status_code})"
                except Exception as e:
                    results[svc_key] = f"stop_error({e})"
            else:
                results[svc_key] = "already_running" if is_running else "already_stopped"

    return results


async def _get_model_config(scope: str):
    async with async_session_factory() as session:
        result = await session.execute(
            select(ModelConfig).where(ModelConfig.scope == scope)
        )
        obj = result.scalar_one_or_none()
        if not obj:
            active = [m["key"] for m in AVAILABLE_MODELS if m["default"]]
            return {"scope": scope, "active": active, "updated_at": None, "updated_by": None}
        return _row_to_dict(obj)


@api.get("/models")
async def list_models(user: dict = Depends(get_current_user)):
    if user["role"] not in ("control_room_officer", "senior_officer"):
        raise HTTPException(403, "Not permitted")
    scope = user.get("jurisdiction") or "State HQ"
    cfg = await _get_model_config(scope)
    gpu_total = sum(m["gpu_pct"] for m in AVAILABLE_MODELS if m["key"] in cfg["active"])

    # Fetch real stream_running status from each analytics service
    stream_status = await _get_service_stream_status()

    # Annotate each model with its real running state
    available_with_status = [
        {
            **m,
            "stream_running": stream_status.get(m["key"], False),
        }
        for m in AVAILABLE_MODELS
    ]

    return {
        "available": available_with_status,
        "active": cfg["active"],
        "stream_status": stream_status,   # {"crowd": True, "person": False, ...}
        "scope": scope,
        "gpu_total_pct": gpu_total,
        "updated_at": cfg.get("updated_at"),
        "updated_by": cfg.get("updated_by"),
        "read_only": False,
    }


class ModelUpdateIn(BaseModel):
    active: List[str]


@api.post("/models")
async def update_models(
    body: ModelUpdateIn,
    # CRO (former field_supervisor) + Senior Officer (former supervisory_officer) can update models
    user: dict = Depends(require_roles("control_room_officer", "senior_officer")),
):
    valid_keys = {m["key"] for m in AVAILABLE_MODELS}
    invalid = [k for k in body.active if k not in valid_keys]
    if invalid:
        raise HTTPException(400, f"Unknown model keys: {invalid}")
    scope = user.get("jurisdiction") or "State HQ"
    now_iso = datetime.now(timezone.utc).isoformat()
    async with async_session_factory() as session:
        # Upsert: try update, if no row affected, insert
        result = await session.execute(
            update(ModelConfig)
            .where(ModelConfig.scope == scope)
            .values(
                active=body.active,
                updated_at=now_iso,
                updated_by=user["email"],
            )
        )
        if result.rowcount == 0:
            session.add(ModelConfig(
                scope=scope,
                active=body.active,
                updated_at=now_iso,
                updated_by=user["email"],
            ))
        await session.commit()
    await write_audit(user, "model_config_update", scope, f"active={','.join(body.active)}")

    # Actually start/stop the analytics stream loops based on what was toggled
    stream_results = await _apply_stream_changes(body.active)
    log.info("model_deploy", extra={"stream_results": stream_results, "active": body.active, "by": user["email"]})

    updated_cfg = await _get_model_config(scope)
    stream_status = await _get_service_stream_status()
    return {
        **updated_cfg,
        "stream_status": stream_status,
        "stream_actions": stream_results,   # what was started/stopped/unchanged
        "deploy_ok": True,
    }


@api.get("/infra/overview")
async def infra_overview(user: dict = Depends(require_roles("system_administrator"))):
    """Rich NOC landing page data."""
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        result = await session.execute(select(Drone).limit(50))
        drones = [_row_to_dict(r) for r in result.scalars().all()]

    # ---- Live stream status ----
    streams = []
    for d in drones:
        st = d["status"]
        fps = random.randint(22, 30) if st == "live" else random.randint(10, 22) if st == "degraded" else 0
        latency = random.randint(45, 120) if st == "live" else random.randint(180, 420) if st == "degraded" else 0
        drop = round(random.uniform(0.0, 0.4) if st == "live" else random.uniform(1.5, 6.2), 2)
        streams.append({
            "drone_id": d["id"],
            "operator": d.get("operator_name", "-"),
            "mission_id": d.get("mission_id", "-"),
            "fps": fps,
            "latency_ms": latency,
            "packet_drop_pct": drop,
            "sync": "synced" if st == "live" and drop < 0.5 else "drift" if st == "degraded" else "lost",
            "bitrate_mbps": round(random.uniform(2.5, 7.8), 2) if st != "offline" else 0,
            "status": st,
        })

    live_ct = sum(1 for s in streams if s["status"] == "live")
    warn_ct = sum(1 for s in streams if s["status"] == "degraded")
    crit_ct = sum(1 for s in streams if s["status"] == "offline")
    avg_lat = round(sum(s["latency_ms"] for s in streams if s["latency_ms"]) / max(1, live_ct + warn_ct))
    avg_fps = round(sum(s["fps"] for s in streams if s["fps"]) / max(1, live_ct + warn_ct))

    # ---- Infra gauges ----
    infra = {
        "cpu_pct": random.randint(48, 78),
        "ram_pct": random.randint(52, 74),
        "gpu_pct": random.randint(58, 88),
        "disk_pct": random.randint(41, 62),
        "network_mbps": random.randint(340, 620),
        "network_cap_mbps": 1000,
        "gpu_mem_used_gb": round(random.uniform(18.5, 26.2), 1),
        "gpu_mem_total_gb": 32.0,
    }

    # ---- Queue monitoring ----
    queues = []
    for i, name in enumerate(["ingest", "detection", "evidence", "audit"]):
        depth = random.randint(20, 240)
        queues.append({
            "name": name,
            "depth": depth,
            "backlog": max(0, depth - 150),
            "throughput_fps": random.randint(60, 220),
            "error_rate_pct": round(random.uniform(0.02, 1.4), 2),
        })

    # ---- Frame ingestion + sparkline ----
    total_frames = random.randint(1_240_000, 1_320_000)
    kept = int(total_frames * random.uniform(0.06, 0.09))
    dropped = int(total_frames * random.uniform(0.001, 0.004))
    spark = [random.randint(180, 260) for _ in range(30)]

    frames = {
        "total_frames_24h": total_frames,
        "kept_frames_24h": kept,
        "dropped_frames_24h": dropped,
        "selection_ratio_pct": round(kept / total_frames * 100, 2),
        "drop_ratio_pct": round(dropped / total_frames * 100, 3),
        "sparkline_fps": spark,
    }

    # ---- Metadata validation ----
    metadata = {
        "gps_valid_pct": round(random.uniform(96.4, 99.8), 2),
        "timestamp_valid_pct": round(random.uniform(97.5, 99.9), 2),
        "sensor_valid_pct": round(random.uniform(93.8, 99.4), 2),
        "records_checked_24h": random.randint(320_000, 480_000),
    }

    # ---- API monitor ----
    endpoints = [
        {"path": "/api/telemetry", "rps": random.randint(180, 340), "p95_ms": random.randint(38, 96),  "success_pct": round(random.uniform(99.4, 99.99), 2)},
        {"path": "/api/frames",    "rps": random.randint(80, 220),  "p95_ms": random.randint(52, 148), "success_pct": round(random.uniform(99.1, 99.95), 2)},
        {"path": "/api/detect",    "rps": random.randint(60, 140),  "p95_ms": random.randint(120, 280),"success_pct": round(random.uniform(98.6, 99.9), 2)},
        {"path": "/api/evidence",  "rps": random.randint(8, 30),    "p95_ms": random.randint(180, 420),"success_pct": round(random.uniform(99.0, 99.99), 2)},
        {"path": "/api/auth",      "rps": random.randint(4, 18),    "p95_ms": random.randint(28, 92),  "success_pct": round(random.uniform(99.8, 99.999), 3)},
    ]
    api_total_rps = sum(e["rps"] for e in endpoints)
    api_avg_success = round(sum(e["success_pct"] for e in endpoints) / len(endpoints), 3)
    api_avg_p95 = round(sum(e["p95_ms"] for e in endpoints) / len(endpoints))

    # ---- CPU/GPU trend (24 points, rolling) ----
    trend = []
    for i in range(24):
        trend.append({
            "t": i,
            "cpu": max(30, min(95, infra["cpu_pct"] + random.randint(-10, 10))),
            "gpu": max(35, min(96, infra["gpu_pct"] + random.randint(-8, 8))),
        })

    return {
        "streams": streams,
        "stream_stats": {
            "active": live_ct, "warning": warn_ct, "critical": crit_ct,
            "authenticated": live_ct + warn_ct,
            "avg_latency_ms": avg_lat, "avg_fps": avg_fps,
        },
        "infra": infra,
        "queues": queues,
        "frames": frames,
        "metadata": metadata,
        "api": {"endpoints": endpoints, "total_rps": api_total_rps, "avg_success_pct": api_avg_success, "avg_p95_ms": api_avg_p95},
        "trend": trend,
        "generated_at": now.isoformat(),
    }


# ---------- Edge Operator status ----------
@api.get("/edge/status")
async def edge_status(user: dict = Depends(require_roles("drone_operator"))):
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        result = await session.execute(
            select(Drone).where(Drone.operator_id == user["id"])
        )
        drone_obj = result.scalar_one_or_none()
        drone = _row_to_dict(drone_obj) if drone_obj else None
    return {
        "drone": drone,
        "buffer_pct": random.randint(55, 92),
        "buffer_seconds_oldest": 60,
        "buffer_seconds_newest": random.randint(0, 3),
        "flagged_last_minute": random.randint(2, 18),
        "selected_last_minute": random.randint(0, 5),
        "clip_status": random.choice(["idle", "uploading", "upload_complete"]),
        "vpn": "connected",
        "ingest_reachable": True,
        "gps_lock": True,
        "battery_pct": random.randint(48, 96),
        "signal_pct": random.randint(65, 98),
        "fps": 28,
        "resolution": "1920x1080",
        "server_time": now.isoformat(),
    }


# ---------- Command dashboard: aggregate KPIs ----------
@api.get("/command/kpis")
async def command_kpis(user: dict = Depends(get_current_user)):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    f = scope_filter_for(user)
    if "__deny__" in f:
        raise HTTPException(403, "Not permitted")

    async with async_session_factory() as session:
        # Active missions count
        missions_stmt = select(func.count()).select_from(Mission).where(Mission.status == "active")
        if user["role"] != "senior_officer":
            missions_stmt = missions_stmt.where(Mission.jurisdiction == user.get("jurisdiction"))
        active_missions = (await session.execute(missions_stmt)).scalar() or 0

        # Alerts count
        alerts_stmt = select(func.count()).select_from(Alert)
        alerts_stmt = _apply_scope_to_query(alerts_stmt, Alert, f) if "__deny__" not in f else alerts_stmt
        alerts_24h = (await session.execute(alerts_stmt)).scalar() or 0

        # Evidence total
        evidence_stmt = select(func.count()).select_from(Evidence)
        evidence_stmt = _apply_scope_to_query(evidence_stmt, Evidence, f) if "__deny__" not in f else evidence_stmt
        evidence_total = (await session.execute(evidence_stmt)).scalar() or 0

        # Exports pending
        exports_stmt = select(func.count()).select_from(Export).where(Export.status == "pending")
        if user["role"] != "senior_officer":
            exports_stmt = exports_stmt.where(Export.jurisdiction == user.get("jurisdiction"))
        exports_pending = (await session.execute(exports_stmt)).scalar() or 0

        # Drones online
        drones_online = (await session.execute(
            select(func.count()).select_from(Drone).where(Drone.status == "live")
        )).scalar() or 0

    return {
        "active_missions": active_missions,
        "alerts_24h": alerts_24h,
        "evidence_total": evidence_total,
        "exports_pending": exports_pending,
        "drones_online": drones_online,
    }


# ---------- Analytics — proxy to analytics microservices ----------
# Gateway is at port 8100; individual services at 8101-8104
# URL from env or default to host.docker.internal for docker compose setup
ANALYTICS_BASE = os.getenv("ANALYTICS_GATEWAY_URL", "http://host.docker.internal:8100")
CROWD_URL   = os.getenv("CROWD_ANALYTICS_URL",   "http://host.docker.internal:8101")
PERSON_URL  = os.getenv("PERSON_ANALYTICS_URL",  "http://host.docker.internal:8102")
VEHICLE_URL = os.getenv("VEHICLE_ANALYTICS_URL", "http://host.docker.internal:8103")
ANPR_URL    = os.getenv("ANPR_ANALYTICS_URL",    "http://host.docker.internal:8104")

# ---------- MinIO Image Proxy ----------
# Serve MinIO images through the backend so MinIO is never exposed to browsers.
# Frontend requests: /api/minio/image?bucket=dvas-plates&key=plates/MSN-.../file.jpg
MINIO_PROXY_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_PROXY_ACCESS   = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_PROXY_SECRET   = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_PROXY_SECURE   = os.getenv("MINIO_SECURE", "false").lower() == "true"

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


@api.get("/minio/image")
async def minio_image_proxy(bucket: str, key: str, _user=Depends(get_current_user)):
    """
    Securely proxy MinIO object downloads through the backend.
    Requires authentication — no direct MinIO exposure to browsers.
    """
    if not bucket or not key:
        raise HTTPException(status_code=400, detail="bucket and key are required")

    # Security: only allow known buckets
    ALLOWED_BUCKETS = {"dvas-plates", "dvas-vehicles", "dvas-evidence", "dvas-faces", "dvas-heatmaps"}
    if bucket not in ALLOWED_BUCKETS:
        raise HTTPException(status_code=403, detail=f"Bucket '{bucket}' not allowed")

    # Sanitize key to prevent path traversal
    if ".." in key or key.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid key")

    client = _get_minio_client()
    if client is None:
        raise HTTPException(status_code=503, detail="MinIO not available")

    try:
        response = client.get_object(bucket, key)
        img_bytes = response.read()
        response.close()
        response.release_conn()

        # Determine content type from key extension
        ct = "image/jpeg"
        if key.endswith(".png"):
            ct = "image/png"
        elif key.endswith(".webp"):
            ct = "image/webp"

        return Response(
            content=img_bytes,
            media_type=ct,
            headers={
                "Cache-Control": "public, max-age=86400",  # cache images for 24h
                "X-Content-Type-Options": "nosniff",
            },
        )
    except Exception as exc:
        logger.debug("MinIO proxy fetch failed (%s/%s): %s", bucket, key, exc)
        raise HTTPException(status_code=404, detail="Image not found")

async def _analytics_get(url: str, timeout: float = 8.0) -> dict:
    """Forward GET to analytics service; return response JSON or error envelope."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=timeout)
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
            r = await client.post(url, json=payload, timeout=timeout)
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
            r = await client.delete(url, timeout=timeout)
            if r.status_code in (200, 204):
                return r.json() if r.content else {"ok": True}
            return {"error": f"upstream {r.status_code}", "detail": r.text[:200]}
    except httpx.ConnectError:
        return {"error": "service_unavailable", "detail": f"Cannot reach {url}"}
    except Exception as exc:
        return {"error": "proxy_error", "detail": str(exc)}


# ── Analytics Health ──────────────────────────────────────────────────────────
@api.get("/analytics/health")
async def analytics_health(user: dict = Depends(get_current_user)):
    """Aggregated health check across all 4 analytics services."""
    return await _analytics_get(f"{ANALYTICS_BASE}/health/all")


@api.get("/analytics/summary")
async def analytics_summary(user: dict = Depends(get_current_user)):
    """Unified stats snapshot from gateway."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{ANALYTICS_BASE}/summary")


@api.get("/analytics/models/status")
async def analytics_models_status(user: dict = Depends(get_current_user)):
    """Real model status from each analytics service health endpoint."""
    import asyncio as _asyncio
    results = await _asyncio.gather(
        _analytics_get(f"{CROWD_URL}/health"),
        _analytics_get(f"{PERSON_URL}/health"),
        _analytics_get(f"{VEHICLE_URL}/health"),
        _analytics_get(f"{ANPR_URL}/health"),
    )
    return {
        "crowd":   results[0],
        "person":  results[1],
        "vehicle": results[2],
        "anpr":    results[3],
    }


# ── SSE Stream proxy ──────────────────────────────────────────────────────────
@api.get("/analytics/events/stream")
async def analytics_events_stream(request: Request, drone_id: str = ""):
    """Proxy the gateway SSE stream to the browser with auth check."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")

    upstream_url = f"{ANALYTICS_BASE}/events/stream"
    if drone_id:
        upstream_url += f"?drone_id={drone_id}"

    async def event_generator():
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream("GET", upstream_url, timeout=None) as resp:
                    async for line in resp.aiter_lines():
                        if await request.is_disconnected():
                            break
                        yield line + "\n"
        except Exception as exc:
            logger.warning("SSE proxy error: %s", exc)
            yield f"data: {{\"error\": \"{exc}\"}}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Crowd Analytics ───────────────────────────────────────────────────────────
@api.get("/analytics/crowd")
async def crowd_analytics(user: dict = Depends(get_current_user)):
    """Crowd density timeline — proxied from crowd-analyzer with fallback."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    result = await _analytics_get(f"{CROWD_URL}/density/timeline")
    if "error" in result:
        # Fallback: synthesized data when service is offline
        base = [120,130,90,60,40,30,45,90,180,260,320,400,380,350,300,340,420,500,460,380,300,240,190,150]
        data = [{"hour": f"{h:02d}:00", "density": base[h], "vehicles": max(0, base[h] // 4)} for h in range(24)]
        return {"series": data, "source": "fallback"}
    return result


@api.get("/analytics/crowd/readings")
async def crowd_readings(user: dict = Depends(get_current_user), drone_id: str = "", limit: int = 50):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{CROWD_URL}/readings/latest?limit={limit}"
    if drone_id:
        url += f"&drone_id={drone_id}"
    return await _analytics_get(url)


@api.get("/analytics/crowd/timeline")
async def crowd_timeline(user: dict = Depends(get_current_user), drone_id: str = "", hours: int = 24):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{CROWD_URL}/density/timeline?hours={hours}"
    if drone_id:
        url += f"&drone_id={drone_id}"
    return await _analytics_get(url)


@api.get("/analytics/crowd/heatmap")
async def crowd_heatmap(user: dict = Depends(get_current_user), drone_id: str = ""):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{CROWD_URL}/density/heatmap/latest"
    if drone_id:
        url += f"?drone_id={drone_id}"
    return await _analytics_get(url)


@api.get("/analytics/crowd/health")
async def crowd_health(user: dict = Depends(get_current_user)):
    return await _analytics_get(f"{CROWD_URL}/health")


# ── Person Analytics ──────────────────────────────────────────────────────────
@api.get("/analytics/person/identities")
async def person_identities(user: dict = Depends(get_current_user), drone_id: str = "", limit: int = 50):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{PERSON_URL}/persons/identities?limit={limit}"
    if drone_id:
        url += f"&drone_id={drone_id}"
    return await _analytics_get(url)


@api.get("/analytics/person/gallery")
async def person_gallery(user: dict = Depends(get_current_user), drone_id: str = "", limit: int = 60):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{PERSON_URL}/faces/gallery?limit={limit}"
    if drone_id:
        url += f"&drone_id={drone_id}"
    return await _analytics_get(url)


@api.get("/analytics/person/count")
async def person_count(user: dict = Depends(get_current_user), drone_id: str = ""):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{PERSON_URL}/persons/count"
    if drone_id:
        url += f"?drone_id={drone_id}"
    return await _analytics_get(url)


@api.get("/analytics/person/tracks")
async def person_tracks(user: dict = Depends(get_current_user), drone_id: str = "", limit: int = 100):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{PERSON_URL}/tracks/active?limit={limit}"
    if drone_id:
        url += f"&drone_id={drone_id}"
    return await _analytics_get(url)


@api.get("/analytics/person/dedup")
async def person_dedup(user: dict = Depends(get_current_user)):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{PERSON_URL}/faces/deduplicate")


@api.get("/analytics/person/detections")
async def person_detections(user: dict = Depends(get_current_user), drone_id: str = "", limit: int = 50):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{PERSON_URL}/detections/recent?limit={limit}"
    if drone_id:
        url += f"&drone_id={drone_id}"
    return await _analytics_get(url)


@api.post("/analytics/person/reid")
async def person_reid_trigger(user: dict = Depends(get_current_user)):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_post(f"{PERSON_URL}/reid/trigger", {})


# ── Analytics Service Health Proxies ─────────────────────────────────────────
@api.get("/analytics/vehicle/health")
async def vehicle_health(user: dict = Depends(get_current_user)):
    """Proxy vehicle-analyzer health including stream_running status."""
    return await _analytics_get(f"{VEHICLE_URL}/health")


@api.get("/analytics/anpr/health")
async def anpr_health(user: dict = Depends(get_current_user)):
    """Proxy anpr-analyzer health including stream_running status."""
    return await _analytics_get(f"{ANPR_URL}/health")


# ── Analytics Stream Start/Stop Proxies ──────────────────────────────────────
@api.get("/analytics/vehicle/stream/start")
async def vehicle_stream_start(user: dict = Depends(get_current_user)):
    """Start the vehicle detection stream loop."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{VEHICLE_URL}/stream/start")


@api.get("/analytics/vehicle/stream/stop")
async def vehicle_stream_stop(user: dict = Depends(get_current_user)):
    """Stop the vehicle detection stream loop."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{VEHICLE_URL}/stream/stop")


@api.get("/analytics/anpr/stream/start")
async def anpr_stream_start(user: dict = Depends(get_current_user)):
    """Start the ANPR detection stream loop."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{ANPR_URL}/stream/start")


@api.get("/analytics/anpr/stream/stop")
async def anpr_stream_stop(user: dict = Depends(get_current_user)):
    """Stop the ANPR detection stream loop."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{ANPR_URL}/stream/stop")


@api.get("/analytics/crowd/stream/start")
async def crowd_stream_start(user: dict = Depends(get_current_user)):
    """Start the crowd analysis stream loop."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{CROWD_URL}/stream/start")


@api.get("/analytics/person/stream/start")
async def person_stream_start(user: dict = Depends(get_current_user)):
    """Start the person detection stream loop."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{PERSON_URL}/stream/start")


@api.get("/analytics/person/health")
async def person_health_proxy(user: dict = Depends(get_current_user)):
    """Proxy person-analyzer health including stream_running status."""
    return await _analytics_get(f"{PERSON_URL}/health")


# ── Vehicle Analytics ─────────────────────────────────────────────────────────

@api.get("/analytics/vehicle/tracks")
async def vehicle_tracks(user: dict = Depends(get_current_user), drone_id: str = "", limit: int = 100):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{VEHICLE_URL}/tracks/active?limit={limit}"
    if drone_id:
        url += f"&drone_id={drone_id}"
    return await _analytics_get(url)


@api.get("/analytics/vehicle/recent")
async def vehicle_recent(user: dict = Depends(get_current_user), drone_id: str = "", limit: int = 50):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{VEHICLE_URL}/vehicles/recent?limit={limit}"
    if drone_id:
        url += f"&drone_id={drone_id}"
    return await _analytics_get(url)


@api.get("/analytics/vehicle/stats")
async def vehicle_stats(user: dict = Depends(get_current_user)):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{VEHICLE_URL}/stats/summary")


@api.get("/analytics/vehicle/stats/flow")
async def vehicle_stats_flow(user: dict = Depends(get_current_user)):
    """24-hour vehicle flow time series broken down by type."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{VEHICLE_URL}/stats/flow/24h")


@api.get("/analytics/vehicle/by-colour")
async def vehicle_by_colour(user: dict = Depends(get_current_user)):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{VEHICLE_URL}/stats/by-colour")


@api.get("/analytics/vehicle/{tracker_id}/plate")
async def vehicle_plate(tracker_id: str, user: dict = Depends(get_current_user), drone_id: str = ""):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{VEHICLE_URL}/vehicles/{tracker_id}/plate"
    if drone_id:
        url += f"?drone_id={drone_id}"
    return await _analytics_get(url)


@api.get("/analytics/vehicle/{tracker_id}/image")
async def vehicle_image_url(tracker_id: str, user: dict = Depends(get_current_user), drone_id: str = ""):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{VEHICLE_URL}/vehicles/{tracker_id}/image"
    if drone_id:
        url += f"?drone_id={drone_id}"
    return await _analytics_get(url)


@api.get("/analytics/vehicle/annotated")
async def vehicle_annotated_frame(user: dict = Depends(get_current_user), drone_id: str = ""):
    """
    Proxy the latest annotated JPEG frame (vehicle bounding boxes + labels)
    from vehicle-analyzer. Used by the dashboard live feed window.
    Returns binary JPEG — NOT JSON.
    """
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    try:
        params = {"drone_id": drone_id} if drone_id else {}
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{VEHICLE_URL}/annotated/latest", params=params)
        if r.status_code == 200:
            return Response(
                content=r.content,
                media_type="image/jpeg",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma":        "no-cache",
                },
            )
        raise HTTPException(r.status_code, "No annotated frame available")
    except httpx.RequestError:
        raise HTTPException(503, "Vehicle analyzer unavailable")


@api.get("/analytics/anpr/annotated")
async def anpr_annotated_frame(user: dict = Depends(get_current_user), drone_id: str = ""):
    """
    Proxy the latest annotated JPEG frame (plate bboxes + OCR text)
    from anpr-analyzer. Used by the ANPR window in the dashboard.
    Returns binary JPEG — NOT JSON.
    """
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    try:
        params = {"drone_id": drone_id} if drone_id else {}
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{ANPR_URL}/annotated/latest", params=params)
        if r.status_code == 200:
            return Response(
                content=r.content,
                media_type="image/jpeg",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma":        "no-cache",
                },
            )
        raise HTTPException(r.status_code, "No annotated frame available")
    except httpx.RequestError:
        raise HTTPException(503, "ANPR analyzer unavailable")


# ── ANPR Analytics ────────────────────────────────────────────────────────────
@api.get("/analytics/plates/recent")
async def plates_recent(
    user: dict = Depends(get_current_user),
    drone_id: str = "",
    mission_id: str = "",
    limit: int = 50,
):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{ANPR_URL}/plates/recent?limit={limit}"
    if mission_id:
        url += f"&mission_id={mission_id}"
    elif drone_id:
        url += f"&drone_id={drone_id}"
    return await _analytics_get(url)


@api.get("/analytics/plates/live")
async def plates_live(
    user: dict = Depends(get_current_user),
    drone_id: str = "",
    mission_id: str = "",
    limit: int = 50,
):
    """In-memory live plate detections from the stream loop — no DB/MinIO required."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{ANPR_URL}/plates/live?limit={limit}"
    if mission_id:
        url += f"&mission_id={mission_id}"
    elif drone_id:
        url += f"&drone_id={drone_id}"
    return await _analytics_get(url)


@api.get("/analytics/plates/{plate_text}")
async def plate_lookup(plate_text: str, user: dict = Depends(get_current_user)):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{ANPR_URL}/plates/{plate_text}")


@api.get("/analytics/plates/{plate_text}/image")
async def plate_image_url(plate_text: str, user: dict = Depends(get_current_user)):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{ANPR_URL}/plates/{plate_text}/image")


@api.get("/analytics/watchlist")
async def watchlist_get(user: dict = Depends(get_current_user)):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{ANPR_URL}/watchlist")


class WatchlistAddIn(BaseModel):
    plate: str
    reason: str = ""
    vehicle_type: str = ""


@api.post("/analytics/watchlist")
async def watchlist_add(
    body: WatchlistAddIn,
    user: dict = Depends(get_current_user),
    request: Request = None,
):
    if user["role"] not in ("control_room_officer", "senior_officer"):
        raise HTTPException(403, "Not permitted")
    result = await _analytics_post(f"{ANPR_URL}/watchlist/add", {
        "plate_text": body.plate,
        "reason":     body.reason,
    })
    await write_audit(user, "watchlist_add", body.plate, body.reason,
                      request.client.host if request and request.client else "-")
    return result


@api.delete("/analytics/watchlist/{plate}")
async def watchlist_remove(
    plate: str,
    user: dict = Depends(get_current_user),
    request: Request = None,
):
    if user["role"] not in ("control_room_officer", "senior_officer"):
        raise HTTPException(403, "Not permitted")
    result = await _analytics_delete(f"{ANPR_URL}/watchlist/{plate}")
    await write_audit(user, "watchlist_remove", plate, "",
                      request.client.host if request and request.client else "-")
    return result


@api.get("/analytics/alerts/theft")
async def theft_alerts(user: dict = Depends(get_current_user), limit: int = 20):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{ANPR_URL}/alerts/theft?limit={limit}")


# ── Mission Vehicle History ──────────────────────────────────────────────────
@api.get("/analytics/mission/{mission_id}/vehicles")
async def mission_vehicles(
    mission_id: str,
    user: dict = Depends(get_current_user),
    plate_search: str = "",
    vehicle_type: str = "",
    sort: str = "newest",
    limit: int = 24,
    offset: int = 0,
):
    """
    Retrieve vehicle/plate detection history for a specific mission.
    Passes mission_id directly to the ANPR service for accurate per-mission filtering.
    """
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")

    # Look up mission from dashboard DB — soft lookup, not required
    mission_name = mission_id
    drone_id = ""
    started_at = ""
    ended_at = ""
    status = "unknown"
    async with AsyncSession(engine) as session:
        mission = await session.get(Mission, mission_id)
        if mission:
            mission_name = mission.name or mission_id
            drone_id = mission.drone_id or ""
            started_at = mission.started_at or ""
            ended_at = mission.ended_at or ""
            status = mission.status or "unknown"

    # Build query string for ANPR service — use mission_id as primary filter
    from urllib.parse import quote
    qs_parts = [
        f"mission_id={quote(mission_id)}",
        f"limit={min(limit, 200)}",
        f"offset={offset}",
    ]
    if plate_search:
        qs_parts.append(f"plate_search={quote(plate_search)}")
    if vehicle_type and vehicle_type != "all":
        qs_parts.append(f"vehicle_type={quote(vehicle_type)}")
    if sort:
        qs_parts.append(f"sort={sort}")

    url = f"{ANPR_URL}/plates/by-mission?{'&'.join(qs_parts)}"
    result = await _analytics_get(url, timeout=15.0)

    # Enrich with mission metadata
    if isinstance(result, dict) and "error" not in result:
        result["mission_id"] = mission_id
        result["mission_name"] = mission_name
        result["drone_id"] = drone_id
        result["status"] = status
        result["time_range"] = {
            "start": started_at,
            "end": ended_at,
        }
        # Compute duration
        if started_at and ended_at:
            try:
                from datetime import datetime as _dt
                s = _dt.fromisoformat(started_at.replace("Z", "+00:00"))
                e = _dt.fromisoformat(ended_at.replace("Z", "+00:00"))
                dur_s = int((e - s).total_seconds())
                h, m = divmod(dur_s // 60, 60)
                result["duration"] = f"{h}h {m}m" if h > 0 else f"{m}m"
            except Exception:
                result["duration"] = None
                
        # The ANPR service returns `plates`, but the frontend expects `vehicles`
        if "plates" in result:
            result["vehicles"] = result.pop("plates")
            
    return result


@api.get("/analytics/mission/{mission_id}/stats")
async def mission_stats(
    mission_id: str,
    user: dict = Depends(get_current_user),
):
    """Quick summary stats for a mission — total vehicles, plates, etc."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")

    async with AsyncSession(engine) as session:
        mission = await session.get(Mission, mission_id)
        if not mission:
            raise HTTPException(404, f"Mission {mission_id} not found")

    drone_id = mission.drone_id or ""

    from urllib.parse import quote
    url = f"{ANPR_URL}/plates/by-mission?mission_id={quote(mission_id)}&limit=1&offset=0"
    result = await _analytics_get(url, timeout=15.0)

    return {
        "mission_id": mission_id,
        "mission_name": mission.name,
        "drone_id": drone_id,
        "total_vehicles": result.get("total", 0) if isinstance(result, dict) else 0,
        "total_plates_read": result.get("total_plates_read", 0) if isinstance(result, dict) else 0,
    }


# ---------- Startup: seed ----------
JURISDICTIONS = ["Thiruvananthapuram", "Kochi", "Kozhikode"]

SEED_USERS = [
    {
        "email": "operator@dvas.kp",
        "name": "Ravi Menon",
        "role": "drone_operator",
        "jurisdiction": "Thiruvananthapuram",
        "mission_scope": ["M-101"],
    },
    {
        "email": "control@dvas.kp",
        "name": "Anjali Nair",
        "role": "control_room_officer",
        "jurisdiction": "Thiruvananthapuram",
        "mission_scope": ["M-101", "M-102"],
    },
    {
        # Migrated from field_supervisor → control_room_officer (see migrate_roles.py)
        "email": "field@dvas.kp",
        "name": "Suresh Pillai",
        "role": "control_room_officer",
        "jurisdiction": "Thiruvananthapuram",
        "mission_scope": ["M-101", "M-102"],
    },
    {
        # Migrated from supervisory_officer → senior_officer (see migrate_roles.py)
        "email": "supervisor@dvas.kp",
        "name": "Deepa Krishnan",
        "role": "senior_officer",
        "jurisdiction": "Kochi",
        "mission_scope": [],
    },
    {
        "email": "senior@dvas.kp",
        "name": "IG. Rajan Varma",
        "role": "senior_officer",
        "jurisdiction": "State HQ",
        "mission_scope": [],
    },
    {
        "email": "admin@dvas.kp",
        "name": "Sys Admin",
        "role": "system_administrator",
        "jurisdiction": "HQ Restricted",
        "mission_scope": [],
    },
]


async def seed_data():
    # ── Migrate existing missions table (rename id → mission_id, add new cols) ──
    async with engine.begin() as conn:
        # Check if old 'id' column exists in missions table
        result = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'missions' AND column_name = 'id'"
        ))
        has_old_id = result.fetchone() is not None

        if has_old_id:
            logger.info("Migrating missions table: renaming 'id' → 'mission_id', adding new columns...")
            # Drop and recreate missions table (safe — seed data will re-populate)
            await conn.execute(text("DROP TABLE IF EXISTS missions CASCADE"))
            logger.info("Dropped old missions table; it will be recreated with new schema.")

    # Create all tables (including the new missions schema)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_factory() as session:
        # Check for legacy audit logs without hash chain → clear and re-seed
        legacy_count = (await session.execute(
            select(func.count()).select_from(AuditLog).where(AuditLog.hash == None)  # noqa: E711
        )).scalar() or 0
        if legacy_count > 0:
            await session.execute(delete(AuditLog))
            await session.commit()
            logger.info(f"Cleared {legacy_count} legacy audit logs (missing hash chain).")

        # users are now seeded in Keycloak via setup script
        user_ids_by_email = {u["email"]: "seed-id" for u in SEED_USERS}

        # If missions already seeded, ensure audit chain exists then return
        mission_count = (await session.execute(
            select(func.count()).select_from(Mission)
        )).scalar() or 0
        if mission_count > 0:
            audit_count = (await session.execute(
                select(func.count()).select_from(AuditLog)
            )).scalar() or 0
            if audit_count == 0:
                await _seed_audit_chain(session, user_ids_by_email)
            return  # already seeded

        # Clean slate for dependent seed tables to prevent IntegrityError on re-seed
        await session.execute(delete(Drone))
        await session.execute(delete(Alert))
        await session.execute(delete(Evidence))
        await session.execute(delete(Export))
        await session.commit()

        now = datetime.now(timezone.utc)
        op_id = user_ids_by_email["operator@dvas.kp"]

        missions = [
            {
                "mission_id": "M-101",
                "name": "Attukal Pongala Crowd Watch",
                "status": "active",
                "drone_id": "DRN-001",
                "operator_name": "Ravi Menon",
                "jurisdiction": "Thiruvananthapuram",
                "started_at": (now - timedelta(hours=3)).isoformat(),
                "operator_ids": [op_id],
                "supervisor": "Suresh Pillai",
                "zone": "Attukal Temple",
                "objective": "Crowd density & choke-point monitoring",
            },
            {
                "mission_id": "M-102",
                "name": "Kochi Marine Drive Traffic",
                "status": "active",
                "drone_id": "DRN-002",
                "operator_name": "K. Suresh",
                "jurisdiction": "Thiruvananthapuram",
                "started_at": (now - timedelta(hours=1)).isoformat(),
                "operator_ids": [],
                "supervisor": "Suresh Pillai",
                "zone": "Marine Drive",
                "objective": "Vehicle flow & anomaly detection",
            },
            {
                "mission_id": "M-103",
                "name": "Kozhikode Beach Perimeter",
                "status": "closed",
                "drone_id": "DRN-003",
                "operator_name": "R. Menon",
                "jurisdiction": "Kozhikode",
                "started_at": (now - timedelta(days=2)).isoformat(),
                "operator_ids": [],
                "supervisor": "R. Menon",
                "zone": "Kozhikode Beach",
                "objective": "Perimeter intrusion detection",
            },
        ]
        for m in missions:
            session.add(Mission(**m))

        drones = [
            {
                "id": "DRN-001",
                "operator_id": op_id,
                "operator_name": "Ravi Menon",
                "mission_id": "M-101",
                "jurisdiction": "Thiruvananthapuram",
                "status": "live",
                "lat": 8.5241,
                "lng": 76.9366,
                "altitude_m": 90,
                "battery_pct": 78,
                "signal_pct": 92,
                "last_analytics": {"crowd_density": 340, "vehicles": 12},
            },
            {
                "id": "DRN-002",
                "operator_id": "op-2",
                "operator_name": "K. Suresh",
                "mission_id": "M-102",
                "jurisdiction": "Thiruvananthapuram",
                "status": "live",
                "lat": 9.9673,
                "lng": 76.2810,
                "altitude_m": 120,
                "battery_pct": 64,
                "signal_pct": 88,
                "last_analytics": {"crowd_density": 90, "vehicles": 210},
            },
            {
                "id": "DRN-003",
                "operator_id": "op-3",
                "operator_name": "A. Thomas",
                "mission_id": "M-101",
                "jurisdiction": "Thiruvananthapuram",
                "status": "degraded",
                "lat": 8.5300,
                "lng": 76.9500,
                "altitude_m": 60,
                "battery_pct": 32,
                "signal_pct": 55,
                "last_analytics": {"crowd_density": 260, "vehicles": 4},
            },
            {
                "id": "DRN-004",
                "operator_id": op_id,
                "operator_name": "P. Kurian",
                "mission_id": "M-101",
                "jurisdiction": "Thiruvananthapuram",
                "status": "live",
                "lat": 8.5350,
                "lng": 76.8861,
                "altitude_m": 85,
                "battery_pct": 72,
                "signal_pct": 89,
                "last_analytics": {"crowd_density": 180, "vehicles": 34},
            },
        ]
        for d in drones:
            session.add(Drone(**d))

        alert_types = [
            "crowd_surge",
            "vehicle_anomaly",
            "perimeter_intrusion",
            "unattended_object",
            "wrong_way",
        ]
        zones = [
            "Attukal Temple",
            "Marine Drive",
            "Padmanabhaswamy",
            "Statue Jn",
            "East Fort",
        ]
        for i in range(28):
            drone = random.choice(drones[:3])
            session.add(Alert(
                id=f"A-{1000 + i}",
                mission_id=drone["mission_id"],
                jurisdiction=drone["jurisdiction"],
                drone_id=drone["id"],
                type=random.choice(alert_types),
                confidence=round(random.uniform(0.72, 0.98), 2),
                zone=random.choice(zones),
                ts=(now - timedelta(minutes=random.randint(1, 720))).isoformat(),
                acknowledged_by=None,
                acknowledged_at=None,
            ))

        sample_videos = [
            "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4",
            "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ElephantsDream.mp4",
            "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4",
        ]
        thumbs = [
            "https://images.unsplash.com/photo-1612043743114-d19a560b70eb?w=400",
            "https://images.unsplash.com/photo-1580509742642-84c9d4bd97a4?w=400",
            "https://images.unsplash.com/photo-1541704328070-20b8a213b12d?w=400",
        ]
        evidence_items = []
        for i in range(14):
            drone = random.choice(drones[:3])
            ev_dict = {
                "id": f"E-{2000 + i}",
                "label": f"Confirmed {random.choice(alert_types).replace('_', ' ')} — {drone['id']}",
                "mission_id": drone["mission_id"],
                "jurisdiction": drone["jurisdiction"],
                "drone_id": drone["id"],
                "event_type": random.choice(alert_types),
                "confidence": round(random.uniform(0.80, 0.99), 2),
                "duration_s": random.randint(12, 46),
                "video_url": random.choice(sample_videos),
                "thumbnail": random.choice(thumbs),
                "ts": (now - timedelta(hours=random.randint(0, 96))).isoformat(),
                "legal_hold": random.random() < 0.15,
                "retention_until": (
                    now + timedelta(days=random.randint(30, 180))
                ).isoformat(),
            }
            evidence_items.append(ev_dict)
            session.add(Evidence(**ev_dict))

        for i in range(4):
            ev = random.choice(evidence_items)
            session.add(Export(
                id=f"X-{3000 + i}",
                evidence_id=ev["id"],
                evidence_label=ev["label"],
                jurisdiction=ev["jurisdiction"],
                requested_by="field@dvas.kp",
                requested_at=(
                    now - timedelta(hours=random.randint(1, 30))
                ).isoformat(),
                justification=random.choice(
                    [
                        "Required for FIR case file — Cr. No. 145/2025",
                        "Court submission — Section 65B certified copy",
                        "Internal review — response time audit",
                        "Retention extension request",
                    ]
                ),
                status=random.choice(["pending", "pending", "approved", "rejected"]),
                decision_by=None,
                decision_at=None,
                decision_comment=None,
            ))

        await session.commit()
        await _seed_audit_chain(session, user_ids_by_email, now=now)
    logger.info("Seed complete.")


async def _seed_audit_chain(session: AsyncSession, user_ids_by_email: dict, now: datetime = None):
    if now is None:
        now = datetime.now(timezone.utc)
    logs = []
    prev_hash = "GENESIS"
    for i in range(25):
        actor = random.choice(SEED_USERS)
        act = random.choice(
            [
                "login",
                "view_evidence",
                "alert_ack",
                "export_request",
                "policy_update",
                "user_create",
                "token_revoke",
            ]
        )
        e = {
            "id": str(uuid.uuid4()),
            "ts": (now - timedelta(hours=random.randint(0, 96))).isoformat(),
            "actor_id": user_ids_by_email.get(actor["email"], "seed"),
            "actor_email": actor["email"],
            "actor_role": actor["role"],
            "action": act,
            "target": random.choice(["-", "E-2001", "A-1005", "M-101", "user:new"]),
            "details": "seeded",
            "ip": f"10.0.{random.randint(0, 5)}.{random.randint(2, 240)}",
            "prev_hash": prev_hash,
        }
        e["hash"] = _audit_hash(e)
        prev_hash = e["hash"]
        logs.append(e)
    # Insert in chronological order so the chain reads correctly
    logs.sort(key=lambda x: x["ts"])
    # Re-chain after sort (since sorting changes prev_hash relationships)
    prev_hash = "GENESIS"
    for e in logs:
        e["prev_hash"] = prev_hash
        e["hash"] = _audit_hash(e)
        prev_hash = e["hash"]
    for log_entry in logs:
        session.add(AuditLog(**log_entry))
    await session.commit()


@app.on_event("startup")
async def _startup():
    await seed_data()


# ---------- Root ----------
@api.get("/")
async def root():
    return {"service": "DVAS", "status": "ok"}


# ---------- Section 65B Evidence Bundle ----------
def _make_65b_pdf(evidence: dict, export: dict, decision_by: str) -> bytes:
    def _s(x):
        return str(x).encode("latin-1", "replace").decode("latin-1")

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, "SECTION 65B(4) CERTIFICATE", ln=1, align="C")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, "Indian Evidence Act, 1872 - Electronic Records", ln=1, align="C")
    pdf.ln(4)
    pdf.set_draw_color(120, 120, 120)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Issuing Authority", ln=1)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(
        0,
        5,
        "Kerala Police - Drone Video Analytics System (DVAS)\nCentral Evidence Vault, Police Headquarters, Thiruvananthapuram",
    )
    pdf.ln(2)

    def kv(k, v):
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(55, 6, _s(f"{k}:"), ln=0)
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(135, 6, _s(v))

    kv("Evidence ID", evidence["id"])
    kv("Label", evidence["label"])
    kv("Event Type", evidence["event_type"])
    kv("Confidence", f"{evidence['confidence']}")
    kv("Drone / Mission", f"{evidence['drone_id']} / {evidence['mission_id']}")
    kv("Jurisdiction", evidence["jurisdiction"])
    kv("Captured At", evidence["ts"])
    kv("Duration (s)", evidence["duration_s"])
    kv("Retention Until", evidence.get("retention_until", "-"))
    pdf.ln(2)
    kv("Export Request ID", export["id"])
    kv("Requested By", export["requested_by"])
    kv("Justification", export["justification"])
    kv("Approved By", decision_by)
    kv("Approved At", export.get("decision_at", "-"))
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Statement of Compliance", ln=1)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(
        0,
        5,
        "I hereby certify that (a) the electronic record referred to above was produced by a computer "
        "system used regularly to store and process information for the purposes of activities lawfully "
        "carried on by Kerala Police; (b) during the material period of production, the computer was "
        "operating properly; and (c) the information contained in the electronic record reproduces "
        "information fed into the computer in the ordinary course of the said activities.\n\n"
        "Chain of custody is maintained by the DVAS audit log (tamper-evident, hash-chained). "
        "This bundle contains: (1) the confirmed-event video clip, (2) event metadata JSON, "
        "(3) this Section 65B(4) certificate.",
    )
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, f"Bundle generated: {datetime.now(timezone.utc).isoformat()}", ln=1)
    pdf.ln(8)
    pdf.cell(90, 6, "_______________________", ln=0)
    pdf.cell(0, 6, "_______________________", ln=1)
    pdf.cell(90, 5, "Signature (Supervisory Officer)", ln=0)
    pdf.cell(0, 5, "Official Seal", ln=1)
    return bytes(pdf.output(dest="S"))


@api.get("/exports/{export_id}/bundle")
async def download_65b_bundle(export_id: str, request: Request, token: str = ""):
    # Support Authorization header, cookie, OR query token for direct <a> downloads
    if token:
        try:
            options = {"verify_aud": False}
            payload = jwt.decode(token, get_jwks(), algorithms=["RS256"], options=options)
            user = {
                "id": payload.get("sub"),
                "email": payload.get("email", ""),
                "role": "control_room_officer", # Assuming higher role for exports
            }
        except jwt.JWTError:
            raise HTTPException(401, "Invalid token")
    else:
        user = await get_current_user(request)

    async with async_session_factory() as session:
        result = await session.execute(
            select(Export).where(Export.id == export_id)
        )
        exp_obj = result.scalar_one_or_none()
    if not exp_obj:
        raise HTTPException(404, "Export not found")
    exp = _row_to_dict(exp_obj)
    if exp["status"] != "approved":
        raise HTTPException(400, "Only approved exports can be bundled")
    # Scope: CRO (former field_supervisor) who raised the request, Senior Officer (full authority)
    role = user["role"]
    if role == "control_room_officer" and exp["requested_by"] != user["email"]:
        raise HTTPException(403, "Not your request")
    if role not in ("control_room_officer", "senior_officer"):
        raise HTTPException(403, "Not permitted")

    async with async_session_factory() as session:
        result = await session.execute(
            select(Evidence).where(Evidence.id == exp["evidence_id"])
        )
        ev_obj = result.scalar_one_or_none()
    if not ev_obj:
        raise HTTPException(404, "Evidence missing")
    ev = _row_to_dict(ev_obj)

    pdf_bytes = _make_65b_pdf(ev, exp, exp.get("decision_by", "-"))
    metadata = {
        "evidence": ev,
        "export": exp,
        "bundle_generated_at": datetime.now(timezone.utc).isoformat(),
        "downloaded_by": user["email"],
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{exp['id']}_certificate_65B.pdf", pdf_bytes)
        z.writestr("metadata.json", json.dumps(metadata, indent=2, default=str))
        z.writestr(
            "video_url.txt",
            f"Original clip URL (secure, time-limited):\n{ev['video_url']}\n\n"
            "Actual video file bytes are streamed from the DVAS evidence vault under "
            "separate signed session and are not embedded in this bundle for size reasons.",
        )
        z.writestr(
            "README.txt",
            f"DVAS Section 65B Evidence Bundle\nExport {exp['id']}\nEvidence {ev['id']}\n"
            f"Approved by {exp.get('decision_by', '-')} on {exp.get('decision_at', '-')}\n",
        )
    buf.seek(0)
    await write_audit(user, "bundle_download", exp["id"], f"65B bundle for {ev['id']}")
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{exp["id"]}_65B_bundle.zip"'
        },
    )


# ---------- WebSocket: live telemetry & alerts push ----------
class WSHub:
    def __init__(self):
        self.clients: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.clients:
            self.clients.remove(ws)

    async def broadcast(self, msg: dict):
        dead = []
        for c in self.clients:
            try:
                await c.send_json(msg)
            except Exception:
                dead.append(c)
        for d in dead:
            self.disconnect(d)


hub = WSHub()


def _authz_ws(token: str) -> Optional[dict]:
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
        return {"role": user_role}
    except Exception as e:
        logger.error(f"_authz_ws: Token error: {e}")
        return None


@app.websocket("/api/ws")
async def ws_endpoint(websocket: WebSocket, token: str = ""):
    raw_token = token or websocket.cookies.get("dvas_access_token", "")
    payload = _authz_ws(raw_token)
    if not payload:
        await websocket.close(code=4401)
        return
    await hub.connect(websocket)
    try:
        await websocket.send_json({"type": "welcome", "role": payload.get("role")})
        while True:
            # keep-alive; clients can send pings
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json(
                    {"type": "pong", "ts": datetime.now(timezone.utc).isoformat()}
                )
    except WebSocketDisconnect:
        hub.disconnect(websocket)
    except Exception:
        hub.disconnect(websocket)


async def _telemetry_pump():
    """Background broadcaster: pushes drone telemetry + occasional new alerts to all clients.

    Hybrid approach:
    1. Fetches real telemetry from the ingest server (/ingest/telemetry).
    2. Upserts any real drones found there into the DB with live GPS/battery data.
    3. Falls back to small simulated drift for DB drones not seen in the ingest server.
    """
    import httpx as _httpx
    INGEST_URL = os.environ.get("INGEST_SERVER_URL", "http://10.51.27.250:8000")

    alert_types = [
        "crowd_surge",
        "vehicle_anomaly",
        "perimeter_intrusion",
        "unattended_object",
        "wrong_way",
    ]
    zones = ["Attukal Temple", "Marine Drive", "East Fort", "Statue Jn"]

    while True:
        await asyncio.sleep(3)
        try:
            # ── 1. Fetch real telemetry from the ingest server ─────────────────
            real_drones: dict[str, dict] = {}  # drone_id → telemetry dict
            try:
                async with _httpx.AsyncClient(timeout=2.0) as client:
                    resp = await client.get(f"{INGEST_URL}/ingest/telemetry")
                    if resp.status_code == 200:
                        data = resp.json()
                        for d in data.get("drones", []):
                            real_drones[d["drone_id"]] = d
            except Exception as te:
                logger.debug("Could not fetch ingest telemetry: %s", te)

            # ── 2. Upsert real drones into the DB ──────────────────────────────
            async with async_session_factory() as session:
                for drone_id, telem in real_drones.items():
                    lat = telem.get("latitude") or 8.5241
                    lng = telem.get("longitude") or 76.9366
                    battery = telem.get("battery_pct") or 85.0
                    altitude = telem.get("altitude_m") or 120.0
                    speed_mps = telem.get("speed_mps") or 0.0
                    # If mission_id is absent, the drone is not actively on a mission
                    # and should be considered offline for the dashboard.
                    mission_id = telem.get("mission_id")
                    mission_name = telem.get("mission_name")
                    operator_name = telem.get("operator")
                    jurisdiction = telem.get("location")
                    drone_status = "live" if mission_id else "offline"

                    # Check if drone already exists in DB
                    result = await session.execute(
                        select(Drone).where(Drone.id == drone_id)
                    )
                    existing = result.scalar_one_or_none()
                    if existing:
                        await session.execute(
                            update(Drone)
                            .where(Drone.id == drone_id)
                            .values(
                                lat=round(lat, 6),
                                lng=round(lng, 6),
                                battery_pct=round(battery, 1),
                                altitude_m=round(altitude, 1),
                                status=drone_status,
                                mission_id=mission_id,
                                mission_name=mission_name,
                                operator_name=operator_name,
                                jurisdiction=jurisdiction,
                            )
                        )
                    else:
                        # Create drone entry so it shows on the map
                        session.add(Drone(
                            id=drone_id,
                            operator_id=None,
                            operator_name=operator_name,
                            mission_id=mission_id,
                            mission_name=mission_name,
                            jurisdiction=jurisdiction,
                            status=drone_status,
                            lat=round(lat, 6),
                            lng=round(lng, 6),
                            altitude_m=round(altitude, 1),
                            battery_pct=round(battery, 1),
                            speed_mps=round(speed_mps, 2),
                            signal_pct=90.0,
                            last_analytics=None,
                        ))
                await session.commit()

            # ── 3. Simulate drift for DB-only drones + build broadcast ─────────
            async with async_session_factory() as session:
                result = await session.execute(
                    select(Drone).where(Drone.status.in_(["live", "degraded"]))
                )
                drone_objs = result.scalars().all()
                updates = []
                for d in drone_objs:
                    if d.id in real_drones:
                        # Use real values — already updated above
                        telem = real_drones[d.id]
                        updates.append({
                            "id": d.id,
                            "lat": round(telem.get("latitude") or d.lat, 6),
                            "lng": round(telem.get("longitude") or d.lng, 6),
                            "battery_pct": round(telem.get("battery_pct") or d.battery_pct, 1),
                            "altitude_m": round(telem.get("altitude_m") or d.altitude_m or 0, 1),
                            "speed_mps": round(telem.get("speed_mps") or 0, 2),
                            "status": "live",
                            "mission_id": telem.get("mission_id") or d.mission_id,
                            "mission_name": telem.get("mission_name") or d.mission_name,
                            "operator_name": telem.get("operator") or d.operator_name,
                            "jurisdiction": telem.get("location") or d.jurisdiction,
                            "is_real": True,
                        })
                    else:
                        # Simulate small drift for demo drones not in ingest
                        new_lat = round(d.lat + random.uniform(-0.0015, 0.0015), 6)
                        new_lng = round(d.lng + random.uniform(-0.0015, 0.0015), 6)
                        new_battery = max(0, d.battery_pct - random.choice([0, 0, 0, 1]))
                        await session.execute(
                            update(Drone)
                            .where(Drone.id == d.id)
                            .values(lat=new_lat, lng=new_lng, battery_pct=new_battery)
                        )
                        updates.append({
                            "id": d.id,
                            "lat": new_lat,
                            "lng": new_lng,
                            "battery_pct": new_battery,
                            "altitude_m": d.altitude_m or 0,
                            "speed_mps": 0,
                            "status": d.status,
                            "mission_id": d.mission_id,
                            "jurisdiction": d.jurisdiction,
                            "is_real": False,
                        })
                await session.commit()

            await hub.broadcast(
                {
                    "type": "telemetry",
                    "drones": updates,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            )
            # occasionally inject a new alert
            if random.random() < 0.28 and drone_objs:
                d = random.choice(drone_objs)
                a = {
                    "id": f"A-{uuid.uuid4().hex[:8].upper()}",
                    "mission_id": d.mission_id,
                    "jurisdiction": d.jurisdiction,
                    "drone_id": d.id,
                    "type": random.choice(alert_types),
                    "confidence": round(random.uniform(0.72, 0.98), 2),
                    "zone": random.choice(zones),
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "acknowledged_by": None,
                    "acknowledged_at": None,
                }
                try:
                    async with async_session_factory() as session:
                        session.add(Alert(**a))
                        await session.commit()
                    await hub.broadcast({"type": "alert", "alert": a})
                except Exception as e:
                    logger.debug(f"telemetry pump alert generation error: {e}")
        except Exception as e:
            logger.warning(f"telemetry pump: {e}")



@app.on_event("startup")
async def _start_pump():
    asyncio.create_task(_telemetry_pump())


app.include_router(api)
app.include_router(ingest_api)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
