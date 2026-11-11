import React, { useState, useEffect } from "react";
import { useCommand } from "@/context/CommandContext";
import { useI18n } from "@/context/I18nContext";
import LiveMap from "@/components/LiveMap";
import { useActiveStreams, framesLeft } from "@/hooks/useActiveStreams";
import { useJpegPoller } from "@/hooks/useJpegPoller";
import { useLiveVideo } from "@/hooks/useLiveVideo";
import { API } from "@/lib/api";
import {
  MapPin,
  Radio,
  RefreshCw,
  User as UserIcon,
} from "lucide-react";

// After a 404 (stream gone, or drone outside the user's jurisdiction) retry every 30 s, not 3 s
const TILE_404_RETRY_MS = 30000;

function MiniStreamCell({ drone }) {
  const isDegraded = drone.status === "degraded";
  const isDraining = drone.status === "draining";
  // A5: only live tiles poll raw frames; draining and offline tiles show a placeholder
  const isLive = drone.status === "live" || isDegraded;
  const isOffline = !isLive && !isDraining;
  const left = framesLeft(drone);

  // v2: continuous H.264 when the edge streams video; the JPEG feed only while it does not
  const live = useLiveVideo(drone.id, isLive, { sub: true });     // small tile: 720p sub-stream
  const pollJpeg = isLive && !live.playing;
  const url = pollJpeg ? `${API}/ingest-server/frame/${encodeURIComponent(drone.id)}/latest` : null;
  const { blobUrl, hasFrame, feedDead, notFound } = useJpegPoller(url, 800, pollJpeg, { notFoundRetryMs: TILE_404_RETRY_MS });
  const noAccess = notFound === "no_access";
  const hasPicture = live.playing || hasFrame;
  const feedDown = !live.playing && feedDead;
  const [lastSeen, setLastSeen] = useState(null);

  useEffect(() => {
    if (hasPicture) setLastSeen(new Date());
  }, [hasPicture, blobUrl]);

  const statusColor = isDraining ? "#fbbf24" : isOffline || feedDown ? "#ef4444" : isDegraded ? "#f59e0b" : "#10b981";
  const borderColor = isDraining || isDegraded
    ? "border-amber-500/40"
    : isOffline || feedDown
    ? "border-red-500/30"
    : hasPicture
    ? "border-teal-500/40"
    : "border-slate-700/60";

  const relAge = (d) => {
    if (!d) return null;
    const s = Math.floor((Date.now() - d.getTime()) / 1000);
    if (s < 60) return `${s}s ago`;
    return `${Math.floor(s / 60)}m ago`;
  };

  const batColor = (pct) => {
    if (pct == null) return "text-slate-500";
    if (pct <= 20) return "text-red-400";
    if (pct <= 50) return "text-amber-400";
    return "text-emerald-400";
  };

  return (
    <div
      className={`relative rounded-lg overflow-hidden border ${borderColor} flex flex-col`}
      style={{ background: "var(--dvas-surface-2)", aspectRatio: "16/10" }}
      data-testid={`grid-drone-${drone.id}`}
    >
      <div className="relative flex-1 overflow-hidden bg-[#060e1a]">
        <div
          className="absolute inset-0 pointer-events-none z-10"
          style={{
            backgroundImage: "repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(20,184,166,0.012) 2px, rgba(20,184,166,0.012) 4px)",
          }}
        />

        {isLive && (
          <video
            ref={live.videoRef}
            muted
            autoPlay
            playsInline
            className="absolute inset-0 w-full h-full object-cover"
            style={{ opacity: live.playing ? (isDegraded ? 0.75 : 0.92) : 0 }}
          />
        )}
        {isLive && live.playing ? null : isLive && hasFrame && blobUrl ? (
          <img
            src={blobUrl}
            alt={`Feed ${drone.id}`}
            className="absolute inset-0 w-full h-full object-cover"
            style={{ opacity: isDegraded ? 0.75 : 0.92 }}
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center">
            {isDraining ? (
              <div className="mono text-[9px] text-amber-300 bg-black/70 px-2 py-1 rounded border border-amber-500/30">
                DRAINING — {left} {left === 1 ? "frame" : "frames"} left
              </div>
            ) : noAccess ? (
              <div
                className="mono text-[9px] text-slate-300 bg-black/70 px-2 py-1 rounded border border-slate-500/30"
                title="This drone is outside your jurisdiction"
              >
                NO ACCESS
              </div>
            ) : isOffline || feedDown ? (
              <div className="mono text-[9px] text-red-400 bg-black/70 px-2 py-1 rounded border border-red-500/30">
                FEED OFFLINE
              </div>
            ) : (
              <div className="flex flex-col items-center gap-1">
                <div className="w-4 h-4 border-2 border-teal-500/40 border-t-teal-400 rounded-full animate-spin" />
                <span className="mono text-[8px] text-slate-500">AWAITING…</span>
              </div>
            )}
          </div>
        )}

        <div className="absolute top-1.5 left-1.5 z-20 flex flex-col gap-0.5">
          <div
            className="mono text-[10px] font-bold px-1.5 py-0.5 rounded"
            style={{
              background: "rgba(6,14,26,0.85)",
              color: isDegraded ? "#fbbf24" : "#2dd4bf",
              border: `1px solid ${isDegraded ? "rgba(245,158,11,0.3)" : "rgba(20,184,166,0.3)"}`,
            }}
          >
            {drone.id}
          </div>
          {drone.mission_id && (
            <div
              className="mono text-[8px] px-1 py-0.5 rounded"
              style={{ background: "rgba(6,14,26,0.75)", color: "#94a3b8" }}
            >
              {drone.mission_id}
            </div>
          )}
        </div>

        <div className="absolute top-1.5 right-1.5 z-20 flex flex-col items-end gap-1">
          <div className="flex items-center gap-1">
            {hasPicture && !feedDown && (
              <span
                className="w-1.5 h-1.5 rounded-full"
                style={{ background: isDraining ? "#fbbf24" : "#10b981", animation: "pulse 1.2s infinite" }}
              />
            )}
            <span
              className="mono text-[8px] px-1.5 py-0.5 rounded font-bold uppercase"
              style={{
                background: "rgba(6,14,26,0.80)",
                color: statusColor,
                border: `1px solid ${statusColor}30`,
              }}
            >
              {isDraining ? "DRAINING" : isOffline || feedDown ? "OFFLINE" : isDegraded ? "DEGRADED" : "LIVE"}
            </span>
          </div>
        </div>
      </div>

      <div
        className="px-2 py-1 flex flex-col gap-0.5 shrink-0"
        style={{ background: "rgba(4,10,20,0.92)", borderTop: "1px solid rgba(255,255,255,0.05)" }}
      >
        <div className="flex items-center gap-1 min-w-0">
          <UserIcon size={8} className="text-slate-500 shrink-0" />
          <span className="mono text-[8px] text-slate-300 truncate">
            {drone.operator_name || "—"}
          </span>
          {drone.battery_pct != null && (
            <span className={`mono text-[8px] ml-auto shrink-0 font-bold ${batColor(drone.battery_pct)}`}>
              {drone.battery_pct}%
            </span>
          )}
        </div>
        <div className="flex items-center gap-1 min-w-0">
          <MapPin size={7} className="text-slate-600 shrink-0" />
          <span className="mono text-[7px] text-slate-500 truncate">
            {drone.jurisdiction || drone.mission_name || "—"}
          </span>
          {lastSeen && (
            <span className="mono text-[7px] text-teal-600 ml-auto shrink-0">{relAge(lastSeen)}</span>
          )}
        </div>
      </div>
    </div>
  );
}

