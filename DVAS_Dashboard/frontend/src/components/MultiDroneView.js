/**
 * MultiDroneView.js
 * ─────────────────────────────────────────────────────────
 * High-fidelity Multi-Drone Situational Command View
 * DVAS – Kerala Police | Central Command & Intelligence
 * ─────────────────────────────────────────────────────────
 */

import { useEffect, useRef, useState, useCallback } from "react";
import {
  Radio,
  Battery,
  BatteryLow,
  BatteryFull,
  BatteryMedium,
  Users,
  Car,
  AlertTriangle,
  ShieldAlert,
  Cpu,
  Activity,
  Eye,
  Play,
  ChevronRight,
  Maximize2,
  Wifi,
  WifiOff,
  Thermometer,
  Wind,
  MapPin,
  Clock,
  TrendingUp,
  TrendingDown,
  Zap,
  Circle,
  X,
  Layers,
  BarChart2,
} from "lucide-react";

/* ─────────────────────────────────────────────
   STATIC / FALLBACK DATA
   (merged with live API props)
   ───────────────────────────────────────────── */
const MOCK_EVENTS = [
  {
    id: "evt-001",
    ts: new Date(Date.now() - 4 * 60000).toISOString(),
    drone_id: "DR-02",
    mission_id: "M-104",
    event_type: "Perimeter Breach",
    confidence: 94,
    zone: "Sector-7 North",
    severity: "critical",
  },
  {
    id: "evt-002",
    ts: new Date(Date.now() - 11 * 60000).toISOString(),
    drone_id: "DR-01",
    mission_id: "M-102",
    event_type: "Crowd Surge",
    confidence: 88,
    zone: "Statue Junction",
    severity: "warning",
  },
  {
    id: "evt-003",
    ts: new Date(Date.now() - 23 * 60000).toISOString(),
    drone_id: "DR-02",
    mission_id: "M-104",
    event_type: "Suspicious Object",
    confidence: 77,
    zone: "Secretariat Rd",
    severity: "warning",
  },
  {
    id: "evt-004",
    ts: new Date(Date.now() - 38 * 60000).toISOString(),
    drone_id: "DR-01",
    mission_id: "M-102",
    event_type: "Vehicle Violation",
    confidence: 91,
    zone: "MG Road",
    severity: "info",
  },
  {
    id: "evt-005",
    ts: new Date(Date.now() - 61 * 60000).toISOString(),
    drone_id: "DR-03",
    mission_id: "M-106",
    event_type: "Loitering Detected",
    confidence: 82,
    zone: "East Fort",
    severity: "info",
  },
  {
    id: "evt-006",
    ts: new Date(Date.now() - 78 * 60000).toISOString(),
    drone_id: "DR-02",
    mission_id: "M-104",
    event_type: "Intrusion Detection",
    confidence: 96,
    zone: "VJT Hall",
    severity: "critical",
  },
];

const MOCK_DRONES = [
  {
    id: "DR-01",
    mission_id: "M-102",
    status: "live",
    battery_pct: 78,
    altitude_m: 120,
    speed_kmh: 18,
    lat: 8.5241,
    lng: 76.9366,
    jurisdiction: "Thiruvananthapuram Central",
    last_analytics: {
      crowd_density: "High",
      crowd_count: 342,
      vehicles: 210,
      cars: 150,
      two_wheelers: 60,
      traffic_state: "Congested",
    },
    signal_strength: 92,
    uptime_min: 47,
  },
  {
    id: "DR-02",
    mission_id: "M-104",
    status: "live",
    battery_pct: 51,
    altitude_m: 95,
    speed_kmh: 12,
    lat: 8.5281,
    lng: 76.9432,
    jurisdiction: "Thiruvananthapuram East",
    last_analytics: {
      crowd_density: "Medium",
      crowd_count: 187,
      vehicles: 134,
      cars: 98,
      two_wheelers: 36,
      traffic_state: "Moderate",
    },
    signal_strength: 85,
    uptime_min: 31,
  },
  {
    id: "DR-03",
    mission_id: "M-106",
    status: "degraded",
    battery_pct: 23,
    altitude_m: 60,
    speed_kmh: 5,
    lat: 8.5193,
    lng: 76.9319,
    jurisdiction: "Thiruvananthapuram South",
    last_analytics: {
      crowd_density: "Low",
      crowd_count: 54,
      vehicles: 38,
      cars: 27,
      two_wheelers: 11,
      traffic_state: "Clear",
    },
    signal_strength: 41,
    uptime_min: 72,
  },
];

/* ─────────────────────────────────────────────
   HELPERS
   ───────────────────────────────────────────── */
