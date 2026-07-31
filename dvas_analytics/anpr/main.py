# -*- coding: utf-8 -*-
"""
ANPR Analyzer Service — FastAPI on port 8104.
License plate recognition, watchlist management, theft alerts.
Stores plate crops in MinIO. Links plates to vehicle tracker via Redis.
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
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, "/analytics")
from shared.config import cfg
from shared.db import (
    close_pool, ensure_drone, get_pool, upsert_track,
    write_confirmed_event, write_plate_read,
    get_recent_plates, add_to_watchlist, remove_from_watchlist,
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


@app.get("/health")
async def health():
    return {
        "status":           "ok",
        "service":          "anpr-analyzer",
        "model_loaded":     analyzer.ready if analyzer else False,
        "ocr_ready":        analyzer.ocr_engine is not None if analyzer else False,
        "stream_running":   _stream_running,
        "frames_processed": _frames_processed,
        "watchlist_count":  len(analyzer.watchlist) if analyzer else 0,
    }


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
    ts = datetime.now(timezone.utc)

    for plate in result["plate_reads"]:
        if not plate["plate_text"]:
            continue

        tracker_id = plate.get("tracker_id")
        track_id = await upsert_track(
            drone_id, None, "vehicle", ts, ts, tracker_id=tracker_id
        )
        plate["track_id"] = track_id

        # Upload plate image to MinIO
        plate_image_key = None
        vehicle_image_key = None
        veh_img_b64 = plate.get("vehicle_image_b64")
        if veh_img_b64:
            veh_img_bytes = base64.b64decode(veh_img_b64)
            key = minio.make_key("plates", drone_id,
                                 f"vehicle_{tracker_id}_{int(ts.timestamp()*1000)}.jpg")
            vehicle_image_key = await minio.upload_bytes(
                cfg.BUCKET_VEHICLES, key, veh_img_bytes, "image/jpeg"
            )

        # Write plate crop separately if available (from analyzer)
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
            ts=ts,
            track_id=track_id,
            attributes={
                "plate_text":   plate["plate_text"],
                "vehicle_type": plate["vehicle_type"],
                "is_theft":     plate["is_watchlisted"],
            },
        )

        await write_plate_read(
            track_id=track_id,
            event_id=event_id,
            plate_text=plate["plate_text"],
            ocr_confidence=plate["ocr_confidence"],
            is_reliable=plate["is_reliable"],
            plate_image_key=plate_image_key,
            vehicle_image_key=vehicle_image_key,
        )

        # Write vehicle-plate link to DB
        if tracker_id is not None:
            # Get colour from Redis vehicle tracker
            veh_data = await hget_vehicle_track(tracker_id, drone_id)
            colour = veh_data.get("colour")

            await write_vehicle_plate_link(
                tracker_id=tracker_id,
                drone_id=drone_id,
                plate_text=plate["plate_text"],
                plate_conf=plate["ocr_confidence"],
                vehicle_type=plate["vehicle_type"],
                colour=colour,
                plate_image_key=plate_image_key,
                vehicle_image_key=vehicle_image_key or veh_data.get("vehicle_image_key"),
            )

            # Update Redis with plate info so vehicle service can read it
            await hset_vehicle_track(
                tracker_id=tracker_id,
                drone_id=drone_id,
                data={
                    "plate_text": plate["plate_text"],
                    "plate_conf": plate["ocr_confidence"],
                    "plate_image_key": plate_image_key or "",
                },
                ttl=cfg.VEHICLE_TRACK_TTL,
            )

        if plate["is_watchlisted"]:
            await publish_confirmed_event({
                "event_id":    event_id,
                "event_type":  "theft_alert",
                "drone_id":    drone_id,
                "ts":          ts.isoformat(),
                "plate_text":  plate["plate_text"],
                "vehicle_type": plate["vehicle_type"],
                "confidence":  plate["ocr_confidence"],
            })
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
                plate["ocr_confidence"], plate["vehicle_type"],
            )

    await publish_analytics_result("anpr", result)
    return result


async def _stream_loop():
    global _stream_running, _frames_processed
    last_ids = {d: "0" for d in cfg.DRONE_IDS}
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
                    nparr = np.frombuffer(img_bytes, np.uint8)
                    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    if frame is None:
                        continue

                    result = analyzer.analyze_frame(frame, drone_id=drone_id)
                    ts = datetime.now(timezone.utc)

                    for plate in result["plate_reads"]:
                        if plate["plate_text"] and plate["is_reliable"]:
                            tracker_id = plate.get("tracker_id")
                            track_id = await upsert_track(
                                drone_id, None, "vehicle", ts, ts,
                                tracker_id=tracker_id
                            )
                            plate_image_key = None
                            plate_crop_b64 = plate.pop("plate_crop_b64", None)
                            if plate_crop_b64:
                                plate_bytes = base64.b64decode(plate_crop_b64)
                                key = minio.make_key(
                                    "plates", drone_id,
                                    f"plate_{plate['plate_text']}_{int(ts.timestamp()*1000)}.jpg"
                                )
                                plate_image_key = await minio.upload_bytes(
                                    cfg.BUCKET_PLATES, key, plate_bytes, "image/jpeg"
                                )

                            await write_plate_read(
                                track_id=track_id,
                                event_id=None,
                                plate_text=plate["plate_text"],
                                ocr_confidence=plate["ocr_confidence"],
                                is_reliable=plate["is_reliable"],
                                plate_image_key=plate_image_key,
                            )

                            if tracker_id is not None:
                                await hset_vehicle_track(
                                    tracker_id=tracker_id,
                                    drone_id=drone_id,
                                    data={
                                        "plate_text": plate["plate_text"],
                                        "plate_conf": plate["ocr_confidence"],
                                    },
                                    ttl=cfg.VEHICLE_TRACK_TTL,
                                )

                    await publish_analytics_result("anpr", result)
                    _frames_processed += 1

            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning("ANPR stream error for %s: %s", drone_id, exc)
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


# ── Plate queries ──────────────────────────────────────────────────────────────
@app.get("/plates/recent")
async def recent_plates(limit: int = 50):
    rows = await get_recent_plates(limit=min(limit, 200))
    return {"count": len(rows), "plates": rows}


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
    """Get presigned MinIO URL for the latest plate crop image."""
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
    rows = await pool.fetch(
        "SELECT * FROM theft_alerts ORDER BY detected_at DESC LIMIT $1", limit
    )
    return {"count": len(rows), "alerts": [dict(r) for r in rows]}


@app.get("/alerts/theft/live")
async def theft_alerts_live():
    """SSE stream of live theft alerts from confirmed events stream."""
    from shared.redis_client import get_client
    import json

    async def event_gen():
        client = get_client()
        last_id = "$"
        try:
            while True:
                results = await client.xread(
                    {"drone:events:confirmed": last_id},
                    count=5, block=5000,
                )
                if results:
                    for _stream, entries in results:
                        for entry_id, fields in entries:
                            last_id = entry_id
                            data_str = fields.get("data", "{}")
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
