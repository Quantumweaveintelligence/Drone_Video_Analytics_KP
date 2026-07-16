# -*- coding: utf-8 -*-
"""
Person & Face Analyzer Service — FastAPI app on port 8102.
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
import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

sys.path.insert(0, "/analytics")
from shared.config import cfg
from shared.db import (
    close_pool, ensure_drone, get_pool, upsert_track,
    write_confirmed_event, get_all_face_embeddings, delete_face_embeddings,
    write_face_embedding,
)
from shared.redis_client import (
    close_client, publish_analytics_result, publish_confirmed_event, xread_frames,
)
from person.analyzer import PersonFaceAnalyzer
from person.deduplicator import (
    cluster_embeddings, find_duplicates_in_clusters, load_resnet_model,
)
from person.schema import FrameAnalysisResult, DeduplicationResult

logging.basicConfig(level=cfg.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("person-service")

app = FastAPI(
    title="DVAS Person & Face Analyzer",
    description="Person tracking, face detection, and Re-ID service",
    version="1.0.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

PERSON_MODEL = os.getenv("PERSON_MODEL", "/analytics/person/models/yolov8m.pt")
FACE_MODEL   = os.getenv("FACE_MODEL",   "/analytics/person/models/yolov8n-face.pt")

analyzer: Optional[PersonFaceAnalyzer] = None
resnet_model = None
resnet_device = None
_stream_task: Optional[asyncio.Task] = None
_stream_running = False
_frames_processed = 0


@app.on_event("startup")
async def startup():
    global analyzer, resnet_model, resnet_device
    await get_pool()
    for drone_id in cfg.DRONE_IDS:
        await ensure_drone(drone_id)
    analyzer = PersonFaceAnalyzer(PERSON_MODEL, FACE_MODEL)
    resnet_device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    from person.deduplicator import load_resnet_model
    resnet_model = load_resnet_model(resnet_device)
    log.info("Person & Face Analyzer ready")


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
        "service":          "person-analyzer",
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
        raise HTTPException(503, "Models not loaded")
    contents = await file.read()
    nparr    = np.frombuffer(contents, np.uint8)
    frame    = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if frame is None:
        raise HTTPException(400, "Cannot decode image")

    result = analyzer.analyze_frame(frame, drone_id=drone_id, save_crops=save_crops)
    ts     = datetime.now(timezone.utc)

    # Persist tracks + confirmed events for each detected person
    for person in result["persons"]:
        track_id = await upsert_track(drone_id, None, "person", ts, ts)
        person["track_id"] = track_id
        if person["confidence"] >= 0.4:
            event_id = await write_confirmed_event(
                drone_id=drone_id, event_type="person_detected",
                confidence=person["confidence"], ts=ts, track_id=track_id,
            )
            person["event_id"] = event_id

    # Store face embeddings in DB for later deduplication
    for face in result["faces"]:
        if face.get("crop_b64") and resnet_model:
            from person.deduplicator import extract_embedding_from_bytes
            img_bytes = base64.b64decode(face["crop_b64"])
            emb = extract_embedding_from_bytes(resnet_model, img_bytes, resnet_device)
            if emb:
                emb_id = await write_face_embedding(
                    track_id=face.get("track_id"),
                    image_path="",
                    embedding=emb,
                    drone_id=drone_id,
                )
                face["embedding_id"] = emb_id

    await publish_analytics_result("person", result)
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
                    for person in result["persons"]:
                        if person["confidence"] >= 0.4:
                            track_id = await upsert_track(drone_id, None, "person", ts, ts)
                            await write_confirmed_event(
                                drone_id=drone_id, event_type="person_detected",
                                confidence=person["confidence"], ts=ts, track_id=track_id,
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


@app.get("/faces/deduplicate")
async def deduplicate_faces():
    """
    Run clustering on all stored face embeddings and mark/delete duplicates.
    Best approach: cosine Agglomerative Clustering (from person_dedupe.py)
    """
    rows = await get_all_face_embeddings()
    if len(rows) < 2:
        return {"message": "Not enough embeddings for deduplication", "total": len(rows)}

    ids        = [str(r["id"]) for r in rows]
    embeddings = [list(r["embedding"]) for r in rows]

    labels = cluster_embeddings(embeddings)
    duplicate_ids, unique_count = find_duplicates_in_clusters(ids, labels)

    deleted = await delete_face_embeddings(duplicate_ids)
    log.info("Deduplication: %d unique, %d duplicates deleted", unique_count, deleted)

    return {
        "total_embeddings":  len(rows),
        "unique_persons":    unique_count,
        "duplicates_found":  len(duplicate_ids),
        "duplicates_deleted": deleted,
    }


@app.get("/tracks/active")
async def active_tracks():
    from shared.db import get_pool
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM tracks WHERE track_type='person' ORDER BY last_seen DESC LIMIT 50"
    )
    return [{k: v for k, v in dict(r).items()} for r in rows]


@app.get("/detections/recent")
async def recent_detections(limit: int = 50):
    from shared.db import get_recent_confirmed_events
    rows = await get_recent_confirmed_events(limit=limit)
    persons = [r for r in rows if r.get("event_type") == "person_detected"]
    return {"count": len(persons), "detections": persons}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8102, reload=False)
