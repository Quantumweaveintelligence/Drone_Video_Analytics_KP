import React, { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmtIST } from "@/lib/ist";
import { X } from "lucide-react";

/**
 * One modal for every evidence action (evidence plan U1) instead of window.prompt():
 * shows the evidence it acts on, validates the fields (reasons ≥ 20 characters) and
 * reports the server's answer in place.
 */
const MIN_REASON = 20;

const ACTIONS = {
  legal_hold: {
    title: "Place legal hold",
    fields: [
      { name: "category", label: "Reason", type: "select", options: ["Court order", "Under investigation", "Complaint / RTI", "Other"] },
      { name: "text", label: "Details (case / order reference)", type: "textarea", min: MIN_REASON },
    ],
    submit: (ev, v) => api.post(`/evidence/${ev.id}/legal-hold`, { reason: `${v.category}: ${v.text}` }),
  },
  release_hold: {
    title: "Release legal hold",
    fields: [{ name: "text", label: "Why is the hold no longer needed?", type: "textarea", min: MIN_REASON }],
    submit: (ev, v) => api.delete(`/evidence/${ev.id}/legal-hold`, { data: { reason: v.text } }),
  },
  direct_export: {
    title: "Direct export (you request and approve it)",
    note: "The certificate and the bundle will say this export was self-approved.",
    fields: [
      { name: "case_number", label: "Case / crime number", type: "text", min: 1 },
      { name: "justification", label: "Purpose of the export", type: "textarea", min: MIN_REASON },
    ],
    submit: (ev, v) => api.post(`/evidence/${ev.id}/direct-export`, { case_number: v.case_number, justification: v.justification }),
  },
  request_export: {
    title: "Request export",
    fields: [
      { name: "case_number", label: "Case / crime number (if known)", type: "text", min: 0 },
      { name: "justification", label: "Purpose of the export", type: "textarea", min: MIN_REASON },
    ],
    submit: (ev, v) => api.post("/exports", { evidence_id: ev.id, justification: v.justification, case_number: v.case_number || undefined }),
  },
  dispose: {
    title: "Dispose of evidence",
    note: "The record and its audit trail are kept; stored images are deleted only after their retention ends.",
    fields: [
      { name: "category", label: "Reason", type: "select", options: ["Duplicate capture", "False detection", "Retention period over", "Other"] },
      { name: "text", label: "Details", type: "textarea", min: MIN_REASON },
    ],
    submit: (ev, v) => api.delete(`/evidence/${ev.id}`, { data: { reason: `${v.category}: ${v.text}` } }),
  },
  add_to_case: {
    title: "Add to case",
    fields: [
      { name: "case_id", label: "Existing case", type: "case" },
      { name: "case_number", label: "…or new case number", type: "text", min: 0 },
    ],
    submit: async (ev, v) => {
      let caseId = v.case_id;
      if (!caseId) {
        if (!v.case_number) throw new Error("Choose a case or enter a new case number");
        caseId = (await api.post("/cases", { case_number: v.case_number })).data.id;
      }
      return api.post(`/cases/${caseId}/evidence`, { evidence_id: ev.id });
    },
  },
};

export default function ActionModal({ action, onClose, onDone }) {
  const spec = action ? ACTIONS[action.kind] : null;
  const [values, setValues] = useState({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [cases, setCases] = useState([]);

  useEffect(() => {
    if (!spec) return;
    const init = {};
    spec.fields.forEach((f) => { init[f.name] = f.type === "select" ? f.options[0] : ""; });
    setValues(init);
    setError("");
    if (spec.fields.some((f) => f.type === "case")) {
      api.get("/cases").then((r) => setCases((r.data || []).filter((c) => c.status === "open"))).catch(() => setCases([]));
    }
  }, [action, spec]);

  if (!spec) return null;
  const ev = action.evidence;

  const problems = spec.fields
    .filter((f) => f.min && (values[f.name] || "").trim().length < f.min)
    .map((f) => (f.min > 1 ? `${f.label}: at least ${f.min} characters` : `${f.label} is required`));

  const submit = async () => {
    if (problems.length) { setError(problems.join(" · ")); return; }
    setBusy(true); setError("");
    try {
      const trimmed = Object.fromEntries(Object.entries(values).map(([k, v]) => [k, typeof v === "string" ? v.trim() : v]));
      const res = await spec.submit(ev, trimmed);
      if (res?.data?.bundle_error) setError(`Done, but the bundle could not be built: ${res.data.bundle_error}`);
      else { onDone?.(); onClose(); }
    } catch (e) {
      setError(e.response?.data?.detail ? String(e.response.data.detail) : e.message || "Failed");
    } finally { setBusy(false); }
  };

  const field = "w-full bg-slate-900 border border-slate-700 focus:border-sky-500 outline-none p-2 text-sm rounded";
  return (
    <div className="fixed inset-0 bg-black/80 z-[60] flex items-center justify-center p-6" onClick={onClose}>
      <div role="dialog" aria-label={spec.title} className="border border-slate-700 bg-slate-950 max-w-md w-full p-4 rounded-xl space-y-3"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <div className="text-sm font-semibold text-slate-100">{spec.title}</div>
          <button onClick={onClose} className="text-slate-400 hover:text-white" aria-label="Close"><X size={16} /></button>
        </div>
        <div className="text-xs border border-slate-800 rounded p-2 text-slate-300">
          <div className="font-medium">{ev.label || ev.id}</div>
          <div className="mono text-[10px] text-slate-500">{ev.id} · {ev.event_type} · {fmtIST(ev.capture_ts || ev.ts)}</div>
        </div>
        {spec.note && <div className="text-[11px] text-amber-300">{spec.note}</div>}
        {spec.fields.map((f) => (
          <label key={f.name} className="block text-xs text-slate-400 space-y-1">
            <span>{f.label}</span>
            {f.type === "select" && (
              <select className={field} value={values[f.name] || ""} onChange={(e) => setValues({ ...values, [f.name]: e.target.value })}>
                {f.options.map((o) => <option key={o}>{o}</option>)}
              </select>)}
            {f.type === "case" && (
              <select className={field} value={values[f.name] || ""} onChange={(e) => setValues({ ...values, [f.name]: e.target.value })}>
                <option value="">— none —</option>
                {cases.map((c) => <option key={c.id} value={c.id}>{c.case_number}{c.title ? ` — ${c.title}` : ""}</option>)}
              </select>)}
            {f.type === "text" && (
              <input className={field} value={values[f.name] || ""} onChange={(e) => setValues({ ...values, [f.name]: e.target.value })} />)}
            {f.type === "textarea" && (<>
              <textarea rows={3} className={field} value={values[f.name] || ""} onChange={(e) => setValues({ ...values, [f.name]: e.target.value })} />
              {f.min > 1 && <span className="text-[10px] text-slate-500">{(values[f.name] || "").trim().length}/{f.min} minimum</span>}
            </>)}
          </label>
        ))}
        {error && <div className="text-xs text-red-400">{error}</div>}
        <div className="flex justify-end gap-2 text-xs">
          <button onClick={onClose} className="px-3 py-1.5 border border-slate-700 text-slate-300 rounded">Cancel</button>
          <button onClick={submit} disabled={busy} className="px-3 py-1.5 bg-sky-600 hover:bg-sky-500 text-white rounded font-semibold disabled:opacity-50">
            {busy ? "Working…" : "Confirm"}
          </button>
        </div>
      </div>
    </div>
  );
}
