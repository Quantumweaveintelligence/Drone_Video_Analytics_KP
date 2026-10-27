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
from shared.config import cfg, get_drone_aliases
from shared.db import (
    close_pool, ensure_drone, get_pool, write_confirmed_event,
    write_crowd_reading, get_crowd_history,
)
from shared.redis_client import (
    close_client, get_client, publish_analytics_result,
    publish_confirmed_event, xread_frames, xreadgroup_frames, ack_frames,
    get_latest_stream_frame,
)
from shared import minio_client as minio
from crowd.analyzer import CrowdAnalyzer
from crowd.stream_policy import FrameRateCap, is_cuda_failure, parse_mission_id
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

# ── Frame-rate cap (RunPod plan F19) ──────────────────────────────────────────
# Per-drone minimum interval between CSRNet inferences. Frames that arrive
# sooner are ACKed without being decoded. The Phase-10 governor will replace
# the env value with dvas:fps_target:crowd:<drone>.
_rate_cap = FrameRateCap(cfg.CROWD_MAX_FPS)
_frames_skipped = 0                    # frames ACKed without inference (rate cap)
HEATMAP_EVERY_N_FRAMES = 10            # heatmap PNG/base64 is only uploaded every Nth frame

# ── Annotated-feed recovery (RunPod plan F21 / Phase-26 parity) ───────────────
LIVE_FRAME_MAX_AGE_S = 6.0             # older cached frames are reported as offline
RECOVERY_REFRESH_S = 30.0              # re-read the Valkey stream at most this often
_recovered_frames: dict = {}           # drone_id -> (jpeg, count, label, monotonic_ts)


def _is_cuda_failure(exc: BaseException) -> bool:
    try:
        import torch
        oom_type = getattr(torch.cuda, "OutOfMemoryError", None)
    except ImportError:
        oom_type = None
    return is_cuda_failure(exc, oom_type)


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
        "frames_skipped":   _frames_skipped,
        "max_fps_per_drone": cfg.CROWD_MAX_FPS,
        "device":           str(analyzer.device) if analyzer else "unknown",
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
    if result is None:
        # Model became unavailable between the readiness check and inference
        raise HTTPException(503, "Model not loaded")
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
async def _process_crowd_frame(drone_id: str, f: dict) -> None:
    """Decode, analyse and persist one frame (the newest one read for a drone)."""
    global _frames_processed
    raw = f.get("jpeg_b64", "")
    if not raw:
        return
    img_bytes = base64.b64decode(raw)
    frame = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        log.debug("Undecodable frame for %s skipped", drone_id)
        return

    mission_id = parse_mission_id(f)
    want_heatmap = _frames_processed % HEATMAP_EVERY_N_FRAMES == 0

    # Run inference in worker thread pool so FastAPI event loop is never blocked
    result = await asyncio.to_thread(
        analyzer.analyze_frame, frame, drone_id=drone_id, include_heatmap=want_heatmap
    )
    if result is None:
        # Model not ready (e.g. mid model-switch) — frame is ACKed by the caller
        return
    _rate_cap.mark(drone_id)
    ts = datetime.now(timezone.utc)

    heatmap_key = None
    if want_heatmap and result.get("heatmap_b64"):
        heatmap_bytes = base64.b64decode(result["heatmap_b64"])
        key = minio.make_key("heatmaps", drone_id,
                             f"heatmap_{int(ts.timestamp()*1000)}.png",
                             mission_id=mission_id)
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
                "mission_id":      mission_id or None,
            },
        )
        await publish_confirmed_event({
            "event_id":   event_id,
            "event_type": "crowd_threshold",
            "drone_id":   drone_id,
            "mission_id": mission_id or None,
            "ts":         ts.isoformat(),
            "count":      result["estimated_count"],
            "density":    result["density_label"],
        })

    # Publish to SSE stream — strip heavy binary fields
    sse_result = {k: v for k, v in result.items()
                  if k not in ("heatmap_b64", "peak_frame")}
    sse_result["mission_id"] = mission_id or None
    await publish_analytics_result("crowd", sse_result)
    _frames_processed += 1


