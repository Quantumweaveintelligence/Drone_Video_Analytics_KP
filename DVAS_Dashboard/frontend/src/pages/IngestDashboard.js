import { useCallback, useEffect, useMemo, useState, useRef } from "react";
import AppShell from "@/components/layout/AppShell";
import { useNavigate } from "react-router-dom";
import { api, apiError, ROLE_LABELS } from "@/lib/api";
import { fmtIST } from "@/lib/ist";
import { useI18n } from "@/context/I18nContext";
import { useIngestServer } from "@/hooks/useIngestServer";
import { useDvasSocket } from "@/lib/useDvasSocket";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, BarChart, Bar, CartesianGrid,
} from "recharts";
import {
  CircuitBoard, AlertTriangle, Table as TableIcon, Activity, Cpu, ClipboardList,
  Users, Trash2, Plus, ShieldAlert, LayoutDashboard, Gauge, Database, HardDrive,
  Zap, CheckCircle2, XCircle, TrendingUp, Server, Wifi, WifiOff, Radio,
  Power, PowerOff, RotateCcw, AlertOctagon, Terminal, ChevronDown, Bell,
} from "lucide-react";

const sevColor = {
  crit: "text-red-400 border-red-500/40 bg-red-500/10",
  warn: "text-amber-400 border-amber-500/40 bg-amber-500/10",
  info: "text-sky-400 border-sky-500/40 bg-sky-500/10",
};

const PAGES = [
  { key: "server", labelKey: "ingest.page.server", Icon: Server },
  { key: "overview", labelKey: "ingest.page.overview", Icon: LayoutDashboard },
  { key: "feeds",  labelKey: "ingest.page.feeds",  Icon: TableIcon },
  { key: "queue",  labelKey: "ingest.page.queue",  Icon: Activity },
  { key: "gpu",    labelKey: "ingest.page.gpu",    Icon: Cpu },
  { key: "alerts", labelKey: "ingest.page.alerts", Icon: AlertTriangle },
  { key: "audit",  labelKey: "ingest.page.audit",  Icon: ClipboardList },
  { key: "users",  labelKey: "ingest.page.users",  Icon: Users },
];

function TopNav({ page, setPage, pendingSuggestionsCount = 0 }) {
  const { t } = useI18n();
  return (
    <nav
      data-testid="ingest-tabbar"
      className="flex items-center gap-1 overflow-x-auto no-scrollbar"
      style={{ borderBottom: "1px solid var(--dvas-border)" }}
    >
      <span className="dvas-label pr-2 shrink-0" style={{ fontSize: 9 }}>{t("ingest.pages")}</span>
      {PAGES.map(({ key, labelKey, Icon }, i) => (
        <button
          key={key}
          data-testid={`ingest-nav-${key}`}
          onClick={() => setPage(key)}
          className={`shrink-0 flex items-center gap-2 px-3 py-2 text-xs whitespace-nowrap transition-colors ${
            page === key ? "text-white" : "text-slate-400 hover:text-white"
          }`}
          style={{
            borderBottom: page === key ? "2px solid var(--dvas-cyan)" : "2px solid transparent",
            marginBottom: "-1px",
          }}
        >
          <span className="mono text-[10px] text-slate-600">{String(i + 1).padStart(2, "0")}</span>
          <Icon size={14} /> {t(labelKey)}
          {key === "gpu" && pendingSuggestionsCount > 0 && (
            <span className="px-1.5 py-0.2 rounded-full text-[9px] bg-sky-500 text-white font-mono font-bold animate-pulse">
              {pendingSuggestionsCount}
            </span>
          )}
        </button>
      ))}
      <span className="ml-auto mono text-[10px] text-slate-500 pl-3 shrink-0 hidden md:inline">
        {t("ingest.infraOnly")}
      </span>
    </nav>
  );
}

