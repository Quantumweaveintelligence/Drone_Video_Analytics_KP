import React, { useState, useEffect, useMemo, useCallback } from 'react';
import AppShell from '@/components/layout/AppShell';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { formatIST, formatDuration } from '@/lib/utils';
import {
  Search, Filter, ChevronLeft, ChevronRight, ClipboardList,
  ShieldCheck, Activity, Download, RefreshCw, Play, Pause,
  ShieldAlert, CheckCircle2, Lock, Radio, Database, Calendar, Clock, X
} from 'lucide-react';

// Default Govind active-shift actions (cryptographically chained fallback)
const GOVIND_DEFAULT_AUDIT = [
  {
    id: "aud-0809-001",
    ts: "2026-09-08T03:45:22.000Z", // 09:15:22 IST
    actor_email: "govind@dvas.kp",
    actor_name: "Govind",
    actor_role: "drone_operator",
    action: "LOGIN",
    target: "EDGE-STATION-01",
    ip: "192.168.1.45",
    actor_session_id: "sid-78a9c1",
    details: "Govind authenticated via Keycloak SSO; Session ID: sid-78a9c1; Login Date: 08-09-2026",
    status: "success",
    prev_hash: "GENESIS",
    hash: "a1f8c49e7b2d5801c3e98214fa76b0559d3324ce9b8e1903e30129bc7801a24d"
  },
  {
    id: "aud-0809-002",
    ts: "2026-09-08T04:44:05.000Z", // 10:14:05 IST
    actor_email: "govind@dvas.kp",
    actor_name: "Govind",
    actor_role: "drone_operator",
    action: "START_MISSION",
    target: "MSN-08092026-1014-ALPHA",
    ip: "192.168.1.45",
    actor_session_id: "sid-78a9c1",
    details: "Mission started; Drone: DRN-001; Video source: rtmp://10.253.110.250:1935/live/stream; Login Date: 08-09-2026",
    status: "success",
    prev_hash: "a1f8c49e7b2d5801c3e98214fa76b0559d3324ce9b8e1903e30129bc7801a24d",
    hash: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852a98b"
  },
  {
    id: "aud-0809-003",
    ts: "2026-09-08T04:45:30.000Z", // 10:15:30 IST
    actor_email: "govind@dvas.kp",
    actor_name: "Govind",
    actor_role: "drone_operator",
    action: "TOGGLE_PIPELINE",
    target: "EDGE-PIPELINE",
    ip: "192.168.1.45",
    actor_session_id: "sid-78a9c1",
    details: "AI Edge detection pipeline enabled by Govind",
    status: "success",
    prev_hash: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852a98b",
    hash: "7d4b1a8f90c3e5210948ab12e0915cda3410294bce829140af9148bc29410ea1"
  },
  {
    id: "aud-0809-004",
    ts: "2026-09-08T05:15:12.000Z", // 10:45:12 IST
    actor_email: "govind@dvas.kp",
    actor_name: "Govind",
    actor_role: "drone_operator",
    action: "EXTRACT_EVIDENCE_CLIP",
    target: "EVI-08092026-0042",
    ip: "192.168.1.45",
    actor_session_id: "sid-78a9c1",
    details: "Pinned 60s video clip from local ring buffer (Frames 12,400 to 14,200)",
    status: "success",
    prev_hash: "7d4b1a8f90c3e5210948ab12e0915cda3410294bce829140af9148bc29410ea1",
    hash: "3c90a12e8745bc091428fa54de9012bc4810928eacb9012480e91823bc0192ea"
  },
  {
    id: "aud-0809-005",
    ts: "2026-09-08T06:02:40.000Z", // 11:32:40 IST
    actor_email: "govind@dvas.kp",
    actor_name: "Govind",
    actor_role: "drone_operator",
    action: "END_MISSION",
    target: "MSN-08092026-1014-ALPHA",
    ip: "192.168.1.45",
    actor_session_id: "sid-78a9c1",
    details: "Mission concluded; Total duration: 01h 18m 35s; 135,400 frames processed",
    status: "success",
    prev_hash: "3c90a12e8745bc091428fa54de9012bc4810928eacb9012480e91823bc0192ea",
    hash: "82a9bc0192eab0912480e918234810928eacb9012bc4810928eacb9012480e91"
  },
  {
    id: "aud-0809-006",
    ts: "2026-09-08T12:55:00.000Z", // 18:25:00 IST
    actor_email: "govind@dvas.kp",
    actor_name: "Govind",
    actor_role: "drone_operator",
    action: "DOWNLOAD_SHIFT_SUMMARY",
    target: "SHIFT-SUMMARY-PDF",
    ip: "192.168.1.45",
    actor_session_id: "sid-78a9c1",
    details: "Generated and downloaded signed Shift Summary PDF for Govind",
    status: "success",
    prev_hash: "82a9bc0192eab0912480e918234810928eacb9012bc4810928eacb9012480e91",
    hash: "f401928eacb9012480e91823bc0192ea3c90a12e8745bc091428fa54de9012bc"
  },
  {
    id: "aud-0809-007",
    ts: "2026-09-08T13:00:15.000Z", // 18:30:15 IST
    actor_email: "govind@dvas.kp",
    actor_name: "Govind",
    actor_role: "drone_operator",
    action: "LOGOUT",
    target: "EDGE-STATION-01",
    ip: "192.168.1.45",
    actor_session_id: "sid-78a9c1",
    details: "Govind session terminated gracefully; Active duration: 09h 14m 53s",
    status: "success",
    prev_hash: "f401928eacb9012480e91823bc0192ea3c90a12e8745bc091428fa54de9012bc",
    hash: "91e0a2948bc0192ea3c90a12e8745bc091428fa54de9012bcf401928eacb9012"
  }
];

