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
import jwt
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Literal
import asyncio
import io
import json
import zipfile
from fastapi import (
    FastAPI,
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import StreamingResponse
from starlette.middleware.cors import CORSMiddleware
from sqlalchemy import select, update, delete, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, EmailStr
from fpdf import FPDF

from database import engine, async_session_factory, get_db
from models import Base, User, AuditLog, Mission, Drone, Alert, Evidence, Export, ModelConfig
from ingest_proxy import ingest_api

# ---------- Setup ----------
JWT_ALGORITHM = "HS256"
JWT_SECRET = os.environ["JWT_SECRET"]

app = FastAPI(title="DVAS API")
api = APIRouter(prefix="/api")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dvas")

# ---------- Auth helpers ----------
# RBAC: 4-role model (field_supervisor → control_room_officer,
#        supervisory_officer → senior_officer — see migrate_roles.py)
Role = Literal[
    "drone_operator",
    "control_room_officer",
    "senior_officer",
    "system_administrator",
]


def hash_password(p: str) -> str:
    return bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()


def verify_password(p: str, h: str) -> bool:
    return bcrypt.checkpw(p.encode(), h.encode())


def create_access_token(user_id: str, email: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=8),
        "type": "access",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        async with async_session_factory() as session:
            result = await session.execute(
                select(User).where(User.id == payload["sub"])
            )
            user_obj = result.scalar_one_or_none()
            if not user_obj:
                raise HTTPException(status_code=401, detail="User not found")
            return _user_to_dict(user_obj, exclude_password=True)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def _user_to_dict(u: User, exclude_password: bool = False) -> dict:
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


class ExportRequestIn(BaseModel):
    evidence_id: str
    justification: str


class ExportDecisionIn(BaseModel):
    decision: Literal["approved", "rejected"]
    comment: str = ""


# ---------- Auth endpoints ----------
@api.post("/auth/login")
async def login(body: LoginIn, response: Response, request: Request):
    email = body.email.lower()
    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.email == email))
        user_obj = result.scalar_one_or_none()
    if not user_obj or not verify_password(body.password, user_obj.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    user = _user_to_dict(user_obj)
    token = create_access_token(user["id"], user["email"], user["role"])
    response.set_cookie(
        "access_token", token, httponly=True, samesite="lax", max_age=28800, path="/"
    )
    await write_audit(
        user,
        "login",
        user["email"],
        "login success",
        request.client.host if request.client else "-",
    )
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "role": user["role"],
        "jurisdiction": user.get("jurisdiction"),
        "mission_scope": user.get("mission_scope", []),
        "access_token": token,
    }


@api.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return user


@api.post("/auth/logout")
async def logout(
    response: Response, user: dict = Depends(get_current_user), request: Request = None
):
    response.delete_cookie("access_token", path="/")
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
        # mission-scoped access (covers former field_supervisor capability)
        return {"mission_id": {"$in": user.get("mission_scope", [])}}
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
    if user["role"] in ("drone_operator", "system_administrator"):
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
    if user["role"] in ("drone_operator", "system_administrator"):
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
    async with async_session_factory() as session:
        if user["role"] == "drone_operator":
            # operator_ids is JSONB array — use contains operator
            stmt = select(Mission).where(
                Mission.operator_ids.contains([user["id"]])
            ).limit(50)
            result = await session.execute(stmt)
            return [_row_to_dict(r) for r in result.scalars().all()]

        if user["role"] == "system_administrator":
            raise HTTPException(403, "Not permitted")

        f = scope_filter_for(user)
        if "__deny__" in f:
            raise HTTPException(403, "Not permitted")

        stmt = select(Mission)
        if "jurisdiction" in f:
            stmt = stmt.where(Mission.jurisdiction == f["jurisdiction"])
        if "mission_id" in f:
            in_list = f["mission_id"].get("$in", [])
            if in_list:
                stmt = stmt.where(Mission.id.in_(in_list))
        stmt = stmt.order_by(Mission.started_at.desc()).limit(50)
        result = await session.execute(stmt)
        return [_row_to_dict(r) for r in result.scalars().all()]


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
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            async with async_session_factory() as session:
                result = await session.execute(
                    select(User).where(User.id == payload["sub"])
                )
                user_obj = result.scalar_one_or_none()
                if not user_obj:
                    raise HTTPException(401, "User not found")
                user = _user_to_dict(user_obj, exclude_password=True)
        except jwt.PyJWTError:
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
                select(Mission).where(Mission.id.in_(user["mission_scope"]))
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


