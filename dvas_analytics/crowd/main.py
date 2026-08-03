# -*- coding: utf-8 -*-
"""
Crowd Analyzer Service — FastAPI on port 8101.
Real-time crowd counting, density estimation, and heatmap persistence via MinIO.
"""
import asyncio
import base64
import hashlib
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

sys.path.insert(0, "/analytics")
from shared.config import cfg
from shared.db import (
    close_pool, ensure_drone, get_pool, write_confirmed_event,
    write_crowd_reading, get_crowd_history,
)
from shared.redis_client import (
    close_client, get_client, publish_analytics_result,
    publish_confirmed_event, xread_frames,
)
from shared import minio_client as minio
from crowd.analyzer import CrowdAnalyzer
from crowd.schema import CrowdAnalysisResult, StreamStatus, CrowdTimelineItem

logging.basicConfig(
    level=cfg.LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("crowd-service")

app = FastAPI(
    title="DVAS Crowd Analyzer",
    description="Real-time crowd counting, density estimation, and heatmap service",
    version="2.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MODEL_PATH = os.getenv("MODEL_PATH", "/analytics/crowd/models/crowd_counting.pth")
analyzer: Optional[CrowdAnalyzer] = None
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


# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status":           "ok",
        "service":          "crowd-analyzer",
        "model_loaded":     analyzer.ready if analyzer else False,
        "stream_running":   _stream_running,
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
    ts = datetime.now(timezone.utc)

    # Upload heatmap to MinIO if available
    heatmap_key = None
    heatmap_url  = None
    if result.get("heatmap_b64"):
        heatmap_bytes = base64.b64decode(result["heatmap_b64"])
        key = minio.make_key("heatmaps", drone_id, f"heatmap_{int(ts.timestamp()*1000)}.png")
        heatmap_key = await minio.upload_bytes(
            cfg.BUCKET_HEATMAPS, key, heatmap_bytes, "image/png"
        )
        if heatmap_key:
            heatmap_url = await minio.get_presigned_url(
                cfg.BUCKET_HEATMAPS, heatmap_key, cfg.MINIO_URL_EXPIRY
            )

    await write_crowd_reading(
        drone_id=drone_id,
        ts=ts,
        estimated_count=result["estimated_count"],
        heatmap_object_key=heatmap_key,
    )

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
            "event_id":   event_id,
            "event_type": "crowd_threshold",
            "drone_id":   drone_id,
            "ts":         ts.isoformat(),
            "count":      result["estimated_count"],
            "density":    result["density_label"],
        })

    result["heatmap_url"] = heatmap_url
    await publish_analytics_result("crowd", result)
    return result


# ── Stream control ─────────────────────────────────────────────────────────────
async def _stream_loop():
    global _stream_running, _frames_processed
    last_ids = {d: "0" for d in cfg.DRONE_IDS}
    log.info("Crowd stream loop started for drones: %s", cfg.DRONE_IDS)

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

                    result = analyzer.analyze_frame(
                        frame, drone_id=drone_id, include_heatmap=False
                    )
                    ts = datetime.now(timezone.utc)

                    heatmap_key = None
                    # Upload heatmap every 10 frames to save bandwidth
                    if _frames_processed % 10 == 0 and result.get("heatmap_b64"):
                        heatmap_bytes = base64.b64decode(result["heatmap_b64"])
                        key = minio.make_key("heatmaps", drone_id,
                                             f"heatmap_{int(ts.timestamp()*1000)}.png")
                        heatmap_key = await minio.upload_bytes(
                            cfg.BUCKET_HEATMAPS, key, heatmap_bytes, "image/png"
                        )

                    await write_crowd_reading(
                        drone_id=drone_id,
                        ts=ts,
                        estimated_count=result["estimated_count"],
                        heatmap_object_key=heatmap_key,
                    )

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
                            "event_id":   event_id,
                            "event_type": "crowd_threshold",
                            "drone_id":   drone_id,
                            "ts":         ts.isoformat(),
                            "count":      result["estimated_count"],
                        })

                    await publish_analytics_result("crowd", result)
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


# ── DB queries ──────────────────────────────────────────────────────────────────
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


@app.get("/density/timeline")
async def density_timeline(drone_id: Optional[str] = None, limit: int = 100):
    """Time-series crowd counts for dashboard charts."""
    rows = await get_crowd_history(drone_id=drone_id, limit=min(limit, 500))
    timeline = [
        {
            "ts":              r["ts"].isoformat() if hasattr(r["ts"], "isoformat") else str(r["ts"]),
            "estimated_count": r["estimated_count"],
            "drone_id":        r["drone_id"],
        }
        for r in rows
    ]
    # Return oldest-first for charting
    return {"count": len(timeline), "timeline": list(reversed(timeline))}


@app.get("/density/heatmap/latest")
async def latest_heatmap(drone_id: str = "DRONE_KL_001"):
    """Return presigned MinIO URL for the latest stored heatmap."""
    rows = await get_crowd_history(drone_id=drone_id, limit=20)
    # Find most recent row with a heatmap key
    for row in rows:
        key = row.get("heatmap_object_key")
        if key:
            url = await minio.get_presigned_url(
                cfg.BUCKET_HEATMAPS, key, cfg.MINIO_URL_EXPIRY
            )
            return {
                "drone_id":        drone_id,
                "heatmap_url":     url,
                "estimated_count": row["estimated_count"],
                "ts":              row["ts"].isoformat() if hasattr(row["ts"], "isoformat") else str(row["ts"]),
            }
    raise HTTPException(404, "No heatmap stored yet — use /analyze/frame first")


@app.get("/count/live")
async def count_live():
    """
    SSE stream of live crowd counts.
    Reads from analytics:crowd:results Redis stream.
    """
    async def event_generator() -> AsyncGenerator[str, None]:
        client = get_client()
        last_id = "$"  # Only new messages
        try:
            while True:
                results = await client.xread(
                    {"analytics:crowd:results": last_id},
                    count=1,
                    block=5000,
                )
                if results:
                    for _stream, entries in results:
                        for entry_id, fields in entries:
                            last_id = entry_id
                            import json
                            data = fields.get("data", "{}")
                            yield f"data: {data}\n\n"
                else:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8101, reload=False)
