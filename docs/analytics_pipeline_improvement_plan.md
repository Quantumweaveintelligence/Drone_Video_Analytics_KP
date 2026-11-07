# Analytics pipeline — findings and implementation plan

*Version 4, 2026-11-07 (adds §2.D ingest queuing, §5 Phase 2a mission-keyed streams). Deployed on RunPod (image `20261106-d51729c`, RTX PRO 6000 Blackwell).*

Scope: the cloud analytics services (`dvas_analytics/`: person, vehicle, ANPR, intrusion, vessel,
anomaly, crowd), their ingest/dashboard touch points, and how changes ship to the RunPod pod.
Edge code is out of scope.

---

## 0. What the pod shows now

After `20261106-d51729c` (native face tiles, person input 1920, temporary CPU fallback):

| Field | Value | Meaning |
|---|---|---|
| Person & Face | 0.2 fps, **model ms 4080**, batch 1.0, input 1920, face tiles 640 | ~4 s per frame |
| GPU busy | **4 %** | the GPU is almost idle |
| CPU FALLBACK tag | **not shown** | the models are on the GPU |
| Lag | 101 s, input 0 fps | the edge stopped; a backlog is being worked off at 0.2 fps |
| Multi-Drone Grid | "0 LIVE STREAMS" but a blank DRN-010 tile, `GET …/frame/DRN-010/latest` → 404 | see finding A5 |

So ~4 s per frame is spent on something that is **not GPU compute**. The candidates, in the order
they must be ruled out (Phase 0 measures them on the pod):

1. **The PyTorch build has no native Blackwell kernels.** The base image `runpod/pytorch:2.8.0-…`
   ships `torch 2.8.0.dev20250319` (an early nightly). If `sm_120` is missing from
   `torch.cuda.get_arch_list()`, kernels run through PTX JIT / compatibility paths: slow while the
   GPU looks idle.
2. **CPU work on the critical path, with capped threads.** Measured on the build machine (4K frame):
   BoT-SORT motion compensation (GMC) **137 ms**, JPEG decode **37 ms**, face/person
   pre/post-processing. On the pod each process gets `OMP_NUM_THREADS = DVAS_CPUS/8` (2–8) threads,
   and the event loop runs blocking embedding work (B3).
3. **GIL / event-loop contention** from synchronous ResNet/ArcFace work in person (B3) stretching
   the inference thread's wall time.

---

## 1. RunPod constraints that shape every change

| Constraint | Consequence for the plan |
|---|---|
| One container, all services under supervisord, one GPU shared by 7 analyzer processes (each its own CUDA context) | Per-process GPU/CPU budgets matter; one service's CPU spike slows the others. |
| CPU is a cgroup quota (e.g. 27 vCPU on the L40S host) while `nproc` shows the host's cores | Thread counts must come from the quota (`DVAS_CPUS`), never `nproc`; CPU-heavy steps (decode, GMC, OCR) are the scarce resource. |
| GPU differs per pod (L40S 48 GB / RTX PRO 6000 96 GB, compute 8.9 / 12.0) | Everything GPU-specific is auto-detected (`gpu_profile`) with env overrides; TensorRT engines are per GPU. |
| Container disk is ephemeral; `/workspace` is the persistent network volume | Caches that are expensive to rebuild (TensorRT engines, CUDA JIT cache `CUDA_CACHE_PATH`, cuDNN autotune results) go to `/workspace`, keyed by GPU + driver + library version. |
| The image is ~24 GB, built on a machine with ~19 GB free after cleanup; every fix needs build → push → set image on the **pod** (not only the template) → restart | Group changes into a few releases; every behaviour change behind an env flag so it can be switched off without a rebuild. |
| Public access only through the RunPod proxy / Cloudflare (100 s idle timeout, limited bandwidth) | Live video stays ≤ 1920 px MJPEG; long requests need heartbeats. |
| Restart clears in-memory state (trackers, caches, governor caps) | Throttling caches (Phase 2) must tolerate a cold start (first write after restart is a full write). |
| `ptrace` is usually not permitted in RunPod containers (no `py-spy`) | Profiling is done in-process (`bench_person.py`, torch.profiler) and through the stage timings (A4). |
| Valkey is capped at 8 GB, streams hold raw JPEGs (4K ≈ 1–1.5 MB) | The backlog a slow analyzer builds costs memory; fixing throughput also protects ingest. |