async def _stream_loop():
    global _stream_running, _frames_skipped
    active_drones = list(cfg.DRONE_IDS)
    log.info("Crowd stream loop started for initial drones: %s (max %.2f fps/drone)",
             active_drones, cfg.CROWD_MAX_FPS)

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
                        await ensure_drone(d)
                        log.info("Crowd stream loop auto-discovered active drone: %s", d)
            except Exception as exc:
                log.warning("Crowd stream scanner warning: %s", exc)

        any_frames_received = False
        for drone_id in list(active_drones):
            try:
                frames = await xreadgroup_frames(
                    "crowd-group", "crowd-analyzer", drone_id, ">", count=5, block_ms=50
                )
                if not frames:
                    continue
                any_frames_received = True
                entry_ids = [entry_id for entry_id, _ in frames]
                try:
                    if not _rate_cap.due(drone_id):
                        # Rate cap: drop without decoding (latest-frame-wins)
                        _frames_skipped += len(entry_ids)
                        continue
                    # Older frames in the batch are skipped; only the newest is analysed
                    _frames_skipped += len(entry_ids) - 1
                    await _process_crowd_frame(drone_id, frames[-1][1])
                except Exception as exc:
                    if _is_cuda_failure(exc) and analyzer is not None:
                        log.warning("CUDA failure in crowd stream for %s — falling back to CPU: %s",
                                    drone_id, exc)
                        await asyncio.to_thread(analyzer.to_device, "cpu")
                    else:
                        raise
                finally:
                    # Always ACK, so failed frames never pile up in the pending list
                    await ack_frames(drone_id, "crowd-group", *entry_ids)

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
    if not cfg.VRAM_OFFLOAD_ON_STOP:
        # RunPod pod: keep the model resident on the GPU (no reload stall on restart)
        log.info("Crowd stream loop stopped (model kept on %s, VRAM_OFFLOAD_ON_STOP=false)",
                 analyzer.device if analyzer else "n/a")
        return {"status": "stopped", "device": str(analyzer.device) if analyzer else "cpu"}
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
async def density_timeline(drone_id: Optional[str] = None, limit: int = 100,
                           hours: Optional[float] = None):
    """Time-series crowd counts for dashboard charts (optionally last `hours` only)."""
    rows = await get_crowd_history(drone_id=drone_id, limit=min(limit, 500))
    if hours is not None and hours > 0:
        cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600.0
        rows = [r for r in rows if hasattr(r["ts"], "timestamp") and r["ts"].timestamp() >= cutoff]
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
                # MinIO is internal in the cloud pod: browsers load the image via
                # /api/minio/image?bucket=<heatmap_bucket>&key=<heatmap_object_key>
                "heatmap_object_key": key,
                "heatmap_bucket":  cfg.BUCKET_HEATMAPS,
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
    """Return latest heatmap-overlaid frame as binary JPEG.

    Live frames come from the analyzer cache (X-Feed-Status: live). When the
    cache is empty or expired (service restart, stream paused) the newest frame
    still in the Valkey stream is analysed once and served with
    X-Feed-Status: offline (Phase-26 parity with the other analyzers).
    """
    if not analyzer or not analyzer.ready:
        raise HTTPException(503, "Model not loaded")
    frame_bytes, matched_id, age, count, label = analyzer.get_last_annotated_frame(drone_id)
    feed_status = "live" if frame_bytes and age <= LIVE_FRAME_MAX_AGE_S else "offline"

    if not frame_bytes and drone_id:
        now = time.monotonic()
        for alias in get_drone_aliases(drone_id):
            cached = _recovered_frames.get(alias)
            if cached and now - cached[3] < RECOVERY_REFRESH_S:
                frame_bytes, count, label = cached[0], cached[1], cached[2]
                matched_id, age = alias, now - cached[3]
                break
            recovered = await get_latest_stream_frame(alias)
            if recovered is None:
                continue
            try:
                rendered = await asyncio.to_thread(analyzer.render_offline_frame, recovered)
            except Exception as exc:
                log.warning("Failed to recover crowd frame for %s: %s", alias, exc)
                continue
            if rendered:
                frame_bytes, count, label = rendered
                matched_id, age = alias, 0.0
                _recovered_frames[alias] = (frame_bytes, count, label, now)
                break
        feed_status = "offline"

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
            "X-Feed-Status": feed_status,
            "X-Stream-Status": feed_status,
            "Access-Control-Expose-Headers": "X-Crowd-Count, X-Density-Label, X-Drone-ID, X-Frame-Age, X-Feed-Status, X-Stream-Status",
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
    samples = analyzer.get_session_samples()
    if not samples:
        raise HTTPException(404, "No data to export")

    from crowd.analyzer import get_density_label
    output = _io.StringIO()
    writer = csv.writer(output)
    # timestamp_sec = real seconds since the first sample of the session
    writer.writerow(["frame_idx", "timestamp_sec", "timestamp_utc", "estimated_count", "density_level"])
    t0 = samples[0][0]
    for i, (ts_epoch, count) in enumerate(samples):
        label, _ = get_density_label(count)
        writer.writerow([
            i,
            round(ts_epoch - t0, 3),
            datetime.fromtimestamp(ts_epoch, tz=timezone.utc).isoformat(),
            round(count, 2),
            label,
        ])

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