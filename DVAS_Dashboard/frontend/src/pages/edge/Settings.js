import React, { useState, useEffect } from 'react';
import AppShell from '@/components/layout/AppShell';
import { 
  Radio, 
  HardDrive, 
  Activity, 
  CheckCircle2, 
  AlertCircle, 
  AlertTriangle, 
  RefreshCw, 
  Trash2, 
  Save, 
  RotateCcw, 
  Server, 
  Clock, 
  Cpu, 
  Sliders, 
  ShieldCheck, 
  Check, 
  Zap, 
  Filter 
} from 'lucide-react';
import { authFetch } from '@/lib/api';

export default function Settings() {
  const [activeTab, setActiveTab] = useState('transmission'); // 'transmission' | 'storage' | 'diagnostics'
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [toastMsg, setToastMsg] = useState('');
  const [toastType, setToastType] = useState('success'); // 'success' | 'error'

  // Settings state
  const [settings, setSettings] = useState({
    transmission: {
      ingest_base_url: '',
      bypass_target_fps: 10,
      jpeg_quality: 80,
      max_resolution: '1080p',
      concurrency_semaphore: 10,
    },
    storage: {
      auto_cleanup_uploads: true,
      max_upload_size_mb: 2048,
    },
    operator_preferences: {
      default_squadron: 'ALPHA',
      session_timeout_minutes: 60,
    }
  });

  // Disk usage state
  const [diskUsage, setDiskUsage] = useState({
    total_gb: 0,
    used_gb: 0,
    free_gb: 0,
    used_percent: 0,
    temp_files_count: 0,
    temp_media_mb: 0,
    status: 'NOMINAL',
    mount_point: '/data'
  });

  // Connection test state
  const [testResult, setTestResult] = useState(null);
  const [testingConnection, setTestingConnection] = useState(false);

  // Storage purge state
  const [purging, setPurging] = useState(false);
  const [purgeMsg, setPurgeMsg] = useState('');

  // Diagnostics & logs state
  const [systemHealth, setSystemHealth] = useState(null);
  const [systemLogs, setSystemLogs] = useState([]);
  const [logFilter, setLogFilter] = useState('ALL');
  const [refreshingLogs, setRefreshingLogs] = useState(false);

  const showToast = (msg, type = 'success') => {
    setToastMsg(msg);
    setToastType(type);
    setTimeout(() => setToastMsg(''), 4000);
  };

  // ── Load Settings & Storage on Mount ──────────────────────────────────────
  const fetchSettings = async () => {
    try {
      const resp = await authFetch('/edge-api/api/edge/settings');
      if (resp.ok) {
        const data = await resp.json();
        setSettings(prev => ({
          ...prev,
          ...data,
          transmission: {
            ...prev.transmission,
            ...(data.transmission || {}),
            ingest_base_url: data.transmission?.ingest_base_url || data.transmission?.ingest_url || '',
          },
          storage: { ...prev.storage, ...(data.storage || {}) },
          operator_preferences: { ...prev.operator_preferences, ...(data.operator_preferences || {}) }
        }));
        if (data?.operator_preferences?.default_squadron) {
          localStorage.setItem('dvas_default_squadron', data.operator_preferences.default_squadron);
        }
      }
    } catch (err) {
      console.error('Failed to fetch settings:', err);
    }
  };

  const fetchDiskUsage = async () => {
    try {
      const resp = await authFetch('/edge-api/api/edge/storage/disk-usage');
      if (resp.ok) {
        const data = await resp.json();
        setDiskUsage(data);
      }
    } catch (err) {
      console.error('Failed to fetch disk usage:', err);
    }
  };

  const fetchDiagnostics = async () => {
    try {
      const resp = await authFetch('/edge-api/api/edge/system-health');
      if (resp.ok) {
        const data = await resp.json();
        setSystemHealth(data);
      }
    } catch (err) {
      console.error('Failed to fetch diagnostics:', err);
    }
  };

  const fetchLogs = async () => {
    setRefreshingLogs(true);
    try {
      const resp = await authFetch(`/edge-api/api/edge/system-logs?limit=80&level=${logFilter}`);
      if (resp.ok) {
        const data = await resp.json();
        setSystemLogs(data);
      }
    } catch (err) {
      console.error('Failed to fetch logs:', err);
    } finally {
      setRefreshingLogs(false);
    }
  };

  useEffect(() => {
    const init = async () => {
      setLoading(true);
      await Promise.all([fetchSettings(), fetchDiskUsage(), fetchDiagnostics(), fetchLogs()]);
      setLoading(false);
    };
    init();
  }, []);

  // Re-fetch logs when filter changes
  useEffect(() => {
    fetchLogs();
  }, [logFilter]);

  // Periodic health polling when on Diagnostics tab
  useEffect(() => {
    if (activeTab === 'diagnostics') {
      const interval = setInterval(() => {
        fetchDiagnostics();
      }, 5000);
      return () => clearInterval(interval);
    }
  }, [activeTab]);

  // ── Save Settings ─────────────────────────────────────────────────────────
  const handleSaveSettings = async () => {
    setSaving(true);
    try {
      const resp = await authFetch('/edge-api/api/edge/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings),
      });
      if (resp.ok) {
        showToast('Settings successfully updated and applied to edge node.', 'success');
        if (settings?.operator_preferences?.default_squadron) {
          localStorage.setItem('dvas_default_squadron', settings.operator_preferences.default_squadron);
        }
        fetchDiskUsage();
      } else if (resp.status === 422) {
        const err = await resp.json().catch(() => ({}));
        showToast(typeof err.detail === 'string' ? err.detail : 'Invalid settings value.', 'error');
      } else {
        showToast('Failed to save settings. Please check server logs.', 'error');
      }
    } catch (err) {
      showToast('Network error while saving settings.', 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleResetDefaults = () => {
    setSettings({
      transmission: {
        ingest_base_url: settings.transmission.ingest_base_url,   // reset never clears the server address
        bypass_target_fps: 10,
        jpeg_quality: 80,
        max_resolution: '1080p',
        concurrency_semaphore: 10,
      },
      storage: {
        auto_cleanup_uploads: true,
        max_upload_size_mb: 2048,
      },
      operator_preferences: {
        default_squadron: 'ALPHA',
        session_timeout_minutes: 60,
      }
    });
    showToast('Form reset to default parameters (Click Save to commit).', 'success');
  };

  // ── Test Ingest Connection ────────────────────────────────────────────────
  const handleTestConnection = async () => {
    setTestingConnection(true);
    setTestResult(null);
    try {
      const resp = await authFetch('/edge-api/api/edge/transmission/test-connection', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: settings.transmission.ingest_base_url }),
      });
      if (resp.ok) {
        const data = await resp.json();
        setTestResult(data);
      } else {
        setTestResult({ status: 'unreachable', error: 'HTTP error ' + resp.status });
      }
    } catch (err) {
      setTestResult({ status: 'unreachable', error: err.message || 'Connection failed' });
    } finally {
      setTestingConnection(false);
    }
  };

  // ── Purge Inactive Temp Files ─────────────────────────────────────────────
  const handlePurgeTempFiles = async () => {
    if (!window.confirm('Are you sure you want to delete all inactive uploaded videos and .SRT files from /data? This cannot be undone.')) {
      return;
    }
    setPurging(true);
    setPurgeMsg('');
    try {
      const resp = await authFetch('/edge-api/api/edge/storage/purge-temp', {
        method: 'POST',
      });
      if (resp.ok) {
        const data = await resp.json();
        setPurgeMsg(`Purged ${data.deleted_count} files (${data.reclaimed_mb} MB reclaimed)`);
        showToast(`Reclaimed ${data.reclaimed_mb} MB of storage space.`, 'success');
        fetchDiskUsage();
      } else {
        showToast('Purge failed.', 'error');
      }
    } catch (err) {
      showToast('Network error during purge.', 'error');
    } finally {
      setPurging(false);
    }
  };

  return (
    <AppShell title="Station Settings" subtitle="DVAS Edge Node Configuration & Diagnostics">
      {/* Toast Notification */}
      {toastMsg && (
        <div className={`fixed top-4 right-4 z-50 flex items-center gap-2 px-4 py-3 rounded border shadow-xl text-xs font-semibold tracking-wide transition-all ${
          toastType === 'success' 
            ? 'bg-emerald-950/90 text-emerald-200 border-emerald-600/60 shadow-emerald-950/50' 
            : 'bg-red-950/90 text-red-200 border-red-600/60 shadow-red-950/50'
        }`}>
          {toastType === 'success' ? <CheckCircle2 size={16} className="text-emerald-400" /> : <AlertCircle size={16} className="text-red-400" />}
          <span>{toastMsg}</span>
        </div>
      )}

      {/* Main Layout */}
      <div className="space-y-5">
        {/* Navigation Tabs */}
        <div className="flex border-b border-slate-800 bg-slate-900/60 p-1.5 rounded-lg gap-2">
          <button
            onClick={() => setActiveTab('transmission')}
            className={`flex items-center gap-2 px-4 py-2.5 rounded-md text-xs font-semibold uppercase tracking-wider transition-all ${
              activeTab === 'transmission'
                ? 'bg-sky-600 text-white shadow-md shadow-sky-600/20 border border-sky-500/30'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
            }`}
          >
            <Radio size={15} />
            <span>Ingest & Streaming</span>
          </button>

          <button
            onClick={() => setActiveTab('storage')}
            className={`flex items-center gap-2 px-4 py-2.5 rounded-md text-xs font-semibold uppercase tracking-wider transition-all ${
              activeTab === 'storage'
                ? 'bg-sky-600 text-white shadow-md shadow-sky-600/20 border border-sky-500/30'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
            }`}
          >
            <HardDrive size={15} />
            <span>Storage & File Limits</span>
          </button>

          <button
            onClick={() => setActiveTab('diagnostics')}
            className={`flex items-center gap-2 px-4 py-2.5 rounded-md text-xs font-semibold uppercase tracking-wider transition-all ${
              activeTab === 'diagnostics'
                ? 'bg-sky-600 text-white shadow-md shadow-sky-600/20 border border-sky-500/30'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
            }`}
          >
            <Activity size={15} />
            <span>System Diagnostics & Logs</span>
          </button>
        </div>

        {/* ── TAB 1: INGEST & TRANSMISSION SETTINGS ────────────────────────── */}
        {activeTab === 'transmission' && (
          <div className="space-y-5">
            {/* Ingest Server Configuration Card */}
            <div className="bg-slate-900 border border-slate-800 rounded-lg p-5">
              <div className="flex items-center justify-between border-b border-slate-800 pb-3 mb-4">
                <div className="flex items-center gap-2">
                  <Server size={18} className="text-sky-400" />
                  <h3 className="text-sm font-semibold uppercase tracking-wider text-slate-200">
                    Central Ingest Endpoint
                  </h3>
                </div>
                <span className="text-[10px] font-mono bg-sky-500/10 text-sky-400 border border-sky-500/20 px-2 py-0.5 rounded">
                  HTTP REST / Multipart Ingest
                </span>
              </div>

              <div className="space-y-4">
                <div>
                  <label className="block text-[11px] font-semibold uppercase tracking-wider text-slate-400 mb-1.5">
                    Ingest Server URL (RunPod)
                  </label>
                  <div className="flex gap-2">
                    <input
                      type="text"
                      value={settings.transmission.ingest_base_url}
                      onChange={e => setSettings({
                        ...settings,
                        transmission: { ...settings.transmission, ingest_base_url: e.target.value }
                      })}
                      className="flex-1 bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-xs font-mono text-slate-200 rounded"
                      placeholder="https://<pod>-80.proxy.runpod.net"
                    />
                    <button
                      onClick={handleTestConnection}
                      disabled={testingConnection}
                      className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-600 rounded text-xs font-semibold flex items-center gap-1.5 transition-colors"
                    >
                      {testingConnection ? <RefreshCw size={14} className="animate-spin text-sky-400" /> : <Zap size={14} className="text-amber-400" />}
                      Test Connection
                    </button>
                  </div>
                  <p className="text-[10px] text-slate-500 mt-1">
                    Server address only. Frames, missions, login and live video all use it. Applies from the next mission.
                  </p>
                </div>

                {/* Connection Test Result Feedback */}
                {testResult && (
                  <div className={`p-3 rounded border text-xs flex items-center justify-between font-mono ${
                    testResult.status === 'online'
                      ? 'bg-emerald-950/40 border-emerald-700/50 text-emerald-300'
                      : 'bg-red-950/40 border-red-700/50 text-red-300'
                  }`}>
                    <div className="flex items-center gap-2">
                      {testResult.status === 'online' ? (
                        <CheckCircle2 size={16} className="text-emerald-400" />
                      ) : (
                        <AlertCircle size={16} className="text-red-400" />
                      )}
                      <span>Target: {testResult.target}</span>
                    </div>
                    <div>
                      {testResult.status === 'online' ? (
                        <span className="font-bold">STATUS: ONLINE ({testResult.latency_ms} ms) • HTTP {testResult.http_status}</span>
                      ) : (
                        <span className="font-bold">STATUS: UNREACHABLE ({testResult.error || 'Timed out'})</span>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Frame Rate & Pipeline Bypass Tuning Card */}
            <div className="bg-slate-900 border border-slate-800 rounded-lg p-5">
              <div className="flex items-center justify-between border-b border-slate-800 pb-3 mb-4">
                <div className="flex items-center gap-2">
                  <Sliders size={18} className="text-emerald-400" />
                  <h3 className="text-sm font-semibold uppercase tracking-wider text-slate-200">
                    Bypass Frame Rate & Quality
                  </h3>
                </div>
                <span className="text-[10px] font-mono bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 px-2 py-0.5 rounded">
                  Active in Pipeline Bypass Mode
                </span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {/* Target Bypass FPS Selector */}
                <div>
                  <label className="block text-[11px] font-semibold uppercase tracking-wider text-slate-400 mb-2">
                    Bypass Target Frame Rate (FPS)
                  </label>
                  <div className="grid grid-cols-4 gap-2">
                    {[
                      { fps: 5, label: '5 FPS', desc: 'Low Bandwidth' },
                      { fps: 10, label: '10 FPS', desc: 'Standard (Default)' },
                      { fps: 15, label: '15 FPS', desc: 'High Rate' },
                      { fps: 30, label: '30 FPS', desc: 'Full Rate' },
                    ].map(opt => (
                      <button
                        key={opt.fps}
                        type="button"
                        onClick={() => setSettings({
                          ...settings,
                          transmission: { ...settings.transmission, bypass_target_fps: opt.fps }
                        })}
                        className={`p-3 rounded border text-center transition-all ${
                          settings.transmission.bypass_target_fps === opt.fps
                            ? 'bg-emerald-600/20 border-emerald-500 text-emerald-300 font-bold shadow-lg shadow-emerald-950/50'
                            : 'bg-slate-950 border-slate-800 text-slate-400 hover:border-slate-700'
                        }`}
                      >
                        <div className="text-sm font-mono">{opt.label}</div>
                        <div className="text-[9px] text-slate-500 mt-0.5">{opt.desc}</div>
                      </button>
                    ))}
                  </div>
                  <p className="text-[10px] text-slate-500 mt-2">
                    Determines frame sampling interval from a 30 FPS video source when cognitive filtering is bypassed.
                  </p>
                </div>

                {/* JPEG Quality Slider */}
                <div>
                  <div className="flex justify-between items-center mb-2">
                    <label className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                      JPEG Compression Quality
                    </label>
                    <span className="text-xs font-mono font-bold text-emerald-400 bg-emerald-950/60 px-2 py-0.5 rounded border border-emerald-700/40">
                      {settings.transmission.jpeg_quality}%
                    </span>
                  </div>
                  <input
                    type="range"
                    min="50"
                    max="95"
                    step="5"
                    value={settings.transmission.jpeg_quality}
                    onChange={e => setSettings({
                      ...settings,
                      transmission: { ...settings.transmission, jpeg_quality: parseInt(e.target.value) }
                    })}
                    className="w-full accent-emerald-500 h-2 bg-slate-950 rounded cursor-pointer"
                  />
                  <div className="flex justify-between text-[10px] text-slate-500 mt-1 font-mono">
                    <span>50% (Compact ~70 KB)</span>
                    <span>80% (Recommended)</span>
                    <span>95% (Fine Detail ~350 KB)</span>
                  </div>
                  <p className="text-[10px] text-slate-500 mt-2">
                    Balancing image compression against network uplink capacity. 80% is optimal for YOLOv8 intrusion detection.
                  </p>
                </div>

                {/* Downscale Resolution */}
                <div>
                  <label className="block text-[11px] font-semibold uppercase tracking-wider text-slate-400 mb-2">
                    Max Transmit Resolution
                  </label>
                  <select
                    value={settings.transmission.max_resolution}
                    onChange={e => setSettings({
                      ...settings,
                      transmission: { ...settings.transmission, max_resolution: e.target.value }
                    })}
                    className="w-full bg-slate-950 border border-slate-700 focus:border-emerald-500 outline-none px-3 py-2 text-xs text-slate-200 rounded font-mono"
                  >
                    <option value="1080p">1080p (Downscale &gt; 1920x1080) — Recommended</option>
                    <option value="720p">720p (Low Bandwidth Downscale to 1280x720)</option>
                    <option value="original">Original (Passthrough Full Sensor Resolution)</option>
                  </select>
                  <p className="text-[10px] text-slate-500 mt-1">
                    Prevents huge 4K drone streams from saturating field wireless uplink links.
                  </p>
                </div>

                {/* Concurrency Semaphore */}
                <div>
                  <label className="block text-[11px] font-semibold uppercase tracking-wider text-slate-400 mb-2">
                    Concurrent Upload Workers
                  </label>
                  <select
                    value={settings.transmission.concurrency_semaphore}
                    onChange={e => setSettings({
                      ...settings,
                      transmission: { ...settings.transmission, concurrency_semaphore: parseInt(e.target.value) }
                    })}
                    className="w-full bg-slate-950 border border-slate-700 focus:border-emerald-500 outline-none px-3 py-2 text-xs text-slate-200 rounded font-mono"
                  >
                    <option value="5">5 Workers (Low CPU / Restrained Link)</option>
                    <option value="10">10 Workers (Standard — 10 FPS Transmission)</option>
                    <option value="15">15 Workers (High Throughput / Gigabit LAN)</option>
                  </select>
                  <p className="text-[10px] text-slate-500 mt-1">
                    Async HTTP pipeline pool size for transmitting frame chunks to central ingest.
                  </p>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ── TAB 2: STORAGE & FILE MANAGEMENT ────────────────────────────── */}
        {activeTab === 'storage' && (
          <div className="space-y-5">
            {/* Visual Storage Gauge Card */}
            <div className="bg-slate-900 border border-slate-800 rounded-lg p-5">
              <div className="flex items-center justify-between border-b border-slate-800 pb-3 mb-4">
                <div className="flex items-center gap-2">
                  <HardDrive size={18} className="text-amber-400" />
                  <h3 className="text-sm font-semibold uppercase tracking-wider text-slate-200">
                    Disk Usage Gauge ({diskUsage.mount_point})
                  </h3>
                </div>
                <div className="flex items-center gap-2">
                  <span className={`text-[10px] font-mono px-2 py-0.5 rounded font-bold border ${
                    diskUsage.status === 'NOMINAL'
                      ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
                      : diskUsage.status === 'WARNING'
                      ? 'bg-amber-500/20 text-amber-400 border-amber-500/30'
                      : 'bg-red-500/20 text-red-400 border-red-500/30'
                  }`}>
                    {diskUsage.status}
                  </span>
                  <button
                    onClick={fetchDiskUsage}
                    className="p-1 text-slate-400 hover:text-slate-200 rounded hover:bg-slate-800 transition-colors"
                    title="Refresh Disk Stats"
                  >
                    <RefreshCw size={14} />
                  </button>
                </div>
              </div>

              {/* Graphical Storage Bar */}
              <div className="space-y-2 mb-6">
                <div className="flex justify-between text-xs font-mono">
                  <span className="text-slate-400">Used: <strong className="text-slate-200">{diskUsage.used_gb} GB</strong> ({diskUsage.used_percent}%)</span>
                  <span className="text-slate-400">Available: <strong className="text-emerald-400">{diskUsage.free_gb} GB</strong> / {diskUsage.total_gb} GB</span>
                </div>
                <div className="w-full bg-slate-950 rounded-full h-3.5 border border-slate-800 p-0.5 overflow-hidden">
                  <div 
                    className={`h-full rounded-full transition-all duration-500 ${
                      diskUsage.used_percent >= 90 
                        ? 'bg-gradient-to-r from-red-600 to-red-400' 
                        : diskUsage.used_percent >= 75 
                        ? 'bg-gradient-to-r from-amber-600 to-amber-400' 
                        : 'bg-gradient-to-r from-emerald-600 to-emerald-400'
                    }`}
                    style={{ width: `${Math.min(100, diskUsage.used_percent)}%` }}
                  />
                </div>
              </div>

              {/* Breakdown metrics */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div className="bg-slate-950/80 p-3 rounded border border-slate-800 text-center">
                  <div className="text-[10px] text-slate-500 uppercase font-semibold">Total Capacity</div>
                  <div className="text-lg font-mono font-bold text-slate-200 mt-0.5">{diskUsage.total_gb} GB</div>
                </div>
                <div className="bg-slate-950/80 p-3 rounded border border-slate-800 text-center">
                  <div className="text-[10px] text-slate-500 uppercase font-semibold">Free Space</div>
                  <div className="text-lg font-mono font-bold text-emerald-400 mt-0.5">{diskUsage.free_gb} GB</div>
                </div>
                <div className="bg-slate-950/80 p-3 rounded border border-slate-800 text-center">
                  <div className="text-[10px] text-slate-500 uppercase font-semibold">Used Space</div>
                  <div className="text-lg font-mono font-bold text-slate-200 mt-0.5">{diskUsage.used_gb} GB</div>
                </div>
                <div className="bg-slate-950/80 p-3 rounded border border-slate-800 text-center">
                  <div className="text-[10px] text-slate-500 uppercase font-semibold">Inactive Media Cache</div>
                  <div className="text-lg font-mono font-bold text-amber-400 mt-0.5">{diskUsage.temp_media_mb} MB</div>
                </div>
              </div>
            </div>

            {/* Storage Policies & Upload Guards */}
            <div className="bg-slate-900 border border-slate-800 rounded-lg p-5">
              <div className="flex items-center justify-between border-b border-slate-800 pb-3 mb-4">
                <div className="flex items-center gap-2">
                  <ShieldCheck size={18} className="text-sky-400" />
                  <h3 className="text-sm font-semibold uppercase tracking-wider text-slate-200">
                    Upload Guardrails & Retention Policies
                  </h3>
                </div>
              </div>

              <div className="space-y-6">
                {/* Auto-cleanup toggle */}
                <div className="flex items-start justify-between p-4 bg-slate-950/70 border border-slate-800 rounded">
                  <div className="space-y-1 pr-4">
                    <div className="text-xs font-semibold text-slate-200 flex items-center gap-2">
                      <span>Auto-Cleanup Temporary Uploads</span>
                      {settings.storage.auto_cleanup_uploads && (
                        <span className="text-[9px] bg-emerald-500/20 text-emerald-400 px-1.5 py-0.2 rounded font-mono font-bold">
                          ACTIVE
                        </span>
                      )}
                    </div>
                    <p className="text-[11px] text-slate-400 leading-relaxed">
                      Automatically purge uploaded video files (<code className="text-slate-300">.mp4</code>, <code className="text-slate-300">.mov</code>, <code className="text-slate-300">.avi</code>) and sidecar <code className="text-slate-300">.SRT</code> files from the <code className="text-slate-300">/data</code> partition immediately when a mission ends.
                    </p>
                    <p className="text-[10px] text-slate-500">
                      When turned OFF, flight videos are preserved on disk for manual operator retrieval and offline auditing.
                    </p>
                  </div>
                  <label className="relative inline-flex items-center cursor-pointer flex-shrink-0 mt-1">
                    <input
                      type="checkbox"
                      checked={settings.storage.auto_cleanup_uploads}
                      onChange={e => setSettings({
                        ...settings,
                        storage: { ...settings.storage, auto_cleanup_uploads: e.target.checked }
                      })}
                      className="sr-only peer"
                    />
                    <div className="w-11 h-6 bg-slate-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-slate-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-emerald-600"></div>
                  </label>
                </div>

                {/* Max Upload Size Limit */}
                <div className="p-4 bg-slate-950/70 border border-slate-800 rounded space-y-3">
                  <div className="flex justify-between items-center">
                    <div>
                      <div className="text-xs font-semibold text-slate-200">
                        Maximum Video Upload Size Ceiling
                      </div>
                      <p className="text-[11px] text-slate-400 mt-0.5">
                        Enforces an upper limit on flight recordings uploaded in Mission Control to prevent edge disk saturation.
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <input
                        type="number"
                        min="100"
                        max="5000"
                        step="100"
                        value={settings.storage.max_upload_size_mb}
                        onChange={e => setSettings({
                          ...settings,
                          storage: { ...settings.storage, max_upload_size_mb: parseInt(e.target.value) || 2048 }
                        })}
                        className="w-24 bg-slate-900 border border-slate-700 focus:border-sky-500 outline-none px-2 py-1 text-xs font-mono text-right text-sky-400 font-bold rounded"
                      />
                      <span className="text-xs font-mono text-slate-400">MB</span>
                    </div>
                  </div>

                  {/* Preset quick buttons */}
                  <div className="flex items-center gap-2 pt-1">
                    <span className="text-[10px] text-slate-500 uppercase font-semibold">Quick Presets:</span>
                    {[
                      { mb: 500, label: '500 MB' },
                      { mb: 1024, label: '1.0 GB' },
                      { mb: 2048, label: '2.0 GB (Recommended)' },
                      { mb: 4096, label: '4.0 GB' },
                    ].map(preset => (
                      <button
                        key={preset.mb}
                        type="button"
                        onClick={() => setSettings({
                          ...settings,
                          storage: { ...settings.storage, max_upload_size_mb: preset.mb }
                        })}
                        className={`px-2.5 py-1 rounded text-[10px] font-mono border transition-all ${
                          settings.storage.max_upload_size_mb === preset.mb
                            ? 'bg-sky-600/20 border-sky-500 text-sky-300 font-bold'
                            : 'bg-slate-900 border-slate-800 text-slate-400 hover:border-slate-700'
                        }`}
                      >
                        {preset.label}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Manual Purge Action Card */}
                <div className="p-4 bg-red-950/20 border border-red-800/40 rounded flex items-center justify-between">
                  <div className="space-y-1 pr-4">
                    <div className="text-xs font-semibold text-red-300 flex items-center gap-1.5">
                      <Trash2 size={14} className="text-red-400" />
                      <span>Purge Inactive Temporary Files Now</span>
                    </div>
                    <p className="text-[10px] text-slate-400 leading-relaxed">
                      Scans the <code className="text-slate-300">/data</code> partition and deletes all unreferenced flight video/SRT files. Files currently in use by an active mission are preserved.
                    </p>
                    {purgeMsg && (
                      <p className="text-[11px] font-mono text-emerald-400 font-bold mt-1">
                        ✓ {purgeMsg}
                      </p>
                    )}
                  </div>
                  <button
                    onClick={handlePurgeTempFiles}
                    disabled={purging}
                    className="px-4 py-2 bg-red-900/40 hover:bg-red-900/70 text-red-200 border border-red-700/60 rounded text-xs font-semibold flex items-center gap-1.5 transition-colors flex-shrink-0"
                  >
                    {purging ? <RefreshCw size={14} className="animate-spin text-red-400" /> : <Trash2 size={14} />}
                    Purge Temp Files
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ── TAB 3: SYSTEM DIAGNOSTICS & OPERATOR MANAGEMENT ────────────── */}
        {activeTab === 'diagnostics' && (
          <div className="space-y-5">
            {/* System Health Overview */}
            <div className="bg-slate-900 border border-slate-800 rounded-lg p-5">
              <div className="flex items-center justify-between border-b border-slate-800 pb-3 mb-4">
                <div className="flex items-center gap-2">
                  <Activity size={18} className="text-emerald-400" />
                  <h3 className="text-sm font-semibold uppercase tracking-wider text-slate-200">
                    Edge Station Hardware Metrics
                  </h3>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-[10px] text-slate-400 font-mono">
                    Uptime: <strong className="text-slate-200">{systemHealth?.uptime_str || '--'}</strong>
                  </span>
                  <span className="text-[10px] font-mono bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 px-2 py-0.5 rounded">
                    NODE: {systemHealth?.node_health || 'HEALTHY'}
                  </span>
                </div>
              </div>

              {/* Hardware gauges grid */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
                <div className="bg-slate-950 p-3 rounded border border-slate-800 text-center">
                  <div className="text-[10px] text-slate-500 uppercase font-semibold">CPU Utilization</div>
                  <div className="text-xl font-mono font-bold text-sky-400 mt-0.5">{systemHealth?.cpu_pct ?? '--'}%</div>
                  {systemHealth?.cpu_temp && <div className="text-[9px] text-slate-500 font-mono">{systemHealth.cpu_temp}°C</div>}
                </div>
                <div className="bg-slate-950 p-3 rounded border border-slate-800 text-center">
                  <div className="text-[10px] text-slate-500 uppercase font-semibold">Memory (RAM)</div>
                  <div className="text-xl font-mono font-bold text-emerald-400 mt-0.5">{systemHealth?.memory_pct ?? '--'}%</div>
                  <div className="text-[9px] text-slate-500 font-mono">{systemHealth?.memory_used_mb ?? '--'} MB Used</div>
                </div>
                <div className="bg-slate-950 p-3 rounded border border-slate-800 text-center">
                  <div className="text-[10px] text-slate-500 uppercase font-semibold">GPU Utilization</div>
                  <div className="text-xl font-mono font-bold text-indigo-400 mt-0.5">{systemHealth?.gpu_pct ?? 0}%</div>
                  {systemHealth?.gpu_temp && <div className="text-[9px] text-slate-500 font-mono">{systemHealth.gpu_temp}°C</div>}
                </div>
                <div className="bg-slate-950 p-3 rounded border border-slate-800 text-center">
                  <div className="text-[10px] text-slate-500 uppercase font-semibold">Edge Software</div>
                  <div className="text-base font-mono font-bold text-slate-300 mt-1">{systemHealth?.software_version || 'v3.2.0'}</div>
                  <div className="text-[9px] text-emerald-400 font-mono">Build 2026-09</div>
                </div>
              </div>

              {/* Component Services Checklist */}
              <div>
                <h4 className="text-[11px] font-semibold uppercase tracking-wider text-slate-400 mb-2">
                  Subsystem Service Health
                </h4>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                  {(systemHealth?.services || [
                    { name: 'FastAPI Edge Server', status: 'OK' },
                    { name: 'Keycloak Security', status: 'OK' },
                    { name: 'Rolling Buffer', status: 'OK' },
                    { name: 'Ingest Client', status: 'OK' },
                  ]).map((svc, i) => (
                    <div key={i} className="flex items-center justify-between p-2 rounded bg-slate-950/80 border border-slate-800/80 text-xs">
                      <span className="text-slate-300 font-medium">{svc.name}</span>
                      <span className={`px-1.5 py-0.5 text-[9px] font-mono rounded font-bold ${
                        svc.status === 'OK' 
                          ? 'bg-emerald-500/20 text-emerald-400' 
                          : 'bg-amber-500/20 text-amber-400'
                      }`}>
                        {svc.status}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* Operator Session Details Card */}
            <div className="bg-slate-900 border border-slate-800 rounded-lg p-5">
              <div className="flex items-center justify-between border-b border-slate-800 pb-3 mb-4">
                <div className="flex items-center gap-2">
                  <ShieldCheck size={18} className="text-indigo-400" />
                  <h3 className="text-sm font-semibold uppercase tracking-wider text-slate-200">
                    Operator Console Profile
                  </h3>
                </div>
                <span className="text-[10px] font-mono bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 px-2 py-0.5 rounded">
                  Active Operator
                </span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div>
                  <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1">Operator Callsign</label>
                  <div className="text-sm font-mono text-slate-200 bg-slate-950 p-2.5 rounded border border-slate-800">
                    operator_001
                  </div>
                </div>
                <div>
                  <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1">Assigned Role</label>
                  <div className="text-sm font-mono text-emerald-400 bg-slate-950 p-2.5 rounded border border-slate-800">
                    drone_operator (Field Unit)
                  </div>
                </div>
                <div>
                  <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1">Default Squadron</label>
                  <select
                    value={settings.operator_preferences?.default_squadron || 'ALPHA'}
                    onChange={e => setSettings({
                      ...settings,
                      operator_preferences: { ...settings.operator_preferences, default_squadron: e.target.value }
                    })}
                    className="w-full bg-slate-950 border border-slate-700 focus:border-indigo-500 outline-none p-2.5 text-xs text-slate-200 rounded font-mono"
                  >
                    <option value="ALPHA">ALPHA SQUADRON</option>
                    <option value="BRAVO">BRAVO SQUADRON</option>
                    <option value="CHARLIE">CHARLIE SQUADRON</option>
                    <option value="DELTA">DELTA SQUADRON</option>
                  </select>
                </div>
              </div>
            </div>

            {/* Live System Log Feed */}
            <div className="bg-slate-900 border border-slate-800 rounded-lg p-5">
              <div className="flex items-center justify-between border-b border-slate-800 pb-3 mb-3">
                <div className="flex items-center gap-2">
                  <Clock size={16} className="text-slate-400" />
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-200">
                    Real-Time Edge Operational Event Stream
                  </h3>
                </div>
                <div className="flex items-center gap-2">
                  <div className="flex items-center gap-1 bg-slate-950 p-0.5 rounded border border-slate-800">
                    {['ALL', 'INFO', 'WARNING', 'ERROR'].map(lvl => (
                      <button
                        key={lvl}
                        onClick={() => setLogFilter(lvl)}
                        className={`px-2 py-0.5 text-[9px] font-mono rounded transition-colors ${
                          logFilter === lvl
                            ? 'bg-sky-600 text-white font-bold'
                            : 'text-slate-400 hover:text-slate-200'
                        }`}
                      >
                        {lvl}
                      </button>
                    ))}
                  </div>
                  <button
                    onClick={fetchLogs}
                    disabled={refreshingLogs}
                    className="p-1.5 text-slate-400 hover:text-slate-200 rounded hover:bg-slate-800 transition-colors"
                    title="Refresh logs"
                  >
                    <RefreshCw size={13} className={refreshingLogs ? 'animate-spin' : ''} />
                  </button>
                </div>
              </div>

              {/* Monospace scrollable log window */}
              <div className="bg-slate-950 border border-slate-800/90 rounded p-3 h-64 overflow-y-auto font-mono text-[11px] space-y-1.5">
                {systemLogs.length === 0 ? (
                  <div className="text-slate-600 text-center py-8">No log events recorded matching filter.</div>
                ) : (
                  systemLogs.map((log, idx) => (
                    <div key={idx} className="flex items-start gap-2 hover:bg-slate-900/40 px-1 py-0.5 rounded">
                      <span className="text-slate-500 text-[10px] flex-shrink-0">{log.timestamp}</span>
                      <span className={`px-1 rounded text-[9px] font-bold flex-shrink-0 ${
                        log.level === 'INFO'
                          ? 'bg-sky-500/20 text-sky-400'
                          : log.level === 'WARNING'
                          ? 'bg-amber-500/20 text-amber-400'
                          : 'bg-red-500/20 text-red-400'
                      }`}>
                        {log.level}
                      </span>
                      <span className="text-indigo-400 text-[10px] flex-shrink-0">[{log.source}]</span>
                      <span className="text-slate-300 flex-1">{log.message}</span>
                      {log.metric && (
                        <span className="text-[10px] text-emerald-400 bg-emerald-950/40 px-1 rounded flex-shrink-0">
                          {log.metric}
                        </span>
                      )}
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        )}

        {/* ── Persistent Save / Reset Actions Toolbar ─────────────────────── */}
        <div className="flex items-center justify-between bg-slate-900/90 border border-slate-800 p-4 rounded-lg">
          <div className="text-xs text-slate-400 font-mono">
            Settings are persistently synchronized with <code className="text-slate-300">/data/edge_settings.json</code>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={handleResetDefaults}
              className="px-4 py-2 border border-slate-700 hover:bg-slate-800 text-slate-300 text-xs font-semibold rounded flex items-center gap-1.5 transition-colors"
            >
              <RotateCcw size={14} />
              Reset Defaults
            </button>
            <button
              onClick={handleSaveSettings}
              disabled={saving}
              className="px-5 py-2 bg-sky-600 hover:bg-sky-500 text-white text-xs font-semibold rounded flex items-center gap-1.5 shadow-lg shadow-sky-600/20 transition-colors"
            >
              {saving ? <RefreshCw size={14} className="animate-spin" /> : <Save size={14} />}
              Save All Settings
            </button>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