---

## 2. Findings

### A. Correctness / stability

| # | Finding | Where | Effect |
|---|---|---|---|
| A1 | Person's face model is used from several threads **without a lock**: the stream's inference thread, `POST /analyze/frame`, and the watchlist enrolment endpoints (which also run inference on the event loop). | `person/main.py:221, 1664-1672, 1984-1991`, `person/analyzer.py` | Concurrent predicts corrupt the predictor's GPU state → CUDA errors (the likely cause of the earlier CPU fallback); endpoints stall the service. |
| A2 | `/annotated/latest` "recovery" runs the **newest** stream frame through the drone's **live** tracker when the cached view is a few seconds old. | `vehicle/main.py:538`, `anpr/main.py:851`, `vessel/main.py:634`, `anomaly/main.py:616` | With a backlog, a frame far ahead is injected between the frames being processed → ids jump, boxes flicker; extra GPU work on another thread. |
| A3 | Per-drone trackers are never reset (no `BatchTracker.reset`, `forget_drone`, `_sampler.forget`). | all analyzers | Old lost tracks re-match on the next mission; state grows with drone ids. |
| A4 | No per-stage timing; "model ms" mixes model, tracker and post-processing time. | `shared/frame_sampler.py` | The 4 s case cannot be attributed from the dashboard. |
| A5 | **Drone status is never updated when a stream is drained or purged.** Nobody handles ingest's `mission_drained` or the backend's `stream_purged` events, so the dashboard's drone record keeps "live"/"draining". The Multi-Drone Grid (`TacticalMapPage.js:167`) shows a tile for it and keeps polling a frame that no longer exists → 404 every 3 s, blank tile, while the counter says "0 LIVE STREAMS". (If the 404 body is "Drone not found" instead of "No frames available", it is the jurisdiction check in `ingest_proxy.py:138` — the officer's jurisdiction does not include the drone; same UI fix applies.) | ingest events, `CommandContext`, `TacticalMapPage.js`, `MultiDroneView.js` | Ghost tiles, console noise, wrong live counts. |

### B. Throughput

| # | Finding | Measured / estimated cost |
|---|---|---|
| B1 | **Write amplification in persist.** Vehicle, per vehicle **per frame**: `upsert_track`, a new MinIO crop object, an `UPDATE tracks`, a `vehicle_attributes` INSERT, a Valkey HSET (`vehicle/main.py:313-371`). Person: `upsert_track` per person per frame, crops + embeddings every 5th frame. ANPR similar. | 30 vehicles × 20 fps ≈ **600 MinIO objects/s, ~3,000 DB ops/s** for one service; persist (one round in flight) caps fps; storage grows without bound. |
| B2 | **JPEG decode serial, on the critical path,** 6 services decode the same frame. | 4K: **37 ms** CPU decode per frame per service. |
| B3 | **Blocking GPU calls on person's event loop**: ResNet appearance embeddings and ArcFace, one crop at a time, in `_persist_person_frame`. | Stalls stream loop, HTTP and MJPEG; no batching. |
| B4 | **ANPR OCR on the CPU** (`use_gpu=False`, 4 threads, `anpr/analyzer.py:404-412`). | OCR, not the GPU, limits ANPR fps with several plates. |
| B5 | **ArcFace on Blackwell unverified**: onnxruntime-gpu 1.20.2 may lack compute 12.0 kernels and fall back to CPU. | Unknown until checked on the pod. |
| B6 | **Catch-up runs one frame per drone per round** (ordered mode). | Backlogs drain at batch 1. |
| B7 | **No TensorRT** (deferred 3 Nov). | 1.5–2× on the YOLO models. |
| B8 | **No warm-up**; cuDNN autotunes on the first frame of each new shape / batch size. | Seconds-long stalls at stream start and when drones join. |
| B9 | **BoT-SORT GMC on the full frame, on the CPU, inside the tracker lock** — person, vehicle, ANPR, intrusion. | **137 ms per 4K frame** (sparseOptFlow at downscale 2; ORB 187 ms). Measured: predict 27 ms vs predict+track 157 ms. |
| B10 | **Base image PyTorch is an early nightly** (`2.8.0.dev20250319+cu128`). | Possibly no native sm_120 kernels (Phase 0 checks); nightlies also carry performance bugs. |

