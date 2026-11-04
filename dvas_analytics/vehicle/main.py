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
import time as _time
from datetime import datetime, timezone
from typing import Optional, Dict

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse, Response

sys.path.insert(0, "/analytics")
from shared.auth import install_service_guard
from shared.config import cfg, get_drone_aliases
from shared.db import (
    close_pool, ensure_drone, get_pool, upsert_track,
    write_vehicle_attributes,
    write_evidence_frame, get_vehicle_plate_link,
)
from shared.redis_client import (
    close_client, publish_analytics_result,
    xread_frames, xreadgroup_frames, ack_frames, hset_vehicle_track, hget_vehicle_track,
    scan_active_stream_drones, get_latest_stream_frame,
)
from shared import minio_client as minio
from shared import gpu_profile
from shared.frame_sampler import FrameSampler, decode_frame
from shared.annotated_hub import AnnotatedHub, install_stream_route, scale_items, to_display
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
# Internal service: no browser calls it (no CORS); every route but /health needs the key (M1)
install_service_guard(app)

VEHICLE_MODEL = os.getenv("VEHICLE_MODEL", "/analytics/vehicle/models/visdrone.pt")
analyzer: Optional[VehicleAnalyzer] = None
_stream_task: Optional[asyncio.Task] = None
_stream_running = False
_frames_processed = 0
# Newest-frame reader, governor cap and live fps stats (RunPod plan 9.2 / 10)
_sampler = FrameSampler("vehicle", "vehicle-group", "vehicle-analyzer")
# Live annotated video (MJPEG, ≤1920 px) for the dashboard
_hub = AnnotatedHub("vehicle")
install_stream_route(app, _hub)

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


def _live_vehicle_jpeg(frame: np.ndarray, items: list, drone_id: str) -> bytes:
    """Live-view JPEG: overlay drawn on the ≤1920 px copy with boxes scaled to it, so
    labels keep their designed size when the drone sends 4K (detection is unaffected)."""
    small, f = to_display(frame)
    return _draw_vehicle_annotations(small, scale_items(items, ("bbox",), f), drone_id)


