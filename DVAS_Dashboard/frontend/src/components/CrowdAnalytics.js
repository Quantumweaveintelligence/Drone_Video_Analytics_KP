/**
 * CrowdAnalytics.js
 * ─────────────────────────────────────────────────────────────────────────────
 * Dedicated Crowd Analytics page for the DVAS Command Centre.
 * Kerala Police | Drone Video Analytics System
 */

import { useState, useEffect, useRef } from "react";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, BarChart, Bar, Cell,
} from "recharts";
import {
  Users, Activity, AlertTriangle, CheckCircle, Radio,
  TrendingUp, Thermometer, MapPin, Play, Pause, ShieldAlert, VideoOff
} from "lucide-react";

/* ─────────────────────────────────────────────────────────
   SHARED HELPERS & CONSTANTS
   ───────────────────────────────────────────────────────── */
const DENSITY_LEVELS = [
  { label: "Empty",      color: "#64748b", threshold: 0   },
  { label: "Sparse",     color: "#10b981", threshold: 50  },
  { label: "Moderate",   color: "#3b82f6", threshold: 150 },
  { label: "Dense",      color: "#f59e0b", threshold: 300 },
  { label: "Very Dense", color: "#f97316", threshold: 500 },
  { label: "Extreme",    color: "#ef4444", threshold: 700 },
];

function getDensityLevel(count) {
  let level = DENSITY_LEVELS[0];
  for (const l of DENSITY_LEVELS) {
    if (count >= l.threshold) level = l;
  }
  return level;
}

const CHART_TOOLTIP = {
  contentStyle: {
    background: "var(--dvas-surface)",
    border: "1px solid var(--dvas-border)",
    fontSize: 11,
    color: "var(--dvas-text)",
    borderRadius: 8,
  },
  itemStyle: { color: "var(--dvas-text)" },
};

