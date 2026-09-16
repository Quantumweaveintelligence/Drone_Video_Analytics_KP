import AppSidebar from "./AppSidebar";
import AppTopbar from "./AppTopbar";
import { NAV_MANIFEST } from "@/lib/navManifest";
import { useEffect, useRef, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { useTheme } from "@/context/ThemeContext";
import { useI18n } from "@/context/I18nContext";
import { useDvasSocket } from "@/lib/useDvasSocket";
import { api } from "@/lib/api";
import {
  Shield, Map as MapIcon, Gauge, ShieldCheck, LogOut, Send as Drone,
  Sun, Moon, Bell, PanelLeftClose, PanelLeftOpen, Activity,
  Languages, AlertTriangle, ChevronDown, LayoutDashboard, Target,
  Video, Compass, Clock, Scan, Image, Archive, Server, ClipboardList, Settings, Cpu, X
} from "lucide-react";

const NAV = {
  drone_operator: [
    { to: "/edge/dashboard", labelKey: "nav.edgeDashboard", labelFallback: "Dashboard", Icon: LayoutDashboard },
    { to: "/edge/mission-control", labelKey: "nav.missionControl", labelFallback: "Mission Control", Icon: Target },
    { to: "/edge/live-monitoring", labelKey: "nav.liveMonitoring", labelFallback: "Live Monitoring", Icon: Video },
    { to: "/edge/gps-telemetry", labelKey: "nav.gpsTelemetry", labelFallback: "GPS & Telemetry", Icon: Compass },
    { to: "/edge/buffer-and-change", labelKey: "nav.bufferAndChange", labelFallback: "Buffer and Change", Icon: Scan },
    { to: "/edge/frame-selection", labelKey: "nav.frameSelection", labelFallback: "Frame Selection Analytics", Icon: Image },
    { to: "/edge/incidents", labelKey: "nav.incidents", labelFallback: "Incidents", Icon: AlertTriangle },
    { to: "/edge/evidence-management", labelKey: "nav.evidenceManagement", labelFallback: "Evidence Management", Icon: Archive },
    { to: "/edge/system-health", labelKey: "nav.systemHealth", labelFallback: "System Health", Icon: Server },
    { to: "/edge/logs-audit", labelKey: "nav.logsAudit", labelFallback: "Logs & Audit", Icon: ClipboardList },
    { to: "/edge/settings", labelKey: "nav.settings", labelFallback: "Settings", Icon: Settings },
  ],
  system_administrator:  [{ to: "/ingest", labelKey: "nav.ingest",  Icon: Gauge },
                          { to: "/admin",  labelKey: "nav.admin",   Icon: ShieldCheck }],
  // CRO inherits former field_supervisor mission/evidence access + former CRO command view
  control_room_officer:  [{ to: "/command",labelKey: "nav.command", Icon: MapIcon }],
  // Senior Officer inherits former supervisory_officer admin access + existing SR command+admin
  senior_officer:        [{ to: "/command",labelKey: "nav.command", Icon: MapIcon },
                          { to: "/admin",  labelKey: "nav.admin",   Icon: ShieldCheck }],
};

const SB_KEY = "dvas_sidebar_collapsed";

/* ---------- Ingest health polling ---------- */
function useIngestHealth() {
  const [state, setState] = useState({ status: "unknown", labelKey: "topbar.online" });
  useEffect(() => {
    let mounted = true;
    const tick = async () => {
      try {
        const { data } = await api.get("/ingest-server/health");
        if (!mounted) return;
        if (!data.reachable) {
          setState({ status: "crit", labelKey: "topbar.offline" });
        } else if (data.status === "ok") {
          setState({ status: "safe", labelKey: "topbar.online" });
        } else {
          setState({ status: "warn", labelKey: "topbar.degraded" });
        }
      } catch (e) {
        if (mounted) setState({ status: "crit", labelKey: "topbar.offline" });
      }
    };
    tick();
    const t = setInterval(tick, 10000);
    return () => { mounted = false; clearInterval(t); };
  }, []);
  return state;
}

/* ---------- Iconic square buttons (top bar) ---------- */
function IconBtn({ children, testid, title, onClick }) {
  return (
    <button
      data-testid={testid}
      onClick={onClick}
      title={title}
      className="h-9 w-9 rounded-lg flex items-center justify-center transition-colors hover:bg-[color:var(--dvas-elev)]"
      style={{ color: "var(--dvas-text-muted)" }}
    >
      {children}
    </button>
  );
}

function ThemeToggle() {
  const { theme, toggle } = useTheme();
  return (
    <IconBtn testid="theme-toggle" onClick={toggle} title={theme === "dark" ? "Switch to light" : "Switch to dark"}>
      {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
    </IconBtn>
  );
}

function LanguageToggle() {
  const { lang, setLang } = useI18n();
  return (
    <button
      data-testid="lang-toggle"
      onClick={() => setLang(lang === "en" ? "ml" : "en")}
      title={lang === "en" ? "മലയാളം" : "English"}
      className="h-9 px-3 rounded-lg flex items-center gap-1.5 transition-colors hover:bg-[color:var(--dvas-elev)]"
      style={{ color: "var(--dvas-text-muted)" }}
    >
      <Languages size={14} />
      <span data-testid="lang-code" className="text-xs font-semibold" style={{ color: "var(--dvas-text)" }}>
        {lang === "en" ? "EN" : "ML"}
      </span>
    </button>
  );
}

/* ---------- Notification bell drawer ---------- */
function NotificationBell() {
  const { user } = useAuth();
  const isEdgeOperator = user?.role === "drone_operator";
  const { t } = useI18n();
  const nav = useNavigate();
  const [notifications, setNotifications] = useState([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [flash, setFlash] = useState(false);
  const [filter, setFilter] = useState("all");
  const [toast, setToast] = useState(null);
  const rootRef = useRef(null);

  // Auto-dismiss live toast after 6 seconds
  useEffect(() => {
    if (!toast) return;
    const tId = setTimeout(() => setToast(null), toast.duration || 6000);
    return () => clearTimeout(tId);
  }, [toast]);

  // Load initial notifications from alerts, incidents, and model suggestions
  const loadNotifications = async () => {
    if (!user || isEdgeOperator) return;
    try {
      const [alertsRes, incsRes, suggsRes] = await Promise.allSettled([
        api.get("/alerts"),
        api.get("/incidents"),
        api.get("/analytics/suggestions"),
      ]);

      const itemMap = new Map();

      // 1. AI Model Suggestions: Format "<MISSION ID>: Suggests <AI Model name>"
      if (suggsRes.status === "fulfilled" && Array.isArray(suggsRes.value.data)) {
        suggsRes.value.data.forEach((s) => {
          const modelList = (s.model_names && s.model_names.length > 0)
            ? s.model_names.join(", ")
            : (s.models || []).map(m => m.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())).join(", ") || "AI Model";
          const missionPrefix = s.mission_id ? `${s.mission_id}: ` : "";
          const title = `${missionPrefix}Suggests ${modelList}`;
          itemMap.set(s.id, {
            id: s.id,
            type: "model_suggestion",
            title,
            subtitle: `${s.suggested_by || "Edge Operator"} · ${s.location || "Ground Station"}`,
            ts: s.timestamp || s.created_at,
            severity: s.priority === "critical" ? "critical" : s.priority === "high" ? "warning" : "info",
            badge: "AI SUGGESTION",
            badgeClass: "bg-sky-500/10 text-sky-400 border-sky-500/30",
            status: s.status || "PENDING",
            icon: "cpu",
          });
        });
      }

      // 2. Edge Station Incidents
      if (incsRes.status === "fulfilled" && Array.isArray(incsRes.value.data)) {
        incsRes.value.data.forEach((inc) => {
          const missionPrefix = inc.mission_id ? `${inc.mission_id}: ` : inc.drone_id ? `${inc.drone_id}: ` : "";
          const rawTitle = inc.title || "Operational Incident";
          const title = rawTitle.startsWith(missionPrefix) ? rawTitle : `${missionPrefix}${rawTitle}`;
          itemMap.set(inc.id, {
            id: inc.id,
            type: "incident",
            title,
            subtitle: `${inc.created_by || "Edge Operator"} · ${inc.location || "Ground Station"}`,
            ts: inc.timestamp,
            severity: inc.severity === "CRITICAL" ? "critical" : inc.severity === "HIGH" ? "warning" : "info",
            badge: inc.severity || "INCIDENT",
            badgeClass: inc.severity === "CRITICAL" ? "bg-red-500/10 text-red-400 border-red-500/30" : "bg-amber-500/10 text-amber-400 border-amber-500/30",
            status: inc.status || "OPEN",
            icon: "incident",
          });
        });
      }

      // 3. Vision Detection Alerts (skip any dual-written alerts that duplicate incidents/suggestions)
      if (alertsRes.status === "fulfilled" && Array.isArray(alertsRes.value.data)) {
        alertsRes.value.data.forEach((a) => {
          if (itemMap.has(a.id)) {
            if (a.acknowledged_by) {
              itemMap.get(a.id).status = "ACKNOWLEDGED";
            }
            return;
          }
          // SUGG- alerts: convert into model_suggestion fallback item if not already in map
          if (a.id && a.id.startsWith("SUGG-")) {
            const title = a.type || a.title || "AI Model Suggestion";
            const missionPrefix = a.mission_id && a.mission_id !== "EDGE" ? `${a.mission_id}: ` : "";
            itemMap.set(a.id, {
              id: a.id,
              type: "model_suggestion",
              title: missionPrefix ? title : title,
              subtitle: `${a.drone_id || "Edge Operator"} · ${a.zone || "Ground Station"}`,
              ts: a.ts,
              severity: a.severity === "critical" ? "critical" : a.severity === "warning" ? "warning" : "info",
              badge: "AI SUGGESTION",
              badgeClass: "bg-sky-500/10 text-sky-400 border-sky-500/30",
              status: a.acknowledged_by ? "ACKNOWLEDGED" : "PENDING",
              icon: "cpu",
            });
            return;
          }
          // INC- prefixed alerts are handled by the incidents section above
          if (a.id && a.id.startsWith("INC-")) {
            return;
          }

          const inScope =
            user.role === "system_administrator" ||
            user.role === "senior_officer" ||
            user.role === "control_room_officer" ||
            user.role === "drone_operator" ||
            ((user.mission_scope || []).includes(a.mission_id));
          if (!inScope) return;

          const missionPrefix = a.mission_id ? `${a.mission_id}: ` : "";
          let alertName = a.type ? a.type.replace(/_/g, " ").replace(/\b\w/g, l => l.toUpperCase()) : (a.title || "Detection Alert");
          if (missionPrefix && alertName.startsWith(missionPrefix)) {
            alertName = alertName.slice(missionPrefix.length).trim();
          }

          itemMap.set(a.id, {
            id: a.id,
            type: "alert",
            title: `${missionPrefix}${alertName}`,
            subtitle: `${a.drone_id || "DRN"} · ${a.zone || "Zone"}`,
            ts: a.ts,
            severity: a.severity === "critical" ? "critical" : "warning",
            badge: "DETECTION",
            badgeClass: a.severity === "critical" ? "bg-red-500/10 text-red-400 border-red-500/30" : "bg-purple-500/10 text-purple-400 border-purple-500/30",
            status: a.acknowledged_by ? "ACKNOWLEDGED" : "OPEN",
            icon: "alert",
          });
        });
      }

      // Sort newest first
      const items = Array.from(itemMap.values());
      items.sort((a, b) => new Date(b.ts || 0).getTime() - new Date(a.ts || 0).getTime());
      setNotifications(items.slice(0, 50));

      // Calculate unread count against last read timestamp (scoped per user to prevent cross-user suppression)
      const bellKey = `dvas_bell_last_read_${user?.id || user?.email || user?.role || "global"}`;
      const lastReadTime = Number(localStorage.getItem(bellKey) || 0);
      const pendingCount = items.filter(x => {
        if (x.status === "ACKNOWLEDGED") return false;
        if (!lastReadTime) return true;
        return new Date(x.ts || 0).getTime() > lastReadTime;
      }).length;
      setUnread(pendingCount);
    } catch (e) {
      console.warn("Failed to fetch initial notifications:", e);
    }
  };

  useEffect(() => {
    if (isEdgeOperator) return;
    loadNotifications();
    const interval = setInterval(loadNotifications, 5000);
    return () => clearInterval(interval);
  }, [user, isEdgeOperator]);

  // Real-time WebSocket listener
  useDvasSocket((msg) => {
    if (!user || isEdgeOperator) return;
    const eventType = msg.type || msg.event;

    // A. AI Model Suggestion: format "<MISSION ID>: Suggests <AI Model name>"
    if (eventType === "model_suggestion") {
      const s = msg.suggestion || msg;
      const modelList = (msg.model_names && msg.model_names.length > 0)
        ? msg.model_names.join(", ")
        : (s.model_names && s.model_names.length > 0)
        ? s.model_names.join(", ")
        : (s.models || []).map(m => m.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())).join(", ") || "AI Model";
      const missionPrefix = (msg.mission_id || s.mission_id) ? `${msg.mission_id || s.mission_id}: ` : "";
      const newNotif = {
        id: s.id || msg.suggestion_id || `sugg-${Date.now()}`,
        type: "model_suggestion",
        title: `${missionPrefix}Suggests ${modelList}`,
        subtitle: `${s.suggested_by || msg.suggested_by || "Edge Operator"} · ${s.location || msg.location || "Ground Station"}`,
        ts: s.timestamp || msg.timestamp || new Date().toISOString(),
        severity: (s.priority || msg.priority) === "critical" ? "critical" : (s.priority || msg.priority) === "high" ? "warning" : "info",
        badge: "AI SUGGESTION",
        badgeClass: "bg-sky-500/10 text-sky-400 border-sky-500/30",
        status: "PENDING",
        icon: "cpu",
      };
      setNotifications((prev) => [newNotif, ...prev.filter(x => x.id !== newNotif.id)].slice(0, 50));
      setUnread((c) => c + 1);
      setFlash(true);
      setTimeout(() => setFlash(false), 1200);
      setToast({ ...newNotif, duration: 6000 });
    }

    // B. Edge Incident
    else if (eventType === "incident" || eventType === "edge_incident") {
      const inc = msg.incident || msg;
      const missionPrefix = inc.mission_id ? `${inc.mission_id}: ` : inc.drone_id ? `${inc.drone_id}: ` : "";
      const rawTitle = inc.title || "Operational Incident";
      const title = rawTitle.startsWith(missionPrefix) ? rawTitle : `${missionPrefix}${rawTitle}`;
      const newNotif = {
        id: inc.id || inc.incident_id || `inc-${Date.now()}`,
        type: "incident",
        title,
        subtitle: `${inc.created_by || "Edge Operator"} · ${inc.location || "Ground Station"}`,
        ts: inc.timestamp || new Date().toISOString(),
        severity: inc.severity === "CRITICAL" ? "critical" : inc.severity === "HIGH" ? "warning" : "info",
        badge: inc.severity || "INCIDENT",
        badgeClass: inc.severity === "CRITICAL" ? "bg-red-500/10 text-red-400 border-red-500/30" : "bg-amber-500/10 text-amber-400 border-amber-500/30",
        status: "OPEN",
        icon: "incident",
      };
      setNotifications((prev) => [newNotif, ...prev.filter(x => x.id !== newNotif.id)].slice(0, 50));
      setUnread((c) => c + 1);
      setFlash(true);
      setTimeout(() => setFlash(false), 1200);
      setToast({ ...newNotif, duration: 6000 });
    }

    // C. Standard Vision Detection Alert (or SUGG- fallback real-time delivery)
    else if (eventType === "alert" && msg.alert) {
      const a = msg.alert;
      // INC- duplicates already handled via incident event — skip
      if (a.id && a.id.startsWith("INC-")) return;
      // SUGG- alert: convert to model_suggestion notification for real-time delivery
      // (backend broadcasts both "model_suggestion" and "alert" for a suggestion,
      //  so if the former was missed this "alert" acts as a fallback)
      if (a.id && a.id.startsWith("SUGG-")) {
        const title = a.type || a.title || "AI Model Suggestion";
        const newNotif = {
          id: a.id,
          type: "model_suggestion",
          title,
          subtitle: `${a.drone_id || "Edge Operator"} · ${a.zone || "Ground Station"}`,
          ts: a.ts || new Date().toISOString(),
          severity: "info",
          badge: "AI SUGGESTION",
          badgeClass: "bg-sky-500/10 text-sky-400 border-sky-500/30",
          status: "PENDING",
          icon: "cpu",
        };
        setNotifications((prev) => [newNotif, ...prev.filter(x => x.id !== newNotif.id)].slice(0, 50));
        setUnread((c) => c + 1);
        setFlash(true);
        setTimeout(() => setFlash(false), 1200);
        setToast({ ...newNotif, duration: 6000 });
        return;
      }
      const inScope =
        user.role === "system_administrator" ||
        user.role === "senior_officer" ||
        user.role === "control_room_officer" ||
        user.role === "drone_operator" ||
        ((user.mission_scope || []).includes(a.mission_id));
      if (!inScope) return;
      const missionPrefix = a.mission_id ? `${a.mission_id}: ` : "";
      let alertName = a.type ? a.type.replace(/_/g, " ").replace(/\b\w/g, l => l.toUpperCase()) : (a.title || "Detection Alert");
      if (missionPrefix && alertName.startsWith(missionPrefix)) {
        alertName = alertName.slice(missionPrefix.length).trim();
      }
      const newNotif = {
        id: a.id || `alert-${Date.now()}`,
        type: "alert",
        title: `${missionPrefix}${alertName}`,
        subtitle: `${a.drone_id || "DRN"} · ${a.zone || "Zone"}`,
        ts: a.ts || new Date().toISOString(),
        severity: a.severity === "critical" ? "critical" : "warning",
        badge: "DETECTION",
        badgeClass: a.severity === "critical" ? "bg-red-500/10 text-red-400 border-red-500/30" : "bg-purple-500/10 text-purple-400 border-purple-500/30",
        status: "OPEN",
        icon: "alert",
      };
      setNotifications((prev) => [newNotif, ...prev.filter(x => x.id !== newNotif.id)].slice(0, 50));
      setUnread((c) => c + 1);
      setFlash(true);
      setTimeout(() => setFlash(false), 1200);
    }

    // D. Deletion / Clear events from Edge
    else if (eventType === "incident_deleted") {
      setNotifications((prev) => prev.filter(x => x.id !== msg.incident_id));
    }
    else if (eventType === "incidents_cleared") {
      setNotifications((prev) => prev.filter(x => x.type !== "incident"));
    }
    else if (eventType === "model_suggestion_deleted") {
      setNotifications((prev) => prev.filter(x => x.id !== msg.suggestion_id));
    }
    else if (eventType === "model_suggestions_cleared") {
      setNotifications((prev) => prev.filter(x => x.type !== "model_suggestion"));
    }
  });

  useEffect(() => {
    if (!open) return;
    const onDoc = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  if (!user) return null;

  const markAllAsRead = () => {
    const bellKey = `dvas_bell_last_read_${user?.id || user?.email || user?.role || "global"}`;
    localStorage.setItem(bellKey, String(Date.now()));
    setUnread(0);
  };

  const jumpToAlerts = () => {
    setOpen(false);
    markAllAsRead();
    if (user?.role === "system_administrator") {
      nav("/ingest");
      try { localStorage.setItem("dvas_ingest_tab", "alerts"); } catch (e) {}
      window.dispatchEvent(new CustomEvent("dvas:ingest-tab", { detail: "alerts" }));
    } else if (user?.role === "drone_operator") {
      nav("/edge/incidents");
    } else {
      nav("/command/alerts");
    }
  };

  const handleItemClick = (item) => {
    setOpen(false);
    markAllAsRead();
    if (user?.role === "drone_operator") {
      nav("/edge/incidents");
      return;
    }
    if (user?.role === "system_administrator") {
      if (item.type === "model_suggestion") {
        nav("/ingest");
        try { localStorage.setItem("dvas_ingest_tab", "gpu"); } catch (e) {}
        window.dispatchEvent(new CustomEvent("dvas:ingest-tab", { detail: "gpu" }));
      } else {
        nav("/ingest");
        try { localStorage.setItem("dvas_ingest_tab", "alerts"); } catch (e) {}
        window.dispatchEvent(new CustomEvent("dvas:ingest-tab", { detail: "alerts" }));
      }
      return;
    }
    if (item.type === "model_suggestion") {
      nav("/command/models");
    } else {
      nav("/command/alerts");
    }
  };

  const filtered = notifications.filter((n) => {
    if (filter === "all") return true;
    if (filter === "suggestions") return n.type === "model_suggestion";
    if (filter === "incidents") return n.type === "incident";
    if (filter === "alerts") return n.type === "alert";
    return true;
  });

  const preview = filtered.slice(0, 15);

  if (isEdgeOperator) return null;

  return (
    <div className="relative" ref={rootRef}>
      {/* Real-time floating toast banner for immediate visibility across all pages */}
      {toast && (
        <div
          data-testid="live-notification-toast"
          className="fixed top-20 right-6 z-[100] max-w-sm w-full bg-slate-900/95 backdrop-blur-md border border-amber-500/60 rounded-xl shadow-2xl p-3.5 flex items-start gap-3 transition-all duration-300 ease-out"
          style={{
            boxShadow: "0 12px 30px -5px rgba(0, 0, 0, 0.7), 0 0 15px rgba(245, 158, 11, 0.25)",
          }}
        >
          <div className="mt-0.5 shrink-0">
            {toast.icon === "cpu" ? (
              <div className="w-8 h-8 rounded-lg bg-sky-500/20 border border-sky-500/40 flex items-center justify-center text-sky-400">
                <Cpu size={16} />
              </div>
            ) : (
              <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${
                toast.severity === "critical"
                  ? "bg-red-500/20 border border-red-500/50 text-red-400"
                  : "bg-amber-500/20 border border-amber-500/50 text-amber-400"
              }`}>
                <AlertTriangle size={16} />
              </div>
            )}
          </div>
          <div className="flex-1 min-w-0 cursor-pointer" onClick={() => { handleItemClick(toast); setToast(null); }}>
            <div className="flex items-center justify-between gap-1 mb-0.5">
              <span className={`text-[9px] font-mono px-1.5 py-0.2 rounded border font-semibold ${toast.badgeClass}`}>
                {toast.badge}
              </span>
              <span className="text-[10px] text-amber-400 font-semibold font-mono animate-pulse">
                JUST NOW
              </span>
            </div>
            <div className="font-bold text-slate-100 text-xs leading-snug break-words">
              {toast.title}
            </div>
            <div className="text-[11px] text-slate-400 mt-0.5 truncate">
              {toast.subtitle}
            </div>
          </div>
          <button
            onClick={() => setToast(null)}
            className="text-slate-400 hover:text-slate-200 p-1 rounded transition-colors"
            title="Dismiss"
          >
            <X size={14} />
          </button>
        </div>
      )}

      {/* Bell Button */}
      <button
        data-testid="notification-bell"
        onClick={() => { setOpen((o) => !o); if (!open) markAllAsRead(); }}
        title={`${unread} new notifications`}
        className="relative h-9 w-9 rounded-lg flex items-center justify-center transition-colors hover:bg-[color:var(--dvas-elev)] cursor-pointer"
        style={{ color: "var(--dvas-text-muted)" }}
      >
        <Bell size={16} className={flash ? "dvas-live-dot text-amber-400" : ""} />
        {unread > 0 && (
          <span
            data-testid="notification-count"
            className="absolute top-1 right-1 px-1 min-w-[14px] h-[14px] rounded-full text-[9px] font-bold font-mono flex items-center justify-center bg-amber-500 text-black shadow-sm animate-pulse"
          >
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>

      {/* Dropdown Drawer */}
      {open && (
        <div
          data-testid="notification-drawer"
          className="absolute right-0 top-11 z-50 w-96 dvas-fade-in overflow-hidden shadow-2xl rounded-xl border border-slate-700/80 bg-slate-900"
          style={{
            boxShadow: "0 10px 25px -5px rgba(0, 0, 0, 0.5), 0 8px 10px -6px rgba(0, 0, 0, 0.5)",
          }}
        >
          {/* Header */}
          <div
            className="px-4 py-3 flex items-center justify-between border-b border-slate-800 bg-slate-950/60"
          >
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-100">
              <Bell size={15} className="text-amber-400" />
              <span>Command Dispatch & Alerts</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-medium font-mono text-slate-400">
                {notifications.length} Total
              </span>
              <button
                onClick={markAllAsRead}
                className="text-[10px] text-sky-400 hover:text-sky-300 transition-colors font-medium cursor-pointer"
              >
                Mark read
              </button>
            </div>
          </div>

          {/* Quick Filter Tabs */}
          <div className="flex items-center gap-1 px-3 py-1.5 bg-slate-950/40 border-b border-slate-800/80 text-[11px] overflow-x-auto">
            <button
              onClick={() => setFilter("all")}
              className={`px-2 py-0.5 rounded transition-colors cursor-pointer ${
                filter === "all" ? "bg-slate-700 text-white font-medium" : "text-slate-400 hover:text-slate-200"
              }`}
            >
              All ({notifications.length})
            </button>
            <button
              onClick={() => setFilter("suggestions")}
              className={`px-2 py-0.5 rounded transition-colors flex items-center gap-1 cursor-pointer ${
                filter === "suggestions" ? "bg-sky-600/30 text-sky-300 border border-sky-500/40 font-medium" : "text-slate-400 hover:text-slate-200"
              }`}
            >
              <Cpu size={11} className="text-sky-400" />
              Suggestions ({notifications.filter(n => n.type === "model_suggestion").length})
            </button>
            <button
              onClick={() => setFilter("incidents")}
              className={`px-2 py-0.5 rounded transition-colors flex items-center gap-1 cursor-pointer ${
                filter === "incidents" ? "bg-amber-600/30 text-amber-300 border border-amber-500/40 font-medium" : "text-slate-400 hover:text-slate-200"
              }`}
            >
              <AlertTriangle size={11} className="text-amber-400" />
              Incidents ({notifications.filter(n => n.type === "incident").length})
            </button>
            <button
              onClick={() => setFilter("alerts")}
              className={`px-2 py-0.5 rounded transition-colors flex items-center gap-1 cursor-pointer ${
                filter === "alerts" ? "bg-purple-600/30 text-purple-300 border border-purple-500/40 font-medium" : "text-slate-400 hover:text-slate-200"
              }`}
            >
              <AlertTriangle size={11} className="text-purple-400" />
              Alerts ({notifications.filter(n => n.type === "alert").length})
            </button>
          </div>

          {/* Notifications List */}
          {preview.length === 0 ? (
            <div
              data-testid="notification-empty"
              className="px-4 py-10 text-center text-xs text-slate-500"
            >
              No incoming messages or alerts in this category.
            </div>
          ) : (
            <ul className="max-h-80 overflow-y-auto divide-y divide-slate-800/60">
              {preview.map((item, i) => (
                <li
                  key={`${item.id || i}-${item.ts}`}
                  data-testid={`notif-item-${i}`}
                  onClick={() => handleItemClick(item)}
                  className="px-4 py-2.5 flex items-start gap-2.5 text-xs hover:bg-slate-800/60 cursor-pointer transition-colors"
                >
                  {/* Icon */}
                  <div className="mt-0.5 shrink-0">
                    {item.icon === "cpu" ? (
                      <div className="w-6 h-6 rounded-md bg-sky-500/10 border border-sky-500/30 flex items-center justify-center text-sky-400">
                        <Cpu size={13} />
                      </div>
                    ) : item.icon === "incident" ? (
                      <div className={`w-6 h-6 rounded-md flex items-center justify-center ${
                        item.severity === "critical"
                          ? "bg-red-500/10 border border-red-500/40 text-red-400"
                          : "bg-amber-500/10 border border-amber-500/40 text-amber-400"
                      }`}>
                        <AlertTriangle size={13} />
                      </div>
                    ) : (
                      <div className="w-6 h-6 rounded-md bg-purple-500/10 border border-purple-500/30 flex items-center justify-center text-purple-400">
                        <AlertTriangle size={13} />
                      </div>
                    )}
                  </div>

                  {/* Content */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between gap-1 mb-0.5">
                      <span className={`text-[9px] font-mono px-1.5 py-0.2 rounded border font-semibold ${item.badgeClass}`}>
                        {item.badge}
                      </span>
                      <span className="mono text-[10px] text-slate-500 shrink-0">
                        {item.ts ? new Date(item.ts).toLocaleTimeString("en-GB") : ""}
                      </span>
                    </div>

                    {/* Exact format: <MISSION ID>: Suggests <AI Model name> */}
                    <div className="font-semibold text-slate-100 text-[12px] leading-snug break-words">
                      {item.title}
                    </div>

                    <div className="mono text-[10px] text-slate-400 mt-0.5 truncate">
                      {item.subtitle}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}

          {/* Footer */}
          <button
            data-testid="notification-viewall"
            onClick={jumpToAlerts}
            className="w-full px-4 py-2.5 text-xs font-semibold flex items-center justify-center gap-1.5 bg-slate-950/80 hover:bg-slate-800 text-sky-400 border-t border-slate-800 transition-colors cursor-pointer"
          >
            {user?.role === "drone_operator" ? "View in Edge Incidents →" : "View All in Alerts & Dispatch →"}
          </button>
        </div>
      )}
    </div>
  );
}

/* ---------- Ingest health chip ---------- */
function HealthDot() {
  const h = useIngestHealth();
  const { t } = useI18n();
  const color =
    h.status === "safe" ? "var(--dvas-safe)"
    : h.status === "warn" ? "var(--dvas-warn)"
    : h.status === "crit" ? "var(--dvas-crit)"
    : "var(--dvas-text-dim)";
  const bg =
    h.status === "safe" ? "var(--dvas-safe-bg)"
    : h.status === "warn" ? "var(--dvas-warn-bg)"
    : h.status === "crit" ? "var(--dvas-crit-bg)"
    : "transparent";
  return (
    <div
      data-testid="ingest-health"
      className="hidden md:flex items-center gap-1.5 px-2.5 h-8 text-[11px] font-medium rounded-full"
      style={{ background: bg, color }}
      title={`Ingest ${t(h.labelKey)}`}
    >
      <Activity size={12} />
      <span className="opacity-80">{t("topbar.ingest")}</span>
      <span
        className={`w-1.5 h-1.5 rounded-full ${h.status !== "unknown" ? "dvas-live-dot" : ""}`}
        style={{ background: color }}
      />
      <span>{t(h.labelKey)}</span>
    </div>
  );
}

/* ---------- User avatar + menu ---------- */
function UserMenu({ onLogout }) {
  const { user } = useAuth();
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const initials = (user?.name || "?").split(" ").map((n) => n[0]).join("").slice(0, 2).toUpperCase();

  return (
    <div className="relative" ref={rootRef}>
      <button
        data-testid="user-menu-trigger"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 h-9 pl-1 pr-2 rounded-lg transition-colors hover:bg-[color:var(--dvas-elev)]"
      >
        <div
          className="w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-semibold"
          style={{ background: "var(--dvas-blue)", color: "#fff" }}
          data-testid="user-avatar"
        >
          {initials}
        </div>
        <ChevronDown size={14} style={{ color: "var(--dvas-text-muted)" }} />
      </button>

      {open && (
        <div
          data-testid="user-menu"
          className="absolute right-0 top-11 z-50 w-56 dvas-fade-in overflow-hidden"
          style={{
            background: "var(--dvas-surface)",
            border: "1px solid var(--dvas-border)",
            borderRadius: "var(--dvas-radius)",
            boxShadow: "var(--dvas-shadow-lg)",
          }}
        >
          <div className="px-4 py-3" style={{ borderBottom: "1px solid var(--dvas-border)" }}>
            <div className="text-sm font-semibold truncate" style={{ color: "var(--dvas-text)" }}>{user?.name}</div>
            <div className="text-[11px] mt-0.5 truncate" style={{ color: "var(--dvas-text-muted)" }}>{user?.email}</div>
            <div className="mt-2 flex items-center gap-1.5 text-[10px]" style={{ color: "var(--dvas-text-muted)" }}>
              <span className="w-1.5 h-1.5 rounded-full" style={{ background: "var(--dvas-blue)" }} />
              <span>{t(`role.${user?.role}`)}</span>
              {user?.jurisdiction && <span> · {user.jurisdiction}</span>}
            </div>
          </div>
          <button
            data-testid="btn-logout"
            onClick={onLogout}
            className="w-full px-4 py-3 text-sm flex items-center gap-2 hover:bg-[color:var(--dvas-elev)] transition-colors"
            style={{ color: "var(--dvas-crit)" }}
          >
            <LogOut size={14} /> {t("auth.signOut")}
          </button>
        </div>
      )}
    </div>
  );
}

/* ---------- App Shell ---------- */
export default function AppShell({ children, title, subtitle }) {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const sections = NAV_MANIFEST[user?.role] || [];

  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem(SB_KEY) === "1"; } catch (e) { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem(SB_KEY, collapsed ? "1" : "0"); } catch (e) { /* noop */ }
  }, [collapsed]);

  const [mobileOpen, setMobileOpen] = useState(false);

  const doLogout = async () => {
    await logout();
    nav("/login");
  };

  const sidebarClass = mobileOpen ? "fixed inset-y-0 left-0 z-40 shadow-2xl" : "hidden lg:flex";

  return (
    <div
      className="h-screen overflow-hidden flex"
      style={{ background: "var(--dvas-root)", color: "var(--dvas-text)" }}
    >
      {/* Mobile Backdrop */}
      {mobileOpen && (
        <div
          className="fixed inset-0 bg-black/40 z-30 lg:hidden backdrop-blur-sm"
          onClick={() => setMobileOpen(false)}
        />
      )}

      {/* Sidebar */}
      <div className={`${sidebarClass} h-full shrink-0`}>
        <AppSidebar
          sections={sections}
          collapsed={collapsed}
          onToggleCollapse={() => setCollapsed((c) => !c)}
        />
      </div>

      {/* Main column */}
      <div className="flex-1 flex flex-col min-w-0 h-full overflow-hidden">
        {/* Top bar */}
        <AppTopbar
          title={title}
          subtitle={subtitle}
          onMobileMenuToggle={() => setMobileOpen((m) => !m)}
        >
          <HealthDot />
          {user?.role !== "drone_operator" && <NotificationBell />}
          <LanguageToggle />
          <ThemeToggle />
          <UserMenu onLogout={doLogout} />
        </AppTopbar>

        <main className="flex-1 overflow-auto p-content dvas-fade-in">{children}</main>
      </div>
    </div>
  );
}
