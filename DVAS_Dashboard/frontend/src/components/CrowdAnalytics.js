/**
 * CrowdAnalytics.js
 * ─────────────────────────────────────────────────────────────────────────────
 * Crowd Analytics page — fully connected to live analytics services.
 * Kerala Police | Drone Video Analytics System
 *
 * Data sources (live):
 *   - /api/analytics/crowd/readings   → live per-drone person counts (5s poll)
 *   - /api/analytics/crowd/timeline   → 24h density chart (30s poll)
 *   - /api/analytics/crowd/heatmap    → MinIO presigned heatmap URL (15s poll)
 *   - /api/analytics/events/stream    → SSE for real-time count updates
 *
 * Demo video (/DETECTED/Demo_annotated.mp4) kept for the DRN-003 live feed.
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts';
import {
  Users, Activity, AlertTriangle, Thermometer, MapPin,
  TrendingUp, Radio, Play, Pause, VideoOff, RefreshCw, Wifi, WifiOff,
} from 'lucide-react';
import {
  useCrowdReadings, useCrowdTimeline, useCrowdHeatmap, useAnalyticsSSE,
} from '../lib/useAnalytics';

/* ───────────────────────────────────────────────────────────────
   CONSTANTS
   ────────────────────────────────────────────────────────────── */
const DENSITY_LEVELS = [
  { label: 'Empty',      color: '#64748b', threshold: 0   },
  { label: 'Sparse',     color: '#10b981', threshold: 50  },
  { label: 'Moderate',   color: '#3b82f6', threshold: 150 },
  { label: 'Dense',      color: '#f59e0b', threshold: 300 },
  { label: 'Very Dense', color: '#f97316', threshold: 500 },
  { label: 'Extreme',    color: '#ef4444', threshold: 700 },
];

