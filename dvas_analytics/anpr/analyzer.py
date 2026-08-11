# -*- coding: utf-8 -*-
"""
ANPR Analyzer — adapted from number-plate-detection.py.
Two-resolution pipeline: track on 720p, OCR on full-res crop.
Uses PaddleOCR + CLAHE + unsharp masking for aerial footage.
"""
import base64
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

import cv2
import numpy as np
import torch
from ultralytics import YOLO

log = logging.getLogger(__name__)

# ── Config (mirrors number-plate-detection.py) ──────────────────────────────
TRACK_RESIZE_WIDTH  = 1280
TRACK_RESIZE_HEIGHT = 720
IMG_SIZE            = 640
CONF_VEHICLE        = 0.25
CONF_LP             = 0.20
VEHICLE_CROP_PAD    = 20
OCR_EVERY_N_FRAMES  = 8
MIN_PLATE_AREA_PX   = 250
MIN_PLATE_SHARPNESS = 25.0
MIN_OCR_CONFIDENCE  = 0.5
PLATE_TEXT_MINLEN   = 5
PLATE_TEXT_MAXLEN   = 12
SUPER_RES_MIN_WIDTH = 160
SUPER_RES_SCALE     = 2
MAX_TRAJECTORY_LEN  = 30

VEHICLE_CLASS_MAP = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}
VEHICLE_CLASSES   = list(VEHICLE_CLASS_MAP.keys())

PLATE_SANITY_RE = re.compile(r"^(?=.*[A-Z])(?=.*[0-9])[A-Z0-9]{5,12}$")


# ── Per-track state ───────────────────────────────────────────────────────────
@dataclass
class TrackRecord:
    track_id: int
    vehicle_type_votes: dict = field(default_factory=dict)
    plate_votes:        dict = field(default_factory=dict)
    plate_read_count:   dict = field(default_factory=dict)
    trajectory:    deque = field(default_factory=lambda: deque(maxlen=MAX_TRAJECTORY_LEN))
    first_seen_frame: Optional[int] = None
    last_seen_frame:  Optional[int] = None
    last_ocr_frame:   int = -999
    theft_alerted:    bool = False
    best_crop_b64:    Optional[str] = None
    best_plate_crop_b64: Optional[str] = None  # plate crop image for gallery

    def register_detection(self, frame_idx, class_name, center):
        if self.first_seen_frame is None:
            self.first_seen_frame = frame_idx
        self.last_seen_frame = frame_idx
        self.vehicle_type_votes[class_name] = self.vehicle_type_votes.get(class_name, 0) + 1
        self.trajectory.append(center)

    def register_plate_read(self, frame_idx, text, confidence):
        self.plate_votes[text]      = self.plate_votes.get(text, 0.0) + confidence
        self.plate_read_count[text] = self.plate_read_count.get(text, 0) + 1
        self.last_ocr_frame = frame_idx

    def best_vehicle_type(self) -> str:
        if not self.vehicle_type_votes:
            return "unknown"
        return max(self.vehicle_type_votes.items(), key=lambda kv: kv[1])[0]

    def best_plate(self) -> Tuple[str, float, int]:
        if not self.plate_votes:
            return "", 0.0, 0
        text       = max(self.plate_votes.items(), key=lambda kv: kv[1])[0]
        total_conf = self.plate_votes[text]
        reads      = self.plate_read_count[text]
        return text, (total_conf / reads if reads else 0.0), reads


# ── Helpers ───────────────────────────────────────────────────────────────────
def clamp_box(x1, y1, x2, y2, w, h):
    return max(0, int(x1)), max(0, int(y1)), min(int(x2), w - 1), min(int(y2), h - 1)


def sharpness_laplacian(gray_crop: np.ndarray) -> float:
    return float(cv2.Laplacian(gray_crop, cv2.CV_64F).var())


def clean_plate_text(txt: str) -> str:
    return "".join(ch for ch in txt if ch.isalnum()).upper()


def is_plausible_plate(txt: str) -> bool:
    if not (PLATE_TEXT_MINLEN <= len(txt) <= PLATE_TEXT_MAXLEN):
        return False
    return bool(PLATE_SANITY_RE.match(txt))


