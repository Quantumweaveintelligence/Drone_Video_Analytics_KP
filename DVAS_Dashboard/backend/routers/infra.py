import asyncio
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
from core.config import PII_VIEW_ROLES
from core.config import (
    CROWD_URL, PERSON_URL, VEHICLE_URL, ANPR_URL, INTRUSION_URL, VESSEL_URL, ANOMALY_URL,
    MINIO_PROXY_ENDPOINT, MINIO_PROXY_ACCESS, MINIO_PROXY_SECRET, MINIO_PROXY_SECURE,
    limiter,
)
from core.analytics_client import _get_minio_client, _service_headers
from core import runtime
from audit_service import write_audit_v2
from schemas.models import ModelUpdateIn
from services.system_telemetry import (
    collect_system_hardware,
    collect_stream_overview,
    collect_queue_metrics,
    collect_frame_ingestion,
    collect_metadata_validation,
    probe_api_monitor,
    update_trend_history,
)

logger = logging.getLogger("dvas")
router = APIRouter(prefix="/api", tags=["Infrastructure & Models"])

CLUSTER_SCOPE = "cluster"


class WorkerSyncIn(BaseModel):
    service: str
    action: str



@router.get("/infra/health")
async def infra_health(user: dict = Depends(require_roles("system_administrator"))):
    """Infrastructure health for sub-tabs (Feeds, GPU, Queue, Alerts) using real data."""
    now = datetime.now(timezone.utc)
    streams, _ = await collect_stream_overview()
    infra = await collect_system_hardware()
    queues = await collect_queue_metrics()

    feeds = [
        {
            "drone_id": s["drone_id"],
            "operator": s["operator"],
            "mission_id": s["mission_id"],
            "status": s["status"],
            "last_heartbeat": s["last_frame_at"],
            "fps": s["fps"],
            "bitrate_mbps": s["bitrate_mbps"],
        }
        for s in streams
    ]

    gpu = [
        {
            "node": f"gpu-0 ({runtime.gpu_name_nvml() or 'GPU'})",
            "util_pct": infra["gpu_pct"],
            "mem_used_gb": infra["gpu_mem_used_gb"],
            "mem_total_gb": infra["gpu_mem_total_gb"],
            # The analyzers do not report batch size or inference time.
            "batch": None,
            "inference_ms": None,
        }
    ]

    queue = [
        {
            "partition": f"q-{q['name']}",
            "depth": q["depth"],
            "throughput_fps": q["throughput_fps"],
            "throughput_unit": q["throughput_unit"],
        }
        for q in queues
    ]

    alerts_infra = []
    if infra["cpu_pct"] is not None and infra["cpu_pct"] > 85:
        alerts_infra.append({
            "id": "alert-cpu",
            "severity": "warn",
            "text": f"High Host CPU utilization: {infra['cpu_pct']}%",
            "ts": now.isoformat(),
        })
    if infra["disk_pct"] is not None and infra["disk_pct"] > 90:
        alerts_infra.append({
            "id": "alert-disk",
            "severity": "warn",
            "text": f"Host disk space usage high: {infra['disk_pct']}% ({infra['disk_used_gb']}GB / {infra['disk_total_gb']}GB)",
            "ts": now.isoformat(),
        })
    try:
        if not runtime.IS_SUPERVISOR:
            raise RuntimeError("process supervision is only reported in the supervisor runtime")
        table = await asyncio.get_running_loop().run_in_executor(None, runtime.get_process_table)
        down = sorted(name for name, info in table.items() if info.get("statename") != "RUNNING")
        if down:
            alerts_infra.append({
                "id": "alert-processes",
                "severity": "warn",
                "text": f"{len(down)} of {len(table)} services not running: {', '.join(down)}",
                "ts": now.isoformat(),
            })
        elif not alerts_infra:
            alerts_infra.append({
                "id": "alert-nominal",
                "severity": "info",
                "text": f"All {len(table)} supervised services running",
                "ts": now.isoformat(),
            })
    except Exception as exc:
        alerts_infra.append({
            "id": "alert-supervisor",
            "severity": "info" if not runtime.IS_SUPERVISOR else "warn",
            "text": f"Service status unavailable: {exc}",
            "ts": now.isoformat(),
        })

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
    {"key": "vessel",       "label": "SeaDrone Maritime Tracking",   "gpu_pct": 15, "throughput_fps": 60, "default": False},
    {"key": "anomaly",      "label": "Anomaly & Suspicious Activity (VisDrone YOLO11x)", "gpu_pct": 30, "throughput_fps": 12, "default": False},
]


