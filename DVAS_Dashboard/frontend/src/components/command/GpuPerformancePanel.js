import React, { useEffect, useState } from "react";
import { api, apiError } from "@/lib/api";
import { Gauge, Cpu, RefreshCw, CheckCircle2, AlertTriangle as Warning, Save } from "lucide-react";

// Detected GPU, FPS governor and measured fps per analyzer / drone (RunPod plan Phase 10)

const POLL_MS = 3000;

const STATE_STYLE = {
  ok:            ["On target", "text-emerald-400 border-emerald-500/30 bg-emerald-500/10"],
  overloaded:    ["GPU overloaded", "text-red-400 border-red-500/30 bg-red-500/10"],
  input_limited: ["Limited by drone input", "text-amber-300 border-amber-500/30 bg-amber-500/10"],
  idle:          ["Idle", "text-slate-400 border-slate-700 bg-slate-800"],
  disabled:      ["Governor off", "text-slate-400 border-slate-700 bg-slate-800"],
  starting:      ["Starting", "text-slate-400 border-slate-700 bg-slate-800"],
};

const DRONE_STATE = {
  on_target:     ["on target", "text-emerald-400"],
  below_target:  ["below target", "text-red-400"],
  input_limited: ["input-limited", "text-amber-300"],
  capped:        ["capped (by design)", "text-slate-400"],
};

// Per-frame stage split (plan A4): where each analysed frame's time goes
const STAGE_ORDER = ["decode", "detect", "track", "model", "total"];
const STAGE_LABEL = { decode: "decode", detect: "detect", track: "track", model: "model", total: "total" };

const fmt = (v, d = 1) => (v == null ? "—" : Number(v).toFixed(d));

function Stat({ label, value, sub, tone = "text-sky-400" }) {
  return (
    <div className="text-right">
      <div className="dvas-label text-[10px]">{label}</div>
      <div className={`mono text-lg font-extrabold ${tone}`}>
        {value} {sub && <span className="text-xs text-slate-400 font-normal">{sub}</span>}
      </div>
    </div>
  );
}

function PolicyEditor({ policy, onSaved }) {
  const [form, setForm] = useState(policy);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState(null);

  // Reset the form only when the stored values change, not on every 3 s poll (new object)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { setForm(policy); }, [policy.target_min, policy.target_max, policy.floor]);

  const n = (k) => Number(form[k]);
  const valid = ["target_min", "target_max", "floor"].every(k => Number.isInteger(n(k)) && n(k) >= 1 && n(k) <= 60)
    && n("floor") <= n("target_min") && n("target_min") <= n("target_max");
  const dirty = ["target_min", "target_max", "floor"].some(k => n(k) !== policy[k]);

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      const { data } = await api.put("/models/performance/policy", {
        target_min: n("target_min"), target_max: n("target_max"), floor: n("floor"),
      });
      setMsg({ ok: data.applied, text: data.applied ? "Applied to the analytics cluster." : data.detail });
      onSaved();
    } catch (e) {
      setMsg({ ok: false, text: apiError(e, "Could not save the FPS policy") });
    } finally {
      setSaving(false);
    }
  };

  const field = (k, label) => (
    <label className="flex flex-col gap-1">
      <span className="dvas-label text-[10px]">{label}</span>
      <input
        type="number" min={1} max={60} step={1}
        data-testid={`fps-policy-${k}`}
        value={form[k]}
        onChange={(e) => setForm(f => ({ ...f, [k]: e.target.value }))}
        className="w-20 px-2 py-1 rounded border border-slate-700 bg-slate-900 mono text-xs text-slate-100"
      />
    </label>
  );

  return (
    <div className="flex items-end gap-3 flex-wrap">
      {field("target_min", "Target min fps")}
      {field("target_max", "Target max fps")}
      {field("floor", "Floor under overload")}
      <button
        data-testid="fps-policy-save"
        disabled={!valid || !dirty || saving}
        onClick={save}
        className="px-3 py-1.5 bg-sky-600 hover:bg-sky-500 text-white text-xs font-semibold rounded-md disabled:opacity-50 flex items-center gap-1.5"
      >
        {saving ? <RefreshCw size={12} className="animate-spin" /> : <Save size={12} />} Save
      </button>
      {!valid && <span className="mono text-[10px] text-red-400">Whole numbers 1–60, floor ≤ min ≤ max</span>}
      {msg && (
        <span className={`mono text-[10px] flex items-center gap-1 ${msg.ok ? "text-emerald-400" : "text-amber-300"}`}>
          {msg.ok ? <CheckCircle2 size={12} /> : <Warning size={12} />} {msg.text}
        </span>
      )}
    </div>
  );
}

