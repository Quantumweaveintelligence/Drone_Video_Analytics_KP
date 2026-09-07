"""
DVAS API — Drone Video Analytics System
Slim entrypoint. All endpoints live in routers/*.py
"""
from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import os
import uuid
import logging
import asyncio
from fastapi import FastAPI, Request
from starlette.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from core.config import CORS_ORIGINS, limiter
from core.security import fetch_jwks
from database import engine
from models import Base
from services.seed_service import seed_data
from services.telemetry_pump import start_telemetry_pump
from services.audit_sync import start_audit_sync_service
from ingest_proxy import ingest_api

from routers import auth, drones, alerts, audit, analytics, edge, infra, ws

# ---------- App ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dvas")

app = FastAPI(title="DVAS API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ---------- Middleware ----------
@app.middleware("http")
async def inject_request_id(request: Request, call_next):
    """Assign a unique request ID to every incoming request for audit tracing."""
    request.state.request_id = str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response

# ---------- Lifecycle ----------
@app.on_event("startup")
async def startup_event():
    fetch_jwks()

@app.on_event("startup")
async def _startup():
    await seed_data()

@app.on_event("startup")
async def _start_pump():
    start_telemetry_pump()

@app.on_event("startup")
async def _start_sync():
    start_audit_sync_service()

# ---------- Root ----------
@app.get("/api/")
async def root():
    return {"service": "DVAS", "status": "ok"}

# ---------- Register routers ----------
app.include_router(auth.router)
app.include_router(drones.router)
app.include_router(alerts.router)
app.include_router(audit.router)
app.include_router(analytics.router)
app.include_router(edge.router)
app.include_router(infra.router)
app.include_router(ws.router)
app.include_router(ingest_api)

# ---------- CORS ----------
cors_origins = [o.strip() for o in CORS_ORIGINS if o.strip() and o.strip() != "*"]
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=cors_origins or ["http://localhost:3000"],
    allow_origin_regex=r"^https?://.*(:[0-9]+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)
