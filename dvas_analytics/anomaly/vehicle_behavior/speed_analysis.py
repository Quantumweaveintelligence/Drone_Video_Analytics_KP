"""
Vehicle speed and stopping behavior analysis module.
Evaluates abrupt stopping, stopping duration, and abnormal speed profiles.
"""

from typing import Dict, List, Optional
import numpy as np

from tracking.track_state import EventType, TrackState, VehicleAction


class VehicleSpeedAnalyzer:
    """
    Monitors vehicle speeds against scene distribution, detects sudden halts
    and tracks dwell/stopping durations.
    """

    def __init__(
        self,
        stopped_speed_thresh: float = 8.0,
        abrupt_stop_decel_thresh: float = -45.0,
    ):
        self.stopped_speed_thresh = stopped_speed_thresh
        self.abrupt_stop_decel_thresh = abrupt_stop_decel_thresh
        # Historical speed per vehicle: vehicle_id -> [speed, ...]
        self.speed_histories: Dict[int, List[float]] = {}

    def analyze(
        self,
        vehicles: List[TrackState],
        timestamp: float,
        fps: float = 30.0,
    ):
        """
        Analyzes stopping and speed anomalies for all active vehicles.
        """
        dt = 1.0 / max(fps, 1.0)
        if not vehicles:
            return

        moving_speeds = [v.speed for v in vehicles if v.speed > self.stopped_speed_thresh]
        median_speed = float(np.median(moving_speeds)) if moving_speeds else 50.0

        for v in vehicles:
            tid = v.track_id
            speed = v.speed
            accel = v.acceleration

            if tid not in self.speed_histories:
                self.speed_histories[tid] = []
            self.speed_histories[tid].append(speed)
            if len(self.speed_histories[tid]) > 30:
                self.speed_histories[tid].pop(0)

            # Stopping Duration
            if speed <= self.stopped_speed_thresh:
                v.vehicle_stop_duration += dt
                v.vehicle_action = VehicleAction.STOPPED
                if v.vehicle_stop_duration > 15.0:
                    v.vehicle_action = VehicleAction.PARKED
            else:
                v.vehicle_stop_duration = 0.0
                v.vehicle_action = VehicleAction.MOVING

            # Abrupt Stop Detection
            if len(self.speed_histories[tid]) >= 5:
                prev_speeds = self.speed_histories[tid][-5:-1]
                if prev_speeds and max(prev_speeds) > 35.0 and speed <= self.stopped_speed_thresh and accel <= self.abrupt_stop_decel_thresh:
                    if EventType.ABRUPT_VEHICLE_STOP not in v.active_events:
                        v.active_events.append(EventType.ABRUPT_VEHICLE_STOP)
                elif speed > self.stopped_speed_thresh and EventType.ABRUPT_VEHICLE_STOP in v.active_events:
                    v.active_events.remove(EventType.ABRUPT_VEHICLE_STOP)

            # Abnormal Speed Detection (compared to scene distribution)
            if len(moving_speeds) >= 3 and speed > self.stopped_speed_thresh:
                if speed > median_speed * 2.3 and speed > 80.0:
                    if EventType.ABNORMAL_VEHICLE_SPEED not in v.active_events:
                        v.active_events.append(EventType.ABNORMAL_VEHICLE_SPEED)
                else:
                    if EventType.ABNORMAL_VEHICLE_SPEED in v.active_events:
                        v.active_events.remove(EventType.ABNORMAL_VEHICLE_SPEED)
