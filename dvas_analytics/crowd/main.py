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
from fastapi.responses import JSONResponse, StreamingResponse, Response

sys.path.insert(0, "/analytics")
from shared.config import cfg
from shared.db import (
    close_pool, ensure_drone, get_pool, write_confirmed_event,
    write_crowd_reading, get_crowd_history,
)
from shared.redis_client import (
    close_client, get_client, publish_analytics_result,
    publish_confirmed_event, xread_frames, xreadgroup_frames, ack_frames
)
from shared import minio_client as minio
from crowd.analyzer import CrowdAnalyzer
from crowd.schema import CrowdAnalysisResult, StreamStatus, CrowdTimelineItem, ModelSwitchRequest, ModelInfo, SessionSummary

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

MODEL_PATH = os.getenv("MODEL_PATH", "/analytics/crowd/models/PartAmodel_best.pth")
if not os.path.isfile(MODEL_PATH) or "crowd_counting.pth" in MODEL_PATH:
    MODEL_PATH = "/analytics/crowd/models/PartAmodel_best.pth"

MODEL_PATHS = {
    "csrnet_parta": "/analytics/crowd/models/PartAmodel_best.pth",
    "csrnet_partb": "/analytics/crowd/models/partBmodel_best.pth",
}
_current_model_type = "csrnet_parta"

analyzer: Optional[CrowdAnalyzer] = None
_stream_task: Optional[asyncio.Task] = None
_stream_running = False
_frames_processed = 0


@app.on_event("startup")
async def startup():
    global analyzer, _stream_running, _stream_task
    await get_pool()
    for drone_id in cfg.DRONE_IDS:
        await ensure_drone(drone_id)
    await minio.ensure_all_buckets()
    analyzer = CrowdAnalyzer(
        model_path=MODEL_PATH,
        alert_threshold=cfg.CROWD_ALERT_THRESHOLD,
    )
    log.info("Crowd Analyzer ready. Model: %s, Device: %s", MODEL_PATH, analyzer.device)
    _stream_running = True
    _stream_task = asyncio.create_task(_stream_loop())
    log.info("Crowd stream loop auto-started for drones: %s", cfg.DRONE_IDS)


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
        "model_type":       _current_model_type,
        "model_path":       analyzer._model_path if analyzer else "",
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
    active_drones = list(cfg.DRONE_IDS)
    last_ids = {d: "0" for d in active_drones}
    log.info("Crowd stream loop started for initial drones: %s", active_drones)

    loop_count = 0
    while _stream_running:
        loop_count += 1
        # Dynamically discover active stream drones from Valkey every ~10 iterations
        if loop_count % 10 == 1:
            try:
                from shared.redis_client import scan_active_stream_drones
                discovered = await scan_active_stream_drones()
                for d in discovered:
                    if d not in active_drones:
                        active_drones.append(d)
                        last_ids[d] = "0"
                        await ensure_drone(d)
                        log.info("Crowd stream loop auto-discovered active drone: %s", d)
            except Exception as exc:
                log.warning("Crowd stream scanner warning: %s", exc)

        any_frames_received = False
        for drone_id in list(active_drones):
            if drone_id not in last_ids:
                last_ids[drone_id] = "0"
            try:
                frames = await xreadgroup_frames(
                    "crowd-group", "crowd-analyzer", drone_id, ">", count=5, block_ms=0
                )
                if not frames:
                    continue

                any_frames_received = True

                # If multiple frames arrived, ACK earlier frames immediately to stay realtime
                processed_ids = [entry_id for entry_id, _ in frames[:-1]]

                # Process only the latest frame
                entry_id, f = frames[-1]
                raw = f.get("jpeg_b64", "")
                if not raw:
                    processed_ids.append(entry_id)
                else:
                    img_bytes = base64.b64decode(raw)
                    nparr = np.frombuffer(img_bytes, np.uint8)
                    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    if frame is None:
                        processed_ids.append(entry_id)
                    else:
                        # Run inference in worker thread pool so FastAPI event loop is never blocked
                        result = await asyncio.to_thread(
                            analyzer.analyze_frame, frame, drone_id=drone_id, include_heatmap=True
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

                        # Publish to SSE stream — strip heavy binary fields
                        sse_result = {k: v for k, v in result.items()
                                      if k not in ("heatmap_b64", "peak_frame")}
                        await publish_analytics_result("crowd", sse_result)
                        _frames_processed += 1
                        processed_ids.append(entry_id)

                if processed_ids:
                    await ack_frames(drone_id, "crowd-group", *processed_ids)

            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning("Stream loop error for %s: %s", drone_id, exc)
                await asyncio.sleep(1)

        if not any_frames_received:
            await asyncio.sleep(0.05)


@app.get("/stream/start")
async def stream_start():
    global _stream_running, _stream_task
    import torch
    if analyzer and hasattr(analyzer, 'to_device') and torch.cuda.is_available():
        try:
            analyzer.to_device("cuda:0")
            log.info("Crowd CSRNet model moved to CUDA:0")
        except Exception as e:
            log.warning("Failed to move crowd model to CUDA: %s", e)
    if _stream_running:
        return {"status": "already_running", "device": str(analyzer.device) if analyzer else "cpu"}
    if analyzer:
        analyzer.reset_session()
    _stream_running = True
    _stream_task = asyncio.create_task(_stream_loop())
    log.info("Crowd stream loop started")
    return {"status": "started", "device": str(analyzer.device) if analyzer else "cpu", "model_type": _current_model_type}


@app.get("/stream/stop")
async def stream_stop():
    global _stream_running, _stream_task
    _stream_running = False
    if _stream_task:
        _stream_task.cancel()
        _stream_task = None
    if analyzer and hasattr(analyzer, 'to_device'):
        try:
            analyzer.to_device("cpu")
        except Exception as e:
            log.warning("Failed to move crowd model to CPU: %s", e)
    import gc, torch
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()
    log.info("Crowd stream loop stopped and GPU VRAM released")
    return {"status": "stopped", "device": "cpu"}


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

# ── New Endpoints ─────────────────────────────────────────────────────────────

@app.get("/annotated/latest")
async def annotated_latest(drone_id: str = ""):
    """Return latest heatmap-overlaid frame as binary JPEG."""
    if not analyzer or not analyzer.ready:
        raise HTTPException(503, "Model not loaded")
    frame_bytes, matched_id, age, count, label = analyzer.get_last_annotated_frame(drone_id)
    if not frame_bytes:
        raise HTTPException(404, "No annotated frame available")
    return Response(
        content=frame_bytes,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "X-Drone-ID": matched_id or drone_id or "unknown",
            "X-Frame-Age": f"{age:.2f}",
            "X-Crowd-Count": f"{count:.1f}" if isinstance(count, float) else str(count),
            "X-Density-Label": label or "Unknown",
            "Access-Control-Expose-Headers": "X-Crowd-Count, X-Density-Label, X-Drone-ID, X-Frame-Age",
        }
    )

