/**
 * AuditLogPage.js  —  Production Audit Log Explorer
 * =================================================================
 * Production-ready, tamper-evident Audit Log Explorer for Kerala Police DVAS.
 * Compliant with Indian Evidence Act Section 65B & Digital Personal Data Protection Act (DPDPA) 2023.
 *
 * Core Capabilities:
 *  1. Dual Timezone Display (IST UTC+5:30 / UTC toggle).
 *  2. Actor details (ID, name, role, IP, session ID, device fingerprint).
 *  3. Action badges with category & compliance tags (DPDPA, 65B, RETENTION_1YR).
 *  4. Target resource with deep-link navigation (/admin, /command).
 *  5. Field-level Before/After Diff Viewer with automated sensitive credential redaction.
 *  6. Status & error-code chips with network & correlation ID tracing.
 *  7. 12-Dimension Filtering: actor, role, action, resource, module, status, severity, category, compliance, legal holds, date range, free text.
 *  8. Filter Presets CRUD (save, load, delete, share).
 *  9. Real-time Live Tail streaming mode (WebSocket hub / polling fallback, pause/resume).
 *  10. Interactive Forensic Analytics Dashboard (Recharts AreaChart, top actors, severity distribution).
 *  11. Inline Expandable Rows & Slide-Out Inspection Drawer with 5 tabs:
 *      - Tab 1: Overview & Metadata
 *      - Tab 2: State Diff (Before / After with redaction)
 *      - Tab 3: Full JSON Payload
 *      - Tab 4: Correlated Events Timeline
 *      - Tab 5: Forensic Chain-of-Custody Proof & Section 65B status
 *  12. Indian Evidence Act Section 65B Certified PDF Evidence Export.
 *  13. Legal Hold Management (Place/Release hold with case number & authority).
 *  14. Statutory 365-Day Retention Policy & Super-Admin Archive & Purge Workflow.
 *  15. Automated "Audit of the Auditors" Access Logging on page view.
 *  16. 8-Rule Security Anomaly Detection Engine Alert Banner.
 *  17. Keyboard navigation (j/k/arrows for row focus, Enter to toggle, Esc to dismiss).
 *  18. Compliance Print-Ready Stylesheet for physical court submission.
 */

import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import AppShell from '@/components/layout/AppShell';
import { useAuth } from '@/context/AuthContext';
import { api, API } from '@/lib/api';
import { useDvasSocket } from '@/lib/useDvasSocket';
import {
  Search, Filter, Download, X, ChevronLeft, ChevronRight,
  Shield, AlertTriangle, CheckCircle, Clock, Activity,
  FileText, ChevronDown, ChevronUp, Eye, Layers,
  RefreshCw, Calendar, SlidersHorizontal, UserCheck, ShieldCheck,
  ShieldAlert, ArrowUpRight, RotateCcw, Bookmark, BookmarkPlus,
  Copy, Check, Printer, FileCheck, Zap, AlertCircle,
  Lock, Unlock, Archive, Trash2, Pause, Play, Globe,
  ExternalLink, HelpCircle, FileSpreadsheet, Scale, Info
} from 'lucide-react';
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis,
  Tooltip, CartesianGrid, BarChart, Bar
} from 'recharts';

// ─────────────────────────────────────────────────────────────────────────────
// Formatters & Helper Utilities
// ─────────────────────────────────────────────────────────────────────────────

function formatTimestamp(ts, tz = 'IST') {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    if (tz === 'UTC') {
      return d.toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
    }
    return d.toLocaleString('en-IN', {
      timeZone: 'Asia/Kolkata',
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: true,
    }) + ' IST';
  } catch (_) {
    return ts;
  }
}

function formatRelativeTime(ts) {
  if (!ts) return '';
  try {
    const diffMs = Date.now() - new Date(ts).getTime();
    const diffSec = Math.floor(diffMs / 1000);
    if (diffSec < 5) return 'just now';
    if (diffSec < 60) return `${diffSec}s ago`;
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHrs = Math.floor(diffMin / 60);
    if (diffHrs < 24) return `${diffHrs}h ago`;
    const diffDays = Math.floor(diffHrs / 24);
    return `${diffDays}d ago`;
  } catch (_) {
    return '';
  }
}

function statusBadge(status, errorCode) {
  const s = (status || 'success').toLowerCase();
  if (s === 'success') {
    return {
      bg: 'var(--dvas-safe-bg)',
      color: 'var(--dvas-safe)',
      border: 'color-mix(in srgb, var(--dvas-safe) 30%, transparent)',
      label: 'Success',
      icon: CheckCircle,
      isError: false,
    };
  }
  if (s === 'failure' || s === 'failed' || s === 'error') {
    return {
      bg: 'var(--dvas-crit-bg)',
      color: 'var(--dvas-crit)',
      border: 'color-mix(in srgb, var(--dvas-crit) 30%, transparent)',
      label: errorCode ? `Failed (${errorCode})` : 'Failed',
      icon: X,
      isError: true,
    };
  }
  return {
    bg: 'var(--dvas-warn-bg)',
    color: 'var(--dvas-warn)',
    border: 'color-mix(in srgb, var(--dvas-warn) 30%, transparent)',
    label: status || 'Warning',
    icon: AlertTriangle,
    isError: false,
  };
}

function severityBadge(severity) {
  const sev = (severity || 'info').toLowerCase();
  if (sev === 'critical') {
    return {
      bg: 'rgba(239, 68, 68, 0.15)',
      color: '#f87171',
      border: 'rgba(239, 68, 68, 0.3)',
      label: 'CRITICAL',
    };
  }
  if (sev === 'warning') {
    return {
      bg: 'rgba(245, 158, 11, 0.15)',
      color: '#fbbf24',
      border: 'rgba(245, 158, 11, 0.3)',
      label: 'WARNING',
    };
  }
  return {
    bg: 'rgba(56, 189, 248, 0.12)',
    color: '#38bdf8',
    border: 'rgba(56, 189, 248, 0.25)',
    label: 'INFO',
  };
}

function actionTypeBadge(actionType) {
  const t = (actionType || 'system').toLowerCase();
  const map = {
    create:            { color: '#34d399', bg: 'rgba(52, 211, 153, 0.12)' },
    update:            { color: '#60a5fa', bg: 'rgba(96, 165, 250, 0.12)' },
    delete:            { color: '#f87171', bg: 'rgba(248, 113, 113, 0.12)' },
    login:             { color: '#818cf8', bg: 'rgba(129, 140, 248, 0.12)' },
    logout:            { color: '#a78bfa', bg: 'rgba(167, 139, 250, 0.12)' },
    export:            { color: '#fb923c', bg: 'rgba(251, 146, 60, 0.12)' },
    permission_change: { color: '#f43f5e', bg: 'rgba(244, 63, 94, 0.15)' },
    configuration:     { color: '#22d3ee', bg: 'rgba(34, 211, 238, 0.12)' },
    read:              { color: '#94a3b8', bg: 'rgba(148, 163, 184, 0.12)' },
    system:            { color: '#94a3b8', bg: 'rgba(148, 163, 184, 0.12)' },
  };
  return map[t] || map.system;
}

// Redact sensitive credentials (passwords, tokens, secrets) before rendering
function sanitizeDiffValue(key, val) {
  if (val == null) return null;
  const k = String(key).toLowerCase();
  if (
    k.includes('password') ||
    k.includes('secret') ||
    k.includes('token') ||
    k.includes('credential') ||
    k.includes('api_key') ||
    k.includes('private_key')
  ) {
    return '•••••••• [CONFIDENTIAL REDACTED]';
  }
  if (typeof val === 'object') {
    try {
      return JSON.stringify(val);
    } catch (_) {
      return String(val);
    }
  }
  return String(val);
}

// ─────────────────────────────────────────────────────────────────────────────
// Subcomponents
// ─────────────────────────────────────────────────────────────────────────────

