import React, { useState } from "react";
import { useCommand } from "@/context/CommandContext";
import { useI18n } from "@/context/I18nContext";
import {
  Bell,
  Cpu,
  AlertTriangle,
  CheckCircle,
  CheckCircle2,
  Clock,
  Radio,
  MapPin,
  Send
} from "lucide-react";

export default function AlertsFeedPage() {
  const { t } = useI18n();
  const {
    alerts,
    incidents,
    suggestions,
    alertsSubTab,
    setAlertsSubTab,
    ackAlert,
    ackIncident,
    ackSuggestion
  } = useCommand();

  const normalizedSuggestions = suggestions.map((s) => {
    const modelList = (s.model_names && s.model_names.length > 0)
      ? s.model_names.join(", ")
      : (s.models || []).map(m => m.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())).join(", ") || "AI Model";
    const missionPrefix = s.mission_id || "Mission";
    return {
      id: s.id,
      category: "suggestion",
      title: `${missionPrefix}: Suggests ${modelList}`,
      subtitle: `${s.suggested_by || "Edge Operator"} · ${s.location || "Ground Station"}`,
      ts: s.timestamp || s.created_at,
      severity: s.priority === "critical" ? "critical" : s.priority === "high" ? "warning" : "info",
      badge: "AI SUGGESTION",
      badgeClass: "bg-sky-500/10 text-sky-400 border-sky-500/30",
      status: s.status || "PENDING",
      raw: s,
    };
  });

  const normalizedIncidents = incidents.map((inc) => {
    const missionPrefix = inc.mission_id ? `${inc.mission_id}: ` : inc.drone_id ? `${inc.drone_id}: ` : "";
    return {
      id: inc.id,
      category: "incident",
      title: `${missionPrefix}${inc.title}`,
      subtitle: `${inc.created_by || "Edge Operator"} · ${inc.location || "Ground Station"}`,
      ts: inc.timestamp,
      severity: inc.severity === "CRITICAL" ? "critical" : inc.severity === "HIGH" ? "warning" : "info",
      badge: inc.severity || "INCIDENT",
      badgeClass: inc.severity === "CRITICAL" ? "bg-red-500/10 text-red-400 border-red-500/30" : "bg-amber-500/10 text-amber-400 border-amber-500/30",
      status: inc.status || "OPEN",
      raw: inc,
    };
  });

  // R1: AI alerts carry a real severity; colour by it (critical / high / medium / low)
  const SEVERITY_BADGE = {
    critical: "bg-red-500/10 text-red-400 border-red-500/30",
    high: "bg-orange-500/10 text-orange-400 border-orange-500/30",
    medium: "bg-amber-500/10 text-amber-300 border-amber-500/30",
    low: "bg-sky-500/10 text-sky-300 border-sky-500/30",
  };
  const normalizedAlerts = alerts.map((a) => {
    const missionPrefix = a.mission_id ? `${a.mission_id}: ` : "";
    const alertName = a.title || (a.type ? a.type.replace(/_/g, " ").replace(/\b\w/g, l => l.toUpperCase()) : "Detection Alert");
    const sev = (a.severity || "medium").toLowerCase();
    return {
      id: a.id,
      category: "vision",
      title: `${missionPrefix}${alertName}`,
      subtitle: `${a.drone_id || "DRN"} · ${a.zone || "Zone"} · conf ${a.confidence ?? "—"}${a.evidence_id ? ` · ${a.evidence_id}` : ""}`,
      ts: a.ts,
      severity: sev === "critical" ? "critical" : sev === "high" ? "warning" : "info",
      badge: `${a.category === "ai" ? "AI" : "ALERT"} · ${sev.toUpperCase()}`,
      badgeClass: SEVERITY_BADGE[sev] || SEVERITY_BADGE.medium,
      status: a.acknowledged_by ? "ACKNOWLEDGED" : "OPEN",
      acknowledged_by: a.acknowledged_by,
      disposition: a.disposition,
      raw: a,
    };
  });
  const [disposition, setDisposition] = useState({});

  const allItems = [...normalizedSuggestions, ...normalizedIncidents, ...normalizedAlerts]
    .sort((a, b) => new Date(b.ts || 0).getTime() - new Date(a.ts || 0).getTime());

  const displayItems = allItems.filter((item) => {
    if (alertsSubTab === "all") return true;
    if (alertsSubTab === "suggestions") return item.category === "suggestion";
    if (alertsSubTab === "incidents") return item.category === "incident";
    if (alertsSubTab === "vision") return item.category === "vision";
    return true;
  });

  const subTabCounts = {
    all: allItems.length,
    suggestions: normalizedSuggestions.length,
    incidents: normalizedIncidents.length,
    vision: normalizedAlerts.length,
  };

  return (
    <div
      className="rounded-lg overflow-hidden flex flex-col"
      style={{
        background: "var(--dvas-surface)",
        border: "1px solid var(--dvas-border)",
        boxShadow: "var(--dvas-shadow)"
      }}
    >
      {/* ── Sub-Tab Filter Bar ── */}
      <div
        className="p-3.5 flex flex-wrap items-center justify-between gap-3 border-b"
        style={{ borderColor: "var(--dvas-border)" }}
      >
        <div className="flex items-center gap-1.5 flex-wrap">
          {[
            { key: "all", label: "All Feeds", icon: Bell },
            { key: "suggestions", label: "Model Suggestions", icon: Cpu },
            { key: "incidents", label: "Ground Incidents", icon: AlertTriangle },
            { key: "vision", label: "Vision Detections", icon: Radio },
          ].map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              data-testid={`alerts-tab-${key}`}
              onClick={() => setAlertsSubTab(key)}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-md transition-all ${
                alertsSubTab === key
                  ? "bg-dvas-accent text-white shadow-sm"
                  : "text-slate-400 hover:text-slate-200 hover:bg-[color:var(--dvas-elev)]"
              }`}
              style={alertsSubTab === key ? { backgroundColor: "var(--dvas-accent)" } : {}}
            >
              <Icon size={12} />
              <span>{label}</span>
              <span
                className={`ml-1 mono text-[10px] px-1.5 py-0.2 rounded-full font-bold ${
                  alertsSubTab === key
                    ? "bg-white/20 text-white"
                    : "bg-slate-800 text-slate-400"
                }`}
              >
                {subTabCounts[key]}
              </span>
            </button>
          ))}
        </div>

        <div className="mono text-[11px] text-slate-500">
          Showing {displayItems.length} of {allItems.length} alerts
        </div>
      </div>

      {/* ── Alerts List ── */}
      <div className="divide-y" style={{ borderColor: "var(--dvas-border)" }}>
        {displayItems.length === 0 && (
          <div className="p-12 text-center text-slate-500 mono text-xs">
            {t("cmd.noAlertsInScope")}
          </div>
        )}

        {displayItems.map((item) => (
          <div
            key={`${item.category}-${item.id}`}
            data-testid={`alert-item-${item.id}`}
            className="p-3.5 flex items-start gap-3 hover:bg-[color:var(--dvas-elev)] transition-colors"
          >
            <div className="mt-0.5 shrink-0">
              {item.category === "suggestion" ? (
                <div className="w-8 h-8 rounded-lg bg-sky-500/15 text-sky-400 border border-sky-500/30 flex items-center justify-center">
                  <Cpu size={16} />
                </div>
              ) : item.category === "incident" ? (
                <div className="w-8 h-8 rounded-lg bg-amber-500/15 text-amber-400 border border-amber-500/30 flex items-center justify-center">
                  <AlertTriangle size={16} />
                </div>
              ) : (
                <div className="w-8 h-8 rounded-lg bg-purple-500/15 text-purple-400 border border-purple-500/30 flex items-center justify-center">
                  <Radio size={16} />
                </div>
              )}
            </div>

            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap mb-1">
                <span className={`mono text-[9px] font-bold px-1.5 py-0.5 rounded border uppercase ${item.badgeClass}`}>
                  {item.badge}
                </span>
                <span className="text-xs font-bold leading-tight" style={{ color: "var(--dvas-text)" }}>
                  {item.title}
                </span>
                <span className="mono text-[10px] text-slate-500 ml-auto shrink-0 flex items-center gap-1">
                  <Clock size={10} />
                  {item.ts ? new Date(item.ts).toLocaleTimeString() : "—"}
                </span>
              </div>
              <div className="text-[11px] text-slate-400 flex items-center gap-2">
                <span>{item.subtitle}</span>
              </div>
            </div>

            <div className="shrink-0 flex items-center gap-2">
              {item.status === "ACKNOWLEDGED" ? (
                <span className="mono text-[10px] text-emerald-400 flex items-center gap-1 font-semibold px-2 py-1 rounded bg-emerald-500/10 border border-emerald-500/20">
                  <CheckCircle size={12} /> ACKNOWLEDGED{item.disposition ? ` · ${item.disposition.replace(/_/g, " ")}` : ""}
                </span>
              ) : (<>
                {item.category === "vision" && (
                  <select
                    aria-label="Disposition"
                    value={disposition[item.id] || ""}
                    onChange={(e) => setDisposition({ ...disposition, [item.id]: e.target.value })}
                    className="bg-slate-900 border border-slate-700 text-[11px] px-1.5 py-1 rounded text-slate-300"
                    title="What was it? Used for the Detection Quality report"
                  >
                    <option value="">Disposition…</option>
                    <option value="true_positive">True positive</option>
                    <option value="false_positive">False positive</option>
                    <option value="duplicate">Duplicate</option>
                    <option value="handled">Handled</option>
                  </select>
                )}
                <button
                  data-testid={`ack-${item.id}`}
                  onClick={() => {
                    if (item.category === "suggestion") ackSuggestion(item.id);
                    else if (item.category === "incident") ackIncident(item.id);
                    else ackAlert(item.id, disposition[item.id] || undefined);
                  }}
                  className="px-2.5 py-1 text-xs font-semibold rounded-md border border-slate-700 hover:border-slate-500 text-slate-300 hover:text-white transition-colors"
                >
                  Acknowledge
                </button>
              </>)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