function getDensityLevel(count) {
  let level = DENSITY_LEVELS[0];
  for (const l of DENSITY_LEVELS) {
    if (count >= l.threshold) level = l;
  }
  return level;
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
   SECTION 1 — Live Detection Video Stream
   ────────────────────────────────────────────────────────────── */
function LiveStreamSection({ activeDrone, drones, setActiveDrone, liveCount, liveLevel }) {
  const [playing, setPlaying] = useState(true);
  const videoRef = useRef(null);
  const isDemo = activeDrone === 'DRN-003';

  const togglePlay = () => {
    if (videoRef.current) {
      if (playing) videoRef.current.pause();
      else videoRef.current.play();
    }
    setPlaying(!playing);
  };

  return (
    <div className="rounded-xl overflow-hidden"
      style={{ background: 'var(--dvas-surface)', border: '1px solid var(--dvas-border)' }}>
      {/* Header */}
      <div className="px-4 py-3 flex items-center gap-3"
        style={{ borderBottom: '1px solid var(--dvas-border)' }}>
        <span className={`w-2 h-2 rounded-full dvas-live-dot ${isDemo ? 'bg-red-500' : 'bg-slate-500'}`} />
        <span className="dvas-label font-semibold">Crowd Detection Live Feed</span>
        <div className="flex items-center gap-1 ml-2">
          {(drones?.length > 0 ? drones : [{ id: 'DRN-001' }, { id: 'DRN-002' }, { id: 'DRN-003' }, { id: 'DRN-004' }]).map(d => (
            <button
              key={d.id}
              onClick={() => setActiveDrone(d.id)}
              className="mono text-[10px] px-2 py-1 rounded transition-all"
              style={{
                background: activeDrone === d.id ? 'rgba(14,165,233,0.15)' : 'var(--dvas-surface-2)',
                color: activeDrone === d.id ? '#38bdf8' : 'var(--dvas-text-muted)',
                border: `1px solid ${activeDrone === d.id ? 'rgba(14,165,233,0.35)' : 'var(--dvas-border)'}`,
              }}
            >{d.id}</button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2">
          {isDemo && (
            <button
              onClick={togglePlay}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all"
              style={{ background: 'var(--dvas-elev)', color: 'var(--dvas-text-muted)', border: '1px solid var(--dvas-border)' }}
            >
              {playing ? <Pause size={12} /> : <Play size={12} />}
              {playing ? 'Pause' : 'Play'}
            </button>
          )}
        </div>
      </div>

      {/* Video area */}
      <div className="relative bg-black flex items-center justify-center" style={{ height: 420 }}>
        {isDemo ? (
          <>
            <video
              ref={videoRef}
              src="/DETECTED/Demo_annotated.mp4"
              autoPlay loop muted
              className="w-full h-full object-cover"
            />
            <div className="absolute top-3 left-3 flex flex-col gap-1.5">
              <div className="mono text-[10px] bg-black/80 px-2 py-1 rounded flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full" style={{ background: liveLevel.color }} />
                <span style={{ color: liveLevel.color }}>{liveLevel.label}</span>
                <span className="text-slate-200 ml-1 font-bold">{liveCount} persons</span>
              </div>
              <div className="mono text-[10px] bg-black/80 px-2 py-1 rounded text-slate-300">
                {(liveCount / 200).toFixed(2)} p/m²
              </div>
            </div>
            <div className="absolute top-3 right-3 mono text-[10px] bg-black/80 px-2 py-1 rounded text-emerald-400 flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 dvas-live-dot" />
              LIVE · DEMO MODE
            </div>
            <div className="absolute bottom-3 left-3 mono text-[10px] bg-black/80 px-2 py-1 rounded text-slate-400">
              Crowd Analytics Model · YOLOv8 · Tile-Based Detection
            </div>
          </>
        ) : (
          <div className="text-center text-slate-500 flex flex-col items-center">
            <VideoOff size={48} className="mb-3 opacity-50 text-slate-600" />
            <p className="mono text-sm">Crowd analytics offline</p>
            <p className="mono text-[10px] mt-1 text-slate-600">Select DRN-003 to view demo feed</p>
          </div>
        )}
      </div>
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 2 — Crowd KPI Strip (LIVE DATA)
   ────────────────────────────────────────────────────────────── */
function CrowdKPIStrip({ readings, sseConnected }) {
  // readings is array of {drone_id, people_count, density_label, ts, alert}
  const items = Array.isArray(readings) ? readings : [];
  const totalCount    = items.reduce((s, r) => s + (r.people_count || 0), 0);
  const alertCount    = items.filter(r => r.alert || (r.people_count || 0) > 300).length;
  const avgDensity    = items.length > 0
    ? (items.reduce((s, r) => s + (r.people_count || 0), 0) / (items.length * 200)).toFixed(2)
    : '0.00';
  const breachedZones = alertCount;
  const activeDrones  = items.filter(r => (r.people_count || 0) > 0).length;

  const kpis = [
    { label: 'Total Persons Detected', value: totalCount.toLocaleString(), tone: 'text-sky-400',    icon: Users,         sub: `${activeDrones} active zone${activeDrones !== 1 ? 's' : ''}` },
    { label: 'Drone Alerts Active',    value: alertCount,                  tone: 'text-red-400',    icon: AlertTriangle, sub: alertCount > 0 ? 'Density threshold exceeded' : 'All zones nominal' },
    { label: 'Avg Density (p/m²)',     value: avgDensity,                  tone: 'text-amber-400',  icon: Thermometer,   sub: 'People per square metre' },
    { label: 'Zones Breached',         value: breachedZones,               tone: breachedZones > 0 ? 'text-red-400' : 'text-emerald-400', icon: MapPin, sub: `${items.length} zones monitored` },
    { label: 'Live Stream',            value: sseConnected ? 'ON' : 'OFF', tone: sseConnected ? 'text-emerald-400' : 'text-slate-500', icon: TrendingUp, sub: sseConnected ? 'Real-time SSE' : 'Polling only' },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
      {kpis.map(({ label, value, tone, icon: Icon, sub }) => (
        <div key={label} className="rounded-xl p-4 flex flex-col gap-1"
          style={{ background: 'var(--dvas-surface)', border: '1px solid var(--dvas-border)' }}>
          <div className="flex items-center gap-2">
            <Icon size={13} className={`${tone} shrink-0`} />
            <span className="dvas-label" style={{ fontSize: 10 }}>{label}</span>
          </div>
          <div className={`mono font-bold text-2xl mt-1 ${tone}`}>{value ?? '—'}</div>
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
  // Use SSE rolling window if available and non-empty, else timeline from API
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
        crowd: Number(row.density ?? row.crowd ?? row.people_count ?? 0),
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
          <Area type="monotone" dataKey="crowd" stroke="#38bdf8" fill="url(#gCrowd)" strokeWidth={2} name="Persons" dot={false} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 4 — Heatmap Panel
   ────────────────────────────────────────────────────────────── */
function HeatmapPanel({ heatmapData }) {
  const url = heatmapData?.url || heatmapData?.presigned_url;
  const ts  = heatmapData?.ts;

  return (
    <div className="rounded-xl overflow-hidden flex flex-col" style={{ minHeight: 220, background: 'var(--dvas-surface-2)', border: '1px solid var(--dvas-border)' }}>
      {url ? (
        <>
          <img src={url} alt="Crowd Density Heatmap" className="w-full object-contain" style={{ maxHeight: 260 }} />
          {ts && (
            <div className="px-3 py-1.5 mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>
              Heatmap captured: {fmtAge(ts)}
            </div>
          )}
        </>
      ) : (
        <div className="flex-1 flex flex-col items-center justify-center gap-2 p-6 opacity-40">
          <Thermometer size={36} className="text-sky-400" />
          <span className="mono text-xs text-slate-400">No heatmap available yet</span>
          <span className="mono text-[10px] text-slate-500">Generated when crowd-analyzer detects activity</span>
        </div>
      )}
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 5 — Drone Status Grid (LIVE per-drone readings)
   ────────────────────────────────────────────────────────────── */
function DroneStatusGrid({ readings, drones }) {
  // Build a map from readings keyed by drone_id
  const readingMap = {};
  if (Array.isArray(readings)) {
    for (const r of readings) {
      if (r.drone_id) readingMap[r.drone_id] = r;
    }
  }

  const droneList = drones?.length > 0
    ? drones
    : [{ id: 'DRN-001' }, { id: 'DRN-002' }, { id: 'DRN-003' }, { id: 'DRN-004' }];

  return (
    <div className="flex flex-col gap-2">
      {droneList.map(d => {
        const r = readingMap[d.id] || readingMap[d.drone_id];
        const count = r ? Number(r.people_count || 0) : 0;
        const level = getDensityLevel(count);
        const pct   = Math.min(100, Math.round((count / 700) * 100));
        const hasData = !!r;
        const label = r?.density_label || level.label;

        return (
          <div key={d.id} className="rounded-lg p-3 flex flex-col gap-2"
            style={{
              background: 'var(--dvas-surface-2)',
              border: `1px solid ${hasData ? 'rgba(56,189,248,0.4)' : 'var(--dvas-border)'}`,
              opacity: hasData ? 1 : 0.5,
            }}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="mono text-xs font-bold" style={{ color: 'var(--dvas-text)' }}>{d.id}</span>
                <span className="mono text-[10px] px-1.5 py-0.5 rounded font-semibold"
                  style={{ background: `${level.color}22`, color: level.color, border: `1px solid ${level.color}44` }}>
                  {hasData ? label : 'Offline'}
                </span>
                {hasData && count > 300 && (
                  <span className="mono text-[10px] text-red-400 flex items-center gap-1">
                    <AlertTriangle size={10} className="dvas-live-dot" /> ALERT
                  </span>
                )}
              </div>
              <div className="mono text-right">
                <div className="text-sm font-bold" style={{ color: level.color }}>{count.toLocaleString()}</div>
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
                {hasData ? (r.ts ? fmtAge(r.ts) : 'Live') : 'Disconnected'}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   MAIN EXPORT
   ────────────────────────────────────────────────────────────── */
export default function CrowdAnalytics({ drones }) {
  const [activeDrone, setActiveDrone] = useState('DRN-003');
  // Rolling SSE window for the real-time chart (last 30 readings)
  const [sseWindow, setSseWindow]     = useState([]);

  const { data: readingsData, loading: readingsLoading } = useCrowdReadings('', 5000);
  const { data: timelineData }    = useCrowdTimeline('', 24);
  const { data: heatmapData }     = useCrowdHeatmap(activeDrone, 15000);

  // Handle SSE events — update rolling chart window
  const handleSSEEvent = useCallback((evt) => {
    if (evt?.service !== 'crowd') return;
    const count  = evt.data?.people_count ?? evt.data?.count ?? 0;
    const ts     = evt.data?.ts || new Date().toISOString();
    const entry  = {
      displayTime: ts.slice(11, 19), // HH:MM:SS
      crowd: Number(count),
    };
    setSseWindow(w => [...w.slice(-59), entry]); // keep last 60
  }, []);

  const { connected: sseConnected } = useAnalyticsSSE(handleSSEEvent);

  // Auto-select active drone
  useEffect(() => {
    if (drones?.length > 0) {
      setActiveDrone(drones.find(d => d.id === 'DRN-003') ? 'DRN-003' : drones[0].id);
    }
  }, [drones]);

  const readings = Array.isArray(readingsData) ? readingsData
    : Array.isArray(readingsData?.readings) ? readingsData.readings : [];

  // Get live count for active drone for video overlay
  const activeDroneReading = readings.find(r => r.drone_id === activeDrone);
  const liveCount = activeDroneReading?.people_count ?? 0;
  const liveLevel = getDensityLevel(liveCount);

  const serviceOnline = !readingsLoading && readings.length > 0;

  return (
    <div className="flex flex-col gap-5" style={{ fontFamily: "'Plus Jakarta Sans', sans-serif" }}>

      {/* LIVE STREAM */}
      <LiveStreamSection
        activeDrone={activeDrone}
        drones={drones}
        setActiveDrone={setActiveDrone}
        liveCount={liveCount}
        liveLevel={liveLevel}
      />

      {/* KPI STRIP */}
      <CrowdKPIStrip readings={readings} sseConnected={sseConnected} />

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
            <DroneStatusGrid readings={readings} drones={drones} />
          </Panel>
        </div>
      </div>

      {/* HEATMAP */}
      <Panel
        title="Crowd Density Heatmap"
        icon={Thermometer}
        iconClass="text-amber-400"
        right={
          <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>
            Generated by crowd-analyzer · MinIO stored
          </span>
        }>
        <HeatmapPanel heatmapData={heatmapData} />
      </Panel>

    </div>
  );
}
