"""
Ground-relative stationarity analysis for drone surveillance.
Evaluates whether a tracked object is genuinely stationary on the ground
after compensating for drone flight and camera ego-motion.
"""

from typing import List, Tuple
import numpy as np

from tracking.track_state import TrackState


class GroundStationarityEvaluator:
    """
    Evaluates ground-relative motion, speed, and spatial variance over a temporal sliding window.
    """

    def __init__(
        self,
        max_ground_speed_px: float = 8.0,       # Max pixels/sec in compensated space
        max_variance_threshold: float = 25.0,     # Max pixel variance
        stationary_min_seconds: float = 1.0,     # Min seconds to confirm initial stationarity
        window_seconds: float = 4.0,             # Analysis window
    ):
        self.max_ground_speed_px = max_ground_speed_px
        self.max_variance_threshold = max_variance_threshold
        self.stationary_min_seconds = stationary_min_seconds
        self.window_seconds = window_seconds

    def update_stationarity(
        self,
        obj: TrackState,
        timestamp: float,
        compensated_pos: Tuple[float, float],
        fps: float = 30.0,
    ):
        """
        Updates ground-relative motion metrics for a candidate object.
        """
        dt = 1.0 / max(fps, 1.0)
        gx, gy = compensated_pos

        # Append to ground history
        obj.ground_history.append((gx, gy, timestamp))

        # Trim to window
        cutoff = timestamp - self.window_seconds
        obj.ground_history = [p for p in obj.ground_history if p[2] >= cutoff]

        if len(obj.ground_history) < 3:
            obj.is_stationary = False
            return

        # Calculate ground displacement & velocity over window
        first_pt = obj.ground_history[0]
        last_pt = obj.ground_history[-1]
        elapsed = max(0.1, last_pt[2] - first_pt[2])

        total_disp = float(np.hypot(last_pt[0] - first_pt[0], last_pt[1] - first_pt[1]))
        ground_speed = total_disp / elapsed
        obj.ground_speed = ground_speed

        # Calculate spatial variance
        xs = [p[0] for p in obj.ground_history]
        ys = [p[1] for p in obj.ground_history]
        var_x = float(np.var(xs))
        var_y = float(np.var(ys))
        total_var = var_x + var_y
        obj.ground_variance = total_var

        # Stationarity decision
        if ground_speed <= self.max_ground_speed_px and total_var <= self.max_variance_threshold:
            obj.stationary_duration += dt
            if obj.stationary_duration >= self.stationary_min_seconds:
                obj.is_stationary = True
        else:
            # Object is moving on the ground
            obj.stationary_duration = max(0.0, obj.stationary_duration - (dt * 2.0))
            if obj.stationary_duration < self.stationary_min_seconds:
                obj.is_stationary = False

    def evaluate(self, obj: TrackState, timestamp: float, fps: float = 30.0):
        """
        Evaluate stationarity for an object using its ground history or bottom center coordinates.
        """
        if obj.ground_history:
            pos = (float(obj.ground_history[-1][0]), float(obj.ground_history[-1][1]))
        else:
            bc = obj.bottom_center
            pos = (float(bc[0]), float(bc[1]))
        return self.update_stationarity(obj, timestamp, pos, fps)