def _build_service_map():
    """Build SERVICE_MAP after analytics URL constants are defined."""
    return {
        "person":    PERSON_URL,
        "face":      PERSON_URL,   # face detection runs inside person-analyzer
        "vehicle":   VEHICLE_URL,
        "crowd":     CROWD_URL,
        "plate":     ANPR_URL,
        "intrusion": INTRUSION_URL,
        "vessel":    VESSEL_URL,
        "anomaly":   ANOMALY_URL,
    }


async def _fetch_single_status(client: httpx.AsyncClient, key: str, url: str) -> tuple:
    try:
        r = await client.get(url, timeout=5.0, headers=_service_headers())
        data = r.json() if r.status_code == 200 else {}
        return key, bool(data.get("stream_running", False))
    except Exception:
        return key, False


async def _get_service_stream_status() -> dict:
    """
    Query each analytics service health endpoint concurrently and return
    {model_key: stream_running bool} for all services.
    face shares person-analyzer status.
    """
    urls = {
        "crowd":     f"{CROWD_URL}/health",
        "person":    f"{PERSON_URL}/health",
        "vehicle":   f"{VEHICLE_URL}/health",
        "plate":     f"{ANPR_URL}/health",
        "intrusion": f"{INTRUSION_URL}/health",
        "vessel":    f"{VESSEL_URL}/health",
        "anomaly":   f"{ANOMALY_URL}/health",
    }
    status = {}
    async with httpx.AsyncClient() as client:
        tasks = [_fetch_single_status(client, key, url) for key, url in urls.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            if isinstance(res, tuple):
                status[res[0]] = res[1]
    status["face"] = status.get("person", False)  # face shares person-analyzer
    return status


async def _apply_single_change(client: httpx.AsyncClient, svc_key: str, base_url: str, should_run: bool, is_running: bool) -> tuple:
    if should_run and not is_running:
        try:
            r = await client.get(f"{base_url}/stream/start", timeout=12.0, headers=_service_headers())
            return svc_key, ("started" if r.status_code == 200 else f"start_failed({r.status_code})")
        except Exception as e:
            return svc_key, f"start_error({e})"
    elif not should_run and is_running:
        try:
            r = await client.get(f"{base_url}/stream/stop", timeout=12.0, headers=_service_headers())
            return svc_key, ("stopped" if r.status_code == 200 else f"stop_failed({r.status_code})")
        except Exception as e:
            return svc_key, f"stop_error({e})"
    elif not should_run and not is_running:
        # Guarantee GPU VRAM is released on deselected service
        try:
            await client.get(f"{base_url}/stream/stop", timeout=5.0, headers=_service_headers())
        except Exception:
            pass
        return svc_key, "already_stopped"
    else:
        return svc_key, ("already_running" if is_running else "already_stopped")


async def _apply_stream_changes(requested_active: list) -> dict:
    """
    Start/stop analytics stream loops concurrently based on the requested active model list.
    Enforces that only selected models hold GPU memory; deselected models release VRAM.
    Returns dict of {model_key: action_taken} e.g. {"crowd": "started", "plate": "already_stopped"}
    """
    effective_active = set(requested_active)
    if "face" in effective_active:
        effective_active.add("person")

    current_status = await _get_service_stream_status()
    smap = {
        "crowd":     CROWD_URL,
        "person":    PERSON_URL,
        "vehicle":   VEHICLE_URL,
        "plate":     ANPR_URL,
        "intrusion": INTRUSION_URL,
        "vessel":    VESSEL_URL,
        "anomaly":   ANOMALY_URL,
    }

    results = {}
    async with httpx.AsyncClient() as client:
        tasks = [
            _apply_single_change(
                client, svc_key, base_url,
                svc_key in effective_active,
                current_status.get(svc_key, False)
            )
            for svc_key, base_url in smap.items()
        ]
        change_results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in change_results:
            if isinstance(res, tuple):
                results[res[0]] = res[1]
            elif isinstance(res, Exception):
                logger.error("Error applying stream change: %s", res)
    return results


async def _get_model_config(scope: str = CLUSTER_SCOPE):
    async with async_session_factory() as session:
        result = await session.execute(
            select(ModelConfig).where(ModelConfig.scope == scope)
        )
        obj = result.scalar_one_or_none()
        if not obj:
            # Fallback: check if State HQ or another scope had configuration
            fallback = await session.execute(
                select(ModelConfig).order_by(ModelConfig.updated_at.desc())
            )
            fb_obj = fallback.scalars().first()
            if fb_obj and fb_obj.active:
                active = fb_obj.active
            else:
                active = [m["key"] for m in AVAILABLE_MODELS if m["default"]]
            return {"scope": scope, "active": active, "updated_at": None, "updated_by": None}
        return _row_to_dict(obj)


@router.get("/models")
async def list_models(user: dict = Depends(get_current_user)):
    if user["role"] not in ("control_room_officer", "senior_officer", "system_administrator"):
        raise HTTPException(403, "Not permitted")
    cfg = await _get_model_config(CLUSTER_SCOPE)
    gpu_total = sum(m["gpu_pct"] for m in AVAILABLE_MODELS if m["key"] in cfg["active"])

    # Fetch real stream_running status from each analytics service in parallel
    stream_status = await _get_service_stream_status()

    # Annotate each model with its real running state
    available_with_status = [
        {
            **m,
            "stream_running": stream_status.get(m["key"], False),
        }
        for m in AVAILABLE_MODELS
    ]

    # Fetch real system hardware telemetry
    gpu_metrics = {"gpu_pct": 0, "used_gb": 0.0, "total_gb": 4.0, "free_gb": 4.0}
    try:
        infra = await collect_system_hardware()
        used = infra.get("gpu_used_gb", 0.0)
        tot = infra.get("gpu_total_gb", 4.0)
        gpu_metrics = {
            "gpu_pct": infra.get("gpu_pct", 0),
            "used_gb": used,
            "total_gb": tot,
            "free_gb": max(0.0, round(tot - used, 1)),
        }
    except Exception:
        pass

    return {
        "available": available_with_status,
        "active": cfg["active"],
        "stream_status": stream_status,
        "scope": CLUSTER_SCOPE,
        "gpu_total_pct": gpu_total,
        "gpu_metrics": gpu_metrics,
        "updated_at": cfg.get("updated_at"),
        "updated_by": cfg.get("updated_by"),
        "read_only": False,
    }


MODEL_ADMIN_ROLES = ("senior_officer", "system_administrator")   # M9: shared cluster config


async def apply_model_config(active: list, user: dict, request=None, reason: str = "") -> dict:
    """Persist the active model set, audit it and start/stop the analytics streams.
    Used by POST /models and by deploying an AI model suggestion (ING-02)."""
    body = ModelUpdateIn(active=active)
    valid_keys = {m["key"] for m in AVAILABLE_MODELS}
    invalid = [k for k in body.active if k not in valid_keys]
    if invalid:
        raise HTTPException(400, f"Unknown model keys: {invalid}")
    now_iso = datetime.now(timezone.utc).isoformat()
    old_active = []
    async with async_session_factory() as session:
        existing = await session.execute(
            select(ModelConfig).where(ModelConfig.scope == CLUSTER_SCOPE)
        )
        existing_obj = existing.scalar_one_or_none()
        if existing_obj and existing_obj.active:
            old_active = existing_obj.active
        result = await session.execute(
            update(ModelConfig)
            .where(ModelConfig.scope == CLUSTER_SCOPE)
            .values(
                active=body.active,
                updated_at=now_iso,
                updated_by=user["email"],
            )
        )
        if result.rowcount == 0:
            session.add(ModelConfig(
                scope=CLUSTER_SCOPE,
                active=body.active,
                updated_at=now_iso,
                updated_by=user["email"],
            ))
        await session.commit()

    await write_audit_v2(
        actor=user,
        action="model_config_update",
        target=CLUSTER_SCOPE,
        target_type="configuration",
        target_name=f"Model Config ({CLUSTER_SCOPE})",
        details=f"active={','.join(body.active)}" + (f"; {reason}" if reason else ""),
        before_values={"active": old_active},
        after_values={"active": body.active},
        source_endpoint="POST /api/models",
        request=request,
    )

    # Concurrently start/stop the analytics stream loops based on what was toggled
    stream_results = await _apply_stream_changes(body.active)
    logger.info("model_deploy", extra={"stream_results": stream_results, "active": body.active, "by": user["email"]})
    return stream_results


@router.post("/models")
async def update_models(
    body: ModelUpdateIn,
    user: dict = Depends(require_roles(*MODEL_ADMIN_ROLES)),
    request: Request = None,
):
    stream_results = await apply_model_config(body.active, user, request)

    updated_cfg = await _get_model_config(CLUSTER_SCOPE)
    stream_status = await _get_service_stream_status()
    gpu_total = sum(m["gpu_pct"] for m in AVAILABLE_MODELS if m["key"] in updated_cfg["active"])

    available_with_status = [
        {
            **m,
            "stream_running": stream_status.get(m["key"], False),
        }
        for m in AVAILABLE_MODELS
    ]

    # Fetch real system hardware telemetry
    gpu_metrics = {"gpu_pct": 0, "used_gb": 0.0, "total_gb": 4.0, "free_gb": 4.0}
    try:
        infra = await collect_system_hardware()
        used = infra.get("gpu_used_gb", 0.0)
        tot = infra.get("gpu_total_gb", 4.0)
        gpu_metrics = {
            "gpu_pct": infra.get("gpu_pct", 0),
            "used_gb": used,
            "total_gb": tot,
            "free_gb": max(0.0, round(tot - used, 1)),
        }
    except Exception:
        pass

    return {
        **updated_cfg,
        "available": available_with_status,
        "stream_status": stream_status,
        "stream_actions": stream_results,
        "gpu_total_pct": gpu_total,
        "gpu_metrics": gpu_metrics,
        "deploy_ok": True,
        "read_only": False,
    }


@router.post("/models/sync-worker")
async def sync_worker_state(
    body: WorkerSyncIn,
    user: dict = Depends(require_roles("control_room_officer", "senior_officer", "system_administrator")),
):
    """Synchronize an individual worker start/stop from an analytics page with ModelConfig."""
    key = body.service
    if key == "anpr":
        key = "plate"
    now_iso = datetime.now(timezone.utc).isoformat()
    async with async_session_factory() as session:
        existing = await session.execute(
            select(ModelConfig).where(ModelConfig.scope == CLUSTER_SCOPE)
        )
        existing_obj = existing.scalar_one_or_none()
        current_active = list(existing_obj.active) if existing_obj and existing_obj.active else [m["key"] for m in AVAILABLE_MODELS if m["default"]]

        if body.action == "start" and key not in current_active:
            current_active.append(key)
        elif body.action == "stop" and key in current_active:
            current_active = [k for k in current_active if k != key]

        result = await session.execute(
            update(ModelConfig)
            .where(ModelConfig.scope == CLUSTER_SCOPE)
            .values(active=current_active, updated_at=now_iso, updated_by=user["email"])
        )
        if result.rowcount == 0:
            session.add(ModelConfig(
                scope=CLUSTER_SCOPE,
                active=current_active,
                updated_at=now_iso,
                updated_by=user["email"],
            ))
        await session.commit()
    return {"ok": True, "active": current_active}


@router.get("/infra/overview")
async def infra_overview(user: dict = Depends(require_roles("system_administrator"))):
    """Rich NOC landing page data with real end-to-end telemetry."""
    now = datetime.now(timezone.utc)

    # 1. Real Streams & Stream Stats (DB + Ingest Server live status)
    streams, stream_stats = await collect_stream_overview()

    # 2. Real System Hardware Gauges (/proc/stat, /proc/meminfo, disk, nvidia-smi, net)
    infra = await collect_system_hardware()

    # 3. Real Queues (Ingest queues, analytics, evidence, audit)
    queues = await collect_queue_metrics()

    # 4. Real Frame Ingestion & Sparkline (Ingest counters + rolling buffer)
    frames = await collect_frame_ingestion()

    # 5. Real Metadata Validation (Verified telemetry records in DB)
    metadata = await collect_metadata_validation()

    # 6. Real API Monitor (Live probe of internal services)
    api_info = await probe_api_monitor()

    # 7. Real CPU/GPU Trend (Rolling 24-sample history of real measurements)
    trend = update_trend_history(infra["cpu_pct"], infra["gpu_pct"])

    return {
        "streams": streams,
        "stream_stats": stream_stats,
        "infra": infra,
        "queues": queues,
        "frames": frames,
        "metadata": metadata,
        "api": api_info,
        "trend": trend,
        "generated_at": now.isoformat(),
    }


# Live analytics buckets the analytics pages may show (evidence copies are served only by
# GET /api/evidence/{id}/objects/{role}; the vault is never reachable here — E5)
_PROXY_BUCKETS = {
    "dvas-plates", "dvas-vehicles", "dvas-evidence", "dvas-faces",
    "dvas-heatmaps", "dvas-intrusion", "dvas-watchlist", "dvas-vessels-maritime",
    "dvas-anomaly"
}
_PROXY_PII_BUCKETS = {"dvas-faces", "dvas-plates", "dvas-watchlist"}


@router.get("/minio/image")
async def minio_image_proxy(bucket: str, key: str, user=Depends(get_current_user)):
    """
    Securely proxy MinIO object downloads through the backend.
    Requires authentication — no direct MinIO exposure to browsers.
    Face / plate / watchlist images only for PII_VIEW_ROLES (Q5); short private cache.
    """
    if not bucket or not key:
        raise HTTPException(status_code=400, detail="bucket and key are required")

    if bucket not in _PROXY_BUCKETS:
        raise HTTPException(status_code=403, detail=f"Bucket '{bucket}' not allowed")
    if bucket in _PROXY_PII_BUCKETS and user.get("role") not in PII_VIEW_ROLES:
        raise HTTPException(status_code=403, detail="Face and plate images are restricted for this role")
    if user.get("role") == "drone_operator" and bucket == "dvas-evidence":
        raise HTTPException(status_code=403, detail="Not permitted")

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
                "Cache-Control": "private, max-age=60",
                "X-Content-Type-Options": "nosniff",
            },
        )
    except Exception as exc:
        logger.debug("MinIO proxy fetch failed (%s/%s): %s", bucket, key, exc)
        raise HTTPException(status_code=404, detail="Image not found")
