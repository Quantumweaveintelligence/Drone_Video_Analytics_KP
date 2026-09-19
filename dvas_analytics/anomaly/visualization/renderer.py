"""
Explainable Tactical HUD and Aerial Visualization Renderer.
Implements the Bounding Box & Annotation UI Specification:
- High visual priority on raw video scene (unobtrusive, information-dense).
- Standardized 8-color state-driven palette (Green, Blue, Yellow, Orange, Red, Purple, Cyan, Gray).
- Thin solid bounding boxes (2px normal/candidate, 3px suspicious/critical) with subtle L-corners.
- Semi-transparent dark navy label backgrounds (75-85% alpha) with crisp typography.
- Compact single-line labels by default; 2nd behavior line only when useful.
- Resolution-responsive font sizing (scaled for 720p/1080p/4K).
- Automatic label collision avoidance with multi-slot scoring (TOP, BOTTOM, SIDES, INSIDE).
- Thin 2px leader lines for detached labels or suspicious alert connections.
- Priority-based density filtering (suppresses low-confidence/unstable clutter).
- Minimal top HUD (CAM info and compact stats); alert banner only when alerts > 0.
- Fixed corner event panel displayed ONLY when active confirmed alerts exist.
- Normal Mode (clean operator UI) vs. Debug Mode (full telemetry).
"""

from typing import Any, Dict, List, Optional, Tuple
import cv2
import numpy as np

from tracking.track_state import EventType, ObjectState, PersonAction, SuspicionLevel, TrackState


# ===========================================================================
# 1. Standard Color Palette (BGR format)
# ===========================================================================
COLOR_PERSON = (118, 230, 0)      # 🟢 Green #00E676: Normal tracked person
COLOR_VEHICLE = (243, 150, 33)    # 🔵 Blue #2196F3: Vehicle (car, van, truck, bus, motor)
COLOR_OBJECT = (7, 193, 255)      # 🟡 Yellow #FFC107: Ordinary Object (bag, backpack, suitcase, package)
COLOR_CANDIDATE = (0, 152, 255)   # 🟠 Orange #FF9800: Suspicious Candidate (under investigation)
COLOR_SUSPICIOUS = (54, 67, 244)  # 🔴 Red #F44336: Confirmed Suspicious / High-Risk Event
COLOR_CRITICAL = (176, 39, 156)   # 🟣 Purple #9C27B0: Highest-priority confirmed event
COLOR_UNKNOWN = (212, 188, 0)     # 🔷 Cyan #00BCD4: Detection whose class is uncertain
COLOR_LOW_CONF = (158, 158, 158)  # ⚪ Gray #9E9E9E: Low confidence / ignored

LEVEL_COLORS = {
    SuspicionLevel.NORMAL: COLOR_PERSON,
    SuspicionLevel.OBSERVE: COLOR_CANDIDATE,
    SuspicionLevel.LOW: COLOR_CANDIDATE,
    SuspicionLevel.MEDIUM: COLOR_CANDIDATE,
    SuspicionLevel.HIGH: COLOR_SUSPICIOUS,
    SuspicionLevel.CRITICAL: COLOR_CRITICAL,
}


