# -*- coding: utf-8 -*-
"""
Person & Face Analyzer — production-ready with ByteTrack cross-frame tracking.
Uses YOLOv8m for person tracking (batched predict + one tracker per drone via
shared.batch_tracker.BatchTracker) and YOLOv8n-face
for tile-based face detection, linking faces to person tracks by IoU.
"""
import base64
import logging
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import torch
from ultralytics import YOLO

try:                                                    # Ultralytics >= 8.3.1xx
    from ultralytics.utils.nms import non_max_suppression as _yolo_nms
except ImportError:
    from ultralytics.utils.ops import non_max_suppression as _yolo_nms
import inspect as _inspect
_NMS_KW = set(_inspect.signature(_yolo_nms).parameters)

from shared import gpu_profile
from shared.batch_tracker import BatchTracker

log = logging.getLogger(__name__)

def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


# Faces are searched at NATIVE resolution: the frame is cut into tiles of this size
# (the face model's input size), so a 4K frame is not shrunk ~3x before face detection
# (it was 2 tiles of ~2070 px squeezed into 640). 3840x2160 → 7 x 4 tiles, one GPU batch.
FACE_TILE        = _env_int("PERSON_FACE_TILE", 640, 320, 1280)
FACE_TILE_OVERLAP = _env_int("PERSON_FACE_TILE_OVERLAP", 96, 0, 320)
FACE_TILE_BATCH  = _env_int("PERSON_FACE_TILE_BATCH", 32, 1, 128)
TILE_IMG_SIZE    = FACE_TILE
# v2 R4 (no change to what is detected — same pixels, same model, same thresholds):
# GPU_TILES: the frame is uploaded once and the tiles are cut on the GPU, instead of 28
#   numpy crops letterboxed and stacked on the CPU (identity letterbox at tile == imgsz).
# PARALLEL: face tiles run on their own CUDA stream while person detection + tracking
#   runs, instead of after it (a round costs max(person, face) rather than the sum).
FACE_GPU_TILES   = os.getenv("PERSON_FACE_GPU_TILES", "1") != "0"
FACE_PARALLEL    = os.getenv("PERSON_FACE_PARALLEL", "1") != "0"
# DIRECT: GPU tiles go straight through the model and Ultralytics' own NMS. predict() would
#   copy every tile back to the host only to read its shape (~19 ms per 4K frame on the pod).
FACE_DIRECT      = os.getenv("PERSON_FACE_DIRECT", "1") != "0"
# After a CUDA failure the analyzer runs on the CPU; it retries the GPU after this long
GPU_RETRY_S      = _env_int("PERSON_GPU_RETRY_S", 60, 10, 3600)


