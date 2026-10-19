# -*- coding: utf-8 -*-
"""
RF-DETR Maritime Perception & Tracking Engine for SeaDrone.
Replaces YOLO models with fine-tuned RF-DETR Medium on SeaDronesSee (dronefreak/seadronessee-rfdetr-medium).

Coordinates:
- Real-time Transformer Detection (swimmer, boat, jetski, life_saving_appliances, buoy)
- Spectral Water-Domain Masking (WDM) for false positive suppression on docks/land
- Deterministic 9-class vessel classification (taxonomy.py)
- Zero-Flicker Maritime Vessel Tracker with EMA smoothing & velocity coasting
"""

import time
import logging
from pathlib import Path
from typing import List, Tuple, Optional, Dict

import cv2
import numpy as np
import torch
try:
    import rfdetr
except ImportError:
    rfdetr = None

from .water_domain import get_water_domain_mask
from .classifier import classify_maritime_object
from .tracker import MaritimeVesselTracker
from .taxonomy import VESSEL_CLASSES, VESSEL_PALETTE

log = logging.getLogger("rfdetr-maritime-engine")

# SeaDronesSee 5 classes
SEADRONESSEE_CLASSES = [
    "swimmer",
    "boat",
    "jetski",
    "life_saving_appliances",
    "buoy",
]


