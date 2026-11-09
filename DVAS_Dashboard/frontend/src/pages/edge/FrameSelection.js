import React, { useState, useEffect, useRef } from 'react';
import AppShell from '@/components/layout/AppShell';
import { 
  LineChart, Line, PieChart, Pie, Cell, 
  ResponsiveContainer, XAxis, YAxis, Tooltip, Legend 
} from "recharts";
import { Image as ImageIcon, History } from 'lucide-react';
import { authFetch } from '@/lib/api';
import { useEdgeLive } from "@/lib/edgeLive";

const COLORS = ['#34d399', '#475569']; // emerald-400 (Selected), slate-600 (Discarded)

export default function FrameSelection() {
  const [timelineData, setTimelineData] = useState(Array.from({ length: 20 }).map((_, i) => ({
    time: `-${(20 - i) * 2}s`,
    selected: 0,
    discarded: 0,
    total: 0
  })));
  
  const [stats, setStats] = useState({
    framesReceived: 0, // Total raw frames captured from the video file or RTMP stream
    framesSelected: 0, // Total frames selected by the Cognitive Selection algorithm
    framesDiscarded: 0, // Frames Received - Frames Selected
  });

  const [missionHistory, setMissionHistory] = useState(() => {
    try {
      const saved = localStorage.getItem('dvas_frame_selection_history');
      return saved ? JSON.parse(saved) : [];
    } catch {
      return [];
    }
  });

  const prevStatsRef = useRef({
    framesReceived: 0,
    framesSelected: 0,
    framesDiscarded: 0,
    initialized: false
  });

  const { getSnapshot } = useEdgeLive();
  const historyRef = useRef({ key: null, rows: [] });

  // Live edge stats from the shared snapshot; mission history only when the mission changes
  useEffect(() => {
    const fetchStats = async () => {
      try {
        const snap = getSnapshot();
        if (!snap) return;
        const missionKey = snap.mission?.active ? `${snap.mission.mission_id}` : 'none';
        if (historyRef.current.key !== missionKey) {
          const historyResp = await authFetch('/edge-api/api/edge/mission/history');
          if (historyResp.ok) {
            historyRef.current = { key: missionKey, rows: await historyResp.json() };
          }
        }

        let totalFrames = 0;
        let selectedFrames = 0;
        let discarded = 0;

        {
          const data = snap.status || {};
          totalFrames = data.frames_total || 0;
          selectedFrames = data.frames_selected || 0;
          discarded = data.frames_discarded !== undefined
            ? data.frames_discarded
            : Math.max(0, (data.pipeline_frames_in || totalFrames) - selectedFrames);

          setStats({
            framesReceived: totalFrames,
            framesSelected: selectedFrames,
            framesDiscarded: discarded
          });

          // Calculate delta for the live timeline
          if (prevStatsRef.current.initialized) {
            const deltaTotal = Math.max(0, totalFrames - prevStatsRef.current.framesReceived);
            const deltaSelected = Math.max(0, selectedFrames - prevStatsRef.current.framesSelected);
            const deltaDiscarded = Math.max(0, discarded - prevStatsRef.current.framesDiscarded);

            setTimelineData(prev => {
              const nowStr = new Date().toLocaleTimeString([], { hour12: false, second: '2-digit', minute: '2-digit' });
              return [...prev.slice(1), {
                time: nowStr,
                selected: deltaSelected,
                discarded: deltaDiscarded,
                total: deltaTotal
              }];
            });
          } else {
            prevStatsRef.current.initialized = true;
          }

          prevStatsRef.current.framesReceived = totalFrames;
          prevStatsRef.current.framesSelected = selectedFrames;
          prevStatsRef.current.framesDiscarded = discarded;
        }

        // Process mission history
        const serverHistory = historyRef.current.rows;
        const activeMission = snap.mission?.active ? snap.mission : null;

        setMissionHistory(prev => {
          let merged = Array.isArray(serverHistory) && serverHistory.length > 0 ? [...serverHistory] : [...prev];
          
          if (activeMission) {
            const activeId = activeMission.mission_id;
            const existingIdx = merged.findIndex(m => m.mission_id === activeId);
            const liveRecord = {
              mission_id: activeId,
              mission_name: activeMission.mission_name || activeMission.name || 'Active Mission',
              drone_id: activeMission.drone_id,
              frames_received: totalFrames,
              frames_selected: selectedFrames,
              frames_discarded: discarded,
              status: 'active'
            };

            if (existingIdx >= 0) {
              merged[existingIdx] = liveRecord;
            } else {
              merged.unshift(liveRecord);
            }
          }

          localStorage.setItem('dvas_frame_selection_history', JSON.stringify(merged));
          return merged;
        });

      } catch (err) {
        console.error("Failed to fetch live edge stats:", err);
      }
    };

    fetchStats();
    const interval = setInterval(fetchStats, 2000);
    return () => clearInterval(interval);
  }, [getSnapshot]);

  // TX Reduction: Percentage of frames that are being discarded
  const txReduction = stats.framesReceived > 0 
    ? ((stats.framesDiscarded / stats.framesReceived) * 100).toFixed(1) 
    : "0.0";

  const selectionRatio = stats.framesReceived > 0
    ? ((stats.framesSelected / stats.framesReceived) * 100).toFixed(1)
    : "0.0";

  const compressionRatio = stats.framesSelected > 0
    ? (stats.framesReceived / stats.framesSelected).toFixed(2)
    : "0.00";

  const donutData = [
    { name: 'Selected', value: stats.framesSelected },
    { name: 'Discarded', value: stats.framesDiscarded }
  ];

  return (
    <AppShell title="Frame Selection Analytics" subtitle="Cognitive frame selector efficiency metrics">
      <div className="space-y-4">
        
        {/* Top 4 Stat Boxes */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col justify-center items-start">
            <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-2">Frames Received</div>
            <div className="text-3xl font-bold font-mono text-cyan-400">{stats.framesReceived.toLocaleString()}</div>
          </div>
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col justify-center items-start">
            <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-2">Frames Selected</div>
            <div className="text-3xl font-bold font-mono text-emerald-400">{stats.framesSelected.toLocaleString()}</div>
          </div>
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col justify-center items-start">
            <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-2">Frames Discarded</div>
            <div className="text-3xl font-bold font-mono text-slate-400">{stats.framesDiscarded.toLocaleString()}</div>
          </div>
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col justify-center items-start">
            <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-2">TX Reduction</div>
            <div className="text-3xl font-bold font-mono text-amber-500">{txReduction}%</div>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
          
          {/* Pie/Donut Chart Card (4 cols) */}
          <div className="lg:col-span-4 bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col items-center">
            <div className="w-full text-left text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-4 flex items-center gap-2">
              <ImageIcon size={14} className="text-slate-400" /> Frame Distribution
            </div>
            
            <div className="w-full h-[180px]">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={donutData}
                    cx="50%"
                    cy="50%"
                    innerRadius={50}
                    outerRadius={80}
                    paddingAngle={2}
                    dataKey="value"
                    stroke="none"
                  >
                    {donutData.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip 
                    contentStyle={{ backgroundColor: '#0f172a', borderColor: '#1e293b', fontSize: '11px', color: '#cbd5e1' }}
                    itemStyle={{ color: '#e2e8f0' }}
                  />
                  <Legend verticalAlign="bottom" height={36} wrapperStyle={{ fontSize: '11px' }} />
                </PieChart>
              </ResponsiveContainer>
            </div>
            
            <div className="w-full grid grid-cols-2 gap-4 mt-4 pt-4 border-t border-slate-800 text-center">
              <div>
                <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Selection Ratio</div>
                <div className="text-lg font-mono font-bold text-emerald-400">{selectionRatio}%</div>
              </div>
              <div>
                <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Compression</div>
                <div className="text-lg font-mono font-bold text-amber-500">{compressionRatio}x</div>
              </div>
            </div>
          </div>

          {/* Stacked Line Chart (8 cols) */}
          <div className="lg:col-span-8 bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col h-[340px]">
            <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-6">Processing Volume</div>
            <div className="flex-1 min-h-0 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={timelineData} margin={{ top: 5, right: 0, left: -20, bottom: 0 }}>
                  <XAxis dataKey="time" stroke="#475569" fontSize={10} tickMargin={8} />
                  <YAxis stroke="#475569" fontSize={10} tickFormatter={(v) => `${v}`} />
                  <Tooltip 
                    contentStyle={{ backgroundColor: '#0f172a', borderColor: '#1e293b', fontSize: '11px', color: '#cbd5e1' }}
                  />
                  <Legend wrapperStyle={{ fontSize: '11px', paddingTop: '10px' }} iconType="circle" />
                  <Line type="monotone" name="Discarded" dataKey="discarded" stroke="#7dd3fc" strokeWidth={2} dot={false} isAnimationActive={false} />
                  <Line type="monotone" name="Selected" dataKey="selected" stroke="#34d399" strokeWidth={2} dot={false} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
          
        </div>

        {/* Frame Log (Mission History) */}
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col">
          <div className="flex items-center justify-between mb-4 pb-3 border-b border-slate-800">
            <div className="flex items-center gap-2">
              <History size={16} className="text-sky-400" />
              <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-300">Mission Frame Log</h3>
            </div>
            <div className="text-[11px] text-slate-500 font-mono">
              {missionHistory.length} {missionHistory.length === 1 ? 'Mission' : 'Missions'} Logged
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-slate-800/80 text-[10px] uppercase font-semibold text-slate-500 tracking-wider">
                  <th className="pb-3 pl-2">Mission ID</th>
                  <th className="pb-3">Mission Name</th>
                  <th className="pb-3">Status</th>
                  <th className="pb-3 text-right">Frames Received</th>
                  <th className="pb-3 text-right">Frames Selected</th>
                  <th className="pb-3 text-right">Frames Discarded</th>
                  <th className="pb-3 text-right pr-2">TX Reduction</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/50 text-xs font-mono">
                {missionHistory.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="py-8 text-center text-slate-500 text-xs font-sans">
                      No mission frame records available. Start a mission in Mission Control to view live frame telemetry.
                    </td>
                  </tr>
                ) : (
                  missionHistory.map((m, idx) => {
                    const received = m.frames_received || 0;
                    const selected = m.frames_selected || 0;
                    const discarded = m.frames_discarded !== undefined ? m.frames_discarded : Math.max(0, received - selected);
                    const reduction = received > 0 ? ((discarded / received) * 100).toFixed(1) : "0.0";
                    const isActive = m.status === 'active';

                    return (
                      <tr key={m.mission_id || idx} className="hover:bg-slate-800/30 transition-colors">
                        <td className="py-3 pl-2 text-sky-400 font-semibold">{m.mission_id}</td>
                        <td className="py-3 text-slate-200 font-sans">{m.mission_name || 'Unnamed Mission'}</td>
                        <td className="py-3">
                          {isActive ? (
                            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] bg-emerald-500/10 border border-emerald-500/20 text-emerald-400">
                              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                              ACTIVE
                            </span>
                          ) : (
                            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] bg-slate-800 border border-slate-700 text-slate-400">
                              COMPLETED
                            </span>
                          )}
                        </td>
                        <td className="py-3 text-right text-cyan-400">{received.toLocaleString()}</td>
                        <td className="py-3 text-right text-emerald-400 font-bold">{selected.toLocaleString()}</td>
                        <td className="py-3 text-right text-slate-400">{discarded.toLocaleString()}</td>
                        <td className="py-3 text-right pr-2 text-amber-400 font-semibold">{reduction}%</td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>

      </div>
    </AppShell>
  );
}
