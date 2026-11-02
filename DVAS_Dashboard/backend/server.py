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

from core.config import CORS_ORIGINS, IS_CLOUD, PUBLIC_BASE_URL, limiter, validate_required_secrets
from core.security import fetch_jwks
from database import engine
from models import Base
from services.seed_service import seed_data
from services.telemetry_pump import start_telemetry_pump
from services.audit_sync import start_audit_sync_service
from services.evidence_sync import start_evidence_sync
from services.retention import start_retention_job
from services.report_jobs import start_report_worker
from ingest_proxy import ingest_api

from routers import auth, drones, alerts, audit, analytics, edge, infra, ws, incidents, reports, push, cases, downloads

# ---------- App ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dvas")

# Fail fast in cloud mode if a required secret is missing (RunPod plan 2.4).
validate_required_secrets()

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

@app.on_event("startup")
async def _start_evidence_sync():
    start_evidence_sync()

@app.on_event("startup")
async def _start_retention_job():
    start_retention_job()

@app.on_event("startup")
async def _start_report_worker():
    start_report_worker()

@app.on_event("startup")
async def _sync_models_on_startup():
    async def _do_sync():
        await asyncio.sleep(5)
        try:
            from routers.infra import _get_model_config, _apply_stream_changes, CLUSTER_SCOPE
            cfg = await _get_model_config(CLUSTER_SCOPE)
            if cfg and "active" in cfg:
                await _apply_stream_changes(cfg["active"])
        except Exception as e:
            logging.getLogger("dvas").warning("Model startup sync deferred: %s", e)
    asyncio.create_task(_do_sync())

# ---------- Root ----------
@app.get("/api/")
async def root():
    return {"service": "DVAS", "status": "ok"}

# ---------- Register routers ----------
app.include_router(auth.router)
app.include_router(drones.router)
app.include_router(alerts.router)
app.include_router(cases.router)
app.include_router(downloads.router)
app.include_router(audit.router)
app.include_router(analytics.router)
app.include_router(edge.router)
app.include_router(infra.router)
app.include_router(ws.router)
app.include_router(ingest_api)
app.include_router(incidents.router)
app.include_router(reports.router)
app.include_router(push.router)

# ---------- CORS ----------
cors_origins = [o.strip() for o in CORS_ORIGINS if o.strip() and o.strip() != "*"]
if IS_CLOUD:
    # Cloud (RunPod): explicit allow-list only — the public pod URL plus the
    # edge application's local gateway. No wildcard regex (RunPod plan 2.4 / S5).
    cloud_origins = set(cors_origins)
    if PUBLIC_BASE_URL:
        cloud_origins.add(PUBLIC_BASE_URL)
    cloud_origins.add("http://localhost:8765")
    app.add_middleware(
        CORSMiddleware,
        allow_credentials=True,
        allow_origins=sorted(cloud_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_credentials=True,
        allow_origins=cors_origins or ["http://localhost:3000"],
        allow_origin_regex=r"^https?://.*(:[0-9]+)?$",
        allow_methods=["*"],
        allow_headers=["*"],
    )