function fmtAge(isoTs) {
  const diff = Math.floor((Date.now() - new Date(isoTs)) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

function BatteryIcon({ pct }) {
  if (pct <= 20) return <BatteryLow size={12} className="text-red-400" />;
  if (pct <= 50) return <BatteryMedium size={12} className="text-amber-400" />;
  return <BatteryFull size={12} className="text-emerald-400" />;
}

function StatusPill({ status }) {
  const cfg = {
    live: {
      dot: "bg-emerald-400",
      bg: "bg-emerald-500/10",
      border: "border-emerald-500/30",
      text: "text-emerald-400",
      label: "LIVE",
    },
    degraded: {
      dot: "bg-amber-400",
      bg: "bg-amber-500/10",
      border: "border-amber-500/30",
      text: "text-amber-400",
      label: "DEGRADED",
    },
    offline: {
      dot: "bg-red-400",
      bg: "bg-red-500/10",
      border: "border-red-500/30",
      text: "text-red-400",
      label: "OFFLINE",
    },
  }[status] || {
    dot: "bg-slate-500",
    bg: "bg-slate-800/40",
    border: "border-slate-600/30",
    text: "text-slate-400",
    label: "UNKNOWN",
  };

  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[9px] font-bold tracking-widest ${cfg.bg} ${cfg.border} ${cfg.text} border`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${cfg.dot} dvas-live-dot`} />
      {cfg.label}
    </span>
  );
}

function SeverityBadge({ severity }) {
  const cfg = {
    critical: { bg: "bg-red-500/15", border: "border-red-500/30", text: "text-red-400" },
    warning: { bg: "bg-amber-500/15", border: "border-amber-500/30", text: "text-amber-400" },
    info: { bg: "bg-sky-500/15", border: "border-sky-500/30", text: "text-sky-400" },
  }[severity] || { bg: "bg-slate-800/40", border: "border-slate-600/30", text: "text-slate-400" };
  return (
    <span
      className={`inline-block w-2 h-2 rounded-full border ${cfg.bg} ${cfg.border}`}
      title={severity}
    />
  );
}

/* ─────────────────────────────────────────────
   MINI SPARKLINE (pure CSS bar chart)
   ───────────────────────────────────────────── */
function Sparkline({ values, color = "#14b8a6" }) {
  const max = Math.max(...values, 1);
  return (
    <div className="flex items-end gap-px h-8">
      {values.map((v, i) => (
        <div
          key={i}
          className="flex-1 rounded-sm opacity-80 transition-all duration-500"
          style={{ height: `${Math.max(8, (v / max) * 100)}%`, background: color }}
        />
      ))}
    </div>
  );
}

/* ─────────────────────────────────────────────
   HEATMAP MOCK  (pure CSS gradient)
   ───────────────────────────────────────────── */
function HeatmapGraphic({ density = "High" }) {
  const intensity = { High: 0.9, Medium: 0.55, Low: 0.2 }[density] ?? 0.5;
  return (
    <div className="relative w-full h-[80px] rounded-lg overflow-hidden border border-slate-700/40">
      {/* base grid */}
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 80% 60% at 35% 55%, rgba(239,68,68,0.55) 0%, rgba(245,158,11,0.35) 45%, rgba(20,184,166,0.15) 75%, transparent 100%)",
          opacity: intensity,
        }}
      />
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 40% 35% at 70% 35%, rgba(245,158,11,0.4) 0%, transparent 65%)",
          opacity: intensity * 0.7,
        }}
      />
      <div
        className="absolute inset-0"
        style={{
          backgroundImage:
            "linear-gradient(rgba(20,184,166,0.08) 1px, transparent 1px), linear-gradient(90deg, rgba(20,184,166,0.08) 1px, transparent 1px)",
          backgroundSize: "12px 12px",
        }}
      />
      <div className="absolute bottom-1 right-2 mono text-[8px] text-teal-400/70 font-semibold">
        DENSITY MAP
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────
   SIGNAL BAR
   ───────────────────────────────────────────── */
function SignalBars({ strength }) {
  const bars = 4;
  const active = Math.ceil((strength / 100) * bars);
  const color =
    strength > 70
      ? "bg-emerald-400"
      : strength > 40
      ? "bg-amber-400"
      : "bg-red-400";
  return (
    <div className="flex items-end gap-px h-3.5">
      {Array.from({ length: bars }).map((_, i) => (
        <div
          key={i}
          className={`w-1.5 rounded-sm transition-all ${
            i < active ? color : "bg-slate-700"
          }`}
          style={{ height: `${(i + 1) * 25}%` }}
        />
      ))}
    </div>
  );
}

/* ─────────────────────────────────────────────
   DRONE FEED CARD
   ───────────────────────────────────────────── */
