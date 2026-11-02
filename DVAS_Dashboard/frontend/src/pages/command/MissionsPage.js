import React, { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useCommand } from "@/context/CommandContext";
import { useAuth } from "@/context/AuthContext";
import {
  ClipboardList,
  Search,
  Download,
  RefreshCw,
  Clock,
  User,
  Radio,
  MapPin
} from "lucide-react";

export default function MissionsPage() {
  const { missions, generateMissionReport, generatingReport } = useCommand();
  const { user } = useAuth();
  const isSenior = user?.role === "senior_officer";
  const [searchParams] = useSearchParams();
  const [filter, setFilter] = useState(() => searchParams.get("q") || "");

  const filtered = (missions || []).filter(m => {
    if (!filter) return true;
    const term = filter.toLowerCase();
    return (
      (m.mission_id && m.mission_id.toLowerCase().includes(term)) ||
      (m.name && m.name.toLowerCase().includes(term)) ||
      (m.operator_name && m.operator_name.toLowerCase().includes(term)) ||
      (m.location && m.location.toLowerCase().includes(term))
    );
  });

  return (
    <div className="space-y-4">
      <div
        className="rounded-lg overflow-hidden"
        style={{
          background: "var(--dvas-surface)",
          border: "1px solid var(--dvas-border)",
          boxShadow: "var(--dvas-shadow)"
        }}
      >
        <div className="p-3.5 border-b flex flex-wrap items-center justify-between gap-3" style={{ borderColor: "var(--dvas-border)" }}>
          <div className="flex items-center gap-2 text-sm font-semibold" style={{ color: "var(--dvas-text)" }}>
            <ClipboardList size={16} className="text-sky-500" />
            <span>Case & Mission Ledger</span>
            <span className="mono text-[10px] text-slate-500 ml-2">
              {filtered.length} missions
            </span>
          </div>

          <div className="relative w-64">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
            <input
              type="text"
              placeholder="Search case / mission…"
              value={filter}
              onChange={e => setFilter(e.target.value)}
              className="w-full pl-8 pr-3 py-1.5 bg-slate-900 border border-slate-700 rounded-md text-xs text-slate-200 outline-none focus:border-sky-500"
            />
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead style={{ background: "var(--dvas-surface-2)" }}>
              <tr>
                {["Mission ID", "Name / Case", "Status", "Operator", "Drone ID", "Jurisdiction", "Start Time", "Actions"].map(h => (
                  <th key={h} className="text-left px-3.5 py-2.5 dvas-label" style={{ fontSize: 10, borderBottom: "1px solid var(--dvas-border)", color: "var(--dvas-text-dim)" }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="mono">
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-3.5 py-8 text-center text-slate-500">
                    No missions found matching search criteria
                  </td>
                </tr>
              )}
              {filtered.map(m => (
                <tr key={m.mission_id} className="hover:bg-[color:var(--dvas-elev)] transition-colors">
                  <td className="px-3.5 py-2.5 border-b border-slate-800/60 font-semibold text-slate-300">
                    {m.mission_id}
                  </td>
                  <td className="px-3.5 py-2.5 border-b border-slate-800/60 text-slate-100 font-medium">
                    {m.name || "Tactical Surveillance"}
                  </td>
                  <td className="px-3.5 py-2.5 border-b border-slate-800/60">
                    <span className={`px-2 py-0.5 text-[10px] rounded border font-semibold ${
                      m.status === "active"
                        ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/30"
                        : m.status === "completed"
                        ? "bg-slate-800 text-slate-400 border-slate-700"
                        : "bg-sky-500/10 text-sky-400 border-sky-500/30"
                    }`}>
                      {m.status || "logged"}
                    </span>
                  </td>
                  <td className="px-3.5 py-2.5 border-b border-slate-800/60 text-slate-300">
                    <div className="flex items-center gap-1.5">
                      <User size={12} className="text-slate-500" />
                      <span>{m.operator_name || "—"}</span>
                    </div>
                  </td>
                  <td className="px-3.5 py-2.5 border-b border-slate-800/60 text-slate-400">
                    <div className="flex items-center gap-1.5">
                      <Radio size={12} className="text-slate-500" />
                      <span>{m.drone_id || "—"}</span>
                    </div>
                  </td>
                  <td className="px-3.5 py-2.5 border-b border-slate-800/60 text-slate-400">
                    <div className="flex items-center gap-1.5">
                      <MapPin size={12} className="text-slate-500" />
                      <span>{m.location || m.jurisdiction || "Kerala"}</span>
                    </div>
                  </td>
                  <td className="px-3.5 py-2.5 border-b border-slate-800/60 text-slate-500">
                    <div className="flex items-center gap-1">
                      <Clock size={11} />
                      <span>{m.start_time ? new Date(m.start_time).toLocaleString() : "—"}</span>
                    </div>
                  </td>
                  <td className="px-3.5 py-2.5 border-b border-slate-800/60">
                    <button
                      onClick={() => generateMissionReport(m.mission_id)}
                      disabled={generatingReport === m.mission_id}
                      className="px-2.5 py-1 border border-sky-600/60 text-sky-300 hover:bg-sky-500/10 flex items-center gap-1 text-[10px] rounded transition-colors disabled:opacity-40"
                    >
                      {generatingReport === m.mission_id ? (
                        <><RefreshCw size={10} className="animate-spin" /> Sending...</>
                      ) : (
                        <><Download size={10} /> {isSenior ? "AAR Report" : "Request AAR"}</>
                      )}
                    </button>
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
