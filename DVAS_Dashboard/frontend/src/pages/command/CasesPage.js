import React, { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/context/AuthContext";
import { api, downloadSigned } from "@/lib/api";
import { fmtIST } from "@/lib/ist";
import { Briefcase, Plus, Download, Send, Trash2 } from "lucide-react";

/** Case workspace (evidence plan U1/E4): cases, their evidence, export requests and bundles. */
export default function CasesPage() {
  const { user } = useAuth();
  const isSenior = user?.role === "senior_officer";
  const [cases, setCases] = useState([]);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [form, setForm] = useState({ case_number: "", fir_number: "", police_station: "", title: "" });
  const [justification, setJustification] = useState("");
  const [error, setError] = useState("");

  const loadCases = useCallback(async () => {
    try { setCases((await api.get("/cases")).data || []); } catch { setCases([]); }
  }, []);
  const loadDetail = useCallback(async (id) => {
    if (!id) return;
    try { setDetail((await api.get(`/cases/${id}`)).data); } catch (e) { setError(e.response?.data?.detail || "Failed"); }
  }, []);

  useEffect(() => { loadCases(); }, [loadCases]);
  useEffect(() => { loadDetail(selected); }, [selected, loadDetail]);

  const run = async (fn) => {
    setError("");
    try { await fn(); await loadCases(); await loadDetail(selected); }
    catch (e) { setError(e.response?.data?.detail ? String(e.response.data.detail) : "Failed"); }
  };

  const createCase = () => run(async () => {
    if (!form.case_number.trim()) throw { response: { data: { detail: "Case number is required" } } };
    const c = (await api.post("/cases", form)).data;
    setForm({ case_number: "", fir_number: "", police_station: "", title: "" });
    setSelected(c.id);
  });

  const requestExport = () => run(async () => {
    if (justification.trim().length < 20) throw { response: { data: { detail: "Purpose: at least 20 characters" } } };
    await api.post("/exports", { case_id: selected, justification: justification.trim() });
    setJustification("");
  });

  const box = { background: "var(--dvas-surface)", border: "1px solid var(--dvas-border)" };
  const input = "bg-slate-900 border border-slate-700 text-xs px-2 py-1 rounded text-slate-200";
  const c = detail?.case;
  const locked = (detail?.exports || []).some((x) => ["pending", "approved"].includes(x.status));

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div className="rounded-lg p-3 space-y-3" style={box}>
        <div className="text-sm font-semibold flex items-center gap-2" style={{ color: "var(--dvas-text)" }}><Briefcase size={15} /> Cases</div>
        <div className="space-y-1">
          {cases.length === 0 && <div className="text-xs text-slate-500">No cases yet.</div>}
          {cases.map((x) => (
            <button key={x.id} onClick={() => setSelected(x.id)}
              className={`w-full text-left text-xs px-2 py-1.5 rounded border ${selected === x.id ? "border-sky-500 bg-sky-500/10" : "border-slate-800"}`}>
              <div className="text-slate-100">{x.case_number} <span className="text-slate-500">· {x.status}</span></div>
              <div className="text-[10px] text-slate-500">{x.title || x.police_station || x.jurisdiction || "—"}</div>
            </button>))}
        </div>
        <div className="border-t border-slate-800 pt-2 space-y-1.5">
          <div className="text-xs text-slate-400">New case</div>
          {[["case_number", "Case / crime number *"], ["fir_number", "FIR number"], ["police_station", "Police station"], ["title", "Title"]].map(([k, l]) => (
            <input key={k} placeholder={l} value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} className={`${input} w-full`} />))}
          <button onClick={createCase} className="px-2 py-1 border border-sky-600/60 text-sky-300 text-xs rounded flex items-center gap-1"><Plus size={11} /> Create</button>
        </div>
      </div>

      <div className="lg:col-span-2 rounded-lg p-3 space-y-3" style={box}>
        {error && <div className="text-xs text-red-400">{error}</div>}
        {!c && <div className="text-xs text-slate-500">Select a case. Add evidence to a case from Evidence Review (“Add to case”).</div>}
        {c && (<>
          <div>
            <div className="text-sm font-semibold text-slate-100">{c.case_number} {c.title ? `— ${c.title}` : ""}</div>
            <div className="text-[11px] text-slate-400">FIR {c.fir_number || "—"} · {c.police_station || "—"} · {c.jurisdiction || "—"} · opened {fmtIST(c.created_at)} by {c.created_by}</div>
            {locked && <div className="text-[11px] text-amber-300 mt-1">This case has a pending or approved export, so its evidence list is frozen.</div>}
          </div>
          <table className="w-full text-xs">
            <thead><tr className="text-left text-slate-400"><th className="py-1">Evidence</th><th>Captured</th><th>Integrity</th><th>Record SHA-256</th><th></th></tr></thead>
            <tbody>
              {detail.evidence.length === 0 && <tr><td colSpan={5} className="py-2 text-slate-500">No evidence in this case yet.</td></tr>}
              {detail.evidence.map((ev) => (
                <tr key={ev.id} className="border-t border-slate-800/60">
                  <td className="py-1 text-slate-200">{ev.id}<div className="text-[10px] text-slate-500">{ev.event_type}</div></td>
                  <td className="text-slate-400">{fmtIST(ev.capture_ts || ev.ts)}</td>
                  <td className={ev.integrity === "ok" ? "text-emerald-400" : "text-amber-400"}>{ev.integrity}</td>
                  <td className="mono text-[10px] text-slate-500" title={ev.record_sha256 || ""}>{(ev.record_sha256 || "").slice(0, 16)}…</td>
                  <td className="text-right">{!locked && c.status === "open" && (
                    <button title="Remove from case" onClick={() => run(() => api.delete(`/cases/${c.id}/evidence/${ev.id}`))} className="text-slate-500 hover:text-red-400"><Trash2 size={12} /></button>)}</td>
                </tr>))}
            </tbody>
          </table>

          {user?.role === "control_room_officer" && c.status === "open" && detail.evidence.length > 0 && !locked && (
            <div className="border border-slate-800 rounded p-2 space-y-1.5">
              <div className="text-xs text-slate-300">Request export of the whole case</div>
              <textarea rows={2} value={justification} onChange={(e) => setJustification(e.target.value)} className={`${input} w-full`}
                placeholder="Purpose (court, investigation reference…) — at least 20 characters" />
              <button onClick={requestExport} className="px-2 py-1 border border-sky-600/60 text-sky-300 text-xs rounded flex items-center gap-1"><Send size={11} /> Request export</button>
            </div>)}

          <div>
            <div className="text-xs text-slate-300 mb-1">Exports</div>
            {detail.exports.length === 0 && <div className="text-[11px] text-slate-500">None.</div>}
            {detail.exports.map((x) => (
              <div key={x.id} className="flex items-center justify-between text-xs border border-slate-800 rounded p-2 mb-1">
                <div>
                  <div className="text-slate-200">{x.status}{x.self_approved ? " · self-approved" : ""}</div>
                  <div className="text-[10px] text-slate-500">{x.requested_by} · {fmtIST(x.requested_at)}</div>
                  {x.bundle_sha256 && <div className="mono text-[10px] text-slate-500" title={x.bundle_sha256}>bundle SHA-256 {x.bundle_sha256.slice(0, 24)}…</div>}
                  {x.bundle_error && <div className="text-[10px] text-red-400">{x.bundle_error}</div>}
                </div>
                {x.status === "approved" && (
                  <button onClick={() => downloadSigned("bundle", x.id).catch((e) => setError(e.response?.data?.detail || "Download failed"))}
                    className="px-2 py-1 border border-emerald-600/60 text-emerald-300 rounded text-[10px] flex items-center gap-1"><Download size={11} /> Bundle</button>)}
              </div>))}
          </div>
          {isSenior && c.status === "open" && (
            <button onClick={() => run(() => api.post(`/cases/${c.id}/close`))} className="px-2 py-1 border border-slate-600 text-slate-300 text-xs rounded">Close case</button>)}
        </>)}
      </div>
    </div>
  );
}
