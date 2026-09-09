# -*- coding: utf-8 -*-
"""projection.py - Geospatial and 3D Camera Ray-Plane Projection Mathematics

Handles coordinate transforms (WGS84 <-> ENU) and camera ray-ground intersection
for projecting pixel bounding box ground-contact points to WGS84 coordinates,
and projecting WGS84 geofence vertices back to per-frame pixel coordinates.
"""

import math
from typing import List, Optional, Tuple
import numpy as np

# WGS84 Ellipsoid constants
WGS84_A = 6378137.0            # Equatorial radius in meters
WGS84_E2 = 0.00669437999014    # Square of eccentricity


def wgs84_to_enu(
    lat: float, lon: float, lat0: float, lon0: float,
    alt: float = 0.0, alt0: float = 0.0,
) -> Tuple[float, float, float]:
    """Convert WGS84 (Lat, Lon, Alt) to Local ENU (East, North, Up) in meters."""
    phi0 = math.radians(lat0)
    dphi = math.radians(lat - lat0)
    dlam = math.radians(lon - lon0)

    sin_phi0 = math.sin(phi0)
    rm = WGS84_A * (1.0 - WGS84_E2) / math.pow(1.0 - WGS84_E2 * sin_phi0 * sin_phi0, 1.5)
    rn = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_phi0 * sin_phi0)

    north = dphi * rm
    east = dlam * rn * math.cos(phi0)
    up = alt - alt0

    return east, north, up


def enu_to_wgs84(
    east: float, north: float, lat0: float, lon0: float,
    up: float = 0.0, alt0: float = 0.0,
) -> Tuple[float, float, float]:
    """Convert Local ENU (East, North, Up) in meters back to WGS84 (Lat, Lon, Alt)."""
    phi0 = math.radians(lat0)
    sin_phi0 = math.sin(phi0)

    rm = WGS84_A * (1.0 - WGS84_E2) / math.pow(1.0 - WGS84_E2 * sin_phi0 * sin_phi0, 1.5)
    rn = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_phi0 * sin_phi0)

    dphi = north / rm
    dlam = east / (rn * math.cos(phi0))

    lat = lat0 + math.degrees(dphi)
    lon = lon0 + math.degrees(dlam)
    alt = alt0 + up

    return lat, lon, alt


