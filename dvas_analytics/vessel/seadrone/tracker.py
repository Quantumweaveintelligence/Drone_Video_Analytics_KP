# -*- coding: utf-8 -*-
"""
Zero-Flicker Maritime Multi-Object Tracker.
Eliminates bounding box shaking, transient flashes, and on/off blinking via:
- Adaptive Spatial Exponential Moving Average (EMA) smoothing
- Temporal Persistence & Velocity-guided Coasting
- Transient Noise Suppression
- 20-frame Rolling Window Majority Vote for stable class labels
"""

from collections import Counter
import cv2
import numpy as np
from .taxonomy import VESSEL_CLASSES, VESSEL_PALETTE

class MaritimeVesselTracker:
    def __init__(self, iou_thresh: float = 0.25, max_coast_frames: int = 8, max_lost_frames: int = 25):
        self.next_id = 1
        self.active_tracks = {}
        self.iou_thresh = iou_thresh
        self.max_coast_frames = max_coast_frames
        self.max_lost_frames = max_lost_frames

    def update(self, detections, frame):
        unmatched_dets = list(range(len(detections)))
        matched_track_ids = set()

        if self.active_tracks and detections:
            det_boxes = np.array([d[0] for d in detections])
            track_ids = list(self.active_tracks.keys())
            track_boxes = np.array([self.active_tracks[tid]['box'] for tid in track_ids])

            iou_matrix = self._compute_iou(track_boxes, det_boxes)

            matched_indices = []
            if iou_matrix.size > 0:
                for t_idx in range(len(track_ids)):
                    d_idx = int(np.argmax(iou_matrix[t_idx]))
                    if iou_matrix[t_idx, d_idx] >= self.iou_thresh:
                        matched_indices.append((t_idx, d_idx))
                        iou_matrix[:, d_idx] = -1.0

            for t_idx, d_idx in matched_indices:
                tid = track_ids[t_idx]
                box, score, fine_cls_id, fine_cls_name = detections[d_idx]
                current_box = np.array(box, dtype=np.float32)
                prev_box = self.active_tracks[tid]['box']

                # Compute motion vector (velocity)
                vel = current_box - prev_box
                prev_vel = self.active_tracks[tid].get('vel', np.zeros(4, dtype=np.float32))
                smoothed_vel = 0.6 * vel + 0.4 * prev_vel

                # Adaptive EMA spatial smoothing: kills wave vibration while locking to real motion
                shift = np.max(np.abs(vel))
                if shift < 10.0:
                    alpha = 0.60  # smooth out wave vibration / jitter
                elif shift < 25.0:
                    alpha = 0.80  # steady vessel glide
                else:
                    alpha = 0.95  # fast camera pan, snap immediately

                smoothed_box = alpha * current_box + (1.0 - alpha) * prev_box

                # Rolling window majority voting for class label stability (anti-flicker)
                self.active_tracks[tid]['cls_history'].append(fine_cls_id)
                if len(self.active_tracks[tid]['cls_history']) > 20:
                    self.active_tracks[tid]['cls_history'].pop(0)

                counts = Counter(self.active_tracks[tid]['cls_history'])
                majority_cls_id = counts.most_common(1)[0][0]

                smoothed_name = VESSEL_CLASSES.get(majority_cls_id, fine_cls_name)
                smoothed_color = VESSEL_PALETTE.get(majority_cls_id, (0, 255, 0))

                self.active_tracks[tid]['box'] = smoothed_box
                self.active_tracks[tid]['vel'] = smoothed_vel
                self.active_tracks[tid]['score'] = max(score, self.active_tracks[tid]['score'] * 0.95)
                self.active_tracks[tid]['cls_id'] = majority_cls_id
                self.active_tracks[tid]['cls_name'] = smoothed_name
                self.active_tracks[tid]['color'] = smoothed_color
                self.active_tracks[tid]['hits'] += 1
                self.active_tracks[tid]['lost_count'] = 0

                matched_track_ids.add(tid)
                if d_idx in unmatched_dets:
                    unmatched_dets.remove(d_idx)

        # Handle unmatched tracks (coasting across momentary detector drops)
        for tid in list(self.active_tracks.keys()):
            if tid not in matched_track_ids:
                trk = self.active_tracks[tid]
                trk['lost_count'] += 1
                if trk['lost_count'] <= self.max_coast_frames:
                    trk['box'] += trk.get('vel', np.zeros(4, dtype=np.float32)) * 0.5
                if trk['lost_count'] > self.max_lost_frames:
                    del self.active_tracks[tid]

        # Register new detections
        for d_idx in unmatched_dets:
            box, score, fine_cls_id, fine_cls_name = detections[d_idx]
            color = VESSEL_PALETTE.get(fine_cls_id, (0, 255, 0))

            tid = self.next_id
            self.next_id += 1
            self.active_tracks[tid] = {
                'box': np.array(box, dtype=np.float32),
                'vel': np.zeros(4, dtype=np.float32),
                'score': score,
                'cls_id': fine_cls_id,
                'cls_name': fine_cls_name,
                'color': color,
                'cls_history': [fine_cls_id],
                'hits': 1,
                'lost_count': 0
            }

        # Render confirmed and coasting tracks (ZERO FLICKER)
        annotated = frame.copy()
        h, w = frame.shape[:2]
        tracked_objects = []
        for tid, trk in self.active_tracks.items():
            if trk['hits'] < 2 and trk['score'] < 0.45:
                continue

            if trk['lost_count'] > self.max_coast_frames:
                continue

            color = trk['color']
            box = trk['box']
            x1, y1, x2, y2 = map(int, box)

            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w - 1, x2), min(h - 1, y2)
            if x2 <= x1 or y2 <= y1:
                continue

            tracked_objects.append({
                "track_id": int(tid),
                "class_id": int(trk['cls_id']),
                "class_name": str(trk['cls_name']),
                "confidence": round(float(trk['score']), 3),
                "bbox_xyxy": [int(x1), int(y1), int(x2), int(y2)],
                "bbox_xywh": [int(x1), int(y1), int(x2 - x1), int(y2 - y1)]
            })

            label_str = f"{trk['cls_name']} {trk['score']:.2f}"

            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            (txt_w, txt_h), _ = cv2.getTextSize(label_str, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
            cv2.rectangle(annotated, (x1, max(0, y1 - txt_h - 4)), (min(w - 1, x1 + txt_w + 4), y1), color, -1)
            cv2.putText(annotated, label_str, (x1 + 2, max(0, y1 - 2)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)

        return annotated, tracked_objects

    def _compute_iou(self, boxes1, boxes2):
        x11, y11, x12, y12 = boxes1[:, 0], boxes1[:, 1], boxes1[:, 2], boxes1[:, 3]
        x21, y21, x22, y22 = boxes2[:, 0], boxes2[:, 1], boxes2[:, 2], boxes2[:, 3]

        area1 = (x12 - x11) * (y12 - y11)
        area2 = (x22 - x21) * (y22 - y21)

        iou = np.zeros((len(boxes1), len(boxes2)), dtype=np.float32)
        for i in range(len(boxes1)):
            xx1 = np.maximum(x11[i], x21)
            yy1 = np.maximum(y11[i], y21)
            xx2 = np.minimum(x12[i], x22)
            yy2 = np.minimum(y12[i], y22)

            w = np.maximum(0.0, xx2 - xx1)
            h = np.maximum(0.0, yy2 - yy1)
            inter = w * h
            union = area1[i] + area2 - inter
            iou[i] = inter / np.maximum(union, 1e-6)

        return iou
