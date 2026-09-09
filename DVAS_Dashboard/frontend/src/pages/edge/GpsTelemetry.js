import React, { useState, useEffect } from 'react';
import AppShell from '@/components/layout/AppShell';
import { MapContainer, TileLayer, Marker, Popup, Polyline, CircleMarker, Polygon, useMap, useMapEvents } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { AreaChart, Area, ResponsiveContainer, XAxis, YAxis, Tooltip } from "recharts";
import { Compass, MapPin, Radio, Play, Pause, ShieldAlert, Undo2, Trash2, Check, Pencil, Lock } from 'lucide-react';
import { authFetch } from '@/lib/api';

// Fix Leaflet's default icon path issues in React
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: require('leaflet/dist/images/marker-icon-2x.png'),
  iconUrl: require('leaflet/dist/images/marker-icon.png'),
  shadowUrl: require('leaflet/dist/images/marker-shadow.png')
});

const KERALA_LAT = 8.5241;
const KERALA_LNG = 76.9366;

// Auto-recenter map when drone position changes
function RecenterMap({ lat, lng }) {
  const map = useMap();
  useEffect(() => {
    if (lat && lng && (lat !== KERALA_LAT || lng !== KERALA_LNG)) {
      map.setView([lat, lng], map.getZoom(), { animate: true });
    }
  }, [lat, lng, map]);
  return null;
}

// Interactive polygon drawing on map click
function PolygonDrawer({ isDrawing, onAddPoint }) {
  useMapEvents({
    click(e) {
      if (isDrawing) {
        onAddPoint(e.latlng);
      }
    }
  });
  return null;
}

