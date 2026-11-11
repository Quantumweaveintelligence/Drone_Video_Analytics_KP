# -*- coding: utf-8 -*-
"""
Intrusion Detection Analyzer Service — FastAPI on port 8105.

Consumes drone video frames from Redis, runs YOLOv8s detection + ByteTrack
tracking, evaluates per-track intrusion states against a geofence polygon,
annotates frames, and publishes results via Redis streams.
"""
import asyncio
import json as _json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
import uuid

import cv2
import gc
import numpy as np
import torch
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from ultralytics import YOLO

sys.path.insert(0, "/analytics")
from shared.auth import install_service_guard
from shared.config import cfg, get_drone_aliases
from shared.db import close_pool, ensure_drone, get_pool, write_confirmed_event
from shared.redis_client import (
    close_client, publish_analytics_result, publish_confirmed_event,
    scan_active_stream_drones, get_client, get_latest_stream_frame,
)
from shared import minio_client as minio
from shared import gpu_profile
from shared.batch_tracker import BatchTracker
from shared.annotated_hub import AnnotatedHub, install_stream_route
from shared.frame_sampler import FrameSampler, frame_bytes as _frame_jpeg
from shared.evidence import EventGate, bbox_xyxy, evidence_block, frame_context, model_info

from intrusion.projection import CameraProjection
from intrusion.state_machine import IntrusionState, IntrusionStateMachine
from intrusion.geofence import WGS84Polygon, GeofenceManager
from intrusion.annotator import IntrusionAnnotator
from intrusion.schema import (
    IntrusionConfig, IntrusionFrameResult, IntrusionDetection,
    GeofenceStatus, MonitoringMode, TelemetryHUD, GeofenceOverlay, BBox,
)

