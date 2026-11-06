# Context: GPU-aware analytics throughput (branch `runpod-v1`, 2026-11-03)

This file covers only the change that makes the cloud analytics detect the GPU and push every
analyzer towards 20–30 fps (RunPod plan Phases 9.1, 9.2, 9.3, 9.6, 9.10 and 10). TensorRT
(9.4) was deliberately left out.

Decisions taken with the product owner:

| Question | Decision |
|---|---|
| Which analyzers hold 20–30 fps | Every analyzer **except crowd** (crowd stays at `CROWD_MAX_FPS`, 2 fps) |
| GPU full | All analyzers slow down **equally**, never below the floor (default 5 fps) |
| Levers | FP16 + cuDNN autotune, and batching the newest frame of all drones. **Inference resolution is never changed** |
| Can't keep up | **Every frame, in order** (changed 2026-11-04; was newest-frame-only). The backlog waits in the queue and is worked off, also after the edge stops. Frames are lost only if the queue overflows (counted as *lost*). Crowd still analyses the newest frame only |
| Models page | SO/SA edit the target range; CRO sees it read-only |
| TensorRT | Not now |
| Queue length | 2000 frames per drone |

---

## 1. Edge application: not affected

- Nothing under `dvas_edge/` or `DVAS_Dashboard/frontend/src/pages/edge/` changed.
- The edge → cloud upload protocol is unchanged. Ingest still accepts the same requests; only how it
  stores a frame in Valkey changed (raw JPEG only, see §4).
- **For the edge owner:** the edge sends **10 fps** by default (`dvas_edge/edge_api_server.py:109`,
  `bypass_target_fps: 10`). The cloud cannot analyse more frames than it receives, so to see 20–30 fps
  the edge has to send 20–30 fps. The Models page shows this as *"Limited by drone input"*.

---

## 2. How it works

```
edge ──► ingest ──XADD (raw JPEG, MAXLEN 2000)──► Valkey drone:frames:<drone>
                                                     │  one XREADGROUP for all due drones
                                                     ▼
            analyzer ── FrameSampler: next frame per drone, in order (crowd: newest only)
                     ── BatchTracker / batched predict: one GPU batch, one tracker per drone (FP16)
                     ── persist task (DB / MinIO / events) runs while the next batch infers
                     ── every 2 s: dvas:svc_stats:<service>
                                                     ▼
            gateway governor (every 2 s): one common cap ──► dvas:fps_cap:<service> (TTL 10 s)
                                                     ▼
            backend GET /api/models/performance ──► Detection Models page
```

### GPU profile (`dvas_analytics/shared/gpu_profile.py`)

| Tier | VRAM | Example | Drones per batch |
|---|---|---|---|
| small | < 8 GB | 4 GB laptop GPU | 1 |
| medium | 8–24 GB | RTX 4090 | 2 |
| large | 24–64 GB | L40S 48 GB | 4 |
| xlarge | ≥ 64 GB | RTX PRO 6000 96 GB | 8 |

- FP16 on compute ≥ 7.0, **except GTX 16xx** (no tensor cores; FP16 output was wrong on the local
  GTX 1650 in testing). TF32 on Ampere+. `cudnn.benchmark` on.
- Overrides: `DVAS_FP16=auto|true|false`, `DVAS_MAX_BATCH`, `DVAS_CUDNN_BENCHMARK`.
- Frames of different sizes are never batched together (Ultralytics pads a mixed batch to a square,
  which would shift boxes).

### Governor (`dvas_analytics/gateway/governor.py`)

- Starts at `target_max`. If any analyzer delivers < 85 % of `min(cap, input)` **and** the GPU is
  ≥ 90 % busy for 2 cycles → cap × 0.85 (not below `floor`). Otherwise, with headroom → cap + 2.
- A shortfall while the GPU is idle is reported as CPU/IO-bound and does not lower the cap.
- `GOVERNOR_ENABLED=false` → analyzers use the static `DVAS_FPS_MAX`.
- If the gateway stops, the caps expire within 10 s and analyzers fall back to `DVAS_FPS_MAX`.

---

## 3. Correctness fixes that came with batching

