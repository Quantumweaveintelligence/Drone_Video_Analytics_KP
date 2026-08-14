# -*- coding: utf-8 -*-
"""
ANPR Analyzer Service — FastAPI on port 8104.
License plate recognition, watchlist management, theft alerts.
Stores annotated frames (with plate bounding boxes + OCR text) in memory for dashboard.
"""
import asyncio
import base64
import io
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional, Dict

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, "/analytics")
from shared.config import cfg
from shared.db import (
    close_pool, ensure_drone, get_pool, upsert_track,
    write_confirmed_event, write_plate_read,
    get_recent_plates, get_plates_by_mission, add_to_watchlist, remove_from_watchlist,
    get_watchlist, is_watchlisted, write_vehicle_plate_link,
)
from shared.redis_client import (
    close_client, publish_analytics_result, publish_confirmed_event,
    xread_frames, hget_vehicle_track, hset_vehicle_track,
)
from shared import minio_client as minio
from anpr.analyzer import ANPRAnalyzer
from anpr.schema import ANPRFrameResult, WatchlistEntry, TheftAlert

logging.basicConfig(
    level=cfg.LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("anpr-service")

app = FastAPI(
    title="DVAS ANPR Analyzer",
    description="License plate recognition, watchlist management, theft alerts",
    version="2.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

VEHICLE_MODEL = os.getenv("VEHICLE_MODEL", "/analytics/anpr/models/yolov8m.pt")
LP_MODEL      = os.getenv("LP_MODEL",      "/analytics/anpr/models/license_plate_detector.pt")

analyzer: Optional[ANPRAnalyzer] = None
_stream_task: Optional[asyncio.Task] = None
_stream_running = False
_frames_processed = 0

# ── Annotated frame cache ─────────────────────────────────────────────────────
# Stores latest JPEG with plate bboxes + OCR text drawn, per drone_id
_last_annotated: Dict[str, bytes] = {}
_last_annotated_ts: Dict[str, float] = {}

# Most recently detected plate info per drone for the ANPR window
_recent_plate_cache: Dict[str, dict] = {}  # drone_id -> {plate_text, vehicle_image_b64, ...}


def _draw_anpr_annotations(frame: np.ndarray, plate_reads: list, drone_id: str) -> bytes:
    """
    Draw vehicle bounding boxes + green plate bounding boxes with OCR text on frame.
    Returns JPEG-encoded bytes.
    """
    annotated = frame.copy()
    h, w = annotated.shape[:2]

    for plate in plate_reads:
        # Vehicle bounding box (blue)
        veh_bbox = plate.get("vehicle_bbox")
        if veh_bbox and len(veh_bbox) >= 4:
            vx1, vy1, vx2, vy2 = [int(v) for v in veh_bbox]
            vx1, vy1 = max(0, vx1), max(0, vy1)
            vx2, vy2 = min(w-1, vx2), min(h-1, vy2)
            cv2.rectangle(annotated, (vx1, vy1), (vx2, vy2), (255, 140, 0), 2)

        # Plate bounding box (bright green)
        plate_bbox = plate.get("plate_bbox")
        if plate_bbox and len(plate_bbox) >= 4:
            px1, py1, px2, py2 = [int(v) for v in plate_bbox]
            px1, py1 = max(0, px1), max(0, py1)
            px2, py2 = min(w-1, px2), min(h-1, py2)
            # Thick green box for plate
            cv2.rectangle(annotated, (px1, py1), (px2, py2), (0, 255, 80), 3)

            # OCR text above plate box
            plate_text = plate.get("plate_text", "")
            conf       = plate.get("ocr_confidence", 0.0)
            watchlisted = plate.get("is_watchlisted", False)

            if plate_text:
                label      = f" {plate_text}  {conf:.2f} "
                font       = cv2.FONT_HERSHEY_SIMPLEX
                font_scale = 0.6
                thickness  = 2
                text_color = (0, 50, 255) if watchlisted else (0, 255, 80)
                bg_color   = (0, 0, 180) if watchlisted else (0, 100, 0)

                (lw, lh), _ = cv2.getTextSize(label, font, font_scale, thickness)
                label_y = max(py1 - 6, lh + 8)
                cv2.rectangle(annotated,
                              (px1, label_y - lh - 6),
                              (px1 + lw + 4, label_y + 2),
                              bg_color, -1)
                cv2.putText(annotated, label,
                            (px1 + 2, label_y - 2),
                            font, font_scale, text_color, thickness, cv2.LINE_AA)

                # ALERT badge for watchlisted plates
                if watchlisted:
                    alert_label = "  !! WATCHLIST HIT !!  "
                    (aw, ah), _ = cv2.getTextSize(alert_label, font, 0.7, 2)
                    cv2.rectangle(annotated, (px1, py2 + 4), (px1 + aw + 4, py2 + ah + 10),
                                  (0, 0, 200), -1)
                    cv2.putText(annotated, alert_label,
                                (px1 + 2, py2 + ah + 6),
                                font, 0.7, (255, 255, 255), 2, cv2.LINE_AA)

    # Watermark
    ts_str   = datetime.now().strftime("%H:%M:%S")
    plate_ct = sum(1 for p in plate_reads if p.get("plate_text"))
    wm = f"DVAS  {drone_id}  ANPR  {ts_str}  {plate_ct} plates"
    cv2.putText(annotated, wm,
                (8, h - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, (160, 160, 160), 1, cv2.LINE_AA)

    ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return buf.tobytes() if ok else b""


@app.on_event("startup")
async def startup():
    global analyzer
    await get_pool()
    for drone_id in cfg.DRONE_IDS:
        await ensure_drone(drone_id)
    await minio.ensure_all_buckets()
    analyzer = ANPRAnalyzer(
        vehicle_model_path=VEHICLE_MODEL,
        lp_model_path=LP_MODEL,
        reid_match_threshold=cfg.ANPR_REID_MATCH_THRESHOLD,
        reid_max_lost_frames=cfg.ANPR_REID_MAX_LOST_FRAMES,
        reid_hist_weight=cfg.ANPR_REID_HIST_WEIGHT,
        reid_aspect_weight=cfg.ANPR_REID_ASPECT_WEIGHT,
        reid_spatial_weight=cfg.ANPR_REID_SPATIAL_WEIGHT,
    )
    log.info("ANPR Analyzer ready. Watchlist: %d entries", len(analyzer.watchlist))


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
    return {
        "status":             "ok",
        "service":            "anpr-analyzer",
        "model_loaded":       analyzer.ready if analyzer else False,
        "ocr_ready":          analyzer.ocr_engine is not None if analyzer else False,
        "stream_running":     _stream_running,
        "frames_processed":   _frames_processed,
        "watchlist_count":    len(analyzer.watchlist) if analyzer else 0,
        "cached_drone_feeds": list(_last_annotated.keys()),
        "reid_stats":         analyzer.reid_tracker.get_stats() if analyzer else {},
    }


# ── Re-ID tracking stats ───────────────────────────────────────────────────────
@app.get("/tracking/stats")
async def tracking_stats():
    """Return vehicle Re-ID tracker statistics for debugging deduplication."""
    if not analyzer:
        raise HTTPException(503, "Analyzer not loaded")
    stats = analyzer.reid_tracker.get_stats()
    stats["frames_processed"] = _frames_processed
    return stats


# ── Single frame analysis ──────────────────────────────────────────────────────
@app.post("/analyze/frame")
async def analyze_frame(
    file: UploadFile = File(...),
    drone_id: str = Form(default="DRONE_KL_001"),
):
    if not analyzer or not analyzer.ready:
        raise HTTPException(503, "Models not loaded")

    contents = await file.read()
    nparr    = np.frombuffer(contents, np.uint8)
    frame    = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if frame is None:
        raise HTTPException(400, "Cannot decode image")

    result = analyzer.analyze_frame(frame, drone_id=drone_id)
    ts     = datetime.now(timezone.utc)

    for plate in result["plate_reads"]:
        if not plate["plate_text"]:
            continue

        tracker_id = plate.get("tracker_id")
        track_id   = await upsert_track(drone_id, None, "vehicle", ts, ts, tracker_id=tracker_id)
        plate["track_id"] = track_id

        plate_image_key   = None
        vehicle_image_key = None

        veh_img_b64 = plate.get("vehicle_image_b64")
        if veh_img_b64:
            veh_img_bytes = base64.b64decode(veh_img_b64)
            key = minio.make_key("plates", drone_id,
                                 f"vehicle_{tracker_id}_{int(ts.timestamp()*1000)}.jpg")
            vehicle_image_key = await minio.upload_bytes(
                cfg.BUCKET_VEHICLES, key, veh_img_bytes, "image/jpeg"
            )

        plate_crop_b64 = plate.pop("plate_crop_b64", None)
        if plate_crop_b64:
            plate_bytes = base64.b64decode(plate_crop_b64)
            key = minio.make_key("plates", drone_id,
                                 f"plate_{plate['plate_text']}_{int(ts.timestamp()*1000)}.jpg")
            plate_image_key = await minio.upload_bytes(
                cfg.BUCKET_PLATES, key, plate_bytes, "image/jpeg"
            )

        event_id = await write_confirmed_event(
            drone_id=drone_id,
            event_type="intrusion" if plate["is_watchlisted"] else "vehicle_detected",
            confidence=plate["ocr_confidence"],
            ts=ts, track_id=track_id,
            attributes={
                "plate_text":   plate["plate_text"],
                "vehicle_type": plate["vehicle_type"],
                "is_theft":     plate["is_watchlisted"],
            },
        )

        await write_plate_read(
            track_id=track_id, event_id=event_id,
            plate_text=plate["plate_text"],
            ocr_confidence=plate["ocr_confidence"],
            is_reliable=plate["is_reliable"],
            plate_image_key=plate_image_key,
            vehicle_image_key=vehicle_image_key,
        )

        if tracker_id is not None:
            veh_data = await hget_vehicle_track(tracker_id, drone_id)
            colour   = veh_data.get("colour")
            await write_vehicle_plate_link(
                tracker_id=tracker_id, drone_id=drone_id,
                plate_text=plate["plate_text"],
                plate_conf=plate["ocr_confidence"],
                vehicle_type=plate["vehicle_type"],
                colour=colour,
                plate_image_key=plate_image_key,
                vehicle_image_key=vehicle_image_key or veh_data.get("vehicle_image_key"),
            )
            await hset_vehicle_track(
                tracker_id=tracker_id, drone_id=drone_id,
                data={
                    "plate_text":      plate["plate_text"],
                    "plate_conf":      plate["ocr_confidence"],
                    "plate_image_key": plate_image_key or "",
                },
                ttl=cfg.VEHICLE_TRACK_TTL,
            )

        if plate["is_watchlisted"]:
            await publish_confirmed_event({
                "event_id":    event_id, "event_type": "theft_alert",
                "drone_id":    drone_id, "ts": ts.isoformat(),
                "plate_text":  plate["plate_text"],
                "vehicle_type": plate["vehicle_type"],
                "confidence":  plate["ocr_confidence"],
            })
            pool = await get_pool()
            await pool.execute(
                """
                INSERT INTO theft_alerts
                    (plate_text, track_id, drone_id, detected_at, frame_ts, confidence, vehicle_type)
                VALUES ($1, $2::uuid, $3, $4, $4, $5, $6)
                ON CONFLICT DO NOTHING
                """,
                plate["plate_text"], track_id, drone_id, ts,
                plate["ocr_confidence"], plate["vehicle_type"],
            )

    await publish_analytics_result("anpr", result)
    return result


# ── Continuous stream loop ─────────────────────────────────────────────────────
async def _stream_loop():
    global _stream_running, _frames_processed
    try:
        last_ids = {d: "0" for d in cfg.DRONE_IDS}
        log.info("ANPR stream loop started for drones: %s", cfg.DRONE_IDS)
    except Exception as exc:
        log.error("ANPR stream loop FAILED to initialize: %s", exc, exc_info=True)
        _stream_running = False
        return

    while _stream_running:
        for drone_id in cfg.DRONE_IDS:
            try:
                new_id, frames = await xread_frames(
                    drone_id, last_ids[drone_id], count=2, block_ms=100
                )
                last_ids[drone_id] = new_id

                for f in frames:
                    raw = f.get("jpeg_b64", "")
                    if not raw:
                        continue
                    img_bytes = base64.b64decode(raw)
                    nparr  = np.frombuffer(img_bytes, np.uint8)
                    frame  = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    if frame is None:
                        continue

                    log.info("ANPR: processing frame for %s size=%s", drone_id, frame.shape if frame is not None else None)
                    result = analyzer.analyze_frame(frame, drone_id=drone_id)
                    log.info("ANPR: frame done — vehicles=%d plates_read=%d inference_ms=%.0f",
                             result.get("total_vehicles", 0), result.get("plates_read", 0), result.get("inference_ms", 0))
                    ts     = datetime.now(timezone.utc)

                    # Extract mission_id from stream frame metadata
                    import json as _json
                    frame_mission_id = None
                    raw_meta = f.get("metadata", "")
                    if raw_meta:
                        if isinstance(raw_meta, bytes):
                            raw_meta = raw_meta.decode()
                        try:
                            meta_dict = _json.loads(raw_meta)
                            frame_mission_id = meta_dict.get("mission_id")
                        except Exception:
                            pass

                    # ── Annotate frame and cache for dashboard ─────────────────
                    import time as _time
                    annotated_bytes = _draw_anpr_annotations(
                        frame, result.get("plate_reads", []), drone_id
                    )
                    if annotated_bytes:
                        _last_annotated[drone_id]    = annotated_bytes
                        _last_annotated_ts[drone_id] = _time.monotonic()

                    # ── Update live in-memory plate cache (for /plates/live) ────
                    import time as _time2
                    if result.get("plate_reads"):
                        log.info("Stream loop: caching %d plates/vehicles for %s", len(result["plate_reads"]), drone_id)
                    for plate in result.get("plate_reads", []):
                        cache_key = f"{drone_id}:{plate['tracker_id']}"
                        existing  = _recent_plate_cache.get(cache_key, {})
                        plate_text = plate.get("plate_text") or existing.get("plate_text", "")
                        
                        log.info(f"CACHE UPDATE tracker {plate['tracker_id']}: veh_bbox={plate.get('vehicle_bbox')} existing_veh_bbox={existing.get('vehicle_bbox')} plate_bbox={plate.get('plate_bbox')} existing_plate_bbox={existing.get('plate_bbox')}")
                        
                        _recent_plate_cache[cache_key] = {
                            "plate_text":        plate_text,
                            "ocr_confidence":    plate.get("ocr_confidence") or existing.get("ocr_confidence", 0.0),
                            "is_reliable":       plate.get("is_reliable", False),
                            "drone_id":          drone_id,
                            "mission_id":        frame_mission_id or existing.get("mission_id"),
                            "tracker_id":        plate.get("tracker_id"),
                            "vehicle_type":      plate.get("vehicle_type", "vehicle"),
                            "vehicle_image_b64": plate.get("vehicle_image_b64") or existing.get("vehicle_image_b64"),
                            "plate_crop_b64":    plate.get("plate_crop_b64") or existing.get("plate_crop_b64"),
                            "vehicle_bbox":      plate.get("vehicle_bbox") or existing.get("vehicle_bbox"),
                            "plate_bbox":        plate.get("plate_bbox") or existing.get("plate_bbox"),
                            "is_watchlisted":    plate.get("is_watchlisted", False),
                            "created_at":        existing.get("created_at") or ts.isoformat(),
                            "_cache_ts":         _time2.monotonic(),
                        }

                    # ── Persist plates ─────────────────────────────────────────
                    for plate in result.get("plate_reads", []):
                        if not (plate.get("plate_text") and plate.get("is_reliable")):
                            continue
                        tracker_id = plate.get("tracker_id")
                        track_id   = await upsert_track(
                            drone_id, None, "vehicle", ts, ts,
                            tracker_id=tracker_id, mission_id=frame_mission_id
                        )
                        plate_image_key = None
                        plate_crop_b64  = plate.pop("plate_crop_b64", None)
                        if plate_crop_b64:
                            plate_bytes_data = base64.b64decode(plate_crop_b64)
                            key = minio.make_key(
                                "plates", drone_id,
                                f"plate_{plate['plate_text']}_{int(ts.timestamp()*1000)}.jpg",
                                mission_id=frame_mission_id or "",
                            )
                            plate_image_key = await minio.upload_bytes(
                                cfg.BUCKET_PLATES, key, plate_bytes_data, "image/jpeg"
                            )

                        # Upload vehicle image if available
                        vehicle_image_key = None
                        veh_img_b64 = plate.pop("vehicle_image_b64", None)
                        if veh_img_b64:
                            veh_bytes_data = base64.b64decode(veh_img_b64)
                            key = minio.make_key(
                                "vehicles", drone_id,
                                f"anpr_veh_{tracker_id}_{int(ts.timestamp()*1000)}.jpg",
                                mission_id=frame_mission_id or "",
                            )
                            vehicle_image_key = await minio.upload_bytes(
                                cfg.BUCKET_VEHICLES, key, veh_bytes_data, "image/jpeg"
                            )

                        await write_plate_read(
                            track_id=track_id, event_id=None,
                            plate_text=plate["plate_text"],
                            ocr_confidence=plate["ocr_confidence"],
                            is_reliable=plate["is_reliable"],
                            plate_image_key=plate_image_key,
                            vehicle_image_key=vehicle_image_key,
                            mission_id=frame_mission_id,
                        )

                        if tracker_id is not None:
                            await hset_vehicle_track(
                                tracker_id=tracker_id, drone_id=drone_id,
                                data={
                                    "plate_text":      plate["plate_text"],
                                    "plate_conf":      plate["ocr_confidence"],
                                    "plate_image_key": plate_image_key or "",
                                    "mission_id":      frame_mission_id or "",
                                },
                                ttl=cfg.VEHICLE_TRACK_TTL,
                            )
                            veh_data = await hget_vehicle_track(tracker_id, drone_id)
                            colour   = veh_data.get("colour", "")
                            await write_vehicle_plate_link(
                                tracker_id=tracker_id, drone_id=drone_id,
                                plate_text=plate["plate_text"],
                                plate_conf=plate["ocr_confidence"],
                                vehicle_type=plate.get("vehicle_type"),
                                colour=colour,
                                plate_image_key=plate_image_key,
                                vehicle_image_key=vehicle_image_key,
                                mission_id=frame_mission_id,
                            )

                        if plate.get("is_watchlisted"):
                            pool = await get_pool()
                            await pool.execute(
                                """
                                INSERT INTO theft_alerts
                                    (plate_text, track_id, drone_id, detected_at, frame_ts,
                                     confidence, vehicle_type)
                                VALUES ($1, $2::uuid, $3, $4, $4, $5, $6)
                                ON CONFLICT DO NOTHING
                                """,
                                plate["plate_text"], track_id, drone_id, ts,
                                plate["ocr_confidence"], plate.get("vehicle_type", "unknown"),
                            )
                            await publish_confirmed_event({
                                "event_type":  "theft_alert",
                                "drone_id":    drone_id,
                                "mission_id":  frame_mission_id,
                                "ts":          ts.isoformat(),
                                "plate_text":  plate["plate_text"],
                            })

                    await publish_analytics_result("anpr", {
                        **result,
                        "drone_id":    drone_id,
                        "ts":          ts.isoformat(),
                        "frame_count": _frames_processed,
                    })
                    _frames_processed += 1

            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning("ANPR stream error for %s: %s", drone_id, exc)
                await asyncio.sleep(1)


# ── Stream control ─────────────────────────────────────────────────────────────
@app.get("/stream/start")
async def stream_start():
    global _stream_running, _stream_task
    if _stream_running:
        return {"status": "already_running", "frames_processed": _frames_processed}
    _stream_running = True
    _stream_task = asyncio.create_task(_stream_loop())

    def _task_done_cb(task):
        if not task.cancelled():
            exc = task.exception()
            if exc:
                log.error("ANPR stream loop CRASHED: %s", exc, exc_info=exc)
    _stream_task.add_done_callback(_task_done_cb)

    log.info("ANPR stream loop STARTED")
    return {"status": "started"}


@app.get("/stream/stop")
async def stream_stop():
    global _stream_running, _stream_task
    _stream_running = False
    if _stream_task:
        _stream_task.cancel()
        _stream_task = None
    log.info("ANPR stream loop STOPPED")
    return {"status": "stopped", "frames_processed": _frames_processed}


# ── Annotated frame endpoint ───────────────────────────────────────────────────
@app.get("/annotated/latest")
async def annotated_latest(drone_id: str = ""):
    """
    Returns the most recently annotated JPEG frame with plate bounding boxes + OCR text.
    """
    frame_bytes = None
    if drone_id and drone_id in _last_annotated:
        frame_bytes = _last_annotated[drone_id]
    else:
        import time as _time
        best_drone = max(_last_annotated_ts, key=_last_annotated_ts.get, default=None)
        if best_drone:
            frame_bytes = _last_annotated.get(best_drone)

    if not frame_bytes:
        raise HTTPException(404, "No annotated frame available yet — start detection first")

    return StreamingResponse(
        io.BytesIO(frame_bytes),
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma":        "no-cache",
            "Expires":       "0",
        },
    )


# ── Plate query endpoints ──────────────────────────────────────────────────────
@app.get("/missions")
async def get_missions():
    """Return all unique mission IDs that have plate reads."""
    try:
        from shared.db import get_all_missions
        missions = await get_all_missions()
        return {"missions": missions}
    except Exception as e:
        logger.error(f"Error fetching missions: {e}")
        raise HTTPException(status_code=500, detail="Database error")

@app.get("/plates/recent")
async def recent_plates(limit: int = 50, drone_id: str = "", mission_id: str = ""):
    pool  = await get_pool()
    query = """
        SELECT
            pr.id, pr.plate_text, pr.ocr_confidence, pr.is_reliable,
            pr.plate_image_key, pr.vehicle_image_key,
            pr.created_at, pr.mission_id_str,
            t.drone_id, t.metadata->>'tracker_id' AS tracker_id
        FROM plate_reads pr
        JOIN tracks t ON t.track_id = pr.track_id
    """
    params = []
    conditions = []
    if mission_id:
        conditions.append(f"pr.mission_id_str = ${len(params)+1}")
        params.append(mission_id)
    elif drone_id:
        conditions.append(f"t.drone_id = ${len(params)+1}")
        params.append(drone_id)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += f" ORDER BY pr.created_at DESC LIMIT ${len(params)+1}"
    params.append(min(limit, 200))

    rows = await pool.fetch(query, *params)
    plates = []
    for r in rows:
        d = {k: (str(v) if hasattr(v, "isoformat") else v) for k, v in dict(r).items()}
        # Generate presigned URLs for images
        if d.get("plate_image_key"):
            try:
                d["plate_image_url"] = await minio.get_presigned_url(
                    cfg.BUCKET_PLATES, d["plate_image_key"], cfg.MINIO_URL_EXPIRY
                )
            except Exception:
                d["plate_image_url"] = None
        if d.get("vehicle_image_key"):
            try:
                d["vehicle_image_url"] = await minio.get_presigned_url(
                    cfg.BUCKET_VEHICLES, d["vehicle_image_key"], cfg.MINIO_URL_EXPIRY
                )
            except Exception:
                d["vehicle_image_url"] = None
        plates.append(d)

    return {"count": len(plates), "plates": plates}


@app.get("/plates/live")
async def live_plates(drone_id: str = "", mission_id: str = "", limit: int = 50):
    """
    Return in-memory plate detections from the active stream loop.
    Includes base64 images directly — no MinIO required.
    Filters by mission_id if provided (preferred), otherwise by drone_id.
    Useful when DB/MinIO writes are lagging behind live detections.
    """
    import time as _t
    now = _t.monotonic()
    MAX_AGE = 30.0  # drop entries older than 30 seconds

    results = []
    for entry in _recent_plate_cache.values():
        age = now - entry.get("_cache_ts", 0)
        if age > MAX_AGE:
            continue
        if mission_id and entry.get("mission_id") != mission_id:
            continue
        if not mission_id and drone_id and entry.get("drone_id") != drone_id:
            continue
        results.append(entry)

    # Sort by cache timestamp descending, newest first
    results.sort(key=lambda e: e.get("_cache_ts", 0), reverse=True)
    results = results[:min(limit, 200)]

    # Strip internal cache field before returning
    cleaned = [{k: v for k, v in e.items() if k != "_cache_ts"} for e in results]
    return {"count": len(cleaned), "plates": cleaned, "source": "live_cache"}


# ── Mission vehicle history ────────────────────────────────────────────────────
@app.get("/plates/by-mission")
async def plates_by_mission(
    mission_id: str = "",
    drone_id: str = "",
    start: str = "",
    end: str = "",
    plate_search: str = "",
    vehicle_type: str = "",
    sort: str = "newest",
    limit: int = 24,
    offset: int = 0,
):
    """
    Query historical plate reads for a given mission or drone + time window.
    When mission_id is provided it filters directly by mission_id_str (accurate per-mission data).
    Falls back to drone_id + time range for backward compatibility.
    Supports plate text search, vehicle type filter, sorting, and pagination.
    """
    pool = await get_pool()
    limit = min(limit, 200)

    # ── Fast path: filter directly by mission_id ──────────────────────────────
    if mission_id:
        result = await get_plates_by_mission(
            mission_id=mission_id,
            plate_search=plate_search,
            vehicle_type=vehicle_type,
            sort=sort,
            limit=limit,
            offset=offset,
        )
        vehicles = []
        plates_read_count = 0
        for r in result["rows"]:
            d = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()}
            # Parse colour_top3
            if d.get("colour_top3") and isinstance(d["colour_top3"], str):
                import json as _json
                try:
                    d["colour_top3"] = _json.loads(d["colour_top3"])
                except Exception:
                    pass
            colour = ""
            if isinstance(d.get("colour_top3"), list) and d["colour_top3"]:
                colour = d["colour_top3"][0].get("colour", "") if isinstance(d["colour_top3"][0], dict) else ""
            d["colour"] = colour
            if d.get("plate_text"):
                plates_read_count += 1
            # Presigned URLs
            if d.get("plate_image_key"):
                try:
                    d["plate_image_url"] = await minio.get_presigned_url(
                        cfg.BUCKET_PLATES, d["plate_image_key"], cfg.MINIO_URL_EXPIRY
                    )
                except Exception:
                    d["plate_image_url"] = None
            if d.get("vehicle_image_key"):
                try:
                    d["vehicle_image_url"] = await minio.get_presigned_url(
                        cfg.BUCKET_VEHICLES, d["vehicle_image_key"], cfg.MINIO_URL_EXPIRY
                    )
                except Exception:
                    d["vehicle_image_url"] = None
            vehicles.append(d)
        return {
            "total": result["total"],
            "total_plates_read": plates_read_count,
            "vehicles": vehicles,
            "offset": offset,
            "limit": limit,
            "has_more": result["has_more"],
            "mission_id": mission_id,
        }

    # ── Fallback: drone_id + time range (backward compat) ─────────────────────
    # Build WHERE clauses dynamically
    conditions = []
    params = []
    idx = 1

    if drone_id:
        conditions.append(f"t.drone_id = ${idx}")
        params.append(drone_id)
        idx += 1

    if start:
        conditions.append(f"t.first_seen >= ${idx}::timestamptz")
        params.append(start)
        idx += 1

    if end:
        conditions.append(f"t.first_seen <= ${idx}::timestamptz")
        params.append(end)
        idx += 1

    if plate_search:
        conditions.append(f"pr.plate_text ILIKE ${idx}")
        params.append(f"%{plate_search.upper().strip()}%")
        idx += 1

    if vehicle_type and vehicle_type.lower() != "all":
        conditions.append(f"LOWER(va.vehicle_type) = ${idx}")
        params.append(vehicle_type.lower().strip())
        idx += 1

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    # Sort mapping
    sort_map = {
        "newest":     "pr.created_at DESC",
        "oldest":     "pr.created_at ASC",
        "confidence": "pr.ocr_confidence DESC NULLS LAST",
        "plate_az":   "pr.plate_text ASC NULLS LAST",
    }
    order_by = sort_map.get(sort, "pr.created_at DESC")

    # Count query (for pagination metadata)
    count_query = f"""
        SELECT COUNT(*) AS total
        FROM plate_reads pr
        JOIN tracks t ON t.track_id = pr.track_id
        LEFT JOIN vehicle_attributes va ON va.track_id = t.track_id
        {where_clause}
    """
    count_row = await pool.fetchrow(count_query, *params)
    total = count_row["total"] if count_row else 0

    # Data query
    data_query = f"""
        SELECT
            pr.id, pr.plate_text, pr.ocr_confidence, pr.is_reliable,
            pr.plate_image_key, pr.vehicle_image_key, pr.created_at, pr.mission_id_str,
            t.drone_id, t.first_seen, t.last_seen,
            t.metadata->>'tracker_id' AS tracker_id,
            va.vehicle_type, va.colour_top3, va.speed_kph
        FROM plate_reads pr
        JOIN tracks t ON t.track_id = pr.track_id
        LEFT JOIN vehicle_attributes va ON va.track_id = t.track_id
        {where_clause}
        ORDER BY {order_by}
        LIMIT ${idx} OFFSET ${idx + 1}
    """
    params.extend([limit, offset])

    rows = await pool.fetch(data_query, *params)
    vehicles = []
    plates_read_count = 0
    for r in rows:
        d = {k: (v.isoformat() if hasattr(v, "isoformat") else v)
             for k, v in dict(r).items()}

        # Parse colour_top3 JSON
        if d.get("colour_top3") and isinstance(d["colour_top3"], str):
            import json as _json
            try:
                d["colour_top3"] = _json.loads(d["colour_top3"])
            except Exception:
                pass
        colour = ""
        if isinstance(d.get("colour_top3"), list) and d["colour_top3"]:
            colour = d["colour_top3"][0].get("colour", "") if isinstance(d["colour_top3"][0], dict) else ""
        d["colour"] = colour

        if d.get("plate_text"):
            plates_read_count += 1

        # Generate presigned URLs for images
        if d.get("plate_image_key"):
            try:
                d["plate_image_url"] = await minio.get_presigned_url(
                    cfg.BUCKET_PLATES, d["plate_image_key"], cfg.MINIO_URL_EXPIRY
                )
            except Exception:
                d["plate_image_url"] = None
        if d.get("vehicle_image_key"):
            try:
                d["vehicle_image_url"] = await minio.get_presigned_url(
                    cfg.BUCKET_VEHICLES, d["vehicle_image_key"], cfg.MINIO_URL_EXPIRY
                )
            except Exception:
                d["vehicle_image_url"] = None

        vehicles.append(d)

    return {
        "total": total,
        "total_plates_read": plates_read_count,
        "vehicles": vehicles,
        "offset": offset,
        "limit": limit,
        "has_more": (offset + limit) < total,
    }



@app.get("/plates/{plate_text}")
async def plate_history(plate_text: str):
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM plate_reads WHERE plate_text=$1 ORDER BY created_at DESC",
        plate_text.upper().strip(),
    )
    return {"plate_text": plate_text, "records": [dict(r) for r in rows]}


