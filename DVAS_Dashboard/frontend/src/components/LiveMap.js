import { useMemo, useEffect, useRef } from "react";
import { MapContainer, TileLayer, Marker, Popup, Tooltip, Polyline, CircleMarker, useMap } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { Radio, Navigation } from "lucide-react";

// Fix Leaflet marker asset paths if loaded inside React
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: require("leaflet/dist/images/marker-icon-2x.png"),
  iconUrl: require("leaflet/dist/images/marker-icon.png"),
  shadowUrl: require("leaflet/dist/images/marker-shadow.png"),
});

const STATUS_COLOR = {
  live: "#10b981",     // Emerald
  degraded: "#f59e0b", // Amber
  offline: "#ef4444",  // Red
};

// Create a directional tactical drone chevron icon oriented by live gimbal yaw / heading
const createDroneIcon = (status, yaw = 0) => {
  const color = STATUS_COLOR[status] || "#10b981";
  const safeYaw = typeof yaw === "number" && !isNaN(yaw) ? yaw : 0;

  const svg = `<div style="width: 36px; height: 36px; display: flex; align-items: center; justify-content: center; transform: rotate(${safeYaw}deg); transition: transform 0.4s ease-out;">
    <svg xmlns="http://www.w3.org/2000/svg" width="30" height="30" viewBox="0 0 24 24" fill="${color}" stroke="#090d16" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="filter: drop-shadow(0 3px 6px rgba(0,0,0,0.65));">
      <polygon points="12 2 19 21 12 17 5 21 12 2"/>
    </svg>
  </div>`;

  return L.divIcon({
    html: svg,
    className: `dvas-drone-marker dvas-drone-${status}`,
    iconSize: [36, 36],
    iconAnchor: [18, 18],
    popupAnchor: [0, -18],
    tooltipAnchor: [0, -18],
  });
};

// Auto-recenter / pan the map when active drones move or when selected
function MapController({ center, selectedCoords }) {
  const map = useMap();
  const lastTargetRef = useRef(null);

  useEffect(() => {
    if (selectedCoords && selectedCoords[0] && selectedCoords[1]) {
      map.setView(selectedCoords, Math.max(map.getZoom(), 14), { animate: true });
      lastTargetRef.current = selectedCoords;
    } else if (center && (center[0] !== 10.5 || center[1] !== 76.2)) {
      map.panTo(center, { animate: true, duration: 0.8 });
    }
  }, [center, selectedCoords, map]);

  return null;
}

