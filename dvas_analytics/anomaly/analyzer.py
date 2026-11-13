# -*- coding: utf-8 -*-
"""
DVAS Anomaly Analyzer Engine.
Encapsulates the 9-stage cognitive surveillance pipeline:
1. Camera Ego-Motion Estimation & Compensation (ORB + RANSAC)
2. Dual Aerial Object Detection (YOLO11x VisDrone + Secondary YOLO11n)
3. Multi-Object Tracking with Motion Warping (Hungarian matching, class voting)
4. Ground Reference Mapping & Kinematics
5. Relational & Ownership Associations (Person-Object & Hand-zone)
6. Multi-Entity Behavior Analysis (Loitering, running, fall, following, traffic anomalies)
7. Cognitive Rules Engine with 4 Separated Confidences
8. 8-Stage Alert Gating, Hysteresis, and 2/3 Corroboration
9. Explainable HUD Visualization
"""
import os
import sys
import time
import logging
import threading
from typing import Dict, List, Optional, Tuple, Any

import cv2
import numpy as np

# Ensure local packages can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/analytics")
sys.path.insert(0, "/analytics/anomaly")

from models.model_loader import load_aerial_yolo
from detection.aerial_detector import AerialVisDroneDetector
from motion.camera_motion import CameraMotionEstimator
from tracking.tracker import DroneMultiObjectTracker
from tracking.track_state import EventType, ObjectState, SuspicionLevel, TrackState
from association.owner import OwnershipAssociator
from association.hand_association import HandAssociationEngine
from behavior import TrajectoryAnalyzer, PersonBehaviorAnalyzer, InteractionAnalyzer
from vehicle_behavior import (
    VehicleTrajectoryAnalyzer, VehicleSpeedAnalyzer,
    LaneTrafficAnalyzer, VehicleInteractionAnalyzer, VehicleCirclingDetector
)
from scene import SceneContextEngine
from suspicious.stationary import GroundStationarityEvaluator
from suspicious.rules_engine import SuspiciousEventRulesEngine
from alerts.manager import AlertManager
from visualization.renderer import ExplainableVisualizer
import yaml

log = logging.getLogger("anomaly.analyzer")


# ── Shared detector models (RunPod plan 9.6) ──────────────────────────────────
# YOLO11x VisDrone + YOLO11n bag detector are loaded ONCE per (weights, device,
# imgsz) and shared by every drone's AnomalyAnalyzer; trackers, rules engine,
# scene context etc. stay per drone. Saves ~2 GB VRAM per extra drone and lets
# the stream loop batch the newest frame of all drones through one forward pass.
_shared_detectors: Dict[Tuple[str, str, int], AerialVisDroneDetector] = {}
_shared_lock = threading.Lock()


def get_shared_detector(
    primary_weights_path: str,
    device: str = "cuda",
    conf_thresh: float = 0.35,
    iou_thresh: float = 0.40,
    imgsz: int = 640,
) -> AerialVisDroneDetector:
    """The process-wide detector for these weights (created on first use, CUDA → CPU fallback)."""
    key = (primary_weights_path, device, int(imgsz))
    with _shared_lock:
        det = _shared_detectors.get(key)
        if det is not None:
            return det
        try:
            raw_yolo = load_aerial_yolo(primary_weights_path, device=device)
            det = AerialVisDroneDetector(
                model=raw_yolo,
                imgsz=imgsz,
                conf_thresh=conf_thresh,
                iou_thresh=iou_thresh,
                device=device,
            )
        except Exception as exc:
            log.warning("Failed to load anomaly detector on %s, falling back to CPU: %s", device, exc)
            raw_yolo = load_aerial_yolo(primary_weights_path, device="cpu")
            det = AerialVisDroneDetector(
                model=raw_yolo,
                imgsz=imgsz,
                conf_thresh=conf_thresh,
                iou_thresh=iou_thresh,
                device="cpu",
            )
        _shared_detectors[key] = det
        log.info("Shared anomaly detector loaded: %s on %s (imgsz=%d)", primary_weights_path, det.device, imgsz)
        return det


def shared_detectors() -> List[AerialVisDroneDetector]:
    with _shared_lock:
        return list(_shared_detectors.values())


