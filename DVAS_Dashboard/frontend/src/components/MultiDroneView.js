/**
 * MultiDroneView.js
 * ─────────────────────────────────────────────────────────
 * High-fidelity Multi-Drone Situational Command View
 * DVAS – Kerala Police | Central Command & Intelligence
 * ─────────────────────────────────────────────────────────
 */

import React, { useEffect, useRef, useState, useCallback, useMemo } from "react";
import { API, api, apiError } from "@/lib/api";
import { fmtIST } from "@/lib/ist";
import { useAuth } from "@/context/AuthContext";
import EvidenceImage from "@/components/EvidenceImage";
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
  Trash2,
  Check,
} from "lucide-react";


import { useActiveStreams, framesLeft } from "@/hooks/useActiveStreams";
import { useJpegPoller } from "@/hooks/useJpegPoller";
import { useLiveVideo } from "@/hooks/useLiveVideo";
import LiveVideoStats from "@/components/LiveVideoStats";
import { useAnalyticsStates } from "@/hooks/useAnalyticsStates";
import { useTileSlots } from "@/hooks/useTileSlots";
import {
  useCrowdReadings,
  usePersonCount,
  usePersonIdentities,
  useFacesGallery,
  usePersonLive,
  useVehicleTracks,
  useVehicleStats,
  usePlatesLive,
  usePlatesRecent,
  useWatchlist,
  useTheftAlerts,
  useModelsStatus,
} from "@/lib/useAnalytics";
import { resolveMinioImageUrl } from "@/lib/api";

function toProxiedUrl(objectKeyOrUrl, bucket = "dvas-faces") {
  if (!objectKeyOrUrl) return null;
  if (objectKeyOrUrl.startsWith("data:image")) return objectKeyOrUrl;
  if (objectKeyOrUrl.startsWith("http://minio") || objectKeyOrUrl.startsWith("http://localhost") || objectKeyOrUrl.startsWith("http://127.0.0.1")) {
    try {
      const u = new URL(objectKeyOrUrl);
      const parts = u.pathname.split("/").filter(Boolean);
      if (parts.length >= 2) {
        const b = parts[0];
        const k = parts.slice(1).join("/");
        return resolveMinioImageUrl(k, b);
      }
    } catch (_) {}
  }
  return resolveMinioImageUrl(objectKeyOrUrl, bucket);
}

/* ─────────────────────────────────────────────
   HELPERS
   ───────────────────────────────────────────── */
function fmtAge(isoTs) {
  if (!isoTs) return "—";
  const t = new Date(isoTs).getTime();
  if (Number.isNaN(t)) return "—";
  const diff = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

// Roles allowed to purge a stream queue (backend returns 403 to everyone else)
const PURGE_ROLES = ["control_room_officer", "senior_officer", "system_administrator"];

const EVENT_WINDOW_MS = 2 * 60 * 60 * 1000; // event log shows the last 2 h

/** Minutes since an ISO start time, or null when no real start time is known */
function minutesSince(isoTs) {
  if (!isoTs) return null;
  const t = new Date(isoTs).getTime();
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.floor((Date.now() - t) / 60000));
}

