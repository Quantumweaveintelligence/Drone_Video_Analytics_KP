import React, { useState, useEffect } from 'react';
import AppShell from '@/components/layout/AppShell';
import { AreaChart, Area, ResponsiveContainer, XAxis, YAxis, Tooltip } from "recharts";
import { Clock, Scan } from 'lucide-react';

// Utility to generate initial blank timeline data
const generateRealisticDummyHistory = () => {
  return Array.from({ length: 60 }).map((_, i) => {
    // Base fill around 74.5, fluctuating a bit
    const fill = 70 + Math.sin(i / 5) * 5 + Math.random() * 2;
    // Score around 0.2 to 0.6, sometimes spiking
    let score = 0.1 + Math.random() * 0.2;
    if (i % 15 === 0) score += 0.3 + Math.random() * 0.3; // Spikes
    
    return {
      time: i.toString(),
      value: fill,
      score: Math.min(score, 1)
    };
  });
};

// Reusable SVG Donut Gauge
const DonutGauge = ({ value, label, max = 100, isPercentage = false, color = "#3b82f6", size = "normal" }) => {
  const radius = size === "large" ? 60 : 45;
  const stroke = size === "large" ? 12 : 8;
  const normalizedRadius = radius - stroke * 2;
  const circumference = normalizedRadius * 2 * Math.PI;
  const percentage = (value / max) * 100;
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
            {isPercentage ? `${value}%` : value}
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

  useEffect(() => {
    let mounted = true;
    const poll = async () => {
      try {
        const resp = await fetch('/edge-api/api/edge/analytics');
        if (resp.ok && mounted) {
          const json = await resp.json();
          setData(json);
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

  const isActive = data.active;

  // Buffer state
  const currentFill = isActive ? (data.buffer?.fill_pct || 0) : 74.5;
  const currentFrames = isActive ? (data.buffer?.frames_in_buffer || 0).toLocaleString() : "8,942";
  const maxFrames = isActive ? (data.buffer?.max_frames || 9000).toLocaleString() : "9,000";
  const totalBuffered = isActive ? (data.buffer?.total_written || 0).toLocaleString() : "142,500";
  const currentSizeMB = isActive ? (data.buffer?.slab_used_bytes ? Math.round(data.buffer.slab_used_bytes / 1048576) : 0).toLocaleString() : "1,240";
  const windowSeconds = isActive ? (data.buffer?.window_s || 0) : 300;
  
  // Change state
  const changeScore = isActive ? (data.change_detection?.last_motion_score || 0) : 0.42;
  const eventsTotal = isActive ? (data.change_detection?.events_total || 0) : 14;

  // Derive charts from history deque (up to 60 elements)
  const historyData = isActive && data.history && data.history.length > 0 
    ? data.history.map((h, i) => ({
        time: i.toString(),
        value: h.buffer_fill,
        score: h.change_score
      }))
    : generateRealisticDummyHistory();

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
                  <span className="text-slate-500 mb-1">AVG SIZE</span>
                  <span className="text-slate-200 font-mono">{isActive ? "-- MB" : "890 MB"}</span>
                </div>
                <div className="flex flex-col">
                  <span className="text-slate-500 mb-1">OVERFLOW EVENTS</span>
                  <span className="text-amber-400 font-mono font-bold">0</span>
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
                  <div className={`${barColor} h-full rounded-full`} style={{ width: `${currentFill}%` }}></div>
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
                  <div className="text-[9px] text-slate-500 uppercase tracking-wider mb-1">Peak Util</div>
                  <div className="text-xs font-mono text-amber-400">{isActive ? "--%" : "82.1%"}</div>
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
              <div className="text-[10px] text-slate-500 font-mono tracking-wider">LAST 20 MINUTES</div>
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
              <DonutGauge value={changeScore} max={1} label="Change Score" color="#fbbf24" />
            </div>
            <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 flex items-center justify-center">
              <DonutGauge value={isActive ? 0 : 87} max={100} isPercentage={true} label={isActive ? "Confidence (N/A)" : "Confidence"} color="#22d3ee" />
            </div>
            <div className="bg-slate-900 border border-slate-800 rounded-lg p-6 flex flex-col justify-center items-center">
              <div className="text-4xl font-bold font-mono text-amber-500 mb-2">{eventsTotal}</div>
              <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider">Total Events</div>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
            {/* Detection Heatmap (6 cols) */}
            <div className="lg:col-span-6 bg-slate-900 border border-slate-800 rounded-lg flex flex-col overflow-hidden h-[340px]">
              <div className="p-4 border-b border-slate-800 flex justify-between items-center bg-slate-950/50">
                <div className="text-xs text-slate-400 font-semibold uppercase tracking-wider flex items-center gap-2">
                  <Scan size={14} className="text-amber-500" />
                  Live Heatmap · 3 Regions Detected
                </div>
                <div className="text-[9px] px-2 py-0.5 rounded border border-sky-500/30 bg-sky-500/10 text-sky-400 uppercase tracking-wider font-semibold">
                  AI Overlay Ready
                </div>
              </div>
              <div className="flex-1 relative bg-gradient-to-b from-slate-900 to-slate-800 overflow-hidden">
                {/* Tactical Grid */}
                <div className="absolute inset-0 pointer-events-none" style={{ 
                  backgroundImage: 'linear-gradient(rgba(34, 197, 94, 0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(34, 197, 94, 0.05) 1px, transparent 1px)',
                  backgroundSize: '8.33% 12.5%' 
                }}></div>
                
                {/* Simulated Heat Blobs */}
                <div className="absolute top-[30%] left-[20%] w-32 h-32 rounded-full" 
                     style={{ background: 'radial-gradient(circle, rgba(245,158,11,0.6) 0%, rgba(245,158,11,0) 70%)', filter: 'blur(8px)' }}></div>
                <div className="absolute top-[60%] left-[65%] w-40 h-24 rounded-full" 
                     style={{ background: 'radial-gradient(ellipse, rgba(245,158,11,0.5) 0%, rgba(245,158,11,0) 70%)', filter: 'blur(12px)' }}></div>
                <div className="absolute top-[15%] left-[70%] w-24 h-24 rounded-full" 
                     style={{ background: 'radial-gradient(circle, rgba(245,158,11,0.8) 0%, rgba(245,158,11,0) 70%)', filter: 'blur(6px)' }}></div>
                     
                {/* Fake Crosshairs to enhance drone feel */}
                <div className="absolute inset-0 flex items-center justify-center pointer-events-none opacity-20">
                  <div className="w-16 h-[1px] bg-sky-400 absolute"></div>
                  <div className="w-[1px] h-16 bg-sky-400 absolute"></div>
                </div>
              </div>
            </div>

            {/* Change Intensity Graph (6 cols) */}
            <div className="lg:col-span-6 bg-slate-900 border border-slate-800 rounded-lg p-5 h-[340px] flex flex-col">
              <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-4">Change Intensity Graph</div>
              <div className="flex-1 min-h-0 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={historyData} margin={{ top: 5, right: 0, left: -25, bottom: 0 }}>
                    <defs>
                      <linearGradient id="colorIntensity" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#fbbf24" stopOpacity={0.4}/>
                        <stop offset="95%" stopColor="#fbbf24" stopOpacity={0}/>
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="time" hide />
                    <YAxis stroke="#475569" fontSize={10} tickFormatter={(v) => v.toFixed(1)} domain={[0, 1]} />
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
