# -*- coding: utf-8 -*-
"""
DVAS Maritime Vessel Analytics Service — FastAPI on port 8106.
End-to-end maritime vessel perception, tracking, telemetry-free analytics,
MinIO thumbnail persistence, and Redis stream publishing.
"""

import asyncio
import json as _json
import logging
import math
import os
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

sys.path.insert(0, "/analytics")
sys.path.insert(0, "/analytics/vessel")

from shared.auth import install_service_guard
from shared.config import cfg, get_drone_aliases
from shared.db import (
    close_pool, get_pool, ensure_drone,
    upsert_vessel_track, write_vessel_event,
    get_active_vessel_tracks, get_vessel_class_stats,
    get_vessel_events, get_vessel_summary_stats
)
from shared.redis_client import (
    close_client, get_client, publish_analytics_result,
    scan_active_stream_drones, get_latest_stream_frame
)
from shared import minio_client as minio
from shared import gpu_profile
from shared.annotated_hub import AnnotatedHub, install_stream_route, to_display
from shared.frame_sampler import FrameSampler, decode_frame, frame_bytes as _frame_jpeg
from shared.db import write_confirmed_event
from shared.evidence import evidence_block, frame_context, model_info
from shared.redis_client import publish_confirmed_event

# SeaDrone Perception Engine & Taxonomy
try:
    from seadrone.engine import MaritimePerceptionEngine
    from seadrone.taxonomy import VESSEL_CLASSES, VESSEL_PALETTE
except ImportError:
    from vessel.seadrone.engine import MaritimePerceptionEngine
    from vessel.seadrone.taxonomy import VESSEL_CLASSES, VESSEL_PALETTE

logging.basicConfig(
    level=cfg.LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("vessel-service")

app = FastAPI(
    title="DVAS Maritime Vessel Analyzer",
    description="Maritime vessel detection, classification, tracking, and telemetry-free trajectory analytics",
    version="1.0.0",
)
# Internal service: no browser calls it (no CORS); every route but /health needs the key (M1)
install_service_guard(app)

# ── Configuration Constants ───────────────────────────────────────────────────
VESSEL_MODEL_TYPE = os.getenv("VESSEL_MODEL_TYPE", "yolov8").lower()
VESSEL_MODEL_PATH = os.getenv(
    "VESSEL_MODEL_PATH",
    "/analytics/vessel/models/yolov8s.pt"
    if os.path.exists("/analytics/vessel/models/yolov8s.pt")
    else "D:/POLICE_DRONE/dvas_analytics/vessel/models/yolov8s.pt"
    if os.path.exists("D:/POLICE_DRONE/dvas_analytics/vessel/models/yolov8s.pt")
    else "models/yolov8s.pt"
)
BUCKET_VESSELS = getattr(cfg, "BUCKET_VESSELS_MARITIME", "dvas-vessels-maritime")
CONF_THRESH = getattr(cfg, "VESSEL_CONFIDENCE", 0.50)
PERSON_CONF_THRESH = getattr(cfg, "VESSEL_PERSON_CONFIDENCE", 0.20)
STATIONARY_THRESH_PX = getattr(cfg, "VESSEL_STATIONARY_THRESHOLD_PX", 6.0)
PROXIMITY_THRESH_PX = getattr(cfg, "VESSEL_PROXIMITY_THRESHOLD_PX", 90.0)

# ── Service State ─────────────────────────────────────────────────────────────
engine: Optional[object] = None

_stream_task: Optional[asyncio.Task] = None
_stream_running = False
_frames_processed = 0
# Newest-frame reader, governor cap and live fps stats (RunPod plan 9.2 / 10)
_sampler = FrameSampler("vessel", "vessel-group", "vessel-analyzer")
# Live annotated video (MJPEG, ≤1920 px) for the dashboard
_hub = AnnotatedHub("vessel")
install_stream_route(app, _hub)

# In-memory per-drone telemetry state
_last_annotated: Dict[str, bytes] = {}
_last_annotated_ts: Dict[str, float] = {}
_last_vessel_counts: Dict[str, int] = defaultdict(int)
_fps_tracker: Dict[str, deque] = defaultdict(lambda: deque(maxlen=20))

# Track history for motion/direction/clustering: {drone_id: {tracker_id: deque([(cx, cy, ts)])}}
_track_trajectories: Dict[str, Dict[int, deque]] = defaultdict(lambda: defaultdict(lambda: deque(maxlen=60)))
_known_track_thumbnails: Dict[str, set] = defaultdict(set)
_heatmap_points: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))

