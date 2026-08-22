# -*- coding: utf-8 -*-
"""
Person & Face Analyzer Service — FastAPI on port 8102.
Person tracking with ByteTrack, face detection, Re-ID, and MinIO crop storage.
"""
import asyncio
import base64
import hashlib
import json as _json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import cv2
import numpy as np
import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

sys.path.insert(0, "/analytics")
from shared.config import cfg, get_drone_aliases
from shared.db import (
    close_pool, ensure_drone, get_pool, upsert_track,
    write_confirmed_event, get_all_face_embeddings, delete_face_embeddings,
    write_face_embedding, update_face_embedding_cluster,
    write_evidence_frame, get_person_identities, upsert_person_identity,
    get_face_embeddings_by_mission, get_distinct_missions_face_embeddings,
    upsert_dedup_run, get_dedup_run, get_dedup_runs,
)
from shared.redis_client import (
    close_client, publish_analytics_result, publish_confirmed_event, xread_frames,
    scan_active_stream_drones,
)
from shared import minio_client as minio
from person.analyzer import PersonFaceAnalyzer
from person.deduplicator import (
    cluster_embeddings, find_duplicates_in_clusters, load_resnet_model,
    extract_embedding_from_bytes, get_identity_representatives,
    update_cluster_labels_db,
)
from person.schema import FrameAnalysisResult, DeduplicationResult

