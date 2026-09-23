/**
 * AnomalyMonitor.js
 * ─────────────────────────────────────────────────────────────────────────────
 * Tactical Anomaly & Suspicious Activity Surveillance Interface
 * Kerala Police | Drone Video Analytics System
 *
 * End-to-end integration with Anomaly Analyzer (Port 8107 / sus_deploy):
 *  - Real-time tactical HUD video feed (dual VisDrone YOLO11x + YOLO11n)
 *  - 9-stage cognitive explainability pipeline & 4-tier confidence scoring
 *  - 8-stage gated alert feed with MinIO evidence thumbnail inspection
 *  - Active track ledger with ground kinematics & owner association
 *  - Anomaly event type distribution & KPI metrics
 *  - Runtime threshold configuration panel
 *  - Explicit FEED OFFLINE — NO ACTIVE STREAM tactical fallback
 */

import React, { useState, useEffect, useRef, useMemo, useCallback } from "react";
import {
  AlertTriangle, ShieldAlert, Eye, Activity, Play, Square, RefreshCw,
  Clock, CheckCircle2, X, ChevronRight, Sliders, Filter, Search,
  Download, Zap, ArrowUpRight, Compass, Shield, Users, Car, Package,
  VideoOff, Crosshair, Maximize2, Layers, Cpu, ShieldCheck
} from "lucide-react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip as RechartsTooltip,
  ResponsiveContainer, Cell, PieChart, Pie
} from "recharts";
import { api, getAuthToken } from "@/lib/api";
import { useJpegPoller } from "@/hooks/useJpegPoller";
import { useActiveStreams } from "@/hooks/useActiveStreams";

const resolveAnomalyThumbnail = (key) => {
  if (!key) return null;
  const cleanKey = String(key).replace(/^\/+/, "");
  const token = getAuthToken();
  const tokenParam = token ? `&token=${encodeURIComponent(token)}` : "";
  return `/api/minio/image?bucket=dvas-anomaly&key=${encodeURIComponent(cleanKey)}${tokenParam}`;
};

const SEVERITY_COLORS = {
  CRITICAL: {
    bg: "bg-red-500/10",
    text: "text-red-400",
    border: "border-red-500/40",
    borderL: "border-l-red-500",
    glow: "shadow-red-500/10",
    hex: "#EF4444"
  },
  HIGH: {
    bg: "bg-orange-500/10",
    text: "text-orange-400",
    border: "border-orange-500/40",
    borderL: "border-l-orange-500",
    glow: "shadow-orange-500/10",
    hex: "#F97316"
  },
  MEDIUM: {
    bg: "bg-amber-500/10",
    text: "text-amber-400",
    border: "border-amber-500/40",
    borderL: "border-l-amber-500",
    glow: "shadow-amber-500/10",
    hex: "#F59E0B"
  },
  LOW: {
    bg: "bg-sky-500/10",
    text: "text-sky-400",
    border: "border-sky-500/40",
    borderL: "border-l-sky-500",
    glow: "shadow-sky-500/10",
    hex: "#38BDF8"
  },
  NORMAL: {
    bg: "bg-slate-500/10",
    text: "text-slate-400",
    border: "border-slate-500/30",
    borderL: "border-l-slate-500",
    glow: "transparent",
    hex: "#94A3B8"
  },
};

const EVENT_TYPE_LABELS = {
  abandoned_object:             { label: "Abandoned Object", icon: Package, color: "#F97316" },
  suspicious_loitering:         { label: "Suspicious Loitering", icon: Clock, color: "#F59E0B" },
  person_fall:                  { label: "Person Fall / Collapse", icon: AlertTriangle, color: "#EF4444" },
  suspicious_following:         { label: "Suspicious Following", icon: Users, color: "#EC4899" },
  crowd_dispersal:              { label: "Rapid Crowd Dispersal", icon: Zap, color: "#8B5CF6" },
  rapid_crowd_dispersal:        { label: "Rapid Crowd Dispersal", icon: Zap, color: "#8B5CF6" },
  group_convergence:            { label: "Group Convergence", icon: Users, color: "#F59E0B" },
  coordinated_arrival:          { label: "Coordinated Arrival", icon: Users, color: "#F97316" },
  object_transfer:              { label: "Object Transfer", icon: Package, color: "#EF4444" },
  erratic_movement:             { label: "Erratic Movement", icon: Activity, color: "#F59E0B" },
  boundary_crossing:            { label: "Boundary Crossing", icon: ShieldAlert, color: "#DC2626" },
  running_flight:               { label: "Running / Flight", icon: Zap, color: "#F97316" },
  vehicle_wrong_way:            { label: "Vehicle Wrong-Way", icon: Car, color: "#EF4444" },
  vehicle_abrupt_stop:          { label: "Vehicle Abrupt Stop", icon: Car, color: "#F59E0B" },
  abnormal_vehicle_speed:       { label: "Abnormal Vehicle Speed", icon: Zap, color: "#EF4444" },
  suspicious_dropoff:           { label: "Suspicious Dropoff", icon: Package, color: "#F97316" },
  vehicle_circling:             { label: "Vehicle Circling / Patrol", icon: Car, color: "#F59E0B" },
  vehicle_approach_flee:        { label: "Vehicle Approach & Flee", icon: Car, color: "#EF4444" },
  vehicle_person_interaction:   { label: "Vehicle-Person Interaction", icon: Users, color: "#F59E0B" },
  vehicle_pedestrian_near_miss: { label: "Vehicle-Pedestrian Near Miss", icon: AlertTriangle, color: "#DC2626" },
  weapon_aiming:                { label: "Weapon / Arm Aiming", icon: ShieldAlert, color: "#DC2626" },
  weapon_detected:              { label: "Weapon-Like Object", icon: ShieldAlert, color: "#DC2626" },
  weapon_abandoned:             { label: "Abandoned Weapon", icon: ShieldAlert, color: "#DC2626" },
  weapon_held:                  { label: "Weapon Carried", icon: ShieldAlert, color: "#F59E0B" },
  unusual_placement:            { label: "Unusual Placement", icon: Package, color: "#F59E0B" },
  opposing_crowd_flow:          { label: "Opposing Crowd Flow", icon: Users, color: "#F59E0B" },
};