- **Trackers were shared across drones.** Every service called one `model.track(persist=True)` for all
  drones, so drone A's detections were matched against drone B's tracks. Now each drone has its own
  tracker (same BoT-SORT/ByteTrack settings; ids stay unique across drones). Verified: boxes identical
  to `.track()` for a single drone.
- **ANPR Re-ID** pooled lost vehicles across drones; now per drone (off by default, `ANPR_ENABLE_REID`).
- **ANPR frame counter** was shared by all drones, so OCR cadence (every 5 / 25 / 30 frames per track),
  Re-ID expiry and first/last-seen frames sped up with every extra drone (3 drones ≈ 3× the OCR reads
  per plate, on the CPU). Now one counter per drone: checked with 1 vs 3 drones, 6 OCR attempts per
  track in 30 frames either way. Single-drone behaviour is unchanged.
- **Vessel** had one tracker for all drones; now one per drone, weights loaded once.
- **Anomaly** loaded YOLO11x + YOLO11n **per drone**; now loaded once and shared (≈ 2 GB VRAM saved
  per extra drone). Trackers / rules stay per drone.
- After a CUDA → CPU fallback, FP16 is switched off and the predictor rebuilt (it used to keep
  `half=True` on CPU).

---

## 4. Valkey queue: 2000 frames, stored once

- `QUEUE_MAX_LENGTH` default 2000 (ingest code and `dvas-init.sh`; was 300 on the pod).
- Each entry now holds the raw JPEG only. The base64 copy (`jpeg_b64`) doubled memory per frame;
  every analytics reader now uses the raw `frame` field (with a `jpeg_b64` fallback for old entries).
  `QUEUE_WRITE_JPEG_B64=true` brings the copy back for an older analytics build.
- Memory at 2000 frames: ≈ 100 KB (720p) – 250 KB (1080p) per frame → 0.2–0.5 GB per drone
  (it would have been 0.47–1.17 GB with the base64 copy).
- **Memory guard:** streams have no TTL, so Valkey (`maxmemory 8gb`, `volatile-lru`) cannot evict them;
  at the limit every XADD would fail and ingest would stop. Above `QUEUE_MEM_GUARD_PCT` (85 %) of
  maxmemory, ingest trims the stream it writes to `MAXLEN/4` until memory recovers (logged as
  `valkey_memory_high` / `valkey_memory_ok`).
- Analyzers read every frame in order (one per drone per round), so the 2000-frame queue is the
  buffer that lets them catch up: ~100 s at 20 fps. Entries a consumer was given but never acknowledged
  (crash/restart) are re-read first. Crowd keeps reading only the newest frame.
- **4K memory:** a 3840×2160 JPEG is ~1–1.5 MB, so 2000 frames ≈ 2–3 GB per drone. With Valkey's
  `maxmemory 8gb`, the memory guard starts trimming (to 500 frames) at about 3 drones streaming 4K;
  frames an analyzer has not reached by then are lost (shown as *Lost*). Raise `maxmemory` in
  `deploy/runpod/etc/valkey.conf` if the pod has the RAM.

### Mission end: the queue is worked off before cleanup (fixed 2026-11-04)

When the edge ends a mission, ingest waits ≥ 20 s and then deletes the drone's stream once every
analyzer is "fully consumed". That check trusted Valkey's `lag`, which is null once MAXLEN trimming has
passed a group's position (every long mission), and treated null as caught up. The stream was deleted
while an analyzer still had hundreds of frames to read (seen: 100 left in intrusion, 300 in person).
Now a group counts as done only when its last-delivered id has reached the stream's last id and it has
nothing pending; groups without consumers (the placeholder `analytics` group) are ignored. The
"frames remaining" figure in ingest status is now what the slowest analyzer still has to read
(it was the stream length, which includes frames already analysed). Verified against Valkey 7.2.

## 2b. Live annotated video (MJPEG, 2026-11-04)

The analytics pages showed ~1 fps because the browser downloaded one annotated JPEG at a time
(poll every 0.6–1.5 s plus the full-size download through the RunPod proxy). That number was the
page's refresh rate, not the analyzers' processing rate.

