import React, { useState, useEffect } from "react";
import AppShell from "@/components/layout/AppShell";
import { AreaChart, Area, ResponsiveContainer, XAxis, YAxis, Tooltip } from "recharts";
import { authFetch } from "@/lib/api";

export default function Dashboard() {
  const [recentMissions, setRecentMissions] = useState([]);

  const [edgeStatus, setEdgeStatus] = useState(null);
  const [telemetry, setTelemetry] = useState(null);
  const [activeMission, setActiveMission] = useState(null);
  const [elapsedTime, setElapsedTime] = useState("--");

  // Initialize altitude and speed charts cleanly with 0
  const [altData, setAltData] = useState(() => Array.from({ length: 30 }, (_, i) => ({ time: i, value: 0 })));
  const [spdData, setSpdData] = useState(() => Array.from({ length: 30 }, (_, i) => ({ time: i, value: 0 })));

  // Live calculation of active mission duration
  useEffect(() => {
    if (!activeMission?.start_time) {
      setElapsedTime("--");
      return;
    }
    const updateDuration = () => {
      const start = new Date(activeMission.start_time).getTime();
      const now = Date.now();
      const diffSeconds = Math.max(0, Math.floor((now - start) / 1000));
      const hrs = Math.floor(diffSeconds / 3600);
      const mins = Math.floor((diffSeconds % 3600) / 60);
      const secs = diffSeconds % 60;
      if (hrs > 0) {
        setElapsedTime(`${hrs}h ${mins.toString().padStart(2, '0')}m ${secs.toString().padStart(2, '0')}s`);
      } else {
        setElapsedTime(`${mins.toString().padStart(2, '0')}m ${secs.toString().padStart(2, '0')}s`);
      }
    };
    updateDuration();
    const timer = setInterval(updateDuration, 1000);
    return () => clearInterval(timer);
  }, [activeMission?.start_time]);

  useEffect(() => {
    let tick = 0;
    const poll = async () => {
      try {
        const [statusResp, telResp, missionResp] = await Promise.all([
          authFetch('/edge-api/api/edge/status'),
          authFetch('/edge-api/api/edge/telemetry'),
          authFetch('/edge-api/api/edge/mission/active'),
        ]);
        const status = await statusResp.json();
        const tel = await telResp.json();
        const missionData = await missionResp.json();
        
        setEdgeStatus(status);
        setTelemetry(tel);
        if (missionData.active) setActiveMission(missionData);
        else setActiveMission(null);
        
        tick++;
        const currentAlt = (tel && tel.altitude_m !== undefined && tel.altitude_m !== null && !isNaN(tel.altitude_m)) ? Math.round(tel.altitude_m) : 0;
        const currentSpd = (tel && tel.speed_mps !== undefined && tel.speed_mps !== null && !isNaN(tel.speed_mps)) ? Math.round(tel.speed_mps * 3.6) : 0;

        setAltData(prev => [...prev.slice(1), { time: tick, value: currentAlt }]);
        setSpdData(prev => [...prev.slice(1), { time: tick, value: currentSpd }]);
      } catch (err) {
        // Edge not available
      }
    };
    poll();
    const interval = setInterval(poll, 2000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    const fetchRecent = async () => {
      try {
        const resp = await authFetch('/api/missions');
        if (resp.ok) {
          const data = await resp.json();
          // Filter to only completed/recovered and take the last 2
          const completed = data.filter(m => m.status === 'completed' || m.status === 'recovered').slice(0, 2);
          setRecentMissions(completed);
        }
      } catch (e) {}
    };
    fetchRecent();
  }, []);

  const kpis = [
    { label: "Active Mission", value: activeMission ? "1" : "0", status: activeMission ? "green" : "neutral", unit: "" },
    { label: "Drone Link", value: edgeStatus?.connected ? "UP" : "DOWN", status: edgeStatus?.connected ? "green" : "red", unit: "" },
    { label: "Stream FPS", value: edgeStatus?.fps !== undefined ? Math.round(edgeStatus.fps) : "--", status: "cyan", unit: "fps" },
    { label: "Bitrate", value: edgeStatus?.bitrate_mbps !== undefined ? edgeStatus.bitrate_mbps.toFixed(1) : (edgeStatus?.connected ? "0.0" : "--"), status: "blue", unit: "Mbps" },
    { label: "Buffer Util.", value: edgeStatus?.buffer_fill_pct !== undefined ? Math.round(edgeStatus.buffer_fill_pct) : "--", status: edgeStatus?.buffer_fill_pct > 80 ? "red" : edgeStatus?.buffer_fill_pct > 60 ? "amber" : "neutral", unit: "%" },
    { label: "CPU Usage", value: edgeStatus?.cpu_pct !== undefined ? Math.round(edgeStatus.cpu_pct) : "--", status: edgeStatus?.cpu_pct > 80 ? "red" : "neutral", unit: "%" },
    { label: "GPU Usage", value: edgeStatus?.gpu_pct !== undefined ? Math.round(edgeStatus.gpu_pct) : "--", status: edgeStatus?.gpu_pct > 80 ? "red" : "purple", unit: "%" },
    { label: "Storage", value: edgeStatus?.storage_pct !== undefined ? Math.round(edgeStatus.storage_pct) : "--", status: edgeStatus?.storage_pct > 85 ? "red" : edgeStatus?.storage_pct > 70 ? "amber" : "green", unit: "%" },
    { label: "Upload Queue", value: edgeStatus?.upload_queue !== undefined ? edgeStatus.upload_queue : "--", status: "neutral", unit: "" },
    { label: "Selected Frames", value: edgeStatus?.frames_selected !== undefined ? edgeStatus.frames_selected.toLocaleString() : "--", status: "green", unit: "" },
    { label: "Discarded Frames", value: edgeStatus?.frames_total !== undefined && edgeStatus?.frames_selected !== undefined ? Math.max(0, edgeStatus.frames_total - edgeStatus.frames_selected).toLocaleString() : "--", status: "neutral", unit: "" },
    { label: "Drone Battery", value: telemetry && telemetry.battery_pct > 0 ? Math.round(telemetry.battery_pct) : "--", status: telemetry?.battery_pct > 50 ? "green" : telemetry?.battery_pct > 20 ? "amber" : "red", unit: "%" },
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

  const currentLiveAlt = (telemetry && telemetry.altitude_m !== undefined && telemetry.altitude_m !== null && !isNaN(telemetry.altitude_m)) ? Math.round(telemetry.altitude_m) : 0;
  const currentLiveSpd = (telemetry && telemetry.speed_mps !== undefined && telemetry.speed_mps !== null && !isNaN(telemetry.speed_mps)) ? Math.round(telemetry.speed_mps * 3.6) : 0;

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
              {activeMission && (
                <div className="flex items-center gap-2 text-xs px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                  ACTIVE
                </div>
              )}
            </div>
            <div className="text-xl font-bold text-sky-200 font-mono mb-1">{activeMission?.mission_id || 'NO ACTIVE MISSION'}</div>
            <div className="text-sm text-slate-300 font-medium mb-4">{activeMission?.mission_name || '--'}</div>
            <div className="grid grid-cols-2 gap-y-3 mt-auto text-sm">
              <div>
                <div className="text-[10px] text-slate-500 uppercase">Drone Assigned</div>
                <div className="text-slate-200">{activeMission?.drone_id || '--'}</div>
              </div>
              <div>
                <div className="text-[10px] text-slate-500 uppercase">Location</div>
                <div className="text-slate-200">{activeMission?.location || '--'}</div>
              </div>
              <div>
                <div className="text-[10px] text-slate-500 uppercase">Duration</div>
                <div className="text-slate-200 font-mono text-emerald-400 font-semibold">{elapsedTime}</div>
              </div>
            </div>
          </div>

          {/* Recent Missions */}
          {recentMissions.map((mission, idx) => (
            <div key={idx} className="bg-slate-900 border border-slate-800 p-5 rounded-lg flex flex-col relative overflow-hidden opacity-80">
              <div className="flex justify-between items-start mb-4">
                <div className="text-xs text-slate-500 font-semibold uppercase tracking-wider">Recent Mission</div>
                <div className="flex items-center gap-2 text-xs px-2 py-0.5 rounded-full bg-slate-500/10 border border-slate-500/20 text-slate-400">
                  COMPLETED
                </div>
              </div>
              <div className="text-xl font-bold text-slate-300 font-mono mb-1">{mission.mission_id || 'UNKNOWN'}</div>
              <div className="text-sm text-slate-400 font-medium mb-4">{mission.name || '--'}</div>
              <div className="grid grid-cols-2 gap-y-3 mt-auto text-sm">
                <div>
                  <div className="text-[10px] text-slate-500 uppercase">Drone Assigned</div>
                  <div className="text-slate-300">{mission.drone_id || '--'}</div>
                </div>
                <div>
                  <div className="text-[10px] text-slate-500 uppercase">Operator</div>
                  <div className="text-slate-300">{mission.operator_name || '--'}</div>
                </div>
                <div>
                  <div className="text-[10px] text-slate-500 uppercase">Location</div>
                  <div className="text-slate-300">{mission.zone || mission.location || '--'}</div>
                </div>
                <div>
                  <div className="text-[10px] text-slate-500 uppercase">Started</div>
                  <div className="text-slate-300 font-mono text-xs">{mission.started_at ? new Date(mission.started_at).toLocaleString() : '--'}</div>
                </div>
              </div>
            </div>
          ))}

          {recentMissions.length < 2 && Array.from({ length: 2 - recentMissions.length }).map((_, idx) => (
            <div key={`empty-${idx}`} className="bg-slate-900 border border-slate-800 p-5 rounded-lg flex flex-col justify-center items-center opacity-50">
               <div className="text-slate-500 text-sm">No recent missions</div>
            </div>
          ))}

        </div>

        {/* Bottom Section (2 Trend Charts) */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 h-64">
          <div className="bg-slate-900 border border-slate-800 p-4 rounded-lg flex flex-col">
            <div className="text-xs text-slate-500 font-semibold uppercase tracking-wider mb-4 flex items-center justify-between">
              <span>Altitude Trend</span>
              <span className="text-cyan-400 font-mono text-xs font-normal">Live: {currentLiveAlt}m</span>
            </div>
            <div className="flex-1 min-h-0 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={altData} margin={{ top: 5, right: 0, left: -20, bottom: 0 }}>
                  <defs>
                    <linearGradient id="colorAlt" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#22d3ee" stopOpacity={0.3}/>
                      <stop offset="95%" stopColor="#22d3ee" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="time" hide />
                  <YAxis stroke="#475569" fontSize={10} domain={[0, 'auto']} tickFormatter={(v) => `${v}m`} />
                  <Tooltip 
                    contentStyle={{ backgroundColor: '#0f172a', borderColor: '#1e293b', fontSize: '12px', color: '#cbd5e1' }}
                    itemStyle={{ color: '#22d3ee' }}
                  />
                  <Area type="monotone" dataKey="value" stroke="#22d3ee" strokeWidth={2} fillOpacity={1} fill="url(#colorAlt)" isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>
          
          <div className="bg-slate-900 border border-slate-800 p-4 rounded-lg flex flex-col">
            <div className="text-xs text-slate-500 font-semibold uppercase tracking-wider mb-4 flex items-center justify-between">
              <span>Speed Trend</span>
              <span className="text-amber-400 font-mono text-xs font-normal">Live: {currentLiveSpd} km/h</span>
            </div>
            <div className="flex-1 min-h-0 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={spdData} margin={{ top: 5, right: 0, left: -20, bottom: 0 }}>
                  <defs>
                    <linearGradient id="colorSpd" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#fbbf24" stopOpacity={0.3}/>
                      <stop offset="95%" stopColor="#fbbf24" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="time" hide />
                  <YAxis stroke="#475569" fontSize={10} domain={[0, 'auto']} tickFormatter={(v) => `${v}kph`} />
                  <Tooltip 
                    contentStyle={{ backgroundColor: '#0f172a', borderColor: '#1e293b', fontSize: '12px', color: '#cbd5e1' }}
                    itemStyle={{ color: '#fbbf24' }}
                  />
                  <Area type="monotone" dataKey="value" stroke="#fbbf24" strokeWidth={2} fillOpacity={1} fill="url(#colorSpd)" isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>

      </div>
    </AppShell>
  );
}