function BatteryIcon({ pct }) {
  if (typeof pct !== "number") return <Battery size={12} className="text-slate-500" />;
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
    draining: {
      dot: "bg-amber-400 animate-pulse",
      bg: "bg-amber-500/10",
      border: "border-amber-500/30",
      text: "text-amber-400",
      label: "DRAINING",
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

// After a 404 (stream gone, or drone outside the user's jurisdiction) retry every 30 s (A5)
const FEED_404_RETRY_MS = 30000;

function useDroneFeed(droneId, disabled, intervalMs = 700) {
  const url = disabled ? null : `${API}/ingest-server/frame/${encodeURIComponent(droneId)}/latest`;
  return useJpegPoller(url, intervalMs, !disabled, { notFoundRetryMs: FEED_404_RETRY_MS });
}

// v2 R6.4: analytics state of a drone, next to its status (live video is never affected)
function AnalyticsStateBadge({ info }) {
  if (!info || info.state === "live") return null;
  const lagging = Object.entries(info.services || {}).filter(([, v]) => v.state !== "live");
  const title = lagging.map(([svc, v]) =>
    `${svc}: ${v.behind_s ?? "?"} s behind live` +
    (v.frames_shed ? `, ${v.frames_shed} frames not analysed` : "") +
    (v.cadence > 1 ? `, every ${v.cadence}th frame` : "")).join("\n");
  const shedding = info.state === "shedding";
  return (
    <span
      className={`mono text-[9px] font-bold px-1.5 py-0.5 rounded border ${shedding
        ? "text-red-300 border-red-500/40 bg-red-500/10" : "text-amber-300 border-amber-500/40 bg-amber-500/10"}`}
      title={title}
      data-testid="analytics-state"
    >
      {shedding ? "SHEDDING" : "DEGRADED"}
    </span>
  );
}

function DroneFeedCard({ drone, isSelected, hasSelection, onSelect, onPurge, canPurge, analytics }) {
  const isDegraded = drone.status === "degraded";
  const isDraining = drone.status === "draining";
  // A5: only live tiles poll raw frames; draining and offline tiles show a placeholder
  const isLive     = drone.status === "live" || isDegraded;
  const isOffline  = !isLive && !isDraining;
  const isOtherSelected = hasSelection && !isSelected;
  const left = framesLeft(drone);

  // v2: continuous H.264 when the edge streams video; the JPEG feed only while it does not.
  // Grid tiles play the 720p sub-stream; the selected tile plays the source.
  const live = useLiveVideo(drone.id, isLive, { sub: !isSelected });
  const { blobUrl, hasFrame, feedDead, notFound } = useDroneFeed(drone.id, !isLive || live.playing);
  const noAccess = notFound === "no_access";
  const hasPicture = live.playing || hasFrame;
  const feedDown = !live.playing && feedDead;

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
  // DB column is signal_pct; ingest-only drones carry no signal reading
  const signal = typeof drone.signal_pct === "number"
    ? drone.signal_pct
    : typeof drone.signal_strength === "number" ? drone.signal_strength : null;
  const uptimeMin = minutesSince(drone.started_at);

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

        {/* Live video (v2), else the JPEG feed, else a placeholder */}
        {isLive && (
          <video
            ref={live.videoRef}
            muted
            autoPlay
            playsInline
            className="absolute inset-0 w-full h-full object-cover"
            style={{ opacity: live.playing ? (isDegraded ? 0.8 : 0.95) : 0 }}
          />
        )}
        {isLive && live.playing && <LiveVideoStats stats={live.stats} sub={live.info?.sub} />}
        {isLive && live.playing ? null : isLive && hasFrame && blobUrl ? (
          <img
            src={blobUrl}
            alt={`Live feed ${drone.id}`}
            className="absolute inset-0 w-full h-full object-cover"
            style={{ opacity: isDegraded ? 0.8 : 0.95 }}
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center">
            {isDraining ? (
              <div className="flex flex-col items-center gap-2">
                <Clock size={22} className="text-amber-400/70" />
                <span className="mono text-[10px] text-amber-300/80">
                  DRAINING — {left} {left === 1 ? "frame" : "frames"} left
                </span>
              </div>
            ) : noAccess ? (
              <div className="flex flex-col items-center gap-2" title="This drone is outside your jurisdiction">
                <WifiOff size={22} className="text-slate-400/60" />
                <span className="mono text-[10px] text-slate-400/80">NO ACCESS</span>
              </div>
            ) : isOffline || feedDown ? (
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

        {/* Top-right: Status pill + streaming dot + Purge button */}
        <div className="absolute top-3 right-3 z-10 flex flex-col items-end gap-1.5">
          <div className="flex items-center gap-1.5">
            {canPurge && (drone.status === "draining" || (drone.messages_ready && drone.messages_ready > 0)) && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onPurge?.(drone.id);
                }}
                title="Delete stream and discard remaining queue immediately"
                className="mono text-[9px] px-1.5 py-0.5 rounded flex items-center gap-1 transition-all bg-red-500/20 text-red-400 border border-red-500/40 hover:bg-red-500/30"
              >
                <Trash2 size={9} /> Purge
              </button>
            )}
            {hasPicture && !feedDown && !isOffline && (
              <span className="w-2 h-2 rounded-full bg-teal-400" style={{ animation: "pulse 1.2s infinite" }} />
            )}
            {isLive && <AnalyticsStateBadge info={analytics} />}
            <StatusPill status={isDraining ? "draining" : isOffline || feedDown ? "offline" : drone.status} />
          </div>
          {isDraining && (
            <span className="text-[10px] bg-amber-900/60 text-amber-300 px-2 py-0.5 rounded-full flex items-center gap-1 font-bold">
              <Clock className="w-2.5 h-2.5" />
              {left} frames
            </span>
          )}
        </div>


        {/* Altitude & speed HUD (bottom bar) */}
        <div
          className="absolute bottom-0 left-0 right-0 px-3 py-1.5 flex items-center gap-3"
          style={{ background: "rgba(6,14,26,0.8)", backdropFilter: "blur(4px)" }}
        >
          <div className="flex items-center gap-1">
            <Wind size={9} className="text-slate-500" />
            <span className="mono text-[9px] text-slate-400">{drone.altitude_m != null ? `${drone.altitude_m}m` : "—"}</span>
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
            {signal != null && <SignalBars strength={signal} />}
            <span className="mono text-[9px] text-slate-500">{signal != null ? `${signal}%` : "—"}</span>
          </div>
        </div>
      </div>

      {/* ── Telemetry Strip ── */}
      <div className="px-3 py-2.5 flex flex-col gap-2">
        {/* Row 1: Zone name */}
        <div className="flex items-center gap-1.5">
          <MapPin size={9} style={{ color: "var(--dvas-text-dim)" }} className="shrink-0" />
          <span className="mono text-[9px] truncate uppercase tracking-wide" style={{ color: "var(--dvas-text-muted)" }}>{drone.jurisdiction || "—"}</span>
          <span className="ml-auto flex items-center gap-1 shrink-0">
            <Clock size={9} style={{ color: "var(--dvas-text-dim)" }} />
            <span className="mono text-[9px]" style={{ color: "var(--dvas-text-muted)" }}>{uptimeMin != null ? `${uptimeMin}m` : "—"}</span>
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
                  typeof drone.battery_pct !== "number"
                    ? "text-slate-500"
                    : drone.battery_pct <= 20
                    ? "text-red-500"
                    : drone.battery_pct <= 50
                    ? "text-amber-500"
                    : "text-emerald-500"
                }`}
              >
                {typeof drone.battery_pct === "number" ? `${drone.battery_pct}%` : "—"}
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
   OFFLINE TILE (v2 R7): a free place in the fixed grid. Makes no requests.
   ───────────────────────────────────────────── */
// Tiles the grid always shows; drones take the lowest free one (hooks/useTileSlots.js)
const GRID_TILES = 20;

function OfflineTileCard({ index, lastId, dimmed }) {
  const label = `TILE ${String(index + 1).padStart(2, "0")}`;
  return (
    <div
      className={`relative flex flex-col rounded-xl border border-slate-700/40 overflow-hidden h-full transition-all duration-500 ${dimmed ? "opacity-40" : ""}`}
      style={{ background: "var(--dvas-surface)" }}
      data-testid={`multidrone-tile-offline-${index + 1}`}
    >
      <div className="relative aspect-video bg-[#060e1a] overflow-hidden flex-shrink-0">
        {[
          "top-2 left-2 border-l-2 border-t-2",
          "top-2 right-2 border-r-2 border-t-2",
          "bottom-2 left-2 border-l-2 border-b-2",
          "bottom-2 right-2 border-r-2 border-b-2",
        ].map((cls, i) => (
          <div key={i} className={`absolute w-4 h-4 ${cls} border-slate-600/40`} />
        ))}
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2">
          <WifiOff size={22} className="text-slate-500/60" />
          <span className="mono text-[10px] text-slate-500">NO STREAM</span>
        </div>
        <div className="absolute top-3 left-3 z-10">
          <div
            className="mono text-[11px] font-bold px-2 py-0.5 rounded"
            style={{ background: "rgba(6,14,26,0.82)", color: "#64748b", border: "1px solid rgba(100,116,139,0.3)" }}
          >
            {label}
          </div>
        </div>
        <div className="absolute top-3 right-3 z-10">
          <StatusPill status="offline" />
        </div>
      </div>
      <div className="px-3 py-2.5 flex flex-col gap-1 flex-1 justify-center">
        <span className="mono text-[8px] uppercase tracking-wider" style={{ color: "var(--dvas-text-dim)" }}>
          Last drone
        </span>
        <span className="mono text-[10px] truncate" style={{ color: "var(--dvas-text-muted)" }}>
          {lastId || "—"}
        </span>
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
/* ─────────────────────────────────────────────
   BIOMETRICS GALLERY MODAL (Real Persons & Faces)
   ───────────────────────────────────────────── */
function BiometricsGalleryModal({ galleryType, onClose, effectiveDroneId }) {
  const { data: identitiesData, loading: idLoading } = usePersonIdentities(effectiveDroneId, 10000);
  const { data: facesData, loading: facesLoading } = useFacesGallery(effectiveDroneId, 10000);
  const { data: personLive, loading: liveLoading } = usePersonLive(effectiveDroneId, 3000);

  const isPersons = galleryType === "persons";
  const loading = isPersons ? (idLoading && liveLoading) : facesLoading;

  const rawList = isPersons
    ? (identitiesData?.identities || identitiesData?.clusters || personLive?.persons || [])
    : (facesData?.faces || facesData?.gallery || []);

  const items = useMemo(() => {
    if (!Array.isArray(rawList)) return [];
    return rawList;
  }, [rawList]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 md:p-8"
      style={{ background: "rgba(0,0,0,0.85)", backdropFilter: "blur(6px)" }}
      onClick={onClose}
    >
      <div
        className="relative w-full max-w-5xl max-h-[90vh] rounded-2xl flex flex-col overflow-hidden"
        style={{
          background: "var(--dvas-surface)",
          border: "1px solid rgba(236,72,153,0.3)",
          boxShadow: "0 0 0 1px rgba(236,72,153,0.1), 0 24px 64px rgba(0,0,0,0.6)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Modal header */}
        <div className="px-5 py-4 flex items-center justify-between" style={{ borderBottom: "1px solid var(--dvas-border)" }}>
          <div className="flex items-center gap-3">
            <Users size={18} className="text-pink-400" />
            <div>
              <h2 className="text-sm font-bold text-pink-300 tracking-wide uppercase flex items-center gap-2">
                Live {isPersons ? "Person Identities" : "Faces"} Gallery
                <span className="mono text-[10px] px-1.5 py-0.5 rounded bg-pink-500/20 text-pink-300 border border-pink-500/40">
                  {effectiveDroneId ? `Drone: ${effectiveDroneId}` : "Fleet Stream"}
                </span>
              </h2>
              <div className="mono text-[10px] text-slate-400 mt-0.5">
                {items.length} {isPersons ? "identities tracked" : "faces cataloged"} · MinIO / Valkey Re-ID
              </div>
            </div>
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 rounded-lg flex items-center justify-center transition-colors hover:bg-slate-800 border border-slate-700 text-slate-300 hover:text-white"
          >
            <X size={16} />
          </button>
        </div>

        {/* Gallery Grid */}
        <div className="flex-1 overflow-y-auto p-5 dvas-scrollbar min-h-[300px]">
          {loading && items.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-slate-500">
              <RefreshCw size={24} className="animate-spin text-pink-400 mb-2" />
              <span className="mono text-xs">Retrieving biometric captures from server…</span>
            </div>
          ) : items.length > 0 ? (
            <div className={`grid gap-4 ${isPersons ? "grid-cols-2 sm:grid-cols-3 lg:grid-cols-5" : "grid-cols-3 sm:grid-cols-4 lg:grid-cols-6"}`}>
              {items.map((item, i) => {
                const imgUrl = item.crop64
                  ? `data:image/jpeg;base64,${item.crop64}`
                  : toProxiedUrl(item.crop_url || item.image_url || item.face_image_url || item.key, isPersons ? "dvas-faces" : "dvas-faces");
                const tag = item.person_id || item.track_id || item.face_id || (isPersons ? `PERS-${i + 1}` : `FACE-${i + 1}`);
                const ts = item.detected_at || item.last_seen || item.created_at || item.timestamp;
                const droneTag = item.drone_id || effectiveDroneId || "DRN";
                const conf = typeof item.confidence === "number" ? Math.round(item.confidence * 100) : null;

                return (
                  <div
                    key={item.id || item.track_id || i}
                    className="flex flex-col rounded-lg overflow-hidden border border-slate-700/60 bg-slate-900/50 transition-transform hover:scale-105 cursor-pointer hover:border-pink-500/50"
                  >
                    <div className={`relative ${isPersons ? "aspect-[3/4]" : "aspect-square"} bg-slate-950`}>
                      {imgUrl ? (
                        <img
                          src={imgUrl}
                          alt={tag}
                          className="w-full h-full object-cover"
                          onError={(e) => {
                            e.target.onerror = null;
                            e.target.style.display = 'none'; e.target.parentElement.classList.add('bg-slate-800'); e.target.insertAdjacentHTML('afterend', '<div class="flex items-center justify-center w-full h-full text-slate-600 text-xs">No image</div>');
                          }}
                        />
                      ) : (
                        <div className="w-full h-full flex items-center justify-center text-slate-600">
                          <UserIcon size={32} />
                        </div>
                      )}
                      <div className="absolute inset-0 border border-pink-500/20 mix-blend-overlay pointer-events-none" />
                      {conf !== null && (
                        <span className="absolute top-1 right-1 mono text-[8px] font-bold px-1 rounded bg-black/70 text-emerald-400">
                          {conf}%
                        </span>
                      )}
                    </div>
                    <div className="p-2 border-t border-slate-700/60 flex flex-col gap-1">
                      <div className="mono font-bold text-[10px] text-slate-200 truncate">{tag}</div>
                      <div className="flex justify-between items-center text-[8px] mono text-slate-400">
                        <span>{ts ? fmtAge(ts) : "Live track"}</span>
                        <span className="px-1 bg-slate-800 text-teal-400 rounded truncate max-w-[65px]">{droneTag}</span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center py-24 text-center text-slate-500">
              <Users size={36} className="text-pink-400/40 mb-3 animate-pulse" />
              <div className="mono text-sm font-semibold text-slate-300">
                No {isPersons ? "Person Identities" : "Face Detections"} Logged Yet
              </div>
              <div className="mono text-xs text-slate-500 mt-1 max-w-sm">
                Biometrics tracking service is scanning active stream <span className="text-pink-300">{effectiveDroneId || "fleet feeds"}</span>. Captures will appear here as subjects enter camera frame.
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────
   ANALYTICS ROW (End-to-End Live Processing Feeds)
   ───────────────────────────────────────────── */
function AnalyticsRow({ selectedDrone, drones, onOpenGallery, effectiveDroneId: passedDroneId, jurisdiction }) {
  const [stolenSearch, setStolenSearch] = useState("");
  const drone = drones.find((d) => d.id === selectedDrone) || drones[0];
  const effectiveDroneId = passedDroneId || drone?.id || "";
  const isSelected = !!selectedDrone;

  // 1. Human & Crowd Analytics Live Hooks
  const { data: crowdData } = useCrowdReadings(effectiveDroneId, 4000);
  const { data: personCount } = usePersonCount(effectiveDroneId, 4000);
  const crowdAnnotatedUrl = effectiveDroneId
    ? `${API}/analytics/crowd/annotated?drone_id=${encodeURIComponent(effectiveDroneId)}`
    : "";
  const { blobUrl: crowdHeatmapUrl, hasFrame: hasCrowdHeatmap } = useJpegPoller(
    crowdAnnotatedUrl,
    1000,
    !!effectiveDroneId
  );

  const crowdCount = (typeof crowdData?.count === "number")
    ? crowdData.count
    : (Array.isArray(crowdData?.readings) && crowdData.readings.length > 0)
    ? (crowdData.readings[0]?.count ?? null)
    : (drone?.last_analytics?.crowd_count ?? null);
  const hasCrowd = typeof crowdCount === "number";

  const densityLevel = !hasCrowd ? null : crowdCount >= 150 ? "Critical" : crowdCount >= 75 ? "High" : crowdCount >= 25 ? "Medium" : "Low";
  const densityColor = {
    Critical: "text-red-500",
    High: "text-orange-400",
    Medium: "text-amber-400",
    Low: "text-emerald-400",
  }[densityLevel] || "text-slate-400";

  const activePersons = personCount?.active_tracks ?? personCount?.total_persons ?? (drone?.last_analytics?.crowd_count ?? null);
  const densityPct = hasCrowd ? Math.min(100, Math.max(0, Math.round((crowdCount / 150) * 100))) : 0;

  // 2. Vehicle Metrics Live Hooks
  const { data: vehicleTracks } = useVehicleTracks(effectiveDroneId, 5000);
  const { data: vehicleStats } = useVehicleStats(15000);
  // Live counts come from this drone's active vehicle tracks. /analytics/vehicle/stats returns
  // { total_detected, total_tracked, total_plates, by_type, avg_speed_kmh } (all drones, all time),
  // so it is shown separately rather than as this drone's live count.
  const hasVehicleTracks = Array.isArray(vehicleTracks?.tracks);
  const liveVehicleList = hasVehicleTracks ? vehicleTracks.tracks : [];
  const vClass = (t) => (t.class_name || t.vehicle_type || t.type || "").toLowerCase();
  const TWO_WHEELERS = ["motorcycle", "motor", "bicycle", "two-wheeler", "2-wheeler", "scooter"];
  const carsCount = hasVehicleTracks ? liveVehicleList.filter(t => vClass(t) === "car" || vClass(t) === "van").length : null;
  const twoWheelersCount = hasVehicleTracks ? liveVehicleList.filter(t => TWO_WHEELERS.includes(vClass(t))).length : null;
  const totalVehicles = hasVehicleTracks ? liveVehicleList.length : null;
  const trafficState = totalVehicles == null ? null : totalVehicles > 60 ? "Congested" : totalVehicles > 20 ? "Moderate" : "Clear";
  const trafficColor = {
    Congested: "text-red-400",
    Moderate: "text-amber-400",
    Clear: "text-emerald-400",
  }[trafficState] || "text-slate-400";

  // 3. Analytics engine status (from each service's /health via /analytics/models/status).
  // No per-engine GPU load is measured, so only running/standby state is shown.
  const { data: modelsStatus } = useModelsStatus(10000);
  const engineRoster = [
    { key: "person", label: "Person Det." },
    { key: "vehicle", label: "Vehicle Det." },
    { key: "crowd", label: "CSRNet Crowd" },
    { key: "anpr", label: "Live ANPR" },
  ];

  // 4. Live ANPR Feed Hooks
  const { data: platesLive } = usePlatesLive(effectiveDroneId, 3000);
  const { data: platesRecent } = usePlatesRecent(effectiveDroneId, 6000);
  const livePlates = useMemo(() => {
    const liveItems = Array.isArray(platesLive?.plates) ? platesLive.plates : [];
    if (liveItems.length > 0) return liveItems;
    return Array.isArray(platesRecent?.plates) ? platesRecent.plates : [];
  }, [platesLive, platesRecent]);

  // 5. Stolen Vehicle Match & Watchlist Hooks
  const { data: watchlistData } = useWatchlist(15000);
  const { data: theftData } = useTheftAlerts(5000);
  const watchlist = Array.isArray(watchlistData?.watchlist) ? watchlistData.watchlist : [];
  const theftAlerts = Array.isArray(theftData?.alerts) ? theftData.alerts : [];

  const searchUpper = stolenSearch.trim().toUpperCase();
  const searchMatch = searchUpper ? watchlist.find(w => (w.plate_text || "").toUpperCase() === searchUpper) : null;
  const liveHit = livePlates.find(p => {
    const text = (p.plate_text || p.plate || "").toUpperCase();
    return text && watchlist.some(w => (w.plate_text || "").toUpperCase() === text);
  });
  const liveHitWatchlist = liveHit ? watchlist.find(w => (w.plate_text || "").toUpperCase() === (liveHit.plate_text || liveHit.plate || "").toUpperCase()) : null;
  const topTheftAlert = theftAlerts[0] || null;

  const matchedRecord = searchMatch || liveHitWatchlist || (topTheftAlert ? {
    plate_text: topTheftAlert.plate_text,
    vehicle_type: topTheftAlert.vehicle_type || "Vehicle",
    reason: topTheftAlert.notes || "Flagged in Stolen Vehicle Database",
    added_at: topTheftAlert.detected_at,
  } : null);

  const hasStolenMatch = !!matchedRecord;
  // Real hit count: theft alerts plus live plate reads that match the watchlist
  const watchlistHitCount = theftAlerts.length + livePlates.filter(p => {
    const text = (p.plate_text || p.plate || "").toUpperCase();
    return p.is_watchlist || p.is_stolen || (text && watchlist.some(w => (w.plate_text || "").toUpperCase() === text));
  }).length;

  /* shared panel shell */
  const Panel = ({ children, borderColor = "var(--dvas-border)", className = "", style = {} }) => (
    <div
      className={`rounded-lg p-3 flex flex-col gap-2 ${className}`}
      style={{ background: "var(--dvas-surface)", border: `1px solid ${borderColor}`, ...style }}
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
      <span className={`mono font-semibold text-sm ${valueClass || "text-[#2D3748]"}`}>{value ?? "—"}</span>
    </div>
  );

  /* progress bar */
  const Bar = ({ pct, color }) => (
    <div className="h-1.5 rounded-full overflow-hidden" style={{ background: "var(--dvas-elev)" }}>
      <div className="h-full rounded-full transition-all duration-700" style={{ width: `${pct}%`, background: color }} />
    </div>
  );

  return (
    <div className="flex flex-col gap-2">
      {/* Scope Indicator Bar */}
      <div className="flex items-center justify-between px-3 py-1 rounded border border-slate-700/40 bg-slate-900/60 mono text-[10px]">
        <div className="flex items-center gap-2">
          <span className="text-slate-400 font-medium">ANALYTICS STREAM:</span>
          {isSelected ? (
            <span className="px-1.5 py-0.5 rounded bg-teal-500/20 text-teal-300 font-bold border border-teal-500/40 flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-teal-400 animate-pulse" />
              Targeted: {effectiveDroneId}
            </span>
          ) : (
            <span className="px-1.5 py-0.5 rounded bg-sky-500/20 text-sky-300 font-medium border border-sky-500/40 flex items-center gap-1">
              <Radio size={10} className="text-sky-400" />
              Primary Stream: {effectiveDroneId || "Awaiting Stream"}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 text-slate-400">
          <span className="flex items-center gap-1">
            <Activity size={11} className="text-emerald-400" /> Live Ingest Sync
          </span>
          <span className="text-slate-500">·</span>
          <span>{jurisdiction || "—"}</span>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 xl:grid-cols-8 gap-3">
        {/* ── A: Human & Crowd Analytics ── */}
        <Panel className="col-span-1">
          <SectionHead
            icon={<Users size={13} className="text-sky-400" />}
            label="Human & Crowd Analytics"
          />

          {/* CSRNet Heatmap Display */}
          <div className="relative w-full rounded overflow-hidden" style={{ height: 58, border: "1px solid var(--dvas-border)", background: "#060e1a" }}>
            {hasCrowdHeatmap && crowdHeatmapUrl ? (
              <img
                src={crowdHeatmapUrl}
                alt="CSRNet Heatmap"
                className="w-full h-full object-cover opacity-90"
              />
            ) : (
              <div className="absolute inset-0 flex items-center justify-center">
                <span className="mono text-[9px] text-slate-500">No frame received</span>
              </div>
            )}
            {hasCrowdHeatmap && crowdHeatmapUrl && densityLevel && (
              <span className="absolute bottom-0.5 right-1.5 mono text-teal-400 text-[9px] font-bold px-1 bg-black/70 rounded">
                CSRNet · {densityLevel.toUpperCase()}
              </span>
            )}
          </div>

          <MetricRow label="Crowd Density" value={densityLevel} valueClass={densityLevel ? densityColor : undefined} />
          <MetricRow label="Active Persons" value={activePersons} />
          <Bar
            pct={densityPct}
            color={
              densityLevel === "Critical" || densityLevel === "High"
                ? "linear-gradient(90deg,#ef4444,#f87171)"
                : densityLevel === "Medium"
                ? "linear-gradient(90deg,#f59e0b,#fbbf24)"
                : "linear-gradient(90deg,#10b981,#34d399)"
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
            { label: "Cars", count: carsCount, max: 150, color: "#818cf8" },
            { label: "Two-Wheelers", count: twoWheelersCount, max: 80, color: "#c084fc" },
          ].map(({ label, count, max, color }) => (
            <div key={label} className="flex flex-col gap-1">
              <div className="flex justify-between items-center">
                <span className="mono text-slate-500 text-xs">{label}</span>
                <span className="mono font-bold text-sm text-slate-100">{count ?? "—"}</span>
              </div>
              <Bar pct={count != null ? Math.min(100, (count / max) * 100) : 0} color={color} />
            </div>
          ))}

          <MetricRow label="Traffic State" value={trafficState} valueClass={trafficState ? trafficColor : undefined} />
          <MetricRow label="Detected (all drones, all time)" value={typeof vehicleStats?.total_detected === "number" ? vehicleStats.total_detected : null} />
        </Panel>

        {/* ── C: Analytics Engine Status ── */}
        <Panel className="col-span-1">
          <SectionHead
            icon={<BarChart2 size={13} className="text-teal-400" />}
            label="Analytics Engines"
          />

          <div className="flex flex-col gap-2 mt-1">
            {engineRoster.map(({ key, label }) => {
              const svcHealth = modelsStatus?.[key];
              const known = svcHealth && typeof svcHealth === "object";
              const isRunning = svcHealth?.stream_running === true;
              const stateLabel = !known ? "—" : isRunning ? "RUNNING" : svcHealth?.status === "ok" ? "STANDBY" : "UNAVAILABLE";
              return (
                <div key={label} className="flex justify-between items-center">
                  <span className="mono text-[11px] flex items-center gap-1" style={{ color: "var(--dvas-text-muted)" }}>
                    <span className={`w-1.5 h-1.5 rounded-full ${!known ? "bg-slate-500" : isRunning ? "bg-emerald-400" : "bg-amber-400/80"}`} />
                    {label}
                  </span>
                  <span className="mono font-semibold text-xs" style={{ color: "var(--dvas-text)" }}>
                    {stateLabel}
                  </span>
                </div>
              );
            })}
          </div>
        </Panel>

        {/* ── D: Live ANPR Feed ── */}
        <Panel className="col-span-1">
          <SectionHead icon={<Camera size={13} className="text-blue-400" />} label="Live ANPR Feed" right="Scanning" />
          <div className="flex flex-col gap-1.5 overflow-y-auto max-h-[160px] pr-1 dvas-scrollbar">
            {livePlates.length > 0 ? (
              livePlates.slice(0, 15).map((v, i) => {
                const pText = v.plate_text || v.plate || "—";
                const isMatch = v.is_watchlist || v.is_stolen || v.match;
                const vType = v.vehicle_type || v.type || "Vehicle";
                const timeLabel = fmtAge(v.detected_at || v.created_at || v.timestamp);
                return (
                  <div
                    key={v.id || i}
                    onClick={() => setStolenSearch(pText)}
                    className="flex justify-between items-center px-2 py-1.5 rounded cursor-pointer transition-all hover:bg-slate-800"
                    style={{ background: isMatch ? "var(--dvas-crit-bg)" : "var(--dvas-surface-2)" }}
                    title="Click to inspect in Stolen Vehicle Match"
                  >
                    <div>
                      <div className={`mono font-semibold text-[11px] ${isMatch ? "text-red-500 font-bold" : ""}`} style={{ color: isMatch ? "var(--dvas-crit)" : "var(--dvas-text)" }}>
                        {pText}
                      </div>
                      <div className="mono text-[9px]" style={{ color: "var(--dvas-text-dim)" }}>{vType}</div>
                    </div>
                    <div className="text-right">
                      <div className="mono text-[9px]" style={{ color: "var(--dvas-text-dim)" }}>{timeLabel}</div>
                      {isMatch && <span className="mono text-[8px] font-bold text-red-400 uppercase">WANTED</span>}
                    </div>
                  </div>
                );
              })
            ) : (
              <div className="flex flex-col items-center justify-center py-6 text-center text-slate-500">
                <Camera size={20} className="mb-1 opacity-40 text-blue-400 animate-pulse" />
                <span className="mono text-[10px]">Scanning active feed…</span>
                <span className="mono text-[8px] text-slate-600 mt-0.5">Awaiting plate optical recognition</span>
              </div>
            )}
          </div>
        </Panel>

        {/* ── E: Subject Tracking & Biometrics ── */}
        <Panel className="col-span-1 md:col-span-2 xl:col-span-2">
          <SectionHead icon={<UserIcon size={13} className="text-pink-400" />} label="Subject Tracking & Biometrics" />
          <div className="flex gap-2 items-center mb-2 mt-1 px-1">
            <div className="flex-1 flex flex-col items-center p-2 rounded" style={{ background: "var(--dvas-surface-2)", border: "1px solid var(--dvas-border)" }}>
              <div className="mono text-[9px] mb-1" style={{ color: "var(--dvas-text-muted)" }}>PERSONS DETECTED</div>
              <div className="mono font-semibold text-base" style={{ color: "var(--dvas-text)" }}>
                {personCount?.total_persons ?? personCount?.active_tracks ?? "—"}
              </div>
            </div>
            <div className="flex-1 flex flex-col items-center p-2 rounded" style={{ background: "var(--dvas-surface-2)", border: "1px solid var(--dvas-border)" }}>
              <div className="mono text-[9px] mb-1" style={{ color: "var(--dvas-text-muted)" }}>FACES CAPTURED</div>
              <div className="mono font-semibold text-base" style={{ color: "var(--dvas-text)" }}>
                {personCount?.total_faces ?? personCount?.faces_captured ?? "—"}
              </div>
            </div>
          </div>
          <div className="flex gap-2 mt-auto">
            <button
              onClick={() => onOpenGallery("persons")}
              className="flex-1 py-1.5 text-[10px] font-semibold mono rounded transition-colors hover:bg-pink-950/40 hover:border-pink-500/50"
              style={{ background: "var(--dvas-surface-2)", border: "1px solid var(--dvas-border-strong)", color: "var(--dvas-text-muted)" }}
            >
              VIEW PERSONS
            </button>
            <button
              onClick={() => onOpenGallery("faces")}
              className="flex-1 py-1.5 text-[10px] font-semibold mono rounded transition-colors hover:bg-pink-950/40 hover:border-pink-500/50"
              style={{ background: "var(--dvas-surface-2)", border: "1px solid var(--dvas-border-strong)", color: "var(--dvas-text-muted)" }}
            >
              VIEW FACES
            </button>
          </div>
        </Panel>

        {/* ── F: Stolen Vehicle Match ── */}
        <Panel
          borderColor={hasStolenMatch ? "var(--dvas-crit)" : "var(--dvas-border)"}
          className="col-span-1 md:col-span-2 xl:col-span-2"
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

          {hasStolenMatch && matchedRecord ? (
            <div className="flex items-stretch gap-2 mt-1 h-full min-h-[140px]">
              {/* Left: Registry File */}
              <div className="flex-1 flex flex-col justify-between rounded p-2 relative" style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-crit)" }}>
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <span className="mono text-[8px] tracking-wider text-red-400 font-bold">POLICE REGISTRY</span>
                    <span className="mono text-[7px] px-1 bg-red-950 text-red-300 border border-red-800 rounded">WANTED</span>
                  </div>
                  <div className="mono font-bold text-sm text-red-300 mb-1">{matchedRecord.plate_text}</div>
                  <div className="mono text-[10px] text-slate-300 mb-0.5">{matchedRecord.vehicle_type || "Motor Vehicle"}</div>
                  <div className="mono text-[9px] text-slate-400 line-clamp-2">{matchedRecord.reason || "Reported stolen in jurisdiction"}</div>
                </div>
                <div className="mono text-[8px] text-slate-500 border-t border-slate-800 pt-1 mt-1">
                  Alert: {matchedRecord.added_at ? fmtAge(matchedRecord.added_at) : "Active Hotlist"}
                </div>
              </div>

              {/* Right: Live Drone Capture */}
              <div className="flex-1 flex flex-col items-center justify-center rounded p-2 relative overflow-hidden" style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-cyan)" }}>
                {liveHit?.plate_image_url || topTheftAlert?.vehicle_image_url ? (
                  <img
                    src={toProxiedUrl(liveHit?.plate_image_url || topTheftAlert?.vehicle_image_url, "dvas-plates")}
                    alt="Live Capture"
                    className="w-full h-24 object-cover rounded mb-1"
                  />
                ) : liveHit?.image_b64 ? (
                  <img
                    src={`data:image/jpeg;base64,${liveHit.image_b64}`}
                    alt="Live Capture"
                    className="w-full h-24 object-cover rounded mb-1"
                  />
                ) : effectiveDroneId ? (
                  <img
                    src={`${API}/ingest-server/frame/${encodeURIComponent(effectiveDroneId)}/latest`}
                    alt="Live Drone Feed"
                    className="w-full h-24 object-cover rounded mb-1 opacity-80"
                    onError={(e) => { e.target.style.display = "none"; }}
                  />
                ) : (
                  <div className="w-full h-24 flex items-center justify-center bg-slate-900 rounded mb-1">
                    <Camera size={24} className="text-slate-600" />
                  </div>
                )}
                <span className="mono text-[9px] tracking-wider" style={{ color: "var(--dvas-cyan)" }}>
                  LIVE · {effectiveDroneId || "DRONE FEED"}
                </span>
                <div className="absolute inset-0 m-2 flex items-center justify-center pointer-events-none">
                  <div className="w-12 h-12 border-2 rounded-sm" style={{ borderColor: "var(--dvas-crit)", opacity: 0.8 }}></div>
                </div>
              </div>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-full min-h-[140px] text-slate-500">
              <ShieldCheck size={28} className="mb-2 text-emerald-400 opacity-80" />
              <span className="mono text-[10px] tracking-widest text-emerald-400 font-bold">WATCHLIST ARMED</span>
              <span className="mono text-[9px] opacity-70 mt-1">
                {Array.isArray(watchlistData?.watchlist) ? `${watchlist.length} Vehicles Monitored` : "Watchlist —"} · {watchlistHitCount} Active Hits
              </span>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────
   EVENT TIMELINE
   ───────────────────────────────────────────── */
function EventTimeline({ events: allEvents, onPlay }) {
  // Only events from the last 2 h (the label below says so)
  const cutoff = Date.now() - EVENT_WINDOW_MS;
  const events = allEvents.filter((e) => {
    const t = e.ts ? new Date(e.ts).getTime() : NaN;
    return !Number.isNaN(t) && t >= cutoff;
  });

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
                    <span title={fmtIST(evt.ts)}>{fmtAge(evt.ts)}</span>
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
                          style={{ color: evt.confidence == null ? "var(--dvas-text-dim)" : evt.confidence >= 90 ? "var(--dvas-safe)" : "var(--dvas-warn)" }}
                        >
                          {evt.confidence != null ? `${evt.confidence}%` : "—"}
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
                    title={evt.evidence_id ? "View stored evidence for this event" : "No stored clip for this event; shows the drone's live frame"}
                  >
                    {evt.evidence_id ? <Camera size={10} /> : <Eye size={10} />}
                    {evt.evidence_id ? "Evidence" : "Live frame"}
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
function GlobalHeader({ drones, alerts, connected, onNavigateTab, jurisdiction }) {
  const online = drones.filter((d) => d.status === "live").length;
  const activeMissions = new Set(
    drones.map((d) => (d.mission_id == null ? "" : String(d.mission_id).trim())).filter(Boolean)
  ).size;
  const sysAlerts = Array.isArray(alerts) ? alerts.length : "—";

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
          Jurisdiction: <span className="font-bold" style={{ color: "var(--dvas-text)" }}>{jurisdiction || "—"}</span>
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

        {onNavigateTab && (
          <button
            onClick={() => onNavigateTab("models")}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg shrink-0 transition-all hover:bg-teal-500/10 cursor-pointer text-left"
            style={{
              background: "rgba(20,184,166,0.08)",
              border: "1px solid rgba(20,184,166,0.3)",
            }}
            title="Configure Detection Models"
          >
            <Cpu size={12} className="text-teal-400" />
            <div>
              <div className="mono text-[11px] font-bold text-teal-400 flex items-center gap-1">
                Models <ChevronRight size={10} />
              </div>
              <div className="mono text-[8px]" style={{ color: "var(--dvas-text-dim)" }}>GPU Cluster</div>
            </div>
          </button>
        )}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────
   MAIN EXPORT
   ───────────────────────────────────────────── */
export default function MultiDroneView({ drones: liveDrones, alerts: liveAlerts, connected, user, onSelectDrone, onNavigateTab }) {
  const auth = useAuth();
  const currentUser = user || auth?.user || null;
  const canPurge = PURGE_ROLES.includes(currentUser?.role);
  const userJurisdiction = currentUser?.jurisdiction || null;

  // Use active ingest streams to discover real drones and enrich with live metadata
  const { mergeWithDrones, isGridDrone, streams } = useActiveStreams();

  // Start with DB drones (from Postgres, telemetry-pumped)
  // Then merge with real ingest streams (adds missing drones, enriches existing ones)
  const baseList = liveDrones && liveDrones.length > 0 ? liveDrones : [];
  let merged   = useMemo(() => mergeWithDrones(baseList), [streams, baseList, mergeWithDrones]);

  // Filter to active streaming or draining drones: the same rule as the Multi-Drone Grid
  // and its live counter (A5) — a drone whose stream is gone gets no card
  const [purgedDrones, setPurgedDrones] = useState(new Set());
  merged = merged.filter(d => isGridDrone(d) && !purgedDrones.has(d.id));

  const drones = merged.sort((a, b) => {
    const ord = { live: 0, degraded: 1, draining: 2, offline: 3 };
    const ao = ord[a.status] ?? 4; const bo = ord[b.status] ?? 4;
    if (ao !== bo) return ao - bo;
    return (a.id || "").localeCompare(b.id || "");
  });
  // v2 R7: a fixed grid of GRID_TILES; each drone keeps its tile while it has one
  const tiles = useTileSlots(drones, GRID_TILES);
  // v2 R6.4: live / degraded / shedding per live tile
  const analyticsStates = useAnalyticsStates(
    drones.filter((d) => d.status === "live" || d.status === "degraded").map((d) => d.id));

  const events = liveAlerts && liveAlerts.length > 0
    ? liveAlerts.map((a) => ({
        id: a.id,
        ts: a.ts,
        drone_id: a.drone_id,
        mission_id: a.mission_id || "—",
        event_type: (a.type || "Unknown").replace(/_/g, " "),
        confidence: typeof a.confidence === "number" ? Math.round(a.confidence * 100) : null,
        zone: a.zone || "—",
        severity: a.severity || "info",
        evidence_id: a.evidence_id || null,
      }))
    : [];

  const [selectedDroneId, setSelectedDroneId] = useState(null);
  const [playingEvent, setPlayingEvent] = useState(null);
  const [galleryType, setGalleryType] = useState(null); // 'persons' | 'faces' | null
  const [purgeConfirm, setPurgeConfirm] = useState(null);
  const [toast, setToast] = useState(null);

  const showToast = useCallback((kind, msg) => {
    setToast({ kind, msg });
    setTimeout(() => setToast(null), 5000);
  }, []);

  const handlePurgeStream = useCallback(async (droneId) => {
    try {
      await api.post(`/ingest-server/streams/${encodeURIComponent(droneId)}/purge`);
      setPurgedDrones(prev => new Set(prev).add(droneId));
      showToast("ok", `Stream purged for ${droneId}`);
    } catch (e) {
      showToast("error", `Purge failed for ${droneId}: ${apiError(e, "request failed")}`);
    }
  }, [showToast]);

  const handleSelectDrone = useCallback((droneId) => {
    setSelectedDroneId(droneId);
    if (droneId && onSelectDrone) onSelectDrone(droneId);
  }, [onSelectDrone]);

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
      <GlobalHeader drones={drones} alerts={liveAlerts} connected={!!connected} onNavigateTab={onNavigateTab} jurisdiction={userJurisdiction} />

      {/* ── 2. Drone Cards Grid (tiled CSS grid format) ── */}
      <div className="flex-1 overflow-y-auto dvas-scrollbar min-h-[60vh] xl:min-h-[500px]">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-4 transition-all duration-500 ease-in-out h-full content-start pb-4">
          {tiles.map(({ index, drone: d, lastId }) => {
            if (!d) {
              return (
                <div key={`tile-${index}`} className="col-span-1">
                  <OfflineTileCard index={index} lastId={lastId} dimmed={!!selectedDroneId} />
                </div>
              );
            }
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
                  onSelect={handleSelectDrone}
                  onPurge={setPurgeConfirm}
                  canPurge={canPurge}
                  analytics={analyticsStates[d.id]}
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
        effectiveDroneId={selectedDroneId || (drones[0]?.id || "")}
        jurisdiction={userJurisdiction}
      />

      {/* ── 4. Event Timeline ── */}
      <EventTimeline 
        events={selectedDroneId ? events.filter(e => e.drone_id === selectedDroneId) : events} 
        onPlay={setPlayingEvent} 
      />

      {/* ── Evidence Modal: stored evidence image if the event has one, else the live frame (labelled) ── */}
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
                  {playingEvent.drone_id} · {playingEvent.zone} · {fmtIST(playingEvent.ts)}
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

            {/* Video / Snapshot viewport */}
            <div
              className="aspect-video flex flex-col items-center justify-center relative bg-[#040c18] overflow-hidden"
            >
              {playingEvent.evidence_id ? (
                <EvidenceImage
                  evidenceId={playingEvent.evidence_id}
                  role="primary"
                  alt="Stored evidence"
                  className="absolute inset-0 w-full h-full object-contain"
                  onError={(e) => { if (e?.target) e.target.style.display = "none"; }}
                />
              ) : playingEvent.drone_id ? (
                <img
                  src={`${API}/ingest-server/frame/${encodeURIComponent(playingEvent.drone_id)}/latest`}
                  alt="Live frame"
                  className="absolute inset-0 w-full h-full object-cover opacity-90"
                  onError={(e) => { e.target.style.display = "none"; }}
                />
              ) : null}
              <div className="absolute bottom-2 left-2 z-10 flex flex-col bg-black/70 px-3 py-1.5 rounded-lg backdrop-blur-md border border-teal-500/30">
                {playingEvent.evidence_id ? (
                  <div className="mono text-[11px] text-teal-300 font-semibold">Stored evidence · {playingEvent.evidence_id}</div>
                ) : (
                  <div className="mono text-[11px] text-amber-300 font-semibold">Live frame (no stored clip) · {playingEvent.drone_id || "—"}</div>
                )}
                <div className="mono text-[9px] text-slate-400 mt-0.5">{playingEvent.event_type} · {playingEvent.zone}</div>
              </div>
            </div>

            {/* Metadata row */}
            <div
              className="px-4 py-2.5 flex items-center gap-4 flex-wrap"
              style={{ borderTop: "1px solid var(--dvas-border)" }}
            >
              <span className="mono text-[10px] text-slate-500">
                Confidence:{" "}
                <span className="text-emerald-400 font-bold">{playingEvent.confidence != null ? `${playingEvent.confidence}%` : "—"}</span>
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
        <BiometricsGalleryModal
          galleryType={galleryType}
          onClose={() => setGalleryType(null)}
          effectiveDroneId={selectedDroneId || (drones[0]?.id || "")}
        />
      )}

      {/* ── Purge Confirmation Modal ── */}
      {purgeConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
          <div className="bg-gray-900 border border-red-800/50 rounded-xl p-6 max-w-md mx-4 shadow-2xl">
            <div className="flex items-center gap-3 mb-4">
              <AlertTriangle className="w-6 h-6 text-red-400" />
              <h3 className="text-lg font-semibold text-white">Purge Stream</h3>
            </div>
            <p className="text-gray-300 text-sm mb-2">
              This will permanently delete all queued frames for drone <span className="font-mono text-amber-300">{purgeConfirm}</span>.
            </p>
            <p className="text-gray-400 text-xs mb-6">
              Any unprocessed analytics data will be lost. This action cannot be undone.
            </p>
            <div className="flex justify-end gap-3">
              <button onClick={() => setPurgeConfirm(null)}
                className="px-4 py-2 text-sm text-gray-400 hover:text-white transition-colors">
                Cancel
              </button>
              <button onClick={() => { handlePurgeStream(purgeConfirm); setPurgeConfirm(null); }}
                className="px-4 py-2 text-sm bg-red-600 hover:bg-red-500 text-white rounded-lg transition-colors">
                Purge Stream
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Toast Notification ── */}
      {toast && (
        <div
          role="status"
          className={`fixed bottom-6 right-6 z-[100] px-4 py-3 rounded-lg shadow-xl text-sm flex items-center gap-2 animate-fade-in border ${
            toast.kind === "error"
              ? "bg-red-900/90 border-red-600/40 text-red-200"
              : "bg-emerald-900/90 border-emerald-600/40 text-emerald-200"
          }`}
        >
          {toast.kind === "error" ? <AlertTriangle className="w-4 h-4" /> : <Check className="w-4 h-4" />} {toast.msg}
        </div>
      )}

    </div>
  );
}
