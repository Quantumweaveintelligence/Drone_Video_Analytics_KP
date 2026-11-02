/**
 * VesselAnalytics.js
 * ─────────────────────────────────────────────────────────────────────────────
 * Tactical Maritime Vessel Analytics & Perception Interface
 * Kerala Police | Drone Video Analytics System
 *
 * End-to-end integration with SeaDrone Maritime Perception Engine (Port 8106):
 *  - Real-time zero-flicker vessel tracking & multi-class taxonomy detection
 *  - In-frame live count & cumulative unique vessel aggregation
 *  - Telemetry-free relative travel direction & stationary/anchored classification
 *  - Proximity clustering alerts & spatial density heatmap overlay
 *  - Sortable vessel ledger with MinIO thumbnail inspection & CSV exports
 */

import React, { useState, useEffect, useRef, useMemo, useCallback } from "react";
import {
  Ship, Anchor, Navigation, Compass, AlertTriangle, ShieldCheck,
  Download, RefreshCw, Play, Square, Eye, Layers, Filter, Search,
  TrendingUp, Radio, Activity, Clock, ZoomIn, CheckCircle2, ChevronRight, X, Cpu
} from "lucide-react";
import {
  PieChart, Pie, Cell, ResponsiveContainer, Tooltip as RechartsTooltip,
  BarChart, Bar, XAxis, YAxis
} from "recharts";
import { api, authFetch } from "@/lib/api";
import { useJpegPoller } from "@/hooks/useJpegPoller";
import { useActiveStreams } from "@/hooks/useActiveStreams";

const resolveVesselThumbnail = (key) => {
  if (!key) return null;
  const cleanKey = String(key).replace(/^\/+/, '');
  // Auth comes from the httpOnly dvas_access_token cookie (same origin); no token in the URL.
  return `/api/minio/image?bucket=dvas-vessels-maritime&key=${encodeURIComponent(cleanKey)}`;
};

// ── 9-Class High-Contrast Marine Palette ───────────────────────────────────────
const CLASS_COLORS = {
  "Fishing Boat / Trawler": "#00E5FF",     // Electric Cyan
  "Cargo / Merchant Ship": "#E040FB",      // Bright Magenta
  "Container Ship": "#7C4DFF",             // Deep Purple
  "Passenger Ship / Ferry": "#00B0FF",     // Royal Blue
  "Speedboat / Jet Ski": "#FF4081",        // Neon Pink
  "Sailboat / Yacht": "#69F0AE",           // Mint Green
  "Small Boat": "#76FF03",                 // Lime Green
  "Person": "#00E676",                     // Emerald Green
  "Life Vest / Floater / Buoy": "#FF9100", // Vibrant Orange
  "Default": "#90CAF9"
};

