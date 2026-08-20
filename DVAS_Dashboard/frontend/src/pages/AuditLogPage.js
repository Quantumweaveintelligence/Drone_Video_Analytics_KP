/**
 * AuditLogPage.js  —  System Administrator Audit Log (Own Actions)
 * =================================================================
 * Full-featured Audit Log page, restricted to system_administrator.
 *
 * What it does:
 *  1. fetchKeycloakUsers()  — calls /api/users (Keycloak Admin API),
 *     falls back to 10 hardcoded Kerala Police DVAS officers.
 *  2. generateAuditLogs()   — 60-85 realistic entries, actors = real users.
 *  3. RBAC: system_administrator sees ONLY their own email as actor.
 *  4. Summary cards: Total Events, Failed Logins, Critical Actions, Warnings.
 *  5. Advanced filters: free-text, date range, actor dropdown, category
 *     multi-select, status toggle buttons.
 *  6. Data table: sortable Timestamp/Actor/Action/Category/Resource/IP/Status.
 *  7. Slide-out drawer: JSON payload, User-Agent, Before/After diff grid.
 *  8. CSV export of currently-filtered rows.
 *  9. Role switcher panel for demo / QA purposes.
 */
import React, { useState, useMemo, useEffect, useCallback, useRef } from 'react';
import AppShell from '@/components/layout/AppShell';
import { useAuth } from '@/context/AuthContext';
import { api } from '@/lib/api';
import {
  Search, Filter, Download, X, ChevronLeft, ChevronRight,
  Shield, AlertTriangle, CheckCircle, Clock, User, Activity,
  FileText, ChevronDown, ChevronUp, Eye, Terminal, Layers,
  RefreshCw, Calendar, SlidersHorizontal, UserCheck
} from 'lucide-react';

// ─────────────────────────────────────────────────────────────────────────────
// 1. Keycloak User Fetching Service
// ─────────────────────────────────────────────────────────────────────────────

async function fetchKeycloakUsers() {
  try {
    const { data } = await api.get('/users');
    if (Array.isArray(data) && data.length > 0) return data;
  } catch (_) { /* fall through */ }
  // Fallback: realistic Kerala Police DVAS officers
  return [
    { id:'u-kp-001', email:'rajesh.kumar@kp.dvas.in',    name:'Rajesh Kumar',    role:'system_administrator', jurisdiction:'State HQ'           },
    { id:'u-kp-002', email:'priya.nair@kp.dvas.in',      name:'Priya Nair',      role:'senior_officer',       jurisdiction:'Thiruvananthapuram' },
    { id:'u-kp-003', email:'sanjay.menon@kp.dvas.in',    name:'Sanjay Menon',    role:'drone_operator',       jurisdiction:'Kochi'              },
    { id:'u-kp-004', email:'deepa.pillai@kp.dvas.in',    name:'Deepa Pillai',    role:'control_room_officer', jurisdiction:'Kozhikode'          },
    { id:'u-kp-005', email:'arun.krishnan@kp.dvas.in',   name:'Arun Krishnan',   role:'drone_operator',       jurisdiction:'Thrissur'           },
    { id:'u-kp-006', email:'sindhu.rajan@kp.dvas.in',    name:'Sindhu Rajan',    role:'control_room_officer', jurisdiction:'Thiruvananthapuram' },
    { id:'u-kp-007', email:'mohan.das@kp.dvas.in',       name:'Mohan Das',       role:'senior_officer',       jurisdiction:'Kannur'             },
    { id:'u-kp-008', email:'anitha.varghese@kp.dvas.in', name:'Anitha Varghese', role:'drone_operator',       jurisdiction:'Kottayam'           },
    { id:'u-kp-009', email:'joseph.thomas@kp.dvas.in',   name:'Joseph Thomas',   role:'control_room_officer', jurisdiction:'Ernakulam'          },
    { id:'u-kp-010', email:'kavitha.suresh@kp.dvas.in',  name:'Kavitha Suresh',  role:'senior_officer',       jurisdiction:'Palakkad'           },
  ];
}

// ─────────────────────────────────────────────────────────────────────────────
// 2. Mock Audit Log Generator
// ─────────────────────────────────────────────────────────────────────────────

const EVENT_TYPES = [
  { action:'USER_LOGIN',           category:'Authentication',  risk:'low',      hasPayload:false },
  { action:'LOGIN_FAILED',         category:'Authentication',  risk:'high',     hasPayload:false },
  { action:'USER_LOGOUT',          category:'Authentication',  risk:'low',      hasPayload:false },
  { action:'TOKEN_REFRESHED',      category:'Authentication',  risk:'low',      hasPayload:false },
  { action:'PASSWORD_CHANGED',     category:'Authentication',  risk:'medium',   hasPayload:true  },
  { action:'API_KEY_ROTATED',      category:'Authentication',  risk:'high',     hasPayload:true  },
  { action:'SESSION_TERMINATED',   category:'Authentication',  risk:'medium',   hasPayload:false },
  { action:'USER_CREATED',         category:'User Management', risk:'critical', hasPayload:true  },
  { action:'USER_SUSPENDED',       category:'User Management', risk:'critical', hasPayload:true  },
  { action:'ROLE_ASSIGNED',        category:'User Management', risk:'critical', hasPayload:true  },
  { action:'USER_PROFILE_UPDATED', category:'User Management', risk:'medium',   hasPayload:true  },
  { action:'MISSION_STARTED',      category:'Operations',      risk:'medium',   hasPayload:true  },
  { action:'MISSION_ENDED',        category:'Operations',      risk:'medium',   hasPayload:true  },
  { action:'DRONE_DEPLOYED',       category:'Operations',      risk:'medium',   hasPayload:true  },
  { action:'ALERT_ACKNOWLEDGED',   category:'Operations',      risk:'low',      hasPayload:false },
  { action:'EVIDENCE_EXPORTED',    category:'Evidence',        risk:'high',     hasPayload:true  },
  { action:'EVIDENCE_DELETED',     category:'Evidence',        risk:'critical', hasPayload:true  },
  { action:'EXPORT_REQUESTED',     category:'Evidence',        risk:'high',     hasPayload:true  },
  { action:'LEGAL_HOLD_APPLIED',   category:'Evidence',        risk:'critical', hasPayload:true  },
  { action:'REPORT_GENERATED',     category:'Evidence',        risk:'low',      hasPayload:false },
  { action:'RECORD_DELETED',       category:'Data',            risk:'critical', hasPayload:true  },
  { action:'RETENTION_PURGE',      category:'Data',            risk:'critical', hasPayload:true  },
  { action:'SETTINGS_UPDATED',     category:'Configuration',   risk:'high',     hasPayload:true  },
  { action:'POLICY_CHANGED',       category:'Configuration',   risk:'critical', hasPayload:true  },
  { action:'INGEST_RESTARTED',     category:'Configuration',   risk:'high',     hasPayload:true  },
];

