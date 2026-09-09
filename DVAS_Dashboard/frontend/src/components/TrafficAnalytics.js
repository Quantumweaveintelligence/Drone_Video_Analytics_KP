/**
 * TrafficAnalytics.js — Traffic & ANPR
 * Live annotated frames + all widgets connected to real analytics data.
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, PieChart, Pie, Cell,
} from 'recharts';
import {
  Car, AlertTriangle, Activity, MapPin, 
  BarChart2, Zap, Clock, Maximize2, Radio, Target, History, RefreshCw, X,
  Hash, Gauge, ShieldAlert, CheckCircle2, Camera, Search, Download, Archive, ChevronLeft, ChevronRight, Folder,
  Plus, Trash2
} from 'lucide-react';

import { useJpegPoller } from '../hooks/useJpegPoller';
import { useActiveStreams } from '../hooks/useActiveStreams';
import { api } from '../lib/api';

/* ─── constants ─────────────────────────────────────────────────── */
const POLL_TRACKS    = 5000;
const POLL_COLOUR    = 30000;
const POLL_FLOW      = 30000;
const POLL_PLATES    = 5000;
const POLL_ALERTS    = 10000;
const POLL_WATCHLIST = 30000;
const FRAME_INTERVAL = 800;

const COLOUR_PALETTE = [
  '#818cf8','#14b8a6','#f59e0b','#f87171','#34d399','#fb923c',
  '#a78bfa','#38bdf8','#4ade80','#facc15','#e879f9','#f472b6',
];

const CHART_TOOLTIP = {
  contentStyle: {
    background: 'var(--dvas-surface)', border: '1px solid var(--dvas-border)',
    fontSize: 11, color: 'var(--dvas-text)', borderRadius: 8,
  },
};

