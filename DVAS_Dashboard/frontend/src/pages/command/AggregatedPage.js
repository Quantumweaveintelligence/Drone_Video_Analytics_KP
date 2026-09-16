import React from "react";
import { useCommand } from "@/context/CommandContext";

export default function AggregatedPage() {
  const { kpis } = useCommand();

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[
          ["Incidents · state", kpis.alerts_24h, "text-amber-400"],
          ["Active missions", kpis.active_missions, "text-sky-400"],
          ["Drones online", kpis.drones_online, "text-emerald-400"],
          ["Evidence retained", kpis.evidence_total, "text-slate-100"]
        ].map(([l, v, t]) => (
          <div
            key={l}
            className="rounded-lg p-4"
            style={{
              background: "var(--dvas-surface)",
              border: "1px solid var(--dvas-border)",
              boxShadow: "var(--dvas-shadow)"
            }}
          >
            <div className="dvas-label text-[10px] font-bold uppercase tracking-wider">{l}</div>
            <div className={`mono text-3xl font-extrabold mt-1 ${t}`}>{v ?? "—"}</div>
          </div>
        ))}

        <div
          className="col-span-2 md:col-span-4 rounded-lg p-4 mono text-xs text-slate-400"
          style={{
            background: "var(--dvas-surface)",
            border: "1px solid var(--dvas-border)"
          }}
        >
          ABAC prevents drill-down into any single district's active operational controls outside your assigned scope. This view is aggregate-only across all Kerala Police districts.
        </div>
      </div>
    </div>
  );
}
