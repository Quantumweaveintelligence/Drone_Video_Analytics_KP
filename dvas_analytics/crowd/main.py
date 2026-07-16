# -*- coding: utf-8 -*-
"""
Crowd Analyzer Service — FastAPI app on port 8101.
Endpoints for frame analysis, Valkey stream consumption, and DB queries.
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
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Add analytics root to path so shared/ is importable
sys.path.insert(0, "/analytics")
from shared.config import cfg
from shared.db import (
    close_pool, get_pool, write_crowd_reading, get_crowd_history,
    write_confirmed_event, ensure_drone,
)
from shared.redis_client import (
    close_client, publish_analytics_result, publish_confirmed_event, xread_frames,
)
from crowd.analyzer import CrowdAnalyzer
from crowd.schema import CrowdHistoryItem, CrowdAnalysisResult, StreamStatus

logging.basicConfig(level=cfg.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("crowd-service")

app = FastAPI(
    title="DVAS Crowd Analyzer",
    description="Real-time crowd counting and density estimation service",
    version="1.0.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Global state ───────────────────────────────────────────────────────────────
MODEL_PATH = os.getenv("MODEL_PATH", "/analytics/crowd/models/crowd_counting.pth")
analyzer: Optional[CrowdAnalyzer] = None
_stream_task: Optional[asyncio.Task] = None
_stream_running = False
_frames_processed = 0


# ── Startup / shutdown ─────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    global analyzer
    await get_pool()  # warm up DB pool
    for drone_id in cfg.DRONE_IDS:
        await ensure_drone(drone_id)
    analyzer = CrowdAnalyzer(
        model_path=MODEL_PATH,
        alert_threshold=cfg.CROWD_ALERT_THRESHOLD,
    )
    log.info("Crowd Analyzer ready. Model: %s, Device: %s", MODEL_PATH, analyzer.device)


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
        "status": "ok",
        "service": "crowd-analyzer",
        "model_loaded": analyzer.ready if analyzer else False,
        "stream_running": _stream_running,
        "frames_processed": _frames_processed,
    }


# ── Single frame analysis ──────────────────────────────────────────────────────
@app.post("/analyze/frame", response_model=CrowdAnalysisResult)
async def analyze_frame(
    file: UploadFile = File(...),
    drone_id: str = Form(default="DRONE_KL_001"),
    include_heatmap: bool = Form(default=True),
):
    if not analyzer or not analyzer.ready:
        raise HTTPException(503, "Model not loaded")

    contents = await file.read()
    nparr    = np.frombuffer(contents, np.uint8)
    frame    = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if frame is None:
        raise HTTPException(400, "Cannot decode image")

    result = analyzer.analyze_frame(frame, drone_id=drone_id, include_heatmap=include_heatmap)

    # Persist to DB
    ts = datetime.now(timezone.utc)
    await write_crowd_reading(
        drone_id=drone_id,
        ts=ts,
        estimated_count=result["estimated_count"],
    )
    # Publish confirmed event if threshold crossed
    if result["alert_triggered"]:
        event_id = await write_confirmed_event(
            drone_id=drone_id,
            event_type="crowd_threshold",
            confidence=min(result["estimated_count"] / 200, 1.0),
            ts=ts,
            attributes={
                "estimated_count": result["estimated_count"],
                "density_label":   result["density_label"],
            },
        )
        await publish_confirmed_event({
            "event_id":     event_id,
            "event_type":   "crowd_threshold",
            "drone_id":     drone_id,
            "ts":           ts.isoformat(),
            "count":        result["estimated_count"],
            "density":      result["density_label"],
        })

    await publish_analytics_result("crowd", result)
    return result


# ── Stream control ─────────────────────────────────────────────────────────────
async def _stream_loop():
    global _stream_running, _frames_processed
    last_ids = {d: "0" for d in cfg.DRONE_IDS}
    log.info("Stream loop started for drones: %s", cfg.DRONE_IDS)

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
                    result = analyzer.analyze_frame(frame, drone_id=drone_id, include_heatmap=False)
                    ts = datetime.now(timezone.utc)
                    await write_crowd_reading(drone_id=drone_id, ts=ts, estimated_count=result["estimated_count"])
                    if result["alert_triggered"]:
                        event_id = await write_confirmed_event(
                            drone_id=drone_id, event_type="crowd_threshold",
                            confidence=min(result["estimated_count"] / 200, 1.0), ts=ts,
                            attributes={"estimated_count": result["estimated_count"],
                                        "density_label": result["density_label"]},
                        )
                        await publish_confirmed_event({"event_id": event_id, "event_type": "crowd_threshold",
                                                       "drone_id": drone_id, "ts": ts.isoformat(),
                                                       "count": result["estimated_count"]})
                    _frames_processed += 1
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning("Stream loop error for %s: %s", drone_id, exc)
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


@app.get("/stream/status", response_model=StreamStatus)
async def stream_status():
    return StreamStatus(
        running=_stream_running,
        drone_ids=cfg.DRONE_IDS,
        frames_processed=_frames_processed,
    )


# ── DB queries ─────────────────────────────────────────────────────────────────
@app.get("/readings/latest")
async def latest_reading(drone_id: Optional[str] = None):
    rows = await get_crowd_history(drone_id=drone_id, limit=1)
    if not rows:
        raise HTTPException(404, "No readings found")
    return rows[0]


@app.get("/readings/history")
async def history(drone_id: Optional[str] = None, limit: int = 100):
    rows = await get_crowd_history(drone_id=drone_id, limit=min(limit, 500))
    return {"count": len(rows), "readings": rows}


@app.get("/density/heatmap")
async def latest_heatmap(drone_id: str = "DRONE_KL_001"):
    """Return latest heatmap — analyze most recent frame from stream."""
    # For now return placeholder; in production hook to latest stored heatmap
    return JSONResponse({"message": "Use /analyze/frame to get a heatmap for a specific frame"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8101, reload=False)
