import React, { useState } from 'react';
import AppShell from '@/components/layout/AppShell';
import { Plus, AlertTriangle, CheckCircle2, Clock } from 'lucide-react';

const INITIAL_INCIDENTS = [
  {
    id: 3,
    title: "Motion Anomaly Detected",
    description: "AI cognitive engine flagged anomalous clustering in Sector 7G. Potential unauthorized gathering.",
    severity: "CRITICAL",
    createdBy: "system",
    timestamp: "2026-07-14T11:42",
    lat: "8.5241",
    lng: "76.9366",
    status: "OPEN"
  },
  {
    id: 2,
    title: "Low Battery Alert",
    description: "Drone battery dropped below 15% threshold during active sweep.",
    severity: "HIGH",
    createdBy: "rajesh.kp",
    timestamp: "2026-07-14T11:15",
    lat: "8.5255",
    lng: "76.9380",
    status: "OPEN"
  },
  {
    id: 1,
    title: "Stream Interruption",
    description: "Telemetry link jitter exceeded 500ms causing frame dropping.",
    severity: "MEDIUM",
    createdBy: "rajesh.kp",
    timestamp: "2026-07-14T09:30",
    lat: "8.5210",
    lng: "76.9350",
    status: "ACKNOWLEDGED"
  }
];

