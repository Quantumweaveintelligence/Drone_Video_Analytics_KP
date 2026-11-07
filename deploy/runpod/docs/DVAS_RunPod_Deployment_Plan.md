# DVAS — RunPod Deployment Implementation Plan (v2)

> **Kerala Police Drone Video Analytics System (DVAS)**
> Target: 1 × RunPod Secure Cloud pod (NVIDIA L40S 48 GB) — dashboard front-end, back-end, ingest, Keycloak, analytics and data services
> Source branch: `testing` → new deployment branch: `runpod-v1` · Working copy: `/mnt/NewVolume/DVAS_RUNPODV1`
> Scope: RunPod cloud deployment (owner: you). The edge `.exe` is a separate plan: `DVAS_Edge_EXE_Build_Plan.md` (owner: edge teammate). Branch & VS Code setup: `DVAS_RunPodV1_Branch_and_VSCode_Setup.md`.
> Initial test load: **3 concurrent drone feeds × 30 fps**
> Prepared: 26 Oct 2026 · Rev 2: restored `crowd/main.py` reviewed (F1 resolved, F19–F21 + Phase 2.5.1) · Rev 3: edge `.exe` moved to its own plan, branch renamed `runpod-v1`, working copy `/mnt/NewVolume/DVAS_RUNPODV1` · Supersedes `implementation_planRUNPOD_UPDATED.md` (that plan assumed Docker Compose inside the pod, A100/H100 GPUs and 20 feeds — none of which match this deployment)

---

## How to use this document

The work is split into **14 phases (0–13)**. Each phase is self-contained and can be done, verified and signed off before the next one starts:

| Block | Phases | Outcome |
|---|---|---|
| **A. Prepare** | 0 – 2 | Accounts ready, `runpod-v1` branch created, blockers fixed, code made cloud-ready |
| **B. Build & deploy** | 3 – 6 | All-in-one image built and pushed, pod running, platform bootstrapped, dashboard live |
| **C. Edge handoff** | 7 | Edge teammate has everything needed to build the `.exe` against the pod (the `.exe` itself is in `DVAS_Edge_EXE_Build_Plan.md`) |
| **D. Validate & optimise** | 8 – 11 | Baseline measured → performance work → adaptive FPS governor + GPU advisor → 3 × 30 fps acceptance test |
| **E. Operate & harden** | 12 – 13 | Runbook, backups, retention, updates, security hardening |

Every phase uses the same layout:

- **Goal** – what the phase achieves
- **Where** – which machine you work on (💻 dev/build machine · ☁️ RunPod pod · 🪟 Windows edge laptop)
- **Prerequisites** – phases that must be complete first
- **Steps** – what to do, in order
- **Files** – what gets created or changed in the repo
- **✅ Done when** – exit checklist; don't start the next phase until every box is ticked
- **↩ Rollback** – how to undo if something goes wrong

Section 1 (decisions and findings) and Section 2 (target architecture and capacity) are background for all phases; read them once before Phase 0.

---

## Table of contents

