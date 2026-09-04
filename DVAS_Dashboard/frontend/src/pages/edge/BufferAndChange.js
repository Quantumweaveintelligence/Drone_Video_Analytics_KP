import React, { useState, useEffect } from 'react';
import AppShell from '@/components/layout/AppShell';
import { AreaChart, Area, ResponsiveContainer, XAxis, YAxis, Tooltip } from "recharts";
import { Clock, Scan, Activity, Database, AlertCircle } from 'lucide-react';
import { authFetch } from '@/lib/api';

// Reusable SVG Donut Gauge
const DonutGauge = ({ value, label, max = 100, isPercentage = false, color = "#3b82f6", size = "normal" }) => {
  const radius = size === "large" ? 60 : 45;
  const stroke = size === "large" ? 12 : 8;
  const normalizedRadius = radius - stroke * 2;
  const circumference = normalizedRadius * 2 * Math.PI;
  const numericValue = typeof value === 'number' ? value : parseFloat(value) || 0;
  const percentage = Math.min(100, Math.max(0, (numericValue / max) * 100));
  const strokeDashoffset = circumference - (percentage / 100) * circumference;

  return (
    <div className="relative flex flex-col items-center justify-center">
      <div className="relative flex items-center justify-center" style={{ width: radius * 2, height: radius * 2 }}>
        <svg height={radius * 2} width={radius * 2} className="transform -rotate-90">
          <circle stroke="#1e293b" fill="transparent" strokeWidth={stroke} r={normalizedRadius} cx={radius} cy={radius} />
          <circle
            stroke={color}
            fill="transparent"
            strokeWidth={stroke}
            strokeDasharray={circumference + ' ' + circumference}
            style={{ strokeDashoffset, transition: 'stroke-dashoffset 0.5s ease 0s' }}
            r={normalizedRadius}
            cx={radius}
            cy={radius}
          />
        </svg>
        <div className="absolute flex flex-col items-center justify-center">
          <span className={`${size === "large" ? "text-2xl" : "text-xl"} font-bold font-mono`} style={{ color }}>
            {isPercentage ? `${numericValue.toFixed(1)}%` : (typeof value === 'number' && max === 1 ? value.toFixed(3) : value)}
          </span>
        </div>
      </div>
      {label && <div className="mt-2 text-[10px] text-slate-500 font-semibold uppercase tracking-wider">{label}</div>}
    </div>
  );
};

