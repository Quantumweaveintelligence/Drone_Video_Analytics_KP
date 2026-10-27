# -*- coding: utf-8 -*-
"""
DVAS Anomaly & Suspicious Activity Analytics Service — FastAPI on port 8107.
End-to-end aerial cognitive surveillance: dual-model detection, motion compensation,
persistent tracking, behavioral anomaly detection, 8-stage alert gating, MinIO snapshot
persistence, and Redis stream publishing.
"""

import asyncio
import base64
import json as _json
import logging
import os
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request, Response, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

sys.path.insert(0, "/analytics")
sys.path.insert(0, "/analytics/anomaly")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared.config import cfg, get_drone_aliases
from shared.db import (
    close_pool, get_pool,
    upsert_anomaly_track, write_anomaly_event,
    get_active_anomaly_tracks, get_anomaly_events,
    get_anomaly_summary_stats, get_anomaly_stats_by_type
)
from shared.redis_client import (
    close_client, get_client, publish_analytics_result,
    publish_confirmed_event, xreadgroup_frames, ack_frames,
    scan_active_stream_drones, get_latest_stream_frame
)
from shared import minio_client as minio
from analyzer import AnomalyAnalyzer

logging.basicConfig(
    level=cfg.LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("anomaly-service")

app = FastAPI(
    title="DVAS Anomaly & Suspicious Activity Analyzer",
    description="Cognitive aerial anomaly detection, behavioral reasoning, and multi-stage alert gating",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Service State ─────────────────────────────────────────────────────────────
_analyzers: Dict[str, AnomalyAnalyzer] = {}
_stream_task: Optional[asyncio.Task] = None
_stream_running = False
_frames_processed = 0

# In-memory per-drone telemetry & frame buffers
_last_annotated: Dict[str, bytes] = {}
_last_annotated_ts: Dict[str, float] = {}
_fps_tracker: Dict[str, deque] = defaultdict(lambda: deque(maxlen=20))

# Configuration overrides (runtime adjustable via PUT /config)
_runtime_config: Dict[str, Any] = {
    "conf_thresh": float(os.getenv("ANOMALY_CONFIDENCE", "0.35")),
    "iou_thresh": 0.40,
    "hysteresis_enter": float(os.getenv("ANOMALY_ALERT_HYSTERESIS_ENTER", "0.72")),
    "hysteresis_maintain": 0.60,
    "hysteresis_clear": 0.48,
    "cooldown_seconds": float(os.getenv("ANOMALY_COOLDOWN_S", "45")),
    "loitering_duration_sec": 30.0,
    "min_evidence_groups": 2,
}

BUCKET_ANOMALY = os.getenv("BUCKET_ANOMALY", "dvas-anomaly")
FRAME_CACHE_TTL_SEC = 6.0


def _get_or_create_analyzer(drone_id: str) -> AnomalyAnalyzer:
    """Retrieve or lazily initialize the AnomalyAnalyzer for a given drone."""
    if drone_id not in _analyzers:
        primary_weights = os.getenv(
            "ANOMALY_MODEL_PRIMARY",
            "/analytics/anomaly/models/weights/visdrone-yolov11x.pt"
        )
        secondary_weights = os.getenv(
            "ANOMALY_MODEL_SECONDARY",
            "/analytics/anomaly/models/weights/yolo11n.pt"
        )
        _analyzers[drone_id] = AnomalyAnalyzer(
            drone_id=drone_id,
            primary_weights_path=primary_weights,
            secondary_weights_path=secondary_weights,
            config_dir="config",
            device="cuda" if os.path.exists("/proc/driver/nvidia") else "cpu",
            conf_thresh=_runtime_config["conf_thresh"],
            iou_thresh=_runtime_config["iou_thresh"],
        )
        # Apply runtime config overrides to rules engine
        _analyzers[drone_id].rules_engine.cooldown_seconds = _runtime_config["cooldown_seconds"]
        _analyzers[drone_id].alert_manager.cooldown_seconds = _runtime_config["cooldown_seconds"]
        _analyzers[drone_id].rules_engine.hysteresis_enter = _runtime_config["hysteresis_enter"]
        _analyzers[drone_id].rules_engine.loiter_dwell_thresh_sec = _runtime_config["loitering_duration_sec"]
    return _analyzers[drone_id]


# ── Background Stream Processing Loop ─────────────────────────────────────────

async def _stream_loop():
    global _stream_running, _frames_processed
    active_drones: List[str] = []
    log.info("Anomaly stream loop started (dynamic stream discovery mode)")

    loop_count = 0
    while _stream_running:
        loop_count += 1
        if loop_count % 5 == 1 or not active_drones:
            try:
                discovered = await scan_active_stream_drones()
                for d in discovered:
                    if d not in active_drones:
                        active_drones.append(d)
                        log.info("Discovered active ingest stream for anomaly analysis: %s", d)
            except Exception as e:
                log.debug("Stream scan error: %s", e)

        if not active_drones:
            await asyncio.sleep(0.5)
            continue

        for drone_id in list(active_drones):
            try:
                frames = await xreadgroup_frames(
                    "anomaly-group", "anomaly-analyzer", drone_id, ">", count=1, block_ms=100
                )
                if not frames:
                    # Check for un-ACKed pending messages in the PEL
                    pending = await xreadgroup_frames(
                        "anomaly-group", "anomaly-analyzer", drone_id, "0", count=1, block_ms=10
                    )
                    if pending:
                        frames = pending
                    else:
                        continue

                pool = await get_pool()

                for entry_id, f in frames:
                    raw = f.get("jpeg_b64", "")
                    if not raw:
                        await ack_frames(drone_id, "anomaly-group", entry_id)
                        continue

                    # Decode base64 frame
                    try:
                        img_bytes = base64.b64decode(raw)
                        nparr = np.frombuffer(img_bytes, np.uint8)
                        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    except Exception as e:
                        log.debug("Frame decode error: %s", e)
                        continue

                    if frame is None:
                        continue

                    t_start = time.monotonic()
                    frame_ts = datetime.now(timezone.utc)

                    # Extract metadata if present
                    raw_meta = f.get("metadata", "")
                    metadata_dict = {}
                    if raw_meta:
                        if isinstance(raw_meta, bytes):
                            raw_meta = raw_meta.decode()
                        try:
                            metadata_dict = _json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
                        except Exception:
                            metadata_dict = {}

                    mission_id = metadata_dict.get("mission_id") or ""
                    if not mission_id and drone_id.startswith("DRN-MSN-"):
                        mission_id = drone_id[4:]

                    # Process frame through AnomalyAnalyzer pipeline in worker thread
                    analyzer = _get_or_create_analyzer(drone_id)
                    annotated_bgr, triggered_alerts, tracks_summary = await asyncio.to_thread(
                        analyzer.process_frame,
                        frame,
                        metadata_dict,
                        time.time() - analyzer.start_time,
                    )

                    # Encode annotated frame as JPEG for the live /annotated/latest endpoint
                    success, jpeg_buf = cv2.imencode(".jpg", annotated_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    if success:
                        _last_annotated[drone_id] = jpeg_buf.tobytes()
                        _last_annotated_ts[drone_id] = time.time()

                    # Handle Triggered Alerts (Evidence Capture & Escalation)
                    for alert in triggered_alerts:
                        event_type = (alert.get("event") or alert.get("event_type") or "suspicious_activity").lower()
                        # Normalise to standard DVAS event types
                        type_map = {
                            "suspicious_object_detected": "abandoned_object",
                            "abandoned_object": "abandoned_object",
                            "unknown_owner_object": "abandoned_object",
                            "unknown_owner_weapon": "weapon_abandoned",
                            "weapon_like_object": "weapon_detected",
                            "weapon_held": "weapon_held",
                            "weapon_aiming_candidate": "weapon_aiming",
                            "weapon_aiming": "weapon_aiming",
                            "suspicious_package": "abandoned_object",
                            "unusual_object_placement": "unusual_placement",
                            "object_transfer": "object_transfer",
                            "loitering": "suspicious_loitering",
                            "loitering_event": "suspicious_loitering",
                            "fall_event": "person_fall",
                            "fall_collapse": "person_fall",
                            "persistent_following": "suspicious_following",
                            "following": "suspicious_following",
                            "opposing_crowd_flow": "opposing_crowd_flow",
                            "group_convergence": "group_convergence",
                            "rapid_crowd_dispersal": "rapid_crowd_dispersal",
                            "crowd_dispersal": "rapid_crowd_dispersal",
                            "erratic_movement": "erratic_movement",
                            "coordinated_arrival": "coordinated_arrival",
                            "boundary_crossing": "boundary_crossing",
                            "running_event": "running_flight",
                            "wrong_direction_vehicle": "vehicle_wrong_way",
                            "wrong_direction_event": "vehicle_wrong_way",
                            "wrong_way": "vehicle_wrong_way",
                            "high_risk_traffic_event": "vehicle_wrong_way",
                            "abnormal_vehicle_speed": "abnormal_vehicle_speed",
                            "abrupt_vehicle_stop": "vehicle_abrupt_stop",
                            "abrupt_stop": "vehicle_abrupt_stop",
                            "vehicle_person_interaction": "vehicle_person_interaction",
                            "vehicle_object_interaction": "suspicious_dropoff",
                            "vehicle_object_abandonment": "suspicious_dropoff",
                            "suspicious_dropoff": "suspicious_dropoff",
                            "vehicle_circling": "vehicle_circling",
                            "vehicle_approach_flee": "vehicle_approach_flee",
                            "vehicle_pedestrian_near_miss": "vehicle_pedestrian_near_miss",
                        }
                        canonical_type = type_map.get(event_type, event_type)
                        severity = alert.get("level", "HIGH")
                        summary_msg = alert.get("summary_explanation", "")

                        # Snapshot upload to MinIO
                        snapshot_key = ""
                        evidence_img = alert.get("evidence_image")
                        if evidence_img:
                            snapshot_key = f"alerts/{drone_id}/{evidence_img}"
                            # Read snapshot if written by alert_manager, or encode current frame
                            snapshot_path = os.path.join(analyzer.alert_manager.output_dir, evidence_img)
                            if os.path.exists(snapshot_path):
                                with open(snapshot_path, "rb") as sf:
                                    snap_bytes = sf.read()
                            else:
                                snap_bytes = jpeg_buf.tobytes() if success else b""
                            if snap_bytes:
                                await minio.upload_bytes(BUCKET_ANOMALY, snapshot_key, snap_bytes, "image/jpeg")

                        # Write to drone_postgres.anomaly_events
                        confidences = alert.get("confidences", {})
                        obj_info = alert.get("object", {})
                        owner_info = alert.get("owner", {})

                        event_id = await write_anomaly_event(
                            pool=pool,
                            drone_id=drone_id,
                            track_id=obj_info.get("track_id", 0),
                            event_type=canonical_type,
                            severity=severity,
                            class_name=obj_info.get("class", ""),
                            confidence_detector=confidences.get("detector_confidence", 0.0),
                            confidence_rule=confidences.get("rule_confidence", 0.0),
                            confidence_event=confidences.get("event_confidence", 0.0),
                            confidence_alert=confidences.get("alert_confidence", 0.0),
                            evidence_groups=alert.get("evidence_groups", []),
                            reasons=alert.get("reasons", []),
                            summary=summary_msg,
                            snapshot_key=snapshot_key,
                            bbox=obj_info.get("bbox", []),
                            owner_track_id=owner_info.get("track_id"),
                            owner_status=owner_info.get("status"),
                            temporal=alert.get("temporal", {}),
                            gps_lat=alert.get("gps_lat"),
                            gps_lon=alert.get("gps_lon"),
                            details=alert,
                            mission_id=mission_id,
                        )

                        # Publish to drone:events:confirmed (for evidence_sync & live alerts feed)
                        await publish_confirmed_event({
                            "event_id": event_id,
                            "event_type": canonical_type,
                            "drone_id": drone_id,
                            "mission_id": mission_id,
                            "track_id": obj_info.get("track_id"),
                            "confidence": confidences.get("alert_confidence", 0.75),
                            "severity": severity,
                            "snapshot_key": snapshot_key,
                            "title": f"Anomaly: {alert.get('event', 'Suspicious Activity')}",
                            "description": summary_msg,
                            "attributes": {
                                "evidence_groups": alert.get("evidence_groups", []),
                                "reasons": alert.get("reasons", []),
                                "summary": summary_msg,
                                "confidences": confidences,
                                "temporal": alert.get("temporal", {}),
                                "class_name": obj_info.get("class", ""),
                            },
                            "ts": frame_ts.isoformat(),
                        })

                        # Publish to analytics:anomaly:results for SSE stream
                        await publish_analytics_result("anomaly", {
                            "type": "anomaly_alert",
                            "drone_id": drone_id,
                            "event_id": event_id,
                            "event_type": canonical_type,
                            "severity": severity,
                            "summary": summary_msg,
                            "snapshot_key": snapshot_key,
                            "ts": frame_ts.isoformat(),
                        })

                    # Update active tracks in drone_postgres (batch upsert)
                    for trk_data in tracks_summary:
                        try:
                            await upsert_anomaly_track(
                                pool=pool,
                                drone_id=drone_id,
                                track_id=trk_data["track_id"],
                                class_name=trk_data["class_name"],
                                class_category=trk_data["class_category"],
                                suspicion_level=trk_data["suspicion_level"],
                                suspicion_score=trk_data["suspicion_score"],
                                pipeline_stage=trk_data["pipeline_stage"],
                                duration_s=trk_data["duration_s"],
                                trajectory=trk_data["trajectory"],
                                ground_trajectory=trk_data["ground_trajectory"],
                                attributes=trk_data["attributes"],
                                bbox_xyxy=trk_data["bbox_xyxy"],
                                is_stationary=trk_data["is_stationary"],
                                stationary_seconds=trk_data["stationary_seconds"],
                                owner_track_id=trk_data.get("owner_track_id"),
                                owner_distance_px=trk_data.get("owner_distance_px"),
                                snapshot_key="",
                                mission_id=mission_id,
                            )
                        except Exception as trk_err:
                            log.debug("Track upsert error: %s", trk_err)

                    _frames_processed += 1
                    t_elapsed = time.monotonic() - t_start
                    if t_elapsed > 0:
                        _fps_tracker[drone_id].append(1.0 / t_elapsed)
                    try:
                        await ack_frames(drone_id, "anomaly-group", entry_id)
                    except Exception as ack_err:
                        log.debug("Failed to ACK frame %s: %s", entry_id, ack_err)

            except Exception as e:
                log.error("[%s] Stream processing iteration error: %s", drone_id, e)
                if 'frames' in locals() and frames:
                    for eid, _ in frames:
                        try:
                            await ack_frames(drone_id, "anomaly-group", eid)
                        except Exception:
                            pass
                await asyncio.sleep(0.5)

        await asyncio.sleep(0.01)


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    global _stream_task, _stream_running
    log.info("Starting DVAS Anomaly Analytics Service...")
    await get_pool()
    await minio.ensure_bucket(BUCKET_ANOMALY)
    # Start stream consumer loop automatically
    _stream_running = True
    _stream_task = asyncio.create_task(_stream_loop())
    log.info("Anomaly Analytics Service startup complete on port 8107")


@app.on_event("shutdown")
async def shutdown_event():
    global _stream_running, _stream_task
    log.info("Stopping DVAS Anomaly Analytics Service...")
    _stream_running = False
    if _stream_task:
        _stream_task.cancel()
        try:
            await _stream_task
        except asyncio.CancelledError:
            pass
    await close_pool()
    await close_client()
    log.info("Shutdown complete")


# ── REST API Endpoints ────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Health check endpoint with live stream and telemetry metrics."""
    avg_fps = 0.0
    all_fps = [fps for d_fps in _fps_tracker.values() for fps in d_fps]
    if all_fps:
        avg_fps = round(float(np.mean(all_fps)), 1)

    return {
        "status": "ok",
        "service": "anomaly-analyzer",
        "stream_running": _stream_running,
        "frames_processed": _frames_processed,
        "active_drones": list(_analyzers.keys()),
        "average_fps": avg_fps,
        "config": _runtime_config,
    }


@app.get("/stream/start")
async def stream_start():
    """Start background Redis stream consumer loop."""
    global _stream_running, _stream_task
    if not _stream_running:
        _stream_running = True
        _stream_task = asyncio.create_task(_stream_loop())
        log.info("Anomaly stream loop started via API")
    return {"status": "started", "stream_running": True}


@app.get("/stream/stop")
async def stream_stop():
    """Stop background Redis stream consumer loop and release GPU VRAM."""
    global _stream_running, _stream_task, _analyzers
    if _stream_running:
        _stream_running = False
        if _stream_task:
            _stream_task.cancel()
            try:
                await _stream_task
            except asyncio.CancelledError:
                pass
            _stream_task = None
    if not cfg.VRAM_OFFLOAD_ON_STOP:
        # RunPod pod: keep the per-drone analyzers (and their models) resident on the GPU
        log.info("Anomaly stream loop stopped (models kept on GPU, VRAM_OFFLOAD_ON_STOP=false)")
        return {"status": "stopped", "stream_running": False}
    # Release dual models from GPU VRAM
    try:
        for d_id, analyzer in list(_analyzers.items()):
            if hasattr(analyzer, 'detector') and hasattr(analyzer.detector, 'model'):
                try:
                    analyzer.detector.model.to("cpu")
                except Exception:
                    pass
        _analyzers.clear()
    except Exception as e:
        log.warning("Error releasing anomaly models: %s", e)
    import gc
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception:
        pass
    log.info("Anomaly stream loop stopped and GPU VRAM released")
    return {"status": "stopped", "stream_running": False}


@app.get("/annotated/latest")
async def get_latest_annotated(drone_id: str = Query("")):
    """
    Return latest annotated frame with HUD overlay as JPEG.
    Supports on-demand frame recovery from Redis stream if not yet in memory or stale,
    guaranteeing that /annotated/latest never returns 404 while frames exist in the stream.
    """
    now = time.monotonic()
    frame_bytes = None
    matched_drone_id = None
    frame_age = 0.0

    if drone_id:
        aliases = get_drone_aliases(drone_id)
        for alias in aliases:
            if alias in _last_annotated:
                frame_bytes = _last_annotated[alias]
                matched_drone_id = alias
                frame_age = now - _last_annotated_ts.get(alias, 0.0)
                break
    elif _last_annotated:
        best_drone = max(_last_annotated_ts, key=_last_annotated_ts.get, default=None)
        if best_drone:
            frame_bytes = _last_annotated.get(best_drone)
            matched_drone_id = best_drone
            frame_age = now - _last_annotated_ts.get(best_drone, 0.0)

    # On-demand frame recovery from Redis stream if not in memory or stale
    if not frame_bytes or frame_age > FRAME_CACHE_TTL_SEC:
        candidate_ids = get_drone_aliases(drone_id) if drone_id else []
        if not candidate_ids:
            try:
                discovered = await scan_active_stream_drones()
                candidate_ids = list(dict.fromkeys(list(_last_annotated.keys()) + discovered))
            except Exception:
                candidate_ids = list(_last_annotated.keys())

        for alias in candidate_ids:
            recovered_frame = await get_latest_stream_frame(alias)
            if recovered_frame is not None:
                try:
                    analyzer = _get_or_create_analyzer(alias)
                    annotated_bgr, triggered_alerts, tracks_summary = await asyncio.to_thread(
                        analyzer.process_frame,
                        recovered_frame,
                        {},
                        time.time() - analyzer.start_time,
                    )
                    success, jpeg_buf = cv2.imencode(".jpg", annotated_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    if success:
                        frame_bytes = jpeg_buf.tobytes()
                        _last_annotated[alias] = frame_bytes
                        _last_annotated_ts[alias] = now
                        matched_drone_id = alias
                        frame_age = 0.0
                        break
                except Exception as exc:
                    log.warning("Failed to recover and annotate anomaly frame for %s: %s", alias, exc)

    if not frame_bytes:
        raise HTTPException(status_code=404, detail=f"No active stream frame for drone '{drone_id}'")

    is_live = frame_age <= FRAME_CACHE_TTL_SEC
    feed_status = "live" if is_live else "offline"

    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
        "Access-Control-Expose-Headers": "X-Drone-ID, X-Frame-Age, X-Feed-Status, X-Stream-Status",
        "X-Drone-ID": matched_drone_id or drone_id,
        "X-Frame-Age": f"{frame_age:.2f}s",
        "X-Frame-Timestamp": str(_last_annotated_ts.get(matched_drone_id or drone_id, 0.0)),
        "X-Feed-Status": feed_status,
        "X-Stream-Status": feed_status,
    }
    return Response(content=frame_bytes, media_type="image/jpeg", headers=headers)




@app.get("/tracks/active")
async def get_active_tracks(
    drone_id: str = Query(""),
    limit: int = Query(100, ge=1, le=200)
):
    """Return active tracked entities with suspicion scores and attributes."""
    pool = await get_pool()
    tracks = await get_active_anomaly_tracks(pool, drone_id=drone_id or None, limit=limit)
    return {"tracks": tracks, "count": len(tracks)}


@app.get("/alerts/recent")
async def get_recent_alerts(
    drone_id: str = Query(""),
    mission_id: str = Query(""),
    event_type: str = Query(""),
    severity: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
):
    """Return recent confirmed anomaly alerts."""
    pool = await get_pool()
    events = await get_anomaly_events(
        pool,
        drone_id=drone_id or None,
        mission_id=mission_id or None,
        event_type=event_type or None,
        severity=severity or None,
        limit=limit,
    )
    return {"alerts": events, "count": len(events)}


@app.get("/alerts/{alert_id}")
async def get_alert_detail(alert_id: str):
    """Return full detail for a single anomaly alert."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT event_id::text, drone_id, mission_id, track_id,
               event_type, severity, class_name,
               confidence_detector, confidence_rule, confidence_event, confidence_alert,
               evidence_groups, reasons, summary, snapshot_key, bbox,
               owner_track_id, owner_status, temporal, gps_lat, gps_lon, details, ts
        FROM anomaly_events
        WHERE event_id = $1::uuid
        """,
        alert_id
    )
    if not row:
        raise HTTPException(status_code=404, detail="Anomaly alert not found")
    return dict(row)


@app.get("/stats/summary")
async def get_stats_summary(
    drone_id: str = Query(""),
    mission_id: str = Query(""),
):
    """Return summary statistics for the Anomaly Monitor KPI banner."""
    pool = await get_pool()
    stats = await get_anomaly_summary_stats(
        pool,
        drone_id=drone_id or None,
        mission_id=mission_id or None,
    )
    return stats


@app.get("/stats/by-type")
async def get_stats_by_type(
    drone_id: str = Query(""),
    mission_id: str = Query(""),
):
    """Return breakdown of alerts by anomaly event type."""
    pool = await get_pool()
    breakdown = await get_anomaly_stats_by_type(
        pool,
        drone_id=drone_id or None,
        mission_id=mission_id or None,
    )
    return {"breakdown": breakdown}


@app.get("/config")
async def get_config():
    """Return current threshold configuration."""
    return _runtime_config


@app.put("/config")
async def update_config(body: Dict[str, Any] = Body(...)):
    """Update runtime threshold parameters across all active analyzers."""
    for key, val in body.items():
        if key in _runtime_config:
            _runtime_config[key] = val

    # Propagate to active analyzers
    for analyzer in _analyzers.values():
        if "conf_thresh" in body:
            analyzer.detector.conf_thresh = float(body["conf_thresh"])
        if "cooldown_seconds" in body:
            analyzer.rules_engine.cooldown_seconds = float(body["cooldown_seconds"])
            analyzer.alert_manager.cooldown_seconds = float(body["cooldown_seconds"])
        if "hysteresis_enter" in body:
            analyzer.rules_engine.hysteresis_enter = float(body["hysteresis_enter"])
        if "loitering_duration_sec" in body:
            analyzer.rules_engine.loiter_dwell_thresh_sec = float(body["loitering_duration_sec"])

    log.info("Anomaly configuration updated: %s", body)
    return {"status": "updated", "config": _runtime_config}