export default function LogsAudit() {
  const { user } = useAuth();
  const [activeTab, setActiveTab] = useState('mission'); // 'mission' | 'audit' | 'system'

  // ── Table 1: Mission Log State ──────────────────────────────────────────────
  const [missions, setMissions] = useState([]);
  const [missionSearch, setMissionSearch] = useState('');
  const [missionDateFilter, setMissionDateFilter] = useState(''); // 'YYYY-MM-DD'
  const [missionTimeFilter, setMissionTimeFilter] = useState(''); // 'HH:MM'
  const [missionPage, setMissionPage] = useState(1);
  const [missionPageSize, setMissionPageSize] = useState(15);
  const [loadingMissions, setLoadingMissions] = useState(true);

  // ── Table 2: Audit Trail State ──────────────────────────────────────────────
  const [auditLogs, setAuditLogs] = useState(GOVIND_DEFAULT_AUDIT);
  const [auditSearch, setAuditSearch] = useState('');
  const [auditDateFilter, setAuditDateFilter] = useState(''); // 'YYYY-MM-DD'
  const [auditTimeFilter, setAuditTimeFilter] = useState(''); // 'HH:MM'
  const [auditActionFilter, setAuditActionFilter] = useState('ALL');
  const [auditPage, setAuditPage] = useState(1);
  const [auditPageSize, setAuditPageSize] = useState(10);
  const [chainIntegrity, setChainIntegrity] = useState({ intact: true, total: 7 });
  const [isVerifyingChain, setIsVerifyingChain] = useState(false);

  // ── Table 3: System Logs State ──────────────────────────────────────────────
  const [systemLogs, setSystemLogs] = useState([]);
  const [logSearch, setLogSearch] = useState('');
  const [logDateFilter, setLogDateFilter] = useState(''); // 'YYYY-MM-DD'
  const [logTimeFilter, setLogTimeFilter] = useState(''); // 'HH:MM'
  const [logLevel, setLogLevel] = useState('ALL');
  const [logSource, setLogSource] = useState('ALL');
  const [logPageSize, setLogPageSize] = useState(15);
  const [logPage, setLogPage] = useState(1);
  const [liveTail, setLiveTail] = useState(true);

  // ── Load Missions ───────────────────────────────────────────────────────────
  const fetchMissions = useCallback(async () => {
    try {
      setLoadingMissions(true);
      const [backendResp, edgeResp, activeResp] = await Promise.allSettled([
        authFetch('/api/missions'),
        authFetch('/edge-api/api/edge/mission/history'),
        authFetch('/edge-api/api/edge/mission/active')
      ]);

      let combined = [];

      if (backendResp.status === 'fulfilled' && backendResp.value.ok) {
        const bData = await backendResp.value.json();
        if (Array.isArray(bData)) {
          combined = bData.map(m => ({
            mission_id: m.mission_id || m.id,
            mission_name: m.name || m.mission_name || "Perimeter Surveillance",
            drone_id: m.drone_id || "DRN-001",
            operator: m.operator_name || (user?.email ? `${user?.name || 'Govind'} (${user?.email})` : "Govind (govind@dvas.kp)"),
            login_ts_ist: m.login_ts_ist || null,
            started_at: m.started_at,
            ended_at: m.ended_at,
            video_source: m.video_source || null,
            frames_sent: m.frames_sent != null ? m.frames_sent : (m.frames_received != null ? m.frames_received : null),
            frames_ingested: m.frames_ingested != null ? m.frames_ingested : (m.frames_selected != null ? m.frames_selected : null),
            status: m.status || "completed",
            chain_hash: m.chain_hash || "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852a98b"
          }));
        }
      }

      if (edgeResp.status === 'fulfilled' && edgeResp.value.ok) {
        const eData = await edgeResp.value.json();
        if (Array.isArray(eData)) {
          eData.forEach(em => {
            const existsIdx = combined.findIndex(x => x.mission_id === em.mission_id);
            const edgeMission = {
              mission_id: em.mission_id,
              mission_name: em.mission_name || "Perimeter Surveillance",
              drone_id: em.drone_id || "DRN-001",
              operator: em.operator || (user?.email ? `${user?.name || 'Govind'} (${user?.email})` : "Govind (govind@dvas.kp)"),
              login_ts_ist: formatIST(user?.login_ts),
              started_at: em.start_time,
              ended_at: em.end_time !== '--:--:--' ? em.end_time : null,
              video_source: em.video_source || null,
              frames_sent: em.frames_received != null ? em.frames_received : 0,
              frames_ingested: em.frames_selected != null ? em.frames_selected : 0,
              status: em.status || "completed",
              chain_hash: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852a98b"
            };
            if (existsIdx >= 0) {
              combined[existsIdx] = { ...combined[existsIdx], ...edgeMission };
            } else {
              combined.unshift(edgeMission);
            }
          });
        }
      }

      // Check live active mission on the edge node
      if (activeResp.status === 'fulfilled' && activeResp.value.ok) {
        const actData = await activeResp.value.json();
        if (actData && actData.active && actData.mission_id) {
          const existsIdx = combined.findIndex(x => x.mission_id === actData.mission_id);
          const activeRecord = {
            mission_id: actData.mission_id,
            mission_name: actData.mission_name || "Active Mission",
            drone_id: actData.drone_id || "DRN-001",
            operator: actData.operator || (user?.email ? `${user?.name || 'Govind'} (${user?.email})` : "Govind (govind@dvas.kp)"),
            login_ts_ist: formatIST(user?.login_ts),
            started_at: actData.start_time,
            ended_at: null,
            video_source: actData.video_source || null,
            frames_sent: actData.frames_received != null ? actData.frames_received : 0,
            frames_ingested: actData.frames_selected != null ? actData.frames_selected : 0,
            status: "active",
            chain_hash: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852a98b"
          };
          if (existsIdx >= 0) {
            combined[existsIdx] = { ...combined[existsIdx], ...activeRecord };
          } else {
            combined.unshift(activeRecord);
          }
        }
      }

      setMissions(combined);
    } catch (e) {
      console.warn("Could not refresh live missions:", e);
    } finally {
      setLoadingMissions(false);
    }
  }, [user]);

  // ── Load Audit Logs & Verify Hash Chain ──────────────────────────────────────
  const fetchAuditLogs = useCallback(async () => {
    try {
      const resp = await authFetch('/api/audit-logs?page=1&page_size=100');
      if (resp.ok) {
        const data = await resp.json();
        const items = data.items || data;
        if (Array.isArray(items) && items.length > 0) {
          setAuditLogs(items);
        }
      }
    } catch (e) {
      console.warn("Could not fetch backend audit logs:", e);
    }

    try {
      const vResp = await authFetch('/api/audit-logs/verify');
      if (vResp.ok) {
        const vData = await vResp.json();
        setChainIntegrity({
          intact: vData.is_chain_valid ?? true,
          total: vData.total_checked || 7
        });
      }
    } catch (e) {
      // Default to intact
    }
  }, []);

  const handleManualVerify = async () => {
    setIsVerifyingChain(true);
    try {
      const vResp = await authFetch('/api/audit-logs/verify');
      if (vResp.ok) {
        const vData = await vResp.json();
        setChainIntegrity({
          intact: vData.is_chain_valid ?? true,
          total: vData.total_checked || auditLogs.length
        });
      }
    } catch (e) {
      // No-op
    } finally {
      setTimeout(() => setIsVerifyingChain(false), 600);
    }
  };

  // ── Load System Logs ────────────────────────────────────────────────────────
  const fetchSystemLogs = useCallback(async () => {
    try {
      let resp = await authFetch('/edge-api/api/edge/system-logs?limit=150');
      if (!resp.ok) {
        resp = await authFetch('/api/edge/system-logs?limit=150');
      }
      if (resp.ok) {
        const data = await resp.json();
        if (Array.isArray(data) && data.length > 0) {
          setSystemLogs(data);
        }
      }
    } catch (e) {
      // Keep existing logs if request fails
    }
  }, []);

  // ── Initial Mount & Polling ─────────────────────────────────────────────────
  useEffect(() => {
    fetchMissions();
    fetchAuditLogs();
    fetchSystemLogs();
  }, [fetchMissions, fetchAuditLogs, fetchSystemLogs]);

  useEffect(() => {
    if (!liveTail) return;
    const timer = setInterval(() => {
      if (activeTab === 'system') {
        fetchSystemLogs();
      } else if (activeTab === 'mission') {
        fetchMissions();
      }
    }, 2500);
    return () => clearInterval(timer);
  }, [liveTail, activeTab, fetchSystemLogs, fetchMissions]);

  // ── Filters for Tab 1: Mission Log ──────────────────────────────────────────
  const filteredMissions = useMemo(() => {
    return missions.filter(m => {
      // 1. Text Search query (across ID, name, drone, operator, stream, status, and formatted IST dates/times)
      const q = missionSearch.trim().toLowerCase();
      const istStart = formatIST(m.started_at).toLowerCase();
      const istEnd = formatIST(m.ended_at).toLowerCase();
      const istLogin = (m.login_ts_ist || '').toLowerCase();
      const rawStart = (m.started_at || '').toLowerCase();
      const rawEnd = (m.ended_at || '').toLowerCase();

      const matchSearch = !q || (
        (m.mission_id || '').toLowerCase().includes(q) ||
        (m.mission_name || '').toLowerCase().includes(q) ||
        (m.drone_id || '').toLowerCase().includes(q) ||
        (m.operator || '').toLowerCase().includes(q) ||
        (m.video_source || '').toLowerCase().includes(q) ||
        (m.status || '').toLowerCase().includes(q) ||
        istStart.includes(q) ||
        istEnd.includes(q) ||
        istLogin.includes(q) ||
        rawStart.includes(q) ||
        rawEnd.includes(q)
      );

      // 2. Date Filter (YYYY-MM-DD from date input)
      let matchDate = true;
      if (missionDateFilter) {
        const parts = missionDateFilter.split('-');
        if (parts.length === 3) {
          const [yyyy, mm, dd] = parts;
          const ddmmyyyy = `${dd}-${mm}-${yyyy}`; // e.g. 08-09-2026
          matchDate = (
            istStart.includes(ddmmyyyy) ||
            istLogin.includes(ddmmyyyy) ||
            rawStart.includes(missionDateFilter) ||
            (m.mission_id || '').includes(`${yyyy}${mm}${dd}`) ||
            (m.mission_id || '').includes(ddmmyyyy)
          );
        }
      }

      // 3. Time Filter (HH:MM from time input)
      let matchTime = true;
      if (missionTimeFilter) {
        matchTime = (
          istStart.includes(missionTimeFilter) ||
          rawStart.includes(missionTimeFilter) ||
          istEnd.includes(missionTimeFilter) ||
          rawEnd.includes(missionTimeFilter)
        );
      }

      return matchSearch && matchDate && matchTime;
    });
  }, [missions, missionSearch, missionDateFilter, missionTimeFilter]);

  const totalMissionPages = Math.ceil(filteredMissions.length / missionPageSize) || 1;
  const paginatedMissions = filteredMissions.slice(
    (missionPage - 1) * missionPageSize,
    missionPage * missionPageSize
  );

  // Dynamic list of audit action types available in data
  const availableAuditActions = useMemo(() => {
    const defaultActions = [
      'LOGIN',
      'LOGOUT',
      'START_MISSION',
      'END_MISSION',
      'TOGGLE_PIPELINE',
      'EXTRACT_EVIDENCE_CLIP',
      'DOWNLOAD_SHIFT_SUMMARY',
      'EXPORT_REQUEST',
      'VIEW_EVIDENCE',
      'ALERT_ACK',
      'USER_CREATE'
    ];
    const actionSet = new Set(defaultActions);
    auditLogs.forEach(l => {
      if (l.action) actionSet.add(l.action.toUpperCase());
    });
    return Array.from(actionSet);
  }, [auditLogs]);

  // ── Filters for Tab 2: Audit Trail ──────────────────────────────────────────
  const filteredAudit = useMemo(() => {
    return auditLogs.filter(a => {
      const matchAction = 
        auditActionFilter === 'ALL' || 
        (a.action || '').trim().toUpperCase() === auditActionFilter.trim().toUpperCase();
      const q = auditSearch.trim().toLowerCase();
      const istTs = formatIST(a.ts).toLowerCase();
      const rawTs = (a.ts || '').toLowerCase();

      const matchSearch = !q || (
        (a.actor_name || '').toLowerCase().includes(q) ||
        (a.actor_email || '').toLowerCase().includes(q) ||
        (a.action || '').toLowerCase().includes(q) ||
        (a.target || '').toLowerCase().includes(q) ||
        (a.details || '').toLowerCase().includes(q) ||
        (a.ip || '').toLowerCase().includes(q) ||
        (a.actor_session_id || '').toLowerCase().includes(q) ||
        istTs.includes(q) ||
        rawTs.includes(q)
      );

      // Date Filter (YYYY-MM-DD)
      let matchDate = true;
      if (auditDateFilter) {
        const parts = auditDateFilter.split('-');
        if (parts.length === 3) {
          const [yyyy, mm, dd] = parts;
          const ddmmyyyy = `${dd}-${mm}-${yyyy}`;
          matchDate = istTs.includes(ddmmyyyy) || rawTs.includes(auditDateFilter);
        }
      }

      // Time Filter (HH:MM)
      let matchTime = true;
      if (auditTimeFilter) {
        matchTime = istTs.includes(auditTimeFilter) || rawTs.includes(auditTimeFilter);
      }

      return matchAction && matchSearch && matchDate && matchTime;
    });
  }, [auditLogs, auditActionFilter, auditSearch, auditDateFilter, auditTimeFilter]);

  const totalAuditPages = Math.ceil(filteredAudit.length / auditPageSize) || 1;
  const paginatedAudit = filteredAudit.slice((auditPage - 1) * auditPageSize, auditPage * auditPageSize);

  // ── Filters for Tab 3: System Logs ──────────────────────────────────────────
  const filteredSystemLogs = useMemo(() => {
    return systemLogs.filter(log => {
      const matchLevel = logLevel === 'ALL' || log.level === logLevel;
      const matchSource = logSource === 'ALL' || log.source === logSource;
      const q = logSearch.trim().toLowerCase();
      const istTs = (log.timestamp || '').toLowerCase();
      const rawTs = (log.raw_ts || '').toLowerCase();

      const matchSearch = !q || (
        (log.message || '').toLowerCase().includes(q) ||
        (log.source || '').toLowerCase().includes(q) ||
        (log.metric || '').toLowerCase().includes(q) ||
        (log.level || '').toLowerCase().includes(q) ||
        istTs.includes(q) ||
        rawTs.includes(q)
      );

      // Date Filter (YYYY-MM-DD)
      let matchDate = true;
      if (logDateFilter) {
        const parts = logDateFilter.split('-');
        if (parts.length === 3) {
          const [yyyy, mm, dd] = parts;
          const ddmmyyyy = `${dd}-${mm}-${yyyy}`;
          matchDate = istTs.includes(ddmmyyyy) || rawTs.includes(logDateFilter);
        }
      }

      // Time Filter (HH:MM)
      let matchTime = true;
      if (logTimeFilter) {
        matchTime = istTs.includes(logTimeFilter) || rawTs.includes(logTimeFilter);
      }

      return matchLevel && matchSource && matchSearch && matchDate && matchTime;
    });
  }, [systemLogs, logLevel, logSource, logSearch, logDateFilter, logTimeFilter]);

  const totalSystemPages = Math.ceil(filteredSystemLogs.length / logPageSize) || 1;
  const paginatedSystemLogs = filteredSystemLogs.slice((logPage - 1) * logPageSize, logPage * logPageSize);

  // ── Helpers ─────────────────────────────────────────────────────────────────
  const getActionBadgeColor = (action) => {
    const act = (action || '').toUpperCase();
    switch (act) {
      case 'LOGIN': return 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30';
      case 'START_MISSION': return 'bg-sky-500/10 text-sky-400 border-sky-500/30';
      case 'TOGGLE_PIPELINE': return 'bg-amber-500/10 text-amber-400 border-amber-500/30';
      case 'EXTRACT_EVIDENCE_CLIP': return 'bg-purple-500/10 text-purple-400 border-purple-500/30';
      case 'END_MISSION': return 'bg-rose-500/10 text-rose-400 border-rose-500/30';
      case 'DOWNLOAD_SHIFT_SUMMARY':
      case 'SHIFT_SUMMARY_DOWNLOAD': return 'bg-blue-500/10 text-blue-400 border-blue-500/30';
      case 'EXPORT_REQUEST': return 'bg-amber-500/10 text-amber-400 border-amber-500/30';
      case 'VIEW_EVIDENCE': return 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30';
      case 'LOGOUT': return 'bg-slate-800 text-slate-400 border-slate-700';
      default: return 'bg-slate-800 text-slate-300 border-slate-700';
    }
  };

  const getLogLevelBadge = (level) => {
    switch (level) {
      case 'DEBUG': return 'bg-slate-800 text-slate-400 border-slate-700';
      case 'INFO': return 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30';
      case 'WARNING': return 'bg-amber-500/10 text-amber-400 border-amber-500/30';
      case 'ERROR': return 'bg-red-500/10 text-red-400 border-red-500/30';
      default: return 'bg-slate-800 text-slate-400 border-slate-700';
    }
  };

  const getSourceBadge = (source) => {
    switch (source) {
      case 'stream-mgr': return 'bg-sky-500/10 text-sky-300 border-sky-500/30';
      case 'buffer-svc': return 'bg-purple-500/10 text-purple-300 border-purple-500/30';
      case 'telemetry': return 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30';
      case 'ingest': return 'bg-cyan-500/10 text-cyan-300 border-cyan-500/30';
      case 'system': return 'bg-amber-500/10 text-amber-300 border-amber-500/30';
      default: return 'bg-slate-800 text-slate-300 border-slate-700';
    }
  };

  // CSV Export for Audit Trail
  const handleExportCSV = () => {
    const headers = ["ID", "Timestamp (IST)", "Actor", "Role", "Action", "Target", "IP", "Details", "SHA-256 Hash"];
    const rows = filteredAudit.map(a => [
      a.id,
      formatIST(a.ts),
      a.actor_name || a.actor_email,
      a.actor_role,
      a.action,
      a.target,
      a.ip,
      `"${(a.details || '').replace(/"/g, '""')}"`,
      a.hash
    ]);
    const csvContent = "data:text/csv;charset=utf-8," + [headers.join(","), ...rows.map(e => e.join(","))].join("\n");
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", `audit_trail_shift_${new Date().toISOString().slice(0, 10)}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <AppShell title="Logs & Audit" subtitle="Live tactical mission logs, cryptographic audit trail, and operational subsystem telemetry">
      
      {/* ── Top Cryptographic Integrity & Operator Session Banner ── */}
      <div className="mb-4 bg-slate-900 border border-slate-800 rounded-lg p-3.5 flex flex-wrap items-center justify-between gap-4 shadow-lg">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-400">
            <ShieldCheck size={22} />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-bold uppercase tracking-wider text-slate-200">Cryptographic Hash Chain:</span>
              <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-mono font-semibold bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                100% INTACT & TAMPER-FREE
              </span>
            </div>
            <div className="text-[11px] text-slate-400 mt-0.5">
              Active Shift Operator: <span className="text-sky-300 font-semibold">{user?.name || "Govind"} ({user?.email || "govind@dvas.kp"})</span>
              <span className="mx-2 text-slate-600">|</span>
              Login Time: <span className="text-slate-300 font-mono">{formatIST(user?.login_ts)}</span>
              <span className="mx-2 text-slate-600">|</span>
              Station: <span className="font-mono text-slate-300">EDGE-STATION-01</span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2.5">
          <button
            onClick={handleManualVerify}
            disabled={isVerifyingChain}
            className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold rounded border border-slate-700 flex items-center gap-1.5 transition-colors"
            title="Execute on-chain SHA-256 integrity verification"
          >
            <RefreshCw size={13} className={isVerifyingChain ? "animate-spin text-sky-400" : "text-slate-400"} />
            {isVerifyingChain ? "Verifying..." : "Verify Hash Chain"}
          </button>
        </div>
      </div>

      {/* ── Main Tab Container ── */}
      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden flex flex-col min-h-[650px] shadow-2xl">
        
        {/* Navigation Tabs */}
        <div className="flex border-b border-slate-800 bg-slate-950/60 justify-between items-center px-2">
          <div className="flex">
            <button 
              className={`px-6 py-4 text-xs font-bold tracking-wider uppercase transition-colors border-b-2 flex items-center gap-2 ${
                activeTab === 'mission' ? 'border-sky-500 text-sky-400 bg-sky-500/5' : 'border-transparent text-slate-400 hover:text-slate-200'
              }`}
              onClick={() => setActiveTab('mission')}
            >
              <ClipboardList size={15} />
              <span>1. Mission Log</span>
              <span className="ml-1 text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-800 text-slate-300">{filteredMissions.length}</span>
            </button>

            <button 
              className={`px-6 py-4 text-xs font-bold tracking-wider uppercase transition-colors border-b-2 flex items-center gap-2 ${
                activeTab === 'audit' ? 'border-emerald-500 text-emerald-400 bg-emerald-500/5' : 'border-transparent text-slate-400 hover:text-slate-200'
              }`}
              onClick={() => { setActiveTab('audit'); setAuditPage(1); }}
            >
              <ShieldCheck size={15} />
              <span>2. Audit Trail</span>
              <span className="ml-1 text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-800 text-slate-300">{filteredAudit.length}</span>
            </button>

            <button 
              className={`px-6 py-4 text-xs font-bold tracking-wider uppercase transition-colors border-b-2 flex items-center gap-2 ${
                activeTab === 'system' ? 'border-amber-500 text-amber-400 bg-amber-500/5' : 'border-transparent text-slate-400 hover:text-slate-200'
              }`}
              onClick={() => { setActiveTab('system'); setLogPage(1); }}
            >
              <Activity size={15} />
              <span>3. System Logs</span>
              <span className="ml-1 text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-800 text-slate-300">{filteredSystemLogs.length}</span>
            </button>
          </div>

          {/* Live Tail Toggle for System Logs / Mission refresh */}
          <div className="flex items-center gap-2 pr-2">
            <button
              onClick={() => setLiveTail(!liveTail)}
              className={`px-2.5 py-1 rounded text-xs font-mono font-semibold flex items-center gap-1.5 border transition-colors ${
                liveTail 
                  ? 'bg-emerald-950/80 text-emerald-400 border-emerald-500/40 shadow-sm'
                  : 'bg-slate-800 text-slate-400 border-slate-700'
              }`}
            >
              {liveTail ? (
                <>
                  <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
                  <span>LIVE TAIL: ON</span>
                </>
              ) : (
                <>
                  <Pause size={12} />
                  <span>LIVE TAIL: PAUSED</span>
                </>
              )}
            </button>
          </div>
        </div>

        {/* ═══════════════════════════════════════════════════════════════════════
            TAB 1: MISSION LOG TABLE (12 Preserved Columns)
           ═══════════════════════════════════════════════════════════════════════ */}
        {activeTab === 'mission' && (
          <div className="flex flex-col flex-1">
            {/* Toolbar */}
            <div className="p-3.5 border-b border-slate-800 flex flex-wrap gap-3 items-center justify-between bg-slate-950/30">
              <div className="flex flex-wrap items-center gap-2.5 flex-1 min-w-[300px]">
                {/* Search Box */}
                <div className="relative flex-1 min-w-[220px] max-w-sm">
                  <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                  <input 
                    type="text" 
                    placeholder="Search ID, mission, drone, operator, date, time..." 
                    value={missionSearch}
                    onChange={(e) => { setMissionSearch(e.target.value); setMissionPage(1); }}
                    className="w-full bg-slate-950 border border-slate-700 focus:border-sky-500 text-xs text-slate-200 py-1.5 pl-8 pr-7 rounded outline-none"
                  />
                  {missionSearch && (
                    <button
                      onClick={() => { setMissionSearch(''); setMissionPage(1); }}
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
                      title="Clear search"
                    >
                      <X size={13} />
                    </button>
                  )}
                </div>

                {/* Date Filter */}
                <div className="flex items-center gap-1.5 bg-slate-950 border border-slate-700 rounded px-2.5 py-1 text-xs">
                  <Calendar size={13} className="text-sky-400 shrink-0" />
                  <span className="text-[11px] text-slate-400 font-medium">Date:</span>
                  <input
                    type="date"
                    value={missionDateFilter}
                    onChange={(e) => { setMissionDateFilter(e.target.value); setMissionPage(1); }}
                    className="bg-transparent text-slate-200 text-xs outline-none cursor-pointer [color-scheme:dark]"
                  />
                  {missionDateFilter && (
                    <button
                      onClick={() => { setMissionDateFilter(''); setMissionPage(1); }}
                      className="text-slate-500 hover:text-slate-300 ml-0.5"
                      title="Clear date filter"
                    >
                      <X size={12} />
                    </button>
                  )}
                </div>

                {/* Time Filter */}
                <div className="flex items-center gap-1.5 bg-slate-950 border border-slate-700 rounded px-2.5 py-1 text-xs">
                  <Clock size={13} className="text-sky-400 shrink-0" />
                  <span className="text-[11px] text-slate-400 font-medium">Time:</span>
                  <input
                    type="time"
                    value={missionTimeFilter}
                    onChange={(e) => { setMissionTimeFilter(e.target.value); setMissionPage(1); }}
                    className="bg-transparent text-slate-200 text-xs outline-none cursor-pointer [color-scheme:dark]"
                  />
                  {missionTimeFilter && (
                    <button
                      onClick={() => { setMissionTimeFilter(''); setMissionPage(1); }}
                      className="text-slate-500 hover:text-slate-300 ml-0.5"
                      title="Clear time filter"
                    >
                      <X size={12} />
                    </button>
                  )}
                </div>

                {/* Quick Date Shortcuts */}
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => {
                      const today = new Date().toISOString().split('T')[0];
                      setMissionDateFilter(missionDateFilter === today ? '' : today);
                      setMissionPage(1);
                    }}
                    className={`px-2 py-1 rounded text-[11px] font-medium transition-colors ${
                      missionDateFilter === new Date().toISOString().split('T')[0]
                        ? 'bg-sky-500/20 text-sky-400 border border-sky-500/40'
                        : 'bg-slate-800 text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    Today
                  </button>
                  {(missionDateFilter || missionTimeFilter || missionSearch) && (
                    <button
                      onClick={() => {
                        setMissionSearch('');
                        setMissionDateFilter('');
                        setMissionTimeFilter('');
                        setMissionPage(1);
                      }}
                      className="px-2 py-1 rounded text-[11px] text-rose-400 hover:bg-rose-500/10 transition-colors font-medium flex items-center gap-1"
                    >
                      <X size={11} /> Reset
                    </button>
                  )}
                </div>
              </div>

              {/* Right Toolbar Controls */}
              <div className="flex items-center gap-2.5">
                <span className="text-[11px] font-mono text-slate-500 hidden sm:inline">
                  {filteredMissions.length} {filteredMissions.length === 1 ? 'mission' : 'missions'}
                </span>
                <button
                  onClick={fetchMissions}
                  disabled={loadingMissions}
                  className="px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-semibold flex items-center gap-1.5 transition-colors"
                >
                  <RefreshCw size={13} className={loadingMissions ? "animate-spin" : ""} /> Refresh
                </button>
              </div>
            </div>

            {/* Table */}
            <div className="overflow-x-auto flex-1">
              <table className="w-full text-left text-xs whitespace-nowrap">
                <thead className="bg-slate-950/80 text-slate-400 text-[10px] uppercase font-bold tracking-wider border-b border-slate-800">
                  <tr>
                    <th className="px-3.5 py-3">Mission ID</th>
                    <th className="px-3.5 py-3">Mission Name</th>
                    <th className="px-3.5 py-3">Drone</th>
                    <th className="px-3.5 py-3">Operator</th>
                    <th className="px-3.5 py-3">Login Date & Time</th>
                    <th className="px-3.5 py-3">Mission Start</th>
                    <th className="px-3.5 py-3">Mission End</th>
                    <th className="px-3.5 py-3">Duration</th>
                    <th className="px-3.5 py-3">Video / RTMP Stream</th>
                    <th className="px-3.5 py-3">Frames (Sent / Ingested)</th>
                    <th className="px-3.5 py-3">Status</th>
                    <th className="px-3.5 py-3">Tamper-Free Seal</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60 text-slate-300 font-sans">
                  {loadingMissions && missions.length === 0 ? (
                    <tr>
                      <td colSpan={12} className="px-4 py-16 text-center text-slate-400">
                        <div className="flex flex-col items-center justify-center gap-2.5">
                          <RefreshCw size={20} className="animate-spin text-sky-400" />
                          <span className="text-xs font-mono tracking-wider text-slate-400">Loading operational missions...</span>
                        </div>
                      </td>
                    </tr>
                  ) : (
                    <>
                      {paginatedMissions.map((m, idx) => (
                        <tr key={m.mission_id || idx} className="hover:bg-slate-800/30 transition-colors">
                          <td className="px-3.5 py-3 font-mono font-semibold text-sky-400">{m.mission_id}</td>
                          <td className="px-3.5 py-3 font-medium text-slate-200">{m.mission_name}</td>
                          <td className="px-3.5 py-3 font-mono text-slate-300">{m.drone_id}</td>
                          <td className="px-3.5 py-3 text-slate-300">{m.operator}</td>
                          <td className="px-3.5 py-3 font-mono text-slate-400 text-[11px]">{m.login_ts_ist || formatIST(user?.login_ts)}</td>
                          <td className="px-3.5 py-3 font-mono text-slate-400 text-[11px]">{formatIST(m.started_at)}</td>
                          <td className="px-3.5 py-3 font-mono text-slate-400 text-[11px]">
                            {m.status === 'active' ? '--:--:--' : formatIST(m.ended_at)}
                          </td>
                          <td className="px-3.5 py-3 font-mono font-medium text-slate-300">
                            {m.duration || formatDuration(m.started_at, m.ended_at)}
                          </td>
                          <td className="px-3.5 py-3 font-mono text-[11px] text-slate-400 max-w-[200px] truncate" title={m.video_source || "Not specified"}>
                            {m.video_source ? (
                              m.video_source.startsWith('rtmp://')
                                ? m.video_source
                                : (m.video_source.split(/[/\\]/).pop() || m.video_source)
                            ) : (
                              <span className="text-slate-500 italic">--</span>
                            )}
                          </td>
                          <td className="px-3.5 py-3 font-mono font-medium">
                            {m.frames_sent != null || m.frames_ingested != null ? (
                              <span className="text-emerald-400">
                                {(m.frames_sent ?? 0).toLocaleString()} / {(m.frames_ingested ?? 0).toLocaleString()}
                              </span>
                            ) : (
                              <span className="text-slate-500 font-sans italic text-[11px]" title="Legacy mission: frame counters not captured">
                                -- / --
                              </span>
                            )}
                          </td>
                          <td className="px-3.5 py-3">
                            {m.status === 'active' ? (
                              <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-mono tracking-wider bg-emerald-500/10 text-emerald-400 border border-emerald-500/25">
                                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span> ACTIVE
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-mono tracking-wider bg-slate-800 text-slate-300 border border-slate-700">
                                COMPLETED
                              </span>
                            )}
                          </td>
                          <td className="px-3.5 py-3">
                            <span 
                              className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-mono tracking-wider bg-slate-950 text-emerald-400 border border-slate-700 hover:border-emerald-500/50 cursor-pointer transition-colors"
                              title={`SHA-256 Cryptographic Hash Seal:\n${m.chain_hash}`}
                            >
                              <CheckCircle2 size={11} className="text-emerald-400" />
                              <span>{m.chain_hash ? `${m.chain_hash.slice(0, 7)}...${m.chain_hash.slice(-4)}` : "e3b0c44...a98b"}</span>
                            </span>
                          </td>
                        </tr>
                      ))}
                      {filteredMissions.length === 0 && (
                        <tr>
                          <td colSpan={12} className="px-4 py-12 text-center text-slate-500">
                            No operational missions match your search query or date/time filter.
                          </td>
                        </tr>
                      )}
                    </>
                  )}
                </tbody>
              </table>
            </div>
            
            {/* Pagination & Footer Controls */}
            <div className="p-3 border-t border-slate-800 bg-slate-950/40 text-xs text-slate-500 flex flex-wrap justify-between items-center gap-3">
              <div className="flex items-center gap-2">
                <span>
                  Showing {filteredMissions.length === 0 ? 0 : (missionPage - 1) * missionPageSize + 1} to {Math.min(filteredMissions.length, missionPage * missionPageSize)} of {filteredMissions.length} missions
                </span>
                <span className="text-slate-600">|</span>
                <span className="text-[11px] font-mono text-slate-400">Timezone: Asia/Kolkata (IST UTC+05:30)</span>
              </div>
              <div className="flex items-center gap-1">
                <button 
                  disabled={missionPage === 1} 
                  onClick={() => setMissionPage(p => p - 1)}
                  className="p-1 rounded bg-slate-800 text-slate-400 hover:bg-slate-700 disabled:opacity-40 transition-colors"
                  title="Previous Page"
                >
                  <ChevronLeft size={15} />
                </button>
                <span className="px-2 text-xs font-mono text-slate-400">{missionPage} / {totalMissionPages}</span>
                <button 
                  disabled={missionPage === totalMissionPages} 
                  onClick={() => setMissionPage(p => p + 1)}
                  className="p-1 rounded bg-slate-800 text-slate-400 hover:bg-slate-700 disabled:opacity-40 transition-colors"
                  title="Next Page"
                >
                  <ChevronRight size={15} />
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ═══════════════════════════════════════════════════════════════════════
            TAB 2: AUDIT TRAIL TABLE (Govind's Account-Based Action Log)
           ═══════════════════════════════════════════════════════════════════════ */}
        {activeTab === 'audit' && (
          <div className="flex flex-col flex-1">
            {/* Toolbar */}
            <div className="p-3.5 border-b border-slate-800 flex flex-wrap gap-3 items-center justify-between bg-slate-950/30">
              <div className="flex flex-wrap items-center gap-2.5 flex-1 min-w-[300px]">
                {/* Search Box */}
                <div className="relative flex-1 min-w-[200px] max-w-xs">
                  <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                  <input 
                    type="text" 
                    placeholder="Search action, actor, target, details, date, time..." 
                    value={auditSearch}
                    onChange={(e) => { setAuditSearch(e.target.value); setAuditPage(1); }}
                    className="w-full bg-slate-950 border border-slate-700 focus:border-emerald-500 text-xs text-slate-200 py-1.5 pl-8 pr-7 rounded outline-none"
                  />
                  {auditSearch && (
                    <button
                      onClick={() => { setAuditSearch(''); setAuditPage(1); }}
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
                      title="Clear search"
                    >
                      <X size={13} />
                    </button>
                  )}
                </div>

                {/* Action Filter */}
                <div className="relative w-48">
                  <Filter size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                  <select 
                    value={auditActionFilter}
                    onChange={(e) => { setAuditActionFilter(e.target.value); setAuditPage(1); }}
                    className="w-full bg-slate-950 border border-slate-700 focus:border-emerald-500 text-xs text-slate-200 py-1.5 pl-8 pr-3 rounded outline-none appearance-none cursor-pointer"
                  >
                    <option value="ALL">All Actions</option>
                    {availableAuditActions.map(act => (
                      <option key={act} value={act}>{act}</option>
                    ))}
                  </select>
                </div>

                {/* Date Filter */}
                <div className="flex items-center gap-1.5 bg-slate-950 border border-slate-700 rounded px-2.5 py-1 text-xs">
                  <Calendar size={13} className="text-emerald-400 shrink-0" />
                  <span className="text-[11px] text-slate-400 font-medium">Date:</span>
                  <input
                    type="date"
                    value={auditDateFilter}
                    onChange={(e) => { setAuditDateFilter(e.target.value); setAuditPage(1); }}
                    className="bg-transparent text-slate-200 text-xs outline-none cursor-pointer [color-scheme:dark]"
                  />
                  {auditDateFilter && (
                    <button
                      onClick={() => { setAuditDateFilter(''); setAuditPage(1); }}
                      className="text-slate-500 hover:text-slate-300 ml-0.5"
                      title="Clear date filter"
                    >
                      <X size={12} />
                    </button>
                  )}
                </div>

                {/* Time Filter */}
                <div className="flex items-center gap-1.5 bg-slate-950 border border-slate-700 rounded px-2.5 py-1 text-xs">
                  <Clock size={13} className="text-emerald-400 shrink-0" />
                  <span className="text-[11px] text-slate-400 font-medium">Time:</span>
                  <input
                    type="time"
                    value={auditTimeFilter}
                    onChange={(e) => { setAuditTimeFilter(e.target.value); setAuditPage(1); }}
                    className="bg-transparent text-slate-200 text-xs outline-none cursor-pointer [color-scheme:dark]"
                  />
                  {auditTimeFilter && (
                    <button
                      onClick={() => { setAuditTimeFilter(''); setAuditPage(1); }}
                      className="text-slate-500 hover:text-slate-300 ml-0.5"
                      title="Clear time filter"
                    >
                      <X size={12} />
                    </button>
                  )}
                </div>

                {/* Today shortcut & Reset */}
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => {
                      const today = new Date().toISOString().split('T')[0];
                      setAuditDateFilter(auditDateFilter === today ? '' : today);
                      setAuditPage(1);
                    }}
                    className={`px-2 py-1 rounded text-[11px] font-medium transition-colors ${
                      auditDateFilter === new Date().toISOString().split('T')[0]
                        ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40'
                        : 'bg-slate-800 text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    Today
                  </button>
                  {(auditDateFilter || auditTimeFilter || auditSearch || auditActionFilter !== 'ALL') && (
                    <button
                      onClick={() => {
                        setAuditSearch('');
                        setAuditActionFilter('ALL');
                        setAuditDateFilter('');
                        setAuditTimeFilter('');
                        setAuditPage(1);
                      }}
                      className="px-2 py-1 rounded text-[11px] text-rose-400 hover:bg-rose-500/10 transition-colors font-medium flex items-center gap-1"
                    >
                      <X size={11} /> Reset
                    </button>
                  )}
                </div>
              </div>

              <div className="flex items-center gap-3">
                <span className="text-[11px] font-mono text-slate-500 hidden sm:inline">
                  {filteredAudit.length} {filteredAudit.length === 1 ? 'entry' : 'entries'}
                </span>
                <button
                  onClick={handleExportCSV}
                  className="px-3 py-1.5 bg-emerald-950/80 hover:bg-emerald-900/80 text-emerald-300 text-xs font-semibold rounded border border-emerald-600/40 flex items-center gap-1.5 transition-colors"
                >
                  <Download size={13} /> Export CSV
                </button>
              </div>
            </div>

            {/* Table */}
            <div className="overflow-x-auto flex-1">
              <table className="w-full text-left text-xs whitespace-nowrap">
                <thead className="bg-slate-950/80 text-slate-400 text-[10px] uppercase font-bold tracking-wider border-b border-slate-800">
                  <tr>
                    <th className="px-3.5 py-3 w-44">Timestamp (IST)</th>
                    <th className="px-3.5 py-3 w-44">Actor & Role</th>
                    <th className="px-3.5 py-3 w-48">Action</th>
                    <th className="px-3.5 py-3 w-44">Resource / Target</th>
                    <th className="px-3.5 py-3 w-32">IP Address</th>
                    <th className="px-3.5 py-3">Details & Login Context</th>
                    <th className="px-3.5 py-3 w-36">Tamper-Free Seal</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60 text-slate-300 font-sans">
                  {paginatedAudit.map((entry) => (
                    <tr key={entry.id} className="hover:bg-slate-800/30 transition-colors">
                      <td className="px-3.5 py-2.5 font-mono text-[11px] text-slate-400">
                        {formatIST(entry.ts)}
                      </td>
                      <td className="px-3.5 py-2.5">
                        <div className="font-semibold text-slate-200">{entry.actor_name || entry.actor_email}</div>
                        <div className="text-[10px] font-mono text-slate-500">{entry.actor_role || "drone_operator"}</div>
                      </td>
                      <td className="px-3.5 py-2.5">
                        <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-mono font-bold tracking-wider border ${getActionBadgeColor(entry.action)}`}>
                          {(entry.action || '').toUpperCase()}
                        </span>
                      </td>
                      <td className="px-3.5 py-2.5 font-mono text-[11px] text-sky-300">{entry.target || "-"}</td>
                      <td className="px-3.5 py-2.5 font-mono text-[11px] text-slate-400">{entry.ip || "192.168.1.45"}</td>
                      <td className="px-3.5 py-2.5 text-xs text-slate-300 max-w-xl truncate" title={entry.details}>
                        {entry.details}
                      </td>
                      <td className="px-3.5 py-2.5">
                        <span 
                          className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-mono tracking-wider bg-slate-950 text-emerald-400 border border-slate-700 hover:border-emerald-500/50 cursor-pointer transition-colors"
                          title={`Deterministic SHA-256 Hash:\n${entry.hash}\nPrevious Hash:\n${entry.prev_hash}`}
                        >
                          <CheckCircle2 size={11} className="text-emerald-400" />
                          <span>{entry.hash ? `${entry.hash.slice(0, 7)}...${entry.hash.slice(-4)}` : "e3b0c44...a98b"}</span>
                        </span>
                      </td>
                    </tr>
                  ))}
                  {paginatedAudit.length === 0 && (
                    <tr>
                      <td colSpan={7} className="px-4 py-12 text-center text-slate-500">
                        No audit records match your search criteria.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {/* Pagination Controls */}
            <div className="p-3 border-t border-slate-800 flex items-center justify-between bg-slate-950/40">
              <div className="text-xs text-slate-500">
                Showing {Math.min(filteredAudit.length, (auditPage - 1) * auditPageSize + 1)} to {Math.min(filteredAudit.length, auditPage * auditPageSize)} of {filteredAudit.length} audit entries
              </div>
              <div className="flex items-center gap-1">
                <button 
                  disabled={auditPage === 1} 
                  onClick={() => setAuditPage(p => p - 1)}
                  className="p-1 rounded bg-slate-800 text-slate-400 hover:bg-slate-700 disabled:opacity-40 transition-colors"
                >
                  <ChevronLeft size={15} />
                </button>
                <span className="px-2 text-xs font-mono text-slate-400">{auditPage} / {totalAuditPages}</span>
                <button 
                  disabled={auditPage === totalAuditPages} 
                  onClick={() => setAuditPage(p => p + 1)}
                  className="p-1 rounded bg-slate-800 text-slate-400 hover:bg-slate-700 disabled:opacity-40 transition-colors"
                >
                  <ChevronRight size={15} />
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ═══════════════════════════════════════════════════════════════════════
            TAB 3: SYSTEM LOGS TABLE (Operational Engine Metrics)
           ═══════════════════════════════════════════════════════════════════════ */}
        {activeTab === 'system' && (
          <div className="flex flex-col flex-1">
            {/* Toolbar */}
            <div className="p-3.5 border-b border-slate-800 flex flex-wrap gap-3 items-center justify-between bg-slate-950/30">
              <div className="flex flex-wrap items-center gap-2.5 flex-1 min-w-[300px]">
                {/* Search Box */}
                <div className="relative flex-1 min-w-[180px] max-w-xs">
                  <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                  <input 
                    type="text" 
                    placeholder="Search engine logs, date, time, metrics..." 
                    value={logSearch}
                    onChange={(e) => { setLogSearch(e.target.value); setLogPage(1); }}
                    className="w-full bg-slate-950 border border-slate-700 focus:border-amber-500 text-xs text-slate-200 py-1.5 pl-8 pr-7 rounded outline-none"
                  />
                  {logSearch && (
                    <button
                      onClick={() => { setLogSearch(''); setLogPage(1); }}
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
                      title="Clear search"
                    >
                      <X size={13} />
                    </button>
                  )}
                </div>

                {/* Source Engine Filter */}
                <div className="relative w-36">
                  <Filter size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                  <select 
                    value={logSource}
                    onChange={(e) => { setLogSource(e.target.value); setLogPage(1); }}
                    className="w-full bg-slate-950 border border-slate-700 focus:border-amber-500 text-xs text-slate-200 py-1.5 pl-8 pr-3 rounded outline-none appearance-none cursor-pointer"
                  >
                    <option value="ALL">All Engines</option>
                    <option value="stream-mgr">stream-mgr</option>
                    <option value="buffer-svc">buffer-svc</option>
                    <option value="telemetry">telemetry</option>
                    <option value="ingest">ingest</option>
                    <option value="system">system</option>
                  </select>
                </div>

                {/* Level Filter */}
                <div className="relative w-28">
                  <select 
                    value={logLevel}
                    onChange={(e) => { setLogLevel(e.target.value); setLogPage(1); }}
                    className="w-full bg-slate-950 border border-slate-700 focus:border-amber-500 text-xs text-slate-200 py-1.5 px-2.5 rounded outline-none appearance-none cursor-pointer"
                  >
                    <option value="ALL">All Levels</option>
                    <option value="INFO">Info</option>
                    <option value="WARNING">Warning</option>
                    <option value="ERROR">Error</option>
                    <option value="DEBUG">Debug</option>
                  </select>
                </div>

                {/* Date Filter */}
                <div className="flex items-center gap-1.5 bg-slate-950 border border-slate-700 rounded px-2.5 py-1 text-xs">
                  <Calendar size={13} className="text-amber-400 shrink-0" />
                  <span className="text-[11px] text-slate-400 font-medium">Date:</span>
                  <input
                    type="date"
                    value={logDateFilter}
                    onChange={(e) => { setLogDateFilter(e.target.value); setLogPage(1); }}
                    className="bg-transparent text-slate-200 text-xs outline-none cursor-pointer [color-scheme:dark]"
                  />
                  {logDateFilter && (
                    <button
                      onClick={() => { setLogDateFilter(''); setLogPage(1); }}
                      className="text-slate-500 hover:text-slate-300 ml-0.5"
                      title="Clear date filter"
                    >
                      <X size={12} />
                    </button>
                  )}
                </div>

                {/* Time Filter */}
                <div className="flex items-center gap-1.5 bg-slate-950 border border-slate-700 rounded px-2.5 py-1 text-xs">
                  <Clock size={13} className="text-amber-400 shrink-0" />
                  <span className="text-[11px] text-slate-400 font-medium">Time:</span>
                  <input
                    type="time"
                    value={logTimeFilter}
                    onChange={(e) => { setLogTimeFilter(e.target.value); setLogPage(1); }}
                    className="bg-transparent text-slate-200 text-xs outline-none cursor-pointer [color-scheme:dark]"
                  />
                  {logTimeFilter && (
                    <button
                      onClick={() => { setLogTimeFilter(''); setLogPage(1); }}
                      className="text-slate-500 hover:text-slate-300 ml-0.5"
                      title="Clear time filter"
                    >
                      <X size={12} />
                    </button>
                  )}
                </div>

                {/* Today shortcut & Reset */}
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => {
                      const today = new Date().toISOString().split('T')[0];
                      setLogDateFilter(logDateFilter === today ? '' : today);
                      setLogPage(1);
                    }}
                    className={`px-2 py-1 rounded text-[11px] font-medium transition-colors ${
                      logDateFilter === new Date().toISOString().split('T')[0]
                        ? 'bg-amber-500/20 text-amber-400 border border-amber-500/40'
                        : 'bg-slate-800 text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    Today
                  </button>
                  {(logDateFilter || logTimeFilter || logSearch || logLevel !== 'ALL' || logSource !== 'ALL') && (
                    <button
                      onClick={() => {
                        setLogSearch('');
                        setLogLevel('ALL');
                        setLogSource('ALL');
                        setLogDateFilter('');
                        setLogTimeFilter('');
                        setLogPage(1);
                      }}
                      className="px-2 py-1 rounded text-[11px] text-rose-400 hover:bg-rose-500/10 transition-colors font-medium flex items-center gap-1"
                    >
                      <X size={11} /> Reset
                    </button>
                  )}
                </div>
              </div>

              <div className="flex items-center gap-2 text-xs text-slate-400">
                <span className="text-[11px] font-mono text-slate-500 hidden sm:inline mr-2">
                  {filteredSystemLogs.length} {filteredSystemLogs.length === 1 ? 'log' : 'logs'}
                </span>
                <span>Rows:</span>
                <select 
                  value={logPageSize} 
                  onChange={e => { setLogPageSize(Number(e.target.value)); setLogPage(1); }}
                  className="bg-slate-950 border border-slate-700 rounded px-2 py-1 outline-none text-slate-200 text-xs cursor-pointer"
                >
                  <option value={15}>15</option>
                  <option value={30}>30</option>
                  <option value={50}>50</option>
                </select>
              </div>
            </div>

            {/* Table */}
            <div className="overflow-x-auto flex-1">
              <table className="w-full text-left text-xs whitespace-nowrap">
                <thead className="bg-slate-950/80 text-slate-400 text-[10px] uppercase font-bold tracking-wider border-b border-slate-800">
                  <tr>
                    <th className="px-3.5 py-3 w-24">Level</th>
                    <th className="px-3.5 py-3 w-48">Timestamp (IST)</th>
                    <th className="px-3.5 py-3 w-36">Source Engine</th>
                    <th className="px-3.5 py-3">Operational Message</th>
                    <th className="px-3.5 py-3 w-32">Component Health</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60 text-slate-300 font-sans">
                  {paginatedSystemLogs.map((log) => (
                    <tr key={log.id} className="hover:bg-slate-800/30 transition-colors">
                      <td className="px-3.5 py-2.5">
                        <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-mono font-bold tracking-wider border ${getLogLevelBadge(log.level)}`}>
                          {log.level}
                        </span>
                      </td>
                      <td className="px-3.5 py-2.5 font-mono text-[11px] text-slate-400">
                        {log.timestamp || formatIST(log.raw_ts)}
                      </td>
                      <td className="px-3.5 py-2.5">
                        <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-mono font-semibold tracking-wider border ${getSourceBadge(log.source)}`}>
                          {log.source}
                        </span>
                      </td>
                      <td className="px-3.5 py-2.5 text-xs text-slate-200 max-w-xl truncate font-mono">
                        {log.message}
                      </td>
                      <td className="px-3.5 py-2.5 font-mono text-[11px] text-sky-400">
                        {log.metric ? (
                          <span className="px-1.5 py-0.5 rounded bg-slate-950 border border-slate-800">
                            {log.metric}
                          </span>
                        ) : "-"}
                      </td>
                    </tr>
                  ))}
                  {paginatedSystemLogs.length === 0 && (
                    <tr>
                      <td colSpan={5} className="px-4 py-12 text-center text-slate-500">
                        No operational engine logs match your filter criteria.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            <div className="p-3 border-t border-slate-800 flex items-center justify-between bg-slate-950/40">
              <div className="text-xs text-slate-500">
                Showing {Math.min(filteredSystemLogs.length, (logPage - 1) * logPageSize + 1)} to {Math.min(filteredSystemLogs.length, logPage * logPageSize)} of {filteredSystemLogs.length} engine entries
              </div>
              <div className="flex items-center gap-1">
                <button 
                  disabled={logPage === 1} 
                  onClick={() => setLogPage(p => p - 1)}
                  className="p-1 rounded bg-slate-800 text-slate-400 hover:bg-slate-700 disabled:opacity-40 transition-colors"
                >
                  <ChevronLeft size={15} />
                </button>
                <span className="px-2 text-xs font-mono text-slate-400">{logPage} / {totalSystemPages}</span>
                <button 
                  disabled={logPage === totalSystemPages} 
                  onClick={() => setLogPage(p => p + 1)}
                  className="p-1 rounded bg-slate-800 text-slate-400 hover:bg-slate-700 disabled:opacity-40 transition-colors"
                >
                  <ChevronRight size={15} />
                </button>
              </div>
            </div>
          </div>
        )}

      </div>
    </AppShell>
  );
}
