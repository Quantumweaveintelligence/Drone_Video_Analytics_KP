import React, { useState } from 'react';
import AppShell from '@/components/layout/AppShell';
import { Target, Play, Trash, XCircle, CheckCircle } from 'lucide-react';

export default function MissionControl() {
  const [activeMission, setActiveMission] = useState({
    id: "MSN-9942-ALPHA",
    designation: "Coastal Surveillance Patrolling",
    drone: "DRN-001",
    operator: "Operator K. Kumar",
    location: "Sector 4, Marina",
    startTime: "14:02:44"
  });

  const [form, setForm] = useState({ name: '', id: '', drone: '', operator: '', location: '' });

  const missions = [
    { id: "MSN-9942-ALPHA", designation: "Coastal Surveillance Patrolling", drone: "DRN-001", operator: "K. Kumar", location: "Sector 4, Marina", start: "14:02:44", end: "--:--:--", status: "active" },
    { id: "MSN-9941-BRAVO", designation: "Traffic Monitoring", drone: "DRN-002", operator: "A. Sharma", location: "Junction 12", start: "11:15:00", end: "13:45:00", status: "completed" },
    { id: "MSN-9940-CHARLIE", designation: "VIP Route Sweep", drone: "DRN-001", operator: "K. Kumar", location: "Airport Road", start: "08:30:00", end: "10:15:00", status: "completed" },
  ];

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
                </div>

                <div className="mt-auto">
                  <button 
                    onClick={() => setActiveMission(null)}
                    className="w-full py-2.5 border border-red-500/50 hover:bg-red-500/10 text-red-400 text-sm font-semibold tracking-wide uppercase transition-colors rounded"
                  >
                    End Mission
                  </button>
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
                  value={form.name} onChange={e => setForm({...form, name: e.target.value})}
                  className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-sm text-slate-200"
                  placeholder="e.g. VIP Route Sweep"
                />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1.5">Mission ID</label>
                  <input 
                    type="text" 
                    value={form.id} onChange={e => setForm({...form, id: e.target.value})}
                    className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-sm font-mono text-slate-200 placeholder-slate-600"
                    placeholder="Auto-generated"
                  />
                </div>
                <div>
                  <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1.5">Drone ID *</label>
                  <input 
                    type="text" 
                    value={form.drone} onChange={e => setForm({...form, drone: e.target.value})}
                    className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-sm font-mono text-slate-200"
                    placeholder="e.g. DRN-001"
                  />
                </div>
              </div>
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
                  <label className="block text-[10px] text-slate-500 uppercase font-semibold mb-1.5">Location / Grid</label>
                  <input 
                    type="text" 
                    value={form.location} onChange={e => setForm({...form, location: e.target.value})}
                    className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-2 text-sm text-slate-200"
                  />
                </div>
              </div>
            </div>

            <div className="mt-6 flex gap-3">
              <button className="flex-1 bg-sky-600 hover:bg-sky-500 text-white py-2.5 text-sm font-semibold tracking-wide flex items-center justify-center gap-2 rounded transition-colors">
                <Play size={16} fill="currentColor" /> Start Mission
              </button>
              <button 
                onClick={() => setForm({ name: '', id: '', drone: '', operator: '', location: '' })}
                className="px-6 border border-slate-700 hover:bg-slate-800 text-slate-300 py-2.5 text-sm font-semibold tracking-wide rounded transition-colors"
              >
                Clear
              </button>
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
                {missions.map((m, i) => (
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