# Proximity cooldown: {(drone_id, trk1, trk2): last_alert_ts}
_proximity_cooldown: Dict[Tuple[str, int, int], float] = {}
_ensured_drones: set = set()


# ── Motion & Analytics Helpers ────────────────────────────────────────────────

def _classify_size(w: int, h: int) -> str:
    """Classify vessel size bucket based on pixel area."""
    area = w * h
    if area < 7500:
        return "Small"
    elif area < 38000:
        return "Medium"
    return "Large"


def _analyze_trajectory(traj: deque) -> Tuple[str, bool]:
    """
    Analyze centroid history to deduce:
    - relative travel direction
    - stationary / anchored status
    """
    if len(traj) < 5:
        return "Acquiring Trajectory", False

    pts = np.array(list(traj), dtype=np.float32)
    # Total spatial dispersion
    std_disp = np.std(pts[:, :2], axis=0)
    total_spread = float(np.linalg.norm(std_disp))

    if total_spread < STATIONARY_THRESH_PX:
        return "Stationary / Anchored", True

    dx = float(pts[-1, 0] - pts[0, 0])
    dy = float(pts[-1, 1] - pts[0, 1])

    # Direction heuristic relative to camera frame
    if abs(dx) > 1.8 * abs(dy):
        direction = "Moving Left → Right" if dx > 0 else "Moving Right → Left"
    elif abs(dy) > 1.8 * abs(dx):
        direction = "Moving Toward Camera" if dy > 0 else "Moving Away from Camera"
    else:
        h_dir = "Right" if dx > 0 else "Left"
        v_dir = "Toward" if dy > 0 else "Away"
        direction = f"Moving Diagonally ({v_dir}-{h_dir})"

    return direction, False


def _check_proximity_clusters(drone_id: str, objects: list) -> List[Tuple[dict, dict, float]]:
    """Detect pairs of vessels in close proximity (< threshold px)."""
    now = time.monotonic()
    alerts = []
    n = len(objects)
    for i in range(n):
        for j in range(i + 1, n):
            o1, o2 = objects[i], objects[j]
            box1, box2 = o1["bbox_xywh"], o2["bbox_xywh"]
            c1 = (box1[0] + box1[2] / 2.0, box1[1] + box1[3] / 2.0)
            c2 = (box2[0] + box2[2] / 2.0, box2[1] + box2[3] / 2.0)
            dist = math.hypot(c1[0] - c2[0], c1[1] - c2[1])

            if dist < PROXIMITY_THRESH_PX:
                t1, t2 = min(o1["track_id"], o2["track_id"]), max(o1["track_id"], o2["track_id"])
                key = (drone_id, t1, t2)
                if now - _proximity_cooldown.get(key, 0.0) > 30.0:
                    _proximity_cooldown[key] = now
                    alerts.append((o1, o2, dist))
    return alerts