export default function IngestDashboard() {
  const { t } = useI18n();
  const [page, setPage] = useState("server");
  const [h, setH] = useState(null);
  const [ov, setOv] = useState(null);
  const [tick, setTick] = useState(0);
  const [audit, setAudit] = useState([]);
  const ingestSrv = useIngestServer();
  const [feedFilter, setFeedFilter] = useState("all");
  const [feedSort, setFeedSort] = useState("drone_id");
  const [liveIncidents, setLiveIncidents] = useState([]);
  const [activeBanner, setActiveBanner] = useState(null);
  const [modelSuggestions, setModelSuggestions] = useState([]);
  const [modelToast, setModelToast] = useState(null);

  const playAlertChime = useCallback((severity) => {
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;
      const ctx = new AudioCtx();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = severity === "CRITICAL" ? "sawtooth" : "sine";
      osc.frequency.setValueAtTime(severity === "CRITICAL" ? 880 : 587.33, ctx.currentTime);
      osc.frequency.exponentialRampToValueAtTime(severity === "CRITICAL" ? 440 : 880, ctx.currentTime + 0.4);
      gain.gain.setValueAtTime(0.18, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.4);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.4);
    } catch (e) {
      // Audio playback might require prior user interaction
    }
  }, []);

  useDvasSocket((msg) => {
    if (msg.type === "incident" && msg.incident) {
      const inc = msg.incident;
      setLiveIncidents((prev) => [inc, ...prev.filter(x => x.id !== inc.id)]);
      setActiveBanner(inc);
      if (inc.severity === "CRITICAL" || inc.severity === "HIGH") {
        playAlertChime(inc.severity);
      }
    } else if (msg.type === "incident_ack") {
      setLiveIncidents((prev) => prev.map(x => x.id === msg.incident_id ? { ...x, status: "ACKNOWLEDGED" } : x));
      setActiveBanner((curr) => (curr?.id === msg.incident_id ? null : curr));
    } else if (msg.type === "model_suggestion" && msg.suggestion) {
      const sugg = msg.suggestion;
      setModelSuggestions((prev) => [sugg, ...prev.filter(x => x.id !== sugg.id)]);
      setModelToast(sugg);
      setTimeout(() => setModelToast(null), 8000);
    } else if (msg.type === "model_suggestion_status") {
      setModelSuggestions((prev) => prev.map(x => x.id === msg.suggestion_id ? { ...x, status: msg.status } : x));
    } else if (msg.type === "incident_deleted") {
      // F2: deletes and clears made elsewhere disappear here too
      setLiveIncidents((prev) => prev.filter(x => x.id !== msg.incident_id));
      setActiveBanner((curr) => (curr?.id === msg.incident_id ? null : curr));
    } else if (msg.type === "incidents_cleared") {
      const ids = msg.incident_ids;
      setLiveIncidents((prev) => (ids ? prev.filter(x => !ids.includes(x.id)) : []));
      setActiveBanner((curr) => (curr && (!ids || ids.includes(curr.id)) ? null : curr));
    } else if (msg.type === "model_suggestion_deleted") {
      setModelSuggestions((prev) => prev.filter(x => x.id !== msg.suggestion_id));
    } else if (msg.type === "model_suggestions_cleared") {
      const ids = msg.suggestion_ids;
      setModelSuggestions((prev) => (ids ? prev.filter(x => !ids.includes(x.id)) : []));
    }
  });

  const loadIncidents = async () => {
    try {
      const { data } = await api.get("/incidents");
      if (Array.isArray(data)) setLiveIncidents(data);
    } catch (e) {}
  };

  const loadModelSuggestions = async () => {
    try {
      const { data } = await api.get("/analytics/suggestions");
      if (Array.isArray(data)) setModelSuggestions(data);
    } catch (e) {}
  };

  const load = async () => {
    try {
      const { data } = await api.get("/infra/health"); setH(data);
    } catch (e) {}
  };
  const loadOverview = async () => {
    try { const { data } = await api.get("/infra/overview"); setOv(data); } catch (e) {}
  };
  const loadAudit = async () => {
    try {
      const { data } = await api.get("/audit-logs", { params: { page_size: 100 } });
      setAudit(Array.isArray(data) ? data : (data?.items || []));
    } catch (e) {
      setAudit([]);
    }
  };


  useEffect(() => {
    load(); loadOverview(); loadIncidents(); loadModelSuggestions();
    // 10 s: each poll samples host metrics and probes services server-side; paused on hidden tabs
    const t = setInterval(() => {
      if (document.hidden) return;
      load(); if (page === "overview") loadOverview(); setTick(x => x + 1);
    }, 10000);
    return () => clearInterval(t);
  }, [page]);
  useEffect(() => {
    if (page === "audit") loadAudit();
    if (page === "alerts") loadIncidents();
    if (page === "gpu") loadModelSuggestions();
  }, [page]);

  useEffect(() => {
    const onTab = (e) => {
      if (e.detail) setPage(e.detail);
    };
    window.addEventListener("dvas:ingest-tab", onTab);
    return () => window.removeEventListener("dvas:ingest-tab", onTab);
  }, []);

  // Real throughput from ingest server status — rolling buffer of snapshots
  const throughputBufferRef = useRef([]);
  const throughput = useMemo(() => {
    const drones = ingestSrv?.status?.drones || [];
    const totalIngested = drones.reduce((s, d) => s + (d?.ingest_stats?.frames_received || 0), 0);
    const totalQueued  = drones.reduce((s, d) => s + (d?.queue?.messages_ready || 0), 0);
    const buf = throughputBufferRef.current;
    buf.push({ t: buf.length, ingested: totalIngested, queued: totalQueued });
    if (buf.length > 24) buf.shift();
    return buf.map((pt, i) => ({ t: i, ingested: pt.ingested, queued: pt.queued }));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, ingestSrv?.status]);

  const feeds = useMemo(() => {
    let arr = [...(h?.feeds || [])];
    if (feedFilter !== "all") arr = arr.filter(f => f.status === feedFilter);
    arr.sort((a, b) => (a[feedSort] > b[feedSort] ? 1 : -1));
    return arr;
  }, [h, feedFilter, feedSort]);

  const handleAcknowledgeIncident = async (incidentId) => {
    try {
      await api.post(`/incidents/${incidentId}/ack`);
      setLiveIncidents((prev) => prev.map(x => x.id === incidentId ? { ...x, status: "ACKNOWLEDGED" } : x));
      setActiveBanner((curr) => (curr?.id === incidentId ? null : curr));
    } catch (err) {
      console.error("Failed to acknowledge incident", err);
    }
  };

  // ING-02: "Deploy to GPU" applies the model config on the backend; show what it did
  const [suggestionMsg, setSuggestionMsg] = useState("");
  const handleUpdateSuggestionStatus = async (suggestionId, newStatus) => {
    setSuggestionMsg("");
    try {
      const { data } = await api.post(`/analytics/suggestions/${encodeURIComponent(suggestionId)}/status`, { status: newStatus });
      setModelSuggestions((prev) => prev.map(s => s.id === suggestionId ? { ...s, status: data?.new_status || newStatus } : s));
      if (newStatus === "DEPLOYED_TO_GPU") {
        const acts = data?.stream_actions || {};
        const n = Array.isArray(acts) ? acts.length : Object.keys(acts).length;
        setSuggestionMsg(`Model configuration applied (${n} stream action${n === 1 ? "" : "s"}); see Detection Models for live status.`);
      }
      loadModelSuggestions();
    } catch (err) {
      setSuggestionMsg(apiError(err, "Failed to update suggestion status"));
    }
  };

  const pendingSuggestionsCount = modelSuggestions.filter(s => s.status === "PENDING").length;

  return (
    <AppShell title={t("ingest.title")} subtitle={t("ingest.subtitle")}>
      <div className="mb-4 border border-amber-500/40 bg-amber-500/5 p-3 flex items-start gap-2">
        <ShieldAlert size={16} className="text-amber-400 shrink-0 mt-0.5" />
        <div className="text-xs text-slate-300 leading-relaxed">
          You are the <span className="text-amber-300">System Administrator / NOC</span>. This dashboard is walled off from
          <span className="mono text-slate-100"> video content</span>, <span className="mono text-slate-100"> analytics</span>, and
          <span className="mono text-slate-100"> evidence playback</span>. Your infrastructure actions are logged and reviewed by Supervisory / Senior Officers.
        </div>
      </div>

      {/* Model Suggestion Toast from Edge Operator */}
      {modelToast && (
        <div className="mb-4 p-3 border border-sky-500 bg-sky-950/85 rounded-lg flex flex-col md:flex-row md:items-center justify-between gap-3 text-xs text-sky-200 font-mono shadow-lg">
          <div className="flex items-center gap-2.5">
            <Cpu size={16} className="text-sky-400 animate-pulse shrink-0" />
            <span>
              <strong>New AI Model Request from Edge:</strong> {modelToast.suggested_by} requested <strong>{(modelToast.model_names || []).join(", ")}</strong> at {modelToast.location}.
            </span>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => { setPage("gpu"); setModelToast(null); }}
              className="px-2.5 py-1 bg-sky-600 hover:bg-sky-500 text-white text-[11px] rounded transition-colors"
            >
              View on GPU Dashboard
            </button>
            <button onClick={() => setModelToast(null)} className="text-slate-400 hover:text-white px-1">✕</button>
          </div>
        </div>
      )}

      {/* High-Visibility Real-Time Incident Notification Banner */}
      {activeBanner && (
        <div
          data-testid="ingest-incident-banner"
          className={`mb-4 p-4 border rounded-lg transition-all animate-pulse shadow-lg ${
            activeBanner.severity === "CRITICAL"
              ? "border-red-500 bg-red-950/85 shadow-red-500/20 text-red-100"
              : activeBanner.severity === "HIGH"
              ? "border-amber-500 bg-amber-950/85 shadow-amber-500/20 text-amber-100"
              : "border-sky-500 bg-sky-950/85 shadow-sky-500/20 text-sky-100"
          }`}
        >
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
            <div className="flex items-start gap-3">
              <div className="relative mt-0.5 shrink-0">
                <span className="flex h-3 w-3">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-3 w-3 bg-red-500"></span>
                </span>
              </div>
              <div className="space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`px-2 py-0.5 text-[10px] font-mono font-bold tracking-wider rounded border uppercase ${
                    activeBanner.severity === "CRITICAL" ? "bg-red-500/20 border-red-500 text-red-300" :
                    activeBanner.severity === "HIGH" ? "bg-amber-500/20 border-amber-500 text-amber-300" :
                    "bg-sky-500/20 border-sky-500 text-sky-300"
                  }`}>
                    {activeBanner.severity} INCIDENT
                  </span>
                  <span className="text-sm font-bold text-white tracking-wide">
                    {activeBanner.title}
                  </span>
                  {activeBanner.category && (
                    <span className="text-[10px] font-mono uppercase px-1.5 py-0.5 bg-slate-800/80 text-slate-300 border border-slate-700 rounded">
                      {activeBanner.category.replace('_', ' ')}
                    </span>
                  )}
                </div>
                <div className="text-xs text-slate-300 leading-relaxed">
                  {activeBanner.description || "Edge station reported an operational incident."}
                </div>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] font-mono text-slate-400">
                  <span>📍 <strong className="text-slate-200">Location:</strong> {activeBanner.location || "Edge Station"}</span>
                  <span>👤 <strong className="text-slate-200">Reported By:</strong> {activeBanner.created_by || "Edge Operator"}</span>
                  <span>🕒 <strong className="text-slate-200">Time:</strong> {activeBanner.timestamp ? fmtIST(activeBanner.timestamp) : "Just now"}</span>
                </div>
              </div>
            </div>

            <div className="flex items-center gap-2 shrink-0 self-end md:self-center">
              <button
                data-testid="banner-ack-btn"
                onClick={() => handleAcknowledgeIncident(activeBanner.id)}
                className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold rounded flex items-center gap-1.5 transition-colors shadow"
              >
                <CheckCircle2 size={13} /> Acknowledge
              </button>
              <button
                data-testid="banner-view-alerts-btn"
                onClick={() => { setPage("alerts"); }}
                className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-medium border border-slate-600 rounded transition-colors"
              >
                View in Alerts
              </button>
              <button
                data-testid="banner-dismiss-btn"
                onClick={() => setActiveBanner(null)}
                title="Dismiss Banner"
                className="p-1.5 text-slate-400 hover:text-white rounded hover:bg-slate-800 transition-colors"
              >
                <XCircle size={16} />
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="flex flex-col gap-4">
        <TopNav page={page} setPage={setPage} pendingSuggestionsCount={pendingSuggestionsCount} />

        <div className="min-w-0">
          {page === "server" && <ServerPage srv={ingestSrv} />}
          {page === "overview" && <OverviewPage ov={ov} />}
          {page === "feeds" && <FeedsPage feeds={feeds} filter={feedFilter} setFilter={setFeedFilter} setSort={setFeedSort} />}
          {page === "queue" && <QueuePage throughput={throughput} queue={h?.queue || []} ingestSrv={ingestSrv} />}
          {page === "gpu" && (
            <GpuPage
              gpu={h?.gpu || []}
              suggestions={modelSuggestions}
              onUpdateStatus={handleUpdateSuggestionStatus}
              message={suggestionMsg}
            />
          )}
          {page === "alerts" && (
            <AlertsPage
              alerts={(h?.alerts || []).filter(a => !a.acknowledged_by)}
              onAck={async (id) => {
                try { await api.post(`/infra/alerts/${encodeURIComponent(id)}/ack`); load(); }
                catch (e) { window.alert(apiError(e, "Could not acknowledge the alert")); }
              }}
              incidents={liveIncidents}
              onAckIncident={handleAcknowledgeIncident}
            />
          )}
          {page === "audit" && <AuditPage audit={audit} />}
          {page === "users" && <UsersPage />}
        </div>
      </div>
    </AppShell>
  );
}

/* ---------- Sub-pages ---------- */

/* ---------- SERVER (new — real ingest server integration) ---------- */

function formatUptime(seconds) {
  if (!seconds || seconds < 0) return "—";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  return `${m}m ${s}s`;
}

function formatBytes(bytes) {
  if (!bytes || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

function StatusDot({ ok, pulse }) {
  return (
    <span
      className={`inline-block w-2 h-2 rounded-full ${pulse && ok ? "dvas-live-dot" : ""}`}
      style={{ background: ok ? "var(--dvas-safe)" : "var(--dvas-crit)" }}
    />
  );
}

/* ── Confirmation Modal ──────────────────────────────────────────────────── */
function ConfirmModal({ open, title, message, confirmWord, onConfirm, onCancel, danger }) {
  const [typed, setTyped] = useState("");
  useEffect(() => { if (open) setTyped(""); }, [open]);
  if (!open) return null;
  const ready = !confirmWord || typed.toUpperCase() === confirmWord.toUpperCase();
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: "rgba(0,0,0,0.75)" }}
      onClick={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div className="w-full max-w-md border border-slate-700 bg-slate-900 p-6 shadow-2xl">
        <div className="flex items-center gap-3 mb-4">
          <AlertOctagon size={20} className={danger ? "text-red-400" : "text-amber-400"} />
          <h3 className="text-base font-semibold" style={{ color: "var(--dvas-text)" }}>{title}</h3>
        </div>
        <p className="text-sm text-slate-400 mb-4 leading-relaxed">{message}</p>
        {confirmWord && (
          <div className="mb-4">
            <div className="dvas-label mb-1.5" style={{ fontSize: 10 }}>
              Type <span className="mono font-bold text-slate-200">{confirmWord}</span> to confirm
            </div>
            <input
              autoFocus
              value={typed}
              onChange={e => setTyped(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter" && ready) onConfirm(); }}
              className="w-full mono text-sm px-3 py-2"
              style={{
                background: "var(--dvas-elev)",
                border: `1px solid ${ready && typed ? "var(--dvas-safe)" : "var(--dvas-border)"}`,
                color: "var(--dvas-text)",
                outline: "none",
              }}
              placeholder={`Type ${confirmWord}…`}
            />
          </div>
        )}
        <div className="flex gap-3 justify-end">
          <button onClick={onCancel}
            className="px-4 py-2 text-sm rounded"
            style={{ background: "var(--dvas-elev)", color: "var(--dvas-text-dim)", border: "1px solid var(--dvas-border)" }}>
            Cancel
          </button>
          <button onClick={onConfirm} disabled={!ready}
            className="px-4 py-2 text-sm rounded font-semibold transition-opacity"
            style={{
              background: danger ? "#dc2626" : "var(--dvas-safe)",
              color: "#fff",
              opacity: ready ? 1 : 0.4,
              cursor: ready ? "pointer" : "not-allowed",
            }}>
            Confirm
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── Container Status Grid ───────────────────────────────────────────────── */
function ContainerGrid({ containers, loading }) {
  const statusColor = (s, h) => {
    if (s === "running") {
      if (h === "healthy") return { bg: "var(--dvas-safe-bg)", fg: "var(--dvas-safe)", text: "healthy" };
      if (h === "unhealthy") return { bg: "var(--dvas-crit-bg)", fg: "var(--dvas-crit)", text: "unhealthy" };
      return { bg: "var(--dvas-safe-bg)", fg: "var(--dvas-safe)", text: "running" };
    }
    if (s === "not_found") return { bg: "rgba(100,116,139,0.1)", fg: "#64748b", text: "missing" };
    return { bg: "var(--dvas-crit-bg)", fg: "var(--dvas-crit)", text: s || "stopped" };
  };

  const coreNames = ["ingest_api", "nginx"];

  return (
    <div className="border border-slate-800 bg-slate-900">
      <div className="p-3 border-b border-slate-800 text-sm flex items-center gap-2">
        <Terminal size={16} className="text-sky-400" />
        Container Status ({containers.length} Services)
        <span className="ml-auto mono text-[10px] text-slate-500">
          {loading ? "refreshing…" : `${containers.filter(c => c.running).length} / ${containers.length} running`}
        </span>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-px bg-slate-800">
        {containers.map(c => {
          const col = statusColor(c.status, c.health);
          const isCore = coreNames.includes(c.short_name);
          return (
            <div key={c.name}
              className="bg-slate-900 p-3 flex flex-col gap-1.5"
              data-testid={`container-${c.short_name}`}>
              <div className="flex items-center justify-between">
                <span className="w-2 h-2 rounded-full flex-shrink-0"
                  style={{ background: col.fg, boxShadow: c.running ? `0 0 6px ${col.fg}` : "none" }} />
                {isCore && (
                  <span className="text-[8px] px-1 py-0.5 rounded"
                    style={{ background: "var(--dvas-blue-bg)", color: "var(--dvas-blue)" }}>CORE</span>
                )}
                {c.category && !isCore && (
                  <span className="text-[8px] px-1 py-0.5 rounded mono text-slate-400 bg-slate-800">
                    {c.category}
                  </span>
                )}
              </div>
              <div className="mono text-[11px] font-semibold text-slate-100 truncate" title={c.name}>
                {c.short_name || c.name}
              </div>
              <div className="text-[9px] px-1.5 py-0.5 rounded font-semibold uppercase tracking-wide flex items-center justify-between"
                style={{ background: col.bg, color: col.fg }}>
                <span>{col.text}</span>
                {c.health && c.health !== "no_healthcheck" && c.health !== "n/a" && (
                  <span className="text-[8px] lowercase opacity-80">({c.health})</span>
                )}
              </div>
            </div>
          );
        })}          
      </div>
    </div>
  );
}

function ServerPage({ srv }) {
  const { t } = useI18n();
  const { health, status, drones, readiness, wsEvents, wsConnected, fps, testConnection } = srv;
  const [testing, setTesting] = useState(false);
  const feedRef = useRef(null);

  // ── Docker control state ──
  const [containers, setContainers] = useState([]);
  const [containersLoading, setContainersLoading] = useState(false);
  const [controlScope, setControlScope] = useState("core");
  const [modal, setModal] = useState(null); // { action, title, message, confirmWord, danger }
  const [actionBusy, setActionBusy] = useState(false);
  const [actionResult, setActionResult] = useState(null);

  const isOnline = health?.reachable === true;

  const loadContainers = useCallback(async () => {
    setContainersLoading(true);
    try {
      const { data } = await api.get("/ingest-server/containers");
      setContainers(data);
    } catch (e) {
      // socket may not be mounted yet
    } finally {
      setContainersLoading(false);
    }
  }, []);

  useEffect(() => {
    loadContainers();
    const iv = setInterval(loadContainers, 15000);
    return () => clearInterval(iv);
  }, [loadContainers]);

  const openModal = (action) => {
    const configs = {
      stop: {
        title: `Stop Ingest Server (${controlScope === "core" ? "Frame Reception" : "Full Stack"})`,
        message: controlScope === "core"
          ? "This will stop drone_ingest_api and drone_nginx. No new frames will be accepted. Database, monitoring and analytics workers remain running. You can restart at any time."
          : `This will stop ingest containers (${containers.length || 24} stack services) including the database. Evidence data is preserved. Only use for full maintenance windows.`,
        confirmWord: "SHUTDOWN",
        danger: true,
      },
      start: {
        title: `Start Ingest Server (${controlScope === "core" ? "Frame Reception" : "Full Stack"})`,
        message: controlScope === "core"
          ? "This will start drone_ingest_api and drone_nginx to resume accepting drone frames."
          : "This will start all ingest stack containers. Keycloak may take ~90 seconds to become healthy.",
        confirmWord: "",
        danger: false,
      },
      restart: {
        title: "Restart Ingest Server",
        message: controlScope === "core"
          ? "This will restart drone_ingest_api and drone_nginx. There will be ~10 seconds of downtime for frame reception."
          : `This will restart all ingest stack containers (${containers.length || 24} services). Full startup may take 2–3 minutes.`,
        confirmWord: "RESTART",
        danger: false,
      },
    };
    setModal({ action, ...configs[action] });
    setActionResult(null);
  };

  const handleControl = async () => {
    if (!modal) return;
    setModal(null);
    setActionBusy(true);
    setActionResult(null);
    try {
      const { data } = await api.post("/ingest-server/control", {
        action: modal.action,
        scope: controlScope,
      });
      setActionResult({ ok: data.success, message: data.success
        ? `${modal.action.charAt(0).toUpperCase() + modal.action.slice(1)} completed. ${data.results.length} container(s) affected.`
        : `Partial failure: ${data.errors.map(e => e.container).join(", ")}` });
      // Refresh container grid after action
      setTimeout(loadContainers, 2000);
      setTimeout(loadContainers, 6000);
    } catch (e) {
      setActionResult({ ok: false, message: e?.response?.data?.detail || "Action failed" });
    } finally {
      setActionBusy(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    await testConnection();
    setTesting(false);
  };

  // FPS history for sparkline (rolling 30 data points)
  const [fpsHistory, setFpsHistory] = useState(() => Array(30).fill(0));
  useEffect(() => {
    const iv = setInterval(() => {
      setFpsHistory(prev => [...prev.slice(1), fps]);
    }, 1000);
    return () => clearInterval(iv);
  }, [fps]);

  const totalFrames = drones.reduce((a, d) => a + (d.frames_total || d.frames_queued || 0), 0);
  const totalQueued = drones.reduce((a, d) => a + (d.queue_depth || 0), 0);
  const totalBytes = drones.reduce((a, d) => a + (d.bytes_ingested || 0), 0);
  const activeDrones = drones.filter(d => d.queue_depth > 0 || (Date.now() / 1000 - (d.last_frame_ts || 0)) < 30).length;

  return (
    <div className="flex flex-col gap-5">
      {/* ── Confirmation Modal ──────────────────────────────────────────── */}
      <ConfirmModal
        open={!!modal}
        title={modal?.title}
        message={modal?.message}
        confirmWord={modal?.confirmWord}
        danger={modal?.danger}
        onConfirm={handleControl}
        onCancel={() => setModal(null)}
      />

      {/* ── Server Control Panel ─────────────────────────────────────────── */}
      <div className="border border-slate-700 bg-slate-900"
        style={{ borderLeft: "3px solid var(--dvas-blue)" }}>
        <div className="p-4">
          <div className="flex flex-wrap items-center gap-3 mb-4">
            <div className="flex items-center gap-2">
              <Power size={16} className="text-sky-400" />
              <span className="text-sm font-semibold" style={{ color: "var(--dvas-text)" }}>
                Server Control
              </span>
              <span className="mono text-[10px] px-2 py-0.5 rounded"
                style={{ background: "var(--dvas-elev)", color: "var(--dvas-text-dim)" }}>
                System Administrator Only
              </span>
            </div>

            {/* Scope selector */}
            <div className="ml-auto flex items-center gap-2">
              <span className="dvas-label" style={{ fontSize: 9 }}>SCOPE</span>
              <div className="flex rounded overflow-hidden border border-slate-700">
                {[["core", "Core (API + Nginx)"], ["all", `Full Stack (${containers.length || 24} Services)`]].map(([val, label]) => (
                  <button key={val} onClick={() => setControlScope(val)}
                    className="px-3 py-1.5 text-xs font-medium transition-colors"
                    style={{
                      background: controlScope === val ? "var(--dvas-blue)" : "var(--dvas-elev)",
                      color: controlScope === val ? "#fff" : "var(--dvas-text-dim)",
                    }}>
                    {label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Scope description */}
          <div className="text-[11px] text-slate-500 mb-4 p-2 rounded"
            style={{ background: "var(--dvas-bg)", border: "1px solid var(--dvas-border)" }}>
            {controlScope === "core"
              ? "⚡ Core scope controls drone_ingest_api + drone_nginx only. Database, analytics workers, and monitoring remain running. Recommended for routine maintenance."
              : `⚠ Full Stack scope controls all ${containers.length || 24} stack containers including the database. Use only for scheduled maintenance windows or security incidents.`}
          </div>

          {/* Action result banner */}
          {actionResult && (
            <div className="mb-4 px-3 py-2 rounded text-xs flex items-center gap-2"
              style={{
                background: actionResult.ok ? "var(--dvas-safe-bg)" : "var(--dvas-crit-bg)",
                color: actionResult.ok ? "var(--dvas-safe)" : "var(--dvas-crit)",
                border: `1px solid ${actionResult.ok ? "var(--dvas-safe)" : "var(--dvas-crit)"}40`,
              }}>
              {actionResult.ok ? <CheckCircle2 size={13} /> : <XCircle size={13} />}
              {actionResult.message}
            </div>
          )}

          {/* Control buttons */}
          <div className="flex flex-wrap gap-3">
            {/* START */}
            <button
              data-testid="ingest-control-start"
              disabled={actionBusy || isOnline}
              onClick={() => openModal("start")}
              className="flex items-center gap-2 px-5 py-2.5 rounded text-sm font-semibold transition-all"
              style={{
                background: !actionBusy && !isOnline ? "var(--dvas-safe)" : "var(--dvas-elev)",
                color: !actionBusy && !isOnline ? "#fff" : "var(--dvas-text-dim)",
                opacity: actionBusy || isOnline ? 0.5 : 1,
                cursor: actionBusy || isOnline ? "not-allowed" : "pointer",
                border: "1px solid transparent",
              }}>
              <Power size={14} />
              Start Ingest
            </button>

            {/* STOP */}
            <button
              data-testid="ingest-control-stop"
              disabled={actionBusy || !isOnline}
              onClick={() => openModal("stop")}
              className="flex items-center gap-2 px-5 py-2.5 rounded text-sm font-semibold transition-all"
              style={{
                background: !actionBusy && isOnline ? "#dc2626" : "var(--dvas-elev)",
                color: !actionBusy && isOnline ? "#fff" : "var(--dvas-text-dim)",
                opacity: actionBusy || !isOnline ? 0.5 : 1,
                cursor: actionBusy || !isOnline ? "not-allowed" : "pointer",
                border: "1px solid transparent",
              }}>
              <PowerOff size={14} />
              Stop Ingest
            </button>

            {/* RESTART */}
            <button
              data-testid="ingest-control-restart"
              disabled={actionBusy || !isOnline}
              onClick={() => openModal("restart")}
              className="flex items-center gap-2 px-5 py-2.5 rounded text-sm font-semibold transition-all"
              style={{
                background: "var(--dvas-elev)",
                color: !actionBusy && isOnline ? "var(--dvas-warn)" : "var(--dvas-text-dim)",
                opacity: actionBusy || !isOnline ? 0.5 : 1,
                cursor: actionBusy || !isOnline ? "not-allowed" : "pointer",
                border: `1px solid ${!actionBusy && isOnline ? "var(--dvas-warn)" : "var(--dvas-border)"}40`,
              }}>
              <RotateCcw size={14} className={actionBusy ? "animate-spin" : ""} />
              Restart Ingest
            </button>

            {/* Busy spinner */}
            {actionBusy && (
              <div className="flex items-center gap-2 text-xs text-amber-400 ml-2">
                <RotateCcw size={13} className="animate-spin" />
                Sending command…
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Row 1: Connection Status + Quick Stats ─────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
        {/* Connection Status Card */}
        <div className="lg:col-span-2 border border-slate-800 bg-slate-900 p-5">
          <div className="flex items-center gap-2 mb-4">
            <Server size={16} className="text-sky-400" />
            <span className="text-sm font-semibold" style={{ color: "var(--dvas-text)" }}>
              {t("ingest.server.status")}
            </span>
            <span className="ml-auto">
              {isOnline ? (
                <span className="flex items-center gap-1.5 px-2 py-1 rounded text-[10px] font-semibold"
                  style={{ background: "var(--dvas-safe-bg)", color: "var(--dvas-safe)" }}>
                  <StatusDot ok pulse /> {t("ingest.server.online")}
                </span>
              ) : (
                <span className="flex items-center gap-1.5 px-2 py-1 rounded text-[10px] font-semibold"
                  style={{ background: "var(--dvas-crit-bg)", color: "var(--dvas-crit)" }}>
                  <StatusDot ok={false} /> {t("ingest.server.offline")}
                </span>
              )}
            </span>
          </div>

          <div className="grid grid-cols-2 gap-3 text-xs">
            <div>
              <div className="dvas-label" style={{ fontSize: 9 }}>{t("ingest.server.url")}</div>
              <div className="mono text-slate-100 mt-0.5 truncate">{health?.url || "—"}</div>
            </div>
            <div>
              <div className="dvas-label" style={{ fontSize: 9 }}>{t("ingest.server.version")}</div>
              <div className="mono text-slate-100 mt-0.5">{health?.version || health?.api_version || "—"}</div>
            </div>
            <div>
              <div className="dvas-label" style={{ fontSize: 9 }}>{t("ingest.server.uptime")}</div>
              <div className="mono text-slate-100 mt-0.5">{formatUptime(health?.uptime_s || health?.uptime)}</div>
            </div>
            <div>
              <div className="dvas-label" style={{ fontSize: 9 }}>{t("ingest.server.wsRelay")}</div>
              <div className="flex items-center gap-1.5 mt-0.5">
                {wsConnected
                  ? <><Wifi size={12} className="text-emerald-400" /> <span className="mono text-emerald-400">{t("ingest.server.connected")}</span></>
                  : <><WifiOff size={12} className="text-slate-500" /> <span className="mono text-slate-500">{t("ingest.server.disconnected")}</span></>}
              </div>
            </div>
          </div>

          {/* Readiness check */}
          {readiness && (
            <div className="mt-3 p-2 rounded text-[10px] mono"
              style={{
                background: readiness.reachable && readiness.ready !== false ? "var(--dvas-safe-bg)" : "var(--dvas-warn-bg)",
                color: readiness.reachable && readiness.ready !== false ? "var(--dvas-safe)" : "var(--dvas-warn)",
              }}>
              {readiness.reachable ? (readiness.ready !== false ? "✓ All systems ready" : "⚠ Server reachable but downstream services degraded") : "✗ Server unreachable"}
              {readiness.redis_ok !== undefined && <span className="ml-2">· Redis: {readiness.redis_ok ? "✓" : "✗"}</span>}
              {readiness.keycloak_ok !== undefined && <span className="ml-2">· Keycloak: {readiness.keycloak_ok ? "✓" : "✗"}</span>}
            </div>
          )}

          <button
            data-testid="test-connection-btn"
            onClick={handleTest}
            disabled={testing}
            className="mt-3 w-full flex items-center justify-center gap-2 px-3 py-2 rounded text-xs font-medium transition-colors"
            style={{
              background: "var(--dvas-elev)",
              color: testing ? "var(--dvas-text-dim)" : "var(--dvas-blue)",
              border: "1px solid var(--dvas-border)",
            }}
          >
            <Radio size={12} />
            {testing ? "Testing…" : t("ingest.server.testConn")}
          </button>
        </div>

        {/* Quick Stats */}
        <div className="border border-slate-800 bg-slate-900 p-4">
          <div className="dvas-label mb-3" style={{ fontSize: 9 }}>{t("ingest.server.framesIngested")}</div>
          <div className="mono text-3xl font-bold text-sky-400">{totalFrames.toLocaleString()}</div>
          <div className="mono text-[10px] text-slate-500 mt-1">{t("ingest.server.framesQueued")}: {totalQueued}</div>
          <div className="mt-3">
            <div className="dvas-label mb-1" style={{ fontSize: 9 }}>{t("ingest.server.bytesIngested")}</div>
            <div className="mono text-lg text-slate-100 font-semibold">{formatBytes(totalBytes)}</div>
          </div>
        </div>

        <div className="border border-slate-800 bg-slate-900 p-4">
          <div className="dvas-label mb-3" style={{ fontSize: 9 }}>{t("ingest.server.fpsRate")}</div>
          <div className="mono text-3xl font-bold" style={{ color: fps > 0 ? "var(--dvas-safe)" : "var(--dvas-text-dim)" }}>
            {fps.toFixed(1)}
          </div>
          <div className="mono text-[10px] text-slate-500 mt-1">
            {t("ingest.server.connectedEdges")}: {activeDrones} / {drones.length}
          </div>
          <div className="mt-3">
            <Sparkline data={fpsHistory} color={fps > 0 ? "#10b981" : "#475569"} height={32} />
          </div>
        </div>
      </div>

      {/* ── Row 2: Connected Edge Devices ──────────────────────────────── */}
      <div className="border border-slate-800 bg-slate-900">
        <div className="p-3 border-b border-slate-800 text-sm flex items-center gap-2">
          <Wifi size={16} className="text-sky-400" />
          {t("ingest.server.connectedEdges")}
          <span className="ml-auto mono text-[10px] text-slate-500">
            {drones.length} {drones.length === 1 ? "drone" : "drones"}
          </span>
        </div>
        {drones.length === 0 ? (
          <div className="p-8 text-center text-xs" style={{ color: "var(--dvas-text-dim)" }}>
            {isOnline ? "No drones registered yet" : "Ingest server offline — cannot fetch drone status"}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs" data-testid="edge-devices-table">
              <thead className="bg-slate-950/60">
                <tr>
                  {[t("ingest.server.droneId"), t("ingest.server.missionId"), "Status",
                    t("ingest.server.queueDepth"), t("ingest.server.framesIngested"),
                    t("ingest.server.bytesIngested"), t("ingest.server.lastFrame")
                  ].map(h => (
                    <th key={h} className="text-left px-3 py-2 dvas-label border-b border-slate-800" style={{ fontSize: 10 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="mono">
                {drones.map(d => {
                  const isActive = d.queue_depth > 0 || (d.last_frame_ts && (Date.now() / 1000 - d.last_frame_ts) < 30);
                  return (
                    <tr key={d.drone_id} className="hover:bg-slate-800/40" data-testid={`drone-row-${d.drone_id}`}>
                      <td className="px-3 py-2 border-b border-slate-800/60">
                        <span className="text-slate-100 font-medium">{d.drone_id}</span>
                      </td>
                      <td className="px-3 py-2 border-b border-slate-800/60 text-slate-400">
                        {d.mission_id || "—"}
                      </td>
                      <td className="px-3 py-2 border-b border-slate-800/60">
                        {isActive ? (
                          <span className="flex items-center gap-1.5 text-emerald-400">
                            <StatusDot ok pulse /> Live
                          </span>
                        ) : (
                          <span className="flex items-center gap-1.5 text-slate-500">
                            <StatusDot ok={false} /> Idle
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2 border-b border-slate-800/60">
                        <span className={d.queue_depth > 50 ? "text-amber-400" : "text-slate-100"}>
                          {d.queue_depth ?? 0}
                        </span>
                      </td>
                      <td className="px-3 py-2 border-b border-slate-800/60 text-slate-100">
                        {(d.frames_total || d.frames_queued || 0).toLocaleString()}
                      </td>
                      <td className="px-3 py-2 border-b border-slate-800/60 text-slate-300">
                        {formatBytes(d.bytes_ingested || 0)}
                      </td>
                      <td className="px-3 py-2 border-b border-slate-800/60 text-slate-400">
                        {d.last_frame_ts ? fmtIST(d.last_frame_ts * 1000) : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Row 3: Live Frame Feed + Queue Health ─────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Live Frame Feed */}
        <div className="lg:col-span-2 border border-slate-800 bg-slate-900">
          <div className="p-3 border-b border-slate-800 text-sm flex items-center gap-2">
            <Radio size={16} className={wsConnected ? "text-emerald-400" : "text-slate-500"} />
            {t("ingest.server.liveFeed")}
            {wsConnected && (
              <span className="ml-2 flex items-center gap-1 text-[10px] font-medium text-emerald-400">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 dvas-live-dot" /> LIVE
              </span>
            )}
            <span className="ml-auto mono text-[10px] text-slate-500">
              {wsEvents.length} events
            </span>
          </div>
          <div ref={feedRef} className="max-h-80 overflow-auto" data-testid="live-feed">
            {wsEvents.length === 0 ? (
              <div className="p-8 text-center text-xs" style={{ color: "var(--dvas-text-dim)" }}>
                {t("ingest.server.noEvents")}
              </div>
            ) : (
              <div className="divide-y divide-slate-800/60">
                {wsEvents.map((ev, i) => {
                  const isMission = ev.event === "mission_started" || ev.event === "mission_ended";
                  const isFrame = ev.event === "frame_queued";
                  return (
                    <div
                      key={`${ev._received}-${i}`}
                      className={`px-3 py-2 text-xs flex items-start gap-3 transition-colors ${
                        i === 0 ? "bg-sky-500/5" : ""
                      } ${isMission ? "bg-amber-500/5" : ""}`}
                      data-testid={`feed-event-${i}`}
                    >
                      <div className="shrink-0 mt-0.5">
                        {isMission ? (
                          <AlertTriangle size={12} className="text-amber-400" />
                        ) : isFrame ? (
                          <Zap size={12} className="text-sky-400" />
                        ) : (
                          <Activity size={12} className="text-slate-500" />
                        )}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="mono font-medium text-slate-100">
                            {ev.event || ev.type || "event"}
                          </span>
                          {ev.drone_id && (
                            <span className="px-1.5 py-0.5 rounded text-[9px] font-semibold"
                              style={{ background: "var(--dvas-elev)", color: "var(--dvas-blue)" }}>
                              {ev.drone_id}
                            </span>
                          )}
                          {ev.change_score !== undefined && (
                            <span className="mono text-[10px] text-slate-400">
                              Δ {(ev.change_score * 100).toFixed(0)}%
                            </span>
                          )}
                        </div>
                        <div className="mono text-[10px] text-slate-500 mt-0.5">
                          {ev.mission_id && <span>mission: {ev.mission_id} · </span>}
                          {ev.message_id && <span>msg: {ev.message_id.slice(0, 12)}… · </span>}
                          {ev._received && fmtIST(ev._received)}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        {/* Queue Health */}
        <div className="border border-slate-800 bg-slate-900 p-4">
          <div className="flex items-center gap-2 mb-4">
            <Database size={14} className="text-sky-400" />
            <span className="text-sm font-semibold" style={{ color: "var(--dvas-text)" }}>
              {t("ingest.server.queueHealth")}
            </span>
          </div>
          {drones.length === 0 ? (
            <div className="text-xs text-center py-6" style={{ color: "var(--dvas-text-dim)" }}>
              No queues active
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              {drones.map(d => {
                const pct = Math.min(100, Math.round(((d.queue_depth || 0) / 1000) * 100));
                const barColor = pct > 85 ? "#ef4444" : pct > 60 ? "#f59e0b" : "#10b981";
                return (
                  <div key={d.drone_id}>
                    <div className="flex items-baseline justify-between mb-1">
                      <span className="dvas-label" style={{ fontSize: 9 }}>{d.drone_id}</span>
                      <span className="mono text-[11px] text-slate-100 font-semibold">{d.queue_depth || 0}</span>
                    </div>
                    <div className="h-2 bg-slate-800 rounded-full overflow-hidden">
                      <div
                        className="h-full transition-all duration-500 rounded-full"
                        style={{ width: `${pct}%`, background: barColor }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Throughput sparkline */}
          <div className="mt-5 pt-4" style={{ borderTop: "1px solid var(--dvas-border)" }}>
            <div className="flex items-center justify-between mb-2">
              <span className="dvas-label" style={{ fontSize: 9 }}>{t("ingest.server.throughput")}</span>
              <span className="mono text-[11px] font-semibold" style={{ color: fps > 0 ? "var(--dvas-safe)" : "var(--dvas-text-dim)" }}>
                {fps.toFixed(1)} f/s
              </span>
            </div>
            <Sparkline data={fpsHistory} color={fps > 0 ? "#10b981" : "#475569"} height={40} />
          </div>
        </div>
      </div>

      {/* ── Row 4: Container Status Grid ─────────────────────────────────── */}
      <ContainerGrid containers={containers} loading={containersLoading} />

    </div>
  );
}

/* ---------- OVERVIEW ---------- */
// Values the backend does not measure arrive as null and are shown as "—" (ING-03…08)
const na = (v, suffix = "") => (v === null || v === undefined ? "—" : `${v}${suffix}`);

function StatCard({ label, value, sub, tone = "text-slate-100", Icon }) {
  return (
    <div className="border border-slate-800 bg-slate-900 p-4">
      <div className="flex items-center justify-between">
        <div className="dvas-label">{label}</div>
        {Icon && <Icon size={14} className="text-slate-500" />}
      </div>
      <div className={`mono text-2xl mt-1 font-semibold ${tone}`}>{value ?? "—"}</div>
      {sub && <div className="mono text-[10px] text-slate-500 mt-1">{sub}</div>}
    </div>
  );
}

function GaugeBar({ label, pct, tone = "auto", suffix = "%" }) {
  const unknown = pct === null || pct === undefined;
  const p = Math.min(100, Math.max(0, pct ?? 0));
  // auto: high = red (usage); good: high = green (validity)
  let barCls;
  if (tone === "good") {
    barCls = p >= 95 ? "bg-emerald-500" : p >= 85 ? "bg-amber-500" : "bg-red-500";
  } else if (tone === "sky") {
    barCls = "bg-sky-500";
  } else {
    barCls = p > 85 ? "bg-red-500" : p > 70 ? "bg-amber-500" : "bg-emerald-500";
  }
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <div className="dvas-label">{label}</div>
        <div className="mono text-sm text-slate-100 font-semibold">{unknown ? "—" : `${p}${suffix}`}</div>
      </div>
      <div className="h-2 bg-slate-800 rounded-full overflow-hidden">
        <div className={`h-full transition-all duration-500 ${barCls}`} style={{ width: `${unknown ? 0 : p}%` }} />
      </div>
    </div>
  );
}

function MeterBar({ label, used, total, suffix = "GB" }) {
  const p = used == null || !total ? 0 : Math.min(100, Math.round((used / total) * 100));
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <div className="dvas-label">{label}</div>
        <div className="mono text-[11px] text-slate-100"><span className="font-semibold">{na(used)}</span> <span className="text-slate-500">{total ? `/ ${total} ${suffix}` : suffix}</span></div>
      </div>
      <div className="h-2 bg-slate-800 rounded-full overflow-hidden">
        <div className={`h-full ${p > 85 ? "bg-red-500" : p > 70 ? "bg-amber-500" : "bg-sky-500"}`} style={{ width: `${p}%` }} />
      </div>
    </div>
  );
}

function Sparkline({ data, color = "#0ea5e9", height = 36 }) {
  if (!data || data.length < 2) {
    return <div className="mono text-[10px] text-slate-500" style={{ height }}>Collecting samples…</div>;
  }
  const max = Math.max(...data) || 1;
  const w = 100 / (data.length - 1 || 1);
  const points = data.map((v, i) => `${i * w},${100 - (v / max) * 100}`).join(" ");
  return (
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{ width: "100%", height }}>
      <polyline fill="none" stroke={color} strokeWidth="2" points={points} />
    </svg>
  );
}

function OverviewPage({ ov }) {
  if (!ov) {
    return <div className="border border-slate-800 bg-slate-900 p-8 text-center text-xs text-slate-500">Loading NOC overview…</div>;
  }
  const { streams, stream_stats: ss, infra, queues, frames, metadata, api: apiInfo, trend } = ov;

  return (
    <div className="flex flex-col gap-4">
      {/* Row 1: 6 stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4">
        <StatCard label="Active streams"   value={ss.active}   tone="text-emerald-400" Icon={CheckCircle2} />
        <StatCard label="Warning streams"  value={ss.warning}  tone="text-amber-400"   Icon={AlertTriangle} />
        <StatCard label="Critical streams" value={ss.critical} tone="text-red-400"     Icon={XCircle} />
        <StatCard label="Ingest server"    value={ss.ingest_reachable ? "reachable" : "unreachable"} tone={ss.ingest_reachable ? "text-emerald-400" : "text-red-400"} Icon={Zap} />
        <StatCard label="Avg frame age"    value={na(ss.avg_frame_age_s, " s")} tone={ss.avg_frame_age_s > 5 ? "text-amber-400" : "text-sky-400"} sub="time since last frame" Icon={TrendingUp} />
        <StatCard label="Avg FPS"          value={na(ss.avg_fps)} tone="text-sky-400" sub="measured between polls" Icon={Activity} />
      </div>

      {/* Row 2: Live stream table + Infra gauges */}
      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-12 lg:col-span-8 border border-slate-800 bg-slate-900">
          <div className="p-3 border-b border-slate-800 text-sm flex items-center gap-2 font-medium">
            <CircuitBoard size={16} className="text-sky-400" /> Live Stream Status
            <span className="ml-auto mono text-[10px] text-slate-500">{streams.length} feeds</span>
          </div>
          <table className="w-full text-xs mono">
            <thead className="bg-slate-950/40">
              <tr>{["Drone","Operator","FPS","Frame age","Rejected","Sync","Bitrate","Status"].map(h => (
                <th key={h} className="text-left px-3 py-2 dvas-label border-b border-slate-800" style={{ fontSize: 10 }}>{h}</th>
              ))}</tr>
            </thead>
            <tbody>
              {streams.map(s => (
                <tr key={s.drone_id} data-testid={`stream-${s.drone_id}`} className="hover:bg-slate-800/40">
                  <td className="px-3 py-2 border-b border-slate-800/60 text-slate-100">{s.drone_id}</td>
                  <td className="px-3 py-2 border-b border-slate-800/60 text-slate-400">{s.operator || "—"}</td>
                  <td className="px-3 py-2 border-b border-slate-800/60">{na(s.fps)}</td>
                  <td className="px-3 py-2 border-b border-slate-800/60">
                    <span className={s.frame_age_s == null ? "text-slate-500" : s.frame_age_s > 15 ? "text-red-400" : s.frame_age_s > 5 ? "text-amber-400" : "text-emerald-400"}>{na(s.frame_age_s, " s")}</span>
                  </td>
                  <td className="px-3 py-2 border-b border-slate-800/60">
                    <span className={s.packet_drop_pct > 1 ? "text-red-400" : s.packet_drop_pct > 0.5 ? "text-amber-400" : "text-emerald-400"}>{na(s.packet_drop_pct, "%")}</span>
                  </td>
                  <td className="px-3 py-2 border-b border-slate-800/60">
                    <span className={`px-1.5 py-0.5 border text-[10px] ${s.sync === "synced" ? "text-emerald-400 border-emerald-500/40 bg-emerald-500/10" : s.sync === "drift" ? "text-amber-400 border-amber-500/40 bg-amber-500/10" : s.sync == null ? "text-slate-400 border-slate-600" : "text-red-400 border-red-500/40 bg-red-500/10"}`}>{s.sync || "unknown"}</span>
                  </td>
                  <td className="px-3 py-2 border-b border-slate-800/60">{na(s.bitrate_mbps, " Mbps")}</td>
                  <td className="px-3 py-2 border-b border-slate-800/60">
                    <span className={`px-1.5 py-0.5 border text-[10px] ${s.status === "live" ? "text-emerald-400 border-emerald-500/40 bg-emerald-500/10" : s.status === "degraded" ? "text-amber-400 border-amber-500/40 bg-amber-500/10" : "text-red-400 border-red-500/40 bg-red-500/10"}`}>{s.status}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="col-span-12 lg:col-span-4 border border-slate-800 bg-slate-900 p-4 space-y-3">
          <div className="text-sm flex items-center gap-2 font-medium">
            <Gauge size={14} className="text-sky-400" /> Infrastructure
          </div>
          <GaugeBar label="CPU" pct={infra.cpu_pct} />
          <GaugeBar label="RAM" pct={infra.ram_pct} />
          <GaugeBar label="GPU" pct={infra.gpu_pct} />
          <GaugeBar label="Disk" pct={infra.disk_pct} />
          <MeterBar label="Network bandwidth" used={infra.network_mbps} total={infra.network_cap_mbps} suffix="Mbps" />
          <MeterBar label="GPU memory" used={infra.gpu_mem_used_gb} total={infra.gpu_mem_total_gb} suffix="GB" />
        </div>
      </div>

      {/* Row 3: Queue monitoring */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {queues.map(q => (
          <div key={q.name} data-testid={`queue-${q.name}`} className="border border-slate-800 bg-slate-900 p-4">
            <div className="flex items-center justify-between mb-2">
              <div className="dvas-label capitalize">{q.name}</div>
              <Database size={12} className="text-slate-500" />
            </div>
            <div className="mono text-2xl font-semibold text-sky-400">{na(q.depth)}</div>
            <div className="mono text-[10px] text-slate-500 mt-0.5">queue length · {q.source}</div>
            <div className="mt-3 grid grid-cols-2 gap-1 text-[10px] mono">
              <div><div className="text-slate-500">Throughput</div><div className="text-slate-300">{na(q.throughput_fps)}{q.throughput_unit && q.throughput_fps != null ? ` ${q.throughput_unit}` : ""}</div></div>
              <div><div className="text-slate-500">Err</div><div className={q.error_rate_pct > 1 ? "text-red-400" : "text-slate-300"}>{na(q.error_rate_pct, "%")}</div></div>
            </div>
          </div>
        ))}
      </div>

      {/* Row 4: Frames + Metadata */}
      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-12 lg:col-span-7 border border-slate-800 bg-slate-900 p-4">
          <div className="flex items-center gap-2 mb-3 font-medium text-sm">
            <TrendingUp size={14} className="text-sky-400" /> Frame Ingestion
            <span className="mono text-[10px] text-slate-500 font-normal">{frames.window}{frames.available === false ? " · ingest server unreachable" : ""}</span>
          </div>
          <div className="grid grid-cols-3 gap-3 mb-3">
            <div>
              <div className="dvas-label">Total frames</div>
              <div className="mono text-2xl font-semibold text-sky-400">{frames.total_frames == null ? "—" : frames.total_frames.toLocaleString()}</div>
            </div>
            <div>
              <div className="dvas-label">Accepted</div>
              <div className="mono text-2xl font-semibold text-emerald-400">{na(frames.selection_ratio_pct, "%")}</div>
              <div className="mono text-[10px] text-slate-500">{na(frames.kept_frames?.toLocaleString())} frames</div>
            </div>
            <div>
              <div className="dvas-label">Dropped</div>
              <div className={`mono text-2xl font-semibold ${frames.drop_ratio_pct > 0.5 ? "text-red-400" : "text-amber-400"}`}>{na(frames.drop_ratio_pct, "%")}</div>
              <div className="mono text-[10px] text-slate-500">{na(frames.dropped_frames?.toLocaleString())} rejected</div>
            </div>
          </div>
          <div className="border-t border-slate-800 pt-3">
            <div className="dvas-label mb-2">Measured FPS, all feeds (last {frames.sparkline_fps?.length || 0} samples)</div>
            <Sparkline data={frames.sparkline_fps} color="#0ea5e9" height={48} />
          </div>
        </div>

        <div className="col-span-12 lg:col-span-5 border border-slate-800 bg-slate-900 p-4 space-y-3">
          <div className="flex items-center gap-2 font-medium text-sm">
            <HardDrive size={14} className="text-sky-400" /> Metadata Validation
          </div>
          <GaugeBar label="GPS validity" pct={metadata.gps_valid_pct} tone="good" />
          <GaugeBar label="Timestamp validity" pct={metadata.timestamp_valid_pct} tone="good" />
          <GaugeBar label="Sensor validity" pct={metadata.sensor_valid_pct} tone="good" />
          <div className="mono text-[10px] text-slate-500 pt-2 border-t border-slate-800">
            {metadata.records_checked} drone record(s) and {metadata.timestamps_checked} frame timestamp(s) checked · {metadata.scope}
          </div>
        </div>
      </div>

      {/* Row 5: API monitor */}
      <div className="border border-slate-800 bg-slate-900">
        <div className="p-3 border-b border-slate-800 flex items-center gap-3 text-sm font-medium">
          <Activity size={16} className="text-sky-400" /> API Monitor
          <span className="mono text-[10px] text-slate-500 ml-2">dependency probes · avg p95 {apiInfo.avg_p95_ms} ms · {apiInfo.avg_success_pct}% ok (last 20 probes each)</span>
        </div>
        <table className="w-full text-xs mono">
          <thead className="bg-slate-950/40">
            <tr>{["Service","Last probe","p95","Success","Health"].map(h => (
              <th key={h} className="text-left px-3 py-2 dvas-label border-b border-slate-800" style={{ fontSize: 10 }}>{h}</th>
            ))}</tr>
          </thead>
          <tbody>
            {apiInfo.endpoints.map(e => {
              const healthy = e.ok && e.success_pct >= 99.5 && e.p95_ms < 200;
              const warn = !healthy && e.ok;
              return (
                <tr key={e.path} data-testid={`api-ep-${e.path}`} className="hover:bg-slate-800/40">
                  <td className="px-3 py-2 border-b border-slate-800/60 text-slate-100">{e.path}</td>
                  <td className="px-3 py-2 border-b border-slate-800/60">{e.ok ? "ok" : "failed"}</td>
                  <td className="px-3 py-2 border-b border-slate-800/60"><span className={e.p95_ms > 250 ? "text-red-400" : e.p95_ms > 150 ? "text-amber-400" : "text-emerald-400"}>{e.p95_ms} ms</span></td>
                  <td className="px-3 py-2 border-b border-slate-800/60">{e.success_pct}% <span className="text-slate-500">of {e.samples}</span></td>
                  <td className="px-3 py-2 border-b border-slate-800/60">
                    <span className={`px-1.5 py-0.5 border text-[10px] ${healthy ? "text-emerald-400 border-emerald-500/40 bg-emerald-500/10" : warn ? "text-amber-400 border-amber-500/40 bg-amber-500/10" : "text-red-400 border-red-500/40 bg-red-500/10"}`}>
                      {healthy ? "healthy" : warn ? "degraded" : "critical"}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Row 6: CPU / GPU trend chart */}
      <div className="border border-slate-800 bg-slate-900 p-3">
        <div className="dvas-label mb-2 flex items-center gap-2"><Cpu size={12}/> CPU / GPU utilisation trend</div>
        <div style={{ width: "100%", height: 220 }}>
          <ResponsiveContainer>
            <LineChart data={trend}>
              <CartesianGrid stroke="rgba(148,163,184,0.2)" />
              <XAxis dataKey="t" stroke="#94a3b8" fontSize={10} />
              <YAxis stroke="#94a3b8" fontSize={10} domain={[0, 100]} />
              <Tooltip contentStyle={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)", fontSize: 12, borderRadius: 8 }} />
              <Line dataKey="cpu" name="CPU %" stroke="#0ea5e9" dot={false} strokeWidth={2} />
              <Line dataKey="gpu" name="GPU %" stroke="#a78bfa" dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}

/* ---------- FeedsPage etc ---------- */

function FeedsPage({ feeds, filter, setFilter, setSort }) {
  return (
    <div className="border border-slate-800 bg-slate-900">
      <div className="p-3 border-b border-slate-800 flex items-center gap-3">
        <CircuitBoard size={16} className="text-sky-400" />
        <div className="text-sm">Feed Overview</div>
        <div className="ml-auto flex items-center gap-2">
          <select data-testid="feed-filter" value={filter} onChange={e => setFilter(e.target.value)} className="bg-slate-950 border border-slate-700 text-xs mono px-2 py-1">
            <option value="all">all</option><option value="live">live</option>
            <option value="degraded">degraded</option><option value="offline">offline (dropped)</option>
          </select>
          <select data-testid="feed-sort" onChange={e => setSort(e.target.value)} className="bg-slate-950 border border-slate-700 text-xs mono px-2 py-1">
            <option value="drone_id">sort: drone</option><option value="status">sort: status</option>
            <option value="mission_id">sort: mission</option><option value="operator">sort: operator</option>
          </select>
        </div>
      </div>
      <table className="w-full text-xs mono">
        <thead className="bg-slate-950/50">
          <tr>{["Drone ID","Operator","Mission","Status","Last heartbeat","FPS","Bitrate"].map(h => (
            <th key={h} className="text-left px-3 py-2 dvas-label border-b border-slate-800" style={{ fontSize: 10 }}>{h}</th>
          ))}</tr>
        </thead>
        <tbody>
          {feeds.map(f => (
            <tr key={f.drone_id} data-testid={`feed-row-${f.drone_id}`} className="hover:bg-slate-800/60">
              <td className="px-3 py-2 border-b border-slate-800/60">{f.drone_id}</td>
              <td className="px-3 py-2 border-b border-slate-800/60">{f.operator || "—"}</td>
              <td className="px-3 py-2 border-b border-slate-800/60">{f.mission_id || "—"}</td>
              <td className="px-3 py-2 border-b border-slate-800/60">
                <span className={`px-1.5 py-0.5 border text-[10px] ${
                  f.status === "live" ? "text-emerald-400 border-emerald-500/40 bg-emerald-500/10"
                  : f.status === "degraded" ? "text-amber-400 border-amber-500/40 bg-amber-500/10"
                  : "text-red-400 border-red-500/40 bg-red-500/10"
                }`}>{f.status}</span>
              </td>
              <td className="px-3 py-2 border-b border-slate-800/60 text-slate-400">{f.last_heartbeat ? fmtIST(f.last_heartbeat) : "no frames yet"}</td>
              <td className="px-3 py-2 border-b border-slate-800/60">{na(f.fps)}</td>
              <td className="px-3 py-2 border-b border-slate-800/60">{na(f.bitrate_mbps, " Mbps")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function QueuePage({ throughput, queue, ingestSrv }) {
  const backlog = queue.filter(q => q.depth > 180);
  // Real drone-level frames data from the ingest server
  const drones = ingestSrv?.status?.drones || [];
  const droneFrameData = drones.map(d => ({
    drone: d.drone_id || d.id || "—",
    frames: d?.ingest_stats?.frames_received || 0,
    queued: d?.queue?.messages_ready || 0,
    bytes: d?.ingest_stats?.bytes_ingested || 0,
    online: d?.online ?? false,
  }));

  return (
    <div className="space-y-4">
      {backlog.length > 0 && (
        <div className="border border-amber-500/40 bg-amber-500/10 text-amber-300 text-xs px-3 py-2 mono">
          Back-pressure detected on {backlog.length} partition(s): {backlog.map(b => b.partition).join(", ")}
        </div>
      )}

      {/* Live drone frame ingestion table */}
      {droneFrameData.length > 0 && (
        <div className="border border-slate-800 bg-slate-900 p-3">
          <div className="dvas-label mb-2">Live Drone Ingestion · Real-Time</div>
          <table className="w-full text-xs mono">
            <thead>
              <tr className="text-slate-500 border-b border-slate-800">
                <th className="text-left pb-1.5 pr-3">Drone ID</th>
                <th className="text-right pb-1.5 pr-3">Frames Rcv'd</th>
                <th className="text-right pb-1.5 pr-3">Queue Depth</th>
                <th className="text-right pb-1.5">Bytes In</th>
              </tr>
            </thead>
            <tbody>
              {droneFrameData.map(d => (
                <tr key={d.drone} className="border-b border-slate-800/50">
                  <td className="py-1.5 pr-3 flex items-center gap-1.5">
                    <span
                      className="inline-block w-1.5 h-1.5 rounded-full"
                      style={{ background: d.online ? "#10b981" : "#ef4444" }}
                    />
                    {d.drone}
                  </td>
                  <td className="text-right py-1.5 pr-3 text-sky-400">{d.frames.toLocaleString()}</td>
                  <td className="text-right py-1.5 pr-3 text-amber-400">{d.queued}</td>
                  <td className="text-right py-1.5 text-slate-400">
                    {d.bytes > 0 ? `${(d.bytes / 1024 / 1024).toFixed(1)} MB` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="border border-slate-800 bg-slate-900 p-3">
        <div className="dvas-label mb-2">Cumulative Frames Ingested · rolling snapshots</div>
        <div style={{ width: "100%", height: 260 }}>
          <ResponsiveContainer>
            <LineChart data={throughput}>
              <CartesianGrid stroke="#1e293b" />
              <XAxis dataKey="t" hide />
              <YAxis stroke="#64748b" fontSize={10} />
              <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155", fontSize: 12 }} />
              <Line dataKey="ingested" name="Frames In" stroke="#0ea5e9" dot={false} strokeWidth={2} />
              <Line dataKey="queued" name="Queue Depth" stroke="#f59e0b" dot={false} strokeWidth={1} strokeDasharray="4 2" />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div className="border border-slate-800 bg-slate-900 p-3">
        <div className="dvas-label mb-2">Message Queue Depth per Partition</div>
        <div style={{ width: "100%", height: 220 }}>
          <ResponsiveContainer>
            <BarChart data={queue}>
              <CartesianGrid stroke="#1e293b" />
              <XAxis dataKey="partition" stroke="#64748b" fontSize={10} />
              <YAxis stroke="#64748b" fontSize={10} />
              <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155", fontSize: 12 }} />
              <Bar dataKey="depth" fill="#f59e0b" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}

function GpuPage({ gpu, suggestions = [], onUpdateStatus, message }) {
  const pendingSuggestionsCount = suggestions.filter(s => s.status === "PENDING").length;

  return (
    <div className="space-y-4">
      {message && <div className="mono text-[11px] text-sky-300 border border-sky-500/30 bg-sky-500/5 px-3 py-2">{message}</div>}
      {/* Edge Operator AI Model Suggestions Panel */}
      <div className="border border-slate-800 bg-slate-900 rounded overflow-hidden font-mono">
        <div className="p-3 border-b border-slate-800 flex items-center justify-between bg-slate-950/40">
          <div className="flex items-center gap-2">
            <Cpu size={16} className="text-sky-400" />
            <span className="text-sm font-medium text-slate-100 font-sans">Edge Operator AI Model Suggestions</span>
            {pendingSuggestionsCount > 0 && (
              <span className="px-2 py-0.5 rounded-full text-[10px] bg-sky-500/20 text-sky-300 border border-sky-500/40 font-mono font-bold animate-pulse">
                {pendingSuggestionsCount} Pending Request{pendingSuggestionsCount > 1 ? "s" : ""}
              </span>
            )}
          </div>
          <span className="text-[11px] text-slate-400 font-mono hidden md:inline">
            Analytical Layer Models (Crowd, Person, Intrusion, ANPR, Vehicle)
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-xs text-left">
            <thead className="bg-slate-950/60 text-slate-400 text-[10px] uppercase border-b border-slate-800">
              <tr>
                <th className="px-3 py-2.5">Priority</th>
                <th className="px-3 py-2.5">Requested AI Models</th>
                <th className="px-3 py-2.5">Location / Place</th>
                <th className="px-3 py-2.5">Operator Reason / Context</th>
                <th className="px-3 py-2.5">Suggested By</th>
                <th className="px-3 py-2.5">Time</th>
                <th className="px-3 py-2.5">Status</th>
                <th className="px-3 py-2.5 text-right">GPU Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {suggestions.length === 0 && (
                <tr>
                  <td colSpan={8} className="p-6 text-center text-slate-500 text-xs">
                    No edge model suggestions received yet. When operators request AI models from the field, they will appear here.
                  </td>
                </tr>
              )}
              {suggestions.map((sugg) => (
                <tr key={sugg.id} className="hover:bg-slate-800/30 transition-colors">
                  <td className="px-3 py-3 whitespace-nowrap">
                    <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase border ${
                      sugg.priority === "urgent" ? "bg-red-500/20 border-red-500/40 text-red-300" :
                      sugg.priority === "high" ? "bg-amber-500/20 border-amber-500/40 text-amber-300" :
                      "bg-sky-500/20 border-sky-500/40 text-sky-300"
                    }`}>
                      {sugg.priority || "normal"}
                    </span>
                  </td>
                  <td className="px-3 py-3">
                    <div className="flex flex-wrap gap-1 max-w-xs">
                      {(sugg.model_names || sugg.models || []).map((m, idx) => (
                        <span key={idx} className="px-1.5 py-0.5 text-[10px] bg-slate-800 text-sky-200 border border-slate-700 rounded">
                          {m}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-3 py-3 whitespace-nowrap text-slate-300">
                    📍 {sugg.location || "Edge Station"}
                  </td>
                  <td className="px-3 py-3 text-slate-400 max-w-xs truncate text-[11px]">
                    {sugg.reason || "Operational monitoring request"}
                  </td>
                  <td className="px-3 py-3 whitespace-nowrap text-slate-400 text-[11px]">
                    {sugg.suggested_by || "Edge Operator"}
                  </td>
                  <td className="px-3 py-3 whitespace-nowrap text-slate-500 text-[11px]">
                    {sugg.timestamp ? fmtIST(sugg.timestamp) : "—"}
                  </td>
                  <td className="px-3 py-3 whitespace-nowrap">
                    <span className={`px-2 py-0.5 rounded text-[10px] font-semibold border ${
                      sugg.status === "DEPLOYED_TO_GPU"
                        ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/40"
                        : sugg.status === "ACKNOWLEDGED"
                        ? "bg-slate-800 text-slate-300 border-slate-700"
                        : "bg-amber-500/20 text-amber-300 border-amber-500/40 animate-pulse"
                    }`}>
                      {sugg.status === "DEPLOYED_TO_GPU" ? "DEPLOYED ON GPU" : sugg.status || "PENDING"}
                    </span>
                  </td>
                  <td className="px-3 py-3 whitespace-nowrap text-right">
                    <div className="flex items-center justify-end gap-1.5">
                      {sugg.status !== "DEPLOYED_TO_GPU" && (
                        <button
                          onClick={() => onUpdateStatus && onUpdateStatus(sugg.id, "DEPLOYED_TO_GPU")}
                          className="px-2.5 py-1 text-[11px] bg-emerald-600/30 hover:bg-emerald-600/50 text-emerald-200 border border-emerald-500/40 rounded transition-colors"
                        >
                          Deploy to GPU
                        </button>
                      )}
                      {sugg.status === "PENDING" && (
                        <button
                          onClick={() => onUpdateStatus && onUpdateStatus(sugg.id, "ACKNOWLEDGED")}
                          className="px-2 py-1 text-[11px] bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded transition-colors"
                        >
                          Ack
                        </button>
                      )}
                      {sugg.status === "DEPLOYED_TO_GPU" && (
                        <span className="text-emerald-400 text-[11px] font-semibold">✓ Active</span>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="border border-slate-800 bg-slate-900 p-3">
        <div className="dvas-label mb-2">GPU Utilisation · per node</div>
        <div style={{ width: "100%", height: 240 }}>
          <ResponsiveContainer>
            <BarChart data={gpu}>
              <CartesianGrid stroke="#1e293b" />
              <XAxis dataKey="node" stroke="#64748b" fontSize={10} />
              <YAxis stroke="#64748b" fontSize={10} />
              <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155", fontSize: 12 }} />
              <Bar dataKey="util_pct" fill="#0ea5e9" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div className="border border-slate-800 bg-slate-900 p-3 text-xs mono">
        <div className="dvas-label mb-2">GPU memory · per node</div>
        {(gpu || []).map(g => (
          <div key={g.node} className="flex items-center justify-between py-1 border-b border-slate-800/60">
            <span className="text-slate-300">{g.node}</span>
            <span className="text-slate-100">{na(g.mem_used_gb)} / {na(g.mem_total_gb)} GB · {na(g.util_pct, "%")} util</span>
          </div>
        ))}
        <div className="text-[10px] text-slate-500 mt-2">
          The analyzers do not report batch size or queue-to-inference latency, so those charts are not shown.
        </div>
      </div>
    </div>
  );
}

function AlertsPage({ alerts, onAck, incidents = [], onAckIncident }) {
  const { t } = useI18n();
  const [activeTab, setActiveTab] = useState("edge"); // "edge" | "infra"
  const openIncidents = incidents.filter(i => i.status !== "ACKNOWLEDGED");

  return (
    <div className="space-y-4 font-mono">
      {/* Tab Switcher */}
      <div className="flex items-center gap-2 border-b border-slate-800 pb-2">
        <button
          onClick={() => setActiveTab("edge")}
          className={`px-3 py-1.5 text-xs rounded transition-colors flex items-center gap-2 ${
            activeTab === "edge"
              ? "bg-red-500/20 text-red-300 border border-red-500/40"
              : "text-slate-400 hover:text-white"
          }`}
        >
          <ShieldAlert size={14} className="text-red-400" />
          <span>Edge Station Incidents</span>
          {openIncidents.length > 0 && (
            <span className="px-1.5 py-0.2 rounded-full text-[10px] bg-red-500 text-white font-bold animate-pulse">
              {openIncidents.length}
            </span>
          )}
        </button>
        <button
          onClick={() => setActiveTab("infra")}
          className={`px-3 py-1.5 text-xs rounded transition-colors flex items-center gap-2 ${
            activeTab === "infra"
              ? "bg-amber-500/20 text-amber-300 border border-amber-500/40"
              : "text-slate-400 hover:text-white"
          }`}
        >
          <AlertTriangle size={14} className="text-amber-400" />
          <span>Infrastructure Alerts</span>
          {alerts.length > 0 && (
            <span className="px-1.5 py-0.2 rounded-full text-[10px] bg-amber-500/40 text-amber-300 font-bold">
              {alerts.length}
            </span>
          )}
        </button>
      </div>

      {activeTab === "edge" && (
        <div className="border border-slate-800 bg-slate-900 rounded">
          <div className="p-3 border-b border-slate-800 text-sm flex items-center justify-between font-sans">
            <div className="flex items-center gap-2">
              <ShieldAlert size={16} className="text-red-400" />
              <span className="font-medium text-slate-100">Live Edge Station Incidents</span>
              <span className="text-xs text-slate-500">({incidents.length} total)</span>
            </div>
            <div className="text-[11px] text-slate-400">
              Relayed from edge field laptops via Ingest API / WebSocket
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs text-left">
              <thead className="bg-slate-950/60 text-slate-400 text-[10px] uppercase border-b border-slate-800">
                <tr>
                  <th className="px-3 py-2.5">Severity</th>
                  <th className="px-3 py-2.5">Incident & Description</th>
                  <th className="px-3 py-2.5">Category</th>
                  <th className="px-3 py-2.5">Location / Place</th>
                  <th className="px-3 py-2.5">Reported By</th>
                  <th className="px-3 py-2.5">Timestamp</th>
                  <th className="px-3 py-2.5">Status</th>
                  <th className="px-3 py-2.5 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {incidents.length === 0 && (
                  <tr>
                    <td colSpan={8} className="p-6 text-center text-slate-500 text-xs font-mono">
                      No edge incidents received yet.
                    </td>
                  </tr>
                )}
                {incidents.map((inc) => (
                  <tr key={inc.id} className="hover:bg-slate-800/40 transition-colors">
                    <td className="px-3 py-3 whitespace-nowrap">
                      <span className={`px-2 py-0.5 border rounded text-[10px] font-bold uppercase ${
                        inc.severity === "CRITICAL" ? "text-red-400 border-red-500/40 bg-red-500/10" :
                        inc.severity === "HIGH" ? "text-amber-400 border-amber-500/40 bg-amber-500/10" :
                        inc.severity === "MEDIUM" ? "text-sky-400 border-sky-500/40 bg-sky-500/10" :
                        "text-slate-400 border-slate-600 bg-slate-800/40"
                      }`}>
                        {inc.severity}
                      </span>
                    </td>
                    <td className="px-3 py-3 min-w-[200px]">
                      <div className="font-semibold text-slate-200">{inc.title}</div>
                      <div className="text-[11px] text-slate-400 max-w-md truncate">{inc.description || "—"}</div>
                    </td>
                    <td className="px-3 py-3 whitespace-nowrap">
                      <span className="px-1.5 py-0.5 bg-slate-800 text-slate-300 rounded border border-slate-700 text-[10px] uppercase">
                        {(inc.category || "general").replace("_", " ")}
                      </span>
                    </td>
                    <td className="px-3 py-3 whitespace-nowrap text-slate-300">
                      📍 {inc.location || "Edge Station"}
                    </td>
                    <td className="px-3 py-3 whitespace-nowrap text-slate-400 font-mono text-[11px]">
                      {inc.created_by || "Edge Operator"}
                    </td>
                    <td className="px-3 py-3 whitespace-nowrap text-slate-400 font-mono text-[11px]">
                      {inc.timestamp ? fmtIST(inc.timestamp) : "—"}
                    </td>
                    <td className="px-3 py-3 whitespace-nowrap">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold border ${
                        inc.status === "ACKNOWLEDGED"
                          ? "bg-slate-800 text-slate-400 border-slate-700"
                          : "bg-red-500/20 text-red-300 border-red-500/40 animate-pulse"
                      }`}>
                        {inc.status || "OPEN"}
                      </span>
                    </td>
                    <td className="px-3 py-3 whitespace-nowrap text-right">
                      {inc.status !== "ACKNOWLEDGED" ? (
                        <button
                          onClick={() => onAckIncident && onAckIncident(inc.id)}
                          className="px-2.5 py-1 text-[11px] bg-emerald-600/20 hover:bg-emerald-600/40 text-emerald-300 border border-emerald-500/40 rounded transition-colors"
                        >
                          Acknowledge
                        </button>
                      ) : (
                        <span className="text-slate-500 text-[11px]">Acknowledged</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {activeTab === "infra" && (
        <div className="border border-slate-800 bg-slate-900 font-mono">
          <div className="p-3 border-b border-slate-800 text-sm flex items-center gap-2 font-sans">
            <AlertTriangle size={16} className="text-amber-400" /> {t("ingest.alertsTitle")}
          </div>
          <ul className="divide-y divide-slate-800">
            {alerts.length === 0 && <li className="p-6 text-center text-slate-500 text-xs">{t("ingest.allAcked")}</li>}
            {alerts.map(a => (
              <li key={a.id} data-testid={`infra-alert-${a.id}`} className="p-3 flex items-start gap-3">
                <span className={`px-1.5 py-0.5 border text-[10px] uppercase mono ${sevColor[a.severity]}`}>{a.severity}</span>
                <div className="flex-1 min-w-0">
                  <div className="text-xs text-slate-200">{a.text}</div>
                  <div className="mono text-[10px] text-slate-500 mt-1">{fmtIST(a.ts)}</div>
                </div>
                <button data-testid={`ack-infra-${a.id}`} onClick={() => onAck(a.id)} className="text-[11px] px-2 py-1 border border-slate-700 hover:border-sky-500">
                  {t("common.acknowledge")}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function AuditPage({ audit }) {
  const auditList = Array.isArray(audit) ? audit : (audit?.items || []);
  return (
    <div className="border border-slate-800 bg-slate-900">
      <div className="p-3 border-b border-slate-800 flex items-center gap-2">
        <ClipboardList size={16} className="text-sky-400" />
        <span className="text-sm font-medium text-slate-100">Audit Trail (Last 100 Events)</span>
      </div>
      <table className="w-full text-xs mono">
        <thead className="bg-slate-950/60">
          <tr>{["Timestamp","Action","Target","IP"].map(h => (
            <th key={h} className="text-left px-3 py-2 dvas-label border-b border-slate-800" style={{ fontSize: 10 }}>{h}</th>
          ))}</tr>
        </thead>
        <tbody>
          {auditList.map(l => (
            <tr key={l.id} className="hover:bg-slate-800/40">
              <td className="px-3 py-1.5 border-b border-slate-800/60 text-slate-400">{fmtIST(l.ts)}</td>
              <td className="px-3 py-1.5 border-b border-slate-800/60"><span className="px-1.5 py-0.5 border border-sky-500/40 bg-sky-500/10 text-sky-300 text-[10px]">{l.action}</span></td>
              <td className="px-3 py-1.5 border-b border-slate-800/60 text-slate-400">{l.target || "-"}</td>
              <td className="px-3 py-1.5 border-b border-slate-800/60 text-slate-500">{l.ip}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// F6l: users are managed in one place (Admin → Users); this tab only links there
function UsersPage() {
  const navigate = useNavigate();
  return (
    <div className="border border-slate-800 bg-slate-900 p-6 text-xs text-slate-300 flex items-center justify-between gap-4">
      <span>User accounts are created, suspended and reviewed on the Admin page.</span>
      <button onClick={() => { try { sessionStorage.setItem("dvas_adm_page", "users"); } catch {} navigate("/admin"); }}
        className="px-3 py-1.5 bg-sky-600 hover:bg-sky-500 text-white rounded">Open Admin → Users</button>
    </div>
  );
}