export default function BufferAndChange() {
  const [data, setData] = useState({
    active: false,
    buffer: {},
    change_detection: {},
    history: []
  });
  const [mission, setMission] = useState(null);

  useEffect(() => {
    let mounted = true;
    const poll = async () => {
      try {
        const [analyticsResp, missionResp] = await Promise.all([
          authFetch('/edge-api/api/edge/analytics'),
          authFetch('/edge-api/api/edge/mission/active')
        ]);

        if (analyticsResp.ok && mounted) {
          const json = await analyticsResp.json();
          setData(json);
        }

        if (missionResp.ok && mounted) {
          const mJson = await missionResp.json();
          setMission(mJson.active ? mJson : null);
        }
      } catch (err) {
        // Ignore fetch errors during polling
      }
    };

    poll();
    const interval = setInterval(poll, 2000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  const isActive = data.active && mission !== null;

  // Real Buffer state
  const currentFill = isActive ? (data.buffer?.fill_pct || 0) : 0;
  const currentFrames = isActive ? (data.buffer?.frames_in_buffer || 0).toLocaleString() : "0";
  const maxFrames = isActive ? (data.buffer?.max_frames || 2200).toLocaleString() : "2,200";
  const totalBuffered = isActive ? (data.buffer?.total_written || data.buffer?.frames_in_buffer || 0).toLocaleString() : "0";
  const currentSizeMB = isActive && data.buffer?.slab_used_bytes 
    ? (data.buffer.slab_used_bytes / 1048576).toFixed(1) 
    : "0.0";
  const windowSeconds = isActive ? Math.round(data.buffer?.window_s || 0) : 0;
  
  // Real Change Detection state
  const changeScore = isActive ? (data.change_detection?.last_motion_score || 0) : 0;
  const eventsTotal = isActive ? (data.change_detection?.events_total || 0) : 0;
  const epm = isActive ? Math.round(data.change_detection?.epm || 0) : 0;

  // Real history deque from edge backend
  const historyData = isActive && data.history && data.history.length > 0 
    ? data.history.map((h, i) => ({
        time: h.time ? new Date(h.time * 1000).toLocaleTimeString([], { second: '2-digit', minute: '2-digit' }) : `-${data.history.length - i}s`,
        value: h.buffer_fill || 0,
        score: h.change_score || 0
      }))
    : Array.from({ length: 20 }).map((_, i) => ({
        time: `-${(20 - i) * 2}s`,
        value: 0,
        score: 0
      }));

  let statusColor = "text-emerald-400";
  let barColor = "bg-emerald-500";
  let gaugeColor = "#34d399";
  if (currentFill >= 85) {
    statusColor = "text-red-400";
    barColor = "bg-red-500";
    gaugeColor = "#f87171";
  } else if (currentFill >= 70) {
    statusColor = "text-amber-400";
    barColor = "bg-amber-500";
    gaugeColor = "#fbbf24";
  }

  return (
    <AppShell title="Buffer and Change" subtitle="Monitor rolling video buffer and AI-based change detection">
      <div className="space-y-6">

        {/* Active Mission Status Strip */}
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="bg-slate-800 p-2 rounded text-slate-300">
              <Database size={18} className="text-cyan-400" />
            </div>
            <div>
              <div className="text-sm font-bold text-slate-100 flex items-center gap-2">
                {mission?.mission_name || 'Standby — No Active Mission'}
                {mission?.mission_id && (
                  <span className="text-xs font-mono text-sky-400 bg-sky-950/60 border border-sky-800/40 px-2 py-0.5 rounded font-normal">
                    {mission.mission_id}
                  </span>
                )}
              </div>
              <div className="text-[10px] text-slate-400 uppercase tracking-wider">
                {isActive ? `Drone: ${mission?.drone_id || 'DRN-001'} | Buffer Engine: Memory-Mapped Ring Slab` : 'Deploy a mission in Mission Control to activate live buffer'}
              </div>
            </div>
          </div>
          <div className={`text-xs px-2.5 py-1 rounded-full font-mono tracking-wider flex items-center gap-2 ${isActive ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400' : 'bg-slate-800 border border-slate-700 text-slate-400'}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${isActive ? 'bg-emerald-400 animate-pulse' : 'bg-slate-500'}`}></span>
            {isActive ? 'BUFFER ACTIVE' : 'STANDBY'}
          </div>
        </div>
        
        {/* --- SECTION 1: BUFFER ANALYTICS --- */}
        <div className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            
            {/* Gauge Donut (Top Left) */}
            <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col items-center justify-center">
              <div className="w-full text-left text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-2">Buffer Fill</div>
              <DonutGauge value={currentFill} max={100} isPercentage={true} color={gaugeColor} size="large" />
              <div className="mt-4 text-[10px] text-slate-400 font-mono text-center">
                Target &lt; 70% | Warning &gt; 70% | Crit &gt; 85%
              </div>
            </div>

            {/* Buffer Status Card */}
            <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col">
              <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-4">Buffer Status</div>
              <div className="grid grid-cols-2 gap-4 flex-1 text-xs">
                <div className="flex flex-col border-b border-slate-800/50 pb-2">
                  <span className="text-slate-500 mb-1">DURATION</span>
                  <span className="text-slate-200 font-mono">{windowSeconds}s</span>
                </div>
                <div className="flex flex-col border-b border-slate-800/50 pb-2">
                  <span className="text-slate-500 mb-1">CURRENT FRAMES</span>
                  <span className="text-slate-200 font-mono">{currentFrames}</span>
                </div>
                <div className="flex flex-col border-b border-slate-800/50 pb-2">
                  <span className="text-slate-500 mb-1">MAX CAPACITY</span>
                  <span className="text-slate-200 font-mono">{maxFrames}</span>
                </div>
                <div className="flex flex-col border-b border-slate-800/50 pb-2">
                  <span className="text-slate-500 mb-1">CURRENT SIZE</span>
                  <span className="text-slate-200 font-mono">{currentSizeMB} MB</span>
                </div>
                <div className="flex flex-col">
                  <span className="text-slate-500 mb-1">SLAB ALLOCATION</span>
                  <span className="text-slate-200 font-mono">128 MB</span>
                </div>
                <div className="flex flex-col">
                  <span className="text-slate-500 mb-1">OVERFLOW EVENTS</span>
                  <span className="text-emerald-400 font-mono font-bold">0</span>
                </div>
              </div>
            </div>

            {/* Frame Capacity Card */}
            <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col justify-between">
              <div>
                <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-4">Frame Capacity</div>
                
                <div className="flex justify-between items-end mb-2">
                  <span className="text-xs text-slate-400 font-bold tracking-wider">OCCUPIED</span>
                  <span className={`text-sm font-mono font-bold ${statusColor}`}>
                    {currentFrames} <span className="text-slate-500 font-normal">/ {maxFrames}</span>
                  </span>
                </div>
                
                {/* Thick progress bar */}
                <div className="w-full bg-slate-800 h-2 rounded-full overflow-hidden mb-6">
                  <div className={`${barColor} h-full rounded-full transition-all duration-500`} style={{ width: `${Math.min(100, currentFill)}%` }}></div>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-2 pt-4 border-t border-slate-800">
                <div>
                  <div className="text-[9px] text-slate-500 uppercase tracking-wider mb-1">Total Buffered</div>
                  <div className="text-xs font-mono text-slate-200">{totalBuffered}</div>
                </div>
                <div>
                  <div className="text-[9px] text-slate-500 uppercase tracking-wider mb-1">Frames Dropped</div>
                  <div className="text-xs font-mono text-emerald-400">0</div>
                </div>
                <div>
                  <div className="text-[9px] text-slate-500 uppercase tracking-wider mb-1">Buffer Health</div>
                  <div className="text-xs font-mono text-emerald-400 font-bold">{isActive ? "OPTIMAL" : "STANDBY"}</div>
                </div>
              </div>
            </div>
          </div>

          {/* Buffer Occupancy Timeline (full width) */}
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-5">
            <div className="flex items-center justify-between mb-4">
              <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider flex items-center gap-2">
                <Clock size={14} /> Buffer Occupancy Timeline
              </div>
              <div className="text-[10px] text-slate-500 font-mono tracking-wider">LIVE ROLLING WINDOW</div>
            </div>
            
            <div className="w-full h-[180px]">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={historyData} margin={{ top: 5, right: 0, left: -25, bottom: 0 }}>
                  <defs>
                    <linearGradient id="colorUtil" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={currentFill >= 85 ? "#f87171" : currentFill >= 70 ? "#fbbf24" : "#34d399"} stopOpacity={0.4}/>
                      <stop offset="95%" stopColor={currentFill >= 85 ? "#f87171" : currentFill >= 70 ? "#fbbf24" : "#34d399"} stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="time" stroke="#475569" fontSize={10} tickMargin={8} />
                  <YAxis stroke="#475569" fontSize={10} tickFormatter={(v) => `${v}%`} domain={[0, 100]} />
                  <Tooltip 
                    contentStyle={{ backgroundColor: '#0f172a', borderColor: '#1e293b', fontSize: '11px', color: '#cbd5e1', fontFamily: 'monospace' }}
                    itemStyle={{ color: currentFill >= 85 ? "#f87171" : currentFill >= 70 ? "#fbbf24" : "#34d399" }}
                  />
                  <Area 
                    type="monotone" 
                    dataKey="value" 
                    stroke={currentFill >= 85 ? "#f87171" : currentFill >= 70 ? "#fbbf24" : "#34d399"} 
                    strokeWidth={2} 
                    fillOpacity={1} 
                    fill="url(#colorUtil)" 
                    isAnimationActive={false} 
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>

        <hr className="border-slate-800" />

        {/* --- SECTION 2: CHANGE DETECTION --- */}
        <div className="space-y-4">
          {/* Top Metric Cards */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 flex items-center justify-center">
              <DonutGauge value={changeScore} max={1} label="MOG2 Motion Score" color="#fbbf24" />
            </div>
            <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 flex items-center justify-center">
              <DonutGauge value={epm} max={60} label="Events / Minute (EPM)" color="#22d3ee" />
            </div>
            <div className="bg-slate-900 border border-slate-800 rounded-lg p-6 flex flex-col justify-center items-center">
              <div className="text-4xl font-bold font-mono text-amber-500 mb-2">{eventsTotal}</div>
              <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider">Total Motion Events</div>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
            {/* Change Detection Status Panel (6 cols) */}
            <div className="lg:col-span-6 bg-slate-900 border border-slate-800 rounded-lg flex flex-col p-5 h-[340px] justify-between">
              <div>
                <div className="text-xs text-slate-300 font-semibold uppercase tracking-wider flex items-center gap-2 mb-4">
                  <Scan size={16} className="text-amber-500" />
                  MOG2 Change Detection Engine
                </div>
                
                <div className="space-y-3 text-xs">
                  <div className="flex justify-between py-2 border-b border-slate-800">
                    <span className="text-slate-400">Background Subtractor</span>
                    <span className="text-slate-200 font-mono">OpenCV MOG2 (History: 500)</span>
                  </div>
                  <div className="flex justify-between py-2 border-b border-slate-800">
                    <span className="text-slate-400">Motion Score Threshold</span>
                    <span className="text-amber-400 font-mono">0.0005 (0.05%)</span>
                  </div>
                  <div className="flex justify-between py-2 border-b border-slate-800">
                    <span className="text-slate-400">Blob Persistence Gate</span>
                    <span className="text-emerald-400 font-mono">2 Consecutive Frames (IoU ≥ 0.25)</span>
                  </div>
                  <div className="flex justify-between py-2 border-b border-slate-800">
                    <span className="text-slate-400">Shadow Suppression</span>
                    <span className="text-sky-400 font-mono">Active (detectShadows: true)</span>
                  </div>
                  <div className="flex justify-between py-2">
                    <span className="text-slate-400">Current Motion Activity</span>
                    <span className={`font-mono font-bold ${changeScore > 0.0005 ? 'text-amber-400 animate-pulse' : 'text-slate-500'}`}>
                      {changeScore > 0.0005 ? 'MOTION DETECTED' : 'QUIET / STABLE'}
                    </span>
                  </div>
                </div>
              </div>

              <div className="p-3 bg-slate-950/60 border border-slate-800 rounded text-[11px] text-slate-400 flex items-center gap-2">
                <Activity size={14} className="text-cyan-400 shrink-0" />
                <span>Change events forward high-variance frames directly into the Cognitive Selector.</span>
              </div>
            </div>

            {/* Change Intensity Graph (6 cols) */}
            <div className="lg:col-span-6 bg-slate-900 border border-slate-800 rounded-lg p-5 h-[340px] flex flex-col">
              <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-4 flex justify-between">
                <span>Motion Intensity Graph</span>
                <span className="text-amber-400 font-mono">Score: {changeScore.toFixed(4)}</span>
              </div>
              <div className="flex-1 min-h-0 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={historyData} margin={{ top: 5, right: 0, left: -25, bottom: 0 }}>
                    <defs>
                      <linearGradient id="colorIntensity" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#fbbf24" stopOpacity={0.4}/>
                        <stop offset="95%" stopColor="#fbbf24" stopOpacity={0}/>
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="time" stroke="#475569" fontSize={10} tickMargin={8} />
                    <YAxis stroke="#475569" fontSize={10} tickFormatter={(v) => v.toFixed(2)} domain={[0, 'auto']} />
                    <Tooltip 
                      contentStyle={{ backgroundColor: '#0f172a', borderColor: '#1e293b', fontSize: '11px', color: '#cbd5e1' }}
                      itemStyle={{ color: '#fbbf24' }}
                    />
                    <Area 
                      type="monotone" 
                      dataKey="score" 
                      stroke="#fbbf24" 
                      strokeWidth={2} 
                      fillOpacity={1} 
                      fill="url(#colorIntensity)" 
                      isAnimationActive={false} 
                    />
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
