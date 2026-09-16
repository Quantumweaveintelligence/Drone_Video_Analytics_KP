import {
  MapPin,
  Radio,
  Bell,
  Film,
  Users,
  Car,
  UserSearch,
  Shield,
  ClipboardList,
  Cpu,
  ExternalLink,
  FileText,
  BarChart3,
  ShieldCheck,
  Gauge,
  LayoutDashboard,
  Target,
  Video,
  Compass,
  Scan,
  Image,
  AlertTriangle,
  Archive,
  Server,
  Settings
} from "lucide-react";

export const NAV_MANIFEST = {
  control_room_officer: [
    {
      section: "Surveillance",
      sectionKey: "nav.section.surveillance",
      items: [
        { key: "tactical-map", path: "/command/tactical-map", labelKey: "cmd.page.map", fallback: "Tactical Map", Icon: MapPin },
        { key: "multi-drone", path: "/command/multi-drone", labelKey: "cmd.page.multidrone", fallback: "Multi-Drone View", Icon: Radio },
        { key: "alerts", path: "/command/alerts", labelKey: "cmd.page.alerts", fallback: "Live Alerts Feed", Icon: Bell },
      ]
    },
    {
      section: "AI Analytics",
      sectionKey: "nav.section.analytics",
      items: [
        { key: "crowd", path: "/command/crowd", labelKey: "cmd.page.crowd", fallback: "Crowd Analytics", Icon: Users },
        { key: "traffic", path: "/command/traffic", labelKey: "cmd.page.traffic", fallback: "Vehicle & Vessel", Icon: Car },
        { key: "person", path: "/command/person", labelKey: "cmd.page.person", fallback: "Person Tracking", Icon: UserSearch },
        { key: "intrusion", path: "/command/intrusion", labelKey: "cmd.page.intrusion", fallback: "Intrusion Detection", Icon: Shield },
      ]
    },
    {
      section: "Evidence & Case",
      sectionKey: "nav.section.evidence",
      items: [
        { key: "evidence", path: "/command/evidence", labelKey: "cmd.page.evidence", fallback: "Evidence Review", Icon: Film },
        { key: "missions", path: "/command/missions", labelKey: "cmd.page.missions", fallback: "Case & Missions", Icon: ClipboardList },
        { key: "models", path: "/command/models", labelKey: "cmd.page.models", fallback: "Detection Models", Icon: Cpu },
        { key: "exports", path: "/command/exports", labelKey: "cmd.page.exports", fallback: "Export Requests", Icon: ExternalLink },
        { key: "reports", path: "/command/reports", labelKey: "cmd.page.reports", fallback: "Reports & Briefs", Icon: FileText },
      ]
    }
  ],
  senior_officer: [
    {
      section: "Surveillance",
      sectionKey: "nav.section.surveillance",
      items: [
        { key: "tactical-map", path: "/command/tactical-map", labelKey: "cmd.page.map", fallback: "Tactical Map", Icon: MapPin },
        { key: "multi-drone", path: "/command/multi-drone", labelKey: "cmd.page.multidrone", fallback: "Multi-Drone View", Icon: Radio },
        { key: "alerts", path: "/command/alerts", labelKey: "cmd.page.alerts", fallback: "Live Alerts Feed", Icon: Bell },
      ]
    },
    {
      section: "AI Analytics",
      sectionKey: "nav.section.analytics",
      items: [
        { key: "crowd", path: "/command/crowd", labelKey: "cmd.page.crowd", fallback: "Crowd Analytics", Icon: Users },
        { key: "traffic", path: "/command/traffic", labelKey: "cmd.page.traffic", fallback: "Vehicle & Vessel", Icon: Car },
        { key: "person", path: "/command/person", labelKey: "cmd.page.person", fallback: "Person Tracking", Icon: UserSearch },
        { key: "intrusion", path: "/command/intrusion", labelKey: "cmd.page.intrusion", fallback: "Intrusion Detection", Icon: Shield },
      ]
    },
    {
      section: "Evidence & Case",
      sectionKey: "nav.section.evidence",
      items: [
        { key: "evidence", path: "/command/evidence", labelKey: "cmd.page.evidence", fallback: "Evidence Review", Icon: Film },
        { key: "missions", path: "/command/missions", labelKey: "cmd.page.missions", fallback: "Case & Missions", Icon: ClipboardList },
        { key: "models", path: "/command/models", labelKey: "cmd.page.models", fallback: "Detection Models", Icon: Cpu },
        { key: "exports", path: "/command/exports", labelKey: "cmd.page.exports", fallback: "Export Requests", Icon: ExternalLink },
        { key: "reports", path: "/command/reports", labelKey: "cmd.page.reports", fallback: "Reports & Briefs", Icon: FileText },
      ]
    },
    {
      section: "Command & Oversight",
      sectionKey: "nav.section.oversight",
      items: [
        { key: "aggregated", path: "/command/aggregated", labelKey: "cmd.page.aggregated", fallback: "District & State View", Icon: BarChart3 },
        { key: "admin", path: "/admin", labelKey: "nav.admin", fallback: "Admin & Compliance", Icon: ShieldCheck },
        { key: "audit", path: "/audit", labelKey: "admin.page.audit", fallback: "Audit Log Explorer", Icon: FileText },
      ]
    }
  ],
  system_administrator: [
    {
      section: "Operations & Audit",
      sectionKey: "nav.section.sysops",
      items: [
        { key: "ingest", path: "/ingest", labelKey: "nav.ingest", fallback: "Ingest Operations", Icon: Gauge },
        { key: "admin", path: "/admin", labelKey: "nav.admin", fallback: "Admin & System Settings", Icon: ShieldCheck },
        { key: "audit", path: "/audit", labelKey: "admin.page.audit", fallback: "Audit Log Explorer", Icon: FileText },
      ]
    }
  ],
  drone_operator: [
    {
      section: "Flight & Stream Console",
      sectionKey: "nav.section.edge",
      items: [
        { key: "edge-dash", path: "/edge/dashboard", labelKey: "nav.edgeDashboard", fallback: "Dashboard", Icon: LayoutDashboard },
        { key: "edge-mission", path: "/edge/mission-control", labelKey: "nav.missionControl", fallback: "Mission Control", Icon: Target },
        { key: "edge-live", path: "/edge/live-monitoring", labelKey: "nav.liveMonitoring", fallback: "Live Monitoring", Icon: Video },
        { key: "edge-gps", path: "/edge/gps-telemetry", labelKey: "nav.gpsTelemetry", fallback: "GPS & Telemetry", Icon: Compass },
        { key: "edge-buffer", path: "/edge/buffer-and-change", labelKey: "nav.bufferAndChange", fallback: "Buffer & Change", Icon: Scan },
        { key: "edge-frame", path: "/edge/frame-selection", labelKey: "nav.frameSelection", fallback: "Frame Selection Analytics", Icon: Image },
        { key: "edge-incidents", path: "/edge/incidents", labelKey: "nav.incidents", fallback: "Incidents", Icon: AlertTriangle },
        { key: "edge-evidence", path: "/edge/evidence-management", labelKey: "nav.evidenceManagement", fallback: "Evidence Management", Icon: Archive },
        { key: "edge-health", path: "/edge/system-health", labelKey: "nav.systemHealth", fallback: "System Health", Icon: Server },
        { key: "edge-audit", path: "/edge/logs-audit", labelKey: "nav.logsAudit", fallback: "Logs & Audit", Icon: ClipboardList },
        { key: "edge-settings", path: "/edge/settings", labelKey: "nav.settings", fallback: "Settings", Icon: Settings },
      ]
    }
  ]
};
