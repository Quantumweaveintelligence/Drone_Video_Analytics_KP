import { useEffect, useMemo, useState } from "react";
import AppShell from "@/components/layout/AppShell";
import { api, ROLE_LABELS } from "@/lib/api";
import { useI18n } from "@/context/I18nContext";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, BarChart, Bar, CartesianGrid,
} from "recharts";
import {
  CircuitBoard, AlertTriangle, Table as TableIcon, Activity, Cpu, ClipboardList,
  Users, Trash2, Plus, ShieldAlert, LayoutDashboard, Gauge, Database, HardDrive,
  Zap, CheckCircle2, XCircle, TrendingUp,
} from "lucide-react";

const sevColor = {
  crit: "text-red-400 border-red-500/40 bg-red-500/10",
  warn: "text-amber-400 border-amber-500/40 bg-amber-500/10",
  info: "text-sky-400 border-sky-500/40 bg-sky-500/10",
};

const PAGES = [
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
  const [page, setPage] = useState("overview");
  const [h, setH] = useState(null);
  const [ov, setOv] = useState(null);
  const [tick, setTick] = useState(0);
  const [audit, setAudit] = useState([]);
  const [users, setUsers] = useState([]);
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

  const throughput = useMemo(() => Array.from({ length: 24 }, (_, i) => ({
    t: i, ingested: 120 + Math.round(Math.sin((i + tick) / 3) * 30 + Math.random() * 15),
    processed: 110 + Math.round(Math.sin((i + tick) / 3 - 0.3) * 28 + Math.random() * 12),
  })), [tick]);

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
          {page === "overview" && <OverviewPage ov={ov} />}
          {page === "feeds" && <FeedsPage feeds={feeds} filter={feedFilter} setFilter={setFeedFilter} setSort={setFeedSort} />}
          {page === "queue" && <QueuePage throughput={throughput} queue={h?.queue || []} />}
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

function QueuePage({ throughput, queue }) {
  const backlog = queue.filter(q => q.depth > 180);
  return (
    <div className="space-y-4">
      {backlog.length > 0 && (
        <div className="border border-amber-500/40 bg-amber-500/10 text-amber-300 text-xs px-3 py-2 mono">
          Back-pressure detected on {backlog.length} partition(s): {backlog.map(b => b.partition).join(", ")}
        </div>
      )}
      <div className="border border-slate-800 bg-slate-900 p-3">
        <div className="dvas-label mb-2">Ingested vs Processed · fps (rolling 24 points)</div>
        <div style={{ width: "100%", height: 260 }}>
          <ResponsiveContainer>
            <LineChart data={throughput}>
              <CartesianGrid stroke="#1e293b" />
              <XAxis dataKey="t" hide />
              <YAxis stroke="#64748b" fontSize={10} />
              <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155", fontSize: 12 }} />
              <Line dataKey="ingested" stroke="#0ea5e9" dot={false} strokeWidth={2} />
              <Line dataKey="processed" stroke="#10b981" dot={false} strokeWidth={2} />
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
  const [form, setForm] = useState({ email: "", name: "", role: "drone_operator", jurisdiction: "", mission_scope: "" });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const submit = async (e) => {
    e.preventDefault(); setBusy(true); setMsg("");
    try {
      await api.post("/users", {
        ...form,
        mission_scope: form.mission_scope.split(",").map(s => s.trim()).filter(Boolean),
      });
      setForm({ email: "", name: "", role: "drone_operator", jurisdiction: "", mission_scope: "" });
      setMsg("User created."); reload();
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
          <select data-testid="user-role" value={form.role} onChange={e => setForm({ ...form, role: e.target.value })} className="col-span-2 bg-slate-950 border border-slate-700 px-2 py-2 text-xs mono">
            {Object.entries(ROLE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <input data-testid="user-jurisdiction" placeholder="jurisdiction" value={form.jurisdiction} onChange={e => setForm({ ...form, jurisdiction: e.target.value })} className="col-span-3 bg-slate-950 border border-slate-700 px-2 py-2 text-xs mono" />
          <input data-testid="user-missions" placeholder="mission scope (M-101,M-102)" value={form.mission_scope} onChange={e => setForm({ ...form, mission_scope: e.target.value })} className="col-span-3 bg-slate-950 border border-slate-700 px-2 py-2 text-xs mono" />
          <button data-testid="user-create" disabled={busy} className="col-span-6 md:col-span-6 bg-sky-600 hover:bg-sky-500 text-white text-xs py-2">
            {busy ? "Creating…" : "Create user (password: dvas1234)"}
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
