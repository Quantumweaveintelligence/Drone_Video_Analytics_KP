# -*- coding: utf-8 -*-
"""
DVAS Analytics — cross-drone batched detection with per-drone tracking
(RunPod plan 9.10).

`model.track(frame, persist=True)` keeps ONE tracker inside the model, so when
several drones feed the same model their detections were matched against each
other's tracks. BatchTracker runs `model.predict` on the newest frame of every
drone as one batch (chunks of GpuProfile.max_batch) and then updates a
separate ByteTrack/BoT-SORT instance per drone — the same steps Ultralytics'
own track callback performs, so results keep `boxes.id` like `.track()`.
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

from shared import gpu_profile
from shared.frame_sampler import chunks, is_late

log = logging.getLogger(__name__)

# v2 R6.7: the per-drone tracker updates of a round run in parallel threads (each drone has
# its own tracker; BoT-SORT's optical flow runs in OpenCV, which releases the GIL). The
# same updates as before, in the same per-drone order; only drones overlap each other.
# 1 = one drone after another (the previous behaviour).
try:
    TRACK_THREADS = max(1, int(os.getenv("DVAS_TRACK_THREADS", "8")))
except ValueError:
    TRACK_THREADS = 8
_track_pool: Optional[ThreadPoolExecutor] = None
_ID_LOCK = threading.Lock()
_ids_locked = False


def _lock_track_ids() -> None:
    """Ultralytics hands out track ids from a process-wide counter (`_count += 1; return
    _count`); two drones updating at once could get the same id. Make it atomic, once."""
    global _ids_locked
    if _ids_locked:
        return
    from ultralytics.trackers.basetrack import BaseTrack  # noqa: PLC0415

    def next_id() -> int:
        with _ID_LOCK:
            BaseTrack._count += 1
            return BaseTrack._count
    BaseTrack.next_id = staticmethod(next_id)
    _ids_locked = True


def _pool() -> ThreadPoolExecutor:
    global _track_pool
    if _track_pool is None:
        _track_pool = ThreadPoolExecutor(TRACK_THREADS, thread_name_prefix="tracker")
    return _track_pool


def _make_tracker(tracker_yaml: str):
    from ultralytics.trackers.track import TRACKER_MAP  # noqa: PLC0415
    from ultralytics.utils import IterableSimpleNamespace  # noqa: PLC0415
    from ultralytics.utils.checks import check_yaml  # noqa: PLC0415
    try:
        from ultralytics.utils import YAML  # noqa: PLC0415  (8.3.1xx+)
        cfg_dict = YAML.load(check_yaml(tracker_yaml))
    except ImportError:
        from ultralytics.utils import yaml_load  # noqa: PLC0415  (≤ 8.3.0xx)
        cfg_dict = yaml_load(check_yaml(tracker_yaml))
    cfg = IterableSimpleNamespace(**cfg_dict)
    if cfg.tracker_type not in TRACKER_MAP:
        raise ValueError(f"Unsupported tracker {cfg.tracker_type}")
    if cfg.tracker_type == "botsort" and getattr(cfg, "with_reid", False):
        cfg.with_reid = False   # ReID needs predictor internals; ByteTrack-style matching only
    from ultralytics.trackers.basetrack import BaseTrack  # noqa: PLC0415
    # A new tracker resets the process-wide id counter; keep it so ids stay unique
    # across drones (analyzers key per-track state by tracker id)
    saved = BaseTrack._count
    tracker = TRACKER_MAP[cfg.tracker_type](args=cfg, frame_rate=30)
    BaseTrack._count = max(BaseTrack._count, saved)
    return tracker


_UPDATE_TAKES_FEATS: Dict[type, bool] = {}

# BoT-SORT global motion compensation runs optical flow on the CPU on every frame
# (≈137 ms per 4K frame at Ultralytics' default downscale 2). Size it to ~GMC_TARGET_PX
# wide instead: 4K → downscale 4, 1080p → 2 (unchanged). 0 keeps Ultralytics' default.
try:
    GMC_TARGET_PX = max(0, int(os.getenv("DVAS_GMC_TARGET_PX", "960")))
except ValueError:
    GMC_TARGET_PX = 960

# Every BatchTracker in this process (for idle resets from the sampler, plan A3)
import weakref  # noqa: E402
_INSTANCES: "weakref.WeakSet" = weakref.WeakSet()


def reset_everywhere(key: str) -> None:
    for t in list(_INSTANCES):
        t.reset(key)
    TIMINGS.pop(key, None)


# Per-process stage timings for the sampler's stats (plan A4): {key: {"detect": ms, "track": ms}}
TIMINGS: Dict[str, Dict[str, float]] = {}


def stage_ms(key: str) -> Dict[str, float]:
    return dict(TIMINGS.get(key, {}))


def _size_gmc(tracker, img) -> None:
    gmc = getattr(tracker, "gmc", None)
    if gmc is None or not GMC_TARGET_PX or getattr(tracker, "_dvas_gmc_sized", False):
        return
    width = img.shape[1] if hasattr(img, "shape") else 0
    if width:
        gmc.downscale = max(2, math.ceil(width / GMC_TARGET_PX))
    tracker._dvas_gmc_sized = True


def _match_without_update(tracker, result):
    """Late frame (plan D1): give detections the ids of the drone's current tracks by IoU,
    WITHOUT stepping the tracker (time must not go backwards). Unmatched detections are
    dropped from the tracked result, as a new track would only be confirmed by later frames."""
    import numpy as np  # noqa: PLC0415
    import torch  # noqa: PLC0415
    tracks = [t for t in getattr(tracker, "tracked_stracks", []) if getattr(t, "is_activated", True)]
    boxes = result.boxes
    if not tracks or boxes is None or len(boxes) == 0:
        return result[[]] if boxes is not None else result
    det = boxes.xyxy.cpu().numpy()
    trk = np.array([t.xyxy for t in tracks], dtype=float)
    ix1 = np.maximum(det[:, None, 0], trk[None, :, 0]); iy1 = np.maximum(det[:, None, 1], trk[None, :, 1])
    ix2 = np.minimum(det[:, None, 2], trk[None, :, 2]); iy2 = np.minimum(det[:, None, 3], trk[None, :, 3])
    inter = np.clip(ix2 - ix1, 0, None) * np.clip(iy2 - iy1, 0, None)
    area_d = (det[:, 2] - det[:, 0]) * (det[:, 3] - det[:, 1])
    area_t = (trk[:, 2] - trk[:, 0]) * (trk[:, 3] - trk[:, 1])
    iou = inter / (area_d[:, None] + area_t[None, :] - inter + 1e-6)
    rows, used = [], set()
    for di in np.argsort(-boxes.conf.cpu().numpy()):
        ti = int(np.argmax(iou[di]))
        if iou[di, ti] >= 0.3 and ti not in used:
            used.add(ti)
            rows.append((int(di), tracks[ti].track_id))
    if not rows:
        return result[[]]
    keep = [d for d, _ in rows]
    out = result[keep]
    data = out.boxes.data.cpu()
    ids = torch.tensor([[float(tid)] for _, tid in rows], dtype=data.dtype)
    out.update(boxes=torch.cat([data[:, :4], ids, data[:, 4:]], dim=1))
    return out


def _update(tracker, result):
    """Apply one tracker step to an Ultralytics Results (in place, like track())."""
    import torch  # noqa: PLC0415
    _size_gmc(tracker, result.orig_img)
    det = result.boxes.cpu().numpy()
    if _UPDATE_TAKES_FEATS.get(type(tracker)) is None:
        import inspect  # noqa: PLC0415
        _UPDATE_TAKES_FEATS[type(tracker)] = "feats" in inspect.signature(tracker.update).parameters
    if _UPDATE_TAKES_FEATS[type(tracker)]:
        tracks = tracker.update(det, result.orig_img, None)
    else:                                   # older Ultralytics: update(results, img)
        tracks = tracker.update(det, result.orig_img)
    if len(tracks) == 0:
        # Same as Ultralytics: no confirmed tracks → boxes stay but carry no id
        return result
    idx = tracks[:, -1].astype(int)
    result = result[idx]
    result.update(boxes=torch.as_tensor(tracks[:, :-1]))
    return result


class BatchTracker:
    """Batched predict + one tracker per drone for a shared Ultralytics model."""

    def __init__(self, model, tracker: str = "botsort.yaml"):   # Ultralytics .track() default
        self.model = model
        self.tracker_yaml = tracker
        self._trackers: Dict[str, object] = {}
        # Ultralytics predictors are not thread-safe; analyzers call from to_thread
        self._lock = threading.Lock()
        self._last_device: Optional[str] = None
        _INSTANCES.add(self)

    def _model_device(self) -> str:
        try:
            return next(self.model.model.parameters()).device.type
        except Exception:
            return "unknown"

    def to(self, device) -> None:
        """Move the model between devices without racing a batch in flight."""
        with self._lock:
            self.model.to(device)

    def _tracker_for(self, drone_id: str):
        t = self._trackers.get(drone_id)
        if t is None:
            t = self._trackers[drone_id] = _make_tracker(self.tracker_yaml)
        return t

    def reset(self, drone_id: Optional[str] = None) -> None:
        with self._lock:
            if drone_id is None:
                self._trackers.clear()
            else:
                self._trackers.pop(drone_id, None)

    def predict_batch(self, frames: Dict[str, object], **kw) -> Dict[str, object]:
        """{drone: frame} → {drone: Results} without tracking (one GPU batch per chunk)."""
        if not frames:
            return {}
        args = {**gpu_profile.predict_kwargs(), **kw}
        # The device this call runs on: the explicit argument, else where the weights are
        # (before the first call Ultralytics still has them on the CPU, so assume the profile)
        if "device" in args and args["device"] is not None:
            device = "cpu" if str(args["device"]).lower() == "cpu" else "cuda"
        else:
            device = self._model_device()
            if device == "cpu" and self._last_device is None and gpu_profile.detect().available:
                device = "cuda"
        if device == "cpu":
            args["half"] = False            # FP16 is GPU-only (CPU fallback path)
        drones = list(frames)
        out: Dict[str, object] = {}
        # Ultralytics pads a mixed-shape batch to a square, which shifts boxes vs
        # single-frame inference; batch only frames of the same size together
        by_shape: Dict[tuple, List[str]] = {}
        for d in drones:
            by_shape.setdefault(getattr(frames[d], "shape", None), []).append(d)
        with self._lock:
            if device != self._last_device:
                # model.to() moved the weights: let Ultralytics build a predictor for the new device
                if self._last_device is not None:
                    self.model.predictor = None
                self._last_device = device
            for group in by_shape.values():
                for part in chunks(group, gpu_profile.detect().max_batch):
                    results = self.model.predict([frames[d] for d in part], **args)
                    out.update(zip(part, results))
        return out

    def track_batch(self, frames: Dict[str, object], **kw) -> Dict[str, object]:
        """{drone: frame} → {drone: Results with boxes.id}, tracked per drone.

        A frame flagged late by the sampler (is_late) is matched to the drone's current
        tracks without updating them, so the live tracker never steps backwards.
        """
        t0 = time.perf_counter()
        results = self.predict_batch(frames, **kw)
        detect_ms = (time.perf_counter() - t0) * 1000 / max(1, len(frames))
        with self._lock:
            trackers = {d: self._tracker_for(d) for d in results}   # created one at a time

            def step(d):
                t1 = time.perf_counter()
                r = results[d]
                res = _match_without_update(trackers[d], r) if is_late(frames[d]) else _update(trackers[d], r)
                TIMINGS[d] = {"detect": detect_ms, "track": (time.perf_counter() - t1) * 1000}
                return d, res

            if TRACK_THREADS > 1 and len(results) > 1:
                _lock_track_ids()
                return dict(_pool().map(step, list(results)))
            return dict(step(d) for d in results)

    def warmup(self, imgsz: int, batch_sizes=None, **kw) -> float:
        if os.getenv("DVAS_WARMUP", "1") == "0":
            return 0.0
        return self._warmup(imgsz, batch_sizes, **kw)

    def _warmup(self, imgsz: int, batch_sizes=None, **kw) -> float:
        """Run dummy 16:9 batches once so cuDNN autotuning happens before the first drone
        frame (plan B8). Returns seconds spent. Tracker state is not touched."""
        import numpy as np  # noqa: PLC0415
        if not gpu_profile.detect().available:
            return 0.0
        sizes = batch_sizes or sorted({1, gpu_profile.detect().max_batch})
        frame = np.zeros((1080, 1920, 3), np.uint8)      # every 16:9 input letterboxes alike
        t = time.perf_counter()
        for n in sizes:
            try:
                self.predict_batch({f"warmup{i}": frame for i in range(n)}, imgsz=imgsz, **kw)
            except Exception as exc:                      # never block a service start
                log.warning("warm-up at batch %d failed: %s", n, exc)
                break
        return time.perf_counter() - t

    def track_one(self, drone_id: str, frame, **kw):
        """Single-frame variant (upload/offline paths): returns a 1-item list like .track()."""
        return [self.track_batch({drone_id: frame}, **kw)[drone_id]]


def strip_track_kwargs(kw: dict) -> dict:
    """Drop .track()-only arguments before passing them to predict()."""
    return {k: v for k, v in kw.items() if k not in ("persist", "tracker")}


def batch_size() -> int:
    return gpu_profile.detect().max_batch


__all__: List[str] = ["BatchTracker", "strip_track_kwargs", "batch_size"]