logging.basicConfig(
    level=cfg.LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("person-service")

app = FastAPI(
    title="DVAS Person & Face Analyzer",
    description="Person tracking (ByteTrack), face detection, Re-ID, deduplication",
    version="2.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PERSON_MODEL = os.getenv("PERSON_MODEL", "/analytics/person/models/yolov8m.pt")
FACE_MODEL   = os.getenv("FACE_MODEL",   "/analytics/person/models/yolov8n-face.pt")

analyzer: Optional[PersonFaceAnalyzer] = None
resnet_model = None
resnet_device = None
_stream_task:  Optional[asyncio.Task] = None
_reid_task:    Optional[asyncio.Task] = None
_stream_running = False
_frames_processed = 0
_reid_frame_counter = 0
_last_annotated_frames: dict = {}  # {drone_id: jpeg_bytes}
_last_annotated_ts: dict = {}      # {drone_id: monotonic_timestamp}
_active_drone_missions: dict = {}  # {drone_id: mission_id}
_live_person_buffer: dict = {}     # {(drone_id, tracker_id): {entry dict}}
_LIVE_BUFFER_MAX = 100
_LIVE_BUFFER_STALE_SEC = 30
_CROP_REFRESH_EVERY = 5           # refresh crops every Nth frame per tracker
_dedup_progress: dict = {          # mission dedup progress tracking
    "mission_id": None, "status": "idle", "progress_pct": 0,
    "total_embeddings": 0, "processed": 0, "unique_found": 0,
    "error": None,
}


@app.on_event("startup")
async def startup():
    global analyzer, resnet_model, resnet_device, _stream_running, _stream_task
    await get_pool()
    for drone_id in cfg.DRONE_IDS:
        await ensure_drone(drone_id)
    await minio.ensure_all_buckets()
    analyzer = PersonFaceAnalyzer(PERSON_MODEL, FACE_MODEL)
    resnet_device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    resnet_model = load_resnet_model(resnet_device)
    log.info("Person & Face Analyzer ready")

    # Auto-start stream background consumer loop
    _stream_running = True
    _stream_task = asyncio.create_task(_stream_loop())
    log.info("Person stream loop auto-started for drones: %s", cfg.DRONE_IDS)



@app.on_event("shutdown")
async def shutdown():
    global _stream_running, _stream_task, _reid_task
    _stream_running = False
    if _stream_task:
        _stream_task.cancel()
    if _reid_task:
        _reid_task.cancel()
    await close_pool()
    await close_client()


# ── Health ─────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status":           "ok",
        "service":          "person-analyzer",
        "model_loaded":     analyzer.ready if analyzer else False,
        "stream_running":   _stream_running,
        "frames_processed": _frames_processed,
        "active_missions":  dict(_active_drone_missions),
    }


# ── Single frame ───────────────────────────────────────────────────────────────
@app.post("/analyze/frame")
async def analyze_frame(
    file: UploadFile = File(...),
    drone_id: str = Form(default="DRONE_KL_001"),
    save_crops: bool = Form(default=True),
):
    if not analyzer or not analyzer.ready:
        raise HTTPException(503, "Models not loaded")

    contents = await file.read()
    nparr    = np.frombuffer(contents, np.uint8)
    frame    = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if frame is None:
        raise HTTPException(400, "Cannot decode image")

    result = analyzer.analyze_frame(frame, drone_id=drone_id, save_crops=save_crops)
    ts = datetime.now(timezone.utc)

    for person in result["persons"]:
        tracker_id = person.get("tracker_id")
        track_id = await upsert_track(
            drone_id, None, "person", ts, ts, tracker_id=tracker_id
        )
        person["track_id"] = track_id

        if person["confidence"] >= 0.4:
            event_id = await write_confirmed_event(
                drone_id=drone_id, event_type="person_detected",
                confidence=person["confidence"], ts=ts, track_id=track_id,
            )
            person["event_id"] = event_id

        # Upload person crop to MinIO
        crop_bytes = person.pop("crop_bytes", None)
        if crop_bytes and person.get("crop_b64"):
            key = minio.make_key("persons", drone_id, f"person_{tracker_id}_{int(ts.timestamp()*1000)}.jpg")
            obj_key = await minio.upload_bytes(cfg.BUCKET_EVIDENCE, key, crop_bytes, "image/jpeg")
            if obj_key:
                person["crop_key"] = obj_key
        else:
            person.pop("crop_bytes", None)

    for face in result["faces"]:
        face_crop_bytes = face.pop("crop_bytes", None)
        face_obj_key = None

        if face_crop_bytes:
            key = minio.make_key("faces", drone_id, f"face_{int(ts.timestamp()*1000)}.jpg")
            face_obj_key = await minio.upload_bytes(cfg.BUCKET_FACES, key, face_crop_bytes, "image/jpeg")

        if resnet_model and face_crop_bytes:
            emb = extract_embedding_from_bytes(resnet_model, face_crop_bytes, resnet_device)
            if emb:
                tracker_id = face.get("tracker_id")
                track_id = None
                if tracker_id is not None:
                    # find the person's track_id
                    for p in result["persons"]:
                        if p.get("tracker_id") == tracker_id:
                            track_id = p.get("track_id")
                            break

                emb_id = await write_face_embedding(
                    track_id=track_id,
                    image_path=face_obj_key or "",
                    embedding=emb,
                    drone_id=drone_id,
                    object_key=face_obj_key,
                )
                face["embedding_id"] = emb_id

        if face_obj_key:
            face["face_key"] = face_obj_key

    await publish_analytics_result("person", result)
    return result


# ── Stream control ─────────────────────────────────────────────────────────────
async def _stream_loop():
    global _stream_running, _frames_processed, _reid_frame_counter
    last_ids = {d: "0" for d in cfg.DRONE_IDS}
    active_drones = list(cfg.DRONE_IDS)
    log.info("Person stream loop started for drones: %s", cfg.DRONE_IDS)

    loop_count = 0
    while _stream_running:
        loop_count += 1
        if loop_count % 10 == 1:
            try:
                discovered = await scan_active_stream_drones()
                for d in discovered:
                    if d not in active_drones:
                        active_drones.append(d)
                        last_ids[d] = "0"
            except Exception as exc:
                log.warning("Person stream scanner warning: %s", exc)

        for drone_id in active_drones:
            if drone_id not in last_ids:
                last_ids[drone_id] = "0"
            try:
                new_id, frames = await xread_frames(
                    drone_id, last_ids[drone_id], count=1, block_ms=10
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

                    # ── Extract mission_id from frame metadata ────────
                    mission_id = None
                    raw_meta = f.get("metadata", "")
                    if raw_meta:
                        try:
                            meta = _json.loads(raw_meta)
                            mission_id = meta.get("mission_id") or None
                        except (_json.JSONDecodeError, TypeError):
                            pass
                    if mission_id:
                        _active_drone_missions[drone_id] = mission_id

                    # Run inference in worker thread pool so FastAPI event loop is never blocked
                    result = await asyncio.to_thread(
                        analyzer.analyze_frame, frame, drone_id=drone_id, save_crops=True
                    )
                    # Tag result with mission_id for downstream consumers
                    result["mission_id"] = mission_id
                    # ── Draw annotated overlay for live viewing ──────────
                    annotated = frame.copy()
                    for p in result["persons"]:
                        bb = p["bbox"]
                        cv2.rectangle(annotated, (bb["x1"], bb["y1"]), (bb["x2"], bb["y2"]), (191, 219, 56), 2)  # teal-ish
                        tid_str = f'P-{p["tracker_id"]}' if p["tracker_id"] > 0 else f'P-{abs(p["tracker_id"])}'
                        label = f'{tid_str}  {int(p["confidence"]*100)}%'
                        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                        cv2.rectangle(annotated, (bb["x1"], bb["y1"]-th-8), (bb["x1"]+tw+6, bb["y1"]), (191, 219, 56), -1)
                        cv2.putText(annotated, label, (bb["x1"]+3, bb["y1"]-4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
                    for fc in result["faces"]:
                        bb = fc["bbox"]
                        cv2.rectangle(annotated, (bb["x1"], bb["y1"]), (bb["x2"], bb["y2"]), (0, 191, 255), 2)  # amber
                        conf_label = f'{int(fc["confidence"]*100)}%'
                        cv2.putText(annotated, conf_label, (bb["x1"], bb["y2"]+14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 191, 255), 1, cv2.LINE_AA)
                    _, ann_buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    _last_annotated_frames[drone_id] = bytes(ann_buf)
                    _last_annotated_ts[drone_id] = time.monotonic()

                    ts = datetime.now(timezone.utc)

                    for person in result["persons"]:
                        tracker_id = person.get("tracker_id")
                        if person["confidence"] >= 0.15:
                            track_id = await upsert_track(
                                drone_id, None, "person", ts, ts,
                                tracker_id=tracker_id,
                                mission_id=mission_id,
                            )
                            await write_confirmed_event(
                                drone_id=drone_id, event_type="person_detected",
                                confidence=person["confidence"], ts=ts,
                                track_id=track_id,
                            )

                    # Process face crops + embeddings
                    for face in result["faces"]:
                        face_crop_bytes = face.pop("crop_bytes", None)
                        if face_crop_bytes and resnet_model:
                            emb = extract_embedding_from_bytes(
                                resnet_model, face_crop_bytes, resnet_device
                            )
                            if emb:
                                key = minio.make_key(
                                    "faces", drone_id,
                                    f"face_{int(ts.timestamp()*1000)}.jpg"
                                )
                                face_obj_key = await minio.upload_bytes(
                                    cfg.BUCKET_FACES, key, face_crop_bytes, "image/jpeg"
                                )
                                await write_face_embedding(
                                    track_id=None,
                                    image_path=face_obj_key or "",
                                    embedding=emb,
                                    drone_id=drone_id,
                                    object_key=face_obj_key,
                                    mission_id=mission_id,
                                )

                    # Also clean up person crop_bytes
                    for person in result["persons"]:
                        person.pop("crop_bytes", None)

                    await publish_analytics_result("person", result)
                    _frames_processed += 1
                    _reid_frame_counter += 1

                    # ── Populate live person buffer with crop pairs ──────
                    _update_live_person_buffer(
                        result, drone_id, mission_id, ts, frame
                    )

                    # Periodic re-ID clustering
                    if _reid_frame_counter >= cfg.REID_CLUSTER_INTERVAL:
                        _reid_frame_counter = 0
                        asyncio.create_task(_run_reid_clustering())

            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning("Person stream error for %s: %s", drone_id, exc)
                await asyncio.sleep(1)


async def _run_reid_clustering():
    """Background Re-ID clustering — runs asynchronously."""
    try:
        rows = await get_all_face_embeddings()
        if len(rows) < 2:
            return
        ids        = [str(r["id"]) for r in rows]
        embeddings = [list(r["embedding"]) for r in rows]
        labels = cluster_embeddings(embeddings, cfg.REID_DISTANCE_THRESHOLD)
        duplicate_ids, unique_count = find_duplicates_in_clusters(ids, labels)
        dup_set = set(duplicate_ids)
        await update_cluster_labels_db(ids, labels, dup_set)
        # Upsert person identities
        reps = get_identity_representatives(ids, labels)
        for cluster_id, rep_id in reps.items():
            rep_row = next((r for r in rows if str(r["id"]) == rep_id), None)
            if rep_row:
                await upsert_person_identity(
                    cluster_id=cluster_id,
                    best_image_key=rep_row.get("object_key"),
                    drone_id=rep_row.get("drone_id"),
                    embedding_count=sum(1 for l in labels if l == cluster_id),
                )
        log.info("Re-ID clustering: %d embeddings → %d identities", len(rows), unique_count)
    except Exception as exc:
        log.warning("Re-ID clustering failed: %s", exc)


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


@app.get("/annotated/latest")
async def annotated_latest(drone_id: str = ""):
    """Return the latest annotated JPEG frame with person + face bounding boxes."""
    from fastapi.responses import Response

    now = time.monotonic()
    frame_bytes = None
    matched_drone_id = None
    frame_age = 0.0

    if drone_id:
        aliases = get_drone_aliases(drone_id)
        for alias in aliases:
            if alias in _last_annotated_frames:
                frame_bytes = _last_annotated_frames[alias]
                matched_drone_id = alias
                frame_age = now - _last_annotated_ts.get(alias, now)
                break

    if not frame_bytes and _last_annotated_frames:
        best_drone = max(_last_annotated_ts, key=_last_annotated_ts.get, default=None)
        if best_drone:
            frame_bytes = _last_annotated_frames.get(best_drone)
            matched_drone_id = best_drone
            frame_age = now - _last_annotated_ts.get(best_drone, now)
        if not frame_bytes:
            matched_drone_id, frame_bytes = next(iter(_last_annotated_frames.items()))

    if not frame_bytes:
        raise HTTPException(404, "No live Person annotated frame available yet")

    return Response(
        content=frame_bytes,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "X-Drone-ID": matched_drone_id or drone_id,
            "X-Frame-Age": f"{frame_age:.2f}s",
        },
    )


# ── Live person buffer helpers ─────────────────────────────────────────────────
def _update_live_person_buffer(result, drone_id, mission_id, ts, frame):
    """Update in-memory live person buffer with crop pairs from latest result."""
    now = time.time()

    # Build face lookup by proximity to each person bbox
    faces_by_person = {}
    for fc in result.get("faces", []):
        # Link face to nearest person by checking if face center is inside person bbox
        fbb = fc["bbox"]
        fcx = (fbb["x1"] + fbb["x2"]) / 2
        fcy = (fbb["y1"] + fbb["y2"]) / 2
        for p in result.get("persons", []):
            pbb = p["bbox"]
            if pbb["x1"] <= fcx <= pbb["x2"] and pbb["y1"] <= fcy <= pbb["y2"]:
                tid = p.get("tracker_id")
                if tid is not None:
                    faces_by_person[tid] = fc
                break

    for p in result.get("persons", []):
        tid = p.get("tracker_id")
        if tid is None:
            continue
        key = (drone_id, tid)
        conf = p.get("confidence", 0)
        bb = p["bbox"]

        if key in _live_person_buffer:
            entry = _live_person_buffer[key]
            entry["last_seen"] = ts.isoformat()
            entry["last_seen_epoch"] = now
            entry["frame_count"] += 1
            entry["confidence"] = conf
            entry["bbox"] = bb
            # Refresh crops every Nth frame
            if entry["frame_count"] % _CROP_REFRESH_EVERY == 0:
                pcrop = _safe_crop_b64(frame, bb)
                if pcrop:
                    entry["person_crop_b64"] = pcrop
                face = faces_by_person.get(tid)
                if face:
                    fcrop = _safe_crop_b64(frame, face["bbox"])
                    if fcrop:
                        entry["face_crop_b64"] = fcrop
        else:
            # New person
            pcrop = _safe_crop_b64(frame, bb)
            fcrop = None
            face = faces_by_person.get(tid)
            if face:
                fcrop = _safe_crop_b64(frame, face["bbox"])
            _live_person_buffer[key] = {
                "tracker_id":      tid,
                "drone_id":        drone_id,
                "mission_id":      mission_id,
                "person_crop_b64": pcrop,
                "face_crop_b64":   fcrop,
                "confidence":      conf,
                "bbox":            bb,
                "first_seen":      ts.isoformat(),
                "last_seen":       ts.isoformat(),
                "last_seen_epoch": now,
                "frame_count":     1,
            }

    # Evict stale entries
    stale_keys = [
        k for k, v in _live_person_buffer.items()
        if now - v["last_seen_epoch"] > _LIVE_BUFFER_STALE_SEC
    ]
    for k in stale_keys:
        del _live_person_buffer[k]

    # Cap buffer size
    if len(_live_person_buffer) > _LIVE_BUFFER_MAX:
        sorted_entries = sorted(
            _live_person_buffer.items(), key=lambda x: x[1]["last_seen_epoch"]
        )
        for k, _ in sorted_entries[: len(_live_person_buffer) - _LIVE_BUFFER_MAX]:
            del _live_person_buffer[k]


def _safe_crop_b64(frame, bbox) -> "str | None":
    """Crop a region from frame and return base64 JPEG, or None on failure."""
    try:
        h, w = frame.shape[:2]
        x1 = max(0, int(bbox["x1"]))
        y1 = max(0, int(bbox["y1"]))
        x2 = min(w, int(bbox["x2"]))
        y2 = min(h, int(bbox["y2"]))
        if x2 <= x1 or y2 <= y1:
            return None
        crop = frame[y1:y2, x1:x2]
        _, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return base64.b64encode(bytes(buf)).decode()
    except Exception:
        return None


@app.get("/persons/live")
async def persons_live(drone_id: str = ""):
    """Return in-memory live person buffer as JSON array with base64 crop pairs."""
    entries = list(_live_person_buffer.values())
    if drone_id:
        entries = [e for e in entries if e["drone_id"] == drone_id]
    # Sort by last_seen descending (most recently seen first)
    entries.sort(key=lambda e: e.get("last_seen_epoch", 0), reverse=True)
    return {"persons": entries, "count": len(entries)}


# ── Face deduplication ─────────────────────────────────────────────────────────
@app.get("/faces/deduplicate")
async def deduplicate_faces():
    """Run clustering on all stored face embeddings and mark duplicates."""
    rows = await get_all_face_embeddings()
    if len(rows) < 2:
        return {"message": "Not enough embeddings", "total": len(rows)}

    ids        = [str(r["id"]) for r in rows]
    embeddings = [list(r["embedding"]) for r in rows]
    labels = cluster_embeddings(embeddings, cfg.REID_DISTANCE_THRESHOLD)
    duplicate_ids, unique_count = find_duplicates_in_clusters(ids, labels)
    dup_set = set(duplicate_ids)
    await update_cluster_labels_db(ids, labels, dup_set)
    deleted = await delete_face_embeddings(duplicate_ids)
    log.info("Deduplication: %d unique, %d duplicates deleted", unique_count, deleted)
    return {
        "total_embeddings":   len(rows),
        "unique_persons":     unique_count,
        "duplicates_found":   len(duplicate_ids),
        "duplicates_deleted": deleted,
    }


@app.post("/faces/deduplicate/mission")
async def deduplicate_mission(body: dict):
    """Run deduplication scoped to a specific mission_id."""
    global _dedup_progress
    mission_id = body.get("mission_id")
    if not mission_id:
        raise HTTPException(400, "mission_id required")
    if _dedup_progress["status"] == "running":
        raise HTTPException(409, f"Dedup already running for {_dedup_progress['mission_id']}")
    # Start async dedup task
    asyncio.create_task(_run_mission_dedup(mission_id))
    return {"status": "started", "mission_id": mission_id}


async def _run_mission_dedup(mission_id: str):
    """Background task: deduplicate face embeddings for a specific mission with DB persistence."""
    global _dedup_progress
    _dedup_progress = {
        "mission_id": mission_id, "status": "running", "progress_pct": 0,
        "total_embeddings": 0, "processed": 0, "unique_found": 0, "error": None,
    }
    await upsert_dedup_run(
        mission_id=mission_id,
        status="running",
        progress_pct=0.0,
    )
    try:
        rows = await get_face_embeddings_by_mission(mission_id)
        total = len(rows)
        _dedup_progress["total_embeddings"] = total
        if total < 2:
            _dedup_progress.update(status="complete", progress_pct=100, unique_found=total, processed=total)
            await upsert_dedup_run(
                mission_id=mission_id,
                status="complete",
                total_embeddings=total,
                unique_found=total,
                duplicates_removed=0,
                progress_pct=100.0,
                completed_at=datetime.now(timezone.utc),
            )
            return

        _dedup_progress["progress_pct"] = 10  # fetched embeddings
        await upsert_dedup_run(
            mission_id=mission_id, status="running",
            total_embeddings=total, progress_pct=10.0,
        )

        ids        = [str(r["id"]) for r in rows]
        embeddings = [list(r["embedding"]) for r in rows]

        _dedup_progress["progress_pct"] = 20  # starting clustering
        await upsert_dedup_run(
            mission_id=mission_id, status="running",
            total_embeddings=total, progress_pct=20.0,
        )
        labels = cluster_embeddings(embeddings, cfg.REID_DISTANCE_THRESHOLD)

        _dedup_progress["progress_pct"] = 50  # clustering complete
        duplicate_ids, unique_count = find_duplicates_in_clusters(ids, labels)
        dup_set = set(duplicate_ids)

        _dedup_progress["progress_pct"] = 60  # updating DB
        await update_cluster_labels_db(ids, labels, dup_set)

        _dedup_progress["progress_pct"] = 70  # upserting identities
        await upsert_dedup_run(
            mission_id=mission_id, status="running",
            total_embeddings=total, unique_found=unique_count,
            duplicates_removed=len(duplicate_ids), progress_pct=70.0,
        )
        reps = get_identity_representatives(ids, labels)
        processed = 0
        for cluster_id, rep_id in reps.items():
            rep_row = next((r for r in rows if str(r["id"]) == rep_id), None)
            if rep_row:
                await upsert_person_identity(
                    cluster_id=cluster_id,
                    best_image_key=rep_row.get("object_key"),
                    drone_id=rep_row.get("drone_id"),
                    embedding_count=sum(1 for l in labels if l == cluster_id),
                )
            processed += 1
            pct = 70 + int(30 * processed / max(len(reps), 1))
            _dedup_progress["progress_pct"] = min(pct, 99)
            _dedup_progress["processed"] = processed

        _dedup_progress.update(
            status="complete", progress_pct=100,
            unique_found=unique_count, processed=total,
        )
        await upsert_dedup_run(
            mission_id=mission_id,
            status="complete",
            total_embeddings=total,
            unique_found=unique_count,
            duplicates_removed=len(duplicate_ids),
            progress_pct=100.0,
            completed_at=datetime.now(timezone.utc),
        )
        log.info("Mission dedup %s: %d embeddings → %d unique", mission_id, total, unique_count)
    except Exception as exc:
        log.warning("Mission dedup %s failed: %s", mission_id, exc)
        _dedup_progress.update(status="error", error=str(exc))
        await upsert_dedup_run(
            mission_id=mission_id,
            status="error",
            error_message=str(exc),
        )


@app.get("/faces/deduplicate/progress")
async def dedup_progress(mission_id: str = ""):
    """Return current mission deduplication progress from memory or DB."""
    if mission_id and _dedup_progress.get("mission_id") != mission_id:
        db_run = await get_dedup_run(mission_id)
        if db_run:
            return {
                "mission_id": db_run["mission_id"],
                "status": db_run["status"],
                "progress_pct": int(db_run["progress_pct"] or 0),
                "total_embeddings": db_run["total_embeddings"] or 0,
                "processed": db_run["total_embeddings"] if db_run["status"] == "complete" else 0,
                "unique_found": db_run["unique_found"] or 0,
                "error": db_run["error_message"],
            }
    return _dedup_progress


@app.get("/missions/dedup-status")
async def missions_dedup_status():
    """List missions with their face embedding counts and dedup status."""
    missions = await get_distinct_missions_face_embeddings()
    result = []
    for m in missions:
        total = m.get("total_faces", 0)
        clustered = m.get("clustered", 0)
        dedup_status = m.get("dedup_status")
        is_deduped = (dedup_status == "complete") or (clustered > 0 and clustered >= total * 0.8)
        result.append({
            "mission_id":      m["mission_id"],
            "total_faces":     total,
            "duplicates":      m.get("duplicates", 0),
            "is_deduplicated": is_deduped,
            "dedup_status":    dedup_status or ("complete" if is_deduped else "pending"),
            "unique_found":    m.get("unique_found"),
            "first_seen":      str(m.get("first_seen", "")),
            "last_seen":       str(m.get("last_seen", "")),
            "completed_at":    str(m.get("dedup_completed_at", "") or ""),
        })
    return {"missions": result, "count": len(result)}


@app.get("/missions/dedup-runs")
async def list_dedup_runs():
    """List all dedup run records from DB."""
    runs = await get_dedup_runs()
    return [{k: str(v) if hasattr(v, 'isoformat') else v for k, v in r.items()} for r in runs]


@app.post("/reid/trigger")
async def reid_trigger():
    """Manually trigger Re-ID clustering."""
    asyncio.create_task(_run_reid_clustering())
    return {"status": "clustering_started"}


# ── Person identity endpoints ──────────────────────────────────────────────────
@app.get("/persons/identities")
async def list_identities(limit: int = 50, drone_id: str = "", mission_id: str = ""):
    """List unique person identities (Re-ID clusters), optionally filtered by mission."""
    identities = await get_person_identities(limit=min(limit, 200), drone_id=drone_id or None)
    # Add presigned URLs for best images (face + person body)
    for identity in identities:
        if identity.get("best_image_key"):
            url = await minio.get_presigned_url(
                cfg.BUCKET_FACES,
                identity["best_image_key"],
                cfg.MINIO_URL_EXPIRY,
            )
            identity["best_image_url"] = url
        if identity.get("best_person_image_key"):
            person_url = await minio.get_presigned_url(
                cfg.BUCKET_FACES,
                identity["best_person_image_key"],
                cfg.MINIO_URL_EXPIRY,
            )
            identity["best_person_image_url"] = person_url
    return {"count": len(identities), "identities": identities}


@app.get("/persons/count")
async def person_count():
    """Current active person tracks."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT COUNT(*) as cnt FROM tracks
        WHERE track_type='person'
          AND last_seen > now() - interval '5 minutes'
        """
    )
    return {"active_persons": row["cnt"] if row else 0}


@app.get("/faces/gallery")
async def face_gallery(limit: int = 50, drone_id: str = "", mission_id: str = ""):
    """Face thumbnail gallery — one best face per Re-ID cluster, filterable by mission."""
    pool = await get_pool()
    conditions = ["is_duplicate = false", "object_key IS NOT NULL"]
    params = []
    idx = 1
    if drone_id:
        conditions.append(f"drone_id = ${idx}")
        params.append(drone_id)
        idx += 1
    if mission_id:
        conditions.append(f"mission_id = ${idx}")
        params.append(mission_id)
        idx += 1
    params.append(limit)
    where = " AND ".join(conditions)
    rows = await pool.fetch(
        f"""
        SELECT DISTINCT ON (cluster_id)
            id::text, cluster_id, object_key, drone_id, mission_id, created_at
        FROM face_embeddings
        WHERE {where}
        ORDER BY cluster_id, created_at DESC
        LIMIT ${idx}
        """,
        *params,
    )
    gallery = []
    for r in rows:
        item = dict(r)
        if item.get("object_key"):
            item["image_url"] = await minio.get_presigned_url(
                cfg.BUCKET_FACES, item["object_key"], cfg.MINIO_URL_EXPIRY
            )
        gallery.append(item)
    return {"count": len(gallery), "gallery": gallery}


# ── Track queries ──────────────────────────────────────────────────────────────
@app.get("/tracks/active")
async def active_tracks():
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM tracks WHERE track_type='person' ORDER BY last_seen DESC LIMIT 50"
    )
    return [{k: str(v) if hasattr(v, 'isoformat') else v for k, v in dict(r).items()} for r in rows]


@app.get("/detections/recent")
async def recent_detections(limit: int = 50):
    from shared.db import get_recent_confirmed_events
    rows = await get_recent_confirmed_events(limit=limit)
    persons = [r for r in rows if r.get("event_type") == "person_detected"]
    return {"count": len(persons), "detections": persons}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8102, reload=False)