# ===========================================================================
# 2. State-Driven Color & Thickness Mapping
# ===========================================================================
def get_entity_style(entity: TrackState) -> Tuple[Tuple[int, int, int], int, bool]:
    """
    Returns (bgr_color, thickness, is_warning) based on the entity state hierarchy:
    - Low confidence / ignored (< 0.40) -> GRAY (2px)
    - CRITICAL -> PURPLE (3px, warning=True)
    - HIGH / SUSPICIOUS -> RED (3px, warning=True)
    - CANDIDATE / INVESTIGATING -> ORANGE (2px, warning=True)
    - NORMAL by Class:
        - Person -> GREEN (2px)
        - Vehicle -> BLUE (2px)
        - Ordinary Object -> YELLOW (2px)
        - Unknown -> CYAN (2px)
    """
    det_conf = entity.smoothed_detector_confidence or entity.detector_confidence or entity.confidence
    if det_conf < 0.40:
        return COLOR_LOW_CONF, 2, False

    # Severity State Progression (Critical -> High -> Candidate -> Normal)
    if entity.suspicion_level == SuspicionLevel.CRITICAL:
        return COLOR_CRITICAL, 3, True
    elif (
        entity.suspicion_level == SuspicionLevel.HIGH
        or entity.state in {ObjectState.SUSPICIOUS, ObjectState.ALERTED}
        or getattr(entity, "is_alert_active", False)
    ):
        return COLOR_SUSPICIOUS, 3, True
    elif (
        entity.suspicion_level == SuspicionLevel.MEDIUM
        or entity.state in {ObjectState.UNATTENDED, ObjectState.OWNER_DEPARTING}
        or (not entity.is_vehicle and not entity.is_person and getattr(entity, "unattended_duration", 0.0) >= 5.0)
        or getattr(entity, "is_weapon_candidate", False)
        or (entity.is_person and getattr(entity, "is_loitering", False) and getattr(entity, "dwell_time", 0.0) >= 30.0)
        or (entity.is_person and getattr(entity, "following_duration", 0.0) >= 15.0)
    ):
        return COLOR_CANDIDATE, 2, True

    # Normal State by Object Class
    if entity.is_person:
        return COLOR_PERSON, 2, False
    elif entity.is_vehicle:
        return COLOR_VEHICLE, 2, False
    elif entity.class_name.lower() in {"backpack", "bag", "suitcase", "package", "box", "handbag"}:
        return COLOR_OBJECT, 2, False
    elif entity.class_name.lower() in {"handgun", "weapon", "knife"}:
        return COLOR_CANDIDATE, 2, True
    else:
        return COLOR_UNKNOWN, 2, False


# ===========================================================================
# 3. Priority-Based Density Filtering
# ===========================================================================
def should_render_entity(entity: TrackState, debug_mode: bool = False) -> bool:
    """
    Evaluates whether an entity should be displayed on screen based on the priority hierarchy:
    Priority 1: Suspicious / Active Alerts -> Always show
    Priority 2: Objects being investigated (Candidate) -> Always show
    Priority 3: Nearby objects related to suspicious event -> Always show
    Priority 4: Tracked entities with adequate confidence / stability -> Show minimal
    Priority 5: Low-confidence detections -> Hidden
    """
    if debug_mode:
        return True

    # Priority 0: Never render stale coasting tracks (eliminates ghost boxes that lag/freeze)
    if not getattr(entity, "is_matched_this_frame", True) and getattr(entity, "lost_frames", 0) > 1:
        return False

    # Priority 1 & 2: Alert or Candidate -> Always show
    if entity.suspicion_level in {SuspicionLevel.HIGH, SuspicionLevel.CRITICAL} or getattr(entity, "is_alert_active", False):
        return True
    if entity.state in {ObjectState.SUSPICIOUS, ObjectState.ALERTED}:
        return True
    if entity.suspicion_level in {SuspicionLevel.MEDIUM, SuspicionLevel.LOW, SuspicionLevel.OBSERVE}:
        return True
    if getattr(entity, "unattended_duration", 0.0) >= 5.0 or getattr(entity, "is_loitering", False):
        return True

    det_conf = entity.smoothed_detector_confidence or entity.detector_confidence or entity.confidence
    track_age = len(entity.history)

    # Base operational cutoff: conf < 0.40 -> do not display
    if det_conf < 0.40:
        return False

    # Vehicle filtering: < 0.60 hide unless track becomes stable (>= 15 frames)
    if entity.is_vehicle:
        if det_conf < 0.60 and track_age < 15:
            return False

    # Person filtering: < 0.50 hide unless track becomes stable (>= 15 frames)
    if entity.is_person:
        if det_conf < 0.50 and track_age < 15:
            return False

    # Weapon-like filtering: < 0.80 hide unless stable
    if entity.class_name.lower() in {"handgun", "weapon", "knife"}:
        if det_conf < 0.80 and track_age < 10:
            return False

    return True


