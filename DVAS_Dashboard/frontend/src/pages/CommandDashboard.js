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
import { useActiveStreams } from "@/hooks/useActiveStreams";
import {
  MapPin, Radio as Broadcast, Bell, Film as FilmSlate,
  BarChart3 as ChartBar, AlertTriangle as Warning, CheckCircle, X,
  ExternalLink as ArrowSquareOut, Folder as FolderLock, Download,
  Cpu, ShieldCheck, User as UserIcon, Users as UsersIcon, Car, Hash,
  UserSearch, RefreshCw, Play, Square, CheckCircle2,
  Activity, Battery, BatteryLow, BatteryMedium, BatteryFull,
  Wifi, WifiOff, Clock, Radio,
} from "lucide-react";
import LiveFrame from "../components/LiveFrame";
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
  { key: "models",    label: "Detection Models",     Icon: Cpu,             roles: ["control_room_officer","senior_officer"] },
  { key: "exports",   label: "Exports",              Icon: ArrowSquareOut,  roles: ["control_room_officer","senior_officer"] },
  { key: "aggregated",label: "Aggregated View",      Icon: ChartBar,        roles: ["senior_officer"] },
  { key: "reports",   label: "Reports",              Icon: FilmSlate,       roles: ["control_room_officer","senior_officer"] },
];