function MultiDroneGrid({ dbDrones, selectedDroneId, onSelectDrone }) {
  const { mergeWithDrones, isGridDrone, loading, streams } = useActiveStreams();
  const merged = mergeWithDrones(dbDrones);
  // A5: the counter and the tiles use the same rule (stream present and live/degraded/draining)
  const activeDrones = merged.filter(isGridDrone);
  const drainingCount = activeDrones.filter((d) => d.status === "draining").length;
  const liveCount = activeDrones.length - drainingCount;
  const slots = activeDrones.slice(0, 4);

  return (
    <div className="p-2 flex flex-col gap-2 h-full">
      <div className="flex items-center justify-between px-1">
        <div className="flex items-center gap-1.5">
          <Radio size={10} className="text-teal-400" style={{ animation: liveCount > 0 ? "pulse 1.5s infinite" : "none" }} />
          <span className="mono text-[9px] text-teal-400 font-bold">
            {liveCount} LIVE {liveCount === 1 ? "STREAM" : "STREAMS"}
          </span>
          {drainingCount > 0 && (
            <span className="mono text-[9px] text-amber-400 font-bold">· {drainingCount} DRAINING</span>
          )}
        </div>
        {loading && <RefreshCw size={9} className="text-slate-500 animate-spin" />}
      </div>

      <div className="grid grid-cols-2 gap-2 flex-1">
        {slots.map((d) => (
          <div
            key={d.id}
            onClick={() => onSelectDrone && onSelectDrone(d.id === selectedDroneId ? null : d.id)}
            className={`cursor-pointer transition-all rounded-lg overflow-hidden ${
              d.id === selectedDroneId ? "ring-2 ring-cyan-400 ring-offset-1 ring-offset-slate-900" : "hover:ring-1 hover:ring-slate-600"
            }`}
          >
            <MiniStreamCell drone={d} />
          </div>
        ))}
        {Array.from({ length: Math.max(0, 4 - slots.length) }).map((_, i) => (
          <div
            key={`empty-${i}`}
            className="rounded-lg border border-slate-800/60 flex flex-col items-center justify-center gap-2"
            style={{ background: "var(--dvas-surface-2)", aspectRatio: "16/10" }}
          >
            <Radio size={16} className="text-slate-700" />
            <span className="mono text-[8px] text-slate-600">AWAITING STREAM</span>
          </div>
        ))}
      </div>

      {streams.length > 0 && (
        <div className="flex justify-center">
          <span className="mono text-[7px] text-slate-600 tracking-wider">
            {streams.reduce((acc, s) => acc + (s.frame_count ?? s.frames_received ?? 0), 0).toLocaleString()} FRAMES INGESTED
          </span>
        </div>
      )}
    </div>
  );
}

