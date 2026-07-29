/**
 * PersonTrackingAnalytics.js
 * ─────────────────────────────────────────────────────────────────────────────
 * Dedicated Person Tracking & Face Intelligence page for the DVAS Command Centre.
 * Kerala Police | Drone Video Analytics System
 * ─────────────────────────────────────────────────────────────────────────────
 * Sections:
 *   1. Live Detection Video Stream
 *   2. Person Tracking KPI Strip
 *   3. Person Detection Window (live track list with confidence + bounding boxes)
 *   4. Person Deduplication Panel (cluster analysis, unique vs duplicates)
 *   5. Face Detection & Duplication Panel (face embeddings, re-ID, cross-drone)
 *   6. Live Detection Crop Feed (thumbnails of tracked persons / faces)
 *
 * Schema sources (dvas_analytics/person/schema.py):
 *   FrameAnalysisResult:  persons[], faces[], person_count, face_count, inference_ms
 *   PersonDetection:      bbox, confidence, track_id, crop_b64
 *   FaceDetection:        bbox, confidence, track_id, embedding_id
 *   UniquePersonResult:   cluster_id, count_in_cluster, is_duplicate
 *   DeduplicationResult:  total_embeddings, unique_persons, duplicates_found
 */

import { useState, useEffect, useRef } from "react";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, BarChart, Bar, Cell,
} from "recharts";
import {
  User, Users, Eye, Camera, Activity, ShieldAlert,
  AlertTriangle, CheckCircle, Radio, RefreshCw, Fingerprint,
  Play, Pause, Layers, ScanFace, Crosshair, VideoOff,
} from "lucide-react";

/* ─────────────────────────────────────────────────────────
   PERSON-ENABLED DRONE IDS & VIDEO MAP
   ───────────────────────────────────────────────────────── */
const PERSON_DRONES = ["DRN-001", "DRN-002", "DRN-003"];
const DRONE_VIDEO_MAP = {
  "DRN-001": "/DETECTED/Person detection 3 video/DRN-001_h264.mp4",
  "DRN-002": "/DETECTED/Person detection 3 video/DRN-002_h264.mp4",
  "DRN-003": "/DETECTED/Person detection 3 video/DRN-003_h264.mp4",
};

const ALL_DRONE_IDS = ["DRN-001", "DRN-002", "DRN-003", "DRN-004"];

/* ─────────────────────────────────────────────────────────
   MOCK DATA — mirroring person/schema.py schemas
   ───────────────────────────────────────────────────────── */
const NOW = Date.now();

// FrameAnalysisResult time-series mock
const MOCK_PERSON_SERIES = Array.from({ length: 24 }, (_, i) => ({
  hour: `${String(i).padStart(2, "0")}:00`,
  persons: Math.round(45  + Math.sin((i / 24) * Math.PI * 2) * 30 + Math.random() * 15),
  faces:   Math.round(28  + Math.sin((i / 24) * Math.PI * 2) * 20 + Math.random() * 10),
  unique:  Math.round(22  + Math.sin((i / 24) * Math.PI * 2) * 15 + Math.random() * 8),
}));

// PersonDetection mock
const MOCK_PERSONS = [
  { track_id: "P-0042", confidence: 0.94, zone: "North Gate",     drone: "DR-01", state: "Moving",  ts: new Date(NOW - 4000).toISOString(),  face_detected: true,  is_duplicate: false },
  { track_id: "P-0087", confidence: 0.88, zone: "Beach Sector A", drone: "DR-02", state: "Stationary", ts: new Date(NOW - 12000).toISOString(), face_detected: true,  is_duplicate: true  },
  { track_id: "P-0113", confidence: 0.91, zone: "MG Road",        drone: "DR-01", state: "Moving",  ts: new Date(NOW - 7000).toISOString(),  face_detected: false, is_duplicate: false },
  { track_id: "P-0031", confidence: 0.97, zone: "Secretariat Rd", drone: "DR-03", state: "Moving",  ts: new Date(NOW - 22000).toISOString(), face_detected: true,  is_duplicate: false },
  { track_id: "P-0056", confidence: 0.82, zone: "East Fort",      drone: "DR-02", state: "Stationary", ts: new Date(NOW - 45000).toISOString(), face_detected: false, is_duplicate: false },
  { track_id: "P-0099", confidence: 0.95, zone: "Palayam",        drone: "DR-03", state: "Moving",  ts: new Date(NOW - 9000).toISOString(),  face_detected: true,  is_duplicate: true  },
  { track_id: "P-0122", confidence: 0.87, zone: "North Gate",     drone: "DR-01", state: "Moving",  ts: new Date(NOW - 3000).toISOString(),  face_detected: true,  is_duplicate: false },
  { track_id: "P-0078", confidence: 0.79, zone: "MG Road",        drone: "DR-02", state: "Stationary", ts: new Date(NOW - 61000).toISOString(), face_detected: false, is_duplicate: false },
];

