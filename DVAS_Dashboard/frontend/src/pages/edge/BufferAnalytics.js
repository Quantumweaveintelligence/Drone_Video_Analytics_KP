import React from 'react';
import AppShell from '@/components/layout/AppShell';
import { AreaChart, Area, ResponsiveContainer, XAxis, YAxis, Tooltip } from "recharts";
import { Clock } from 'lucide-react';

const generateChartData = (count) => {
  return Array.from({ length: count }).map((_, i) => {
    // Generate data fluctuating around 74.5%
    const value = Math.max(0, Math.min(100, 74.5 + (Math.random() - 0.5) * 15));
    return {
      time: `-${20 - i}m`,
      value: value.toFixed(1)
    };
  });
};

const timelineData = generateChartData(20);

// Simple SVG Donut Component
const DonutGauge = ({ percentage }) => {
  const radius = 60;
  const stroke = 12;
  const normalizedRadius = radius - stroke * 2;
  const circumference = normalizedRadius * 2 * Math.PI;
  const strokeDashoffset = circumference - (percentage / 100) * circumference;

  let color = "#34d399"; // emerald-400 (green)
  if (percentage >= 85) color = "#f87171"; // red-400
  else if (percentage >= 70) color = "#fbbf24"; // amber-400

  return (
    <div className="relative flex items-center justify-center w-36 h-36">
      <svg height={radius * 2} width={radius * 2} className="transform -rotate-90">
        <circle
          stroke="#1e293b" // slate-800
          fill="transparent"
          strokeWidth={stroke}
          r={normalizedRadius}
          cx={radius}
          cy={radius}
        />
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
        <span className="text-2xl font-bold font-mono" style={{ color }}>{percentage}%</span>
      </div>
    </div>
  );
};

export default function BufferAnalytics() {
  const currentFill = 74.5;
  const currentFrames = "8,942";
  const maxFrames = "9,000";

  let statusColor = "text-emerald-400";
  let barColor = "bg-emerald-500";
  if (currentFill >= 85) {
    statusColor = "text-red-400";
    barColor = "bg-red-500";
  } else if (currentFill >= 70) {
    statusColor = "text-amber-400";
    barColor = "bg-amber-500";
  }

  return (
    <AppShell title="Buffer Analytics" subtitle="Monitor rolling video buffer for evidence extraction">
      <div className="space-y-4">
        
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          
          {/* Gauge Donut (Top Left) */}
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col items-center justify-center">
            <div className="w-full text-left text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-2">Buffer Fill</div>
            <DonutGauge percentage={currentFill} />
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
                <span className="text-slate-200 font-mono">5m 0s <span className="text-slate-500 text-[10px]">(300s)</span></span>
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
                <span className="text-slate-200 font-mono">1,240 MB</span>
              </div>
              <div className="flex flex-col">
                <span className="text-slate-500 mb-1">AVG SIZE</span>
                <span className="text-slate-200 font-mono">890 MB</span>
              </div>
              <div className="flex flex-col">
                <span className="text-slate-500 mb-1">OVERFLOW EVENTS</span>
                <span className="text-amber-400 font-mono font-bold">2</span>
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
                <div className="text-xs font-mono text-slate-200">142.5K</div>
              </div>
              <div>
                <div className="text-[9px] text-slate-500 uppercase tracking-wider mb-1">Frames Dropped</div>
                <div className="text-xs font-mono text-emerald-400">0</div>
              </div>
              <div>
                <div className="text-[9px] text-slate-500 uppercase tracking-wider mb-1">Peak Util</div>
                <div className="text-xs font-mono text-amber-400">82.1%</div>
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
              <AreaChart data={timelineData} margin={{ top: 5, right: 0, left: -25, bottom: 0 }}>
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
    </AppShell>
  );
}
