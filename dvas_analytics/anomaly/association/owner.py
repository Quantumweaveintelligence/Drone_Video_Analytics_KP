"""
Person-Object Association and Ownership Estimation Module.
Tracks the relationship between people and objects to establish ownership,
detect object placement events (CARRIED -> PLACED), and monitor spatial separation.
"""

from typing import Dict, List, Optional, Tuple
import numpy as np

from tracking.track_state import EventType, ObjectState, TrackState



class OwnershipAssociator:
    """
    Evaluates spatial proximity, trajectory co-movement, and placement events
    to maintain ownership links between persons and candidate objects.
    """

    def __init__(
        self,
        max_association_dist_px: float = 120.0,
        co_movement_min_duration: float = 1.0,
        handoff_resistance: float = 0.75,  # Requires high confidence to switch owners
    ):
        self.max_association_dist_px = max_association_dist_px
        self.co_movement_min_duration = co_movement_min_duration
        self.handoff_resistance = handoff_resistance

        # Track historical pairwise co-location: (person_id, object_id) -> duration_sec
        self.colocation_history: Dict[Tuple[int, int], float] = {}

    def update_associations(
        self,
        tracks: List[TrackState],
        timestamp: float,
        fps: float = 30.0,
    ):
        """
        Updates owner associations for all candidate objects in the current frame.
        """
        dt = 1.0 / max(fps, 1.0)
        persons = [t for t in tracks if t.is_person]
        objects = [t for t in tracks if not t.is_person]

        # Prune colocation_history: remove entries for track IDs no longer active
        active_ids = {t.track_id for t in tracks}
        stale_keys = [
            k for k in self.colocation_history
            if k[0] not in active_ids or k[1] not in active_ids
        ]
        for k in stale_keys:
            del self.colocation_history[k]

        if not persons or not objects:
            # If no persons present, objects maintain previous owner if one exists
            for obj in objects:
                if obj.owner_id is not None:
                    # Mark owner as absent
                    obj.owner_distance = 999.0
            return

        for obj in objects:
            obj_pos = obj.bottom_center

            # Compute distances to all active persons
            person_distances: List[Tuple[int, float]] = []
            for p in persons:
                p_pos = p.bottom_center
                dist = float(np.hypot(obj_pos[0] - p_pos[0], obj_pos[1] - p_pos[1]))
                person_distances.append((p.track_id, dist))

            person_distances.sort(key=lambda x: x[1])
            closest_person_id, closest_dist = person_distances[0]

            # Update co-location record
            for pid, dist in person_distances:
                key = (pid, obj.track_id)
                if dist <= self.max_association_dist_px:
                    self.colocation_history[key] = self.colocation_history.get(key, 0.0) + dt
                else:
                    self.colocation_history[key] = max(0.0, self.colocation_history.get(key, 0.0) - (dt * 0.2))

            # Case A: Object has NO established owner yet
            if obj.owner_id is None:
                if closest_dist <= self.max_association_dist_px:
                    coloc_time = self.colocation_history.get((closest_person_id, obj.track_id), 0.0)
                    assoc_conf = min(1.0, coloc_time / self.co_movement_min_duration)

                    # Initial association
                    obj.owner_id = closest_person_id
                    obj.association_confidence = assoc_conf
                    obj.association_time = timestamp
                    obj.owner_distance = closest_dist
                    obj.state = ObjectState.ASSOCIATED
                    obj.timeline["t_association"] = timestamp
                    if EventType.UNKNOWN_OWNER_OBJECT in obj.active_events:
                        obj.active_events.remove(EventType.UNKNOWN_OWNER_OBJECT)
                else:
                    # Explicit Unknown Owner state: do not invent ownership
                    obj.owner_distance = closest_dist
                    if EventType.UNKNOWN_OWNER_OBJECT not in obj.active_events:
                        obj.active_events.append(EventType.UNKNOWN_OWNER_OBJECT)

            # Case B: Object ALREADY has an established owner
            else:
                # Find current distance to established owner
                owner_track = next((p for p in persons if p.track_id == obj.owner_id), None)
                if owner_track is not None:
                    o_pos = owner_track.bottom_center
                    obj.owner_distance = float(np.hypot(obj_pos[0] - o_pos[0], obj_pos[1] - o_pos[1]))
                else:
                    # Owner is outside the current camera FOV
                    obj.owner_distance = max(obj.owner_distance, 250.0)

                # Object Pickup & Reclaim Detection:
                # If object was unattended/stationary and is now approached and moving with person
                if (obj.unattended_duration > 0.0 or obj.state in {ObjectState.UNATTENDED, ObjectState.STATIONARY, ObjectState.SUSPICIOUS}):
                    if closest_dist <= self.max_association_dist_px * 0.85 and (obj.speed > 10.0 or (owner_track and owner_track.speed > 10.0)):
                        obj.unattended_duration = 0.0
                        obj.state = ObjectState.ASSOCIATED
                        obj.timeline["t_resolved"] = timestamp
                        if EventType.OBJECT_RECLAIMED not in obj.active_events:
                            obj.active_events.append(EventType.OBJECT_RECLAIMED)

                # Custody Transfer / Object Swap:
                # Only switch owner if another person stays with the object much longer
                if closest_person_id != obj.owner_id and closest_dist <= self.max_association_dist_px * 0.85:
                    new_coloc = self.colocation_history.get((closest_person_id, obj.track_id), 0.0)
                    old_coloc = self.colocation_history.get((obj.owner_id, obj.track_id), 0.0)
                    if new_coloc > old_coloc * (1.0 + self.handoff_resistance):
                        # Intentional custody transfer
                        obj.owner_id = closest_person_id
                        obj.association_confidence = 0.85
                        obj.owner_distance = closest_dist
                        obj.timeline["t_association"] = timestamp
                        if EventType.OBJECT_TRANSFER not in obj.active_events:
                            obj.active_events.append(EventType.OBJECT_TRANSFER)


            # Unexplained Object Movement Detection:
            # If object is moving fast with no person within proximity
            if obj.speed > 15.0 and closest_dist > self.max_association_dist_px * 1.5:
                if EventType.UNEXPLAINED_OBJECT_MOVEMENT not in obj.active_events:
                    obj.active_events.append(EventType.UNEXPLAINED_OBJECT_MOVEMENT)
            elif EventType.UNEXPLAINED_OBJECT_MOVEMENT in obj.active_events and obj.speed <= 5.0:
                obj.active_events.remove(EventType.UNEXPLAINED_OBJECT_MOVEMENT)

