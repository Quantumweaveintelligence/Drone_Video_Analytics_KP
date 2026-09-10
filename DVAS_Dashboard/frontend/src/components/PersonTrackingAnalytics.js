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
} from 'lucide-react';
import {
  useAnalyticsFetch,
  usePersonTracks, usePersonDedup, usePersonIdentities,
  useFacesGallery, usePersonCount, usePersonLive, useAnalyticsSSE,
  usePersonDedupProgress, usePersonMissionDedup,
} from '../lib/useAnalytics';
import { useActiveStreams } from '../hooks/useActiveStreams';
import { useJpegPoller } from '../hooks/useJpegPoller';
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

/**
 * Rewrite an internal MinIO URL (http://minio:9000/BUCKET/KEY?...)
 * into a proxied URL (/api/minio/image?bucket=BUCKET&key=KEY) that the
 * browser can actually reach.  Also handles raw object_key strings.
 */
function toProxiedUrl(objectKeyOrUrl, bucket = 'dvas-faces') {
  if (!objectKeyOrUrl) return null;

  // Already a proxied URL — pass through
  if (objectKeyOrUrl.startsWith('/api/minio/')) return objectKeyOrUrl;

  // Internal MinIO presigned URL: http://minio:9000/<bucket>/<key>?...
  if (objectKeyOrUrl.startsWith('http://minio') || objectKeyOrUrl.startsWith('http://localhost')) {
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

function AnnotatedPersonFeedViewer({ activeDrone, setActiveDrone, drones, streamRunning, onStartStream, onStopStream, modelActive, startingStream, activeMissionId }) {
  const hasDrone = !!activeDrone;
  const [fps, setFps] = useState(0);
  const fpsRef = useRef({ count: 0, ts: Date.now() });
  const prevAnnotatedRef = useRef(null);

  const { streams } = useActiveStreams();
  const droneList = streams?.length > 0
    ? streams.map(s => ({ id: s.drone_id, ...s }))
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
              <button key={d.id} onClick={() => setActiveDrone(d.id)}
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
        {!modelActive && !streamRunning && (
          <span className="mono text-[10px] px-2.5 py-1 rounded-lg"
            style={{ color: '#64748b', background: 'var(--dvas-surface-2)', border: '1px solid var(--dvas-border)' }}>
            <Cpu size={10} className="inline mr-1 -mt-0.5" />
            Enable in Detection Models
          </span>
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
              YOLOv8m Person · YOLOv8n-face · ResNet50 Re-ID
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
function PersonKPIStrip({ tracks, dedupData, countData, sseConnected, liveFaceCount, activeMissionId }) {
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
    { label: 'Duplicates Removed', value: duplicates,                          tone: 'text-emerald-400', icon: Copy,        sub: `${dedupRate}% dedup rate` },
    { label: 'Live Stream',        value: sseConnected ? 'ON' : 'OFF',         tone: sseConnected ? 'text-emerald-400' : 'text-slate-500', icon: Radio, sub: activeMissionId ? `MSN: ${activeMissionId}` : (sseConnected ? 'Real-time SSE active' : 'Polling mode') },
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
                background: 'rgba(15,23,42,0.9)',
                border: `1px solid ${isSelected ? '#38bdf8' : 'rgba(51,65,85,0.8)'}`,
                transform: isSelected ? 'scale(1.04)' : 'scale(1)',
                zIndex: isSelected ? 10 : 1,
                filter: (isAnySelected && !isSelected) ? 'blur(2px) opacity(0.35) grayscale(40%)' : 'none',
                boxShadow: isSelected
                  ? '0 8px 24px -4px rgba(56,189,248,0.3)'
                  : '0 1px 4px rgba(0,0,0,0.4)',
              }}>

              {/* ── Card Header ── */}
              <div className="flex items-center justify-between px-2.5 py-1.5"
                style={{ background: 'rgba(30,41,59,0.75)', borderBottom: '1px solid rgba(51,65,85,0.6)' }}>
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
                style={{ background: 'rgba(15,23,42,0.97)', borderTop: '1px solid rgba(51,65,85,0.5)', padding: '6px 8px' }}>
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
                style={{ background: 'rgba(30,41,59,0.5)', borderTop: '1px solid rgba(51,65,85,0.5)' }}>
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
function DetectionChart({ sseWindow }) {
  const data = sseWindow.length > 0
    ? sseWindow
    : Array.from({ length: 20 }, (_, i) => ({ t: String(i), count: 0, faces: 0 }));

  const latest = sseWindow.length > 0 ? sseWindow[sseWindow.length - 1] : null;
  const currentPersons = latest?.count ?? 0;
  const currentFaces   = latest?.faces ?? 0;

  return (
    <div className="relative">
      {sseWindow.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center z-10">
          <span className="mono text-xs text-slate-500">Waiting for person detection events…</span>
        </div>
      )}
      {sseWindow.length > 0 && (
        <div className="flex items-center gap-4 mb-2 px-1">
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
      )}
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
   SECTION 5a — Gallery Mission Filter Bar
   ────────────────────────────────────────────────────────────── */
function GalleryMissionFilter({ missions, selected, onSelect, activeMissionId }) {
  const missionList = Array.isArray(missions) ? missions : [];
  const hasCurrentMission = activeMissionId && activeMissionId !== selected;

  return (
    <div className="flex items-center gap-2 flex-wrap mb-3">
      <select
        value={selected}
        onChange={(e) => onSelect(e.target.value)}
        className="mono text-[11px] px-2 py-1.5 rounded-lg"
        style={{
          background: 'var(--dvas-surface-2)',
          color: 'var(--dvas-text)',
          border: '1px solid var(--dvas-border)',
          outline: 'none',
          minWidth: 140,
        }}>
        <option value="">All Missions</option>
        {missionList.map(m => (
          <option key={m.mission_id} value={m.mission_id}>
            {m.mission_id} ({m.total_faces} faces)
          </option>
        ))}
      </select>

      {hasCurrentMission && (
        <button onClick={() => onSelect(activeMissionId)}
          className="flex items-center gap-1 px-2 py-1 rounded-lg mono text-[10px] font-semibold transition-all"
          style={{ background: 'rgba(45,212,191,0.1)', color: '#2dd4bf', border: '1px solid rgba(45,212,191,0.25)' }}>
          <Radio size={9} /> Current Mission
        </button>
      )}

      {selected && (
        <button onClick={() => onSelect('')}
          className="mono text-[10px] px-1.5 py-0.5 rounded transition-all"
          style={{ background: 'rgba(248,113,113,0.08)', color: '#f87171', border: '1px solid rgba(248,113,113,0.2)' }}>
          ✕ Clear
        </button>
      )}
      {selected && (
        <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>
          Filtered: {selected}
        </span>
      )}
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 5b — Gallery Card (extracted so useState is called at top level)
   ────────────────────────────────────────────────────────────── */
function GalleryCard({ item, i, isSelected, isAnySelected, onSelect, isPersonGallery, bucket }) {
  const [imgError, setImgError] = useState(false);
  const itemId   = item.identity_id || item.id || i;

  // For person gallery: prefer person body crop (dvas-evidence bucket)
  // For face gallery: use face crop (dvas-faces bucket)
  const personObjKey = item.best_person_image_key;
  const faceObjKey   = item.best_image_key || item.object_key;
  const faceRawUrl   = item.best_image_url || item.image_url || item.presigned_url || item.url;
  const personRawUrl = item.best_person_image_url;

  let imgUrl;
  if (isPersonGallery) {
    // Strictly use person body crop from dvas-faces bucket (persons/ prefix)
    imgUrl = personObjKey
      ? toProxiedUrl(personObjKey, 'dvas-faces')
      : personRawUrl
      ? toProxiedUrl(personRawUrl, 'dvas-faces')
      : null;
  } else {
    // Face gallery uses face crop from dvas-faces bucket
    imgUrl = faceObjKey
      ? toProxiedUrl(faceObjKey, bucket)
      : toProxiedUrl(faceRawUrl, bucket);
  }

  const clusterId = item.cluster_id != null ? `CL-${item.cluster_id}` : `ID-${i + 1}`;
  const drone     = item.drone_id || '';
  const msnId     = item.mission_id || '';
  const lastSeen  = item.last_seen || item.created_at || item.ts;
  const count     = item.embedding_count || 1;

  return (
    <div
      key={itemId}
      onClick={onSelect}
      className="flex flex-col rounded-xl overflow-hidden cursor-pointer transition-all duration-300"
      style={{
        background: 'rgba(15,23,42,0.9)',
        border: `1px solid ${isSelected ? (isPersonGallery ? '#38bdf8' : '#fbbf24') : 'rgba(51,65,85,0.8)'}`,
        transform: isSelected ? 'scale(1.05)' : 'scale(1)',
        zIndex: isSelected ? 10 : 1,
        filter: (isAnySelected && !isSelected) ? 'blur(2px) opacity(0.35) grayscale(40%)' : 'none',
        boxShadow: isSelected
          ? isPersonGallery ? '0 8px 24px -4px rgba(56,189,248,0.3)' : '0 8px 24px -4px rgba(251,191,36,0.3)'
          : '0 1px 4px rgba(0,0,0,0.4)',
      }}>
      {/* Header */}
      <div className="flex items-center justify-between px-2 py-1"
        style={{ background: 'rgba(30,41,59,0.8)', borderBottom: '1px solid rgba(51,65,85,0.5)' }}>
        <span className="font-mono font-bold text-[9px]" style={{ color: isPersonGallery ? '#38bdf8' : '#fbbf24' }}>
          {clusterId}
        </span>
        {count > 1 && (
          <span className="font-mono font-bold text-[8px] px-1 py-0.5 rounded"
            style={{ background: 'rgba(167,139,250,0.15)', color: '#a78bfa', border: '1px solid rgba(167,139,250,0.2)' }}>
            ×{count}
          </span>
        )}
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
              className="absolute inset-0 w-full h-full object-contain block"
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
      <div className="px-2 py-1.5 flex flex-col gap-0.5" style={{ background: 'rgba(15,23,42,0.85)' }}>
        {drone   && <span className="mono text-[9px] font-semibold" style={{ color: '#94a3b8' }}>{drone}</span>}
        {msnId   && <span className="mono text-[8px] truncate" style={{ color: '#64748b' }}>{msnId}</span>}
        <span className="mono text-[9px]" style={{ color: '#475569' }}>{fmtAge(lastSeen)}</span>
      </div>
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────
   SECTION 5c — Image Gallery wrapper
   ────────────────────────────────────────────────────────────── */
function ImageGallery({ items, loading, label = 'Items', bucket = 'dvas-faces', isPersonGallery = false }) {
  const [page, setPage] = useState(0);
  const [selectedId, setSelectedId] = useState(null);
  const perPage = 12;
  const arr = Array.isArray(items) ? items : [];
  const pages = Math.ceil(arr.length / perPage);
  const shown = arr.slice(page * perPage, page * perPage + perPage);
  const isAnySelected = selectedId !== null;

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
        <span className="mono text-[10px] text-slate-600">
          Images stored in MinIO will appear here after detection
        </span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))' }}>
        {shown.map((item, i) => {
          const itemId = item.identity_id || item.id || i;
          const isSelected = selectedId === String(itemId);
          return (
            <GalleryCard
              key={itemId}
              item={item}
              i={i}
              isSelected={isSelected}
              isAnySelected={isAnySelected}
              onSelect={() => setSelectedId(isSelected ? null : String(itemId))}
              isPersonGallery={isPersonGallery}
              bucket={bucket}
            />
          );
        })}
      </div>

      {/* Pagination */}
      {pages > 1 && (
        <div className="flex items-center justify-between pt-1">
          <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>
            {page * perPage + 1}–{Math.min((page + 1) * perPage, arr.length)} of {arr.length}
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
                  background: i === page ? 'rgba(56,189,248,0.15)' : 'var(--dvas-surface-2)',
                  color: i === page ? '#38bdf8' : 'var(--dvas-text-muted)',
                  border: `1px solid ${i === page ? 'rgba(56,189,248,0.3)' : 'var(--dvas-border)'}`,
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
   MAIN EXPORT
   ────────────────────────────────────────────────────────────── */
export default function PersonTrackingAnalytics({ drones }) {
  const [activeDrone, setActiveDrone] = useState(null);

  useEffect(() => {
    if (!drones || drones.length === 0) return;
    const currentStillValid = drones.some(d => d.id === activeDrone);
    if (!activeDrone || !currentStillValid) {
      const onlineDrone = drones.find(d => d.status !== 'offline');
      setActiveDrone(onlineDrone ? onlineDrone.id : drones[0].id);
    }
  }, [drones]); // eslint-disable-line react-hooks/exhaustive-deps

  const [reidLoading,          setReidLoading]          = useState(false);
  const [sseWindow,            setSseWindow]            = useState([]);
  const [startingStream,       setStartingStream]       = useState(false);
  const [liveFaceCount,        setLiveFaceCount]        = useState(0);
  const [galleryMissionFilter, setGalleryMissionFilter] = useState('');

  // Health
  const { data: healthData } = useAnalyticsFetch('/analytics/person/health', 5000);
  const streamRunning  = healthData?.stream_running ?? false;
  const modelActive    = healthData?.model_loaded  ?? false;
  const activeMissions = healthData?.active_missions ?? {};

  // Active mission from stream map
  const { streams: activeStreams, streamMap } = useActiveStreams();
  const activeMissionId = (() => {
    if (activeDrone && streamMap.has(activeDrone)) {
      return streamMap.get(activeDrone)?.mission_id || null;
    }
    return activeMissions[activeDrone] || null;
  })();

  // Data hooks
  const { data: tracksData,     loading: tracksLoading }   = usePersonTracks(activeDrone || '', 5000);
  const { data: dedupData }                                 = usePersonDedup(15000);
  const { data: identitiesData, loading: identLoading }    = usePersonIdentities(activeDrone || '', 10000, galleryMissionFilter);
  const { data: facesData,      loading: facesLoading }    = useFacesGallery(activeDrone || '', 10000, galleryMissionFilter);
  const { data: countData }                                 = usePersonCount(activeDrone || '', 5000);
  const { data: liveData,       loading: liveLoading }     = usePersonLive(activeDrone || '', 3000);

  // Mission dedup
  const { data: dedupProgressData }  = usePersonDedupProgress(2000);
  const { data: missionDedupData }   = usePersonMissionDedup(30000);

  const handleDeduplicate = useCallback(async (missionId) => {
    try {
      await api.post('/analytics/person/deduplicate', { mission_id: missionId });
    } catch (err) {
      console.error('Mission dedup trigger failed:', err);
    }
  }, []);

  // SSE
  const handleSSEEvent = useCallback((evt) => {
    if (evt?.service !== 'person') return;
    if (activeDrone && evt.drone_id && evt.drone_id !== activeDrone) return;
    const pCount = evt.data?.person_count ?? evt.data?.count ?? 0;
    const fCount = evt.data?.face_count ?? 0;
    const ts     = new Date().toISOString().slice(11, 19);
    setSseWindow(w => [...w.slice(-59), { t: ts, count: Number(pCount), faces: Number(fCount) }]);
    setLiveFaceCount(fc => fc + Number(fCount));
  }, [activeDrone]);

  const { connected: sseConnected } = useAnalyticsSSE(handleSSEEvent);

  // Stream start/stop
  const handleStartStream = useCallback(async () => {
    setStartingStream(true);
    try {
      await api.get('/analytics/person/stream/start');
    } catch (err) {
      console.error('Failed to start person stream:', err);
    } finally {
      setTimeout(() => setStartingStream(false), 3000);
    }
  }, []);

  const handleStopStream = useCallback(async () => {
    try {
      await api.get('/analytics/person/stream/stop');
    } catch (err) {
      console.error('Failed to stop person stream:', err);
    }
  }, []);

  // Re-ID
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

  // Normalise arrays
  const tracks     = Array.isArray(tracksData)            ? tracksData
    : Array.isArray(tracksData?.tracks)                   ? tracksData.tracks : [];
  const identities = Array.isArray(identitiesData)        ? identitiesData
    : Array.isArray(identitiesData?.identities)           ? identitiesData.identities : [];
  const faces      = Array.isArray(facesData)             ? facesData
    : Array.isArray(facesData?.gallery)                   ? facesData.gallery
    : Array.isArray(facesData?.faces)                     ? facesData.faces : [];

  // Live persons from in-memory buffer (has crop_b64 pairs)
  const livePersons = Array.isArray(liveData?.persons)
    ? liveData.persons
    : Array.isArray(liveData?.entries)
    ? liveData.entries
    : Array.isArray(liveData)
    ? liveData
    : [];

  return (
    <div className="flex flex-col gap-5" style={{ fontFamily: "'Plus Jakarta Sans', sans-serif" }}>

      {/* LIVE ANNOTATED FEED */}
      <AnnotatedPersonFeedViewer
        activeDrone={activeDrone}
        setActiveDrone={setActiveDrone}
        drones={drones}
        streamRunning={streamRunning}
        onStartStream={handleStartStream}
        onStopStream={handleStopStream}
        modelActive={modelActive}
        startingStream={startingStream}
        activeMissionId={activeMissionId}
      />

      {/* KPI STRIP */}
      <PersonKPIStrip
        tracks={tracks}
        dedupData={dedupData}
        countData={countData}
        sseConnected={sseConnected}
        liveFaceCount={liveFaceCount}
        activeMissionId={activeMissionId}
      />

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
            right={<span className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>SSE rolling window</span>}>
            <DetectionChart sseWindow={sseWindow} />
          </Panel>
        </div>
      </div>

      {/* PERSON IDENTITIES GALLERY (deduplicated, MinIO stored) */}
      <Panel
        title="Detected Persons — Image Gallery"
        icon={Eye}
        iconClass="text-sky-400"
        badge={identities.length > 0 ? `${identities.length} identities` : null}
        right={
          <span className="mono text-[10px]" style={{ color: 'var(--dvas-text-dim)' }}>
            {galleryMissionFilter ? 'Mission filtered' : 'Deduplicated'} · MinIO
          </span>
        }>
        <GalleryMissionFilter
          missions={missionDedupData?.missions}
          selected={galleryMissionFilter}
          onSelect={setGalleryMissionFilter}
          activeMissionId={activeMissionId}
        />
        <ImageGallery
          items={identities}
          loading={identLoading}
          label="Detected Persons"
          bucket="dvas-faces"
          isPersonGallery={true}
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
            {galleryMissionFilter ? 'Mission filtered' : 'Face crops'} · MinIO
          </span>
        }>
        <GalleryMissionFilter
          missions={missionDedupData?.missions}
          selected={galleryMissionFilter}
          onSelect={setGalleryMissionFilter}
          activeMissionId={activeMissionId}
        />
        <ImageGallery
          items={faces}
          loading={facesLoading}
          label="Detected Faces"
          bucket="dvas-faces"
          isPersonGallery={false}
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

    </div>
  );
}