logging.basicConfig(
    level=cfg.LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("intrusion-service")

app = FastAPI(
    title="DVAS Intrusion Analyzer",
    description="Geofence intrusion detection via YOLOv8s + temporal state machine",
    version="1.0.0",
)
# Internal service: no browser calls it (no CORS); every route but /health needs the key (M1)
install_service_guard(app)

INTRUSION_MODEL = os.getenv(
    "INTRUSION_MODEL",
    "/analytics/intrusion/models/geotrax_hbb_yolov8s_1920_v1.pt",
)
INTRUSION_DEFAULT_PRESET = os.getenv("INTRUSION_DEFAULT_PRESET", "default")

# YOLOv8s geotrax class mapping (fallback)
CLASS_NAMES: Dict[int, str] = {
    0: "CAR", 1: "BUS", 2: "TRUCK", 3: "MOTORCYCLE",
    4: "PEOPLE", 5: "BICYCLE",
}


def resolve_model_class_names(model) -> Dict[int, str]:
    """Resolve class names from YOLO model weights with pedestrian/people normalization."""
    fallback = {0: "CAR", 1: "BUS", 2: "TRUCK", 3: "MOTORCYCLE", 4: "PEOPLE", 5: "BICYCLE"}
    if model and hasattr(model, "names") and model.names:
        cleaned = {}
        for k, v in model.names.items():
            name_str = str(v).upper()
            if name_str in ("PEDESTRIAN", "PEDESTRIANS", "PERSON", "PEOPLE"):
                cleaned[int(k)] = "PEOPLE"
            else:
                cleaned[int(k)] = name_str
        log.info("Resolved class mapping from model weights: %s", cleaned)
        return cleaned
    return fallback

# ── Service state ──────────────────────────────────────────────────────────────
yolo_model = None
# Batched predict + one tracker per drone over yolo_model (recreate whenever yolo_model is reassigned)
_tracker: Optional[BatchTracker] = None
_infer_device = "cuda:0" if torch.cuda.is_available() else "cpu"
# Newest-frame reader, governor cap and live fps stats (RunPod plan 9.2 / 10)
_sampler = FrameSampler("intrusion", "intrusion-group", "intrusion-analyzer")
# Live annotated video (MJPEG, ≤1920 px) for the dashboard
_hub = AnnotatedHub("intrusion")
install_stream_route(app, _hub, path="/intrusion/annotated/stream")
# One intrusion_breach confirmed event per track per zone per 60 s (evidence plan E1)
_breach_gate = EventGate(cooldown_s=60.0)
_stream_task: Optional[asyncio.Task] = None
_stream_running = False
_frames_processed = 0

# Per-drone in-memory state
_last_annotated_frames: Dict[str, bytes] = {}
_last_annotated_ts: Dict[str, float] = {}
_active_drone_missions: Dict[str, Optional[str]] = {}
_geofence_managers: Dict[str, GeofenceManager] = {}
_state_machines: Dict[str, Optional[IntrusionStateMachine]] = {}
_cameras: Dict[str, CameraProjection] = {}
_configs: Dict[str, IntrusionConfig] = {}
_live_detections: Dict[str, List[dict]] = {}  # {drone_id: [detection dicts]}


def _load_preset(preset: str) -> IntrusionConfig:
    """Load a YAML preset into IntrusionConfig."""
    path = f"/analytics/intrusion/cfg/{preset}.yaml"
    if not os.path.exists(path):
        log.warning("Config preset '%s' not found, using defaults", preset)
        return IntrusionConfig(preset=preset)
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
        # Map YAML keys to IntrusionConfig fields
        return IntrusionConfig(
            preset=preset,
            min_confidence=data.get("ultralytics", {}).get("conf", 0.25),
            motorcycle_min_confidence=data.get("extraction", {}).get("motorcycle_min_conf", 0.75),
            imgsz=data.get("ultralytics", {}).get("imgsz", 1920),
            confirm_frames=cfg.INTRUSION_CONFIRM_FRAMES,
            dwell_threshold_s=cfg.INTRUSION_DWELL_THRESHOLD_S,
        )
    except Exception as e:
        log.error("Error loading preset '%s': %s", preset, e)
        return IntrusionConfig(preset=preset)


# ── Startup / Shutdown ─────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    global yolo_model, _tracker, _stream_running, _stream_task

    # Database + MinIO
    await get_pool()
    for drone_id in cfg.DRONE_IDS:
        await ensure_drone(drone_id)
    if hasattr(minio, "ensure_all_buckets"):
        await minio.ensure_all_buckets()

    # Per-drone state
    default_config = _load_preset(INTRUSION_DEFAULT_PRESET)
    for drone_id in cfg.DRONE_IDS:
        _geofence_managers[drone_id] = GeofenceManager()
        _state_machines[drone_id] = None  # Created when geofence is loaded
        _cameras[drone_id] = CameraProjection(3840, 2160, focal_len_mm=24.0)
        _configs[drone_id] = default_config
        _live_detections[drone_id] = []

    # Load YOLOv8s model
    gpu_profile.apply_torch_settings()      # FP16 / cuDNN autotune for the detected GPU
    log.info("Loading YOLO model: %s", INTRUSION_MODEL)
    yolo_model = YOLO(INTRUSION_MODEL)
    # Default tracker (botsort.yaml), same as the previous yolo_model.track() call
    _tracker = BatchTracker(yolo_model)
    try:
        _imgsz = int(next(iter(_configs.values())).imgsz) if _configs else 1920
    except Exception:
        _imgsz = 1920
    log.info("Intrusion warm-up %.1fs (imgsz %d)",
             await asyncio.to_thread(_tracker.warmup, _imgsz, device=_infer_device), _imgsz)
    global CLASS_NAMES
    CLASS_NAMES = resolve_model_class_names(yolo_model)
    log.info("Intrusion Analyzer ready with class mapping: %s", CLASS_NAMES)

    # Auto-start stream consumer
    _stream_running = True
    _stream_task = asyncio.create_task(_stream_loop())
    log.info("Stream loop auto-started for drones: %s", cfg.DRONE_IDS)


@app.on_event("shutdown")
async def shutdown():
    global _stream_running, _stream_task
    _stream_running = False
    if _stream_task:
        _stream_task.cancel()
    await close_pool()
    await close_client()


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    active_geofences = {
        d: gm.is_loaded() for d, gm in _geofence_managers.items()
    }
    return {
        "status":            "ok",
        "service":           "intrusion-analyzer",
        "model_loaded":      yolo_model is not None,
        "stream_running":    _stream_running,
        "frames_processed":  _frames_processed,
        "active_missions":   dict(_active_drone_missions),
        "active_geofences":  active_geofences,
    }


# ── YOLO inference (runs in thread pool) ───────────────────────────────────────

def _run_yolo_track(frame: np.ndarray, conf: float = 0.25, imgsz: int = 1920,
                    key: str = "upload:adhoc"):
    """Single-frame tracking for non-stream callers. Returns raw results (1-item list).

    Uses its own tracker key so it never disturbs a live drone's tracks.
    """
    if yolo_model is None or _tracker is None:
        return []
    return _tracker.track_one(key, frame, conf=conf, imgsz=imgsz, device=_infer_device)


def _infer_round(groups: Dict[Tuple[float, int], Dict[str, np.ndarray]]):
    """Worker thread: one batched track call per (conf, imgsz) group.

    Touches only the model / BatchTracker; all per-drone state (state machines,
    geofences, cameras) is updated later on the event loop in _persist_round.
    Returns ({drone: Results}, {drone: model ms per frame}, [error strings]).
    """
    results: Dict[str, object] = {}
    infer_ms: Dict[str, float] = {}
    errors: List[str] = []
    if _tracker is None:
        return results, infer_ms, errors
    for (conf, imgsz), frames in groups.items():
        t0 = time.perf_counter()
        try:
            results.update(_tracker.track_batch(frames, conf=conf, imgsz=imgsz, device=_infer_device))
        except Exception as yolo_err:
            # Same as before: a failed inference leaves the frame without detections
            log.warning("YOLO tracking error for %s: %s", ",".join(frames), yolo_err)
            errors.append(str(yolo_err))
        per_frame = (time.perf_counter() - t0) * 1000 / max(1, len(frames))
        for d in frames:
            infer_ms[d] = per_frame
    return results, infer_ms, errors


# ── Stream consumer loop ──────────────────────────────────────────────────────

def _init_drone_state(d: str) -> None:
    _geofence_managers[d] = GeofenceManager()
    _state_machines[d] = None
    _cameras[d] = CameraProjection(3840, 2160, focal_len_mm=24.0)
    _configs[d] = _load_preset(INTRUSION_DEFAULT_PRESET)
    _live_detections[d] = []


async def _prepare_frame(drone_id: str, f: dict, frame: np.ndarray, redis) -> Optional[dict]:
    """Metadata + geofence/state-machine checks for one frame, before inference.

    Returns a work item: mode "standby" (no geofence → HUD only, no inference),
    mode "monitor" (needs inference), or None (nothing to do for this frame).
    """
    # ── Extract metadata ──────────────────────────────
    mission_id = None
    d_lat, d_lon, d_alt = 0.0, 0.0, 50.0
    g_yaw, g_pitch, g_roll = 0.0, -90.0, 0.0
    focal_len = 24.0

    raw_meta = f.get("metadata", "")
    if raw_meta:
        try:
            meta = _json.loads(raw_meta)
            mission_id = meta.get("mission_id")

            # Robust telemetry parsing: support flat keys (edge_sender) and nested dicts
            gps_dict = meta.get("gps") if isinstance(meta.get("gps"), dict) else {}
            d_lat = float(meta.get("latitude") or meta.get("lat") or gps_dict.get("lat") or gps_dict.get("latitude") or 0.0)
            d_lon = float(meta.get("longitude") or meta.get("lon") or gps_dict.get("lon") or gps_dict.get("longitude") or 0.0)
            d_alt = float(meta.get("altitude_m") or meta.get("alt") or gps_dict.get("alt") or gps_dict.get("altitude") or 50.0)

            gimbal_dict = meta.get("gimbal") if isinstance(meta.get("gimbal"), dict) else {}
            g_yaw = float(meta.get("gb_yaw") or meta.get("gimbal_yaw") or gimbal_dict.get("yaw") or 0.0)
            g_pitch = float(meta.get("gb_pitch") or meta.get("gimbal_pitch") or gimbal_dict.get("pitch") or -90.0)
            g_roll = float(meta.get("gb_roll") or meta.get("gimbal_roll") or gimbal_dict.get("roll") or 0.0)

            focal_len = float(meta.get("focal_len") or meta.get("focal_length") or 24.0)
        except (_json.JSONDecodeError, TypeError, ValueError):
            pass

    _active_drone_missions[drone_id] = mission_id

    # ── Geofence check ────────────────────────────────
    gm = _geofence_managers[drone_id]
    geofence_changed = False
    # Check Redis on every frame until armed; once armed, poll every 30 frames for mid-flight updates
    if not gm.is_loaded() or (_frames_processed % 30 == 0):
        geofence_changed = await gm.load_from_redis(redis, drone_id)
        if not gm.is_loaded() and mission_id:
            geofence_changed = await gm.load_from_redis(redis, mission_id) or geofence_changed
        if not gm.is_loaded():
            for cand_did, cand_gm in _geofence_managers.items():
                if cand_gm and cand_gm.is_loaded():
                    gm.polygon = cand_gm.polygon
                    gm._raw_hash = cand_gm._raw_hash
                    geofence_changed = True
                    break

    # Fallback to reference metadata if live frame GPS is missing (0.0)
    if gm.is_loaded() and gm.polygon and (d_lat == 0.0 or d_lon == 0.0):
        ref = getattr(gm.polygon, "reference_metadata", {})
        if ref:
            try:
                d_lat = float(ref.get("initial_drone_lat") or d_lat)
                d_lon = float(ref.get("initial_drone_lon") or d_lon)
                d_alt = float(ref.get("initial_drone_alt_m") or d_alt)
            except (ValueError, TypeError):
                pass

    item = {
        "drone_id": drone_id, "f": f, "frame": frame, "mission_id": mission_id,
        "d_lat": d_lat, "d_lon": d_lon, "d_alt": d_alt,
        "g_yaw": g_yaw, "g_pitch": g_pitch, "g_roll": g_roll, "focal_len": focal_len,
        "gm": gm,
    }

    if not gm.is_loaded():
        # Geofence is not loaded yet (standby mode): HUD-only frame, no inference
        item["mode"] = "standby"
        return item

    # If geofence changed, rebuild state machine
    if geofence_changed and gm.polygon is not None:
        ic = _configs[drone_id]
        coords_lonlat = list(gm.polygon.coordinates[:-1])  # Exclude closing vertex
        _state_machines[drone_id] = IntrusionStateMachine(
            polygon_coords_lonlat=coords_lonlat,
            confirm_inside_frames=ic.confirm_frames,
            confirm_outside_frames=ic.confirm_frames,
            min_dwell_time_s=ic.dwell_threshold_s,
        )
        log.info("State machine rebuilt for %s (geofence %s)",
                 drone_id, gm.polygon.zone_id)

    sm = _state_machines[drone_id]
    if sm is None and gm.polygon is not None:
        ic = _configs[drone_id]
        coords_lonlat = list(gm.polygon.coordinates[:-1])
        _state_machines[drone_id] = IntrusionStateMachine(
            polygon_coords_lonlat=coords_lonlat,
            confirm_inside_frames=ic.confirm_frames,
            confirm_outside_frames=ic.confirm_frames,
            min_dwell_time_s=ic.dwell_threshold_s,
        )
        sm = _state_machines[drone_id]

    if sm is None:
        return None

    # Snapshot the config / state machine this frame is evaluated with (a later
    # config POST or geofence change only affects the following frames)
    item.update(mode="monitor", sm=sm, ic=_configs[drone_id], ts=datetime.now(timezone.utc))
    return item


def _encode_full_jpeg(img) -> bytes:
    """Full-resolution annotated evidence frame (only encoded when an event needs it)."""
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return bytes(buf)


async def _persist_standby(item: dict, annotator: IntrusionAnnotator) -> None:
    """Standby frame: annotate with tactical HUD and store so live video is immediately available."""
    global _frames_processed
    drone_id, frame, mission_id = item["drone_id"], item["frame"], item["mission_id"]
    annotated = frame.copy()
    ts = datetime.now(timezone.utc)
    ts_s = ts.timestamp()
    telemetry_dict = {
        "frame_idx": _frames_processed,
        "frame_timestamp_s": ts_s,
        "latitude": item["d_lat"],
        "longitude": item["d_lon"],
        "rel_alt_m": item["d_alt"],
        "gimbal_yaw_deg": item["g_yaw"],
        "gimbal_pitch_deg": item["g_pitch"],
        "gimbal_roll_deg": item["g_roll"],
    }
    annotator.draw_hud(
        annotated, telemetry_dict, polygon=None, active_intrusions_count=0,
    )

    # Live view only (no evidence on standby): ≤1920 px display copy
    jpeg_bytes = await _hub.put_frame(drone_id, annotated)
    _last_annotated_frames[drone_id] = jpeg_bytes
    _last_annotated_ts[drone_id] = time.monotonic()
    for alias in get_drone_aliases(drone_id):
        _last_annotated_frames[alias] = jpeg_bytes
        _last_annotated_ts[alias] = time.monotonic()
    _live_detections[drone_id] = []
    _frames_processed += 1

    if _frames_processed % 10 == 0:
        result_payload = {
            "drone_id": drone_id,
            "mission_id": mission_id,
            "ts": ts.isoformat(),
            "frame_sequence": _frames_processed,
            "detections": [],
            "active_intrusions": 0,
            "monitoring_mode": "STANDBY",
            "geofence_zone_id": None,
        }
        try:
            await publish_analytics_result("intrusion", result_payload)
        except Exception:
            pass


async def _persist_monitor(item: dict, results, annotator: IntrusionAnnotator) -> None:
    """Monitored frame: state machine, annotation, DB + MinIO evidence, publish."""
    global _frames_processed
    drone_id, f, frame = item["drone_id"], item["f"], item["frame"]
    mission_id = item["mission_id"]
    d_lat, d_lon, d_alt = item["d_lat"], item["d_lon"], item["d_alt"]
    g_yaw, g_pitch, g_roll = item["g_yaw"], item["g_pitch"], item["g_roll"]
    focal_len = item["focal_len"]
    gm, sm, ic, ts = item["gm"], item["sm"], item["ic"], item["ts"]
    ts_s = ts.timestamp()
    frame_seq = _frames_processed
    # The state machine runs on capture time (v2 R6.5): confirmations and dwell measure the
    # scene, not when this frame happened to be processed (lag, catch-up, shedding).
    try:
        cap_s = float(f.get("capture_ts") or 0) or ts_s
    except (TypeError, ValueError):
        cap_s = ts_s

    # Update camera intrinsics if focal length or resolution changed (here, in
    # frame order, so the frame being projected uses its own intrinsics)
    cam = _cameras[drone_id]
    cam.update_resolution(frame.shape[1], frame.shape[0])
    if abs(focal_len - 24.0) > 0.01:
        cam.update_focal_length(focal_len)

    # ── Process detections ────────────────────────────
    frame_detections: List[dict] = []

    if results and len(results) > 0 and results[0].boxes is not None:
        boxes = results[0].boxes
        for box in boxes:
            cls_id = int(box.cls[0].cpu().numpy())
            conf = float(box.conf[0].cpu().numpy())
            cls_name = CLASS_NAMES.get(cls_id, f"CLASS_{cls_id}").upper()

            # Normalise pedestrian class names
            if cls_name in ("PEDESTRIAN", "PEDESTRIANS", "PERSON"):
                cls_name = "PEOPLE"

            # Skip excluded classes
            if cls_name in ic.excluded_classes:
                continue

            # Motorcycle high-confidence filter
            if cls_name == "MOTORCYCLE" and conf < ic.motorcycle_min_confidence:
                continue

            # General confidence filter
            if conf < ic.min_confidence:
                continue

            track_id = int(box.id[0].cpu().numpy()) if box.id is not None else -1
            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].cpu().numpy().tolist()]

            # Ground contact: bottom-center of bbox
            gc_px = (x1 + x2) / 2.0
            gc_py = float(y2)

            # Project to WGS84
            g_lat, g_lon, _, _ = cam.pixel_to_ground(
                px=gc_px, py=gc_py,
                drone_lat=d_lat, drone_lon=d_lon,
                drone_rel_alt=d_alt,
                gimbal_yaw=g_yaw, gimbal_pitch=g_pitch,
                gimbal_roll=g_roll,
            )
            g_lat, g_lon = float(g_lat), float(g_lon)

            # Evaluate intrusion state
            intrusion_state = IntrusionState.OUTSIDE
            is_intruding = False
            dwell_s = 0.0
            prev_state = IntrusionState.OUTSIDE

            if track_id > 0:
                state, is_inside, is_boundary = sm.process_track_observation(
                    track_id=track_id,
                    lat=g_lat, lon=g_lon,
                    timestamp_s=cap_s,
                    frame_idx=frame_seq,
                )
                intrusion_state = state
                is_intruding = state in (
                    IntrusionState.ENTERING,
                    IntrusionState.INSIDE,
                    IntrusionState.DWELLING,
                )
                ts_obj = sm.track_states.get(track_id)
                if ts_obj:
                    dwell_s = ts_obj.dwell_duration_s
                    prev_state = ts_obj.current_state

            det = {
                "track_id": track_id,
                "class": cls_name,
                "confidence": round(conf, 3),
                "bbox": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                "ground_contact": [int(gc_px), int(gc_py)],
                "geolocation": {"latitude": g_lat, "longitude": g_lon},
                "intrusion_state": intrusion_state.value,
                "is_intruding": is_intruding,
                "dwell_duration_s": round(dwell_s, 2),
            }
            frame_detections.append(det)

    # ── Annotate frame (CPU work off the event loop) ───
    def _annotate() -> bytes:
        annotated = frame.copy()

        # Draw geofence overlay
        pixel_pts, vis_flags = gm.project_to_frame(
            camera=cam,
            drone_lat=d_lat, drone_lon=d_lon, drone_alt=d_alt,
            gimbal_yaw=g_yaw, gimbal_pitch=g_pitch,
            gimbal_roll=g_roll,
        )
        annotator.draw_geofence(annotated, pixel_pts, vis_flags, gm.polygon)

        # Draw objects
        annotator.draw_objects(annotated, frame_detections)

        # Draw HUD
        telemetry_dict = {
            "frame_idx": frame_seq,
            "frame_timestamp_s": ts_s,
            "latitude": d_lat,
            "longitude": d_lon,
            "rel_alt_m": d_alt,
            "gimbal_yaw_deg": g_yaw,
            "gimbal_pitch_deg": g_pitch,
            "gimbal_roll_deg": g_roll,
        }
        annotator.draw_hud(
            annotated, telemetry_dict, gm.polygon, active_intrusions,
        )

        return annotated

    active_intrusions = sm.get_active_intrusion_count()
    annotated_img = await asyncio.to_thread(_annotate)
    # Live view: ≤1920 px copy. Evidence below is encoded from the full-resolution image.
    jpeg_bytes = await _hub.put_frame(drone_id, annotated_img)
    _last_annotated_frames[drone_id] = jpeg_bytes
    _last_annotated_ts[drone_id] = time.monotonic()
    for alias in get_drone_aliases(drone_id):
        _last_annotated_frames[alias] = jpeg_bytes
        _last_annotated_ts[alias] = time.monotonic()

    # Update live detections buffer
    _live_detections[drone_id] = frame_detections

    # ── Process state transitions → DB + MinIO ────────
    try:
        pool = await get_pool()
        for det in frame_detections:
            tid = det["track_id"]
            if tid <= 0:
                continue
            ts_obj = sm.track_states.get(tid)
            if ts_obj is None:
                continue

            # Check if state changed relative to last notified state
            cur_state_str = str(det.get("intrusion_state", "OUTSIDE"))
            prev_state_str = getattr(ts_obj, "last_notified_state", "OUTSIDE")
            if cur_state_str == prev_state_str:
                continue

            ts_obj.last_notified_state = cur_state_str

            # State transition detected!
            log.info(
                "Track #%d %s: %s → %s",
                tid, det["class"], prev_state_str, cur_state_str,
            )

            # Q1: Upload JPEG snapshot on ENTERING/DWELLING (source frame + annotated),
            # hashed, before any event is written
            snapshot_key = None
            evidence_objects = []
            zone_ref = str(gm.zone_id) if gm.zone_id else "RESTRICTED_ZONE_01"
            if cur_state_str in ("ENTERING", "DWELLING"):
                ts_ms = int(ts_s * 1000)
                s_key = f"intrusion/{drone_id}/{mission_id}/event_{tid}_{ts_ms}.jpg"
                src_obj = await minio.upload_object(
                    cfg.BUCKET_INTRUSION,
                    f"intrusion/{drone_id}/{mission_id}/event_{tid}_{ts_ms}_source.jpg",
                    _frame_jpeg(f), "image/jpeg", kind="source_frame",
                )
                full_jpeg = await asyncio.to_thread(_encode_full_jpeg, annotated_img)
                ann_obj = await minio.upload_object(
                    cfg.BUCKET_INTRUSION, s_key, full_jpeg, "image/jpeg",
                    kind="annotated_frame",
                )
                snapshot_key = ann_obj["key"] if ann_obj else None
                evidence_objects = [src_obj, ann_obj]

            # Write to intrusion_events table
            intrusion_event_id = None
            try:
                await ensure_drone(drone_id)
                intrusion_event_id = await pool.fetchval(
                    """INSERT INTO intrusion_events (
                        drone_id, mission_id, zone_id, track_id,
                        track_class, state_from, state_to,
                        confidence, latitude, longitude,
                        entry_ts, exit_ts, dwell_duration_s,
                        trajectory, snapshot_key, frame_sequence, ts
                    ) VALUES (
                        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,
                        $11,$12,$13,$14,$15,$16,$17
                    ) RETURNING event_id::text""",
                    drone_id,
                    str(mission_id) if mission_id else None,
                    str(gm.zone_id) if gm.zone_id else "RESTRICTED_ZONE_01",
                    int(tid),
                    str(det["class"]),
                    str(prev_state_str),
                    str(cur_state_str),
                    float(det["confidence"]),
                    float(det["geolocation"]["latitude"]),
                    float(det["geolocation"]["longitude"]),
                    (datetime.fromtimestamp(ts_obj.entry_timestamp_s, tz=timezone.utc)
                     if ts_obj.entry_timestamp_s else None),
                    (datetime.fromtimestamp(ts_obj.exit_timestamp_s, tz=timezone.utc)
                     if ts_obj.exit_timestamp_s else None),
                    float(ts_obj.dwell_duration_s),
                    _json.dumps(ts_obj.trajectory[-10:], default=str),  # Safe float/datetime serialization
                    snapshot_key,
                    int(frame_seq),
                    ts,
                )
            except Exception as db_err:
                log.warning("DB insert error: %s", db_err)

            # Confirmed event (B28): ENTERING / DWELLING, one per track per zone per 60 s,
            # same id as the intrusion_events row and the Valkey message
            confirmed_id = None
            if (intrusion_event_id and cur_state_str in ("ENTERING", "DWELLING")
                    and _breach_gate.allow((drone_id, int(tid), zone_ref))):
                try:
                    target_geo = {"lat": float(det["geolocation"]["latitude"]),
                                  "lon": float(det["geolocation"]["longitude"]),
                                  "alt_m": None, "heading_deg": None}
                    confirmed_id = await write_confirmed_event(
                        drone_id=drone_id,
                        event_type="intrusion_breach",
                        confidence=float(det["confidence"]),
                        ts=ts,
                        attributes={
                            "zone_id": zone_ref,
                            "tracker_id": int(tid),
                            "track_class": str(det["class"]),
                            "state_from": prev_state_str,
                            "state_to": cur_state_str,
                            "dwell_duration_s": float(ts_obj.dwell_duration_s),
                            "mission_id": str(mission_id) if mission_id else None,
                            "source_table": "intrusion_events",
                            "evidence": evidence_block(
                                evidence_objects, frame_context(f),
                                detection={"track_id": int(tid), "class": str(det["class"]),
                                           "bbox_xyxy": bbox_xyxy(det.get("bbox")),
                                           "confidence": float(det["confidence"])},
                                model=model_info("intrusion", INTRUSION_MODEL),
                                geo=target_geo,
                            ),
                        },
                        mission_id=str(mission_id) if mission_id else None,
                        zone_ref=zone_ref,
                        latitude=target_geo["lat"],
                        longitude=target_geo["lon"],
                        event_id=intrusion_event_id,
                    )
                except Exception as ce_err:
                    log.warning("Confirmed intrusion event write failed: %s", ce_err)

            # Publish on intrusion transitions (live feed); confirmed ones carry the real id
            if cur_state_str in ("ENTERING", "INSIDE", "DWELLING", "EXITED"):
                try:
                    await publish_confirmed_event({
                        "event_id": confirmed_id or str(uuid.uuid4()),
                        "confirmed": bool(confirmed_id),
                        "event_type": "intrusion_alert",
                        "drone_id": drone_id,
                        "mission_id": str(mission_id) if mission_id else "",
                        "zone_id": str(gm.zone_id) if gm.zone_id else "RESTRICTED_ZONE_01",
                        "track_id": int(tid),
                        "track_class": det["class"],
                        "state": cur_state_str,
                        "state_from": prev_state_str,
                        "state_to": cur_state_str,
                        "confidence": float(det["confidence"]),
                        "dwell_duration_s": float(ts_obj.dwell_duration_s),
                        "latitude": float(det["geolocation"]["latitude"]),
                        "longitude": float(det["geolocation"]["longitude"]),
                        "snapshot_key": snapshot_key,
                        "frame_sequence": int(frame_seq),
                        "ts": ts.isoformat(),
                    })
                except Exception as pub_err:
                    log.warning("Failed to publish confirmed event: %s", pub_err)

    except Exception as ev_err:
        log.warning("Event processing error: %s", ev_err)

    # ── Publish analytics result to SSE stream ─────────
    active_breaches = [
        d for d in frame_detections
        if d.get("is_intruding") or (d.get("intrusion_state") in ("ENTERING", "INSIDE", "DWELLING"))
    ]
    result_payload = {
        "drone_id": drone_id,
        "mission_id": str(mission_id) if mission_id else "",
        "ts": ts.isoformat(),
        "frame_sequence": int(frame_seq),
        "detections": frame_detections,
        "active_breaches": active_breaches,
        "active_intrusions": sm.get_active_intrusion_count(),
        "monitoring_mode": "MONITORING",
        "geofence_zone_id": gm.zone_id,
    }
    try:
        await publish_analytics_result("intrusion", result_payload)
    except Exception as pub_err:
        log.warning("Failed to publish result: %s", pub_err)

    _frames_processed += 1


