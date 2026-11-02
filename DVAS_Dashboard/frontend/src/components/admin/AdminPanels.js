// Admin page tabs backed by /api/admin/* and the audit / evidence hold APIs
// (ADM-01, 03-07, 09). Every value shown here comes from the server.
import { useCallback, useEffect, useState } from "react";
import { Key, Folder, RefreshCw, LogOut } from "lucide-react";
import { api, apiError, ROLE_LABELS } from "@/lib/api";
import { fmtIST } from "@/lib/ist";

const th = "text-left px-3 py-2 dvas-label border-b border-slate-800";
const td = "px-3 py-2 border-b border-slate-800/60";

function useLoad(fn, deps) {
  const [state, setState] = useState({ data: null, error: "", loading: true });
  const load = useCallback(async () => {
    setState(s => ({ ...s, loading: true }));
    try {
      setState({ data: await fn(), error: "", loading: false });
    } catch (e) {
      setState({ data: null, error: apiError(e, "Could not load"), loading: false });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => { load(); }, [load]);
  return [state, load];
}

const ErrorLine = ({ text }) => text ? <div className="mono text-[11px] text-red-400 p-3">{text}</div> : null;

const msToIso = (ms) => (ms ? new Date(ms).toISOString() : null);

// ── ADM-01: Keycloak sessions ───────────────────────────────────────────────
export function SessionsPanel() {
  const [{ data, error, loading }, reload] = useLoad(async () => (await api.get("/admin/sessions")).data, []);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");

  const revoke = async (s) => {
    if (!window.confirm(`End the session of ${s.email} from ${s.ip || "unknown IP"}?`)) return;
    setBusy(s.session_id); setMsg("");
    try {
      await api.delete(`/admin/sessions/${encodeURIComponent(s.session_id)}`);
      setMsg(`Session of ${s.email} ended.`);
      reload();
    } catch (e) { setMsg(apiError(e)); } finally { setBusy(""); }
  };
  const logoutAll = async (s) => {
    if (!window.confirm(`Sign ${s.email} out of every session?`)) return;
    setBusy(s.user_id); setMsg("");
    try {
      await api.post(`/admin/users/${encodeURIComponent(s.user_id)}/logout`);
      setMsg(`${s.email} signed out everywhere.`);
      reload();
    } catch (e) { setMsg(apiError(e)); } finally { setBusy(""); }
  };

  const rows = Array.isArray(data) ? data : [];
  return (
    <div className="border border-slate-800 bg-slate-900">
      <div className="p-3 border-b border-slate-800 text-sm flex items-center gap-2">
        <Key size={16} className="text-sky-400" /> Active Sessions
        <span className="mono text-[11px] text-slate-500 ml-2">{loading ? "loading…" : `${rows.length} session(s) · Keycloak`}</span>
        <button onClick={reload} className="ml-auto text-xs px-2 py-1 border border-slate-700 hover:border-sky-500 flex items-center gap-1"><RefreshCw size={11} /> Refresh</button>
      </div>
      <ErrorLine text={error} />
      {msg && <div className="mono text-[11px] text-sky-300 px-3 pt-2">{msg}</div>}
      <table className="w-full text-xs mono">
        <thead className="bg-slate-950/60"><tr>{["User", "Role", "Started", "Last activity", "IP", "Clients", "Action"].map(h => <th key={h} className={th} style={{ fontSize: 10 }}>{h}</th>)}</tr></thead>
        <tbody>
          {rows.map(s => (
            <tr key={s.session_id} className="hover:bg-slate-800/40">
              <td className={td}>{s.email}<div className="text-[10px] text-slate-500">{s.name}</div></td>
              <td className={td}>{ROLE_LABELS[s.role] || s.role || "—"}</td>
              <td className={`${td} text-slate-400`}>{fmtIST(msToIso(s.started_ms))}</td>
              <td className={`${td} text-slate-400`}>{fmtIST(msToIso(s.last_access_ms))}</td>
              <td className={`${td} text-slate-400`}>{s.ip || "—"}</td>
              <td className={`${td} text-slate-500`}>{(s.clients || []).join(", ") || "—"}</td>
              <td className={td}>
                <div className="flex gap-1">
                  <button data-testid={`revoke-${s.session_id}`} disabled={!!busy} onClick={() => revoke(s)}
                    className="px-2 py-1 border border-red-600/60 text-red-300 hover:bg-red-500/10 disabled:opacity-40">Revoke</button>
                  <button disabled={!!busy} onClick={() => logoutAll(s)} title="End every session of this user"
                    className="px-2 py-1 border border-slate-700 text-slate-300 hover:border-red-500 disabled:opacity-40 flex items-center gap-1"><LogOut size={11} /> All</button>
                </div>
              </td>
            </tr>
          ))}
          {!loading && !error && rows.length === 0 && <tr><td colSpan={7} className="p-6 text-center text-slate-500">No active sessions.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

const fmtDuration = (s) => {
  if (s == null) return "—";
  if (s % 3600 === 0) return `${s / 3600} h`;
  if (s % 60 === 0) return `${s / 60} min`;
  return `${s} s`;
};

// ── ADM-05: policy actually enforced ────────────────────────────────────────
export function PolicyPanel() {
  const [{ data, error }] = useLoad(async () => (await api.get("/admin/policy")).data, []);
  if (error) return <ErrorLine text={error} />;
  if (!data) return <div className="text-xs text-slate-500 p-3">Loading…</div>;
  const s = data.settings || {};
  const idn = data.identity || {};
  const card = (label, value, unit, note) => (
    <div className="border border-slate-800 bg-slate-900 p-4">
      <div className="dvas-label mb-2">{label}</div>
      <div className="mono text-2xl text-sky-400">{value ?? "—"} {unit && <span className="text-xs text-slate-500">{unit}</span>}</div>
      {note && <div className="mono text-[10px] text-slate-500 mt-1">{note}</div>}
    </div>
  );
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      {card("Retention · Evidence", s.evidence_retention_days, "days", "new evidence; vault object lock")}
      {card("Retention · Audit log", s.audit_retention_days, "days", "statutory floor 365 days")}
      {card("Retention · Report PDFs", s.report_retention_days, "days", `job runs daily at ${s.retention_job_time_ist || "—"} IST`)}
      {card("Access token lifetime", fmtDuration(idn.access_token_lifespan_s), "", idn.source)}
      {card("Session idle timeout", fmtDuration(idn.sso_session_idle_s), "", "Keycloak realm")}
      {card("Session maximum", fmtDuration(idn.sso_session_max_s), "", "Keycloak realm")}
      <div className="col-span-1 md:col-span-3 border border-slate-800 bg-slate-900 p-4">
        <div className="dvas-label mb-3">Access rules enforced by the backend</div>
        <ul className="text-xs mono space-y-2 text-slate-300">
          {(data.rules || []).map(r => (
            <li key={r.name}>• <span className="text-sky-400">{r.name}</span> — {r.rule}</li>
          ))}
        </ul>
        <div className="mono text-[10px] text-slate-500 mt-3">
          These rules are part of the code and change only with a release. Token and session lifetimes are set in the Keycloak admin console. Transport: {data.transport?.tls}.
        </div>
      </div>
    </div>
  );
}

// ── ADM-06: editable settings with a real diff and save ─────────────────────
export function SettingsPanel() {
  const [{ data, error }, reload] = useLoad(async () => (await api.get("/admin/settings")).data, []);
  const [draft, setDraft] = useState({});
  const [pending, setPending] = useState(null);
  const [msg, setMsg] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (data) setDraft(Object.fromEntries(data.settings.map(s => [s.key, s.value])));
  }, [data]);

  if (error) return <ErrorLine text={error} />;
  if (!data) return <div className="text-xs text-slate-500 p-3">Loading…</div>;

  const changes = data.settings.filter(s => String(draft[s.key]) !== String(s.value));
  const save = async () => {
    setSaving(true); setMsg("");
    try {
      const body = Object.fromEntries(changes.map(s => [s.key, s.type === "int" ? Number(draft[s.key]) : draft[s.key]]));
      const res = await api.put("/admin/settings", body);
      setMsg(`Saved ${Object.keys(res.data.changed || {}).length} change(s). Recorded in the audit log.`);
      setPending(null);
      reload();
    } catch (e) {
      setMsg(apiError(e));
      setPending(null);
    } finally { setSaving(false); }
  };

  return (
    <div className="border border-slate-800 bg-slate-900 p-4 space-y-4">
      <div className="dvas-label">System settings {data.editable ? "" : "(read-only for your role)"}</div>
      {data.settings.map(s => (
        <label key={s.key} className="block">
          <div className="dvas-label mb-1">{s.label}</div>
          <input
            data-testid={`setting-${s.key}`}
            disabled={!data.editable}
            type={s.type === "int" ? "number" : s.type === "time" ? "time" : "text"}
            min={s.min ?? undefined} max={s.max ?? undefined}
            value={draft[s.key] ?? ""}
            onChange={e => setDraft({ ...draft, [s.key]: e.target.value })}
            className="bg-slate-950 border border-slate-700 px-2 py-2 text-xs mono w-40 disabled:opacity-60"
          />
          <div className="mono text-[10px] text-slate-500 mt-1">
            {s.min != null && `Range ${s.min}–${s.max}. `}Enforced by: {s.enforced_by}
          </div>
        </label>
      ))}
      <div className="mono text-[10px] text-slate-500">TLS is terminated at the RunPod / Cloudflare edge and token lifetimes live in Keycloak; neither is set here.</div>
      {data.editable && (
        <button data-testid="sysconf-save" disabled={changes.length === 0}
          onClick={() => setPending(changes.map(s => `${s.key}: ${s.value} → ${draft[s.key]}`).join("\n"))}
          className="px-3 py-1.5 bg-sky-600 hover:bg-sky-500 disabled:opacity-40 text-white text-xs">
          Save {changes.length ? `(${changes.length} change${changes.length > 1 ? "s" : ""})` : ""}
        </button>
      )}
      {msg && <div className="mono text-[11px] text-sky-300">{msg}</div>}

      {pending && (
        <div className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-6" onClick={() => setPending(null)}>
          <div className="border border-slate-700 bg-slate-950 max-w-md w-full p-4" onClick={e => e.stopPropagation()}>
            <div className="text-sm mb-2">Save system settings</div>
            <div className="dvas-label mb-2">Before → After</div>
            <pre className="mono text-[11px] bg-slate-900 border border-slate-800 p-3 whitespace-pre-wrap">{pending}</pre>
            <div className="mt-3 flex justify-end gap-2 text-xs">
              <button onClick={() => setPending(null)} className="px-3 py-1.5 border border-slate-700">Cancel</button>
              <button data-testid="confirm-save" disabled={saving} onClick={save} className="px-3 py-1.5 bg-sky-600 hover:bg-sky-500 disabled:opacity-50">
                {saving ? "Saving…" : "Confirm & save"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── ADM-03: evidence and audit-entry legal holds ────────────────────────────
export function HoldsPanel({ canReleaseEvidence }) {
  const [{ data, error, loading }, reload] = useLoad(async () => {
    const [ev, au] = await Promise.all([
      api.get("/evidence", { params: { legal_hold_only: true, limit: 200 } }),
      api.get("/audit-logs", { params: { is_legal_hold: "true", page_size: 200 } }),
    ]);
    const evidence = (ev.data?.items || []).map(e => ({
      kind: "evidence", id: e.id, label: e.label || e.event_type || e.id,
      ref: e.legal_hold_reason, by: e.legal_hold_by, since: e.legal_hold_at,
    }));
    const audit = (au.data?.items || []).map(l => ({
      kind: "audit", id: l.id, label: `${l.action} · ${l.target_name || l.target || ""}`,
      ref: `Case ${l.legal_hold?.case_number || "—"} · ${l.legal_hold?.hold_reason || ""}`,
      by: l.legal_hold?.placed_by_name, since: l.legal_hold?.placed_at,
      authority: l.legal_hold?.legal_authority,
    }));
    return [...evidence, ...audit];
  }, []);
  const [msg, setMsg] = useState("");

  const release = async (h) => {
    const reason = window.prompt(`Reason for releasing the hold on ${h.kind} ${h.id}:`);
    if (!reason || !reason.trim()) return;
    setMsg("");
    try {
      if (h.kind === "evidence") {
        await api.delete(`/evidence/${encodeURIComponent(h.id)}/legal-hold`, { data: { reason: reason.trim() } });
      } else {
        await api.delete(`/audit-logs/${encodeURIComponent(h.id)}/legal-hold`, { data: { release_reason: reason.trim() } });
      }
      setMsg(`Hold on ${h.kind} ${h.id} released.`);
      reload();
    } catch (e) { setMsg(apiError(e)); }
  };

  const rows = data || [];
  return (
    <div className="border border-slate-800 bg-slate-900 p-4">
      <div className="dvas-label mb-3 flex items-center gap-2"><Folder size={12} /> Active Legal Holds
        <span className="mono text-[10px] text-slate-500 normal-case">{loading ? "loading…" : `${rows.length} active`}</span>
      </div>
      <ErrorLine text={error} />
      {msg && <div className="mono text-[11px] text-sky-300 mb-2">{msg}</div>}
      <ul className="divide-y divide-slate-800 -mx-4">
        {rows.map(h => (
          <li key={`${h.kind}-${h.id}`} className="px-4 py-3 flex items-center justify-between text-xs mono">
            <div className="min-w-0">
              <div className="text-slate-100"><span className="text-[10px] uppercase text-slate-500 mr-2">{h.kind}</span>{h.label}</div>
              <div className="text-slate-500 text-[11px] mt-0.5 truncate">{h.ref || "—"}{h.authority ? ` · ${h.authority}` : ""}</div>
              <div className="text-slate-600 text-[10px]">{h.by || "—"}</div>
            </div>
            <div className="text-right shrink-0 ml-3">
              <div className="text-slate-400">since {fmtIST(h.since)}</div>
              <div className="flex gap-1 mt-1 justify-end">
                <span className="px-1.5 py-0.5 border border-red-500/50 bg-red-500/10 text-red-400 text-[10px] uppercase">Hold Active</span>
                {(h.kind === "audit" || canReleaseEvidence) && (
                  <button data-testid={`release-${h.id}`} onClick={() => release(h)} className="text-[10px] px-1.5 py-0.5 border border-slate-700 hover:border-sky-500">Release</button>
                )}
              </div>
            </div>
          </li>
        ))}
        {!loading && !error && rows.length === 0 && <li className="px-4 py-6 text-center text-slate-500 text-xs">No active legal holds.</li>}
      </ul>
    </div>
  );
}

// ── ADM-07: computed compliance cards ───────────────────────────────────────
export function CompliancePanel() {
  const [{ data, error }, reload] = useLoad(async () => (await api.get("/admin/compliance")).data, []);
  if (error) return <ErrorLine text={error} />;
  if (!data) return <div className="text-xs text-slate-500 p-3">Loading…</div>;
  return (
    <div>
      <div className="flex items-center mb-2">
        <span className="mono text-[10px] text-slate-500">Computed {fmtIST(data.generated_at)}</span>
        <button onClick={reload} className="ml-auto text-xs px-2 py-1 border border-slate-700 hover:border-sky-500 flex items-center gap-1"><RefreshCw size={11} /> Refresh</button>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {data.cards.map(c => (
          <div key={c.key} className="border border-slate-800 bg-slate-900 p-4">
            <div className="dvas-label">{c.label}</div>
            <div className="mono text-3xl mt-2 text-sky-400">{c.value}</div>
            <div className="mono text-[11px] text-slate-400 mt-1">{c.note}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── ADM-09: whole-log aggregate (grouped in the database) ───────────────────
export function AuditAggregate() {
  const [days, setDays] = useState(30);
  const [{ data, error, loading }] = useLoad(async () => (await api.get("/audit-logs/aggregate", { params: { days } })).data, [days]);
  const table = (title, rows, tone) => (
    <div className="border border-slate-800 bg-slate-900/60 p-3">
      <div className="dvas-label mb-2 text-[10px]">{title}</div>
      <table className="w-full text-xs mono">
        <tbody>{(rows || []).map(r => (
          <tr key={r.key} className="hover:bg-slate-800/40">
            <td className="px-2 py-1 border-b border-slate-800/40 text-slate-300">{r.key}</td>
            <td className={`px-2 py-1 border-b border-slate-800/40 text-right ${tone}`}>{r.count}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
  return (
    <div className="p-4 space-y-3">
      <div className="flex items-center gap-2 text-xs">
        <span className="dvas-label text-[10px]">Window</span>
        {[7, 30, 90].map(d => (
          <button key={d} onClick={() => setDays(d)} className={`px-2 py-0.5 border ${days === d ? "border-sky-500 text-white" : "border-slate-700 text-slate-400"}`}>{d} days</button>
        ))}
        <span className="mono text-[10px] text-slate-500 ml-2">{loading ? "loading…" : data ? `${data.total} entries in the whole log` : ""}</span>
      </div>
      <ErrorLine text={error} />
      {data && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {table("Events by role", data.by_role, "text-sky-400")}
          {table("Events by action", data.by_action, "text-amber-400")}
          {table("Events by category", data.by_category, "text-emerald-400")}
        </div>
      )}
    </div>
  );
}
