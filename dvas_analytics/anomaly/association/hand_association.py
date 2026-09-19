"""
Object-in-hand and handheld weapon association module.
Evaluates human hand/arm body quadrant proximity, persistent co-movement,
weapon-held states, aiming candidates, and high-risk abandoned weapons.
"""

from typing import Dict, List, Optional, Tuple
import numpy as np

from tracking.track_state import EventType, TrackState


class HandAssociationEngine:
    """
    Tracks spatial association between small candidate objects (e.g. handguns, tools, small bags)
    and human hand/extremity zones across frames.
    """

    def __init__(
        self,
        hand_zone_dist_px: float = 40.0,
        co_movement_thresh: float = 0.80,
        aiming_angle_thresh_deg: float = 35.0,
        min_held_duration_sec: float = 0.5,
    ):
        self.hand_zone_dist_px = hand_zone_dist_px
        self.co_movement_thresh = co_movement_thresh
        self.aiming_angle_thresh_deg = aiming_angle_thresh_deg
        self.min_held_duration_sec = min_held_duration_sec

        # Track history of (person_id, object_id) -> duration_held
        self.held_durations: Dict[Tuple[int, int], float] = {}

    def update_hand_associations(
        self,
        persons: List[TrackState],
        objects: List[TrackState],
        timestamp: float,
        fps: float = 30.0,
    ):
        """
        Evaluates hand/body relationships for objects and checks for aiming or weapon-held states.
        """
        dt = 1.0 / max(fps, 1.0)
        weapon_classes = {"gun", "handgun", "pistol", "rifle", "weapon", "knife"}

        for obj in objects:
            is_weapon = obj.class_name.lower() in weapon_classes
            obj.is_weapon_candidate = is_weapon
            obj_center = obj.center

            best_holder_id: Optional[int] = None
            best_dist = 999.0

            for p in persons:
                # Geometric estimation of hand/arm extremities from bounding box:
                # Left hand region: (p.bbox[0], p.bbox[1] + 0.6 * p.height)
                # Right hand region: (p.bbox[2], p.bbox[1] + 0.6 * p.height)
                # Center chest/hip region: (p.center[0], p.bbox[1] + 0.5 * p.height)
                p_h = p.height
                p_w = p.width
                left_hand = (p.bbox[0] - 0.05 * p_w, p.bbox[1] + 0.55 * p_h)
                right_hand = (p.bbox[2] + 0.05 * p_w, p.bbox[1] + 0.55 * p_h)
                torso_point = (p.center[0], p.bbox[1] + 0.50 * p_h)

                d_left = float(np.hypot(obj_center[0] - left_hand[0], obj_center[1] - left_hand[1]))
                d_right = float(np.hypot(obj_center[0] - right_hand[0], obj_center[1] - right_hand[1]))
                d_torso = float(np.hypot(obj_center[0] - torso_point[0], obj_center[1] - torso_point[1]))

                min_hand_dist = min(d_left, d_right, d_torso)
                # Scale threshold with person bounding height
                adaptive_thresh = max(self.hand_zone_dist_px, p_h * 0.35)

                if min_hand_dist <= adaptive_thresh:
                    # Check velocity co-movement (person and object move synchronously)
                    if p.speed > 5.0 and obj.speed > 5.0:
                        v_p = np.array(p.velocity)
                        v_o = np.array(obj.velocity)
                        norm_p = np.linalg.norm(v_p)
                        norm_o = np.linalg.norm(v_o)
                        if norm_p > 0 and norm_o > 0 and (np.dot(v_p, v_o) / (norm_p * norm_o)) >= self.co_movement_thresh:
                            if min_hand_dist < best_dist:
                                best_dist = min_hand_dist
                                best_holder_id = p.track_id
                    elif min_hand_dist < best_dist:
                        # Stationary or slow movement together
                        best_dist = min_hand_dist
                        best_holder_id = p.track_id

            if best_holder_id is not None:
                pair_key = (best_holder_id, obj.track_id)
                dur = self.held_durations.get(pair_key, 0.0) + dt
                self.held_durations[pair_key] = dur

                if dur >= self.min_held_duration_sec:
                    obj.is_held = True
                    obj.held_by_id = best_holder_id
                    obj.owner_id = best_holder_id
                    obj.association_confidence = 0.95
                    obj.timeline["t_association"] = timestamp

                    # Handheld weapon classification
                    if is_weapon:
                        if EventType.WEAPON_HELD not in obj.active_events:
                            obj.active_events.append(EventType.WEAPON_HELD)

                        # Weapon Aiming Evaluation
                        # Check vector from holding person through weapon pointing toward other persons
                        holder = next((p for p in persons if p.track_id == best_holder_id), None)
                        if holder is not None:
                            h_center = holder.center
                            aim_vector = np.array([obj_center[0] - h_center[0], obj_center[1] - h_center[1]])
                            aim_norm = np.linalg.norm(aim_vector)

                            if aim_norm > 5.0:
                                aim_unit = aim_vector / aim_norm
                                for other_p in persons:
                                    if other_p.track_id == best_holder_id:
                                        continue
                                    o_pos = other_p.center
                                    target_vec = np.array([o_pos[0] - obj_center[0], o_pos[1] - obj_center[1]])
                                    t_norm = np.linalg.norm(target_vec)

                                    if 40.0 <= t_norm <= 350.0:
                                        t_unit = target_vec / t_norm
                                        cos_aim = float(np.dot(aim_unit, t_unit))
                                        angle_deg = float(np.degrees(np.arccos(np.clip(cos_aim, -1.0, 1.0))))

                                        if angle_deg <= self.aiming_angle_thresh_deg:
                                            obj.aiming_target_id = other_p.track_id
                                            if EventType.WEAPON_AIMING_CANDIDATE not in obj.active_events:
                                                obj.active_events.append(EventType.WEAPON_AIMING_CANDIDATE)
                                            break
            else:
                # Not currently held
                obj.is_held = False
                obj.held_by_id = None
                obj.aiming_target_id = None
                if EventType.WEAPON_HELD in obj.active_events:
                    obj.active_events.remove(EventType.WEAPON_HELD)
                if EventType.WEAPON_AIMING_CANDIDATE in obj.active_events:
                    obj.active_events.remove(EventType.WEAPON_AIMING_CANDIDATE)
