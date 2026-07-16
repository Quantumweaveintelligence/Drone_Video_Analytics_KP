import React from 'react';
import AppShell from '@/components/layout/AppShell';
import { LineChart, Line, ResponsiveContainer, XAxis, YAxis, Tooltip, Legend } from "recharts";
import { CheckCircle2, Server } from 'lucide-react';

const generateData = (count) => {
  return Array.from({ length: count }).map((_, i) => ({
    time: `-${(count - i) * 2}m`,
    cpu: Math.max(0, 42.3 + (Math.random() - 0.5) * 15),
    gpu: Math.max(0, 68.1 + (Math.random() - 0.5) * 20),
    memory: Math.max(0, 55.7 + (Math.random() - 0.5) * 5),
  }));
};

const historyData = generateData(20);

const DonutGauge = ({ percentage, label, subtext, forceColor }) => {
  const radius = 50;
  const stroke = 10;
  const normalizedRadius = radius - stroke * 2;
  const circumference = normalizedRadius * 2 * Math.PI;
  const strokeDashoffset = circumference - (percentage / 100) * circumference;

  let color = forceColor;
  if (!color) {
    if (percentage >= 80) color = "#ef4444"; // red-500
    else if (percentage >= 60) color = "#f59e0b"; // amber-500
    else color = "#10b981"; // emerald-500
  }

  return (
    <div className="flex flex-col items-center justify-center">
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
          <span className="text-lg font-bold font-mono" style={{ color }}>{percentage.toFixed(1)}%</span>
        </div>
      </div>
      <div className="mt-3 text-[10px] text-slate-400 font-semibold uppercase tracking-wider">{label}</div>
      {subtext && <div className="mt-1 text-[10px] text-slate-500 font-mono bg-slate-950 px-2 py-0.5 rounded border border-slate-800">{subtext}</div>}
    </div>
  );
};