const IP_RANGES   = ['10.0.1','10.0.2','192.168.1','192.168.10','172.16.0'];
const USER_AGENTS = [
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0',
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Safari/537.36',
  'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/119.0.0.0',
  'Mozilla/5.0 (iPad; CPU OS 16_0 like Mac OS X) Mobile/15E148 Safari/604.1',
  'DVAS-EdgeClient/2.4.1 (Embedded; ARM64)',
];
const TARGETS = [
  'MSN-2026-0441','MSN-2026-0512','DRN-001','DRN-002','DRN-004',
  'EVI-0028','EVI-0031','EVI-0045','USR-kp-003','USR-kp-007',
  'SYSTEM','INGEST-SERVER','POLICY-ABAC','RETENTION-JOB','API-GATEWAY',
  'REPORT-2026-Q3','LEGAL-HOLD-LH24001','SESSION-f9a3c1',
];
const BEFORE_AFTER = {
  USER_CREATED:         { before:null,                                          after:{ role:'drone_operator', jurisdiction:'Kochi', status:'active' } },
  USER_SUSPENDED:       { before:{ status:'active' },                           after:{ status:'suspended', reason:'Policy violation' } },
  ROLE_ASSIGNED:        { before:{ role:'control_room_officer' },               after:{ role:'senior_officer' } },
  SETTINGS_UPDATED:     { before:{ retention_days:90, tls_min:'1.2' },          after:{ retention_days:180, tls_min:'1.3' } },
  POLICY_CHANGED:       { before:{ rule:'jurisdiction_lock', enabled:false },   after:{ rule:'jurisdiction_lock', enabled:true } },
  RETENTION_PURGE:      { before:{ records_count:8421 },                        after:{ records_count:6003, purged:2418 } },
  EVIDENCE_DELETED:     { before:{ file:'EVI-0031.mp4', status:'active' },      after:{ file:'EVI-0031.mp4', status:'deleted' } },
  INGEST_RESTARTED:     { before:{ status:'stopped', uptime:0 },                after:{ status:'running', uptime:1 } },
  API_KEY_ROTATED:      { before:{ key_prefix:'sk-old-***' },                   after:{ key_prefix:'sk-new-***' } },
  LEGAL_HOLD_APPLIED:   { before:{ hold:null },                                 after:{ hold:'LH-24003', reason:'Court order Cr. No. 201/2026' } },
};

const rand    = (arr) => arr[Math.floor(Math.random() * arr.length)];
const randInt = (mn, mx) => Math.floor(Math.random() * (mx - mn + 1)) + mn;

