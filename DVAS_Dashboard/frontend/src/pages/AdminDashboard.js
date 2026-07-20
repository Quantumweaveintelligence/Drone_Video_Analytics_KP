import { useEffect, useState } from "react";
import AppShell from "@/components/layout/AppShell";
import { api, ROLE_LABELS } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { useI18n } from "@/context/I18nContext";
import { ShieldCheck, Users, ClipboardList, Folder, Settings, Key, FileText, ShieldAlert, Download } from "lucide-react";

// 4-role model: supervisory_officer pages merged into senior_officer
const PAGES = [
  { key: "users",   labelKey: "admin.page.users",   Icon: Users,          roles: ["system_administrator"] },
  { key: "policy",  labelKey: "admin.page.policy",  Icon: ShieldCheck,    roles: ["system_administrator","senior_officer"] },
  { key: "tokens",  labelKey: "admin.page.tokens",  Icon: Key,            roles: ["system_administrator"] },
  { key: "audit",   labelKey: "admin.page.audit",   Icon: ClipboardList,  roles: ["senior_officer","system_administrator"] },
  { key: "holds",   labelKey: "admin.page.holds",   Icon: Folder,         roles: ["senior_officer"] },
  { key: "sysconf", labelKey: "admin.page.sysconf", Icon: Settings,       roles: ["system_administrator"] },
  { key: "compl",   labelKey: "admin.page.compl",   Icon: FileText,       roles: ["senior_officer"] },
];

