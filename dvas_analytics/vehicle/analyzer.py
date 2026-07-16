# -*- coding: utf-8 -*-
"""
Vehicle Analyzer — wraps VehicleDetectionTracker for headless/API use.
Adapted from detection.py.
"""
import base64
import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
import torch

log = logging.getLogger(__name__)

VEHICLE_CLASSES = {"car", "truck", "bus", "motorcycle", "auto-rickshaw"}


def _crop_to_b64(frame: np.ndarray, x, y, w, h) -> Optional[str]:
    """Crop vehicle from frame and return base64 JPEG."""
    fh, fw = frame.shape[:2]
    x1 = max(0, int(x))
    y1 = max(0, int(y))
    x2 = min(fw, int(x + w))
    y2 = min(fh, int(y + h))
    if x2 <= x1 or y2 <= y1:
        return None
    crop = frame[y1:y2, x1:x2]
    _, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(buf).decode()


class VehicleAnalyzer:
    """
    Headless wrapper around VehicleDetectionTracker.
    Processes individual frames and returns structured detection results.
    """

    def __init__(self, model_path: str):
        self.model_path = model_path
        self.device     = "cuda" if torch.cuda.is_available() else "cpu"
        log.info("VehicleAnalyzer device: %s", self.device)

        self.tracker = None
        self._frame_detections: List[Dict] = []   # staging for callback
        self._load_tracker()
        self._vehicle_counter = 0

    def _load_tracker(self):
        try:
            sys.path.insert(0, "/analytics/vehicle")
            from VehicleDetectionTracker.VehicleDetectionTracker import VehicleDetectionTracker
            self.tracker = VehicleDetectionTracker(model_path=self.model_path)
            log.info("VehicleDetectionTracker loaded: %s", self.model_path)
        except Exception as exc:
            log.error("Failed to load VehicleDetectionTracker: %s", exc)
            self.tracker = None

    @property
    def ready(self) -> bool:
        return self.tracker is not None

    # ── Frame-level callback used by VehicleDetectionTracker.process_video ────
    def _frame_callback(self, result: dict):
        """Capture detections emitted by the tracker callback."""
        frame_ts = datetime.now(timezone.utc).isoformat()
        for veh in result.get("detected_vehicles", []):
            vtype = veh.get("vehicle_type", "")
            if vtype not in VEHICLE_CLASSES:
                continue
            self._frame_detections.append({
                "frame_timestamp":  frame_ts,
                "vehicle_id":       veh.get("vehicle_id"),
                "tracker_id":       veh.get("tracker_id"),
                "vehicle_type":     vtype,
                "confidence":       round(veh.get("detection_confidence", 0.0), 4),
                "bbox_x":           round(veh["vehicle_coordinates"]["x"], 2),
                "bbox_y":           round(veh["vehicle_coordinates"]["y"], 2),
                "bbox_w":           round(veh["vehicle_coordinates"]["width"], 2),
                "bbox_h":           round(veh["vehicle_coordinates"]["height"], 2),
                "speed_info":       veh.get("speed_info", {}),
                "colour_top3":      self._parse_json_field(veh.get("color_info")),
                "make_model_top3":  self._parse_json_field(veh.get("model_info")),
            })

    @staticmethod
    def _parse_json_field(value) -> list:
        if not value:
            return []
        if isinstance(value, list):
            return value
        try:
            return json.loads(value)
        except Exception:
            return []

    def analyze_frame(
        self,
        frame_bgr: np.ndarray,
        drone_id: str = "DRONE_KL_001",
        save_crops: bool = True,
    ) -> dict:
        """
        Process a single BGR frame through VehicleDetectionTracker.
        Returns a dict matching VehicleFrameResult.
        """
        if not self.ready:
            return {
                "drone_id": drone_id, "ts": datetime.now(timezone.utc).isoformat(),
                "frame_timestamp": datetime.now(timezone.utc).isoformat(),
                "vehicles": [], "vehicle_count": 0, "inference_ms": 0.0,
            }

        t0 = time.perf_counter()
        self._frame_detections.clear()

        # VehicleDetectionTracker expects a callback for each processed result.
        # We call process_frame() if it exists, otherwise fall back to process_video
        # with an in-memory capture. Use direct YOLO tracking for single-frame mode.
        try:
            # Direct inference — use tracker's underlying model
            import torch
            model = self.tracker.model
            results = model.track(
                frame_bgr,
                persist=True,
                imgsz=640,
                conf=0.25,
                device=self.device,
                verbose=False,
            )

            vehicles = []
            if results and results[0].boxes:
                self.tracker._initialize_classifiers()
                for box in results[0].boxes:
                    if box.id is None:
                        continue
                    tracker_id = int(box.id[0])
                    seq_id     = self.tracker._get_sequential_id(tracker_id)
                    cls_id     = int(box.cls[0])
                    conf       = float(box.conf[0])

                    # Map YOLO COCO class ID to vehicle type
                    class_names = model.names
                    vtype = class_names.get(cls_id, "vehicle")
                    if vtype not in VEHICLE_CLASSES:
                        continue

                    x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                    bx, by, bw, bh  = float(x1), float(y1), float(x2 - x1), float(y2 - y1)

                    crop_b64 = None
                    if save_crops:
                        crop_b64 = _crop_to_b64(frame_bgr, bx, by, bw, bh)

                    vehicles.append({
                        "vehicle_id":      seq_id,
                        "tracker_id":      tracker_id,
                        "vehicle_type":    vtype,
                        "confidence":      round(conf, 4),
                        "bbox_x":          round(bx, 2),
                        "bbox_y":          round(by, 2),
                        "bbox_w":          round(bw, 2),
                        "bbox_h":          round(bh, 2),
                        "speed_info":      {},
                        "colour_top3":     [],
                        "make_model_top3": [],
                        "crop_b64":        crop_b64,
                    })

        except Exception as exc:
            log.error("Frame inference error: %s", exc)
            vehicles = []

        elapsed_ms = (time.perf_counter() - t0) * 1000
        ts = datetime.now(timezone.utc)
        return {
            "drone_id":        drone_id,
            "ts":              ts.isoformat(),
            "frame_timestamp": ts.isoformat(),
            "vehicles":        vehicles,
            "vehicle_count":   len(vehicles),
            "inference_ms":    round(elapsed_ms, 1),
        }