# ===========================================================================
# 4. Compact Label Builder
# ===========================================================================
def build_compact_label(
    entity: TrackState,
    debug_mode: bool = False,
) -> Tuple[str, Optional[str]]:
    """
    Builds compact, information-dense label strings according to the specification.
    Normal: single-line label (#223 PERSON 0.78, #199 CAR 0.77, #421 BACKPACK 0.87).
    Behavior line is only added when meaningful (Walking, Running, Stopped, Monitoring · 24s, SUSPICIOUS · 0.91).
    """
    det_conf = entity.smoothed_detector_confidence or entity.detector_confidence or entity.confidence
    cls_name = entity.class_name.upper()

    # Confirmed Suspicious
    if (
        entity.suspicion_level in {SuspicionLevel.HIGH, SuspicionLevel.CRITICAL}
        or entity.state in {ObjectState.SUSPICIOUS, ObjectState.ALERTED}
        or getattr(entity, "is_alert_active", False)
    ):
        if entity.suspicion_level == SuspicionLevel.CRITICAL or getattr(entity, "is_weapon_candidate", False):
            line1 = f"[!] #{entity.track_id} {cls_name}"
            line2 = f"HIGH RISK | {entity.event_confidence or det_conf:.2f}"
        else:
            line1 = f"[!] #{entity.track_id} {cls_name}"
            line2 = f"SUSPICIOUS | {entity.event_confidence or det_conf:.2f}"
        return line1, line2

    # Suspicious Candidate (under investigation)
    if (
        entity.suspicion_level == SuspicionLevel.MEDIUM
        or entity.state in {ObjectState.UNATTENDED, ObjectState.OWNER_DEPARTING}
        or (not entity.is_vehicle and not entity.is_person and getattr(entity, "unattended_duration", 0.0) >= 5.0)
        or (entity.is_person and getattr(entity, "is_loitering", False) and getattr(entity, "dwell_time", 0.0) >= 30.0)
        or getattr(entity, "is_weapon_candidate", False)
    ):
        line1 = f"[!] #{entity.track_id} {cls_name} {det_conf:.2f}"
        unatt = getattr(entity, "unattended_duration", 0.0)
        if getattr(entity, "is_loitering", False):
            line2 = f"Loitering | {entity.dwell_time:.0f}s"
        elif unatt >= 5.0:
            line2 = f"Monitoring | {unatt:.0f}s"
        else:
            line2 = "Monitoring"
        return line1, line2

    # Normal Person
    if entity.is_person:
        line1 = f"#{entity.track_id} PERSON {det_conf:.2f}"
        line2 = None
        if getattr(entity, "is_running", False) or getattr(entity, "person_action", None) == PersonAction.RUNNING:
            line2 = "Running"
        elif getattr(entity, "following_target_id", None) is not None:
            line2 = f"Following #{entity.following_target_id}"
        elif debug_mode:
            line2 = "Walking" if entity.speed >= 8.0 else "Standing"
        return line1, line2

    # Normal Vehicle
    if entity.is_vehicle:
        line1 = f"#{entity.track_id} {cls_name} {det_conf:.2f}"
        if getattr(entity, "vehicle_stop_duration", 0.0) > 0 or entity.speed < 5.0:
            line2 = "Stopped"
        else:
            line2 = f"Moving | {entity.speed:.0f} px/s"
        return line1, line2

    # Normal Ordinary Object
    line1 = f"#{entity.track_id} {cls_name} {det_conf:.2f}"
    line2 = None
    if entity.suspicion_level == SuspicionLevel.OBSERVE:
        line2 = "Monitoring"
    elif debug_mode:
        if entity.owner_id is not None:
            line2 = f"Associated · Person #{entity.owner_id}"
        elif getattr(entity, "is_held", False):
            line2 = f"Held · Person #{entity.held_by_id or entity.owner_id}"
        else:
            line2 = f"Stat {entity.stationary_duration:.0f}s"
    elif getattr(entity, "is_held", False):
        line2 = "Carried"

    return line1, line2


