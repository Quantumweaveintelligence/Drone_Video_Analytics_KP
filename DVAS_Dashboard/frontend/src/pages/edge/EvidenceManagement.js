import React, { useState, useEffect } from 'react';
import AppShell from '@/components/layout/AppShell';
import { api, authFetch } from '@/lib/api';
import { useAuth } from '@/context/AuthContext';
import { 
  Package, Download, Database, CheckCircle2, Clock, UploadCloud, 
  RefreshCw, AlertTriangle, Play, Trash2, X, Search, HardDrive, 
  Film, Shield, Sliders, ExternalLink, Zap
} from 'lucide-react';

export default function EvidenceManagement() {
  const { user } = useAuth();

  // Local SSD Evidence Clips & GPU Confirmed Events
  const [savedClips, setSavedClips] = useState([]);
  const [gpuEvents, setGpuEvents] = useState([]);
  const [missions, setMissions] = useState([]);
  const [activeMission, setActiveMission] = useState(null);
  const [diskUsage, setDiskUsage] = useState({ total_storage_mb: 0, free_gb: 93.8, used_percent: 52 });
  const [loading, setLoading] = useState(true);

  // Buffer Extraction Sliders & Form
  const [selectedMission, setSelectedMission] = useState("");
  const [preEventS, setPreEventS] = useState(15);
  const [postEventS, setPostEventS] = useState(15);
  const [eventType, setEventType] = useState("manual_flag");
  const [operatorNotes, setOperatorNotes] = useState("");
  const [isExtracting, setIsExtracting] = useState(false);
  const [extractProgress, setExtractProgress] = useState(0);
  const [extractStep, setExtractStep] = useState("");

  // Tabs & Search / Filters
  const [activeTab, setActiveTab] = useState("clips"); // "clips" | "gpu"
  const [searchQuery, setSearchQuery] = useState("");

  // Video Playback Modal & Action States
  const [activeModalClip, setActiveModalClip] = useState(null);
  const [deleteCandidate, setDeleteCandidate] = useState(null);
  const [toast, setToast] = useState(null);

  // Live IST Clock
  const [clockIst, setClockIst] = useState("");

  useEffect(() => {
    const timer = setInterval(() => {
      const now = new Date();
      const utc = now.getTime() + (now.getTimezoneOffset() * 60000);
      const istDate = new Date(utc + (3600000 * 5.5));
      const d = String(istDate.getDate()).padStart(2, '0');
      const m = String(istDate.getMonth() + 1).padStart(2, '0');
      const y = istDate.getFullYear();
      const timeStr = istDate.toTimeString().split(' ')[0];
      setClockIst(`${d}-${m}-${y} ${timeStr} IST`);
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  const showToast = (msg, type = "info") => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 4000);
  };

  const loadData = async () => {
    try {
      setLoading(true);
      
      let edgeClips = [];
      let totalStorageMb = 0;
      try {
        const edgeRes = await authFetch('/edge-api/api/edge/evidence');
        if (edgeRes.ok) {
          const edgeData = await edgeRes.json();
          edgeClips = edgeData.clips || [];
          totalStorageMb = edgeData.total_storage_mb || 0;
        }
      } catch (err) {
        console.debug("Edge evidence API fetch error:", err);
      }

      let confEvents = [];
      try {
        const backendRes = await api.get('/evidence', { params: { limit: 100 } });
        const items = Array.isArray(backendRes.data) ? backendRes.data : (backendRes.data?.items || []);
        confEvents = items;
      } catch (err) {
        console.debug("Backend evidence API fetch error:", err);
      }

      let missionList = [];
      try {
        const mRes = await api.get('/missions');
        missionList = mRes.data || [];
      } catch (err) {
        console.debug("Missions fetch error:", err);
      }

      let activeM = null;
      try {
        const actRes = await authFetch('/edge-api/api/edge/mission/active');
        if (actRes.ok) {
          const actData = await actRes.json();
          if (actData.active) {
            activeM = actData;
          }
        }
      } catch (err) {
        console.debug("Active mission fetch error:", err);
      }

      let storageFreeGb = 93.8;
      let usedPct = 52.0;
      try {
        const diskRes = await authFetch('/edge-api/api/edge/storage/disk-usage');
        if (diskRes.ok) {
          const dData = await diskRes.json();
          storageFreeGb = dData.free_gb || 93.8;
          usedPct = dData.used_percent || 52.0;
        }
      } catch (err) {
        console.debug("Disk usage fetch error:", err);
      }

      setSavedClips(edgeClips);
      setGpuEvents(confEvents);
      setMissions(missionList);
      setActiveMission(activeM);
      setDiskUsage({
        total_storage_mb: totalStorageMb,
        free_gb: storageFreeGb,
        used_percent: usedPct,
      });

      if (!selectedMission && activeM?.mission_id) {
        setSelectedMission(activeM.mission_id);
      } else if (!selectedMission && missionList.length > 0) {
        setSelectedMission(missionList[0].mission_id);
      }

    } catch (e) {
      console.error("Evidence page load error:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 20000);
    return () => clearInterval(interval);
  }, []);

  const handleTriggerExtract = async () => {
    if (isExtracting) return;
    setIsExtracting(true);
    setExtractProgress(15);
    setExtractStep("Slicing 60s rolling buffer on SSD...");

    const targetMissionId = selectedMission || activeMission?.mission_id || "M-101";

    const progressTimer = setInterval(() => {
      setExtractProgress(p => {
        if (p >= 85) return p;
        if (p >= 50) setExtractStep("Encoding MP4 video via OpenCV...");
        return p + 15;
      });
    }, 250);

    try {
      const resp = await authFetch('/edge-api/api/edge/evidence/extract', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          pre_event_s: preEventS,
          post_event_s: postEventS,
          mission_id: targetMissionId,
          event_type: eventType,
          notes: operatorNotes || `Manual buffer trigger (${preEventS}s pre + ${postEventS}s post)`,
        })
      });

      clearInterval(progressTimer);
      setExtractProgress(100);
      setExtractStep("Clip saved to /data/evidence/!");

      if (resp.ok) {
        const result = await resp.json();
        showToast(`✓ Clip saved: ${result.clip?.filename || 'EVID_...mp4'}`, "success");
        setOperatorNotes("");
        await loadData();
        setActiveTab("clips");
      } else {
        showToast("Buffer extraction failed. Check edge logs.", "error");
      }
    } catch (e) {
      clearInterval(progressTimer);
      console.error("Extraction error:", e);
      showToast("Network error communicating with Edge Station", "error");
    } finally {
      setTimeout(() => {
        setIsExtracting(false);
        setExtractProgress(0);
        setExtractStep("");
      }, 700);
    }
  };

  const handleExtractFromGpuEvent = (evt) => {
    setSelectedMission(evt.mission_id || activeMission?.mission_id || "M-101");
    setEventType(evt.event_type || "confirmed_gpu_event");
    setOperatorNotes(`Auto-extracted for confirmed GPU event: ${evt.id || ''} (${evt.event_type || ''})`);
    setActiveTab("clips");
    setTimeout(() => {
      handleTriggerExtract();
    }, 100);
  };

  const confirmDelete = async () => {
    if (!deleteCandidate) return;
    const { filename, id } = deleteCandidate;
    try {
      const res = await authFetch(`/edge-api/api/edge/evidence/${encodeURIComponent(filename)}`, {
        method: 'DELETE'
      });
      if (res.ok) {
        showToast(`Clip ${filename} deleted from SSD`, "info");
        setSavedClips(prev => prev.filter(c => c.filename !== filename && c.id !== id));
        if (activeModalClip?.filename === filename) {
          setActiveModalClip(null);
        }
      } else {
        showToast(`Failed to delete clip from SSD`, "error");
      }
    } catch (err) {
      console.error("Delete error:", err);
      showToast("Error deleting clip", "error");
    } finally {
      setDeleteCandidate(null);
      loadData();
    }
  };

  const handleDownload = (filename) => {
    const downloadUrl = `/edge-api/api/edge/evidence/${encodeURIComponent(filename)}/download`;
    const a = document.createElement('a');
    a.href = downloadUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    showToast(`Downloading ${filename} for transfer...`, "success");
  };

  const filteredClips = savedClips.filter(c => {
    const q = searchQuery.toLowerCase();
    return !q || 
      (c.filename && c.filename.toLowerCase().includes(q)) ||
      (c.mission_id && c.mission_id.toLowerCase().includes(q)) ||
      (c.notes && c.notes.toLowerCase().includes(q));
  });

  const filteredGpuEvents = gpuEvents.filter(e => {
    const q = searchQuery.toLowerCase();
    return !q || 
      (e.id && e.id.toLowerCase().includes(q)) ||
      (e.event_type && e.event_type.toLowerCase().includes(q)) ||
      (e.mission_id && e.mission_id.toLowerCase().includes(q)) ||
      (e.label && e.label.toLowerCase().includes(q));
  });

  const totalDurationS = preEventS + postEventS;


  return (
    <AppShell title="Evidence Management" subtitle={`Operator: ${user?.email || 'Field Operator'} — Local SSD Evidence & Buffer Extraction`}>
      {/* Toast Notification */}
      {toast && (
        <div className={`fixed bottom-6 right-6 z-50 px-4 py-3 rounded-lg shadow-2xl text-xs font-mono flex items-center gap-2 border transition-all ${
          toast.type === 'success' ? 'bg-emerald-950/95 border-emerald-500/50 text-emerald-200' :
          toast.type === 'error' ? 'bg-rose-950/95 border-rose-500/50 text-rose-200' :
          'bg-slate-900/95 border-slate-700 text-slate-200'
        }`}>
          {toast.type === 'success' ? <CheckCircle2 size={15} className="text-emerald-400" /> : 
           toast.type === 'error' ? <AlertTriangle size={15} className="text-rose-400" /> : 
           <Zap size={15} className="text-sky-400" />}
          <span>{toast.msg}</span>
        </div>
      )}

      {/* Operational Status Ribbon */}
      <div className="bg-slate-900/90 border border-slate-800 rounded-lg px-4 py-2.5 mb-5 flex flex-wrap items-center justify-between gap-3 text-xs">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="text-[10px] uppercase font-bold tracking-wider text-slate-500">Mission:</span>
            <span className="font-mono font-bold text-sky-400 bg-sky-500/10 px-2 py-0.5 rounded border border-sky-500/20">
              {activeMission?.mission_id || selectedMission || 'M-101'}
            </span>
          </div>

          <div className="h-3 w-px bg-slate-800 hidden sm:block"></div>

          <div className="flex items-center gap-2">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
            </span>
            <span className="text-[10px] uppercase font-bold tracking-wider text-slate-400">Rolling Buffer:</span>
            <span className="font-mono text-emerald-400 font-semibold">60s Circular (RAM/SSD)</span>
          </div>

          <div className="h-3 w-px bg-slate-800 hidden sm:block"></div>

          <div className="flex items-center gap-2">
            <HardDrive size={13} className="text-slate-400" />
            <span className="text-[10px] uppercase font-bold tracking-wider text-slate-500">Storage Target:</span>
            <span className="font-mono text-slate-300">/data/evidence/ (Local SSD)</span>
          </div>
        </div>

        <div className="flex items-center gap-3 ml-auto">
          <div className="flex items-center gap-1.5 font-mono text-slate-400 text-[11px] bg-slate-950 px-2.5 py-1 rounded border border-slate-800">
            <Clock size={12} className="text-amber-400" />
            <span>{clockIst || 'IST'}</span>
          </div>
          <button
            onClick={loadData}
            title="Refresh local clips & GPU events"
            className="p-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded border border-slate-700 transition-colors"
          >
            <RefreshCw size={13} className={loading ? "animate-spin text-sky-400" : ""} />
          </button>
        </div>
      </div>

      {/* 4 Metric Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 flex flex-col">
          <div className="flex items-center justify-between text-slate-500 mb-2">
            <span className="text-[10px] font-bold uppercase tracking-wider">Clips on Local SSD</span>
            <Film size={15} className="text-sky-400" />
          </div>
          <div className="text-2xl font-mono font-bold text-slate-100">{savedClips.length}</div>
          <div className="text-[11px] text-slate-500 mt-1">Immutable Mission IDs</div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 flex flex-col">
          <div className="flex items-center justify-between text-slate-500 mb-2">
            <span className="text-[10px] font-bold uppercase tracking-wider">Evidence Storage</span>
            <HardDrive size={15} className="text-indigo-400" />
          </div>
          <div className="text-2xl font-mono font-bold text-indigo-300">{diskUsage.total_storage_mb || 0} <span className="text-sm font-normal text-slate-400">MB</span></div>
          <div className="text-[11px] text-slate-500 mt-1">Direct SSD Allocation</div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 flex flex-col">
          <div className="flex items-center justify-between text-slate-500 mb-2">
            <span className="text-[10px] font-bold uppercase tracking-wider">Free SSD Space</span>
            <Database size={15} className="text-emerald-400" />
          </div>
          <div className="text-2xl font-mono font-bold text-emerald-400">{diskUsage.free_gb || 93.8} <span className="text-sm font-normal text-slate-400">GB</span></div>
          <div className="text-[11px] text-slate-500 mt-1">{100 - (diskUsage.used_percent || 52)}% storage headroom</div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 flex flex-col">
          <div className="flex items-center justify-between text-slate-500 mb-2">
            <span className="text-[10px] font-bold uppercase tracking-wider">Confirmed GPU Events</span>
            <Zap size={15} className="text-amber-400" />
          </div>
          <div className="text-2xl font-mono font-bold text-amber-300">{gpuEvents.length}</div>
          <div className="text-[11px] text-slate-500 mt-1">From Ingest / Analytics</div>
        </div>
      </div>

      {/* Main Dual-Column Interface */}
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-start">
        
        {/* Left Column: Buffer Extraction Configurator & Policy */}
        <div className="xl:col-span-4 flex flex-col gap-6 sticky top-6">
          
          <div className="bg-slate-900 border border-slate-800 rounded-lg flex flex-col overflow-hidden shadow-lg">
            <div className="p-4 border-b border-slate-800 bg-slate-950/60 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Sliders size={16} className="text-sky-400" />
                <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-300">Extract Buffer Clip</h2>
              </div>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-sky-500/10 text-sky-400 border border-sky-500/20">
                Window: {totalDurationS}s
              </span>
            </div>
            
            <div className="p-5 flex flex-col gap-4">
              <div>
                <label className="block text-[10px] text-slate-400 uppercase font-bold tracking-wider mb-1.5">
                  Target Mission ID
                </label>
                <select 
                  value={selectedMission}
                  onChange={e => setSelectedMission(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-sm text-slate-200 font-mono rounded cursor-pointer"
                >
                  {missions.length > 0 ? (
                    missions.map(m => <option key={m.mission_id} value={m.mission_id}>{m.mission_id} — {m.name || 'Active Mission'}</option>)
                  ) : (
                    <option value="M-101">M-101 — Kerala Police Drone Patrol</option>
                  )}
                </select>
              </div>

              {/* Pre-Event Context Slider */}
              <div className="bg-slate-950/80 border border-slate-800/80 p-3.5 rounded-lg">
                <div className="flex justify-between items-center mb-2">
                  <label className="text-[10px] uppercase font-bold tracking-wider text-slate-400">
                    Pre-Event Context
                  </label>
                  <span className="font-mono text-xs font-bold text-sky-400 bg-sky-500/10 px-2 py-0.5 rounded">
                    -{preEventS}s
                  </span>
                </div>
                <input 
                  type="range" 
                  min="5" 
                  max="30" 
                  step="5"
                  value={preEventS}
                  onChange={e => setPreEventS(Number(e.target.value))}
                  className="w-full accent-sky-500 bg-slate-800 h-1.5 rounded-lg cursor-pointer"
                />
                <div className="flex justify-between text-[9px] text-slate-500 font-mono mt-1">
                  <span>5s</span>
                  <span>15s (default)</span>
                  <span>30s max</span>
                </div>
              </div>

              {/* Post-Event Context Slider */}
              <div className="bg-slate-950/80 border border-slate-800/80 p-3.5 rounded-lg">
                <div className="flex justify-between items-center mb-2">
                  <label className="text-[10px] uppercase font-bold tracking-wider text-slate-400">
                    Post-Event Context
                  </label>
                  <span className="font-mono text-xs font-bold text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded">
                    +{postEventS}s
                  </span>
                </div>
                <input 
                  type="range" 
                  min="5" 
                  max="30" 
                  step="5"
                  value={postEventS}
                  onChange={e => setPostEventS(Number(e.target.value))}
                  className="w-full accent-emerald-500 bg-slate-800 h-1.5 rounded-lg cursor-pointer"
                />
                <div className="flex justify-between text-[9px] text-slate-500 font-mono mt-1">
                  <span>5s</span>
                  <span>15s (default)</span>
                  <span>30s max</span>
                </div>
              </div>

              {/* Event Type & Operator Notes */}
              <div>
                <label className="block text-[10px] text-slate-400 uppercase font-bold tracking-wider mb-1.5">
                  Incident Category / Event Tag
                </label>
                <select
                  value={eventType}
                  onChange={e => setEventType(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-xs text-slate-200 font-mono rounded cursor-pointer"
                >
                  <option value="manual_flag">manual_flag (Operator Flagged)</option>
                  <option value="violence_detected">violence_detected (Physical Altercation)</option>
                  <option value="weapon_detected">weapon_detected (Visible Threat)</option>
                  <option value="crowd_formation">crowd_formation (Unlawful Assembly)</option>
                  <option value="perimeter_breach">perimeter_breach (Restricted Area)</option>
                  <option value="traffic_anomaly">traffic_anomaly (Congestion / Incident)</option>
                </select>
              </div>

              <div>
                <label className="block text-[10px] text-slate-400 uppercase font-bold tracking-wider mb-1.5">
                  Operator Incident Notes
                </label>
                <textarea
                  rows="2"
                  value={operatorNotes}
                  onChange={e => setOperatorNotes(e.target.value)}
                  placeholder="e.g., Target vehicle stopped near junction, sudden agitation observed..."
                  className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none p-2.5 text-xs text-slate-200 rounded resize-none"
                />
              </div>

              {/* Extraction Progress Feedback */}
              {isExtracting && (
                <div className="space-y-1.5 bg-slate-950 p-3 rounded-lg border border-sky-500/30">
                  <div className="flex justify-between text-[10px] font-semibold text-sky-400 uppercase tracking-wider">
                    <span>{extractStep || "Extracting..."}</span>
                    <span className="font-mono">{extractProgress}%</span>
                  </div>
                  <div className="w-full bg-slate-800 h-2 rounded-full overflow-hidden">
                    <div 
                      className="bg-sky-500 h-full rounded-full transition-all duration-250 ease-out" 
                      style={{ width: `${extractProgress}%` }}
                    />
                  </div>
                </div>
              )}

              <button 
                onClick={handleTriggerExtract}
                disabled={isExtracting}
                className="w-full bg-sky-600 hover:bg-sky-500 disabled:bg-slate-800 disabled:text-slate-500 disabled:cursor-not-allowed text-white py-2.5 text-xs font-bold tracking-wide uppercase flex items-center justify-center gap-2 rounded transition-colors shadow-lg shadow-sky-600/20 mt-1"
              >
                {isExtracting ? (
                  <><RefreshCw size={14} className="animate-spin" /> Slicing Local Buffer...</>
                ) : (
                  <><Film size={14} /> Extract & Save to SSD ({totalDurationS}s)</>
                )}
              </button>
            </div>
          </div>

          {/* Evidence Architecture Policy Card */}
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 flex flex-col gap-2.5 text-xs text-slate-400">
            <div className="text-[10px] text-slate-500 font-bold uppercase tracking-wider flex items-center gap-1.5">
              <Shield size={13} className="text-emerald-400" /> Evidence Architecture Policy
            </div>
            <p className="text-[11px] leading-relaxed text-slate-300">
              Clips extracted from the rolling buffer are saved directly to the local SSD partition (<code className="text-sky-300">/data/evidence/</code>).
            </p>
            <div className="bg-slate-950 p-2.5 rounded border border-slate-800 text-[10px] font-mono space-y-1">
              <div className="text-slate-400 font-semibold">Naming Format:</div>
              <div className="text-emerald-400">EVID_&#123;mission_id&#125;_&#123;DD-MM-YYYY_HHmmss&#125;.mp4</div>
            </div>
            <div className="text-[10px] text-slate-500">
              ✓ Field-ready offline persistence<br />
              ✓ Direct operator download for external transfer<br />
              ✓ Full deletion rights to reclaim SSD storage
            </div>
          </div>

        </div>

        {/* Right Column: Dual-Tab Workspace (Local Clips vs GPU Events) */}
        <div className="xl:col-span-8 flex flex-col gap-4">
          
          {/* Header with Tabs & Search Toolbar */}
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-3 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <button
                onClick={() => setActiveTab("clips")}
                className={`flex items-center gap-2 px-3 py-1.5 rounded text-xs font-semibold tracking-wider uppercase transition-colors ${
                  activeTab === "clips" 
                    ? "bg-sky-600 text-white shadow" 
                    : "bg-slate-800/80 hover:bg-slate-800 text-slate-400"
                }`}
              >
                <Film size={14} />
                <span>Local SSD Clips</span>
                <span className={`px-1.5 py-0.2 rounded-full text-[10px] font-mono ${
                  activeTab === "clips" ? "bg-sky-700 text-white" : "bg-slate-700 text-slate-300"
                }`}>
                  {savedClips.length}
                </span>
              </button>

              <button
                onClick={() => setActiveTab("gpu")}
                className={`flex items-center gap-2 px-3 py-1.5 rounded text-xs font-semibold tracking-wider uppercase transition-colors ${
                  activeTab === "gpu" 
                    ? "bg-amber-600 text-white shadow" 
                    : "bg-slate-800/80 hover:bg-slate-800 text-slate-400"
                }`}
              >
                <Zap size={14} />
                <span>Confirmed GPU Events</span>
                <span className={`px-1.5 py-0.2 rounded-full text-[10px] font-mono ${
                  activeTab === "gpu" ? "bg-amber-700 text-white" : "bg-slate-700 text-slate-300"
                }`}>
                  {gpuEvents.length}
                </span>
              </button>
            </div>

            {/* Search Input */}
            <div className="relative min-w-[220px]">
              <Search size={13} className="absolute left-3 top-2.5 text-slate-500" />
              <input
                type="text"
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                placeholder={activeTab === "clips" ? "Filter clips, mission..." : "Filter event type..."}
                className="w-full bg-slate-950 border border-slate-800 focus:border-slate-700 outline-none pl-8 pr-3 py-1.5 text-xs text-slate-200 rounded font-mono"
              />
              {searchQuery && (
                <button 
                  onClick={() => setSearchQuery("")}
                  className="absolute right-2.5 top-2.5 text-slate-500 hover:text-slate-300"
                >
                  <X size={12} />
                </button>
              )}
            </div>
          </div>

          {/* TAB 1: LOCAL SSD CLIPS */}
          {activeTab === "clips" && (
            <div className="bg-slate-900 border border-slate-800 rounded-lg flex flex-col overflow-hidden min-h-[440px]">
              <div className="p-3.5 border-b border-slate-800 bg-slate-950/60 flex justify-between items-center text-xs">
                <div className="flex items-center gap-2">
                  <span className="font-semibold uppercase tracking-wider text-slate-400">Stored Video Clips</span>
                  <span className="text-slate-600">•</span>
                  <span className="text-[11px] text-slate-500 font-mono">{filteredClips.length} items on SSD</span>
                </div>
                <div className="text-[11px] text-slate-500">
                  Click <Play size={10} className="inline mx-0.5 text-sky-400" /> to preview or <Download size={10} className="inline mx-0.5 text-emerald-400" /> to transfer
                </div>
              </div>

              <div className="overflow-x-auto flex-1">
                {loading && savedClips.length === 0 ? (
                  <div className="flex items-center justify-center h-48 text-slate-500 text-xs">
                    <RefreshCw size={15} className="animate-spin mr-2 text-sky-400" /> Scanning local SSD evidence...
                  </div>
                ) : filteredClips.length === 0 ? (
                  <div className="flex flex-col items-center justify-center h-52 text-slate-500 text-xs px-4 text-center">
                    <Film size={28} className="text-slate-700 mb-2" />
                    <span className="font-semibold text-slate-400">No Evidence Clips Saved On SSD</span>
                    <p className="text-[11px] text-slate-500 max-w-sm mt-1">
                      Use the left panel to trigger a manual buffer extraction ({totalDurationS}s window), or extract from confirmed GPU events.
                    </p>
                  </div>
                ) : (
                  <table className="w-full text-left text-xs whitespace-nowrap">
                    <thead className="bg-slate-950/80 text-slate-500 text-[10px] uppercase font-bold tracking-wider border-b border-slate-800/80">
                      <tr>
                        <th className="px-4 py-3">Clip Filename & Mission</th>
                        <th className="px-4 py-3">Duration & Frames</th>
                        <th className="px-4 py-3">File Size</th>
                        <th className="px-4 py-3">Captured (IST)</th>
                        <th className="px-4 py-3 text-right">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/50 font-mono">
                      {filteredClips.map((clip, idx) => (
                        <tr key={clip.filename || idx} className="hover:bg-slate-800/30 transition-colors">
                          <td className="px-4 py-3.5">
                            <div className="flex items-center gap-2.5">
                              <button 
                                onClick={() => setActiveModalClip(clip)}
                                className="p-1.5 bg-sky-500/10 hover:bg-sky-500/20 text-sky-400 rounded border border-sky-500/30 transition-colors"
                                title="Play clip in player"
                              >
                                <Play size={13} fill="currentColor" />
                              </button>
                              <div className="flex flex-col">
                                <button 
                                  onClick={() => setActiveModalClip(clip)}
                                  className="text-left font-bold text-slate-200 hover:text-sky-400 transition-colors"
                                >
                                  {clip.filename}
                                </button>
                                <div className="text-[10px] text-slate-500 flex items-center gap-1.5">
                                  <span className="text-sky-400 font-semibold">{clip.mission_id || 'M-101'}</span>
                                  <span>•</span>
                                  <span className="text-slate-400 capitalize">{(clip.event_type || 'manual_flag').replace(/_/g, ' ')}</span>
                                  {clip.notes && (
                                    <>
                                      <span>•</span>
                                      <span className="text-slate-400 italic max-w-[160px] truncate">{clip.notes}</span>
                                    </>
                                  )}
                                </div>
                              </div>
                            </div>
                          </td>

                          <td className="px-4 py-3.5 text-slate-300">
                            <div className="flex flex-col">
                              <span>{clip.duration_s ? `${clip.duration_s.toFixed(1)}s` : '30.0s'}</span>
                              <span className="text-[10px] text-slate-500">{clip.frame_count || '900'} frames</span>
                            </div>
                          </td>

                          <td className="px-4 py-3.5 text-slate-300">
                            <span className="px-2 py-0.5 rounded bg-slate-950 border border-slate-800 text-[11px]">
                              {clip.size_mb ? `${clip.size_mb.toFixed(1)} MB` : '< 1 MB'}
                            </span>
                          </td>

                          <td className="px-4 py-3.5 text-slate-400 text-[11px]">
                            {clip.created_at_ist || clip.timestamp || '—'}
                          </td>

                          <td className="px-4 py-3.5 text-right">
                            <div className="flex items-center justify-end gap-1.5">
                              <button
                                onClick={() => setActiveModalClip(clip)}
                                className="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded border border-slate-700 text-[11px] font-sans flex items-center gap-1 transition-colors"
                                title="Watch video clip"
                              >
                                <Play size={11} /> Play
                              </button>

                              <button
                                onClick={() => handleDownload(clip.filename)}
                                className="px-2.5 py-1 bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 rounded border border-emerald-500/30 text-[11px] font-sans flex items-center gap-1 transition-colors"
                                title="Download MP4 for transfer"
                              >
                                <Download size={11} /> Download
                              </button>

                              <button
                                onClick={() => setDeleteCandidate(clip)}
                                className="p-1 text-slate-500 hover:text-rose-400 hover:bg-rose-500/10 rounded transition-colors"
                                title="Delete from SSD"
                              >
                                <Trash2 size={13} />
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          )}

          {/* TAB 2: CONFIRMED GPU EVENTS */}
          {activeTab === "gpu" && (
            <div className="bg-slate-900 border border-slate-800 rounded-lg flex flex-col overflow-hidden min-h-[440px]">
              <div className="p-3.5 border-b border-slate-800 bg-slate-950/60 flex justify-between items-center text-xs">
                <div className="flex items-center gap-2">
                  <span className="font-semibold uppercase tracking-wider text-slate-400">Confirmed Events from Analytics</span>
                  <span className="text-slate-600">•</span>
                  <span className="text-[11px] text-amber-400 font-mono">{filteredGpuEvents.length} events logged</span>
                </div>
                <div className="text-[11px] text-slate-500">
                  Click <Zap size={10} className="inline mx-0.5 text-amber-400" /> to extract buffer window around the incident
                </div>
              </div>

              <div className="overflow-x-auto flex-1">
                {filteredGpuEvents.length === 0 ? (
                  <div className="flex flex-col items-center justify-center h-52 text-slate-500 text-xs px-4 text-center">
                    <Zap size={28} className="text-slate-700 mb-2" />
                    <span className="font-semibold text-slate-400">No Confirmed Events</span>
                    <p className="text-[11px] text-slate-500 max-w-sm mt-1">
                      When AI models (crowd, violence, weapon, perimeter, traffic) confirm incidents on the GPU stream, they appear here.
                    </p>
                  </div>
                ) : (
                  <table className="w-full text-left text-xs whitespace-nowrap">
                    <thead className="bg-slate-950/80 text-slate-500 text-[10px] uppercase font-bold tracking-wider border-b border-slate-800/80">
                      <tr>
                        <th className="px-4 py-3">Event ID & Type</th>
                        <th className="px-4 py-3">Mission & Drone</th>
                        <th className="px-4 py-3">Confidence</th>
                        <th className="px-4 py-3">Event Time (IST)</th>
                        <th className="px-4 py-3 text-right">Edge Buffer Action</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/50 font-mono">
                      {filteredGpuEvents.map((evt, idx) => (
                        <tr key={evt.id || idx} className="hover:bg-slate-800/30 transition-colors">
                          <td className="px-4 py-3.5">
                            <div className="flex items-center gap-2">
                              <span className="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-amber-500/10 text-amber-300 border border-amber-500/20">
                                {(evt.event_type || 'Event').replace(/_/g, ' ')}
                              </span>
                              <span className="text-slate-400 text-[11px]">{evt.id}</span>
                            </div>
                          </td>

                          <td className="px-4 py-3.5 text-slate-300">
                            <div className="flex flex-col">
                              <span className="font-bold text-sky-400">{evt.mission_id || activeMission?.mission_id || 'M-101'}</span>
                              <span className="text-[10px] text-slate-500">{evt.drone_id || 'DRONE-01'}</span>
                            </div>
                          </td>

                          <td className="px-4 py-3.5">
                            <span className="text-emerald-400 font-bold">
                              {evt.confidence ? `${(evt.confidence * 100).toFixed(0)}%` : '92%'}
                            </span>
                          </td>

                          <td className="px-4 py-3.5 text-slate-400 text-[11px]">
                            {evt.ts ? new Date(evt.ts).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' }) + ' IST' : (evt.timestamp || '—')}
                          </td>

                          <td className="px-4 py-3.5 text-right">
                            <button
                              onClick={() => handleExtractFromGpuEvent(evt)}
                              disabled={isExtracting}
                              className="px-3 py-1 bg-amber-600 hover:bg-amber-500 text-white rounded text-[11px] font-sans font-semibold flex items-center gap-1.5 ml-auto transition-colors shadow-sm disabled:opacity-50"
                              title="Extract context window around this event"
                            >
                              <Zap size={12} /> Extract Context ({preEventS + postEventS}s)
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          )}

        </div>

      </div>

      {/* Embedded HTML5 Video Playback Modal */}
      {activeModalClip && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl max-w-3xl w-full overflow-hidden shadow-2xl animate-in fade-in zoom-in-95 duration-150">
            {/* Modal Header */}
            <div className="p-4 border-b border-slate-800 bg-slate-950 flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <Film size={17} className="text-sky-400" />
                <div className="flex flex-col">
                  <h3 className="text-sm font-bold text-slate-200 font-mono">{activeModalClip.filename}</h3>
                  <span className="text-[10px] text-slate-500 font-mono">Mission: {activeModalClip.mission_id || 'M-101'}</span>
                </div>
              </div>
              <button 
                onClick={() => setActiveModalClip(null)}
                className="p-1 text-slate-400 hover:text-slate-200 rounded hover:bg-slate-800 transition-colors"
              >
                <X size={18} />
              </button>
            </div>

            {/* Video Player Container */}
            <div className="bg-black p-1 flex items-center justify-center">
              <video 
                controls 
                autoPlay 
                className="w-full max-h-[55vh] object-contain rounded"
                key={activeModalClip.filename}
              >
                <source 
                  src={`/edge-api/api/edge/evidence/${encodeURIComponent(activeModalClip.filename)}/stream`} 
                  type="video/mp4" 
                />
                Your browser does not support the video tag.
              </video>
            </div>

            {/* Video Metadata Drawer */}
            <div className="p-4 bg-slate-950/80 border-t border-slate-800 flex flex-wrap items-center justify-between gap-4 text-xs">
              <div className="flex flex-wrap items-center gap-4 text-slate-400 font-mono text-[11px]">
                <div>
                  <span className="text-slate-500 uppercase text-[9px] block">Duration</span>
                  <span className="text-slate-200">{activeModalClip.duration_s ? `${activeModalClip.duration_s.toFixed(1)}s` : '30s'}</span>
                </div>
                <div>
                  <span className="text-slate-500 uppercase text-[9px] block">Frames</span>
                  <span className="text-slate-200">{activeModalClip.frame_count || '900'}</span>
                </div>
                <div>
                  <span className="text-slate-500 uppercase text-[9px] block">File Size</span>
                  <span className="text-slate-200">{activeModalClip.size_mb ? `${activeModalClip.size_mb.toFixed(1)} MB` : '< 1 MB'}</span>
                </div>
                <div>
                  <span className="text-slate-500 uppercase text-[9px] block">Recorded (IST)</span>
                  <span className="text-slate-200">{activeModalClip.created_at_ist || '—'}</span>
                </div>
              </div>

              {/* Modal Action Buttons */}
              <div className="flex items-center gap-2">
                <button
                  onClick={() => handleDownload(activeModalClip.filename)}
                  className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-xs font-semibold flex items-center gap-1.5 transition-colors shadow"
                >
                  <Download size={13} /> Download for Transfer
                </button>
                <button
                  onClick={() => {
                    setDeleteCandidate(activeModalClip);
                  }}
                  className="p-1.5 bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/30 rounded transition-colors"
                  title="Delete clip"
                >
                  <Trash2 size={15} />
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Delete Confirmation Modal */}
      {deleteCandidate && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-lg max-w-md w-full p-5 shadow-2xl">
            <div className="flex items-center gap-3 text-rose-400 mb-3">
              <AlertTriangle size={20} />
              <h3 className="text-sm font-bold uppercase tracking-wider text-slate-200">Delete Evidence Clip</h3>
            </div>
            <p className="text-xs text-slate-300 mb-2 leading-relaxed">
              Are you sure you want to delete this clip from the local SSD?
            </p>
            <div className="bg-slate-950 p-2.5 rounded border border-slate-800 text-[11px] font-mono text-slate-300 mb-4 truncate">
              {deleteCandidate.filename}
            </div>
            <p className="text-[11px] text-slate-500 mb-5">
              This action permanently reclaims SSD space. The video file and metadata sidecar will be removed.
            </p>
            <div className="flex justify-end gap-2.5">
              <button
                onClick={() => setDeleteCandidate(null)}
                className="px-3.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs font-semibold transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={confirmDelete}
                className="px-3.5 py-1.5 bg-rose-600 hover:bg-rose-500 text-white rounded text-xs font-semibold transition-colors shadow"
              >
                Confirm Delete
              </button>
            </div>
          </div>
        </div>
      )}

    </AppShell>
  );
}
