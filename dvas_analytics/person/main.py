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
from collections import defaultdict, deque

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
    write_person_embedding, get_all_person_embeddings, get_person_embeddings_by_mission,
    update_person_embedding_cluster, get_distinct_missions_person_embeddings,
    write_evidence_frame, get_person_identities, upsert_person_identity,
    get_face_embeddings_by_mission, get_distinct_missions_face_embeddings,
    upsert_dedup_run, get_dedup_run, get_dedup_runs,
    # ── Phase 1 watchlist DB functions ──
    add_to_person_watchlist, get_person_watchlist, get_person_watchlist_entry,
    update_person_watchlist, remove_from_person_watchlist, delete_person_watchlist,
    write_watchlist_embedding, get_watchlist_embeddings, get_all_active_watchlist_with_embeddings,
    write_person_watchlist_alert, get_person_watchlist_alerts,
    confirm_watchlist_alert, dismiss_watchlist_alert,
)
from shared.redis_client import (
    close_client, publish_analytics_result, publish_confirmed_event, xread_frames, xreadgroup_frames, ack_frames,
    scan_active_stream_drones,
)
from shared import minio_client as minio
from person.analyzer import PersonFaceAnalyzer
from person.deduplicator import (
    cluster_embeddings, find_duplicates_in_clusters, load_resnet_model,
    extract_embedding_from_bytes, get_identity_representatives,
    update_cluster_labels_db, update_person_cluster_labels_db, get_sharpness_score,
)
from person.schema import FrameAnalysisResult, DeduplicationResult
from person.face_recognizer import (
    ArcFaceRecognizer, WatchlistMatcher, AlertDeduplicator,
    enhance_face_crop, compute_sharpness, compute_sha256, build_evidence_metadata,
)

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
_drone_last_frame_ts: dict = {}    # {drone_id: monotonic_timestamp}
_person_trajectories: dict = defaultdict(lambda: defaultdict(lambda: deque(maxlen=30)))
_tracker_last_seen: dict = defaultdict(dict)  # {drone_id: {tracker_id: frame_idx}}
_live_person_buffer: dict = {}     # {(drone_id, tracker_id): {entry dict}}
_LIVE_BUFFER_MAX = 100
_LIVE_BUFFER_STALE_SEC = 60
_CROP_REFRESH_EVERY = 5           # refresh crops every Nth frame per tracker
_dedup_progress: dict = {          # mission dedup progress tracking
    "mission_id": None, "status": "idle", "progress_pct": 0,
    "total_embeddings": 0, "processed": 0, "unique_found": 0,
    "error": None,
}

# ── Face Recognition (ArcFace) globals ─────────────────────────────────────────
arcface_recognizer: Optional[ArcFaceRecognizer] = None
watchlist_matcher:  Optional[WatchlistMatcher]  = None
alert_deduplicator: Optional[AlertDeduplicator] = None

# Annotation toggle state — configurable per-drone via API
_annotation_config: dict = {
    "person_boxes":     True,
    "face_boxes":       True,
    "track_ids":        True,
    "confidence":       True,
    "watchlist_alerts": False,
    "alert_labels":     False,
    "trajectory":       False,
}


