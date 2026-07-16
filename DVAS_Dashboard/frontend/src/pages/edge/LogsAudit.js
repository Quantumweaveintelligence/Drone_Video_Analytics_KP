import React, { useState, useMemo } from 'react';
import AppShell from '@/components/layout/AppShell';
import { Search, Filter, ChevronLeft, ChevronRight, ClipboardList } from 'lucide-react';

const generateSystemLogs = () => {
  const sources = ['stream-mgr', 'buffer-svc', 'telemetry', 'ingest', 'system'];
  const levels = ['INFO', 'DEBUG', 'WARNING', 'ERROR'];
  const messages = [
    "Connection established successfully.",
    "Frame dropped due to high latency.",
    "Buffer allocation expanded.",
    "Heartbeat timeout.",
    "Received invalid payload signature.",
    "Garbage collection triggered.",
    "Syncing time with master node.",
    "Socket connection closed unexpectedly."
  ];

  return Array.from({ length: 50 }).map((_, i) => {
    const level = levels[Math.floor(Math.random() * levels.length)];
    return {
      id: 50 - i,
      timestamp: new Date(Date.now() - Math.random() * 86400000).toISOString().replace('T', ' ').slice(0, 19),
      level: i === 0 ? 'ERROR' : i === 3 ? 'WARNING' : level, // Ensure some errors/warnings exist
      source: sources[Math.floor(Math.random() * sources.length)],
      message: messages[Math.floor(Math.random() * messages.length)]
    };
  }).sort((a, b) => b.timestamp.localeCompare(a.timestamp));
};

const INITIAL_LOGS = generateSystemLogs();

const AUDIT_TRAIL = [
  { id: 1, timestamp: "2026-07-14 11:42:05", user: "rajesh.kp", action: "START_MISSION", resource: "MSN-2025-0441", ip: "192.168.1.42", details: "Initiated via manual override" },
  { id: 2, timestamp: "2026-07-14 10:15:22", user: "rajesh.kp", action: "TRIGGER_EXTRACTION", resource: "EVI-0028", ip: "192.168.1.42", details: "Extracted 420 frames" },
  { id: 3, timestamp: "2026-07-14 09:30:00", user: "shyam.kumar", action: "VIEW_OPERATOR_FEED", resource: "rajesh.kp", ip: "192.168.1.10", details: "Supervisory monitoring session started" },
  { id: 4, timestamp: "2026-07-13 18:45:10", user: "rajesh.kp", action: "LOGIN", resource: "SYSTEM", ip: "192.168.1.42", details: "Authenticated via SSO" }
];

