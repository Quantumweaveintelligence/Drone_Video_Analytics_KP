import React, { useState, useEffect } from 'react';
import AppShell from '@/components/layout/AppShell';
import { LineChart, Line, ResponsiveContainer, XAxis, YAxis, Tooltip, Legend } from "recharts";
import { CheckCircle2, Server, Clock } from 'lucide-react';
import { authFetch } from '@/lib/api';

const DonutGauge = ({ percentage, label, subtext, forceColor }) => {
  const radius = 50;
  const stroke = 10;
  const normalizedRadius = radius - stroke * 2;
  const circumference = normalizedRadius * 2 * Math.PI;
  const strokeDashoffset = circumference - ((percentage || 0) / 100) * circumference;

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
          <span className="text-lg font-bold font-mono" style={{ color }}>{(percentage || 0).toFixed(1)}%</span>
        </div>
      </div>
      <div className="mt-3 text-[10px] text-slate-400 font-semibold uppercase tracking-wider">{label}</div>
      {subtext && <div className="mt-1 text-[10px] text-slate-400 font-mono bg-slate-950 px-2 py-0.5 rounded border border-slate-800">{subtext}</div>}
    </div>
  );
};

export default function SystemHealth() {
  const [healthData, setHealthData] = useState({
    cpu_pct: 0,
    cpu_temp: null,
    gpu_pct: 0,
    gpu_temp: null,
    gpu_mem_used_mb: 0,
    gpu_mem_total_mb: 0,
    memory_pct: 0,
    memory_used_mb: 0,
    memory_total_mb: 0,
    storage_pct: 0,
    storage_used_gb: 0,
    storage_total_gb: 0,
    uptime_seconds: 0,
    uptime_str: "--:--:--",
    software_version: "3.2.0-edge",
    node_health: "HEALTHY",
    time_sync: "SYNCED",
    services: [
      { name: "Stream Manager", status: "STANDBY" },
      { name: "Rolling Buffer", status: "STANDBY" },
      { name: "Cognitive Selector", status: "STANDBY" },
      { name: "Change Detector", status: "STANDBY" },
      { name: "Ingest Client", status: "STANDBY" },
      { name: "Telemetry Feed", status: "STANDBY" },
      { name: "FastAPI Edge Server", status: "OK" },
      { name: "Keycloak Security", status: "OK" },
    ],
    history: []
  });

  useEffect(() => {
    let isMounted = true;

    const fetchHealth = async () => {
      try {
        const resp = await authFetch('/edge-api/api/edge/system-health');
        if (resp.ok && isMounted) {
          const data = await resp.json();
          setHealthData(data);
        }
      } catch (err) {
        console.error("Failed to fetch edge system health:", err);
      }
    };

    fetchHealth();
    const interval = setInterval(fetchHealth, 2000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, []);

  const getBarColor = (val) => {
    if (val >= 80) return "bg-red-500";
    if (val >= 60) return "bg-amber-500";
    return "bg-emerald-500";
  };

  const getStatusBadge = (status) => {
    switch (status) {
      case "OK":
        return (
          <span className="text-[9px] flex items-center gap-1 font-mono tracking-wider text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20">
            <CheckCircle2 size={10} /> OK
          </span>
        );
      case "STANDBY":
        return (
          <span className="text-[9px] flex items-center gap-1 font-mono tracking-wider text-sky-400 bg-sky-500/10 px-2 py-0.5 rounded border border-sky-500/20">
            <Clock size={10} /> STANDBY
          </span>
        );
      case "CONNECTING":
        return (
          <span className="text-[9px] flex items-center gap-1 font-mono tracking-wider text-amber-400 bg-amber-500/10 px-2 py-0.5 rounded border border-amber-500/20 animate-pulse">
            CONNECTING
          </span>
        );
      default:
        return (
          <span className="text-[9px] flex items-center gap-1 font-mono tracking-wider text-slate-400 bg-slate-800 px-2 py-0.5 rounded border border-slate-700">
            {status}
          </span>
        );
    }
  };

  const memoryUsedGB = (healthData.memory_used_mb / 1024).toFixed(1);
  const memoryTotalGB = (healthData.memory_total_mb / 1024).toFixed(1);
  const isHealthy = healthData.node_health === "HEALTHY";

  return (
    <AppShell title="System Health" subtitle="Live edge node hardware, subsystem statuses and resource metrics">
      <div className="space-y-4">
        
        {/* Top 4 Donut Gauges */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 flex items-center justify-center">
            <DonutGauge 
              percentage={healthData.cpu_pct} 
              label="CPU Usage" 
              subtext={healthData.cpu_temp ? `TEMP: ${healthData.cpu_temp}°C` : "HOST CORES"} 
            />
          </div>
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 flex items-center justify-center">
            <DonutGauge 
              percentage={healthData.gpu_pct} 
              label="GPU Usage" 
              subtext={healthData.gpu_temp ? `TEMP: ${healthData.gpu_temp}°C` : (healthData.gpu_pct > 0 ? "ACTIVE" : "IDLE")} 
              forceColor={healthData.gpu_pct > 0 ? undefined : "#64748b"}
            />
          </div>
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 flex items-center justify-center">
            <DonutGauge 
              percentage={healthData.memory_pct} 
              label="Memory Usage" 
              subtext={`${memoryUsedGB} GB / ${memoryTotalGB} GB`}
              forceColor="#3b82f6" 
            />
          </div>
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 flex items-center justify-center">
            <DonutGauge 
              percentage={healthData.storage_pct} 
              label="Storage Capacity" 
              subtext={`${healthData.storage_used_gb} GB / ${healthData.storage_total_gb} GB`}
              forceColor="#06b6d4" 
            />
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
          
          {/* Node Status Card (4 cols) */}
          <div className="lg:col-span-4 bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col justify-between">
            <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-5 flex items-center gap-2">
              <Server size={14} className="text-sky-400" /> Node Status
            </div>
            
            <div className="space-y-4 flex-1">
              <div className="flex justify-between items-center pb-2 border-b border-slate-800">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider">Node Health</span>
                <span className={`text-sm font-mono font-bold ${isHealthy ? 'text-emerald-400' : 'text-amber-400'}`}>
                  {healthData.node_health}
                </span>
              </div>
              <div className="flex justify-between items-center pb-2 border-b border-slate-800">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider">Software Version</span>
                <span className="text-xs font-mono text-cyan-400">{healthData.software_version}</span>
              </div>
              <div className="flex justify-between items-center pb-2 border-b border-slate-800">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider">Uptime</span>
                <span className="text-xs font-mono text-slate-200">{healthData.uptime_str}</span>
              </div>
              <div className="flex justify-between items-center pb-2 border-b border-slate-800">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider">Time Sync</span>
                <span className="text-[10px] flex items-center gap-1 font-mono text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20">
                  <CheckCircle2 size={12} /> {healthData.time_sync}
                </span>
              </div>
            </div>

            <div className={`mt-5 rounded p-3 flex items-center justify-center gap-3 border ${
              isHealthy ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : 'bg-amber-500/10 border-amber-500/20 text-amber-400'
            }`}>
              <span className={`w-2 h-2 rounded-full animate-pulse ${isHealthy ? 'bg-emerald-500' : 'bg-amber-500'}`}></span>
              <span className="text-[10px] font-bold tracking-wider uppercase">
                {isHealthy ? "All Systems Operational" : "High Resource Load"}
              </span>
            </div>
          </div>

          {/* Resource Usage Card (4 cols) */}
          <div className="lg:col-span-4 bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col">
            <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-5">Resource Usage</div>
            
            <div className="space-y-6 flex-1">
              <div>
                <div className="flex justify-between text-[10px] font-semibold uppercase tracking-wider mb-1.5">
                  <span className="text-slate-400">CPU</span>
                  <span className="text-emerald-400 font-mono">{(healthData.cpu_pct || 0).toFixed(1)}%</span>
                </div>
                <div className="w-full bg-slate-800 h-2 rounded-full overflow-hidden">
                  <div className={`${getBarColor(healthData.cpu_pct)} h-full rounded-full transition-all duration-500`} style={{ width: `${Math.min(100, healthData.cpu_pct || 0)}%` }}></div>
                </div>
              </div>
              <div>
                <div className="flex justify-between text-[10px] font-semibold uppercase tracking-wider mb-1.5">
                  <span className="text-slate-400">GPU</span>
                  <span className="text-amber-400 font-mono">{(healthData.gpu_pct || 0).toFixed(1)}%</span>
                </div>
                <div className="w-full bg-slate-800 h-2 rounded-full overflow-hidden">
                  <div className={`${getBarColor(healthData.gpu_pct)} h-full rounded-full transition-all duration-500`} style={{ width: `${Math.min(100, healthData.gpu_pct || 0)}%` }}></div>
                </div>
              </div>
              <div>
                <div className="flex justify-between text-[10px] font-semibold uppercase tracking-wider mb-1.5">
                  <span className="text-slate-400">Memory</span>
                  <span className="text-blue-400 font-mono">{(healthData.memory_pct || 0).toFixed(1)}%</span>
                </div>
                <div className="w-full bg-slate-800 h-2 rounded-full overflow-hidden">
                  <div className="bg-blue-500 h-full rounded-full transition-all duration-500" style={{ width: `${Math.min(100, healthData.memory_pct || 0)}%` }}></div>
                </div>
              </div>
              <div>
                <div className="flex justify-between text-[10px] font-semibold uppercase tracking-wider mb-1.5">
                  <span className="text-slate-400">Storage</span>
                  <span className="text-cyan-400 font-mono">{(healthData.storage_pct || 0).toFixed(1)}%</span>
                </div>
                <div className="w-full bg-slate-800 h-2 rounded-full overflow-hidden">
                  <div className="bg-cyan-500 h-full rounded-full transition-all duration-500" style={{ width: `${Math.min(100, healthData.storage_pct || 0)}%` }}></div>
                </div>
              </div>
            </div>
          </div>

          {/* Service Status Card (4 cols) */}
          <div className="lg:col-span-4 bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col">
            <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-4">Service Status</div>
            
            <div className="grid grid-cols-1 gap-2 flex-1 content-start">
              {(healthData.services || []).map((svc, i) => (
                <div key={i} className="flex justify-between items-center py-1.5 border-b border-slate-800/50 last:border-0">
                  <span className="text-xs text-slate-300 font-medium">{svc.name}</span>
                  {getStatusBadge(svc.status)}
                </div>
              ))}
            </div>
          </div>

        </div>

        {/* Resource Utilization History Chart (Full width) */}
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col h-[260px]">
          <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-4">Resource Utilization History (Live)</div>
          <div className="flex-1 min-h-0 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={healthData.history || []} margin={{ top: 5, right: 0, left: -20, bottom: 0 }}>
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
