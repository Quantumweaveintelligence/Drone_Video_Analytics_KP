# -*- coding: utf-8 -*-
"""
geofence.py — WGS84 GeoJSON Polygon & Geofence Manager for DVAS Intrusion Detection.

Adapted from intrusion_detection/core/intrusion/geofence.py.
Loads geofence polygons from Redis (published by edge dashboard / ingest server).
"""

import json
import logging
from typing import Dict, Any, List, Optional, Tuple

from intrusion.projection import CameraProjection, enu_to_wgs84, wgs84_to_enu

logger = logging.getLogger(__name__)


class FrozenPolygonError(PermissionError):
    """Raised when attempting to mutate an immutable WGS84 polygon."""
    pass


class WGS84Polygon:
    """Represents a frozen, immutable WGS84 geographic polygon (GeoJSON format: [lon, lat])."""

    def __init__(
        self,
        coordinates: List[Tuple[float, float]],
        zone_id: str = "RESTRICTED_ZONE_01",
        name: str = "Restricted Geographic Zone",
        creation_mode: str = "INTERACTIVE",
        reference_metadata: Optional[dict] = None,
    ):
        if not coordinates or len(coordinates) < 3:
            raise ValueError("A polygon must have at least 3 vertices.")

        clean_coords: List[Tuple[float, float]] = []
        for pt in coordinates:
            lon, lat = float(pt[0]), float(pt[1])
            if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
                raise ValueError(f"Invalid WGS84 coordinates: lon={lon}, lat={lat}")
            clean_coords.append((lon, lat))

        # Ensure closure (last == first)
        if clean_coords[0] != clean_coords[-1]:
            clean_coords.append(clean_coords[0])

        self._coordinates: Tuple[Tuple[float, float], ...] = tuple(clean_coords)
        self.zone_id: str = zone_id
        self.name: str = name
        self.creation_mode: str = creation_mode
        self.reference_metadata: dict = reference_metadata or {}

    @property
    def coordinates(self) -> Tuple[Tuple[float, float], ...]:
        """Coordinates in (lon, lat) order per GeoJSON RFC 7946."""
        return self._coordinates

    def get_vertices_lon_lat(self) -> List[Tuple[float, float]]:
        return list(self._coordinates)

    def get_vertices_lat_lon(self) -> List[Tuple[float, float]]:
        return [(lat, lon) for lon, lat in self._coordinates]

    def to_geojson_dict(self) -> dict:
        return {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {
                    "zone_id": self.zone_id,
                    "name": self.name,
                    "creation_mode": self.creation_mode,
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[list(pt) for pt in self._coordinates]],
                },
            }],
        }

    @classmethod
    def from_geojson_dict(cls, data: dict) -> 'WGS84Polygon':
        """Parse GeoJSON FeatureCollection, Feature, or bare Polygon geometry."""
        # Handle FeatureCollection → take first feature
        if data.get("type") == "FeatureCollection":
            features = data.get("features", [])
            if not features:
                raise ValueError("Empty FeatureCollection")
            data = features[0]

        # Handle Feature → extract geometry + properties
        if data.get("type") == "Feature":
            props = data.get("properties", {})
            geometry = data.get("geometry", {})
        else:
            # Bare Geometry object
            props = {}
            geometry = data

        if geometry.get("type") != "Polygon":
            raise ValueError(f"Unsupported geometry type: {geometry.get('type')}")

        coords_raw = geometry.get("coordinates", [[]])[0]
        if not coords_raw:
            raise ValueError("Empty polygon coordinates")

        coords = [(float(pt[0]), float(pt[1])) for pt in coords_raw]
        zone_id = props.get("zone_id", "RESTRICTED_ZONE_01")
        name = props.get("name", "Restricted Geographic Zone")
        creation_mode = props.get("creation_mode", "INTERACTIVE")
        reference_metadata = props.get("reference_metadata", {})

        return cls(
            coordinates=coords,
            zone_id=zone_id,
            name=name,
            creation_mode=creation_mode,
            reference_metadata=reference_metadata,
        )


