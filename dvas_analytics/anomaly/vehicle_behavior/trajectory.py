"""
Vehicle trajectory analysis module for drone surveillance.
Evaluates trajectory curvature, erratic paths, and U-turn / trajectory reversals.
"""

from typing import Dict, List, Optional, Tuple
import numpy as np

from tracking.track_state import EventType, TrackState, VehicleAction


class VehicleTrajectoryAnalyzer:
    """
    Analyzes vehicle trajectory dynamics, curvature, and U-turns.
    """

    def __init__(
        self,
        reversal_angle_thresh: float = 135.0,
        reversal_window_sec: float = 10.0,
    ):
        self.reversal_angle_thresh = reversal_angle_thresh
        self.reversal_window_sec = reversal_window_sec
        # Heading histories: vehicle_id -> [(heading_deg, timestamp), ...]
        self.vehicle_headings: Dict[int, List[Tuple[float, float]]] = {}

    def analyze(
        self,
        vehicle: TrackState,
        timestamp: float,
    ):
        """
        Updates trajectory dynamics, detects U-turns and erratic trajectories.
        """
        history = vehicle.ground_history if vehicle.ground_history else vehicle.history
        if len(history) < 3:
            return

        tid = vehicle.track_id
        # Record heading
        if vehicle.speed > 10.0:
            if tid not in self.vehicle_headings:
                self.vehicle_headings[tid] = []
            self.vehicle_headings[tid].append((vehicle.heading_deg, timestamp))
            self.vehicle_headings[tid] = [
                (h, t) for h, t in self.vehicle_headings[tid]
                if timestamp - t <= self.reversal_window_sec
            ]

        # Check for U-turn (Trajectory Reversal)
        headings = self.vehicle_headings.get(tid, [])
        if len(headings) >= 2:
            initial_h = headings[0][0]

            current_h = headings[-1][0]
            diff = abs(current_h - initial_h)
            if diff > 180.0:
                diff = 360.0 - diff

            if diff >= self.reversal_angle_thresh:
                vehicle.vehicle_action = VehicleAction.U_TURN
                if EventType.VEHICLE_TRAJECTORY_ANOMALY not in vehicle.active_events:
                    vehicle.active_events.append(EventType.VEHICLE_TRAJECTORY_ANOMALY)
            elif EventType.VEHICLE_TRAJECTORY_ANOMALY in vehicle.active_events and diff < 60.0:
                vehicle.active_events.remove(EventType.VEHICLE_TRAJECTORY_ANOMALY)
