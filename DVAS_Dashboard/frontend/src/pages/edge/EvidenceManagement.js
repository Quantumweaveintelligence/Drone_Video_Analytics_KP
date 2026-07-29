import React, { useState } from 'react';
import AppShell from '@/components/layout/AppShell';
import { Package, Download, Database, CheckCircle2, Clock, UploadCloud } from 'lucide-react';

const INITIAL_EVIDENCE = [
  {
    id: "EVI-0028",
    mission: "MSN-2025-0441",
    drone: "DRN-001",
    timestamp: "2026-07-14 10:15:22",
    frames: "1,847",
    size: "142.3 MB",
    status: "READY"
  },
  {
    id: "EVI-0027",
    mission: "MSN-2025-0440",
    drone: "DRN-001",
    timestamp: "2026-07-13 18:30:00",
    frames: "1,102",
    size: "89.1 MB",
    status: "READY"
  },
  {
    id: "EVI-0026",
    mission: "MSN-2025-0439",
    drone: "DRN-001",
    timestamp: "2026-07-13 14:05:11",
    frames: "--",
    size: "--",
    status: "PROCESSING"
  }
];

export default function EvidenceManagement() {
  const [evidence, setEvidence] = useState(INITIAL_EVIDENCE);
  const [selectedMission, setSelectedMission] = useState("MSN-2025-0441");
  const [isExtracting, setIsExtracting] = useState(false);
  const [progress, setProgress] = useState(0);

  const handleExtraction = () => {
    if (isExtracting) return;
    
    setIsExtracting(true);
    setProgress(0);

    const interval = setInterval(() => {
      setProgress(p => {
        if (p >= 100) {
          clearInterval(interval);
          
          // Complete extraction
          setTimeout(() => {
            const newId = `EVI-002${evidence.length + 6}`; // naive id generator for mockup
            setEvidence([
              {
                id: newId,
                mission: selectedMission,
                drone: "DRN-001",
                timestamp: new Date().toISOString().replace('T', ' ').slice(0, 19),
                frames: "2,041",
                size: "168.4 MB",
                status: "READY"
              },
              ...evidence
            ]);
            setIsExtracting(false);
            setProgress(0);
          }, 300);
          return 100;
        }
        return p + 20; // Complete in ~2 seconds (10 steps of 200ms)
      });
    }, 200);
  };

  const totalPackages = evidence.length;
  const readyPackages = evidence.filter(e => e.status === 'READY').length;
  const processingPackages = evidence.filter(e => e.status === 'PROCESSING').length;
  
  // Naive calculation for mock sizes
  const totalSize = evidence
    .map(e => parseFloat(e.size) || 0)
    .reduce((a, b) => a + b, 0)
    .toFixed(1);

  const getStatusStyle = (status) => {
    switch(status) {
      case 'READY': return 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400';
      case 'PROCESSING': return 'bg-amber-500/10 border-amber-500/20 text-amber-400';
      case 'FAILED': return 'bg-red-500/10 border-red-500/20 text-red-400';
      default: return 'bg-slate-800 border-slate-700 text-slate-500';
    }
  };

  const getStatusIcon = (status) => {
    switch(status) {
      case 'READY': return <CheckCircle2 size={12} />;
      case 'PROCESSING': return <Clock size={12} />;
      default: return null;
    }
  };

  return (
    <AppShell title="Evidence Management" subtitle="Extract and manage evidence packages from the buffer">
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-start">
        
        {/* Left Column (4 cols) */}
        <div className="xl:col-span-4 flex flex-col gap-6 sticky top-6">
          
          {/* Trigger Extraction Form */}
          <div className="bg-slate-900 border border-slate-800 rounded-lg flex flex-col overflow-hidden">
            <div className="p-4 border-b border-slate-800 bg-slate-950/50 flex items-center gap-2">
              <Database size={16} className="text-slate-400" />
              <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400">Trigger Extraction</h2>
            </div>
            
            <div className="p-5 flex flex-col gap-5">
              <div>
                <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1.5">Mission Target</label>
                <div className="relative">
                  <select 
                    value={selectedMission}
                    onChange={e => setSelectedMission(e.target.value)}
                    disabled={isExtracting}
                    className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-sm text-slate-200 font-mono rounded appearance-none cursor-pointer disabled:opacity-50"
                  >
                    <option value="MSN-2025-0441">MSN-2025-0441</option>
                    <option value="MSN-2025-0440">MSN-2025-0440</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1.5">Assigned Asset</label>
                <div className="inline-flex items-center px-3 py-1.5 bg-sky-900/30 border border-sky-500/20 text-sky-400 rounded text-xs font-mono font-bold tracking-wider">
                  DRN-001 · Garuda-1
                </div>
              </div>

              <div>
                <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1.5">Extraction Time Range</label>
                <div className="flex items-center gap-2 text-xs font-mono">
                  <div className="flex-1 bg-slate-950 border border-slate-700 px-3 py-1.5 rounded text-slate-200">
                    10:15:00 <span className="text-slate-500 text-[10px] ml-1">UTC</span>
                  </div>
                  <span className="text-slate-500 font-sans">to</span>
                  <div className="flex-1 bg-slate-950 border border-slate-700 px-3 py-1.5 rounded text-slate-200">
                    11:30:00 <span className="text-slate-500 text-[10px] ml-1">UTC</span>
                  </div>
                </div>
              </div>

              <div className="bg-slate-950 border border-slate-800 p-3 rounded text-xs text-slate-400 leading-relaxed">
                <span className="font-semibold text-slate-300">Note:</span> This will package selected frames from the rolling buffer spanning the entire duration of the selected mission.
              </div>

              {isExtracting && (
                <div className="space-y-1.5">
                  <div className="flex justify-between text-[10px] font-semibold text-sky-400 uppercase tracking-wider">
                    <span>Extracting...</span>
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
                  <><RefreshCw size={16} className="animate-spin" /> Processing...</>
                ) : (
                  <><UploadCloud size={16} /> Extract Evidence</>
                )}
              </button>
            </div>
          </div>

          {/* Package Summary Card */}
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col">
            <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider mb-4 flex items-center gap-2">
              <Package size={14} /> Package Summary
            </div>
            
            <div className="grid grid-cols-2 gap-y-4 gap-x-2">
              <div className="flex flex-col">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Total Packages</span>
                <span className="text-xl font-mono text-slate-200">{totalPackages}</span>
              </div>
              <div className="flex flex-col">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Total Size</span>
                <span className="text-xl font-mono text-sky-400">{totalSize} <span className="text-xs text-sky-400/50">MB</span></span>
              </div>
              <div className="flex flex-col">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Ready</span>
                <span className="text-xl font-mono text-emerald-400">{readyPackages}</span>
              </div>
              <div className="flex flex-col">
                <span className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Processing</span>
                <span className="text-xl font-mono text-amber-400">{processingPackages}</span>
              </div>
            </div>
          </div>

        </div>

        {/* Evidence Archive Table (8 cols) */}
        <div className="xl:col-span-8 bg-slate-900 border border-slate-800 rounded-lg flex flex-col overflow-hidden">
          <div className="p-4 border-b border-slate-800 bg-slate-950/50 flex justify-between items-center">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400">Evidence Archive</h2>
          </div>
          
          <div className="overflow-x-auto flex-1 min-h-[400px]">
            <table className="w-full text-left text-sm whitespace-nowrap">
              <thead className="bg-slate-950/80 text-slate-500 text-[10px] uppercase font-semibold">
                <tr>
                  <th className="px-4 py-3">Evidence ID</th>
                  <th className="px-4 py-3">Mission</th>
                  <th className="px-4 py-3">Drone</th>
                  <th className="px-4 py-3">Extracted</th>
                  <th className="px-4 py-3">Frames</th>
                  <th className="px-4 py-3">Size</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/50">
                {evidence.map(item => (
                  <tr key={item.id} className="hover:bg-slate-800/30 transition-colors">
                    <td className="px-4 py-4 font-mono font-bold text-sky-200">{item.id}</td>
                    <td className="px-4 py-4 font-mono text-slate-300">{item.mission}</td>
                    <td className="px-4 py-4">
                      <span className="inline-block px-2 py-0.5 rounded text-[10px] font-mono tracking-wider bg-slate-800 text-slate-300 border border-slate-700">
                        {item.drone}
                      </span>
                    </td>
                    <td className="px-4 py-4 font-mono text-xs text-slate-400">
                      {item.timestamp}
                    </td>
                    <td className="px-4 py-4 font-mono text-slate-300">{item.frames}</td>
                    <td className="px-4 py-4 font-mono text-slate-300">{item.size}</td>
                    <td className="px-4 py-4">
                      <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[9px] font-mono font-bold tracking-wider border ${getStatusStyle(item.status)}`}>
                        {getStatusIcon(item.status)}
                        {item.status}
                      </span>
                    </td>
                    <td className="px-4 py-4 text-right">
                      {item.status === 'READY' ? (
                        <button className="inline-flex items-center justify-center p-1.5 bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/30 text-emerald-400 rounded transition-colors" title="Download Package">
                          <Download size={14} />
                        </button>
                      ) : (
                        <button disabled className="inline-flex items-center justify-center p-1.5 bg-slate-800 text-slate-600 rounded cursor-not-allowed">
                          <Download size={14} />
                        </button>
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
