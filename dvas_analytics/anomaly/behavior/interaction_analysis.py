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

    def update_interactions(
        self,
        persons: List[TrackState],
        timestamp: float,
        fps: float = 30.0,
    ) -> List[EventType]:
        """
        Updates pairwise person interactions and collective crowd dynamics.
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

        return active_events
