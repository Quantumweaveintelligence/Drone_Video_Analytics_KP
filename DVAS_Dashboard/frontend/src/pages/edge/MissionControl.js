import React, { useState, useEffect } from 'react';
import AppShell from '@/components/layout/AppShell';
import { Target, Play, Trash, XCircle, CheckCircle, Cpu } from 'lucide-react';
import { authFetch, getAuthToken } from '@/lib/api';

export default function MissionControl() {
  const [activeMission, setActiveMission] = useState(null);
  const [serverSend, setServerSend] = useState(false);
  const [pipelineEnabled, setPipelineEnabled] = useState(true);

  const [form, setForm] = useState({ name: '', id: '', video_source_type: 'rtmp', video_source: '', operator: '', location: '', squadron: 'ALPHA' });
  const [videoFile, setVideoFile] = useState(null);
  const [srtFile, setSrtFile] = useState(null);
  const [maxUploadMb, setMaxUploadMb] = useState(2048);

  const [missionLog, setMissionLog] = useState(() => {
    const saved = localStorage.getItem('dvas_mission_log');
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        if (parsed.length > 2 || (parsed.length > 0 && parsed[0].id !== "MSN-9941-BRAVO")) {
          return parsed;
        }
      } catch (e) {
        console.error("Failed to parse saved mission log", e);
      }
    }
    return [];
  });

  useEffect(() => {
    const fetchSettings = async () => {
      try {
        const resp = await authFetch('/edge-api/api/edge/settings');
        if (resp.ok) {
          const data = await resp.json();
          if (data?.storage?.max_upload_size_mb) {
            setMaxUploadMb(data.storage.max_upload_size_mb);
          }
        }
      } catch (err) {
        console.warn('Failed to load edge settings:', err);
      }
    };
    fetchSettings();
  }, []);

  useEffect(() => {
    const fetchHistoricalMissions = async () => {
      try {
        const resp = await authFetch('/api/missions');
        if (resp.ok) {
          const data = await resp.json();
          if (data && data.length > 0) {
            setMissionLog(data.map(m => ({
              id: m.mission_id,
              designation: m.name,
              drone: m.drone_id,
              operator: m.operator_name,
              location: m.zone || m.location || '',
              start: m.started_at ? (m.started_at.includes('T') ? new Date(m.started_at).toLocaleTimeString('en-GB') : m.started_at) : '',
              end: m.ended_at ? new Date(m.ended_at).toLocaleTimeString('en-GB') : '',
              status: m.status
            })));
            return;
          }
        }
      } catch (err) {}
      
      // Fallback mock data if DB is completely empty and we have no local cache
      setMissionLog(prev => prev.length > 0 ? prev : [
        { id: "MSN-9941-BRAVO", designation: "Traffic Monitoring", drone: "DRN-002", operator: "A. Sharma", location: "Junction 12", start: "11:15:00", end: "13:45:00", status: "completed" },
        { id: "MSN-9940-CHARLIE", designation: "VIP Route Sweep", drone: "DRN-001", operator: "K. Kumar", location: "Airport Road", start: "08:30:00", end: "10:15:00", status: "completed" },
      ]);
    };
    fetchHistoricalMissions();
  }, []);

  useEffect(() => {
    localStorage.setItem('dvas_mission_log', JSON.stringify(missionLog));
  }, [missionLog]);

  const [loading, setLoading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState('');
  const [errorMsg, setErrorMsg] = useState('');

  const generateMissionId = (squadron = form.squadron || 'ALPHA') => {
    const now = new Date();
    const yyyy = now.getFullYear();
    const mm = String(now.getMonth() + 1).padStart(2, '0');
    const dd = String(now.getDate()).padStart(2, '0');
    const hh = String(now.getHours()).padStart(2, '0');
    const min = String(now.getMinutes()).padStart(2, '0');
    const ss = String(now.getSeconds()).padStart(2, '0');
    return `MSN-${yyyy}${mm}${dd}-${hh}${min}-${ss}-${squadron.toUpperCase()}`;
  };

  const handleNameChange = (e) => {
    const name = e.target.value;
    setForm(prev => ({
      ...prev,
      name,
      id: prev.id && !prev.id.startsWith('MSN-') ? prev.id : generateMissionId(prev.squadron)
    }));
  };

  const handleSquadronChange = (e) => {
    const squadron = e.target.value;
    setForm(prev => ({
      ...prev,
      squadron,
      id: prev.id && !prev.id.startsWith('MSN-') ? prev.id : generateMissionId(squadron)
    }));
  };

  const handleVideoFileChange = (e) => {
    const file = e.target.files[0];
    if (!file) {
      setVideoFile(null);
      return;
    }
    const maxBytes = maxUploadMb * 1024 * 1024;
    if (file.size > maxBytes) {
      const actualMb = (file.size / (1024 * 1024)).toFixed(1);
      setErrorMsg(`Selected video (${actualMb} MB) exceeds maximum allowed limit of ${maxUploadMb} MB. Adjust limit in Settings or choose a smaller file.`);
      e.target.value = '';
      setVideoFile(null);
      return;
    }
    setErrorMsg('');
    setVideoFile(file);
  };

  const handleStartMission = async () => {
    if (!form.name) return;
    if (form.video_source_type === 'rtmp' && !form.video_source) return;
    if (form.video_source_type === 'file' && !videoFile) return;

    setLoading(true);
    setErrorMsg('');
    try {
      let finalVideoSource = form.video_source;

      if (form.video_source_type === 'file') {
        const maxBytes = maxUploadMb * 1024 * 1024;
        if (videoFile.size > maxBytes) {
          setErrorMsg(`File size (${(videoFile.size / (1024 * 1024)).toFixed(1)} MB) exceeds allowed limit of ${maxUploadMb} MB.`);
          setLoading(false);
          return;
        }
        setUploadProgress('Uploading video...');
        if (srtFile) {
          const srtFormData = new FormData();
          srtFormData.append('srt', srtFile);
          await authFetch('/edge-api/api/edge/mission/upload-srt', {
            method: 'POST',
            body: srtFormData,
          });
        }

        const uploadData = await new Promise((resolve, reject) => {
          const xhr = new XMLHttpRequest();
          xhr.open('POST', '/edge-api/api/edge/mission/upload-video-stream', true);
          xhr.withCredentials = true; // Required to send cookies along with the request!
          
          const token = getAuthToken();
          if (token) {
            xhr.setRequestHeader('Authorization', `Bearer ${token}`);
          }
          xhr.setRequestHeader('x-filename', encodeURIComponent(videoFile.name));
          xhr.setRequestHeader('Content-Type', videoFile.type || 'application/octet-stream');

          xhr.upload.onprogress = (event) => {
            if (event.lengthComputable) {
              const percentComplete = Math.round((event.loaded / event.total) * 100);
              const mbUploaded = (event.loaded / (1024 * 1024)).toFixed(1);
              const mbTotal = (event.total / (1024 * 1024)).toFixed(1);
              setUploadProgress(`Streaming to disk... ${percentComplete}% (${mbUploaded} / ${mbTotal} MB)`);
            }
          };

          xhr.onload = () => {
            if (xhr.status >= 200 && xhr.status < 300) {
              resolve(JSON.parse(xhr.responseText));
            } else {
              reject(new Error(`Video upload failed: ${xhr.status} ${xhr.statusText}`));
            }
          };

          xhr.onerror = () => {
            reject(new Error('Video upload failed: Network Error'));
          };

          xhr.send(videoFile); // Send raw file directly as stream
        });
        finalVideoSource = uploadData.video_path;
        setUploadProgress('');
      }

      const resp = await authFetch('/edge-api/api/edge/mission/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mission_id: form.id || generateMissionId(form.squadron),
          mission_name: form.name,
          video_source: finalVideoSource,
          video_source_type: form.video_source_type,
          operator: form.operator,
          location: form.location,
        }),
      });
      const data = await resp.json();
      if (data.status === 'started') {
        setActiveMission({
          id: data.mission_id,
          designation: data.mission_name,
          drone: data.drone_id,
          operator: data.operator || form.operator,
          location: data.location || form.location,
          videoSource: data.video_source,
          videoSourceType: data.video_source_type,
          startTime: data.start_time,
        });
        setServerSend(false);
        setPipelineEnabled(true);
        setMissionLog(prev => [{
          id: data.mission_id,
          designation: data.mission_name,
          drone: data.drone_id,
          operator: form.operator,
          location: form.location,
          start: data.start_time.includes('T') ? new Date(data.start_time).toLocaleTimeString('en-GB') : data.start_time,
          end: '--:--:--',
          status: 'active',
        }, ...prev]);
        setForm({ name: '', id: '', video_source_type: 'rtmp', video_source: '', operator: '', location: '', squadron: 'ALPHA' });
        setVideoFile(null);
        setSrtFile(null);

        // Save to backend database
        try {
          await authFetch('/api/missions', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({
              mission_id: data.mission_id,
              name: data.mission_name,
              drone_id: data.drone_id,
              operator_name: form.operator,
              location: form.location,
              started_at: data.start_time
            })
          });
        } catch (backendErr) {
          console.error("Failed to save mission to backend:", backendErr);
        }
      }
    } catch (err) {
      console.error('Failed to start mission:', err);
      setErrorMsg(err.message || 'Failed to start mission');
      setUploadProgress('');
    } finally {
      setLoading(false);
    }
  };

  const handleEndMission = async () => {
    try {
      const resp = await authFetch('/edge-api/api/edge/mission/end', { method: 'POST' });
      const data = await resp.json();
      if (data.status === 'ended') {
        setMissionLog(prev => prev.map(m => 
          m.id === activeMission?.id 
            ? { ...m, end: (data.end_time && data.end_time.includes('T')) ? new Date(data.end_time).toLocaleTimeString('en-GB') : (data.end_time || new Date().toLocaleTimeString('en-GB')), status: 'completed' }
            : m
        ));
        
        // Update backend database
        if (activeMission?.id) {
          try {
            await authFetch(`/api/missions/${activeMission.id}/end`, {
              method: 'PATCH',
            });
          } catch (backendErr) {
            console.error("Failed to mark mission as ended in backend:", backendErr);
          }
        }
        
        setActiveMission(null);
      }
    } catch (err) {
      console.error('Failed to end mission:', err);
      setActiveMission(null);
    }
  };

  const activeMissionRef = React.useRef(activeMission);
  useEffect(() => {
    activeMissionRef.current = activeMission;
  }, [activeMission]);

  useEffect(() => {
    const poll = async () => {
      try {
        const resp = await authFetch('/edge-api/api/edge/mission/active');
        const data = await resp.json();
        if (data.active) {
          setServerSend(data.server_send_enabled || false);
          setPipelineEnabled(data.pipeline_enabled !== undefined ? data.pipeline_enabled : true);
          setActiveMission({
            id: data.mission_id,
            designation: data.mission_name,
            drone: data.drone_id,
            operator: data.operator,
            location: data.location,
            startTime: data.start_time,
            videoSource: data.video_source,
            videoSourceType: data.video_source_type,
          });
        } else {
          setActiveMission(null);
          setMissionLog(logs => {
            const staleMissions = logs.filter(m => m.status === 'active');
            if (staleMissions.length > 0) {
              staleMissions.forEach(m => {
                authFetch(`/api/missions/${m.id}/end`, { method: 'PATCH' }).catch(e => console.error(e));
              });
              return logs.map(m => m.status === 'active' ? { ...m, end: new Date().toLocaleTimeString('en-GB'), status: 'completed' } : m);
            }
            return logs;
          });
        }
      } catch (err) {
        // Edge server not available - keep current state
      }
    };
    poll();
    const interval = setInterval(poll, 3000);
    return () => clearInterval(interval);
  }, []);

  const handleToggleServerSend = async () => {
    try {
      const resp = await authFetch('/edge-api/api/edge/mission/toggle-ingest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !serverSend })
      });
      const data = await resp.json();
      setServerSend(data.server_send_enabled);
    } catch (err) {
      console.error('Failed to toggle server send:', err);
    }
  };

  const handleTogglePipeline = async () => {
    try {
      const resp = await authFetch('/edge-api/api/edge/mission/toggle-pipeline', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !pipelineEnabled })
      });
      const data = await resp.json();
      setPipelineEnabled(data.pipeline_enabled);
    } catch (err) {
      console.error('Failed to toggle pipeline:', err);
    }
  };

  return (
    <AppShell title="Mission Control" subtitle="Start, monitor, and terminate drone missions">
      <div className="space-y-6">
        
        {/* Top Two Panels */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          
          {/* Active Mission Panel */}
          <div className="bg-slate-900 border border-slate-800 p-6 rounded-lg flex flex-col">
            <div className="flex justify-between items-center mb-6">
              <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400">Active Mission</h2>
              {activeMission ? (
                <div className="flex items-center gap-2 text-xs px-2.5 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 font-mono tracking-wider">
                  <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
                  ACTIVE
                </div>
              ) : (
                <div className="text-xs px-2.5 py-1 rounded-full bg-slate-800 text-slate-400 font-mono tracking-wider">
                  NO ACTIVE MISSION
                </div>
              )}
            </div>

            {activeMission ? (
              <div className="flex-1 flex flex-col">
                <div className="text-2xl font-bold text-sky-200 font-mono mb-2">{activeMission.id}</div>
                <div className="text-base text-slate-300 font-medium mb-6">{activeMission.designation}</div>
                
                <div className="grid grid-cols-2 gap-y-4 mb-8">
                  <div>
                    <div className="text-[10px] text-slate-500 uppercase font-semibold">Drone Asset</div>
                    <div className="text-sm text-slate-200">{activeMission.drone}</div>
                  </div>
                  <div>
                    <div className="text-[10px] text-slate-500 uppercase font-semibold">Operator</div>
                    <div className="text-sm text-slate-200">{activeMission.operator}</div>
                  </div>
                  <div>
                    <div className="text-[10px] text-slate-500 uppercase font-semibold">Location</div>
                    <div className="text-sm text-slate-200">{activeMission.location}</div>
                  </div>
                  <div>
                    <div className="text-[10px] text-slate-500 uppercase font-semibold">Start Time</div>
                    <div className="text-sm text-slate-200 font-mono">{activeMission.startTime}</div>
                  </div>
                  <div>
                    <div className="text-[10px] text-slate-500 uppercase font-semibold">Stream Type</div>
                    <div className="text-sm text-slate-200 uppercase">{activeMission.videoSourceType === 'rtmp' ? 'RTMP' : 'Input Video'}</div>
                  </div>
                  <div className="col-span-2">
                    <div className="text-[10px] text-slate-500 uppercase font-semibold">Source</div>
                    <div className="text-sm text-slate-200 font-mono break-all">{activeMission.videoSourceType === 'file' ? activeMission.videoSource.split('/').pop() : activeMission.videoSource}</div>
                  </div>
                </div>

                <div className="mt-auto space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <button 
                      onClick={handleToggleServerSend}
                      className={`py-2.5 border text-sm font-semibold tracking-wide uppercase transition-colors rounded ${
                        serverSend 
                          ? 'border-emerald-500/50 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20' 
                          : 'border-slate-600 hover:bg-slate-800 text-slate-300'
                      }`}
                    >
                      {serverSend ? 'Server Send: ON' : 'Server Send: OFF'}
                    </button>
                    <button 
                      onClick={handleEndMission}
                      className="py-2.5 border border-red-500/50 hover:bg-red-500/10 text-red-400 text-sm font-semibold tracking-wide uppercase transition-colors rounded"
                    >
                      End Mission
                    </button>
                  </div>
                  {!serverSend && activeMission.videoSourceType === 'file' && (
                    <div className="text-[11px] text-amber-400/90 text-center font-mono tracking-wide bg-amber-500/10 border border-amber-500/20 py-1.5 px-2 rounded">
                      Video playback & live telemetry are holding on Frame 1. Activate Server Send to begin transmission.
                    </div>
                  )}

                  {/* Pipeline Toggle */}
                  <button 
                    onClick={handleTogglePipeline}
                    className={`w-full py-2.5 border text-sm font-semibold tracking-wide uppercase transition-colors rounded flex items-center justify-center gap-2 ${
                      pipelineEnabled
                        ? 'border-sky-500/50 bg-sky-500/10 text-sky-400 hover:bg-sky-500/20'
                        : 'border-amber-500/50 bg-amber-500/10 text-amber-400 hover:bg-amber-500/20'
                    }`}
                  >
                    <Cpu size={16} />
                    {pipelineEnabled ? 'Pipeline: ON' : 'Pipeline: OFF — Direct Send'}
                  </button>
                  {!pipelineEnabled && (
                    <div className="text-[10px] text-amber-400/70 text-center font-mono tracking-wide">
                      Change Detection & Cognitive Selection bypassed — sending frames continuously as fast as network permits
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="flex-1 flex items-center justify-center text-slate-500 text-sm">
                Awaiting new mission deployment.
              </div>
            )}
          </div>

          {/* New Mission Form */}
          <div className="bg-slate-900 border border-slate-800 p-6 rounded-lg flex flex-col">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-6">New Mission</h2>
            <div className="space-y-4 flex-1">
              <div>
                <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1.5">Mission Name *</label>
                <input 
                  type="text" 
                  value={form.name} onChange={handleNameChange}
                  className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-sm text-slate-200"
                  placeholder="e.g. VIP Route Sweep"
                />
              </div>
              <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1.5">Mission ID</label>
                    <input 
                      type="text" 
                      value={form.id}
                      readOnly
                      className="w-full bg-slate-950/50 border border-slate-700 outline-none px-3 py-2 text-sm font-mono text-slate-400 cursor-not-allowed"
                      placeholder="Auto-generated"
                    />
                  </div>
                <div>
                  <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1.5">Source Type *</label>
                  <select 
                    value={form.video_source_type} 
                    onChange={e => setForm({...form, video_source_type: e.target.value})}
                    className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-sm font-mono text-slate-200"
                  >
                    <option value="rtmp">RTMP Stream</option>
                    <option value="file">Video File</option>
                  </select>
                </div>
              </div>
              {form.video_source_type === 'rtmp' ? (
                <div>
                  <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1.5">RTMP URL *</label>
                  <input 
                    type="text" 
                    value={form.video_source} 
                    onChange={e => setForm({...form, video_source: e.target.value})}
                    className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-sm text-slate-200 font-mono"
                    placeholder="rtmp://your-server/live/stream"
                  />
                  <p className="text-[9px] text-slate-600 mt-1">Enter a valid RTMP stream URL</p>
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1.5">Video File *</label>
                    <input 
                      type="file" 
                      accept=".mp4,.avi,.mov"
                      onChange={handleVideoFileChange}
                      className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-sm text-slate-200"
                    />
                    <p className="text-[9px] text-slate-500 mt-1">Supported: .mp4, .avi, .mov • Max size: {maxUploadMb} MB</p>
                  </div>
                  <div>
                    <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1.5">SRT Telemetry File (Optional)</label>
                    <input 
                      type="file" 
                      accept=".srt"
                      onChange={e => setSrtFile(e.target.files[0])}
                      className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-sm text-slate-200"
                    />
                    <p className="text-[9px] text-slate-600 mt-1">Optional sidecar metadata</p>
                  </div>
                </div>
              )}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1.5">Operator Name</label>
                  <input 
                    type="text" 
                    value={form.operator} onChange={e => setForm({...form, operator: e.target.value})}
                    className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-sm text-slate-200"
                  />
                </div>
                <div>
                  <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1.5">Squadron</label>
                  <input 
                    type="text" 
                    value={form.squadron} onChange={handleSquadronChange}
                    className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-sm font-mono text-slate-200 uppercase"
                    placeholder="e.g. ALPHA"
                  />
                </div>
              </div>
              <div>
                <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1.5">Location / Grid</label>
                <input 
                  type="text" 
                  value={form.location} onChange={e => setForm({...form, location: e.target.value})}
                  className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-sm text-slate-200"
                />
              </div>
            </div>

            <div className="mt-6 flex flex-col gap-2">
              {errorMsg && <div className="text-xs text-red-400 font-mono text-center mb-2">{errorMsg}</div>}
              {uploadProgress && <div className="text-xs text-sky-400 font-mono text-center mb-2 animate-pulse">{uploadProgress}</div>}
              <div className="flex gap-3">
                <button 
                  onClick={handleStartMission} 
                  disabled={loading || !form.name || (form.video_source_type === 'rtmp' && !form.video_source) || (form.video_source_type === 'file' && !videoFile)}
                  className="flex-1 bg-sky-600 hover:bg-sky-500 text-white py-2.5 text-sm font-semibold tracking-wide flex items-center justify-center gap-2 rounded transition-colors disabled:opacity-50"
                >
                  <Play size={16} fill="currentColor" /> {loading ? 'Starting...' : 'Start Mission'}
                </button>
                <button 
                  onClick={() => setForm({ name: '', id: '', video_source_type: 'rtmp', video_source: '', operator: '', location: '', squadron: 'ALPHA' })}
                  className="px-6 border border-slate-700 hover:bg-slate-800 text-slate-300 py-2.5 text-sm font-semibold tracking-wide rounded transition-colors"
                >
                  Clear
                </button>
              </div>
            </div>
          </div>
        </div>

        {/* Mission Log Table */}
        <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
          <div className="p-4 border-b border-slate-800">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400">Mission Log</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm whitespace-nowrap">
              <thead className="bg-slate-950/50 text-slate-500 text-[10px] uppercase font-semibold">
                <tr>
                  <th className="px-4 py-3">Mission ID</th>
                  <th className="px-4 py-3">Designation</th>
                  <th className="px-4 py-3">Drone</th>
                  <th className="px-4 py-3">Operator</th>
                  <th className="px-4 py-3">Location</th>
                  <th className="px-4 py-3">Start</th>
                  <th className="px-4 py-3">End</th>
                  <th className="px-4 py-3">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/50 text-slate-300">
                {missionLog.map((m, i) => (
                  <tr key={i} className="hover:bg-slate-800/20 transition-colors">
                    <td className="px-4 py-3 font-mono text-sky-200">{m.id}</td>
                    <td className="px-4 py-3 font-medium">{m.designation}</td>
                    <td className="px-4 py-3 font-mono">{m.drone}</td>
                    <td className="px-4 py-3">{m.operator}</td>
                    <td className="px-4 py-3">{m.location}</td>
                    <td className="px-4 py-3 font-mono text-xs text-slate-400">{m.start}</td>
                    <td className="px-4 py-3 font-mono text-xs text-slate-400">{m.end}</td>
                    <td className="px-4 py-3">
                      {m.status === 'active' ? (
                        <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-mono tracking-wider bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span> ACTIVE
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-mono tracking-wider bg-slate-800 text-slate-400 border border-slate-700">
                          COMPLETED
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

      </div>
    </AppShell>
  );
}