def release_shared_detectors() -> None:
    """Move the shared models to CPU and drop them (VRAM_OFFLOAD_ON_STOP)."""
    with _shared_lock:
        dets = list(_shared_detectors.values())
        _shared_detectors.clear()
    for det in dets:
        for m in (getattr(det, "model", None), getattr(det, "object_model", None)):
            if m is None:
                continue
            try:
                m.to("cpu")
            except Exception:
                pass


def format_time(seconds: float) -> str:
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 100)
    return f"{mins:02d}:{secs:02d}.{millis:02d}"



NOMINAL_FPS = 20.0       # analytics frame rate the frame-count defaults were tuned at
MAX_FRAME_DT_S = 1.0

class AnomalyAnalyzer:
    """
    Per-drone Anomaly Analyzer instance maintaining tracker, motion,
    association, and hysteresis state across continuous video frames.
    """

    def __init__(
        self,
        drone_id: str,
        primary_weights_path: str = "models/weights/visdrone-yolov11x.pt",
        secondary_weights_path: str = "models/weights/yolo11n.pt",
        config_dir: str = "config",
        device: str = "cuda",
        conf_thresh: float = 0.35,
        iou_thresh: float = 0.40,
        imgsz: int = 640,
        detector: Optional[AerialVisDroneDetector] = None,
    ):
        self.drone_id = drone_id
        self.device = device
        self.conf_thresh = conf_thresh
        self.iou_thresh = iou_thresh
        self.imgsz = imgsz

        # Resolve weights path if relative
        base_dir = os.path.dirname(os.path.abspath(__file__))
        if not os.path.isabs(primary_weights_path):
            primary_weights_path = os.path.join(base_dir, primary_weights_path)
        if not os.path.isabs(secondary_weights_path):
            secondary_weights_path = os.path.join(base_dir, secondary_weights_path)

        thresholds_path = os.path.join(base_dir, config_dir, "thresholds.yaml")
        if not os.path.exists(thresholds_path):
            thresholds_path = os.path.join(base_dir, "thresholds.yaml")

        log.info(
            "[%s] Initializing AnomalyAnalyzer: primary=%s, device=%s, conf=%.2f",
            drone_id, primary_weights_path, device, conf_thresh
        )

        # 1. Dual-Model Aerial Detector — shared across drones (RunPod plan 9.6)
        self.detector = detector or get_shared_detector(
            primary_weights_path,
            device=device,
            conf_thresh=conf_thresh,
            iou_thresh=iou_thresh,
            imgsz=imgsz,
        )

        # Load parsed thresholds configuration
        cfg = {}
        if os.path.exists(thresholds_path):
            try:
                with open(thresholds_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
            except Exception as e:
                log.warning("[%s] Failed to load thresholds from %s: %s", drone_id, thresholds_path, e)

        beh_cfg = cfg.get("behavior", {})
        veh_cfg = cfg.get("vehicle", {})
        enable_wrong_way = veh_cfg.get("enable_wrong_way", False)

        # 2. Core Subsystems
        self.motion_estimator = CameraMotionEstimator(max_features=800)
        self.tracker = DroneMultiObjectTracker(
            max_lost_frames=30,
            human_classes={"pedestrian", "people", "person"}
        )
        self.associator = OwnershipAssociator(max_association_dist_px=140.0)
        self.hand_associator = HandAssociationEngine(hand_zone_dist_px=40.0)
        self.stationarity_evaluator = GroundStationarityEvaluator(
            max_ground_speed_px=10.0,
            stationary_min_seconds=1.5
        )
        self.rules_engine = SuspiciousEventRulesEngine(
            config_path=thresholds_path,
            enable_wrong_way=enable_wrong_way,
        )
        self.trajectory_analyzer = TrajectoryAnalyzer()
        self.person_behavior_analyzer = PersonBehaviorAnalyzer(
            loiter_dwell_thresh_sec=beh_cfg.get("loitering_duration_sec", 15.0),
            running_speed_thresh=beh_cfg.get("running_speed_thresh_px", 90.0),
        )
        self.interaction_analyzer = InteractionAnalyzer(
            min_following_duration_sec=beh_cfg.get("following_duration_sec", 4.0),
        )
        self.vehicle_trajectory_analyzer = VehicleTrajectoryAnalyzer()
        self.vehicle_speed_analyzer = VehicleSpeedAnalyzer(
            abrupt_stop_decel_thresh=-veh_cfg.get("abrupt_stop_decel_percent", 0.60) * 75.0,
        )
        self.lane_traffic_analyzer = LaneTrafficAnalyzer(enabled=enable_wrong_way)
        self.vehicle_circling_detector = VehicleCirclingDetector(
            min_revisits=veh_cfg.get("circling_min_passes", 3)
        )
        self.vehicle_interaction_analyzer = VehicleInteractionAnalyzer()
        self.scene_context_engine = SceneContextEngine()

        self.alert_manager = AlertManager(
            output_dir="/data/evidence/alerts" if os.path.exists("/data/evidence") else "output/alerts",
            cooldown_seconds=self.rules_engine.cooldown_seconds,
        )
        self.visualizer = ExplainableVisualizer()

        # Telemetry & Performance Tracking
        self.frame_count = 0
        self.start_time = time.time()
        # v2 R6.5: the behaviour modules integrate durations as 1 / fps per frame. They were given a
        # fixed 30.0 while analytics frames arrive at 20 fps (timers ran at 2/3 speed); they now get
        # the real spacing of this drone's frames in capture time, clamped to [1/60 s, 1 s] (a longer
        # gap — shedding, a drop — counts as 1 s, never as a jump).
        self._last_ts: Optional[float] = None
        self.cap_t0: Optional[float] = None
        self.rolling_fps = 0.0
        self.latest_annotated_frame: Optional[np.ndarray] = None
        self.active_tracks_cache: List[Dict[str, Any]] = []
        # Per-drone pipeline state is not thread-safe (stream loop vs /annotated/latest recovery)
        self.lock = threading.RLock()

    def process_frame(
        self,
        frame: np.ndarray,
        metadata: Optional[Dict[str, Any]] = None,
        timestamp: Optional[float] = None,
    ) -> Tuple[np.ndarray, List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Processes a single video frame synchronously.
        Returns:
            (annotated_frame_bgr, triggered_alerts, active_tracks_summary)
        """
        with self.lock:
            t0 = time.time()
            # Step 1: Camera Ego-Motion Estimation (ORB + RANSAC Affine)
            motion = self.motion_estimator.estimate_motion(frame)
            # Step 2: Dual Aerial Object Detection (YOLO11x + YOLO11n)
            detections = self.detector.detect(frame)
            return self.process_detections(frame, detections, metadata, timestamp,
                                           motion=motion, t0=t0)

    def process_detections(
        self,
        frame: np.ndarray,
        detections: List[Any],
        metadata: Optional[Dict[str, Any]] = None,
        timestamp: Optional[float] = None,
        motion: Any = None,
        t0: Optional[float] = None,
    ) -> Tuple[np.ndarray, List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Steps 3–9 for one frame whose detections were computed already
        (batched across drones by analyze_round). `motion` is this frame's step-1
        camera motion; it is estimated here when not given. `t0` is when the
        frame's processing started (HUD fps), default now.
        """
        with self.lock:
            return self._process_detections(frame, detections, metadata, timestamp, motion, t0)

    def _process_detections(self, frame, detections, metadata, timestamp, motion, t0):
        self.frame_count += 1
        if t0 is None:
            t0 = time.time()
        if timestamp is None or timestamp <= 0:
            timestamp = time.time() - self.start_time
        dt = (timestamp - self._last_ts) if self._last_ts is not None else 1.0 / NOMINAL_FPS
        self._last_ts = timestamp
        fps_now = 1.0 / min(MAX_FRAME_DT_S, max(1.0 / 60.0, dt))

        metadata = metadata or {}
        triggered_alerts = []

        if motion is None:
            motion = self.motion_estimator.estimate_motion(frame)

        # Step 3: Multi-Object Tracking with Motion Compensation
        active_tracks = self.tracker.update(
            detections=detections,
            timestamp=timestamp,
            H_camera=motion.matrix if motion.is_valid else None,
        )

        # Step 4: Map Ground Coordinates & Compute Kinematics
        for trk in active_tracks:
            ground_pos = self.motion_estimator.map_to_ground_reference(trk.bottom_center)
            trk.ground_history.append((ground_pos[0], ground_pos[1], timestamp))
            if len(trk.ground_history) > 120:
                trk.ground_history.pop(0)
            self.trajectory_analyzer.update_kinematics(trk, timestamp, fps=fps_now)

        # Separate Entities by Role
        active_persons = [t for t in active_tracks if t.is_person]
        active_vehicles = [t for t in active_tracks if t.is_vehicle]
        active_objects = [t for t in active_tracks if not t.is_person and not t.is_vehicle]

        # Step 5: Relational & Ownership Associations
        self.associator.update_associations(
            tracks=active_tracks,
            timestamp=timestamp,
            fps=fps_now,
        )
        self.hand_associator.update_hand_associations(
            persons=active_persons,
            objects=active_objects,
            timestamp=timestamp,
            fps=fps_now,
        )

        # Step 6: Multi-Entity Behavior Analysis
        for p in active_persons:
            self.person_behavior_analyzer.analyze(p, timestamp, fps=fps_now)
            if self.trajectory_analyzer.is_repetitive_trajectory(p):
                if EventType.LOITERING_EVENT not in p.active_events:
                    p.active_events.append(EventType.LOITERING_EVENT)
        self.interaction_analyzer.update_interactions(
            active_persons, timestamp, fps=fps_now, objects=active_objects
        )
        self.trajectory_analyzer.evaluate_crowd_flow(active_persons, timestamp)

        for v in active_vehicles:
            self.vehicle_trajectory_analyzer.analyze(v, timestamp)
            self.vehicle_circling_detector.analyze(v, timestamp)
        self.lane_traffic_analyzer.evaluate_traffic_flow(active_vehicles)
        self.vehicle_speed_analyzer.analyze(active_vehicles, timestamp, fps=fps_now)
        self.vehicle_interaction_analyzer.update_interactions(
            vehicles=active_vehicles,
            persons=active_persons,
            objects=active_objects,
            timestamp=timestamp,
            fps=fps_now,
        )

        self.scene_context_engine.evaluate_context(active_tracks)

        for trk in active_objects:
            self.stationarity_evaluator.evaluate(trk, timestamp, fps=fps_now)

        # Step 7: Cognitive Suspicious Rules Engine & 4-Tier Confidences
        for trk in active_tracks:
            trk.update_detector_smoothing(trk.confidence, alpha=0.4)
            self.rules_engine.evaluate_entity(
                track=trk,
                all_tracks=active_tracks,
                timestamp=timestamp,
                fps=fps_now,
            )

            # Step 8: Multi-Stage Alert Gating & Evidence Capture
            can_alert, gate_reason, alert_meta = self.rules_engine.should_alert(
                track=trk,
                all_tracks=active_tracks,
                timestamp=timestamp,
            )
            if can_alert:
                alert = self.alert_manager.trigger_alert_if_ready(
                    obj=trk,
                    frame=frame,
                    timestamp=timestamp,
                    timestamp_formatted=format_time(timestamp),
                )
                if alert:
                    trk.alert_triggered = True
                    trk.state = ObjectState.ALERTED
                    # Attach drone_id and mission_id to alert payload
                    alert["drone_id"] = self.drone_id
                    alert["mission_id"] = metadata.get("mission_id", "")
                    alert["gps_lat"] = metadata.get("lat") or metadata.get("latitude")
                    alert["gps_lon"] = metadata.get("lon") or metadata.get("longitude")
                    triggered_alerts.append(alert)
                    log.info(
                        "[%s] 🚨 ANOMALY ALERT #%s [%s]: %s",
                        self.drone_id, alert.get("alert_id"),
                        trk.suspicion_level.value, alert.get("summary_explanation")
                    )

        # Step 9: HUD Rendering
        t_infer = time.time() - t0
        inst_fps = 1.0 / max(t_infer, 1e-4)
        self.rolling_fps = 0.9 * self.rolling_fps + 0.1 * inst_fps if self.rolling_fps > 0 else inst_fps

        current_active_alerts = [
            t for t in active_tracks
            if getattr(t, "is_alert_active", False)
            or t.state == ObjectState.ALERTED
            or (t.suspicion_level in {SuspicionLevel.HIGH, SuspicionLevel.CRITICAL} and getattr(t, "alert_triggered", False))
        ]

        annotated = self.visualizer.render(
            frame=frame,
            tracks=active_tracks,
            frame_idx=self.frame_count,
            total_frames=0,
            timestamp_sec=timestamp,
            timestamp_formatted=format_time(timestamp),
            fps=self.rolling_fps,
            camera_dx=motion.dx,
            camera_dy=motion.dy,
            alerts=current_active_alerts,
        )
        self.latest_annotated_frame = annotated

        # Build clean active tracks summary for API/DB consumers
        tracks_summary = []
        for trk in active_tracks:
            category = "person" if trk.is_person else ("vehicle" if trk.is_vehicle else "object")
            tracks_summary.append({
                "track_id": trk.track_id,
                "class_name": trk.class_name,
                "class_category": category,
                "suspicion_level": trk.suspicion_level.value,
                "suspicion_score": round(float(trk.suspiciousness_score), 3),
                "pipeline_stage": trk.pipeline_stage.value,
                "duration_s": round(float(trk.dwell_time), 1),
                "trajectory": [[round(float(pt[0]), 1), round(float(pt[1]), 1)] for pt in trk.history[-20:]],
                "ground_trajectory": [[round(float(pt[0]), 1), round(float(pt[1]), 1)] for pt in trk.ground_history[-20:]],
                "attributes": {
                    "speed_px_s": round(float(trk.speed), 2),
                    "is_running": trk.is_running,
                    "is_loitering": trk.is_loitering,
                    "is_wrong_direction": trk.is_wrong_direction,
                    "opposing_crowd_flow": trk.opposing_crowd_flow,
                },
                "bbox_xyxy": [round(float(v), 1) for v in trk.bbox],
                "is_stationary": trk.is_stationary,
                "stationary_seconds": round(float(trk.stationary_duration), 1),
                "owner_track_id": trk.owner_id,
                "owner_distance_px": round(float(trk.owner_distance), 1) if trk.owner_distance else None,
                "evidence_groups": list(trk.evidence_groups),
                "reasons": list(trk.explainability_reasons),
            })
        self.active_tracks_cache = tracks_summary

        return annotated, triggered_alerts, tracks_summary


def analyze_round(
    items: List[Tuple["AnomalyAnalyzer", np.ndarray, Optional[Dict[str, Any]], Optional[float]]],
    max_batch: int = 1,
) -> List[Tuple[np.ndarray, List[Dict[str, Any]], List[Dict[str, Any]], float]]:
    """Run the newest frame of several drones through the pipeline.

    items: (analyzer, frame, metadata, timestamp) per drone. Step 1 (camera motion)
    runs per drone, step 2 (detection) runs batched for analyzers sharing a
    detector in chunks of `max_batch`, steps 3–9 run per drone with that drone's
    own state. Returns (annotated, alerts, tracks_summary, detect_ms) per item, in
    order. Output equals calling process_frame on each item.
    """
    n = len(items)
    t0s = [0.0] * n
    motions: List[Any] = [None] * n
    dets: List[Optional[List[Any]]] = [None] * n
    det_ms = [0.0] * n

    # Step 1 per drone (needs the previous frame of the same drone)
    for i, (an, frame, _meta, _ts) in enumerate(items):
        t0s[i] = time.time()
        t_m = time.perf_counter()
        with an.lock:
            motions[i] = an.motion_estimator.estimate_motion(frame)
        # stage timings of this drone's last frame, published by the service (v2 R6.9)
        an.stage_ms = {"motion": (time.perf_counter() - t_m) * 1000}

    # Step 2 batched per shared detector
    by_det: Dict[int, List[int]] = {}
    for i, (an, _f, _m, _t) in enumerate(items):
        by_det.setdefault(id(an.detector), []).append(i)
    step = max(1, int(max_batch))
    for idxs in by_det.values():
        detector = items[idxs[0]][0].detector
        for c in range(0, len(idxs), step):
            chunk = idxs[c:c + step]
            t_det = time.perf_counter()
            frames = [items[i][1] for i in chunk]
            if len(chunk) > 1 and hasattr(detector, "detect_batch"):
                res = detector.detect_batch(frames)
            else:
                res = [detector.detect(f) for f in frames]
            ms = (time.perf_counter() - t_det) * 1000 / len(chunk)
            for i, d in zip(chunk, res):
                dets[i] = d
                det_ms[i] = ms

    # Steps 3–9 per drone
    out = []
    for i, (an, frame, meta, ts) in enumerate(items):
        t_p = time.perf_counter()
        annotated, alerts, summary = an.process_detections(
            frame, dets[i] or [], meta, ts, motion=motions[i], t0=t0s[i],
        )
        an.stage_ms["pipeline"] = (time.perf_counter() - t_p) * 1000
        out.append((annotated, alerts, summary, det_ms[i]))
    return out
