import React from "react";
import { Outlet } from "react-router-dom";
import AppShell from "@/components/layout/AppShell";
import { CommandProvider, useCommand } from "@/context/CommandContext";
import { useAuth } from "@/context/AuthContext";
import { useI18n } from "@/context/I18nContext";
import {
  X,
  Eye,
  FolderLock,
  ShieldCheck,
  Briefcase,
  Trash2,
  Image as ImageIcon
} from "lucide-react";

import { api, apiError, downloadSigned } from "@/lib/api";
import { fmtIST } from "@/lib/ist";
import EvidenceImage from "@/components/EvidenceImage";
import ActionModal from "@/components/evidence/ActionModal";

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

function CommandInner() {
  const { user } = useAuth();
  const { t } = useI18n();
  const {
    kpis,
    playing,
    setPlaying,
    evidenceDetail,
    setEvidenceDetail,
    action,
    setAction,
    openAction,
    load,
    loadFilteredEvidence
  } = useCommand();
  const [verify, setVerify] = React.useState(null);

  const runVerify = async (id) => {
    setVerify({ busy: true });
    try { setVerify((await api.get(`/evidence/${id}/verify`)).data); }
    catch (e) { setVerify({ error: apiError(e, "Verification failed") }); }
  };
  const ev = evidenceDetail?.evidence;
  const act = (kind) => { openAction(kind, ev); setEvidenceDetail(null); setVerify(null); };

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
          {/* P12: the user's own scope, not a fixed district */}
          {user?.role === "senior_officer" ? "All districts" : (user?.jurisdiction || "No jurisdiction assigned")}{" "}
          <span className="mx-2 font-light" style={{ color: "var(--dvas-text-dim)" }}>|</span>{" "}
          <span className="text-xs font-semibold">{fmtIST(new Date().toISOString())}</span>
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
                  <EvidenceImage
                    evidenceId={playing.id}
                    role="primary"
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
                {playing.secondary_image_key ? (
                  <div className="relative flex flex-col items-center justify-center h-full max-w-[48%]">
                    <EvidenceImage
                      evidenceId={playing.id}
                      role="secondary"
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
                ) : (playing.event_type === "vehicle_detected" || playing.label?.toLowerCase().includes("vehicle")) ? (
                  <div className="relative flex flex-col items-center justify-center h-full max-w-[48%] border border-dashed border-slate-800 rounded bg-slate-950/60 p-6">
                    <ImageIcon size={28} className="text-slate-600 mb-2" />
                    <span className="text-[11px] mono font-semibold text-slate-400">PLATE CROP NOT CAPTURED</span>
                    <span className="text-[9px] mono text-slate-500 mt-1 text-center">No license plate detected or readable in this vehicle frame</span>
                    <span className="text-[10px] mono text-slate-500 mt-3 uppercase tracking-wider">
                      License Plate Crop
                    </span>
                  </div>
                ) : null}
              </div>

            ) : (
              <div className="p-8 text-center text-slate-500 mono text-xs">No image was captured for this event</div>
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
              <button onClick={() => { setEvidenceDetail(null); setVerify(null); }} className="text-slate-400 hover:text-white p-1">
                <X size={16} />
              </button>
            </div>
            <div className="p-4 space-y-4">
              {(evidenceDetail.evidence?.primary_image_key || evidenceDetail.evidence?.thumbnail) && (
                <div className="w-full bg-slate-900/80 rounded-lg p-3 flex items-center justify-center gap-4 border border-slate-800">
                  <div className="relative flex flex-col items-center">
                    <EvidenceImage
                      evidenceId={evidenceDetail.evidence.id}
                      role="primary"
                      alt="Primary Capture"
                      className="max-h-48 object-contain rounded border border-slate-700 shadow"
                      onError={(e) => { e.target.style.display = 'none'; }}
                    />
                    <span className="text-[9px] mono text-slate-400 mt-1 uppercase tracking-wider">
                      {evidenceDetail.evidence.event_type === "vehicle_detected" ? "Vehicle Crop" : evidenceDetail.evidence.event_type === "person_detected" ? "Body Crop" : "Primary Capture"}
                    </span>
                  </div>
                  {evidenceDetail.evidence?.secondary_image_key ? (
                    <div className="relative flex flex-col items-center">
                      <EvidenceImage
                        evidenceId={evidenceDetail.evidence.id}
                        role="secondary"
                        alt="Secondary Capture"
                        className="max-h-48 object-contain rounded border border-slate-700 shadow"
                        onError={(e) => { e.target.style.display = 'none'; }}
                      />
                      <span className="text-[9px] mono text-slate-400 mt-1 uppercase tracking-wider">
                        {evidenceDetail.evidence.event_type === "vehicle_detected" ? "Plate Crop" : "Secondary Detection"}
                      </span>
                    </div>
                  ) : (evidenceDetail.evidence?.event_type === "vehicle_detected" || evidenceDetail.evidence?.label?.toLowerCase().includes("vehicle")) ? (
                    <div className="relative flex flex-col items-center justify-center p-4 border border-dashed border-slate-800 rounded bg-slate-950/60 min-w-[120px]">
                      <ImageIcon size={20} className="text-slate-600 mb-1" />
                      <span className="text-[9px] mono text-slate-500">Plate Not Captured</span>
                    </div>
                  ) : null}
                </div>

              )}
              <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs">
                {[
                  ["Label", evidenceDetail.evidence?.label],
                  ["Event Type", evidenceDetail.evidence?.event_type],
                  ["Drone", evidenceDetail.evidence?.drone_id],
                  ["Mission", evidenceDetail.evidence?.mission_id],
                  ["Confidence", evidenceDetail.evidence?.confidence],
                  ["Jurisdiction", evidenceDetail.evidence?.jurisdiction],
                  ["Captured", fmtIST(evidenceDetail.evidence?.capture_ts || evidenceDetail.evidence?.ts)],
                  ["Image", { exact: "Exact capture from the analyzer", heuristic: "Matched afterwards (pre-E1) — verify before use", none: "No image captured" }[evidenceDetail.evidence?.image_match] || "—"],
                  ["Integrity", evidenceDetail.evidence?.integrity],
                  ["Record SHA-256", evidenceDetail.evidence?.record_sha256 ? `${evidenceDetail.evidence.record_sha256.slice(0, 24)}…` : "—"],
                  ["Legal Hold", evidenceDetail.evidence?.legal_hold ? `Yes — ${evidenceDetail.evidence.legal_hold_reason || ""}` : "No"],
                  ["Retention Until", evidenceDetail.evidence?.retention_until ? fmtIST(evidenceDetail.evidence.retention_until) : "-"]
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
                    {evidenceDetail.evidence.legal_hold_at ? fmtIST(evidenceDetail.evidence.legal_hold_at) : "-"}
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
                          <button
                            type="button"
                            onClick={() => downloadSigned("bundle", x.id).catch((e) => alert(apiError(e, "Download failed")))}
                            className="text-sky-400 hover:text-sky-300 text-[10px]"
                          >
                            Download Evidence Bundle
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {verify && (
                <div className="border border-slate-800 rounded p-2 text-xs">
                  {verify.busy && <span className="text-slate-400">Re-hashing stored files…</span>}
                  {verify.error && <span className="text-red-400">{verify.error}</span>}
                  {verify.integrity && (
                    <div className={verify.integrity === "ok" ? "text-emerald-400" : "text-red-400"}>
                      Integrity: {verify.integrity} · record {verify.record_ok ? "matches" : "DOES NOT match"} its hash
                      {(verify.objects || []).map((o) => (
                        <div key={o.key} className="mono text-[10px] text-slate-400">{o.role}: {o.ok ? "match" : "MISMATCH"} {(o.actual || "").slice(0, 16)}…</div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              <div className="flex flex-wrap gap-2 pt-2 border-t border-slate-800">
                <button
                  onClick={() => { setPlaying(evidenceDetail.evidence); setEvidenceDetail(null); }}
                  className="px-3 py-1.5 border border-sky-600/60 text-sky-300 hover:bg-sky-500/10 text-xs flex items-center gap-1 rounded"
                >
                  <Eye size={12} /> View image
                </button>
                {["control_room_officer", "senior_officer"].includes(user?.role) && (
                  <button onClick={() => act("add_to_case")} className="px-3 py-1.5 border border-slate-700 text-slate-300 hover:border-sky-500 text-xs rounded flex items-center gap-1">
                    <Briefcase size={12} /> Add to case
                  </button>
                )}
                {user?.role === "control_room_officer" && (
                  <button onClick={() => act("request_export")} className="px-3 py-1.5 border border-slate-700 text-slate-300 hover:border-sky-500 text-xs rounded">
                    Request Export
                  </button>
                )}
                {user?.role === "senior_officer" && (
                  <button onClick={() => act("direct_export")} className="px-3 py-1.5 border border-emerald-600/60 text-emerald-300 hover:bg-emerald-500/10 text-xs rounded">
                    Direct Export
                  </button>
                )}
                {user?.role === "senior_officer" && (
                  <button onClick={() => act(ev?.legal_hold ? "release_hold" : "legal_hold")} className="px-3 py-1.5 border border-amber-600/60 text-amber-300 hover:bg-amber-500/10 text-xs rounded flex items-center gap-1">
                    <FolderLock size={12} /> {ev?.legal_hold ? "Release legal hold" : "Legal hold"}
                  </button>
                )}
                {["senior_officer", "system_administrator"].includes(user?.role) && (
                  <button onClick={() => runVerify(ev.id)} className="px-3 py-1.5 border border-slate-700 text-slate-300 hover:border-emerald-500 text-xs rounded flex items-center gap-1">
                    <ShieldCheck size={12} /> Verify integrity
                  </button>
                )}
                {user?.role === "senior_officer" && !ev?.legal_hold && (
                  <button onClick={() => act("dispose")} className="px-3 py-1.5 border border-red-700/60 text-red-300 hover:bg-red-500/10 text-xs rounded flex items-center gap-1">
                    <Trash2 size={12} /> Dispose
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Evidence action modal (legal hold, export, disposal, add to case) ── */}
      <ActionModal action={action} onClose={() => setAction(null)}
        onDone={() => { load(); loadFilteredEvidence(); }} />
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
