# Context: Role & API audit fixes (branch `runpod-v1`, 2026-11-02)

This file covers only the changes made to resolve
`DVAS_ROLE_AND_API_AUDIT_COMBINED.md` and `DEAD_UI_FINDINGS.md`. The edge fixes from those
documents (H6, EDG-01…29, F6b–F6d, edge parts of F6e–F6g) were **not** done; they are left
to the edge application owner.

- Base: `cc137b8` (release `20261102-cc137b8`). Head: `b63595e`, pushed to `origin/runpod-v1`.
- Commits:

| Commit | Scope |
|---|---|
| `d455f33` | Backend auth, role gates and jurisdiction scoping (C1, H2, H3, H5, H7, H8, J1–J7, M1–M8) |
| `6ddc20d` | Admin backend (settings, sessions, policy, compliance), audit API fixes, measured NOC telemetry |
| `cb208ec` | Admin, Audit and Ingest pages use real data (ADM, AUD, ING, F2) |
| `b91d4ca` | Command, analytics and monitor pages (CMD, ANA, MON, P1–P15, F2–F5) |
| `3d10d62` | Cookie-only browser auth, shared WebSocket, lighter polling, dead code removed |
| `b63595e` | Docs (`deploy/runpod/README.md`, `deploy/runpod/env/dvas.env.example`) |

---

## 1. Edge application: what is and is not affected

### 1.1 Not touched

- Nothing under `dvas_edge/` was changed.
- Nothing under `DVAS_Dashboard/frontend/src/pages/edge/` was changed.

### 1.2 Shared frontend files that edge pages import (changed; safe for the edge build)

| File | Change | Effect on edge pages |
|---|---|---|
| `lib/api.js` | `getAuthToken()` returns the token **only when `REACT_APP_DEPLOYMENT_TARGET=edge`** (`isEdge`), otherwise `""`. New single-flight `refreshSession()`; `authFetch` and the axios interceptor use it. New helpers `wsUrl`, `backoffMs`, `downloadBlob`, `apiError`. | Edge build: unchanged. The token is still read from `localStorage.dvas_token` and sent as Bearer / `?token=`. The "all" (local compose) build: edge pages are same-origin, so they authenticate with the `dvas_access_token` cookie. `dvas_edge/edge_api_server.py` (around line 1122) already accepts that cookie. |
| `context/AuthContext.js` | On login, the token is stored in `localStorage` only in the edge build. On logout, the edge mission is ended only for `drone_operator` (F5). | Edge build: unchanged. |
| `lib/useDvasSocket.js` | One shared WebSocket for all subscribers, with exponential backoff. URL from `wsUrl("/ws")`, which adds `?token=` only in the edge build. | `pages/edge/Incidents.js` keeps the same hook API (`useDvasSocket(cb) → { connected }`). |
| `lib/useAnalytics.js` | SSE no longer adds `?token=` (cookie). `useAnalyticsFetch` also returns `mutate` and pauses on hidden tabs. | Edge pages do not use this hook. |
| `context/I18nContext.js` | Added Malayalam labels for the edge nav keys (`nav.edgeDashboard` … `nav.settings`). | Text only. |

### 1.3 Backend behaviour changes that reach edge pages or the edge app

Edge pages call these cloud-backend routes. Their rules changed:

