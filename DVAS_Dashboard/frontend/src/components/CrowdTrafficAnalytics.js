/**
 * CrowdTrafficAnalytics.js
 * ─────────────────────────────────────────────────────────
 * Rich analytics dashboard for the "Crowd / Traffic" tab
 * DVAS – Kerala Police | Central Command & Intelligence
 * ─────────────────────────────────────────────────────────
 * Sections:
 *   1. Vehicle Tracking & Flow  (ledger + donut + time-series)
 *   2. Crowd Density & Zone Counting  (zone bars + threshold alerts)
 *   3. ANPR Live Plate Ticker
 *   4. Face Detection & De-duplication Metrics
 */

import { useState, useEffect, useRef } from "react";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, BarChart, Bar, RadarChart, PolarGrid,
  PolarAngleAxis, Radar, Cell, PieChart, Pie, Legend,
} from "recharts";
import {
  Car, Users, Camera, ShieldAlert, Activity, Eye,
  AlertTriangle, CheckCircle, Clock, Radio, Hash,
  TrendingUp, RefreshCw, ChevronRight,
} from "lucide-react";

/* ─────────────────────────────────────────────
   MOCK / FALLBACK DATA
   ───────────────────────────────────────────── */
const NOW = Date.now();

const MOCK_CROWD_SERIES = Array.from({ length: 24 }, (_, i) => ({
  hour: `${String(i).padStart(2, "0")}:00`,
  density: Math.round(80 + Math.sin((i / 24) * Math.PI * 2) * 60 + Math.random() * 30),
  vehicles: Math.round(40 + Math.sin((i / 24) * Math.PI * 2) * 30 + Math.random() * 20),
}));

const MOCK_ZONES = [
  { zone: "North Gate",    crowd: 312, threshold: 300, vehicles: 88  },
  { zone: "Beach Sector A", crowd: 187, threshold: 250, vehicles: 42 },
  { zone: "Secretariat Rd", crowd: 94,  threshold: 200, vehicles: 61 },
  { zone: "MG Road",        crowd: 256, threshold: 280, vehicles: 130},
  { zone: "East Fort",      crowd: 143, threshold: 200, vehicles: 57 },
  { zone: "Palayam",        crowd: 78,  threshold: 150, vehicles: 29 },
];

const MOCK_VEHICLE_TYPES = [
  { name: "Two-Wheelers", value: 214, color: "#818cf8" },
  { name: "Cars",         value: 156, color: "#14b8a6" },
  { name: "Heavy",        value:  38, color: "#f59e0b" },
];

const MOCK_TRACKING_LEDGER = [
  { id: "VEH-0041", type: "Car",         duration: "14m 32s", zone: "MG Road",        last_seen: new Date(NOW - 87000).toISOString() },
  { id: "VEH-0089", type: "Two-Wheeler", duration: "6m 12s",  zone: "North Gate",     last_seen: new Date(NOW - 22000).toISOString() },
  { id: "VEH-0113", type: "Heavy",       duration: "31m 05s", zone: "Secretariat Rd", last_seen: new Date(NOW - 145000).toISOString() },
  { id: "VEH-0057", type: "Car",         duration: "2m 48s",  zone: "Beach Sector A", last_seen: new Date(NOW - 8000).toISOString() },
  { id: "VEH-0022", type: "Two-Wheeler", duration: "9m 17s",  zone: "East Fort",      last_seen: new Date(NOW - 54000).toISOString() },
  { id: "VEH-0098", type: "Car",         duration: "22m 11s", zone: "Palayam",        last_seen: new Date(NOW - 200000).toISOString() },
];

