# Evidence & reports review (2 Nov 2026)

Review of evidence storage, export and report generation on branch `runpod-v1` (commit `3a7a21a`). No code was changed.

| Document | Read it for |
|---|---|
| [01_Report_Generation_Assessment.md](01_Report_Generation_Assessment.md) | What evidence/data DVAS stores today, what the four reports use of it, and which better reports are possible **now** vs after changes |
| [02_Evidence_and_Reporting_Implementation_Plan.md](02_Evidence_and_Reporting_Implementation_Plan.md) | Phased plan (P0 hot-fixes → evidence integrity → report rebuild → UI), in the `claude.md` plan format, with tests, risks and open questions |
| [03_UI_and_Backend_Issues.md](03_UI_and_Backend_Issues.md) | Every logical/practical defect found (B1–B30), with severity, file:line, impact and fix |

**Most urgent:** the crowd analyzer has stored nothing since commit `819ef2f` (B27). Its health check still says "ok".

**Start with** the summary table in 03, then the P0 phase in 02: it removes the issues that can produce wrong evidence or
wrong legal documents today. Items marked *(verify on pod)* need a check against live data. The pod was stopped during the review.

## Status

| Phase | State |
|---|---|
| P0 | Code done (3 Nov 2026), unit tests only. Needs an image rebuild and a pod check: crowd `/health` 200 with `model_loaded:true`, `healthcheck.sh` all OK, crowd rows in `crowd_density_readings`, ANPR `watchlist_count` after a restart. Then run `DVAS_Dashboard/backend/scripts/review_synthesized_missions.sql` and decide what to do with each row. |
| E1–E5 | Code done (3 Nov 2026), unit tests only (in-memory DB and MinIO fakes). Needs an image rebuild and the pod checks below. |
| E6, R1–R5, U1 | Code done (2 Nov 2026). Unit tests + a production frontend build; E6/analytics migrations and every report query checked on a real Postgres 16. Needs an image rebuild and the pod checks below. |

P0 differs from the plan in two places:

- **Untracked missions.** `GET /missions` still returns a plain list, so the UI is unchanged. Untracked mission ids are on a separate admin-only endpoint, `GET /api/missions/untracked`, and are no longer looked up on every list call.
- **Image lookup (B2, partly).** Superseded by E1: the dashboard no longer guesses images at all.

### E1–E5: what was built

- **E1 — exact capture.** Analyzers upload the evidence images first (hashed), then write a confirmed event whose `attributes.evidence` block names the exact objects, frame (sequence, capture time), GPS and model (weights hash, `DVAS_GIT_SHA`). Helper: `dvas_analytics/shared/evidence.py`.
  - Confirmed event = an incident. Crowd: one per surge (re-armed below 0.8 × threshold after 60 s). Intrusion: ENTERING/DWELLING, one per track and zone per 60 s. ANPR: watchlist plate, one per plate and mission per 5 min. Person: face-watchlist match. Anomaly: every gated alert. Vessel: proximity alert. Per-frame `person_detected` / `vehicle_detected` events are gone (B29); intrusion, anomaly, vessel and ANPR theft now reach evidence at all (B28).
  - Migration `013_confirmed_events_v2.sql`: text mission id, zone ref, cursor index, and the face/person columns the code wrote but no migration created (B30 — face embeddings could not be stored on the pod).
  - Dashboard sync: persistent cursor (`evidence_sync_state`), paging oldest first with a 2-minute overlap, service-key auth on the gateway. Events without a block only get an image from an exact event/track match; otherwise none (`image_match`).
- **E2 — vault.** Each object is copied to the new bucket `dvas-evidence-vault` (object lock, GOVERNANCE, `EVIDENCE_RETENTION_DAYS`, versioned), created by the backend. Legal hold sets the S3 legal hold on the copies and refuses if MinIO refuses. `DELETE /api/evidence/{id}` now disposes (status change). A retention job (every 6 h) disposes expired items and later removes their vault copies; preview at `GET /api/retention/preview`.
- **E3 — hashes.** SHA-256 at analyzer upload (`x-amz-meta-sha256`), re-checked at the vault copy, record hash (`record_sha256`) in the row and the creation audit entry, report PDFs hashed, `GET /api/evidence/{id}/verify`.
- **E4 — cases and bundles.** `/api/cases` (create, add/remove evidence, close; access by jurisdiction). Exports of a case or a single item; decisions are final (B15); the bundle is built once at approval with `manifest.json`, `SHA256SUMS.txt` and a BSA s.63 certificate (Part A + Part B form), stored in the vault under legal hold. Direct export needs a case number and is marked self-approved.
- **Single-frame `/analyze/frame`** (crowd, vehicle, person, ANPR) follows the same incident rules: crowd shares the surge gate and stores the uploaded image as evidence; vehicle/person write no per-detection event; ANPR writes a gated `theft_alert` (previously every plate was an event and stolen plates were typed `intrusion`).
- **E5 — access.** `GET /api/evidence/{id}/objects/{role}` (scope-checked, 404 out of scope, PII roles for face/plate, `no-store`, view audited once per hour). `/api/minio/image` limits face/plate/watchlist buckets to PII roles and caches privately for 60 s. Signed single-use 60 s links (`POST /api/downloads`) for bundles, reports and evidence images; the evidence screens use them and load images with the Bearer header.

### Decisions taken on open questions (change if wrong)

