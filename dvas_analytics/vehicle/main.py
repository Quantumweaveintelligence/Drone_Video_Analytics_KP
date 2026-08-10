# -*- coding: utf-8 -*-
"""
Vehicle Analyzer Service — FastAPI on port 8103.
Vehicle detection, tracking, colour classification, speed estimation.
Publishes track info to Redis for ANPR linkage.
Stores annotated frames (with bounding boxes) in memory for live dashboard feed.
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

sys.path.insert(0, "/analytics")
from shared.config import cfg
from shared.db import (
    close_pool, ensure_drone, get_pool, upsert_track,
    write_confirmed_event, write_vehicle_attributes,
    write_evidence_frame, get_vehicle_plate_link,
)
from shared.redis_client import (
    close_client, publish_analytics_result, publish_confirmed_event,
    xread_frames, hset_vehicle_track, hget_vehicle_track,
)
from shared import minio_client as minio
from vehicle.analyzer import VehicleAnalyzer
from vehicle.schema import VehicleFrameResult, VehicleStats

logging.basicConfig(
    level=cfg.LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("vehicle-service")

app = FastAPI(
    title="DVAS Vehicle Analyzer",
    description="Vehicle detection, tracking, colour classification, speed estimation",
    version="2.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

VEHICLE_MODEL = os.getenv("VEHICLE_MODEL", "/analytics/vehicle/models/visdrone.pt")
analyzer: Optional[VehicleAnalyzer] = None
_stream_task: Optional[asyncio.Task] = None
_stream_running = False
_frames_processed = 0

# ── Annotated frame cache ─────────────────────────────────────────────────────
# Stores the latest annotated JPEG bytes per drone_id for dashboard display
_last_annotated: Dict[str, bytes] = {}
_last_annotated_ts: Dict[str, float] = {}

# Bounding box colours per vehicle type (BGR)
_BBOX_COLOURS = {
    'car':         (255, 180,  50),   # blue-gold
    'truck':       (50,  50,  255),   # red
    'bus':         (50,  160, 255),   # orange-red
    'motorcycle':  (50,  255, 150),   # green
    'bicycle':     (255, 220,  50),   # cyan
    'van':         (200,  80, 255),   # purple
    'default':     (100, 255, 100),   # lime
}


def _draw_vehicle_annotations(frame: np.ndarray, vehicles: list, drone_id: str) -> bytes:
    """
    Draw vehicle bounding boxes, tracker IDs, type, colour, speed and plate text
    onto the frame. Returns JPEG-encoded bytes.
    """
    annotated = frame.copy()
    h, w = annotated.shape[:2]

    for veh in vehicles:
        bbox = veh.get("bbox")
        if not bbox or len(bbox) < 4:
            continue
        x1, y1, x2, y2 = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
        # Clamp to frame
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w - 1, x2), min(h - 1, y2)

        vtype      = (veh.get("vehicle_type") or "vehicle").lower()
        colour_bgr = _BBOX_COLOURS.get(vtype, _BBOX_COLOURS["default"])
        tid        = veh.get("tracker_id", "?")
        conf       = veh.get("confidence", 0.0)
        speed_info = veh.get("speed_info") or {}
        speed_kph  = speed_info.get("kph", 0) or 0
        colour_top3 = veh.get("colour_top3") or []
        dom_colour  = colour_top3[0].get("colour", "") if colour_top3 else ""
        plate_text  = veh.get("plate_text", "")

        # Main bounding box (2px)
        cv2.rectangle(annotated, (x1, y1), (x2, y2), colour_bgr, 2)

        # Corner ticks for visual clarity
        tick = 10
        for cx, cy, dx, dy in [
            (x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)
        ]:
            cv2.line(annotated, (cx, cy), (cx + dx * tick, cy), colour_bgr, 3)
            cv2.line(annotated, (cx, cy), (cx, cy + dy * tick), colour_bgr, 3)

        # Build label
        label_parts = [f"#{tid}", vtype.upper(), f"{conf:.2f}"]
        if dom_colour:
            label_parts.append(dom_colour)
        if speed_kph and speed_kph > 0:
            label_parts.append(f"{int(speed_kph)}km/h")
        label = "  ".join(label_parts)

        font       = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.42
        thickness  = 1
        (lw, lh), baseline = cv2.getTextSize(label, font, font_scale, thickness)

        # Label background bar
        label_y = max(y1 - 4, lh + 6)
        cv2.rectangle(annotated,
                      (x1, label_y - lh - baseline - 4),
                      (x1 + lw + 6, label_y),
                      colour_bgr, -1)
        cv2.putText(annotated, label,
                    (x1 + 3, label_y - baseline - 1),
                    font, font_scale, (0, 0, 0), thickness, cv2.LINE_AA)

        # Plate text overlay (inside box at bottom)
        if plate_text:
            plate_label = f" {plate_text} "
            (pw, ph), _ = cv2.getTextSize(plate_label, font, 0.55, 2)
            px = x1 + 4
            py = min(y2 - 4, h - ph - 6)
            cv2.rectangle(annotated, (px - 2, py - ph - 2), (px + pw + 2, py + 4),
                          (0, 0, 0), -1)
            cv2.putText(annotated, plate_label, (px, py),
                        font, 0.55, (50, 255, 200), 2, cv2.LINE_AA)

    # Watermark overlay
    ts_str = datetime.now().strftime("%H:%M:%S")
    wm = f"DVAS  {drone_id}  Vehicle+ANPR  {ts_str}  {len(vehicles)} detections"
    cv2.putText(annotated, wm,
                (8, h - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, (160, 160, 160), 1, cv2.LINE_AA)

    # Encode to JPEG
    ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return buf.tobytes() if ok else b""


@app.on_event("startup")
async def startup():
    global analyzer
    await get_pool()
    for drone_id in cfg.DRONE_IDS:
        await ensure_drone(drone_id)
    await minio.ensure_all_buckets()
    analyzer = VehicleAnalyzer(VEHICLE_MODEL)
    log.info("Vehicle Analyzer ready. Model: %s", VEHICLE_MODEL)


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
        "service":            "vehicle-analyzer",
        "model_loaded":       analyzer.ready if analyzer else False,
        "stream_running":     _stream_running,
        "frames_processed":   _frames_processed,
        "cached_drone_feeds": list(_last_annotated.keys()),
    }


# ── Single frame analysis ──────────────────────────────────────────────────────
@app.post("/analyze/frame")
async def analyze_frame(
    file: UploadFile = File(...),
    drone_id: str = Form(default="DRONE_KL_001"),
    save_crops: bool = Form(default=True),
):
    if not analyzer or not analyzer.ready:
        raise HTTPException(503, "Model not loaded")
    contents = await file.read()
    nparr    = np.frombuffer(contents, np.uint8)
    frame    = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if frame is None:
        raise HTTPException(400, "Cannot decode image")

    result = analyzer.analyze_frame(frame, drone_id=drone_id, save_crops=save_crops)
    ts = datetime.now(timezone.utc)

    for veh in result["vehicles"]:
        tracker_id = veh.get("tracker_id")
        track_id = await upsert_track(
            drone_id, None, "vehicle", ts, ts, tracker_id=tracker_id
        )
        veh["track_id"] = track_id

        event_id = await write_confirmed_event(
            drone_id=drone_id, event_type="vehicle_detected",
            confidence=veh["confidence"], ts=ts, track_id=track_id,
            attributes={"vehicle_type": veh["vehicle_type"]},
        )

        colour_label = veh["colour_top3"][0]["colour"] if veh.get("colour_top3") else None

        await write_vehicle_attributes(
            track_id=track_id,
            event_id=event_id,
            vehicle_type=veh["vehicle_type"],
            colour_top3=veh.get("colour_top3", []),
            make_model_top3=veh.get("make_model_top3", []),
            speed_kph=veh.get("speed_info", {}).get("kph"),
            speed_reliability=veh.get("speed_info", {}).get("reliability"),
        )

        crop_bytes = veh.pop("crop_bytes", None)
        veh_image_key = None
        if crop_bytes:
            key = minio.make_key("vehicles", drone_id,
                                 f"vehicle_{tracker_id}_{int(ts.timestamp()*1000)}.jpg")
            veh_image_key = await minio.upload_bytes(
                cfg.BUCKET_VEHICLES, key, crop_bytes, "image/jpeg"
            )
            if veh_image_key:
                veh["crop_key"] = veh_image_key

        await hset_vehicle_track(
            tracker_id=tracker_id,
            drone_id=drone_id,
            data={
                "vehicle_type":      veh["vehicle_type"],
                "colour":            colour_label or "",
                "colour_top3":       veh.get("colour_top3", []),
                "track_id":          track_id,
                "vehicle_image_key": veh_image_key or "",
                "ts":                ts.isoformat(),
            },
            ttl=cfg.VEHICLE_TRACK_TTL,
        )

        await publish_confirmed_event({
            "event_id":    event_id,
            "event_type":  "vehicle_detected",
            "drone_id":    drone_id,
            "ts":          ts.isoformat(),
            "vehicle_type": veh["vehicle_type"],
            "colour":      colour_label,
            "confidence":  veh["confidence"],
        })

    await publish_analytics_result("vehicle", result)
    return result


# ── Continuous stream loop ─────────────────────────────────────────────────────
async def _stream_loop():
    global _stream_running, _frames_processed
    last_ids = {d: "0" for d in cfg.DRONE_IDS}
    log.info("Vehicle stream loop started for drones: %s", cfg.DRONE_IDS)

    while _stream_running:
        for drone_id in cfg.DRONE_IDS:
            try:
                new_id, frames = await xread_frames(
                    drone_id, last_ids[drone_id], count=4, block_ms=100
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

                    result = analyzer.analyze_frame(frame, drone_id=drone_id, save_crops=True)
                    ts     = datetime.now(timezone.utc)

                    # ── Annotate frame and cache for dashboard ────────────────
                    import time as _time
                    annotated_bytes = _draw_vehicle_annotations(
                        frame, result.get("vehicles", []), drone_id
                    )
                    if annotated_bytes:
                        _last_annotated[drone_id]    = annotated_bytes
                        _last_annotated_ts[drone_id] = _time.monotonic()

                    # ── Persist to DB + Redis ─────────────────────────────────
                    for veh in result.get("vehicles", []):
                        tracker_id   = veh.get("tracker_id")
                        track_id     = await upsert_track(
                            drone_id, None, "vehicle", ts, ts, tracker_id=tracker_id
                        )
                        await write_confirmed_event(
                            drone_id=drone_id, event_type="vehicle_detected",
                            confidence=veh["confidence"], ts=ts, track_id=track_id,
                        )

                        colour_label = (veh.get("colour_top3") or [{}])[0].get("colour", "")
                        crop_bytes   = veh.pop("crop_bytes", None)
                        veh_image_key = None
                        if crop_bytes:
                            key = minio.make_key("vehicles", drone_id,
                                                 f"v_{tracker_id}_{int(ts.timestamp()*1000)}.jpg")
                            veh_image_key = await minio.upload_bytes(
                                cfg.BUCKET_VEHICLES, key, crop_bytes, "image/jpeg"
                            )

                        await write_vehicle_attributes(
                            track_id=track_id,
                            event_id=None,
                            vehicle_type=veh.get("vehicle_type", "unknown"),
                            colour_top3=veh.get("colour_top3", []),
                            make_model_top3=veh.get("make_model_top3", []),
                            speed_kph=veh.get("speed_info", {}).get("kph"),
                            speed_reliability=veh.get("speed_info", {}).get("reliability"),
                        )

                        await hset_vehicle_track(
                            tracker_id=tracker_id,
                            drone_id=drone_id,
                            data={
                                "vehicle_type":      veh.get("vehicle_type", "unknown"),
                                "colour":            colour_label,
                                "track_id":          track_id,
                                "vehicle_image_key": veh_image_key or "",
                                "speed_kph":         str(veh.get("speed_info", {}).get("kph") or 0),
                                "ts":                ts.isoformat(),
                                "drone_id":          drone_id,
                            },
                            ttl=cfg.VEHICLE_TRACK_TTL,
                        )

                    await publish_analytics_result("vehicle", {
                        **result,
                        "drone_id":  drone_id,
                        "ts":        ts.isoformat(),
                        "frame_count": _frames_processed,
                    })
                    _frames_processed += 1

            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning("Vehicle stream error for %s: %s", drone_id, exc)
                await asyncio.sleep(1)


# ── Stream control ─────────────────────────────────────────────────────────────
@app.get("/stream/start")
async def stream_start():
    global _stream_running, _stream_task
    if _stream_running:
        return {"status": "already_running", "frames_processed": _frames_processed}
    _stream_running = True
    _stream_task = asyncio.create_task(_stream_loop())
    log.info("Vehicle stream loop STARTED")
    return {"status": "started"}


@app.get("/stream/stop")
async def stream_stop():
    global _stream_running, _stream_task
    _stream_running = False
    if _stream_task:
        _stream_task.cancel()
        _stream_task = None
    log.info("Vehicle stream loop STOPPED")
    return {"status": "stopped", "frames_processed": _frames_processed}


# ── Annotated frame endpoint ───────────────────────────────────────────────────
@app.get("/annotated/latest")
async def annotated_latest(drone_id: str = ""):
    """
    Returns the most recently annotated JPEG frame (with vehicle bounding boxes).
    If drone_id is specified and has a cached frame, returns that drone's frame.
    Falls back to any available cached frame.
    """
    frame_bytes = None
    if drone_id and drone_id in _last_annotated:
        frame_bytes = _last_annotated[drone_id]
    else:
        # Return the most recently updated frame from any drone
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


# ── Query endpoints ────────────────────────────────────────────────────────────
@app.get("/tracks/active")
async def active_tracks(limit: int = 100, drone_id: str = ""):
    """Return active vehicle tracks enriched with latest Redis data."""
    pool = await get_pool()
    query = """
        SELECT t.track_id, t.drone_id, t.metadata->>'tracker_id' AS tracker_id, t.first_seen, t.last_seen,
               va.vehicle_type, va.colour_top3, va.speed_kph
        FROM tracks t
        LEFT JOIN vehicle_attributes va ON va.track_id = t.track_id
        WHERE t.track_type = 'vehicle'
          AND t.last_seen > now() - interval '10 minutes'
    """
    params = []
    if drone_id:
        query += " AND t.drone_id = $1"
        params.append(drone_id)
    query += f" ORDER BY t.last_seen DESC LIMIT ${len(params)+1}"
    params.append(min(limit, 200))

    rows = await pool.fetch(query, *params)

    tracks = []
    for r in rows:
        d = {k: (str(v) if hasattr(v, "isoformat") else v) for k, v in dict(r).items()}
        # Enrich from Redis
        tid = d.get("tracker_id")
        did = d.get("drone_id", "")
        if tid:
            try:
                redis_data = await hget_vehicle_track(int(tid), did)
                d["colour"]     = redis_data.get("colour", "")
                d["plate_text"] = redis_data.get("plate_text", "")
                d["speed_kmh"]  = float(redis_data.get("speed_kph", d.get("speed_kph") or 0))
                d["state"]      = "Moving" if d["speed_kmh"] > 3 else "Stopped"
            except Exception:
                pass
        d.setdefault("colour", "")
        d.setdefault("plate_text", "")
        d.setdefault("speed_kmh", float(d.get("speed_kph") or 0))
        d.setdefault("state", "Moving" if d.get("speed_kmh", 0) > 3 else "Stopped")
        tracks.append(d)

    return {"count": len(tracks), "tracks": tracks}


@app.get("/vehicles/recent")
async def recent_vehicles(limit: int = 50):
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT t.track_id, t.drone_id, t.metadata->>'tracker_id' AS tracker_id, t.last_seen,
               va.vehicle_type, va.colour_top3, va.speed_kph
        FROM tracks t
        LEFT JOIN vehicle_attributes va ON va.track_id = t.track_id
        WHERE t.track_type = 'vehicle'
        ORDER BY t.last_seen DESC
        LIMIT $1
        """,
        min(limit, 200),
    )
    return {
        "count": len(rows),
        "vehicles": [{k: (str(v) if hasattr(v, "isoformat") else v)
                      for k, v in dict(r).items()} for r in rows],
    }


