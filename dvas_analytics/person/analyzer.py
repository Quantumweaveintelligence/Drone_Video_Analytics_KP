# -*- coding: utf-8 -*-
"""
Person & Face Analyzer — production-ready with ByteTrack cross-frame tracking.
Uses YOLOv8m for person tracking (.track persist=True) and YOLOv8n-face
for tile-based face detection, linking faces to person tracks by IoU.
"""
import base64
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import torch
from ultralytics import YOLO

log = logging.getLogger(__name__)

FACE_TILE_ROWS   = 1
FACE_TILE_COLS   = 2
TILE_IMG_SIZE    = 640
CONF_PERSON      = 0.15
CONF_FACE        = 0.25
NMS_IOU          = 0.40
SAVE_EVERY_N     = 5


def _bbox_dict(x1, y1, x2, y2):
    return {"x1": int(x1), "y1": int(y1), "x2": int(x2), "y2": int(y2)}


def _crop_to_b64(frame: np.ndarray, x1, y1, x2, y2) -> Optional[str]:
    h, w = frame.shape[:2]
    x1, y1 = max(0, int(x1)), max(0, int(y1))
    x2, y2 = min(w, int(x2)), min(h, int(y2))
    if x2 <= x1 or y2 <= y1:
        return None
    crop = frame[y1:y2, x1:x2]
    _, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(buf).decode()


def _crop_to_bytes(frame: np.ndarray, x1, y1, x2, y2) -> Optional[bytes]:
    h, w = frame.shape[:2]
    x1, y1 = max(0, int(x1)), max(0, int(y1))
    x2, y2 = min(w, int(x2)), min(h, int(y2))
    if x2 <= x1 or y2 <= y1:
        return None
    crop = frame[y1:y2, x1:x2]
    _, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return bytes(buf)


def _iou(b1, b2) -> float:
    """IoU between two (x1,y1,x2,y2) boxes."""
    ix1 = max(b1[0], b2[0]); iy1 = max(b1[1], b2[1])
    ix2 = min(b1[2], b2[2]); iy2 = min(b1[3], b2[3])
    iw = max(0, ix2 - ix1); ih = max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    a1 = (b1[2]-b1[0]) * (b1[3]-b1[1])
    a2 = (b2[2]-b2[0]) * (b2[3]-b2[1])
    return inter / (a1 + a2 - inter + 1e-6)


def _face_in_person(face_bbox, person_bbox, threshold=0.1) -> bool:
    """True if face centre falls inside person bbox (with some tolerance)."""
    fx = (face_bbox["x1"] + face_bbox["x2"]) / 2
    fy = (face_bbox["y1"] + face_bbox["y2"]) / 2
    px1, py1 = person_bbox["x1"], person_bbox["y1"]
    px2, py2 = person_bbox["x2"], person_bbox["y2"]
    pw = px2 - px1; ph = py2 - py1
    return (
        px1 - pw * threshold <= fx <= px2 + pw * threshold and
        py1 - ph * threshold <= fy <= py2 + ph * threshold
    )


def detect_faces_on_tiles(
    model: YOLO,
    frame: np.ndarray,
    rows: int = FACE_TILE_ROWS,
    cols: int = FACE_TILE_COLS,
    device=0,
) -> List[Tuple[int, int, int, int, float]]:
    """Tile-based face detection. Returns list of (x1, y1, x2, y2, conf)."""
    h_img, w_img = frame.shape[:2]
    step_h, step_w = h_img // rows, w_img // cols
    overlap = 150
    all_boxes, all_confidences = [], []

    for r in range(rows):
        for c in range(cols):
            y1 = max(0, r * step_h - overlap)
            x1 = max(0, c * step_w - overlap)
            y2 = min(h_img, (r + 1) * step_h + overlap)
            x2 = min(w_img, (c + 1) * step_w + overlap)
            tile = frame[y1:y2, x1:x2]
            results = model(
                tile, device=device, imgsz=TILE_IMG_SIZE,
                conf=CONF_FACE, verbose=False, agnostic_nms=True,
            )
            for res in results:
                for box in res.boxes:
                    bx1, by1, bx2, by2 = box.xyxy[0].cpu().numpy()
                    conf = float(box.conf[0].cpu().numpy())
                    gx1 = int(bx1 + x1); gy1 = int(by1 + y1)
                    gx2 = int(bx2 + x1); gy2 = int(by2 + y1)
                    all_boxes.append([gx1, gy1, gx2 - gx1, gy2 - gy1])
                    all_confidences.append(conf)

    if not all_boxes:
        return []
    indices = cv2.dnn.NMSBoxes(all_boxes, all_confidences, CONF_FACE, NMS_IOU)
    final = []
    if len(indices) > 0:
        for i in indices.flatten():
            x, y, w, h = all_boxes[i]
            conf = all_confidences[i]
            final.append((x, y, x + w, y + h, conf))
    return final