async def _persist_round(items: List[dict], results: Dict[str, object],
                         infer_ms: Dict[str, float], annotator: IntrusionAnnotator,
                         t_start: float) -> None:
    """Per-frame handling of one round, in drone order (overlaps the next round's inference)."""
    for item in items:
        drone_id = item["drone_id"]
        try:
            if item["mode"] == "standby":
                await _persist_standby(item, annotator)
            else:
                r = results.get(drone_id)
                await _persist_monitor(item, [r] if r is not None else None, annotator)
        except Exception as exc:
            log.warning("Intrusion persist error for %s: %s", drone_id, exc)
        _sampler.record(drone_id, infer_ms=infer_ms.get(drone_id),
                        total_ms=(time.perf_counter() - t_start) * 1000)


def _move_model(device: str) -> None:
    """Move the YOLO model under the BatchTracker lock (never during a running batch)."""
    global _infer_device
    if _tracker is not None:
        _tracker.to(device)
    else:
        yolo_model.to(device)
    # Explicit device on every predict, or Ultralytics would pick cuda:0 again
    _infer_device = "cpu" if device == "cpu" else device


def _cpu_fallback(reason: str) -> None:
    if yolo_model is None:
        return
    log.warning("CUDA error in intrusion stream — falling back to CPU: %s", reason)
    try:
        _move_model("cpu")
    except Exception:
        pass