@app.get("/vehicles/{tracker_id}/plate")
async def get_vehicle_plate(tracker_id: int, drone_id: str = "DRONE_KL_001"):
    redis_data = await hget_vehicle_track(tracker_id, drone_id)
    plate_text = redis_data.get("plate_text")
    if not plate_text:
        link = await get_vehicle_plate_link(tracker_id, drone_id)
        if link:
            plate_text = link.get("plate_text")
            redis_data.update(link)
    return {"tracker_id": tracker_id, "drone_id": drone_id, "plate_text": plate_text, "data": redis_data}


@app.get("/vehicles/{tracker_id}/image")
async def get_vehicle_image(tracker_id: int, drone_id: str = "DRONE_KL_001"):
    redis_data = await hget_vehicle_track(tracker_id, drone_id)
    image_key  = redis_data.get("vehicle_image_key")
    if not image_key:
        raise HTTPException(404, "No image stored for this vehicle")
    url = await minio.get_presigned_url(cfg.BUCKET_VEHICLES, image_key, cfg.MINIO_URL_EXPIRY)
    return {"tracker_id": tracker_id, "image_url": url}


@app.get("/stats/summary")
async def stats_summary():
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT vehicle_type, COUNT(*) as cnt FROM vehicle_attributes GROUP BY vehicle_type"
    )
    by_type   = {r["vehicle_type"]: r["cnt"] for r in rows}
    total     = sum(by_type.values())
    speed_row = await pool.fetchrow(
        "SELECT AVG(speed_kph) as avg_speed FROM vehicle_attributes WHERE speed_kph IS NOT NULL"
    )
    active_row = await pool.fetchrow(
        "SELECT COUNT(*) as cnt FROM tracks WHERE track_type='vehicle' AND last_seen > now() - interval '5 minutes'"
    )
    plates_row = await pool.fetchrow(
        "SELECT COUNT(*) as cnt FROM plate_reads WHERE created_at > now() - interval '24 hours'"
    )
    return {
        "total_detected":   total,
        "total_tracked":    active_row["cnt"] if active_row else 0,
        "total_plates":     plates_row["cnt"] if plates_row else 0,
        "by_type":          by_type,
        "avg_speed_kmh":    round(float(speed_row["avg_speed"]), 1) if speed_row and speed_row["avg_speed"] else 0,
    }


