"""
State machine and explainable suspiciousness engine for drone surveillance.
Evaluates the full causal lifecycle:
NEW -> TRACKED -> ASSOCIATED -> PLACED -> STATIONARY -> OWNER_DEPARTING -> UNATTENDED -> SUSPICIOUS -> ALERTED
Generates structured, human-readable explanations answering exactly WHY an object is flagged.
"""

from typing import Dict, List, Tuple
from tracking.track_state import ObjectState, TrackState


class SuspiciousnessEngine:
    """
    Computes explainable multi-signal suspiciousness scores and governs state transitions.
    """

    def __init__(
        self,
        owner_departure_dist_px: float = 80.0,       # Separation distance threshold
        unattended_duration_threshold: float = 3.0,  # Seconds to trigger suspicious alert (configurable)
        score_threshold: float = 0.70,
        weights: Dict[str, float] = None,
    ):
        self.owner_departure_dist_px = owner_departure_dist_px
        self.unattended_duration_threshold = unattended_duration_threshold
        self.score_threshold = score_threshold

        self.weights = weights or {
            "unattended_duration": 0.35,
            "owner_distance": 0.25,
            "stationary": 0.20,
            "placement": 0.10,
            "association": 0.10,
        }

    def evaluate_object(
        self,
        obj: TrackState,
        all_tracks: List[TrackState],
        timestamp: float,
        fps: float = 30.0,
    ) -> Tuple[float, List[str], Dict[str, float]]:
        """
        Calculates explainable suspiciousness score, score breakdown, and descriptive reasons.
        """
        dt = 1.0 / max(fps, 1.0)
        reasons: List[str] = []
        breakdown: Dict[str, float] = {}

        # 1. Ground Stationarity Score
        if obj.is_stationary:
            stat_score = min(1.0, obj.stationary_duration / 5.0)
            reasons.append(
                f"GROUND_STATIONARY: Object is stationary on the ground (speed={obj.ground_speed:.1f}px/s, duration={obj.stationary_duration:.1f}s)"
            )
        else:
            stat_score = 0.0
        breakdown["stationary_score"] = round(stat_score, 3)

        # 2. Owner Association Score
        if obj.owner_id is not None:
            assoc_score = obj.association_confidence
            reasons.append(
                f"OWNER_LINKED: Established ownership with Person #{obj.owner_id} (confidence={obj.association_confidence:.0%})"
            )
        else:
            assoc_score = 0.30  # Baseline: object discovered without apparent owner
            reasons.append("NO_CURRENT_OWNER: Object appeared without clear owner in immediate vicinity")
        breakdown["association_score"] = round(assoc_score, 3)

        # 3. Owner Departure & Separation Score
        if obj.owner_id is not None:
            if obj.owner_distance >= self.owner_departure_dist_px:
                dist_score = min(1.0, obj.owner_distance / (self.owner_departure_dist_px * 2.0))
                reasons.append(
                    f"OWNER_DEPARTED: Owner Person #{obj.owner_id} moved away (distance={obj.owner_distance:.1f}px > {self.owner_departure_dist_px:.0f}px threshold)"
                )
            else:
                dist_score = 0.0
                reasons.append(
                    f"OWNER_NEAR: Owner Person #{obj.owner_id} is nearby (distance={obj.owner_distance:.1f}px)"
                )
        else:
            dist_score = 0.50
        breakdown["owner_distance_score"] = round(dist_score, 3)

        # 4. Temporal Unattended Duration Score
        if obj.is_stationary and (obj.owner_distance >= self.owner_departure_dist_px or obj.owner_id is None):
            obj.unattended_duration += dt
            unattended_ratio = min(1.0, obj.unattended_duration / self.unattended_duration_threshold)
            unattended_score = unattended_ratio
            reasons.append(
                f"UNATTENDED_DURATION: Abandoned for {obj.unattended_duration:.1f}s (threshold={self.unattended_duration_threshold:.1f}s)"
            )
        else:
            # Recovery: Owner returned or object picked up
            obj.unattended_duration = max(0.0, obj.unattended_duration - (dt * 2.0))
            unattended_score = 0.0
        breakdown["unattended_duration_score"] = round(unattended_score, 3)

        # 5. Bystander Isolation Check
        other_persons = [t for t in all_tracks if t.is_person and t.track_id != obj.owner_id]
        min_bystander_dist = 999.0
        for p in other_persons:
            d = float(sum((a - b) ** 2 for a, b in zip(obj.bottom_center, p.bottom_center)) ** 0.5)
            min_bystander_dist = min(min_bystander_dist, d)

        if min_bystander_dist >= self.owner_departure_dist_px * 0.8:
            placement_score = 0.90
            reasons.append(f"ISOLATED_CUSTODY: No bystanders within proximity (nearest person={min_bystander_dist:.1f}px)")
        else:
            placement_score = 0.30
            reasons.append(f"BYSTANDER_NEARBY: Another person is near the object ({min_bystander_dist:.1f}px)")
        breakdown["placement_isolation_score"] = round(placement_score, 3)

        # Compute Composite Weighted Suspiciousness Score
        w = self.weights
        total_score = (
            w["unattended_duration"] * unattended_score
            + w["owner_distance"] * dist_score
            + w["stationary"] * stat_score
            + w["placement"] * placement_score
            + w["association"] * assoc_score
        )
        total_score = min(1.0, max(0.0, total_score))

        # Update TrackState
        obj.suspiciousness_score = total_score
        obj.explainability_reasons = reasons
        obj.score_breakdown = breakdown

        # State Machine Transitions
        self._update_state_machine(obj, total_score, timestamp)

        return total_score, reasons, breakdown

    def _update_state_machine(self, obj: TrackState, score: float, timestamp: float):
        """Advances the state machine based on temporal criteria."""
        prev_state = obj.state

        if score >= self.score_threshold and obj.unattended_duration >= self.unattended_duration_threshold:
            obj.state = ObjectState.SUSPICIOUS
        elif obj.unattended_duration >= self.unattended_duration_threshold * 0.5:
            obj.state = ObjectState.UNATTENDED
        elif obj.owner_distance >= self.owner_departure_dist_px and obj.is_stationary:
            obj.state = ObjectState.OWNER_DEPARTING
        elif obj.is_stationary:
            obj.state = ObjectState.STATIONARY
        elif obj.owner_id is not None:
            obj.state = ObjectState.ASSOCIATED
        else:
            obj.state = ObjectState.TRACKED

        if obj.state != prev_state:
            obj.state_change_time = timestamp
