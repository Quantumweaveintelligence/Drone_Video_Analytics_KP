import React, { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { useCommand } from "@/context/CommandContext";
import { api, apiError } from "@/lib/api";
import { fmtIST } from "@/lib/ist";

// District & State view (senior officers only, P4). Totals and the per-district table
// come from the server (CMD-05); alerts are those of the last 24 hours.
export default function AggregatedPage() {
  const { kpis, user } = useCommand();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (user?.role !== "senior_officer") return;
    api.get("/command/districts")
      .then(({ data }) => setData(data))
      .catch((e) => setError(apiError(e, "Could not load the district view")));
  }, [user?.role]);

  if (user?.role !== "senior_officer") return <Navigate to="/command/tactical-map" replace />;

  const card = { background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)", boxShadow: "var(--dvas-shadow)" };
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[
          ["Alerts · state · 24 h", kpis.alerts_24h, "text-amber-400"],
          ["Active missions", kpis.active_missions, "text-sky-400"],
          ["Drones online", kpis.drones_online, "text-emerald-400"],
          ["Evidence retained", kpis.evidence_total, "text-slate-100"]
        ].map(([l, v, t]) => (
          <div key={l} className="rounded-lg p-4" style={card}>
            <div className="dvas-label text-[10px] font-bold uppercase tracking-wider">{l}</div>
            <div className={`mono text-3xl font-extrabold mt-1 ${t}`}>{v ?? "—"}</div>
          </div>
        ))}
      </div>

      <div className="rounded-lg" style={card}>
        <div className="p-3 border-b border-slate-800 text-sm flex items-center">
          By district
          {data && <span className="ml-auto mono text-[10px] text-slate-500">as of {fmtIST(data.generated_at)}</span>}
        </div>
        {error && <div className="p-3 mono text-[11px] text-red-400">{error}</div>}
        <table className="w-full text-xs mono">
          <thead>
            <tr>{["District", "Alerts (24 h)", "Active missions", "Drones live", "Evidence"].map((h) => (
              <th key={h} className="text-left px-3 py-2 dvas-label border-b border-slate-800" style={{ fontSize: 10 }}>{h}</th>
            ))}</tr>
          </thead>
          <tbody>
            {(data?.districts || []).map((d) => (
              <tr key={d.jurisdiction} className="hover:bg-slate-800/40">
                <td className="px-3 py-2 border-b border-slate-800/60 text-slate-100">{d.jurisdiction}</td>
                <td className="px-3 py-2 border-b border-slate-800/60 text-amber-400">{d.alerts_24h}</td>
                <td className="px-3 py-2 border-b border-slate-800/60 text-sky-400">{d.active_missions}</td>
                <td className="px-3 py-2 border-b border-slate-800/60 text-emerald-400">{d.drones_live}</td>
                <td className="px-3 py-2 border-b border-slate-800/60">{d.evidence}</td>
              </tr>
            ))}
            {data && data.districts.length === 0 && (
              <tr><td colSpan={5} className="p-6 text-center text-slate-500">No activity recorded.</td></tr>
            )}
          </tbody>
        </table>
        <div className="p-3 mono text-[10px] text-slate-500">
          Aggregate counts only; operational controls stay with each district's control room.
        </div>
      </div>
    </div>
  );
}
