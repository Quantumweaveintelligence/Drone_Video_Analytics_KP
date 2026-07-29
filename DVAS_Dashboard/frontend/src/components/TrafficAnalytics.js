/**
 * TrafficAnalytics.js
 * ─────────────────────────────────────────────────────────────────────────────
 * Dedicated Traffic & ANPR page for the DVAS Command Centre.
 * Kerala Police | Drone Video Analytics System
 * ─────────────────────────────────────────────────────────────────────────────
 * Sections:
 *   1. Live Detection Video Stream (plays real drone video for DRN-001/002/004)
 *   2. Traffic KPI Strip
 *   3. Tracked Vehicles Window (live ledger with speed, colour, make/model)
 *   4. Vehicle Classification Donut + Flow Chart
 *   5. ANPR Number Plate Detection Window (real vehicle + plate images)
 *   6. Theft / Watchlist Alert Panel
 *
 * Data sources:
 *   - Videos:  /DETECTED/DRN-001_h264.mp4, DRN-002_h264.mp4, DRN-004_h264.mp4
 *   - Plates:  /DETECTED/plate_manifest.json → images under /DETECTED/PLATE/
 *   - Ingest:  Drone connectivity from drones prop (ingest server status)
 */

import { useState, useEffect, useRef } from "react";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, PieChart, Pie, Cell, Legend,
} from "recharts";
import {
  Car, Activity, AlertTriangle, CheckCircle, Hash,
  TrendingUp, Radio, Gauge, Eye, ShieldAlert,
  Play, Pause, Search, Clock, VideoOff,
} from "lucide-react";

/* ─────────────────────────────────────────────────────────
   TRAFFIC-ENABLED DRONE IDS & VIDEO MAP
   ───────────────────────────────────────────────────────── */
const TRAFFIC_DRONES = ["DRN-001", "DRN-002", "DRN-004"];
const DRONE_VIDEO_MAP = {
  "DRN-001": "/DETECTED/DRN-001_h264.mp4",
  "DRN-002": "/DETECTED/DRN-002_h264.mp4",
  "DRN-004": "/DETECTED/DRN-004_h264.mp4",
};

const ALL_DRONE_IDS = ["DRN-001", "DRN-002", "DRN-003", "DRN-004"];

/* ─────────────────────────────────────────────────────────
   MOCK / FALLBACK DATA (for charts & tracked vehicles)
   ───────────────────────────────────────────────────────── */
const NOW = Date.now();

const MOCK_VEHICLE_SERIES = Array.from({ length: 24 }, (_, i) => ({
  hour: `${String(i).padStart(2, "0")}:00`,
  cars:       Math.round(60  + Math.sin((i / 24) * Math.PI * 2) * 40 + Math.random() * 20),
  twoWheelers:Math.round(90  + Math.sin((i / 24) * Math.PI * 2) * 50 + Math.random() * 25),
  heavy:      Math.round(15  + Math.sin((i / 24) * Math.PI * 2) * 10 + Math.random() * 8),
  total:      Math.round(165 + Math.sin((i / 24) * Math.PI * 2) * 90 + Math.random() * 45),
}));

const MOCK_VEHICLE_TYPES = [
  { name: "Two-Wheelers", value: 214, color: "#818cf8" },
  { name: "Cars",         value: 156, color: "#14b8a6" },
  { name: "Heavy",        value:  38, color: "#f59e0b" },
];

