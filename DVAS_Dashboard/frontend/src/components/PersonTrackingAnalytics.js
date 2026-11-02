/**
 * PersonTrackingAnalytics.js
 * ─────────────────────────────────────────────────────────────────────────────
 * Person Tracking Analytics page — fully connected to live analytics services.
 * Kerala Police | Drone Video Analytics System
 *
 * Data sources (live):
 *   - /api/analytics/person/live       → live person track card grid w/ crop64 (3s poll)
 *   - /api/analytics/person/tracks     → DB track history for the table (5s poll)
 *   - /api/analytics/person/dedup      → deduplication stats & clusters (15s poll)
 *   - /api/analytics/person/identities → deduplicated person crops from MinIO (10s poll)
 *   - /api/analytics/person/gallery    → face crops from MinIO (10s poll)
 *   - /api/analytics/person/count      → KPI counts per drone (5s poll)
 *   - /api/analytics/events/stream     → SSE for real-time detection events
 *   - /api/analytics/person/annotated  → annotated JPEG frame (800ms poll)
 *   - /api/analytics/person/health     → stream_running status (5s poll)
 *   - POST /api/analytics/person/reid  → trigger manual Re-ID clustering
 *   - POST /api/analytics/person/deduplicate → trigger mission dedup
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid,
} from 'recharts';
import {
  Users, Activity, AlertTriangle, CheckCircle, Eye, Radio,
  VideoOff, RefreshCw, Wifi, WifiOff, UserCheck,
  Fingerprint, Clock, Copy, Database, Play, Square, Cpu,
  Zap, Power, ChevronLeft, ChevronRight, Shield,
  Search, X, Maximize2, Download, Calendar, ExternalLink,
  Plus, Trash2, SlidersHorizontal, Check, FileCheck,
  Bell, Volume2, VolumeX, ShieldAlert,
} from 'lucide-react';
import {
  useAnalyticsFetch,
  usePersonTracks, usePersonDedup, usePersonIdentities,
  useFacesGallery, usePersonCount, usePersonLive, useAnalyticsSSE,
  usePersonDedupProgress, usePersonMissionDedup,
  usePersonWatchlist, usePersonWatchlistAlerts, usePersonAnnotationConfig,
} from '../lib/useAnalytics';
import { useActiveStreams } from '../hooks/useActiveStreams';
import { useJpegPoller } from '../hooks/useJpegPoller';
import { api, apiError } from '../lib/api';
import { fmtIST } from '../lib/ist';

function playAlertChime() {
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    const ctx = new AudioCtx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(440, ctx.currentTime + 0.35);
    gain.gain.setValueAtTime(0.25, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.35);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.35);
  } catch (e) {
    // Autoplay restrictions
  }
}

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

/**
 * Image URLs carry no credential: the proxy authenticates with the httpOnly
 * dvas_access_token cookie (same origin). Any token= left in a stored URL is removed.
 */
export function withoutToken(url) {
  if (!url || !url.includes('token=')) return url;
  const [path, query = ''] = url.split('?');
  const rest = query.split('&').filter((p) => p && !p.startsWith('token=')).join('&');
  return rest ? `${path}?${rest}` : path;
}

/**
 * Rewrite an internal MinIO URL (http://minio:9000/BUCKET/KEY?...)
 * into a proxied URL (/api/minio/image?bucket=BUCKET&key=KEY) that the
 * browser can actually reach.  Also handles raw object_key strings.
 */
