import React from 'react';
import AppShell from '@/components/layout/AppShell';
import { AreaChart, Area, BarChart, Bar, ResponsiveContainer, XAxis, YAxis, Tooltip } from "recharts";
import { Cloud, UploadCloud, RefreshCw, WifiOff } from 'lucide-react';

const generateData = (count) => {
  return Array.from({ length: count }).map((_, i) => ({
    time: `-${(count - i) * 2}s`,
    throughput: Math.max(0, 3.2 + (Math.random() - 0.5) * 1.5)
  }));
};

const throughputData = generateData(20);

// Generate history over a longer period for the bar chart
const historyData = Array.from({ length: 40 }).map((_, i) => ({
  time: `-${40 - i}m`,
  volume: Math.max(0, Math.round(150 + (Math.random() - 0.5) * 80))
}));

export default function UploadMonitoring() {
  const currentState = "UPLOADING";

  return (
    <AppShell title="Upload Monitoring" subtitle="Monitor upload status to the ingest server">
      <div className="space-y-4">
        
        {/* State Indicator Cards */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <div className={`border rounded-lg p-4 flex items-center gap-4 transition-colors ${currentState === 'CONNECTED' || currentState === 'UPLOADING' ? 'bg-slate-900 border-emerald-500/30 shadow-[0_0_15px_rgba(16,185,129,0.1)]' : 'bg-slate-950 border-slate-800 opacity-50'}`}>
            <div className={`p-3 rounded-full ${currentState === 'CONNECTED' || currentState === 'UPLOADING' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-800 text-slate-500'}`}>
              <Cloud size={24} />
            </div>
            <div>
              <div className="text-sm font-bold text-slate-200">CONNECTED</div>
              <div className="text-[10px] text-slate-500 uppercase tracking-wider">Ingest Link Active</div>
            </div>
          </div>
          
          <div className={`border rounded-lg p-4 flex items-center gap-4 transition-colors ${currentState === 'UPLOADING' ? 'bg-slate-900 border-cyan-500/30 shadow-[0_0_15px_rgba(6,182,212,0.1)]' : 'bg-slate-950 border-slate-800 opacity-50'}`}>
            <div className={`p-3 rounded-full ${currentState === 'UPLOADING' ? 'bg-cyan-500/20 text-cyan-400 animate-pulse' : 'bg-slate-800 text-slate-500'}`}>
              <UploadCloud size={24} />
            </div>
            <div>
              <div className="text-sm font-bold text-slate-200">UPLOADING</div>
              <div className="text-[10px] text-slate-500 uppercase tracking-wider">TX in Progress</div>
            </div>
          </div>
          
          <div className={`border rounded-lg p-4 flex items-center gap-4 transition-colors ${currentState === 'RETRYING' ? 'bg-slate-900 border-amber-500/30 shadow-[0_0_15px_rgba(245,158,11,0.1)]' : 'bg-slate-950 border-slate-800 opacity-50'}`}>
            <div className={`p-3 rounded-full ${currentState === 'RETRYING' ? 'bg-amber-500/20 text-amber-400' : 'bg-slate-800 text-slate-500'}`}>
              <RefreshCw size={24} />
            </div>
            <div>
              <div className="text-sm font-bold text-slate-200">RETRYING</div>
              <div className="text-[10px] text-slate-500 uppercase tracking-wider">Backoff Active</div>
            </div>
          </div>

          <div className={`border rounded-lg p-4 flex items-center gap-4 transition-colors ${currentState === 'OFFLINE' ? 'bg-slate-900 border-red-500/30 shadow-[0_0_15px_rgba(239,68,68,0.1)]' : 'bg-slate-950 border-slate-800 opacity-50'}`}>
            <div className={`p-3 rounded-full ${currentState === 'OFFLINE' ? 'bg-red-500/20 text-red-400' : 'bg-slate-800 text-slate-500'}`}>
              <WifiOff size={24} />
            </div>
            <div>
              <div className="text-sm font-bold text-slate-200">OFFLINE</div>
              <div className="text-[10px] text-slate-500 uppercase tracking-wider">Connection Lost</div>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
          
          {/* Upload Status Card (3 cols) */}
          <div className="lg:col-span-3 bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col justify-between">
            <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-4">Upload Status</div>
            
            <div className="flex items-center justify-between mb-6 pb-4 border-b border-slate-800">
              <span className="text-xs text-slate-400">Current State</span>
              <span className="px-2.5 py-1 text-[10px] font-mono tracking-wider font-semibold rounded bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">
                UPLOADING
              </span>
            </div>

            <div className="space-y-4 flex-1">
              <div>
                <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Queue Length</div>
                <div className="text-2xl font-bold font-mono text-emerald-400">127 <span className="text-xs text-slate-500 font-sans font-normal uppercase">frames</span></div>
              </div>
              <div>
                <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Frames Uploaded</div>
                <div className="text-2xl font-bold font-mono text-cyan-400">12,450</div>
              </div>
              <div>
                <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">TX Rate</div>
                <div className="text-2xl font-bold font-mono text-sky-400">3.2 <span className="text-xs text-slate-500 font-sans font-normal uppercase">MB/s</span></div>
              </div>
            </div>
          </div>

          {/* Network Quality Card (3 cols) */}
          <div className="lg:col-span-3 bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col justify-between">
            <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-4">Network Quality</div>
            
            <div className="mb-6 flex items-end gap-2">
              <div className="text-6xl font-bold font-mono text-emerald-400 leading-none">88</div>
              <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider pb-1">/ 100 <br/> Quality Score</div>
            </div>

            <div className="w-full bg-slate-800 h-2 rounded-full overflow-hidden mb-6">
              <div className="bg-emerald-500 h-full rounded-full" style={{ width: '88%' }}></div>
            </div>

            <div className="space-y-3 pt-2 flex-1">
              <div className="flex justify-between items-center pb-2 border-b border-slate-800/50">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider">Latency</span>
                <span className="text-xs font-mono text-slate-200">42ms</span>
              </div>
              <div className="flex justify-between items-center pb-2 border-b border-slate-800/50">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider">Jitter</span>
                <span className="text-xs font-mono text-slate-200">3ms</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider">Loss</span>
                <span className="text-xs font-mono text-emerald-400">0.2%</span>
              </div>
            </div>
          </div>

          {/* Upload Throughput Chart (6 cols) */}
          <div className="lg:col-span-6 bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col">
            <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-4">Upload Throughput (MB/s)</div>
            <div className="flex-1 min-h-[180px] w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={throughputData} margin={{ top: 5, right: 0, left: -25, bottom: 0 }}>
                  <defs>
                    <linearGradient id="colorTx" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#38bdf8" stopOpacity={0.4}/>
                      <stop offset="95%" stopColor="#38bdf8" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="time" stroke="#475569" fontSize={10} tickMargin={8} />
                  <YAxis stroke="#475569" fontSize={10} tickFormatter={(v) => v.toFixed(1)} />
                  <Tooltip 
                    contentStyle={{ backgroundColor: '#0f172a', borderColor: '#1e293b', fontSize: '11px', color: '#cbd5e1' }}
                    itemStyle={{ color: '#38bdf8' }}
                  />
                  <Area 
                    type="monotone" 
                    dataKey="throughput" 
                    stroke="#38bdf8" 
                    strokeWidth={2} 
                    fillOpacity={1} 
                    fill="url(#colorTx)" 
                    isAnimationActive={false} 
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>
          
        </div>

        {/* Network Throughput History (Full width) */}
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col h-[220px]">
          <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-4">Network Throughput History (MB)</div>
          <div className="flex-1 min-h-[160px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={historyData} margin={{ top: 5, right: 0, left: -25, bottom: 0 }} barCategoryGap="10%">
                <XAxis dataKey="time" stroke="#475569" fontSize={10} tickMargin={8} />
                <YAxis stroke="#475569" fontSize={10} />
                <Tooltip 
                  cursor={{ fill: '#1e293b' }}
                  contentStyle={{ backgroundColor: '#0f172a', borderColor: '#1e293b', fontSize: '11px', color: '#cbd5e1' }}
                  itemStyle={{ color: '#22d3ee' }}
                />
                <Bar dataKey="volume" fill="#22d3ee" radius={[2, 2, 0, 0]} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

      </div>
    </AppShell>
  );
}
