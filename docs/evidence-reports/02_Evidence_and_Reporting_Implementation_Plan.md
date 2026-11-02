# DVAS — Implementation plan: evidence storage & report generation

> Branch `runpod-v1` at `3a7a21a` · 2 Nov 2026 · **Plan only — no code has been changed.** Per `claude.md`
> ("plan first, code only after explicit approval"), each phase below is a separate approval unit.
> Inputs: `03_UI_and_Backend_Issues.md` (findings B1–B30) and `01_Report_Generation_Assessment.md` (data
> inventory, report gaps). Finding IDs (B…) refer to document 03.

---

## 0. Problem restated

The command dashboard turns analytics detections into **evidence** and **reports**, and a Senior Officer exports evidence as a
court bundle. Today three things are wrong:

1. **Evidence isn't trustworthy.**
   - Images are looked up after the fact by guessing, sometimes from unrelated events (B1, B2).
   - Images expire in MinIO after 3–14 days, whatever the legal hold says (B3).
   - Nothing is hashed (B4).
   - The bundle certificate cites the repealed s.65B and changes on every download (B5).
2. **Reports are thin and partly wrong.**
   - The "alert" figures don't include AI detections (B10).
   - Date/period parameters are ignored or use UTC days (B11).
   - The AAR borrows other missions' data (B12).
   - Hours are inflated (B13).
   - A named officer can be fabricated as mission operator (B26).
   - The PDFs ignore almost all stored analytics data (B18; document 01).
3. **Access control around evidence is inconsistent.**
   - Anyone can delete evidence (B6).
   - The image proxy is open to every bucket (B7).
   - Scope can be bypassed (B8).
   - Seniors can't download bundles (B9).

**Goal.** Evidence that is complete, immutable, hashed and retained per policy; reports that are correct, reproducible and use the
analytics data DVAS already collects; one consistent RBAC/scope policy. Each change stays **backward compatible with the local compose
stack** (env-driven, like the RunPod work) and is delivered in phases that can ship independently.

**Ambiguities resolved (flag if wrong):**
- "Evidence types" = what DVAS stores today: still images (crops, snapshots, heatmaps) plus structured analytics rows. Video clips
  stay on the edge SSD (plan F18). Clip upload is out of scope here, with only a hook reserved (E7).
- The legal frame is the **Bharatiya Sakshya Adhiniyam 2023, s.63** (in force since 1 Jul 2024), plus DPDPA 2023 for faces/plates.
  The certificate wording must be confirmed by the department's legal cell (open question Q1).

---

## 1. Phase overview

| Phase | Name | Fixes | Depends on | Effort | Ships independently |
|---|---|---|---|---|---|
| **P0** | Stop the harm (hot-fixes) | B27, B6, B9, B12, B26, B1 (env URL), B8, ANPR watchlist reload (B30) | — | 2–3 d | ✅ |
| **E1** | Exact evidence capture (analyzer → event → evidence) | B2, B23, B28, B29, B30 (drift, uploads, keys) | P0 | 5–7 d | ✅ |
| **E2** | Immutable storage, retention, legal hold in MinIO | B3, B6 | E1 | 2–3 d | ✅ |
| **E3** | Integrity: SHA-256 end to end | B4 | E1 (E2 for lock) | 2 d | ✅ |
| **E4** | Case-based export + BSA s.63 certificate, built once | B5, B15 | E3 | 3 d | ✅ |
| **E5** | Evidence access API (scoped image endpoint, signed downloads) | B7, B9 | P0 | 2 d | ✅ |
| **E6** | Dashboard schema hygiene (`timestamptz`, alert severity, links) | B22, B16 | — | 2 d | ✅ (migration first) |
| **R1** | Alerts from confirmed events | B10, B16 | E1, E6 | 2 d | ✅ |
| **R2** | Report data layer: ranges, IST, scope, reproducible params | B11, B13, B14 | E6 | 2–3 d | ✅ |
| **R3** | PDF engine: Unicode, images, maps, annexes | B17, B18 | R2 | 3 d | ✅ |
| **R4** | Report jobs: worker, hash, storage, notifications | B14 | R2 | 2 d | ✅ |
| **R5** | New/extended report types using analytics data | doc 01 §4 | R1–R4, E1 | 4–6 d | per report |
| **U1** | UI: evidence & reports workflow fixes | B19, B20, B21, B24, B25 | per item | 3–4 d | ✅ |

