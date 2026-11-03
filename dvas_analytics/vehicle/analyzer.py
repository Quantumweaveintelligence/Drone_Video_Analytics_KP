# -*- coding: utf-8 -*-
"""
Vehicle Analyzer — wraps YOLO for vehicle detection/tracking.
Uses HSV colour classifier (shared.colour_classifier) instead of TF1 model.
Publishes vehicle track info to Redis for ANPR linkage.
"""
import base64
import logging
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
import torch
from ultralytics import YOLO

from shared.batch_tracker import BatchTracker

log = logging.getLogger(__name__)

# Vehicle classes present in VisDrone model
VEHICLE_CLASSES = {"car", "truck", "bus", "motorcycle", "auto-rickshaw",
                   "van", "motor", "bicycle"}
VEHICLE_COCO_IDS = {2, 3, 5, 6, 7}  # car, motorcycle, bus, train, truck


def _crop_to_bytes(frame: np.ndarray, x1, y1, x2, y2) -> Optional[bytes]:
    fh, fw = frame.shape[:2]
    x1 = max(0, int(x1)); y1 = max(0, int(y1))
    x2 = min(fw, int(x2)); y2 = min(fh, int(y2))
    if x2 <= x1 or y2 <= y1:
        return None
    crop = frame[y1:y2, x1:x2]
    _, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return bytes(buf)


def _crop_to_b64(frame: np.ndarray, x1, y1, x2, y2) -> Optional[str]:
    b = _crop_to_bytes(frame, x1, y1, x2, y2)
    return base64.b64encode(b).decode() if b else None


class _TrackState:
    """Per-vehicle track state for speed estimation."""
    def __init__(self):
        self.positions: List[Tuple[float, float]] = []  # (cx, cy) history
        self.timestamps: List[float] = []
        self.max_history = 10

    def update(self, cx: float, cy: float, ts: float):
        self.positions.append((cx, cy))
        self.timestamps.append(ts)
        if len(self.positions) > self.max_history:
            self.positions.pop(0)
            self.timestamps.pop(0)

    def estimate_speed_px_per_s(self) -> Optional[float]:
        """Pixel displacement speed — caller converts to km/h with calibration."""
        if len(self.positions) < 2:
            return None
        dx = self.positions[-1][0] - self.positions[0][0]
        dy = self.positions[-1][1] - self.positions[0][1]
        dt = self.timestamps[-1] - self.timestamps[0]
        if dt < 0.001:
            return None
        return (dx**2 + dy**2) ** 0.5 / dt