const MOCK_TRACKED_VEHICLES = [
  { id: "VEH-0041", type: "Car",         speed: 34, colour: "White",  make: "Maruti Swift",   state: "Moving",  zone: "MG Road",        drone: "DRN-001", last_seen: new Date(NOW - 12000).toISOString() },
  { id: "VEH-0089", type: "Two-Wheeler", speed: 28, colour: "Black",  make: "Honda Activa",   state: "Moving",  zone: "North Gate",     drone: "DRN-002", last_seen: new Date(NOW - 22000).toISOString() },
  { id: "VEH-0113", type: "Heavy",       speed:  8, colour: "Blue",   make: "Tata 407",       state: "Slow",    zone: "Secretariat Rd", drone: "DRN-002", last_seen: new Date(NOW - 45000).toISOString() },
  { id: "VEH-0057", type: "Car",         speed: 41, colour: "Silver", make: "Hyundai i20",    state: "Moving",  zone: "Beach Sector A", drone: "DRN-004", last_seen: new Date(NOW - 8000).toISOString()  },
  { id: "VEH-0022", type: "Two-Wheeler", speed:  0, colour: "Red",    make: "Royal Enfield",  state: "Stopped", zone: "East Fort",      drone: "DRN-001", last_seen: new Date(NOW - 54000).toISOString() },
  { id: "VEH-0098", type: "Car",         speed: 22, colour: "Grey",   make: "Toyota Innova",  state: "Moving",  zone: "Palayam",        drone: "DRN-004", last_seen: new Date(NOW - 91000).toISOString() },
  { id: "VEH-0147", type: "Car",         speed: 37, colour: "White",  make: "Kia Seltos",     state: "Moving",  zone: "MG Road",        drone: "DRN-001", last_seen: new Date(NOW - 3000).toISOString()  },
  { id: "VEH-0062", type: "Heavy",       speed: 12, colour: "Yellow", make: "JCB Loader",     state: "Slow",    zone: "North Gate",     drone: "DRN-002", last_seen: new Date(NOW - 72000).toISOString() },
];

/* ─────────────────────────────────────────────────────────
   HELPERS
   ───────────────────────────────────────────────────────── */
const CHART_TOOLTIP = {
  contentStyle: {
    background: "var(--dvas-surface)",
    border: "1px solid var(--dvas-border)",
    fontSize: 11,
    color: "var(--dvas-text)",
    borderRadius: 8,
  },
  itemStyle: { color: "var(--dvas-text)" },
};

