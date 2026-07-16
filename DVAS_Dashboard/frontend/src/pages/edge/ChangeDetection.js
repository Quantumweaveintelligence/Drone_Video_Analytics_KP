import React from 'react';
import AppShell from '@/components/layout/AppShell';
import { AreaChart, Area, LineChart, Line, ResponsiveContainer, XAxis, YAxis, Tooltip, Legend } from "recharts";
import { Scan } from 'lucide-react';

const generateTimelineData = (count) => {
  return Array.from({ length: count }).map((_, i) => ({
    time: `-${(count - i) * 3}m`,
    score: Math.max(0, Math.min(1, 0.2 + (Math.random() - 0.5) * 0.6)),
    regions: Math.max(0, Math.round(1 + (Math.random() - 0.5) * 4))
  }));
};

const timelineData = generateTimelineData(20);

// Reusable SVG Donut Gauge
const DonutGauge = ({ value, label, max = 100, isPercentage = false, color = "#3b82f6" }) => {
  const radius = 45;
  const stroke = 8;
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
          <span className="text-xl font-bold font-mono" style={{ color }}>{isPercentage ? `${value}%` : value}</span>
        </div>
      </div>
      <div className="mt-2 text-[10px] text-slate-500 font-semibold uppercase tracking-wider">{label}</div>
    </div>
  );
};

export default function ChangeDetection() {
  return (
    <AppShell title="Change Detection" subtitle="Visualise AI-based motion and change detection analytics">
      <div className="space-y-4">
        
        {/* Top Metric Cards */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 flex items-center justify-center">
            <DonutGauge value={0.42} max={1} label="Change Score" color="#fbbf24" />
          </div>
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 flex items-center justify-center">
            <DonutGauge value={87} max={100} isPercentage={true} label="Confidence" color="#22d3ee" />
          </div>
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-6 flex flex-col justify-center items-center">
            <div className="text-4xl font-bold font-mono text-amber-500 mb-2">3</div>
            <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider">Active Regions</div>
          </div>
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-6 flex flex-col justify-center items-center">
            <div className="text-4xl font-bold font-mono text-sky-400 mb-2">12.4</div>
            <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider">% Frame Area</div>
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

          {/* Detection Timeline Chart (6 cols) */}
          <div className="lg:col-span-6 bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col h-[340px]">
            <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-6">Detection Timeline (Last Hour)</div>
            <div className="flex-1 min-h-0 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={timelineData} margin={{ top: 5, right: 0, left: -20, bottom: 0 }}>
                  <XAxis dataKey="time" stroke="#475569" fontSize={10} tickMargin={8} />
                  <YAxis yAxisId="left" stroke="#475569" fontSize={10} domain={[0, 1]} tickFormatter={v => v.toFixed(1)} />
                  <YAxis yAxisId="right" orientation="right" stroke="#475569" fontSize={10} domain={[0, 6]} />
                  <Tooltip 
                    contentStyle={{ backgroundColor: '#0f172a', borderColor: '#1e293b', fontSize: '11px', color: '#cbd5e1' }}
                  />
                  <Legend wrapperStyle={{ fontSize: '11px', paddingTop: '10px' }} iconType="circle" />
                  <Line yAxisId="left" type="monotone" name="Change Score" dataKey="score" stroke="#fbbf24" strokeWidth={2} dot={false} isAnimationActive={false} />
                  <Line yAxisId="right" type="stepAfter" name="Regions" dataKey="regions" stroke="#ef4444" strokeWidth={2} dot={false} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>

        {/* Change Intensity Graph (full width) */}
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 h-[220px] flex flex-col">
          <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-4">Change Intensity Graph</div>
          <div className="flex-1 min-h-0 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={timelineData} margin={{ top: 5, right: 0, left: -25, bottom: 0 }}>
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
    </AppShell>
  );
}
