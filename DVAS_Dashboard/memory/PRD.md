# DVAS — Product Requirements

## Problem
Build the Drone Video Analytics System for Kerala Police per `DVAS_Dashboards_Roles_Access_Architecture.md`: 4 dashboards, 6 roles, RBAC + ABAC + audit log + export approval workflow.

## Architecture
- **Backend**: FastAPI + MongoDB (`/app/backend/server.py`), JWT auth (bcrypt), route-level RBAC, mission/jurisdiction ABAC filter, immutable audit log, WebSocket telemetry pump, Section 65B ZIP/PDF bundle generator.
- **Frontend**: React + Tailwind + shadcn + lucide-react + Recharts + react-leaflet. **DVAS Design System v2 "Soft SaaS"**: light default with soft gray-blue canvas (#F3F4F6) + pure-white cards, deep-slate dark mode (#0F172A). Rounded-14px cards with subtle drop shadows, Royal Blue (#2563EB) accent, semantic pill badges (green/amber/red soft backgrounds). System-wide Light/Dark toggle + EN/ML language toggle (localStorage persisted). Plus Jakarta Sans + Noto Sans Malayalam + JetBrains Mono (tabular figures). Persistent Left Sidebar with active-state light-blue highlight + royal-blue text; Top Bar with global search (⌘K), Ingest health pill, notification bell + drawer, language toggle, theme toggle, user avatar menu. Horizontal in-dashboard tab navigation with cyan underline on active tab.

## Roles & Home Dashboards
| Role | Home |
|---|---|
| Drone Operator | `/edge` |
| Control Room Officer | `/command` |
| Field Supervisor | `/command` (+ can request exports) |
| Supervisory Officer | `/command` + `/admin` (approves exports) |
| Senior Officer | `/command` + `/admin` (aggregated) |
| System Administrator | `/ingest` + `/admin` (own actions only) |

## Implemented (Feb 2026)
- 4 dashboards fully built: Edge Operator, Ingest & Streaming Ops, Central Command, Admin & Audit
- Edge Operator: 6-section spec (Live View, Buffer Health, Change Detection counters, Clip Status chip, Connectivity, Local Controls w/ mandatory anomaly form) + **End-of-Shift PDF summary download**
- Command: KPIs, Live Situational Map (react-leaflet, CartoDB tiles, theme-aware light/dark), Live Alerts + ack, Evidence grid + video playback, Missions table, Analytics charts, Exports approval workflow, Forensic log CSV export, Reports
- Ingest: feeds table, GPU load, queue depth, throughput chart, infra alerts, own-audit view, user CRUD
- Admin: **Hash-chained audit log explorer with per-row tamper indicator + chain-integrity chip**, Users & Roles, Policy config with ABAC rules, Token/sessions, Legal holds, Compliance reports, System configuration with before/after diff confirmation
- Seed data: 6 users, 3 missions, 4 drones, 28 alerts, 14 evidence clips w/ sample MP4s, 4 export requests, 25 hash-chained audit entries
- All endpoints prefixed `/api`; JWT via httpOnly cookie + Authorization bearer fallback
- WebSocket `/api/ws` streams live telemetry + alerts
- Section 65B evidence export bundle (ZIP w/ fpdf2-generated certificate PDF + metadata + video text)
- Edge end-of-shift PDF (`/api/edge/shift-summary`) — operator-scoped, includes buffer stats + anomaly summary + tamper-evident chain reference
- **DLS Phase 1**:
  - `ThemeProvider` (light/dark, localStorage)
  - `I18nProvider` with **English + Malayalam** dictionaries (`Noto Sans Malayalam` font stack) — toggle in TopAppBar
  - CSS-variable design tokens + light-mode slate remap (auto-adapts existing dashboards)
  - Inter typeface + JetBrains Mono tabular numerals (font-feature-settings tnum,zero)
  - Top App Bar: role/jurisdiction chip, Ingest health dot, **notification bell drawer** (last 5 in-scope alerts + "View all alerts →" deep-link), theme toggle, language toggle, clock
  - Collapsible left sidebar (localStorage persisted)
  - Theme-aware Leaflet tiles (light_all / dark_all)
- In-dashboard nav: horizontal top tab bar on Command/Ingest/Admin (cyan underline active tab)
- **Audit tamper-evident chain**: SHA-256 hash chain on `write_audit()`; `/api/audit-logs/verify` returns integrity summary; each `/api/audit-logs` row has a `tamper` flag
- Testing: iteration_1 + iteration_2 both 100% backend/frontend pass; new endpoints curl-verified

## Backlog / P1
- Wire the global search bar to a real command palette (fuzzy match drones/evidence/missions).
- Retention/legal-hold background job (server-side).
- Actual anomaly report persistence.
- WebSocket ingress upgrade fix for realtime telemetry in preview env.
- Notification drawer polish: hover-preview on the bell.

## P2
- Extend Malayalam translation into remaining long-form body copy (analytics chart labels, Ingest sub-page tables, admin policy detail text).
- Full 65B audit-log export button wiring.
- Cross-district Senior Officer heatmap rollups.