def enhance_plate_crop(crop_bgr: np.ndarray) -> np.ndarray:
    """CLAHE + unsharp masking + super-resolution — identical to number-plate-detection.py."""
    gray    = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    clahe   = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    gray    = clahe.apply(gray)
    blurred = cv2.GaussianBlur(gray, (0, 0), sigmaX=1.2)
    sharp   = cv2.addWeighted(gray, 1.6, blurred, -0.6, 0)
    h, w    = sharp.shape[:2]
    if w < SUPER_RES_MIN_WIDTH:
        sharp = cv2.resize(sharp, (w * SUPER_RES_SCALE, h * SUPER_RES_SCALE),
                           interpolation=cv2.INTER_CUBIC)
    return cv2.cvtColor(sharp, cv2.COLOR_GRAY2BGR)


def build_ocr_engine(use_gpu: bool):
    """
    Build PaddleOCR 2.x engine (requires paddleocr==2.9.x + paddlepaddle 2.6.x).
    Fast, accurate, GPU-capable — the standard production choice.
    """
    try:
        from paddleocr import PaddleOCR
        engine = PaddleOCR(
            use_angle_cls=True,
            lang="en",
            use_gpu=use_gpu,
            show_log=False,
            enable_mkldnn=True,   # CPU acceleration
            cpu_threads=4,
            det_db_score_mode="slow",   # more accurate plate boundary detection
            rec_algorithm="SVTR_LCNet", # lightweight+fast recognition model
        )
        log.info("PaddleOCR 2.x engine ready (gpu=%s)", use_gpu)
        return engine
    except Exception as exc:
        log.error("PaddleOCR init failed: %s", exc)
        return None


def run_ocr(ocr_engine, crop_bgr: np.ndarray) -> List[Tuple[str, float]]:
    """
    Run PaddleOCR 2.x inference on a BGR crop.
    Returns list of (text, confidence) tuples.
    """
    if ocr_engine is None:
        return []
    try:
        results = ocr_engine.ocr(crop_bgr, cls=True)
    except Exception as exc:
        log.debug("OCR run error: %s", exc)
        return []

    if not results:
        return []

    lines = []
    for page in results:
        if page is None:
            continue
        for item in page:
            try:
                # PaddleOCR 2.x: item = [[pts], (text, conf)]
                text, conf = item[1]
                if text:
                    lines.append((text.strip(), float(conf)))
            except (TypeError, IndexError, ValueError):
                continue
    return lines


