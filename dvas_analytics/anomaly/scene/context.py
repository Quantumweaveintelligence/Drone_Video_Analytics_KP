"""
Scene context analysis module for drone surveillance.
Evaluates spatial zones (road, sidewalk, pedestrian area, restricted zone)
and detects object/context mismatches (CONTEXTUAL_ANOMALY).
"""

from typing import Dict, List, Optional, Tuple
import numpy as np

from tracking.track_state import EventType, TrackState


class SceneZone:
    """Represents a spatial zone in the surveillance scene."""
    def __init__(
        self,
        name: str,
        zone_type: str,
        bbox: Optional[List[float]] = None,  # [x1, y1, x2, y2]
        polygon: Optional[List[Tuple[float, float]]] = None,
        is_restricted: bool = False,
    ):
        self.name = name
        self.zone_type = zone_type  # "road", "sidewalk", "pedestrian", "restricted", "isolated"
        self.bbox = bbox
        self.polygon = polygon
        self.is_restricted = is_restricted

    def contains(self, point: Tuple[float, float]) -> bool:
        x, y = point
        if self.bbox is not None:
            return self.bbox[0] <= x <= self.bbox[2] and self.bbox[1] <= y <= self.bbox[3]
        if self.polygon is not None and len(self.polygon) >= 3:
            # Ray-casting algorithm for point-in-polygon
            n = len(self.polygon)
            inside = False
            p1x, p1y = self.polygon[0]
            for i in range(1, n + 1):
                p2x, p2y = self.polygon[i % n]
                if y > min(p1y, p2y):
                    if y <= max(p1y, p2y):
                        if x <= max(p1x, p2x):
                            if p1y != p2y:
                                xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                            if p1x == p2x or x <= xinters:
                                inside = not inside
                p1x, p1y = p2x, p2y
            return inside
        return False


class SceneContextEngine:
    """
    Manages scene zones and assesses context-anomaly scores for objects, persons, and vehicles.
    """

    def __init__(self, zones: Optional[List[SceneZone]] = None):
        self.zones: List[SceneZone] = zones or []
        # Dynamic heatmap memory when zones are not predefined
        self.vehicle_occupancy_centroids: List[Tuple[float, float]] = []
        self.pedestrian_occupancy_centroids: List[Tuple[float, float]] = []

    def add_zone(self, zone: SceneZone):
        self.zones.append(zone)

    def evaluate_context(
        self,
        tracks: List[TrackState],
    ) -> Dict[int, Tuple[float, List[str]]]:
        """
        Evaluates context anomaly score [0, 1] and explanation reasons for all tracks.
        Returns map: track_id -> (anomaly_score, reasons).
        """
        results: Dict[int, Tuple[float, List[str]]] = {}

        # Accumulate dynamic spatial usage
        for t in tracks:
            pos = t.bottom_center
            if t.is_vehicle and t.speed > 10.0:
                self.vehicle_occupancy_centroids.append(pos)
                if len(self.vehicle_occupancy_centroids) > 500:
                    self.vehicle_occupancy_centroids.pop(0)
            elif t.is_person and t.speed > 5.0:
                self.pedestrian_occupancy_centroids.append(pos)
                if len(self.pedestrian_occupancy_centroids) > 500:
                    self.pedestrian_occupancy_centroids.pop(0)

        for t in tracks:
            pos = t.bottom_center
            score = 0.0
            reasons: List[str] = []
            zone_name = "general"

            # Check predefined zones
            matched_zone: Optional[SceneZone] = None
            for z in self.zones:
                if z.contains(pos):
                    matched_zone = z
                    zone_name = z.name
                    break

            t.context_zone = zone_name

            if matched_zone is not None:
                # Rule 1: Restricted Zone
                if matched_zone.is_restricted:
                    score += 0.50
                    reasons.append(f"Entity present inside restricted zone '{matched_zone.name}'")

                # Rule 2: Unattended Object in Vehicle Road
                if not t.is_person and not t.is_vehicle and matched_zone.zone_type == "road":
                    score += 0.60
                    reasons.append(f"Object located directly inside vehicular traffic lane '{matched_zone.name}'")
                    if EventType.UNUSUAL_OBJECT_PLACEMENT not in t.active_events:
                        t.active_events.append(EventType.UNUSUAL_OBJECT_PLACEMENT)

                # Rule 3: Vehicle in Pedestrian Zone
                if t.is_vehicle and matched_zone.zone_type == "pedestrian":
                    score += 0.70
                    reasons.append(f"Vehicle operating inside designated pedestrian area '{matched_zone.name}'")

            # Fallback dynamic evaluation: object placed in active vehicle lane
            elif not t.is_person and not t.is_vehicle and t.is_stationary:
                if self.vehicle_occupancy_centroids:
                    # Check distance to recent moving vehicle paths
                    dists = [np.hypot(pos[0] - vc[0], pos[1] - vc[1]) for vc in self.vehicle_occupancy_centroids[-80:]]
                    min_v_dist = min(dists) if dists else 999.0
                    if min_v_dist < 60.0:
                        score += 0.40
                        reasons.append(f"Stationary object located within active vehicular path corridor ({min_v_dist:.1f}px to traffic)")
                        if EventType.UNUSUAL_OBJECT_PLACEMENT not in t.active_events:
                            t.active_events.append(EventType.UNUSUAL_OBJECT_PLACEMENT)

            score = min(1.0, score)
            t.context_anomaly_score = score
            results[t.track_id] = (score, reasons)

        return results