function generateAuditLogs(users) {
  if (!users || !users.length) return [];
  const now = Date.now();
  return Array.from({ length: randInt(60, 85) }, (_, i) => {
    const ev     = rand(EVENT_TYPES);
    const actor  = rand(users);
    const target = rand(TARGETS);
    const ip     = `${rand(IP_RANGES)}.${randInt(10, 254)}`;
    const ts     = new Date(now - randInt(0, 7 * 24 * 3600 * 1000)).toISOString();
    let status;
    if (ev.action === 'LOGIN_FAILED')   status = 'Failed';
    else if (ev.risk === 'critical')    status = Math.random() > 0.15 ? 'Success' : 'Warning';
    else if (ev.risk === 'high')        status = Math.random() > 0.1  ? 'Success' : Math.random() > 0.5 ? 'Failed' : 'Warning';
    else                                status = 'Success';
    const ba = BEFORE_AFTER[ev.action] || null;
    return {
      id: `LOG-${String(i+1).padStart(4,'0')}`,
      ts, actor_id:actor.id, actor_name:actor.name, actor_email:actor.email,
      actor_role:actor.role, actor_dept:actor.jurisdiction||'State HQ',
      action:ev.action, category:ev.category, risk:ev.risk,
      target, ip, status, user_agent:rand(USER_AGENTS),
      payload: ev.hasPayload ? {
        request_id:`req-${Math.random().toString(36).slice(2,10)}`,
        action:ev.action, actor_id:actor.id, target, ts,
        metadata:{ ip, session:`ses-${Math.random().toString(36).slice(2,8)}` },
      } : null,
      before: ba?.before ?? null,
      after:  ba?.after  ?? null,
    };
  }).sort((a,b) => b.ts.localeCompare(a.ts));
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. RBAC — System Admin sees ONLY own actions
// ─────────────────────────────────────────────────────────────────────────────

function applyRbacFilter(logs, currentUser) {
  if (!currentUser) return logs;
  if (currentUser.role === 'system_administrator') {
    return logs.filter(l => l.actor_email === currentUser.email);
  }
  return logs; // demo role override: show all
}

// ─────────────────────────────────────────────────────────────────────────────
// 4. Constants & helpers
// ─────────────────────────────────────────────────────────────────────────────

const CATEGORIES = ['Authentication','User Management','Operations','Evidence','Data','Configuration'];
const STATUSES   = ['Success','Failed','Warning'];
const PAGE_SIZE  = 15;

function statusStyle(s) {
  const m = {
    Success:{ bg:'var(--dvas-safe-bg)', color:'var(--dvas-safe)', label:'✓ Success' },
    Failed: { bg:'var(--dvas-crit-bg)', color:'var(--dvas-crit)', label:'✗ Failed'  },
    Warning:{ bg:'var(--dvas-warn-bg)', color:'var(--dvas-warn)', label:'⚠ Warning' },
  };
  return m[s] || { bg:'var(--dvas-info-bg)', color:'var(--dvas-blue)', label:s };
}
function riskColor(r) {
  return r==='critical'?'var(--dvas-crit)':r==='high'?'var(--dvas-warn)':r==='medium'?'var(--dvas-blue)':'var(--dvas-text-dim)';
}

// ─────────────────────────────────────────────────────────────────────────────
// 5. Sub-components
// ─────────────────────────────────────────────────────────────────────────────

function SummaryCard({ icon:Icon, label, value, sub, color, testId }) {
  return (
    <div data-testid={testId} style={{ background:'var(--dvas-surface)', border:'1px solid var(--dvas-border)', borderRadius:'var(--dvas-radius)', boxShadow:'var(--dvas-shadow)', padding:20, display:'flex', alignItems:'center', gap:16 }}>
      <div style={{ width:48, height:48, borderRadius:12, display:'flex', alignItems:'center', justifyContent:'center', flexShrink:0, background:`color-mix(in srgb, ${color} 15%, transparent)` }}>
        <Icon size={22} style={{ color }} />
      </div>
      <div>
        <div style={{ fontSize:28, fontWeight:700, fontFamily:'JetBrains Mono,monospace', color }}>{value}</div>
        <div style={{ fontSize:12, fontWeight:600, marginTop:2, color:'var(--dvas-text)' }}>{label}</div>
        {sub && <div style={{ fontSize:10, marginTop:2, color:'var(--dvas-text-dim)' }}>{sub}</div>}
      </div>
    </div>
  );
}

function MultiSelect({ label, options, selected, onChange, testId }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return;
    const h = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, [open]);
  const toggle = v => onChange(selected.includes(v) ? selected.filter(x=>x!==v) : [...selected,v]);
  const btnText = selected.length===0 ? label : selected.length===options.length ? 'All Categories' : `${selected.length} selected`;
  return (
    <div ref={ref} style={{ position:'relative' }} data-testid={testId}>
      <button onClick={() => setOpen(o=>!o)} style={{ display:'flex', alignItems:'center', gap:6, height:36, paddingInline:12, fontSize:12, borderRadius:10, background:'var(--dvas-elev)', border:'1px solid var(--dvas-border)', color:'var(--dvas-text)', minWidth:140, cursor:'pointer' }}>
        <Filter size={13} style={{ color:'var(--dvas-text-muted)', flexShrink:0 }} />
        <span style={{ flex:1, textAlign:'left', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{btnText}</span>
        <ChevronDown size={12} style={{ color:'var(--dvas-text-muted)', flexShrink:0 }} />
      </button>
      {open && (
        <div className="dvas-fade-in" style={{ position:'absolute', top:40, left:0, zIndex:50, width:208, padding:'4px 0', background:'var(--dvas-surface)', border:'1px solid var(--dvas-border)', borderRadius:'var(--dvas-radius-sm)', boxShadow:'var(--dvas-shadow-lg)' }}>
          {options.map(opt => (
            <label key={opt} style={{ display:'flex', alignItems:'center', gap:8, padding:'8px 12px', cursor:'pointer', fontSize:12, color:'var(--dvas-text)' }} className="hover:bg-[color:var(--dvas-elev)]">
              <input type="checkbox" checked={selected.includes(opt)} onChange={() => toggle(opt)} style={{ width:14, height:14 }} />
              {opt}
            </label>
          ))}
          <div style={{ padding:'6px 12px', borderTop:'1px solid var(--dvas-border)' }}>
            <button onClick={() => onChange(selected.length===options.length ? [] : [...options])} style={{ fontSize:10, fontWeight:600, color:'var(--dvas-blue)', background:'none', border:'none', cursor:'pointer' }}>
              {selected.length===options.length ? 'Clear all' : 'Select all'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function JsonViewer({ data }) {
  return (
    <pre style={{ fontSize:11, overflow:'auto', borderRadius:10, padding:16, fontFamily:'JetBrains Mono,monospace', background:'var(--dvas-surface-2)', color:'var(--dvas-text)', border:'1px solid var(--dvas-border)', maxHeight:260, lineHeight:1.6 }}>
      {JSON.stringify(data || { note:'No payload for this event type.' }, null, 2)}
    </pre>
  );
}

function DiffViewer({ before, after }) {
  if (!before && !after) return null;
  const keys = Array.from(new Set([...Object.keys(before||{}), ...Object.keys(after||{})]));
  return (
    <div style={{ overflow:'hidden', borderRadius:10, border:'1px solid var(--dvas-border)', fontFamily:'JetBrains Mono,monospace', fontSize:11 }}>
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', background:'var(--dvas-surface-2)', borderBottom:'1px solid var(--dvas-border)', color:'var(--dvas-text-muted)', padding:'6px 12px', fontSize:10, fontWeight:600, textTransform:'uppercase', letterSpacing:'0.06em' }}>
        <span>Field</span><span>Before</span><span>After</span>
      </div>
      {keys.map(key => {
        const bv=before?.[key], av=after?.[key], changed=JSON.stringify(bv)!==JSON.stringify(av);
        return (
          <div key={key} style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', borderBottom:'1px solid var(--dvas-border)', background:changed?'color-mix(in srgb, var(--dvas-warn-bg) 60%, transparent)':'transparent', padding:'7px 12px', alignItems:'center' }}>
            <span style={{ color:'var(--dvas-text-muted)' }}>{key}</span>
            <span style={{ color:bv==null?'var(--dvas-text-dim)':'var(--dvas-crit)' }}>{bv==null?'—':JSON.stringify(bv)}</span>
            <span style={{ color:av==null?'var(--dvas-text-dim)':'var(--dvas-safe)' }}>{av==null?'—':JSON.stringify(av)}</span>
          </div>
        );
      })}
    </div>
  );
}

function DetailDrawer({ log, onClose }) {
  const [tab, setTab] = useState('payload');
  const sty = statusStyle(log.status);
  return (
    <div onClick={onClose} style={{ position:'fixed', inset:0, zIndex:50, display:'flex', background:'rgba(15,23,42,0.55)' }}>
      <div onClick={e=>e.stopPropagation()} className="dvas-fade-in" style={{ marginLeft:'auto', height:'100%', display:'flex', flexDirection:'column', width:520, background:'var(--dvas-surface)', borderLeft:'1px solid var(--dvas-border)', boxShadow:'var(--dvas-shadow-lg)', overflow:'hidden' }}>
        {/* Header */}
        <div style={{ display:'flex', alignItems:'center', gap:12, padding:'16px 20px', flexShrink:0, borderBottom:'1px solid var(--dvas-border)' }}>
          <div style={{ width:32, height:32, borderRadius:8, display:'flex', alignItems:'center', justifyContent:'center', background:'var(--dvas-info-bg)', flexShrink:0 }}>
            <FileText size={16} style={{ color:'var(--dvas-blue)' }} />
          </div>
          <div style={{ flex:1, minWidth:0 }}>
            <div style={{ fontSize:13, fontWeight:700, fontFamily:'JetBrains Mono,monospace', color:'var(--dvas-text)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{log.action}</div>
            <div style={{ fontSize:10, marginTop:2, fontFamily:'JetBrains Mono,monospace', color:'var(--dvas-text-dim)' }}>{log.id}</div>
          </div>
          <span style={{ fontSize:11, fontWeight:600, padding:'3px 10px', borderRadius:999, background:sty.bg, color:sty.color, flexShrink:0 }}>{sty.label}</span>
          <button data-testid="drawer-close" onClick={onClose} style={{ width:28, height:28, borderRadius:8, display:'flex', alignItems:'center', justifyContent:'center', background:'none', border:'none', cursor:'pointer', color:'var(--dvas-text-muted)' }}><X size={16}/></button>
        </div>
        {/* Meta grid */}
        <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', padding:'12px 20px', gap:12, flexShrink:0, borderBottom:'1px solid var(--dvas-border)', background:'var(--dvas-surface-2)' }}>
          {[['Actor',log.actor_name||log.actor_email],['Target',log.target],['IP',log.ip],
            ['Timestamp',new Date(log.ts).toLocaleString('en-IN',{timeZone:'Asia/Kolkata'})],
            ['Role',log.actor_role?.replace(/_/g,' ')],['Dept',log.actor_dept]].map(([l,v]) => (
            <div key={l}>
              <div style={{ fontSize:9, fontWeight:600, textTransform:'uppercase', letterSpacing:'0.07em', color:'var(--dvas-text-dim)', marginBottom:2 }}>{l}</div>
              <div style={{ fontSize:11, fontWeight:500, fontFamily:'JetBrains Mono,monospace', color:'var(--dvas-text)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{v||'—'}</div>
            </div>
          ))}
        </div>
        {/* User-Agent */}
        <div style={{ padding:'10px 20px', flexShrink:0, borderBottom:'1px solid var(--dvas-border)' }}>
          <div style={{ fontSize:10, fontWeight:600, textTransform:'uppercase', letterSpacing:'0.06em', color:'var(--dvas-text-dim)', marginBottom:4, display:'flex', alignItems:'center', gap:4 }}><Terminal size={11}/>User-Agent</div>
          <div style={{ fontSize:11, fontFamily:'JetBrains Mono,monospace', wordBreak:'break-all', color:'var(--dvas-text-muted)' }}>{log.user_agent}</div>
        </div>
        {/* Tabs */}
        <div style={{ display:'flex', flexShrink:0, borderBottom:'1px solid var(--dvas-border)' }}>
          {[{k:'payload',l:'JSON Payload',I:Layers},{k:'diff',l:'Before / After',I:Eye,d:!log.before&&!log.after}].map(({k,l,I:TI,d}) => (
            <button key={k} onClick={()=>!d&&setTab(k)} disabled={d} style={{ display:'flex', alignItems:'center', gap:6, padding:'12px 16px', fontSize:12, fontWeight:600, background:'none', border:'none', cursor:d?'not-allowed':'pointer', color:tab===k?'var(--dvas-blue)':d?'var(--dvas-text-dim)':'var(--dvas-text-muted)', borderBottom:tab===k?'2px solid var(--dvas-blue)':'2px solid transparent', marginBottom:-1 }}>
              <TI size={13}/>{l}{d&&<span style={{fontSize:9,opacity:0.6}}>(N/A)</span>}
            </button>
          ))}
        </div>
        {/* Tab body */}
        <div style={{ flex:1, overflow:'auto', padding:20 }}>
          {tab==='payload' && <div><div style={{ fontSize:10, fontWeight:600, textTransform:'uppercase', letterSpacing:'0.06em', color:'var(--dvas-text-dim)', marginBottom:8 }}>Event Payload</div><JsonViewer data={log.payload}/></div>}
          {tab==='diff' && <div><div style={{ fontSize:10, fontWeight:600, textTransform:'uppercase', letterSpacing:'0.06em', color:'var(--dvas-text-dim)', marginBottom:8 }}>Data Modification Diff</div>{log.before||log.after?<DiffViewer before={log.before} after={log.after}/>:<div style={{ fontSize:12, textAlign:'center', padding:'32px 0', color:'var(--dvas-text-dim)' }}>No data modification for this event.</div>}</div>}
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// 6. CSV Export
// ─────────────────────────────────────────────────────────────────────────────

function exportToCsv(logs) {
  const H = ['ID','Timestamp (IST)','Actor','Email','Role','Dept','Action','Category','Resource','IP','Status'];
  const rows = logs.map(l => [l.id,new Date(l.ts).toLocaleString('en-IN',{timeZone:'Asia/Kolkata'}),l.actor_name,l.actor_email,l.actor_role,l.actor_dept,l.action,l.category,l.target,l.ip,l.status].map(v=>`"${String(v??'').replace(/"/g,'""')}"`).join(','));
  const blob = new Blob([[H.join(','),...rows].join('\n')],{type:'text/csv;charset=utf-8;'});
  const a = Object.assign(document.createElement('a'),{href:URL.createObjectURL(blob),download:'dvas_audit_own_actions.csv'});
  a.click();
}

// ─────────────────────────────────────────────────────────────────────────────
// 7. Main Page
// ─────────────────────────────────────────────────────────────────────────────

const COLS = [
  {key:'ts',       w:'15%', label:'Timestamp'},
  {key:'actor_name',w:'14%', label:'Actor'},
  {key:'action',   w:'18%', label:'Action'},
  {key:'category', w:'12%', label:'Category'},
  {key:'target',   w:'13%', label:'Resource'},
  {key:'ip',       w:'10%', label:'IP Address'},
  {key:'status',   w:'10%', label:'Status'},
  {key:'_view',    w:'8%',  label:''},
];

export default function AuditLogPage() {
  const { user: authUser } = useAuth();

  // ── data
  const [users,      setUsers]      = useState([]);
  const [allLogs,    setAllLogs]    = useState([]);
  const [loading,    setLoading]    = useState(true);

  // ── demo role override (for QA)
  const [roleMode,   setRoleMode]   = useState(null);
  const [showPicker, setShowPicker] = useState(false);

  // real current user = authUser unless demo override
  const currentUser = useMemo(() =>
    roleMode || authUser || { role:'system_administrator', email:'rajesh.kumar@kp.dvas.in', jurisdiction:'State HQ', name:'Rajesh Kumar' }
  , [roleMode, authUser]);

  // ── filters
  const [search,        setSearch]        = useState('');
  const [dateFrom,      setDateFrom]      = useState('');
  const [dateTo,        setDateTo]        = useState('');
  const [actorFilter,   setActorFilter]   = useState('');
  const [categories,    setCategories]    = useState([...CATEGORIES]);
  const [statusFilters, setStatusFilters] = useState([...STATUSES]);
  const [sortKey,       setSortKey]       = useState('ts');
  const [sortDir,       setSortDir]       = useState('desc');
  const [page,          setPage]          = useState(1);

  // ── UI
  const [selectedLog,  setSelectedLog]  = useState(null);
  const [exporting,    setExporting]    = useState(false);

  // ── load
  const loadData = useCallback(async () => {
    setLoading(true);
    const fetched = await fetchKeycloakUsers();
    setUsers(fetched);
    setAllLogs(generateAuditLogs(fetched));
    setLoading(false);
  }, []);
  useEffect(() => { loadData(); }, [loadData]);

  // ── RBAC
  const rbacLogs = useMemo(() => applyRbacFilter(allLogs, currentUser), [allLogs, currentUser]);

  // ── filter + sort
  const filteredLogs = useMemo(() => rbacLogs.filter(l => {
    const term = search.toLowerCase();
    const ok = !term || [l.actor_name,l.actor_email,l.action,l.target,l.ip,l.category].some(v=>(v||'').toLowerCase().includes(term));
    const okActor  = !actorFilter || l.actor_id===actorFilter;
    const okCat    = !categories.length    || categories.includes(l.category);
    const okStatus = !statusFilters.length || statusFilters.includes(l.status);
    const d = new Date(l.ts);
    const okFrom = !dateFrom || d>=new Date(dateFrom);
    const okTo   = !dateTo   || d<=new Date(dateTo+'T23:59:59');
    return ok && okActor && okCat && okStatus && okFrom && okTo;
  }), [rbacLogs, search, actorFilter, categories, statusFilters, dateFrom, dateTo]);

  const sortedLogs = useMemo(() => [...filteredLogs].sort((a,b) => {
    let av=a[sortKey], bv=b[sortKey];
    if (typeof av==='string') { av=av.toLowerCase(); bv=bv.toLowerCase(); }
    return av<bv?(sortDir==='asc'?-1:1):av>bv?(sortDir==='asc'?1:-1):0;
  }), [filteredLogs, sortKey, sortDir]);

  const totalPages    = Math.max(1, Math.ceil(sortedLogs.length/PAGE_SIZE));
  const safePage      = Math.min(page, totalPages);
  const paginatedLogs = sortedLogs.slice((safePage-1)*PAGE_SIZE, safePage*PAGE_SIZE);

  useEffect(() => setPage(1), [search,actorFilter,categories,statusFilters,dateFrom,dateTo,currentUser]);

  // ── summary
  const summary = useMemo(() => ({
    total:    filteredLogs.length,
    failed:   filteredLogs.filter(l=>l.status==='Failed').length,
    critical: filteredLogs.filter(l=>l.risk==='critical').length,
    warnings: filteredLogs.filter(l=>l.status==='Warning').length,
  }), [filteredLogs]);

  const handleSort = k => { if (sortKey===k) setSortDir(d=>d==='asc'?'desc':'asc'); else { setSortKey(k); setSortDir('desc'); } };
  const SortIcon = ({ col }) => sortKey!==col ? <ChevronDown size={11} style={{opacity:0.3}}/> : sortDir==='asc' ? <ChevronUp size={11}/> : <ChevronDown size={11}/>;

  const actorOptions = useMemo(() => {
    const seen = new Set();
    return rbacLogs.filter(l => { if(seen.has(l.actor_id))return false; seen.add(l.actor_id); return true; })
      .map(l => ({ value:l.actor_id, label:l.actor_name||l.actor_email }));
  }, [rbacLogs]);

  const demoOptions = [
    { label:'System Administrator (own actions only — PRODUCTION)', value:{ role:'system_administrator', email:authUser?.email||'rajesh.kumar@kp.dvas.in', jurisdiction:'State HQ', name:authUser?.name||'Rajesh Kumar' } },
    { label:'Demo: view all (QA only)',  value:{ role:'_demo_all', email:'qa@kp.dvas.in', jurisdiction:'State HQ', name:'QA User' } },
  ];

  const handleExport = () => { setExporting(true); exportToCsv(sortedLogs); setTimeout(()=>setExporting(false),900); };

  return (
    <AppShell title="Audit Log — Own Actions" subtitle="System Administrator · RBAC-enforced immutable audit trail · Last 7 days">

      {/* ── RBAC context banner ── */}
      <div style={{ marginBottom:20, padding:'10px 14px', borderRadius:12, display:'flex', alignItems:'center', justifyContent:'space-between', gap:12, flexWrap:'wrap', background:'var(--dvas-info-bg)', border:'1px solid color-mix(in srgb, var(--dvas-blue) 25%, transparent)' }}>
        <div style={{ display:'flex', alignItems:'center', gap:8, flexWrap:'wrap' }}>
          <UserCheck size={15} style={{ color:'var(--dvas-blue)', flexShrink:0 }}/>
          <span style={{ fontSize:12, fontWeight:600, color:'var(--dvas-text)' }}>Viewing as:&nbsp;
            <span style={{ fontFamily:'JetBrains Mono,monospace', color:'var(--dvas-blue)' }}>
              {currentUser.name||currentUser.email} · {currentUser.role?.replace(/_/g,' ')} · {currentUser.jurisdiction||'State HQ'}
            </span>
          </span>
          {currentUser.role==='system_administrator' && (
            <span style={{ fontSize:10, fontWeight:600, padding:'2px 8px', borderRadius:999, background:'var(--dvas-warn-bg)', color:'var(--dvas-warn)', marginLeft:4 }}>
              Own actions only
            </span>
          )}
        </div>
        <div style={{ position:'relative' }}>
          <button data-testid="role-switcher-btn" onClick={()=>setShowPicker(o=>!o)} style={{ display:'flex', alignItems:'center', gap:6, fontSize:12, fontWeight:600, padding:'6px 12px', borderRadius:8, cursor:'pointer', color:'var(--dvas-blue)', background:'var(--dvas-surface)', border:'1px solid var(--dvas-border)' }}>
            <Shield size={13}/> View Mode <ChevronDown size={12}/>
          </button>
          {showPicker && (
            <div className="dvas-fade-in" style={{ position:'absolute', right:0, top:40, zIndex:50, width:300, paddingBlock:4, background:'var(--dvas-surface)', border:'1px solid var(--dvas-border)', borderRadius:'var(--dvas-radius-sm)', boxShadow:'var(--dvas-shadow-lg)' }}>
              <div style={{ padding:'6px 12px', fontSize:10, fontWeight:600, textTransform:'uppercase', letterSpacing:'0.06em', color:'var(--dvas-text-dim)', borderBottom:'1px solid var(--dvas-border)' }}>Select Audit View Mode</div>
              {demoOptions.map((o,i) => (
                <button key={i} onClick={()=>{setRoleMode(o.value);setShowPicker(false);}} style={{ width:'100%', textAlign:'left', padding:'10px 12px', fontSize:12, cursor:'pointer', background:'none', border:'none', color:'var(--dvas-text)', borderLeft:`3px solid ${JSON.stringify(currentUser)===JSON.stringify(o.value)?'var(--dvas-blue)':'transparent'}` }} className="hover:bg-[color:var(--dvas-elev)]">
                  {o.label}
                </button>
              ))}
              {roleMode && <button onClick={()=>{setRoleMode(null);setShowPicker(false);}} style={{ width:'100%', textAlign:'left', padding:'10px 12px', fontSize:12, cursor:'pointer', background:'none', border:'none', color:'var(--dvas-crit)', borderTop:'1px solid var(--dvas-border)' }}>↩ Reset to actual user</button>}
            </div>
          )}
        </div>
      </div>

      {/* ── Summary Cards ── */}
      <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fit,minmax(190px,1fr))', gap:16, marginBottom:24 }}>
        <SummaryCard testId="card-total"    icon={Activity}      label="Total Events"     value={summary.total}    sub="In selected period"      color="var(--dvas-blue)"/>
        <SummaryCard testId="card-failed"   icon={AlertTriangle} label="Failed Logins"    value={summary.failed}   sub="Authentication failures" color="var(--dvas-crit)"/>
        <SummaryCard testId="card-critical" icon={Shield}        label="Critical Actions" value={summary.critical} sub="High-risk operations"    color="var(--dvas-warn)"/>
        <SummaryCard testId="card-warnings" icon={Clock}         label="Warnings"         value={summary.warnings} sub="Flagged for review"      color="var(--dvas-warn)"/>
      </div>

      {/* ── Main Panel ── */}
      <div style={{ background:'var(--dvas-surface)', border:'1px solid var(--dvas-border)', borderRadius:'var(--dvas-radius)', boxShadow:'var(--dvas-shadow)', overflow:'hidden' }}>

        {/* Toolbar */}
        <div style={{ padding:16, display:'flex', flexWrap:'wrap', gap:12, alignItems:'center', borderBottom:'1px solid var(--dvas-border)' }}>
          {/* Search */}
          <div style={{ position:'relative', flex:1, minWidth:200 }}>
            <Search size={14} style={{ position:'absolute', left:10, top:'50%', transform:'translateY(-50%)', color:'var(--dvas-text-muted)', pointerEvents:'none' }}/>
            <input data-testid="audit-search" type="text" placeholder="Search actor, action, resource, IP…" value={search} onChange={e=>setSearch(e.target.value)}
              style={{ width:'100%', paddingLeft:34, paddingRight:12, paddingBlock:8, fontSize:12, background:'var(--dvas-elev)', border:'1px solid var(--dvas-border)', borderRadius:10, color:'var(--dvas-text)', outline:'none', boxSizing:'border-box' }}/>
          </div>
          {/* Date range */}
          <div style={{ display:'flex', alignItems:'center', gap:6 }}>
            <Calendar size={13} style={{ color:'var(--dvas-text-muted)' }}/>
            <input data-testid="date-from" type="date" value={dateFrom} onChange={e=>setDateFrom(e.target.value)}
              style={{ fontSize:12, paddingBlock:6, paddingInline:8, background:'var(--dvas-elev)', border:'1px solid var(--dvas-border)', borderRadius:10, color:'var(--dvas-text)', outline:'none' }}/>
            <span style={{ color:'var(--dvas-text-dim)', fontSize:12 }}>–</span>
            <input data-testid="date-to" type="date" value={dateTo} onChange={e=>setDateTo(e.target.value)}
              style={{ fontSize:12, paddingBlock:6, paddingInline:8, background:'var(--dvas-elev)', border:'1px solid var(--dvas-border)', borderRadius:10, color:'var(--dvas-text)', outline:'none' }}/>
          </div>
          {/* Actor dropdown */}
          <div style={{ position:'relative' }}>
            <User size={13} style={{ position:'absolute', left:10, top:'50%', transform:'translateY(-50%)', color:'var(--dvas-text-muted)', pointerEvents:'none' }}/>
            <select data-testid="actor-filter" value={actorFilter} onChange={e=>setActorFilter(e.target.value)}
              style={{ paddingLeft:30, paddingRight:12, paddingBlock:8, fontSize:12, background:'var(--dvas-elev)', border:'1px solid var(--dvas-border)', borderRadius:10, color:'var(--dvas-text)', outline:'none', minWidth:160, cursor:'pointer', appearance:'none' }}>
              <option value="">All Actors</option>
              {actorOptions.map(o=><option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
          {/* Category multi-select */}
          <MultiSelect label="Categories" options={CATEGORIES} selected={categories} onChange={setCategories} testId="category-filter"/>
          {/* Status toggles */}
          <div data-testid="status-toggles" style={{ display:'flex', gap:4 }}>
            {STATUSES.map(s => { const st=statusStyle(s); const active=statusFilters.includes(s); return (
              <button key={s} onClick={()=>setStatusFilters(f=>active?f.filter(x=>x!==s):[...f,s])}
                style={{ fontSize:11, fontWeight:600, padding:'6px 10px', borderRadius:8, cursor:'pointer', transition:'all 120ms',
                         background:active?st.bg:'var(--dvas-elev)', color:active?st.color:'var(--dvas-text-dim)',
                         border:`1px solid ${active?'color-mix(in srgb, '+st.color+' 30%, transparent)':'var(--dvas-border)'}` }}>{s}</button>
            ); })}
          </div>
          {/* Actions */}
          <div style={{ marginLeft:'auto', display:'flex', gap:8 }}>
            <button data-testid="refresh-logs" onClick={loadData} title="Regenerate logs"
              style={{ width:36, height:36, display:'flex', alignItems:'center', justifyContent:'center', borderRadius:8, cursor:'pointer', color:'var(--dvas-text-muted)', background:'var(--dvas-surface)', border:'1px solid var(--dvas-border)' }}>
              <RefreshCw size={14}/>
            </button>
            <button data-testid="export-csv" onClick={handleExport} disabled={exporting||sortedLogs.length===0}
              style={{ display:'flex', alignItems:'center', gap:8, padding:'0 12px', height:36, fontSize:12, fontWeight:600, borderRadius:8, cursor:exporting?'default':'pointer', transition:'all 120ms',
                       background:exporting?'var(--dvas-safe-bg)':'var(--dvas-blue)', color:'#fff', border:'none', opacity:sortedLogs.length===0?0.5:1 }}>
              <Download size={13}/> {exporting?'Exporting…':`Export CSV (${sortedLogs.length})`}
            </button>
          </div>
        </div>

        {/* Table */}
        {loading ? (
          <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', padding:'80px 0', gap:12 }}>
            <div style={{ width:32, height:32, borderRadius:'50%', border:'2px solid var(--dvas-blue)', borderTopColor:'transparent', animation:'auditSpin 0.8s linear infinite' }}/>
            <div style={{ fontSize:12, color:'var(--dvas-text-dim)' }}>Fetching users from Keycloak…</div>
          </div>
        ) : (
          <div style={{ overflowX:'auto' }}>
            <table style={{ width:'100%', fontSize:12, tableLayout:'fixed', borderCollapse:'collapse' }}>
              <thead>
                <tr style={{ background:'var(--dvas-surface-2)', borderBottom:'1px solid var(--dvas-border)' }}>
                  {COLS.map(c => (
                    <th key={c.key} onClick={()=>c.key!=='_view'&&handleSort(c.key)} style={{ width:c.w, padding:'10px 14px', textAlign:'left', cursor:c.key!=='_view'?'pointer':'default', whiteSpace:'nowrap' }}>
                      <div style={{ display:'flex', alignItems:'center', gap:4, fontSize:10, fontWeight:600, textTransform:'uppercase', letterSpacing:'0.06em', color:sortKey===c.key?'var(--dvas-blue)':'var(--dvas-text-muted)' }}>
                        {c.label}{c.key!=='_view'&&<SortIcon col={c.key}/>}
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {paginatedLogs.length===0 ? (
                  <tr><td colSpan={8} style={{ padding:'64px 0', textAlign:'center', color:'var(--dvas-text-dim)' }}>
                    <div style={{ display:'flex', flexDirection:'column', alignItems:'center', gap:8 }}>
                      <SlidersHorizontal size={28} style={{ opacity:0.3 }}/>
                      <span style={{ fontSize:13 }}>No audit records match your current filters.</span>
                    </div>
                  </td></tr>
                ) : paginatedLogs.map(log => {
                  const sty = statusStyle(log.status);
                  return (
                    <tr key={log.id} data-testid={`audit-row-${log.id}`} onClick={()=>setSelectedLog(log)} style={{ borderBottom:'1px solid var(--dvas-border)', cursor:'pointer' }} className="hover:bg-[color:var(--dvas-elev)]">
                      <td style={{ padding:'10px 14px' }}>
                        <div style={{ fontSize:11, fontFamily:'JetBrains Mono,monospace', color:'var(--dvas-text-muted)' }}>{new Date(log.ts).toLocaleDateString('en-IN')}</div>
                        <div style={{ fontSize:10, fontFamily:'JetBrains Mono,monospace', color:'var(--dvas-text-dim)' }}>{new Date(log.ts).toLocaleTimeString('en-IN',{timeZone:'Asia/Kolkata'})}</div>
                      </td>
                      <td style={{ padding:'10px 14px' }}>
                        <div style={{ fontSize:11, fontWeight:600, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', color:'var(--dvas-text)' }}>{log.actor_name}</div>
                        <div style={{ fontSize:10, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', color:'var(--dvas-text-dim)' }}>{log.actor_dept}</div>
                      </td>
                      <td style={{ padding:'10px 14px' }}>
                        <span style={{ fontFamily:'JetBrains Mono,monospace', display:'inline-block', padding:'2px 8px', borderRadius:999, fontSize:10, fontWeight:600, background:'var(--dvas-info-bg)', color:riskColor(log.risk) }}>{log.action}</span>
                      </td>
                      <td style={{ padding:'10px 14px', fontSize:11, color:'var(--dvas-text-muted)' }}>{log.category}</td>
                      <td style={{ padding:'10px 14px' }}><span style={{ fontFamily:'JetBrains Mono,monospace', fontSize:11, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', display:'block', color:'var(--dvas-text)' }}>{log.target}</span></td>
                      <td style={{ padding:'10px 14px', fontFamily:'JetBrains Mono,monospace', fontSize:11, color:'var(--dvas-text-dim)' }}>{log.ip}</td>
                      <td style={{ padding:'10px 14px' }}>
                        <span style={{ display:'inline-flex', alignItems:'center', gap:4, padding:'3px 8px', borderRadius:999, fontSize:10, fontWeight:600, background:sty.bg, color:sty.color }}>
                          {log.status==='Success'&&<CheckCircle size={10}/>}{log.status==='Failed'&&<X size={10}/>}{log.status==='Warning'&&<AlertTriangle size={10}/>}{log.status}
                        </span>
                      </td>
                      <td style={{ padding:'10px 14px' }}>
                        <button data-testid={`view-log-${log.id}`} onClick={e=>{e.stopPropagation();setSelectedLog(log);}} style={{ display:'inline-flex', alignItems:'center', gap:4, fontSize:11, fontWeight:600, padding:'4px 8px', borderRadius:8, cursor:'pointer', color:'var(--dvas-blue)', background:'var(--dvas-info-bg)', border:'none' }}>
                          <Eye size={11}/>View
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination */}
        {!loading && (
          <div style={{ padding:'10px 20px', display:'flex', alignItems:'center', justifyContent:'space-between', borderTop:'1px solid var(--dvas-border)', background:'var(--dvas-surface-2)' }}>
            <div style={{ fontSize:12, color:'var(--dvas-text-dim)' }}>
              Showing&nbsp;
              <span style={{ fontWeight:600, fontFamily:'JetBrains Mono,monospace', color:'var(--dvas-text)' }}>
                {sortedLogs.length===0?0:(safePage-1)*PAGE_SIZE+1}–{Math.min(safePage*PAGE_SIZE,sortedLogs.length)}
              </span>
              &nbsp;of&nbsp;
              <span style={{ fontWeight:600, fontFamily:'JetBrains Mono,monospace', color:'var(--dvas-text)' }}>{sortedLogs.length}</span> entries
              {sortedLogs.length!==allLogs.length&&<span style={{ color:'var(--dvas-blue)' }}> (filtered from {allLogs.length})</span>}
            </div>
            <div style={{ display:'flex', gap:4 }}>
              <button data-testid="page-prev" disabled={safePage===1} onClick={()=>setPage(p=>p-1)} style={{ width:32, height:32, display:'flex', alignItems:'center', justifyContent:'center', borderRadius:8, cursor:safePage===1?'not-allowed':'pointer', background:'var(--dvas-elev)', border:'none', color:'var(--dvas-text)', opacity:safePage===1?0.4:1 }}><ChevronLeft size={15}/></button>
              {Array.from({length:Math.min(5,totalPages)},(_,i)=>{
                let p; if(totalPages<=5)p=i+1; else if(safePage<=3)p=i+1; else if(safePage>=totalPages-2)p=totalPages-4+i; else p=safePage-2+i;
                return <button key={p} data-testid={`page-btn-${p}`} onClick={()=>setPage(p)} style={{ width:32, height:32, display:'flex', alignItems:'center', justifyContent:'center', borderRadius:8, cursor:'pointer', fontSize:12, fontWeight:600, fontFamily:'JetBrains Mono,monospace', background:safePage===p?'var(--dvas-blue)':'var(--dvas-elev)', color:safePage===p?'#fff':'var(--dvas-text)', border:'none' }}>{p}</button>;
              })}
              <button data-testid="page-next" disabled={safePage===totalPages} onClick={()=>setPage(p=>p+1)} style={{ width:32, height:32, display:'flex', alignItems:'center', justifyContent:'center', borderRadius:8, cursor:safePage===totalPages?'not-allowed':'pointer', background:'var(--dvas-elev)', border:'none', color:'var(--dvas-text)', opacity:safePage===totalPages?0.4:1 }}><ChevronRight size={15}/></button>
            </div>
          </div>
        )}
      </div>

      {/* Detail Drawer */}
      {selectedLog && <DetailDrawer log={selectedLog} onClose={()=>setSelectedLog(null)}/>}

      <style>{`@keyframes auditSpin { to { transform: rotate(360deg); } }`}</style>
    </AppShell>
  );
}