const MODEL_ICONS = { person: UserIcon, face: ShieldCheck, vehicle: Car, crowd: UsersIcon, plate: Hash };

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
  const [blobUrl, setBlobUrl]   = useState(null);
  const [hasFrame, setHasFrame] = useState(false);
  const [feedDead, setFeedDead] = useState(false);
  const [lastSeen, setLastSeen] = useState(null);

  const timerRef    = useRef(null);
  const watchRef    = useRef(null);
  const errRef      = useRef(0);
  const prevBlobRef = useRef(null);
  const mountedRef  = useRef(true);

  const isOffline  = drone.status === "offline";
  const isDegraded = drone.status === "degraded";

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      clearTimeout(timerRef.current);
      clearTimeout(watchRef.current);
      if (prevBlobRef.current) URL.revokeObjectURL(prevBlobRef.current);
    };
  }, []);

  useEffect(() => {
    clearTimeout(timerRef.current);
    clearTimeout(watchRef.current);
    if (prevBlobRef.current) { URL.revokeObjectURL(prevBlobRef.current); prevBlobRef.current = null; }
    errRef.current = 0;
    setBlobUrl(null);
    setHasFrame(false);
    setFeedDead(isOffline);

    if (isOffline) return;

    const schedule = (delay) => {
      if (!mountedRef.current) return;
      timerRef.current = setTimeout(doFetch, delay);
    };

    const doFetch = async () => {
      if (!mountedRef.current) return;
      const token = localStorage.getItem("dvas_token") || "";
      const url   = `${API}/ingest-server/frame/${encodeURIComponent(drone.id)}/latest?_t=${Date.now()}`;
      try {
        const res = await fetch(url, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          credentials: "include",
        });
        if (!mountedRef.current) return;
        if (res.ok) {
          const blob = await res.blob();
          const next = URL.createObjectURL(blob);
          if (prevBlobRef.current) URL.revokeObjectURL(prevBlobRef.current);
          prevBlobRef.current = next;
          setBlobUrl(next);
          setHasFrame(true);
          setFeedDead(false);
          setLastSeen(new Date());
          errRef.current = 0;
          clearTimeout(watchRef.current);
          watchRef.current = setTimeout(() => { if (mountedRef.current) setFeedDead(true); }, MINI_WATCHDOG_MS);
          schedule(800);
        } else { throw new Error(`HTTP ${res.status}`); }
      } catch {
        if (!mountedRef.current) return;
        errRef.current++;
        if (errRef.current > 5) setFeedDead(true);
        schedule(MINI_BACKOFF[Math.min(errRef.current - 1, MINI_BACKOFF.length - 1)]);
      }
    };

    doFetch();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drone.id, isOffline]);

  const statusColor = isDegraded ? "#f59e0b" : isOffline || feedDead ? "#ef4444" : "#10b981";
  const borderColor = isDegraded
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

        {/* Top-right: Status + streaming dot */}
        <div className="absolute top-1.5 right-1.5 z-20 flex items-center gap-1">
          {hasFrame && !feedDead && (
            <span
              className="w-1.5 h-1.5 rounded-full"
              style={{ background: "#10b981", animation: "pulse 1.2s infinite" }}
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
            {isOffline || feedDead ? "OFFLINE" : isDegraded ? "DEGRADED" : "LIVE"}
          </span>
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
  const activeDrones = merged.filter((d) => d.status === "live" || d.status === "draining" || (d.messages_ready ?? 0) > 0);
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
  const [evidence, setEvidence] = useState([]);
  const [missions, setMissions] = useState([]);
  const [exports, setExports] = useState([]);
  const [crowd, setCrowd] = useState([]);
  const [playing, setPlaying] = useState(null);
  const [requestOn, setRequestOn] = useState(null);
  const [justification, setJustification] = useState("");
  const [selectedDrone, setSelectedDrone] = useState(null);

  const { mergeWithDrones } = useActiveStreams();
  const mergedDrones = mergeWithDrones(drones);
  const liveMissionsCount = mergedDrones.filter((d) => d.status === "live" && Boolean(d.mission_id)).length;

  const load = async () => {
    try {
      const [k, dR, aR, eR, mR, xR, cR] = await Promise.all([
        api.get("/command/kpis"),
        api.get("/drones"),
        api.get("/alerts"),
        api.get("/evidence"),
        api.get("/missions"),
        // CRO now has access to their own export requests
        api.get("/exports").catch(() => ({ data: [] })),
        api.get("/analytics/crowd"),
      ]);
      setKpis(k.data); setDrones(dR.data); setAlerts(aR.data); setEvidence(eR.data);
      setMissions(mR.data); setExports(xR.data); setCrowd(cR.data.series || []);
    } catch (e) { console.error(e); }
  };

  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, []);

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
        ((user.mission_scope || []).includes(a.mission_id));
      if (inScope) setAlerts((prev) => [a, ...prev].slice(0, 200));
    }
  });

  const ackAlert = async (id) => {
    try { await api.post(`/alerts/${id}/ack`); load(); } catch (e) {}
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


      {tab === "alerts" && (
        <div className="rounded-xl overflow-hidden" style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}>
          <div className="p-3 flex items-center gap-2 text-sm font-semibold" style={{ borderBottom: "1px solid var(--dvas-border)", color: "var(--dvas-text)" }}><Bell size={16} className="text-amber-500" weight="duotone" /> {t("cmd.page.alerts")}</div>
          <ul className="divide-y max-h-[560px] overflow-auto" style={{ borderColor: "var(--dvas-border)" }}>
            {alerts.length === 0 && <li className="p-6 text-center text-slate-500 text-xs">{t("cmd.noAlertsInScope")}</li>}
            {alerts.map(a => (
              <li key={a.id} data-testid={`alert-${a.id}`} className="p-3 hover:bg-[color:var(--dvas-elev)] transition-colors">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="mono text-[10px] px-1.5 py-0.5 border border-red-500/50 bg-red-500/10 text-red-400 uppercase">{a.type.replace("_", " ")}</span>
                  <span className="mono text-[10px] text-slate-400">conf {a.confidence}</span>
                  <span className="mono text-[10px] text-slate-500">· {a.zone}</span>
                  <span className="mono text-[10px] text-slate-500">· {a.drone_id}</span>
                  <span className="mono text-[10px] text-slate-500 ml-auto">{new Date(a.ts).toLocaleString()}</span>
                </div>
                <div className="mt-1.5 flex items-center gap-2">
                  {a.acknowledged_by ? (
                    <span className="text-[11px] text-emerald-400 flex items-center gap-1"><CheckCircle size={12} /> acked by {a.acknowledged_by}</span>
                  ) : (
                    // CRO (former CRO + field_supervisor) and SR (former supervisory_officer) can ACK
                    ["control_room_officer","senior_officer"].includes(user.role) && (
                      <button data-testid={`ack-${a.id}`} onClick={() => ackAlert(a.id)} className="text-[11px] px-2 py-1 border border-slate-700 hover:border-sky-500">Acknowledge</button>
                    )
                  )}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {tab === "evidence" && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {evidence.map(ev => (
            <div key={ev.id} data-testid={`evidence-${ev.id}`} className="rounded-xl overflow-hidden" style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}>
              <div className="aspect-video relative overflow-hidden" style={{ background: "var(--dvas-surface-2)" }}>
                <img src={ev.thumbnail} alt="" className="w-full h-full object-cover opacity-90" />
                <button data-testid={`play-${ev.id}`} onClick={() => setPlaying(ev)} className="absolute inset-0 flex items-center justify-center bg-black/10 hover:bg-black/5 transition-colors text-white">
                  <div className="border border-white/70 px-3 py-1 text-xs bg-black/40 rounded">▶ Play</div>
                </button>
                {ev.legal_hold && (
                  <span className="absolute top-2 left-2 mono text-[10px] px-1.5 py-0.5 border border-red-500/50 bg-white/90 text-red-500 uppercase flex items-center gap-1 shadow-sm rounded-sm"><FolderLock size={10} /> hold</span>
                )}
              </div>
              <div className="p-3">
                <div className="text-xs font-medium">{ev.label}</div>
                <div className="mono text-[10px] text-slate-500 mt-1 flex flex-wrap gap-2">
                  <span>{ev.event_type}</span><span>·</span><span>conf {ev.confidence}</span><span>·</span><span>{ev.duration_s}s</span>
                </div>
                <div className="mono text-[10px] text-slate-500">{new Date(ev.ts).toLocaleString()}</div>
                {/* CRO (former field_supervisor) can request exports for active-mission evidence */}
                {user.role === "control_room_officer" && (
                  <button data-testid={`request-${ev.id}`} onClick={() => setRequestOn(ev)} className="mt-2 w-full text-xs border border-slate-700 hover:border-sky-500 py-1.5">Request Export</button>
                )}
              </div>
            </div>
          ))}
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

      {tab === "models" && <ModelsPage user={user} t={t} />}

      {tab === "exports" && (
        <div className="rounded-xl overflow-hidden" style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}>
          <div className="p-3 text-sm flex items-center gap-2 font-semibold" style={{ borderBottom: "1px solid var(--dvas-border)", color: "var(--dvas-text)" }}>
            <ArrowSquareOut size={16} className="text-sky-500" weight="duotone" /> Evidence Export Requests
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
                    {/* SR (former supervisory_officer) approves/rejects pending exports */}
                    {user.role === "senior_officer" && x.status === "pending" && (
                      <div className="flex gap-1">
                        <button data-testid={`approve-${x.id}`} onClick={() => decide(x.id, "approved")} className="px-2 py-1 border border-emerald-600/60 text-emerald-300 hover:bg-emerald-500/10">Approve</button>
                        <button data-testid={`reject-${x.id}`} onClick={() => decide(x.id, "rejected")} className="px-2 py-1 border border-red-600/60 text-red-300 hover:bg-red-500/10">Reject</button>
                      </div>
                    )}
                    {/* CRO who submitted (own request) or SR (full jurisdiction access) can download bundle */}
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
        <div className="border border-slate-800 bg-slate-900 p-4">
          <div className="dvas-label mb-3">On-demand Reports</div>
          <ul className="space-y-2 text-xs">
            {["Daily incident summary","Weekly response-time report","Drone utilisation (30d)","Mission after-action report"].map(r=>(
              <li key={r} data-testid={`report-${r}`} className="flex items-center justify-between border border-slate-800 p-3">
                <div><div className="text-slate-100">{r}</div><div className="mono text-[10px] text-slate-500">Watermarked · {user.email} · {new Date().toLocaleDateString()}</div></div>
                <button onClick={() => { const w=window.open("","_blank"); if(w){w.document.write(`<pre style='font:12px monospace;padding:24px'>${r}\nGenerated for: ${user.email}\nGenerated at: ${new Date().toISOString()}\nJurisdiction: ${user.jurisdiction}\n\n(Placeholder report body — actual data pipeline pending)</pre>`);}}} className="px-3 py-1.5 border border-sky-600/60 text-sky-300 hover:bg-sky-500/10 flex items-center gap-1"><Download size={11}/> Generate</button>
              </li>
            ))}
          </ul>
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
            <video data-testid="video-player" src={playing.video_url} controls autoPlay className="w-full aspect-video bg-black" />
            <div className="p-3 mono text-[11px] text-slate-400 flex flex-wrap gap-3">
              <span>{playing.event_type}</span><span>conf {playing.confidence}</span>
              <span>duration {playing.duration_s}s</span><span>mission {playing.mission_id}</span>
              <span className="ml-auto">retention until {new Date(playing.retention_until).toLocaleDateString()}</span>
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
            {runningCount}<span className="text-slate-500 text-sm"> / 4</span>
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
