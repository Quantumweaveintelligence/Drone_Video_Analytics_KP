import React, { useState, useEffect } from 'react';
import AppShell from '@/components/layout/AppShell';
import { 
  AreaChart, Area, LineChart, Line, PieChart, Pie, Cell, 
  ResponsiveContainer, XAxis, YAxis, Tooltip, Legend 
} from "recharts";
import { Image as ImageIcon } from 'lucide-react';
import { api } from '@/lib/api';

const COLORS = ['#1e3a8a', '#94a3b8']; // navy blue, light blue-grey

export default function FrameSelection() {
  const [timelineData, setTimelineData] = useState(Array.from({ length: 20 }).map((_, i) => ({
    time: `-${(20 - i) * 3}s`,
    selected: 0,
    discarded: 0,
    total: 0
  })));
  
  const [stats, setStats] = useState({
    framesReceived: 0, // This is frames_total from the edge
    framesSelected: 0, // This is frames_received by the ingest server
    framesDiscarded: 0,
  });

  const [activeDrone, setActiveDrone] = useState(null);

  // Fetch the first available drone to monitor
  useEffect(() => {
    const fetchDrones = async () => {
      try {
        const data = await api.get('/ingest-server/status');
        if (data && data.drones && data.drones.length > 0) {
          setActiveDrone(data.drones[0].drone_id);
        }
      } catch (err) {
        console.error("Failed to fetch drones:", err);
      }
    };
    fetchDrones();
  }, []);

  // Poll for live stats
  useEffect(() => {
    if (!activeDrone) return;

    let previousStats = {
      framesReceived: 0,
      framesSelected: 0
    };

    const fetchStats = async () => {
      try {
        const data = await api.get(`/ingest-server/status/${activeDrone}`);
        if (data && data.ingest_stats) {
          const s = data.ingest_stats;
          
          const totalEdgeFrames = s.frames_total || 0; // The new field added from edge metadata
          const ingestFrames = s.frames_received || 0;
          const discarded = Math.max(0, totalEdgeFrames - ingestFrames);

          setStats({
            framesReceived: totalEdgeFrames,
            framesSelected: ingestFrames,
            framesDiscarded: discarded
          });

          // Calculate delta for the timeline
          const deltaTotal = totalEdgeFrames - previousStats.framesReceived;
          const deltaSelected = ingestFrames - previousStats.framesSelected;
          const deltaDiscarded = Math.max(0, deltaTotal - deltaSelected);
          
          if (previousStats.framesReceived > 0) {
            setTimelineData(prev => {
              const newData = [...prev.slice(1), {
                time: new Date().toLocaleTimeString([], { hour12: false, second: '2-digit', minute: '2-digit' }),
                selected: deltaSelected,
                discarded: deltaDiscarded,
                total: deltaTotal
              }];
              return newData;
            });
          }

          previousStats = {
            framesReceived: totalEdgeFrames,
            framesSelected: ingestFrames
          };
        }
      } catch (err) {
        console.error("Failed to fetch drone stats:", err);
      }
    };

    fetchStats();
    const interval = setInterval(fetchStats, 3000);
    return () => clearInterval(interval);
  }, [activeDrone]);

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

  const sumSelected = timelineData.reduce((acc, curr) => acc + curr.selected, 0);
  const avgSelectionPerMin = Math.round((sumSelected / timelineData.length) * (60 / 3));

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

        {/* Bottom Full Width Stats & Chart Card */}
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-6 pb-6 border-b border-slate-800">
            <div>
              <div className="text-[10px] text-slate-500 uppercase font-semibold tracking-wider mb-1">Bandwidth Saved</div>
              <div className="text-2xl font-bold font-mono text-amber-500">{txReduction}%</div>
            </div>
            <div>
              <div className="text-[10px] text-slate-500 uppercase font-semibold tracking-wider mb-1">Avg Selection / Min</div>
              <div className="text-2xl font-bold font-mono text-emerald-400">{avgSelectionPerMin} <span className="text-sm text-slate-500 font-sans">frames</span></div>
            </div>
            <div>
              <div className="text-[10px] text-slate-500 uppercase font-semibold tracking-wider mb-1">Compression Ratio</div>
              <div className="text-2xl font-bold font-mono text-cyan-400">{compressionRatio}x</div>
            </div>
          </div>
          
          <div className="text-[10px] text-slate-500 uppercase font-semibold tracking-wider mb-4">Selected Frames (Per Interval)</div>
          <div className="w-full h-[120px]">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={timelineData} margin={{ top: 5, right: 0, left: -25, bottom: 0 }}>
                <defs>
                  <linearGradient id="colorSel" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#34d399" stopOpacity={0.4}/>
                    <stop offset="95%" stopColor="#34d399" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <XAxis dataKey="time" hide />
                <YAxis stroke="#475569" fontSize={10} />
                <Tooltip 
                  contentStyle={{ backgroundColor: '#0f172a', borderColor: '#1e293b', fontSize: '11px', color: '#cbd5e1' }}
                  itemStyle={{ color: '#34d399' }}
                />
                <Area 
                  type="monotone" 
                  dataKey="selected" 
                  stroke="#34d399" 
                  strokeWidth={2} 
                  fillOpacity={1} 
                  fill="url(#colorSel)" 
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
