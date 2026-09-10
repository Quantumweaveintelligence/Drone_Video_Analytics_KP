import React, { useState, useEffect, useMemo, useRef } from 'react';
import AppShell from '@/components/layout/AppShell';
import { useAuth } from '@/context/AuthContext';
import { api } from '@/lib/api';
import { useDvasSocket } from '@/lib/useDvasSocket';
import {
  Plus, AlertTriangle, CheckCircle2, Clock, Send, RotateCcw,
  Search, ShieldAlert, Cpu, Video, VideoOff, Zap, Activity,
  MapPin, User, Check, Sparkles, Filter, ChevronRight, AlertOctagon,
  WifiOff, Battery, CloudRain, Navigation, Users, Car, Truck, Flag,
} from 'lucide-react';

// ── 10 Simple, User-Friendly Predefined Issues (No technical jargon) ─────────
const PREDEFINED_10_ISSUES = [
  {
    id: "ISSUE-01",
    title: "Drone and laptop connection is lost",
    severity: "CRITICAL",
    category: "Connection",
    description: "The laptop cannot communicate with the drone. Signal is lost.",
    icon: WifiOff,
  },
  {
    id: "ISSUE-02",
    title: "Laptop charge is finished",
    severity: "CRITICAL",
    category: "Battery & Power",
    description: "The laptop battery is almost empty or the charger is unplugged.",
    icon: Battery,
  },
  {
    id: "ISSUE-03",
    title: "Drone battery is low",
    severity: "HIGH",
    category: "Battery & Power",
    description: "The drone is running out of battery and needs to come back home.",
    icon: Zap,
  },
  {
    id: "ISSUE-04",
    title: "I am going to end this mission",
    severity: "MEDIUM",
    category: "Mission Status",
    description: "The operator is finishing the flight and bringing the drone down.",
    icon: Flag,
  },
  {
    id: "ISSUE-05",
    title: "Laptop storage is getting full",
    severity: "HIGH",
    category: "Laptop Storage",
    description: "The laptop hard drive has very little space left for saving videos.",
    icon: Activity,
  },
  {
    id: "ISSUE-06",
    title: "Live camera video has stopped",
    severity: "HIGH",
    category: "Camera & Video",
    description: "The camera video on screen is frozen or black and no new picture is showing.",
    icon: VideoOff,
  },
  {
    id: "ISSUE-07",
    title: "Weather is bad / high wind or rain",
    severity: "MEDIUM",
    category: "Weather",
    description: "Strong wind, rain, or bad weather makes it unsafe to continue flying.",
    icon: CloudRain,
  },
  {
    id: "ISSUE-08",
    title: "Person or vehicle entered restricted zone",
    severity: "CRITICAL",
    category: "Security Alert",
    description: "Someone walked or drove into a protected area without permission.",
    icon: ShieldAlert,
  },
  {
    id: "ISSUE-09",
    title: "Drone GPS signal is lost",
    severity: "HIGH",
    category: "Connection",
    description: "The drone cannot find satellite GPS and might drift in the air.",
    icon: Navigation,
  },
  {
    id: "ISSUE-10",
    title: "Emergency landing needed right now",
    severity: "CRITICAL",
    category: "Mission Status",
    description: "Urgent problem with the drone. It must land on the ground immediately.",
    icon: AlertOctagon,
  },
];

// ── Analytical Layer AI Models Available for Selection ───────────────────────
const ANALYTICAL_AI_MODELS = [
  {
    id: "crowd",
    name: "Crowd Monitoring",
    description: "Counts people density in crowds, warns of stampede risk and crowd rushes.",
    badge: "Crowd Analytics",
    icon: Users,
  },
  {
    id: "person",
    name: "Person Detection",
    description: "Finds people on ground, tracks walking paths, and flags loitering.",
    badge: "Human Tracking",
    icon: User,
  },
  {
    id: "intrusion",
    name: "Intrusion Detection",
    description: "Triggers instant alerts when anyone crosses into a marked geofence zone.",
    badge: "Perimeter Alert",
    icon: ShieldAlert,
  },
  {
    id: "anpr",
    name: "ANPR Detection",
    description: "Reads vehicle number plates automatically and checks against watchlists.",
    badge: "License Plate",
    icon: Car,
  },
  {
    id: "vehicle",
    name: "Vehicle Detection",
    description: "Detects cars, trucks, motorcycles, and monitors traffic movement.",
    badge: "Traffic Flow",
    icon: Truck,
  },
];

const INITIAL_INCIDENTS = [
  {
    id: "INC-20260714-001",
    title: "Person or vehicle entered restricted zone",
    description: "Unauthorized movement detected along North Gate boundary.",
    severity: "CRITICAL",
    category: "Security Alert",
    created_by: "Govind (govind@dvas.kp)",
    timestamp: "2026-07-14T11:42:00",
    location: "North Gate Perimeter",
    status: "OPEN",
    sent_to_ingest: true,
  },
  {
    id: "INC-20260714-002",
    title: "Drone battery is low",
    description: "Drone battery dropped low. Returning to landing zone.",
    severity: "HIGH",
    category: "Battery & Power",
    created_by: "Govind (govind@dvas.kp)",
    timestamp: "2026-07-14T11:15:00",
    location: "Sector 4 Ground Station",
    status: "OPEN",
    sent_to_ingest: true,
  },
  {
    id: "INC-20260714-003",
    title: "Live camera video has stopped",
    description: "Video display froze temporarily during turn.",
    severity: "MEDIUM",
    category: "Camera & Video",
    created_by: "Govind (govind@dvas.kp)",
    timestamp: "2026-07-14T09:30:00",
    location: "Sector 2 Field",
    status: "ACKNOWLEDGED",
    sent_to_ingest: true,
  },
];

