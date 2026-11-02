# DVAS — Can report generation be improved with the evidence we store today?

> Branch `runpod-v1` at `3a7a21a` · 2 Nov 2026 · code review (no code changed).
> - Analytics inventory: `dvas_analytics/*/main.py`, `shared/{db,redis_client,minio_client,crypto}.py`, `db/migrations/*.sql`.
> - Dashboard: `DVAS_Dashboard/backend/routers/reports.py`, `services/{pdf_service,evidence_sync}.py`, `models.py`.
> - The pod was stopped during the review: volumes and row counts below are what the code **writes**, not measured.
>
> Companion documents: `02_…_Implementation_Plan.md` (phases P0–U1) and `03_UI_and_Backend_Issues.md` (B1–B30).

---

## 1. Short answer

**Yes, substantially.** DVAS already stores much richer data than the four reports use:
- plate text with crops;
- vehicle type and colour;
- crowd counts every ~0.5 s;
- geofence breaches with entry/exit, dwell, trajectory and ground GPS;
- vessel tracks;
- anomaly alerts with severity, reasons and GPS;
- face-watchlist matches with SHA-256 hashes.

The reports read only four dashboard tables (`alerts`, `evidence`, `missions`, `drones`) and print IDs, types and confidence values.

**But better reports on top of today's data would mostly make wrong numbers look more convincing.** Four facts need fixing first, or alongside:

1. **The crowd analyzer does nothing on the pod** since commit `819ef2f`. Its startup hook was removed, so `analyzer` stays `None`
   (`crowd/main.py:66`) and no crowd readings, heatmaps or crowd events are stored. `/health` still says `ok` (with `model_loaded:false`),
   so the Phase 5 checks passed. This is a regression from this branch's own Phase 2 work (B27).
2. **Intrusion, vessel and anomaly never reach evidence.** They don't write `confirmed_events`, the only table the dashboard syncs
   from (`gateway/main.py:234-242`). Their data sits in their own tables (`intrusion_events`, `vessel_events`, `anomaly_events`) (B28).
3. **"Evidence" is mostly raw detections.** Person and vehicle write a "confirmed" event for **every object in every frame**:
   - person at confidence ≥ 0.15 (`person/main.py:663-675`);
   - vehicle at any confidence (`vehicle/main.py:353-362`).

   Each becomes an evidence row. Evidence counts in reports measure frames × objects, not incidents (B29).
4. **Evidence images are guessed and expire.** Images are attached by heuristic lookup, sometimes from unrelated events (B2). The
   source buckets expire in 3–14 days (B3). No hash exists except on the face-watchlist path (B4).

So the honest order is:
1. Fix capture (plan P0 + E1): crowd back on, real confirmed events per module, exact images.
2. Rebuild the report data layer (R2).
3. Then add the richer reports (R5).

Section 4 marks which report content can be built **now** from tables that are already reliable, and which must wait.

---

## 2. What is stored today, per module (usable for reports?)

Legend: ✅ reliable enough for reports · ⚠️ stored but with caveats · ❌ missing or unusable.

### 2.1 Analytics database (`dvas`)

