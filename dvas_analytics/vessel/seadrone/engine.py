# -*- coding: utf-8 -*-
"""
Maritime Perception & Tracking Engine.
Coordinates Context-Aware Global Inference, Spectral Water-Domain Masking (WDM),
Class-Agnostic NMS, and Hierarchical IoS Suppression.
"""

import time
import json
from pathlib import Path
import cv2
import numpy as np
import torch
from ultralytics import YOLO
import logging

from .water_domain import get_water_domain_mask, generate_water_slices
from .classifier import classify_maritime_object
from .tracker import MaritimeVesselTracker

class MaritimePerceptionEngine:
    def __init__(
        self,
        weights_path: str = "models/yolov8s.pt",
        global_imgsz: int = 1280,
        enable_sahi: bool = False,
        slice_size: int = 640,
        overlap_ratio: float = 0.2,
        conf_thresh: float = 0.50,
        person_conf: float = 0.25,
        iou_thresh: float = 0.35,
        device: str = None
    ):
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"

        self.device = device
        self.global_imgsz = global_imgsz
        self.enable_sahi = enable_sahi
        self.slice_size = slice_size
        self.overlap_ratio = overlap_ratio
        self.conf_thresh = conf_thresh
        self.person_conf = person_conf
        self.iou_thresh = iou_thresh

        weights_file = Path(weights_path)
        if not weights_file.is_file():
            alt = Path(__file__).resolve().parent.parent / "models" / weights_file.name
            if alt.is_file():
                weights_file = alt

        self.model = YOLO(str(weights_file))
        self.tracker = MaritimeVesselTracker()

    def to_device(self, target_device: str):
        self.device = target_device
        if self.model is not None:
            try:
                self.model.to(target_device)
            except Exception:
                pass

    def _fallback_to_cpu(self):
        """Fall back to CPU when CUDA is unavailable or errored."""
        if self.device != "cpu":
            _log = logging.getLogger("vessel-engine")
            _log.warning("CUDA error detected — falling back vessel engine to CPU")
            self.device = "cpu"
            try:
                self.model.to("cpu")
            except Exception:
                pass

    def process_frame(self, frame: np.ndarray):
        img_h, img_w = frame.shape[:2]
        water_mask, dilated_mask = get_water_domain_mask(frame)

        cand_boxes = []
        cand_scores = []
        cand_classes = []

        # 1. Global Context Inference (imgsz=1280)
        # 1. Global Context Inference (detect candidates down to min threshold)
        infer_conf = min(self.conf_thresh, self.person_conf)
        try:
            res_global = self.model.predict(source=frame, imgsz=self.global_imgsz, conf=infer_conf, device=self.device, verbose=False)[0]
        except RuntimeError as e:
            if "CUDA" in str(e):
                self._fallback_to_cpu()
                res_global = self.model.predict(source=frame, imgsz=self.global_imgsz, conf=infer_conf, device=self.device, verbose=False)[0]
            else:
                raise
        if res_global.boxes is not None and len(res_global.boxes) > 0:
            for b, conf, cls_id in zip(res_global.boxes.xyxy.cpu().numpy(), res_global.boxes.conf.cpu().numpy(), res_global.boxes.cls.cpu().numpy()):
                name = self.model.names[int(cls_id)].lower()
                if name in ['boat', 'ship'] and conf >= self.conf_thresh:
                    x1, y1, x2, y2 = map(int, b)
                    crop_mask = dilated_mask[max(0, y1):min(img_h, y2), max(0, x1):min(img_w, x2)]
                    water_ratio = (np.sum(crop_mask > 0) / crop_mask.size) if crop_mask.size > 0 else 0
                    if water_ratio >= 0.25:
                        cand_boxes.append(b)
                        cand_scores.append(conf)
                        cand_classes.append(name)
                elif name == 'person' and conf >= self.person_conf:
                    x1, y1, x2, y2 = map(int, b)
                    bw, bh = x2 - x1, y2 - y1
                    if bw < 80 and bh < 160:
                        crop_mask = dilated_mask[max(0, y1):min(img_h, y2), max(0, x1):min(img_w, x2)]
                        water_ratio = (np.sum(crop_mask > 0) / crop_mask.size) if crop_mask.size > 0 else 0
                        if water_ratio >= 0.50:
                            cand_boxes.append(b)
                            cand_scores.append(conf)
                            cand_classes.append(name)

        # 2. Water-Domain Guided SAHI Slicing (Optional)
        if self.enable_sahi:
            slices = generate_water_slices(img_h, img_w, water_mask, slice_size=self.slice_size, overlap_ratio=self.overlap_ratio)
            if slices:
                slice_crops = [frame[ymin:ymax, xmin:xmax] for (xmin, ymin, xmax, ymax) in slices]
                try:
                    slice_res = self.model.predict(source=slice_crops, imgsz=self.slice_size, conf=infer_conf, device=self.device, verbose=False)
                except RuntimeError as e:
                    if "CUDA" in str(e):
                        self._fallback_to_cpu()
                        slice_res = self.model.predict(source=slice_crops, imgsz=self.slice_size, conf=infer_conf, device=self.device, verbose=False)
                    else:
                        raise
                for idx, sres in enumerate(slice_res):
                    if sres.boxes is None or len(sres.boxes) == 0:
                        continue
                    xmin, ymin, xmax, ymax = slices[idx]
                    for b, conf, cls_id in zip(sres.boxes.xyxy.cpu().numpy(), sres.boxes.conf.cpu().numpy(), sres.boxes.cls.cpu().numpy()):
                        name = self.model.names[int(cls_id)].lower()
                        if name in ['boat', 'ship'] and conf >= self.conf_thresh:
                            gx1 = b[0] + xmin
                            gy1 = b[1] + ymin
                            gx2 = b[2] + xmin
                            gy2 = b[3] + ymin
                            crop_mask = dilated_mask[max(0, int(gy1)):min(img_h, int(gy2)), max(0, int(gx1)):min(img_w, int(gx2))]
                            water_ratio = (np.sum(crop_mask > 0) / crop_mask.size) if crop_mask.size > 0 else 0
                            if water_ratio >= 0.25:
                                cand_boxes.append([gx1, gy1, gx2, gy2])
                                cand_scores.append(conf)
                                cand_classes.append(name)
                        elif name == 'person' and conf >= self.person_conf:
                            gx1 = b[0] + xmin
                            gy1 = b[1] + ymin
                            gx2 = b[2] + xmin
                            gy2 = b[3] + ymin
                            crop_mask = dilated_mask[max(0, int(gy1)):min(img_h, int(gy2)), max(0, int(gx1)):min(img_w, int(gx2))]
                            water_ratio = (np.sum(crop_mask > 0) / crop_mask.size) if crop_mask.size > 0 else 0
                            if water_ratio >= 0.50:
                                cand_boxes.append([gx1, gy1, gx2, gy2])
                                cand_scores.append(conf)
                                cand_classes.append(name)

        if not cand_boxes:
            return self.tracker.update([], frame)

        # 3. Class-Agnostic NMS (via native cv2.dnn.NMSBoxes, eliminating torchvision dependency)
        boxes_xywh = [[int(b[0]), int(b[1]), int(b[2] - b[0]), int(b[3] - b[1])] for b in cand_boxes]
        keep_indices = cv2.dnn.NMSBoxes(boxes_xywh, cand_scores, score_threshold=self.conf_thresh, nms_threshold=self.iou_thresh)
        keep_indices = np.array(keep_indices).flatten().tolist() if len(keep_indices) > 0 else []

        final_boxes = [cand_boxes[k] for k in keep_indices]
        final_scores = [cand_scores[k] for k in keep_indices]
        final_classes = [cand_classes[k] for k in keep_indices]

        # 4. Hierarchical IoS Suppression (Intersection over Smaller Area)
        if len(final_boxes) > 1:
            keep_mask = np.ones(len(final_boxes), dtype=bool)
            for i in range(len(final_boxes)):
                if not keep_mask[i]:
                    continue
                ax1, ay1, ax2, ay2 = final_boxes[i]
                area_a = (ax2 - ax1) * (ay2 - ay1)
                for j in range(len(final_boxes)):
                    if i == j or not keep_mask[j]:
                        continue
                    bx1, by1, bx2, by2 = final_boxes[j]
                    area_b = (bx2 - bx1) * (by2 - by1)

                    inter = max(0, min(ax2, bx2) - max(ax1, bx1)) * max(0, min(ay2, by2) - max(ay1, by1))
                    if inter > 0:
                        ios = inter / float(min(area_a, area_b))
                        if ios > 0.45:
                            if area_a > 1.8 * area_b:
                                keep_mask[j] = False
                            elif area_b > 1.8 * area_a:
                                keep_mask[i] = False
                                break
                            else:
                                if final_scores[i] >= final_scores[j]:
                                    keep_mask[j] = False
                                else:
                                    keep_mask[i] = False
                                    break

            final_boxes = [final_boxes[k] for k in range(len(final_boxes)) if keep_mask[k]]
            final_scores = [final_scores[k] for k in range(len(final_scores)) if keep_mask[k]]
            final_classes = [final_classes[k] for k in range(len(final_classes)) if keep_mask[k]]

        # 5. Deterministic Vessel Classification
        detections = []
        for box, score, orig_cls in zip(final_boxes, final_scores, final_classes):
            x1, y1, x2, y2 = map(int, box[:4])
            crop = frame[max(0, y1):min(img_h, y2), max(0, x1):min(img_w, x2)]
            fine_id, fine_name = classify_maritime_object(box, orig_cls, img_h, img_w, crop=crop)
            detections.append((box, score, fine_id, fine_name))

        annotated_frame, tracked_objects = self.tracker.update(detections, frame)
        return annotated_frame, tracked_objects