**Independent work (can run in parallel):** P0 ∥ E6. After P0: E1 ∥ E5. After E1: E2 ∥ E3 ∥ R1. R2 ∥ E4 (after
their dependencies). U1 items attach to whichever backend phase they need.
**Hard ordering:** every schema change (E6, E1, E2, E3, R1, R2) ships its SQL migration **before** the code that reads the new columns.

---

## 2. Phases in detail

### P0 — Stop the harm (hot-fixes, no schema change)

| Change | File(s) | Detail |
|---|---|---|
| **Crowd analyzer back on (B27)** | `dvas_analytics/crowd/main.py` | Restore the startup removed in `819ef2f` (FastAPI `lifespan`: `ensure_all_buckets`, `CrowdAnalyzer(model, alert_threshold=cfg.CROWD_ALERT_THRESHOLD)`, auto-start stream like before). `/health` → 503 while `model_loaded` is false. `deploy/runpod/bin/healthcheck.sh`: analyzers must report `model_loaded:true` (or `status:ok` with a loaded flag) |
| **ANPR watchlist on restart (B30)** | `dvas_analytics/anpr/main.py:148-164` | Load active `plate_watchlist` rows at startup (and every 60 s), persist `case_ref` |
| Evidence delete restricted | `routers/alerts.py:432` | `Depends(require_roles("senior_officer"))`; reject unless `retention_until < now` or a disposal reason is given (`DisposeIn{reason: str min 20}`); body → audit `before_values`. *(The full soft-delete comes in E2.)* |
| Bundle download auth | `routers/alerts.py:565-587` | Drop the hand-written `jwt.decode`; `user = await get_current_user(request)` (already reads `?token=`). Same in `routers/reports.py:235` (uses it already) and `routers/edge.py` shift-summary |
| AAR without borrowed data | `routers/reports.py:83-93, 355-365` | Remove both `drone_id` fallbacks; add `mission_window_events` only when `started_at/ended_at` exist, rendered as a separately titled section |
| No fabricated missions | `routers/drones.py:87-163` | Remove the insert block and the literal name; return `untracked_mission_ids: [...]` alongside the list (admin only). One-off SQL report of rows created by it (`operator_ids='[]' AND name LIKE 'Mission %'`) for manual review, **not** auto-deleted |
| Analytics DB URL | `services/evidence_sync.py:19`, `routers/drones.py:92` | Read `ANALYTICS_DATABASE_URL` (already set by `dvas-init.sh`; add to `DVAS_Dashboard/docker-compose.yml` env from the existing `POSTGRES_*` vars — **new env var for local compose, flagged**). Missing → log `error` once + skip, never a literal default |
| Scope bypass | `routers/alerts.py:151-167, 242-276, 46-47` | Apply `scope_filter_for` regardless of `drone_id`/`mission_id`; scope `/evidence/meta`; operators limited to their `mission_scope` (empty scope → nothing, not everything) |

**Analytics tests:** crowd app startup creates the analyzer (mock weights, CPU); ANPR startup loads the watchlist from a test DB.

**Tests** (`DVAS_Dashboard/backend/tests/test_evidence_rbac.py`, new; pytest + httpx `AsyncClient`, DB fixture like `test_runtime.py`):
- operator `DELETE /evidence/{id}` → 403; senior with reason → 200 + audit row;
- senior `GET /exports/{id}/bundle?token=` on a CRO-requested approved export → 200; operator → 403;
- AAR for a mission with no tagged alerts doesn't include other missions' alerts;
- `GET /missions` performs no insert (row count unchanged);
- CRO with `drone_id` filter only gets in-scope evidence.

**Risk surface:** RBAC (tighter: some current UI paths start returning 403, e.g. CRO "AAR Report" button, which U1 hides). Legal: the
review of fabricated mission rows needs a human decision.

---

### E1 — Exact evidence capture

**Idea:** the analyzer that *confirms* an event knows exactly which frame, crop and track caused it. It must put that into the event,
and the dashboard must only copy it, never guess.

**Contract change: `confirmed_events.attributes` (and the `drone:events:confirmed` payload)** gets a standard block, written by
every analyzer through one helper `shared/evidence.py::evidence_block(...)`:

