import React, { createContext, useContext, useState, useEffect, useRef } from "react";
import { api, apiError, downloadSigned } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { useDvasSocket } from "@/lib/useDvasSocket";
import { useActiveStreams } from "@/hooks/useActiveStreams";

const CommandContext = createContext(null);

// F3: older rows stored incidents/suggestions a second time as INC-* / SUGG-* alerts;
// they are listed under Incidents / Suggestions, never as detections.
const isCopiedAlert = (a) => /^(INC|SUGG)-/.test(String(a?.id || ""));

export function CommandProvider({ children }) {
  const { user } = useAuth();

  const [kpis, setKpis] = useState({});
  const [drones, setDrones] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [incidents, setIncidents] = useState([]);
  const [suggestions, setSuggestions] = useState([]);
  const [alertsSubTab, setAlertsSubTab] = useState("all");
  const [evidence, setEvidence] = useState([]);
  const [missions, setMissions] = useState([]);
  const [exports, setExports] = useState([]);
  const [playing, setPlaying] = useState(null);
  // U1: one modal for every evidence action ({ kind, evidence }), see components/evidence/ActionModal.js
  const [action, setAction] = useState(null);
  const openAction = (kind, evidence) => setAction({ kind, evidence });
  const [selectedDrone, setSelectedDrone] = useState(null);

  const [evidenceCategory, setEvidenceCategory] = useState("all");
  const [evidenceDedup, setEvidenceDedup] = useState(true);
  const [evidenceFilters, setEvidenceFilters] = useState({
    category: "all",
    dedup: true,
    drone_id: "",
    mission_id: "",
    event_type: "",
    from_ts: "",
    to_ts: "",
    legal_hold_only: false,
    search: ""
  });
  const [evidenceTotal, setEvidenceTotal] = useState(0);
  const [evidenceMeta, setEvidenceMeta] = useState({ drones: [], missions: [], event_types: [], counts: {} });
  const [loadingMoreEvidence, setLoadingMoreEvidence] = useState(false);
  const [evidenceDetail, setEvidenceDetail] = useState(null);
  const [reports, setReports] = useState([]);
  const [generatingReport, setGeneratingReport] = useState(null);   // mission id whose AAR is being built
  const generatingReportIdRef = useRef(null);

  const evidenceFiltersRef = useRef(evidenceFilters);
  evidenceFiltersRef.current = evidenceFilters;
  const evidenceCategoryRef = useRef(evidenceCategory);
  evidenceCategoryRef.current = evidenceCategory;
  const evidenceDedupRef = useRef(evidenceDedup);
  evidenceDedupRef.current = evidenceDedup;

  const { mergeWithDrones } = useActiveStreams();
  const mergedDrones = mergeWithDrones(drones);
  // mergeWithDrones marks a drone without an ingest stream offline (A5), so a drained or
  // purged drone no longer counts here
  const liveMissionsCount = mergedDrones.filter((d) => ["live", "draining"].includes(d.status) && Boolean(d.mission_id)).length;

  const load = async () => {
    try {
      const [k, dR, aR, mR, xR, rptR, incR, sugR, metaR] = await Promise.all([
        api.get("/command/kpis").catch(() => ({ data: {} })),
        api.get("/drones").catch(() => ({ data: [] })),
        api.get("/alerts").catch(() => ({ data: [] })),
        api.get("/missions").catch(() => ({ data: [] })),
        api.get("/exports").catch(() => ({ data: [] })),
        api.get("/reports").catch(() => ({ data: [] })),
        api.get("/incidents").catch(() => ({ data: [] })),
        api.get("/analytics/suggestions").catch(() => ({ data: [] })),
        api.get("/evidence/meta").catch(() => ({ data: { drones: [], missions: [], event_types: [], counts: {} } })),
      ]);
      if (k?.data) setKpis(k.data);
      if (dR?.data) setDrones(dR.data);
      if (Array.isArray(aR?.data)) setAlerts(aR.data.filter((a) => !isCopiedAlert(a)));
      if (incR?.data && Array.isArray(incR.data)) setIncidents(incR.data);
      if (sugR?.data && Array.isArray(sugR.data)) setSuggestions(sugR.data);
      if (metaR?.data) setEvidenceMeta(metaR.data);
      if (mR?.data) setMissions(mR.data);
      if (xR?.data) setExports(xR.data);
      if (rptR?.data) setReports(rptR.data || []);
    } catch (e) {
      console.error("CommandContext poll error:", e);
    }
  };

  const loadFilteredEvidence = async (filters, cat, isDedup) => {
    try {
      const activeFilters = filters ?? evidenceFiltersRef.current;
      const activeCat = cat ?? evidenceCategoryRef.current;
      const activeDedup = isDedup ?? evidenceDedupRef.current;
      const params = new URLSearchParams();
      const merged = { ...activeFilters, category: activeCat, dedup: activeDedup };
      Object.entries(merged).forEach(([k, v]) => {
        if (v !== "" && v !== false && v !== undefined && v !== null) params.set(k, v);
      });
      params.set("limit", "60");
      params.set("offset", "0");
      const res = await api.get(`/evidence?${params.toString()}`);
      const data = res.data;
      setEvidence(Array.isArray(data) ? data : (data.items || []));
      setEvidenceTotal(data.total || 0);
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    load();
    loadFilteredEvidence(evidenceFiltersRef.current, evidenceCategoryRef.current, evidenceDedupRef.current);
    const t = setInterval(() => { if (!document.hidden) load(); }, 60000);   // fallback; WebSocket events refresh sooner
    return () => clearInterval(t);
  }, []);

  // WebSocket live updates singleton for Command section
  const { connected } = useDvasSocket((msg) => {
    if (msg.type === "telemetry" && Array.isArray(msg.drones)) {
      setDrones((prev) => {
        const existingIds = new Set(prev.map((d) => d.id));
        const updated = prev.map((d) => {
          const u = msg.drones.find((x) => x.id === d.id);
          return u ? { ...d, ...u } : d;
        });
        const brandNew = msg.drones.filter((x) => !existingIds.has(x.id));
        return brandNew.length > 0 ? [...updated, ...brandNew] : updated;
      });
    } else if (["stream_purged", "drone_offline", "mission_drained"].includes(msg.type || msg.event) && msg.drone_id
               && !msg.drone_still_active) {   // an older mission drained; the drone flies a newer one
      // A5: the stream is gone (purged by an officer, drained by ingest, or timed out):
      // the drone is offline now, not after the next /drones reload
      setDrones((prev) => prev.map((d) => (d.id === msg.drone_id ? { ...d, status: "offline", messages_ready: 0 } : d)));
    } else if (msg.type === "alert" && msg.alert) {
      // F4: the hub already filters live alerts per user (jurisdiction / mission scope)
      const a = msg.alert;
      if (!isCopiedAlert(a)) setAlerts((prev) => [a, ...prev.filter((x) => x.id !== a.id)].slice(0, 200));
    } else if (msg.type === "model_suggestion") {
      const s = msg.suggestion || msg;
      setSuggestions((prev) => [s, ...prev.filter(x => x.id !== s.id)]);
    } else if (msg.type === "incident" && msg.incident) {
      const inc = msg.incident;
      setIncidents((prev) => [inc, ...prev.filter(x => x.id !== inc.id)]);
    } else if (msg.type === "incident_ack") {
      setIncidents((prev) => prev.map(x => x.id === msg.incident_id ? { ...x, status: "ACKNOWLEDGED" } : x));
    } else if (msg.type === "model_suggestion_status") {
      setSuggestions((prev) => prev.map(x => x.id === msg.suggestion_id ? { ...x, status: msg.status } : x));
    } else if (msg.type === "incident_deleted") {
      // F2: deletes and clears made elsewhere disappear without a reload
      setIncidents((prev) => prev.filter((x) => x.id !== msg.incident_id));
    } else if (msg.type === "incidents_cleared") {
      const ids = msg.incident_ids;
      setIncidents((prev) => (ids ? prev.filter((x) => !ids.includes(x.id)) : []));
    } else if (msg.type === "model_suggestion_deleted") {
      setSuggestions((prev) => prev.filter((x) => x.id !== msg.suggestion_id));
    } else if (msg.type === "model_suggestions_cleared") {
      const ids = msg.suggestion_ids;
      setSuggestions((prev) => (ids ? prev.filter((x) => !ids.includes(x.id)) : []));
    } else if (["report_ready", "report_failed"].includes(msg.type) && msg.report_id === generatingReportIdRef.current) {
      // MON-29: the AAR spinner stops when the report job finishes
      generatingReportIdRef.current = null;
      setGeneratingReport(null);
      if (msg.type === "report_failed") alert(`Report failed: ${msg.error || "unknown error"}`);
      scheduleRefresh(false);
    } else if (["evidence_created", "export_decided", "report_ready", "report_failed"].includes(msg.type)) {
      scheduleRefresh(msg.type === "evidence_created");
    }
  });

  // WebSocket events refresh the lists (debounced); the poll below is only a fallback (U1)
  const refreshTimer = useRef(null);
  const scheduleRefresh = (withEvidence) => {
    if (refreshTimer.current) return;
    refreshTimer.current = setTimeout(() => {
      refreshTimer.current = null;
      load();
      if (withEvidence) loadFilteredEvidence();
    }, 1500);
  };

  // MON-28: failures (403, 500) are shown, not swallowed
  const ackAlert = async (id, disposition) => {
    try {
      await api.post(`/alerts/${encodeURIComponent(id)}/ack`, disposition ? { disposition } : undefined);
      load();
    } catch (e) { alert(apiError(e, "Could not acknowledge the alert")); }
  };

  const ackIncident = async (id) => {
    try {
      await api.post(`/incidents/${encodeURIComponent(id)}/ack`);
      setIncidents((prev) => prev.map(x => x.id === id ? { ...x, status: "ACKNOWLEDGED" } : x));
    } catch (e) { alert(apiError(e, "Could not acknowledge the incident")); }
  };

  const ackSuggestion = async (id) => {
    try {
      await api.post(`/analytics/suggestions/${encodeURIComponent(id)}/status`, { status: "ACKNOWLEDGED" });
      setSuggestions((prev) => prev.map(x => x.id === id ? { ...x, status: "ACKNOWLEDGED" } : x));
    } catch (e) { alert(apiError(e, "Could not acknowledge the suggestion")); }
  };

  const requestExport = (ev) => openAction("request_export", ev);

  const decide = async (id, decision) => {
    try {
      await api.post(`/exports/${id}/decision`, { decision, comment: `${decision} by ${user?.email}` });
      load();
    } catch (e) {
      alert(apiError(e, "Decision failed"));
    }
  };

  const loadMoreEvidence = async () => {
    if (loadingMoreEvidence || evidence.length >= evidenceTotal) return;
    setLoadingMoreEvidence(true);
    try {
      const params = new URLSearchParams();
      const merged = { ...evidenceFiltersRef.current, category: evidenceCategoryRef.current, dedup: evidenceDedupRef.current };
      Object.entries(merged).forEach(([k, v]) => {
        if (v !== "" && v !== false && v !== undefined && v !== null) params.set(k, v);
      });
      params.set("offset", String(evidence.length));
      params.set("limit", "60");
      const res = await api.get(`/evidence?${params.toString()}`);
      const data = res.data;
      const items = Array.isArray(data) ? data : (data.items || []);
      setEvidence(prev => [...prev, ...items]);
      setEvidenceTotal(data.total || 0);
    } catch (e) {
      console.error("Load more evidence failed:", e);
    } finally {
      setLoadingMoreEvidence(false);
    }
  };

  const handleCategoryChange = (cat) => {
    setEvidenceCategory(cat);
    evidenceCategoryRef.current = cat;
    const newFilters = { ...evidenceFiltersRef.current, category: cat };
    setEvidenceFilters(newFilters);
    evidenceFiltersRef.current = newFilters;
    loadFilteredEvidence(newFilters, cat, evidenceDedupRef.current);
  };

  const handleDedupToggle = (checked) => {
    setEvidenceDedup(checked);
    evidenceDedupRef.current = checked;
    const newFilters = { ...evidenceFiltersRef.current, dedup: checked };
    setEvidenceFilters(newFilters);
    evidenceFiltersRef.current = newFilters;
    loadFilteredEvidence(newFilters, evidenceCategoryRef.current, checked);
  };

  const toggleLegalHold = (ev) => openAction(ev.legal_hold ? "release_hold" : "legal_hold", ev);

  const directExport = (ev) => openAction("direct_export", ev);

  const generateMissionReport = async (missionId) => {
    try {
      setGeneratingReport(missionId);
      const { data } = await api.post("/reports/generate", { report_type: "mission_after_action", params: { mission_id: missionId } });
      generatingReportIdRef.current = data?.id || null;
      if (data?.status === "requested") alert("AAR requested. A senior officer must approve it before it is generated.");
      if (!data?.id || ["ready", "failed", "requested"].includes(data.status)) {
        // already finished, or waiting for approval: nothing to wait for here
        generatingReportIdRef.current = null;
        setGeneratingReport(null);
      }
      load();
    } catch (e) {
      alert(apiError(e, "Report request failed"));
      generatingReportIdRef.current = null;
      setGeneratingReport(null);
    }
  };

  const openEvidenceDetail = async (evidenceId) => {
    try {
      const res = await api.get(`/evidence/${evidenceId}`);
      setEvidenceDetail(res.data);
    } catch (e) {
      alert(apiError(e, "Could not open the evidence"));
    }
  };

  const downloadReport = (reportId) => {
    downloadSigned("report", reportId).catch((e) => alert(apiError(e, "Download failed")));
  };

  const value = {
    user,
    kpis,
    drones,
    alerts,
    incidents,
    suggestions,
    missions,
    exports,
    reports,
    evidence,
    evidenceTotal,
    evidenceFilters,
    setEvidenceFilters,
    evidenceCategory,
    evidenceDedup,
    evidenceMeta,
    selectedDrone,
    setSelectedDrone,
    alertsSubTab,
    setAlertsSubTab,
    playing,
    setPlaying,
    evidenceDetail,
    setEvidenceDetail,
    action,
    setAction,
    openAction,
    setRequestOn: (ev) => ev && openAction("request_export", ev),
    generatingReport,
    connected,
    mergedDrones,
    liveMissionsCount,
    load,
    loadFilteredEvidence,
    loadMoreEvidence,
    handleCategoryChange,
    handleDedupToggle,
    ackAlert,
    ackIncident,
    ackSuggestion,
    requestExport,
    decide,
    toggleLegalHold,
    directExport,
    generateMissionReport,
    openEvidenceDetail,
    downloadReport
  };

  return <CommandContext.Provider value={value}>{children}</CommandContext.Provider>;
}

export const useCommand = () => {
  const ctx = useContext(CommandContext);
  if (!ctx) throw new Error("useCommand must be used within CommandProvider");
  return ctx;
};
