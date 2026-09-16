import React from "react";
import { NavLink } from "react-router-dom";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useI18n } from "@/context/I18nContext";

export default function AppSidebar({ sections = [], collapsed, onToggleCollapse }) {
  const { t } = useI18n();

  return (
    <aside
      className="h-full flex flex-col transition-all duration-200 ease-out z-40 select-none"
      style={{
        width: collapsed ? "var(--dvas-sidebar-collapsed)" : "var(--dvas-sidebar-width)",
        backgroundColor: "var(--dvas-surface)",
        borderRight: "1px solid var(--dvas-border)",
        boxShadow: "0 0 15px rgba(0, 0, 0, 0.03)"
      }}
      data-testid="app-sidebar"
      data-collapsed={collapsed}
    >
      {/* Brand Header */}
      <div
        className={`h-navbar px-4 flex items-center shrink-0 border-b ${
          collapsed ? "justify-center" : "gap-3"
        }`}
        style={{ borderColor: "var(--dvas-border)" }}
      >
        <div className="w-9 h-9 shrink-0 flex items-center justify-center">
          <img
            src="/kerala-police-logo.png"
            alt="Kerala Police Logo"
            className="max-w-full max-h-full object-contain"
          />
        </div>
        {!collapsed && (
          <div className="min-w-0 flex flex-col justify-center">
            <div
              className="text-base font-extrabold tracking-tight truncate leading-tight font-sans"
              style={{ color: "var(--dvas-text)" }}
            >
              {t("app.title")}
            </div>
            <div
              className="text-[10px] font-semibold uppercase tracking-wider truncate mt-0.5"
              style={{ color: "var(--dvas-text-dim)" }}
            >
              {t("app.subtitle")}
            </div>
          </div>
        )}
      </div>

      {/* Nav Menu Items grouped by Section */}
      <nav className="flex-1 overflow-y-auto px-3 py-3 flex flex-col gap-4 no-scrollbar">
        {sections.map((sec, secIdx) => (
          <div key={sec.section || secIdx} className="flex flex-col gap-1">
            {!collapsed && sec.section && (
              <div
                className="px-3 pt-1 pb-1 text-[10px] font-bold uppercase tracking-widest truncate"
                style={{ color: "var(--dvas-text-dim)" }}
              >
                {t(sec.sectionKey) || sec.section}
              </div>
            )}
            {collapsed && sec.section && secIdx > 0 && (
              <div
                className="mx-auto my-1 w-6 border-t"
                style={{ borderColor: "var(--dvas-border)" }}
              />
            )}

            {sec.items.map(({ key, path, labelKey, fallback, Icon }) => {
              const label = t(labelKey) || fallback;
              return (
                <NavLink
                  key={path}
                  to={path}
                  data-testid={`nav-${key}`}
                  title={collapsed ? label : undefined}
                  className={({ isActive }) =>
                    `dvas-menu-link px-3 py-2 flex items-center ${
                      collapsed ? "justify-center" : "gap-3"
                    } text-xs font-medium transition-all ${
                      isActive ? "active" : "hover:bg-[color:var(--dvas-elev)]"
                    }`
                  }
                  style={({ isActive }) => ({
                    color: isActive ? "var(--dvas-accent)" : "var(--dvas-text-muted)",
                  })}
                >
                  <Icon size={18} className="shrink-0" />
                  {!collapsed && (
                    <span className="truncate font-medium">{label}</span>
                  )}
                </NavLink>
              );
            })}
          </div>
        ))}
      </nav>

      {/* Collapse Toggle Footer */}
      <div
        className="p-3 border-t shrink-0"
        style={{ borderColor: "var(--dvas-border)" }}
      >
        <button
          data-testid="sidebar-toggle"
          onClick={onToggleCollapse}
          className="w-full flex items-center justify-center gap-2 h-9 rounded-md text-xs font-semibold transition-colors hover:bg-[color:var(--dvas-elev)] cursor-pointer"
          style={{ color: "var(--dvas-text-muted)" }}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? (
            <PanelLeftOpen size={16} />
          ) : (
            <>
              <PanelLeftClose size={16} />
              <span>Collapse Menu</span>
            </>
          )}
        </button>
      </div>
    </aside>
  );
}
