"""
Person behavior analysis module for drone surveillance.
Evaluates individual actions: walking, running, loitering, sudden stops,
sudden speed changes, and falling/collapse events.
"""

from typing import Dict, List, Optional
import numpy as np

from tracking.track_state import EventType, PersonAction, TrackState


class PersonBehaviorAnalyzer:
    """
    Analyzes person kinematics and posture variations over time to classify
    actions and generate behavioral anomaly signals.
    """

    def __init__(
        self,
        running_speed_thresh: float = 90.0,      # Ground px/sec
        walking_min_speed: float = 12.0,         # Below this is considered standing/dwelling
        loiter_dwell_thresh_sec: float = 15.0,   # Seconds in area before loitering evaluation
        loiter_max_displacement: float = 80.0,   # Max net spatial displacement for loitering
        sudden_accel_thresh: float = 60.0,       # px/s^2 for sudden acceleration
        sudden_stop_thresh: float = -50.0,       # px/s^2 for abrupt stopping
    ):
        self.running_speed_thresh = running_speed_thresh
        self.walking_min_speed = walking_min_speed
        self.loiter_dwell_thresh_sec = loiter_dwell_thresh_sec
        self.loiter_max_displacement = loiter_max_displacement
        self.sudden_accel_thresh = sudden_accel_thresh
        self.sudden_stop_thresh = sudden_stop_thresh

        # History of aspect ratios for fall detection: track_id -> [aspect_ratio, ...]
        self.aspect_histories: Dict[int, List[float]] = {}
        # Prior speed tracking for sudden transitions: track_id -> [speed, ...]
        self.speed_histories: Dict[int, List[float]] = {}

    def analyze(
        self,
        person: TrackState,
        timestamp: float,
        fps: float = 30.0,
    ):
        """
        Analyzes a single person track and updates `person_action`, flags, and active events.
        """
        tid = person.track_id
        speed = person.speed
        accel = person.acceleration

        # Update speed history
        if tid not in self.speed_histories:
            self.speed_histories[tid] = []
        self.speed_histories[tid].append(speed)
        if len(self.speed_histories[tid]) > 30:
            self.speed_histories[tid].pop(0)

        # 1. Fall / Collapse Detection
        # Human standing aspect ratio (h/w) in aerial view is typically 1.3 - 3.0.
        # When collapsed, h/w drops sharply below 0.8
        w = max(1.0, person.width)
        h = max(1.0, person.height)
        ar = h / w

        if tid not in self.aspect_histories:
            self.aspect_histories[tid] = []
        self.aspect_histories[tid].append(ar)
        if len(self.aspect_histories[tid]) > 15:
            self.aspect_histories[tid].pop(0)

        is_fallen = False
        if len(self.aspect_histories[tid]) >= 8:
            baseline_ar = np.median(self.aspect_histories[tid][:5])
            curr_ar = self.aspect_histories[tid][-1]
            # Rapid vertical collapse: previous was upright (>1.3) and dropped below 0.8
            if baseline_ar >= 1.3 and curr_ar <= 0.75:
                is_fallen = True
                person.person_action = PersonAction.FALLEN
                if EventType.FALL_EVENT not in person.active_events:
                    person.active_events.append(EventType.FALL_EVENT)

        if is_fallen:
            return

        # 2. Running vs. Walking vs. Standing
        # Note: Running is a behavioral state, NOT automatically suspicious.
        if speed >= self.running_speed_thresh:
            person.person_action = PersonAction.RUNNING
            person.is_running = True
            if EventType.RAPID_MOVEMENT not in person.active_events:
                person.active_events.append(EventType.RAPID_MOVEMENT)
        elif speed >= self.walking_min_speed:
            person.person_action = PersonAction.WALKING
            person.is_running = False
            if EventType.RAPID_MOVEMENT in person.active_events:
                person.active_events.remove(EventType.RAPID_MOVEMENT)
        else:
            person.person_action = PersonAction.STANDING
            person.is_running = False
            if EventType.RAPID_MOVEMENT in person.active_events:
                person.active_events.remove(EventType.RAPID_MOVEMENT)

        # 3. Sudden Speed Change & Sudden Stop
        if len(self.speed_histories[tid]) >= 5:
            recent_accel = accel
            # Sudden Acceleration
            if recent_accel >= self.sudden_accel_thresh:
                if EventType.RAPID_MOVEMENT not in person.active_events:
                    person.active_events.append(EventType.RAPID_MOVEMENT)

            # Sudden Stop (was moving briskly and stopped abruptly)
            prev_speeds = self.speed_histories[tid][-5:-1]
            if prev_speeds and max(prev_speeds) > 40.0 and speed < 8.0 and recent_accel <= self.sudden_stop_thresh:
                if EventType.SUDDEN_STOP not in person.active_events:
                    person.active_events.append(EventType.SUDDEN_STOP)
            elif speed >= self.walking_min_speed and EventType.SUDDEN_STOP in person.active_events:
                person.active_events.remove(EventType.SUDDEN_STOP)

        # 4. Loitering Detection
        # Defined as: prolonged presence (dwell > threshold) + limited net spatial displacement
        history = person.ground_history if person.ground_history else person.history
        if person.dwell_time >= self.loiter_dwell_thresh_sec and len(history) >= 15:
            start_pos = history[0][:2]
            curr_pos = history[-1][:2]
            net_disp = float(np.hypot(curr_pos[0] - start_pos[0], curr_pos[1] - start_pos[1]))

            if net_disp <= self.loiter_max_displacement:
                person.is_loitering = True
                person.person_action = PersonAction.LOITERING
                if EventType.LOITERING not in person.active_events:
                    person.active_events.append(EventType.LOITERING)
            else:
                person.is_loitering = False
                if EventType.LOITERING in person.active_events:
                    person.active_events.remove(EventType.LOITERING)
        else:
            person.is_loitering = False
            if EventType.LOITERING in person.active_events:
                person.active_events.remove(EventType.LOITERING)

        # 5. Erratic / Zigzag Movement Detection
        # High direction reversals while moving briskly within a confined or oscillating zone
        if getattr(person, "direction_reversals", 0) >= 4 and speed >= self.walking_min_speed and len(history) >= 15:
            xs = [p[0] for p in history]
            ys = [p[1] for p in history]
            spatial_extent = float(np.hypot(max(xs) - min(xs), max(ys) - min(ys)))
            if spatial_extent <= 220.0:
                if EventType.ERRATIC_MOVEMENT not in person.active_events:
                    person.active_events.append(EventType.ERRATIC_MOVEMENT)
            elif EventType.ERRATIC_MOVEMENT in person.active_events and spatial_extent > 260.0:
                person.active_events.remove(EventType.ERRATIC_MOVEMENT)
        elif EventType.ERRATIC_MOVEMENT in person.active_events and getattr(person, "direction_reversals", 0) < 2:
            person.active_events.remove(EventType.ERRATIC_MOVEMENT)