async def _stream_loop():
    """Background task: newest frame of every drone → one batched detection → intrusion logic."""
    global _stream_running

    active_drones = list(cfg.DRONE_IDS)
    loop_count = 0
    redis = await get_client()
    annotator = IntrusionAnnotator()
    log.info("Intrusion stream loop started for drones: %s (GPU %s)",
             cfg.DRONE_IDS, gpu_profile.detect().as_dict())

    pending: Optional[asyncio.Task] = None
    try:
        while _stream_running:
            loop_count += 1

            # Periodically discover new drones
            if loop_count % 50 == 1:
                try:
                    discovered = await scan_active_stream_drones()
                    for d in discovered:
                        if d not in active_drones:
                            active_drones.append(d)
                            await ensure_drone(d)
                            _init_drone_state(d)
                except Exception as exc:
                    log.warning("Drone scanner warning: %s", exc)

            try:
                await _sampler.maybe_publish()
                # Decoded in parallel; the next round decodes while this one is on the GPU (B2)
                round_frames = await _sampler.next_decoded(active_drones)
                if not round_frames:
                    continue
                t_start = time.perf_counter()

                items: List[dict] = []
                for drone_id, (f, frame) in round_frames.items():
                    if drone_id not in _geofence_managers:     # configured drone missing state
                        _init_drone_state(drone_id)
                    item = await _prepare_frame(drone_id, f, frame, redis)
                    if item is not None:
                        items.append(item)
                if not items:
                    continue

                # Only monitored drones need the detector; group by per-drone (conf, imgsz)
                groups: Dict[Tuple[float, int], Dict[str, np.ndarray]] = {}
                for item in items:
                    if item["mode"] == "monitor":
                        ic = item["ic"]
                        groups.setdefault((ic.min_confidence, ic.imgsz), {})[item["drone_id"]] = item["frame"]

                results: Dict[str, object] = {}
                infer_ms: Dict[str, float] = {}
                if groups:
                    results, infer_ms, errors = await asyncio.to_thread(_infer_round, groups)
                    _sampler.record_batch(sum(len(g) for g in groups.values()))
                    cuda_err = next((e for e in errors if "CUDA" in e), None)
                    if cuda_err:
                        _cpu_fallback(cuda_err)

                # Persist the previous round before queuing this one (keeps per-drone order);
                # it ran concurrently with the inference above.
                if pending:
                    await asyncio.shield(pending)   # a stream stop must not cut a round half-written
                pending = asyncio.create_task(
                    _persist_round(items, results, infer_ms, annotator, t_start)
                )

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                err_str = str(exc)
                if "CUDA" in err_str:
                    _cpu_fallback(err_str)
                else:
                    log.warning("Intrusion stream error: %s", exc)
                await asyncio.sleep(1)
    finally:
        if pending and not pending.done():
            try:
                await asyncio.shield(pending)   # a stream stop must not cut a round half-written
            except Exception:
                pass
        try:
            await _sampler.done()           # the last round is finished: acknowledge it
        except Exception:
            pass


