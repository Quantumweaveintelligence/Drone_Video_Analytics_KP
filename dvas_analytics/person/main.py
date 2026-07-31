# -*- coding: utf-8 -*-
"""
Person & Face Analyzer Service — FastAPI on port 8102.
Person tracking with ByteTrack, face detection, Re-ID, and MinIO crop storage.
"""
import asyncio
import base64
import hashlib
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional

import cv2
import numpy as np
import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

sys.path.insert(0, "/analytics")
from shared.config import cfg
from shared.db import (
    close_pool, ensure_drone, get_pool, upsert_track,
    write_confirmed_event, get_all_face_embeddings, delete_face_embeddings,
    write_face_embedding, update_face_embedding_cluster,
    write_evidence_frame, get_person_identities, upsert_person_identity,
)
from shared.redis_client import (
    close_client, publish_analytics_result, publish_confirmed_event, xread_frames,
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


@app.on_event("startup")
async def startup():
    global analyzer, resnet_model, resnet_device
    await get_pool()
    for drone_id in cfg.DRONE_IDS:
        await ensure_drone(drone_id)
    await minio.ensure_all_buckets()
    analyzer = PersonFaceAnalyzer(PERSON_MODEL, FACE_MODEL)
    resnet_device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    resnet_model = load_resnet_model(resnet_device)
    log.info("Person & Face Analyzer ready")


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
                        frame, drone_id=drone_id, save_crops=True
                    )
                    ts = datetime.now(timezone.utc)

                    for person in result["persons"]:
                        tracker_id = person.get("tracker_id")
                        if person["confidence"] >= 0.4:
                            track_id = await upsert_track(
                                drone_id, None, "person", ts, ts,
                                tracker_id=tracker_id
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
                                )

                    # Also clean up person crop_bytes
                    for person in result["persons"]:
                        person.pop("crop_bytes", None)

                    await publish_analytics_result("person", result)
                    _frames_processed += 1
                    _reid_frame_counter += 1

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


@app.post("/reid/trigger")
async def reid_trigger():
    """Manually trigger Re-ID clustering."""
    asyncio.create_task(_run_reid_clustering())
    return {"status": "clustering_started"}


# ── Person identity endpoints ──────────────────────────────────────────────────
@app.get("/persons/identities")
async def list_identities(limit: int = 50):
    """List unique person identities (Re-ID clusters)."""
    identities = await get_person_identities(limit=min(limit, 200))
    # Add presigned URLs for best images
    for identity in identities:
        if identity.get("best_image_key"):
            url = await minio.get_presigned_url(
                cfg.BUCKET_FACES,
                identity["best_image_key"],
                cfg.MINIO_URL_EXPIRY,
            )
            identity["best_image_url"] = url
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
async def face_gallery(limit: int = 50):
    """Face thumbnail gallery — one best face per Re-ID cluster."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT DISTINCT ON (cluster_id)
            id::text, cluster_id, object_key, drone_id, created_at
        FROM face_embeddings
        WHERE is_duplicate = false AND object_key IS NOT NULL
        ORDER BY cluster_id, created_at DESC
        LIMIT $1
        """,
        limit,
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