function fmtAge(isoTs) {
  if (!isoTs) return "—";
  const s = Math.floor((Date.now() - new Date(isoTs)) / 1000);
  if (s < 60)   return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

function parseCSV(csvText) {
  const lines = csvText.split('\n').map(l => l.trim()).filter(Boolean);
  if (lines.length < 2) return [];
  const headers = lines[0].split(',');
  return lines.slice(1).map(line => {
    const values = line.split(',');
    return headers.reduce((obj, header, index) => {
      obj[header] = values[index];
      return obj;
    }, {});
  }).filter(row => row.video_time_sec && row.is_fresh_inference === 'True'); 
}

/* ─────────────────────────────────────────────────────────
   PANEL WRAPPER
   ───────────────────────────────────────────────────────── */
function Panel({ title, icon: Icon, iconClass = "text-sky-400", children, right, badge, noPad }) {
  return (
    <div className="rounded-xl flex flex-col overflow-hidden"
      style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}>
      <div className="px-4 py-3 flex items-center gap-2 shrink-0"
        style={{ borderBottom: "1px solid var(--dvas-border)" }}>
        <Icon size={14} className={iconClass} />
        <span className="dvas-label flex-1 font-semibold">{title}</span>
        {badge && (
          <span className="mono text-[10px] px-1.5 py-0.5 rounded font-bold"
            style={{ background: "rgba(239,68,68,0.12)", color: "#f87171", border: "1px solid rgba(239,68,68,0.2)" }}>
            {badge}
          </span>
        )}
        {right}
      </div>
      <div className={`flex-1 min-h-0 ${noPad ? "" : "p-3"}`}>{children}</div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   SECTION 1 — Live Detection Video Stream
   ───────────────────────────────────────────────────────── */
function LiveStreamSection({ activeDrone, drones, setActiveDrone, currentMetrics, onTimeUpdate }) {
  const [playing, setPlaying] = useState(true);
  const videoRef = useRef(null);

  const isDemo = activeDrone === "DRN-003";

  // Toggle play/pause for video
  const togglePlay = () => {
    if (videoRef.current) {
      if (playing) videoRef.current.pause();
      else videoRef.current.play();
    }
    setPlaying(!playing);
  };

  return (
    <div className="rounded-xl overflow-hidden"
      style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" }}>
      {/* Header */}
      <div className="px-4 py-3 flex items-center gap-3"
        style={{ borderBottom: "1px solid var(--dvas-border)" }}>
        <span className={`w-2 h-2 rounded-full dvas-live-dot ${isDemo ? 'bg-red-500' : 'bg-slate-500'}`} />
        <span className="dvas-label font-semibold">Crowd Detection Live Feed</span>
        <div className="flex items-center gap-1 ml-2">
          {drones?.map(d => (
            <button
              key={d.id}
              onClick={() => setActiveDrone(d.id)}
              className="mono text-[10px] px-2 py-1 rounded transition-all"
              style={{
                background: activeDrone === d.id ? "rgba(14,165,233,0.15)" : "var(--dvas-surface-2)",
                color: activeDrone === d.id ? "#38bdf8" : "var(--dvas-text-muted)",
                border: `1px solid ${activeDrone === d.id ? "rgba(14,165,233,0.35)" : "var(--dvas-border)"}`,
              }}
            >{d.id}</button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2">
          {isDemo && (
            <button
              onClick={togglePlay}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all"
              style={{ background: "var(--dvas-elev)", color: "var(--dvas-text-muted)", border: "1px solid var(--dvas-border)" }}
            >
              {playing ? <Pause size={12} /> : <Play size={12} />}
              {playing ? "Pause" : "Play"}
            </button>
          )}
        </div>
      </div>

      {/* Video area */}
      <div className="relative bg-black flex items-center justify-center" style={{ height: 420 }}>
        {isDemo ? (
          <>
            <video 
              ref={videoRef}
              src="/DETECTED/Demo_annotated.mp4" 
              autoPlay 
              loop 
              muted 
              className="w-full h-full object-cover"
              onTimeUpdate={(e) => onTimeUpdate(e.target.currentTime)}
            />
            {/* Overlay HUD */}
            <div className="absolute top-3 left-3 flex flex-col gap-1.5">
              {(() => {
                const level = getDensityLevel(currentMetrics?.people_count || 0);
                return (
                  <>
                    <div className="mono text-[10px] bg-black/80 px-2 py-1 rounded flex items-center gap-1.5">
                      <span className="w-2 h-2 rounded-full" style={{ background: level.color }} />
                      <span style={{ color: level.color }}>{currentMetrics?.density_label || level.label}</span>
                      <span className="text-slate-200 ml-1 font-bold">{currentMetrics?.people_count ?? "0"} persons</span>
                    </div>
                    <div className="mono text-[10px] bg-black/80 px-2 py-1 rounded text-slate-300">
                      {((currentMetrics?.people_count || 0) / 200).toFixed(2)} p/m²
                    </div>
                  </>
                );
              })()}
            </div>
            <div className="absolute top-3 right-3 mono text-[10px] bg-black/80 px-2 py-1 rounded text-emerald-400 flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 dvas-live-dot" />
              LIVE · DEMO MODE
            </div>
            <div className="absolute bottom-3 left-3 mono text-[10px] bg-black/80 px-2 py-1 rounded text-slate-400">
              Crowd Analytics Model · YOLOv8 · Tile-Based Detection
            </div>
          </>
        ) : (
          <div className="text-center text-slate-500 flex flex-col items-center">
            <VideoOff size={48} className="mb-3 opacity-50 text-slate-600" />
            <p className="mono text-sm">Crowd analytics offline</p>
            <p className="mono text-[10px] mt-1 text-slate-600">Select DRN-003 to view demo feed</p>
          </div>
        )}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   SECTION 2 — Crowd KPI Strip
   ───────────────────────────────────────────────────────── */
function CrowdKPIStrip({ currentMetrics, isDemo }) {
  const totalCount    = isDemo ? Number(currentMetrics?.people_count || 0) : 0;
  const alertCount    = (totalCount > 300) ? 1 : 0; // Simple mock alert based on dense crowd
  const avgDensity    = isDemo ? (totalCount / 200).toFixed(2) : "0.00";
  const breachedZones = (totalCount > 300) ? 1 : 0;
  const peakHour      = "Now"; // Dynamic peak could be computed, static for demo simplicity

  const kpis = [
    { label: "Total Persons Detected", value: totalCount.toLocaleString(), tone: "text-sky-400",    icon: Users,       sub: "Active zone" },
    { label: "Drone Alerts Active",    value: alertCount,                  tone: "text-red-400",    icon: AlertTriangle, sub: alertCount > 0 ? "Density threshold exceeded" : "All zones nominal" },
    { label: "Avg Density (p/m²)",     value: avgDensity,                  tone: "text-amber-400",  icon: Thermometer, sub: "People per square metre" },
    { label: "Zones Breached",         value: breachedZones,               tone: breachedZones > 0 ? "text-red-400" : "text-emerald-400", icon: MapPin, sub: "1 zone monitored" },
    { label: "Peak Crowd Time",        value: peakHour,                    tone: "text-violet-400", icon: TrendingUp,  sub: "Current session" },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
      {kpis.map(({ label, value, tone, icon: Icon, sub }) => (
        <div key={label} className="rounded-xl p-4 flex flex-col gap-1"
          style={{ background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)", opacity: isDemo ? 1 : 0.5 }}>
          <div className="flex items-center gap-2">
            <Icon size={13} className={`${tone} shrink-0`} />
            <span className="dvas-label" style={{ fontSize: 10 }}>{label}</span>
          </div>
          <div className={`mono font-bold text-2xl mt-1 ${tone}`}>{value ?? "—"}</div>
          <div className="mono text-[10px]" style={{ color: "var(--dvas-text-dim)" }}>{sub}</div>
        </div>
      ))}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   SECTION 3 — People Count Over Time
   ───────────────────────────────────────────────────────── */
function PeopleCountChart({ csvData, isDemo, currentMetrics }) {
  // Aggregate data down so it's not too dense for Recharts
  // e.g. group by 2 second intervals
  const chartData = [];
  if (isDemo && csvData?.length > 0) {
    let currentBucket = null;
    for (const row of csvData) {
      const sec = parseFloat(row.video_time_sec);
      const bucketSec = Math.floor(sec / 2) * 2;
      
      if (!currentBucket || currentBucket.time !== bucketSec) {
        if (currentBucket) chartData.push(currentBucket);
        currentBucket = { time: bucketSec, displayTime: `0:${String(bucketSec).padStart(2, '0')}`, crowd: Number(row.people_count) };
      } else {
        currentBucket.crowd = Math.max(currentBucket.crowd, Number(row.people_count));
      }
    }
    if (currentBucket) chartData.push(currentBucket);
  } else {
    // Fill with empty data if offline
    for (let i = 0; i < 20; i++) {
      chartData.push({ displayTime: `0:${String(i*2).padStart(2, '0')}`, crowd: 0 });
    }
  }

  const activeTime = currentMetrics ? Math.floor(parseFloat(currentMetrics.video_time_sec) / 2) * 2 : null;
  const activeDisplayTime = `0:${String(activeTime).padStart(2, '0')}`;

  return (
    <div className="flex flex-col gap-2 relative">
      {!isDemo && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-slate-900/50 backdrop-blur-sm rounded-lg">
          <span className="mono text-xs text-slate-400">Chart offline (waiting for DRN-003)</span>
        </div>
      )}
      <ResponsiveContainer width="100%" height={220}>
        <AreaChart data={chartData} margin={{ top: 10, right: 8, left: -20, bottom: 0 }}>
          <defs>
            <linearGradient id="gCrowd" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"   stopColor="#38bdf8" stopOpacity={0.45} />
              <stop offset="100%" stopColor="#38bdf8" stopOpacity={0}   />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--dvas-border)" strokeDasharray="3 3" />
          <XAxis dataKey="displayTime" stroke="var(--dvas-text-muted)" fontSize={9} minTickGap={20} />
          <YAxis stroke="var(--dvas-text-muted)" fontSize={9} domain={[0, 400]} />
          <Tooltip {...CHART_TOOLTIP} />
          <Area type="monotone" dataKey="crowd" stroke="#38bdf8" fill="url(#gCrowd)" strokeWidth={2} name="Persons" dot={false} isAnimationActive={false} />
          {isDemo && activeDisplayTime && (
            <Area 
              type="monotone" 
              dataKey={(d) => d.displayTime === activeDisplayTime ? d.crowd : null} 
              stroke="none" 
              fill="red" 
              activeDot={{ r: 4, fill: '#ef4444', stroke: '#fff' }} 
            />
          )}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   SECTION 6 — Drone Status Grid
   ───────────────────────────────────────────────────────── */
function DroneStatusGrid({ drones, activeDrone, currentMetrics, isDemo }) {
  const data = drones?.length > 0 ? drones : [{ id: "DRN-001" }, { id: "DRN-002" }, { id: "DRN-003" }, { id: "DRN-004" }];
  
  return (
    <div className="flex flex-col gap-2">
      {data.map(d => {
        const isActiveDemo = (d.id === "DRN-003" && isDemo);
        const count = isActiveDemo ? Number(currentMetrics?.people_count || 0) : 0;
        const level = getDensityLevel(count);
        const pct   = Math.min(100, Math.round((count / 700) * 100));
        
        return (
          <div key={d.id} className="rounded-lg p-3 flex flex-col gap-2"
            style={{ 
              background: "var(--dvas-surface-2)", 
              border: `1px solid ${isActiveDemo ? 'var(--dvas-border-active, rgba(56,189,248,0.5))' : 'var(--dvas-border)'}`,
              opacity: isActiveDemo || (d.id === activeDrone) ? 1 : 0.5 
            }}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="mono text-xs font-bold" style={{ color: "var(--dvas-text)" }}>{d.id}</span>
                <span className="mono text-[10px] px-1.5 py-0.5 rounded font-semibold"
                  style={{ background: `${level.color}22`, color: level.color, border: `1px solid ${level.color}44` }}>
                  {isActiveDemo ? currentMetrics?.density_label || level.label : "Offline"}
                </span>
                {isActiveDemo && count > 300 && (
                  <span className="mono text-[10px] text-red-400 flex items-center gap-1">
                    <AlertTriangle size={10} className="dvas-live-dot" /> ALERT
                  </span>
                )}
              </div>
              <div className="mono text-right">
                <div className="text-sm font-bold" style={{ color: level.color }}>{count.toLocaleString()}</div>
                <div className="text-[10px]" style={{ color: "var(--dvas-text-dim)" }}>persons</div>
              </div>
            </div>
            <div className="h-2 rounded-full overflow-hidden" style={{ background: "var(--dvas-elev)" }}>
              <div className="h-full rounded-full transition-all duration-300"
                style={{ width: `${pct}%`, background: level.color }} />
            </div>
            <div className="flex items-center justify-between">
              <span className="mono text-[10px]" style={{ color: "var(--dvas-text-dim)" }}>
                {isActiveDemo ? (count / 200).toFixed(2) : "0.00"} p/m²
              </span>
              <span className="mono text-[10px]" style={{ color: "var(--dvas-text-dim)" }}>
                {isActiveDemo ? "Live Stream" : "Disconnected"}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────
   MAIN EXPORT
   ───────────────────────────────────────────────────────── */
export default function CrowdAnalytics({ drones }) {
  const [activeDrone, setActiveDrone] = useState("DRN-003");
  const [csvData, setCsvData] = useState([]);
  const [currentMetrics, setCurrentMetrics] = useState(null);

  // Default to DRN-003 or the first available drone
  useEffect(() => {
    if (drones && drones.length > 0) {
      if (drones.some(d => d.id === "DRN-003")) {
        setActiveDrone("DRN-003");
      } else {
        setActiveDrone(drones[0].id);
      }
    } else {
      setActiveDrone("DRN-003");
    }
  }, [drones]);

  // Fetch CSV Data
  useEffect(() => {
    const loadCSV = async () => {
      try {
        const response = await fetch('/DETECTED/Edited_Demo_metrics.csv');
        if (!response.ok) throw new Error("Failed to fetch CSV");
        const text = await response.text();
        const parsed = parseCSV(text);
        setCsvData(parsed);
        if (parsed.length > 0) {
          setCurrentMetrics(parsed[0]);
        }
      } catch (err) {
        console.error("Error loading CSV:", err);
      }
    };
    loadCSV();
  }, []);

  // Update metrics based on video time
  const handleTimeUpdate = (currentTimeSec) => {
    if (!csvData || csvData.length === 0) return;
    
    // Find the closest timestamp in the CSV
    let closestRow = csvData[0];
    let minDiff = Infinity;
    
    // Binary search could be used here, but linear is fine for 2k rows
    for (let i = 0; i < csvData.length; i++) {
      const row = csvData[i];
      const diff = Math.abs(parseFloat(row.video_time_sec) - currentTimeSec);
      if (diff < minDiff) {
        minDiff = diff;
        closestRow = row;
      }
      // Since CSV is sorted by time, we can break early if we passed it
      if (parseFloat(row.video_time_sec) > currentTimeSec + 1) break;
    }
    
    if (closestRow !== currentMetrics) {
      setCurrentMetrics(closestRow);
    }
  };

  const isDemo = activeDrone === "DRN-003";

  return (
    <div className="flex flex-col gap-5" style={{ fontFamily: "'Plus Jakarta Sans', sans-serif" }}>

      {/* LIVE STREAM */}
      <LiveStreamSection 
        activeDrone={activeDrone} 
        drones={drones?.length > 0 ? drones : [{id: "DRN-001"}, {id: "DRN-002"}, {id: "DRN-003"}, {id: "DRN-004"}]}
        setActiveDrone={setActiveDrone}
        currentMetrics={currentMetrics}
        onTimeUpdate={handleTimeUpdate}
      />

      {/* KPI STRIP */}
      <CrowdKPIStrip currentMetrics={currentMetrics} isDemo={isDemo} />

      {/* PEOPLE COUNT CHART + DRONE STATUS */}
      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-12 lg:col-span-8">
          <Panel title="People Count Over Time" icon={Activity} iconClass="text-sky-400"
            right={
              <span className={`mono text-[10px] flex items-center gap-1 ${isDemo ? 'text-sky-400' : 'text-slate-500'}`}>
                <span className={`w-1.5 h-1.5 rounded-full ${isDemo ? 'bg-sky-400 dvas-live-dot' : 'bg-slate-500'}`} />
                {isDemo ? "Live Sync" : "Offline"}
              </span>
            }>
            <PeopleCountChart csvData={csvData} isDemo={isDemo} currentMetrics={currentMetrics} />
          </Panel>
        </div>
        <div className="col-span-12 lg:col-span-4">
          <Panel title="Drone Crowd Status" icon={Radio} iconClass="text-violet-400">
            <DroneStatusGrid 
              drones={drones?.length > 0 ? drones : [{id: "DRN-001"}, {id: "DRN-002"}, {id: "DRN-003"}, {id: "DRN-004"}]} 
              activeDrone={activeDrone}
              currentMetrics={currentMetrics}
              isDemo={isDemo}
            />
          </Panel>
        </div>
      </div>
    </div>
  );
}