export default function AdminDashboard() {
  const { user } = useAuth();
  const { t } = useI18n();
  const visible = PAGES.filter(p => p.roles.includes(user.role));
  const [page, setPage] = useState(() => {
    try {
      const saved = sessionStorage.getItem("dvas_adm_page");
      // Only restore if the saved page is visible to this role
      if (saved && visible.some(p => p.key === saved)) return saved;
    } catch (e) { /* noop */ }
    return visible[0]?.key || "audit";
  });
  const [logs, setLogs] = useState([]);
  const [chainInfo, setChainInfo] = useState(null);
  const [users, setUsers] = useState([]);
  const [q, setQ] = useState("");
  // Audit scope toggle: senior_officer has both jurisdiction-detail AND cross-jurisdiction aggregate
  const [auditScope, setAuditScope] = useState("detail"); // "detail" | "aggregate"
  const [sysCfg, setSysCfg] = useState({ retention_days: 90, backup_time: "02:30", tls_min: "1.3" });
  const [pending, setPending] = useState(null);

  // Persist page to sessionStorage on every change
  useEffect(() => {
    try { sessionStorage.setItem("dvas_adm_page", page); } catch (e) {}
  }, [page]);

  useEffect(() => {
    if (page === "audit") {
      api.get(`/audit-logs${q ? `?q=${encodeURIComponent(q)}` : ""}`).then(r => setLogs(r.data)).catch(()=>{});
      api.get("/audit-logs/verify").then(r => setChainInfo(r.data)).catch(()=>setChainInfo(null));
    }
    if (page === "users" && user.role === "system_administrator") api.get("/users").then(r => setUsers(r.data)).catch(()=>{});
  }, [page, q]);

  return (
    <AppShell title={t("admin.title")} subtitle={t("admin.subtitle")}>
      <div className="mb-4 border border-amber-500/40 bg-amber-500/5 p-3 flex items-start gap-2">
        <ShieldAlert size={16} className="text-amber-400 shrink-0 mt-0.5" />
        <div className="text-xs text-slate-300">{t("admin.sepPowers")}</div>
      </div>
      <div className="flex flex-col gap-4">
        <nav
          data-testid="adm-tabbar"
          className="flex items-center gap-1 overflow-x-auto no-scrollbar"
          style={{ borderBottom: "1px solid var(--dvas-border)" }}
        >
          <span className="dvas-label pr-2 shrink-0" style={{fontSize:9}}>{t("admin.pages")}</span>
          {visible.map((p,i)=>(
            <button
              key={p.key}
              data-testid={`adm-nav-${p.key}`}
              onClick={()=>setPage(p.key)}
              className={`shrink-0 flex items-center gap-2 px-3 py-2 text-xs whitespace-nowrap transition-colors ${
                page===p.key?"text-white":"text-slate-400 hover:text-white"
              }`}
              style={{
                borderBottom: page===p.key ? "2px solid var(--dvas-cyan)" : "2px solid transparent",
                marginBottom: "-1px",
              }}
            >
              <span className="mono text-[10px] text-slate-600">{String(i+1).padStart(2,"0")}</span>
              <p.Icon size={14}/> {t(p.labelKey)}
            </button>
          ))}
        </nav>

        <div className="min-w-0">
          {page === "users" && user.role === "system_administrator" && (
            <div className="border border-slate-800 bg-slate-900">
              <div className="p-3 border-b border-slate-800 text-sm flex items-center gap-2"><Users size={16} className="text-sky-400"/> {t("admin.page.users")}</div>
              <table className="w-full text-xs"><thead className="bg-slate-950/60"><tr>{[t("table.name"),t("table.email"),t("table.role"),t("table.jurisdiction"),t("table.missionScope"),t("table.status")].map(h=><th key={h} className="text-left px-3 py-2 dvas-label border-b border-slate-800" style={{fontSize:10}}>{h}</th>)}</tr></thead>
                <tbody className="mono">{users.map(u=>(<tr key={u.id} className="hover:bg-slate-800/40"><td className="px-3 py-2 border-b border-slate-800/60 text-slate-100">{u.name}</td><td className="px-3 py-2 border-b border-slate-800/60">{u.email}</td><td className="px-3 py-2 border-b border-slate-800/60"><span className="px-1.5 py-0.5 border border-sky-500/40 bg-sky-500/10 text-sky-300 text-[10px]">{t(`role.${u.role}`)}</span></td><td className="px-3 py-2 border-b border-slate-800/60">{u.jurisdiction}</td><td className="px-3 py-2 border-b border-slate-800/60 text-slate-400">{(u.mission_scope||[]).join(", ")||"-"}</td><td className="px-3 py-2 border-b border-slate-800/60">{u.suspended?<span className="text-red-400">{t("common.suspended")}</span>:<span className="text-emerald-400">{t("common.active")}</span>}</td></tr>))}</tbody></table>
              <div className="p-3 border-t border-slate-800 mono text-[11px] text-slate-500">Full CRUD (create / suspend) is available under Ingest → User &amp; Role Management. Every change is auto-logged.</div>
            </div>
          )}

          {page === "policy" && (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="border border-slate-800 bg-slate-900 p-4"><div className="dvas-label mb-2">Retention · Analytics logs</div><div className="mono text-2xl text-sky-400">180 <span className="text-xs text-slate-500">days</span></div></div>
              <div className="border border-slate-800 bg-slate-900 p-4"><div className="dvas-label mb-2">Retention · Evidence clips</div><div className="mono text-2xl text-sky-400">90 <span className="text-xs text-slate-500">days</span></div></div>
              <div className="border border-slate-800 bg-slate-900 p-4"><div className="dvas-label mb-2">Access token TTL</div><div className="mono text-2xl text-sky-400">15 <span className="text-xs text-slate-500">min</span></div></div>
              <div className="col-span-1 md:col-span-3 border border-slate-800 bg-slate-900 p-4">
                <div className="dvas-label mb-3">Active ABAC Rules · rule editor</div>
                <ul className="text-xs mono space-y-2 text-slate-300">
                  <li>• <span className="text-sky-400">jurisdiction_lock</span> — Officer scope ≡ jurisdiction attribute</li>
                  <li>• <span className="text-sky-400">active_mission_window</span> — Control Room / Field limited to open missions</li>
                  <li>• <span className="text-sky-400">seniority_no_bypass</span> — Rank ≠ automatic drill-down</li>
                  <li>• <span className="text-sky-400">admin_content_wall</span> — Sys Admin denied evidence content</li>
                </ul>
                {user.role === "system_administrator" && <button data-testid="policy-save" onClick={()=>setPending({title:"Save ABAC ruleset",diff:"admin_content_wall: unchanged\nseniority_no_bypass: unchanged\nnew rule: mission_time_ttl=8h"})} className="mt-4 px-3 py-1.5 bg-sky-600 hover:bg-sky-500 text-white text-xs">Save with diff preview…</button>}
              </div>
            </div>
          )}

          {page === "tokens" && user.role === "system_administrator" && (
            <div className="border border-slate-800 bg-slate-900">
              <div className="p-3 border-b border-slate-800 text-sm flex items-center gap-2"><Key size={16} className="text-sky-400"/> Active Sessions</div>
              <table className="w-full text-xs mono"><thead className="bg-slate-950/60"><tr>{["User","Role","Issued","Expires","IP","Action"].map(h=><th key={h} className="text-left px-3 py-2 dvas-label border-b border-slate-800" style={{fontSize:10}}>{h}</th>)}</tr></thead>
              <tbody>{["operator@dvas.kp","control@dvas.kp","supervisor@dvas.kp"].map((e,i)=>(<tr key={e} className="hover:bg-slate-800/40"><td className="px-3 py-2 border-b border-slate-800/60">{e}</td><td className="px-3 py-2 border-b border-slate-800/60">{["Drone Op","Ctrl Room","Supervisor"][i]}</td><td className="px-3 py-2 border-b border-slate-800/60 text-slate-500">{new Date(Date.now()-i*600000).toLocaleTimeString()}</td><td className="px-3 py-2 border-b border-slate-800/60 text-slate-500">{new Date(Date.now()+3600000).toLocaleTimeString()}</td><td className="px-3 py-2 border-b border-slate-800/60 text-slate-500">10.0.0.{22+i}</td><td className="px-3 py-2 border-b border-slate-800/60"><button data-testid={`revoke-${e}`} className="px-2 py-1 border border-red-600/60 text-red-300 hover:bg-red-500/10">Revoke</button></td></tr>))}</tbody></table>
            </div>
          )}

          {page === "audit" && (
            <div className="border border-slate-800 bg-slate-900">
              <div className="p-3 border-b border-slate-800 flex items-center gap-3">
                <div className="text-sm flex items-center gap-2"><ClipboardList size={16} className="text-sky-400"/> {t("admin.auditHeader")}</div>
                {chainInfo && (
                  <div
                    data-testid="audit-integrity"
                    className={`px-2 py-0.5 border text-[10px] mono flex items-center gap-1.5 ${chainInfo.integrity_ok ? "border-emerald-500/50 text-emerald-300 bg-emerald-500/10" : "border-red-500/50 text-red-300 bg-red-500/10"}`}
                    title={chainInfo.integrity_ok ? "SHA-256 chain verified" : `${chainInfo.tampered_count} tampered entries`}
                  >
                    <span className={`w-1.5 h-1.5 ${chainInfo.integrity_ok ? "" : "dvas-live-dot"}`} style={{background: chainInfo.integrity_ok ? "var(--dvas-safe)" : "var(--dvas-crit)"}} />
                    {chainInfo.integrity_ok ? t("admin.chainOk") : `${t("admin.tamper")} × ${chainInfo.tampered_count}`}
                    <span className="opacity-60">· {chainInfo.total} {t("admin.rows")}</span>
                  </div>
                )}
                <input data-testid="audit-search" placeholder={t("common.filter")} value={q} onChange={e=>setQ(e.target.value)} className="ml-auto bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-1.5 text-xs mono w-64"/>
                {/* SR: 65B Export — inherits former supervisory_officer + existing SR authority */}
                {user.role === "senior_officer" && <button data-testid="audit-65b" className="text-xs px-2 py-1 border border-sky-600/60 text-sky-300 hover:bg-sky-500/10 flex items-center gap-1"><Download size={11}/> 65B Export</button>}
              </div>

              {/* Jurisdiction-detail / Cross-jurisdiction aggregate toggle — Senior Officer only */}
              {user.role === "senior_officer" && (
                <div className="px-3 pt-2 pb-0 flex items-center gap-2 border-b border-slate-800/60">
                  <span className="dvas-label text-[10px] mr-1">Audit View:</span>
                  {[{key:"detail",label:"Unit Detail"},{key:"aggregate",label:"Cross-Jurisdiction Aggregate"}].map(opt => (
                    <button
                      key={opt.key}
                      data-testid={`audit-scope-${opt.key}`}
                      onClick={() => setAuditScope(opt.key)}
                      className={`px-3 py-1.5 text-xs flex items-center gap-1.5 transition-colors ${
                        auditScope === opt.key ? "text-white" : "text-slate-400 hover:text-white"
                      }`}
                      style={{
                        borderBottom: auditScope === opt.key ? "2px solid var(--dvas-cyan)" : "2px solid transparent",
                        marginBottom: "-1px",
                      }}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              )}

              {user.role === "system_administrator" && <div className="p-2 border-b border-slate-800 bg-amber-500/5 mono text-[11px] text-amber-300">Sys Admin view limited to own actions.</div>}

              {/* Unit Detail view — full log table */}
              {(user.role !== "senior_officer" || auditScope === "detail") && (
                <div className="max-h-[560px] overflow-auto"><table className="w-full text-xs mono"><thead className="bg-slate-950/60 sticky top-0"><tr>{[t("table.timestamp"),t("table.actor"),t("table.role"),t("table.action"),t("table.target"),t("table.ip")].map(h=><th key={h} className="text-left px-3 py-2 dvas-label border-b border-slate-800" style={{fontSize:10}}>{h}</th>)}</tr></thead>
                <tbody>{logs.map(l=>(<tr key={l.id} data-testid={`audit-row-${l.id}`} data-tamper={l.tamper ? "true" : "false"} className={`hover:bg-slate-800/40 ${l.tamper ? "bg-red-500/5" : ""}`}><td className="px-3 py-1.5 border-b border-slate-800/60 text-slate-400"><div className="flex items-center gap-1.5">{l.tamper ? <span data-testid={`tamper-${l.id}`} title="Hash-chain broken — this row may have been tampered with" className="inline-block w-1.5 h-1.5 dvas-live-dot" style={{background:"var(--dvas-crit)"}}/> : <span title="Chain OK" className="inline-block w-1.5 h-1.5" style={{background:"var(--dvas-safe)",opacity:0.8}}/>}<span>{new Date(l.ts).toLocaleString()}</span></div></td><td className="px-3 py-1.5 border-b border-slate-800/60 text-slate-100">{l.actor_email}</td><td className="px-3 py-1.5 border-b border-slate-800/60 text-slate-500">{l.actor_role}</td><td className="px-3 py-1.5 border-b border-slate-800/60"><span className="px-1.5 py-0.5 border border-sky-500/40 bg-sky-500/10 text-sky-300 text-[10px]">{l.action}</span></td><td className="px-3 py-1.5 border-b border-slate-800/60 text-slate-400">{l.target||"-"}</td><td className="px-3 py-1.5 border-b border-slate-800/60 text-slate-500">{l.ip}</td></tr>))}</tbody></table></div>
              )}

              {/* Cross-Jurisdiction Aggregate view — Senior Officer toggle */}
              {user.role === "senior_officer" && auditScope === "aggregate" && (() => {
                // Group by role and action for a cross-jurisdiction rollup
                const byRole = logs.reduce((acc, l) => { acc[l.actor_role] = (acc[l.actor_role] || 0) + 1; return acc; }, {});
                const byAction = logs.reduce((acc, l) => { acc[l.action] = (acc[l.action] || 0) + 1; return acc; }, {});
                return (
                  <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="border border-slate-800 bg-slate-900/60 p-3">
                      <div className="dvas-label mb-2 text-[10px]">Events by Role · all jurisdictions</div>
                      <table className="w-full text-xs mono">
                        <thead><tr><th className="text-left px-2 py-1 text-slate-500" style={{fontSize:10}}>Role</th><th className="text-right px-2 py-1 text-slate-500" style={{fontSize:10}}>Events</th></tr></thead>
                        <tbody>{Object.entries(byRole).sort((a,b)=>b[1]-a[1]).map(([role,count])=>(
                          <tr key={role} className="hover:bg-slate-800/40">
                            <td className="px-2 py-1 border-b border-slate-800/40 text-slate-300">{role}</td>
                            <td className="px-2 py-1 border-b border-slate-800/40 text-sky-400 text-right">{count}</td>
                          </tr>
                        ))}</tbody>
                      </table>
                    </div>
                    <div className="border border-slate-800 bg-slate-900/60 p-3">
                      <div className="dvas-label mb-2 text-[10px]">Events by Action · all jurisdictions</div>
                      <table className="w-full text-xs mono">
                        <thead><tr><th className="text-left px-2 py-1 text-slate-500" style={{fontSize:10}}>Action</th><th className="text-right px-2 py-1 text-slate-500" style={{fontSize:10}}>Count</th></tr></thead>
                        <tbody>{Object.entries(byAction).sort((a,b)=>b[1]-a[1]).map(([action,count])=>(
                          <tr key={action} className="hover:bg-slate-800/40">
                            <td className="px-2 py-1 border-b border-slate-800/40 text-slate-300">{action}</td>
                            <td className="px-2 py-1 border-b border-slate-800/40 text-amber-400 text-right">{count}</td>
                          </tr>
                        ))}</tbody>
                      </table>
                    </div>
                    <div className="col-span-1 md:col-span-2 border border-slate-800/60 bg-slate-900/30 px-4 py-2 mono text-[10px] text-slate-500">
                      ABAC: Aggregate view shows cross-jurisdiction rollup only. Drill-down into individual jurisdiction operations is in “Unit Detail” mode.
                    </div>
                  </div>
                );
              })()}
            </div>
          )}

          {page === "holds" && (
            <div className="border border-slate-800 bg-slate-900 p-4"><div className="dvas-label mb-3 flex items-center gap-2"><Folder size={12}/> Active Legal Holds</div>
              <ul className="divide-y divide-slate-800 -mx-4">
                {[{id:"LH-24001",ev:"E-2003",who:"Supervisory Officer · Tvm",ref:"Cr. No. 145/2025",since:"2025-11-04"},{id:"LH-24002",ev:"E-2011",who:"Senior Officer · State HQ",ref:"PIL 42/2025",since:"2025-12-01"}].map(h=>(
                  <li key={h.id} className="px-4 py-3 flex items-center justify-between text-xs mono"><div><div className="text-slate-100">{h.id} · {h.ev}</div><div className="text-slate-500 text-[11px] mt-0.5">{h.who} · {h.ref}</div></div><div className="text-right"><div className="text-slate-400">since {h.since}</div><div className="flex gap-1 mt-1 justify-end"><span className="px-1.5 py-0.5 border border-red-500/50 bg-red-500/10 text-red-400 text-[10px] uppercase">Hold Active</span><button data-testid={`release-${h.id}`} className="text-[10px] px-1.5 py-0.5 border border-slate-700 hover:border-sky-500">Release</button></div></div></li>
                ))}
              </ul>
            </div>
          )}

          {page === "sysconf" && user.role === "system_administrator" && (
            <div className="border border-slate-800 bg-slate-900 p-4 space-y-4">
              <div className="dvas-label">Infrastructure settings</div>
              <label className="block"><div className="dvas-label mb-1">Retention job runs at</div><input value={sysCfg.backup_time} onChange={e=>setSysCfg({...sysCfg,backup_time:e.target.value})} className="bg-slate-950 border border-slate-700 px-2 py-2 text-xs mono w-32"/></label>
              <label className="block"><div className="dvas-label mb-1">Minimum TLS</div><input value={sysCfg.tls_min} onChange={e=>setSysCfg({...sysCfg,tls_min:e.target.value})} className="bg-slate-950 border border-slate-700 px-2 py-2 text-xs mono w-32"/></label>
              <button data-testid="sysconf-save" onClick={()=>setPending({title:"Save system configuration",diff:`backup_time: 02:30 → ${sysCfg.backup_time}\ntls_min: 1.3 → ${sysCfg.tls_min}`})} className="px-3 py-1.5 bg-sky-600 hover:bg-sky-500 text-white text-xs">Save (confirm required)</button>
            </div>
          )}

          {page === "compl" && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {[{t:"Weekly access-pattern review",a:2,f:"↓ 12% vs last week"},{t:"Cross-jurisdiction attempts blocked",a:7,f:"↑ 3 anomalies flagged"},{t:"Admin config-change count",a:14,f:"Reviewed by Supervisory"},{t:"Export requests · SLA breach",a:1,f:"Needs Supervisor attention"}].map(c=>(
                <div key={c.t} className="border border-slate-800 bg-slate-900 p-4"><div className="dvas-label">{c.t}</div><div className="mono text-3xl mt-2 text-sky-400">{c.a}</div><div className="mono text-[11px] text-slate-400 mt-1">{c.f}</div></div>
              ))}
            </div>
          )}
        </div>
      </div>

      {pending && (
        <div className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-6" onClick={()=>setPending(null)}>
          <div className="border border-slate-700 bg-slate-950 max-w-md w-full p-4" onClick={e=>e.stopPropagation()}>
            <div className="text-sm mb-2">{pending.title}</div>
            <div className="dvas-label mb-2">Before / After Diff</div>
            <pre className="mono text-[11px] bg-slate-900 border border-slate-800 p-3 whitespace-pre-wrap">{pending.diff}</pre>
            <div className="mt-3 flex justify-end gap-2 text-xs">
              <button onClick={()=>setPending(null)} className="px-3 py-1.5 border border-slate-700">Cancel</button>
              <button data-testid="confirm-save" onClick={()=>setPending(null)} className="px-3 py-1.5 bg-sky-600 hover:bg-sky-500">Confirm &amp; save</button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}
