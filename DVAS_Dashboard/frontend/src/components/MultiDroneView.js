/**
 * MultiDroneView.js
 * ─────────────────────────────────────────────────────────
 * High-fidelity Multi-Drone Situational Command View
 * DVAS – Kerala Police | Central Command & Intelligence
 * ─────────────────────────────────────────────────────────
 */

import React, { useEffect, useRef, useState, useCallback } from "react";
import { API } from "@/lib/api";
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
  Camera,
  ShieldCheck,
  User as UserIcon,
  RefreshCw,
} from "lucide-react";

import { useActiveStreams } from "@/hooks/useActiveStreams";

/* ─────────────────────────────────────────────
   STATIC / FALLBACK DATA
   (merged with live API props)
   ───────────────────────────────────────────── */
/* ─────────────────────────────────────────────
   HELPERS
   ───────────────────────────────────────────── */

const MOCK_EVENTS = [
  {
    id: "evt-001",
    ts: new Date(Date.now() - 4 * 60000).toISOString(),
    drone_id: "DRN-002",
    mission_id: "M-104",
    event_type: "Perimeter Breach",
    confidence: 94,
    zone: "Sector-7 North",
    severity: "critical",
  },
  {
    id: "evt-002",
    ts: new Date(Date.now() - 11 * 60000).toISOString(),
    drone_id: "DRN-001",
    mission_id: "M-102",
    event_type: "Crowd Surge",
    confidence: 88,
    zone: "Statue Junction",
    severity: "warning",
  },
  {
    id: "evt-003",
    ts: new Date(Date.now() - 23 * 60000).toISOString(),
    drone_id: "DRN-002",
    mission_id: "M-104",
    event_type: "Suspicious Object",
    confidence: 77,
    zone: "Secretariat Rd",
    severity: "warning",
  },
  {
    id: "evt-004",
    ts: new Date(Date.now() - 38 * 60000).toISOString(),
    drone_id: "DRN-001",
    mission_id: "M-102",
    event_type: "Vehicle Violation",
    confidence: 91,
    zone: "MG Road",
    severity: "info",
  },
  {
    id: "evt-005",
    ts: new Date(Date.now() - 61 * 60000).toISOString(),
    drone_id: "DRN-003",
    mission_id: "M-106",
    event_type: "Loitering Detected",
    confidence: 82,
    zone: "East Fort",
    severity: "info",
  },
  {
    id: "evt-006",
    ts: new Date(Date.now() - 78 * 60000).toISOString(),
    drone_id: "DRN-002",
    mission_id: "M-104",
    event_type: "Intrusion Detection",
    confidence: 96,
    zone: "VJT Hall",
    severity: "critical",
  },
];

