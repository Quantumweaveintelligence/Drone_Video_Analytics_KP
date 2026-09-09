import { Routes, Route, Navigate } from "react-router-dom";
import Dashboard from "./Dashboard";
import MissionControl from "./MissionControl";
import LiveMonitoring from "./LiveMonitoring";
import GpsTelemetry from "./GpsTelemetry";
import BufferAndChange from "./BufferAndChange";
import FrameSelection from "./FrameSelection";
import Incidents from "./Incidents";
import EvidenceManagement from "./EvidenceManagement";
import SystemHealth from "./SystemHealth";
import LogsAudit from "./LogsAudit";
import Settings from "./Settings";

export default function EdgeRouter() {
  return (
    <Routes>
      <Route path="dashboard" element={<Dashboard />} />
      <Route path="mission-control" element={<MissionControl />} />
      <Route path="live-monitoring" element={<LiveMonitoring />} />
      <Route path="gps-telemetry" element={<GpsTelemetry />} />
      <Route path="buffer-and-change" element={<BufferAndChange />} />
      <Route path="frame-selection" element={<FrameSelection />} />
      <Route path="incidents" element={<Incidents />} />
      <Route path="evidence-management" element={<EvidenceManagement />} />
      <Route path="system-health" element={<SystemHealth />} />
      <Route path="logs-audit" element={<LogsAudit />} />
      <Route path="settings" element={<Settings />} />
      <Route path="*" element={<Navigate to="dashboard" replace />} />
    </Routes>
  );
}
