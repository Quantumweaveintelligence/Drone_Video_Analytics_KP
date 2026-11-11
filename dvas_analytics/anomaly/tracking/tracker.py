"""
Multi-Object Tracker for Drone Surveillance.
Maintains persistent track IDs for persons and candidate objects independently,
integrating camera motion compensation into the association gate.
"""

from typing import Dict, List, Optional, Tuple
import numpy as np
from scipy.optimize import linear_sum_assignment

from detection.schema import Detection
from .track_state import ObjectState, TrackState


def bbox_iou(box1: List[float], box2: List[float]) -> float:
    """Calculates Intersection over Union (IoU) between two bounding boxes."""
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])

    inter_w = max(0.0, x2 - x1)
    inter_h = max(0.0, y2 - y1)
    inter_area = inter_w * inter_h

    area1 = max(0.0, box1[2] - box1[0]) * max(0.0, box1[3] - box1[1])
    area2 = max(0.0, box2[2] - box2[0]) * max(0.0, box2[3] - box2[1])
    union_area = area1 + area2 - inter_area

    if union_area <= 0.0:
        return 0.0
    return inter_area / union_area


def warp_bbox(bbox: List[float], H: Optional[np.ndarray]) -> List[float]:
    """Warps all 4 corners of a bounding box under a 3x3 transformation matrix."""
    if H is None or np.allclose(H, np.eye(3)):
        return list(bbox)
    pts = np.array([
        [bbox[0], bbox[1], 1.0],
        [bbox[2], bbox[1], 1.0],
        [bbox[0], bbox[3], 1.0],
        [bbox[2], bbox[3], 1.0],
    ], dtype=np.float32).T  # 3x4
    warped = H @ pts
    w = np.where(np.abs(warped[2]) > 1e-6, warped[2], 1.0)
    xs = warped[0] / w
    ys = warped[1] / w
    return [float(np.min(xs)), float(np.min(ys)), float(np.max(xs)), float(np.max(ys))]


