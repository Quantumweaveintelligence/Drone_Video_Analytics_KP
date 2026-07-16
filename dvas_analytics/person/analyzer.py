# -*- coding: utf-8 -*-
"""
Person & Face Analyzer — headless adaptation of person_face_crowd.py.
Uses tile-based detection for accuracy on aerial/drone footage.
"""
import base64
import logging
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import cv2
import numpy as np
import torch
from ultralytics import YOLO

log = logging.getLogger(__name__)

TILE_ROWS        = 2
TILE_COLS        = 2
TILE_IMG_SIZE    = 1280
CONF_THRESHOLD   = 0.20
NMS_IOU          = 0.40
SAVE_EVERY_N     = 5     # Save face crops every N frames


def _bbox_dict(x1, y1, x2, y2):
    return {"x1": int(x1), "y1": int(y1), "x2": int(x2), "y2": int(y2)}


def _crop_to_b64(frame: np.ndarray, x1, y1, x2, y2) -> Optional[str]:
    """Crop a region from frame and return base64 JPEG."""
    h, w = frame.shape[:2]
    x1, y1 = max(0, int(x1)), max(0, int(y1))
    x2, y2 = min(w, int(x2)), min(h, int(y2))
    if x2 <= x1 or y2 <= y1:
        return None
    crop = frame[y1:y2, x1:x2]
    _, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(buf).decode()


def detect_on_tiles(
    model: YOLO,
    frame: np.ndarray,
    rows: int = TILE_ROWS,
    cols: int = TILE_COLS,
    classes: Optional[List[int]] = None,
    device=0,
) -> List[Tuple[int, int, int, int, float]]:
    """
    Tile-based detection — identical logic to person_face_crowd.py.
    Returns list of (x1, y1, x2, y2, confidence).
    """
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
                conf=CONF_THRESHOLD, classes=classes,
                verbose=False, agnostic_nms=True,
            )
            for res in results:
                for box in res.boxes:
                    bx1, by1, bx2, by2 = box.xyxy[0].cpu().numpy()
                    conf = float(box.conf[0].cpu().numpy())
                    gx1, gy1 = int(bx1 + x1), int(by1 + y1)
                    gx2, gy2 = int(bx2 + x1), int(by2 + y1)
                    all_boxes.append([gx1, gy1, gx2 - gx1, gy2 - gy1])
                    all_confidences.append(conf)

    if not all_boxes:
        return []

    indices = cv2.dnn.NMSBoxes(all_boxes, all_confidences, CONF_THRESHOLD, NMS_IOU)
    final = []
    if len(indices) > 0:
        for i in indices.flatten():
            x, y, w, h = all_boxes[i]
            conf = all_confidences[i]
            final.append((x, y, x + w, y + h, conf))
    return final


class PersonFaceAnalyzer:
    """
    Headless person + face detector.
    Wraps YOLOv8m (person) and YOLOv8n-face (face) with tile-based inference.
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
        Detect persons and faces in a frame.
        Returns a dict matching FrameAnalysisResult.
        """
        t0 = time.perf_counter()
        self._frame_count += 1

        persons, faces = [], []

        # ── Person detection ─────────────────────────────────────────────
        if self.person_model:
            person_boxes = detect_on_tiles(
                self.person_model, frame_bgr,
                classes=[0], device=self.device,
            )
            for (x1, y1, x2, y2, conf) in person_boxes:
                crop_b64 = None
                if save_crops and self._frame_count % 10 == 0:
                    crop_b64 = _crop_to_b64(frame_bgr, x1, y1, x2, y2)
                persons.append({
                    "bbox": _bbox_dict(x1, y1, x2, y2),
                    "confidence": round(conf, 4),
                    "crop_b64": crop_b64,
                })

        # ── Face detection ────────────────────────────────────────────────
        if self.face_model:
            face_boxes = detect_on_tiles(
                self.face_model, frame_bgr,
                classes=[0], device=self.device,
            )
            for (x1, y1, x2, y2, conf) in face_boxes:
                crop_b64 = None
                if save_crops and self._frame_count % SAVE_EVERY_N == 0:
                    crop_b64 = _crop_to_b64(frame_bgr, x1, y1, x2, y2)
                faces.append({
                    "bbox": _bbox_dict(x1, y1, x2, y2),
                    "confidence": round(conf, 4),
                    "crop_b64": crop_b64,
                })

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