```json
"evidence": {
  "schema": 1,
  "objects": [
    {"role": "primary",   "bucket": "dvas-intrusion", "key": "M-123/DRN-001/2026-11-02T10-15-03.120Z_ev7f3.jpg",
     "content_type": "image/jpeg", "sha256": "…", "bytes": 84213, "kind": "annotated_frame"},
    {"role": "secondary", "bucket": "dvas-plates", "key": "…", "content_type": "image/jpeg", "sha256": "…", "kind": "plate_crop"}
  ],
  "frame": {"frame_sequence": 18231, "capture_ts": 1793600103.12, "ingest_ts": 1793600103.48},
  "geo":   {"lat": 8.5241, "lon": 76.9366, "alt_m": 62.0, "heading_deg": 140},
  "detection": {"bbox_xyxy": [412, 220, 498, 377], "track_id": "…", "class": "person", "confidence": 0.91},
  "model": {"service": "intrusion", "weights": "geotrax_yolov8s.pt", "weights_sha256": "…", "image_tag": "20261030-d0df131"}
}
```

Analyzer-specific facts (plate text, counts, zone, dwell, vessel class, anomaly class) stay in their existing attribute keys and
are **also** copied into evidence `attributes` (see below). Per-analyzer work, where the object is uploaded and which keys
exist today: see document 01 §2.

**Confirmation rules (B28, B29)** — "confirmed" must mean an *incident*, not a detection:

| Module | Confirmed event = | Rule |
|---|---|---|
| intrusion | state ENTERING or DWELLING per track per zone | write `confirmed_events` (`intrusion_breach`) referencing `intrusion_events.event_id`; one per track per zone per 60 s; same `event_id` in Valkey |
| vessel | `proximity_alert`; first sighting of a vessel class flagged as "of interest" (config) | one per pair per 30 s (existing cooldown) |
| anomaly | every gated alert (already cooled down 15 s per track) | copy severity, reasons |
| crowd | count ≥ threshold, sustained ≥ N s | one per drone per 60 s, resets when count drops below 0.8 × threshold |
| person | face-watchlist match (from `person_watchlist_alerts`); optional "new identity" | no event per detection |
| vehicle / ANPR | watchlist plate (theft), optional vehicle-of-interest rule | one per plate per mission per 5 min |

Raw per-frame rows stay in module tables. Reports aggregate from them (document 01), not from evidence.
`confirmed_events.mission_id` becomes `text` (migration in `dvas_analytics/db/migrations/013_confirmed_events_v2.sql`: drop the UUID FK,
add `mission_id_str text`, `zone_id`, `latitude/longitude` filled by `write_confirmed_event`).

**Analytics data fixes in the same phase (B30):**
- `013` also adds the columns the code already writes (`face_embeddings.encrypted_embedding/person_object_key/arcface_embedding`,
  `person_identities.best_person_image_key/mission_id`), with `IF NOT EXISTS`.
- `upload_bytes` raises (or returns a result object); callers store a key only after a successful upload.
- Anomaly keys use `make_key` (mission/UTC time/uuid).
- Analyzers stamp `capture_ts` from frame metadata (the ingest already republishes it), with processing time kept separately.

**Dashboard side** (`services/evidence_sync.py`):
- `_resolve_image_keys()` is used **only** for events without an `evidence` block (legacy), and **only** with exact matches:
  `event_id`, or `track_id` + same drone + |Δt| ≤ 5 s. All "latest anywhere" tiers are deleted. A legacy match is flagged
  `image_match = 'heuristic'`, an exact one `'exact'`, none `'none'`.
- **Persistent cursor:** table `evidence_sync_state(id int pk, last_ts timestamptz, last_event_id text)`. Page with
  `(ts, event_id) > cursor` until empty. Gateway `GET /events/confirmed` gets `after_ts`, `after_id`, `order=asc` params
  (gateway change, backward compatible).
- **Long-term (optional, decide in Q3):** read `confirmed_events` directly from the same Postgres (both DBs are on one server in the pod),
  instead of HTTP polling.

