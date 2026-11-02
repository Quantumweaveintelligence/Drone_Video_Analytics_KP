import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/context/AuthContext";
import { useCommand } from "@/context/CommandContext";
import { api, apiError, downloadSigned } from "@/lib/api";
import { istDate, istDaysAgo, istMonday, fmtIST } from "@/lib/ist";
import { useDvasSocket } from "@/lib/useDvasSocket";
import { FileText, Download, RefreshCw, CheckCircle, XCircle, Eye, Send, X } from "lucide-react";

// Default parameters per period kind (IST calendar dates)
const defaultsFor = (period) => {
  if (period === "day") return { date: istDate() };
  if (period === "week") return { week_start: istMonday() };
  if (period === "range") return { start_date: istDaysAgo(6), end_date: istDate() };
  if (period === "mission") return { mission_id: "" };
  if (period === "case") return { case_id: "" };
  return {};
};

const STATUS_STYLE = {
  requested: "text-sky-300 border-sky-500/40 bg-sky-500/10",
  queued: "text-amber-300 border-amber-500/40 bg-amber-500/10",
  running: "text-amber-300 border-amber-500/40 bg-amber-500/10",
  ready: "text-emerald-400 border-emerald-500/40 bg-emerald-500/10",
  failed: "text-red-400 border-red-500/40 bg-red-500/10",
  rejected: "text-slate-400 border-slate-500/40 bg-slate-500/10",
  expired: "text-slate-500 border-slate-600/40 bg-slate-700/10",      // PDF deleted by retention
};

const input = "bg-slate-800 border border-slate-700 text-xs px-2 py-1 rounded text-slate-200";

function ParamFields({ spec, value, onChange, missions, cases }) {
  const set = (k) => (e) => onChange({ ...value, [k]: e.target.value });
  return (
    <div className="flex flex-wrap items-center gap-2">
      {spec.period === "day" && (<><label className="text-[10px] text-slate-400">Date (IST)</label>
        <input type="date" value={value.date || ""} onChange={set("date")} className={input} /></>)}
      {spec.period === "week" && (<><label className="text-[10px] text-slate-400">Week starting (IST)</label>
        <input type="date" value={value.week_start || ""} onChange={set("week_start")} className={input} /></>)}
      {spec.period === "range" && (<>
        <label className="text-[10px] text-slate-400">From</label>
        <input type="date" value={value.start_date || ""} onChange={set("start_date")} className={input} />
        <label className="text-[10px] text-slate-400">to (IST, inclusive)</label>
        <input type="date" value={value.end_date || ""} onChange={set("end_date")} className={input} />
      </>)}
      {spec.period === "mission" && (
        <select value={value.mission_id || ""} onChange={set("mission_id")} className={input}>
          <option value="">Select mission…</option>
          {missions.map((m) => <option key={m.mission_id} value={m.mission_id}>{m.name || m.mission_id} ({m.mission_id})</option>)}
        </select>)}
      {spec.period === "case" && (
        <select value={value.case_id || ""} onChange={set("case_id")} className={input}>
          <option value="">Select case…</option>
          {cases.map((c) => <option key={c.id} value={c.id}>{c.case_number}{c.title ? ` — ${c.title}` : ""}</option>)}
        </select>)}
      {["day", "week", "range"].includes(spec.period) && (<>
        <label className="text-[10px] text-slate-400">Drones (optional)</label>
        <input placeholder="DRN-001, DRN-002" value={value.drone_ids || ""} onChange={set("drone_ids")} className={`${input} w-40`} />
      </>)}
    </div>
  );
}