@app.get("/plates/{plate_text}/image")
async def plate_image_url(plate_text: str):
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT plate_image_key FROM plate_reads
        WHERE plate_text=$1 AND plate_image_key IS NOT NULL
        ORDER BY created_at DESC LIMIT 1
        """,
        plate_text.upper().strip(),
    )
    if not row or not row["plate_image_key"]:
        raise HTTPException(404, "No plate image stored")
    url = await minio.get_presigned_url(
        cfg.BUCKET_PLATES, row["plate_image_key"], cfg.MINIO_URL_EXPIRY
    )
    return {"plate_text": plate_text, "image_url": url}


# ── Watchlist management ───────────────────────────────────────────────────────
class WatchlistAddRequest(BaseModel):
    plate_text: str
    reason: Optional[str] = None
    case_ref: Optional[str] = None


@app.post("/watchlist/add")
async def watchlist_add(req: WatchlistAddRequest):
    await add_to_watchlist(req.plate_text, req.reason)
    if analyzer:
        analyzer.watchlist.add(req.plate_text.upper().strip())
    return {"status": "added", "plate": req.plate_text.upper().strip()}


@app.delete("/watchlist/{plate}")
async def watchlist_remove(plate: str):
    await remove_from_watchlist(plate)
    if analyzer:
        analyzer.watchlist.discard(plate.upper().strip())
    return {"status": "removed", "plate": plate.upper().strip()}


@app.get("/watchlist")
async def watchlist_get():
    rows = await get_watchlist()
    return {"count": len(rows), "watchlist": rows}


@app.get("/alerts/theft")
async def theft_alerts(limit: int = 50):
    pool = await get_pool()
    # Join with plate_reads to get the latest images for each alert
    rows = await pool.fetch(
        """
        SELECT
            ta.id, ta.plate_text, ta.drone_id, ta.detected_at, ta.frame_ts,
            ta.confidence, ta.vehicle_type, ta.is_confirmed, ta.notes,
            pr.plate_image_key, pr.vehicle_image_key, pr.mission_id_str
        FROM theft_alerts ta
        LEFT JOIN LATERAL (
            SELECT plate_image_key, vehicle_image_key, mission_id_str
            FROM plate_reads
            WHERE plate_text = ta.plate_text
              AND (plate_image_key IS NOT NULL OR vehicle_image_key IS NOT NULL)
            ORDER BY created_at DESC
            LIMIT 1
        ) pr ON true
        ORDER BY ta.detected_at DESC
        LIMIT $1
        """,
        limit,
    )
    alerts = []
    for r in rows:
        d = {k: (str(v) if hasattr(v, "isoformat") else v) for k, v in dict(r).items()}
        if d.get("plate_image_key"):
            try:
                d["plate_image_url"] = await minio.get_presigned_url(
                    cfg.BUCKET_PLATES, d["plate_image_key"], cfg.MINIO_URL_EXPIRY
                )
            except Exception:
                d["plate_image_url"] = None
        if d.get("vehicle_image_key"):
            try:
                d["vehicle_image_url"] = await minio.get_presigned_url(
                    cfg.BUCKET_VEHICLES, d["vehicle_image_key"], cfg.MINIO_URL_EXPIRY
                )
            except Exception:
                d["vehicle_image_url"] = None
        alerts.append(d)
    return {"count": len(alerts), "alerts": alerts}


@app.get("/alerts/theft/live")
async def theft_alerts_live():
    """SSE stream of live theft/watchlist alerts from confirmed events."""
    from shared.redis_client import get_client
    import json

    async def event_gen():
        client  = get_client()
        last_id = "$"
        try:
            while True:
                results = await client.xread(
                    {"drone:events:confirmed": last_id}, count=5, block=5000,
                )
                if results:
                    for _stream, entries in results:
                        for entry_id, fields in entries:
                            last_id  = entry_id
                            data_str = fields.get("data", "{}")
                            if isinstance(data_str, bytes):
                                data_str = data_str.decode()
                            try:
                                data = json.loads(data_str)
                            except Exception:
                                continue
                            if data.get("event_type") == "theft_alert":
                                yield f"data: {data_str}\n\n"
                else:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )



if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8104, reload=False)
