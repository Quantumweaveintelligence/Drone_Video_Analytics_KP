# -*- coding: utf-8 -*-
"""
ANPR Analyzer — adapted from number-plate-detection.py.
Two-resolution pipeline: track on 720p, OCR on full-res crop.
Uses PaddleOCR + CLAHE + unsharp masking for aerial footage.
Includes VehicleReIDTracker for appearance-based re-identification
to prevent duplicate tracker IDs when ByteTrack loses/re-acquires vehicles.
"""
import base64
import logging
import os
import re
import time
from collections import defaultdict, deque
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
CONF_LP             = 0.15          # lowered for aerial footage
VEHICLE_CROP_PAD    = 30           # wider crop to capture more context
OCR_EVERY_N_FRAMES  = 5            # OCR more frequently
MIN_PLATE_AREA_PX   = 50           # lowered: plates are tiny from aerial view
MIN_PLATE_SHARPNESS = 6.0          # lowered: aerial footage is naturally soft
MIN_OCR_CONFIDENCE  = 0.4          # slightly lower to catch partial reads
PLATE_TEXT_MINLEN   = 4
PLATE_TEXT_MAXLEN   = 12
SUPER_RES_MIN_WIDTH = 80           # lowered to upscale even small plates
SUPER_RES_SCALE     = 3            # upscale more aggressively
MAX_TRAJECTORY_LEN  = 30

# Vehicle classes present in VisDrone and COCO models
ALLOWED_VEHICLE_CLASSES = {
    "car", "truck", "bus", "motorcycle", "motor", "van",
    "auto-rickshaw", "tricycle", "awning-tricycle", "bicycle", "vehicle",
}
VEHICLE_NAME_STANDARDIZATION = {
    "motor": "motorcycle",
    "awning-tricycle": "auto-rickshaw",
    "tricycle": "auto-rickshaw",
}
COCO_VEHICLE_IDS = {2, 3, 5, 6, 7}  # car, motorcycle, bus, train, truck

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
    ocr_attempts:     int = 0
    theft_alerted:    bool = False
    best_crop_b64:    Optional[str] = None
    best_plate_crop_b64: Optional[str] = None  # plate crop image for gallery
    best_plate_sharpness: float = -1.0         # sharpness score of best plate crop
    best_plate_bbox: Optional[list] = None      # plate bbox in full-frame coords [x1,y1,x2,y2]
    best_plate_rel_bbox: Optional[list] = None  # [rx1, ry1, rx2, ry2] relative to vehicle crop (0..1)


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


# ── Vehicle Re-ID Tracker ────────────────────────────────────────────────────

@dataclass
class _ReIDFeatures:
    """Visual + spatial features for a tracked vehicle."""
    hist: np.ndarray          # HSV colour histogram (flattened, normalised)
    aspect_ratio: float       # width / height of bounding box
    centroid: Tuple[float, float]  # (cx, cy) in tracking-resolution coords
    frame_idx: int            # last frame this vehicle was seen


