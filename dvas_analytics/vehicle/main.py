# -*- coding: utf-8 -*-
"""
Vehicle Analyzer Service — FastAPI on port 8103.
Vehicle detection, tracking, colour classification, speed estimation.
Publishes track info to Redis for ANPR linkage.
"""
import asyncio
import base64
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

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


@app.get("/health")
async def health():
    return {
        "status":           "ok",
        "service":          "vehicle-analyzer",
        "model_loaded":     analyzer.ready if analyzer else False,
        "stream_running":   _stream_running,
        "frames_processed": _frames_processed,
    }


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

        colour_label = veh["colour_top3"][0]["colour"] if veh["colour_top3"] else None

        await write_vehicle_attributes(
            track_id=track_id,
            event_id=event_id,
            vehicle_type=veh["vehicle_type"],
            colour_top3=veh.get("colour_top3", []),
            make_model_top3=veh.get("make_model_top3", []),
            speed_kph=veh.get("speed_info", {}).get("kph"),
            speed_reliability=veh.get("speed_info", {}).get("reliability"),
        )

        # Upload vehicle crop to MinIO
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

        # Publish vehicle track info to Redis for ANPR linkage
        await hset_vehicle_track(
            tracker_id=tracker_id,
            drone_id=drone_id,
            data={
                "vehicle_type":    veh["vehicle_type"],
                "colour":          colour_label or "",
                "colour_top3":     veh["colour_top3"],
                "track_id":        track_id,
                "vehicle_image_key": veh_image_key or "",
                "ts":              ts.isoformat(),
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


async def _stream_loop():
    global _stream_running, _frames_processed
    last_ids = {d: "0" for d in cfg.DRONE_IDS}
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
                    nparr = np.frombuffer(img_bytes, np.uint8)
                    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    if frame is None:
                        continue

                    result = analyzer.analyze_frame(frame, drone_id=drone_id, save_crops=True)
                    ts = datetime.now(timezone.utc)

                    for veh in result["vehicles"]:
                        tracker_id = veh.get("tracker_id")
                        track_id = await upsert_track(
                            drone_id, None, "vehicle", ts, ts, tracker_id=tracker_id
                        )
                        await write_confirmed_event(
                            drone_id=drone_id, event_type="vehicle_detected",
                            confidence=veh["confidence"], ts=ts, track_id=track_id,
                        )

                        colour_label = veh["colour_top3"][0]["colour"] if veh.get("colour_top3") else None
                        crop_bytes = veh.pop("crop_bytes", None)
                        veh_image_key = None
                        if crop_bytes:
                            key = minio.make_key("vehicles", drone_id,
                                                 f"v_{tracker_id}_{int(ts.timestamp()*1000)}.jpg")
                            veh_image_key = await minio.upload_bytes(
                                cfg.BUCKET_VEHICLES, key, crop_bytes, "image/jpeg"
                            )

                        await hset_vehicle_track(
                            tracker_id=tracker_id,
                            drone_id=drone_id,
                            data={
                                "vehicle_type": veh["vehicle_type"],
                                "colour":       colour_label or "",
                                "track_id":     track_id,
                                "vehicle_image_key": veh_image_key or "",
                            },
                            ttl=cfg.VEHICLE_TRACK_TTL,
                        )

                    await publish_analytics_result("vehicle", result)
                    _frames_processed += 1

            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning("Vehicle stream error for %s: %s", drone_id, exc)
                await asyncio.sleep(1)


@app.get("/stream/start")
async def stream_start():
    global _stream_running, _stream_task
    if _stream_running:
        return {"status": "already_running"}
    _stream_running = True
    _stream_task = asyncio.create_task(_stream_loop())
    return {"status": "started"}


@app.get("/stream/stop")
async def stream_stop():
    global _stream_running, _stream_task
    _stream_running = False
    if _stream_task:
        _stream_task.cancel()
        _stream_task = None
    return {"status": "stopped"}


@app.get("/tracks/active")
async def active_tracks():
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM tracks WHERE track_type='vehicle' ORDER BY last_seen DESC LIMIT 50"
    )
    return [{k: str(v) if hasattr(v, 'isoformat') else v for k, v in dict(r).items()} for r in rows]


@app.get("/vehicles/recent")
async def recent_vehicles(limit: int = 50):
    from shared.db import get_recent_confirmed_events
    rows = await get_recent_confirmed_events(limit=limit)
    vehicles = [r for r in rows if r.get("event_type") == "vehicle_detected"]
    return {"count": len(vehicles), "vehicles": vehicles}


@app.get("/vehicles/{tracker_id}/plate")
async def get_vehicle_plate(tracker_id: int, drone_id: str = "DRONE_KL_001"):
    """Get plate info for a vehicle tracker_id from Redis (fast) or DB (fallback)."""
    redis_data = await hget_vehicle_track(tracker_id, drone_id)
    plate_text = redis_data.get("plate_text")
    if not plate_text:
        # Fallback to DB
        link = await get_vehicle_plate_link(tracker_id, drone_id)
        if link:
            plate_text = link.get("plate_text")
            redis_data.update(link)
    return {
        "tracker_id": tracker_id,
        "drone_id":   drone_id,
        "plate_text": plate_text,
        "data":       redis_data,
    }


@app.get("/vehicles/{tracker_id}/image")
async def get_vehicle_image(tracker_id: int, drone_id: str = "DRONE_KL_001"):
    """Get presigned MinIO URL for vehicle crop image."""
    redis_data = await hget_vehicle_track(tracker_id, drone_id)
    image_key = redis_data.get("vehicle_image_key")
    if not image_key:
        raise HTTPException(404, "No image stored for this vehicle")
    url = await minio.get_presigned_url(cfg.BUCKET_VEHICLES, image_key, cfg.MINIO_URL_EXPIRY)
    return {"tracker_id": tracker_id, "image_url": url}


@app.get("/stats/summary", response_model=VehicleStats)
async def stats_summary():
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT vehicle_type, COUNT(*) as cnt FROM vehicle_attributes GROUP BY vehicle_type"
    )
    by_type = {r["vehicle_type"]: r["cnt"] for r in rows}
    total   = sum(by_type.values())
    speed_row = await pool.fetchrow(
        "SELECT AVG(speed_kph) as avg_speed FROM vehicle_attributes WHERE speed_kph IS NOT NULL"
    )
    return VehicleStats(
        total_vehicles=total,
        by_type=by_type,
        avg_speed_kph=float(speed_row["avg_speed"]) if speed_row and speed_row["avg_speed"] else None,
    )


@app.get("/stats/by-colour")
async def stats_by_colour():
    """Vehicle counts grouped by dominant colour."""
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
        """
    )
    return {"by_colour": [{"colour": r["colour"], "count": r["cnt"]} for r in rows]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8103, reload=False)
