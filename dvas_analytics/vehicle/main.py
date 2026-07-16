# -*- coding: utf-8 -*-
"""
Vehicle Analyzer Service — FastAPI app on port 8103.
"""
import asyncio
import base64
import logging
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

sys.path.insert(0, "/analytics")
from shared.config import cfg
from shared.db import (
    close_pool, ensure_drone, get_pool, upsert_track,
    write_confirmed_event, write_vehicle_attributes,
)
from shared.redis_client import (
    close_client, publish_analytics_result, publish_confirmed_event, xread_frames,
)
from vehicle.analyzer import VehicleAnalyzer
from vehicle.schema import VehicleFrameResult, VehicleStats

logging.basicConfig(level=cfg.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("vehicle-service")

app = FastAPI(
    title="DVAS Vehicle Analyzer",
    description="Vehicle detection, tracking and attribute classification service",
    version="1.0.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

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
    ts     = datetime.now(timezone.utc)

    for veh in result["vehicles"]:
        track_id = await upsert_track(drone_id, None, "vehicle", ts, ts)
        veh["track_id"] = track_id
        event_id = await write_confirmed_event(
            drone_id=drone_id, event_type="vehicle_detected",
            confidence=veh["confidence"], ts=ts, track_id=track_id,
            attributes={"vehicle_type": veh["vehicle_type"]},
        )
        await write_vehicle_attributes(
            track_id=track_id,
            event_id=event_id,
            vehicle_type=veh["vehicle_type"],
            colour_top3=veh.get("colour_top3", []),
            make_model_top3=veh.get("make_model_top3", []),
            speed_kph=veh.get("speed_info", {}).get("kph"),
            speed_reliability=veh.get("speed_info", {}).get("reliability"),
            direction_label=veh.get("speed_info", {}).get("direction_label"),
        )
        await publish_confirmed_event({
            "event_id":    event_id,
            "event_type":  "vehicle_detected",
            "drone_id":    drone_id,
            "ts":          ts.isoformat(),
            "vehicle_type": veh["vehicle_type"],
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
                new_id, frames = await xread_frames(drone_id, last_ids[drone_id], count=4, block_ms=100)
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
                        track_id = await upsert_track(drone_id, None, "vehicle", ts, ts)
                        await write_confirmed_event(
                            drone_id=drone_id, event_type="vehicle_detected",
                            confidence=veh["confidence"], ts=ts, track_id=track_id,
                        )
                    _frames_processed += 1
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning("Stream error for %s: %s", drone_id, exc)
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
    return [{k: v for k, v in dict(r).items()} for r in rows]


@app.get("/vehicles/recent")
async def recent_vehicles(limit: int = 50):
    from shared.db import get_recent_confirmed_events
    rows = await get_recent_confirmed_events(limit=limit)
    vehicles = [r for r in rows if r.get("event_type") == "vehicle_detected"]
    return {"count": len(vehicles), "vehicles": vehicles}


@app.get("/stats/summary")
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8103, reload=False)