class VehicleAnalyzer:
    """
    Headless vehicle detection + tracking with:
    - ByteTrack cross-frame tracker
    - HSV colour classification (no TF dependency)
    - Speed estimation via centroid displacement
    - Redis track info publishing for ANPR linkage
    """

    def __init__(self, model_path: str):
        self.model_path = model_path
        self.device     = "cuda" if torch.cuda.is_available() else "cpu"
        log.info("VehicleAnalyzer device: %s", self.device)

        self.model: Optional[YOLO] = None
        self.tracker: Optional[BatchTracker] = None
        self._load_model()

        # Per-tracker state for speed estimation (tracker ids are unique across drones)
        self._track_states: Dict[int, _TrackState] = defaultdict(_TrackState)

        # HSV colour classifier (lazy import to avoid issues if shared/ not on path yet)
        self._colour_classifier = None

    def _load_model(self):
        try:
            self.model = YOLO(self.model_path)
            # One tracker per drone; frames of all drones go through the model as one batch
            self.tracker = BatchTracker(self.model)
            log.info("Vehicle YOLO model loaded: %s", self.model_path)
        except Exception as exc:
            log.error("Failed to load vehicle model: %s", exc)
            self.model = None

    def to_device(self, target_device: str):
        self.device = target_device
        if self.model is not None:
            try:
                self.model.to(target_device)
                log.info("Vehicle YOLO model moved to %s", target_device)
            except Exception as e:
                log.warning("Failed to move vehicle model to %s: %s", target_device, e)

    def _get_colour_classifier(self):
        if self._colour_classifier is None:
            try:
                from shared.colour_classifier import classify_colour
                self._colour_classifier = classify_colour
            except ImportError:
                log.warning("Colour classifier not available")
                self._colour_classifier = lambda x, **kwargs: []
        return self._colour_classifier

    @property
    def ready(self) -> bool:
        return self.model is not None

    def _empty(self, drone_id: str) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        return {"drone_id": drone_id, "ts": now, "frame_timestamp": now,
                "vehicles": [], "vehicle_count": 0, "inference_ms": 0.0}

    def _track_kwargs(self) -> dict:
        return dict(imgsz=640, conf=0.25, device=self.device)

    def analyze_frame(
        self,
        frame_bgr: np.ndarray,
        drone_id: str = "DRONE_KL_001",
        save_crops: bool = True,
        px_per_meter: float = 0.1,   # calibration: pixels per meter (drone altitude dependent)
    ) -> dict:
        """
        Process a single BGR frame.
        Returns structured detection dict with colour, speed, and crop data.
        """
        try:
            return self.analyze_batch({drone_id: frame_bgr}, save_crops, px_per_meter)[drone_id]
        except RuntimeError as exc:     # CUDA failure on the upload / offline paths
            log.error("Vehicle frame inference error: %s", exc)
            return self._empty(drone_id)

    def analyze_batch(
        self,
        frames: Dict[str, np.ndarray],
        save_crops: bool = True,
        px_per_meter: float = 0.1,
    ) -> Dict[str, dict]:
        """Newest frame of several drones → {drone_id: result}. One GPU batch, per-drone tracks."""
        if not self.ready:
            return {d: self._empty(d) for d in frames}
        t0 = time.perf_counter()
        try:
            tracked = self.tracker.track_batch(frames, **self._track_kwargs())
        except Exception as exc:
            if "CUDA" in str(exc):
                raise           # main.py moves the model to CPU and retries
            log.error("Vehicle frame inference error: %s", exc)
            tracked = {}
        infer_ms = (time.perf_counter() - t0) * 1000 / max(1, len(frames))
        return {
            d: self._postprocess(d, frame, tracked.get(d), save_crops, px_per_meter, infer_ms)
            for d, frame in frames.items()
        }

    def _postprocess(self, drone_id, frame_bgr, result, save_crops, px_per_meter, infer_ms) -> dict:
        t0 = time.perf_counter()
        classify_colour = self._get_colour_classifier()
        vehicles = []
        now_ts = time.time()

        try:
            results = [result] if result is not None else []

            if results and results[0].boxes:
                class_names = self.model.names
                for box in results[0].boxes:
                    if box.id is None:
                        continue

                    tracker_id = int(box.id[0])
                    cls_id     = int(box.cls[0])
                    conf       = float(box.conf[0])
                    vtype      = class_names.get(cls_id, "vehicle")

                    # Accept any vehicle-ish class
                    if vtype.lower() not in VEHICLE_CLASSES and cls_id not in VEHICLE_COCO_IDS:
                        continue

                    x1, y1, x2, y2 = [float(v) for v in box.xyxy[0].cpu().numpy()]
                    bx, by = float(x1), float(y1)
                    bw, bh = float(x2 - x1), float(y2 - y1)
                    cx, cy = x1 + bw / 2, y1 + bh / 2

                    # Update track state for speed estimation
                    state = self._track_states[tracker_id]
                    state.update(cx, cy, now_ts)
                    px_speed = state.estimate_speed_px_per_s()
                    kph = None
                    if px_speed is not None and px_per_meter > 0:
                        # pixels/s -> m/s -> km/h
                        kph = round((px_speed / px_per_meter) * 3.6, 1)
                        kph = min(kph, 200.0)  # sanity cap

                    # Crop vehicle
                    crop_bytes = None
                    crop_b64   = None
                    colour_top3 = []
                    if save_crops:
                        crop_bytes = _crop_to_bytes(frame_bgr, x1, y1, x2, y2)
                        if crop_bytes:
                            crop_b64 = base64.b64encode(crop_bytes).decode()
                            # HSV colour classification on crop
                            nparr = np.frombuffer(crop_bytes, np.uint8)
                            crop_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                            if crop_bgr is not None:
                                colour_top3 = classify_colour(crop_bgr, top_k=3)

                    vehicles.append({
                        "tracker_id":  tracker_id,
                        "vehicle_type": vtype,
                        "confidence":  round(conf, 4),
                        "bbox":        [round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)],
                        "bbox_x":      round(bx, 2),
                        "bbox_y":      round(by, 2),
                        "bbox_w":      round(bw, 2),
                        "bbox_h":      round(bh, 2),
                        "speed_info": {
                            "kph":         kph,
                            "reliability": "estimated" if kph is not None else "unavailable",
                        },
                        "colour_top3":     colour_top3,
                        "make_model_top3": [],
                        "crop_b64":        crop_b64,
                        "crop_bytes":      crop_bytes,  # kept for MinIO upload by main.py
                    })

        except Exception as exc:
            log.error("Vehicle frame inference error: %s", exc)

        elapsed_ms = infer_ms + (time.perf_counter() - t0) * 1000
        ts = datetime.now(timezone.utc)
        return {
            "drone_id":        drone_id,
            "ts":              ts.isoformat(),
            "frame_timestamp": ts.isoformat(),
            "vehicles":        vehicles,
            "vehicle_count":   len(vehicles),
            "inference_ms":    round(elapsed_ms, 1),
            "model_ms":        round(infer_ms, 1),
        }
