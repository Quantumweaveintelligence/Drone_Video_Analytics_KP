"""
Vehicle lane and traffic flow direction analysis module.
Calculates dominant traffic headings and flags wrong-way travel.
"""

from typing import List, Optional
import numpy as np

from tracking.track_state import EventType, TrackState, VehicleAction


class LaneTrafficAnalyzer:
    """
    Estimates dominant flow direction from moving traffic and detects
    wrong-way travel anomalies.
    """

    def __init__(self, wrong_way_angle_thresh: float = 135.0, enabled: bool = False):
        self.wrong_way_angle_thresh = wrong_way_angle_thresh
        self.enabled = enabled
        self.dominant_flow_heading: Optional[float] = None

    def evaluate_traffic_flow(
        self,
        vehicles: List[TrackState],
    ) -> Optional[float]:
        """
        Calculates prevailing traffic flow heading and flags wrong-way vehicles if enabled.
        """
        if not self.enabled:
            for v in vehicles:
                v.is_wrong_direction = False
                if EventType.WRONG_DIRECTION_VEHICLE in v.active_events:
                    v.active_events.remove(EventType.WRONG_DIRECTION_VEHICLE)
            return None

        moving_vehicles = [v for v in vehicles if v.speed > 15.0]
        if len(moving_vehicles) < 2:
            return self.dominant_flow_heading

        angles_rad = [np.radians(v.heading_deg) for v in moving_vehicles]
        mean_sin = np.mean(np.sin(angles_rad))
        mean_cos = np.mean(np.cos(angles_rad))
        r_length = float(np.hypot(mean_sin, mean_cos))

        # Check directional coherence
        if r_length >= 0.40:
            self.dominant_flow_heading = float(np.degrees(np.arctan2(mean_sin, mean_cos))) % 360.0

        if self.dominant_flow_heading is not None:
            for v in moving_vehicles:
                diff = abs(v.heading_deg - self.dominant_flow_heading)
                if diff > 180.0:
                    diff = 360.0 - diff

                if diff >= self.wrong_way_angle_thresh:
                    v.is_wrong_direction = True
                    v.vehicle_action = VehicleAction.WRONG_WAY
                    if EventType.WRONG_DIRECTION_VEHICLE not in v.active_events:
                        v.active_events.append(EventType.WRONG_DIRECTION_VEHICLE)
                else:
                    v.is_wrong_direction = False
                    if EventType.WRONG_DIRECTION_VEHICLE in v.active_events:
                        v.active_events.remove(EventType.WRONG_DIRECTION_VEHICLE)

        return self.dominant_flow_heading