export default function VesselAnalytics({ drones, droneId: propDroneId, onNavigateTab }) {
  const { streams, loading: dronesLoading } = useActiveStreams();
  const activeDrones = (streams && streams.length > 0
    ? streams.map(s => ({
        drone_id: s.drone_id || s.id || '',
        mission_id: s.mission_id,
        status: s.status,
        messages_ready: s.messages_ready ?? 0,
        consumer_lag: s.consumer_lag,
        frames_received: s.status === 'live' || (s.messages_ready || 0) > 0 ? 1 : 0
      }))
    : (drones && drones.length > 0
        ? drones.filter(d => (d.status === 'live' || d.status === 'draining') && !/^DRN-00[1-5]$/i.test(d.id || d.drone_id)).map(d => ({
            drone_id: d.drone_id || d.id || '',
            mission_id: d.mission_id,
            status: d.status,
            messages_ready: d.messages_ready ?? 0,
            consumer_lag: d.consumer_lag,
            frames_received: d.status === 'live' || (d.messages_ready || 0) > 0 ? 1 : 0
          }))
        : [])
  ).filter(d => Boolean(d.drone_id));

  const [activeDrone, setActiveDrone] = useState(propDroneId || null);
  const [activeMissionId, setActiveMissionId] = useState(null);

  // Auto-select first active drone streaming from ingest telemetry
  useEffect(() => {
    if (activeDrones.length === 0) return;
    setActiveDrone(prev => {
      if (prev && activeDrones.find(d => (d.drone_id || d.id) === prev)) return prev;
      const live = activeDrones.find(d => (d.frames_received || 0) > 0);
      return (live || activeDrones[0])?.drone_id || (live || activeDrones[0])?.id || null;
    });
  }, [activeDrones]);

  // Sync current mission_id for the selected drone
  useEffect(() => {
    if (!activeDrone || !activeDrones.length) return;
    const drone = activeDrones.find(d => (d.drone_id || d.id) === activeDrone);
    const missionId = drone?.mission_id || null;
    setActiveMissionId(prev => (prev !== missionId ? missionId : prev));
  }, [activeDrone, activeDrones]);

  // ── State ──────────────────────────────────────────────────────────────────
  const [streamRunning, setStreamRunning] = useState(true);
  const [togglingStream, setTogglingStream] = useState(false);
  const [showHeatmap, setShowHeatmap] = useState(false);

  // Data state
  const [tracks, setTracks] = useState([]);
  const [summaryStats, setSummaryStats] = useState({
    live_count: 0,
    cumulative_count: 0,
    stationary_count: 0,
    moving_count: 0,
    proximity_alerts_24h: 0
  });
  const [classStats, setClassStats] = useState([]);
  const [events, setEvents] = useState([]);
  const [heatmapPoints, setHeatmapPoints] = useState([]);

  // Table filtering & sorting
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedClassFilter, setSelectedClassFilter] = useState("ALL");
  const [selectedMotionFilter, setSelectedMotionFilter] = useState("ALL");
  const [selectedSizeFilter, setSelectedSizeFilter] = useState("ALL");
  const [sortField, setSortField] = useState("last_seen");
  const [sortAsc, setSortAsc] = useState(false);

  // Inspection modal
  const [inspectedTrack, setInspectedTrack] = useState(null);

  // ── Two-Layer Live Feeds ───────────────────────────────────────────────────
  const hasDrone = Boolean(activeDrone);
  const rawUrl = hasDrone
    ? `/api/ingest-server/frame/${encodeURIComponent(activeDrone)}/latest?drone_id=${encodeURIComponent(activeDrone)}`
    : null;
  const annotatedUrl = hasDrone
    ? `/api/analytics/vessel/annotated?drone_id=${encodeURIComponent(activeDrone)}`
    : null;

  // Layer 1: Raw Ingest Feed (1500ms)
  const { blobUrl: rawSrc, ok: rawOk } = useJpegPoller(rawUrl, 1500, hasDrone);

  // Layer 2: Annotated Overlay (800ms when stream running)
  const {
    blobUrl: annotatedSrc,
    ok: annotatedOk,
    meta: annotatedMeta,
    fps,
  } = useJpegPoller(annotatedUrl, 800, hasDrone && streamRunning);

  const displaySrc = annotatedOk ? annotatedSrc : rawSrc;
  const isAnnotated = annotatedOk && Boolean(annotatedSrc);
  const hasAnyFeed = Boolean(displaySrc);

  // Poll health of vessel stream worker from microservice
  useEffect(() => {
    const check = async () => {
      try {
        const res = await api.get('/analytics/vessel/health');
        if (res && res.stream_running !== undefined) {
          setStreamRunning(Boolean(res.stream_running));
        }
      } catch { /* ignore */ }
    };
    check();
    const id = setInterval(check, 6000);
    return () => clearInterval(id);
  }, []);

  // ── Periodic Data Fetching ─────────────────────────────────────────────────
  const fetchData = useCallback(async () => {
    if (!activeDrone) return;
    try {
      const msnParam = activeMissionId ? `&mission_id=${encodeURIComponent(activeMissionId)}` : "";

      const [trkRes, statRes, clsRes, evRes] = await Promise.allSettled([
        api.get(`/analytics/vessel/tracks?drone_id=${encodeURIComponent(activeDrone)}${msnParam}&limit=100`),
        api.get(`/analytics/vessel/stats?drone_id=${encodeURIComponent(activeDrone)}${msnParam}`),
        api.get(`/analytics/vessel/stats/by-class?drone_id=${encodeURIComponent(activeDrone)}${msnParam}`),
        api.get(`/analytics/vessel/events?drone_id=${encodeURIComponent(activeDrone)}${msnParam}&limit=30`),
      ]);

      // Helper: safely parse JSON fields that may arrive as strings (asyncpg jsonb)
      const safeParse = (v) => {
        if (typeof v === 'string') { try { return JSON.parse(v); } catch { return v; } }
        return v ?? {};
      };

      const trkData = trkRes.status === "fulfilled" ? (trkRes.value?.data || trkRes.value) : null;
      if (trkData?.tracks) {
        setTracks(trkData.tracks.map(t => ({
          ...t,
          trajectory: safeParse(t.trajectory),
          bbox_xyxy: safeParse(t.bbox_xyxy),
        })));
      }

      const statData = statRes.status === "fulfilled" ? (statRes.value?.data || statRes.value) : null;
      if (statData && !statData.error) {
        setSummaryStats(statData);
      }

      const clsData = clsRes.status === "fulfilled" ? (clsRes.value?.data || clsRes.value) : null;
      if (clsData?.classes) {
        setClassStats(clsData.classes);
      }

      const evData = evRes.status === "fulfilled" ? (evRes.value?.data || evRes.value) : null;
      if (evData?.events) {
        setEvents(evData.events.map(ev => ({
          ...ev,
          details: safeParse(ev.details),
        })));
      }

      if (showHeatmap) {
        const hmRes = await api.get(`/analytics/vessel/heatmap?drone_id=${encodeURIComponent(activeDrone)}`);
        const hmData = hmRes?.data || hmRes;
        if (hmData?.points) {
          setHeatmapPoints(hmData.points);
        }
      }
    } catch (err) {
      console.warn("Vessel analytics data poll warning:", err);
    }
  }, [activeDrone, activeMissionId, showHeatmap]);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 3000);
    return () => clearInterval(interval);
  }, [fetchData]);

  // ── Stream Controls ────────────────────────────────────────────────────────
  const handleToggleStream = async () => {
    setTogglingStream(true);
    try {
      const isRunning = streamRunning;
      const endpoint = isRunning ? "/analytics/vessel/stream/stop" : "/analytics/vessel/stream/start";
      await api.get(endpoint);
      const nextState = !isRunning;
      setStreamRunning(nextState);
      try {
        await api.post('/models/sync-worker', { service: 'vessel', action: nextState ? 'start' : 'stop' });
      } catch (syncErr) {
        console.warn('Vessel model sync error:', syncErr);
      }
    } catch (e) {
      console.error("Failed to toggle maritime stream:", e);
    } finally {
      setTogglingStream(false);
    }
  };

  // ── CSV Export ─────────────────────────────────────────────────────────────
  const exportCSV = () => {
    if (!tracks || tracks.length === 0) return;
    const headers = [
      "TrackID", "DroneID", "Class", "SizeBucket", "Direction",
      "IsStationary", "Confidence", "DurationSec", "FirstSeen", "LastSeen"
    ];
    const rows = tracks.map(t => [
      t.tracker_id,
      t.drone_id,
      `"${t.class_name || ''}"`,
      t.size_bucket || '',
      `"${t.direction || ''}"`,
      t.is_stationary ? "Yes" : "No",
      (t.confidence || 0).toFixed(2),
      (t.duration_s || 0).toFixed(1),
      t.first_seen || '',
      t.last_seen || ''
    ]);

    const csvContent = "data:text/csv;charset=utf-8," +
      [headers.join(","), ...rows.map(e => e.join(","))].join("\n");
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", `vessel_tracks_${activeDrone}_${Date.now()}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  // ── Filtered & Sorted Tracks ───────────────────────────────────────────────
  const filteredTracks = useMemo(() => {
    return tracks.filter(t => {
      // Search
      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        const matchesId = String(t.tracker_id).includes(q);
        const matchesClass = (t.class_name || "").toLowerCase().includes(q);
        if (!matchesId && !matchesClass) return false;
      }
      // Class filter
      if (selectedClassFilter !== "ALL" && t.class_name !== selectedClassFilter) {
        return false;
      }
      // Motion filter
      if (selectedMotionFilter === "STATIONARY" && !t.is_stationary) return false;
      if (selectedMotionFilter === "MOVING" && t.is_stationary) return false;
      // Size filter
      if (selectedSizeFilter !== "ALL" && t.size_bucket !== selectedSizeFilter) return false;

      return true;
    }).sort((a, b) => {
      let vA = a[sortField];
      let vB = b[sortField];
      if (typeof vA === "string") vA = vA.toLowerCase();
      if (typeof vB === "string") vB = vB.toLowerCase();
      if (vA < vB) return sortAsc ? -1 : 1;
      if (vA > vB) return sortAsc ? 1 : -1;
      return 0;
    });
  }, [tracks, searchQuery, selectedClassFilter, selectedMotionFilter, selectedSizeFilter, sortField, sortAsc]);

  const handleSort = (field) => {
    if (sortField === field) {
      setSortAsc(!sortAsc);
    } else {
      setSortField(field);
      setSortAsc(false);
    }
  };

  // ── Heatmap Canvas Drawing ─────────────────────────────────────────────────
  const heatmapCanvasRef = useRef(null);
  useEffect(() => {
    if (!showHeatmap || !heatmapCanvasRef.current || heatmapPoints.length === 0) return;
    const canvas = heatmapCanvasRef.current;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Draw radial glowing gradient blobs for each detection point
    heatmapPoints.forEach(([x, y]) => {
      // Scale from 1280x720 video space to canvas size
      const scaleX = canvas.width / 1280;
      const scaleY = canvas.height / 720;
      const cx = x * scaleX;
      const cy = y * scaleY;

      const grad = ctx.createRadialGradient(cx, cy, 2, cx, cy, 28);
      grad.addColorStop(0, "rgba(255, 60, 0, 0.7)");
      grad.addColorStop(0.5, "rgba(255, 200, 0, 0.35)");
      grad.addColorStop(1, "rgba(0, 200, 255, 0)");

      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(cx, cy, 28, 0, 2 * Math.PI);
      ctx.fill();
    });
  }, [showHeatmap, heatmapPoints]);

  return (
    <div className="w-full space-y-5 text-slate-100 font-sans">

      {/* ── Subheader / Control Toolbar ─────────────────────────────────────── */}
      <div className="flex flex-wrap items-center justify-between gap-3 p-3.5 bg-slate-900/90 border border-slate-800 rounded-xl shadow-lg">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-cyan-950/60 border border-cyan-500/40 rounded-lg text-cyan-400">
            <Anchor size={20} className="animate-pulse" />
          </div>
          <div>
            <h2 className="text-base font-bold tracking-tight text-white flex items-center gap-2">
              Maritime Vessel Analytics & Tracking
              <span className="px-2 py-0.5 text-[10px] font-mono tracking-wider bg-cyan-900/50 text-cyan-300 border border-cyan-700/50 rounded-full">
                SEADRONE YOLOV8
              </span>
            </h2>
            <p className="text-xs text-slate-400">
              Zero-flicker coastal perimeter surveillance & telemetry-free motion trajectory profiling
            </p>
          </div>
        </div>

        {/* Dynamic Drone Selector Pills from Ingest Server Telemetry */}
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="mono text-[10px] text-slate-400 mr-1 uppercase font-semibold">Drone Stream:</span>
          {dronesLoading && activeDrones.length === 0 && (
            <span className="mono text-[10px] text-slate-500 flex items-center gap-1">
              <RefreshCw size={10} className="animate-spin" /> Discovering drones…
            </span>
          )}
          {activeDrones.map(d => {
            const dId = d?.drone_id || d?.id || '';
            if (!dId) return null;
            const isLive = (d.frames_received || 0) > 0;
            const isSelected = activeDrone === dId;
            const isDraining = d.status === 'draining';
            const displayId = typeof dId === 'string'
              ? dId.replace(/DRONE_KL_/i, 'DRN-').replace(/DRONE_/i, 'DRN-')
              : String(dId);
            return (
              <button
                key={dId}
                onClick={() => setActiveDrone(dId)}
                className="mono text-[10px] px-2.5 py-1 rounded transition-all flex items-center gap-1.5 cursor-pointer"
                title={`${dId} — ${isLive ? 'streaming' : 'no frames'}`}
                style={{
                  background: isSelected ? 'rgba(6,182,212,0.18)' : 'var(--dvas-surface-2, #1e293b)',
                  color: isSelected ? '#22d3ee' : isLive ? 'var(--dvas-text, #f1f5f9)' : 'var(--dvas-text-muted, #94a3b8)',
                  border: `1px solid ${isSelected ? 'rgba(6,182,212,0.5)' : 'var(--dvas-border, #334155)'}`,
                }}
              >
                {isLive && !isDraining && (
                  <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 dvas-live-dot shrink-0" />
                )}
                {isDraining && (
                  <span className="w-1.5 h-1.5 rounded-full bg-amber-400 dvas-live-dot shrink-0" />
                )}
                <span className="font-semibold">{displayId}</span>
                {isDraining && (
                  <span className="text-[8px] bg-amber-800/60 text-amber-300 px-1 py-0.5 rounded font-bold">DRAINING</span>
                )}
              </button>
            );
          })}
          {!dronesLoading && activeDrones.length === 0 && (
            <span className="mono text-[10px] text-slate-500">No active drone feeds in ingest</span>
          )}
        </div>

        <div className="flex items-center gap-2">
          {/* Heatmap Toggle */}
          <button
            onClick={() => setShowHeatmap(!showHeatmap)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-all ${
              showHeatmap
                ? "bg-amber-500/20 border-amber-500/60 text-amber-300 shadow-md shadow-amber-950/40"
                : "bg-slate-800/80 border-slate-700 text-slate-300 hover:bg-slate-700/80"
            }`}
          >
            <Layers size={14} />
            <span>{showHeatmap ? "Heatmap ON" : "Density Heatmap"}</span>
          </button>

          {/* Stream Start/Stop */}
          <button
            onClick={handleToggleStream}
            disabled={togglingStream}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border transition-all ${
              streamRunning
                ? "bg-rose-950/40 border-rose-600/60 text-rose-300 hover:bg-rose-900/50"
                : "bg-emerald-950/40 border-emerald-600/60 text-emerald-300 hover:bg-emerald-900/50"
            }`}
          >
            {streamRunning ? <Square size={14} /> : <Play size={14} />}
            <span>{togglingStream ? "Updating..." : streamRunning ? "Stop Stream" : "Start Stream"}</span>
          </button>

          {onNavigateTab && (
            <button
              onClick={() => onNavigateTab("models")}
              className="mono text-[10px] px-2.5 py-1.5 rounded-lg flex items-center gap-1 transition-all cursor-pointer text-slate-400 hover:text-sky-300 hover:bg-slate-800"
              style={{ border: '1px solid var(--dvas-border, #334155)' }}
              title="Configure models in Detection Models"
            >
              <Cpu size={12} /> Models
            </button>
          )}

          {/* Export CSV */}
          <button
            onClick={exportCSV}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 border border-slate-700 hover:bg-slate-700 text-slate-200 rounded-lg text-xs font-medium transition-all"
          >
            <Download size={14} />
            <span>Export CSV</span>
          </button>
        </div>
      </div>

      {/* Draining Queue Banner */}
      {(() => {
        const selectedDroneObj = activeDrones.find(d => (d.drone_id || d.id) === activeDrone);
        const remaining = selectedDroneObj?.consumer_lag
          ? Object.values(selectedDroneObj.consumer_lag).reduce((sum, v) => sum + v, 0)
          : (selectedDroneObj?.messages_ready ?? 0);
        if (selectedDroneObj?.status === 'draining') {
          return (
            <div className="px-4 py-2 bg-amber-900/40 border border-amber-500/30 rounded-xl flex items-center justify-center gap-2">
              {remaining > 0 ? (
                <>
                  <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
                  <span className="mono text-[11px] font-bold text-amber-300 tracking-wider">
                    DRAINING — {remaining} frames remaining in queue for {activeDrone}
                  </span>
                </>
              ) : (
                <>
                  <span className="w-2 h-2 rounded-full bg-emerald-400" />
                  <span className="mono text-[11px] font-bold text-emerald-400 tracking-wider">
                    PROCESSING COMPLETE FOR {activeDrone}
                  </span>
                </>
              )}
            </div>
          );
        }
        return null;
      })()}

      {/* ── KPI Strip Cards ─────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3.5">
        {/* Live in Frame */}
        <div className="p-3.5 bg-slate-900/80 border border-cyan-900/40 rounded-xl shadow-md relative overflow-hidden">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-cyan-400">Live In Frame</span>
            <span className="w-2 h-2 rounded-full bg-cyan-400 animate-ping"></span>
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-2xl font-black tracking-tight text-white">
              {annotatedMeta?.vesselCount ?? summaryStats.live_count ?? 0}
            </span>
            <span className="text-xs text-slate-400">vessels detected</span>
          </div>
          <div className="mt-1 text-[11px] text-slate-400">
            FPS: <strong className="text-slate-200 font-mono">{fps || "--"}</strong>
          </div>
        </div>

        {/* Cumulative Unique */}
        <div className="p-3.5 bg-slate-900/80 border border-blue-900/40 rounded-xl shadow-md">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-blue-400">Cumulative Tracks</span>
            <Ship size={15} className="text-blue-400" />
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-2xl font-black tracking-tight text-white">
              {summaryStats.cumulative_count || tracks.length || 0}
            </span>
            <span className="text-xs text-slate-400">unique craft</span>
          </div>
          <div className="mt-1 text-[11px] text-slate-400">
            Total observed this deployment
          </div>
        </div>

        {/* Moving vs Stationary */}
        <div className="p-3.5 bg-slate-900/80 border border-purple-900/40 rounded-xl shadow-md">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-purple-400">Motion Classification</span>
            <Navigation size={15} className="text-purple-400" />
          </div>
          <div className="mt-2 flex items-center justify-between text-xs">
            <span className="text-emerald-400 font-semibold flex items-center gap-1">
              Cruising: {summaryStats.moving_count || 0}
            </span>
            <span className="text-amber-400 font-semibold flex items-center gap-1">
              Anchored: {summaryStats.stationary_count || 0}
            </span>
          </div>
          <div className="mt-2 w-full h-1.5 bg-slate-800 rounded-full overflow-hidden flex">
            <div
              className="bg-emerald-500 h-full"
              style={{
                width: `${(summaryStats.moving_count / Math.max(1, (summaryStats.moving_count + summaryStats.stationary_count))) * 100}%`
              }}
            />
            <div
              className="bg-amber-500 h-full"
              style={{
                width: `${(summaryStats.stationary_count / Math.max(1, (summaryStats.moving_count + summaryStats.stationary_count))) * 100}%`
              }}
            />
          </div>
        </div>

        {/* Proximity / Convergence Alerts */}
        <div className="p-3.5 bg-slate-900/80 border border-amber-900/40 rounded-xl shadow-md">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-amber-400">Proximity Alerts (24h)</span>
            <AlertTriangle size={15} className="text-amber-400" />
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-2xl font-black tracking-tight text-amber-300">
              {summaryStats.proximity_alerts_24h || 0}
            </span>
            <span className="text-xs text-slate-400">convergence events</span>
          </div>
          <div className="mt-1 text-[11px] text-slate-400">
            Separation &lt; 90px threshold
          </div>
        </div>
      </div>

      {/* ── Main Work Area: Video Feed + Right Panel ───────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">

        {/* ── Left Column: Live Annotated Stream & Controls (7 cols) ───────── */}
        <div className="lg:col-span-7 space-y-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-2xl relative">
            {/* Header tab in player */}
            <div className="px-4 py-2.5 bg-slate-950/80 border-b border-slate-800 flex items-center justify-between text-xs">
              <div className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full ${hasAnyFeed ? 'bg-cyan-400 dvas-live-dot' : 'bg-slate-500'}`}></span>
                <span className="font-semibold text-slate-200">TACTICAL PERCEPTION RADAR</span>
                <span className="text-slate-500">|</span>
                <span className="text-cyan-300 font-mono font-semibold">{activeDrone || "DISCOVERING..."}</span>
                {activeMissionId && (
                  <span className="text-[10px] mono text-slate-400 bg-slate-800 px-1.5 py-0.5 rounded">
                    {activeMissionId}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2 text-[11px] text-slate-400 font-mono">
                <span>AGE: {annotatedMeta?.frameAge ? `${annotatedMeta.frameAge}` : "<1s"}</span>
                <span>•</span>
                <span className={hasAnyFeed ? "text-cyan-400" : "text-slate-500"}>
                  {isAnnotated ? (annotatedMeta?.feedStatus === 'offline' ? 'STANDBY' : 'LIVE ANNOTATED') : rawOk ? 'LIVE RAW' : 'OFFLINE'}
                </span>
              </div>
            </div>

            {/* Video Canvas Container (Two-Layer Resilient Display) */}
            <div className="relative aspect-video bg-black flex items-center justify-center overflow-hidden">
              {!hasDrone ? (
                <div className="w-full h-full flex flex-col items-center justify-center gap-3">
                  <RefreshCw size={32} className="text-cyan-600 opacity-40 animate-spin" />
                  <p className="mono text-sm text-slate-400">Discovering active maritime drone…</p>
                  <p className="mono text-[10px] text-slate-500">Reading live stream telemetry from ingest server</p>
                </div>
              ) : hasAnyFeed ? (
                <img
                  src={displaySrc}
                  alt={isAnnotated ? "Maritime Detection Feed" : "Raw Drone Ingest Feed"}
                  className="w-full h-full object-contain"
                />
              ) : (
                <div className="w-full h-full flex flex-col items-center justify-center gap-3 text-center px-4">
                  <Activity size={36} className="text-slate-600 opacity-40" />
                  <p className="mono text-sm text-slate-400 font-bold tracking-wider">FEED OFFLINE — NO ACTIVE STREAM</p>
                  <p className="mono text-[11px] text-slate-500 max-w-sm">
                    Drone <span className="text-cyan-400 font-semibold">{activeDrone}</span> is not transmitting frames to the ingest server.
                  </p>
                </div>
              )}

              {/* Heatmap Overlay Canvas */}
              {showHeatmap && (
                <canvas
                  ref={heatmapCanvasRef}
                  width={1280}
                  height={720}
                  className="absolute inset-0 w-full h-full pointer-events-none mix-blend-screen opacity-75"
                />
              )}

              {/* ── HUD: top-left labels ───────────────────────── */}
              {hasDrone && (
                <div className="absolute top-3 left-3 flex flex-col gap-1.5 z-10 pointer-events-none">
                  {isAnnotated ? (
                    <div className="mono text-[10px] bg-black/85 px-2 py-1 rounded text-cyan-300">
                      Maritime Vessel Tracking — annotated
                    </div>
                  ) : rawOk ? (
                    <div className="mono text-[10px] bg-black/85 px-2 py-1 rounded text-slate-400">
                      Raw drone ingest feed
                    </div>
                  ) : null}
                  <div className="mono text-[10px] bg-black/85 px-2 py-1 rounded text-cyan-300 font-bold">
                    {activeDrone}
                  </div>
                </div>
              )}

              {/* ── HUD: top-right status pill ─────────────────── */}
              {hasDrone && (
                isAnnotated ? (
                  <div className={`absolute top-3 right-3 z-10 pointer-events-none flex items-center gap-1 mono text-[10px] bg-black/85 px-2 py-1 rounded ${annotatedMeta?.feedStatus === 'offline' ? 'text-amber-300' : 'text-cyan-300'}`}>
                    <span className={`w-1.5 h-1.5 rounded-full ${annotatedMeta?.feedStatus === 'offline' ? 'bg-amber-400' : 'bg-cyan-400 dvas-live-dot'}`} />
                    {annotatedMeta?.feedStatus === 'offline' ? 'ANNOTATED · OFFLINE' : `ANNOTATED${fps > 0 ? ` · ${fps}fps` : ''}`}
                  </div>
                ) : rawOk ? (
                  <div className="absolute top-3 right-3 z-10 pointer-events-none flex items-center gap-1 mono text-[10px] bg-black/85 px-2 py-1 rounded text-slate-400">
                    <span className="w-1.5 h-1.5 rounded-full bg-slate-400" />
                    LIVE · RAW
                  </div>
                ) : null
              )}

              {/* ── HUD: bottom banner — detection status ──────── */}
              {streamRunning && !isAnnotated && rawOk && (
                <div className="absolute bottom-10 left-0 right-0 flex justify-center z-10 pointer-events-none">
                  <div className="mono text-[10px] bg-black/85 px-3 py-1.5 rounded-full text-amber-400 flex items-center gap-2">
                    <RefreshCw size={10} className="animate-spin" />
                    SeaDrone vessel detection starting — awaiting first annotated frame…
                  </div>
                </div>
              )}

              {/* ── HUD: bottom-left model label ───────────────── */}
              {isAnnotated && (
                <div className="absolute bottom-3 left-3 z-10 pointer-events-none mono text-[10px] bg-black/85 px-2 py-1 rounded text-slate-400">
                  SeaDrone YOLOv8 + ByteTrack
                </div>
              )}

              {/* In-Video HUD Badge */}
              <div className="absolute bottom-3 right-3 px-2.5 py-1 bg-black/75 backdrop-blur border border-cyan-500/40 rounded text-[11px] font-mono text-cyan-300 flex items-center gap-2">
                <Radio size={12} className="text-cyan-400 animate-spin" />
                <span>ACTIVE TARGETS: {annotatedMeta?.vesselCount ?? summaryStats.live_count ?? 0}</span>
              </div>
            </div>
          </div>
        </div>

        {/* ── Right Column: Class Distribution & Event Feed (5 cols) ───────── */}
        <div className="lg:col-span-5 space-y-4">

          {/* Class Breakdown Pie Chart */}
          <div className="p-4 bg-slate-900/90 border border-slate-800 rounded-xl shadow-lg">
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300 mb-3 flex items-center justify-between">
              <span>Vessel Class Taxonomy</span>
              <span className="text-[11px] font-normal text-slate-400">9 Categories</span>
            </h3>

            {classStats && classStats.length > 0 ? (
              <div className="h-44 w-full flex items-center">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={classStats}
                      dataKey="count"
                      nameKey="class_name"
                      cx="50%"
                      cy="50%"
                      innerRadius={45}
                      outerRadius={65}
                      paddingAngle={4}
                    >
                      {classStats.map((entry, index) => (
                        <Cell
                          key={`cell-${index}`}
                          fill={CLASS_COLORS[entry.class_name] || CLASS_COLORS["Default"]}
                        />
                      ))}
                    </Pie>
                    <RechartsTooltip
                      contentStyle={{ background: "#0f172a", borderColor: "#334155", borderRadius: "8px", fontSize: "12px" }}
                    />
                  </PieChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <div className="h-44 flex items-center justify-center text-xs text-slate-500">
                Awaiting classification events...
              </div>
            )}

            {/* Class color tags grid */}
            <div className="grid grid-cols-2 gap-1.5 mt-2 pt-2 border-t border-slate-800/80 text-[11px]">
              {classStats.map((cs, idx) => (
                <div key={idx} className="flex items-center gap-1.5 truncate">
                  <span
                    className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                    style={{ background: CLASS_COLORS[cs.class_name] || "#3b82f6" }}
                  />
                  <span className="text-slate-300 truncate">{cs.class_name}:</span>
                  <span className="text-slate-100 font-bold ml-auto">{cs.count}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Real-Time Alert & Event Feed */}
          <div className="p-4 bg-slate-900/90 border border-slate-800 rounded-xl shadow-lg">
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300 mb-2.5 flex items-center justify-between">
              <span>Maritime Alerts & Events</span>
              <Activity size={14} className="text-emerald-400 animate-pulse" />
            </h3>

            <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
              {events && events.length > 0 ? (
                events.map((ev, i) => {
                  const isProximity = ev.event_type === "proximity_alert";
                  return (
                    <div
                      key={i}
                      className={`p-2.5 rounded-lg border text-xs flex items-start justify-between gap-2 transition-all ${
                        isProximity
                          ? "bg-amber-950/30 border-amber-500/40 text-amber-200"
                          : "bg-slate-800/60 border-slate-700/60 text-slate-300"
                      }`}
                    >
                      <div className="space-y-0.5">
                        <div className="flex items-center gap-1.5 font-medium">
                          {isProximity ? (
                            <AlertTriangle size={13} className="text-amber-400 flex-shrink-0" />
                          ) : (
                            <Ship size={13} className="text-cyan-400 flex-shrink-0" />
                          )}
                          <span className="font-semibold text-white">
                            {isProximity ? "Convergence Warning" : ev.class_name || "Vessel Detected"}
                          </span>
                          <span className="text-[10px] text-slate-400 font-mono">
                            #TRK-{ev.tracker_id}
                          </span>
                        </div>
                        <p className="text-[11px] text-slate-400">
                          {ev.details?.message || (ev.details?.direction ? `Direction: ${ev.details.direction}` : "Track established in water domain")}
                        </p>
                      </div>

                      <div className="text-right flex-shrink-0 text-[10px] font-mono text-slate-400">
                        {ev.ts ? new Date(ev.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : "--"}
                      </div>
                    </div>
                  );
                })
              ) : (
                <div className="text-center py-6 text-xs text-slate-500">
                  No maritime alerts logged for current drone
                </div>
              )}
            </div>
          </div>

        </div>
      </div>

      {/* ── Full Width: Vessel Track Ledger & Table ─────────────────────────── */}
      <div className="bg-slate-900/90 border border-slate-800 rounded-xl overflow-hidden shadow-xl">
        {/* Table Header Controls */}
        <div className="p-4 border-b border-slate-800 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-bold text-white flex items-center gap-2">
              <Compass size={16} className="text-cyan-400" />
              <span>Active Vessel Ledger</span>
              <span className="px-2 py-0.5 text-xs bg-slate-800 text-cyan-300 rounded-md font-mono">
                {filteredTracks.length} Records
              </span>
            </h3>
            <p className="text-xs text-slate-400">
              Live tracking attributes, bounding box area bucketing, and velocity trajectory analysis
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {/* Search Input */}
            <div className="relative">
              <Search size={14} className="absolute left-2.5 top-2.5 text-slate-400" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search class or track ID..."
                className="pl-8 pr-3 py-1.5 bg-slate-800 border border-slate-700 rounded-lg text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-cyan-500"
              />
            </div>

            {/* Class Filter */}
            <select
              value={selectedClassFilter}
              onChange={(e) => setSelectedClassFilter(e.target.value)}
              className="px-2.5 py-1.5 bg-slate-800 border border-slate-700 rounded-lg text-xs text-slate-200 focus:outline-none focus:border-cyan-500"
            >
              <option value="ALL">All Classes</option>
              {Object.keys(CLASS_COLORS).filter(k => k !== "Default").map(k => (
                <option key={k} value={k}>{k}</option>
              ))}
            </select>

            {/* Motion Filter */}
            <select
              value={selectedMotionFilter}
              onChange={(e) => setSelectedMotionFilter(e.target.value)}
              className="px-2.5 py-1.5 bg-slate-800 border border-slate-700 rounded-lg text-xs text-slate-200 focus:outline-none focus:border-cyan-500"
            >
              <option value="ALL">All Motion</option>
              <option value="MOVING">Cruising / Moving</option>
              <option value="STATIONARY">Stationary / Anchored</option>
            </select>

            {/* Size Filter */}
            <select
              value={selectedSizeFilter}
              onChange={(e) => setSelectedSizeFilter(e.target.value)}
              className="px-2.5 py-1.5 bg-slate-800 border border-slate-700 rounded-lg text-xs text-slate-200 focus:outline-none focus:border-cyan-500"
            >
              <option value="ALL">All Sizes</option>
              <option value="Small">Small (Dinghy/Jetski)</option>
              <option value="Medium">Medium (Fishing/Sail)</option>
              <option value="Large">Large (Cargo/Ferry)</option>
            </select>
          </div>
        </div>

        {/* Table Body */}
        <div className="overflow-x-auto max-h-96">
          <table className="w-full text-left text-xs border-collapse">
            <thead className="bg-slate-950/80 text-slate-400 font-semibold uppercase tracking-wider sticky top-0 border-b border-slate-800 z-10">
              <tr>
                <th className="py-2.5 px-3">Thumb</th>
                <th className="py-2.5 px-3 cursor-pointer hover:text-white" onClick={() => handleSort("tracker_id")}>
                  Track ID
                </th>
                <th className="py-2.5 px-3 cursor-pointer hover:text-white" onClick={() => handleSort("class_name")}>
                  Taxonomy Class
                </th>
                <th className="py-2.5 px-3">Size Bucket</th>
                <th className="py-2.5 px-3">Travel Direction</th>
                <th className="py-2.5 px-3">Status</th>
                <th className="py-2.5 px-3 cursor-pointer hover:text-white" onClick={() => handleSort("duration_s")}>
                  Duration
                </th>
                <th className="py-2.5 px-3 cursor-pointer hover:text-white" onClick={() => handleSort("confidence")}>
                  Confidence
                </th>
                <th className="py-2.5 px-3 text-right">Inspect</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 text-slate-200">
              {filteredTracks.length > 0 ? (
                filteredTracks.map((trk) => {
                  const badgeColor = CLASS_COLORS[trk.class_name] || "#3b82f6";
                  return (
                    <tr
                      key={trk.id || trk.tracker_id}
                      className="hover:bg-slate-800/50 transition-colors group"
                    >
                      {/* Thumbnail */}
                      <td className="py-2 px-3">
                        {trk.thumbnail_key ? (
                          <img
                            src={resolveVesselThumbnail(trk.thumbnail_key)}
                            alt="crop"
                            className="w-10 h-7 object-cover rounded border border-slate-700 cursor-pointer hover:scale-110 transition-transform"
                            onClick={() => setInspectedTrack(trk)}
                            onError={(e) => { e.target.style.display = 'none'; }}
                          />
                        ) : (
                          <div className="w-10 h-7 bg-slate-800 rounded border border-slate-700/60 flex items-center justify-center text-slate-500">
                            <Ship size={12} />
                          </div>
                        )}
                      </td>

                      {/* Track ID */}
                      <td className="py-2 px-3 font-mono font-bold text-cyan-400">
                        #TRK-{trk.tracker_id}
                      </td>

                      {/* Class */}
                      <td className="py-2 px-3 font-medium">
                        <span
                          className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[11px] font-semibold border"
                          style={{
                            background: `${badgeColor}15`,
                            borderColor: `${badgeColor}40`,
                            color: badgeColor
                          }}
                        >
                          <span className="w-1.5 h-1.5 rounded-full" style={{ background: badgeColor }}></span>
                          {trk.class_name}
                        </span>
                      </td>

                      {/* Size */}
                      <td className="py-2 px-3">
                        <span className="px-2 py-0.5 rounded text-[11px] bg-slate-800 text-slate-300">
                          {trk.size_bucket || "Medium"}
                        </span>
                      </td>

                      {/* Direction */}
                      <td className="py-2 px-3 font-mono text-[11px] text-slate-300">
                        {trk.direction || "Undetermined"}
                      </td>

                      {/* Status */}
                      <td className="py-2 px-3">
                        {trk.is_stationary ? (
                          <span className="inline-flex items-center gap-1 text-amber-400 text-[11px] font-medium">
                            <Anchor size={12} /> Anchored
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-emerald-400 text-[11px] font-medium">
                            <Navigation size={12} /> Cruising
                          </span>
                        )}
                      </td>

                      {/* Duration */}
                      <td className="py-2 px-3 font-mono text-slate-300">
                        {trk.duration_s ? `${Math.round(trk.duration_s)}s` : "<1s"}
                      </td>

                      {/* Confidence */}
                      <td className="py-2 px-3 font-mono font-medium text-slate-200">
                        {trk.confidence ? `${Math.round(trk.confidence * 100)}%` : "--"}
                      </td>

                      {/* Actions */}
                      <td className="py-2 px-3 text-right">
                        <button
                          onClick={() => setInspectedTrack(trk)}
                          className="p-1 rounded bg-slate-800 hover:bg-cyan-900/40 text-slate-300 hover:text-cyan-300 border border-slate-700 transition-colors"
                          title="Inspect Telemetry & Crop"
                        >
                          <Eye size={13} />
                        </button>
                      </td>
                    </tr>
                  );
                })
              ) : (
                <tr>
                  <td colSpan={9} className="py-8 text-center text-slate-500">
                    No matching vessels found in ledger
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Inspection Modal ─────────────────────────────────────────────────── */}
      {inspectedTrack && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4">
          <div className="bg-slate-900 border border-slate-700 rounded-xl max-w-lg w-full overflow-hidden shadow-2xl animate-in fade-in zoom-in-95">
            <div className="px-4 py-3 bg-slate-950 border-b border-slate-800 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Ship size={16} className="text-cyan-400" />
                <h4 className="text-sm font-bold text-white">
                  Vessel Telemetry Profile: #TRK-{inspectedTrack.tracker_id}
                </h4>
              </div>
              <button
                onClick={() => setInspectedTrack(null)}
                className="text-slate-400 hover:text-white"
              >
                <X size={16} />
              </button>
            </div>

            <div className="p-4 space-y-4">
              {/* Image Preview */}
              {inspectedTrack.thumbnail_key ? (
                <div className="w-full h-48 bg-black rounded-lg overflow-hidden border border-slate-800 flex items-center justify-center">
                  <img
                    src={resolveVesselThumbnail(inspectedTrack.thumbnail_key)}
                    alt="Vessel crop"
                    className="max-h-full max-w-full object-contain"
                  />
                </div>
              ) : (
                <div className="w-full h-32 bg-slate-800 rounded-lg flex items-center justify-center text-slate-500 text-xs">
                  No high-resolution crop available
                </div>
              )}

              {/* Data Specs Grid */}
              <div className="grid grid-cols-2 gap-2.5 text-xs">
                <div className="p-2.5 bg-slate-800/60 rounded-lg border border-slate-700/60">
                  <span className="text-slate-400 block">Class Taxonomy:</span>
                  <strong className="text-cyan-300 font-semibold">{inspectedTrack.class_name}</strong>
                </div>
                <div className="p-2.5 bg-slate-800/60 rounded-lg border border-slate-700/60">
                  <span className="text-slate-400 block">Relative Motion:</span>
                  <strong className="text-slate-200">{inspectedTrack.direction || "Stationary"}</strong>
                </div>
                <div className="p-2.5 bg-slate-800/60 rounded-lg border border-slate-700/60">
                  <span className="text-slate-400 block">Size Category:</span>
                  <strong className="text-slate-200">{inspectedTrack.size_bucket || "Medium"}</strong>
                </div>
                <div className="p-2.5 bg-slate-800/60 rounded-lg border border-slate-700/60">
                  <span className="text-slate-400 block">Tracking Duration:</span>
                  <strong className="text-slate-200 font-mono">{Math.round(inspectedTrack.duration_s || 0)}s</strong>
                </div>
                <div className="p-2.5 bg-slate-800/60 rounded-lg border border-slate-700/60">
                  <span className="text-slate-400 block">First Detected:</span>
                  <span className="text-slate-300 font-mono text-[11px]">{inspectedTrack.first_seen || "--"}</span>
                </div>
                <div className="p-2.5 bg-slate-800/60 rounded-lg border border-slate-700/60">
                  <span className="text-slate-400 block">Confidence Score:</span>
                  <strong className="text-emerald-400 font-mono">{Math.round((inspectedTrack.confidence || 0) * 100)}%</strong>
                </div>
              </div>
            </div>

            <div className="px-4 py-3 bg-slate-950 border-t border-slate-800 flex justify-end">
              <button
                onClick={() => setInspectedTrack(null)}
                className="px-3.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs font-semibold"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