| Route | New rule | Edge impact |
|---|---|---|
| `DELETE /api/missions/{id}` | Senior officer / administrator only. Returns 409 if evidence or alerts reference the mission. | **Breaks a call:** `pages/edge/MissionControl.js:460` (delete from mission history) now gets **403** for a drone operator. The edge owner must decide: remove the cloud delete from that action (keep only the local `/edge-api/.../mission/history/{id}` delete), or ask for the rule to change. |
| `PATCH /api/missions/{id}/end` | Only the drone operator who flew the mission, or a senior officer. Returns `already_ended` when repeated. | MissionControl ends its own missions, so this keeps working. |
| `GET /api/missions` | A drone operator sees only their missions (`operator_ids`, `mission_scope`, or their email in `operator_name`). | Lists may be shorter than before. |
| `GET /api/audit-logs` | Non-senior roles see only their own entries (`actor_email`). | `pages/edge/LogsAudit.js` shows only the operator's entries. |
| `GET /api/edge/system-logs`, `POST /api/edge/action-log` | Drone operator only. `action-log` rejects reserved action names (400) and forces `source_module="edge"`. | The fallback in LogsAudit still works for a drone operator. |
| `GET /api/edge/status` | **Removed.** It returned fabricated data. | Edge pages call the edge server's own `/edge-api/api/edge/status`, not this one. |
| `POST /api/audit-logs/sync` | Drone operator only, max 500 records (413 above that), the caller's own rows only. Each row is re-chained through `write_audit_v2` with `request_id=edge:<id>`. Returns `{synced, rejected, received}`. | No current caller found in `dvas_edge` or the frontend. Any future sync client must use this contract. |
| Incidents (`/api/incidents…`) | Create: drone operator, control room officer, senior officer. Delete / resend: owner, or senior officer / administrator. Clear: senior officer / administrator clear everything, others clear their own. | The operator's own incidents work as before. |
| `/api/analytics/suggestions…` | A drone operator lists only their own suggestions. Delete: owner, or senior officer / administrator. | The operator's own suggestions work as before. |
| `POST /api/auth/login`, `/api/auth/refresh` | `access_token` is in the JSON body **only** if the request `Origin` is in `EDGE_CLIENT_ORIGINS` (default `http://localhost:8765,http://127.0.0.1:8765`). Refresh is limited to 30/minute. | **The edge app's origin must be listed** in `EDGE_CLIENT_ORIGINS` (and in `CORS_ORIGINS`), or it receives no token. `deploy/runpod/bin/dvas-init.sh` already adds `http://localhost:8765` to CORS. |
| WebSocket `/api/ws` | `?token=` is accepted on the handshake (or the cookie). Closes with 4401 on a missing, bad or expired token. Messages are filtered per user. | The edge build passes `?token=`. |
| `/api/ingest-server/streams/{id}/purge` | Control room officer, senior officer, administrator (CRO added 2026-11-06; audit-logged). | — |

---

## 2. Backend changes (`DVAS_Dashboard/backend`)

### 2.1 Auth and roles (`core/security.py`)

- JWKS cache with a 1 h TTL, refreshed on an unknown `kid` (30 s cooldown). The issuer must end with `/realms/{KC_REALM}`.
- The Keycloak role is authoritative. Priority: sys-admin > supervisor > control-room > drone-operator. A user with no DVAS role gets 403.
- `get_current_user`: Bearer header or cookie (no query token). `authenticate_ws`: query token or cookie.
- Shared jurisdiction rule (`jurisdiction_clause`, `jurisdiction_visible`):
  - Control room officer: own jurisdiction, plus `Kerala`, `State HQ` and empty/NULL.
  - Senior officer / administrator: everything.
  - The Thiruvananthapuram special case is gone.
  - Used by alerts, evidence, drones, missions, KPIs and reports.
- `services/websocket_hub.py`: `filter_for(msg, user)` filters per role and scope. Expired sessions are closed with 4401.
- CORS uses an explicit list (`CORS_ORIGINS` + `EDGE_CLIENT_ORIGINS`), no regex. API docs only when `DVAS_API_DOCS=1`.

### 2.2 Analytics

- `routers/analytics.py`: one role policy on the router. All 14 stream start/stop routes are **POST**.
- Suggestion `DEPLOYED_TO_GPU` (senior officer / administrator) really applies the model config via `routers.infra.apply_model_config`.
- `core/analytics_client.py`: upstream errors map to real HTTP status codes (503/504/502, upstream 4xx passed through). `soft=True` keeps the old envelope for aggregations.
- `dvas_analytics/shared/auth.py` `install_service_guard`: every analytics service route except `/health` needs `X-Internal-Service-Key`. CORS was removed from the services.
- New `GET /api/analytics/intrusion/config?drone_id=`, backed by a new `GET /intrusion/config` in `dvas_analytics/intrusion/main.py`.

### 2.3 Admin (`routers/admin.py`, new, prefix `/api/admin`)

