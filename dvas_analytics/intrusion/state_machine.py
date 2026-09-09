# -*- coding: utf-8 -*-
"""state_machine.py - Geospatial Point-In-Polygon & Temporal Intrusion State Machine"""

from enum import Enum
import logging
import math
from typing import Dict, List, Optional, Tuple
import numpy as np

from intrusion.projection import wgs84_to_enu

logger = logging.getLogger(__name__)


class IntrusionState(str, Enum):
    """Temporal intrusion lifecycle states."""
    OUTSIDE = "OUTSIDE"
    ENTERING = "ENTERING"
    INSIDE = "INSIDE"
    DWELLING = "DWELLING"
    EXITING = "EXITING"
    EXITED = "EXITED"


def point_in_polygon_enu(
    point_enu: Tuple[float, float],
    polygon_enu: List[Tuple[float, float]],
    boundary_tolerance_m: float = 0.5,
) -> Tuple[bool, bool]:
    """Ray-casting point-in-polygon test in ENU meters."""
    x, y = point_enu
    n = len(polygon_enu)
    inside = False

    p1x, p1y = polygon_enu[0]
    for i in range(1, n + 1):
        p2x, p2y = polygon_enu[i % n]

        dx, dy = p2x - p1x, p2y - p1y
        if dx != 0 or dy != 0:
            t = max(0.0, min(1.0, ((x - p1x) * dx + (y - p1y) * dy) / (dx * dx + dy * dy)))
            nearest_x = p1x + t * dx
            nearest_y = p1y + t * dy
            dist = math.hypot(x - nearest_x, y - nearest_y)
            if dist <= boundary_tolerance_m:
                return True, True

        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    else:
                        xinters = p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y

    return inside, False


class TrackIntrusionState:
    """Tracks the temporal state history of a single vehicle/object Track ID."""

    def __init__(
        self,
        track_id: int,
        confirm_inside_frames: int = 3,
        confirm_outside_frames: int = 3,
        min_dwell_time_s: float = 3.0,
    ):
        self.track_id: int = track_id
        self.current_state: IntrusionState = IntrusionState.OUTSIDE
        self.consecutive_inside_count: int = 0
        self.consecutive_outside_count: int = 0

        self.confirm_inside_frames: int = confirm_inside_frames
        self.confirm_outside_frames: int = confirm_outside_frames
        self.min_dwell_time_s: float = min_dwell_time_s

        self.first_seen_timestamp_s: Optional[float] = None
        self.entry_timestamp_s: Optional[float] = None
        self.exit_timestamp_s: Optional[float] = None
        self.trajectory: List[dict] = []  # [{ts, lat, lon}]

    def update(
        self,
        is_inside: bool,
        is_boundary: bool,
        timestamp_s: float,
        frame_idx: int,
    ) -> IntrusionState:
        if self.first_seen_timestamp_s is None:
            self.first_seen_timestamp_s = timestamp_s

        if is_inside:
            self.consecutive_inside_count += 1
            self.consecutive_outside_count = 0
        else:
            self.consecutive_outside_count += 1
            self.consecutive_inside_count = 0

        prev_state = self.current_state

        if self.current_state in (IntrusionState.OUTSIDE, IntrusionState.EXITED):
            if self.consecutive_inside_count >= 1:
                self.current_state = IntrusionState.ENTERING
                if self.entry_timestamp_s is None:
                    self.entry_timestamp_s = timestamp_s

        elif self.current_state == IntrusionState.ENTERING:
            if self.consecutive_inside_count >= self.confirm_inside_frames:
                self.current_state = IntrusionState.INSIDE
            elif self.consecutive_outside_count >= self.confirm_outside_frames:
                self.current_state = IntrusionState.OUTSIDE
                self.entry_timestamp_s = None

        elif self.current_state == IntrusionState.INSIDE:
            if self.consecutive_outside_count >= self.confirm_outside_frames:
                self.current_state = IntrusionState.EXITING
            elif self.entry_timestamp_s is not None and (timestamp_s - self.entry_timestamp_s) >= self.min_dwell_time_s:
                self.current_state = IntrusionState.DWELLING

        elif self.current_state == IntrusionState.DWELLING:
            if self.consecutive_outside_count >= self.confirm_outside_frames:
                self.current_state = IntrusionState.EXITING

        elif self.current_state == IntrusionState.EXITING:
            if self.consecutive_outside_count >= self.confirm_outside_frames:
                self.current_state = IntrusionState.EXITED
                self.exit_timestamp_s = timestamp_s
            elif self.consecutive_inside_count >= self.confirm_inside_frames:
                self.current_state = IntrusionState.INSIDE

        return self.current_state

    @property
    def dwell_duration_s(self) -> float:
        if self.entry_timestamp_s is None:
            return 0.0
        end = self.exit_timestamp_s if self.exit_timestamp_s else (self.first_seen_timestamp_s or 0.0)
        # Use latest trajectory timestamp if available
        if self.trajectory:
            end = max(end, self.trajectory[-1].get("ts", end))
        return max(0.0, end - self.entry_timestamp_s)


