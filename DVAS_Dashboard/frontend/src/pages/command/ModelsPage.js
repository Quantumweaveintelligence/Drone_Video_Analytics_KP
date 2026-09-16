import React, { useState, useEffect } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import {
  Cpu,
  RefreshCw,
  Play,
  CheckCircle2,
  AlertTriangle as Warning,
  User as UserIcon,
  ShieldCheck,
  Car,
  Users as UsersIcon,
  Hash,
  Shield
} from "lucide-react";

const MODEL_ICONS = {
  person: UserIcon,
  face: ShieldCheck,
  vehicle: Car,
  crowd: UsersIcon,
  plate: Hash,
  intrusion: Shield
};

const actionColour = (a) => {
  if (!a) return "text-slate-500";
  if (a === "retained" || a === "already_running") return "text-emerald-400";
  if (a.startsWith("started")) return "text-sky-400";
  if (a.startsWith("stopped")) return "text-amber-400";
  return "text-red-400";
};

const actionLabel = (a) => {
  if (!a) return null;
  if (a === "retained" || a === "already_running") return "active";
  if (a.startsWith("started")) return "started";
  if (a.startsWith("stopped")) return "stopped";
  return a;
};

export default function ModelsPage() {
  const { user } = useAuth();
  const [cfg, setCfg] = useState(null);
  const [selected, setSelected] = useState([]);
  const [saving, setSaving] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [msg, setMsg] = useState({ text: "", ok: true });
  const [actions, setActions] = useState(null);

  const load = async (silent = false) => {
    if (!silent) setRefreshing(true);
    try {
      const { data } = await api.get("/models");
      setCfg(data);
      if (!cfg) setSelected(data.active);
    } catch (e) {
      console.error(e);
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    const id = setInterval(() => load(true), 15000);
    return () => clearInterval(id);
  }, []);

  const toggle = (key) => {
    if (cfg?.read_only) return;
    setSelected(s => s.includes(key) ? s.filter(x => x !== key) : [...s, key]);
  };

  const deploy = async () => {
    setSaving(true);
    setMsg({ text: "", ok: true });
    setActions(null);
    try {
      const res = await api.post("/models/deploy", { active: selected });
      setMsg({ text: "Deployment applied to GPU cluster.", ok: true });
      if (res.data?.stream_actions) {
        setActions(res.data.stream_actions);
      }
      load(true);
    } catch (e) {
      setMsg({ text: e.response?.data?.detail || "Deploy failed", ok: false });
    } finally {
      setSaving(false);
    }
  };

  if (!cfg) {
    return (
      <div className="p-8 text-center text-slate-500 mono text-xs flex items-center justify-center gap-2">
        <RefreshCw size={14} className="animate-spin" /> Loading model configuration…
      </div>
    );
  }

  const dirty = JSON.stringify([...selected].sort()) !== JSON.stringify([...cfg.active].sort());
  const gpuUsed = cfg.available
    .filter(m => selected.includes(m.key))
    .reduce((acc, m) => acc + (m.gpu_pct || 0), 0);

  return (
    <div className="space-y-4">
      {/* Cluster telemetry banner */}
      <div
        className="rounded-lg p-4 flex items-center justify-between gap-4 flex-wrap"
        style={{
          background: "var(--dvas-surface)",
          border: "1px solid var(--dvas-border)",
          boxShadow: "var(--dvas-shadow)"
        }}
      >
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-sky-500/10 text-sky-400 border border-sky-500/30 flex items-center justify-center">
            <Cpu size={20} />
          </div>
          <div>
            <div className="text-sm font-bold" style={{ color: "var(--dvas-text)" }}>
              {cfg.cluster_label || "Kerala Police Analytics Node (H100)"}
            </div>
            <div className="mono text-[10px] text-slate-500 mt-0.5">
              Target inference latency: {cfg.max_latency_ms || 120} ms · Stream format: RTSP/MJPEG
            </div>
          </div>
        </div>

        <div className="flex items-center gap-6">
          <div className="text-right">
            <div className="dvas-label text-[10px]">Allocated GPU Load</div>
            <div className={`mono text-lg font-extrabold ${gpuUsed > 80 ? "text-amber-400" : "text-emerald-400"}`}>
              {gpuUsed}%
            </div>
          </div>
          <div className="text-right">
            <div className="dvas-label text-[10px]">Active Models</div>
            <div className="mono text-lg font-extrabold text-sky-400">
              {selected.length} / {cfg.available.length}
            </div>
          </div>
        </div>
      </div>

      {/* Model Cards Grid */}
      <div
        className="rounded-lg overflow-hidden"
        style={{
          background: "var(--dvas-surface)",
          border: "1px solid var(--dvas-border)",
          boxShadow: "var(--dvas-shadow)"
        }}
      >
        <div className="p-3.5 border-b flex items-center justify-between gap-2" style={{ borderColor: "var(--dvas-border)" }}>
          <div className="flex items-center gap-2 text-sm font-semibold" style={{ color: "var(--dvas-text)" }}>
            <Cpu size={16} className="text-sky-400" />
            <span>Detection Models on GPU Cluster</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => load(false)}
              disabled={refreshing}
              className="flex items-center gap-1 px-2.5 py-1 border border-slate-700 rounded text-[11px] mono text-slate-300 hover:border-slate-500 disabled:opacity-50 transition-colors"
            >
              <RefreshCw size={11} className={refreshing ? "animate-spin" : ""} />
              <span>{refreshing ? "Refreshing…" : "Refresh"}</span>
            </button>
          </div>
        </div>

        <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-3">
          {cfg.available.map(m => {
            const checked = selected.includes(m.key);
            const isRunning = m.stream_running === true;
            const ModelIcon = MODEL_ICONS[m.key] || Cpu;
            const lastAction = actions?.[m.key === "face" ? "person" : m.key] || actions?.[m.key];

            return (
              <label
                key={m.key}
                data-testid={`model-${m.key}`}
                data-active={checked ? "true" : "false"}
                data-running={isRunning ? "true" : "false"}
                className={`border p-3.5 flex items-start gap-3 cursor-pointer transition-all rounded-lg ${
                  isRunning
                    ? "border-emerald-500/50 bg-emerald-500/5"
                    : checked
                    ? "border-sky-500/60 bg-sky-500/8"
                    : "border-slate-800 bg-slate-900/60 hover:border-slate-700"
                } ${cfg.read_only ? "cursor-not-allowed opacity-70" : ""}`}
                onClick={(e) => { e.preventDefault(); toggle(m.key); }}
              >
                <div className={`h-9 w-9 flex items-center justify-center rounded-lg shrink-0 ${
                  isRunning ? "bg-emerald-500/20 text-emerald-300"
                  : checked ? "bg-sky-500/20 text-sky-300"
                  : "bg-slate-800 text-slate-500"
                }`}>
                  <ModelIcon size={18} />
                </div>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <div className="text-xs font-bold text-slate-100">{m.label}</div>
                    {m.default && (
                      <span className="mono text-[9px] px-1.5 py-0.2 border border-slate-700 text-slate-400 uppercase rounded">
                        default
                      </span>
                    )}
                    {isRunning ? (
                      <span className="mono text-[9px] px-1.5 py-0.2 rounded-full bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 flex items-center gap-1">
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block" style={{ animation: "pulse 2s infinite" }} />
                        RUNNING
                      </span>
                    ) : (
                      <span className="mono text-[9px] px-1.5 py-0.2 rounded-full bg-slate-800 text-slate-500 border border-slate-700">
                        IDLE
                      </span>
                    )}
                  </div>
                  <div className="mono text-[10px] text-slate-500 mt-1 flex items-center gap-2">
                    <span>GPU <span className="text-slate-300 font-semibold">{m.gpu_pct}%</span></span>
                    <span>·</span>
                    <span>Peak <span className="text-slate-300 font-semibold">{m.throughput_fps} fps</span></span>
                    {lastAction && (
                      <>
                        <span>·</span>
                        <span className={`font-bold ${actionColour(lastAction)}`}>{actionLabel(lastAction)}</span>
                      </>
                    )}
                  </div>
                </div>

                <input
                  type="checkbox"
                  data-testid={`model-toggle-${m.key}`}
                  checked={checked}
                  onChange={() => toggle(m.key)}
                  disabled={cfg.read_only}
                  className="h-4 w-4 accent-sky-500 shrink-0 mt-1"
                />
              </label>
            );
          })}
        </div>

        {/* Deploy Footer Bar */}
        {!cfg.read_only && (
          <div className="p-3.5 border-t flex items-center gap-3" style={{ borderColor: "var(--dvas-border)" }}>
            {gpuUsed > 100 && (
              <div className="mono text-xs text-red-400 flex items-center gap-1.5">
                <Warning size={14} /> GPU over-committed ({gpuUsed}%) — reduce models before deploying
              </div>
            )}
            {msg.text && (
              <div className={`mono text-xs flex items-center gap-1.5 ${msg.ok ? "text-emerald-400" : "text-red-400"}`}>
                {msg.ok ? <CheckCircle2 size={14} /> : <Warning size={14} />}
                {msg.text}
              </div>
            )}
            <div className="ml-auto flex gap-2">
              <button
                data-testid="models-reset"
                disabled={!dirty || saving}
                onClick={() => { setSelected(cfg.active); setActions(null); setMsg({ text: "", ok: true }); }}
                className="px-3 py-1.5 border border-slate-700 text-xs rounded-md disabled:opacity-50 text-slate-300 hover:border-slate-500 transition-colors"
              >
                Reset
              </button>
              <button
                data-testid="models-save"
                disabled={!dirty || saving || gpuUsed > 100}
                onClick={deploy}
                className="px-4 py-1.5 bg-sky-600 hover:bg-sky-500 text-white text-xs font-semibold rounded-md disabled:opacity-50 transition-colors flex items-center gap-1.5 shadow-sm"
              >
                {saving ? <><RefreshCw size={12} className="animate-spin" /> Deploying…</> : <><Play size={12} fill="currentColor" /> Deploy Cluster</>}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