| Route | Roles | Purpose |
|---|---|---|
| `GET /sessions`, `DELETE /sessions/{id}`, `POST /users/{id}/logout` | Administrator | Keycloak sessions, revoke (audited) |
| `GET /settings` / `PUT /settings` | Senior officer + administrator read; administrator writes | Enforced settings; changes are audited as `system_settings_update` |
| `GET /policy` | Senior officer, administrator | Settings, Keycloak token/session lifetimes, enforced rules |
| `GET /compliance` | Senior officer, administrator | Six computed cards |

### 2.4 Settings (`services/settings_service.py`, table `system_settings`)

| Key | Range | Default |
|---|---|---|
| `evidence_retention_days` | 30–3650 | `EVIDENCE_RETENTION_DAYS` |
| `report_retention_days` | 30–3650 | 365 |
| `audit_retention_days` | 365–3650 | 365 |
| `retention_job_time_ist` | `HH:MM` | `02:30` |

Where the settings are applied:
- Evidence retention date and vault object lock.
- Report-PDF retention (`run_report_retention` deletes PDFs older than the setting; the row becomes `expired`).
- Audit purge minimum.
- Daily retention job time.

### 2.5 Audit (`audit_service.py`, `routers/audit.py`)

- Legal hold:
  - Placing a hold accepts `justification` (alias of `hold_reason`), `legal_authority` and `contact_email`. These are stored in new columns.
  - Releasing a hold needs `release_reason` or `release_notes`.
- `GET /audit-logs/summary` accepts every list filter.
- `actor_session_id` filter added.
- `GET /audit-logs/trends` accepts `range` or `range_val`: `today` (from 00:00 IST), `24h`, `7d`, `30d`. Any other value returns 400.
- New `GET /audit-logs/aggregate?days=` (senior officer / administrator): grouped by role, action and category.
- Chain of custody adds `chain_position`, `chain_length` and `is_genesis`.
- `get_retention_policy()` is async and setting-based.

### 2.6 Command, infra and telemetry

- New `GET /api/command/districts` (senior officer): per-district alerts (24 h), active missions, live drones, evidence.
- `GET/POST /api/models`:
  - Measured GPU memory and GPU name.
  - Service state `true` / `false` / `null` (unreachable).
  - `read_only` for control room officers.
  - Per-model GPU % / fps flagged as estimates.
- `services/system_telemetry.py` and `/infra/health` report only measured values; anything else is `null`:
  - FPS and bitrate from counter deltas.
  - Frame age instead of latency.
  - Totals counted since the ingest server started.
  - Metadata validation from drone rows.
  - Probe success over the last 20 probes.
  - Correct Keycloak realm.
  - Supervisor process check for container alerts.
- New `POST /api/infra/alerts/{id}/ack` (administrator, audited). Acks are cleared when the alert stops firing.
- New `GET /api/ingest-server/health/detail` (administrator). Public `/ingest-server/health` returns only `{reachable, status}`.
- Evidence list: a bare `YYYY-MM-DD` `from_ts`/`to_ts` is an IST calendar day; `to` includes the whole day.
- `ingest_proxy.py`: drone-id validation and per-role drone access; the token-prefix debug print is removed.
- `routers/push.py`: constant-time key compare; `/receive` limited to 120/min.
- `dvas_ingest/ingest/api/main.py`: `/ingest/streams/active` also returns `started_at`.

### 2.7 Migrations (applied at backend start, `STARTUP_MIGRATIONS` in `services/seed_service.py`)

- `migrations/system_settings.sql`: table `system_settings`.
- `migrations/audit_holds_v2.sql`: `audit_legal_holds.legal_authority`, `contact_email`.

Both are idempotent. Both were checked twice over an existing schema on Postgres 16.

---

## 3. Frontend changes (non-edge)

- **Admin** (`pages/AdminDashboard.js`, new `components/admin/AdminPanels.js`):
  - Sessions, policy, settings (with a before/after confirm), legal holds (evidence and audit) with release, compliance, whole-log aggregate.
  - 65B export.
  - The integrity chip reads `is_chain_valid`, `verified_count` and `total_checked`.