export default function LiveMap({ drones, height = 480, selectedDroneId = null }) {
  // Flight history breadcrumbs stored across renders
  const trailsRef = useRef({});

  // STRICT POLICY: Only active live mission drones render on the situational map
  const liveMissionDrones = useMemo(() => {
    return (drones || []).filter(
      (d) =>
        d &&
        ["live", "draining"].includes(d.status) &&
        Boolean(d.mission_id) &&
        typeof d.lat === "number" &&
        typeof d.lng === "number" &&
        !isNaN(d.lat) &&
        !isNaN(d.lng) &&
        (d.lat !== 0 || d.lng !== 0)
    );
  }, [drones]);

  // Update flight trail history for active drones
  useEffect(() => {
    liveMissionDrones.forEach((d) => {
      if (!trailsRef.current[d.id]) {
        trailsRef.current[d.id] = [];
      }
      const trail = trailsRef.current[d.id];
      const lastPoint = trail[trail.length - 1];
      if (!lastPoint || lastPoint[0] !== d.lat || lastPoint[1] !== d.lng) {
        trail.push([d.lat, d.lng]);
        if (trail.length > 50) trail.shift(); // Keep last 50 coordinates
      }
    });
  }, [liveMissionDrones]);

  // Calculate center of all active mission drones, or fallback to Kerala center
  const center = useMemo(() => {
    if (liveMissionDrones.length > 0) {
      const lat = liveMissionDrones.reduce((s, d) => s + d.lat, 0) / liveMissionDrones.length;
      const lng = liveMissionDrones.reduce((s, d) => s + d.lng, 0) / liveMissionDrones.length;
      return [lat, lng];
    }
    return [10.5, 76.2]; // Kerala geographical center
  }, [liveMissionDrones]);

  // Coordinates of selected drone, if any
  const selectedCoords = useMemo(() => {
    if (!selectedDroneId) return null;
    const found = liveMissionDrones.find((d) => d.id === selectedDroneId);
    return found ? [found.lat, found.lng] : null;
  }, [selectedDroneId, liveMissionDrones]);

  // Standard OpenStreetMap Tile Layer
  const osmTileUrl = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
  const osmAttribution = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

  return (
    <div style={{ height }} data-testid="live-map" className="relative w-full overflow-hidden rounded-xl bg-slate-950">
      <MapContainer
        center={center}
        zoom={liveMissionDrones.length > 0 ? 13 : 7}
        scrollWheelZoom={true}
        style={{ height: "100%", width: "100%" }}
      >
        <TileLayer
          attribution={osmAttribution}
          url={osmTileUrl}
          maxZoom={19}
        />

        <MapController center={center} selectedCoords={selectedCoords} />

        {/* Flight path breadcrumbs */}
        {liveMissionDrones.map((d) => {
          const trail = trailsRef.current[d.id] || [];
          if (trail.length < 2) return null;
          return (
            <Polyline
              key={`trail-${d.id}`}
              positions={trail}
              pathOptions={{
                color: "#06b6d4", // Cyan
                weight: 3,
                opacity: 0.75,
                dashArray: "6, 6",
              }}
            />
          );
        })}

        {/* Live Drone Markers & GPS Pulse Halos */}
        {liveMissionDrones.map((d) => {
          const yaw = d.gb_yaw != null ? d.gb_yaw : (d.heading_deg || 0);

          return (
            <div key={d.id}>
              {/* GPS Position Halo Indicator */}
              <CircleMarker
                center={[d.lat, d.lng]}
                radius={20}
                pathOptions={{
                  color: d.gps_lock === false ? "#f59e0b" : "#10b981",
                  fillColor: d.gps_lock === false ? "#f59e0b" : "#10b981",
                  fillOpacity: 0.18,
                  weight: 1.5,
                }}
              />

              <Marker
                position={[d.lat, d.lng]}
                icon={createDroneIcon(d.status, yaw)}
              >
                <Tooltip direction="top" offset={[0, -10]} className="mono" opacity={0.95}>
                  <span className="font-semibold text-emerald-400">{d.id}</span> · {d.status} ({d.mission_id})
                </Tooltip>

                <Popup>
                  <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, minWidth: 220, lineHeight: 1.4 }}>
                    <div className="font-bold text-sm text-slate-900 border-b pb-1 mb-1 flex items-center justify-between">
                      <span>{d.id}</span>
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800 font-semibold uppercase">
                        {d.status}
                      </span>
                    </div>
                    <div className="text-slate-700"><b>Mission:</b> {d.mission_id || "—"}</div>
                    {d.mission_name && <div className="text-slate-600"><b>Name:</b> {d.mission_name}</div>}
                    <div className="text-slate-700"><b>Jurisdiction:</b> {d.jurisdiction || "—"}</div>
                    
                    <div className="my-1 border-t border-slate-200 pt-1 text-slate-800">
                      <div><b>Lat:</b> {d.lat.toFixed(6)}°</div>
                      <div><b>Lng:</b> {d.lng.toFixed(6)}°</div>
                    </div>

                    <div className="grid grid-cols-2 gap-1 text-[10px] bg-slate-100 p-1.5 rounded my-1">
                      <div><b>Rel Alt:</b> {d.altitude_m != null ? `${d.altitude_m.toFixed(1)}m` : "—"}</div>
                      <div><b>Abs Alt:</b> {d.abs_alt != null ? `${d.abs_alt.toFixed(1)}m` : "—"}</div>
                      <div><b>Gimbal Yaw:</b> {d.gb_yaw != null ? `${d.gb_yaw}°` : "—"}</div>
                      <div><b>Gimbal Pitch:</b> {d.gb_pitch != null ? `${d.gb_pitch}°` : "—"}</div>
                      <div><b>Speed:</b> {d.speed_mps != null ? `${d.speed_mps} m/s` : "—"}</div>
                      <div><b>Battery:</b> {d.battery_pct != null ? `${d.battery_pct}%` : "—"}</div>
                    </div>

                    <div className="text-[10px] text-emerald-700 font-semibold flex items-center gap-1 mt-1">
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-ping inline-block" />
                      {d.gps_lock !== false ? "GPS Lock: Acquired" : "GPS Lock: Searching"}
                    </div>
                  </div>
                </Popup>
              </Marker>
            </div>
          );
        })}
      </MapContainer>

      {/* Awaiting Active Mission Overlay when 0 active streams are streaming */}
      {liveMissionDrones.length === 0 && (
        <div className="absolute top-4 left-1/2 -translate-x-1/2 z-[400] px-4 py-2 rounded-xl flex items-center gap-3 backdrop-blur-md shadow-lg border border-slate-700/60 bg-slate-900/85 text-slate-300">
          <Radio size={16} className="text-cyan-400 animate-pulse" />
          <div className="flex flex-col">
            <span className="text-xs font-semibold text-slate-200 uppercase tracking-wider">Awaiting Active Mission Stream</span>
            <span className="text-[10px] text-slate-400">Map will lock onto drone coordinates when field unit begins transmission</span>
          </div>
        </div>
      )}

      {/* Tactical Status / Map Legend Overlay */}
      <div className="absolute bottom-3 right-3 z-[400] mono text-[10px] px-3 py-1.5 flex items-center gap-3 backdrop-blur-md rounded-lg shadow-md bg-slate-900/85 border border-slate-700/60 text-slate-300">
        <span className="flex items-center gap-1.5 font-semibold text-emerald-400">
          <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
          {liveMissionDrones.length} Live Mission{liveMissionDrones.length !== 1 ? "s" : ""}
        </span>
        <span className="text-slate-600">|</span>
        <span className="flex items-center gap-1 text-slate-400">
          <Navigation size={11} className="text-cyan-400 rotate-45" /> Live Yaw Orientation
        </span>
        <span className="text-slate-600">|</span>
        <span className="text-slate-400 font-medium">
          OpenStreetMap
        </span>
      </div>
    </div>
  );
}