- Each analyzer serves `GET /annotated/stream?drone_id=` (intrusion: `/intrusion/annotated/stream`):
  `multipart/x-mixed-replace`, one part per new frame, part headers `X-Frame-Seq`, `X-Proc-Fps`
  (the analyzer's real rate), `X-Frame-Age`. Backend relay: `GET /api/analytics/{service}/annotated/stream`
  (CRO/SO, 8 streams per user, 40 per pod).
- **Resolution:** detection runs on the full frame (faces, plates, tiles: unchanged). The live copy is
  ≤ 1920 px wide (`DVAS_LIVE_MAX_WIDTH`, JPEG q80 `DVAS_LIVE_JPEG_Q`): ~200 KB per 1080p frame instead of
  1–2 MB at 4K, so 20+ fps fits through the proxy. Overlays are drawn so labels keep their designed size
  on 4K (vehicle/ANPR/person/vessel draw on the 1920 px copy; intrusion/anomaly scale text with width).
  **Evidence keeps full resolution** (intrusion/anomaly annotated evidence is encoded from the full image).
- Frontend `hooks/useLiveFeed.js` reads the stream with `fetch` (cookie or token auth), shows the newest
  frame, pauses on hidden tabs, reconnects, and falls back to polling if the stream route is missing.
  Pages show **Processing** fps (from the analyzer) separately from the view rate. Raw ingest frames are
  polled only while no annotated video is available.
- Person drew its overlay and encoded the 4K JPEG on the event loop; that now runs in a worker thread.

---

## 5. API

| Endpoint | Roles | Purpose |
|---|---|---|
| `GET /api/models/performance` | CRO, SO, SA | GPU profile, governor state/reason/cap, policy, per-service × drone fps, input fps, model ms, lag, skipped. Re-sends the stored policy if the gateway has a different one |
| `PUT /api/models/performance/policy` | SO, SA | `{target_min, target_max, floor}`, integers 1–60, `floor ≤ min ≤ max`, unknown fields rejected; 20/min rate limit; audited as `fps_policy_update` |
| `GET /api/models` | unchanged + `live` per model | measured fps per model (None until it has streamed) |
| gateway `GET /governor/status`, `PUT /governor/policy` | service key | internal |

The policy is stored with the other run-time settings (`analytics_fps_min/max/floor`), so SA can also
change it under Admin → System settings; the backend pushes it to the gateway on startup and on change.

---

## 6. Environment

| Variable | Default | Where |
|---|---|---|
| `DVAS_FP16` | `auto` | analyzers |
| `DVAS_MAX_BATCH` | from GPU tier | analyzers |
| `DVAS_FPS_MIN` / `DVAS_FPS_MAX` / `DVAS_FPS_FLOOR` | 20 / 30 / 5 | gateway fallback until the backend pushes the stored policy; `DVAS_FPS_MAX` is also the analyzers' cap without a governor |
| `GOVERNOR_ENABLED` | `true` | gateway |
| `QUEUE_MAX_LENGTH` | 2000 | ingest |
| `QUEUE_WRITE_JPEG_B64` | `false` | ingest |
| `QUEUE_MEM_GUARD_PCT` | 85 | ingest |

`nvidia-ml-py` was added to the gateway venv (GPU utilisation for the governor).

---

## 7. Verified / not verified

Verified locally (GTX 1650, Ultralytics 8.3.253, FP32):
- Batched detection + per-drone tracking gives the same boxes as `.track()` for every converted service
  (vehicle, person, ANPR, intrusion, vessel, anomaly); output formats unchanged.
- In-order reading, overflow counting, crash re-read and the mission-drain check verified on a real
  Valkey 7.2; MJPEG relay verified end to end (4K in → 1920×1080 parts, parsed by the browser hook).
- Unit tests: GPU tiers, sampler (in-order mode, newest mode, overflow, cap bounds), governor (overload,
  hysteresis, floor, input-limited, CPU-bound), backend endpoints (roles, validation, audit, gateway
  down), crowd stream evidence. Analytics 43, backend 100, deploy 9 tests pass. Frontend builds.

An independent review of the full diff found no security issues; its correctness findings were
fixed: backlog jump (above), stream stop no longer cancels a half-written persist round
(`asyncio.shield`), anomaly's OOM downscale applies to that call only (the detector is shared),
the governor holds its cap when GPU utilisation is unknown, vessel engine and intrusion model moves
are locked, intrusion's CPU fallback sticks, ingest tolerates a bad `QUEUE_MEM_GUARD_PCT`, and a
policy change under Admin → System settings is pushed to the gateway too.

Not verified:
- Real throughput and FP16 accuracy on L40S / RTX PRO 6000 — check on the pod with the Models page.
- The services against real Postgres / Valkey / MinIO (only stubbed loop tests for vessel/intrusion).

Known limits:
- ANPR OCR (PaddleOCR, CPU) and anomaly's per-drone camera-motion step are CPU work done one drone
  after another; at many drones they, not the GPU, may set the frame rate (the Models page then says
  "limited by CPU/IO").
- Per-drone trackers are not reset when a drone stops and later resumes (old lost tracks can re-match).
- Person / vehicle / ANPR model moves on stream stop (only with `VRAM_OFFLOAD_ON_STOP=true`, off on the
  pod) still don't take the batch lock.

---

## 8. Fixes 2026-11-06

| Report | Cause | Fix |
|---|---|---|
| Stream stays "DRAINING — 1 frames remaining" after all frames were analysed | A frame still in flight from the edge arrived after the drained stream was deleted and re-created it with one entry. That stream belonged to no mission, so nothing ever cleaned it up. The page also showed the stream length, which includes frames already analysed. | Ingest removes a stream that belongs to no mission once every analyzer has read it and no frame arrived for 30 s (`_cleanup_orphan_streams`, every 15 s). `/ingest/streams/active` `messages_ready` = frames the slowest analyzer still has to read (`frame_count` stays the stream length). The newest stream id is taken from ingest's own XADDs, so the checks never download a frame. Verified on Valkey 7.2. |
| Purge missing in Multi-Drone View | The role audit (2 Nov) limited purge to SO/SA. | Control room officers may purge too (backend `PURGE_ROLES`, frontend). Still audit-logged. |
| CRO cannot start/stop modules on the Detection Models page | Audit item M9 made the shared model set SO/SA only. | `MODEL_ADMIN_ROLES` includes CRO (deploy is audit-logged as `model_config_update`). The FPS governor policy stays SO/SA (`FPS_POLICY_ROLES`). |
| CRO cannot switch drones on the Person page (Crowd had the same lock) | The page always passed a drone (the selected one, else the first in the list), which disabled the feed's drone pills, and there is no drone selector at the top of those pages. | The page passes a drone only when one was picked; the pills update the shared command selection (`onSelectDrone`). |

## 9. Person & face on the big GPUs (2026-11-07)

Seen on the RTX PRO 6000: person at 0.2 fps, 3.3 s model time per frame, GPU 0 % busy, face
boxes appearing and disappearing.

| Cause | Fix |
|---|---|
| One CUDA error switched person **and** face to the CPU for good (`self.device = "cpu"`, never reset), which explains 3.3 s/frame with an idle GPU. | The fallback is temporary: the GPU is retried every `PERSON_GPU_RETRY_S` (60 s); the error is logged at ERROR level and the Detection Models page shows **CPU FALLBACK** with the CUDA message. |
| Faces were searched on 2 tiles of ~2070 px each, shrunk to 640 px — a 4K frame lost ~3× resolution before face detection, so small faces from drone height were missed or flickered. | Faces are searched at **native resolution**: 640 px tiles with 96 px overlap (4K → 28 tiles, 1080p → 8), all in one GPU batch; partial duplicates at tile edges are removed. Local test (synthetic 4K, faces 36–71 px): old 77–98 % found with up to 121 false boxes; new 100 % with 0 false. |
| The person detector looked at 4K frames through a 640 px input. | `PERSON_IMGSZ=auto`: 1920 on the RTX PRO 6000, 1280 on the L40S, 640 on small GPUs. |

**Still to confirm on the pod:** what the original CUDA error was (`grep -iE "cuda|cpu" /var/log/dvas/person.log`).
If it repeats on the GPU, the page will keep showing CPU FALLBACK with its message.