function DroneFeedCard({ drone, isSelected, onSelect }) {
  const spark = useRef(
    Array.from({ length: 14 }, () => 30 + Math.random() * 60)
  );
  const [sparkData] = useState(spark.current);

  const isDegraded = drone.status === "degraded";
  const isOffline = drone.status === "offline";

  const borderColor = isDegraded
    ? "border-amber-500/40"
    : isOffline
    ? "border-red-500/40"
    : isSelected
    ? "border-teal-500/60"
    : "border-slate-700/60";

  const glowStyle = isSelected
    ? { boxShadow: "0 0 0 1px rgba(20,184,166,0.3), 0 4px 24px rgba(20,184,166,0.12)" }
    : isDegraded
    ? { boxShadow: "0 0 0 1px rgba(245,158,11,0.2)" }
    : {};

  return (
    <div
      className={`relative flex flex-col rounded-xl border ${borderColor} overflow-hidden cursor-pointer transition-all duration-200 hover:scale-[1.012] hover:border-teal-500/40`}
      style={{
        background: "var(--dvas-surface)",
        ...glowStyle,
      }}
      onClick={() => onSelect(drone.id === isSelected ? null : drone.id)}
      data-testid={`multidrone-card-${drone.id}`}
    >
      {/* ── Video Viewport ── */}
      <div className="relative aspect-video bg-[#060e1a] overflow-hidden flex-shrink-0">
        {/* scan-line animation */}
        <div
          className="absolute inset-0 pointer-events-none"
          style={{
            backgroundImage:
              "repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(20,184,166,0.015) 2px, rgba(20,184,166,0.015) 4px)",
          }}
        />

        {/* Corner brackets */}
        {[
          "top-2 left-2 border-l-2 border-t-2",
          "top-2 right-2 border-r-2 border-t-2",
          "bottom-2 left-2 border-l-2 border-b-2",
          "bottom-2 right-2 border-r-2 border-b-2",
        ].map((cls, i) => (
          <div
            key={i}
            className={`absolute w-4 h-4 ${cls} ${
              isDegraded
                ? "border-amber-400/60"
                : isOffline
                ? "border-red-400/60"
                : "border-teal-400/60"
            }`}
          />
        ))}

        {/* Drone ID badge (top-left) */}
        <div className="absolute top-3 left-3 z-10 flex items-center gap-1.5">
          <div
            className="mono text-[11px] font-bold px-2 py-0.5 rounded"
            style={{
              background: "rgba(6,14,26,0.82)",
              color: isDegraded ? "#fbbf24" : "#2dd4bf",
              border: `1px solid ${isDegraded ? "rgba(245,158,11,0.3)" : "rgba(20,184,166,0.3)"}`,
            }}
          >
            {drone.id}
          </div>
          <div
            className="mono text-[9px] px-1.5 py-0.5 rounded"
            style={{ background: "rgba(6,14,26,0.70)", color: "#94a3b8" }}
          >
            {drone.mission_id}
          </div>
        </div>

        {/* Status pill (top-right) */}
        <div className="absolute top-3 right-3 z-10">
          <StatusPill status={drone.status} />
        </div>

        {/* Center graphic: cognitive feed placeholder */}
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          {isOffline ? (
            <div className="flex flex-col items-center gap-2">
              <WifiOff size={22} className="text-red-400/60" />
              <span className="mono text-[10px] text-red-400/60">FEED OFFLINE</span>
            </div>
          ) : (
            <>
              {/* Simulated thermal-overlay grid */}
              <div
                className="absolute inset-0 opacity-20"
                style={{
                  background: isDegraded
                    ? "radial-gradient(ellipse at 50% 50%, rgba(245,158,11,0.3) 0%, transparent 70%)"
                    : "radial-gradient(ellipse at 50% 50%, rgba(20,184,166,0.15) 0%, transparent 70%)",
                }}
              />
              {/* Centered crosshair */}
              <div className="relative">
                <div
                  className="w-8 h-8 rounded-full border flex items-center justify-center"
                  style={{
                    borderColor: isDegraded
                      ? "rgba(245,158,11,0.25)"
                      : "rgba(20,184,166,0.25)",
                    background: isDegraded
                      ? "rgba(245,158,11,0.05)"
                      : "rgba(20,184,166,0.05)",
                  }}
                >
                  <div
                    className="w-1.5 h-1.5 rounded-full"
                    style={{
                      background: isDegraded ? "#fbbf24" : "#2dd4bf",
                      opacity: 0.7,
                    }}
                  />
                </div>
                <div
                  className="absolute top-1/2 -translate-y-1/2 -left-5 w-3 h-px"
                  style={{ background: isDegraded ? "rgba(245,158,11,0.35)" : "rgba(20,184,166,0.35)" }}
                />
                <div
                  className="absolute top-1/2 -translate-y-1/2 -right-5 w-3 h-px"
                  style={{ background: isDegraded ? "rgba(245,158,11,0.35)" : "rgba(20,184,166,0.35)" }}
                />
                <div
                  className="absolute -top-5 left-1/2 -translate-x-1/2 w-px h-3"
                  style={{ background: isDegraded ? "rgba(245,158,11,0.35)" : "rgba(20,184,166,0.35)" }}
                />
                <div
                  className="absolute -bottom-5 left-1/2 -translate-x-1/2 w-px h-3"
                  style={{ background: isDegraded ? "rgba(245,158,11,0.35)" : "rgba(20,184,166,0.35)" }}
                />
              </div>
              <div className="absolute bottom-10 left-0 right-0 flex justify-center">
                <div
                  className="mono text-[8px] px-2 py-0.5 rounded"
                  style={{
                    background: "rgba(6,14,26,0.65)",
                    color: "rgba(148,163,184,0.5)",
                  }}
                >
                  COGNITIVE FRAME · AI PROCESSING
                </div>
              </div>
            </>
          )}
        </div>

        {/* Altitude & speed HUD (bottom bar) */}
        <div
          className="absolute bottom-0 left-0 right-0 px-3 py-1.5 flex items-center gap-3"
          style={{ background: "rgba(6,14,26,0.8)", backdropFilter: "blur(4px)" }}
        >
          <div className="flex items-center gap-1">
            <Wind size={9} className="text-slate-500" />
            <span className="mono text-[9px] text-slate-400">{drone.altitude_m}m</span>
          </div>
          <div className="flex items-center gap-1">
            <Activity size={9} className="text-slate-500" />
            <span className="mono text-[9px] text-slate-400">{drone.speed_kmh} km/h</span>
          </div>
          <div className="flex items-center gap-1 ml-auto">
            <SignalBars strength={drone.signal_strength} />
            <span className="mono text-[9px] text-slate-500">{drone.signal_strength}%</span>
          </div>
        </div>
      </div>

      {/* ── Telemetry Strip ── */}
      <div className="px-3 py-2.5 flex flex-col gap-2">
        {/* Row 1: Zone name */}
        <div className="flex items-center gap-1.5">
          <MapPin size={9} className="text-slate-600 shrink-0" />
          <span className="mono text-[9px] text-slate-500 truncate">{drone.jurisdiction}</span>
          <span className="ml-auto flex items-center gap-1 shrink-0">
            <Clock size={9} className="text-slate-600" />
            <span className="mono text-[9px] text-slate-600">{drone.uptime_min}m</span>
          </span>
        </div>

        {/* Row 2: KPI trio */}
        <div className="grid grid-cols-3 gap-1">
          {/* Battery */}
          <div
            className="flex flex-col items-center rounded-lg py-1.5 gap-0.5"
            style={{ background: "var(--dvas-surface-2)", border: "1px solid var(--dvas-border)" }}
          >
            <div className="flex items-center gap-1">
              <BatteryIcon pct={drone.battery_pct} />
              <span
                className={`mono text-[11px] font-bold ${
                  drone.battery_pct <= 20
                    ? "text-red-400"
                    : drone.battery_pct <= 50
                    ? "text-amber-400"
                    : "text-emerald-400"
                }`}
              >
                {drone.battery_pct}%
              </span>
            </div>
            <span className="mono text-[8px] text-slate-600">Battery</span>
          </div>

          {/* Crowd */}
          <div
            className="flex flex-col items-center rounded-lg py-1.5 gap-0.5"
            style={{ background: "var(--dvas-surface-2)", border: "1px solid var(--dvas-border)" }}
          >
            <div className="flex items-center gap-1">
              <Users size={10} className="text-sky-400" />
              <span className="mono text-[11px] font-bold text-slate-200">
                {drone.last_analytics?.crowd_count ?? "—"}
              </span>
            </div>
            <span className="mono text-[8px] text-slate-600">Crowd</span>
          </div>

          {/* Vehicles */}
          <div
            className="flex flex-col items-center rounded-lg py-1.5 gap-0.5"
            style={{ background: "var(--dvas-surface-2)", border: "1px solid var(--dvas-border)" }}
          >
            <div className="flex items-center gap-1">
              <Car size={10} className="text-violet-400" />
              <span className="mono text-[11px] font-bold text-slate-200">
                {drone.last_analytics?.vehicles ?? "—"}
              </span>
            </div>
            <span className="mono text-[8px] text-slate-600">Vehicles</span>
          </div>
        </div>

        {/* Row 3: Sparkline */}
        <Sparkline
          values={sparkData}
          color={isDegraded ? "#f59e0b" : "#14b8a6"}
        />
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────
   ANALYTICS SIDEBAR
   ───────────────────────────────────────────── */
/* ─────────────────────────────────────────────
   ANALYTICS ROW  (4 panels, full-width horizontal)
   ───────────────────────────────────────────── */
function AnalyticsRow({ selectedDrone, drones }) {
  const drone = drones.find((d) => d.id === selectedDrone) || drones[0];
  const ana = drone?.last_analytics || {};

  const densityColor = {
    High: "text-red-400", Medium: "text-amber-400", Low: "text-emerald-400",
  }[ana.crowd_density] || "text-slate-400";

  const trafficColor = {
    Congested: "text-red-400", Moderate: "text-amber-400", Clear: "text-emerald-400",
  }[ana.traffic_state] || "text-slate-400";

  const gpuStats = [
    { label: "Person Det.", pct: 87, color: "#14b8a6" },
    { label: "Vehicle Det.", pct: 72, color: "#818cf8" },
    { label: "Face Recog.", pct: 64, color: "#f59e0b" },
    { label: "ANPR",        pct: 55, color: "#34d399" },
  ];

  const safetyItems = [
    { label: "Intrusion Detection", active: drone?.id === "DR-02", severity: "critical" },
    { label: "Suspicious Object",   active: drone?.id === "DR-02", severity: "warning"  },
    { label: "Perimeter Breach",    active: drone?.id === "DR-02", severity: "critical" },
  ];

  /* shared panel shell */
  const Panel = ({ children, borderColor = "var(--dvas-border)" }) => (
    <div
      className="rounded-lg p-3 flex flex-col gap-2"
      style={{ background: "var(--dvas-surface)", border: `1px solid ${borderColor}` }}
    >
      {children}
    </div>
  );

  /* section heading — uses dvas-label at its natural 11px */
  const SectionHead = ({ icon, label, right }) => (
    <div className="flex items-center gap-1.5 pb-1" style={{ borderBottom: "1px solid var(--dvas-border)" }}>
      {icon}
      <span className="dvas-label">{label}</span>
      {right && <span className="ml-auto mono text-slate-500 text-xs">{right}</span>}
    </div>
  );

  /* label + bold value row */
  const MetricRow = ({ label, value, valueClass = "text-slate-100" }) => (
    <div className="flex items-center justify-between">
      <span className="mono text-slate-500 text-xs">{label}</span>
      <span className={`mono font-bold text-sm ${valueClass}`}>{value ?? "—"}</span>
    </div>
  );

  /* progress bar — taller for readability */
  const Bar = ({ pct, color }) => (
    <div className="h-1.5 rounded-full overflow-hidden" style={{ background: "var(--dvas-elev)" }}>
      <div className="h-full rounded-full transition-all duration-700" style={{ width: `${pct}%`, background: color }} />
    </div>
  );

  return (
    <div className="grid grid-cols-4 gap-3">

      {/* ── A: Human & Crowd ── */}
      <Panel>
        <SectionHead
          icon={<Users size={13} className="text-sky-400" />}
          label="Human & Crowd Analytics"
        />

        {/* heatmap */}
        <div className="relative w-full rounded overflow-hidden" style={{ height: 52, border: "1px solid var(--dvas-border)" }}>
          <div className="absolute inset-0" style={{ background: "var(--dvas-surface-2)" }} />
          <div className="absolute inset-0" style={{
            background: "radial-gradient(ellipse 80% 60% at 35% 55%, rgba(239,68,68,0.50) 0%, rgba(245,158,11,0.30) 45%, rgba(20,184,166,0.12) 75%, transparent 100%)",
            opacity: { High: 0.9, Medium: 0.55, Low: 0.2 }[ana.crowd_density] ?? 0.5,
          }} />
          <div className="absolute inset-0" style={{
            backgroundImage: "linear-gradient(rgba(20,184,166,0.07) 1px,transparent 1px),linear-gradient(90deg,rgba(20,184,166,0.07) 1px,transparent 1px)",
            backgroundSize: "12px 12px",
          }} />
          <span className="absolute bottom-0.5 right-1.5 mono text-teal-500 text-[9px]">DENSITY MAP</span>
        </div>

        <MetricRow label="Crowd Density"  value={ana.crowd_density}  valueClass={densityColor} />
        <MetricRow label="Active Persons" value={ana.crowd_count} />
        <Bar
          pct={ana.crowd_density === "High" ? 88 : ana.crowd_density === "Medium" ? 55 : 22}
          color={
            ana.crowd_density === "High"   ? "linear-gradient(90deg,#ef4444,#f87171)" :
            ana.crowd_density === "Medium" ? "linear-gradient(90deg,#f59e0b,#fbbf24)" :
                                             "linear-gradient(90deg,#10b981,#34d399)"
          }
        />
      </Panel>

      {/* ── B: Vehicle Metrics ── */}
      <Panel>
        <SectionHead
          icon={<Car size={13} className="text-violet-400" />}
          label="Vehicle Metrics"
        />

        {[
          { label: "Cars",         count: ana.cars         ?? 0, max: 200, color: "#818cf8" },
          { label: "Two-Wheelers", count: ana.two_wheelers ?? 0, max: 100, color: "#c084fc" },
        ].map(({ label, count, max, color }) => (
          <div key={label} className="flex flex-col gap-1">
            <div className="flex justify-between items-center">
              <span className="mono text-slate-500 text-xs">{label}</span>
              <span className="mono font-bold text-sm text-slate-100">{count}</span>
            </div>
            <Bar pct={Math.min(100, (count / max) * 100)} color={color} />
          </div>
        ))}

        <MetricRow label="Traffic State" value={ana.traffic_state} valueClass={trafficColor} />
      </Panel>

      {/* ── C: Safety Intelligence ── */}
      <Panel borderColor="rgba(239,68,68,0.3)">
        <SectionHead
          icon={<ShieldAlert size={13} className="text-red-400" />}
          label="Safety Intelligence"
        />

        <div className="flex flex-col gap-1.5">
          {safetyItems.map(({ label, active, severity }) => (
            <div
              key={label}
              className="flex items-center gap-2 px-2 py-1.5 rounded"
              style={{
                background: active
                  ? severity === "critical" ? "rgba(239,68,68,0.10)" : "rgba(245,158,11,0.08)"
                  : "var(--dvas-surface-2)",
                border: active
                  ? severity === "critical" ? "1px solid rgba(239,68,68,0.22)" : "1px solid rgba(245,158,11,0.18)"
                  : "1px solid var(--dvas-border)",
              }}
            >
              <span
                className={`w-2 h-2 rounded-full shrink-0 ${
                  active
                    ? severity === "critical" ? "bg-red-400 dvas-live-dot" : "bg-amber-400 dvas-live-dot"
                    : "bg-slate-600"
                }`}
              />
              <span
                className="mono text-xs flex-1"
                style={{
                  color: active
                    ? severity === "critical" ? "#fca5a5" : "#fcd34d"
                    : "var(--dvas-text-muted)",
                }}
              >
                {label}
              </span>
              {active && (
                <span
                  className="mono font-bold text-[10px] px-1.5 py-0.5 rounded"
                  style={{
                    color: severity === "critical" ? "#f87171" : "#fbbf24",
                    background: severity === "critical" ? "rgba(239,68,68,0.15)" : "rgba(245,158,11,0.12)",
                  }}
                >
                  ACTIVE
                </span>
              )}
            </div>
          ))}
        </div>
      </Panel>

      {/* ── D: GPU Pipeline Load ── */}
      <Panel>
        <SectionHead
          icon={<BarChart2 size={13} className="text-teal-400" />}
          label="GPU Pipeline Load"
          right="Processing Batch"
        />

        <div className="flex flex-col gap-2">
          {gpuStats.map(({ label, pct, color }) => (
            <div key={label} className="flex flex-col gap-1">
              <div className="flex justify-between items-center">
                <span className="mono text-slate-500 text-xs">{label}</span>
                <span className="mono font-bold text-sm" style={{ color }}>{pct}%</span>
              </div>
              <Bar pct={pct} color={color} />
            </div>
          ))}
        </div>
      </Panel>

    </div>
  );
}

/* ─────────────────────────────────────────────
   EVENT TIMELINE
   ───────────────────────────────────────────── */
function EventTimeline({ events, onPlay }) {
  const severityIcon = {
    critical: <AlertTriangle size={11} className="text-red-400 shrink-0" />,
    warning: <AlertTriangle size={11} className="text-amber-400 shrink-0" />,
    info: <Eye size={11} className="text-sky-400 shrink-0" />,
  };

  const severityRowStyle = {
    critical: {
      bg: "rgba(239,68,68,0.05)",
      border: "rgba(239,68,68,0.15)",
      textType: "text-red-300",
    },
    warning: {
      bg: "rgba(245,158,11,0.05)",
      border: "rgba(245,158,11,0.12)",
      textType: "text-amber-300",
    },
    info: {
      bg: "rgba(14,165,233,0.04)",
      border: "rgba(14,165,233,0.1)",
      textType: "text-sky-300",
    },
  };

  return (
    <div
      className="rounded-xl overflow-hidden"
      style={{
        background: "var(--dvas-surface)",
        border: "1px solid var(--dvas-border)",
      }}
    >
      {/* Title bar */}
      <div
        className="px-4 py-2.5 flex items-center gap-3"
        style={{ borderBottom: "1px solid var(--dvas-border)" }}
      >
        <Layers size={13} className="text-teal-400 shrink-0" />
        <span className="text-[10px] font-bold text-teal-400 tracking-wider uppercase flex-1">
          Confirmed Events &amp; Evidence Log
        </span>
        <span className="mono text-[9px] text-slate-600">{events.length} events · Last 2h</span>
      </div>

      {/* Scrollable event list */}
      <div className="overflow-x-auto">
        <div className="flex gap-2 px-3 py-2" style={{ minWidth: "max-content" }}>
          {events.map((evt) => {
            const s = severityRowStyle[evt.severity] || severityRowStyle.info;
            return (
              <div
                key={evt.id}
                data-testid={`event-${evt.id}`}
                className="flex-shrink-0 flex flex-col gap-1.5 rounded-xl px-3 py-2.5 w-56 transition-all duration-150 hover:scale-[1.02]"
                style={{
                  background: s.bg || "var(--dvas-surface-2)",
                  border: `1px solid ${s.border || "var(--dvas-border)"}`,
                  minWidth: "210px",
                }}
              >
                {/* Top row: severity + timestamp */}
                <div className="flex items-center gap-1.5">
                  {severityIcon[evt.severity] || severityIcon.info}
                  <span className="mono text-[9px] text-slate-500 flex-1 truncate">
                    {fmtAge(evt.ts)}
                  </span>
                  <span
                    className="mono text-[8px] px-1.5 py-0.5 rounded font-bold"
                    style={{
                      background: "rgba(14,184,166,0.1)",
                      color: "#2dd4bf",
                      border: "1px solid rgba(20,184,166,0.2)",
                    }}
                  >
                    {evt.drone_id}
                  </span>
                </div>

                {/* Event type */}
                <div className={`mono text-[11px] font-bold ${s.textType}`}>
                  {evt.event_type}
                </div>

                {/* Zone + mission */}
                <div className="flex items-center gap-1">
                  <MapPin size={8} className="text-slate-600 shrink-0" />
                  <span className="mono text-[8px] text-slate-500 truncate">{evt.zone}</span>
                </div>

                {/* Confidence + Play */}
                <div className="flex items-center gap-2 mt-0.5">
                  <div className="flex-1">
                    <div className="flex items-center gap-1">
                      <TrendingUp size={8} className="text-slate-600" />
                      <span className="mono text-[9px] text-slate-400">
                        Conf:{" "}
                        <span
                          className={`font-bold ${
                            evt.confidence >= 90 ? "text-emerald-400" : "text-amber-400"
                          }`}
                        >
                          {evt.confidence}%
                        </span>
                      </span>
                    </div>
                  </div>
                  <button
                    onClick={(e) => { e.stopPropagation(); onPlay && onPlay(evt); }}
                    className="flex items-center gap-1 px-2 py-1 rounded-lg text-[8px] font-semibold mono transition-all hover:scale-105"
                    style={{
                      background: "rgba(20,184,166,0.1)",
                      border: "1px solid rgba(20,184,166,0.25)",
                      color: "#2dd4bf",
                    }}
                    data-testid={`play-evidence-${evt.id}`}
                  >
                    <Play size={8} />
                    Clip
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────
   GLOBAL HEADER BAR (inside multidrone view)
   ───────────────────────────────────────────── */
function GlobalHeader({ drones, alerts, connected }) {
  const online = drones.filter((d) => d.status === "live").length;
  const activeMissions = [...new Set(drones.map((d) => d.mission_id))].length;
  const sysAlerts = alerts?.length ?? 1010;

  const metrics = [
    {
      label: "Drones Online",
      value: `${online} / ${drones.length}`,
      tone: "text-emerald-400",
      icon: <Radio size={12} className="text-emerald-400" />,
      glow: "rgba(16,185,129,0.12)",
    },
    {
      label: "Active Missions",
      value: activeMissions,
      tone: "text-sky-400",
      icon: <Activity size={12} className="text-sky-400" />,
      glow: "rgba(14,165,233,0.12)",
    },
    {
      label: "System Alerts",
      value: sysAlerts,
      tone: "text-red-400",
      icon: <AlertTriangle size={12} className="text-red-400" />,
      glow: "rgba(239,68,68,0.1)",
    },
    {
      label: "GPU Analytics",
      value: "Processing",
      tone: "text-teal-400",
      icon: <Cpu size={12} className="text-teal-400" />,
      glow: "rgba(20,184,166,0.12)",
    },
  ];

  return (
    <div
      className="rounded-xl px-4 py-3 flex items-center gap-4 flex-wrap"
      style={{
        background: "var(--dvas-surface)",
        border: "1px solid rgba(20,184,166,0.2)",
        boxShadow: "var(--dvas-shadow)",
      }}
    >
      {/* Branding */}
      <div className="flex flex-col shrink-0 mr-2">
        <div className="flex items-center gap-2">
          <div
            className="w-6 h-6 rounded-lg flex items-center justify-center shrink-0"
            style={{ background: "rgba(20,184,166,0.15)", border: "1px solid rgba(20,184,166,0.3)" }}
          >
            <Radio size={12} className="text-teal-400" />
          </div>
          <div className="text-[11px] font-bold text-teal-300 tracking-wide leading-tight">
            DVAS – Kerala Police
          </div>
        </div>
        <div className="mono text-[8px] text-slate-600 mt-0.5 pl-8">
          Central Command &amp; Intelligence · Multi-Drone View
        </div>
      </div>

      {/* Jurisdiction chip */}
      <div
        className="hidden md:flex items-center gap-1.5 px-3 py-1.5 rounded-lg shrink-0"
        style={{ background: "var(--dvas-surface-2)", border: "1px solid var(--dvas-border)" }}
      >
        <MapPin size={10} className="text-slate-500" />
        <span className="mono text-[9px] text-slate-400 font-medium">
          Jurisdiction: <span className="text-slate-100">Thiruvananthapuram</span>
        </span>
      </div>

      {/* WS indicator */}
      <div className="flex items-center gap-1.5 shrink-0">
        {connected ? (
          <Wifi size={11} className="text-emerald-400" />
        ) : (
          <WifiOff size={11} className="text-red-400" />
        )}
        <span
          className={`mono text-[9px] ${connected ? "text-emerald-400" : "text-red-400"}`}
        >
          {connected ? "WS LIVE" : "OFFLINE"}
        </span>
      </div>

      {/* Metric cards */}
      <div className="ml-auto flex items-center gap-2 flex-wrap">
        {metrics.map(({ label, value, tone, icon, glow }) => (
          <div
            key={label}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg shrink-0"
            style={{
              background: "var(--dvas-surface-2)",
              border: "1px solid var(--dvas-border)",
            }}
          >
            {icon}
            <div>
              <div className={`mono text-[12px] font-bold ${tone}`}>{value}</div>
              <div className="mono text-[8px] text-slate-600">{label}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────
   MAIN EXPORT
   ───────────────────────────────────────────── */
export default function MultiDroneView({ drones: liveDrones, alerts: liveAlerts, connected }) {
  // Merge live API drones with mock fallback (by ID)
  const drones = (liveDrones && liveDrones.length > 0 ? liveDrones : MOCK_DRONES).map(
    (d) => {
      const mock = MOCK_DRONES.find((m) => m.id === d.id) || {};
      return { ...mock, ...d };
    }
  );

  const events = liveAlerts && liveAlerts.length > 0
    ? liveAlerts.map((a) => ({
        id: a.id,
        ts: a.ts,
        drone_id: a.drone_id,
        mission_id: a.mission_id || "—",
        event_type: (a.type || "Unknown").replace(/_/g, " "),
        confidence: Math.round((a.confidence || 0) * 100) || 80,
        zone: a.zone || "—",
        severity: a.severity || "info",
      }))
    : MOCK_EVENTS;

  const [selectedDroneId, setSelectedDroneId] = useState(null);
  const [playingEvent, setPlayingEvent] = useState(null);

  // Clock tick for "X min ago" labels
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 30000);
    return () => clearInterval(id);
  }, []);

  return (
    <div
      className="flex flex-col gap-3 min-h-0"
      data-testid="multidrone-view"
      style={{ fontFamily: "'Plus Jakarta Sans', sans-serif" }}
    >
      {/* ── 1. Global Header ── */}
      <GlobalHeader drones={drones} alerts={liveAlerts} connected={connected} />

      {/* ── 2. Drone Cards Grid (full width) ── */}
      <div
        className={`grid gap-3 ${
          drones.length === 1
            ? "grid-cols-1"
            : drones.length === 2
            ? "grid-cols-2"
            : "grid-cols-1 md:grid-cols-2 lg:grid-cols-3"
        }`}
      >
        {drones.map((d) => (
          <DroneFeedCard
            key={d.id}
            drone={d}
            isSelected={selectedDroneId === d.id}
            onSelect={setSelectedDroneId}
          />
        ))}
      </div>

      {/* ── 3. Analytics Row (4 panels horizontal) ── */}
      <AnalyticsRow
        selectedDrone={selectedDroneId}
        drones={drones}
      />

      {/* ── 3. Event Timeline ── */}
      <EventTimeline events={events} onPlay={setPlayingEvent} />

      {/* ── Evidence Clip Modal ── */}
      {playingEvent && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-6"
          style={{ background: "rgba(0,0,0,0.85)", backdropFilter: "blur(6px)" }}
          onClick={() => setPlayingEvent(null)}
        >
          <div
            className="relative w-full max-w-xl rounded-2xl overflow-hidden"
            style={{
              background: "var(--dvas-surface)",
              border: "1px solid rgba(20,184,166,0.25)",
              boxShadow: "0 0 0 1px rgba(20,184,166,0.1), 0 24px 64px rgba(0,0,0,0.6)",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            {/* Modal header */}
            <div
              className="px-4 py-3 flex items-center gap-3"
              style={{ borderBottom: "1px solid var(--dvas-border)" }}
            >
              <Play size={14} className="text-teal-400" />
              <div className="flex-1 min-w-0">
                <div className="text-sm font-bold text-teal-300 truncate">
                  {playingEvent.event_type}
                </div>
                <div className="mono text-[10px] text-slate-500 mt-0.5">
                  {playingEvent.drone_id} · {playingEvent.zone} · {fmtAge(playingEvent.ts)}
                </div>
              </div>
              <button
                onClick={() => setPlayingEvent(null)}
                className="w-7 h-7 rounded-lg flex items-center justify-center transition-colors hover:bg-slate-800"
                style={{ color: "var(--dvas-text-muted)" }}
                data-testid="close-evidence-modal"
              >
                <X size={14} />
              </button>
            </div>

            {/* Video placeholder */}
            <div
              className="aspect-video flex flex-col items-center justify-center"
              style={{ background: "#040c18" }}
            >
              <div
                className="w-14 h-14 rounded-full flex items-center justify-center mb-3"
                style={{
                  background: "rgba(20,184,166,0.12)",
                  border: "1px solid rgba(20,184,166,0.25)",
                }}
              >
                <Play size={24} className="text-teal-400 ml-1" />
              </div>
              <div className="mono text-[11px] text-slate-500">Evidence Clip · {playingEvent.drone_id}</div>
              <div className="mono text-[9px] text-slate-700 mt-1">
                Video stream from backend not available in demo
              </div>
            </div>

            {/* Metadata row */}
            <div
              className="px-4 py-2.5 flex items-center gap-4 flex-wrap"
              style={{ borderTop: "1px solid var(--dvas-border)" }}
            >
              <span className="mono text-[10px] text-slate-500">
                Confidence:{" "}
                <span className="text-emerald-400 font-bold">{playingEvent.confidence}%</span>
              </span>
              <span className="mono text-[10px] text-slate-500">
                Mission: <span className="text-slate-300">{playingEvent.mission_id}</span>
              </span>
              <span className="mono text-[10px] text-slate-500">
                Severity:{" "}
                <span
                  className={
                    playingEvent.severity === "critical"
                      ? "text-red-400"
                      : playingEvent.severity === "warning"
                      ? "text-amber-400"
                      : "text-sky-400"
                  }
                >
                  {playingEvent.severity}
                </span>
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
