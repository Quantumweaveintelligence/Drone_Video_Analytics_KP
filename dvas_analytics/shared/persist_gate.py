# -*- coding: utf-8 -*-
"""
DVAS Analytics — which routine per-track writes happen on a frame (plan B1, §3 rules).

Detection is never throttled; this only decides how often a track's *routine* copies are
written. Events, alerts, plate reads, face crops / embeddings and alert-moment frames are
never passed through here — services write those immediately, as before.

Per track (drone id + tracker id):
  * track row (`upsert_track`): first sighting, then at most every PERSIST_TRACK_EVERY_S,
    and once more when the track ends (exact last_seen);
  * crop: first sighting; a clearly better one (larger, sharper, not cut by the frame edge);
    a periodic trail crop every PERSIST_CROP_EVERY_S; the newest crop when the track ends;
    always when the caller says it is part of an alert;
  * attributes (`vehicle_attributes`): first sighting, when type / colour / speed bucket
    change, else every PERSIST_ATTR_EVERY_S.

A value of 0 writes on every frame (the behaviour before this change).
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple


def _env_s(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except ValueError:
        return default


TRACK_EVERY_S = _env_s("PERSIST_TRACK_EVERY_S", 2.0)
CROP_EVERY_S = _env_s("PERSIST_CROP_EVERY_S", 1.0)
ATTR_EVERY_S = _env_s("PERSIST_ATTR_EVERY_S", 5.0)
# A track not seen for this long has ended (its final row and crop are written)
TRACK_END_S = _env_s("PERSIST_TRACK_END_S", 3.0)
BETTER = 1.25            # a crop must score this much higher than the best one to replace it


@dataclass
class TrackState:
    track_id: Optional[str] = None      # DB uuid once the row exists
    row_at: Optional[float] = None
    crop_at: Optional[float] = None     # last uploaded crop (any reason)
    best: float = 0.0                   # score of the best uploaded crop
    attr_at: Optional[float] = None
    attr_sig: Any = None
    seen_at: float = 0.0
    last_ts: Any = None                 # wall-clock datetime of the last sighting
    mission_id: Optional[str] = None
    image_key: Optional[str] = None     # newest uploaded crop object
    pending: Optional[bytes] = None     # newest crop not uploaded (the final crop if it ends now)


def crop_score(frame, bbox) -> float:
    """Area × sharpness, halved when the box touches the frame edge (cut-off vehicle)."""
    import cv2  # noqa: PLC0415
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = (int(round(v)) for v in bbox[:4])
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    crop = frame[y1:y2, x1:x2]
    step = max(1, max(crop.shape[:2]) // 128)          # sharpness on ≤ 128 px: cheap
    gray = cv2.cvtColor(crop[::step, ::step], cv2.COLOR_BGR2GRAY)
    sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    inside = x1 > 2 and y1 > 2 and x2 < w - 2 and y2 < h - 2
    return (x2 - x1) * (y2 - y1) * (1.0 + sharp ** 0.5) * (1.0 if inside else 0.5)


class TrackGate:
    def __init__(self, track_every_s: float = None, crop_every_s: float = None,
                 attr_every_s: float = None, end_s: float = None,
                 clock: Callable[[], float] = time.monotonic):
        self.track_every_s = TRACK_EVERY_S if track_every_s is None else track_every_s
        self.crop_every_s = CROP_EVERY_S if crop_every_s is None else crop_every_s
        self.attr_every_s = ATTR_EVERY_S if attr_every_s is None else attr_every_s
        self.end_s = TRACK_END_S if end_s is None else end_s
        self._clock = clock
        self._tracks: Dict[Tuple[str, Any], TrackState] = {}
        self._retired: List[Tuple[Tuple[str, Any], TrackState]] = []   # tracker id reused by a new mission

    def seen(self, drone_id: str, tracker_id, ts=None, mission_id: Optional[str] = None) -> TrackState:
        key = (drone_id, tracker_id)
        st = self._tracks.get(key)
        if st is not None and mission_id and st.mission_id and mission_id != st.mission_id:
            self._retired.append((key, st))
            st = None
        if st is None:
            st = self._tracks[key] = TrackState(mission_id=mission_id)
        st.seen_at = self._clock()
        st.last_ts = ts
        return st

    def want_row(self, st: TrackState) -> bool:
        now = self._clock()
        if st.track_id is None or st.row_at is None or now - st.row_at >= self.track_every_s:
            st.row_at = now
            return True
        return False

    def want_crop(self, st: TrackState, score: float, alert: bool = False) -> Optional[str]:
        """Why this crop is uploaded ("first", "alert", "better", "periodic"), or None."""
        now = self._clock()
        if st.crop_at is None:
            why = "first"
        elif alert:
            why = "alert"
        elif score > st.best * BETTER:
            why = "better"
        elif now - st.crop_at >= self.crop_every_s:
            why = "periodic"
        else:
            return None
        st.crop_at = now
        st.best = max(st.best, score)
        return why

    def want_attrs(self, st: TrackState, signature) -> bool:
        now = self._clock()
        if (st.attr_at is None or signature != st.attr_sig
                or now - st.attr_at >= self.attr_every_s):
            st.attr_at, st.attr_sig = now, signature
            return True
        return False

    def ended(self, drone_id: Optional[str] = None, force: bool = False) -> List[Tuple[Tuple[str, Any], TrackState]]:
        """Tracks not seen for end_s (all of a drone's when force), removed from the gate."""
        now = self._clock()
        out = [r for r in self._retired if drone_id is None or r[0][0] == drone_id]
        self._retired = [r for r in self._retired if r not in out]
        for key, st in list(self._tracks.items()):
            if drone_id is not None and key[0] != drone_id:
                continue
            if force or now - st.seen_at >= self.end_s:
                out.append((key, self._tracks.pop(key)))
        return out

    def __len__(self) -> int:
        return len(self._tracks)


def speed_bucket(kph) -> Optional[int]:
    """Speed in 10 km/h steps, for "attributes changed"."""
    try:
        return None if kph is None else int(float(kph) // 10)
    except (TypeError, ValueError):
        return None
