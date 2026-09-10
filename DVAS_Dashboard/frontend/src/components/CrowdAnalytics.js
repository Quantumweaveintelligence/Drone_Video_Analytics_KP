/**
 * CrowdAnalytics.js
 * ─────────────────────────────────────────────────────────────────────────────
 * Crowd Analytics page — fully connected to CSRNet live analytics services
 * and live ingest streams.
 * Kerala Police | Drone Video Analytics System
 *
 * Feed architecture (identical to Person Tracking & Traffic):
 *   - Layer 1: /api/ingest-server/frame/{id}/latest  → raw camera JPEG from edge ingest
 *   - Layer 2: /api/analytics/crowd/annotated         → CSRNet heatmap overlaid JPEG (800ms)
 *   - Stream discovery: useActiveStreams hook         → dynamic drone discovery (e.g. DRN-MSN-...)
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
  ReferenceLine,
} from 'recharts';
import {
  Users, Activity, AlertTriangle, Thermometer,
  TrendingUp, TrendingDown, Minus, Radio, VideoOff, RefreshCw,
  Wifi, WifiOff, Download, FileText, ChevronDown,
  BarChart3, Clock, Zap, Play, Square, Cpu,
} from 'lucide-react';
import {
  useCrowdReadings, useCrowdTimeline, useCrowdSessionSummary,
  useCrowdModel, useAnalyticsSSE, switchCrowdModel, useCrowdHealth,
} from '../lib/useAnalytics';
import { useActiveStreams } from '../hooks/useActiveStreams';
import { useJpegPoller } from '../hooks/useJpegPoller';
import { api } from '../lib/api';

/* ───────────────────────────────────────────────────────────────
   CONSTANTS
   ────────────────────────────────────────────────────────────── */
const DENSITY_LEVELS = [
  { label: 'Low',      color: '#10b981', threshold: 0   },
  { label: 'Medium',   color: '#f59e0b', threshold: 25  },
  { label: 'High',     color: '#f97316', threshold: 75  },
  { label: 'Critical', color: '#ef4444', threshold: 150 },
];

const MODEL_OPTIONS = [
  { value: 'csrnet_parta', label: 'CSRNet Part A — Dense Crowds (Drone)',       short: 'Part A · Dense' },
  { value: 'csrnet_partb', label: 'CSRNet Part B — Static Surveillance (CCTV)', short: 'Part B · Sparse' },
];

const FRAME_INTERVAL = 800;

function getDensityLevel(count) {
  let level = DENSITY_LEVELS[0];
  for (const l of DENSITY_LEVELS) {
    if (count >= l.threshold) level = l;
  }
  return level;
}

function computeTrend(counts) {
  if (!counts || counts.length < 3) return 'stable';
  const recent = counts.slice(-3);
  const avg = recent.reduce((s, c) => s + c, 0) / recent.length;
  const prev = counts.slice(-6, -3);
  if (prev.length === 0) return 'stable';
  const prevAvg = prev.reduce((s, c) => s + c, 0) / prev.length;
  const diff = avg - prevAvg;
  if (diff > 3) return 'up';
  if (diff < -3) return 'down';
  return 'stable';
}

const CHART_TOOLTIP = {
  contentStyle: {
    background: 'var(--dvas-surface)',
    border: '1px solid var(--dvas-border)',
    fontSize: 11,
    color: 'var(--dvas-text)',
    borderRadius: 8,
  },
  itemStyle: { color: 'var(--dvas-text)' },
};

