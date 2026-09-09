import random
import logging
from datetime import datetime, timezone, timedelta
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
import httpx
from sqlalchemy import select, update

from database import async_session_factory
from models import Drone, ModelConfig
from core.security import get_current_user, require_roles, _row_to_dict
from core.config import (
    CROWD_URL, PERSON_URL, VEHICLE_URL, ANPR_URL, INTRUSION_URL,
    MINIO_PROXY_ENDPOINT, MINIO_PROXY_ACCESS, MINIO_PROXY_SECRET, MINIO_PROXY_SECURE,
    limiter,
)
from core.analytics_client import _get_minio_client
from audit_service import write_audit_v2
from schemas.models import ModelUpdateIn

logger = logging.getLogger("dvas")
router = APIRouter(prefix="/api", tags=["Infrastructure & Models"])


@router.get("/infra/health")
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
    {"key": "crowd",        "label": "CSRNet Crowd Density",          "gpu_pct": 20, "throughput_fps": 8,  "default": True},
    {"key": "plate",        "label": "Number-Plate (ANPR)",          "gpu_pct": 20, "throughput_fps": 40, "default": False},
    {"key": "intrusion",    "label": "GeoTrax Intrusion Detection",  "gpu_pct": 15, "throughput_fps": 45, "default": False},
]


def _build_service_map():
    """Build SERVICE_MAP after analytics URL constants are defined."""
    return {
        "person":  PERSON_URL,
        "face":    PERSON_URL,   # face detection runs inside person-analyzer
        "vehicle": VEHICLE_URL,
        "crowd":   CROWD_URL,
        "plate":   ANPR_URL,
        "intrusion": INTRUSION_URL,
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
        "intrusion": f"{INTRUSION_URL}/health",
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
    service_keys = {"crowd", "person", "vehicle", "plate", "intrusion"}
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
        "intrusion": INTRUSION_URL,
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


@router.get("/models")
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


@router.post("/models")
async def update_models(
    body: ModelUpdateIn,
    # CRO (former field_supervisor) + Senior Officer (former supervisory_officer) can update models
    user: dict = Depends(require_roles("control_room_officer", "senior_officer")),
    request: Request = None,
):
    valid_keys = {m["key"] for m in AVAILABLE_MODELS}
    invalid = [k for k in body.active if k not in valid_keys]
    if invalid:
        raise HTTPException(400, f"Unknown model keys: {invalid}")
    scope = user.get("jurisdiction") or "State HQ"
    now_iso = datetime.now(timezone.utc).isoformat()
    old_active = []
    async with async_session_factory() as session:
        existing = await session.execute(
            select(ModelConfig).where(ModelConfig.scope == scope)
        )
        existing_obj = existing.scalar_one_or_none()
        if existing_obj and existing_obj.active:
            old_active = existing_obj.active
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
    await write_audit_v2(
        actor=user,
        action="model_config_update",
        target=scope,
        target_type="configuration",
        target_name=f"Model Config ({scope})",
        details=f"active={','.join(body.active)}",
        before_values={"active": old_active},
        after_values={"active": body.active},
        source_endpoint="POST /api/models",
        request=request,
    )

    # Actually start/stop the analytics stream loops based on what was toggled
    stream_results = await _apply_stream_changes(body.active)
    logger.info("model_deploy", extra={"stream_results": stream_results, "active": body.active, "by": user["email"]})

    updated_cfg = await _get_model_config(scope)
    stream_status = await _get_service_stream_status()
    return {
        **updated_cfg,
        "stream_status": stream_status,
        "stream_actions": stream_results,   # what was started/stopped/unchanged
        "deploy_ok": True,
    }


@router.get("/infra/overview")
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


@router.get("/minio/image")
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