@app.on_event("startup")
async def startup():
    global analyzer, resnet_model, resnet_device, _stream_running, _stream_task
    global arcface_recognizer, watchlist_matcher, alert_deduplicator
    await get_pool()
    for drone_id in cfg.DRONE_IDS:
        await ensure_drone(drone_id)
    await minio.ensure_all_buckets()
    analyzer = PersonFaceAnalyzer(PERSON_MODEL, FACE_MODEL)
    resnet_device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    resnet_model = load_resnet_model(resnet_device)
    log.info("Person & Face Analyzer ready")

    # ── ArcFace Recognition Init ──────────────────────────────────────────
    if cfg.ARCFACE_ENABLED:
        try:
            arcface_recognizer = ArcFaceRecognizer(cfg.ARCFACE_MODEL_PATH)
            if arcface_recognizer.ready:
                watchlist_matcher = WatchlistMatcher()
                alert_deduplicator = AlertDeduplicator(cfg.ARCFACE_ALERT_COOLDOWN_S)
                await watchlist_matcher.reload_gallery()
                log.info(
                    "ArcFace recognition active — provider: %s, gallery: %d entries",
                    arcface_recognizer._provider,
                    watchlist_matcher.gallery_size,
                )
            else:
                log.warning("ArcFace model not ready — recognition disabled")
                arcface_recognizer = None
        except Exception as exc:
            log.error("ArcFace init failed (recognition disabled): %s", exc)
            arcface_recognizer = None
    else:
        log.info("ArcFace recognition disabled via ARCFACE_ENABLED=false")

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
    now_mono = time.monotonic()
    live_missions = {
        d: m for d, m in _active_drone_missions.items()
        if now_mono - _drone_last_frame_ts.get(d, 0) <= 20.0
    }
    return {
        "status":           "ok",
        "service":          "person-analyzer",
        "model_loaded":     analyzer.ready if analyzer else False,
        "stream_running":   _stream_running,
        "frames_processed": _frames_processed,
        "active_missions":  live_missions,
        "arcface_loaded":   arcface_recognizer.ready if arcface_recognizer else False,
        "arcface_provider": arcface_recognizer._provider if arcface_recognizer else "none",
        "watchlist_size":   watchlist_matcher.gallery_size if watchlist_matcher else 0,
        "alert_cooldown_s": alert_deduplicator.cooldown if alert_deduplicator else 0,
        "annotation_config": dict(_annotation_config),
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


# ── Tactical HUD Drawing Helpers ───────────────────────────────────────────────

def _draw_corner_brackets(img, bbox, color, thickness=3, length=20):
    """Draw tactical corner brackets around a bounding box (police HUD style)."""
    x1, y1, x2, y2 = bbox["x1"], bbox["y1"], bbox["x2"], bbox["y2"]
    # Top-left
    cv2.line(img, (x1, y1), (x1 + length, y1), color, thickness)
    cv2.line(img, (x1, y1), (x1, y1 + length), color, thickness)
    # Top-right
    cv2.line(img, (x2, y1), (x2 - length, y1), color, thickness)
    cv2.line(img, (x2, y1), (x2, y1 + length), color, thickness)
    # Bottom-left
    cv2.line(img, (x1, y2), (x1 + length, y2), color, thickness)
    cv2.line(img, (x1, y2), (x1, y2 - length), color, thickness)
    # Bottom-right
    cv2.line(img, (x2, y2), (x2 - length, y2), color, thickness)
    cv2.line(img, (x2, y2), (x2, y2 - length), color, thickness)


def _draw_label_bg(img, text, x, y, color):
    """Draw text with a filled background rectangle."""
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    cv2.rectangle(img, (x, y - th - 6), (x + tw + 8, y), color, -1)
    cv2.putText(img, text, (x + 4, y - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                (255, 255, 255), 1, cv2.LINE_AA)


# ── Background Task & Alert Processing Helpers ─────────────────────────────────

_person_clustering_lock = asyncio.Lock()
_face_clustering_lock = asyncio.Lock()


async def _safe_task(coro, label: str = ""):
    """Safely execute a background task with error logging."""
    try:
        return await coro
    except Exception as exc:
        log.warning("Background task '%s' failed: %s", label, exc)


async def _process_watchlist_alert(
    match,
    tid,
    face_crop_bytes: bytes,
    frame: np.ndarray,
    drone_id: str,
    mission_id: Optional[str],
    _ts_alert: datetime,
):
    """
    Process a confirmed watchlist match:
    1. Uploads face crop and encoded frame to MinIO.
    2. Writes alert to PostgreSQL to acquire true UUID alert_id.
    3. Builds Section 65B metadata linking the actual alert_id and uploads JSON to MinIO.
    4. Emits real-time SSE event via Redis for dashboard display and audio chime.
    """
    try:
        _face_sha = compute_sha256(face_crop_bytes)

        # Offload frame encoding to worker thread
        _, _frame_buf = await asyncio.to_thread(cv2.imencode, ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        _frame_bytes = bytes(_frame_buf)
        _frame_sha = compute_sha256(_frame_bytes)

        _alert_ts_ms = int(_ts_alert.timestamp() * 1000)
        _alert_face_key = minio.make_key(
            "alerts", drone_id,
            f"alert_{_alert_ts_ms}_{match.watchlist_id[:8]}_face.jpg",
            mission_id=mission_id or "",
        )
        _alert_frame_key = minio.make_key(
            "alerts", drone_id,
            f"alert_{_alert_ts_ms}_{match.watchlist_id[:8]}_frame.jpg",
            mission_id=mission_id or "",
        )
        _evidence_key = minio.make_key(
            "alerts", drone_id,
            f"evidence_{_alert_ts_ms}_{match.watchlist_id[:8]}.json",
            mission_id=mission_id or "",
        )

        # 1. Upload evidence images to MinIO
        await asyncio.gather(
            minio.upload_bytes(cfg.BUCKET_FACES, _alert_face_key, face_crop_bytes, "image/jpeg"),
            minio.upload_bytes(cfg.BUCKET_EVIDENCE, _alert_frame_key, _frame_bytes, "image/jpeg"),
        )

        # 2. Write alert to DB first to acquire true UUID alert_id
        alert_id = await write_person_watchlist_alert(
            watchlist_id=match.watchlist_id,
            name=match.name,
            category=match.category,
            confidence=match.similarity,
            drone_id=drone_id,
            mission_id=mission_id,
            tracker_id=tid,
            face_crop_key=_alert_face_key,
            frame_crop_key=_alert_frame_key,
            face_crop_sha256=_face_sha,
            frame_sha256=_frame_sha,
            evidence_packet_key=_evidence_key,
        )

        # 3. Build Section 65B evidence metadata with true alert_id
        _evidence_meta = build_evidence_metadata(
            alert_id=alert_id,
            watchlist_id=match.watchlist_id,
            name=match.name,
            category=match.category,
            drone_id=drone_id,
            mission_id=mission_id,
            tracker_id=tid,
            confidence=match.similarity,
            face_crop_sha256=_face_sha,
            frame_sha256=_frame_sha,
            detected_at_iso=_ts_alert.isoformat(),
        )
        import json as _json_mod
        _evidence_json = _json_mod.dumps(_evidence_meta, indent=2, default=str).encode("utf-8")
        await minio.upload_bytes(cfg.BUCKET_EVIDENCE, _evidence_key, _evidence_json, "application/json")

        # 4. Publish real-time alert via Redis for gateway SSE multiplexer
        await publish_analytics_result("person_alert", {
            "id": alert_id,
            "type": "watchlist_match",
            "watchlist_id": match.watchlist_id,
            "name": match.name,
            "category": match.category,
            "similarity": match.similarity,
            "drone_id": drone_id,
            "mission_id": mission_id,
            "tracker_id": tid,
            "ts": _ts_alert.isoformat(),
            "face_crop_key": _alert_face_key,
        })
        log.info(
            "🚨 WATCHLIST ALERT: '%s' (%s) matched on %s [sim=%.3f, tracker=%s, alert_id=%s]",
            match.name, match.category, drone_id, match.similarity, tid, alert_id,
        )
    except Exception as exc:
        log.exception("Watchlist alert processing failed for %s: %s", match.name, exc)


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

            # Evict missions and trajectory buffers for drones inactive for > 20s
            now_mono = time.monotonic()
            for d in list(_active_drone_missions.keys()):
                if now_mono - _drone_last_frame_ts.get(d, 0) > 20.0:
                    _active_drone_missions.pop(d, None)
                    _person_trajectories.pop(d, None)
                    _tracker_last_seen.pop(d, None)

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

                    _drone_last_frame_ts[drone_id] = time.monotonic()

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

                    # ── ArcFace recognition on detected faces ─────────────
                    recognition_results = {}  # tracker_id -> MatchResult
                    if (arcface_recognizer and arcface_recognizer.ready
                            and watchlist_matcher and watchlist_matcher.gallery_size > 0):
                        for face in result.get("faces", []):
                            face_crop_bytes = face.get("crop_bytes")
                            if not face_crop_bytes:
                                fb = face.get("bbox")
                                if fb:
                                    h_f, w_f = frame.shape[:2]
                                    fx1 = max(0, fb["x1"]); fy1 = max(0, fb["y1"])
                                    fx2 = min(w_f, fb["x2"]); fy2 = min(h_f, fb["y2"])
                                    if fx2 > fx1 and fy2 > fy1:
                                        _, fb_buf = cv2.imencode(
                                            ".jpg", frame[fy1:fy2, fx1:fx2],
                                            [cv2.IMWRITE_JPEG_QUALITY, 90]
                                        )
                                        face_crop_bytes = bytes(fb_buf)
                                        face["crop_bytes"] = face_crop_bytes
                            if not face_crop_bytes:
                                continue
                            nparr_fc = np.frombuffer(face_crop_bytes, np.uint8)
                            face_bgr = cv2.imdecode(nparr_fc, cv2.IMREAD_COLOR)
                            if face_bgr is None:
                                continue

                            # Enhance for drone footage (offload to worker thread)
                            if cfg.FACE_ENHANCE_ENABLED:
                                face_bgr = await asyncio.to_thread(enhance_face_crop, face_bgr)

                            # Extract ArcFace embedding in worker thread (prevents event loop blocking)
                            embedding = await asyncio.to_thread(arcface_recognizer.extract_embedding, face_bgr)
                            if embedding is None:
                                continue

                            # Match against in-memory gallery
                            match = watchlist_matcher.match(
                                embedding, cfg.ARCFACE_MATCH_THRESHOLD
                            )
                            if match:
                                tid = face.get("tracker_id")
                                recognition_results[tid] = match

                                # Deduplication-gated alert pipeline
                                if alert_deduplicator and alert_deduplicator.should_alert(match.watchlist_id):
                                    _ts_alert = datetime.now(timezone.utc)
                                    asyncio.create_task(_process_watchlist_alert(
                                        match=match,
                                        tid=tid,
                                        face_crop_bytes=face_crop_bytes,
                                        frame=frame,
                                        drone_id=drone_id,
                                        mission_id=mission_id,
                                        _ts_alert=_ts_alert,
                                    ))

                    # Tag result with recognition data for SSE consumers
                    result["recognitions"] = {
                        str(tid): {
                            "name": m.name, "category": m.category,
                            "similarity": m.similarity,
                            "watchlist_id": m.watchlist_id,
                        }
                        for tid, m in recognition_results.items()
                    }

                    # ── Draw annotated overlay with configurable toggles ──
                    annotated = frame.copy()
                    ac = _annotation_config  # snapshot for this frame

                    # ── Trajectory state update for active persons ──
                    for p in result["persons"]:
                        tid = p.get("tracker_id", 0)
                        if tid > 0:
                            bb = p["bbox"]
                            # Center-bottom of bounding box (feet position on ground plane)
                            pt = ((bb["x1"] + bb["x2"]) // 2, bb["y2"] - 2)
                            _person_trajectories[drone_id][tid].append(pt)
                            _tracker_last_seen[drone_id][tid] = _frames_processed

                    # Periodic cleanup of lost tracks (every 50 frames)
                    if _frames_processed % 50 == 0:
                        stale_tids = [
                            t for t, f_seen in _tracker_last_seen[drone_id].items()
                            if _frames_processed - f_seen > 60
                        ]
                        for t in stale_tids:
                            _person_trajectories[drone_id].pop(t, None)
                            _tracker_last_seen[drone_id].pop(t, None)

                    # ── Draw Trajectories if enabled ──
                    if ac.get("trajectory", False):
                        for tid, pts in _person_trajectories[drone_id].items():
                            if len(pts) >= 2:
                                poly_pts = np.array(list(pts), dtype=np.int32).reshape((-1, 1, 2))
                                # Black outline for sharp contrast against any ground/lighting
                                cv2.polylines(annotated, [poly_pts], isClosed=False, color=(0, 0, 0), thickness=3, lineType=cv2.LINE_AA)
                                # Vibrant tactical vector path (neon cyan/sky blue)
                                cv2.polylines(annotated, [poly_pts], isClosed=False, color=(255, 215, 0), thickness=2, lineType=cv2.LINE_AA)
                                # Target marker dot at latest known position
                                cv2.circle(annotated, pts[-1], 3, (0, 255, 255), -1, lineType=cv2.LINE_AA)

                    for p in result["persons"]:
                        bb = p["bbox"]
                        tid = p.get("tracker_id", 0)
                        match = recognition_results.get(tid)

                        if match and ac.get("watchlist_alerts", True):
                            # WATCHLIST MATCH — tactical corner brackets
                            if match.category == "missing":
                                alert_color = (0, 165, 255)   # orange
                            elif match.category == "vip":
                                alert_color = (0, 255, 0)     # green
                            else:
                                alert_color = (0, 0, 255)     # red (wanted/poi)
                            _draw_corner_brackets(annotated, bb, alert_color, thickness=3, length=20)
                            if ac.get("alert_labels", True):
                                lbl = f"[{match.category.upper()}] {match.name} ({int(match.similarity*100)}%)"
                                _draw_label_bg(annotated, lbl, bb["x1"], bb["y1"] - 25, alert_color)
                        elif ac.get("person_boxes", True):
                            # Normal person box (existing teal)
                            cv2.rectangle(annotated, (bb["x1"], bb["y1"]), (bb["x2"], bb["y2"]),
                                          (191, 219, 56), 2)
                            if ac.get("track_ids", True):
                                tid_str = f'P-{tid}' if tid > 0 else f'P-{abs(tid)}'
                                label = tid_str
                                if ac.get("confidence", True):
                                    label += f'  {int(p["confidence"]*100)}%'
                                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                                cv2.rectangle(annotated, (bb["x1"], bb["y1"]-th-8),
                                              (bb["x1"]+tw+6, bb["y1"]), (191, 219, 56), -1)
                                cv2.putText(annotated, label, (bb["x1"]+3, bb["y1"]-4),
                                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)

                    if ac.get("face_boxes", True):
                        for fc in result["faces"]:
                            bb = fc["bbox"]
                            fc_tid = fc.get("tracker_id")
                            match = recognition_results.get(fc_tid)
                            if match and ac.get("watchlist_alerts", True):
                                # Matched face — crosshair marker
                                cx = (bb["x1"] + bb["x2"]) // 2
                                cy = (bb["y1"] + bb["y2"]) // 2
                                xh_color = (0, 0, 255) if match.category == "wanted" else (0, 165, 255)
                                cv2.drawMarker(annotated, (cx, cy), xh_color,
                                               cv2.MARKER_CROSS, 15, 2)
                            else:
                                cv2.rectangle(annotated, (bb["x1"], bb["y1"]),
                                              (bb["x2"], bb["y2"]), (0, 191, 255), 2)
                                if ac.get("confidence", True):
                                    conf_label = f'{int(fc["confidence"]*100)}%'
                                    cv2.putText(annotated, conf_label,
                                                (bb["x1"], bb["y2"]+14),
                                                cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                                                (0, 191, 255), 1, cv2.LINE_AA)

                    _, ann_buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    _last_annotated_frames[drone_id] = bytes(ann_buf)
                    _last_annotated_ts[drone_id] = time.monotonic()

                    ts = datetime.now(timezone.utc)

                    # ── Populate live person buffer with crop pairs IMMEDIATELY ──
                    # Must happen before any DB / network I/O so live UI is decoupled
                    _update_live_person_buffer(
                        result, drone_id, mission_id, ts, frame
                    )

                    # ── Database updates (isolated so DB errors don't crash stream) ──
                    try:
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
                    except Exception as db_exc:
                        log.warning("Person track/event write error: %s", db_exc)

                    # 1. Process whole-body person crops + person appearance embeddings
                    try:
                        for person in result.get("persons", []):
                            p_crop_bytes = person.pop("crop_bytes", None)
                            tid = person.get("tracker_id", 0)
                            if not p_crop_bytes and tid is not None:
                                # Fallback: crop from frame using person bbox
                                pbb = person.get("bbox")
                                if pbb:
                                    h, w = frame.shape[:2]
                                    x1, y1 = max(0, int(pbb["x1"])), max(0, int(pbb["y1"]))
                                    x2, y2 = min(w, int(pbb["x2"])), min(h, int(pbb["y2"]))
                                    if x2 > x1 and y2 > y1:
                                        _, p_buf = cv2.imencode(".jpg", frame[y1:y2, x1:x2], [cv2.IMWRITE_JPEG_QUALITY, 85])
                                        p_crop_bytes = bytes(p_buf)

                            if p_crop_bytes and resnet_model:
                                ts_ms = int(ts.timestamp() * 1000)
                                try:
                                    nparr = np.frombuffer(p_crop_bytes, np.uint8)
                                    img_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                                    sharpness = get_sharpness_score(img_bgr) if img_bgr is not None else 0.0
                                except Exception:
                                    sharpness = 0.0

                                person_key = minio.make_key(
                                    "persons", drone_id,
                                    f"person_{ts_ms}_{tid}.jpg"
                                )
                                person_obj_key = await minio.upload_bytes(
                                    cfg.BUCKET_FACES, person_key, p_crop_bytes, "image/jpeg"
                                )
                                p_emb = extract_embedding_from_bytes(
                                    resnet_model, p_crop_bytes, resnet_device
                                )
                                if p_emb and person_obj_key:
                                    await write_person_embedding(
                                        track_id=None,
                                        image_path=person_obj_key,
                                        embedding=p_emb,
                                        drone_id=drone_id,
                                        object_key=person_obj_key,
                                        mission_id=mission_id,
                                        tracker_id=tid,
                                        sharpness_score=sharpness,
                                    )
                    except Exception as person_exc:
                        log.warning("Person appearance embedding write error: %s", person_exc)

                    # 2. Process face crops + face embeddings (for Detected Faces gallery)
                    try:
                        for face in result.get("faces", []):
                            face_crop_bytes = face.pop("crop_bytes", None)
                            if face_crop_bytes and resnet_model:
                                emb = extract_embedding_from_bytes(
                                    resnet_model, face_crop_bytes, resnet_device
                                )
                                if emb:
                                    face_ts_ms = int(ts.timestamp() * 1000)
                                    key = minio.make_key(
                                        "faces", drone_id,
                                        f"face_{face_ts_ms}.jpg"
                                    )
                                    face_obj_key = await minio.upload_bytes(
                                        cfg.BUCKET_FACES, key, face_crop_bytes, "image/jpeg"
                                    )
                                    # Extract ArcFace embedding if available
                                    af_emb = None
                                    if arcface_recognizer and arcface_recognizer.ready:
                                        try:
                                            nparr_f = np.frombuffer(face_crop_bytes, np.uint8)
                                            f_bgr = cv2.imdecode(nparr_f, cv2.IMREAD_COLOR)
                                            if f_bgr is not None:
                                                if cfg.FACE_ENHANCE_ENABLED:
                                                    f_bgr = enhance_face_crop(f_bgr)
                                                af_emb = arcface_recognizer.extract_embedding(f_bgr, quality_check=False)
                                        except Exception:
                                            pass

                                    await write_face_embedding(
                                        track_id=None,
                                        image_path=face_obj_key or "",
                                        embedding=emb,
                                        drone_id=drone_id,
                                        object_key=face_obj_key,
                                        mission_id=mission_id,
                                        arcface_embedding=af_emb,
                                    )
                    except Exception as face_exc:
                        log.warning("Face embedding write error: %s", face_exc)

                    try:
                        await publish_analytics_result("person", result)
                    except Exception as pub_exc:
                        log.warning("Failed to publish analytics result: %s", pub_exc)

                    _frames_processed += 1
                    _reid_frame_counter += 1

                    # Periodic cleanup of alert deduplicator cooldown state
                    if alert_deduplicator and _frames_processed % 100 == 0:
                        alert_deduplicator.cleanup()

                    # Periodic re-ID clustering for both Persons and Faces
                    if _reid_frame_counter >= cfg.REID_CLUSTER_INTERVAL:
                        _reid_frame_counter = 0
                        asyncio.create_task(_safe_task(_run_person_reid_clustering(), "Person Re-ID Clustering"))
                        asyncio.create_task(_safe_task(_run_reid_clustering(), "Face Re-ID Clustering"))

            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning("Person stream error for %s: %s", drone_id, exc)
                await asyncio.sleep(1)


async def _run_person_reid_clustering():
    """Background Person Re-ID clustering on whole-body person embeddings with re-entrancy lock."""
    if _person_clustering_lock.locked():
        return
    async with _person_clustering_lock:
        try:
            rows = await get_all_person_embeddings()
            if len(rows) < 2:
                if len(rows) == 1:
                    r = rows[0]
                    await upsert_person_identity(
                        cluster_id=0,
                        best_image_key=None,
                        drone_id=r.get("drone_id"),
                        embedding_count=1,
                        best_person_image_key=r.get("object_key"),
                        mission_id=r.get("mission_id"),
                    )
                return

            ids = [str(r["id"]) for r in rows]
            embeddings = [list(r["embedding"]) for r in rows]
            sharpness_scores = [float(r.get("sharpness_score") or 0.0) for r in rows]

            labels = await asyncio.to_thread(cluster_embeddings, embeddings, cfg.REID_DISTANCE_THRESHOLD)
            duplicate_ids, unique_count = await asyncio.to_thread(find_duplicates_in_clusters, ids, labels, sharpness_scores)
            dup_set = set(duplicate_ids)
            await update_person_cluster_labels_db(ids, labels, dup_set)

            # Upsert person identities from best representative whole-body crops
            reps = get_identity_representatives(ids, labels, sharpness_scores)
            for cluster_id, rep_id in reps.items():
                rep_row = next((r for r in rows if str(r["id"]) == rep_id), None)
                if rep_row and rep_row.get("object_key"):
                    await upsert_person_identity(
                        cluster_id=cluster_id,
                        best_image_key=None,
                        drone_id=rep_row.get("drone_id"),
                        embedding_count=sum(1 for l in labels if l == cluster_id),
                        best_person_image_key=rep_row.get("object_key"),
                        mission_id=rep_row.get("mission_id"),
                    )
            log.info("Person Re-ID clustering: %d person embeddings → %d unique person identities", len(rows), unique_count)
        except Exception as exc:
            log.warning("Person Re-ID clustering failed: %s", exc)


async def _run_reid_clustering():
    """Background Face Re-ID clustering with re-entrancy lock."""
    if _face_clustering_lock.locked():
        return
    async with _face_clustering_lock:
        try:
            rows = await get_all_face_embeddings()
            if len(rows) < 2:
                return
            ids        = [str(r["id"]) for r in rows]
            embeddings = [list(r["embedding"]) for r in rows]
            labels = await asyncio.to_thread(cluster_embeddings, embeddings, cfg.REID_DISTANCE_THRESHOLD)
            duplicate_ids, unique_count = await asyncio.to_thread(find_duplicates_in_clusters, ids, labels)
            dup_set = set(duplicate_ids)
            await update_cluster_labels_db(ids, labels, dup_set)
            log.info("Face Re-ID clustering: %d embeddings → %d clusters", len(rows), unique_count)
        except Exception as exc:
            log.warning("Face Re-ID clustering failed: %s", exc)


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
    _active_drone_missions.clear()
    _drone_last_frame_ts.clear()
    _last_annotated_frames.clear()
    _last_annotated_ts.clear()
    _person_trajectories.clear()
    _tracker_last_seen.clear()
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
                age = now - _last_annotated_ts.get(alias, now)
                if age <= 30.0:
                    frame_bytes = _last_annotated_frames[alias]
                    matched_drone_id = alias
                    frame_age = age
                    break

    if not frame_bytes and _last_annotated_frames:
        best_drone = max(_last_annotated_ts, key=_last_annotated_ts.get, default=None)
        if best_drone:
            age = now - _last_annotated_ts.get(best_drone, now)
            if age <= 30.0:
                frame_bytes = _last_annotated_frames.get(best_drone)
                matched_drone_id = best_drone
                frame_age = age

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

    # Build face lookup by tracker_id OR spatial proximity to each person bbox
    faces_by_person = {}
    for fc in result.get("faces", []):
        tid = fc.get("tracker_id")
        if tid is not None:
            faces_by_person[tid] = fc
            continue
        # Link face to nearest person by checking if face center is inside person bbox
        fbb = fc["bbox"]
        fcx = (fbb["x1"] + fbb["x2"]) / 2
        fcy = (fbb["y1"] + fbb["y2"]) / 2
        for p in result.get("persons", []):
            pbb = p["bbox"]
            if pbb["x1"] <= fcx <= pbb["x2"] and pbb["y1"] <= fcy <= pbb["y2"]:
                ptid = p.get("tracker_id")
                if ptid is not None:
                    faces_by_person[ptid] = fc
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
    labels = await asyncio.to_thread(cluster_embeddings, embeddings, cfg.REID_DISTANCE_THRESHOLD)
    duplicate_ids, unique_count = await asyncio.to_thread(find_duplicates_in_clusters, ids, labels)
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


@app.post("/persons/deduplicate/mission")
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
    """Background task: deduplicate person and face embeddings for a specific mission with DB persistence."""
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
        # Prioritize whole-body person embeddings for this mission
        rows = await get_person_embeddings_by_mission(mission_id)
        is_person_pipeline = True
        if len(rows) < 2:
            # Fallback to face embeddings if no person embeddings exist
            rows = await get_face_embeddings_by_mission(mission_id)
            is_person_pipeline = False

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
        sharpness_scores = [float(r.get("sharpness_score") or 0.0) for r in rows]

        _dedup_progress["progress_pct"] = 20  # starting clustering
        await upsert_dedup_run(
            mission_id=mission_id, status="running",
            total_embeddings=total, progress_pct=20.0,
        )
        labels = await asyncio.to_thread(cluster_embeddings, embeddings, cfg.REID_DISTANCE_THRESHOLD)

        _dedup_progress["progress_pct"] = 50  # clustering complete
        duplicate_ids, unique_count = await asyncio.to_thread(find_duplicates_in_clusters, ids, labels, sharpness_scores)
        dup_set = set(duplicate_ids)

        _dedup_progress["progress_pct"] = 60  # updating DB
        if is_person_pipeline:
            await update_person_cluster_labels_db(ids, labels, dup_set)
        else:
            await update_cluster_labels_db(ids, labels, dup_set)

        _dedup_progress["progress_pct"] = 70  # upserting identities
        await upsert_dedup_run(
            mission_id=mission_id, status="running",
            total_embeddings=total, unique_found=unique_count,
            duplicates_removed=len(duplicate_ids), progress_pct=70.0,
        )
        reps = get_identity_representatives(ids, labels, sharpness_scores)
        processed = 0
        for cluster_id, rep_id in reps.items():
            rep_row = next((r for r in rows if str(r["id"]) == rep_id), None)
            if rep_row:
                if is_person_pipeline:
                    await upsert_person_identity(
                        cluster_id=cluster_id,
                        best_image_key=None,
                        drone_id=rep_row.get("drone_id"),
                        embedding_count=sum(1 for l in labels if l == cluster_id),
                        best_person_image_key=rep_row.get("object_key"),
                        mission_id=rep_row.get("mission_id") or mission_id,
                    )
                else:
                    person_key = rep_row.get("person_object_key")
                    await upsert_person_identity(
                        cluster_id=cluster_id,
                        best_image_key=rep_row.get("object_key"),
                        drone_id=rep_row.get("drone_id"),
                        embedding_count=sum(1 for l in labels if l == cluster_id),
                        best_person_image_key=person_key,
                        mission_id=rep_row.get("mission_id") or mission_id,
                    )
            processed += 1
            pct = 70 + int(30 * processed / max(len(reps), 1))
            _dedup_progress["progress_pct"] = min(pct, 99)
            _dedup_progress["processed"] = processed

        _dedup_progress.update(
            status="complete", progress_pct=100,
            unique_found=unique_count, processed=total,
            duplicates_removed=len(duplicate_ids),
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


@app.get("/persons/deduplicate/progress")
async def dedup_progress(mission_id: str = ""):
    """Return current mission deduplication progress from memory or DB."""
    if mission_id and _dedup_progress.get("mission_id") != mission_id:
        db_run = await get_dedup_run(mission_id)
        if db_run:
            return {
                "mission_id": mission_id,
                "status": db_run["status"],
                "progress_pct": int(db_run["progress_pct"] or 0),
                "total_embeddings": db_run["total_embeddings"] or 0,
                "processed": db_run["total_embeddings"] or 0,
                "unique_found": db_run["unique_found"] or 0,
                "error": db_run["error_message"],
            }
    return _dedup_progress


@app.get("/missions/dedup-status")
async def missions_dedup_status():
    """List missions with their person and face embedding counts and dedup status."""
    person_missions = await get_distinct_missions_person_embeddings()
    face_missions = await get_distinct_missions_face_embeddings()
    # Combine or prioritize person missions
    missions_map = {}
    for m in person_missions:
        mid = m["mission_id"]
        total = m.get("total_persons", 0)
        clustered = m.get("clustered", 0)
        dedup_status = m.get("dedup_status")
        is_deduped = (dedup_status == "complete") or (clustered > 0 and clustered >= total * 0.8)
        missions_map[mid] = {
            "mission_id": mid,
            "total_faces": 0,
            "total_persons": total,
            "duplicates": m.get("duplicates", 0),
            "is_deduplicated": is_deduped,
            "dedup_status": dedup_status or ("complete" if is_deduped else "pending"),
            "unique_found": m.get("unique_found"),
            "first_seen": str(m.get("first_seen", "")),
            "last_seen": str(m.get("last_seen", "")),
            "completed_at": str(m.get("dedup_completed_at", "") or ""),
        }
    for m in face_missions:
        mid = m["mission_id"]
        total_f = m.get("total_faces", 0)
        if mid in missions_map:
            missions_map[mid]["total_faces"] = total_f
        else:
            clustered = m.get("clustered", 0)
            dedup_status = m.get("dedup_status")
            is_deduped = (dedup_status == "complete") or (clustered > 0 and clustered >= total_f * 0.8)
            missions_map[mid] = {
                "mission_id": mid,
                "total_faces": total_f,
                "total_persons": 0,
                "duplicates": m.get("duplicates", 0),
                "is_deduplicated": is_deduped,
                "dedup_status": dedup_status or ("complete" if is_deduped else "pending"),
                "unique_found": m.get("unique_found"),
                "first_seen": str(m.get("first_seen", "")),
                "last_seen": str(m.get("last_seen", "")),
                "completed_at": str(m.get("dedup_completed_at", "") or ""),
            }
    result = list(missions_map.values())
    return {"missions": result, "count": len(result)}


@app.get("/missions/dedup-runs")
async def list_dedup_runs():
    """List all dedup run records from DB."""
    runs = await get_dedup_runs()
    return [{k: str(v) if hasattr(v, 'isoformat') else v for k, v in r.items()} for r in runs]


@app.post("/reid/trigger")
async def reid_trigger():
    """Manually trigger Person & Face Re-ID clustering."""
    asyncio.create_task(_run_person_reid_clustering())
    asyncio.create_task(_run_reid_clustering())
    return {"status": "clustering_started"}


# ── Person identity endpoints ──────────────────────────────────────────────────
@app.get("/persons/identities")
async def list_identities(limit: int = 50, drone_id: str = "", mission_id: str = ""):
    """List unique person identities (Re-ID clusters), optionally filtered by mission."""
    identities = await get_person_identities(
        limit=min(limit, 200),
        drone_id=drone_id or None,
        mission_id=mission_id or None,
    )
    # Concurrently generate presigned URLs for person body and face images
    async def _attach_urls(identity):
        p_task = (
            minio.get_presigned_url(cfg.BUCKET_FACES, identity["best_person_image_key"], cfg.MINIO_URL_EXPIRY)
            if identity.get("best_person_image_key") else None
        )
        f_task = (
            minio.get_presigned_url(cfg.BUCKET_FACES, identity["best_image_key"], cfg.MINIO_URL_EXPIRY)
            if identity.get("best_image_key") else None
        )
        if p_task and f_task:
            p_url, f_url = await asyncio.gather(p_task, f_task)
            identity["best_person_image_url"] = p_url
            identity["best_image_url"] = f_url
        elif p_task:
            identity["best_person_image_url"] = await p_task
        elif f_task:
            identity["best_image_url"] = await f_task
        return identity

    if identities:
        await asyncio.gather(*[_attach_urls(i) for i in identities])

    return {"count": len(identities), "identities": identities}


@app.get("/persons/deduplicate")
async def person_deduplicate_stats():
    """Return summary statistics of deduplicated person tracks."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT COUNT(*) AS total_persons,
               COUNT(*) FILTER (WHERE is_duplicate = true) AS duplicates,
               COUNT(DISTINCT cluster_id) FILTER (WHERE cluster_id IS NOT NULL) AS unique_identities
        FROM person_embeddings
        """
    )
    total = row["total_persons"] if row else 0
    dups = row["duplicates"] if row else 0
    uniq = row["unique_identities"] if row else 0
    # Fallback to face_embeddings if person_embeddings has no rows yet
    if total == 0:
        row_f = await pool.fetchrow(
            """
            SELECT COUNT(*) AS total_faces,
                   COUNT(*) FILTER (WHERE is_duplicate = true) AS duplicates,
                   COUNT(DISTINCT cluster_id) FILTER (WHERE cluster_id IS NOT NULL) AS unique_identities
            FROM face_embeddings
            """
        )
        total = row_f["total_faces"] if row_f else 0
        dups = row_f["duplicates"] if row_f else 0
        uniq = row_f["unique_identities"] if row_f else 0

    return {
        "total_persons": total,
        "total_faces": total,
        "unique_identities": uniq,
        "duplicates_found": dups,
        "dedup_rate": round((dups / total) * 100, 1) if total > 0 else 0,
    }


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
async def face_gallery(limit: int = 60, drone_id: str = "", mission_id: str = ""):
    """Face thumbnail gallery — deduplicated face crops, filterable by mission."""
    pool = await get_pool()
    conditions = ["is_duplicate = false", "object_key IS NOT NULL"]
    params = []
    idx = 1
    if mission_id:
        conditions.append(f"mission_id = ${idx}")
        params.append(mission_id)
        idx += 1
    elif drone_id:
        conditions.append(f"drone_id = ${idx}")
        params.append(drone_id)
        idx += 1
    params.append(min(limit, 200))
    where = " AND ".join(conditions)
    rows = await pool.fetch(
        f"""
        SELECT 
            id::text, cluster_id, object_key, drone_id, mission_id, created_at,
            (SELECT COUNT(*) FROM face_embeddings f2 
             WHERE (f2.cluster_id = face_embeddings.cluster_id AND face_embeddings.cluster_id IS NOT NULL)
                OR f2.id = face_embeddings.id) AS embedding_count
        FROM face_embeddings
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT ${idx}
        """,
        *params,
    )

    # If no results and mission_id or drone_id was specified, fall back to all face crops for that filter
    if len(rows) == 0 and (mission_id or drone_id):
        fallback_conds = ["object_key IS NOT NULL"]
        f_params = []
        f_idx = 1
        if mission_id:
            fallback_conds.append(f"mission_id = ${f_idx}")
            f_params.append(mission_id)
            f_idx += 1
        elif drone_id:
            fallback_conds.append(f"drone_id = ${f_idx}")
            f_params.append(drone_id)
            f_idx += 1
        f_params.append(min(limit, 200))
        f_where = " AND ".join(fallback_conds)
        rows = await pool.fetch(
            f"""
            SELECT 
                id::text, cluster_id, object_key, drone_id, mission_id, created_at,
                1 AS embedding_count
            FROM face_embeddings
            WHERE {f_where}
            ORDER BY created_at DESC
            LIMIT ${f_idx}
            """,
            *f_params,
        )

    async def _attach_face_url(r):
        item = dict(r)
        if item.get("object_key"):
            item["image_url"] = await minio.get_presigned_url(
                cfg.BUCKET_FACES, item["object_key"], cfg.MINIO_URL_EXPIRY
            )
        return item

    if rows:
        gallery = await asyncio.gather(*[_attach_face_url(r) for r in rows])
    else:
        gallery = []

    return {"count": len(gallery), "gallery": gallery}


# ══════════════════════════════════════════════════════════════════════════════
#  Annotation Config API
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/annotation/config")
async def get_annotation_config():
    """Return current annotation toggle state."""
    return dict(_annotation_config)


@app.put("/annotation/config")
async def set_annotation_config(body: dict):
    """Update annotation toggles. Only supplied keys are changed."""
    changed = {}
    for key in body:
        if key in _annotation_config:
            _annotation_config[key] = bool(body[key])
            changed[key] = _annotation_config[key]
    log.info("Annotation config updated: %s", changed)
    return dict(_annotation_config)


# ══════════════════════════════════════════════════════════════════════════════
#  Person Watchlist CRUD API (Phase 3)
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/watchlist/enroll")
async def watchlist_enroll(
    name: str = Form(...),
    category: str = Form("wanted"),
    case_ref: str = Form(None),
    description: str = Form(None),
    image: UploadFile = File(...),
):
    """
    Enroll a new person into the face watchlist.

    Pipeline:
      1. Validate image (JPEG/PNG, <10 MB).
      2. Detect face using YOLOv8n-face.
      3. Enhance the best face crop (CLAHE + Lanczos).
      4. Extract ArcFace 512-dim embedding.
      5. Generate 3 augmented embeddings (original + flip + bright).
      6. Upload reference image to MinIO.
      7. Store in DB: person_watchlist + watchlist_embeddings.
      8. Trigger gallery reload (atomic swap).
    """
    # ── Guard: ArcFace must be loaded ─────────────────────────────────────
    if not arcface_recognizer or not arcface_recognizer.ready:
        raise HTTPException(503, "Face recognition model not loaded — cannot enroll")

    # ── 1. Read and validate image ────────────────────────────────────────
    img_bytes = await image.read()
    if len(img_bytes) > 10 * 1024 * 1024:
        raise HTTPException(413, "Image too large (max 10 MB)")

    nparr = np.frombuffer(img_bytes, np.uint8)
    img_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img_bgr is None:
        raise HTTPException(400, "Cannot decode image — use JPEG or PNG")

    # ── 2. Detect face ────────────────────────────────────────────────────
    if not analyzer or not analyzer.face_model:
        raise HTTPException(503, "Face detection model not loaded")

    from person.analyzer import detect_faces_on_tiles
    face_boxes = detect_faces_on_tiles(analyzer.face_model, img_bgr, device=analyzer.device)

    if face_boxes:
        best_face = max(face_boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
        x1, y1, x2, y2, face_conf = best_face
        face_crop = img_bgr[max(0, int(y1)):int(y2), max(0, int(x1)):int(x2)]
    else:
        # Fallback 1: Direct full-image YOLO face detection without tiling
        results = analyzer.face_model(img_bgr, conf=0.15, device=analyzer.device, verbose=False)
        direct_boxes = []
        for res in results:
            for box in res.boxes:
                bx1, by1, bx2, by2 = box.xyxy[0].cpu().numpy()
                conf = float(box.conf[0].cpu().numpy())
                direct_boxes.append((int(bx1), int(by1), int(bx2), int(by2), conf))
        if direct_boxes:
            best_face = max(direct_boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
            x1, y1, x2, y2, face_conf = best_face
            face_crop = img_bgr[max(0, int(y1)):int(y2), max(0, int(x1)):int(x2)]
        else:
            # Fallback 2: Check if image is already a pre-cropped face/mugshot
            h_img, w_img = img_bgr.shape[:2]
            if h_img >= 30 and w_img >= 30:
                test_emb = arcface_recognizer.extract_embedding(img_bgr, quality_check=False)
                if test_emb:
                    face_crop = img_bgr
                    face_conf = 1.0  # Operator-provided tight face crop
                else:
                    raise HTTPException(422, "No face detected in the uploaded image — try a clearer photo")
            else:
                raise HTTPException(422, "No face detected in the uploaded image — try a clearer photo")

    if face_crop.size == 0:
        raise HTTPException(422, "Face crop is empty — try a different photo")

    # ── 3. Enhance for drone-quality reference ────────────────────────────
    if cfg.FACE_ENHANCE_ENABLED:
        face_crop = enhance_face_crop(face_crop)

    # ── 4. Quality check ──────────────────────────────────────────────────
    sharpness = compute_sharpness(face_crop)
    quality_warning = None
    if sharpness < 80:
        quality_warning = f"Low quality reference (sharpness={sharpness:.0f}). Enrollment proceeds, but match accuracy may be reduced."
        log.warning("Low quality enrollment for '%s': sharpness=%.1f", name, sharpness)

    # ── 5. Generate 3 augmented embeddings ────────────────────────────────
    aug_embeddings = arcface_recognizer.generate_augmented_embeddings(face_crop)
    if not aug_embeddings:
        raise HTTPException(
            422,
            "Could not extract face embedding — the face may be too small or blurry"
        )

    # ── 6. Upload reference image to MinIO ────────────────────────────────
    ref_sha256 = compute_sha256(img_bytes)
    ref_key = minio.make_key(
        "watchlist", "enrollment",
        f"{name.replace(' ', '_').lower()}_{int(time.time()*1000)}.jpg"
    )
    try:
        await minio.upload_bytes(cfg.BUCKET_WATCHLIST, ref_key, img_bytes, "image/jpeg")
    except Exception as exc:
        log.warning("MinIO upload for watchlist ref image failed: %s", exc)
        ref_key = None

    # ── 7. Store in DB ────────────────────────────────────────────────────
    wid = await add_to_person_watchlist(
        name=name.strip(),
        category=category.strip().lower(),
        case_ref=case_ref.strip() if case_ref else None,
        description=description.strip() if description else None,
        reference_image_key=ref_key,
        reference_image_sha256=ref_sha256,
    )

    # Store all augmented embeddings
    stored_emb_ids = []
    for emb, aug_type, q_score in aug_embeddings:
        eid = await write_watchlist_embedding(
            watchlist_id=wid,
            embedding=emb,
            augmentation_type=aug_type,
            quality_score=q_score,
        )
        stored_emb_ids.append(eid)

    # ── 8. Reload gallery ─────────────────────────────────────────────────
    if watchlist_matcher:
        await watchlist_matcher.reload_gallery()

    log.info(
        "Watchlist enrollment: '%s' (%s) — %d embeddings, gallery now %d",
        name, category, len(stored_emb_ids),
        watchlist_matcher.gallery_size if watchlist_matcher else 0,
    )

    return {
        "status": "enrolled",
        "watchlist_id": wid,
        "name": name.strip(),
        "category": category.strip().lower(),
        "embeddings_stored": len(stored_emb_ids),
        "face_confidence": round(face_conf, 4),
        "sharpness": round(sharpness, 1),
        "quality_warning": quality_warning,
        "gallery_size": watchlist_matcher.gallery_size if watchlist_matcher else 0,
        "faces_detected": len(face_boxes),
    }


@app.get("/watchlist")
async def watchlist_list(category: str = "", limit: int = 100, offset: int = 0):
    """List all active watchlist entries with presigned reference image URLs."""
    entries = await get_person_watchlist(
        category=category.strip().lower() if category else None,
        limit=min(limit, 200),
        offset=offset,
    )

    # Add presigned URLs for reference images
    for entry in entries:
        if entry.get("reference_image_key"):
            try:
                entry["reference_image_url"] = await minio.get_presigned_url(
                    cfg.BUCKET_WATCHLIST,
                    entry["reference_image_key"],
                    cfg.MINIO_URL_EXPIRY,
                )
            except Exception:
                entry["reference_image_url"] = None

        # Serialise datetimes
        for dt_field in ("added_at", "updated_at", "expires_at"):
            if entry.get(dt_field) and hasattr(entry[dt_field], "isoformat"):
                entry[dt_field] = entry[dt_field].isoformat()

    return {"count": len(entries), "watchlist": entries}


# ── Watchlist Alerts API (must be before /{watchlist_id} parametric routes) ────

@app.get("/watchlist/alerts")
async def watchlist_alerts_list(
    limit: int = 50,
    offset: int = 0,
    mission_id: str = "",
    drone_id: str = "",
    category: str = "",
):
    """Get recent watchlist match alerts with presigned image URLs."""
    alerts = await get_person_watchlist_alerts(
        limit=min(limit, 200),
        offset=offset,
        mission_id=mission_id or None,
        drone_id=drone_id or None,
        category=category.strip().lower() if category else None,
    )

    for alert in alerts:
        # Add presigned URLs for crops
        if alert.get("face_crop_key"):
            try:
                alert["face_crop_url"] = await minio.get_presigned_url(
                    cfg.BUCKET_FACES, alert["face_crop_key"], cfg.MINIO_URL_EXPIRY
                )
            except Exception:
                alert["face_crop_url"] = None
        if alert.get("frame_crop_key"):
            try:
                alert["frame_crop_url"] = await minio.get_presigned_url(
                    cfg.BUCKET_EVIDENCE, alert["frame_crop_key"], cfg.MINIO_URL_EXPIRY
                )
            except Exception:
                alert["frame_crop_url"] = None
        if alert.get("reference_image_key"):
            try:
                alert["reference_image_url"] = await minio.get_presigned_url(
                    cfg.BUCKET_WATCHLIST, alert["reference_image_key"], cfg.MINIO_URL_EXPIRY
                )
            except Exception:
                alert["reference_image_url"] = None

        # Serialise datetimes
        for dt_field in ("detected_at", "frame_ts", "confirmed_at"):
            if alert.get(dt_field) and hasattr(alert[dt_field], "isoformat"):
                alert[dt_field] = alert[dt_field].isoformat()

    return {"count": len(alerts), "alerts": alerts}


@app.post("/watchlist/alerts/{alert_id}/confirm")
async def watchlist_alert_confirm(alert_id: str, notes: str = ""):
    """Operator confirms a watchlist alert as a true match."""
    await confirm_watchlist_alert(alert_id, notes=notes or None)
    return {"status": "confirmed", "alert_id": alert_id}


@app.post("/watchlist/alerts/{alert_id}/dismiss")
async def watchlist_alert_dismiss(alert_id: str, notes: str = ""):
    """Operator dismisses a watchlist alert as false positive."""
    await dismiss_watchlist_alert(alert_id, notes=notes or None)
    return {"status": "dismissed", "alert_id": alert_id}


@app.post("/watchlist/gallery/reload")
async def watchlist_gallery_reload():
    """Force reload the in-memory watchlist gallery matrix."""
    if not watchlist_matcher:
        raise HTTPException(503, "Watchlist matcher not initialised")
    await watchlist_matcher.reload_gallery()
    return {
        "status": "reloaded",
        "gallery_size": watchlist_matcher.gallery_size,
    }


# ── Watchlist Detail / Update / Delete (parametric routes last) ───────────────

@app.get("/watchlist/{watchlist_id}")
async def watchlist_detail(watchlist_id: str):
    """Get a single watchlist entry with embedding metadata."""
    try:
        entry = await get_person_watchlist_entry(watchlist_id)
    except Exception:
        raise HTTPException(400, "Invalid watchlist ID format")
    if not entry:
        raise HTTPException(404, "Watchlist entry not found")

    if entry.get("reference_image_key"):
        try:
            entry["reference_image_url"] = await minio.get_presigned_url(
                cfg.BUCKET_WATCHLIST,
                entry["reference_image_key"],
                cfg.MINIO_URL_EXPIRY,
            )
        except Exception:
            entry["reference_image_url"] = None

    for dt_field in ("added_at", "updated_at", "expires_at"):
        if entry.get(dt_field) and hasattr(entry[dt_field], "isoformat"):
            entry[dt_field] = entry[dt_field].isoformat()

    return entry


@app.put("/watchlist/{watchlist_id}")
async def watchlist_update(watchlist_id: str, body: dict):
    """Update mutable fields of a watchlist entry."""
    try:
        entry = await get_person_watchlist_entry(watchlist_id)
    except Exception:
        raise HTTPException(400, "Invalid watchlist ID format")
    if not entry:
        raise HTTPException(404, "Watchlist entry not found")

    await update_person_watchlist(watchlist_id, **body)

    # Reload gallery if name/category changed (affects match labels)
    if watchlist_matcher and any(k in body for k in ("name", "category", "is_active")):
        await watchlist_matcher.reload_gallery()

    return {"status": "updated", "watchlist_id": watchlist_id}


@app.delete("/watchlist/{watchlist_id}")
async def watchlist_remove(watchlist_id: str):
    """Soft-delete a watchlist entry (deactivate) and reload gallery."""
    try:
        entry = await get_person_watchlist_entry(watchlist_id)
    except Exception:
        raise HTTPException(400, "Invalid watchlist ID format")
    if not entry:
        raise HTTPException(404, "Watchlist entry not found")

    await remove_from_person_watchlist(watchlist_id)

    if watchlist_matcher:
        await watchlist_matcher.reload_gallery()

    log.info("Watchlist entry deactivated: %s ('%s')", watchlist_id, entry.get("name"))
    return {"status": "removed", "watchlist_id": watchlist_id}


# ══════════════════════════════════════════════════════════════════════════════
#  Reverse Face Search API (Phase 5)
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/faces/search")
async def face_search(
    image: UploadFile = File(...),
    mission_id: str = Form(""),
    limit: int = Form(20),
    threshold: float = Form(0.40),
):
    """
    Reverse face search: upload a suspect photo, find matching faces across drone footage.
    Supports:
      1. Mission-scoped search: search only within a specific drone mission.
      2. Universal search: search across all historical drone recordings.
    Dual matching:
      - Evaluates ArcFace 512-dim cosine similarity on ArcFace-enabled records.
      - Falls back to ResNet50 2048-dim cosine similarity on historical records.
    """
    if not arcface_recognizer or not arcface_recognizer.ready:
        raise HTTPException(503, "Face recognition model not loaded")
    if not analyzer or not hasattr(analyzer, 'face_model') or analyzer.face_model is None:
        raise HTTPException(503, "Face detection model not loaded")

    img_bytes = await image.read()
    if len(img_bytes) > 10 * 1024 * 1024:
        raise HTTPException(413, "Image too large (max 10 MB)")

    nparr = np.frombuffer(img_bytes, np.uint8)
    img_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img_bgr is None:
        raise HTTPException(400, "Cannot decode image — use JPEG or PNG")

    # 1. Detect face (tiled -> direct -> pre-cropped mugshot fallback)
    from person.analyzer import detect_faces_on_tiles
    face_boxes = detect_faces_on_tiles(analyzer.face_model, img_bgr, device=analyzer.device)

    if face_boxes:
        best_face = max(face_boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
        x1, y1, x2, y2, face_conf = best_face
        face_crop = img_bgr[max(0, int(y1)):int(y2), max(0, int(x1)):int(x2)]
    else:
        results = analyzer.face_model(img_bgr, conf=0.15, device=analyzer.device, verbose=False)
        direct_boxes = []
        for res in results:
            for box in res.boxes:
                bx1, by1, bx2, by2 = box.xyxy[0].cpu().numpy()
                conf = float(box.conf[0].cpu().numpy())
                direct_boxes.append((int(bx1), int(by1), int(bx2), int(by2), conf))
        if direct_boxes:
            best_face = max(direct_boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
            x1, y1, x2, y2, face_conf = best_face
            face_crop = img_bgr[max(0, int(y1)):int(y2), max(0, int(x1)):int(x2)]
        else:
            raise HTTPException(
                422,
                "No face detected in the uploaded image. "
                "Please upload a clear photo with a visible face."
            )

    if face_crop.size == 0:
        raise HTTPException(422, "Extracted face crop is empty")

    # 2. Enhance and extract query embeddings
    if cfg.FACE_ENHANCE_ENABLED:
        face_crop = enhance_face_crop(face_crop)

    query_arcface = arcface_recognizer.extract_embedding(face_crop, quality_check=False)
    if query_arcface is None:
        raise HTTPException(422, "Could not extract face features — face is too small or blurry")

    # Also extract ResNet embedding if model available (for historical records)
    query_resnet = None
    if resnet_model:
        try:
            _, q_buf = cv2.imencode(".jpg", face_crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
            query_resnet = extract_embedding_from_bytes(resnet_model, bytes(q_buf), resnet_device)
        except Exception as q_exc:
            log.warning("ResNet query extraction warning: %s", q_exc)

    # 3. Query DB
    pool = await get_pool()
    query_mission = mission_id.strip() if mission_id else None

    if query_mission:
        db_rows = await pool.fetch(
            """
            SELECT id::text, track_id::text, image_path, encrypted_embedding, arcface_embedding,
                   drone_id, cluster_id, is_duplicate, object_key, person_object_key,
                   mission_id, created_at
            FROM face_embeddings
            WHERE mission_id = $1
            ORDER BY created_at DESC
            LIMIT 5000
            """,
            query_mission,
        )
    else:
        db_rows = await pool.fetch(
            """
            SELECT id::text, track_id::text, image_path, encrypted_embedding, arcface_embedding,
                   drone_id, cluster_id, is_duplicate, object_key, person_object_key,
                   mission_id, created_at
            FROM face_embeddings
            ORDER BY created_at DESC
            LIMIT 10000
            """
        )

    # 4. Vectorized cosine search
    from shared.crypto import decrypt_vector

    query_af_vec = np.array(query_arcface, dtype=np.float32)
    query_af_norm = np.linalg.norm(query_af_vec)
    if query_af_norm > 0:
        query_af_vec /= query_af_norm

    query_res_vec = None
    if query_resnet:
        query_res_vec = np.array(query_resnet, dtype=np.float32)
        q_norm = np.linalg.norm(query_res_vec)
        if q_norm > 0:
            query_res_vec /= q_norm

    candidates = []
    for r in db_rows:
        row_dict = dict(r)
        sim = 0.0
        matched_on = "none"

        # Check arcface embedding first
        raw_af = row_dict.pop("arcface_embedding", None)
        raw_res = row_dict.pop("encrypted_embedding", None)

        if raw_af:
            try:
                db_af = np.array(decrypt_vector(bytes(raw_af), expected_dim=512), dtype=np.float32)
                db_af_norm = np.linalg.norm(db_af)
                if db_af_norm > 0:
                    db_af /= db_af_norm
                sim = float(np.dot(query_af_vec, db_af))
                matched_on = "arcface"
            except Exception:
                pass

        # Fallback to ResNet embedding if arcface not stored or below threshold
        if sim < threshold and raw_res and query_res_vec is not None:
            try:
                db_res = np.array(decrypt_vector(bytes(raw_res), expected_dim=2048), dtype=np.float32)
                db_res_norm = np.linalg.norm(db_res)
                if db_res_norm > 0:
                    db_res /= db_res_norm
                res_sim = float(np.dot(query_res_vec, db_res))
                if res_sim > sim:
                    sim = res_sim
                    matched_on = "resnet"
            except Exception:
                pass

        if sim >= threshold:
            row_dict["similarity"] = round(sim, 4)
            row_dict["matched_on"] = matched_on
            candidates.append(row_dict)

    # Sort descending by similarity
    candidates.sort(key=lambda x: x["similarity"], reverse=True)
    top_matches = candidates[:min(limit, 100)]

    # 5. Attach presigned URLs for thumbnails/crops
    for m in top_matches:
        if m.get("object_key"):
            try:
                m["image_url"] = await minio.get_presigned_url(
                    cfg.BUCKET_FACES, m["object_key"], cfg.MINIO_URL_EXPIRY
                )
            except Exception:
                m["image_url"] = None
        if m.get("person_object_key"):
            try:
                m["person_image_url"] = await minio.get_presigned_url(
                    cfg.BUCKET_FACES, m["person_object_key"], cfg.MINIO_URL_EXPIRY
                )
            except Exception:
                m["person_image_url"] = None

        if m.get("created_at") and hasattr(m["created_at"], "isoformat"):
            m["created_at"] = m["created_at"].isoformat()

    return {
        "query_face_detected": True,
        "face_confidence": face_conf,
        "search_scope": f"mission:{query_mission}" if query_mission else "universal",
        "total_scanned": len(db_rows),
        "count": len(top_matches),
        "results": top_matches,
    }


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