**Schema (dashboard) — migration `DVAS_Dashboard/backend/migrations/evidence_v2.sql`:**
```sql
ALTER TABLE evidence
  ADD COLUMN IF NOT EXISTS attributes      jsonb       NOT NULL DEFAULT '{}'::jsonb,  -- analyzer facts (plate, count, zone…)
  ADD COLUMN IF NOT EXISTS objects         jsonb       NOT NULL DEFAULT '[]'::jsonb,  -- evidence.objects (bucket/key/sha256/kind)
  ADD COLUMN IF NOT EXISTS image_match     text        NOT NULL DEFAULT 'none',       -- exact | heuristic | none
  ADD COLUMN IF NOT EXISTS capture_ts      timestamptz,                               -- from the frame, not sync time
  ADD COLUMN IF NOT EXISTS lat double precision, ADD COLUMN IF NOT EXISTS lon double precision,
  ADD COLUMN IF NOT EXISTS severity        text,
  ADD COLUMN IF NOT EXISTS model_info      jsonb,
  ADD COLUMN IF NOT EXISTS status          text        NOT NULL DEFAULT 'active';     -- active | disposed (E2)
CREATE INDEX IF NOT EXISTS evidence_mission_ts ON evidence (mission_id, capture_ts);
CREATE INDEX IF NOT EXISTS evidence_attr_gin   ON evidence USING gin (attributes jsonb_path_ops);
```
`primary_image_key`/`secondary_image_key`/`image_bucket` stay (filled from `objects`) so the current UI keeps working.

**Tests:**
- unit: `evidence_block()` builds the schema, hashes bytes;
- sync: event with block → evidence has exact objects; event without block and no exact match → `image_match='none'`, **no** image;
- cursor: 250 events with identical `ts` are all synced exactly once; restart resumes from the stored cursor.

**Risk surface:** cross-boundary (`dvas_analytics` ↔ `DVAS_Dashboard`), needs the analytics owner's OK. Evidence integrity improves.
Events arrive before their image upload finishes → analyzers must upload **before** publishing (order fixed in the helper).

---

### E2 — Immutable storage, retention, legal hold

**Design:**
1. **Evidence vault copy.** When `evidence_sync` creates an evidence row, it copies each object to
   `dvas-evidence/evidence/<evidence_id>/<role>.<ext>` (server-side `copy_object`, plus `x-amz-meta-sha256`). The source buckets keep
   their short ILM (3/14 d) for raw analytics. Evidence never depends on them again.
2. **Object lock.** `dvas-evidence` is recreated **with object lock + versioning** (MinIO requires lock at bucket creation →
   one-off migration: create `dvas-evidence-v2` with `--with-lock`, copy, switch the name in config; keep the old one read-only until
   verified). Default retention: **GOVERNANCE, `EVIDENCE_RETENTION_DAYS`** (policy value, Q2).
   - Legal hold → `mc legalhold set` (S3 `PutObjectLegalHold`) on every version.
   - Release → `legalhold clear`.
   - Reports (`reports/…`) move to a separate `dvas-reports` bucket (no lock, 1-year ILM, Q2).
3. **Retention job** (`services/retention.py`, in `backup-loop.sh` or a backend scheduler, nightly):
   - evidence `status='active'`, past `retention_until`, `legal_hold=false`, not referenced by an open export/case → `status='disposed'`, `disposed_at`, `disposal_reason='retention'`;
   - objects deleted only after the lock expires;
   - audit entry per item (category `evidence`, tag `RETENTION`).
4. `DELETE /api/evidence/{id}` becomes **disposal** (status change + audit). Hard delete only through the retention job.

