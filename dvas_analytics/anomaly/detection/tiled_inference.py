"""
Tiled inference module for tiny object aerial detection.
Slices high-resolution drone frames into overlapping tiles, runs detector,
offsets coordinates back to global frame, and merges candidates via NMS.
"""

from typing import List
import cv2
import numpy as np
import torch
from torchvision.ops import nms

from .schema import Detection
from .detector import ObjectDetector


def run_tiled_inference(
    detector: ObjectDetector,
    frame: np.ndarray,
    tile_size: int = 640,
    overlap: float = 0.20,
    iou_thresh: float = 0.45,
) -> List[Detection]:
    """
    Executes sliced tiled inference over a high-resolution frame.
    """
    h, w = frame.shape[:2]
    step = int(tile_size * (1.0 - overlap))

    all_detections: List[Detection] = []

    # Iterate over grid tiles
    y_starts = list(range(0, max(1, h - tile_size + 1), step))
    if y_starts[-1] + tile_size < h:
        y_starts.append(h - tile_size)

    x_starts = list(range(0, max(1, w - tile_size + 1), step))
    if x_starts[-1] + tile_size < w:
        x_starts.append(w - tile_size)

    for y in y_starts:
        for x in x_starts:
            tile = frame[y : y + tile_size, x : x + tile_size]
            tile_dets = detector.detect(tile)

            for d in tile_dets:
                # Offset bounding box to global coordinates
                global_bbox = [
                    d.bbox[0] + x,
                    d.bbox[1] + y,
                    d.bbox[2] + x,
                    d.bbox[3] + y,
                ]
                all_detections.append(
                    Detection(
                        bbox=global_bbox,
                        confidence=d.confidence,
                        class_id=d.class_id,
                        class_name=d.class_name,
                        metadata={"tile_x": x, "tile_y": y},
                    )
                )

    if not all_detections:
        return []

    # Run Class-agnostic Non-Maximum Suppression (NMS) on global coordinates
    boxes = torch.tensor([d.bbox for d in all_detections], dtype=torch.float32)
    scores = torch.tensor([d.confidence for d in all_detections], dtype=torch.float32)

    keep_indices = nms(boxes, scores, iou_thresh)
    keep_indices = keep_indices.cpu().numpy()

    merged_dets = [all_detections[i] for i in keep_indices]

    # Advanced category-aware & rider-vehicle duplicate suppression
    if len(merged_dets) > 1:
        HUMAN_CLASSES = {"pedestrian", "people", "person"}
        LIGHT_VEHICLE_CLASSES = {"bicycle", "motor", "tricycle", "awning-tricycle", "motorcycle", "scooter"}
        VEHICLE_CLASSES = LIGHT_VEHICLE_CLASSES | {"car", "van", "truck", "bus", "vehicle"}

        merged_dets.sort(key=lambda d: d.confidence, reverse=True)
        suppressed = set()
        clean_dets: List[Detection] = []
        for i in range(len(merged_dets)):
            if i in suppressed:
                continue
            d_i = merged_dets[i]
            clean_dets.append(d_i)
            for j in range(i + 1, len(merged_dets)):
                if j in suppressed:
                    continue
                d_j = merged_dets[j]
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
                    dj_cx, dj_cy = (d_j.bbox[0] + d_j.bbox[2]) / 2.0, (d_j.bbox[1] + d_j.bbox[3]) / 2.0
                    di_cx, di_cy = (d_i.bbox[0] + d_i.bbox[2]) / 2.0, (d_i.bbox[1] + d_i.bbox[3]) / 2.0
                    in_i = (d_i.bbox[0] <= dj_cx <= d_i.bbox[2]) and (d_i.bbox[1] <= dj_cy <= d_i.bbox[3])
                    in_j = (d_j.bbox[0] <= di_cx <= d_j.bbox[2]) and (d_j.bbox[1] <= di_cy <= d_j.bbox[3])
                    if iou > 0.20 or in_i or in_j:
                        if is_lv_i:
                            suppressed.add(j)
                        else:
                            suppressed.add(i)
                            clean_dets.pop()
                            clean_dets.append(d_j)
                            break
                elif iou > 0.45:
                    suppressed.add(j)
        merged_dets = clean_dets

    return merged_dets

