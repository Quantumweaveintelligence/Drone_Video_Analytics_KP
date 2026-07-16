import { useState } from "react";
import { useNavigate, Navigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { useI18n } from "@/context/I18nContext";
import { ROLE_HOME } from "@/lib/api";
import { Shield, Lock as LockKey, User } from "lucide-react";

const QUICK = [
  { email: "operator@dvas.kp", role: "drone_operator" },
  { email: "control@dvas.kp", role: "control_room_officer" },
  { email: "senior@dvas.kp", role: "senior_officer" },
  { email: "admin@dvas.kp", role: "system_administrator" },
];

function fmt(detail) {
  if (!detail) return "Login failed";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map(e => e.msg || JSON.stringify(e)).join(" ");
  return String(detail);
}

export default function Login() {
  const { user, ready, login } = useAuth();
  const { t } = useI18n();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("dvas1234");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const nav = useNavigate();

  if (!ready) return <div className="min-h-screen" style={{ background: "var(--dvas-root)" }} />;
  if (user) return <Navigate to={ROLE_HOME[user.role] || "/command"} replace />;

  const submit = async (e) => {
    e.preventDefault();
    setErr(""); setBusy(true);
    try {
      const u = await login(email.trim().toLowerCase(), password);
      nav(ROLE_HOME[u.role] || "/command", { replace: true });
    } catch (ex) {
      setErr(fmt(ex.response?.data?.detail) || ex.message);
    } finally { setBusy(false); }
  };

  return (
    <div className="min-h-screen relative flex items-center justify-center overflow-hidden">
      <div
        className="absolute inset-0"
        style={{
          backgroundImage: `linear-gradient(rgba(2,6,23,0.85), rgba(2,6,23,0.95)), url('https://images.unsplash.com/photo-1768571845597-38f066307f1e?w=1920&q=80')`,
          backgroundSize: "cover", backgroundPosition: "center",
        }}
      />
      <div className="absolute inset-0 dvas-grid-bg opacity-30" />

      <div className="relative w-full max-w-5xl grid grid-cols-1 md:grid-cols-2 gap-0 mx-6">
        {/* Left panel — branding */}
        <div className="hidden md:flex flex-col justify-between border border-slate-800 bg-slate-900/70 backdrop-blur p-8 min-h-[560px]">
          <div className="flex flex-col items-center justify-center flex-1 text-center mt-8">
            <img src="/kerala-police-logo.png" alt="Kerala Police Logo" className="w-32 h-32 object-contain mb-6" />
            <div className="text-4xl font-bold tracking-tight text-slate-100">
              Kerala Police
            </div>
            <div className="text-xl font-medium tracking-tight text-slate-300 mt-3">
              Drone Video Analytics System
            </div>
            <div className="dvas-label mt-6 max-w-sm">
              {t("login.tagline")}
            </div>
          </div>
          <div className="border-t border-slate-800 pt-4 space-y-1">
            <div className="dvas-label">{t("login.accessModel")}</div>
            <div className="text-xs text-slate-400 leading-relaxed">
              {t("login.accessNote")}
            </div>
          </div>
        </div>

        {/* Right — login form */}
        <div className="border border-slate-800 md:border-l-0 bg-slate-950/90 backdrop-blur p-8">
          <div className="dvas-label mb-1">{t("login.secureAccess")}</div>
          <h2 className="text-2xl font-semibold tracking-tight mb-6">{t("login.signIn")}</h2>

          <form onSubmit={submit} className="space-y-4">
            <label className="block">
              <div className="dvas-label mb-1.5">{t("login.email")}</div>
              <div className="relative">
                <User size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                <input
                  data-testid="input-email"
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full bg-slate-900 border border-slate-700 focus:border-sky-500 outline-none px-9 py-2.5 text-sm mono"
                  placeholder="officer@dvas.kp"
                />
              </div>
            </label>
            <label className="block">
              <div className="dvas-label mb-1.5">{t("login.password")}</div>
              <div className="relative">
                <LockKey size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                <input
                  data-testid="input-password"
                  type="password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full bg-slate-900 border border-slate-700 focus:border-sky-500 outline-none px-9 py-2.5 text-sm mono"
                />
              </div>
            </label>

            {err && (
              <div data-testid="login-error" className="text-xs px-3 py-2 border border-red-500/50 bg-red-500/10 text-red-300">
                {err}
              </div>
            )}

            <button
              type="submit"
              data-testid="btn-login"
              disabled={busy}
              className="w-full bg-sky-600 hover:bg-sky-500 text-white text-sm py-2.5 disabled:opacity-60 transition-colors"
            >
              {busy ? t("login.busy") : t("login.enter")}
            </button>
          </form>

          <div className="mt-8 border-t border-slate-800 pt-4">
            <div className="dvas-label mb-2">{t("login.quickTitle")}</div>
            <div className="grid grid-cols-2 gap-1.5">
              {QUICK.map((q) => (
                <button
                  key={q.email}
                  type="button"
                  data-testid={`quick-${q.role}`}
                  onClick={() => { setEmail(q.email); setPassword("dvas1234"); }}
                  className="text-left text-xs px-2.5 py-1.5 border border-slate-800 hover:border-sky-500/60 hover:bg-slate-900 transition-colors"
                >
                  <div className="text-slate-100">{t(`role.${q.role}`)}</div>
                  <div className="mono text-[10px] text-slate-500 truncate">{q.email}</div>
                </button>
              ))}
            </div>
            <div className="mono text-[10px] text-slate-600 mt-3">{t("login.demoNote")}</div>
          </div>
        </div>
      </div>
    </div>
  );
}
