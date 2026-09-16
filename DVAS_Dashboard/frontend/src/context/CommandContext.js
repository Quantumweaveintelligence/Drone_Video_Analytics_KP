import React, { createContext, useContext, useState, useEffect, useRef } from "react";
import { api, API } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { useDvasSocket } from "@/lib/useDvasSocket";
import { useActiveStreams } from "@/hooks/useActiveStreams";

const CommandContext = createContext(null);

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
  const [crowd, setCrowd] = useState([]);
  const [playing, setPlaying] = useState(null);
  const [requestOn, setRequestOn] = useState(null);
  const [justification, setJustification] = useState("");
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
  const [generatingReport, setGeneratingReport] = useState(null);
  const [reportParams, setReportParams] = useState({
    daily_incident: { date: new Date().toISOString().split("T")[0] },
    weekly_response: { week_start: new Date(Date.now() - 6 * 86400000).toISOString().split("T")[0] },
    drone_utilisation: { period_days: 30 },
    mission_after_action: { mission_id: "" },
  });

  const evidenceFiltersRef = useRef(evidenceFilters);
  evidenceFiltersRef.current = evidenceFilters;
  const evidenceCategoryRef = useRef(evidenceCategory);
  evidenceCategoryRef.current = evidenceCategory;
  const evidenceDedupRef = useRef(evidenceDedup);
  evidenceDedupRef.current = evidenceDedup;

  const { mergeWithDrones } = useActiveStreams();
  const mergedDrones = mergeWithDrones(drones);
  const liveMissionsCount = mergedDrones.filter((d) => ["live", "draining"].includes(d.status) && Boolean(d.mission_id)).length;

  const load = async () => {
    try {
      const [k, dR, aR, mR, xR, cR, rptR, incR, sugR, metaR] = await Promise.all([
        api.get("/command/kpis").catch(() => ({ data: {} })),
        api.get("/drones").catch(() => ({ data: [] })),
        api.get("/alerts").catch(() => ({ data: [] })),
        api.get("/missions").catch(() => ({ data: [] })),
        api.get("/exports").catch(() => ({ data: [] })),
        api.get("/analytics/crowd").catch(() => ({ data: { series: [] } })),
        api.get("/reports").catch(() => ({ data: [] })),
        api.get("/incidents").catch(() => ({ data: [] })),
        api.get("/analytics/suggestions").catch(() => ({ data: [] })),
        api.get("/evidence/meta").catch(() => ({ data: { drones: [], missions: [], event_types: [], counts: {} } })),
      ]);
      if (k?.data) setKpis(k.data);
      if (dR?.data) setDrones(dR.data);
      if (aR?.data) setAlerts(aR.data);
      if (incR?.data && Array.isArray(incR.data)) setIncidents(incR.data);
      if (sugR?.data && Array.isArray(sugR.data)) setSuggestions(sugR.data);
      if (metaR?.data) setEvidenceMeta(metaR.data);
      if (mR?.data) setMissions(mR.data);
      if (xR?.data) setExports(xR.data);
      if (cR?.data) setCrowd(cR.data.series || []);
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
    const t = setInterval(load, 5000);
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
    } else if (msg.type === "alert" && msg.alert) {
      const a = msg.alert;
      const inScope =
        user?.role === "senior_officer" ||
        user?.role === "control_room_officer" ||
        user?.role === "system_administrator" ||
        ((user?.mission_scope || []).includes(a.mission_id));
      if (inScope) setAlerts((prev) => [a, ...prev].slice(0, 200));
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
    }
  });

  const ackAlert = async (id) => {
    try { await api.post(`/alerts/${id}/ack`); load(); } catch (e) {}
  };

  const ackIncident = async (id) => {
    try {
      await api.post(`/incidents/${id}/ack`);
      setIncidents((prev) => prev.map(x => x.id === id ? { ...x, status: "ACKNOWLEDGED" } : x));
    } catch (e) {}
  };

  const ackSuggestion = async (id) => {
    try {
      await api.post(`/analytics/suggestions/${id}/status`, { status: "ACKNOWLEDGED" });
      setSuggestions((prev) => prev.map(x => x.id === id ? { ...x, status: "ACKNOWLEDGED" } : x));
    } catch (e) {}
  };

  const requestExport = async () => {
    if (!requestOn || !justification.trim()) return;
    try {
      await api.post("/exports", { evidence_id: requestOn.id, justification });
      setRequestOn(null);
      setJustification("");
      load();
    } catch (e) {
      alert(e.response?.data?.detail || "failed");
    }
  };

  const decide = async (id, decision) => {
    try {
      await api.post(`/exports/${id}/decision`, { decision, comment: `${decision} by ${user?.email}` });
      load();
    } catch (e) {
      alert(e.response?.data?.detail || "failed");
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

  const toggleLegalHold = async (ev) => {
    try {
      if (ev.legal_hold) {
        await api.delete(`/evidence/${ev.id}/legal-hold`, { data: { reason: "Released by Senior Officer" } });
      } else {
        const reason = prompt("Enter legal hold reason:");
        if (!reason) return;
        await api.post(`/evidence/${ev.id}/legal-hold`, { reason });
      }
      load();
    } catch (e) {
      alert(e.response?.data?.detail || "Failed");
    }
  };

  const directExport = async (ev) => {
    const just = prompt("Enter justification for direct export:");
    if (!just) return;
    try {
      await api.post(`/evidence/${ev.id}/direct-export`, { justification: just });
      load();
    } catch (e) {
      alert(e.response?.data?.detail || "Failed");
    }
  };

  const generateMissionReport = async (missionId) => {
    try {
      setGeneratingReport(missionId);
      await api.post("/reports/generate", { report_type: "mission_after_action", params: { mission_id: missionId } });
      load();
      setTimeout(() => setGeneratingReport(null), 3000);
    } catch (e) {
      alert(e.response?.data?.detail || "Failed");
      setGeneratingReport(null);
    }
  };

  const openEvidenceDetail = async (evidenceId) => {
    try {
      const res = await api.get(`/evidence/${evidenceId}`);
      setEvidenceDetail(res.data);
    } catch (e) {
      alert(e.response?.data?.detail || "Failed");
    }
  };

  const generateReport = async (reportType) => {
    try {
      setGeneratingReport(reportType);
      const params = reportParams[reportType] || {};
      await api.post("/reports/generate", { report_type: reportType, params });
      load();
      setTimeout(() => setGeneratingReport(null), 3000);
    } catch (e) {
      alert(e.response?.data?.detail || "Failed");
      setGeneratingReport(null);
    }
  };

  const downloadReport = (reportId) => {
    const token = localStorage.getItem("dvas_token") || "";
    window.open(`${API}/reports/${reportId}/download?token=${encodeURIComponent(token)}`, "_blank");
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
    crowd,
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
    requestOn,
    setRequestOn,
    justification,
    setJustification,
    generatingReport,
    reportParams,
    setReportParams,
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
    generateReport,
    downloadReport
  };

  return <CommandContext.Provider value={value}>{children}</CommandContext.Provider>;
}

export const useCommand = () => {
  const ctx = useContext(CommandContext);
  if (!ctx) throw new Error("useCommand must be used within CommandProvider");
  return ctx;
};