// UniquePersonResult clusters mock
const MOCK_CLUSTERS = [
  { cluster_id: 1, count_in_cluster: 3, is_duplicate: true,  best_track: "P-0087", zones: ["North Gate", "MG Road", "Beach Sector A"], cross_drone: true  },
  { cluster_id: 2, count_in_cluster: 1, is_duplicate: false, best_track: "P-0042", zones: ["North Gate"],    cross_drone: false },
  { cluster_id: 3, count_in_cluster: 2, is_duplicate: true,  best_track: "P-0099", zones: ["Palayam", "East Fort"], cross_drone: true  },
  { cluster_id: 4, count_in_cluster: 1, is_duplicate: false, best_track: "P-0031", zones: ["Secretariat Rd"], cross_drone: false },
  { cluster_id: 5, count_in_cluster: 1, is_duplicate: false, best_track: "P-0056", zones: ["East Fort"],    cross_drone: false },
];

// DeduplicationResult mock
const MOCK_DEDUP = {
  total_embeddings: 1847,
  unique_persons:    614,
  duplicates_found:  233,
  duplicates_deleted: 148,
  dedup_rate: 67,
  reid_single_drone: 89,
  reid_cross_drone:  34,
  inference_ms: 12.4,
};

// Face metrics derived
const FACE_STATS = [
  { label: "Total Face Detections",  value: "1,847", sub: "Raw detections across all drones",       color: "#38bdf8", bg: "rgba(14,165,233,0.08)",   border: "rgba(14,165,233,0.18)",   icon: Camera    },
  { label: "Unique Faces Indexed",   value: "614",   sub: "67% de-duplication efficiency",           color: "#2dd4bf", bg: "rgba(20,184,166,0.08)",   border: "rgba(20,184,166,0.18)",   icon: Eye       },
  { label: "Re-ID (Single Drone)",   value: "89",    sub: "Re-entries within same feed",             color: "#a78bfa", bg: "rgba(139,92,246,0.08)",   border: "rgba(139,92,246,0.18)",   icon: RefreshCw },
  { label: "Re-ID (Cross-Drone)",    value: "34",    sub: "Person matched across different drones",  color: "#fb923c", bg: "rgba(251,146,60,0.08)",   border: "rgba(251,146,60,0.18)",   icon: Radio     },
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

function Panel({ title, icon: Icon, iconClass = "text-sky-400", children, right, badge }) {
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
      <div className="flex-1 min-h-0 p-3">{children}</div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   SECTION 1 — Live Detection Video Stream
   ───────────────────────────────────────────────────────── */
function LiveStreamSection({ activeDrone, setActiveDrone, drones }) {
  const [playing, setPlaying] = useState(true);
  const [showFace, setShowFace] = useState(true);
  const videoRef = useRef(null);
  
  const isPersonDrone = PERSON_DRONES.includes(activeDrone);
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
      <div className="px-4 py-3 flex items-center gap-3 flex-wrap"
        style={{ borderBottom: "1px solid var(--dvas-border)" }}>
        <span className={`w-2 h-2 rounded-full dvas-live-dot ${isPersonDrone ? 'bg-emerald-500' : 'bg-slate-500'}`} />
        <span className="dvas-label font-semibold">Person Tracking & Face Detection Live Feed</span>
        <div className="flex items-center gap-1 ml-2">
          {droneList.map(d => (
            <button key={d.id}
              onClick={() => setActiveDrone(d.id)}
              className="mono text-[10px] px-2 py-1 rounded transition-all"
              style={{
                background: activeDrone === d.id ? "rgba(52,211,153,0.15)" : "var(--dvas-surface-2)",
                color: activeDrone === d.id ? "#34d399" : "var(--dvas-text-muted)",
                border: `1px solid ${activeDrone === d.id ? "rgba(52,211,153,0.35)" : "var(--dvas-border)"}`,
              }}>
              {d.id}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2">
          {isPersonDrone && (
            <button
              onClick={() => setShowFace(f => !f)}
              className="mono text-[10px] px-3 py-1.5 rounded-lg flex items-center gap-1.5 transition-all"
              style={{
                background: showFace ? "rgba(52,211,153,0.12)" : "var(--dvas-elev)",
                color: showFace ? "#34d399" : "var(--dvas-text-muted)",
                border: `1px solid ${showFace ? "rgba(52,211,153,0.3)" : "var(--dvas-border)"}`,
              }}>
              <ScanFace size={11} />
              Face overlay {showFace ? "ON" : "OFF"}
            </button>
          )}
          {isPersonDrone && (
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
      <div className="relative bg-black w-full" style={{ aspectRatio: "16 / 9", maxHeight: "80vh" }}>
        {isPersonDrone ? (
          <div className="w-full h-full relative overflow-hidden flex items-center justify-center">
            <video
              ref={videoRef}
              src={videoSrc}
              className="w-full h-full object-contain"
              autoPlay
              loop
              muted
              playsInline
            />
            {/* Person detection boxes overlay (simulated) */}
            {playing && showFace && activeDrone === "DRN-001" && (
              <div className="absolute inset-0 pointer-events-none">
                 {/* Decorative overlay only if needed, video is already annotated. We can leave it clean since the source videos have annotations. */}
              </div>
            )}
          </div>
        ) : (
          <div className="absolute inset-0 flex items-center justify-center bg-slate-900">
            <div className="text-center">
              <VideoOff size={32} className="text-slate-600 mx-auto mb-3" />
              <p className="mono text-sm text-slate-400">Person tracking offline</p>
              <p className="mono text-[10px] text-slate-500 mt-1">Select a supported drone (DRN-001, 002, 003)</p>
            </div>
          </div>
        )}

        {/* HUD */}
        {isPersonDrone && (
          <>
            <div className="absolute top-3 left-3 flex flex-col gap-1.5">
              <div className="mono text-[10px] bg-black/80 px-2 py-1 rounded text-emerald-300">
                Tracking Active · {activeDrone}
              </div>
            </div>
            <div className="absolute top-3 right-3 mono text-[10px] bg-black/80 px-2 py-1 rounded text-emerald-400 flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 dvas-live-dot" />
              LIVE · {new Date().toLocaleTimeString()}
            </div>
            <div className="absolute bottom-3 left-3 mono text-[10px] bg-black/80 px-2 py-1 rounded text-slate-400">
              YOLOv8 Person · Tile-Based · {MOCK_DEDUP.inference_ms}ms inference
            </div>
            <div className="absolute bottom-3 right-3 mono text-[10px] bg-black/80 px-2 py-1 rounded text-slate-400">
              {activeDrone}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   SECTION 2 — KPI Strip
   ───────────────────────────────────────────────────────── */
function PersonKPIStrip() {
  const totalPersons   = MOCK_PERSONS.length;
  const movingPersons  = MOCK_PERSONS.filter(p => p.state === "Moving").length;
  const faceDetected   = MOCK_PERSONS.filter(p => p.face_detected).length;
  const duplicates     = MOCK_PERSONS.filter(p => p.is_duplicate).length;
  const crossDrone     = MOCK_CLUSTERS.filter(c => c.cross_drone && c.is_duplicate).length;

  const kpis = [
    { label: "Persons Tracked",       value: totalPersons,                        tone: "text-emerald-400", icon: Users,       sub: "Active tracks across drones" },
    { label: "Moving / Stationary",   value: `${movingPersons} / ${totalPersons - movingPersons}`, tone: "text-sky-400", icon: Activity, sub: "Current movement states" },
    { label: "Faces Detected",        value: faceDetected,                        tone: "text-yellow-400",  icon: ScanFace,    sub: `${Math.round((faceDetected/totalPersons)*100)}% of tracked persons` },
    { label: "Duplicate / Re-ID",     value: duplicates,                          tone: "text-orange-400",  icon: RefreshCw,   sub: "Re-identification events" },
    { label: "Cross-Drone Re-ID",     value: crossDrone,                          tone: "text-violet-400",  icon: Radio,       sub: "Matched across multiple drones" },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
      {kpis.map(({ label, value, tone, icon: Icon, sub }) => (
        <div key={label} className="rounded-xl p-4 flex flex-col gap-1"
          style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}>
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
   SECTION 3 — Person Detection Window
   ───────────────────────────────────────────────────────── */
function PersonDetectionWindow() {
  const [filterDrone, setFilterDrone] = useState("All");
  const drones = ["All", "DR-01", "DR-02", "DR-03"];
  const droneColors = { "DR-01": "#38bdf8", "DR-02": "#a78bfa", "DR-03": "#34d399" };

  const filtered = filterDrone === "All" ? MOCK_PERSONS : MOCK_PERSONS.filter(p => p.drone === filterDrone);

  return (
    <div className="flex flex-col gap-3">
      {/* Drone filter */}
      <div className="flex items-center gap-2 flex-wrap">
        {drones.map(d => (
          <button key={d}
            onClick={() => setFilterDrone(d)}
            className="mono text-[10px] px-2.5 py-1 rounded-full transition-all"
            style={{
              background: filterDrone === d ? (droneColors[d] ? `${droneColors[d]}22` : "rgba(52,211,153,0.15)") : "var(--dvas-surface-2)",
              color: filterDrone === d ? (droneColors[d] || "#34d399") : "var(--dvas-text-muted)",
              border: `1px solid ${filterDrone === d ? (droneColors[d] ? `${droneColors[d]}44` : "rgba(52,211,153,0.3)") : "var(--dvas-border)"}`,
            }}>
            {d}
          </button>
        ))}
      </div>

      {/* Table */}
      <div className="overflow-auto" style={{ maxHeight: 280 }}>
        <table className="w-full text-xs">
          <thead className="sticky top-0" style={{ background: "var(--dvas-surface)" }}>
            <tr style={{ borderBottom: "1px solid var(--dvas-border)" }}>
              {["Track ID", "Confidence", "State", "Face", "Re-ID", "Zone", "Drone", "Last Seen"].map(h => (
                <th key={h} className="text-left px-2 py-2 dvas-label whitespace-nowrap" style={{ fontSize: 10 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="mono">
            {filtered.map(p => (
              <tr key={p.track_id}
                className="hover:bg-[color:var(--dvas-elev)] transition-colors"
                style={{ borderBottom: "1px solid var(--dvas-border)" }}>
                <td className="px-2 py-2 font-bold" style={{ color: "var(--dvas-text)" }}>
                  <div className="flex items-center gap-1.5">
                    <Crosshair size={10} className="text-emerald-400 shrink-0" />
                    {p.track_id}
                  </div>
                </td>
                <td className="px-2 py-2">
                  <span className="font-semibold" style={{ color: p.confidence > 0.9 ? "#34d399" : p.confidence > 0.8 ? "#fbbf24" : "#f87171" }}>
                    {Math.round(p.confidence * 100)}%
                  </span>
                </td>
                <td className="px-2 py-2">
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold"
                    style={{
                      background: p.state === "Moving" ? "rgba(52,211,153,0.12)" : "rgba(148,163,184,0.12)",
                      color: p.state === "Moving" ? "#34d399" : "#94a3b8",
                      border: `1px solid ${p.state === "Moving" ? "rgba(52,211,153,0.25)" : "rgba(148,163,184,0.2)"}`,
                    }}>
                    {p.state}
                  </span>
                </td>
                <td className="px-2 py-2">
                  {p.face_detected
                    ? <ScanFace size={13} className="text-yellow-400" />
                    : <span className="text-slate-600">—</span>}
                </td>
                <td className="px-2 py-2">
                  {p.is_duplicate
                    ? <span className="mono text-[10px] font-bold px-1.5 py-0.5 rounded"
                        style={{ background: "rgba(251,146,60,0.12)", color: "#fb923c", border: "1px solid rgba(251,146,60,0.25)" }}>
                        Re-ID
                      </span>
                    : <span className="text-slate-600">—</span>}
                </td>
                <td className="px-2 py-2" style={{ color: "var(--dvas-text-muted)" }}>{p.zone}</td>
                <td className="px-2 py-2">
                  <span className="text-[10px] px-1.5 py-0.5 rounded"
                    style={{ background: `${droneColors[p.drone]}22`, color: droneColors[p.drone], border: `1px solid ${droneColors[p.drone]}44` }}>
                    {p.drone}
                  </span>
                </td>
                <td className="px-2 py-2" style={{ color: "var(--dvas-text-dim)" }}>{fmtAge(p.ts)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   SECTION 4 — Person Deduplication Panel
   ───────────────────────────────────────────────────────── */
function DeduplicationPanel() {
  const { total_embeddings, unique_persons, duplicates_found, duplicates_deleted, dedup_rate, reid_single_drone, reid_cross_drone } = MOCK_DEDUP;
  const dupRate = Math.round((duplicates_found / total_embeddings) * 100);

  const bars = [
    { label: "Unique Persons",    value: unique_persons,    pct: Math.round((unique_persons / total_embeddings) * 100),    color: "#34d399" },
    { label: "Duplicates Found",  value: duplicates_found,  pct: Math.round((duplicates_found / total_embeddings) * 100),  color: "#fb923c" },
    { label: "Deleted / Merged",  value: duplicates_deleted,pct: Math.round((duplicates_deleted / total_embeddings) * 100),color: "#94a3b8" },
  ];

  return (
    <div className="flex flex-col gap-4">
      {/* Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          { label: "Total Embeddings",  value: total_embeddings.toLocaleString(), color: "text-sky-400"    },
          { label: "Unique Persons",    value: unique_persons.toLocaleString(),   color: "text-emerald-400" },
          { label: "Duplicates Found",  value: duplicates_found.toLocaleString(), color: "text-orange-400" },
          { label: "Dedup Rate",        value: `${dedup_rate}%`,                  color: "text-violet-400" },
        ].map(({ label, value, color }) => (
          <div key={label} className="rounded-lg px-3 py-2.5 flex flex-col gap-0.5"
            style={{ background: "var(--dvas-surface-2)", border: "1px solid var(--dvas-border)" }}>
            <span className="dvas-label" style={{ fontSize: 9 }}>{label}</span>
            <span className={`mono font-bold text-xl ${color}`}>{value}</span>
          </div>
        ))}
      </div>

      {/* Stacked bars */}
      <div className="flex flex-col gap-2">
        <span className="dvas-label" style={{ fontSize: 10 }}>Embedding Breakdown (of {total_embeddings.toLocaleString()} total)</span>
        {bars.map(({ label, value, pct, color }) => (
          <div key={label} className="flex flex-col gap-0.5">
            <div className="flex justify-between items-center">
              <span className="mono text-[11px]" style={{ color: "var(--dvas-text-muted)" }}>{label}</span>
              <span className="mono font-bold text-xs" style={{ color }}>{value.toLocaleString()} ({pct}%)</span>
            </div>
            <div className="h-2.5 rounded-full overflow-hidden" style={{ background: "var(--dvas-elev)" }}>
              <div className="h-full rounded-full transition-all duration-700"
                style={{ width: `${pct}%`, background: color }} />
            </div>
          </div>
        ))}
      </div>

      {/* Cluster table */}
      <div>
        <span className="dvas-label block mb-2" style={{ fontSize: 10 }}>Person Clusters (Re-ID Groupings)</span>
        <div className="overflow-auto" style={{ maxHeight: 200 }}>
          <table className="w-full text-xs">
            <thead>
              <tr style={{ borderBottom: "1px solid var(--dvas-border)" }}>
                {["Cluster ID", "Best Track", "Count", "Zones", "Cross-Drone", "Status"].map(h => (
                  <th key={h} className="text-left px-2 py-1.5 dvas-label" style={{ fontSize: 10 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="mono">
              {MOCK_CLUSTERS.map(c => (
                <tr key={c.cluster_id}
                  className="hover:bg-[color:var(--dvas-elev)] transition-colors"
                  style={{ borderBottom: "1px solid var(--dvas-border)" }}>
                  <td className="px-2 py-1.5 font-bold text-sky-400">CLU-{String(c.cluster_id).padStart(3, "0")}</td>
                  <td className="px-2 py-1.5" style={{ color: "var(--dvas-text)" }}>{c.best_track}</td>
                  <td className="px-2 py-1.5 font-bold" style={{ color: c.count_in_cluster > 1 ? "#fb923c" : "var(--dvas-text)" }}>
                    {c.count_in_cluster}
                  </td>
                  <td className="px-2 py-1.5" style={{ color: "var(--dvas-text-muted)" }}>
                    {c.zones.slice(0, 2).join(", ")}{c.zones.length > 2 ? ` +${c.zones.length - 2}` : ""}
                  </td>
                  <td className="px-2 py-1.5">
                    {c.cross_drone
                      ? <span className="text-violet-400 flex items-center gap-1"><Radio size={10} /> Yes</span>
                      : <span style={{ color: "var(--dvas-text-dim)" }}>—</span>}
                  </td>
                  <td className="px-2 py-1.5">
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold"
                      style={{
                        background: c.is_duplicate ? "rgba(251,146,60,0.12)" : "rgba(52,211,153,0.12)",
                        color: c.is_duplicate ? "#fb923c" : "#34d399",
                        border: `1px solid ${c.is_duplicate ? "rgba(251,146,60,0.25)" : "rgba(52,211,153,0.25)"}`,
                      }}>
                      {c.is_duplicate ? "DUPLICATE" : "UNIQUE"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   SECTION 5 — Face Detection & Duplication
   ───────────────────────────────────────────────────────── */
function FaceIntelligencePanel() {
  const { dedup_rate, reid_single_drone, reid_cross_drone } = MOCK_DEDUP;
  const retainedRate = 100 - dedup_rate;

  return (
    <div className="flex flex-col gap-4">
      {/* Privacy banner */}
      <div className="flex items-start gap-2 px-3 py-2.5 rounded-lg"
        style={{ background: "rgba(245,158,11,0.06)", border: "1px solid rgba(245,158,11,0.15)" }}>
        <ShieldAlert size={13} className="text-amber-400 mt-0.5 shrink-0" />
        <span className="text-[11px] leading-relaxed" style={{ color: "var(--dvas-text-muted)" }}>
          <strong className="text-amber-400">Privacy Safeguard:</strong> Metrics reflect de-duplication efficiency only.
          No autonomous identity confirmation is performed. All face data is subject to ABAC controls and judicial oversight.
        </span>
      </div>

      {/* 4 face stat cards */}
      <div className="grid grid-cols-2 gap-3">
        {FACE_STATS.map(({ label, value, sub, color, bg, border, icon: Icon }) => (
          <div key={label} className="rounded-xl px-3 py-3 flex flex-col gap-1"
            style={{ background: bg, border: `1px solid ${border}` }}>
            <div className="flex items-center gap-1.5">
              <Icon size={12} style={{ color }} />
              <span className="dvas-label" style={{ fontSize: 9 }}>{label}</span>
            </div>
            <div className="mono font-black text-2xl" style={{ color }}>{value}</div>
            <div className="mono text-[10px]" style={{ color: "var(--dvas-text-dim)" }}>{sub}</div>
          </div>
        ))}
      </div>

      {/* Re-ID breakdown */}
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-lg p-3" style={{ background: "var(--dvas-surface-2)", border: "1px solid var(--dvas-border)" }}>
          <div className="dvas-label mb-2" style={{ fontSize: 10 }}>Re-ID Events Breakdown</div>
          {[
            { label: "Single-Drone Re-ID",  value: reid_single_drone, color: "#a78bfa" },
            { label: "Cross-Drone Re-ID",   value: reid_cross_drone,  color: "#fb923c" },
          ].map(({ label, value, color }) => {
            const total = reid_single_drone + reid_cross_drone;
            const pct = Math.round((value / total) * 100);
            return (
              <div key={label} className="flex flex-col gap-0.5 mb-2">
                <div className="flex justify-between">
                  <span className="mono text-[11px]" style={{ color: "var(--dvas-text-muted)" }}>{label}</span>
                  <span className="mono font-bold text-xs" style={{ color }}>{value} ({pct}%)</span>
                </div>
                <div className="h-2 rounded-full overflow-hidden" style={{ background: "var(--dvas-elev)" }}>
                  <div className="h-full rounded-full" style={{ width: `${pct}%`, background: color }} />
                </div>
              </div>
            );
          })}
        </div>
        <div className="rounded-lg p-3" style={{ background: "var(--dvas-surface-2)", border: "1px solid var(--dvas-border)" }}>
          <div className="dvas-label mb-2" style={{ fontSize: 10 }}>De-duplication Efficiency</div>
          {[
            { label: "Duplicates Filtered", pct: dedup_rate,   color: "#34d399" },
            { label: "Unique Retained",     pct: retainedRate, color: "#818cf8" },
          ].map(({ label, pct, color }) => (
            <div key={label} className="flex flex-col gap-0.5 mb-2">
              <div className="flex justify-between">
                <span className="mono text-[11px]" style={{ color: "var(--dvas-text-muted)" }}>{label}</span>
                <span className="mono font-bold text-xs" style={{ color }}>{pct}%</span>
              </div>
              <div className="h-2 rounded-full overflow-hidden" style={{ background: "var(--dvas-elev)" }}>
                <div className="h-full rounded-full" style={{ width: `${pct}%`, background: color }} />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   SECTION 6 — Detection Time-Series Chart
   ───────────────────────────────────────────────────────── */
function DetectionTimeChart() {
  return (
    <div className="flex flex-col gap-2">
      <ResponsiveContainer width="100%" height={190}>
        <AreaChart data={MOCK_PERSON_SERIES} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
          <defs>
            <linearGradient id="gPersons" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"   stopColor="#34d399" stopOpacity={0.45} />
              <stop offset="100%" stopColor="#34d399" stopOpacity={0}   />
            </linearGradient>
            <linearGradient id="gFaces" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"   stopColor="#fbbf24" stopOpacity={0.4} />
              <stop offset="100%" stopColor="#fbbf24" stopOpacity={0}   />
            </linearGradient>
            <linearGradient id="gUnique" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"   stopColor="#818cf8" stopOpacity={0.35} />
              <stop offset="100%" stopColor="#818cf8" stopOpacity={0}   />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--dvas-border)" strokeDasharray="3 3" />
          <XAxis dataKey="hour" stroke="var(--dvas-text-muted)" fontSize={9} interval={3} />
          <YAxis stroke="var(--dvas-text-muted)" fontSize={9} />
          <Tooltip {...CHART_TOOLTIP} />
          <Area dataKey="persons" stroke="#34d399" fill="url(#gPersons)" strokeWidth={1.5} name="Total Persons"   dot={false} />
          <Area dataKey="faces"   stroke="#fbbf24" fill="url(#gFaces)"   strokeWidth={1.5} name="Faces Detected"  dot={false} />
          <Area dataKey="unique"  stroke="#818cf8" fill="url(#gUnique)"  strokeWidth={1.5} name="Unique Indexed"  dot={false} />
        </AreaChart>
      </ResponsiveContainer>
      <div className="flex gap-4 flex-wrap">
        {[{ c: "#34d399", l: "Total Persons" }, { c: "#fbbf24", l: "Faces Detected" }, { c: "#818cf8", l: "Unique Indexed" }].map(({ c, l }) => (
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
   SECTION 7 — Detected Persons Image Gallery
   ───────────────────────────────────────────────────────── */
function DetectedPersonsGallery({ activeDrone, manifestPersons, onImageClick }) {
  // Filter manifest by active drone
  const currentPersons = manifestPersons.filter(p => p.drone === activeDrone);
  
  // Use mock data / placeholders if not enough actual images are available
  const displayItems = currentPersons.length > 0 
    ? currentPersons
    : Array.from({ length: 8 }, (_, i) => ({
        id: `P-${String(i + 1).padStart(4, "0")}`,
        track_id: MOCK_PERSONS[i % MOCK_PERSONS.length]?.track_id || `P-${i}`,
        drone: activeDrone || "DRN-001",
        zone: "Unknown",
        confidence: 0.85,
        image: null,
      }));

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3 flex-wrap">
        <span className="mono text-[10px]" style={{ color: "var(--dvas-text-dim)" }}>
          {displayItems.length} person detections · {activeDrone}
        </span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4 max-h-[800px] overflow-y-auto pr-2">
        {displayItems.map((p, i) => (
          <div key={i} className="rounded-xl overflow-hidden flex flex-col shrink-0 hover:scale-[1.02] transition-transform duration-200"
            style={{ background: "var(--dvas-surface-2)", border: "1px solid var(--dvas-border)", boxShadow: "0 4px 6px -1px rgba(0, 0, 0, 0.5)" }}>
            {/* Image area */}
            <div className="w-full relative cursor-pointer" style={{ paddingBottom: "177.77%", background: "var(--dvas-elev)" }} onClick={() => p.image && onImageClick && onImageClick(p.image)}>
              <div className="absolute inset-0 flex items-center justify-center">
                {p.image ? (
                  <img src={p.image} alt={`Person ${p.track_id}`} className="w-full h-full object-contain" />
                ) : (
                  <div className="flex flex-col items-center gap-2 opacity-40">
                    <User size={28} className="text-emerald-500" />
                    <span className="mono text-[9px] text-slate-500">[PERSON IMAGE]</span>
                  </div>
                )}
                {/* Track ID badge */}
                <div className="absolute top-1.5 left-1.5 bg-black/70 px-1.5 py-0.5 rounded shadow">
                  <span className="mono text-[9px] text-emerald-400 font-bold">{p.track_id}</span>
                </div>
                {/* Confidence badge */}
                <div className="absolute top-1.5 right-1.5 bg-black/70 px-1.5 py-0.5 rounded shadow">
                  <span className="mono text-[9px] font-bold"
                    style={{ color: p.confidence > 0.9 ? "#34d399" : p.confidence > 0.8 ? "#fbbf24" : "#f87171" }}>
                    {Math.round(p.confidence * 100)}%
                  </span>
                </div>
              </div>
            </div>
            {/* Info strip */}
            <div className="px-2.5 py-2 flex items-center justify-between shrink-0">
              <div className="flex flex-col">
                <span className="mono text-[9px]" style={{ color: "var(--dvas-text-dim)" }}>ZONE</span>
                <span className="mono text-[10px] font-semibold" style={{ color: "var(--dvas-text-muted)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: "60px" }}>{p.zone}</span>
              </div>
              <span className="mono text-[10px] px-1.5 py-0.5 rounded"
                style={{ background: "rgba(52,211,153,0.12)", color: "#34d399", border: "1px solid rgba(52,211,153,0.2)" }}>
                {p.drone}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   SECTION 8 — Detected Faces Image Gallery
   ───────────────────────────────────────────────────────── */
function DetectedFacesGallery({ activeDrone, manifestFaces, onImageClick }) {
  // Filter manifest by active drone
  const currentFaces = manifestFaces.filter(f => f.drone === activeDrone);
  
  // Use mock data / placeholders if not enough actual images are available
  const displayItems = currentFaces.length > 0
    ? currentFaces
    : Array.from({ length: 12 }, (_, i) => {
        const person = MOCK_PERSONS[i % MOCK_PERSONS.length];
        return {
          id: `F-${String(i + 1).padStart(4, "0")}`,
          track_id: person?.track_id || `P-${i}`,
          drone: activeDrone || "DRN-001",
          zone: "Unknown",
          confidence: (0.75 + Math.random() * 0.24),
          is_duplicate: person?.is_duplicate || false,
          cluster_id: MOCK_CLUSTERS[i % MOCK_CLUSTERS.length]?.cluster_id || null,
          image: null,
        };
      });

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3 flex-wrap">
        <span className="mono text-[10px]" style={{ color: "var(--dvas-text-dim)" }}>
          {displayItems.length} face detections · {activeDrone}
        </span>
      </div>

      <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-3 max-h-[500px] overflow-y-auto pr-2">
        {displayItems.map((f, i) => (
          <div key={i} className="rounded-xl overflow-hidden flex flex-col shrink-0"
            style={{
              background: f.is_duplicate ? "rgba(251,146,60,0.05)" : "var(--dvas-surface-2)",
              border: `1px solid ${f.is_duplicate ? "rgba(251,146,60,0.25)" : "var(--dvas-border)"}`,
            }}>
            {/* Face image area */}
            <div className="w-full aspect-square flex items-center justify-center relative cursor-pointer"
              style={{ background: "var(--dvas-elev)" }} onClick={() => f.image && onImageClick && onImageClick(f.image)}>
              {f.image ? (
                <img src={f.image} alt={`Face ${f.id}`} className="w-full h-full object-cover" />
              ) : (
                <div className="flex flex-col items-center gap-1.5 opacity-40">
                  <ScanFace size={24} className="text-yellow-500" />
                  <span className="mono text-[8px] text-slate-500">[FACE]</span>
                </div>
              )}
              {/* Track ID */}
              <div className="absolute top-1 left-1 bg-black/70 px-1 py-0.5 rounded">
                <span className="mono text-[8px] text-sky-400 font-bold">{f.track_id}</span>
              </div>
              {/* Confidence */}
              <div className="absolute top-1 right-1 bg-black/70 px-1 py-0.5 rounded">
                <span className="mono text-[8px] font-bold"
                  style={{ color: f.confidence > 0.9 ? "#34d399" : f.confidence > 0.8 ? "#fbbf24" : "#f87171" }}>
                  {Math.round(f.confidence * 100)}%
                </span>
              </div>
              {/* Duplicate badge */}
              {f.is_duplicate && (
                <div className="absolute bottom-1 left-1 right-1 bg-orange-500/80 px-1 py-0.5 rounded text-center">
                  <span className="mono text-[7px] text-white font-bold tracking-wider">RE-ID · C{f.cluster_id}</span>
                </div>
              )}
            </div>
            {/* Mini info */}
            <div className="px-2 py-1.5 flex items-center justify-between shrink-0">
              <span className="mono text-[8px]" style={{ color: "var(--dvas-text-dim)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: "45px" }}>{f.zone}</span>
              <span className="mono text-[8px] px-1 py-0.5 rounded"
                style={{ background: "rgba(251,191,36,0.12)", color: "#fbbf24", border: "1px solid rgba(251,191,36,0.2)" }}>
                {f.drone}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   MAIN EXPORT
   ───────────────────────────────────────────────────────── */
export default function PersonTrackingAnalytics({ drones }) {
  const [, setTick] = useState(0);
  const [activeDrone, setActiveDrone] = useState("DRN-001");
  const [manifestPersons, setManifestPersons] = useState([]);
  const [manifestFaces, setManifestFaces] = useState([]);
  const [selectedImage, setSelectedImage] = useState(null);

  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 30000);
    return () => clearInterval(id);
  }, []);

  // Fetch the manifest on mount
  useEffect(() => {
    fetch("/DETECTED/Person detection 3 video/person_manifest.json")
      .then(r => r.json())
      .then(data => {
        if (data.persons) setManifestPersons(data.persons);
        if (data.faces) setManifestFaces(data.faces);
      })
      .catch(err => console.error("Could not load person manifest", err));
  }, []);

  // Auto-select active drone if ingest list changes
  useEffect(() => {
    if (drones && drones.length > 0 && !drones.find(d => d.id === activeDrone)) {
      setActiveDrone(drones[0].id);
    }
  }, [drones, activeDrone]);

  const reIdCount = MOCK_PERSONS.filter(p => p.is_duplicate).length;

  return (
    <div className="flex flex-col gap-5" style={{ fontFamily: "'Plus Jakarta Sans', sans-serif" }}>

      {/* LIVE STREAM */}
      <LiveStreamSection activeDrone={activeDrone} setActiveDrone={setActiveDrone} drones={drones} />

      {/* KPI STRIP */}
      <PersonKPIStrip />

      {/* DETECTION TIME CHART + PERSON WINDOW */}
      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-12 lg:col-span-7">
          <Panel title="Person & Face Detection — 24h Time Series" icon={Activity} iconClass="text-emerald-400"
            right={
              <span className="mono text-[10px] text-emerald-400 flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 dvas-live-dot" />
                Live · 15s refresh
              </span>
            }>
            <DetectionTimeChart />
          </Panel>
        </div>
        <div className="col-span-12 lg:col-span-5">
          <Panel title="Person Detection Window" icon={Crosshair} iconClass="text-sky-400"
            right={
              <span className="mono text-[10px] text-sky-400">{MOCK_PERSONS.length} active tracks</span>
            }>
            <PersonDetectionWindow />
          </Panel>
        </div>
      </div>

      {/* DETECTED PERSONS GALLERY */}
      <Panel title="Detected Persons — Image Gallery" icon={User} iconClass="text-emerald-400"
        right={
          <span className="mono text-[10px] text-emerald-400 flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 dvas-live-dot" />
            Person crops
          </span>
        }>
        <DetectedPersonsGallery activeDrone={activeDrone} manifestPersons={manifestPersons} onImageClick={setSelectedImage} />
      </Panel>

      {/* DETECTED FACES GALLERY */}
      <Panel title="Detected Faces — Image Gallery" icon={ScanFace} iconClass="text-yellow-400"
        right={
          <span className="mono text-[10px] text-yellow-400 flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-yellow-400 dvas-live-dot" />
            Face crops
          </span>
        }>
        <DetectedFacesGallery activeDrone={activeDrone} manifestFaces={manifestFaces} onImageClick={setSelectedImage} />
      </Panel>

      {/* DEDUPLICATION + FACE INTELLIGENCE */}
      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-12 lg:col-span-6">
          <Panel title="Person Deduplication — Cluster Analysis" icon={Layers} iconClass="text-orange-400"
            badge={reIdCount > 0 ? `${reIdCount} RE-ID` : undefined}>
            <DeduplicationPanel />
          </Panel>
        </div>
        <div className="col-span-12 lg:col-span-6">
          <Panel title="Face Detection & Duplication Intelligence" icon={Fingerprint} iconClass="text-yellow-400">
            <FaceIntelligencePanel />
          </Panel>
        </div>
      </div>

      {/* FULLSCREEN IMAGE MODAL */}
      {selectedImage && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4 cursor-pointer"
             onClick={() => setSelectedImage(null)}>
          <div className="relative max-w-6xl max-h-[90vh] flex flex-col rounded-xl overflow-hidden shadow-2xl"
               onClick={e => e.stopPropagation()}>
            <img src={selectedImage} alt="Full resolution view" className="max-w-full max-h-[85vh] object-contain bg-slate-900" />
            <div className="absolute top-3 right-3">
              <button onClick={() => setSelectedImage(null)} className="p-2 rounded-full bg-black/60 hover:bg-black/80 text-white transition-colors">
                <span className="sr-only">Close</span>
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}