class IntrusionStateMachine:
    """Manages PIP evaluation and per-track state machines across the mission."""

    def __init__(
        self,
        polygon_coords_lonlat: List[Tuple[float, float]],
        confirm_inside_frames: int = 3,
        confirm_outside_frames: int = 3,
        min_dwell_time_s: float = 3.0,
    ):
        self.confirm_inside_frames = confirm_inside_frames
        self.confirm_outside_frames = confirm_outside_frames
        self.min_dwell_time_s = min_dwell_time_s

        # Use first vertex as ENU reference origin
        ref_lon, ref_lat = polygon_coords_lonlat[0]
        self.ref_lat0 = ref_lat
        self.ref_lon0 = ref_lon

        self.polygon_enu: List[Tuple[float, float]] = []
        for lon, lat in polygon_coords_lonlat:
            e, n, _ = wgs84_to_enu(lat, lon, self.ref_lat0, self.ref_lon0)
            self.polygon_enu.append((e, n))

        self.track_states: Dict[int, TrackIntrusionState] = {}

    def is_point_inside(self, lat: float, lon: float) -> Tuple[bool, bool]:
        e, n, _ = wgs84_to_enu(lat, lon, self.ref_lat0, self.ref_lon0)
        return point_in_polygon_enu((e, n), self.polygon_enu)

    def process_track_observation(
        self,
        track_id: int,
        lat: float,
        lon: float,
        timestamp_s: float,
        frame_idx: int,
    ) -> Tuple[IntrusionState, bool, bool]:
        is_inside, is_boundary = self.is_point_inside(lat, lon)

        if track_id not in self.track_states:
            self.track_states[track_id] = TrackIntrusionState(
                track_id=track_id,
                confirm_inside_frames=self.confirm_inside_frames,
                confirm_outside_frames=self.confirm_outside_frames,
                min_dwell_time_s=self.min_dwell_time_s,
            )

        track_state = self.track_states[track_id]
        prev_state = track_state.current_state
        state = track_state.update(
            is_inside=is_inside,
            is_boundary=is_boundary,
            timestamp_s=timestamp_s,
            frame_idx=frame_idx,
        )

        # Record trajectory waypoint
        track_state.trajectory.append({
            "ts": timestamp_s,
            "lat": lat,
            "lon": lon,
            "frame_idx": frame_idx,
            "state": state.value,
        })

        return state, is_inside, is_boundary

    def get_active_intrusion_count(self) -> int:
        return sum(
            1 for ts in self.track_states.values()
            if ts.current_state in (
                IntrusionState.ENTERING,
                IntrusionState.INSIDE,
                IntrusionState.DWELLING,
            )
        )
