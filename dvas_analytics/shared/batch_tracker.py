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
import threading
from typing import Dict, List, Optional

from shared import gpu_profile
from shared.frame_sampler import chunks

log = logging.getLogger(__name__)


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


def _update(tracker, result):
    """Apply one tracker step to an Ultralytics Results (in place, like track())."""
    import torch  # noqa: PLC0415
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
        """{drone: frame} → {drone: Results with boxes.id}, tracked per drone."""
        results = self.predict_batch(frames, **kw)
        with self._lock:
            return {d: _update(self._tracker_for(d), r) for d, r in results.items()}

    def track_one(self, drone_id: str, frame, **kw):
        """Single-frame variant (upload/offline paths): returns a 1-item list like .track()."""
        return [self.track_batch({drone_id: frame}, **kw)[drone_id]]


def strip_track_kwargs(kw: dict) -> dict:
    """Drop .track()-only arguments before passing them to predict()."""
    return {k: v for k, v in kw.items() if k not in ("persist", "tracker")}


def batch_size() -> int:
    return gpu_profile.detect().max_batch


__all__: List[str] = ["BatchTracker", "strip_track_kwargs", "batch_size"]