export default function SystemHealth() {
  const services = [
    { name: "Stream Manager", status: "OK" },
    { name: "Buffer Service", status: "OK" },
    { name: "Frame Selector", status: "OK" },
    { name: "Change Detector", status: "OK" },
    { name: "Ingest Client", status: "OK" },
    { name: "Telemetry Feed", status: "OK" },
    { name: "GPS Module", status: "OK" },
    { name: "Socket Gateway", status: "OK" }
  ];

  const getBarColor = (val) => {
    if (val >= 80) return "bg-red-500";
    if (val >= 60) return "bg-amber-500";
    return "bg-emerald-500";
  };

  return (
    <AppShell title="System Health" subtitle="Monitor edge node hardware and software health">
      <div className="space-y-4">
        
        {/* Top 4 Donut Gauges */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 flex items-center justify-center">
            <DonutGauge percentage={42.3} label="CPU Usage" subtext="TEMP: 58°C" />
          </div>
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 flex items-center justify-center">
            <DonutGauge percentage={68.1} label="GPU Usage" subtext="TEMP: 72°C" />
          </div>
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 flex items-center justify-center">
            <DonutGauge percentage={55.7} label="Memory Usage" forceColor="#3b82f6" />
          </div>
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 flex items-center justify-center">
            <DonutGauge percentage={62.4} label="Storage Capacity" forceColor="#06b6d4" />
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
          
          {/* Node Status Card (4 cols) */}
          <div className="lg:col-span-4 bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col justify-between">
            <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-5 flex items-center gap-2">
              <Server size={14} /> Node Status
            </div>
            
            <div className="space-y-4 flex-1">
              <div className="flex justify-between items-center pb-2 border-b border-slate-800">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider">Node Health</span>
                <span className="text-sm font-mono text-emerald-400 font-bold">HEALTHY</span>
              </div>
              <div className="flex justify-between items-center pb-2 border-b border-slate-800">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider">Software Version</span>
                <span className="text-xs font-mono text-cyan-400">2.4.1-edge</span>
              </div>
              <div className="flex justify-between items-center pb-2 border-b border-slate-800">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider">Uptime</span>
                <span className="text-xs font-mono text-slate-200">5d 0h 0m</span>
              </div>
              <div className="flex justify-between items-center pb-2 border-b border-slate-800">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider">Time Sync</span>
                <span className="text-[10px] flex items-center gap-1 font-mono text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20">
                  <CheckCircle2 size={12} /> SYNCED
                </span>
              </div>
            </div>

            <div className="mt-5 bg-emerald-500/10 border border-emerald-500/20 rounded p-3 flex items-center justify-center gap-3">
              <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
              <span className="text-[10px] font-bold text-emerald-400 tracking-wider uppercase">All Systems Operational</span>
            </div>
          </div>

          {/* Resource Usage Card (4 cols) */}
          <div className="lg:col-span-4 bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col">
            <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-5">Resource Usage</div>
            
            <div className="space-y-6 flex-1">
              <div>
                <div className="flex justify-between text-[10px] font-semibold uppercase tracking-wider mb-1.5">
                  <span className="text-slate-400">CPU</span>
                  <span className="text-emerald-400 font-mono">42.3%</span>
                </div>
                <div className="w-full bg-slate-800 h-2 rounded-full overflow-hidden">
                  <div className={`${getBarColor(42.3)} h-full rounded-full`} style={{ width: '42.3%' }}></div>
                </div>
              </div>
              <div>
                <div className="flex justify-between text-[10px] font-semibold uppercase tracking-wider mb-1.5">
                  <span className="text-slate-400">GPU</span>
                  <span className="text-amber-400 font-mono">68.1%</span>
                </div>
                <div className="w-full bg-slate-800 h-2 rounded-full overflow-hidden">
                  <div className={`${getBarColor(68.1)} h-full rounded-full`} style={{ width: '68.1%' }}></div>
                </div>
              </div>
              <div>
                <div className="flex justify-between text-[10px] font-semibold uppercase tracking-wider mb-1.5">
                  <span className="text-slate-400">Memory</span>
                  <span className="text-blue-400 font-mono">55.7%</span>
                </div>
                <div className="w-full bg-slate-800 h-2 rounded-full overflow-hidden">
                  <div className="bg-blue-500 h-full rounded-full" style={{ width: '55.7%' }}></div>
                </div>
              </div>
              <div>
                <div className="flex justify-between text-[10px] font-semibold uppercase tracking-wider mb-1.5">
                  <span className="text-slate-400">Storage</span>
                  <span className="text-cyan-400 font-mono">62.4%</span>
                </div>
                <div className="w-full bg-slate-800 h-2 rounded-full overflow-hidden">
                  <div className="bg-cyan-500 h-full rounded-full" style={{ width: '62.4%' }}></div>
                </div>
              </div>
            </div>
          </div>

          {/* Service Status Card (4 cols) */}
          <div className="lg:col-span-4 bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col">
            <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-4">Service Status</div>
            
            <div className="grid grid-cols-1 gap-2 flex-1 content-start">
              {services.map((svc, i) => (
                <div key={i} className="flex justify-between items-center py-1.5 border-b border-slate-800/50 last:border-0">
                  <span className="text-xs text-slate-300 font-medium">{svc.name}</span>
                  <span className="text-[9px] flex items-center gap-1 font-mono tracking-wider text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20">
                    <CheckCircle2 size={10} /> {svc.status}
                  </span>
                </div>
              ))}
            </div>
          </div>

        </div>

        {/* Resource Utilization History Chart (Full width) */}
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col h-[260px]">
          <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-4">Resource Utilization History</div>
          <div className="flex-1 min-h-0 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={historyData} margin={{ top: 5, right: 0, left: -20, bottom: 0 }}>
                <XAxis dataKey="time" stroke="#475569" fontSize={10} tickMargin={8} />
                <YAxis stroke="#475569" fontSize={10} tickFormatter={(v) => `${v}%`} domain={[0, 100]} />
                <Tooltip 
                  contentStyle={{ backgroundColor: '#0f172a', borderColor: '#1e293b', fontSize: '11px', color: '#cbd5e1' }}
                />
                <Legend wrapperStyle={{ fontSize: '11px', paddingTop: '10px' }} iconType="circle" />
                <Line type="monotone" name="GPU Usage" dataKey="gpu" stroke="#a855f7" strokeWidth={2} dot={false} isAnimationActive={false} />
                <Line type="monotone" name="Memory Usage" dataKey="memory" stroke="#3b82f6" strokeWidth={2} dot={false} isAnimationActive={false} />
                <Line type="monotone" name="CPU Usage" dataKey="cpu" stroke="#10b981" strokeWidth={2} dot={false} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

      </div>
    </AppShell>
  );
}
