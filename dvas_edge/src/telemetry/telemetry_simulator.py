import math
import random

class TelemetrySimulator:
    def __init__(self):
        self.waypoints = [
            (8.5569, 76.8818), # Technopark
            (8.4913, 76.8948), # Shanghumugham Beach
            (8.3988, 76.9788), # Kovalam
            (8.5074, 76.9518)  # Museum
        ]
        self.reset()
    
    def reset(self):
        self.current_wp_idx = 0
        self.progress = 0.0
        self.battery_pct = 100.0
        self.signal_pct = 90.0
        self.altitude_m = 120.0
        
        self.lat = self.waypoints[0][0]
        self.lon = self.waypoints[0][1]
        self.speed_mps = 15.0
        self.heading_deg = 0.0
    
    def update(self, frame_index: int, utc_ms: int) -> dict:
        # Interpolate GPS
        wp1 = self.waypoints[self.current_wp_idx]
        wp2 = self.waypoints[(self.current_wp_idx + 1) % len(self.waypoints)]
        
        # distance approx
        lat_diff = wp2[0] - wp1[0]
        lon_diff = wp2[1] - wp1[1]
        dist = math.sqrt(lat_diff**2 + lon_diff**2)
        
        if dist > 0:
            step = 0.00005 # rough degree step
            self.progress += step / dist
            if self.progress >= 1.0:
                self.progress = 0.0
                self.current_wp_idx = (self.current_wp_idx + 1) % len(self.waypoints)
                wp1 = self.waypoints[self.current_wp_idx]
                wp2 = self.waypoints[(self.current_wp_idx + 1) % len(self.waypoints)]
                lat_diff = wp2[0] - wp1[0]
                lon_diff = wp2[1] - wp1[1]
                dist = math.sqrt(lat_diff**2 + lon_diff**2)
                
            self.lat = wp1[0] + lat_diff * self.progress
            self.lon = wp1[1] + lon_diff * self.progress
            
            # Heading
            self.heading_deg = math.degrees(math.atan2(lon_diff, lat_diff))
            if self.heading_deg < 0:
                self.heading_deg += 360
                
            # Speed based on step distance (very rough, but fits requirements 5-25 m/s)
            self.speed_mps = random.uniform(10.0, 20.0)
        
        # Altitude
        self.altitude_m = 120.0 + 40.0 * math.sin(frame_index * 2 * math.pi / 200.0)
        
        # Battery
        self.battery_pct -= 0.003
        if self.battery_pct < 0:
            self.battery_pct = 0
            
        # Signal
        self.signal_pct += random.uniform(-1, 1)
        self.signal_pct = max(75.0, min(99.0, self.signal_pct))
        
        return self.get_current()
        
    def get_current(self) -> dict:
        return {
            "latitude": self.lat,
            "longitude": self.lon,
            "altitude_m": self.altitude_m,
            "speed_mps": self.speed_mps,
            "heading_deg": self.heading_deg,
            "battery_pct": self.battery_pct,
            "signal_pct": self.signal_pct,
            "gps_lock": True
        }