export default function Incidents() {
  const [incidents, setIncidents] = useState(INITIAL_INCIDENTS);
  
  const [form, setForm] = useState({
    title: '',
    description: '',
    severity: 'LOW',
    timestamp: new Date().toISOString().slice(0, 16),
    lat: '',
    lng: ''
  });

  const openCount = incidents.filter(i => i.status === 'OPEN').length;

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!form.title) return;
    
    const newInc = {
      id: incidents.length > 0 ? Math.max(...incidents.map(i => i.id)) + 1 : 1,
      ...form,
      createdBy: "current_operator", // Hardcoded for mockup
      status: "OPEN"
    };
    
    setIncidents([newInc, ...incidents]);
    setForm({
      title: '',
      description: '',
      severity: 'LOW',
      timestamp: new Date().toISOString().slice(0, 16),
      lat: '',
      lng: ''
    });
  };

  const getSeverityStyle = (severity) => {
    switch(severity) {
      case 'CRITICAL': return 'bg-red-500/10 border-red-500/20 text-red-400';
      case 'HIGH': return 'bg-amber-500/10 border-amber-500/20 text-amber-400';
      case 'MEDIUM': return 'bg-sky-500/10 border-sky-500/20 text-sky-400';
      case 'LOW': return 'bg-slate-800 border-slate-700 text-slate-400';
      default: return 'bg-slate-800 border-slate-700 text-slate-400';
    }
  };

  const getSeverityDot = (severity) => {
    switch(severity) {
      case 'CRITICAL': return 'bg-red-500';
      case 'HIGH': return 'bg-amber-500';
      case 'MEDIUM': return 'bg-sky-500';
      case 'LOW': return 'bg-slate-500';
      default: return 'bg-slate-500';
    }
  };

  const getStatusStyle = (status) => {
    switch(status) {
      case 'OPEN': return 'bg-red-500/10 border-red-500/20 text-red-400';
      case 'ACKNOWLEDGED': return 'bg-amber-500/10 border-amber-500/20 text-amber-400';
      case 'RESOLVED': return 'bg-slate-800 border-slate-700 text-slate-500';
      default: return 'bg-slate-800 border-slate-700 text-slate-500';
    }
  };

  const getStatusIcon = (status) => {
    switch(status) {
      case 'OPEN': return <AlertTriangle size={12} />;
      case 'ACKNOWLEDGED': return <Clock size={12} />;
      case 'RESOLVED': return <CheckCircle2 size={12} />;
      default: return null;
    }
  };

  return (
    <AppShell title="Incidents" subtitle="Create and track operational incidents">
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-start">
        
        {/* Create Incident Form (4 cols) */}
        <div className="xl:col-span-4 bg-slate-900 border border-slate-800 rounded-lg flex flex-col overflow-hidden sticky top-6">
          <div className="p-4 border-b border-slate-800 bg-slate-950/50">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400">Report New Incident</h2>
          </div>
          <form onSubmit={handleSubmit} className="p-5 flex flex-col gap-4">
            
            <div>
              <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1.5">Title <span className="text-red-500">*</span></label>
              <input 
                type="text" 
                required
                value={form.title}
                onChange={e => setForm({...form, title: e.target.value})}
                className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-sm text-slate-200 rounded transition-colors"
                placeholder="Brief summary of the issue"
              />
            </div>

            <div>
              <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1.5">Description</label>
              <textarea 
                rows="3"
                value={form.description}
                onChange={e => setForm({...form, description: e.target.value})}
                className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-sm text-slate-200 rounded transition-colors resize-none"
                placeholder="Detailed explanation..."
              ></textarea>
            </div>

            <div>
              <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1.5">Severity</label>
              <div className="relative">
                <select 
                  value={form.severity}
                  onChange={e => setForm({...form, severity: e.target.value})}
                  className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 pl-8 text-sm text-slate-200 rounded appearance-none cursor-pointer"
                >
                  <option value="LOW">Low</option>
                  <option value="MEDIUM">Medium</option>
                  <option value="HIGH">High</option>
                  <option value="CRITICAL">Critical</option>
                </select>
                <div className={`absolute left-3 top-1/2 -translate-y-1/2 w-2 h-2 rounded-full ${getSeverityDot(form.severity)}`}></div>
              </div>
            </div>

            <div>
              <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1.5">Timestamp</label>
              <input 
                type="datetime-local" 
                value={form.timestamp}
                onChange={e => setForm({...form, timestamp: e.target.value})}
                className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-sm text-slate-200 font-mono rounded"
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1.5">Latitude</label>
                <input 
                  type="text" 
                  value={form.lat}
                  onChange={e => setForm({...form, lat: e.target.value})}
                  className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-sm font-mono text-slate-200 rounded"
                  placeholder="e.g. 8.5241"
                />
              </div>
              <div>
                <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1.5">Longitude</label>
                <input 
                  type="text" 
                  value={form.lng}
                  onChange={e => setForm({...form, lng: e.target.value})}
                  className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-sm font-mono text-slate-200 rounded"
                  placeholder="e.g. 76.9366"
                />
              </div>
            </div>

            <button type="submit" className="mt-2 w-full bg-sky-600 hover:bg-sky-500 text-white py-2.5 text-sm font-semibold tracking-wide flex items-center justify-center gap-2 rounded transition-colors shadow-lg shadow-sky-500/20">
              <Plus size={16} /> Create Incident
            </button>

          </form>
        </div>

        {/* Incident Log Table (8 cols) */}
        <div className="xl:col-span-8 bg-slate-900 border border-slate-800 rounded-lg flex flex-col overflow-hidden">
          <div className="p-4 border-b border-slate-800 flex justify-between items-center bg-slate-950/50">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400">Incident Log</h2>
            <div className="text-[10px] px-2 py-0.5 rounded border border-red-500/30 bg-red-500/10 text-red-400 font-bold tracking-wider flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-red-400 animate-pulse"></span>
              {openCount} OPEN
            </div>
          </div>
          
          <div className="overflow-x-auto flex-1">
            <table className="w-full text-left text-sm whitespace-nowrap">
              <thead className="bg-slate-950/80 text-slate-500 text-[10px] uppercase font-semibold">
                <tr>
                  <th className="px-4 py-3 w-28">Severity</th>
                  <th className="px-4 py-3 min-w-[250px]">Title & Details</th>
                  <th className="px-4 py-3 w-32">Created By</th>
                  <th className="px-4 py-3 w-32">Timestamp</th>
                  <th className="px-4 py-3 w-32">GPS</th>
                  <th className="px-4 py-3 w-32">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/50">
                {incidents.map(incident => (
                  <tr key={incident.id} className={`hover:bg-slate-800/30 transition-colors ${incident.status === 'RESOLVED' ? 'opacity-60' : ''}`}>
                    <td className="px-4 py-4">
                      <span className={`inline-block px-2 py-0.5 rounded text-[9px] font-mono tracking-wider border ${getSeverityStyle(incident.severity)}`}>
                        {incident.severity}
                      </span>
                    </td>
                    <td className="px-4 py-4">
                      <div className="font-semibold text-slate-200 mb-0.5">{incident.title}</div>
                      <div className="text-xs text-slate-500 truncate max-w-[250px] lg:max-w-[350px]">{incident.description || 'No description provided.'}</div>
                    </td>
                    <td className="px-4 py-4">
                      <span className="inline-block px-2 py-0.5 rounded text-[10px] font-mono tracking-wider bg-slate-800 text-slate-300 border border-slate-700">
                        {incident.createdBy}
                      </span>
                    </td>
                    <td className="px-4 py-4 font-mono text-xs text-slate-400">
                      {incident.timestamp.replace('T', ' ')}
                    </td>
                    <td className="px-4 py-4 font-mono text-[10px] text-slate-500">
                      {incident.lat && incident.lng ? (
                        <div className="flex flex-col">
                          <span>{incident.lat}</span>
                          <span>{incident.lng}</span>
                        </div>
                      ) : (
                        <span>--</span>
                      )}
                    </td>
                    <td className="px-4 py-4">
                      <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[9px] font-mono font-bold tracking-wider border ${getStatusStyle(incident.status)}`}>
                        {getStatusIcon(incident.status)}
                        {incident.status}
                      </span>
                    </td>
                  </tr>
                ))}
                {incidents.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-4 py-8 text-center text-slate-500">No incidents reported.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

      </div>
    </AppShell>
  );
}
