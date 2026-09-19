"""
Trajectory analysis module for drone surveillance.
Computes ground-compensated kinematics, direction reversals, repetitive paths,
and crowd-flow directional anomalies.
"""

from typing import Dict, List, Optional, Tuple
import numpy as np

from tracking.track_state import EventType, TrackState


class TrajectoryAnalyzer:
    """
    Analyzes temporal trajectory points to calculate speed, acceleration,
    curvature, direction reversals, and collective crowd flow vectors.
    """

    def __init__(
        self,
        velocity_window: int = 3,
        reversal_angle_thresh_deg: float = 120.0,
        reversal_window_sec: float = 8.0,
    ):

        self.velocity_window = velocity_window
        self.reversal_angle_thresh_deg = reversal_angle_thresh_deg
        self.reversal_window_sec = reversal_window_sec
        # Historical heading history per track: track_id -> [(heading_deg, timestamp), ...]
        self.heading_history: Dict[int, List[Tuple[float, float]]] = {}
        # Recorded reversal timestamps per track: track_id -> [timestamp, ...]
        self.reversals: Dict[int, List[float]] = {}

    def update_kinematics(
        self,
        track: TrackState,
        timestamp: float,
        fps: float = 30.0,
    ):
        """
        Updates velocity, speed, acceleration, and heading for a single track
        using ground-compensated trajectory history if available, else image history.
        """
        history = track.ground_history if track.ground_history else track.history
        if len(history) < 2:
            track.speed = 0.0
            track.velocity = (0.0, 0.0)
            track.acceleration = 0.0
            track.dwell_time = max(0.0, timestamp - track.first_seen_time)
            return

        # Dwell time
        track.dwell_time = max(0.0, timestamp - track.first_seen_time)

        # Use recent window to smooth derivative calculations
        win = min(len(history), self.velocity_window)
        p_curr = history[-1]
        p_prev = history[-win]

        dt = max(1e-3, p_curr[2] - p_prev[2])
        dx = p_curr[0] - p_prev[0]
        dy = p_curr[1] - p_prev[1]

        vx = dx / dt
        vy = dy / dt
        curr_speed = float(np.hypot(vx, vy))

        # Acceleration: change in speed over recent window
        if hasattr(track, "speed"):
            prev_speed = track.speed
            track.acceleration = float((curr_speed - prev_speed) / dt)
        else:
            track.acceleration = 0.0

        track.velocity = (float(vx), float(vy))
        track.speed = curr_speed

        # Heading in degrees [0, 360)
        if curr_speed > 5.0:  # Only compute heading if moving above noise floor
            heading = float(np.degrees(np.arctan2(vy, vx))) % 360.0
            track.heading_deg = heading
            self._record_heading(track.track_id, heading, timestamp)
            self._check_direction_reversal(track, timestamp)

    def _record_heading(self, track_id: int, heading: float, timestamp: float):
        if track_id not in self.heading_history:
            self.heading_history[track_id] = []
        headings = self.heading_history[track_id]
        headings.append((heading, timestamp))
        # Keep recent history
        self.heading_history[track_id] = [
            (h, t) for h, t in headings if timestamp - t <= self.reversal_window_sec
        ]

    def _check_direction_reversal(self, track: TrackState, timestamp: float):
        headings = self.heading_history.get(track.track_id, [])
        if len(headings) < 2:
            return

        curr_h = headings[-1][0]
        prev_h = headings[0][0]

        # Angular difference in [0, 180]
        diff = abs(curr_h - prev_h)
        if diff > 180.0:
            diff = 360.0 - diff

        if diff >= self.reversal_angle_thresh_deg:
            # Record reversal event
            if track.track_id not in self.reversals:
                self.reversals[track.track_id] = []
            revs = self.reversals[track.track_id]
            # Debounce reversals (at least 1.0s apart)
            if not revs or (timestamp - revs[-1] > 1.0):
                revs.append(timestamp)
                self.reversals[track.track_id] = [
                    t for t in revs if timestamp - t <= self.reversal_window_sec
                ]

        track.direction_reversals = len(self.reversals.get(track.track_id, []))

    def evaluate_crowd_flow(
        self,
        persons: List[TrackState],
        timestamp: float,
    ) -> Optional[float]:
        """
        Calculates dominant crowd heading vector and identifies individuals
        moving against the prevailing flow (OPPOSING_CROWD_FLOW).
        Returns dominant crowd heading in degrees or None if no collective motion.
        """
        # Collect headings of all actively moving pedestrians
        moving_persons = [p for p in persons if p.speed > 15.0]
        if len(moving_persons) < 4:
            for p in persons:
                p.opposing_crowd_flow = False
            return None

        # Compute mean circular direction vector
        angles_rad = [np.radians(p.heading_deg) for p in moving_persons]
        mean_sin = np.mean(np.sin(angles_rad))
        mean_cos = np.mean(np.cos(angles_rad))
        r_length = float(np.hypot(mean_sin, mean_cos))

        # Only evaluate if crowd flow exhibits directional coherence
        if r_length < 0.45:
            for p in persons:
                p.opposing_crowd_flow = False
            return None

        dominant_heading_deg = float(np.degrees(np.arctan2(mean_sin, mean_cos))) % 360.0

        for p in moving_persons:
            diff = abs(p.heading_deg - dominant_heading_deg)
            if diff > 180.0:
                diff = 360.0 - diff
            if diff >= 135.0:  # Moving counter to dominant flow
                p.opposing_crowd_flow = True
                if EventType.OPPOSING_CROWD_FLOW not in p.active_events:
                    p.active_events.append(EventType.OPPOSING_CROWD_FLOW)
            else:
                p.opposing_crowd_flow = False
                if EventType.OPPOSING_CROWD_FLOW in p.active_events:
                    p.active_events.remove(EventType.OPPOSING_CROWD_FLOW)

        return dominant_heading_deg

    def is_repetitive_trajectory(self, track: TrackState) -> bool:
        """
        Detects repetitive A -> B -> A -> B oscillatory trajectories within a limited spatial footprint.
        """
        history = track.ground_history if track.ground_history else track.history
        if len(history) < 30:
            return False

        # Spatial bounding box of trajectory
        xs = [p[0] for p in history]
        ys = [p[1] for p in history]
        span_x = max(xs) - min(xs)
        span_y = max(ys) - min(ys)
        spatial_extent = np.hypot(span_x, span_y)

        # Total cumulative path length traversed
        path_length = sum(
            np.hypot(history[i][0] - history[i - 1][0], history[i][1] - history[i - 1][1])
            for i in range(1, len(history))
        )

        # Repetitive if path length is high, but spatial extent is constrained and reversals occur
        if spatial_extent > 0 and path_length > 3.5 * spatial_extent and track.direction_reversals >= 3:
            return True
        return False