# ── Stream control ─────────────────────────────────────────────────────────────

@app.get("/stream/start")
async def stream_start():
    global _stream_running, _stream_task, yolo_model
    # Allocate model to CUDA if available
    if yolo_model is not None and torch.cuda.is_available():
        try:
            await asyncio.to_thread(_move_model, "cuda:0")
            log.info("Intrusion YOLO model moved to CUDA:0")
        except Exception as e:
            log.warning("Failed to move Intrusion model to CUDA: %s", e)

    if _stream_running:
        return {"status": "already_running", "device": "cuda:0" if torch.cuda.is_available() else "cpu"}
    _stream_running = True
    _stream_task = asyncio.create_task(_stream_loop())
    log.info("Intrusion stream loop started")
    return {"status": "started", "device": "cuda:0" if torch.cuda.is_available() else "cpu"}


@app.get("/stream/stop")
async def stream_stop():
    global _stream_running, _stream_task, yolo_model
    _stream_running = False
    if _stream_task:
        _stream_task.cancel()
        _stream_task = None

    if not cfg.VRAM_OFFLOAD_ON_STOP:
        # RunPod pod: keep models resident on the GPU (no reload stall; required for TensorRT)
        log.info("Intrusion stream loop stopped (models kept on GPU, VRAM_OFFLOAD_ON_STOP=false)")
        return {"status": "stopped", "device": "gpu"}
    # Release model from GPU VRAM to CPU and empty CUDA cache
    if yolo_model is not None:
        try:
            await asyncio.to_thread(_move_model, "cpu")
            log.info("Intrusion YOLO model offloaded to CPU")
        except Exception as e:
            log.warning("Failed to offload Intrusion model to CPU: %s", e)
    gc.collect()
    if torch.cuda.is_available():
        try:
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
        except Exception as e:
            log.warning("Failed to clear CUDA cache (non-fatal): %s", e)
    log.info("Intrusion stream stopped and GPU VRAM released")
    return {"status": "stopped", "device": "cpu"}


