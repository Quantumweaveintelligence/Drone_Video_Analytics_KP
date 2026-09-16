import React from "react";
import { Outlet } from "react-router-dom";
import AppShell from "@/components/layout/AppShell";
import { CommandProvider, useCommand } from "@/context/CommandContext";
import { useAuth } from "@/context/AuthContext";
import { useI18n } from "@/context/I18nContext";
import { API } from "@/lib/api";
import {
  X,
  Play,
  FolderLock,
  Image as ImageIcon
} from "lucide-react";

function KPI({ label, value, tone = "text-sky-500" }) {
  return (
    <div
      className="p-4 rounded-lg flex flex-col justify-between transition-all"
      style={{
        background: "var(--dvas-surface)",
        border: "1px solid var(--dvas-border)",
        boxShadow: "var(--dvas-shadow)"
      }}
    >
      <div className="dvas-label text-[10px] font-bold uppercase tracking-wider">{label}</div>
      <div className={`mono text-2xl font-extrabold mt-1 ${tone}`}>{value ?? "—"}</div>
    </div>
  );
}

const resolveMinioBucket = (key, bucket) => {
  if (bucket && bucket.trim()) return bucket.trim();
  if (!key) return "dvas-faces";
  if (key.startsWith("vehicles/")) return "dvas-vehicles";
  if (key.startsWith("plates/")) return "dvas-plates";
  if (key.startsWith("intrusion/")) return "dvas-intrusion";
  if (key.startsWith("heatmaps/")) return "dvas-heatmaps";
  if (key.startsWith("persons/") || key.startsWith("faces/") || key.startsWith("alerts/")) return "dvas-faces";
  return "dvas-faces";
};

