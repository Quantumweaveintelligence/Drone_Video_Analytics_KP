import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import { ThemeProvider } from "@/context/ThemeContext";
import { I18nProvider } from "@/context/I18nContext";
import { ROLE_HOME } from "@/lib/api";
import Login from "@/pages/Login";
import EdgeRouter from "@/pages/edge/EdgeRouter";
import IngestDashboard from "@/pages/IngestDashboard";
import CommandDashboard from "@/pages/CommandDashboard";
import AdminDashboard from "@/pages/AdminDashboard";
import AuditLogPage from "@/pages/AuditLogPage";

function Splash() {
  return <div className="min-h-screen" style={{ background: "var(--dvas-root)" }} />;
}

function Guard({ allow, children }) {
  const { user, ready } = useAuth();
  if (!ready) return <Splash />;
  if (!user) return <Navigate to="/login" replace />;
  if (allow && !allow.includes(user.role)) return <Navigate to={ROLE_HOME[user.role] || "/command"} replace />;
  return children;
}

function RootRedirect() {
  const { user, ready } = useAuth();
  if (!ready) return <Splash />;
  return <Navigate to={user ? (ROLE_HOME[user.role] || "/command") : "/login"} replace />;
}

export default function App() {
  return (
    <ThemeProvider>
    <I18nProvider>
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<RootRedirect />} />
          <Route path="/login" element={<Login />} />
          <Route path="/edge/*" element={<Guard allow={["drone_operator"]}><EdgeRouter /></Guard>} />
          <Route path="/ingest" element={<Guard allow={["system_administrator"]}><IngestDashboard /></Guard>} />
          <Route path="/command" element={<Guard allow={["control_room_officer","senior_officer"]}><CommandDashboard /></Guard>} />
          <Route path="/admin" element={<Guard allow={["senior_officer","system_administrator"]}><AdminDashboard /></Guard>} />
          <Route path="/audit" element={<Guard allow={["system_administrator"]}><AuditLogPage /></Guard>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
    </I18nProvider>
    </ThemeProvider>
  );
}