# ── Live intrusion tracks ─────────────────────────────────────────────────────

@app.get("/intrusion/live")
async def intrusion_live(drone_id: str = ""):
    """Current active tracks and their intrusion states."""
    if drone_id:
        aliases = get_drone_aliases(drone_id)
        for alias in aliases:
            if alias in _live_detections:
                return {
                    "drone_id": alias,
                    "detections": _live_detections[alias],
                    "count": len(_live_detections[alias]),
                }
    # Return first available
    for did, dets in _live_detections.items():
        if dets:
            return {"drone_id": did, "detections": dets, "count": len(dets)}
    return {"drone_id": drone_id, "detections": [], "count": 0}


# ── Latest annotated frame ────────────────────────────────────────────────────

@app.get("/intrusion/annotated/latest")
async def annotated_latest(drone_id: str = ""):
    """Return the latest annotated JPEG frame with geofence + bounding boxes for requested drone."""
    now = time.monotonic()
    frame_bytes = None
    matched_drone_id = None
    frame_age = 0.0

    if drone_id:
        aliases = get_drone_aliases(drone_id)
        for alias in aliases:
            if alias in _last_annotated_frames:
                frame_bytes = _last_annotated_frames[alias]
                matched_drone_id = alias
                frame_age = now - _last_annotated_ts.get(alias, now)
                break

    # If no fresh annotated frame (<3.0s) in memory, recover latest raw frame from Redis on demand
    if (not frame_bytes or frame_age > 3.0) and drone_id:
        raw_bgr = await get_latest_stream_frame(drone_id)
        if raw_bgr is not None:
            try:
                ann_frame = raw_bgr.copy()
                gm = _geofence_managers.get(drone_id)
                cam = _cameras.get(drone_id)
                if gm and gm.is_loaded() and cam:
                    poly = gm.polygon
                    d_lat, d_lon = (poly.coordinates[0][1], poly.coordinates[0][0]) if (poly and poly.coordinates) else (0.0, 0.0)
                    pixel_pts, vis_flags = gm.project_to_frame(
                        camera=cam,
                        drone_lat=d_lat, drone_lon=d_lon, drone_alt=50.0,
                        gimbal_yaw=0.0, gimbal_pitch=-45.0, gimbal_roll=0.0,
                    )
                    # The stream loop's annotator is local to it; this recovery path draws its own
                    annotator = IntrusionAnnotator()
                    annotator.draw_geofence(ann_frame, pixel_pts, vis_flags, gm.polygon)
                    sm = _state_machines.get(drone_id)
                    active_intrusions = sm.get_active_intrusion_count() if sm else 0
                    annotator.draw_hud(
                        ann_frame,
                        {"frame_idx": _frames_processed, "latitude": d_lat, "longitude": d_lon, "rel_alt_m": 50.0},
                        gm.polygon,
                        active_intrusions,
                    )
                _, ann_buf = cv2.imencode(".jpg", ann_frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                frame_bytes = bytes(ann_buf)
                matched_drone_id = drone_id
                frame_age = 0.1
                _last_annotated_frames[drone_id] = frame_bytes
                _last_annotated_ts[drone_id] = now
            except Exception as e:
                log.warning("On-demand annotation failed for %s: %s", drone_id, e)

    # Strictly isolated: if drone_id was requested, NEVER fall back to another drone!
    if not frame_bytes and not drone_id and _last_annotated_frames:
        best = max(_last_annotated_ts, key=_last_annotated_ts.get, default=None)
        if best:
            frame_bytes = _last_annotated_frames.get(best)
            matched_drone_id = best
            frame_age = now - _last_annotated_ts.get(best, now)

    if not frame_bytes:
        raise HTTPException(404, f"No live annotated frame available for {drone_id or 'any drone'}")

    return Response(
        content=frame_bytes,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "X-Drone-ID": matched_drone_id or drone_id,
            "X-Frame-Age": f"{frame_age:.2f}s",
        },
    )


