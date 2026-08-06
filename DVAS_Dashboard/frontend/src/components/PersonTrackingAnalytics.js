/**
 * PersonTrackingAnalytics.js
 * ─────────────────────────────────────────────────────────────────────────────
 * Person Tracking Analytics page — fully connected to live analytics services.
 * Kerala Police | Drone Video Analytics System
 *
 * Data sources (live):
 *   - /api/analytics/person/tracks      → live person track table (5s poll)
 *   - /api/analytics/person/dedup       → deduplication stats & clusters (15s poll)
 *   - /api/analytics/person/identities  → deduplicated person crops from MinIO (10s poll)
 *   - /api/analytics/person/gallery     → face crops from MinIO (10s poll)
 *   - /api/analytics/person/count       → KPI counts per drone (5s poll)
 *   - /api/analytics/events/stream      → SSE for real-time detection events
 *   - POST /api/analytics/person/reid   → trigger manual Re-ID clustering
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, BarChart, Bar, Cell,
} from 'recharts';
import {
  Users, Activity, AlertTriangle, CheckCircle, Eye, Radio,
  VideoOff, RefreshCw, Wifi, WifiOff, UserCheck,
  Fingerprint, Clock, Copy, Database,
} from 'lucide-react';
import {
  usePersonTracks, usePersonDedup, usePersonIdentities,
  useFacesGallery, usePersonCount, useAnalyticsSSE,
} from '../lib/useAnalytics';
import LiveFrame from './LiveFrame';
import { api } from '../lib/api';

/* ───────────────────────────────────────────────────────────────
   HELPERS
   ────────────────────────────────────────────────────────────── */
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
            style={{ background: 'rgba(56,189,248,0.12)', color: '#38bdf8', border: '1px solid rgba(56,189,248,0.2)' }}>
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
function LiveStreamSection({ activeDrone, setActiveDrone, drones, liveCount }) {
  const hasDrone = !!activeDrone;

  const droneList = drones?.length > 0
    ? drones
    : [{ id: 'DRN-001' }, { id: 'DRN-002' }, { id: 'DRN-003' }, { id: 'DRN-004' }];

  return (
    <div className="rounded-xl overflow-hidden"
      style={{ background: 'var(--dvas-surface)', border: '1px solid var(--dvas-border)' }}>
      <div className="px-4 py-3 flex items-center gap-3"
        style={{ borderBottom: '1px solid var(--dvas-border)' }}>
        <span className={`w-2 h-2 rounded-full dvas-live-dot ${hasDrone ? 'bg-sky-500' : 'bg-slate-500'}`} />
        <span className="dvas-label font-semibold">Person & Face Detection Live Feed</span>
        <div className="flex items-center gap-1 ml-2">
          {droneList.map(d => (
            <button key={d.id} onClick={() => setActiveDrone(d.id)}
              className="mono text-[10px] px-2 py-1 rounded transition-all"
              style={{
                background: activeDrone === d.id ? 'rgba(56,189,248,0.15)' : 'var(--dvas-surface-2)',
                color: activeDrone === d.id ? '#38bdf8' : 'var(--dvas-text-muted)',
                border: `1px solid ${activeDrone === d.id ? 'rgba(56,189,248,0.35)' : 'var(--dvas-border)'}`,
              }}>{d.id}</button>
          ))}
        </div>
      </div>

      <div className="relative bg-black flex items-center justify-center" style={{ height: 420 }}>
        {hasDrone ? (
          <div className="relative w-full h-full">
            <LiveFrame droneId={activeDrone} isOffline={false} isDegraded={false} />
            <div className="absolute top-3 left-3 flex flex-col gap-1.5 z-10">
              <div className="mono text-[10px] bg-black/80 px-2 py-1 rounded text-sky-300">
                Person tracking active · ByteTrack
              </div>
              <div className="mono text-[10px] bg-black/80 px-2 py-1 rounded text-emerald-300">
                Face detection · Re-ID enabled
              </div>
            </div>
            <div className="absolute top-3 right-3 mono text-[10px] bg-black/80 px-2 py-1 rounded text-emerald-400 flex items-center gap-1 z-10">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 dvas-live-dot" />
              LIVE
            </div>
            <div className="absolute bottom-3 left-3 mono text-[10px] bg-black/80 px-2 py-1 rounded text-slate-400 z-10">
              YOLOv8m Person · YOLOv8n-face · ResNet50 Re-ID
            </div>
          </div>
        ) : (
          <div className="text-center text-slate-500 flex flex-col items-center">
            <VideoOff size={48} className="mb-3 opacity-50 text-slate-600" />
            <p className="mono text-sm">Person analytics offline</p>
            <p className="mono text-[10px] mt-1 text-slate-600">Select a drone to view live feed</p>
          </div>
        )}
      </div>
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 2 — Person KPI Strip (LIVE)
   ────────────────────────────────────────────────────────────── */
function PersonKPIStrip({ tracks, dedupData, countData, sseConnected }) {
  const tracksArr = Array.isArray(tracks) ? tracks : [];
  const totalTracked  = tracksArr.length || countData?.total_tracks || 0;
  const uniquePersons = dedupData?.unique_identities || dedupData?.total_unique || 0;
  const facesDetected = dedupData?.total_faces || 0;
  const duplicates    = dedupData?.duplicates_found || 0;
  const dedupRate     = facesDetected > 0 ? Math.round((duplicates / facesDetected) * 100) : 0;

  const kpis = [
    { label: 'Persons Tracked',   value: totalTracked,                        tone: 'text-sky-400',     icon: Users,        sub: 'Active tracks across drones' },
    { label: 'Unique Identities', value: uniquePersons,                       tone: 'text-violet-400',  icon: UserCheck,    sub: 'After Re-ID deduplication' },
    { label: 'Faces Detected',    value: facesDetected,                       tone: 'text-amber-400',   icon: Fingerprint,  sub: 'Total face crops stored' },
    { label: 'Duplicates Removed',value: duplicates,                          tone: 'text-emerald-400', icon: Copy,         sub: `${dedupRate}% dedup rate` },
    { label: 'Live Stream',       value: sseConnected ? 'ON' : 'OFF',         tone: sseConnected ? 'text-emerald-400' : 'text-slate-500', icon: Radio, sub: sseConnected ? 'Real-time SSE active' : 'Polling mode' },
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
   SECTION 3 — Live Track Table (LIVE)
   ────────────────────────────────────────────────────────────── */
function PersonTrackTable({ tracks, loading }) {
  const arr = Array.isArray(tracks) ? tracks : [];

  if (loading && arr.length === 0) {
    return (
      <div className="flex items-center justify-center py-10 gap-2">
        <RefreshCw size={16} className="text-slate-500 animate-spin" />
        <span className="mono text-xs text-slate-500">Loading person tracks…</span>
      </div>
    );
  }

  if (!loading && arr.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-10 gap-2">
        <Users size={36} className="text-slate-600 opacity-40" />
        <span className="mono text-xs text-slate-500">No active person tracks</span>
        <span className="mono text-[10px] text-slate-600">Person analyzer will populate when drones stream frames</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="mono text-[10px] mb-1" style={{ color: 'var(--dvas-text-dim)' }}>
        {arr.length} active tracks
      </div>
      <div className="overflow-x-auto">
        <table className="w-full" style={{ borderCollapse: 'separate', borderSpacing: '0 3px' }}>
          <thead>
            <tr>
              {['Track ID', 'Drone', 'Conf', 'Cluster', 'Duration', 'Last Seen'].map(h => (
                <th key={h} className="text-left px-3 py-1 mono text-[10px]"
                  style={{ color: 'var(--dvas-text-dim)' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {arr.slice(0, 50).map((t, i) => {
              const conf    = t.confidence ?? t.conf ?? 0;
              const cluster = t.cluster_id != null ? `CL-${t.cluster_id}` : '—';
              const drone   = t.drone_id || '—';
              const dur     = t.duration_sec != null ? `${Math.round(t.duration_sec)}s` : '—';
              const lastTs  = t.last_seen || t.updated_at || t.ts;
              const confPct = Math.round(conf * 100);
              const confCol = confPct > 70 ? '#2dd4bf' : confPct > 40 ? '#fbbf24' : '#f87171';

              return (
                <tr key={t.track_id || t.id || i}
                  style={{ background: 'var(--dvas-surface-2)', borderRadius: 8 }}>
                  <td className="px-3 py-2 rounded-l-lg">
                    <span className="mono text-xs font-bold" style={{ color: 'var(--dvas-text)' }}>
                      #{String(t.tracker_id || t.track_id || i).slice(-6)}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    <span className="mono text-[10px] px-1.5 py-0.5 rounded"
                      style={{ background: 'rgba(56,189,248,0.1)', color: '#38bdf8', border: '1px solid rgba(56,189,248,0.2)' }}>
                      {drone}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-1.5">
                      <div className="h-1.5 w-12 rounded-full" style={{ background: 'var(--dvas-elev)' }}>
                        <div className="h-full rounded-full" style={{ width: `${confPct}%`, background: confCol }} />
                      </div>
                      <span className="mono text-[10px]" style={{ color: confCol }}>{confPct}%</span>
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-muted)' }}>{cluster}</span>
                  </td>
                  <td className="px-3 py-2">
                    <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-muted)' }}>{dur}</span>
                  </td>
                  <td className="px-3 py-2 rounded-r-lg">
                    <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>{fmtAge(lastTs)}</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 4 — Detection chart (SSE rolling window)
   ────────────────────────────────────────────────────────────── */
function DetectionChart({ sseWindow }) {
  const data = sseWindow.length > 0
    ? sseWindow
    : Array.from({ length: 20 }, (_, i) => ({ t: String(i), count: 0 }));

  return (
    <div className="relative">
      {sseWindow.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center z-10">
          <span className="mono text-xs text-slate-500">Waiting for person detection events…</span>
        </div>
      )}
      <ResponsiveContainer width="100%" height={180}>
        <AreaChart data={data} margin={{ top: 8, right: 8, left: -24, bottom: 0 }}>
          <defs>
            <linearGradient id="gPerson" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#818cf8" stopOpacity={0.5} />
              <stop offset="100%" stopColor="#818cf8" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--dvas-border)" strokeDasharray="3 3" />
          <XAxis dataKey="t" stroke="var(--dvas-text-muted)" fontSize={9} tick={false} />
          <YAxis stroke="var(--dvas-text-muted)" fontSize={9} />
          <Tooltip {...CHART_TOOLTIP} />
          <Area type="monotone" dataKey="count" stroke="#818cf8" fill="url(#gPerson)" strokeWidth={2} name="Detections" dot={false} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 5 — Detected Persons Gallery (MinIO presigned URLs)
   ────────────────────────────────────────────────────────────── */
function PersonGallery({ identities, loading, label = 'Detected Persons' }) {
  const [page, setPage] = useState(0);
  const perPage = 12;
  const arr = Array.isArray(identities) ? identities : [];
  const pages = Math.ceil(arr.length / perPage);
  const shown = arr.slice(page * perPage, page * perPage + perPage);

  if (loading && arr.length === 0) {
    return (
      <div className="flex items-center justify-center py-12 gap-2">
        <RefreshCw size={16} className="text-slate-500 animate-spin" />
        <span className="mono text-xs text-slate-500">Loading {label.toLowerCase()}…</span>
      </div>
    );
  }

  if (!loading && arr.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 gap-3">
        <Eye size={36} className="text-slate-600 opacity-40" />
        <span className="mono text-xs text-slate-500">No {label.toLowerCase()} yet</span>
        <span className="mono text-[10px] text-slate-600">Images stored in MinIO will appear here after detection</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-2">
        {shown.map((item, i) => {
          const imgUrl  = item.best_image_url || item.image_url || item.presigned_url || item.url;
          const clusterId = item.cluster_id != null ? `CL-${item.cluster_id}` : `ID-${i}`;
          const drone   = item.drone_id || '';
          const lastSeen = item.last_seen || item.ts;
          const count   = item.embedding_count || 1;

          return (
            <div key={item.identity_id || item.id || i}
              className="flex flex-col gap-1 rounded-xl overflow-hidden transition-all hover:scale-105"
              style={{ background: 'var(--dvas-surface-2)', border: '1px solid var(--dvas-border)' }}>
              {/* Crop image */}
              <div className="relative w-full" style={{ paddingBottom: '120%', background: 'var(--dvas-elev)' }}>
                {imgUrl ? (
                  <img
                    src={imgUrl}
                    alt={clusterId}
                    className="absolute inset-0 w-full h-full object-cover"
                    onError={(e) => { e.target.style.display = 'none'; }}
                  />
                ) : (
                  <div className="absolute inset-0 flex items-center justify-center">
                    <Users size={24} className="text-slate-600 opacity-40" />
                  </div>
                )}
                <div className="absolute top-1 left-1">
                  <span className="mono text-[8px] px-1 py-0.5 rounded font-bold"
                    style={{ background: 'rgba(0,0,0,0.7)', color: '#38bdf8' }}>{clusterId}</span>
                </div>
                {count > 1 && (
                  <div className="absolute top-1 right-1">
                    <span className="mono text-[8px] px-1 py-0.5 rounded font-bold"
                      style={{ background: 'rgba(0,0,0,0.7)', color: '#a78bfa' }}>×{count}</span>
                  </div>
                )}
              </div>
              {/* Caption */}
              <div className="px-1.5 pb-1.5 flex flex-col gap-0.5">
                {drone && (
                  <span className="mono text-[9px]" style={{ color: 'var(--dvas-text-dim)' }}>{drone}</span>
                )}
                <span className="mono text-[9px]" style={{ color: 'var(--dvas-text-dim)' }}>{fmtAge(lastSeen)}</span>
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
            {Array.from({ length: Math.min(pages, 8) }).map((_, i) => (
              <button key={i} onClick={() => setPage(i)}
                className="w-6 h-6 rounded text-[10px] mono font-bold transition-colors"
                style={{
                  background: i === page ? 'rgba(56,189,248,0.15)' : 'var(--dvas-surface-2)',
                  color: i === page ? '#38bdf8' : 'var(--dvas-text-muted)',
                  border: `1px solid ${i === page ? 'rgba(56,189,248,0.3)' : 'var(--dvas-border)'}`,
                }}>{i + 1}</button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 6 — Deduplication Stats
   ────────────────────────────────────────────────────────────── */
function DedupPanel({ dedupData, onReidTrigger, reidLoading }) {
  const clusters = Array.isArray(dedupData?.clusters) ? dedupData.clusters : [];
  const totalFaces    = dedupData?.total_faces    || 0;
  const uniqueIds     = dedupData?.unique_identities || dedupData?.total_unique || 0;
  const duplicates    = dedupData?.duplicates_found  || 0;
  const lastClustered = dedupData?.last_clustered_at || dedupData?.ts;

  // Bar chart for cluster sizes (top 10)
  const chartData = clusters
    .slice(0, 10)
    .map((c, i) => ({
      name: `CL-${c.cluster_id ?? i}`,
      count: c.size || c.count || 0,
    }));

  const barColors = ['#818cf8', '#38bdf8', '#34d399', '#f59e0b', '#f87171',
                     '#a78bfa', '#2dd4bf', '#4ade80', '#facc15', '#fb923c'];

  return (
    <div className="flex flex-col gap-4">
      {/* Stats row */}
      <div className="grid grid-cols-3 gap-3">
        {[
          { label: 'Total Faces', value: totalFaces,  tone: 'text-amber-400' },
          { label: 'Unique IDs',  value: uniqueIds,   tone: 'text-sky-400' },
          { label: 'Duplicates',  value: duplicates,  tone: 'text-emerald-400' },
        ].map(({ label, value, tone }) => (
          <div key={label} className="rounded-lg p-3 text-center"
            style={{ background: 'var(--dvas-surface-2)', border: '1px solid var(--dvas-border)' }}>
            <div className={`mono font-bold text-xl ${tone}`}>{value}</div>
            <div className="mono text-[10px] mt-0.5" style={{ color: 'var(--dvas-text-dim)' }}>{label}</div>
          </div>
        ))}
      </div>

      {/* Re-ID trigger */}
      <div className="flex items-center justify-between">
        <div>
          <div className="mono text-xs font-semibold" style={{ color: 'var(--dvas-text)' }}>Re-ID Clustering</div>
          <div className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>
            {lastClustered ? `Last run: ${fmtAge(lastClustered)}` : 'Not yet run'}
          </div>
        </div>
        <button
          onClick={onReidTrigger}
          disabled={reidLoading}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg mono text-xs font-semibold transition-all"
          style={{
            background: reidLoading ? 'rgba(129,140,248,0.05)' : 'rgba(129,140,248,0.12)',
            color: '#818cf8',
            border: '1px solid rgba(129,140,248,0.3)',
            opacity: reidLoading ? 0.5 : 1,
            cursor: reidLoading ? 'not-allowed' : 'pointer',
          }}>
          {reidLoading ? (
            <><RefreshCw size={11} className="animate-spin" /> Running…</>
          ) : (
            <><Fingerprint size={11} /> Trigger Re-ID</>
          )}
        </button>
      </div>

      {/* Cluster bar chart */}
      {chartData.length > 0 && (
        <div>
          <div className="dvas-label mb-2" style={{ fontSize: 9 }}>CLUSTER SIZES (TOP 10)</div>
          <ResponsiveContainer width="100%" height={120}>
            <BarChart data={chartData} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
              <CartesianGrid stroke="var(--dvas-border)" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="name" stroke="var(--dvas-text-muted)" fontSize={8} />
              <YAxis stroke="var(--dvas-text-muted)" fontSize={8} />
              <Tooltip {...CHART_TOOLTIP} />
              <Bar dataKey="count" name="Faces" radius={[3, 3, 0, 0]}>
                {chartData.map((_, i) => (
                  <Cell key={i} fill={barColors[i % barColors.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   MAIN EXPORT
   ────────────────────────────────────────────────────────────── */
export default function PersonTrackingAnalytics({ drones }) {
  const [activeDrone, setActiveDrone] = useState(null);

  // Auto-select the first online drone when drones list loads/changes
  useEffect(() => {
    if (!drones || drones.length === 0) return;
    const currentStillValid = drones.some(d => d.id === activeDrone);
    if (!activeDrone || !currentStillValid) {
      const onlineDrone = drones.find(d => d.status !== 'offline');
      setActiveDrone(onlineDrone ? onlineDrone.id : drones[0].id);
    }
  }, [drones]); // eslint-disable-line react-hooks/exhaustive-deps
  const [reidLoading, setReidLoading] = useState(false);
  const [sseWindow,   setSseWindow]   = useState([]); // rolling detection counts

  const { data: tracksData, loading: tracksLoading }  = usePersonTracks('', 5000);
  const { data: dedupData }                            = usePersonDedup(15000);
  const { data: identitiesData, loading: identLoading } = usePersonIdentities('', 10000);
  const { data: facesData, loading: facesLoading }     = useFacesGallery('', 10000);
  const { data: countData }                            = usePersonCount('', 5000);

  // SSE — build rolling chart window
  const handleSSEEvent = useCallback((evt) => {
    if (evt?.service !== 'person') return;
    const count = evt.data?.person_count ?? evt.data?.count ?? 0;
    const ts    = new Date().toISOString().slice(11, 19);
    setSseWindow(w => [...w.slice(-59), { t: ts, count: Number(count) }]);
  }, []);

  const { connected: sseConnected } = useAnalyticsSSE(handleSSEEvent);

  // Auto-select drone
  useEffect(() => {
    if (drones?.length > 0) {
      setActiveDrone(drones.find(d => d.id === 'DRN-003') ? 'DRN-003' : drones[0].id);
    }
  }, [drones]);

  // Re-ID trigger
  const handleReidTrigger = async () => {
    setReidLoading(true);
    try {
      await api.post('/analytics/person/reid');
    } catch (err) {
      console.error('Re-ID trigger failed:', err);
    } finally {
      setTimeout(() => setReidLoading(false), 3000);
    }
  };

  // Normalise arrays from various response shapes
  const tracks     = Array.isArray(tracksData) ? tracksData
    : Array.isArray(tracksData?.tracks) ? tracksData.tracks : [];
  const identities = Array.isArray(identitiesData) ? identitiesData
    : Array.isArray(identitiesData?.identities) ? identitiesData.identities : [];
  const faces      = Array.isArray(facesData) ? facesData
    : Array.isArray(facesData?.faces) ? facesData.faces : [];

  const liveCount = countData?.total_current ?? countData?.count ?? tracks.length;

  return (
    <div className="flex flex-col gap-5" style={{ fontFamily: "'Plus Jakarta Sans', sans-serif" }}>

      {/* LIVE STREAM */}
      <LiveStreamSection
        activeDrone={activeDrone}
        setActiveDrone={setActiveDrone}
        drones={drones}
        liveCount={liveCount}
      />

      {/* KPI STRIP */}
      <PersonKPIStrip
        tracks={tracks}
        dedupData={dedupData}
        countData={countData}
        sseConnected={sseConnected}
      />

      {/* TRACK TABLE + DETECTION CHART */}
      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-12 lg:col-span-8">
          <Panel title="Active Person Tracks" icon={Activity} iconClass="text-sky-400"
            badge={tracks.length > 0 ? `${tracks.length} tracks` : null}
            right={
              <span className={`mono text-[10px] flex items-center gap-1 ${sseConnected ? 'text-sky-400' : 'text-slate-500'}`}>
                <span className={`w-1.5 h-1.5 rounded-full ${sseConnected ? 'bg-sky-400 dvas-live-dot' : 'bg-slate-500'}`} />
                {sseConnected ? 'Live' : 'Polling'}
              </span>
            }>
            <PersonTrackTable tracks={tracks} loading={tracksLoading} />
          </Panel>
        </div>
        <div className="col-span-12 lg:col-span-4">
          <Panel title="Detections (Real-time)" icon={Radio} iconClass="text-violet-400"
            right={
              <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>SSE rolling window</span>
            }>
            <DetectionChart sseWindow={sseWindow} />
          </Panel>
        </div>
      </div>

      {/* PERSON GALLERY (deduplicated crops from MinIO) */}
      <Panel
        title="Detected Persons — Image Gallery"
        icon={Eye}
        iconClass="text-sky-400"
        badge={identities.length > 0 ? `${identities.length} identities` : null}
        right={
          <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>
            Deduplicated · MinIO stored
          </span>
        }>
        <PersonGallery identities={identities} loading={identLoading} label="Detected Persons" />
      </Panel>

      {/* FACE GALLERY (deduplicated face crops from MinIO) */}
      <Panel
        title="Detected Faces — Image Gallery"
        icon={Fingerprint}
        iconClass="text-amber-400"
        badge={faces.length > 0 ? `${faces.length} faces` : null}
        right={
          <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>
            Face crops · MinIO stored
          </span>
        }>
        <PersonGallery identities={faces} loading={facesLoading} label="Detected Faces" />
      </Panel>

      {/* DEDUPLICATION PANEL */}
      <Panel
        title="Re-ID & Deduplication"
        icon={Database}
        iconClass="text-violet-400"
        right={
          <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>15s refresh</span>
        }>
        <DedupPanel
          dedupData={dedupData}
          onReidTrigger={handleReidTrigger}
          reidLoading={reidLoading}
        />
      </Panel>

    </div>
  );
}
