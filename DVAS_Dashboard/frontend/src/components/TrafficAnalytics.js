/**
 * TrafficAnalytics.js
 * ─────────────────────────────────────────────────────────────────────────────
 * Traffic & ANPR page — fully connected to live analytics services.
 * Kerala Police | Drone Video Analytics System
 *
 * Data sources (live):
 *   - /api/analytics/vehicle/tracks       → Tracked Vehicles ledger (5s poll)
 *   - /api/analytics/vehicle/by-colour    → Classification donut (30s poll)
 *   - /api/analytics/vehicle/stats        → 24h flow chart (30s poll)
 *   - /api/analytics/plates/recent        → ANPR window images (5s poll)
 *   - /api/analytics/alerts/theft         → Watchlist alerts (10s poll)
 *   - /api/analytics/watchlist            → Watchlist management
 *   - /api/analytics/events/stream        → SSE for real-time events
 *
 * Demo videos kept: /DETECTED/DRN-001_h264.mp4 etc.
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, PieChart, Pie, Cell,
} from 'recharts';
import {
  Car, Activity, AlertTriangle, CheckCircle, Hash,
  TrendingUp, Gauge, ShieldAlert, Play, Pause, Search,
  Clock, VideoOff, RefreshCw, Plus, Trash2, Wifi, WifiOff,
} from 'lucide-react';
import {
  useVehicleTracks, useVehicleByColour, useVehicleStats,
  usePlatesRecent, useTheftAlerts, useWatchlist, useAnalyticsSSE,
} from '../lib/useAnalytics';
import { api } from '../lib/api';

/* ───────────────────────────────────────────────────────────────
   CONSTANTS
   ────────────────────────────────────────────────────────────── */
const TRAFFIC_DRONES = ['DRN-001', 'DRN-002', 'DRN-004'];
const DRONE_VIDEO_MAP = {
  'DRN-001': '/DETECTED/DRN-001_h264.mp4',
  'DRN-002': '/DETECTED/DRN-002_h264.mp4',
  'DRN-004': '/DETECTED/DRN-004_h264.mp4',
};
const ALL_DRONE_IDS = ['DRN-001', 'DRN-002', 'DRN-003', 'DRN-004'];

// Colour palette for the donut chart
const COLOUR_PALETTE = [
  '#818cf8','#14b8a6','#f59e0b','#f87171','#34d399','#fb923c',
  '#a78bfa','#38bdf8','#4ade80','#facc15',
];

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

function normalizeVehicleType(raw) {
  if (!raw) return 'Unknown';
  const lower = raw.toLowerCase();
  if (lower === 'car') return 'Car';
  if (lower === 'truck') return 'Truck';
  if (lower.includes('3-wheel') || lower === '3w') return '3-Wheeler';
  if (lower.includes('two-wheel') || lower.includes('2-wheel') || lower === 'motorcycle') return 'Two-Wheeler';
  if (lower === 'bus') return 'Bus';
  if (lower === 'van') return 'Van';
  return raw.charAt(0).toUpperCase() + raw.slice(1).toLowerCase();
}