export default function GpsTelemetry() {
  const [mission, setMission] = useState(null);
  const [serverSend, setServerSend] = useState(false);
  const [isDrawing, setIsDrawing] = useState(false);
  const [drawnPoints, setDrawnPoints] = useState([]);
  const [savedGeofence, setSavedGeofence] = useState(null);
  const [isSavingGeofence, setIsSavingGeofence] = useState(false);
  const [geofenceFeedback, setGeofenceFeedback] = useState(null);

  const [telemetry, setTelemetry] = useState({
    lat: KERALA_LAT,
    lng: KERALA_LNG,
    alt: 0,
    heading: 0,
    speed: 0,
    battery: 0,
    signal: 0,
    gpsLock: false,
    lastUpdate: '--:--:--'
  });

  const [path, setPath] = useState([{ lat: KERALA_LAT, lng: KERALA_LNG }]);
  const [history, setHistory] = useState(
    Array.from({ length: 30 }).map((_, i) => ({
      time: i,
      alt: 0,
      speed: 0,
      battery: 0
    }))
  );

  // Sync active mission, telemetry, and server send state
  useEffect(() => {
    const timer = setInterval(async () => {
      try {
        const [telResp, missionResp] = await Promise.all([
          authFetch('/edge-api/api/edge/telemetry'),
          authFetch('/edge-api/api/edge/mission/active')
        ]);
        const tel = await telResp.json();
        const missionData = await missionResp.json();

        if (missionData.active) {
          setMission(missionData);
          setServerSend(Boolean(missionData.server_send_enabled));
        } else {
          setMission(null);
          setServerSend(false);
          setSavedGeofence(null);
          setIsDrawing(false);
          setDrawnPoints([]);
        }

        const altVal = (tel && tel.altitude_m !== undefined && tel.altitude_m !== null && !isNaN(tel.altitude_m)) ? Number(tel.altitude_m) : 0;
        const spdVal = (tel && tel.speed_mps !== undefined && tel.speed_mps !== null && !isNaN(tel.speed_mps)) ? Number(tel.speed_mps) : 0;
        const batVal = (tel && tel.battery_pct !== undefined && tel.battery_pct !== null && !isNaN(tel.battery_pct)) ? Number(tel.battery_pct) : 0;
        const headVal = (tel && tel.heading_deg !== undefined && tel.heading_deg !== null && !isNaN(tel.heading_deg)) ? Number(tel.heading_deg) : 0;
        const sigVal = (tel && tel.signal_pct !== undefined && tel.signal_pct !== null && !isNaN(tel.signal_pct)) ? Number(tel.signal_pct) : 0;
        const hasGps = Boolean(tel && tel.latitude && tel.longitude && tel.gps_lock);

        setTelemetry({
          lat: tel?.latitude ? Number(tel.latitude) : KERALA_LAT,
          lng: tel?.longitude ? Number(tel.longitude) : KERALA_LNG,
          alt: altVal,
          heading: headVal,
          speed: spdVal,
          battery: batVal,
          signal: sigVal,
          gpsLock: hasGps,
          lastUpdate: new Date().toLocaleTimeString()
        });

        if (tel?.latitude && tel?.longitude) {
          const newLat = Number(tel.latitude);
          const newLng = Number(tel.longitude);
          setPath(prev => {
            const last = prev[prev.length - 1];
            if (last && Math.abs(last.lat - newLat) < 0.000001 && Math.abs(last.lng - newLng) < 0.000001) {
              return prev;
            }
            const newPath = [...prev, { lat: newLat, lng: newLng }];
            if (newPath.length > 50) newPath.shift();
            return newPath;
          });
        }

        setHistory(h => {
          const newHist = [...h, {
            time: h.length,
            alt: Math.round(altVal * 10) / 10,
            speed: Math.round(spdVal * 10) / 10,
            battery: Math.round(batVal * 10) / 10
          }];
          if (newHist.length > 30) newHist.shift();
          return newHist;
        });
      } catch (err) {
        // Edge API unavailable — keep current state, no simulated data
      }
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  // Fetch existing geofence on mount or when active mission changes
  useEffect(() => {
    if (mission?.active && (mission?.has_geofence || mission?.geofence)) {
      if (mission.geofence) {
        setSavedGeofence(mission.geofence);
      } else {
        authFetch('/edge-api/api/edge/mission/geofence')
          .then(r => r.json())
          .then(d => {
            if (d.has_geofence && d.geofence) {
              setSavedGeofence(d.geofence);
            }
          })
          .catch(() => {});
      }
    }
  }, [mission?.active, mission?.has_geofence, mission?.geofence]);

  const handleToggleServerSend = async () => {
    try {
      const resp = await authFetch('/edge-api/api/edge/mission/toggle-ingest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !serverSend })
      });
      const data = await resp.json();
      setServerSend(data.server_send_enabled);
      if (data.server_send_enabled) {
        setIsDrawing(false);
      }
    } catch (err) {
      console.error('Failed to toggle server send:', err);
    }
  };

  const handleAddPoint = (latlng) => {
    setDrawnPoints(prev => [...prev, { lat: Number(latlng.lat), lng: Number(latlng.lng) }]);
  };

  const handleUndoPoint = () => {
    setDrawnPoints(prev => prev.slice(0, -1));
  };

  const handleClearDrawing = async () => {
    setDrawnPoints([]);
    setIsDrawing(false);
    if (savedGeofence) {
      try {
        await authFetch('/edge-api/api/edge/mission/geofence', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ geofence: null })
        });
        setSavedGeofence(null);
        setGeofenceFeedback({ type: 'info', message: 'Geofence cleared.' });
        setTimeout(() => setGeofenceFeedback(null), 3000);
      } catch (err) {
        console.error('Failed to clear geofence:', err);
      }
    }
  };

  const handleSaveGeofence = async () => {
    if (drawnPoints.length < 3) {
      setGeofenceFeedback({ type: 'error', message: 'At least 3 vertices required.' });
      setTimeout(() => setGeofenceFeedback(null), 3000);
      return;
    }

    setIsSavingGeofence(true);
    try {
      const missionId = mission?.mission_id || mission?.id || `MISSION_${Date.now()}`;
      const zoneId = `ZONE_${missionId}`;
      const zoneName = `Restricted Zone (${mission?.mission_name || 'Active Mission'})`;

      // Convert [lat, lng] to WGS84 GeoJSON [lng, lat] and close ring
      const coords = drawnPoints.map(p => [Number(p.lng.toFixed(6)), Number(p.lat.toFixed(6))]);
      coords.push([coords[0][0], coords[0][1]]); // closed ring

      const geojsonPayload = {
        type: "FeatureCollection",
        features: [
          {
            type: "Feature",
            properties: {
              zone_id: zoneId,
              name: zoneName,
              creation_mode: "MAP_DRAWN",
              reference_metadata: {
                mission_id: missionId,
                drone_id: mission?.drone_id || `DRN-${missionId}`,
                operator: mission?.operator || 'operator_001',
                created_at: new Date().toISOString(),
                initial_drone_lat: telemetry.lat,
                initial_drone_lon: telemetry.lng,
                initial_drone_alt_m: telemetry.alt
              }
            },
            geometry: {
              type: "Polygon",
              coordinates: [coords]
            }
          }
        ]
      };

      const resp = await authFetch('/edge-api/api/edge/mission/geofence', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ geofence: geojsonPayload })
      });
      const data = await resp.json();

      if (data.status === 'ok') {
        setSavedGeofence(geojsonPayload);
        setIsDrawing(false);
        setDrawnPoints([]);
        setGeofenceFeedback({
          type: 'success',
          message: `Zone ${zoneId} saved! Transmits when Server Send: ON.`
        });
        setTimeout(() => setGeofenceFeedback(null), 4000);
      }
    } catch (err) {
      console.error('Failed to save geofence:', err);
      setGeofenceFeedback({ type: 'error', message: 'Failed to save geofence to edge server.' });
      setTimeout(() => setGeofenceFeedback(null), 3500);
    } finally {
      setIsSavingGeofence(false);
    }
  };

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
                <span className="text-sm font-mono text-slate-200">
                  {telemetry.gpsLock ? `${telemetry.lat.toFixed(6)}°` : 'N/A (No Lock)'}
                </span>
              </div>
              <div className="flex justify-between items-center pb-2 border-b border-slate-800">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider">Longitude</span>
                <span className="text-sm font-mono text-slate-200">
                  {telemetry.gpsLock ? `${telemetry.lng.toFixed(6)}°` : 'N/A (No Lock)'}
                </span>
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

              {/* Server Send Transmission Control Card */}
              <div className="pt-3 border-t border-slate-800/80 mt-2">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[10px] text-slate-400 font-semibold uppercase tracking-wider flex items-center gap-1.5">
                    <Radio size={13} className={serverSend ? "text-emerald-400 animate-pulse" : "text-slate-500"} />
                    Server Send
                  </span>
                  <span className={`text-[10px] font-mono px-2 py-0.5 rounded font-bold ${serverSend ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30" : "bg-amber-500/20 text-amber-400 border border-amber-500/30"}`}>
                    {serverSend ? "ACTIVE" : "STANDBY"}
                  </span>
                </div>
                <button
                  onClick={handleToggleServerSend}
                  disabled={!mission?.active}
                  className={`w-full py-2 px-3 rounded text-xs font-semibold flex items-center justify-center gap-2 transition-all ${
                    !mission?.active
                      ? "bg-slate-800 text-slate-500 cursor-not-allowed border border-slate-700/50"
                      : serverSend
                      ? "bg-red-950/50 hover:bg-red-900/70 text-red-300 border border-red-700/50"
                      : "bg-emerald-600 hover:bg-emerald-500 text-white shadow-lg shadow-emerald-600/20"
                  }`}
                >
                  {serverSend ? (
                    <>
                      <Pause size={14} /> SERVER SEND: OFF
                    </>
                  ) : (
                    <>
                      <Play size={14} /> SERVER SEND: ON
                    </>
                  )}
                </button>
                <div className="text-[10px] text-slate-500 mt-1.5 leading-relaxed text-center">
                  {serverSend
                    ? "Transmitting frames to Ingest Server. Toggle SERVER SEND: OFF to pause & edit geofence."
                    : savedGeofence
                    ? "Zone armed. Activate SERVER SEND: ON to transmit geofence and resume playback."
                    : "Mission in Standby. Draw polygon to set restricted zone or activate SERVER SEND: ON without zone."}
                </div>
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
          <div className={`lg:col-span-9 bg-slate-900 border border-slate-800 rounded-lg p-2 h-[420px] relative z-0 ${isDrawing ? 'cursor-crosshair' : ''}`}>
            <MapContainer 
              center={[KERALA_LAT, KERALA_LNG]} 
              zoom={15} 
              scrollWheelZoom={true} 
              className="w-full h-full rounded-md"
              zoomControl={false}
            >
              <TileLayer
                attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              />
              
              {/* Auto-recenter map on drone position */}
              <RecenterMap lat={telemetry.lat} lng={telemetry.lng} />
              
              {/* Flight path trail */}
              <Polyline 
                positions={path.map(p => [p.lat, p.lng])}
                pathOptions={{ 
                  color: '#0f172a',
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
                    <div className="font-bold text-slate-800">
                      {mission?.mission_name || mission?.drone_id || 'Active Asset (Standby)'}
                    </div>
                    <div className="text-xs mt-1 text-slate-600">
                      Alt: <span className="font-mono text-cyan-600">{telemetry.alt.toFixed(1)}m</span><br/>
                      Spd: <span className="font-mono text-amber-600">{telemetry.speed.toFixed(1)}m/s</span><br/>
                      Bat: <span className="font-mono text-emerald-600">{telemetry.battery.toFixed(1)}%</span>
                    </div>
                  </div>
                </Popup>
              </Marker>

              {/* Drawn / saved geofence polygon */}
              {savedGeofence?.features?.[0]?.geometry?.coordinates?.[0]?.length > 2 && (
                <Polygon 
                  positions={savedGeofence.features[0].geometry.coordinates[0].map(pt => [pt[1], pt[0]])}
                  pathOptions={{ 
                    color: serverSend ? '#ef4444' : '#f59e0b',
                    fillColor: serverSend ? '#ef4444' : '#f59e0b',
                    fillOpacity: 0.18,
                    weight: 2,
                    dashArray: serverSend ? undefined : '5, 5'
                  }}
                >
                  <Popup className="dvas-map-popup">
                    <div className="font-sans text-xs">
                      <div className="font-bold text-red-600 flex items-center gap-1">
                        <ShieldAlert size={14} /> {savedGeofence.features[0].properties.zone_id}
                      </div>
                      <div className="text-slate-600 font-medium mt-0.5">{savedGeofence.features[0].properties.name}</div>
                      <div className="text-[10px] text-slate-500 mt-1">
                        Status: <span className="font-semibold text-slate-700">{serverSend ? 'ACTIVE (MONITORING INTRUSION)' : 'ARMED (STANDBY)'}</span>
                      </div>
                    </div>
                  </Popup>
                </Polygon>
              )}

              {/* Active drawing points & lines in progress */}
              {isDrawing && drawnPoints.map((pt, idx) => (
                <CircleMarker 
                  key={`draw-pt-${idx}`} 
                  center={[pt.lat, pt.lng]} 
                  radius={5}
                  pathOptions={{ color: '#f59e0b', fillColor: '#fbbf24', fillOpacity: 0.9, weight: 2 }}
                />
              ))}
              {isDrawing && drawnPoints.length > 1 && (
                <Polyline 
                  positions={drawnPoints.map(p => [p.lat, p.lng])}
                  pathOptions={{ color: '#f59e0b', weight: 2, dashArray: '4, 4' }}
                />
              )}
              {isDrawing && drawnPoints.length >= 3 && (
                <Polygon 
                  positions={drawnPoints.map(p => [p.lat, p.lng])}
                  pathOptions={{ color: '#f59e0b', fillColor: '#f59e0b', fillOpacity: 0.1, weight: 1, dashArray: '4, 4' }}
                />
              )}
              <PolygonDrawer isDrawing={isDrawing} onAddPoint={handleAddPoint} />
            </MapContainer>

            {/* Floating map labels over the map (Top Left) */}
            <div className="absolute top-4 left-4 z-[400] flex gap-2 pointer-events-none">
              <div className="px-3 py-1.5 bg-white/90 backdrop-blur shadow-sm border border-slate-200 text-slate-800 text-xs font-semibold rounded flex items-center gap-1.5">
                <MapPin size={14} className="text-sky-500" /> {mission?.location || 'Tactical Sector (Standby)'}
              </div>
              {mission?.active && !mission?.server_send_enabled && (mission?.video_source_type === 'file' || mission?.is_paused) && (
                <div className="px-3 py-1.5 bg-amber-500/90 text-slate-950 font-mono text-xs font-semibold rounded shadow-md flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-slate-950 animate-ping"></span>
                  STANDBY — Initial GPS Loaded (Awaiting Server Send: ON)
                </div>
              )}
            </div>

            {/* Top Right Floating Geofence Toolbar */}
            <div className="absolute top-4 right-4 z-[400] flex flex-col items-end gap-2">
              {/* Feedback notification pill */}
              {geofenceFeedback && (
                <div className={`px-3 py-1.5 rounded text-xs font-semibold shadow-md flex items-center gap-1.5 backdrop-blur border ${
                  geofenceFeedback.type === 'success' ? 'bg-emerald-950/90 text-emerald-300 border-emerald-500/40' :
                  geofenceFeedback.type === 'error' ? 'bg-red-950/90 text-red-300 border-red-500/40' :
                  'bg-slate-900/90 text-slate-200 border-slate-700'
                }`}>
                  {geofenceFeedback.message}
                </div>
              )}

              {/* Locked state during active streaming */}
              {mission?.active && serverSend && (
                <div className="px-3 py-1.5 bg-slate-950/85 backdrop-blur text-slate-300 text-xs font-semibold rounded border border-slate-700 shadow-md flex items-center gap-2">
                  <Lock size={13} className="text-amber-400" />
                  <span>Geofence Locked (Pause to Edit)</span>
                  {savedGeofence && (
                    <span className="text-[10px] font-mono bg-red-950/70 text-red-400 border border-red-800/60 px-1.5 py-0.5 rounded">
                      {savedGeofence.features[0].properties.zone_id}
                    </span>
                  )}
                </div>
              )}

              {/* Editable state when Standby or Paused */}
              {mission?.active && !serverSend && (
                <div className="bg-slate-950/90 backdrop-blur p-2 rounded-lg border border-slate-700/80 shadow-lg flex items-center gap-2">
                  {!isDrawing ? (
                    <>
                      <button
                        onClick={() => {
                          setIsDrawing(true);
                          setDrawnPoints([]);
                        }}
                        className="px-2.5 py-1.5 bg-amber-500 hover:bg-amber-400 text-slate-950 text-xs font-bold rounded flex items-center gap-1.5 shadow"
                      >
                        <Pencil size={13} /> {savedGeofence ? 'Redraw Zone' : 'Draw Geofence'}
                      </button>
                      {savedGeofence && (
                        <>
                          <div className="text-[11px] font-mono text-amber-400 px-2 flex items-center gap-1">
                            <ShieldAlert size={13} className="text-red-400" />
                            <span>Zone Armed ({savedGeofence.features[0].geometry.coordinates[0].length - 1} pts)</span>
                          </div>
                          <button
                            onClick={handleClearDrawing}
                            className="p-1.5 hover:bg-slate-800 text-slate-400 hover:text-red-400 rounded transition-colors"
                            title="Remove Geofence"
                          >
                            <Trash2 size={14} />
                          </button>
                        </>
                      )}
                    </>
                  ) : (
                    <>
                      <div className="text-xs text-amber-300 font-mono px-2 flex items-center gap-1.5">
                        <span className="w-2 h-2 rounded-full bg-amber-400 animate-ping"></span>
                        Click map ({drawnPoints.length} pts)
                      </div>
                      <button
                        onClick={handleUndoPoint}
                        disabled={drawnPoints.length === 0}
                        className="px-2 py-1 bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-slate-300 text-xs rounded flex items-center gap-1"
                        title="Undo last vertex"
                      >
                        <Undo2 size={13} /> Undo
                      </button>
                      <button
                        onClick={() => {
                          setIsDrawing(false);
                          setDrawnPoints([]);
                        }}
                        className="px-2 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs rounded flex items-center gap-1"
                        title="Cancel drawing"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={handleSaveGeofence}
                        disabled={drawnPoints.length < 3 || isSavingGeofence}
                        className="px-3 py-1 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 disabled:hover:bg-emerald-600 text-white text-xs font-bold rounded flex items-center gap-1.5 shadow"
                      >
                        <Check size={13} /> {isSavingGeofence ? 'Saving...' : 'Save Zone'}
                      </button>
                    </>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Bottom Section: 3 Trend Charts */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          
          {/* Altitude Chart */}
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 h-[140px] flex flex-col">
            <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-2 font-semibold flex items-center justify-between">
              <span>Altitude (m)</span>
              <span className="text-cyan-400 font-mono text-xs">{telemetry.alt.toFixed(1)} m</span>
            </div>
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
                  <YAxis stroke="#475569" fontSize={9} tickFormatter={(v) => `${v}`} domain={[0, 'auto']} />
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
            <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-2 font-semibold flex items-center justify-between">
              <span>Speed (m/s)</span>
              <span className="text-amber-400 font-mono text-xs">{telemetry.speed.toFixed(1)} m/s</span>
            </div>
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
                  <YAxis stroke="#475569" fontSize={9} tickFormatter={(v) => `${v}`} domain={[0, 'auto']} />
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
            <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-2 font-semibold flex items-center justify-between">
              <span>Battery (%)</span>
              <span className="text-emerald-400 font-mono text-xs">{telemetry.battery.toFixed(1)}%</span>
            </div>
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
