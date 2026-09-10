import React, { useState, useEffect } from 'react';
import AppShell from '@/components/layout/AppShell';
import { api, API } from '@/lib/api';
import { useAuth } from '@/context/AuthContext';
import { Package, Download, Database, CheckCircle2, Clock, UploadCloud, RefreshCw, AlertTriangle } from 'lucide-react';

export default function EvidenceManagement() {
  const { user } = useAuth();
  const [evidence, setEvidence] = useState([]);
  const [missions, setMissions] = useState([]);
  const [selectedMission, setSelectedMission] = useState("");
  const [isExtracting, setIsExtracting] = useState(false);
  const [progress, setProgress] = useState(0);
  const [loading, setLoading] = useState(true);

  const loadData = async () => {
    try {
      setLoading(true);
      const [eR, mR] = await Promise.all([
        api.get("/evidence").catch(() => ({ data: { items: [] } })),
        api.get("/missions").catch(() => ({ data: [] })),
      ]);
      const eData = Array.isArray(eR.data) ? eR.data : (eR.data.items || []);
      setEvidence(eData);
      setMissions(mR.data || []);
      if (!selectedMission && mR.data?.length > 0) {
        setSelectedMission(mR.data[0].mission_id);
      }
    } catch (e) { console.error(e); }
    finally { setLoading(false); }
  };

  useEffect(() => { loadData(); const t = setInterval(loadData, 30000); return () => clearInterval(t); }, []);

  // Filter evidence by selected mission
  const filteredEvidence = selectedMission
    ? evidence.filter(e => e.mission_id === selectedMission)
    : evidence;

  const handleExtraction = () => {
    if (isExtracting) return;
    setIsExtracting(true);
    setProgress(0);
    const interval = setInterval(() => {
      setProgress(p => {
        if (p >= 100) {
          clearInterval(interval);
          setTimeout(() => {
            setIsExtracting(false);
            setProgress(0);
            loadData(); // Refresh to pick up any new evidence
          }, 500);
          return 100;
        }
        return p + 20;
      });
    }, 200);
  };

  const totalPackages = filteredEvidence.length;
  const legalHoldCount = filteredEvidence.filter(e => e.legal_hold).length;

  const getStatusStyle = (ev) => {
    if (ev.legal_hold) return 'bg-red-500/10 border-red-500/20 text-red-400';
    return 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400';
  };

  return (
    <AppShell title="Evidence Management" subtitle={`Operator: ${user?.email || 'Unknown'} — View mission evidence`}>
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-start">
        
        {/* Left Column */}
        <div className="xl:col-span-4 flex flex-col gap-6 sticky top-6">
          
          {/* Mission Selector & Extract */}
          <div className="bg-slate-900 border border-slate-800 rounded-lg flex flex-col overflow-hidden">
            <div className="p-4 border-b border-slate-800 bg-slate-950/50 flex items-center gap-2">
              <Database size={16} className="text-slate-400" />
              <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400">Mission Evidence</h2>
            </div>
            
            <div className="p-5 flex flex-col gap-5">
              <div>
                <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1.5">Filter by Mission</label>
                <select 
                  value={selectedMission}
                  onChange={e => setSelectedMission(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-sm text-slate-200 font-mono rounded appearance-none cursor-pointer"
                >
                  <option value="">All Missions</option>
                  {missions.map(m => <option key={m.mission_id} value={m.mission_id}>{m.mission_id} — {m.name}</option>)}
                </select>
              </div>

              <div className="bg-slate-950 border border-slate-800 p-3 rounded text-xs text-slate-400 leading-relaxed">
                <span className="font-semibold text-slate-300">Note:</span> Evidence items are automatically captured by the analytics pipeline during missions. Items shown here are scoped to your assigned missions.
              </div>

              {isExtracting && (
                <div className="space-y-1.5">
                  <div className="flex justify-between text-[10px] font-semibold text-sky-400 uppercase tracking-wider">
                    <span>Refreshing...</span>
                    <span className="font-mono">{progress}%</span>
                  </div>
                  <div className="w-full bg-slate-950 h-2 rounded-full overflow-hidden border border-slate-800">
                    <div className="bg-sky-500 h-full rounded-full transition-all duration-200 ease-linear" style={{ width: `${progress}%` }}></div>
                  </div>
                </div>
              )}

              <button 
                onClick={handleExtraction}
                disabled={isExtracting}
                className="w-full bg-sky-600 hover:bg-sky-500 disabled:bg-slate-800 disabled:text-slate-500 disabled:cursor-not-allowed text-white py-2.5 text-sm font-semibold tracking-wide flex items-center justify-center gap-2 rounded transition-colors shadow-lg shadow-sky-500/20 disabled:shadow-none mt-2"
              >
                {isExtracting ? (
                  <><RefreshCw size={16} className="animate-spin" /> Refreshing...</>
                ) : (
                  <><UploadCloud size={16} /> Refresh Evidence</>
                )}
              </button>
            </div>
          </div>

          {/* Package Summary */}
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col">
            <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-4 flex items-center gap-2">
              <Package size={14} /> Evidence Summary
            </div>
            <div className="grid grid-cols-2 gap-y-4 gap-x-2">
              <div className="flex flex-col">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Total Items</span>
                <span className="text-xl font-mono text-slate-200">{totalPackages}</span>
              </div>
              <div className="flex flex-col">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">All Evidence</span>
                <span className="text-xl font-mono text-sky-400">{evidence.length}</span>
              </div>
              <div className="flex flex-col">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Missions</span>
                <span className="text-xl font-mono text-emerald-400">{missions.length}</span>
              </div>
              <div className="flex flex-col">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Legal Holds</span>
                <span className="text-xl font-mono text-amber-400">{legalHoldCount}</span>
              </div>
            </div>
          </div>

        </div>

        {/* Evidence Table */}
        <div className="xl:col-span-8 bg-slate-900 border border-slate-800 rounded-lg flex flex-col overflow-hidden">
          <div className="p-4 border-b border-slate-800 bg-slate-950/50 flex justify-between items-center">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400">Evidence Archive</h2>
            <span className="mono text-[10px] text-slate-500">{filteredEvidence.length} items{selectedMission ? ` for ${selectedMission}` : ''}</span>
          </div>
          
          <div className="overflow-x-auto flex-1 min-h-[400px]">
            {loading ? (
              <div className="flex items-center justify-center h-40 text-slate-500 text-sm"><RefreshCw size={16} className="animate-spin mr-2" /> Loading evidence...</div>
            ) : (
              <table className="w-full text-left text-sm whitespace-nowrap">
                <thead className="bg-slate-950/80 text-slate-500 text-[10px] uppercase font-semibold">
                  <tr>
                    <th className="px-4 py-3">Evidence ID</th>
                    <th className="px-4 py-3">Mission</th>
                    <th className="px-4 py-3">Drone</th>
                    <th className="px-4 py-3">Type</th>
                    <th className="px-4 py-3">Confidence</th>
                    <th className="px-4 py-3">Captured</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3 text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/50">
                  {filteredEvidence.length === 0 && (
                    <tr><td colSpan={8} className="px-4 py-12 text-center text-slate-500">No evidence items found{selectedMission ? ` for mission ${selectedMission}` : ''}</td></tr>
                  )}
                  {filteredEvidence.map(item => (
                    <tr key={item.id} className="hover:bg-slate-800/30 transition-colors">
                      <td className="px-4 py-4 font-mono font-bold text-sky-200">{item.id}</td>
                      <td className="px-4 py-4 font-mono text-slate-300">{item.mission_id || '—'}</td>
                      <td className="px-4 py-4">
                        <span className="inline-block px-2 py-0.5 rounded text-[10px] font-mono tracking-wider bg-slate-800 text-slate-300 border border-slate-700">
                          {item.drone_id || '—'}
                        </span>
                      </td>
                      <td className="px-4 py-4 font-mono text-xs text-slate-400">{(item.event_type || '').replace(/_/g, ' ')}</td>
                      <td className="px-4 py-4 font-mono text-slate-300">{item.confidence || '—'}</td>
                      <td className="px-4 py-4 font-mono text-xs text-slate-400">{item.ts ? new Date(item.ts).toLocaleString() : '—'}</td>
                      <td className="px-4 py-4">
                        <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[9px] font-mono font-bold tracking-wider border ${getStatusStyle(item)}`}>
                          {item.legal_hold ? <><AlertTriangle size={10} /> HOLD</> : <><CheckCircle2 size={10} /> READY</>}
                        </span>
                      </td>
                      <td className="px-4 py-4 text-right">
                        {item.video_url ? (
                          <a href={item.video_url} target="_blank" rel="noreferrer" className="inline-flex items-center justify-center p-1.5 bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/30 text-emerald-400 rounded transition-colors" title="View Evidence">
                            <Download size={14} />
                          </a>
                        ) : (
                          <button disabled className="inline-flex items-center justify-center p-1.5 bg-slate-800 text-slate-600 rounded cursor-not-allowed" title="No video available">
                            <Download size={14} />
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

      </div>
    </AppShell>
  );
}
