"""
Aerial detector module for VisDrone domain initialization.
Handles aerial perspective detections and separates human candidates from context objects.
"""

from typing import List, Optional, Tuple
import numpy as np
from ultralytics import YOLO

from .schema import Detection
from .detector import YOLODetector


# VisDrone 10 classes
VISDRONE_HUMAN_CLASSES = {"pedestrian", "people"}
VISDRONE_VEHICLE_CLASSES = {
    "bicycle", "car", "van", "truck", "tricycle", "awning-tricycle", "bus", "motor"
}
VISDRONE_OBJECT_CLASSES = {"others", "backpack", "suitcase", "handbag", "bag", "box", "package"}


class AerialVisDroneDetector(YOLODetector):
    """
    Specialized aerial detector using YOLO11x VisDrone weights.
    Categorizes detections into humans, specific vehicle types, and ambient objects.
    Supports secondary object detection for backpacks, suitcases, and bags.
    """

    def __init__(
        self,
        model: YOLO,
        imgsz: int = 1280,
        conf_thresh: float = 0.25,
        iou_thresh: float = 0.45,
        device: str = "cuda",
        filter_classes: Optional[List[str]] = None,
        enable_object_detector: bool = True,
    ):
        super().__init__(
            model=model,
            imgsz=imgsz,
            conf_thresh=conf_thresh,
            iou_thresh=iou_thresh,
            device=device,
            filter_classes=filter_classes,
        )
        self.object_model: Optional[YOLO] = None
        if enable_object_detector:
            from pathlib import Path
            coco_path = Path(__file__).resolve().parent.parent / "models" / "weights" / "yolo11n.pt"
            if coco_path.exists():
                try:
                    self.object_model = YOLO(str(coco_path))
                except Exception:
                    self.object_model = None

    def detect(self, frame: np.ndarray) -> List[Detection]:
        dets = super().detect(frame)
        if self.object_model is not None:
            import torch
            try:
                with torch.inference_mode():
                    obj_res = self.object_model.predict(
                        source=frame,
                        classes=[24, 26, 28],  # backpack, handbag, suitcase
                        conf=max(0.20, self.conf_thresh * 0.8),
                        imgsz=640,
                        device=self.device,
                        verbose=False,
                    )
                if obj_res and len(obj_res) > 0 and obj_res[0].boxes is not None:
                    boxes = obj_res[0].boxes
                    for i in range(len(boxes)):
                        cls_id = int(boxes.cls[i].item())
                        cls_name = self.object_model.names.get(cls_id, "object")
                        conf = float(boxes.conf[i].item())
                        bbox = [float(v) for v in boxes.xyxy[i].tolist()]
                        dets.append(
                            Detection(
                                bbox=bbox,
                                confidence=conf,
                                class_id=cls_id + 100,
                                class_name=cls_name,
                            )
                        )
            except Exception:
                pass
        return dets

    def detect_separated(
        self, frame: np.ndarray
    ) -> Tuple[List[Detection], List[Detection]]:
        """
        Executes aerial detection and returns separated (humans, other_objects).

        :param frame: HxWxC input frame
        :return: Tuple of (human_detections, other_detections)
        """
        all_dets = self.detect(frame)
        humans: List[Detection] = []
        others: List[Detection] = []

        for d in all_dets:
            if d.class_name in VISDRONE_HUMAN_CLASSES:
                humans.append(d)
            else:
                others.append(d)

        return humans, others