# ── Main Analyzer ─────────────────────────────────────────────────────────────
class ANPRAnalyzer:
    """
    Headless ANPR analyzer.
    Maintains per-track state across frames; call analyze_frame() per frame.
    Call get_all_tracks() to get final plate results.
    """

    def __init__(
        self,
        vehicle_model_path: str,
        lp_model_path: str,
        watchlist: Optional[Set[str]] = None,
    ):
        self.use_gpu    = torch.cuda.is_available()
        self.device     = 0 if self.use_gpu else "cpu"

        try:
            self.vehicle_model = YOLO(vehicle_model_path)
            log.info("ANPR vehicle model loaded: %s", vehicle_model_path)
        except Exception as exc:
            log.error("Failed to load vehicle model: %s", exc)
            self.vehicle_model = None

        self.lp_model = None
        if lp_model_path:
            try:
                self.lp_model = YOLO(lp_model_path)
                log.info("License plate model loaded: %s", lp_model_path)
            except Exception as exc:
                log.warning("LP model not loaded: %s", exc)

        log.info("Initializing PaddleOCR engine...")
        try:
            self.ocr_engine = build_ocr_engine(self.use_gpu)
            log.info("PaddleOCR ready")
        except Exception as exc:
            log.error("PaddleOCR failed to initialize: %s", exc)
            self.ocr_engine = None

        self.watchlist: Set[str] = watchlist or set()
        self.tracks: Dict[int, TrackRecord] = {}
        self._frame_idx = 0

    @property
    def ready(self) -> bool:
        return self.vehicle_model is not None

    def update_watchlist(self, watchlist: Set[str]):
        self.watchlist = watchlist

    def analyze_frame(
        self,
        frame_orig: np.ndarray,
        drone_id: str = "DRONE_KL_001",
    ) -> dict:
        """
        Process a single full-resolution frame.
        Two-resolution pipeline: track on 720p, OCR on original-res crop.
        """
        if not self.ready:
            return {
                "drone_id": drone_id, "ts": datetime.now(timezone.utc).isoformat(),
                "plate_reads": [], "total_vehicles": 0, "plates_read": 0,
                "theft_alerts": 0, "inference_ms": 0.0,
            }

        t0 = time.perf_counter()
        self._frame_idx += 1
        frame_idx = self._frame_idx

        orig_h, orig_w = frame_orig.shape[:2]
        scale_x = orig_w / TRACK_RESIZE_WIDTH
        scale_y = orig_h / TRACK_RESIZE_HEIGHT

        frame_track = cv2.resize(frame_orig, (TRACK_RESIZE_WIDTH, TRACK_RESIZE_HEIGHT))

        # ── 1. Vehicle detection + tracking (720p) ──────────────────────────
        try:
            results = self.vehicle_model.track(
                frame_track, persist=True, imgsz=IMG_SIZE, conf=CONF_VEHICLE,
                classes=VEHICLE_CLASSES, device=self.device, verbose=False,
            )
        except Exception as exc:
            log.warning("Vehicle tracking failed: %s", exc)
            return {
                "drone_id": drone_id, "ts": datetime.now(timezone.utc).isoformat(),
                "plate_reads": [], "total_vehicles": 0, "plates_read": 0,
                "theft_alerts": 0, "inference_ms": 0.0,
            }

        plate_reads  = []
        theft_alerts = 0

        if results and results[0].boxes:
            for box in results[0].boxes:
                if box.id is None:
                    continue

                track_id   = int(box.id[0])
                cls_id     = int(box.cls[0])
                class_name = VEHICLE_CLASS_MAP.get(cls_id, "vehicle")

                tx1, ty1, tx2, ty2 = clamp_box(
                    *box.xyxy[0].tolist(), TRACK_RESIZE_WIDTH, TRACK_RESIZE_HEIGHT
                )
                cx, cy = (tx1 + tx2) // 2, (ty1 + ty2) // 2

                rec = self.tracks.setdefault(track_id, TrackRecord(track_id=track_id))
                rec.register_detection(frame_idx, class_name, (cx, cy))

                # ── 2. Plate OCR on full-res crop ───────────────────────────
                due_for_ocr = (frame_idx - rec.last_ocr_frame) >= OCR_EVERY_N_FRAMES
                if self.lp_model and self.ocr_engine and due_for_ocr:
                    ox1 = int(tx1 * scale_x) - VEHICLE_CROP_PAD
                    oy1 = int(ty1 * scale_y) - VEHICLE_CROP_PAD
                    ox2 = int(tx2 * scale_x) + VEHICLE_CROP_PAD
                    oy2 = int(ty2 * scale_y) + VEHICLE_CROP_PAD
                    ox1, oy1, ox2, oy2 = clamp_box(ox1, oy1, ox2, oy2, orig_w, orig_h)
                    vehicle_crop = frame_orig[oy1:oy2, ox1:ox2]

                    if vehicle_crop.size > 0:
                        if rec.best_crop_b64 is None:
                            _, buf = cv2.imencode(".jpg", vehicle_crop,
                                                  [cv2.IMWRITE_JPEG_QUALITY, 90])
                            rec.best_crop_b64 = base64.b64encode(buf).decode()

                        try:
                            lp_res = self.lp_model.predict(
                                vehicle_crop, imgsz=IMG_SIZE, conf=CONF_LP,
                                device=self.device, verbose=False,
                            )
                            if lp_res and lp_res[0].boxes and len(lp_res[0].boxes) > 0:
                                confs    = lp_res[0].boxes.conf.tolist()
                                best_idx = confs.index(max(confs))
                                bx1, by1, bx2, by2 = lp_res[0].boxes.xyxy[best_idx].tolist()
                                ch, cw = vehicle_crop.shape[:2]
                                lx1, ly1, lx2, ly2 = clamp_box(bx1, by1, bx2, by2, cw, ch)
                                plate_area = (lx2 - lx1) * (ly2 - ly1)

                                if plate_area >= MIN_PLATE_AREA_PX:
                                    plate_crop = vehicle_crop[ly1:ly2, lx1:lx2]
                                    if plate_crop.size > 0:
                                        gray_check = cv2.cvtColor(plate_crop, cv2.COLOR_BGR2GRAY)
                                        if sharpness_laplacian(gray_check) >= MIN_PLATE_SHARPNESS:
                                            enhanced = enhance_plate_crop(plate_crop)
                                            lines    = run_ocr(self.ocr_engine, enhanced)
                                            if lines:
                                                raw_txt  = "".join(t for t, _ in lines)
                                                avg_conf = sum(c for _, c in lines) / len(lines)
                                                clean    = clean_plate_text(raw_txt)
                                                if avg_conf >= MIN_OCR_CONFIDENCE and is_plausible_plate(clean):
                                                    rec.register_plate_read(frame_idx, clean, avg_conf)
                                                    # Store plate crop as b64 for MinIO upload
                                                    _, pbuf = cv2.imencode(".jpg", plate_crop,
                                                                            [cv2.IMWRITE_JPEG_QUALITY, 90])
                                                    rec.best_plate_crop_b64 = base64.b64encode(pbuf).decode()
                                        else:
                                            rec.last_ocr_frame = frame_idx
                        except Exception as exc:
                            log.debug("LP inference error: %s", exc)
                            rec.last_ocr_frame = frame_idx

                # ── 3. Build result for this track ──────────────────────────
                plate_text, plate_conf, plate_reads_count = rec.best_plate()
                is_theft = bool(plate_text) and plate_text in self.watchlist

                if is_theft and not rec.theft_alerted:
                    log.warning("[THEFT ALERT] Track %d plate %s matches watchlist",
                                track_id, plate_text)
                    rec.theft_alerted = True
                    theft_alerts += 1

                # Compute vehicle bbox scaled back to original frame coords for annotation
                veh_bbox_orig = [
                    round(tx1 * scale_x, 2), round(ty1 * scale_y, 2),
                    round(tx2 * scale_x, 2), round(ty2 * scale_y, 2),
                ]

                plate_reads.append({
                    "tracker_id":        track_id,
                    "vehicle_type":      class_name,
                    "plate_text":        plate_text,
                    "ocr_confidence":    round(plate_conf, 3),
                    "is_reliable":       (plate_conf >= MIN_OCR_CONFIDENCE
                                          and is_plausible_plate(plate_text)) if plate_text else False,
                    "times_read":        plate_reads_count,
                    "is_watchlisted":    is_theft,
                    "vehicle_image_b64": rec.best_crop_b64,
                    "plate_crop_b64":    rec.best_plate_crop_b64,
                    "vehicle_bbox":      veh_bbox_orig,
                    "first_seen_frame":  rec.first_seen_frame,
                    "last_seen_frame":   rec.last_seen_frame,
                })

        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {
            "drone_id":       drone_id,
            "ts":             datetime.now(timezone.utc).isoformat(),
            "plate_reads":    plate_reads,
            "total_vehicles": len(plate_reads),
            "plates_read":    sum(1 for p in plate_reads if p["plate_text"]),
            "theft_alerts":   theft_alerts,
            "inference_ms":   round(elapsed_ms, 1),
        }

    def get_all_tracks(self) -> List[dict]:
        """Return final per-track plate summary (for batch video processing)."""
        result = []
        for rec in self.tracks.values():
            plate_text, plate_conf, reads = rec.best_plate()
            is_theft = bool(plate_text) and plate_text in self.watchlist
            result.append({
                "tracker_id":       rec.track_id,
                "vehicle_type":     rec.best_vehicle_type(),
                "plate_text":       plate_text,
                "ocr_confidence":   round(plate_conf, 3),
                "times_read":       reads,
                "is_reliable":      is_plausible_plate(plate_text) if plate_text else False,
                "is_watchlisted":   is_theft,
                "first_seen_frame": rec.first_seen_frame,
                "last_seen_frame":  rec.last_seen_frame,
                "vehicle_image_b64": rec.best_crop_b64,
            })
        return result
