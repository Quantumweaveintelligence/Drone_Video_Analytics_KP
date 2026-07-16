import React, { useState } from "react";
import AppShell from "@/components/layout/AppShell";
import { AreaChart, Area, ResponsiveContainer, XAxis, YAxis, Tooltip } from "recharts";
import { Send as Drone, AlertTriangle as Warning, Info } from "lucide-react";

// Mock data generation for charts
const generateChartData = (base, variance, count) => {
  return Array.from({ length: count }).map((_, i) => ({
    time: i,
    value: Math.max(0, Math.round(base + (Math.random() - 0.5) * variance))
  }));
};

const altitudeData = generateChartData(120, 20, 30);
const speedData = generateChartData(45, 10, 30);

export default function Dashboard() {
  const [activeAlerts, setActiveAlerts] = useState([
    { id: 1, text: "Unidentified object detected in sector 7G", severity: "amber", ack: false, time: "14:32:01" },
    { id: 2, text: "Wind speed exceeding safe operating limits", severity: "red", ack: false, time: "14:30:44" },
    { id: 3, text: "Mission waypoint 4 reached", severity: "neutral", ack: true, time: "14:28:12" },
  ]);

  const toggleAck = (id) => {
    setActiveAlerts(prev => prev.map(a => a.id === id ? { ...a, ack: !a.ack } : a));
  };

  const kpis = [
    { label: "Active Mission", value: "1", status: "green", unit: "" },
    { label: "Drone Link", value: "UP", status: "green", unit: "" },
    { label: "Stream FPS", value: "30", status: "cyan", unit: "fps" },
    { label: "Bitrate", value: "4.2", status: "blue", unit: "Mbps" },
    { label: "Buffer Util.", value: "74", status: "amber", unit: "%" },
    { label: "CPU Usage", value: "42", status: "neutral", unit: "%" },
    { label: "GPU Usage", value: "68", status: "purple", unit: "%" },
    { label: "Storage", value: "62", status: "amber", unit: "%" },
    { label: "Upload Queue", value: "127", status: "neutral", unit: "" },
    { label: "Selected Frames", value: "12,800", status: "green", unit: "" },
    { label: "Discarded Frames", value: "32,200", status: "neutral", unit: "" },
    { label: "Drone Battery", value: "78", status: "green", unit: "%" },
  ];

  const getColorClass = (status) => {
    const map = {
      green: "text-emerald-400",
      red: "text-red-400",
      amber: "text-amber-400",
      cyan: "text-cyan-400",
      blue: "text-sky-400",
      purple: "text-purple-400",
      neutral: "text-slate-300"
    };
    return map[status] || map.neutral;
  };

  return (
    <AppShell title="Dashboard" subtitle="Central Operational Overview">
      <div className="space-y-6">
        
        {/* KPI Grid (12 Cards) */}
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          {kpis.map((kpi, idx) => (
            <div key={idx} className="bg-slate-900 border border-slate-800 p-3 rounded-lg flex flex-col justify-between">
              <div className="text-[10px] text-slate-500 uppercase tracking-wider font-semibold">{kpi.label}</div>
              <div className="mt-2 flex items-baseline gap-1">
                <span className={`text-2xl font-bold mono ${getColorClass(kpi.status)}`}>{kpi.value}</span>
                {kpi.unit && <span className="text-xs text-slate-500">{kpi.unit}</span>}
              </div>
            </div>
          ))}
        </div>

        {/* Middle Section (3 Cards) */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          
          {/* Active Mission Card */}
          <div className="bg-slate-900 border border-slate-800 p-5 rounded-lg flex flex-col relative overflow-hidden">
            <div className="flex justify-between items-start mb-4">
              <div className="text-xs text-slate-500 font-semibold uppercase tracking-wider">Active Mission</div>
              <div className="flex items-center gap-2 text-xs px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                ACTIVE
              </div>
            </div>
            <div className="text-xl font-bold text-sky-200 font-mono mb-1">MSN-9942-ALPHA</div>
            <div className="text-sm text-slate-300 font-medium mb-4">Coastal Surveillance Patrolling</div>
            <div className="grid grid-cols-2 gap-y-3 mt-auto text-sm">
              <div>
                <div className="text-[10px] text-slate-500 uppercase">Drone Assigned</div>
                <div className="text-slate-200">DRN-001</div>
              </div>
              <div>
                <div className="text-[10px] text-slate-500 uppercase">Location</div>
                <div className="text-slate-200">Sector 4, Marina</div>
              </div>
              <div>
                <div className="text-[10px] text-slate-500 uppercase">Duration</div>
                <div className="text-slate-200">42 mins</div>
              </div>
            </div>
          </div>

          {/* Drone Asset Card */}
          <div className="bg-slate-900 border border-slate-800 p-5 rounded-lg flex flex-col">
            <div className="flex justify-between items-start mb-5">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-full bg-slate-800 flex items-center justify-center text-slate-300 border border-slate-700">
                  <Drone size={24} />
                </div>
                <div>
                  <div className="text-sm font-bold text-slate-100">Garuda-1</div>
                  <div className="text-xs text-slate-500 font-mono">DRN-001</div>
                </div>
              </div>
              <div className="text-[10px] px-2 py-1 rounded-sm bg-sky-500/10 border border-sky-500/20 text-sky-400 font-mono tracking-wider">
                CONNECTED
              </div>
            </div>
            
            <div className="space-y-4 mt-auto">
              <div>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-slate-400">Battery</span>
                  <span className="text-emerald-400 font-mono">78%</span>
                </div>
                <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
                  <div className="bg-emerald-500 h-full rounded-full" style={{ width: '78%' }}></div>
                </div>
              </div>
              
              <div className="flex justify-between items-center text-xs">
                <span className="text-slate-400">Signal Strength</span>
                <span className="text-slate-200 font-mono">92%</span>
              </div>
              <div className="flex justify-between items-center text-xs pt-2 border-t border-slate-800">
                <span className="text-slate-500">Last Seen</span>
                <span className="text-slate-400 font-mono">Just now</span>
              </div>
            </div>
          </div>

          {/* Active Alerts Card */}
          <div className="bg-slate-900 border border-slate-800 p-5 rounded-lg flex flex-col">
            <div className="text-xs text-slate-500 font-semibold uppercase tracking-wider mb-4">Active Alerts</div>
            <div className="space-y-3 overflow-y-auto max-h-[160px] pr-1 scrollbar-thin scrollbar-thumb-slate-700">
              {activeAlerts.map(alert => (
                <div key={alert.id} className={`flex gap-3 text-xs p-2 rounded border ${alert.ack ? 'bg-slate-950 border-slate-800 opacity-60' : 'bg-slate-800/50 border-slate-700'}`}>
                  <div className="mt-0.5 shrink-0">
                    {alert.severity === 'red' ? <Warning className="text-red-400" size={14} /> :
                     alert.severity === 'amber' ? <Warning className="text-amber-400" size={14} /> :
                     <Info className="text-slate-400" size={14} />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className={`mb-1 ${alert.ack ? 'text-slate-500' : (alert.severity === 'red' ? 'text-red-300' : alert.severity === 'amber' ? 'text-amber-300' : 'text-slate-300')}`}>
                      {alert.text}
                    </div>
                    <div className="text-[10px] text-slate-500 font-mono">{alert.time}</div>
                  </div>
                  <button 
                    onClick={() => toggleAck(alert.id)}
                    className={`shrink-0 px-2 h-6 text-[9px] uppercase tracking-wider font-bold rounded flex items-center gap-1 ${alert.ack ? 'bg-slate-800 text-slate-500' : 'bg-slate-700 hover:bg-slate-600 text-slate-200'}`}
                  >
                    {alert.ack ? 'ACKED' : 'ACK'}
                  </button>
                </div>
              ))}
              {activeAlerts.length === 0 && (
                <div className="text-sm text-slate-500 text-center py-4">No active alerts.</div>
              )}
            </div>
          </div>

        </div>

        {/* Bottom Section (2 Trend Charts) */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 h-64">
          <div className="bg-slate-900 border border-slate-800 p-4 rounded-lg flex flex-col">
            <div className="text-xs text-slate-500 font-semibold uppercase tracking-wider mb-4">Altitude Trend</div>
            <div className="flex-1 min-h-0 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={altitudeData} margin={{ top: 5, right: 0, left: -20, bottom: 0 }}>
                  <defs>
                    <linearGradient id="colorAlt" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#22d3ee" stopOpacity={0.3}/>
                      <stop offset="95%" stopColor="#22d3ee" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="time" hide />
                  <YAxis stroke="#475569" fontSize={10} tickFormatter={(v) => `${v}m`} />
                  <Tooltip 
                    contentStyle={{ backgroundColor: '#0f172a', borderColor: '#1e293b', fontSize: '12px', color: '#cbd5e1' }}
                    itemStyle={{ color: '#22d3ee' }}
                  />
                  <Area type="monotone" dataKey="value" stroke="#22d3ee" strokeWidth={2} fillOpacity={1} fill="url(#colorAlt)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>
          
          <div className="bg-slate-900 border border-slate-800 p-4 rounded-lg flex flex-col">
            <div className="text-xs text-slate-500 font-semibold uppercase tracking-wider mb-4">Speed Trend</div>
            <div className="flex-1 min-h-0 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={speedData} margin={{ top: 5, right: 0, left: -20, bottom: 0 }}>
                  <defs>
                    <linearGradient id="colorSpd" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#fbbf24" stopOpacity={0.3}/>
                      <stop offset="95%" stopColor="#fbbf24" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="time" hide />
                  <YAxis stroke="#475569" fontSize={10} tickFormatter={(v) => `${v}kph`} />
                  <Tooltip 
                    contentStyle={{ backgroundColor: '#0f172a', borderColor: '#1e293b', fontSize: '12px', color: '#cbd5e1' }}
                    itemStyle={{ color: '#fbbf24' }}
                  />
                  <Area type="monotone" dataKey="value" stroke="#fbbf24" strokeWidth={2} fillOpacity={1} fill="url(#colorSpd)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>

      </div>
    </AppShell>
  );
}