const MOCK_DRONES = [
  {
    id: "DRN-001",
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
    id: "DRN-002",
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
    id: "DRN-003",
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
   Uses fetch-based JPEG polling (same auth pattern as TrafficAnalytics)
   so live frames load reliably with or without cookie support.
   ───────────────────────────────────────────── */

const FEED_BACKOFF = [500, 1000, 2000, 4000, 8000, 16000, 30000];
const FEED_WATCHDOG_MS = 6000;

function useDroneFeed(droneId, disabled, intervalMs = 700) {
  const [blobUrl,  setBlobUrl]  = useState(null);
  const [hasFrame, setHasFrame] = useState(false);
  const [feedDead, setFeedDead] = useState(false);
  const timerRef    = useRef(null);
  const watchRef    = useRef(null);
  const errRef      = useRef(0);
  const prevRef     = useRef(null);
  const mountedRef  = useRef(true);

  useEffect(() => { mountedRef.current = true; return () => { mountedRef.current = false; clearTimeout(timerRef.current); clearTimeout(watchRef.current); if (prevRef.current) URL.revokeObjectURL(prevRef.current); }; }, []);

  useEffect(() => {
    clearTimeout(timerRef.current); clearTimeout(watchRef.current);
    if (prevRef.current) { URL.revokeObjectURL(prevRef.current); prevRef.current = null; }
    errRef.current = 0; setBlobUrl(null); setHasFrame(false); setFeedDead(!!disabled);
    if (disabled) return;

    const schedule = (d) => { if (mountedRef.current) timerRef.current = setTimeout(doFetch, d); };
    const doFetch = async () => {
      if (!mountedRef.current) return;
      const token = localStorage.getItem("dvas_token") || "";
      const url = `${API}/ingest-server/frame/${encodeURIComponent(droneId)}/latest?_t=${Date.now()}`;
      try {
        const res = await fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {}, credentials: "include" });
        if (!mountedRef.current) return;
        if (res.ok) {
          const next = URL.createObjectURL(await res.blob());
          setBlobUrl((prev) => {
            if (prev) URL.revokeObjectURL(prev);
            return next;
          });
          setHasFrame(true); setFeedDead(false); errRef.current = 0;
          clearTimeout(watchRef.current);
          watchRef.current = setTimeout(() => { if (mountedRef.current) setFeedDead(true); }, FEED_WATCHDOG_MS);
          schedule(intervalMs);
        } else { throw new Error(`HTTP ${res.status}`); }
      } catch {
        if (!mountedRef.current) return;
        errRef.current++; if (errRef.current > 6) setFeedDead(true);
        schedule(FEED_BACKOFF[Math.min(errRef.current - 1, FEED_BACKOFF.length - 1)]);
      }
    };
    doFetch();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [droneId, disabled, intervalMs]);

  return { blobUrl, hasFrame, feedDead };
}

function DroneFeedCard({ drone, isSelected, hasSelection, onSelect }) {
  const isDegraded = drone.status === "degraded";
  const isOffline  = drone.status === "offline";
  const isOtherSelected = hasSelection && !isSelected;

  const { blobUrl, hasFrame, feedDead } = useDroneFeed(drone.id, isOffline);

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

  const focusClass = isOtherSelected
    ? "opacity-40 blur-[2px] scale-[0.98] hover:opacity-80 hover:blur-none"
    : isSelected
    ? "scale-100 ring-1 ring-teal-500/50"
    : "hover:scale-[1.012] hover:border-teal-500/40";

  const missionLabel  = drone.mission_name  || (drone.mission_id ? `Mission ${drone.mission_id}` : null);
  const operatorLabel = drone.operator_name || null;

  return (
    <div
      className={`relative flex flex-col rounded-xl border ${borderColor} overflow-hidden cursor-pointer transition-all duration-500 ease-in-out ${focusClass} h-full`}
      style={{ background: "var(--dvas-surface)", ...glowStyle }}
      onClick={() => onSelect(isSelected ? null : drone.id)}
      data-testid={`multidrone-card-${drone.id}`}
    >
      {/* ── Video Viewport ── */}
      <div className="relative aspect-video bg-[#060e1a] overflow-hidden flex-shrink-0">
        {/* scan-line animation */}
        <div
          className="absolute inset-0 pointer-events-none"
          style={{ backgroundImage: "repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(20,184,166,0.015) 2px, rgba(20,184,166,0.015) 4px)" }}
        />

        {/* Corner brackets */}
        {[
          "top-2 left-2 border-l-2 border-t-2",
          "top-2 right-2 border-r-2 border-t-2",
          "bottom-2 left-2 border-l-2 border-b-2",
          "bottom-2 right-2 border-r-2 border-b-2",
        ].map((cls, i) => (
          <div key={i} className={`absolute w-4 h-4 ${cls} ${isDegraded ? "border-amber-400/60" : isOffline ? "border-red-400/60" : "border-teal-400/60"}`} />
        ))}

        {/* Live JPEG feed or placeholder */}
        {!isOffline && hasFrame && blobUrl ? (
          <img
            src={blobUrl}
            alt={`Live feed ${drone.id}`}
            className="absolute inset-0 w-full h-full object-cover"
            style={{ opacity: isDegraded ? 0.8 : 0.95 }}
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center">
            {isOffline || feedDead ? (
              <div className="flex flex-col items-center gap-2">
                <WifiOff size={22} className="text-red-400/60" />
                <span className="mono text-[10px] text-red-400/60">FEED OFFLINE</span>
              </div>
            ) : (
              <div className="flex flex-col items-center gap-2">
                <div className="w-6 h-6 border-2 border-teal-500/30 border-t-teal-400 rounded-full animate-spin" />
                <span className="mono text-[9px] text-slate-500">AWAITING FEED…</span>
              </div>
            )}
          </div>
        )}

        {/* Top-left: Drone ID + Mission ID + Mission Name + Operator */}
        <div className="absolute top-3 left-3 z-10 flex flex-col gap-1.5">
          <div className="flex items-center gap-1.5">
            <div
              className="mono text-[11px] font-bold px-2 py-0.5 rounded"
              style={{ background: "rgba(6,14,26,0.82)", color: isDegraded ? "#fbbf24" : "#2dd4bf", border: `1px solid ${isDegraded ? "rgba(245,158,11,0.3)" : "rgba(20,184,166,0.3)"}` }}
            >
              {drone.id}
            </div>
            {drone.mission_id && (
              <div className="mono text-[9px] px-1.5 py-0.5 rounded" style={{ background: "rgba(6,14,26,0.70)", color: "#94a3b8" }}>
                {drone.mission_id}
              </div>
            )}
          </div>
          <div className="flex items-center gap-1.5">
            {missionLabel && (
              <div
                className="mono text-[9px] px-1.5 py-0.5 rounded border border-slate-700/50 truncate max-w-[110px]"
                style={{ background: "rgba(6,14,26,0.70)", color: "#e2e8f0" }}
                title={missionLabel}
              >
                {missionLabel}
              </div>
            )}
            {operatorLabel && (
              <div
                className="mono text-[9px] px-1.5 py-0.5 rounded border border-slate-700/50 flex items-center gap-1 truncate max-w-[110px]"
                style={{ background: "rgba(6,14,26,0.70)", color: "#38bdf8" }}
                title={operatorLabel}
              >
                <Users size={8} /> {operatorLabel}
              </div>
            )}
            {!missionLabel && !operatorLabel && (
              <div className="mono text-[8px] px-1.5 py-0.5 rounded" style={{ background: "rgba(6,14,26,0.60)", color: "rgba(148,163,184,0.5)" }}>
                No active mission
              </div>
            )}
          </div>
        </div>

        {/* Top-right: Status pill + streaming dot */}
        <div className="absolute top-3 right-3 z-10 flex items-center gap-1.5">
          {hasFrame && !feedDead && !isOffline && (
            <span className="w-2 h-2 rounded-full bg-teal-400" style={{ animation: "pulse 1.2s infinite" }} />
          )}
          <StatusPill status={isOffline ? "offline" : feedDead ? "offline" : drone.status} />
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
            <span className="mono text-[9px] text-slate-400">
              {drone.speed_mps != null
                ? `${(drone.speed_mps * 3.6).toFixed(1)} km/h`
                : drone.speed_kmh != null
                ? `${drone.speed_kmh} km/h`
                : "—"}
            </span>
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
          <MapPin size={9} style={{ color: "var(--dvas-text-dim)" }} className="shrink-0" />
          <span className="mono text-[9px] truncate uppercase tracking-wide" style={{ color: "var(--dvas-text-muted)" }}>{drone.jurisdiction}</span>
          <span className="ml-auto flex items-center gap-1 shrink-0">
            <Clock size={9} style={{ color: "var(--dvas-text-dim)" }} />
            <span className="mono text-[9px]" style={{ color: "var(--dvas-text-muted)" }}>{drone.uptime_min}m</span>
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
                    ? "text-red-500"
                    : drone.battery_pct <= 50
                    ? "text-amber-500"
                    : "text-emerald-500"
                }`}
              >
                {drone.battery_pct}%
              </span>
            </div>
            <span className="mono text-[8px]" style={{ color: "var(--dvas-text-dim)" }}>Battery</span>
          </div>

          {/* Crowd */}
          <div
            className="flex flex-col items-center rounded-lg py-1.5 gap-0.5"
            style={{ background: "var(--dvas-surface-2)", border: "1px solid var(--dvas-border)" }}
          >
            <div className="flex items-center gap-1">
              <Users size={10} className="text-sky-500" />
              <span className="mono text-[11px] font-bold" style={{ color: "var(--dvas-text)" }}>
                {drone.last_analytics?.crowd_count ?? "—"}
              </span>
            </div>
            <span className="mono text-[8px]" style={{ color: "var(--dvas-text-dim)" }}>Crowd</span>
          </div>

          {/* Vehicles */}
          <div
            className="flex flex-col items-center rounded-lg py-1.5 gap-0.5"
            style={{ background: "var(--dvas-surface-2)", border: "1px solid var(--dvas-border)" }}
          >
            <div className="flex items-center gap-1">
              <Car size={10} className="text-violet-500" />
              <span className="mono text-[11px] font-bold" style={{ color: "var(--dvas-text)" }}>
                {drone.last_analytics?.vehicles ?? "—"}
              </span>
            </div>
            <span className="mono text-[8px]" style={{ color: "var(--dvas-text-dim)" }}>Vehicles</span>
          </div>
        </div>

        {/* Row 3: Mission Info */}
        <div className="grid grid-cols-2 gap-2 mt-1">
          <div className="flex flex-col gap-0.5">
            <span className="mono text-[8px] uppercase tracking-wider" style={{ color: "var(--dvas-text-dim)" }}>Mission ID</span>
            <span className="mono text-[10px] truncate" style={{ color: "var(--dvas-text)" }}>{drone.mission_id || "NONE"}</span>
          </div>
          <div className="flex flex-col gap-0.5">
            <span className="mono text-[8px] uppercase tracking-wider" style={{ color: "var(--dvas-text-dim)" }}>Mission Name</span>
            <span className="mono text-[10px] truncate" style={{ color: "var(--dvas-text)" }}>{drone.mission_name || "—"}</span>
          </div>
          <div className="flex flex-col gap-0.5">
            <span className="mono text-[8px] uppercase tracking-wider" style={{ color: "var(--dvas-text-dim)" }}>Operator</span>
            <span className="mono text-[10px] truncate" style={{ color: "var(--dvas-text)" }}>{drone.operator_name || "—"}</span>
          </div>
          <div className="flex flex-col gap-0.5">
            <span className="mono text-[8px] uppercase tracking-wider" style={{ color: "var(--dvas-text-dim)" }}>Location</span>
            <span className="mono text-[10px] truncate" style={{ color: "var(--dvas-text)" }}>{drone.jurisdiction || "—"}</span>
          </div>
        </div>
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
function AnalyticsRow({ selectedDrone, drones, onOpenGallery }) {
  const [stolenSearch, setStolenSearch] = useState("");
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
    { label: "Intrusion Detection", active: drone?.id === "DRN-002", severity: "critical" },
    { label: "Suspicious Object",   active: drone?.id === "DRN-002", severity: "warning"  },
    { label: "Perimeter Breach",    active: drone?.id === "DRN-002", severity: "critical" },
  ];

  /* ANPR Data Mock */
  const anprData = [
    { plate: "KL-01-BK-9090", type: "Car", time: "12s ago", match: false },
    { plate: "KL-22-C-4512", type: "Two-Wheeler", time: "1m ago", match: false },
    { plate: "KL-01-CJ-1234", type: "Car", time: "2m ago", match: true }, // STOLEN MATCH
    { plate: "KL-05-AB-7777", type: "Heavy", time: "5m ago", match: false },
    { plate: "KL-01-CD-9999", type: "Car", time: "8m ago", match: false },
  ];
  
  const matchedPlate = anprData.find(v => v.plate.toLowerCase() === stolenSearch.toLowerCase() && v.match);
  const hasStolenMatch = !!matchedPlate;

  /* shared panel shell */
  const Panel = ({ children, borderColor = "var(--dvas-border)", className = "" }) => (
    <div
      className={`rounded-lg p-3 flex flex-col gap-2 ${className}`}
      style={{ background: "var(--dvas-surface)", border: `1px solid ${borderColor}` }}
    >
      {children}
    </div>
  );

  /* section heading */
  const SectionHead = ({ icon, label, right }) => (
    <div className="flex items-center gap-1.5 pb-1" style={{ borderBottom: "1px solid var(--dvas-border)" }}>
      {React.cloneElement(icon, { size: 14 })}
      <span className="text-xs font-semibold" style={{ color: "var(--dvas-text)" }}>{label}</span>
      {right && <span className="ml-auto mono text-xs font-medium" style={{ color: "var(--dvas-text-dim)" }}>{right}</span>}
    </div>
  );

  /* label + bold value row */
  const MetricRow = ({ label, value, valueClass }) => (
    <div className="flex items-center justify-between">
      <span className="mono text-[11px]" style={{ color: "var(--dvas-text-muted)" }}>{label}</span>
      <span className={`mono font-semibold text-sm ${valueClass || 'text-[#2D3748]'}`}>{value ?? "—"}</span>
    </div>
  );

  /* progress bar */
  const Bar = ({ pct, color }) => (
    <div className="h-1.5 rounded-full overflow-hidden" style={{ background: "var(--dvas-elev)" }}>
      <div className="h-full rounded-full transition-all duration-700" style={{ width: `${pct}%`, background: color }} />
    </div>
  );

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 xl:grid-cols-8 gap-3">

      {/* ── A: Human & Crowd ── */}
      <Panel className="col-span-1">
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
      <Panel className="col-span-1">
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

      {/* ── C: GPU Pipeline Load ── */}
      <Panel className="col-span-1">
        <SectionHead
          icon={<BarChart2 size={13} className="text-teal-400" />}
          label="GPU Pipeline Load"
          right="Processing Batch"
        />

        <div className="flex flex-col gap-2 mt-1">
          {gpuStats.map(({ label, pct, color }) => (
            <div key={label} className="flex flex-col gap-1">
              <div className="flex justify-between items-center">
                <span className="mono text-[11px]" style={{ color: "var(--dvas-text-muted)" }}>{label}</span>
                <span className="mono font-semibold text-sm" style={{ color: "var(--dvas-text)" }}>{pct}%</span>
              </div>
              <Bar pct={pct} color={color === "#14b8a6" ? "var(--dvas-cyan)" : color} />
            </div>
          ))}
        </div>
      </Panel>

      {/* ── D: Live ANPR Feed ── */}
      <Panel className="col-span-1">
        <SectionHead icon={<Camera size={13} className="text-blue-400" />} label="Live ANPR Feed" right="Scanning" />
        <div className="flex flex-col gap-1.5 overflow-y-auto max-h-[160px] pr-1 dvas-scrollbar">
          {anprData.map((v, i) => (
             <div key={i} className="flex justify-between items-center px-2 py-1.5 rounded" style={{background: v.match ? "var(--dvas-crit-bg)" : "var(--dvas-surface-2)"}}>
                <div>
                   <div className={`mono font-semibold text-[11px] ${v.match ? 'text-red-500' : ''}`} style={{ color: v.match ? 'var(--dvas-crit)' : 'var(--dvas-text)' }}>{v.plate}</div>
                   <div className="mono text-[9px]" style={{ color: "var(--dvas-text-dim)" }}>{v.type}</div>
                </div>
                <div className="mono text-[9px]" style={{ color: "var(--dvas-text-dim)" }}>{v.time}</div>
             </div>
          ))}
        </div>
      </Panel>

      {/* ── E: Subject Tracking & Biometrics ── */}
      <Panel className="col-span-1 md:col-span-2 xl:col-span-2">
        <SectionHead icon={<UserIcon size={13} className="text-pink-400" />} label="Subject Tracking & Biometrics" />
        <div className="flex gap-2 items-center mb-2 mt-1 px-1">
           <div className="flex-1 flex flex-col items-center p-2 rounded" style={{ background: "var(--dvas-surface-2)", border: "1px solid var(--dvas-border)" }}>
             <div className="mono text-[9px] mb-1" style={{ color: "var(--dvas-text-muted)" }}>PERSONS DETECTED</div>
             <div className="mono font-semibold text-base" style={{ color: "var(--dvas-text)" }}>1,240</div>
           </div>
           <div className="flex-1 flex flex-col items-center p-2 rounded" style={{ background: "var(--dvas-surface-2)", border: "1px solid var(--dvas-border)" }}>
             <div className="mono text-[9px] mb-1" style={{ color: "var(--dvas-text-muted)" }}>FACES CAPTURED</div>
             <div className="mono font-semibold text-base" style={{ color: "var(--dvas-text)" }}>842</div>
           </div>
        </div>
        <div className="flex gap-2 mt-auto">
          <button onClick={() => onOpenGallery('persons')} className="flex-1 py-1.5 text-[10px] font-semibold mono rounded transition-colors" style={{ background: "var(--dvas-surface-2)", border: "1px solid var(--dvas-border-strong)", color: "var(--dvas-text-muted)" }}>
            VIEW PERSONS
          </button>
          <button onClick={() => onOpenGallery('faces')} className="flex-1 py-1.5 text-[10px] font-semibold mono rounded transition-colors" style={{ background: "var(--dvas-surface-2)", border: "1px solid var(--dvas-border-strong)", color: "var(--dvas-text-muted)" }}>
            VIEW FACES
          </button>
        </div>
      </Panel>

      {/* ── F: Stolen Vehicle Match ── */}
      <Panel 
        borderColor={hasStolenMatch ? "var(--dvas-crit)" : "var(--dvas-border)"} 
        className={`col-span-1 md:col-span-2 xl:col-span-2`}
        style={hasStolenMatch ? { background: "var(--dvas-crit-bg)" } : {}}
      >
        <div className="flex items-center justify-between pb-1" style={{ borderBottom: "1px solid var(--dvas-border)" }}>
           <div className="flex items-center gap-1.5">
             <ShieldAlert size={14} className={hasStolenMatch ? "text-red-500" : ""} style={{ color: hasStolenMatch ? "var(--dvas-crit)" : "var(--dvas-text-dim)" }} />
             <span className="text-xs font-semibold" style={{ color: "var(--dvas-text)" }}>Stolen Vehicle Match</span>
           </div>
           <input 
             type="text" 
             placeholder="Enter Plate #..." 
             value={stolenSearch}
             onChange={(e) => setStolenSearch(e.target.value)}
             className="mono text-[10px] px-2 py-1 rounded w-28 focus:outline-none transition-colors uppercase"
             style={{ background: "var(--dvas-surface-2)", border: "1px solid var(--dvas-border-strong)", color: "var(--dvas-text)" }}
           />
        </div>
        
        {hasStolenMatch ? (
           <div className="flex items-stretch gap-2 mt-1 h-full min-h-[140px]">
              <div className="flex-1 flex flex-col items-center justify-center rounded p-2 relative" style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-crit)" }}>
                  <img src="https://images.unsplash.com/photo-1533473359331-0135ef1b58bf?auto=format&fit=crop&q=80&w=200&h=150" alt="DB Photo" className="w-full h-24 object-cover rounded mb-1 opacity-90" />
                  <span className="mono text-[9px] tracking-wider" style={{ color: "var(--dvas-crit)" }}>REGISTRY DATABASE</span>
                  <div className="absolute top-3 left-3 text-white mono font-bold text-[8px] px-1.5 py-0.5 rounded shadow-sm" style={{ background: "var(--dvas-crit)" }}>{matchedPlate.plate}</div>
              </div>
              <div className="flex-1 flex flex-col items-center justify-center rounded p-2 relative" style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-cyan)" }}>
                  <img src="https://images.unsplash.com/photo-1512428818580-08e13291eb81?auto=format&fit=crop&q=80&w=200&h=150" alt="Live Photo" className="w-full h-24 object-cover rounded mb-1" style={{ borderColor: "var(--dvas-cyan)" }} />
                  <span className="mono text-[9px] tracking-wider" style={{ color: "var(--dvas-cyan)" }}>LIVE CAPTURE · DRN-001</span>
                  <div className="absolute inset-0 m-2 flex items-center justify-center pointer-events-none">
                     <div className="w-12 h-12 border-2 rounded-sm" style={{ borderColor: "var(--dvas-crit)", opacity: 0.8 }}></div>
                  </div>
              </div>
           </div>
        ) : (
           <div className="flex flex-col items-center justify-center h-full min-h-[140px] text-slate-500">
               <ShieldCheck size={28} className="mb-2 opacity-40 text-teal-500" />
               <span className="mono text-[10px] tracking-widest text-slate-400">NO ACTIVE MATCHES</span>
               <span className="mono text-[9px] opacity-70 mt-1">Search or monitor live feed</span>
           </div>
        )}
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
      bg: "var(--dvas-crit-bg)",
      border: "var(--dvas-crit)",
      textType: "var(--dvas-crit)",
    },
    warning: {
      bg: "var(--dvas-warn-bg)",
      border: "var(--dvas-warn)",
      textType: "var(--dvas-warn)",
    },
    info: {
      bg: "var(--dvas-info-bg)",
      border: "var(--dvas-blue)",
      textType: "var(--dvas-blue)",
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
        className="px-4 py-3 flex items-center gap-2"
        style={{ borderBottom: "1px solid var(--dvas-border)" }}
      >
        <Layers size={14} className="text-teal-500 shrink-0" style={{ color: "var(--dvas-cyan)" }} />
        <span className="text-xs font-semibold uppercase flex-1" style={{ color: "var(--dvas-text)" }}>
          Confirmed Events &amp; Evidence Log
        </span>
        <span className="mono text-[10px] font-medium" style={{ color: "var(--dvas-text-dim)" }}>{events.length} events · Last 2h</span>
      </div>

      {/* Scrollable event list */}
      <div className="overflow-x-auto">
        <div className="flex gap-4 px-4 py-4" style={{ minWidth: "max-content" }}>
          {events.map((evt) => {
            const s = severityRowStyle[evt.severity] || severityRowStyle.info;
            return (
              <div
                key={evt.id}
                data-testid={`event-${evt.id}`}
                className="flex-shrink-0 flex flex-col gap-1.5 rounded-lg px-4 py-3 w-64 transition-all duration-150 hover:shadow-md"
                style={{
                  background: s.bg || "var(--dvas-surface-2)",
                  border: `1px solid ${s.border || "var(--dvas-border)"}`,
                  minWidth: "220px",
                }}
              >
                {/* Top row: severity + timestamp */}
                <div className="flex items-center gap-1.5">
                  {React.cloneElement(severityIcon[evt.severity] || severityIcon.info, { size: 12 })}
                  <span className="mono text-[10px] font-medium flex-1 truncate" style={{ color: "var(--dvas-text-muted)" }}>
                    {fmtAge(evt.ts)}
                  </span>
                  <span
                    className="mono text-[9px] px-1.5 py-0.5 rounded font-bold uppercase tracking-wide"
                    style={{
                      background: "var(--dvas-safe-bg)",
                      color: "var(--dvas-safe)",
                      border: "1px solid var(--dvas-safe)",
                      opacity: 0.8
                    }}
                  >
                    {evt.drone_id}
                  </span>
                </div>

                {/* Event type */}
                <div className="mono text-xs font-bold uppercase tracking-wide mt-1" style={{ color: s.textType }}>
                  {evt.event_type}
                </div>

                {/* Zone + mission */}
                <div className="flex items-center gap-1.5 mt-0.5">
                  <MapPin size={10} style={{ color: "var(--dvas-text-dim)" }} className="shrink-0" />
                  <span className="mono text-[9px] truncate uppercase tracking-wide" style={{ color: "var(--dvas-text-muted)" }}>{evt.zone}</span>
                </div>

                {/* Confidence + Play */}
                <div className="flex items-center gap-2 mt-2">
                  <div className="flex-1">
                    <div className="flex items-center gap-1.5">
                      <TrendingUp size={10} style={{ color: "var(--dvas-text-dim)" }} />
                      <span className="mono text-[9px] font-medium uppercase tracking-wide" style={{ color: "var(--dvas-text-muted)" }}>
                        Conf:{" "}
                        <span
                          className="font-bold"
                          style={{ color: evt.confidence >= 90 ? "var(--dvas-safe)" : "var(--dvas-warn)" }}
                        >
                          {evt.confidence}%
                        </span>
                      </span>
                    </div>
                  </div>
                  <button
                    onClick={(e) => { e.stopPropagation(); onPlay && onPlay(evt); }}
                    className="flex items-center gap-1 px-2 py-1 rounded text-[9px] font-bold uppercase tracking-wide mono transition-colors hover:shadow-sm"
                    style={{
                      background: "var(--dvas-safe-bg)",
                      border: "1px solid var(--dvas-safe)",
                      color: "var(--dvas-safe)",
                    }}
                    data-testid={`play-evidence-${evt.id}`}
                  >
                    <Play size={10} />
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
            style={{ background: "var(--dvas-safe-bg)", border: "1px solid var(--dvas-safe)" }}
          >
            <Radio size={12} style={{ color: "var(--dvas-safe)" }} />
          </div>
          <div className="text-[11px] font-bold tracking-wide leading-tight" style={{ color: "var(--dvas-safe)" }}>
            DVAS – Kerala Police
          </div>
        </div>
        <div className="mono text-[8px] mt-0.5 pl-8" style={{ color: "var(--dvas-text-dim)" }}>
          Central Command &amp; Intelligence · Multi-Drone View
        </div>
      </div>

      {/* Jurisdiction chip */}
      <div
        className="hidden md:flex items-center gap-1.5 px-3 py-1.5 rounded-lg shrink-0"
        style={{ background: "var(--dvas-surface-2)", border: "1px solid var(--dvas-border)" }}
      >
        <MapPin size={10} style={{ color: "var(--dvas-text-dim)" }} />
        <span className="mono text-[9px] font-medium" style={{ color: "var(--dvas-text-muted)" }}>
          Jurisdiction: <span className="font-bold" style={{ color: "var(--dvas-text)" }}>Thiruvananthapuram</span>
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
              <div className="mono text-[8px]" style={{ color: "var(--dvas-text-dim)" }}>{label}</div>
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
  // Use active ingest streams to discover real drones and enrich with live metadata
  const { mergeWithDrones, streams } = useActiveStreams();

  // Start with DB drones (from Postgres, telemetry-pumped)
  // Then merge with real ingest streams (adds missing drones, enriches existing ones)
  const baseList = liveDrones && liveDrones.length > 0 ? liveDrones : [];
  let merged   = mergeWithDrones(baseList);

  // Filter out offline drones whose stream queue is exhausted
  merged = merged.filter(d => !(d.status === "offline" && d.messages_ready === 0));

  // If neither DB nor ingest has any drones, fall back to MOCK_DRONES for UI demo
  const drones = merged.length > 0
    ? merged.sort((a, b) => {
        const ord = { live: 0, degraded: 1, offline: 2 };
        const ao = ord[a.status] ?? 3; const bo = ord[b.status] ?? 3;
        if (ao !== bo) return ao - bo;
        return (a.id || "").localeCompare(b.id || "");
      })
    : MOCK_DRONES;

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
  const [galleryType, setGalleryType] = useState(null); // 'persons' | 'faces' | null

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

      {/* ── 2. Drone Cards Grid (tiled CSS grid format) ── */}
      <div className="flex-1 overflow-y-auto dvas-scrollbar min-h-[60vh] xl:min-h-[500px]">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 transition-all duration-500 ease-in-out h-full content-start pb-4">
          {drones.map((d) => {
            const isSelected = selectedDroneId === d.id;
            
            return (
              <div 
                key={d.id} 
                className={`transition-all duration-500 ease-in-out ${
                  isSelected 
                    ? "col-span-1 md:col-span-2 lg:col-span-2 xl:col-span-2 row-span-1 md:row-span-2" 
                    : "col-span-1"
                }`}
              >
                <DroneFeedCard
                  drone={d}
                  isSelected={isSelected}
                  hasSelection={!!selectedDroneId}
                  onSelect={setSelectedDroneId}
                />
              </div>
            );
          })}
        </div>
      </div>

      {/* ── 3. Analytics Row (5 panels horizontal) ── */}
      <AnalyticsRow
        selectedDrone={selectedDroneId}
        drones={drones}
        onOpenGallery={setGalleryType}
      />

      {/* ── 4. Event Timeline ── */}
      <EventTimeline 
        events={selectedDroneId ? events.filter(e => e.drone_id === selectedDroneId) : events} 
        onPlay={setPlayingEvent} 
      />

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

      {/* ── Biometrics Gallery Modal ── */}
      {galleryType && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4 md:p-8"
          style={{ background: "rgba(0,0,0,0.85)", backdropFilter: "blur(6px)" }}
          onClick={() => setGalleryType(null)}
        >
          <div
            className="relative w-full max-w-5xl max-h-full rounded-2xl flex flex-col"
            style={{
              background: "var(--dvas-surface)",
              border: "1px solid rgba(236,72,153,0.3)", // pink border for biometrics
              boxShadow: "0 0 0 1px rgba(236,72,153,0.1), 0 24px 64px rgba(0,0,0,0.6)",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            {/* Modal header */}
            <div className="px-5 py-4 flex items-center justify-between" style={{ borderBottom: "1px solid var(--dvas-border)" }}>
              <div className="flex items-center gap-3">
                <Users size={18} className="text-pink-400" />
                <div>
                  <h2 className="text-sm font-bold text-pink-300 tracking-wide uppercase">
                    Deduplicated {galleryType === "persons" ? "Persons" : "Faces"} Gallery
                  </h2>
                  <div className="mono text-[10px] text-slate-500 mt-1">Cross-jurisdictional biometric tracking</div>
                </div>
              </div>
              <button
                onClick={() => setGalleryType(null)}
                className="w-8 h-8 rounded-lg flex items-center justify-center transition-colors hover:bg-slate-800 border border-slate-700 text-slate-300 hover:text-white"
              >
                <X size={16} />
              </button>
            </div>

            {/* Gallery Grid */}
            <div className="flex-1 overflow-y-auto p-5 dvas-scrollbar">
              <div className={`grid gap-4 ${galleryType === "persons" ? "grid-cols-2 sm:grid-cols-3 lg:grid-cols-5" : "grid-cols-3 sm:grid-cols-4 lg:grid-cols-6"}`}>
                {Array.from({ length: 15 }).map((_, i) => (
                  <div key={i} className="flex flex-col rounded-lg overflow-hidden border border-slate-700/60 bg-slate-900/50 transition-transform hover:scale-105 cursor-pointer hover:border-pink-500/50">
                    <div className={`relative ${galleryType === "persons" ? "aspect-[3/4]" : "aspect-square"}`}>
                      {galleryType === "persons" ? (
                        <img src={`https://images.unsplash.com/photo-${1500000000000 + i * 1000}?auto=format&fit=crop&w=300&h=400&q=80`} alt="Person Crop" className="w-full h-full object-cover opacity-80" onError={(e) => { e.target.src = "https://images.unsplash.com/photo-1511367461989-f85a21fda167?auto=format&fit=crop&w=300&h=400&q=80"; }} />
                      ) : (
                        <img src={`https://images.unsplash.com/photo-${1550000000000 + i * 1000}?auto=format&fit=facearea&facepad=2&w=256&h=256&q=80`} alt="Face Crop" className="w-full h-full object-cover opacity-80" onError={(e) => { e.target.src = "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?auto=format&fit=facearea&facepad=2&w=256&h=256&q=80"; }} />
                      )}
                      <div className="absolute inset-0 border border-pink-500/20 mix-blend-overlay"></div>
                    </div>
                    <div className="p-2 border-t border-slate-700/60 flex flex-col gap-1">
                      <div className="mono font-bold text-[10px] text-slate-300">SUBJ-{8800 + i}</div>
                      <div className="flex justify-between items-center">
                        <span className="mono text-[9px] text-slate-500">{new Date(Date.now() - i * 360000).toLocaleTimeString()}</span>
                        <span className="mono text-[8px] px-1 bg-slate-800 text-teal-400 rounded">DR-0{i%3 + 1}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
