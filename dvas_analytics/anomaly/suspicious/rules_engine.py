"""
Cognitive Reasoning and Suspicious-Event Rules Engine for Drone Aerial Surveillance.
Decouples Rule Confidence (evidence strength) from Alert Confidence (persistent, accumulated, mutually consistent evidence).
Implements a 4-tier confidence system, 5-stage alert pipeline, hard gates, independent evidence groups (2/3 rule),
rolling temporal voting, and hysteresis.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import yaml
import numpy as np

from tracking.track_state import (
    AlertPipelineStage,
    EventType,
    ObjectState,
    PersonAction,
    SuspicionLevel,
    TrackState,
)


class SuspiciousEventRulesEngine:
    """
    Synthesizes multi-signal evidence hierarchies across detection, kinematics, behaviors,
    interactions, scene context, and temporal histories without assuming human intent.

    Enforces the 5-Stage Alert Pipeline:
    DETECTION -> OBSERVATION -> CANDIDATE EVENT -> CONFIRMATION -> ALERT

    Evaluates 4 Distinct Confidences:
    1. detector_confidence : Certainty that the physical entity exists
    2. rule_confidence     : Evidence strength for a specific behavior rule
    3. event_confidence    : Multi-rule combination score for a specific event
    4. alert_confidence    : Persistent, accumulated, mutually consistent evidence (hard to reach)
    """

    def __init__(
        self,
        owner_departure_dist_px: Optional[float] = None,
        unattended_duration_thresh_sec: Optional[float] = None,
        loiter_dwell_thresh_sec: Optional[float] = None,
        wrong_direction_speed_thresh: Optional[float] = None,
        cooldown_seconds: Optional[float] = None,
        enable_wrong_way: bool = False,
        config_path: Optional[str] = None,
    ):
        # Baseline Defaults (Calibrated with additional 20% reduction)
        self.owner_departure_dist_px = 60.0
        self.unattended_duration_thresh_sec = 16.0
        self.loiter_dwell_thresh_sec = 24.0
        self.wrong_direction_speed_thresh = 20.0
        self.speed_hazard_thresh = 250.0
        self.cooldown_seconds = 28.0
        self.enable_wrong_way = enable_wrong_way
        self.min_groups_medium = 2
        self.min_groups_high = 2

        # Detector Confidence Thresholds (calibrated for aerial VisDrone + 10%)
        self.min_det_conf_person = 0.33
        self.min_det_conf_object = 0.33
        self.min_det_conf_vehicle = 0.33
        self.min_det_conf_weapon = 0.55

        # Temporal Voting Requirements
        self.vote_configs = {
            "ordinary_behavior": {"window": 5, "required": 3},
            "important_behavior": {"window": 8, "required": 5},
            "weapon_like": {"window": 10, "required": 6},
        }

        # Hysteresis Thresholds (calibrated for 2-5 alerts + 10%)
        self.hysteresis_enter = 0.66
        self.hysteresis_maintain = 0.55
        self.hysteresis_clear = 0.44
        self.hysteresis_clear_frames = 10

        # Smoothing Parameter
        self.detector_alpha = 0.40

        # Load YAML configuration if provided or found at default location
        cfg_file = Path(config_path) if config_path else Path(__file__).resolve().parent.parent / "config" / "thresholds.yaml"
        if cfg_file.exists():
            try:
                with open(cfg_file, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f)
                    if cfg and isinstance(cfg, dict):
                        self._load_yaml_config(cfg)
            except Exception as e:
                print(f"[RulesEngine] Warning: Could not parse {cfg_file}: {e}")

        # Explicit kwargs override YAML configuration
        if owner_departure_dist_px is not None:
            self.owner_departure_dist_px = owner_departure_dist_px
        if unattended_duration_thresh_sec is not None:
            self.unattended_duration_thresh_sec = unattended_duration_thresh_sec
        if loiter_dwell_thresh_sec is not None:
            self.loiter_dwell_thresh_sec = loiter_dwell_thresh_sec
        if wrong_direction_speed_thresh is not None:
            self.wrong_direction_speed_thresh = wrong_direction_speed_thresh
        if cooldown_seconds is not None:
            self.cooldown_seconds = cooldown_seconds

    def _load_yaml_config(self, cfg: Dict[str, Any]) -> None:
        """Applies thresholds and parameters from parsed YAML configuration."""
        conf_cfg = cfg.get("confidence", {})
        det_cfg = conf_cfg.get("detector", {})
        self.min_det_conf_person = det_cfg.get("person", self.min_det_conf_person)
        self.min_det_conf_object = det_cfg.get("ordinary_object", self.min_det_conf_object)
        self.min_det_conf_vehicle = det_cfg.get("vehicle", self.min_det_conf_vehicle)
        self.min_det_conf_weapon = det_cfg.get("weapon_like", self.min_det_conf_weapon)

        hyst = cfg.get("hysteresis", {})
        self.hysteresis_enter = hyst.get("enter_threshold", self.hysteresis_enter)
        self.hysteresis_maintain = hyst.get("maintain_threshold", self.hysteresis_maintain)
        self.hysteresis_clear = hyst.get("clear_threshold", self.hysteresis_clear)
        self.hysteresis_clear_frames = hyst.get("clear_frames", self.hysteresis_clear_frames)

        sm = cfg.get("smoothing", {})
        self.detector_alpha = sm.get("detector_alpha", self.detector_alpha)

        cd = cfg.get("cooldown", {})
        self.cooldown_seconds = cd.get("default_seconds", self.cooldown_seconds)

        ab = cfg.get("abandoned_object", {})
        self.unattended_duration_thresh_sec = ab.get("minimum_unattended_seconds", self.unattended_duration_thresh_sec)
        self.owner_departure_dist_px = ab.get("owner_departure_dist_px", self.owner_departure_dist_px)

        veh_cfg = cfg.get("vehicle", {})
        self.speed_hazard_thresh = veh_cfg.get("speed_hazard_thresh_px", self.speed_hazard_thresh)
        if "enable_wrong_way" in veh_cfg:
            self.enable_wrong_way = veh_cfg["enable_wrong_way"]

        ev_grp = cfg.get("evidence_groups", {})
        self.min_groups_medium = ev_grp.get("min_groups_medium", self.min_groups_medium)
        self.min_groups_high = ev_grp.get("min_groups_high", self.min_groups_high)

        tv = cfg.get("temporal_confirmation", {})
        if tv:
            self.vote_configs.update(tv)

    @staticmethod
    def get_confidence_band(conf: float) -> Tuple[str, str]:
        """
        Maps rule confidence to recommended engineering band and action:
        < 0.28       -> ("VERY_WEAK", "Ignore")
        0.28 - 0.38  -> ("WEAK", "Record internally")
        0.39 - 0.49  -> ("MODERATE", "Monitor")
        0.50 - 0.56  -> ("STRONG", "Candidate event")
        0.57 - 0.65  -> ("VERY_STRONG", "Event evidence")
        >= 0.66      -> ("EXTREME", "High-quality evidence")
        """
        if conf < 0.28:
            return "VERY_WEAK", "Ignore"
        elif conf < 0.39:
            return "WEAK", "Record internally"
        elif conf < 0.50:
            return "MODERATE", "Monitor"
        elif conf < 0.57:
            return "STRONG", "Candidate event"
        elif conf < 0.66:
            return "VERY_STRONG", "Event evidence"
        else:
            return "EXTREME", "High-quality evidence"

    def evaluate_entity(
        self,
        track: TrackState,
        all_tracks: List[TrackState],
        timestamp: float,
        fps: float = 30.0,
    ) -> Dict[str, Any]:
        """
        Evaluates a single tracked entity across detection, kinematics, behaviors,
        interactions, scene context, and temporal histories.

        Updates the 4 separate confidences, evidence groups, and pipeline stage.
        """
        reasons: List[str] = []
        active_groups: Set[str] = set()

        level = SuspicionLevel.NORMAL
        primary_event: Optional[EventType] = None
        pipeline_stage = AlertPipelineStage.DETECTION
        is_suspicious = False

        # 1. Update smoothed detector confidence
        det_conf = track.update_detector_smoothing(track.confidence, alpha=self.detector_alpha)
        track.detector_confidence = round(float(track.confidence), 3)

        track_dur = max(0.0, timestamp - track.first_seen_time)

        # Baseline Group A: Object/Entity Evidence
        if det_conf >= 0.65:
            active_groups.add("A")

        # Baseline Group G: Temporal Persistence Evidence
        if track_dur >= 3.0:
            active_groups.add("G")

        rule_conf = 0.0

        # -------------------------------------------------------------------
        # Branch 1: Candidate Physical Objects (bags, packages, weapons)
        # -------------------------------------------------------------------
        if not track.is_person and not track.is_vehicle:
            # Check if object is carried or held by a person (Group D)
            is_held = track.is_held or (
                track.owner_id is not None
                and track.owner_distance < 35.0
                and not track.is_stationary
            )

            # --- 1A. Held Object (Carried Normally) ---
            if is_held and not track.is_weapon_candidate:
                track.is_held = True
                level = SuspicionLevel.NORMAL
                primary_event = EventType.HELD_OBJECT
                pipeline_stage = AlertPipelineStage.OBSERVATION
                is_suspicious = False
                rule_conf = 0.75
                track.held_object_confidence = 0.80
                active_groups.update(["A", "D"])
                reasons.append("carried_object_synchronous_movement_with_person")
                reasons.append(f"associated_with_carrier_track_{track.owner_id or track.held_by_id}")
                reasons.append("normal_transit_activity_zero_alerts")

            # --- 1B. Weapon-Like Object ---
            elif track.is_weapon_candidate:
                track.record_temporal_vote("weapon_detection", det_conf >= self.min_det_conf_weapon, window_size=10)
                if track.is_held:
                    track.timeline["t_interaction"] = timestamp
                    active_groups.add("D")
                    # Aiming pose: weapon held and oriented toward another person
                    if track.aiming_target_id is not None:
                        level = SuspicionLevel.HIGH
                        primary_event = EventType.WEAPON_AIMING_CANDIDATE
                        pipeline_stage = AlertPipelineStage.CONFIRMATION
                        is_suspicious = True
                        rule_conf = 0.88
                        active_groups.add("B")
                        reasons.append("weapon_like_object_detected")
                        reasons.append(f"held_by_person_{track.held_by_id}")
                        reasons.append(f"weapon_aligned_toward_target_{track.aiming_target_id}")
                        reasons.append("hostile_aiming_pose_candidate")
                    else:
                        # Carried normally: MONITOR, not alert
                        level = SuspicionLevel.LOW
                        primary_event = EventType.WEAPON_HELD
                        pipeline_stage = AlertPipelineStage.OBSERVATION
                        is_suspicious = False
                        rule_conf = 0.78
                        reasons.append("weapon_like_object_detected")
                        reasons.append(f"carried_normally_by_person_{track.held_by_id or track.owner_id}")
                        reasons.append("visual_detection_monitored_no_hostile_interaction")
                elif track.is_stationary and (track.owner_distance >= self.owner_departure_dist_px or track.owner_id is None):
                    # Abandoned Weapon
                    active_groups.update(["C", "G"])
                    if track.owner_id is not None:
                        active_groups.add("D")
                        crit_thresh = min(3.0, self.unattended_duration_thresh_sec)
                        level = SuspicionLevel.CRITICAL if track.unattended_duration >= crit_thresh else SuspicionLevel.HIGH
                        primary_event = EventType.WEAPON_LIKE_OBJECT
                        pipeline_stage = AlertPipelineStage.CONFIRMATION
                        is_suspicious = True
                        rule_conf = 0.90
                        reasons.append("weapon_like_object_abandoned")
                        reasons.append(f"owner_{track.owner_id}_departed_distance_{track.owner_distance:.1f}px")
                        reasons.append(f"weapon_remained_stationary_{track.stationary_duration:.1f}s")
                        reasons.append(f"unattended_duration_{track.unattended_duration:.1f}s")
                    else:
                        level = SuspicionLevel.HIGH if track_dur >= 15.0 else SuspicionLevel.MEDIUM
                        primary_event = EventType.UNKNOWN_OWNER_WEAPON
                        pipeline_stage = AlertPipelineStage.CONFIRMATION if track_dur >= 15.0 else AlertPipelineStage.CANDIDATE
                        is_suspicious = True
                        rule_conf = 0.85
                        reasons.append("unattended_weapon_discovered_without_known_owner")
                        reasons.append(f"weapon_remained_stationary_{track.stationary_duration:.1f}s")
                        reasons.append(f"persistence_time_{track_dur:.1f}s")
                else:
                    level = SuspicionLevel.OBSERVE
                    primary_event = EventType.WEAPON_LIKE_OBJECT
                    pipeline_stage = AlertPipelineStage.OBSERVATION
                    rule_conf = 0.65
                    reasons.append("weapon_like_object_detected")
                    reasons.append("observing_temporal_behavior")

            # --- 1C. Ordinary Objects (backpacks, suitcases, boxes, packages) ---
            else:
                # Check Vehicle Dropoff Sequence
                if EventType.VEHICLE_OBJECT_INTERACTION in track.active_events:
                    level = SuspicionLevel.HIGH
                    primary_event = EventType.VEHICLE_OBJECT_ABANDONMENT
                    pipeline_stage = AlertPipelineStage.CONFIRMATION
                    is_suspicious = True
                    rule_conf = 0.92
                    active_groups.update(["C", "D", "E", "G"])
                    reasons.append("package_placed_adjacent_to_vehicle")
                    reasons.append("person_returned_to_vehicle")
                    reasons.append("vehicle_departed_scene")
                    reasons.append("package_remained_stationary_on_ground")
                elif EventType.OBJECT_TRANSFER in track.active_events:
                    level = SuspicionLevel.MEDIUM
                    primary_event = EventType.OBJECT_TRANSFER
                    pipeline_stage = AlertPipelineStage.CANDIDATE
                    is_suspicious = True
                    rule_conf = 0.75
                    active_groups.update(["A", "D"])
                    reasons.append("object_transferred_between_persons_in_close_proximity")
                elif track.is_stationary:
                    active_groups.add("C")  # Object Behavior (Stationarity)
                    track.timeline["t_stationary"] = track.timeline["t_stationary"] or timestamp

                    # Unattended duration accumulator
                    dt = 1.0 / max(fps, 1.0)
                    if track.owner_id is None or track.owner_distance >= self.owner_departure_dist_px:
                        track.unattended_duration += dt
                    else:
                        track.unattended_duration = max(0.0, track.unattended_duration - (dt * 2.0))

                    unattended_dur = track.unattended_duration

                    # Evaluate Owner Separation
                    if track.owner_id is not None:
                        active_groups.add("D")  # Person-Object Relationship
                        if track.owner_distance >= self.owner_departure_dist_px:
                            track.timeline["t_owner_departure"] = track.timeline["t_owner_departure"] or timestamp
                            track.timeline["t_unattended"] = track.timeline["t_unattended"] or timestamp

                            # 4-Stage Abandoned-Object Timing Progression:
                            # 0-10s  -> OBSERVATION / LOW
                            # 10-30s -> CANDIDATE / MEDIUM
                            # 30-60s -> CONFIRMATION / HIGH
                            # 60+s   -> HIGH-CONFIDENCE ABANDONMENT / HIGH or CRITICAL
                            mid_thresh = min(10.0, self.unattended_duration_thresh_sec * 0.5)
                            if unattended_dur >= self.unattended_duration_thresh_sec:
                                level = SuspicionLevel.HIGH
                                primary_event = EventType.ABANDONED_OBJECT
                                pipeline_stage = AlertPipelineStage.CONFIRMATION
                                is_suspicious = True
                                rule_conf = 0.88 if unattended_dur < 60.0 else 0.95
                                active_groups.add("G")
                                reasons.append(f"object_associated_with_owner_{track.owner_id}")
                                reasons.append(f"owner_departed_distance_{track.owner_distance:.1f}px")
                                reasons.append(f"object_ground_stationary_{track.stationary_duration:.1f}s")
                                reasons.append(f"unattended_duration_exceeded_threshold_{unattended_dur:.1f}s")
                            elif unattended_dur >= mid_thresh:
                                level = SuspicionLevel.MEDIUM
                                primary_event = EventType.ABANDONED_OBJECT
                                pipeline_stage = AlertPipelineStage.CANDIDATE
                                is_suspicious = False
                                rule_conf = 0.75
                                reasons.append(f"owner_departed_distance_{track.owner_distance:.1f}px")
                                reasons.append("candidate_unattended_object_monitoring")
                                reasons.append(f"unattended_duration_{unattended_dur:.1f}s")
                            else:
                                level = SuspicionLevel.LOW
                                primary_event = EventType.ABANDONED_OBJECT
                                pipeline_stage = AlertPipelineStage.OBSERVATION
                                rule_conf = 0.55
                                reasons.append(f"owner_{track.owner_id}_moved_away")
                                reasons.append("initial_unattended_observation")
                        else:
                            # Owner is nearby (< departure threshold) -> NOT ABANDONED
                            level = SuspicionLevel.NORMAL
                            primary_event = EventType.ABANDONED_OBJECT
                            pipeline_stage = AlertPipelineStage.OBSERVATION
                            rule_conf = 0.20
                            reasons.append(f"owner_{track.owner_id}_in_close_proximity_{track.owner_distance:.1f}px")
                            reasons.append("object_supervised_by_owner")
                    else:
                        # Unknown Owner Object
                        primary_event = EventType.UNKNOWN_OWNER_OBJECT
                        if unattended_dur >= self.unattended_duration_thresh_sec:
                            level = SuspicionLevel.MEDIUM
                            pipeline_stage = AlertPipelineStage.CANDIDATE
                            is_suspicious = True
                            rule_conf = 0.75
                            active_groups.add("G")
                            reasons.append("object_detected_without_associated_owner")
                            reasons.append(f"remains_stationary_{track.stationary_duration:.1f}s")
                            reasons.append("isolation_from_active_pedestrians")
                        else:
                            level = SuspicionLevel.OBSERVE
                            pipeline_stage = AlertPipelineStage.OBSERVATION
                            rule_conf = 0.45
                            reasons.append("object_discovered_no_owner_in_immediate_vicinity")
                            reasons.append("observing_temporal_persistence")

                    # Spatial Context Anomaly Escalation
                    if track.context_anomaly_score >= 0.50:
                        active_groups.add("F")  # Spatial Context
                        reasons.append(f"unusual_placement_in_zone_{track.context_zone}")
                        if level == SuspicionLevel.MEDIUM:
                            level = SuspicionLevel.HIGH
                else:
                    # Object is moving
                    if EventType.UNEXPLAINED_OBJECT_MOVEMENT in track.active_events:
                        level = SuspicionLevel.LOW
                        primary_event = EventType.UNEXPLAINED_OBJECT_MOVEMENT
                        pipeline_stage = AlertPipelineStage.OBSERVATION
                        rule_conf = 0.50
                        reasons.append("object_displacement_detected_without_visible_person")
                    elif track.owner_id is not None:
                        level = SuspicionLevel.NORMAL
                        primary_event = EventType.HELD_OBJECT
                        pipeline_stage = AlertPipelineStage.OBSERVATION
                        rule_conf = 0.15
                        reasons.append(f"object_carried_by_owner_{track.owner_id}")

        # -------------------------------------------------------------------
        # Branch 2: Persons (Walking, Running, Loitering, Crowds, Interactions)
        # -------------------------------------------------------------------
        elif track.is_person:
            active_groups.add("B")  # Person Behavior

            # 2A. Fall / Collapse Event
            if EventType.FALL_EVENT in track.active_events:
                pos_votes, win_len = track.record_temporal_vote("fall_event", True, window_size=8)
                is_confirmed_fall = (pos_votes >= 5) or (track_dur < 1.0 and pos_votes >= 2)
                rule_conf = 0.85 if is_confirmed_fall else 0.50
                level = SuspicionLevel.HIGH if is_confirmed_fall else SuspicionLevel.OBSERVE
                primary_event = EventType.FALL_EVENT
                pipeline_stage = AlertPipelineStage.CONFIRMATION if is_confirmed_fall else AlertPipelineStage.OBSERVATION
                is_suspicious = is_confirmed_fall
                reasons.append("rapid_vertical_aspect_ratio_collapse_detected")
                reasons.append("ground_level_posture_assumed")
                reasons.append("potential_safety_incident_monitoring")

            # 2B. Boundary Crossing into Restricted Zone
            elif EventType.BOUNDARY_CROSSING in track.active_events:
                level = SuspicionLevel.HIGH
                primary_event = EventType.BOUNDARY_CROSSING
                pipeline_stage = AlertPipelineStage.CONFIRMATION
                is_suspicious = True
                rule_conf = 0.85
                active_groups.update(["B", "F"])
                reasons.append(f"unauthorized_boundary_crossing_into_restricted_zone_{track.context_zone}")

            # 2C. Vehicle-Pedestrian Near Miss Hazard
            elif EventType.VEHICLE_PEDESTRIAN_NEAR_MISS in track.active_events:
                level = SuspicionLevel.HIGH
                primary_event = EventType.VEHICLE_PEDESTRIAN_NEAR_MISS
                pipeline_stage = AlertPipelineStage.CONFIRMATION
                is_suspicious = True
                rule_conf = 0.86
                active_groups.update(["B", "E"])
                reasons.append("pedestrian_involved_in_hazardous_vehicle_near_miss")

            # 2D. Rapid Crowd Dispersal
            elif EventType.RAPID_CROWD_DISPERSAL in track.active_events:
                level = SuspicionLevel.HIGH
                primary_event = EventType.RAPID_CROWD_DISPERSAL
                pipeline_stage = AlertPipelineStage.CONFIRMATION
                is_suspicious = True
                rule_conf = 0.88
                active_groups.update(["B", "G"])
                reasons.append("rapid_crowd_dispersal_detected_high_speed_outward_movement")

            # 2E. Group Convergence
            elif EventType.GROUP_CONVERGENCE in track.active_events:
                level = SuspicionLevel.MEDIUM
                primary_event = EventType.GROUP_CONVERGENCE
                pipeline_stage = AlertPipelineStage.CANDIDATE
                is_suspicious = False
                rule_conf = 0.72
                active_groups.update(["B", "G"])
                reasons.append("multiple_persons_converging_toward_common_centroid")

            # 2F. Coordinated Multi-Person Arrival
            elif EventType.COORDINATED_ARRIVAL in track.active_events:
                level = SuspicionLevel.MEDIUM
                primary_event = EventType.COORDINATED_ARRIVAL
                pipeline_stage = AlertPipelineStage.CANDIDATE
                is_suspicious = False
                rule_conf = 0.72
                active_groups.update(["B", "D", "G"])
                reasons.append("coordinated_multi_person_arrival_from_diverse_origins")

            # 2G. Object Transfer between Persons
            elif EventType.OBJECT_TRANSFER in track.active_events:
                level = SuspicionLevel.MEDIUM
                primary_event = EventType.OBJECT_TRANSFER
                pipeline_stage = AlertPipelineStage.CANDIDATE
                is_suspicious = False
                rule_conf = 0.75
                active_groups.update(["B", "D"])
                reasons.append("person_involved_in_close_proximity_object_transfer")

            # 2H. Persistent Following
            elif EventType.PERSISTENT_FOLLOWING in track.active_events and track.following_target_id is not None:
                level = SuspicionLevel.MEDIUM
                primary_event = EventType.PERSISTENT_FOLLOWING
                pipeline_stage = AlertPipelineStage.CANDIDATE
                is_suspicious = False  # Behavioral anomaly, not an alarm alone
                rule_conf = 0.75
                active_groups.add("D")
                reasons.append(f"persistent_trajectory_following_person_{track.following_target_id}")
                reasons.append(f"consistent_inter_distance_maintained_{track.following_duration:.1f}s")
                reasons.append("behavioral_monitoring_active")

            # 2I. Loitering in Area
            elif EventType.LOITERING in track.active_events or EventType.LOITERING_EVENT in track.active_events:
                is_restricted = track.context_anomaly_score >= 0.50
                level = SuspicionLevel.MEDIUM if is_restricted else SuspicionLevel.LOW
                primary_event = EventType.LOITERING_EVENT
                pipeline_stage = AlertPipelineStage.CANDIDATE if is_restricted else AlertPipelineStage.OBSERVATION
                is_suspicious = is_restricted
                rule_conf = 0.80 if is_restricted else 0.65
                if is_restricted:
                    active_groups.add("F")
                reasons.append(f"prolonged_dwell_time_{track.dwell_time:.1f}s")
                reasons.append("constrained_net_spatial_displacement")
                reasons.append("repeated_localized_movement_pattern")
                if is_restricted:
                    reasons.append(f"loitering_inside_sensitive_zone_{track.context_zone}")

            # 2J. Erratic / Zigzag Movement
            elif EventType.ERRATIC_MOVEMENT in track.active_events:
                is_restricted = track.context_anomaly_score >= 0.50
                level = SuspicionLevel.HIGH if is_restricted else SuspicionLevel.MEDIUM
                primary_event = EventType.ERRATIC_MOVEMENT
                pipeline_stage = AlertPipelineStage.CONFIRMATION if is_restricted else AlertPipelineStage.CANDIDATE
                is_suspicious = is_restricted
                rule_conf = 0.82 if is_restricted else 0.72
                active_groups.add("B")
                if is_restricted:
                    active_groups.add("F")
                reasons.append("chaotic_oscillating_trajectory_frequent_direction_reversals")

            # 2K. Opposing Crowd Flow
            elif EventType.OPPOSING_CROWD_FLOW in track.active_events:
                is_sustained = track_dur >= 2.0
                level = SuspicionLevel.MEDIUM if is_sustained else SuspicionLevel.OBSERVE
                primary_event = EventType.OPPOSING_CROWD_FLOW
                pipeline_stage = AlertPipelineStage.CANDIDATE if is_sustained else AlertPipelineStage.OBSERVATION
                rule_conf = 0.80 if is_sustained else 0.60
                is_suspicious = is_sustained
                active_groups.add("G")
                reasons.append("movement_direction_opposes_prevailing_crowd_flow")
                reasons.append("directional_anomaly_confirmed")

            # 2L. Running (Zero Alerts Alone, Escalate if Near Incident)
            elif track.person_action == PersonAction.RUNNING:
                has_nearby_incident = any(
                    other.track_id != track.track_id
                    and (getattr(other, "is_alert_active", False) or other.suspicion_level in {SuspicionLevel.HIGH, SuspicionLevel.CRITICAL})
                    and float(np.hypot(track.center[0] - other.center[0], track.center[1] - other.center[1])) <= 250.0
                    for other in all_tracks
                )
                if has_nearby_incident:
                    level = SuspicionLevel.MEDIUM
                    primary_event = EventType.RUNNING_EVENT
                    pipeline_stage = AlertPipelineStage.CANDIDATE
                    is_suspicious = True
                    rule_conf = 0.78
                    active_groups.update(["B", "G"])
                    reasons.append("person_running_away_from_active_incident_area")
                else:
                    level = SuspicionLevel.NORMAL
                    primary_event = EventType.RUNNING_EVENT
                    pipeline_stage = AlertPipelineStage.OBSERVATION
                    rule_conf = 0.80
                    reasons.append("person_running_detected")
                    reasons.append("ordinary_activity_or_transit_exercise_zero_alerts")

            # 2M. Sudden Stop
            elif EventType.SUDDEN_STOP in track.active_events:
                level = SuspicionLevel.NORMAL
                primary_event = EventType.SUDDEN_STOP
                pipeline_stage = AlertPipelineStage.OBSERVATION
                rule_conf = 0.70
                reasons.append("sudden_deceleration_to_stop_detected")

            # 2N. Normal Walking / Standing
            else:
                level = SuspicionLevel.NORMAL
                pipeline_stage = AlertPipelineStage.OBSERVATION
                rule_conf = 0.10
                reasons.append("normal_pedestrian_trajectory")


        # -------------------------------------------------------------------
        # Branch 3: Vehicles (Speed, Direction, Interactions)
        # -------------------------------------------------------------------
        elif track.is_vehicle:
            active_groups.add("E")  # Vehicle Behavior

            pedestrian_dist = 999.0
            for other in all_tracks:
                if other.is_person and other.track_id != track.track_id:
                    d = float(np.hypot(track.center[0] - other.center[0], track.center[1] - other.center[1]))
                    if d < pedestrian_dist:
                        pedestrian_dist = d

            # 3A. Vehicle-Pedestrian Near Miss Hazard
            if EventType.VEHICLE_PEDESTRIAN_NEAR_MISS in track.active_events:
                level = SuspicionLevel.HIGH
                is_suspicious = True
                primary_event = EventType.VEHICLE_PEDESTRIAN_NEAR_MISS
                pipeline_stage = AlertPipelineStage.CONFIRMATION
                rule_conf = 0.88
                active_groups.update(["E", "B"])
                reasons.append(f"vehicle_high_speed_near_miss_with_pedestrian_{pedestrian_dist:.1f}px")

            # 3B. Vehicle Approach and Flee
            elif EventType.VEHICLE_APPROACH_FLEE in track.active_events:
                has_ped_threat = pedestrian_dist <= 120.0
                level = SuspicionLevel.HIGH if has_ped_threat else SuspicionLevel.MEDIUM
                is_suspicious = has_ped_threat
                primary_event = EventType.VEHICLE_APPROACH_FLEE
                pipeline_stage = AlertPipelineStage.CONFIRMATION if has_ped_threat else AlertPipelineStage.CANDIDATE
                rule_conf = 0.85 if has_ped_threat else 0.75
                active_groups.update(["E", "G"])
                if has_ped_threat:
                    active_groups.update(["B", "D"])
                reasons.append("vehicle_brief_stop_followed_by_rapid_escape_acceleration")

            # 3C. Vehicle Circling / Area Patrol
            elif EventType.VEHICLE_CIRCLING in track.active_events:
                level = SuspicionLevel.MEDIUM
                is_suspicious = False
                primary_event = EventType.VEHICLE_CIRCLING
                pipeline_stage = AlertPipelineStage.CANDIDATE
                rule_conf = 0.74
                active_groups.update(["E", "G"])
                reasons.append("repetitive_spatial_circling_or_patrol_pattern_detected")

            # 3D. Wrong-Way Vehicle Travel
            elif self.enable_wrong_way and (
                track.is_wrong_direction
                or EventType.WRONG_DIRECTION_VEHICLE in track.active_events
                or EventType.WRONG_DIRECTION_EVENT in track.active_events
            ):
                pos_votes, win_len = track.record_temporal_vote("wrong_way", True, window_size=5)
                has_support = pos_votes >= 3
                is_fast = track.speed >= self.wrong_direction_speed_thresh
                has_pedestrian_threat = pedestrian_dist <= 120.0

                if has_support and is_fast and has_pedestrian_threat:
                    level = SuspicionLevel.HIGH
                    is_suspicious = True
                    primary_event = EventType.HIGH_RISK_TRAFFIC_EVENT
                    pipeline_stage = AlertPipelineStage.CONFIRMATION
                    rule_conf = 0.88
                    active_groups.add("B")
                elif has_support:
                    level = SuspicionLevel.MEDIUM
                    is_suspicious = False
                    primary_event = EventType.WRONG_DIRECTION_EVENT
                    pipeline_stage = AlertPipelineStage.CANDIDATE
                    rule_conf = 0.75
                else:
                    level = SuspicionLevel.OBSERVE
                    primary_event = EventType.WRONG_DIRECTION_EVENT
                    pipeline_stage = AlertPipelineStage.OBSERVATION
                    rule_conf = 0.50

                reasons.append("vehicle_trajectory_opposes_prevailing_traffic_flow")
                if is_fast:
                    reasons.append(f"elevated_speed_in_wrong_direction_{track.speed:.1f}px/s")
                if has_pedestrian_threat:
                    reasons.append(f"pedestrian_proximity_threat_{pedestrian_dist:.1f}px")

            # 3E. Abnormal Speed
            elif EventType.ABNORMAL_VEHICLE_SPEED in track.active_events:
                is_speed_hazard = track.speed >= self.speed_hazard_thresh
                level = SuspicionLevel.MEDIUM if is_speed_hazard else SuspicionLevel.LOW
                primary_event = EventType.ABNORMAL_VEHICLE_SPEED
                pipeline_stage = AlertPipelineStage.CANDIDATE if is_speed_hazard else AlertPipelineStage.OBSERVATION
                rule_conf = 0.80 if is_speed_hazard else 0.60
                is_suspicious = is_speed_hazard
                if is_speed_hazard:
                    active_groups.add("G")
                reasons.append("speed_significantly_deviates_from_traffic_distribution")
                if is_speed_hazard:
                    reasons.append(f"elevated_speed_hazard_{track.speed:.1f}px_s")

            # 3F. Abrupt Stop or Stoppage Hazard
            elif EventType.ABRUPT_VEHICLE_STOP in track.active_events or EventType.ABRUPT_STOP in track.active_events:
                is_sustained = getattr(track, "stationary_duration", 0.0) >= 2.0 or getattr(track, "vehicle_stop_duration", 0.0) >= 2.0
                level = SuspicionLevel.MEDIUM if is_sustained else SuspicionLevel.OBSERVE
                primary_event = EventType.ABRUPT_STOP
                pipeline_stage = AlertPipelineStage.CANDIDATE if is_sustained else AlertPipelineStage.OBSERVATION
                rule_conf = 0.80 if is_sustained else 0.65
                is_suspicious = is_sustained
                reasons.append("rapid_deceleration_to_halt_detected")
                if is_sustained:
                    active_groups.add("C")
                    active_groups.add("G")
                    reasons.append(f"prolonged_stationary_road_stoppage_{track.stationary_duration:.1f}s")

            # 3G. Vehicle-Person Interaction
            elif EventType.VEHICLE_PERSON_INTERACTION in track.active_events:
                is_sustained = getattr(track, "vehicle_stop_duration", 0.0) >= 5.0
                level = SuspicionLevel.MEDIUM if is_sustained and track.context_anomaly_score >= 0.50 else SuspicionLevel.LOW
                primary_event = EventType.VEHICLE_PERSON_INTERACTION
                pipeline_stage = AlertPipelineStage.CANDIDATE if level == SuspicionLevel.MEDIUM else AlertPipelineStage.OBSERVATION
                rule_conf = 0.70 if level == SuspicionLevel.MEDIUM else 0.50
                is_suspicious = level == SuspicionLevel.MEDIUM
                active_groups.update(["E", "B", "D"])
                reasons.append("stopped_vehicle_in_close_proximity_to_pedestrian")

            # 3H. Trajectory Anomaly / U-Turn (DISABLED per user instruction)
            elif EventType.VEHICLE_TRAJECTORY_ANOMALY in track.active_events:
                # Per user instruction, vehicle U-turns and trajectory reversals are normal maneuvers, NOT alerts
                level = SuspicionLevel.NORMAL
                pipeline_stage = AlertPipelineStage.OBSERVATION
                rule_conf = 0.10
                reasons.append("normal_vehicle_travel")

            else:
                level = SuspicionLevel.NORMAL
                pipeline_stage = AlertPipelineStage.OBSERVATION
                rule_conf = 0.10
                reasons.append("normal_vehicle_travel")

        # -------------------------------------------------------------------
        # 4. Multi-Signal Event Confidence Calculation
        # -------------------------------------------------------------------
        if primary_event == EventType.ABANDONED_OBJECT and not track.is_person and not track.is_vehicle:
            # Weighted Scoring Formula:
            # object_detection (15%) + owner_association (15%) + placement (15%) +
            # stationarity (20%) + owner_departure (15%) + owner_distance (10%) + temporal_persistence (10%)
            s_det = min(1.0, det_conf / 0.85)
            s_assoc = min(1.0, track.association_confidence / 0.80) if track.association_confidence else (0.80 if track.owner_id is not None else 0.40)
            s_placement = 0.90 if track.timeline.get("t_placement") else 0.70
            s_stat = 1.0 if track.is_stationary else 0.0
            s_dep = min(1.0, track.owner_distance / self.owner_departure_dist_px) if (track.owner_distance and track.owner_id) else (0.85 if track.owner_id is None and track.unattended_duration >= 15.0 else 0.0)
            s_dist = min(1.0, track.owner_distance / (self.owner_departure_dist_px * 1.5)) if track.owner_distance else 0.0
            s_temp = min(1.0, track.unattended_duration / self.unattended_duration_thresh_sec)

            event_conf = (
                (0.15 * s_det)
                + (0.15 * s_assoc)
                + (0.15 * s_placement)
                + (0.20 * s_stat)
                + (0.15 * s_dep)
                + (0.10 * s_dist)
                + (0.10 * s_temp)
            )
        elif primary_event == EventType.HELD_OBJECT:
            event_conf = 0.15  # Normal event, zero alert confidence
        else:
            level_base = {
                SuspicionLevel.NORMAL: 0.10,
                SuspicionLevel.OBSERVE: 0.30,
                SuspicionLevel.LOW: 0.50,
                SuspicionLevel.MEDIUM: 0.70,
                SuspicionLevel.HIGH: 0.88,
                SuspicionLevel.CRITICAL: 0.98,
            }[level]
            event_conf = min(0.98, (0.35 * det_conf) + (0.35 * rule_conf) + (0.30 * level_base))

        track.event_confidence = round(float(event_conf), 3)

        # -------------------------------------------------------------------
        # 5. Alert Confidence Calculation (Hard to reach)
        # -------------------------------------------------------------------
        # Alert confidence requires accumulated, persistent, mutually consistent evidence
        if primary_event in {EventType.HELD_OBJECT, EventType.RUNNING_EVENT}:
            raw_alert_conf = 0.10
        else:
            group_bonus = min(0.15, len(active_groups) * 0.04)
            raw_alert_conf = track.event_confidence * 0.85 + group_bonus

        track.alert_confidence = round(float(min(0.99, max(0.0, raw_alert_conf))), 3)
        track.suspiciousness_score = track.event_confidence
        track.rule_confidence = round(float(rule_conf), 3)
        track.suspicion_level = level
        track.primary_event = primary_event
        track.explainability_reasons = reasons
        track.evidence_groups = sorted(list(active_groups))
        track.pipeline_stage = pipeline_stage

        if is_suspicious and not track.alert_triggered and level in {SuspicionLevel.HIGH, SuspicionLevel.CRITICAL}:
            track.timeline["t_alert"] = timestamp

        # Update ObjectState
        if track.alert_triggered or getattr(track, "is_alert_active", False):
            track.state = ObjectState.ALERTED
            track.pipeline_stage = AlertPipelineStage.ALERT
        elif level in {SuspicionLevel.HIGH, SuspicionLevel.CRITICAL}:
            track.state = ObjectState.SUSPICIOUS
        elif level == SuspicionLevel.MEDIUM:
            track.state = ObjectState.UNATTENDED if (not track.is_person and not track.is_vehicle) else ObjectState.TRACKED
        elif not track.is_person and not track.is_vehicle:
            if track.owner_distance >= self.owner_departure_dist_px and track.owner_id is not None and track.is_stationary:
                track.state = ObjectState.OWNER_DEPARTING
            elif track.is_stationary:
                track.state = ObjectState.STATIONARY
            elif track.owner_id is not None:
                track.state = ObjectState.ASSOCIATED
            else:
                track.state = ObjectState.TRACKED
        else:
            track.state = ObjectState.NORMAL

        band_name, band_action = self.get_confidence_band(track.rule_confidence)

        track.score_breakdown = {
            "detector_confidence": round(track.detector_confidence, 3),
            "smoothed_detector_confidence": round(track.smoothed_detector_confidence, 3),
            "rule_confidence": round(track.rule_confidence, 3),
            "event_confidence": round(track.event_confidence, 3),
            "alert_confidence": round(track.alert_confidence, 3),
            "rule_band": band_name,
            "pipeline_stage": track.pipeline_stage.value,
        }

        event_str = primary_event.value if primary_event else (
            "PEDESTRIAN_ACTIVITY" if track.is_person else ("VEHICLE_TRAVEL" if track.is_vehicle else "OBJECT_MONITORED")
        )

        return {
            "suspicion": is_suspicious,
            "level": level.value,
            "event": event_str,
            "track_id": track.track_id,
            "class_name": track.class_name,
            "pipeline_stage": track.pipeline_stage.value,
            "reasons": reasons,
            "evidence_groups": track.evidence_groups,
            "confidences": {
                "detector_confidence": round(track.detector_confidence, 2),
                "smoothed_detector_confidence": round(track.smoothed_detector_confidence, 2),
                "rule_confidence": round(track.rule_confidence, 2),
                "event_confidence": round(track.event_confidence, 2),
                "alert_confidence": round(track.alert_confidence, 2),
                "tracker_confidence": round(track.tracker_confidence, 2),
                "association_confidence": round(track.association_confidence, 2),
                "behavior_confidence": round(track.behavior_confidence, 2),
            },
            "rule_confidence_band": {
                "band": band_name,
                "recommended_action": band_action,
            },
            "timeline": track.timeline,
            "disclaimer": "Visual/behavioral detection; does not establish intent or legal offense.",
        }

    def should_alert(
        self,
        track: TrackState,
        all_tracks: List[TrackState],
        timestamp: float,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Master 8-Stage Alert Gating Architecture:
        1. Detector Confidence Gate
        2. Track Duration Gate
        3. Event-Specific Mandatory Rules (Hard Gates)
        4. Temporal Confirmation Gate (Voting)
        5. Independent Evidence Group Count Gate (2/3 Rule)
        6. Hysteresis State Machine & Alert Confidence Gate (Enter >= 0.85, Maintain >= 0.70)
        7. Cooldown Gate (with severity escalation bypass)
        """
        track_duration = max(0.0, timestamp - track.first_seen_time)
        meta: Dict[str, Any] = {
            "track_id": track.track_id,
            "event": track.primary_event.value if track.primary_event else "UNKNOWN",
            "level": track.suspicion_level.value,
            "pipeline_stage": track.pipeline_stage.value,
            "confidences": {
                "detector": track.detector_confidence,
                "smoothed_detector": track.smoothed_detector_confidence,
                "rule": track.rule_confidence,
                "event": track.event_confidence,
                "alert": track.alert_confidence,
            },
            "evidence_groups": track.evidence_groups,
            "hard_gates_passed": False,
        }

        # --- Gate 1: Detector Confidence Gate ---
        min_det = self.min_det_conf_weapon if track.is_weapon_candidate else (
            self.min_det_conf_vehicle if track.is_vehicle else (
                self.min_det_conf_person if track.is_person else self.min_det_conf_object
            )
        )
        det_score = track.smoothed_detector_confidence or track.detector_confidence
        if det_score < min_det:
            return False, f"Detector confidence {det_score:.2f} < {min_det:.2f} threshold", meta

        # --- Gate 2: Track Duration Gate ---
        min_dur = 5.0 if (track.is_weapon_candidate or track.is_vehicle) else 3.0
        if track_duration < min_dur:
            return False, f"Track duration {track_duration:.1f}s < {min_dur:.1f}s minimum", meta

        # --- Gate 3: Event-Specific Mandatory Hard Gates ---
        # 3A. Held Object (Carried bag/package generates zero alerts)
        if track.primary_event == EventType.HELD_OBJECT or (track.is_held and not track.is_weapon_candidate):
            return False, "Hard Gate: Carried or held object is normal activity (zero alerts)", meta

        # 3B. Running alone never alerts
        if track.primary_event == EventType.RUNNING_EVENT or track.person_action == PersonAction.RUNNING:
            if "person_running_away_from_active_incident_area" not in track.explainability_reasons:
                return False, "Hard Gate: Running alone is non-suspicious baseline activity", meta

        # 3C. Weapon Carried Normally
        if track.primary_event == EventType.WEAPON_HELD and track.aiming_target_id is None:
            return False, "Hard Gate: Weapon carried normally is monitored (no alert without hostile interaction)", meta

        # 3D. Physical Object Stationarity & Departure Hard Gate
        if not track.is_person and not track.is_vehicle and not track.is_weapon_candidate:
            if not track.is_stationary:
                return False, "Hard Gate: Object is not ground-stationary", meta
            if track.owner_id is not None and track.owner_distance < self.owner_departure_dist_px:
                return False, f"Hard Gate: Owner remains in close proximity ({track.owner_distance:.1f}px < {self.owner_departure_dist_px:.1f}px)", meta
            if track.unattended_duration < self.unattended_duration_thresh_sec:
                return False, f"Hard Gate: Unattended duration {track.unattended_duration:.1f}s < {self.unattended_duration_thresh_sec:.1f}s threshold", meta

        # Block vehicle U-turn or reversal trajectory rule per user request
        if track.primary_event == EventType.VEHICLE_TRAJECTORY_ANOMALY:
            return False, "Vehicle U-turn or reversal trajectory rule is disabled per user request", meta

        # 3E. Behavior events alone never alert without corroboration
        if track.primary_event in {
            EventType.LOITERING_EVENT,
            EventType.LOITERING,
            EventType.PERSISTENT_FOLLOWING,
            EventType.OPPOSING_CROWD_FLOW,
            EventType.SUDDEN_STOP,
            EventType.SUDDEN_ACCELERATION,
            EventType.ABRUPT_STOP,
            EventType.ABNORMAL_VEHICLE_SPEED,
            EventType.ERRATIC_MOVEMENT,
            EventType.GROUP_CONVERGENCE,
            EventType.COORDINATED_ARRIVAL,
            EventType.OBJECT_TRANSFER,
            EventType.VEHICLE_CIRCLING,
            EventType.VEHICLE_APPROACH_FLEE,
            EventType.VEHICLE_PERSON_INTERACTION,
        }:
            # Require multi-group evidence corroboration (2/3 rule) or contextual anomaly
            if track.context_anomaly_score < 0.50 and len(track.evidence_groups) < 2:
                return False, f"Hard Gate: {track.primary_event.value} requires corroborating multi-group evidence", meta

        # 3F. Unknown Owner Weapon
        if track.primary_event == EventType.UNKNOWN_OWNER_WEAPON:
            if not track.is_stationary:
                return False, "Hard Gate: Weapon is not ground-stationary", meta
            if track_duration < 15.0:
                return False, f"Hard Gate: Unknown owner weapon persistence {track_duration:.1f}s < 15.0s threshold", meta

        # 3G. Wrong-Direction Vehicle
        if track.primary_event in {EventType.WRONG_DIRECTION_VEHICLE, EventType.WRONG_DIRECTION_EVENT}:
            if not self.enable_wrong_way:
                return False, "Hard Gate: Wrong-direction vehicle evaluation disabled", meta
            has_speed = track.speed >= self.wrong_direction_speed_thresh
            has_peds = any(
                o.is_person and np.hypot(track.center[0] - o.center[0], track.center[1] - o.center[1]) <= 120.0
                for o in all_tracks
            )
            if not (has_speed and has_peds):
                return False, "Hard Gate: Wrong-direction vehicle requires high-speed pedestrian proximity threat", meta

        meta["hard_gates_passed"] = True

        # --- Gate 4: Temporal Confirmation Gate ---
        if track.is_weapon_candidate and "weapon_detection" in track.temporal_votes:
            pos_votes = sum(1 for v in track.temporal_votes["weapon_detection"] if v)
            req_votes = self.vote_configs.get("weapon_like", {}).get("required", 7)
            if len(track.temporal_votes["weapon_detection"]) >= 5 and pos_votes < req_votes:
                return False, f"Temporal voting: weapon votes ({pos_votes}) < {req_votes} required", meta

        # --- Gate 5: Independent Evidence Group Count Gate (2/3 Rule) ---
        required_groups = 3 if track.suspicion_level in {SuspicionLevel.HIGH, SuspicionLevel.CRITICAL} else 2
        if len(track.evidence_groups) < required_groups:
            return False, f"Evidence groups {track.evidence_groups} ({len(track.evidence_groups)}) < {required_groups} required", meta

        # --- Gate 5B: Suspicion Severity Gate (Master alerts reserved for MEDIUM, HIGH, and CRITICAL events) ---
        if track.suspicion_level not in {SuspicionLevel.MEDIUM, SuspicionLevel.HIGH, SuspicionLevel.CRITICAL}:
            return False, f"Suspicion level {track.suspicion_level.value} below alert severity threshold (requires MEDIUM+)", meta

        # --- Gate 6: Hysteresis State Machine & Alert Confidence Gate ---
        if track.is_alert_active:
            # While active, maintain alert as long as alert_confidence >= hysteresis_maintain (0.70)
            if track.alert_confidence < self.hysteresis_maintain:
                # v2 R6.5: clear after the confidence stayed low for clear_frames at 20 fps worth of
                # capture time ((N - 1) / 20 s) — the same as N frames at 20 fps, at any rate
                track.alert_clearing_counter += 1
                if track.alert_clearing_since is None:
                    track.alert_clearing_since = timestamp
                clear_s = max(0.0, (self.hysteresis_clear_frames - 1) / 20.0)
                if timestamp - track.alert_clearing_since >= clear_s - 0.025:
                    track.is_alert_active = False
                    track.alert_clearing_counter = 0
                    track.alert_clearing_since = None
                    return False, "Alert cleared by hysteresis (confidence dropped below maintain threshold)", meta
                else:
                    # Still in grace period, maintain alert
                    pass
            else:
                track.alert_clearing_counter = 0
                track.alert_clearing_since = None
        else:
            # Enter alert only if event_confidence >= 0.85 and alert_confidence >= 0.85
            if track.event_confidence < self.hysteresis_enter or track.alert_confidence < self.hysteresis_enter:
                return False, f"Confidence below alert entry threshold (event={track.event_confidence:.2f}, alert={track.alert_confidence:.2f} < {self.hysteresis_enter})", meta
            track.is_alert_active = True
            track.alert_clearing_counter = 0
            track.alert_clearing_since = None

        # --- Gate 7: Cooldown Gate with Escalation Bypass ---
        in_cooldown = (timestamp - track.last_alert_time) < self.cooldown_seconds
        is_escalation = (
            track.last_alerted_level is not None
            and track.suspicion_level == SuspicionLevel.CRITICAL
            and track.last_alerted_level != SuspicionLevel.CRITICAL
        )

        if in_cooldown and not is_escalation:
            return False, f"Entity in cooldown ({timestamp - track.last_alert_time:.1f}s / {self.cooldown_seconds:.1f}s)", meta

        # All Gates Satisfied!
        track.last_alerted_level = track.suspicion_level
        track.pipeline_stage = AlertPipelineStage.ALERT
        return True, "All alert gates satisfied with multi-signal confirmation", meta

    def generate_narrative_explanation(self, track: TrackState) -> str:
        """
        Generates a human-readable narrative paragraph explaining WHY an entity was flagged,
        incorporating confidences, pipeline stage, and evidence groups.
        """
        if not track.explainability_reasons:
            return f"{track.class_name.upper()} #{track.track_id} is exhibiting normal baseline activity."

        event_name = track.primary_event.value if track.primary_event else "MONITORED_ACTIVITY"
        groups_str = f" [Evidence Groups: {', '.join(track.evidence_groups)}]" if track.evidence_groups else ""
        header = f"[{track.suspicion_level.value} — {event_name} | Stage: {track.pipeline_stage.value}]{groups_str} for {track.class_name.upper()} #{track.track_id}:"

        reasons_bulleted = "; ".join(r.replace("_", " ") for r in track.explainability_reasons[:4])
        narrative = f"{header} {reasons_bulleted}."

        narrative += f" [Confidences: Det={track.detector_confidence:.2f}, Rule={track.rule_confidence:.2f}, Evt={track.event_confidence:.2f}, Alert={track.alert_confidence:.2f}]."

        if track.timeline.get("t_first_seen") and track.timeline.get("t_unattended"):
            narrative += f" (Timeline: first seen at {track.timeline['t_first_seen']:.1f}s, unattended since {track.timeline['t_unattended']:.1f}s)."
        if track.unattended_duration > 0.0:
            narrative += f" (Unattended duration: {track.unattended_duration:.1f}s)."

        return narrative