### D. Ingest frame queuing (`dvas_ingest/ingest/api/`)

| # | Finding | Where | Effect | Verified |
|---|---|---|---|---|
| D1 | **Frames are analysed in arrival order, but they arrive out of order.** The edge uploads up to 5 frames at once and retries failed uploads later (`dvas_edge/edge_api_server.py:446-470`); ingest XADDs in arrival order; since 4 Nov the analyzers process strictly in stream order. The sampler also counts a late frame as a gap. | `ingest_frame` (`main.py:372`), `shared/frame_sampler.py` `_next_ordered` | Trackers see time go backwards → id switches and jumping boxes (another cause of "annotation not consistent"); the Models page **Lost** count is wrong. | **Reproduced:** arrival 1,2,4,3,5,6 → analysed 1,2,4,3,5,6 and *2 frames reported lost, really 0*. |
| D2 | **Any DVAS role can upload frames and start/end missions, for any drone.** Ingest only checks that the token carries one of drone-operator / control-room / supervisor / sys-admin (`config.py:47`, `auth.py:102`); there is no link between the operator and the drone or mission. | `/ingest/frame`, `/ingest/mission/start`, `/ingest/mission/end` | A control-room or any operator token can push frames into another drone's stream (evidence integrity), end its mission, or cancel its drain. | Read in code. |
| D3 | **Frames are accepted for a drone with no mission, or for a mission other than the active one** (retries arriving after mission end or after the next mission started). | `ingest_frame` | Re-creates drained streams (the "1 frame remaining" orphans); interleaves two missions in one stream → ANPR resets its caches on every mission flip (`anpr/main.py:667`), and Phase 1's tracker reset would flap the same way. | Read in code. |
| D4 | **A WebSocket broadcast per frame**: `create_task(ws_manager.broadcast(...))` for every uploaded frame; `broadcast` sends to each client in turn with no timeout. | `main.py:176-190, 510` | One slow client → broadcast tasks pile up (memory) and delay the event loop that also serves uploads. 3 drones × 30 fps = 90 tasks/s. | Read in code. |
| D5 | **An INFO log line per frame** (`frame_queued`). | `main.py:475` | 90 lines/s at 3 × 30 fps; `/var/log/dvas/ingest.log` (50 MB × 3) rotates within minutes, so earlier errors are gone when needed. | Read in code. |
| D6 | **Rate limit 150 req/s per client IP** on `/ingest/` (`nginx.conf:40`). | nginx | Edge laptops behind one office NAT share an IP: 5 drones × 30 fps already hit it → 429 → retries → more out-of-order frames (D1). | Read in config. |
| D7 | **`drone_id` from the metadata is not validated** (no pattern / length). | `models.py:24` | Arbitrary stream keys, unbounded `_ingest_stats` / Prometheus label cardinality. | Read in code. |
| D8 | **`QUEUE_MSG_TTL_MS` is documented but never used.** | `config.py:55`, `queue_manager.py:40`, env example | Misleading setting (age-based trimming would also contradict "every frame, in order"). | grep. |
| D10 | **Streams are keyed by drone id, which is not a unique identity.** All queues and analytics state use `drone:frames:{drone_id}`. The edge takes the drone id from a setting or **derives it from the mission id** (`DRN-` + mission suffix, `dvas_edge/edge_api_server.py:523-529`), so ids like `DRN-010` / `DRN-0111111111` are reused and user-chosen. Mission ids are generated per flight with a timestamp (`MSN-YYYYMMDD-HHMM-SS-SQ`, `pages/edge/MissionControl.js:34`). | ingest `queue_manager._stream_key`, `shared/frame_sampler.stream_key`, `shared/redis_client`, every analyzer's per-drone state | Two edges using the same drone id write into **one** stream: frames of two flights interleave, trackers mix both feeds, evidence of two missions shares a queue. A new mission on the same drone inherits the previous mission's backlog, trackers and caches (A3, D3). | Read in code. |
| D9 | `MAX_FRAME_MB=5` rejects 4K **PNG** frames (PNG is an allowed type; a 4K PNG is ~10–20 MB). JPEG 4K (1–2.5 MB) is fine. | `ingest_frame` | Only matters if an edge ever sends PNG. | Read in code. |