# ── Zone status ────────────────────────────────────────────────────────────────

@app.get("/intrusion/zone/status")
async def zone_status(drone_id: str = ""):
    """Active geofence zone status."""
    gm = _geofence_managers.get(drone_id)
    if drone_id and (not gm or not gm.is_loaded()):
        try:
            redis = await get_client()
            if not gm:
                gm = GeofenceManager()
                _geofence_managers[drone_id] = gm
            await gm.load_from_redis(redis, drone_id)
        except Exception:
            pass

    if (not gm or not gm.is_loaded()) and drone_id:
        for alias in get_drone_aliases(drone_id):
            cand = _geofence_managers.get(alias)
            if cand and cand.is_loaded():
                gm = cand
                break

    # If still not loaded, resolve to any currently active armed geofence manager
    if not gm or not gm.is_loaded():
        for cand_id, cand_gm in _geofence_managers.items():
            if cand_gm and cand_gm.is_loaded():
                gm = cand_gm
                break

    if gm and gm.is_loaded():
        sm = _state_machines.get(drone_id)
        if not sm and drone_id:
            for alias in get_drone_aliases(drone_id):
                cand_sm = _state_machines.get(alias)
                if cand_sm:
                    sm = cand_sm
                    break
        active = sm.get_active_intrusion_count() if sm else 0
        return GeofenceStatus(
            zone_id=gm.zone_id,
            name=gm.zone_name,
            vertex_count=gm.vertex_count,
            monitoring_state=MonitoringMode.MONITORING,
            is_loaded=True,
            coordinates=[[lon, lat] for lon, lat in gm.polygon.coordinates]
                        if gm.polygon else None,
        )
    return GeofenceStatus()


