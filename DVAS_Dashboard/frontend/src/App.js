import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import { ThemeProvider } from "@/context/ThemeContext";
import { I18nProvider } from "@/context/I18nContext";
import { ROLE_HOME } from "@/lib/api";
import Login from "@/pages/Login";
import EdgeRouter from "@/pages/edge/EdgeRouter";
import IngestDashboard from "@/pages/IngestDashboard";
import AdminDashboard from "@/pages/AdminDashboard";
import AuditLogPage from "@/pages/AuditLogPage";

// Decomposed Command Shell & Sub-pages
import CommandShell from "@/pages/command/CommandShell";
import TacticalMapPage from "@/pages/command/TacticalMapPage";
import MultiDronePage from "@/pages/command/MultiDronePage";
import AlertsFeedPage from "@/pages/command/AlertsFeedPage";
import CrowdPage from "@/pages/command/CrowdPage";
import TrafficPage from "@/pages/command/TrafficPage";
import PersonPage from "@/pages/command/PersonPage";
import IntrusionPage from "@/pages/command/IntrusionPage";
import AnomalyPage from "@/pages/command/AnomalyPage";
import EvidencePage from "@/pages/command/EvidencePage";
import MissionsPage from "@/pages/command/MissionsPage";
import ModelsPage from "@/pages/command/ModelsPage";
import ExportsPage from "@/pages/command/ExportsPage";
import ReportsPage from "@/pages/command/ReportsPage";
import AggregatedPage from "@/pages/command/AggregatedPage";

function Splash() {
  return <div className="min-h-screen" style={{ background: "var(--dvas-root)" }} />;
}

function Guard({ allow, children }) {
  const { user, ready } = useAuth();
  if (!ready) return <Splash />;
  if (!user) return <Navigate to="/login" replace />;
  if (allow && !allow.includes(user.role)) return <Navigate to={ROLE_HOME[user.role] || "/command/tactical-map"} replace />;
  return children;
}

function RootRedirect() {
  const { user, ready } = useAuth();
  if (!ready) return <Splash />;
  return <Navigate to={user ? (ROLE_HOME[user.role] || "/command/tactical-map") : "/login"} replace />;
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

              {/* Nested Command Routes */}
              <Route
                path="/command"
                element={<Guard allow={["control_room_officer", "senior_officer"]}><CommandShell /></Guard>}
              >
                <Route index element={<Navigate to="tactical-map" replace />} />
                <Route path="tactical-map" element={<TacticalMapPage />} />
                <Route path="multi-drone" element={<MultiDronePage />} />
                <Route path="alerts" element={<AlertsFeedPage />} />
                <Route path="crowd" element={<CrowdPage />} />
                <Route path="traffic" element={<TrafficPage />} />
                <Route path="person" element={<PersonPage />} />
                <Route path="intrusion" element={<IntrusionPage />} />
                <Route path="anomaly" element={<AnomalyPage />} />
                <Route path="evidence" element={<EvidencePage />} />
                <Route path="missions" element={<MissionsPage />} />
                <Route path="models" element={<ModelsPage />} />
                <Route path="exports" element={<ExportsPage />} />
                <Route path="reports" element={<ReportsPage />} />
                <Route path="aggregated" element={<AggregatedPage />} />
                <Route path="*" element={<Navigate to="tactical-map" replace />} />
              </Route>

              <Route path="/admin" element={<Guard allow={["senior_officer", "system_administrator"]}><AdminDashboard /></Guard>} />
              <Route path="/audit" element={<Guard allow={["system_administrator", "senior_officer"]}><AuditLogPage /></Guard>} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </BrowserRouter>
        </AuthProvider>
      </I18nProvider>
    </ThemeProvider>
  );
}
