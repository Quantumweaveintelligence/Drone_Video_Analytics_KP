# -*- coding: utf-8 -*-
"""
Evidence block for confirmed events (evidence plan E1).

The analyzer that confirms an event knows exactly which frame and crop caused
it. It uploads those objects first (shared.minio_client.upload_object, which
hashes them), then writes the event with an `evidence` block in its attributes:

    {"schema": 1,
     "objects":   [{"role", "bucket", "key", "content_type", "sha256", "bytes", "kind"}],
     "frame":     {"frame_sequence", "capture_ts", "ingest_ts", "timestamp"},
     "geo":       {"lat", "lon", "alt_m", "heading_deg"},
     "detection": {"bbox_xyxy", "track_id", "class", "confidence"},
     "model":     {"service", "weights", "weights_sha256", "git_sha"}}

The dashboard copies this block into the evidence record; it never looks images
up afterwards. Pure functions here (no I/O except hashing a weights file once),
so they are unit-tested without MinIO or Postgres.
"""
import hashlib
import json
import logging
import os
import time
from functools import lru_cache
from typing import Any, Callable, Dict, Hashable, List, Optional

log = logging.getLogger(__name__)

EVIDENCE_SCHEMA = 1


def _num(value) -> Optional[float]:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def frame_context(fields: Dict[str, Any]) -> Dict[str, Any]:
    """Frame facts from a Valkey frame entry (fields published by the ingest).

    Returns {"mission_id", "frame": {...}, "geo": {...} or None}. Missing values are None.
    """
    meta: Dict[str, Any] = {}
    raw = fields.get("metadata", "")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                meta = parsed
        except ValueError:
            log.debug("Unparseable frame metadata")

    def pick(name):
        value = fields.get(name)
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        return value if value not in (None, "") else meta.get(name)

    seq = pick("frame_sequence")
    ingest_ms = _num(fields.get("ts"))
    frame = {
        "frame_sequence": int(seq) if str(seq or "").isdigit() else None,
        "capture_ts": _num(pick("capture_ts")),
        "ingest_ts": ingest_ms / 1000.0 if ingest_ms else None,
        "timestamp": pick("timestamp") or None,
    }
    lat, lon = _num(meta.get("latitude")), _num(meta.get("longitude"))
    geo = None
    if lat is not None and lon is not None and (lat, lon) != (0.0, 0.0):
        geo = {"lat": lat, "lon": lon, "alt_m": _num(meta.get("altitude_m")),
               "heading_deg": _num(meta.get("heading_deg"))}
    return {"mission_id": str(meta.get("mission_id") or "") or None, "frame": frame, "geo": geo}


@lru_cache(maxsize=32)
def _file_sha256(path: str) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def model_info(service: str, weights_path: str = "") -> Dict[str, Any]:
    """Which code and weights produced the event. Weights are hashed once per process."""
    return {
        "service": service,
        "weights": os.path.basename(weights_path) if weights_path else None,
        "weights_sha256": _file_sha256(weights_path) if weights_path else None,
        "git_sha": os.getenv("DVAS_GIT_SHA") or None,
    }


def bbox_xyxy(bbox) -> Optional[List[float]]:
    """Normalise {'x1','y1','x2','y2'} / [x1,y1,x2,y2] to a list of 4 floats."""
    if isinstance(bbox, dict):
        try:
            return [float(bbox[k]) for k in ("x1", "y1", "x2", "y2")]
        except (KeyError, TypeError, ValueError):
            return None
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        try:
            return [float(v) for v in bbox]
        except (TypeError, ValueError):
            return None
    return None


def evidence_block(
    objects: List[Optional[Dict[str, Any]]],
    ctx: Optional[Dict[str, Any]] = None,
    detection: Optional[Dict[str, Any]] = None,
    model: Optional[Dict[str, Any]] = None,
    geo: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the `evidence` attribute block.

    objects: upload_object() results (None entries = failed uploads, dropped).
             The first object is the primary one; give `role` explicitly to override.
    geo:     overrides the frame GPS (e.g. ground position of the target).
    """
    out_objects = []
    for i, obj in enumerate(o for o in objects if o):
        entry = {k: obj.get(k) for k in ("bucket", "key", "content_type", "sha256", "bytes", "kind")}
        entry["role"] = obj.get("role") or ("primary" if i == 0 else "secondary")
        out_objects.append(entry)
    ctx = ctx or {}
    return {
        "schema": EVIDENCE_SCHEMA,
        "objects": out_objects,
        "frame": ctx.get("frame") or {},
        "geo": geo or ctx.get("geo"),
        "detection": detection or {},
        "model": model or {},
    }


class EventGate:
    """At most one confirmed event per key per `cooldown_s` (B29: incidents, not detections)."""

    def __init__(self, cooldown_s: float, clock: Callable[[], float] = time.monotonic, max_keys: int = 10000):
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._last: Dict[Hashable, float] = {}
        self._max_keys = max_keys

    def allow(self, key: Hashable) -> bool:
        now = self._clock()
        last = self._last.get(key)
        if last is not None and now - last < self.cooldown_s:
            return False
        if len(self._last) >= self._max_keys:
            cutoff = now - self.cooldown_s
            self._last = {k: t for k, t in self._last.items() if t >= cutoff}
        self._last[key] = now
        return True


class ThresholdGate:
    """Crowd rule: fire when value >= threshold; re-arm only after it drops below
    `rearm_ratio` x threshold and `cooldown_s` has passed since the last event."""

    def __init__(self, cooldown_s: float = 60.0, rearm_ratio: float = 0.8,
                 clock: Callable[[], float] = time.monotonic):
        self.cooldown_s = cooldown_s
        self.rearm_ratio = rearm_ratio
        self._clock = clock
        self._armed: Dict[Hashable, bool] = {}
        self._last: Dict[Hashable, float] = {}

    def update(self, key: Hashable, value: float, threshold: float) -> bool:
        now = self._clock()
        armed = self._armed.get(key, True)
        if value < threshold * self.rearm_ratio:
            if now - self._last.get(key, float("-inf")) >= self.cooldown_s:
                self._armed[key] = True
            return False
        if value >= threshold and armed:
            self._armed[key] = False
            self._last[key] = now
            return True
        return False