# ---------- Users (admin) ----------
@api.get("/users")
async def list_users(user: dict = Depends(require_roles("system_administrator"))):
    async with async_session_factory() as session:
        result = await session.execute(select(User))
        return [_user_to_dict(r, exclude_password=True) for r in result.scalars().all()]


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
    body: UserCreateIn, user: dict = Depends(require_roles("system_administrator"))
):
    email = body.email.lower()
    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.email == email))
        if result.scalar_one_or_none():
            raise HTTPException(400, "Email already exists")
        doc = {
            "id": str(uuid.uuid4()),
            "email": email,
            "name": body.name,
            "role": body.role,
            "jurisdiction": body.jurisdiction,
            "mission_scope": body.mission_scope,
            "password_hash": hash_password(body.password),
            "suspended": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        session.add(User(**doc))
        await session.commit()
    await write_audit(user, "user_create", email, f"role={body.role}")
    doc.pop("password_hash", None)
    return doc


@api.patch("/users/{user_id}")
async def patch_user(
    user_id: str,
    body: UserPatchIn,
    user: dict = Depends(require_roles("system_administrator")),
):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No updates")
    async with async_session_factory() as session:
        result = await session.execute(
            update(User).where(User.id == user_id).values(**updates)
        )
        if result.rowcount == 0:
            raise HTTPException(404, "User not found")
        await session.commit()
    await write_audit(user, "user_update", user_id, str(updates))
    return {"ok": True}


@api.delete("/users/{user_id}")
async def delete_user(
    user_id: str, user: dict = Depends(require_roles("system_administrator"))
):
    async with async_session_factory() as session:
        result = await session.execute(
            update(User).where(User.id == user_id).values(suspended=True)
        )
        if result.rowcount == 0:
            raise HTTPException(404, "User not found")
        await session.commit()
    await write_audit(user, "user_suspend", user_id, "suspended")
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
AVAILABLE_MODELS = [
    {"key": "person",       "label": "Person Detection",             "gpu_pct": 18, "throughput_fps": 90, "default": True},
    {"key": "face",         "label": "Face Detection",               "gpu_pct": 14, "throughput_fps": 72, "default": False},
    {"key": "vehicle",      "label": "Vehicle Detection & Tracking", "gpu_pct": 22, "throughput_fps": 68, "default": True},
    {"key": "crowd",        "label": "Crowd Count & Heatmap",        "gpu_pct": 26, "throughput_fps": 45, "default": True},
    {"key": "plate",        "label": "Number-Plate (ANPR)",          "gpu_pct": 20, "throughput_fps": 40, "default": False},
]


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
    return {
        "available": AVAILABLE_MODELS,
        "active": cfg["active"],
        "scope": scope,
        "gpu_total_pct": gpu_total,
        "updated_at": cfg.get("updated_at"),
        "updated_by": cfg.get("updated_by"),
        # CRO now has full write access (inherits former field_supervisor model-config capability)
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
    return await _get_model_config(scope)


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
    if user["role"] in ("drone_operator", "system_administrator"):
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


# ---------- Analytics ----------
@api.get("/analytics/crowd")
async def crowd_analytics(user: dict = Depends(get_current_user)):
    if user["role"] in ("drone_operator", "system_administrator"):
        raise HTTPException(403, "Not permitted")
    # 24 buckets — synthesized crowd density trend
    base = [
        120,
        130,
        90,
        60,
        40,
        30,
        45,
        90,
        180,
        260,
        320,
        400,
        380,
        350,
        300,
        340,
        420,
        500,
        460,
        380,
        300,
        240,
        190,
        150,
    ]
    data = [
        {
            "hour": f"{h:02d}:00",
            "density": base[h] + random.randint(-15, 15),
            "vehicles": max(0, base[h] // 4 + random.randint(-8, 8)),
        }
        for h in range(24)
    ]
    return {"series": data}


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
    # Create all tables
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

        # users
        user_ids_by_email = {}
        for u in SEED_USERS:
            result = await session.execute(
                select(User).where(User.email == u["email"])
            )
            existing = result.scalar_one_or_none()
            if not existing:
                uid = str(uuid.uuid4())
                session.add(User(
                    **u,
                    id=uid,
                    password_hash=hash_password("dvas1234"),
                    created_at=datetime.now(timezone.utc).isoformat(),
                ))
                user_ids_by_email[u["email"]] = uid
            else:
                user_ids_by_email[u["email"]] = existing.id
        await session.commit()

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

        now = datetime.now(timezone.utc)
        op_id = user_ids_by_email["operator@dvas.kp"]

        missions = [
            {
                "id": "M-101",
                "name": "Attukal Pongala Crowd Watch",
                "status": "active",
                "jurisdiction": "Thiruvananthapuram",
                "started_at": (now - timedelta(hours=3)).isoformat(),
                "operator_ids": [op_id],
                "supervisor": "Suresh Pillai",
                "zone": "Attukal Temple",
                "objective": "Crowd density & choke-point monitoring",
            },
            {
                "id": "M-102",
                "name": "Kochi Marine Drive Traffic",
                "status": "active",
                "jurisdiction": "Thiruvananthapuram",
                "started_at": (now - timedelta(hours=1)).isoformat(),
                "operator_ids": [],
                "supervisor": "Suresh Pillai",
                "zone": "Marine Drive",
                "objective": "Vehicle flow & anomaly detection",
            },
            {
                "id": "M-103",
                "name": "Kozhikode Beach Perimeter",
                "status": "closed",
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
                "id": "DR-01",
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
                "id": "DR-02",
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
                "id": "DR-03",
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
                "id": "DR-04",
                "operator_id": "op-4",
                "operator_name": "P. Kurian",
                "mission_id": "M-103",
                "jurisdiction": "Kozhikode",
                "status": "offline",
                "lat": 11.2588,
                "lng": 75.7804,
                "altitude_m": 0,
                "battery_pct": 0,
                "signal_pct": 0,
                "last_analytics": {},
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
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            async with async_session_factory() as session:
                result = await session.execute(
                    select(User).where(User.id == payload["sub"])
                )
                user_obj = result.scalar_one_or_none()
                if not user_obj:
                    raise HTTPException(401, "User not found")
                user = _user_to_dict(user_obj, exclude_password=True)
        except jwt.PyJWTError:
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
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        return None


@app.websocket("/api/ws")
async def ws_endpoint(websocket: WebSocket, token: str = ""):
    payload = _authz_ws(token)
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
    """Background broadcaster: pushes drone telemetry + occasional new alerts to all clients."""
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
            async with async_session_factory() as session:
                result = await session.execute(
                    select(Drone).where(Drone.status.in_(["live", "degraded"]))
                )
                drone_objs = result.scalars().all()
                # simulate small movement
                updates = []
                for d in drone_objs:
                    new_lat = round(d.lat + random.uniform(-0.0015, 0.0015), 6)
                    new_lng = round(d.lng + random.uniform(-0.0015, 0.0015), 6)
                    new_battery = max(
                        0, d.battery_pct - random.choice([0, 0, 0, 1])
                    )
                    await session.execute(
                        update(Drone)
                        .where(Drone.id == d.id)
                        .values(
                            lat=new_lat,
                            lng=new_lng,
                            battery_pct=new_battery,
                        )
                    )
                    updates.append(
                        {
                            "id": d.id,
                            "lat": new_lat,
                            "lng": new_lng,
                            "battery_pct": new_battery,
                            "status": d.status,
                            "mission_id": d.mission_id,
                            "jurisdiction": d.jurisdiction,
                        }
                    )
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
                    "id": f"A-{random.randint(9000, 9999)}",
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
                async with async_session_factory() as session:
                    session.add(Alert(**a))
                    await session.commit()
                await hub.broadcast({"type": "alert", "alert": a})
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