@app.put("/model/switch")
@app.post("/model/switch")
async def switch_model(body: dict):
    global _current_model_type
    model_type = body.get("model_type", "csrnet_parta")
    if model_type not in MODEL_PATHS:
        raise HTTPException(400, f"Unknown model_type: {model_type}. Options: {list(MODEL_PATHS.keys())}")
    new_path = MODEL_PATHS[model_type]
    if not os.path.isfile(new_path):
        raise HTTPException(404, f"Model file not found: {new_path}")
    try:
        analyzer.switch_model(new_path)
    except Exception as exc:
        log.error("Failed to switch model to %s: %s", model_type, exc)
        raise HTTPException(500, f"Failed to switch model to {model_type}: {exc}")
    _current_model_type = model_type
    log.info("Switched crowd model to %s (%s)", model_type, new_path)
    return {"status": "switched", "model_type": model_type, "model_path": new_path}

@app.get("/model/current")
async def current_model():
    return {
        "model_type": _current_model_type,
        "model_path": analyzer._model_path if analyzer else "",
        "device": str(analyzer.device) if analyzer else "unknown",
        "status": "loaded" if (analyzer and analyzer.ready) else "not_loaded",
    }

@app.get("/session/summary")
async def session_summary():
    if not analyzer:
        raise HTTPException(503, "Analyzer not initialized")
    return analyzer.get_session_summary()

import csv
import io as _io

@app.get("/export/csv")
async def export_csv():
    if not analyzer:
        raise HTTPException(503, "No session data")
    summary = analyzer.get_session_summary()
    counts = analyzer._session_counts
    if not counts:
        raise HTTPException(404, "No data to export")
    
    from crowd.analyzer import get_density_label
    output = _io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["frame_idx", "timestamp_sec", "estimated_count", "density_level"])
    fps = 25.0  # default, could be made configurable
    for i, count in enumerate(counts):
        label, _ = get_density_label(count)
        writer.writerow([i, round(i / fps, 3), round(count, 2), label])
    
    csv_bytes = output.getvalue().encode('utf-8')
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=crowd_report.csv"}
    )

@app.get("/export/pdf")
async def export_pdf(operator: str = "", location: str = "", mission_id: str = ""):
    if not analyzer:
        raise HTTPException(503, "No session data")
    summary = analyzer.get_session_summary()
    if summary["total_frames"] == 0:
        raise HTTPException(404, "No data to export")
    
    from crowd.report import generate_pdf_report
    pdf_bytes = generate_pdf_report(
        summary=summary,
        operator=operator or "Unknown",
        location=location or "Unknown",
        mission_id=mission_id or f"MSN-{int(time.time())}",
        model_type=_current_model_type,
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=crowd_mission_report_{mission_id or 'export'}.pdf"}
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8101, reload=False)
