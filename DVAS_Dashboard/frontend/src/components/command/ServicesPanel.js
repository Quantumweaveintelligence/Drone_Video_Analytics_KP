/**
 * ServicesPanel.js
 * ───────────────────────────────────────────────────────────────
 * Multi-Drone View (v2 plan R8): beside the selected live tile, every analytics service. The ones
 * selected in Detection Models are highlighted and open their page on this drone's annotated feed
 * (same browser tab); the others are greyed out and do not navigate.
 *
 * Floats next to the tile (right, else left, else below), follows it while the grid moves, and
 * closes on Esc. GET /api/models only while it is open (15 s, as the Detection Models page).
 */

import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { serviceEntries, serviceRoute } from "@/lib/analyticsServices";
import {
  User as UserIcon,
  ShieldCheck,
  Car,
  Hash,
  Users as UsersIcon,
  Shield,
  Ship,
  ShieldAlert,
  Lock,
  ChevronRight,
  X,
  Layers,
  Settings2,
} from "lucide-react";

const ICONS = {
  person: UserIcon,
  face: ShieldCheck,
  vehicle: Car,
  plate: Hash,
  crowd: UsersIcon,
  intrusion: Shield,
  vessel: Ship,
  anomaly: ShieldAlert,
};

// Accent per service family: [solid, rgb for tints]
const FAMILY = {
  sky:     ["#0ea5e9", "14,165,233"],
  amber:   ["#f59e0b", "245,158,11"],
  blue:    ["#3b82f6", "59,130,246"],
  violet:  ["#8b5cf6", "139,92,246"],
  emerald: ["#10b981", "16,185,129"],
  rose:    ["#f43f5e", "244,63,94"],
  cyan:    ["#06b6d4", "6,182,212"],
  orange:  ["#f97316", "249,115,22"],
};

const STATE_CHIP = {
  live:     { text: "LIVE",     color: "#10b981", rgb: "16,185,129", pulse: true },
  degraded: { text: "DEGRADED", color: "#f59e0b", rgb: "245,158,11" },
  shedding: { text: "SHEDDING", color: "#f97316", rgb: "249,115,22" },
  starting: { text: "STARTING", color: "#64748b", rgb: "100,116,139" },
  off:      { text: "OFF",      color: "#64748b", rgb: "100,116,139" },
};

const PAGE_LABEL = { person: "Person", traffic: "Traffic", crowd: "Crowd", intrusion: "Intrusion", anomaly: "Anomaly" };

const MODELS_POLL_MS = 15000;
const PANEL_W = 320;
const GAP = 12;
const EDGE = 8;

// Roles that may change the model set (MODEL_ADMIN_ROLES in backend/routers/infra.py)
const MODEL_ADMIN_ROLES = ["control_room_officer", "senior_officer", "system_administrator"];

/** Where the panel goes for an anchor rect: right of it, else left, else below. */
export function placePanel(rect, panelH, vw, vh, w = PANEL_W) {
  let left;
  let top;
  let side;
  if (rect.right + GAP + w <= vw - EDGE) {
    left = rect.right + GAP;
    side = "right";
  } else if (rect.left - GAP - w >= EDGE) {
    left = rect.left - GAP - w;
    side = "left";
  } else {
    left = Math.min(Math.max(rect.left, EDGE), vw - w - EDGE);
    top = rect.bottom + GAP;
    side = "below";
  }
  if (side !== "below") top = Math.min(Math.max(rect.top, EDGE), Math.max(EDGE, vh - panelH - EDGE));
  return { left, top, side };
}

function useModels() {
  const [models, setModels] = useState(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const { data } = await api.get("/models");
        if (!cancelled) {
          setModels(data);
          setError(false);
        }
      } catch {
        if (!cancelled) setError(true);     // keep the last answer
      }
    };
    load();
    const t = setInterval(load, MODELS_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);
  return { models, error };
}

/** Follows the anchor's position every frame (the grid animates and scrolls); state only on change. */
function useAnchorPlacement(anchorEl, panelRef) {
  const [pos, setPos] = useState(null);
  useLayoutEffect(() => {
    if (!anchorEl) return undefined;
    let raf;
    let last = "";
    const tick = () => {
      const r = anchorEl.getBoundingClientRect();
      const h = panelRef.current?.offsetHeight || 420;
      const p = placePanel(r, h, window.innerWidth, window.innerHeight);
      const k = `${Math.round(p.left)}:${Math.round(p.top)}:${p.side}`;
      if (k !== last) {
        last = k;
        setPos(p);
      }
      raf = requestAnimationFrame(tick);
    };
    tick();
    return () => cancelAnimationFrame(raf);
  }, [anchorEl, panelRef]);
  return pos;
}

