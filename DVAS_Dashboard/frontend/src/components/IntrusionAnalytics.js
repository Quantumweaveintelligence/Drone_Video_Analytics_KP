/**
 * IntrusionAnalytics.js
 * ─────────────────────────────────────────────────────────────────────────────
 * Comprehensive Intrusion Detection & Geofence Analytics Interface
 * Kerala Police | Drone Video Analytics System
 *
 * Dedicated to Command and Senior Officer roles (control_room_officer, senior_officer).
 * Connects end-to-end with the Intrusion Analytics microservice (port 8105).
 *
 * Modes:
 *   1. Live Monitoring & Alerts:
 *      - Live annotated video feed (geofence polygon + class bounding boxes + tactical HUD)
 *      - Real-time SSE alert cards (ENTERING, DWELLING, INSIDE, EXITED)
 *      - Zone armed status & WGS84 coordinate inspector
 *      - Stream controls (Start / Stop stream)
 *   2. Mission Review & Analytics:
 *      - Mission selector & 6 analytical KPI cards
 *      - Recharts temporal event timeline
 *      - Paginated intrusion event audit table
 *      - Export full analytics.json report
 *   3. Configuration & Sensitivity (Senior Officer):
 *      - Preset selection (default, confident, lenient, stable)
 *      - Confirm frames & dwell time threshold tuning
 *      - YOLO minimum confidence threshold tuning
 */

import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  Shield, ShieldAlert, Crosshair, Activity, Video, VideoOff,
  BarChart3, Clock, Settings, Download, RefreshCw, Play, Square,
  AlertTriangle, CheckCircle2, ChevronDown, ChevronLeft, ChevronRight,
  Maximize2, Server, Eye, MapPin, Layers, Radio, Cpu
} from 'lucide-react';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, Legend
} from 'recharts';
import { api, authFetch } from '@/lib/api';
import { useAnalyticsFetch, useAnalyticsSSE } from '@/lib/useAnalytics';
import { useActiveStreams } from '@/hooks/useActiveStreams';
import { useJpegPoller } from '@/hooks/useJpegPoller';