class PersonFaceAnalyzer:
    """
    Production person + face analyzer with ByteTrack cross-frame person tracking.
    """

    def __init__(self, person_model_path: str, face_model_path: str):
        self.device = 0 if torch.cuda.is_available() else "cpu"
        log.info("PersonFaceAnalyzer device: %s", self.device)

        try:
            self.person_model = YOLO(person_model_path)
            log.info("Person model loaded: %s", person_model_path)
        except Exception as exc:
            log.error("Failed to load person model: %s", exc)
            self.person_model = None

        try:
            self.face_model = YOLO(face_model_path)
            log.info("Face model loaded: %s", face_model_path)
        except Exception as exc:
            log.error("Failed to load face model: %s", exc)
            self.face_model = None

        self._frame_count = 0

    @property
    def ready(self) -> bool:
        return self.person_model is not None and self.face_model is not None

    def analyze_frame(
        self,
        frame_bgr: np.ndarray,
        drone_id: str = "DRONE_KL_001",
        save_crops: bool = True,
    ) -> dict:
        """
        Detect and track persons (ByteTrack) + detect faces (tile-based).
        Persons get stable tracker_id across frames.
        Each face is linked to the nearest person by centre-point containment.
        """
        t0 = time.perf_counter()
        self._frame_count += 1
        persons: List[dict] = []
        faces:   List[dict] = []
        _untracked_counter = 0

        # ── Person detection + ByteTrack ─────────────────────────────────────
        if self.person_model:
            try:
                results = self.person_model.track(
                    frame_bgr,
                    persist=True,
                    imgsz=640,
                    conf=CONF_PERSON,
                    classes=[0],      # person class
                    device=self.device,
                    verbose=False,
                )
                if results and results[0].boxes:
                    for box in results[0].boxes:
                        if box.id is not None:
                            tracker_id = int(box.id[0])
                        else:
                            _untracked_counter += 1
                            tracker_id = -_untracked_counter
                        conf = float(box.conf[0])
                        x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]

                        crop_b64  = None
                        crop_bytes = None
                        if save_crops and self._frame_count % SAVE_EVERY_N == 0:
                            crop_bytes = _crop_to_bytes(frame_bgr, x1, y1, x2, y2)
                            if crop_bytes:
                                crop_b64 = base64.b64encode(crop_bytes).decode()

                        persons.append({
                            "tracker_id": tracker_id,
                            "bbox":       _bbox_dict(x1, y1, x2, y2),
                            "confidence": round(conf, 4),
                            "crop_b64":   crop_b64,
                            "crop_bytes": crop_bytes,   # kept for MinIO upload
                        })
            except Exception as exc:
                if "CUDA" in str(exc) and self.device != "cpu":
                    log.warning("CUDA error in person tracking — falling back to CPU: %s", exc)
                    self.device = "cpu"
                    try:
                        self.person_model.to("cpu")
                    except Exception:
                        pass
                    # Retry on CPU
                    try:
                        results = self.person_model.track(
                            frame_bgr, persist=True, imgsz=640, conf=CONF_PERSON,
                            classes=[0], device=self.device, verbose=False,
                        )
                        if results and results[0].boxes:
                            for box in results[0].boxes:
                                if box.id is not None:
                                    tracker_id = int(box.id[0])
                                else:
                                    _untracked_counter += 1
                                    tracker_id = -_untracked_counter
                                conf = float(box.conf[0])
                                x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
                                crop_b64 = None
                                crop_bytes = None
                                if save_crops and self._frame_count % SAVE_EVERY_N == 0:
                                    crop_bytes = _crop_to_bytes(frame_bgr, x1, y1, x2, y2)
                                    if crop_bytes:
                                        crop_b64 = base64.b64encode(crop_bytes).decode()
                                persons.append({
                                    "tracker_id": tracker_id,
                                    "bbox": _bbox_dict(x1, y1, x2, y2),
                                    "confidence": round(conf, 4),
                                    "crop_b64": crop_b64,
                                    "crop_bytes": crop_bytes,
                                })
                    except Exception as retry_exc:
                        log.warning("Person tracking CPU retry also failed: %s", retry_exc)
                else:
                    log.warning("Person tracking error: %s", exc)

        # ── Face detection (tile-based) ───────────────────────────────────────
        if self.face_model:
            try:
                face_boxes = detect_faces_on_tiles(
                    self.face_model, frame_bgr, device=self.device
                )
                for (x1, y1, x2, y2, conf) in face_boxes:
                    face_bbox = _bbox_dict(x1, y1, x2, y2)

                    # Link face to person by containment
                    linked_tracker_id = None
                    for p in persons:
                        if _face_in_person(face_bbox, p["bbox"]):
                            linked_tracker_id = p["tracker_id"]
                            break

                    crop_b64  = None
                    crop_bytes = None
                    if save_crops and self._frame_count % SAVE_EVERY_N == 0:
                        crop_bytes = _crop_to_bytes(frame_bgr, x1, y1, x2, y2)
                        if crop_bytes:
                            crop_b64 = base64.b64encode(crop_bytes).decode()

                    faces.append({
                        "tracker_id": linked_tracker_id,
                        "bbox":       face_bbox,
                        "confidence": round(conf, 4),
                        "crop_b64":   crop_b64,
                        "crop_bytes": crop_bytes,
                    })

                # ── Anchor person bounding boxes to unlinked faces ─────────────
                h_frame, w_frame = frame_bgr.shape[:2]
                for face in faces:
                    if face["tracker_id"] is None:
                        fb = face["bbox"]
                        fw = max(12, fb["x2"] - fb["x1"])
                        fh = max(12, fb["y2"] - fb["y1"])
                        fx_center = (fb["x1"] + fb["x2"]) // 2

                        py1 = max(0, fb["y1"] - int(fh * 0.15))
                        py2 = min(h_frame, fb["y1"] + int(fh * 5.8))
                        px1 = max(0, fx_center - int(fw * 1.6))
                        px2 = min(w_frame, fx_center + int(fw * 1.6))

                        _untracked_counter += 1
                        syn_tid = -_untracked_counter
                        face["tracker_id"] = syn_tid

                        crop_bytes = _crop_to_bytes(frame_bgr, px1, py1, px2, py2) if (save_crops and self._frame_count % SAVE_EVERY_N == 0) else None
                        crop_b64 = base64.b64encode(crop_bytes).decode() if crop_bytes else None

                        persons.append({
                            "tracker_id": syn_tid,
                            "bbox":       _bbox_dict(px1, py1, px2, py2),
                            "confidence": round(face["confidence"], 4),
                            "crop_b64":   crop_b64,
                            "crop_bytes": crop_bytes,
                        })

            except Exception as exc:
                if "CUDA" in str(exc) and self.device != "cpu":
                    log.warning("CUDA error in face detection — falling back to CPU: %s", exc)
                    self.device = "cpu"
                    try:
                        self.face_model.to("cpu")
                    except Exception:
                        pass
                else:
                    log.warning("Face detection error: %s", exc)

        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {
            "drone_id":     drone_id,
            "ts":           datetime.now(timezone.utc).isoformat(),
            "persons":      persons,
            "faces":        faces,
            "person_count": len(persons),
            "face_count":   len(faces),
            "inference_ms": round(elapsed_ms, 1),
        }
