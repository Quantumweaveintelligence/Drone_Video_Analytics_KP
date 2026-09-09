# -*- coding: utf-8 -*-
"""
annotator.py — Professional GIS / Surveillance Video Annotation Generator.

Adapted from intrusion_detection/core/intrusion/annotation.py.
Renders geofence overlays, class-coded bounding boxes, and telemetry HUD
onto drone video frames for the DVAS intrusion detection service.
"""

from typing import Dict, List, Optional, Tuple
import cv2
import numpy as np

# Color constants in BGR format
COLOR_BLACK = (0, 0, 0)
COLOR_WHITE = (255, 255, 255)
COLOR_YELLOW_INNER = (10, 214, 255)       # #FFD60A in BGR
COLOR_LIGHT_YELLOW_FILL = (120, 240, 255)  # Light yellow shade in BGR
COLOR_GREEN_INNER = (0, 220, 0)           # Green (BGR)
COLOR_RED_INNER = (0, 0, 255)             # Bright Red (BGR)
COLOR_CYAN_INNER = (255, 200, 0)          # Cyan (BGR)

CLASS_COLORS: Dict[str, Tuple[int, int, int]] = {
    "CAR": (0, 255, 0),              # Bright Green (#00FF00)
    "BUS": (255, 191, 0),            # Sky Blue / Cyan (#00BFFF)
    "TRUCK": (0, 165, 255),          # Orange (#FFA500)
    "MOTORCYCLE": (255, 0, 255),     # Magenta (#FF00FF)
    "AUTO": (0, 255, 255),           # Yellow (#FFFF00)
    "AUTORICKSHAW": (0, 255, 255),   # Yellow (#FFFF00)
    "KEKE": (0, 255, 255),           # Yellow (#FFFF00)
    "BICYCLE": (255, 255, 0),        # Cyan (#00FFFF)
    "PEOPLE": (180, 105, 255),       # Hot Pink / Rose (#FF69B4)
    "PEDESTRIAN": (180, 105, 255),   # Hot Pink / Rose (#FF69B4)
    "PEDESTRIANS": (180, 105, 255),  # Hot Pink / Rose (#FF69B4)
    "PERSON": (180, 105, 255),       # Hot Pink / Rose (#FF69B4)
}

# States considered as active intrusion
_INTRUSION_STATES = {"ENTERING", "INSIDE", "DWELLING"}


