import React from "react";
import { useCommand } from "@/context/CommandContext";
import { useAuth } from "@/context/AuthContext";
import { API } from "@/lib/api";
import { ExternalLink, FileText, Download } from "lucide-react";

export default function ExportsPage() {
  const { user } = useAuth();
  const { exports, reports, decide, downloadReport } = useCommand();

  return (
    <div className="space-y-5">
      {/* Evidence Export Requests */}
      <div
        className="rounded-lg overflow-hidden"
        style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)", boxShadow: "var(--dvas-shadow)" }}
      >
        <div className="p-3.5 text-sm flex items-center gap-2 font-semibold" style={{ borderBottom: "1px solid var(--dvas-border)", color: "var(--dvas-text)" }}>
          <ExternalLink size={16} className="text-sky-500" /> Evidence Export Requests
          <span className="ml-auto mono text-[10px] text-slate-500">{exports.length} requests</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead style={{ background: "var(--dvas-surface-2)" }}>
              <tr>
                {["ID", "Evidence", "Requester", "Justification", "Status", "Actions"].map(h => (
                  <th key={h} className="text-left px-3 py-2.5 dvas-label" style={{ fontSize: 10, borderBottom: "1px solid var(--dvas-border)", color: "var(--dvas-text-dim)" }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="mono">
              {exports.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-3 py-8 text-center text-slate-500">
                    No export requests in scope
                  </td>
                </tr>
              )}
              {exports.map(x => (
                <tr key={x.id} data-testid={`export-${x.id}`} className="hover:bg-[color:var(--dvas-elev)] transition-colors">
                  <td className="px-3 py-2.5 border-b border-slate-800/60">{x.id}</td>
                  <td className="px-3 py-2.5 border-b border-slate-800/60 font-medium text-slate-100">{x.evidence_label}</td>
                  <td className="px-3 py-2.5 border-b border-slate-800/60 text-slate-300">{x.requested_by}</td>
                  <td className="px-3 py-2.5 border-b border-slate-800/60 text-slate-400 max-w-[280px] truncate">{x.justification}</td>
                  <td className="px-3 py-2.5 border-b border-slate-800/60">
                    <span className={`px-1.5 py-0.5 border text-[10px] rounded ${
                      x.status === "pending" ? "text-amber-400 border-amber-500/40 bg-amber-500/10"
                      : x.status === "approved" ? "text-emerald-400 border-emerald-500/40 bg-emerald-500/10"
                      : "text-red-400 border-red-500/40 bg-red-500/10"
                    }`}>
                      {x.status}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 border-b border-slate-800/60">
                    {user?.role === "senior_officer" && x.status === "pending" && (
                      <div className="flex gap-1">
                        <button
                          data-testid={`approve-${x.id}`}
                          onClick={() => decide(x.id, "approved")}
                          className="px-2 py-1 border border-emerald-600/60 text-emerald-300 hover:bg-emerald-500/10 rounded text-[10px]"
                        >
                          Approve
                        </button>
                        <button
                          data-testid={`reject-${x.id}`}
                          onClick={() => decide(x.id, "rejected")}
                          className="px-2 py-1 border border-red-600/60 text-red-300 hover:bg-red-500/10 rounded text-[10px]"
                        >
                          Reject
                        </button>
                      </div>
                    )}
                    {x.status === "approved" && ["control_room_officer", "senior_officer"].includes(user?.role) && (
                      <a
                        data-testid={`bundle-${x.id}`}
                        href={`${API}/exports/${x.id}/bundle?token=${encodeURIComponent(localStorage.getItem("dvas_token") || "")}`}
                        className="px-2 py-1 border border-sky-600/60 text-sky-300 hover:bg-sky-500/10 inline-flex items-center gap-1 rounded text-[10px]"
                      >
                        <ExternalLink size={12} /> 65B Bundle
                      </a>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Generated Reports Downloads */}
      <div
        className="rounded-lg overflow-hidden"
        style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)", boxShadow: "var(--dvas-shadow)" }}
      >
        <div className="p-3.5 text-sm flex items-center gap-2 font-semibold" style={{ borderBottom: "1px solid var(--dvas-border)", color: "var(--dvas-text)" }}>
          <FileText size={16} className="text-emerald-500" /> Report Downloads
          <span className="ml-auto mono text-[10px] text-slate-500">
            {(reports || []).filter(r => r.status === "ready").length} ready
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead style={{ background: "var(--dvas-surface-2)" }}>
              <tr>
                {["ID", "Type", "Title", "Generated", "Size", "Download"].map(h => (
                  <th key={h} className="text-left px-3 py-2.5 dvas-label" style={{ fontSize: 10, borderBottom: "1px solid var(--dvas-border)", color: "var(--dvas-text-dim)" }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="mono">
              {(reports || []).filter(r => r.status === "ready").length === 0 && (
                <tr>
                  <td colSpan={6} className="px-3 py-8 text-center text-slate-500">
                    No reports available for download — generate reports from the Reports tab
                  </td>
                </tr>
              )}
              {(reports || []).filter(r => r.status === "ready").map(r => (
                <tr key={r.id} className="hover:bg-[color:var(--dvas-elev)] transition-colors">
                  <td className="px-3 py-2.5 border-b border-slate-800/60 text-slate-300">{r.id}</td>
                  <td className="px-3 py-2.5 border-b border-slate-800/60 text-slate-400">{(r.report_type || "").replace(/_/g, " ")}</td>
                  <td className="px-3 py-2.5 border-b border-slate-800/60 text-slate-200 max-w-[200px] truncate">{r.title}</td>
                  <td className="px-3 py-2.5 border-b border-slate-800/60 text-slate-500">{r.generated_at ? new Date(r.generated_at).toLocaleString() : "—"}</td>
                  <td className="px-3 py-2.5 border-b border-slate-800/60 text-slate-500">{r.file_size_bytes ? `${(r.file_size_bytes / 1024).toFixed(1)} KB` : "—"}</td>
                  <td className="px-3 py-2.5 border-b border-slate-800/60">
                    {user?.role === "senior_officer" ? (
                      <button
                        onClick={() => downloadReport(r.id)}
                        className="px-2 py-1 border border-sky-600/60 text-sky-300 hover:bg-sky-500/10 flex items-center gap-1 text-[10px] rounded"
                      >
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
    </div>
  );
}
