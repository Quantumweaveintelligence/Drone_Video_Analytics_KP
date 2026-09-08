import React, { useState, useEffect } from 'react';
import AppShell from '@/components/layout/AppShell';
import { Camera, Radio, Film, Activity, Layers } from 'lucide-react';
import { AreaChart, Area, ResponsiveContainer, XAxis, YAxis, Tooltip } from "recharts";
import { authFetch, getAuthToken } from '@/lib/api';

export default function LiveMonitoring() {
  const [metrics, setMetrics] = useState({ fps: 0, connected: false, buffer_fill_pct: 0, frames_total: 0, frames_selected: 0 });
  const [mission, setMission] = useState(null);
  const [fpsHistory, setFpsHistory] = useState(() => Array.from({length: 30}, (_, i) => ({ time: i, value: 0 })));
  const [bufferHistory, setBufferHistory] = useState(() => Array.from({length: 30}, (_, i) => ({ time: i, value: 0 })));

  useEffect(() => {
    let tick = 0;
    const poll = async () => {
      try {
        const [statusResp, missionResp] = await Promise.all([
          authFetch('/edge-api/api/edge/status'),
          authFetch('/edge-api/api/edge/mission/active'),
        ]);
        const status = await statusResp.json();
        const missionData = await missionResp.json();
        setMetrics(status);
        if (missionData.active) {
          setMission(missionData);
        } else {
          setMission(null);
        }

        tick++;
        setFpsHistory(prev => [...prev.slice(1), { time: tick, value: Math.round(status.fps || 0) }]);
        setBufferHistory(prev => [...prev.slice(1), { time: tick, value: Math.round(status.buffer_fill_pct || 0) }]);
      } catch (err) {
        // Edge not available — keep last known state
      }
    };
    poll();
    const interval = setInterval(poll, 2000);
    return () => clearInterval(interval);
  }, []);

  const token = getAuthToken();
  const streamUrl = token ? `/edge-api/stream/live?token=${encodeURIComponent(token)}` : '/edge-api/stream/live';

  // Determine dynamic stream type
  const getStreamTypeInfo = () => {
    if (!mission) {
      return { label: 'No Active Stream', type: 'NONE', isLive: false };
    }
    const rawType = (mission.video_source_type || '').toLowerCase();
    const source = (mission.video_source || '').toLowerCase();

    if (rawType === 'rtmp' || source.startsWith('rtmp://')) {
      return { label: 'RTMP Stream', type: 'RTMP', isLive: true };
    }
    if (rawType === 'rtsp' || source.startsWith('rtsp://')) {
      return { label: 'RTSP Stream', type: 'RTSP', isLive: true };
    }
    if (rawType === 'file' || rawType === 'video' || source.endsWith('.mp4') || source.endsWith('.mov') || source.endsWith('.avi') || source.endsWith('.mkv') || source.endsWith('.ts')) {
      return { label: 'Video Input (File Upload)', type: 'FILE', isLive: false };
    }
    return {
      label: mission.video_source_type ? `${mission.video_source_type.toUpperCase()} Stream` : 'Video Input',
      type: 'CUSTOM',
      isLive: true
    };
  };

  const streamInfo = getStreamTypeInfo();

  return (
    <AppShell title="Live Monitoring" subtitle="Real-time drone video feed with stream telemetry">
      <div className="space-y-4">

        {/* Drone Status Strip */}
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="bg-slate-800 p-2 rounded text-slate-300">
              <Camera size={18} />
            </div>
            <div>
              <div className="text-sm font-bold text-slate-100 flex items-center gap-2">
                {mission?.mission_name || mission?.drone_id || 'Standby — No Active Mission'}
                {mission?.drone_id && (
                  <span className="text-xs font-mono text-sky-400 bg-sky-950/60 border border-sky-800/40 px-2 py-0.5 rounded font-normal">
                    {mission.drone_id}
                  </span>
                )}
              </div>
              <div className="text-[10px] text-slate-400 uppercase tracking-wider">
                {mission?.location ? `Location: ${mission.location}` : (metrics.connected ? 'Active Feed Receiving' : 'Waiting for Stream Source')}
              </div>
            </div>
          </div>
          <div className={`text-xs px-2.5 py-1 rounded-full font-mono tracking-wider flex items-center gap-2 ${metrics.connected ? 'bg-sky-500/10 border border-sky-500/20 text-sky-400' : 'bg-slate-800 border border-slate-700 text-slate-400'}`}>
            <span className={`w-1.5 h-1.5 rounded-full animate-pulse ${metrics.connected ? 'bg-sky-400' : 'bg-slate-500'}`}></span>
            {metrics.connected ? 'CONNECTED' : 'OFFLINE'}
          </div>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-12 gap-4">

          {/* Main Video Area (8 columns) */}
          <div className="xl:col-span-8 flex flex-col gap-4">

            {/* Header bar above video */}
            <div className="flex flex-col sm:flex-row gap-3 sm:items-center justify-between">
              <div className="flex items-center gap-3">
                <div className={`text-[10px] px-2 py-1 rounded-sm font-mono tracking-wider flex items-center gap-1.5 ${metrics.connected ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400' : 'bg-red-500/10 border border-red-500/20 text-red-400'}`}>
                  <span className={`w-1.5 h-1.5 rounded-full animate-pulse ${metrics.connected ? 'bg-emerald-400' : 'bg-red-400'}`}></span>
                  {metrics.connected ? 'HEALTHY' : 'NO SIGNAL'}
                </div>
                <div className="text-sm text-slate-200 font-medium">{mission?.mission_name || mission?.drone_id || 'Live Feed'}</div>
                <div className="text-xs text-slate-500 font-mono">{mission?.mission_id || 'NO MISSION'}</div>
              </div>

              <div className="flex gap-2 text-xs font-mono">
                <div className="px-2 py-1 bg-slate-900 border border-slate-800 rounded flex items-center gap-1">
                  <span className="text-slate-500">FPS:</span>
                  <span className="text-cyan-400 font-bold">{Math.round(metrics.fps || 0)}</span>
                </div>
                <div className="px-2 py-1 bg-slate-900 border border-slate-800 rounded flex items-center gap-1">
                  <span className="text-slate-500">TYPE:</span>
                  <span className="text-sky-400 font-semibold">{streamInfo.type}</span>
                </div>
                <div className="px-2 py-1 bg-slate-900 border border-slate-800 rounded flex items-center gap-1">
                  <span className="text-slate-500">INGEST:</span>
                  <span className={mission?.server_send_enabled ? "text-emerald-400 font-semibold" : "text-amber-400 font-semibold"}>
                    {mission?.server_send_enabled ? 'ACTIVE' : 'STANDBY'}
                  </span>
                </div>
              </div>
            </div>

            {/* Video Player Box */}
            <div className="relative w-full aspect-video bg-black rounded-lg overflow-hidden border border-slate-800 shadow-2xl">
              {/* Overlay banner when paused waiting for Server Send */}
              {mission?.active && !mission?.server_send_enabled && (streamInfo.type === 'FILE' || mission?.is_paused) && (
                <div className="absolute top-4 left-1/2 -translate-x-1/2 z-10 px-3 py-1.5 bg-amber-950/80 border border-amber-500/60 text-amber-300 text-xs font-mono rounded backdrop-blur flex items-center gap-2 shadow-xl">
                  <span className="w-2 h-2 rounded-full bg-amber-400 animate-ping"></span>
                  <span>PAUSED — First Frame Preview (Awaiting Server Send: ON)</span>
                </div>
              )}

              {/* MJPEG stream from dvas_edge */}
              <img
                src={streamUrl}
                alt="Live drone feed"
                className="w-full h-full object-cover"
                style={{ minHeight: '100%' }}
              />

              {/* Top Left HUD */}
              <div className="absolute top-4 left-4 flex gap-2">
                <div className="px-2 py-1 bg-sky-900/80 text-sky-200 border border-sky-500/30 text-xs font-mono rounded backdrop-blur">
                  {mission?.drone_id || (metrics.connected ? 'STREAM ACTIVE' : 'STANDBY')}
                </div>
                <div className="px-2 py-1 bg-black/60 text-slate-300 border border-slate-700/50 text-xs font-mono rounded backdrop-blur flex items-center gap-1.5">
                  {streamInfo.type === 'RTMP' || streamInfo.type === 'RTSP' ? <Radio size={12} className="text-emerald-400 animate-pulse" /> : <Film size={12} className="text-sky-400" />}
                  {streamInfo.label}
                </div>
              </div>

              {/* Bottom Right HUD */}
              <div className="absolute bottom-4 right-4">
                <div className={`px-2 py-1 bg-black/60 border border-slate-700/50 text-[10px] font-mono tracking-wider rounded backdrop-blur flex items-center gap-1.5 ${metrics.connected ? 'text-emerald-400' : 'text-red-400'}`}>
                  <span className={`w-1.5 h-1.5 rounded-full animate-pulse ${metrics.connected ? 'bg-emerald-400' : 'bg-red-400'}`}></span>
                  {metrics.connected ? 'HEALTHY' : 'NO SIGNAL'}
                </div>
              </div>

              {/* Crosshair decoration */}
              <div className="absolute inset-0 pointer-events-none flex items-center justify-center opacity-30">
                <div className="w-12 h-12 border border-slate-400 rounded-full flex items-center justify-center">
                  <div className="w-1 h-1 bg-slate-400 rounded-full"></div>
                </div>
              </div>
            </div>
          </div>

          {/* Right Metrics Panel (4 columns) */}
          <div className="xl:col-span-4 flex flex-col gap-4">
            <div className="bg-slate-900 border border-slate-800 p-5 rounded-lg flex flex-col">
              <div className="text-xs text-slate-500 font-semibold uppercase tracking-wider mb-4 flex items-center justify-between">
                <span>Stream Information</span>
                <span className={`text-[10px] px-2 py-0.5 rounded font-mono ${metrics.connected ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-slate-800 text-slate-400'}`}>
                  {metrics.connected ? 'LIVE FEED' : 'OFFLINE'}
                </span>
              </div>

              <div className="space-y-4">
                {/* Stream Type Display */}
                <div className="p-3 bg-slate-800/60 border border-slate-700/50 rounded-lg">
                  <div className="text-[10px] text-slate-400 uppercase tracking-wider mb-1">Stream Input Type</div>
                  <div className="text-sm font-bold text-slate-100 flex items-center gap-2">
                    {streamInfo.type === 'RTMP' || streamInfo.type === 'RTSP' ? (
                      <Radio size={16} className="text-emerald-400" />
                    ) : (
                      <Film size={16} className="text-sky-400" />
                    )}
                    <span>{streamInfo.label}</span>
                  </div>
                  {mission?.video_source && (
                    <div className="text-[11px] font-mono text-slate-400 mt-1.5 truncate" title={mission.video_source}>
                      <span className="text-slate-500">Source: </span>
                      {mission.video_source}
                    </div>
                  )}
                </div>

                {/* Frame Rate */}
                <div>
                  <div className="flex justify-between text-xs mb-1.5">
                    <span className="text-slate-400">Frame Rate</span>
                    <span className="text-emerald-400 font-mono font-semibold">{Math.round(metrics.fps || 0)} fps</span>
                  </div>
                  <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
                    <div className="bg-emerald-500 h-full rounded-full transition-all duration-300" style={{ width: `${Math.min(100, ((metrics.fps || 0) / 30) * 100)}%` }}></div>
                  </div>
                </div>

                {/* Pipeline Stats Grid */}
                <div className="pt-3 border-t border-slate-800/50 grid grid-cols-2 gap-3">
                  <div className="bg-slate-800/30 p-2.5 rounded border border-slate-800">
                    <div className="text-[10px] text-slate-500 uppercase tracking-wider">Frames Ingested</div>
                    <div className="text-base font-mono text-cyan-400 mt-0.5">{metrics.frames_total || 0}</div>
                  </div>
                  <div className="bg-slate-800/30 p-2.5 rounded border border-slate-800">
                    <div className="text-[10px] text-slate-500 uppercase tracking-wider">Frames Selected</div>
                    <div className="text-base font-mono text-sky-400 mt-0.5">{metrics.frames_selected || 0}</div>
                  </div>
                </div>
              </div>
            </div>

            {/* FPS Trend Chart */}
            <div className="bg-slate-900 border border-slate-800 p-4 rounded-lg flex flex-col">
              <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-2 flex items-center justify-between">
                <span>FPS Trend</span>
                <span className="text-cyan-400 font-mono text-xs">{Math.round(metrics.fps || 0)} fps</span>
              </div>
              <div className="w-full h-[100px]">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={fpsHistory} margin={{ top: 5, right: 0, left: -25, bottom: 0 }}>
                    <defs>
                      <linearGradient id="colorFps" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#22d3ee" stopOpacity={0.3}/>
                        <stop offset="95%" stopColor="#22d3ee" stopOpacity={0}/>
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="time" hide />
                    <YAxis stroke="#475569" fontSize={9} domain={['dataMin - 5', 'dataMax + 5']} />
                    <Tooltip contentStyle={{ backgroundColor: '#0f172a', borderColor: '#1e293b', fontSize: '10px', color: '#cbd5e1' }} itemStyle={{ color: '#22d3ee' }} />
                    <Area type="monotone" dataKey="value" stroke="#22d3ee" strokeWidth={1.5} fillOpacity={1} fill="url(#colorFps)" isAnimationActive={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Edge Buffer Fill % Chart */}
            <div className="bg-slate-900 border border-slate-800 p-4 rounded-lg flex flex-col">
              <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-2 flex items-center justify-between">
                <span>Edge Buffer Utilization</span>
                <span className="text-amber-400 font-mono text-xs">{Math.round(metrics.buffer_fill_pct || 0)}%</span>
              </div>
              <div className="w-full h-[100px]">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={bufferHistory} margin={{ top: 5, right: 0, left: -25, bottom: 0 }}>
                    <defs>
                      <linearGradient id="colorBuffer" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.3}/>
                        <stop offset="95%" stopColor="#f59e0b" stopOpacity={0}/>
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="time" hide />
                    <YAxis stroke="#475569" fontSize={9} domain={[0, 100]} />
                    <Tooltip contentStyle={{ backgroundColor: '#0f172a', borderColor: '#1e293b', fontSize: '10px', color: '#cbd5e1' }} itemStyle={{ color: '#f59e0b' }} />
                    <Area type="monotone" dataKey="value" stroke="#f59e0b" strokeWidth={1.5} fillOpacity={1} fill="url(#colorBuffer)" isAnimationActive={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>

          </div>
        </div>

      </div>
    </AppShell>
  );
}