@app.get("/stats/by-colour")
async def stats_by_colour():
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT
            colour_top3->0->>'colour' AS colour,
            COUNT(*) AS cnt
        FROM vehicle_attributes
        WHERE colour_top3 IS NOT NULL AND colour_top3 != '[]'::jsonb
        GROUP BY colour_top3->0->>'colour'
        ORDER BY cnt DESC
        LIMIT 12
        """
    )
    return {"by_colour": [{"colour": r["colour"] or "Unknown", "count": r["cnt"]} for r in rows]}


@app.get("/stats/flow/24h")
async def stats_flow_24h():
    """24-hour vehicle flow time series, broken down by type per hour."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT
            date_trunc('hour', t.last_seen) AS hour_ts,
            va.vehicle_type,
            COUNT(*) AS cnt
        FROM tracks t
        LEFT JOIN vehicle_attributes va ON va.track_id = t.track_id
        WHERE t.track_type = 'vehicle'
          AND t.last_seen > now() - interval '24 hours'
        GROUP BY hour_ts, va.vehicle_type
        ORDER BY hour_ts ASC
        """
    )
    # Build per-hour dict
    hours: dict = {}
    for r in rows:
        ht = r["hour_ts"]
        if ht is None:
            continue
        label  = ht.strftime("%H:%M") if hasattr(ht, "strftime") else str(ht)[:5]
        vtype  = (r["vehicle_type"] or "unknown").lower()
        cnt    = r["cnt"]
        if label not in hours:
            hours[label] = {"hour": label, "cars": 0, "two_wheelers": 0, "heavy": 0, "other": 0}
        if vtype == "car":
            hours[label]["cars"] += cnt
        elif vtype in ("motorcycle", "bicycle", "two-wheeler"):
            hours[label]["two_wheelers"] += cnt
        elif vtype in ("truck", "bus"):
            hours[label]["heavy"] += cnt
        else:
            hours[label]["other"] += cnt

    timeline = list(hours.values())
    return {
        "hourly_timeline": timeline,
        "total_hours": len(timeline),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8103, reload=False)