/* ─── Main IntrusionAnalytics Component ───────────────────────────── */
export default function IntrusionAnalytics({ activeDrone: initialDrone, drones = [], missions: initialMissions = [], user = {} }) {
  const { streams, streamMap } = useActiveStreams();

  // Dynamic drone list from active streams or backend drones (no hardcoding)
  const droneList = useMemo(() => {
    const list = [];
    const seen = new Set();

    // 1. Add active streams arriving at ingest
    if (streams && streams.length > 0) {
      streams.forEach(s => {
        const id = s.drone_id || s.id;
        if (id && !seen.has(id)) {
          seen.add(id);
          list.push({
            id,
            drone_id: id,
            name: s.name || id,
            mission_id: s.mission_id || null,
            status: s.status || 'live',
            isLive: true,
            frames_received: s.frames_received || s.frame_count || 0,
            messages_ready: s.messages_ready ?? 0,
            consumer_lag: s.consumer_lag || null,
          });
        }
      });
    }

    // 2. Add drones from DB/props
    if (drones && drones.length > 0) {
      drones.forEach(d => {
        const id = d.drone_id || d.id;
        if (id && !seen.has(id)) {
          seen.add(id);
          const isLive = d.status === 'live' || (d.frame_count || 0) > 0 || (d.messages_ready || 0) > 0;
          list.push({
            id,
            drone_id: id,
            name: d.name || d.label || id,
            mission_id: d.mission_id || null,
            status: d.status || 'offline',
            isLive,
            frames_received: d.frames_received || d.frame_count || 0,
            messages_ready: d.messages_ready ?? 0,
            consumer_lag: d.consumer_lag || null,
          });
        }
      });
    }

    return list;
  }, [streams, drones]);

  const [selectedDrone, setSelectedDrone] = useState(initialDrone || null);

  // Auto-select first live drone or first available drone
  useEffect(() => {
    if (droneList.length === 0) return;
    setSelectedDrone(prev => {
      if (prev && droneList.some(d => d.id === prev)) return prev;
      const live = droneList.find(d => d.isLive || d.status === 'live' || d.mission_id);
      return (live || droneList[0])?.id || null;
    });
  }, [droneList]);

  // Keep selectedDrone in sync if initialDrone changes
  useEffect(() => {
    if (initialDrone) {
      setSelectedDrone(initialDrone);
    }
  }, [initialDrone]);

  // Derive current drone object and active mission ID
  const selectedDroneObj = useMemo(() => {
    return droneList.find(d => d.id === selectedDrone) || null;
  }, [droneList, selectedDrone]);

  const activeMissionId = useMemo(() => {
    if (!selectedDrone) return null;
    if (streamMap && streamMap.has(selectedDrone)) {
      return streamMap.get(selectedDrone)?.mission_id || null;
    }
    return selectedDroneObj?.mission_id || null;
  }, [selectedDrone, streamMap, selectedDroneObj]);

  // Mode switcher: 'live' | 'review' | 'config'
  const [subTab, setSubTab] = useState('live');

  // Service health polling (every 8s)
  const { data: health, mutate: mutateHealth } = useAnalyticsFetch('/analytics/intrusion/health', 8000);
  const isServiceHealthy = health?.status === 'ok';
  const isStreamRunning = Boolean(health?.stream_running);

  // Zone status polling for selected drone (every 4s)
  const { data: zoneStatus, mutate: mutateZone } = useAnalyticsFetch(
    selectedDrone ? `/analytics/intrusion/zone/status?drone_id=${encodeURIComponent(selectedDrone)}` : null,
    4000
  );

  // ─── Real-time SSE alerts ──────────────────────────────────────────
  const [liveAlerts, setLiveAlerts] = useState([]);
  const [liveStats, setLiveStats] = useState({
    activeIntrusions: 0,
    totalBreachesToday: 0,
    lastBreachTime: null,
  });

  // ─── Initial historical events fetch when selected drone changes ───────
  useEffect(() => {
    if (!selectedDrone) return;
    let cancelled = false;
    api.get(`/analytics/intrusion/events?drone_id=${encodeURIComponent(selectedDrone)}&limit=25`)
      .then(res => {
        if (cancelled) return;
        const events = res.data?.events || [];
        if (events.length > 0) {
          const items = events.map(e => ({
            id: e.event_id || `${Date.now()}-${Math.random().toString(36).substring(2, 7)}`,
            ts: e.ts || new Date().toISOString(),
            trackId: e.track_id || 'N/A',
            className: e.track_class || 'Target',
            state: (e.state_to || e.state || 'INSIDE').toUpperCase(),
            confidence: Number(e.confidence || 0.85),
            dwell: Number(e.dwell_duration_s || 0),
            latitude: e.latitude,
            longitude: e.longitude,
            snapshotKey: e.snapshot_key,
          }));
          setLiveAlerts(prev => {
            const existingIds = new Set(prev.map(p => p.id));
            const newFromDb = items.filter(it => !existingIds.has(it.id));
            return [...prev, ...newFromDb].slice(0, 80);
          });
          setLiveStats(prev => ({
            ...prev,
            totalBreachesToday: Math.max(prev.totalBreachesToday, events.length),
            lastBreachTime: events[0]?.ts || prev.lastBreachTime,
          }));
        }
      })
      .catch(() => {
        // Silently ignore if events table is empty or error
      });
    return () => { cancelled = true; };
  }, [selectedDrone]);

  const handleSSEEvent = useCallback((event) => {
    try {
      // event is already a parsed JS object from useAnalyticsSSE, but handle both object and string
      const raw = typeof event === 'string' ? JSON.parse(event) : event;
      if (!raw) return;

      const stream = raw.stream;
      const payload = raw.payload || raw;

      // Case 1: Confirmed intrusion event from drone:events:confirmed (or direct alert)
      if (
        payload.event_type === 'intrusion_alert' ||
        payload.state_to ||
        (payload.state && (stream === 'events' || stream === 'intrusion'))
      ) {
        const item = {
          id: payload.event_id || `${Date.now()}-${Math.random().toString(36).substring(2, 7)}`,
          ts: payload.ts || new Date().toISOString(),
          trackId: payload.track_id || payload.id || 'N/A',
          className: payload.track_class || payload.class_name || payload.class || 'Target',
          state: (payload.state_to || payload.state || 'INSIDE').toUpperCase(),
          confidence: Number(payload.confidence || 0.85),
          dwell: Number(payload.dwell_duration_s || payload.dwell || 0),
          latitude: payload.latitude,
          longitude: payload.longitude,
          snapshotKey: payload.snapshot_key,
        };

        setLiveAlerts(prev => [item, ...prev.filter(x => x.id !== item.id)].slice(0, 80));

        setLiveStats(prev => {
          const isEntering = item.state === 'ENTERING' || item.state === 'INSIDE';
          const isExited = item.state === 'EXITED';
          return {
            activeIntrusions: isEntering
              ? prev.activeIntrusions + 1
              : isExited ? Math.max(0, prev.activeIntrusions - 1) : prev.activeIntrusions,
            totalBreachesToday: isEntering ? prev.totalBreachesToday + 1 : prev.totalBreachesToday,
            lastBreachTime: item.ts,
          };
        });
        return;
      }

      // Case 2: Frame results from analytics:intrusion:results (stream === 'intrusion')
      if (stream === 'intrusion') {
        const breaches = Array.isArray(payload.active_breaches)
          ? payload.active_breaches
          : Array.isArray(payload.detections)
          ? payload.detections.filter(d => d.is_intruding || (d.intrusion_state && d.intrusion_state !== 'OUTSIDE'))
          : [];

        if (breaches.length > 0) {
          const newAlerts = breaches.map(b => ({
            id: `live-${b.track_id}-${payload.frame_sequence || Math.floor(Date.now() / 2000)}`,
            ts: payload.ts || new Date().toISOString(),
            trackId: b.track_id,
            className: b.class || b.track_class || 'Target',
            state: (b.intrusion_state || b.state || 'INSIDE').toUpperCase(),
            confidence: Number(b.confidence || 0.85),
            dwell: Number(b.dwell_duration_s || 0),
          }));

          setLiveAlerts(prev => {
            const existingIds = new Set(prev.map(p => p.id));
            const toAdd = newAlerts.filter(a => !existingIds.has(a.id));
            return toAdd.length > 0 ? [...toAdd, ...prev].slice(0, 80) : prev;
          });
        }

        if (typeof payload.active_intrusions === 'number') {
          setLiveStats(prev => ({
            ...prev,
            activeIntrusions: payload.active_intrusions,
          }));
        }
      }
    } catch (e) {
      console.warn('[IntrusionSSE] Error handling event:', e);
    }
  }, []);

  useAnalyticsSSE(handleSSEEvent, selectedDrone);

  // ─── Annotated Frame Poller ────────────────────────────────────────
  const pollUrl = selectedDrone ? `/api/analytics/intrusion/annotated?drone_id=${encodeURIComponent(selectedDrone)}` : null;
  const { blobUrl, hasFrame, feedDead, fps } = useJpegPoller(pollUrl, 800, subTab === 'live');

  // ─── Stream Control Actions ────────────────────────────────────────
  const [streamToggling, setStreamToggling] = useState(false);

  const handleStartStream = async () => {
    setStreamToggling(true);
    try {
      await api.get('/analytics/intrusion/stream/start');
      mutateHealth();
    } catch (err) {
      console.error('Failed to start intrusion stream:', err);
    } finally {
      setStreamToggling(false);
    }
  };

  const handleStopStream = async () => {
    setStreamToggling(true);
    try {
      await api.get('/analytics/intrusion/stream/stop');
      mutateHealth();
    } catch (err) {
      console.error('Failed to stop intrusion stream:', err);
    } finally {
      setStreamToggling(false);
    }
  };

  // ─── Post-Mission Review State & Data ──────────────────────────────
  const [missions, setMissions] = useState([]);
  const [selectedMission, setSelectedMission] = useState('');
  const [events, setEvents] = useState([]);
  const [analyticsData, setAnalyticsData] = useState(null);
  const [loadingReview, setLoadingReview] = useState(false);
  const [eventFilter, setEventFilter] = useState('ALL');
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 10;

  // Load distinct missions from BOTH /api/missions and /analytics/intrusion/events
  useEffect(() => {
    let active = true;
    const fetchMissions = async () => {
      try {
        const [missionsRes, eventsRes] = await Promise.allSettled([
          api.get('/missions'),
          api.get('/analytics/intrusion/events?limit=200'),
        ]);

        const discovered = new Set();

        // 1. From /api/missions
        if (missionsRes.status === 'fulfilled' && missionsRes.value?.data) {
          const mData = missionsRes.value.data;
          const mArray = Array.isArray(mData) ? mData : (Array.isArray(mData?.missions) ? mData.missions : []);
          mArray.forEach(m => {
            const mId = typeof m === 'string' ? m : (m.mission_id || m.id || m.name);
            if (mId) discovered.add(mId);
          });
        }

        // 2. From initialMissions prop
        if (Array.isArray(initialMissions)) {
          initialMissions.forEach(m => {
            const mId = typeof m === 'string' ? m : (m.mission_id || m.id || m.name);
            if (mId) discovered.add(mId);
          });
        }

        // 3. From active streams / active mission
        if (activeMissionId) discovered.add(activeMissionId);
        if (streams && streams.length > 0) {
          streams.forEach(s => {
            if (s.mission_id) discovered.add(s.mission_id);
          });
        }

        // 4. From /analytics/intrusion/events
        if (eventsRes.status === 'fulfilled' && eventsRes.value?.data) {
          const eData = eventsRes.value.data;
          const eArray = Array.isArray(eData) ? eData : (Array.isArray(eData?.events) ? eData.events : []);
          eArray.forEach(e => {
            if (e.mission_id) discovered.add(e.mission_id);
          });
        }

        if (!active) return;
        const allMissions = Array.from(discovered);
        setMissions(allMissions);

        // Pre-select active mission or first mission if none selected
        setSelectedMission(prev => {
          if (prev && discovered.has(prev)) return prev;
          if (activeMissionId && discovered.has(activeMissionId)) return activeMissionId;
          return allMissions.length > 0 ? allMissions[0] : '';
        });
      } catch (err) {
        console.error('Error fetching missions:', err);
      }
    };
    fetchMissions();
    return () => { active = false; };
  }, [activeMissionId, streams, initialMissions]);

  // When active mission becomes available and user switches to review, pre-select it
  useEffect(() => {
    if (activeMissionId && subTab === 'review') {
      setSelectedMission(prev => prev || activeMissionId);
    }
  }, [activeMissionId, subTab]);

  // Load events & analytics for selected mission
  useEffect(() => {
    if (!selectedMission || subTab !== 'review') return;
    let active = true;
    setLoadingReview(true);
    const fetchMissionData = async () => {
      try {
        const [evRes, anRes] = await Promise.allSettled([
          api.get(`/analytics/intrusion/events?mission_id=${encodeURIComponent(selectedMission)}&limit=200`),
          api.get(`/analytics/intrusion/analytics?mission_id=${encodeURIComponent(selectedMission)}`),
        ]);

        if (!active) return;

        let evList = [];
        if (evRes.status === 'fulfilled' && evRes.value?.data) {
          const raw = evRes.value.data;
          evList = Array.isArray(raw) ? raw : (Array.isArray(raw?.events) ? raw.events : []);
        }
        setEvents(evList);

        if (anRes.status === 'fulfilled' && anRes.value?.data) {
          setAnalyticsData(anRes.value.data);
        } else {
          setAnalyticsData(null);
        }
      } catch (err) {
        console.error('Error fetching mission review data:', err);
        if (active) {
          setEvents([]);
          setAnalyticsData(null);
        }
      } finally {
        if (active) setLoadingReview(false);
      }
    };
    fetchMissionData();
    return () => { active = false; };
  }, [selectedMission, subTab]);

  // Review KPI calculations
  const kpis = useMemo(() => {
    const evArr = Array.isArray(events) ? events : [];
    const totalEvents = evArr.length;
    const uniqueTracks = new Set(evArr.map(e => e.track_id).filter(Boolean)).size;
    const dwellTimes = evArr.map(e => Number(e.dwell_duration_s) || 0).filter(d => d > 0);
    const avgDwell = dwellTimes.length > 0
      ? (dwellTimes.reduce((a, b) => a + b, 0) / dwellTimes.length).toFixed(1)
      : '0.0';

    const classCounts = evArr.reduce((acc, e) => {
      const cls = (e.track_class || 'UNKNOWN').toUpperCase();
      acc[cls] = (acc[cls] || 0) + 1;
      return acc;
    }, {});

    const topClass = Object.entries(classCounts).sort((a, b) => b[1] - a[1])[0];

    return {
      totalEvents,
      uniqueTracks,
      avgDwell: `${avgDwell}s`,
      topClass: topClass ? `${topClass[0]} (${topClass[1]})` : '—',
      activeIntrusions: evArr.filter(e => e.state_to === 'INSIDE' || e.state_to === 'DWELLING').length,
    };
  }, [events]);

  // Timeline chart data
  const chartData = useMemo(() => {
    const evArr = Array.isArray(events) ? events : [];
    if (evArr.length === 0) return [];
    // Group events into time buckets (minute intervals)
    const buckets = {};
    evArr.forEach(e => {
      let timeKey = '00:00';
      try {
        if (e.ts) {
          timeKey = new Date(e.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        }
      } catch (err) {
        timeKey = '00:00';
      }
      if (!buckets[timeKey]) {
        buckets[timeKey] = { time: timeKey, ENTERING: 0, DWELLING: 0, INSIDE: 0, EXITED: 0 };
      }
      const st = (e.state_to || 'INSIDE').toUpperCase();
      if (buckets[timeKey][st] !== undefined) {
        buckets[timeKey][st] += 1;
      }
    });
    return Object.values(buckets).slice(-15);
  }, [events]);

  // Filtered and paginated events table
  const filteredEvents = useMemo(() => {
    const evArr = Array.isArray(events) ? events : [];
    if (eventFilter === 'ALL') return evArr;
    return evArr.filter(e => (e.state_to || '').toUpperCase() === eventFilter);
  }, [events, eventFilter]);

  const paginatedEvents = useMemo(() => {
    const start = page * PAGE_SIZE;
    return filteredEvents.slice(start, start + PAGE_SIZE);
  }, [filteredEvents, page]);

  const totalPages = Math.max(1, Math.ceil(filteredEvents.length / PAGE_SIZE));

  // Export mission analytics JSON
  const handleExportJson = () => {
    if (!selectedMission) return;
    const jsonStr = JSON.stringify(analyticsData || { mission_id: selectedMission, events }, null, 2);
    const blob = new Blob([jsonStr], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `intrusion_analytics_${selectedMission}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // ─── Configuration & Sensitivity (Senior Officer) ─────────────────
  const [configPreset, setConfigPreset] = useState('default');
  const [confirmFrames, setConfirmFrames] = useState(3);
  const [dwellThreshold, setDwellThreshold] = useState(3.0);
  const [minConfidence, setMinConfidence] = useState(0.25);
  const [savingConfig, setSavingConfig] = useState(false);
  const [configFeedback, setConfigFeedback] = useState(null);

  const handleApplyConfig = async () => {
    setSavingConfig(true);
    try {
      await api.post('/analytics/intrusion/config', {
        preset: configPreset,
        confirm_frames: Number(confirmFrames),
        dwell_threshold_s: Number(dwellThreshold),
        min_confidence: Number(minConfidence),
      });
      setConfigFeedback({ type: 'success', text: 'Sensitivity configuration updated successfully.' });
      setTimeout(() => setConfigFeedback(null), 3500);
    } catch (err) {
      setConfigFeedback({ type: 'error', text: 'Failed to update sensitivity configuration.' });
      setTimeout(() => setConfigFeedback(null), 3500);
    } finally {
      setSavingConfig(false);
    }
  };

  return (
    <div className="flex flex-col gap-4 font-sans text-slate-200">
      
      {/* ── Top Header & Mode Navigation ─────────────────────────────── */}
      <div
        className="rounded-xl p-4 flex flex-wrap items-center justify-between gap-4"
        style={{ background: 'var(--dvas-surface)', border: '1px solid var(--dvas-border)' }}
      >
        {/* Left: Title & Active Drone Switcher */}
        <div className="flex items-center gap-4 flex-wrap">
          <div className="flex items-center gap-2.5">
            <div className="w-9 h-9 rounded-lg bg-cyan-500/10 border border-cyan-500/30 flex items-center justify-center">
              <Shield className="w-5 h-5 text-cyan-400" />
            </div>
            <div>
              <div className="text-sm font-semibold tracking-wide flex items-center gap-2 text-slate-100">
                <span>INTRUSION DETECTION & GEOFENCE OVERSIGHT</span>
                <span className="mono text-[10px] px-2 py-0.5 rounded font-bold bg-cyan-500/20 text-cyan-400 border border-cyan-500/30">
                  GEOTRAX YOLOv8s
                </span>
              </div>
              <div className="text-xs text-slate-400 flex items-center gap-3">
                <span>PORT 8105</span>
                <span>•</span>
                <span className="flex items-center gap-1">
                  <span className={`w-2 h-2 rounded-full ${isServiceHealthy ? 'bg-emerald-400' : 'bg-red-400'}`} />
                  {isServiceHealthy ? 'SERVICE ONLINE' : 'SERVICE OFFLINE'}
                </span>
                <span>•</span>
                <span className={isStreamRunning ? 'text-emerald-400 font-medium' : 'text-slate-400'}>
                  {isStreamRunning ? 'INFERENCE ACTIVE' : 'STREAM IDLE'}
                </span>
              </div>
            </div>
          </div>

          {/* Drone & Live Mission Selector */}
          <div className="flex items-center gap-3 border-l border-slate-700/60 pl-4 flex-wrap">
            <div className="flex flex-col gap-0.5">
              <span className="text-[10px] text-slate-400 font-mono tracking-wider">LIVE DRONE / STREAM:</span>
              <div className="flex gap-1.5 flex-wrap items-center">
                {droneList.length > 0 ? (
                  droneList.map(d => {
                    const isSelected = selectedDrone === d.id;
                    const isLive = d.isLive || d.status === 'live';
                    const isDraining = d.status === 'draining';
                    return (
                      <button
                        key={d.id}
                        onClick={() => setSelectedDrone(d.id)}
                        className={`mono text-xs px-2.5 py-1 rounded transition-all flex items-center gap-1.5 ${
                          isSelected
                            ? 'bg-cyan-500 text-slate-950 font-bold shadow-[0_0_12px_rgba(6,182,212,0.4)]'
                            : 'bg-slate-800/80 text-slate-300 hover:bg-slate-700/80 hover:text-white border border-slate-700/40'
                        }`}
                        title={`Drone: ${d.id}${d.mission_id ? ` · Mission: ${d.mission_id}` : ''}${isLive ? ' (Live Stream)' : ''}`}
                      >
                        {isDraining ? (
                          <span className={`w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse`} />
                        ) : (
                          <span className={`w-1.5 h-1.5 rounded-full ${isLive ? 'bg-emerald-400 animate-pulse' : (isSelected ? 'bg-slate-950' : 'bg-slate-500')}`} />
                        )}
                        <span>{d.id}</span>
                        {d.mission_id && (
                          <span className={`text-[10px] px-1 py-0.2 rounded font-mono ${isSelected ? 'bg-slate-950/40 text-slate-900' : 'bg-slate-900 text-cyan-400'}`}>
                            {d.mission_id}
                          </span>
                        )}
                        {isDraining && (
                          <span className="text-[8px] bg-amber-800/60 text-amber-300 px-1.5 py-0.5 rounded ml-1 font-bold">DRAINING</span>
                        )}
                      </button>
                    );
                  })
                ) : (
                  <span className="mono text-[11px] text-slate-500 italic flex items-center gap-1.5">
                    <Radio className="w-3 h-3 text-slate-500 animate-pulse" />
                    No active drone streams detected
                  </span>
                )}
              </div>
            </div>

            {/* Current Mission HUD pill */}
            {activeMissionId && (
              <div className="flex flex-col gap-0.5 border-l border-slate-800 pl-3">
                <span className="text-[10px] text-slate-500 font-mono tracking-wider">CURRENT MISSION:</span>
                <span className="mono text-xs font-bold text-violet-300 bg-violet-950/60 border border-violet-700/50 px-2 py-0.5 rounded flex items-center gap-1">
                  <Activity className="w-3 h-3 text-violet-400" />
                  {activeMissionId}
                </span>
              </div>
            )}
          </div>
        </div>

        {/* Right: Sub-tab Switcher & Stream Controls */}
        <div className="flex items-center gap-3 flex-wrap">
          {/* Sub-tab Switcher */}
          <div className="flex bg-slate-900/90 rounded-lg p-1 border border-slate-800">
            <button
              onClick={() => setSubTab('live')}
              className={`px-3 py-1.5 rounded text-xs font-semibold flex items-center gap-1.5 transition-all ${
                subTab === 'live'
                  ? 'bg-cyan-500 text-slate-950 font-bold shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Video className="w-3.5 h-3.5" />
              <span>Live Monitoring</span>
            </button>
            <button
              onClick={() => setSubTab('review')}
              className={`px-3 py-1.5 rounded text-xs font-semibold flex items-center gap-1.5 transition-all ${
                subTab === 'review'
                  ? 'bg-cyan-500 text-slate-950 font-bold shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <BarChart3 className="w-3.5 h-3.5" />
              <span>Mission Review</span>
            </button>
            {user?.role === 'senior_officer' && (
              <button
                onClick={() => setSubTab('config')}
                className={`px-3 py-1.5 rounded text-xs font-semibold flex items-center gap-1.5 transition-all ${
                  subTab === 'config'
                    ? 'bg-cyan-500 text-slate-950 font-bold shadow-sm'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                <Settings className="w-3.5 h-3.5" />
                <span>Sensitivity Config</span>
              </button>
            )}
          </div>

          {/* Stream Start/Stop Toggle */}
          {isStreamRunning ? (
            <button
              onClick={handleStopStream}
              disabled={streamToggling}
              className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-red-500/20 text-red-400 border border-red-500/40 hover:bg-red-500/30 flex items-center gap-1.5 transition-all"
            >
              <Square className="w-3.5 h-3.5 fill-current" />
              <span>Stop Stream</span>
            </button>
          ) : (
            <button
              onClick={handleStartStream}
              disabled={streamToggling}
              className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 hover:bg-emerald-500/30 flex items-center gap-1.5 transition-all"
            >
              <Play className="w-3.5 h-3.5 fill-current" />
              <span>Start Stream</span>
            </button>
          )}
        </div>
      </div>

      {/* ── MODE 1: LIVE MONITORING & REAL-TIME ALERTS ───────────────── */}
      {subTab === 'live' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          
          {/* Left Column (2 cols): Video Player & Live Feed */}
          <div className="lg:col-span-2 flex flex-col gap-4">
            <div
              className="rounded-xl overflow-hidden flex flex-col"
              style={{ background: 'var(--dvas-surface)', border: '1px solid var(--dvas-border)' }}
            >
              {/* Video Header Bar */}
              <div className="px-4 py-2.5 bg-slate-900/80 border-b border-slate-800 flex items-center justify-between text-xs font-mono">
                <div className="flex items-center gap-2.5">
                  <span className={`w-2 h-2 rounded-full ${feedDead ? 'bg-red-500' : 'bg-emerald-400 animate-ping'}`} />
                  <span className="font-semibold text-slate-200">
                    {feedDead ? 'FEED STANDBY' : `LIVE ANNOTATED STREAM — ${selectedDrone}`}
                  </span>
                </div>
                <div className="flex items-center gap-3 text-slate-400">
                  <span className="bg-slate-950 px-2 py-0.5 rounded border border-slate-800">
                    FPS: <strong className="text-emerald-400">{fps}</strong>
                  </span>
                  <span className="bg-slate-950 px-2 py-0.5 rounded border border-slate-800">
                    3840×2160
                  </span>
                </div>
              </div>

              {(() => {
                const selectedDroneObj = droneList.find(d => d.id === selectedDrone);
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

              {/* Video Player Display */}
              <div className="relative bg-black flex items-center justify-center min-h-[420px] max-h-[560px] overflow-hidden">
                {hasFrame && blobUrl ? (
                  <img
                    src={blobUrl}
                    alt="Live Intrusion Video"
                    className="w-full h-full object-contain max-h-[560px]"
                  />
                ) : (
                  <div className="flex flex-col items-center justify-center gap-3 text-slate-500 p-8 text-center">
                    <VideoOff className="w-12 h-12 text-slate-600" />
                    <div className="text-sm font-mono text-slate-400">
                      {isStreamRunning
                        ? 'WAITING FOR FRAMES WITH ARMED GEOFENCE...'
                        : 'INTRUSION STREAM PAUSED'}
                    </div>
                    <div className="text-xs text-slate-500 max-w-md">
                      The Edge operator marks the restricted zone on the GPS & Telemetry map and enables{' '}
                      <span className="text-cyan-400 font-semibold">SERVER SEND: ON</span>. Detections and geofence overlays will appear here automatically.
                    </div>
                  </div>
                )}

                {/* Tactical HUD Overlay Tag */}
                {zoneStatus?.is_loaded && (
                  <div className="absolute top-3 left-3 bg-slate-950/85 backdrop-blur border border-cyan-500/40 px-3 py-1.5 rounded-lg flex items-center gap-2 text-xs font-mono text-cyan-400 shadow-md">
                    <Shield className="w-4 h-4 text-cyan-400" />
                    <span>GEOFENCE ARMED ({zoneStatus.vertex_count} VERTICES)</span>
                  </div>
                )}
              </div>

              {/* Video Footer Telemetry Bar */}
              <div className="px-4 py-2 bg-slate-950/90 border-t border-slate-800 text-xs font-mono flex items-center justify-between text-slate-400 flex-wrap gap-2">
                <div className="flex items-center gap-3">
                  <span>DRONE: <strong className="text-slate-200">{selectedDrone}</strong></span>
                  <span>•</span>
                  <span>STATUS:{' '}
                    <strong className={zoneStatus?.is_loaded ? 'text-emerald-400' : 'text-amber-400'}>
                      {zoneStatus?.is_loaded ? 'MONITORING ARMED' : 'WAITING FOR EDGE ZONE'}
                    </strong>
                  </span>
                </div>
                <div>
                  <span className="text-slate-500">MODEL: </span>
                  <span className="text-slate-300">geotrax_hbb_yolov8s_1920_v1.pt</span>
                </div>
              </div>
            </div>

            {/* Zone Status & Geofence Details Card */}
            <div
              className="rounded-xl p-4 flex flex-col gap-3"
              style={{ background: 'var(--dvas-surface)', border: '1px solid var(--dvas-border)' }}
            >
              <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                <div className="flex items-center gap-2 text-xs font-semibold text-slate-200 uppercase tracking-wider">
                  <Layers className="w-4 h-4 text-cyan-400" />
                  <span>Restricted Zone Parameterization</span>
                </div>
                <span className="text-xs font-mono text-slate-400">
                  {zoneStatus?.zone_id || 'NO ACTIVE ZONE'}
                </span>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs font-mono">
                <div className="bg-slate-900/60 p-2.5 rounded border border-slate-800">
                  <div className="text-slate-500 text-[10px]">ZONE STATUS</div>
                  <div className={`font-bold mt-1 ${zoneStatus?.is_loaded ? 'text-emerald-400' : 'text-amber-400'}`}>
                    {zoneStatus?.is_loaded ? 'ARMED & TRACKING' : 'IDLE (NO ZONE)'}
                  </div>
                </div>
                <div className="bg-slate-900/60 p-2.5 rounded border border-slate-800">
                  <div className="text-slate-500 text-[10px]">POLYGON VERTICES</div>
                  <div className="font-bold text-slate-200 mt-1">{zoneStatus?.vertex_count || 0} POINTS</div>
                </div>
                <div className="bg-slate-900/60 p-2.5 rounded border border-slate-800">
                  <div className="text-slate-500 text-[10px]">ACTIVE INTRUDERS</div>
                  <div className={`font-bold mt-1 ${liveStats.activeIntrusions > 0 ? 'text-red-400' : 'text-slate-200'}`}>
                    {liveStats.activeIntrusions} TARGETS
                  </div>
                </div>
                <div className="bg-slate-900/60 p-2.5 rounded border border-slate-800">
                  <div className="text-slate-500 text-[10px]">TOTAL BREACHES</div>
                  <div className="font-bold text-cyan-400 mt-1">{liveStats.totalBreachesToday} EVENTS</div>
                </div>
              </div>

              {zoneStatus?.coordinates && zoneStatus.coordinates.length > 0 && (
                <div className="bg-slate-950/70 p-2 rounded border border-slate-800 text-[11px] font-mono text-slate-400 overflow-x-auto">
                  <span className="text-slate-500">WGS84 Coordinates: </span>
                  {zoneStatus.coordinates.map((c, i) => (
                    <span key={i} className="mr-2 text-cyan-400/90">
                      [{c[0]?.toFixed(5)}, {c[1]?.toFixed(5)}]
                    </span>
                  ))}
                </div>
              )}
            </div>

          </div>

          {/* Right Column (1 col): Real-Time SSE Breach Alerts Feed */}
          <div
            className="rounded-xl flex flex-col h-full min-h-[500px]"
            style={{ background: 'var(--dvas-surface)', border: '1px solid var(--dvas-border)' }}
          >
            {/* Alert Panel Header */}
            <div className="px-4 py-3 bg-slate-900/90 border-b border-slate-800 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <ShieldAlert className="w-4 h-4 text-red-400" />
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-200">
                  Live Intrusion Events
                </span>
              </div>
              <span className="mono text-[10px] px-2 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">
                {liveAlerts.length} STREAMED
              </span>
            </div>

            {/* Scrolling Alerts Feed */}
            <div className="flex-1 p-3 overflow-y-auto space-y-2.5 max-h-[640px]">
              {liveAlerts.length === 0 ? (
                <div className="h-full flex flex-col items-center justify-center text-slate-600 gap-3 py-16">
                  <Crosshair className="w-10 h-10 opacity-30 text-cyan-500" />
                  <div className="text-xs font-mono text-center">
                    NO ACTIVE BREACHES DETECTED<br />
                    <span className="text-[10px] text-slate-600">Events stream live when targets cross the zone</span>
                  </div>
                </div>
              ) : (
                liveAlerts.map(a => {
                  const state = (a.state || 'INSIDE').toUpperCase();
                  const isBreach = state === 'ENTERING' || state === 'INSIDE' || state === 'DWELLING';

                  return (
                    <div
                      key={a.id}
                      className={`p-3 rounded-lg border text-xs font-mono transition-all ${
                        state === 'ENTERING'
                          ? 'bg-red-500/15 border-red-500/50 text-red-300'
                          : state === 'DWELLING'
                          ? 'bg-amber-500/15 border-amber-500/50 text-amber-300'
                          : state === 'EXITED'
                          ? 'bg-emerald-500/15 border-emerald-500/50 text-emerald-300'
                          : 'bg-cyan-500/15 border-cyan-500/50 text-cyan-300'
                      }`}
                    >
                      <div className="flex items-center justify-between font-bold mb-1">
                        <div className="flex items-center gap-1.5">
                          {state === 'ENTERING' && <ShieldAlert className="w-4 h-4 text-red-400" />}
                          {state === 'DWELLING' && <AlertTriangle className="w-4 h-4 text-amber-400" />}
                          {state === 'EXITED' && <CheckCircle2 className="w-4 h-4 text-emerald-400" />}
                          {state === 'INSIDE' && <Crosshair className="w-4 h-4 text-cyan-400" />}
                          <span>{state}</span>
                        </div>
                        <span className="text-[10px] opacity-70 flex items-center gap-1">
                          <Clock className="w-3 h-3" />
                          {a.ts ? new Date(a.ts).toLocaleTimeString() : 'NOW'}
                        </span>
                      </div>

                      <div className="grid grid-cols-2 gap-1.5 mt-2 pt-2 border-t border-current/20 text-[11px]">
                        <div>
                          <span className="opacity-60">TARGET:</span>{' '}
                          <strong className="text-white font-bold">#{a.trackId}</strong>
                        </div>
                        <div>
                          <span className="opacity-60">CLASS:</span>{' '}
                          <strong className="text-white">{a.className}</strong>
                        </div>
                        <div>
                          <span className="opacity-60">CONF:</span>{' '}
                          <span>{(a.confidence * 100).toFixed(0)}%</span>
                        </div>
                        <div>
                          <span className="opacity-60">DWELL:</span>{' '}
                          <span>{a.dwell ? `${Number(a.dwell).toFixed(1)}s` : '0.0s'}</span>
                        </div>
                      </div>
                    </div>
                  );
                })
              )}
            </div>

            {/* Health & Socket Footer */}
            <div className="px-3 py-2 bg-slate-950 border-t border-slate-800 text-[11px] font-mono flex items-center justify-between text-slate-400">
              <span className="flex items-center gap-1">
                <Server className="w-3 h-3 text-cyan-400" />
                GATEWAY SSE CONNECTED
              </span>
              <span className="text-slate-500">PORT 8100/8105</span>
            </div>

          </div>

        </div>
      )}

      {/* ── MODE 2: MISSION REVIEW & HISTORICAL ANALYTICS ────────────── */}
      {subTab === 'review' && (
        <div className="flex flex-col gap-4">
          
          {/* Mission Selector & Export Bar */}
          <div
            className="rounded-xl p-4 flex flex-wrap items-center justify-between gap-4"
            style={{ background: 'var(--dvas-surface)', border: '1px solid var(--dvas-border)' }}
          >
            <div className="flex items-center gap-3">
              <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
                SELECT MISSION:
              </span>
              <div className="relative">
                <select
                  value={selectedMission}
                  onChange={e => setSelectedMission(e.target.value)}
                  className="bg-slate-900 border border-slate-700 text-slate-200 text-xs rounded-lg px-3 py-1.5 pr-8 appearance-none font-mono focus:border-cyan-500 focus:outline-none"
                >
                  {missions && missions.length > 0 ? (
                    missions.map(m => {
                      const id = typeof m === 'string' ? m : (m.mission_id || m.id || m.name);
                      const label = typeof m === 'string' ? m : `${m.mission_id || m.id}${m.name ? ` — ${m.name}` : ''}`;
                      return (
                        <option key={id} value={id}>{label}</option>
                      );
                    })
                  ) : (
                    <option value="">No Missions Available</option>
                  )}
                </select>
                <ChevronDown className="w-4 h-4 text-slate-400 absolute right-2.5 top-2 pointer-events-none" />
              </div>
            </div>

            <button
              onClick={handleExportJson}
              className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-cyan-500/10 text-cyan-400 border border-cyan-500/30 hover:bg-cyan-500/20 flex items-center gap-1.5 transition-all"
            >
              <Download className="w-3.5 h-3.5" />
              <span>Export Mission Analytics JSON</span>
            </button>
          </div>

          {/* 6 KPI Cards */}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            {[
              { label: 'TOTAL INTRUSION EVENTS', val: kpis.totalEvents, tone: 'text-cyan-400' },
              { label: 'UNIQUE TARGETS', val: kpis.uniqueTracks, tone: 'text-emerald-400' },
              { label: 'AVG DWELL DURATION', val: kpis.avgDwell, tone: 'text-amber-400' },
              { label: 'TOP BREACH CLASS', val: kpis.topClass, tone: 'text-purple-400' },
              { label: 'ACTIVE INTRUDERS', val: kpis.activeIntrusions, tone: 'text-red-400' },
              { label: 'SESSION GEOFENCE', val: zoneStatus?.is_loaded ? 'ARMED' : 'RECORDED', tone: 'text-sky-400' },
            ].map((k, i) => (
              <div
                key={i}
                className="p-3.5 rounded-xl border flex flex-col justify-between"
                style={{ background: 'var(--dvas-surface)', borderColor: 'var(--dvas-border)' }}
              >
                <span className="text-[10px] font-mono text-slate-400">{k.label}</span>
                <span className={`mono text-xl font-bold mt-1 ${k.tone}`}>{k.val}</span>
              </div>
            ))}
          </div>

          {/* Temporal Event Activity Timeline Chart */}
          <div
            className="rounded-xl p-4"
            style={{ background: 'var(--dvas-surface)', border: '1px solid var(--dvas-border)' }}
          >
            <div className="text-xs font-semibold uppercase tracking-wider text-slate-300 mb-3 flex items-center justify-between">
              <span>Temporal Intrusion Timeline Activity</span>
              <span className="text-[11px] font-mono text-slate-500">State transitions / minute</span>
            </div>
            <div className="h-60 w-full">
              {chartData.length === 0 ? (
                <div className="h-full flex items-center justify-center text-xs font-mono text-slate-500">
                  No temporal intrusion events recorded for this mission
                </div>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                    <XAxis dataKey="time" stroke="#64748b" fontSize={10} tickLine={false} />
                    <YAxis stroke="#64748b" fontSize={10} tickLine={false} />
                    <Tooltip
                      contentStyle={{
                        background: '#0f172a',
                        border: '1px solid #334155',
                        borderRadius: '8px',
                        fontSize: '11px',
                        color: '#f8fafc',
                      }}
                    />
                    <Legend wrapperStyle={{ fontSize: '11px', paddingTop: '8px' }} />
                    <Bar dataKey="ENTERING" name="Entering" fill="#ef4444" stackId="a" />
                    <Bar dataKey="DWELLING" name="Dwelling" fill="#f59e0b" stackId="a" />
                    <Bar dataKey="INSIDE" name="Inside" fill="#06b6d4" stackId="a" />
                    <Bar dataKey="EXITED" name="Exited" fill="#10b981" stackId="a" />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          {/* Paginated Intrusion Events Log Table */}
          <div
            className="rounded-xl overflow-hidden"
            style={{ background: 'var(--dvas-surface)', border: '1px solid var(--dvas-border)' }}
          >
            {/* Table Header & State Filter */}
            <div className="p-3 bg-slate-900/80 border-b border-slate-800 flex items-center justify-between flex-wrap gap-2">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-slate-200">INTRUSION AUDIT EVENTS</span>
                <span className="mono text-[10px] px-2 py-0.5 rounded bg-slate-800 text-slate-400">
                  {filteredEvents.length} TOTAL
                </span>
              </div>
              <div className="flex items-center gap-1 text-xs">
                {['ALL', 'ENTERING', 'DWELLING', 'INSIDE', 'EXITED'].map(st => (
                  <button
                    key={st}
                    onClick={() => { setEventFilter(st); setPage(0); }}
                    className={`px-2 py-1 rounded text-[11px] font-mono transition-all ${
                      eventFilter === st
                        ? 'bg-cyan-500/20 text-cyan-300 font-bold border border-cyan-500/40'
                        : 'text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    {st}
                  </button>
                ))}
              </div>
            </div>

            {/* Table Body */}
            <div className="overflow-x-auto">
              <table className="w-full text-xs text-left font-mono">
                <thead className="bg-slate-950/70 text-slate-400 uppercase text-[10px] border-b border-slate-800">
                  <tr>
                    <th className="px-4 py-2.5">Track ID</th>
                    <th className="px-4 py-2.5">Class</th>
                    <th className="px-4 py-2.5">State Transition</th>
                    <th className="px-4 py-2.5">Confidence</th>
                    <th className="px-4 py-2.5">Dwell Duration</th>
                    <th className="px-4 py-2.5">Location (Lat, Lon)</th>
                    <th className="px-4 py-2.5">Timestamp</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60">
                  {paginatedEvents.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="px-4 py-8 text-center text-slate-500">
                        No intrusion events match the current filter
                      </td>
                    </tr>
                  ) : (
                    paginatedEvents.map((ev, idx) => (
                      <tr key={ev.event_id || idx} className="hover:bg-slate-800/30 transition-colors">
                        <td className="px-4 py-2.5 font-bold text-white">#{ev.track_id}</td>
                        <td className="px-4 py-2.5 text-cyan-300">{ev.track_class}</td>
                        <td className="px-4 py-2.5">
                          <span
                            className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                              ev.state_to === 'ENTERING'
                                ? 'bg-red-500/20 text-red-400 border border-red-500/30'
                                : ev.state_to === 'DWELLING'
                                ? 'bg-amber-500/20 text-amber-400 border border-amber-500/30'
                                : ev.state_to === 'EXITED'
                                ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                                : 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/30'
                            }`}
                          >
                            {ev.state_from} → {ev.state_to}
                          </span>
                        </td>
                        <td className="px-4 py-2.5 text-slate-300">
                          {ev.confidence != null && !isNaN(ev.confidence)
                            ? `${(Number(ev.confidence) * 100).toFixed(0)}%`
                            : '—'}
                        </td>
                        <td className="px-4 py-2.5 text-amber-300">
                          {ev.dwell_duration_s != null && !isNaN(ev.dwell_duration_s)
                            ? `${Number(ev.dwell_duration_s).toFixed(1)}s`
                            : '0.0s'}
                        </td>
                        <td className="px-4 py-2.5 text-slate-400">
                          {ev.latitude != null && ev.longitude != null && !isNaN(ev.latitude) && !isNaN(ev.longitude)
                            ? `${Number(ev.latitude).toFixed(5)}, ${Number(ev.longitude).toFixed(5)}`
                            : '—'}
                        </td>
                        <td className="px-4 py-2.5 text-slate-400">
                          {ev.ts ? (() => { try { return new Date(ev.ts).toLocaleString(); } catch { return String(ev.ts); } })() : '—'}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            {/* Pagination Controls */}
            {totalPages > 1 && (
              <div className="p-3 bg-slate-950/80 border-t border-slate-800 flex items-center justify-between text-xs font-mono">
                <span className="text-slate-400">
                  Page {page + 1} of {totalPages}
                </span>
                <div className="flex gap-2">
                  <button
                    onClick={() => setPage(p => Math.max(0, p - 1))}
                    disabled={page === 0}
                    className="px-2.5 py-1 rounded bg-slate-800 border border-slate-700 disabled:opacity-30 flex items-center gap-1"
                  >
                    <ChevronLeft className="w-3.5 h-3.5" /> Previous
                  </button>
                  <button
                    onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
                    disabled={page >= totalPages - 1}
                    className="px-2.5 py-1 rounded bg-slate-800 border border-slate-700 disabled:opacity-30 flex items-center gap-1"
                  >
                    Next <ChevronRight className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            )}
          </div>

        </div>
      )}

      {/* ── MODE 3: CONFIGURATION & SENSITIVITY (Senior Officer) ───────── */}
      {subTab === 'config' && user?.role === 'senior_officer' && (
        <div
          className="rounded-xl p-6 flex flex-col gap-6 max-w-3xl"
          style={{ background: 'var(--dvas-surface)', border: '1px solid var(--dvas-border)' }}
        >
          <div className="border-b border-slate-800 pb-3">
            <h3 className="text-sm font-semibold tracking-wide text-slate-100 flex items-center gap-2">
              <Settings className="w-4 h-4 text-cyan-400" />
              Intrusion Detection Sensitivity & Profile Settings
            </h3>
            <p className="text-xs text-slate-400 mt-1">
              Adjust algorithm sensitivity, hysteresis thresholds, and model confidence for restricted zones.
            </p>
          </div>

          {configFeedback && (
            <div
              className={`p-3 rounded-lg text-xs font-mono border ${
                configFeedback.type === 'success'
                  ? 'bg-emerald-500/10 border-emerald-500/40 text-emerald-300'
                  : 'bg-red-500/10 border-red-500/40 text-red-300'
              }`}
            >
              {configFeedback.text}
            </div>
          )}

          <div className="space-y-4 text-xs font-mono">
            {/* Preset Profile */}
            <div>
              <label className="block text-slate-300 font-semibold mb-1">
                OPERATIONAL PRESET PROFILE
              </label>
              <select
                value={configPreset}
                onChange={e => setConfigPreset(e.target.value)}
                className="w-full bg-slate-900 border border-slate-700 text-slate-200 rounded-lg p-2.5 focus:border-cyan-500 focus:outline-none"
              >
                <option value="default">Default Profile (Balanced detection & false-alarm suppression)</option>
                <option value="confident">Confident Profile (High confidence threshold, strict breach check)</option>
                <option value="lenient">Lenient Profile (High recall, flags border hoverers quickly)</option>
                <option value="stable">Stable Profile (Maximum temporal hysteresis against camera jitter)</option>
              </select>
            </div>

            {/* Confirm Frames */}
            <div>
              <div className="flex justify-between text-slate-300 font-semibold mb-1">
                <span>CONFIRM INSIDE FRAMES</span>
                <span className="text-cyan-400">{confirmFrames} frames</span>
              </div>
              <input
                type="range"
                min="1"
                max="10"
                value={confirmFrames}
                onChange={e => setConfirmFrames(e.target.value)}
                className="w-full accent-cyan-500"
              />
              <span className="text-[11px] text-slate-500">
                Number of consecutive frames a target must remain inside the polygon before escalating state to INSIDE.
              </span>
            </div>

            {/* Dwell Threshold */}
            <div>
              <div className="flex justify-between text-slate-300 font-semibold mb-1">
                <span>DWELL TIME THRESHOLD (SECONDS)</span>
                <span className="text-amber-400">{dwellThreshold}s</span>
              </div>
              <input
                type="range"
                min="1.0"
                max="15.0"
                step="0.5"
                value={dwellThreshold}
                onChange={e => setDwellThreshold(e.target.value)}
                className="w-full accent-amber-500"
              />
              <span className="text-[11px] text-slate-500">
                Duration a target can linger inside the zone before escalating to DWELLING alert status.
              </span>
            </div>

            {/* Min Confidence */}
            <div>
              <div className="flex justify-between text-slate-300 font-semibold mb-1">
                <span>MINIMUM DETECTION CONFIDENCE</span>
                <span className="text-purple-400">{(minConfidence * 100).toFixed(0)}%</span>
              </div>
              <input
                type="range"
                min="0.10"
                max="0.80"
                step="0.05"
                value={minConfidence}
                onChange={e => setMinConfidence(e.target.value)}
                className="w-full accent-purple-500"
              />
              <span className="text-[11px] text-slate-500">
                YOLOv8s bounding box score cutoff threshold.
              </span>
            </div>

            {/* Apply Button */}
            <div className="pt-2">
              <button
                onClick={handleApplyConfig}
                disabled={savingConfig}
                className="px-5 py-2.5 rounded-lg bg-cyan-500 text-slate-950 font-bold hover:bg-cyan-400 transition-all flex items-center gap-2 disabled:opacity-50"
              >
                {savingConfig ? (
                  <>
                    <RefreshCw className="w-4 h-4 animate-spin" />
                    <span>Applying Configuration...</span>
                  </>
                ) : (
                  <>
                    <CheckCircle2 className="w-4 h-4" />
                    <span>Apply Sensitivity Settings</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