| Module | Rows written | Mission | Time | GPS | Images (bucket) | Hash | For reports |
|---|---|---|---|---|---|---|---|
| **Crowd** | `crowd_density_readings` (count, heatmap key every 10th frame); `confirmed_events crowd_threshold` per frame above 150, no cooldown | ❌ no mission column (only in event `attributes`) | processing time | ❌ | heatmap (`dvas-heatmaps`, JPEG labelled PNG, 3 d) | ❌ | ❌ **nothing written at HEAD** (B27). When fixed: ✅ counts/peaks per drone & time; ⚠️ mission only via time window |
| **Person** | `tracks`, `person_embeddings`, `face_embeddings`, `person_identities` (Re-ID clusters), `confirmed_events person_detected` **per person per frame** | ✅ `mission_id_str` on tracks/embeddings; ❌ not on events | processing time | ❌ | body + face crops (`dvas-faces`, 14 d), keys without mission | ❌ | ⚠️ unique-person counts per mission (clusters) usable; event counts not |
| **Face watchlist** | `person_watchlist_alerts` (match, similarity, crops, **SHA-256** of crop + frame, JSON "65B" packet) | ✅ | ⚠️ `detected_at` = insert time, `frame_ts` NULL | ❌ | face crop (`dvas-faces`), full frame + packet (`dvas-evidence`, no expiry) | ✅ crop/frame | ✅ **best evidence-grade data in DVAS today** but not in the dashboard evidence/report path at all |
| **Vehicle** | `tracks` (+ `vehicle_image_key`), `vehicle_attributes` (type, colour top-3, speed "estimated") **per vehicle per frame**, `confirmed_events vehicle_detected` per vehicle per frame | ✅ on attributes/tracks; ❌ on events | processing time | ❌ | crop per vehicle per frame (`dvas-vehicles`, 14 d) | ❌ | ⚠️ distinct-vehicle counts by type/colour per mission via `tracks` (tracker-ID collisions with ANPR, B30) |
| **ANPR** | `plate_reads` (1 row per track, text overwritten), `vehicle_plate_links`, `theft_alerts` (per frame, no mission, no images), `vehicle_attributes` | ✅ `mission_id_str` (not on `theft_alerts`) | processing time | ❌ | plate crop (`dvas-plates`), vehicle crop (`dvas-vehicles`), both 14 d, re-uploaded every frame | ❌ | ✅ plate list per mission with OCR confidence + crops; ⚠️ theft hits need dedup; watchlist not reloaded after restart |
| **Intrusion** | `intrusion_events` on **state transitions** only: zone, track, class, entry/exit, **dwell**, trajectory, **object ground lat/lon**, snapshot | ✅ (TEXT, or NULL) | processing time | ✅ object position (0.0 fallback when telemetry is missing) | annotated snapshot (`dvas-intrusion`, 14 d); key contains `None` without mission | ❌ | ✅ **ready for an intrusion report**: breaches per zone, dwell, timeline, map |
| **Vessel** | `vessel_tracks` (class, size, direction, stationary, bbox, pixel trajectory, duration), `vessel_events` (`vessel_detected`, `proximity_alert`) | ✅ | DB `now()` | ❌ | first-sighting crop (`dvas-vessels-maritime`, 14 d), no mission in key | ❌ | ✅ vessel counts by class/size per mission, proximity alerts; no map |
| **Anomaly** | `anomaly_tracks`, `anomaly_events` (type, **severity**, 4 confidences, reasons, summary, bbox, **drone GPS**) | ✅ | DB `now()`; image names relative to service start | ⚠️ drone position, not object | full frame (`dvas-anomaly`, 14 d); keys collide after restart; key stored even if upload failed | ❌ | ✅ anomaly report by type/severity with reasons; ⚠️ images unreliable |

Never written by any code, though the schema has them:
- `detections` (bboxes);
- `evidence_clips` and `evidence_frames` (with `checksum_sha256`, `retention_until`, `legal_hold`);
- `audit_log` (analytics side);
- `*_mission_summaries` (intrusion, vessel, anomaly);
- `missions` (UUID table; services use text IDs).

`confirmed_events.mission_id/zone_id/latitude/longitude` are always NULL (`shared/db.py:158-177`).

### 2.2 Dashboard database (`dvas_dashboard`)

| Table | Filled by | For reports |
|---|---|---|
| `missions` | edge `POST /api/missions`; **auto-invented by `GET /missions`** with a hard-coded operator name (B26) | ⚠️ start/end reliable only for edge-created missions |
| `drones` | telemetry pump (current state only) | ❌ no history: readiness/battery in reports are *today's* values |
| `alerts` | edge incidents, AI-model suggestions, peer push, demo seed: **no AI detections** (B10) | ⚠️ only usable as "operator incidents" |
| `incidents`, `ai_model_suggestions` | edge pages | ✅ operator reports and model requests, with ack status |
| `evidence` | `evidence_sync` from `confirmed_events` (crowd, person, vehicle, ANPR `/analyze/frame` only), heuristic images, 90 d `retention_until` not enforced | ⚠️ see §1 points 2–4 |
| `exports`, `reports`, `audit_logs` | dashboard | ✅ export workflow and **hash-chained audit log** (the strongest part of the system) |

---

## 3. What the four current reports actually use