export default function AnomalyMonitor({ droneId: propDroneId, drones = [] }) {
  const { mergeWithDrones, streams, loading: streamsLoading } = useActiveStreams();
  const activeDrones = useMemo(() => {
    const merged = mergeWithDrones(drones || []);
    return merged
      .filter(d => (d.status === "live" || d.status === "degraded" || d.status === "draining" || (d.messages_ready ?? 0) > 0))
      .map(d => ({
        drone_id: d.id || d.drone_id || "",
        mission_id: d.mission_id || "",
        mission_name: d.mission_name || "",
        status: d.status,
        messages_ready: d.messages_ready ?? 0,
        consumer_lag: d.consumer_lag,
        frames_received: d.status === "live" || (d.messages_ready || 0) > 0 ? 1 : 0,
      }))
      .sort((a, b) => {
        const ord = { live: 0, draining: 1, degraded: 2, offline: 3 };
        const ao = ord[a.status] ?? 4;
        const bo = ord[b.status] ?? 4;
        if (ao !== bo) return ao - bo;
        return (a.drone_id || "").localeCompare(b.drone_id || "");
      });
  }, [streams, drones, mergeWithDrones]);

  const [activeDrone, setActiveDrone] = useState(propDroneId || null);
  const [activeMissionId, setActiveMissionId] = useState(null);
  const [streamRunning, setStreamRunning] = useState(true);
  const [togglingStream, setTogglingStream] = useState(false);

  // Sync activeDrone when propDroneId changes from parent, but ONLY if it is an active stream
  useEffect(() => {
    if (propDroneId && activeDrones.some(d => d.drone_id === propDroneId)) {
      setActiveDrone(propDroneId);
    }
  }, [propDroneId, activeDrones]);

  // Sync initial streamRunning state from health endpoint
  useEffect(() => {
    const checkHealth = async () => {
      try {
        const { data } = await api.get("/analytics/anomaly/health");
        if (typeof data?.stream_running === "boolean") {
          setStreamRunning(data.stream_running);
        }
      } catch (err) {
        console.debug("Failed to fetch anomaly health:", err);
      }
    };
    checkHealth();
  }, []);

  // Auto-select first live/draining drone if current activeDrone is not valid
  useEffect(() => {
    if (activeDrones.length === 0) {
      setActiveDrone(null);
      setActiveMissionId(null);
      return;
    }
    setActiveDrone(prev => {
      if (prev && activeDrones.some(d => d.drone_id === prev)) return prev;
      const live = activeDrones.find(d => d.status === "live" || (d.frames_received || 0) > 0);
      return (live || activeDrones[0]).drone_id;
    });
  }, [activeDrones]);

  // Sync current mission_id
  useEffect(() => {
    if (!activeDrone || !activeDrones.length) {
      setActiveMissionId(null);
      return;
    }
    const drone = activeDrones.find(d => d.drone_id === activeDrone);
    setActiveMissionId(drone?.mission_id || null);
  }, [activeDrone, activeDrones]);


  // Data states
  const [tracks, setTracks] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [summaryStats, setSummaryStats] = useState(null);
  const [typeBreakdown, setTypeBreakdown] = useState([]);
  const [loadingData, setLoadingData] = useState(false);

  // Filter & Detail states
  const [selectedAlert, setSelectedAlert] = useState(null);
  const [severityFilter, setSeverityFilter] = useState("ALL");
  const [trackSearch, setTrackSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("ALL");
  const [showConfigDrawer, setShowConfigDrawer] = useState(false);

  // Config state
  const [runtimeConfig, setRuntimeConfig] = useState(null);
  const [savingConfig, setSavingConfig] = useState(false);
  const [configFeedback, setConfigFeedback] = useState("");

  // Live video feed using canonical useJpegPoller
  const streamUrl = useMemo(() => {
    if (!activeDrone || !streamRunning) return "";
    return `/api/analytics/anomaly/annotated/latest?drone_id=${encodeURIComponent(activeDrone)}`;
  }, [activeDrone, streamRunning]);

  const { blobUrl, hasFrame, feedDead, fps, ok: isStreaming, error: streamError } = useJpegPoller(streamUrl, 800, streamRunning);

  // Determine if stream is live or offline
  const selectedStreamObj = activeDrones.find(d => d.drone_id === activeDrone);
  const isDroneLive = selectedStreamObj
    ? (selectedStreamObj.status === "live" || (selectedStreamObj.frames_received || 0) > 0 || (selectedStreamObj.messages_ready || 0) > 0)
    : false;
  const hasActiveStream = Boolean(activeDrone && streamRunning && blobUrl && !feedDead);

  // Fetch all telemetry data
  const fetchData = useCallback(async () => {
    if (!activeDrone) return;
    try {
      const [trkRes, altRes, statRes, typeRes] = await Promise.allSettled([
        api.get(`/analytics/anomaly/tracks/active?drone_id=${encodeURIComponent(activeDrone)}&limit=100`),
        api.get(`/analytics/anomaly/alerts/recent?drone_id=${encodeURIComponent(activeDrone)}&limit=50`),
        api.get(`/analytics/anomaly/stats/summary?drone_id=${encodeURIComponent(activeDrone)}`),
        api.get(`/analytics/anomaly/stats/by-type?drone_id=${encodeURIComponent(activeDrone)}`),
      ]);

      if (trkRes.status === "fulfilled") {
        setTracks(trkRes.value.data?.tracks || []);
      }
      if (altRes.status === "fulfilled") {
        setAlerts(altRes.value.data?.alerts || []);
      }
      if (statRes.status === "fulfilled") {
        setSummaryStats(statRes.value.data || null);
      }
      if (typeRes.status === "fulfilled") {
        setTypeBreakdown(typeRes.value.data?.breakdown || []);
      }
    } catch (err) {
      console.error("AnomalyMonitor fetchData error:", err);
    }
  }, [activeDrone]);

  // Initial & periodic polling
  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 4000);
    return () => clearInterval(interval);
  }, [fetchData]);

  // Load runtime config
  const fetchConfig = async () => {
    try {
      const { data } = await api.get("/analytics/anomaly/config");
      setRuntimeConfig(data);
    } catch (err) {
      console.error("Failed to load anomaly config:", err);
    }
  };

  useEffect(() => {
    fetchConfig();
  }, []);

  // Toggle stream on/off
  const handleToggleStream = async () => {
    setTogglingStream(true);
    try {
      const nextState = !streamRunning;
      const endpoint = streamRunning ? "/analytics/anomaly/stream/stop" : "/analytics/anomaly/stream/start";
      await api.get(endpoint);
      setStreamRunning(nextState);
      try {
        await api.post("/models/sync-worker", { service: "anomaly", action: nextState ? "start" : "stop" });
      } catch (syncErr) {
        console.warn("Anomaly model sync error:", syncErr);
      }
    } catch (err) {
      console.error("Failed to toggle stream:", err);
    } finally {
      setTogglingStream(false);
    }
  };

  // Export tracks as CSV
  const exportTracksCSV = () => {
    if (!filteredTracks || filteredTracks.length === 0) return;
    const headers = ["Track ID", "Category", "Class", "Suspicion Score", "Stage", "Duration (s)", "Speed (px/s)", "Stationary", "Stationary (s)"];
    const rows = filteredTracks.map(t => [
      t.track_id,
      t.class_category,
      t.class_name,
      Math.round(t.suspicion_score * 100) + "%",
      t.pipeline_stage,
      Math.round(t.duration_s || 0),
      Number(t.attributes?.speed_px_s || 0).toFixed(1),
      t.is_stationary ? "YES" : "NO",
      Math.round(t.stationary_seconds || 0)
    ]);
    const csvContent = "data:text/csv;charset=utf-8," + [headers.join(","), ...rows.map(r => r.join(","))].join("\n");
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", `anomaly_tracks_${activeDrone || "all"}_${new Date().toISOString().slice(0,10)}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  // Save config
  const handleSaveConfig = async (e) => {
    e.preventDefault();
    setSavingConfig(true);
    setConfigFeedback("");
    try {
      await api.put("/analytics/anomaly/config", runtimeConfig);
      setConfigFeedback("Configuration updated successfully");
      setTimeout(() => setConfigFeedback(""), 3000);
    } catch (err) {
      setConfigFeedback("Failed to update configuration");
    } finally {
      setSavingConfig(false);
    }
  };

  // Filtered alerts
  const filteredAlerts = useMemo(() => {
    if (severityFilter === "ALL") return alerts;
    return alerts.filter(a => a.severity === severityFilter);
  }, [alerts, severityFilter]);

  // Filtered tracks
  const filteredTracks = useMemo(() => {
    return tracks.filter(t => {
      const matchesSearch = !trackSearch ||
        String(t.track_id).includes(trackSearch) ||
        t.class_name.toLowerCase().includes(trackSearch.toLowerCase());
      const matchesCat = categoryFilter === "ALL" || t.class_category === categoryFilter;
      return matchesSearch && matchesCat;
    });
  }, [tracks, trackSearch, categoryFilter]);

  // Chart data
  const chartData = useMemo(() => {
    return typeBreakdown.map(item => {
      const def = EVENT_TYPE_LABELS[item.event_type] || { label: item.event_type, color: "#94A3B8" };
      return {
        name: def.label,
        count: item.count,
        color: def.color,
      };
    });
  }, [typeBreakdown]);

  return (
    <div className="space-y-4">
      {/* ── Top Bar: Drone Selector & Stream Controls ────────────────────────── */}
      <div
        className="rounded-xl p-3.5 flex items-center justify-between gap-4 flex-wrap backdrop-blur-md transition-all"
        style={{
          background: "var(--dvas-surface)",
          border: "1px solid var(--dvas-border)",
          boxShadow: "var(--dvas-shadow)"
        }}
      >
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-orange-500/20 to-amber-500/10 text-orange-400 border border-orange-500/30 flex items-center justify-center shadow-lg shadow-orange-500/10">
            <AlertTriangle size={20} />
          </div>
          <div>
            <div className="text-sm font-extrabold flex items-center gap-2" style={{ color: "var(--dvas-text)" }}>
              <span>Cognitive Anomaly Monitor</span>
              <span className="mono text-[10px] px-2 py-0.5 rounded-full bg-orange-500/15 text-orange-400 border border-orange-500/30 font-semibold tracking-wide">
                VisDrone YOLO11x + Secondary YOLO11n
              </span>
            </div>
            <div className="mono text-[10px] text-slate-500 mt-0.5 flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block" />
              <span>ORB-RANSAC Ego-Motion · 4-Tier Confidences · 8-Stage Gating · Section 65B Preserved</span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Drone Selector Dropdown */}
          <div className="flex items-center gap-2">
            <span className="mono text-xs text-slate-400 font-semibold">Feed:</span>
            <select
              value={activeDrone || ""}
              onChange={(e) => {
                const selected = activeDrones.find(d => d.drone_id === e.target.value);
                setActiveDrone(e.target.value);
                setActiveMissionId(selected?.mission_id || null);
              }}
              className="px-3 py-1.5 text-xs mono rounded-lg border border-slate-700/80 bg-slate-900/90 text-slate-200 focus:outline-none focus:border-orange-500 transition-colors shadow-inner"
            >
              {activeDrones.length === 0 && <option value="">No Active Streams</option>}
              {activeDrones.map(d => {
                const missionLabel = d.mission_name || (d.mission_id ? `Mission ${d.mission_id}` : null);
                const statusSuffix = d.status === "draining" ? " [DRAINING]" : "";
                return (
                  <option key={d.drone_id} value={d.drone_id}>
                    {d.drone_id}{missionLabel ? ` · ${missionLabel}` : ""}{statusSuffix}
                  </option>
                );
              })}
            </select>
          </div>

          {/* Stream Toggle Button */}
          <button
            onClick={handleToggleStream}
            disabled={togglingStream}
            className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-bold mono transition-all shadow-sm ${
              streamRunning
                ? "bg-amber-500/15 text-amber-300 border border-amber-500/40 hover:bg-amber-500/25 active:scale-95"
                : "bg-emerald-500/15 text-emerald-300 border border-emerald-500/40 hover:bg-emerald-500/25 active:scale-95"
            }`}
          >
            {streamRunning ? <Square size={13} /> : <Play size={13} />}
            <span>{streamRunning ? "Pause Inference" : "Resume Inference"}</span>
          </button>

          {/* Config Drawer Toggle */}
          <button
            onClick={() => setShowConfigDrawer(!showConfigDrawer)}
            className="flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-bold mono border border-slate-700/80 text-slate-300 hover:border-slate-500 hover:text-white transition-all active:scale-95 shadow-sm"
          >
            <Sliders size={13} className="text-orange-400" />
            <span>Thresholds</span>
          </button>
        </div>
      </div>

      {/* ── KPI Metrics Cards (4-Grid) with Watermarks & Glow ────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {/* Card 1: Active Entities */}
        <div
          className="rounded-xl p-4 flex flex-col justify-between relative overflow-hidden transition-all hover:border-slate-600/80 group"
          style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}
        >
          <div className="absolute -top-3 -right-3 text-sky-500/5 group-hover:text-sky-500/10 transition-colors pointer-events-none">
            <Activity size={84} />
          </div>
          <div className="flex items-center justify-between text-xs text-slate-400 relative z-10">
            <span className="mono uppercase text-[10px] tracking-wider font-semibold">Active Tracked Entities</span>
            <Activity size={15} className="text-sky-400" />
          </div>
          <div className="mt-2.5 flex items-baseline gap-2 relative z-10">
            <span className="mono text-3xl font-black text-sky-400 tracking-tight">
              {summaryStats?.live_count ?? tracks.length}
            </span>
            <span className="mono text-[10px] text-slate-500">in current view</span>
          </div>
          <div className="mt-3 pt-2.5 border-t border-slate-800/80 flex items-center justify-between mono text-[10px] text-slate-400 relative z-10">
            <span className="text-sky-300 font-medium">P: {summaryStats?.live_persons ?? 0}</span>
            <span>·</span>
            <span className="text-indigo-300 font-medium">V: {summaryStats?.live_vehicles ?? 0}</span>
            <span>·</span>
            <span className="text-amber-300 font-medium">Obj: {summaryStats?.live_objects ?? 0}</span>
          </div>
        </div>

        {/* Card 2: Suspicious Entities */}
        <div
          className="rounded-xl p-4 flex flex-col justify-between relative overflow-hidden transition-all hover:border-slate-600/80 group"
          style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}
        >
          <div className="absolute -top-3 -right-3 text-amber-500/5 group-hover:text-amber-500/10 transition-colors pointer-events-none">
            <AlertTriangle size={84} />
          </div>
          <div className="flex items-center justify-between text-xs text-slate-400 relative z-10">
            <span className="mono uppercase text-[10px] tracking-wider font-semibold">Suspicious Candidates</span>
            <AlertTriangle size={15} className="text-amber-400" />
          </div>
          <div className="mt-2.5 flex items-baseline gap-2 relative z-10">
            <span className="mono text-3xl font-black text-amber-400 tracking-tight">
              {summaryStats?.suspicious_count ?? tracks.filter(t => t.suspicion_level !== "NORMAL").length}
            </span>
            <span className="mono text-[10px] text-slate-500">elevated scores</span>
          </div>
          <div className="mt-3 pt-2.5 border-t border-slate-800/80 mono text-[10px] text-amber-400 flex items-center gap-1.5 relative z-10">
            <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse inline-block" />
            <span className="font-semibold">Observation & Candidate Stages</span>
          </div>
        </div>

        {/* Card 3: 24h Confirmed Alerts */}
        <div
          className="rounded-xl p-4 flex flex-col justify-between relative overflow-hidden transition-all hover:border-slate-600/80 group"
          style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}
        >
          <div className="absolute -top-3 -right-3 text-red-500/5 group-hover:text-red-500/10 transition-colors pointer-events-none">
            <ShieldAlert size={84} />
          </div>
          <div className="flex items-center justify-between text-xs text-slate-400 relative z-10">
            <span className="mono uppercase text-[10px] tracking-wider font-semibold">Confirmed Alerts (24h)</span>
            <ShieldAlert size={15} className="text-red-400" />
          </div>
          <div className="mt-2.5 flex items-baseline gap-2 relative z-10">
            <span className="mono text-3xl font-black text-red-400 tracking-tight">
              {summaryStats?.total_alerts_24h ?? alerts.length}
            </span>
            <span className="mono text-[10px] text-slate-500">passed 8 gates</span>
          </div>
          <div className="mt-3 pt-2.5 border-t border-slate-800/80 flex items-center justify-between mono text-[10px] text-slate-400 relative z-10">
            <span className="text-red-400 font-bold">Crit: {summaryStats?.critical_alerts_24h ?? 0}</span>
            <span>·</span>
            <span className="text-orange-400 font-bold">High: {summaryStats?.high_alerts_24h ?? 0}</span>
            <span>·</span>
            <span className="text-amber-400 font-bold">Med: {summaryStats?.medium_alerts_24h ?? 0}</span>
          </div>
        </div>

        {/* Card 4: Average Alert Confidence Gauge */}
        <div
          className="rounded-xl p-4 flex flex-col justify-between relative overflow-hidden transition-all hover:border-slate-600/80 group"
          style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}
        >
          <div className="absolute -top-3 -right-3 text-emerald-500/5 group-hover:text-emerald-500/10 transition-colors pointer-events-none">
            <CheckCircle2 size={84} />
          </div>
          <div className="flex items-center justify-between text-xs text-slate-400 relative z-10">
            <span className="mono uppercase text-[10px] tracking-wider font-semibold">Avg Alert Confidence</span>
            <CheckCircle2 size={15} className="text-emerald-400" />
          </div>
          <div className="mt-2.5 flex items-baseline justify-between relative z-10">
            <div className="flex items-baseline gap-2">
              <span className="mono text-3xl font-black text-emerald-400 tracking-tight">
                {typeof summaryStats?.avg_alert_confidence === "number" && summaryStats.avg_alert_confidence > 0
                  ? `${Math.round(summaryStats.avg_alert_confidence * 100)}%`
                  : "0%"}
              </span>
              <span className="mono text-[10px] text-slate-500">4-tier blended</span>
            </div>
            <span className="mono text-[10px] px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 font-bold">
              Gate ≥ 72%
            </span>
          </div>

          {/* Progress Bar with Gate Marker */}
          <div className="mt-3 relative z-10">
            <div className="w-full bg-slate-800/80 rounded-full h-2 overflow-hidden relative">
              <div
                className="bg-gradient-to-r from-emerald-500 to-teal-400 h-2 rounded-full transition-all duration-500"
                style={{ width: `${Math.min(100, Math.max(0, (summaryStats?.avg_alert_confidence ?? 0) * 100))}%` }}
              />
            </div>
            <div className="flex items-center justify-between mono text-[9px] text-slate-500 mt-1">
              <span>0%</span>
              <span className="text-emerald-400 font-semibold">Gate: 72%</span>
              <span>100%</span>
            </div>
          </div>
        </div>
      </div>

      {/* ── Main Layout: Live Stream (Left) & Event Type Chart (Right) ─────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Left 2 Cols: Live Video Feed with Tactical Reticle Framing */}
        <div
          className="lg:col-span-2 rounded-xl overflow-hidden flex flex-col relative"
          style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}
        >
          {/* Feed Header */}
          <div className="p-3.5 border-b border-slate-800/80 flex items-center justify-between flex-wrap gap-2">
            <div className="flex items-center gap-2.5 text-xs font-bold text-slate-200">
              <Eye size={15} className="text-orange-400" />
              <span>Tactical HUD Perception Feed</span>
              {hasActiveStream ? (
                <span className="mono text-[9px] px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 flex items-center gap-1.5 font-bold">
                  <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                  LIVE (800ms)
                </span>
              ) : (
                <span className="mono text-[9px] px-2 py-0.5 rounded-full bg-red-500/15 text-red-400 border border-red-500/30 flex items-center gap-1.5 font-bold">
                  <span className="w-2 h-2 rounded-full bg-red-400" />
                  FEED OFFLINE
                </span>
              )}
            </div>
            <div className="mono text-[10px] text-slate-500 flex items-center gap-2">
              <span>60fps Tactical HUD</span>
              <span>·</span>
              <span>Collision-Avoidant Labels</span>
            </div>
          </div>

          {/* Viewport Frame with Reticle Corners */}
          <div className="relative bg-black aspect-video flex items-center justify-center overflow-hidden">
            {/* Tactical Reticle Corner Accents */}
            <div className="absolute top-2 left-2 w-4 h-4 border-t-2 border-l-2 border-orange-500/60 pointer-events-none z-20" />
            <div className="absolute top-2 right-2 w-4 h-4 border-t-2 border-r-2 border-orange-500/60 pointer-events-none z-20" />
            <div className="absolute bottom-2 left-2 w-4 h-4 border-b-2 border-l-2 border-orange-500/60 pointer-events-none z-20" />
            <div className="absolute bottom-2 right-2 w-4 h-4 border-b-2 border-r-2 border-orange-500/60 pointer-events-none z-20" />

            {/* Tactical Reticle Crosshairs in Center (subtle) */}
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none z-10 opacity-20">
              <Crosshair size={48} className="text-orange-400" />
            </div>

            {hasActiveStream ? (
              <img
                src={blobUrl}
                alt="Anomaly Tactical HUD"
                className="w-full h-full object-contain select-none z-10"
              />
            ) : (
              /* ── Canonical FEED OFFLINE — NO ACTIVE STREAM Tactical Screen ── */
              <div className="w-full h-full flex flex-col items-center justify-center gap-3 text-center px-4 bg-slate-950/95 relative overflow-hidden select-none z-10">
                {/* Tactical grid background overlay */}
                <div className="absolute inset-0 opacity-15 bg-[radial-gradient(#f97316_1px,transparent_1px)] [background-size:20px_20px]" />
                <div className="absolute inset-0 bg-gradient-to-t from-black via-transparent to-black opacity-80" />

                <div className="relative z-10 flex flex-col items-center gap-3 max-w-md">
                  <div className="w-16 h-16 rounded-2xl bg-slate-900/90 border border-slate-700/70 flex items-center justify-center text-slate-500 shadow-2xl shadow-orange-950/20">
                    <VideoOff size={32} className="text-slate-400 opacity-80" />
                  </div>
                  <div>
                    <p className="mono text-sm text-slate-300 font-extrabold tracking-widest uppercase">
                      FEED OFFLINE — NO ACTIVE STREAM
                    </p>
                    <p className="mono text-[11px] text-slate-500 mt-1 leading-relaxed">
                      {activeDrone ? (
                        <>Drone <span className="text-orange-400 font-semibold">{activeDrone}</span> is not transmitting video frames to the ingest server.</>
                      ) : (
                        <>Select an active drone stream to initiate cognitive anomaly perception.</>
                      )}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 mt-1">
                    <span className="mono text-[10px] px-2.5 py-1 rounded bg-slate-900 border border-slate-800 text-slate-400 flex items-center gap-1.5">
                      <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
                      INGEST DISCONNECTED
                    </span>
                    {activeMissionId && (
                      <span className="mono text-[10px] px-2.5 py-1 rounded bg-slate-900 border border-slate-800 text-slate-400">
                        MSN: {activeMissionId}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* Floating HUD Chips: Top-Left Metadata */}
            {activeDrone && (
              <div className="absolute top-4 left-4 flex flex-col gap-1.5 z-20 pointer-events-none">
                <div className="mono text-[9px] bg-black/85 backdrop-blur-md px-2 py-1 rounded-md text-sky-300 border border-slate-800/80 font-bold flex items-center gap-1.5 shadow-lg">
                  <ShieldCheck size={11} className="text-sky-400" />
                  <span>ANOMALY PERCEPTION · COGNITIVE HUD</span>
                </div>
                <div className="mono text-[9px] bg-black/85 backdrop-blur-md px-2 py-1 rounded-md text-teal-300 border border-slate-800/80 flex items-center gap-1.5 shadow-lg">
                  <span className="text-slate-500">DRONE:</span>
                  <span className="font-bold">{activeDrone}</span>
                </div>
                {activeMissionId && (
                  <div className="mono text-[9px] bg-black/85 backdrop-blur-md px-2 py-1 rounded-md text-violet-300 border border-slate-800/80 flex items-center gap-1.5 shadow-lg">
                    <span className="text-slate-500">MSN:</span>
                    <span className="font-bold">{activeMissionId}</span>
                  </div>
                )}
              </div>
            )}

            {/* Floating HUD Chips: Top-Right Stream Telemetry */}
            {activeDrone && (
              <div className="absolute top-4 right-4 flex flex-col items-end gap-1.5 z-20 pointer-events-none">
                <div className="mono text-[9px] bg-black/85 backdrop-blur-md px-2 py-1 rounded-md text-slate-300 border border-slate-800/80 flex items-center gap-1.5 shadow-lg">
                  <span className={`w-1.5 h-1.5 rounded-full ${hasActiveStream ? "bg-emerald-400 animate-pulse" : "bg-red-500"}`} />
                  <span className="font-bold">{hasActiveStream ? "TELEMETRY SYNCHRONIZED" : "NO TELEMETRY"}</span>
                </div>
                <div className="mono text-[9px] bg-black/85 backdrop-blur-md px-2 py-1 rounded-md text-slate-400 border border-slate-800/80 shadow-lg">
                  {fps > 0 ? `${fps} FPS · 1280×720` : "INACTIVE · 0 FPS"}
                </div>
              </div>
            )}

            {/* Floating HUD Chips: Bottom-Left Camera Motion Telemetry */}
            {hasActiveStream && (
              <div className="absolute bottom-4 left-4 z-20 pointer-events-none">
                <div className="mono text-[9px] bg-black/85 backdrop-blur-md px-2 py-1 rounded-md text-slate-400 border border-slate-800/80 flex items-center gap-2 shadow-lg">
                  <Compass size={11} className="text-orange-400" />
                  <span>EGO-MOTION: STABLE (ORB-RANSAC)</span>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Right 1 Col: Event Type Distribution Chart */}
        <div
          className="rounded-xl p-4 flex flex-col justify-between"
          style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}
        >
          <div>
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Layers size={15} className="text-orange-400" />
                <span className="text-xs font-bold text-slate-200">Anomaly Event Distribution</span>
              </div>
              <span className="mono text-[10px] px-2 py-0.5 rounded bg-slate-800 text-slate-400 font-semibold">
                Total: {typeBreakdown.reduce((acc, c) => acc + c.count, 0)}
              </span>
            </div>

            {chartData.length > 0 ? (
              <div className="h-60 mt-1">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={chartData} layout="vertical" margin={{ left: 10, right: 15, top: 5, bottom: 5 }}>
                    <XAxis type="number" hide />
                    <YAxis
                      dataKey="name"
                      type="category"
                      width={120}
                      tick={{ fontSize: 10, fill: "#94A3B8" }}
                    />
                    <RechartsTooltip
                      contentStyle={{
                        background: "#0F172A",
                        border: "1px solid #334155",
                        borderRadius: "8px",
                        fontSize: "11px",
                        boxShadow: "0 10px 25px -5px rgba(0, 0, 0, 0.5)"
                      }}
                    />
                    <Bar dataKey="count" radius={[0, 6, 6, 0]}>
                      {chartData.map((entry, idx) => (
                        <Cell key={`cell-${idx}`} fill={entry.color} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <div className="h-60 flex flex-col items-center justify-center mono text-xs text-slate-500 gap-2">
                <Package size={24} className="opacity-40" />
                <span>No anomaly events recorded for this drone feed</span>
              </div>
            )}
          </div>

          {/* Tactical Cognitive Rule Info Box */}
          <div className="mt-3 p-3 rounded-lg bg-slate-900/80 border border-slate-800/80 text-[11px] text-slate-400 space-y-1.5 shadow-inner">
            <div className="font-bold text-slate-300 flex items-center gap-1.5">
              <Shield size={13} className="text-sky-400" />
              <span>2/3 Corroboration Rule Active:</span>
            </div>
            <div className="text-[10px] text-slate-400 leading-relaxed">
              Alerts require independent corroboration across at least 2 evidence groups
              (Object, Kinematics, Ownership, Vehicle Behavior, Context) to suppress false positives.
            </div>
          </div>
        </div>
      </div>

      {/* ── Bottom Section: Alerts Feed (Left) & Active Track Ledger (Right) ───── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Left: Confirmed Alerts Feed */}
        <div
          className="rounded-xl overflow-hidden flex flex-col"
          style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}
        >
          <div className="p-3.5 border-b border-slate-800 flex items-center justify-between flex-wrap gap-2">
            <div className="flex items-center gap-2">
              <ShieldAlert size={16} className="text-red-400" />
              <span className="text-xs font-bold text-slate-200">Gated Anomaly Alerts Feed</span>
              <span className="mono text-[10px] px-1.5 py-0.2 rounded bg-slate-800 text-slate-400 font-bold">
                {filteredAlerts.length}
              </span>
            </div>

            {/* Severity Filter Tabs */}
            <div className="flex items-center gap-1 bg-slate-900/80 p-0.5 rounded-lg border border-slate-800">
              {["ALL", "CRITICAL", "HIGH", "MEDIUM", "LOW"].map(sev => (
                <button
                  key={sev}
                  onClick={() => setSeverityFilter(sev)}
                  className={`mono text-[9px] px-2 py-1 rounded-md transition-all ${
                    severityFilter === sev
                      ? "bg-slate-700 text-white font-bold shadow-sm"
                      : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  {sev}
                </button>
              ))}
            </div>
          </div>

          <div className="p-3.5 space-y-2.5 max-h-96 overflow-y-auto no-scrollbar">
            {filteredAlerts.length === 0 ? (
              <div className="p-10 text-center text-slate-500 mono text-xs flex flex-col items-center gap-2">
                <CheckCircle2 size={24} className="text-emerald-500/40" />
                <span>No alerts matching the selected severity criteria</span>
              </div>
            ) : (
              filteredAlerts.map(alert => {
                const sevCfg = SEVERITY_COLORS[alert.severity] || SEVERITY_COLORS.MEDIUM;
                const typeCfg = EVENT_TYPE_LABELS[alert.event_type] || { label: alert.event_type, icon: AlertTriangle };
                const IconComp = typeCfg.icon;

                return (
                  <div
                    key={alert.event_id || alert.id}
                    onClick={() => setSelectedAlert(alert)}
                    className={`p-3.5 rounded-xl border border-l-4 cursor-pointer transition-all hover:border-slate-600 hover:shadow-lg ${sevCfg.bg} ${sevCfg.border} ${sevCfg.borderL} ${alert.severity === "CRITICAL" ? "animate-pulse" : ""}`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex items-start gap-2.5">
                        <div className={`p-2 rounded-lg ${sevCfg.bg} ${sevCfg.text} shrink-0 mt-0.5 border ${sevCfg.border}`}>
                          <IconComp size={16} />
                        </div>
                        <div>
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="text-xs font-bold text-slate-100">{typeCfg.label}</span>
                            <span className={`mono text-[9px] px-2 py-0.5 rounded font-extrabold tracking-wide uppercase ${sevCfg.text} bg-slate-900/80 border ${sevCfg.border}`}>
                              {alert.severity}
                            </span>
                            {alert.track_id && (
                              <span className="mono text-[10px] text-slate-400">
                                Track #{alert.track_id} ({alert.class_name || "object"})
                              </span>
                            )}
                          </div>
                          <div className="text-[11px] text-slate-300 mt-1 line-clamp-2 leading-relaxed">
                            {alert.summary || alert.reasons?.join("; ") || "Suspicious anomaly detected"}
                          </div>
                        </div>
                      </div>

                      {/* Snapshot Thumbnail Preview with Zoom Hover */}
                      {alert.snapshot_key && (
                        <div className="w-18 h-14 rounded-lg overflow-hidden bg-black shrink-0 border border-slate-700 shadow-md group/thumb relative">
                          <img
                            src={resolveAnomalyThumbnail(alert.snapshot_key)}
                            alt="Evidence"
                            className="w-full h-full object-cover transition-transform group-hover/thumb:scale-110"
                            onError={(e) => { e.target.style.display = "none"; }}
                          />
                        </div>
                      )}
                    </div>

                    {/* Footer: Confidence & Evidence Groups */}
                    <div className="mt-3 pt-2.5 border-t border-slate-800/60 flex items-center justify-between mono text-[10px] text-slate-400">
                      <div className="flex items-center gap-2">
                        <span>Confidence: <span className="font-bold text-slate-200">
                          {typeof alert.confidence_alert === "number" ? `${Math.round(alert.confidence_alert * 100)}%` : "—"}
                        </span></span>
                        <span>·</span>
                        <span>Evidence: <span className="text-sky-400 font-semibold">
                          {(alert.evidence_groups && alert.evidence_groups.length > 0) ? alert.evidence_groups.join(", ") : "—"}
                        </span></span>
                      </div>
                      <div className="text-slate-500">
                        {alert.ts ? new Date(alert.ts).toLocaleTimeString() : "Recent"}
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* Right: Active Track Ledger */}
        <div
          className="rounded-xl overflow-hidden flex flex-col"
          style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}
        >
          <div className="p-3.5 border-b border-slate-800 flex items-center justify-between flex-wrap gap-2">
            <div className="flex items-center gap-2">
              <Activity size={16} className="text-sky-400" />
              <span className="text-xs font-bold text-slate-200">Active Track Ledger</span>
              <span className="mono text-[10px] px-1.5 py-0.2 rounded bg-slate-800 text-slate-400 font-bold">
                {filteredTracks.length}
              </span>
            </div>

            <div className="flex items-center gap-2">
              <div className="relative">
                <input
                  type="text"
                  placeholder="Filter tracks..."
                  value={trackSearch}
                  onChange={(e) => setTrackSearch(e.target.value)}
                  className="px-2.5 py-1 text-[11px] mono rounded-lg border border-slate-700 bg-slate-900 text-slate-200 w-32 focus:outline-none focus:border-sky-500 transition-colors shadow-inner"
                />
              </div>
              <select
                value={categoryFilter}
                onChange={(e) => setCategoryFilter(e.target.value)}
                className="px-2.5 py-1 text-[11px] mono rounded-lg border border-slate-700 bg-slate-900 text-slate-200 focus:outline-none focus:border-sky-500 transition-colors shadow-inner"
              >
                <option value="ALL">All Categories</option>
                <option value="person">Persons</option>
                <option value="vehicle">Vehicles</option>
                <option value="object">Objects</option>
              </select>
              <button
                onClick={exportTracksCSV}
                disabled={filteredTracks.length === 0}
                className="flex items-center gap-1.5 px-2.5 py-1 text-[11px] mono rounded-lg border border-slate-700 bg-slate-900 text-slate-300 hover:text-white hover:border-slate-500 transition-colors disabled:opacity-40 shadow-inner"
                title="Export active tracks as CSV"
              >
                <Download size={12} />
                <span>Export CSV</span>
              </button>
            </div>
          </div>

          <div className="overflow-x-auto max-h-96 no-scrollbar">
            <table className="w-full text-left mono text-[11px]">
              <thead className="bg-slate-900/70 text-slate-400 border-b border-slate-800 sticky top-0 backdrop-blur">
                <tr>
                  <th className="p-3">Track</th>
                  <th className="p-3">Category</th>
                  <th className="p-3">Suspicion Score</th>
                  <th className="p-3">Stage</th>
                  <th className="p-3">Dwell</th>
                  <th className="p-3">Speed</th>
                  <th className="p-3">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/40 text-slate-300">
                {filteredTracks.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="p-10 text-center text-slate-500">
                      No active tracks in surveillance view
                    </td>
                  </tr>
                ) : (
                  filteredTracks.map(trk => {
                    const sevCfg = SEVERITY_COLORS[trk.suspicion_level] || SEVERITY_COLORS.NORMAL;
                    const isElevated = trk.suspicion_level !== "NORMAL";
                    const CatIcon = trk.class_category === "person" ? Users : (trk.class_category === "vehicle" ? Car : Package);

                    return (
                      <tr key={trk.track_id} className="hover:bg-slate-800/40 transition-colors">
                        <td className="p-3 font-bold text-slate-100">
                          <div className="flex items-center gap-1.5">
                            {isElevated && <span className="w-2 h-2 rounded-full bg-amber-400 animate-ping inline-block" />}
                            <span>#{trk.track_id}</span>
                          </div>
                        </td>
                        <td className="p-3 capitalize">
                          <div className="flex items-center gap-1.5 text-slate-300">
                            <CatIcon size={12} className="text-slate-400" />
                            <span>{trk.class_name}</span>
                          </div>
                        </td>
                        <td className="p-3">
                          <div className="flex items-center gap-2">
                            <div className="w-12 bg-slate-800 rounded-full h-1.5 overflow-hidden">
                              <div
                                className="h-full rounded-full"
                                style={{
                                  width: `${Math.round(trk.suspicion_score * 100)}%`,
                                  background: sevCfg.hex
                                }}
                              />
                            </div>
                            <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${sevCfg.bg} ${sevCfg.text}`}>
                              {Math.round(trk.suspicion_score * 100)}%
                            </span>
                          </div>
                        </td>
                        <td className="p-3 text-[10px] text-slate-400 font-semibold">{trk.pipeline_stage}</td>
                        <td className="p-3">{Math.round(trk.duration_s || 0)}s</td>
                        <td className="p-3">{Number(trk.attributes?.speed_px_s || 0).toFixed(1)} px/s</td>
                        <td className="p-3">
                          {trk.is_stationary ? (
                            <span className="text-amber-400 font-bold flex items-center gap-1">
                              <Clock size={11} />
                              <span>{Math.round(trk.stationary_seconds || 0)}s stat.</span>
                            </span>
                          ) : (
                            <span className="text-slate-500">Moving</span>
                          )}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* ── Detail Modal: Explainability Dossier ──────────────────────────────── */}
      {selectedAlert && (
        <div
          onClick={(e) => { if (e.target === e.currentTarget) setSelectedAlert(null); }}
          className="fixed inset-0 z-50 bg-black/80 backdrop-blur-md flex items-center justify-center p-4 cursor-pointer"
        >
          <div
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-2xl rounded-2xl overflow-hidden border border-slate-700 shadow-2xl flex flex-col max-h-[90vh] animate-in fade-in duration-200 cursor-default"
            style={{ background: "var(--dvas-surface)" }}
          >
            {/* Modal Header */}
            <div className="p-4 border-b border-slate-800 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-xl bg-red-500/10 text-red-400 border border-red-500/30">
                  <ShieldAlert size={20} />
                </div>
                <div>
                  <div className="text-sm font-bold text-slate-100">
                    Anomaly Alert Explainability Dossier
                  </div>
                  <div className="mono text-[10px] text-slate-400">
                    ID: {selectedAlert.event_id} · Drone: {selectedAlert.drone_id}
                  </div>
                </div>
              </div>
              <button
                onClick={() => setSelectedAlert(null)}
                className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
              >
                <X size={18} />
              </button>
            </div>

            {/* Modal Body */}
            <div className="p-5 space-y-4 overflow-y-auto no-scrollbar">
              {/* Snapshot image if available */}
              {selectedAlert.snapshot_key && (
                <div className="w-full h-60 rounded-xl overflow-hidden bg-black border border-slate-700 flex items-center justify-center shadow-lg relative">
                  <img
                    src={resolveAnomalyThumbnail(selectedAlert.snapshot_key)}
                    alt="Alert Snapshot"
                    className="w-full h-full object-contain"
                    onError={(e) => {
                      e.target.onerror = null;
                      e.target.style.display = "none";
                      if (e.target.nextElementSibling) e.target.nextElementSibling.style.display = "flex";
                    }}
                  />
                  <div className="hidden flex-col items-center justify-center h-full text-slate-500 mono text-xs gap-2">
                    <Package size={28} className="opacity-40" />
                    <span>Evidence snapshot capture unavailable</span>
                  </div>
                </div>
              )}

              {/* 4-Tier Separated Confidence Breakdown */}
              <div>
                <div className="text-xs font-bold text-slate-300 mb-2">4-Tier Separated Confidences:</div>
                <div className="grid grid-cols-4 gap-2.5 text-center mono">
                  <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800 shadow-inner">
                    <div className="text-[9px] text-slate-500 uppercase tracking-wider font-semibold">Detector</div>
                    <div className="text-lg font-black text-sky-400 mt-0.5">
                      {typeof selectedAlert.confidence_detector === "number" ? `${Math.round(selectedAlert.confidence_detector * 100)}%` : "—"}
                    </div>
                  </div>
                  <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800 shadow-inner">
                    <div className="text-[9px] text-slate-500 uppercase tracking-wider font-semibold">Rule Band</div>
                    <div className="text-lg font-black text-indigo-400 mt-0.5">
                      {typeof selectedAlert.confidence_rule === "number" ? `${Math.round(selectedAlert.confidence_rule * 100)}%` : "—"}
                    </div>
                  </div>
                  <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800 shadow-inner">
                    <div className="text-[9px] text-slate-500 uppercase tracking-wider font-semibold">Event</div>
                    <div className="text-lg font-black text-amber-400 mt-0.5">
                      {typeof selectedAlert.confidence_event === "number" ? `${Math.round(selectedAlert.confidence_event * 100)}%` : "—"}
                    </div>
                  </div>
                  <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800 shadow-inner">
                    <div className="text-[9px] text-slate-500 uppercase tracking-wider font-semibold">Alert Gate</div>
                    <div className="text-lg font-black text-red-400 mt-0.5">
                      {typeof selectedAlert.confidence_alert === "number" ? `${Math.round(selectedAlert.confidence_alert * 100)}%` : "—"}
                    </div>
                  </div>
                </div>
              </div>

              {/* Narrative Summary */}
              <div>
                <div className="text-xs font-bold text-slate-300 mb-1">Cognitive Narrative:</div>
                <div className="p-3.5 rounded-xl bg-slate-900/90 border border-slate-800 text-xs text-slate-300 leading-relaxed shadow-inner">
                  {selectedAlert.summary || selectedAlert.reasons?.join("; ") || "Suspicious anomaly detected."}
                </div>
              </div>

              {/* Evidence Groups Corroborated */}
              <div>
                <div className="text-xs font-bold text-slate-300 mb-1.5">Corroborating Evidence Groups:</div>
                <div className="flex gap-2 flex-wrap">
                  {(selectedAlert.evidence_groups && selectedAlert.evidence_groups.length > 0) ? (
                    selectedAlert.evidence_groups.map(grp => (
                      <span
                        key={grp}
                        className="mono text-[10px] px-3 py-1 rounded-lg bg-sky-500/10 text-sky-300 border border-sky-500/30 font-bold"
                      >
                        Group {grp}
                      </span>
                    ))
                  ) : (
                    <span className="mono text-[10px] text-slate-500">None recorded</span>
                  )}
                </div>
              </div>

              {/* Actions & Legal Disclaimer */}
              <div className="border-t border-slate-800 pt-3 flex items-center justify-between flex-wrap gap-2">
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => {
                      navigator.clipboard?.writeText(selectedAlert.event_id || "");
                    }}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-900 border border-slate-700 text-slate-300 hover:text-white hover:border-slate-500 mono text-[11px] transition-colors"
                  >
                    <span>Copy Event ID</span>
                  </button>
                  <a
                    href={`/command/evidence?search=${encodeURIComponent(selectedAlert.event_id || "")}`}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-orange-500/15 text-orange-400 border border-orange-500/30 hover:bg-orange-500/25 mono text-[11px] font-bold transition-colors"
                  >
                    <span>View in Evidence Review</span>
                    <ArrowUpRight size={13} />
                  </a>
                </div>
                <div className="mono text-[9px] text-slate-500">
                  Section 65B metadata preserved in vault
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Slide-Out Drawer: Runtime Threshold Configuration ───────────────── */}
      {showConfigDrawer && runtimeConfig && (
        <div
          onClick={(e) => { if (e.target === e.currentTarget) setShowConfigDrawer(false); }}
          className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex justify-end cursor-pointer"
        >
          <div
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-md h-full p-5 flex flex-col justify-between shadow-2xl border-l border-slate-700 animate-in slide-in-from-right duration-200 cursor-default"
            style={{ background: "var(--dvas-surface)" }}
          >
            <div>
              <div className="flex items-center justify-between pb-3.5 border-b border-slate-800">
                <div className="flex items-center gap-2">
                  <Sliders size={18} className="text-orange-400" />
                  <span className="text-sm font-bold text-slate-100">Anomaly Engine Thresholds</span>
                </div>
                <button
                  onClick={() => setShowConfigDrawer(false)}
                  className="p-1 text-slate-400 hover:text-white transition-colors"
                >
                  <X size={18} />
                </button>
              </div>

              <form onSubmit={handleSaveConfig} className="mt-5 space-y-5 mono text-xs">
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-slate-400 font-semibold">Detector Confidence</label>
                    <span className="text-orange-400 font-bold">{Math.round(runtimeConfig.conf_thresh * 100)}%</span>
                  </div>
                  <input
                    type="range"
                    min="0.20"
                    max="0.80"
                    step="0.05"
                    value={runtimeConfig.conf_thresh}
                    onChange={(e) => setRuntimeConfig({ ...runtimeConfig, conf_thresh: parseFloat(e.target.value) })}
                    className="w-full accent-orange-500"
                  />
                  <div className="flex justify-between text-[9px] text-slate-500 mt-1">
                    <span>20% (High Recall)</span>
                    <span>80% (High Precision)</span>
                  </div>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-slate-400 font-semibold">Alert Hysteresis Enter Gate</label>
                    <span className="text-orange-400 font-bold">{Math.round(runtimeConfig.hysteresis_enter * 100)}%</span>
                  </div>
                  <input
                    type="range"
                    min="0.50"
                    max="0.90"
                    step="0.02"
                    value={runtimeConfig.hysteresis_enter}
                    onChange={(e) => setRuntimeConfig({ ...runtimeConfig, hysteresis_enter: parseFloat(e.target.value) })}
                    className="w-full accent-orange-500"
                  />
                  <div className="flex justify-between text-[9px] text-slate-500 mt-1">
                    <span>50%</span>
                    <span>90%</span>
                  </div>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-slate-400 font-semibold">Alert Cooldown Window</label>
                    <span className="text-orange-400 font-bold">{runtimeConfig.cooldown_seconds}s</span>
                  </div>
                  <input
                    type="range"
                    min="15"
                    max="120"
                    step="5"
                    value={runtimeConfig.cooldown_seconds}
                    onChange={(e) => setRuntimeConfig({ ...runtimeConfig, cooldown_seconds: parseInt(e.target.value) })}
                    className="w-full accent-orange-500"
                  />
                  <div className="flex justify-between text-[9px] text-slate-500 mt-1">
                    <span>15s</span>
                    <span>120s</span>
                  </div>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-slate-400 font-semibold">Loitering Time Window</label>
                    <span className="text-orange-400 font-bold">{runtimeConfig.loitering_duration_sec}s</span>
                  </div>
                  <input
                    type="range"
                    min="10"
                    max="60"
                    step="5"
                    value={runtimeConfig.loitering_duration_sec}
                    onChange={(e) => setRuntimeConfig({ ...runtimeConfig, loitering_duration_sec: parseInt(e.target.value) })}
                    className="w-full accent-orange-500"
                  />
                  <div className="flex justify-between text-[9px] text-slate-500 mt-1">
                    <span>10s</span>
                    <span>60s</span>
                  </div>
                </div>

                {configFeedback && (
                  <div className="p-2.5 rounded-lg bg-slate-800/90 border border-slate-700 text-center text-xs font-bold text-orange-400 shadow-sm">
                    {configFeedback}
                  </div>
                )}

                <button
                  type="submit"
                  disabled={savingConfig}
                  className="w-full py-2.5 bg-gradient-to-r from-orange-600 to-amber-600 hover:from-orange-500 hover:to-amber-500 text-white rounded-lg font-bold transition-all shadow-md active:scale-98 disabled:opacity-50"
                >
                  {savingConfig ? "Applying Changes…" : "Apply Runtime Thresholds"}
                </button>
              </form>
            </div>

            <div className="mono text-[10px] text-slate-500 border-t border-slate-800 pt-3 flex items-center gap-2">
              <ShieldCheck size={13} className="text-emerald-400" />
              <span>Thresholds apply instantaneously to all connected surveillance streams.</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