1. [Decisions & findings from the code review](#1-decisions--findings-from-the-code-review)
2. [Target architecture & capacity planning](#2-target-architecture--capacity-planning)
3. [Phase 0 — Prerequisites & accounts](#phase-0--prerequisites--accounts)
4. [Phase 1 — Branch & repository preparation (blocker fixes)](#phase-1--branch--repository-preparation-blocker-fixes)
5. [Phase 2 — Cloud-readiness code changes](#phase-2--cloud-readiness-code-changes)
6. [Phase 3 — Build the all-in-one image & test locally](#phase-3--build-the-all-in-one-image--test-locally)
7. [Phase 4 — Push to GHCR & provision RunPod](#phase-4--push-to-ghcr--provision-runpod)
8. [Phase 5 — First boot, platform bootstrap & smoke tests](#phase-5--first-boot-platform-bootstrap--smoke-tests)
9. [Phase 6 — Dashboard go-live (front-end + back-end)](#phase-6--dashboard-go-live-front-end--back-end)
10. [Phase 7 — Edge integration handoff](#phase-7--edge-integration-handoff)
11. [Phase 8 — Baseline end-to-end test (functional)](#phase-8--baseline-end-to-end-test-functional)
12. [Phase 9 — Performance foundation](#phase-9--performance-foundation)
13. [Phase 10 — Adaptive FPS governor, viewer priority & GPU capacity advisor](#phase-10--adaptive-fps-governor-viewer-priority--gpu-capacity-advisor)
14. [Phase 11 — 3 × 30 fps load test, tuning & acceptance](#phase-11--3--30-fps-load-test-tuning--acceptance)
15. [Phase 12 — Operations runbook](#phase-12--operations-runbook)
16. [Phase 13 — Security hardening](#phase-13--security-hardening)
17. [Appendices](#appendices) — port map, environment variables, file inventory, command cheat-sheet, risk register

---

## 1. Decisions & findings from the code review

### 1.1 Decisions taken (from Q&A on 26 Oct 2026)

| # | Topic | Decision |
|---|---|---|
| D1 | Frame rate | Edge sends **all 30 fps** (pipeline-bypass mode). Each enabled analyzer targets **15–20 fps per drone**. The analyzer whose annotated feed an officer is **watching gets priority**; analyzers nobody is watching are **throttled**. A **GPU capacity advisor** tells the officer which analytics combinations the L40S can sustain. |
| D2 | Public access | **RunPod HTTP proxy URL** `https://<POD_ID>-80.proxy.runpod.net` (TLS by RunPod/Cloudflare). A custom domain can be added later (Phase 12.8). |
| D3 | Image strategy | **One custom image** built from the RunPod PyTorch 2.8 template image, pushed to a **private GHCR** registry, used as a RunPod template. |
| D4 | Edge app | Built by the **edge teammate** (separate plan `DVAS_Edge_EXE_Build_Plan.md`): Inno Setup installer + embedded Python 3.11, login online through the cloud (Keycloak). This plan only provides the cloud side and the interface contract (Phase 7). |
| D5 | Branch | New branch **`runpod-v1`** cut from `testing` (git branch names can't contain spaces, so "runpod v1" becomes `runpod-v1`), worked on in the duplicate folder `/mnt/NewVolume/DVAS_RUNPODV1`. The edge `.exe` is owned by a teammate on a separate branch and plan. Cloud-specific behaviour sits behind environment flags so changes can flow back to `testing`. |

### 1.2 Platform constraints that drive the design

| Constraint | Consequence for DVAS |
|---|---|
| A RunPod pod **is itself a container**; you cannot run the Docker daemon or `docker compose` inside it. | The two current compose stacks (26 containers) become **~20 processes in one container**, managed by **supervisord**. Container names used as hostnames are kept working through `/etc/hosts` aliases to `127.0.0.1`. |
| HTTP proxy: `https://[POD_ID]-[PORT].proxy.runpod.net`, HTTPS only, path *User → Cloudflare → RunPod LB → pod*, **100-second limit** (Cloudflare returns 524). | Only **port 80 (nginx)** is exposed over HTTP. Long-lived SSE/WebSocket connections need heartbeats. Any request that takes longer than 100 s (big PDF reports, large uploads) must become async/chunked. Uploads over ~100 MB per request must be chunked. |
| Container disk (50 GB) is **cleared when the pod stops**. Network volume (30 GB) mounted at `/workspace` persists independently of the pod. | Everything stateful (Postgres, MinIO, TensorRT engines, secrets, backups) lives under `/workspace/dvas`. Everything else (code, venvs, models, Keycloak, frontend build) is **baked into the image**. Valkey frame streams stay on container disk because they are transient. |
| A network volume is tied to **one data centre** and must be attached when the pod is created. | Pick a DC with Secure-Cloud L40S availability **before** creating the volume (Phase 4). |
| Terminating the pod and creating a new one changes the **pod ID → public URL**. | Edge laptops read the server URL from a config file and a first-run prompt (edge plan). Notify the edge team when it changes (Phase 7.1). A custom domain removes this in Phase 12.8. |
| Pods have no UDP; TCP ports get a mapped external port (stable public IP on Secure Cloud). | SSH via exposed TCP port 22 for admin work; no RTSP/UDP ingest into the pod. |
| Host driver supports CUDA 13.2; template ships CUDA 12.8.1 + PyTorch 2.8.0. | Fine — the driver is backward-compatible. All Python GPU packages must be **CUDA 12 / cuDNN 9** builds (affects PaddlePaddle and onnxruntime). |

### 1.3 Findings in the current code (must be addressed)

| ID | Severity | Finding | Fixed in |
|---|---|---|---|
| F1 | ✅ Resolved 26 Oct | `dvas_analytics/crowd/main.py` was **0 bytes** after the last merge. The team restored it (21 KB, 539 lines) and it was reviewed: it compiles, all imports resolve, and every endpoint the backend proxies exists (Phase 1.2). Follow-up gaps are tracked as F19–F21. | Phase 1 (commit) |
| F2 | 🔴 Blocker | `dvas_analytics/db/migrations/` (what `init.sh` runs on a fresh DB) is **missing** `007_intrusion_schema`, `010_alert_status_fix`, `010_vessel_schema`, `011_anomaly_schema`; `002_analytics_extras.sql` differs between `db/` and `db/migrations/`. A fresh database would lack intrusion, vessel and anomaly tables. | Phase 1 |
| F3 | 🔴 Blocker | All model weights (`*.pt`, `*.pth`, `*.onnx`, ~1.4 GB) are **git-ignored**, so a clone of the repo has no models. | Phase 1 (model staging + manifest), Phase 3 (baked into image) |
| F4 | 🔴 Blocker | Dashboard backend uses the **Docker socket** (`ingest_proxy.py` container list/start/stop, `system_telemetry.py` GPU stats via `docker exec`). No Docker in the pod. | Phase 2 |
| F5 | 🟠 High | Ingest nginx rate limit is `30 r/s` keyed on `$binary_remote_addr`. Behind the RunPod proxy every client has the **proxy's IP**, so 3 drones × 30 fps = 90 r/s would share one 30 r/s bucket → mass `429`s. | Phase 3 (new nginx config) |
| F6 | 🟠 High | Valkey runs with `maxmemory 1536mb` + `allkeys-lru` + AOF. Every frame is stored **twice** (raw `frame` + base64 `jpeg_b64`, ≈2.3× the JPEG size). At 3 × 30 fps with `MAXLEN 1000` this exceeds 1.5 GB and LRU starts **evicting arbitrary keys** (mission state, vehicle tracks). AOF would also write ~50 MB/s to disk. | Phase 3 (config) + Phase 9 (payload) |
| F7 | 🟠 High | Ingest API runs `--workers 4`, but `_latest_metadata`, mission/drain state and stats are **in-process dicts** → inconsistent telemetry and mission status across workers. | Phase 2 (run 1 worker) |
| F8 | 🟠 High | Each analyzer consumes **every** frame sequentially with batch size 1 (`xreadgroup … count=1–4`), so a slow analyzer builds an ever-growing backlog (up to 1000 frames ≈ 33 s behind live). The anomaly service creates **one YOLO11x + YOLO11n per drone**; intrusion runs at **imgsz 1920**. | Phase 9 / Phase 10 |
| F9 | 🟠 High | Secrets are hard-coded and committed: Keycloak client secret and admin-service secret defaults in `backend/core/config.py`, compose files and `dvas_edge/config/settings.yaml`; operator passwords in `settings.yaml` and `setup_keycloak.py`; `JWT_SECRET`, `DVAS_SERVICE_KEY`, `EDGE_API_KEY` defaults; `minioadmin`. `dvas_edge/token.txt` and `config/wireguard/*.conf` sit in the source tree. | Phase 2 (env-only) + Phase 13 (rotation) |
| F10 | 🟡 Medium | `services/audit_sync.py` **pulls** audit logs from edge laptop IPs (`EDGE_SYNC_HOSTS`). The cloud cannot reach laptops behind NAT. With the `.exe` design the edge writes audit entries straight to the cloud API, so pulling isn't needed. | Phase 2 (`EDGE_SYNC_HOSTS=""`) |
| F11 | 🟡 Medium | The edge UI pages call **both** `/edge-api/...` (local edge server) and `/api/...` (dashboard backend) on the **same origin**. Today the dashboard nginx stitches these together; the `.exe` needs a local gateway that does the same. | Edge plan (E2) |
| F12 | 🟡 Medium | `edge_api_server.py` hard-codes `host="0.0.0.0", port=8765` and settings path `/data/edge_settings.json` (becomes `C:\data` on Windows). | Edge plan (E1) |
| F13 | 🟡 Medium | ANPR installs `paddlepaddle-gpu==2.6.2` (CUDA 11.x/12.0, cuDNN 8) — this clashes with PyTorch 2.8 (CUDA 12.8, cuDNN 9) in a shared environment. Vessel RF-DETR imports `rfdetr`, which isn't in `vessel/requirements.txt`. | Phase 3 (separate ANPR venv, CPU Paddle; add `rfdetr`) |
| F14 | 🟡 Medium | `MINIO_PUBLIC_URL=http://localhost:9000` for vehicle/ANPR → presigned URLs point at the officer's own machine. The dashboard already proxies images via `/api/minio/image`. | Phase 2 (`MINIO_PUBLIC_URL=""`) |
| F15 | 🟡 Medium | Backend decides `Secure` cookies from `request.url.scheme`; behind nginx/RunPod it sees `http`. | Phase 2 (`--proxy-headers`, `SECURE_COOKIES=true`) |
| F16 | 🟢 Low | The analytics code ran on Python 3.10 (pytorch 2.3 images); the template is Ubuntu 24.04 (Python 3.12 expected). Minor compatibility risk. | Phase 3 (verify) |
| F17 | 🟢 Low | Frontend has no build-time split between cloud and edge; a `drone_operator` who logs into the cloud dashboard would land on edge pages that can't work there. | Phase 2 (`REACT_APP_DEPLOYMENT_TARGET`) |
| F18 | 🟢 Low | Evidence clips are extracted on the edge SSD; the cloud dashboard cannot reach the laptop to play them. | Edge plan (E6) + a backend upload route here when scheduled |
| F19 | 🟠 High | **Crowd service has no frame-rate cap.** Its loop polls with `block_ms=0` and runs CSRNet (resized to 1280 px on GPU) on the newest frame of every drone as fast as it can. It also builds a heatmap PNG + base64 and an annotated JPEG on every frame. At 3 × 30 fps it will take a large share of GPU time that the plan reserves for watched analyzers — although crowd counts only need 1–2 fps. | Phase 2.5.1 (env cap), Phase 9/10 (governor) |
| F20 | 🟡 Medium | **Crowd service is not mission-aware.** It ignores the `mission_id` in frame metadata (vehicle/person/anomaly read it). So readings, heatmap keys and `crowd_threshold` events go out with `mission_id = "M-DEFAULT"` → edge evidence extraction and per-mission reports can't match crowd alerts. | Phase 2.5.1 |
| F21 | 🟡 Medium | **Crowd lacks the Phase-26 behaviours the other analyzers have:** no `X-Feed-Status` / `X-Stream-Status` headers and no recovery from the last Valkey frame on `/annotated/latest` (returns 404 after 15 s); no CUDA-OOM → CPU failover in the loop; frames are left un-ACKed in the pending list when an exception occurs; `analyze_frame()` returns `None` when the model isn't ready, which turns into a `TypeError` in the loop and a 500 on `/analyze/frame`; `/stream/stop` always offloads to CPU. | Phase 2.5.1 |

---

## 2. Target architecture & capacity planning

### 2.1 Single-pod architecture

```
                       Officers (browser)                     Edge laptop(s) (DVAS_Edge.exe)
                              │ HTTPS                                  │ HTTPS POST /ingest/frame (30 fps/drone)
                              ▼                                        ▼   + /realms/... token, /api/... via local gateway
             ┌──────────── Cloudflare → RunPod LB  (https://<POD_ID>-80.proxy.runpod.net) ────────────┐
             └─────────────────────────────────────────┬────────────────────────────────────────────────┘
                                                       ▼  :80 (only public HTTP port)   + TCP :22 (SSH, admin)
┌──────────────────────────── RunPod pod: 1× L40S 48 GB · 16 vCPU · 62 GB RAM ─────────────────────────────────┐
│ supervisord (PID 1 via tini)                                                                                  │
│                                                                                                               │
│  nginx :80 ── /            → static React build (cloud target)                                                │
│           ├── /api/*  (WS) → dvas-backend      127.0.0.1:8001  (FastAPI, 12 routers, audit, reports, minio proxy)│
│           ├── /ingest/*    → dvas-ingest       127.0.0.1:8000  (FastAPI, 1 worker, JWT, XADD)                 │
│           ├── /realms/*    → keycloak          127.0.0.1:8080  (realm kerala-police-drone; admin console NOT public)│
│           └── /edge-api/*  → 404 (edge API exists only on laptops)                                           │
│                                                                                                               │
│  ── analytics (venv /opt/venvs/ai, GPU) ─────────────────────────────────────────────────────────────────    │
│  crowd 8101 · person 8102 · vehicle 8103 · anpr 8104 (venv /opt/venvs/anpr) · intrusion 8105 ·              │
│  vessel 8106 · anomaly 8107 · analytics-gateway 8100 (SSE, summary, FPS governor — Phase 10)                 │
│                                                                                                               │
│  ── data ──────────────────────────────────────────────────────────────────────────────────────────────    │
│  postgres 16  127.0.0.1:5432  DBs: keycloak · dvas (analytics) · dvas_dashboard   → /workspace/dvas/pgdata  │
│  valkey 7     127.0.0.1:6379  frame streams, events, hashes (no persistence)    → container disk            │
│  minio        127.0.0.1:9000  9 buckets (+ILM expiry)                            → /workspace/dvas/minio     │
│  sshd :22 · dvas-bootstrap (one-shot) · dvas-backup (nightly)                                                 │
└───────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
   /workspace (30 GB network volume): pgdata · minio · engines (TensorRT) · secrets · backups · calibration
```

**Dropped from the cloud build:** headscale, step-ca (TLS comes from RunPod), SeaweedFS (MinIO already stores evidence), the legacy `gpu-consumer` stub, the `edge` container and the dashboard's own nginx. Prometheus/Grafana/Alertmanager are optional (Phase 12.6).

### 2.2 Compute budget — why the FPS governor is needed

These are **planning estimates for an L40S** (batch 1, 1080p frames letter-boxed to the model input size). Phase 10 replaces them with **measured** numbers from a calibration script.

| Service | Model(s) | Est. GPU ms/frame today (PyTorch FP32) | Est. after Phase 9 (FP16 / TensorRT) | Notes |
|---|---|---|---|---|
| person | YOLOv8m + YOLOv8n-face (tiled) + ResNet50 Re-ID + ArcFace | 18–30 | 6–10 | ArcFace currently on CPU |
| vehicle | VisDrone YOLOv8 + ByteTrack + colour/make classifiers | 10–15 | 3–5 | |
| anpr | VisDrone YOLOv8 + LP detector + PaddleOCR | 15–25 GPU + OCR on CPU | 5–8 GPU | OCR only on plate crops |
| intrusion | GeoTrax YOLOv8s @ **1920** | 20–30 | 6–10 (3–5 at 1280) | |
| vessel | YOLOv8s @ 1280 (or RF-DETR-M) | 8–12 (20–35) | 3–4 (10–15) | Use YOLOv8 for the test |
| anomaly | VisDrone YOLO11x + YOLO11n (per drone today) | 25–40 | 8–12 | Share weights across drones |
| crowd | CSRNet (frame resized to 1280 px) + heatmap/annotation | 30–60 | 10–20 | Only needs 1–2 fps, but **currently unthrottled (F19)** — cap it before any load test |

**GPU-time arithmetic.** The seven analyzers share one GPU by time-slicing, so the useful budget is ≈ **850 ms of GPU time per second** (85 % target). One service on 3 drones at 15 fps = 45 frames/s:

- person 45 × 8 ms ≈ 360 ms/s (36 %)
- intrusion 45 × 8 ms ≈ 360 ms/s (36 %)
- anomaly 45 × 10 ms ≈ 450 ms/s (45 %)
- vehicle 45 × 4 ms ≈ 180 ms/s (18 %)

So even after optimisation, **only 2–3 heavy analyzers can run at 15 fps on all three drones at once**. Running all seven at 15–20 fps would need roughly 3–4× the GPU. That's why D1 calls for:

1. **viewer priority** — the analyzer an officer is watching gets 15–20 fps; others drop to a configurable background rate (e.g. 3–5 fps)
2. a **capacity advisor** that predicts load before a mission and warns when the selected combination won't fit

Both are built in Phase 10.

### 2.3 VRAM, CPU, RAM, disk

| Resource | Budget | Estimate for the test | Comment |
|---|---|---|---|
| **VRAM 48 GB** | keep < 40 GB | 7 CUDA contexts (~0.5–1 GB each) + weights/engines (~4 GB) + activations ≈ **15–25 GB** (anomaly per-drone duplication adds ~2 GB/drone until Phase 9.6) | Comfortable; the Phase-26 "offload to CPU on stop" logic is not needed and doesn't work with TensorRT engines. |
| **vCPU 16** | keep < 75 % | JPEG decode ~4–6 ms per 1080p frame per core, only for *sampled* frames; ingest ~1 core; Postgres 1–2; Keycloak <1; OCR/ArcFace on CPU are the wildcards | Base64 removal (Phase 9.1) and annotate-on-demand (9.5) save a lot of CPU. |
| **RAM 62 GB** | keep < 48 GB | Analyzers 7 × 2–4 GB; Postgres 6 GB; Keycloak 1.5 GB; Valkey ≤ 2 GB; MinIO 1 GB; web services 1.5 GB → **~30–40 GB** | |
| **Container disk 50 GB** | image + runtime | Image target ≤ 25 GB uncompressed; logs (rotated) 2 GB; Valkey ≤ 2 GB; tmp | Wiped on stop — nothing important lives here. |
| **Network volume 30 GB** | keep < 80 % (24 GB) | pgdata 3–6 GB · MinIO ≤ 14 GB (with ILM) · TensorRT engines ~1.5 GB · backups ~2 GB · calibration/logs < 1 GB | Retention rules in Phase 5.6 are **mandatory** at this size. |

### 2.4 Network — the field uplink is the real constraint

| Edge setting (per drone, 30 fps JPEG) | Typical frame size | Uplink per drone | 3 drones |
|---|---|---|---|
| 1080p, quality 80 | 150–250 KB | 36–60 Mbps | 110–180 Mbps |
| 1080p, quality 65 | 90–140 KB | 22–34 Mbps | 65–100 Mbps |
| **720p, quality 75** (recommended for the test) | 60–90 KB | **14–22 Mbps** | **45–65 Mbps** |
| H.264 stream (future, not in this plan) | — | 4–8 Mbps | 12–24 Mbps |

- **Round-trip time.** India → the nearest RunPod Secure-Cloud DC is likely 120–200 ms. Sending 30 requests/s per drone needs ≥ 6 requests in flight (30 × 0.2 s); the edge sender already uses a semaphore of 10. Concurrent requests can **arrive out of order**, so Phase 9.2 adds a frame sequence number and analyzers drop frames older than the last one they processed.
- **Recommendation.** Run the first tests at **720p q75**, measure, then try 1080p if the uplink allows. Moving live transport to H.264 with NVDEC decode in the pod is the long-term fix for bandwidth and is noted in the risk register.
- **Data residency.** RunPod has no Indian data centre. Confirm with the client that police video and biometric data may be processed abroad for this **test**; production may need an Indian host.

---

# BLOCK A — PREPARE

## Phase 0 — Prerequisites & accounts

**Goal.** Get every account, tool and decision in place so the later phases don't stall.
**Where.** 💻 + RunPod console + GitHub.
**Prerequisites.** None.
**Indicative effort.** 0.5 day.

### Steps

1. **RunPod account**
   - Enable **2FA**.
   - Add credit (L40S Secure Cloud on-demand + $0.07/GB-month network volume → 30 GB ≈ $2.10/month; check the live GPU price in the console).
   - Add your **SSH public key** in *Settings → SSH Public Keys*. It's injected as `PUBLIC_KEY`.
2. **Pick the data centre.** In *Pods → Deploy → Secure Cloud*, filter on **L40S** and note which data centres show availability. Prefer the region with the lowest latency from Kerala. Measure it from the field site later with `curl -w "%{time_connect}\n"` against a test pod's proxy URL. You'll create the network volume in this DC in Phase 4.
3. **GitHub Container Registry (GHCR)**
   - Create a fine-grained or classic **PAT** with `write:packages` (build machine) and another with `read:packages` (RunPod).
   - Images go to `ghcr.io/quantumweaveintelligence/dvas-runpod`. Keep the package **private**.
4. **Build machine (💻 the existing Ubuntu GPU server is ideal)**
   - Docker ≥ 24 with **buildx**
   - **NVIDIA Container Toolkit** (already used for the GPU compose stack)
   - ≥ 80 GB free disk
   - git, Node 20 (optional — the image builds the frontend itself)
   - `docker login ghcr.io -u <github-user>` using the write PAT
5. **Windows build machine for the edge installer (🪟)**
   - Windows 10/11 x64
   - [Inno Setup 6](https://jrsoftware.org/isinfo.php)
   - PowerShell 5+
   - Internet access (to download the Python 3.11 embeddable zip and pip packages)
   - `ffmpeg.exe` static build
6. **Record the current working versions** from the running local stack (used to pin versions in Phase 3):
   ```bash
   docker exec drone_anomaly_analyzer pip freeze > /tmp/freeze_anomaly.txt
   docker exec drone_person_analyzer  pip freeze > /tmp/freeze_person.txt
   docker exec drone_anpr_analyzer    pip freeze > /tmp/freeze_anpr.txt
   docker exec dvas_backend           pip freeze > /tmp/freeze_backend.txt
   docker exec drone_minio minio --version
   docker exec drone_keycloak /opt/keycloak/bin/kc.sh --version
   ```
7. **Data-residency confirmation** (Section 2.4) from the project owner, in writing.

### ✅ Done when
- [ ] RunPod account has 2FA, credit and an SSH key; target DC chosen
- [ ] GHCR PATs created; `docker login ghcr.io` works on the build machine
- [ ] Build machine has buildx + NVIDIA toolkit + 80 GB free
- [ ] Windows build machine has Inno Setup
- [ ] `pip freeze` / version files collected
- [ ] Data-residency OK received

---

## Phase 1 — Branch & repository preparation (blocker fixes)

**Goal.** Create the `runpod-v1` branch, fix blockers F1–F3, and set up the deployment folder structure.
**Where.** 💻 in `/mnt/NewVolume/DVAS_RUNPODV1` (the duplicate working copy).
**Prerequisites.** Phase 0.
**Indicative effort.** 1 day.

### Steps

**1.1 Create the `runpod-v1` branch in the duplicate working copy**

Follow **`DVAS_RunPodV1_Branch_and_VSCode_Setup.md`** end-to-end. Summary:
1. In the original `/mnt/NewVolume/POLICE_DRONE`: commit/push anything still pending on `testing` (including the restored `crowd/main.py`).
2. Turn `/mnt/NewVolume/DVAS_RUNPODV1` into a real git working copy — the copied folders have **no `.git` and no root `.gitignore`**.
3. `git switch -c runpod-v1 origin/testing`, tag `pre-runpod-2026-10-26`, push with upstream.
4. Open the folder in VS Code and confirm the status bar shows `runpod-v1`.

Branch rules:
- `testing` keeps receiving feature work (including the edge teammate's).
- Bring it into your branch with `git merge origin/testing` when needed.
- Never merge `runpod-v1 → testing` wholesale. Send cloud-safe fixes (all behind env flags) back as small PRs.
- Protect `runpod-v1` on GitHub against force-push and deletion.

**1.2 `crowd/main.py` — restored ✅ (F1). Commit it.**

The file was restored on 26 Oct (21,004 bytes, 539 lines) and reviewed against the rest of the codebase:

| Check | Result |
|---|---|
| Syntax | `py_compile` passes |
| Imports | `shared.db` (`close_pool`, `ensure_drone`, `get_pool`, `write_confirmed_event`, `write_crowd_reading`, `get_crowd_history`), `shared.redis_client`, `shared.minio_client` (`ensure_all_buckets`, `make_key`, `upload_bytes`, `get_presigned_url`), `crowd.analyzer` and all 6 `crowd.schema` classes exist with matching signatures |
| Analyzer API | `ready`, `device`, `_model_path`, `_session_counts`, `analyze_frame()`, `get_last_annotated_frame()` (5-tuple), `switch_model()`, `to_device()`, `reset_session()`, `get_session_summary()` and `get_density_label()` all match `crowd/analyzer.py` |
| Response model | `analyze_frame()` keys match `CrowdAnalysisResult` (`density_colour` list of ints ✓) |
| Backend contract | All 12 crowd routes proxied by `backend/routers/analytics.py` exist with the same verbs and params: `/health`, `/readings/history`, `/density/timeline`, `/density/heatmap/latest`, `/annotated/latest` (+ `X-Crowd-Count`, `X-Density-Label`, `X-Drone-ID`, `X-Frame-Age`), `PUT`/`POST /model/switch`, `/model/current`, `/session/summary`, `/export/csv`, `/export/pdf`, `/stream/start`, `/stream/stop` |
| Gateway / infra | `/health` returns `stream_running` and `frames_processed` (used by the Models page and the gateway) |
| Stream handling | Uses consumer group `crowd-group`, dynamic drone discovery, latest-frame-wins (older frames ACKed without processing), inference off the event loop (`asyncio.to_thread`) |
| Model files | `PartAmodel_best.pth` / `partBmodel_best.pth` present under `crowd/models/` |

**Verdict:** correct and compatible — the crowd service will start and the dashboard's crowd page will work. The gaps found (F19 no fps cap, F20 no mission awareness, F21 missing Phase-26 robustness) don't block deployment; they're fixed in Phase 2.5.1.

Commit the file now, so the restored version is what the image build picks up:
```bash
git add dvas_analytics/crowd/main.py
git commit -m "crowd: restore main.py (emptied by merge)"
git show --stat HEAD | grep crowd/main.py      # must show ~539 lines added
```

To stop a merge from silently emptying a file again, add a CI or pre-commit check that fails on zero-byte `*.py` files except `__init__.py`:
```bash
find . -name "*.py" ! -name "__init__.py" -size 0 -not -path "*/node_modules/*"
```

**1.3 Consolidate database schema (F2)** — use the **live local databases as the source of truth**. That guarantees the schema matches what the code runs against today.
```bash
mkdir -p deploy/runpod/db
# Analytics DB (drone_postgres, database "dvas")
docker exec drone_postgres pg_dump -U keycloak -d dvas --schema-only --no-owner --no-privileges \
  > deploy/runpod/db/analytics_000_baseline_schema.sql
# Reference/seed rows needed at runtime (presets etc.) — list the tables you need, e.g.:
docker exec drone_postgres pg_dump -U keycloak -d dvas --data-only --no-owner \
  -t analytics_presets > deploy/runpod/db/analytics_001_reference_data.sql
# Dashboard DB (dvas_postgres, database "dvas")
docker exec dvas_postgres pg_dump -U dvas -d dvas --schema-only --no-owner --no-privileges \
  > deploy/runpod/db/dashboard_000_baseline_schema.sql
```
- Check the real preset/config table names with `\dt` first. Include only reference data, never detections, faces or audit rows.
- Review both dumps for partitioned tables (`intrusion_events`, `anomaly_events`). Make sure partitions for the **next 3 months** exist, or add a monthly partition job (Phase 12.5).
- Also tidy the legacy folder so `db/migrations/` == `db/` (copy 007/010/011, resolve the two `004_*` and two `010_*` files). The local compose path keeps working.

**1.4 Model weights staging + manifest (F3)** — weights stay out of git but are versioned by checksum.
```bash
mkdir -p ../dvas_models_stage && cd /mnt/NewVolume/DVAS_RUNPODV1
rsync -a --prune-empty-dirs --include='*/' \
  --include='*.pt' --include='*.pth' --include='*.onnx' --include='*.pb' --exclude='*' \
  dvas_analytics/ ../dvas_models_stage/analytics/
( cd ../dvas_models_stage && find . -type f -exec sha256sum {} \; | sort -k2 ) > deploy/runpod/models.sha256
git add deploy/runpod/models.sha256
```
Expected contents (~1.4 GB):
- anpr: `visdrone.pt`, `license_plate_detector.pt`, `yolov8m.pt`, `checkpoint_best_total.pth`
- crowd: `PartAmodel_best.pth`, `partBmodel_best.pth`
- intrusion: `geotrax_hbb_yolov8s_1920_v1.pt`
- person: `yolov8m.pt`, `yolov8n-face.pt`, `arcface_r100.onnx`
- vehicle: `visdrone.pt`, `checkpoint_best_total.pth`, `VehicleDetectionTracker/data/*.pb`
- vessel: `yolov8s.pt`, `checkpoint_best_total.pth`
- anomaly: `models/weights/visdrone-yolov11x.pt`, `yolo11n.pt`

Also store a copy of `../dvas_models_stage` somewhere safe (internal NAS or encrypted cloud bucket) — it's needed for every image build.

**1.5 Keycloak realm export** (preserves clients, roles, mappers and the role mapping the backend expects)
```bash
docker exec drone_keycloak /opt/keycloak/bin/kc.sh export --dir /tmp/kc --realm kerala-police-drone --users realm_file
docker cp drone_keycloak:/tmp/kc/kerala-police-drone-realm.json deploy/runpod/keycloak/realm-export.json
```
- **Do not commit** the export as-is: it contains client secrets and password hashes.
- Run the sanitiser (Phase 2.8) that blanks secrets and users, then commit `realm-template.json`.
- Keep the raw export outside git.

**1.6 Create the deployment skeleton**
```
deploy/runpod/
├── Dockerfile                    # Phase 3
├── Dockerfile.dockerignore       # BuildKit per-Dockerfile ignore file
├── constraints.txt               # pins torch/torchvision to the base image versions
├── requirements/{ai,anpr,backend,ingest,gateway}.txt
├── etc/{supervisord.conf,nginx.conf,valkey.conf,postgresql.conf,pg_hba.conf}
├── bin/{dvas-init.sh,dvas-bootstrap.sh,wait-for.sh,gen-secrets.sh,dvas-ctl,backup-loop.sh,healthcheck.sh}
├── tools/{build_trt_engines.py,calibrate.py,loadtest_ingest.py,sanitize_realm.py}
├── db/                           # baseline schema dumps (1.3)
├── keycloak/realm-template.json  # sanitised realm (1.5 / 2.8)
├── env/dvas.env.example          # every variable, no real values
├── models.sha256
└── README.md                     # short pointer to this plan
                                  # (dvas_edge/packaging/ belongs to the edge teammate's branch)
```

**1.7 Remove sensitive files from the tree** (they stay in history until Phase 13 rotation)
```bash
git rm --cached dvas_edge/token.txt dvas_edge/config/wireguard/wg0-client.conf \
  dvas_ingest/config/wireguard/wg0-server.conf 2>/dev/null
git rm --cached DVAS_Dashboard/cookies.txt
echo -e "token.txt\n**/wireguard/*.conf\ncookies.txt" >> .gitignore
```
All four files are tracked on `testing` today (checked from the git index). The two `dvas_edge/` files belong to the edge teammate's area. Tell them, so their `edge-exe-v1` branch removes them too (edge plan E1), or send this change to `testing` as a small PR so both branches pick it up.

### Files
`deploy/runpod/**` (skeleton, db dumps, `models.sha256`), `dvas_analytics/crowd/main.py`, `dvas_analytics/db/migrations/*`, `.gitignore`.

### ✅ Done when
- [ ] `runpod-v1` exists on origin; tag `pre-runpod-2026-10-26` pushed
- [x] `crowd/main.py` restored and reviewed (26 Oct)
- [ ] Restored `crowd/main.py` committed to `runpod-v1` (and to `testing`); zero-byte `.py` check added
- [ ] Baseline schema dumps for both DBs committed; reference data identified
- [ ] Model staging folder + `models.sha256` created, with a backup copy kept
- [ ] Sanitised realm template committed (raw export kept outside git)
- [ ] Token/WireGuard/cookie files untracked

**↩ Rollback.** `git switch testing && git branch -D runpod-v1` (the tag preserves the starting point).

---

## Phase 2 — Cloud-readiness code changes

**Goal.** Make every service run as a plain process configured only by environment variables, with no Docker dependency, no hard-coded secrets, and a cloud build of the frontend.
**Where.** 💻 on `runpod-v1` in `/mnt/NewVolume/DVAS_RUNPODV1`.
**Prerequisites.** Phase 1.
**Indicative effort.** 3–4 days.

> Rule for this phase: **every change is backward-compatible.** Defaults keep the local docker-compose stack working, and cloud behaviour switches on via env vars (`DVAS_RUNTIME=supervisor`, `DVAS_DEPLOYMENT=cloud`).

### Steps

**2.1 Hostnames → env + `/etc/hosts` aliases (no code change needed).** The code is full of compose hostnames (`postgres`, `valkey`, `minio`, `keycloak`, `crowd-analyzer`, `drone_nginx`, …). In the pod, `dvas-init.sh` (Phase 3) adds aliases for all of them to `127.0.0.1` in `/etc/hosts`, and every service gets explicit URLs in its environment. Ports stay the same as in compose, except that the **backend moves from 8000 to 8001** (8000 belongs to ingest). Then:
- Override the dashboard nginx default `http://backend:8000` with the new nginx config.
- Set `INGEST_SERVER_URL=http://127.0.0.1:8000` explicitly in the backend env. Today it defaults to `http://drone_nginx`, which still works through the alias (nginx proxies `/ingest/*`), but going direct is faster.

**2.2 Ingest API (`dvas_ingest/ingest/api`)**
- Run with **1 uvicorn worker** (F7). The state is in-process, and one async worker easily handles 90 req/s of ~100 KB uploads.
- Add `--proxy-headers --forwarded-allow-ips=127.0.0.1`.
- Make CORS origins env-driven: `ALLOWED_ORIGINS` (comma list). Default to the current list.
- Add optional `frame_seq: int | None` and `capture_ts: float | None` to `FrameMetadata` in `models.py`, and pass them through into the stream entry (used in Phase 9.2).
- Confirm `SKIP_AUTH` isn't read anywhere (it's set in compose but unused). Remove it from compose to avoid confusion.

**2.3 Dashboard backend — replace the Docker socket (F4)**
- New module `backend/core/runtime.py`:
  ```python
  RUNTIME = os.getenv("DVAS_RUNTIME", "docker")   # "docker" | "supervisor"
  CONTAINER_TO_PROGRAM = {   # compose container name -> supervisord program
      "drone_postgres": "postgres", "dvas_postgres": "postgres", "drone_valkey": "valkey",
      "drone_minio": "minio", "drone_keycloak": "keycloak", "drone_ingest_api": "ingest",
      "drone_nginx": "nginx", "dvas_backend": "backend", "dvas_frontend": "nginx",
      "drone_analytics_gateway": "analytics:gateway",
      "drone_crowd_analyzer": "analytics:crowd", "drone_person_analyzer": "analytics:person",
      "drone_vehicle_analyzer": "analytics:vehicle", "drone_anpr_analyzer": "analytics:anpr",
      "drone_intrusion_analyzer": "analytics:intrusion", "drone_vessel_analyzer": "analytics:vessel",
      "drone_anomaly_analyzer": "analytics:anomaly",
  }
  def supervisor():
      from xmlrpc.client import ServerProxy
      from supervisor.xmlrpc import SupervisorTransport   # pip install supervisor
      return ServerProxy("http://localhost", transport=SupervisorTransport(None, None, "unix:///run/supervisor.sock"))
  # list -> supervisor().supervisor.getAllProcessInfo(); control -> startProcess/stopProcess(name)
  ```
- In `ingest_proxy.py` (`/containers`, `/control`), branch on `RUNTIME`:
  - `supervisor` → status from `getAllProcessInfo()`, mapped back to the container names the UI expects
  - Hide containers that don't exist in the cloud (headscale, step-ca, seaweedfs, edge, grafana…) or report them as `not_deployed`
  - **Never allow stopping `backend`, `nginx`, `postgres` or `keycloak` from the UI** in supervisor mode — you'd cut off your own control plane
- In `services/system_telemetry.py`, read GPU stats through **pynvml** (`nvidia-ml-py`) directly instead of `docker exec nvidia-smi`. CPU/RAM via `/proc` already works.
- Add `supervisor` and `nvidia-ml-py` to backend requirements.

**2.4 Backend configuration hygiene (F9, F10, F14, F15)**
- `core/config.py`: **remove the hard-coded defaults** for `KC_CLIENT_SECRET` and `ADMIN_CLIENT_SECRET`. Fail at startup if they're empty and `DVAS_DEPLOYMENT=cloud`.
- Read `CORS_ORIGINS` from env. In cloud mode, **drop** `allow_origin_regex=r"^https?://.*"` in `server.py` and allow only `PUBLIC_BASE_URL` plus `http://localhost:8765` (edge gateway).
- Start uvicorn with `--proxy-headers --forwarded-allow-ips=127.0.0.1` and set `SECURE_COOKIES=true`.
- Set `EDGE_SYNC_HOSTS=""` → make `start_audit_sync_service()` a no-op when the list is empty (check the code path; add `if not hosts: return`).
- Set `DVAS_PEER_URL=""` (peer push isn't used in the cloud). `/api/push/receive` stays available for the edge.
- `EDGE_API_HOST` unset → `/api/edge/system-logs` returns `[]` quickly. Lower its timeout to 1 s.
- Long-running endpoints: the PDF report generation and the Section-65B export must finish in < 90 s or be turned into "start job → poll status → download" (Cloudflare 100 s cap). Measure them in Phase 6.

**2.5 Analytics services**
- Set `MINIO_PUBLIC_URL=""` (F14). The browser always goes through `/api/minio/image`.
- Add `rfdetr` to `vessel/requirements.txt` (F13). The test uses `VESSEL_MODEL_TYPE=yolov8`; RF-DETR stays optional.
- Remove the hard-coded Windows fallback path in `vessel/seadrone/rfdetr_engine.py` (`D:\POLICE_DRONE\...`).
- Add env `VRAM_OFFLOAD_ON_STOP` (default `true`). When `false`, `/stream/stop` keeps models on the GPU — required later for TensorRT engines and avoids reload stalls.
- Gateway: emit an SSE **heartbeat comment** (`: ping\n\n`) every 15 s on `/events/stream` so Cloudflare doesn't drop idle streams. Do the same for any SSE endpoint the backend proxies. Crowd's `/count/live` already sends `: keepalive` every 5 s.

**2.5.1 Crowd service follow-ups (F19–F21)** — small, self-contained changes to `dvas_analytics/crowd/main.py`, in priority order:

1. **Frame-rate cap (F19, do before any load test).**
   - Add `CROWD_MAX_FPS` (per drone, default `2`).
   - Keep `last_processed_ts[drone_id]`. If `time.monotonic() - last < 1/CROWD_MAX_FPS`, ACK the frames just read without decoding them and move on.
   - Change `block_ms=0` to `block_ms=50` so the loop doesn't spin.
   - In Phase 10 the governor's `dvas:fps_target:crowd:<drone>` replaces the env value.
2. **Heatmap only when needed (F19).**
   - Call `analyzer.analyze_frame(..., include_heatmap=(_frames_processed % 10 == 0))`. The heatmap PNG/base64 is only uploaded every 10th frame anyway.
   - The annotated JPEG becomes on-demand in Phase 9.5.
3. **Mission awareness (F20).**
   - Parse `f.get("metadata")` → `mission_id`, exactly like `vehicle/main.py` (lines ~331–339).
   - Pass it to `minio.make_key(..., mission_id=mission_id)`.
   - Add it to the `write_confirmed_event(... attributes={..., "mission_id": mission_id})` attributes and to the `publish_confirmed_event({... "mission_id": mission_id})` payload, so edge evidence extraction can match the event.
   - Optional: `crowd_density_readings` has a `zone_id` column; if per-mission filtering of readings is needed, add a `mission_id` column via a new numbered migration (Phase 12.2), rather than reusing `zone_id`.
4. **Phase-26 parity (F21).**
   - `/annotated/latest`: when the cache is empty or older than 15 s, fall back to `get_latest_stream_frame(drone_id)` and run one analysis. Return `X-Feed-Status`/`X-Stream-Status: live|offline` (and add them to `Access-Control-Expose-Headers`) — copy the pattern from `vehicle/main.py`.
   - Add the 2 headers to the backend's crowd annotated proxy header pass-through list (`routers/analytics.py`, `crowd_annotated_frame`).
   - In the loop, catch `torch.cuda.OutOfMemoryError` / CUDA `RuntimeError` → `analyzer.to_device("cpu")`, log a warning, continue.
   - Move `ack_frames(...)` into a `finally:` so failed frames don't pile up in the pending list.
   - Guard `result is None` (model not ready): ACK and `continue` in the loop; return `503` from `/analyze/frame`.
   - Honour `VRAM_OFFLOAD_ON_STOP` in `/stream/stop`.
5. **Minor**
   - `/export/csv` uses a fixed 25 fps for timestamps → use real timestamps (store `(ts, count)` pairs in the session).
   - `/density/timeline` ignores the `hours` parameter the backend sends → filter by `ts >= now() - hours`.
   - `/density/heatmap/latest` returns a presigned MinIO URL that browsers can't reach in the cloud (MinIO is internal). Also return `heatmap_object_key`, so the UI can load it through `/api/minio/image` (bucket `dvas-heatmaps`).

Verify after the change: `curl 127.0.0.1:8101/health` shows `frames_processed` rising by ≈ `CROWD_MAX_FPS × drones` per second, and GPU time for the crowd PID (`nvidia-smi pmon`) stays small.

**2.6 Edge-side changes** — moved to the edge teammate's plan (`DVAS_Edge_EXE_Build_Plan.md`, Phase E1). Not part of `runpod-v1`.

**2.7 Frontend: cloud vs edge build target (F17)** — *shared with the edge team: land this as one small PR on `testing` first, then merge `testing` into `runpod-v1` (see Phase 7.2).*
- `src/lib/deployment.js`:
  ```js
  export const TARGET = process.env.REACT_APP_DEPLOYMENT_TARGET || "all"; // "cloud" | "edge" | "all"
  export const isCloud = TARGET === "cloud", isEdge = TARGET === "edge";
  ```
- `App.js`:
  - Register the `/edge/*` route only when `!isCloud`.
  - Register `/command`, `/ingest`, `/admin` and `/audit` only when `!isEdge`.
- `Login.js`:
  - If `isCloud` and the user's role is `drone_operator`, show: "Drone operators use the DVAS Edge application" and log out.
  - If `isEdge` and the role is not `drone_operator`, show the reverse.
- `AuthContext.logout()`: skip the `/edge-api/...` mission cleanup calls when `isCloud`.
- `useJpegPoller`: pause polling when `document.hidden` (saves bandwidth; also needed for viewer priority in Phase 10).
- Build commands (cloud: used by the Dockerfile; edge: used by the edge teammate's build script):
  - `REACT_APP_DEPLOYMENT_TARGET=cloud REACT_APP_BACKEND_URL= GENERATE_SOURCEMAP=false npm run build`
  - `REACT_APP_DEPLOYMENT_TARGET=edge  REACT_APP_BACKEND_URL= GENERATE_SOURCEMAP=false npm run build`

**2.8 Keycloak setup made non-interactive and secret-free**
- `tools/sanitize_realm.py`: from the raw export, strip `users`, set every client `secret` to `"**********"`, keep roles/clients/mappers/flows, and write `realm-template.json`.
- Extend `dvas_ingest/ingest/scripts/setup_keycloak.py` with:
  - `--client-secret`, `--admin-client-secret` (sets the secret explicitly via `PUT /admin/realms/{realm}/clients/{id}` with `"secret": ...`)
  - `--users-file users.json` (user list and passwords come from a file generated at bootstrap from env, never from the source code)
  - `--frontend-url <PUBLIC_BASE_URL>` (sets realm attribute `frontendUrl`)
  - Idempotent: re-running updates instead of failing

**2.9 Requirements for Python 3.12.** Create `deploy/runpod/requirements/*.txt` from the Phase-0 `pip freeze` outputs, pinning the versions that work today **except** torch/torchvision (taken from the base image via `constraints.txt`) and Paddle (CPU build, see Phase 3.3).

| File | Used by | Key pins |
|---|---|---|
| `ai.txt` | crowd, person, vehicle, intrusion, vessel, anomaly | ultralytics (same version as today, ≥ 8.3 for YOLO11), opencv-python-headless, fastapi/uvicorn, asyncpg, redis[hiredis], miniopy-async, httpx, scikit-learn, scipy, lap/lapx, onnxruntime-gpu (CUDA 12 / cuDNN 9 build), av, pyyaml, huggingface_hub, rfdetr, matplotlib, fpdf2, cryptography, Pillow, nvidia-ml-py |
| `anpr.txt` | anpr | ultralytics, `paddlepaddle==2.6.2` (CPU), `paddleocr==2.9.1`, `numpy<2`, pandas + the common web libs |
| `backend.txt` | backend | current `backend/requirements.txt` minus dev tools (pytest, black, isort, flake8, mypy), plus `supervisor`, `nvidia-ml-py` |
| `ingest.txt` | ingest | current `ingest/api/requirements.txt` |
| `gateway.txt` | gateway | current `gateway/requirements.txt` |

Ingest pins `fastapi 0.111/starlette 0.37.2/pydantic 2.7.0` while the backend pins `fastapi 0.110.1` and the gateway `pydantic 2.7.1` — so they get **separate small venvs** rather than one shared one.

**2.10 Unit smoke on the dev machine.** Bring the local compose stack up from the `runpod-v1` branch (stop the original POLICE_DRONE stack first — same container names, see the branch setup guide) with no new env vars. Everything must behave exactly as before; this proves the defaults are backward-compatible.

### Files
`backend/core/runtime.py` (new), `backend/core/config.py`, `backend/server.py`, `backend/ingest_proxy.py`, `backend/services/system_telemetry.py`, `backend/services/audit_sync.py`, `backend/requirements.txt`, `ingest/api/{main.py,models.py,config.py}`, analytics `*/main.py` (offload flag), `gateway/main.py`, `vessel/requirements.txt`, `vessel/seadrone/rfdetr_engine.py`, frontend `src/lib/deployment.js` (new), `App.js`, `Login.js`, `AuthContext.js`, `useJpegPoller.js`, `setup_keycloak.py`, `deploy/runpod/requirements/*`, `deploy/runpod/tools/sanitize_realm.py`.

### ✅ Done when
- [ ] `grep -rn "yNa0AfA0\|MvusAlJk\|super-secret-dvas\|Operator@00\|admin123" --include=*.py --include=*.yml --include=*.yaml .` returns nothing outside history
- [ ] Backend container controls and GPU telemetry work with `DVAS_RUNTIME=supervisor` (test by running supervisord on the dev box, or with a unit test against a mock)
- [ ] Cloud build hides edge routes; edge build hides command routes
- [ ] Crowd follow-ups 2.5.1 items 1–4 done: fps cap honoured, `mission_id` on crowd events, `X-Feed-Status` returned, no un-ACKed frames after induced errors
- [ ] Local compose stack on `runpod-v1` works unchanged (regression check)
- [ ] Changes committed and pushed to `runpod-v1`

**↩ Rollback.** Revert the PR; the env-flag design means nothing changes until the flags are set.

---

# BLOCK B — BUILD & DEPLOY

## Phase 3 — Build the all-in-one image & test locally

**Goal.** One image containing every cloud service, verified on the local GPU server with a fake `/workspace` before spending any RunPod time.
**Where.** 💻 build machine.
**Prerequisites.** Phases 1–2.
**Indicative effort.** 3–4 days (most of it getting dependency versions right).

### 3.1 Find the base image

In the RunPod console open *Templates → RunPod PyTorch 2.8.0 (CUDA 12.8.1, Ubuntu 24.04, 1.4.0-2.8.0)* and copy its **Container Image** value (format `runpod/pytorch:<tag>`). Then inspect it:

```bash
BASE=runpod/pytorch:<TAG_FROM_TEMPLATE>
docker pull $BASE
docker run --rm --gpus all $BASE bash -lc 'cat /etc/os-release | head -2; which python3; python3 --version;
  python3 -c "import torch,torchvision,sys;print(sys.executable,torch.__version__,torch.version.cuda,torchvision.__version__,torch.cuda.is_available())";
  df -h / | tail -1; ls /start.sh 2>/dev/null'
docker image inspect $BASE --format '{{.Size}}'
```

Record the interpreter that has torch. The Dockerfile assumes `/usr/bin/python3`; change `ARG PY` if it differs.

### 3.2 `deploy/runpod/Dockerfile`

```dockerfile
# syntax=docker/dockerfile:1.7
ARG BASE_IMAGE=runpod/pytorch:<TAG_FROM_TEMPLATE>
ARG KEYCLOAK_IMAGE=quay.io/keycloak/keycloak:24.0.5
ARG VALKEY_IMAGE=valkey/valkey:7.2
ARG MINIO_IMAGE=minio/minio:<TAG_YOU_RUN_LOCALLY>     # pin — see Phase 0 step 6
ARG MC_IMAGE=minio/mc:<MATCHING_TAG>

# ── Stage 1: cloud frontend build ─────────────────────────────────────────────
FROM node:20-bookworm AS frontend
WORKDIR /src
COPY DVAS_Dashboard/frontend/package.json DVAS_Dashboard/frontend/package-lock.json ./
RUN npm ci --legacy-peer-deps
COPY DVAS_Dashboard/frontend/ ./
ENV REACT_APP_DEPLOYMENT_TARGET=cloud REACT_APP_BACKEND_URL= GENERATE_SOURCEMAP=false CI=false
RUN npm run build

# ── Stage 2: binaries from official images ────────────────────────────────────
FROM ${KEYCLOAK_IMAGE} AS keycloak
FROM ${VALKEY_IMAGE}   AS valkey
FROM ${MINIO_IMAGE}    AS minio
FROM ${MC_IMAGE}       AS mc

# ── Stage 3: runtime ──────────────────────────────────────────────────────────
FROM ${BASE_IMAGE}
ARG PY=/usr/bin/python3
ENV DEBIAN_FRONTEND=noninteractive TZ=Asia/Kolkata PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    LANG=C.UTF-8 LC_ALL=C.UTF-8

# Don't let the postgres package create a default cluster
RUN mkdir -p /etc/postgresql-common && echo "create_main_cluster = false" > /etc/postgresql-common/createcluster.conf
RUN apt-get update && apt-get install -y --no-install-recommends \
      nginx supervisor tini openssh-server curl ca-certificates jq rsync \
      postgresql-16 postgresql-client-16 openjdk-21-jre-headless \
      ffmpeg libgl1 libglib2.0-0 libgomp1 libsm6 libxext6 libxrender1 python3-venv \
 && rm -rf /var/lib/apt/lists/* /etc/nginx/sites-enabled/default

# Keycloak, Valkey, MinIO, mc
COPY --from=keycloak /opt/keycloak /opt/keycloak
COPY --from=valkey /usr/local/bin/valkey-server /usr/local/bin/valkey-cli /usr/local/bin/
COPY --from=minio /usr/bin/minio /usr/local/bin/minio
COPY --from=mc /usr/bin/mc /usr/local/bin/mc
RUN /opt/keycloak/bin/kc.sh build --db=postgres --health-enabled=true --metrics-enabled=false

# Python environments (uv for speed). torch/torchvision come from the base image.
RUN ${PY} -m pip install --no-cache-dir uv && \
    ${PY} -c "import torch,torchvision;print(f'torch=={torch.__version__}\ntorchvision=={torchvision.__version__}')" > /opt/torch-constraints.txt
COPY deploy/runpod/requirements/ /opt/dvas/requirements/
RUN set -e; \
  uv venv --system-site-packages --python ${PY} /opt/venvs/ai && \
  uv pip install --python /opt/venvs/ai/bin/python -c /opt/torch-constraints.txt -r /opt/dvas/requirements/ai.txt && \
  uv venv --system-site-packages --python ${PY} /opt/venvs/anpr && \
  uv pip install --python /opt/venvs/anpr/bin/python -c /opt/torch-constraints.txt -r /opt/dvas/requirements/anpr.txt && \
  for v in backend ingest gateway; do \
    uv venv --python ${PY} /opt/venvs/$v && \
    uv pip install --python /opt/venvs/$v/bin/python -r /opt/dvas/requirements/$v.txt; done && \
  /opt/venvs/ai/bin/python -c "import torch;assert torch.__version__.startswith('2.8'), torch.__version__"

# Application code
COPY dvas_analytics/            /opt/dvas/dvas_analytics/
COPY dvas_ingest/ingest/api/    /opt/dvas/dvas_ingest/ingest/api/
COPY dvas_ingest/ingest/scripts/ /opt/dvas/dvas_ingest/ingest/scripts/
COPY DVAS_Dashboard/backend/    /opt/dvas/DVAS_Dashboard/backend/
COPY --from=frontend /src/build /opt/dvas/frontend
COPY deploy/runpod/             /opt/dvas/deploy/
# Model weights from the named build context "models" (../dvas_models_stage) + checksum verification
COPY --from=models analytics/   /opt/dvas/dvas_analytics/
RUN cd /opt/dvas && sed 's#  \./analytics/#  ./dvas_analytics/#' deploy/models.sha256 | sha256sum -c --quiet && \
    ln -s /opt/dvas/dvas_analytics /analytics

# Pre-download PaddleOCR models into the image (use the SAME constructor args as anpr/analyzer.py)
RUN /opt/venvs/anpr/bin/python -c "from paddleocr import PaddleOCR; PaddleOCR(use_angle_cls=True, lang='en', use_gpu=False, show_log=False)"

# Config + scripts
RUN mkdir -p /etc/dvas /var/log/dvas /var/lib/valkey /run/postgresql /run/sshd && \
    cp /opt/dvas/deploy/etc/* /etc/dvas/ && chmod +x /opt/dvas/deploy/bin/* && \
    ln -s /opt/dvas/deploy/bin/dvas-ctl /usr/local/bin/dvas-ctl && \
    chown postgres:postgres /run/postgresql

EXPOSE 80 22
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/dvas/deploy/bin/dvas-init.sh"]
```

`deploy/runpod/Dockerfile.dockerignore` (the build context is the repo root):
```
**/node_modules
**/build
**/.venv
**/venv
**/__pycache__
**/*.pyc
**/*.MP4
**/*.mp4
**/*.pt
**/*.pth
**/*.onnx
**/.env
**/.env.*
**/*.log
_dvas_temp_raw_upload_*
videos/
reports/
PERFORMANCE REPORT/
**/output_documents/
dvas_edge/
face_recognition_project_best/
Crowd_Density/
intrusion_detection/
police_drone_person/
QWI_Face_Video_Search_Team_Package/
seadrone_deploy/
sus_deploy/
vehicle-tracking/
vehicle_detection_tracker-main/
*.pdf
*.docx
*.zip
```

Weights come **only** from the verified `models` build context, never from the working tree.

### 3.3 Dependency notes

- **ANPR / Paddle (F13).** Use `paddlepaddle==2.6.2` **CPU** in its own venv. OCR runs only on small plate crops, and `test_ocr_cpu.py` already exists in the repo. If no cp312 wheel exists for 2.6.2, use `paddlepaddle==3.0.x` (CPU) with `paddleocr 2.10.x`, then re-check OCR accuracy on 20 known plates. GPU Paddle 3.x (CUDA 12.6 build) is a later optimisation.
- **onnxruntime-gpu** must be a CUDA 12 / cuDNN 9 build (the default wheels from 1.19+). Verify with `python -c "import onnxruntime as o;print(o.get_available_providers())"` → must list `CUDAExecutionProvider`.
- **Python 3.12 (F16).** Run `python -m compileall -q /opt/dvas` inside the image as a build smoke test. Any `asyncio`/`distutils` breakage shows up here.
- **Image size.** Target ≤ 25 GB uncompressed (`docker image ls`). If it's bigger, drop `postgresql-client` extras, use `--no-install-recommends` everywhere, and make sure no `.git` or model duplicates were copied.

### 3.4 Runtime configuration files (`deploy/runpod/etc/`)

**`supervisord.conf`** (abridged; every analyzer follows the `crowd` pattern)
```ini
[unix_http_server]
file=/run/supervisor.sock
chmod=0700

[supervisord]
nodaemon=true
user=root
logfile=/var/log/dvas/supervisord.log
logfile_maxbytes=20MB
pidfile=/run/supervisord.pid

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

[supervisorctl]
serverurl=unix:///run/supervisor.sock

; shared defaults are repeated per program (supervisord has no inheritance)
[program:sshd]
command=/usr/sbin/sshd -D -e
priority=1
autorestart=true
stdout_logfile=/var/log/dvas/sshd.log
redirect_stderr=true

[program:postgres]
command=/usr/lib/postgresql/16/bin/postgres -D /workspace/dvas/pgdata -c config_file=/etc/dvas/postgresql.conf
user=postgres
priority=10
autorestart=true
stopsignal=INT
stopwaitsecs=90
stdout_logfile=/var/log/dvas/postgres.log
redirect_stderr=true
stdout_logfile_maxbytes=50MB

[program:valkey]
command=/usr/local/bin/valkey-server /etc/dvas/valkey.conf
priority=10
autorestart=true
stdout_logfile=/var/log/dvas/valkey.log
redirect_stderr=true

[program:minio]
command=/usr/local/bin/minio server /workspace/dvas/minio --address 127.0.0.1:9000 --console-address 127.0.0.1:9001
environment=MINIO_ROOT_USER="%(ENV_MINIO_ACCESS_KEY)s",MINIO_ROOT_PASSWORD="%(ENV_MINIO_SECRET_KEY)s"
priority=10
autorestart=true
stdout_logfile=/var/log/dvas/minio.log
redirect_stderr=true

[program:keycloak]
command=/opt/dvas/deploy/bin/wait-for.sh 127.0.0.1:5432 -- /opt/dvas/deploy/bin/keycloak-start.sh
priority=20
autorestart=true
startsecs=30
startretries=10
stopwaitsecs=60
stdout_logfile=/var/log/dvas/keycloak.log
redirect_stderr=true
stdout_logfile_maxbytes=50MB

[program:ingest]
directory=/opt/dvas/dvas_ingest/ingest/api
command=/opt/dvas/deploy/bin/wait-for.sh 127.0.0.1:6379 127.0.0.1:8080 -- /opt/venvs/ingest/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips=127.0.0.1 --no-access-log
environment=KEYCLOAK_URL="http://127.0.0.1:8080",REDIS_URL="redis://127.0.0.1:6379/0",QUEUE_MAX_LENGTH="300",QUEUE_MSG_TTL_MS="30000",MAX_FRAME_MB="5",LOG_JSON="true"
priority=30
autorestart=true
stdout_logfile=/var/log/dvas/ingest.log
redirect_stderr=true
stdout_logfile_maxbytes=50MB

[program:crowd]
directory=/analytics
command=/opt/dvas/deploy/bin/wait-for.sh 127.0.0.1:5432 127.0.0.1:6379 127.0.0.1:9000 -- /opt/venvs/ai/bin/uvicorn crowd.main:app --host 127.0.0.1 --port 8101 --workers 1 --no-access-log
environment=DATABASE_URL="%(ENV_ANALYTICS_DATABASE_URL)s",MODEL_PATH="/analytics/crowd/models/PartAmodel_best.pth",BUCKET_HEATMAPS="dvas-heatmaps",BUCKET_EVIDENCE="dvas-evidence",CROWD_ALERT_THRESHOLD="50",CROWD_MAX_FPS="2"
priority=40
autorestart=true
startsecs=20
startretries=10
stopasgroup=true
killasgroup=true
stdout_logfile=/var/log/dvas/crowd.log
redirect_stderr=true
stdout_logfile_maxbytes=50MB

; person  : port 8102, PERSON_MODEL=/analytics/person/models/yolov8m.pt, FACE_MODEL=/analytics/person/models/yolov8n-face.pt, BUCKET_FACES=dvas-faces, REID_CLUSTER_INTERVAL=50
; vehicle : port 8103, VEHICLE_MODEL=/analytics/vehicle/models/visdrone.pt, BUCKET_VEHICLES=dvas-vehicles, MINIO_PUBLIC_URL=""
; anpr    : port 8104, uses /opt/venvs/anpr, VEHICLE_MODEL=/analytics/anpr/models/visdrone.pt, LP_MODEL=/analytics/anpr/models/license_plate_detector.pt, BUCKET_PLATES=dvas-plates, MINIO_PUBLIC_URL=""
; intrusion: port 8105, INTRUSION_MODEL=/analytics/intrusion/models/geotrax_hbb_yolov8s_1920_v1.pt, INTRUSION_DEFAULT_PRESET=default, BUCKET_INTRUSION=dvas-intrusion
; vessel  : port 8106, VESSEL_MODEL_PATH=/analytics/vessel/models/yolov8s.pt, VESSEL_MODEL_TYPE=yolov8, BUCKET_VESSELS_MARITIME=dvas-vessels-maritime
; anomaly : port 8107, ANOMALY_MODEL_PRIMARY=/analytics/anomaly/models/weights/visdrone-yolov11x.pt, ANOMALY_MODEL_SECONDARY=/analytics/anomaly/models/weights/yolo11n.pt, BUCKET_ANOMALY=dvas-anomaly
; gateway : port 8100, uses /opt/venvs/gateway, module gateway.main:app

[group:analytics]
programs=crowd,person,vehicle,anpr,intrusion,vessel,anomaly,gateway

[program:backend]
directory=/opt/dvas/DVAS_Dashboard/backend
command=/opt/dvas/deploy/bin/wait-for.sh 127.0.0.1:5432 127.0.0.1:8080 127.0.0.1:6379 -- /opt/venvs/backend/bin/uvicorn server:app --host 127.0.0.1 --port 8001 --workers 1 --proxy-headers --forwarded-allow-ips=127.0.0.1
environment=DATABASE_URL="%(ENV_DASHBOARD_DATABASE_URL)s",INGEST_SERVER_URL="http://127.0.0.1:8000",SECURE_COOKIES="true",EDGE_SYNC_HOSTS="",DVAS_PEER_URL=""
priority=50
autorestart=true
stdout_logfile=/var/log/dvas/backend.log
redirect_stderr=true
stdout_logfile_maxbytes=50MB

[program:nginx]
command=/usr/sbin/nginx -c /etc/dvas/nginx.conf -g "daemon off;"
priority=60
autorestart=true
stdout_logfile=/var/log/dvas/nginx.log
redirect_stderr=true

[program:bootstrap]
command=/opt/dvas/deploy/bin/dvas-bootstrap.sh
priority=90
autorestart=false
startsecs=0
stdout_logfile=/var/log/dvas/bootstrap.log
redirect_stderr=true

[program:backup]
command=/opt/dvas/deploy/bin/backup-loop.sh
priority=95
autorestart=true
stdout_logfile=/var/log/dvas/backup.log
redirect_stderr=true
```

**Important.** `DATABASE_URL` means different databases for the analytics services and the backend. The global env therefore defines `ANALYTICS_DATABASE_URL` and `DASHBOARD_DATABASE_URL`, and each program maps one of them to `DATABASE_URL`. Everything else (`REDIS_URL`, `MINIO_*`, `KC_*`, `ANALYTICS_SERVICE_KEY`, `FACE_VECTOR_ENCRYPTION_KEY`, `DRONE_IDS`, analytics service URLs) is exported globally by `dvas-init.sh` and inherited by all programs.

**`nginx.conf`**
```nginx
worker_processes auto;
events { worker_connections 4096; }
http {
  include /etc/nginx/mime.types;
  sendfile on; tcp_nopush on; keepalive_timeout 65; server_tokens off;
  access_log /var/log/dvas/nginx_access.log;  error_log /var/log/dvas/nginx_error.log warn;
  client_max_body_size 100m;                 # Cloudflare in front caps a single request at ~100 MB anyway

  # Real client IP from the RunPod/Cloudflare chain (verify header names in Phase 5.4)
  set_real_ip_from 0.0.0.0/0;  real_ip_header X-Forwarded-For;  real_ip_recursive on;

  map $http_upgrade $connection_upgrade { default upgrade; '' close; }
  limit_req_zone $binary_remote_addr zone=ingest:10m rate=150r/s;   # 3 drones × 30 fps from one site + headroom
  limit_req_zone $binary_remote_addr zone=auth:10m   rate=10r/s;

  upstream ingest   { server 127.0.0.1:8000; keepalive 64; }
  upstream backend  { server 127.0.0.1:8001; keepalive 64; }
  upstream keycloak { server 127.0.0.1:8080; keepalive 16; }

  server {
    listen 80 default_server;
    root /opt/dvas/frontend;  index index.html;
    gzip on; gzip_types text/css application/javascript application/json image/svg+xml; gzip_min_length 1024;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options SAMEORIGIN always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;

    location = /healthz { access_log off; return 200 "ok\n"; }

    location /ingest/ {
      limit_req zone=ingest burst=300 nodelay;  limit_req_status 429;
      proxy_pass http://ingest;  proxy_http_version 1.1;  proxy_set_header Connection "";
      proxy_set_header Host $host;  proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;  proxy_set_header X-Forwarded-Proto https;
      client_max_body_size 10m;  proxy_read_timeout 30s;  proxy_send_timeout 30s;
    }

    location ~ ^/(realms|resources)/ {            # token endpoint, JWKS, login theme assets — NOT /admin
      limit_req zone=auth burst=30 nodelay;  limit_req_status 429;
      proxy_pass http://keycloak;  proxy_http_version 1.1;  proxy_set_header Connection "";
      proxy_set_header Host $host;  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto https;  proxy_set_header X-Forwarded-Host $host;
    }

    location /api/ {
      proxy_pass http://backend;  proxy_http_version 1.1;
      proxy_set_header Upgrade $http_upgrade;  proxy_set_header Connection $connection_upgrade;
      proxy_set_header Host $host;  proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;  proxy_set_header X-Forwarded-Proto https;
      proxy_buffering off;  proxy_read_timeout 3600s;  proxy_send_timeout 300s;
    }

    location /edge-api/ { default_type application/json; return 404 '{"detail":"Edge API is only available in the DVAS Edge application"}'; }

    location = /index.html { add_header Cache-Control "no-store" always; }
    location /static/      { expires 1y; add_header Cache-Control "public, immutable"; }
    location /             { try_files $uri /index.html; }
  }
}
```

**`valkey.conf`** (fixes F6)
```
bind 127.0.0.1
port 6379
protected-mode yes
dir /var/lib/valkey
maxmemory 8gb
maxmemory-policy volatile-lru      # only keys with a TTL may be evicted; streams are bounded by MAXLEN
appendonly no
save ""                            # frames are transient; no disk persistence
tcp-keepalive 60
```

**`postgresql.conf`**
```
listen_addresses = '127.0.0.1'
port = 5432
unix_socket_directories = '/run/postgresql'
hba_file = '/etc/dvas/pg_hba.conf'
max_connections = 300
shared_buffers = 4GB
effective_cache_size = 16GB
work_mem = 16MB
maintenance_work_mem = 512MB
synchronous_commit = off           # may lose <1 s of detections on a crash; never corrupts
wal_compression = on
max_wal_size = 1GB
min_wal_size = 256MB
checkpoint_timeout = 15min
max_parallel_workers_per_gather = 0   # avoids /dev/shm usage (container shm can be small)
dynamic_shared_memory_type = posix
log_min_duration_statement = 500
timezone = 'Asia/Kolkata'
```

**`pg_hba.conf`**
```
local   all   postgres                 peer
local   all   all                      scram-sha-256
host    all   all   127.0.0.1/32       scram-sha-256
```

### 3.5 Scripts (`deploy/runpod/bin/`)

**`dvas-init.sh`** — runs on every container start, before supervisord.
```bash
#!/usr/bin/env bash
set -euo pipefail
VOL=/workspace/dvas; PGDATA=$VOL/pgdata; PGBIN=/usr/lib/postgresql/16/bin
mkdir -p $VOL/{pgdata,minio,engines,backups,secrets,state,calibration} /var/log/dvas /var/lib/valkey /run/postgresql
chown postgres:postgres $PGDATA /run/postgresql; chmod 700 $PGDATA $VOL/secrets

# 1) Secrets: generated once onto the network volume; real env vars (e.g. RunPod Secrets) win
[ -f $VOL/secrets/dvas.env ] || /opt/dvas/deploy/bin/gen-secrets.sh $VOL/secrets
set -a; . $VOL/secrets/dvas.env; set +a
export PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-https://${RUNPOD_POD_ID:-localhost}-80.proxy.runpod.net}"
export DVAS_DEPLOYMENT=cloud DVAS_RUNTIME=supervisor
export ANALYTICS_DATABASE_URL="postgresql://dvas:${DVAS_DB_PASSWORD}@127.0.0.1:5432/dvas?sslmode=disable"
export DASHBOARD_DATABASE_URL="postgresql+asyncpg://dvas:${DVAS_DB_PASSWORD}@127.0.0.1:5432/dvas_dashboard"
export REDIS_URL="redis://127.0.0.1:6379/0" MINIO_ENDPOINT="127.0.0.1:9000" MINIO_SECURE=false MINIO_PUBLIC_URL=""
export KEYCLOAK_URL="http://127.0.0.1:8080" KC_REALM=kerala-police-drone KC_CLIENT_ID=drone-ingest-client
export CORS_ORIGINS="${PUBLIC_BASE_URL},http://localhost:8765" ALLOWED_ORIGINS="${PUBLIC_BASE_URL},http://localhost:8765"
export ANALYTICS_GATEWAY_URL=http://127.0.0.1:8100 CROWD_ANALYTICS_URL=http://127.0.0.1:8101 PERSON_ANALYTICS_URL=http://127.0.0.1:8102 \
       VEHICLE_ANALYTICS_URL=http://127.0.0.1:8103 ANPR_ANALYTICS_URL=http://127.0.0.1:8104 INTRUSION_ANALYTICS_URL=http://127.0.0.1:8105 \
       VESSEL_ANALYTICS_URL=http://127.0.0.1:8106 ANOMALY_ANALYTICS_URL=http://127.0.0.1:8107 \
       CROWD_URL=http://127.0.0.1:8101 PERSON_URL=http://127.0.0.1:8102 VEHICLE_URL=http://127.0.0.1:8103 ANPR_URL=http://127.0.0.1:8104 \
       INTRUSION_URL=http://127.0.0.1:8105 VESSEL_URL=http://127.0.0.1:8106 ANOMALY_URL=http://127.0.0.1:8107
export VRAM_OFFLOAD_ON_STOP=false

# 2) Compose hostnames -> localhost (catches hard-coded defaults). Ignore failure if /etc/hosts is read-only.
for h in postgres valkey redis minio keycloak drone_nginx ingest-api backend analytics-gateway crowd-analyzer \
         person-analyzer vehicle-analyzer anpr-analyzer intrusion-analyzer vessel-analyzer anomaly-analyzer; do
  grep -qE "[[:space:]]$h(\s|$)" /etc/hosts || echo "127.0.0.1 $h" >> /etc/hosts || true
done

# 3) SSH key from RunPod
if [ -n "${PUBLIC_KEY:-}" ]; then mkdir -p /root/.ssh; echo "$PUBLIC_KEY" > /root/.ssh/authorized_keys
  chmod 700 /root/.ssh; chmod 600 /root/.ssh/authorized_keys; fi
ssh-keygen -A >/dev/null 2>&1 || true
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config

# 4) First-time database creation (Postgres started temporarily on the unix socket only)
if [ ! -s $PGDATA/PG_VERSION ]; then
  su postgres -s /bin/bash -c "$PGBIN/initdb -D $PGDATA -U postgres -E UTF8 --locale=C.UTF-8 --auth-local=peer --auth-host=scram-sha-256"
fi
if [ ! -f $VOL/state/db_bootstrapped ]; then
  su postgres -s /bin/bash -c "$PGBIN/pg_ctl -D $PGDATA -o \"-c config_file=/etc/dvas/postgresql.conf -c listen_addresses=''\" -w start"
  PSQL="su postgres -s /bin/bash -c"
  $PSQL "psql -v ON_ERROR_STOP=1 -q" <<SQL
CREATE ROLE keycloak LOGIN PASSWORD '${KC_DB_PASSWORD}';  CREATE DATABASE keycloak OWNER keycloak;
CREATE ROLE dvas     LOGIN PASSWORD '${DVAS_DB_PASSWORD}'; CREATE DATABASE dvas OWNER dvas; CREATE DATABASE dvas_dashboard OWNER dvas;
SQL
  load() { # $1=db $2=sql file : extensions as superuser, everything else as role dvas
    grep -E '^CREATE EXTENSION' "$2" | $PSQL "psql -v ON_ERROR_STOP=1 -q -d $1" || true
    sed -E '/^(CREATE|COMMENT ON) EXTENSION/d' "$2" | $PSQL "psql -v ON_ERROR_STOP=1 -q -d $1 -c 'SET ROLE dvas' -f -"; }
  load dvas           /opt/dvas/deploy/db/analytics_000_baseline_schema.sql
  load dvas           /opt/dvas/deploy/db/analytics_001_reference_data.sql
  load dvas_dashboard /opt/dvas/deploy/db/dashboard_000_baseline_schema.sql
  su postgres -s /bin/bash -c "$PGBIN/pg_ctl -D $PGDATA -m fast -w stop"
  date -Is > $VOL/state/db_bootstrapped
fi

# 5) Optional dev mode: run code from a git checkout on the volume instead of the baked copy
if [ "${DVAS_DEV_MODE:-0}" = 1 ] && [ -d $VOL/code/.git ]; then
  for d in dvas_analytics DVAS_Dashboard/backend dvas_ingest/ingest/api; do
    rsync -a --exclude '*.pt' --exclude '*.pth' --exclude '*.onnx' $VOL/code/$d/ /opt/dvas/$d/; done
fi

exec /usr/bin/supervisord -c /etc/dvas/supervisord.conf
```

**`gen-secrets.sh`** — writes `dvas.env` and `users.json` (both `chmod 600`). Use `openssl rand -base64 32 | tr -d '=+/' | cut -c1-32` for each of:
- `KC_DB_PASSWORD`, `DVAS_DB_PASSWORD`
- `KEYCLOAK_ADMIN=admin`, `KEYCLOAK_ADMIN_PASSWORD`
- `KC_CLIENT_SECRET`, `ADMIN_CLIENT_ID=dvas-admin-service`, `ADMIN_CLIENT_SECRET`
- `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`
- `ANALYTICS_SERVICE_KEY`, `DVAS_SERVICE_KEY`, `EDGE_API_KEY`, `JWT_SECRET`
- `FACE_VECTOR_ENCRYPTION_KEY` — same format as today's value, see `dvas_analytics/shared/crypto.py`
- `DRONE_IDS`

It also writes one random password per test user into `users.json`: `control_001`, `senior_001`, `admin_dvas`, `operator_001..003` (same usernames/emails/roles as the current credentials matrix). **Back up `dvas.env` offline straight after first boot** — losing `FACE_VECTOR_ENCRYPTION_KEY` makes stored face embeddings unreadable.

**`keycloak-start.sh`**
```bash
#!/usr/bin/env bash
set -e
export KC_DB=postgres KC_DB_URL="jdbc:postgresql://127.0.0.1:5432/keycloak" KC_DB_USERNAME=keycloak KC_DB_PASSWORD="$KC_DB_PASSWORD"
export JAVA_OPTS_KC_HEAP="-Xms512m -Xmx1536m"
if [ ! -f /workspace/dvas/state/realm_imported ]; then
  /opt/keycloak/bin/kc.sh import --optimized --file /opt/dvas/deploy/keycloak/realm-template.json --override false
  date -Is > /workspace/dvas/state/realm_imported
fi
exec /opt/keycloak/bin/kc.sh start --optimized --http-enabled=true --http-host=127.0.0.1 --http-port=8080 \
  --hostname-url="$PUBLIC_BASE_URL" --hostname-strict=false --hostname-strict-backchannel=false \
  --proxy-headers=xforwarded
```
On first start, `KEYCLOAK_ADMIN` / `KEYCLOAK_ADMIN_PASSWORD` (exported from `dvas.env`) create the master admin.

**`wait-for.sh`** — `wait-for.sh host:port [host:port...] -- cmd...`. Loops on bash `/dev/tcp` checks, up to 300 s, then `exec "$@"`.

**`dvas-bootstrap.sh`** — one-shot, idempotent, runs on **every** boot:
1. Wait for `http://127.0.0.1:8080/health/ready`, `http://127.0.0.1:9000/minio/health/live` and `http://127.0.0.1:8001/api/`.
2. **Keycloak:** `python setup_keycloak.py --url http://127.0.0.1:8080 --client-secret $KC_CLIENT_SECRET --admin-client-secret $ADMIN_CLIENT_SECRET --users-file $VOL/secrets/users.json --frontend-url $PUBLIC_BASE_URL`. This re-applies the current pod URL after a pod is re-created.
3. **MinIO:**
   - `mc alias set local http://127.0.0.1:9000 …`
   - `mc mb --ignore-existing` for the 9 buckets: `dvas-evidence`, `dvas-faces`, `dvas-plates`, `dvas-heatmaps`, `dvas-vehicles`, `dvas-intrusion`, `dvas-watchlist`, `dvas-vessels-maritime`, `dvas-anomaly`
   - **No anonymous policies**
   - Apply the ILM rules from Phase 5.6
4. **TensorRT engines** (only if `DVAS_TRT=1` and an engine is missing, Phase 9.4): `python tools/build_trt_engines.py --out /workspace/dvas/engines`, then `supervisorctl restart analytics:*`.
5. Write `$VOL/state/bootstrap_ok` and print a status summary (program states, disk usage, GPU).

**`dvas-ctl`** — admin helper:
- `dvas-ctl status`
- `dvas-ctl restart <prog|analytics>`
- `dvas-ctl logs <prog>` (`tail -f`)
- `dvas-ctl health` (curls every `/health`)
- `dvas-ctl lag` (`XINFO GROUPS` for each drone stream)
- `dvas-ctl disk`
- `dvas-ctl backup-now` (runs one backup immediately)

`dvas-ctl` wraps `supervisorctl -c /etc/dvas/supervisord.conf`.

**`backup-loop.sh`** — see Phase 12.3.

### 3.6 Build and test locally
```bash
cd /mnt/NewVolume/DVAS_RUNPODV1
TAG=$(date +%Y%m%d)-$(git rev-parse --short HEAD)
docker buildx build -f deploy/runpod/Dockerfile --build-context models=../dvas_models_stage \
  --build-arg BASE_IMAGE=$BASE -t ghcr.io/quantumweaveintelligence/dvas-runpod:$TAG --load .

# Simulate the pod: fake network volume, port 80 -> 8088
mkdir -p /tmp/dvas-vol
docker run -d --name dvas-pod-test --gpus all -p 8088:80 -v /tmp/dvas-vol:/workspace \
  -e PUBLIC_BASE_URL=http://localhost:8088 -e PUBLIC_KEY="$(cat ~/.ssh/id_ed25519.pub)" \
  --shm-size=1g ghcr.io/quantumweaveintelligence/dvas-runpod:$TAG
docker exec -it dvas-pod-test dvas-ctl status
docker exec -it dvas-pod-test dvas-ctl health
```
Stop the local compose stacks first or use different ports — **8088 must not collide**. Then:
- Log in at `http://localhost:8088` with `control_001` (password from `/tmp/dvas-vol/dvas/secrets/users.json`).
- Push a test frame (Phase 5.4 command, with `localhost:8088`).
- `docker restart dvas-pod-test` → everything returns; the DB isn't re-initialised.

### Files
`deploy/runpod/{Dockerfile,Dockerfile.dockerignore,etc/*,bin/*,requirements/*}`.

### ✅ Done when
- [ ] Image builds from a **clean clone** + models context; checksum verification passes
- [ ] `python -m compileall` and all imports pass inside the image (Python 3.12)
- [ ] Local container: all programs `RUNNING` for 10 min, no restart loops
- [ ] `nvidia-smi` inside the container shows the analyzer processes
- [ ] Login works, a test frame reaches Valkey, at least one analyzer processes it
- [ ] Restarting the container keeps DB + MinIO data; second boot skips DB init
- [ ] Image ≤ 25 GB uncompressed

**↩ Rollback.** Nothing is deployed yet; fix and rebuild.

---

## Phase 4 — Push to GHCR & provision RunPod

**Goal.** A running pod on an L40S with the network volume attached, using the DVAS image.
**Where.** 💻 + RunPod console.
**Prerequisites.** Phase 3.
**Indicative effort.** 0.5 day.

### Steps

1. **Push the image**
   ```bash
   docker push ghcr.io/quantumweaveintelligence/dvas-runpod:$TAG
   ```
   Use **immutable tags** (date + commit); never deploy `latest`.
2. **Registry credentials in RunPod.** *Settings → Container Registry Auth → Add*: registry `ghcr.io`, your GitHub username, the **read:packages** PAT.
3. **Network volume.** *Storage → New Network Volume*:
   - DC: the one chosen in Phase 0
   - Size: **30 GB**
   - Name: `dvas-vol-test`
4. **Template.** *Templates → New Template*:

   | Field | Value |
   |---|---|
   | Name | `dvas-runpod-test` |
   | Type | Pod |
   | Container image | `ghcr.io/quantumweaveintelligence/dvas-runpod:<TAG>` |
   | Registry credentials | the GHCR entry |
   | Container start command | *(empty — the image ENTRYPOINT runs)* |
   | Container disk | **50 GB** |
   | Volume mount path | `/workspace` |
   | Expose HTTP ports | **80** |
   | Expose TCP ports | **22** |
   | Environment variables | `TZ=Asia/Kolkata`, `DVAS_TRT=0`, `DVAS_DEV_MODE=0` (secrets are generated on the volume at first boot; RunPod Secrets can override any variable later) |

5. **Deploy the pod.** *Pods → Deploy*:
   - **Secure Cloud**, choose network volume `dvas-vol-test` (this locks the DC)
   - GPU **L40S × 1**
   - Template `dvas-runpod-test`
   - On-Demand
   - Check that the summary shows 16 vCPU / 62 GB RAM
   - Deploy
6. Wait for *Running*. Open *Connect*:
   - **HTTP Service :80** → `https://<POD_ID>-80.proxy.runpod.net`
   - **TCP :22** → `ssh root@<IP> -p <PORT> -i ~/.ssh/id_ed25519`
7. **Check stop/restart behaviour for your account** in the pod menu: is *Stop* offered for a pod with a network volume, or only *Terminate*? Write the answer into the runbook (Phase 12.1). It decides whether the URL survives overnight shutdowns.

### ✅ Done when
- [ ] Pod running on L40S, Secure Cloud, network volume mounted (`df -h /workspace` shows ~30 GB)
- [ ] HTTPS proxy URL returns the DVAS login page; SSH works
- [ ] Stop-vs-terminate behaviour documented

**↩ Rollback.** Terminate the pod; the network volume keeps any data. Deploy again with the previous image tag.

---

## Phase 5 — First boot, platform bootstrap & smoke tests

**Goal.** Prove every service works in the pod, with persistence on the network volume.
**Where.** ☁️ over SSH + 💻 browser/curl.
**Prerequisites.** Phase 4.
**Indicative effort.** 1 day.

### Steps

**5.1 Watch the first boot**
```bash
ssh root@<IP> -p <PORT>
tail -f /var/log/dvas/supervisord.log /var/log/dvas/bootstrap.log
dvas-ctl status            # expect every program RUNNING, bootstrap EXITED (0)
cat /workspace/dvas/state/*
```

**5.2 Retrieve and secure the generated credentials**
```bash
cat /workspace/dvas/secrets/users.json      # test user passwords
cat /workspace/dvas/secrets/dvas.env        # copy BOTH files to the team password manager now
```

**5.3 Service health**
```bash
dvas-ctl health      # 8000, 8001, 8080/health/ready, 8100–8107 /health, 9000 /minio/health/live
nvidia-smi           # analyzer PIDs listed, VRAM total < 30 GB at idle
free -g; df -h / /workspace /dev/shm
valkey-cli ping
su postgres -c "psql -d dvas -c '\dt'" | head; su postgres -c "psql -d dvas_dashboard -c '\dt'" | head
mc ls local
```

**5.4 Public-path tests (💻)**
```bash
URL=https://<POD_ID>-80.proxy.runpod.net
curl -s $URL/healthz
# Token for an operator (ROPC via the drone-ingest-client)
TOKEN=$(curl -s -X POST $URL/realms/kerala-police-drone/protocol/openid-connect/token \
  -d grant_type=password -d client_id=drone-ingest-client -d client_secret=$KC_CLIENT_SECRET \
  -d username=operator_001 -d password='<from users.json>' | jq -r .access_token)
# One frame
curl -s -X POST $URL/ingest/frame -H "Authorization: Bearer $TOKEN" \
  -F "frame=@test.jpg;type=image/jpeg" \
  -F 'metadata_json={"drone_id":"DRN-TEST","mission_id":"MSN-SMOKE-1","timestamp":"2026-10-26T10:00:00Z"}'
```
Use the real `FrameMetadata` field names from `ingest/api/models.py`. Then check on the pod: `valkey-cli XLEN mission:frames:$(valkey-cli GET drone:active_mission:DRN-TEST)` (`drone:frames:DRN-TEST` with STREAM_KEY_MODE=drone).

Confirm what the pod actually sees as the client IP:
- `tail /var/log/dvas/nginx_access.log` should show **your** public IP.
- If it shows a RunPod/Cloudflare IP, change `real_ip_header` to the header that carries the client IP (log `$http_x_forwarded_for` / `$http_cf_connecting_ip` once to see which). This matters for the per-site rate limit.

**5.5 Persistence test**
- Create a test user note or alert in the dashboard and upload a test object to MinIO.
- *Stop/Restart* (or terminate and re-deploy with the same volume, if Stop isn't offered).
- After boot: the data is still there, the DB init was skipped, and the bootstrap updated the Keycloak `frontendUrl` to the new URL.

**5.6 Storage retention (mandatory with 30 GB)** — applied by `dvas-bootstrap.sh`:

| Bucket | Test retention | Why |
|---|---|---|
| `dvas-heatmaps` | 3 days | regenerable |
| `dvas-faces`, `dvas-vehicles`, `dvas-plates`, `dvas-intrusion`, `dvas-anomaly`, `dvas-vessels-maritime` | 14 days | test crops |
| `dvas-evidence` | **no automatic expiry** | Section 65B evidence — delete only by procedure |
| `dvas-watchlist` | **never** | reference images |

- Add a disk guard: `backup-loop.sh` also checks `df /workspace` hourly and writes a `system_alert` row (shown on the Ingest dashboard) when usage passes **80 %**.
- Valkey streams use `QUEUE_MAX_LENGTH=300` (10 s at 30 fps).

### ✅ Done when
- [ ] All programs RUNNING after first boot; `bootstrap_ok` written
- [ ] Credentials stored in the password manager; `dvas.env` backed up offline
- [ ] Token + frame ingest work through the public URL; the real client IP is visible in nginx logs
- [ ] Data survives a restart/re-deploy; Keycloak `frontendUrl` follows the URL
- [ ] ILM rules listed by `mc ilm rule ls local/<bucket>`

**↩ Rollback.** Terminate the pod. If the volume state is bad, delete `/workspace/dvas/state/*` (and `pgdata` for a full reset) and redeploy.

---

## Phase 6 — Dashboard go-live (front-end + back-end)

**Goal.** Every role can use the cloud dashboard end-to-end through the public URL.
**Where.** 💻 browser (Chrome + Edge/Firefox) and ☁️ logs.
**Prerequisites.** Phase 5.
**Indicative effort.** 1–2 days (mostly fixing what the checklist finds).

### Checklist (run as each role)

| Area | Test | Role |
|---|---|---|
| Login / redirect | Login → lands on `/command/tactical-map` (CRO, Senior), `/ingest` (Admin); `operator_001` is **refused** with the "use the Edge app" message | all |
| Token refresh | Stay idle past the access-token lifetime → `/api/auth/refresh` succeeds, no logout | CRO |
| WebSocket | DevTools → WS `/api/ws` connected, pings every 20 s, survives 10 min idle | CRO |
| Command pages (13) | tactical-map, multi-drone, alerts, crowd, traffic, person, intrusion, anomaly, evidence, missions, models, exports, reports — each loads without console errors | CRO / Senior |
| Models page | Start/stop analyzer streams; status reflects `/health` | Senior |
| Admin (7 tabs) | Users & roles (Keycloak admin API via `dvas-admin-service`), sessions, ABAC, legal holds, settings, compliance | Admin |
| Ingest dashboard | Container grid shows **supervisor programs** (Phase 2.3); GPU telemetry via pynvml; restart of an analyzer works; restarting backend/nginx/postgres/keycloak is **blocked** | Admin |
| Audit | Audit explorer lists entries; hash-chain **verify** passes; PDF export < 90 s | Admin / Senior |
| Reports | Each report type generates in < 90 s (otherwise convert to async job — Phase 2.4) | Senior |
| Images | Gallery thumbnails load via `/api/minio/image?...&token=` | CRO |
| Security | `$URL/admin/master/console` → **SPA page, not Keycloak**; `$URL/docs` → SPA; MinIO not reachable | — |

### ✅ Done when
- [ ] Every row passes for every listed role
- [ ] No 5xx in `backend.log`/`nginx_error.log` during the run
- [ ] Known gaps logged as issues on the `runpod-v1` board

**↩ Rollback.** Redeploy the previous image tag.

---

# BLOCK C — EDGE (owned by the edge teammate)

## Phase 7 — Edge integration handoff

> The Windows edge operator application (`DVAS_Edge_Setup.exe`) is built by the edge teammate and has its own plan: **`DVAS_Edge_EXE_Build_Plan.md`**. This phase covers only what the RunPod owner must **provide to** and **agree with** the edge team, so the two pieces fit together.

**Goal.** The edge team can build and test the `.exe` against the running pod, without either side blocking the other.
**Where.** ☁️ + communication with the edge teammate.
**Prerequisites.** Phase 6.
**Indicative effort (RunPod side).** 0.5 day.

### 7.1 What the RunPod side hands over

| Item | Value / where it comes from |
|---|---|
| Server base URL | `https://<POD_ID>-80.proxy.runpod.net` (Phase 4). Tell the edge team immediately when it changes. |
| Ingest endpoints | `/ingest/frame`, `/ingest/mission/start`, `/ingest/mission/end`, `/ingest/mission/geofence` |
| Token endpoint | `/realms/kerala-police-drone/protocol/openid-connect/token` (client `drone-ingest-client`) |
| Client secret | `KC_CLIENT_SECRET` from `/workspace/dvas/secrets/dvas.env` — share via the password manager, never by chat/email |
| Operator logins | `operator_001..003` passwords from `users.json` |
| Uploader accounts | One Keycloak user per laptop (`edge_laptop_01`, …, role `drone-operator`), created by you in Keycloak |
| `EDGE_API_KEY` | from `dvas.env` |
| Cloud API used by edge pages | `/api/auth/login`, `/api/auth/refresh`, `/api/auth/me`, `/api/missions*`, `/api/audit-logs*`, `/api/incidents*`, `/api/edge/*`, `/api/push/receive`, `/api/ws` |
| CORS origin to allow | `http://localhost:8765` (already in `CORS_ORIGINS` / `ALLOWED_ORIGINS`, Phase 3) |

### 7.2 Agreements (the interface contract)

1. **No edge code in the cloud image.** `dvas_edge/` is excluded from the RunPod image; the cloud returns `404` for `/edge-api/*`.
2. **Shared frontend change lands on `testing` first.** The build-target switch (`REACT_APP_DEPLOYMENT_TARGET`, Phase 2.7) touches `App.js`, `Login.js`, `AuthContext.js` and `useJpegPoller.js`, which both teams build from. The RunPod owner implements it once in a small PR to `testing`; both `runpod-v1` and the edge branch merge it from there. Avoids conflicting edits in the same files.
3. **API changes are announced.** Any change to the routes in 7.1 is posted to the edge teammate before merge.
4. **Frame metadata.** The edge adds `frame_seq` and `capture_ts` to each frame's metadata (used by Phase 9.2). Until then the cloud treats them as optional.
5. **Test drones/missions.** Use drone IDs `DRN-001..003` for the 3-feed test, so Phases 8 and 11 can be run with the real `.exe`.
6. **Until the `.exe` is ready,** Phases 8 and 11 use `tools/loadtest_ingest.py` as the 3 synthetic drones; the real `.exe` is added when it's available.

### ✅ Done when
- [ ] Edge teammate has the URL, credentials (via password manager) and this contract
- [ ] Shared frontend build-target PR merged to `testing` and into `runpod-v1`
- [ ] A test frame from the edge teammate's build appears in `dvas-ctl lag` on the pod

---

# BLOCK D — VALIDATE & OPTIMISE

## Phase 8 — Baseline end-to-end test (functional)

**Goal.** Prove the whole chain works with the **unoptimised** code and record baseline numbers. Phases 9–11 are measured against these.
**Where.** 🪟 edge laptop(s) running the teammate's `.exe` (when available) + ☁️ + 💻.
**Prerequisites.** Phase 6 and Phase 7 handoff (the real `.exe` is optional — use `loadtest_ingest.py` until the edge teammate's build is ready).
**Indicative effort.** 1 day.

### 8.1 Measurement tools (create in `deploy/runpod/tools/`)

- **`measure.py`** (runs on the pod, every 5 s, writes CSV to `/workspace/dvas/calibration/run-<ts>.csv`):
  - processed fps per analyzer — delta of `frames_processed` from each `/health`
  - stream lag per consumer group — `XINFO GROUPS mission:frames:<mission>` (legacy `drone:frames:<id>`) → `lag`, `pending`
  - GPU util/VRAM per process — `nvidia-smi --query-compute-apps` + `pynvml`
  - CPU % per program — psutil by supervisord PID
  - Postgres TPS — `pg_stat_database`
- **`loadtest_ingest.py`** (synthetic drones):
  - Replays a real drone video (e.g. `DJI_0110.MP4`) as **N virtual drones** at F fps
  - Encodes 720p or 1080p JPEG at quality Q
  - Concurrency 10, adds `frame_seq`
  - Gets tokens from Keycloak
  - Reports achieved fps, 2xx/429/5xx counts and p50/p95/p99 latency
  - Run it **from India** (real WAN) and once **inside the pod** against `127.0.0.1:80` (server capacity without the WAN)

### 8.2 Test runs

| Run | Source | Drones × fps | Analyzers enabled | Duration |
|---|---|---|---|---|
| B1 | 1 edge laptop with the teammate's `.exe` (or loadtest 1 × 10 fps if not ready yet), cognitive mode | 1 × ~10 | person, vehicle, crowd (defaults) | 15 min |
| B2 | 1 edge laptop, bypass (or loadtest 1 × 30 fps) | 1 × 30 | defaults | 15 min |
| B3 | loadtest from India | 3 × 30 @ 720p q75 | none (ingest only) | 15 min |
| B4 | loadtest from India | 3 × 30 | all 7 | 15 min |

Record: ingest success rate, edge→pod p95 latency, per-analyzer processed fps and lag growth, GPU %, VRAM, CPU %, RAM, disk growth per hour.

**What to expect.** B3 should pass (ingest isn't the bottleneck). In B4, analyzer lag will grow without bound for the heavier models. That's the expected finding motivating Phases 9–10, not a failure of this phase.

### 8.3 Functional checks during B1/B2
- Live feed tile in Multi-Drone view; annotated person feed; alerts appear in Alerts Feed within ~3 s of the event; evidence record auto-created by `evidence_sync`; mission ends cleanly and streams are drained/purged.
- Geofence armed from the edge → intrusion analyzer receives it.
- Telemetry (GPS) on the Tactical Map.

### ✅ Done when
- [ ] B1–B4 executed; CSVs and a 1-page baseline summary saved to `/workspace/dvas/calibration/` and the project folder
- [ ] All functional checks in 8.3 pass in B1/B2
- [ ] Bottleneck list ranked (by GPU ms and lag)

---

## Phase 9 — Performance foundation

**Goal.** Cut the cost per frame so the governor in Phase 10 has room to work with. Each item is independent — do them in order, measure after each, and keep only what helps.
**Where.** 💻 code → image rebuild → ☁️ measure (or `DVAS_DEV_MODE=1` for quick iterations).
**Prerequisites.** Phase 8.
**Indicative effort.** 5–8 days.

| # | Change | Where | Expected effect |
|---|---|---|---|
| 9.1 | **Single frame payload.** Ingest stores only the raw JPEG `frame` field (drop `jpeg_b64`). Add `shared/frames.py::decode_frame(fields)` that uses `frame` bytes and falls back to `jpeg_b64` for old entries. `xreadgroup_frames` returns the bytes instead of skipping them. Update all 7 analyzers to call `decode_frame`. | `ingest/api/queue_manager.py`, `shared/redis_client.py`, analyzers | −57 % Valkey memory, no base64 CPU in 8 processes |
| 9.2 | **Latest-frame-wins sampler.** `shared/sampler.py`: per drone read up to 32 entries (`XREADGROUP … COUNT 32 BLOCK 50`), keep the **newest** entry whose `capture_ts` is due (`1/target_fps` since the last processed one) and whose `frame_seq` is > the last processed seq, **XACK all**, and never decode the skipped ones. Target fps comes from env now, from the governor in Phase 10. Crowd already does latest-frame-wins and gets its cap in 2.5.1; migrate it onto the shared sampler too, so all 7 services behave the same. | `shared/`, every analyzer loop | Bounded lag (≤ 1 frame), no backlog, CPU only for sampled frames |
| 9.3 | **FP16 + cudnn.benchmark.** `half=True` on every Ultralytics `predict/track` call; `torch.backends.cudnn.benchmark=True`; CSRNet under `torch.autocast("cuda", dtype=torch.float16)`. | analyzers | ~1.5–2× faster inference |
| 9.4 | **TensorRT engines** for the YOLO models. `tools/build_trt_engines.py` exports each `.pt` with `model.export(format="engine", half=True, imgsz=<service size>, device=0)` into `/workspace/dvas/engines/` (engines are GPU + TensorRT-version specific, so they're built on the pod, once). Model env vars point to `.engine` when `DVAS_TRT=1`; `.pt` remains the fallback. Requires `VRAM_OFFLOAD_ON_STOP=false` (an engine can't be moved to CPU) and the `tensorrt` pip package for CUDA 12 in `ai.txt`. | tools, env | further ~1.5–2× on YOLO models |
| 9.5 | **Annotate on demand.** Draw the HUD and JPEG-encode the annotated frame **only** when someone is watching that service+drone (`dvas:viewer:*` key, Phase 10.2), otherwise at most once every 2 s for thumbnails. Encode annotated output at 720p q70. | analyzers | saves 5–10 ms CPU/frame and bandwidth to officers |
| 9.6 | **Anomaly: one detector, per-drone state.** Load YOLO11x/11n **once** and share it across the per-drone `AnomalyAnalyzer` instances (trackers/rules stay per drone). | `anomaly/main.py`, `anomaly/analyzer.py` | −2 GB VRAM per extra drone, warm-up once |
| 9.7 | **Intrusion input size.** Test `imgsz` 1280 vs 1920 on recorded footage; keep 1280 if recall drops < 3 %. | `intrusion/cfg/*.yaml` | ~2× faster |
| 9.8 | **ArcFace on GPU.** onnxruntime `CUDAExecutionProvider` with CPU fallback. | `person/face_recognizer.py` | CPU relief in crowded scenes |
| 9.9 | **DB write batching** (only if `measure.py` shows Postgres > 20 % CPU): buffer track upserts for 250 ms and write with `executemany`. | `shared/db.py` | fewer round-trips |
| 9.10 | *(Stretch)* **Cross-drone batching:** run the detector on the latest frame of all 3 drones as one batch of 3 (per-drone trackers unchanged). | analyzers | ~1.3–1.8× GPU efficiency |

**Accuracy guard.** Before/after each item, run the service on the same 2-minute recorded clip and compare detections (count per class, mean confidence, number of confirmed events). Accept only changes within an agreed tolerance (e.g. ±3 % detections, no lost confirmed events).

### ✅ Done when
- [ ] 9.1–9.8 implemented (9.9/9.10 if needed); each with before/after numbers in the calibration log
- [ ] Re-run B4: analyzer lag stays bounded for every enabled service (no growing backlog)
- [ ] Accuracy guard passed for every change kept

**↩ Rollback.** Every item sits behind an env flag (`DVAS_TRT`, `DVAS_FP16`, `DVAS_SAMPLER`, `ANNOTATE_ON_DEMAND`) and can be switched off without a rebuild.

---

## Phase 10 — Adaptive FPS governor, viewer priority & GPU capacity advisor

**Goal (decision D1).**
- (a) Each enabled analyzer **tries** to run at 15–20 fps per drone.
- (b) The analyzer whose annotated feed an officer is watching gets **priority**; others are **throttled**.
- (c) A **capacity advisor** tells officers which analytics combinations the L40S can sustain, before and during a mission.

**Where.** 💻 code (gateway, analyzers, backend, frontend) → ☁️.
**Prerequisites.** Phase 9 (needs the sampler from 9.2).
**Indicative effort.** 5–7 days.

### 10.1 Calibration (measured cost per frame)
`tools/calibrate.py` (run on the pod after any model/engine change; takes ~10 min):
- For each service, loads the model exactly as the service does (`.engine` or `.pt`, FP16 flag).
- Runs 50 warm-up + 300 timed frames from a fixed 1080p sample set.
- Records `gpu_ms_p50/p95` (CUDA events), `cpu_ms` (decode + pre/post-process + annotate), and `vram_mb` (pynvml delta).
- Writes `/workspace/dvas/calibration/model_costs.json` and upserts table `model_calibration(service, variant, gpu_ms_p50, gpu_ms_p95, cpu_ms, vram_mb, measured_at)` in `dvas_dashboard`.

The services then keep the numbers live: every 2 s each analyzer writes an EMA of its real per-frame times to `dvas:svc_stats:<service>` (hash: `gpu_ms`, `cpu_ms`, `fps_actual`, `dropped`, `vram_mb`).

### 10.2 Viewer heartbeat (who is watching what)
- Every annotated-frame proxy route in `backend/routers/analytics.py` (person/vehicle/anpr/crowd/intrusion/vessel/anomaly `…/annotated…`) calls one helper: `SET dvas:viewer:<service>:<drone_id> <user_id> EX 4`.
- Polling at 600 ms keeps the key alive. When the officer leaves the page or the tab is hidden (`useJpegPoller` pauses — Phase 2.7), the key expires within 4 s.
- Pages that show a service without an annotated feed (e.g. the Traffic ledger) send `POST /api/analytics/viewing {service, drone_id}` every 3 s.

### 10.3 The governor (runs in the analytics gateway, every 2 s)

Inputs:
- enabled `(service, drone)` pairs (models page config + active streams)
- `dvas:viewer:*` keys
- recent alerts (`drone:events:confirmed` in the last 30 s)
- `svc_stats` costs
- `nvidia-smi` utilisation and CPU load

Output: `SET dvas:fps_target:<service>:<drone> <fps>` (TTL 10 s; analyzers fall back to `FPS_DEFAULT` if it's missing).

```text
policy (editable in Models page, stored in dvas_dashboard.fps_policy):
  watched_max = 20   watched_min = 15
  alert_fps   = 10   (service+drone with a confirmed event in the last 30 s)
  background  = 5    min_fps[service] = {crowd: 1, anpr: 3, others: 2}
  gpu_budget  = 0.85 × 1000 ms/s      cpu_budget = 0.75 × 16 cores × 1000 ms/s

algorithm (water-filling by priority):
  1. every enabled pair gets min_fps[service]
     if Σ fps·gpu_ms > gpu_budget  → mark lowest-priority background pairs "paused" until it fits; raise OVERLOAD
  2. watched pairs:  raise towards watched_max while budget remains (all watched pairs together, equal share)
  3. alert pairs:    raise towards alert_fps
  4. background:     raise towards background (then towards watched_min if budget is still left)
  5. smoothing:      change any target by at most ±5 fps per cycle; hysteresis 1 fps
  6. feedback:       if measured GPU util > 92 % for 3 cycles → scale all non-watched targets by 0.8
                     if any watched pair's fps_actual < 0.8 × target for 3 cycles → steal from background first
publish: targets + a status document dvas:governor:status (budget used, paused pairs, reasons)
```

With the Section 2.2 estimates (after Phase 9), the result for 3 drones looks like:
- watched service at ~20 fps on all three drones
- 2–3 other services at ~5 fps
- crowd at 1–2 fps
- anything beyond that **paused, with an explanation**

### 10.4 Capacity advisor (API + UI)

**Backend (`routers/infra.py`)**
- `GET /api/models/capacity?drones=3&fps=20&models=person,vehicle,intrusion` →
  ```json
  {"gpu_load_pct": 71, "cpu_load_pct": 38, "vram_gb": 21.4, "fits": true,
   "max_fps_per_model": {"person": 20, "vehicle": 20, "intrusion": 14},
   "recommendation": "All three fit at 15 fps; intrusion limited to 14 fps at 20 fps target.",
   "suggested_sets": [ {"models": ["person","vehicle","intrusion"], "fps": 15},
                       {"models": ["person","vehicle","anomaly"], "fps": 12} ]}
  ```
  `load = Σ_models drones × fps × gpu_ms / 1000` (+ VRAM sum vs 40 GB, CPU likewise).
- `suggested_sets` = greedy knapsack over the officer's preferred order (Models page drag order), maximising the number of models at ≥ 15 fps, then ≥ 10 fps.
- `GET /api/models/governor` → the live `dvas:governor:status` (targets, actual fps, paused pairs, budget).

**Frontend (`ModelsPage.js`, plus a small badge in `CommandShell`)**
- **Capacity meter**: predicted GPU % and VRAM for the current selection and drone count. Green < 70 %, amber 70–90 %, red > 90 %.
- When an officer enables a model that pushes the prediction past 100 %, show the advisor's suggestion and a "Run anyway (lower FPS)" option.
- **Live per-model fps** with the reason ("watched → 20 fps", "background → 5 fps", "paused: GPU budget").
- On each analytics page, show a small "Running at N fps" pill next to the annotated feed.

### 10.5 Tests
- **Unit:** governor allocation with synthetic costs (watched gets priority; nothing exceeds budget; smoothing; hysteresis).
- **Integration (pod, loadtest 3 × 30 fps, all 7 enabled):**
  - Open the Person page → person reaches ≥ 15 fps per drone within 10 s; other services drop.
  - Switch to the Anomaly page → priority moves within 10 s.
  - Close the tab → everything returns to background rates.

### ✅ Done when
- [ ] Calibration table populated on the pod
- [ ] Watched service sustains **≥ 15 fps per drone** (target 20) on all 3 drones while the others keep their minimum
- [ ] Priority follows the officer's page within 10 s
- [ ] Advisor predictions within ±15 % of measured GPU load for 3 tested combinations
- [ ] Models page shows the meter, suggestions and live fps

**↩ Rollback.** `GOVERNOR_ENABLED=false` → analyzers use the static `FPS_DEFAULT` from env.

---

## Phase 11 — 3 × 30 fps load test, tuning & acceptance

**Goal.** Formal acceptance of the initial deployment at the agreed load.
**Where.** 🪟 3 edge streams (real laptops, or 1 laptop + `loadtest_ingest.py` for 2 drones) + ☁️ + 💻.
**Prerequisites.** Phases 9–10, plus the edge teammate's `.exe` for at least one of the three streams (see Phase 7.2 item 6).
**Indicative effort.** 2–3 days.

### 11.1 Test matrix

| Test | Load | Analyzer set | Duration |
|---|---|---|---|
| A1 Soak | 3 × 30 fps, 720p q75 | default set (person, vehicle, crowd) + governor | 2 h |
| A2 Full | 3 × 30 fps | all 7, officer rotates between pages every 5 min | 1 h |
| A3 1080p | 3 × 30 fps, 1080p q70 | default set | 30 min (only if uplink allows) |
| A4 Resilience | 3 × 30 fps | default set | kill an analyzer (`supervisorctl stop analytics:person`) → auto-restart; restart the pod mid-mission; drop edge network 60 s |
| A5 Dashboard | 3 officers logged in, 2 watching annotated feeds | — | during A1 |

### 11.2 Acceptance criteria

| Metric | Target |
|---|---|
| Ingest success (2xx) | ≥ 99 % (429 + 5xx < 1 %) |
| Edge → pod upload latency | p95 < 400 ms (incl. WAN) |
| Watched analyzer | ≥ 15 fps per drone (target 20), lag ≤ 1 s |
| Background analyzers | ≥ their policy minimum; no unbounded lag |
| Alert latency (event → Alerts Feed) | p95 < 3 s |
| GPU | avg util ≤ 90 %, VRAM ≤ 40 GB, zero CUDA OOM |
| CPU / RAM | ≤ 80 % / ≤ 50 GB |
| Volume growth | measured GB/hour → retention keeps the volume < 80 % at the planned test duty cycle |
| Recovery | analyzer crash → back in < 60 s; pod restart → full service in < 5 min; edge reconnect → buffered frames delivered |
| Dashboard | all Phase 6 rows still pass under load |

### 11.3 Tuning knobs (in order of preference)
1. Edge resolution/quality (720p ↔ 1080p, q65–80) — bandwidth and CPU
2. Governor policy (`watched_max`, `background`, `min_fps`) — GPU sharing
3. Model input size per service (`imgsz`) — GPU
4. Enable TensorRT/batching for the services the calibration ranks most expensive
5. Postgres `shared_buffers`/batching if DB CPU is high
6. Valkey `QUEUE_MAX_LENGTH` (300 → 150) if memory is tight

### ✅ Done when
- [ ] A1–A5 executed and every acceptance criterion met (or a signed-off exception)
- [ ] Final image tag recorded as `dvas-runpod:<tag>-accepted`; `runpod-v1` tagged `runpod-test-accepted-<date>`
- [ ] Test report (numbers + graphs from `measure.py` CSVs) saved to the project

---

# BLOCK E — OPERATE & HARDEN

## Phase 12 — Operations runbook

**Goal.** Anyone on the team can start, stop, update, back up and recover the deployment.
**Where.** ☁️ + RunPod console.
**Prerequisites.** Phase 5 (start using it then; finalise after Phase 11).
**Indicative effort.** 1–2 days to write, test and hand over.

**12.1 Daily start / stop**
- **Start of test day:**
  - Start (or deploy from template with the same network volume).
  - `ssh … dvas-ctl status` → all RUNNING; `cat /workspace/dvas/state/bootstrap_ok`.
  - Open the dashboard.
  - If the pod ID changed, send the new URL to the edge operators (`Change Server URL`).
- **End of day:**
  - End all missions on the edge (streams drain).
  - `dvas-ctl backup-now`.
  - Stop the pod. If only Terminate is offered (Phase 4.7), terminate — **all state is on the volume**.
  - GPU billing stops; the volume keeps costing $0.07/GB-month.
- **If no L40S is available** in the DC at start time ("zero GPU"): wait or retry later. A network volume can only be used in its own DC, so moving DC means creating a new volume and restoring from backup (12.3).

**12.2 Deploying a new version**
1. Merge to `runpod-v1` → build with a new immutable tag (Phase 3.6) → local container smoke test → `docker push`.
2. RunPod: edit the template's image tag (or *Pod → Edit → Container image*) → the pod restarts with the new image. The volume, DB and secrets stay.
3. **DB changes:** never edit the baseline dumps after first deploy. Add numbered migration files in `deploy/runpod/db/migrations/NNN_*.sql`; `dvas-bootstrap.sh` applies any not listed in table `deploy_migrations` (one transaction per file).
4. Verify with the Phase 5.3 health block + a short Phase 6 spot check.
5. **Rollback:** set the previous tag. If a migration ran, restore the DB from the pre-deploy backup (`dvas-ctl backup-now` is mandatory before step 2).

**Fast iteration without rebuilds (test phase only).**
- `DVAS_DEV_MODE=1` + `git clone -b runpod-v1 … /workspace/dvas/code` → `git pull` + `dvas-ctl restart analytics`.
- Python code only (no new dependencies); costs ~100 MB of volume.
- Turn it off for acceptance runs.

**12.3 Backups** (`backup-loop.sh`, nightly 02:00 IST + `dvas-ctl backup-now`)
- `pg_dump -Fc` for `keycloak`, `dvas`, `dvas_dashboard` → `/workspace/dvas/backups/<date>/`. Keep **3** nightly sets on the volume (~1–2 GB).
- `dvas.env` + `users.json` copy into the same folder (the folder is `chmod 700`).
- **Off-pod copy weekly** (and before every deploy):
  - `scp -P <port> -r root@<ip>:/workspace/dvas/backups/<date> ./dvas-backups/`
  - or `rclone` to an approved storage target. Encrypt at rest (e.g. `age`/`gpg`) because the dumps contain biometric data.
- MinIO evidence: `mc mirror local/dvas-evidence <offsite>/dvas-evidence` when the test produces evidence that must be kept.
- **Restore drill** (do it once during the test):
  - new volume → deploy → stop programs
  - `pg_restore -d <db> --clean`
  - copy `dvas.env` back
  - start

**12.4 Monitoring & alerts (lightweight)**
- Ingest dashboard (already built): container grid (supervisor), GPU telemetry, queue depth.
- Models page: governor status (Phase 10).
- `dvas-ctl health` and `dvas-ctl lag` for SSH checks.
- Disk guard at 80 % (Phase 5.6), plus a GPU-OOM / restart-loop detector in `backup-loop.sh`:
  - counts supervisord `exited unexpectedly` lines per hour → `system_alert` if > 3.
- Optional: an uptime monitor (e.g. UptimeRobot) on `https://<POD_ID>-80.proxy.runpod.net/healthz`.

**12.5 Database housekeeping**
- Monthly partition creation for `intrusion_events` / `anomaly_events` / `vessel_events` (pg function or SQL file run by `backup-loop.sh` on the 25th for the next month). Check the dumps for existing helper functions.
- Weekly `VACUUM (ANALYZE)` on the high-churn track tables.
- Retention of detection rows: for the test, delete track/event rows older than 30 days (or when `dvas` DB > 5 GB). **Never** touch `audit_logs` (immutable triggers) or evidence/legal-hold tables.

**12.6 Optional: Prometheus + Grafana**
- Add the `prometheus` and `grafana` binaries (copied from their official images) as supervisor programs bound to `127.0.0.1` with `autostart=false`.
- Reach them by SSH tunnel: `ssh -L 3001:127.0.0.1:3001 …`.
- Reuse `dvas_ingest/monitoring/*` configs with targets changed to `127.0.0.1`.
- Keep retention ≤ 3 days (volume size).

**12.7 Log locations.** `/var/log/dvas/<program>.log` (container disk, rotated 50 MB × 3, lost on stop). Copy relevant logs into the backup folder when investigating an incident.

**12.8 Later: stable URL with your own domain.** When the URL changes become painful (or before field trials):
- Run `cloudflared` as a supervisor program with a **named Cloudflare Tunnel** to `http://127.0.0.1:80` → `https://dvas.<your-domain>`.
- Set `PUBLIC_BASE_URL` to that domain (Keycloak `frontendUrl`, CORS, edge `edge.env`).
- The RunPod proxy URL can keep working in parallel.

**12.9 Cost control**
- GPU is billed per second while running — stop it outside test windows.
- The 30 GB volume ≈ $2.10/month.
- Check the RunPod billing page weekly; set a spend alert if available.

### ✅ Done when
- [ ] Start/stop, deploy, rollback and restore procedures each executed once by someone other than the author
- [ ] Nightly backup produced 3 consecutive sets; one off-pod encrypted copy exists
- [ ] Partition and retention jobs verified

---

## Phase 13 — Security hardening

**Goal.** Close the gaps found in the review (and in `DVAS_ADVANCED_SECURITY_AUDIT_…_2026-09-01.md`) before anything beyond a closed test.
**Where.** 💻 + ☁️ + Keycloak.
**Prerequisites.** Phase 6 for the basics; complete before any field trial.
**Indicative effort.** 2–3 days.

| # | Item | Action |
|---|---|---|
| S1 | Committed secrets (F9) | Every secret in the pod is generated fresh (Phase 3/5). Also **rotate** the values that exist in git history on any environment still using them (local stack). Optionally purge history with `git filter-repo` (coordinate with the team — rewrites history). |
| S2 | Exposure | Only `:80` (HTTP proxy) and `:22` (key-only SSH) exposed. Keycloak admin console, MinIO, analytics ports, Postgres and Valkey bound to `127.0.0.1`. Verify with `ss -ltnp` on the pod. |
| S3 | Keycloak | Brute-force detection on; password policy (length ≥ 12, not username); access token 5 min, refresh 30 min; `directAccessGrants` only on the clients that need it; `KEYCLOAK_ADMIN` used only via SSH tunnel; 2FA (OTP) for `system_administrator` and `senior_officer`. |
| S4 | Edge client secret (edge team implements, you create the Keycloak clients/users) | Replace the shared client secret in the installer with either per-laptop **service-account clients** (client-credentials, revocable per laptop) or a **public client + PKCE** for the human login, and per-laptop users for upload. Revoke a laptop by disabling its client/user. |
| S5 | CORS / cookies | Cloud: only `PUBLIC_BASE_URL` + `http://localhost:8765`; `SECURE_COOKIES=true`; no wildcard regex (Phase 2.4). |
| S6 | Issuer/audience | Add `iss` and `azp`/`aud` checks to the JWT verification in backend, ingest, analytics `shared/auth.py` and the edge (today `verify_aud=False` and no issuer check). |
| S7 | Rate limits | nginx per-IP limits on `/ingest` and `/realms`; slowapi limits on `/api/auth/login` (already present) verified behind the proxy (real client IP). |
| S8 | Data protection | `FACE_VECTOR_ENCRYPTION_KEY` held only in `dvas.env` + offline backup; backups encrypted; MinIO buckets private; evidence SHA-256 chain kept; retention documented for biometric data. |
| S9 | RunPod account | 2FA on all team accounts; least-privilege team roles; GHCR read token scoped to the one package; rotate PATs every 90 days. |
| S10 | Edge laptop (edge team) | BitLocker on the SSD holding the buffer/evidence; Windows standard user; signed installer; `REQUIRE_EDGE_AUTH=true`; `EDGE_API_KEY` unique per deployment. |
| S11 | Compliance | Written approval for processing police data outside India for the test (Section 2.4); log where data is stored; plan an Indian-hosted production target if required. |

### ✅ Done when
- [ ] S1–S11 completed or explicitly risk-accepted by the project owner
- [ ] Port scan of the public URL shows only the expected routes
- [ ] Security audit document updated with the RunPod deployment status

---

# Appendices

## Appendix A — Port & route map (inside the pod)

| Port | Program | Bind | Public route |
|---|---|---|---|
| 80 | nginx | 0.0.0.0 | `https://<POD_ID>-80.proxy.runpod.net` |
| 22 | sshd | 0.0.0.0 | TCP mapping (key only) |
| 8000 | ingest | 127.0.0.1 | `/ingest/*` |
| 8001 | backend | 127.0.0.1 | `/api/*` (incl. `/api/ws`) |
| 8080 | keycloak | 127.0.0.1 | `/realms/*`, `/resources/*` only |
| 8100 | analytics-gateway | 127.0.0.1 | — (via backend) |
| 8101–8107 | crowd, person, vehicle, anpr, intrusion, vessel, anomaly | 127.0.0.1 | — (via backend) |
| 5432 | postgres (DBs `keycloak`, `dvas`, `dvas_dashboard`) | 127.0.0.1 | — |
| 6379 | valkey | 127.0.0.1 | — |
| 9000 / 9001 | minio API / console | 127.0.0.1 | — (images via `/api/minio/image`) |

## Appendix B — Environment variables (pod)

| Variable | Set by | Used by | Notes |
|---|---|---|---|
| `PUBLIC_BASE_URL` | init (from `RUNPOD_POD_ID`) or template | Keycloak, CORS, bootstrap | override when a custom domain is used |
| `DVAS_DEPLOYMENT=cloud`, `DVAS_RUNTIME=supervisor` | init | backend | enables cloud code paths |
| `ANALYTICS_DATABASE_URL` → `DATABASE_URL` | init / supervisord | analyzers, gateway | DB `dvas` |
| `DASHBOARD_DATABASE_URL` → `DATABASE_URL` | init / supervisord | backend | DB `dvas_dashboard` (asyncpg URL) |
| `KC_DB_PASSWORD`, `DVAS_DB_PASSWORD` | `dvas.env` | postgres init, keycloak, services | |
| `KEYCLOAK_ADMIN`, `KEYCLOAK_ADMIN_PASSWORD` | `dvas.env` | keycloak, bootstrap | master realm admin |
| `KEYCLOAK_URL`, `KC_REALM`, `KC_CLIENT_ID`, `KC_CLIENT_SECRET` | init / `dvas.env` | backend, ingest, bootstrap | realm `kerala-police-drone` |
| `ADMIN_CLIENT_ID`, `ADMIN_CLIENT_SECRET` | `dvas.env` | backend `keycloak_service.py` | user management |
| `REDIS_URL` | init | all | `redis://127.0.0.1:6379/0` |
| `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_SECURE`, `MINIO_PUBLIC_URL=""` | init / `dvas.env` | minio, analyzers, backend | |
| `ANALYTICS_SERVICE_KEY` | `dvas.env` | backend ↔ analyzers | service auth |
| `FACE_VECTOR_ENCRYPTION_KEY` | `dvas.env` | person, gateway | **back up offline** |
| `DVAS_SERVICE_KEY`, `EDGE_API_KEY`, `JWT_SECRET` | `dvas.env` | backend push, edge | |
| `CORS_ORIGINS`, `ALLOWED_ORIGINS` | init | backend, ingest | public URL + `http://localhost:8765` |
| `*_ANALYTICS_URL`, `*_URL`, `ANALYTICS_GATEWAY_URL` | init | backend, gateway | 127.0.0.1 ports |
| `QUEUE_MAX_LENGTH=300`, `MAX_FRAME_MB=5` | supervisord | ingest | |
| `CROWD_MAX_FPS=2`, `CROWD_ALERT_THRESHOLD=50` | supervisord | crowd | per-drone cap until the governor (Phase 10) takes over |
| `VRAM_OFFLOAD_ON_STOP=false` | init | analyzers | required for TensorRT |
| `DVAS_TRT`, `DVAS_FP16`, `DVAS_SAMPLER`, `ANNOTATE_ON_DEMAND`, `GOVERNOR_ENABLED`, `FPS_DEFAULT` | template / init | analyzers, gateway | Phase 9–10 flags |
| `EDGE_SYNC_HOSTS=""`, `DVAS_PEER_URL=""`, `SECURE_COOKIES=true` | supervisord | backend | |
| `DVAS_DEV_MODE` | template | init | test only |

## Appendix C — New / changed files (summary)

| Path | Phase | Purpose |
|---|---|---|
| `deploy/runpod/Dockerfile`, `Dockerfile.dockerignore` | 3 | all-in-one image |
| `deploy/runpod/etc/{supervisord,nginx,valkey,postgresql,pg_hba}.conf` | 3 | runtime config |
| `deploy/runpod/bin/{dvas-init,dvas-bootstrap,keycloak-start,wait-for,gen-secrets,backup-loop,dvas-ctl}.sh` | 3, 12 | lifecycle |
| `deploy/runpod/requirements/*.txt` | 2–3 | pinned venvs |
| `deploy/runpod/db/*` | 1, 12 | baseline schema + future migrations |
| `deploy/runpod/keycloak/realm-template.json`, `tools/sanitize_realm.py` | 1–2 | realm import |
| `deploy/runpod/tools/{measure,loadtest_ingest,calibrate,build_trt_engines}.py` | 8–10 | performance |
| `deploy/runpod/models.sha256` | 1 | weight integrity |
| `DVAS_Dashboard/backend/core/runtime.py` + changes | 2, 10 | supervisor control, capacity API, viewer heartbeat |
| `DVAS_Dashboard/frontend/src/lib/deployment.js` + changes | 2, 10 | build targets, capacity meter |
| `dvas_analytics/shared/{frames,sampler}.py` + analyzer changes | 9–10 | payload, sampling, FP16/TRT, governor targets |
| `dvas_analytics/gateway/main.py` | 2, 10 | SSE heartbeat, governor |
| `dvas_ingest/ingest/api/*` | 2, 9 | 1 worker, frame_seq, single payload |
| `dvas_edge/edge_api_server.py`, `config/settings.yaml` | 2, 7 | env host/port/data dir, no secrets |
| *(edge `.exe` files are listed in `DVAS_Edge_EXE_Build_Plan.md`)* | — | — |

## Appendix D — Command cheat-sheet

```bash
# Build & push (💻)
docker buildx build -f deploy/runpod/Dockerfile --build-context models=../dvas_models_stage \
  --build-arg BASE_IMAGE=$BASE -t ghcr.io/quantumweaveintelligence/dvas-runpod:$TAG --push .

# On the pod (☁️)
dvas-ctl status | health | lag | disk
dvas-ctl restart analytics            # all analyzers + gateway
dvas-ctl restart backend
dvas-ctl logs person
supervisorctl -c /etc/dvas/supervisord.conf tail -f analytics:anomaly
valkey-cli XINFO GROUPS "mission:frames:$(valkey-cli GET drone:active_mission:DRN-001)"
su postgres -c "psql -d dvas_dashboard"
mc ls local/dvas-evidence
nvidia-smi dmon -s um                 # live GPU util / memory
dvas-ctl backup-now

# Keycloak admin console (💻 tunnel)
ssh -L 8080:127.0.0.1:8080 root@<IP> -p <PORT>   # then http://localhost:8080/admin
```

## Appendix E — Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Field uplink can't carry 3 × 30 fps JPEG | High | High | 720p q75 default; measure in Phase 8; long-term H.264 transport + NVDEC in the pod |
| R2 | All 7 analyzers can't reach 15–20 fps simultaneously on one L40S | Certain (by estimate) | Medium | Viewer-priority governor + advisor (Phase 10); Phase 9 optimisations; larger GPU or a 2nd pod for production |
| R3 | Postgres on the network volume is slow or refuses `chmod/fsync` semantics | Low–Med | High | Verified in Phase 3/5 (local + pod); fallback: pgdata on container disk with WAL archiving + 15-min `pg_dump` to `/workspace` (accepting ≤ 15 min loss) |
| R4 | Pod URL changes on re-create | Med | Med | URL in `edge.env` + `Change Server URL`; Keycloak `frontendUrl` auto-updated; custom domain via Cloudflare Tunnel (12.8) |
| R5 | No L40S available in the volume's DC at start time | Med | Med | Off-pod backups enable restore to another DC; keep start times flexible |
| R6 | Cloudflare 100 s limit breaks long reports/exports or big uploads | Med | Low–Med | Phase 6 timing check; async jobs; chunked uploads ≤ 90 MB |
| R7 | 30 GB volume fills up | Med | High | ILM rules, DB retention, disk guard at 80 %, backups limited to 3 sets |
| R8 | Dependency drift on Python 3.12 (Paddle, ultralytics, rfdetr) | Med | Med | Pinned requirements from today's working versions; CPU Paddle; compileall/import smoke test in the build |
| R9 | Data residency objection | Med | High | Written approval before Phase 4; Indian-hosted production option |
| R10 | Secrets exposed through git history / installer | High (already) | High | Fresh secrets in the pod; rotation; per-laptop credentials (Phase 13) |
| R11 | Out-of-order frames break tracking | Med | Med | `frame_seq` + sampler drops older frames (Phase 9.2) |
| R12 | Single pod = single point of failure | — | Med | Acceptable for the test; production design (HA DB, object storage, 2+ GPU nodes) is a separate plan |

## Appendix F — Phase summary & indicative effort

| Phase | Title | Effort (indicative) | Depends on |
|---|---|---|---|
| 0 | Prerequisites & accounts | 0.5 d | — |
| 1 | Branch & blocker fixes | 1 d | 0 |
| 2 | Cloud-readiness code changes | 3–4 d | 1 |
| 3 | Build image & local test | 3–4 d | 2 |
| 4 | GHCR push & RunPod provisioning | 0.5 d | 3 |
| 5 | First boot & smoke tests | 1 d | 4 |
| 6 | Dashboard go-live | 1–2 d | 5 |
| 7 | Edge integration handoff (the `.exe` itself: edge teammate, separate plan) | 0.5 d | 6 |
| 8 | Baseline E2E test | 1 d | 6, 7 |
| 9 | Performance foundation | 5–8 d | 8 |
| 10 | FPS governor, viewer priority, advisor | 5–7 d | 9 |
| 11 | 3 × 30 fps acceptance | 2–3 d | 7–10 |
| 12 | Operations runbook | 1–2 d | 5 → 11 |
| 13 | Security hardening | 2–3 d | 6 (before field trials) |
| | **Total (RunPod side)** | **≈ 26.5–37.5 working days** for one person. The edge `.exe` runs in parallel on the teammate's plan; the only sync points are Phase 7 (handoff) and Phases 8/11 (testing with the real `.exe` — loadtest drones can stand in until it's ready). | |

*Effort figures are rough planning estimates and will change once Phase 8 baseline numbers are available.*