export default function LogsAudit() {
  const [activeTab, setActiveTab] = useState('system');
  
  // System Logs State
  const [logSearch, setLogSearch] = useState('');
  const [logLevel, setLogLevel] = useState('ALL');
  const [logPageSize, setLogPageSize] = useState(10);
  const [logPage, setLogPage] = useState(1);

  // Audit Trail State
  const [auditSearch, setAuditSearch] = useState('');

  // Filtering System Logs
  const filteredLogs = useMemo(() => {
    return INITIAL_LOGS.filter(log => {
      const matchLevel = logLevel === 'ALL' || log.level === logLevel;
      const term = logSearch.toLowerCase();
      const matchSearch = log.message.toLowerCase().includes(term) || log.source.toLowerCase().includes(term);
      return matchLevel && matchSearch;
    });
  }, [logSearch, logLevel]);

  const totalLogPages = Math.ceil(filteredLogs.length / logPageSize) || 1;
  const paginatedLogs = filteredLogs.slice((logPage - 1) * logPageSize, logPage * logPageSize);

  // Filtering Audit Trail
  const filteredAudit = useMemo(() => {
    return AUDIT_TRAIL.filter(log => {
      const term = auditSearch.toLowerCase();
      return log.user.toLowerCase().includes(term) || log.action.toLowerCase().includes(term) || log.resource.toLowerCase().includes(term);
    });
  }, [auditSearch]);

  const getLevelColor = (level) => {
    switch(level) {
      case 'DEBUG': return 'bg-slate-800 text-slate-400 border-slate-700';
      case 'INFO': return 'bg-sky-500/10 text-sky-400 border-sky-500/20';
      case 'WARNING': return 'bg-amber-500/10 text-amber-400 border-amber-500/20';
      case 'ERROR': return 'bg-red-500/10 text-red-400 border-red-500/20';
      default: return 'bg-slate-800 text-slate-400 border-slate-700';
    }
  };

  const getMessageColor = (level) => {
    switch(level) {
      case 'WARNING': return 'text-amber-300';
      case 'ERROR': return 'text-red-400 font-medium';
      default: return 'text-slate-300';
    }
  };

  return (
    <AppShell title="Logs & Audit" subtitle="Browse system events and operator action audit trail">
      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden flex flex-col min-h-[600px]">
        
        {/* Tabs */}
        <div className="flex border-b border-slate-800 bg-slate-950/50">
          <button 
            className={`px-6 py-4 text-sm font-semibold tracking-wider uppercase transition-colors border-b-2 ${activeTab === 'system' ? 'border-sky-500 text-sky-400' : 'border-transparent text-slate-500 hover:text-slate-300'}`}
            onClick={() => { setActiveTab('system'); setLogPage(1); }}
          >
            System Logs
          </button>
          <button 
            className={`px-6 py-4 text-sm font-semibold tracking-wider uppercase transition-colors border-b-2 ${activeTab === 'audit' ? 'border-emerald-500 text-emerald-400' : 'border-transparent text-slate-500 hover:text-slate-300'}`}
            onClick={() => setActiveTab('audit')}
          >
            Audit Trail
          </button>
        </div>

        {/* Tab 1: System Logs */}
        {activeTab === 'system' && (
          <div className="flex flex-col flex-1">
            {/* Toolbar */}
            <div className="p-4 border-b border-slate-800 flex flex-wrap gap-4 items-center justify-between">
              <div className="flex gap-4 flex-1 max-w-xl">
                <div className="relative flex-1">
                  <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                  <input 
                    type="text" 
                    placeholder="Search messages or sources..." 
                    value={logSearch}
                    onChange={(e) => { setLogSearch(e.target.value); setLogPage(1); }}
                    className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 text-sm text-slate-200 py-2 pl-9 pr-3 rounded outline-none"
                  />
                </div>
                <div className="relative w-40">
                  <Filter size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                  <select 
                    value={logLevel}
                    onChange={(e) => { setLogLevel(e.target.value); setLogPage(1); }}
                    className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 text-sm text-slate-200 py-2 pl-9 pr-3 rounded outline-none appearance-none cursor-pointer"
                  >
                    <option value="ALL">All Levels</option>
                    <option value="DEBUG">Debug</option>
                    <option value="INFO">Info</option>
                    <option value="WARNING">Warning</option>
                    <option value="ERROR">Error</option>
                  </select>
                </div>
              </div>
              <div className="flex items-center gap-2 text-xs text-slate-400">
                Rows per page: 
                <select 
                  value={logPageSize} 
                  onChange={e => { setLogPageSize(Number(e.target.value)); setLogPage(1); }}
                  className="bg-slate-950 border border-slate-700 rounded px-2 py-1 outline-none text-slate-200 cursor-pointer"
                >
                  <option value={10}>10</option>
                  <option value={20}>20</option>
                  <option value={50}>50</option>
                </select>
              </div>
            </div>

            {/* Logs Table */}
            <div className="overflow-x-auto flex-1">
              <table className="w-full text-left text-sm whitespace-nowrap">
                <thead className="bg-slate-950/50 text-slate-500 text-[10px] uppercase font-semibold">
                  <tr>
                    <th className="px-4 py-3 w-24">Level</th>
                    <th className="px-4 py-3 w-48">Timestamp</th>
                    <th className="px-4 py-3 w-32">Source</th>
                    <th className="px-4 py-3">Message</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/50">
                  {paginatedLogs.map(log => (
                    <tr key={log.id} className="hover:bg-slate-800/30 transition-colors">
                      <td className="px-4 py-2.5">
                        <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-mono tracking-wider border ${getLevelColor(log.level)}`}>
                          {log.level}
                        </span>
                      </td>
                      <td className="px-4 py-2.5 font-mono text-xs text-slate-400">{log.timestamp}</td>
                      <td className="px-4 py-2.5">
                        <span className="inline-block px-2 py-0.5 rounded text-[10px] font-mono tracking-wider bg-sky-500/10 text-sky-300 border border-sky-500/20">
                          {log.source}
                        </span>
                      </td>
                      <td className={`px-4 py-2.5 text-sm truncate max-w-xl ${getMessageColor(log.level)}`}>
                        {log.message}
                      </td>
                    </tr>
                  ))}
                  {paginatedLogs.length === 0 && (
                    <tr>
                      <td colSpan={4} className="px-4 py-8 text-center text-slate-500">No logs match your filter criteria.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {/* Pagination Controls */}
            <div className="p-4 border-t border-slate-800 flex items-center justify-between bg-slate-950/30">
              <div className="text-xs text-slate-500 font-medium">
                Showing {Math.min(filteredLogs.length, (logPage - 1) * logPageSize + 1)} to {Math.min(filteredLogs.length, logPage * logPageSize)} of {filteredLogs.length} entries
              </div>
              <div className="flex gap-1">
                <button 
                  disabled={logPage === 1} 
                  onClick={() => setLogPage(p => p - 1)}
                  className="p-1 rounded bg-slate-800 text-slate-400 hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  <ChevronLeft size={16} />
                </button>
                <button 
                  disabled={logPage === totalLogPages} 
                  onClick={() => setLogPage(p => p + 1)}
                  className="p-1 rounded bg-slate-800 text-slate-400 hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  <ChevronRight size={16} />
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Tab 2: Audit Trail */}
        {activeTab === 'audit' && (
          <div className="flex flex-col flex-1">
            {/* Toolbar */}
            <div className="p-4 border-b border-slate-800 flex">
              <div className="relative w-full max-w-xl">
                <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                <input 
                  type="text" 
                  placeholder="Search user, action, or resource..." 
                  value={auditSearch}
                  onChange={(e) => setAuditSearch(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-700 focus:border-emerald-500 text-sm text-slate-200 py-2 pl-9 pr-3 rounded outline-none"
                />
              </div>
            </div>

            {/* Audit Table */}
            <div className="overflow-x-auto flex-1">
              <table className="w-full text-left text-sm whitespace-nowrap">
                <thead className="bg-slate-950/50 text-slate-500 text-[10px] uppercase font-semibold">
                  <tr>
                    <th className="px-4 py-3 w-48">Timestamp</th>
                    <th className="px-4 py-3 w-32">User</th>
                    <th className="px-4 py-3 w-48">Action</th>
                    <th className="px-4 py-3 w-32">Resource</th>
                    <th className="px-4 py-3 w-32">IP Address</th>
                    <th className="px-4 py-3">Details</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/50">
                  {filteredAudit.map(entry => (
                    <tr key={entry.id} className="hover:bg-slate-800/30 transition-colors">
                      <td className="px-4 py-3 font-mono text-xs text-slate-400">{entry.timestamp}</td>
                      <td className="px-4 py-3 font-mono text-sky-200">{entry.user}</td>
                      <td className="px-4 py-3">
                        <span className="inline-block px-2 py-0.5 rounded text-[10px] font-mono tracking-wider bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                          {entry.action}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-slate-300 font-mono text-xs">{entry.resource}</td>
                      <td className="px-4 py-3 text-slate-500 font-mono text-xs">{entry.ip}</td>
                      <td className="px-4 py-3 text-slate-400 text-xs">{entry.details}</td>
                    </tr>
                  ))}
                  {filteredAudit.length === 0 && (
                    <tr>
                      <td colSpan={6} className="px-4 py-8 text-center text-slate-500">No audit records match your search.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}
        
      </div>
    </AppShell>
  );
}