def run_pipeline(
    source_path: str,
    weights_path: str = "models/yolov8s.pt",
    output_dir: str = "output/videos",
    global_imgsz: int = 1280,
    enable_sahi: bool = False,
    conf_thresh: float = 0.50,
    person_conf: float = 0.25,
    iou_thresh: float = 0.35,
    last_sec: int = None,
    max_dim: int = 1920,
    device: str = None
):
    source_file = Path(source_path)
    if not source_file.is_file():
        alt = Path(__file__).resolve().parent.parent / "input" / source_file.name
        if alt.is_file():
            source_file = alt
        else:
            raise FileNotFoundError(f"Input video not found: {source_path}")

    out_dir = Path(output_dir)
    if not out_dir.is_absolute():
        out_dir = Path(__file__).resolve().parent.parent / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    engine = MaritimePerceptionEngine(
        weights_path=weights_path,
        global_imgsz=global_imgsz,
        enable_sahi=enable_sahi,
        conf_thresh=conf_thresh,
        person_conf=person_conf,
        iou_thresh=iou_thresh,
        device=device
    )

    cap = cv2.VideoCapture(str(source_file))
    orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    start_frame = 0
    if last_sec is not None and last_sec > 0:
        target_frames = int(last_sec * fps)
        start_frame = max(0, total_frames - target_frames)
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        print(f"⏩ Processing LAST {last_sec} SECONDS (Frames {start_frame} to {total_frames}, {total_frames - start_frame} frames total)...")

    width, height = orig_w, orig_h
    scale_factor = 1.0
    if max_dim is not None and max(orig_w, orig_h) > max_dim:
        scale_factor = float(max_dim) / float(max(orig_w, orig_h))
        width = int(orig_w * scale_factor)
        height = int(orig_h * scale_factor)

    sec_tag = f"_last{last_sec}s" if last_sec else ""
    out_video_path = out_dir / f"{source_file.stem}_detected{sec_tag}.mp4"
    out_json_path = out_video_path.with_suffix(".json")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(out_video_path), fourcc, fps, (width, height))

    frames_to_process = total_frames - start_frame
    metadata = {
        "video_info": {
            "source_file": source_file.name,
            "output_video": out_video_path.name,
            "fps": fps,
            "resolution": {"width": width, "height": height},
            "original_resolution": {"width": orig_w, "height": orig_h},
            "total_frames": total_frames,
            "processed_frames": frames_to_process,
            "start_frame": start_frame,
            "end_frame": total_frames
        },
        "parameters": {
            "weights": str(Path(weights_path).name),
            "global_imgsz": global_imgsz,
            "conf_thresh": conf_thresh,
            "person_conf": person_conf,
            "iou_thresh": iou_thresh,
            "device": str(device)
        },
        "summary": {
            "total_detections": 0,
            "unique_vessels_tracked": 0,
            "classes_detected": {}
        },
        "frames": []
    }

    print(f"🎬 Processing {frames_to_process} frames ({width}x{height} @ {fps:.1f} FPS)...")

    frame_idx = start_frame
    start_time = time.time()

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        frame_idx += 1
        t0 = time.time()

        if scale_factor != 1.0 and frame is not None:
            frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)

        annotated_frame, tracked_objects = engine.process_frame(frame)
        writer.write(annotated_frame)

        if tracked_objects:
            metadata["frames"].append({
                "frame": frame_idx,
                "timestamp_sec": round(float((frame_idx - start_frame) / fps), 3),
                "vessel_count": len(tracked_objects),
                "tracked_objects": tracked_objects
            })

        dt = (time.time() - t0) * 1000.0

        if frame_idx % 30 == 0:
            import gc
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        processed_count = frame_idx - start_frame
        if processed_count % 30 == 0 or frame_idx == total_frames:
            fps_current = 1000.0 / max(1.0, dt)
            print(f"Frame {frame_idx}/{total_frames} ({processed_count/frames_to_process*100:.1f}%) - {len(tracked_objects)} vessels tracked - {dt:.1f} ms/frame ({fps_current:.1f} FPS)")

    cap.release()
    writer.release()

    total_time = time.time() - start_time

    # Compute summary statistics
    all_tids = set()
    class_counts = {}
    total_det = 0
    for f_data in metadata["frames"]:
        for obj in f_data["tracked_objects"]:
            all_tids.add(obj["track_id"])
            cls_name = obj["class_name"]
            class_counts[cls_name] = class_counts.get(cls_name, 0) + 1
            total_det += 1

    metadata["summary"]["total_detections"] = total_det
    metadata["summary"]["unique_vessels_tracked"] = len(all_tids)
    metadata["summary"]["classes_detected"] = class_counts
    metadata["summary"]["total_processing_time_sec"] = round(total_time, 2)
    metadata["summary"]["avg_fps"] = round(float(frames_to_process / max(0.01, total_time)), 2)

    with open(out_json_path, "w", encoding="utf-8") as jf:
        json.dump(metadata, jf, indent=2)

    print("=" * 80)
    print(f"✅ Video Processing Completed in {total_time:.2f}s ({frames_to_process / total_time:.1f} FPS)!")
    print(f"🎬 Output video saved to: {out_video_path.resolve()}")
    print(f"📄 Detection JSON saved to: {out_json_path.resolve()}")
    print("=" * 80)
    return out_video_path