const MOCK_PLATES = [
  { plate: "KL 01 AB 4521", ts: new Date(NOW - 12000).toISOString(),  drone: "DRN-001", zone: "MG Road"     },
  { plate: "KL 04 CD 8877", ts: new Date(NOW - 34000).toISOString(),  drone: "DRN-002", zone: "North Gate"  },
  { plate: "KL 11 EF 2210", ts: new Date(NOW - 71000).toISOString(),  drone: "DRN-001", zone: "Beach Sector A" },
  { plate: "TN 09 GH 3301", ts: new Date(NOW - 98000).toISOString(),  drone: "DRN-003", zone: "East Fort"   },
  { plate: "KL 02 JK 7654", ts: new Date(NOW - 143000).toISOString(), drone: "DRN-002", zone: "Secretariat Rd" },
  { plate: "KL 07 LM 5509", ts: new Date(NOW - 189000).toISOString(), drone: "DRN-001", zone: "Palayam"     },
  { plate: "KA 03 NP 0012", ts: new Date(NOW - 231000).toISOString(), drone: "DRN-003", zone: "MG Road"     },
  { plate: "KL 10 QR 9988", ts: new Date(NOW - 284000).toISOString(), drone: "DRN-001", zone: "North Gate"  },
];

/* ─────────────────────────────────────────────
   SHARED HELPERS
   ───────────────────────────────────────────── */
function fmtAge(isoTs) {
  const s = Math.floor((Date.now() - new Date(isoTs)) / 1000);
  if (s < 60)   return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

const CHART_TOOLTIP = {
  contentStyle: {
    background: "var(--dvas-surface)",
    border: "1px solid var(--dvas-border)",
    fontSize: 12,
    color: "var(--dvas-text)",
    borderRadius: 6,
  },
  itemStyle: { color: "var(--dvas-text)" },
};

/** Reusable panel card */
function Panel({ title, icon: Icon, iconClass = "text-teal-400", children, right, badge }) {
  return (
    <div
      className="rounded-xl flex flex-col"
      style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}
    >
      <div
        className="px-4 py-2.5 flex items-center gap-2 shrink-0"
        style={{ borderBottom: "1px solid var(--dvas-border)" }}
      >
        <Icon size={14} className={iconClass} />
        <span className="dvas-label flex-1">{title}</span>
        {badge && (
          <span className="mono text-[10px] px-1.5 py-0.5 rounded font-bold"
            style={{ background: "rgba(239,68,68,0.12)", color: "#f87171", border: "1px solid rgba(239,68,68,0.2)" }}>
            {badge}
          </span>
        )}
        {right}
      </div>
      <div className="flex-1 min-h-0 p-3">{children}</div>
    </div>
  );
}

/* ─────────────────────────────────────────────
   SECTION 1A — Vehicle Time-Series
   ───────────────────────────────────────────── */
