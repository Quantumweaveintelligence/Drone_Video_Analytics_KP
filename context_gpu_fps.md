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
| Can't keep up | Analyse the newest frame only; older ones are acked unread and counted as *skipped* |
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
            analyzer ── FrameSampler: newest frame per drone, rest XACKed (skipped)
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
- With newest-frame-only, analyzers never work through the backlog; the longer queue helps the
  ingest `/latest` endpoint, drain and crowd recovery, not analysis.
- New consumer groups start at the newest entry (`$`). When a group finds a backlog (≥ 32 waiting,
  e.g. after a stall), it reads one chunk, then jumps with `XGROUP SETID` to the newest entry instead
  of transferring every queued frame. After such a jump `XINFO GROUPS` reports `lag` as null until the
  next delivery; the ingest mission drain already treats null lag as caught up.

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
- Unit tests: GPU tiers, sampler (newest frame, backlog drain, cap bounds), governor (overload,
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
