"""
Vehicle-person and vehicle-object interaction analysis module.
Tracks vehicle-person interactions and vehicle-abandoned object sequences.
"""

from typing import Dict, List, Optional, Set, Tuple
import numpy as np

from tracking.track_state import EventType, TrackState


class VehicleInteractionAnalyzer:
    """
    Analyzes inter-entity interactions involving vehicles:
    - Vehicle following patterns
    - Vehicle-person approach/depart events
    - Vehicle-dropoff object abandonment sequences
    """

    def __init__(
        self,
        interaction_dist_thresh: float = 140.0,
        following_min_duration_sec: float = 5.0,
    ):
        self.interaction_dist_thresh = interaction_dist_thresh
        self.following_min_duration_sec = following_min_duration_sec

        # Vehicle following pairs: (follower_id, lead_id) -> duration_sec
        self.vehicle_following_pairs: Dict[Tuple[int, int], float] = {}

        # Vehicle dropoff sequence state tracking:
        # object_id -> { "vehicle_id": int, "person_id": int, "stage": str, "placed_time": float, "departure_time": float }
        self.vehicle_dropoffs: Dict[int, Dict] = {}

    def update_interactions(
        self,
        vehicles: List[TrackState],
        persons: List[TrackState],
        objects: List[TrackState],
        timestamp: float,
        fps: float = 30.0,
    ) -> List[Tuple[int, EventType, str]]:
        """
        Updates interactions among vehicles, people, and objects.
        Returns list of (entity_id, EventType, reason_str).
        """
        dt = 1.0 / max(fps, 1.0)
        events_triggered: List[Tuple[int, EventType, str]] = []

        # 1. Vehicle-Vehicle Following
        for v1 in vehicles:
            if v1.speed < 15.0:
                continue
            for v2 in vehicles:
                if v1.track_id == v2.track_id or v2.speed < 15.0:
                    continue

                pos1 = v1.bottom_center
                pos2 = v2.bottom_center
                dist = float(np.hypot(pos1[0] - pos2[0], pos1[1] - pos2[1]))

                if 50.0 <= dist <= 280.0:
                    v1_vec = np.array(v1.velocity)
                    v2_vec = np.array(v2.velocity)
                    n1 = np.linalg.norm(v1_vec)
                    n2 = np.linalg.norm(v2_vec)
                    if n1 > 0 and n2 > 0 and np.dot(v1_vec, v2_vec) / (n1 * n2) >= 0.75:
                        pair = (v1.track_id, v2.track_id)
                        dur = self.vehicle_following_pairs.get(pair, 0.0) + dt
                        self.vehicle_following_pairs[pair] = dur
                        if dur >= self.following_min_duration_sec:
                            events_triggered.append((
                                v1.track_id,
                                EventType.VEHICLE_TRAJECTORY_ANOMALY,
                                f"Persistent vehicle following pattern: Vehicle #{v1.track_id} trailing Vehicle #{v2.track_id}",
                            ))
                            continue

                pair = (v1.track_id, v2.track_id)
                if pair in self.vehicle_following_pairs:
                    self.vehicle_following_pairs[pair] = max(0.0, self.vehicle_following_pairs[pair] - (dt * 0.5))

        # 2. Vehicle-Person Interaction
        for v in vehicles:
            v_pos = v.bottom_center
            for p in persons:
                p_pos = p.bottom_center
                dist = float(np.hypot(v_pos[0] - p_pos[0], v_pos[1] - p_pos[1]))
                if dist <= self.interaction_dist_thresh and v.speed <= 10.0:
                    if EventType.VEHICLE_PERSON_INTERACTION not in v.active_events:
                        v.active_events.append(EventType.VEHICLE_PERSON_INTERACTION)
                    if EventType.VEHICLE_PERSON_INTERACTION not in p.active_events:
                        p.active_events.append(EventType.VEHICLE_PERSON_INTERACTION)
                    p.timeline["t_interaction"] = timestamp

        # 3. Vehicle-Object Interaction & Dropoff Sequence
        # Sequence: Vehicle + Person near each other -> Object appears/placed -> Vehicle departs -> Object remains stationary
        for obj in objects:
            obj_id = obj.track_id
            obj_pos = obj.bottom_center

            # Check if object is stationary
            if not obj.is_stationary:
                continue

            # Stage 0/1: Detection of potential vehicle dropoff
            if obj_id not in self.vehicle_dropoffs:
                for v in vehicles:
                    v_dist = float(np.hypot(obj_pos[0] - v.bottom_center[0], obj_pos[1] - v.bottom_center[1]))
                    if v_dist <= self.interaction_dist_thresh:
                        self.vehicle_dropoffs[obj_id] = {
                            "vehicle_id": v.track_id,
                            "person_id": obj.owner_id,
                            "stage": "PLACED_NEAR_VEHICLE",
                            "placed_time": timestamp,
                            "departure_time": 0.0,
                        }
                        obj.timeline["t_placement"] = timestamp
                        break

            # Stage 2: Vehicle departs while object remains
            if obj_id in self.vehicle_dropoffs:
                dropoff = self.vehicle_dropoffs[obj_id]
                vid = dropoff["vehicle_id"]
                linked_v = next((v for v in vehicles if v.track_id == vid), None)

                if linked_v is not None:
                    v_dist = float(np.hypot(obj_pos[0] - linked_v.bottom_center[0], obj_pos[1] - linked_v.bottom_center[1]))
                    if v_dist > self.interaction_dist_thresh * 1.5 and linked_v.speed > 15.0:
                        dropoff["stage"] = "VEHICLE_DEPARTED"
                        if dropoff["departure_time"] == 0.0:
                            dropoff["departure_time"] = timestamp
                else:
                    # Vehicle departed camera field of view entirely
                    dropoff["stage"] = "VEHICLE_DEPARTED"
                    if dropoff["departure_time"] == 0.0:
                        dropoff["departure_time"] = timestamp

                if dropoff["stage"] == "VEHICLE_DEPARTED":
                    events_triggered.append((
                        obj_id,
                        EventType.VEHICLE_OBJECT_INTERACTION,
                        f"Object #{obj_id} placed and abandoned following departure of Vehicle #{vid}",
                    ))
                    if EventType.VEHICLE_OBJECT_INTERACTION not in obj.active_events:
                        obj.active_events.append(EventType.VEHICLE_OBJECT_INTERACTION)

        return events_triggered