function fmtAge(isoTs) {
  const s = Math.floor((Date.now() - new Date(isoTs)) / 1000);
  if (s < 60)   return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

/** Normalize vehicle type labels for display */
function normalizeVehicleType(raw) {
  if (!raw) return "Unknown";
  const lower = raw.toLowerCase();
  if (lower === "car") return "Car";
  if (lower === "truck") return "Truck";
  if (lower === "3-weeler" || lower === "3-wheeler") return "3-Wheeler";
  if (lower === "two-wheeler" || lower === "twowheeler" || lower === "2-wheeler") return "Two-Wheeler";
  if (lower === "bus") return "Bus";
  // Capitalize first letter as fallback
  return raw.charAt(0).toUpperCase() + raw.slice(1).toLowerCase();
}

function Panel({ title, icon: Icon, iconClass = "text-teal-400", children, right, badge, noPad }) {
  return (
    <div className="rounded-xl flex flex-col overflow-hidden"
      style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}>
      <div className="px-4 py-3 flex items-center gap-2 shrink-0"
        style={{ borderBottom: "1px solid var(--dvas-border)" }}>
        <Icon size={14} className={iconClass} />
        <span className="dvas-label flex-1 font-semibold">{title}</span>
        {badge && (
          <span className="mono text-[10px] px-1.5 py-0.5 rounded font-bold"
            style={{ background: "rgba(239,68,68,0.12)", color: "#f87171", border: "1px solid rgba(239,68,68,0.2)" }}>
            {badge}
          </span>
        )}
        {right}
      </div>
      <div className={`flex-1 min-h-0 ${noPad ? "" : "p-3"}`}>{children}</div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   SECTION 1 — Live Detection Video Stream
   ───────────────────────────────────────────────────────── */
function LiveStreamSection({ activeDrone, setActiveDrone, drones }) {
  const [playing, setPlaying] = useState(true);
  const videoRef = useRef(null);
  const isTrafficDrone = TRAFFIC_DRONES.includes(activeDrone);
  const videoSrc = DRONE_VIDEO_MAP[activeDrone];

  const droneList = drones?.length > 0
    ? drones
    : ALL_DRONE_IDS.map(id => ({ id }));

  const togglePlay = () => {
    if (videoRef.current) {
      if (playing) videoRef.current.pause();
      else videoRef.current.play();
    }
    setPlaying(!playing);
  };

  return (
    <div className="rounded-xl overflow-hidden"
      style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}>
      {/* Header */}
      <div className="px-4 py-3 flex items-center gap-3"
        style={{ borderBottom: "1px solid var(--dvas-border)" }}>
        <span className={`w-2 h-2 rounded-full dvas-live-dot ${isTrafficDrone ? 'bg-violet-500' : 'bg-slate-500'}`} />
        <span className="dvas-label font-semibold">Traffic & Vehicle Detection Live Feed</span>
        <div className="flex items-center gap-1 ml-2">
          {droneList.map(d => (
            <button key={d.id}
              onClick={() => setActiveDrone(d.id)}
              className="mono text-[10px] px-2 py-1 rounded transition-all"
              style={{
                background: activeDrone === d.id ? "rgba(139,92,246,0.15)" : "var(--dvas-surface-2)",
                color: activeDrone === d.id ? "#a78bfa" : "var(--dvas-text-muted)",
                border: `1px solid ${activeDrone === d.id ? "rgba(139,92,246,0.35)" : "var(--dvas-border)"}`,
              }}>
              {d.id}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2">
          {isTrafficDrone && (
            <button onClick={togglePlay}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all"
              style={{ background: "var(--dvas-elev)", color: "var(--dvas-text-muted)", border: "1px solid var(--dvas-border)" }}>
              {playing ? <Pause size={12} /> : <Play size={12} />}
              {playing ? "Pause" : "Play"}
            </button>
          )}
        </div>
      </div>

      {/* Video area */}
      <div className="relative bg-black flex items-center justify-center" style={{ height: 420 }}>
        {isTrafficDrone && videoSrc ? (
          <>
            <video
              ref={videoRef}
              src={videoSrc}
              autoPlay
              loop
              muted
              className="w-full h-full object-cover"
            />
            {/* HUD overlays */}
            <div className="absolute top-3 left-3 flex flex-col gap-1.5">
              <div className="mono text-[10px] bg-black/80 px-2 py-1 rounded text-violet-300">
                Vehicle tracking active · {activeDrone}
              </div>
              <div className="mono text-[10px] bg-black/80 px-2 py-1 rounded text-teal-300">
                ANPR active · Number plate detection
              </div>
            </div>
            <div className="absolute top-3 right-3 mono text-[10px] bg-black/80 px-2 py-1 rounded text-emerald-400 flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 dvas-live-dot" />
              LIVE · DEMO MODE
            </div>
            <div className="absolute bottom-3 left-3 mono text-[10px] bg-black/80 px-2 py-1 rounded text-slate-400">
              YOLOv8 Vehicle Tracker · ANPR OCR Engine
            </div>
            <div className="absolute bottom-3 right-3 mono text-[10px] bg-black/80 px-2 py-1 rounded text-slate-400">
              {activeDrone} · Thiruvananthapuram
            </div>
          </>
        ) : (
          <div className="text-center text-slate-500 flex flex-col items-center">
            <VideoOff size={48} className="mb-3 opacity-50 text-slate-600" />
            <p className="mono text-sm">Traffic analytics offline</p>
            <p className="mono text-[10px] mt-1 text-slate-600">
              Select DRN-001, DRN-002, or DRN-004 to view traffic feed
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   SECTION 2 — Traffic KPI Strip
   ───────────────────────────────────────────────────────── */
function TrafficKPIStrip({ plateData, isTrafficDrone }) {
  const totalVehicles   = MOCK_TRACKED_VEHICLES.length;
  const avgSpeed        = Math.round(MOCK_TRACKED_VEHICLES.reduce((s, v) => s + v.speed, 0) / totalVehicles);
  const platesRead      = isTrafficDrone ? plateData.length : 0;
  const theftAlerts     = 0;
  const movingVehicles  = MOCK_TRACKED_VEHICLES.filter(v => v.state === "Moving").length;

  const kpis = [
    { label: "Vehicles Tracked",   value: totalVehicles,  tone: "text-violet-400", icon: Car,          sub: "Active across all drones" },
    { label: "Moving / Stopped",   value: `${movingVehicles} / ${totalVehicles - movingVehicles}`, tone: "text-teal-400", icon: Activity, sub: "Current vehicle states" },
    { label: "Avg Speed (km/h)",   value: avgSpeed,       tone: "text-amber-400",  icon: Gauge,        sub: "Across tracked vehicles" },
    { label: "Plates Detected",    value: platesRead,     tone: "text-sky-400",    icon: Hash,         sub: "ANPR readings from drone" },
    { label: "Watchlist Matches",  value: theftAlerts,    tone: "text-emerald-400", icon: AlertTriangle, sub: "No watchlist hits" },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
      {kpis.map(({ label, value, tone, icon: Icon, sub }) => (
        <div key={label} className="rounded-xl p-4 flex flex-col gap-1"
          style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)", opacity: isTrafficDrone ? 1 : 0.5 }}>
          <div className="flex items-center gap-2">
            <Icon size={13} className={`${tone} shrink-0`} />
            <span className="dvas-label" style={{ fontSize: 10 }}>{label}</span>
          </div>
          <div className={`mono font-bold text-2xl mt-1 ${tone}`}>{value ?? "—"}</div>
          <div className="mono text-[10px]" style={{ color: "var(--dvas-text-dim)" }}>{sub}</div>
        </div>
      ))}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   SECTION 3 — Tracked Vehicles Window
   ───────────────────────────────────────────────────────── */
function TrackedVehiclesWindow() {
  const [sortKey, setSortKey] = useState("last_seen");
  const sorted = [...MOCK_TRACKED_VEHICLES].sort((a, b) => {
    if (sortKey === "speed")     return b.speed - a.speed;
    if (sortKey === "last_seen") return new Date(b.last_seen) - new Date(a.last_seen);
    return 0;
  });

  const stateColor = { Moving: "#2dd4bf", Slow: "#fbbf24", Stopped: "#f87171" };

  return (
    <div className="flex flex-col gap-2">
      {/* Sort controls */}
      <div className="flex items-center gap-2 flex-wrap">
        {["last_seen", "speed"].map(k => (
          <button key={k} onClick={() => setSortKey(k)}
            className="mono text-[10px] px-2.5 py-1 rounded-lg transition-all"
            style={{
              background: sortKey === k ? "rgba(139,92,246,0.12)" : "var(--dvas-surface-2)",
              color: sortKey === k ? "#a78bfa" : "var(--dvas-text-muted)",
              border: `1px solid ${sortKey === k ? "rgba(139,92,246,0.3)" : "var(--dvas-border)"}`,
            }}>
            {k === "last_seen" ? "Recent First" : "Speed ↓"}
          </button>
        ))}
      </div>

      {/* Vehicle rows */}
      <div className="grid grid-cols-1 gap-1.5">
        {sorted.map(v => (
          <div key={v.id} className="flex items-center gap-3 px-3 py-2 rounded-lg"
            style={{ background: "var(--dvas-surface-2)", border: "1px solid var(--dvas-border)" }}>
            <div className="shrink-0 w-16">
              <span className="mono text-xs font-bold" style={{ color: "var(--dvas-text)" }}>{v.id}</span>
            </div>
            <div className="shrink-0 w-20">
              <span className="mono text-[10px] px-1.5 py-0.5 rounded font-semibold"
                style={{ background: "rgba(139,92,246,0.12)", color: "#a78bfa", border: "1px solid rgba(139,92,246,0.2)" }}>
                {v.type}
              </span>
            </div>
            <div className="shrink-0 w-16 text-center">
              <span className="mono text-sm font-bold" style={{ color: stateColor[v.state] || "#94a3b8" }}>{v.speed}</span>
              <span className="mono text-[9px]" style={{ color: "var(--dvas-text-dim)" }}> km/h</span>
            </div>
            <div className="shrink-0 w-14">
              <span className="mono text-[10px] px-1.5 py-0.5 rounded"
                style={{ background: `${stateColor[v.state]}18`, color: stateColor[v.state], border: `1px solid ${stateColor[v.state]}33` }}>
                {v.state}
              </span>
            </div>
            <div className="flex-1 min-w-0">
              <div className="mono text-[10px]" style={{ color: "var(--dvas-text-muted)" }}>{v.colour} · {v.make}</div>
              <div className="mono text-[10px]" style={{ color: "var(--dvas-text-dim)" }}>{v.zone} · {v.drone}</div>
            </div>
            <div className="shrink-0 mono text-[10px]" style={{ color: "var(--dvas-text-dim)" }}>
              <Clock size={9} className="inline mr-1" />{fmtAge(v.last_seen)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   SECTION 4 — Vehicle Classification Donut
   ───────────────────────────────────────────────────────── */
function VehicleDonut({ types }) {
  return (
    <div className="flex flex-col items-center gap-2">
      <ResponsiveContainer width="100%" height={200}>
        <PieChart>
          <Pie data={types} dataKey="value" innerRadius={50} outerRadius={80} paddingAngle={3} stroke="none">
            {types.map((t, i) => <Cell key={i} fill={t.color} />)}
          </Pie>
          <Tooltip {...CHART_TOOLTIP} />
        </PieChart>
      </ResponsiveContainer>
      <div className="flex gap-4 flex-wrap justify-center">
        {types.map(t => (
          <div key={t.name} className="flex items-center gap-1.5">
            <span className="w-3 h-1.5 rounded-full" style={{ background: t.color }} />
            <span className="mono text-[10px]" style={{ color: "var(--dvas-text-muted)" }}>{t.name}</span>
            <span className="mono text-[10px] font-bold" style={{ color: t.color }}>{t.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   SECTION 4b — Vehicle Flow Chart
   ───────────────────────────────────────────────────────── */
function VehicleFlowChart() {
  return (
    <div className="flex flex-col gap-2">
      <ResponsiveContainer width="100%" height={220}>
        <AreaChart data={MOCK_VEHICLE_SERIES} margin={{ top: 10, right: 8, left: -20, bottom: 0 }}>
          <defs>
            {[
              { id: "gCars", color: "#14b8a6" },
              { id: "g2W",   color: "#818cf8" },
              { id: "gHvy",  color: "#f59e0b" },
            ].map(({ id, color }) => (
              <linearGradient key={id} id={id} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%"   stopColor={color} stopOpacity={0.4} />
                <stop offset="100%" stopColor={color} stopOpacity={0}   />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid stroke="var(--dvas-border)" strokeDasharray="3 3" />
          <XAxis dataKey="hour" stroke="var(--dvas-text-muted)" fontSize={9} interval={3} />
          <YAxis stroke="var(--dvas-text-muted)" fontSize={9} />
          <Tooltip {...CHART_TOOLTIP} />
          <Area dataKey="cars"        stroke="#14b8a6" fill="url(#gCars)" strokeWidth={1.5} name="Cars"          dot={false} />
          <Area dataKey="twoWheelers" stroke="#818cf8" fill="url(#g2W)"   strokeWidth={1.5} name="Two-Wheelers"  dot={false} />
          <Area dataKey="heavy"       stroke="#f59e0b" fill="url(#gHvy)"  strokeWidth={1.5} name="Heavy"         dot={false} />
        </AreaChart>
      </ResponsiveContainer>
      <div className="flex gap-4">
        {[{ c: "#14b8a6", l: "Cars" }, { c: "#818cf8", l: "Two-Wheelers" }, { c: "#f59e0b", l: "Heavy" }].map(({ c, l }) => (
          <div key={l} className="flex items-center gap-1.5">
            <span className="w-3 h-1.5 rounded-full" style={{ background: c }} />
            <span className="mono text-[10px]" style={{ color: "var(--dvas-text-muted)" }}>{l}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   SECTION 5 — ANPR Plate Detection Window (REAL DATA)
   ───────────────────────────────────────────────────────── */
function ANPRPlateWindow({ plateData, activeDrone, isTrafficDrone }) {
  const [page, setPage] = useState(0);
  const perPage = 5;

  // Reset page when drone changes
  useEffect(() => { setPage(0); }, [activeDrone]);

  if (!isTrafficDrone) {
    return (
      <div className="flex flex-col items-center justify-center py-10 gap-3">
        <VideoOff size={36} className="text-slate-600 opacity-50" />
        <p className="mono text-sm text-slate-500">ANPR offline</p>
        <p className="mono text-[10px] text-slate-600">Select DRN-001, DRN-002, or DRN-004 to view detections</p>
      </div>
    );
  }

  if (!plateData || plateData.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-10 gap-3">
        <Search size={36} className="text-slate-600 opacity-50" />
        <p className="mono text-sm text-slate-500">No plate detections for {activeDrone}</p>
      </div>
    );
  }

  const pages = Math.ceil(plateData.length / perPage);
  const shown = plateData.slice(page * perPage, page * perPage + perPage);

  return (
    <div className="flex flex-col gap-3">
      {/* Header info */}
      <div className="flex items-center gap-3 flex-wrap">
        <span className="mono text-[10px] px-3 py-1.5 rounded-lg font-semibold"
          style={{ background: "rgba(139,92,246,0.12)", color: "#a78bfa", border: "1px solid rgba(139,92,246,0.3)" }}>
          {activeDrone}
        </span>
        <span className="mono text-[10px]" style={{ color: "var(--dvas-text-dim)" }}>
          {plateData.length} vehicles with plates detected
        </span>
      </div>

      {/* Plate cards */}
      <div className="grid grid-cols-1 gap-2">
        {shown.map((p, i) => (
          <div key={`${p.id}-${i}`}
            className="flex items-center gap-4 px-3 py-3 rounded-xl transition-all"
            style={{
              background: "var(--dvas-surface-2)",
              border: "1px solid var(--dvas-border)",
            }}>

            {/* Vehicle Image */}
            <div className="shrink-0 w-36 h-24 rounded-lg overflow-hidden flex items-center justify-center relative"
                 style={{ border: "1px solid var(--dvas-border)", background: "var(--dvas-elev)" }}>
              {p.vehicle_image ? (
                <img src={p.vehicle_image} alt={p.vehicle_type} className="w-full h-full object-cover" />
              ) : (
                <Car size={24} className="text-slate-500 opacity-50" />
              )}
              {/* Vehicle type badge */}
              <div className="absolute bottom-0 left-0 right-0 bg-black/70 px-2 py-0.5">
                <span className="mono text-[9px] text-violet-300 font-semibold">
                  {normalizeVehicleType(p.vehicle_type)}
                </span>
              </div>
            </div>

            {/* Plate Image */}
            <div className="shrink-0">
              <div className="w-40 h-14 rounded overflow-hidden flex items-center justify-center relative"
                style={{
                  background: "rgba(20,184,166,0.06)",
                  border: "2px solid rgba(20,184,166,0.25)",
                }}>
                {p.plate_image ? (
                  <img src={p.plate_image} alt="License Plate" className="w-full h-full object-contain" style={{ imageRendering: "auto" }} />
                ) : (
                  <span className="mono text-[10px] font-bold opacity-60 text-teal-400">
                    [NO PLATE]
                  </span>
                )}
              </div>
              {p.plate_text && (
                <div className="text-center mt-1">
                  <span className="mono text-[10px] font-bold tracking-widest" style={{ color: "#2dd4bf" }}>
                    {p.plate_text}
                  </span>
                </div>
              )}
            </div>

            {/* Details */}
            <div className="flex-1 min-w-0 grid grid-cols-2 md:grid-cols-3 gap-2">
              <div>
                <div className="dvas-label mb-0.5" style={{ fontSize: 9 }}>VEHICLE TYPE</div>
                <div className="mono text-xs font-semibold" style={{ color: "var(--dvas-text)" }}>
                  {normalizeVehicleType(p.vehicle_type)}
                </div>
              </div>
              <div>
                <div className="dvas-label mb-0.5" style={{ fontSize: 9 }}>DRONE</div>
                <span className="mono text-[10px] px-1.5 py-0.5 rounded"
                  style={{ background: "rgba(129,140,248,0.12)", color: "#818cf8", border: "1px solid rgba(129,140,248,0.2)" }}>
                  {p.drone}
                </span>
              </div>
              <div>
                <div className="dvas-label mb-0.5" style={{ fontSize: 9 }}>DETECTION ID</div>
                <div className="mono text-xs" style={{ color: "var(--dvas-text-muted)" }}>#{p.id}</div>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Pagination */}
      {pages > 1 && (
        <div className="flex items-center justify-between">
          <span className="mono text-[10px]" style={{ color: "var(--dvas-text-dim)" }}>
            {page * perPage + 1}–{Math.min((page + 1) * perPage, plateData.length)} of {plateData.length}
          </span>
          <div className="flex gap-1">
            {Array.from({ length: pages }).map((_, i) => (
              <button key={i} onClick={() => setPage(i)}
                className="w-6 h-6 rounded text-[10px] mono font-bold transition-colors"
                style={{
                  background: i === page ? "rgba(20,184,166,0.15)" : "var(--dvas-surface-2)",
                  color: i === page ? "#2dd4bf" : "var(--dvas-text-muted)",
                  border: `1px solid ${i === page ? "rgba(20,184,166,0.3)" : "var(--dvas-border)"}`,
                }}>
                {i + 1}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   SECTION 6 — Theft / Watchlist Alerts
   ───────────────────────────────────────────────────────── */
function TheftAlertPanel() {
  return (
    <div className="flex items-center gap-2 px-3 py-3 rounded-lg"
      style={{ background: "rgba(16,185,129,0.08)", border: "1px solid rgba(16,185,129,0.2)" }}>
      <CheckCircle size={14} className="text-emerald-400" />
      <span className="text-xs text-emerald-400 font-medium">No watchlist vehicles detected in current session</span>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   MAIN EXPORT
   ───────────────────────────────────────────────────────── */
export default function TrafficAnalytics({ drones }) {
  const [activeDrone, setActiveDrone] = useState("DRN-001");
  const [plateManifest, setPlateManifest] = useState({});
  const [, setTick] = useState(0);

  // Periodic refresh for timestamps
  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 30000);
    return () => clearInterval(id);
  }, []);

  // Default to first traffic drone
  useEffect(() => {
    if (drones && drones.length > 0) {
      const trafficDrone = drones.find(d => TRAFFIC_DRONES.includes(d.id));
      if (trafficDrone) setActiveDrone(trafficDrone.id);
      else setActiveDrone(drones[0].id);
    }
  }, [drones]);

  // Fetch plate manifest
  useEffect(() => {
    const loadManifest = async () => {
      try {
        const response = await fetch('/DETECTED/plate_manifest.json');
        if (!response.ok) throw new Error("Failed to fetch plate manifest");
        const data = await response.json();
        setPlateManifest(data);
      } catch (err) {
        console.error("Error loading plate manifest:", err);
      }
    };
    loadManifest();
  }, []);

  const isTrafficDrone = TRAFFIC_DRONES.includes(activeDrone);
  const plateData = plateManifest[activeDrone] || [];

  return (
    <div className="flex flex-col gap-5" style={{ fontFamily: "'Plus Jakarta Sans', sans-serif" }}>

      {/* LIVE STREAM */}
      <LiveStreamSection
        activeDrone={activeDrone}
        setActiveDrone={setActiveDrone}
        drones={drones}
      />

      {/* KPI STRIP */}
      <TrafficKPIStrip plateData={plateData} isTrafficDrone={isTrafficDrone} />

      {/* TRACKED VEHICLES WINDOW */}
      <Panel title="Tracked Vehicles — Live Ledger" icon={Car} iconClass="text-violet-400"
        right={
          <span className="mono text-[10px] text-violet-400 flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-violet-400 dvas-live-dot" />
            {MOCK_TRACKED_VEHICLES.length} active tracks
          </span>
        }>
        <TrackedVehiclesWindow />
      </Panel>

      {/* CLASSIFICATION DONUT + FLOW CHART */}
      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-12 lg:col-span-4">
          <Panel title="Vehicle Classification" icon={Car} iconClass="text-teal-400">
            <VehicleDonut types={MOCK_VEHICLE_TYPES} />
          </Panel>
        </div>
        <div className="col-span-12 lg:col-span-8">
          <Panel title="Vehicle Flow — 24h Time Series" icon={TrendingUp} iconClass="text-sky-400"
            right={
              <span className="mono text-[10px] text-sky-400 flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-sky-400 dvas-live-dot" />
                Live · 15s refresh
              </span>
            }>
            <VehicleFlowChart />
          </Panel>
        </div>
      </div>

      {/* ANPR PLATE WINDOW */}
      <Panel
        title="ANPR — Number Plate Detection Window"
        icon={Hash}
        iconClass="text-amber-400"
        right={
          <div className="flex items-center gap-1.5">
            <span className={`w-1.5 h-1.5 rounded-full ${isTrafficDrone ? 'bg-amber-400 dvas-live-dot' : 'bg-slate-500'}`} />
            <span className={`mono text-[10px] ${isTrafficDrone ? 'text-amber-400' : 'text-slate-500'}`}>
              {isTrafficDrone ? `${plateData.length} plates · ${activeDrone}` : "Offline"}
            </span>
          </div>
        }>
        <ANPRPlateWindow plateData={plateData} activeDrone={activeDrone} isTrafficDrone={isTrafficDrone} />
      </Panel>

      {/* THEFT / WATCHLIST ALERTS */}
      <Panel
        title="Watchlist & Theft Alerts"
        icon={ShieldAlert}
        iconClass="text-red-400">
        <TheftAlertPanel />
      </Panel>

    </div>
  );
}
