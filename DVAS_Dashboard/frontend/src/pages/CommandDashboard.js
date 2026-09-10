import { useEffect, useState, useRef, useCallback } from "react";
import AppShell from "@/components/layout/AppShell";
import { api, API } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { useI18n } from "@/context/I18nContext";
import { useDvasSocket } from "@/lib/useDvasSocket";
import LiveMap from "@/components/LiveMap";
import MultiDroneView from "@/components/MultiDroneView";
import CrowdAnalytics from "@/components/CrowdAnalytics";
import TrafficAnalytics from "@/components/TrafficAnalytics";
import PersonTrackingAnalytics from "@/components/PersonTrackingAnalytics";
import IntrusionAnalytics from "@/components/IntrusionAnalytics";
import { useActiveStreams } from "@/hooks/useActiveStreams";
import {
  MapPin, Radio as Broadcast, Bell, Film as FilmSlate,
  BarChart3 as ChartBar, AlertTriangle as Warning, CheckCircle, X,
  ExternalLink as ArrowSquareOut, Folder as FolderLock, Download,
  Cpu, ShieldCheck, User as UserIcon, Users as UsersIcon, Car, Hash,
  UserSearch, RefreshCw, Play, Square, CheckCircle2,
  Activity, Battery, BatteryLow, BatteryMedium, BatteryFull,
  Wifi, WifiOff, Clock, Radio, Shield, FileText,
} from "lucide-react";
import { useJpegPoller } from "@/hooks/useJpegPoller";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, BarChart, Bar } from "recharts";

// Tab visibility — 4-role model
// CRO inherits all former field_supervisor tabs
// SR  inherits all former supervisory_officer tabs
const ALL_PAGES = [
  { key: "map",       label: "Tactical Map",         Icon: MapPin,          roles: ["control_room_officer","senior_officer"] },
  { key: "alerts",    label: "Alerts",               Icon: Bell,            roles: ["control_room_officer","senior_officer"] },
  { key: "multidrone",label: "Multi-Drone View",     Icon: Broadcast,       roles: ["control_room_officer","senior_officer"] },
  { key: "evidence",  label: "Evidence",             Icon: FilmSlate,       roles: ["control_room_officer","senior_officer"] },
  { key: "crowd",     label: "Crowd Analytics",      Icon: UsersIcon,       roles: ["control_room_officer","senior_officer"] },
  { key: "traffic",   label: "Traffic & ANPR",       Icon: Car,             roles: ["control_room_officer","senior_officer"] },
  { key: "person",    label: "Person Tracking",      Icon: UserSearch,      roles: ["control_room_officer","senior_officer"] },
  { key: "intrusion", label: "Intrusion Detection",  Icon: Shield,          roles: ["control_room_officer","senior_officer"] },
  { key: "models",    label: "Detection Models",     Icon: Cpu,             roles: ["control_room_officer","senior_officer"] },
  { key: "exports",   label: "Exports",              Icon: ArrowSquareOut,  roles: ["control_room_officer","senior_officer"] },
  { key: "aggregated",label: "Aggregated View",      Icon: ChartBar,        roles: ["senior_officer"] },
  { key: "reports",   label: "Reports",              Icon: FilmSlate,       roles: ["control_room_officer","senior_officer"] },
];

const MODEL_ICONS = { person: UserIcon, face: ShieldCheck, vehicle: Car, crowd: UsersIcon, plate: Hash, intrusion: Shield };

