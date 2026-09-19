"""
Detector abstraction layer for Drone Suspicious Object Detection System.
Provides a clean interface decoupling detection architectures from tracking and suspiciousness engines.
"""

from abc import ABC, abstractmethod
from typing import List, Optional
import numpy as np
from ultralytics import YOLO

from detection.schema import Detection  # noqa: F401  (re-exported for convenience)




class ObjectDetector(ABC):
    """Abstract Base Class for all object detectors in the system."""

    @abstractmethod
    def detect(self, frame: np.ndarray) -> List[Detection]:
        """
        Executes detection on a single RGB frame.

        :param frame: HxWxC numpy array (BGR or RGB image)
        :return: List of Detection instances
        """
        raise NotImplementedError


class YOLODetector(ObjectDetector):
    """General YOLO detector implementation supporting high-resolution inference."""

    def __init__(
        self,
        model: YOLO,
        imgsz: int = 1280,
        conf_thresh: float = 0.25,
        iou_thresh: float = 0.45,
        device: str = "cuda",
        filter_classes: Optional[List[str]] = None,
    ):
        self.model = model
        self.imgsz = imgsz
        self.conf_thresh = conf_thresh
        self.iou_thresh = iou_thresh
        self.device = device
        self.filter_classes = set(filter_classes) if filter_classes else None
        self.class_names = self.model.names

    def detect(self, frame: np.ndarray) -> List[Detection]:
        import torch
        try:
            with torch.inference_mode():
                results = self.model.predict(
                    source=frame,
                    conf=self.conf_thresh,
                    iou=self.iou_thresh,
                    imgsz=self.imgsz,
                    device=self.device,
                    agnostic_nms=True,
                    verbose=False,
                    save=False,
                )
        except Exception as e:
            err_msg = str(e).lower()
            if "out of memory" in err_msg or "cuda error" in err_msg:
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                fallback_sz = 640 if self.imgsz > 640 else (480 if self.imgsz > 480 else 320)
                print(f"[Detector WARNING] GPU VRAM constrained at {self.imgsz}. Downscaling inference resolution to {fallback_sz}...")
                self.imgsz = fallback_sz
                try:
                    with torch.inference_mode():
                        results = self.model.predict(
                            source=frame,
                            conf=self.conf_thresh,
                            iou=self.iou_thresh,
                            imgsz=self.imgsz,
                            device=self.device,
                            agnostic_nms=True,
                            verbose=False,
                            save=False,
                        )
                except Exception:
                    # Final fallback: switch to CPU
                    print("[Detector WARNING] Falling back to CPU for current frame inference...")
                    with torch.inference_mode():
                        results = self.model.predict(
                            source=frame,
                            conf=self.conf_thresh,
                            iou=self.iou_thresh,
                            imgsz=self.imgsz,
                            device="cpu",
                            agnostic_nms=True,
                            verbose=False,
                            save=False,
                        )
            else:
                raise

        detections: List[Detection] = []
        if not results or len(results) == 0 or results[0].boxes is None:
            return detections

        boxes = results[0].boxes
        for i in range(len(boxes)):
            cls_id = int(boxes.cls[i].item())
            cls_name = self.model.names.get(cls_id, str(cls_id)) if hasattr(self.model, "names") else self.class_names.get(cls_id, str(cls_id))

            if self.filter_classes and cls_name not in self.filter_classes:
                continue

            conf = float(boxes.conf[i].item())
            bbox = [float(v) for v in boxes.xyxy[i].tolist()]

            detections.append(
                Detection(
                    bbox=bbox,
                    confidence=conf,
                    class_id=cls_id,
                    class_name=cls_name,
                )
            )

        # Advanced category-aware & rider-vehicle duplicate suppression
        if len(detections) > 1:
            HUMAN_CLASSES = {"pedestrian", "people", "person"}
            LIGHT_VEHICLE_CLASSES = {"bicycle", "motor", "tricycle", "awning-tricycle", "motorcycle", "scooter"}
            VEHICLE_CLASSES = LIGHT_VEHICLE_CLASSES | {"car", "van", "truck", "bus", "vehicle"}

            detections.sort(key=lambda d: d.confidence, reverse=True)
            suppressed = set()
            clean_detections: List[Detection] = []
            for i in range(len(detections)):
                if i in suppressed:
                    continue
                d_i = detections[i]
                clean_detections.append(d_i)
                for j in range(i + 1, len(detections)):
                    if j in suppressed:
                        continue
                    d_j = detections[j]

                    x1 = max(d_i.bbox[0], d_j.bbox[0])
                    y1 = max(d_i.bbox[1], d_j.bbox[1])
                    x2 = min(d_i.bbox[2], d_j.bbox[2])
                    y2 = min(d_i.bbox[3], d_j.bbox[3])
                    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
                    area_i = max(0.0, d_i.bbox[2] - d_i.bbox[0]) * max(0.0, d_i.bbox[3] - d_i.bbox[1])
                    area_j = max(0.0, d_j.bbox[2] - d_j.bbox[0]) * max(0.0, d_j.bbox[3] - d_j.bbox[1])
                    union = area_i + area_j - inter
                    iou = inter / union if union > 0 else 0.0

                    is_h_i = d_i.class_name.lower() in HUMAN_CLASSES
                    is_h_j = d_j.class_name.lower() in HUMAN_CLASSES
                    is_v_i = d_i.class_name.lower() in VEHICLE_CLASSES
                    is_v_j = d_j.class_name.lower() in VEHICLE_CLASSES
                    is_lv_i = d_i.class_name.lower() in LIGHT_VEHICLE_CLASSES
                    is_lv_j = d_j.class_name.lower() in LIGHT_VEHICLE_CLASSES

                    # Case 1: Same category with moderate overlap -> Suppress duplicate
                    if (is_h_i and is_h_j) or (is_v_i and is_v_j):
                        if iou > 0.35:
                            suppressed.add(j)
                    # Case 2: Rider on vehicle (Person + Motorcycle/Bicycle/Scooter) -> Keep vehicle, suppress rider box
                    elif (is_h_i and is_lv_j) or (is_lv_i and is_h_j):
                        # Check center containment or overlap
                        dj_cx, dj_cy = (d_j.bbox[0] + d_j.bbox[2]) / 2.0, (d_j.bbox[1] + d_j.bbox[3]) / 2.0
                        di_cx, di_cy = (d_i.bbox[0] + d_i.bbox[2]) / 2.0, (d_i.bbox[1] + d_i.bbox[3]) / 2.0
                        in_i = (d_i.bbox[0] <= dj_cx <= d_i.bbox[2]) and (d_i.bbox[1] <= dj_cy <= d_i.bbox[3])
                        in_j = (d_j.bbox[0] <= di_cx <= d_j.bbox[2]) and (d_j.bbox[1] <= di_cy <= d_j.bbox[3])
                        if iou > 0.20 or in_i or in_j:
                            if is_lv_i:
                                suppressed.add(j)  # Suppress human sub-box
                            else:
                                suppressed.add(i)
                                clean_detections.pop()
                                clean_detections.append(d_j)
                                break
                    # Case 3: Any general strong overlap
                    elif iou > 0.45:
                        suppressed.add(j)
            detections = clean_detections

        return detections