function Panel({ title, icon: Icon, iconClass = 'text-teal-400', children, right, badge, noPad }) {
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
   SECTION 1 — Live Detection Video Stream
   ────────────────────────────────────────────────────────────── */
function LiveStreamSection({ activeDrone, setActiveDrone, drones, vehicleCount }) {
  const [playing, setPlaying] = useState(true);
  const videoRef = useRef(null);
  const isTrafficDrone = TRAFFIC_DRONES.includes(activeDrone);
  const videoSrc = DRONE_VIDEO_MAP[activeDrone];
  const droneList = drones?.length > 0 ? drones : ALL_DRONE_IDS.map(id => ({ id }));

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
      <div className="px-4 py-3 flex items-center gap-3"
        style={{ borderBottom: '1px solid var(--dvas-border)' }}>
        <span className={`w-2 h-2 rounded-full dvas-live-dot ${isTrafficDrone ? 'bg-violet-500' : 'bg-slate-500'}`} />
        <span className="dvas-label font-semibold">Traffic & Vehicle Detection Live Feed</span>
        <div className="flex items-center gap-1 ml-2">
          {droneList.map(d => (
            <button key={d.id} onClick={() => setActiveDrone(d.id)}
              className="mono text-[10px] px-2 py-1 rounded transition-all"
              style={{
                background: activeDrone === d.id ? 'rgba(139,92,246,0.15)' : 'var(--dvas-surface-2)',
                color: activeDrone === d.id ? '#a78bfa' : 'var(--dvas-text-muted)',
                border: `1px solid ${activeDrone === d.id ? 'rgba(139,92,246,0.35)' : 'var(--dvas-border)'}`,
              }}>{d.id}</button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2">
          {vehicleCount > 0 && (
            <span className="mono text-[10px] px-2 py-1 rounded" style={{ background: 'rgba(139,92,246,0.12)', color: '#a78bfa', border: '1px solid rgba(139,92,246,0.2)' }}>
              {vehicleCount} tracked
            </span>
          )}
          {isTrafficDrone && (
            <button onClick={togglePlay}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all"
              style={{ background: 'var(--dvas-elev)', color: 'var(--dvas-text-muted)', border: '1px solid var(--dvas-border)' }}>
              {playing ? <Pause size={12} /> : <Play size={12} />}
              {playing ? 'Pause' : 'Play'}
            </button>
          )}
        </div>
      </div>

      <div className="relative bg-black flex items-center justify-center" style={{ height: 420 }}>
        {isTrafficDrone && videoSrc ? (
          <>
            <video ref={videoRef} src={videoSrc} autoPlay loop muted className="w-full h-full object-cover" />
            <div className="absolute top-3 left-3 flex flex-col gap-1.5">
              <div className="mono text-[10px] bg-black/80 px-2 py-1 rounded text-violet-300">
                Vehicle tracking active · {activeDrone}
              </div>
              <div className="mono text-[10px] bg-black/80 px-2 py-1 rounded text-teal-300">
                ANPR active · Number plate detection
              </div>
            </div>
            <div className="absolute top-3 right-3 mono text-[10px] bg-black/80 px-2 py-1 rounded text-emerald-400 flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 dvas-live-dot" />
              LIVE · DEMO MODE
            </div>
            <div className="absolute bottom-3 left-3 mono text-[10px] bg-black/80 px-2 py-1 rounded text-slate-400">
              YOLOv8 Vehicle Tracker · ANPR OCR Engine
            </div>
            <div className="absolute bottom-3 right-3 mono text-[10px] bg-black/80 px-2 py-1 rounded text-slate-400">
              {activeDrone} · Thiruvananthapuram
            </div>
          </>
        ) : (
          <div className="text-center text-slate-500 flex flex-col items-center">
            <VideoOff size={48} className="mb-3 opacity-50 text-slate-600" />
            <p className="mono text-sm">Traffic analytics offline</p>
            <p className="mono text-[10px] mt-1 text-slate-600">Select DRN-001, DRN-002, or DRN-004 to view traffic feed</p>
          </div>
        )}
      </div>
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 2 — Traffic KPI Strip (LIVE)
   ────────────────────────────────────────────────────────────── */
function TrafficKPIStrip({ vehicles, plates, theftAlerts, statsData }) {
  const vehicleArr  = Array.isArray(vehicles) ? vehicles : [];
  const platesArr   = Array.isArray(plates) ? plates : [];
  const alertsArr   = Array.isArray(theftAlerts?.alerts || theftAlerts) ? (theftAlerts?.alerts || theftAlerts) : [];

  const totalVehicles  = vehicleArr.length || statsData?.total_tracked || 0;
  const moving         = vehicleArr.filter(v => v.state === 'Moving' || v.state === 'moving').length;
  const stopped        = totalVehicles - moving;
  const avgSpeed       = vehicleArr.length > 0
    ? Math.round(vehicleArr.reduce((s, v) => s + (v.speed_kmh || v.speed || 0), 0) / vehicleArr.length)
    : statsData?.avg_speed_kmh || 0;
  const platesCount    = platesArr.length || statsData?.total_plates || 0;
  const theftCount     = alertsArr.length;

  const kpis = [
    { label: 'Vehicles Tracked',  value: totalVehicles,               tone: 'text-violet-400', icon: Car,           sub: 'Active across all drones' },
    { label: 'Moving / Stopped',  value: `${moving} / ${stopped}`,    tone: 'text-teal-400',   icon: Activity,      sub: 'Current vehicle states' },
    { label: 'Avg Speed (km/h)',  value: avgSpeed,                     tone: 'text-amber-400',  icon: Gauge,         sub: 'Across tracked vehicles' },
    { label: 'Plates Detected',   value: platesCount,                  tone: 'text-sky-400',    icon: Hash,          sub: 'ANPR readings from drones' },
    { label: 'Watchlist Matches', value: theftCount,                   tone: theftCount > 0 ? 'text-red-400' : 'text-emerald-400', icon: AlertTriangle, sub: theftCount > 0 ? 'ALERT — matches found' : 'No watchlist hits' },
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
   SECTION 3 — Tracked Vehicles Window (LIVE LEDGER)
   ────────────────────────────────────────────────────────────── */
function TrackedVehiclesWindow({ vehicles, loading }) {
  const [sortKey, setSortKey] = useState('last_seen');
  const arr = Array.isArray(vehicles) ? vehicles : [];

  const sorted = [...arr].sort((a, b) => {
    if (sortKey === 'speed')     return (b.speed_kmh || b.speed || 0) - (a.speed_kmh || a.speed || 0);
    if (sortKey === 'last_seen') return new Date(b.last_seen || b.updated_at || 0) - new Date(a.last_seen || a.updated_at || 0);
    return 0;
  });

  const stateColor = { Moving: '#2dd4bf', moving: '#2dd4bf', Slow: '#fbbf24', slow: '#fbbf24', Stopped: '#f87171', stopped: '#f87171' };

  if (loading && arr.length === 0) {
    return (
      <div className="flex items-center justify-center py-10 gap-2">
        <RefreshCw size={16} className="text-slate-500 animate-spin" />
        <span className="mono text-xs text-slate-500">Loading vehicle tracks…</span>
      </div>
    );
  }

  if (!loading && arr.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-10 gap-2">
        <Car size={36} className="text-slate-600 opacity-40" />
        <span className="mono text-xs text-slate-500">No active vehicle tracks</span>
        <span className="mono text-[10px] text-slate-600">Vehicle analyzer will populate this when drones stream frames</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 flex-wrap">
        {['last_seen', 'speed'].map(k => (
          <button key={k} onClick={() => setSortKey(k)}
            className="mono text-[10px] px-2.5 py-1 rounded-lg transition-all"
            style={{
              background: sortKey === k ? 'rgba(139,92,246,0.12)' : 'var(--dvas-surface-2)',
              color: sortKey === k ? '#a78bfa' : 'var(--dvas-text-muted)',
              border: `1px solid ${sortKey === k ? 'rgba(139,92,246,0.3)' : 'var(--dvas-border)'}`,
            }}>
            {k === 'last_seen' ? 'Recent First' : 'Speed ↓'}
          </button>
        ))}
        <span className="ml-auto mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>
          {arr.length} vehicles
        </span>
      </div>

      <div className="grid grid-cols-1 gap-1.5 max-h-[420px] overflow-y-auto pr-1">
        {sorted.map((v, idx) => {
          const speed  = v.speed_kmh ?? v.speed ?? 0;
          const colour = v.colour || v.vehicle_colour || '—';
          const vtype  = normalizeVehicleType(v.vehicle_type || v.type);
          const state  = v.state || (speed > 5 ? 'Moving' : 'Stopped');
          const drone  = v.drone_id || v.drone || '—';
          const plate  = v.plate_text || '';
          const lastTs = v.last_seen || v.updated_at || v.ts;
          const sc     = stateColor[state] || '#94a3b8';

          return (
            <div key={v.tracker_id || v.id || idx}
              className="flex items-center gap-3 px-3 py-2 rounded-lg"
              style={{ background: 'var(--dvas-surface-2)', border: '1px solid var(--dvas-border)' }}>
              <div className="shrink-0 w-[72px]">
                <span className="mono text-xs font-bold" style={{ color: 'var(--dvas-text)' }}>
                  #{v.tracker_id || v.id || idx}
                </span>
              </div>
              <div className="shrink-0 w-24">
                <span className="mono text-[10px] px-1.5 py-0.5 rounded font-semibold"
                  style={{ background: 'rgba(139,92,246,0.12)', color: '#a78bfa', border: '1px solid rgba(139,92,246,0.2)' }}>
                  {vtype}
                </span>
              </div>
              <div className="shrink-0 w-16 text-center">
                <span className="mono text-sm font-bold" style={{ color: sc }}>{speed}</span>
                <span className="mono text-[9px]" style={{ color: 'var(--dvas-text-dim)' }}> km/h</span>
              </div>
              <div className="shrink-0 w-16">
                <span className="mono text-[10px] px-1.5 py-0.5 rounded"
                  style={{ background: `${sc}18`, color: sc, border: `1px solid ${sc}33` }}>
                  {state}
                </span>
              </div>
              <div className="flex-1 min-w-0">
                <div className="mono text-[10px]" style={{ color: 'var(--dvas-text-muted)' }}>
                  {colour} {plate && <span className="text-teal-400 font-bold tracking-widest ml-1">· {plate}</span>}
                </div>
                <div className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>{drone}</div>
              </div>
              <div className="shrink-0 mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>
                <Clock size={9} className="inline mr-1" />{fmtAge(lastTs)}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 4 — Vehicle Colour Donut (LIVE)
   ────────────────────────────────────────────────────────────── */
function VehicleDonut({ colourData }) {
  // colourData: array of { colour: string, count: number } from API
  const arr = Array.isArray(colourData) ? colourData : [];
  const types = arr.map((item, i) => ({
    name:  item.colour || item.name || `Type ${i}`,
    value: item.count  || item.value || 0,
    color: COLOUR_PALETTE[i % COLOUR_PALETTE.length],
  }));

  if (types.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-8 gap-2 opacity-40">
        <Car size={32} className="text-teal-400" />
        <span className="mono text-xs text-slate-400">No vehicle data yet</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center gap-2">
      <ResponsiveContainer width="100%" height={200}>
        <PieChart>
          <Pie data={types} dataKey="value" innerRadius={50} outerRadius={80} paddingAngle={3} stroke="none">
            {types.map((t, i) => <Cell key={i} fill={t.color} />)}
          </Pie>
          <Tooltip {...CHART_TOOLTIP} />
        </PieChart>
      </ResponsiveContainer>
      <div className="flex gap-3 flex-wrap justify-center">
        {types.map(t => (
          <div key={t.name} className="flex items-center gap-1.5">
            <span className="w-3 h-1.5 rounded-full" style={{ background: t.color }} />
            <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-muted)' }}>{t.name}</span>
            <span className="mono text-[10px] font-bold" style={{ color: t.color }}>{t.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 4b — Vehicle Flow Chart (LIVE stats)
   ────────────────────────────────────────────────────────────── */
function VehicleFlowChart({ statsData }) {
  const timeline = Array.isArray(statsData?.hourly_timeline) ? statsData.hourly_timeline : [];

  // Fallback: synthesized empty-ish chart until data arrives
  const chartData = timeline.length > 0
    ? timeline.map(h => ({
        hour:        h.hour,
        cars:        h.cars ?? h.car ?? 0,
        twoWheelers: h.two_wheelers ?? h.twoWheelers ?? h.motorcycle ?? 0,
        heavy:       h.heavy ?? h.truck ?? h.bus ?? 0,
      }))
    : Array.from({ length: 24 }, (_, i) => ({
        hour: `${String(i).padStart(2, '0')}:00`,
        cars: 0, twoWheelers: 0, heavy: 0,
      }));

  return (
    <div className="flex flex-col gap-2">
      {timeline.length === 0 && (
        <div className="mono text-[10px] text-slate-500 text-center mb-1">
          Awaiting vehicle-analyzer data…
        </div>
      )}
      <ResponsiveContainer width="100%" height={220}>
        <AreaChart data={chartData} margin={{ top: 10, right: 8, left: -20, bottom: 0 }}>
          <defs>
            {[{ id: 'gCars', color: '#14b8a6' }, { id: 'g2W', color: '#818cf8' }, { id: 'gHvy', color: '#f59e0b' }].map(({ id, color }) => (
              <linearGradient key={id} id={id} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={color} stopOpacity={0.4} />
                <stop offset="100%" stopColor={color} stopOpacity={0} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid stroke="var(--dvas-border)" strokeDasharray="3 3" />
          <XAxis dataKey="hour" stroke="var(--dvas-text-muted)" fontSize={9} interval={3} />
          <YAxis stroke="var(--dvas-text-muted)" fontSize={9} />
          <Tooltip {...CHART_TOOLTIP} />
          <Area dataKey="cars"        stroke="#14b8a6" fill="url(#gCars)" strokeWidth={1.5} name="Cars"         dot={false} />
          <Area dataKey="twoWheelers" stroke="#818cf8" fill="url(#g2W)"   strokeWidth={1.5} name="Two-Wheelers" dot={false} />
          <Area dataKey="heavy"       stroke="#f59e0b" fill="url(#gHvy)"  strokeWidth={1.5} name="Heavy"        dot={false} />
        </AreaChart>
      </ResponsiveContainer>
      <div className="flex gap-4">
        {[{ c: '#14b8a6', l: 'Cars' }, { c: '#818cf8', l: 'Two-Wheelers' }, { c: '#f59e0b', l: 'Heavy' }].map(({ c, l }) => (
          <div key={l} className="flex items-center gap-1.5">
            <span className="w-3 h-1.5 rounded-full" style={{ background: c }} />
            <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-muted)' }}>{l}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 5 — ANPR Window (REAL vehicle + plate images)
   ────────────────────────────────────────────────────────────── */
function ANPRPlateWindow({ platesData, activeDrone, loading }) {
  const [page, setPage] = useState(0);
  const perPage = 5;

  useEffect(() => { setPage(0); }, [activeDrone]);

  const arr = Array.isArray(platesData) ? platesData : [];

  if (loading && arr.length === 0) {
    return (
      <div className="flex items-center justify-center py-10 gap-2">
        <RefreshCw size={16} className="text-slate-500 animate-spin" />
        <span className="mono text-xs text-slate-500">Loading ANPR data…</span>
      </div>
    );
  }

  if (!loading && arr.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-10 gap-3">
        <Search size={36} className="text-slate-600 opacity-50" />
        <p className="mono text-sm text-slate-500">No plate detections yet</p>
        <p className="mono text-[10px] text-slate-600">ANPR service will populate this when plates are detected</p>
      </div>
    );
  }

  const pages = Math.ceil(arr.length / perPage);
  const shown = arr.slice(page * perPage, page * perPage + perPage);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3 flex-wrap">
        <span className="mono text-[10px] px-3 py-1.5 rounded-lg font-semibold"
          style={{ background: 'rgba(139,92,246,0.12)', color: '#a78bfa', border: '1px solid rgba(139,92,246,0.3)' }}>
          ALL DRONES
        </span>
        <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>
          {arr.length} plates detected
        </span>
      </div>

      <div className="grid grid-cols-1 gap-3">
        {shown.map((p, i) => {
          const vehicleImg  = p.vehicle_image_url || p.vehicle_image || null;
          const plateImg    = p.plate_image_url   || p.plate_image   || null;
          const plateText   = p.plate_text || p.plate || '';
          const vtype       = normalizeVehicleType(p.vehicle_type || p.type);
          const colour      = p.colour || p.vehicle_colour || '';
          const conf        = p.confidence || p.plate_conf || 0;
          const droneId     = p.drone_id || p.drone || '—';
          const ts          = p.created_at || p.ts || p.linked_at;
          const watchlisted = p.watchlisted || p.is_stolen || false;

          return (
            <div key={p.id || i}
              className="flex items-center gap-4 px-3 py-3 rounded-xl transition-all"
              style={{
                background: watchlisted ? 'rgba(239,68,68,0.06)' : 'var(--dvas-surface-2)',
                border: `1px solid ${watchlisted ? 'rgba(239,68,68,0.3)' : 'var(--dvas-border)'}`,
              }}>

              {/* Vehicle Image */}
              <div className="shrink-0 w-36 h-24 rounded-lg overflow-hidden flex items-center justify-center relative"
                style={{ border: '1px solid var(--dvas-border)', background: 'var(--dvas-elev)' }}>
                {vehicleImg ? (
                  <img src={vehicleImg} alt={vtype} className="w-full h-full object-cover" />
                ) : (
                  <Car size={28} className="text-slate-500 opacity-40" />
                )}
                <div className="absolute bottom-0 left-0 right-0 bg-black/70 px-2 py-0.5">
                  <span className="mono text-[9px] text-violet-300 font-semibold">{vtype}</span>
                </div>
                {watchlisted && (
                  <div className="absolute top-0 left-0 right-0 bg-red-600/80 px-2 py-0.5 text-center">
                    <span className="mono text-[8px] text-white font-bold tracking-wider">WATCHLIST</span>
                  </div>
                )}
              </div>

              {/* Plate Image + Text */}
              <div className="shrink-0 flex flex-col gap-1">
                <div className="w-44 h-16 rounded overflow-hidden flex items-center justify-center"
                  style={{ background: 'rgba(20,184,166,0.06)', border: '2px solid rgba(20,184,166,0.25)' }}>
                  {plateImg ? (
                    <img src={plateImg} alt="License Plate" className="w-full h-full object-contain" />
                  ) : (
                    <span className="mono text-[10px] font-bold opacity-60 text-teal-400">[NO IMAGE]</span>
                  )}
                </div>
                {plateText && (
                  <div className="text-center">
                    <span className="mono text-sm font-black tracking-[0.2em]" style={{ color: '#2dd4bf' }}>
                      {plateText}
                    </span>
                  </div>
                )}
                {conf > 0 && (
                  <div className="text-center">
                    <span className="mono text-[9px]" style={{ color: 'var(--dvas-text-dim)' }}>
                      Conf: {Math.round(conf * 100)}%
                    </span>
                  </div>
                )}
              </div>

              {/* Details */}
              <div className="flex-1 min-w-0 grid grid-cols-2 gap-2">
                <div>
                  <div className="dvas-label mb-0.5" style={{ fontSize: 9 }}>VEHICLE TYPE</div>
                  <div className="mono text-xs font-semibold" style={{ color: 'var(--dvas-text)' }}>{vtype}</div>
                </div>
                <div>
                  <div className="dvas-label mb-0.5" style={{ fontSize: 9 }}>COLOUR</div>
                  <div className="mono text-xs font-semibold" style={{ color: 'var(--dvas-text)' }}>{colour || '—'}</div>
                </div>
                <div>
                  <div className="dvas-label mb-0.5" style={{ fontSize: 9 }}>DRONE</div>
                  <span className="mono text-[10px] px-1.5 py-0.5 rounded"
                    style={{ background: 'rgba(129,140,248,0.12)', color: '#818cf8', border: '1px solid rgba(129,140,248,0.2)' }}>
                    {droneId}
                  </span>
                </div>
                <div>
                  <div className="dvas-label mb-0.5" style={{ fontSize: 9 }}>DETECTED</div>
                  <div className="mono text-[10px]" style={{ color: 'var(--dvas-text-muted)' }}>{fmtAge(ts)}</div>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {pages > 1 && (
        <div className="flex items-center justify-between">
          <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>
            {page * perPage + 1}–{Math.min((page + 1) * perPage, arr.length)} of {arr.length}
          </span>
          <div className="flex gap-1">
            {Array.from({ length: pages }).map((_, i) => (
              <button key={i} onClick={() => setPage(i)}
                className="w-6 h-6 rounded text-[10px] mono font-bold transition-colors"
                style={{
                  background: i === page ? 'rgba(20,184,166,0.15)' : 'var(--dvas-surface-2)',
                  color: i === page ? '#2dd4bf' : 'var(--dvas-text-muted)',
                  border: `1px solid ${i === page ? 'rgba(20,184,166,0.3)' : 'var(--dvas-border)'}`,
                }}>{i + 1}</button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 6 — Theft / Watchlist Alerts (LIVE)
   ────────────────────────────────────────────────────────────── */
function WatchlistPanel({ theftAlerts, watchlist, onAdd, onRemove }) {
  const [newPlate,  setNewPlate]  = useState('');
  const [newReason, setNewReason] = useState('');
  const [adding,    setAdding]    = useState(false);
  const [showAdd,   setShowAdd]   = useState(false);

  const alertsArr    = Array.isArray(theftAlerts?.alerts || theftAlerts) ? (theftAlerts?.alerts || theftAlerts) : [];
  const watchlistArr = Array.isArray(watchlist) ? watchlist : [];

  const handleAdd = async () => {
    if (!newPlate.trim()) return;
    setAdding(true);
    try {
      await onAdd(newPlate.trim().toUpperCase(), newReason.trim());
      setNewPlate(''); setNewReason(''); setShowAdd(false);
    } catch (err) {
      console.error('Add watchlist failed', err);
    } finally {
      setAdding(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Active Theft Alerts */}
      {alertsArr.length > 0 ? (
        <div className="flex flex-col gap-2">
          <span className="dvas-label" style={{ fontSize: 10 }}>ACTIVE THEFT ALERTS</span>
          {alertsArr.map((a, i) => (
            <div key={a.id || i} className="flex items-center gap-3 px-3 py-2.5 rounded-lg"
              style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)' }}>
              <AlertTriangle size={14} className="text-red-400 shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="mono font-black tracking-widest text-red-300 text-sm">{a.plate_text}</div>
                <div className="mono text-[10px]" style={{ color: 'var(--dvas-text-muted)' }}>
                  {a.vehicle_type} · {a.colour} · {a.drone_id} · {fmtAge(a.detected_at || a.ts)}
                </div>
              </div>
              <span className="mono text-[10px] px-2 py-0.5 rounded font-bold" style={{ background: 'rgba(239,68,68,0.15)', color: '#f87171' }}>ALERT</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="flex items-center gap-2 px-3 py-3 rounded-lg"
          style={{ background: 'rgba(16,185,129,0.08)', border: '1px solid rgba(16,185,129,0.2)' }}>
          <CheckCircle size={14} className="text-emerald-400" />
          <span className="text-xs text-emerald-400 font-medium">No watchlist vehicles detected in current session</span>
        </div>
      )}

      {/* Watchlist Management */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <span className="dvas-label" style={{ fontSize: 10 }}>WATCHLIST ({watchlistArr.length} plates)</span>
          <button
            onClick={() => setShowAdd(v => !v)}
            className="mono text-[10px] px-2 py-1 rounded flex items-center gap-1 transition-all"
            style={{ background: 'rgba(14,165,233,0.12)', color: '#38bdf8', border: '1px solid rgba(14,165,233,0.25)' }}>
            <Plus size={10} /> Add Plate
          </button>
        </div>

        {showAdd && (
          <div className="flex gap-2 mb-3 flex-wrap">
            <input
              value={newPlate}
              onChange={e => setNewPlate(e.target.value.toUpperCase())}
              placeholder="Plate number"
              className="mono text-xs px-2 py-1.5 rounded flex-1"
              style={{ background: 'var(--dvas-elev)', border: '1px solid var(--dvas-border)', color: 'var(--dvas-text)', minWidth: 120 }}
            />
            <input
              value={newReason}
              onChange={e => setNewReason(e.target.value)}
              placeholder="Reason (optional)"
              className="mono text-xs px-2 py-1.5 rounded flex-1"
              style={{ background: 'var(--dvas-elev)', border: '1px solid var(--dvas-border)', color: 'var(--dvas-text)', minWidth: 120 }}
            />
            <button onClick={handleAdd} disabled={adding || !newPlate.trim()}
              className="mono text-xs px-3 py-1.5 rounded font-semibold transition-all"
              style={{ background: 'rgba(14,165,233,0.15)', color: '#38bdf8', border: '1px solid rgba(14,165,233,0.3)', opacity: adding || !newPlate.trim() ? 0.5 : 1 }}>
              {adding ? 'Adding…' : 'Add'}
            </button>
          </div>
        )}

        {watchlistArr.length > 0 && (
          <div className="flex flex-col gap-1.5 max-h-52 overflow-y-auto">
            {watchlistArr.map((w, i) => (
              <div key={w.plate || i} className="flex items-center gap-3 px-3 py-2 rounded-lg"
                style={{ background: 'var(--dvas-surface-2)', border: '1px solid var(--dvas-border)' }}>
                <span className="mono font-black tracking-widest text-sm" style={{ color: '#2dd4bf' }}>{w.plate}</span>
                <span className="flex-1 mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>{w.reason || '—'}</span>
                <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>{fmtAge(w.added_at || w.ts)}</span>
                <button onClick={() => onRemove(w.plate)}
                  className="p-1 rounded transition-all hover:bg-red-500/10"
                  title="Remove from watchlist">
                  <Trash2 size={11} className="text-red-400" />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   MAIN EXPORT
   ────────────────────────────────────────────────────────────── */
export default function TrafficAnalytics({ drones }) {
  const [activeDrone, setActiveDrone] = useState('DRN-001');

  const { data: tracksData,  loading: tracksLoading }  = useVehicleTracks('', 5000);
  const { data: colourData }                            = useVehicleByColour(30000);
  const { data: statsData }                             = useVehicleStats(30000);
  const { data: platesData,  loading: platesLoading }  = usePlatesRecent('', 5000);
  const { data: theftData }                             = useTheftAlerts(10000);
  const { data: watchlistData, addPlate, removePlate } = useWatchlist(30000);

  // SSE for real-time vehicle/ANPR events
  const { connected: sseConnected } = useAnalyticsSSE(useCallback((evt) => {
    // Optionally handle specific events if needed
  }, []));

  useEffect(() => {
    if (drones?.length > 0) {
      const td = drones.find(d => TRAFFIC_DRONES.includes(d.id));
      setActiveDrone(td ? td.id : drones[0].id);
    }
  }, [drones]);

  const vehicles   = Array.isArray(tracksData) ? tracksData : (Array.isArray(tracksData?.tracks) ? tracksData.tracks : []);
  const plates     = Array.isArray(platesData) ? platesData : (Array.isArray(platesData?.plates) ? platesData.plates : []);
  const colourArr  = Array.isArray(colourData) ? colourData : (Array.isArray(colourData?.colours) ? colourData.colours : []);
  const watchlist  = Array.isArray(watchlistData) ? watchlistData : (Array.isArray(watchlistData?.plates) ? watchlistData.plates : []);

  return (
    <div className="flex flex-col gap-5" style={{ fontFamily: "'Plus Jakarta Sans', sans-serif" }}>

      {/* LIVE STREAM */}
      <LiveStreamSection
        activeDrone={activeDrone}
        setActiveDrone={setActiveDrone}
        drones={drones}
        vehicleCount={vehicles.length}
      />

      {/* KPI STRIP */}
      <TrafficKPIStrip
        vehicles={vehicles}
        plates={plates}
        theftAlerts={theftData}
        statsData={statsData}
      />

      {/* TRACKED VEHICLES WINDOW */}
      <Panel title="Tracked Vehicles — Live Ledger" icon={Car} iconClass="text-violet-400"
        right={
          <span className={`mono text-[10px] flex items-center gap-1 ${sseConnected ? 'text-violet-400' : 'text-slate-500'}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${sseConnected ? 'bg-violet-400 dvas-live-dot' : 'bg-slate-500'}`} />
            {vehicles.length} active tracks
          </span>
        }>
        <TrackedVehiclesWindow vehicles={vehicles} loading={tracksLoading} />
      </Panel>

      {/* CLASSIFICATION DONUT + FLOW CHART */}
      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-12 lg:col-span-4">
          <Panel title="Vehicle Colour Distribution" icon={Car} iconClass="text-teal-400">
            <VehicleDonut colourData={colourArr} />
          </Panel>
        </div>
        <div className="col-span-12 lg:col-span-8">
          <Panel title="Vehicle Flow — 24h Time Series" icon={TrendingUp} iconClass="text-sky-400"
            right={
              <span className="mono text-[10px] text-sky-400 flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-sky-400 dvas-live-dot" />
                Live · 30s refresh
              </span>
            }>
            <VehicleFlowChart statsData={statsData} />
          </Panel>
        </div>
      </div>

      {/* ANPR PLATE WINDOW */}
      <Panel
        title="ANPR — Number Plate Detection Window"
        icon={Hash}
        iconClass="text-amber-400"
        badge={plates.length > 0 ? `${plates.length} detections` : null}
        right={
          <div className="flex items-center gap-1.5">
            <span className={`w-1.5 h-1.5 rounded-full ${sseConnected ? 'bg-amber-400 dvas-live-dot' : 'bg-slate-500'}`} />
            <span className={`mono text-[10px] ${sseConnected ? 'text-amber-400' : 'text-slate-500'}`}>
              {sseConnected ? 'Live' : 'Polling'}
            </span>
          </div>
        }>
        <ANPRPlateWindow platesData={plates} activeDrone={activeDrone} loading={platesLoading} />
      </Panel>

      {/* WATCHLIST & THEFT ALERTS */}
      <Panel
        title="Watchlist & Theft Alerts"
        icon={ShieldAlert}
        iconClass="text-red-400"
        badge={(Array.isArray(theftData?.alerts || theftData) ? (theftData?.alerts || theftData) : []).length > 0
          ? `${(Array.isArray(theftData?.alerts || theftData) ? (theftData?.alerts || theftData) : []).length} ALERTS` : null}>
        <WatchlistPanel
          theftAlerts={theftData}
          watchlist={watchlist}
          onAdd={addPlate}
          onRemove={removePlate}
        />
      </Panel>

    </div>
  );
}