function fmtAge(isoTs) {
  if (!isoTs) return '—';
  const s = Math.floor((Date.now() - new Date(isoTs)) / 1000);
  if (s < 60)   return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

function fmtDuration(seconds) {
  if (!seconds || seconds <= 0) return '0:00';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

/* ───────────────────────────────────────────────────────────────
   useJpegPoller — EXACT copy from PersonTrackingAnalytics.js
   with added meta (X-Crowd-Count header extraction)
   ────────────────────────────────────────────────────────────── */


/* ───────────────────────────────────────────────────────────────
   PANEL WRAPPER
   ────────────────────────────────────────────────────────────── */
function Panel({ title, icon: Icon, iconClass = 'text-sky-400', children, right, badge, noPad }) {
  return (
    <div className="rounded-xl flex flex-col overflow-hidden"
      style={{ background: 'var(--dvas-surface)', border: '1px solid var(--dvas-border)' }}>
      <div className="px-4 py-3 flex items-center gap-2 shrink-0"
        style={{ borderBottom: '1px solid var(--dvas-border)' }}>
        <Icon size={14} className={iconClass} />
        <span className="dvas-label flex-1 font-semibold">{title}</span>
        {badge && (
          <span className="mono text-[10px] px-1.5 py-0.5 rounded font-bold"
            style={{ background: 'rgba(239,68,68,0.12)', color: '#f87171', border: '1px solid rgba(239,68,68,0.2)' }}>
            {badge}
          </span>
        )}
        {right}
      </div>
      <div className={`flex-1 min-h-0 ${noPad ? '' : 'p-3'}`}>{children}</div>
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SERVICE STATUS BADGE
   ────────────────────────────────────────────────────────────── */
function ServiceBadge({ online }) {
  return (
    <span className={`mono text-[10px] flex items-center gap-1 ${online ? 'text-emerald-400' : 'text-slate-500'}`}>
      {online ? <Wifi size={10} /> : <WifiOff size={10} />}
      {online ? 'LIVE' : 'OFFLINE'}
    </span>
  );
}

/* ───────────────────────────────────────────────────────────────
   MODEL SELECTOR DROPDOWN
   ────────────────────────────────────────────────────────────── */
function ModelSelector({ activeModel, onSwitch, switching }) {
  const [open, setOpen] = useState(false);
  const current = MODEL_OPTIONS.find(m => m.value === activeModel) || MODEL_OPTIONS[0];

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        disabled={switching}
        className="mono text-[11px] px-3 py-1.5 rounded-lg flex items-center gap-2 transition-all"
        style={{
          background: 'var(--dvas-surface-2)',
          border: '1px solid var(--dvas-border)',
          color: switching ? 'var(--dvas-text-dim)' : 'var(--dvas-text)',
        }}
      >
        {switching ? (
          <><RefreshCw size={11} className="animate-spin" /> Switching…</>
        ) : (
          <>{current.short} <ChevronDown size={11} /></>
        )}
      </button>
      {open && !switching && (
        <div className="absolute top-full mt-1 right-0 z-50 rounded-lg overflow-hidden shadow-xl min-w-[300px]"
          style={{ background: 'var(--dvas-surface)', border: '1px solid var(--dvas-border)' }}>
          {MODEL_OPTIONS.map(opt => (
            <button
              key={opt.value}
              onClick={() => { setOpen(false); onSwitch(opt.value); }}
              className={`block w-full text-left px-4 py-2.5 mono text-[11px] transition-colors ${
                opt.value === activeModel ? 'text-sky-400' : 'text-slate-300 hover:text-white'
              }`}
              style={{
                background: opt.value === activeModel ? 'rgba(14,165,233,0.08)' : 'transparent',
                borderBottom: '1px solid var(--dvas-border)',
              }}
            >
              <div className="font-semibold">{opt.label}</div>
              {opt.value === activeModel && (
                <span className="text-[9px] text-sky-500 mt-0.5 block">● Active</span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 1 — Live Detection Video Stream & Heatmap Viewer
   ────────────────────────────────────────────────────────────── */
function LiveFeedSection({
  activeDrone,
  droneList,
  setActiveDrone,
  liveCount,
  liveLevel,
  streamRunning,
  activeModel,
  activeMissionId,
  onStartStream,
  onStopStream,
  startingStream,
  onFrameCount,
}) {
  const hasDrone = !!activeDrone;
  const modelLabel = MODEL_OPTIONS.find(m => m.value === activeModel)?.label || 'CSRNet Part A';
  const [fps, setFps] = useState(0);
  const fpsRef = useRef({ count: 0, ts: Date.now() });
  const prevAnnotatedRef = useRef(null);

  // Layer 1: raw ingest feed from edge camera
  const rawUrl = hasDrone
    ? `/api/ingest-server/frame/${encodeURIComponent(activeDrone)}/latest?drone_id=${encodeURIComponent(activeDrone)}`
    : null;
  const { blobUrl: rawSrc, ok: rawOk } = useJpegPoller(rawUrl, 1500, hasDrone);

  // Layer 2: annotated heatmap overlay
  const annotatedUrl = hasDrone
    ? `/api/analytics/crowd/annotated?drone_id=${encodeURIComponent(activeDrone)}`
    : null;
  const { blobUrl: annotatedSrc, ok: annotatedOk, meta: annotatedMeta } = useJpegPoller(
    annotatedUrl, FRAME_INTERVAL, hasDrone
  );

  // Push frame-level crowd count up to parent whenever annotated meta updates
  useEffect(() => {
    if (annotatedOk && annotatedMeta?.crowdCount != null && onFrameCount) {
      onFrameCount(annotatedMeta.crowdCount, annotatedMeta.densityLabel || '');
    }
  }, [annotatedMeta, annotatedOk, onFrameCount]);

  // Measure FPS of live feed
  useEffect(() => {
    const currentBlob = annotatedOk ? annotatedSrc : rawSrc;
    if (!currentBlob || currentBlob === prevAnnotatedRef.current) return;
    prevAnnotatedRef.current = currentBlob;
    fpsRef.current.count++;
    if (Date.now() - fpsRef.current.ts > 3000) {
      setFps(Math.round(fpsRef.current.count / ((Date.now() - fpsRef.current.ts) / 1000)));
      fpsRef.current = { count: 0, ts: Date.now() };
    }
  }, [annotatedSrc, annotatedOk, rawSrc]);

  const displaySrc  = (annotatedOk && annotatedSrc) ? annotatedSrc : rawSrc;
  const isAnnotated = annotatedOk && !!annotatedSrc;
  const hasAnyFeed  = !!displaySrc;

  return (
    <div className="rounded-xl overflow-hidden"
      style={{ background: 'var(--dvas-surface)', border: '1px solid var(--dvas-border)' }}>
      {/* Header */}
      <div className="px-4 py-3 flex items-center gap-3 flex-wrap"
        style={{ borderBottom: '1px solid var(--dvas-border)' }}>
        <span className={`w-2 h-2 rounded-full dvas-live-dot ${hasDrone && hasAnyFeed ? 'bg-sky-500' : 'bg-slate-500'}`} />
        <span className="dvas-label font-semibold">Crowd Detection Live Feed</span>

        {/* Dynamic drone selector pills */}
        <div className="flex items-center gap-1 ml-2 flex-wrap">
          {droneList.length > 0 ? (
            droneList.map(d => {
              const isDraining = d.status === 'draining';
              return (
                <button
                  key={d.id}
                  onClick={() => setActiveDrone(d.id)}
                  className="mono text-[10px] px-2.5 py-1 rounded transition-all font-semibold flex items-center gap-1"
                  style={{
                    background: activeDrone === d.id ? 'rgba(56,189,248,0.15)' : 'var(--dvas-surface-2)',
                    color: activeDrone === d.id ? '#38bdf8' : 'var(--dvas-text-muted)',
                    border: `1px solid ${activeDrone === d.id ? 'rgba(56,189,248,0.35)' : 'var(--dvas-border)'}`,
                  }}
                >
                  {isDraining && (
                    <span className="w-1.5 h-1.5 rounded-full bg-amber-400 dvas-live-dot shrink-0" />
                  )}
                  {d.id}
                  {isDraining && (
                    <span className="text-[8px] bg-amber-800/60 text-amber-300 px-1.5 py-0.5 rounded ml-1 font-bold">DRAINING</span>
                  )}
                </button>
              )
            })
          ) : (
            <span className="mono text-[10px] text-slate-500 italic">No active drone streams</span>
          )}
        </div>

        <div className="flex-1" />

        {/* Stream start/stop controls */}
        {!streamRunning ? (
          <button
            onClick={onStartStream}
            disabled={startingStream}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg mono text-[11px] font-bold transition-all"
            style={{
              background: startingStream ? 'rgba(16,185,129,0.08)' : 'rgba(16,185,129,0.15)',
              color: '#34d399',
              border: '1px solid rgba(16,185,129,0.35)',
              cursor: startingStream ? 'wait' : 'pointer',
            }}
          >
            {startingStream ? (
              <><RefreshCw size={11} className="animate-spin" /> Starting Analytics…</>
            ) : (
              <><Play size={11} /> Start Crowd Analysis</>
            )}
          </button>
        ) : (
          <div className="flex items-center gap-2">
            <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg mono text-[10px] font-bold"
              style={{ background: 'rgba(52,211,153,0.12)', color: '#34d399', border: '1px solid rgba(52,211,153,0.3)' }}>
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 dvas-live-dot" />
              Density Analysis Active
            </span>
            <button
              onClick={onStopStream}
              className="flex items-center gap-1 px-2.5 py-1 rounded-lg mono text-[10px] font-bold transition-all hover:bg-red-500/20"
              title="Stop crowd worker"
              style={{ color: '#f87171', border: '1px solid rgba(248,113,113,0.25)' }}
            >
              <Square size={9} /> Stop Worker
            </button>
          </div>
        )}
      </div>

      {(() => {
        const selectedDroneObj = droneList.find(d => d.id === activeDrone);
        if (selectedDroneObj?.status === 'draining') {
          const remaining = selectedDroneObj.consumer_lag ? Object.values(selectedDroneObj.consumer_lag).reduce((sum, v) => sum + v, 0) : (selectedDroneObj.messages_ready ?? 0);
          return (
            <div className="px-4 py-1.5 bg-amber-900/40 border-b border-amber-500/20 flex items-center justify-center gap-2">
              {remaining > 0 ? (
                <>
                  <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
                  <span className="mono text-[10px] font-bold text-amber-300 tracking-wider">
                    DRAINING — {remaining} frames remaining in queue
                  </span>
                </>
              ) : (
                <>
                  <span className="w-2 h-2 rounded-full bg-emerald-400" />
                  <span className="mono text-[10px] font-bold text-emerald-400 tracking-wider">
                    PROCESSING COMPLETE
                  </span>
                </>
              )}
            </div>
          );
        }
        return null;
      })()}

      {/* Video viewport */}
      <div className="relative bg-black flex items-center justify-center" style={{ height: 420 }}>
        {hasAnyFeed ? (
          <div className="relative w-full h-full">
            <img
              src={displaySrc}
              alt={isAnnotated ? 'Crowd density heatmap overlay' : 'Raw drone camera stream'}
              className="w-full h-full object-contain"
            />
            {/* Top Left Overlay */}
            <div className="absolute top-3 left-3 flex flex-col gap-1.5 z-10 pointer-events-none">
              {isAnnotated && (
                <div className="mono text-[10px] bg-black/85 px-2 py-1 rounded flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full" style={{ background: liveLevel?.color }} />
                  <span style={{ color: liveLevel?.color }} className="font-semibold">{liveLevel?.label}</span>
                  <span className="text-slate-200 ml-1 font-bold">{Math.round(liveCount)} persons</span>
                  <span className="text-slate-400 ml-1">({(liveCount / 200).toFixed(2)} p/m²)</span>
                </div>
              )}
              <div className="mono text-[10px] bg-black/85 px-2 py-1 rounded text-teal-300">
                Drone: {activeDrone}
              </div>
              {activeMissionId && (
                <div className="mono text-[10px] bg-black/85 px-2 py-1 rounded text-violet-300">
                  Mission: {activeMissionId}
                </div>
              )}
            </div>

            {/* Top Right Live badge */}
            <div className="absolute top-3 right-3 z-10 pointer-events-none flex items-center gap-1 mono text-[10px] bg-black/85 px-2 py-1 rounded"
              style={{ color: isAnnotated ? '#34d399' : '#38bdf8' }}>
              <span className="w-1.5 h-1.5 rounded-full dvas-live-dot" style={{ background: isAnnotated ? '#34d399' : '#38bdf8' }} />
              {isAnnotated ? `HEATMAP${fps > 0 ? ` · ${fps}fps` : ''}` : `LIVE · RAW FEED${fps > 0 ? ` · ${fps}fps` : ''}`}
            </div>

            {/* Bottom Left Model badge */}
            <div className="absolute bottom-3 left-3 z-10 pointer-events-none mono text-[10px] bg-black/85 px-2 py-1 rounded text-slate-300">
              {isAnnotated ? `${modelLabel} · Density Estimation` : 'Raw Ingest Stream · Start Analytics for Live Heatmap'}
            </div>

            {/* Bottom Right Status */}
            <div className="absolute bottom-3 right-3 z-10 pointer-events-none mono text-[10px] bg-black/85 px-2 py-1 rounded"
              style={{ color: streamRunning ? '#34d399' : '#fbbf24' }}>
              {streamRunning ? '● INFERENCE WORKER ACTIVE' : '○ INFERENCE STANDBY'}
            </div>
          </div>
        ) : hasDrone ? (
          <div className="text-center text-slate-500 flex flex-col items-center gap-3 px-4">
            <VideoOff size={42} className="opacity-40 text-slate-600" />
            <p className="mono text-sm text-slate-400 font-bold tracking-wider">FEED OFFLINE — NO ACTIVE STREAM</p>
            <p className="mono text-[10px] text-slate-500">Drone <span className="text-sky-400 font-semibold">{activeDrone}</span> is not currently transmitting frames</p>
          </div>
        ) : (
          <div className="text-center text-slate-500 flex flex-col items-center gap-3">
            <RefreshCw size={32} className="text-slate-600 opacity-40 animate-spin" />
            <p className="mono text-sm text-slate-400">Discovering active drone streams…</p>
            <p className="mono text-[10px] text-slate-500">Waiting for edge device telemetry from ingest server</p>
          </div>
        )}
      </div>
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 2 — KPI Strip (Current Count, Peak Count, Alerts, Density Status)
   ────────────────────────────────────────────────────────────── */
function CrowdKPIStrip({ liveCount, peakCount, peakTimestamp, alertCount, avgDensity, densityLevel, trend, sseConnected }) {
  const TrendIcon = trend === 'up' ? TrendingUp : trend === 'down' ? TrendingDown : Minus;
  const trendColor = trend === 'up' ? 'text-emerald-400' : trend === 'down' ? 'text-red-400' : 'text-slate-500';

  const kpis = [
    {
      label: 'Current Count',
      value: (
        <div className="flex items-center gap-2">
          <span>{Math.round(liveCount).toLocaleString()}</span>
          <TrendIcon size={16} className={trendColor} />
        </div>
      ),
      tone: densityLevel.color,
      icon: Users,
      sub: `${trend === 'up' ? 'Increasing' : trend === 'down' ? 'Decreasing' : 'Stable'} trend`,
    },
    {
      label: 'Peak Count',
      value: Math.round(peakCount).toLocaleString(),
      tone: '#f59e0b',
      icon: TrendingUp,
      sub: peakTimestamp ? `at ${new Date(peakTimestamp).toLocaleTimeString()}` : 'No peak recorded',
    },
    {
      label: 'Drone Alerts Active',
      value: alertCount,
      tone: alertCount > 0 ? '#ef4444' : '#64748b',
      icon: AlertTriangle,
      sub: alertCount > 0 ? 'Density threshold exceeded' : 'All zones nominal',
    },
    {
      label: 'Density Status',
      value: densityLevel.label,
      tone: densityLevel.color,
      icon: Thermometer,
      sub: `${avgDensity} p/m² avg density`,
    },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      {kpis.map(({ label, value, tone, icon: Icon, sub }) => (
        <div key={label} className="rounded-xl p-4 flex flex-col gap-1"
          style={{ background: 'var(--dvas-surface)', border: '1px solid var(--dvas-border)' }}>
          <div className="flex items-center gap-2">
            <Icon size={13} style={{ color: tone }} className="shrink-0" />
            <span className="dvas-label" style={{ fontSize: 10 }}>{label}</span>
          </div>
          <div className="mono font-bold text-2xl mt-1" style={{ color: tone }}>
            {typeof value === 'object' ? value : (value ?? '—')}
          </div>
          <div className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>{sub}</div>
        </div>
      ))}
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 3 — People Count Over Time (LIVE TIMELINE)
   ────────────────────────────────────────────────────────────── */
function PeopleCountChart({ timelineData, sseWindow, online }) {
  const chartData = sseWindow.length > 0
    ? sseWindow
    : (Array.isArray(timelineData?.timeline) ? timelineData.timeline : []);

  const isEmpty = chartData.length === 0;
  const displayData = isEmpty
    ? Array.from({ length: 20 }, (_, i) => ({
        displayTime: `${String(i).padStart(2, '0')}:00`,
        crowd: 0,
      }))
    : chartData.map(row => ({
        displayTime: row.hour || row.displayTime || row.ts?.slice(11, 16) || '',
        crowd: Number(row.density ?? row.crowd ?? row.estimated_count ?? row.people_count ?? 0),
      }));

  return (
    <div className="flex flex-col gap-2 relative">
      {isEmpty && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-slate-900/50 backdrop-blur-sm rounded-lg">
          <span className="mono text-xs text-slate-400">Waiting for crowd data from analytics service…</span>
        </div>
      )}
      <ResponsiveContainer width="100%" height={220}>
        <AreaChart data={displayData} margin={{ top: 10, right: 8, left: -20, bottom: 0 }}>
          <defs>
            <linearGradient id="gCrowd" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"   stopColor="#38bdf8" stopOpacity={0.45} />
              <stop offset="100%" stopColor="#38bdf8" stopOpacity={0}   />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--dvas-border)" strokeDasharray="3 3" />
          <XAxis dataKey="displayTime" stroke="var(--dvas-text-muted)" fontSize={9} minTickGap={20} />
          <YAxis stroke="var(--dvas-text-muted)" fontSize={9} />
          <Tooltip {...CHART_TOOLTIP} />
          <ReferenceLine y={25}  stroke="#10b981" strokeDasharray="4 4" strokeOpacity={0.5} label={{ value: 'Low', position: 'insideTopRight', fill: '#10b981', fontSize: 9 }} />
          <ReferenceLine y={75}  stroke="#f59e0b" strokeDasharray="4 4" strokeOpacity={0.5} label={{ value: 'Medium', position: 'insideTopRight', fill: '#f59e0b', fontSize: 9 }} />
          <ReferenceLine y={150} stroke="#ef4444" strokeDasharray="4 4" strokeOpacity={0.5} label={{ value: 'Critical', position: 'insideTopRight', fill: '#ef4444', fontSize: 9 }} />
          <Area type="monotone" dataKey="crowd" stroke="#38bdf8" fill="url(#gCrowd)" strokeWidth={2} name="Persons" dot={false} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 4 — Session Summary Stats
   ────────────────────────────────────────────────────────────── */
function SessionStatsRow({ summary }) {
  const stats = [
    { label: 'Mean Count',  value: summary?.mean_count != null ? Math.round(summary.mean_count) : '—', icon: BarChart3, color: 'text-sky-400' },
    { label: 'Min Count',   value: summary?.min_count  != null ? Math.round(summary.min_count)  : '—', icon: TrendingDown, color: 'text-emerald-400' },
    { label: 'Max Count',   value: summary?.max_count  != null ? Math.round(summary.max_count)  : '—', icon: TrendingUp, color: 'text-amber-400' },
    { label: 'Std Dev',     value: summary?.std_dev    != null ? summary.std_dev.toFixed(1)      : '—', icon: Activity, color: 'text-violet-400' },
    { label: 'Duration',    value: summary?.duration_seconds != null ? fmtDuration(summary.duration_seconds) : '—', icon: Clock, color: 'text-slate-400' },
    { label: 'Frames',      value: summary?.total_frames ?? '—', icon: Zap, color: 'text-pink-400' },
  ];

  return (
    <div className="grid grid-cols-3 md:grid-cols-6 gap-3">
      {stats.map(({ label, value, icon: Icon, color }) => (
        <div key={label} className="rounded-xl p-3 text-center"
          style={{ background: 'var(--dvas-surface)', border: '1px solid var(--dvas-border)' }}>
          <Icon size={13} className={`${color} mx-auto mb-1`} />
          <div className="mono text-xs" style={{ color: 'var(--dvas-text-dim)' }}>{label}</div>
          <div className={`mono font-bold text-lg mt-0.5 ${color}`}>{value}</div>
        </div>
      ))}
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 5 — Drone Status Grid (LIVE per-drone readings)
   ────────────────────────────────────────────────────────────── */
function DroneStatusGrid({ readings, droneList }) {
  const readingMap = {};
  if (Array.isArray(readings)) {
    for (const r of readings) {
      if (r.drone_id) readingMap[r.drone_id] = r;
    }
  }

  if (!droneList || droneList.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center p-6 text-center opacity-40">
        <Radio size={32} className="text-slate-500 mb-2" />
        <span className="mono text-xs text-slate-400">No active drones connected</span>
        <span className="mono text-[10px] text-slate-600">Telemetry will appear when edge streams are active</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {droneList.map(d => {
        const r = readingMap[d.id] || readingMap[d.drone_id];
        const count = r ? Number(r.estimated_count || r.people_count || 0) : 0;
        const level = getDensityLevel(count);
        const pct   = Math.min(100, Math.round((count / 300) * 100));
        const hasData = !!r;
        const label = r?.density_label || level.label;

        return (
          <div key={d.id} className="rounded-lg p-3 flex flex-col gap-2"
            style={{
              background: 'var(--dvas-surface-2)',
              border: `1px solid ${hasData ? 'rgba(56,189,248,0.4)' : 'var(--dvas-border)'}`,
              opacity: hasData || d.status === 'live' ? 1 : 0.6,
            }}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="mono text-xs font-bold" style={{ color: 'var(--dvas-text)' }}>{d.id}</span>
                <span className="mono text-[10px] px-1.5 py-0.5 rounded font-semibold"
                  style={{ background: `${level.color}22`, color: level.color, border: `1px solid ${level.color}44` }}>
                  {hasData ? label : d.status === 'live' ? 'Connected' : 'Offline'}
                </span>
                {hasData && count > 150 && (
                  <span className="mono text-[10px] text-red-400 flex items-center gap-1">
                    <AlertTriangle size={10} className="dvas-live-dot" /> ALERT
                  </span>
                )}
              </div>
              <div className="mono text-right">
                <div className="text-sm font-bold" style={{ color: level.color }}>{Math.round(count).toLocaleString()}</div>
                <div className="text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>persons</div>
              </div>
            </div>
            <div className="h-2 rounded-full overflow-hidden" style={{ background: 'var(--dvas-elev)' }}>
              <div className="h-full rounded-full transition-all duration-300" style={{ width: `${pct}%`, background: level.color }} />
            </div>
            <div className="flex items-center justify-between">
              <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>
                {hasData ? (count / 200).toFixed(2) : '0.00'} p/m²
              </span>
              <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>
                {hasData ? (r.ts ? fmtAge(r.ts) : 'Live') : d.last_seen ? fmtAge(d.last_seen) : 'Standby'}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 6 — Mission Report Export
   ────────────────────────────────────────────────────────────── */
function MissionExport({ hasData, activeModel }) {
  const [meta, setMeta] = useState({ operator: '', location: '', mission_id: '' });
  const [exporting, setExporting] = useState(null);

  const exportFile = async (type) => {
    setExporting(type);
    try {
      const token = localStorage.getItem('dvas_token') || '';
      const params = new URLSearchParams();
      if (meta.operator) params.set('operator', meta.operator);
      if (meta.location) params.set('location', meta.location);
      if (meta.mission_id) params.set('mission_id', meta.mission_id);
      if (activeModel) params.set('model_type', activeModel);

      const url = type === 'pdf'
        ? `/api/analytics/crowd/export/pdf?${params.toString()}`
        : `/api/analytics/crowd/export/csv?${params.toString()}`;

      const res = await fetch(url, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        credentials: 'include',
      });

      if (res.ok) {
        const blob = await res.blob();
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = type === 'pdf'
          ? `crowd_mission_report_${meta.mission_id || 'export'}.pdf`
          : 'crowd_report.csv';
        a.click();
        URL.revokeObjectURL(a.href);
      }
    } catch (err) {
      console.error('Export failed:', err);
    } finally {
      setExporting(null);
    }
  };

  return (
    <div className="rounded-xl p-4"
      style={{ background: 'var(--dvas-surface)', border: '1px solid var(--dvas-border)' }}>
      <div className="flex items-center gap-2 mb-3">
        <FileText size={14} className="text-sky-400" />
        <span className="dvas-label font-semibold">Mission Report</span>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">
        <div>
          <label className="mono text-[10px] text-slate-500 block mb-1">Operator Name</label>
          <input
            type="text"
            value={meta.operator}
            onChange={e => setMeta(m => ({ ...m, operator: e.target.value }))}
            className="w-full bg-slate-900 border border-slate-700 focus:border-sky-500 outline-none px-3 py-1.5 rounded text-sm mono text-slate-200"
            placeholder="Officer name"
          />
        </div>
        <div>
          <label className="mono text-[10px] text-slate-500 block mb-1">Location</label>
          <input
            type="text"
            value={meta.location}
            onChange={e => setMeta(m => ({ ...m, location: e.target.value }))}
            className="w-full bg-slate-900 border border-slate-700 focus:border-sky-500 outline-none px-3 py-1.5 rounded text-sm mono text-slate-200"
            placeholder="Location / area"
          />
        </div>
        <div>
          <label className="mono text-[10px] text-slate-500 block mb-1">Mission ID</label>
          <input
            type="text"
            value={meta.mission_id}
            onChange={e => setMeta(m => ({ ...m, mission_id: e.target.value }))}
            className="w-full bg-slate-900 border border-slate-700 focus:border-sky-500 outline-none px-3 py-1.5 rounded text-sm mono text-slate-200"
            placeholder="MSN-001"
          />
        </div>
      </div>
      <div className="flex items-center gap-3">
        <button
          onClick={() => exportFile('pdf')}
          disabled={!hasData || exporting === 'pdf'}
          className="flex items-center gap-2 px-4 py-2 rounded-lg mono text-xs font-semibold transition-all disabled:opacity-40"
          style={{ background: 'rgba(14,165,233,0.15)', color: '#38bdf8', border: '1px solid rgba(14,165,233,0.35)' }}
        >
          <Download size={13} />
          {exporting === 'pdf' ? 'Generating…' : 'Export PDF Report'}
        </button>
        <button
          onClick={() => exportFile('csv')}
          disabled={!hasData || exporting === 'csv'}
          className="flex items-center gap-2 px-4 py-2 rounded-lg mono text-xs font-semibold transition-all disabled:opacity-40"
          style={{ background: 'var(--dvas-surface-2)', color: 'var(--dvas-text-muted)', border: '1px solid var(--dvas-border)' }}
        >
          <Download size={13} />
          {exporting === 'csv' ? 'Exporting…' : 'Export CSV Data'}
        </button>
      </div>
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   MAIN EXPORT
   ────────────────────────────────────────────────────────────── */
export default function CrowdAnalytics({ crowdSeries, drones }) {
  const { streams } = useActiveStreams();
  const [activeDrone, setActiveDrone] = useState(null);
  const [activeMissionId, setActiveMissionId] = useState(null);
  const [activeModel, setActiveModel] = useState('csrnet_parta');
  const [modelSwitching, setModelSwitching] = useState(false);
  const [streamRunning, setStreamRunning] = useState(false);
  const [startingStream, setStartingStream] = useState(false);

  // Dynamic drone list from active streams or fallback to props
  const droneList = streams?.length > 0
    ? streams.map(s => ({ id: s.drone_id, ...s }))
    : (drones?.length > 0
        ? drones.filter(d => (d.status === 'live' || d.status === 'draining') && !/^DRN-00[1-5]$/i.test(d.id || d.drone_id))
        : []);

  // Auto-select first active drone once discovered
  useEffect(() => {
    if (droneList.length === 0) return;
    setActiveDrone(prev => {
      if (prev && droneList.some(d => d.id === prev)) return prev;
      const live = droneList.find(d => d.status === 'live' || (d.frame_count || 0) > 0 || (d.messages_ready || 0) > 0);
      return (live || droneList[0])?.id || null;
    });
  }, [droneList]);

  // Track mission ID for selected drone
  useEffect(() => {
    if (!activeDrone || !droneList.length) return;
    const droneObj = droneList.find(d => d.id === activeDrone);
    const mId = droneObj?.mission_id || null;
    setActiveMissionId(mId);
  }, [activeDrone, droneList]);

  // SSE rolling window for real-time chart (last 120 readings)
  const [sseWindow, setSseWindow]         = useState([]);
  const [recentCounts, setRecentCounts]   = useState([]);
  const [realtimeCount, setRealtimeCount] = useState(null);
  const [peakCount, setPeakCount]         = useState(0);
  const [peakTimestamp, setPeakTimestamp] = useState(null);
  const [alertCount, setAlertCount]       = useState(0);

  // Reset realtime count when switching drones
  useEffect(() => {
    setRealtimeCount(null);
  }, [activeDrone]);

  // Data hooks
  const { data: readingsData, loading: readingsLoading } = useCrowdReadings(activeDrone || '', 4000);
  const { data: timelineData }    = useCrowdTimeline(activeDrone || '', 24);
  const { data: sessionSummary }  = useCrowdSessionSummary(10000);
  const { data: modelInfo }       = useCrowdModel(30000);
  const { data: healthData }      = useCrowdHealth(10000);

  // Sync stream running state from health check
  useEffect(() => {
    if (healthData && typeof healthData.stream_running === 'boolean') {
      setStreamRunning(healthData.stream_running);
    }
  }, [healthData]);

  // Sync model info from API
  useEffect(() => {
    if (modelInfo?.model_type) {
      setActiveModel(modelInfo.model_type);
    }
  }, [modelInfo]);

  // Update alert count from session summary
  useEffect(() => {
    if (sessionSummary) {
      setAlertCount(sessionSummary.alert_timestamps?.length || 0);
      if (peakCount === 0 && sessionSummary.peak_count > 0) {
        setPeakCount(sessionSummary.peak_count);
        setPeakTimestamp(sessionSummary.peak_timestamp);
      }
    }
  }, [sessionSummary]); // eslint-disable-line react-hooks/exhaustive-deps

  // Handle SSE events from Analytics Gateway
  const handleSSEEvent = useCallback((evt) => {
    const isCrowd = evt?.stream === 'crowd' || evt?.service === 'crowd' || evt?.event_type === 'crowd_threshold';
    if (!isCrowd) return;

    const payload = evt.payload || evt.data || evt;
    const countVal = payload.estimated_count ?? payload.people_count ?? payload.count ?? payload.density;
    if (countVal == null) return;
    const count = Number(countVal);
    const ts = payload.ts || new Date().toISOString();
    const eventDroneId = payload.drone_id || evt.drone_id;

    if (!activeDrone || !eventDroneId || eventDroneId === activeDrone || eventDroneId.includes(activeDrone) || activeDrone.includes(eventDroneId)) {
      setRealtimeCount(count);
      setRecentCounts(c => [...c.slice(-9), count]);

      const entry = {
        displayTime: typeof ts === 'string' && ts.length >= 19 ? ts.slice(11, 19) : new Date().toISOString().slice(11, 19),
        crowd: count,
      };
      setSseWindow(w => [...w.slice(-119), entry]);

      if (count > peakCount) {
        setPeakCount(count);
        setPeakTimestamp(ts);
      }
    }
  }, [activeDrone, peakCount]);

  const { connected: sseConnected } = useAnalyticsSSE(handleSSEEvent);

  const readings = Array.isArray(readingsData)
    ? readingsData
    : Array.isArray(readingsData?.readings)
      ? readingsData.readings
      : (readingsData && typeof readingsData === 'object' && (readingsData.estimated_count != null || readingsData.people_count != null))
        ? [readingsData]
        : [];

  const activeDroneReading = readings.find(r => r.drone_id === activeDrone || (activeDrone && r.drone_id && (r.drone_id.includes(activeDrone) || activeDrone.includes(r.drone_id))));
  const polledCount = activeDroneReading?.estimated_count ?? activeDroneReading?.people_count ?? (readings.length > 0 ? readings[0]?.estimated_count : null);

  // Live count prioritization: frame header count (realtimeCount) > SSE count > recent DB reading
  const liveCount = realtimeCount !== null
    ? realtimeCount
    : (polledCount != null ? Number(polledCount) : 0);
  const liveLevel = getDensityLevel(liveCount);
  const trend = computeTrend(recentCounts.length > 0 ? recentCounts : (readings.length > 0 ? readings.slice(0, 5).map(r => Number(r.estimated_count || 0)) : []));
  const avgDensity = (liveCount / 200).toFixed(2);

  const serviceOnline = sseConnected || !readingsLoading || (healthData?.status === 'ok');

  // Model switching handler
  const handleModelSwitch = async (modelType) => {
    if (modelType === activeModel) return;
    setModelSwitching(true);
    try {
      await switchCrowdModel(modelType);
      setActiveModel(modelType);
    } catch (err) {
      console.error('Model switch failed:', err);
    } finally {
      setModelSwitching(false);
    }
  };

  // Start stream handler
  const handleStartStream = async () => {
    setStartingStream(true);
    try {
      await api.get('/analytics/crowd/stream/start');
      setStreamRunning(true);
      setSseWindow([]);
      setRecentCounts([]);
      setPeakCount(0);
      setPeakTimestamp(null);
    } catch (err) {
      console.error('Failed to start stream:', err);
    } finally {
      setStartingStream(false);
    }
  };

  // Stop stream handler
  const handleStopStream = async () => {
    try {
      await api.get('/analytics/crowd/stream/stop');
      setStreamRunning(false);
    } catch (err) {
      console.error('Failed to stop stream:', err);
    }
  };

  // Frame-level count from annotated JPEG response headers (most reliable source)
  const handleFrameCount = useCallback((count, label) => {
    if (count == null || isNaN(count)) return;
    const c = Number(count);
    setRealtimeCount(c);
    setRecentCounts(prev => [...prev.slice(-9), c]);

    const ts = new Date().toISOString();
    const entry = {
      displayTime: ts.slice(11, 19),
      crowd: c,
    };
    setSseWindow(prev => [...prev.slice(-119), entry]);

    if (c > peakCount) {
      setPeakCount(c);
      setPeakTimestamp(ts);
    }
  }, [peakCount]);

  return (
    <div className="flex flex-col gap-5" style={{ fontFamily: "'Plus Jakarta Sans', sans-serif" }}>

      {/* HEADER — Model Selector + Service Badge */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <ModelSelector activeModel={activeModel} onSwitch={handleModelSwitch} switching={modelSwitching} />
        </div>
        <ServiceBadge online={sseConnected || serviceOnline} />
      </div>

      {/* LIVE FEED & HEATMAP STREAM */}
      <LiveFeedSection
        activeDrone={activeDrone}
        droneList={droneList}
        setActiveDrone={setActiveDrone}
        liveCount={liveCount}
        liveLevel={liveLevel}
        streamRunning={streamRunning}
        activeModel={activeModel}
        activeMissionId={activeMissionId}
        onStartStream={handleStartStream}
        onStopStream={handleStopStream}
        startingStream={startingStream}
        onFrameCount={handleFrameCount}
      />

      {/* KPI STRIP */}
      <CrowdKPIStrip
        liveCount={liveCount}
        peakCount={peakCount}
        peakTimestamp={peakTimestamp}
        alertCount={alertCount}
        avgDensity={avgDensity}
        densityLevel={liveLevel}
        trend={trend}
        sseConnected={sseConnected}
      />

      {/* PEOPLE COUNT CHART + DRONE STATUS */}
      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-12 lg:col-span-8">
          <Panel title="People Count Over Time" icon={Activity} iconClass="text-sky-400"
            right={
              <div className="flex items-center gap-2">
                <ServiceBadge online={sseConnected || serviceOnline} />
              </div>
            }>
            <PeopleCountChart
              timelineData={timelineData}
              sseWindow={sseWindow}
              online={serviceOnline}
            />
          </Panel>
        </div>
        <div className="col-span-12 lg:col-span-4">
          <Panel title="Drone Crowd Status" icon={Radio} iconClass="text-violet-400">
            <DroneStatusGrid readings={readings} droneList={droneList} />
          </Panel>
        </div>
      </div>

      {/* SESSION STATS */}
      <SessionStatsRow summary={sessionSummary} />

      {/* MISSION REPORT EXPORT */}
      <MissionExport hasData={sessionSummary?.total_frames > 0} activeModel={activeModel} />

    </div>
  );
}
