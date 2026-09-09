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
  Video, Compass, Clock, Scan, Image, Archive, Server, ClipboardList, Settings
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
  const [alerts, setAlerts] = useState([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [flash, setFlash] = useState(false);
  const rootRef = useRef(null);

  useDvasSocket((msg) => {
    if (msg.type !== "alert" || !msg.alert) return;
    if (!user) return;
    const a = msg.alert;
    // Admin & SR see all alerts; CRO sees only alerts within their assigned mission scope
    const inScope =
      user.role === "system_administrator" ||
      user.role === "senior_officer" ||
      ((user.mission_scope || []).includes(a.mission_id));
    if (!inScope) return;
    setAlerts((prev) => [a, ...prev].slice(0, 20));
    setUnread((c) => c + 1);
    setFlash(true);
    setTimeout(() => setFlash(false), 1200);
  });

  useEffect(() => {
    if (!open) return;
    const onDoc = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  if (!user || user.role === "drone_operator") return null;

  const jumpToAlerts = () => {
    setOpen(false);
    setUnread(0);
    if (user?.role === "system_administrator") {
      nav("/ingest");
      try { localStorage.setItem("dvas_ingest_tab", "alerts"); } catch (e) {}
      window.dispatchEvent(new CustomEvent("dvas:ingest-tab", { detail: "alerts" }));
    } else {
      nav("/command");
      try { localStorage.setItem("dvas_cmd_tab", "alerts"); } catch (e) { /* noop */ }
      window.dispatchEvent(new CustomEvent("dvas:cmd-tab", { detail: "alerts" }));
    }
  };

  const preview = alerts.slice(0, 5);

  return (
    <div className="relative" ref={rootRef}>
      <button
        data-testid="notification-bell"
        onClick={() => { setOpen((o) => !o); if (!open) setUnread(0); }}
        title={`${unread} unread`}
        className="relative h-9 w-9 rounded-lg flex items-center justify-center transition-colors hover:bg-[color:var(--dvas-elev)]"
        style={{ color: "var(--dvas-text-muted)" }}
      >
        <Bell size={16} className={flash ? "dvas-live-dot" : ""} />
        {unread > 0 && (
          <span
            data-testid="notification-count"
            className="absolute top-1.5 right-1.5 w-2 h-2 rounded-full dvas-live-dot"
            style={{ background: "var(--dvas-blue)" }}
          />
        )}
      </button>

      {open && (
        <div
          data-testid="notification-drawer"
          className="absolute right-0 top-11 z-50 w-80 dvas-fade-in overflow-hidden"
          style={{
            background: "var(--dvas-surface)",
            border: "1px solid var(--dvas-border)",
            borderRadius: "var(--dvas-radius)",
            boxShadow: "var(--dvas-shadow-lg)",
          }}
        >
          <div
            className="px-4 py-3 flex items-center gap-2 text-sm font-semibold"
            style={{ borderBottom: "1px solid var(--dvas-border)" }}
          >
            <Bell size={14} style={{ color: "var(--dvas-warn)" }} />
            {t("topbar.notifications")}
            <span className="ml-auto text-[10px] font-medium mono" style={{ color: "var(--dvas-text-dim)" }}>
              {preview.length}/{alerts.length}
            </span>
          </div>

          {preview.length === 0 ? (
            <div
              data-testid="notification-empty"
              className="px-4 py-8 text-center text-xs"
              style={{ color: "var(--dvas-text-dim)" }}
            >
              {t("topbar.noAlerts")}
            </div>
          ) : (
            <ul className="max-h-72 overflow-auto">
              {preview.map((a, i) => (
                <li
                  key={`${a.id || i}-${a.ts}`}
                  data-testid={`notif-item-${i}`}
                  className="px-4 py-3 flex items-start gap-2.5 text-xs hover:bg-[color:var(--dvas-elev)] transition-colors"
                  style={{ borderBottom: "1px solid var(--dvas-border)" }}
                >
                  <AlertTriangle size={14} style={{
                    color: a.severity === "critical" ? "var(--dvas-crit)"
                         : a.severity === "warning" ? "var(--dvas-warn)"
                         : "var(--dvas-blue)",
                    marginTop: 2,
                  }} />
                  <div className="flex-1 min-w-0">
                    <div className="font-medium truncate" style={{ color: "var(--dvas-text)" }}>
                      {a.type || a.title || "alert"}
                    </div>
                    <div className="mono text-[10px] mt-0.5" style={{ color: "var(--dvas-text-dim)" }}>
                      {a.drone_id || "—"} · {a.zone || ""} · {new Date(a.ts).toLocaleTimeString()}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}

          <button
            data-testid="notification-viewall"
            onClick={jumpToAlerts}
            className="w-full px-4 py-3 text-xs font-semibold flex items-center justify-center gap-1.5 hover:bg-[color:var(--dvas-elev)] transition-colors"
            style={{ borderTop: "1px solid var(--dvas-border)", color: "var(--dvas-blue)" }}
          >
            {t("topbar.viewAllAlerts")} →
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