export default function TacticalMapPage() {
  const { t } = useI18n();
  const {
    drones,
    mergedDrones,
    liveMissionsCount,
    connected,
    selectedDrone,
    setSelectedDrone
  } = useCommand();

  return (
    <div className="grid grid-cols-12 gap-4">
      <div
        className="col-span-12 lg:col-span-8 rounded-lg overflow-hidden"
        style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)", boxShadow: "var(--dvas-shadow)" }}
      >
        <div className="p-3.5 flex items-center justify-between" style={{ borderBottom: "1px solid var(--dvas-border)" }}>
          <div className="flex items-center gap-2 text-sm font-semibold" style={{ color: "var(--dvas-text)" }}>
            <MapPin size={16} className="text-sky-500" /> {t("cmd.page.map")}
            <span className={`ml-2 mono text-[10px] flex items-center gap-1 ${connected ? "text-emerald-500" : "text-slate-400"}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${connected ? "bg-emerald-500 dvas-live-dot" : "bg-slate-600"}`} />
              {connected ? t("cmd.wsLive") : t("cmd.connecting")}
            </span>
          </div>
          <div className="flex items-center gap-3">
            <div className="mono text-[10px] text-emerald-400 font-semibold flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
              {liveMissionsCount} {liveMissionsCount === 1 ? "Live Mission" : "Live Missions"} on Map
            </div>
            <div className="mono text-[10px] text-slate-500">{drones.length} {t("cmd.dronesInScope")}</div>
          </div>
        </div>
        <LiveMap drones={mergedDrones} height={520} selectedDroneId={selectedDrone} />
      </div>

      <div
        className="col-span-12 lg:col-span-4 rounded-lg overflow-hidden flex flex-col"
        style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)", boxShadow: "var(--dvas-shadow)" }}
      >
        <div className="p-3.5 text-sm flex items-center gap-2 font-semibold shrink-0" style={{ borderBottom: "1px solid var(--dvas-border)", color: "var(--dvas-text)" }}>
          <Radio size={14} className="text-emerald-500 dvas-live-dot" /> {t("cmd.multiDroneGrid")}
        </div>
        <div className="flex-1 min-h-0">
          <MultiDroneGrid dbDrones={drones} selectedDroneId={selectedDrone} onSelectDrone={setSelectedDrone} />
        </div>
      </div>
    </div>
  );
}