**Config:** `EVIDENCE_RETENTION_DAYS` (default 90, matches today's `retention_until`), `REPORT_RETENTION_DAYS` (365): **new env vars,
flagged**. Bootstrap (`deploy/runpod/bin/dvas-bootstrap.sh`) creates `dvas-evidence` with lock and `dvas-reports`.

**Volume sizing:** evidence copies are small (crops/snapshots 50–300 KB). 10 000 items ≈ 1–3 GB. Fits the test volume; production
50–100 GB (as already planned).

**Tests:**
- integration against a throwaway MinIO (`minio server` in pytest fixture, or the local compose one): copy + hash, hold blocks delete, release allows;
- retention job dry-run lists only eligible rows; held item never disposed.

**Risk surface:** evidence integrity (positive). MinIO object lock is irreversible per bucket: test on a copy first. Volume growth.

---

### E3 — SHA-256 end to end

| Point | What |
|---|---|
| Analyzer upload | `shared/minio_client.py` `put_object` wrapper computes SHA-256 of the bytes, sets `x-amz-meta-sha256`, returns it → into `evidence.objects[].sha256` |
| Vault copy (E2) | re-read, re-hash, must equal the analyzer's value; mismatch → evidence `integrity='mismatch'`, alert to admin |
| Evidence row | `objects[].sha256`; the evidence **record hash** = SHA-256 of a canonical JSON (id, capture_ts, drone, mission, event_type, attributes, objects) stored in `record_sha256` and in the creation audit entry (`after_values`) → the audit hash chain now covers the evidence |
| Reports (R4) | `reports.sha256` of the PDF; in the download audit entry |
| Bundles (E4) | manifest with per-file SHA-256 + bundle SHA-256 |
| Verify | `GET /api/evidence/{id}/verify` (senior/admin) re-hashes the stored objects; shown in the evidence detail |

**Migration:** `evidence_v3_integrity.sql` adds `record_sha256 text`, `integrity text default 'unverified'`; `reports` adds `sha256 text`.

**Tests:** tampered object (overwrite in a test bucket without lock) → verify reports mismatch; canonical JSON stable across
key order.

---

### E4 — Case-based export with a BSA s.63 certificate, built once

**Model** (migration `exports_v2.sql`):
```sql
CREATE TABLE IF NOT EXISTS cases (
  id text PRIMARY KEY, case_number text NOT NULL, fir_number text, police_station text, jurisdiction text,
  title text, created_by text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), status text NOT NULL DEFAULT 'open');
CREATE TABLE IF NOT EXISTS case_evidence (case_id text REFERENCES cases(id), evidence_id text REFERENCES evidence(id),
  added_by text NOT NULL, added_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (case_id, evidence_id));
ALTER TABLE exports
  ADD COLUMN IF NOT EXISTS case_id text REFERENCES cases(id),
  ADD COLUMN IF NOT EXISTS evidence_ids jsonb,              -- frozen list at approval
  ADD COLUMN IF NOT EXISTS bundle_key text, ADD COLUMN IF NOT EXISTS bundle_sha256 text,
  ADD COLUMN IF NOT EXISTS certificate_key text, ADD COLUMN IF NOT EXISTS self_approved boolean NOT NULL DEFAULT false;
```
(`exports.evidence_id` stays for old rows.)

**Flow:**
1. A CRO (or senior) creates a case (case number mandatory) and adds evidence.
2. The CRO requests an export of the case with a justification.
3. A senior decides. State machine: `pending → approved | rejected` only (B15).
4. **On approval** the backend builds the bundle **once**: evidence objects (from the vault, hashes re-verified), `manifest.json`
   (every file + SHA-256, evidence records, audit excerpt of each item), the certificate PDF, `README.txt`. It stores the bundle in
   `dvas-evidence/exports/<export_id>/bundle.zip` (locked) and writes the bundle hash into `exports` and the audit chain.
5. Downloads stream those bytes. Each download is audited with the hash.

**Direct export by a senior:** allowed only with `case_number`, marked `self_approved=true`, shown as such in the certificate and the
UI. Optionally requires a second senior (config, Q4).

**Certificate** (`services/certificate.py`, replaces `_make_65b_pdf`):
- **Part A** (person in charge of the system): system identification (DVAS version / image tag, pod, analyzer service + model weights hash), the record list with **hash values (SHA-256)**, capture times (IST + UTC), drone, mission, GPS, and a statement of proper operation.
- **Part B** (expert) left as a form if the legal cell requires it.
- Wording and layout to be supplied or approved by the legal cell (Q1).
- Unicode font (R3).

**Tests:** bundle built once (two downloads → identical bytes and hash); manifest hashes match files; re-decide after approval →
409; self-approval flagged; a disposed evidence item can't be added to a case.

**Risk surface:** legal (certificate content), RBAC (case access by jurisdiction), storage (bundles locked = retained).

---

### E5 — Evidence access API

- `GET /api/evidence/{id}/objects/{role}`: scope check via the evidence row, then stream from the vault.
  - `Cache-Control: private, no-store`.
  - Audit `evidence_view` (first view per user per evidence per hour, to keep the chain size sane).
  - Face/plate objects are visible only to roles in `PII_VIEW_ROLES` (Q5).
- `/api/minio/image` is restricted to buckets of **live/annotated** data (heatmaps, current frames) and to the same scope rules.
  Its `public` cache header becomes `private, max-age=60`.
- **Signed download links** replace `?token=` for files: `POST /api/downloads` returns `{url: /api/dl/<opaque>, expires_in: 60}`.
  The opaque id maps to (user, resource) in Valkey with a 60 s TTL. This replaces tokens in URLs for reports, bundles and images.
- Frontend: `resolveMinioImageUrl(…)` → `evidenceImageUrl(evidenceId, role)`; downloads call `POST /api/downloads` first.

**Tests:** CRO out of scope → 404 (no existence leak); operator can't fetch `dvas-watchlist`; signed link expires, single user.

---

### E6 — Dashboard schema hygiene

Migration `dashboard_timestamps.sql` (run in a maintenance window; tables are small):
- Every time column in `alerts, evidence, exports, missions, reports, incidents, ai_model_suggestions` becomes
  `timestamptz` with `USING NULLIF(col,'')::timestamptz` (handles `Z` / `+00:00`; rows that fail to parse are logged into
  `migration_issues` first, by a pre-check query).
- `alerts`: add `severity text`, `category text` (`ai|incident|suggestion`), `rule text`, `evidence_id text`, `lat/lon`, `source_event_id text unique`.
- `audit_logs.ts` stays text (hash chain input must not change).

The ORM (`models.py`) switches to `DateTime(timezone=True)`. Every `isoformat()` writer and `LIKE 'date%'` reader is updated
(grep list in the PR). The API output stays ISO-8601 strings, so the frontend is unchanged.

**Tests:** migration up on a copy of the demo DB; every router test passes; range filters across midnight IST.

---

### R1 — Alerts from confirmed events

- `evidence_sync` (or the direct reader, E1) also inserts `alerts` with `category='ai'`, `source_event_id`, severity (from the
  analyzer's rule: intrusion and theft = critical, crowd threshold = high, …), `evidence_id`, zone, GPS.
- Incidents and suggestions **stop** creating `alerts` copies (`routers/incidents.py:173`, `routers/analytics.py:1440`,
  `routers/push.py`). Existing copies are migrated: `category='incident'|'suggestion'`.
- Ack writes `acknowledged_by/at` (+ optional `disposition`: `true_positive | false_positive | duplicate | handled`), the basis for
  precision figures in reports.
- The feed reads `/api/alerts?category=` (B16).

**Tests:** an event produces exactly one alert + one evidence; ack with disposition; feed has no duplicates.

---

### R2 — Report data layer

New module `services/report_data.py`, one function per report, all with the same signature:
```python
async def collect(session, *, start: datetime, end: datetime, scope: ScopeFilter,
                  drone_ids: list[str] | None = None, mission_id: str | None = None) -> ReportData
```
- `start/end` are IST calendar boundaries converted to UTC (`zoneinfo.ZoneInfo("Asia/Kolkata")`); the UI sends dates only.
- Scope is applied to every query (B8 policy).
- Durations come from mission start/end; open missions are counted separately. No minimum padding (B13).
- Analytics-DB aggregates (plates, crowd peaks, intrusion dwell, vessel/anomaly counts) via read-only SQL on the `dvas` DB:
  `ANALYTICS_DATABASE_URL`, role with `SELECT` only (Q6).
- `reports.params` stores `{start, end, tz, scope, filters, data_cutoff}`, so a report can be regenerated byte-for-byte from the same data.
- `preview-data` is reimplemented on top of `collect()` (same numbers as the PDF).

**Tests:** fixtures across an IST midnight; weekly report respects the week; utilisation respects the period; scope applied.

---

### R3 — PDF engine

- `fpdf2` (already pinned 2.8.7) with **Noto Sans** + **Noto Sans Malayalam** TTF (`add_font`), and text shaping
  (`set_text_shaping(True)`, which needs `uharfbuzz`, **new dependency**, wheels exist for cp311/manylinux). Fonts are vendored
  under `backend/assets/fonts/` (OFL licence).
- Shared layout module `services/pdf/`: header/footer, classification banner, IST time with the zone, page "x of y", watermark, and
  a "Showing N of M" rule for every table.
- **Images:** evidence thumbnails from the vault (E2), max 2 per row, with caption: evidence ID, time, hash prefix.
- **Maps:** a static map of event locations. Offline-safe choice: plot on a pre-rendered district base map. Alternative: an OSM tile
  fetch at generation time, whose tile licence and attribution must be handled (Q7).
- **Charts:** small matplotlib PNGs (already in the AI venv; add to the backend venv **or** draw simple bars with fpdf primitives,
  which is preferred to avoid the dependency).
- **Annexes:** full lists continue as annex tables (no silent truncation).

**Tests:** PDF with Malayalam operator name renders (text extraction contains the original string); a 500-alert report has the annex
with 500 rows.

---

### R4 — Report jobs

- Table `report_jobs` (or extend `reports`): `status queued|running|ready|failed`, `attempts`, `started_at`, `finished_at`, `sha256`.
- Worker: an asyncio task started on app start that claims jobs with `SELECT … FOR UPDATE SKIP LOCKED`.
  - Timeout 80 s (below Cloudflare's 100 s, though downloads are separate anyway).
  - On start, `running` older than 5 min → `failed: interrupted`.
- Storage: `dvas-reports/<id>.pdf` + `sha256`. No in-memory cache.
- WebSocket `report_ready` / `report_failed` events to the requester (U1 removes the polling dependence).
- Report download via a signed link (E5); audit with the hash.

**Tests:** restart during generation → row becomes `failed`, not stuck; hash in row equals the hash of the stored object.

---

### R5 — New and extended report types

Detailed content per report is in document 01 §4. Recommended order:
1. **Mission After-Action v2** (fix + evidence gallery + timeline + analytics summary per enabled module + map).
2. **Daily Situation Report (SITREP)** by district, IST day.
3. **Evidence Register / Case report** (for a case from E4; chain-of-custody table from the audit log).
4. **ANPR / vehicle-of-interest report** (plate hits, watchlist matches, locations).
5. **Crowd event report** (peak counts, threshold breaches, heatmaps, timeline).
6. **Intrusion / geofence report** (breaches per zone, dwell, response).
7. **Detection quality report** (dispositions from R1: precision by module and confidence band).
8. Weekly response and utilisation reports, rebuilt on R2.

Each report gets: a data function (R2), a template (R3), a preview (same data), an RBAC role list, and a test with a fixed fixture.

---

### U1 — UI changes (attach to the backend phase they need)

| Change | Needs | File(s) |
|---|---|---|
| Legal hold / export / disposal modals (case number, reason category, free text ≥ 20 chars, shows the evidence) instead of `prompt()` | P0/E4 | `CommandContext.js:234-260`, new `components/evidence/ActionModal.js` |
| Hide "AAR Report" for non-seniors; add "Request report" for CRO (creates a `report_request` the senior sees) | P0 / R4 | `EvidencePage.js:204-213`, `ReportsPage.js` |
| Report cards: date **range** pickers (IST), district/drone filters, preview drawer (uses `preview-data`), honest descriptions | R2 | `ReportsPage.js` |
| Evidence card: "No image captured" vs "image unavailable"; badge `exact/heuristic`; hash prefix + verify button in detail; still-image wording (no ▶, no "clip") | E1/E3 | `EvidencePage.js:100-160` |
| Case workspace (list cases, add evidence, request export, download bundle with hash shown) | E4 | new `pages/command/CasesPage.js`, nav entry |
| WebSocket-driven refresh (`evidence_created`, `alert_created`, `report_ready`, `export_decided`) + 60 s fallback poll; `/evidence/meta` cached | R1/R4 | `CommandContext.js:64-117`, `services/websocket_hub.py` |
| Alerts feed: three sources, severity colours, disposition on ack | R1 | `AlertsFeedPage.js:64-90` |
| KPI tiles use the same scope as the lists; correct labels | P0 | `routers/drones.py:404-426`, `TacticalMapPage.js` |
| Default dates in IST (`Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Kolkata'})`) | R2 | `CommandContext.js:47-48` |

**Tests:** React Testing Library for the modals (validation) and the report form (IST date → request params); existing CRA test
setup (`npm run test`).

---

## 3. Tradeoffs & alternatives considered

| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Where evidence keys come from | analyzer writes them into the event (E1) | keep resolving in the dashboard, but stricter | Only the analyzer knows the exact frame; any lookup afterwards is a guess |
| Immutability | MinIO object lock (governance) + copy into the vault | DB flag only (today); WORM storage outside MinIO | Object lock is native and enforceable, with no new service; governance mode still lets an admin with special rights fix mistakes |
| Hashing | SHA-256 at upload + re-hash at copy/export | hash only at export | Hash at capture proves the bytes never changed since the analyzer saw them |
| Bundle | built once at approval, stored | build per download (today) | Same bytes for everyone; certificate refers to a fixed hash |
| Report data | read-only SQL on the analytics DB | copy analytics into the dashboard DB | Avoids duplication and drift; one Postgres server anyway (pod) |
| PDF library | stay on fpdf2 (+ fonts, shaping) | WeasyPrint/HTML templates | Fewer system deps (WeasyPrint needs Pango/Cairo); fpdf2 already in use. Revisit if layouts get complex |
| Report execution | DB-backed job table + in-process worker | Celery/RQ | One process, low volume; no new infra in the pod |
| Sync | persistent cursor over HTTP now, direct DB read later (Q3) | direct DB read now | HTTP keeps the service boundary; direct read is simpler but couples schemas |

## 4. Risk surface (explicit)

- **Evidence integrity / chain of custody:** E1–E4 change how evidence is created, stored and exported. Each phase must keep the audit
  hash chain append-only (no rewrites of `audit_logs`), and E2's bucket migration must be verified (hash compare) before the old bucket
  is retired.
- **RBAC:**
  - P0/E5 tighten access, so some current UI paths will 403 until U1 lands.
  - Case access by jurisdiction is new (E4).
  - PII view restriction (Q5).
- **Legal:** the BSA s.63 certificate text (Q1); retention periods (Q2); fabricated mission rows (P0) need human review; the DPDPA
  purpose limitation for faces/plates in reports (R5: blur option in non-evidentiary reports).
- **Cross-boundary:**
  - P0 (crowd startup, ANPR watchlist), E1 and E3 touch `dvas_analytics` (all analyzers + `shared/`). The RunPod owner and analytics
    owner must both agree. The crowd fix (B27) repairs a regression introduced by this branch's own commit `819ef2f`.
  - `dvas_edge` is **not** touched.
  - `dvas_ingest` only if `capture_ts` handling changes (it already passes it through).
- **Operations:**
  - Volume growth (E2).
  - New env vars: `ANALYTICS_DATABASE_URL` for local compose, `EVIDENCE_RETENTION_DAYS`, `REPORT_RETENTION_DAYS`.
  - New dependency `uharfbuzz` (R3).
  - Image rebuild for each shipped phase.

## 5. Test strategy

- Backend: pytest + httpx `AsyncClient` against a disposable Postgres 16 (same approach used for the RunPod migration tests), MinIO in
  a fixture for E2/E3/E4. Each phase lists its tests above. Every new logic path gets one happy-path test and one realistic failure.
- Analytics: unit tests for `shared/evidence.py` and the upload wrapper (no GPU needed).
- Frontend: React Testing Library for new forms; manual run-through of the Phase 6 checklist per role after each UI change.
- On the pod: `deploy/runpod/tools/smoke_public.py --write` extended with:
  - evidence created from the smoke frames has `image_match=exact` and a hash;
  - bundle download hash equals the stored hash;
  - report download hash equals the row.

## 6. Open questions

| # | Question | Needed for | Owner |
|---|---|---|---|
| Q1 | Exact certificate format and who signs (BSA 2023 s.63 Schedule Part A/B)? Digital signature (DSC) required? | E4 | Legal cell |
| Q2 | Retention periods: evidence not on hold, reports, raw analytics crops (today 14 d), heatmaps (3 d) | E2 | Kerala Police policy / DPDPA officer |
| Q3 | Keep HTTP sync from the gateway, or read `confirmed_events` directly from Postgres? | E1 | Architecture (you) |
| Q4 | Must a senior's direct export be counter-signed by a second senior? | E4 | Operations |
| Q5 | Which roles may see face crops / plate images (PII)? Should reports blur faces by default? | E5, R5 | Legal / DPDPA |
| Q6 | OK to add a read-only Postgres role for the dashboard on the analytics DB? | R2 | Architecture |
| Q7 | Maps in reports: offline base maps (no external call) or OSM tiles at generation time? | R3 | Security (data egress) |
| Q8 | `claude.md` mentions Alembic, but the backend has plain SQL migrations (`backend/migrations/*.sql`). Keep SQL files (applied by `db-migrate.sh` on the pod) or introduce Alembic? | all schema phases | You |
| Q9 | What to do with existing evidence that has heuristic/possibly wrong images (B2)? Re-check with the exact rule and mark, or leave? | E1 | Legal / you |
