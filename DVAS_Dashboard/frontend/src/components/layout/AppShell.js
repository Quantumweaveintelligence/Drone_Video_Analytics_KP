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
  Video, Compass, Clock, Scan, Image, Archive, Server, ClipboardList, Settings, Cpu
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
  const { t } = useI18n();
  const nav = useNavigate();
  const [notifications, setNotifications] = useState([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [flash, setFlash] = useState(false);
  const [filter, setFilter] = useState("all");
  const rootRef = useRef(null);

  // Load initial notifications from alerts, incidents, and model suggestions
  const loadNotifications = async () => {
    if (!user) return;
    try {
      const [alertsRes, incsRes, suggsRes] = await Promise.allSettled([
        api.get("/alerts"),
        api.get("/incidents"),
        api.get("/analytics/suggestions"),
      ]);

      const items = [];

      // 1. AI Model Suggestions: Format "<MISSION ID>: Suggests <AI Model name>"
      if (suggsRes.status === "fulfilled" && Array.isArray(suggsRes.value.data)) {
        suggsRes.value.data.forEach((s) => {
          const modelList = (s.model_names && s.model_names.length > 0)
            ? s.model_names.join(", ")
            : (s.models || []).map(m => m.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())).join(", ") || "AI Model";
          const missionPrefix = s.mission_id || "Mission";
          items.push({
            id: s.id,
            type: "model_suggestion",
            title: `${missionPrefix}: Suggests ${modelList}`,
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
          items.push({
            id: inc.id,
            type: "incident",
            title: `${missionPrefix}${inc.title}`,
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

      // 3. Vision Detection Alerts
      if (alertsRes.status === "fulfilled" && Array.isArray(alertsRes.value.data)) {
        alertsRes.value.data.forEach((a) => {
          const inScope =
            user.role === "system_administrator" ||
            user.role === "senior_officer" ||
            user.role === "control_room_officer" ||
            user.role === "drone_operator" ||
            ((user.mission_scope || []).includes(a.mission_id));
          if (!inScope) return;
          const missionPrefix = a.mission_id ? `${a.mission_id}: ` : "";
          const alertName = a.type ? a.type.replace(/_/g, " ").replace(/\b\w/g, l => l.toUpperCase()) : (a.title || "Detection Alert");
          items.push({
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
      items.sort((a, b) => new Date(b.ts || 0).getTime() - new Date(a.ts || 0).getTime());
      setNotifications(items.slice(0, 50));

      // Calculate unacknowledged count for badge
      const pendingCount = items.filter(x => x.status !== "ACKNOWLEDGED").length;
      setUnread(pendingCount);
    } catch (e) {
      console.warn("Failed to fetch initial notifications:", e);
    }
  };

  useEffect(() => {
    loadNotifications();
    const interval = setInterval(loadNotifications, 5000);
    return () => clearInterval(interval);
  }, [user]);

  // Real-time WebSocket listener
  useDvasSocket((msg) => {
    if (!user) return;

    // A. AI Model Suggestion: format "<MISSION ID>: Suggests <AI Model name>"
    if (msg.type === "model_suggestion") {
      const s = msg.suggestion || msg;
      const modelList = (msg.model_names && msg.model_names.length > 0)
        ? msg.model_names.join(", ")
        : (s.model_names && s.model_names.length > 0)
        ? s.model_names.join(", ")
        : (s.models || []).map(m => m.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())).join(", ") || "AI Model";
      const missionPrefix = msg.mission_id || s.mission_id || "Mission";
      const newNotif = {
        id: s.id || `sugg-${Date.now()}`,
        type: "model_suggestion",
        title: `${missionPrefix}: Suggests ${modelList}`,
        subtitle: `${s.suggested_by || msg.suggested_by || "Edge Operator"} · ${s.location || msg.location || "Ground Station"}`,
        ts: s.timestamp || msg.timestamp || new Date().toISOString(),
        severity: (s.priority || msg.priority) === "critical" ? "critical" : (s.priority || msg.priority) === "high" ? "warning" : "info",
        badge: "AI SUGGESTION",
        badgeClass: "bg-sky-500/10 text-sky-400 border-sky-500/30",
        icon: "cpu",
      };
      setNotifications((prev) => [newNotif, ...prev.filter(x => x.id !== newNotif.id)].slice(0, 50));
      setUnread((c) => c + 1);
      setFlash(true);
      setTimeout(() => setFlash(false), 1200);
    }

    // B. Edge Incident
    else if (msg.type === "incident" && msg.incident) {
      const inc = msg.incident;
      const missionPrefix = inc.mission_id ? `${inc.mission_id}: ` : inc.drone_id ? `${inc.drone_id}: ` : "";
      const newNotif = {
        id: inc.id || `inc-${Date.now()}`,
        type: "incident",
        title: `${missionPrefix}${inc.title}`,
        subtitle: `${inc.created_by || "Edge Operator"} · ${inc.location || "Ground Station"}`,
        ts: inc.timestamp || new Date().toISOString(),
        severity: inc.severity === "CRITICAL" ? "critical" : inc.severity === "HIGH" ? "warning" : "info",
        badge: inc.severity || "INCIDENT",
        badgeClass: inc.severity === "CRITICAL" ? "bg-red-500/10 text-red-400 border-red-500/30" : "bg-amber-500/10 text-amber-400 border-amber-500/30",
        icon: "incident",
      };
      setNotifications((prev) => [newNotif, ...prev.filter(x => x.id !== newNotif.id)].slice(0, 50));
      setUnread((c) => c + 1);
      setFlash(true);
      setTimeout(() => setFlash(false), 1200);
    }

    // C. Standard Vision Detection Alert
    else if (msg.type === "alert" && msg.alert) {
      const a = msg.alert;
      const inScope =
        user.role === "system_administrator" ||
        user.role === "senior_officer" ||
        user.role === "control_room_officer" ||
        user.role === "drone_operator" ||
        ((user.mission_scope || []).includes(a.mission_id));
      if (!inScope) return;
      const missionPrefix = a.mission_id ? `${a.mission_id}: ` : "";
      const alertName = a.type ? a.type.replace(/_/g, " ").replace(/\b\w/g, l => l.toUpperCase()) : (a.title || "Detection Alert");
      const newNotif = {
        id: a.id || `alert-${Date.now()}`,
        type: "alert",
        title: `${missionPrefix}${alertName}`,
        subtitle: `${a.drone_id || "DRN"} · ${a.zone || "Zone"}`,
        ts: a.ts || new Date().toISOString(),
        severity: a.severity === "critical" ? "critical" : "warning",
        badge: "DETECTION",
        badgeClass: a.severity === "critical" ? "bg-red-500/10 text-red-400 border-red-500/30" : "bg-purple-500/10 text-purple-400 border-purple-500/30",
        icon: "alert",
      };
      setNotifications((prev) => [newNotif, ...prev.filter(x => x.id !== newNotif.id)].slice(0, 50));
      setUnread((c) => c + 1);
      setFlash(true);
      setTimeout(() => setFlash(false), 1200);
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

  const jumpToAlerts = () => {
    setOpen(false);
    setUnread(0);
    if (user?.role === "system_administrator") {
      nav("/ingest");
      try { localStorage.setItem("dvas_ingest_tab", "alerts"); } catch (e) {}
      window.dispatchEvent(new CustomEvent("dvas:ingest-tab", { detail: "alerts" }));
    } else if (user?.role === "drone_operator") {
      nav("/edge/incidents");
    } else {
      nav("/command");
      try { localStorage.setItem("dvas_cmd_tab", "alerts"); } catch (e) { /* noop */ }
      window.dispatchEvent(new CustomEvent("dvas:cmd-tab", { detail: "alerts" }));
    }
  };

  const handleItemClick = (item) => {
    setOpen(false);
    setUnread(0);
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
    nav("/command");
    try { localStorage.setItem("dvas_cmd_tab", "alerts"); } catch (e) {}
    window.dispatchEvent(new CustomEvent("dvas:cmd-tab", { detail: "alerts" }));
  };

  const filtered = notifications.filter((n) => {
    if (filter === "all") return true;
    if (filter === "suggestions") return n.type === "model_suggestion";
    if (filter === "incidents") return n.type === "incident";
    if (filter === "alerts") return n.type === "alert";
    return true;
  });

  const preview = filtered.slice(0, 15);

  return (
    <div className="relative" ref={rootRef}>
      <button
        data-testid="notification-bell"
        onClick={() => { setOpen((o) => !o); if (!open) setUnread(0); }}
        title={`${unread} unread alerts & model suggestions`}
        className="relative h-9 w-9 rounded-lg flex items-center justify-center transition-colors hover:bg-[color:var(--dvas-elev)]"
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
              {unread > 0 && (
                <button
                  onClick={() => setUnread(0)}
                  className="text-[10px] text-sky-400 hover:text-sky-300 transition-colors"
                >
                  Mark read
                </button>
              )}
            </div>
          </div>

          {/* Quick Filter Tabs */}
          <div className="flex items-center gap-1 px-3 py-1.5 bg-slate-950/40 border-b border-slate-800/80 text-[11px] overflow-x-auto">
            <button
              onClick={() => setFilter("all")}
              className={`px-2 py-0.5 rounded transition-colors ${
                filter === "all" ? "bg-slate-700 text-white font-medium" : "text-slate-400 hover:text-slate-200"
              }`}
            >
              All ({notifications.length})
            </button>
            <button
              onClick={() => setFilter("suggestions")}
              className={`px-2 py-0.5 rounded transition-colors flex items-center gap-1 ${
                filter === "suggestions" ? "bg-sky-600/30 text-sky-300 border border-sky-500/40 font-medium" : "text-slate-400 hover:text-slate-200"
              }`}
            >
              <Cpu size={11} className="text-sky-400" />
              Suggestions ({notifications.filter(n => n.type === "model_suggestion").length})
            </button>
            <button
              onClick={() => setFilter("incidents")}
              className={`px-2 py-0.5 rounded transition-colors flex items-center gap-1 ${
                filter === "incidents" ? "bg-amber-600/30 text-amber-300 border border-amber-500/40 font-medium" : "text-slate-400 hover:text-slate-200"
              }`}
            >
              <AlertTriangle size={11} className="text-amber-400" />
              Incidents ({notifications.filter(n => n.type === "incident").length})
            </button>
            <button
              onClick={() => setFilter("alerts")}
              className={`px-2 py-0.5 rounded transition-colors ${
                filter === "alerts" ? "bg-purple-600/30 text-purple-300 border border-purple-500/40 font-medium" : "text-slate-400 hover:text-slate-200"
              }`}
            >
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
            className="w-full px-4 py-2.5 text-xs font-semibold flex items-center justify-center gap-1.5 bg-slate-950/80 hover:bg-slate-800 text-sky-400 border-t border-slate-800 transition-colors"
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
  const { t } = useI18n();
  const nav = useNavigate();
  const items = NAV[user?.role] || [];

  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem(SB_KEY) === "1"; } catch (e) { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem(SB_KEY, collapsed ? "1" : "0"); } catch (e) { /* noop */ }
  }, [collapsed]);

  const doLogout = async () => {
    await logout();
    nav("/login");
  };

  const sidebarW = collapsed ? "w-16" : "w-64";

  return (
    <div
      className="h-screen overflow-hidden flex"
      style={{ background: "var(--dvas-root)", color: "var(--dvas-text)" }}
    >
      {/* Sidebar */}
      <aside
        className={`${sidebarW} shrink-0 flex flex-col transition-all duration-200 ease-out`}
        style={{
          background: "var(--dvas-surface)",
          borderRight: "1px solid var(--dvas-border)",
        }}
        data-testid="app-sidebar"
        data-collapsed={collapsed}
      >
        <div
          className={`h-16 px-4 flex items-center ${collapsed ? "justify-center" : "gap-2.5"}`}
        >
          <div
            className="w-10 h-10 flex items-center justify-center"
          >
            <img src="/kerala-police-logo.png" alt="Kerala Police Logo" className="max-w-full max-h-full object-contain" />
          </div>
          {!collapsed && (
            <div className="min-w-0">
              <div className="text-base font-bold tracking-tight" style={{ color: "var(--dvas-text)" }}>
                {t("app.title")}
              </div>
              <div className="text-[10px] font-medium" style={{ color: "var(--dvas-text-muted)" }}>
                {t("app.subtitle")}
              </div>
            </div>
          )}
        </div>

        <nav className="flex-1 px-3 py-2 flex flex-col gap-1">
          {items.map(({ to, labelKey, labelFallback, Icon }) => (
            <NavLink
              key={to}
              to={to}
              data-testid={`nav-${to.slice(1)}`}
              title={collapsed ? (labelFallback || t(labelKey)) : undefined}
              className={({ isActive }) =>
                `px-3 py-2.5 rounded-lg flex items-center ${collapsed ? "justify-center" : "gap-3"} text-sm font-medium transition-colors ${
                  isActive ? "font-semibold shadow-sm" : ""
                }`
              }
              style={({ isActive }) => ({
                background: isActive ? "var(--dvas-elev)" : "transparent",
                color: isActive ? "var(--dvas-blue)" : "var(--dvas-text-muted)",
              })}
            >
              <Icon size={18} />
              {!collapsed && <span>{labelFallback || t(labelKey)}</span>}
            </NavLink>
          ))}
        </nav>

        <div className="p-3 border-t" style={{ borderColor: "var(--dvas-border)" }}>
          <button
            data-testid="sidebar-toggle"
            onClick={() => setCollapsed((c) => !c)}
            className="w-full flex items-center justify-center gap-2 h-9 rounded-lg text-xs font-medium transition-colors hover:bg-[color:var(--dvas-elev)]"
            style={{ color: "var(--dvas-text-muted)" }}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {collapsed ? <PanelLeftOpen size={14} /> : <><PanelLeftClose size={14} /> Collapse</>}
          </button>
        </div>
      </aside>

      {/* Main column */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top bar */}
        <header
          className="h-16 flex items-center gap-4 px-6"
          style={{
            background: "var(--dvas-surface)",
            borderBottom: "1px solid var(--dvas-border)",
          }}
          data-testid="top-app-bar"
        >
          <div className="min-w-0 hidden sm:block">
            <h1
              className="text-lg font-bold tracking-tight truncate"
              data-testid="page-title"
              style={{ color: "var(--dvas-text)" }}
            >
              {title}
            </h1>
            {subtitle && (
              <div
                className="text-[11px] mt-0.5 truncate font-medium"
                data-testid="page-subtitle"
                style={{ color: "var(--dvas-text-muted)" }}
              >
                {subtitle}
              </div>
            )}
          </div>

          <div className="ml-auto flex items-center gap-2">
            <HealthDot />
            <NotificationBell />
            <LanguageToggle />
            <ThemeToggle />
            <UserMenu onLogout={doLogout} />
          </div>
        </header>

        <main className="flex-1 overflow-auto p-8 dvas-fade-in">{children}</main>
      </div>
    </div>
  );
}