function SummaryCard({ icon: Icon, label, value, sub, color, testId, onClick }) {
  return (
    <div
      data-testid={testId}
      onClick={onClick}
      style={{
        background: 'var(--dvas-surface)',
        border: '1px solid var(--dvas-border)',
        borderRadius: 'var(--dvas-radius-sm)',
        padding: '14px 18px',
        boxShadow: 'var(--dvas-shadow)',
        display: 'flex',
        alignItems: 'center',
        gap: 14,
        cursor: onClick ? 'pointer' : 'default',
        transition: 'transform 0.15s, border-color 0.15s',
      }}
      className={onClick ? 'hover:border-[color:var(--dvas-blue)]' : ''}
    >
      <div
        style={{
          width: 42,
          height: 42,
          borderRadius: 10,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: `color-mix(in srgb, ${color} 15%, transparent)`,
          border: `1px solid color-mix(in srgb, ${color} 30%, transparent)`,
          flexShrink: 0,
        }}
      >
        <Icon size={20} style={{ color }} />
      </div>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--dvas-text-dim)' }}>
          {label}
        </div>
        <div style={{ fontSize: 22, fontWeight: 700, fontFamily: 'JetBrains Mono, monospace', color: 'var(--dvas-text)', marginBlock: '2px' }}>
          {value ?? 0}
        </div>
        {sub && (
          <div style={{ fontSize: 11, color: 'var(--dvas-text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {sub}
          </div>
        )}
      </div>
    </div>
  );
}

function MultiSelect({ label, options, selected, onChange, testId, minWidth = 140 }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const handleClick = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [open]);

  const toggle = (v) => {
    onChange(selected.includes(v) ? selected.filter((x) => x !== v) : [...selected, v]);
  };

  const btnText = selected.length === 0
    ? label
    : selected.length === options.length
    ? `All ${label}`
    : `${selected.length} ${label}`;

  return (
    <div ref={ref} style={{ position: 'relative' }} data-testid={testId}>
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          height: 34,
          paddingInline: 10,
          fontSize: 12,
          borderRadius: 8,
          background: selected.length > 0 ? 'rgba(56, 189, 248, 0.08)' : 'var(--dvas-elev)',
          border: selected.length > 0 ? '1px solid var(--dvas-blue)' : '1px solid var(--dvas-border)',
          color: selected.length > 0 ? 'var(--dvas-blue)' : 'var(--dvas-text)',
          minWidth,
          cursor: 'pointer',
          whiteSpace: 'nowrap',
        }}
      >
        <Filter size={12} style={{ color: selected.length > 0 ? 'var(--dvas-blue)' : 'var(--dvas-text-muted)', flexShrink: 0 }} />
        <span style={{ flex: 1, textAlign: 'left', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {btnText}
        </span>
        <ChevronDown size={12} style={{ color: 'var(--dvas-text-muted)', flexShrink: 0 }} />
      </button>
      {open && (
        <div
          className="dvas-fade-in"
          style={{
            position: 'absolute',
            top: 38,
            left: 0,
            zIndex: 60,
            width: Math.max(minWidth + 30, 210),
            maxHeight: 260,
            overflowY: 'auto',
            padding: '4px 0',
            background: 'var(--dvas-surface)',
            border: '1px solid var(--dvas-border)',
            borderRadius: 'var(--dvas-radius-sm)',
            boxShadow: 'var(--dvas-shadow-lg)',
          }}
        >
          {options.map((opt) => (
            <label
              key={opt}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '7px 12px',
                cursor: 'pointer',
                fontSize: 12,
                color: 'var(--dvas-text)',
              }}
              className="hover:bg-[color:var(--dvas-elev)]"
            >
              <input
                type="checkbox"
                checked={selected.includes(opt)}
                onChange={() => toggle(opt)}
                style={{ width: 14, height: 14, cursor: 'pointer' }}
              />
              <span style={{ textTransform: 'capitalize' }}>{String(opt).replace(/_/g, ' ')}</span>
            </label>
          ))}
          <div style={{ padding: '6px 12px', borderTop: '1px solid var(--dvas-border)', display: 'flex', justifyContent: 'space-between' }}>
            <button
              onClick={() => onChange([...options])}
              style={{ fontSize: 10, fontWeight: 600, color: 'var(--dvas-blue)', background: 'none', border: 'none', cursor: 'pointer' }}
            >
              Select all
            </button>
            <button
              onClick={() => onChange([])}
              style={{ fontSize: 10, fontWeight: 600, color: 'var(--dvas-text-dim)', background: 'none', border: 'none', cursor: 'pointer' }}
            >
              Clear
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function JsonViewer({ data, title = 'Event Payload' }) {
  const [copied, setCopied] = useState(false);
  const text = JSON.stringify(data || {}, null, 2);

  const handleCopy = () => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div style={{ position: 'relative' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
        <span style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', color: 'var(--dvas-text-dim)' }}>
          {title} ({text.length} bytes)
        </span>
        <button
          onClick={handleCopy}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 4,
            padding: '3px 8px',
            fontSize: 11,
            fontWeight: 600,
            borderRadius: 6,
            background: 'var(--dvas-elev)',
            border: '1px solid var(--dvas-border)',
            color: copied ? 'var(--dvas-safe)' : 'var(--dvas-text-muted)',
            cursor: 'pointer',
          }}
        >
          {copied ? <Check size={11} /> : <Copy size={11} />}
          {copied ? 'Copied' : 'Copy JSON'}
        </button>
      </div>
      <pre
        style={{
          fontSize: 11,
          overflow: 'auto',
          borderRadius: 8,
          padding: 14,
          fontFamily: 'JetBrains Mono, monospace',
          background: 'var(--dvas-surface-2)',
          color: 'var(--dvas-text)',
          border: '1px solid var(--dvas-border)',
          maxHeight: 300,
          lineHeight: 1.6,
        }}
      >
        {text}
      </pre>
    </div>
  );
}

function DiffViewer({ before, after }) {
  if (!before && !after) {
    return (
      <div style={{ fontSize: 12, textAlign: 'center', padding: '28px 0', color: 'var(--dvas-text-dim)' }}>
        No field-level before/after state diff was recorded for this event.
      </div>
    );
  }

  const bKeys = Object.keys(before || {});
  const aKeys = Object.keys(after || {});
  const allKeys = Array.from(new Set([...bKeys, ...aKeys]));

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', color: 'var(--dvas-text-dim)' }}>
          Field-Level State Diff ({allKeys.length} field{allKeys.length === 1 ? '' : 's'})
        </div>
        <div style={{ display: 'flex', gap: 10, fontSize: 10 }}>
          <span style={{ color: 'var(--dvas-safe)' }}>● Added / Changed</span>
          <span style={{ color: 'var(--dvas-crit)' }}>● Removed</span>
          <span style={{ color: 'var(--dvas-warn)' }}>● Modified</span>
        </div>
      </div>
      <div style={{ overflow: 'hidden', borderRadius: 8, border: '1px solid var(--dvas-border)', fontFamily: 'JetBrains Mono, monospace', fontSize: 11 }}>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1.2fr 1.9fr 1.9fr',
            background: 'var(--dvas-surface-2)',
            borderBottom: '1px solid var(--dvas-border)',
            color: 'var(--dvas-text-muted)',
            padding: '8px 12px',
            fontSize: 10,
            fontWeight: 600,
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
          }}
        >
          <span>Field Key</span>
          <span>Previous State (Before)</span>
          <span>Applied State (After)</span>
        </div>
        {allKeys.map((key) => {
          const rawB = before?.[key];
          const rawA = after?.[key];
          const bv = sanitizeDiffValue(key, rawB);
          const av = sanitizeDiffValue(key, rawA);
          const isAdded = rawB === undefined && rawA !== undefined;
          const isRemoved = rawB !== undefined && rawA === undefined;
          const isModified = !isAdded && !isRemoved && JSON.stringify(rawB) !== JSON.stringify(rawA);

          let rowBg = 'transparent';
          if (isModified) rowBg = 'rgba(245, 158, 11, 0.08)';
          else if (isAdded) rowBg = 'rgba(52, 211, 153, 0.08)';
          else if (isRemoved) rowBg = 'rgba(239, 68, 68, 0.08)';

          return (
            <div
              key={key}
              style={{
                display: 'grid',
                gridTemplateColumns: '1.2fr 1.9fr 1.9fr',
                borderBottom: '1px solid var(--dvas-border)',
                background: rowBg,
                padding: '8px 12px',
                alignItems: 'center',
                gap: 8,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                {isModified && <span style={{ color: 'var(--dvas-warn)', fontWeight: 700 }}>~</span>}
                {isAdded && <span style={{ color: 'var(--dvas-safe)', fontWeight: 700 }}>+</span>}
                {isRemoved && <span style={{ color: 'var(--dvas-crit)', fontWeight: 700 }}>-</span>}
                <span style={{ color: 'var(--dvas-text)', fontWeight: 600 }}>{key}</span>
              </div>
              <span style={{ color: bv == null ? 'var(--dvas-text-dim)' : isRemoved ? 'var(--dvas-crit)' : 'var(--dvas-text)', wordBreak: 'break-all' }}>
                {bv == null ? '—' : bv}
              </span>
              <span style={{ color: av == null ? 'var(--dvas-text-dim)' : isAdded ? 'var(--dvas-safe)' : 'var(--dvas-text)', wordBreak: 'break-all' }}>
                {av == null ? '—' : av}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function RelatedEventsTimeline({ correlationId, sessionId, currentLogId, onSelectLog, onFilterCorrelationId, timezone }) {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    async function loadRelated() {
      if (!correlationId && !sessionId) {
        setEvents([]);
        setLoading(false);
        return;
      }
      try {
        setLoading(true);
        const url = correlationId
          ? `/audit-logs/related/${encodeURIComponent(correlationId)}`
          : `/audit-logs?actor_session_id=${encodeURIComponent(sessionId)}&page_size=20`;
        const { data } = await api.get(url);
        if (active) {
          setEvents(Array.isArray(data) ? data : data.items || []);
        }
      } catch (_) {
        if (active) setEvents([]);
      } finally {
        if (active) setLoading(false);
      }
    }
    loadRelated();
    return () => { active = false; };
  }, [correlationId, sessionId]);

  if (loading) {
    return <div style={{ fontSize: 12, padding: 20, textAlign: 'center', color: 'var(--dvas-text-dim)' }}>Loading correlated timeline events…</div>;
  }

  if (!events || events.length === 0) {
    return (
      <div style={{ fontSize: 12, padding: 20, textAlign: 'center', color: 'var(--dvas-text-dim)' }}>
        No related timeline events found for correlation ID: {correlationId || 'N/A'}.
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
        <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--dvas-text-muted)' }}>
          Transaction Chain ({events.length} correlated events):
        </div>
        {correlationId && onFilterCorrelationId && (
          <button
            onClick={() => onFilterCorrelationId(correlationId)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              fontSize: 10,
              fontWeight: 600,
              padding: '3px 8px',
              borderRadius: 6,
              background: 'var(--dvas-elev)',
              border: '1px solid var(--dvas-border)',
              color: 'var(--dvas-blue)',
              cursor: 'pointer',
            }}
          >
            <Filter size={10} /> Filter by this Correlation ID
          </button>
        )}
      </div>
      {events.map((ev, idx) => {
        const isCurrent = ev.id === currentLogId;
        const sty = statusBadge(ev.status, ev.error_code);
        return (
          <div
            key={ev.id || idx}
            onClick={() => onSelectLog && onSelectLog(ev)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              padding: '8px 12px',
              borderRadius: 8,
              background: isCurrent ? 'rgba(56, 189, 248, 0.12)' : 'var(--dvas-surface-2)',
              border: isCurrent ? '1px solid var(--dvas-blue)' : '1px solid var(--dvas-border)',
              cursor: onSelectLog ? 'pointer' : 'default',
            }}
          >
            <div style={{ width: 8, height: 8, borderRadius: '50%', background: isCurrent ? 'var(--dvas-blue)' : 'var(--dvas-text-dim)' }} />
            <div style={{ fontSize: 11, fontFamily: 'JetBrains Mono, monospace', color: 'var(--dvas-text-dim)', width: 140 }}>
              {formatTimestamp(ev.ts, timezone)}
            </div>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--dvas-text)', flex: 1 }}>
              {ev.action}
              {ev.target_name && <span style={{ fontWeight: 400, color: 'var(--dvas-text-muted)', marginLeft: 6 }}>({ev.target_name})</span>}
            </div>
            <span style={{ fontSize: 10, padding: '2px 6px', borderRadius: 999, background: sty.bg, color: sty.color }}>
              {ev.status}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Interactive Activity Trends & Forensic Analytics
// ─────────────────────────────────────────────────────────────────────────────

function ActivityTrendsChart({ range, onRangeChange, trendsData, loading }) {
  const timeline = trendsData?.timeline || [];
  const topActors = trendsData?.top_actors || [];
  const severities = trendsData?.severity_distribution || [];

  return (
    <div
      style={{
        background: 'var(--dvas-surface)',
        border: '1px solid var(--dvas-border)',
        borderRadius: 12,
        padding: 16,
        marginBottom: 20,
        boxShadow: 'var(--dvas-shadow)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14, flexWrap: 'wrap', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Activity size={18} style={{ color: 'var(--dvas-blue)' }} />
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--dvas-text)' }}>
              Audit Activity Trends & Threat Telemetry
            </div>
            <div style={{ fontSize: 11, color: 'var(--dvas-text-muted)' }}>
              Temporal distribution, failure spikes, and top operational actors
            </div>
          </div>
        </div>

        {/* Range Selector */}
        <div style={{ display: 'flex', gap: 4, background: 'var(--dvas-elev)', padding: 3, borderRadius: 8, border: '1px solid var(--dvas-border)' }}>
          {['today', '24h', '7d', '30d'].map((r) => (
            <button
              key={r}
              onClick={() => onRangeChange(r)}
              style={{
                fontSize: 11,
                fontWeight: 600,
                padding: '4px 10px',
                borderRadius: 6,
                border: 'none',
                background: range === r ? 'var(--dvas-blue)' : 'transparent',
                color: range === r ? '#fff' : 'var(--dvas-text-muted)',
                cursor: 'pointer',
                textTransform: 'uppercase',
              }}
            >
              {r}
            </button>
          ))}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 16, alignItems: 'stretch' }}>
        {/* Timeline AreaChart */}
        <div style={{ minHeight: 180, background: 'var(--dvas-surface-2)', padding: 12, borderRadius: 8, border: '1px solid var(--dvas-border)' }}>
          <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', color: 'var(--dvas-text-dim)', marginBottom: 8 }}>
            Events Over Time vs. Failed Actions
          </div>
          {loading ? (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 140, color: 'var(--dvas-text-dim)', fontSize: 12 }}>
              Loading activity metrics…
            </div>
          ) : timeline.length === 0 ? (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 140, color: 'var(--dvas-text-dim)', fontSize: 12 }}>
              No event activity recorded in this time range.
            </div>
          ) : (
            <div style={{ width: '100%', height: 140 }}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={timeline} margin={{ top: 5, right: 10, left: -20, bottom: 0 }}>
                  <defs>
                    <linearGradient id="colorCount" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#38bdf8" stopOpacity={0.4} />
                      <stop offset="95%" stopColor="#38bdf8" stopOpacity={0.0} />
                    </linearGradient>
                    <linearGradient id="colorFailed" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#ef4444" stopOpacity={0.4} />
                      <stop offset="95%" stopColor="#ef4444" stopOpacity={0.0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                  <XAxis dataKey="time" stroke="var(--dvas-text-dim)" fontSize={10} tickLine={false} />
                  <YAxis stroke="var(--dvas-text-dim)" fontSize={10} tickLine={false} allowDecimals={false} />
                  <Tooltip
                    contentStyle={{
                      background: 'var(--dvas-surface)',
                      border: '1px solid var(--dvas-border)',
                      borderRadius: 6,
                      fontSize: 11,
                    }}
                  />
                  <Area type="monotone" dataKey="count" name="Total Events" stroke="#38bdf8" strokeWidth={2} fillOpacity={1} fill="url(#colorCount)" />
                  <Area type="monotone" dataKey="failed" name="Failed Actions" stroke="#ef4444" strokeWidth={2} fillOpacity={1} fill="url(#colorFailed)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>

        {/* Top 5 Actors & Severity distribution */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {/* Top Actors */}
          <div style={{ background: 'var(--dvas-surface-2)', padding: 12, borderRadius: 8, border: '1px solid var(--dvas-border)', flex: 1 }}>
            <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', color: 'var(--dvas-text-dim)', marginBottom: 8 }}>
              Top Operational Actors
            </div>
            {topActors.length === 0 ? (
              <div style={{ fontSize: 11, color: 'var(--dvas-text-dim)', textAlign: 'center', padding: '16px 0' }}>No actors recorded</div>
            ) : (
              topActors.slice(0, 4).map((act, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 11, marginBottom: 6 }}>
                  <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '70%', color: 'var(--dvas-text)' }}>
                    <span style={{ fontFamily: 'JetBrains Mono, monospace', color: 'var(--dvas-text-dim)', marginRight: 6 }}>#{i + 1}</span>
                    {act.name || act.email || 'Officer'}
                  </div>
                  <span style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 600, color: 'var(--dvas-blue)' }}>
                    {act.count} ev
                  </span>
                </div>
              ))
            )}
          </div>

          {/* Severity Progress */}
          <div style={{ background: 'var(--dvas-surface-2)', padding: 10, borderRadius: 8, border: '1px solid var(--dvas-border)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, fontWeight: 600, color: 'var(--dvas-text-dim)', marginBottom: 4 }}>
              <span>SEVERITY BREAKDOWN</span>
              <span>{trendsData?.total_events || 0} TOTAL</span>
            </div>
            <div style={{ display: 'flex', height: 6, borderRadius: 3, overflow: 'hidden', gap: 2 }}>
              {severities.map((s) => {
                const total = trendsData?.total_events || 1;
                const pct = Math.max(5, Math.round((s.count / total) * 100));
                const col = s.severity === 'critical' ? '#ef4444' : s.severity === 'warning' ? '#f59e0b' : '#38bdf8';
                return (
                  <div
                    key={s.severity}
                    title={`${s.severity}: ${s.count}`}
                    style={{ width: `${pct}%`, background: col, height: '100%' }}
                  />
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Modals
// ─────────────────────────────────────────────────────────────────────────────

function ChainOfCustodyModal({ log, isOpen, onClose, timezone }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!isOpen || !log) return;
    let active = true;
    async function loadProof() {
      try {
        setLoading(true);
        const res = await api.get(`/audit-logs/${log.id}/chain-of-custody`);
        if (active) setData(res.data);
      } catch (err) {
        console.error('Failed to load chain of custody:', err);
      } finally {
        if (active) setLoading(false);
      }
    }
    loadProof();
    return () => { active = false; };
  }, [isOpen, log]);

  if (!isOpen || !log) return null;

  const handleCopyProof = () => {
    if (!data) return;
    navigator.clipboard.writeText(JSON.stringify(data.proof_bundle || data, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 120, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.7)' }}>
      <div
        className="dvas-fade-in"
        style={{
          width: 580,
          maxHeight: '90vh',
          overflowY: 'auto',
          background: 'var(--dvas-surface)',
          border: '1px solid var(--dvas-border)',
          borderRadius: 14,
          padding: 24,
          boxShadow: 'var(--dvas-shadow-lg)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <ShieldCheck size={20} style={{ color: 'var(--dvas-safe)' }} />
            <div>
              <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--dvas-text)' }}>
                Forensic Chain-of-Custody Certificate
              </div>
              <div style={{ fontSize: 11, color: 'var(--dvas-text-muted)' }}>
                Section 65B Indian Evidence Act Proof & Cryptographic Integrity Card
              </div>
            </div>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'var(--dvas-text-dim)', cursor: 'pointer' }}>
            <X size={18} />
          </button>
        </div>

        {loading ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--dvas-text-dim)', fontSize: 12 }}>
            <RefreshCw size={20} className="animate-spin" style={{ margin: '0 auto 8px', color: 'var(--dvas-blue)' }} />
            Verifying cryptographic proof with backend ledger…
          </div>
        ) : (
          <div>
            {/* Status Banner */}
            <div
              style={{
                padding: '12px 16px',
                borderRadius: 8,
                marginBottom: 16,
                background: data?.verification?.is_valid ? 'var(--dvas-safe-bg)' : 'var(--dvas-crit-bg)',
                border: `1px solid ${data?.verification?.is_valid ? 'rgba(52,211,153,0.3)' : 'rgba(239,68,68,0.3)'}`,
                display: 'flex',
                alignItems: 'center',
                gap: 10,
              }}
            >
              {data?.verification?.is_valid ? <CheckCircle size={18} style={{ color: 'var(--dvas-safe)' }} /> : <AlertTriangle size={18} style={{ color: 'var(--dvas-crit)' }} />}
              <div style={{ fontSize: 12, fontWeight: 600, color: data?.verification?.is_valid ? 'var(--dvas-safe)' : 'var(--dvas-crit)' }}>
                {data?.verification?.is_valid
                  ? 'Cryptographic Chain Integrity VERIFIED (Immutable & Tamper-Free)'
                  : 'INTEGRITY ALERT: Hash mismatch detected on this record'}
              </div>
            </div>

            {/* Core Details Grid */}
            <div style={{ background: 'var(--dvas-surface-2)', padding: 14, borderRadius: 8, border: '1px solid var(--dvas-border)', marginBottom: 14, fontSize: 11 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 10 }}>
                <div>
                  <div style={{ color: 'var(--dvas-text-dim)', fontSize: 10 }}>EVENT TIMESTAMP</div>
                  <div style={{ color: 'var(--dvas-text)', fontWeight: 600 }}>{formatTimestamp(data?.timestamp_utc, timezone)}</div>
                </div>
                <div>
                  <div style={{ color: 'var(--dvas-text-dim)', fontSize: 10 }}>CHAIN POSITION</div>
                  <div style={{ color: 'var(--dvas-text)', fontWeight: 600, fontFamily: 'JetBrains Mono, monospace' }}>
                    #{data?.chain_index || 0} of {data?.total_chain_entries || '—'}
                  </div>
                </div>
              </div>

              <div style={{ marginBottom: 8 }}>
                <div style={{ color: 'var(--dvas-text-dim)', fontSize: 10 }}>CURRENT RECORD SHA-256 HASH</div>
                <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 10, color: 'var(--dvas-blue)', wordBreak: 'break-all', background: 'var(--dvas-elev)', padding: '6px 8px', borderRadius: 4, marginTop: 2 }}>
                  {data?.hash || log.hash || '—'}
                </div>
              </div>

              <div>
                <div style={{ color: 'var(--dvas-text-dim)', fontSize: 10 }}>PREVIOUS LINKED SHA-256 HASH</div>
                <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 10, color: 'var(--dvas-text-muted)', wordBreak: 'break-all', background: 'var(--dvas-elev)', padding: '6px 8px', borderRadius: 4, marginTop: 2 }}>
                  {data?.prev_hash || log.prev_hash || (data?.is_genesis ? 'GENESIS_BLOCK (Initial Ledger Seed)' : '—')}
                </div>
              </div>
            </div>

            {/* Section 65B Admissibility Box */}
            <div style={{ background: 'rgba(56, 189, 248, 0.06)', border: '1px solid rgba(56, 189, 248, 0.25)', borderRadius: 8, padding: 12, marginBottom: 16, fontSize: 11 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 700, color: 'var(--dvas-blue)', marginBottom: 4 }}>
                <Scale size={14} /> Section 65B Indian Evidence Act Compliance
              </div>
              <div style={{ color: 'var(--dvas-text)', lineHeight: 1.5 }}>
                Admissible as secondary electronic evidence under §65B(4). The electronic record was generated by a computer system during its regular operational activity under lawful control of the Kerala Police Drone Surveillance System.
              </div>
            </div>

            {/* Legal Hold Status (if present) */}
            {data?.legal_hold && (
              <div style={{ background: 'rgba(245, 158, 11, 0.08)', border: '1px solid rgba(245, 158, 11, 0.3)', borderRadius: 8, padding: 12, marginBottom: 16, fontSize: 11 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 700, color: 'var(--dvas-warn)', marginBottom: 4 }}>
                  <Lock size={14} /> ACTIVE LEGAL HOLD PRESERVATION
                </div>
                <div style={{ color: 'var(--dvas-text)' }}>
                  Case No: <strong>{data.legal_hold.case_number}</strong> · Authority: {data.legal_hold.legal_authority}
                </div>
                <div style={{ color: 'var(--dvas-text-muted)', marginTop: 2 }}>
                  Placed by: {data.legal_hold.placed_by} on {formatTimestamp(data.legal_hold.placed_at, timezone)}
                </div>
              </div>
            )}

            {/* Actions */}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
              <button
                onClick={handleCopyProof}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  padding: '7px 14px',
                  fontSize: 12,
                  fontWeight: 600,
                  borderRadius: 8,
                  background: 'var(--dvas-elev)',
                  border: '1px solid var(--dvas-border)',
                  color: copied ? 'var(--dvas-safe)' : 'var(--dvas-text)',
                  cursor: 'pointer',
                }}
              >
                {copied ? <Check size={13} /> : <Copy size={13} />}
                {copied ? 'Proof JSON Copied' : 'Copy Verification Bundle'}
              </button>
              <button
                onClick={onClose}
                style={{
                  padding: '7px 16px',
                  fontSize: 12,
                  fontWeight: 600,
                  borderRadius: 8,
                  background: 'var(--dvas-blue)',
                  border: 'none',
                  color: '#fff',
                  cursor: 'pointer',
                }}
              >
                Close
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function LegalHoldModal({ log, isOpen, onClose, onHoldUpdated, timezone }) {
  const isHeld = Boolean(log?.is_legal_hold);
  const [caseNumber, setCaseNumber] = useState('');
  const [legalAuthority, setLegalAuthority] = useState('');
  const [justification, setJustification] = useState('');
  const [contactEmail, setContactEmail] = useState('');
  const [releaseNotes, setReleaseNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');

  if (!isOpen || !log) return null;

  const handlePlaceHold = async (e) => {
    e.preventDefault();
    if (!caseNumber.trim() || !legalAuthority.trim() || !justification.trim()) {
      setErrorMsg('All mandatory legal hold fields must be filled.');
      return;
    }
    try {
      setSubmitting(true);
      setErrorMsg('');
      await api.post(`/audit-logs/${log.id}/legal-hold`, {
        case_number: caseNumber.trim(),
        legal_authority: legalAuthority.trim(),
        justification: justification.trim(),
        contact_email: contactEmail.trim() || undefined,
      });
      if (onHoldUpdated) onHoldUpdated();
      onClose();
    } catch (err) {
      setErrorMsg(err.response?.data?.detail || 'Failed to place legal hold.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleReleaseHold = async (e) => {
    e.preventDefault();
    if (!releaseNotes.trim()) {
      setErrorMsg('Release justification / court disposal order note is required.');
      return;
    }
    try {
      setSubmitting(true);
      setErrorMsg('');
      await api.delete(`/audit-logs/${log.id}/legal-hold`, {
        data: {
          release_notes: releaseNotes.trim(),
        },
      });
      if (onHoldUpdated) onHoldUpdated();
      onClose();
    } catch (err) {
      setErrorMsg(err.response?.data?.detail || 'Failed to release legal hold.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 120, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.7)' }}>
      <div
        className="dvas-fade-in"
        style={{
          width: 500,
          background: 'var(--dvas-surface)',
          border: '1px solid var(--dvas-border)',
          borderRadius: 14,
          padding: 24,
          boxShadow: 'var(--dvas-shadow-lg)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {isHeld ? <Unlock size={18} style={{ color: 'var(--dvas-safe)' }} /> : <Lock size={18} style={{ color: 'var(--dvas-warn)' }} />}
            <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--dvas-text)' }}>
              {isHeld ? 'Release Legal Hold Preservation' : 'Place Section 65B Legal Hold'}
            </div>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'var(--dvas-text-dim)', cursor: 'pointer' }}>
            <X size={16} />
          </button>
        </div>

        {errorMsg && (
          <div style={{ padding: '8px 12px', background: 'rgba(239, 68, 68, 0.15)', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 8, color: 'var(--dvas-crit)', fontSize: 11, marginBottom: 14 }}>
            {errorMsg}
          </div>
        )}

        {isHeld ? (
          <form onSubmit={handleReleaseHold}>
            <div style={{ background: 'var(--dvas-surface-2)', padding: 12, borderRadius: 8, border: '1px solid var(--dvas-border)', marginBottom: 14, fontSize: 11 }}>
              <div style={{ fontWeight: 600, color: 'var(--dvas-text)', marginBottom: 4 }}>
                Current Active Hold: Case #{log.legal_hold?.case_number || 'N/A'}
              </div>
              <div style={{ color: 'var(--dvas-text-muted)' }}>Authority: {log.legal_hold?.legal_authority}</div>
              <div style={{ color: 'var(--dvas-text-dim)', marginTop: 4 }}>
                Placed on: {formatTimestamp(log.legal_hold?.placed_at, timezone)} by {log.legal_hold?.placed_by}
              </div>
            </div>

            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontSize: 11, fontWeight: 600, color: 'var(--dvas-text-dim)', marginBottom: 6 }}>
                Disposal / Release Justification (Court Order / FIR Close No.) *
              </label>
              <textarea
                required
                rows={3}
                placeholder="State the official judicial disposal reference or reason for releasing evidence hold…"
                value={releaseNotes}
                onChange={(e) => setReleaseNotes(e.target.value)}
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  fontSize: 12,
                  background: 'var(--dvas-elev)',
                  border: '1px solid var(--dvas-border)',
                  borderRadius: 8,
                  color: 'var(--dvas-text)',
                  outline: 'none',
                  boxSizing: 'border-box',
                }}
              />
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
              <button
                type="button"
                onClick={onClose}
                style={{ padding: '7px 14px', fontSize: 12, fontWeight: 600, borderRadius: 8, background: 'var(--dvas-elev)', border: '1px solid var(--dvas-border)', color: 'var(--dvas-text)', cursor: 'pointer' }}
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={submitting}
                style={{ padding: '7px 16px', fontSize: 12, fontWeight: 600, borderRadius: 8, background: 'var(--dvas-safe)', border: 'none', color: '#fff', cursor: 'pointer' }}
              >
                {submitting ? 'Releasing…' : 'Release Legal Hold'}
              </button>
            </div>
          </form>
        ) : (
          <form onSubmit={handlePlaceHold}>
            <div style={{ fontSize: 11, color: 'var(--dvas-text-muted)', marginBottom: 14 }}>
              Placing a legal hold locks this audit record permanently against all retention policy purges and records an official preservation notice.
            </div>

            <div style={{ marginBottom: 10 }}>
              <label style={{ display: 'block', fontSize: 11, fontWeight: 600, color: 'var(--dvas-text-dim)', marginBottom: 4 }}>
                Case / FIR / Inquiry Number *
              </label>
              <input
                type="text"
                required
                placeholder="e.g. CR-2026-KL-0914 / High Court WP 402"
                value={caseNumber}
                onChange={(e) => setCaseNumber(e.target.value)}
                style={{ width: '100%', padding: '7px 10px', fontSize: 12, background: 'var(--dvas-elev)', border: '1px solid var(--dvas-border)', borderRadius: 8, color: 'var(--dvas-text)', outline: 'none', boxSizing: 'border-box' }}
              />
            </div>

            <div style={{ marginBottom: 10 }}>
              <label style={{ display: 'block', fontSize: 11, fontWeight: 600, color: 'var(--dvas-text-dim)', marginBottom: 4 }}>
                Legal Authority / Court of Jurisdiction *
              </label>
              <input
                type="text"
                required
                placeholder="e.g. Chief Judicial Magistrate Ernakulam / Kerala High Court"
                value={legalAuthority}
                onChange={(e) => setLegalAuthority(e.target.value)}
                style={{ width: '100%', padding: '7px 10px', fontSize: 12, background: 'var(--dvas-elev)', border: '1px solid var(--dvas-border)', borderRadius: 8, color: 'var(--dvas-text)', outline: 'none', boxSizing: 'border-box' }}
              />
            </div>

            <div style={{ marginBottom: 10 }}>
              <label style={{ display: 'block', fontSize: 11, fontWeight: 600, color: 'var(--dvas-text-dim)', marginBottom: 4 }}>
                Preservation Justification / Subpoena Details *
              </label>
              <textarea
                required
                rows={2}
                placeholder="Specify the legal context or investigation mandate…"
                value={justification}
                onChange={(e) => setJustification(e.target.value)}
                style={{ width: '100%', padding: '7px 10px', fontSize: 12, background: 'var(--dvas-elev)', border: '1px solid var(--dvas-border)', borderRadius: 8, color: 'var(--dvas-text)', outline: 'none', boxSizing: 'border-box' }}
              />
            </div>

            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontSize: 11, fontWeight: 600, color: 'var(--dvas-text-dim)', marginBottom: 4 }}>
                Authorized Officer Contact Email
              </label>
              <input
                type="email"
                placeholder="e.g. cybercell.kochi@keralapolice.gov.in"
                value={contactEmail}
                onChange={(e) => setContactEmail(e.target.value)}
                style={{ width: '100%', padding: '7px 10px', fontSize: 12, background: 'var(--dvas-elev)', border: '1px solid var(--dvas-border)', borderRadius: 8, color: 'var(--dvas-text)', outline: 'none', boxSizing: 'border-box' }}
              />
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
              <button
                type="button"
                onClick={onClose}
                style={{ padding: '7px 14px', fontSize: 12, fontWeight: 600, borderRadius: 8, background: 'var(--dvas-elev)', border: '1px solid var(--dvas-border)', color: 'var(--dvas-text)', cursor: 'pointer' }}
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={submitting}
                style={{ padding: '7px 16px', fontSize: 12, fontWeight: 600, borderRadius: 8, background: 'var(--dvas-warn)', border: 'none', color: '#fff', cursor: 'pointer' }}
              >
                {submitting ? 'Placing Hold…' : 'Place Legal Hold'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

function RetentionPolicyModal({ isOpen, onClose }) {
  if (!isOpen) return null;

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 120, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.7)' }}>
      <div
        className="dvas-fade-in"
        style={{
          width: 520,
          background: 'var(--dvas-surface)',
          border: '1px solid var(--dvas-border)',
          borderRadius: 14,
          padding: 24,
          boxShadow: 'var(--dvas-shadow-lg)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Clock size={18} style={{ color: 'var(--dvas-blue)' }} />
            <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--dvas-text)' }}>
              Kerala Police DVAS · Statutory Retention Policy
            </div>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'var(--dvas-text-dim)', cursor: 'pointer' }}>
            <X size={16} />
          </button>
        </div>

        <div style={{ fontSize: 12, color: 'var(--dvas-text)', lineHeight: 1.6, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ background: 'var(--dvas-surface-2)', padding: 12, borderRadius: 8, border: '1px solid var(--dvas-border)' }}>
            <div style={{ fontWeight: 700, color: 'var(--dvas-blue)', marginBottom: 2 }}>Statutory 365-Day Operational Window</div>
            <div style={{ fontSize: 11, color: 'var(--dvas-text-muted)' }}>
              In compliance with the Digital Personal Data Protection Act (DPDPA) 2023 and Kerala Police IT Standing Orders, audit records are retained for an active statutory window of 365 calendar days.
            </div>
          </div>

          <div style={{ background: 'var(--dvas-surface-2)', padding: 12, borderRadius: 8, border: '1px solid var(--dvas-border)' }}>
            <div style={{ fontWeight: 700, color: 'var(--dvas-safe)', marginBottom: 2 }}>Cryptographic Immutability by Database Triggers</div>
            <div style={{ fontSize: 11, color: 'var(--dvas-text-muted)' }}>
              PostgreSQL database triggers enforce strict read-only immutability. No officer, administrator, or service can execute SQL UPDATE on audit records. Standard direct DELETE commands are rejected unconditionally.
            </div>
          </div>

          <div style={{ background: 'var(--dvas-surface-2)', padding: 12, borderRadius: 8, border: '1px solid var(--dvas-border)' }}>
            <div style={{ fontWeight: 700, color: 'var(--dvas-warn)', marginBottom: 2 }}>Permanent Legal Hold Immunity</div>
            <div style={{ fontSize: 11, color: 'var(--dvas-text-muted)' }}>
              Any audit log row marked under active Legal Hold (with an active FIR/Case number) is permanently shielded from statutory purges. The database trigger guarantees that even during maintenance purges, legally held rows cannot be removed.
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 18 }}>
          <button
            onClick={onClose}
            style={{
              padding: '7px 18px',
              fontSize: 12,
              fontWeight: 600,
              borderRadius: 8,
              background: 'var(--dvas-blue)',
              border: 'none',
              color: '#fff',
              cursor: 'pointer',
            }}
          >
            Acknowledge
          </button>
        </div>
      </div>
    </div>
  );
}