# ── Config update ──────────────────────────────────────────────────────────────

@app.get("/intrusion/config")
async def get_config(drone_id: str = ""):
    """Current configuration of a drone (the defaults when it has none yet)."""
    cfg = _configs.get(drone_id) if drone_id else None
    return {"drone_id": drone_id or None, "saved": cfg is not None,
            "config": (cfg or IntrusionConfig()).dict()}


@app.post("/intrusion/config")
async def update_config(request: Request, drone_id: str = ""):
    """Update intrusion detection runtime configuration."""
    body = await request.json()
    config = IntrusionConfig(**body)

    if drone_id:
        _configs[drone_id] = config
    else:
        for d in _configs:
            _configs[d] = config

    # Rebuild state machine with new thresholds if geofence is loaded
    for d, c in _configs.items():
        if c == config:
            gm = _geofence_managers.get(d)
            if gm and gm.is_loaded() and gm.polygon:
                coords = list(gm.polygon.coordinates[:-1])
                _state_machines[d] = IntrusionStateMachine(
                    polygon_coords_lonlat=coords,
                    confirm_inside_frames=config.confirm_frames,
                    confirm_outside_frames=config.confirm_frames,
                    min_dwell_time_s=config.dwell_threshold_s,
                )

    return {"status": "updated", "config": config.dict()}


# ── Historical events (DB queries) ────────────────────────────────────────────

@app.get("/intrusion/events")
async def get_events(
    drone_id: str = "",
    mission_id: Optional[str] = None,
    limit: int = 50,
):
    """Query intrusion events from the database."""
    pool = await get_pool()
    if mission_id and drone_id:
        rows = await pool.fetch(
            "SELECT * FROM intrusion_events "
            "WHERE drone_id = $1 AND mission_id::text = $2 "
            "ORDER BY ts DESC LIMIT $3",
            drone_id, mission_id, limit,
        )
    elif mission_id:
        rows = await pool.fetch(
            "SELECT * FROM intrusion_events "
            "WHERE mission_id::text = $1 "
            "ORDER BY ts DESC LIMIT $2",
            mission_id, limit,
        )
    elif drone_id:
        rows = await pool.fetch(
            "SELECT * FROM intrusion_events "
            "WHERE drone_id = $1 ORDER BY ts DESC LIMIT $2",
            drone_id, limit,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM intrusion_events ORDER BY ts DESC LIMIT $1",
            limit,
        )

    events = []
    for r in rows:
        event = dict(r)
        # Serialize datetime and UUID fields for JSON
        for k, v in event.items():
            if isinstance(v, datetime):
                event[k] = v.isoformat()
            elif isinstance(v, uuid.UUID):
                event[k] = str(v)
            elif isinstance(v, (float, int)):
                event[k] = v
        events.append(event)

    return {"events": events, "count": len(events)}


# ── Mission analytics ─────────────────────────────────────────────────────────

@app.get("/intrusion/analytics")
async def get_analytics(mission_id: str):
    """Full analytics JSON for a completed mission."""
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM intrusion_events "
        "WHERE mission_id::text = $1 ORDER BY ts ASC",
        mission_id,
    )

    events = []
    track_ids = set()
    class_counts: Dict[str, int] = {}
    for r in rows:
        event = dict(r)
        for k, v in event.items():
            if isinstance(v, datetime):
                event[k] = v.isoformat()
            elif isinstance(v, uuid.UUID):
                event[k] = str(v)
            elif isinstance(v, (float, int)):
                event[k] = v
        events.append(event)
        track_ids.add(event.get("track_id"))
        cls = event.get("track_class", "UNKNOWN")
        class_counts[cls] = class_counts.get(cls, 0) + 1

    # Summary stats
    dwell_times = [
        float(e.get("dwell_duration_s") or 0.0) for e in events
        if e.get("dwell_duration_s") and e.get("state_to") in ("DWELLING", "EXITED")
    ]

    return {
        "schema_version": "1.0",
        "mission_id": mission_id,
        "total_events": len(events),
        "unique_tracks": len(track_ids),
        "detections_by_class": class_counts,
        "avg_dwell_time_s": round(sum(dwell_times) / len(dwell_times), 2) if dwell_times else 0.0,
        "events": events,
    }


# ── Intrusion Missions Summary ───────────────────────────────────────────────

@app.get("/intrusion/missions")
async def get_intrusion_missions():
    """List missions with intrusion event counts and details."""
    pool = await get_pool()
    rows = await pool.fetch("""
        SELECT 
            mission_id,
            COUNT(*)::int as event_count,
            COUNT(DISTINCT track_id)::int as unique_tracks,
            MIN(ts) as first_event,
            MAX(ts) as last_event,
            array_agg(DISTINCT track_class) as breach_classes
        FROM intrusion_events
        WHERE mission_id IS NOT NULL AND mission_id != ''
        GROUP BY mission_id
        ORDER BY MAX(ts) DESC
    """)
    missions = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("first_event"), datetime):
            d["first_event"] = d["first_event"].isoformat()
        if isinstance(d.get("last_event"), datetime):
            d["last_event"] = d["last_event"].isoformat()
        missions.append(d)
    return {"missions": missions, "count": len(missions)}