@app.on_event("startup")
async def startup():
    global analyzer, _stream_running, _stream_task
    await get_pool()
    for drone_id in cfg.DRONE_IDS:
        await ensure_drone(drone_id)
    await minio.ensure_all_buckets()
    gpu_profile.apply_torch_settings()      # FP16 / cuDNN autotune for the detected GPU
    analyzer = VehicleAnalyzer(VEHICLE_MODEL)
    log.info("Vehicle Analyzer ready. Model: %s", VEHICLE_MODEL)

    # Auto-start stream background consumer loop
    _stream_running = True
    _stream_task = asyncio.create_task(_stream_loop())
    log.info("Vehicle stream loop auto-started for drones: %s", cfg.DRONE_IDS)



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
        # No confirmed event per detected vehicle (B29), same as the stream path;
        # vehicle incidents come from ANPR (theft_alert)

        colour_label = veh["colour_top3"][0]["colour"] if veh.get("colour_top3") else None

        await write_vehicle_attributes(
            track_id=track_id,
            event_id=None,
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

    await publish_analytics_result("vehicle", result)
    return result


# ── Continuous stream loop ─────────────────────────────────────────────────────
async def _persist_vehicle_frame(drone_id: str, f: dict, frame: np.ndarray, result: dict) -> None:
    """Annotate, store and publish one analysed frame (runs while the next batch infers)."""
    global _frames_processed
    ts     = datetime.now(timezone.utc)

    # Extract mission_id from stream frame metadata
    import json as _json
    frame_mission_id = None
    raw_meta = f.get("metadata", "")
    if raw_meta:
        if isinstance(raw_meta, bytes):
            raw_meta = raw_meta.decode()
        try:
            frame_mission_id = _json.loads(raw_meta).get("mission_id")
        except Exception:
            pass

    # ── Annotate frame and cache for dashboard ────────────────
    import time as _time
    annotated_bytes = await asyncio.to_thread(
        _live_vehicle_jpeg, frame, result.get("vehicles", []), drone_id
    )
    if annotated_bytes:
        _hub.put(drone_id, annotated_bytes)
        _last_annotated[drone_id]    = annotated_bytes
        _last_annotated_ts[drone_id] = _time.monotonic()

    # ── Persist to DB + Redis ─────────────────────────────────
    for veh in result.get("vehicles", []):
        tracker_id   = veh.get("tracker_id")
        track_id     = await upsert_track(
            drone_id, None, "vehicle", ts, ts,
            tracker_id=tracker_id, mission_id=frame_mission_id
        )
        # No confirmed event per vehicle per frame (B29): detections stay in
        # tracks / vehicle_attributes; vehicle incidents come from ANPR (theft_alert).

        colour_label = (veh.get("colour_top3") or [{}])[0].get("colour", "")
        crop_bytes   = veh.pop("crop_bytes", None)
        veh_image_key = None
        if crop_bytes:
            key = minio.make_key("vehicles", drone_id,
                                 f"v_{tracker_id}_{int(ts.timestamp()*1000)}.jpg",
                                 mission_id=frame_mission_id or "")
            veh_image_key = await minio.upload_bytes(
                cfg.BUCKET_VEHICLES, key, crop_bytes, "image/jpeg"
            )

        if veh_image_key and track_id:
            pool = await get_pool()
            await pool.execute(
                """
                UPDATE tracks
                SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('vehicle_image_key', $1::text)
                WHERE track_id = $2::uuid
                """,
                veh_image_key, track_id,
            )

        await write_vehicle_attributes(
            track_id=track_id,
            event_id=None,
            vehicle_type=veh.get("vehicle_type", "unknown"),
            colour_top3=veh.get("colour_top3", []),
            make_model_top3=veh.get("make_model_top3", []),
            speed_kph=veh.get("speed_info", {}).get("kph"),
            speed_reliability=veh.get("speed_info", {}).get("reliability"),
            mission_id=frame_mission_id,
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


async def _persist_round(items: list) -> None:
    for drone_id, f, frame, result, t_start in items:
        try:
            await _persist_vehicle_frame(drone_id, f, frame, result)
        except Exception as exc:
            log.warning("Vehicle persist error for %s: %s", drone_id, exc)
        _sampler.record(drone_id, infer_ms=result.get("model_ms"),
                        total_ms=(_time.perf_counter() - t_start) * 1000)


async def _stream_loop():
    global _stream_running
    active_drones = list(cfg.DRONE_IDS)
    log.info("Vehicle stream loop started for drones: %s (GPU %s)",
             cfg.DRONE_IDS, gpu_profile.detect().as_dict())

    loop_count = 0
    pending: Optional[asyncio.Task] = None
    try:
        while _stream_running:
            loop_count += 1
            if loop_count % 50 == 1:
                try:
                    for d in await scan_active_stream_drones():
                        if d not in active_drones:
                            active_drones.append(d)
                except Exception:
                    pass
            try:
                await _sampler.maybe_publish()
                round_frames = await _sampler.next_round(active_drones)
                if not round_frames:
                    continue
                t_start = _time.perf_counter()
                decoded = {}
                for drone_id, f in round_frames.items():
                    frame = await asyncio.to_thread(decode_frame, f)
                    if frame is not None:
                        decoded[drone_id] = (f, frame)
                if not decoded:
                    continue
                results = await asyncio.to_thread(
                    analyzer.analyze_batch, {d: fr for d, (_, fr) in decoded.items()}, True
                )
                _sampler.record_batch(len(decoded))
                # Persist the previous round before queuing this one (keeps per-drone order);
                # it ran concurrently with the inference above.
                if pending:
                    await asyncio.shield(pending)   # a stream stop must not cut a round half-written
                pending = asyncio.create_task(_persist_round(
                    [(d, f, fr, results[d], t_start) for d, (f, fr) in decoded.items()]
                ))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                err_str = str(exc)
                if "CUDA" in err_str and analyzer and hasattr(analyzer, 'to_device'):
                    log.warning("CUDA error in vehicle stream — falling back to CPU: %s", exc)
                    try:
                        analyzer.to_device("cpu")
                    except Exception:
                        pass
                else:
                    log.warning("Vehicle stream error: %s", exc)
                await asyncio.sleep(1)
    finally:
        if pending and not pending.done():
            try:
                await asyncio.shield(pending)   # a stream stop must not cut a round half-written
            except Exception:
                pass


# ── Stream control ─────────────────────────────────────────────────────────────
@app.get("/stream/start")
async def stream_start():
    global _stream_running, _stream_task
    import torch
    if analyzer and hasattr(analyzer, 'to_device') and torch.cuda.is_available():
        try:
            analyzer.to_device("cuda:0")
        except Exception as e:
            log.warning("Failed to move vehicle model to CUDA, staying on CPU: %s", e)
    if _stream_running:
        return {"status": "already_running", "device": analyzer.device if analyzer else "cpu", "frames_processed": _frames_processed}
    _stream_running = True
    _stream_task = asyncio.create_task(_stream_loop())
    log.info("Vehicle stream loop STARTED")
    return {"status": "started", "device": analyzer.device if analyzer else "cpu"}


@app.get("/stream/stop")
async def stream_stop():
    global _stream_running, _stream_task
    _stream_running = False
    if _stream_task:
        _stream_task.cancel()
        _stream_task = None

    if not cfg.VRAM_OFFLOAD_ON_STOP:
        # RunPod pod: keep models resident on the GPU (no reload stall; required for TensorRT)
        log.info("Vehicle stream loop stopped (models kept on GPU, VRAM_OFFLOAD_ON_STOP=false)")
        return {"status": "stopped", "device": "gpu", "frames_processed": _frames_processed}
    if analyzer and hasattr(analyzer, 'to_device'):
        try:
            analyzer.to_device("cpu")
        except Exception as e:
            log.warning("Failed to move vehicle model to CPU: %s", e)
    import gc, torch
    gc.collect()
    if torch.cuda.is_available():
        try:
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
        except Exception as e:
            log.warning("Failed to clear CUDA cache (non-fatal): %s", e)
    log.info("Vehicle stream loop STOPPED and GPU VRAM released")
    return {"status": "stopped", "device": "cpu", "frames_processed": _frames_processed}


# ── Annotated frame endpoint ───────────────────────────────────────────────────
FRAME_CACHE_TTL_SEC = 6.0

@app.get("/annotated/latest")
async def annotated_latest(drone_id: str = ""):
    """
    Returns the most recently annotated JPEG frame (with vehicle bounding boxes).
    If a live stream is ongoing, serves live frame with X-Feed-Status: live.
    If the stream has ended or paused, serves the last known annotated frame with X-Feed-Status: offline.
    If not in memory (e.g. after container restart), loads and analyzes the latest frame from Redis.
    """
    import time as _time
    now = _time.monotonic()
    frame_bytes = None
    matched_drone_id = None
    frame_age = 0.0

    if drone_id:
        aliases = get_drone_aliases(drone_id)
        for alias in aliases:
            if alias in _last_annotated:
                frame_bytes = _last_annotated[alias]
                matched_drone_id = alias
                frame_age = now - _last_annotated_ts.get(alias, 0)
                break
    elif _last_annotated:
        # If no specific drone requested, find the freshest frame
        best_drone = max(_last_annotated_ts, key=_last_annotated_ts.get, default=None)
        if best_drone:
            frame_bytes = _last_annotated.get(best_drone)
            matched_drone_id = best_drone
            frame_age = now - _last_annotated_ts.get(best_drone, 0)

    # If no in-memory frame, try to recover the latest frame from the Redis stream
    if not frame_bytes and drone_id:
        aliases = get_drone_aliases(drone_id)
        for alias in aliases:
            recovered_frame = await get_latest_stream_frame(alias)
            if recovered_frame is not None and analyzer and analyzer.ready:
                try:
                    result = await asyncio.to_thread(
                        analyzer.analyze_frame, recovered_frame, drone_id=alias, save_crops=False
                    )
                    annotated_bytes = await asyncio.to_thread(
                        _live_vehicle_jpeg, recovered_frame, result.get("vehicles", []), alias
                    )
                    if annotated_bytes:
                        _last_annotated[alias] = annotated_bytes
                        _last_annotated_ts[alias] = 0.0  # Mark as historical
                        frame_bytes = annotated_bytes
                        matched_drone_id = alias
                        frame_age = 999.0
                        break
                except Exception as exc:
                    log.warning("Failed to recover and annotate vehicle frame for %s: %s", alias, exc)

    if not frame_bytes:
        raise HTTPException(404, "No annotated frame available (feed offline or expired)")

    is_live = frame_age <= FRAME_CACHE_TTL_SEC
    feed_status = "live" if is_live else "offline"

    return Response(
        content=frame_bytes,
        media_type="image/jpeg",
        headers={
            "Cache-Control":   "no-cache, no-store, must-revalidate",
            "Pragma":          "no-cache",
            "Expires":         "0",
            "X-Drone-ID":      matched_drone_id or drone_id,
            "X-Frame-Age":     f"{frame_age:.2f}s",
            "X-Feed-Status":   feed_status,
            "X-Stream-Status": feed_status,
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