function CommandInner() {
  const { user } = useAuth();
  const { t } = useI18n();
  const {
    kpis,
    playing,
    setPlaying,
    evidenceDetail,
    setEvidenceDetail,
    requestOn,
    setRequestOn,
    justification,
    setJustification,
    requestExport,
    directExport
  } = useCommand();

  return (
    <div className="flex flex-col gap-5">
      {/* ── GLOBAL HEADER ── */}
      <div
        className="p-4 rounded-lg flex flex-col md:flex-row md:items-center justify-between gap-2 border-l-4"
        style={{
          background: "var(--dvas-surface)",
          borderLeftColor: "var(--dvas-accent)",
          borderRight: "1px solid var(--dvas-border)",
          borderTop: "1px solid var(--dvas-border)",
          borderBottom: "1px solid var(--dvas-border)",
          boxShadow: "var(--dvas-shadow)"
        }}
      >
        <h1 className="text-lg font-extrabold tracking-tight uppercase font-sans" style={{ color: "var(--dvas-text)" }}>
          Thiruvananthapuram{" "}
          <span className="mx-2 font-light" style={{ color: "var(--dvas-text-dim)" }}>|</span>{" "}
          <span className="text-xs font-semibold">
            {new Date().toLocaleString("en-US", {
              month: "long",
              day: "numeric",
              year: "numeric",
              hour: "numeric",
              minute: "numeric"
            })}
          </span>
        </h1>
        <div className="font-bold uppercase tracking-widest text-[11px]" style={{ color: "var(--dvas-accent)" }}>
          Kerala Police Command Center
        </div>
      </div>

      {/* ── KPI ROW ── */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <KPI label={t("cmd.kpi.dronesOnline")} value={kpis.drones_online} tone="text-emerald-500" />
        <KPI label={t("cmd.kpi.activeMissions")} value={kpis.active_missions} tone="text-sky-500" />
        <KPI label={t("cmd.kpi.alertsScope")} value={kpis.alerts_24h} tone="text-amber-500" />
        <KPI label={t("cmd.kpi.evidenceItems")} value={kpis.evidence_total} tone="text-purple-500" />
        <KPI label={t("cmd.kpi.exportsPending")} value={kpis.exports_pending} tone="text-red-500" />
      </div>

      {/* ── ACTIVE SUB-ROUTE VIEW ── */}
      <div className="min-w-0">
        <Outlet />
      </div>

      {/* ── Playback Modal ── */}
      {playing && (
        <div className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-6" onClick={() => setPlaying(null)}>
          <div className="border border-slate-700 bg-slate-950 max-w-4xl w-full rounded-xl overflow-hidden" onClick={e => e.stopPropagation()}>
            <div className="p-3 border-b border-slate-800 flex items-center justify-between">
              <div className="text-sm font-semibold text-slate-100">{playing.label}</div>
              <button data-testid="close-player" onClick={() => setPlaying(null)} className="text-slate-400 hover:text-white p-1">
                <X size={16} />
              </button>
            </div>
            {(playing.primary_image_key || playing.thumbnail) ? (
              <div className="w-full aspect-video bg-slate-900 flex items-center justify-center gap-4 p-4">
                <div className={`relative flex flex-col items-center justify-center h-full ${playing.secondary_image_key ? 'max-w-[48%]' : 'max-w-[85%]'}`}>
                  <img
                    src={`/api/minio/image?bucket=${encodeURIComponent(resolveMinioBucket(playing.primary_image_key || playing.thumbnail, playing.image_bucket))}&key=${encodeURIComponent(playing.primary_image_key || playing.thumbnail)}`}
                    alt="Primary Evidence"
                    className="max-h-[calc(100%-24px)] object-contain rounded border border-slate-700 shadow-md"
                    onError={(e) => {
                      e.target.onerror = null;
                      e.target.style.display = 'none';
                      const fb = e.target.parentElement.querySelector('.modal-img-fallback');
                      if (fb) fb.style.display = 'flex';
                    }}
                  />
                  <div className="modal-img-fallback hidden flex-col items-center justify-center p-4 text-slate-500 border border-slate-800 rounded bg-slate-950">
                    <ImageIcon size={24} className="text-slate-600 mb-1" />
                    <span className="text-[10px] mono">Primary capture unavailable</span>
                  </div>
                  <span className="text-[10px] mono text-slate-400 mt-1 uppercase tracking-wider">
                    {playing.event_type === "vehicle_detected" ? "Vehicle Crop" : playing.event_type === "person_detected" ? "Full Body Crop" : playing.event_type?.includes("crowd") ? "Density Heatmap" : "Primary Capture"}
                  </span>
                </div>
                {playing.secondary_image_key && (
                  <div className="relative flex flex-col items-center justify-center h-full max-w-[48%]">
                    <img
                      src={`/api/minio/image?bucket=${encodeURIComponent(resolveMinioBucket(playing.secondary_image_key, playing.secondary_image_key.startsWith('plates/') ? 'dvas-plates' : (playing.image_bucket === 'dvas-vehicles' ? 'dvas-plates' : 'dvas-faces')))}&key=${encodeURIComponent(playing.secondary_image_key)}`}
                      alt="Secondary Evidence"
                      className="max-h-[calc(100%-24px)] object-contain rounded border border-slate-700 shadow-md"
                      onError={(e) => {
                        e.target.onerror = null;
                        e.target.style.display = 'none';
                        const fb = e.target.parentElement.querySelector('.modal-img-fallback-sec');
                        if (fb) fb.style.display = 'flex';
                      }}
                    />
                    <div className="modal-img-fallback-sec hidden flex-col items-center justify-center p-4 text-slate-500 border border-slate-800 rounded bg-slate-950">
                      <ImageIcon size={24} className="text-slate-600 mb-1" />
                      <span className="text-[10px] mono">Secondary capture unavailable</span>
                    </div>
                    <span className="text-[10px] mono text-slate-400 mt-1 uppercase tracking-wider">
                      {playing.event_type === "vehicle_detected" ? "License Plate Crop" : "Secondary Detection"}
                    </span>
                  </div>
                )}
              </div>
            ) : (
              <div className="p-8 text-center text-slate-500 mono text-xs">No media available for this detection</div>
            )}
          </div>
        </div>
      )}

      {/* ── Evidence Detail Modal ── */}
      {evidenceDetail && (
        <div className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-6" onClick={() => setEvidenceDetail(null)}>
          <div className="border border-slate-700 bg-slate-950 max-w-3xl w-full max-h-[85vh] overflow-y-auto rounded-xl" onClick={e => e.stopPropagation()}>
            <div className="p-4 border-b border-slate-800 flex items-center justify-between">
              <div className="text-sm font-semibold" style={{ color: "var(--dvas-text)" }}>
                Evidence Detail — {evidenceDetail.evidence?.id}
              </div>
              <button onClick={() => setEvidenceDetail(null)} className="text-slate-400 hover:text-white p-1">
                <X size={16} />
              </button>
            </div>
            <div className="p-4 space-y-4">
              <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs">
                {[
                  ["Label", evidenceDetail.evidence?.label],
                  ["Event Type", evidenceDetail.evidence?.event_type],
                  ["Drone", evidenceDetail.evidence?.drone_id],
                  ["Mission", evidenceDetail.evidence?.mission_id],
                  ["Confidence", evidenceDetail.evidence?.confidence],
                  ["Duration", `${evidenceDetail.evidence?.duration_s}s`],
                  ["Jurisdiction", evidenceDetail.evidence?.jurisdiction],
                  ["Captured", evidenceDetail.evidence?.ts ? new Date(evidenceDetail.evidence.ts).toLocaleString() : "-"],
                  ["Legal Hold", evidenceDetail.evidence?.legal_hold ? `Yes — ${evidenceDetail.evidence.legal_hold_reason || ""}` : "No"],
                  ["Retention Until", evidenceDetail.evidence?.retention_until ? new Date(evidenceDetail.evidence.retention_until).toLocaleDateString() : "-"]
                ].map(([k, v]) => (
                  <div key={k}>
                    <span className="dvas-label text-[10px]">{k}</span>
                    <div className="mono text-slate-200 mt-0.5">{v || "—"}</div>
                  </div>
                ))}
              </div>

              {evidenceDetail.evidence?.legal_hold && (
                <div className="border border-red-500/30 bg-red-500/5 rounded p-3 text-xs">
                  <div className="text-red-400 font-semibold flex items-center gap-1">
                    <FolderLock size={12} /> Legal Hold Active
                  </div>
                  <div className="text-slate-400 mt-1">Reason: {evidenceDetail.evidence.legal_hold_reason}</div>
                  <div className="text-slate-500 mt-0.5">
                    Placed by: {evidenceDetail.evidence.legal_hold_by} on{" "}
                    {evidenceDetail.evidence.legal_hold_at ? new Date(evidenceDetail.evidence.legal_hold_at).toLocaleString() : "-"}
                  </div>
                </div>
              )}

              {evidenceDetail.exports?.length > 0 && (
                <div>
                  <div className="dvas-label text-[10px] mb-2">Associated Exports</div>
                  <div className="space-y-1">
                    {evidenceDetail.exports.map(x => (
                      <div key={x.id} className="flex items-center justify-between text-xs border border-slate-800 rounded p-2">
                        <div className="mono text-slate-300">{x.id}</div>
                        <div className="text-slate-400">{x.requested_by}</div>
                        <span className={`px-1.5 py-0.5 border text-[10px] ${
                          x.status === "pending" ? "text-amber-400 border-amber-500/40" :
                          x.status === "approved" ? "text-emerald-400 border-emerald-500/40" :
                          "text-red-400 border-red-500/40"
                        }`}>
                          {x.status}
                        </span>
                        {x.status === "approved" && (
                          <a
                            href={`${API}/exports/${x.id}/bundle?token=${encodeURIComponent(localStorage.getItem("dvas_token") || "")}`}
                            className="text-sky-400 hover:text-sky-300 text-[10px]"
                          >
                            Download 65B Bundle
                          </a>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="flex flex-wrap gap-2 pt-2 border-t border-slate-800">
                <button
                  onClick={() => { setPlaying(evidenceDetail.evidence); setEvidenceDetail(null); }}
                  className="px-3 py-1.5 border border-sky-600/60 text-sky-300 hover:bg-sky-500/10 text-xs flex items-center gap-1 rounded"
                >
                  <Play size={12} /> View Evidence
                </button>
                {user?.role === "control_room_officer" && (
                  <button
                    onClick={() => { setRequestOn(evidenceDetail.evidence); setEvidenceDetail(null); }}
                    className="px-3 py-1.5 border border-slate-700 text-slate-300 hover:border-sky-500 text-xs rounded"
                  >
                    Request Export
                  </button>
                )}
                {user?.role === "senior_officer" && (
                  <button
                    onClick={() => { directExport(evidenceDetail.evidence); setEvidenceDetail(null); }}
                    className="px-3 py-1.5 border border-emerald-600/60 text-emerald-300 hover:bg-emerald-500/10 text-xs rounded"
                  >
                    Direct Export
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Export Request Modal ── */}
      {requestOn && (
        <div className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-6" onClick={() => setRequestOn(null)}>
          <div className="border border-slate-700 bg-slate-950 max-w-md w-full p-4 rounded-xl" onClick={e => e.stopPropagation()}>
            <div className="dvas-label">Request Export</div>
            <div className="text-sm mt-1 mb-3 text-slate-200">{requestOn.label}</div>
            <label className="dvas-label block mb-1">Justification</label>
            <textarea
              data-testid="input-justification"
              value={justification}
              onChange={e => setJustification(e.target.value)}
              rows={4}
              className="w-full bg-slate-900 border border-slate-700 focus:border-sky-500 outline-none p-2 text-sm mono rounded"
              placeholder="Cr. No / Case ref / purpose…"
            />
            <div className="mt-3 flex justify-end gap-2 text-xs">
              <button onClick={() => setRequestOn(null)} className="px-3 py-1.5 border border-slate-700 text-slate-300 rounded">
                Cancel
              </button>
              <button data-testid="submit-export-request" onClick={requestExport} className="px-3 py-1.5 bg-sky-600 hover:bg-sky-500 text-white rounded font-semibold">
                Submit
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function CommandShell() {
  const { user } = useAuth();
  const { t } = useI18n();

  return (
    <CommandProvider>
      <AppShell
        title={t("cmd.title")}
        subtitle={user?.role === "senior_officer" ? t("cmd.subtitle.aggregated") : `${t("cmd.subtitle.jurisdiction")}: ${user?.jurisdiction || "Kerala"}`}
      >
        <CommandInner />
      </AppShell>
    </CommandProvider>
  );
}
