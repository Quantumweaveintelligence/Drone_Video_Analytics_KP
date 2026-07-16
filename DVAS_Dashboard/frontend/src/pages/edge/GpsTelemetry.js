import React, { useState, useEffect } from 'react';
import AppShell from '@/components/layout/AppShell';
import { MapContainer, TileLayer, Marker, Popup, Polyline, CircleMarker } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { AreaChart, Area, ResponsiveContainer, XAxis, YAxis, Tooltip } from "recharts";
import { Compass, MapPin } from 'lucide-react';

// Fix Leaflet's default icon path issues in React
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: require('leaflet/dist/images/marker-icon-2x.png'),
  iconUrl: require('leaflet/dist/images/marker-icon.png'),
  shadowUrl: require('leaflet/dist/images/marker-shadow.png')
});

const KERALA_LAT = 8.5241;
const KERALA_LNG = 76.9366;

export default function GpsTelemetry() {
  const [telemetry, setTelemetry] = useState({
    lat: KERALA_LAT,
    lng: KERALA_LNG,
    alt: 120,
    heading: 45,
    speed: 12.5,
    battery: 78,
    signal: 92,
    lastUpdate: new Date().toLocaleTimeString()
  });

  const [path, setPath] = useState([{ lat: KERALA_LAT, lng: KERALA_LNG }]);
  const [history, setHistory] = useState(
    Array.from({ length: 30 }).map((_, i) => ({
      time: i,
      alt: 120 + (Math.random() - 0.5) * 10,
      speed: 12.5 + (Math.random() - 0.5) * 2,
      battery: 100 - (30 - i) * 0.1
    }))
  );

  useEffect(() => {
    const timer = setInterval(() => {
      setTelemetry(prev => {
        const newLat = prev.lat + 0.0001 + (Math.random() - 0.5) * 0.00005;
        const newLng = prev.lng + 0.0001 + (Math.random() - 0.5) * 0.00005;
        const newAlt = prev.alt + (Math.random() - 0.5) * 2;
        const newSpeed = prev.speed + (Math.random() - 0.5) * 1;
        const newBattery = Math.max(0, prev.battery - 0.01);
        
        setPath(p => {
          const newPath = [...p, { lat: newLat, lng: newLng }];
          if (newPath.length > 30) newPath.shift();
          return newPath;
        });

        setHistory(h => {
          const newHist = [...h, {
            time: h.length,
            alt: newAlt,
            speed: newSpeed,
            battery: newBattery
          }];
          if (newHist.length > 30) newHist.shift();
          return newHist;
        });

        return {
          ...prev,
          lat: newLat,
          lng: newLng,
          alt: newAlt,
          speed: newSpeed,
          battery: newBattery,
          heading: (prev.heading + (Math.random() - 0.5) * 10) % 360,
          lastUpdate: new Date().toLocaleTimeString()
        };
      });
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  return (
    <AppShell title="GPS & Telemetry" subtitle="Real-time position tracking and sensor data visualisation">
      <div className="space-y-4">
        
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
          
          {/* Left Panel: Live Data (3 columns) */}
          <div className="lg:col-span-3 bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col">
            <div className="flex items-center gap-2 text-sm text-slate-400 font-semibold uppercase tracking-wider mb-6">
              <Compass size={16} /> Live Data
            </div>
            
            <div className="space-y-4 flex-1">
              <div className="flex justify-between items-center pb-2 border-b border-slate-800">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider">Latitude</span>
                <span className="text-sm font-mono text-slate-200">{telemetry.lat.toFixed(6)}°</span>
              </div>
              <div className="flex justify-between items-center pb-2 border-b border-slate-800">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider">Longitude</span>
                <span className="text-sm font-mono text-slate-200">{telemetry.lng.toFixed(6)}°</span>
              </div>
              <div className="flex justify-between items-center pb-2 border-b border-slate-800">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider">Altitude</span>
                <span className="text-sm font-mono text-cyan-400">{telemetry.alt.toFixed(1)} m</span>
              </div>
              <div className="flex justify-between items-center pb-2 border-b border-slate-800">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider">Heading</span>
                <span className="text-sm font-mono text-amber-400">{telemetry.heading.toFixed(1)}°</span>
              </div>
              <div className="flex justify-between items-center pb-2 border-b border-slate-800">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider">Ground Speed</span>
                <span className="text-sm font-mono text-emerald-400">{telemetry.speed.toFixed(1)} m/s</span>
              </div>
              <div className="flex justify-between items-center pb-2 border-b border-slate-800">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider">Battery</span>
                <span className={`text-sm font-mono ${telemetry.battery > 40 ? 'text-emerald-400' : telemetry.battery > 20 ? 'text-amber-400' : 'text-red-400'}`}>
                  {telemetry.battery.toFixed(1)}%
                </span>
              </div>
              <div className="flex justify-between items-center pb-2 border-b border-slate-800">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider">Signal</span>
                <span className="text-sm font-mono text-emerald-400">{telemetry.signal}%</span>
              </div>
            </div>
            
            <div className="mt-6 pt-4 border-t border-slate-800 text-center">
              <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Last Update</div>
              <div className="text-xs text-sky-400 font-mono bg-sky-900/20 py-1.5 rounded inline-block px-3 border border-sky-500/20">
                {telemetry.lastUpdate}
              </div>
            </div>
          </div>

          {/* Map (9 columns) */}
          <div className="lg:col-span-9 bg-slate-900 border border-slate-800 rounded-lg p-2 h-[420px] relative z-0">
            {/* The tile layer below is CartoDB Positron (Light), providing a clean, subtle background */}
            <MapContainer 
              center={[KERALA_LAT, KERALA_LNG]} 
              zoom={15} 
              scrollWheelZoom={true} 
              className="w-full h-full rounded-md"
              zoomControl={false}
            >
              <TileLayer
                attribution='&copy; <a href="https://carto.com/">CartoDB</a>'
                url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
              />
              
              {/* Flight path trail */}
              <Polyline 
                positions={path.map(p => [p.lat, p.lng])}
                pathOptions={{ 
                  color: '#0f172a', // Navy blue
                  weight: 3, 
                  opacity: 0.55, 
                  dashArray: '6, 5' 
                }}
              />
              
              {/* Position indicator halo */}
              <CircleMarker 
                center={[telemetry.lat, telemetry.lng]} 
                radius={18}
                pathOptions={{ color: '#0ea5e9', fillColor: '#0ea5e9', fillOpacity: 0.15, weight: 1 }}
              />
              
              {/* Drone marker */}
              <Marker position={[telemetry.lat, telemetry.lng]}>
                <Popup className="dvas-map-popup">
                  <div className="font-sans">
                    <div className="font-bold text-slate-800">DRN-001 (Garuda-1)</div>
                    <div className="text-xs mt-1 text-slate-600">
                      Alt: <span className="font-mono text-cyan-600">{telemetry.alt.toFixed(1)}m</span><br/>
                      Spd: <span className="font-mono text-amber-600">{telemetry.speed.toFixed(1)}m/s</span>
                    </div>
                  </div>
                </Popup>
              </Marker>
            </MapContainer>

            {/* Floating map controls/labels over the map */}
            <div className="absolute top-4 left-4 z-[400] flex gap-2 pointer-events-none">
              <div className="px-3 py-1.5 bg-white/90 backdrop-blur shadow-sm border border-slate-200 text-slate-800 text-xs font-semibold rounded flex items-center gap-1.5">
                <MapPin size={14} className="text-sky-500" /> Thiruvananthapuram Sector
              </div>
            </div>
          </div>
        </div>

        {/* Bottom Section: 3 Trend Charts */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          
          {/* Altitude Chart */}
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 h-[140px] flex flex-col">
            <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-2 font-semibold">Altitude (m)</div>
            <div className="flex-1 min-h-0 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={history} margin={{ top: 5, right: 0, left: -25, bottom: 0 }}>
                  <defs>
                    <linearGradient id="colorAltTelem" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#22d3ee" stopOpacity={0.3}/>
                      <stop offset="95%" stopColor="#22d3ee" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="time" hide />
                  <YAxis stroke="#475569" fontSize={9} tickFormatter={(v) => `${v}`} domain={['dataMin - 10', 'dataMax + 10']} />
                  <Tooltip 
                    contentStyle={{ backgroundColor: '#0f172a', borderColor: '#1e293b', fontSize: '10px', color: '#cbd5e1' }}
                    itemStyle={{ color: '#22d3ee' }}
                  />
                  <Area type="monotone" dataKey="alt" stroke="#22d3ee" strokeWidth={1.5} fillOpacity={1} fill="url(#colorAltTelem)" isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Speed Chart */}
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 h-[140px] flex flex-col">
            <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-2 font-semibold">Speed (m/s)</div>
            <div className="flex-1 min-h-0 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={history} margin={{ top: 5, right: 0, left: -25, bottom: 0 }}>
                  <defs>
                    <linearGradient id="colorSpdTelem" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#fbbf24" stopOpacity={0.3}/>
                      <stop offset="95%" stopColor="#fbbf24" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="time" hide />
                  <YAxis stroke="#475569" fontSize={9} tickFormatter={(v) => `${v}`} domain={['dataMin - 2', 'dataMax + 2']} />
                  <Tooltip 
                    contentStyle={{ backgroundColor: '#0f172a', borderColor: '#1e293b', fontSize: '10px', color: '#cbd5e1' }}
                    itemStyle={{ color: '#fbbf24' }}
                  />
                  <Area type="monotone" dataKey="speed" stroke="#fbbf24" strokeWidth={1.5} fillOpacity={1} fill="url(#colorSpdTelem)" isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Battery Chart */}
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 h-[140px] flex flex-col">
            <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-2 font-semibold">Battery (%)</div>
            <div className="flex-1 min-h-0 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={history} margin={{ top: 5, right: 0, left: -25, bottom: 0 }}>
                  <defs>
                    <linearGradient id="colorBatTelem" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#34d399" stopOpacity={0.3}/>
                      <stop offset="95%" stopColor="#34d399" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="time" hide />
                  <YAxis stroke="#475569" fontSize={9} tickFormatter={(v) => `${v}`} domain={[0, 100]} />
                  <Tooltip 
                    contentStyle={{ backgroundColor: '#0f172a', borderColor: '#1e293b', fontSize: '10px', color: '#cbd5e1' }}
                    itemStyle={{ color: '#34d399' }}
                  />
                  <Area type="monotone" dataKey="battery" stroke="#34d399" strokeWidth={1.5} fillOpacity={1} fill="url(#colorBatTelem)" isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

        </div>

      </div>
    </AppShell>
  );
}
