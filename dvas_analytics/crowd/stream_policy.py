# -*- coding: utf-8 -*-
"""
crowd/stream_policy.py — pure-Python helpers for the crowd stream loop
(RunPod plan F19/F20). Kept free of torch/cv2 imports so they can be unit-tested.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Callable, Dict, Optional

log = logging.getLogger("crowd-service")


class FrameRateCap:
    """Per-drone minimum interval between inferences (latest-frame-wins).

    max_fps <= 0 disables the cap. `mark(drone)` is called only after a frame
    was actually analysed, so failed/undecodable frames never consume the slot.
    """

    def __init__(self, max_fps: float, clock: Callable[[], float] = time.monotonic):
        self.min_interval_s = 1.0 / max_fps if max_fps > 0 else 0.0
        self._clock = clock
        self._last: Dict[str, float] = {}

    def due(self, drone_id: str) -> bool:
        last = self._last.get(drone_id)
        return last is None or self._clock() - last >= self.min_interval_s

    def mark(self, drone_id: str) -> None:
        self._last[drone_id] = self._clock()


def parse_mission_id(fields: dict) -> str:
    """mission_id from a Valkey frame entry's `metadata` JSON ('' when absent)."""
    raw_meta = fields.get("metadata", "")
    if not raw_meta:
        return ""
    if isinstance(raw_meta, bytes):
        raw_meta = raw_meta.decode("utf-8", errors="replace")
    try:
        meta = json.loads(raw_meta)
    except ValueError as exc:
        log.debug("Unparseable frame metadata: %s", exc)
        return ""
    if not isinstance(meta, dict):
        return ""
    return str(meta.get("mission_id") or "")


def is_cuda_failure(exc: BaseException, oom_type: Optional[type] = None) -> bool:
    """True for CUDA OOM / kernel errors that warrant a CPU failover."""
    if oom_type is not None and isinstance(exc, oom_type):
        return True
    return isinstance(exc, RuntimeError) and "CUDA" in str(exc)