| Report | Uses | Ignores | Correctness problems |
|---|---|---|---|
| **Daily Incident Summary** | `alerts` of the UTC day (`LIKE 'date%'`), evidence **count**, **all missions ever** | images, every analytics table, GPS, plates, counts | UTC day for an IST force (B11); "alerts" ≠ detections (B10); top-20/10 truncation (B17) |
| **Weekly Response-Time** | last **500** alerts regardless of week, all missions | week parameter, detections, per-type analysis promised in the UI | wrong period (B11); SLA is computed over incidents/suggestions, not detections (B10) |
| **Drone Utilisation (30 d)** | all missions, current drone rows | the period, telemetry history, frames ingested | ≥ 0.5 h per mission (B13); readiness/battery = today's; invented missions (B26) |
| **Mission After-Action (AAR)** | mission row, its alerts and evidence (**falls back to the drone's other missions**), first 8 evidence IDs | images, intrusion/vessel/anomaly/plate data for the mission, timeline, map, frames/telemetry stats | borrowed data (B12); fabricated operator (B26); "cryptographically preserved" claim (B4) |

None of them embeds an image, a map or a chart. All four print Latin-1 text only (Malayalam names become `?`).

---

## 4. What better reports are possible — now vs after fixes

"**Now**" = the data exists in reliable form today; only report code (plan R2/R3) is needed.
"**After E1/R1**" = needs the capture fixes first.

### 4.1 Mission After-Action Report v2 (highest value)

| Section | Data source | When |
|---|---|---|
| Mission header (operator, supervisor, drone, zone, objective, IST start/end, duration) | `missions` (edge-created only), ingest mission start/end | **Now** (after removing invented missions, P0) |
| Flight & ingest statistics (frames received/queued/rejected, avg fps, bytes) | ingest `_ingest_stats` (in memory → **persist at mission end**, small R2 task) | After R2 |
| Track/flight map from telemetry | ❌ telemetry not persisted (only latest per drone) → persist a sampled track (1 point / 5 s) at ingest | After R2 (new table) |
| Intrusion summary: breaches per zone, entry/exit times, dwell, class, map of ground positions, snapshot thumbnails | `intrusion_events` by `mission_id` | **Now** (images only while < 14 d, until E2) |
| Vehicles & plates: distinct plates read, OCR confidence, watchlist hits, crops | `plate_reads`, `vehicle_plate_links` by `mission_id_str`; `theft_alerts` (dedupe per plate, join by time+drone) | **Now** (⚠️ last-read plate text only) |
| Vehicle mix (type, colour) | `tracks` + `vehicle_attributes` by mission (distinct tracker) | **Now** (⚠️ tracker collisions, B30) |
| Persons: unique individuals (Re-ID clusters), dedup run status | `person_identities`, `dedup_runs` by mission | **Now** |
| Face-watchlist matches with hashes | `person_watchlist_alerts` by mission (SHA-256 present) | **Now**; blur/PII rules per Q5 |
| Crowd: peak count, time above threshold, heatmap at peak | `crowd_density_readings` by drone + mission time window | After B27 fix |
| Vessels: counts by class/size, proximity alerts | `vessel_tracks`, `vessel_events` by mission | **Now** |
| Anomalies: by type & severity, reasons, GPS | `anomaly_events` by mission | **Now** (images ⚠️) |
| Operator incidents & model requests | `incidents`, `ai_model_suggestions` by mission | **Now** |
| Evidence register with hashes, legal-hold state, image thumbnails | `evidence` | After E1–E3 |
| Control-room response (detection → ack time, dispositions) | `alerts` from confirmed events | After R1 |

### 4.2 Daily Situation Report (SITREP), per district, IST day

Missions flown and hours (real durations), then one line per module:
- intrusions (count, worst dwell);
- plates read and watchlist hits;
- unique persons;
- crowd peaks (after B27);
- vessels;
- anomalies by severity.

Also included: operator incidents (open/closed), exports approved, and a map of all geo-located events (intrusion ground positions, anomaly drone positions).
**Now**, except crowd (B27) and response times (R1).

### 4.3 ANPR / vehicle-of-interest report (period or mission)
- Plate list with first/last seen, drone, mission, OCR confidence, plate + vehicle crop, colour and type.
- Watchlist hits highlighted (case reference: dropped today, `anpr/main.py:1081-1086`).
- Search by plate.

**Now** (14-day image window).

### 4.4 Intrusion / geofence report (zone, period)
- Breaches per zone and day, dwell distribution, object classes.
- Snapshot per breach, map of positions.
- Time to acknowledge: after R1.

**Now** (excluding rows recreated by `intrusion/recover_mission_events.py`; they have no provenance flag, B30).

### 4.5 Crowd event report
Peak and time-series per drone/mission, threshold breaches, heatmap at peak. **After B27** (plus a cooldown on threshold events).

### 4.6 Maritime (vessel) report
Vessels by class/size/direction, stationary vessels, proximity alerts, first-sighting crops. **Now** (no GPS: pixel trajectories only).

### 4.7 Anomaly report
By type and severity, reasons/summary text, confidences, GPS of the drone. **Now**. Images are unreliable until the key/upload fixes (E1).

### 4.8 Evidence register / case report
For a case (plan E4): every item with:
- hashes and capture time;
- chain of custody built from `audit_logs` (create, view, hold, export, download, each with actor/IP/time);
- export history.

**After E1–E4**. The audit chain part is possible **now**.

### 4.9 Detection quality report
Per module: alerts acknowledged as true/false positive by confidence band. Gives precision figures for tuning. **After R1**
(dispositions on ack).

### 4.10 Operations / utilisation (rebuilt)
- Real flight hours per drone/operator for the chosen period.
- Missions per district.
- Frames ingested, upload fps vs target 15–20 fps (Phase 8/11 numbers).

Analytics capacity used comes after the Phase 10 governor exists. Readiness is removed until status history exists.

---

## 5. Quick wins (small, safe, high value)

1. **Restore crowd startup** (B27): re-add the init in a FastAPI `lifespan` or startup handler, plus a health check that fails when
   `model_loaded` is false (`deploy/runpod/bin/healthcheck.sh` should check the JSON, not only HTTP 200). ~0.5 d. **Do this first.**
2. **Correct the four reports' queries** (B11–B13, plan P0/R2): IST ranges, respect week/period, no borrowed data, real durations. ~1–2 d.
3. **Add intrusion, plates and anomaly sections to the AAR** from their own tables (they are already mission-tagged). ~1–2 d.
4. **Unicode font** in the PDFs (Noto Sans + Malayalam) and "N of M" table notes (B17). ~0.5 d.
5. **Thumbnails in the AAR** for intrusion snapshots and plate crops, with "image expired" placeholders (until E2). ~0.5 d.

Items 2–5 don't change any schema, and none touches `dvas_edge`.

---

## 6. Data-quality caveats every report must print (until fixed)

Reports should state their own limits instead of hiding them:
- "Times are analyzer processing times (not frame capture time)." (Analytics stamps `now()`; the frame's capture time is never stored.)
- "Images older than 14 days (3 days for heatmaps) are not retained." (Until E2.)
- "Plate text shows the last OCR reading per vehicle track." (`plate_reads` overwrites.)
- "Locations: intrusion = estimated ground position of the object; anomaly = drone position; other modules have no location."
- "Counts of persons/vehicles are unique tracker IDs per mission and may double-count re-acquired objects."

---

## 7. Summary table

| Want in a report | Data today | Blocker | Plan phase |
|---|---|---|---|
| Correct period / day / hours | ✅ | query bugs | P0, R2 |
| Mission-scoped analytics (intrusion, plates, vessels, anomalies, persons) | ✅ | reports don't read it | R2, R5 |
| Crowd figures | ❌ (service broken) | B27 | P0 |
| Detection → response times | ❌ | detections aren't alerts | R1 |
| Evidence images that are right and still there | ⚠️ | heuristic match, 14 d expiry | E1, E2 |
| Hashes / certificate | ⚠️ face-watchlist only | no hashing elsewhere | E3, E4 |
| Maps | ⚠️ intrusion/anomaly only; no flight track | telemetry not persisted | R2, R3 |
| Malayalam names, IST, images in PDF | ❌ | Latin-1 core font | R3 |
