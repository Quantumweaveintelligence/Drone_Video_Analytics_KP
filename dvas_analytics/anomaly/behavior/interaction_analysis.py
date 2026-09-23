"""
Person-person interaction and collective crowd dynamics analysis module.
Detects persistent following, group convergence, and rapid crowd dispersal.
"""

from typing import Dict, List, Optional, Tuple
import numpy as np

from tracking.track_state import EventType, PersonAction, TrackState


class InteractionAnalyzer:
    """
    Analyzes inter-person relational kinematics to identify pair interactions
    (following, grouping) and collective crowd dynamics (convergence, dispersal).
    """

    def __init__(
        self,
        min_following_duration_sec: float = 4.0,
        following_dist_min: float = 25.0,
        following_dist_max: float = 180.0,
        convergence_dist_thresh: float = 120.0,
        dispersal_speed_thresh: float = 65.0,
    ):
        self.min_following_duration_sec = min_following_duration_sec
        self.following_dist_min = following_dist_min
        self.following_dist_max = following_dist_max
        self.convergence_dist_thresh = convergence_dist_thresh
        self.dispersal_speed_thresh = dispersal_speed_thresh

        # History of persistent following pairs: (follower_id, target_id) -> duration_sec
        self.following_pairs: Dict[Tuple[int, int], float] = {}

        # Pair meeting history: (min_id, max_id) -> {"start_time": float, "duration": float, "pos": (x,y)}
        self.pair_meetings: Dict[Tuple[int, int], Dict[str, Any]] = {}

        # Historical owner tracking for objects: obj_id -> last_known_owner_id
        self.prior_object_owners: Dict[int, int] = {}

    def update_interactions(
        self,
        persons: List[TrackState],
        timestamp: float,
        fps: float = 30.0,
        objects: Optional[List[TrackState]] = None,
    ) -> List[EventType]:
        """
        Updates pairwise person interactions, object transfers, and collective crowd dynamics.
        Returns newly triggered collective event types for this frame.
        """
        dt = 1.0 / max(fps, 1.0)
        active_events: List[EventType] = []

        if len(persons) < 2:
            self.following_pairs.clear()
            return active_events

        active_pids = {p.track_id for p in persons}
        # Clean stale pair tracking
        stale_pairs = [k for k in self.following_pairs if k[0] not in active_pids or k[1] not in active_pids]
        for k in stale_pairs:
            del self.following_pairs[k]

        person_map = {p.track_id: p for p in persons}

        # 1. Pairwise Following Detection
        for p1 in persons:
            if p1.speed < 12.0:
                continue

            for p2 in persons:
                if p1.track_id == p2.track_id or p2.speed < 12.0:
                    continue

                pos1 = p1.bottom_center
                pos2 = p2.bottom_center
                dist = float(np.hypot(pos1[0] - pos2[0], pos1[1] - pos2[1]))

                if self.following_dist_min <= dist <= self.following_dist_max:
                    # Check velocity vector alignment (cosine similarity)
                    v1 = np.array(p1.velocity)
                    v2 = np.array(p2.velocity)
                    norm1 = np.linalg.norm(v1)
                    norm2 = np.linalg.norm(v2)

                    if norm1 > 0 and norm2 > 0:
                        cos_sim = float(np.dot(v1, v2) / (norm1 * norm2))
                        # Heading aligned within ~45 degrees
                        if cos_sim >= 0.70:
                            # Check if p1 is moving in direction of p2 (p1 is following p2)
                            disp = np.array([pos2[0] - pos1[0], pos2[1] - pos1[1]])
                            disp_norm = np.linalg.norm(disp)
                            if disp_norm > 0:
                                heading_to_target = float(np.dot(v1, disp) / (norm1 * disp_norm))
                                if heading_to_target >= 0.50:
                                    pair_key = (p1.track_id, p2.track_id)
                                    dur = self.following_pairs.get(pair_key, 0.0) + dt
                                    self.following_pairs[pair_key] = dur

                                    if dur >= self.min_following_duration_sec:
                                        p1.following_target_id = p2.track_id
                                        p1.following_duration = dur
                                        p1.person_action = PersonAction.FOLLOWING
                                        if EventType.PERSISTENT_FOLLOWING not in p1.active_events:
                                            p1.active_events.append(EventType.PERSISTENT_FOLLOWING)
                                    continue

                # Decay if not following

                pair_key = (p1.track_id, p2.track_id)
                if pair_key in self.following_pairs:
                    self.following_pairs[pair_key] = max(0.0, self.following_pairs[pair_key] - (dt * 0.5))
                    if self.following_pairs[pair_key] < self.min_following_duration_sec:
                        if p1.following_target_id == p2.track_id:
                            p1.following_target_id = None
                            p1.following_duration = 0.0
                        if EventType.PERSISTENT_FOLLOWING in p1.active_events:
                            p1.active_events.remove(EventType.PERSISTENT_FOLLOWING)

        # 2. Collective Group Dynamics (Convergence vs. Dispersal)
        if len(persons) >= 4:
            positions = np.array([p.bottom_center for p in persons])
            velocities = np.array([p.velocity for p in persons])
            speeds = np.array([p.speed for p in persons])

            centroid = np.mean(positions, axis=0)
            # Radial vectors from centroid to each person
            rad_vectors = positions - centroid
            rad_dists = np.linalg.norm(rad_vectors, axis=1, keepdims=True)
            valid_mask = rad_dists.flatten() > 5.0

            if np.sum(valid_mask) >= 4:
                unit_rad = rad_vectors[valid_mask] / rad_dists[valid_mask]
                v_valid = velocities[valid_mask]
                s_valid = speeds[valid_mask]

                # Radial dot product: >0 means moving away (dispersal), <0 means moving inward (convergence)
                radial_projections = np.sum(v_valid * unit_rad, axis=1)

                # Group Convergence: majority moving inward toward common centroid
                inward_count = np.sum(radial_projections < -15.0)
                if inward_count >= 3 and inward_count / len(v_valid) >= 0.60:
                    mean_cluster_radius = float(np.mean(rad_dists[valid_mask]))
                    if mean_cluster_radius <= self.convergence_dist_thresh:
                        active_events.append(EventType.GROUP_CONVERGENCE)
                        for p in persons:
                            if EventType.GROUP_CONVERGENCE not in p.active_events:
                                p.active_events.append(EventType.GROUP_CONVERGENCE)

                # Rapid Crowd Dispersal: majority moving outward at high speed
                outward_fast_count = np.sum((radial_projections > 25.0) & (s_valid >= self.dispersal_speed_thresh))
                if outward_fast_count >= 3 and outward_fast_count / len(v_valid) >= 0.50:
                    active_events.append(EventType.RAPID_CROWD_DISPERSAL)
                    for p in persons:
                        if EventType.RAPID_CROWD_DISPERSAL not in p.active_events:
                            p.active_events.append(EventType.RAPID_CROWD_DISPERSAL)

        # 3. Object Handoff / Transfer Detection
        if objects and len(persons) >= 2:
            for obj in objects:
                oid = obj.track_id
                curr_owner = obj.owner_id
                prev_owner = self.prior_object_owners.get(oid)

                if curr_owner is not None:
                    if prev_owner is not None and prev_owner != curr_owner:
                        # Direct owner change detected
                        p_prev = person_map.get(prev_owner)
                        p_curr = person_map.get(curr_owner)
                        if p_prev and p_curr:
                            dist_between = float(np.hypot(p_prev.center[0] - p_curr.center[0], p_prev.center[1] - p_curr.center[1]))
                            if dist_between <= 90.0:
                                active_events.append(EventType.OBJECT_TRANSFER)
                                for entity in [obj, p_prev, p_curr]:
                                    if EventType.OBJECT_TRANSFER not in entity.active_events:
                                        entity.active_events.append(EventType.OBJECT_TRANSFER)
                    self.prior_object_owners[oid] = curr_owner

        # 4. Coordinated Multi-Person Arrival
        if len(persons) >= 3:
            for i, p1 in enumerate(persons):
                cluster = [p1]
                pos1 = p1.bottom_center
                for j, p2 in enumerate(persons):
                    if i != j:
                        pos2 = p2.bottom_center
                        if float(np.hypot(pos1[0] - pos2[0], pos1[1] - pos2[1])) <= 85.0:
                            cluster.append(p2)

                if len(cluster) >= 3:
                    # Check historical separation: were they separated (>130px) in earlier frames?
                    diverse_origins = True
                    for c_idx1 in range(len(cluster)):
                        for c_idx2 in range(c_idx1 + 1, len(cluster)):
                            cp1, cp2 = cluster[c_idx1], cluster[c_idx2]
                            h1 = cp1.ground_history if cp1.ground_history else cp1.history
                            h2 = cp2.ground_history if cp2.ground_history else cp2.history
                            if len(h1) >= 15 and len(h2) >= 15:
                                hist_dist = float(np.hypot(h1[0][0] - h2[0][0], h1[0][1] - h2[0][1]))
                                if hist_dist < 110.0:
                                    diverse_origins = False
                                    break
                            else:
                                diverse_origins = False
                                break
                        if not diverse_origins:
                            break

                    if diverse_origins:
                        active_events.append(EventType.COORDINATED_ARRIVAL)
                        for member in cluster:
                            if EventType.COORDINATED_ARRIVAL not in member.active_events:
                                member.active_events.append(EventType.COORDINATED_ARRIVAL)

        return active_events