def _draw_hud_overlay(annotated: np.ndarray, drone_id: str, count: int, fps: float) -> bytes:
    """Draw tactical maritime HUD bar on the ≤1920 px live-view copy and encode to JPEG bytes."""
    annotated, _ = to_display(annotated)
    h, w = annotated.shape[:2]

    # Top tactical HUD banner
    banner_h = 36
    cv2.rectangle(annotated, (0, 0), (w, banner_h), (12, 18, 28), -1)
    cv2.line(annotated, (0, banner_h), (w, banner_h), (0, 200, 255), 1)

    hud_text = f"MARITIME DEFENSE RADAR | DRONE: {drone_id} | VESSELS: {count} | FPS: {fps:.1f}"
    cv2.putText(annotated, hud_text, (14, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1, cv2.LINE_AA)

    ts_text = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    (tw, _), _ = cv2.getTextSize(ts_text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
    cv2.putText(annotated, ts_text, (w - tw - 14, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 255), 1, cv2.LINE_AA)

    # Encode JPEG with quality 85
    success, buffer = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buffer.tobytes() if success else b""


# ── Background Stream Processing Loop ─────────────────────────────────────────

async def _persist_vessel_frame(drone_id: str, f: dict, frame: np.ndarray,
                                annotated_frame: np.ndarray, tracked_objects: list,
                                engine_ms: float) -> None:
    """Telemetry, DB/MinIO persistence, alerts, HUD and publish for one analysed frame.

    Runs as a task while the GPU processes the next round (per-drone order kept).
    """
    global _frames_processed
    if drone_id not in _ensured_drones:
        try:
            await ensure_drone(drone_id)
            _ensured_drones.add(drone_id)
        except Exception as ex:
            log.warning("Failed to ensure drone %s: %s", drone_id, ex)

    pool = await get_pool()
    frame_ts = datetime.now(timezone.utc)

    # Extract mission_id from metadata if present
    raw_meta = f.get("metadata", "")
    frame_mission_id = None
    if raw_meta:
        if isinstance(raw_meta, bytes):
            raw_meta = raw_meta.decode()
        try:
            frame_mission_id = _json.loads(raw_meta).get("mission_id")
        except Exception:
            pass

    # Calculate FPS (engine time of this frame; a batched round is amortised over its frames)
    proc_time = max(engine_ms / 1000.0, 0.001)
    fps = 1.0 / proc_time
    _fps_tracker[drone_id].append(fps)
    avg_fps = sum(_fps_tracker[drone_id]) / len(_fps_tracker[drone_id])

    # 2. Enrich & Update Per-Track Telemetry
    enriched_objects = []
    for obj in tracked_objects:
        tid = obj["track_id"]
        bx, by, bw, bh = obj["bbox_xywh"]
        cx, cy = bx + bw / 2.0, by + bh / 2.0

        # Record trajectory history
        traj = _track_trajectories[drone_id][tid]
        traj.append((cx, cy, time.time()))
        direction, is_stationary = _analyze_trajectory(traj)
        size_bucket = _classify_size(bw, bh)

        # Accumulate heatmap points
        _heatmap_points[drone_id].append((round(cx, 1), round(cy, 1)))

        # Auto-capture thumbnail for new track
        thumb_key = None
        if tid not in _known_track_thumbnails[drone_id]:
            _known_track_thumbnails[drone_id].add(tid)
            try:
                crop_x1, crop_y1 = max(0, bx), max(0, by)
                crop_x2, crop_y2 = min(frame.shape[1], bx + bw), min(frame.shape[0], by + bh)
                if crop_x2 > crop_x1 and crop_y2 > crop_y1:
                    crop = frame[crop_y1:crop_y2, crop_x1:crop_x2]
                    ok, crop_buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    if ok:
                        ts_ms = int(frame_ts.timestamp() * 1000)
                        t_key = f"vessels/{drone_id}/v_{tid}_{ts_ms}.jpg"
                        thumb_key = await minio.upload_bytes(
                            BUCKET_VESSELS, t_key, crop_buf.tobytes(), "image/jpeg"
                        )
            except Exception as ex:
                log.debug("Thumbnail capture error: %s", ex)

            # Record new track event
            try:
                await write_vessel_event(
                    pool=pool,
                    drone_id=drone_id,
                    mission_id=frame_mission_id,
                    tracker_id=tid,
                    event_type="vessel_detected",
                    class_name=obj["class_name"],
                    confidence=obj["confidence"],
                    snapshot_key=thumb_key,
                    details={"size": size_bucket, "direction": direction}
                )
            except Exception as ex:
                log.warning("Error writing vessel event: %s", ex)

        # Persist updated track state to Postgres
        try:
            traj_coords = [[round(p[0], 1), round(p[1], 1)] for p in list(traj)[-20:]]
            await upsert_vessel_track(
                pool=pool,
                drone_id=drone_id,
                mission_id=frame_mission_id,
                tracker_id=tid,
                class_id=obj["class_id"],
                class_name=obj["class_name"],
                confidence=obj["confidence"],
                bbox_xyxy=obj["bbox_xyxy"],
                size_bucket=size_bucket,
                direction=direction,
                is_stationary=is_stationary,
                trajectory=traj_coords,
                thumbnail_key=thumb_key,
            )
        except Exception as ex:
            log.warning("Error upserting vessel track: %s", ex)

        enriched_objects.append({
            **obj,
            "direction": direction,
            "is_stationary": is_stationary,
            "size_bucket": size_bucket,
        })

    # 3. Proximity Clustering Alert Detection
    # (cooldown 30 s per pair inside _check_proximity_clusters)
    proximity_pairs = _check_proximity_clusters(drone_id, tracked_objects)
    for (o1, o2, dist) in proximity_pairs:
        try:
            # Evidence: the source frame, hashed, uploaded before the event
            snap_obj = await minio.upload_object(
                BUCKET_VESSELS,
                minio.make_key("vessels", drone_id,
                               f"proximity_{o1['track_id']}_{o2['track_id']}.jpg",
                               mission_id=frame_mission_id or ""),
                _frame_jpeg(f), "image/jpeg", kind="source_frame",
            )
            details = {
                "target_1": o1["track_id"],
                "target_2": o2["track_id"],
                "distance_px": round(dist, 1),
                "message": f"Convergence detected ({round(dist, 0)}px separation)"
            }
            vessel_event_id = await write_vessel_event(
                pool=pool,
                drone_id=drone_id,
                mission_id=frame_mission_id,
                tracker_id=o1["track_id"],
                event_type="proximity_alert",
                class_name=f"{o1['class_name']} & {o2['class_name']}",
                confidence=min(o1["confidence"], o2["confidence"]),
                snapshot_key=snap_obj["key"] if snap_obj else None,
                details=details,
            )
            if vessel_event_id:
                # Confirmed event (B28), same id as vessel_events
                ctx = frame_context(f)
                geo = ctx["geo"] or {}
                await write_confirmed_event(
                    drone_id=drone_id,
                    event_type="vessel_proximity",
                    confidence=min(o1["confidence"], o2["confidence"]),
                    ts=frame_ts,
                    attributes={
                        **details,
                        "classes": [o1["class_name"], o2["class_name"]],
                        "mission_id": frame_mission_id,
                        "source_table": "vessel_events",
                        "evidence": evidence_block(
                            [snap_obj], ctx,
                            detection={"track_id": o1["track_id"], "class": o1["class_name"],
                                       "bbox_xyxy": o1.get("bbox_xyxy"),
                                       "confidence": o1["confidence"]},
                            model=model_info("vessel", VESSEL_MODEL_PATH),
                        ),
                    },
                    mission_id=frame_mission_id,
                    latitude=geo.get("lat"),
                    longitude=geo.get("lon"),
                    event_id=vessel_event_id,
                )
                await publish_confirmed_event({
                    "event_id": vessel_event_id,
                    "event_type": "vessel_proximity",
                    "drone_id": drone_id,
                    "mission_id": frame_mission_id,
                    "ts": frame_ts.isoformat(),
                    **details,
                })
        except Exception as ex:
            log.warning("Proximity alert error: %s", ex)

    # 4. Render & Cache HUD Frame for Live Dashboard
    vessel_count = len(tracked_objects)
    annotated_jpeg = await asyncio.to_thread(_draw_hud_overlay, annotated_frame, drone_id, vessel_count, avg_fps)
    if annotated_jpeg:
        _hub.put(drone_id, annotated_jpeg)
        _last_annotated[drone_id] = annotated_jpeg
        _last_annotated_ts[drone_id] = time.monotonic()
        _last_vessel_counts[drone_id] = vessel_count

    # 5. Publish to Redis Analytics Stream for Gateway SSE
    await publish_analytics_result("vessel", {
        "drone_id": drone_id,
        "mission_id": frame_mission_id,
        "vessel_count": vessel_count,
        "vessels": enriched_objects,
        "fps": round(avg_fps, 1),
        "ts": frame_ts.isoformat(),
    })

    _frames_processed += 1


def _analyze_round(frames: Dict[str, np.ndarray]) -> Tuple[Dict[str, tuple], float, float]:
    """GPU work of one round (worker thread): ({drone: (annotated, tracked)}, model ms/frame, total ms/frame)."""
    t0 = time.perf_counter()
    if hasattr(engine, "process_batch"):
        results, model_ms = engine.process_batch(frames)
    else:
        results = {d: engine.process_frame(fr, drone_id=d) for d, fr in frames.items()}
        model_ms = None
    total_ms = (time.perf_counter() - t0) * 1000 / max(1, len(frames))
    return results, model_ms, total_ms


async def _persist_round(items: list) -> None:
    for drone_id, f, frame, (annotated_frame, tracked_objects), model_ms, engine_ms, t_start in items:
        try:
            await _persist_vessel_frame(drone_id, f, frame, annotated_frame, tracked_objects, engine_ms)
        except Exception as exc:
            log.warning("Vessel persist error for %s: %s", drone_id, exc)
        _sampler.record(drone_id, infer_ms=model_ms,
                        total_ms=(time.perf_counter() - t_start) * 1000)


async def _stream_loop():
    global _stream_running, _ensured_drones
    active_drones: List[str] = []
    log.info("Maritime vessel stream loop started (dynamic ingest stream discovery mode, GPU %s)",
             gpu_profile.detect().as_dict())

    loop_count = 0
    pending: Optional[asyncio.Task] = None
    try:
        while _stream_running:
            loop_count += 1
            # Discovery: every 50 rounds (rounds are now frame-paced, not 0.5 s-paced)
            if loop_count % 50 == 1 or not active_drones:
                try:
                    discovered = await scan_active_stream_drones()
                    for d in discovered:
                        if d not in active_drones:
                            active_drones.append(d)
                            log.info("Discovered active ingest stream for vessel analysis: %s", d)
                        if d not in _ensured_drones:
                            try:
                                await ensure_drone(d)
                                _ensured_drones.add(d)
                            except Exception as ex:
                                log.warning("Failed to ensure drone %s in drones table: %s", d, ex)
                except Exception as e:
                    log.debug("Stream scan error: %s", e)

            try:
                await _sampler.maybe_publish()
                # Newest frame per drone (older ones acked + counted as skipped); blocks
                # briefly when nothing is new, so no fixed sleep is needed here.
                round_frames = await _sampler.next_round(active_drones)
                if not round_frames:
                    continue
                t_start = time.perf_counter()
                decoded = {}
                for drone_id, f in round_frames.items():
                    frame = await asyncio.to_thread(decode_frame, f)
                    if frame is not None:
                        decoded[drone_id] = (f, frame)
                if not decoded:
                    continue

                # 1. Run Maritime Perception & Tracking Engine (global pass batched across drones)
                results, model_ms, eng_ms = await asyncio.to_thread(
                    _analyze_round, {d: fr for d, (_, fr) in decoded.items()}
                )
                _sampler.record_batch(len(decoded))
                # Persist the previous round before queuing this one (keeps per-drone order);
                # it ran concurrently with the inference above.
                if pending:
                    await asyncio.shield(pending)   # a stream stop must not cut a round half-written
                pending = asyncio.create_task(_persist_round(
                    [(d, f, fr, results[d], model_ms, eng_ms, t_start) for d, (f, fr) in decoded.items()]
                ))
            except asyncio.CancelledError:
                log.info("Maritime vessel stream cancelled")
                raise
            except Exception as e:
                err_str = str(e)
                if "CUDA" in err_str:
                    log.warning("CUDA error in vessel stream loop — falling back engine to CPU: %s", e)
                    if engine:
                        engine._fallback_to_cpu()
                else:
                    log.error("Maritime stream loop error: %s", e, exc_info=True)
                await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        pass
    finally:
        if pending and not pending.done():
            try:
                await asyncio.shield(pending)   # a stream stop must not cut a round half-written
            except BaseException:
                pass


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    global engine, _stream_task, _stream_running
    log.info("Initializing SeaDrone YOLOv8s Maritime Perception Engine with weights: %s", VESSEL_MODEL_PATH)
    gpu_profile.apply_torch_settings()      # FP16 / cuDNN autotune for the detected GPU
    engine = MaritimePerceptionEngine(
        weights_path=VESSEL_MODEL_PATH,
        global_imgsz=1280,
        enable_sahi=False,
        conf_thresh=CONF_THRESH,
        person_conf=PERSON_CONF_THRESH,
    )
    await get_pool()
    _stream_running = True
    _stream_task = asyncio.create_task(_stream_loop())
    log.info("Maritime vessel analyzer service ready on port 8106")


@app.on_event("shutdown")
async def shutdown():
    global _stream_running, _stream_task
    _stream_running = False
    if _stream_task:
        _stream_task.cancel()
        try:
            await _stream_task
        except asyncio.CancelledError:
            pass
    await close_pool()
    await close_client()
    log.info("Maritime vessel analyzer service shut down cleanly")


# ── REST API Endpoints ────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "vessel-analyzer",
        "model": "SeaDrone YOLOv8s",
        "stream_running": _stream_running,
        "frames_processed": _frames_processed,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/stream/start")
async def start_stream():
    global _stream_running, _stream_task
    import torch
    if engine and hasattr(engine, 'to_device') and torch.cuda.is_available():
        try:
            engine.to_device("cuda:0")
        except Exception as e:
            log.warning("Failed to move vessel engine to CUDA, staying on CPU: %s", e)
    if not _stream_running:
        _stream_running = True
        _stream_task = asyncio.create_task(_stream_loop())
    return {"status": "started", "device": engine.device if engine else "cpu", "stream_running": True}


@app.get("/stream/stop")
async def stop_stream():
    global _stream_running, _stream_task
    _stream_running = False
    if _stream_task:
        _stream_task.cancel()
        _stream_task = None

    if not cfg.VRAM_OFFLOAD_ON_STOP:
        # RunPod pod: keep models resident on the GPU (no reload stall; required for TensorRT)
        log.info("Vessel stream loop stopped (models kept on GPU, VRAM_OFFLOAD_ON_STOP=false)")
        return {"status": "stopped", "device": "gpu", "stream_running": False}
    if engine and hasattr(engine, 'to_device'):
        engine.to_device("cpu")
    import gc, torch
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()
    return {"status": "stopped", "device": "cpu", "stream_running": False}


FRAME_CACHE_TTL_SEC = 6.0


@app.get("/annotated/latest")
async def get_latest_annotated_frame(drone_id: str = ""):
    """Serve the latest annotated maritime video frame with tactical headers.
    
    If in-memory cache exists and is fresh, returns it immediately.
    If not in memory, attempts on-demand frame recovery from Redis Valkey stream
    (drone:frames:{drone_id}), runs perception and HUD drawing, caches and returns.
    If feed offline or expired, returns 404 so frontend two-layer viewer can display
    the raw ingest camera feed seamlessly.
    """
    now = time.monotonic()
    frame_bytes = None
    matched_drone_id = None
    frame_age = 0.0
    vessel_cnt = 0

    if drone_id:
        aliases = get_drone_aliases(drone_id)
        for alias in aliases:
            if alias in _last_annotated:
                frame_bytes = _last_annotated[alias]
                matched_drone_id = alias
                frame_age = now - _last_annotated_ts.get(alias, 0.0)
                vessel_cnt = _last_vessel_counts.get(alias, 0)
                break
    elif _last_annotated:
        best_drone = max(_last_annotated_ts, key=_last_annotated_ts.get, default=None)
        if best_drone:
            frame_bytes = _last_annotated.get(best_drone)
            matched_drone_id = best_drone
            frame_age = now - _last_annotated_ts.get(best_drone, 0.0)
            vessel_cnt = _last_vessel_counts.get(best_drone, 0)

    # On-demand frame recovery from Redis stream if not in memory or stale
    if (not frame_bytes or frame_age > FRAME_CACHE_TTL_SEC) and drone_id:
        aliases = get_drone_aliases(drone_id)
        for alias in aliases:
            recovered_frame = await get_latest_stream_frame(alias)
            if recovered_frame is not None and engine is not None:
                try:
                    annotated_frame, tracked_objects = await asyncio.to_thread(
                        engine.process_frame, recovered_frame, alias
                    )
                    vessel_cnt = len(tracked_objects)
                    annotated_jpeg = _draw_hud_overlay(annotated_frame, alias, vessel_cnt, 15.0)
                    if annotated_jpeg:
                        _last_annotated[alias] = annotated_jpeg
                        _last_annotated_ts[alias] = now
                        _last_vessel_counts[alias] = vessel_cnt
                        frame_bytes = annotated_jpeg
                        matched_drone_id = alias
                        frame_age = 0.0
                        break
                except Exception as exc:
                    log.warning("Failed to recover and annotate maritime vessel frame for %s: %s", alias, exc)

    if not frame_bytes:
        raise HTTPException(404, "No live annotated maritime frame available")

    is_live = frame_age <= FRAME_CACHE_TTL_SEC
    feed_status = "live" if is_live else "offline"

    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Access-Control-Expose-Headers": "X-Drone-ID, X-Frame-Age, X-Vessel-Count, X-Feed-Status, X-Stream-Status",
        "X-Drone-ID": matched_drone_id or drone_id,
        "X-Frame-Age": f"{frame_age:.2f}s",
        "X-Vessel-Count": str(vessel_cnt),
        "X-Feed-Status": feed_status,
        "X-Stream-Status": feed_status,
    }
    return Response(content=frame_bytes, media_type="image/jpeg", headers=headers)


@app.get("/tracks/active")
async def list_active_tracks(drone_id: str = "", mission_id: str = "", limit: int = 100, window: int = 60):
    """List active vessel tracks from PostgreSQL."""
    pool = await get_pool()
    tracks = await get_active_vessel_tracks(
        pool, drone_id=drone_id or None, mission_id=mission_id or None, limit=limit, active_window_minutes=window
    )
    # Serialize datetime and JSON objects
    for t in tracks:
        for k, v in t.items():
            if hasattr(v, "isoformat"):
                t[k] = v.isoformat()
            elif isinstance(v, (bytes, memoryview)):
                t[k] = str(v)
    return {"tracks": tracks, "count": len(tracks)}


@app.get("/stats/summary")
async def get_summary_stats(drone_id: str = "", mission_id: str = ""):
    """Get live and cumulative maritime vessel metrics."""
    pool = await get_pool()
    stats = await get_vessel_summary_stats(
        pool, drone_id=drone_id or None, mission_id=mission_id or None
    )
    return stats


@app.get("/stats/by-class")
async def get_stats_by_class(drone_id: str = "", mission_id: str = ""):
    """Get vessel count breakdown by fine-grained taxonomy class."""
    pool = await get_pool()
    data = await get_vessel_class_stats(
        pool, drone_id=drone_id or None, mission_id=mission_id or None
    )
    return {"classes": data, "count": len(data)}


@app.get("/events/recent")
async def list_recent_events(
    drone_id: str = "", mission_id: str = "", event_type: str = "", limit: int = 50
):
    """Retrieve recent maritime alerts and track inception events."""
    pool = await get_pool()
    events = await get_vessel_events(
        pool,
        drone_id=drone_id or None,
        mission_id=mission_id or None,
        event_type=event_type or None,
        limit=limit,
    )
    for e in events:
        for k, v in e.items():
            if hasattr(v, "isoformat"):
                e[k] = v.isoformat()
    return {"events": events, "count": len(events)}


@app.get("/heatmap/data")
async def get_heatmap_points(drone_id: str = ""):
    """Retrieve detection centroid density coordinates for maritime canvas heatmap."""
    points = list(_heatmap_points.get(drone_id, [])) if drone_id else [
        pt for pts in _heatmap_points.values() for pt in pts
    ]
    return {"points": points[-500:], "count": len(points)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8106, reload=False)