class RFDETRMaritimeEngine:
    """
    Maritime Perception Engine powered by RF-DETR Medium (SeaDronesSee).
    """

    def __init__(
        self,
        weights_path: str = "models/checkpoint_best_total.pth",
        conf_thresh: float = 0.25,
        person_conf: float = 0.20,
        iou_thresh: float = 0.35,
        water_filtering: bool = True,
        device: Optional[str] = None,
    ):
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"

        self.device = device
        self.conf_thresh = conf_thresh
        self.person_conf = person_conf
        self.iou_thresh = iou_thresh
        self.water_filtering = water_filtering

        # Resolve weights file
        self.weights_path = self._resolve_weights(weights_path)
        log.info("Loading SeaDronesSee RF-DETR Medium from: %s", self.weights_path)

        # Initialize RF-DETR Medium (num_classes=5 for SeaDronesSee)
        self.model = rfdetr.RFDETRMedium(
            pretrain_weights=self.weights_path,
            num_classes=len(SEADRONESSEE_CLASSES),
        )

        self.tracker = MaritimeVesselTracker(iou_thresh=self.iou_thresh)

    def _resolve_weights(self, weights_str: str) -> str:
        """Resolve local checkpoint or download from Hugging Face if not found."""
        p = Path(weights_str)
        if p.is_file():
            return str(p.resolve())

        # Check in script parent / models
        pkg_dir = Path(__file__).resolve().parent.parent
        candidates = [
            pkg_dir / "models" / "checkpoint_best_total.pth",
            pkg_dir / "checkpoint_best_total.pth",
            pkg_dir / weights_str,
            Path(r"D:\POLICE_DRONE\dvas_analytics\vessel\models\checkpoint_best_total.pth"),
        ]
        for c in candidates:
            if c.is_file():
                return str(c.resolve())

        # Download from Hugging Face
        log.warning("Weights not found locally. Downloading from Hugging Face...")
        from huggingface_hub import hf_hub_download
        downloaded = hf_hub_download(
            repo_id="dronefreak/seadronessee-rfdetr-medium",
            filename="checkpoint_best_total.pth",
            local_dir=str(pkg_dir / "models"),
        )
        return str(downloaded)

    def to_device(self, device: str):
        """Move underlying PyTorch model to device ('cpu', 'cuda', 'cuda:0')."""
        self.device = device
        try:
            if hasattr(self.model, "model") and hasattr(self.model.model, "to"):
                self.model.model.to(device)
            elif hasattr(self.model, "to"):
                self.model.to(device)
            log.info("Moved RF-DETR model to %s", device)
        except Exception as e:
            log.warning("Could not move model to %s: %s", device, e)

    def process_frame(self, frame: np.ndarray) -> Tuple[np.ndarray, List[Dict]]:

        """
        Process a single BGR frame:
        1. Spectral Water-Domain Masking
        2. RF-DETR Maritime Object Detection (RGB)
        3. Water-boundary gating
        4. Fine-grained 9-class physical vessel taxonomy classification
        5. Zero-flicker Maritime Multi-Object Tracker

        Returns:
            annotated_frame: BGR frame with bounding boxes and HUD tags
            tracked_objects: list of tracked object dicts
        """
        img_h, img_w = frame.shape[:2]

        # 1. Spectral Water Domain Masking
        if self.water_filtering:
            water_mask, dilated_mask = get_water_domain_mask(frame)
        else:
            dilated_mask = np.ones((img_h, img_w), dtype=np.uint8) * 255

        # 2. RF-DETR Model Inference
        # Convert BGR to RGB for Transformer
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        infer_conf = min(self.conf_thresh, self.person_conf)
        raw_det = self.model.predict(rgb, threshold=infer_conf)

        cand_boxes = []
        cand_scores = []
        cand_classes = []

        if len(raw_det) > 0 and raw_det.xyxy is not None:
            class_names_in_det = raw_det.data.get("class_name")
            for i in range(len(raw_det)):
                b = raw_det.xyxy[i]
                conf = float(raw_det.confidence[i])
                cls_id = int(raw_det.class_id[i])

                if class_names_in_det is not None and len(class_names_in_det) > i:
                    name = str(class_names_in_det[i]).lower()
                elif 0 <= cls_id < len(SEADRONESSEE_CLASSES):
                    name = SEADRONESSEE_CLASSES[cls_id].lower()
                else:
                    name = "boat"

                # Check confidence per class
                if name in ["swimmer", "person"] and conf < self.person_conf:
                    continue
                elif name not in ["swimmer", "person"] and conf < self.conf_thresh:
                    continue

                # Water Domain Gating (suppress land/dock false positives)
                if self.water_filtering:
                    x1, y1, x2, y2 = map(int, b)
                    crop_mask = dilated_mask[max(0, y1):min(img_h, y2), max(0, x1):min(img_w, x2)]
                    water_ratio = (np.sum(crop_mask > 0) / crop_mask.size) if crop_mask.size > 0 else 0
                    min_ratio = 0.40 if name in ["swimmer", "person"] else 0.20
                    if water_ratio < min_ratio:
                        continue

                cand_boxes.append(b)
                cand_scores.append(conf)
                cand_classes.append(name)

        if not cand_boxes:
            return self.tracker.update([], frame)

        # 3. Class-Agnostic NMS (cv2.dnn.NMSBoxes)
        boxes_xywh = [[int(b[0]), int(b[1]), int(b[2] - b[0]), int(b[3] - b[1])] for b in cand_boxes]
        keep_indices = cv2.dnn.NMSBoxes(
            boxes_xywh, cand_scores, score_threshold=infer_conf, nms_threshold=self.iou_thresh
        )
        keep_indices = np.array(keep_indices).flatten().tolist() if len(keep_indices) > 0 else []

        final_boxes = [cand_boxes[k] for k in keep_indices]
        final_scores = [cand_scores[k] for k in keep_indices]
        final_classes = [cand_classes[k] for k in keep_indices]

        # 4. Deterministic 9-Class Maritime Classification
        detections = []
        for box, score, orig_cls in zip(final_boxes, final_scores, final_classes):
            x1, y1, x2, y2 = map(int, box[:4])
            crop = frame[max(0, y1):min(img_h, y2), max(0, x1):min(img_w, x2)]
            fine_id, fine_name = classify_maritime_object(box, orig_cls, img_h, img_w, crop=crop)
            detections.append((box, score, fine_id, fine_name))

        # 5. Zero-Flicker Tracking
        annotated_frame, tracked_objects = self.tracker.update(detections, frame)
        return annotated_frame, tracked_objects
