import { useMemo } from "react";
import { MapContainer, TileLayer, Marker, Popup, Tooltip } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useTheme } from "@/context/ThemeContext";

const STATUS_COLOR = { live: "#10b981", degraded: "#f59e0b", offline: "#ef4444" };

const createDroneIcon = (status) => {
  const color = STATUS_COLOR[status] || "#0ea5e9";
  // A clean, drone-like SVG (Navigation pointer) rotated 45 degrees
  const svg = `<div style="width: 28px; height: 28px; display: flex; align-items: center; justify-content: center;">
    <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="${color}" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="transform: rotate(45deg); filter: drop-shadow(0 2px 4px rgba(0,0,0,0.5));">
      <polygon points="3 11 22 2 13 21 11 13 3 11"/>
    </svg>
  </div>`;
  
  return L.divIcon({
    html: svg,
    className: `dvas-drone-marker ${status === "live" ? "dvas-drone-live-marker" : ""}`,
    iconSize: [28, 28],
    iconAnchor: [14, 14],
    popupAnchor: [0, -14],
    tooltipAnchor: [0, -14]
  });
};

export default function LiveMap({ drones, height = 480 }) {
  const { theme } = useTheme();
  const center = useMemo(() => {
    if (drones && drones.length) {
      const lat = drones.reduce((s, d) => s + (d.lat || 10), 0) / drones.length;
      const lng = drones.reduce((s, d) => s + (d.lng || 76), 0) / drones.length;
      return [lat, lng];
    }
    return [10.5, 76.2]; // Kerala centre
  }, [drones?.length]);

  const tileUrl = "https://{s}.google.com/vt/lyrs=m&x={x}&y={y}&z={z}";
  const mapBg = "var(--dvas-surface)";

  return (
    <div style={{ height }} data-testid="live-map" className="relative">
      <MapContainer key={theme} center={center} zoom={7} scrollWheelZoom={false} style={{ height: "100%", width: "100%", background: mapBg }}>
        <TileLayer
          attribution='&copy; Google Maps'
          url={tileUrl}
          subdomains={['mt0', 'mt1', 'mt2', 'mt3']}
          maxZoom={19}
        />
        {(drones || []).map((d) => (
          <Marker
            key={d.id}
            position={[d.lat, d.lng]}
            icon={createDroneIcon(d.status)}
          >
            <Tooltip direction="top" offset={[0, 0]} className="mono" opacity={0.95}>
              {d.id} · {d.status}
            </Tooltip>
            <Popup>
              <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11, minWidth: 180 }}>
                <div style={{ fontWeight: 600 }}>{d.id}</div>
                <div>Mission: {d.mission_id}</div>
                <div>Jurisdiction: {d.jurisdiction}</div>
                <div>Status: <b style={{ color: STATUS_COLOR[d.status] }}>{d.status}</b></div>
                <div>Battery: {d.battery_pct}%</div>
                <div>Lat/Lng: {d.lat?.toFixed(4)}, {d.lng?.toFixed(4)}</div>
              </div>
            </Popup>
          </Marker>
        ))}
      </MapContainer>
      <div className="absolute bottom-3 right-3 z-[400] mono text-[10px] px-2 py-1 flex items-center gap-3 pointer-events-none rounded" style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)", color: "var(--dvas-text)" }}>
        <span className="flex items-center gap-1">
           <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="#10b981" stroke="white" stroke-width="2" style={{transform: "rotate(45deg)"}}><polygon points="3 11 22 2 13 21 11 13 3 11"/></svg>
           live
        </span>
        <span className="flex items-center gap-1">
           <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="#f59e0b" stroke="white" stroke-width="2" style={{transform: "rotate(45deg)"}}><polygon points="3 11 22 2 13 21 11 13 3 11"/></svg>
           degraded
        </span>
        <span className="flex items-center gap-1">
           <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="#ef4444" stroke="white" stroke-width="2" style={{transform: "rotate(45deg)"}}><polygon points="3 11 22 2 13 21 11 13 3 11"/></svg>
           offline
        </span>
      </div>
    </div>
  );
}
