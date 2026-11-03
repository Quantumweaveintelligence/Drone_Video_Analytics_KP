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

    def _object_predict(self, source):
        """Secondary COCO bag detector (backpack, handbag, suitcase); None on any error."""
        import torch
        try:
            with self.lock, torch.inference_mode():
                return self.object_model.predict(
                    source=source,
                    classes=[24, 26, 28],  # backpack, handbag, suitcase
                    conf=max(0.20, self.conf_thresh * 0.8),
                    imgsz=640,
                    device=self.device,
                    verbose=False,
                    **self._predict_kwargs(self.device),
                )
        except Exception:
            return None

    def _object_dets(self, obj_res) -> List[Detection]:
        dets: List[Detection] = []
        try:
            if obj_res is not None and obj_res.boxes is not None:
                boxes = obj_res.boxes
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
            pass    # keep what was parsed (same as the old inline loop)
        return dets

    def detect(self, frame: np.ndarray) -> List[Detection]:
        dets = super().detect(frame)
        if self.object_model is not None:
            obj_res = self._object_predict(frame)
            if obj_res and len(obj_res) > 0:
                dets.extend(self._object_dets(obj_res[0]))
        return dets

    def detect_batch(self, frames: List[np.ndarray]) -> List[List[Detection]]:
        """detect() for several frames with one batched forward pass per model."""
        if len(frames) <= 1:
            return [self.detect(f) for f in frames]
        out = super().detect_batch(frames)
        if self.object_model is not None:
            # Equal shapes only, for the same letterbox as the single-frame path
            groups: dict = {}
            for i, f in enumerate(frames):
                groups.setdefault(f.shape, []).append(i)
            for idxs in groups.values():
                obj_res = self._object_predict([frames[i] for i in idxs])
                if obj_res is None or len(obj_res) != len(idxs):
                    if len(idxs) > 1:   # e.g. OOM on the batch: per frame
                        for i in idxs:
                            single = self._object_predict(frames[i])
                            if single and len(single) > 0:
                                out[i].extend(self._object_dets(single[0]))
                    continue
                for i, r in zip(idxs, obj_res):
                    out[i].extend(self._object_dets(r))
        return out

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