# ===========================================================================
# 5. Automatic Label Collision Avoidance
# ===========================================================================
def find_optimal_label_position(
    bbox: List[float],
    label_w: int,
    label_h: int,
    placed_labels: List[Tuple[int, int, int, int]],
    all_bboxes: List[List[float]],
    img_w: int,
    img_h: int,
    top_hud_margin: int = 36,
) -> Tuple[int, int, bool]:
    """
    Selects label coordinates (x, y) that minimize overlap with other labels,
    bounding boxes, and image edges.
    Tries candidate positions: TOP -> TOP_CENTER -> TOP_RIGHT -> BOTTOM -> RIGHT -> LEFT -> INSIDE_TOP.
    Returns (best_x, best_y, is_detached).
    """
    bx1, by1, bx2, by2 = [int(v) for v in bbox]
    bw = max(1, bx2 - bx1)

    candidates = [
        # (name, x, y, is_detached, preference_cost)
        ("TOP", bx1, by1 - label_h - 3, False, 0.0),
        ("TOP_CENTER", bx1 + (bw - label_w) // 2, by1 - label_h - 3, False, 5.0),
        ("TOP_RIGHT", bx2 - label_w, by1 - label_h - 3, False, 10.0),
        ("BOTTOM", bx1, by2 + 3, False, 20.0),
        ("BOTTOM_CENTER", bx1 + (bw - label_w) // 2, by2 + 3, False, 25.0),
        ("RIGHT", bx2 + 4, by1, True, 40.0),
        ("LEFT", bx1 - label_w - 4, by1, True, 45.0),
        ("INSIDE_TOP", bx1 + 2, by1 + 2, False, 80.0),
    ]

    best_score = float("inf")
    best_pos = (bx1, max(top_hud_margin, by1 - label_h - 3))
    best_detached = False

    def _rect_overlap(r1: Tuple[int, int, int, int], r2: Tuple[int, int, int, int]) -> float:
        x_left = max(r1[0], r2[0])
        y_top = max(r1[1], r2[1])
        x_right = min(r1[0] + r1[2], r2[0] + r2[2])
        y_bottom = min(r1[1] + r1[3], r2[1] + r2[3])
        if x_right <= x_left or y_bottom <= y_top:
            return 0.0
        return float((x_right - x_left) * (y_bottom - y_top))

    for _, cx, cy, detached, pref_cost in candidates:
        score = pref_cost

        # Edge boundary penalties
        if cy < top_hud_margin:
            score += (top_hud_margin - cy) * 150.0
        if cy + label_h > img_h - 4:
            score += (cy + label_h - (img_h - 4)) * 150.0
        if cx < 2:
            score += (2 - cx) * 150.0
        if cx + label_w > img_w - 2:
            score += (cx + label_w - (img_w - 2)) * 150.0

        clamped_x = max(2, min(img_w - label_w - 2, cx))
        clamped_y = max(top_hud_margin, min(img_h - label_h - 2, cy))
        cand_rect = (clamped_x, clamped_y, label_w, label_h)

        # Overlap with previously placed labels (heavy penalty)
        for pl in placed_labels:
            ov = _rect_overlap(cand_rect, pl)
            if ov > 0:
                score += ov * 30.0

        # Overlap with other entities' bounding boxes (moderate penalty)
        for ob in all_bboxes:
            if ob is bbox:
                continue
            ob_rect = (int(ob[0]), int(ob[1]), int(ob[2] - ob[0]), int(ob[3] - ob[1]))
            ov = _rect_overlap(cand_rect, ob_rect)
            if ov > 0:
                score += ov * 3.0

        if score < best_score:
            best_score = score
            best_pos = (clamped_x, clamped_y)
            best_detached = detached

    return best_pos[0], best_pos[1], best_detached


# ===========================================================================
# 6. Semi-Transparent Label Badge & Bounding Box Drawer
# ===========================================================================
def draw_semi_transparent_label(
    image: np.ndarray,
    lx: int,
    ly: int,
    lw: int,
    lh: int,
    line1: str,
    line2: Optional[str],
    border_color: Tuple[int, int, int],
    text_color: Tuple[int, int, int],
    font_scale: float,
    opacity: float = 0.0,
):
    """
    Renders clean annotation text without black background box.
    Uses the exact same color as the bounding box with a 1px subtle dark shadow for legibility.
    """
    font = cv2.FONT_HERSHEY_SIMPLEX
    if not line2:
        l1_y = ly + int(lh * 0.75)
        # Subtle 1px shadow for high contrast on light backgrounds without any black box
        cv2.putText(image, line1, (lx + 1, l1_y + 1), font, font_scale, (0, 0, 0), 1, cv2.LINE_AA)
        cv2.putText(image, line1, (lx, l1_y), font, font_scale, text_color, 1, cv2.LINE_AA)
    else:
        l1_y = ly + int(lh * 0.45)
        l2_y = ly + int(lh * 0.88)
        cv2.putText(image, line1, (lx + 1, l1_y + 1), font, font_scale, (0, 0, 0), 1, cv2.LINE_AA)
        cv2.putText(image, line1, (lx, l1_y), font, font_scale, text_color, 1, cv2.LINE_AA)
        cv2.putText(image, line2, (lx + 1, l2_y + 1), font, font_scale * 0.88, (0, 0, 0), 1, cv2.LINE_AA)
        cv2.putText(image, line2, (lx, l2_y), font, font_scale * 0.88, text_color, 1, cv2.LINE_AA)


def draw_detection_box(
    image: np.ndarray,
    bbox: List[float],
    color: Tuple[int, int, int],
    thickness: int,
    line1: str,
    line2: Optional[str] = None,
    is_warning: bool = False,
    is_critical: bool = False,
    frame_idx: int = 0,
    placed_labels: Optional[List[Tuple[int, int, int, int]]] = None,
    all_bboxes: Optional[List[List[float]]] = None,
):
    """
    Renders thin bounding box with subtle corner L-brackets and automatically positioned
    floating text matching the exact color of the box (with zero black background).
    """
    x1, y1, x2, y2 = [int(v) for v in bbox]
    h_img, w_img = image.shape[:2]

    # Clamp coordinates to image
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w_img - 1, x2), min(h_img - 1, y2)
    w_box, h_box = max(1, x2 - x1), max(1, y2 - y1)

    # 1. Solid Rectangle Border
    cv2.rectangle(image, (x1, y1), (x2, y2), color, thickness)

    # 2. Subtle Corner L-Brackets (Thin and clean)
    c_len = min(10, max(5, int(min(w_box, h_box) * 0.18)))
    c_thick = thickness
    cv2.line(image, (x1, y1), (x1 + c_len, y1), color, c_thick)
    cv2.line(image, (x1, y1), (x1, y1 + c_len), color, c_thick)
    cv2.line(image, (x2, y1), (x2 - c_len, y1), color, c_thick)
    cv2.line(image, (x2, y1), (x2, y1 + c_len), color, c_thick)
    cv2.line(image, (x1, y2), (x1 + c_len, y2), color, c_thick)
    cv2.line(image, (x1, y2), (x1, y2 - c_len), color, c_thick)
    cv2.line(image, (x2, y2), (x2 - c_len, y2), color, c_thick)
    cv2.line(image, (x2, y2), (x2, y2 - c_len), color, c_thick)

    # 3. Dynamic Font & Label Sizing
    font_scale = max(0.33, min(0.50, h_img / 1080.0 * 0.42))
    font = cv2.FONT_HERSHEY_SIMPLEX

    (w1, h1), _ = cv2.getTextSize(line1, font, font_scale, 1)
    w2, h2 = (0, 0)
    if line2:
        (w2, h2), _ = cv2.getTextSize(line2, font, font_scale * 0.88, 1)

    badge_w = max(w1, w2) + 6
    badge_h = (h1 + h2 + 8) if line2 else (h1 + 6)

    # 4. Collision Avoidance Position Finding
    placed_labels_list = placed_labels if placed_labels is not None else []
    all_bboxes_list = all_bboxes if all_bboxes is not None else []

    lx, ly, is_detached = find_optimal_label_position(
        bbox=bbox,
        label_w=badge_w,
        label_h=badge_h,
        placed_labels=placed_labels_list,
        all_bboxes=all_bboxes_list,
        img_w=w_img,
        img_h=h_img,
        top_hud_margin=34,
    )

    if placed_labels is not None:
        placed_labels.append((lx, ly, badge_w, badge_h))

    # 5. Thin Leader Line if detached (e.g. pushed to side to avoid overlap)
    if is_detached:
        box_cx = (x1 + x2) // 2
        box_cy = y1 if ly < y1 else (y2 if ly > y2 else (y1 + y2) // 2)
        lbl_cx = lx + badge_w // 2
        lbl_cy = ly + badge_h // 2
        cv2.line(image, (lbl_cx, lbl_cy), (box_cx, box_cy), color, 1, cv2.LINE_AA)

    # 6. Render Floating Text in the Exact Box Color (Zero Black Background)
    draw_semi_transparent_label(
        image=image,
        lx=lx,
        ly=ly,
        lw=badge_w,
        lh=badge_h,
        line1=line1,
        line2=line2,
        border_color=color,
        text_color=color,  # Exact same color as the box!
        font_scale=font_scale,
        opacity=0.0,
    )


# ===========================================================================
# 7. Explainable Visualizer Main Class
# ===========================================================================
class ExplainableVisualizer:
    """
    Renders tactical HUD and aerial video overlays adhering strictly to the
    Bounding Box & Annotation UI Specification (unobtrusive, state-driven, anti-collision).
    """

    def __init__(self, font_scale: float = 0.42, bbox_thickness: int = 2, debug_mode: bool = False):
        self.font_scale = font_scale
        self.bbox_thickness = bbox_thickness
        self.debug_mode = debug_mode

    def render(
        self,
        frame: np.ndarray,
        tracks: List[TrackState],
        frame_idx: int,
        total_frames: int,
        timestamp_sec: float,
        timestamp_formatted: str,
        fps: float,
        camera_dx: float = 0.0,
        camera_dy: float = 0.0,
        alerts: Optional[List[Dict[str, Any]]] = None,
        debug_mode: Optional[bool] = None,
    ) -> np.ndarray:
        h, w = frame.shape[:2]
        output = frame  # In-place rendering
        is_debug = debug_mode if debug_mode is not None else self.debug_mode

        def _iou(b1, b2) -> float:
            x1 = max(b1[0], b2[0])
            y1 = max(b1[1], b2[1])
            x2 = min(b1[2], b2[2])
            y2 = min(b1[3], b2[3])
            inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
            a1 = max(0.0, b1[2] - b1[0]) * max(0.0, b1[3] - b1[1])
            a2 = max(0.0, b2[2] - b2[0]) * max(0.0, b2[3] - b2[1])
            union = a1 + a2 - inter
            return inter / union if union > 0 else 0.0

        # -------------------------------------------------------------
        # 1. Density Filtering & Priority Selection (Req 19, 20, 32)
        # -------------------------------------------------------------
        visible_tracks = [t for t in tracks if should_render_entity(t, debug_mode=is_debug)]

        # Deduplicate overlapping detections
        if len(visible_tracks) > 1:
            def _rank(e):
                is_alert = 2 if e.suspicion_level in {SuspicionLevel.HIGH, SuspicionLevel.CRITICAL} else (
                    1 if e.suspicion_level in {SuspicionLevel.MEDIUM, SuspicionLevel.LOW, SuspicionLevel.OBSERVE} else 0
                )
                return (is_alert, len(e.history), e.confidence)

            visible_tracks.sort(key=_rank, reverse=True)
            suppressed = set()
            clean_tracks = []
            for i, t_i in enumerate(visible_tracks):
                if i in suppressed:
                    continue
                clean_tracks.append(t_i)
                for j in range(i + 1, len(visible_tracks)):
                    if j in suppressed:
                        continue
                    # Suppress overlapping boxes of same category
                    same_cat = (t_i.is_person == visible_tracks[j].is_person)
                    if same_cat and _iou(t_i.bbox, visible_tracks[j].bbox) > 0.45:
                        suppressed.add(j)
            visible_tracks = clean_tracks

        all_bboxes = [t.bbox for t in visible_tracks]
        placed_labels: List[Tuple[int, int, int, int]] = []

        # Count entities for compact top HUD
        person_count = sum(1 for t in visible_tracks if t.is_person)
        vehicle_count = sum(1 for t in visible_tracks if t.is_vehicle)
        object_count = sum(1 for t in visible_tracks if not t.is_person and not t.is_vehicle)
        alert_count = len(alerts) if alerts is not None else sum(
            1 for t in visible_tracks if getattr(t, "is_alert_active", False) or t.state == ObjectState.ALERTED or (t.suspicion_level in {SuspicionLevel.HIGH, SuspicionLevel.CRITICAL} and getattr(t, "alert_triggered", False))
        )

        highest_alert_entity: Optional[TrackState] = None
        max_severity = -1

        # -------------------------------------------------------------
        # 2. Draw Entities (with Collision-Avoidance Labels)
        # -------------------------------------------------------------
        for entity in visible_tracks:
            color, thick, is_warn = get_entity_style(entity)
            line1, line2 = build_compact_label(entity, debug_mode=is_debug)
            is_crit = (entity.suspicion_level == SuspicionLevel.CRITICAL)

            draw_detection_box(
                image=output,
                bbox=entity.bbox,
                color=color,
                thickness=thick,
                line1=line1,
                line2=line2,
                is_warning=is_warn,
                is_critical=is_crit,
                frame_idx=frame_idx,
                placed_labels=placed_labels,
                all_bboxes=all_bboxes,
            )

            # Check if this entity should populate the fixed event panel
            is_active_alert_entity = getattr(entity, "is_alert_active", False) or entity.state == ObjectState.ALERTED or (entity.suspicion_level in {SuspicionLevel.HIGH, SuspicionLevel.CRITICAL} and getattr(entity, "alert_triggered", False))
            if is_active_alert_entity:
                sev = 3 if entity.suspicion_level == SuspicionLevel.CRITICAL else 2
                if sev > max_severity and entity.explainability_reasons:
                    max_severity = sev
                    highest_alert_entity = entity

        # -------------------------------------------------------------
        # 3. Top HUD: Minimal & Unobtrusive (Req 25, 26, 27)
        # -------------------------------------------------------------
        # Top-Left Tag: CAM-01 | DRONE | {w}x{h}
        hud_font = cv2.FONT_HERSHEY_SIMPLEX
        top_left_text = f"CAM-01 | DRONE | {w}x{h} | {timestamp_formatted}"
        (tl_w, tl_h), _ = cv2.getTextSize(top_left_text, hud_font, 0.38, 1)

        # Semi-transparent top-left badge
        tl_box = (10, 8, tl_w + 14, 22)
        sub_tl = output[tl_box[1]:tl_box[1] + tl_box[3], tl_box[0]:tl_box[0] + tl_box[2]]
        bg_tl = np.full_like(sub_tl, (14, 18, 24), dtype=np.uint8)
        output[tl_box[1]:tl_box[1] + tl_box[3], tl_box[0]:tl_box[0] + tl_box[2]] = cv2.addWeighted(bg_tl, 0.80, sub_tl, 0.20, 0)
        cv2.rectangle(output, (tl_box[0], tl_box[1]), (tl_box[0] + tl_box[2], tl_box[1] + tl_box[3]), (50, 60, 70), 1)
        cv2.putText(output, top_left_text, (17, 23), hud_font, 0.38, (220, 230, 240), 1, cv2.LINE_AA)

        # Top-Right Statistics Tag
        stats_text = f"P: {person_count}  |  V: {vehicle_count}  |  O: {object_count}  |  ALERTS: {alert_count}"
        (st_w, st_h), _ = cv2.getTextSize(stats_text, hud_font, 0.38, 1)
        tr_x = max(10, w - st_w - 24)
        tr_box = (tr_x, 8, st_w + 14, 22)
        sub_tr = output[tr_box[1]:tr_box[1] + tr_box[3], tr_box[0]:tr_box[0] + tr_box[2]]
        bg_tr = np.full_like(sub_tr, (14, 18, 24), dtype=np.uint8)
        output[tr_box[1]:tr_box[1] + tr_box[3], tr_box[0]:tr_box[0] + tr_box[2]] = cv2.addWeighted(bg_tr, 0.80, sub_tr, 0.20, 0)
        border_tr = COLOR_SUSPICIOUS if alert_count > 0 else (50, 60, 70)
        cv2.rectangle(output, (tr_box[0], tr_box[1]), (tr_box[0] + tr_box[2], tr_box[1] + tr_box[3]), border_tr, 1)
        cv2.putText(output, stats_text, (tr_x + 7, 23), hud_font, 0.38, (220, 230, 240), 1, cv2.LINE_AA)

        # Alert Banner: ONLY shown when an actual alert exists (Req 27)
        if alert_count > 0:
            banner_text = f"[!] {alert_count} HIGH-RISK EVENT"
            (bn_w, _), _ = cv2.getTextSize(banner_text, hud_font, 0.40, 1)
            bn_x = (w - bn_w - 16) // 2
            bn_box = (bn_x, 8, bn_w + 16, 22)
            sub_bn = output[bn_box[1]:bn_box[1] + bn_box[3], bn_box[0]:bn_box[0] + bn_box[2]]
            bg_bn = np.full_like(sub_bn, (20, 20, 180), dtype=np.uint8)
            output[bn_box[1]:bn_box[1] + bn_box[3], bn_box[0]:bn_box[0] + bn_box[2]] = cv2.addWeighted(bg_bn, 0.85, sub_bn, 0.15, 0)
            cv2.rectangle(output, (bn_box[0], bn_box[1]), (bn_box[0] + bn_box[2], bn_box[1] + bn_box[3]), COLOR_SUSPICIOUS, 1)
            cv2.putText(output, banner_text, (bn_x + 8, 23), hud_font, 0.40, (255, 255, 255), 1, cv2.LINE_AA)

        # -------------------------------------------------------------
        # 4. Fixed Corner Event Panel: ONLY when alert exists (Req 14, 15)
        # -------------------------------------------------------------
        if highest_alert_entity is not None and alert_count > 0:
            panel_w = min(320, int(w * 0.32))
            panel_h = 130
            panel_x = w - panel_w - 12
            panel_y = h - panel_h - 12

            # Semi-transparent dark navy background
            sub_p = output[panel_y:panel_y + panel_h, panel_x:panel_x + panel_w]
            bg_p = np.full_like(sub_p, (14, 18, 24), dtype=np.uint8)
            output[panel_y:panel_y + panel_h, panel_x:panel_x + panel_w] = cv2.addWeighted(bg_p, 0.85, sub_p, 0.15, 0)

            # Red/Purple state border & header strip
            ent_color = COLOR_CRITICAL if highest_alert_entity.suspicion_level == SuspicionLevel.CRITICAL else COLOR_SUSPICIOUS
            cv2.rectangle(output, (panel_x, panel_y), (panel_x + panel_w, panel_y + panel_h), ent_color, 1)
            cv2.rectangle(output, (panel_x, panel_y), (panel_x + panel_w, panel_y + 20), ent_color, -1)

            # Header text
            title_text = f"[!] SUSPICIOUS EVENT -- {highest_alert_entity.class_name.upper()} #{highest_alert_entity.track_id}"
            cv2.putText(output, title_text, (panel_x + 8, panel_y + 14), hud_font, 0.38, (255, 255, 255), 1, cv2.LINE_AA)

            # Metrics
            risk_label = highest_alert_entity.suspicion_level.value
            conf_val = highest_alert_entity.event_confidence or highest_alert_entity.detector_confidence
            line_meta = f"Risk: {risk_label}  |  Event Conf: {conf_val:.2f}"
            cv2.putText(output, line_meta, (panel_x + 8, panel_y + 36), hud_font, 0.35, (210, 225, 230), 1, cv2.LINE_AA)

            cv2.putText(output, "WHY?", (panel_x + 8, panel_y + 52), hud_font, 0.35, ent_color, 1, cv2.LINE_AA)

            # Bullet points
            reasons = highest_alert_entity.explainability_reasons[:4]
            y_offset = panel_y + 68
            for r in reasons:
                clean_r = r.strip().replace("_", " ")
                bullet_text = f"- {clean_r}"
                cv2.putText(output, bullet_text[:40], (panel_x + 12, y_offset), hud_font, 0.33, (220, 230, 235), 1, cv2.LINE_AA)
                y_offset += 15

            # Leader line connecting event panel to the object
            obj_cx = int((highest_alert_entity.bbox[0] + highest_alert_entity.bbox[2]) / 2)
            obj_cy = int((highest_alert_entity.bbox[1] + highest_alert_entity.bbox[3]) / 2)
            cv2.line(output, (panel_x, panel_y + panel_h // 2), (obj_cx, obj_cy), ent_color, 1, cv2.LINE_AA)

        return output