class CameraProjection:
    """Handles 3D camera ray geometry, pixel-to-ground projection, and ground-to-pixel projection."""

    def __init__(
        self,
        img_width: int,
        img_height: int,
        focal_len_mm: float = 24.0,
        sensor_width_mm: float = 36.0,
        hfov_deg: Optional[float] = None,
    ):
        self.W = img_width
        self.H = img_height
        self.cx = self.W / 2.0
        self.cy = self.H / 2.0
        self.focal_len_mm = focal_len_mm
        self.sensor_width_mm = sensor_width_mm

        if hfov_deg is not None and hfov_deg > 0:
            self.fx = (self.W / 2.0) / math.tan(math.radians(hfov_deg / 2.0))
        else:
            self.fx = (focal_len_mm / sensor_width_mm) * self.W

        self.fy = self.fx

    def update_resolution(self, img_width: int, img_height: int):
        """Update camera resolution to match the current decoded frame size."""
        if img_width > 0 and img_height > 0 and (img_width != self.W or img_height != self.H):
            self.W = img_width
            self.H = img_height
            self.cx = self.W / 2.0
            self.cy = self.H / 2.0
            self.fx = (self.focal_len_mm / self.sensor_width_mm) * self.W
            self.fy = self.fx

    def update_focal_length(self, focal_len_mm: float, sensor_width_mm: float = 36.0):
        """Update camera intrinsics from DJI SRT metadata focal length."""
        self.focal_len_mm = focal_len_mm
        self.sensor_width_mm = sensor_width_mm
        self.fx = (focal_len_mm / sensor_width_mm) * self.W
        self.fy = self.fx

    def get_rotation_matrix(self, yaw_deg: float, pitch_deg: float, roll_deg: float) -> np.ndarray:
        yaw = math.radians(yaw_deg)
        pitch = math.radians(pitch_deg)
        roll = math.radians(roll_deg)

        R_nadir = np.array([
            [1.0,  0.0,  0.0],
            [0.0, -1.0,  0.0],
            [0.0,  0.0, -1.0]
        ])

        delta_pitch = pitch + math.pi / 2.0
        R_pitch = np.array([
            [1.0, 0.0, 0.0],
            [0.0, math.cos(delta_pitch), -math.sin(delta_pitch)],
            [0.0, math.sin(delta_pitch), math.cos(delta_pitch)]
        ])

        R_roll = np.array([
            [math.cos(roll), -math.sin(roll), 0.0],
            [math.sin(roll), math.cos(roll), 0.0],
            [0.0, 0.0, 1.0]
        ])

        psi = yaw
        R_yaw = np.array([
            [ math.cos(psi), math.sin(psi), 0.0],
            [-math.sin(psi), math.cos(psi), 0.0],
            [ 0.0,           0.0,           1.0]
        ])

        return R_yaw @ R_nadir @ R_pitch @ R_roll

    def pixel_to_ground(
        self,
        px: float, py: float,
        drone_lat: float, drone_lon: float, drone_rel_alt: float,
        gimbal_yaw: float, gimbal_pitch: float, gimbal_roll: float = 0.0,
        ground_alt: float = 0.0, digital_zoom: float = 1.0,
    ) -> Tuple[float, float, float, float]:
        """Project a pixel coordinate to ground WGS84 via 3D ray-plane intersection.
        Returns (lat, lon, east_m, north_m)."""
        if drone_rel_alt <= 0.1:
            drone_rel_alt = 1.0

        effective_fx = self.fx * max(0.1, digital_zoom)
        effective_fy = self.fy * max(0.1, digital_zoom)

        x_cam = (px - self.cx) / effective_fx
        y_cam = (py - self.cy) / effective_fy
        v_cam = np.array([x_cam, y_cam, 1.0])
        v_cam = v_cam / np.linalg.norm(v_cam)

        R = self.get_rotation_matrix(gimbal_yaw, gimbal_pitch, gimbal_roll)
        d_world = R @ v_cam

        dz = d_world[2]
        if dz >= -1e-4:
            dz = -1e-4

        camera_height = drone_rel_alt - ground_alt
        t = -camera_height / dz

        east = t * d_world[0]
        north = t * d_world[1]

        lat, lon, _ = enu_to_wgs84(east, north, drone_lat, drone_lon, 0.0, ground_alt)
        return lat, lon, east, north

    def ground_to_pixel(
        self,
        lat: float, lon: float,
        drone_lat: float, drone_lon: float, drone_rel_alt: float,
        gimbal_yaw: float, gimbal_pitch: float, gimbal_roll: float = 0.0,
        ground_alt: float = 0.0, digital_zoom: float = 1.0,
    ) -> Tuple[float, float, bool]:
        """Project a WGS84 ground coordinate to pixel coordinates.
        Returns (px, py, is_visible)."""
        east, north, _ = wgs84_to_enu(lat, lon, drone_lat, drone_lon, alt=ground_alt, alt0=0.0)
        camera_pos_enu = np.array([0.0, 0.0, drone_rel_alt])
        p_world_enu = np.array([east, north, ground_alt])

        delta_world = p_world_enu - camera_pos_enu
        R = self.get_rotation_matrix(gimbal_yaw, gimbal_pitch, gimbal_roll)
        v_cam = R.T @ delta_world

        z_cam = v_cam[2]
        if z_cam <= 1e-3:
            return -999.0, -999.0, False

        effective_fx = self.fx * max(0.1, digital_zoom)
        effective_fy = self.fy * max(0.1, digital_zoom)

        px = self.cx + effective_fx * (v_cam[0] / z_cam)
        py = self.cy + effective_fy * (v_cam[1] / z_cam)

        is_visible = (0 <= px < self.W) and (0 <= py < self.H)
        return float(px), float(py), is_visible
