import React from "react";
import { Menu } from "lucide-react";

export default function AppTopbar({ title, subtitle, onMobileMenuToggle, children }) {
  return (
    <header
      className="h-navbar flex items-center justify-between px-6 shrink-0 z-30 transition-colors"
      style={{
        background: "color-mix(in srgb, var(--dvas-topbar) 88%, transparent)",
        backdropFilter: "blur(10px)",
        WebkitBackdropFilter: "blur(10px)",
        borderBottom: "1px solid var(--dvas-border)",
        boxShadow: "0 1px 4px 0 rgba(67, 89, 113, 0.06)"
      }}
      data-testid="top-app-bar"
    >
      {/* Left: Mobile menu toggle + Page title & subtitle */}
      <div className="flex items-center gap-3 min-w-0">
        {onMobileMenuToggle && (
          <button
            onClick={onMobileMenuToggle}
            className="lg:hidden p-1.5 rounded-lg text-slate-500 hover:bg-[color:var(--dvas-elev)] transition-colors"
            title="Toggle Menu"
            data-testid="mobile-menu-toggle"
          >
            <Menu size={20} />
          </button>
        )}
        <div className="min-w-0">
          {title && (
            <h1
              className="text-base font-bold tracking-tight truncate leading-tight font-sans"
              data-testid="page-title"
              style={{ color: "var(--dvas-text)" }}
            >
              {title}
            </h1>
          )}
          {subtitle && (
            <div
              className="text-[11px] font-medium truncate mt-0.5"
              data-testid="page-subtitle"
              style={{ color: "var(--dvas-text-muted)" }}
            >
              {subtitle}
            </div>
          )}
        </div>
      </div>

      {/* Right: Actions slot */}
      <div className="flex items-center gap-2 shrink-0">
        {children}
      </div>
    </header>
  );
}
