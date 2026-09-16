import React from "react";
import { useCommand } from "@/context/CommandContext";
import { useAuth } from "@/context/AuthContext";
import {
  FileText,
  Shield,
  Bell,
  Clock,
  Activity,
  Download,
  RefreshCw,
  CheckCircle
} from "lucide-react";

export default function ReportsPage() {
  const { user } = useAuth();
  const {
    reports,
    missions,
    generatingReport,
    reportParams,
    setReportParams,
    generateReport,
    downloadReport
  } = useCommand();

  return (
    <div className="space-y-4">
      {user?.role !== "senior_officer" && (
        <div
          className="border border-amber-500/30 bg-amber-500/5 rounded-lg p-4"
          style={{ boxShadow: "var(--dvas-shadow)" }}
        >
          <div className="flex items-center gap-2 text-sm text-amber-400 mb-2 font-semibold">
            <Shield size={16} /> Report generation is restricted to Senior Officers
          </div>
          <div className="text-xs text-slate-400">
            Only Senior Officers can generate and download reports directly. Contact your Senior Officer to request a report.
          </div>
        </div>
      )}

      {user?.role === "senior_officer" && (
        <div
          className="rounded-lg p-4 space-y-4"
          style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)", boxShadow: "var(--dvas-shadow)" }}
        >
          <div className="flex items-center gap-2 text-sm font-semibold" style={{ color: "var(--dvas-text)" }}>
            <FileText size={16} className="text-sky-500" /> On-Demand Report Generator
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Daily Incident Summary */}
            <div className="border border-slate-800 rounded-lg p-4 space-y-3" style={{ background: "var(--dvas-surface-2)" }}>
              <div className="flex items-center gap-2">
                <Bell size={14} className="text-amber-400" />
                <span className="text-sm font-medium text-slate-100">Daily Incident Summary</span>
              </div>
              <div className="mono text-[10px] text-slate-500">
                All alerts, evidence items, and active missions for a specific date. Includes acknowledgement rates and type breakdown.
              </div>
              <div className="flex items-center gap-2">
                <label className="text-[10px] text-slate-400">Date:</label>
                <input
                  type="date"
                  value={reportParams.daily_incident.date}
                  onChange={e => setReportParams(p => ({ ...p, daily_incident: { date: e.target.value } }))}
                  className="bg-slate-800 border border-slate-700 text-xs px-2 py-1 rounded text-slate-200"
                />
              </div>
              <div className="flex items-center justify-between">
                <div className="mono text-[10px] text-slate-500">Watermarked · {user.email}</div>
                <button
                  onClick={() => generateReport("daily_incident")}
                  disabled={generatingReport === "daily_incident"}
                  className="px-3 py-1.5 border border-sky-600/60 text-sky-300 hover:bg-sky-500/10 text-xs flex items-center gap-1 disabled:opacity-40 rounded"
                >
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
              <div className="mono text-[10px] text-slate-500">
                Alert-to-acknowledgement response times, daily breakdown, per-type analysis, and mission hours for a 7-day period.
              </div>
              <div className="flex items-center gap-2">
                <label className="text-[10px] text-slate-400">Week start:</label>
                <input
                  type="date"
                  value={reportParams.weekly_response.week_start}
                  onChange={e => setReportParams(p => ({ ...p, weekly_response: { week_start: e.target.value } }))}
                  className="bg-slate-800 border border-slate-700 text-xs px-2 py-1 rounded text-slate-200"
                />
              </div>
              <div className="flex items-center justify-between">
                <div className="mono text-[10px] text-slate-500">Watermarked · {user.email}</div>
                <button
                  onClick={() => generateReport("weekly_response")}
                  disabled={generatingReport === "weekly_response"}
                  className="px-3 py-1.5 border border-sky-600/60 text-sky-300 hover:bg-sky-500/10 text-xs flex items-center gap-1 disabled:opacity-40 rounded"
                >
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
              <div className="mono text-[10px] text-slate-500">
                Fleet summary, per-drone mission counts and hours, average mission duration, and top operators ranked by flight time.
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-slate-400 italic">Period: last 30 days (automatic)</span>
              </div>
              <div className="flex items-center justify-between">
                <div className="mono text-[10px] text-slate-500">Watermarked · {user.email}</div>
                <button
                  onClick={() => generateReport("drone_utilisation")}
                  disabled={generatingReport === "drone_utilisation"}
                  className="px-3 py-1.5 border border-sky-600/60 text-sky-300 hover:bg-sky-500/10 text-xs flex items-center gap-1 disabled:opacity-40 rounded"
                >
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
              <div className="mono text-[10px] text-slate-500">
                Full mission debrief: objective, timeline, all alerts, evidence captured, acknowledgement rates, and duration analysis.
              </div>
              <div className="flex items-center gap-2">
                <label className="text-[10px] text-slate-400">Mission:</label>
                <select
                  value={reportParams.mission_after_action.mission_id}
                  onChange={e => setReportParams(p => ({ ...p, mission_after_action: { mission_id: e.target.value } }))}
                  className="bg-slate-800 border border-slate-700 text-xs px-2 py-1 rounded text-slate-200 flex-1"
                >
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
                <button
                  onClick={() => generateReport("mission_after_action")}
                  disabled={generatingReport === "mission_after_action" || !reportParams.mission_after_action.mission_id}
                  className="px-3 py-1.5 border border-sky-600/60 text-sky-300 hover:bg-sky-500/10 text-xs flex items-center gap-1 disabled:opacity-40 rounded"
                >
                  {generatingReport === "mission_after_action" ? <><RefreshCw size={11} className="animate-spin" /> Generating...</> : <><Download size={11} /> Generate</>}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Generated Reports Table */}
      <div
        className="rounded-lg overflow-hidden"
        style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)", boxShadow: "var(--dvas-shadow)" }}
      >
        <div className="p-3.5 text-sm flex items-center gap-2 font-semibold" style={{ borderBottom: "1px solid var(--dvas-border)", color: "var(--dvas-text)" }}>
          <FileText size={16} className="text-emerald-500" /> Generated Reports
          <span className="ml-auto mono text-[10px] text-slate-500">{reports.length} reports</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead style={{ background: "var(--dvas-surface-2)" }}>
              <tr>
                {["ID", "Type", "Title", "Status", "Generated", "Size", "Actions"].map(h => (
                  <th key={h} className="text-left px-3 py-2.5 dvas-label" style={{ fontSize: 10, borderBottom: "1px solid var(--dvas-border)", color: "var(--dvas-text-dim)" }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="mono">
              {reports.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-3 py-8 text-center text-slate-500">
                    No reports generated yet — use the cards above to generate your first report
                  </td>
                </tr>
              )}
              {(Array.isArray(reports) ? reports : []).map(r => (
                <tr key={r.id} className="hover:bg-[color:var(--dvas-elev)] transition-colors">
                  <td className="px-3 py-2.5 border-b border-slate-800/60 text-slate-300">{r.id}</td>
                  <td className="px-3 py-2.5 border-b border-slate-800/60 text-slate-400">{(r.report_type || "").replace(/_/g, " ")}</td>
                  <td className="px-3 py-2.5 border-b border-slate-800/60 text-slate-200 max-w-[200px] truncate">{r.title}</td>
                  <td className="px-3 py-2.5 border-b border-slate-800/60">
                    <span className={`px-1.5 py-0.5 border text-[10px] rounded ${
                      r.status === "generating" ? "text-amber-400 border-amber-500/40 bg-amber-500/10" :
                      r.status === "ready" ? "text-emerald-400 border-emerald-500/40 bg-emerald-500/10" :
                      "text-red-400 border-red-500/40 bg-red-500/10"
                    }`}>
                      {r.status === "generating" && <RefreshCw size={9} className="inline animate-spin mr-1" />}
                      {r.status === "ready" && <CheckCircle size={9} className="inline mr-1" />}
                      {r.status}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 border-b border-slate-800/60 text-slate-500">{r.generated_at ? new Date(r.generated_at).toLocaleString() : "—"}</td>
                  <td className="px-3 py-2.5 border-b border-slate-800/60 text-slate-500">{r.file_size_bytes ? `${(r.file_size_bytes / 1024).toFixed(1)} KB` : "—"}</td>
                  <td className="px-3 py-2.5 border-b border-slate-800/60">
                    {r.status === "ready" && user?.role === "senior_officer" && (
                      <button
                        onClick={() => downloadReport(r.id)}
                        className="px-2 py-1 border border-sky-600/60 text-sky-300 hover:bg-sky-500/10 flex items-center gap-1 text-[10px] rounded"
                      >
                        <Download size={10} /> Download PDF
                      </button>
                    )}
                    {r.status === "ready" && user?.role !== "senior_officer" && (
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
    </div>
  );
}