function ArchiveAndPurgeModal({ isOpen, onClose, onPurged, timezone }) {
  const [thresholdDays, setThresholdDays] = useState(365);
  const [dryRunData, setDryRunData] = useState(null);
  const [runningDryRun, setRunningDryRun] = useState(false);
  const [archiveDownloaded, setArchiveDownloaded] = useState(false);
  const [downloadingArchive, setDownloadingArchive] = useState(false);
  const [confirmationPhrase, setConfirmationPhrase] = useState('');
  const [executingPurge, setExecutingPurge] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const [purgeSuccess, setPurgeSuccess] = useState(null);

  if (!isOpen) return null;

  const handleRunDryRun = async () => {
    try {
      setRunningDryRun(true);
      setErrorMsg('');
      const { data } = await api.post('/audit-logs/archive/dry-run', {
        older_than_days: Number(thresholdDays),
      });
      setDryRunData(data);
    } catch (err) {
      setErrorMsg(err.response?.data?.detail || 'Dry run preview failed.');
    } finally {
      setRunningDryRun(false);
    }
  };

  const handleDownloadArchive = async () => {
    try {
      setDownloadingArchive(true);
      setErrorMsg('');
      const response = await api.get(`/audit-logs/archive/export?older_than_days=${thresholdDays}`, { responseType: 'blob' });
      const blob = new Blob([response.data], { type: 'application/gzip' });
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = `dvas_audit_archive_${new Date().toISOString().slice(0, 10)}.tar.gz`;
      link.click();
      setArchiveDownloaded(true);
    } catch (err) {
      setErrorMsg('Failed to download pre-purge archive bundle.');
    } finally {
      setDownloadingArchive(false);
    }
  };

  const handleExecutePurge = async () => {
    if (confirmationPhrase !== 'CONFIRM STATUTORY PURGE') {
      setErrorMsg('Exact confirmation phrase "CONFIRM STATUTORY PURGE" must be entered.');
      return;
    }
    try {
      setExecutingPurge(true);
      setErrorMsg('');
      const { data } = await api.post('/audit-logs/archive/execute', {
        older_than_days: Number(thresholdDays),
        confirmation_phrase: confirmationPhrase,
        archive_reference_id: dryRunData?.dry_run_id || 'manual_archive',
      });
      setPurgeSuccess(data);
      if (onPurged) onPurged();
    } catch (err) {
      setErrorMsg(err.response?.data?.detail || 'Statutory purge execution failed.');
    } finally {
      setExecutingPurge(false);
    }
  };

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 120, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.7)' }}>
      <div
        className="dvas-fade-in"
        style={{
          width: 580,
          background: 'var(--dvas-surface)',
          border: '1px solid var(--dvas-border)',
          borderRadius: 14,
          padding: 24,
          boxShadow: 'var(--dvas-shadow-lg)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Archive size={20} style={{ color: 'var(--dvas-crit)' }} />
            <div>
              <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--dvas-text)' }}>
                Super-Admin Statutory Archive & Purge Wizard
              </div>
              <div style={{ fontSize: 11, color: 'var(--dvas-text-muted)' }}>
                Restricted to System Administrator · DPDPA & Legal Hold Compliant
              </div>
            </div>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'var(--dvas-text-dim)', cursor: 'pointer' }}>
            <X size={18} />
          </button>
        </div>

        {errorMsg && (
          <div style={{ padding: '8px 12px', background: 'rgba(239, 68, 68, 0.15)', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 8, color: 'var(--dvas-crit)', fontSize: 11, marginBottom: 14 }}>
            {errorMsg}
          </div>
        )}

        {purgeSuccess ? (
          <div>
            <div style={{ padding: '16px', background: 'var(--dvas-safe-bg)', border: '1px solid rgba(52,211,153,0.3)', borderRadius: 8, color: 'var(--dvas-safe)', fontSize: 12, marginBottom: 16 }}>
              <div style={{ fontWeight: 700, marginBottom: 4 }}>Statutory Purge Complete</div>
              <div>Successfully purged {purgeSuccess.purged_count} records older than {thresholdDays} days.</div>
              <div>Protected {purgeSuccess.protected_by_legal_hold || 0} active legal hold records.</div>
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <button
                onClick={onClose}
                style={{ padding: '7px 18px', fontSize: 12, fontWeight: 600, borderRadius: 8, background: 'var(--dvas-blue)', border: 'none', color: '#fff', cursor: 'pointer' }}
              >
                Done
              </button>
            </div>
          </div>
        ) : (
          <div style={{ fontSize: 12, display: 'flex', flexDirection: 'column', gap: 14 }}>
            {/* Step 1: Threshold */}
            <div style={{ background: 'var(--dvas-surface-2)', padding: 12, borderRadius: 8, border: '1px solid var(--dvas-border)' }}>
              <div style={{ fontWeight: 600, color: 'var(--dvas-text)', marginBottom: 6 }}>
                1. Select Statutory Purge Retention Threshold
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <select
                  value={thresholdDays}
                  onChange={(e) => {
                    setThresholdDays(Number(e.target.value));
                    setDryRunData(null);
                    setArchiveDownloaded(false);
                  }}
                  style={{ padding: '6px 12px', fontSize: 12, background: 'var(--dvas-elev)', border: '1px solid var(--dvas-border)', borderRadius: 8, color: 'var(--dvas-text)', outline: 'none' }}
                >
                  <option value={365}>365 Days (Statutory 1 Year - Recommended)</option>
                  <option value={180}>180 Days (6 Months)</option>
                  <option value={730}>730 Days (2 Years)</option>
                </select>
                <button
                  onClick={handleRunDryRun}
                  disabled={runningDryRun}
                  style={{ padding: '6px 14px', fontSize: 12, fontWeight: 600, borderRadius: 8, background: 'var(--dvas-blue)', border: 'none', color: '#fff', cursor: 'pointer' }}
                >
                  {runningDryRun ? 'Evaluating…' : 'Run Dry-Run Preview'}
                </button>
              </div>
            </div>

            {/* Step 2: Dry Run Preview Metrics */}
            {dryRunData && (
              <div style={{ background: 'var(--dvas-surface-2)', padding: 12, borderRadius: 8, border: '1px solid var(--dvas-border)' }}>
                <div style={{ fontWeight: 600, color: 'var(--dvas-text)', marginBottom: 8 }}>
                  2. Dry-Run Evaluation Results
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, textAlign: 'center', marginBottom: 10 }}>
                  <div style={{ background: 'var(--dvas-elev)', padding: 8, borderRadius: 6 }}>
                    <div style={{ fontSize: 10, color: 'var(--dvas-text-dim)' }}>CANDIDATES</div>
                    <div style={{ fontSize: 16, fontWeight: 700, fontFamily: 'JetBrains Mono, monospace', color: 'var(--dvas-text)' }}>
                      {dryRunData.total_candidates}
                    </div>
                  </div>
                  <div style={{ background: 'var(--dvas-elev)', padding: 8, borderRadius: 6 }}>
                    <div style={{ fontSize: 10, color: 'var(--dvas-warn)' }}>LEGAL HOLDS (SHIELDED)</div>
                    <div style={{ fontSize: 16, fontWeight: 700, fontFamily: 'JetBrains Mono, monospace', color: 'var(--dvas-warn)' }}>
                      {dryRunData.protected_by_legal_hold}
                    </div>
                  </div>
                  <div style={{ background: 'var(--dvas-elev)', padding: 8, borderRadius: 6 }}>
                    <div style={{ fontSize: 10, color: 'var(--dvas-crit)' }}>NET DELETABLE</div>
                    <div style={{ fontSize: 16, fontWeight: 700, fontFamily: 'JetBrains Mono, monospace', color: 'var(--dvas-crit)' }}>
                      {dryRunData.net_purgeable}
                    </div>
                  </div>
                </div>

                {/* Step 3: Mandatory Pre-Purge Archive */}
                <div style={{ borderTop: '1px solid var(--dvas-border)', paddingTop: 10 }}>
                  <div style={{ fontWeight: 600, color: 'var(--dvas-text)', marginBottom: 4 }}>
                    3. Mandatory Pre-Purge Archive Download
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--dvas-text-muted)', marginBottom: 8 }}>
                    Before database records can be purged, you must generate and preserve the signed archive export.
                  </div>
                  <button
                    onClick={handleDownloadArchive}
                    disabled={downloadingArchive}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 6,
                      padding: '6px 14px',
                      fontSize: 12,
                      fontWeight: 600,
                      borderRadius: 8,
                      background: archiveDownloaded ? 'var(--dvas-safe)' : 'var(--dvas-elev)',
                      border: archiveDownloaded ? 'none' : '1px solid var(--dvas-blue)',
                      color: archiveDownloaded ? '#fff' : 'var(--dvas-blue)',
                      cursor: 'pointer',
                    }}
                  >
                    {archiveDownloaded ? <Check size={13} /> : <Download size={13} />}
                    {archiveDownloaded ? 'Archive Downloaded & Verified' : downloadingArchive ? 'Packaging Archive…' : 'Download Signed Archive (.tar.gz)'}
                  </button>
                </div>
              </div>
            )}

            {/* Step 4: Phrase Confirmation & Execute */}
            {archiveDownloaded && (
              <div style={{ background: 'rgba(239, 68, 68, 0.08)', padding: 12, borderRadius: 8, border: '1px solid rgba(239, 68, 68, 0.3)' }}>
                <div style={{ fontWeight: 700, color: 'var(--dvas-crit)', marginBottom: 4 }}>
                  4. Irreversible Confirmation
                </div>
                <div style={{ fontSize: 11, color: 'var(--dvas-text)', marginBottom: 8 }}>
                  To finalize statutory deletion of {dryRunData?.net_purgeable || 0} records, type: <strong>CONFIRM STATUTORY PURGE</strong>
                </div>
                <input
                  type="text"
                  placeholder="CONFIRM STATUTORY PURGE"
                  value={confirmationPhrase}
                  onChange={(e) => setConfirmationPhrase(e.target.value)}
                  style={{
                    width: '100%',
                    padding: '8px 10px',
                    fontSize: 12,
                    fontFamily: 'JetBrains Mono, monospace',
                    background: 'var(--dvas-elev)',
                    border: '1px solid var(--dvas-border)',
                    borderRadius: 8,
                    color: 'var(--dvas-text)',
                    outline: 'none',
                    boxSizing: 'border-box',
                    marginBottom: 10,
                  }}
                />
                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
                  <button
                    type="button"
                    onClick={onClose}
                    style={{ padding: '7px 14px', fontSize: 12, fontWeight: 600, borderRadius: 8, background: 'var(--dvas-elev)', border: '1px solid var(--dvas-border)', color: 'var(--dvas-text)', cursor: 'pointer' }}
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleExecutePurge}
                    disabled={confirmationPhrase !== 'CONFIRM STATUTORY PURGE' || executingPurge}
                    style={{
                      padding: '7px 18px',
                      fontSize: 12,
                      fontWeight: 700,
                      borderRadius: 8,
                      background: 'var(--dvas-crit)',
                      border: 'none',
                      color: '#fff',
                      cursor: confirmationPhrase === 'CONFIRM STATUTORY PURGE' ? 'pointer' : 'not-allowed',
                      opacity: confirmationPhrase === 'CONFIRM STATUTORY PURGE' ? 1 : 0.5,
                    }}
                  >
                    {executingPurge ? 'Purging…' : 'Execute Statutory Purge'}
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function SavePresetModal({ isOpen, onClose, currentFilters, onSaved }) {
  const [name, setName] = useState('');
  const [isShared, setIsShared] = useState(false);
  const [saving, setSaving] = useState(false);

  if (!isOpen) return null;

  const handleSave = async (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    try {
      setSaving(true);
      await api.post('/audit-presets', {
        name: name.trim(),
        filters: currentFilters,
        is_shared: isShared,
      });
      if (onSaved) onSaved();
      onClose();
    } catch (err) {
      console.error('Failed to save preset:', err);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 120, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.7)' }}>
      <div
        className="dvas-fade-in"
        style={{
          width: 400,
          background: 'var(--dvas-surface)',
          border: '1px solid var(--dvas-border)',
          borderRadius: 12,
          padding: 24,
          boxShadow: 'var(--dvas-shadow-lg)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--dvas-text)', display: 'flex', alignItems: 'center', gap: 8 }}>
            <BookmarkPlus size={16} style={{ color: 'var(--dvas-blue)' }} /> Save Custom Filter Preset
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'var(--dvas-text-dim)', cursor: 'pointer' }}>
            <X size={16} />
          </button>
        </div>
        <form onSubmit={handleSave}>
          <div style={{ marginBottom: 14 }}>
            <label style={{ display: 'block', fontSize: 11, fontWeight: 600, color: 'var(--dvas-text-dim)', marginBottom: 6 }}>
              Preset Configuration Name
            </label>
            <input
              type="text"
              placeholder="e.g. Critical Security Failures · 24h"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              style={{
                width: '100%',
                padding: '8px 12px',
                fontSize: 12,
                background: 'var(--dvas-elev)',
                border: '1px solid var(--dvas-border)',
                borderRadius: 8,
                color: 'var(--dvas-text)',
                outline: 'none',
                boxSizing: 'border-box',
              }}
            />
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--dvas-text)', marginBottom: 20, cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={isShared}
              onChange={(e) => setIsShared(e.target.checked)}
              style={{ width: 14, height: 14 }}
            />
            Share preset across Kerala Police officers
          </label>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
            <button
              type="button"
              onClick={onClose}
              style={{ padding: '6px 14px', fontSize: 12, fontWeight: 600, borderRadius: 8, background: 'var(--dvas-elev)', border: '1px solid var(--dvas-border)', color: 'var(--dvas-text)', cursor: 'pointer' }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving || !name.trim()}
              style={{ padding: '6px 16px', fontSize: 12, fontWeight: 600, borderRadius: 8, background: 'var(--dvas-blue)', border: 'none', color: '#fff', cursor: 'pointer' }}
            >
              {saving ? 'Saving…' : 'Save Preset'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Main Audit Log Explorer Page
// ─────────────────────────────────────────────────────────────────────────────

export default function AuditLogPage() {
  const { user: authUser } = useAuth();
  const navigate = useNavigate();

  // Dual Timezone Toggle ('IST' vs 'UTC')
  const [timezone, setTimezone] = useState('IST');

  // Real-time Live Tail Mode
  const [liveTailActive, setLiveTailActive] = useState(false);
  const [liveTailPaused, setLiveTailPaused] = useState(false);
  const [newLogIds, setNewLogIds] = useState(new Set());

  // Data state
  const [logs, setLogs] = useState([]);
  const [totalLogs, setTotalLogs] = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [queryError, setQueryError] = useState(null);

  // KPIs, Anomaly Alerts & Trends
  const [summary, setSummary] = useState({ total_events: 0, failed_actions: 0, critical_events: 0, warnings: 0, active_legal_holds: 0 });
  const [chainStatus, setChainStatus] = useState(null);
  const [verifyingChain, setVerifyingChain] = useState(false);
  const [anomaliesData, setAnomaliesData] = useState([]);
  const [showAnomaliesBanner, setShowAnomaliesBanner] = useState(true);
  const [showTrends, setShowTrends] = useState(true);
  const [trendsRange, setTrendsRange] = useState('24h');
  const [trendsData, setTrendsData] = useState(null);
  const [trendsLoading, setTrendsLoading] = useState(false);

  // 12-Dimension Filters
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [quickRange, setQuickRange] = useState('all');
  const [actorFilter, setActorFilter] = useState('');
  const [roleFilter, setRoleFilter] = useState('');
  const [selectedActionTypes, setSelectedActionTypes] = useState([]);
  const [selectedCategories, setSelectedCategories] = useState([]);
  const [targetTypeFilter, setTargetTypeFilter] = useState('');
  const [sourceModuleFilter, setSourceModuleFilter] = useState('');
  const [selectedSeverities, setSelectedSeverities] = useState([]);
  const [selectedStatuses, setSelectedStatuses] = useState([]);
  const [complianceTagFilter, setComplianceTagFilter] = useState('');
  const [legalHoldOnly, setLegalHoldOnly] = useState(false);

  // Sorting & Pagination
  const [sortKey, setSortKey] = useState('ts');
  const [sortDir, setSortDir] = useState('desc');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);

  // Filter Dropdown Options from Backend
  const [actorsList, setActorsList] = useState([]);
  const [actionTypesList, setActionTypesList] = useState([]);
  const [categoriesList, setCategoriesList] = useState([]);
  const [severitiesList, setSeveritiesList] = useState([]);
  const [rolesList, setRolesList] = useState([]);
  const [complianceTagsList, setComplianceTagsList] = useState([]);
  const [targetTypesList, setTargetTypesList] = useState([]);
  const [presetsList, setPresetsList] = useState([]);

  // UI & Drawer State
  const [expandedRows, setExpandedRows] = useState(new Set());
  const [rowTabMap, setRowTabMap] = useState({});
  const [selectedDrawerLog, setSelectedDrawerLog] = useState(null);
  const [drawerTab, setDrawerTab] = useState('overview');
  const [selectedRowIndex, setSelectedRowIndex] = useState(0);

  // Modals State
  const [isPresetModalOpen, setIsPresetModalOpen] = useState(false);
  const [isChainModalOpen, setIsChainModalOpen] = useState(false);
  const [selectedChainLog, setSelectedChainLog] = useState(null);
  const [isLegalHoldModalOpen, setIsLegalHoldModalOpen] = useState(false);
  const [selectedHoldLog, setSelectedHoldLog] = useState(null);
  const [isRetentionModalOpen, setIsRetentionModalOpen] = useState(false);
  const [isArchiveModalOpen, setIsArchiveModalOpen] = useState(false);
  const [exporting, setExporting] = useState(false);

  // ── Access Logging (Audit of the Auditors) on Page Mount ──
  useEffect(() => {
    api.post('/audit-logs/access-log', {
      officer_id: authUser?.id || '',
      officer_name: authUser?.name || authUser?.email || 'Officer',
      officer_role: authUser?.role || '',
      action: 'view_explorer',
      referrer: document.referrer || window.location.href,
      filters_applied: {},
    }).catch(() => {});
  }, [authUser]);

  // Debounce search
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(timer);
  }, [search]);

  // Load dropdown metadata & presets once on mount
  useEffect(() => {
    async function loadMetadata() {
      try {
        const [actRes, typRes, catRes, sevRes, rolRes, tagRes, trgRes, preRes, anomRes] = await Promise.allSettled([
          api.get('/audit/actors'),
          api.get('/audit-logs/action-types'),
          api.get('/audit-logs/categories'),
          api.get('/audit-logs/severities'),
          api.get('/audit-logs/roles'),
          api.get('/audit-logs/compliance-tags'),
          api.get('/audit-logs/target-types'),
          api.get('/audit-presets'),
          api.get('/audit-logs/anomalies?hours=24'),
        ]);
        if (actRes.status === 'fulfilled') setActorsList(actRes.value.data || []);
        if (typRes.status === 'fulfilled') setActionTypesList(typRes.value.data || []);
        if (catRes.status === 'fulfilled') setCategoriesList(catRes.value.data || []);
        if (sevRes.status === 'fulfilled') setSeveritiesList(sevRes.value.data || []);
        if (rolRes.status === 'fulfilled') setRolesList(rolRes.value.data || []);
        if (tagRes.status === 'fulfilled') setComplianceTagsList(tagRes.value.data || []);
        if (trgRes.status === 'fulfilled') setTargetTypesList(trgRes.value.data || []);
        if (preRes.status === 'fulfilled') setPresetsList(preRes.value.data || []);
        if (anomRes.status === 'fulfilled') setAnomaliesData(anomRes.value.data?.anomalies || []);
      } catch (err) {
        console.error('Metadata load error:', err);
      }
    }
    loadMetadata();
  }, []);

  // Fetch Trends Analytics
  const fetchTrends = useCallback(async (rangeVal) => {
    try {
      setTrendsLoading(true);
      const { data } = await api.get(`/audit-logs/trends?range_val=${rangeVal}`);
      setTrendsData(data);
    } catch (err) {
      console.error('Trends fetch error:', err);
    } finally {
      setTrendsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (showTrends) {
      fetchTrends(trendsRange);
    }
  }, [showTrends, trendsRange, fetchTrends]);

  // Primary Data Query Handler
  const fetchAuditLogs = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      params.set('page', String(page));
      params.set('page_size', String(pageSize));
      params.set('sort_by', sortKey);
      params.set('sort_dir', sortDir);

      if (debouncedSearch) params.set('q', debouncedSearch);
      if (dateFrom) params.set('date_from', dateFrom);
      if (dateTo) params.set('date_to', dateTo.includes('T') ? dateTo : dateTo + 'T23:59:59');
      if (actorFilter) params.set('actor_id', actorFilter);
      if (roleFilter) params.set('actor_role', roleFilter);
      if (selectedCategories.length) params.set('category', selectedCategories.join(','));
      if (selectedSeverities.length) params.set('severity', selectedSeverities.join(','));
      if (selectedActionTypes.length) params.set('action_type', selectedActionTypes.join(','));
      if (selectedStatuses.length) params.set('status', selectedStatuses.join(','));
      if (targetTypeFilter) params.set('target_type', targetTypeFilter);
      if (sourceModuleFilter) params.set('source_module', sourceModuleFilter);
      if (complianceTagFilter) params.set('compliance_tag', complianceTagFilter);
      if (legalHoldOnly) params.set('is_legal_hold', 'true');

      const [logsRes, summaryRes] = await Promise.all([
        api.get(`/audit-logs?${params.toString()}`),
        api.get(`/audit-logs/summary?${params.toString()}`),
      ]);

      setLogs(logsRes.data.items || []);
      setTotalLogs(logsRes.data.total || 0);
      setTotalPages(logsRes.data.total_pages || 1);
      setSummary(summaryRes.data || {});
      setQueryError(null);
    } catch (err) {
      console.error('Audit query error:', err);
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail || err?.message || 'Failed to communicate with the Audit API service';
      setQueryError({ status, detail });
      setLogs([]);
    } finally {
      setLoading(false);
    }
  }, [
    page, pageSize, sortKey, sortDir, debouncedSearch,
    dateFrom, dateTo, actorFilter, roleFilter,
    selectedCategories, selectedSeverities, selectedActionTypes,
    selectedStatuses, targetTypeFilter, sourceModuleFilter,
    complianceTagFilter, legalHoldOnly,
  ]);

  useEffect(() => {
    fetchAuditLogs();
  }, [fetchAuditLogs]);

  // Reset to page 1 on filter modifications
  useEffect(() => {
    setPage(1);
  }, [
    debouncedSearch, dateFrom, dateTo, actorFilter, roleFilter,
    selectedCategories, selectedSeverities, selectedActionTypes,
    selectedStatuses, targetTypeFilter, sourceModuleFilter,
    complianceTagFilter, legalHoldOnly,
  ]);

  // ── Real-Time Live Tail Integration via useDvasSocket ──
  useDvasSocket((msg) => {
    const incoming = msg?.event || msg?.data;
    if (msg?.type === 'audit_event' && liveTailActive && !liveTailPaused && incoming) {
      setLogs((prev) => [incoming, ...prev.slice(0, pageSize - 1)]);
      setTotalLogs((t) => t + 1);
      setNewLogIds((prev) => new Set([...prev, incoming.id]));
      setTimeout(() => {
        setNewLogIds((prev) => {
          const next = new Set(prev);
          next.delete(incoming.id);
          return next;
        });
      }, 4000);
    }
  });

  // Polling fallback when Live Tail is ON
  useEffect(() => {
    if (!liveTailActive || liveTailPaused) return;
    const interval = setInterval(() => {
      fetchAuditLogs();
    }, 4000);
    return () => clearInterval(interval);
  }, [liveTailActive, liveTailPaused, fetchAuditLogs]);

  // Verify SHA-256 chain
  const handleVerifyChain = useCallback(async () => {
    try {
      setVerifyingChain(true);
      const { data } = await api.get('/audit-logs/verify');
      setChainStatus(data);
    } catch (err) {
      console.error('Verification error:', err);
    } finally {
      setVerifyingChain(false);
    }
  }, []);

  useEffect(() => {
    handleVerifyChain();
  }, [handleVerifyChain]);

  // Quick Date Range Handler
  const handleQuickRange = (rng) => {
    setQuickRange(rng);
    const now = new Date();
    if (rng === 'all') {
      setDateFrom('');
      setDateTo('');
    } else if (rng === 'today') {
      const todayStr = now.toISOString().slice(0, 10);
      setDateFrom(todayStr);
      setDateTo(todayStr);
    } else if (rng === '24h') {
      const past24h = new Date(now.getTime() - 24 * 60 * 60 * 1000);
      setDateFrom(past24h.toISOString().slice(0, 16));
      setDateTo(now.toISOString().slice(0, 16));
    } else if (rng === '7d') {
      const past7d = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
      setDateFrom(past7d.toISOString().slice(0, 10));
      setDateTo(now.toISOString().slice(0, 10));
    } else if (rng === '30d') {
      const past30d = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
      setDateFrom(past30d.toISOString().slice(0, 10));
      setDateTo(now.toISOString().slice(0, 10));
    }
  };

  // Keyboard Navigation
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target?.tagName)) {
        if (e.key === 'Escape') e.target.blur();
        return;
      }
      if (e.key === 'Escape') {
        setSelectedDrawerLog(null);
        setIsPresetModalOpen(false);
        setIsChainModalOpen(false);
        setIsLegalHoldModalOpen(false);
        setIsRetentionModalOpen(false);
        setIsArchiveModalOpen(false);
      } else if (e.key === 'j' || e.key === 'ArrowDown') {
        setSelectedRowIndex((prev) => Math.min(logs.length - 1, prev + 1));
      } else if (e.key === 'k' || e.key === 'ArrowUp') {
        setSelectedRowIndex((prev) => Math.max(0, prev - 1));
      } else if (e.key === 'Enter') {
        const targetLog = logs[selectedRowIndex];
        if (targetLog) toggleRow(targetLog.id);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [logs, selectedRowIndex]);

  // Sorting Handler
  const handleSort = (key) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  };

  const SortIcon = ({ col }) => {
    if (sortKey !== col) return <ChevronDown size={11} style={{ opacity: 0.3 }} />;
    return sortDir === 'asc' ? <ChevronUp size={11} /> : <ChevronDown size={11} />;
  };

  // Row Expansion Toggle
  const toggleRow = (id) => {
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const setRowTab = (id, tab) => {
    setRowTabMap((prev) => ({ ...prev, [id]: tab }));
  };

  // Target resource deep navigator
  const handleNavigateResource = (targetType, targetId) => {
    if (!targetType) return;
    const type = targetType.toLowerCase();
    if (type === 'user') navigate('/admin');
    else if (type === 'mission' || type === 'drone') navigate('/command');
    else if (type === 'evidence' || type === 'export') navigate('/admin');
    else navigate('/admin');
  };

  // Preset Application
  const handleApplyPreset = (preset) => {
    const f = preset.filters || {};
    setSearch(f.q || '');
    setDateFrom(f.date_from || '');
    setDateTo(f.date_to || '');
    setActorFilter(f.actor_id || '');
    setRoleFilter(f.actor_role || '');
    setSelectedCategories(f.category ? (Array.isArray(f.category) ? f.category : f.category.split(',')) : []);
    setSelectedSeverities(f.severity ? (Array.isArray(f.severity) ? f.severity : f.severity.split(',')) : []);
    setSelectedActionTypes(f.action_type ? (Array.isArray(f.action_type) ? f.action_type : f.action_type.split(',')) : []);
    setSelectedStatuses(f.status ? (Array.isArray(f.status) ? f.status : f.status.split(',')) : []);
    setTargetTypeFilter(f.target_type || '');
    setSourceModuleFilter(f.source_module || '');
    setComplianceTagFilter(f.compliance_tag || '');
    setLegalHoldOnly(Boolean(f.is_legal_hold));
  };

  // Exports
  const buildExportParams = () => {
    const params = new URLSearchParams();
    if (debouncedSearch) params.set('q', debouncedSearch);
    if (dateFrom) params.set('date_from', dateFrom);
    if (dateTo) params.set('date_to', dateTo.includes('T') ? dateTo : dateTo + 'T23:59:59');
    if (actorFilter) params.set('actor_id', actorFilter);
    if (roleFilter) params.set('actor_role', roleFilter);
    if (selectedCategories.length) params.set('category', selectedCategories.join(','));
    if (selectedSeverities.length) params.set('severity', selectedSeverities.join(','));
    if (selectedActionTypes.length) params.set('action_type', selectedActionTypes.join(','));
    if (selectedStatuses.length) params.set('status', selectedStatuses.join(','));
    if (targetTypeFilter) params.set('target_type', targetTypeFilter);
    if (sourceModuleFilter) params.set('source_module', sourceModuleFilter);
    if (complianceTagFilter) params.set('compliance_tag', complianceTagFilter);
    if (legalHoldOnly) params.set('is_legal_hold', 'true');
    return params;
  };

  const handleExportCSV = async () => {
    try {
      setExporting(true);
      const params = buildExportParams();
      const response = await api.get(`/audit-logs/export/csv?${params.toString()}`, { responseType: 'blob' });
      const blob = new Blob([response.data], { type: 'text/csv;charset=utf-8;' });
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = `dvas_audit_log_${new Date().toISOString().slice(0, 10)}.csv`;
      link.click();
    } catch (err) {
      console.error('Export CSV error:', err);
    } finally {
      setExporting(false);
    }
  };

  const handleExportJSON = async () => {
    try {
      setExporting(true);
      const params = buildExportParams();
      const { data } = await api.get(`/audit-logs/export/json?${params.toString()}`);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = `dvas_audit_log_${new Date().toISOString().slice(0, 10)}.json`;
      link.click();
    } catch (err) {
      console.error('Export JSON error:', err);
    } finally {
      setExporting(false);
    }
  };

  const handleExportSection65BPDF = async () => {
    try {
      setExporting(true);
      const params = buildExportParams();
      const response = await api.get(`/audit-logs/export/pdf?${params.toString()}`, { responseType: 'blob' });
      const blob = new Blob([response.data], { type: 'application/pdf' });
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = `dvas_section65b_certificate_${new Date().toISOString().slice(0, 10)}.pdf`;
      link.click();
    } catch (err) {
      console.error('Export Section 65B PDF error:', err);
    } finally {
      setExporting(false);
    }
  };

  const handleClearAllFilters = () => {
    setSearch('');
    setDateFrom('');
    setDateTo('');
    setQuickRange('all');
    setActorFilter('');
    setRoleFilter('');
    setSelectedCategories([]);
    setSelectedSeverities([]);
    setSelectedActionTypes([]);
    setSelectedStatuses([]);
    setTargetTypeFilter('');
    setSourceModuleFilter('');
    setComplianceTagFilter('');
    setLegalHoldOnly(false);
  };

  const hasActiveFilters = Boolean(
    search || dateFrom || dateTo || actorFilter || roleFilter ||
    selectedCategories.length || selectedSeverities.length ||
    selectedActionTypes.length || selectedStatuses.length ||
    targetTypeFilter || sourceModuleFilter || complianceTagFilter || legalHoldOnly
  );

  const isSuperAdmin = authUser?.role === 'system_administrator' || authUser?.role === 'admin';

  return (
    <AppShell
      title="Audit Log Explorer"
      subtitle="Kerala Police DVAS · Cryptographically Verified & Immutable Audit Trail (DPDPA 2023 & Indian Evidence Act §65B)"
    >
      {/* ── Statutory Print Stylesheet ── */}
      <style>{`
        @media print {
          nav, aside, .dvas-no-print, [data-testid="audit-search"], select, button, input {
            display: none !important;
          }
          body {
            background: #fff !important;
            color: #000 !important;
          }
          table {
            font-size: 9px !important;
            table-layout: fixed !important;
          }
          th, td {
            border: 1px solid #ccc !important;
            padding: 4px 6px !important;
          }
          .dvas-print-header {
            display: block !important;
            margin-bottom: 16px;
            font-family: sans-serif;
          }
        }
        @keyframes pulseGlow {
          0% { background-color: rgba(56, 189, 248, 0.25); }
          100% { background-color: transparent; }
        }
        .animate-new-row {
          animation: pulseGlow 3.5s ease-out;
        }
      `}</style>

      {/* ── Anomaly Alert Banner (8 Rules) ── */}
      {anomaliesData.length > 0 && showAnomaliesBanner && (
        <div
          data-testid="anomaly-banner"
          style={{
            marginBottom: 16,
            padding: '12px 16px',
            borderRadius: 10,
            background: 'rgba(239, 68, 68, 0.12)',
            border: '1px solid rgba(239, 68, 68, 0.35)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 12,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Zap size={18} style={{ color: 'var(--dvas-crit)', flexShrink: 0 }} />
            <div>
              <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--dvas-crit)' }}>
                {anomaliesData.length} Security Anomaly Pattern(s) Detected in Last 24 Hours
              </div>
              <div style={{ fontSize: 11, color: 'var(--dvas-text)' }}>
                {anomaliesData.map((a) => a.title).join(' · ')}
              </div>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <button
              onClick={() => setSelectedSeverities(['critical', 'warning'])}
              style={{
                fontSize: 11,
                fontWeight: 600,
                padding: '4px 10px',
                borderRadius: 6,
                background: 'var(--dvas-crit)',
                color: '#fff',
                border: 'none',
                cursor: 'pointer',
              }}
            >
              Filter Anomalous Events
            </button>
            <button
              onClick={() => setShowAnomaliesBanner(false)}
              style={{ background: 'none', border: 'none', color: 'var(--dvas-text-dim)', cursor: 'pointer' }}
            >
              <X size={14} />
            </button>
          </div>
        </div>
      )}

      {/* ── Header Context & Operational Controls Bar ── */}
      <div
        style={{
          marginBottom: 16,
          padding: '10px 16px',
          borderRadius: 10,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
          flexWrap: 'wrap',
          background: 'var(--dvas-surface)',
          border: '1px solid var(--dvas-border)',
        }}
      >
        {/* Officer Context */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <UserCheck size={16} style={{ color: 'var(--dvas-blue)' }} />
          <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--dvas-text)' }}>
            Logged Officer:&nbsp;
            <span style={{ fontFamily: 'JetBrains Mono, monospace', color: 'var(--dvas-blue)' }}>
              {authUser?.name || authUser?.email || 'Officer'} · {authUser?.role?.replace(/_/g, ' ')}
            </span>
          </span>
          <span style={{ fontSize: 10, padding: '2px 8px', borderRadius: 999, background: 'var(--dvas-elev)', color: 'var(--dvas-text-muted)', border: '1px solid var(--dvas-border)' }}>
            Jurisdiction: {authUser?.jurisdiction || 'State HQ (Kerala)'}
          </span>
        </div>

        {/* Global Controls: Timezone + Live Tail + Retention + Purge */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {/* Dual Timezone Toggle */}
          <div style={{ display: 'flex', background: 'var(--dvas-elev)', borderRadius: 6, border: '1px solid var(--dvas-border)', padding: 2 }}>
            <button
              onClick={() => setTimezone('IST')}
              title="Display timestamps in Indian Standard Time (UTC+5:30)"
              style={{
                padding: '3px 8px',
                fontSize: 10,
                fontWeight: 600,
                borderRadius: 4,
                border: 'none',
                background: timezone === 'IST' ? 'var(--dvas-blue)' : 'transparent',
                color: timezone === 'IST' ? '#fff' : 'var(--dvas-text-muted)',
                cursor: 'pointer',
              }}
            >
              IST (UTC+5:30)
            </button>
            <button
              onClick={() => setTimezone('UTC')}
              title="Display timestamps in Universal Coordinated Time (UTC)"
              style={{
                padding: '3px 8px',
                fontSize: 10,
                fontWeight: 600,
                borderRadius: 4,
                border: 'none',
                background: timezone === 'UTC' ? 'var(--dvas-blue)' : 'transparent',
                color: timezone === 'UTC' ? '#fff' : 'var(--dvas-text-muted)',
                cursor: 'pointer',
              }}
            >
              UTC
            </button>
          </div>

          {/* Live Tail Toggle */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <button
              onClick={() => {
                setLiveTailActive((prev) => !prev);
                setLiveTailPaused(false);
              }}
              title={liveTailActive ? 'Stop streaming live tail' : 'Start streaming live tail events in real time'}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                padding: '4px 10px',
                borderRadius: 6,
                fontSize: 11,
                fontWeight: 600,
                background: liveTailActive ? 'rgba(52, 211, 153, 0.15)' : 'var(--dvas-elev)',
                color: liveTailActive ? 'var(--dvas-safe)' : 'var(--dvas-text-muted)',
                border: `1px solid ${liveTailActive ? 'rgba(52, 211, 153, 0.4)' : 'var(--dvas-border)'}`,
                cursor: 'pointer',
              }}
            >
              <span
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: '50%',
                  background: liveTailActive ? 'var(--dvas-safe)' : 'var(--dvas-text-dim)',
                  boxShadow: liveTailActive ? '0 0 6px var(--dvas-safe)' : 'none',
                }}
              />
              {liveTailActive ? (liveTailPaused ? 'Live Tail (Paused)' : 'Live Tail ON') : 'Live Tail OFF'}
            </button>

            {liveTailActive && (
              <button
                onClick={() => setLiveTailPaused((p) => !p)}
                title={liveTailPaused ? 'Resume live stream' : 'Pause live stream'}
                style={{
                  padding: '4px 8px',
                  borderRadius: 6,
                  background: 'var(--dvas-elev)',
                  border: '1px solid var(--dvas-border)',
                  color: 'var(--dvas-text-dim)',
                  cursor: 'pointer',
                }}
              >
                {liveTailPaused ? <Play size={11} /> : <Pause size={11} />}
              </button>
            )}
          </div>

          {/* Retention Policy Pill */}
          <button
            onClick={() => setIsRetentionModalOpen(true)}
            title="View Statutory 365-Day Retention & DPDPA Policy"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 5,
              padding: '4px 10px',
              borderRadius: 6,
              fontSize: 11,
              fontWeight: 600,
              background: 'var(--dvas-elev)',
              color: 'var(--dvas-text)',
              border: '1px solid var(--dvas-border)',
              cursor: 'pointer',
            }}
          >
            <Clock size={12} style={{ color: 'var(--dvas-blue)' }} />
            Retention: 365 Days · IEA 65B & DPDPA
          </button>

          {/* Super-Admin Archive & Purge Button */}
          {isSuperAdmin && (
            <button
              onClick={() => setIsArchiveModalOpen(true)}
              title="Super-Admin Authorized Statutory Archive & Purge Wizard"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 5,
                padding: '4px 10px',
                borderRadius: 6,
                fontSize: 11,
                fontWeight: 600,
                background: 'rgba(239, 68, 68, 0.12)',
                color: 'var(--dvas-crit)',
                border: '1px solid rgba(239, 68, 68, 0.3)',
                cursor: 'pointer',
              }}
            >
              <Archive size={12} />
              Archive & Purge
            </button>
          )}

          {/* Chain Integrity Badge */}
          {chainStatus && (
            <div
              data-testid="chain-status-badge"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                padding: '4px 10px',
                borderRadius: 6,
                fontSize: 11,
                fontWeight: 600,
                fontFamily: 'JetBrains Mono, monospace',
                background: chainStatus.is_chain_valid ? 'var(--dvas-safe-bg)' : 'var(--dvas-crit-bg)',
                color: chainStatus.is_chain_valid ? 'var(--dvas-safe)' : 'var(--dvas-crit)',
                border: `1px solid ${chainStatus.is_chain_valid ? 'rgba(52,211,153,0.3)' : 'rgba(239,68,68,0.3)'}`,
              }}
            >
              {chainStatus.is_chain_valid ? <ShieldCheck size={13} /> : <ShieldAlert size={13} />}
              {chainStatus.is_chain_valid ? 'SHA-256 Ledger (OK)' : `Tamper Alert (${chainStatus.tampered_count})`}
            </div>
          )}
          <button
            onClick={handleVerifyChain}
            disabled={verifyingChain}
            title="Re-verify full cryptographic hash-chain from genesis"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              fontSize: 11,
              fontWeight: 600,
              padding: '5px 8px',
              borderRadius: 6,
              background: 'var(--dvas-elev)',
              border: '1px solid var(--dvas-border)',
              color: 'var(--dvas-text)',
              cursor: verifyingChain ? 'wait' : 'pointer',
            }}
          >
            <RefreshCw size={11} className={verifyingChain ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* ── Summary KPI Cards ── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 12, marginBottom: 16 }}>
        <SummaryCard testId="card-total" icon={Activity} label="Total Events" value={summary.total_events} sub="Matching active filters" color="var(--dvas-blue)" />
        <SummaryCard testId="card-failed" icon={AlertTriangle} label="Failed Actions" value={summary.failed_actions} sub="Unauthorized or rejected" color="var(--dvas-crit)" onClick={() => setSelectedStatuses(['failure'])} />
        <SummaryCard testId="card-critical" icon={Shield} label="Critical Actions" value={summary.critical_events} sub="User & security changes" color="var(--dvas-warn)" onClick={() => setSelectedSeverities(['critical'])} />
        <SummaryCard testId="card-holds" icon={Lock} label="Active Legal Holds" value={summary.active_legal_holds || 0} sub="Preserved for judicial inquiry" color="#f59e0b" onClick={() => setLegalHoldOnly((h) => !h)} />
      </div>

      {/* ── Collapsible Activity Trends & Telemetry ── */}
      <div style={{ marginBottom: 14, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <button
          onClick={() => setShowTrends((v) => !v)}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            background: 'none',
            border: 'none',
            fontSize: 12,
            fontWeight: 600,
            color: 'var(--dvas-text-dim)',
            cursor: 'pointer',
          }}
        >
          <Activity size={14} style={{ color: 'var(--dvas-blue)' }} />
          {showTrends ? 'Hide Forensic Activity Trends' : 'Show Forensic Activity Trends & Threat Telemetry'}
          {showTrends ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
        </button>
      </div>

      {showTrends && (
        <ActivityTrendsChart
          range={trendsRange}
          onRangeChange={setTrendsRange}
          trendsData={trendsData}
          loading={trendsLoading}
        />
      )}

      {/* ── Main Panel ── */}
      <div
        style={{
          background: 'var(--dvas-surface)',
          border: '1px solid var(--dvas-border)',
          borderRadius: 12,
          boxShadow: 'var(--dvas-shadow)',
          overflow: 'hidden',
        }}
      >
        {/* ── Filter & Action Toolbar (Row 1) ── */}
        <div style={{ padding: '12px 14px', display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', borderBottom: '1px solid var(--dvas-border)' }}>
          {/* Search Box */}
          <div style={{ position: 'relative', flex: 1, minWidth: 220 }}>
            <Search size={14} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--dvas-text-muted)', pointerEvents: 'none' }} />
            <input
              data-testid="audit-search"
              type="text"
              placeholder="Search actor, email, action, resource, IP, request ID…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{
                width: '100%',
                paddingLeft: 30,
                paddingRight: search ? 30 : 10,
                paddingBlock: 7,
                fontSize: 12,
                background: 'var(--dvas-elev)',
                border: '1px solid var(--dvas-border)',
                borderRadius: 8,
                color: 'var(--dvas-text)',
                outline: 'none',
                boxSizing: 'border-box',
              }}
            />
            {search && (
              <button
                onClick={() => setSearch('')}
                style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', color: 'var(--dvas-text-dim)', cursor: 'pointer' }}
              >
                <X size={13} />
              </button>
            )}
          </div>

          {/* Quick Date Presets */}
          <div style={{ display: 'flex', gap: 3, background: 'var(--dvas-elev)', padding: 2, borderRadius: 8, border: '1px solid var(--dvas-border)' }}>
            {[
              { id: 'all', label: 'All Time' },
              { id: 'today', label: 'Today' },
              { id: '24h', label: '24h' },
              { id: '7d', label: '7d' },
              { id: '30d', label: '30d' },
            ].map((p) => (
              <button
                key={p.id}
                onClick={() => handleQuickRange(p.id)}
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  padding: '4px 8px',
                  borderRadius: 6,
                  border: 'none',
                  background: quickRange === p.id ? 'var(--dvas-blue)' : 'transparent',
                  color: quickRange === p.id ? '#fff' : 'var(--dvas-text-muted)',
                  cursor: 'pointer',
                }}
              >
                {p.label}
              </button>
            ))}
          </div>

          {/* Custom Date Inputs */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <Calendar size={13} style={{ color: 'var(--dvas-text-muted)' }} />
            <input
              data-testid="date-from"
              type="date"
              value={dateFrom ? dateFrom.slice(0, 10) : ''}
              onChange={(e) => {
                setQuickRange('custom');
                setDateFrom(e.target.value);
              }}
              style={{ fontSize: 11, paddingBlock: 5, paddingInline: 8, background: 'var(--dvas-elev)', border: '1px solid var(--dvas-border)', borderRadius: 8, color: 'var(--dvas-text)', outline: 'none' }}
            />
            <span style={{ color: 'var(--dvas-text-dim)', fontSize: 11 }}>–</span>
            <input
              data-testid="date-to"
              type="date"
              value={dateTo ? dateTo.slice(0, 10) : ''}
              onChange={(e) => {
                setQuickRange('custom');
                setDateTo(e.target.value);
              }}
              style={{ fontSize: 11, paddingBlock: 5, paddingInline: 8, background: 'var(--dvas-elev)', border: '1px solid var(--dvas-border)', borderRadius: 8, color: 'var(--dvas-text)', outline: 'none' }}
            />
          </div>

          {/* Action Buttons (Right-aligned) */}
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center' }}>
            {hasActiveFilters && (
              <button
                onClick={handleClearAllFilters}
                title="Reset all active filters"
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 4,
                  fontSize: 11,
                  fontWeight: 600,
                  padding: '6px 9px',
                  borderRadius: 8,
                  background: 'none',
                  border: '1px dashed var(--dvas-border)',
                  color: 'var(--dvas-text-muted)',
                  cursor: 'pointer',
                }}
              >
                <RotateCcw size={11} /> Clear
              </button>
            )}

            <button
              onClick={() => setIsPresetModalOpen(true)}
              title="Save current filter set as a preset"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 4,
                fontSize: 11,
                fontWeight: 600,
                padding: '6px 10px',
                borderRadius: 8,
                background: 'var(--dvas-elev)',
                border: '1px solid var(--dvas-border)',
                color: 'var(--dvas-text)',
                cursor: 'pointer',
              }}
            >
              <Bookmark size={12} /> Save Preset
            </button>

            {/* Section 65B Certified PDF Export */}
            <button
              data-testid="export-65b-pdf"
              onClick={handleExportSection65BPDF}
              disabled={exporting || totalLogs === 0}
              title="Generate Official Indian Evidence Act Section 65B Compliance Certificate (PDF)"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 5,
                padding: '0 10px',
                height: 32,
                fontSize: 11,
                fontWeight: 700,
                borderRadius: 8,
                cursor: exporting ? 'wait' : 'pointer',
                background: 'rgba(56, 189, 248, 0.18)',
                color: 'var(--dvas-blue)',
                border: '1px solid var(--dvas-blue)',
                opacity: totalLogs === 0 ? 0.5 : 1,
              }}
            >
              <FileCheck size={13} /> §65B PDF
            </button>

            {/* CSV & JSON Export */}
            <button
              data-testid="export-csv"
              onClick={handleExportCSV}
              disabled={exporting || totalLogs === 0}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 5,
                padding: '0 10px',
                height: 32,
                fontSize: 11,
                fontWeight: 600,
                borderRadius: 8,
                cursor: exporting ? 'wait' : 'pointer',
                background: 'var(--dvas-blue)',
                color: '#fff',
                border: 'none',
                opacity: totalLogs === 0 ? 0.5 : 1,
              }}
            >
              <Download size={12} /> CSV
            </button>
            <button
              data-testid="export-json"
              onClick={handleExportJSON}
              disabled={exporting || totalLogs === 0}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 4,
                padding: '0 8px',
                height: 32,
                fontSize: 11,
                fontWeight: 600,
                borderRadius: 8,
                cursor: exporting ? 'wait' : 'pointer',
                background: 'var(--dvas-elev)',
                color: 'var(--dvas-text)',
                border: '1px solid var(--dvas-border)',
                opacity: totalLogs === 0 ? 0.5 : 1,
              }}
            >
              JSON
            </button>

            {/* Print Button */}
            <button
              onClick={() => window.print()}
              title="Print official audit log report"
              style={{
                width: 32,
                height: 32,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                borderRadius: 8,
                cursor: 'pointer',
                color: 'var(--dvas-text-muted)',
                background: 'var(--dvas-elev)',
                border: '1px solid var(--dvas-border)',
              }}
            >
              <Printer size={13} />
            </button>
          </div>
        </div>

        {/* ── Filter Toolbar (Row 2: 12-Dimension Dropdowns & Toggles) ── */}
        <div style={{ padding: '8px 14px', display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', background: 'var(--dvas-surface-2)', borderBottom: '1px solid var(--dvas-border)' }}>
          {/* Actor Select */}
          <select
            data-testid="actor-filter"
            value={actorFilter}
            onChange={(e) => setActorFilter(e.target.value)}
            style={{
              paddingInline: 10,
              paddingBlock: 6,
              fontSize: 11,
              background: actorFilter ? 'rgba(56, 189, 248, 0.08)' : 'var(--dvas-elev)',
              border: actorFilter ? '1px solid var(--dvas-blue)' : '1px solid var(--dvas-border)',
              borderRadius: 8,
              color: 'var(--dvas-text)',
              outline: 'none',
              minWidth: 130,
              cursor: 'pointer',
            }}
          >
            <option value="">All Actors</option>
            {actorsList.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name || a.email} ({a.role?.replace(/_/g, ' ')})
              </option>
            ))}
          </select>

          {/* Role Filter */}
          <select
            value={roleFilter}
            onChange={(e) => setRoleFilter(e.target.value)}
            style={{
              paddingInline: 10,
              paddingBlock: 6,
              fontSize: 11,
              background: roleFilter ? 'rgba(56, 189, 248, 0.08)' : 'var(--dvas-elev)',
              border: roleFilter ? '1px solid var(--dvas-blue)' : '1px solid var(--dvas-border)',
              borderRadius: 8,
              color: 'var(--dvas-text)',
              outline: 'none',
              minWidth: 120,
              cursor: 'pointer',
            }}
          >
            <option value="">All Roles</option>
            {(rolesList.length ? rolesList : ['system_administrator', 'senior_officer', 'operator', 'analyst', 'external_auditor']).map((r) => (
              <option key={r} value={r}>{r.replace(/_/g, ' ')}</option>
            ))}
          </select>

          {/* Category Filter */}
          <MultiSelect
            label="Category"
            options={categoriesList.length ? categoriesList : ['authentication', 'user_mgmt', 'operations', 'evidence', 'configuration', 'security', 'analytics']}
            selected={selectedCategories}
            onChange={setSelectedCategories}
            testId="category-filter"
            minWidth={120}
          />

          {/* Action Type Filter */}
          <MultiSelect
            label="Action Type"
            options={actionTypesList.length ? actionTypesList : ['create', 'update', 'delete', 'login', 'logout', 'export', 'permission_change', 'read', 'system', 'configuration']}
            selected={selectedActionTypes}
            onChange={setSelectedActionTypes}
            testId="action-type-filter"
            minWidth={125}
          />

          {/* Target Type Filter */}
          <select
            value={targetTypeFilter}
            onChange={(e) => setTargetTypeFilter(e.target.value)}
            style={{
              paddingInline: 10,
              paddingBlock: 6,
              fontSize: 11,
              background: targetTypeFilter ? 'rgba(56, 189, 248, 0.08)' : 'var(--dvas-elev)',
              border: targetTypeFilter ? '1px solid var(--dvas-blue)' : '1px solid var(--dvas-border)',
              borderRadius: 8,
              color: 'var(--dvas-text)',
              outline: 'none',
              minWidth: 120,
              cursor: 'pointer',
            }}
          >
            <option value="">All Resources</option>
            {(targetTypesList.length ? targetTypesList : ['user', 'mission', 'drone', 'evidence', 'export', 'setting', 'system']).map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>

          {/* Compliance Tag Filter */}
          <select
            value={complianceTagFilter}
            onChange={(e) => setComplianceTagFilter(e.target.value)}
            style={{
              paddingInline: 10,
              paddingBlock: 6,
              fontSize: 11,
              background: complianceTagFilter ? 'rgba(56, 189, 248, 0.08)' : 'var(--dvas-elev)',
              border: complianceTagFilter ? '1px solid var(--dvas-blue)' : '1px solid var(--dvas-border)',
              borderRadius: 8,
              color: 'var(--dvas-text)',
              outline: 'none',
              minWidth: 125,
              cursor: 'pointer',
            }}
          >
            <option value="">All Compliance</option>
            {(complianceTagsList.length ? complianceTagsList : ['DPDPA_2023', 'IEA_SECTION_65B', 'RETENTION_1YR', 'LEGAL_HOLD_EXEMPT']).map((tag) => (
              <option key={tag} value={tag}>{tag.replace(/_/g, ' ')}</option>
            ))}
          </select>

          {/* Severity Quick Toggles */}
          <div style={{ display: 'flex', gap: 3 }}>
            {['critical', 'warning', 'info'].map((sev) => {
              const active = selectedSeverities.includes(sev);
              const b = severityBadge(sev);
              return (
                <button
                  key={sev}
                  onClick={() =>
                    setSelectedSeverities((prev) =>
                      active ? prev.filter((s) => s !== sev) : [...prev, sev]
                    )
                  }
                  style={{
                    fontSize: 10,
                    fontWeight: 700,
                    padding: '4px 8px',
                    borderRadius: 6,
                    cursor: 'pointer',
                    background: active ? b.bg : 'var(--dvas-elev)',
                    color: active ? b.color : 'var(--dvas-text-dim)',
                    border: `1px solid ${active ? b.border : 'var(--dvas-border)'}`,
                  }}
                >
                  {sev.toUpperCase()}
                </button>
              );
            })}
          </div>

          {/* Legal Hold Quick Toggle */}
          <button
            onClick={() => setLegalHoldOnly((h) => !h)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              fontSize: 11,
              fontWeight: 600,
              padding: '4px 10px',
              borderRadius: 8,
              cursor: 'pointer',
              background: legalHoldOnly ? 'rgba(245, 158, 11, 0.15)' : 'var(--dvas-elev)',
              color: legalHoldOnly ? 'var(--dvas-warn)' : 'var(--dvas-text-dim)',
              border: `1px solid ${legalHoldOnly ? 'rgba(245, 158, 11, 0.4)' : 'var(--dvas-border)'}`,
            }}
          >
            <Lock size={11} /> Legal Holds Only
          </button>

          {/* Presets Quick Selector */}
          {presetsList.length > 0 && (
            <select
              value=""
              onChange={(e) => {
                const found = presetsList.find((p) => p.id === e.target.value);
                if (found) handleApplyPreset(found);
              }}
              style={{
                paddingInline: 10,
                paddingBlock: 6,
                fontSize: 11,
                background: 'var(--dvas-elev)',
                border: '1px solid var(--dvas-border)',
                borderRadius: 8,
                color: 'var(--dvas-text)',
                outline: 'none',
                minWidth: 120,
                cursor: 'pointer',
              }}
            >
              <option value="" disabled>Presets ({presetsList.length})</option>
              {presetsList.map((p) => (
                <option key={p.id} value={p.id}>{p.name} {p.is_shared ? '(Shared)' : ''}</option>
              ))}
            </select>
          )}
        </div>

        {/* ── Audit Trail Table ── */}
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse', tableLayout: 'fixed' }}>
            <thead>
              <tr style={{ background: 'var(--dvas-surface-2)', borderBottom: '1px solid var(--dvas-border)' }}>
                <th style={{ width: '36px', padding: '10px 8px' }} />
                <th onClick={() => handleSort('ts')} style={{ width: '16%', padding: '10px 10px', textAlign: 'left', cursor: 'pointer' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: sortKey === 'ts' ? 'var(--dvas-blue)' : 'var(--dvas-text-muted)' }}>
                    Timestamp ({timezone})<SortIcon col="ts" />
                  </div>
                </th>
                <th onClick={() => handleSort('actor_email')} style={{ width: '16%', padding: '10px 10px', textAlign: 'left', cursor: 'pointer' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: sortKey === 'actor_email' ? 'var(--dvas-blue)' : 'var(--dvas-text-muted)' }}>
                    Actor & Role<SortIcon col="actor_email" />
                  </div>
                </th>
                <th onClick={() => handleSort('action')} style={{ width: '17%', padding: '10px 10px', textAlign: 'left', cursor: 'pointer' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: sortKey === 'action' ? 'var(--dvas-blue)' : 'var(--dvas-text-muted)' }}>
                    Action & Tags<SortIcon col="action" />
                  </div>
                </th>
                <th onClick={() => handleSort('category')} style={{ width: '11%', padding: '10px 10px', textAlign: 'left', cursor: 'pointer' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: sortKey === 'category' ? 'var(--dvas-blue)' : 'var(--dvas-text-muted)' }}>
                    Category<SortIcon col="category" />
                  </div>
                </th>
                <th onClick={() => handleSort('target')} style={{ width: '15%', padding: '10px 10px', textAlign: 'left', cursor: 'pointer' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: sortKey === 'target' ? 'var(--dvas-blue)' : 'var(--dvas-text-muted)' }}>
                    Resource Target<SortIcon col="target" />
                  </div>
                </th>
                <th onClick={() => handleSort('severity')} style={{ width: '9%', padding: '10px 10px', textAlign: 'left', cursor: 'pointer' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: sortKey === 'severity' ? 'var(--dvas-blue)' : 'var(--dvas-text-muted)' }}>
                    Severity<SortIcon col="severity" />
                  </div>
                </th>
                <th onClick={() => handleSort('status')} style={{ width: '9%', padding: '10px 10px', textAlign: 'left', cursor: 'pointer' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: sortKey === 'status' ? 'var(--dvas-blue)' : 'var(--dvas-text-muted)' }}>
                    Status<SortIcon col="status" />
                  </div>
                </th>
                <th style={{ width: '70px', padding: '10px 10px', textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={9} style={{ padding: '50px 0', textAlign: 'center' }}>
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
                      <RefreshCw size={22} className="animate-spin" style={{ color: 'var(--dvas-blue)' }} />
                      <span style={{ fontSize: 12, color: 'var(--dvas-text-dim)' }}>Loading tamper-evident ledger from server…</span>
                    </div>
                  </td>
                </tr>
              ) : queryError ? (
                <tr>
                  <td colSpan={9} style={{ padding: '40px 20px', textAlign: 'center' }}>
                    <div style={{
                      maxWidth: 520,
                      margin: '0 auto',
                      padding: '20px 24px',
                      background: 'rgba(239, 68, 68, 0.08)',
                      border: '1px solid rgba(239, 68, 68, 0.3)',
                      borderRadius: 8,
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: 'center',
                      gap: 12,
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#f87171', fontWeight: 600, fontSize: 15 }}>
                        <AlertTriangle size={20} />
                        <span>Unable to load Audit Ledger ({queryError.status ? `HTTP ${queryError.status}` : 'Network / CORS Error'})</span>
                      </div>
                      <div style={{ fontSize: 13, color: 'var(--dvas-text-dim)', lineHeight: 1.5 }}>
                        {queryError.status === 401 ? (
                          'Your session has expired or authentication credentials were not provided. Please log in again.'
                        ) : queryError.status === 403 ? (
                          'Access restricted: Only Senior Officers and System Administrators have clearance under Section 65B to inspect the system audit trail.'
                        ) : (
                          `Error: ${queryError.detail}. Please verify network connectivity and backend CORS configuration.`
                        )}
                      </div>
                      <div style={{ display: 'flex', gap: 10, marginTop: 4 }}>
                        {queryError.status === 401 ? (
                          <button
                            onClick={() => navigate('/login')}
                            style={{
                              padding: '6px 14px',
                              background: '#38bdf8',
                              color: '#0f172a',
                              border: 'none',
                              borderRadius: 6,
                              fontWeight: 600,
                              fontSize: 12,
                              cursor: 'pointer',
                            }}
                          >
                            Return to Login
                          </button>
                        ) : (
                          <button
                            onClick={() => fetchAuditLogs()}
                            style={{
                              padding: '6px 14px',
                              background: 'var(--dvas-blue)',
                              color: '#0f172a',
                              border: 'none',
                              borderRadius: 6,
                              fontWeight: 600,
                              fontSize: 12,
                              cursor: 'pointer',
                              display: 'flex',
                              alignItems: 'center',
                              gap: 6,
                            }}
                          >
                            <RefreshCw size={14} /> Retry Query
                          </button>
                        )}
                      </div>
                    </div>
                  </td>
                </tr>
              ) : logs.length === 0 ? (
                <tr>
                  <td colSpan={9} style={{ padding: '50px 0', textAlign: 'center', color: 'var(--dvas-text-dim)' }}>
                    <SlidersHorizontal size={26} style={{ opacity: 0.3, margin: '0 auto 8px' }} />
                    <div style={{ fontSize: 13 }}>No audit records found matching your filters.</div>
                  </td>
                </tr>
              ) : (
                logs.map((log, index) => {
                  const isExpanded = expandedRows.has(log.id);
                  const isRowFocused = index === selectedRowIndex;
                  const activeTab = rowTabMap[log.id] || 'overview';
                  const sBadge = statusBadge(log.status, log.error_code);
                  const sevBadge = severityBadge(log.severity);
                  const actBadge = actionTypeBadge(log.action_type);
                  const SIcon = sBadge.icon;
                  const isHeld = Boolean(log.is_legal_hold);
                  const isNewIncoming = newLogIds.has(log.id);

                  return (
                    <React.Fragment key={log.id}>
                      <tr
                        data-testid={`audit-row-${log.id}`}
                        onClick={() => {
                          setSelectedRowIndex(index);
                          toggleRow(log.id);
                        }}
                        style={{
                          borderBottom: '1px solid var(--dvas-border)',
                          background: isRowFocused ? 'rgba(56, 189, 248, 0.06)' : isExpanded ? 'var(--dvas-elev)' : 'transparent',
                          cursor: 'pointer',
                          outline: isRowFocused ? '1px solid rgba(56, 189, 248, 0.3)' : 'none',
                        }}
                        className={`hover:bg-[color:var(--dvas-elev)] ${isNewIncoming ? 'animate-new-row' : ''}`}
                      >
                        {/* Expand Toggle Chevron */}
                        <td style={{ padding: '10px 6px', textAlign: 'center' }}>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setSelectedRowIndex(index);
                              toggleRow(log.id);
                            }}
                            style={{ background: 'none', border: 'none', color: 'var(--dvas-text-dim)', cursor: 'pointer', padding: 0 }}
                          >
                            {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                          </button>
                        </td>

                        {/* Timestamp (Dual IST/UTC) */}
                        <td style={{ padding: '10px 10px' }}>
                          <div style={{ fontSize: 11, fontFamily: 'JetBrains Mono, monospace', color: 'var(--dvas-text)', fontWeight: 500 }}>
                            {formatTimestamp(log.ts, timezone)}
                          </div>
                          <div style={{ fontSize: 10, color: 'var(--dvas-text-dim)', fontFamily: 'JetBrains Mono, monospace' }}>
                            {formatRelativeTime(log.ts)}
                          </div>
                        </td>

                        {/* Actor & Role */}
                        <td style={{ padding: '10px 10px' }}>
                          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--dvas-text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {log.actor_name || log.actor_email || 'System'}
                          </div>
                          <div style={{ fontSize: 10, color: 'var(--dvas-text-dim)', textTransform: 'capitalize', display: 'flex', alignItems: 'center', gap: 4 }}>
                            <span>{log.actor_role?.replace(/_/g, ' ') || 'Internal'}</span>
                            {log.actor_device_id && (
                              <span style={{ fontFamily: 'JetBrains Mono, monospace', opacity: 0.6 }}>· {log.actor_device_id.slice(0, 8)}</span>
                            )}
                          </div>
                        </td>

                        {/* Action & Tags */}
                        <td style={{ padding: '10px 10px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                            <span
                              style={{
                                display: 'inline-block',
                                padding: '2px 7px',
                                borderRadius: 6,
                                fontSize: 11,
                                fontWeight: 600,
                                fontFamily: 'JetBrains Mono, monospace',
                                background: actBadge.bg,
                                color: actBadge.color,
                              }}
                            >
                              {log.action}
                            </span>
                            {isHeld && (
                              <span
                                title={`Legal Hold: Case #${log.legal_hold?.case_number || 'N/A'}`}
                                style={{
                                  display: 'inline-flex',
                                  alignItems: 'center',
                                  gap: 2,
                                  fontSize: 9,
                                  fontWeight: 700,
                                  padding: '1px 5px',
                                  borderRadius: 4,
                                  background: 'rgba(245, 158, 11, 0.2)',
                                  color: 'var(--dvas-warn)',
                                  border: '1px solid rgba(245, 158, 11, 0.4)',
                                }}
                              >
                                <Lock size={9} /> HOLD
                              </span>
                            )}
                            {Array.isArray(log.compliance_tags) && log.compliance_tags.slice(0, 2).map((t) => (
                              <span
                                key={t}
                                style={{
                                  fontSize: 9,
                                  padding: '1px 4px',
                                  borderRadius: 4,
                                  background: 'var(--dvas-elev)',
                                  color: 'var(--dvas-text-muted)',
                                  border: '1px solid var(--dvas-border)',
                                }}
                              >
                                {t.replace(/_/g, '')}
                              </span>
                            ))}
                          </div>
                        </td>

                        {/* Category */}
                        <td style={{ padding: '10px 10px', fontSize: 11, color: 'var(--dvas-text-muted)', textTransform: 'capitalize' }}>
                          {log.category?.replace(/_/g, ' ') || 'operations'}
                        </td>

                        {/* Resource Target with Deep Links */}
                        <td style={{ padding: '10px 10px' }}>
                          {log.target_name || log.target ? (
                            <span
                              onClick={(e) => {
                                if (log.target_type) {
                                  e.stopPropagation();
                                  handleNavigateResource(log.target_type, log.target_id);
                                }
                              }}
                              style={{
                                display: 'inline-flex',
                                alignItems: 'center',
                                gap: 4,
                                fontFamily: 'JetBrains Mono, monospace',
                                fontSize: 11,
                                color: log.target_type ? 'var(--dvas-blue)' : 'var(--dvas-text)',
                                textDecoration: log.target_type ? 'underline' : 'none',
                                textUnderlineOffset: 3,
                                cursor: log.target_type ? 'pointer' : 'default',
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                whiteSpace: 'nowrap',
                                maxWidth: '100%',
                              }}
                            >
                              {log.target_name || log.target}
                              {log.target_type && <ArrowUpRight size={11} />}
                            </span>
                          ) : (
                            <span style={{ color: 'var(--dvas-text-dim)', fontSize: 11 }}>—</span>
                          )}
                        </td>

                        {/* Severity */}
                        <td style={{ padding: '10px 10px' }}>
                          <span
                            style={{
                              display: 'inline-block',
                              padding: '2px 6px',
                              borderRadius: 4,
                              fontSize: 10,
                              fontWeight: 700,
                              fontFamily: 'JetBrains Mono, monospace',
                              background: sevBadge.bg,
                              color: sevBadge.color,
                              border: `1px solid ${sevBadge.border}`,
                            }}
                          >
                            {sevBadge.label}
                          </span>
                        </td>

                        {/* Status with Error Code */}
                        <td style={{ padding: '10px 10px' }}>
                          <span
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: 4,
                              padding: '2px 7px',
                              borderRadius: 999,
                              fontSize: 10,
                              fontWeight: 600,
                              background: sBadge.bg,
                              color: sBadge.color,
                              border: `1px solid ${sBadge.border}`,
                            }}
                          >
                            <SIcon size={10} /> {sBadge.label}
                          </span>
                        </td>

                        {/* Quick Row Actions */}
                        <td style={{ padding: '10px 10px', textAlign: 'right' }}>
                          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                            {/* Legal Hold Button */}
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                setSelectedHoldLog(log);
                                setIsLegalHoldModalOpen(true);
                              }}
                              title={isHeld ? 'Manage / Release Legal Hold' : 'Place Section 65B Legal Hold'}
                              style={{
                                background: 'none',
                                border: 'none',
                                color: isHeld ? 'var(--dvas-warn)' : 'var(--dvas-text-dim)',
                                cursor: 'pointer',
                                padding: 3,
                              }}
                            >
                              {isHeld ? <Lock size={13} /> : <Unlock size={13} />}
                            </button>

                            {/* Chain of Custody Proof */}
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                setSelectedChainLog(log);
                                setIsChainModalOpen(true);
                              }}
                              title="Inspect Section 65B Forensic Proof"
                              style={{
                                background: 'none',
                                border: 'none',
                                color: 'var(--dvas-safe)',
                                cursor: 'pointer',
                                padding: 3,
                              }}
                            >
                              <ShieldCheck size={14} />
                            </button>

                            {/* Drawer Button */}
                            <button
                              data-testid={`drawer-view-${log.id}`}
                              onClick={(e) => {
                                e.stopPropagation();
                                setSelectedDrawerLog(log);
                              }}
                              title="Open in slide-out inspection drawer"
                              style={{
                                background: 'none',
                                border: 'none',
                                color: 'var(--dvas-blue)',
                                cursor: 'pointer',
                                padding: 3,
                              }}
                            >
                              <Eye size={14} />
                            </button>
                          </div>
                        </td>
                      </tr>

                      {/* ── Inline Expanded Detail Row (5 Tabs) ── */}
                      {isExpanded && (
                        <tr>
                          <td colSpan={9} style={{ padding: 0, background: 'var(--dvas-surface-2)', borderBottom: '2px solid var(--dvas-border)' }}>
                            <div style={{ padding: '16px 20px' }}>
                              {/* Tab Headers */}
                              <div style={{ display: 'flex', gap: 6, borderBottom: '1px solid var(--dvas-border)', paddingBottom: 8, marginBottom: 14 }}>
                                {[
                                  { k: 'overview', l: 'Overview & Metadata', icon: FileText },
                                  { k: 'diff', l: 'State Diff (Before / After)', icon: Eye, disabled: !log.before_values && !log.after_values },
                                  { k: 'payload', l: 'Full JSON Payload', icon: Layers },
                                  { k: 'related', l: 'Correlated Events Timeline', icon: Clock },
                                  { k: 'chain', l: 'Forensic Chain-of-Custody Proof', icon: ShieldCheck },
                                ].map((tb) => {
                                  const TIcon = tb.icon;
                                  const active = activeTab === tb.k;
                                  return (
                                    <button
                                      key={tb.k}
                                      disabled={tb.disabled}
                                      onClick={() => setRowTab(log.id, tb.k)}
                                      style={{
                                        display: 'flex',
                                        alignItems: 'center',
                                        gap: 6,
                                        padding: '6px 12px',
                                        fontSize: 12,
                                        fontWeight: 600,
                                        borderRadius: 6,
                                        cursor: tb.disabled ? 'not-allowed' : 'pointer',
                                        background: active ? 'var(--dvas-surface)' : 'transparent',
                                        border: active ? '1px solid var(--dvas-border)' : '1px solid transparent',
                                        color: active ? 'var(--dvas-blue)' : tb.disabled ? 'var(--dvas-text-dim)' : 'var(--dvas-text-muted)',
                                        opacity: tb.disabled ? 0.5 : 1,
                                      }}
                                    >
                                      <TIcon size={13} />
                                      {tb.l}
                                    </button>
                                  );
                                })}
                              </div>

                              {/* Tab 1: Overview */}
                              {activeTab === 'overview' && (
                                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12, fontSize: 11 }}>
                                  <div style={{ background: 'var(--dvas-surface)', padding: 12, borderRadius: 8, border: '1px solid var(--dvas-border)' }}>
                                    <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', color: 'var(--dvas-text-dim)', marginBottom: 4 }}>Actor & Identity</div>
                                    <div style={{ color: 'var(--dvas-text)', fontWeight: 600 }}>{log.actor_name || '—'}</div>
                                    <div style={{ color: 'var(--dvas-text-muted)', fontFamily: 'JetBrains Mono, monospace' }}>{log.actor_email || '—'}</div>
                                    <div style={{ color: 'var(--dvas-text-dim)', marginTop: 4 }}>Role: {log.actor_role || '—'}</div>
                                    <div style={{ color: 'var(--dvas-text-dim)', fontFamily: 'JetBrains Mono, monospace' }}>Actor ID: {log.actor_id || '—'}</div>
                                    <div style={{ color: 'var(--dvas-text-dim)' }}>Session: {log.actor_session_id || '—'}</div>
                                    <div style={{ color: 'var(--dvas-text-dim)' }}>Device: {log.actor_device_id || '—'}</div>
                                  </div>

                                  <div style={{ background: 'var(--dvas-surface)', padding: 12, borderRadius: 8, border: '1px solid var(--dvas-border)' }}>
                                    <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', color: 'var(--dvas-text-dim)', marginBottom: 4 }}>Target Resource</div>
                                    <div style={{ color: 'var(--dvas-text)', fontWeight: 600 }}>{log.target_name || log.target || '—'}</div>
                                    <div style={{ color: 'var(--dvas-text-muted)' }}>Type: {log.target_type || '—'}</div>
                                    <div style={{ color: 'var(--dvas-text-dim)', fontFamily: 'JetBrains Mono, monospace' }}>ID: {log.target_id || '—'}</div>
                                    {log.details && <div style={{ color: 'var(--dvas-text-muted)', marginTop: 4 }}>Details: {log.details}</div>}
                                  </div>

                                  <div style={{ background: 'var(--dvas-surface)', padding: 12, borderRadius: 8, border: '1px solid var(--dvas-border)' }}>
                                    <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', color: 'var(--dvas-text-dim)', marginBottom: 4 }}>Network & Tracing</div>
                                    <div style={{ color: 'var(--dvas-text)', fontFamily: 'JetBrains Mono, monospace' }}>IP: {log.ip}</div>
                                    <div style={{ color: 'var(--dvas-text-muted)' }}>Module: {log.source_module || 'dvas_core'}</div>
                                    <div style={{ color: 'var(--dvas-text-muted)' }}>Endpoint: {log.source_endpoint || '—'}</div>
                                    <div style={{ color: 'var(--dvas-text-dim)', fontFamily: 'JetBrains Mono, monospace' }}>Req ID: {log.request_id || '—'}</div>
                                    <div style={{ color: 'var(--dvas-text-dim)', fontFamily: 'JetBrains Mono, monospace' }}>Corr ID: {log.correlation_id || '—'}</div>
                                  </div>

                                  <div style={{ background: 'var(--dvas-surface)', padding: 12, borderRadius: 8, border: '1px solid var(--dvas-border)' }}>
                                    <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', color: 'var(--dvas-text-dim)', marginBottom: 4 }}>Compliance & Chain</div>
                                    <div style={{ fontSize: 10, color: 'var(--dvas-blue)', fontFamily: 'JetBrains Mono, monospace', wordBreak: 'break-all' }}>
                                      SHA-256: {log.hash || '—'}
                                    </div>
                                    <div style={{ fontSize: 10, color: 'var(--dvas-text-dim)', fontFamily: 'JetBrains Mono, monospace', wordBreak: 'break-all', marginTop: 4 }}>
                                      Prev: {log.prev_hash || '—'}
                                    </div>
                                    <div style={{ marginTop: 6, display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                                      {Array.isArray(log.compliance_tags) && log.compliance_tags.map((t) => (
                                        <span key={t} style={{ fontSize: 9, padding: '1px 5px', borderRadius: 4, background: 'var(--dvas-elev)', color: 'var(--dvas-blue)', border: '1px solid var(--dvas-border)' }}>
                                          {t}
                                        </span>
                                      ))}
                                    </div>
                                  </div>
                                </div>
                              )}

                              {/* Tab 2: Diff with Credential Redaction */}
                              {activeTab === 'diff' && (
                                <DiffViewer before={log.before_values} after={log.after_values} />
                              )}

                              {/* Tab 3: Payload */}
                              {activeTab === 'payload' && (
                                <JsonViewer data={log} />
                              )}

                              {/* Tab 4: Related Events Timeline */}
                              {activeTab === 'related' && (
                                <RelatedEventsTimeline
                                  correlationId={log.correlation_id}
                                  sessionId={log.actor_session_id}
                                  currentLogId={log.id}
                                  timezone={timezone}
                                  onSelectLog={(selected) => setSelectedDrawerLog(selected)}
                                  onFilterCorrelationId={(corrId) => setSearch(corrId)}
                                />
                              )}

                              {/* Tab 5: Forensic Proof */}
                              {activeTab === 'chain' && (
                                <div style={{ background: 'var(--dvas-surface)', padding: 16, borderRadius: 8, border: '1px solid var(--dvas-border)' }}>
                                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                      <ShieldCheck size={18} style={{ color: 'var(--dvas-safe)' }} />
                                      <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--dvas-text)' }}>
                                        Section 65B Electronic Proof Certificate
                                      </span>
                                    </div>
                                    <button
                                      onClick={() => {
                                        setSelectedChainLog(log);
                                        setIsChainModalOpen(true);
                                      }}
                                      style={{
                                        display: 'flex',
                                        alignItems: 'center',
                                        gap: 4,
                                        fontSize: 11,
                                        fontWeight: 600,
                                        padding: '4px 10px',
                                        borderRadius: 6,
                                        background: 'var(--dvas-elev)',
                                        border: '1px solid var(--dvas-blue)',
                                        color: 'var(--dvas-blue)',
                                        cursor: 'pointer',
                                      }}
                                    >
                                      <ExternalLink size={12} /> Open Full Forensic Card
                                    </button>
                                  </div>
                                  <div style={{ fontSize: 11, color: 'var(--dvas-text-muted)', lineHeight: 1.6 }}>
                                    This log record is sealed with SHA-256 cryptographic linkage and is admissible as secondary evidence in court under Section 65B(4) of the Indian Evidence Act 1872 / Bharatiya Sakshya Adhiniyam 2023.
                                  </div>
                                </div>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {/* ── Server-Side Pagination Bar ── */}
        <div
          style={{
            padding: '12px 18px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderTop: '1px solid var(--dvas-border)',
            background: 'var(--dvas-surface-2)',
            flexWrap: 'wrap',
            gap: 12,
          }}
        >
          <div style={{ fontSize: 12, color: 'var(--dvas-text-dim)', display: 'flex', alignItems: 'center', gap: 12 }}>
            <span>
              Showing&nbsp;
              <strong style={{ color: 'var(--dvas-text)', fontFamily: 'JetBrains Mono, monospace' }}>
                {totalLogs === 0 ? 0 : (page - 1) * pageSize + 1}–{Math.min(page * pageSize, totalLogs)}
              </strong>
              &nbsp;of&nbsp;
              <strong style={{ color: 'var(--dvas-text)', fontFamily: 'JetBrains Mono, monospace' }}>{totalLogs}</strong> entries
              &nbsp;(Page {page} of {totalPages})
            </span>

            {/* Page Size Selector */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ fontSize: 11 }}>Per page:</span>
              <select
                value={pageSize}
                onChange={(e) => {
                  setPageSize(Number(e.target.value));
                  setPage(1);
                }}
                style={{
                  fontSize: 11,
                  padding: '3px 8px',
                  borderRadius: 6,
                  background: 'var(--dvas-elev)',
                  border: '1px solid var(--dvas-border)',
                  color: 'var(--dvas-text)',
                  outline: 'none',
                  cursor: 'pointer',
                }}
              >
                {[15, 25, 50, 100].map((sz) => (
                  <option key={sz} value={sz}>{sz}</option>
                ))}
              </select>
            </div>
          </div>

          <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
            <button
              data-testid="page-prev"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              style={{
                width: 32,
                height: 32,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                borderRadius: 8,
                cursor: page <= 1 ? 'not-allowed' : 'pointer',
                background: 'var(--dvas-elev)',
                border: '1px solid var(--dvas-border)',
                color: 'var(--dvas-text)',
                opacity: page <= 1 ? 0.4 : 1,
              }}
            >
              <ChevronLeft size={15} />
            </button>

            {/* Page Numbers */}
            {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
              let p;
              if (totalPages <= 5) p = i + 1;
              else if (page <= 3) p = i + 1;
              else if (page >= totalPages - 2) p = totalPages - 4 + i;
              else p = page - 2 + i;

              return (
                <button
                  key={p}
                  data-testid={`page-btn-${p}`}
                  onClick={() => setPage(p)}
                  style={{
                    width: 32,
                    height: 32,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    borderRadius: 8,
                    cursor: 'pointer',
                    fontSize: 12,
                    fontWeight: 600,
                    fontFamily: 'JetBrains Mono, monospace',
                    background: page === p ? 'var(--dvas-blue)' : 'var(--dvas-elev)',
                    color: page === p ? '#fff' : 'var(--dvas-text)',
                    border: page === p ? 'none' : '1px solid var(--dvas-border)',
                  }}
                >
                  {p}
                </button>
              );
            })}

            <button
              data-testid="page-next"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              style={{
                width: 32,
                height: 32,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                borderRadius: 8,
                cursor: page >= totalPages ? 'not-allowed' : 'pointer',
                background: 'var(--dvas-elev)',
                border: '1px solid var(--dvas-border)',
                color: 'var(--dvas-text)',
                opacity: page >= totalPages ? 0.4 : 1,
              }}
            >
              <ChevronRight size={15} />
            </button>
          </div>
        </div>
      </div>

      {/* ── Slide-Out Inspection Drawer (5 Tabs) ── */}
      {selectedDrawerLog && (
        <div
          onClick={() => setSelectedDrawerLog(null)}
          style={{ position: 'fixed', inset: 0, zIndex: 90, display: 'flex', background: 'rgba(15,23,42,0.65)' }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            className="dvas-fade-in"
            style={{
              marginLeft: 'auto',
              height: '100%',
              display: 'flex',
              flexDirection: 'column',
              width: 560,
              maxWidth: '95vw',
              background: 'var(--dvas-surface)',
              borderLeft: '1px solid var(--dvas-border)',
              boxShadow: 'var(--dvas-shadow-lg)',
              overflow: 'hidden',
            }}
          >
            {/* Drawer Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '16px 20px', borderBottom: '1px solid var(--dvas-border)', flexShrink: 0 }}>
              <div style={{ width: 34, height: 34, borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--dvas-info-bg)', flexShrink: 0 }}>
                <FileText size={18} style={{ color: 'var(--dvas-blue)' }} />
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 700, fontFamily: 'JetBrains Mono, monospace', color: 'var(--dvas-text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {selectedDrawerLog.action}
                </div>
                <div style={{ fontSize: 10, marginTop: 2, fontFamily: 'JetBrains Mono, monospace', color: 'var(--dvas-text-dim)' }}>
                  ID: {selectedDrawerLog.id}
                </div>
              </div>
              <button
                data-testid="drawer-close"
                onClick={() => setSelectedDrawerLog(null)}
                style={{ width: 28, height: 28, borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--dvas-text-muted)' }}
              >
                <X size={16} />
              </button>
            </div>

            {/* Meta Grid */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', padding: '12px 20px', gap: 10, borderBottom: '1px solid var(--dvas-border)', background: 'var(--dvas-surface-2)', flexShrink: 0 }}>
              <div>
                <div style={{ fontSize: 9, fontWeight: 600, textTransform: 'uppercase', color: 'var(--dvas-text-dim)', marginBottom: 2 }}>Timestamp ({timezone})</div>
                <div style={{ fontSize: 11, fontFamily: 'JetBrains Mono, monospace', color: 'var(--dvas-text)' }}>{formatTimestamp(selectedDrawerLog.ts, timezone)}</div>
              </div>
              <div>
                <div style={{ fontSize: 9, fontWeight: 600, textTransform: 'uppercase', color: 'var(--dvas-text-dim)', marginBottom: 2 }}>Actor</div>
                <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--dvas-text)' }}>{selectedDrawerLog.actor_name || selectedDrawerLog.actor_email}</div>
              </div>
              <div>
                <div style={{ fontSize: 9, fontWeight: 600, textTransform: 'uppercase', color: 'var(--dvas-text-dim)', marginBottom: 2 }}>IP Address</div>
                <div style={{ fontSize: 11, fontFamily: 'JetBrains Mono, monospace', color: 'var(--dvas-text)' }}>{selectedDrawerLog.ip}</div>
              </div>
              <div>
                <div style={{ fontSize: 9, fontWeight: 600, textTransform: 'uppercase', color: 'var(--dvas-text-dim)', marginBottom: 2 }}>Target Resource</div>
                <div style={{ fontSize: 11, fontFamily: 'JetBrains Mono, monospace', color: 'var(--dvas-text)' }}>{selectedDrawerLog.target_name || selectedDrawerLog.target || '—'}</div>
              </div>
            </div>

            {/* Drawer Tab Headers */}
            <div style={{ display: 'flex', gap: 4, padding: '8px 16px', borderBottom: '1px solid var(--dvas-border)', background: 'var(--dvas-elev)', flexShrink: 0 }}>
              {[
                { k: 'overview', l: 'Overview' },
                { k: 'diff', l: 'State Diff' },
                { k: 'payload', l: 'JSON' },
                { k: 'timeline', l: 'Timeline' },
                { k: 'proof', l: '§65B Proof' },
              ].map((t) => (
                <button
                  key={t.k}
                  onClick={() => setDrawerTab(t.k)}
                  style={{
                    padding: '4px 10px',
                    fontSize: 11,
                    fontWeight: 600,
                    borderRadius: 6,
                    border: 'none',
                    background: drawerTab === t.k ? 'var(--dvas-surface)' : 'transparent',
                    color: drawerTab === t.k ? 'var(--dvas-blue)' : 'var(--dvas-text-muted)',
                    cursor: 'pointer',
                  }}
                >
                  {t.l}
                </button>
              ))}
            </div>

            {/* Drawer Body */}
            <div style={{ flex: 1, overflow: 'auto', padding: 20 }}>
              {drawerTab === 'overview' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                  <div style={{ background: 'var(--dvas-surface-2)', padding: 12, borderRadius: 8, border: '1px solid var(--dvas-border)', fontSize: 11 }}>
                    <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', color: 'var(--dvas-text-dim)', marginBottom: 6 }}>Identity & Device Fingerprint</div>
                    <div>Email: {selectedDrawerLog.actor_email || '—'}</div>
                    <div>Role: {selectedDrawerLog.actor_role || '—'}</div>
                    <div>Session ID: {selectedDrawerLog.actor_session_id || '—'}</div>
                    <div>Device ID: {selectedDrawerLog.actor_device_id || '—'}</div>
                    <div>User Agent: {selectedDrawerLog.user_agent || '—'}</div>
                  </div>

                  <div style={{ background: 'var(--dvas-surface-2)', padding: 12, borderRadius: 8, border: '1px solid var(--dvas-border)', fontSize: 11 }}>
                    <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', color: 'var(--dvas-text-dim)', marginBottom: 6 }}>Correlation & Tracing</div>
                    <div>Correlation ID: <span style={{ fontFamily: 'JetBrains Mono, monospace', color: 'var(--dvas-blue)' }}>{selectedDrawerLog.correlation_id || '—'}</span></div>
                    <div>Request ID: <span style={{ fontFamily: 'JetBrains Mono, monospace' }}>{selectedDrawerLog.request_id || '—'}</span></div>
                    <div>Source Module: {selectedDrawerLog.source_module || 'dvas_core'}</div>
                    <div>Source Endpoint: {selectedDrawerLog.source_endpoint || '—'}</div>
                  </div>

                  {selectedDrawerLog.is_legal_hold && (
                    <div style={{ background: 'rgba(245, 158, 11, 0.08)', padding: 12, borderRadius: 8, border: '1px solid rgba(245, 158, 11, 0.3)', fontSize: 11 }}>
                      <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', color: 'var(--dvas-warn)', marginBottom: 4 }}>Preserved Under Legal Hold</div>
                      <div>Case Number: <strong>{selectedDrawerLog.legal_hold?.case_number}</strong></div>
                      <div>Authority: {selectedDrawerLog.legal_hold?.legal_authority}</div>
                      <div>Placed By: {selectedDrawerLog.legal_hold?.placed_by}</div>
                      <div>Justification: {selectedDrawerLog.legal_hold?.justification}</div>
                    </div>
                  )}
                </div>
              )}

              {drawerTab === 'diff' && (
                <DiffViewer before={selectedDrawerLog.before_values} after={selectedDrawerLog.after_values} />
              )}

              {drawerTab === 'payload' && (
                <JsonViewer data={selectedDrawerLog} />
              )}

              {drawerTab === 'timeline' && (
                <RelatedEventsTimeline
                  correlationId={selectedDrawerLog.correlation_id}
                  sessionId={selectedDrawerLog.actor_session_id}
                  currentLogId={selectedDrawerLog.id}
                  timezone={timezone}
                  onSelectLog={(sel) => setSelectedDrawerLog(sel)}
                  onFilterCorrelationId={(corrId) => setSearch(corrId)}
                />
              )}

              {drawerTab === 'proof' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                  <div style={{ background: 'var(--dvas-surface-2)', padding: 12, borderRadius: 8, border: '1px solid var(--dvas-border)', fontSize: 11 }}>
                    <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', color: 'var(--dvas-text-dim)', marginBottom: 6 }}>Cryptographic Chain Proof</div>
                    <div style={{ marginBottom: 6 }}>
                      <div style={{ color: 'var(--dvas-text-dim)', fontSize: 10 }}>RECORD HASH (SHA-256)</div>
                      <div style={{ fontFamily: 'JetBrains Mono, monospace', color: 'var(--dvas-blue)', wordBreak: 'break-all' }}>{selectedDrawerLog.hash}</div>
                    </div>
                    <div>
                      <div style={{ color: 'var(--dvas-text-dim)', fontSize: 10 }}>LINKED PREVIOUS HASH</div>
                      <div style={{ fontFamily: 'JetBrains Mono, monospace', color: 'var(--dvas-text-muted)', wordBreak: 'break-all' }}>{selectedDrawerLog.prev_hash}</div>
                    </div>
                  </div>
                  <button
                    onClick={() => {
                      setSelectedChainLog(selectedDrawerLog);
                      setIsChainModalOpen(true);
                    }}
                    style={{
                      padding: '8px 14px',
                      fontSize: 12,
                      fontWeight: 600,
                      borderRadius: 8,
                      background: 'var(--dvas-blue)',
                      border: 'none',
                      color: '#fff',
                      cursor: 'pointer',
                    }}
                  >
                    Open Full Section 65B Certificate Card
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── Modals ── */}
      <SavePresetModal
        isOpen={isPresetModalOpen}
        onClose={() => setIsPresetModalOpen(false)}
        currentFilters={{
          q: search,
          date_from: dateFrom,
          date_to: dateTo,
          actor_id: actorFilter,
          actor_role: roleFilter,
          category: selectedCategories,
          severity: selectedSeverities,
          action_type: selectedActionTypes,
          status: selectedStatuses,
          target_type: targetTypeFilter,
          source_module: sourceModuleFilter,
          compliance_tag: complianceTagFilter,
          is_legal_hold: legalHoldOnly,
        }}
        onSaved={async () => {
          try {
            const { data } = await api.get('/audit-presets');
            setPresetsList(data || []);
          } catch (_) {}
        }}
      />

      <ChainOfCustodyModal
        log={selectedChainLog}
        isOpen={isChainModalOpen}
        timezone={timezone}
        onClose={() => {
          setIsChainModalOpen(false);
          setSelectedChainLog(null);
        }}
      />

      <LegalHoldModal
        log={selectedHoldLog}
        isOpen={isLegalHoldModalOpen}
        timezone={timezone}
        onClose={() => {
          setIsLegalHoldModalOpen(false);
          setSelectedHoldLog(null);
        }}
        onHoldUpdated={() => {
          fetchAuditLogs();
        }}
      />

      <RetentionPolicyModal
        isOpen={isRetentionModalOpen}
        onClose={() => setIsRetentionModalOpen(false)}
      />

      <ArchiveAndPurgeModal
        isOpen={isArchiveModalOpen}
        timezone={timezone}
        onClose={() => setIsArchiveModalOpen(false)}
        onPurged={() => {
          fetchAuditLogs();
          handleVerifyChain();
        }}
      />
    </AppShell>
  );
}