### C. Smaller items

- C1 Anomaly camera-motion (ORB) and steps 3–9 run drone by drone in one thread.
- C2 Crowd CSRNet in FP32.
- C3 FP16 face recall on Blackwell not yet verified (only FP32 checked locally).

---

## 3. Evidence and recording rules (apply to every phase)

These rules decide what may be throttled. They exist so that **no throughput change can lose
evidence or reduce detections**.

1. **Detection is never throttled.** Every frame that is analysed today is still analysed: same
   models, input sizes, thresholds. OCR attempts and ArcFace matching per frame are unchanged.
2. **Never throttled, always written immediately, with the full-resolution source frame:**
   - every confirmed event and alert (theft alert, watchlist match, intrusion ENTERING/DWELLING,
     anomaly alert, crowd surge, vessel proximity, wrong-way, …),
   - every plate read (ANPR's plate crop + vehicle crop + source frame for theft alerts),
   - every face crop / face embedding / ArcFace embedding that is written today (face crops and
     embeddings every 5th frame stay exactly as they are — Re-ID clustering and the face gallery
     depend on them).
3. **Vehicle and person crops (the routine, non-event copies) per track:**
   - **first sighting:** always uploaded, so every track has at least one image however short it is;
   - **better crop:** replaced whenever a better one appears (larger area, sharper by Laplacian
     variance, fully inside the frame) — not on a timer;
   - **periodic trail:** one crop at most every `PERSIST_CROP_EVERY_S` (default **1 s**; set to 0
     to keep every frame as today), so a route can still be reconstructed crop by crop;
   - **final crop** when the track ends;
   - **alert moment:** when a track is part of any alert/event, its crop **and** the source frame
     are uploaded immediately (rule 2), regardless of the timers.
4. **Track rows** (`upsert_track`): on first sighting, then at most every `PERSIST_TRACK_EVERY_S`
   (default 2 s), and on track end; "last seen" therefore accurate to ≤ 2 s.
5. **`vehicle_attributes`**: written when type, colour or speed bucket changes, or every
   `PERSIST_ATTR_EVERY_S` (default 5 s).
6. **Before rule 4/5 ship**, audit every query that counts **rows** of `tracks`/`vehicle_attributes`
   (dashboards, reports, KPIs) and switch it to counting **distinct tracks**, so totals do not drop.
7. **Tests (required):** for every event type, a recorded clip run must produce the same events as
   before, each with its exact-moment crop and source frame in MinIO; every track has ≥ 1 crop.

---

## 4. Accuracy guard (for any change that can alter numbers)

Same recorded clips (person/faces at 1080p and 4K, vehicles with readable plates, an intrusion
geofence, a vessel scene) before and after:

- detections per class within ±1 % (faces: no loss allowed at all),
- plate reads: **no fewer correct reads**,
- watchlist similarity scores for the enrolled faces: change < 0.01,
- confirmed events: identical set.

A change that fails stays off (env flag) and is reported.

| Change | Risk to detection / OCR / faces |
|---|---|
| Locks, recovery fix, tracker lifecycle, stage timings, warm-up, decode prefetch (CPU), multi-frame catch-up, write throttling under §3 | none (by construction) |
| D1 re-ordering window | none for detections (late frames are still analysed, outside the live tracker); tracking becomes more stable |
| D2 ingest authorisation, D3 mission tagging, Phase 2a mission-keyed streams | none for detections; **operational risk** if the edge sent frames with another account's token → coordinate with the edge owner, ship behind `INGEST_ENFORCE_OWNER=1` |
| GPU JPEG decode (nvJPEG), batched embeddings, onnxruntime upgrade, crowd FP16, GMC downscale | low — accuracy guard decides |
| ANPR GPU OCR, TensorRT engines (esp. the face model), base-image PyTorch upgrade | real — A/B on recorded clips; keep the old path behind a flag |

---

## 5. Implementation plan

Every phase = one image release (`build_image.sh` → `push_ghcr.sh` → set the image **on the pod**
→ restart → `RELEASES.md`), with the benchmark run on the pod before and after.

### Phase 0 — Measure on the pod (½ day, no code change on the pod)

`deploy/runpod/tools/bench_person.py` (added; read-only, loads its own model copy) reports the
PyTorch build's GPU architectures, a GPU sanity matmul, thread counts, and per-stage times
(decode, person predict, predict+track, face tiles, full analyze) plus the top operators of one
frame. Until it is in an image, paste it into the pod's web terminal and run:

```bash
cd /analytics && /opt/venvs/ai/bin/python /path/to/bench_person.py --drone DRN-010
```

Also collect: `grep -iE "cuda|cpu|provider" /var/log/dvas/person.log | tail -40`,
`nvidia-smi dmon -s u -c 30` while one drone streams, and the 404 response body for
`/api/ingest-server/frame/DRN-010/latest`.

**Decision:** if `sm_120` is missing or the matmul is far below expectation → do **Phase 1b** first.

### Phase 1 — Correctness, visibility, quick wins, ingest ordering (≈ 3 days)

1. **A1 model locks** — one `RLock` per Ultralytics model (person, face, and the same audit for the
   other services' endpoint paths); endpoint inference via `asyncio.to_thread`.
2. **A2 recovery** — while a stream runs, `/annotated/latest` serves the cached annotated frame (with
   `X-Frame-Age`); recovery inference only when the stream is stopped, on a separate tracker key.
3. **A3 tracker lifecycle** — reset on an explicit mission-id change or after 120 s without frames
   (configurable); never on a missing mission id.
4. **A4 stage timings** — decode / model / tracker / post / persist / wait per drone in the sampler
   stats; Models page shows the split; warning in the log when a stage exceeds budget for 30 s.
5. **A5 drone status** — backend handles `mission_drained` / `stream_purged` (WebSocket) → drone
   status offline; Multi-Drone Grid and map tiles: non-live drones show the last annotated frame or
   an "Offline / Draining — N left" placeholder and **stop polling** raw frames after a 404 (retry
   every 30 s); the live counter and the tiles use the same rule.
6. **B9 GMC** — run BoT-SORT motion compensation at `downscale` 4 (≈ 960 px wide on 4K; expected
   ~4× less than 137 ms) via a tracker YAML shipped with the analytics (`DVAS_GMC_DOWNSCALE`), with
   the accuracy guard on track-id stability (id switches per minute on a moving-drone clip).
7. **C3 FP16 face recall check** on the pod (accuracy guard); `DVAS_FP16=false` for the face model
   only if recall drops.
8. **B8 warm-up** at service start (dummy batch per expected shape and batch size).
9. **D1 re-ordering** (analytics only, no edge change):
   - the ordered sampler reads up to K entries per drone and holds them for a short window
     (`DVAS_REORDER_MS`, default 300 ms) to emit them in `frame_seq` order (per mission;
     `capture_ts` as fallback);
   - a frame that arrives after a newer one was already analysed is still analysed for
     **detections and alerts**, but through a separate "late" path that does not update the live
     tracker (no frame is dropped, no id switches);
   - Lost = real gaps only (a later arrival fills its gap); new **Late** counter on the Models page;
   - test: arrival 1,2,4,3,5,6 → analysed 1–6 in order, Lost 0.
10. **D3 mission tagging** (interim until Phase 2a; superseded by it) — ingest stamps each entry with the drone's active mission; frames for an
    ended or other mission get `late_mission=1` (accepted, never silently dropped), and analyzers
    never treat a backward mission change as a new mission (no cache/tracker flapping). Frames for a
    drone with no mission are accepted only within 30 s of its last mission end.
11. **D4 / D5 ingest hot path** — WebSocket "frame_queued" coalesced to ≤ 2 Hz per drone with a
    per-client send timeout (slow clients dropped); `frame_queued` logged once per second per drone
    as a summary (count, bytes, latency), errors still logged individually.
12. **D7 / D8** — validate `drone_id` (`[A-Za-z0-9_.:-]{1,64}`, same rule as the backend); remove
    `QUEUE_MSG_TTL_MS` from code, `dvas-init.sh` and the env example.

### Phase 1b — Runtime fix for Blackwell (only if Phase 0 points there; ≈ 1–2 days)

- Move the base image to a **stable** PyTorch release with native sm_120 kernels (e.g. a
  `runpod/pytorch` tag with torch ≥ 2.7 stable, cu128); rebuild; accuracy guard; keep the old image
  tag in `RELEASES.md` for rollback.
- Put `CUDA_CACHE_PATH=/workspace/dvas/cuda-cache` and `CUDA_CACHE_MAXSIZE=4294967296` in
  `dvas-init.sh` so any PTX JIT happens once per GPU/driver, not on every restart.

### Phase 2a — Mission-keyed streams (≈ 4–6 days; replaces D2/D3 and most of A3)

**Goal:** every flight has its own queue, its own analytics state and its own owner. The mission id
is the identity; the drone id becomes a label.

*As built (2026-11-07). The first draft keyed every analytics structure and dashboard tile by
mission id; the implementation keeps the drone id as the key above the queue and makes the queue
itself per mission. That gives the same isolation — one flight per stream, fresh trackers per
mission — without changing the dashboard, the Models page, results or database rows. It works
because ingest now refuses two operators flying the same drone id at the same time (below), so at
any moment a drone id names at most one live flight.*

**Valkey layout** (`sid` = mission id with characters outside `[A-Za-z0-9_.:-]` replaced by `_`;
mission ids may contain spaces)

| Key | Type | Content |
|---|---|---|
| `mission:frames:{sid}` | stream | frames of one mission (same fields as before, plus `mission_id`) |
| `mission:index:{sid}` | hash | `drone_id`, `mission_id`, `operator_sub`, `status` (`live`/`draining`/`ended`), `started_at`, `ended_at`; kept 7 days after purge for audit |
| `drone:missions:{drone_id}` | sorted set | sids of the drone's missions whose stream exists, scored by start time |
| `drone:active_mission:{drone_id}` | string | sid of the drone's newest mission |

**Ingest** (`dvas_ingest/ingest/api/`)
- `mission/start` records the caller's token subject as the operator. **409** when the mission id
  exists for another operator or drone, or when the drone has a live/draining mission of a
  **different operator** (two flights never share a drone id). The same operator re-sending start
  is idempotent; the same operator starting a new mission on the drone moves the previous one to
  `draining`.
- `frame` requires a started mission (**409** `mission_not_started`), the mission's operator
  (**403**) and the mission's drone id (**409**). Frames of an ended mission are accepted within
  `MISSION_GRACE_S` (30 s) for in-flight retries and go to **that** mission's stream.
- `mission/end`: the mission's operator, a supervisor/sys-admin, or the backend service account.
- Drain, purge, orphan cleanup, remaining/unread counts and the memory guard work per stream; a
  drained mission's stream is removed with its registry entries and `mission_drained` is broadcast
  as before. No placeholder `analytics` group is created on mission streams.
- Drone-addressed endpoints keep their URLs and response shape (`/streams/active`, `/status`,
  `/frame/{drone}/latest`, MJPEG, purge): they aggregate the drone's mission streams;
  `/streams/active` adds a `missions` list per drone.

**Analytics** (`shared/mission_streams.py`, `shared/frame_sampler.py`, `shared/redis_client.py`)
- Drones are discovered from `drone:missions:*` (plus non-empty legacy `drone:frames:*`).
- Per drone the sampler reads its streams **oldest first** — legacy stream, then missions by start
  time — and moves to the next one only when the current mission no longer takes frames (status
  `live`, or `draining` for less than `DVAS_MISSION_SWITCH_S` = 5 s) **and** this service's group
  has read all of it. Frames of one mission are therefore never analysed after the next mission's.
- At each mission boundary the sampler clears the re-order window and sequence state and resets the
  drone's trackers (`BatchTracker.reset_everywhere`) and per-drone analyzer state (`on_idle` hooks:
  vessel, anomaly) — a new mission never continues the last one's tracks (A3).
- Consumer groups, pending re-reads and "queued" counts are per stream; "queued" on the Models page
  sums every stream of the drone. Recovery frames, `get_latest_stream_frame` and `dvas-ctl lag`
  use the drone's active mission stream.
- Unchanged: drone id as the key of trackers, stats, governor rows, results and database rows
  (which already carry `mission_id`); detection, OCR and face models see the same frames.

**Backend / dashboard:** no change needed — they address drones, and a drone id names one live
flight at a time. Per-mission tiles (two simultaneous flights with the same drone id) are not
needed because ingest refuses that case.

**Compatibility and rollout (RunPod)**
- `STREAM_KEY_MODE=mission|drone` in `dvas-init.sh`, default `mission` (confirmed). `drone` is the
  rollback: today's per-drone streams, no ownership checks — set it for ingest and analytics alike.
- Pods upgrade with data in place: existing `drone:frames:*` streams are still listed, drained and
  purged, and analytics read them before the drone's first mission stream.
- **Edge contract:** the edge already sends `mission_id` in every frame and calls `mission/start`
  first (MissionControl, `edge_sender`), so no edge change is expected. Before release, confirm with
  the edge owner that (a) every upload follows a `mission/start`, and (b) each laptop uses its own
  operator account; uploads that break either rule would get 409/403.
- Tests (real Valkey, `dvas_ingest/ingest/tests/test_mission_streams.py`,
  `dvas_analytics/shared/tests/test_mission_streams.py`): a second operator starting a mission on a
  live drone id → 409; missions of one drone are analysed oldest first with one tracker reset
  between them; a mission restart on the same drone starts with empty trackers; a frame with a foreign
  token or another drone id → rejected; late retries after mission end land in their own mission's
  stream and are drained, not orphaned.

**Risk:** no change to detection, OCR or face recognition (same frames, same models). The risk is
operational — an edge that uploads without `mission/start` or with a shared account would be
refused — hence the confirmation step and the `STREAM_KEY_MODE=drone` rollback.

### Phase 2 — Remove the non-GPU bottlenecks, ingest authorisation (≈ 3–4 days)

13. **B1 write throttling under §3** — per-drone track cache; first/best/periodic/final/alert crops;
   `executemany` per round; Valkey pipeline; query audit (rule 6); tests (rule 7). Flags:
   `PERSIST_TRACK_EVERY_S`, `PERSIST_CROP_EVERY_S`, `PERSIST_ATTR_EVERY_S` (0 = today's behaviour).
14. **B2 decode prefetch** — decode round N+1 while N is on the GPU; drones in parallel (thread
    pool sized from `DVAS_CPUS`). nvJPEG only if it wins on the pod and passes the guard.
15. **B3 person embeddings** — ResNet + ArcFace in a worker thread, batched per frame.
16. **B5 ArcFace provider** — show the provider on the Models page; if CPU on Blackwell, upgrade
    onnxruntime-gpu to a release with compute 12.0 support (verify against the image's cuDNN 9).
17. **D2 ingest authorisation** (delivered by Phase 2a if 2a ships; otherwise as below) — frame upload and mission start/end accept only drone-operator
    (and the backend service account); `mission/start` records the operator's `sub`, and frames /
    `mission/end` for that drone are accepted only from the same `sub` (or a supervisor/sys-admin
    for end). **Edge contract:** the edge already uses the operator's own token, so no edge change is
    expected — confirm with the edge owner before release (a 403 here would stop uploads).
18. **D6 rate limit** — key the ingest limit on the bearer token's subject (or raise it to cover
    the planned drones × 30 fps with headroom, e.g. 600 r/s burst 600) so drones behind one NAT do not
    share a 150 r/s budget.

**Done when:** person/vehicle/ANPR hold ≥ 20 fps per drone on 4K with persist on, the stage split
shows the GPU as the largest share, and §3 tests pass.

### Phase 3 — Make the GPU faster (≈ 4–6 days, each step measured on the pod)

19. **B6 multi-frame catch-up** — up to K consecutive frames per drone per round when a backlog
    exists; batched detection, tracker updated in frame order.
20. **B4 GPU OCR** — PaddlePaddle build matching CUDA 12.8 / cuDNN 9, or PP-OCR recognition as ONNX
    on onnxruntime-gpu with batched plate crops; ship only if correct plate reads ≥ today.
21. **B7 TensorRT** — engines built on the pod into `/workspace/dvas/engines/<gpu>-<trt>/`, `.pt`
    fallback, `DVAS_TRT=1`; face model only if face recall is unchanged.
22. **C1, C2** as measured.

---

## 6. Rollback

Each item sits behind an env flag in `dvas-init.sh` / the RunPod template, so it can be switched off
by editing the pod's environment and restarting — no rebuild. The previous image tag stays in
`RELEASES.md`; local images kept on the build machine: the current and the previous release only.