function KPI({ label, value, tone = "text-sky-400" }) {
  return (
    <div className="border border-slate-800 bg-slate-900 p-4">
      <div className="dvas-label">{label}</div>
      <div className={`mono text-2xl mt-1 ${tone}`}>{value ?? "—"}</div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────
   MINI STREAM CELL — one slot in the Tactical Map Multi-Drone Grid
   ───────────────────────────────────────────────────────────────── */
const MINI_BACKOFF = [600, 1200, 2400, 5000, 10000, 20000];
const MINI_WATCHDOG_MS = 8000;

function MiniStreamCell({ drone }) {
  const isOffline  = drone.status === "offline";
  const isDegraded = drone.status === "degraded";
  const isDraining = drone.status === "draining" || (drone.messages_ready ?? 0) > 0;
  
  const url = isOffline ? null : `${API}/ingest-server/frame/${encodeURIComponent(drone.id)}/latest`;
  const { blobUrl, hasFrame, feedDead } = useJpegPoller(url, 800, !isOffline);
  const [lastSeen, setLastSeen] = useState(null);

  useEffect(() => {
    if (hasFrame) setLastSeen(new Date());
  }, [hasFrame, blobUrl]);

  const statusColor = isDraining ? "#fbbf24" : isDegraded ? "#f59e0b" : isOffline || feedDead ? "#ef4444" : "#10b981";
  const borderColor = isDraining || isDegraded
    ? "border-amber-500/40"
    : isOffline || feedDead
    ? "border-red-500/30"
    : hasFrame
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
      {/* ── Video area ── */}
      <div className="relative flex-1 overflow-hidden bg-[#060e1a]">
        {/* Scan-line overlay */}
        <div
          className="absolute inset-0 pointer-events-none z-10"
          style={{
            backgroundImage: "repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(20,184,166,0.012) 2px, rgba(20,184,166,0.012) 4px)",
          }}
        />

        {/* Live frame / placeholder */}
        {!isOffline && hasFrame && blobUrl ? (
          <img
            src={blobUrl}
            alt={`Feed ${drone.id}`}
            className="absolute inset-0 w-full h-full object-cover"
            style={{ opacity: isDegraded ? 0.75 : 0.92 }}
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center">
            {isOffline || feedDead ? (
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

        {/* Top-left: Drone ID + Mission ID */}
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
            {hasFrame && !feedDead && (
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
              {isOffline || feedDead ? "OFFLINE" : isDraining ? "DRAINING" : isDegraded ? "DEGRADED" : "LIVE"}
            </span>
          </div>
          {isDraining && (
            <span className="mono text-[8px] px-1.5 py-0.5 rounded font-bold bg-amber-900/60 text-amber-300">
              {drone.messages_ready ?? 0} frames
            </span>
          )}
        </div>
      </div>

      {/* ── Metadata strip ── */}
      <div
        className="px-2 py-1 flex flex-col gap-0.5 shrink-0"
        style={{ background: "rgba(4,10,20,0.92)", borderTop: "1px solid rgba(255,255,255,0.05)" }}
      >
        {/* Operator name */}
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
        {/* Location + last seen */}
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

/* ─────────────────────────────────────────────────────────────────
   MULTI DRONE GRID — Tactical Map right panel
   ───────────────────────────────────────────────────────────────── */
function MultiDroneGrid({ dbDrones, selectedDroneId, onSelectDrone }) {
  const { mergeWithDrones, loading, streams } = useActiveStreams();
  const merged = mergeWithDrones(dbDrones);
  // Only assign slots to active streaming or draining drones
  const activeDrones = merged.filter((d) => d.status === "live" || d.status === "degraded" || d.status === "draining" || (d.messages_ready ?? 0) > 0);
  const slots  = activeDrones.slice(0, 4);

  return (
    <div className="p-2 flex flex-col gap-2 h-full">
      {/* Header row with live count */}
      <div className="flex items-center justify-between px-1">
        <div className="flex items-center gap-1.5">
          <Radio size={10} className="text-teal-400" style={{ animation: streams.length > 0 ? "pulse 1.5s infinite" : "none" }} />
          <span className="mono text-[9px] text-teal-400 font-bold">
            {streams.length} LIVE {streams.length === 1 ? "STREAM" : "STREAMS"}
          </span>
        </div>
        {loading && <RefreshCw size={9} className="text-slate-500 animate-spin" />}
      </div>

      {/* 2×2 grid */}
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
        {/* Fill empty slots */}
        {Array.from({ length: Math.max(0, 4 - slots.length) }).map((_, i) => (
          <div
            key={`empty-${i}`}
            className="rounded-lg border border-slate-800/60 flex flex-col items-center justify-center gap-2"
            style={{ background: "var(--dvas-surface-2)", aspectRatio: "16/10" }}
          >
            <Broadcast size={16} className="text-slate-700" />
            <span className="mono text-[8px] text-slate-600">AWAITING STREAM</span>
          </div>
        ))}
      </div>

      {/* Footer: total frame count hint */}
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


function Pin({ d, onClick }) {
  const color = d.status === "live" ? "bg-emerald-500" : d.status === "degraded" ? "bg-amber-500" : "bg-red-500";
  // Kerala roughly lat 8-13, lng 74-77. Map to 0-100% offsets
  const left = ((d.lng - 74.5) / 3) * 100;
  const top = 100 - ((d.lat - 8) / 5) * 100;
  return (
    <button
      data-testid={`map-pin-${d.id}`}
      onClick={() => onClick(d)}
      className="absolute -translate-x-1/2 -translate-y-1/2 group"
      style={{ left: `${Math.max(3, Math.min(97, left))}%`, top: `${Math.max(3, Math.min(97, top))}%` }}
    >
      <div className={`w-3 h-3 ${color} rotate-45 ring-2 ring-slate-950`} />
      <div className="absolute left-4 top-0 hidden group-hover:block whitespace-nowrap bg-black/90 border border-slate-700 px-2 py-1 mono text-[10px]">
        {d.id} · {d.jurisdiction} · {d.status}
      </div>
    </button>
  );
}

export default function CommandDashboard() {
  const { user } = useAuth();
  const { t } = useI18n();
  const [tab, setTab] = useState(() => {
    try {
      // One-shot deep-link from notification bell (localStorage, consumed once)
      const deepLink = localStorage.getItem("dvas_cmd_tab");
      if (deepLink) { localStorage.removeItem("dvas_cmd_tab"); return deepLink; }
      // Persist active tab across refreshes (sessionStorage, survives F5)
      const saved = sessionStorage.getItem("dvas_cmd_tab");
      if (saved) return saved;
    } catch (e) { /* noop */ }
    return "map";
  });

  // Persist tab to sessionStorage on every change
  useEffect(() => {
    try { sessionStorage.setItem("dvas_cmd_tab", tab); } catch (e) {}
  }, [tab]);

  // Listen for deep-link jumps from the notification bell
  useEffect(() => {
    const handler = (e) => setTab(e.detail || "alerts");
    window.addEventListener("dvas:cmd-tab", handler);
    return () => window.removeEventListener("dvas:cmd-tab", handler);
  }, []);
  const [kpis, setKpis] = useState({});
  const [drones, setDrones] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [incidents, setIncidents] = useState([]);
  const [suggestions, setSuggestions] = useState([]);
  const [alertsSubTab, setAlertsSubTab] = useState("all");
  const [evidence, setEvidence] = useState([]);
  const [missions, setMissions] = useState([]);
  const [exports, setExports] = useState([]);
  const [crowd, setCrowd] = useState([]);
  const [playing, setPlaying] = useState(null);
  const [requestOn, setRequestOn] = useState(null);
  const [justification, setJustification] = useState("");
  const [selectedDrone, setSelectedDrone] = useState(null);

  const [evidenceFilters, setEvidenceFilters] = useState({ drone_id: "", mission_id: "", event_type: "", from_ts: "", to_ts: "", legal_hold_only: false, search: "" });
  const [evidenceTotal, setEvidenceTotal] = useState(0);
  const [evidenceDetail, setEvidenceDetail] = useState(null); // for detail modal
  const [reports, setReports] = useState([]);
  const [generatingReport, setGeneratingReport] = useState(null); // report_id being generated
  const [reportParams, setReportParams] = useState({
    daily_incident: { date: new Date().toISOString().split("T")[0] },
    weekly_response: { week_start: new Date(Date.now() - 6 * 86400000).toISOString().split("T")[0] },
    drone_utilisation: { period_days: 30 },
    mission_after_action: { mission_id: "" },
  });

  const { mergeWithDrones } = useActiveStreams();
  const mergedDrones = mergeWithDrones(drones);
  const liveMissionsCount = mergedDrones.filter((d) => ["live", "draining"].includes(d.status) && Boolean(d.mission_id)).length;

  const load = async () => {
    try {
      const [k, dR, aR, eR, mR, xR, cR, rptR, incR, sugR] = await Promise.all([
        api.get("/command/kpis"),
        api.get("/drones"),
        api.get("/alerts"),
        api.get("/evidence"),
        api.get("/missions"),
        // CRO now has access to their own export requests
        api.get("/exports").catch(() => ({ data: [] })),
        api.get("/analytics/crowd"),
        api.get("/reports").catch(() => ({ data: [] })),
        api.get("/incidents").catch(() => ({ data: [] })),
        api.get("/analytics/suggestions").catch(() => ({ data: [] })),
      ]);
      setKpis(k.data); setDrones(dR.data); setAlerts(aR.data);
      if (incR?.data && Array.isArray(incR.data)) setIncidents(incR.data);
      if (sugR?.data && Array.isArray(sugR.data)) setSuggestions(sugR.data);
      const eData = Array.isArray(eR.data) ? eR.data : (eR.data.items || []);
      setEvidence(eData);
      setEvidenceTotal(eR.data.total || eData.length);
      setMissions(mR.data); setExports(xR.data); setCrowd(cR.data.series || []);
      setReports(rptR.data || []);
    } catch (e) { console.error(e); }
  };

  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, []);

  // WebSocket live updates
  const { connected } = useDvasSocket((msg) => {
    if (msg.type === "telemetry" && Array.isArray(msg.drones)) {
      setDrones((prev) => {
        const existingIds = new Set(prev.map((d) => d.id));
        const updated = prev.map((d) => {
          const u = msg.drones.find((x) => x.id === d.id);
          return u ? { ...d, ...u } : d;
        });
        const brandNew = msg.drones.filter((x) => !existingIds.has(x.id));
        return brandNew.length > 0 ? [...updated, ...brandNew] : updated;
      });
    } else if (msg.type === "alert" && msg.alert) {
      // Only include alerts in scope
      const a = msg.alert;
      // SR receives all alerts; CRO receives only mission-scoped alerts
      const inScope =
        user.role === "senior_officer" ||
        user.role === "control_room_officer" ||
        user.role === "system_administrator" ||
        ((user.mission_scope || []).includes(a.mission_id));
      if (inScope) setAlerts((prev) => [a, ...prev].slice(0, 200));
    } else if (msg.type === "model_suggestion") {
      const s = msg.suggestion || msg;
      setSuggestions((prev) => [s, ...prev.filter(x => x.id !== s.id)]);
    } else if (msg.type === "incident" && msg.incident) {
      const inc = msg.incident;
      setIncidents((prev) => [inc, ...prev.filter(x => x.id !== inc.id)]);
    } else if (msg.type === "incident_ack") {
      setIncidents((prev) => prev.map(x => x.id === msg.incident_id ? { ...x, status: "ACKNOWLEDGED" } : x));
    } else if (msg.type === "model_suggestion_status") {
      setSuggestions((prev) => prev.map(x => x.id === msg.suggestion_id ? { ...x, status: msg.status } : x));
    }
  });

  const ackAlert = async (id) => {
    try { await api.post(`/alerts/${id}/ack`); load(); } catch (e) {}
  };

  const ackIncident = async (id) => {
    try {
      await api.post(`/incidents/${id}/ack`);
      setIncidents((prev) => prev.map(x => x.id === id ? { ...x, status: "ACKNOWLEDGED" } : x));
    } catch (e) {}
  };

  const ackSuggestion = async (id) => {
    try {
      await api.post(`/analytics/suggestions/${id}/status`, { status: "ACKNOWLEDGED" });
      setSuggestions((prev) => prev.map(x => x.id === id ? { ...x, status: "ACKNOWLEDGED" } : x));
    } catch (e) {}
  };

  const requestExport = async () => {
    if (!requestOn || !justification.trim()) return;
    try {
      await api.post("/exports", { evidence_id: requestOn.id, justification });
      setRequestOn(null); setJustification(""); load();
    } catch (e) { alert(e.response?.data?.detail || "failed"); }
  };

  const decide = async (id, decision) => {
    try {
      await api.post(`/exports/${id}/decision`, { decision, comment: `${decision} by ${user.email}` });
      load();
    } catch (e) { alert(e.response?.data?.detail || "failed"); }
  };

  const loadFilteredEvidence = async (filters) => {
      try {
          const params = new URLSearchParams();
          Object.entries(filters).forEach(([k, v]) => {
              if (v !== "" && v !== false) params.set(k, v);
          });
          const res = await api.get(`/evidence?${params.toString()}`);
          const data = res.data;
          setEvidence(Array.isArray(data) ? data : (data.items || []));
          setEvidenceTotal(data.total || 0);
      } catch (e) { console.error(e); }
  };

  const toggleLegalHold = async (ev) => {
      try {
          if (ev.legal_hold) {
              await api.delete(`/evidence/${ev.id}/legal-hold`, { data: { reason: "Released by Senior Officer" } });
          } else {
              const reason = prompt("Enter legal hold reason:");
              if (!reason) return;
              await api.post(`/evidence/${ev.id}/legal-hold`, { reason });
          }
          load();
      } catch (e) { alert(e.response?.data?.detail || "Failed"); }
  };

  const directExport = async (ev) => {
      const justification = prompt("Enter justification for direct export:");
      if (!justification) return;
      try {
          await api.post(`/evidence/${ev.id}/direct-export`, { justification });
          load();
      } catch (e) { alert(e.response?.data?.detail || "Failed"); }
  };

  const generateMissionReport = async (missionId) => {
      try {
          setGeneratingReport(missionId);
          await api.post("/reports/generate", { report_type: "mission_after_action", params: { mission_id: missionId } });
          load();
          setTimeout(() => setGeneratingReport(null), 3000);
      } catch (e) { alert(e.response?.data?.detail || "Failed"); setGeneratingReport(null); }
  };

  const openEvidenceDetail = async (evidenceId) => {
      try {
          const res = await api.get(`/evidence/${evidenceId}`);
          setEvidenceDetail(res.data);
      } catch (e) { alert(e.response?.data?.detail || "Failed"); }
  };

  const generateReport = async (reportType) => {
      try {
          setGeneratingReport(reportType);
          const params = reportParams[reportType] || {};
          await api.post("/reports/generate", { report_type: reportType, params });
          load();
          setTimeout(() => setGeneratingReport(null), 3000);
      } catch (e) { alert(e.response?.data?.detail || "Failed"); setGeneratingReport(null); }
  };

  const downloadReport = (reportId) => {
      const token = localStorage.getItem("dvas_token") || "";
      window.open(`${API}/reports/${reportId}/download?token=${encodeURIComponent(token)}`, "_blank");
  };

  return (
    <AppShell title={t("cmd.title")} subtitle={user.role === "senior_officer" ? t("cmd.subtitle.aggregated") : `${t("cmd.subtitle.jurisdiction")}: ${user.jurisdiction}`}>
      
      {/* ── NEW GLOBAL HEADER ── */}
      <div className="mb-6 p-4 rounded-xl flex flex-col md:flex-row md:items-center justify-between gap-2 shadow-sm border-l-4" style={{ background: "var(--dvas-surface)", borderLeftColor: "var(--dvas-cyan)", borderRight: "1px solid var(--dvas-border)", borderTop: "1px solid var(--dvas-border)", borderBottom: "1px solid var(--dvas-border)" }}>
        <h1 className="text-xl font-bold tracking-tight uppercase font-sans" style={{ color: "var(--dvas-text)" }}>
          Thiruvananthapuram <span className="mx-2 font-light" style={{ color: "var(--dvas-text-dim)" }}>|</span> <span className="text-sm font-medium">{new Date().toLocaleString('en-US', { month: 'long', day: 'numeric', year: 'numeric', hour: 'numeric', minute: 'numeric' })}</span>
        </h1>
        <div className="font-semibold uppercase tracking-widest text-xs" style={{ color: "var(--dvas-cyan)" }}>
          Kerala Police Command Center
        </div>
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-4">
        <KPI label={t("cmd.kpi.dronesOnline")} value={kpis.drones_online} tone="text-emerald-500" />
        <KPI label={t("cmd.kpi.activeMissions")} value={kpis.active_missions} />
        <KPI label={t("cmd.kpi.alertsScope")} value={kpis.alerts_24h} tone="text-amber-500" />
        <KPI label={t("cmd.kpi.evidenceItems")} value={kpis.evidence_total} />
        <KPI label={t("cmd.kpi.exportsPending")} value={kpis.exports_pending} tone="text-red-500" />
      </div>

      {/* Side panel + content */}
      <div className="flex flex-col gap-4">
        <nav
          data-testid="cmd-tabbar"
          className="flex items-center gap-1 overflow-x-auto no-scrollbar pb-px"
          style={{ borderBottom: "1px solid var(--dvas-border)" }}
        >
          <span className="dvas-label pr-3 shrink-0 font-bold text-[10px] uppercase tracking-widest" style={{ color: "var(--dvas-text-dim)" }}>{t("cmd.pages")}</span>
          {ALL_PAGES.filter(p => p.roles.includes(user.role)).map((p, i) => (
            <button
              key={p.key}
              data-testid={`cmd-nav-${p.key}`}
              onClick={() => setTab(p.key)}
              className={`shrink-0 flex items-center gap-2 px-3 py-2 text-xs font-semibold whitespace-nowrap transition-colors rounded-t-md ${
                tab === p.key ? "shadow-sm" : ""
              }`}
              style={{
                borderBottom: tab === p.key ? "2px solid var(--dvas-cyan)" : "2px solid transparent",
                marginBottom: "-1px",
                background: tab === p.key ? "var(--dvas-elev)" : "transparent",
                color: tab === p.key ? "var(--dvas-cyan)" : "var(--dvas-text-muted)"
              }}
            >
              <span className="mono text-[10px] opacity-70 font-normal">{String(i+1).padStart(2,"0")}</span>
              <p.Icon size={14} /> {p.label}
            </button>
          ))}
        </nav>
        <div className="min-w-0">

      {tab === "map" && (
        <div className="grid grid-cols-12 gap-4">
          <div className="col-span-12 lg:col-span-8 rounded-xl overflow-hidden" style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}>
            <div className="p-3 flex items-center justify-between" style={{ borderBottom: "1px solid var(--dvas-border)" }}>
              <div className="flex items-center gap-2 text-sm font-semibold" style={{ color: "var(--dvas-text)" }}>
                <MapPin size={16} className="text-sky-500" /> {t("cmd.page.map")}
                <span className={`ml-2 mono text-[10px] flex items-center gap-1 ${connected ? "text-emerald-500" : "text-slate-400"}`}>
                  <span className={`w-1.5 h-1.5 ${connected ? "bg-emerald-500 dvas-live-dot" : "bg-slate-600"}`} />
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
            <LiveMap drones={mergedDrones} height={480} selectedDroneId={selectedDrone} />
          </div>

          <div className="col-span-12 lg:col-span-4 rounded-xl overflow-hidden flex flex-col" style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}>
            <div className="p-3 text-sm flex items-center gap-2 font-semibold shrink-0" style={{ borderBottom: "1px solid var(--dvas-border)", color: "var(--dvas-text)" }}>
              <Broadcast size={14} className="text-emerald-500 dvas-live-dot" /> {t("cmd.multiDroneGrid")}
            </div>
            <div className="flex-1 min-h-0">
              <MultiDroneGrid dbDrones={drones} selectedDroneId={selectedDrone} onSelectDrone={setSelectedDrone} />
            </div>
          </div>
        </div>
      )}


      {tab === "alerts" && (() => {
        // Normalize suggestions
        const normalizedSuggestions = suggestions.map((s) => {
          const modelList = (s.model_names && s.model_names.length > 0)
            ? s.model_names.join(", ")
            : (s.models || []).map(m => m.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())).join(", ") || "AI Model";
          const missionPrefix = s.mission_id || "Mission";
          return {
            id: s.id,
            category: "suggestion",
            title: `${missionPrefix}: Suggests ${modelList}`,
            subtitle: `${s.suggested_by || "Edge Operator"} · ${s.location || "Ground Station"}`,
            ts: s.timestamp || s.created_at,
            severity: s.priority === "critical" ? "critical" : s.priority === "high" ? "warning" : "info",
            badge: "AI SUGGESTION",
            badgeClass: "bg-sky-500/10 text-sky-400 border-sky-500/30",
            status: s.status || "PENDING",
            raw: s,
          };
        });

        // Normalize incidents
        const normalizedIncidents = incidents.map((inc) => {
          const missionPrefix = inc.mission_id ? `${inc.mission_id}: ` : inc.drone_id ? `${inc.drone_id}: ` : "";
          return {
            id: inc.id,
            category: "incident",
            title: `${missionPrefix}${inc.title}`,
            subtitle: `${inc.created_by || "Edge Operator"} · ${inc.location || "Ground Station"}`,
            ts: inc.timestamp,
            severity: inc.severity === "CRITICAL" ? "critical" : inc.severity === "HIGH" ? "warning" : "info",
            badge: inc.severity || "INCIDENT",
            badgeClass: inc.severity === "CRITICAL" ? "bg-red-500/10 text-red-400 border-red-500/30" : "bg-amber-500/10 text-amber-400 border-amber-500/30",
            status: inc.status || "OPEN",
            raw: inc,
          };
        });

        // Normalize vision alerts
        const normalizedAlerts = alerts.map((a) => {
          const missionPrefix = a.mission_id ? `${a.mission_id}: ` : "";
          const alertName = a.type ? a.type.replace(/_/g, " ").replace(/\b\w/g, l => l.toUpperCase()) : (a.title || "Detection Alert");
          return {
            id: a.id,
            category: "vision",
            title: `${missionPrefix}${alertName}`,
            subtitle: `${a.drone_id || "DRN"} · ${a.zone || "Zone"} · conf ${a.confidence || "—"}`,
            ts: a.ts,
            severity: a.severity === "critical" ? "critical" : "warning",
            badge: "DETECTION",
            badgeClass: a.severity === "critical" ? "bg-red-500/10 text-red-400 border-red-500/30" : "bg-purple-500/10 text-purple-400 border-purple-500/30",
            status: a.acknowledged_by ? "ACKNOWLEDGED" : "OPEN",
            acknowledged_by: a.acknowledged_by,
            raw: a,
          };
        });

        const allItems = [...normalizedSuggestions, ...normalizedIncidents, ...normalizedAlerts]
          .sort((a, b) => new Date(b.ts || 0).getTime() - new Date(a.ts || 0).getTime());

        const displayItems = allItems.filter((item) => {
          if (alertsSubTab === "all") return true;
          if (alertsSubTab === "suggestions") return item.category === "suggestion";
          if (alertsSubTab === "incidents") return item.category === "incident";
          if (alertsSubTab === "vision") return item.category === "vision";
          return true;
        });

        return (
          <div className="rounded-xl overflow-hidden" style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}>
            {/* Header with Sub-tabs */}
            <div className="p-3 flex items-center justify-between flex-wrap gap-2" style={{ borderBottom: "1px solid var(--dvas-border)", color: "var(--dvas-text)" }}>
              <div className="flex items-center gap-2 text-sm font-semibold">
                <Bell size={16} className="text-amber-500" weight="duotone" />
                <span>Command Center Dispatch & Alerts</span>
                <span className="mono text-[10px] text-slate-500 ml-2">({displayItems.length} items)</span>
              </div>

              {/* Sub-tab Filter Pills */}
              <div className="flex items-center gap-1 bg-slate-900/80 p-1 rounded-lg border border-slate-800 text-xs">
                <button
                  onClick={() => setAlertsSubTab("all")}
                  className={`px-2.5 py-1 rounded transition-colors ${
                    alertsSubTab === "all" ? "bg-slate-700 text-white font-medium" : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  All ({allItems.length})
                </button>
                <button
                  onClick={() => setAlertsSubTab("suggestions")}
                  className={`px-2.5 py-1 rounded transition-colors flex items-center gap-1 ${
                    alertsSubTab === "suggestions" ? "bg-sky-600/30 text-sky-300 border border-sky-500/40 font-medium" : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <Cpu size={12} className="text-sky-400" />
                  AI Suggestions ({normalizedSuggestions.length})
                </button>
                <button
                  onClick={() => setAlertsSubTab("incidents")}
                  className={`px-2.5 py-1 rounded transition-colors flex items-center gap-1 ${
                    alertsSubTab === "incidents" ? "bg-amber-600/30 text-amber-300 border border-amber-500/40 font-medium" : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <Warning size={12} className="text-amber-400" />
                  Incidents ({normalizedIncidents.length})
                </button>
                <button
                  onClick={() => setAlertsSubTab("vision")}
                  className={`px-2.5 py-1 rounded transition-colors flex items-center gap-1 ${
                    alertsSubTab === "vision" ? "bg-purple-600/30 text-purple-300 border border-purple-500/40 font-medium" : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  Vision Alerts ({normalizedAlerts.length})
                </button>
              </div>
            </div>

            {/* List */}
            <ul className="divide-y max-h-[600px] overflow-auto" style={{ borderColor: "var(--dvas-border)" }}>
              {displayItems.length === 0 && (
                <li className="p-8 text-center text-slate-500 text-xs">
                  No alerts or messages in this category.
                </li>
              )}
              {displayItems.map((item) => (
                <li key={`${item.category}-${item.id}`} className="p-3.5 hover:bg-[color:var(--dvas-elev)] transition-colors">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-start gap-2.5 min-w-0">
                      {/* Icon */}
                      <div className="mt-0.5 shrink-0">
                        {item.category === "suggestion" ? (
                          <div className="w-7 h-7 rounded-md bg-sky-500/10 border border-sky-500/30 flex items-center justify-center text-sky-400">
                            <Cpu size={14} />
                          </div>
                        ) : item.category === "incident" ? (
                          <div className={`w-7 h-7 rounded-md flex items-center justify-center ${
                            item.severity === "critical"
                              ? "bg-red-500/10 border border-red-500/40 text-red-400"
                              : "bg-amber-500/10 border border-amber-500/40 text-amber-400"
                          }`}>
                            <Warning size={14} />
                          </div>
                        ) : (
                          <div className="w-7 h-7 rounded-md bg-purple-500/10 border border-purple-500/30 flex items-center justify-center text-purple-400">
                            <Warning size={14} />
                          </div>
                        )}
                      </div>

                      {/* Text */}
                      <div className="min-w-0">
                        <div className="flex items-center gap-2 flex-wrap mb-1">
                          <span className={`mono text-[10px] px-1.5 py-0.5 rounded border font-semibold ${item.badgeClass}`}>
                            {item.badge}
                          </span>
                          <span className="mono text-[10px] text-slate-500">
                            {item.ts ? new Date(item.ts).toLocaleString() : ""}
                          </span>
                        </div>

                        {/* Exact format: <MISSION ID>: Suggests <AI Model name> */}
                        <div className="text-sm font-semibold text-slate-100 leading-snug">
                          {item.title}
                        </div>

                        <div className="mono text-[11px] text-slate-400 mt-1">
                          {item.subtitle}
                        </div>
                      </div>
                    </div>

                    {/* Actions / Status */}
                    <div className="shrink-0 flex items-center gap-2 pt-1">
                      {item.category === "suggestion" && (
                        item.status === "ACKNOWLEDGED" ? (
                          <span className="text-[11px] text-emerald-400 flex items-center gap-1 font-mono">
                            <CheckCircle size={12} /> Acknowledged
                          </span>
                        ) : (
                          ["control_room_officer", "senior_officer"].includes(user.role) && (
                            <button
                              onClick={() => ackSuggestion(item.id)}
                              className="text-[11px] px-2.5 py-1 rounded border border-sky-600/60 text-sky-300 hover:bg-sky-500/20 transition-colors"
                            >
                              Acknowledge
                            </button>
                          )
                        )
                      )}

                      {item.category === "incident" && (
                        item.status === "ACKNOWLEDGED" ? (
                          <span className="text-[11px] text-emerald-400 flex items-center gap-1 font-mono">
                            <CheckCircle size={12} /> Acknowledged
                          </span>
                        ) : (
                          ["control_room_officer", "senior_officer"].includes(user.role) && (
                            <button
                              onClick={() => ackIncident(item.id)}
                              className="text-[11px] px-2.5 py-1 rounded border border-amber-600/60 text-amber-300 hover:bg-amber-500/20 transition-colors"
                            >
                              Acknowledge
                            </button>
                          )
                        )
                      )}

                      {item.category === "vision" && (
                        item.acknowledged_by ? (
                          <span className="text-[11px] text-emerald-400 flex items-center gap-1 font-mono">
                            <CheckCircle size={12} /> acked by {item.acknowledged_by}
                          </span>
                        ) : (
                          ["control_room_officer", "senior_officer"].includes(user.role) && (
                            <button
                              onClick={() => ackAlert(item.id)}
                              className="text-[11px] px-2.5 py-1 rounded border border-slate-700 hover:border-sky-500 text-slate-300 transition-colors"
                            >
                              Acknowledge
                            </button>
                          )
                        )
                      )}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        );
      })()}

      {tab === "evidence" && (
          <div className="space-y-4">
              {/* Search & Filter Bar */}
              <div className="rounded-xl p-4 space-y-3" style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}>
                  <div className="flex items-center gap-2 text-sm font-semibold" style={{ color: "var(--dvas-text)" }}>
                      <FilmSlate size={16} className="text-sky-500" /> Evidence Vault
                      <span className="ml-auto mono text-[10px] text-slate-500">{evidenceTotal} items</span>
                  </div>
                  <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-2">
                      <select value={evidenceFilters.drone_id} onChange={e => { const f = {...evidenceFilters, drone_id: e.target.value}; setEvidenceFilters(f); loadFilteredEvidence(f); }} className="bg-slate-800 border border-slate-700 text-xs px-2 py-1.5 rounded text-slate-200">
                          <option value="">All Drones</option>
                          {[...new Set(drones.map(d => d.id))].map(id => <option key={id} value={id}>{id}</option>)}
                      </select>
                      <select value={evidenceFilters.mission_id} onChange={e => { const f = {...evidenceFilters, mission_id: e.target.value}; setEvidenceFilters(f); loadFilteredEvidence(f); }} className="bg-slate-800 border border-slate-700 text-xs px-2 py-1.5 rounded text-slate-200">
                          <option value="">All Missions</option>
                          {missions.map(m => <option key={m.mission_id} value={m.mission_id}>{m.mission_id}</option>)}
                      </select>
                      <select value={evidenceFilters.event_type} onChange={e => { const f = {...evidenceFilters, event_type: e.target.value}; setEvidenceFilters(f); loadFilteredEvidence(f); }} className="bg-slate-800 border border-slate-700 text-xs px-2 py-1.5 rounded text-slate-200">
                          <option value="">All Types</option>
                          {["crowd_surge","vehicle_anomaly","perimeter_intrusion","unattended_object","wrong_way","crowd_threshold","theft_alert","intrusion_alert","vehicle_detected"].map(t => <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>)}
                      </select>
                      <input type="date" value={evidenceFilters.from_ts?.split('T')[0] || ""} onChange={e => { const f = {...evidenceFilters, from_ts: e.target.value ? e.target.value + "T00:00:00" : ""}; setEvidenceFilters(f); loadFilteredEvidence(f); }} className="bg-slate-800 border border-slate-700 text-xs px-2 py-1.5 rounded text-slate-200" placeholder="From" />
                      <input type="date" value={evidenceFilters.to_ts?.split('T')[0] || ""} onChange={e => { const f = {...evidenceFilters, to_ts: e.target.value ? e.target.value + "T23:59:59" : ""}; setEvidenceFilters(f); loadFilteredEvidence(f); }} className="bg-slate-800 border border-slate-700 text-xs px-2 py-1.5 rounded text-slate-200" placeholder="To" />
                      <label className="flex items-center gap-1 text-[10px] text-amber-400 cursor-pointer">
                          <input type="checkbox" checked={evidenceFilters.legal_hold_only} onChange={e => { const f = {...evidenceFilters, legal_hold_only: e.target.checked}; setEvidenceFilters(f); loadFilteredEvidence(f); }} className="accent-amber-500" />
                          Legal Hold Only
                      </label>
                      <input type="text" value={evidenceFilters.search} onChange={e => { const f = {...evidenceFilters, search: e.target.value}; setEvidenceFilters(f); }} onKeyDown={e => { if (e.key === "Enter") loadFilteredEvidence(evidenceFilters); }} placeholder="Search label..." className="bg-slate-800 border border-slate-700 text-xs px-2 py-1.5 rounded text-slate-200 placeholder-slate-500" />
                  </div>
              </div>

              {/* Evidence Cards Grid */}
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                  {evidence.map(ev => (
                      <div key={ev.id} data-testid={`evidence-${ev.id}`} className="rounded-xl overflow-hidden" style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}>
                          <div className="aspect-video relative overflow-hidden bg-slate-900" style={{ background: "var(--dvas-surface-2)" }}>
                              {ev.thumbnail ? (
                                  <img src={ev.thumbnail.startsWith('http') ? ev.thumbnail : `/api/minio/image?bucket=${encodeURIComponent(ev.image_bucket || 'dvas-faces')}&key=${encodeURIComponent(ev.thumbnail)}`} alt="" className="w-full h-full object-cover opacity-90" onError={(e) => { e.target.style.display = 'none'; }} />
                              ) : (
                                  <div className="w-full h-full flex flex-col items-center justify-center text-slate-600 gap-1">
                                      <FilmSlate size={32} />
                                      <span className="mono text-[10px] text-slate-500">{ev.event_type}</span>
                                  </div>
                              )}
                              <button data-testid={`play-${ev.id}`} onClick={() => setPlaying(ev)} className="absolute inset-0 flex items-center justify-center bg-black/20 hover:bg-black/40 transition-colors text-white">
                                  <div className="border border-white/70 px-3 py-1 text-xs bg-black/60 rounded flex items-center gap-1.5">
                                      <Play size={11} fill="currentColor" /> View Evidence
                                  </div>
                              </button>
                              {ev.legal_hold && (
                                  <span className="absolute top-2 left-2 mono text-[10px] px-1.5 py-0.5 border border-red-500/50 bg-white/90 text-red-500 uppercase flex items-center gap-1 shadow-sm rounded-sm"><FolderLock size={10} /> hold</span>
                              )}
                              <button onClick={() => openEvidenceDetail(ev.id)} className="absolute top-2 right-2 mono text-[10px] px-1.5 py-0.5 border border-slate-400/50 bg-black/60 text-slate-200 hover:bg-black/80 rounded-sm">Details</button>
                          </div>
                          <div className="p-3">
                              <div className="text-xs font-medium" style={{ color: "var(--dvas-text)" }}>{ev.label}</div>
                              <div className="mono text-[10px] text-slate-500 mt-1 flex flex-wrap gap-2">
                                  <span>{ev.event_type}</span><span>·</span><span>conf {ev.confidence}</span><span>·</span><span>{ev.duration_s}s</span>
                              </div>
                              <div className="mono text-[10px] text-slate-500">{new Date(ev.ts).toLocaleString()}</div>
                              <div className="mono text-[10px] text-slate-500">{ev.drone_id} · {ev.mission_id}</div>
                              <div className="mt-2 flex flex-wrap gap-1">
                                  {/* CRO: Request Export */}
                                  {user.role === "control_room_officer" && (
                                      <button data-testid={`request-${ev.id}`} onClick={() => setRequestOn(ev)} className="text-[10px] px-2 py-1 border border-slate-700 hover:border-sky-500 text-slate-300">Request Export</button>
                                  )}
                                  {/* SR: Direct Export */}
                                  {user.role === "senior_officer" && (
                                      <button onClick={() => directExport(ev)} className="text-[10px] px-2 py-1 border border-emerald-600/60 text-emerald-300 hover:bg-emerald-500/10">Direct Export</button>
                                  )}
                                  {/* SR: Legal Hold Toggle */}
                                  {user.role === "senior_officer" && (
                                      <button onClick={() => toggleLegalHold(ev)} className={`text-[10px] px-2 py-1 border ${ev.legal_hold ? 'border-red-500/60 text-red-300 hover:bg-red-500/10' : 'border-amber-500/60 text-amber-300 hover:bg-amber-500/10'}`}>
                                          <FolderLock size={10} className="inline mr-1" />{ev.legal_hold ? 'Release Hold' : 'Legal Hold'}
                                      </button>
                                  )}
                                  {/* Generate Mission AAR Report */}
                                  {ev.mission_id && (
                                      <button onClick={() => generateMissionReport(ev.mission_id)} disabled={generatingReport === ev.mission_id} className="text-[10px] px-2 py-1 border border-sky-600/60 text-sky-300 hover:bg-sky-500/10 disabled:opacity-40 flex items-center gap-1">
                                          {generatingReport === ev.mission_id ? <><RefreshCw size={10} className="animate-spin" /> Generating...</> : <><Download size={10} /> AAR Report</>}
                                      </button>
                                  )}
                              </div>
                          </div>
                      </div>
                  ))}
              </div>
              {evidence.length === 0 && <div className="text-center text-slate-500 py-12 text-sm">No evidence items match the current filters</div>}
          </div>
      )}

      {tab === "crowd" && (
        <CrowdAnalytics crowdSeries={crowd} drones={drones} />
      )}

      {tab === "traffic" && (
        <TrafficAnalytics drones={drones} />
      )}

      {tab === "person" && (
        <PersonTrackingAnalytics drones={drones} />
      )}

      {tab === "intrusion" && (
        <IntrusionAnalytics activeDrone={selectedDrone} drones={mergedDrones || drones} missions={missions} user={user} />
      )}

      {tab === "models" && <ModelsPage user={user} t={t} />}

      {tab === "exports" && (
        <div className="space-y-4">
          {/* Section 1: Evidence Export Requests */}
          <div className="rounded-xl overflow-hidden" style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}>
            <div className="p-3 text-sm flex items-center gap-2 font-semibold" style={{ borderBottom: "1px solid var(--dvas-border)", color: "var(--dvas-text)" }}>
              <ArrowSquareOut size={16} className="text-sky-500" /> Evidence Export Requests
              <span className="ml-auto mono text-[10px] text-slate-500">{exports.length} requests</span>
            </div>
            <table className="w-full text-xs">
              <thead style={{ background: "var(--dvas-surface-2)" }}>
                <tr>{["ID","Evidence","Requester","Justification","Status","Actions"].map(h => <th key={h} className="text-left px-3 py-2 dvas-label" style={{ fontSize: 10, borderBottom: "1px solid var(--dvas-border)", color: "var(--dvas-text-dim)" }}>{h}</th>)}</tr>
              </thead>
              <tbody className="mono">
                {exports.length === 0 && <tr><td colSpan={6} className="px-3 py-6 text-center text-slate-500">No export requests in scope</td></tr>}
                {exports.map(x => (
                  <tr key={x.id} data-testid={`export-${x.id}`} className="hover:bg-slate-800/40">
                    <td className="px-3 py-2 border-b border-slate-800/60">{x.id}</td>
                    <td className="px-3 py-2 border-b border-slate-800/60 text-slate-100">{x.evidence_label}</td>
                    <td className="px-3 py-2 border-b border-slate-800/60">{x.requested_by}</td>
                    <td className="px-3 py-2 border-b border-slate-800/60 text-slate-400 max-w-[280px]">{x.justification}</td>
                    <td className="px-3 py-2 border-b border-slate-800/60">
                      <span className={`px-1.5 py-0.5 border text-[10px] ${
                        x.status === "pending" ? "text-amber-400 border-amber-500/40 bg-amber-500/10"
                        : x.status === "approved" ? "text-emerald-400 border-emerald-500/40 bg-emerald-500/10"
                        : "text-red-400 border-red-500/40 bg-red-500/10"
                      }`}>{x.status}</span>
                    </td>
                    <td className="px-3 py-2 border-b border-slate-800/60">
                      {user.role === "senior_officer" && x.status === "pending" && (
                        <div className="flex gap-1">
                          <button data-testid={`approve-${x.id}`} onClick={() => decide(x.id, "approved")} className="px-2 py-1 border border-emerald-600/60 text-emerald-300 hover:bg-emerald-500/10">Approve</button>
                          <button data-testid={`reject-${x.id}`} onClick={() => decide(x.id, "rejected")} className="px-2 py-1 border border-red-600/60 text-red-300 hover:bg-red-500/10">Reject</button>
                        </div>
                      )}
                      {x.status === "approved" && ["control_room_officer","senior_officer"].includes(user.role) && (
                        <a
                          data-testid={`bundle-${x.id}`}
                          href={`${API}/exports/${x.id}/bundle?token=${encodeURIComponent(localStorage.getItem("dvas_token") || "")}`}
                          className="px-2 py-1 border border-sky-600/60 text-sky-300 hover:bg-sky-500/10 inline-flex items-center gap-1"
                        >
                          <ArrowSquareOut size={12} /> 65B Bundle
                        </a>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Section 2: Generated Reports Downloads */}
          <div className="rounded-xl overflow-hidden" style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}>
            <div className="p-3 text-sm flex items-center gap-2 font-semibold" style={{ borderBottom: "1px solid var(--dvas-border)", color: "var(--dvas-text)" }}>
              <FileText size={16} className="text-emerald-500" /> Report Downloads
              <span className="ml-auto mono text-[10px] text-slate-500">{(reports || []).filter(r => r.status === "ready").length} ready</span>
            </div>
            <table className="w-full text-xs">
              <thead style={{ background: "var(--dvas-surface-2)" }}>
                <tr>{["ID","Type","Title","Generated","Size","Download"].map(h => <th key={h} className="text-left px-3 py-2 dvas-label" style={{ fontSize: 10, borderBottom: "1px solid var(--dvas-border)", color: "var(--dvas-text-dim)" }}>{h}</th>)}</tr>
              </thead>
              <tbody className="mono">
                {(reports || []).filter(r => r.status === "ready").length === 0 && <tr><td colSpan={6} className="px-3 py-6 text-center text-slate-500">No reports available for download — generate reports from the Reports tab</td></tr>}
                {(reports || []).filter(r => r.status === "ready").map(r => (
                  <tr key={r.id} className="hover:bg-slate-800/40">
                    <td className="px-3 py-2 border-b border-slate-800/60 text-slate-300">{r.id}</td>
                    <td className="px-3 py-2 border-b border-slate-800/60 text-slate-400">{(r.report_type || "").replace(/_/g, " ")}</td>
                    <td className="px-3 py-2 border-b border-slate-800/60 text-slate-200 max-w-[200px] truncate">{r.title}</td>
                    <td className="px-3 py-2 border-b border-slate-800/60 text-slate-500">{r.generated_at ? new Date(r.generated_at).toLocaleString() : "—"}</td>
                    <td className="px-3 py-2 border-b border-slate-800/60 text-slate-500">{r.file_size_bytes ? `${(r.file_size_bytes / 1024).toFixed(1)} KB` : "—"}</td>
                    <td className="px-3 py-2 border-b border-slate-800/60">
                      {user.role === "senior_officer" ? (
                        <button onClick={() => downloadReport(r.id)} className="px-2 py-1 border border-sky-600/60 text-sky-300 hover:bg-sky-500/10 flex items-center gap-1 text-[10px]">
                          <Download size={10} /> Download PDF
                        </button>
                      ) : (
                        <span className="text-[10px] text-slate-500 italic">SR Only</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === "multidrone" && (
        <MultiDroneView
          drones={drones}
          alerts={alerts}
          connected={connected}
        />
      )}

      {/* Forensic Log Viewer removed per overhaul requirements */}

      {tab === "aggregated" && user.role === "senior_officer" && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {[["Incidents · state",kpis.alerts_24h,"text-amber-400"],["Active missions",kpis.active_missions,"text-sky-400"],["Drones online",kpis.drones_online,"text-emerald-400"],["Evidence retained",kpis.evidence_total,"text-slate-100"]].map(([l,v,t])=>(
            <div key={l} className="border border-slate-800 bg-slate-900 p-4"><div className="dvas-label">{l}</div><div className={`mono text-3xl mt-1 ${t}`}>{v ?? "—"}</div></div>
          ))}
          <div className="col-span-2 md:col-span-4 border border-slate-800 bg-slate-900 p-4 mono text-[11px] text-slate-400">ABAC prevents drill-down into any single district's active operational controls outside your assigned scope. This view is aggregate-only.</div>
        </div>
      )}

      {tab === "reports" && (
        <div className="space-y-4">
          {/* ── Report Type Cards (SR only) ── */}
          {user.role !== "senior_officer" && (
            <div className="rounded-xl p-4" style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}>
              <div className="flex items-center gap-2 text-sm text-amber-400 mb-2"><Shield size={16} /> Report generation is restricted to Senior Officers</div>
              <div className="text-xs text-slate-400">Only Senior Officers can generate and download reports directly. Contact your Senior Officer to request a report.</div>
            </div>
          )}
          {user.role === "senior_officer" && (
          <div className="rounded-xl p-4" style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}>
            <div className="flex items-center gap-2 text-sm font-semibold mb-4" style={{ color: "var(--dvas-text)" }}>
              <FileText size={16} className="text-sky-500" /> On-Demand Report Generator
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">

              {/* Daily Incident Summary */}
              <div className="border border-slate-800 rounded-lg p-4 space-y-3" style={{ background: "var(--dvas-surface-2)" }}>
                <div className="flex items-center gap-2">
                  <Bell size={14} className="text-amber-400" />
                  <span className="text-sm font-medium text-slate-100">Daily Incident Summary</span>
                </div>
                <div className="mono text-[10px] text-slate-500">All alerts, evidence items, and active missions for a specific date. Includes acknowledgement rates and type breakdown.</div>
                <div className="flex items-center gap-2">
                  <label className="text-[10px] text-slate-400">Date:</label>
                  <input type="date" value={reportParams.daily_incident.date} onChange={e => setReportParams(p => ({...p, daily_incident: { date: e.target.value }}))} className="bg-slate-800 border border-slate-700 text-xs px-2 py-1 rounded text-slate-200" />
                </div>
                <div className="flex items-center justify-between">
                  <div className="mono text-[10px] text-slate-500">Watermarked · {user.email}</div>
                  <button onClick={() => generateReport("daily_incident")} disabled={generatingReport === "daily_incident"} className="px-3 py-1.5 border border-sky-600/60 text-sky-300 hover:bg-sky-500/10 text-xs flex items-center gap-1 disabled:opacity-40">
                    {generatingReport === "daily_incident" ? <><RefreshCw size={11} className="animate-spin" /> Generating...</> : <><Download size={11} /> Generate</>}
                  </button>
                </div>
              </div>

              {/* Weekly Response-Time Report */}
              <div className="border border-slate-800 rounded-lg p-4 space-y-3" style={{ background: "var(--dvas-surface-2)" }}>
                <div className="flex items-center gap-2">
                  <Clock size={14} className="text-sky-400" />
                  <span className="text-sm font-medium text-slate-100">Weekly Response-Time Report</span>
                </div>
                <div className="mono text-[10px] text-slate-500">Alert-to-acknowledgement response times, daily breakdown, per-type analysis, and mission hours for a 7-day period.</div>
                <div className="flex items-center gap-2">
                  <label className="text-[10px] text-slate-400">Week start:</label>
                  <input type="date" value={reportParams.weekly_response.week_start} onChange={e => setReportParams(p => ({...p, weekly_response: { week_start: e.target.value }}))} className="bg-slate-800 border border-slate-700 text-xs px-2 py-1 rounded text-slate-200" />
                </div>
                <div className="flex items-center justify-between">
                  <div className="mono text-[10px] text-slate-500">Watermarked · {user.email}</div>
                  <button onClick={() => generateReport("weekly_response")} disabled={generatingReport === "weekly_response"} className="px-3 py-1.5 border border-sky-600/60 text-sky-300 hover:bg-sky-500/10 text-xs flex items-center gap-1 disabled:opacity-40">
                    {generatingReport === "weekly_response" ? <><RefreshCw size={11} className="animate-spin" /> Generating...</> : <><Download size={11} /> Generate</>}
                  </button>
                </div>
              </div>

              {/* Drone Utilisation (30d) */}
              <div className="border border-slate-800 rounded-lg p-4 space-y-3" style={{ background: "var(--dvas-surface-2)" }}>
                <div className="flex items-center gap-2">
                  <Activity size={14} className="text-emerald-400" />
                  <span className="text-sm font-medium text-slate-100">Drone Utilisation (30-Day)</span>
                </div>
                <div className="mono text-[10px] text-slate-500">Fleet summary, per-drone mission counts and hours, average mission duration, and top operators ranked by flight time.</div>
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-slate-400 italic">Period: last 30 days (automatic)</span>
                </div>
                <div className="flex items-center justify-between">
                  <div className="mono text-[10px] text-slate-500">Watermarked · {user.email}</div>
                  <button onClick={() => generateReport("drone_utilisation")} disabled={generatingReport === "drone_utilisation"} className="px-3 py-1.5 border border-sky-600/60 text-sky-300 hover:bg-sky-500/10 text-xs flex items-center gap-1 disabled:opacity-40">
                    {generatingReport === "drone_utilisation" ? <><RefreshCw size={11} className="animate-spin" /> Generating...</> : <><Download size={11} /> Generate</>}
                  </button>
                </div>
              </div>

              {/* Mission After-Action Report */}
              <div className="border border-slate-800 rounded-lg p-4 space-y-3" style={{ background: "var(--dvas-surface-2)" }}>
                <div className="flex items-center gap-2">
                  <Shield size={14} className="text-red-400" />
                  <span className="text-sm font-medium text-slate-100">Mission After-Action Report</span>
                </div>
                <div className="mono text-[10px] text-slate-500">Full mission debrief: objective, timeline, all alerts, evidence captured, acknowledgement rates, and duration analysis.</div>
                <div className="flex items-center gap-2">
                  <label className="text-[10px] text-slate-400">Mission:</label>
                  <select value={reportParams.mission_after_action.mission_id} onChange={e => setReportParams(p => ({...p, mission_after_action: { mission_id: e.target.value }}))} className="bg-slate-800 border border-slate-700 text-xs px-2 py-1 rounded text-slate-200 flex-1">
                    <option value="">Select mission...</option>
                    {missions.map(m => (
                      <option key={m.mission_id} value={m.mission_id}>
                        {m.name || m.mission_id} ({m.mission_id}) — Op: {m.operator_name || "—"}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="flex items-center justify-between">
                  <div className="mono text-[10px] text-slate-500">Watermarked · {user.email}</div>
                  <button onClick={() => generateReport("mission_after_action")} disabled={generatingReport === "mission_after_action" || !reportParams.mission_after_action.mission_id} className="px-3 py-1.5 border border-sky-600/60 text-sky-300 hover:bg-sky-500/10 text-xs flex items-center gap-1 disabled:opacity-40">
                    {generatingReport === "mission_after_action" ? <><RefreshCw size={11} className="animate-spin" /> Generating...</> : <><Download size={11} /> Generate</>}
                  </button>
                </div>
              </div>

            </div>
          </div>
          )}

          {/* ── Generated Reports Table ── */}
          <div className="rounded-xl overflow-hidden" style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}>
            <div className="p-3 text-sm flex items-center gap-2 font-semibold" style={{ borderBottom: "1px solid var(--dvas-border)", color: "var(--dvas-text)" }}>
              <FileText size={16} className="text-emerald-500" /> Generated Reports
              <span className="ml-auto mono text-[10px] text-slate-500">{reports.length} reports</span>
            </div>
            <table className="w-full text-xs">
              <thead style={{ background: "var(--dvas-surface-2)" }}>
                <tr>{["ID", "Type", "Title", "Status", "Generated", "Size", "Actions"].map(h => <th key={h} className="text-left px-3 py-2 dvas-label" style={{ fontSize: 10, borderBottom: "1px solid var(--dvas-border)", color: "var(--dvas-text-dim)" }}>{h}</th>)}</tr>
              </thead>
              <tbody className="mono">
                {reports.length === 0 && <tr><td colSpan={7} className="px-3 py-6 text-center text-slate-500">No reports generated yet — use the cards above to generate your first report</td></tr>}
                {(Array.isArray(reports) ? reports : []).map(r => (
                  <tr key={r.id} className="hover:bg-slate-800/40">
                    <td className="px-3 py-2 border-b border-slate-800/60 text-slate-300">{r.id}</td>
                    <td className="px-3 py-2 border-b border-slate-800/60 text-slate-400">{(r.report_type || "").replace(/_/g, " ")}</td>
                    <td className="px-3 py-2 border-b border-slate-800/60 text-slate-200 max-w-[200px] truncate">{r.title}</td>
                    <td className="px-3 py-2 border-b border-slate-800/60">
                      <span className={`px-1.5 py-0.5 border text-[10px] ${
                        r.status === "generating" ? "text-amber-400 border-amber-500/40 bg-amber-500/10" :
                        r.status === "ready" ? "text-emerald-400 border-emerald-500/40 bg-emerald-500/10" :
                        "text-red-400 border-red-500/40 bg-red-500/10"
                      }`}>
                        {r.status === "generating" && <RefreshCw size={9} className="inline animate-spin mr-1" />}
                        {r.status === "ready" && <CheckCircle size={9} className="inline mr-1" />}
                        {r.status}
                      </span>
                    </td>
                    <td className="px-3 py-2 border-b border-slate-800/60 text-slate-500">{r.generated_at ? new Date(r.generated_at).toLocaleString() : "—"}</td>
                    <td className="px-3 py-2 border-b border-slate-800/60 text-slate-500">{r.file_size_bytes ? `${(r.file_size_bytes / 1024).toFixed(1)} KB` : "—"}</td>
                    <td className="px-3 py-2 border-b border-slate-800/60">
                      {r.status === "ready" && user.role === "senior_officer" && (
                        <button onClick={() => downloadReport(r.id)} className="px-2 py-1 border border-sky-600/60 text-sky-300 hover:bg-sky-500/10 flex items-center gap-1 text-[10px]">
                          <Download size={10} /> Download PDF
                        </button>
                      )}
                      {r.status === "ready" && user.role !== "senior_officer" && (
                        <span className="text-[10px] text-slate-500 italic">SR Only</span>
                      )}
                      {r.status === "failed" && (
                        <span className="text-[10px] text-red-400" title={r.error_message || ""}>Failed — hover for details</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

        </div>
      </div>
      {/* Playback modal */}
      {playing && (
        <div className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-6" onClick={() => setPlaying(null)}>
          <div className="border border-slate-700 bg-slate-950 max-w-4xl w-full" onClick={e => e.stopPropagation()}>
            <div className="p-3 border-b border-slate-800 flex items-center justify-between">
              <div className="text-sm">{playing.label}</div>
              <button data-testid="close-player" onClick={() => setPlaying(null)}><X size={16} /></button>
            </div>
            {(playing.primary_image_key || playing.thumbnail) ? (
              <div className="w-full aspect-video bg-slate-900 flex items-center justify-center gap-4 p-4">
                <img
                  src={`/api/minio/image?bucket=${encodeURIComponent(playing.image_bucket || 'dvas-faces')}&key=${encodeURIComponent(playing.primary_image_key || playing.thumbnail)}`}
                  alt="Evidence capture"
                  className="max-h-full max-w-[48%] object-contain rounded border border-slate-700"
                  onError={(e) => { e.target.style.display = 'none'; }}
                />
                {playing.secondary_image_key && (
                  <img
                    src={`/api/minio/image?bucket=${encodeURIComponent(playing.image_bucket === 'dvas-vehicles' ? 'dvas-plates' : playing.image_bucket || 'dvas-faces')}&key=${encodeURIComponent(playing.secondary_image_key)}`}
                    alt="Secondary evidence capture"
                    className="max-h-full max-w-[48%] object-contain rounded border border-slate-700"
                    onError={(e) => { e.target.style.display = 'none'; }}
                  />
                )}
              </div>
            ) : (
              <div className="w-full aspect-video bg-slate-900 flex flex-col items-center justify-center text-slate-500 p-8">
                <svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1" className="mb-4 text-slate-600"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
                <p className="text-sm font-medium">Image Unavailable</p>
                <p className="text-xs text-slate-600 mt-1">Event recorded without visual capture</p>
              </div>
            )}
            <div className="p-3 mono text-[11px] text-slate-400 flex flex-wrap gap-3">
              <span>{playing.event_type}</span><span>conf {playing.confidence}</span>
              <span>duration {playing.duration_s}s</span><span>mission {playing.mission_id}</span>
              <span className="ml-auto">retention until {new Date(playing.retention_until).toLocaleDateString()}</span>
            </div>
          </div>
        </div>
      )}

      {/* Evidence Detail Modal */}
      {evidenceDetail && (
          <div className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-6" onClick={() => setEvidenceDetail(null)}>
              <div className="border border-slate-700 bg-slate-950 max-w-3xl w-full max-h-[85vh] overflow-y-auto rounded-xl" onClick={e => e.stopPropagation()}>
                  <div className="p-4 border-b border-slate-800 flex items-center justify-between">
                      <div className="text-sm font-semibold" style={{ color: "var(--dvas-text)" }}>Evidence Detail — {evidenceDetail.evidence?.id}</div>
                      <button onClick={() => setEvidenceDetail(null)}><X size={16} /></button>
                  </div>
                  <div className="p-4 space-y-4">
                      {/* Metadata Grid */}
                      <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs">
                          {[["Label", evidenceDetail.evidence?.label], ["Event Type", evidenceDetail.evidence?.event_type], ["Drone", evidenceDetail.evidence?.drone_id], ["Mission", evidenceDetail.evidence?.mission_id], ["Confidence", evidenceDetail.evidence?.confidence], ["Duration", `${evidenceDetail.evidence?.duration_s}s`], ["Jurisdiction", evidenceDetail.evidence?.jurisdiction], ["Captured", evidenceDetail.evidence?.ts ? new Date(evidenceDetail.evidence.ts).toLocaleString() : "-"], ["Legal Hold", evidenceDetail.evidence?.legal_hold ? `Yes — ${evidenceDetail.evidence.legal_hold_reason || ''}` : "No"], ["Retention Until", evidenceDetail.evidence?.retention_until ? new Date(evidenceDetail.evidence.retention_until).toLocaleDateString() : "-"]].map(([k, v]) => (
                              <div key={k}><span className="dvas-label text-[10px]">{k}</span><div className="mono text-slate-200 mt-0.5">{v || "—"}</div></div>
                          ))}
                      </div>

                      {/* Legal Hold Info */}
                      {evidenceDetail.evidence?.legal_hold && (
                          <div className="border border-red-500/30 bg-red-500/5 rounded p-3 text-xs">
                              <div className="text-red-400 font-semibold flex items-center gap-1"><FolderLock size={12} /> Legal Hold Active</div>
                              <div className="text-slate-400 mt-1">Reason: {evidenceDetail.evidence.legal_hold_reason}</div>
                              <div className="text-slate-500 mt-0.5">Placed by: {evidenceDetail.evidence.legal_hold_by} on {evidenceDetail.evidence.legal_hold_at ? new Date(evidenceDetail.evidence.legal_hold_at).toLocaleString() : "-"}</div>
                          </div>
                      )}

                      {/* Associated Exports */}
                      {evidenceDetail.exports?.length > 0 && (
                          <div>
                              <div className="dvas-label text-[10px] mb-2">Associated Exports</div>
                              <div className="space-y-1">
                                  {evidenceDetail.exports.map(x => (
                                      <div key={x.id} className="flex items-center justify-between text-xs border border-slate-800 rounded p-2">
                                          <div className="mono text-slate-300">{x.id}</div>
                                          <div className="text-slate-400">{x.requested_by}</div>
                                          <span className={`px-1.5 py-0.5 border text-[10px] ${x.status === "pending" ? "text-amber-400 border-amber-500/40" : x.status === "approved" ? "text-emerald-400 border-emerald-500/40" : "text-red-400 border-red-500/40"}`}>{x.status}</span>
                                          {x.status === "approved" && (
                                              <a href={`${API}/exports/${x.id}/bundle?token=${encodeURIComponent(localStorage.getItem("dvas_token") || "")}`} className="text-sky-400 hover:text-sky-300 text-[10px]">Download 65B Bundle</a>
                                          )}
                                      </div>
                                  ))}
                              </div>
                          </div>
                      )}

                      {/* Action Buttons */}
                      <div className="flex flex-wrap gap-2 pt-2 border-t border-slate-800">
                          <button onClick={() => { setPlaying(evidenceDetail.evidence); setEvidenceDetail(null); }} className="px-3 py-1.5 border border-sky-600/60 text-sky-300 hover:bg-sky-500/10 text-xs flex items-center gap-1"><Play size={12} /> View Evidence</button>
                          {user.role === "control_room_officer" && (
                              <button onClick={() => { setRequestOn(evidenceDetail.evidence); setEvidenceDetail(null); }} className="px-3 py-1.5 border border-slate-700 text-slate-300 hover:border-sky-500 text-xs">Request Export</button>
                          )}
                          {user.role === "senior_officer" && (
                              <button onClick={() => { directExport(evidenceDetail.evidence); setEvidenceDetail(null); }} className="px-3 py-1.5 border border-emerald-600/60 text-emerald-300 hover:bg-emerald-500/10 text-xs">Direct Export</button>
                          )}
                      </div>
                  </div>
              </div>
          </div>
      )}

      {/* Export request modal */}
      {requestOn && (
        <div className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-6" onClick={() => setRequestOn(null)}>
          <div className="border border-slate-700 bg-slate-950 max-w-md w-full p-4" onClick={e => e.stopPropagation()}>
            <div className="dvas-label">Request Export</div>
            <div className="text-sm mt-1 mb-3">{requestOn.label}</div>
            <label className="dvas-label block mb-1">Justification</label>
            <textarea
              data-testid="input-justification"
              value={justification}
              onChange={e => setJustification(e.target.value)}
              rows={4}
              className="w-full bg-slate-900 border border-slate-700 focus:border-sky-500 outline-none p-2 text-sm mono"
              placeholder="Cr. No / Case ref / purpose…"
            />
            <div className="mt-3 flex justify-end gap-2 text-xs">
              <button onClick={() => setRequestOn(null)} className="px-3 py-1.5 border border-slate-700">Cancel</button>
              <button data-testid="submit-export-request" onClick={requestExport} className="px-3 py-1.5 bg-sky-600 hover:bg-sky-500">Submit</button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}

/* ---------- Detection Models (GPU Layer) ---------- */
function ModelsPage({ user, t }) {
  const [cfg, setCfg]         = useState(null);
  const [selected, setSelected] = useState([]);
  const [saving, setSaving]   = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [msg, setMsg]         = useState({ text: "", ok: true });
  const [actions, setActions] = useState(null); // stream_actions from last deploy

  const load = async (silent = false) => {
    if (!silent) setRefreshing(true);
    try {
      const { data } = await api.get("/models");
      setCfg(data);
      // Only reset selection on first load, not on refresh
      if (!cfg) setSelected(data.active);
    } catch (e) {
      console.error(e);
    } finally {
      setRefreshing(false);
    }
  };

  // Initial load
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, []);

  // Auto-refresh stream status every 15 seconds (lightweight health poll)
  useEffect(() => {
    const id = setInterval(() => load(true), 15000);
    return () => clearInterval(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggle = (key) => {
    if (cfg?.read_only) return;
    setSelected(s => s.includes(key) ? s.filter(x => x !== key) : [...s, key]);
  };

  const deploy = async () => {
    setSaving(true); setMsg({ text: "", ok: true }); setActions(null);
    try {
      const { data } = await api.post("/models", { active: selected });
      setCfg(c => ({ ...c, ...data }));
      setActions(data.stream_actions || null);

      // Build human-readable summary
      const started = Object.entries(data.stream_actions || {})
        .filter(([, v]) => v === "started").map(([k]) => k);
      const stopped = Object.entries(data.stream_actions || {})
        .filter(([, v]) => v === "stopped").map(([k]) => k);
      let summary = "Deployed.";
      if (started.length) summary += ` Started: ${started.join(", ")}.`;
      if (stopped.length) summary += ` Stopped: ${stopped.join(", ")}.`;
      if (!started.length && !stopped.length) summary = "No stream changes — already in desired state.";
      setMsg({ text: summary, ok: true });
      setTimeout(() => setMsg({ text: "", ok: true }), 5000);
    } catch (e) {
      setMsg({ text: e?.response?.data?.detail || "Deploy failed", ok: false });
    } finally { setSaving(false); }
  };

  if (!cfg) return (
    <div className="border border-slate-800 bg-slate-900 p-8 text-center text-xs text-slate-500 flex items-center justify-center gap-2">
      <RefreshCw size={13} className="animate-spin" /> Loading model roster and stream status…
    </div>
  );

  const gpuUsed = cfg.available.filter(m => selected.includes(m.key)).reduce((s, m) => s + m.gpu_pct, 0);
  const fpsMin  = cfg.available.filter(m => selected.includes(m.key)).reduce((s, m) => Math.min(s, m.throughput_fps), Infinity);
  const gpuTone = gpuUsed > 90 ? "text-red-400" : gpuUsed > 70 ? "text-amber-400" : "text-emerald-400";
  const dirty   = JSON.stringify([...selected].sort()) !== JSON.stringify([...cfg.active].sort());
  const streamStatus = cfg.stream_status || {};
  const runningCount = Object.values(streamStatus).filter(Boolean).length;

  // Action badge colour helper
  const actionColour = (v) => {
    if (!v) return "text-slate-500";
    if (v === "started")         return "text-emerald-400";
    if (v === "stopped")         return "text-amber-400";
    if (v === "already_running") return "text-sky-400";
    if (v === "already_stopped") return "text-slate-500";
    return "text-red-400"; // errors
  };

  const actionLabel = (v) => {
    if (!v) return "";
    if (v === "started")         return "▶ Started";
    if (v === "stopped")         return "■ Stopped";
    if (v === "already_running") return "● Running";
    if (v === "already_stopped") return "○ Idle";
    return v; // show raw error string
  };

  return (
    <div className="flex flex-col gap-4">

      {/* ── KPI strip ──────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <div className="border border-slate-800 bg-slate-900 p-4">
          <div className="dvas-label">Scope</div>
          <div className="mono text-lg mt-1 text-slate-100">{cfg.scope}</div>
          {cfg.read_only && <div className="mono text-[10px] text-amber-300 mt-1">Read-only</div>}
        </div>
        <div className="border border-slate-800 bg-slate-900 p-4">
          <div className="dvas-label">Selected</div>
          <div className="mono text-2xl mt-1 text-sky-400">{selected.length}<span className="text-slate-500 text-sm"> / {cfg.available.length}</span></div>
          <div className="mono text-[9px] text-slate-500 mt-1">toggled for deploy</div>
        </div>
        <div className="border border-slate-800 bg-slate-900 p-4">
          <div className="dvas-label">Running Now</div>
          <div className={`mono text-2xl mt-1 font-semibold ${runningCount > 0 ? "text-emerald-400" : "text-slate-500"}`}>
            {runningCount}<span className="text-slate-500 text-sm"> / 5</span>
          </div>
          <div className="mono text-[9px] text-slate-500 mt-1">live stream loops</div>
        </div>
        <div className="border border-slate-800 bg-slate-900 p-4">
          <div className="dvas-label">GPU Allocation</div>
          <div className={`mono text-2xl mt-1 font-semibold ${gpuTone}`}>{gpuUsed}%</div>
          <div className="h-1.5 bg-slate-800 rounded-full mt-2 overflow-hidden">
            <div className={`h-full ${gpuUsed > 90 ? "bg-red-500" : gpuUsed > 70 ? "bg-amber-500" : "bg-emerald-500"} transition-all`}
              style={{ width: `${Math.min(100, gpuUsed)}%` }} />
          </div>
        </div>
        <div className="border border-slate-800 bg-slate-900 p-4">
          <div className="dvas-label">Bottleneck FPS</div>
          <div className="mono text-2xl mt-1 text-slate-100">{fpsMin === Infinity ? "—" : fpsMin}</div>
          <div className="mono text-[10px] text-slate-500 mt-1">slowest active pipeline</div>
        </div>
      </div>

      {/* ── Deploy result / stream actions feedback ──────────────────── */}
      {actions && (
        <div className="border border-slate-700 bg-slate-900 p-3 rounded-lg">
          <div className="mono text-[10px] text-slate-400 mb-2 uppercase tracking-widest">Last Deploy — Stream Actions</div>
          <div className="flex flex-wrap gap-3">
            {Object.entries(actions).map(([svc, result]) => (
              <div key={svc} className="flex items-center gap-1.5">
                <span className="mono text-[11px] text-slate-300 font-semibold">{svc}:</span>
                <span className={`mono text-[11px] font-bold ${actionColour(result)}`}>{actionLabel(result)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Model cards ──────────────────────────────────────────────── */}
      <div className="border border-slate-800 bg-slate-900">
        <div className="p-3 border-b border-slate-800 flex items-center gap-2 text-sm">
          <Cpu size={16} className="text-sky-400" />
          <span className="text-slate-100 font-semibold">Detection Models on GPU Layer</span>
          <span className="mono text-[10px] text-slate-500 ml-1">Toggle to select, then Deploy to start/stop inference.</span>
          <div className="ml-auto flex items-center gap-3">
            {cfg.updated_at && (
              <span className="mono text-[10px] text-slate-500">
                last deploy · {new Date(cfg.updated_at).toLocaleString()} · {cfg.updated_by || "—"}
              </span>
            )}
            <button
              onClick={() => load(false)}
              disabled={refreshing}
              title="Refresh stream status"
              className="flex items-center gap-1 px-2 py-1 border border-slate-700 rounded text-[10px] mono text-slate-400 hover:border-sky-600 hover:text-sky-400 transition-colors disabled:opacity-50"
            >
              <RefreshCw size={11} className={refreshing ? "animate-spin" : ""} />
              {refreshing ? "Refreshing…" : "Refresh Status"}
            </button>
          </div>
        </div>

        <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-3">
          {cfg.available.map(m => {
            const checked      = selected.includes(m.key);
            const isRunning    = m.stream_running === true;   // from health endpoint
            const ModelIcon    = MODEL_ICONS[m.key] || Cpu;
            const lastAction   = actions?.[m.key === "face" ? "person" : m.key] ||
                                 actions?.[m.key];

            return (
              <label
                key={m.key}
                data-testid={`model-${m.key}`}
                data-active={checked ? "true" : "false"}
                data-running={isRunning ? "true" : "false"}
                className={`border p-3 flex items-start gap-3 cursor-pointer transition-all rounded-lg ${
                  isRunning
                    ? "border-emerald-500/50 bg-emerald-500/5"
                    : checked
                      ? "border-sky-500/60 bg-sky-500/8"
                      : "border-slate-800 bg-slate-900 hover:border-slate-700"
                } ${cfg.read_only ? "cursor-not-allowed opacity-70" : ""}`}
                onClick={(e) => { e.preventDefault(); toggle(m.key); }}
              >
                {/* Icon */}
                <div className={`h-9 w-9 flex items-center justify-center rounded-lg shrink-0 ${
                  isRunning ? "bg-emerald-500/20 text-emerald-300"
                  : checked  ? "bg-sky-500/20 text-sky-300"
                             : "bg-slate-800 text-slate-500"
                }`}>
                  <ModelIcon size={16} />
                </div>

                {/* Info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <div className="text-sm text-slate-100 font-medium">{m.label}</div>
                    {m.default && <span className="mono text-[9px] px-1.5 py-0.5 border border-slate-700 text-slate-400 uppercase">default</span>}
                    {/* Real running status badge */}
                    {isRunning ? (
                      <span className="mono text-[9px] px-1.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 flex items-center gap-1">
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block" style={{ animation: 'pulse 2s infinite' }} />
                        RUNNING
                      </span>
                    ) : (
                      <span className="mono text-[9px] px-1.5 py-0.5 rounded-full bg-slate-800 text-slate-500 border border-slate-700">
                        IDLE
                      </span>
                    )}
                  </div>
                  <div className="mono text-[10px] text-slate-500 mt-1 flex items-center gap-3">
                    <span>GPU <span className="text-slate-300">{m.gpu_pct}%</span></span>
                    <span>·</span>
                    <span>Peak <span className="text-slate-300">{m.throughput_fps} fps</span></span>
                    {lastAction && (
                      <><span>·</span>
                      <span className={`font-bold ${actionColour(lastAction)}`}>{actionLabel(lastAction)}</span></>
                    )}
                  </div>
                </div>

                {/* Toggle checkbox */}
                <input
                  type="checkbox"
                  data-testid={`model-toggle-${m.key}`}
                  checked={checked}
                  onChange={() => toggle(m.key)}
                  disabled={cfg.read_only}
                  className="h-5 w-5 accent-sky-500 shrink-0 mt-1"
                />
              </label>
            );
          })}
        </div>

        {/* ── Legend ─────────────────────────────────────────────────── */}
        <div className="px-4 pb-3 flex items-center gap-4 text-[10px] mono text-slate-500">
          <div className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-emerald-400" />Running — frame inference active</div>
          <div className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-sky-500" />Selected — will start on Deploy</div>
          <div className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-slate-600" />Idle — not processing frames</div>
        </div>

        {/* ── Deploy bar ─────────────────────────────────────────────── */}
        {!cfg.read_only && (
          <div className="p-3 border-t border-slate-800 flex items-center gap-3">
            {gpuUsed > 100 && (
              <div className="mono text-[11px] text-red-400 flex items-center gap-1.5">
                <Warning size={12} /> GPU over-committed ({gpuUsed}%) — reduce active models before deploying
              </div>
            )}
            {msg.text && (
              <div className={`mono text-[11px] flex items-center gap-1.5 ${
                msg.ok ? "text-emerald-400" : "text-red-400"
              }`}>
                {msg.ok ? <CheckCircle2 size={12} /> : <Warning size={12} />}
                {msg.text}
              </div>
            )}
            <div className="ml-auto flex gap-2">
              <button
                data-testid="models-reset"
                disabled={!dirty || saving}
                onClick={() => { setSelected(cfg.active); setActions(null); setMsg({ text: "", ok: true }); }}
                className="px-3 py-1.5 border border-slate-700 text-xs rounded-lg disabled:opacity-50 text-slate-300 hover:border-slate-500 transition-colors"
              >
                Reset
              </button>
              <button
                data-testid="models-save"
                disabled={!dirty || saving || gpuUsed > 100}
                onClick={deploy}
                className="px-4 py-1.5 bg-sky-600 hover:bg-sky-500 text-white text-xs font-semibold rounded-lg disabled:opacity-50 transition-colors flex items-center gap-1.5"
              >
                {saving ? <><RefreshCw size={11} className="animate-spin" /> Deploying…</> : <><Play size={11} fill="currentColor" /> Deploy to GPU cluster</>}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