export function toProxiedUrl(objectKeyOrUrl, bucket = 'dvas-faces') {
  if (!objectKeyOrUrl) return null;
  if (objectKeyOrUrl.startsWith('data:image')) return objectKeyOrUrl;

  // Already a proxied URL — pass through (without any token)
  if (objectKeyOrUrl.startsWith('/api/minio/')) {
    return withoutToken(objectKeyOrUrl);
  }

  // Internal MinIO presigned URL: http://minio:9000/<bucket>/<key>?...
  if (objectKeyOrUrl.startsWith('http://minio') || objectKeyOrUrl.startsWith('http://localhost') || objectKeyOrUrl.startsWith('http://127.0.0.1')) {
    try {
      const u = new URL(objectKeyOrUrl);
      const parts = u.pathname.split('/').filter(Boolean); // ['<bucket>', ...rest]
      if (parts.length >= 2) {
        const b = parts[0];
        const k = parts.slice(1).join('/');
        return `/api/minio/image?bucket=${encodeURIComponent(b)}&key=${encodeURIComponent(k)}`;
      }
    } catch { /* ignore parse errors */ }
  }

  // Plain object_key string (e.g. "faces/DRN-001/...jpg")
  return `/api/minio/image?bucket=${encodeURIComponent(bucket)}&key=${encodeURIComponent(objectKeyOrUrl)}`;
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
   useJpegPoller
   ────────────────────────────────────────────────────────────── */


/* ───────────────────────────────────────────────────────────────
   SECTION 1 — Annotated Person Feed Viewer
   ────────────────────────────────────────────────────────────── */
const FRAME_INTERVAL = 800;

function AnnotatedPersonFeedViewer({ activeDrone, setActiveDrone, drones, streamRunning, onStartStream, onStopStream, modelActive, startingStream, activeMissionId, onNavigateTab, annotationConfig, onToggleAnnotation }) {
  const hasDrone = !!activeDrone;
  const [fps, setFps] = useState(0);
  const fpsRef = useRef({ count: 0, ts: Date.now() });
  const prevAnnotatedRef = useRef(null);

  const { streams } = useActiveStreams();
  const liveStreams = streams?.filter(s => s.status === 'live' || s.status === 'draining' || s.online);
  const droneList = liveStreams?.length > 0
    ? liveStreams.map(s => ({ id: s.drone_id, ...s }))
    : (drones?.length > 0
        ? drones.filter(d => (d.status === 'live' || d.status === 'draining') && !/^DRN-00[1-5]$/i.test(d.id || d.drone_id))
        : []);

  const rawUrl = hasDrone
    ? `/api/ingest-server/frame/${encodeURIComponent(activeDrone)}/latest?drone_id=${encodeURIComponent(activeDrone)}`
    : null;
  const { blobUrl: rawSrc, ok: rawOk } = useJpegPoller(rawUrl, 1500, hasDrone);

  const annotatedUrl = hasDrone
    ? `/api/analytics/person/annotated?drone_id=${encodeURIComponent(activeDrone)}`
    : null;
  const { blobUrl: annotatedSrc, ok: annotatedOk } = useJpegPoller(
    annotatedUrl, FRAME_INTERVAL, hasDrone && streamRunning
  );

  useEffect(() => {
    if (!annotatedSrc || annotatedSrc === prevAnnotatedRef.current) return;
    prevAnnotatedRef.current = annotatedSrc;
    fpsRef.current.count++;
    if (Date.now() - fpsRef.current.ts > 3000) {
      setFps(Math.round(fpsRef.current.count / ((Date.now() - fpsRef.current.ts) / 1000)));
      fpsRef.current = { count: 0, ts: Date.now() };
    }
  }, [annotatedSrc]);

  const displaySrc  = annotatedOk ? annotatedSrc : rawSrc;
  const isAnnotated = annotatedOk && !!annotatedSrc;
  const hasAnyFeed  = !!displaySrc;

  const modelStatusLabel = streamRunning
    ? 'DETECTION ACTIVE'
    : modelActive ? 'MODEL READY' : 'MODEL INACTIVE';
  const modelStatusColor = streamRunning ? '#34d399' : modelActive ? '#fbbf24' : '#64748b';

  return (
    <div className="rounded-xl overflow-hidden"
      style={{ background: 'var(--dvas-surface)', border: '1px solid var(--dvas-border)' }}>
      {/* Header */}
      <div className="px-4 py-3 flex items-center gap-3 flex-wrap"
        style={{ borderBottom: '1px solid var(--dvas-border)' }}>
        <span className={`w-2 h-2 rounded-full dvas-live-dot ${hasDrone && hasAnyFeed ? 'bg-sky-500' : 'bg-slate-500'}`} />
        <span className="dvas-label font-semibold">Person & Face Detection Live Feed</span>

        <div className="flex items-center gap-1 ml-2">
          {droneList.map(d => {
            const isDraining = d.status === 'draining';
            return (
              <button key={d.id} onClick={() => setActiveDrone && setActiveDrone(d.id)}
                disabled={!setActiveDrone}
                title={!setActiveDrone ? 'Drone is selected at the top of the page' : undefined}
                className="mono text-[10px] px-2 py-1 rounded transition-all flex items-center gap-1"
                style={{
                  background: activeDrone === d.id ? 'rgba(56,189,248,0.15)' : 'var(--dvas-surface-2)',
                  color: activeDrone === d.id ? '#38bdf8' : 'var(--dvas-text-muted)',
                  border: `1px solid ${activeDrone === d.id ? 'rgba(56,189,248,0.35)' : 'var(--dvas-border)'}`,
                }}>
                {isDraining && (
                  <span className="w-1.5 h-1.5 rounded-full bg-amber-400 dvas-live-dot shrink-0" />
                )}
                {d.id}
                {isDraining && (
                  <span className="text-[8px] bg-amber-800/60 text-amber-300 px-1.5 py-0.5 rounded ml-1 font-bold">DRAINING</span>
                )}
              </button>
            )
          })}
        </div>

        <div className="flex-1" />

        {modelActive && !streamRunning && (
          <button onClick={onStartStream} disabled={startingStream}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg mono text-[11px] font-bold transition-all"
            style={{
              background: startingStream ? 'rgba(251,191,36,0.08)' : 'rgba(251,191,36,0.15)',
              color: '#fbbf24', border: '1px solid rgba(251,191,36,0.35)',
              cursor: startingStream ? 'wait' : 'pointer',
            }}>
            {startingStream
              ? <><RefreshCw size={11} className="animate-spin" /> Starting…</>
              : <><Play size={11} /> Start Person Detection</>
            }
          </button>
        )}
        {streamRunning && (
          <div className="flex items-center gap-2">
            <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg mono text-[10px] font-bold"
              style={{ background: 'rgba(52,211,153,0.12)', color: '#34d399', border: '1px solid rgba(52,211,153,0.3)' }}>
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 dvas-live-dot" />
              Detection Active
            </span>
            <button onClick={onStopStream}
              className="flex items-center gap-1 px-2.5 py-1 rounded-lg mono text-[10px] font-bold transition-all hover:bg-red-500/20"
              title="Stop Person Detection worker to save GPU/CPU compute"
              style={{ color: '#f87171', border: '1px solid rgba(248,113,113,0.25)' }}>
              <Square size={9} /> Stop Worker
            </button>
          </div>
        )}
        {!modelActive && !streamRunning && (onNavigateTab ? (
          <button
            onClick={() => onNavigateTab('models')}
            className="mono text-[10px] px-2.5 py-1 rounded-lg transition-all hover:bg-slate-700/60 cursor-pointer flex items-center"
            style={{ color: '#94a3b8', background: 'var(--dvas-surface-2)', border: '1px solid var(--dvas-border)' }}
            title="Configure and enable Person Detection in Detection Models"
          >
            <Cpu size={10} className="inline mr-1 -mt-0.5 text-sky-400" />
            Enable in Detection Models
          </button>
        ) : (
          <span className="mono text-[10px] px-2.5 py-1 rounded-lg flex items-center"
            style={{ color: '#94a3b8', background: 'var(--dvas-surface-2)', border: '1px solid var(--dvas-border)' }}
            title="The person detection model is not loaded on the analytics server">
            <Cpu size={10} className="inline mr-1 -mt-0.5 text-slate-500" />
            Person model not loaded
          </span>
        ))}
        {onNavigateTab && (modelActive || streamRunning) && (
          <button
            onClick={() => onNavigateTab('models')}
            className="flex items-center gap-1 px-2.5 py-1 rounded-lg mono text-[10px] font-bold text-slate-400 hover:text-sky-300 hover:bg-slate-800 transition-colors"
            title="Open Detection Models Configuration"
            style={{ border: '1px solid var(--dvas-border)' }}
          >
            <Cpu size={10} /> Models
          </button>
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

      {/* Tactical Annotation Toggle Bar */}
      <div className="px-4 py-2 flex items-center gap-2 flex-wrap"
        style={{ borderBottom: '1px solid var(--dvas-border)', background: 'var(--dvas-surface-2)' }}>
        <div className="flex items-center gap-1.5 mr-1">
          <SlidersHorizontal size={11} className="text-sky-400" />
          <span className="mono text-[9px] font-bold text-slate-400 tracking-wider uppercase">HUD OVERLAYS</span>
        </div>
        {[
          { key: 'person_boxes',     label: 'Persons',      icon: Users,         desc: 'Person bounding boxes' },
          { key: 'face_boxes',       label: 'Faces',        icon: Eye,           desc: 'Face bounding boxes' },
          { key: 'track_ids',        label: 'Track IDs',    icon: UserCheck,     desc: 'Unique Tracker IDs' },
          { key: 'confidence',       label: 'Confidence',   icon: Zap,           desc: 'Detection & Match Confidence %' },
          { key: 'watchlist_alerts', label: 'Watchlist',    icon: Shield,        desc: 'Tactical Watchlist Crosshairs & Brackets' },
          { key: 'alert_labels',    label: 'Alert Labels', icon: AlertTriangle, desc: 'Subject Category & FIR Labels' },
          { key: 'trajectory',      label: 'Trajectories', icon: Activity,      desc: 'Movement Path Vectors' },
        ].map(({ key, label, icon: Icon, desc }) => {
          const isActive = !!annotationConfig?.[key];
          return (
            <button
              key={key}
              type="button"
              onClick={() => onToggleAnnotation && onToggleAnnotation(key)}
              title={desc}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded mono text-[9px] font-bold transition-all cursor-pointer ${
                isActive
                  ? 'bg-sky-500/20 text-sky-300 border border-sky-500/40 shadow-sm shadow-sky-500/10'
                  : 'bg-slate-900/60 text-slate-500 border border-slate-800 hover:border-slate-700 hover:text-slate-400'
              }`}
            >
              <Icon size={10} className={isActive ? 'text-sky-400' : 'text-slate-600'} />
              <span>{label}</span>
              <span className={`w-1.5 h-1.5 rounded-full ml-0.5 ${isActive ? 'bg-sky-400 shadow-sm shadow-sky-400' : 'bg-slate-700'}`} />
            </button>
          );
        })}
      </div>

      {/* Feed viewport */}
      <div className="relative bg-black flex items-center justify-center" style={{ height: 420 }}>
        {hasAnyFeed ? (
          <img src={displaySrc} alt={isAnnotated ? 'Annotated person detection feed' : 'Raw drone feed'}
            className="w-full h-full object-contain" />
        ) : hasDrone ? (
          <div className="text-center text-slate-500 flex flex-col items-center gap-3 px-4">
            <VideoOff size={42} className="opacity-40 text-slate-600" />
            <p className="mono text-sm text-slate-400 font-bold tracking-wider">FEED OFFLINE — NO ACTIVE STREAM</p>
            <p className="mono text-[10px] text-slate-500">Drone <span className="text-sky-400 font-semibold">{activeDrone}</span> is not transmitting frames</p>
          </div>
        ) : (
          <div className="text-center text-slate-500 flex flex-col items-center">
            <VideoOff size={48} className="mb-3 opacity-50 text-slate-600" />
            <p className="mono text-sm">Person analytics offline</p>
            <p className="mono text-[10px] mt-1 text-slate-600">Select a drone to view live feed</p>
          </div>
        )}


        {hasDrone && (
          <>
            <div className="absolute top-3 left-3 flex flex-col gap-1.5 z-10 pointer-events-none">
              <div className="mono text-[10px] bg-black/85 px-2 py-1 rounded text-sky-300">
                {isAnnotated ? 'Person tracking · annotated' : 'Person tracking · raw feed'}
              </div>
              <div className="mono text-[10px] bg-black/85 px-2 py-1 rounded text-teal-300">{activeDrone}</div>
              {activeMissionId && (
                <div className="mono text-[10px] bg-black/85 px-2 py-1 rounded text-violet-300">
                  MSN: {activeMissionId}
                </div>
              )}
            </div>
            <div className="absolute top-3 right-3 z-10 pointer-events-none flex items-center gap-1 mono text-[10px] bg-black/85 px-2 py-1 rounded"
              style={{ color: modelStatusColor }}>
              <span className="w-1.5 h-1.5 rounded-full dvas-live-dot" style={{ background: modelStatusColor }} />
              {isAnnotated ? `ANNOTATED${fps > 0 ? ` · ${fps}fps` : ''}` : hasAnyFeed ? 'LIVE · RAW' : 'CONNECTING'}
            </div>
            <div className="absolute bottom-3 left-3 z-10 pointer-events-none mono text-[10px] bg-black/85 px-2 py-1 rounded text-slate-400">
              Person · Face · Re-ID
            </div>
            <div className="absolute bottom-3 right-3 z-10 pointer-events-none mono text-[10px] bg-black/85 px-2 py-1 rounded"
              style={{ color: modelStatusColor }}>
              {modelStatusLabel}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 2 — Person KPI Strip
   ────────────────────────────────────────────────────────────── */
function PersonKPIStrip({ tracks, dedupData, countData, sseConnected, liveFaceCount, activeMissionId, watchlistAlertCount = 0 }) {
  const tracksArr = Array.isArray(tracks) ? tracks : [];
  const totalTracked  = countData?.active_persons ?? (tracksArr.length || 0);
  const uniquePersons = dedupData?.unique_identities || dedupData?.total_unique || 0;
  const facesDetected = liveFaceCount || dedupData?.total_faces || 0;
  const duplicates    = dedupData?.duplicates_found || 0;
  const dedupRate     = facesDetected > 0 ? Math.round((duplicates / facesDetected) * 100) : 0;

  const kpis = [
    { label: 'Persons Tracked',    value: totalTracked,                        tone: 'text-sky-400',     icon: Users,       sub: 'Active tracks (selected drone)' },
    { label: 'Unique Identities',  value: uniquePersons,                       tone: 'text-violet-400',  icon: UserCheck,   sub: 'After Re-ID deduplication' },
    { label: 'Faces Detected',     value: facesDetected,                       tone: 'text-amber-400',   icon: Fingerprint, sub: liveFaceCount ? 'Live SSE counter' : 'Total face crops stored' },
    { label: 'Watchlist Matches',  value: watchlistAlertCount,                 tone: watchlistAlertCount > 0 ? 'text-red-400 animate-pulse' : 'text-slate-400', icon: ShieldAlert, sub: watchlistAlertCount > 0 ? 'Active suspect alerts' : 'No active alerts' },
    { label: 'Duplicates Removed', value: duplicates,                          tone: 'text-emerald-400', icon: Copy,        sub: `${dedupRate}% dedup rate` },
    { label: 'Live Stream',        value: sseConnected ? 'ON' : 'OFF',         tone: sseConnected ? 'text-emerald-400' : 'text-slate-500', icon: Radio, sub: activeMissionId ? `MSN: ${activeMissionId}` : (sseConnected ? 'Real-time SSE active' : 'Polling mode') },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
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
   SECTION 3 — Active Person Tracks — Card Grid (ANPR-ledger style)
   ────────────────────────────────────────────────────────────── */
function ActivePersonTracks({ persons, loading }) {
  const [selectedId, setSelectedId] = useState(null);
  const arr = Array.isArray(persons) ? persons : [];
  const isAnySelected = selectedId !== null;

  if (loading && arr.length === 0) {
    return (
      <div className="flex items-center justify-center py-12 gap-2">
        <RefreshCw size={16} className="text-slate-500 animate-spin" />
        <span className="mono text-xs text-slate-500">Loading live persons…</span>
      </div>
    );
  }

  if (!loading && arr.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 gap-2">
        <Users size={36} className="text-slate-600 opacity-40" />
        <span className="mono text-xs text-slate-500">No active person detections</span>
        <span className="mono text-[10px] text-slate-600">Persons will appear here when the detection stream is running</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="mono text-[10px] mb-1" style={{ color: 'var(--dvas-text-dim)' }}>
        {arr.length} active · sorted by last seen
      </div>

      {/* Card Grid — 2 cols on sm, 3 on md, 4 on lg, 5 on xl */}
      <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))' }}>
        {arr.map((p) => {
          const id = `${p.drone_id}-${p.tracker_id}`;
          const isSelected = selectedId === id;
          const conf = Math.round((p.confidence || 0) * 100);
          const confCol = conf > 70 ? '#2dd4bf' : conf > 40 ? '#fbbf24' : '#f87171';
          const dur = p.first_seen && p.last_seen
            ? `${Math.max(1, Math.round((new Date(p.last_seen) - new Date(p.first_seen)) / 1000))}s`
            : '—';
          const hasPersonCrop = !!p.person_crop_b64;
          const hasFaceCrop   = !!p.face_crop_b64;

          return (
            <div
              key={id}
              onClick={() => setSelectedId(isSelected ? null : id)}
              className="flex flex-col rounded-xl overflow-hidden cursor-pointer transition-all duration-300"
              style={{
                background: 'var(--dvas-surface)',
                border: `1px solid ${isSelected ? '#38bdf8' : 'var(--dvas-border)'}`,
                transform: isSelected ? 'scale(1.04)' : 'scale(1)',
                zIndex: isSelected ? 10 : 1,
                filter: (isAnySelected && !isSelected) ? 'blur(2px) opacity(0.35) grayscale(40%)' : 'none',
                boxShadow: isSelected
                  ? '0 8px 24px -4px rgba(56,189,248,0.3)'
                  : 'var(--dvas-shadow)',
              }}>

              {/* ── Card Header ── */}
              <div className="flex items-center justify-between px-2.5 py-1.5"
                style={{ background: 'var(--dvas-surface-2)', borderBottom: '1px solid var(--dvas-border)' }}>
                <span className="font-mono font-semibold text-slate-400" style={{ fontSize: 10 }}>
                  P-{String(p.tracker_id).padStart(3, '0')}
                </span>
                <span className="font-mono font-bold px-1.5 py-0.5 rounded text-[9px]"
                  style={{ background: 'rgba(56,189,248,0.15)', color: '#38bdf8', border: '1px solid rgba(56,189,248,0.3)' }}>
                  {p.drone_id}
                </span>
              </div>

              {/* ── Person Body Crop (tall portrait: 3:4) ── */}
              <div className="relative w-full bg-black overflow-hidden" style={{ paddingTop: '133%' /* 3:4 */ }}>
                {hasPersonCrop ? (
                  <>
                    {/* Blurred background fill */}
                    <img
                      src={`data:image/jpeg;base64,${p.person_crop_b64}`}
                      alt=""
                      aria-hidden="true"
                      className="absolute inset-0 w-full h-full object-cover block"
                      style={{ filter: 'blur(14px) brightness(0.4)', transform: 'scale(1.15)' }}
                    />
                    {/* Sharp foreground — object-contain so full body is always visible */}
                    <img
                      src={`data:image/jpeg;base64,${p.person_crop_b64}`}
                      alt={`Person P-${p.tracker_id}`}
                      className="absolute inset-0 w-full h-full object-contain block"
                    />
                  </>
                ) : (
                  <div className="absolute inset-0 flex items-center justify-center">
                    <Users size={32} className="text-slate-700 opacity-35" />
                  </div>
                )}

                {/* Confidence badge */}
                <div className="absolute bottom-1.5 left-1.5 z-10">
                  <span className="font-mono font-bold px-1 py-0.5 rounded text-[9px]"
                    style={{ background: 'rgba(0,0,0,0.72)', color: confCol }}>
                    {conf}%
                  </span>
                </div>
                {/* Duration */}
                <div className="absolute bottom-1.5 right-1.5 z-10">
                  <span className="font-mono px-1 py-0.5 rounded text-[9px]"
                    style={{ background: 'rgba(0,0,0,0.65)', color: '#94a3b8' }}>
                    {dur}
                  </span>
                </div>
                {/* PERSON CROP label */}
                <div className="absolute top-1.5 left-1.5 z-10">
                  <span className="font-mono font-bold uppercase px-1 py-0.5 rounded"
                    style={{ fontSize: 8, background: 'rgba(0,0,0,0.65)', color: '#64748b', letterSpacing: '0.06em' }}>
                    PERSON
                  </span>
                </div>
              </div>

              {/* ── Face Crop (square 1:1) ── */}
              <div className="w-full flex flex-col"
                style={{ background: 'var(--dvas-surface)', borderTop: '1px solid var(--dvas-border)', padding: '6px 8px' }}>
                <div className="w-full mb-1.5">
                  <span className="font-mono font-bold uppercase text-slate-500" style={{ fontSize: 8, letterSpacing: '0.08em' }}>
                    FACE CROP
                  </span>
                </div>
                {/* 1:1 square box */}
                <div className="relative w-full rounded-lg overflow-hidden border border-slate-700/70 bg-black" style={{ paddingTop: '100%' }}>
                  {hasFaceCrop ? (
                    <>
                      <img
                        src={`data:image/jpeg;base64,${p.face_crop_b64}`}
                        alt=""
                        aria-hidden="true"
                        className="absolute inset-0 w-full h-full object-cover block"
                        style={{ filter: 'blur(10px) brightness(0.4)', transform: 'scale(1.15)' }}
                      />
                      <img
                        src={`data:image/jpeg;base64,${p.face_crop_b64}`}
                        alt={`Face of P-${p.tracker_id}`}
                        className="absolute inset-0 w-full h-full object-contain block"
                      />
                    </>
                  ) : (
                    <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 font-mono text-slate-600" style={{ fontSize: 8 }}>
                      <Fingerprint size={20} className="opacity-30" />
                      <span>No face</span>
                    </div>
                  )}
                </div>
              </div>

              {/* ── Card Footer ── */}
              <div className="flex items-center justify-between px-2.5 py-1.5"
                style={{ background: 'var(--dvas-surface-2)', borderTop: '1px solid var(--dvas-border)' }}>
                <div className="flex items-center gap-1">
                  <Clock size={9} className="text-slate-500 opacity-60" />
                  <span className="font-mono text-[9px] text-slate-500">{fmtAge(p.last_seen)}</span>
                </div>
                <span className="font-mono text-[9px] text-slate-600">{p.frame_count} frames</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 4 — Detection Chart (SSE rolling window)
   ────────────────────────────────────────────────────────────── */
function DetectionChart({ sseWindow, sseConnected }) {
  const data = sseWindow.length > 0
    ? sseWindow
    : Array.from({ length: 20 }, (_, i) => ({ t: String(i), count: 0, faces: 0 }));

  const latest = sseWindow.length > 0 ? sseWindow[sseWindow.length - 1] : null;
  const currentPersons = latest?.count ?? 0;
  const currentFaces   = latest?.faces ?? 0;

  return (
    <div className="relative">
      {sseWindow.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center z-10 pointer-events-none">
          <span className="mono text-xs text-slate-500 flex items-center gap-1.5">
            <Radio size={12} className={sseConnected ? "text-teal-400 animate-pulse" : "text-slate-500"} />
            {sseConnected ? "Listening for real-time person detections…" : "Connecting to detection stream…"}
          </span>
        </div>
      )}
      <div className="flex items-center justify-between mb-2 px-1">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-sm" style={{ background: '#2dd4bf' }} />
            <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-muted)' }}>Persons</span>
            <span className="mono text-xs font-bold text-teal-400">{currentPersons}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-sm" style={{ background: '#fbbf24' }} />
            <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-muted)' }}>Faces</span>
            <span className="mono text-xs font-bold text-amber-400">{currentFaces}</span>
          </div>
        </div>
        <span className={`mono text-[9px] flex items-center gap-1 ${sseConnected ? 'text-teal-400' : 'text-slate-500'}`}>
          <span className={`w-1.5 h-1.5 rounded-full ${sseConnected ? 'bg-teal-400 dvas-live-dot' : 'bg-slate-500'}`} />
          {sseWindow.length > 0 ? `${sseWindow.length}s window` : (sseConnected ? 'Live' : 'Offline')}
        </span>
      </div>
      <ResponsiveContainer width="100%" height={180}>
        <AreaChart data={data} margin={{ top: 8, right: 8, left: -24, bottom: 0 }}>
          <defs>
            <linearGradient id="gPersonTeal" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#2dd4bf" stopOpacity={0.45} />
              <stop offset="100%" stopColor="#2dd4bf" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="gFaceAmber" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#fbbf24" stopOpacity={0.35} />
              <stop offset="100%" stopColor="#fbbf24" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--dvas-border)" strokeDasharray="3 3" />
          <XAxis dataKey="t" stroke="var(--dvas-text-muted)" fontSize={9} tick={false} />
          <YAxis stroke="var(--dvas-text-muted)" fontSize={9} allowDecimals={false} />
          <Tooltip {...CHART_TOOLTIP} />
          <Area type="monotone" dataKey="count" stroke="#2dd4bf" fill="url(#gPersonTeal)"
            strokeWidth={2} name="Persons" dot={false} isAnimationActive={false} />
          <Area type="monotone" dataKey="faces" stroke="#fbbf24" fill="url(#gFaceAmber)"
            strokeWidth={1.5} name="Faces" dot={false} isAnimationActive={false} strokeDasharray="4 2" />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 5a — Searchable Mission Filter Bar
   ────────────────────────────────────────────────────────────── */
function formatMissionDate(missionId, rawTs) {
  if (rawTs) return fmtIST(rawTs);
  if (!missionId) return '—';
  const match = missionId.match(/MSN-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/);
  if (match) {
    const [, y, m, d, hh, mm] = match;
    try {
      const dt = new Date(`${y}-${m}-${d}T${hh}:${mm}:00Z`);
      return fmtIST(dt.toISOString());
    } catch {
      return `${y}-${m}-${d} ${hh}:${mm}`;
    }
  }
  return '—';
}

function SearchableMissionFilter({ missions, selected, onSelect, activeMissionId, type = 'person' }) {
  const [isOpen, setIsOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const dropdownRef = useRef(null);

  const missionList = Array.isArray(missions) ? missions : [];

  // Close dropdown on outside click
  useEffect(() => {
    function handleClickOutside(e) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
        setIsOpen(false);
      }
    }
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isOpen]);

  const filteredMissions = missionList.filter(m => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase().trim();
    const idMatch = (m.mission_id || '').toLowerCase().includes(q);
    const dateRawMatch = (m.first_seen || m.last_seen || '').toLowerCase().includes(q);
    const dateParsed = formatMissionDate(m.mission_id, m.first_seen || m.last_seen).toLowerCase();
    return idMatch || dateRawMatch || dateParsed.includes(q);
  });

  const hasCurrentMission = activeMissionId && activeMissionId !== selected;
  const isPerson = type === 'person';
  const accentColor = isPerson ? '#38bdf8' : '#fbbf24';
  const accentBg = isPerson ? 'rgba(56,189,248,0.1)' : 'rgba(251,191,36,0.1)';
  const accentBorder = isPerson ? 'rgba(56,189,248,0.3)' : 'rgba(251,191,36,0.3)';

  return (
    <div className="flex items-center gap-2 flex-wrap mb-3">
      {/* Searchable Combobox Input */}
      <div className="relative" ref={dropdownRef} style={{ minWidth: 260 }}>
        <div
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg cursor-pointer transition-all"
          style={{
            background: 'var(--dvas-surface-2)',
            border: `1px solid ${isOpen ? accentColor : 'var(--dvas-border)'}`,
            color: 'var(--dvas-text)',
          }}
          onClick={() => setIsOpen(!isOpen)}
        >
          <Search size={13} style={{ color: isOpen ? accentColor : 'var(--dvas-text-dim)' }} />
          <input
            type="text"
            className="bg-transparent border-none outline-none mono text-[11px] w-full"
            style={{ color: 'var(--dvas-text)' }}
            placeholder={selected ? selected : "Search / Select Mission..."}
            value={isOpen ? searchQuery : (selected ? selected : '')}
            onChange={(e) => {
              setSearchQuery(e.target.value);
              if (!isOpen) setIsOpen(true);
            }}
            onFocus={() => {
              setIsOpen(true);
              setSearchQuery('');
            }}
            onClick={(e) => e.stopPropagation()}
          />
          {selected ? (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onSelect('');
                setSearchQuery('');
                setIsOpen(false);
              }}
              className="hover:text-red-400 p-0.5 text-slate-400"
              title="Clear Mission Filter"
            >
              <X size={12} />
            </button>
          ) : (
            <span className="mono text-[9px] text-slate-500 select-none">▼</span>
          )}
        </div>

        {/* Dropdown Menu */}
        {isOpen && (
          <div
            className="absolute top-full left-0 mt-1 w-80 max-h-64 overflow-y-auto rounded-lg shadow-2xl z-50 py-1"
            style={{
              background: 'var(--dvas-surface)',
              border: '1px solid var(--dvas-border)',
              boxShadow: '0 10px 30px -5px rgba(0,0,0,0.8)',
            }}
          >
            {/* Option: All Missions */}
            <div
              className="px-3 py-2 cursor-pointer transition-colors flex items-center justify-between border-b hover:bg-slate-800/60"
              style={{
                borderBottomColor: 'var(--dvas-border)',
                background: !selected ? accentBg : 'transparent',
              }}
              onClick={() => {
                onSelect('');
                setIsOpen(false);
              }}
            >
              <span className="mono text-[11px] font-semibold" style={{ color: !selected ? accentColor : 'var(--dvas-text)' }}>
                All Missions (Entire History)
              </span>
              <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>
                {missionList.length} missions
              </span>
            </div>

            {filteredMissions.length === 0 ? (
              <div className="p-3 mono text-xs text-slate-500 text-center">
                No matching missions found
              </div>
            ) : (
              filteredMissions.map((m) => {
                const isItemActive = selected === m.mission_id;
                const count = isPerson ? (m.total_persons ?? 0) : (m.total_faces ?? 0);
                const countLabel = isPerson ? `${count} persons` : `${count} faces`;
                const dateDisplay = formatMissionDate(m.mission_id, m.first_seen || m.last_seen);

                return (
                  <div
                    key={m.mission_id}
                    className="px-3 py-2 cursor-pointer transition-colors border-b last:border-b-0 hover:bg-slate-800/60"
                    style={{
                      borderBottomColor: 'rgba(51,65,85,0.3)',
                      background: isItemActive ? accentBg : 'transparent',
                    }}
                    onClick={() => {
                      onSelect(m.mission_id);
                      setIsOpen(false);
                    }}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span
                        className="mono text-[11px] font-bold truncate"
                        style={{ color: isItemActive ? accentColor : 'var(--dvas-text)' }}
                      >
                        {m.mission_id}
                      </span>
                      <span
                        className="mono text-[9px] font-semibold px-1.5 py-0.5 rounded flex-shrink-0"
                        style={{
                          background: count > 0 ? (isPerson ? 'rgba(56,189,248,0.15)' : 'rgba(251,191,36,0.15)') : 'rgba(100,116,139,0.15)',
                          color: count > 0 ? accentColor : '#64748b',
                          border: `1px solid ${count > 0 ? accentBorder : 'rgba(100,116,139,0.2)'}`,
                        }}
                      >
                        {countLabel}
                      </span>
                    </div>
                    <div className="flex items-center justify-between text-[10px] text-slate-400 mt-0.5">
                      <span className="mono flex items-center gap-1">
                        <Calendar size={10} className="text-slate-500" />
                        {dateDisplay}
                      </span>
                      {m.is_deduplicated && (
                        <span className="mono text-[8px] text-emerald-400">deduped</span>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        )}
      </div>

      {/* Quick Action: Current Live Mission */}
      {hasCurrentMission && (
        <button
          onClick={() => onSelect(activeMissionId)}
          className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg mono text-[10px] font-semibold transition-all shadow-sm"
          style={{
            background: 'rgba(45,212,191,0.12)',
            color: '#2dd4bf',
            border: '1px solid rgba(45,212,191,0.3)',
          }}
          title={`Filter to current mission: ${activeMissionId}`}
        >
          <Radio size={10} className="animate-pulse" /> Current Mission
        </button>
      )}

      {/* Active Filter Pill with Clear */}
      {selected && (
        <div
          className="flex items-center gap-1.5 px-2 py-1 rounded-lg mono text-[10px] font-semibold"
          style={{
            background: accentBg,
            color: accentColor,
            border: `1px solid ${accentBorder}`,
          }}
        >
          <span className="truncate max-w-[160px]">{selected}</span>
          <button
            onClick={() => onSelect('')}
            className="hover:text-red-400 transition-colors p-0.5 rounded"
            title="Clear mission filter"
          >
            <X size={11} />
          </button>
        </div>
      )}
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 5b — Gallery Card
   ────────────────────────────────────────────────────────────── */
function GalleryCard({ item, i, onOpenModal, isPersonGallery, bucket }) {
  const [imgError, setImgError] = useState(false);
  const itemId = item.identity_id || item.id || i;

  const personObjKey = item.best_person_image_key;
  const faceObjKey   = item.best_image_key || item.object_key;
  const faceRawUrl   = item.best_image_url || item.image_url || item.presigned_url || item.url;
  const personRawUrl = item.best_person_image_url;

  let imgUrl;
  if (isPersonGallery) {
    imgUrl = personObjKey
      ? toProxiedUrl(personObjKey, 'dvas-faces')
      : personRawUrl
      ? toProxiedUrl(personRawUrl, 'dvas-faces')
      : null;
  } else {
    imgUrl = faceObjKey
      ? toProxiedUrl(faceObjKey, bucket)
      : toProxiedUrl(faceRawUrl, bucket);
  }

  const clusterId = item.cluster_id != null ? `CL-${item.cluster_id}` : `ID-${i + 1}`;
  const drone     = item.drone_id || '';
  const msnId     = item.mission_id || '';
  const lastSeen  = item.last_seen || item.created_at || item.ts;
  const count     = item.embedding_count || 1;
  const accentColor = isPersonGallery ? '#38bdf8' : '#fbbf24';

  return (
    <div
      key={itemId}
      onClick={() => onOpenModal(item)}
      className="group relative flex flex-col rounded-xl overflow-hidden cursor-pointer transition-all duration-200 hover:-translate-y-1 hover:shadow-lg"
      style={{
        background: 'var(--dvas-surface)',
        border: '1px solid var(--dvas-border)',
        boxShadow: 'var(--dvas-shadow)',
      }}>
      {/* Header */}
      <div className="flex items-center justify-between px-2 py-1"
        style={{ background: 'var(--dvas-surface-2)', borderBottom: '1px solid var(--dvas-border)' }}>
        <span className="font-mono font-bold text-[9px]" style={{ color: accentColor }}>
          {clusterId}
        </span>
        <div className="flex items-center gap-1">
          {count > 1 && (
            <span className="font-mono font-bold text-[8px] px-1 py-0.5 rounded"
              style={{ background: 'rgba(167,139,250,0.15)', color: '#a78bfa', border: '1px solid rgba(167,139,250,0.2)' }}>
              ×{count}
            </span>
          )}
          <Maximize2 size={10} className="text-slate-400 opacity-0 group-hover:opacity-100 transition-opacity" />
        </div>
      </div>

      {/* Image (blurred bg + sharp fg) */}
      <div className="relative w-full bg-black overflow-hidden"
        style={{ paddingTop: isPersonGallery ? '133%' : '100%' }}>
        {imgUrl && !imgError ? (
          <>
            <img src={imgUrl} alt="" aria-hidden="true"
              className="absolute inset-0 w-full h-full object-cover block"
              style={{ filter: 'blur(14px) brightness(0.4)', transform: 'scale(1.15)' }}
              onError={() => setImgError(true)} />
            <img src={imgUrl} alt={clusterId}
              className="absolute inset-0 w-full h-full object-contain block transition-transform duration-200 group-hover:scale-105"
              onError={() => setImgError(true)} />
          </>
        ) : (
          <div className="absolute inset-0 flex items-center justify-center">
            {isPersonGallery
              ? <Users size={28} className="text-slate-700 opacity-35" />
              : <Fingerprint size={28} className="text-slate-700 opacity-35" />
            }
          </div>
        )}
      </div>

      {/* Caption */}
      <div className="px-2 py-1.5 flex flex-col gap-0.5" style={{ background: 'var(--dvas-surface)' }}>
        {drone   && <span className="mono text-[9px] font-semibold truncate" style={{ color: '#94a3b8' }}>{drone}</span>}
        {msnId   && <span className="mono text-[8px] truncate" style={{ color: '#64748b' }}>{msnId}</span>}
        <div className="flex items-center justify-between mt-0.5">
          <span className="mono text-[9px]" style={{ color: '#475569' }}>{fmtAge(lastSeen)}</span>
          <span className="mono text-[8px] text-slate-500 opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-0.5">
            <Maximize2 size={8} /> zoom
          </span>
        </div>
      </div>
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 5c — Full-Resolution Preview Modal
   ────────────────────────────────────────────────────────────── */
function GalleryPreviewModal({ item, isPersonGallery, bucket, onClose }) {
  const [copiedKey, setCopiedKey] = useState(false);
  const [copiedMsn, setCopiedMsn] = useState(false);

  // Close on ESC
  useEffect(() => {
    function handleKeyDown(e) {
      if (e.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  if (!item) return null;

  const personObjKey = item.best_person_image_key;
  const faceObjKey   = item.best_image_key || item.object_key;
  const faceRawUrl   = item.best_image_url || item.image_url || item.presigned_url || item.url;
  const personRawUrl = item.best_person_image_url;

  let imgUrl;
  let objectKey;
  if (isPersonGallery) {
    objectKey = personObjKey;
    imgUrl = personObjKey
      ? toProxiedUrl(personObjKey, 'dvas-faces')
      : personRawUrl
      ? toProxiedUrl(personRawUrl, 'dvas-faces')
      : null;
  } else {
    objectKey = faceObjKey;
    imgUrl = faceObjKey
      ? toProxiedUrl(faceObjKey, bucket)
      : toProxiedUrl(faceRawUrl, bucket);
  }

  const clusterLabel = item.name
    ? `${item.name}${item.category ? ` · [${item.category.toUpperCase()}]` : ''}`
    : (item.cluster_id != null ? `Cluster #${item.cluster_id}` : `Identity #${item.identity_id || item.id || '—'}`);
  const drone = item.drone_id || '—';
  const msnId = item.case_ref || item.mission_id || '—';
  const timestamp = item.last_seen || item.created_at || item.ts;
  const count = item.embedding_count || 1;
  const accentColor = item.name ? '#fbbf24' : isPersonGallery ? '#38bdf8' : '#fbbf24';

  const copyToClipboard = (text, type) => {
    navigator.clipboard.writeText(text);
    if (type === 'key') {
      setCopiedKey(true);
      setTimeout(() => setCopiedKey(false), 2000);
    } else {
      setCopiedMsn(true);
      setTimeout(() => setCopiedMsn(false), 2000);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="relative w-full max-w-xl rounded-2xl overflow-hidden shadow-2xl flex flex-col"
        style={{
          background: 'var(--dvas-surface)',
          border: `1px solid var(--dvas-border)`,
          maxHeight: '90vh',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Modal Header */}
        <div
          className="flex items-center justify-between px-4 py-3 border-b"
          style={{
            background: 'var(--dvas-surface-2)',
            borderBottomColor: 'var(--dvas-border)',
          }}
        >
          <div className="flex items-center gap-2">
            {item.name ? <Shield size={18} className="text-amber-400" /> : isPersonGallery ? <Users size={18} style={{ color: accentColor }} /> : <Fingerprint size={18} style={{ color: accentColor }} />}
            <span className="mono font-bold text-sm" style={{ color: accentColor }}>
              {clusterLabel}
            </span>
            {count > 1 && (
              <span
                className="font-mono font-bold text-xs px-2 py-0.5 rounded"
                style={{ background: 'rgba(167,139,250,0.18)', color: '#a78bfa', border: '1px solid rgba(167,139,250,0.3)' }}
              >
                ×{count} sightings
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg hover:bg-slate-700/50 text-slate-400 hover:text-white transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Modal Body: Image Preview */}
        <div className="relative bg-black flex items-center justify-center p-2 min-h-[300px] max-h-[55vh] overflow-hidden">
          {imgUrl ? (
            <img
              src={imgUrl}
              alt={clusterLabel}
              className="max-h-[50vh] w-auto max-w-full object-contain rounded"
            />
          ) : (
            <div className="flex flex-col items-center justify-center py-16 text-slate-600 gap-2">
              <VideoOff size={32} />
              <span className="mono text-xs">No image available</span>
            </div>
          )}
        </div>

        {/* Modal Footer / Metadata */}
        <div className="p-4 flex flex-col gap-2.5 overflow-y-auto" style={{ background: 'var(--dvas-surface)' }}>
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div>
              <span className="mono text-[10px] text-slate-400 block mb-0.5">Mission ID</span>
              <div className="flex items-center gap-1.5">
                <span className="mono font-semibold text-[11px] truncate text-slate-200" title={msnId}>
                  {msnId}
                </span>
                {msnId !== '—' && (
                  <button
                    onClick={() => copyToClipboard(msnId, 'msn')}
                    className="text-slate-500 hover:text-slate-300 transition-colors"
                    title="Copy Mission ID"
                  >
                    <Copy size={11} />
                  </button>
                )}
                {copiedMsn && <span className="mono text-[9px] text-emerald-400">Copied!</span>}
              </div>
            </div>

            <div>
              <span className="mono text-[10px] text-slate-400 block mb-0.5">Drone ID</span>
              <span className="mono font-semibold text-[11px] text-slate-200">{drone}</span>
            </div>

            <div>
              <span className="mono text-[10px] text-slate-400 block mb-0.5">Recorded Time (IST)</span>
              <span className="mono text-[11px] text-slate-200">{fmtIST(timestamp)}</span>
              <span className="mono text-[9px] text-slate-500 ml-1">({fmtAge(timestamp)})</span>
            </div>

            <div>
              <span className="mono text-[10px] text-slate-400 block mb-0.5">MinIO Storage</span>
              <div className="flex items-center gap-1.5">
                <span className="mono text-[10px] truncate text-slate-300 max-w-[150px]" title={objectKey || '—'}>
                  {objectKey ? objectKey.split('/').pop() : '—'}
                </span>
                {objectKey && (
                  <button
                    onClick={() => copyToClipboard(objectKey, 'key')}
                    className="text-slate-500 hover:text-slate-300 transition-colors"
                    title="Copy object key"
                  >
                    <Copy size={11} />
                  </button>
                )}
                {copiedKey && <span className="mono text-[9px] text-emerald-400">Copied!</span>}
              </div>
            </div>
          </div>

          {/* Action Row */}
          <div className="flex items-center justify-between pt-2 border-t border-slate-800">
            <span className="mono text-[10px] text-slate-500">
              {isPersonGallery ? 'Whole-body person crop' : 'Face detection crop'} · MinIO
            </span>
            {imgUrl && (
              <a
                href={imgUrl}
                target="_blank"
                rel="noreferrer"
                download
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg mono text-xs font-semibold transition-all hover:brightness-110"
                style={{
                  background: isPersonGallery ? 'rgba(56,189,248,0.2)' : 'rgba(251,191,36,0.2)',
                  color: accentColor,
                  border: `1px solid ${isPersonGallery ? 'rgba(56,189,248,0.4)' : 'rgba(251,191,36,0.4)'}`,
                }}
              >
                <Download size={12} /> Open / Download High-Res
              </a>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 5d — Image Gallery wrapper
   ────────────────────────────────────────────────────────────── */
function ImageGallery({ items, loading, label = 'Items', bucket = 'dvas-faces', isPersonGallery = false, selectedMission = '' }) {
  const [page, setPage] = useState(0);
  const [searchTerm, setSearchTerm] = useState('');
  const [previewItem, setPreviewItem] = useState(null);
  const perPage = 12;

  const rawArr = Array.isArray(items) ? items : [];

  // Reset page to 0 when items length, selected mission, or search term changes
  useEffect(() => {
    setPage(0);
  }, [rawArr.length, selectedMission, searchTerm]);

  // Client-side search within loaded items
  const filtered = rawArr.filter((item, idx) => {
    if (!searchTerm) return true;
    const q = searchTerm.toLowerCase().trim();
    const cluster = item.cluster_id != null ? `cl-${item.cluster_id} ${item.cluster_id}` : `id-${idx + 1}`;
    const drone = (item.drone_id || '').toLowerCase();
    const msn = (item.mission_id || '').toLowerCase();
    return cluster.toLowerCase().includes(q) || drone.includes(q) || msn.includes(q);
  });

  const pages = Math.ceil(filtered.length / perPage);
  const shown = filtered.slice(page * perPage, page * perPage + perPage);

  if (loading && rawArr.length === 0) {
    return (
      <div className="flex items-center justify-center py-12 gap-2">
        <RefreshCw size={16} className="text-slate-500 animate-spin" />
        <span className="mono text-xs text-slate-500">Loading {label.toLowerCase()}…</span>
      </div>
    );
  }

  if (!loading && rawArr.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 gap-3">
        <Eye size={36} className="text-slate-600 opacity-40" />
        <span className="mono text-xs text-slate-500">No {label.toLowerCase()} found</span>
        <span className="mono text-[10px] text-slate-600">
          {selectedMission
            ? `No detections recorded for mission ${selectedMission}`
            : 'Images stored in MinIO will appear here after detection'}
        </span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {/* Sub-bar: In-Gallery Search & Counts */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="relative flex-1" style={{ maxWidth: 260 }}>
          <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            type="text"
            placeholder={`Filter ${label.toLowerCase()} (cluster / drone)...`}
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full pl-7 pr-7 py-1 rounded-lg mono text-[11px] outline-none transition-all"
            style={{
              background: 'var(--dvas-surface-2)',
              border: '1px solid var(--dvas-border)',
              color: 'var(--dvas-text)',
            }}
          />
          {searchTerm && (
            <button
              onClick={() => setSearchTerm('')}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
            >
              <X size={12} />
            </button>
          )}
        </div>

        <div className="flex items-center gap-2">
          <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>
            Showing {filtered.length} of {rawArr.length} {isPersonGallery ? 'identities' : 'faces'}
          </span>
        </div>
      </div>

      {/* Grid of Cards */}
      {filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-8 gap-2">
          <Search size={24} className="text-slate-600 opacity-40" />
          <span className="mono text-xs text-slate-500">No matches for "{searchTerm}"</span>
        </div>
      ) : (
        <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))' }}>
          {shown.map((item, i) => {
            const itemId = item.identity_id || item.id || i;
            return (
              <GalleryCard
                key={itemId}
                item={item}
                i={page * perPage + i}
                onOpenModal={setPreviewItem}
                isPersonGallery={isPersonGallery}
                bucket={bucket}
              />
            );
          })}
        </div>
      )}

      {/* Pagination */}
      {pages > 1 && (
        <div className="flex items-center justify-between pt-1">
          <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>
            {page * perPage + 1}–{Math.min((page + 1) * perPage, filtered.length)} of {filtered.length}
          </span>
          <div className="flex gap-1 items-center">
            <button onClick={() => setPage(p => Math.max(0, p - 1))} disabled={page === 0}
              className="w-6 h-6 rounded flex items-center justify-center transition-colors disabled:opacity-30"
              style={{ background: 'var(--dvas-surface-2)', border: '1px solid var(--dvas-border)', color: 'var(--dvas-text-muted)' }}>
              <ChevronLeft size={12} />
            </button>
            {Array.from({ length: Math.min(pages, 8) }).map((_, i) => (
              <button key={i} onClick={() => setPage(i)}
                className="w-6 h-6 rounded text-[10px] mono font-bold transition-colors"
                style={{
                  background: i === page ? (isPersonGallery ? 'rgba(56,189,248,0.15)' : 'rgba(251,191,36,0.15)') : 'var(--dvas-surface-2)',
                  color: i === page ? (isPersonGallery ? '#38bdf8' : '#fbbf24') : 'var(--dvas-text-muted)',
                  border: `1px solid ${i === page ? (isPersonGallery ? 'rgba(56,189,248,0.3)' : 'rgba(251,191,36,0.3)') : 'var(--dvas-border)'}`,
                }}>{i + 1}</button>
            ))}
            <button onClick={() => setPage(p => Math.min(pages - 1, p + 1))} disabled={page >= pages - 1}
              className="w-6 h-6 rounded flex items-center justify-center transition-colors disabled:opacity-30"
              style={{ background: 'var(--dvas-surface-2)', border: '1px solid var(--dvas-border)', color: 'var(--dvas-text-muted)' }}>
              <ChevronRight size={12} />
            </button>
          </div>
        </div>
      )}

      {/* Full-resolution preview modal */}
      {previewItem && (
        <GalleryPreviewModal
          item={previewItem}
          isPersonGallery={isPersonGallery}
          bucket={bucket}
          onClose={() => setPreviewItem(null)}
        />
      )}
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 6 — Mission Deduplication Panel
   ────────────────────────────────────────────────────────────── */
function DeduplicationSection({ missions, dedupProgress, onDeduplicate, dedupData, onReidTrigger, reidLoading }) {
  const missionList = Array.isArray(missions) ? missions : [];
  const progress = dedupProgress || { status: 'idle', progress_pct: 0 };
  const isRunning  = progress.status === 'running';
  const isComplete = progress.status === 'complete';
  const isError    = progress.status === 'error';

  const totalPersons = dedupData?.total_persons || dedupData?.total_faces || 0;
  const uniqueIds    = dedupData?.unique_identities || dedupData?.total_unique || 0;
  const duplicates   = dedupData?.duplicates_found  || 0;
  const dedupRate    = totalPersons > 0 ? Math.round((duplicates / totalPersons) * 100) : 0;

  return (
    <div className="flex flex-col gap-4">

      {/* Stats row */}
      <div className="grid grid-cols-4 gap-3">
        {[
          { label: 'Total Sightings', value: totalPersons, tone: '#fbbf24' },
          { label: 'Unique Persons',  value: uniqueIds,    tone: '#38bdf8' },
          { label: 'Duplicates',      value: duplicates,   tone: '#34d399' },
          { label: 'Dedup Rate',      value: `${dedupRate}%`, tone: '#a78bfa' },
        ].map(({ label, value, tone }) => (
          <div key={label} className="rounded-lg p-3 text-center"
            style={{ background: 'var(--dvas-surface-2)', border: '1px solid var(--dvas-border)' }}>
            <div className="mono font-bold text-xl" style={{ color: tone }}>{value}</div>
            <div className="mono text-[10px] mt-0.5" style={{ color: 'var(--dvas-text-dim)' }}>{label}</div>
          </div>
        ))}
      </div>

      {/* Progress bar — only when active */}
      {(isRunning || isComplete || isError) && (
        <div className="rounded-lg p-3"
          style={{ background: 'var(--dvas-surface-2)', border: '1px solid var(--dvas-border)' }}>
          <div className="flex items-center justify-between mb-2">
            <span className="mono text-xs font-semibold" style={{ color: 'var(--dvas-text)' }}>
              {isRunning  ? `Deduplicating ${progress.mission_id || ''}…`
               : isComplete ? '✓ Deduplication complete'
               : '✗ Deduplication failed'}
            </span>
            <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>
              {progress.progress_pct}%
            </span>
          </div>
          <div className="h-2 rounded-full overflow-hidden" style={{ background: 'var(--dvas-elev)' }}>
            <div className="h-full rounded-full transition-all duration-500"
              style={{
                width: `${progress.progress_pct}%`,
                background: isError ? '#f87171' : isComplete ? '#2dd4bf' : '#818cf8',
              }} />
          </div>
          <div className="mono text-[10px] mt-1.5" style={{ color: 'var(--dvas-text-dim)' }}>
            {isRunning  && `${progress.processed || 0} / ${progress.total_embeddings || 0} embeddings`}
            {isComplete && `${progress.unique_found || 0} unique persons from ${progress.total_embeddings || 0} embeddings`}
            {isError    && (progress.error || 'Unknown error')}
          </div>
        </div>
      )}

      {/* Mission list */}
      <div>
        <div className="dvas-label mb-2" style={{ fontSize: 9 }}>MISSIONS WITH PERSON TRACK DATA</div>
        {missionList.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 gap-2">
            <Database size={28} className="text-slate-600 opacity-40" />
            <span className="mono text-xs text-slate-500">No missions with person track data</span>
            <span className="mono text-[10px] text-slate-600">Person tracks will appear after person detection runs on drone feeds</span>
          </div>
        ) : (
          <div className="flex flex-col gap-2" style={{ maxHeight: 320, overflowY: 'auto' }}>
            {missionList.map((m) => {
              const isThisRunning = isRunning && progress.mission_id === m.mission_id;
              const missionDeduped = m.is_deduplicated || m.dedup_status === 'complete';
              return (
                <div key={m.mission_id}
                  className="rounded-xl px-3 py-2.5 flex items-center justify-between gap-3"
                  style={{
                    background: 'var(--dvas-surface-2)',
                    border: `1px solid ${isThisRunning ? 'rgba(129,140,248,0.4)' : 'var(--dvas-border)'}`,
                  }}>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="mono text-[11px] font-bold truncate" style={{ color: 'var(--dvas-text)' }}>
                        {m.mission_id}
                      </span>
                      {missionDeduped ? (
                        <span className="mono text-[9px] px-1.5 py-0.5 rounded flex items-center gap-1"
                          style={{ background: 'rgba(45,212,191,0.12)', color: '#2dd4bf', border: '1px solid rgba(45,212,191,0.25)' }}>
                          <CheckCircle size={9} /> Deduped
                        </span>
                      ) : (
                        <span className="mono text-[9px] px-1.5 py-0.5 rounded"
                          style={{ background: 'rgba(251,191,36,0.1)', color: '#fbbf24', border: '1px solid rgba(251,191,36,0.2)' }}>
                          Pending
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-3 mt-1">
                      <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>
                        {m.total_faces} faces
                      </span>
                      {m.duplicates > 0 && (
                        <span className="mono text-[10px] text-emerald-400/70">
                          {m.duplicates} duplicates removed
                        </span>
                      )}
                      {m.unique_found != null && (
                        <span className="mono text-[10px] text-sky-400/70">
                          {m.unique_found} unique
                        </span>
                      )}
                    </div>
                    <div className="mono text-[9px] mt-0.5" style={{ color: 'var(--dvas-text-dim)', opacity: 0.6 }}>
                      Last seen: {fmtAge(m.last_seen)}
                    </div>
                  </div>

                  {/* Action: deduplicate button or already deduped */}
                  {missionDeduped ? (
                    <div className="flex items-center gap-1 shrink-0">
                      <CheckCircle size={12} className="text-teal-400" />
                      <span className="mono text-[9px] text-teal-400/70">Done</span>
                    </div>
                  ) : (
                    <button
                      onClick={() => onDeduplicate(m.mission_id)}
                      disabled={isRunning}
                      className="shrink-0 flex items-center gap-1 px-2.5 py-1.5 rounded-lg mono text-[10px] font-semibold transition-all"
                      style={{
                        background: isThisRunning ? 'rgba(129,140,248,0.05)' : 'rgba(129,140,248,0.12)',
                        color: '#818cf8',
                        border: '1px solid rgba(129,140,248,0.3)',
                        opacity: isRunning ? 0.5 : 1,
                        cursor: isRunning ? 'not-allowed' : 'pointer',
                      }}>
                      {isThisRunning
                        ? <><RefreshCw size={10} className="animate-spin" /> Running…</>
                        : <><Fingerprint size={10} /> Deduplicate</>
                      }
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Global Re-ID trigger */}
      <div className="flex items-center justify-between pt-3" style={{ borderTop: '1px solid var(--dvas-border)' }}>
        <div>
          <div className="mono text-xs font-semibold" style={{ color: 'var(--dvas-text)' }}>
            Global Re-ID Clustering
          </div>
          <div className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>
            Cross-mission clustering across all stored embeddings
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
          {reidLoading
            ? <><RefreshCw size={11} className="animate-spin" /> Running…</>
            : <><Zap size={11} /> Trigger Global Re-ID</>
          }
        </button>
      </div>
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 6 — Face Recognition Watchlist & Reverse Search Components
   ────────────────────────────────────────────────────────────── */

function WatchlistAlertCard({ alert, onConfirm, onDismiss, onOpenPreview, canReview = true, busy = false }) {
  const categoryColors = {
    wanted:  { bg: 'rgba(239,68,68,0.12)', border: 'rgba(239,68,68,0.35)', text: '#ef4444', badge: 'bg-red-500/20 text-red-300 border-red-500/30' },
    missing: { bg: 'rgba(251,146,60,0.12)', border: 'rgba(251,146,60,0.35)', text: '#fb923c', badge: 'bg-orange-500/20 text-orange-300 border-orange-500/30' },
    vip:     { bg: 'rgba(34,197,94,0.12)',  border: 'rgba(34,197,94,0.35)',  text: '#22c55e', badge: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30' },
    poi:     { bg: 'rgba(168,85,247,0.12)', border: 'rgba(168,85,247,0.35)', text: '#a855f7', badge: 'bg-purple-500/20 text-purple-300 border-purple-500/30' },
  };
  const cat = (alert.category || 'wanted').toLowerCase();
  const colors = categoryColors[cat] || categoryColors.wanted;
  const matchPct = alert.confidence != null ? Math.round(alert.confidence * 100) : null;
  // Only claim evidence when the stored alert row carries the Sec 65B packet key + hashes
  // (live SSE alerts do not; the 5 s poll fills these fields in once the row is read back).
  const hasEvidence = !!(alert.evidence_packet_key && (alert.face_crop_sha256 || alert.frame_sha256));

  return (
    <div className="rounded-xl p-3.5 flex gap-3.5 transition-all relative overflow-hidden"
      style={{ background: colors.bg, border: `1px solid ${colors.border}` }}>
      {/* Face crop thumbnail */}
      <div className="w-20 h-20 rounded-lg overflow-hidden shrink-0 bg-black/60 border border-slate-700/50 relative group cursor-pointer"
        onClick={() => onOpenPreview && onOpenPreview({
          object_key: alert.face_crop_key,
          image_url: alert.face_crop_url,
          frame_crop_url: alert.frame_crop_url,
          name: alert.name,
          category: alert.category,
        })}>
        {alert.face_crop_url ? (
          <img src={toProxiedUrl(alert.face_crop_url, 'dvas-faces')} alt={alert.name} className="w-full h-full object-cover group-hover:scale-105 transition-transform" />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-slate-600">
            <Eye size={22} />
          </div>
        )}
        <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
          <Maximize2 size={14} className="text-white" />
        </div>
      </div>

      {/* Details */}
      <div className="flex-1 min-w-0 flex flex-col justify-between">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`mono text-[9px] font-bold px-1.5 py-0.5 rounded border uppercase ${colors.badge}`}>
              {cat}
            </span>
            <span className="mono text-[11px] font-bold text-amber-300">
              {matchPct != null ? `${matchPct}% match` : '— match'}
            </span>
            {alert.is_confirmed && (
              <span className="mono text-[9px] bg-emerald-500/20 text-emerald-300 px-1.5 py-0.5 rounded border border-emerald-500/30 flex items-center gap-1">
                <Check size={9} /> CONFIRMED
              </span>
            )}
          </div>
          <div className="mono text-sm font-bold text-white mt-1 truncate" title={alert.name}>
            {alert.name}
          </div>
          <div className="mono text-[10px] text-slate-400 mt-0.5 truncate">
            {alert.drone_id || 'Drone'} {alert.mission_id ? `· ${alert.mission_id}` : ''} · {fmtAge(alert.detected_at)}
          </div>
          {alert.notes && (
            <div className="mono text-[10px] italic mt-1 px-2 py-0.5 rounded truncate" style={{ color: 'var(--dvas-text-muted)', background: 'var(--dvas-surface-2)' }}>
              "{alert.notes}"
            </div>
          )}
        </div>

        {/* Section 65B indicator — only when the alert carries an evidence reference */}
        {hasEvidence ? (
          <div className="mono text-[9px] text-slate-500 flex items-center gap-1 mt-1" title={alert.evidence_packet_key}>
            <FileCheck size={10} className="text-emerald-400" />
            <span>Sec 65B evidence packet stored</span>
          </div>
        ) : (
          <div className="mono text-[9px] text-slate-600 flex items-center gap-1 mt-1">
            <span>Not yet in evidence</span>
          </div>
        )}
      </div>

      {/* Action buttons */}
      {!alert.is_confirmed && canReview && (
        <div className="flex flex-col justify-center gap-2 pl-2 border-l border-slate-700/40">
          <button
            onClick={() => onConfirm(alert.id)}
            disabled={busy}
            title="Confirm Match (Operator Verification)"
            className="p-2 rounded-lg bg-emerald-500/20 hover:bg-emerald-500/35 text-emerald-400 border border-emerald-500/40 transition-all cursor-pointer"
          >
            <CheckCircle size={15} />
          </button>
          <button
            onClick={() => onDismiss(alert.id)}
            disabled={busy}
            title="Dismiss Alert (False Positive)"
            className="p-2 rounded-lg bg-slate-800/60 hover:bg-red-500/20 text-slate-400 hover:text-red-400 border border-slate-700 hover:border-red-500/30 transition-all cursor-pointer"
          >
            <X size={15} />
          </button>
        </div>
      )}
    </div>
  );
}

function WatchlistCard({ entry, onRemove, onOpenPreview }) {
  const [imgError, setImgError] = useState(false);
  const categoryColors = {
    wanted:  { text: '#ef4444', badge: 'bg-red-500/20 text-red-300 border-red-500/30' },
    missing: { text: '#fb923c', badge: 'bg-orange-500/20 text-orange-300 border-orange-500/30' },
    vip:     { text: '#22c55e', badge: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30' },
    poi:     { text: '#a855f7', badge: 'bg-purple-500/20 text-purple-300 border-purple-500/30' },
  };
  const cat = (entry.category || 'wanted').toLowerCase();
  const colors = categoryColors[cat] || categoryColors.wanted;
  const hasImage = entry.reference_image_url && !imgError;

  return (
    <div className="rounded-xl p-3 flex flex-col justify-between gap-3 relative group"
      style={{ background: 'var(--dvas-surface-2)', border: '1px solid var(--dvas-border)' }}>
      <div className="flex items-start gap-3">
        {/* Photo thumbnail with click-to-preview */}
        <div
          className={`w-16 h-16 rounded-lg overflow-hidden shrink-0 bg-black/50 border border-slate-700/50 relative group ${hasImage ? 'cursor-pointer hover:border-amber-500/50' : ''}`}
          onClick={() => {
            if (hasImage && onOpenPreview) {
              onOpenPreview({
                image_url: entry.reference_image_url,
                object_key: entry.reference_image_key,
                name: entry.name,
                category: entry.category,
                case_ref: entry.case_ref,
                description: entry.description,
                bucket: 'dvas-watchlist',
              });
            }
          }}
          title={hasImage ? "Click to view full-resolution photograph" : undefined}
        >
          {hasImage ? (
            <>
              <img
                src={toProxiedUrl(entry.reference_image_url, 'dvas-watchlist')}
                alt={entry.name}
                className="w-full h-full object-cover group-hover:scale-105 transition-transform"
                onError={() => setImgError(true)}
              />
              <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
                <Maximize2 size={13} className="text-white" />
              </div>
            </>
          ) : (
            <div className="w-full h-full flex items-center justify-center text-slate-600">
              <Users size={20} />
            </div>
          )}
        </div>

        {/* Info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className={`mono text-[8px] font-bold px-1.5 py-0.5 rounded border uppercase ${colors.badge}`}>
              {cat}
            </span>
            {entry.embedding_count > 0 && (
              <span className="mono text-[8px] text-sky-400 bg-sky-500/10 px-1 py-0.5 rounded">
                {entry.embedding_count} emb
              </span>
            )}
          </div>
          <div className="mono text-xs font-bold text-white mt-1 truncate" title={entry.name}>
            {entry.name}
          </div>
          {entry.case_ref && (
            <div className="mono text-[10px] text-amber-300/80 truncate mt-0.5">
              {entry.case_ref}
            </div>
          )}
        </div>
      </div>

      {entry.description && (
        <div className="mono text-[10px] line-clamp-2 p-1.5 rounded" style={{ color: 'var(--dvas-text-muted)', background: 'var(--dvas-surface-2)' }}>
          {entry.description}
        </div>
      )}

      <div className="flex items-center justify-between pt-2 border-t border-slate-800/80">
        <span className="mono text-[9px] text-slate-500">
          Added {fmtAge(entry.added_at)}
        </span>
        <button
          onClick={() => onRemove(entry.id, entry.name)}
          title="Remove from Watchlist"
          className="p-1 rounded text-slate-500 hover:text-red-400 hover:bg-red-500/10 transition-colors cursor-pointer"
        >
          <Trash2 size={13} />
        </button>
      </div>
    </div>
  );
}

function EnrollmentModal({ isOpen, onClose, onEnrolled, arcfaceLoaded = true, arcfaceProvider = '' }) {
  const [name, setName] = useState('');
  const [category, setCategory] = useState('wanted');
  const [caseRef, setCaseRef] = useState('');
  const [description, setDescription] = useState('');
  const [file, setFile] = useState(null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState(null);
  const [successMsg, setSuccessMsg] = useState(null);
  const fileInputRef = useRef(null);

  const handleClose = useCallback(() => {
    setName('');
    setCategory('wanted');
    setCaseRef('');
    setDescription('');
    setFile(null);
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl(null);
    setErrorMsg(null);
    setSuccessMsg(null);
    onClose && onClose();
  }, [previewUrl, onClose]);

  const [dragOver, setDragOver] = useState(false);

  // Shared by the click picker and drag-and-drop
  const selectFile = (f) => {
    if (!f) return;
    if (!/^image\/(jpeg|jpg|png)$/i.test(f.type || '')) {
      setErrorMsg('Please use a JPEG or PNG photograph');
      return;
    }
    setFile(f);
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl(URL.createObjectURL(f));
    setErrorMsg(null);
    setSuccessMsg(null);
  };

  const handleFileChange = (e) => selectFile(e.target.files?.[0]);

  const handleDragOver = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!dragOver) setDragOver(true);
  };

  const handleDragLeave = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
    selectFile(e.dataTransfer?.files?.[0]);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!name.trim()) {
      setErrorMsg('Subject name is required');
      return;
    }
    if (!file) {
      setErrorMsg('Reference face photograph is required');
      return;
    }

    setSubmitting(true);
    setErrorMsg(null);
    setSuccessMsg(null);

    const formData = new FormData();
    formData.append('name', name.trim());
    formData.append('category', category);
    if (caseRef.trim()) formData.append('case_ref', caseRef.trim());
    if (description.trim()) formData.append('description', description.trim());
    formData.append('image', file);

    try {
      const res = await api.post('/analytics/person/watchlist/enroll', formData);
      if (res.data?.status === 'enrolled') {
        const embCount = res.data.embeddings_stored ?? '—';
        setSuccessMsg(`Subject "${res.data.name || name}" enrolled successfully (face embeddings stored: ${embCount}).`);
        setTimeout(() => {
          onEnrolled && onEnrolled(res.data);
          handleClose();
        }, 1200);
      } else {
        setErrorMsg(res.data?.detail || 'Enrollment failed');
      }
    } catch (err) {
      setErrorMsg(apiError(err, 'Failed to enroll person'));
    } finally {
      setSubmitting(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm">
      <div className="w-full max-w-lg rounded-2xl overflow-hidden shadow-2xl flex flex-col"
        style={{ background: 'var(--dvas-surface)', border: '1px solid var(--dvas-border)' }}>
        {/* Header */}
        <div className="px-5 py-4 flex items-center justify-between border-b border-slate-800">
          <div className="flex items-center gap-2">
            <Shield className="text-amber-400" size={18} />
            <span className="font-bold text-white text-sm tracking-wide">Enroll Person to Face Watchlist</span>
            <span
              className={`mono text-[9px] px-2 py-0.5 rounded-full flex items-center gap-1 border ${
                arcfaceLoaded
                  ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                  : 'bg-red-500/10 text-red-400 border-red-500/30'
              }`}
            >
              <span className={`w-1.5 h-1.5 rounded-full ${arcfaceLoaded ? 'bg-emerald-400 animate-pulse' : 'bg-red-400'}`} />
              <span>{arcfaceLoaded ? `ArcFace (${arcfaceProvider.replace('ExecutionProvider', '')})` : 'ArcFace Inactive'}</span>
            </span>
          </div>
          <button onClick={handleClose} disabled={submitting} className="text-slate-400 hover:text-white transition-colors cursor-pointer">
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-5 flex flex-col gap-4">
          {!arcfaceLoaded && (
            <div className="p-3 rounded-lg bg-amber-500/15 border border-amber-500/30 text-amber-300 mono text-xs flex items-center gap-2">
              <AlertTriangle size={15} className="shrink-0 text-amber-400" />
              <span>Face recognition model is not loaded on the server. Enrollment is temporarily unavailable.</span>
            </div>
          )}
          {errorMsg && (
            <div className="p-3 rounded-lg bg-red-500/15 border border-red-500/30 text-red-300 mono text-xs flex items-center gap-2">
              <AlertTriangle size={15} className="shrink-0 text-red-400" />
              <span>{errorMsg}</span>
            </div>
          )}

          {successMsg && (
            <div className="p-3 rounded-lg bg-emerald-500/15 border border-emerald-500/30 text-emerald-300 mono text-xs flex items-center gap-2">
              <Check size={15} className="shrink-0 text-emerald-400" />
              <span>{successMsg}</span>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Photo Upload & Preview */}
            <div className="flex flex-col gap-2">
              <label className="mono text-[10px] text-slate-400 font-bold uppercase tracking-wider">
                Reference Photograph *
              </label>
              <div
                onClick={() => fileInputRef.current?.click()}
                onDragOver={handleDragOver}
                onDragEnter={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                className="w-full h-44 rounded-xl border-2 border-dashed hover:border-sky-500/60 flex flex-col items-center justify-center cursor-pointer transition-colors overflow-hidden relative group"
                style={{ background: 'var(--dvas-surface-2)', borderColor: dragOver ? 'rgba(56,189,248,0.6)' : 'var(--dvas-border)' }}
              >
                {previewUrl ? (
                  <>
                    <img src={previewUrl} alt="Preview" className="w-full h-full object-contain" />
                    <div className="absolute inset-0 bg-black/50 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center mono text-xs text-sky-300 font-bold">
                      Change Photo
                    </div>
                  </>
                ) : (
                  <div className="text-center p-3 flex flex-col items-center gap-2 text-slate-500">
                    <Eye size={28} className="text-slate-600" />
                    <span className="mono text-xs font-semibold text-slate-400">Click or Drag Photo</span>
                    <span className="mono text-[9px] text-slate-600">JPEG, PNG (Clear frontal face recommended)</span>
                  </div>
                )}
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/jpeg,image/png,image/jpg"
                  onChange={handleFileChange}
                  className="hidden"
                />
              </div>
            </div>

            {/* Fields */}
            <div className="flex flex-col gap-3">
              <div>
                <label className="mono text-[10px] text-slate-400 font-bold uppercase tracking-wider block mb-1">
                  Subject Full Name *
                </label>
                <input
                  type="text"
                  required
                  value={name}
                  onChange={e => setName(e.target.value)}
                  placeholder="e.g. Ramesh Kumar"
                  className="w-full px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-white mono text-xs focus:outline-none focus:border-sky-500"
                />
              </div>

              <div>
                <label className="mono text-[10px] text-slate-400 font-bold uppercase tracking-wider block mb-1">
                  Category *
                </label>
                <select
                  value={category}
                  onChange={e => setCategory(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-white mono text-xs focus:outline-none focus:border-sky-500"
                >
                  <option value="wanted">Wanted Suspect (Red Alert)</option>
                  <option value="missing">Missing Person (Amber Alert)</option>
                  <option value="vip">VIP / Protected Subject (Green)</option>
                  <option value="poi">Person of Interest (Purple)</option>
                </select>
              </div>

              <div>
                <label className="mono text-[10px] text-slate-400 font-bold uppercase tracking-wider block mb-1">
                  FIR / Case Reference
                </label>
                <input
                  type="text"
                  value={caseRef}
                  onChange={e => setCaseRef(e.target.value)}
                  placeholder="e.g. FIR/2026/EKM/912"
                  className="w-full px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-white mono text-xs focus:outline-none focus:border-sky-500"
                />
              </div>
            </div>
          </div>

          <div>
            <label className="mono text-[10px] text-slate-400 font-bold uppercase tracking-wider block mb-1">
              Description & Surveillance Notes
            </label>
            <textarea
              rows={2}
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="Identifying features, known associates, operating district..."
              className="w-full px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-white mono text-xs focus:outline-none focus:border-sky-500"
            />
          </div>

          {/* Footer Buttons */}
          <div className="flex items-center justify-end gap-3 pt-3 border-t border-slate-800">
            <button
              type="button"
              onClick={handleClose}
              disabled={submitting}
              className="px-4 py-2 rounded-lg mono text-xs text-slate-400 hover:text-white hover:bg-slate-800 transition-colors cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting || !arcfaceLoaded}
              className="px-5 py-2 rounded-lg mono text-xs font-bold bg-amber-500 hover:bg-amber-400 text-slate-950 flex items-center gap-2 transition-all cursor-pointer shadow-lg shadow-amber-500/20 disabled:opacity-50"
            >
              {submitting ? (
                <>
                  <RefreshCw size={13} className="animate-spin" />
                  <span>Extracting Biometrics…</span>
                </>
              ) : (
                <>
                  <Shield size={13} />
                  <span>Enroll Subject</span>
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function FaceSearchPanel({ missions, onOpenPreview }) {
  const [queryFile, setQueryFile] = useState(null);
  const [queryPreview, setQueryPreview] = useState(null);
  const [selectedMission, setSelectedMission] = useState('');
  const [threshold, setThreshold] = useState(0.40);
  const [searching, setSearching] = useState(false);
  const [searchResults, setSearchResults] = useState(null);
  const [searchError, setSearchError] = useState(null);
  const fileInputRef = useRef(null);

  const handleFileChange = (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setQueryFile(f);
    setQueryPreview(URL.createObjectURL(f));
    setSearchResults(null);
    setSearchError(null);
  };

  const handleSearch = async (e) => {
    if (e) e.preventDefault();
    if (!queryFile) {
      setSearchError('Please upload a face photo to search');
      return;
    }

    setSearching(true);
    setSearchError(null);

    const formData = new FormData();
    formData.append('image', queryFile);
    if (selectedMission) formData.append('mission_id', selectedMission);
    formData.append('threshold', threshold.toString());
    formData.append('limit', '24');

    try {
      const res = await api.post('/analytics/person/faces/search', formData);
      setSearchResults(res.data);
    } catch (err) {
      setSearchError(err.response?.data?.detail || err.message || 'Face search failed');
    } finally {
      setSearching(false);
    }
  };

  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        {/* Dropzone & Controls */}
        <div className="lg:col-span-5 flex flex-col gap-3">
          <div
            onClick={() => fileInputRef.current?.click()}
            className="w-full h-44 rounded-xl border-2 border-dashed hover:border-violet-500/60 flex flex-col items-center justify-center cursor-pointer transition-colors overflow-hidden relative group"
            style={{ background: 'var(--dvas-surface-2)', borderColor: 'var(--dvas-border)' }}
          >
            {queryPreview ? (
              <>
                <img src={queryPreview} alt="Query Face" className="w-full h-full object-contain" />
                <div className="absolute inset-0 bg-black/50 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center mono text-xs text-violet-300 font-bold">
                  Change Photo
                </div>
              </>
            ) : (
              <div className="text-center p-3 flex flex-col items-center gap-2 text-slate-500">
                <Search size={28} className="text-violet-400/60" />
                <span className="mono text-xs font-semibold" style={{ color: 'var(--dvas-text-muted)' }}>Upload Target Face Photo</span>
                <span className="mono text-[9px]" style={{ color: 'var(--dvas-text-dim)' }}>Searches drone video recordings database</span>
              </div>
            )}
            <input
              ref={fileInputRef}
              type="file"
              accept="image/jpeg,image/png,image/jpg"
              onChange={handleFileChange}
              className="hidden"
            />
          </div>

          {/* Search Scope & Threshold Controls */}
          <div className="flex flex-col gap-2.5 p-3 rounded-xl" style={{ background: 'var(--dvas-surface-2)', border: '1px solid var(--dvas-border)' }}>
            <div>
              <label className="mono text-[10px] text-slate-400 font-bold uppercase tracking-wider block mb-1">
                Search Scope
              </label>
              <select
                value={selectedMission}
                onChange={e => setSelectedMission(e.target.value)}
                className="w-full px-3 py-1.5 rounded-lg mono text-xs focus:outline-none focus:border-violet-500"
                style={{ background: 'var(--dvas-surface)', border: '1px solid var(--dvas-border)', color: 'var(--dvas-text)' }}
              >
                <option value="">Universal Search (All Drone Missions)</option>
                {missions?.map(m => {
                  const mId = typeof m === 'string' ? m : m.mission_id;
                  return (
                    <option key={mId} value={mId}>
                      Mission: {mId}
                    </option>
                  );
                })}
              </select>
            </div>

            <div>
              <div className="flex items-center justify-between mono text-[10px] text-slate-400 font-bold mb-1">
                <span className="uppercase tracking-wider">Similarity Match Threshold</span>
                <span className="text-violet-300 font-bold">{Math.round(threshold * 100)}%</span>
              </div>
              <input
                type="range"
                min="0.20"
                max="0.80"
                step="0.05"
                value={threshold}
                onChange={e => setThreshold(parseFloat(e.target.value))}
                className="w-full accent-violet-500 cursor-pointer"
              />
              <div className="flex justify-between mono text-[8px] text-slate-600 mt-0.5">
                <span>Broad (20%)</span>
                <span>Standard (40%)</span>
                <span>Strict (80%)</span>
              </div>
            </div>

            <button
              onClick={handleSearch}
              disabled={searching || !queryFile}
              className="w-full py-2 px-4 rounded-lg mono text-xs font-bold bg-violet-600 hover:bg-violet-500 flex items-center justify-center gap-2 transition-all cursor-pointer shadow-lg shadow-violet-600/20 disabled:opacity-40 disabled:cursor-not-allowed mt-1"
              style={{ color: '#ffffff' }}
            >
              {searching ? (
                <>
                  <RefreshCw size={13} className="animate-spin" />
                  <span>Searching for matches…</span>
                </>
              ) : (
                <>
                  <Search size={13} />
                  <span>Run Reverse Biometric Search</span>
                </>
              )}
            </button>
          </div>
        </div>

        {/* Results Grid */}
        <div className="lg:col-span-7 flex flex-col gap-3">
          {searchError && (
            <div className="p-3 rounded-lg bg-red-500/15 border border-red-500/30 text-red-300 mono text-xs flex items-center gap-2">
              <AlertTriangle size={15} className="shrink-0 text-red-400" />
              <span>{searchError}</span>
            </div>
          )}

          {searchResults && (
            <div className="flex items-center justify-between p-2.5 rounded-lg" style={{ background: 'var(--dvas-surface-2)', border: '1px solid var(--dvas-border)' }}>
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-emerald-400" />
                <span className="mono text-xs font-bold" style={{ color: 'var(--dvas-text)' }}>
                  {searchResults.count} Matches Found
                </span>
                <span className="mono text-[10px] text-slate-400">
                  (Scanned {searchResults.total_scanned ?? 'all'} faces across {searchResults.search_scope})
                </span>
              </div>
            </div>
          )}

          {searchResults?.results?.length > 0 ? (
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 max-h-[460px] overflow-y-auto pr-1">
              {searchResults.results.map((r, idx) => {
                const simPct = Math.round(r.similarity * 100);
                const scoreColor = simPct >= 70 ? 'text-emerald-400 border-emerald-500/30 bg-emerald-500/15'
                  : simPct >= 50 ? 'text-amber-400 border-amber-500/30 bg-amber-500/15'
                  : 'text-sky-400 border-sky-500/30 bg-sky-500/15';

                return (
                  <div key={r.id || idx}
                    onClick={() => onOpenPreview && onOpenPreview({
                      image_url: r.image_url,
                      object_key: r.object_key,
                      person_image_url: r.person_image_url,
                      drone_id: r.drone_id,
                      mission_id: r.mission_id,
                      created_at: r.created_at,
                    })}
                    className="rounded-xl p-2.5 flex flex-col gap-2 cursor-pointer transition-all hover:border-violet-500/50 group"
                    style={{ background: 'var(--dvas-surface-2)', border: '1px solid var(--dvas-border)' }}>
                    <div className="w-full h-28 rounded-lg overflow-hidden relative" style={{ background: 'var(--dvas-surface-2)' }}>
                      {r.image_url ? (
                        <img src={toProxiedUrl(r.image_url, 'dvas-faces')} alt="Match" className="w-full h-full object-contain group-hover:scale-105 transition-transform" />
                      ) : (
                        <div className="w-full h-full flex items-center justify-center text-slate-600">
                          <Eye size={20} />
                        </div>
                      )}
                      <div className="absolute top-1.5 right-1.5">
                        <span className={`mono text-[9px] font-bold px-1.5 py-0.5 rounded border ${scoreColor}`}>
                          {simPct}%
                        </span>
                      </div>
                      <div className="absolute bottom-1.5 left-1.5">
                        <span className="mono text-[8px] bg-black/80 text-violet-300 px-1.5 py-0.5 rounded uppercase">
                          {r.matched_on || 'ArcFace'}
                        </span>
                      </div>
                    </div>

                    <div className="flex flex-col gap-0.5">
                      <span className="mono text-[10px] font-bold text-white truncate">
                        {r.drone_id || 'Drone'}
                      </span>
                      <span className="mono text-[9px] text-slate-400 truncate">
                        {r.mission_id || 'Mission'}
                      </span>
                      <span className="mono text-[8px] text-slate-500">
                        {fmtIST(r.created_at)}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : searchResults ? (
            <div className="p-8 text-center text-slate-500 mono text-xs flex flex-col items-center gap-2">
              <Search size={32} className="opacity-30 text-slate-600" />
              <span>No matching faces found above {Math.round(threshold * 100)}% threshold.</span>
              <span className="text-[10px] text-slate-600">Try lowering the threshold slider or choosing universal search scope.</span>
            </div>
          ) : (
            <div className="h-64 rounded-xl border border-dashed border-slate-800 flex flex-col items-center justify-center text-slate-600 mono text-xs gap-2">
              <Fingerprint size={36} className="opacity-30" />
              <span>Upload a reference photo on the left to initiate search</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   MAIN EXPORT
   ────────────────────────────────────────────────────────────── */
/** Soft-proxied routes answer 200 with {error, detail} when the analyzer fails. */
function ensureOk(res) {
  const d = res?.data;
  if (d && typeof d === 'object' && !Array.isArray(d) && d.error) {
    throw new Error(typeof d.detail === 'string' ? d.detail : String(d.error));
  }
  return res;
}

const ALERT_REVIEW_ROLES = ['system_administrator', 'senior_officer', 'control_room_officer'];

export default function PersonTrackingAnalytics({ drones, droneId = '', user, onNavigateTab }) {
  // The page's drone selector wins; our own pill selection is only a fallback when it is empty.
  const [selectedDrone, setSelectedDrone] = useState(null);
  const activeDrone = droneId || selectedDrone;
  const canReviewAlerts = !user?.role || ALERT_REVIEW_ROLES.includes(user.role);

  // Inline status line for action results / failures (shown at the top of the page)
  const [notice, setNotice] = useState(null); // { type: 'error' | 'ok', text }
  const showError = useCallback((err, fallback) => setNotice({ type: 'error', text: `${fallback}: ${apiError(err, 'request failed')}` }), []);

  // Active streams hook
  const { streams: activeStreams, streamMap } = useActiveStreams();
  const liveStreams = activeStreams?.filter(s => s.status === 'live' || s.status === 'draining' || s.online);
  const validDrones = liveStreams?.length > 0
    ? liveStreams.map(s => ({ id: s.drone_id, ...s }))
    : (drones?.length > 0
        ? drones.filter(d => (d.status === 'live' || d.status === 'draining') && !/^DRN-00[1-5]$/i.test(d.id || d.drone_id))
        : []);

  useEffect(() => {
    if (droneId) return; // page-selected drone is authoritative
    if (!validDrones || validDrones.length === 0) {
      if (selectedDrone !== null) setSelectedDrone(null);
      return;
    }
    const currentStillValid = validDrones.some(d => d.id === selectedDrone);
    if (!selectedDrone || !currentStillValid) {
      setSelectedDrone(validDrones[0].id);
    }
  }, [validDrones, selectedDrone, droneId]);

  const [reidLoading,         setReidLoading]         = useState(false);
  const [sseWindow,           setSseWindow]           = useState([]);
  const [startingStream,      setStartingStream]      = useState(false);
  const [liveFaceCount,       setLiveFaceCount]       = useState(0);
  const [personMissionFilter, setPersonMissionFilter] = useState('');
  const [faceMissionFilter,   setFaceMissionFilter]   = useState('');

  // ── Face Recognition & Watchlist State ─────────────────────────────────────
  // Defaults: only persons, faces, track_ids, confidence enabled; others enabled by user
  const [annotationConfig, setAnnotationConfig] = useState({
    person_boxes:     true,
    face_boxes:       true,
    track_ids:        true,
    confidence:       true,
    watchlist_alerts: false,
    alert_labels:     false,
    trajectory:       false,
  });
  const [showEnrollModal, setShowEnrollModal] = useState(false);
  const [previewModalItem, setPreviewModalItem] = useState(null);
  const [alertsList, setAlertsList] = useState([]);
  const [watchlistCategoryFilter, setWatchlistCategoryFilter] = useState('');

  // Health
  const { data: healthData, refetch: refetchHealth } = useAnalyticsFetch('/analytics/person/health', 5000);
  const streamRunning   = healthData?.stream_running ?? false;
  const modelActive     = healthData?.model_loaded  ?? false;
  const arcfaceLoaded   = healthData?.arcface_loaded ?? false;
  const arcfaceProvider = healthData?.arcface_provider ?? 'none';
  const activeMissions  = healthData?.active_missions ?? {};

  // Annotation config sync from backend
  const { data: serverAnnotationConfig } = usePersonAnnotationConfig(10000);
  useEffect(() => {
    if (serverAnnotationConfig && typeof serverAnnotationConfig === 'object' && !serverAnnotationConfig.error) {
      setAnnotationConfig(prev => ({ ...prev, ...serverAnnotationConfig }));
    }
  }, [serverAnnotationConfig]);

  const handleToggleAnnotation = useCallback(async (key) => {
    const prevVal = !!annotationConfig[key];
    setAnnotationConfig(c => ({ ...c, [key]: !prevVal }));
    try {
      ensureOk(await api.put('/analytics/person/annotation/config', { [key]: !prevVal }));
    } catch (err) {
      setAnnotationConfig(c => ({ ...c, [key]: prevVal })); // roll back the optimistic toggle
      showError(err, 'HUD overlay change failed');
    }
  }, [annotationConfig, showError]);

  // Active mission from stream map or health data (only for live/draining drones)
  const activeMissionId = (() => {
    if (activeDrone && streamMap.has(activeDrone)) {
      const s = streamMap.get(activeDrone);
      if (s?.status === 'live' || s?.status === 'draining' || s?.online) {
        return s.mission_id || null;
      }
    }
    const isLive = validDrones.some(d => d.id === activeDrone);
    if (isLive && activeMissions[activeDrone]) {
      return activeMissions[activeDrone];
    }
    return null;
  })();

  // Data hooks
  const { data: tracksData,     loading: tracksLoading }   = usePersonTracks(activeDrone || '', 5000);
  const { data: dedupData }                                 = usePersonDedup(15000);
  const { data: identitiesData, loading: identLoading }    = usePersonIdentities(activeDrone || '', 10000, personMissionFilter);
  const { data: facesData,      loading: facesLoading }    = useFacesGallery(activeDrone || '', 10000, faceMissionFilter);
  const { data: countData }                                 = usePersonCount(activeDrone || '', 5000);
  const { data: liveData,       loading: liveLoading }     = usePersonLive(activeDrone || '', 3000);

  // Watchlist & Alerts hooks
  const { data: watchlistData, loading: watchlistLoading, refetch: refreshWatchlist } = usePersonWatchlist(watchlistCategoryFilter, 15000);
  const { data: alertsData, refetch: refetchAlerts } = usePersonWatchlistAlerts(activeDrone || '', 20, 5000);
  // Alerts the operator dismissed/hid this session; the 5 s poll must not bring them back.
  const dismissedIdsRef = useRef(new Set());
  const [alertBusy, setAlertBusy] = useState(false);

  // Normalise arrays
  const tracks     = Array.isArray(tracksData)            ? tracksData
    : Array.isArray(tracksData?.tracks)                   ? tracksData.tracks : [];
  const identities = Array.isArray(identitiesData)        ? identitiesData
    : Array.isArray(identitiesData?.identities)           ? identitiesData.identities : [];
  const faces      = Array.isArray(facesData)             ? facesData
    : Array.isArray(facesData?.gallery)                   ? facesData.gallery
    : Array.isArray(facesData?.faces)                     ? facesData.faces : [];
  const watchlist  = Array.isArray(watchlistData)         ? watchlistData
    : Array.isArray(watchlistData?.watchlist)             ? watchlistData.watchlist : [];

  // Live persons from in-memory buffer (has crop_b64 pairs)
  const livePersons = Array.isArray(liveData?.persons)
    ? liveData.persons
    : Array.isArray(liveData?.entries)
    ? liveData.entries
    : Array.isArray(liveData)
    ? liveData
    : [];

  useEffect(() => {
    const arr = Array.isArray(alertsData?.alerts) ? alertsData.alerts
      : Array.isArray(alertsData) ? alertsData : [];
    if (arr.length > 0) {
      setAlertsList(prev => {
        const hidden = dismissedIdsRef.current;
        const validItems = arr.filter(a => !hidden.has(a.id) && (!a.notes || !a.notes.toLowerCase().includes('dismiss')));
        const polledById = new Map(validItems.map(a => [a.id, a]));
        // Refresh alerts we already show with the stored row (evidence keys, confirmation state)
        const kept = prev
          .filter(a => !hidden.has(a.id))
          .map(a => (polledById.has(a.id) ? { ...a, ...polledById.get(a.id) } : a));
        const existingIds = new Set(prev.map(a => a.id));
        const newItems = validItems.filter(a => !existingIds.has(a.id));
        return [...newItems, ...kept].slice(0, 30);
      });
    }
  }, [alertsData]);

  // Mission dedup
  const { data: dedupProgressData }  = usePersonDedupProgress(2000);
  const { data: missionDedupData }   = usePersonMissionDedup(30000);

  const handleDeduplicate = useCallback(async (missionId) => {
    try {
      ensureOk(await api.post('/analytics/person/deduplicate', { mission_id: missionId }));
    } catch (err) {
      showError(err, `Deduplication of ${missionId} failed`);
    }
  }, [showError]);

  // Clear SSE detection window and live counter on active drone switch
  useEffect(() => {
    setSseWindow([]);
    setLiveFaceCount(0);
  }, [activeDrone]);

  // Seed baseline point in sseWindow from liveData or countData if sseWindow is empty
  useEffect(() => {
    if (sseWindow.length === 0) {
      const initPersons = livePersons.length > 0 ? livePersons.length : (countData?.active_persons ?? 0);
      const initFaces = livePersons.filter(p => p.face_crop_b64 || p.face_b64).length;
      if (initPersons > 0 || initFaces > 0) {
        const ts = new Date().toISOString().slice(11, 19);
        setSseWindow([{ t: ts, count: Number(initPersons), faces: Number(initFaces) }]);
      }
    }
  }, [livePersons.length, countData?.active_persons]); // eslint-disable-line react-hooks/exhaustive-deps

  // SSE handler with real-time watchlist match support & audio chime
  const handleSSEEvent = useCallback((evt) => {
    if (!evt) return;

    // Normalise envelope: Analytics Gateway sends { stream, service, payload, data, drone_id }
    const stream = evt.stream || evt.service;
    const payload = evt.payload || evt.data || evt;

    // ── Watchlist match alert ──────────────────────────────────────
    if (stream === 'person_alert' || payload?.type === 'watchlist_match') {
      const alertData = payload;
      if (alertData && !(alertData.id && dismissedIdsRef.current.has(alertData.id))) {
        setAlertsList(prev => [
          {
            id: alertData.id || `alert-${Date.now()}`,
            watchlist_id: alertData.watchlist_id,
            name: alertData.name,
            category: alertData.category,
            confidence: alertData.similarity ?? alertData.confidence ?? null,
            drone_id: alertData.drone_id,
            mission_id: alertData.mission_id,
            tracker_id: alertData.tracker_id,
            face_crop_url: alertData.face_crop_key
              ? toProxiedUrl(alertData.face_crop_key, 'dvas-faces')
              : (alertData.face_crop_url ? toProxiedUrl(alertData.face_crop_url, 'dvas-faces') : null),
            face_crop_key: alertData.face_crop_key,
            detected_at: alertData.ts || new Date().toISOString(),
            is_confirmed: false,
          },
          ...prev.filter(a => a.watchlist_id !== alertData.watchlist_id || Date.now() - new Date(a.detected_at).getTime() > 10000),
        ].slice(0, 30));
        playAlertChime();
      }
      return;
    }

    // ── Person detection event ─────────────────────────────────────
    if (stream !== 'person') return;

    const eventDroneId = payload?.drone_id || evt.drone_id;
    if (activeDrone && eventDroneId) {
      const matches = eventDroneId === activeDrone ||
                      eventDroneId.includes(activeDrone) ||
                      activeDrone.includes(eventDroneId);
      if (!matches) return;
    }

    const pCount = payload?.person_count ?? payload?.persons?.length ?? payload?.count ?? 0;
    const fCount = payload?.face_count ?? payload?.faces?.length ?? 0;
    const rawTs = payload?.ts;
    const ts = typeof rawTs === 'string' && rawTs.length >= 19
      ? rawTs.slice(11, 19)
      : new Date().toISOString().slice(11, 19);

    setSseWindow(w => [...w.slice(-59), { t: ts, count: Number(pCount), faces: Number(fCount) }]);
    setLiveFaceCount(fc => fc + Number(fCount));
  }, [activeDrone]);

  const { connected: sseConnected } = useAnalyticsSSE(handleSSEEvent);

  const handleConfirmAlert = useCallback(async (alertId) => {
    setAlertBusy(true);
    try {
      ensureOk(await api.post(`/analytics/person/watchlist/alerts/${alertId}/confirm`, { notes: 'Verified by Operator' }));
      setAlertsList(prev => prev.map(a => a.id === alertId ? { ...a, is_confirmed: true, notes: 'Verified by Operator' } : a));
      refetchAlerts && refetchAlerts();
    } catch (err) {
      showError(err, 'Confirm alert failed');
    } finally {
      setAlertBusy(false);
    }
  }, [refetchAlerts, showError]);

  const handleDismissAlert = useCallback(async (alertId) => {
    if (typeof alertId !== 'string' || alertId.startsWith('alert-')) {
      // Live-only alert without a server id: nothing to dismiss server-side, hide it here
      dismissedIdsRef.current.add(alertId);
      setAlertsList(prev => prev.filter(a => a.id !== alertId));
      return;
    }
    setAlertBusy(true);
    try {
      ensureOk(await api.post(`/analytics/person/watchlist/alerts/${alertId}/dismiss`, { notes: 'Dismissed by Operator' }));
      dismissedIdsRef.current.add(alertId);
      setAlertsList(prev => prev.filter(a => a.id !== alertId));
      refetchAlerts && refetchAlerts();
    } catch (err) {
      showError(err, 'Dismiss alert failed');
    } finally {
      setAlertBusy(false);
    }
  }, [refetchAlerts, showError]);

  // Dismiss All: unconfirmed alerts are dismissed on the server (same endpoint as the
  // per-card X); confirmed alerts stay confirmed on the server and are only hidden here.
  // Alerts that only arrived over SSE without a server id can only be hidden locally.
  const handleDismissAll = useCallback(async () => {
    const isServerId = (id) => typeof id === 'string' && !id.startsWith('alert-');
    const toDismiss = alertsList.filter(a => !a.is_confirmed && isServerId(a.id));
    const hideOnly = alertsList.filter(a => a.is_confirmed || !isServerId(a.id));
    setAlertBusy(true);
    const results = await Promise.allSettled(toDismiss.map(a =>
      api.post(`/analytics/person/watchlist/alerts/${a.id}/dismiss`, { notes: 'Dismissed by Operator (bulk)' }).then(ensureOk)
    ));
    const failed = [];
    results.forEach((r, i) => {
      if (r.status === 'fulfilled') dismissedIdsRef.current.add(toDismiss[i].id);
      else failed.push(r.reason);
    });
    hideOnly.forEach(a => dismissedIdsRef.current.add(a.id));
    setAlertsList(prev => prev.filter(a => !dismissedIdsRef.current.has(a.id)));
    setAlertBusy(false);
    if (failed.length > 0) {
      showError(failed[0], `${failed.length} of ${toDismiss.length} alerts could not be dismissed`);
    }
    refetchAlerts && refetchAlerts();
  }, [alertsList, refetchAlerts, showError]);

  const handleRemoveWatchlist = useCallback(async (id, name) => {
    if (!window.confirm(`Are you sure you want to remove "${name}" from the watchlist?`)) return;
    try {
      ensureOk(await api.delete(`/analytics/person/watchlist/${id}`));
      refreshWatchlist && refreshWatchlist();
    } catch (err) {
      showError(err, `Removing "${name}" from the watchlist failed`);
    }
  }, [refreshWatchlist, showError]);

  // Stream start/stop (POST only on the backend)
  const handleStartStream = useCallback(async () => {
    setStartingStream(true);
    setNotice(null);
    try {
      ensureOk(await api.post('/analytics/person/stream/start'));
      try {
        await api.post('/models/sync-worker', { service: 'person', action: 'start' });
      } catch (e) {
        setNotice({ type: 'error', text: `Person detection started, but worker sync failed: ${apiError(e, 'request failed')}` });
      }
      refetchHealth && refetchHealth();
    } catch (err) {
      showError(err, 'Failed to start person detection');
    } finally {
      setStartingStream(false);
    }
  }, [refetchHealth, showError]);

  const handleStopStream = useCallback(async () => {
    setNotice(null);
    try {
      ensureOk(await api.post('/analytics/person/stream/stop'));
      try {
        await api.post('/models/sync-worker', { service: 'person', action: 'stop' });
      } catch (e) {
        setNotice({ type: 'error', text: `Person detection stopped, but worker sync failed: ${apiError(e, 'request failed')}` });
      }
      refetchHealth && refetchHealth();
    } catch (err) {
      showError(err, 'Failed to stop person detection');
    }
  }, [refetchHealth, showError]);

  // Re-ID: spinner lasts exactly as long as the request
  const handleReidTrigger = async () => {
    setReidLoading(true);
    try {
      ensureOk(await api.post('/analytics/person/reid'));
      setNotice({ type: 'ok', text: 'Global Re-ID clustering triggered' });
    } catch (err) {
      showError(err, 'Global Re-ID trigger failed');
    } finally {
      setReidLoading(false);
    }
  };

  return (
    <div className="flex flex-col gap-5" style={{ fontFamily: "'Plus Jakarta Sans', sans-serif" }}>

      {notice && (
        <div className={`rounded-lg px-3 py-2 mono text-xs flex items-center gap-2 border ${
          notice.type === 'error'
            ? 'bg-red-500/10 border-red-500/30 text-red-300'
            : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
        }`} role={notice.type === 'error' ? 'alert' : 'status'}>
          {notice.type === 'error' ? <AlertTriangle size={13} className="shrink-0" /> : <Check size={13} className="shrink-0" />}
          <span className="flex-1">{notice.text}</span>
          <button onClick={() => setNotice(null)} className="text-slate-400 hover:text-white cursor-pointer" title="Close">
            <X size={13} />
          </button>
        </div>
      )}

      {/* LIVE ANNOTATED FEED */}
      <AnnotatedPersonFeedViewer
        activeDrone={activeDrone}
        setActiveDrone={droneId ? null : setSelectedDrone}
        drones={drones}
        streamRunning={streamRunning}
        onStartStream={handleStartStream}
        onStopStream={handleStopStream}
        modelActive={modelActive}
        startingStream={startingStream}
        activeMissionId={activeMissionId}
        onNavigateTab={onNavigateTab}
        annotationConfig={annotationConfig}
        onToggleAnnotation={handleToggleAnnotation}
      />

      {/* KPI STRIP */}
      <PersonKPIStrip
        tracks={tracks}
        dedupData={dedupData}
        countData={countData}
        sseConnected={sseConnected}
        liveFaceCount={liveFaceCount}
        activeMissionId={activeMissionId}
        watchlistAlertCount={alertsList.filter(a => !a.is_confirmed).length}
      />

      {/* WATCHLIST ALERTS PANEL (Real-Time match notifications) */}
      {alertsList?.length > 0 && (
        <Panel
          title="⚠️ Watchlist Matches (Real-Time)"
          icon={ShieldAlert}
          iconClass="text-red-400"
          badge={`${alertsList.length} alerts`}
          right={
            <div className="flex items-center gap-3">
              <span className="mono text-[10px] text-red-400 font-bold animate-pulse flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-red-400 shadow-sm shadow-red-400" />
                LIVE MATCH
              </span>
              {canReviewAlerts && (
                <button
                  onClick={handleDismissAll}
                  disabled={alertBusy}
                  title="Dismiss all unconfirmed alerts on the server; confirmed alerts are hidden from this panel only"
                  className="mono text-[10px] text-slate-500 hover:text-slate-400 transition-colors cursor-pointer disabled:opacity-50"
                >
                  {alertBusy ? 'Dismissing…' : 'Dismiss All'}
                </button>
              )}
            </div>
          }
        >
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 p-3">
            {alertsList.slice(0, 9).map(alert => (
              <WatchlistAlertCard
                key={alert.id}
                alert={alert}
                onConfirm={handleConfirmAlert}
                onDismiss={handleDismissAlert}
                onOpenPreview={setPreviewModalItem}
                canReview={canReviewAlerts}
                busy={alertBusy}
              />
            ))}
          </div>
        </Panel>
      )}

      {/* ACTIVE PERSON TRACK CARDS + DETECTION CHART */}
      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-12 lg:col-span-8">
          <Panel
            title="Active Person Tracks"
            icon={Activity}
            iconClass="text-sky-400"
            badge={livePersons.length > 0 ? `${livePersons.length} tracks` : tracks.length > 0 ? `${tracks.length} tracks` : null}
            right={
              <span className={`mono text-[10px] flex items-center gap-1 ${sseConnected ? 'text-sky-400' : 'text-slate-500'}`}>
                <span className={`w-1.5 h-1.5 rounded-full ${sseConnected ? 'bg-sky-400 dvas-live-dot' : 'bg-slate-500'}`} />
                {sseConnected ? 'Live' : 'Polling'}
              </span>
            }>
            <ActivePersonTracks
              persons={livePersons}
              loading={liveLoading}
            />
          </Panel>
        </div>
        <div className="col-span-12 lg:col-span-4">
          <Panel title="Detections (Real-time)" icon={Radio} iconClass="text-violet-400"
            badge={sseWindow.length > 0 ? `${sseWindow[sseWindow.length - 1].count} live` : null}
            right={<span className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>SSE rolling window</span>}>
            <DetectionChart sseWindow={sseWindow} sseConnected={sseConnected} />
          </Panel>
        </div>
      </div>

      {/* FACE WATCHLIST MANAGEMENT PANEL */}
      <Panel
        title="Face Watchlist (Enrolled Persons)"
        icon={Shield}
        iconClass="text-amber-400"
        badge={watchlist.length > 0 ? `${watchlist.length}${watchlistCategoryFilter ? ` (${watchlistCategoryFilter})` : ''} enrolled` : null}
        right={
          <div className="flex items-center gap-2">
            <span
              className={`mono text-[9px] px-2 py-0.5 rounded-full flex items-center gap-1 border ${
                arcfaceLoaded
                  ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                  : 'bg-red-500/10 text-red-400 border-red-500/30'
              }`}
              title={
                arcfaceLoaded
                  ? `ArcFace Recognition Active (${arcfaceProvider})`
                  : 'ArcFace model not loaded — enrollments disabled'
              }
            >
              <span className={`w-1.5 h-1.5 rounded-full ${arcfaceLoaded ? 'bg-emerald-400 animate-pulse' : 'bg-red-400'}`} />
              <span>{arcfaceLoaded ? `ArcFace ${arcfaceProvider.replace('ExecutionProvider', '')}` : 'ArcFace Inactive'}</span>
            </span>
            <select
              value={watchlistCategoryFilter}
              onChange={e => setWatchlistCategoryFilter(e.target.value)}
              className="mono text-[10px] px-2 py-1 rounded bg-slate-900 border border-slate-700 text-slate-300 focus:outline-none cursor-pointer"
            >
              <option value="">All Categories</option>
              <option value="wanted">Wanted</option>
              <option value="missing">Missing</option>
              <option value="vip">VIP</option>
              <option value="poi">POI</option>
            </select>
            <button
              onClick={() => setShowEnrollModal(true)}
              disabled={!arcfaceLoaded}
              title={!arcfaceLoaded ? 'ArcFace model not loaded' : 'Enroll a person into the face watchlist'}
              className={`mono text-[10px] font-bold px-3 py-1 rounded-lg border transition-all flex items-center gap-1.5 shadow-sm ${
                arcfaceLoaded
                  ? 'bg-amber-500/20 text-amber-300 border-amber-500/40 hover:bg-amber-500/30 cursor-pointer shadow-amber-500/10'
                  : 'bg-slate-800/40 text-slate-500 border-slate-700/50 cursor-not-allowed opacity-60'
              }`}
            >
              <Plus size={11} />
              <span>Enroll Person</span>
            </button>
          </div>
        }
      >
        {watchlistLoading && watchlist.length === 0 ? (
          <div className="p-8 text-center text-slate-500 mono text-xs flex flex-col items-center gap-2">
            <div className="w-5 h-5 border-2 border-amber-500/40 border-t-amber-400 rounded-full animate-spin" />
            <span>Loading watchlist...</span>
          </div>
        ) : watchlist.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3 p-3">
            {watchlist.map(entry => (
              <WatchlistCard
                key={entry.id}
                entry={entry}
                onRemove={handleRemoveWatchlist}
                onOpenPreview={setPreviewModalItem}
              />
            ))}
          </div>
        ) : (
          <div className="p-8 text-center text-slate-500 mono text-xs flex flex-col items-center gap-2">
            <Shield size={32} className="opacity-30 text-amber-500/60" />
            <span>
              {watchlistCategoryFilter
                ? `No enrolled subjects found in category "${watchlistCategoryFilter.toUpperCase()}".`
                : 'No enrolled subjects in the Face Watchlist.'}
            </span>
            <span className="text-[10px] text-slate-600">
              {watchlistCategoryFilter
                ? 'Select "All Categories" or click "+ Enroll Person" to enroll a suspect in this category.'
                : 'Click "+ Enroll Person" to enroll suspects with reference photographs.'}
            </span>
          </div>
        )}
      </Panel>

      {/* REVERSE FACE SEARCH */}
      <Panel
        title="Reverse Face Search (Biometric Search)"
        icon={Search}
        iconClass="text-violet-400"
        right={
          <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>
            Face embedding similarity
          </span>
        }
      >
        <FaceSearchPanel
          missions={missionDedupData?.missions}
          onOpenPreview={setPreviewModalItem}
        />
      </Panel>

      {/* PERSON IDENTITIES GALLERY (deduplicated, MinIO stored) */}
      <Panel
        title="Detected Persons — Image Gallery"
        icon={Eye}
        iconClass="text-sky-400"
        badge={identities.length > 0 ? `${identities.length} identities` : null}
        right={
          <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>
            {personMissionFilter ? `Mission: ${personMissionFilter}` : 'Deduplicated'} · MinIO
          </span>
        }>
        <SearchableMissionFilter
          missions={missionDedupData?.missions}
          selected={personMissionFilter}
          onSelect={setPersonMissionFilter}
          activeMissionId={activeMissionId}
          type="person"
        />
        <ImageGallery
          items={identities}
          loading={identLoading}
          label="Detected Persons"
          bucket="dvas-faces"
          isPersonGallery={true}
          selectedMission={personMissionFilter}
        />
      </Panel>

      {/* FACE GALLERY (face crops from MinIO) */}
      <Panel
        title="Detected Faces — Image Gallery"
        icon={Fingerprint}
        iconClass="text-amber-400"
        badge={faces.length > 0 ? `${faces.length} faces` : null}
        right={
          <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>
            {faceMissionFilter ? `Mission: ${faceMissionFilter}` : 'Face crops'} · MinIO
          </span>
        }>
        <SearchableMissionFilter
          missions={missionDedupData?.missions}
          selected={faceMissionFilter}
          onSelect={setFaceMissionFilter}
          activeMissionId={activeMissionId}
          type="face"
        />
        <ImageGallery
          items={faces}
          loading={facesLoading}
          label="Detected Faces"
          bucket="dvas-faces"
          isPersonGallery={false}
          selectedMission={faceMissionFilter}
        />
      </Panel>

      {/* MISSION DEDUPLICATION */}
      <Panel
        title="Mission Deduplication"
        icon={Database}
        iconClass="text-violet-400"
        badge={missionDedupData?.count > 0 ? `${missionDedupData.count} missions` : null}
        right={
          <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>
            {dedupProgressData?.status === 'running' ? 'Dedup running…' : '30s refresh'}
          </span>
        }>
        <DeduplicationSection
          missions={missionDedupData?.missions}
          dedupProgress={dedupProgressData}
          onDeduplicate={handleDeduplicate}
          dedupData={dedupData}
          onReidTrigger={handleReidTrigger}
          reidLoading={reidLoading}
        />
      </Panel>

      {/* ENROLLMENT MODAL */}
      <EnrollmentModal
        isOpen={showEnrollModal}
        onClose={() => setShowEnrollModal(false)}
        arcfaceLoaded={arcfaceLoaded}
        arcfaceProvider={arcfaceProvider}
        onEnrolled={() => {
          refreshWatchlist && refreshWatchlist();
        }}
      />

      {/* FULL-RESOLUTION PREVIEW MODAL */}
      {previewModalItem && (
        <GalleryPreviewModal
          item={previewModalItem}
          isPersonGallery={false}
          bucket={previewModalItem.bucket || 'dvas-faces'}
          onClose={() => setPreviewModalItem(null)}
        />
      )}

    </div>
  );
}