export default function Incidents() {
  const { user } = useAuth();
  const formRef = useRef(null);
  const aiSectionRef = useRef(null);

  // Formatted operator display name
  const currentOperatorName = useMemo(() => {
    if (!user) return "Govind (govind@dvas.kp)";
    const name = user.name || (user.first_name ? `${user.first_name} ${user.last_name || ''}`.trim() : null);
    if (name && user.email) return `${name} (${user.email})`;
    return name || user.email || "Govind (govind@dvas.kp)";
  }, [user]);

  const [incidents, setIncidents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [submittingIncident, setSubmittingIncident] = useState(false);
  const [submittingSuggestion, setSubmittingSuggestion] = useState(false);
  const [feedbackMsg, setFeedbackMsg] = useState(null);

  // Predefined catalog search filter
  const [searchQuery, setSearchQuery] = useState("");

  // Report New Incident form state (manual location text input, no Lat/Lng, no autofill)
  const [form, setForm] = useState({
    title: "",
    description: "",
    severity: "MEDIUM",
    category: "General",
    location: "",
    timestamp: new Date().toISOString().slice(0, 16),
  });

  // AI Model Suggestion State
  const [selectedModels, setSelectedModels] = useState(["crowd", "intrusion"]);
  const [modelPriority, setModelPriority] = useState("high");
  const [modelLocation, setModelLocation] = useState("");
  const [modelReason, setModelReason] = useState("");
  const [recentSuggestions, setRecentSuggestions] = useState([]);

  // Active mission state for payload metadata
  const [activeMission, setActiveMission] = useState(null);

  // Load incidents & suggestions on mount
  const loadData = async () => {
    try {
      setLoading(true);
      const [incRes, suggRes] = await Promise.allSettled([
        api.get("/incidents"),
        api.get("/analytics/suggestions"),
      ]);

      if (incRes.status === "fulfilled" && Array.isArray(incRes.value.data) && incRes.value.data.length > 0) {
        setIncidents(incRes.value.data);
      } else {
        setIncidents(INITIAL_INCIDENTS);
      }

      if (suggRes.status === "fulfilled" && Array.isArray(suggRes.value.data)) {
        setRecentSuggestions(suggRes.value.data);
      }
    } catch (e) {
      setIncidents(INITIAL_INCIDENTS);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();

    // Fetch active mission ID from edge or localStorage
    const fetchActiveMission = async () => {
      try {
        const resp = await api.get("/edge/mission/active").catch(() => null);
        if (resp?.data && (resp.data.mission_id || resp.data.id)) {
          setActiveMission({
            mission_id: resp.data.mission_id || resp.data.id,
            drone_id: resp.data.drone_id || resp.data.drone || "DRN-001",
          });
          return;
        }
      } catch (e) {}

      try {
        const saved = localStorage.getItem("dvas_mission_log");
        if (saved) {
          const parsed = JSON.parse(saved);
          if (Array.isArray(parsed) && parsed.length > 0) {
            const active = parsed.find(m => m.status === "active") || parsed[0];
            if (active) {
              setActiveMission({
                mission_id: active.id || active.mission_id,
                drone_id: active.drone || active.drone_id || "DRN-001",
              });
            }
          }
        }
      } catch (e) {}
    };
    fetchActiveMission();
  }, []);

  // Real-time socket subscription
  useDvasSocket((msg) => {
    if (msg.type === "incident" && msg.incident) {
      const inc = msg.incident;
      setIncidents(prev => [inc, ...prev.filter(x => x.id !== inc.id)]);
    } else if (msg.type === "incident_ack") {
      setIncidents(prev => prev.map(x => x.id === msg.incident_id ? { ...x, status: "ACKNOWLEDGED" } : x));
    } else if (msg.type === "model_suggestion" && msg.suggestion) {
      setRecentSuggestions(prev => [msg.suggestion, ...prev.filter(x => x.id !== msg.suggestion.id)]);
    } else if (msg.type === "model_suggestion_status") {
      setRecentSuggestions(prev => prev.map(x => x.id === msg.suggestion_id ? { ...x, status: msg.status } : x));
    }
  });

  // Filter 10 predefined issues
  const filteredIssues = useMemo(() => {
    if (!searchQuery) return PREDEFINED_10_ISSUES;
    return PREDEFINED_10_ISSUES.filter(issue =>
      issue.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      issue.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
      issue.category.toLowerCase().includes(searchQuery.toLowerCase())
    );
  }, [searchQuery]);

  // Submit incident
  const submitIncident = async (incidentPayload) => {
    setSubmittingIncident(true);
    setFeedbackMsg(null);
    try {
      const payload = {
        title: incidentPayload.title.trim(),
        description: incidentPayload.description.trim(),
        severity: incidentPayload.severity,
        category: incidentPayload.category,
        location: incidentPayload.location?.trim() || "Edge Ground Station",
        timestamp: incidentPayload.timestamp || new Date().toISOString(),
        mission_id: activeMission?.mission_id || undefined,
        drone_id: activeMission?.drone_id || undefined,
      };

      const { data } = await api.post("/incidents", payload);
      const inc = data?.incident;
      if (inc) {
        setIncidents(prev => [inc, ...prev.filter(x => x.id !== inc.id)]);
        setFeedbackMsg({
          type: "success",
          text: `Reported "${inc.title}". Transmission to Ingest Server: ${inc.sent_to_ingest ? "Message Sent" : "Saved Locally (Ingest Offline)"}.`,
        });
      }

      setForm({
        title: "",
        description: "",
        severity: "MEDIUM",
        category: "General",
        location: "",
        timestamp: new Date().toISOString().slice(0, 16),
      });
    } catch (err) {
      setFeedbackMsg({
        type: "error",
        text: err.response?.data?.detail || "Could not transmit incident to edge server.",
      });
    } finally {
      setSubmittingIncident(false);
    }
  };

  const handleFormSubmit = (e) => {
    e.preventDefault();
    if (!form.title) return;
    submitIncident(form);
  };

  // 1-Click Quick Send for predefined issue
  const handleQuickSend = async (issue) => {
    const loc = form.location.trim() || "Edge Ground Station";
    await submitIncident({
      title: issue.title,
      description: issue.description,
      severity: issue.severity,
      category: issue.category,
      location: loc,
      timestamp: new Date().toISOString(),
    });
  };

  // Populate custom form from predefined issue
  const handleUseInForm = (issue) => {
    setForm(prev => ({
      ...prev,
      title: issue.title,
      description: issue.description,
      severity: issue.severity,
      category: issue.category,
    }));
    formRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  // Toggle AI model selection
  const toggleModelSelection = (modelId) => {
    setSelectedModels(prev =>
      prev.includes(modelId) ? prev.filter(m => m !== modelId) : [...prev, modelId]
    );
  };

  // Submit AI Model Suggestion to Ingest Server / GPU Dashboard
  const handleSendModelSuggestion = async (e) => {
    e.preventDefault();
    if (selectedModels.length === 0) {
      setFeedbackMsg({ type: "error", text: "Please select at least one AI model to suggest." });
      return;
    }

    setSubmittingSuggestion(true);
    setFeedbackMsg(null);
    try {
      const selectedModelObjects = ANALYTICAL_AI_MODELS.filter(m => selectedModels.includes(m.id));
      const modelNames = selectedModelObjects.map(m => m.name);

      const payload = {
        models: selectedModels,
        model_names: modelNames,
        priority: modelPriority,
        location: modelLocation.trim() || form.location.trim() || "Edge Ground Station",
        reason: modelReason.trim(),
        timestamp: new Date().toISOString(),
        mission_id: activeMission?.mission_id || undefined,
        drone_id: activeMission?.drone_id || undefined,
      };

      const { data } = await api.post("/analytics/suggestions", payload);
      if (data?.suggestion) {
        setRecentSuggestions(prev => [data.suggestion, ...prev.filter(x => x.id !== data.suggestion.id)]);
        setFeedbackMsg({
          type: "success",
          text: `AI Models (${modelNames.join(", ")}) suggested to Ingest & GPU Dashboard successfully!`,
        });
        setModelReason("");
      }
    } catch (err) {
      setFeedbackMsg({
        type: "error",
        text: err.response?.data?.detail || "Could not send model suggestion to Ingest Server.",
      });
    } finally {
      setSubmittingSuggestion(false);
    }
  };

  // Retry sending unsent incident
  const handleResend = async (incidentId) => {
    try {
      const { data } = await api.post(`/incidents/${incidentId}/resend`);
      setIncidents(prev => prev.map(x => x.id === incidentId ? { ...x, sent_to_ingest: data.sent_to_ingest } : x));
      if (data.sent_to_ingest) {
        setFeedbackMsg({ type: "success", text: `Incident ${incidentId} transmitted to Ingest Server.` });
      } else {
        setFeedbackMsg({ type: "warning", text: `Ingest Server unreachable. Retained in local buffer.` });
      }
    } catch (err) {
      setFeedbackMsg({ type: "error", text: "Retry request failed." });
    }
  };

  // Acknowledge incident
  const handleAcknowledge = async (incidentId) => {
    try {
      await api.post(`/incidents/${incidentId}/ack`);
      setIncidents(prev => prev.map(x => x.id === incidentId ? { ...x, status: "ACKNOWLEDGED" } : x));
    } catch (err) {
      console.error("Ack error", err);
    }
  };

  // Stats calculation
  const openCount = incidents.filter(i => i.status === 'OPEN').length;
  const criticalCount = incidents.filter(i => i.severity === 'CRITICAL' || i.severity === 'HIGH').length;
  const sentCount = incidents.filter(i => i.sent_to_ingest).length;

  const getSeverityStyle = (severity) => {
    switch(severity) {
      case 'CRITICAL': return 'bg-red-500/15 border-red-500/40 text-red-300';
      case 'HIGH': return 'bg-amber-500/15 border-amber-500/40 text-amber-300';
      case 'MEDIUM': return 'bg-sky-500/15 border-sky-500/40 text-sky-300';
      case 'LOW': return 'bg-slate-800 border-slate-700 text-slate-400';
      default: return 'bg-slate-800 border-slate-700 text-slate-400';
    }
  };

  const getSeverityDot = (severity) => {
    switch(severity) {
      case 'CRITICAL': return 'bg-red-500';
      case 'HIGH': return 'bg-amber-500';
      case 'MEDIUM': return 'bg-sky-500';
      case 'LOW': return 'bg-slate-500';
      default: return 'bg-slate-500';
    }
  };

  return (
    <AppShell title="Incidents & Fault Reporting" subtitle="Simple edge issue reporting, AI analytical model suggestions, and real-time Ingest dispatch">
      <div className="space-y-6">

        {/* Top Summary Stats Bar */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div className="bg-slate-900/90 border border-slate-800 p-3.5 rounded-lg">
            <div className="flex items-center justify-between text-slate-400 text-xs mb-1">
              <span>Total Recorded</span>
              <Activity size={14} className="text-sky-400" />
            </div>
            <div className="text-xl font-bold font-mono text-white">{incidents.length}</div>
            <div className="text-[10px] text-slate-500 mt-0.5">Across current session</div>
          </div>

          <div className="bg-slate-900/90 border border-slate-800 p-3.5 rounded-lg">
            <div className="flex items-center justify-between text-slate-400 text-xs mb-1">
              <span>Active / Open</span>
              <AlertTriangle size={14} className="text-amber-400" />
            </div>
            <div className="text-xl font-bold font-mono text-amber-400 flex items-center gap-2">
              {openCount}
              {openCount > 0 && <span className="w-2 h-2 rounded-full bg-amber-400 animate-ping"></span>}
            </div>
            <div className="text-[10px] text-slate-500 mt-0.5">Needs attention</div>
          </div>

          <div className="bg-slate-900/90 border border-slate-800 p-3.5 rounded-lg">
            <div className="flex items-center justify-between text-slate-400 text-xs mb-1">
              <span>Critical & High</span>
              <ShieldAlert size={14} className="text-red-400" />
            </div>
            <div className="text-xl font-bold font-mono text-red-400">{criticalCount}</div>
            <div className="text-[10px] text-slate-500 mt-0.5">High importance issues</div>
          </div>

          <div className="bg-slate-900/90 border border-slate-800 p-3.5 rounded-lg">
            <div className="flex items-center justify-between text-slate-400 text-xs mb-1">
              <span>Sent to Ingest</span>
              <Send size={14} className="text-emerald-400" />
            </div>
            <div className="text-xl font-bold font-mono text-emerald-400">
              {sentCount} <span className="text-xs text-slate-400 font-normal">/ {incidents.length}</span>
            </div>
            <div className="text-[10px] text-slate-500 mt-0.5">Delivered to central server</div>
          </div>
        </div>

        {/* Feedback alert toast */}
        {feedbackMsg && (
          <div className={`p-3 rounded-lg border text-xs font-mono flex items-center justify-between transition-all ${
            feedbackMsg.type === "success" ? "bg-emerald-950/60 border-emerald-500/40 text-emerald-200" :
            feedbackMsg.type === "warning" ? "bg-amber-950/60 border-amber-500/40 text-amber-200" :
            "bg-red-950/60 border-red-500/40 text-red-200"
          }`}>
            <div className="flex items-center gap-2">
              {feedbackMsg.type === "success" ? <CheckCircle2 size={15} /> : <AlertOctagon size={15} />}
              <span>{feedbackMsg.text}</span>
            </div>
            <button onClick={() => setFeedbackMsg(null)} className="text-slate-400 hover:text-white text-xs px-2 py-0.5">✕</button>
          </div>
        )}

        {/* ── SECTION 1: Predefined Edge Issues (10 Simple Issues, User-Friendly) ── */}
        <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
          <div className="p-4 border-b border-slate-800 bg-slate-950/60 flex flex-col md:flex-row md:items-center justify-between gap-3">
            <div className="flex items-center gap-2.5">
              <div className="p-2 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-400">
                <AlertTriangle size={18} />
              </div>
              <div>
                <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-200">
                  Predefined Edge Issues & Problems (10 Quick Options)
                </h2>
                <p className="text-xs text-slate-400 mt-0.5">
                  Click <strong>Use in Form</strong> to add extra details, or <strong>Send Now</strong> to alert the Ingest Server immediately.
                </p>
              </div>
            </div>

            {/* Quick search input */}
            <div className="relative w-full md:w-64">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
              <input
                type="text"
                placeholder="Search 10 issues..."
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                className="w-full bg-slate-950 border border-slate-800 rounded pl-8 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:border-sky-500 outline-none"
              />
            </div>
          </div>

          {/* 10 Simple Issues Grid */}
          <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-3.5">
            {filteredIssues.map((issue, idx) => {
              const IconComponent = issue.icon;
              return (
                <div
                  key={issue.id}
                  className="p-3.5 bg-slate-950/60 border border-slate-800/90 hover:border-slate-700 rounded-lg flex flex-col justify-between transition-all"
                >
                  <div>
                    <div className="flex items-center justify-between gap-2 mb-2">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-mono text-slate-500 font-bold">#{idx + 1}</span>
                        <span className={`px-2 py-0.5 rounded text-[10px] font-mono font-bold uppercase tracking-wider border ${getSeverityStyle(issue.severity)}`}>
                          {issue.severity}
                        </span>
                      </div>
                      <span className="text-[11px] font-mono text-slate-400 bg-slate-800/80 px-2 py-0.5 rounded border border-slate-700">
                        {issue.category}
                      </span>
                    </div>

                    <div className="flex items-start gap-2.5 mt-1">
                      <div className="p-1.5 rounded bg-slate-800/70 text-slate-300 mt-0.5 shrink-0">
                        <IconComponent size={16} />
                      </div>
                      <div>
                        <h3 className="text-sm font-semibold text-white mb-1">
                          {issue.title}
                        </h3>
                        <p className="text-xs text-slate-400 leading-relaxed">
                          {issue.description}
                        </p>
                      </div>
                    </div>
                  </div>

                  <div className="mt-3.5 pt-2.5 border-t border-slate-800/60 flex items-center justify-end gap-2">
                    <button
                      onClick={() => handleUseInForm(issue)}
                      title="Put this issue into the report form below to add custom notes or location"
                      className="px-3 py-1 text-xs bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 rounded flex items-center gap-1.5 transition-colors"
                    >
                      <span>Use in Form</span>
                      <ChevronRight size={12} />
                    </button>
                    <button
                      onClick={() => handleQuickSend(issue)}
                      disabled={submittingIncident}
                      title="Send this issue to Ingest Server right now"
                      className="px-3 py-1 text-xs bg-red-600/20 hover:bg-red-600/30 text-red-300 border border-red-500/40 rounded flex items-center gap-1.5 transition-colors font-medium"
                    >
                      <Send size={12} />
                      <span>Send Now</span>
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* ── SECTION 2: Analytical Layer AI Model Suggestions & Selection ────── */}
        <div ref={aiSectionRef} className="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
          <div className="p-4 border-b border-slate-800 bg-slate-950/60 flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <div className="p-2 rounded-lg bg-sky-500/10 border border-sky-500/30 text-sky-400">
                <Cpu size={18} />
              </div>
              <div>
                <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-200">
                  Analytical Layer · Suggest & Request AI Models
                </h2>
                <p className="text-xs text-slate-400 mt-0.5">
                  Select which AI models you recommend for this drone video feed. Sent to the Ingest Server & GPU Dashboard.
                </p>
              </div>
            </div>
            <span className="hidden sm:inline text-xs font-mono text-sky-400 bg-sky-500/10 border border-sky-500/30 px-2.5 py-1 rounded">
              GPU Analytics Dispatch
            </span>
          </div>

          <form onSubmit={handleSendModelSuggestion} className="p-4 space-y-4">
            
            {/* Model Selection Cards */}
            <div>
              <label className="block text-xs text-slate-300 uppercase font-semibold mb-2">
                Select Analytical AI Models to Run on Video Feed <span className="text-red-500">*</span>
              </label>
              <div className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-5 gap-3">
                {ANALYTICAL_AI_MODELS.map(model => {
                  const isSelected = selectedModels.includes(model.id);
                  const IconComp = model.icon;
                  return (
                    <div
                      key={model.id}
                      onClick={() => toggleModelSelection(model.id)}
                      className={`cursor-pointer p-3 rounded-lg border transition-all flex flex-col justify-between ${
                        isSelected
                          ? "bg-sky-950/50 border-sky-500 shadow-md shadow-sky-500/10"
                          : "bg-slate-950/50 border-slate-800 hover:border-slate-700"
                      }`}
                    >
                      <div>
                        <div className="flex items-center justify-between mb-2">
                          <div className={`p-1.5 rounded ${isSelected ? "bg-sky-500 text-white" : "bg-slate-800 text-slate-400"}`}>
                            <IconComp size={15} />
                          </div>
                          <input
                            type="checkbox"
                            checked={isSelected}
                            onChange={() => {}}
                            className="rounded accent-sky-500 h-4 w-4 cursor-pointer"
                          />
                        </div>
                        <h4 className="text-xs font-semibold text-slate-100 mb-1">
                          {model.name}
                        </h4>
                        <p className="text-[11px] text-slate-400 leading-relaxed mb-2">
                          {model.description}
                        </p>
                      </div>
                      <div className="pt-2 border-t border-slate-800/80">
                        <span className={`text-[9px] font-mono uppercase px-1.5 py-0.5 rounded border ${
                          isSelected ? "bg-sky-500/20 text-sky-300 border-sky-500/40" : "bg-slate-800 text-slate-400 border-slate-700"
                        }`}>
                          {model.badge}
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Inputs: Priority, Location, Reason */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <div>
                <label className="block text-[10px] text-slate-400 uppercase font-semibold mb-1">
                  Priority Level
                </label>
                <select
                  value={modelPriority}
                  onChange={e => setModelPriority(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-xs text-slate-200 rounded"
                >
                  <option value="normal">Normal Priority</option>
                  <option value="high">High Priority</option>
                  <option value="urgent">Urgent / Immediate Attention</option>
                </select>
              </div>

              <div>
                <label className="block text-[10px] text-slate-400 uppercase font-semibold mb-1">
                  Location / Area of Interest
                </label>
                <div className="relative">
                  <MapPin size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
                  <input
                    type="text"
                    value={modelLocation}
                    onChange={e => setModelLocation(e.target.value)}
                    placeholder="e.g. Sector 4 - Main Gate, Temple Road"
                    className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none pl-8 pr-3 py-2 text-xs text-slate-200 rounded"
                  />
                </div>
              </div>

              <div>
                <label className="block text-[10px] text-slate-400 uppercase font-semibold mb-1">
                  Operational Reason / Notes
                </label>
                <input
                  type="text"
                  value={modelReason}
                  onChange={e => setModelReason(e.target.value)}
                  placeholder="e.g. Expecting crowd buildup near entrance"
                  className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-xs text-slate-200 rounded"
                />
              </div>
            </div>

            {/* Submit button & operator preview */}
            <div className="flex flex-col sm:flex-row items-center justify-between gap-3 pt-2">
              <div className="text-xs text-slate-400 font-mono">
                Operator: <strong className="text-slate-200">{currentOperatorName}</strong> · Selected: <strong className="text-sky-300">{selectedModels.length} model(s)</strong>
              </div>

              <button
                type="submit"
                disabled={submittingSuggestion || selectedModels.length === 0}
                className="w-full sm:w-auto px-5 py-2.5 bg-sky-600 hover:bg-sky-500 disabled:opacity-50 text-white text-xs font-semibold rounded flex items-center justify-center gap-2 transition-colors shadow-lg shadow-sky-500/20"
              >
                {submittingSuggestion ? (
                  <>
                    <RotateCcw size={14} className="animate-spin" />
                    <span>Transmitting to Ingest & GPU...</span>
                  </>
                ) : (
                  <>
                    <Send size={14} />
                    <span>Send Model Suggestion to Ingest & GPU Dashboard</span>
                  </>
                )}
              </button>
            </div>
          </form>

          {/* Recent Model Requests History */}
          {recentSuggestions.length > 0 && (
            <div className="px-4 pb-4 pt-1">
              <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
                Recent Model Suggestions Sent to Ingest
              </div>
              <div className="overflow-x-auto border border-slate-800 rounded bg-slate-950/40">
                <table className="w-full text-xs text-left font-mono">
                  <thead className="bg-slate-950/80 text-slate-400 text-[10px] uppercase border-b border-slate-800">
                    <tr>
                      <th className="px-3 py-2">Requested Models</th>
                      <th className="px-3 py-2">Priority</th>
                      <th className="px-3 py-2">Location</th>
                      <th className="px-3 py-2">Operator Note</th>
                      <th className="px-3 py-2">Time</th>
                      <th className="px-3 py-2">Ingest / GPU Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60">
                    {recentSuggestions.slice(0, 5).map(sugg => (
                      <tr key={sugg.id} className="hover:bg-slate-800/20">
                        <td className="px-3 py-2">
                          <div className="flex flex-wrap gap-1">
                            {(sugg.model_names || sugg.models || []).map((m, i) => (
                              <span key={i} className="px-1.5 py-0.5 text-[10px] bg-sky-950/60 text-sky-200 border border-sky-800/50 rounded">
                                {m}
                              </span>
                            ))}
                          </div>
                        </td>
                        <td className="px-3 py-2">
                          <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold uppercase border ${
                            sugg.priority === "urgent" ? "bg-red-500/20 text-red-300 border-red-500/40" :
                            sugg.priority === "high" ? "bg-amber-500/20 text-amber-300 border-amber-500/40" :
                            "bg-slate-800 text-slate-300 border-slate-700"
                          }`}>
                            {sugg.priority || "normal"}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-slate-300">
                          📍 {sugg.location || "Ground Station"}
                        </td>
                        <td className="px-3 py-2 text-slate-400 max-w-xs truncate text-[11px]">
                          {sugg.reason || "—"}
                        </td>
                        <td className="px-3 py-2 text-slate-500 text-[11px]">
                          {sugg.timestamp ? new Date(sugg.timestamp).toLocaleTimeString() : "—"}
                        </td>
                        <td className="px-3 py-2">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-semibold border ${
                            sugg.status === "DEPLOYED_TO_GPU"
                              ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/40"
                              : sugg.status === "ACKNOWLEDGED"
                              ? "bg-slate-800 text-slate-300 border-slate-700"
                              : "bg-sky-500/20 text-sky-300 border-sky-500/40"
                          }`}>
                            {sugg.status === "DEPLOYED_TO_GPU" ? "DEPLOYED ON GPU" : sugg.status || "SENT TO INGEST"}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>

        {/* ── SECTION 3: Custom Incident Form & Incident Log Table ───────────── */}
        <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-start">
          
          {/* Report New Incident Form (4 cols) */}
          <div ref={formRef} className="xl:col-span-4 bg-slate-900 border border-slate-800 rounded-lg flex flex-col overflow-hidden sticky top-6">
            <div className="p-4 border-b border-slate-800 bg-slate-950/50 flex items-center justify-between">
              <div>
                <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-300">
                  Report Custom Incident
                </h2>
                <p className="text-[11px] text-slate-500 mt-0.5">
                  Type any customized operational fault or edit prefilled details
                </p>
              </div>
              <span className="p-1 rounded bg-sky-500/10 text-sky-400">
                <Plus size={15} />
              </span>
            </div>

            <form onSubmit={handleFormSubmit} className="p-4 flex flex-col gap-3.5">
              
              <div>
                <label className="block text-[10px] text-slate-400 uppercase font-semibold mb-1">
                  Title <span className="text-red-500">*</span>
                </label>
                <input 
                  type="text" 
                  required
                  value={form.title}
                  onChange={e => setForm({...form, title: e.target.value})}
                  className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-xs text-slate-200 rounded transition-colors"
                  placeholder="e.g. Drone and laptop connection is lost"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-[10px] text-slate-400 uppercase font-semibold mb-1">
                    Severity
                  </label>
                  <div className="relative">
                    <select 
                      value={form.severity}
                      onChange={e => setForm({...form, severity: e.target.value})}
                      className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 pl-7 text-xs text-slate-200 rounded appearance-none cursor-pointer"
                    >
                      <option value="LOW">Low</option>
                      <option value="MEDIUM">Medium</option>
                      <option value="HIGH">High</option>
                      <option value="CRITICAL">Critical</option>
                    </select>
                    <div className={`absolute left-2.5 top-1/2 -translate-y-1/2 w-2 h-2 rounded-full ${getSeverityDot(form.severity)}`}></div>
                  </div>
                </div>

                <div>
                  <label className="block text-[10px] text-slate-400 uppercase font-semibold mb-1">
                    Category
                  </label>
                  <select 
                    value={form.category}
                    onChange={e => setForm({...form, category: e.target.value})}
                    className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-xs text-slate-200 rounded appearance-none cursor-pointer"
                  >
                    <option value="Connection">Connection</option>
                    <option value="Battery & Power">Battery & Power</option>
                    <option value="Mission Status">Mission Status</option>
                    <option value="Laptop Storage">Laptop Storage</option>
                    <option value="Camera & Video">Camera & Video</option>
                    <option value="Weather">Weather</option>
                    <option value="Security Alert">Security Alert</option>
                    <option value="General">General / Other</option>
                  </select>
                </div>
              </div>

              {/* Manual Location / Place Input (No Lat/Lng, No Autofill) */}
              <div>
                <label className="block text-[10px] text-slate-400 uppercase font-semibold mb-1">
                  Location / Place <span className="text-red-500">*</span>
                </label>
                <div className="relative">
                  <MapPin size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
                  <input 
                    type="text" 
                    required
                    value={form.location}
                    onChange={e => setForm({...form, location: e.target.value})}
                    className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none pl-8 pr-3 py-2 text-xs text-slate-200 rounded transition-colors"
                    placeholder="e.g. Ground Station Alpha, North Gate, Sector 4"
                  />
                </div>
              </div>

              <div>
                <label className="block text-[10px] text-slate-400 uppercase font-semibold mb-1">
                  Description / Explanation
                </label>
                <textarea 
                  rows="3"
                  value={form.description}
                  onChange={e => setForm({...form, description: e.target.value})}
                  className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-xs text-slate-200 rounded transition-colors resize-none"
                  placeholder="Simple explanation of what happened..."
                ></textarea>
              </div>

              <div>
                <label className="block text-[10px] text-slate-400 uppercase font-semibold mb-1">
                  Timestamp
                </label>
                <input 
                  type="datetime-local" 
                  value={form.timestamp}
                  onChange={e => setForm({...form, timestamp: e.target.value})}
                  className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-1.5 text-xs text-slate-200 font-mono rounded"
                />
              </div>

              {/* Readonly Operator Info Preview */}
              <div className="p-2.5 rounded bg-slate-950/70 border border-slate-800 text-[11px] font-mono text-slate-400 flex items-center gap-2">
                <User size={13} className="text-slate-500 shrink-0" />
                <span className="truncate">
                  Created By: <strong className="text-slate-200">{currentOperatorName}</strong>
                </span>
              </div>

              <button 
                type="submit" 
                disabled={submittingIncident}
                className="mt-1 w-full bg-sky-600 hover:bg-sky-500 disabled:opacity-50 text-white py-2.5 text-xs font-semibold tracking-wide flex items-center justify-center gap-2 rounded transition-colors shadow-lg shadow-sky-500/20"
              >
                {submittingIncident ? (
                  <>
                    <RotateCcw size={14} className="animate-spin" />
                    <span>Transmitting to Ingest...</span>
                  </>
                ) : (
                  <>
                    <Send size={14} />
                    <span>Report & Transmit to Ingest</span>
                  </>
                )}
              </button>
            </form>
          </div>

          {/* Incident Log Table (8 cols) */}
          <div className="xl:col-span-8 bg-slate-900 border border-slate-800 rounded-lg flex flex-col overflow-hidden">
            <div className="p-4 border-b border-slate-800 flex justify-between items-center bg-slate-950/50">
              <div className="flex items-center gap-2">
                <ShieldAlert size={16} className="text-sky-400" />
                <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-300">
                  Incident Log & Ingest Status
                </h2>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-mono text-slate-400">
                  {incidents.length} recorded
                </span>
                <div className="text-[10px] px-2 py-0.5 rounded border border-red-500/30 bg-red-500/10 text-red-400 font-bold tracking-wider flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-red-400 animate-pulse"></span>
                  {openCount} OPEN
                </div>
              </div>
            </div>
            
            <div className="overflow-x-auto flex-1">
              <table className="w-full text-left text-xs whitespace-nowrap">
                <thead className="bg-slate-950/80 text-slate-400 text-[10px] uppercase font-semibold border-b border-slate-800">
                  <tr>
                    <th className="px-3 py-3 w-24">Severity</th>
                    <th className="px-3 py-3 min-w-[200px]">Title & Details</th>
                    <th className="px-3 py-3 w-28">Category</th>
                    <th className="px-3 py-3 w-36">Location / Place</th>
                    <th className="px-3 py-3 w-40">Created By</th>
                    <th className="px-3 py-3 w-32">Timestamp</th>
                    <th className="px-3 py-3 w-32">Status</th>
                    <th className="px-3 py-3 w-24 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60 font-sans">
                  {incidents.map(incident => (
                    <tr key={incident.id} className="hover:bg-slate-800/30 transition-colors">
                      <td className="px-3 py-3">
                        <span className={`inline-block px-2 py-0.5 rounded text-[9px] font-mono font-bold tracking-wider border ${getSeverityStyle(incident.severity)}`}>
                          {incident.severity}
                        </span>
                      </td>
                      <td className="px-3 py-3">
                        <div className="font-semibold text-slate-100 mb-0.5">{incident.title}</div>
                        <div className="text-[11px] text-slate-400 truncate max-w-[240px] lg:max-w-[320px]">
                          {incident.description || 'No description provided.'}
                        </div>
                      </td>
                      <td className="px-3 py-3">
                        <span className="inline-block px-2 py-0.5 rounded text-[10px] font-mono tracking-wider bg-slate-800 text-slate-300 border border-slate-700">
                          {incident.category || "General"}
                        </span>
                      </td>
                      <td className="px-3 py-3 text-slate-200">
                        <div className="flex items-center gap-1.5">
                          <MapPin size={11} className="text-slate-500 shrink-0" />
                          <span className="font-medium text-[11px] truncate max-w-[140px]">
                            {incident.location || incident.place || "Ground Station"}
                          </span>
                        </div>
                      </td>
                      <td className="px-3 py-3 font-mono text-[11px] text-slate-300">
                        {incident.created_by || incident.createdBy || currentOperatorName}
                      </td>
                      <td className="px-3 py-3 font-mono text-[11px] text-slate-400">
                        {incident.timestamp ? (
                          incident.timestamp.length > 16 
                            ? incident.timestamp.slice(0, 16).replace('T', ' ')
                            : incident.timestamp.replace('T', ' ')
                        ) : '—'}
                      </td>
                      <td className="px-3 py-3">
                        {incident.sent_to_ingest ? (
                          <span className="inline-flex items-center gap-1 text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 font-semibold">
                            <CheckCircle2 size={11} /> Message Sent
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-[10px] font-mono px-2 py-0.5 rounded bg-amber-500/10 border border-amber-500/30 text-amber-400 font-semibold">
                            <AlertTriangle size={11} /> Not Sent
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-3 text-right">
                        <div className="flex items-center justify-end gap-1.5">
                          {!incident.sent_to_ingest && (
                            <button
                              onClick={() => handleResend(incident.id)}
                              title="Retry transmitting incident to Ingest Server"
                              className="px-2 py-0.5 text-[10px] font-mono border border-amber-500/50 hover:bg-amber-500/20 text-amber-300 rounded flex items-center gap-1"
                            >
                              <RotateCcw size={10} /> Retry
                            </button>
                          )}
                          {incident.status !== "ACKNOWLEDGED" ? (
                            <button
                              onClick={() => handleAcknowledge(incident.id)}
                              title="Mark incident as acknowledged"
                              className="px-2 py-0.5 text-[10px] font-mono border border-slate-700 hover:border-slate-500 text-slate-300 hover:text-white rounded"
                            >
                              Ack
                            </button>
                          ) : (
                            <span className="text-[10px] font-mono text-slate-500">Acked</span>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                  {incidents.length === 0 && (
                    <tr>
                      <td colSpan={8} className="px-4 py-8 text-center text-slate-500 text-xs font-mono">
                        {loading ? "Loading operational incidents..." : "No incidents reported yet."}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

        </div>

      </div>
    </AppShell>
  );
}