| Q | Default used |
|---|---|
| Q1 certificate | Template marked "wording pending legal-cell approval"; Part A + blank Part B; no DSC |
| Q2 retention | `EVIDENCE_RETENTION_DAYS=90` (today's value); bundles under legal hold |
| Q3 sync | HTTP from the gateway with a cursor (no direct DB read) |
| Q4 second senior | Not required; direct exports are marked self-approved in the certificate and manifest |
| Q5 PII roles | `PII_VIEW_ROLES=control_room_officer,senior_officer` (admins and operators can't see faces/plates) |
| Q8 migrations | Plain SQL files; dashboard ones applied by the backend at startup (`STARTUP_MIGRATIONS`) |
| Q9 old evidence | Rows with a looked-up image are marked `image_match='heuristic'`, not re-checked; they are not copied to the vault |

New env vars: `EVIDENCE_RETENTION_DAYS`, `PII_VIEW_ROLES` (both optional, defaults above), `DVAS_GIT_SHA` (set in the image from the build).

### Still to do / not covered

- The vault bucket is created with object lock by the backend on first start; MinIO object lock cannot be added to an existing bucket, so do not create `dvas-evidence-vault` by hand.
- Live video/event streams (SSE, WebSocket, edge MJPEG) still pass the token in the URL, because browsers can't send headers on them. Image URLs no longer do: the analytics pages rely on the httpOnly `dvas_access_token` cookie.
- There is no cases screen yet (U1); cases are API-only. CRO export requests from the evidence screen still export one item.
- `services/pdf_service._make_65b_pdf` is no longer used.

### Pod checks after the next image

1. `db-migrate` applied `013`; backend log shows the three startup migrations.
2. `mc stat local/dvas-evidence-vault` shows object lock enabled.
3. Walk a drone through a geofence: one `intrusion_breach` per track, evidence with `image_match=exact`, `vault_status=copied`, `integrity=ok`.
4. Legal hold → `mc legalhold info` on the vault copy shows ON.
5. Approve an export and download it twice: identical bytes, `sha256sum -c SHA256SUMS.txt` passes inside the zip.
6. `GET /api/evidence/{id}/verify` → `ok`.

### E6–U1: what was built

- **E6 — time columns.** 20 dashboard time columns are `timestamptz` (column type `UTCDateTime`: still ISO-8601 strings in the API). Migration `dashboard_timestamps.sql` runs at backend start in the same transaction as table creation, logs unparseable values to `migration_issues` and converts each column once. `audit_logs.ts` stays text (hash chain).
- **R1 — alerts.** Every confirmed AI event becomes one evidence item and one alert (`category=ai`, severity, rule, evidence link, GPS). Incidents and suggestions no longer create alert copies (B16). Acknowledge takes a disposition. Also fixed: changing a suggestion's status crashed (missing import).
- **R2 — report data.** `services/report_data.py`: IST calendar periods, the requester's scope on every query, hours clipped to the period, analytics via the read-only role.
- **R3 — PDF engine.** `services/pdf/`: Noto Sans + Malayalam with text shaping, classification banner, IST, page x of y, watermark, "Showing N of M" + annexes, evidence galleries, charts, offline maps.
- **R4 — report jobs.** Queue with requested → queued → running → ready/failed/rejected, 80 s limit, interrupted jobs failed on restart, PDFs in `dvas-reports` with SHA-256, live `report_ready`.
- **R5 — report types.** Daily SITREP, Mission AAR, Evidence Register (case), ANPR, Crowd, Intrusion, Detection Quality, Weekly Response, Drone Utilisation. Seniors generate; CROs request and a senior approves.
- **U1 — UI.** One validated modal for legal hold / release / export / disposal / add-to-case; Cases page; new Reports page (catalog, IST dates, drone filter, preview, approvals, hashes); evidence cards with still-image wording, exact/heuristic badge, hash, Verify; alerts feed with severities and dispositions; refresh on WebSocket events with a 60 s fallback poll (was 5 s); KPI tiles use the lists' scope.

### Decisions (Q6, Q7)

| Q | Chosen |
|---|---|
| Q6 read-only role | Yes: `dvas_report_ro`, SELECT only, read-only sessions, 30 s statement limit; re-applied by `dvas-bootstrap.sh` every boot |
| Q7 maps | Offline only: maps drawn from coordinates (graticule, scale, north arrow); optional `.mbtiles` base map; no tile fetch at generation |

New env vars: `ANALYTICS_REPORT_DATABASE_URL` (set by `dvas-init.sh`), `DVAS_REPORT_DB_PASSWORD` (appended to `dvas.env` on existing pods by `gen-secrets.sh`), `REPORT_BASEMAP_MBTILES` (optional). New dependency: `uharfbuzz` (Malayalam shaping). Fonts vendored under `backend/assets/fonts` (OFL).

### Pod checks for E6–U1

1. Backend log: `dashboard_timestamps.sql` and `reports_v2.sql` applied; `SELECT * FROM migration_issues` reviewed.
2. `psql -U dvas_report_ro -d dvas -c 'INSERT …'` is refused; reports show analytics sections (not "unavailable").
3. A confirmed event shows once in the alerts feed (AI · severity) and once in evidence; an incident shows once.
4. Generate a Daily SITREP and a Mission AAR: status goes queued → ready without refreshing; the PDF's hash matches the list; Malayalam names render.
5. As CRO, request a report; as senior, approve it; the CRO downloads it.
6. Restart the backend during a generation: the report ends as failed ("interrupted"), not stuck.

### Not covered

- React Testing Library tests for the modals and report form were not written (the CRA project has no test setup); the frontend was checked with a production build only.
- The cases screen has no evidence search of its own; evidence is added from Evidence Review.
- Reports blur nothing: faces / plates appear in ANPR and evidence galleries for the roles allowed to generate them (DPDPA note printed on the ANPR report).

