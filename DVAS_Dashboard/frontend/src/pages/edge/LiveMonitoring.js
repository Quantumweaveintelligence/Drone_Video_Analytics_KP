import React from 'react';
import AppShell from '@/components/layout/AppShell';
import { Camera, Send as Drone, Activity, RefreshCw } from 'lucide-react';
import { AreaChart, Area, ResponsiveContainer, XAxis, YAxis, Tooltip } from "recharts";

// Mock data generation for charts
const generateChartData = (base, variance, count) => {
  return Array.from({ length: count }).map((_, i) => ({
    time: i,
    value: Math.max(0, Math.round(base + (Math.random() - 0.5) * variance))
  }));
};

const fpsData = generateChartData(30, 2, 30);
const latencyData = generateChartData(42, 5, 30);

export default function LiveMonitoring() {
  return (
    <AppShell title="Live Monitoring" subtitle="Real-time drone video feed with stream telemetry">
      <div className="space-y-4">
        
        {/* Drone Status Strip */}
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="bg-slate-800 p-2 rounded text-slate-300">
              <Camera size={18} />
            </div>
            <div>
              <div className="text-sm font-bold text-slate-100 flex items-center gap-2">
                Garuda-1 <span className="text-xs font-mono text-slate-500 font-normal">DRN-001</span>
              </div>
              <div className="text-[10px] text-slate-400 uppercase tracking-wider">Dedicated Asset</div>
            </div>
          </div>
          <div className="text-xs px-2.5 py-1 rounded-full bg-sky-500/10 border border-sky-500/20 text-sky-400 font-mono tracking-wider flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-sky-400 animate-pulse"></span>
            CONNECTED
          </div>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-12 gap-4">
          
          {/* Main Video Area (8 columns) */}
          <div className="xl:col-span-8 flex flex-col gap-4">
            
            {/* Header bar above video */}
            <div className="flex flex-col sm:flex-row gap-3 sm:items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="text-[10px] px-2 py-1 rounded-sm bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 font-mono tracking-wider flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                  HEALTHY
                </div>
                <div className="text-sm text-slate-200 font-medium">Garuda-1</div>
                <div className="text-xs text-slate-500 font-mono">MSN-9942-ALPHA</div>
              </div>
              
              <div className="flex gap-2 text-xs font-mono">
                <div className="px-2 py-1 bg-slate-900 border border-slate-800 rounded">
                  <span className="text-slate-500 mr-1">FPS</span><span className="text-cyan-400">30</span>
                </div>
                <div className="px-2 py-1 bg-slate-900 border border-slate-800 rounded">
                  <span className="text-slate-500 mr-1">KBPS</span><span className="text-sky-400">4250</span>
                </div>
                <div className="px-2 py-1 bg-slate-900 border border-slate-800 rounded">
                  <span className="text-slate-500 mr-1">MS</span><span className="text-emerald-400">42</span>
                </div>
                <div className="px-2 py-1 bg-slate-900 border border-slate-800 rounded">
                  <span className="text-slate-500 mr-1">PKT LOSS</span><span className="text-emerald-400">0%</span>
                </div>
              </div>
            </div>

            {/* Video Player Box */}
            <div className="relative w-full aspect-video bg-black rounded-lg overflow-hidden border border-slate-800 shadow-2xl">
              <video 
                autoPlay 
                muted 
                loop 
                playsInline
                className="w-full h-full object-cover"
                poster="https://images.unsplash.com/photo-1524661135-423995f22d0b?w=1280&q=80"
              >
                {/* Fallback mock stream, usually handled by HLS/WebRTC player */}
                <source src="https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8" />
              </video>
              
              {/* Top Left HUD */}
              <div className="absolute top-4 left-4 flex gap-2">
                <div className="px-2 py-1 bg-sky-900/80 text-sky-200 border border-sky-500/30 text-xs font-mono rounded backdrop-blur">
                  DRN-001
                </div>
                <div className="px-2 py-1 bg-black/60 text-slate-300 border border-slate-700/50 text-xs font-mono rounded backdrop-blur">
                  1920×1080
                </div>
              </div>
              
              {/* Bottom Right HUD */}
              <div className="absolute bottom-4 right-4">
                <div className="px-2 py-1 bg-black/60 text-emerald-400 border border-slate-700/50 text-[10px] font-mono tracking-wider rounded backdrop-blur flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                  HEALTHY
                </div>
              </div>

              {/* Crosshair decoration (optional) */}
              <div className="absolute inset-0 pointer-events-none flex items-center justify-center opacity-30">
                <div className="w-12 h-12 border border-slate-400 rounded-full flex items-center justify-center">
                  <div className="w-1 h-1 bg-slate-400 rounded-full"></div>
                </div>
              </div>
            </div>
          </div>

          {/* Right Metrics Panel (4 columns) */}
          <div className="xl:col-span-4 flex flex-col gap-4">
            
            {/* Stream Metrics Card */}
            <div className="bg-slate-900 border border-slate-800 p-5 rounded-lg flex flex-col">
              <div className="text-xs text-slate-500 font-semibold uppercase tracking-wider mb-5">Stream Metrics</div>
              
              <div className="space-y-5">
                <div>
                  <div className="flex justify-between text-xs mb-1.5">
                    <span className="text-slate-400">Frame Rate</span>
                    <span className="text-emerald-400 font-mono">30 fps</span>
                  </div>
                  <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
                    <div className="bg-emerald-500 h-full rounded-full" style={{ width: '100%' }}></div>
                  </div>
                </div>
                
                <div>
                  <div className="flex justify-between text-xs mb-1.5">
                    <span className="text-slate-400">Bitrate</span>
                    <span className="text-sky-400 font-mono">4.2 Mbps</span>
                  </div>
                  <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
                    <div className="bg-sky-500 h-full rounded-full" style={{ width: '84%' }}></div>
                  </div>
                </div>

                <div className="pt-2 border-t border-slate-800/50 grid grid-cols-2 gap-4">
                  <div>
                    <div className="text-[10px] text-slate-500 uppercase tracking-wider">Latency</div>
                    <div className="text-lg font-mono text-emerald-400 mt-0.5">42 <span className="text-xs text-slate-500">ms</span></div>
                  </div>
                  <div>
                    <div className="text-[10px] text-slate-500 uppercase tracking-wider">Packet Loss</div>
                    <div className="text-lg font-mono text-emerald-400 mt-0.5">0.0 <span className="text-xs text-slate-500">%</span></div>
                  </div>
                </div>
              </div>
            </div>

            {/* FPS Trend Chart */}
            <div className="bg-slate-900 border border-slate-800 p-4 rounded-lg flex flex-col">
              <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-2">FPS Trend</div>
              <div className="w-full h-[100px]">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={fpsData} margin={{ top: 5, right: 0, left: -25, bottom: 0 }}>
                    <defs>
                      <linearGradient id="colorFps" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#22d3ee" stopOpacity={0.3}/>
                        <stop offset="95%" stopColor="#22d3ee" stopOpacity={0}/>
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="time" hide />
                    <YAxis stroke="#475569" fontSize={9} tickFormatter={(v) => `${v}`} domain={['dataMin - 5', 'dataMax + 5']} />
                    <Tooltip 
                      contentStyle={{ backgroundColor: '#0f172a', borderColor: '#1e293b', fontSize: '10px', color: '#cbd5e1' }}
                      itemStyle={{ color: '#22d3ee' }}
                    />
                    <Area type="monotone" dataKey="value" stroke="#22d3ee" strokeWidth={1.5} fillOpacity={1} fill="url(#colorFps)" isAnimationActive={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Latency Trend Chart */}
            <div className="bg-slate-900 border border-slate-800 p-4 rounded-lg flex flex-col">
              <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-2">Latency (ms)</div>
              <div className="w-full h-[100px]">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={latencyData} margin={{ top: 5, right: 0, left: -25, bottom: 0 }}>
                    <defs>
                      <linearGradient id="colorLat" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#fbbf24" stopOpacity={0.3}/>
                        <stop offset="95%" stopColor="#fbbf24" stopOpacity={0}/>
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="time" hide />
                    <YAxis stroke="#475569" fontSize={9} tickFormatter={(v) => `${v}`} domain={['dataMin - 10', 'dataMax + 10']} />
                    <Tooltip 
                      contentStyle={{ backgroundColor: '#0f172a', borderColor: '#1e293b', fontSize: '10px', color: '#cbd5e1' }}
                      itemStyle={{ color: '#fbbf24' }}
                    />
                    <Area type="monotone" dataKey="value" stroke="#fbbf24" strokeWidth={1.5} fillOpacity={1} fill="url(#colorLat)" isAnimationActive={false} />
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