function VehicleTimeSeries({ series }) {
  return (
    <ResponsiveContainer width="100%" height={180}>
      <AreaChart data={series} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
        <defs>
          <linearGradient id="gVeh" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"   stopColor="#818cf8" stopOpacity={0.5} />
            <stop offset="100%" stopColor="#818cf8" stopOpacity={0}   />
          </linearGradient>
          <linearGradient id="gCar" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"   stopColor="#14b8a6" stopOpacity={0.4} />
            <stop offset="100%" stopColor="#14b8a6" stopOpacity={0}   />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="var(--dvas-border)" strokeDasharray="3 3" />
        <XAxis dataKey="hour" stroke="var(--dvas-text-muted)" fontSize={9} interval={3} />
        <YAxis stroke="var(--dvas-text-muted)" fontSize={9} />
        <Tooltip {...CHART_TOOLTIP} />
        <Area dataKey="vehicles" stroke="#818cf8" fill="url(#gVeh)" strokeWidth={1.5} name="Vehicles" dot={false} />
        <Area dataKey="density"  stroke="#14b8a6" fill="url(#gCar)" strokeWidth={1.5} name="Crowd"    dot={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

/* ─────────────────────────────────────────────
   SECTION 1B — Vehicle Classification Donut
   ───────────────────────────────────────────── */
function VehicleDonut({ types }) {
  const total = types.reduce((s, t) => s + t.value, 0);
  return (
    <div className="flex flex-col items-center gap-2">
      <PieChart width={160} height={140}>
        <Pie
          data={types}
          cx={80}
          cy={65}
          innerRadius={42}
          outerRadius={64}
          paddingAngle={3}
          dataKey="value"
          strokeWidth={0}
        >
          {types.map((entry) => (
            <Cell key={entry.name} fill={entry.color} />
          ))}
        </Pie>
        <Tooltip {...CHART_TOOLTIP} />
      </PieChart>
      <div className="w-full space-y-1">
        {types.map((t) => (
          <div key={t.name} className="flex items-center justify-between">
            <div className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full shrink-0" style={{ background: t.color }} />
              <span className="mono text-xs" style={{ color: "var(--dvas-text-muted)" }}>{t.name}</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="mono font-bold text-sm" style={{ color: "var(--dvas-text)" }}>{t.value}</span>
              <span className="mono text-[10px]" style={{ color: "var(--dvas-text-dim)" }}>
                {Math.round((t.value / total) * 100)}%
              </span>
            </div>
          </div>
        ))}
        <div className="pt-1" style={{ borderTop: "1px solid var(--dvas-border)" }}>
          <div className="flex justify-between">
            <span className="mono text-xs" style={{ color: "var(--dvas-text-muted)" }}>Total Active</span>
            <span className="mono font-bold text-sm text-teal-400">{total}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────
   SECTION 1C — Active Tracking Ledger
   ───────────────────────────────────────────── */
function TrackingLedger({ data }) {
  const typeColor = { Car: "#14b8a6", "Two-Wheeler": "#818cf8", Heavy: "#f59e0b" };
  return (
    <div className="overflow-auto" style={{ maxHeight: 200 }}>
      <table className="w-full text-xs">
        <thead>
          <tr style={{ borderBottom: "1px solid var(--dvas-border)" }}>
            {["Vehicle ID", "Type", "Active Duration", "Last Zone", "Last Seen"].map((h) => (
              <th key={h} className="text-left px-2 py-1.5 dvas-label" style={{ fontSize: 10 }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="mono">
          {data.map((row) => (
            <tr key={row.id} className="hover:bg-slate-800/20 transition-colors"
              style={{ borderBottom: "1px solid var(--dvas-border)" }}>
              <td className="px-2 py-1.5 font-bold" style={{ color: "var(--dvas-text)" }}>{row.id}</td>
              <td className="px-2 py-1.5">
                <span className="px-1.5 py-0.5 rounded text-[10px] font-bold"
                  style={{ background: `${typeColor[row.type]}1a`, color: typeColor[row.type], border: `1px solid ${typeColor[row.type]}33` }}>
                  {row.type}
                </span>
              </td>
              <td className="px-2 py-1.5 text-teal-400">{row.duration}</td>
              <td className="px-2 py-1.5" style={{ color: "var(--dvas-text-muted)" }}>{row.zone}</td>
              <td className="px-2 py-1.5" style={{ color: "var(--dvas-text-dim)" }}>{fmtAge(row.last_seen)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ─────────────────────────────────────────────
   SECTION 2A — Zone Density Bar Chart
   ───────────────────────────────────────────── */
function ZoneDensityChart({ zones }) {
  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={zones} margin={{ top: 4, right: 8, left: -20, bottom: 30 }}>
        <CartesianGrid stroke="var(--dvas-border)" strokeDasharray="3 3" />
        <XAxis
          dataKey="zone"
          stroke="var(--dvas-text-muted)"
          fontSize={9}
          angle={-30}
          textAnchor="end"
          interval={0}
        />
        <YAxis stroke="var(--dvas-text-muted)" fontSize={9} />
        <Tooltip {...CHART_TOOLTIP} />
        <Bar dataKey="crowd" name="Crowd" radius={[3, 3, 0, 0]}>
          {zones.map((z) => (
            <Cell key={z.zone} fill={z.crowd >= z.threshold ? "#ef4444" : "#14b8a6"} />
          ))}
        </Bar>
        <Bar dataKey="vehicles" name="Vehicles" fill="#818cf8" radius={[3, 3, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

/* ─────────────────────────────────────────────
   SECTION 2B — Threshold Alert Panel
   ───────────────────────────────────────────── */
function ThresholdAlerts({ zones }) {
  const breached = zones.filter((z) => z.crowd >= z.threshold);
  const warning  = zones.filter((z) => z.crowd >= z.threshold * 0.85 && z.crowd < z.threshold);

  if (breached.length === 0 && warning.length === 0) {
    return (
      <div className="flex items-center gap-2 px-3 py-2.5 rounded-lg"
        style={{ background: "rgba(16,185,129,0.08)", border: "1px solid rgba(16,185,129,0.2)" }}>
        <CheckCircle size={14} className="text-emerald-400" />
        <span className="text-xs text-emerald-400 font-medium">All zones within safe density thresholds</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1.5">
      {breached.map((z) => (
        <div key={z.zone}
          className="flex items-center gap-2 px-3 py-2 rounded-lg"
          style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)" }}>
          <AlertTriangle size={13} className="text-red-400 shrink-0 dvas-live-dot" />
          <div className="flex-1 min-w-0">
            <span className="mono text-xs text-red-300 font-bold">{z.zone}</span>
            <span className="mono text-[10px] text-red-400/70 ml-2">
              {z.crowd} / {z.threshold} — {Math.round((z.crowd / z.threshold) * 100)}% of threshold
            </span>
          </div>
          <span className="mono text-[10px] font-bold px-1.5 py-0.5 rounded"
            style={{ background: "rgba(239,68,68,0.15)", color: "#f87171" }}>BREACH</span>
        </div>
      ))}
      {warning.map((z) => (
        <div key={z.zone}
          className="flex items-center gap-2 px-3 py-2 rounded-lg"
          style={{ background: "rgba(245,158,11,0.07)", border: "1px solid rgba(245,158,11,0.18)" }}>
          <AlertTriangle size={13} className="text-amber-400 shrink-0" />
          <div className="flex-1 min-w-0">
            <span className="mono text-xs text-amber-300 font-bold">{z.zone}</span>
            <span className="mono text-[10px] text-amber-400/70 ml-2">
              {z.crowd} / {z.threshold} — approaching limit
            </span>
          </div>
          <span className="mono text-[10px] font-bold px-1.5 py-0.5 rounded"
            style={{ background: "rgba(245,158,11,0.12)", color: "#fbbf24" }}>WARNING</span>
        </div>
      ))}
    </div>
  );
}

/* ─────────────────────────────────────────────
   SECTION 3 — ANPR Plate Ticker
   ───────────────────────────────────────────── */
function AnprTicker({ plates }) {
  const [page, setPage] = useState(0);
  const perPage = 5;
  const pages = Math.ceil(plates.length / perPage);
  const shown = plates.slice(page * perPage, page * perPage + perPage);

  return (
    <div className="flex flex-col gap-2">
      <div className="overflow-auto">
        <table className="w-full text-xs">
          <thead>
            <tr style={{ borderBottom: "1px solid var(--dvas-border)" }}>
              {["Plate", "Drone", "Zone", "Detected"].map((h) => (
                <th key={h} className="text-left px-2 py-1.5 dvas-label" style={{ fontSize: 10 }}>{h}</th>
              ))}
              <th className="px-2 py-1.5 dvas-label text-right" style={{ fontSize: 10 }}>Frame</th>
            </tr>
          </thead>
          <tbody className="mono">
            {shown.map((p, i) => (
              <tr key={i}
                className="hover:bg-slate-800/20 transition-colors"
                style={{ borderBottom: "1px solid var(--dvas-border)" }}>
                <td className="px-2 py-2">
                  <span className="mono font-bold text-sm tracking-widest px-2 py-0.5 rounded"
                    style={{ background: "rgba(20,184,166,0.1)", color: "#2dd4bf", border: "1px solid rgba(20,184,166,0.2)", letterSpacing: "0.1em" }}>
                    {p.plate}
                  </span>
                </td>
                <td className="px-2 py-2">
                  <span className="text-[10px] px-1.5 py-0.5 rounded font-bold"
                    style={{ background: "rgba(129,140,248,0.12)", color: "#818cf8", border: "1px solid rgba(129,140,248,0.2)" }}>
                    {p.drone}
                  </span>
                </td>
                <td className="px-2 py-2" style={{ color: "var(--dvas-text-muted)" }}>{p.zone}</td>
                <td className="px-2 py-2" style={{ color: "var(--dvas-text-dim)" }}>{fmtAge(p.ts)}</td>
                <td className="px-2 py-2 text-right">
                  <button
                    className="mono text-[10px] px-2 py-1 rounded transition-colors hover:opacity-80"
                    style={{ background: "rgba(14,165,233,0.1)", color: "#38bdf8", border: "1px solid rgba(14,165,233,0.2)" }}
                  >
                    View
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="flex items-center justify-between">
          <span className="mono text-[10px]" style={{ color: "var(--dvas-text-dim)" }}>
            {page * perPage + 1}–{Math.min((page + 1) * perPage, plates.length)} of {plates.length} detections
          </span>
          <div className="flex gap-1">
            {Array.from({ length: pages }).map((_, i) => (
              <button
                key={i}
                onClick={() => setPage(i)}
                className="w-5 h-5 rounded text-[10px] mono font-bold transition-colors"
                style={{
                  background: i === page ? "rgba(20,184,166,0.15)" : "var(--dvas-surface-2)",
                  color: i === page ? "#2dd4bf" : "var(--dvas-text-muted)",
                  border: `1px solid ${i === page ? "rgba(20,184,166,0.3)" : "var(--dvas-border)"}`,
                }}
              >
                {i + 1}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ─────────────────────────────────────────────
   SECTION 4 — Face De-duplication Metrics
   ───────────────────────────────────────────── */
function FaceMetrics({ drones }) {
  // Compute realistic mock metrics from drone count
  const droneCount = drones?.length || 3;
  const totalDetections  = 1847 + droneCount * 43;
  const uniqueIndexed    = 614  + droneCount * 12;
  const dedupRate        = Math.round((1 - uniqueIndexed / totalDetections) * 100);
  const reIdCount        = 89   + droneCount * 7;
  const crossDroneReId   = 34   + droneCount * 3;

  const stats = [
    {
      label:   "Total Face Detections",
      value:   totalDetections.toLocaleString(),
      sub:     "Raw detections across all drones",
      color:   "text-sky-400",
      bg:      "rgba(14,165,233,0.08)",
      border:  "rgba(14,165,233,0.18)",
      icon:    Camera,
    },
    {
      label:   "Unique Faces Indexed",
      value:   uniqueIndexed.toLocaleString(),
      sub:     `${dedupRate}% de-duplication efficiency`,
      color:   "text-teal-400",
      bg:      "rgba(20,184,166,0.08)",
      border:  "rgba(20,184,166,0.18)",
      icon:    Eye,
    },
    {
      label:   "Re-ID Events (Single Drone)",
      value:   reIdCount.toLocaleString(),
      sub:     "Re-entries detected within same feed",
      color:   "text-violet-400",
      bg:      "rgba(139,92,246,0.08)",
      border:  "rgba(139,92,246,0.18)",
      icon:    RefreshCw,
    },
    {
      label:   "Cross-Drone Re-ID",
      value:   crossDroneReId.toLocaleString(),
      sub:     "Person matched across different drones",
      color:   "text-amber-400",
      bg:      "rgba(245,158,11,0.08)",
      border:  "rgba(245,158,11,0.18)",
      icon:    Radio,
    },
  ];

  /* De-duplication efficiency bar */
  const efficiencyData = [
    { label: "Duplicates Filtered", pct: dedupRate,      color: "#14b8a6" },
    { label: "Unique Retained",     pct: 100 - dedupRate, color: "#818cf8" },
  ];

  return (
    <div className="flex flex-col gap-3">
      {/* Privacy note */}
      <div className="flex items-start gap-2 px-3 py-2 rounded-lg"
        style={{ background: "rgba(245,158,11,0.06)", border: "1px solid rgba(245,158,11,0.15)" }}>
        <ShieldAlert size={12} className="text-amber-400 mt-0.5 shrink-0" />
        <span className="text-[11px] leading-relaxed" style={{ color: "var(--dvas-text-muted)" }}>
          <strong className="text-amber-400">Privacy Safeguard:</strong> Metrics reflect de-duplication efficiency only.
          No autonomous identity confirmation is performed. All data subject to ABAC controls.
        </span>
      </div>

      {/* 4 metric cards */}
      <div className="grid grid-cols-2 gap-2">
        {stats.map(({ label, value, sub, color, bg, border, icon: Icon }) => (
          <div key={label}
            className="rounded-lg px-3 py-2.5 flex flex-col gap-1"
            style={{ background: bg, border: `1px solid ${border}` }}>
            <div className="flex items-center gap-1.5">
              <Icon size={12} className={color} />
              <span className="dvas-label" style={{ fontSize: 9 }}>{label}</span>
            </div>
            <div className={`mono font-bold text-2xl ${color}`}>{value}</div>
            <div className="mono text-[10px]" style={{ color: "var(--dvas-text-dim)" }}>{sub}</div>
          </div>
        ))}
      </div>

      {/* Efficiency breakdown bars */}
      <div className="flex flex-col gap-2">
        <div className="dvas-label" style={{ fontSize: 10 }}>De-duplication Breakdown</div>
        {efficiencyData.map(({ label, pct, color }) => (
          <div key={label} className="flex flex-col gap-0.5">
            <div className="flex justify-between items-center">
              <span className="mono text-xs" style={{ color: "var(--dvas-text-muted)" }}>{label}</span>
              <span className="mono font-bold text-sm" style={{ color }}>{pct}%</span>
            </div>
            <div className="h-2 rounded-full overflow-hidden" style={{ background: "var(--dvas-elev)" }}>
              <div className="h-full rounded-full transition-all duration-700"
                style={{ width: `${pct}%`, background: color }} />
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
export default function CrowdTrafficAnalytics({ crowdSeries: liveSeries, drones }) {
  const series = (liveSeries && liveSeries.length > 0) ? liveSeries : MOCK_CROWD_SERIES;
  const [, setTick] = useState(0);

  // Refresh "X ago" labels every 30s
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 30000);
    return () => clearInterval(id);
  }, []);

  const breachedCount = MOCK_ZONES.filter((z) => z.crowd >= z.threshold).length;

  return (
    <div className="flex flex-col gap-4" style={{ fontFamily: "'Plus Jakarta Sans', sans-serif" }}>

      {/* ════════ SECTION 1: Vehicle Tracking & Flow ════════ */}
      <div>
        <div className="flex items-center gap-2 mb-2">
          <Car size={14} className="text-violet-400" />
          <span className="dvas-label text-violet-400">Vehicle Tracking & Flow</span>
        </div>
        <div className="grid grid-cols-12 gap-3">

          {/* Time-series chart */}
          <div className="col-span-12 lg:col-span-6">
            <Panel title="Crowd & Vehicle — 24h Time Series" icon={Activity} iconClass="text-teal-400">
              <VehicleTimeSeries series={series} />
              <div className="flex gap-4 mt-1">
                {[{ label: "Vehicles", color: "#818cf8" }, { label: "Crowd Density", color: "#14b8a6" }].map(({ label, color }) => (
                  <div key={label} className="flex items-center gap-1.5">
                    <span className="w-3 h-1.5 rounded-full" style={{ background: color }} />
                    <span className="mono text-[10px]" style={{ color: "var(--dvas-text-muted)" }}>{label}</span>
                  </div>
                ))}
              </div>
            </Panel>
          </div>

          {/* Donut chart */}
          <div className="col-span-12 lg:col-span-3">
            <Panel title="Vehicle Classification" icon={Car} iconClass="text-violet-400">
              <VehicleDonut types={MOCK_VEHICLE_TYPES} />
            </Panel>
          </div>

          {/* Active tracking ledger */}
          <div className="col-span-12 lg:col-span-3">
            <Panel title="Active Tracking Ledger" icon={Hash} iconClass="text-sky-400"
              right={
                <span className="mono text-[10px] px-1.5 py-0.5 rounded font-bold"
                  style={{ background: "rgba(20,184,166,0.1)", color: "#2dd4bf", border: "1px solid rgba(20,184,166,0.2)" }}>
                  {MOCK_TRACKING_LEDGER.length} ACTIVE
                </span>
              }
            >
              <TrackingLedger data={MOCK_TRACKING_LEDGER} />
            </Panel>
          </div>
        </div>
      </div>

      {/* ════════ SECTION 2: Crowd Density & Zone Counting ════════ */}
      <div>
        <div className="flex items-center gap-2 mb-2">
          <Users size={14} className="text-sky-400" />
          <span className="dvas-label text-sky-400">Crowd Density & Zone Counting</span>
        </div>
        <div className="grid grid-cols-12 gap-3">

          {/* Zone bar chart */}
          <div className="col-span-12 lg:col-span-8">
            <Panel title="Zone-Based Crowd & Vehicle Distribution" icon={TrendingUp} iconClass="text-sky-400">
              <ZoneDensityChart zones={MOCK_ZONES} />
              <div className="flex gap-4 mt-1">
                <div className="flex items-center gap-1.5">
                  <span className="w-3 h-1.5 rounded-full bg-teal-400" />
                  <span className="mono text-[10px]" style={{ color: "var(--dvas-text-muted)" }}>Crowd (safe)</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="w-3 h-1.5 rounded-full bg-red-400" />
                  <span className="mono text-[10px]" style={{ color: "var(--dvas-text-muted)" }}>Crowd (breach)</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="w-3 h-1.5 rounded-full bg-violet-400" />
                  <span className="mono text-[10px]" style={{ color: "var(--dvas-text-muted)" }}>Vehicles</span>
                </div>
              </div>
            </Panel>
          </div>

          {/* Threshold alerts */}
          <div className="col-span-12 lg:col-span-4">
            <Panel
              title="Density Threshold Alerts"
              icon={ShieldAlert}
              iconClass="text-red-400"
              badge={breachedCount > 0 ? `${breachedCount} BREACHED` : undefined}
            >
              <ThresholdAlerts zones={MOCK_ZONES} />
              <div className="mt-3 flex flex-col gap-1">
                <span className="dvas-label" style={{ fontSize: 9 }}>Zone Threshold Configuration</span>
                {MOCK_ZONES.map((z) => (
                  <div key={z.zone} className="flex items-center gap-2">
                    <span className="mono text-[10px] flex-1 truncate" style={{ color: "var(--dvas-text-muted)" }}>{z.zone}</span>
                    <div className="h-1 rounded-full flex-1 overflow-hidden" style={{ background: "var(--dvas-elev)", maxWidth: 80 }}>
                      <div className="h-full rounded-full"
                        style={{
                          width: `${Math.min(100, (z.crowd / z.threshold) * 100)}%`,
                          background: z.crowd >= z.threshold ? "#ef4444" : z.crowd >= z.threshold * 0.85 ? "#f59e0b" : "#14b8a6",
                        }} />
                    </div>
                    <span className="mono text-[10px] shrink-0" style={{ color: "var(--dvas-text-dim)" }}>{z.threshold}</span>
                  </div>
                ))}
              </div>
            </Panel>
          </div>
        </div>
      </div>

      {/* ════════ SECTION 3: ANPR Live Plate Ticker ════════ */}
      <div>
        <div className="flex items-center gap-2 mb-2">
          <Hash size={14} className="text-amber-400" />
          <span className="dvas-label text-amber-400">ANPR — Live Plate Detection Feed</span>
        </div>
        <Panel
          title="License Plate Detection Log"
          icon={Hash}
          iconClass="text-amber-400"
          right={
            <div className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 dvas-live-dot" />
              <span className="mono text-[10px] text-amber-400">{MOCK_PLATES.length} plates · last 2h</span>
            </div>
          }
        >
          <AnprTicker plates={MOCK_PLATES} />
        </Panel>
      </div>

      {/* ════════ SECTION 4: Face Detection & De-duplication ════════ */}
      <div>
        <div className="flex items-center gap-2 mb-2">
          <Eye size={14} className="text-emerald-400" />
          <span className="dvas-label text-emerald-400">Face Detection & De-duplication (Privacy-Preserving)</span>
        </div>
        <Panel title="De-duplication Engine Metrics" icon={Eye} iconClass="text-emerald-400">
          <FaceMetrics drones={drones} />
        </Panel>
      </div>

    </div>
  );
}
