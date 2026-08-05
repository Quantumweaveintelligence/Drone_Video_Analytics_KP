import { useCallback, useEffect, useMemo, useState, useRef } from "react";
import AppShell from "@/components/layout/AppShell";
import { api, ROLE_LABELS } from "@/lib/api";
import { useI18n } from "@/context/I18nContext";
import { useIngestServer } from "@/hooks/useIngestServer";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, BarChart, Bar, CartesianGrid,
} from "recharts";
import {
  CircuitBoard, AlertTriangle, Table as TableIcon, Activity, Cpu, ClipboardList,
  Users, Trash2, Plus, ShieldAlert, LayoutDashboard, Gauge, Database, HardDrive,
  Zap, CheckCircle2, XCircle, TrendingUp, Server, Wifi, WifiOff, Radio,
  Power, PowerOff, RotateCcw, AlertOctagon, Terminal, ChevronDown,
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

function TopNav({ page, setPage }) {
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
  const [users, setUsers] = useState([]);
  const ingestSrv = useIngestServer();
  const [ackedAlertIds, setAckedAlertIds] = useState([]);
  const [feedFilter, setFeedFilter] = useState("all");
  const [feedSort, setFeedSort] = useState("drone_id");

  const load = async () => {
    try {
      const { data } = await api.get("/infra/health"); setH(data);
    } catch (e) {}
  };
  const loadOverview = async () => {
    try { const { data } = await api.get("/infra/overview"); setOv(data); } catch (e) {}
  };
  const loadAudit = async () => {
    try { const { data } = await api.get("/audit-logs"); setAudit(data); } catch (e) {}
  };
  const loadUsers = async () => {
    try { const { data } = await api.get("/users"); setUsers(data); } catch (e) {}
  };

  useEffect(() => {
    load(); loadOverview();
    const t = setInterval(() => { load(); if (page === "overview") loadOverview(); setTick(x => x + 1); }, 4000);
    return () => clearInterval(t);
  }, [page]);
  useEffect(() => { if (page === "audit") loadAudit(); if (page === "users") loadUsers(); }, [page]);

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

      <div className="flex flex-col gap-4">
        <TopNav page={page} setPage={setPage} />

        <div className="min-w-0">
          {page === "server" && <ServerPage srv={ingestSrv} />}
          {page === "overview" && <OverviewPage ov={ov} />}
          {page === "feeds" && <FeedsPage feeds={feeds} filter={feedFilter} setFilter={setFeedFilter} setSort={setFeedSort} />}
          {page === "queue" && <QueuePage throughput={throughput} queue={h?.queue || []} ingestSrv={ingestSrv} />}
          {page === "gpu" && <GpuPage gpu={h?.gpu || []} />}
          {page === "alerts" && <AlertsPage alerts={(h?.alerts || []).filter(a => !ackedAlertIds.includes(a.id))} onAck={(id) => setAckedAlertIds(v => [...v, id])} />}
          {page === "audit" && <AuditPage audit={audit} />}
          {page === "users" && <UsersPage users={users} reload={loadUsers} />}
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
  const statusColor = (s) => {
    if (s === "running") return { bg: "var(--dvas-safe-bg)", fg: "var(--dvas-safe)" };
    if (s === "not_found") return { bg: "rgba(100,116,139,0.1)", fg: "#64748b" };
    return { bg: "var(--dvas-crit-bg)", fg: "var(--dvas-crit)" };
  };

  const coreNames = ["ingest_api", "nginx"];

  return (
    <div className="border border-slate-800 bg-slate-900">
      <div className="p-3 border-b border-slate-800 text-sm flex items-center gap-2">
        <Terminal size={16} className="text-sky-400" />
        Container Status
        <span className="ml-auto mono text-[10px] text-slate-500">
          {loading ? "refreshing…" : `${containers.filter(c => c.running).length} / ${containers.length} running`}
        </span>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-px bg-slate-800">
        {containers.map(c => {
          const col = statusColor(c.status);
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
              </div>
              <div className="mono text-[11px] font-semibold text-slate-100 truncate" title={c.short_name}>
                {c.short_name}
              </div>
              <div className="text-[9px] px-1.5 py-0.5 rounded font-semibold uppercase tracking-wide"
                style={{ background: col.bg, color: col.fg, width: "fit-content" }}>
                {c.status}
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
          : "This will stop ALL 18 ingest containers including the database. Evidence data is preserved. Only use for full maintenance windows.",
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
          : "This will restart all ingest stack containers. Full startup may take 2–3 minutes.",
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
                {[["core", "Core (API + Nginx)"], ["all", "Full Stack (All 18)"]].map(([val, label]) => (
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
              : "⚠ Full Stack scope controls all 18 ingest containers including the database. Use only for scheduled maintenance windows or security incidents."}
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
                        {d.last_frame_ts ? new Date(d.last_frame_ts * 1000).toLocaleTimeString() : "—"}
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
                          {ev._received && new Date(ev._received).toLocaleTimeString()}
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
        <div className="mono text-sm text-slate-100 font-semibold">{p}{suffix}</div>
      </div>
      <div className="h-2 bg-slate-800 rounded-full overflow-hidden">
        <div className={`h-full transition-all duration-500 ${barCls}`} style={{ width: `${p}%` }} />
      </div>
    </div>
  );
}

function MeterBar({ label, used, total, suffix = "GB" }) {
  const p = Math.min(100, Math.round((used / (total || 1)) * 100));
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <div className="dvas-label">{label}</div>
        <div className="mono text-[11px] text-slate-100"><span className="font-semibold">{used}</span> <span className="text-slate-500">/ {total} {suffix}</span></div>
      </div>
      <div className="h-2 bg-slate-800 rounded-full overflow-hidden">
        <div className={`h-full ${p > 85 ? "bg-red-500" : p > 70 ? "bg-amber-500" : "bg-sky-500"}`} style={{ width: `${p}%` }} />
      </div>
    </div>
  );
}

function Sparkline({ data, color = "#0ea5e9", height = 36 }) {
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
        <StatCard label="Authenticated"    value={ss.authenticated} sub="tokens active" Icon={Zap} />
        <StatCard label="Avg latency"      value={`${ss.avg_latency_ms} ms`} tone={ss.avg_latency_ms > 200 ? "text-amber-400" : "text-sky-400"} Icon={TrendingUp} />
        <StatCard label="Avg FPS"          value={ss.avg_fps} tone="text-sky-400" sub="across active feeds" Icon={Activity} />
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
              <tr>{["Drone","Operator","FPS","Latency","Packet drop","Sync","Bitrate","Status"].map(h => (
                <th key={h} className="text-left px-3 py-2 dvas-label border-b border-slate-800" style={{ fontSize: 10 }}>{h}</th>
              ))}</tr>
            </thead>
            <tbody>
              {streams.map(s => (
                <tr key={s.drone_id} data-testid={`stream-${s.drone_id}`} className="hover:bg-slate-800/40">
                  <td className="px-3 py-2 border-b border-slate-800/60 text-slate-100">{s.drone_id}</td>
                  <td className="px-3 py-2 border-b border-slate-800/60 text-slate-400">{s.operator}</td>
                  <td className="px-3 py-2 border-b border-slate-800/60">{s.fps}</td>
                  <td className="px-3 py-2 border-b border-slate-800/60">
                    <span className={s.latency_ms > 200 ? "text-red-400" : s.latency_ms > 100 ? "text-amber-400" : "text-emerald-400"}>{s.latency_ms} ms</span>
                  </td>
                  <td className="px-3 py-2 border-b border-slate-800/60">
                    <span className={s.packet_drop_pct > 1 ? "text-red-400" : s.packet_drop_pct > 0.5 ? "text-amber-400" : "text-emerald-400"}>{s.packet_drop_pct}%</span>
                  </td>
                  <td className="px-3 py-2 border-b border-slate-800/60">
                    <span className={`px-1.5 py-0.5 border text-[10px] ${s.sync === "synced" ? "text-emerald-400 border-emerald-500/40 bg-emerald-500/10" : s.sync === "drift" ? "text-amber-400 border-amber-500/40 bg-amber-500/10" : "text-red-400 border-red-500/40 bg-red-500/10"}`}>{s.sync}</span>
                  </td>
                  <td className="px-3 py-2 border-b border-slate-800/60">{s.bitrate_mbps} Mbps</td>
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
            <div className="mono text-2xl font-semibold text-sky-400">{q.depth}</div>
            <div className="mono text-[10px] text-slate-500 mt-0.5">queue length</div>
            <div className="mt-3 grid grid-cols-3 gap-1 text-[10px] mono">
              <div><div className="text-slate-500">Backlog</div><div className={q.backlog > 30 ? "text-amber-300" : "text-slate-300"}>{q.backlog}</div></div>
              <div><div className="text-slate-500">Thr.</div><div className="text-slate-300">{q.throughput_fps}</div></div>
              <div><div className="text-slate-500">Err</div><div className={q.error_rate_pct > 1 ? "text-red-400" : "text-slate-300"}>{q.error_rate_pct}%</div></div>
            </div>
          </div>
        ))}
      </div>

      {/* Row 4: Frames + Metadata */}
      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-12 lg:col-span-7 border border-slate-800 bg-slate-900 p-4">
          <div className="flex items-center gap-2 mb-3 font-medium text-sm">
            <TrendingUp size={14} className="text-sky-400" /> Frame Ingestion (24h)
          </div>
          <div className="grid grid-cols-3 gap-3 mb-3">
            <div>
              <div className="dvas-label">Total frames</div>
              <div className="mono text-2xl font-semibold text-sky-400">{(frames.total_frames_24h / 1e6).toFixed(2)}M</div>
            </div>
            <div>
              <div className="dvas-label">Selection ratio</div>
              <div className="mono text-2xl font-semibold text-emerald-400">{frames.selection_ratio_pct}%</div>
              <div className="mono text-[10px] text-slate-500">{(frames.kept_frames_24h / 1e3).toFixed(0)}k kept</div>
            </div>
            <div>
              <div className="dvas-label">Dropped</div>
              <div className={`mono text-2xl font-semibold ${frames.drop_ratio_pct > 0.5 ? "text-red-400" : "text-amber-400"}`}>{frames.drop_ratio_pct}%</div>
              <div className="mono text-[10px] text-slate-500">{frames.dropped_frames_24h.toLocaleString()} frames</div>
            </div>
          </div>
          <div className="border-t border-slate-800 pt-3">
            <div className="dvas-label mb-2">Live FPS (last 30 samples)</div>
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
            {metadata.records_checked_24h.toLocaleString()} records validated in the last 24h
          </div>
        </div>
      </div>

      {/* Row 5: API monitor */}
      <div className="border border-slate-800 bg-slate-900">
        <div className="p-3 border-b border-slate-800 flex items-center gap-3 text-sm font-medium">
          <Activity size={16} className="text-sky-400" /> API Monitor
          <span className="mono text-[10px] text-slate-500 ml-2">{apiInfo.total_rps} req/s · avg p95 {apiInfo.avg_p95_ms}ms · {apiInfo.avg_success_pct}% ok</span>
        </div>
        <table className="w-full text-xs mono">
          <thead className="bg-slate-950/40">
            <tr>{["Endpoint","Req/s","p95","Success","Health"].map(h => (
              <th key={h} className="text-left px-3 py-2 dvas-label border-b border-slate-800" style={{ fontSize: 10 }}>{h}</th>
            ))}</tr>
          </thead>
          <tbody>
            {apiInfo.endpoints.map(e => {
              const healthy = e.success_pct >= 99.5 && e.p95_ms < 200;
              const warn = !healthy && e.success_pct >= 99.0;
              return (
                <tr key={e.path} data-testid={`api-ep-${e.path}`} className="hover:bg-slate-800/40">
                  <td className="px-3 py-2 border-b border-slate-800/60 text-slate-100">{e.path}</td>
                  <td className="px-3 py-2 border-b border-slate-800/60">{e.rps}</td>
                  <td className="px-3 py-2 border-b border-slate-800/60"><span className={e.p95_ms > 250 ? "text-red-400" : e.p95_ms > 150 ? "text-amber-400" : "text-emerald-400"}>{e.p95_ms} ms</span></td>
                  <td className="px-3 py-2 border-b border-slate-800/60">{e.success_pct}%</td>
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
              <td className="px-3 py-2 border-b border-slate-800/60">{f.operator}</td>
              <td className="px-3 py-2 border-b border-slate-800/60">{f.mission_id}</td>
              <td className="px-3 py-2 border-b border-slate-800/60">
                <span className={`px-1.5 py-0.5 border text-[10px] ${
                  f.status === "live" ? "text-emerald-400 border-emerald-500/40 bg-emerald-500/10"
                  : f.status === "degraded" ? "text-amber-400 border-amber-500/40 bg-amber-500/10"
                  : "text-red-400 border-red-500/40 bg-red-500/10"
                }`}>{f.status}</span>
              </td>
              <td className="px-3 py-2 border-b border-slate-800/60 text-slate-400">{f.last_heartbeat?.slice(11, 19)}</td>
              <td className="px-3 py-2 border-b border-slate-800/60">{f.fps}</td>
              <td className="px-3 py-2 border-b border-slate-800/60">{f.bitrate_mbps} Mbps</td>
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

function GpuPage({ gpu }) {
  return (
    <div className="space-y-4">
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
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="border border-slate-800 bg-slate-900 p-3">
          <div className="dvas-label mb-2">Queue → inference latency (ms)</div>
          <div style={{ width: "100%", height: 220 }}>
            <ResponsiveContainer>
              <LineChart data={gpu}>
                <CartesianGrid stroke="#1e293b" />
                <XAxis dataKey="node" stroke="#64748b" fontSize={10} />
                <YAxis stroke="#64748b" fontSize={10} />
                <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155", fontSize: 12 }} />
                <Line dataKey="inference_ms" stroke="#a78bfa" dot={{ fill: "#a78bfa" }} strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div className="border border-slate-800 bg-slate-900 p-3">
          <div className="dvas-label mb-2">Batch size per node</div>
          <div style={{ width: "100%", height: 220 }}>
            <ResponsiveContainer>
              <BarChart data={gpu}>
                <CartesianGrid stroke="#1e293b" />
                <XAxis dataKey="node" stroke="#64748b" fontSize={10} />
                <YAxis stroke="#64748b" fontSize={10} />
                <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155", fontSize: 12 }} />
                <Bar dataKey="batch" fill="#10b981" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
    </div>
  );
}

function AlertsPage({ alerts, onAck }) {
  const { t } = useI18n();
  return (
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
              <div className="mono text-[10px] text-slate-500 mt-1">{a.ts.slice(11, 19)}</div>
            </div>
            <button data-testid={`ack-infra-${a.id}`} onClick={() => onAck(a.id)} className="text-[11px] px-2 py-1 border border-slate-700 hover:border-sky-500">
              {t("common.acknowledge")}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function AuditPage({ audit }) {
  return (
    <div className="border border-slate-800 bg-slate-900">
      <div className="p-3 border-b border-slate-800 text-sm flex items-center gap-2">
        <ClipboardList size={16} className="text-sky-400" /> Audit — Own Actions Only (read-only)
      </div>
      <div className="p-2 border-b border-slate-800 bg-amber-500/5 mono text-[11px] text-amber-300">
        This log is your own configuration-change history. Cross-role review is exclusive to Supervisory / Senior Officers.
      </div>
      <table className="w-full text-xs mono">
        <thead className="bg-slate-950/60">
          <tr>{["Timestamp","Action","Target","IP"].map(h => (
            <th key={h} className="text-left px-3 py-2 dvas-label border-b border-slate-800" style={{ fontSize: 10 }}>{h}</th>
          ))}</tr>
        </thead>
        <tbody>
          {audit.map(l => (
            <tr key={l.id} className="hover:bg-slate-800/40">
              <td className="px-3 py-1.5 border-b border-slate-800/60 text-slate-400">{new Date(l.ts).toLocaleString()}</td>
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

function UsersPage({ users, reload }) {
  const [form, setForm] = useState({ email: "", name: "", password: "", role: "drone_operator", jurisdiction: "", mission_scope: "" });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const submit = async (e) => {
    e.preventDefault(); setBusy(true); setMsg("");
    try {
      await api.post("/users", {
        ...form,
        mission_scope: form.mission_scope.split(",").map(s => s.trim()).filter(Boolean),
      });
      setForm({ email: "", name: "", password: "", role: "drone_operator", jurisdiction: "", mission_scope: "" });
      setMsg("User created successfully."); reload();
    } catch (e) { setMsg(e.response?.data?.detail || "Failed"); } finally { setBusy(false); }
  };

  const suspend = async (id) => {
    if (!window.confirm("Suspend this user?")) return;
    try { await api.delete(`/users/${id}`); reload(); } catch (e) { alert("failed"); }
  };

  return (
    <div className="space-y-4">
      <div className="border border-slate-800 bg-slate-900">
        <div className="p-3 border-b border-slate-800 text-sm flex items-center gap-2">
          <Plus size={16} className="text-sky-400" /> Create User (every change is auto-logged)
        </div>
        <form onSubmit={submit} className="p-3 grid grid-cols-1 md:grid-cols-6 gap-2">
          <input data-testid="user-email" required type="email" placeholder="email" value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} className="col-span-2 bg-slate-950 border border-slate-700 px-2 py-2 text-xs mono" />
          <input data-testid="user-name" required placeholder="full name" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} className="col-span-2 bg-slate-950 border border-slate-700 px-2 py-2 text-xs mono" />
          <input data-testid="user-password" required type="password" placeholder="initial password" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} className="col-span-2 bg-slate-950 border border-slate-700 px-2 py-2 text-xs mono" />
          <select data-testid="user-role" value={form.role} onChange={e => setForm({ ...form, role: e.target.value })} className="col-span-2 bg-slate-950 border border-slate-700 px-2 py-2 text-xs mono">
            {Object.entries(ROLE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <input data-testid="user-jurisdiction" placeholder="jurisdiction" value={form.jurisdiction} onChange={e => setForm({ ...form, jurisdiction: e.target.value })} className="col-span-2 bg-slate-950 border border-slate-700 px-2 py-2 text-xs mono" />
          <input data-testid="user-missions" placeholder="mission scope (M-101,M-102)" value={form.mission_scope} onChange={e => setForm({ ...form, mission_scope: e.target.value })} className="col-span-2 bg-slate-950 border border-slate-700 px-2 py-2 text-xs mono" />
          <button data-testid="user-create" disabled={busy} className="col-span-6 md:col-span-6 bg-sky-600 hover:bg-sky-500 text-white text-xs py-2">
            {busy ? "Creating…" : "Create User"}
          </button>
          {msg && <div className="col-span-6 mono text-[11px] text-slate-300">{msg}</div>}
        </form>
      </div>

      <div className="border border-slate-800 bg-slate-900">
        <div className="p-3 border-b border-slate-800 text-sm flex items-center gap-2">
          <Users size={16} className="text-sky-400" /> User Directory
        </div>
        <table className="w-full text-xs">
          <thead className="bg-slate-950/60">
            <tr>{["Name","Email","Role","Jurisdiction","Mission Scope","Status","Actions"].map(h => (
              <th key={h} className="text-left px-3 py-2 dvas-label border-b border-slate-800" style={{ fontSize: 10 }}>{h}</th>
            ))}</tr>
          </thead>
          <tbody className="mono">
            {users.map(u => (
              <tr key={u.id} data-testid={`user-row-${u.email}`} className="hover:bg-slate-800/40">
                <td className="px-3 py-2 border-b border-slate-800/60 text-slate-100">{u.name}</td>
                <td className="px-3 py-2 border-b border-slate-800/60">{u.email}</td>
                <td className="px-3 py-2 border-b border-slate-800/60">
                  <span className="px-1.5 py-0.5 border border-sky-500/40 bg-sky-500/10 text-sky-300 text-[10px]">{ROLE_LABELS[u.role]}</span>
                </td>
                <td className="px-3 py-2 border-b border-slate-800/60">{u.jurisdiction}</td>
                <td className="px-3 py-2 border-b border-slate-800/60 text-slate-400">{(u.mission_scope || []).join(", ") || "-"}</td>
                <td className="px-3 py-2 border-b border-slate-800/60">
                  {u.suspended ? <span className="text-red-400">suspended</span> : <span className="text-emerald-400">active</span>}
                </td>
                <td className="px-3 py-2 border-b border-slate-800/60">
                  {!u.suspended && (
                    <button data-testid={`suspend-${u.email}`} onClick={() => suspend(u.id)} className="px-2 py-1 border border-red-600/60 text-red-300 hover:bg-red-500/10 inline-flex items-center gap-1">
                      <Trash2 size={11} /> Suspend
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
