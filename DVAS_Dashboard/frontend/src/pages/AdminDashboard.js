import { useEffect, useState } from "react";
import AppShell from "@/components/layout/AppShell";
import { api, apiError, downloadBlob, ROLE_LABELS } from "@/lib/api";
import { fmtIST } from "@/lib/ist";
import { SessionsPanel, PolicyPanel, SettingsPanel, HoldsPanel, CompliancePanel, AuditAggregate } from "@/components/admin/AdminPanels";
import { useAuth } from "@/context/AuthContext";
import { useI18n } from "@/context/I18nContext";
import { ShieldCheck, Users, ClipboardList, Folder, Settings, Key, FileText, ShieldAlert, Download, Server, Plus, Trash2 } from "lucide-react";
import { useNavigate } from "react-router-dom";

// 4-role model: supervisory_officer pages merged into senior_officer
const PAGES = [
  { key: "users",   labelKey: "admin.page.users",   Icon: Users,          roles: ["system_administrator"] },
  { key: "policy",  labelKey: "admin.page.policy",  Icon: ShieldCheck,    roles: ["system_administrator","senior_officer"] },
  { key: "tokens",  labelKey: "admin.page.tokens",  Icon: Key,            roles: ["system_administrator"] },
  { key: "audit",   labelKey: "admin.page.audit",   Icon: ClipboardList,  roles: ["senior_officer","system_administrator"] },
  { key: "holds",   labelKey: "admin.page.holds",   Icon: Folder,         roles: ["senior_officer"] },
  { key: "sysconf", labelKey: "admin.page.sysconf", Icon: Settings,       roles: ["system_administrator","senior_officer"] },
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
  const [userForm, setUserForm] = useState({ email: "", name: "", password: "", role: "drone_operator", jurisdiction: "", mission_scope: "" });
  const [userBusy, setUserBusy] = useState(false);
  const [userMsg, setUserMsg] = useState("");

  const reloadUsers = async () => {
    try {
      const res = await api.get("/users");
      setUsers(Array.isArray(res.data) ? res.data : []);
    } catch (e) {
      setUsers([]);
    }
  };

  const handleCreateUser = async (e) => {
    e.preventDefault(); setUserBusy(true); setUserMsg("");
    try {
      await api.post("/users", {
        ...userForm,
        mission_scope: userForm.mission_scope.split(",").map(s => s.trim()).filter(Boolean),
      });
      setUserForm({ email: "", name: "", password: "", role: "drone_operator", jurisdiction: "", mission_scope: "" });
      setUserMsg("User created successfully.");
      reloadUsers();
    } catch (err) {
      setUserMsg(apiError(err, "Failed to create user"));
    } finally {
      setUserBusy(false);
    }
  };

  const handleSuspendUser = async (id) => {
    if (!window.confirm("Suspend this user?")) return;
    try {
      await api.delete(`/users/${id}`);
      reloadUsers();
    } catch (err) {
      alert(apiError(err, "Failed to suspend user"));
    }
  };
  const [q, setQ] = useState("");
  // Audit scope toggle: senior_officer has both jurisdiction-detail AND cross-jurisdiction aggregate
  const [auditScope, setAuditScope] = useState("detail"); // "detail" | "aggregate"
  const [exporting, setExporting] = useState(false);
  const [exportMsg, setExportMsg] = useState("");
  const nav = useNavigate();

  // ADM-02: Section 65B certificate for the entries matching the search box
  const export65b = async () => {
    setExporting(true); setExportMsg("");
    try {
      await downloadBlob("/audit-logs/export/pdf", q ? { q } : {}, "audit-65b-certificate.pdf");
    } catch (e) {
      setExportMsg(e?.response?.status === 429 ? "Rate limit reached; try again in a minute." : apiError(e, "Export failed"));
    } finally { setExporting(false); }
  };

  // Ingest server health (for sys admin quick-glance card)
  const [ingestHealth, setIngestHealth] = useState(null);
  useEffect(() => {
    if (user.role !== "system_administrator") return;
    let mounted = true;
    const fetchHealth = async () => {
      try {
        const { data } = await api.get("/ingest-server/ready");
        if (mounted) setIngestHealth(data);
      } catch {
        if (mounted) setIngestHealth({ reachable: false, status: "unreachable" });
      }
    };
    fetchHealth();
    const iv = setInterval(() => { if (!document.hidden) fetchHealth(); }, 15000);
    return () => { mounted = false; clearInterval(iv); };
  }, [user.role]);

  // Persist page to sessionStorage on every change
  useEffect(() => {
    try { sessionStorage.setItem("dvas_adm_page", page); } catch (e) {}
  }, [page]);

  useEffect(() => {
    if (page === "audit") {
      api.get(`/audit-logs${q ? `?q=${encodeURIComponent(q)}` : ""}`)
        .then(r => setLogs(Array.isArray(r.data) ? r.data : (r.data?.items || [])))
        .catch(() => setLogs([]));
      api.get("/audit-logs/verify").then(r => setChainInfo(r.data)).catch(()=>setChainInfo(null));
    }
    if (page === "users" && user.role === "system_administrator") reloadUsers();
  }, [page, q]);

  return (
    <AppShell title={t("admin.title")} subtitle={t("admin.subtitle")}>
      <div className="mb-4 border border-amber-500/40 bg-amber-500/5 p-3 flex items-start gap-2">
        <ShieldAlert size={16} className="text-amber-400 shrink-0 mt-0.5" />
        <div className="text-xs text-slate-300">{t("admin.sepPowers")}</div>
      </div>
      <div className="flex flex-col gap-4">
        {/* Ingest Server Quick Status — sys admin only */}
        {user.role === "system_administrator" && (
          <div
            data-testid="admin-ingest-card"
            className="border bg-slate-900 p-4 flex items-center gap-4 cursor-pointer hover:bg-slate-800/40 transition-colors"
            style={{ borderColor: ingestHealth?.reachable ? "var(--dvas-safe)" : "var(--dvas-crit)" }}
            onClick={() => nav("/ingest")}
          >
            <div className="w-10 h-10 rounded-lg flex items-center justify-center shrink-0"
              style={{ background: ingestHealth?.reachable ? "var(--dvas-safe-bg)" : "var(--dvas-crit-bg)" }}>
              <Server size={20} style={{ color: ingestHealth?.reachable ? "var(--dvas-safe)" : "var(--dvas-crit)" }} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-semibold" style={{ color: "var(--dvas-text)" }}>Ingest Server</div>
              <div className="text-xs mt-0.5" style={{ color: "var(--dvas-text-muted)" }}>
                {ingestHealth?.reachable
                  ? <span style={{ color: ingestHealth?.ready === false ? "var(--dvas-warn)" : "var(--dvas-safe)" }}>
                      ● {ingestHealth?.ready === false ? "Reachable · not ready" : "Online"}{ingestHealth?.version ? ` · v${ingestHealth.version}` : ""}
                    </span>
                  : <span style={{ color: "var(--dvas-crit)" }}>● Offline — server unreachable</span>}
              </div>
            </div>
            <div className="text-xs mono" style={{ color: "var(--dvas-blue)" }}>
              Open Ingest Dashboard →
            </div>
          </div>
        )}

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
            <div className="space-y-4">
              {/* Create User Form */}
              <div className="border border-slate-800 bg-slate-900">
                <div className="p-3 border-b border-slate-800 text-sm flex items-center gap-2">
                  <Plus size={16} className="text-sky-400" /> Create User (Every change is auto-logged to Audit Trail)
                </div>
                <form onSubmit={handleCreateUser} className="p-3 grid grid-cols-1 md:grid-cols-6 gap-2">
                  <input
                    data-testid="admin-user-email"
                    required
                    type="email"
                    placeholder="email (e.g. officer@dvas.kp)"
                    value={userForm.email}
                    onChange={e => setUserForm({ ...userForm, email: e.target.value })}
                    className="col-span-2 bg-slate-950 border border-slate-700 px-2 py-2 text-xs mono"
                  />
                  <input
                    data-testid="admin-user-name"
                    required
                    placeholder="full name"
                    value={userForm.name}
                    onChange={e => setUserForm({ ...userForm, name: e.target.value })}
                    className="col-span-2 bg-slate-950 border border-slate-700 px-2 py-2 text-xs mono"
                  />
                  <input
                    data-testid="admin-user-password"
                    required
                    type="password"
                    placeholder="initial password"
                    value={userForm.password}
                    onChange={e => setUserForm({ ...userForm, password: e.target.value })}
                    className="col-span-2 bg-slate-950 border border-slate-700 px-2 py-2 text-xs mono"
                  />
                  <select
                    data-testid="admin-user-role"
                    value={userForm.role}
                    onChange={e => setUserForm({ ...userForm, role: e.target.value })}
                    className="col-span-2 bg-slate-950 border border-slate-700 px-2 py-2 text-xs mono"
                  >
                    {Object.entries(ROLE_LABELS).map(([k, v]) => (
                      <option key={k} value={k}>{v}</option>
                    ))}
                  </select>
                  <input
                    data-testid="admin-user-jurisdiction"
                    placeholder="jurisdiction (e.g. TVM_NORTH)"
                    value={userForm.jurisdiction}
                    onChange={e => setUserForm({ ...userForm, jurisdiction: e.target.value })}
                    className="col-span-2 bg-slate-950 border border-slate-700 px-2 py-2 text-xs mono"
                  />
                  <input
                    data-testid="admin-user-missions"
                    placeholder="mission scope (e.g. M-101, M-102)"
                    value={userForm.mission_scope}
                    onChange={e => setUserForm({ ...userForm, mission_scope: e.target.value })}
                    className="col-span-2 bg-slate-950 border border-slate-700 px-2 py-2 text-xs mono"
                  />
                  <button
                    data-testid="admin-user-create-btn"
                    disabled={userBusy}
                    className="col-span-6 md:col-span-6 bg-sky-600 hover:bg-sky-500 text-white text-xs py-2 font-medium"
                  >
                    {userBusy ? "Creating User in Keycloak…" : "Create & Provision User"}
                  </button>
                  {userMsg && (
                    <div className={`col-span-6 mono text-[11px] ${userMsg.includes("successfully") ? "text-emerald-400" : "text-red-400"}`}>
                      {userMsg}
                    </div>
                  )}
                </form>
              </div>

              {/* User Directory */}
              <div className="border border-slate-800 bg-slate-900">
                <div className="p-3 border-b border-slate-800 text-sm flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Users size={16} className="text-sky-400" /> User Directory
                  </div>
                  <span className="mono text-xs text-slate-400">{users.length} registered officers</span>
                </div>
                <table className="w-full text-xs">
                  <thead className="bg-slate-950/60">
                    <tr>
                      {[t("table.name"), t("table.email"), t("table.role"), t("table.jurisdiction"), t("table.missionScope"), t("table.status"), "Actions"].map(h => (
                        <th key={h} className="text-left px-3 py-2 dvas-label border-b border-slate-800" style={{ fontSize: 10 }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="mono">
                    {users.map(u => (
                      <tr key={u.id} className="hover:bg-slate-800/40">
                        <td className="px-3 py-2 border-b border-slate-800/60 text-slate-100 font-medium">{u.name}</td>
                        <td className="px-3 py-2 border-b border-slate-800/60 text-slate-300">{u.email}</td>
                        <td className="px-3 py-2 border-b border-slate-800/60">
                          <span className="px-1.5 py-0.5 border border-sky-500/40 bg-sky-500/10 text-sky-300 text-[10px]">
                            {ROLE_LABELS[u.role] || u.role}
                          </span>
                        </td>
                        <td className="px-3 py-2 border-b border-slate-800/60">{u.jurisdiction || "—"}</td>
                        <td className="px-3 py-2 border-b border-slate-800/60 text-slate-400">
                          {(u.mission_scope || []).join(", ") || "—"}
                        </td>
                        <td className="px-3 py-2 border-b border-slate-800/60">
                          {u.suspended ? (
                            <span className="text-red-400 font-semibold">{t("common.suspended")}</span>
                          ) : (
                            <span className="text-emerald-400 font-semibold">{t("common.active")}</span>
                          )}
                        </td>
                        <td className="px-3 py-2 border-b border-slate-800/60">
                          {!u.suspended && (
                            <button
                              onClick={() => handleSuspendUser(u.id)}
                              className="px-2 py-1 border border-red-600/60 text-red-300 hover:bg-red-500/10 inline-flex items-center gap-1 text-[11px]"
                            >
                              <Trash2 size={11} /> Suspend
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {page === "policy" && <PolicyPanel />}

          {page === "tokens" && user.role === "system_administrator" && <SessionsPanel />}

          {page === "audit" && (
            <div className="border border-slate-800 bg-slate-900">
              <div className="p-3 border-b border-slate-800 flex items-center gap-3">
                <div className="text-sm flex items-center gap-2"><ClipboardList size={16} className="text-sky-400"/> {t("admin.auditHeader")}</div>
                {chainInfo && (
                  <div
                    data-testid="audit-integrity"
                    className={`px-2 py-0.5 border text-[10px] mono flex items-center gap-1.5 ${chainInfo.is_chain_valid ? "border-emerald-500/50 text-emerald-300 bg-emerald-500/10" : "border-red-500/50 text-red-300 bg-red-500/10"}`}
                    title={chainInfo.is_chain_valid ? "SHA-256 chain verified" : `${chainInfo.tampered_count} tampered entries`}
                  >
                    <span className={`w-1.5 h-1.5 ${chainInfo.is_chain_valid ? "" : "dvas-live-dot"}`} style={{background: chainInfo.is_chain_valid ? "var(--dvas-safe)" : "var(--dvas-crit)"}} />
                    {chainInfo.is_chain_valid ? t("admin.chainOk") : `${t("admin.tamper")} × ${chainInfo.tampered_count}`}
                    <span className="opacity-60">· {chainInfo.verified_count}/{chainInfo.total_checked} {t("admin.rows")}</span>
                  </div>
                )}
                <input data-testid="audit-search" placeholder={t("common.filter")} value={q} onChange={e=>setQ(e.target.value)} className="ml-auto bg-slate-950 border border-slate-700 focus:border-sky-500 outline-none px-3 py-1.5 text-xs mono w-64"/>
                {/* SR: 65B Export — inherits former supervisory_officer + existing SR authority */}
                {["senior_officer", "system_administrator"].includes(user.role) && (
                  <button data-testid="audit-65b" disabled={exporting} onClick={export65b} title="Section 65B certificate (PDF) for the entries matching the filter"
                    className="text-xs px-2 py-1 border border-sky-600/60 text-sky-300 hover:bg-sky-500/10 disabled:opacity-50 flex items-center gap-1">
                    <Download size={11}/> {exporting ? "Generating…" : "65B Export"}
                  </button>
                )}
                {exportMsg && <span className="mono text-[10px] text-red-400">{exportMsg}</span>}
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

              <div className="p-3 border-b border-slate-800 bg-slate-950 flex items-center justify-between flex-wrap gap-2">
                <div className="mono text-[11px] text-sky-300 flex items-center gap-2">
                  <ShieldAlert size={14} className="text-sky-400 shrink-0" />
                  <span>Interactive Audit Log Explorer with server-side filtering, Before/After field diffs, timeline correlation, and compliance export is available on the dedicated explorer page.</span>
                </div>
                <button
                  data-testid="open-full-audit-page"
                  onClick={() => nav("/audit")}
                  className="px-3 py-1 bg-sky-600 hover:bg-sky-500 text-white text-xs font-medium rounded transition-colors flex items-center gap-1.5 cursor-pointer shadow"
                >
                  <ClipboardList size={13} /> Open Full Audit Log Explorer →
                </button>
              </div>

              {/* Unit Detail view — full log table */}
              {(user.role !== "senior_officer" || auditScope === "detail") && (() => {
                const logList = Array.isArray(logs) ? logs : (logs?.items || []);
                return (
                  <div className="max-h-[560px] overflow-auto"><table className="w-full text-xs mono"><thead className="bg-slate-950/60 sticky top-0"><tr>{[t("table.timestamp"),t("table.actor"),t("table.role"),t("table.action"),t("table.target"),t("table.ip")].map(h=><th key={h} className="text-left px-3 py-2 dvas-label border-b border-slate-800" style={{fontSize:10}}>{h}</th>)}</tr></thead>
                  <tbody>{logList.map(l=>(<tr key={l.id} data-testid={`audit-row-${l.id}`} data-tamper={l.tamper ? "true" : "false"} className={`hover:bg-slate-800/40 ${l.tamper ? "bg-red-500/5" : ""}`}><td className="px-3 py-1.5 border-b border-slate-800/60 text-slate-400"><div className="flex items-center gap-1.5">{l.tamper ? <span data-testid={`tamper-${l.id}`} title="Hash-chain broken — this row may have been tampered with" className="inline-block w-1.5 h-1.5 dvas-live-dot" style={{background:"var(--dvas-crit)"}}/> : <span title="Chain OK" className="inline-block w-1.5 h-1.5" style={{background:"var(--dvas-safe)",opacity:0.8}}/>}<span>{fmtIST(l.ts)}</span></div></td><td className="px-3 py-1.5 border-b border-slate-800/60 text-slate-100">{l.actor_email}</td><td className="px-3 py-1.5 border-b border-slate-800/60 text-slate-500">{l.actor_role}</td><td className="px-3 py-1.5 border-b border-slate-800/60"><span className="px-1.5 py-0.5 border border-sky-500/40 bg-sky-500/10 text-sky-300 text-[10px]">{l.action}</span></td><td className="px-3 py-1.5 border-b border-slate-800/60 text-slate-400">{l.target||"-"}</td><td className="px-3 py-1.5 border-b border-slate-800/60 text-slate-500">{l.ip}</td></tr>))}</tbody></table></div>
                );
              })()}

              {/* Whole-log aggregate (grouped server-side) — Senior Officer toggle */}
              {user.role === "senior_officer" && auditScope === "aggregate" && <AuditAggregate />}
            </div>
          )}

          {page === "holds" && <HoldsPanel canReleaseEvidence={user.role === "senior_officer"} />}

          {page === "sysconf" && <SettingsPanel />}

          {page === "compl" && <CompliancePanel />}
        </div>
      </div>

    </AppShell>
  );
}