def person_imgsz() -> int:
    """Person detector input size. PERSON_IMGSZ=<n> fixes it; "auto" (default) follows the
    GPU: drone frames are 1080p–4K and people are small, so the big cloud GPUs look at a
    larger input (RTX PRO 6000: 1920, L40S: 1280); small GPUs keep 640."""
    raw = os.getenv("PERSON_IMGSZ", "auto").strip().lower()
    if raw not in ("", "auto"):
        try:
            return max(320, min(3840, int(raw) // 32 * 32))
        except ValueError:
            log.warning("PERSON_IMGSZ=%r is not a number; using auto", raw)
    return {"xlarge": 1920, "large": 1280}.get(gpu_profile.detect().tier, 640)
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


def face_tiles(h_img: int, w_img: int, tile: int = FACE_TILE,
               overlap: int = FACE_TILE_OVERLAP) -> List[Tuple[int, int, int, int]]:
    """(x1, y1, x2, y2) windows of `tile` px covering the frame with `overlap` px overlap.

    Every window is tile x tile (edge windows are shifted inwards), so all tiles share one
    shape and go through the face model as a single batch at native resolution.
    """
    def starts(length: int) -> List[int]:
        if length <= tile:
            return [0]
        step = max(1, tile - overlap)
        out = list(range(0, length - tile + 1, step))
        if out[-1] != length - tile:
            out.append(length - tile)
        return out
    return [(x, y, min(w_img, x + tile), min(h_img, y + tile))
            for y in starts(h_img) for x in starts(w_img)]


def _tile_dets_direct(model: YOLO, crops: torch.Tensor, tile: int):
    """Face boxes per tile as (n, 6) tensors (x1, y1, x2, y2, conf, cls) in tile pixels — the
    same as model.predict() on these tiles: the predictor's own network and NMS with the same
    settings, and boxes clipped to the tile as its scale_boxes() does (the letterbox is the
    identity at tile == imgsz). None when the predictor is not set up for this input yet."""
    p = getattr(model, "predictor", None)
    m = getattr(p, "model", None)
    if not FACE_DIRECT or m is None:
        return None
    try:
        weights = next(m.parameters())
    except StopIteration:
        return None
    if weights.device != crops.device or bool(getattr(m, "fp16", False)) != (crops.dtype == torch.float16):
        return None
    kw = {"max_det": p.args.max_det, "nc": 0, "end2end": getattr(m, "end2end", False)}
    with torch.inference_mode():
        dets = _yolo_nms(m(crops), CONF_FACE, p.args.iou, None, True,
                         **{k: v for k, v in kw.items() if k in _NMS_KW})
        for d in dets:
            d[:, :4] = d[:, :4].clamp(0, tile)
    return dets


def detect_faces_on_tiles(
    model: YOLO,
    frame: np.ndarray,
    device=0,
    tile: int = FACE_TILE,
    overlap: int = FACE_TILE_OVERLAP,
) -> List[Tuple[int, int, int, int, float]]:
    """Face detection at native resolution on overlapping tiles. Returns (x1, y1, x2, y2, conf)."""
    # FP16 on GPUs that support it (no effect on CPU)
    half = device != "cpu" and gpu_profile.predict_kwargs()["half"]
    h_img, w_img = frame.shape[:2]
    windows = face_tiles(h_img, w_img, tile, overlap)
    all_boxes, all_confidences = [], []
    src = None
    if (FACE_GPU_TILES and device != "cpu" and tile % 32 == 0
            and all(x2 - x1 == tile and y2 - y1 == tile for (x1, y1, x2, y2) in windows)):
        src = torch.from_numpy(np.ascontiguousarray(frame)).to(
            f"cuda:{device}" if isinstance(device, int) else device)

    for i in range(0, len(windows), FACE_TILE_BATCH):
        part = windows[i:i + FACE_TILE_BATCH]
        if src is not None:
            # BCHW RGB in 0..1, converted exactly as Ultralytics does for numpy input
            # (uint8 → half/float, then / 255); tensors skip its letterbox and BGR flip.
            crops = torch.stack([src[y1:y2, x1:x2] for (x1, y1, x2, y2) in part])
            crops = crops.flip(-1).permute(0, 3, 1, 2).contiguous()
            crops = (crops.half() if half else crops.float()) / 255
        else:
            crops = [frame[y1:y2, x1:x2] for (x1, y1, x2, y2) in part]
        dets = _tile_dets_direct(model, crops, tile) if src is not None else None
        if dets is None:
            results = model.predict(
                crops, device=device, imgsz=tile,
                conf=CONF_FACE, verbose=False, agnostic_nms=True, half=half,
            )
            dets = [res.boxes.data if res.boxes is not None else torch.zeros((0, 6)) for res in results]
        for (x1, y1, _, _), d in zip(part, dets):
            if len(d) == 0:
                continue
            d = d.float().cpu().numpy()
            xyxy, confs = d[:, :4], d[:, 4]
            for (bx1, by1, bx2, by2), conf in zip(xyxy, confs):
                gx1 = int(bx1 + x1); gy1 = int(by1 + y1)
                gx2 = int(bx2 + x1); gy2 = int(by2 + y1)
                all_boxes.append([gx1, gy1, gx2 - gx1, gy2 - gy1])
                all_confidences.append(float(conf))

    if not all_boxes:
        return []
    indices = cv2.dnn.NMSBoxes(all_boxes, all_confidences, CONF_FACE, NMS_IOU)
    final = []
    if len(indices) > 0:
        for i in indices.flatten():
            x, y, w, h = all_boxes[i]
            conf = all_confidences[i]
            final.append((x, y, x + w, y + h, conf))
    return _drop_contained(final)


def _drop_contained(boxes: List[Tuple[int, int, int, int, float]], ratio: float = 0.6):
    """A face cut by a tile edge gives a partial box next to the full box from the
    neighbouring tile; their IoU is too low for NMS. Drop a box that lies mostly
    (>= ratio of its area) inside a larger box."""
    keep = []
    for i, a in enumerate(boxes):
        area_a = max(1, (a[2] - a[0]) * (a[3] - a[1]))
        inside = False
        for j, b in enumerate(boxes):
            if i == j:
                continue
            area_b = (b[2] - b[0]) * (b[3] - b[1])
            if area_b <= area_a:
                continue
            iw = max(0, min(a[2], b[2]) - max(a[0], b[0]))
            ih = max(0, min(a[3], b[3]) - max(a[1], b[1]))
            if iw * ih >= ratio * area_a:
                inside = True
                break
        if not inside:
            keep.append(a)
    return keep


class PersonFaceAnalyzer:
    """
    Production person + face analyzer with ByteTrack cross-frame person tracking.
    """

    def __init__(self, person_model_path: str, face_model_path: str):
        self.device = 0 if torch.cuda.is_available() else "cpu"
        self.imgsz = person_imgsz()
        # CPU fallback state: when, and why (shown on the Detection Models page)
        self.cpu_since: Optional[float] = None
        self.cpu_reason: str = ""
        # One Ultralytics predictor must never run in two threads at once (plan A1): the
        # stream thread, /analyze/frame and the watchlist enrolment endpoints all use the
        # face model. (The person model is guarded by BatchTracker's lock.)
        self.face_lock = threading.RLock()
        log.info("PersonFaceAnalyzer device: %s, person imgsz %d, face tiles %d px (overlap %d)",
                 self.device, self.imgsz, FACE_TILE, FACE_TILE_OVERLAP)
        self.tracker: Optional[BatchTracker] = None
        # Face tiles of a round run here, on their own CUDA stream, during person tracking
        self._face_pool = ThreadPoolExecutor(1, thread_name_prefix="person-face")
        self._face_stream = torch.cuda.Stream() if self.device != "cpu" else None

        try:
            self.person_model = YOLO(person_model_path)
            # One tracker per drone; newest frames of all drones go through the model
            # as one batch. Holds the same model object, so .to() moves still apply.
            self.tracker = BatchTracker(self.person_model)
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

        # cuDNN autotuning before the first drone frame (plan B8)
        if self.tracker is not None and self.face_model is not None and self.device != "cpu" \
                and os.getenv("DVAS_WARMUP", "1") != "0":
            t = time.perf_counter()
            self.tracker.warmup(self.imgsz, device=self.device)
            try:
                detect_faces_on_tiles(self.face_model, np.zeros((2160, 3840, 3), np.uint8), device=self.device)
            except Exception as exc:
                log.warning("Face model warm-up failed: %s", exc)
            log.info("Person/face warm-up %.1fs", time.perf_counter() - t)

        self._frame_count = 0

    @property
    def ready(self) -> bool:
        return self.person_model is not None and self.face_model is not None

    def _track_kwargs(self) -> dict:
        # Same arguments as the former person_model.track(...) call (persist/verbose
        # are handled by BatchTracker; FP16 comes from gpu_profile.predict_kwargs())
        return dict(imgsz=self.imgsz, conf=CONF_PERSON, classes=[0], device=self.device)

    def detect_faces(self, frame_bgr) -> List[Tuple[int, int, int, int, float]]:
        """Native-resolution tiled face detection, serialised on the face model."""
        with self.face_lock:
            return detect_faces_on_tiles(self.face_model, frame_bgr, device=self.device)

    def _faces_timed(self, frame_bgr) -> Tuple[List[Tuple[int, int, int, int, float]], float]:
        t = time.perf_counter()
        if self._face_stream is not None and self.device != "cpu":
            with torch.cuda.stream(self._face_stream):
                boxes = self.detect_faces(frame_bgr)      # .cpu() of the results syncs the stream
        else:
            boxes = self.detect_faces(frame_bgr)
        return boxes, (time.perf_counter() - t) * 1000

    def detect_faces_direct(self, img_bgr, conf: float = 0.15) -> List[Tuple[int, int, int, int, float]]:
        """Whole-image face detection (enrolment fallback), serialised on the face model."""
        with self.face_lock:
            results = self.face_model(img_bgr, conf=conf, device=self.device, verbose=False)
        out = []
        for res in results:
            for box in res.boxes:
                bx1, by1, bx2, by2 = box.xyxy[0].cpu().numpy()
                out.append((int(bx1), int(by1), int(bx2), int(by2), float(box.conf[0].cpu().numpy())))
        return out

    def device_info(self) -> dict:
        return {"device": "cpu" if self.device == "cpu" else "cuda",
                "cpu_fallback": self.cpu_since is not None, "cpu_reason": self.cpu_reason,
                "imgsz": self.imgsz, "face_tile": FACE_TILE}

    def _fall_back_to_cpu(self, exc: Exception) -> None:
        log.error("CUDA error in person/face inference — running on CPU, GPU retry in %ds: %s",
                  GPU_RETRY_S, exc)
        self.device = "cpu"
        self.cpu_since = time.monotonic()
        self.cpu_reason = str(exc)[:300]
        try:
            if self.tracker is not None:
                self.tracker.to("cpu")            # under the predictor lock
            with self.face_lock:
                if self.face_model is not None:
                    self.face_model.to("cpu")
        except Exception:
            pass

    def _maybe_back_to_gpu(self) -> None:
        """A CPU fallback is temporary: after GPU_RETRY_S try the GPU again."""
        if (self.cpu_since is None or not torch.cuda.is_available()
                or time.monotonic() - self.cpu_since < GPU_RETRY_S):
            return
        try:
            torch.cuda.empty_cache()
            if self.tracker is not None:
                self.tracker.to("cuda:0")
            with self.face_lock:
                if self.face_model is not None:
                    self.face_model.to("cuda:0")
            self.device = 0
            self.cpu_since = None
            log.info("Person/face models back on the GPU after CPU fallback (%s)", self.cpu_reason)
        except Exception as exc:
            self.cpu_since = time.monotonic()
            log.warning("GPU still unusable, staying on CPU: %s", exc)

    def analyze_frame(
        self,
        frame_bgr: np.ndarray,
        drone_id: str = "DRONE_KL_001",
        save_crops: bool = True,
    ) -> dict:
        """
        Detect and track persons + detect faces (tile-based) on one frame.
        Thin wrapper over analyze_batch (upload / offline paths).
        """
        try:
            return self.analyze_batch({drone_id: frame_bgr}, save_crops)[drone_id]
        except RuntimeError as exc:     # unexpected CUDA failure on the upload path
            log.warning("Person frame inference error: %s", exc)
            return self._result(drone_id, [], [], 0.0, 0.0)

    def analyze_batch(
        self,
        frames: Dict[str, np.ndarray],
        save_crops: bool = True,
    ) -> Dict[str, dict]:
        """
        Newest frame of several drones -> {drone_id: result}.
        Person detection runs as ONE batched GPU call with a separate tracker per
        drone; face tiling + linking run per drone afterwards.
        Persons get stable tracker_id across frames of the same drone.
        """
        if not frames:
            return {}
        self._maybe_back_to_gpu()
        t0 = time.perf_counter()
        tracked: Dict[str, object] = {}
        face_jobs: Dict[str, Future] = {}
        if FACE_PARALLEL and self.face_model and self.device != "cpu":
            face_jobs = {d: self._face_pool.submit(self._faces_timed, fr) for d, fr in frames.items()}

        # ── Person detection + tracking (batched across drones) ─────────────
        if self.person_model and self.tracker:
            try:
                tracked = self.tracker.track_batch(frames, **self._track_kwargs())
            except Exception as exc:
                if "CUDA" in str(exc) and self.device != "cpu":
                    self._fall_back_to_cpu(exc)
                    # Retry on CPU
                    try:
                        tracked = self.tracker.track_batch(frames, **self._track_kwargs())
                    except Exception as retry_exc:
                        log.warning("Person tracking CPU retry also failed: %s", retry_exc)
                else:
                    log.warning("Person tracking error: %s", exc)
        person_ms = (time.perf_counter() - t0) * 1000 / max(1, len(frames))

        return {
            d: self._postprocess(d, frame, tracked.get(d), save_crops, person_ms, face_jobs.get(d))
            for d, frame in frames.items()
        }

    def _result(self, drone_id, persons, faces, elapsed_ms, model_ms) -> dict:
        return {
            "drone_id":     drone_id,
            "ts":           datetime.now(timezone.utc).isoformat(),
            "persons":      persons,
            "faces":        faces,
            "person_count": len(persons),
            "face_count":   len(faces),
            "inference_ms": round(elapsed_ms, 1),
            "model_ms":     round(model_ms, 1),
        }

    def _postprocess(self, drone_id, frame_bgr, result, save_crops, person_ms,
                     face_job: Optional[Future] = None) -> dict:
        """Per-drone part: person boxes from the batched result, face tiles, linking."""
        t0 = time.perf_counter()
        self._frame_count += 1
        persons: List[dict] = []
        faces:   List[dict] = []
        _untracked_counter = 0
        face_ms = 0.0

        # ── Person boxes (tracked) ───────────────────────────────────────────
        try:
            if result is not None and result.boxes:
                for box in result.boxes:
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
            log.warning("Person tracking error: %s", exc)

        # ── Face detection (tile-based) ───────────────────────────────────────
        if self.face_model:
            try:
                # started with the person call (parallel), else run here
                face_boxes, face_ms = face_job.result() if face_job else self._faces_timed(frame_bgr)
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
                    self._fall_back_to_cpu(exc)
                else:
                    log.warning("Face detection error: %s", exc)

        elapsed_ms = person_ms + (time.perf_counter() - t0) * 1000
        # model_ms: this frame's share of the batched person call + its face tiles
        return self._result(drone_id, persons, faces, elapsed_ms, person_ms + face_ms)
