import React, { useRef } from "react";
import { useCommand } from "@/context/CommandContext";
import { useAuth } from "@/context/AuthContext";
import {
  Film,
  UserSearch,
  Car,
  Users,
  FolderLock,
  Play,
  Download,
  RefreshCw,
  Image as ImageIcon
} from "lucide-react";

const EVIDENCE_CATEGORIES = [
  { key: "all", label: "All Evidence", Icon: Film, desc: "All detections & alerts", countKey: "all", dedupKey: "all_dedup" },
  { key: "person", label: "Person Evidence", Icon: UserSearch, desc: "Face Re-ID & Tracks", countKey: "person", dedupKey: "person_dedup" },
  { key: "vehicle", label: "Vehicle & ANPR", Icon: Car, desc: "Plates & Vehicle Tracks", countKey: "vehicle", dedupKey: "vehicle_dedup" },
  { key: "crowd", label: "Crowd & Heatmaps", Icon: Users, desc: "Density & Congestion", countKey: "crowd", dedupKey: "crowd_dedup" },
];

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

export default function EvidencePage() {
  const { user } = useAuth();
  const {
    evidence,
    evidenceTotal,
    evidenceFilters,
    setEvidenceFilters,
    evidenceCategory,
    evidenceDedup,
    evidenceMeta,
    drones,
    missions,
    handleCategoryChange,
    handleDedupToggle,
    loadFilteredEvidence,
    loadMoreEvidence,
    loadingMoreEvidence,
    setPlaying,
    setRequestOn,
    directExport,
    toggleLegalHold,
    generateMissionReport,
    generatingReport,
    openEvidenceDetail
  } = useCommand();

  const evidenceFiltersRef = useRef(evidenceFilters);
  evidenceFiltersRef.current = evidenceFilters;

  return (
    <div className="space-y-4">
      {/* Category Selector Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {EVIDENCE_CATEGORIES.map(cat => {
          const isSelected = evidenceCategory === cat.key;
          const count = evidenceDedup
            ? (evidenceMeta.counts?.[cat.dedupKey] ?? 0)
            : (evidenceMeta.counts?.[cat.countKey] ?? 0);
          const CatIcon = cat.Icon;
          return (
            <button
              key={cat.key}
              data-testid={`evidence-cat-${cat.key}`}
              type="button"
              onClick={() => handleCategoryChange(cat.key)}
              className={`flex items-center gap-3 p-3.5 rounded-lg text-left transition-all border ${
                isSelected
                  ? "bg-slate-800/90 border-sky-500 shadow-md ring-1 ring-sky-500/30"
                  : "bg-slate-900/60 border-slate-800 hover:border-slate-700 hover:bg-slate-800/40"
              }`}
              style={{
                boxShadow: isSelected ? "var(--dvas-shadow)" : "none"
              }}
            >
              <div className={`p-2.5 rounded-lg shrink-0 ${
                isSelected ? "bg-sky-500/20 text-sky-400" : "bg-slate-800 text-slate-400"
              }`}>
                <CatIcon size={18} />
              </div>
              <div className="min-w-0 flex-1">
                <div className={`text-xs font-semibold truncate ${isSelected ? "text-white" : "text-slate-300"}`}>
                  {cat.label}
                </div>
                <div className="flex items-center gap-1.5 mt-0.5">
                  <span className="mono text-xs font-bold text-sky-400">
                    {count ? count.toLocaleString() : "—"}
                  </span>
                  <span className="text-[10px] text-slate-500">
                    {evidenceDedup ? "unique captures" : "total events"}
                  </span>
                </div>
              </div>
            </button>
          );
        })}
      </div>

      {/* Search & Filter Bar */}
      <div
        className="rounded-lg p-4 space-y-3"
        style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)", boxShadow: "var(--dvas-shadow)" }}
      >
        <div className="flex flex-wrap items-center justify-between gap-2 text-sm font-semibold" style={{ color: "var(--dvas-text)" }}>
          <div className="flex items-center gap-2">
            <Film size={16} className="text-sky-500" /> Evidence Vault
            <span className="text-xs font-normal text-slate-400">
              · <span className="capitalize text-sky-400 font-medium">{evidenceCategory}</span>
            </span>
          </div>
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 cursor-pointer select-none bg-slate-800/80 px-2.5 py-1 rounded border border-slate-700 hover:border-slate-600 transition-colors">
              <input
                type="checkbox"
                checked={evidenceDedup}
                onChange={e => handleDedupToggle(e.target.checked)}
                className="accent-sky-500 h-3.5 w-3.5 rounded cursor-pointer"
              />
              <span className="text-xs text-slate-300">Deduplicate Captures</span>
              <span className={`text-[9px] mono px-1.5 py-0.2 rounded font-bold ${
                evidenceDedup ? "bg-sky-500/20 text-sky-300 border border-sky-500/30" : "bg-slate-700 text-slate-400"
              }`}>
                {evidenceDedup ? "ON" : "OFF"}
              </span>
            </label>
            <span className="mono text-[10px] text-slate-500">
              Showing {evidence.length} of {evidenceTotal} {evidenceDedup ? "unique" : "items"}
            </span>
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-2">
          <select
            value={evidenceFilters.drone_id}
            onChange={e => {
              const f = { ...evidenceFilters, drone_id: e.target.value };
              setEvidenceFilters(f);
              loadFilteredEvidence(f);
            }}
            className="bg-slate-800 border border-slate-700 text-xs px-2 py-1.5 rounded text-slate-200"
          >
            <option value="">All Drones</option>
            {[...new Set([...(evidenceMeta.drones || []), ...drones.map(d => d.id)])].filter(Boolean).sort().map(id => (
              <option key={id} value={id}>{id}</option>
            ))}
          </select>

          <select
            value={evidenceFilters.mission_id}
            onChange={e => {
              const f = { ...evidenceFilters, mission_id: e.target.value };
              setEvidenceFilters(f);
              loadFilteredEvidence(f);
            }}
            className="bg-slate-800 border border-slate-700 text-xs px-2 py-1.5 rounded text-slate-200"
          >
            <option value="">All Missions</option>
            {[...new Set([...(evidenceMeta.missions || []), ...missions.map(m => m.mission_id)])].filter(Boolean).sort().map(mid => (
              <option key={mid} value={mid}>{mid}</option>
            ))}
          </select>

          <select
            value={evidenceFilters.event_type}
            onChange={e => {
              const f = { ...evidenceFilters, event_type: e.target.value };
              setEvidenceFilters(f);
              loadFilteredEvidence(f);
            }}
            className="bg-slate-800 border border-slate-700 text-xs px-2 py-1.5 rounded text-slate-200"
          >
            <option value="">All Event Types</option>
            {[...new Set([...(evidenceMeta.event_types || []), "person_detected", "vehicle_detected", "crowd_threshold", "crowd_surge", "vehicle_anomaly", "perimeter_intrusion", "unattended_object", "wrong_way", "theft_alert", "intrusion_alert"])].filter(Boolean).sort().map(t => (
              <option key={t} value={t}>{t.replace(/_/g, " ")}</option>
            ))}
          </select>

          <input
            type="date"
            placeholder="From Date"
            value={evidenceFilters.from_ts}
            onChange={e => {
              const f = { ...evidenceFilters, from_ts: e.target.value };
              setEvidenceFilters(f);
              loadFilteredEvidence(f);
            }}
            className="bg-slate-800 border border-slate-700 text-xs px-2 py-1.5 rounded text-slate-200"
          />

          <input
            type="date"
            placeholder="To Date"
            value={evidenceFilters.to_ts}
            onChange={e => {
              const f = { ...evidenceFilters, to_ts: e.target.value };
              setEvidenceFilters(f);
              loadFilteredEvidence(f);
            }}
            className="bg-slate-800 border border-slate-700 text-xs px-2 py-1.5 rounded text-slate-200"
          />

          <input
            type="text"
            placeholder="Search tags / label…"
            value={evidenceFilters.search}
            onChange={e => {
              const f = { ...evidenceFilters, search: e.target.value };
              setEvidenceFilters(f);
              loadFilteredEvidence(f);
            }}
            className="bg-slate-800 border border-slate-700 text-xs px-2 py-1.5 rounded text-slate-200"
          />

          <label className="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer">
            <input
              type="checkbox"
              checked={evidenceFilters.legal_hold_only}
              onChange={e => {
                const f = { ...evidenceFilters, legal_hold_only: e.target.checked };
                setEvidenceFilters(f);
                loadFilteredEvidence(f);
              }}
              className="accent-red-500"
            />
            <span>Legal Hold only</span>
          </label>
        </div>
      </div>

      {/* Grid of Evidence Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
        {evidence.map(ev => (
          <div
            key={ev.id}
            data-testid={`evidence-card-${ev.id}`}
            className="rounded-lg overflow-hidden flex flex-col transition-all"
            style={{
              background: "var(--dvas-surface)",
              border: "1px solid var(--dvas-border)",
              boxShadow: "var(--dvas-shadow)"
            }}
          >
            <div className="relative aspect-video bg-slate-950 overflow-hidden">
              {ev.thumbnail ? (
                <img
                  src={ev.thumbnail.startsWith("http") ? ev.thumbnail : `/api/minio/image?bucket=${encodeURIComponent(resolveMinioBucket(ev.thumbnail, ev.image_bucket))}&key=${encodeURIComponent(ev.thumbnail)}`}
                  alt=""
                  className="w-full h-full object-cover opacity-90 transition-opacity hover:opacity-100"
                  onError={(e) => {
                    e.target.onerror = null;
                    e.target.style.display = "none";
                    const fb = e.target.parentElement.querySelector(".thumbnail-fallback");
                    if (fb) fb.style.display = "flex";
                  }}
                />
              ) : null}
              <div
                className="thumbnail-fallback w-full h-full flex flex-col items-center justify-center text-slate-500 gap-1.5"
                style={{ display: ev.thumbnail ? "none" : "flex" }}
              >
                <ImageIcon size={28} className="text-slate-600" />
                <span className="mono text-[10px] text-slate-500 uppercase tracking-wider">{ev.event_type?.replace(/_/g, " ")}</span>
              </div>
              <button
                data-testid={`play-${ev.id}`}
                onClick={() => setPlaying(ev)}
                className="absolute inset-0 flex items-center justify-center bg-black/20 hover:bg-black/40 transition-colors text-white"
              >
                <div className="border border-white/70 px-3 py-1 text-xs bg-black/60 rounded flex items-center gap-1.5">
                  <Play size={11} fill="currentColor" /> View Evidence
                </div>
              </button>
              {ev.legal_hold && (
                <span className="absolute top-2 left-2 mono text-[10px] px-1.5 py-0.5 border border-red-500/50 bg-white/90 text-red-500 uppercase flex items-center gap-1 shadow-sm rounded-sm">
                  <FolderLock size={10} /> hold
                </span>
              )}
              {ev.duplicate_count > 1 && (
                <span
                  className="absolute bottom-2 left-2 mono text-[10px] px-2 py-0.5 rounded bg-sky-950/90 text-sky-300 border border-sky-600/70 flex items-center gap-1 font-semibold backdrop-blur-sm shadow-md"
                  title={`${ev.duplicate_count} detections share this visual capture`}
                >
                  <span className="w-1.5 h-1.5 rounded-full bg-sky-400"></span>
                  {ev.duplicate_count} detections
                </span>
              )}
              <button
                onClick={() => openEvidenceDetail(ev.id)}
                className="absolute top-2 right-2 mono text-[10px] px-1.5 py-0.5 border border-slate-400/50 bg-black/60 text-slate-200 hover:bg-black/80 rounded-sm"
              >
                Details
              </button>
            </div>

            <div className="p-3">
              <div className="text-xs font-semibold truncate" style={{ color: "var(--dvas-text)" }}>{ev.label}</div>
              <div className="mono text-[10px] text-slate-500 mt-1 flex flex-wrap gap-2">
                <span>{ev.event_type}</span><span>·</span><span>conf {ev.confidence}</span><span>·</span><span>{ev.duration_s}s</span>
              </div>
              <div className="mono text-[10px] text-slate-500">{new Date(ev.ts).toLocaleString()}</div>
              <div className="mono text-[10px] text-slate-500">{ev.drone_id} · {ev.mission_id}</div>

              <div className="mt-2.5 flex flex-wrap gap-1">
                {user?.role === "control_room_officer" && (
                  <button
                    data-testid={`request-${ev.id}`}
                    onClick={() => setRequestOn(ev)}
                    className="text-[10px] px-2 py-1 border border-slate-700 hover:border-sky-500 text-slate-300 rounded"
                  >
                    Request Export
                  </button>
                )}
                {user?.role === "senior_officer" && (
                  <button
                    onClick={() => directExport(ev)}
                    className="text-[10px] px-2 py-1 border border-emerald-600/60 text-emerald-300 hover:bg-emerald-500/10 rounded"
                  >
                    Direct Export
                  </button>
                )}
                {user?.role === "senior_officer" && (
                  <button
                    onClick={() => toggleLegalHold(ev)}
                    className={`text-[10px] px-2 py-1 border rounded ${ev.legal_hold ? "border-red-500/60 text-red-300 hover:bg-red-500/10" : "border-amber-500/60 text-amber-300 hover:bg-amber-500/10"}`}
                  >
                    <FolderLock size={10} className="inline mr-1" />{ev.legal_hold ? "Release Hold" : "Legal Hold"}
                  </button>
                )}
                {ev.mission_id && (
                  <button
                    onClick={() => generateMissionReport(ev.mission_id)}
                    disabled={generatingReport === ev.mission_id}
                    className="text-[10px] px-2 py-1 border border-sky-600/60 text-sky-300 hover:bg-sky-500/10 disabled:opacity-40 flex items-center gap-1 rounded"
                  >
                    {generatingReport === ev.mission_id ? <><RefreshCw size={10} className="animate-spin" /> Generating...</> : <><Download size={10} /> AAR Report</>}
                  </button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      {evidence.length < evidenceTotal && (
        <div className="flex justify-center pt-2 pb-4">
          <button
            onClick={loadMoreEvidence}
            disabled={loadingMoreEvidence}
            className="px-6 py-2.5 rounded-lg bg-sky-600/20 border border-sky-500/50 hover:bg-sky-600/30 text-sky-300 text-xs font-semibold flex items-center gap-2 transition-all disabled:opacity-50 shadow-sm"
          >
            {loadingMoreEvidence ? (
              <>
                <RefreshCw size={13} className="animate-spin" />
                <span>Loading more evidence...</span>
              </>
            ) : (
              <>
                <span>Load More Evidence</span>
                <span className="mono text-[10px] text-sky-400 bg-sky-950/80 px-2 py-0.5 rounded border border-sky-800/60">
                  +{Math.min(60, evidenceTotal - evidence.length)} remaining
                </span>
              </>
            )}
          </button>
        </div>
      )}

      {evidence.length === 0 && (
        <div className="text-center text-slate-500 py-12 text-sm mono">
          No evidence items match the current filters
        </div>
      )}
    </div>
  );
}
