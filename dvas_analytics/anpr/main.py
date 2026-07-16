# -*- coding: utf-8 -*-
"""
ANPR Service — FastAPI app on port 8104.
License plate recognition with PaddleOCR, theft watchlist, and Postgres persistence.
"""
import asyncio
import base64
import logging
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional, Set

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, "/analytics")
from shared.config import cfg
from shared.db import (
    add_to_watchlist, close_pool, ensure_drone, get_pool, get_recent_plates,
    get_watchlist, remove_from_watchlist, upsert_track,
    write_confirmed_event, write_plate_read,
)
from shared.redis_client import (
    close_client, publish_analytics_result, publish_confirmed_event, xread_frames,
)
from anpr.analyzer import ANPRAnalyzer
from anpr.schema import ANPRFrameResult, WatchlistEntry

logging.basicConfig(level=cfg.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("anpr-service")

app = FastAPI(
    title="DVAS ANPR Service",
    description="License plate recognition with theft watchlist — aerial drone footage",
    version="1.0.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

VEHICLE_MODEL = os.getenv("VEHICLE_MODEL", "/analytics/anpr/models/yolov8m.pt")
LP_MODEL      = os.getenv("LP_MODEL",      "/analytics/anpr/models/license_plate_detector.pt")

analyzer: Optional[ANPRAnalyzer] = None
_stream_task: Optional[asyncio.Task] = None
_stream_running = False
_frames_processed = 0


async def _load_watchlist_from_db() -> Set[str]:
    try:
        rows = await get_watchlist()
        return {r["plate_text"] for r in rows}
    except Exception:
        return set()


@app.on_event("startup")
async def startup():
    global analyzer
    await get_pool()
    for drone_id in cfg.DRONE_IDS:
        await ensure_drone(drone_id)
    watchlist = await _load_watchlist_from_db()
    analyzer  = ANPRAnalyzer(
        vehicle_model_path=VEHICLE_MODEL,
        lp_model_path=LP_MODEL,
        watchlist=watchlist,
    )
    log.info("ANPR Service ready. Watchlist: %d plates", len(watchlist))


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
    ts     = datetime.now(timezone.utc)

    # Persist to Postgres — deduplication via ON CONFLICT (track_id)
    for plate in result["plate_reads"]:
        if not plate["plate_text"]:
            continue
        track_id = await upsert_track(drone_id, None, "vehicle", ts, ts)
        plate["track_id"] = track_id
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
        # Store plate with vehicle image — ON CONFLICT deduplicates by track_id
        await write_plate_read(
            track_id=track_id,
            event_id=event_id,
            plate_text=plate["plate_text"],
            ocr_confidence=plate["ocr_confidence"],
            is_reliable=plate["is_reliable"],
            vehicle_image_b64=plate.get("vehicle_image_b64"),
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
            # Log theft alert
            pool = await get_pool()
            await pool.execute(
                """
                INSERT INTO theft_alerts
                    (plate_text, track_id, drone_id, detected_at, frame_ts,
                     confidence, vehicle_type)
                VALUES ($1, $2::uuid, $3, $4, $4, $5, $6)
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
                new_id, frames = await xread_frames(drone_id, last_ids[drone_id], count=2, block_ms=100)
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
                            track_id = await upsert_track(drone_id, None, "vehicle", ts, ts)
                            await write_confirmed_event(
                                drone_id=drone_id,
                                event_type="vehicle_detected",
                                confidence=plate["ocr_confidence"], ts=ts, track_id=track_id,
                                attributes={"plate_text": plate["plate_text"]},
                            )
                            await write_plate_read(
                                track_id=track_id, event_id=None,
                                plate_text=plate["plate_text"],
                                ocr_confidence=plate["ocr_confidence"],
                                is_reliable=plate["is_reliable"],
                                vehicle_image_b64=plate.get("vehicle_image_b64"),
                            )
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8104, reload=False)