class IntrusionAnnotator:
    """Renders professional GIS/surveillance video frame annotations."""

    def __init__(
        self,
        show_telemetry_hud: bool = True,
        show_fence_coords: bool = True,
    ):
        self.show_telemetry_hud = show_telemetry_hud
        self.show_fence_coords = show_fence_coords

    def draw_text_with_bg(
        self,
        img: np.ndarray,
        text: str,
        org: Tuple[int, int],
        font_scale: float = 0.45,
        thickness: int = 1,
        text_color: Tuple[int, int, int] = COLOR_WHITE,
        bg_color: Tuple[int, int, int] = (20, 20, 20),
        alpha: float = 0.75,
        margin: int = 3,
    ) -> Tuple[int, int, int, int]:
        """Draw text with an alpha-blended background rectangle."""
        font = cv2.FONT_HERSHEY_SIMPLEX
        (tw, th), baseline = cv2.getTextSize(text, font, font_scale, thickness)

        x, y = org
        x1 = max(0, x - margin)
        y1 = max(0, y - th - margin)
        x2 = min(img.shape[1] - 1, x + tw + margin)
        y2 = min(img.shape[0] - 1, y + baseline + margin)

        if alpha >= 1.0:
            cv2.rectangle(img, (x1, y1), (x2, y2), bg_color, -1)
        else:
            sub = img[y1:y2, x1:x2]
            if sub.size > 0:
                rect = np.full_like(sub, bg_color, dtype=np.uint8)
                blended = cv2.addWeighted(sub, 1.0 - alpha, rect, alpha, 0)
                img[y1:y2, x1:x2] = blended

        cv2.putText(img, text, (x, y), font, font_scale, text_color, thickness, cv2.LINE_AA)
        return x1, y1, x2, y2

    def draw_geofence(
        self,
        img: np.ndarray,
        projected_pixels: List[Tuple[float, float]],
        visibility_flags: List[bool],
        polygon=None,
    ) -> None:
        """Draw geofence polygon with translucent yellow fill, double-stroke outline, vertex labels."""
        if len(projected_pixels) < 3:
            return

        pts = np.array(projected_pixels, dtype=np.int32).reshape((-1, 1, 2))

        # Translucent yellow fill
        overlay = img.copy()
        cv2.fillPoly(overlay, [pts], COLOR_LIGHT_YELLOW_FILL)
        cv2.addWeighted(overlay, 0.18, img, 0.82, 0, img)

        # Double-stroke outline (black outer, yellow inner)
        cv2.polylines(img, [pts], isClosed=True, color=COLOR_BLACK, thickness=2, lineType=cv2.LINE_AA)
        cv2.polylines(img, [pts], isClosed=True, color=COLOR_YELLOW_INNER, thickness=1, lineType=cv2.LINE_AA)

        # Vertex labels (F0, F1, ...)
        for i, (px, py) in enumerate(projected_pixels[:-1]):  # Skip closing vertex
            if 0 <= px < img.shape[1] and 0 <= py < img.shape[0]:
                cx, cy = int(px), int(py)
                cv2.circle(img, (cx, cy), 3, COLOR_BLACK, -1, lineType=cv2.LINE_AA)
                cv2.circle(img, (cx, cy), 1, COLOR_WHITE, -1, lineType=cv2.LINE_AA)
                lbl = f"F{i}"
                self.draw_text_with_bg(
                    img, lbl, (cx + 5, cy - 5),
                    font_scale=0.35, text_color=COLOR_YELLOW_INNER, alpha=0.75,
                )

        # Draw prominent Tactical Zone Header Badge on the polygon
        if polygon:
            zone_id = getattr(polygon, "zone_id", "RESTRICTED ZONE")
            zone_name = getattr(polygon, "name", "")
            label_text = f"RESTRICTED ZONE: {zone_id}"
            if zone_name and zone_name != "Restricted Geographic Zone":
                label_text += f" ({zone_name})"

            visible_pts = [
                (px, py) for px, py in projected_pixels[:-1]
                if 0 <= px < img.shape[1] and 0 <= py < img.shape[0]
            ]
            if visible_pts:
                # Place near the topmost visible vertex
                anchor_x, anchor_y = min(visible_pts, key=lambda pt: pt[1])
                badge_x = int(max(15, min(img.shape[1] - 350, anchor_x - 40)))
                badge_y = int(max(30, anchor_y - 12))
                self.draw_text_with_bg(
                    img, label_text, (badge_x, badge_y),
                    font_scale=0.42, thickness=1,
                    text_color=COLOR_BLACK, bg_color=COLOR_YELLOW_INNER,
                    alpha=0.90, margin=4,
                )

    def draw_objects(
        self,
        img: np.ndarray,
        objects_data: List[dict],
    ) -> None:
        """Draw bounding boxes with class-coded colors, switching to red on intrusion."""
        for obj in objects_data:
            bbox = obj.get("bbox", {})
            if isinstance(bbox, dict):
                x1 = int(bbox.get("x1", 0))
                y1 = int(bbox.get("y1", 0))
                x2 = int(bbox.get("x2", 0))
                y2 = int(bbox.get("y2", 0))
            elif isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                x1, y1, x2, y2 = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
            else:
                continue

            state_str = str(obj.get("intrusion_state", "OUTSIDE"))
            tid = obj.get("track_id", -1)
            cls_name = str(obj.get("class", obj.get("class_name", "VEHICLE"))).upper()
            if cls_name in ("PEDESTRIAN", "PEDESTRIANS", "PERSON"):
                cls_name = "PEOPLE"
            conf = float(obj.get("confidence", 0.0))

            is_intrusion = state_str in _INTRUSION_STATES
            inner_color = COLOR_RED_INNER if is_intrusion else CLASS_COLORS.get(cls_name, (0, 255, 0))

            # Double-stroke bounding box
            cv2.rectangle(img, (x1, y1), (x2, y2), COLOR_BLACK, 2, lineType=cv2.LINE_AA)
            cv2.rectangle(img, (x1, y1), (x2, y2), inner_color, 1, lineType=cv2.LINE_AA)

            # Ground contact marker
            gc = obj.get("ground_contact", [0, 0])
            if isinstance(gc, dict):
                gx, gy = int(gc.get("pixel_x", 0)), int(gc.get("pixel_y", 0))
            elif isinstance(gc, (list, tuple)) and len(gc) >= 2:
                gx, gy = int(gc[0]), int(gc[1])
            else:
                gx, gy = (x1 + x2) // 2, y2
            cv2.circle(img, (gx, gy), 2, COLOR_BLACK, -1, lineType=cv2.LINE_AA)
            cv2.circle(img, (gx, gy), 1, inner_color, -1, lineType=cv2.LINE_AA)

            # Label
            conf_pct = int(round(conf * 100)) if conf <= 1.0 else int(conf)
            label_text = f"#{tid} {cls_name} {conf_pct}%"
            lbl_y = max(y1 - 4, 14)
            lbl_x = max(x1, 3)
            bg_col = (0, 0, 140) if is_intrusion else tuple(int(c * 0.35) for c in inner_color)

            self.draw_text_with_bg(
                img, label_text, (lbl_x, lbl_y),
                font_scale=0.35, thickness=1,
                text_color=COLOR_WHITE, bg_color=bg_col,
                alpha=0.75, margin=2,
            )

    def draw_hud(
        self,
        img: np.ndarray,
        telemetry_dict: Optional[dict] = None,
        polygon=None,
        active_intrusions_count: int = 0,
    ) -> None:
        """Draw heads-up display with telemetry, geofence info, and intrusion alerts."""
        if not self.show_telemetry_hud and not self.show_fence_coords:
            return

        lines: List[str] = []

        if telemetry_dict and self.show_telemetry_hud:
            frame_idx = telemetry_dict.get("frame_idx", 0)
            ts_s = telemetry_dict.get("frame_timestamp_s", 0.0)
            lat = telemetry_dict.get("latitude", 0.0)
            lon = telemetry_dict.get("longitude", 0.0)
            alt = telemetry_dict.get("rel_alt_m", 0.0)
            yaw = telemetry_dict.get("gimbal_yaw_deg", 0.0)
            pitch = telemetry_dict.get("gimbal_pitch_deg", 0.0)
            roll = telemetry_dict.get("gimbal_roll_deg", 0.0)

            lines.append(f"FRAME     : #{frame_idx}")
            lines.append(f"DRONE GPS : {lat:.6f}, {lon:.6f} | ALT: {alt:.1f}m")
            lines.append(f"GIMBAL    : Y:{yaw:.1f}° P:{pitch:.1f}° R:{roll:.1f}°")

        if polygon and self.show_fence_coords:
            zone_id = getattr(polygon, "zone_id", "ZONE")
            coords = getattr(polygon, "coordinates", ())
            num_vertices = len(coords) - 1 if coords else 0
            lines.append(f"GEOFENCE  : {zone_id} ({num_vertices} Vertices)")
            for i, (lon, lat) in enumerate(coords[:-1][:4]):
                lines.append(f"  F{i}: {lat:.6f}, {lon:.6f}")
        elif self.show_fence_coords:
            lines.append("GEOFENCE  : STANDBY - NO GEOFENCE ARMED")

        if active_intrusions_count > 0:
            lines.append(f"ALERT     : {active_intrusions_count} ACTIVE INTRUSION(S) IN ZONE!")

        if not lines:
            return

        y_offset = 25
        for line in lines:
            is_alert = "ALERT" in line
            is_standby = "STANDBY" in line
            self.draw_text_with_bg(
                img, line, (12, y_offset),
                font_scale=0.40, thickness=1,
                text_color=COLOR_WHITE if not is_alert else COLOR_YELLOW_INNER,
                bg_color=(120, 0, 0) if is_alert else ((15, 60, 90) if is_standby else (15, 15, 15)),
                alpha=0.80,
            )
            y_offset += 18

    def get_overlay_json(
        self,
        objects_data: List[dict],
        geofence_pixels: List[Tuple[float, float]],
        visibility_flags: List[bool],
        telemetry_dict: Optional[dict] = None,
        active_intrusions: int = 0,
    ) -> dict:
        """Return overlay data as JSON-serializable dict for client-side canvas rendering."""
        return {
            "geofence": {
                "pixel_coords": [[float(x), float(y)] for x, y in geofence_pixels],
                "visibility_flags": visibility_flags,
                "vertex_count": max(0, len(geofence_pixels) - 1),
            },
            "objects": [
                {
                    "track_id": o.get("track_id", -1),
                    "class": o.get("class", "UNKNOWN"),
                    "confidence": o.get("confidence", 0.0),
                    "bbox": o.get("bbox", {}),
                    "intrusion_state": o.get("intrusion_state", "OUTSIDE"),
                    "is_intruding": o.get("is_intruding", False),
                    "ground_contact": o.get("ground_contact", [0, 0]),
                }
                for o in objects_data
            ],
            "telemetry": telemetry_dict or {},
            "active_intrusions": active_intrusions,
        }