function fmtAge(ts) {
  if (!ts) return '—';
  const s = Math.floor((Date.now() - new Date(ts)) / 1000);
  if (s < 60)   return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s/60)}m ago`;
  return `${Math.floor(s/3600)}h ago`;
}
function nvt(raw) {
  if (!raw) return 'Unknown';
  const l = raw.toLowerCase();
  if (l === 'car') return 'Car';
  if (l === 'truck') return 'Truck';
  if (l.includes('3-wheel') || l === '3w' || l.includes('rickshaw') || l.includes('tricycle')) return '3-Wheeler';
  if (l.includes('two-wheel') || l.includes('2-wheel') || l === 'motorcycle' || l === 'motor' || l === 'bicycle') return '2-Wheeler';
  if (l === 'bus') return 'Bus';
  if (l === 'van') return 'Van';
  return raw[0].toUpperCase() + raw.slice(1);
}

/* ─── data hooks ─────────────────────────────────────────────────── */
function usePoll(path, interval, transform) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const doFetch = useCallback(async () => {
    try {
      const res = await api.get(path);
      setData(transform ? transform(res.data) : res.data);
      setError(null);
    } catch (e) {
      setError(e?.message || 'Error');
    } finally {
      setLoading(false);
    }
  }, [path, transform]);

  useEffect(() => {
    let mounted = true;
    const fetchWrapper = async () => {
      try {
        const res = await api.get(path);
        if (mounted) {
          setData(transform ? transform(res.data) : res.data);
          setError(null);
        }
      } catch (e) {
        if (mounted) setError(e?.message || 'Error');
      } finally {
        if (mounted) setLoading(false);
      }
    };
    fetchWrapper();
    const id = setInterval(fetchWrapper, interval);
    return () => { mounted = false; clearInterval(id); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  return { data, loading, error, mutate: doFetch };
}

/* ─── Panel wrapper ──────────────────────────────────────────────── */
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
            style={{ background:'rgba(239,68,68,0.12)', color:'#f87171', border:'1px solid rgba(239,68,68,0.2)' }}>
            {badge}
          </span>
        )}
        {right}
      </div>
      <div className={`flex-1 min-h-0 ${noPad ? '' : 'p-3'}`}>{children}</div>
    </div>
  );
}



/* ─── Annotated Frame Viewer ─────────────────────────────────────── */
function AnnotatedFrameViewer({ droneId, mode = 'vehicle', streamRunning, isLive }) {
  const [fps, setFps] = useState(0);
  const fpsRef = useRef({ count: 0, ts: Date.now() });
  const prevAnnotatedRef = useRef(null);

  // Disable pollers when no drone is selected yet
  const hasDrone = !!droneId;

  // Build URLs — null-safe (poller ignores empty url)
  const rawUrl = hasDrone
    ? `/api/ingest-server/frame/${encodeURIComponent(droneId)}/latest?drone_id=${encodeURIComponent(droneId)}`
    : null;
  const annotatedUrl = hasDrone
    ? (mode === 'anpr'
        ? `/api/analytics/anpr/annotated?drone_id=${encodeURIComponent(droneId)}`
        : `/api/analytics/vehicle/annotated?drone_id=${encodeURIComponent(droneId)}`)
    : null;

  // Layer 1: raw ingest feed — only when a drone is known
  const { blobUrl: rawSrc, ok: rawOk } = useJpegPoller(rawUrl, 1500, hasDrone);

  // Layer 2: annotated overlay — polls when stream is running or drone is known
  const { blobUrl: annotatedSrc, ok: annotatedOk } = useJpegPoller(
    annotatedUrl, FRAME_INTERVAL, hasDrone && streamRunning
  );

  // FPS counter — ticks whenever a new annotated blob URL appears
  useEffect(() => {
    if (!annotatedSrc || annotatedSrc === prevAnnotatedRef.current) return;
    prevAnnotatedRef.current = annotatedSrc;
    fpsRef.current.count++;
    if (Date.now() - fpsRef.current.ts > 3000) {
      setFps(Math.round(fpsRef.current.count / ((Date.now() - fpsRef.current.ts) / 1000)));
      fpsRef.current = { count: 0, ts: Date.now() };
    }
  }, [annotatedSrc]);

  // What to display in the <img>
  const displaySrc  = annotatedOk ? annotatedSrc : rawSrc;
  const isAnnotated = annotatedOk && !!annotatedSrc;
  const hasAnyFeed  = !!displaySrc;

  return (
    <div className="relative w-full h-full bg-black flex items-center justify-center" style={{ minHeight: 340 }}>

      {/* ── Main image ────────────────────────────────────── */}
      {!hasDrone ? (
        <div className="w-full h-full flex flex-col items-center justify-center gap-3">
          <RefreshCw size={32} className="text-slate-600 opacity-40 animate-spin" />
          <p className="mono text-sm text-slate-500">Discovering active drone…</p>
          <p className="mono text-[10px] text-slate-600">Reading live telemetry from ingest server</p>
        </div>
      ) : hasAnyFeed ? (
        <img
          src={displaySrc}
          alt={isAnnotated ? 'Annotated detection feed' : 'Raw drone feed'}
          className="w-full h-full object-contain"
        />
      ) : (
        <div className="w-full h-full flex flex-col items-center justify-center gap-3 text-center px-4">
          <Activity size={36} className="text-slate-600 opacity-40" />
          <p className="mono text-sm text-slate-400 font-bold tracking-wider">FEED OFFLINE — NO ACTIVE STREAM</p>
          <p className="mono text-[11px] text-slate-500 max-w-sm">
            Drone <span className="text-teal-400 font-semibold">{droneId}</span> is not transmitting frames to the ingest server.
          </p>
        </div>
      )}



      {/* ── HUD: top-left labels ───────────────────────── */}
      <div className="absolute top-3 left-3 flex flex-col gap-1.5 z-10 pointer-events-none">
        {isAnnotated ? (
          <div className="mono text-[10px] bg-black/85 px-2 py-1 rounded text-violet-300">
            {mode === 'anpr' ? 'ANPR — Plate detection' : 'Vehicle tracking'} — annotated
          </div>
        ) : rawOk ? (
          <div className="mono text-[10px] bg-black/85 px-2 py-1 rounded text-slate-400">
            Raw drone feed
          </div>
        ) : null}
        <div className="mono text-[10px] bg-black/85 px-2 py-1 rounded text-teal-300">
          {droneId}
        </div>
      </div>

      {/* ── HUD: top-right status pill ─────────────────── */}
      {isAnnotated ? (
        <div className="absolute top-3 right-3 z-10 pointer-events-none flex items-center gap-1 mono text-[10px] bg-black/85 px-2 py-1 rounded text-emerald-400">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 dvas-live-dot" />
          ANNOTATED{fps > 0 ? ` · ${fps}fps` : ''}
        </div>
      ) : rawOk ? (
        <div className="absolute top-3 right-3 z-10 pointer-events-none flex items-center gap-1 mono text-[10px] bg-black/85 px-2 py-1 rounded text-slate-400">
          <span className="w-1.5 h-1.5 rounded-full bg-slate-400" />
          LIVE · RAW
        </div>
      ) : null}

      {/* ── HUD: bottom banner — detection status ──────── */}
      {streamRunning && !isAnnotated && rawOk && (
        <div className="absolute bottom-3 left-0 right-0 flex justify-center z-10 pointer-events-none">
          <div className="mono text-[10px] bg-black/85 px-3 py-1.5 rounded-full text-amber-400 flex items-center gap-2">
            <RefreshCw size={10} className="animate-spin" />
            {mode === 'anpr' ? 'ANPR' : 'Vehicle'} detection starting — awaiting first annotated frame…
          </div>
        </div>
      )}

      {!streamRunning && !isAnnotated && hasAnyFeed && (
        <div className="absolute bottom-3 left-0 right-0 flex justify-center z-10 pointer-events-none">
          <div className="mono text-[10px] bg-black/85 px-3 py-1.5 rounded-full text-amber-400 flex items-center gap-2">
            <RefreshCw size={10} className="animate-spin" />
            Waiting for detection — starting analytics stream…
          </div>
        </div>
      )}

      {/* ── HUD: bottom-left model label ───────────────── */}
      {isAnnotated && (
        <div className="absolute bottom-3 left-3 z-10 pointer-events-none mono text-[10px] bg-black/85 px-2 py-1 rounded text-slate-400">
          {mode === 'anpr' ? 'YOLOv8 + PaddleOCR' : 'YOLOv8 + ByteTrack'}
        </div>
      )}
    </div>
  );
}


/* ─── KPI Strip ───────────────────────────────────────────────────── */
function KPIStrip({ tracks, plates, alerts, stats }) {
  const tracksArr = tracks?.tracks || [];
  const platesArr = plates?.plates || [];
  const alertsArr = alerts?.alerts || [];
  const moving    = tracksArr.filter(v => (v.speed_kmh || 0) > 3).length;
  const stopped   = tracksArr.length - moving;
  const avgSpeed  = tracksArr.length > 0
    ? Math.round(tracksArr.reduce((s,v) => s + (v.speed_kmh || 0), 0) / tracksArr.length)
    : (stats?.avg_speed_kmh || 0);

  const kpis = [
    { label:'Vehicles Tracked',  value: tracksArr.length || stats?.total_tracked || 0, tone:'text-violet-400', icon:Car,          sub:'Active across all drones' },
    { label:'Moving / Stopped',  value:`${moving} / ${stopped}`,                        tone:'text-teal-400',  icon:Activity,     sub:'Current vehicle states' },
    { label:'Avg Speed (km/h)',  value: avgSpeed,                                        tone:'text-amber-400', icon:Gauge,        sub:'Across tracked vehicles' },
    { label:'Plates Detected',   value: platesArr.length || stats?.total_plates || 0,   tone:'text-sky-400',   icon:Hash,         sub:'ANPR readings from drones' },
    { label:'Watchlist Matches', value: alertsArr.length,                                tone: alertsArr.length > 0 ? 'text-red-400':'text-emerald-400', icon:AlertTriangle, sub: alertsArr.length > 0 ? 'ALERT — matches found':'No watchlist hits' },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
      {kpis.map(({ label, value, tone, icon: Icon, sub }) => (
        <div key={label} className="rounded-xl p-4 flex flex-col gap-1"
          style={{ background:'var(--dvas-surface)', border:'1px solid var(--dvas-border)' }}>
          <div className="flex items-center gap-2">
            <Icon size={13} className={`${tone} shrink-0`} />
            <span className="dvas-label text-[10px]">{label}</span>
          </div>
          <div className={`mono font-bold text-2xl mt-1 ${tone}`}>{value ?? '—'}</div>
          <div className="mono text-[10px]" style={{ color:'var(--dvas-text-dim)' }}>{sub}</div>
        </div>
      ))}
    </div>
  );
}

/* ─── Tracked Vehicles Ledger ────────────────────────────────────── */
function VehicleLedger({ tracks, loading }) {
  const arr = tracks?.tracks || [];
  const [sortKey, setSortKey] = useState('last_seen');
  const sorted = [...arr].sort((a, b) => {
    if (sortKey === 'speed') return (b.speed_kmh||0)-(a.speed_kmh||0);
    return new Date(b.last_seen||0) - new Date(a.last_seen||0);
  });
  const sc = { Moving:'#2dd4bf', Stopped:'#f87171', Slow:'#fbbf24' };

  if (loading && arr.length === 0) return (
    <div className="flex items-center justify-center py-10 gap-2">
      <RefreshCw size={14} className="animate-spin text-slate-500" />
      <span className="mono text-xs text-slate-500">Loading vehicle tracks…</span>
    </div>
  );
  if (!loading && arr.length === 0) return (
    <div className="flex flex-col items-center justify-center py-10 gap-2">
      <Car size={36} className="text-slate-600 opacity-40" />
      <span className="mono text-xs text-slate-500">No active vehicle tracks</span>
      <span className="mono text-[10px] text-slate-600">Vehicle analyzer will populate this when drones stream frames</span>
    </div>
  );

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        {['last_seen','speed'].map(k => (
          <button key={k} onClick={() => setSortKey(k)}
            className="mono text-[10px] px-2.5 py-1 rounded-lg transition-all"
            style={{
              background: sortKey===k ? 'rgba(139,92,246,0.12)':'var(--dvas-surface-2)',
              color: sortKey===k ? '#a78bfa':'var(--dvas-text-muted)',
              border: `1px solid ${sortKey===k ? 'rgba(139,92,246,0.3)':'var(--dvas-border)'}`,
            }}>
            {k === 'last_seen' ? 'Recent First' : 'Speed ↓'}
          </button>
        ))}
        <span className="ml-auto mono text-[10px]" style={{ color:'var(--dvas-text-dim)' }}>
          {arr.length} active tracks
        </span>
      </div>
      <div className="flex flex-col gap-1.5 max-h-[380px] overflow-y-auto pr-1">
        {sorted.map((v, i) => {
          const speed = v.speed_kmh ?? 0;
          const state = v.state || (speed > 3 ? 'Moving':'Stopped');
          const colour = sc[state] || '#94a3b8';
          return (
            <div key={v.tracker_id || v.id || i}
              className="flex items-center gap-3 px-3 py-2 rounded-lg"
              style={{ background:'var(--dvas-surface-2)', border:'1px solid var(--dvas-border)' }}>
              <div className="shrink-0 w-14 mono text-xs font-bold" style={{ color:'var(--dvas-text)' }}>#{v.tracker_id || i}</div>
              <div className="shrink-0 w-20">
                <span className="mono text-[10px] px-1.5 py-0.5 rounded font-semibold"
                  style={{ background:'rgba(139,92,246,0.12)', color:'#a78bfa', border:'1px solid rgba(139,92,246,0.2)' }}>
                  {nvt(v.vehicle_type)}
                </span>
              </div>
              <div className="shrink-0 w-14 text-center">
                <span className="mono text-sm font-bold" style={{ color: colour }}>{speed}</span>
                <span className="mono text-[9px]" style={{ color:'var(--dvas-text-dim)' }}> km/h</span>
              </div>
              <div className="shrink-0 w-16">
                <span className="mono text-[10px] px-1.5 py-0.5 rounded"
                  style={{ background:`${colour}18`, color: colour, border:`1px solid ${colour}33` }}>
                  {state}
                </span>
              </div>
              <div className="flex-1 min-w-0">
                <div className="mono text-[10px]" style={{ color:'var(--dvas-text-muted)' }}>
                  {v.colour || '—'}
                  {v.plate_text && <span className="text-teal-400 font-bold ml-2 tracking-widest">{v.plate_text}</span>}
                </div>
                <div className="mono text-[10px]" style={{ color:'var(--dvas-text-dim)' }}>{v.drone_id}</div>
              </div>
              <div className="shrink-0 mono text-[10px]" style={{ color:'var(--dvas-text-dim)' }}>
                <Clock size={9} className="inline mr-0.5" />{fmtAge(v.last_seen)}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ─── Colour Donut ────────────────────────────────────────────────── */
function ColourDonut({ data }) {
  const arr = data?.by_colour || [];
  if (arr.length === 0) return (
    <div className="flex flex-col items-center justify-center py-8 gap-2 opacity-40">
      <Car size={32} className="text-teal-400" />
      <span className="mono text-xs text-slate-400">No vehicle data yet</span>
    </div>
  );
  const slices = arr.map((item, i) => ({ name: item.colour, value: item.count, color: COLOUR_PALETTE[i % COLOUR_PALETTE.length] }));
  return (
    <div className="flex flex-col items-center gap-2">
      <ResponsiveContainer width="100%" height={190}>
        <PieChart>
          <Pie data={slices} dataKey="value" innerRadius={48} outerRadius={76} paddingAngle={3} stroke="none">
            {slices.map((s, i) => <Cell key={i} fill={s.color} />)}
          </Pie>
          <Tooltip {...CHART_TOOLTIP} />
        </PieChart>
      </ResponsiveContainer>
      <div className="flex gap-3 flex-wrap justify-center">
        {slices.map(s => (
          <div key={s.name} className="flex items-center gap-1.5">
            <span className="w-3 h-1.5 rounded-full" style={{ background: s.color }} />
            <span className="mono text-[10px]" style={{ color:'var(--dvas-text-muted)' }}>{s.name}</span>
            <span className="mono text-[10px] font-bold" style={{ color: s.color }}>{s.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ─── Flow Chart ──────────────────────────────────────────────────── */
function FlowChart({ data }) {
  const timeline = data?.hourly_timeline || [];
  const chartData = timeline.length > 0
    ? timeline.map(h => ({ hour: h.hour, cars: h.cars ?? 0, twoWheelers: h.two_wheelers ?? 0, heavy: h.heavy ?? 0 }))
    : Array.from({ length: 24 }, (_, i) => ({ hour: `${String(i).padStart(2,'0')}:00`, cars: 0, twoWheelers: 0, heavy: 0 }));

  return (
    <div className="flex flex-col gap-1">
      {timeline.length === 0 && (
        <div className="mono text-[10px] text-slate-500 text-center mb-1">Awaiting vehicle-analyzer data…</div>
      )}
      <ResponsiveContainer width="100%" height={210}>
        <AreaChart data={chartData} margin={{ top: 8, right: 8, left: -22, bottom: 0 }}>
          <defs>
            {[['gC','#14b8a6'],['g2','#818cf8'],['gH','#f59e0b']].map(([id, c]) => (
              <linearGradient key={id} id={id} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={c} stopOpacity={0.4} />
                <stop offset="100%" stopColor={c} stopOpacity={0} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid stroke="var(--dvas-border)" strokeDasharray="3 3" />
          <XAxis dataKey="hour" stroke="var(--dvas-text-muted)" fontSize={9} interval={3} />
          <YAxis stroke="var(--dvas-text-muted)" fontSize={9} />
          <Tooltip {...CHART_TOOLTIP} />
          <Area type="monotone" dataKey="cars"        stroke="#14b8a6" fill="url(#gC)" strokeWidth={1.5} name="Cars" />
          <Area type="monotone" dataKey="twoWheelers" stroke="#818cf8" fill="url(#g2)" strokeWidth={1.5} name="Two-Wheelers" />
          <Area type="monotone" dataKey="heavy"       stroke="#f59e0b" fill="url(#gH)" strokeWidth={1.5} name="Heavy" />
        </AreaChart>
      </ResponsiveContainer>
      <div className="flex gap-4 justify-center">
        {[['#14b8a6','Cars'],['#818cf8','Two-Wheelers'],['#f59e0b','Heavy']].map(([c,l]) => (
          <div key={l} className="flex items-center gap-1.5">
            <span className="w-3 h-1.5 rounded-full" style={{ background: c }} />
            <span className="mono text-[10px]" style={{ color:'var(--dvas-text-muted)' }}>{l}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ─── ANPR Window ─────────────────────────────────────────────────── */
function ANPRWindow({ plates, activeDrone, activeMissionId, anprStreamRunning }) {
  const [view, setView] = useState('grid');
  const [selectedId, setSelectedId] = useState(null);
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState('all');

  // Clear selection and search when mission changes — no leftover data across missions
  useEffect(() => {
    setSelectedId(null);
    setSearch('');
    setTypeFilter('all');
  }, [activeMissionId, activeDrone]);

  // Poll /plates/live filtered by mission_id (preferred) or drone_id
  const liveUrl = activeMissionId
    ? `/analytics/plates/live?mission_id=${encodeURIComponent(activeMissionId)}&limit=30`
    : activeDrone
      ? `/analytics/plates/live?drone_id=${activeDrone}&limit=30`
      : '/analytics/plates/live?limit=30';

  const { data: livePlates } = usePoll(liveUrl, 3000);

  // Merge DB plates (from /plates/recent) with live in-memory plates.
  // Scoped strictly by mission_id and drone_id to prevent cross-mission bleed.
  const rawDbArr   = plates?.plates  || [];
  const rawLiveArr = livePlates?.plates || [];

  // Filter out any plates from other missions if activeMissionId is set
  const dbArr = activeMissionId
    ? rawDbArr.filter(p => !p.mission_id_str || p.mission_id_str === activeMissionId)
    : rawDbArr;

  const liveArr = activeMissionId
    ? rawLiveArr.filter(p => !p.mission_id || p.mission_id === activeMissionId)
    : rawLiveArr;

  const mergedMap = new Map();
  // Insert live plates first (they hold fresh synchronized b64 crops)
  for (const p of liveArr) {
    const mId = p.mission_id || activeMissionId || 'live';
    const key = `${p.drone_id || activeDrone || ''}:${mId}:${p.tracker_id != null ? p.tracker_id : (p.plate_text || '')}`;
    mergedMap.set(key, { ...p });
  }

  // Merge DB records into existing live items or add them
  for (const p of dbArr) {
    const mId = p.mission_id_str || p.mission_id || activeMissionId || 'db';
    const key = `${p.drone_id || activeDrone || ''}:${mId}:${p.tracker_id != null ? p.tracker_id : (p.plate_text || '')}`;
    const existing = mergedMap.get(key);
    if (!existing) {
      mergedMap.set(key, { ...p });
    } else {
      // Enrich with presigned URLs or metadata only if missing in live item
      const merged = { ...existing };
      if (p.plate_image_url && !merged.plate_crop_b64) merged.plate_image_url = p.plate_image_url;
      if (p.vehicle_image_url && !merged.vehicle_image_b64) merged.vehicle_image_url = p.vehicle_image_url;
      if (p.plate_image_key && !merged.plate_crop_b64) merged.plate_image_key = p.plate_image_key;
      if (p.vehicle_image_key && !merged.vehicle_image_b64) merged.vehicle_image_key = p.vehicle_image_key;
      if (p.plate_text && !merged.plate_text) merged.plate_text = p.plate_text;
      if (p.ocr_confidence && !merged.ocr_confidence) merged.ocr_confidence = p.ocr_confidence;
      if (p.vehicle_type && (!merged.vehicle_type || merged.vehicle_type === 'vehicle')) merged.vehicle_type = p.vehicle_type;
      if (p.id) merged.id = p.id;
      mergedMap.set(key, merged);
    }
  }
  let arr = Array.from(mergedMap.values())
    .sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
    .slice(0, 50);


  // Apply filters
  if (search) {
    const q = search.toUpperCase();
    arr = arr.filter(p => (p.plate_text || '').toUpperCase().includes(q));
  }
  if (typeFilter !== 'all') {
    arr = arr.filter(p => nvt(p.vehicle_type).toLowerCase() === typeFilter);
  }

  // Build image URL: prefer proxy (key-based), fallback to b64, then to presigned URL
  const proxyUrl = (bucket, key) => {
    if (!key) return null;
    return `/api/minio/image?bucket=${encodeURIComponent(bucket)}&key=${encodeURIComponent(key)}`;
  };
  const imgSrc = (bucket, key, url, b64) => {
    if (key) return proxyUrl(bucket, key);
    if (b64) return `data:image/jpeg;base64,${b64}`;
    if (url) return url;  // last resort: direct presigned URL
    return null;
  };
  const vehicleSrc = (p) => imgSrc('dvas-vehicles', p.vehicle_image_key, p.vehicle_image_url, p.vehicle_image_b64);
  const plateSrc   = (p) => imgSrc('dvas-plates',   p.plate_image_key,   p.plate_image_url,   p.plate_crop_b64);

  const handleContainerClick = (e) => {
    if (e.target === e.currentTarget) setSelectedId(null);
  };

  return (
    <div className="flex flex-col gap-3" onClick={handleContainerClick}>
      {/* ── Top Toolbar ── */}
      <div className="flex flex-wrap items-center gap-2">
        {/* Search */}
        <div className="flex items-center gap-1.5 px-2 py-1 rounded border border-slate-700/70 bg-slate-800/50" style={{ minWidth: 140 }}>
          <Search size={12} className="text-slate-500 shrink-0" />
          <input
            type="text"
            placeholder="Search"
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="bg-transparent outline-none text-slate-300 placeholder-slate-600 mono w-full"
            style={{ fontSize: 11 }}
          />
        </div>

        {/* Type Filter */}
        <select
          value={typeFilter}
          onChange={e => setTypeFilter(e.target.value)}
          className="px-2 py-1 rounded border border-slate-700/70 bg-slate-800/50 text-slate-300 mono outline-none cursor-pointer"
          style={{ fontSize: 11 }}
        >
          <option value="all">All Vehicles</option>
          <option value="car">Car</option>
          <option value="truck">Truck</option>
          <option value="bus">Bus</option>
          <option value="motorcycle">Motorcycle</option>
        </select>

        {/* Spacer */}
        <div className="flex-grow" />

        {/* Plate count */}
        <span className="mono text-[10px] text-teal-400 font-bold">{arr.length} plates detected</span>

        {/* Live indicator */}
        {liveArr.length > 0 && (
          <span className="mono text-[9px] px-1.5 py-0.5 rounded-full flex items-center gap-1"
            style={{ background:'rgba(52,211,153,0.1)', color:'#34d399', border:'1px solid rgba(52,211,153,0.25)' }}>
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 dvas-live-dot" />
            Live
          </span>
        )}

        {/* View toggles */}
        <span className="mono text-[10px] text-slate-500">Show:</span>
        {[['grid','Plate Gallery'],['annotated','Live ANPR Feed']].map(([k,l]) => (
          <button key={k} onClick={() => setView(k)}
            className="mono text-[10px] px-2 py-1 rounded transition-all"
            style={{
              background: view===k ? 'rgba(20,184,166,0.15)':'var(--dvas-surface-2)',
              color: view===k ? '#14b8a6':'var(--dvas-text-muted)',
              border: `1px solid ${view===k ? 'rgba(20,184,166,0.35)':'var(--dvas-border)'}`,
            }}>{l}</button>
        ))}
      </div>

      {/* ── Content ── */}
      {view === 'annotated' ? (
        <div className="rounded-lg overflow-hidden" style={{ height: 320 }}>
          <AnnotatedFrameViewer droneId={activeDrone} mode="anpr" streamRunning={anprStreamRunning} />
        </div>
      ) : arr.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-8 gap-2 opacity-50">
          <Hash size={32} className="text-teal-400" />
          <span className="mono text-xs text-slate-400">No plate detections yet</span>
          <span className="mono text-[10px] text-slate-500">ANPR service will populate this when plates are detected</span>
        </div>
      ) : (
        <div
          className="grid gap-3 overflow-y-auto pr-1 pb-2"
          style={{
            gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
            maxHeight: 900,
            alignItems: 'start',
          }}
          onClick={handleContainerClick}
        >
          {arr.map((p, i) => {
            const id = p.id || `${p.plate_text}-${i}`;
            const isSelected = selectedId === id;
            const isAnySelected = selectedId !== null;
            const vSrc = vehicleSrc(p);
            const pSrc = plateSrc(p);
            const vType = nvt(p.vehicle_type).toUpperCase();
            const elapsed = p.created_at ? fmtAge(p.created_at) : '';
            // vehicle is detected first; plate crop + OCR text land a moment
            // later — flag that in-progress window so the UI can say so
            // instead of just showing blank placeholders.
            const isPending = !pSrc && !p.plate_text;
            
            return (
              <div key={id}
                onClick={() => setSelectedId(isSelected ? null : id)}
                className="flex flex-col h-auto rounded-lg overflow-hidden transition-all duration-300 cursor-pointer"
                style={{
                  background: 'rgba(15, 23, 42, 0.9)',
                  border: `1px solid ${
                    p.is_watchlisted
                      ? (isSelected ? '#ef4444' : 'rgba(239,68,68,0.5)')
                      : (isSelected ? '#06b6d4' : 'rgba(51,65,85,0.8)')
                  }`,
                  transform: isSelected ? 'scale(1.03)' : 'scale(1)',
                  zIndex: isSelected ? 10 : 1,
                  filter: (isAnySelected && !isSelected) ? 'blur(3px) opacity(0.35) grayscale(40%)' : 'none',
                  boxShadow: isSelected
                    ? (p.is_watchlisted ? '0 8px 20px -4px rgba(239,68,68,0.35)' : '0 8px 20px -4px rgba(6,182,212,0.25)')
                    : '0 1px 3px rgba(0,0,0,0.3)',
                }}>
                
                {/* ── Card Header ── */}
                <div className="flex items-center justify-between px-2.5 py-1.5" style={{ background: 'rgba(30,41,59,0.7)', borderBottom: '1px solid rgba(51,65,85,0.6)' }}>
                  <span className="font-mono text-slate-400 font-semibold" style={{ fontSize: 10 }}>
                    ID: #{p.tracker_id?.toString().padStart(2, '0') || String(i+1).padStart(2, '0')}
                  </span>
                  <span className="font-mono font-bold px-1.5 py-0.5 rounded text-[9px]"
                    style={{ background: 'rgba(20,184,166,0.15)', color: '#14b8a6', border: '1px solid rgba(20,184,166,0.3)' }}>
                    {vType || 'VEHICLE'}
                  </span>
                  <span className="text-slate-500 font-mono flex items-center gap-1" style={{ fontSize: 9 }}>
                    <Clock size={9} className="opacity-60" />
                    {elapsed || '--'}
                  </span>
                </div>

                {/* ── Pending-OCR banner ── */}
                {isPending && (
                  <div className="flex items-center gap-1.5 px-2.5 py-1 font-mono"
                    style={{ fontSize: 9, background: 'rgba(245,158,11,0.1)', color: '#f59e0b', borderBottom: '1px solid rgba(245,158,11,0.25)' }}>
                    <RefreshCw size={9} className="animate-spin" />
                    Reading plate…
                  </div>
                )}

                {/* ── Vehicle Crop ── */}
                {/* padding-bottom % box (classic aspect-ratio technique) instead
                    of the CSS `aspect-ratio` property — sizes the box through
                    normal padding/box-model math so CSS Grid's row-height
                    calculation always sees the real height up front, instead
                    of collapsing the row to whatever fit before the box
                    resolved its aspect ratio. object-contain keeps the whole
                    vehicle in frame; a blurred, oversized copy of the same
                    image fills the letterbox space so there's no dead black bars. */}
                <div className="relative w-full bg-black overflow-hidden" style={{ paddingTop: '75%' /* 4:3 */ }}>
                  {vSrc ? (
                    <>
                      <img src={vSrc} alt="" aria-hidden="true"
                        className="absolute inset-0 w-full h-full object-cover block"
                        style={{ filter: 'blur(14px) brightness(0.5)', transform: 'scale(1.15)' }} />
                      <img src={vSrc} alt="vehicle"
                        className="absolute inset-0 w-full h-full object-contain block" />
                    </>
                  ) : (
                    <div className="absolute inset-0 flex items-center justify-center"><Car size={28} className="text-slate-700 opacity-40" /></div>
                  )}
                  <div className="absolute bottom-1 left-1 z-10">
                    <span className="font-mono font-bold uppercase px-1 py-0.5 rounded"
                      style={{ fontSize: 8, background: 'rgba(0,0,0,0.65)', color: '#94a3b8', letterSpacing: '0.08em' }}>
                      VEHICLE CROP
                    </span>
                  </div>
                </div>

                {/* ── Plate Crop ── */}
                <div className="w-full flex flex-col items-center" style={{ background: 'rgba(15,23,42,0.95)', borderTop: '1px solid rgba(51,65,85,0.5)', padding: '6px 8px' }}>
                  <div className="w-full mb-1">
                    <span className="font-mono font-bold uppercase text-slate-500" style={{ fontSize: 8, letterSpacing: '0.08em' }}>PLATE CROP</span>
                  </div>
                  {/* plates are wide, short crops — same padding-bottom % technique,
                      tuned to a 3:1 (wide) box, with object-contain so the full
                      plate is always visible, however wide/narrow the source is */}
                  <div className="relative w-full rounded border border-slate-700/70 bg-black overflow-hidden" style={{ paddingTop: '33.33%' /* 3:1 */ }}>
                    {pSrc ? (
                      <>
                        <img src={pSrc} alt="" aria-hidden="true"
                          className="absolute inset-0 w-full h-full object-cover block"
                          style={{ filter: 'blur(10px) brightness(0.5)', transform: 'scale(1.15)' }} />
                        <img src={pSrc} alt="plate"
                          className="absolute inset-0 w-full h-full object-contain block" />
                      </>
                    ) : (
                      <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 font-mono text-amber-500/80" style={{ fontSize: 8 }}>
                        <RefreshCw size={11} className="animate-spin opacity-70" />
                        <span>READING PLATE…</span>
                      </div>
                    )}
                  </div>
                </div>

                {/* ── Card Footer ── */}
                <div className="flex items-center justify-between px-2.5 py-2" style={{ background: 'rgba(30,41,59,0.5)', borderTop: '1px solid rgba(51,65,85,0.5)' }}>
                  <div className="flex flex-col">
                    {p.plate_text ? (
                      <span className={`font-mono font-black tracking-wider ${p.is_watchlisted ? 'text-red-400' : 'text-cyan-300'}`} style={{ fontSize: 11 }}>
                        {p.plate_text}
                      </span>
                    ) : (
                      <span className="font-mono font-bold text-amber-500/70" style={{ fontSize: 9 }}>Pending OCR</span>
                    )}
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="text-center">
                      <div className="font-mono text-slate-500 uppercase" style={{ fontSize: 7, letterSpacing: '0.05em' }}>Confidence</div>
                      <div className={`font-mono font-bold ${p.ocr_confidence > 0.8 ? 'text-emerald-400' : p.ocr_confidence > 0.5 ? 'text-amber-400' : 'text-slate-500'}`} style={{ fontSize: 10 }}>
                        {p.ocr_confidence ? `${(p.ocr_confidence * 100).toFixed(1)}%` : 'Pending'}
                      </div>
                    </div>
                    <div className="text-center">
                      <div className="font-mono text-slate-500 uppercase" style={{ fontSize: 7, letterSpacing: '0.05em' }}>Type</div>
                      <div className="font-mono font-bold text-slate-300" style={{ fontSize: 10 }}>
                        {vType || '--'}
                      </div>
                    </div>
                  </div>
                </div>

              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ─── Watchlist Panel ─────────────────────────────────────────────── */
function WatchlistPanel({ alerts, watchlist, onAdd, onRemove, adding }) {
  const [inputPlate, setInputPlate] = useState('');
  const [inputReason, setInputReason] = useState('');
  const [searchPlate, setSearchPlate] = useState('');
  const [searchResult, setSearchResult] = useState(null); // null | 'loading' | {} | 'notfound'
  const [searchError, setSearchError] = useState(null);
  const alertsArr    = alerts?.alerts || [];
  const watchlistArr = watchlist?.watchlist || [];

  const handleSearch = async () => {
    const q = searchPlate.trim().toUpperCase();
    if (!q) return;
    setSearchResult('loading');
    setSearchError(null);
    try {
      const res = await api.get(`/analytics/plates/${encodeURIComponent(q)}`);
      setSearchResult(res.data || 'notfound');
    } catch (e) {
      if (e?.response?.status === 404) {
        setSearchResult('notfound');
      } else {
        setSearchError('Search failed — ANPR service may be offline');
        setSearchResult(null);
      }
    }
  };

  return (
    <div className="flex flex-col gap-4">
      {/* ── Active Theft Alerts ── */}
      {alertsArr.length > 0 && (
        <div className="rounded-lg p-3 flex flex-col gap-2"
          style={{ background:'rgba(239,68,68,0.07)', border:'1px solid rgba(239,68,68,0.2)' }}>
          <div className="mono text-[10px] font-bold text-red-400 uppercase tracking-widest">
            !! Watchlist Hits — {alertsArr.length} alert{alertsArr.length > 1 ? 's':''}
          </div>
          {alertsArr.map((a, i) => (
            <div key={i} className="flex flex-col rounded overflow-hidden"
              style={{ background:'rgba(239,68,68,0.06)', border:'1px solid rgba(239,68,68,0.15)' }}>
              {/* Alert header row */}
              <div className="flex items-center gap-3 px-2 py-1.5">
                <AlertTriangle size={12} className="text-red-400 shrink-0" />
                <span className="mono text-sm font-black tracking-widest text-red-300">{a.plate_text}</span>
                <span className="mono text-[10px] text-slate-400">{a.vehicle_type || '—'}</span>
                <span className="ml-auto mono text-[10px]" style={{ color:'var(--dvas-text-dim)' }}>{fmtAge(a.detected_at)}</span>
              </div>
              {/* Vehicle + Plate image pair if available */}
              {(a.vehicle_image_key || a.vehicle_image_url || a.plate_image_key || a.plate_image_url) && (
                <div className="flex gap-1 px-2 pb-2">
                  {(a.vehicle_image_key || a.vehicle_image_url) && (
                    <div className="relative rounded overflow-hidden bg-black" style={{ flex:'2', paddingTop:'40%' }}>
                      <img src={a.vehicle_image_key ? `/api/minio/image?bucket=dvas-vehicles&key=${encodeURIComponent(a.vehicle_image_key)}` : a.vehicle_image_url} alt="vehicle" loading="lazy"
                        className="absolute inset-0 w-full h-full object-cover" />
                      <span className="absolute bottom-0.5 left-0.5 mono text-[7px] px-1 rounded"
                        style={{ background:'rgba(0,0,0,0.7)', color:'#94a3b8' }}>VEHICLE</span>
                    </div>
                  )}
                  {(a.plate_image_key || a.plate_image_url) && (
                    <div className="relative rounded overflow-hidden bg-black" style={{ flex:'1', paddingTop:'40%' }}>
                      <img src={a.plate_image_key ? `/api/minio/image?bucket=dvas-plates&key=${encodeURIComponent(a.plate_image_key)}` : a.plate_image_url} alt="plate" loading="lazy"
                        className="absolute inset-0 w-full h-full object-contain" />
                      <span className="absolute bottom-0.5 left-0.5 mono text-[7px] px-1 rounded"
                        style={{ background:'rgba(0,0,0,0.7)', color:'#94a3b8' }}>PLATE</span>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {alertsArr.length === 0 && (
        <div className="mono text-[11px] text-slate-500 flex items-center gap-2">
          <CheckCircle2 size={13} className="text-emerald-500" />
          No watchlist vehicles detected in current session
        </div>
      )}

      {/* ── Plate DB Search ── */}
      <div className="flex flex-col gap-2">
        <div className="mono text-[10px] text-slate-400 font-semibold uppercase tracking-widest">Search Plate in DB</div>
        <div className="flex gap-2">
          <input
            value={searchPlate}
            onChange={e => setSearchPlate(e.target.value.toUpperCase())}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
            placeholder="e.g. KL01AB1234"
            className="flex-1 mono text-xs px-2 py-1.5 rounded outline-none uppercase"
            style={{ background:'var(--dvas-surface-2)', border:'1px solid var(--dvas-border)', color:'var(--dvas-text)' }} />
          <button
            onClick={handleSearch}
            disabled={!searchPlate.trim()}
            className="px-3 py-1.5 rounded mono text-xs font-semibold transition-colors flex items-center gap-1 disabled:opacity-40"
            style={{ background:'rgba(20,184,166,0.12)', color:'#14b8a6', border:'1px solid rgba(20,184,166,0.3)' }}>
            <Search size={11} /> Search
          </button>
        </div>
        {/* Search results */}
        {searchResult === 'loading' && (
          <div className="flex items-center gap-2 mono text-[10px] text-slate-400">
            <RefreshCw size={10} className="animate-spin" /> Searching…
          </div>
        )}
        {searchResult === 'notfound' && (
          <div className="mono text-[10px] text-slate-500 px-1">
            No records found for <span className="text-slate-300 font-bold">{searchPlate}</span>
          </div>
        )}
        {searchError && (
          <div className="mono text-[10px] text-red-400 px-1">{searchError}</div>
        )}
        {searchResult && searchResult !== 'loading' && searchResult !== 'notfound' && !searchError && (() => {
          const r = searchResult;
          return (
            <div className="rounded-lg overflow-hidden"
              style={{ background:'var(--dvas-surface-2)', border:'1px solid rgba(20,184,166,0.25)' }}>
              {/* Images */}
              {(r.vehicle_image_key || r.vehicle_image_url || r.plate_image_key || r.plate_image_url) && (
                <div className="flex gap-1 p-1.5">
                  {(r.vehicle_image_key || r.vehicle_image_url) && (
                    <div className="relative rounded overflow-hidden bg-black" style={{ flex:'2', paddingTop:'40%' }}>
                      <img src={r.vehicle_image_key ? `/api/minio/image?bucket=dvas-vehicles&key=${encodeURIComponent(r.vehicle_image_key)}` : r.vehicle_image_url} alt="vehicle" className="absolute inset-0 w-full h-full object-cover" />
                      <span className="absolute bottom-0.5 left-0.5 mono text-[7px] px-1 rounded"
                        style={{ background:'rgba(0,0,0,0.7)', color:'#94a3b8' }}>VEHICLE</span>
                    </div>
                  )}
                  {(r.plate_image_key || r.plate_image_url) && (
                    <div className="relative rounded overflow-hidden bg-black" style={{ flex:'1', paddingTop:'40%' }}>
                      <img src={r.plate_image_key ? `/api/minio/image?bucket=dvas-plates&key=${encodeURIComponent(r.plate_image_key)}` : r.plate_image_url} alt="plate" className="absolute inset-0 w-full h-full object-contain" />
                      <span className="absolute bottom-0.5 left-0.5 mono text-[7px] px-1 rounded"
                        style={{ background:'rgba(0,0,0,0.7)', color:'#94a3b8' }}>PLATE</span>
                    </div>
                  )}
                </div>
              )}
              {/* Metadata */}
              <div className="flex items-center gap-3 px-3 py-2">
                <span className="mono text-sm font-black tracking-widest text-teal-300">{r.plate_text || searchPlate}</span>
                <span className="mono text-[10px] text-slate-400">{r.vehicle_type || '—'}</span>
                {r.ocr_confidence != null && (
                  <span className={`mono text-[10px] font-bold ${r.ocr_confidence > 0.8 ? 'text-emerald-400' : 'text-amber-400'}`}>
                    {(r.ocr_confidence * 100).toFixed(1)}%
                  </span>
                )}
                <span className="ml-auto mono text-[9px] text-slate-500">{r.last_seen ? fmtAge(r.last_seen) : (r.created_at ? fmtAge(r.created_at) : '')}</span>
              </div>
              {r.mission_id_str && (
                <div className="px-3 pb-2 mono text-[9px] text-violet-400">Mission: {r.mission_id_str}</div>
              )}
            </div>
          );
        })()}
      </div>

      {/* ── Add to Watchlist ── */}
      <div className="flex flex-col gap-2">
        <div className="mono text-[10px] text-slate-400 font-semibold uppercase tracking-widest">Add Plate to Watchlist</div>
        <div className="flex gap-2">
          <input value={inputPlate} onChange={e => setInputPlate(e.target.value.toUpperCase())}
            placeholder="PLATE TEXT"
            className="flex-1 mono text-xs px-2 py-1.5 rounded outline-none"
            style={{ background:'var(--dvas-surface-2)', border:'1px solid var(--dvas-border)', color:'var(--dvas-text)' }} />
          <input value={inputReason} onChange={e => setInputReason(e.target.value)}
            placeholder="Reason (optional)"
            className="flex-1 mono text-xs px-2 py-1.5 rounded outline-none"
            style={{ background:'var(--dvas-surface-2)', border:'1px solid var(--dvas-border)', color:'var(--dvas-text)' }} />
          <button
            onClick={() => { if (inputPlate.trim()) { onAdd(inputPlate.trim(), inputReason.trim()); setInputPlate(''); setInputReason(''); } }}
            disabled={adding || !inputPlate.trim()}
            className="px-3 py-1.5 rounded bg-teal-600 hover:bg-teal-500 text-white text-xs font-semibold disabled:opacity-50 transition-colors flex items-center gap-1">
            <Plus size={12} /> Add
          </button>
        </div>
      </div>

      {/* ── Current Watchlist ── */}
      <div>
        <div className="mono text-[10px] text-slate-400 font-semibold uppercase tracking-widest mb-2">
          WATCHLIST ({watchlistArr.length} plates)
        </div>
        {watchlistArr.length === 0
          ? <div className="mono text-[11px] text-slate-600 py-2">No plates on watchlist</div>
          : (
            <div className="flex flex-col gap-1.5 max-h-48 overflow-y-auto">
              {watchlistArr.map((w, i) => (
                <div key={i} className="flex items-center gap-3 px-3 py-2 rounded-lg"
                  style={{ background:'var(--dvas-surface-2)', border:'1px solid var(--dvas-border)' }}>
                  <span className="mono text-sm font-black tracking-widest" style={{ color:'var(--dvas-text)' }}>
                    {w.plate_text || w.plate}
                  </span>
                  <span className="mono text-[10px] flex-1" style={{ color:'var(--dvas-text-muted)' }}>{w.reason || '—'}</span>
                  <button onClick={() => onRemove(w.plate_text || w.plate)} className="text-red-400 hover:text-red-300 transition-colors">
                    <Trash2 size={13} />
                  </button>
                </div>
              ))}
            </div>
          )
        }
      </div>
    </div>
  );
}



/* ─── Main Component ──────────────────────────────────────────────── */
/* ─── MissionHistory ──────────────────────────────────────────────────────── */
function MissionHistory() {
  const { data: missions, mutate: refreshMissions } = usePoll('/missions', 60000);
  const [selectedMission, setSelectedMission] = useState(null);
  const [missionSearch, setMissionSearch] = useState('');
  const [showMissionDropdown, setShowMissionDropdown] = useState(false);
  const dropdownRef = useRef(null);

  // New filters
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo]   = useState('');
  const [operatorFilter, setOperatorFilter] = useState('');

  const [plateSearch, setPlateSearch] = useState('');
  const [debouncedPlateSearch, setDebouncedPlateSearch] = useState('');
  const [vehicleType, setVehicleType] = useState('all');
  const [sortBy, setSortBy] = useState('newest');
  const [offset, setOffset] = useState(0);
  const limit = 24;

  const [missionData, setMissionData] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedPlateSearch(plateSearch);
      setOffset(0);
    }, 500);
    return () => clearTimeout(t);
  }, [plateSearch]);

  useEffect(() => {
    if (!selectedMission) return;
    let mounted = true;
    const fetchVehicles = async () => {
      setLoading(true);
      try {
        const query = new URLSearchParams({
          plate_search: debouncedPlateSearch,
          vehicle_type: vehicleType,
          sort: sortBy,
          limit,
          offset
        }).toString();
        const res = await api.get(`/analytics/mission/${selectedMission.mission_id}/vehicles?${query}`);
        if (mounted) setMissionData(res.data);
      } catch (err) {
        console.error(err);
      } finally {
        if (mounted) setLoading(false);
      }
    };
    fetchVehicles();
    return () => { mounted = false; };
  }, [selectedMission, debouncedPlateSearch, vehicleType, sortBy, offset]);

  // Click outside to close dropdown
  useEffect(() => {
    const handleClick = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
        setShowMissionDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  const filteredMissions = (missions || []).filter(m => {
    const q = missionSearch.toLowerCase();
    if (q && !(m.name?.toLowerCase().includes(q) || m.mission_id?.toLowerCase().includes(q))) return false;
    // Date range filter on started_at
    if (dateFrom) {
      const mStart = m.started_at ? new Date(m.started_at) : null;
      if (!mStart || mStart < new Date(dateFrom)) return false;
    }
    if (dateTo) {
      const mStart = m.started_at ? new Date(m.started_at) : null;
      // add 1 day so "to" date is inclusive
      const toDate = new Date(dateTo);
      toDate.setDate(toDate.getDate() + 1);
      if (!mStart || mStart > toDate) return false;
    }
    // Operator name filter
    if (operatorFilter) {
      const op = (m.operator_name || '').toLowerCase();
      if (!op.includes(operatorFilter.toLowerCase())) return false;
    }
    return true;
  });

  const handleDownloadCsv = () => {
    if (!missionData || !missionData.vehicles || missionData.vehicles.length === 0) return;
    const rows = [
      ['Timestamp', 'Tracker ID', 'Vehicle Type', 'Colour', 'Speed KPH', 'Plate Text', 'OCR Confidence']
    ];
    missionData.vehicles.forEach(v => {
      rows.push([
        v.created_at || '',
        v.tracker_id || '',
        v.vehicle_type || '',
        v.colour || '',
        v.speed_kph || '',
        v.plate_text || '',
        v.ocr_confidence || ''
      ]);
    });
    const csvContent = rows.map(r => r.map(c => `"${c}"`).join(',')).join('\n');
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `mission_${selectedMission.mission_id}_vehicles.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Filters Toolbar */}
      <div className="flex flex-wrap items-center gap-3">
        <button
          onClick={() => refreshMissions()}
          className="px-2 py-1.5 rounded mono text-xs font-semibold flex items-center gap-1 transition-colors hover:bg-violet-900/40"
          style={{ background: 'rgba(139, 92, 246, 0.15)', color: '#a78bfa', border: '1px solid rgba(139, 92, 246, 0.3)' }}
          title="Refresh Mission List"
        >
          <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
          Refresh
        </button>

        {/* Mission Dropdown */}
        <div className="relative" ref={dropdownRef}>
          <input
            type="text"
            className="mono text-xs px-2 py-1.5 rounded outline-none w-64"
            style={{ background: 'var(--dvas-surface-2)', border: '1px solid var(--dvas-border)', color: 'var(--dvas-text)' }}
            placeholder="🔍 Search / Select Mission ▼"
            value={showMissionDropdown ? missionSearch : (selectedMission ? `${selectedMission.mission_id} - ${selectedMission.name}` : '')}
            onChange={(e) => setMissionSearch(e.target.value)}
            onFocus={() => { setShowMissionDropdown(true); setMissionSearch(''); }}
          />
          {showMissionDropdown && (
            <div className="absolute top-full mt-1 w-80 max-h-60 overflow-y-auto rounded shadow-lg z-50"
              style={{ background: 'var(--dvas-surface)', border: '1px solid var(--dvas-border)' }}>
              {filteredMissions.length === 0 ? (
                <div className="p-3 mono text-xs text-slate-500">No missions found</div>
              ) : (
                filteredMissions.map((m) => (
                  <div
                    key={m.mission_id}
                    className="px-3 py-2 cursor-pointer hover:bg-violet-900/30 transition-colors border-b border-slate-700/50"
                    onClick={() => {
                      setSelectedMission(m);
                      setShowMissionDropdown(false);
                      setOffset(0);
                    }}
                  >
                    <div className="mono text-[11px] font-bold text-violet-300">{m.mission_id}</div>
                    <div className="mono text-[10px] text-slate-400">
                      {m.name} · {m.drone_id}
                      {m.operator_name && <span className="text-slate-500"> · {m.operator_name}</span>}
                      {m.started_at && <span className="text-slate-600"> · {new Date(m.started_at).toLocaleDateString()}</span>}
                    </div>
                  </div>
                ))
              )}
            </div>
          )}
        </div>

        {/* Date From */}
        <div className="flex items-center gap-1">
          <span className="mono text-[10px] text-slate-500">From</span>
          <input
            type="date"
            value={dateFrom}
            onChange={e => { setDateFrom(e.target.value); setSelectedMission(null); setOffset(0); }}
            className="mono text-xs px-2 py-1.5 rounded outline-none"
            style={{ background: 'var(--dvas-surface-2)', border: '1px solid var(--dvas-border)', color: 'var(--dvas-text)' }}
          />
        </div>

        {/* Date To */}
        <div className="flex items-center gap-1">
          <span className="mono text-[10px] text-slate-500">To</span>
          <input
            type="date"
            value={dateTo}
            onChange={e => { setDateTo(e.target.value); setSelectedMission(null); setOffset(0); }}
            className="mono text-xs px-2 py-1.5 rounded outline-none"
            style={{ background: 'var(--dvas-surface-2)', border: '1px solid var(--dvas-border)', color: 'var(--dvas-text)' }}
          />
        </div>

        {/* Operator Filter */}
        <input
          type="text"
          value={operatorFilter}
          onChange={e => setOperatorFilter(e.target.value)}
          placeholder="Operator..."
          className="mono text-xs px-2 py-1.5 rounded outline-none w-32"
          style={{ background: 'var(--dvas-surface-2)', border: '1px solid var(--dvas-border)', color: 'var(--dvas-text)' }}
        />

        {/* Clear date/operator filters */}
        {(dateFrom || dateTo || operatorFilter) && (
          <button
            onClick={() => { setDateFrom(''); setDateTo(''); setOperatorFilter(''); }}
            className="mono text-[10px] px-2 py-1.5 rounded flex items-center gap-1 transition-colors"
            style={{ background: 'rgba(239,68,68,0.08)', color: '#f87171', border: '1px solid rgba(239,68,68,0.2)' }}
          >
            ✕ Clear
          </button>
        )}

        {/* Plate Search */}
        <div className="relative">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            type="text"
            className="mono text-xs pl-7 pr-2 py-1.5 rounded outline-none w-36 uppercase"
            style={{ background: 'var(--dvas-surface-2)', border: '1px solid var(--dvas-border)', color: 'var(--dvas-text)' }}
            placeholder="PLATE..."
            value={plateSearch}
            onChange={(e) => setPlateSearch(e.target.value)}
          />
        </div>

        {/* Vehicle Type */}
        <select
          className="mono text-xs px-2 py-1.5 rounded outline-none cursor-pointer"
          style={{ background: 'var(--dvas-surface-2)', border: '1px solid var(--dvas-border)', color: 'var(--dvas-text)' }}
          value={vehicleType}
          onChange={(e) => { setVehicleType(e.target.value); setOffset(0); }}
        >
          <option value="all">Type: All</option>
          <option value="car">Car</option>
          <option value="truck">Truck</option>
          <option value="bus">Bus</option>
          <option value="motorcycle">Motorcycle</option>
        </select>

        {/* Sort */}
        <select
          className="mono text-xs px-2 py-1.5 rounded outline-none cursor-pointer"
          style={{ background: 'var(--dvas-surface-2)', border: '1px solid var(--dvas-border)', color: 'var(--dvas-text)' }}
          value={sortBy}
          onChange={(e) => { setSortBy(e.target.value); setOffset(0); }}
        >
          <option value="newest">Sort: Newest</option>
          <option value="oldest">Sort: Oldest</option>
          <option value="confidence">Sort: Confidence</option>
          <option value="plate_az">Sort: Plate A-Z</option>
        </select>

        {/* CSV */}
        <button
          onClick={handleDownloadCsv}
          disabled={!missionData?.vehicles?.length}
          className="ml-auto mono text-[10px] px-2.5 py-1.5 rounded flex items-center gap-1 transition-colors disabled:opacity-50"
          style={{ background: 'rgba(20,184,166,0.1)', color: '#14b8a6', border: '1px solid rgba(20,184,166,0.3)' }}
        >
          <Download size={12} /> CSV
        </button>
      </div>

      {/* Mission Summary */}
      {missionData && !missionData.error && (
        <div className="flex items-center justify-between px-4 py-3 rounded-lg"
          style={{ background: 'rgba(139,92,246,0.06)', border: '1px solid rgba(139,92,246,0.15)' }}>
          <div>
            <div className="mono text-sm font-bold text-violet-300">
              {missionData.mission_id} <span className="text-slate-500">·</span> {missionData.mission_name} <span className="text-slate-500">·</span> {missionData.drone_id}
            </div>
            <div className="mono text-[10px] text-slate-400 mt-1">
              Status: <span className="text-emerald-400">{missionData.status}</span> · 
              Duration: {missionData.duration || 'N/A'} · 
              Started: {missionData.time_range?.start ? new Date(missionData.time_range.start).toLocaleString() : 'N/A'}
            </div>
          </div>
          <div className="flex gap-4 text-center">
            <div>
              <div className="mono text-[20px] font-black text-slate-200">{missionData.total}</div>
              <div className="mono text-[9px] uppercase tracking-widest text-slate-500">Vehicles</div>
            </div>
            <div>
              <div className="mono text-[20px] font-black text-teal-400">{missionData.total_plates_read}</div>
              <div className="mono text-[9px] uppercase tracking-widest text-slate-500">Plates</div>
            </div>
          </div>
        </div>
      )}

      {/* Grid State */}
      {!selectedMission ? (
        <div className="flex flex-col items-center justify-center py-12 gap-3 opacity-50">
          <Archive size={48} className="text-violet-400" />
          <span className="mono text-sm text-slate-400">Select a mission to view vehicle detection history</span>
        </div>
      ) : loading ? (
        <div className="flex flex-col items-center justify-center py-12 gap-3 opacity-50">
          <RefreshCw size={32} className="text-teal-400 animate-spin" />
          <span className="mono text-xs text-slate-400">Loading mission data...</span>
        </div>
      ) : missionData?.error ? (
        <div className="flex flex-col items-center justify-center py-12 gap-3 opacity-50">
          <AlertTriangle size={32} className="text-red-400" />
          <span className="mono text-xs text-slate-400">Failed to load data: {missionData.error}</span>
        </div>
      ) : (!missionData || !missionData.vehicles || missionData.vehicles.length === 0) ? (
        <div className="flex flex-col items-center justify-center py-12 gap-3 opacity-50">
          <Car size={32} className="text-slate-500" />
          <span className="mono text-xs text-slate-400">No vehicle detections found for this mission</span>
        </div>
      ) : (
        <>
          <div
            className="grid gap-3"
            style={{
              gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
              alignItems: 'start',
            }}
          >
            {missionData.vehicles.map((p, i) => {
              const vSrc = p.vehicle_image_key
                ? `/api/minio/image?bucket=dvas-vehicles&key=${encodeURIComponent(p.vehicle_image_key)}`
                : p.vehicle_image_url || null;
              const pSrc = p.plate_image_key
                ? `/api/minio/image?bucket=dvas-plates&key=${encodeURIComponent(p.plate_image_key)}`
                : p.plate_image_url || null;
              const vType = (p.vehicle_type || 'VEHICLE').toUpperCase();
              
              return (
                <div key={p.id || i}
                  className="flex flex-col h-auto rounded-lg overflow-hidden transition-all duration-300"
                  style={{
                    background: 'rgba(15, 23, 42, 0.9)',
                    border: '1px solid rgba(51,65,85,0.8)',
                    boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
                  }}>
                  
                  {/* Header */}
                  <div className="flex items-center justify-between px-2.5 py-1.5" style={{ background: 'rgba(30,41,59,0.7)', borderBottom: '1px solid rgba(51,65,85,0.6)' }}>
                    <span className="font-mono text-slate-400 font-semibold" style={{ fontSize: 10 }}>
                      ID: #{p.tracker_id?.toString().padStart(2, '0') || String(i+1).padStart(2, '0')}
                    </span>
                    <span className="font-mono font-bold px-1.5 py-0.5 rounded text-[9px]"
                      style={{ background: 'rgba(20,184,166,0.15)', color: '#14b8a6', border: '1px solid rgba(20,184,166,0.3)' }}>
                      {vType}
                    </span>
                    <span className="text-slate-500 font-mono flex items-center gap-1" style={{ fontSize: 9 }}>
                      <Clock size={9} className="opacity-60" />
                      {new Date(p.created_at).toLocaleTimeString()}
                    </span>
                  </div>

                  {/* Vehicle Crop */}
                  <div className="relative w-full bg-black overflow-hidden" style={{ paddingTop: '75%' /* 4:3 */ }}>
                    {vSrc ? (
                      <>
                        <img src={vSrc} alt="" aria-hidden="true"
                          className="absolute inset-0 w-full h-full object-cover block"
                          style={{ filter: 'blur(14px) brightness(0.5)', transform: 'scale(1.15)' }} />
                        <img src={vSrc} alt="vehicle" loading="lazy"
                          className="absolute inset-0 w-full h-full object-contain block" />
                      </>
                    ) : (
                      <div className="absolute inset-0 flex items-center justify-center"><Car size={28} className="text-slate-700 opacity-40" /></div>
                    )}
                    <div className="absolute bottom-1 left-1 z-10">
                      <span className="font-mono font-bold uppercase px-1 py-0.5 rounded"
                        style={{ fontSize: 8, background: 'rgba(0,0,0,0.65)', color: '#94a3b8', letterSpacing: '0.08em' }}>
                        VEHICLE CROP
                      </span>
                    </div>
                  </div>

                  {/* Plate Crop */}
                  <div className="w-full flex flex-col items-center" style={{ background: 'rgba(15,23,42,0.95)', borderTop: '1px solid rgba(51,65,85,0.5)', padding: '6px 8px' }}>
                    <div className="w-full mb-1">
                      <span className="font-mono font-bold uppercase text-slate-500" style={{ fontSize: 8, letterSpacing: '0.08em' }}>PLATE CROP</span>
                    </div>
                    <div className="relative w-full rounded border border-slate-700/70 bg-black overflow-hidden" style={{ paddingTop: '33.33%' /* 3:1 */ }}>
                      {pSrc ? (
                        <>
                          <img src={pSrc} alt="" aria-hidden="true"
                            className="absolute inset-0 w-full h-full object-cover block"
                            style={{ filter: 'blur(10px) brightness(0.5)', transform: 'scale(1.15)' }} />
                          <img src={pSrc} alt="plate" loading="lazy"
                            className="absolute inset-0 w-full h-full object-contain block" />
                        </>
                      ) : (
                        <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 font-mono text-slate-600" style={{ fontSize: 8 }}>
                          <span>NO PLATE IMAGE</span>
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Footer */}
                  <div className="flex items-center justify-between px-2.5 py-2" style={{ background: 'rgba(30,41,59,0.5)', borderTop: '1px solid rgba(51,65,85,0.5)' }}>
                    <div className="flex flex-col">
                      {p.plate_text ? (
                        <span className="font-mono font-black tracking-wider text-cyan-300" style={{ fontSize: 11 }}>
                          {p.plate_text}
                        </span>
                      ) : (
                        <span className="font-mono font-bold text-slate-500" style={{ fontSize: 9 }}>No Plate</span>
                      )}
                    </div>
                    <div className="flex items-center gap-3">
                      <div className="text-center">
                        <div className="font-mono text-slate-500 uppercase" style={{ fontSize: 7, letterSpacing: '0.05em' }}>Confidence</div>
                        <div className={`font-mono font-bold ${p.ocr_confidence > 0.8 ? 'text-emerald-400' : p.ocr_confidence > 0.5 ? 'text-amber-400' : 'text-slate-500'}`} style={{ fontSize: 10 }}>
                          {p.ocr_confidence ? `${(p.ocr_confidence * 100).toFixed(1)}%` : '--'}
                        </div>
                      </div>
                      <div className="text-center">
                        <div className="font-mono text-slate-500 uppercase" style={{ fontSize: 7, letterSpacing: '0.05em' }}>Type</div>
                        <div className="font-mono font-bold text-slate-300" style={{ fontSize: 10 }}>
                          {vType || '--'}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between mt-4 py-2 border-t border-slate-800">
            <button
              onClick={() => setOffset(Math.max(0, offset - limit))}
              disabled={offset === 0}
              className="px-3 py-1.5 rounded flex items-center gap-1 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              style={{ background: 'var(--dvas-surface-2)', border: '1px solid var(--dvas-border)' }}
            >
              <ChevronLeft size={14} className="text-slate-400" />
              <span className="mono text-[10px] font-bold text-slate-300">PREV</span>
            </button>
            <div className="mono text-[10px] text-slate-400">
              Showing {Math.min(offset + 1, missionData.total)}-{Math.min(offset + limit, missionData.total)} of <span className="font-bold text-slate-200">{missionData.total}</span> vehicles
            </div>
            <button
              onClick={() => setOffset(offset + limit)}
              disabled={!missionData.has_more}
              className="px-3 py-1.5 rounded flex items-center gap-1 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              style={{ background: 'var(--dvas-surface-2)', border: '1px solid var(--dvas-border)' }}
            >
              <span className="mono text-[10px] font-bold text-slate-300">NEXT</span>
              <ChevronRight size={14} className="text-slate-400" />
            </button>
          </div>
        </>
      )}
    </div>
  );
}
export default function TrafficAnalytics() {
  const { streams, loading: dronesLoading } = useActiveStreams();
  const activeDrones = streams.map(s => ({
    drone_id: s.drone_id,
    mission_id: s.mission_id,
    status: s.status,
    messages_ready: s.messages_ready,
    consumer_lag: s.consumer_lag,
    frames_received: s.status === 'live' || (s.messages_ready || 0) > 0 ? 1 : 0
  }));
  const [activeDrone, setActiveDrone] = useState(null);
  const [activeMissionId, setActiveMissionId] = useState(null); // current mission_id for selected drone
  const [feedMode, setFeedMode]       = useState('vehicle');
  const [serviceStatus, setServiceStatus] = useState({ vehicle: false, anpr: false });
  const [adding, setAdding] = useState(false);

  // Auto-select first active drone once telemetry loads
  useEffect(() => {
    if (activeDrones.length === 0) return;
    setActiveDrone(prev => {
      if (prev && activeDrones.find(d => d.drone_id === prev)) return prev; // keep if still active
      // Pick first drone with frames, else first in list
      const live = activeDrones.find(d => (d.frames_received || 0) > 0);
      return (live || activeDrones[0])?.drone_id || null;
    });
  }, [activeDrones]);

  // Track the current mission_id for the selected drone from ingest telemetry
  useEffect(() => {
    if (!activeDrone || !activeDrones.length) return;
    const drone = activeDrones.find(d => d.drone_id === activeDrone);
    const missionId = drone?.mission_id || null;
    setActiveMissionId(prev => {
      if (prev !== missionId) return missionId;
      return prev;
    });
  }, [activeDrone, activeDrones]);

  const { data: tracks, loading: tracksLoading } = usePoll('/analytics/vehicle/tracks', POLL_TRACKS);
  const { data: colourData }                      = usePoll('/analytics/vehicle/by-colour', POLL_COLOUR);
  const { data: flowData }                        = usePoll('/analytics/vehicle/stats/flow', POLL_FLOW);
  const { data: stats }                           = usePoll('/analytics/vehicle/stats', 30000);
  // Scope recent plates to the active mission if available, else fall back to unfiltered
  const platesUrl = activeMissionId
    ? `/analytics/plates/recent?mission_id=${encodeURIComponent(activeMissionId)}&limit=50`
    : '/analytics/plates/recent?limit=50';
  const { data: plates }                          = usePoll(platesUrl, POLL_PLATES);
  const { data: alerts }                          = usePoll('/analytics/alerts/theft', POLL_ALERTS);
  const { data: watchlist }                       = usePoll('/analytics/watchlist', POLL_WATCHLIST);

  // Respect the Detection Models page config: only start streams for
  // models the user has explicitly enabled (persisted in the DB via POST /api/models).
  // If the model config cannot be fetched (e.g. user is a drone_operator
  // who has no access to /models), fall back to polling health only.
  useEffect(() => {
    const syncStreams = async () => {
      // Ensure vehicle and ANPR streams are started when visiting Traffic & ANPR page
      try {
        await Promise.allSettled([
          api.get('/analytics/vehicle/stream/start'),
          api.get('/analytics/anpr/stream/start'),
        ]);
      } catch { /* ignore network/permission errors */ }

      try {
        const { data: cfg } = await api.get('/models');
        const active = cfg?.active || [];
        const starts = [];
        if (active.includes('vehicle')) starts.push(api.get('/analytics/vehicle/stream/start'));
        if (active.includes('plate'))   starts.push(api.get('/analytics/anpr/stream/start'));
        if (starts.length) await Promise.allSettled(starts);
      } catch { /* /models unreachable or user lacks permission — do nothing */ }
    };
    syncStreams();
  }, []);

  useEffect(() => {
    const check = async () => {
      try {
        const [vh, ah] = await Promise.allSettled([
          api.get('/analytics/vehicle/health'),
          api.get('/analytics/anpr/health'),
        ]);
        setServiceStatus({
          vehicle: vh.status === 'fulfilled' && vh.value?.data?.stream_running === true,
          anpr:    ah.status === 'fulfilled' && ah.value?.data?.stream_running === true,
        });
      } catch { /* ignore */ }
    };
    check();
    const id = setInterval(check, 5000);
    return () => clearInterval(id);
  }, []);

  const toggleWorker = async (service) => {
    const isRunning = serviceStatus[service];
    const endpoint = isRunning ? `/analytics/${service}/stream/stop` : `/analytics/${service}/stream/start`;
    try {
      await api.get(endpoint);
      setServiceStatus(prev => ({ ...prev, [service]: !isRunning }));
    } catch (e) {
      console.error(`Failed to toggle ${service} worker`, e);
    }
  };

  const handleAddWatchlist = async (plate, reason) => {
    setAdding(true);
    try { await api.post('/analytics/watchlist', { plate, reason }); }
    catch (e) { console.error(e); }
    finally { setAdding(false); }
  };

  const handleRemoveWatchlist = async (plate) => {
    try { await api.delete(`/analytics/watchlist/${plate}`); }
    catch (e) { console.error(e); }
  };

  return (
    <div className="flex flex-col gap-4 p-4">

      {/* Live Feed */}
      <div className="rounded-xl overflow-hidden"
        style={{ background:'var(--dvas-surface)', border:'1px solid var(--dvas-border)' }}>
        <div className="px-4 py-3 flex items-center gap-3 flex-wrap"
          style={{ borderBottom:'1px solid var(--dvas-border)' }}>
          <span className="w-2 h-2 rounded-full bg-violet-500 dvas-live-dot" />
          <span className="dvas-label font-semibold">Traffic & Vehicle Detection Live Feed</span>
          {/* Drone selector — populated from live ingest telemetry */}
          <div className="flex items-center gap-1 ml-2 flex-wrap">
            {dronesLoading && activeDrones.length === 0 && (
              <span className="mono text-[10px] text-slate-500 flex items-center gap-1">
                <RefreshCw size={9} className="animate-spin" /> Discovering drones…
              </span>
            )}
            {activeDrones.map(d => {
              const isLive = (d.frames_received || 0) > 0;
              const isSelected = activeDrone === d.drone_id;
              const isDraining = d.status === 'draining';
              return (
                <button key={d.drone_id} onClick={() => setActiveDrone(d.drone_id)}
                  className="mono text-[10px] px-2 py-1 rounded transition-all flex items-center gap-1"
                  title={`${d.drone_id} — ${isLive ? 'streaming' : 'no frames'}`}
                  style={{
                    background: isSelected ? 'rgba(139,92,246,0.15)':'var(--dvas-surface-2)',
                    color: isSelected ? '#a78bfa' : isLive ? 'var(--dvas-text)' : 'var(--dvas-text-muted)',
                    border: `1px solid ${isSelected ? 'rgba(139,92,246,0.35)':'var(--dvas-border)'}`,
                  }}>
                  {isLive && !isDraining && (
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 dvas-live-dot shrink-0" />
                  )}
                  {isDraining && (
                    <span className="w-1.5 h-1.5 rounded-full bg-amber-400 dvas-live-dot shrink-0" />
                  )}
                  {d.drone_id.replace(/DRONE_KL_/i, 'DRN-').replace(/DRONE_/i, 'DRN-')}
                  {isDraining && (
                    <span className="text-[8px] bg-amber-800/60 text-amber-300 px-1.5 py-0.5 rounded ml-1 font-bold">DRAINING</span>
                  )}
                </button>
              );
            })}
            {!dronesLoading && activeDrones.length === 0 && (
              <span className="mono text-[10px] text-slate-600">No drones connected to ingest</span>
            )}
          </div>
          <div className="flex items-center gap-1 ml-auto">
            <span className="mono text-[10px] text-slate-500 mr-1">View:</span>
            {[['vehicle','Vehicle Tracking'],['anpr','ANPR Overlay']].map(([k,l]) => (
              <button key={k} onClick={() => setFeedMode(k)}
                className="mono text-[10px] px-2 py-1 rounded transition-all"
                style={{
                  background: feedMode===k ? 'rgba(20,184,166,0.12)':'transparent',
                  color: feedMode===k ? '#14b8a6':'var(--dvas-text-muted)',
                  border: `1px solid ${feedMode===k ? 'rgba(20,184,166,0.3)':'var(--dvas-border)'}`,
                }}>{l}</button>
            ))}
          </div>
          <div className="flex items-center gap-2">
            {[['vehicle','Vehicle'],['anpr','ANPR']].map(([k,l]) => {
              const isRunning = serviceStatus[k];
              return (
                <button
                  key={k}
                  onClick={() => toggleWorker(k)}
                  title={isRunning ? `Click to pause ${l} inference (saves GPU/CPU)` : `Click to start ${l} inference`}
                  className="mono text-[9px] px-2 py-1 rounded-full flex items-center gap-1.5 transition-all cursor-pointer hover:opacity-90"
                  style={{
                    background: isRunning ? 'rgba(52,211,153,0.12)' : 'rgba(148,163,184,0.1)',
                    color: isRunning ? '#34d399' : '#64748b',
                    border: `1px solid ${isRunning ? 'rgba(52,211,153,0.35)' : 'rgba(148,163,184,0.2)'}`,
                  }}
                >
                  <span className={`w-1.5 h-1.5 rounded-full ${isRunning ? 'bg-emerald-400 dvas-live-dot' : 'bg-slate-500'}`} />
                  <span>{l} {isRunning ? 'RUNNING' : 'PAUSED'}</span>
                </button>
              );
            })}
          </div>
        </div>
        {(() => {
          const selectedDroneObj = activeDrones.find(d => d.drone_id === activeDrone);
          const remaining = selectedDroneObj?.consumer_lag ? Object.values(selectedDroneObj.consumer_lag).reduce((sum, v) => sum + v, 0) : (selectedDroneObj?.messages_ready ?? 0);
          if (selectedDroneObj?.status === 'draining') {
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
        <div style={{ height: 400, background:'#0a0a0f' }}>
          <AnnotatedFrameViewer
            droneId={activeDrone}
            mode={feedMode}
            streamRunning={feedMode === 'anpr' ? serviceStatus.anpr : serviceStatus.vehicle}
          />
        </div>
      </div>


      {/* KPI Strip */}
      <KPIStrip tracks={tracks} plates={plates} alerts={alerts} stats={stats} />

      {/* Main grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2">
          <Panel title="Tracked Vehicles — Live Ledger" icon={Car} iconClass="text-violet-400"
            badge={tracks?.count ? `${tracks.count} active tracks` : undefined}>
            <VehicleLedger tracks={tracks} loading={tracksLoading} />
          </Panel>
        </div>
        <Panel title="Vehicle Colour Distribution" icon={Activity} iconClass="text-teal-400">
          <ColourDonut data={colourData} />
        </Panel>
      </div>

      {/* Flow chart */}
      <Panel title="Vehicle Flow — 24h Time Series" icon={Activity} iconClass="text-amber-400"
        right={<span className="mono text-[10px] text-slate-500">Live · 30s refresh</span>}>
        <FlowChart data={flowData} />
      </Panel>

      {/* ANPR Window */}
      <Panel title="ANPR — Number Plate Detection Window" icon={Hash} iconClass="text-teal-400"
        right={
          <span className={`mono text-[9px] px-1.5 py-0.5 rounded flex items-center gap-1
            ${serviceStatus.anpr ? 'text-emerald-400':'text-slate-500'}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${serviceStatus.anpr ? 'bg-emerald-400 dvas-live-dot':'bg-slate-500'}`} />
            {serviceStatus.anpr ? 'Live':'Offline'}
          </span>
        }>
        <ANPRWindow plates={plates} activeDrone={activeDrone} activeMissionId={activeMissionId} anprStreamRunning={serviceStatus.anpr} />
      </Panel>

      {/* Watchlist & Alerts */}
      <Panel title="Watchlist & Theft Alerts" icon={ShieldAlert} iconClass="text-red-400"
        badge={alerts?.count > 0 ? `${alerts.count} ALERT${alerts.count > 1 ? 'S':''}`:undefined}>
        <WatchlistPanel
          alerts={alerts} watchlist={watchlist}
          onAdd={handleAddWatchlist} onRemove={handleRemoveWatchlist} adding={adding}
        />
      </Panel>

      {/* Mission Vehicle History */}
      <Panel title="Mission Vehicle History" icon={Archive} iconClass="text-violet-400"
        right={<span className="mono text-[10px] text-slate-500">Historical mission data</span>}>
        <MissionHistory />
      </Panel>

    </div>
  );
}