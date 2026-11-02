# DVAS — Evidence, Exports & Reports: logical and practical issues (UI + backend)

> Review of branch `runpod-v1` at commit `3a7a21a` · 2 Nov 2026 · scope: dashboard backend (`DVAS_Dashboard/backend`),
> command UI (`DVAS_Dashboard/frontend/src`), and how they consume analytics data (`dvas_analytics`).
> Companion documents: `01_Report_Generation_Assessment.md` (what the reports could show with today's data) and
> `02_Evidence_and_Reporting_Implementation_Plan.md` (how to fix it, in phases).
>
> **Method.** Code reading with file:line references; every finding was traced through the code path. The live pod
> was **stopped** during this review, so the findings marked *(verify on pod)* still need confirming against real data.
> No code was changed for this review.

**Severity scale**
- 🔴 **Critical**: wrong or unprovable evidence, an authorisation hole, or a legal claim the system can't back.
- 🟠 **High**: wrong numbers in official reports, or a broken workflow for a role.
- 🟡 **Medium**: misleading UI, performance or maintainability risk.
- 🟢 **Low**: polish.

---

## Summary

| # | Sev | Area | Finding (one line) |
|---|---|---|---|
| B1 | 🔴 | Evidence sync | Image lookup uses a hard-coded DB URL + password → on the pod every lookup fails silently *(verify on pod)* |
| B2 | 🔴 | Evidence sync | Fallback attaches **unrelated** images (latest image of that type from any drone/mission) to evidence |
| B3 | 🔴 | Retention | Legal hold and `retention_until` (90 d) exist only in the DB; MinIO deletes the images after 3–14 days |
| B4 | 🔴 | Integrity | No SHA-256 / hash of any evidence image, bundle or report; 65B text claims a cryptographic chain of custody |
| B5 | 🔴 | 65B bundle | Certificate is re-generated on each download, cites the repealed Evidence Act 1872 and has no hash values |
| B6 | 🔴 | RBAC | `DELETE /api/evidence/{id}` allowed for **any** logged-in user (operators included); MinIO object left behind |
| B7 | 🔴 | RBAC | Image proxy serves any key in any bucket (watchlist, faces, report PDFs) to any logged-in user, unaudited |
| B26 | 🔴 | Missions | `GET /missions` invents missions with a hard-coded **real officer's name** as operator; the AAR prints it above a signature line |
| B8 | 🟠 | RBAC | CRO jurisdiction filter is skipped when a `drone_id` or `mission_id` filter is given; `/evidence/meta` is unscoped |
| B9 | 🟠 | 65B bundle | `?token=` path maps Keycloak role names wrongly → everyone becomes CRO → **seniors get 403** on bundles |
| B10 | 🟠 | Data model | AI detections never become `alerts`; reports' alert/SLA numbers only count edge incidents + model suggestions |
| B11 | 🟠 | Reports | Weekly report ignores the chosen week; utilisation ignores the period; daily uses UTC dates for an IST force |
| B12 | 🟠 | Reports | After-Action Report silently falls back to **all** alerts/evidence of the drone (other missions) |
| B13 | 🟠 | Reports | Flight hours inflated (`max(0.5 h)` per mission); readiness uses *today's* drone status for a past period |
| B14 | 🟠 | Reports | Jobs run in a fire-and-forget task; a restart leaves reports "generating" forever; `params`/`jurisdiction` never saved |
| B15 | 🟠 | Exports | Decision can be flipped after approval; senior can self-approve (direct export) without a second officer |
| B16 | 🟠 | Alerts UI | Each edge incident appears twice (incident + "vision DETECTION"); "critical" never shows (`severity` not stored) |
| B17 | 🟡 | Reports PDF | Latin-1 only: `—`, `₹`, Malayalam names print as `?`; UTC timestamps; silent truncation (top 8/15/20 rows) |
| B18 | 🟡 | Reports PDF | No images, no maps, no analytics data (plates, counts, zones) — reports are tables of IDs |
| B19 | 🟡 | UI | Report page promises content the PDFs don't have; CRO sees an always-empty reports list; "AAR Report" button 403s for CRO |
| B20 | 🟡 | UI | `prompt()`/`alert()` for legal hold, export justification and errors; no confirmation, no reason codes |
| B21 | 🟡 | Performance | Every command tab polls 10 endpoints every 5 s (incl. 9 `COUNT`s on `evidence`) while a WebSocket is open |
| B22 | 🟡 | Data model | All timestamps are `String` columns; comparisons/`LIKE` on text; mixed `Z`/`+00:00` formats |
| B23 | 🟡 | Evidence sync | Cursor in memory, 100 events/30 s, `>=` vs `=` ties; restart > 24 h loses events; jurisdiction defaults to "Thiruvananthapuram" |
| B24 | 🟡 | KPIs | Evidence KPI (strict scope) ≠ evidence page (lenient scope) → "0" vs "14"; `alerts_24h` has no 24 h filter |
| B27 | 🔴 | Analytics | **Crowd analyzer never initialised** since `819ef2f` (startup hook removed) → no crowd data at all; `/health` still "ok" |
| B28 | 🟠 | Analytics | Intrusion, vessel, anomaly never write `confirmed_events` → their events never become evidence or alerts |
| B29 | 🟠 | Analytics | "Confirmed" events written per object **per frame** (person ≥ 0.15, every vehicle; crowd/theft without cooldown) → evidence flood |
| B30 | 🟠 | Analytics | Data-quality group: schema drift (face tables), ANPR watchlist lost on restart, tracker collisions, plate overwrite, silent upload failures, fabricated recovery rows, PII in clear |
| B25 | 🟢 | Wording | "Evidence Vault **Clips**", "Clip Length", "View Evidence ▶" for still images; "Verified & Closed" for merely acknowledged alerts |

---

## 🔴 Critical

### B1 — Evidence image lookup can't reach the analytics database on the pod *(verify on pod)*
- **Where:** `DVAS_Dashboard/backend/services/evidence_sync.py:19`
  `DRONE_DB_URL = "postgresql://dvas:dvas_secret@drone_postgres:5432/dvas"`. The same literal is used at
  `routers/drones.py:92` (mission discovery, B26).
- **Why it breaks:** the pod generates `DVAS_DB_PASSWORD` per volume (`deploy/runpod/bin/gen-secrets.sh`). `drone_postgres` resolves
  to 127.0.0.1, but the password `dvas_secret` is wrong, so `asyncpg.connect` raises, and the exception is swallowed at
  `evidence_sync.py:319-320` (`logger.debug`). Result: evidence rows are created **without any image keys**. It is also a
  hard-coded credential in source (claude.md: "no secret defaults").
- **Impact:** evidence cards show the placeholder icon. The 65B bundle contains `evidence_note.txt` ("No image evidence available") instead of the image.
- **Fix:** use the existing `ANALYTICS_DATABASE_URL` env (set by `dvas-init.sh`, or local compose). Fail loud (warning + metric).
  Better: stop resolving images after the fact (plan Phase E1).
- **Verify on pod:** `select count(*) filter (where primary_image_key<>'') from evidence where source_event_id is not null`.

### B2 — Fallback attaches images from unrelated events
- **Where:** `evidence_sync.py` `_resolve_image_keys()`:
  - person Tier 3 `:86-97`: the latest person identity of **any** drone;
  - vehicle step 6 `:185-196`: the latest plate read anywhere;
  - intrusion `:216-219`: the latest snapshot anywhere;
  - crowd `:265-275`: the nearest heatmap of any drone, then the latest anywhere;
  - person Tier 2 / vehicle step 5 / vessel / anomaly: "nearest in time" on the same drone, with no time window.
- **Impact:** an evidence item can show a face, plate or scene **from a different drone, mission or day**, and it is then
  exported in a 65B bundle as "the confirmed-event image evidence". This is the most serious integrity risk in the module.
- **Fix:** the analyzer that confirms the event must write the exact object keys into the event (`confirmed_events.attributes`).
  Evidence takes only those keys. If none exist, the evidence has **no image**, and the UI says so (plan E1).

### B3 — Legal hold and retention don't protect the images
- **Where:**
  - MinIO lifecycle: `deploy/runpod/bin/dvas-bootstrap.sh:43-64` (heatmaps 3 d; faces, vehicles, plates, intrusion, anomaly, vessels 14 d).
  - The evidence row points into those buckets (`image_bucket`, set in `evidence_sync.py`).
  - Legal hold only sets DB columns (`routers/alerts.py:297-338`).
  - `retention_until = now + 90 d` (`evidence_sync.py:434`) is enforced nowhere.
- **Impact:** a held case loses its pictures after ≤ 14 days, the row still says "LEGAL HOLD", and nobody notices until a court
  bundle comes out empty. `retention_until` lies.
- **Fix:** when evidence is created, **copy** its objects into `dvas-evidence` (no expiry) with versioning + object lock
  (governance mode, retention = policy). Legal hold → MinIO legal hold. A retention job enforces `retention_until` (plan E2).

### B4 — No integrity values anywhere
- **Where:** `models.py:157-180` (Evidence: no hash), `models.py:241-261` (Report: no hash), bundle builder `routers/alerts.py:623-660`,
  report store `routers/reports.py:99-117`.
- **Text that overclaims:**
  - `pdf_service.py:702`: "The chain of custody has been cryptographically preserved."
  - `pdf_service.py:135`: "Chain of custody is maintained by the DVAS audit log (tamper-evident, hash-chained)."

  Only the **audit log** is hash-chained. The evidence bytes are never hashed, so the audit chain can't prove the image wasn't replaced.
- **Fix:** SHA-256 at capture (analyzer, before upload) stored with the object metadata **and** in the evidence row. Re-verify on
  bundle build. Manifest with per-file hashes. Hash of every generated report/bundle stored in its row and in the audit entry (plan E3).

### B5 — The 65B certificate
- **Where:** `pdf_service.py:76-147`, `routers/alerts.py:565-679`.
- **Problems:**
  1. It cites the **Indian Evidence Act 1872, s.65B**. Since 1 Jul 2024 the governing law is the **Bharatiya Sakshya Adhiniyam
     2023, s.63**. Its prescribed certificate (Schedule; Part A by the person in charge, Part B by an expert) asks for the
     **hash value(s) and algorithm** of the electronic record and the device details. **Confirm the exact format with the
     department's legal cell.**
  2. Regenerated on every download: a new "Bundle generated" time each time, so two copies of "the same" certificate differ. Nothing records
     which bytes were handed out.
  3. No device/system identification (pod, image version, analyzer model + version), no capture-time vs ingest-time, no GPS.
  4. Signature lines for a physical signature, but the bundle is a ZIP. No digital signature.
- **Fix:** build the bundle **once** at approval, store it in `dvas-evidence/exports/<id>/` with a manifest + SHA-256, and log the
  hash in the audit chain. Downloads stream the stored bytes. New certificate template per BSA s.63 (plan E4).

### B6 — Anyone can delete evidence
- **Where:** `routers/alerts.py:432-462`. The dependency is `get_current_user`, with no role check. The only guard is `legal_hold`.
- **Impact:** a drone operator (or any account) can delete any non-held evidence row through the API. The MinIO objects stay
  (orphaned), and the deletion is only in the audit log.
- **Fix:** no hard delete. Use `status = 'disposed'` with a disposal reason, by `senior_officer` only, past `retention_until`, and never
  under hold. Object deletion through the retention job only (plan E2).

### B7 — Image proxy is an open door to every bucket
- **Where:** `routers/infra.py:480-525`.
- **Problems:**
  - Any authenticated user can read any key in 9 buckets, including `dvas-watchlist` and `dvas-evidence/reports/*.pdf`
    (served as `image/jpeg`).
  - No scope check against the evidence the user is allowed to see.
  - No audit entry for viewing faces or plates (DPDPA: access to personal data should be accountable).
  - `Cache-Control: public, max-age=86400` lets browsers and shared caches keep face crops for a day.
  - The token travels in the query string (`lib/api.js:31-38`).
- **Fix:** `GET /api/evidence/{id}/image/{n}` resolves the key from the evidence row after the scope check, then audits (sampled per
  session) and serves with `Cache-Control: private, no-store`. Keep the generic proxy only for live annotated frames, restricted to
  their buckets (plan E5).

### B26 — Missions are fabricated, with a named officer as operator
- **Where:** `routers/drones.py:87-163`, inside `GET /api/missions`, which every command tab polls every 5 s (B21).
- **What happens:** it collects mission IDs from the analytics `tracks` table (hard-coded DB URL, `:92`), the drones table and the evidence table. For
  every ID without a `missions` row, it **inserts** one:
  - `operator_name` = the drone's operator or the literal **"Safna S"** (`:147`);
  - `jurisdiction` = "Thiruvananthapuram";
  - `status = "completed"`, with no `ended_at`;
  - `started_at` parsed from the ID text and labelled `+00:00`, although IDs like `MSN-20260914-1318-…` are most likely IST
    wall-clock time.
- **Impact:**
  - The Mission After-Action Report prints "Primary Drone Operator: Safna S" above an operator signature line, for missions
    that person may never have flown (`pdf_service.py:599-616,705-709`).
  - The utilisation report credits them with sorties.
  - A read endpoint performs writes, and a DB connect + 3 `DISTINCT` scans, on every 5 s poll.
- **Fix:**
  - Remove the auto-creation from `GET`. Missions are created by `POST /api/missions` (edge) or `/ingest/mission/start`.
  - Unknown mission IDs found in data are listed as "untracked mission IDs" for an admin to reconcile, with operator = `NULL`.
  - Delete the literal name; review existing `missions` rows created this way (`name LIKE 'Mission %'` and `operator_ids = '[]'`).

---

## 🟠 High

### B8 — Scope bypass on the evidence list
- **Where:** `routers/alerts.py:151-159`: `if jur and not (drone_id or mission_id)`. A CRO who adds any `drone_id` or `mission_id`
  filter sees evidence of every jurisdiction.
- Also:
  - drone operators without `mission_scope` see everything (`:160-162`);
  - `/api/evidence/meta` (`:242-276`) returns all drones, missions and counts unscoped;
  - `/api/alerts` gives operators the last 100 alerts statewide (`:46-47`).
- **Fix:** one scope function (`core/security.scope_filter_for` + `_apply_scope_to_query`) used by every evidence/alert/report query.
  Remove the hand-written OR lists (see B24).

### B9 — Seniors can't download approved bundles from the Exports page
- **Where:** `routers/alerts.py:568-585`. The token branch looks for `senior_officer`/`system_administrator`/… in
  `realm_access.roles`, but Keycloak roles are `supervisor`, `control-room`, `sys-admin`, `drone-operator`
  (`core/security.py:39-44 ROLE_MAP`). Nothing matches, so `role = "control_room_officer"` for every token.
- **Effect:**
  - The UI link always uses `?token=` (`ExportsPage.js:78`), so a senior gets **403 "Not your request"** on bundles that a CRO requested.
  - A drone operator's token is treated as a CRO.
  - The same pattern exists in `routers/edge.py` (`shift-summary` hard-codes `control_room_officer`).
- **Fix:** delete the hand-written decode; use `core.security.get_current_user` (it already reads `?token=`). Better: replace
  token-in-URL downloads with a short-lived signed download link (plan E5).

### B10 — Reports count the wrong "alerts"
- **Where alerts come from:**
  - `routers/incidents.py:173` (edge incident → also an Alert);
  - `routers/analytics.py:1440` (AI model suggestion → also an Alert);
  - `routers/push.py:84,148` (peer push);
  - `services/seed_service.py` (demo).

  **No AI confirmed event creates an alert row.** Confirmed events become **evidence** only (`evidence_sync.py`).
- **Impact:**
  - "Total alerts", "Ack rate", "Mean response", "60 s SLA" in the daily, weekly and AAR reports measure operator notes and model
    suggestions, not detections.
  - An intrusion detected by the AI and acted on by the control room is invisible in every report.
  - Acknowledging a detection in the Alerts feed is not possible (feed shows `alerts` rows only).
- **Fix:** one `alerts` row per confirmed event (dedup on `source_event_id`), with severity, rule, zone, GPS, and evidence link.
  Incidents and suggestions stay in their own tables and get their own report sections (plan R1).

### B11 — Report parameters ignored / wrong day boundaries
- **Where:** `routers/reports.py`.
  - Weekly: `:55-57` takes the **last 500 alerts**, whatever `week_start` is; the PDF then prints a "daily" table of whatever dates appear (`pdf_service.py:396-425`, max 7 rows, sorted ascending, so it may show the *oldest* 7 days of those 500).
  - Utilisation: `:65-70` reads `period_days` but queries **all** missions.
  - Daily: `Alert.ts.like("YYYY-MM-DD%")` on UTC strings (`:43,46`). The UI default date is the UTC date (`CommandContext.js:47`), so
    00:00–05:29 IST belongs to the previous day.
  - Daily lists **all missions ever** (`:49-50`) as "Active missions".
- **Fix:** typed date ranges in IST (`Asia/Kolkata`), converted to UTC `timestamptz` bounds; every query filtered by range + scope (plan R2).

### B12 — After-Action Report borrows other missions' data
- **Where:** `routers/reports.py:83-93` (and the same in the preview at `:355-365`). If a mission has no alerts/evidence with its `mission_id`, the code takes
  **all** alerts/evidence of the same drone, from any mission, any date.
- **Impact:** a debrief signed by the operator and supervisor lists events from other operations. Legally dangerous.
- **Fix:** never fall back silently. Mission time window match (`drone_id` AND `ts BETWEEN started_at AND ended_at`) only as an
  **explicitly labelled** section ("events on this drone during the mission window without a mission tag").

### B13 — Utilisation numbers are made up in places
- **Where:** `pdf_service.py:447,498`: `max(0.5, hours)` turns a 5-minute sortie into 30 min. Missions without `ended_at` count 0 h.
  "Fleet readiness" (`:475,518`) uses the drone's **current** status. Battery is the current value. "Certified Drone Operator
  Activity Rankings" (`:543`) ranks people with no certification data.
- **Fix:** real durations (from ingest mission start/end + first/last frame), "open" missions flagged, readiness only from
  recorded status history (or remove it). Rename the ranking.

### B14 — Report job lifecycle
- **Where:** `routers/reports.py:188`, `asyncio.create_task(...)`.
  - No reference is kept (the task can be garbage-collected).
  - No timeout.
  - A backend restart leaves rows in `generating` forever (demo `RPT-4001`).
  - The fallback `_report_cache` (`:27,116-117`) is process memory: lost on restart, and the row still says `ready`.
  - `Report.params` and `jurisdiction` are never written (`:176-184`), so a report can't be reproduced or audited.
  - The report PDF is stored in `dvas-evidence/reports/` (mixed with evidence).
- **Fix:** a `report_jobs` worker with a timeout, retries and a startup sweep (`generating` → `failed: interrupted`); params, scope and
  data-cutoff time stored; PDF in its own bucket/prefix with a SHA-256 (plan R4).

### B15 — Export approval rules
- **Where:** `routers/alerts.py:525-562`.
  - No check of the current status, so an approved export can be re-decided (approved → rejected → approved). Bundle download depends on the latest state.
  - `direct-export` (`:385-429`) lets a senior create **and** approve their own export (no second officer).
  - Exports cover a single evidence item; there's no case/FIR grouping.
- **Fix:** state machine `pending → approved|rejected` (final). Direct export needs a second senior or is logged as "self-approved"
  with a mandatory case number. Export = case + list of evidence (plan E4).

### B16 — Alerts feed duplicates and severity
- **Where:** `frontend/src/pages/command/AlertsFeedPage.js:64-80`. Every `alerts` row is shown as category **"vision"** /
  badge **DETECTION**. Incidents and suggestions are also inserted into `alerts` with the same id (see B10), so each appears twice.
  `a.severity` is read but the `Alert` model has no `severity` (`models.py:142-154`), so nothing is ever critical.
- **Fix:** follows from B10. The feed reads alerts (AI), incidents and suggestions as three sources, with no copies.

---

### B27 — Crowd analyzer is never initialised (regression from this branch)
- **Where:** `dvas_analytics/crowd/main.py:66` (`analyzer: Optional[CrowdAnalyzer] = None`, never assigned). Commit `819ef2f`
  ("runpod-v1: phases 0-3") removed the `@app.on_event("startup")` block that created `CrowdAnalyzer`, called
  `minio.ensure_all_buckets()` and started the stream, and didn't replace it. Check with `git show 819ef2f -- dvas_analytics/crowd/main.py`.
- **Effect:**
  - `/analyze/frame` → 503.
  - The stream loop throws on `None` and ACKs every frame.
  - No `crowd_density_readings`, no heatmaps, no `crowd_threshold` events.
  - `CROWD_ALERT_THRESHOLD` is no longer applied.
  - `/health` returns `status: ok` with `model_loaded: false`. `deploy/runpod/bin/healthcheck.sh` checks only HTTP 200, so Phases 3–6
    reported crowd as healthy.
- **Fix (P0):**
  - Restore the init (FastAPI lifespan/startup) with the threshold from config.
  - Make `/health` return 503 until the model is loaded, or make `healthcheck.sh` check `model_loaded`.
  - Add a regression test that imports the app and asserts the analyzer is created on startup (CPU, small weights or mocked).

### B28 — Three analyzers never produce evidence
- **Where:**
  - `intrusion/main.py`, `vessel/main.py` and `anomaly/main.py` contain no `write_confirmed_event` call.
  - The gateway's `GET /events/confirmed` (`gateway/main.py:234-242`) reads only `confirmed_events`.
  - `evidence_sync` reads only that endpoint.
- **Effect:** geofence breaches, vessel sightings/proximity alerts and anomaly alerts never become evidence and never reach reports.
  The intrusion items visible in the vault come from the demo seed.
- **Also:** intrusion publishes a fresh `uuid4` as `event_id` to Valkey that doesn't match `intrusion_events.event_id`
  (`intrusion/main.py:582` vs `:619`). The ANPR `/analyze/frame` path labels a theft as `event_type='intrusion'` (`anpr/main.py:259`).
- **Fix (E1):** every analyzer writes one `confirmed_events` row per *incident* through the shared helper, with the module's own
  event table row referenced in `attributes`.

### B29 — "Confirmed" means "seen in a frame"
- **Where:**
  - `person/main.py:663-675`: one `person_detected` event per person **per frame** at confidence ≥ 0.15.
  - `vehicle/main.py:353-362`: one `vehicle_detected` per vehicle per frame, any confidence.
  - crowd: `crowd_threshold` per analysed frame above the threshold, with no cooldown.
  - ANPR: `theft_alerts` one row per processed frame (`ON CONFLICT` never triggers).
- **Effect:**
  - `confirmed_events` and the dashboard evidence table fill with raw detections.
  - Sync falls behind (100 events / 30 s, B23).
  - Evidence counts in reports are meaningless.
  - The vault needs "deduplicate captures" to be usable.
- **Fix (E1):** a per-module confirmation rule, with a minimum track age/confidence and one event per track per rule plus a cooldown. Raw
  detections stay in their module tables.

### B30 — Analytics data-quality group (each affects reports or evidence)
| Issue | Where | Effect |
|---|---|---|
| Schema drift: `face_embeddings.encrypted_embedding/person_object_key/arcface_embedding`, `person_identities.best_person_image_key/mission_id` written by code but created by no migration | `shared/db.py:657-688, 992-1048` vs `db/migrations/002_*.sql` | Face embedding inserts fail on a database built from the migrations (the pod) *(verify on pod)* → face Re-ID and person identities empty |
| ANPR plate watchlist not loaded at startup | `anpr/main.py:148-164` | After any restart **no theft alerts** until plates are re-added; `case_ref` dropped (`:1081-1086`) |
| Vehicle and ANPR trackers share `tracks` keyed by tracker id | `shared/db.py:91-100`, `vehicle/main.py:355`, `anpr/main.py:457` | Unrelated vehicles merged; wrong plate↔vehicle links |
| `plate_reads` overwrites plate text per track, `times_read` never updated | `shared/db.py:376-385` | No OCR history; reports show the last read only |
| `upload_bytes` swallows errors and returns `None`; callers still store the key | `shared/minio_client.py:100-102`; `person/main.py:361-380`; `anomaly/main.py:265,274` | DB points to images that don't exist |
| Anomaly object keys use a per-process counter + seconds since start | `anomaly/alerts/manager.py:53-57`; `anomaly/main.py:265` | Keys collide and overwrite after restarts |
| Intrusion recovery script deletes and re-inserts a mission's events with synthetic GPS/confidence, no provenance flag | `intrusion/recover_mission_events.py:126,151,162,187` | Fabricated rows indistinguishable from real ones |
| Times are processing time (`now()`), never the frame's capture time | all analyzers; `shared/db.py:1425,1554,1598,1869` | Event times drift from reality under load |
| PII stored without encryption: face/body/plate crops in MinIO (no SSE), plate text and subject names in object keys, base64 crops in Valkey result streams | `shared/minio_client.py:91-97`; `person/main.py:1651`; `anpr/main.py:468`; `person/main.py:771` | DPDPA exposure; Valkey data visible to any service |

## 🟡 Medium

### B17 — PDF rendering limits
- **Where:** `pdf_service.py:151-152`. `_s()` encodes to Latin-1 with `replace`, so `—` (used as the default for empty values throughout),
  `₹`, `°` and all Malayalam names print as `?`. Core Helvetica font only.
- Timestamps are raw UTC ISO strings (`[:19]`).
- Rows are cut silently: daily alerts top 20 (`:328`), missions 10 (`:300`), AAR alerts 15 (`:658`), AAR evidence **8** (`:685`), and the
  AAR heading says "Captured Evidence Registry" with no "8 of N".
- **Fix:** embed a Unicode TTF (Noto Sans + Noto Sans Malayalam) with fpdf2's `add_font`. Show IST with the zone. Every table says "showing N
  of M" and continues on the next pages, or annexes the full list (plan R3).

### B18 — Reports don't use the data DVAS actually has
The PDFs contain only IDs, types, confidence and acknowledgement times. Not used:
- evidence images;
- plate text;
- vehicle colour/type;
- crowd counts and peaks;
- intrusion zones and dwell;
- vessel tracks;
- anomaly classes;
- GPS / map of events;
- the mission's frame and telemetry statistics.

Details and what's feasible now in `01_Report_Generation_Assessment.md`.

### B19 — Reports UI says more than it does
- **Where:** `frontend/src/pages/command/ReportsPage.js`.
  - The weekly card promises "per-type analysis" (`:89`); the PDF has none.
  - The daily card promises "evidence items" (`:59`); the PDF shows only a count.
  - The utilisation card says "last 30 days (automatic)" (`:122`); the backend ignores it (B11).
- The CRO sees "Generated Reports" filtered to `requested_by = self` (`routers/reports.py:208-209`), but CROs can't generate, so the
  table is always empty, apart from demo rows. There's no "request a report" flow, although the banner tells them to contact a senior.
- `EvidencePage.js:204-213`: the "AAR Report" button shows for every role; for a CRO it ends in a 403 `alert()`.
- No preview, though `GET /api/reports/{type}/preview-data` exists (`routers/reports.py:288`) and nothing calls it.
- The mission dropdown lists every mission, with no search, filter or date.
- Status needs the 5 s poll to change; there's no notification when ready.

### B20 — Browser `prompt()` / `alert()` for legal actions
- **Where:** `CommandContext.js:234-260` (legal hold reason, direct-export justification); errors via `alert()` throughout.
- No case number, no reason code list, no confirmation of what's being held/exported, and no minimum length. These texts go into
  the legal record. They need a proper modal with a case/FIR number, reason category and free text (min. length), and they should show the evidence being acted on.

### B21 — Polling load
- **Where:** `CommandContext.js:64-90,117`: 10 requests every 5 s per open command tab (kpis, drones, alerts, missions, exports,
  crowd, reports, incidents, suggestions, evidence/meta). `/evidence/meta` alone runs 3 `DISTINCT` + 6 `COUNT` queries
  (`routers/alerts.py:242-276`).
- 10 officers ≈ 20 req/s and 180 count queries per 5 s, while `/api/ws` is connected and already broadcasts telemetry,
  incidents and audit events.
- **Fix:** push changes over the WebSocket (`evidence_created`, `alert_created`, `report_ready`, `export_decided`) and poll slowly (60 s) as a
  fallback. Cache `/evidence/meta` (e.g. 30 s) or keep counters in a summary table.

### B22 — String timestamps
- **Where:** `models.py`: every time column is `String` (`ts`, `started_at`, `requested_at`, `acknowledged_at`, …).
- Range filters compare text (`Evidence.ts >= from_ts`, `routers/alerts.py:185-188`), which only works while every writer uses the same ISO
  format and offset. Today writers mix `…Z`, `…+00:00` and naive strings (seed vs `datetime.isoformat()` vs gateway `ts`).
- The analytics DB uses real `timestamptz` (partitioned tables); the dashboard DB doesn't.
- **Fix:** Alembic-style migration to `timestamptz` (with a cast + backfill), in the dashboard schema only (plan E6).

### B23 — Evidence sync robustness
- **Where:** `evidence_sync.py:326-447`.
  - The cursor `_last_sync_ts` is in memory. After a restart it re-reads 24 h; a backend outage longer than 24 h loses events.
  - `limit=100` per 30 s, so more than 200 events/min fall behind indefinitely.
  - `since` = newest `ts` seen, so ties and out-of-order inserts can be skipped (depends on the gateway's `>`/`>=`).
  - Jurisdiction defaults to "Thiruvananthapuram" when the mission is unknown (`:402`).
  - The event `attributes` (plate, count, zone, …) are **discarded** (only `duration_s` is kept).
- **Fix:** persistent cursor `(ts, event_id)` in the DB; page until empty; store `attributes` JSONB on evidence; unknown jurisdiction
  is `NULL` + a flag, not a guess. Long term: the dashboard reads `confirmed_events` directly (same Postgres) instead of HTTP polling.

### B24 — KPI and list disagree
- **Where:** `routers/drones.py:404-426` (`/command/kpis`, strict `_apply_scope_to_query`) vs `routers/alerts.py:151-159`
  (lenient OR list incl. `NULL`, `""`, "Kerala", "State HQ", "Thiruvananthapuram"). Seen live on 30 Oct: KPI "Evidence 0", vault 14.
- `alerts_24h` (`drones.py:418`) counts all alerts ever, but the tile reads "Alerts (scope)".
- **Fix:** single scope policy (B8). Rename or filter the KPI.

## 🟢 Low

### B25 — Wording that misleads
- **Video wording for still images.** "Evidence Vault Clips", "Clip Length" (`pdf_service.py:639,677`), the ▶ "View Evidence" overlay
  (`EvidencePage.js:129-136`), and "duration_s" are presented as video. In fact evidence is a still image (clips stay on the edge SSD, plan F18).
- **Disposition labels.** The AAR "Disposition" says "Verified & Closed" when an alert was merely acknowledged, and "Under Investigation"
  otherwise (`pdf_service.py:663`).
- **Fixed role labels.** "Supervising Officer … (Senior Officer)" and "(Drone Operator)" are hard-coded labels, whatever the person's actual role is.
- **Shift summary.** `routers/edge.py` `shift-summary` hard-codes `"role": "control_room_officer"` for any token.

---

## What is already good (keep)
- Audit log v2: hash chain, immutability triggers, legal holds on audit entries, access logging (`audit_service.py`,
  `backend/migrations/audit_*.sql`).
- Evidence dedup by source event (`source_event_id` unique) and by shared image (`duplicate_count`).
- Two-person export workflow (CRO requests, senior approves), audited both ways.
- Every report request and download is audited; PDFs carry a per-officer watermark.
- MinIO behind the backend (no public bucket), private buckets, evidence bucket without expiry.