class VehicleReIDTracker:
    """
    Appearance-based vehicle re-identification layer.

    Sits between ByteTrack's raw IDs and the ANPR TrackRecord dict.
    When ByteTrack assigns a new ID (due to occlusion, edge exit, etc.),
    this tracker checks if the vehicle visually matches a recently-lost
    track and remaps the ID to the original stable ID.

    Feature vector: HSV colour histogram + aspect ratio + spatial proximity.
    Zero external dependencies — uses OpenCV histogram comparison.
    """

    # HSV histogram bins — coarse enough to be noise-tolerant
    H_BINS, S_BINS, V_BINS = 16, 16, 8

    def __init__(
        self,
        match_threshold: float = 0.55,
        max_lost_frames: int = 90,
        hist_weight: float = 0.5,
        aspect_weight: float = 0.2,
        spatial_weight: float = 0.3,
        frame_width: int = TRACK_RESIZE_WIDTH,
        frame_height: int = TRACK_RESIZE_HEIGHT,
    ):
        self.match_threshold = match_threshold
        self.max_lost_frames = max_lost_frames
        self.hist_weight     = hist_weight
        self.aspect_weight   = aspect_weight
        self.spatial_weight  = spatial_weight
        self.frame_diag      = (frame_width ** 2 + frame_height ** 2) ** 0.5

        # ByteTrack ID → stable ID mapping
        self._bt_to_stable: Dict[int, int] = {}

        # Stable ID → current features  (currently visible vehicles)
        self._active: Dict[int, _ReIDFeatures] = {}

        # Stable ID → features at time of disappearance
        self._lost: Dict[int, _ReIDFeatures] = {}

        # Monotonic stable-ID counter
        self._next_stable_id: int = 1

        # ByteTrack IDs seen in the *current* frame (reset each frame)
        self._frame_bt_ids: set = set()

        # Statistics
        self.total_remaps: int = 0
        self.total_new_ids: int = 0

    def reset(self):
        """Reset all tracking and Re-ID state between missions/streams."""
        self._bt_to_stable.clear()
        self._active.clear()
        self._lost.clear()
        self._frame_bt_ids.clear()
        self._next_stable_id = 1
        self.total_remaps = 0
        self.total_new_ids = 0

    # ── Feature extraction ─────────────────────────────────────────────────

    def _extract_hist(self, crop_bgr: np.ndarray) -> np.ndarray:
        """Compute normalised HSV colour histogram from a BGR crop."""
        hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist(
            [hsv], [0, 1, 2],
            None,
            [self.H_BINS, self.S_BINS, self.V_BINS],
            [0, 180, 0, 256, 0, 256],
        )
        cv2.normalize(hist, hist)
        return hist.flatten()

    def _extract_features(
        self,
        crop_bgr: np.ndarray,
        cx: float, cy: float,
        bw: float, bh: float,
        frame_idx: int,
    ) -> _ReIDFeatures:
        """Build feature vector for a detected vehicle."""
        hist = self._extract_hist(crop_bgr)
        aspect = bw / max(bh, 1.0)
        return _ReIDFeatures(
            hist=hist,
            aspect_ratio=aspect,
            centroid=(cx, cy),
            frame_idx=frame_idx,
        )

    # ── Similarity scoring ─────────────────────────────────────────────────

    def _similarity(self, a: _ReIDFeatures, b: _ReIDFeatures) -> float:
        """
        Weighted similarity score between two feature sets.
        Returns a value in [0, 1] where 1 = perfect match.
        """
        # 1. Histogram similarity (correlation: -1..1, mapped to 0..1)
        hist_sim = cv2.compareHist(
            a.hist.astype(np.float32),
            b.hist.astype(np.float32),
            cv2.HISTCMP_CORREL,
        )
        hist_sim = max(0.0, (hist_sim + 1.0) / 2.0)  # map [-1,1] → [0,1]

        # 2. Aspect ratio similarity (penalise large differences)
        ar_diff = abs(a.aspect_ratio - b.aspect_ratio) / max(a.aspect_ratio, b.aspect_ratio, 0.01)
        aspect_sim = max(0.0, 1.0 - ar_diff)

        # 3. Spatial proximity (normalised by frame diagonal)
        dx = a.centroid[0] - b.centroid[0]
        dy = a.centroid[1] - b.centroid[1]
        dist = (dx ** 2 + dy ** 2) ** 0.5
        spatial_sim = max(0.0, 1.0 - dist / (self.frame_diag * 0.5))

        return (
            self.hist_weight    * hist_sim
            + self.aspect_weight  * aspect_sim
            + self.spatial_weight * spatial_sim
        )

    # ── Core resolve method ────────────────────────────────────────────────

    def resolve_id(
        self,
        bt_id: int,
        crop_bgr: np.ndarray,
        cx: float, cy: float,
        bw: float, bh: float,
        frame_idx: int,
    ) -> int:
        """
        Resolve a ByteTrack ID to a stable Re-ID.

        Args:
            bt_id:     Raw ByteTrack tracker ID
            crop_bgr:  Vehicle crop (BGR, from tracking resolution)
            cx, cy:    Centroid in tracking-resolution coords
            bw, bh:    Bounding box width/height
            frame_idx: Current frame index

        Returns:
            Stable vehicle ID (int) that persists across track losses.
        """
        self._frame_bt_ids.add(bt_id)

        # Fast path: ByteTrack ID already mapped to a stable ID
        if bt_id in self._bt_to_stable:
            stable_id = self._bt_to_stable[bt_id]
            # Update features for this active track
            if crop_bgr is not None and crop_bgr.size > 0:
                self._active[stable_id] = self._extract_features(
                    crop_bgr, cx, cy, bw, bh, frame_idx
                )
            return stable_id

        # New ByteTrack ID — try to match against lost tracks
        features = None
        if crop_bgr is not None and crop_bgr.size > 0:
            features = self._extract_features(crop_bgr, cx, cy, bw, bh, frame_idx)

        best_match_id = None
        best_score = 0.0

        if features is not None and self._lost:
            for lost_id, lost_feat in self._lost.items():
                score = self._similarity(features, lost_feat)
                if score > best_score:
                    best_score = score
                    best_match_id = lost_id

        if best_match_id is not None and best_score >= self.match_threshold:
            # Re-identified! Remap new ByteTrack ID to old stable ID
            stable_id = best_match_id
            self._bt_to_stable[bt_id] = stable_id
            del self._lost[best_match_id]
            self._active[stable_id] = features
            self.total_remaps += 1
            log.info(
                "Re-ID MATCH: ByteTrack %d → stable %d (score=%.3f)",
                bt_id, stable_id, best_score,
            )
            return stable_id

        # No match — assign new stable ID
        stable_id = self._next_stable_id
        self._next_stable_id += 1
        self._bt_to_stable[bt_id] = stable_id
        if features is not None:
            self._active[stable_id] = features
        self.total_new_ids += 1
        return stable_id

    # ── End-of-frame housekeeping ──────────────────────────────────────────

    def end_frame(self, frame_idx: int):
        """
        Call at the end of each frame.
        Moves disappeared tracks from active → lost, prunes stale lost tracks.
        """
        # Find ByteTrack IDs that were active last frame but not this frame
        disappeared_bt_ids = [
            bt_id for bt_id in self._bt_to_stable
            if bt_id not in self._frame_bt_ids
        ]

        for bt_id in disappeared_bt_ids:
            stable_id = self._bt_to_stable.pop(bt_id)
            if stable_id in self._active:
                self._lost[stable_id] = self._active.pop(stable_id)

        # Prune lost tracks that are too old
        stale = [
            sid for sid, feat in self._lost.items()
            if (frame_idx - feat.frame_idx) > self.max_lost_frames
        ]
        for sid in stale:
            del self._lost[sid]

        # Reset frame tracking
        self._frame_bt_ids.clear()

    # ── Stats ──────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Return tracker statistics for the /tracking/stats endpoint."""
        return {
            "total_stable_ids_assigned": self._next_stable_id - 1,
            "total_remaps":             self.total_remaps,
            "total_new_ids":            self.total_new_ids,
            "active_tracks":            len(self._active),
            "lost_tracks":              len(self._lost),
            "bt_id_mappings":           len(self._bt_to_stable),
            "match_threshold":          self.match_threshold,
            "max_lost_frames":          self.max_lost_frames,
        }


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
        # Force use_gpu=False because the PyTorch CUDA 12.1 container is incompatible 
        # with PaddlePaddle's expected CUDNN version, causing silent crashes.
        engine = PaddleOCR(
            use_angle_cls=True,
            lang="en",
            use_gpu=False,
            show_log=False,
            enable_mkldnn=True,   # CPU acceleration
            cpu_threads=4,
            det_db_score_mode="slow",   # more accurate plate boundary detection
            rec_algorithm="SVTR_LCNet", # lightweight+fast recognition model
        )
        log.info("PaddleOCR 2.x engine ready (forced cpu to avoid cudnn error)")
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
        reid_match_threshold: float = 0.55,
        reid_max_lost_frames: int = 90,
        reid_hist_weight: float = 0.5,
        reid_aspect_weight: float = 0.2,
        reid_spatial_weight: float = 0.3,
    ):
        self.use_gpu    = torch.cuda.is_available()
        self.device     = 0 if self.use_gpu else "cpu"

        self.vehicle_model: Optional[YOLO] = None
        self._vehicle_class_ids: Optional[List[int]] = None
        self._vehicle_class_map: Dict[int, str] = {}

        try:
            self.vehicle_model = YOLO(vehicle_model_path)
            log.info("ANPR vehicle model loaded: %s", vehicle_model_path)

            if hasattr(self.vehicle_model, "names") and self.vehicle_model.names:
                names = self.vehicle_model.names
                class_ids = []
                for cid, cname in names.items():
                    cname_clean = str(cname).lower().strip()
                    if cname_clean in ALLOWED_VEHICLE_CLASSES or int(cid) in COCO_VEHICLE_IDS:
                        class_ids.append(int(cid))
                        self._vehicle_class_map[int(cid)] = VEHICLE_NAME_STANDARDIZATION.get(cname_clean, cname_clean)
                if class_ids:
                    self._vehicle_class_ids = class_ids
                    log.info(
                        "ANPR vehicle detection active class IDs: %s (mapping: %s)",
                        self._vehicle_class_ids, self._vehicle_class_map,
                    )
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
        self._drone_tracks: Dict[str, Dict[int, TrackRecord]] = defaultdict(dict)
        self._drone_missions: Dict[str, str] = {}
        self._frame_idx = 0

        # Enable/disable appearance Re-ID (defaults to False for clean ByteTrack stability)
        self.enable_reid: bool = os.getenv("ANPR_ENABLE_REID", "false").lower() == "true"

        # Vehicle Re-ID tracker for deduplication
        self.reid_tracker = VehicleReIDTracker(
            match_threshold=reid_match_threshold,
            max_lost_frames=reid_max_lost_frames,
            hist_weight=reid_hist_weight,
            aspect_weight=reid_aspect_weight,
            spatial_weight=reid_spatial_weight,
        )
        log.info(
            "Vehicle Re-ID tracker initialised (enabled=%s, threshold=%.2f, max_lost=%d)",
            self.enable_reid, reid_match_threshold, reid_max_lost_frames,
        )

    @property
    def ready(self) -> bool:
        return self.vehicle_model is not None

    @property
    def tracks(self) -> Dict[int, TrackRecord]:
        """Backward-compatible view of all active tracks across drones."""
        merged: Dict[int, TrackRecord] = {}
        for d_tracks in self._drone_tracks.values():
            merged.update(d_tracks)
        return merged

    def reset_drone(self, drone_id: str, new_mission_id: Optional[str] = None):
        """Cleanly flush tracking and Re-ID state for a drone on mission transition or stream reset."""
        log.info("ANPRAnalyzer: resetting tracks for drone %s (new_mission=%s)", drone_id, new_mission_id)
        if drone_id in self._drone_tracks:
            self._drone_tracks[drone_id].clear()
        if new_mission_id is not None:
            self._drone_missions[drone_id] = new_mission_id
        self.reid_tracker.reset()
        if self.vehicle_model and hasattr(self.vehicle_model, "predictor") and self.vehicle_model.predictor:
            for t in getattr(self.vehicle_model.predictor, "trackers", []):
                if hasattr(t, "reset"):
                    try:
                        t.reset()
                    except Exception:
                        pass


    def update_watchlist(self, watchlist: Set[str]):
        self.watchlist = watchlist

    def analyze_frame(
        self,
        frame_orig: np.ndarray,
        drone_id: str = "DRONE_KL_001",
        mission_id: Optional[str] = None,
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

        # Check mission transition per drone
        if mission_id and self._drone_missions.get(drone_id) and self._drone_missions.get(drone_id) != mission_id:
            log.info("ANPRAnalyzer: mission transition for %s (%s -> %s), resetting tracks",
                     drone_id, self._drone_missions.get(drone_id), mission_id)
            self.reset_drone(drone_id, mission_id)
        elif mission_id:
            self._drone_missions[drone_id] = mission_id

        drone_tracks = self._drone_tracks[drone_id]

        t0 = time.perf_counter()
        self._frame_idx += 1
        frame_idx = self._frame_idx

        orig_h, orig_w = frame_orig.shape[:2]
        if orig_w == TRACK_RESIZE_WIDTH and orig_h == TRACK_RESIZE_HEIGHT:
            frame_track = frame_orig
            scale_x = 1.0
            scale_y = 1.0
        else:
            scale_x = orig_w / TRACK_RESIZE_WIDTH
            scale_y = orig_h / TRACK_RESIZE_HEIGHT
            frame_track = cv2.resize(frame_orig, (TRACK_RESIZE_WIDTH, TRACK_RESIZE_HEIGHT), interpolation=cv2.INTER_LINEAR)

        # ── 1. Vehicle detection + tracking (720p) ──────────────────────────
        try:
            track_kwargs = dict(
                persist=True, imgsz=IMG_SIZE, conf=CONF_VEHICLE,
                classes=self._vehicle_class_ids, device=self.device, verbose=False,
            )
            if self.use_gpu:
                track_kwargs["half"] = True
            results = self.vehicle_model.track(frame_track, **track_kwargs)
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

                bt_id      = int(box.id[0])   # raw ByteTrack ID
                cls_id     = int(box.cls[0])
                class_name = self._vehicle_class_map.get(
                    cls_id,
                    self.vehicle_model.names.get(cls_id, "vehicle") if self.vehicle_model and hasattr(self.vehicle_model, "names") else "vehicle"
                )

                tx1, ty1, tx2, ty2 = clamp_box(
                    *box.xyxy[0].tolist(), TRACK_RESIZE_WIDTH, TRACK_RESIZE_HEIGHT
                )
                cx, cy = (tx1 + tx2) // 2, (ty1 + ty2) // 2
                bw, bh = tx2 - tx1, ty2 - ty1

                # ── Re-ID: extract crop at tracking resolution for matching ──
                reid_crop = frame_track[ty1:ty2, tx1:tx2]

                # Resolve ByteTrack ID → stable Re-ID (if enabled)
                if self.enable_reid:
                    track_id = self.reid_tracker.resolve_id(
                        bt_id, reid_crop, cx, cy, bw, bh, frame_idx
                    )
                else:
                    track_id = bt_id

                rec = drone_tracks.setdefault(track_id, TrackRecord(track_id=track_id))
                rec.register_detection(frame_idx, class_name, (cx, cy))

                # ── 2. Always capture vehicle crop on first sight ────────────
                # Compute full-res coordinates for vehicle bounding box
                ox1 = int(tx1 * scale_x) - VEHICLE_CROP_PAD
                oy1 = int(ty1 * scale_y) - VEHICLE_CROP_PAD
                ox2 = int(tx2 * scale_x) + VEHICLE_CROP_PAD
                oy2 = int(ty2 * scale_y) + VEHICLE_CROP_PAD
                ox1, oy1, ox2, oy2 = clamp_box(ox1, oy1, ox2, oy2, orig_w, orig_h)
                vehicle_crop = frame_orig[oy1:oy2, ox1:ox2]

                # Always capture vehicle crop if we don't have one yet
                if vehicle_crop.size > 0 and rec.best_crop_b64 is None:
                    _, buf = cv2.imencode(".jpg", vehicle_crop,
                                          [cv2.IMWRITE_JPEG_QUALITY, 85])
                    rec.best_crop_b64 = base64.b64encode(buf).decode()

                # ── 3. Adaptive OCR on full-res crop ─────────────────────────
                best_text, best_conf, read_count = rec.best_plate()
                if read_count >= 2 and best_conf >= 0.75:
                    ocr_interval = OCR_EVERY_N_FRAMES * 6  # 30 frames: locked in
                elif rec.ocr_attempts >= 12 and read_count == 0:
                    ocr_interval = OCR_EVERY_N_FRAMES * 5  # 25 frames: no plate visible
                else:
                    ocr_interval = OCR_EVERY_N_FRAMES

                due_for_ocr = (frame_idx - rec.last_ocr_frame) >= ocr_interval
                if self.lp_model and self.ocr_engine and due_for_ocr and vehicle_crop.size > 0:
                    rec.ocr_attempts += 1
                    rec.last_ocr_frame = frame_idx
                    try:
                        lp_kwargs = dict(imgsz=IMG_SIZE, conf=CONF_LP, device=self.device, verbose=False)
                        if self.use_gpu:
                            lp_kwargs["half"] = True
                        lp_res = self.lp_model.predict(vehicle_crop, **lp_kwargs)
                        lp_found = False
                        if lp_res and lp_res[0].boxes and len(lp_res[0].boxes) > 0:
                            confs    = lp_res[0].boxes.conf.tolist()
                            best_idx = confs.index(max(confs))
                            bx1, by1, bx2, by2 = lp_res[0].boxes.xyxy[best_idx].tolist()
                            ch, cw = vehicle_crop.shape[:2]
                            # Add slight padding (8% width, 12% height) to prevent character boundary clipping
                            pad_x = int((bx2 - bx1) * 0.08)
                            pad_y = int((by2 - by1) * 0.12)
                            lx1, ly1, lx2, ly2 = clamp_box(bx1 - pad_x, by1 - pad_y, bx2 + pad_x, by2 + pad_y, cw, ch)
                            plate_area = (lx2 - lx1) * (ly2 - ly1)

                            if plate_area >= MIN_PLATE_AREA_PX:
                                plate_crop = vehicle_crop[ly1:ly2, lx1:lx2]
                                if plate_crop.size > 0:
                                    gray_check = cv2.cvtColor(plate_crop, cv2.COLOR_BGR2GRAY)
                                    sharpness = sharpness_laplacian(gray_check)
                                    log.debug("Track %d: LP model found %d boxes, best conf=%.2f area=%d sharp=%.1f",
                                              track_id, len(lp_res[0].boxes), max(confs), plate_area, sharpness)

                                    # Always capture the best plate crop if none exists or if this frame is sharper
                                    if rec.best_plate_crop_b64 is None or sharpness > rec.best_plate_sharpness:
                                        rec.best_plate_sharpness = sharpness
                                        _, pbuf = cv2.imencode(".jpg", plate_crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
                                        rec.best_plate_crop_b64 = base64.b64encode(pbuf).decode()
                                        rec.best_plate_rel_bbox = [
                                            round(lx1 / max(cw, 1), 4),
                                            round(ly1 / max(ch, 1), 4),
                                            round(lx2 / max(cw, 1), 4),
                                            round(ly2 / max(ch, 1), 4),
                                        ]
                                        pfx1 = round(ox1 + lx1, 2)
                                        pfy1 = round(oy1 + ly1, 2)
                                        pfx2 = round(ox1 + lx2, 2)
                                        pfy2 = round(oy1 + ly2, 2)
                                        rec.best_plate_bbox = [pfx1, pfy1, pfx2, pfy2]
                                        if vehicle_crop.size > 0:
                                            _, vbuf = cv2.imencode(".jpg", vehicle_crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
                                            rec.best_crop_b64 = base64.b64encode(vbuf).decode()

                                    # Two-tier OCR: Run on raw crop first (preserves sharp character contours)
                                    lines = run_ocr(self.ocr_engine, plate_crop)
                                    if not lines and sharpness >= MIN_PLATE_SHARPNESS:
                                        # Fallback to enhanced crop only if raw yielded nothing
                                        enhanced = enhance_plate_crop(plate_crop)
                                        lines    = run_ocr(self.ocr_engine, enhanced)

                                    if lines:
                                        raw_txt  = "".join(t for t, _ in lines)
                                        avg_conf = sum(c for _, c in lines) / len(lines)
                                        clean    = clean_plate_text(raw_txt)
                                        if avg_conf >= MIN_OCR_CONFIDENCE and is_plausible_plate(clean):
                                            rec.register_plate_read(frame_idx, clean, avg_conf)
                                            lp_found = True
                            else:
                                log.debug("Track %d: plate area %d < %d, skipping",
                                          track_id, plate_area, MIN_PLATE_AREA_PX)

                        # ── OCR-direct fallback (only initial attempts) ──
                        if not lp_found and rec.best_plate_crop_b64 is None and rec.ocr_attempts <= 2:
                            try:
                                enhanced_vc = enhance_plate_crop(vehicle_crop)
                                ocr_results = self.ocr_engine.ocr(enhanced_vc, cls=True)
                                if ocr_results and ocr_results[0]:
                                    for line in ocr_results[0]:
                                        box_pts, (text, conf) = line
                                        clean = clean_plate_text(text)
                                        if conf >= MIN_OCR_CONFIDENCE and is_plausible_plate(clean):
                                            # Extract plate region from OCR box
                                            xs = [pt[0] for pt in box_pts]
                                            ys = [pt[1] for pt in box_pts]
                                            bx1o, by1o = int(min(xs)), int(min(ys))
                                            bx2o, by2o = int(max(xs)), int(max(ys))
                                            ch, cw = vehicle_crop.shape[:2]
                                            bx1o, by1o, bx2o, by2o = clamp_box(bx1o, by1o, bx2o, by2o, cw, ch)
                                            ocr_plate_crop = vehicle_crop[by1o:by2o, bx1o:bx2o]
                                            if ocr_plate_crop.size > 0:
                                                rec.register_plate_read(frame_idx, clean, conf)
                                                _, pbuf = cv2.imencode(".jpg", ocr_plate_crop,
                                                                        [cv2.IMWRITE_JPEG_QUALITY, 85])
                                                rec.best_plate_crop_b64 = base64.b64encode(pbuf).decode()
                                                rec.best_plate_rel_bbox = [
                                                    round(bx1o / max(cw, 1), 4),
                                                    round(by1o / max(ch, 1), 4),
                                                    round(bx2o / max(cw, 1), 4),
                                                    round(by2o / max(ch, 1), 4),
                                                ]
                                                pfx1 = round(ox1 + bx1o, 2)
                                                pfy1 = round(oy1 + by1o, 2)
                                                pfx2 = round(ox1 + bx2o, 2)
                                                pfy2 = round(oy1 + by2o, 2)
                                                rec.best_plate_bbox = [pfx1, pfy1, pfx2, pfy2]
                                                if vehicle_crop.size > 0:
                                                    _, vbuf = cv2.imencode(".jpg", vehicle_crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
                                                    rec.best_crop_b64 = base64.b64encode(vbuf).decode()
                                                log.debug("Track %d: OCR fallback found plate '%s' conf=%.2f",
                                                          track_id, clean, conf)
                                                break  # use first valid plate
                            except Exception as ocr_exc:
                                log.debug("OCR fallback error: %s", ocr_exc)

                    except Exception as exc:
                        log.debug("LP inference error: %s", exc)

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

                # Dynamic projection of plate bounding box onto current vehicle position
                current_plate_bbox = None
                if rec.best_plate_rel_bbox and len(rec.best_plate_rel_bbox) == 4:
                    rx1, ry1, rx2, ry2 = rec.best_plate_rel_bbox
                    vw = max(0, ox2 - ox1)
                    vh = max(0, oy2 - oy1)
                    current_plate_bbox = [
                        round(ox1 + rx1 * vw, 2),
                        round(oy1 + ry1 * vh, 2),
                        round(ox1 + rx2 * vw, 2),
                        round(oy1 + ry2 * vh, 2),
                    ]
                elif rec.best_plate_bbox:
                    current_plate_bbox = rec.best_plate_bbox

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
                    "plate_bbox":        current_plate_bbox,
                    "first_seen_frame":  rec.first_seen_frame,
                    "last_seen_frame":   rec.last_seen_frame,
                })

        # ── End-of-frame Re-ID housekeeping ──────────────────────────────
        if self.enable_reid:
            self.reid_tracker.end_frame(frame_idx)

        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {
            "drone_id":       drone_id,
            "ts":             datetime.now(timezone.utc).isoformat(),
            "plate_reads":    plate_reads,
            "total_vehicles": len(plate_reads),
            "plates_read":    sum(1 for p in plate_reads if p["plate_text"]),
            "theft_alerts":   theft_alerts,
            "inference_ms":   round(elapsed_ms, 1),
            "reid_stats":     self.reid_tracker.get_stats(),
        }

    def get_all_tracks(self, drone_id: Optional[str] = None) -> List[dict]:
        """Return final per-track plate summary (for batch video processing)."""
        result = []
        track_source = self._drone_tracks[drone_id].values() if (drone_id and drone_id in self._drone_tracks) else self.tracks.values()
        for rec in track_source:
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