function PreviewDrawer({ preview, onClose }) {
  if (!preview) return null;
  const d = preview.data || {};
  const counts = Object.entries(d)
    .filter(([, v]) => Array.isArray(v))
    .map(([k, v]) => [k.replace(/_/g, " "), v.length]);
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50" onClick={onClose}>
      <div className="w-full max-w-lg h-full overflow-y-auto p-4 space-y-3" style={{ background: "var(--dvas-surface)" }}
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <div className="text-sm font-semibold text-slate-100">Preview — {preview.title}</div>
          <button onClick={onClose} className="text-slate-400 hover:text-white"><X size={16} /></button>
        </div>
        <div className="text-[11px] text-slate-400">
          Same data the PDF will use. Period: {preview.params.start_day ? `${preview.params.start_day} → ${preview.params.end_day} (IST)` : "from the mission / case"} ·
          Scope: {preview.params.scope?.jurisdiction || "statewide"}
        </div>
        <table className="w-full text-xs">
          <tbody>
            {counts.map(([k, n]) => (
              <tr key={k}><td className="py-1 text-slate-400 capitalize">{k}</td><td className="py-1 text-right text-slate-100 mono">{n}</td></tr>
            ))}
          </tbody>
        </table>
        {d.analytics && (
          <div className="text-[11px] space-y-1">
            <div className="text-slate-300 font-medium">Analytics sections</div>
            {Object.entries(d.analytics).map(([k, v]) => (
              <div key={k} className={v?.error ? "text-amber-400" : "text-slate-400"}>{k}: {v?.error || "included"}</div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function ReportsPage() {
  const { user } = useAuth();
  const { missions } = useCommand();
  const isSenior = user?.role === "senior_officer";
  const [types, setTypes] = useState([]);
  const [params, setParams] = useState({});
  const [reports, setReports] = useState([]);
  const [cases, setCases] = useState([]);
  const [busy, setBusy] = useState(null);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState("");

  const loadReports = useCallback(async () => {
    try { setReports((await api.get("/reports")).data || []); } catch { /* keep last list */ }
  }, []);

  useEffect(() => {
    api.get("/reports/types").then((r) => {
      setTypes(r.data || []);
      setParams(Object.fromEntries((r.data || []).map((t) => [t.type, defaultsFor(t.period)])));
    }).catch(() => setTypes([]));
    api.get("/cases").then((r) => setCases(r.data || [])).catch(() => setCases([]));
    loadReports();
    const t = setInterval(loadReports, 60000);          // fallback; WebSocket events refresh sooner
    return () => clearInterval(t);
  }, [loadReports]);

  useDvasSocket((msg) => {
    if (msg.type === "report_ready" || msg.type === "report_failed") loadReports();
  });

  const clean = (raw) => {
    const out = { ...raw };
    if (typeof out.drone_ids === "string") out.drone_ids = out.drone_ids.split(",").map((s) => s.trim()).filter(Boolean);
    return out;
  };

  const submit = async (t) => {
    setBusy(t.type); setError("");
    try {
      await api.post("/reports/generate", { report_type: t.type, params: clean(params[t.type] || {}) });
      await loadReports();
    } catch (e) {
      setError(e.response?.data?.detail || "Could not create the report");
    } finally { setBusy(null); }
  };

  const showPreview = async (t) => {
    setError("");
    try {
      const p = clean(params[t.type] || {});
      const qs = new URLSearchParams(Object.entries(p).filter(([, v]) => v && (!Array.isArray(v) || v.length))
        .map(([k, v]) => [k, Array.isArray(v) ? v.join(",") : v])).toString();
      setPreview((await api.get(`/reports/${t.type}/preview-data?${qs}`)).data);
    } catch (e) {
      setError(e.response?.data?.detail || "Preview failed");
    }
  };

  const decide = async (r, decision) => {
    try { await api.post(`/reports/${r.id}/decision`, { decision }); loadReports(); }
    catch (e) { setError(e.response?.data?.detail || "Failed"); }
  };

  const pendingRequests = useMemo(() => reports.filter((r) => r.status === "requested"), [reports]);

  if (!["senior_officer", "control_room_officer"].includes(user?.role)) {
    return <div className="text-sm text-slate-400">Reports are available to Control Room and Senior Officers.</div>;
  }

  return (
    <div className="space-y-4">
      {error && <div className="text-xs text-red-400 border border-red-500/30 bg-red-500/5 rounded p-2">{error}</div>}

      {isSenior && pendingRequests.length > 0 && (
        <div className="rounded-lg p-4 border border-sky-500/30 bg-sky-500/5 space-y-2">
          <div className="text-sm font-semibold text-sky-300">Report requests awaiting your approval</div>
          {pendingRequests.map((r) => (
            <div key={r.id} className="flex items-center justify-between text-xs">
              <span className="text-slate-200">{r.title} <span className="text-slate-500">· {r.requested_by}</span></span>
              <span className="flex gap-2">
                <button onClick={() => decide(r, "approved")} className="px-2 py-1 border border-emerald-600/60 text-emerald-300 rounded flex items-center gap-1"><CheckCircle size={11} /> Approve</button>
                <button onClick={() => decide(r, "rejected")} className="px-2 py-1 border border-red-600/60 text-red-300 rounded flex items-center gap-1"><XCircle size={11} /> Reject</button>
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="rounded-lg p-4 space-y-3" style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}>
        <div className="flex items-center gap-2 text-sm font-semibold" style={{ color: "var(--dvas-text)" }}>
          <FileText size={16} className="text-sky-500" /> {isSenior ? "Generate a report" : "Request a report"}
        </div>
        {!isSenior && <div className="text-[11px] text-slate-400">Requests go to a Senior Officer; once approved the report is generated for your jurisdiction and appears below.</div>}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {types.map((t) => (
            <div key={t.type} className="border border-slate-800 rounded-lg p-3 space-y-2" style={{ background: "var(--dvas-surface-2)" }}>
              <div className="text-sm font-medium text-slate-100">{t.title}</div>
              <div className="text-[11px] text-slate-400">{t.description}</div>
              <ParamFields spec={t} value={params[t.type] || {}} missions={missions || []} cases={cases}
                onChange={(v) => setParams((p) => ({ ...p, [t.type]: v }))} />
              <div className="flex justify-end gap-2">
                <button onClick={() => showPreview(t)} className="px-2 py-1 border border-slate-600 text-slate-300 text-xs rounded flex items-center gap-1"><Eye size={11} /> Preview</button>
                <button onClick={() => submit(t)} disabled={busy === t.type}
                  className="px-3 py-1 border border-sky-600/60 text-sky-300 hover:bg-sky-500/10 text-xs rounded flex items-center gap-1 disabled:opacity-40">
                  {busy === t.type ? <RefreshCw size={11} className="animate-spin" /> : t.mode === "generate" ? <Download size={11} /> : <Send size={11} />}
                  {t.mode === "generate" ? "Generate" : "Request"}
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="rounded-lg overflow-hidden" style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-slate-400">
              <th className="px-3 py-2">Report</th><th className="px-3 py-2">Requested</th>
              <th className="px-3 py-2">Status</th><th className="px-3 py-2">SHA-256</th><th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {reports.length === 0 && <tr><td colSpan={5} className="px-3 py-4 text-slate-500">No reports yet.</td></tr>}
            {reports.map((r) => (
              <tr key={r.id} className="border-t border-slate-800/60">
                <td className="px-3 py-2 text-slate-200">{r.title}<div className="text-[10px] text-slate-500 mono">{r.id}</div></td>
                <td className="px-3 py-2 text-slate-400">{fmtIST(r.requested_at)}<div className="text-[10px]">{r.requested_by}</div></td>
                <td className="px-3 py-2">
                  <span className={`px-1.5 py-0.5 border rounded text-[10px] ${STATUS_STYLE[r.status] || ""}`}>
                    {["queued", "running"].includes(r.status) && <RefreshCw size={9} className="inline animate-spin mr-1" />}{r.status}
                  </span>
                  {["failed", "expired"].includes(r.status) && r.error_message && (
                    <div className={`text-[10px] mt-1 ${r.status === "failed" ? "text-red-400" : "text-slate-500"}`}>{r.error_message}</div>
                  )}
                </td>
                <td className="px-3 py-2 mono text-[10px] text-slate-500" title={r.sha256 || ""}>{r.sha256 ? `${r.sha256.slice(0, 16)}…` : "—"}</td>
                <td className="px-3 py-2 text-right">
                  {r.status === "ready" && (
                    <button onClick={() => downloadSigned("report", r.id).catch((e) => setError(apiError(e, "Download failed")))}
                      className="px-2 py-1 border border-emerald-600/60 text-emerald-300 rounded text-[10px] inline-flex items-center gap-1">
                      <Download size={11} /> PDF
                    </button>)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <PreviewDrawer preview={preview} onClose={() => setPreview(null)} />
    </div>
  );
}
