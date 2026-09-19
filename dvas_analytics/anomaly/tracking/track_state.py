"""
Track state definitions for Drone Suspicious Object Detection System.
Maintains temporal spatial histories, ground-compensated positions, behavior metrics, and cognitive state progression.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class ObjectState(str, Enum):
    NEW = "NEW"
    TRACKED = "TRACKED"
    ASSOCIATED = "ASSOCIATED"
    PLACED = "PLACED"
    STATIONARY = "STATIONARY"
    OWNER_DEPARTING = "OWNER_DEPARTING"
    UNATTENDED = "UNATTENDED"
    SUSPICIOUS = "SUSPICIOUS"
    ALERTED = "ALERTED"
    NORMAL = "NORMAL"


class SuspicionLevel(str, Enum):
    NORMAL = "NORMAL"
    OBSERVE = "OBSERVE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AlertPipelineStage(str, Enum):
    DETECTION = "DETECTION"
    OBSERVATION = "OBSERVATION"
    CANDIDATE = "CANDIDATE"
    CONFIRMATION = "CONFIRMATION"
    ALERT = "ALERT"


class EventType(str, Enum):
    # Object & Abandonment Events
    ABANDONED_OBJECT = "ABANDONED_OBJECT"
    UNKNOWN_OWNER_OBJECT = "UNKNOWN_OWNER_OBJECT"
    UNKNOWN_OWNER_WEAPON = "UNKNOWN_OWNER_WEAPON"
    HELD_OBJECT = "HELD_OBJECT"
    WEAPON_LIKE_OBJECT = "WEAPON_LIKE_OBJECT"
    WEAPON_HELD = "WEAPON_HELD"
    WEAPON_AIMING_CANDIDATE = "WEAPON_AIMING_CANDIDATE"
    SUSPICIOUS_PACKAGE = "SUSPICIOUS_PACKAGE"
    UNUSUAL_OBJECT_PLACEMENT = "UNUSUAL_OBJECT_PLACEMENT"
    OBJECT_RECLAIMED = "OBJECT_RECLAIMED"
    OBJECT_TRANSFER = "OBJECT_TRANSFER"
    UNEXPLAINED_OBJECT_MOVEMENT = "UNEXPLAINED_OBJECT_MOVEMENT"

    # Person Behavior Events
    LOITERING = "LOITERING"
    LOITERING_EVENT = "LOITERING_EVENT"
    OPPOSING_CROWD_FLOW = "OPPOSING_CROWD_FLOW"
    RAPID_MOVEMENT = "RAPID_MOVEMENT"
    RUNNING_EVENT = "RUNNING_EVENT"
    SUDDEN_STOP = "SUDDEN_STOP"
    SUDDEN_ACCELERATION = "SUDDEN_ACCELERATION"
    FALL_EVENT = "FALL_EVENT"
    PERSISTENT_FOLLOWING = "PERSISTENT_FOLLOWING"
    GROUP_CONVERGENCE = "GROUP_CONVERGENCE"
    RAPID_CROWD_DISPERSAL = "RAPID_CROWD_DISPERSAL"

    # Vehicle Events
    WRONG_DIRECTION_VEHICLE = "WRONG_DIRECTION_VEHICLE"
    WRONG_DIRECTION_EVENT = "WRONG_DIRECTION_EVENT"
    HIGH_RISK_TRAFFIC_EVENT = "HIGH_RISK_TRAFFIC_EVENT"
    ABNORMAL_VEHICLE_SPEED = "ABNORMAL_VEHICLE_SPEED"
    ABRUPT_VEHICLE_STOP = "ABRUPT_VEHICLE_STOP"
    ABRUPT_STOP = "ABRUPT_STOP"
    VEHICLE_TRAJECTORY_ANOMALY = "VEHICLE_TRAJECTORY_ANOMALY"
    VEHICLE_PERSON_INTERACTION = "VEHICLE_PERSON_INTERACTION"
    VEHICLE_OBJECT_INTERACTION = "VEHICLE_OBJECT_INTERACTION"
    VEHICLE_OBJECT_ABANDONMENT = "VEHICLE_OBJECT_ABANDONMENT"


class PersonAction(str, Enum):
    WALKING = "WALKING"
    RUNNING = "RUNNING"
    STANDING = "STANDING"
    LOITERING = "LOITERING"
    TURNING = "TURNING"
    PLACING = "PLACING"
    PICKING_UP = "PICKING_UP"
    FOLLOWING = "FOLLOWING"
    DEPARTING = "DEPARTING"
    FALLEN = "FALLEN"


class VehicleAction(str, Enum):
    MOVING = "MOVING"
    STOPPED = "STOPPED"
    TURNING = "TURNING"
    U_TURN = "U_TURN"
    WRONG_WAY = "WRONG_WAY"
    PARKED = "PARKED"


def default_timeline() -> Dict[str, Optional[float]]:
    return {
        "t_first_seen": None,
        "t_association": None,
        "t_interaction": None,
        "t_placement": None,
        "t_stationary": None,
        "t_owner_departure": None,
        "t_unattended": None,
        "t_alert": None,
        "t_resolved": None,
    }


@dataclass
class TrackState:
    """Represents a tracked entity (person, vehicle, or candidate object) over time."""
    track_id: int
    class_name: str
    class_id: int
    bbox: List[float]                  # [x1, y1, x2, y2]
    confidence: float
    first_seen_time: float
    last_seen_time: float
    is_person: bool = False
    is_vehicle: bool = False
    lost_frames: int = 0
    is_matched_this_frame: bool = True

    # Historical trajectories (image coordinates)
    history: List[Tuple[float, float, float]] = field(default_factory=list)  # [(x, y, timestamp), ...]

    # Motion-compensated ground positions
    ground_history: List[Tuple[float, float, float]] = field(default_factory=list)  # [(gx, gy, timestamp), ...]

    # State Machine State & Suspicion Tier
    state: ObjectState = ObjectState.NEW
    state_change_time: float = 0.0
    pipeline_stage: AlertPipelineStage = AlertPipelineStage.DETECTION
    suspicion_level: SuspicionLevel = SuspicionLevel.NORMAL
    primary_event: Optional[EventType] = None
    active_events: List[EventType] = field(default_factory=list)

    # 4-Tier Separated Confidence Architecture & Smoothing
    detector_confidence: float = 0.0
    smoothed_detector_confidence: float = 0.0
    tracker_confidence: float = 0.0
    rule_confidence: float = 0.0
    behavior_confidence: float = 0.0
    event_confidence: float = 0.0
    alert_confidence: float = 0.0
    held_object_confidence: float = 0.0

    # Independent Evidence Groups (A: Object, B: Person, C: Object Behavior, D: Relationship, E: Vehicle, F: Context, G: Temporal)
    evidence_groups: List[str] = field(default_factory=list)

    # Temporal Voting Windows per Rule: {rule_name: [bool, bool, ...]}
    temporal_votes: Dict[str, List[bool]] = field(default_factory=dict)

    # Hysteresis & Alert Cooldown Tracking
    is_alert_active: bool = False
    alert_clearing_counter: int = 0
    last_alerted_level: Optional[SuspicionLevel] = None

    # Temporal Entity Timeline
    timeline: Dict[str, Optional[float]] = field(default_factory=default_timeline)

    # Kinematics (Ground-Relative)
    velocity: Tuple[float, float] = (0.0, 0.0)
    speed: float = 0.0
    acceleration: float = 0.0
    heading_deg: float = 0.0
    dwell_time: float = 0.0
    trajectory_curvature: float = 0.0
    direction_reversals: int = 0

    # Motion & Stationarity Metrics
    ground_speed: float = 0.0          # Current ground-relative speed (px/sec)
    ground_variance: float = 0.0       # Spatial variance over recent window
    stationary_duration: float = 0.0   # Seconds continuously stationary
    is_stationary: bool = False

    # Ownership & Association Metrics
    owner_id: Optional[int] = None
    association_confidence: float = 0.0
    association_time: float = 0.0
    owner_distance: float = 0.0        # Ground distance to owner
    unattended_duration: float = 0.0   # Seconds owner has been separated

    # Behavior States (People)
    person_action: PersonAction = PersonAction.STANDING
    is_running: bool = False
    is_loitering: bool = False
    opposing_crowd_flow: bool = False
    following_target_id: Optional[int] = None
    following_duration: float = 0.0

    # Vehicle States
    vehicle_action: Optional[VehicleAction] = None
    is_wrong_direction: bool = False
    vehicle_stop_duration: float = 0.0

    # Handheld / Weapon Attributes
    is_held: bool = False
    held_by_id: Optional[int] = None
    aiming_target_id: Optional[int] = None
    is_weapon_candidate: bool = False

    # Scene Context
    context_zone: str = "general"
    context_anomaly_score: float = 0.0

    # Reasoning / Explainability
    suspiciousness_score: float = 0.0
    explainability_reasons: List[str] = field(default_factory=list)
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    alert_triggered: bool = False
    last_alert_time: float = -999.0

    def __post_init__(self):
        if self.detector_confidence == 0.0:
            self.detector_confidence = self.confidence
        if self.smoothed_detector_confidence == 0.0:
            self.smoothed_detector_confidence = self.detector_confidence
        if self.tracker_confidence == 0.0:
            self.tracker_confidence = 0.90
        if not self.timeline["t_first_seen"]:
            self.timeline["t_first_seen"] = self.first_seen_time
        # Categorize vehicle classes
        if self.class_name.lower() in {
            "car", "van", "bus", "truck", "motor", "motorcycle", "vehicle",
            "bicycle", "tricycle", "awning-tricycle", "scooter"
        }:
            self.is_vehicle = True

    def update_detector_smoothing(self, current_conf: float, alpha: float = 0.4) -> float:
        """Applies EMA smoothing: smooth = (1 - alpha) * smooth + alpha * current."""
        self.detector_confidence = current_conf
        self.smoothed_detector_confidence = (1.0 - alpha) * self.smoothed_detector_confidence + alpha * current_conf
        return self.smoothed_detector_confidence

    def record_temporal_vote(self, rule_name: str, vote: bool, window_size: int = 5) -> Tuple[int, int]:
        """Records a binary vote into rolling window and returns (positive_votes, window_length)."""
        if rule_name not in self.temporal_votes:
            self.temporal_votes[rule_name] = []
        votes = self.temporal_votes[rule_name]
        votes.append(vote)
        if len(votes) > window_size:
            votes.pop(0)
        return sum(1 for v in votes if v), len(votes)

    @property
    def center(self) -> Tuple[float, float]:
        return (self.bbox[0] + self.bbox[2]) / 2.0, (self.bbox[1] + self.bbox[3]) / 2.0

    @property
    def bottom_center(self) -> Tuple[float, float]:
        """Returns foot/ground contact point: (center_x, bottom_y)."""
        return (self.bbox[0] + self.bbox[2]) / 2.0, self.bbox[3]

    @property
    def width(self) -> float:
        return max(0.0, self.bbox[2] - self.bbox[0])

    @property
    def height(self) -> float:
        return max(0.0, self.bbox[3] - self.bbox[1])

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def vehicle_type(self) -> Optional[str]:
        """Returns specific vehicle sub-type (car, van, motor, bus, truck, etc.) or None."""
        if self.is_vehicle:
            return self.class_name.lower()
        return None

    @property
    def object_type(self) -> Optional[str]:
        """Returns specific object sub-type (backpack, suitcase, handbag, etc.) or None."""
        if not self.is_person and not self.is_vehicle:
            return self.class_name.lower()
        return None

    @property
    def person_type(self) -> Optional[str]:
        """Returns specific human sub-type (pedestrian, people) or None."""
        if self.is_person:
            return self.class_name.lower()
        return None

