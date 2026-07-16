import { useMemo } from "react";
import { MapContainer, TileLayer, CircleMarker, Popup, Tooltip } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import { useTheme } from "@/context/ThemeContext";

const STATUS_COLOR = { live: "#10b981", degraded: "#f59e0b", offline: "#ef4444" };

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

  const tileUrl = theme === "light"
    ? "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
    : "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png";
  const mapBg = theme === "light" ? "#eef2f7" : "#020617";

  return (
    <div style={{ height }} data-testid="live-map" className="relative">
      <MapContainer key={theme} center={center} zoom={7} scrollWheelZoom={false} style={{ height: "100%", width: "100%", background: mapBg }}>
        <TileLayer
          attribution='&copy; OpenStreetMap &copy; CARTO'
          url={tileUrl}
          subdomains="abcd"
          maxZoom={19}
        />
        {(drones || []).map((d) => (
          <CircleMarker
            key={d.id}
            center={[d.lat, d.lng]}
            radius={8}
            pathOptions={{
              color: STATUS_COLOR[d.status] || "#0ea5e9",
              fillColor: STATUS_COLOR[d.status] || "#0ea5e9",
              fillOpacity: 0.7,
              weight: 2,
            }}
          >
            <Tooltip direction="top" offset={[0, -8]} className="mono" opacity={0.95}>
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
          </CircleMarker>
        ))}
      </MapContainer>
      <div className="absolute bottom-3 right-3 z-[400] mono text-[10px] text-slate-400 bg-black/70 border border-slate-700 px-2 py-1 flex items-center gap-3 pointer-events-none">
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full inline-block" style={{ background: "#10b981" }} /> live</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full inline-block" style={{ background: "#f59e0b" }} /> degraded</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full inline-block" style={{ background: "#ef4444" }} /> offline</span>
      </div>
    </div>
  );
}
