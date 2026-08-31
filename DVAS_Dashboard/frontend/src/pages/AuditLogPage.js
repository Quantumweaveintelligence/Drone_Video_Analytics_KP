/**
 * AuditLogPage.js  —  Production Audit Log Explorer
 * =================================================================
 * Production-ready, tamper-evident Audit Log Explorer for Kerala Police DVAS.
 *
 * Core Capabilities:
 *  1. Real backend API integration via `/api/audit-logs` (no mock data).
 *  2. Server-side pagination, multi-column sorting, and comprehensive 10+ dimension filtering.
 *  3. Summary KPI Cards with real-time aggregate counts from `/api/audit-logs/summary`.
 *  4. Cryptographic SHA-256 hash-chain verification with live integrity status badge.
 *  5. Rule-Based Security Anomaly Detection (7 patterns) with real-time alert banner.
 *  6. Indian Evidence Act Section 65B Certified PDF Evidence Export.
 *  7. Inline expandable rows with 4 inspection tabs:
 *     - Overview: Actor grid (name, email, role, IP, session ID, device fingerprint, request ID)
 *     - Changes: Field-level Before / After diff viewer with change highlight
 *     - Payload: Formatted raw JSON viewer with one-click copy
 *     - Related: Correlated session / transaction events timeline
 *  8. Deep resource links navigating to target entities (Users, Missions, Evidence).
 *  9. Filter presets management (save, load, and delete custom filter configurations).
 *  10. Direct server-side streaming CSV & JSON exports.
 *  11. Keyboard navigation (j/k/arrows for row focus, Enter to toggle, Esc to dismiss).
 *  12. Print-optimized stylesheet for statutory physical reporting.
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import AppShell from '@/components/layout/AppShell';
import { useAuth } from '@/context/AuthContext';
import { api } from '@/lib/api';
import {
  Search, Filter, Download, X, ChevronLeft, ChevronRight,
  Shield, AlertTriangle, CheckCircle, Clock, Activity,
  FileText, ChevronDown, ChevronUp, Eye, Layers,
  RefreshCw, Calendar, SlidersHorizontal, UserCheck, ShieldCheck,
  ShieldAlert, ArrowUpRight, RotateCcw, Bookmark, BookmarkPlus,
  Copy, Check, Printer, FileCheck, Zap, AlertCircle
} from 'lucide-react';

// ─────────────────────────────────────────────────────────────────────────────
// Formatters & Styling Helpers
// ─────────────────────────────────────────────────────────────────────────────

function formatDateIST(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    return d.toLocaleString('en-IN', {
      timeZone: 'Asia/Kolkata',
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: true,
    });
  } catch (_) {
    return ts;
  }
}

function formatRelativeTime(ts) {
  if (!ts) return '';
  try {
    const diffMs = Date.now() - new Date(ts).getTime();
    const diffSec = Math.floor(diffMs / 1000);
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

function statusBadge(status) {
  const s = (status || 'success').toLowerCase();
  if (s === 'success') {
    return {
      bg: 'var(--dvas-safe-bg)',
      color: 'var(--dvas-safe)',
      border: 'color-mix(in srgb, var(--dvas-safe) 30%, transparent)',
      label: 'Success',
      icon: CheckCircle,
    };
  }
  if (s === 'failure' || s === 'failed' || s === 'error') {
    return {
      bg: 'var(--dvas-crit-bg)',
      color: 'var(--dvas-crit)',
      border: 'color-mix(in srgb, var(--dvas-crit) 30%, transparent)',
      label: 'Failed',
      icon: X,
    };
  }
  return {
    bg: 'var(--dvas-warn-bg)',
    color: 'var(--dvas-warn)',
    border: 'color-mix(in srgb, var(--dvas-warn) 30%, transparent)',
    label: status || 'Warning',
    icon: AlertTriangle,
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
    create: { color: '#34d399', bg: 'rgba(52, 211, 153, 0.12)' },
    update: { color: '#60a5fa', bg: 'rgba(96, 165, 250, 0.12)' },
    delete: { color: '#f87171', bg: 'rgba(248, 113, 113, 0.12)' },
    login:  { color: '#818cf8', bg: 'rgba(129, 140, 248, 0.12)' },
    logout: { color: '#a78bfa', bg: 'rgba(167, 139, 250, 0.12)' },
    export: { color: '#fb923c', bg: 'rgba(251, 146, 60, 0.12)' },
    read:   { color: '#94a3b8', bg: 'rgba(148, 163, 184, 0.12)' },
    system: { color: '#94a3b8', bg: 'rgba(148, 163, 184, 0.12)' },
  };
  return map[t] || map.system;
}

// ─────────────────────────────────────────────────────────────────────────────
// Subcomponents
// ─────────────────────────────────────────────────────────────────────────────

function SummaryCard({ icon: Icon, label, value, sub, color, testId }) {
  return (
    <div
      data-testid={testId}
      style={{
        background: 'var(--dvas-surface)',
        border: '1px solid var(--dvas-border)',
        borderRadius: 'var(--dvas-radius-sm)',
        padding: '16px 20px',
        boxShadow: 'var(--dvas-shadow)',
        display: 'flex',
        alignItems: 'center',
        gap: 16,
      }}
    >
      <div
        style={{
          width: 44,
          height: 44,
          borderRadius: 10,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: `color-mix(in srgb, ${color} 15%, transparent)`,
          border: `1px solid color-mix(in srgb, ${color} 30%, transparent)`,
          flexShrink: 0,
        }}
      >
        <Icon size={22} style={{ color }} />
      </div>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--dvas-text-dim)' }}>
          {label}
        </div>
        <div style={{ fontSize: 24, fontWeight: 700, fontFamily: 'JetBrains Mono, monospace', color: 'var(--dvas-text)', marginBlock: '2px' }}>
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

function MultiSelect({ label, options, selected, onChange, testId }) {
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
          height: 36,
          paddingInline: 12,
          fontSize: 12,
          borderRadius: 8,
          background: 'var(--dvas-elev)',
          border: '1px solid var(--dvas-border)',
          color: 'var(--dvas-text)',
          minWidth: 140,
          cursor: 'pointer',
        }}
      >
        <Filter size={13} style={{ color: 'var(--dvas-text-muted)', flexShrink: 0 }} />
        <span style={{ flex: 1, textAlign: 'left', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {btnText}
        </span>
        <ChevronDown size={12} style={{ color: 'var(--dvas-text-muted)', flexShrink: 0 }} />
      </button>
      {open && (
        <div
          className="dvas-fade-in"
          style={{
            position: 'absolute',
            top: 40,
            left: 0,
            zIndex: 50,
            width: 220,
            maxHeight: 280,
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
                padding: '8px 12px',
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
              <span style={{ textTransform: 'capitalize' }}>{opt.replace(/_/g, ' ')}</span>
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

function JsonViewer({ data }) {
  const [copied, setCopied] = useState(false);
  const text = JSON.stringify(data || {}, null, 2);

  const handleCopy = () => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div style={{ position: 'relative' }}>
      <button
        onClick={handleCopy}
        style={{
          position: 'absolute',
          top: 10,
          right: 10,
          display: 'flex',
          alignItems: 'center',
          gap: 4,
          padding: '4px 8px',
          fontSize: 11,
          fontWeight: 600,
          borderRadius: 6,
          background: 'var(--dvas-elev)',
          border: '1px solid var(--dvas-border)',
          color: copied ? 'var(--dvas-safe)' : 'var(--dvas-text-muted)',
          cursor: 'pointer',
          zIndex: 10,
        }}
      >
        {copied ? <Check size={12} /> : <Copy size={12} />}
        {copied ? 'Copied' : 'Copy'}
      </button>
      <pre
        style={{
          fontSize: 11,
          overflow: 'auto',
          borderRadius: 8,
          padding: 16,
          fontFamily: 'JetBrains Mono, monospace',
          background: 'var(--dvas-surface-2)',
          color: 'var(--dvas-text)',
          border: '1px solid var(--dvas-border)',
          maxHeight: 280,
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
      <div style={{ fontSize: 12, textAlign: 'center', padding: '32px 0', color: 'var(--dvas-text-dim)' }}>
        No before/after state diff captured for this action.
      </div>
    );
  }

  const keys = Array.from(new Set([...Object.keys(before || {}), ...Object.keys(after || {})]));

  return (
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
        <span>Field</span>
        <span>Before Value (Previous)</span>
        <span>After Value (Applied)</span>
      </div>
      {keys.map((key) => {
        const bv = before?.[key];
        const av = after?.[key];
        const changed = JSON.stringify(bv) !== JSON.stringify(av);
        return (
          <div
            key={key}
            style={{
              display: 'grid',
              gridTemplateColumns: '1.2fr 1.9fr 1.9fr',
              borderBottom: '1px solid var(--dvas-border)',
              background: changed ? 'rgba(245, 158, 11, 0.08)' : 'transparent',
              padding: '8px 12px',
              alignItems: 'center',
              gap: 8,
            }}
          >
            <span style={{ color: 'var(--dvas-text)', fontWeight: 600 }}>{key}</span>
            <span style={{ color: bv == null ? 'var(--dvas-text-dim)' : 'var(--dvas-crit)', wordBreak: 'break-all' }}>
              {bv == null ? '—' : JSON.stringify(bv)}
            </span>
            <span style={{ color: av == null ? 'var(--dvas-text-dim)' : 'var(--dvas-safe)', wordBreak: 'break-all' }}>
              {av == null ? '—' : JSON.stringify(av)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function RelatedEventsTimeline({ correlationId, sessionId, currentLogId, onSelectLog }) {
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
      } catch (err) {
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
    return <div style={{ fontSize: 12, padding: 20, textAlign: 'center', color: 'var(--dvas-text-dim)' }}>No related timeline events found for correlation ID.</div>;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--dvas-text-muted)', marginBottom: 4 }}>
        Timeline ({events.length} correlated events):
      </div>
      {events.map((ev, idx) => {
        const isCurrent = ev.id === currentLogId;
        const sty = statusBadge(ev.status);
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
              {formatDateIST(ev.ts)}
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
    <div style={{ position: 'fixed', inset: 0, zIndex: 100, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.65)' }}>
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
            <BookmarkPlus size={16} style={{ color: 'var(--dvas-blue)' }} /> Save Filter Preset
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'var(--dvas-text-dim)', cursor: 'pointer' }}>
            <X size={16} />
          </button>
        </div>
        <form onSubmit={handleSave}>
          <div style={{ marginBottom: 14 }}>
            <label style={{ display: 'block', fontSize: 11, fontWeight: 600, color: 'var(--dvas-text-dim)', marginBottom: 6 }}>
              Preset Name
            </label>
            <input
              type="text"
              placeholder="e.g. Critical Security Actions"
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
            Share preset with other authorized officers
          </label>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
            <button
              type="button"
              onClick={onClose}
              style={{
                padding: '6px 14px',
                fontSize: 12,
                fontWeight: 600,
                borderRadius: 8,
                background: 'var(--dvas-elev)',
                border: '1px solid var(--dvas-border)',
                color: 'var(--dvas-text)',
                cursor: 'pointer',
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving || !name.trim()}
              style={{
                padding: '6px 16px',
                fontSize: 12,
                fontWeight: 600,
                borderRadius: 8,
                background: 'var(--dvas-blue)',
                border: 'none',
                color: '#fff',
                cursor: 'pointer',
                opacity: saving || !name.trim() ? 0.6 : 1,
              }}
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

  // Data state
  const [logs, setLogs] = useState([]);
  const [totalLogs, setTotalLogs] = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading] = useState(true);

  // KPIs & summary
  const [summary, setSummary] = useState({ total_events: 0, failed_actions: 0, critical_events: 0, warnings: 0, info_events: 0, anomalies_detected: 0 });
  const [chainStatus, setChainStatus] = useState(null);
  const [verifyingChain, setVerifyingChain] = useState(false);
  const [anomaliesData, setAnomaliesData] = useState([]);
  const [showAnomaliesBanner, setShowAnomaliesBanner] = useState(true);

  // Filters state
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [actorFilter, setActorFilter] = useState('');
  const [selectedCategories, setSelectedCategories] = useState([]);
  const [selectedSeverities, setSelectedSeverities] = useState([]);
  const [selectedActionTypes, setSelectedActionTypes] = useState([]);
  const [selectedStatuses, setSelectedStatuses] = useState([]);
  const [sortKey, setSortKey] = useState('ts');
  const [sortDir, setSortDir] = useState('desc');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);

  // Filter options from backend
  const [actorsList, setActorsList] = useState([]);
  const [actionTypesList, setActionTypesList] = useState([]);
  const [categoriesList, setCategoriesList] = useState([]);
  const [severitiesList, setSeveritiesList] = useState([]);
  const [presetsList, setPresetsList] = useState([]);

  // UI state
  const [expandedRows, setExpandedRows] = useState(new Set());
  const [rowTabMap, setRowTabMap] = useState({});
  const [selectedDrawerLog, setSelectedDrawerLog] = useState(null);
  const [isPresetModalOpen, setIsPresetModalOpen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [selectedRowIndex, setSelectedRowIndex] = useState(0);

  // Debounce search
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(timer);
  }, [search]);

  // Load dropdown metadata & presets once on mount
  useEffect(() => {
    async function loadMetadata() {
      try {
        const [actRes, typRes, catRes, sevRes, preRes, anomRes] = await Promise.allSettled([
          api.get('/audit/actors'),
          api.get('/audit-logs/action-types'),
          api.get('/audit-logs/categories'),
          api.get('/audit-logs/severities'),
          api.get('/audit-presets'),
          api.get('/audit-logs/anomalies?hours=24'),
        ]);
        if (actRes.status === 'fulfilled') setActorsList(actRes.value.data || []);
        if (typRes.status === 'fulfilled') setActionTypesList(typRes.value.data || []);
        if (catRes.status === 'fulfilled') setCategoriesList(catRes.value.data || []);
        if (sevRes.status === 'fulfilled') setSeveritiesList(sevRes.value.data || []);
        if (preRes.status === 'fulfilled') setPresetsList(preRes.value.data || []);
        if (anomRes.status === 'fulfilled') setAnomaliesData(anomRes.value.data?.anomalies || []);
      } catch (err) {
        console.error('Metadata load error:', err);
      }
    }
    loadMetadata();
  }, []);

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
      if (dateTo) params.set('date_to', dateTo + 'T23:59:59');
      if (actorFilter) params.set('actor_id', actorFilter);
      if (selectedCategories.length) params.set('category', selectedCategories.join(','));
      if (selectedSeverities.length) params.set('severity', selectedSeverities.join(','));
      if (selectedActionTypes.length) params.set('action_type', selectedActionTypes.join(','));
      if (selectedStatuses.length) params.set('status', selectedStatuses.join(','));

      const [logsRes, summaryRes] = await Promise.all([
        api.get(`/audit-logs?${params.toString()}`),
        api.get(`/audit-logs/summary?${params.toString()}`),
      ]);

      setLogs(logsRes.data.items || []);
      setTotalLogs(logsRes.data.total || 0);
      setTotalPages(logsRes.data.total_pages || 1);
      setSummary(summaryRes.data || {});
    } catch (err) {
      console.error('Audit query error:', err);
      setLogs([]);
    } finally {
      setLoading(false);
    }
  }, [
    page,
    pageSize,
    sortKey,
    sortDir,
    debouncedSearch,
    dateFrom,
    dateTo,
    actorFilter,
    selectedCategories,
    selectedSeverities,
    selectedActionTypes,
    selectedStatuses,
  ]);

  // Execute query on filter change
  useEffect(() => {
    fetchAuditLogs();
  }, [fetchAuditLogs]);

  // Reset to page 1 on filter modifications
  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, dateFrom, dateTo, actorFilter, selectedCategories, selectedSeverities, selectedActionTypes, selectedStatuses]);

  // Verify chain on mount or on demand
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

  // Keyboard navigation
  useEffect(() => {
    const handleKeyDown = (e) => {
      // If typing in an input, don't trigger hotkeys
      if (['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target?.tagName)) {
        if (e.key === 'Escape') {
          e.target.blur();
        }
        return;
      }

      if (e.key === 'Escape') {
        setSelectedDrawerLog(null);
        setIsPresetModalOpen(false);
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

  // Target resource navigator
  const handleNavigateResource = (targetType, targetId) => {
    if (!targetType) return;
    const type = targetType.toLowerCase();
    if (type === 'user') navigate('/admin');
    else if (type === 'mission' || type === 'drone') navigate('/command');
    else if (type === 'evidence' || type === 'export') navigate('/admin');
  };

  // Preset Application
  const handleApplyPreset = (preset) => {
    const f = preset.filters || {};
    setSearch(f.q || '');
    setDateFrom(f.date_from || '');
    setDateTo(f.date_to || '');
    setActorFilter(f.actor_id || '');
    setSelectedCategories(f.category ? (Array.isArray(f.category) ? f.category : f.category.split(',')) : []);
    setSelectedSeverities(f.severity ? (Array.isArray(f.severity) ? f.severity : f.severity.split(',')) : []);
    setSelectedActionTypes(f.action_type ? (Array.isArray(f.action_type) ? f.action_type : f.action_type.split(',')) : []);
    setSelectedStatuses(f.status ? (Array.isArray(f.status) ? f.status : f.status.split(',')) : []);
  };

  // Exports
  const handleExportCSV = async () => {
    try {
      setExporting(true);
      const params = new URLSearchParams();
      if (debouncedSearch) params.set('q', debouncedSearch);
      if (dateFrom) params.set('date_from', dateFrom);
      if (dateTo) params.set('date_to', dateTo + 'T23:59:59');
      if (actorFilter) params.set('actor_id', actorFilter);
      if (selectedCategories.length) params.set('category', selectedCategories.join(','));
      if (selectedSeverities.length) params.set('severity', selectedSeverities.join(','));
      if (selectedActionTypes.length) params.set('action_type', selectedActionTypes.join(','));
      if (selectedStatuses.length) params.set('status', selectedStatuses.join(','));

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
      const params = new URLSearchParams();
      if (debouncedSearch) params.set('q', debouncedSearch);
      if (dateFrom) params.set('date_from', dateFrom);
      if (dateTo) params.set('date_to', dateTo + 'T23:59:59');
      if (actorFilter) params.set('actor_id', actorFilter);
      if (selectedCategories.length) params.set('category', selectedCategories.join(','));
      if (selectedSeverities.length) params.set('severity', selectedSeverities.join(','));
      if (selectedActionTypes.length) params.set('action_type', selectedActionTypes.join(','));
      if (selectedStatuses.length) params.set('status', selectedStatuses.join(','));

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
      const params = new URLSearchParams();
      if (debouncedSearch) params.set('q', debouncedSearch);
      if (dateFrom) params.set('date_from', dateFrom);
      if (dateTo) params.set('date_to', dateTo + 'T23:59:59');
      if (actorFilter) params.set('actor_id', actorFilter);
      if (selectedCategories.length) params.set('category', selectedCategories.join(','));
      if (selectedSeverities.length) params.set('severity', selectedSeverities.join(','));
      if (selectedActionTypes.length) params.set('action_type', selectedActionTypes.join(','));
      if (selectedStatuses.length) params.set('status', selectedStatuses.join(','));

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
    setActorFilter('');
    setSelectedCategories([]);
    setSelectedSeverities([]);
    setSelectedActionTypes([]);
    setSelectedStatuses([]);
  };

  const hasActiveFilters = Boolean(
    search || dateFrom || dateTo || actorFilter ||
    selectedCategories.length || selectedSeverities.length ||
    selectedActionTypes.length || selectedStatuses.length
  );

  return (
    <AppShell
      title="Audit Log Explorer"
      subtitle="Kerala Police DVAS · Cryptographically Verified & Immutable Audit Trail"
    >
      {/* ── Print Stylesheet ── */}
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
            font-size: 10px !important;
          }
        }
      `}</style>

      {/* ── Anomaly Alert Banner (if any anomalies detected) ── */}
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

      {/* ── Status & Integrity Context Bar ── */}
      <div
        style={{
          marginBottom: 20,
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
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <UserCheck size={16} style={{ color: 'var(--dvas-blue)' }} />
          <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--dvas-text)' }}>
            Logged Officer:&nbsp;
            <span style={{ fontFamily: 'JetBrains Mono, monospace', color: 'var(--dvas-blue)' }}>
              {authUser?.name || authUser?.email || 'Officer'} · {authUser?.role?.replace(/_/g, ' ')}
            </span>
          </span>
          <span style={{ fontSize: 10, padding: '2px 8px', borderRadius: 999, background: 'var(--dvas-elev)', color: 'var(--dvas-text-muted)', border: '1px solid var(--dvas-border)' }}>
            Jurisdiction: {authUser?.jurisdiction || 'State HQ'}
          </span>
        </div>

        {/* Chain Integrity Badge */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {chainStatus && (
            <div
              data-testid="chain-status-badge"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                padding: '4px 10px',
                borderRadius: 999,
                fontSize: 11,
                fontWeight: 600,
                fontFamily: 'JetBrains Mono, monospace',
                background: chainStatus.is_chain_valid ? 'var(--dvas-safe-bg)' : 'var(--dvas-crit-bg)',
                color: chainStatus.is_chain_valid ? 'var(--dvas-safe)' : 'var(--dvas-crit)',
                border: `1px solid ${chainStatus.is_chain_valid ? 'rgba(52,211,153,0.3)' : 'rgba(239,68,68,0.3)'}`,
              }}
            >
              {chainStatus.is_chain_valid ? <ShieldCheck size={13} /> : <ShieldAlert size={13} />}
              {chainStatus.is_chain_valid ? 'SHA-256 Chain Verified (OK)' : `Tamper Detected (${chainStatus.tampered_count} breaches)`}
              <span style={{ opacity: 0.6 }}>· {chainStatus.total_checked} rows</span>
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
              padding: '5px 10px',
              borderRadius: 8,
              background: 'var(--dvas-elev)',
              border: '1px solid var(--dvas-border)',
              color: 'var(--dvas-text)',
              cursor: verifyingChain ? 'wait' : 'pointer',
            }}
          >
            <RefreshCw size={12} className={verifyingChain ? 'animate-spin' : ''} />
            {verifyingChain ? 'Verifying…' : 'Verify Chain'}
          </button>
        </div>
      </div>

      {/* ── Summary KPI Cards ── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 14, marginBottom: 20 }}>
        <SummaryCard testId="card-total" icon={Activity} label="Total Events" value={summary.total_events} sub="Matching active filters" color="var(--dvas-blue)" />
        <SummaryCard testId="card-failed" icon={AlertTriangle} label="Failed Actions" value={summary.failed_actions} sub="Unauthorized or rejected" color="var(--dvas-crit)" />
        <SummaryCard testId="card-critical" icon={Shield} label="Critical Actions" value={summary.critical_events} sub="User & security modifications" color="var(--dvas-warn)" />
        <SummaryCard testId="card-warnings" icon={Clock} label="Warnings" value={summary.warnings} sub="Config & watchlist updates" color="#fbbf24" />
      </div>

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
        {/* Filter & Action Toolbar */}
        <div style={{ padding: 14, display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center', borderBottom: '1px solid var(--dvas-border)' }}>
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
                paddingLeft: 32,
                paddingRight: search ? 30 : 12,
                paddingBlock: 8,
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

          {/* Date Range Inputs */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <Calendar size={13} style={{ color: 'var(--dvas-text-muted)' }} />
            <input
              data-testid="date-from"
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
              style={{ fontSize: 12, paddingBlock: 6, paddingInline: 8, background: 'var(--dvas-elev)', border: '1px solid var(--dvas-border)', borderRadius: 8, color: 'var(--dvas-text)', outline: 'none' }}
            />
            <span style={{ color: 'var(--dvas-text-dim)', fontSize: 12 }}>–</span>
            <input
              data-testid="date-to"
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
              style={{ fontSize: 12, paddingBlock: 6, paddingInline: 8, background: 'var(--dvas-elev)', border: '1px solid var(--dvas-border)', borderRadius: 8, color: 'var(--dvas-text)', outline: 'none' }}
            />
          </div>

          {/* Actor Select Dropdown */}
          <div style={{ position: 'relative' }}>
            <select
              data-testid="actor-filter"
              value={actorFilter}
              onChange={(e) => setActorFilter(e.target.value)}
              style={{
                paddingInline: 12,
                paddingBlock: 8,
                fontSize: 12,
                background: 'var(--dvas-elev)',
                border: '1px solid var(--dvas-border)',
                borderRadius: 8,
                color: 'var(--dvas-text)',
                outline: 'none',
                minWidth: 150,
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
          </div>

          {/* Category Filter Dropdown */}
          <MultiSelect
            label="Category"
            options={categoriesList.length ? categoriesList : ['authentication', 'user_mgmt', 'operations', 'evidence', 'configuration', 'security']}
            selected={selectedCategories}
            onChange={setSelectedCategories}
            testId="category-filter"
          />

          {/* Action Type Filter Dropdown */}
          <MultiSelect
            label="Action Type"
            options={actionTypesList.length ? actionTypesList : ['create', 'update', 'delete', 'login', 'logout', 'export', 'read', 'system']}
            selected={selectedActionTypes}
            onChange={setSelectedActionTypes}
            testId="action-type-filter"
          />

          {/* Severity Quick Toggles */}
          <div style={{ display: 'flex', gap: 4 }}>
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
                    fontSize: 11,
                    fontWeight: 600,
                    padding: '6px 10px',
                    borderRadius: 8,
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

          {/* Presets Dropdown */}
          <div style={{ position: 'relative' }}>
            <select
              value=""
              onChange={(e) => {
                const found = presetsList.find((p) => p.id === e.target.value);
                if (found) handleApplyPreset(found);
              }}
              style={{
                paddingInline: 12,
                paddingBlock: 8,
                fontSize: 12,
                background: 'var(--dvas-elev)',
                border: '1px solid var(--dvas-border)',
                borderRadius: 8,
                color: 'var(--dvas-text)',
                outline: 'none',
                minWidth: 130,
                cursor: 'pointer',
              }}
            >
              <option value="" disabled>Presets ({presetsList.length})</option>
              {presetsList.map((p) => (
                <option key={p.id} value={p.id}>{p.name} {p.is_shared ? '(Shared)' : ''}</option>
              ))}
            </select>
          </div>

          {/* Action Buttons */}
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
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
                  padding: '7px 10px',
                  borderRadius: 8,
                  background: 'none',
                  border: '1px dashed var(--dvas-border)',
                  color: 'var(--dvas-text-muted)',
                  cursor: 'pointer',
                }}
              >
                <RotateCcw size={12} /> Clear
              </button>
            )}

            <button
              onClick={() => setIsPresetModalOpen(true)}
              title="Save current filters as preset"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 5,
                fontSize: 12,
                fontWeight: 600,
                padding: '7px 12px',
                borderRadius: 8,
                background: 'var(--dvas-elev)',
                border: '1px solid var(--dvas-border)',
                color: 'var(--dvas-text)',
                cursor: 'pointer',
              }}
            >
              <Bookmark size={13} /> Save Preset
            </button>

            <button
              data-testid="refresh-logs"
              onClick={fetchAuditLogs}
              title="Refresh logs from server"
              style={{
                width: 36,
                height: 36,
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
              <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            </button>

            {/* Section 65B PDF Export Button */}
            <button
              data-testid="export-65b-pdf"
              onClick={handleExportSection65BPDF}
              disabled={exporting || totalLogs === 0}
              title="Generate Official Indian Evidence Act Section 65B Compliance Certificate (PDF)"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                padding: '0 12px',
                height: 36,
                fontSize: 12,
                fontWeight: 700,
                borderRadius: 8,
                cursor: exporting ? 'wait' : 'pointer',
                background: 'rgba(56, 189, 248, 0.18)',
                color: 'var(--dvas-blue)',
                border: '1px solid var(--dvas-blue)',
                opacity: totalLogs === 0 ? 0.5 : 1,
              }}
            >
              <FileCheck size={14} /> Section 65B PDF
            </button>

            {/* CSV & JSON Export */}
            <button
              data-testid="export-csv"
              onClick={handleExportCSV}
              disabled={exporting || totalLogs === 0}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                padding: '0 12px',
                height: 36,
                fontSize: 12,
                fontWeight: 600,
                borderRadius: 8,
                cursor: exporting ? 'wait' : 'pointer',
                background: 'var(--dvas-blue)',
                color: '#fff',
                border: 'none',
                opacity: totalLogs === 0 ? 0.5 : 1,
              }}
            >
              <Download size={13} /> CSV
            </button>
            <button
              data-testid="export-json"
              onClick={handleExportJSON}
              disabled={exporting || totalLogs === 0}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                padding: '0 12px',
                height: 36,
                fontSize: 12,
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
              title="Print audit trail report"
              style={{
                width: 36,
                height: 36,
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
              <Printer size={14} />
            </button>
          </div>
        </div>

        {/* ── Table ── */}
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse', tableLayout: 'fixed' }}>
            <thead>
              <tr style={{ background: 'var(--dvas-surface-2)', borderBottom: '1px solid var(--dvas-border)' }}>
                <th style={{ width: '40px', padding: '10px 12px' }} />
                <th onClick={() => handleSort('ts')} style={{ width: '16%', padding: '10px 12px', textAlign: 'left', cursor: 'pointer' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: sortKey === 'ts' ? 'var(--dvas-blue)' : 'var(--dvas-text-muted)' }}>
                    Timestamp (IST)<SortIcon col="ts" />
                  </div>
                </th>
                <th onClick={() => handleSort('actor_email')} style={{ width: '17%', padding: '10px 12px', textAlign: 'left', cursor: 'pointer' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: sortKey === 'actor_email' ? 'var(--dvas-blue)' : 'var(--dvas-text-muted)' }}>
                    Actor<SortIcon col="actor_email" />
                  </div>
                </th>
                <th onClick={() => handleSort('action')} style={{ width: '18%', padding: '10px 12px', textAlign: 'left', cursor: 'pointer' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: sortKey === 'action' ? 'var(--dvas-blue)' : 'var(--dvas-text-muted)' }}>
                    Action<SortIcon col="action" />
                  </div>
                </th>
                <th onClick={() => handleSort('category')} style={{ width: '12%', padding: '10px 12px', textAlign: 'left', cursor: 'pointer' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: sortKey === 'category' ? 'var(--dvas-blue)' : 'var(--dvas-text-muted)' }}>
                    Category<SortIcon col="category" />
                  </div>
                </th>
                <th onClick={() => handleSort('target')} style={{ width: '14%', padding: '10px 12px', textAlign: 'left', cursor: 'pointer' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: sortKey === 'target' ? 'var(--dvas-blue)' : 'var(--dvas-text-muted)' }}>
                    Resource<SortIcon col="target" />
                  </div>
                </th>
                <th onClick={() => handleSort('severity')} style={{ width: '10%', padding: '10px 12px', textAlign: 'left', cursor: 'pointer' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: sortKey === 'severity' ? 'var(--dvas-blue)' : 'var(--dvas-text-muted)' }}>
                    Severity<SortIcon col="severity" />
                  </div>
                </th>
                <th onClick={() => handleSort('status')} style={{ width: '9%', padding: '10px 12px', textAlign: 'left', cursor: 'pointer' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: sortKey === 'status' ? 'var(--dvas-blue)' : 'var(--dvas-text-muted)' }}>
                    Status<SortIcon col="status" />
                  </div>
                </th>
                <th style={{ width: '60px', padding: '10px 12px', textAlign: 'right' }} />
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={9} style={{ padding: '60px 0', textAlign: 'center' }}>
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
                      <RefreshCw size={24} className="animate-spin" style={{ color: 'var(--dvas-blue)' }} />
                      <span style={{ fontSize: 12, color: 'var(--dvas-text-dim)' }}>Loading audit trail from server…</span>
                    </div>
                  </td>
                </tr>
              ) : logs.length === 0 ? (
                <tr>
                  <td colSpan={9} style={{ padding: '60px 0', textAlign: 'center', color: 'var(--dvas-text-dim)' }}>
                    <SlidersHorizontal size={28} style={{ opacity: 0.3, margin: '0 auto 8px' }} />
                    <div style={{ fontSize: 13 }}>No audit records found matching your filters.</div>
                  </td>
                </tr>
              ) : (
                logs.map((log, index) => {
                  const isExpanded = expandedRows.has(log.id);
                  const isRowFocused = index === selectedRowIndex;
                  const activeTab = rowTabMap[log.id] || 'overview';
                  const sBadge = statusBadge(log.status);
                  const sevBadge = severityBadge(log.severity);
                  const actBadge = actionTypeBadge(log.action_type);
                  const SIcon = sBadge.icon;

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
                        className="hover:bg-[color:var(--dvas-elev)]"
                      >
                        {/* Expand Toggle Chevron */}
                        <td style={{ padding: '10px 8px', textAlign: 'center' }}>
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

                        {/* Timestamp */}
                        <td style={{ padding: '10px 12px' }}>
                          <div style={{ fontSize: 11, fontFamily: 'JetBrains Mono, monospace', color: 'var(--dvas-text)', fontWeight: 500 }}>
                            {formatDateIST(log.ts)}
                          </div>
                          <div style={{ fontSize: 10, color: 'var(--dvas-text-dim)', fontFamily: 'JetBrains Mono, monospace' }}>
                            {formatRelativeTime(log.ts)}
                          </div>
                        </td>

                        {/* Actor */}
                        <td style={{ padding: '10px 12px' }}>
                          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--dvas-text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {log.actor_name || log.actor_email || 'System'}
                          </div>
                          <div style={{ fontSize: 10, color: 'var(--dvas-text-dim)', textTransform: 'capitalize' }}>
                            {log.actor_role?.replace(/_/g, ' ') || 'Internal'}
                          </div>
                        </td>

                        {/* Action */}
                        <td style={{ padding: '10px 12px' }}>
                          <span
                            style={{
                              display: 'inline-block',
                              padding: '2px 8px',
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
                        </td>

                        {/* Category */}
                        <td style={{ padding: '10px 12px', fontSize: 11, color: 'var(--dvas-text-muted)', textTransform: 'capitalize' }}>
                          {log.category?.replace(/_/g, ' ') || 'operations'}
                        </td>

                        {/* Resource Target */}
                        <td style={{ padding: '10px 12px' }}>
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
                        <td style={{ padding: '10px 12px' }}>
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

                        {/* Status */}
                        <td style={{ padding: '10px 12px' }}>
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

                        {/* Detail View Icon */}
                        <td style={{ padding: '10px 12px', textAlign: 'right' }}>
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
                              padding: 4,
                            }}
                          >
                            <Eye size={14} />
                          </button>
                        </td>
                      </tr>

                      {/* ── Inline Expanded Detail Row ── */}
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
                                  { k: 'related', l: 'Related Timeline Events', icon: Clock },
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

                              {/* Tab: Overview */}
                              {activeTab === 'overview' && (
                                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12, fontSize: 11 }}>
                                  <div style={{ background: 'var(--dvas-surface)', padding: 12, borderRadius: 8, border: '1px solid var(--dvas-border)' }}>
                                    <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', color: 'var(--dvas-text-dim)', marginBottom: 4 }}>Actor & Identity</div>
                                    <div style={{ color: 'var(--dvas-text)', fontWeight: 600 }}>{log.actor_name || '—'}</div>
                                    <div style={{ color: 'var(--dvas-text-muted)', fontFamily: 'JetBrains Mono, monospace' }}>{log.actor_email || '—'}</div>
                                    <div style={{ color: 'var(--dvas-text-dim)', marginTop: 4 }}>Role: {log.actor_role || '—'}</div>
                                    <div style={{ color: 'var(--dvas-text-dim)' }}>Session ID: {log.actor_session_id || '—'}</div>
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
                                    <div style={{ color: 'var(--dvas-text-muted)' }}>Endpoint: {log.source_endpoint || '—'}</div>
                                    <div style={{ color: 'var(--dvas-text-dim)', fontFamily: 'JetBrains Mono, monospace' }}>Req ID: {log.request_id || '—'}</div>
                                    <div style={{ color: 'var(--dvas-text-dim)', fontFamily: 'JetBrains Mono, monospace' }}>Corr ID: {log.correlation_id || '—'}</div>
                                  </div>

                                  <div style={{ background: 'var(--dvas-surface)', padding: 12, borderRadius: 8, border: '1px solid var(--dvas-border)' }}>
                                    <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', color: 'var(--dvas-text-dim)', marginBottom: 4 }}>Cryptographic Hash Chain</div>
                                    <div style={{ fontSize: 10, color: 'var(--dvas-text-dim)', fontFamily: 'JetBrains Mono, monospace', wordBreak: 'break-all' }}>
                                      SHA-256: {log.hash || '—'}
                                    </div>
                                    <div style={{ fontSize: 10, color: 'var(--dvas-text-dim)', fontFamily: 'JetBrains Mono, monospace', wordBreak: 'break-all', marginTop: 4 }}>
                                      Prev: {log.prev_hash || '—'}
                                    </div>
                                  </div>
                                </div>
                              )}

                              {/* Tab: Diff */}
                              {activeTab === 'diff' && (
                                <DiffViewer before={log.before_values} after={log.after_values} />
                              )}

                              {/* Tab: Payload */}
                              {activeTab === 'payload' && (
                                <JsonViewer data={log} />
                              )}

                              {/* Tab: Related Events */}
                              {activeTab === 'related' && (
                                <RelatedEventsTimeline
                                  correlationId={log.correlation_id}
                                  sessionId={log.actor_session_id}
                                  currentLogId={log.id}
                                  onSelectLog={(selected) => setSelectedDrawerLog(selected)}
                                />
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

      {/* ── Slide-Out Detail Drawer ── */}
      {selectedDrawerLog && (
        <div
          onClick={() => setSelectedDrawerLog(null)}
          style={{ position: 'fixed', inset: 0, zIndex: 60, display: 'flex', background: 'rgba(15,23,42,0.65)' }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            className="dvas-fade-in"
            style={{
              marginLeft: 'auto',
              height: '100%',
              display: 'flex',
              flexDirection: 'column',
              width: 540,
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
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', padding: '14px 20px', gap: 12, borderBottom: '1px solid var(--dvas-border)', background: 'var(--dvas-surface-2)', flexShrink: 0 }}>
              <div>
                <div style={{ fontSize: 9, fontWeight: 600, textTransform: 'uppercase', color: 'var(--dvas-text-dim)', marginBottom: 2 }}>Timestamp (IST)</div>
                <div style={{ fontSize: 11, fontFamily: 'JetBrains Mono, monospace', color: 'var(--dvas-text)' }}>{formatDateIST(selectedDrawerLog.ts)}</div>
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

            {/* Body */}
            <div style={{ flex: 1, overflow: 'auto', padding: 20 }}>
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', color: 'var(--dvas-text-dim)', marginBottom: 8 }}>State Modification Diff</div>
                <DiffViewer before={selectedDrawerLog.before_values} after={selectedDrawerLog.after_values} />
              </div>

              <div>
                <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', color: 'var(--dvas-text-dim)', marginBottom: 8 }}>Full JSON Event Data</div>
                <JsonViewer data={selectedDrawerLog} />
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Save Filter Preset Modal ── */}
      <SavePresetModal
        isOpen={isPresetModalOpen}
        onClose={() => setIsPresetModalOpen(false)}
        currentFilters={{
          q: search,
          date_from: dateFrom,
          date_to: dateTo,
          actor_id: actorFilter,
          category: selectedCategories,
          severity: selectedSeverities,
          action_type: selectedActionTypes,
          status: selectedStatuses,
        }}
        onSaved={async () => {
          try {
            const { data } = await api.get('/audit-presets');
            setPresetsList(data || []);
          } catch (_) {}
        }}
      />
    </AppShell>
  );
}
