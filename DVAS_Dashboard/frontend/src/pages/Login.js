import { useState } from "react";
import { useNavigate, Navigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { useI18n } from "@/context/I18nContext";
import { ROLE_HOME, ROLE_LABELS } from "@/lib/api";
import { Shield, Lock as LockKey, User, ChevronDown, Eye, EyeOff } from "lucide-react";


function fmt(detail) {
  if (!detail) return "Login failed";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map(e => e.msg || JSON.stringify(e)).join(" ");
  return String(detail);
}

export default function Login() {
  const { user, ready, login, accessError } = useAuth();
  const { t } = useI18n();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
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
      // Server errors carry a detail; client-side errors (e.g. wrong application for the role) a message
      setErr(ex.response ? fmt(ex.response.data?.detail) : (ex.message || "Login failed"));
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
          <div className="flex flex-col items-center justify-center flex-1 text-center my-auto">
            <img src="/kerala-police-logo.png" alt="Kerala Police Logo" className="w-32 h-32 object-contain mb-6 drop-shadow-md" />
            <div className="flex flex-col items-center leading-tight">
              <div className="text-3xl lg:text-4xl font-extrabold tracking-normal text-slate-100 font-malayalam drop-shadow-sm">
                കേരള പോലീസ്
              </div>
              <div className="text-2xl lg:text-3xl font-black tracking-wider uppercase text-slate-100 font-kerala-en mt-2 drop-shadow-sm">
                KERALA POLICE
              </div>
            </div>
            <div className="text-xl font-medium tracking-tight text-slate-300 mt-4">
              Drone Video Analytics System
            </div>
            <div className="dvas-label mt-6 max-w-sm">
              {t("login.tagline")}
            </div>
          </div>
        </div>

        {/* Right — login form */}
        <div className="border border-slate-800 md:border-l-0 bg-slate-950/90 backdrop-blur p-8">
          <div className="md:hidden flex flex-col items-center mb-6 text-center">
            <img src="/kerala-police-logo.png" alt="Kerala Police Logo" className="w-16 h-16 object-contain mb-3 drop-shadow-md" />
            <div className="text-xl font-extrabold text-slate-100 font-malayalam leading-tight">
              കേരള പോലീസ്
            </div>
            <div className="text-lg font-black tracking-wider uppercase text-slate-100 font-kerala-en mt-0.5">
              KERALA POLICE
            </div>
            <div className="text-xs text-slate-400 mt-1">
              Drone Video Analytics System
            </div>
          </div>
          <div className="dvas-label mb-1">{t("login.secureAccess")}</div>
          <h2 className="text-2xl font-semibold tracking-tight mb-2">{t("login.signIn")}</h2>
          <p className="text-xs text-slate-400 leading-relaxed mb-6">
            Welcome to Kerala Police Drone Video Analytics System Platform login. Please enter your Email and Password to getting started.
          </p>

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
                  placeholder="Email"
                />
              </div>
            </label>
            <label className="block">
              <div className="dvas-label mb-1.5">{t("login.password")}</div>
              <div className="relative">
                <LockKey size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                <input
                  data-testid="input-password"
                  type={showPassword ? "text" : "password"}
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full bg-slate-900 border border-slate-700 focus:border-sky-500 outline-none pl-9 pr-10 py-2.5 text-sm mono"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300 transition-colors focus:outline-none"
                  title={showPassword ? "Hide password" : "Show password"}
                >
                  {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </label>

            {!err && accessError && (
              <div data-testid="login-access-error" className="text-xs px-3 py-2 border border-amber-500/50 bg-amber-500/10 text-amber-300">
                {accessError}
              </div>
            )}
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


        </div>
      </div>
    </div>
  );
}
