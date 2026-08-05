# DVAS — Dashboards, User Roles, and Access Architecture
### Technical Reference Document (Revision 4 — 4-Role Model + 13-Page Edge Dashboard)
**Project:** Drone Video Analytics System (DVAS) — Kerala Police
**Prepared by:** Quantumweave Intelligence Pvt. Ltd.
**Basis:** DVAS Architecture & Compliance Document + Production Roadmap

---

## 0. What Changed in This Revision

**Field Supervisor** and **Supervisory Officer** have been removed. The system now has **four roles**: Drone Operator, Control Room Officer, Senior Officer, System Administrator.

Their responsibilities didn't disappear — they were redistributed along the two natural fault lines already in the system (operational vs. approval/oversight):

| Removed Role | Its Functions Went To | Why |
|---|---|---|
| **Field Supervisor** (ground-level operational oversight, corroboration, mission/case management, export *requests*, zone-config *requests*) | **Control Room Officer** | CRO already sits at the first point of confirmed-event contact; absorbing field-supervision duties makes it the single operational role instead of splitting "sees the alert" and "decides what to do about it" across two logins. |
| **Supervisory Officer** (export *approval*, jurisdiction-wide evidence/audit review, retention & legal-hold approval, forensic-log full access, zone-config authority, Section 65B sign-off) | **Senior Officer** | Senior Officer already held the top-of-chain approval/policy function; absorbing district-level approval authority makes it the single approval/oversight role instead of splitting approval power across a mid-tier that no longer exists. |