class GeofenceManager:
    """Manages loading geofences from Redis and projecting them onto camera frames."""

    def __init__(self):
        self.polygon: Optional[WGS84Polygon] = None
        self._raw_hash: Optional[str] = None  # Track changes

    def is_loaded(self) -> bool:
        return self.polygon is not None

    @property
    def zone_id(self) -> Optional[str]:
        return self.polygon.zone_id if self.polygon else None

    @property
    def zone_name(self) -> Optional[str]:
        return self.polygon.name if self.polygon else None

    @property
    def vertex_count(self) -> int:
        if self.polygon is None:
            return 0
        return len(self.polygon.coordinates) - 1  # Exclude closing vertex

    def load_from_geojson_dict(self, data: dict) -> None:
        """Load geofence from a GeoJSON dict (FeatureCollection or Feature)."""
        try:
            self.polygon = WGS84Polygon.from_geojson_dict(data)
            logger.info(
                "Loaded geofence '%s' (%d vertices)",
                self.polygon.zone_id, self.vertex_count,
            )
        except Exception as e:
            logger.error("Failed to parse geofence GeoJSON: %s", e)
            self.polygon = None

    async def load_from_redis(self, redis_client, drone_id: str) -> bool:
        """Load geofence from Redis key drone:geofence:{drone_id} (checking aliases).
        Returns True if geofence changed, False otherwise.
        """
        try:
            candidates = [drone_id]
            try:
                from shared.config import get_drone_aliases
                candidates = list(get_drone_aliases(drone_id) or [drone_id])
            except Exception:
                pass

            if drone_id.startswith("DRN-"):
                cand_mission = drone_id[4:]
                if cand_mission not in candidates:
                    candidates.append(cand_mission)
            elif not drone_id.startswith("DRONE_KL_"):
                cand_drn = f"DRN-{drone_id}"
                if cand_drn not in candidates:
                    candidates.append(cand_drn)

            raw = None
            for cand in candidates:
                raw = await redis_client.get(f"drone:geofence:{cand}")
                if raw:
                    break

            if not raw:
                # Do NOT clear an already-armed polygon if Redis key is missing or evicted.
                # A geofence remains active for the drone/mission until explicitly replaced or cleared.
                if self.polygon is not None:
                    return False

                # Fallback: check local disk cache if Redis was evicted
                try:
                    import os
                    from pathlib import Path
                    for cand in candidates:
                        cache_paths = [
                            Path(f"/tmp/geofence_cache_{cand}.json"),
                            Path(f"/data/geofences/{cand}_geofence.json"),
                            Path(f"data/geofences/{cand}_geofence.json"),
                        ]
                        for cp in cache_paths:
                            if cp.exists():
                                raw_str = cp.read_text(encoding="utf-8")
                                data = json.loads(raw_str)
                                self.load_from_geojson_dict(data)
                                logger.info("Restored geofence for %s from local cache %s", drone_id, cp)
                                return True
                except Exception:
                    pass

                return False

            # Only re-parse if data changed
            raw_str = raw if isinstance(raw, str) else raw.decode("utf-8")
            import hashlib
            new_hash = hashlib.md5(raw_str.encode()).hexdigest()
            if new_hash == self._raw_hash:
                return False

            data = json.loads(raw_str)
            self.load_from_geojson_dict(data)
            self._raw_hash = new_hash

            # Cache to local disk so eviction in Redis never loses the polygon
            try:
                from pathlib import Path
                Path(f"/tmp/geofence_cache_{drone_id}.json").write_text(raw_str, encoding="utf-8")
            except Exception:
                pass

            return True
        except Exception as e:
            logger.error("Failed to load geofence from Redis for %s: %s", drone_id, e)
            return False

    def clear(self) -> None:
        """Explicitly clear loaded geofence."""
        self.polygon = None
        self._raw_hash = None

    def project_to_frame(
        self,
        camera: CameraProjection,
        drone_lat: float,
        drone_lon: float,
        drone_alt: float,
        gimbal_yaw: float,
        gimbal_pitch: float,
        gimbal_roll: float = 0.0,
    ) -> Tuple[List[Tuple[float, float]], List[bool]]:
        """Project geofence polygon vertices to pixel coordinates in the current frame.

        Returns (pixel_points, visibility_flags).
        """
        if self.polygon is None:
            return [], []

        pixel_pts: List[Tuple[float, float]] = []
        vis_flags: List[bool] = []

        for lon, lat in self.polygon.coordinates:
            px, py, vis = camera.ground_to_pixel(
                lat=lat,
                lon=lon,
                drone_lat=drone_lat,
                drone_lon=drone_lon,
                drone_rel_alt=drone_alt,
                gimbal_yaw=gimbal_yaw,
                gimbal_pitch=gimbal_pitch,
                gimbal_roll=gimbal_roll,
            )
            pixel_pts.append((px, py))
            vis_flags.append(vis)

        return pixel_pts, vis_flags
