"""
Vehicle circling and repetitive patrol pattern detector.
Detects vehicles repeatedly circling the same area or patrolling a perimeter.
"""

from typing import Dict, List, Optional, Tuple
import numpy as np

from tracking.track_state import EventType, TrackState, VehicleAction


class VehicleCirclingDetector:
    """
    Monitors spatial trajectory history and cell revisitations to detect
    vehicles looping, circling, or patrolling an area.
    """

    def __init__(
        self,
        grid_size_px: float = 120.0,
        min_revisits: int = 3,
        window_seconds: float = 120.0,
        min_speed_px: float = 15.0,
    ):
        self.grid_size_px = grid_size_px
        self.min_revisits = min_revisits
        self.window_seconds = window_seconds
        self.min_speed_px = min_speed_px

        # track_id -> List of (cell_x, cell_y, timestamp)
        self.vehicle_cell_visits: Dict[int, List[Tuple[int, int, float]]] = {}

    def analyze(
        self,
        vehicle: TrackState,
        timestamp: float,
    ) -> bool:
        """
        Analyzes vehicle trajectory for circling behavior.
        Updates vehicle active_events and returns True if circling is detected.
        """
        if not vehicle.is_vehicle or vehicle.speed < self.min_speed_px:
            return False

        tid = vehicle.track_id
        pos = vehicle.bottom_center
        cell_x = int(pos[0] // self.grid_size_px)
        cell_y = int(pos[1] // self.grid_size_px)

        if tid not in self.vehicle_cell_visits:
            self.vehicle_cell_visits[tid] = []

        visits = self.vehicle_cell_visits[tid]

        # Only append if entering a new cell or sufficient time elapsed
        if not visits or (visits[-1][0] != cell_x or visits[-1][1] != cell_y):
            visits.append((cell_x, cell_y, timestamp))

        # Trim to window
        cutoff = timestamp - self.window_seconds
        visits = [v for v in visits if v[2] >= cutoff]
        self.vehicle_cell_visits[tid] = visits

        if len(visits) < 6:
            return False

        # Count revisitations across distinct cells
        cell_counts: Dict[Tuple[int, int], int] = {}
        for vx, vy, _ in visits:
            cell_key = (vx, vy)
            cell_counts[cell_key] = cell_counts.get(cell_key, 0) + 1

        # Check if any cell has been visited at least min_revisits times
        max_revisits = max(cell_counts.values()) if cell_counts else 0

        history = vehicle.ground_history if vehicle.ground_history else vehicle.history
        if len(history) >= 20 and max_revisits >= self.min_revisits:
            xs = [p[0] for p in history]
            ys = [p[1] for p in history]
            spatial_extent = float(np.hypot(max(xs) - min(xs), max(ys) - min(ys)))
            path_len = sum(
                np.hypot(history[i][0] - history[i - 1][0], history[i][1] - history[i - 1][1])
                for i in range(1, len(history))
            )

            if spatial_extent > 0 and path_len > 3.0 * spatial_extent:
                if EventType.VEHICLE_CIRCLING not in vehicle.active_events:
                    vehicle.active_events.append(EventType.VEHICLE_CIRCLING)
                return True

        if EventType.VEHICLE_CIRCLING in vehicle.active_events and max_revisits < 2:
            vehicle.active_events.remove(EventType.VEHICLE_CIRCLING)

        return False