export default function GpuPerformancePanel() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  const load = async () => {
    try {
      const res = await api.get("/models/performance");
      setData(res.data);
      setError("");
    } catch (e) {
      setError(apiError(e, "Could not load GPU performance"));
    }
  };

  useEffect(() => {
    load();
    const id = setInterval(() => { if (!document.hidden) load(); }, POLL_MS);
    return () => clearInterval(id);
  }, []);

  const card = {
    background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)", boxShadow: "var(--dvas-shadow)",
  };

  if (!data) {
    return (
      <div className="rounded-lg p-4 mono text-xs text-slate-500 flex items-center gap-2" style={card}>
        {error ? <><Warning size={14} className="text-amber-400" /> {error}</>
               : <><RefreshCw size={14} className="animate-spin" /> Loading GPU performance…</>}
      </div>
    );
  }

  const gpu = data.gpu || {};
  const gov = data.governor || {};
  const [stateLabel, stateCls] = STATE_STYLE[gov.state] || STATE_STYLE.starting;
  const p = data.policy;

  return (
    <div className="rounded-lg overflow-hidden" style={card} data-testid="gpu-performance">
      <div className="p-4 flex items-center justify-between gap-4 flex-wrap border-b" style={{ borderColor: "var(--dvas-border)" }}>
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-violet-500/10 text-violet-400 border border-violet-500/30 flex items-center justify-center">
            <Gauge size={20} />
          </div>
          <div>
            <div className="text-sm font-bold" style={{ color: "var(--dvas-text)" }} data-testid="gpu-name">
              {gpu.name || "GPU not detected"}
            </div>
            <div className="mono text-[10px] text-slate-500 mt-0.5">
              {gpu.tier ? `${gpu.tier} tier` : "tier —"}
              {gpu.compute_capability ? ` · compute ${gpu.compute_capability}` : ""}
              {gpu.fp16 != null ? ` · FP16 ${gpu.fp16 ? "on" : "off"}` : ""}
              {gpu.max_batch ? ` · ${gpu.max_batch} drones per batch` : ""}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-6">
          <Stat label="VRAM" value={fmt(gpu.vram_gb)} sub="GB" />
          <Stat label="GPU busy" value={gov.gpu_util_pct == null ? "—" : `${Math.round(gov.gpu_util_pct)}%`}
                tone={gov.gpu_util_pct >= 90 ? "text-red-400" : "text-emerald-400"} />
          <Stat label="Cap per drone" value={fmt(gov.cap_fps, 0)} sub="fps" />
          <Stat label="Target" value={`${p.target_min}–${p.target_max}`} sub="fps" tone="text-slate-200" />
        </div>
      </div>

      <div className="p-3.5 flex items-center gap-2 flex-wrap border-b" style={{ borderColor: "var(--dvas-border)" }}>
        <span className={`mono text-[10px] px-2 py-0.5 rounded-full border ${stateCls}`} data-testid="governor-state">
          {stateLabel}
        </span>
        <span className="mono text-[11px] text-slate-400">
          {data.available ? gov.reason : data.detail}
        </span>
      </div>

      {data.editable && (
        <div className="p-3.5 border-b" style={{ borderColor: "var(--dvas-border)" }}>
          <PolicyEditor policy={p} onSaved={load} />
          <div className="mono text-[10px] text-slate-500 mt-2">
            Every analyzer except crowd aims for the target range and analyses every frame in order.
            When the GPU is full they all slow down equally (never below the floor); frames wait in the
            queue and are worked off, also after the drone stops streaming.
          </div>
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-left mono text-[11px]">
          <thead className="text-slate-500">
            <tr>
              <th className="px-3.5 py-2 font-normal">Analyzer</th>
              <th className="px-2 py-2 font-normal">Drone</th>
              <th className="px-2 py-2 font-normal text-right" title="Frames analysed per second">Analysed fps</th>
              <th className="px-2 py-2 font-normal text-right" title="Frames the drone sends per second">Input fps</th>
              <th className="px-2 py-2 font-normal text-right" title="Model time per frame">Model ms</th>
              <th className="px-2 py-2 font-normal text-right" title="Ingest → analysis start">Lag ms</th>
              <th className="px-2 py-2 font-normal text-right" title="Frames waiting in the queue for this analyzer (worked off after the stream stops)">Queued</th>
              <th className="px-2 py-2 font-normal text-right" title="Frames that never arrived (gaps in the drone's frame sequence) or were trimmed from a full queue">Lost</th>
              <th className="px-2 py-2 font-normal text-right" title="Frames that arrived after a newer one was analysed; still analysed, outside the live tracker">Late</th>
              <th className="px-3.5 py-2 font-normal">Status</th>
            </tr>
          </thead>
          <tbody>
            {data.services.length === 0 && (
              <tr><td colSpan={10} className="px-3.5 py-4 text-slate-500">
                <Cpu size={12} className="inline mr-1" />No analyzer is streaming right now.
              </td></tr>
            )}
            {data.services.flatMap(s => (s.drones.length ? s.drones : [null]).map((d, i) => {
              const [lbl, cls] = d ? DRONE_STATE[d.state] || ["—", "text-slate-400"] : ["no drones", "text-slate-500"];
              return (
                <tr key={`${s.service}-${d?.drone_id || "none"}`} className="border-t" style={{ borderColor: "var(--dvas-border)" }}
                    data-testid={`perf-${s.service}-${d?.drone_id || "none"}`}>
                  <td className="px-3.5 py-1.5 text-slate-200">
                    {i === 0 && <>
                      {s.label}{s.avg_batch ? <span className="text-slate-500"> · batch {fmt(s.avg_batch)}</span> : null}
                      {s.device_info?.cpu_fallback && (
                        <span className="ml-1.5 px-1.5 py-0.5 rounded border border-red-500/40 bg-red-500/10 text-red-300"
                              title={`CUDA error, running on the CPU (GPU retried every minute): ${s.device_info.cpu_reason || ""}`}
                              data-testid={`cpu-fallback-${s.service}`}>
                          CPU FALLBACK
                        </span>
                      )}
                      {s.device_info?.imgsz && (
                        <span className="text-slate-500" title="Person detector input / face tile size (native resolution)">
                          {" "}· input {s.device_info.imgsz}{s.device_info.face_tile ? ` · face tiles ${s.device_info.face_tile}px` : ""}
                        </span>
                      )}
                    </>}
                  </td>
                  <td className="px-2 py-1.5 text-slate-300">{d?.drone_id || "—"}</td>
                  <td className="px-2 py-1.5 text-right text-slate-100 font-semibold">{fmt(d?.fps)}</td>
                  <td className="px-2 py-1.5 text-right text-slate-400">{fmt(d?.input_fps)}</td>
                  <td className="px-2 py-1.5 text-right text-slate-400">{fmt(d?.infer_ms)}</td>
                  <td className="px-2 py-1.5 text-right text-slate-400">{fmt(d?.lag_ms, 0)}</td>
                  <td className="px-2 py-1.5 text-right text-slate-400">{d?.queued ?? "—"}</td>
                  <td className="px-2 py-1.5 text-right text-slate-400">{d?.skipped ?? "—"}</td>
                  <td className="px-2 py-1.5 text-right text-slate-400">{d?.late ?? "—"}</td>
                  <td className={`px-3.5 py-1.5 ${cls}`}>
                    {lbl}
                    {d?.stages && Object.keys(d.stages).length > 0 && (
                      <div className="text-[10px] text-slate-500 whitespace-nowrap" data-testid={`stages-${s.service}-${d.drone_id}`}
                           title="Average ms per frame for each pipeline stage">
                        {STAGE_ORDER.filter((k) => d.stages[k] != null)
                          .map((k) => `${STAGE_LABEL[k]} ${Math.round(d.stages[k])}`).join(" · ")}
                      </div>
                    )}
                  </td>
                </tr>
              );
            }))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