function StateChip({ entry }) {
  const c = STATE_CHIP[entry.state] || STATE_CHIP.starting;
  return (
    <span
      className="mono text-[9px] font-bold tracking-wider px-1.5 py-0.5 rounded-md flex items-center gap-1 whitespace-nowrap"
      style={{ color: c.color, background: `rgba(${c.rgb},0.12)`, border: `1px solid rgba(${c.rgb},0.28)` }}
    >
      {entry.state === "off" ? <Lock size={9} /> : (
        <span className={`w-1.5 h-1.5 rounded-full ${c.pulse ? "dvas-live-dot" : ""}`} style={{ background: c.color }} />
      )}
      {c.text}
      {entry.fps != null && <span className="opacity-80 font-semibold">· {entry.fps} fps</span>}
    </span>
  );
}

function ServiceRow({ entry, onOpen, canConfigure }) {
  const Icon = ICONS[entry.key] || Layers;
  const [solid, rgb] = FAMILY[entry.family] || FAMILY.sky;
  const page = PAGE_LABEL[entry.page] || entry.page;
  const title = entry.enabled
    ? `Open ${entry.label} on the ${page} page`
    : `${entry.label} is off — not selected in Detection Models${canConfigure ? "" : " (ask a senior officer)"}`;

  return (
    <button
      type="button"
      role="listitem"
      disabled={!entry.enabled}
      aria-disabled={!entry.enabled}
      aria-label={title}
      title={title}
      onClick={() => entry.enabled && onOpen(entry)}
      className={`group relative w-full flex items-center gap-3 px-2.5 py-2 rounded-xl text-left transition-all duration-200 focus:outline-none focus-visible:ring-2 ${
        entry.enabled ? "cursor-pointer hover:-translate-y-px" : "cursor-not-allowed opacity-50 grayscale"
      }`}
      style={entry.enabled ? {
        background: `linear-gradient(135deg, rgba(${rgb},0.14), rgba(${rgb},0.04))`,
        border: `1px solid rgba(${rgb},0.32)`,
        "--tw-ring-color": `rgba(${rgb},0.6)`,
      } : {
        background: "var(--dvas-surface-2)",
        border: "1px dashed var(--dvas-border)",
      }}
      onMouseEnter={(e) => { if (entry.enabled) e.currentTarget.style.boxShadow = `0 6px 20px -6px rgba(${rgb},0.55)`; }}
      onMouseLeave={(e) => { e.currentTarget.style.boxShadow = "none"; }}
    >
      <span
        className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0 transition-transform duration-200 group-hover:scale-105"
        style={entry.enabled
          ? { background: `rgba(${rgb},0.18)`, color: solid, boxShadow: `inset 0 0 0 1px rgba(${rgb},0.35)` }
          : { background: "var(--dvas-surface)", color: "var(--dvas-text-muted)", boxShadow: "inset 0 0 0 1px var(--dvas-border)" }}
      >
        <Icon size={17} />
      </span>
      <span className="flex-1 min-w-0">
        <span className="block text-[12px] font-semibold leading-snug truncate" style={{ color: "var(--dvas-text)" }}>
          {entry.label}
        </span>
        <span className="flex items-center gap-1.5 mt-1 min-w-0">
          <StateChip entry={entry} />
          <span className="mono text-[9px] truncate" style={{ color: "var(--dvas-text-muted)" }}>
            {entry.enabled ? `${page} page` : "Not in Detection Models"}
          </span>
        </span>
      </span>
      <ChevronRight
        size={14}
        className={`flex-shrink-0 transition-all duration-200 ${entry.enabled ? "opacity-50 group-hover:opacity-100 group-hover:translate-x-0.5" : "opacity-0"}`}
        style={{ color: solid }}
      />
    </button>
  );
}

