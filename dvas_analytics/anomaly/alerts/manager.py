"""
Alert and Evidence Management Module.
Generates structured explainable alert JSONs, saves high-resolution evidence snapshots,
and manages alert cooldowns to avoid duplicate alerts.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional
import cv2
import numpy as np

from tracking.track_state import TrackState, ObjectState, SuspicionLevel


class AlertManager:
    """
    Manages alerting, evidence snapshot persistence, and cooldown periods.
    """

    def __init__(
        self,
        output_dir: str = "output/alerts",
        cooldown_seconds: float = 15.0,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.cooldown_seconds = cooldown_seconds
        self.alert_history: List[Dict] = []

    def trigger_alert_if_ready(
        self,
        obj: TrackState,
        frame: np.ndarray,
        timestamp: float,
        timestamp_formatted: str,
    ) -> Optional[Dict]:
        """
        Triggers an alert event with explainability payload if cooldown allows.
        """
        is_escalation = (
            obj.last_alerted_level is not None
            and obj.suspicion_level == SuspicionLevel.CRITICAL
            and obj.last_alerted_level != SuspicionLevel.CRITICAL
        )
        if (timestamp - obj.last_alert_time < self.cooldown_seconds) and not is_escalation:
            return None

        obj.last_alert_time = timestamp
        obj.alert_triggered = True
        obj.last_alerted_level = obj.suspicion_level

        alert_id = len(self.alert_history) + 1
        time_tag = timestamp_formatted.replace(":", "m").replace(".", "s")

        # Save Evidence Image
        evidence_filename = f"alert_{alert_id:03d}_obj{obj.track_id}_{time_tag}.jpg"
        evidence_path = self.output_dir / evidence_filename
        cv2.imwrite(str(evidence_path), frame)

        # Generate Explainable Alert Payload adhering to cognitive rule specification
        evt_name = obj.primary_event.value if obj.primary_event else (
            "ABANDONED_OBJECT" if obj.state == ObjectState.SUSPICIOUS else obj.state.value
        )
        payload = {
            "suspicion": True,
            "level": obj.suspicion_level.value,
            "pipeline_stage": obj.pipeline_stage.value,
            "event": evt_name,
            "event_type": "SUSPICIOUS_OBJECT_DETECTED",
            "reasons": obj.explainability_reasons,
            "evidence_groups": obj.evidence_groups,
            "alert_id": alert_id,
            "timestamp": timestamp_formatted,
            "timestamp_sec": round(timestamp, 2),
            "evidence_image": str(evidence_path.name),
            "object": {
                "track_id": obj.track_id,
                "class": obj.class_name,
                "confidence": round(obj.confidence, 3),
                "bbox": [round(v, 1) for v in obj.bbox],
                "ground_speed_px_s": round(obj.ground_speed, 2),
                "ground_variance": round(obj.ground_variance, 2),
            },
            "owner": {
                "track_id": obj.owner_id,
                "distance_px": round(obj.owner_distance, 1),
                "status": "DEPARTED" if obj.owner_distance > 80.0 else "NEARBY",
            },
            "confidences": {
                "detector_confidence": round(obj.detector_confidence, 2),
                "smoothed_detector_confidence": round(obj.smoothed_detector_confidence, 2),
                "rule_confidence": round(obj.rule_confidence, 2),
                "event_confidence": round(obj.event_confidence, 2),
                "alert_confidence": round(obj.alert_confidence, 2),
                "tracker_confidence": round(obj.tracker_confidence, 2),
                "association_confidence": round(obj.association_confidence, 2),
                "behavior_confidence": round(obj.behavior_confidence, 2),
            },
            "timeline": obj.timeline,
            "temporal": {
                "stationary_seconds": round(obj.stationary_duration, 1),
                "unattended_seconds": round(obj.unattended_duration, 1),
            },
            "suspiciousness": {
                "score": round(obj.suspiciousness_score, 3),
                "state": obj.state.value,
                "breakdown": obj.score_breakdown,
            },
            "why_suspicious": obj.explainability_reasons,
            "summary_explanation": (
                (
                    f"Object #{obj.track_id} ({obj.class_name}) is flagged as {obj.state.value} "
                    f"[{obj.suspicion_level.value}] (Score: {obj.suspiciousness_score:.2f}) because it has been stationary on the ground "
                    f"for {obj.stationary_duration:.1f}s and separated from its owner for {obj.unattended_duration:.1f}s."
                ) if (not getattr(obj, "is_person", False) and not getattr(obj, "is_vehicle", False) and (obj.stationary_duration > 0 or obj.unattended_duration > 0 or obj.class_name in {"backpack", "suitcase", "handbag", "bag"})) else (
                    f"Entity #{obj.track_id} ({obj.class_name}) is flagged as {obj.state.value} "
                    f"[{obj.suspicion_level.value}] (Score: {obj.suspiciousness_score:.2f}) due to: "
                    f"{', '.join(r.replace('_', ' ') for r in obj.explainability_reasons) if obj.explainability_reasons else 'anomalous behavior pattern detected'}."
                )
            ),
            "disclaimer": "Visual/behavioral detection; does not establish intent or legal offense.",
        }



        # Save Individual Alert JSON
        json_filename = f"alert_{alert_id:03d}_obj{obj.track_id}_{time_tag}.json"
        with open(self.output_dir / json_filename, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        self.alert_history.append(payload)
        return payload