- **Audit** (`pages/AuditLogPage.js`), AUD-01…14:
  - Trend field names fixed; the custody proof reads `cryptographic_proof.*`.
  - The legal-hold form sends the backend's field names; 422 errors are shown as text (no more blank page).
  - Purge counters and the enforced minimum days; the archive is labelled as JSON with a SHA-256 manifest.
  - Retention shown from the policy API.
  - Deep links only to pages the role can open (`/command/evidence?q=`, `/command/missions?q=`).
  - The anomaly filter uses the anomaly time window.
- **Ingest** (`pages/IngestDashboard.js`):
  - Unknown values show "—"; frame age replaces latency; counters are labelled "since ingest server start".
  - Infra alerts are acknowledged on the server; the deploy-to-GPU result is shown.
  - The audit tab shows the last 100 events.
  - Delete/clear socket events are handled; times are in IST.
  - The Users tab links to Admin.
- **Command**:
  - Pages pass `droneId`, `user` and `onNavigateTab` to the analytics components (P1, P8–P10).
  - The header shows the user's scope.
  - The evidence modal has legal hold.
  - The aggregated page is senior-officer-only, with a per-district table.
  - Models: control room officers may start/stop modules and deploy the set since 2026-11-06 (audit-logged); the FPS policy stays SO/SA; estimates labelled.
  - Missions use `started_at`.
  - AAR spinner ends on `report_ready` / `report_failed`.
  - `CommandContext` handles incident/suggestion delete and clear events, drops `INC-*`/`SUGG-*` alert copies, and shows ack errors.
- **Analytics components** (Person, Crowd, Traffic, Vessel, Intrusion, Anomaly, MultiDrone, LiveMap):
  - Stream calls use POST.
  - No invented values.
  - Errors are shown; optimistic toggles roll back.
  - Watchlist refetches after changes; the ANPR type filter options match the data.
  - Full-history CSV; intrusion config is read back.
  - Purge: control room officer, senior officer, administrator (CRO added 2026-11-06).
- **Polling:**
  - The bell poll is a 60 s fallback.
  - Polls pause on hidden tabs.
  - Ingest overview polls every 10 s.
- **Removed:**
  - `components/CrowdTrafficAnalytics.js`, `IntrusionReview.js`, `LiveFrame.js` (no importers).
  - The unused `NAV` constant in `AppShell.js`.

## 4. Removed files

- `DVAS_Dashboard/backend/server_monolith_backup.py` and `migrate_roles.py`. Both remain in git history.
- `dvas_analytics/db/0*.sql`: byte-identical copies of `dvas_analytics/db/migrations/`. Docker `init.sh` and RunPod bootstrap use `migrations/`.

## 5. Configuration

| Variable | Default | Purpose |
|---|---|---|
| `EDGE_CLIENT_ORIGINS` | `http://localhost:8765,http://127.0.0.1:8765` | Origins that receive the token in the login/refresh body (edge app) |
| `DVAS_API_DOCS` | off | `1` serves `/docs` and `/openapi.json` |
| `CORS_ORIGINS` | `http://localhost:3000` | Explicit list. On RunPod, `dvas-init.sh` sets the public URL plus `http://localhost:8765` |
| `ANALYTICS_SERVICE_KEY` | — | Required by analytics services (`X-Internal-Service-Key`) |

## 6. Tests

- Backend: 94 passed (`tests/`, excluding `backend_test.py`, which needs a live server). New files:
  - `tests/test_role_audit.py`
  - `tests/test_admin_settings.py`
- Deploy: 9 passed (`deploy/runpod/tests`).
- Analytics: 30 passed. The 2 face-recognition failures are environmental (no ArcFace weights; numpy `_ARRAY_API` error).
- Frontend: the production `craco build` compiles (warnings only).
- Not done: a click-through of the new screens in a running stack.

## 7. Known gaps (need backend or service work)

- Vehicle, ANPR, intrusion and person `/health` report no model name, so the labels are neutral.
- No bulk dismiss for watchlist alerts (one request per alert).
- The crowd CSV export takes no operator / location / mission parameters (the PDF does).
- The anomaly service reports no ego-motion status; the vessel service does not return its proximity threshold.
- `/analytics/vehicle/stats` cannot be filtered by drone or time.
- Edge items listed at the top of this file.
