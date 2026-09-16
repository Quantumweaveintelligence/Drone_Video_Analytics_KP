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
        <div className="w-10 h-10 shrink-0 flex items-center justify-center">
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
              {t("app.title", "DVAS")}
            </div>
            <div
              className="text-[11px] font-semibold uppercase tracking-wider truncate mt-0.5"
              style={{ color: "var(--dvas-text-dim)" }}
            >
              {t("app.subtitle", "Kerala Police")}
            </div>
          </div>
        )}
      </div>

      {/* Nav Menu Items grouped by Section */}
      <nav className="flex-1 overflow-y-auto px-3 py-3 flex flex-col gap-4 no-scrollbar">
        {sections.map((sec, secIdx) => {
          const sectionTitle = t(sec.sectionKey, sec.section) || sec.section;
          return (
            <div key={sec.section || secIdx} className="flex flex-col gap-1">
              {!collapsed && sectionTitle && (
                <div
                  className="px-3.5 pt-1.5 pb-1 text-[11px] font-bold uppercase tracking-wider truncate"
                  style={{ color: "var(--dvas-text-dim)" }}
                >
                  {sectionTitle}
                </div>
              )}
              {collapsed && sectionTitle && secIdx > 0 && (
                <div
                  className="mx-auto my-1.5 w-6 border-t"
                  style={{ borderColor: "var(--dvas-border)" }}
                />
              )}

              {sec.items.map(({ key, path, labelKey, fallback, Icon }) => {
                const label = t(labelKey, fallback) || fallback;
                return (
                  <NavLink
                    key={path}
                    to={path}
                    data-testid={`nav-${key}`}
                    title={collapsed ? label : undefined}
                    className={({ isActive }) =>
                      `dvas-menu-link px-3.5 py-2.5 flex items-center ${
                        collapsed ? "justify-center" : "gap-3"
                      } text-[13.5px] font-semibold transition-all rounded-md ${
                        isActive ? "active font-bold" : "hover:bg-[color:var(--dvas-elev)]"
                      }`
                    }
                    style={({ isActive }) => ({
                      color: isActive ? "var(--dvas-accent)" : "var(--dvas-text)",
                    })}
                  >
                    <Icon size={20} className="shrink-0" />
                    {!collapsed && (
                      <span className="truncate font-medium text-sm leading-snug">{label}</span>
                    )}
                  </NavLink>
                );
              })}
            </div>
          );
        })}
      </nav>

      {/* Collapse Toggle Footer */}
      <div
        className="p-3 border-t shrink-0"
        style={{ borderColor: "var(--dvas-border)" }}
      >
        <button
          data-testid="sidebar-toggle"
          onClick={onToggleCollapse}
          className="w-full flex items-center justify-center gap-2 h-10 rounded-md text-xs font-semibold transition-colors hover:bg-[color:var(--dvas-elev)] cursor-pointer"
          style={{ color: "var(--dvas-text-muted)" }}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? (
            <PanelLeftOpen size={18} />
          ) : (
            <>
              <PanelLeftClose size={18} />
              <span className="font-medium text-xs">Collapse Menu</span>
            </>
          )}
        </button>
      </div>
    </aside>
  );
}