class DroneMultiObjectTracker:
    """
    Decoupled tracker for drone feeds with camera-motion compensation.
    Tracks persons and candidate objects with persistent track IDs.
    """

    def __init__(
        self,
        max_lost_frames: int = 30,
        min_iou_thresh: float = 0.20,
        max_dist_thresh: float = 120.0,  # Max pixels centroid displacement
        human_classes: Optional[set] = None,
    ):
        self.max_lost_frames = max_lost_frames
        # v2 R6.5: a track expires after max_lost_frames at 20 fps worth of capture time (1.5 s by
        # default — what 30 misses meant at 20 fps), whatever the frame rate.
        self.max_lost_s = max_lost_frames / 20.0
        self.lost_since: Dict[int, float] = {}
        self._last_ts: Optional[float] = None
        self.min_iou_thresh = min_iou_thresh
        self.max_dist_thresh = max_dist_thresh
        self.human_classes = human_classes or {"pedestrian", "people", "person"}

        self.next_track_id = 1
        self.tracks: Dict[int, TrackState] = {}
        self.lost_frames: Dict[int, int] = {}

    def update(
        self,
        detections: List[Detection],
        timestamp: float,
        H_camera: Optional[np.ndarray] = None,
    ) -> List[TrackState]:
        """
        Updates the tracker with detections from current frame.

        :param detections: List of Detection instances in current frame
        :param timestamp: Current video timestamp in seconds
        :param H_camera: 3x3 camera transformation matrix from frame t-1 to frame t
        :return: List of active TrackState objects
        """
        frame_dt = (min(1.0, max(1.0 / 60.0, timestamp - self._last_ts))
                    if self._last_ts is not None else 1.0 / 20.0)
        self._last_ts = timestamp
        active_tracks = list(self.tracks.values())

        # If camera motion matrix is provided, warp existing track bounding boxes
        # so that positions stay anchored to the ground rather than screen pixels.
        compensated_boxes = []
        for trk in active_tracks:
            comp_box = warp_bbox(trk.bbox, H_camera)
            trk.bbox = comp_box  # Keep spatial coordinates updated with camera motion
            compensated_boxes.append(comp_box)

        # Match detections to existing tracks
        num_tracks = len(active_tracks)
        num_dets = len(detections)

        matched_tracks = set()
        matched_dets = set()

        VEHICLE_CLASSES = {
            "bicycle", "motor", "tricycle", "awning-tricycle", "car", "van", "truck",
            "bus", "motorcycle", "scooter", "vehicle"
        }

        if num_tracks > 0 and num_dets > 0:
            # Construct cost matrix combining IoU and centroid distance
            cost_matrix = np.zeros((num_tracks, num_dets), dtype=np.float32)

            for t_idx, trk in enumerate(active_tracks):
                trk_box = compensated_boxes[t_idx]
                trk_cx = (trk_box[0] + trk_box[2]) / 2.0
                trk_cy = (trk_box[1] + trk_box[3]) / 2.0

                for d_idx, det in enumerate(detections):
                    det_is_human = det.class_name.lower() in self.human_classes
                    det_is_vehicle = det.class_name.lower() in VEHICLE_CLASSES

                    # Class matching check: category-level consistency
                    if trk.is_person != det_is_human:
                        cost_matrix[t_idx, d_idx] = 1e6
                        continue

                    if trk.is_vehicle != det_is_vehicle:
                        cost_matrix[t_idx, d_idx] = 1e6
                        continue

                    if not trk.is_person and not trk.is_vehicle and trk.class_name != det.class_name:
                        cost_matrix[t_idx, d_idx] = 1e6
                        continue

                    iou = bbox_iou(trk_box, det.bbox)
                    det_cx, det_cy = det.center
                    dist = np.hypot(trk_cx - det_cx, trk_cy - det_cy)

                    # Gating threshold: allow close centroid even if IoU is small for tiny aerial bboxes
                    if iou < 0.10 and dist > self.max_dist_thresh:
                        cost_matrix[t_idx, d_idx] = 1e6
                    else:
                        # Cost: IoU penalty + normalized distance + minor sub-class change penalty
                        cost = (1.0 - iou) + (dist / (self.max_dist_thresh * 2.0))
                        if trk.class_name.lower() != det.class_name.lower():
                            cost += 0.15  # Small penalty, but allowable within category
                        cost_matrix[t_idx, d_idx] = cost

            row_ind, col_ind = linear_sum_assignment(cost_matrix)

            for r, c in zip(row_ind, col_ind):
                if cost_matrix[r, c] < 1.35:  # Robust cost threshold allowing aerial motion
                    trk = active_tracks[r]
                    det = detections[c]

                    # Update velocity estimate for smooth motion prediction
                    if len(trk.history) >= 1:
                        p_prev = trk.history[-1]
                        dt = max(1e-3, timestamp - p_prev[2])
                        vx = (det.center[0] - p_prev[0]) / dt
                        vy = (det.center[1] - p_prev[1]) / dt
                        trk.velocity = (0.7 * trk.velocity[0] + 0.3 * vx, 0.7 * trk.velocity[1] + 0.3 * vy)

                    # Update track state with measured detection
                    trk.bbox = det.bbox
                    trk.confidence = det.confidence
                    if not hasattr(trk, "class_history"):
                        trk.class_history = {}
                    trk.class_history[det.class_name] = trk.class_history.get(det.class_name, 0.0) + det.confidence
                    trk.class_name = max(trk.class_history.keys(), key=lambda k: trk.class_history[k])
                    trk.class_id = det.class_id
                    trk.last_seen_time = timestamp
                    trk.lost_frames = 0
                    trk.is_matched_this_frame = True
                    trk.history.append((det.center[0], det.center[1], timestamp))
                    if len(trk.history) > 120:
                        trk.history.pop(0)

                    self.lost_frames[trk.track_id] = 0
                    self.lost_since.pop(trk.track_id, None)
                    matched_tracks.add(trk.track_id)
                    matched_dets.add(c)

        # Handle unmatched existing tracks (extrapolate motion and increment lost frame count)
        for trk in active_tracks:
            if trk.track_id not in matched_tracks:
                cnt = self.lost_frames.get(trk.track_id, 0) + 1
                self.lost_frames[trk.track_id] = cnt
                self.lost_since.setdefault(trk.track_id, timestamp)
                trk.lost_frames = cnt
                trk.is_matched_this_frame = False

                # Extrapolate bounding box along estimated velocity vector for smooth tracking
                if cnt <= 5 and abs(trk.velocity[0]) > 0.1 or abs(trk.velocity[1]) > 0.1:
                    dt = frame_dt
                    dx = trk.velocity[0] * dt
                    dy = trk.velocity[1] * dt
                    trk.bbox = [
                        trk.bbox[0] + dx,
                        trk.bbox[1] + dy,
                        trk.bbox[2] + dx,
                        trk.bbox[3] + dy,
                    ]

        # Delete expired tracks
        expired = [tid for tid, since in self.lost_since.items()
                   if tid in self.lost_frames and timestamp - since >= self.max_lost_s - 0.025]
        for tid in expired:
            del self.tracks[tid]
            del self.lost_frames[tid]
            self.lost_since.pop(tid, None)

        # Initiate new tracks for unmatched detections (with recovery check against coasting tracks)
        for d_idx, det in enumerate(detections):
            if d_idx not in matched_dets:
                is_human = det.class_name.lower() in self.human_classes
                is_vehicle = det.class_name.lower() in VEHICLE_CLASSES

                # Check if this detection belongs to an existing coasting track
                recovered = False
                for trk in self.tracks.values():
                    if trk.track_id not in matched_tracks:
                        cat_match = (trk.is_person and is_human) or (trk.is_vehicle and is_vehicle)
                        if cat_match and (bbox_iou(trk.bbox, det.bbox) > 0.25):
                            # Recover coasting track with current detection
                            trk.bbox = det.bbox
                            trk.confidence = det.confidence
                            if not hasattr(trk, "class_history"):
                                trk.class_history = {}
                            trk.class_history[det.class_name] = trk.class_history.get(det.class_name, 0.0) + det.confidence
                            trk.class_name = max(trk.class_history.keys(), key=lambda k: trk.class_history[k])
                            trk.last_seen_time = timestamp
                            trk.lost_frames = 0
                            trk.is_matched_this_frame = True
                            trk.history.append((det.center[0], det.center[1], timestamp))
                            if len(trk.history) > 120:
                                trk.history.pop(0)
                            self.lost_frames[trk.track_id] = 0
                            self.lost_since.pop(trk.track_id, None)
                            matched_tracks.add(trk.track_id)
                            recovered = True
                            break

                if recovered:
                    continue

                # Guard against creating a duplicate track if an active track heavily overlaps
                is_duplicate = False
                for trk in self.tracks.values():
                    if trk.is_matched_this_frame:
                        cat_match = (trk.is_person and is_human) or (trk.is_vehicle and is_vehicle)
                        if cat_match and (bbox_iou(trk.bbox, det.bbox) > 0.35):
                            is_duplicate = True
                            break

                if is_duplicate:
                    continue

                tid = self.next_track_id
                self.next_track_id += 1

                new_trk = TrackState(
                    track_id=tid,
                    class_name=det.class_name,
                    class_id=det.class_id,
                    bbox=det.bbox,
                    confidence=det.confidence,
                    first_seen_time=timestamp,
                    last_seen_time=timestamp,
                    is_person=is_human,
                    history=[(det.center[0], det.center[1], timestamp)],
                    state=ObjectState.TRACKED if is_human else ObjectState.NEW,
                    lost_frames=0,
                    is_matched_this_frame=True,
                )
                new_trk.class_history = {det.class_name: det.confidence}
                self.tracks[tid] = new_trk
                self.lost_frames[tid] = 0
                self.lost_since.pop(tid, None)

        # Post-update track deduplication: eliminate overlapping dual tracks
        track_list = list(self.tracks.values())
        if len(track_list) > 1:
            suppressed_ids = set()
            for i in range(len(track_list)):
                t_i = track_list[i]
                if t_i.track_id in suppressed_ids:
                    continue
                for j in range(i + 1, len(track_list)):
                    t_j = track_list[j]
                    if t_j.track_id in suppressed_ids:
                        continue

                    iou = bbox_iou(t_i.bbox, t_j.bbox)
                    # Deduplicate if same category with IoU > 0.45, or cross-category with IoU > 0.75
                    should_dedup = False
                    if t_i.is_person == t_j.is_person and iou > 0.45:
                        should_dedup = True
                    elif iou > 0.75:
                        should_dedup = True

                    if should_dedup:
                        # Prioritize active matched over coasting
                        if t_i.is_matched_this_frame and not t_j.is_matched_this_frame:
                            suppressed_ids.add(t_j.track_id)
                        elif not t_i.is_matched_this_frame and t_j.is_matched_this_frame:
                            suppressed_ids.add(t_i.track_id)
                        elif len(t_i.history) >= len(t_j.history):
                            suppressed_ids.add(t_j.track_id)
                        else:
                            suppressed_ids.add(t_i.track_id)

            for sid in suppressed_ids:
                if sid in self.tracks:
                    del self.tracks[sid]
                if sid in self.lost_frames:
                    del self.lost_frames[sid]
                self.lost_since.pop(sid, None)

        return list(self.tracks.values())