export default function ServicesPanel({ drone, anchorEl, analytics, user, onClose }) {
  const navigate = useNavigate();
  const panelRef = useRef(null);
  const pos = useAnchorPlacement(anchorEl, panelRef);
  const { models, error } = useModels();
  const entries = serviceEntries(models, analytics);
  const enabled = entries.filter((e) => e.enabled).length;
  const canConfigure = MODEL_ADMIN_ROLES.includes(user?.role);

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!anchorEl) return null;

  const slide = pos?.side === "left" ? "slide-in-from-right-3" : pos?.side === "below" ? "slide-in-from-top-3" : "slide-in-from-left-3";

  return createPortal(
    <div
      ref={panelRef}
      role="dialog"
      aria-label={`Analytics services for ${drone.id}`}
      data-testid="multidrone-services-panel"
      onClick={(e) => e.stopPropagation()}
      className={`fixed z-50 rounded-2xl overflow-hidden animate-in fade-in ${slide} duration-200`}
      style={{
        width: PANEL_W,
        left: pos?.left ?? -9999,
        top: pos?.top ?? -9999,
        visibility: pos ? "visible" : "hidden",
        maxHeight: "calc(100vh - 16px)",
        background: "color-mix(in srgb, var(--dvas-surface) 86%, transparent)",
        backdropFilter: "blur(18px) saturate(1.4)",
        WebkitBackdropFilter: "blur(18px) saturate(1.4)",
        border: "1px solid var(--dvas-border)",
        boxShadow: "0 24px 48px -12px rgba(2,6,23,0.45), 0 0 0 1px rgba(20,184,166,0.12)",
        fontFamily: "'Plus Jakarta Sans', sans-serif",
      }}
    >
      {/* accent bar */}
      <div className="h-[3px]" style={{ background: "linear-gradient(90deg,#14b8a6,#0ea5e9,#8b5cf6,#f43f5e)" }} />

      {/* Header */}
      <div className="px-4 pt-3 pb-2.5 flex items-start gap-3" style={{ borderBottom: "1px solid var(--dvas-border)" }}>
        <div
          className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0"
          style={{ background: "linear-gradient(135deg, rgba(20,184,166,0.25), rgba(14,165,233,0.2))", color: "#14b8a6", boxShadow: "inset 0 0 0 1px rgba(20,184,166,0.35)" }}
        >
          <Layers size={17} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-[13px] font-bold leading-tight" style={{ color: "var(--dvas-text)" }}>Analytics services</div>
          <div className="flex items-center gap-1.5 mt-1 flex-wrap">
            <span className="mono text-[10px] font-bold px-1.5 py-0.5 rounded" style={{ color: "#14b8a6", background: "rgba(20,184,166,0.12)", border: "1px solid rgba(20,184,166,0.3)" }}>
              {drone.id}
            </span>
            <span className="mono text-[9px] font-bold px-1.5 py-0.5 rounded flex items-center gap-1" style={{ color: "#10b981", background: "rgba(16,185,129,0.12)" }}>
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 dvas-live-dot" /> LIVE
            </span>
            {drone.mission_id && (
              <span className="mono text-[9px] truncate max-w-[120px]" style={{ color: "var(--dvas-text-muted)" }} title={drone.mission_id}>
                {drone.mission_id}
              </span>
            )}
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close services panel"
          className="w-7 h-7 rounded-lg flex items-center justify-center transition-colors hover:bg-slate-500/15"
          style={{ color: "var(--dvas-text-muted)" }}
        >
          <X size={15} />
        </button>
      </div>

      {/* Summary */}
      <div className="px-4 py-2 flex items-center justify-between mono text-[10px]" style={{ color: "var(--dvas-text-muted)" }}>
        <span>
          {models ? <><span className="font-bold" style={{ color: "var(--dvas-text)" }}>{enabled}</span> of {entries.length} enabled</> : error ? "Model selection unavailable" : "Loading model selection…"}
        </span>
        <span>Click to open the feed</span>
      </div>

      {/* Services */}
      <div role="list" className="px-3 pb-3 flex flex-col gap-1.5 overflow-y-auto dvas-scrollbar" style={{ maxHeight: "calc(100vh - 160px)" }}>
        {entries.map((e) => (
          <ServiceRow
            key={e.key}
            entry={e}
            canConfigure={canConfigure}
            onOpen={(svc) => navigate(serviceRoute(svc, drone.id))}
          />
        ))}
      </div>

      {canConfigure && enabled < entries.length && (
        <button
          type="button"
          onClick={() => navigate("/command/models")}
          className="w-full px-4 py-2.5 flex items-center justify-center gap-1.5 text-[11px] font-semibold transition-colors hover:bg-teal-500/10"
          style={{ borderTop: "1px solid var(--dvas-border)", color: "#14b8a6" }}
        >
          <Settings2 size={13} /> Enable more in Detection Models
          <ChevronRight size={13} />
        </button>
      )}
    </div>,
    document.body,
  );
}