**Structural consequence you should be aware of before this goes into the RBAC schema:** in the original 6-role model, the Senior Officer was deliberately kept *aggregate-only*, explicitly barred from drilling into a single active operation (Section 8.3's "even senior roles are restricted"). With Supervisory Officer gone, **someone** still has to approve exports and review jurisdiction-level evidence — and the only role left above Control Room Officer is Senior Officer. So Senior Officer's access now necessarily includes jurisdiction-level drill-down *in addition to* the aggregated cross-district view. This is a real widening of that role's access footprint, not a cosmetic renaming — flagging it so it's a conscious decision, not a side effect.

It also means **separation of duties is thinner** than the original 6-role design: Control Room Officer is now both "first responder to the alert" and "the person who corroborates and requests action," and Senior Officer is now both "district approver" and "state-level policy authority." If Dr. Sabu wants to preserve stronger separation later, reintroducing a mid-tier role is the easiest lever — but as specified, here's the 4-role model.

---

## 1. Design Principle Recap (unchanged)

The proposal document is explicit: **the dashboard layer is a visualisation/interaction layer only — it never performs analytics, stores evidence, or makes decisions** (Section 7). Every dashboard sits on top of:

- Authentication (OIDC/OAuth2, short-lived JWT)
- RBAC (which dashboard/module a role can even open)
- ABAC (whether *this* access, *right now*, in *this* context is valid — mission, jurisdiction, time window, case association)
- Audit logging (every view/action is written to an immutable log)

**RBAC decides the door, ABAC decides whether it opens today, the audit log records that you tried the handle.**

---

## 2. Dashboards Required (still 4 — dashboard count doesn't change, only who sees what inside them)

| # | Dashboard | Hosted At | Primary Users | Core Purpose |
|---|---|---|---|---|
| 1 | **Edge Operator Dashboard** | Operator laptop (local, edge) | Drone Operator | Flight/stream safety monitoring only |
| 2 | **Ingest & Streaming Operations Dashboard** | Central ingest server (HQ) | System Administrator | Infrastructure health monitoring |
| 3 | **Central Command & Intelligence Dashboard** | Police HQ / District command centre | Control Room Officer, Senior Officer | Live alerts, analytics, evidence review, decision support, approvals |
| 4 | **Administrative & Audit Dashboard** | Police HQ (restricted zone) | System Administrator, Senior Officer | User/role management, policy configuration, audit & compliance review |

The Central Command Dashboard remains **one codebase with role-filtered views** — Control Room Officer and Senior Officer log into the same application but see different widgets/scopes based on RBAC + ABAC.

---

## 3. User Login Roles Required (4 roles)

| Role | Function (post-redistribution) | Device/Location |
|---|---|---|
| **1. Drone Operator** | Pilots the drone, manages the edge laptop | Field, operator laptop |
| **2. Control Room Officer** | Monitors live feeds/alerts, corroborates with ground reality, manages active missions/cases, requests evidence export and zone changes | Control room, HQ/district |
| **3. Senior Officer / Command** | Approves exports, retention & legal holds; full jurisdiction and aggregated cross-district evidence/audit review; policy and Section 65B sign-off | HQ, district/state command centre |
| **4. System Administrator** | Infrastructure, RBAC/ABAC policy config, credentials, token lifecycle | HQ (restricted) |

---

## 4. How Each Role Affects / Interacts With the Others

```
Drone Operator
     │  (flies drone, produces frames — cannot see any analytics)
     ▼
Central Ingest + GPU Analytics  ──► Confirmed Events
     │
     ▼
Control Room Officer  ◄────────────── receives live alerts, views evidence for active mission(s),
     │                                 corroborates with ground reality, manages case/mission status,
     │                                 REQUESTS export / zone changes (cannot approve either)
     ▼
Senior Officer / Command  ◄────────── APPROVES exports, retention changes, legal holds;
     │                                 full jurisdiction evidence + audit review; aggregated
     │                                 cross-district view; sets policy; signs Section 65B certs
     ▼
System Administrator  ──────────────► configures RBAC/ABAC for ALL of the above,
                                       provisions/revokes credentials, cannot view evidence content;
                                       own config-change history is reviewed by Senior Officer
```

Key interaction rules:

1. **Drone Operator → Control Room Officer**: still strictly one-directional. The operator never sees analytics; the officer never controls the drone.
2. **Control Room Officer → Senior Officer**: this is now the *only* approval boundary in the system. CRO can act on and request things (export, zone/perimeter change, retention exception) but every one of those requests terminates at Senior Officer for approval — there's no intermediate sign-off step anymore. Expect this single officer to be a busier approval queue than in the 6-role model.
3. **Control Room Officer's scope is now mission-based only** (not jurisdiction-wide) — it doesn't inherit the old Field Supervisor's slightly broader "ground-level oversight" scope beyond the active mission(s) assigned to it via ABAC.
4. **Senior Officer's scope is now two-tiered by necessity**: (a) jurisdiction-level drill-down for approvals and case review — the old Supervisory Officer job — and (b) aggregated cross-jurisdiction rollups for command-level awareness — the old Senior Officer job. Both live under one login now, so the dashboard needs a clear UI distinction between "my jurisdiction, live detail" and "everything, aggregated" views (see Section 6.3).
5. **System Administrator ↔ everyone**: unchanged — full RBAC/ABAC/credential control, explicitly walled off from evidence content (Section 17.1). With Supervisory Officer gone, **Senior Officer is now the sole reviewer of the Administrator's audit trail** — there is no one else left to do it.

---

## 5. Module Access Matrix — What Each Role CAN and CANNOT Access

Legend: **F** = Full access · **V** = View only · **S** = Scoped (mission/jurisdiction/time window via ABAC) · **—** = No access

| Module | Drone Operator | Control Room Officer | Senior Officer | System Administrator |
|---|---|---|---|---|
| Live video feed status (not content) | F | V | — | — |
| Buffer/stream health | F | V | — | V |
| Live confirmed alerts | — | S (active mission) | S (jurisdiction + aggregated) | — |
| Analytics results (crowd, vehicle, intrusion, etc.) | — | S (active mission) | S (jurisdiction + aggregated) | — |
| Evidence clip playback | — | S (active mission only) | S (jurisdiction, incl. closed cases + aggregated read) | — |
| Evidence export/download | — | Request only | **Approve + F** | — |
| Forensic logs (CSV/JSON) | — | V (active mission) | **F** (jurisdiction + aggregated) | — |
| Zone/perimeter configuration | — | Request only | **F** | — |
| Case/mission management | — | **F** | Oversight / V | — |
| Retention policy configuration | — | — | Approve/request-up | F |
| Legal hold management | — | — | Approve/initiate | Enforce (technical) |
| RBAC/ABAC policy engine | — | — | — | F |
| User provisioning/de-provisioning | — | — | — | F |
| Token issuance/revocation | — | — | Emergency trigger | F |
| Ingest/GPU/infra health metrics | — | — | — | F |
| Audit log database (all roles) | — | — | **F** (only reviewer left) | V (own actions only) |
| Section 65B certificate generation | — | Request | **F** (prepare + sign-off) | — |
| Dashboard/system configuration | Local device only | — | — | F |

Critical negative constraints (unchanged from the source document, still enforced):

- **Drone Operator still sees nothing beyond flight/stream health** — no analytics, no alerts, no evidence, regardless of role consolidation elsewhere (Section 7.1).
- **System Administrator still never sees evidence content by default** (Section 17.1) — removing mid-tier roles doesn't touch this wall.
- **No role has direct storage/database access** — everything is API-mediated with RBAC+ABAC re-checked per request (Sections 3.6, 9.1).
- **New consideration**: with only Senior Officer able to review the Administrator's audit log, that pairing (Admin config changes ↔ Senior Officer review) is now the *entire* check-and-balance for infrastructure changes. If Senior Officer is unavailable or the account is compromised, this control has a single point of failure — worth a mitigation note in the SOP (e.g., a secondary Senior Officer account or scheduled peer review).

---

## 6. Dashboard-by-Dashboard Page Breakdown

### 6.1 Edge Operator Dashboard (local, on operator laptop) — updated to 13 pages

**Role using this dashboard:** Drone Operator only. Everything below is re-scoped to fit the operator's hard boundary from the proposal (Section 7.1): **no confirmed incidents, no analytics content, no evidence playback, no audit trail of other roles.** Three of your thirteen page names collide with terms used elsewhere in the system for exactly that restricted content — flagged below with how each is redefined to stay compliant rather than dropped.

| # | Page | Contents | Display Behaviour |
|---|---|---|---|
| 1 | **Dashboard** | Home/overview: stream status tile, buffer % tile, upload status tile, connectivity tile, active-mission ID (read-only) | Summary tile grid, traffic-light colour coding, no drill-down into analytics |
| 2 | **Mission Control** | Assigned mission ID, flight plan/route reference (as pushed from Control Room), mission start/end controls, geofence boundary (read-only, for the operator's own awareness) | Read-mostly view; only actionable controls are "acknowledge mission" / "start" / "end" — no tactical decision content |
| 3 | **Live Monitoring** | Live video preview (own feed only), frame rate, resolution, encoder status | Traffic-light indicators, no overlays, no analytics boxes — this is the old "Home/Live View" |
| 4 | **GPS & Telemetry** | Drone GPS position, altitude, speed, heading, battery %, RF/signal strength | Map pin (own drone only) + telemetry readout, no historical trail beyond current flight |
| 5 | **Buffer Analytics** | Rolling 60-second buffer fill %, overwrite cycle indicator, oldest/newest frame timestamp | Progress bar + timestamp readout — this is the old "Buffer Health," renamed |
| 6 | **Change Detection** | "X candidate timestamps flagged in last minute" — numeric/statistical only | Counters and a rate sparkline; **no preview of what was flagged** (prevents operator inference of incident content, per Section 4.2's "does not decide whether the change is meaningful") |
| 7 | **Frame Selection Analytics** | "Y frames selected and forwarded / minute," redundancy-reduction ratio | Numeric counters + rate chart; no thumbnail previews of selected frames |
| 8 | **Upload Monitoring** | Frame/clip upload queue status, retry count, bandwidth used, ingest-server reachability | Status list + progress indicators, no content of what's being uploaded |
| 9 | ⚠️ **Incidents** | **Redefined scope: local/system incidents only** — hardware faults, connectivity drops, upload failures, buffer overrun warnings. **Not** confirmed policing incidents (those are GPU-cluster-authoritative per Section 4.4 and belong to Control Room Officer/Senior Officer only) | Alert list, severity-tagged, "report to Control Room" action button |
| 10 | ⚠️ **Evidence Management** | **Redefined scope: local clip lifecycle only** — "clip staged," "uploading," "upload confirmed, local copy discarded," local storage used. **Not** clip browsing, playback, or evidence review (per Section 3.3: local copy is discarded after upload; the operator has nothing left to manage past that point) | Status cards per clip, no video player, no thumbnails |
| 11 | **System Health** | Laptop CPU/RAM/disk/battery, network throughput, thermal status | Gauges/sparklines, standard device-health widget |
| 12 | ⚠️ **Logs & Audit** | **Redefined scope: this operator's own local action log only** — stream start/stop, buffer restarts, anomaly reports filed, login/logout. **Not** the central audit trail (that's the Administrative & Audit Dashboard, Senior Officer/System Administrator only) | Read-only chronological list, exportable by the operator for their own record, not searchable across other users |
| 13 | **Settings** | Stream quality, buffer duration (if configurable within policy limits), notification preferences, device pairing/credentials, network config | Form-driven; anything touching retention/buffer-duration policy should be **read-only here and only editable centrally** by System Administrator, to avoid the edge device silently drifting from the retention policy set at HQ |

---

### 6.2 Ingest & Streaming Operations Dashboard (System Administrator) — unchanged

| Page | Contents | Display Behaviour |
|---|---|---|
| **Feed Overview** | All active drone feeds: ID, operator, mission, status, heartbeat | Sortable/filterable table, colour-coded |
| **Queue & Throughput** | Queue depth per drone, back-pressure, frames/sec | Time-series charts, threshold banners |
| **GPU Cluster Load** | GPU utilisation %, batch size, latency | Bar/line charts per node |
| **System Alerts** | Infra-level alerts only | Severity-tagged alert feed |
| **Audit (own actions)** | Admin's own config-change history | Read-only log table (this is what Senior Officer reviews from the other side) |
| **User & Role Management** | Create/suspend/modify accounts, assign roles, ABAC attributes | Form-driven CRUD, auto-logged |

---

### 6.3 Central Command & Intelligence Dashboard (now 2 role-filtered views instead of 4)

| Page | Contents | Control Room Officer | Senior Officer | Display Behaviour |
|---|---|---|---|---|
| **Live Situational Map** | Geospatial view, event markers, zone overlays | S — active mission(s) | S — jurisdiction + aggregated toggle | Map pins colour-coded by event/confidence; auto-refresh |
| **Live Alerts Feed** | Confirmed-event stream: type, confidence, drone, timestamp | Full for active mission | Full, jurisdiction + aggregated | Card feed, filterable, acknowledgment button |
| **Multi-Drone Situational View** | Grid of active drones with mini analytics | Assigned drones only | All in jurisdiction / aggregated toggle | Tiles with sparkline trends |
| **Evidence Review & Playback** | Clip list, secure playback, annotated frames | Active mission only | Jurisdiction (incl. closed cases) + aggregated read | Video player with pre/post-event markers; export button visible but **disabled** for CRO (request-only) |
| **Forensic Log Viewer** | CSV/JSON structured logs | View, active mission | **Full**, jurisdiction + aggregated | Tabular; CSV export only enabled for Senior Officer |
| **Crowd/Traffic Analytics** | Density heatmaps, vehicle/traffic summaries | Active mission | Jurisdiction + aggregated | Heatmap overlay, time-slider scrub |
| **Case/Mission Management** | Missions/cases, status, assigned personnel | **Full** — create/update/close | Oversight view only | Kanban/table, status transitions logged |
| **Zone/Perimeter Configuration** | Define/edit restricted zones, perimeters | Request form only | **Full** — create/approve/edit | Map-drawing tool for Senior Officer; read-only + "request change" for CRO |
| **Evidence Export Request Queue** | Pending requests: requester, justification, status | Submit request, track own requests | **Approve/reject queue**, mandatory comment | Approval workflow UI, fully audit-logged |
| **Retention & Legal Hold Requests** | Requests for extended retention / legal hold | Submit request | **Approve/initiate** | Status tracker with initiating authority recorded |
| **Aggregated District/State View** | Cross-jurisdiction rollups: incidents, response times, drone utilisation | — (not visible) | **Full** — this is exclusively Senior Officer's view now | KPI tiles, charts, no operational drill-down controls here (drill-down happens on the jurisdiction-scoped pages above) |
| **Section 65B Certificate Workflow** | Prepare/sign electronic-record certificates | Request only | **Full** — prepare and sign | Form-driven, generates the Annexure B document |
| **Reports** | Scheduled/on-demand operational summaries | Generate for own mission(s) | Generate for jurisdiction/aggregated | PDF/CSV, watermarked with user + timestamp |

Display principle (unchanged): ABAC is re-checked per request, not just at login — a Control Room Officer cannot page into evidence outside their assigned mission by manipulating a URL parameter.

---

### 6.4 Administrative & Audit Dashboard (now System Admin + Senior Officer only)

| Page | Contents | Visible To | Display Behaviour |
|---|---|---|---|
| **User & Role Management** | Create/edit/deactivate accounts, assign role, set ABAC attributes | System Administrator (full) | Form CRUD, auto-logged |
| **Policy Configuration** | Retention durations, legal-hold toggles, ABAC rule editor | Sys Admin (technical enforcement), Senior Officer (approval) | Rule-builder with before/after diff |
| **Token & Session Management** | Active sessions, force-revoke, expiry | System Administrator | Table with revoke action |
| **Audit Log Explorer** | Full searchable trail: who/what/when/where/what-data | **Senior Officer — full**; System Admin — own actions only | Advanced search, append-only, hash-chained; Section 65B-ready export |
| **Legal Hold Tracker** | Active holds, linked evidence, initiating authority | Senior Officer | Status table, hold/release logged |
| **System Configuration** | Retention job schedules, backup status, infra config | System Administrator | Config panel, confirmation modal |
| **Compliance Reports** | Periodic access-pattern / anomaly summaries | Senior Officer | Auto-generated report cards |

---

## 7. One-Line Summary Per Role

- **Drone Operator** — flight/stream health only; zero intelligence visibility. Unchanged.
- **Control Room Officer** — the single operational role: live monitoring, ground corroboration, case/mission management, evidence review for active missions, and *requests* (not approvals) for export/zone/retention changes.
- **Senior Officer / Command** — the single approval and oversight role: approves everything CRO requests, holds full jurisdiction + aggregated evidence and audit access, signs Section 65B certificates, sets policy, and is the sole reviewer of the System Administrator's own audit trail.
- **System Administrator** — full infra/RBAC/ABAC/credential control; explicitly walled off from evidence content; own actions reviewed only by Senior Officer.

---

*This document is derived from Sections 2–3, 7–8, and 15–17 of the DVAS Architecture & Compliance proposal, adjusted per explicit instruction to remove the Field Supervisor and Supervisory Officer roles. The redistribution keeps every original permission accounted for — nothing was dropped, only reassigned to Control Room Officer (operational) or Senior Officer (approval/oversight). The single-point-of-failure note in Section 5 (Admin ↔ Senior Officer audit pairing) and the widened Senior Officer scope in Section 0 are the two structural trade-offs worth flagging to Dr. Sabu before this goes into the RBAC schema.*

*Revision 4 update: the Edge Operator Dashboard was expanded from 6 to 13 pages (Section 6.1) to match the implemented laptop UI. Three pages — Incidents, Evidence Management, and Logs & Audit — share names with restricted-content modules used elsewhere in the system, so their scope was explicitly redefined (local/system-level only) rather than left ambiguous, to preserve the Section 7.1 boundary that the Drone Operator never sees confirmed incidents, evidence content, or the central audit trail.*
