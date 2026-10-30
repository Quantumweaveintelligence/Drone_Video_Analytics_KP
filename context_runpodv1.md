# context_runpodv1.md — DVAS on RunPod (`runpod-v1`): implementation log, Phases 0–7

> Branch `runpod-v1` · Working copy `/mnt/NewVolume/DVAS_RUNPODV1` · Started 26 Oct 2026 · **Last update 30 Oct 2026, 17:30 IST**
> Plan: `deploy/runpod/docs/DVAS_RunPod_Deployment_Plan.md` (v2, rev 3). Phase guides in `deploy/runpod/docs/`.
> Directives: restored `crowd/main.py` is the base; **RF-DETR not integrated**; `dvas_edge/` is owned by the edge
> teammate (branch `edge-exe-v1`) and isn't edited here.
> Read **§0** for where things stand; §1–§12 are the history, phase by phase.

---

## 0. Current state (30 Oct 2026, 17:30 IST)

### 0.1 Live system
| Item | Value |
|---|---|
| Pod | **`4nrza3dukc231h`**, RunPod Secure Cloud **EU-NL-1**, **1 × NVIDIA L40S 46 GB** (was RTX PRO 6000 until 30 Oct afternoon) |
| Public URL | **https://4nrza3dukc231h-80.proxy.runpod.net** (changes on every pod re-create — 3 IDs on 29–30 Oct) |
| SSH | `ssh root@91.199.227.82 -p 25676` (port changes per pod: Connect → Direct TCP, or pod env `RUNPOD_TCP_PORT_22`) |
| Image | `ghcr.io/quantumweaveintelligence/dvas-runpod:20261030-d0df131` (`sha256:24b5777e…6831`); rollback `20261028-82c7456` |
| Resources | cgroup quota **27.2 vCPU**, memory limit 125 GB (`nproc`/`free` show the host), container disk 50 GB |
| Volume | `dvas-vol-test` 20 GB Standard, MooseFS (`du` ≈ 94 MB). Postgres runs on container disk, base backups + WAL on the volume (timeline 3 = restored 3×) |
| Secrets | on the volume `/workspace/dvas/secrets/{dvas.env,users.json,authorized_keys}`; laptop copy `~/dvas-runpod-secrets/` (0700/0600, `users.json` incl. `edge_laptop_*` since 17:10) |
| Accounts | 6 users from Phase 4 + **`edge_laptop_01..03`** (drone-operator, Phase 7) |
| Health | `smoke_public.py`: 57/57 read-only on this pod; edge checks §12.2 all pass |

### 0.2 Phase status
| Phase | Status | Where |
|---|---|---|
| 0–3 prepare, build | ✅ (Phase 1 extras skipped on purpose: baseline dumps, realm export) | §2–§5 |
| 4 GHCR + RunPod | ✅ (2FA, GHCR private, secrets in password manager: confirm) | §10 |
| 5 first boot, smoke | ✅ incl. persistence across 2 pod re-creates (MinIO marker + DB restore) | §11 |
| 6 dashboard go-live | ✅ all roles, all pages | §11 |
| **7 edge handoff** | ◐ sheet ready, accounts created, checks pass; **waiting**: password-manager entries, frontend PR, first edge frame | §12 |
| 8–13 | not started | plan |

### 0.3 Open items (owner)
1. **Template** (teammate): add `DVAS_VOLUME_GB=20` (disk guard shows `?` today) and decide `DVAS_SEED_DEMO`. Both missing on
   `4nrza3dukc231h`.
2. **Next image** (you, when convenient): carries `c7da60c` (SSH key filter) and the cgroup-v1 CPU-quota fix (§12.3).
   Until then, on this pod: analyzers use 8 threads each (32 OS threads) instead of 3.
3. **Before real data:** rotate the six Phase 4 test passwords (pasted in chat 30 Oct), password manager, data-residency
   approval (EU-NL), production DC/GPU/volume size (50–100 GB).
4. **Security gaps logged, not fixed:** `DELETE /api/evidence/{id}` allowed for any logged-in role (incl. operators);
   ingest `/ingest/incident` and `/ingest/model-suggestion` accept empty bodies (broadcast only, not stored);
   old local client secret still in `dvas_edge/config/settings.yaml` on `testing` (rotate in Phase 13).
5. Known UI gaps (§11.4): disk-guard alert not on the Ingest dashboard; Models page "H100" label/VRAM placeholders.

---

## 1. What I learned (context)

### 1.1 System shape today (local, two compose stacks, 26 containers)
- **Edge laptop** (`dvas_edge`, :8765) → JWT-authenticated `POST /ingest/frame` (multipart JPEG +
  `metadata_json`) → **ingest API** (`dvas_ingest/ingest/api`, :8000) → Valkey stream
  `drone:frames:<drone_id>` (fields `jpeg_b64`, raw `frame`, `metadata` JSON, `frame_sequence`, `ts`).
- **7 analyzers** (`dvas_analytics/<svc>/main.py`, ports 8101–8107) each read their own consumer
  group (`crowd-group` …), write Postgres (`drone_postgres`/db `dvas`, user `dvas`), MinIO
  (9 buckets) and publish `analytics:<svc>:results` + `drone:events:confirmed`.
  **Gateway** (:8100) multiplexes SSE and health; protected by `X-Internal-Service-Key`
  (`ANALYTICS_SERVICE_KEY`).
- **Dashboard backend** (`DVAS_Dashboard/backend`, FastAPI, 12 routers) on its own Postgres
  (`dvas_postgres`/db `dvas`). Creates its tables itself (`seed_service.seed_data` →
  `Base.metadata.create_all`); the audit SQL in `backend/migrations/*.sql`
  (hash-chain columns, **immutability triggers**, legal holds) was applied by hand.
- **Keycloak 24** realm `kerala-police-drone`; KC roles `drone-operator`, `control-room`,
  `supervisor`, `sys-admin` → backend roles `drone_operator`, `control_room_officer`,
  `senior_officer`, `system_administrator` (`core/security.py ROLE_MAP`). The backend logs users in
  with ROPC through `drone-ingest-client`; user management uses the `dvas-admin-service` service account.
  Login maps e-mails to usernames (`admin@dvas.kp→admin_dvas`, `control@…→control_001`, …).
- Frontend: CRA + craco, routes `/edge/*` (operator), `/command/*` (CRO/senior), `/ingest`
  (sysadmin), `/admin`, `/audit`. `useJpegPoller` polls annotated JPEGs every ~600 ms.

### 1.2 Facts that shaped the implementation (verified in code)
| Finding | Where | Consequence |
|---|---|---|
| `FrameMetadata` already has a required `frame_sequence` | `ingest/api/models.py` | Only `capture_ts` was added; `frame_sequence` is republished as `frame_seq` in the stream entry |
| `SKIP_AUTH` is set in compose but never read | `dvas_ingest/docker-compose.yml` | Removed |
| Ingest CORS was effectively `*` (`ALLOWED_ORIGINS` list existed but was unused) | `ingest/api/main.py` | Env-driven list; wildcard kept only when unset |
| Backend reads `SECURE_COOKIES` already | `routers/auth.py` | Only uvicorn `--proxy-headers` + env needed |
| `audit_sync` default host `10.10.55.112`; empty env still looped forever | `services/audit_sync.py` | Early return when `EDGE_SYNC_HOSTS=""` |
| `system_telemetry` imported `docker` at module level and read GPU via `docker exec` | `services/system_telemetry.py` | Lazy Docker; NVML in supervisor mode |
| Container grid lacked vessel/anomaly | `ingest_proxy.py` | Metadata added; shown in supervisor mode |
| **Fresh-DB init was broken**: `003_minio_reid.sql` indexes `vehicle_attributes.created_at`, a column no migration creates | `dvas_analytics/db` | `ADD COLUMN IF NOT EXISTS` before the index (fresh DBs only; existing DBs unaffected) |
| **Partitions stop at 2026-08/2026-10** for `detections`, `confirmed_events`, `crowd_density_readings`, `audit_log`, `intrusion_events`, `vessel_events` | `001`, `007`, `010` | New `012_monthly_partitions.sql` (rolling function + DEFAULT partitions) |
| `001` creates role `dvas_app` with password `dvas_secret` if missing | `001_dvas_schema.sql` | Pod pre-creates `dvas_app NOLOGIN`, so the weak login role is never created |
| `checkpoint_best_total.pth` (anpr/vehicle/vessel, ~400 MB) is referenced by no code | analytics | Excluded from model staging (RF-DETR deferred) |
| `VESSEL_MODEL_TYPE` is read but RF-DETR is not wired into `vessel/main.py` | `vessel/main.py` | Nothing to disable; pod sets `VESSEL_MODEL_TYPE=yolov8` |
| ANPR already uses CPU PaddleOCR (`use_gpu=False`) | `anpr/analyzer.py` | CPU Paddle in its own venv is a no-behaviour-change |
| `FACE_VECTOR_ENCRYPTION_KEY` must be 64 hex chars | `shared/crypto.py` | `gen-secrets.sh` uses `token_hex(32)` |
| `dvas_ingest/.env` `KC_CLIENT_SECRET` is a placeholder, not the real client secret | local `.env` | Local `setup` service does **not** force client secrets (would break login) |
| `dvas_ingest/compose_dump.yml` is a resolved compose dump containing real passwords | repo | Must be untracked (§6) and is now git-ignored |

### 1.3 Restored `crowd/main.py`
Confirmed compile-clean and API-compatible with `crowd/analyzer.py`, `crowd/schema.py`,
`shared/*` and every backend crowd proxy route (same conclusion as plan §1.2). Gaps F19–F21 fixed (§3.4).

---

## 2. Phase 0 — Prerequisites (tooling delivered)

| Item | Delivered | Status |
|---|---|---|
| 0.4 Build host check | `deploy/runpod/tools/check_build_host.sh` (docker, buildx, NVIDIA runtime, ≥80 GB, git, ghcr login) | 🧑 run it |
| 0.6 Record working versions | `deploy/runpod/tools/collect_versions.sh` → `deploy/runpod/versions/freeze_*.txt` + `platform_versions.txt` (MinIO/Keycloak/Valkey/Postgres/torch) | 🧑 run with the local stack up |
| 0.1–0.3, 0.5, 0.7 | RunPod account/2FA/credit/SSH key, DC choice, GHCR PATs, Windows build box, data-residency approval | 🧑 manual (accounts) |

---

## 3. Phase 1 — Branch & blocker fixes

| Plan step | Change |
|---|---|
| 1.1 Branch | Done by you (`runpod-v1`). Tag `pre-runpod-2026-10-26` → §6 |
| 1.2 crowd restore + guard | `deploy/runpod/tools/check_zero_byte_py.sh`, `tools/install_git_hooks.sh` (pre-commit), CI job `.github/workflows/runpod-v1-checks.yml` |
| 1.3 Schema (F2) | `tools/dump_baseline_schema.sh` (schema-only dumps + reference rows with `ON CONFLICT DO NOTHING`) → `deploy/runpod/db/`. `dvas_analytics/db/migrations/` now == `db/`: copied `004_mission_id_text`, `007`, `010_alert_status_fix`, `010_vessel_schema`, `011`; `002` replaced by the superset version from `db/`; fixed `003` (both copies); added `012_monthly_partitions.sql` (both copies). Order is lexical and verified (§5) |
| 1.4 Models (F3) | `tools/stage_models.sh` → `../dvas_models_stage/analytics/**` + `deploy/runpod/models.sha256`; checks all 13 expected weights + vehicle `.pb`; excludes RF-DETR checkpoints |
| 1.5 Realm export | `tools/export_realm.sh` (raw export to `../dvas_secrets_local/`, outside git) + `tools/sanitize_realm.py` (removes users/hashes, masks client secrets, **removes signing-key providers**, masks SMTP/IdP secrets, refuses to write if anything secret-looking remains) |
| 1.6 Skeleton | `deploy/runpod/{Dockerfile,Dockerfile.dockerignore,requirements,etc,bin,tools,db,keycloak,env,tests,README.md}` |
| 1.7 Sensitive files | `.gitignore` += `token.txt`, `**/wireguard/*.conf`, `cookies.txt`, `**/users.local.json`, `**/secrets/users.json`, `dvas_secrets_local/`, `*.engine`, `**/compose_dump.yml`. `git rm --cached` commands in §6 |

---

## 4. Phase 2 — Cloud-readiness code changes

Rule kept: **every change is backward-compatible**; cloud behaviour switches on through
`DVAS_DEPLOYMENT=cloud`, `DVAS_RUNTIME=supervisor` and the other env vars set by `dvas-init.sh`.

### 4.1 Ingest (`dvas_ingest/ingest/api`) — 2.2, F7
- `config.py`: `ALLOWED_ORIGINS` env (comma list); new `CORS_ALLOW_ORIGINS` = that list, or `["*"]` when unset (old behaviour).
- `main.py`: CORS uses `CORS_ALLOW_ORIGINS`.
- `models.py`: optional `capture_ts: float` (epoch s).
- `queue_manager.py`: stream entry also carries `frame_seq` (= `frame_sequence`) and `capture_ts` (for the Phase 9.2 sampler).
- `Dockerfile`: **1 uvicorn worker** (in-process mission/telemetry state), `--proxy-headers`.
- `docker-compose.yml`: removed unused `SKIP_AUTH`.

### 4.2 Dashboard backend — 2.3 / 2.4 (F4, F9, F10, F14, F15)
- **New `core/runtime.py`**: `DVAS_RUNTIME` (`docker` default | `supervisor`), compose-name → supervisord
  program map, XML-RPC client on `unix:///run/supervisor.sock`, state mapping to the UI's
  container shape, `control_programs()` that **never stops/restarts `backend`, `nginx`, `postgres`,
  `keycloak`** (would cut off the control plane), NVML GPU metrics.
- `ingest_proxy.py`: `/api/ingest/containers` and `/api/ingest/control` branch on the runtime. Supervisor
  mode hides services not deployed in the pod (headscale, step-ca, seaweedfs, monitoring, edge),
  includes vessel + anomaly, `core` scope = ingest only (nginx serves the UI in the pod), returns `skipped`.
- `services/system_telemetry.py`: GPU via NVML in supervisor mode; Docker import lazy; disk usage of
  `DVAS_DISK_PATH` (pod: `/workspace`, default `/`).
- `core/config.py`: **no secret defaults** (`KC_CLIENT_SECRET`, `ADMIN_CLIENT_SECRET`, `DVAS_SERVICE_KEY`);
  `DVAS_DEPLOYMENT`/`IS_CLOUD`/`PUBLIC_BASE_URL`; `validate_required_secrets()` → **fails startup in
  cloud mode**, logs an error locally.
- `server.py`: calls the validation; cloud CORS = `PUBLIC_BASE_URL` + `CORS_ORIGINS` + `http://localhost:8765`,
  **no wildcard regex**; local CORS unchanged.
- `services/audit_sync.py`: no-op when `EDGE_SYNC_HOSTS=""`.
- `routers/edge.py`: `/api/edge/system-logs` returns `[]` immediately when `EDGE_API_HOST=""`, timeout 1 s, narrowed exception.
- `routers/analytics.py`: crowd annotated proxy passes `X-Feed-Status` / `X-Stream-Status`.
- `server_monolith_backup.py` (dead backup, not imported): secret default removed; excluded from the image.
- `DVAS_Dashboard/docker-compose.yml`: `KC_CLIENT_SECRET`/`ADMIN_CLIENT_SECRET` now `${…:?}` from
  `DVAS_Dashboard/.env`; `JWT_SECRET`, `DVAS_SERVICE_KEY` from env; edge `INGEST__CLIENT_SECRET` from `KC_CLIENT_SECRET`.
  I appended the previous values to your **git-ignored** local `DVAS_Dashboard/.env`, so the local stack behaves as before.

### 4.3 Analytics — 2.5
- `shared/config.py`: `VRAM_OFFLOAD_ON_STOP` (default `true`), `CROWD_MAX_FPS` (default `2`).
- `person, vehicle, anpr, intrusion, vessel, anomaly /main.py`: `/stream/stop` keeps models on the GPU
  when `VRAM_OFFLOAD_ON_STOP=false` (pod default; required later for TensorRT). Local behaviour unchanged.
- `gateway/main.py`: SSE `: ping` at least every 15 s **even while entries are being filtered out by
  `drone_id`** (the old 3 s keepalive only fired when Valkey was idle) — Cloudflare 100 s cap.
- MinIO: pod sets `MINIO_PUBLIC_URL=""`; browsers use `/api/minio/image`.
- RF-DETR: **not added** (your instruction). `rfdetr_engine.py` untouched; vessel runs YOLOv8.

### 4.4 Crowd service follow-ups — 2.5.1 (F19–F21)
`dvas_analytics/crowd/main.py`, `crowd/analyzer.py`, new `crowd/stream_policy.py`:
1. **Rate cap (F19)**: `FrameRateCap(CROWD_MAX_FPS)` per drone; frames arriving sooner are ACKed **without
   decoding**; only the newest frame of a batch is analysed; `block_ms` 0 → 50. `/health` adds
   `frames_skipped`, `max_fps_per_drone`, `device`.
2. **Heatmap only when needed**: `include_heatmap` only on every 10th processed frame (the only frames uploaded).
3. **Mission awareness (F20)**: `mission_id` parsed from frame metadata → MinIO key folder,
   `confirmed_events.attributes.mission_id`, `publish_confirmed_event` payload, SSE result.
4. **Phase-26 parity (F21)**: `ack_frames` in `finally` (no pending-list pile-up); CUDA OOM/kernel error →
   `to_device("cpu")`; `result is None` → ACK + skip in the loop, **503** on `/analyze/frame`;
   `/annotated/latest` returns `X-Feed-Status`/`X-Stream-Status` (`live` ≤ 6 s) and, when the cache is
   empty, recovers the newest Valkey frame once via `analyzer.render_offline_frame()` (no session-stat
   pollution, re-read at most every 30 s); `/stream/stop` honours `VRAM_OFFLOAD_ON_STOP`.
5. Minor: `/export/csv` uses real timestamps (`timestamp_sec` = seconds since first sample, plus
   `timestamp_utc`); `/density/timeline` honours `hours`; `/density/heatmap/latest` also returns
   `heatmap_object_key` + `heatmap_bucket`.

### 4.5 Frontend — 2.7 (F17)
- New `src/lib/deployment.js`: `REACT_APP_DEPLOYMENT_TARGET` = `cloud` | `edge` | `all` (default).
- `App.js`: `/edge/*` not registered in the cloud build; `/ingest`, `/command`, `/admin`, `/audit` not in the edge build.
- `AuthContext.js`: role not allowed in this build (e.g. `drone_operator` on cloud) → server session ended
  quietly + message (avoids a redirect loop to a missing route); edge mission cleanup skipped on cloud logout.
- `Login.js`: shows the wrong-application message; client-side errors are no longer masked as "Login failed".
- `useJpegPoller.js`: polling **pauses while the tab is hidden** and resumes on visibility (Phase 10 viewer priority).

### 4.6 Keycloak setup — 2.8
`dvas_ingest/ingest/scripts/setup_keycloak.py` (idempotent):
`--client-secret`/`--admin-client-secret` (default from env; applied on create **and** update via PUT),
`--users-file` (JSON; validated; no users in source), `--frontend-url` (realm `frontendUrl`),
admin password from `KEYCLOAK_ADMIN_PASSWORD`, secrets printed masked unless `--show-secrets`.
`users.example.json` added; your local credentials are in the git-ignored `ingest/scripts/users.local.json`
(same users/passwords as the old hard-coded list → local password sync unchanged).
Local compose `setup` service passes `--users-file /scripts/users.local.json` and the admin password only.

### 4.7 Requirements — 2.9
`deploy/runpod/requirements/`: `ai.txt` (6 analyzers; numpy 1.26.4, opencv-python 4.10, ultralytics 8.3.x,
lap, onnxruntime-gpu 1.20.1 for CUDA 12/cuDNN 9, nvidia-ml-py; torch from the base image),
`anpr.txt` (CPU `paddlepaddle==2.6.2` + `paddleocr==2.9.1`, separate venv), `backend.txt` (backend minus dev
tools and Docker SDK, plus `supervisor`, `nvidia-ml-py`), `ingest.txt`, `gateway.txt` (same pins as today).
Ranges are bounded; tighten them with `tools/freeze_image_versions.sh` after the first build (R8).

---

## 5. Phase 3 — All-in-one image (files)

- **`Dockerfile`**: node 20 frontend build (`REACT_APP_DEPLOYMENT_TARGET=cloud`) → binaries from
  Keycloak 24.0.5 / Valkey 7.2 / MinIO / mc images → runtime on `BASE_IMAGE` (RunPod PyTorch 2.8,
  **no default — pass it**). apt: nginx, supervisor, tini, sshd, postgresql-16, JRE 21, ffmpeg, GL libs.
  5 venvs (`pip`, AI venvs use `--system-site-packages` + generated torch constraints with the local
  label stripped). Weights **only** from the `models` build context with `sha256sum -c`; PaddleOCR models
  pre-downloaded; `compileall` smoke test (F16); `nginx -t`; HEALTHCHECK `/healthz`.
- **`Dockerfile.dockerignore`**: weights, `.env`, secrets, edge, docs, research folders, monolith backup.
- **`etc/`**: `supervisord.conf` (18 programs, `analytics` group, per-program `DATABASE_URL` mapping),
  `nginx.conf` (only :80; `/ingest/` 150 r/s/IP burst 300 (F5), `/realms|/resources` only (no `/admin`),
  `/api/` with WS + 3600 s, `/edge-api/` → 404 JSON, SPA; `X-Forwarded-Proto` from the proxy or `$scheme`),
  `valkey.conf` (8 GB, `volatile-lru`, no persistence — F6), `postgresql.conf`, `pg_hba.conf`.
- **`bin/`**: `dvas-init.sh` (volume dirs, secrets — real env wins, all runtime env, `/etc/hosts` aliases,
  key-only SSH, first-boot DB, dev mode), `gen-secrets.sh`, `db-bootstrap.sh`, `db-migrate.sh`
  (once-only, checksum-tracked, runs as role `dvas`), `keycloak-start.sh` (admin console only via SSH
  tunnel: `--hostname-admin-url=http://localhost:8080`), `dvas-bootstrap.sh` (Keycloak setup with the
  current pod URL, 9 private buckets + ILM 3/14 days, evidence & watchlist never expire, pending
  migrations, partition upkeep, `bootstrap_ok`), `backup-loop.sh`, `healthcheck.sh`, `dvas-ctl`, `wait-for.sh`.
- **`tools/`**: `build_image.sh`, `run_local_test.sh` (fake volume, :8088), `freeze_image_versions.sh`.

**Database bootstrap order (pod):** baseline dump if present, then all analytics migrations (idempotent,
verified), reference data; dashboard baseline if present (audit SQL marked as applied) — otherwise the
backend's `create_all` + `audit_v2 → audit_immutability → audit_v3` applied by `dvas-bootstrap.sh`
after the backend is up.

### Verification performed here
| Check | Result |
|---|---|
| `py_compile` of every changed/new Python file | ✅ |
| Fresh **PostgreSQL 16**: `db-bootstrap.sh` → all 13 analytics migrations apply, all tables owned by `dvas`, partitions 2026-10…2027-01 + DEFAULT created, re-run is a no-op | ✅ (found + fixed the `003` bug) |
| Re-applying all migrations on an existing schema (baseline path) | ✅ |
| Dashboard: SQLAlchemy `create_all` + 3 audit migrations; `DELETE FROM audit_logs` blocked by trigger | ✅ tamper-evidence preserved |
| Baseline-dump load path (`load_dump` + `--mark-only`) | ✅ |
| `supervisord.conf` parsed by supervisor (18 programs, env mapping) | ✅ |
| `nginx -t` on `etc/nginx.conf` | ✅ |
| `shellcheck -S warning` on all scripts | ✅ clean |
| Frontend changed files parse (esbuild JSX) | ✅ (full `npm run build` not run here) |
| pytest: `deploy/runpod/tests` (9), `crowd/tests` (5), `backend/tests/test_runtime.py` (7) | ✅ 21 passed |
| Image build / GPU run | ❌ not possible here → 🧑 Phase 3.6 |

---

## 6. Phase 0–3 hand-off (historical, done — current open items in §0.3)

> **Status 27 Oct 2026:** done. Commit `819ef2f` "runpod-v1: phases 0-3 …" is on `origin/runpod-v1` and the CI
> workflow is in `.github/workflows/`. The build-server items below (Phase 0/1 scripts, Phase 3 build)
> are **still open**. They are now steps A1–A6 of the Phase 4 guide (§10).

```bash
cd /mnt/NewVolume/DVAS_RUNPODV1
git switch runpod-v1
git tag pre-runpod-2026-10-26 origin/testing 2>/dev/null; git push origin pre-runpod-2026-10-26   # if not done
# Phase 1.7 — stop tracking sensitive files (they stay in history until Phase 13 rotation)
git rm --cached --ignore-unmatch dvas_edge/token.txt dvas_edge/config/wireguard/wg0-client.conf \
  dvas_ingest/config/wireguard/wg0-server.conf DVAS_Dashboard/cookies.txt dvas_ingest/compose_dump.yml
git add -A && git status        # check: no .env, users.local.json, *.pt/*.pth/*.onnx staged
# scripts must be executable in git (the files were copied in without mode bits)
git update-index --chmod=+x deploy/runpod/bin/* deploy/runpod/tools/*.sh deploy/runpod/tools/*.py
chmod +x deploy/runpod/bin/* deploy/runpod/tools/*.sh deploy/runpod/tools/*.py
git commit -m "runpod-v1: phases 0-3 (cloud-readiness, deploy/runpod image, crowd F19-F21)"
git push -u origin runpod-v1
deploy/runpod/tools/install_git_hooks.sh
```
Tell the edge teammate that `dvas_edge/token.txt` and the WireGuard confs are being untracked
(edge plan E1); `dvas_edge/` itself was **not** modified (cross-boundary rule).

Then, on the Ubuntu build server:
1. Phase 0: `tools/check_build_host.sh`, `tools/collect_versions.sh` (stack up). Put the MinIO/mc tags
   you run into `MINIO_IMAGE`/`MC_IMAGE` (Dockerfile defaults are placeholders to verify with `docker pull`).
2. Phase 1: `tools/dump_baseline_schema.sh` (review partitions/no data), `tools/stage_models.sh` (commit
   `models.sha256`, back up `../dvas_models_stage`), `tools/export_realm.sh` (commit only `realm-template.json`).
3. Phase 2.10 regression: bring both compose stacks up from `runpod-v1` (stop the POLICE_DRONE stacks
   first — same container names) and check login, live feeds, crowd page (now ~2 fps), container grid.
4. Phase 3: find the template image (plan 3.1), then
   `BASE_IMAGE=runpod/pytorch:<tag> deploy/runpod/tools/build_image.sh` → `tools/run_local_test.sh <image>` →
   go through the Phase 3 "Done when" list; `tools/freeze_image_versions.sh <image>` and pin.

## 7. Behaviour changes to be aware of
- **Crowd analytics runs at ≤ 2 fps per drone** by default (`CROWD_MAX_FPS`), also locally. The crowd
  annotated feed refreshes accordingly. Set `CROWD_MAX_FPS=0` to disable the cap.
- **Ingest runs 1 worker** (was 4) — fixes inconsistent telemetry/mission state.
- Local compose **requires** `KC_CLIENT_SECRET`/`ADMIN_CLIENT_SECRET` in `DVAS_Dashboard/.env` (added for you).
- On a brand-new local Keycloak the client secret is now generated by Keycloak (no longer the hard-coded one);
  copy it into `DVAS_Dashboard/.env`.
- `setup_keycloak.py` prints secrets masked (use `--show-secrets`).
- New env vars (all documented in `deploy/runpod/env/dvas.env.example`): `DVAS_DEPLOYMENT`, `DVAS_RUNTIME`,
  `PUBLIC_BASE_URL`, `VRAM_OFFLOAD_ON_STOP`, `CROWD_MAX_FPS`, `ALLOWED_ORIGINS` (ingest), `DVAS_DISK_PATH`,
  `SUPERVISOR_SOCKET`, `REACT_APP_DEPLOYMENT_TARGET`, `KC_USERS_FILE`.

## 8. Open items / risks as of 26 Oct (current list in §0.3)
- **Not run**: image build, `npm run build` of the cloud target, dependency resolution on the base image's
  Python (3.11 on the Ubuntu 22.04 RunPod tag, 3.12 on a 24.04 one). Paddle 2.6.2 has cp311 wheels; the cp312
  fallback is noted in `anpr.txt`.
- ~~Valkey binary copied from the official image~~ → **replaced 27 Oct** (§10.2): `valkey/valkey:7.2` is now
  Debian trixie (glibc 2.41), which can't run on Ubuntu 22.04/24.04. Valkey now comes from the distro (24.04: 7.2.13)
  or falls back to `redis-server` (22.04: 6.0.16; every command DVAS uses was checked and is supported).
- Keycloak **must stay 24.x**: from 25 the management port 9000 collides with MinIO.
- nginx trusts `X-Forwarded-For` from anywhere (`set_real_ip_from 0.0.0.0/0`) — confirm the real header in
  Phase 5.4 (affects only the per-IP rate limit).
- Long-running endpoints (PDF / Section 65B export) vs Cloudflare 100 s — measure in Phase 6.
- `heatmap_b64` is a JPEG stored with a `.png` name / `image/png` type (pre-existing; browsers sniff it).
- Anomaly still loads models per drone (Phase 9.6); crowd still JPEG-encodes every processed frame (9.5).
- Partitions for Sep 2026 don't exist in fresh DBs (only past data would go there; DEFAULT catches it).
- Backups stay on the volume; off-pod encrypted copies are a Phase 12 procedure.
- RF-DETR integration deferred (your instruction); `checkpoint_best_total.pth` files excluded from the image.

## 9. File inventory
**New:** `context_runpodv1.md`, `.github/workflows/runpod-v1-checks.yml`,
`DVAS_Dashboard/backend/core/runtime.py`, `DVAS_Dashboard/backend/tests/test_runtime.py`,
`DVAS_Dashboard/frontend/src/lib/deployment.js`, `dvas_analytics/crowd/stream_policy.py`,
`dvas_analytics/crowd/tests/test_stream_policy.py`, `dvas_analytics/db/012_monthly_partitions.sql`,
`dvas_analytics/db/migrations/{004_mission_id_text,007_intrusion_schema,010_alert_status_fix,010_vessel_schema,011_anomaly_schema,012_monthly_partitions}.sql`,
`dvas_ingest/ingest/scripts/users.example.json`, everything under `deploy/runpod/` except `docs/`.
**New in Phase 4 (27 Oct):** `deploy/runpod/tools/{phase4_preflight,push_ghcr}.sh`, `deploy/runpod/RELEASES.md`,
`deploy/runpod/docs/DVAS_Phase4_GHCR_Push_and_RunPod_Provisioning.md`.
**Modified in Phase 4:** `deploy/runpod/Dockerfile`, `deploy/runpod/tools/build_image.sh`, `deploy/runpod/README.md`,
`.gitignore` (+ `deploy/runpod/.last_build_tag`), this file.
**Modified:** `.gitignore`; backend `core/config.py`, `server.py`, `ingest_proxy.py`, `routers/{analytics,edge}.py`,
`services/{audit_sync,system_telemetry}.py`, `server_monolith_backup.py`; `DVAS_Dashboard/docker-compose.yml`;
frontend `App.js`, `context/AuthContext.js`, `hooks/useJpegPoller.js`, `pages/Login.js`;
analytics `shared/config.py`, `crowd/{main,analyzer}.py`, `{person,vehicle,anpr,intrusion,vessel,anomaly}/main.py`,
`gateway/main.py`, `db/003_minio_reid.sql`, `db/migrations/{002,003}*.sql`;
ingest `api/{config,main,models,queue_manager}.py`, `api/Dockerfile`, `scripts/setup_keycloak.py`,
`dvas_ingest/docker-compose.yml`.
**Local only (git-ignored, not for commit):** `DVAS_Dashboard/.env` (appended block),
`dvas_ingest/ingest/scripts/users.local.json`.

---

## 10. Phase 4 — Push to GHCR & provision RunPod (27 Oct 2026)

Guide for you: **`deploy/runpod/docs/DVAS_Phase4_GHCR_Push_and_RunPod_Provisioning.md`**.
Part A covers build, test and push on the build machine; Part B is the RunPod console, step by step.

### 10.1 State found, and why the push is yours to run
| Checked | Result |
|---|---|
| `runpod-v1` local vs `origin/runpod-v1` | both `819ef2f` (phases 0–3 pushed) ✅ |
| `.github/workflows/runpod-v1-checks.yml` | present ✅ (`deploy/runpod/ci/` is now an empty folder and can be deleted) |
| `deploy/runpod/models.sha256`, `../dvas_models_stage/` | **missing** → the image build stops at the checksum step. Phase 1.4 must run first |
| baseline dumps / `realm-template.json` | missing (optional; fallbacks are tested) |
| built image | none |

I could not do the push from this session:

- no image exists yet;
- building it needs the weights that only exist on your machine, plus about 80 GB of Docker space;
- this session has no shell on your computer, and the cloud workspace's proxy blocks Docker Hub and GHCR (`registry-1.docker.io`, `hub.docker.com` → 403).

The push is therefore a script you run (`tools/push_ghcr.sh`). It reads your PAT from a hidden prompt; no token passes through the chat.

### 10.2 New findings
1. **`valkey/valkey:7.2` now points at Debian trixie** (Docker Hub, updated 27 Oct 2026: `7.2`, `7.2.15`,
   `7.2-trixie`). Its glibc (2.41) is newer than Ubuntu 24.04's (2.39) and 22.04's (2.35), so the binary the Phase 3
   Dockerfile copied would fail to start. **Fixed:** Valkey now comes from apt.
   - Ubuntu 24.04 ships `valkey-server 7.2.13`.
   - Otherwise the Dockerfile falls back to `redis-server` and links it as `valkey-server`/`valkey-cli`.
   - Verified on Ubuntu 24.04: our `valkey.conf` starts, replies `PONG` and applies `maxmemory-policy volatile-lru`.
   - Redis 6.0 (22.04) supports every command the code uses (streams, consumer groups, `xinfo_stream`, `SET`); grep-checked.
2. **The RunPod PyTorch 2.8 image on Docker Hub is Ubuntu 22.04 / Python 3.11.**
   - The tag is `runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04`: amd64, ~10.0 GB compressed, digest `sha256:cb154fcca15d…d853`, pushed Mar 2025.
   - The plan assumed Ubuntu 24.04 / Python 3.12. On 22.04, `postgresql-16` isn't in the distro and `/usr/bin/python3` may not be the interpreter that has torch.
   - **Fixed:** the Dockerfile now works on both:
     - auto-detects the interpreter that has torch (or takes `PY`)
     - adds the apt.postgresql.org repo when the distro lacks PostgreSQL 16
     - uses Java 21, or 17 if 21 isn't packaged
     - installs `pythonX.Y-venv` if `venv` is missing
     - records the choices in `/opt/dvas-os-packages.txt`
   - The OS-package step was executed end-to-end on Ubuntu 24.04: PostgreSQL 16.15, OpenJDK 21, Valkey 7.2.13. The 22.04 path was not run here.
3. **Image size.** The base alone is ~10 GB compressed (~20 GB unpacked), so the final image will likely be
   ~25–30 GB uncompressed and ~12–15 GB to upload. Plan targets: "≤ 25 GB" and a 50 GB container disk. If the pull
   fails on disk, use an 80 GB container disk.
4. **RunPod console (docs.runpod.io, 27 Oct 2026)** differs from the plan text:
   - Registry credentials and SSH keys live on the **Credentials** page, under the **Container Registry Auth** and **SSH Public Keys** tabs (not "Settings").
   - A network volume can only be chosen **at deploy time** and only on Secure Cloud. There are Standard ($0.07/GB-month) and High-Performance tiers.
   - Changing a pod's environment variables restarts it (harmless: all state is on `/workspace`).
   - "Running" doesn't mean ready.
   - RunPod injects `RUNPOD_POD_ID`, `PUBLIC_KEY`, `RUNPOD_PUBLIC_IP`, `RUNPOD_TCP_PORT_22`; `dvas-init.sh` already uses the first two.
   - Docs say a stopped pod with a network volume keeps its data, but a restart can fail with "Zero GPU Pods".
5. **Build-machine notes.**
   - `/mnt/NewVolume` looks like an NTFS drive and git runs with `fileMode=false`. Keep the repo there if you like, but Docker's data root must be on ext4/xfs, and the executable bits come from `chmod`/`git update-index --chmod=+x`, not from the file system.
   - `.git/config` uses `credential.helper=store`, so the GitHub token sits in plain text in `~/.git-credentials`. Consider `libsecret`/`cache` (plan S9).
6. **Buildx attestations.** `build_image.sh` now passes `--provenance=false --sbom=false --platform linux/amd64`, so GHCR receives a plain single-platform manifest (what RunPod pulls). Image labels record the source repo (links the GHCR package to the repo), the commit and the base image.

### 10.3 Changes made (Phase 4)
| File | Change |
|---|---|
| `deploy/runpod/Dockerfile` | removed the Valkey image stage; runtime stage handles Ubuntu 22.04 **and** 24.04 (PostgreSQL 16 via PGDG fallback, Java 21→17, Valkey→Redis fallback, Python auto-detect via `PY`/`PY_CANDIDATES`, venv package fix); OCI labels (`source`, `revision`, `created`, `dvas.base-image`); `SHELL bash -o pipefail`; extra build-time version checks |
| `deploy/runpod/tools/build_image.sh` | amd64, no provenance/SBOM, `GIT_SHA`/`BUILD_DATE` labels, `PY`/`KEYCLOAK_IMAGE` pass-through, refuses `latest`, warns on uncommitted changes, writes `deploy/runpod/.last_build_tag` |
| `deploy/runpod/tools/phase4_preflight.sh` (new) | branch/clean/pushed, zero-byte check, **weights vs `models.sha256`**, optional Phase 1 artefacts, Docker daemon/buildx, Docker root not NTFS + ≥ 80 GB, every source image pullable (base, Keycloak, MinIO, mc, node), ghcr login |
| `deploy/runpod/tools/push_ghcr.sh` (new) | login via hidden prompt / `GHCR_TOKEN` + `--password-stdin`, push, remote digest via `buildx imagetools`, appends tag/digest/commit/base/size to `RELEASES.md` |
| `deploy/runpod/RELEASES.md` (new) | release log = rollback reference |
| `deploy/runpod/docs/DVAS_Phase4_GHCR_Push_and_RunPod_Provisioning.md` (new) | the Phase 4 guide (A: build/test/push, B: RunPod console, C: troubleshooting, D: what to send back) |
| `deploy/runpod/README.md`, `.gitignore` | Phase 4 tools listed; `.last_build_tag` ignored |

Verification: `bash -n` + `shellcheck -S warning` clean on the new/changed scripts; OS-package step run on
Ubuntu 24.04 (above). Not run: a full image build (no registry access / weights here).

### 10.4 Next (🧑), in order
1. `git pull` on the build machine, `chmod +x deploy/runpod/bin/* deploy/runpod/tools/*`, commit these Phase 4 files.
2. Guide A1–A7: choose `BASE_IMAGE` → `stage_models.sh` (commit `models.sha256`) → `phase4_preflight.sh` →
   `build_image.sh` → `run_local_test.sh` → `push_ghcr.sh` → commit `RELEASES.md`.
3. Guide B1–B10 in the RunPod console. Use a **separate read-only (`read:packages`) PAT** for the `ghcr-dvas` credential.
4. Send the Part D items (tag/digest, pod ID, data centre, vCPU/RAM, `dvas-ctl status`, `/opt/dvas-os-packages.txt`,
   bootstrap log tail, stop-vs-terminate answer). Then Phase 5 (first boot, bootstrap & smoke tests) starts.

### 10.5 Provisioning record (as of Phase 4; current values in §0.1)
| Item | Value |
|---|---|
| Image tag / digest | `20261028-82c7456` / `sha256:5f75353d3b58c73cdcdfa2621c9f7038b630d60a054324261d87c31f910f6b57` (commit `82c7456`) |
| Base image used | `runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04` |
| Data centre / volume | EU-NL-1 · network volume `dvas-vol-test` (`jtvh8it04e`), 20 GB, Standard ($1.40/mo), MooseFS/FUSE |
| GPU | 1 × RTX PRO 6000 (L40S not available in EU-NL-1 at deploy time) |
| Pod ID / proxy URL | `o5vg7007ar5zk9` · https://o5vg7007ar5zk9-80.proxy.runpod.net |
| vCPU / RAM / container disk | cgroup quota 27.2 vCPU (`cpu.max` / `cpu.cfs_quota_us`), memory limit 251 GB (RTX host) / 125 GB (L40S host) · container disk 50 GB |
| Stop offered with a network volume? URL kept after Stop/Start? | Re-create on the same volume observed twice (template changes): new pod ID + URL + SSH port each time, data restored (timelines 2, 3). Explicit Stop → Start still untested |

### 10.6 Build machine = your laptop (27 Oct 2026, later)
- You build Part A yourself on `user-Nitro-AN515-57` (Ubuntu). Docker root is on `/` (`/dev/nvme0n1p6`, 108 GB, **47 GB free**).
  Docker holds 0 images and 0 build cache. The 23 volumes (5.3 GB) are the old local compose data: **never prune them**.
- Disk budget with the classic `overlay2` store and the default `docker` builder: base ~20 GB unpacked + DVAS layers
  ~8–10 GB + frontend stage/cache ~3–5 GB ≈ **35 GB peak**, so 47 GB is enough. The containerd image store would add
  ~10 GB, so the guide (A2b) switches to `overlay2` (safe now, with 0 images).
- The Phase 1 extras (`dump_baseline_schema.sh`, `export_realm.sh`, `collect_versions.sh`) need the local stacks
  running, which would mean rebuilding ~30 GB of images. **Skipped on purpose**; the tested fallbacks are used.
- Tooling changed: `phase4_preflight.sh` now fails below 40 GB and warns below 60 GB (was a hard 80). It also checks the
  image-store driver and the buildx builder driver. `check_build_host.sh` needs ≥ 40 GB.
- Guide Part A rewritten as A1–A10, each step with expected output:
  1. code + commit
  2. Docker prep
  3. pull the base image
  4. stage weights
  5. skip the extras
  6. preflight + build
  7. local test
  8. push
  9. GitHub package check
  10. free disk

  Part C gained build-failure rows.

### 10.7 Preflight result on the laptop (27 Oct 2026) and the fix
- Preflight: weights ✅ (15 files match `models.sha256`), base image, Keycloak 24.0.5 and node:20 pullable ✅, ghcr login ✅.
- **FAIL:** the Dockerfile defaults `minio/minio:RELEASE.2025-04-22T22-12-26Z` and `minio/mc:RELEASE.2025-04-16T18-13-26Z`
  are not pullable from Docker Hub. MinIO stopped publishing community images in 2025, so old tags may be gone.
- **WARN:** image store is the containerd snapshotter (`overlayfs`) → switch to `overlay2` (guide A2b, now with an
  image/cache cleanup first); uncommitted changes → commit before building.
- **New `deploy/runpod/tools/resolve_minio_images.sh [--write]`:** finds pullable server and client images and pins
  them by digest (`repo:tag@sha256:…`) in the Dockerfile `ARG MINIO_IMAGE` / `ARG MC_IMAGE`; commit the change. It tries,
  in order: `$MINIO_IMAGE`/`$MC_IMAGE`, the Dockerfile default, the same tag on `quay.io/minio/*`, then `:latest` on
  Docker Hub, then `:latest` on quay.io. Logic tested with a stubbed docker; shellcheck clean.
- The guide's A6 no longer asks you to type a tag (the literal `<tag>` caused `bash: syntax error near unexpected token`).
  Part A also now says how placeholders work.

### 10.8 Resolver found no MinIO image at all → MinIO is built from source (27 Oct 2026, later)
- The resolver's run on the laptop marked **every** candidate not pullable, `:latest` included. Two causes:
  1. **My bug:** the digest was parsed from `imagetools inspect --format '{{json .Manifest}}'`, which doesn't work for
     a multi-arch index, so even good images could be skipped. Now the plain `Digest:` line is parsed, and the
     reason for each failure is printed (`reason: …`). `push_ghcr.sh` had the same parse; fixed the same way.
  2. MinIO's community images are no longer maintained (2025), so relying on them is fragile anyway.
- **Dockerfile:** new `ARG MINIO_FROM=source|image` (default **source**). Stages `minio-source` / `mc-source` clone
  `github.com/minio/{minio,mc}` at `ARG MINIO_GIT_TAG` / `MC_GIT_TAG` and `go build` static binaries (`CGO_ENABLED=0`,
  `GOTOOLCHAIN=auto`) on `ARG GO_IMAGE=golang:1-bookworm`; `FROM minio-${MINIO_FROM} AS minio` picks the branch.
  The final `COPY --from=minio/mc` lines are unchanged. Defaults: minio `RELEASE.2025-10-15T17-29-55Z`,
  mc `RELEASE.2025-08-13T08-35-41Z` (newest tags on GitHub, checked with `git ls-remote`).
- BuildKit resolves the image of every `FROM`, even unused ones, so in source mode `MINIO_IMAGE` / `MC_IMAGE` point
  at the Go image instead of a dead MinIO tag.
- **`resolve_minio_images.sh`** now: image mode (override → Dockerfile default → quay → newest release tags on Hub/quay
  → latest) if a server+client pair is pullable; otherwise source mode with the newest GitHub tags. `--source` forces it.
  `--write` records the result in the Dockerfile ARGs. **`phase4_preflight.sh`** checks the Go image and both git tags
  in source mode, the images in image mode.
- Not testable from my workspace (no Docker Hub / Go proxy access): the Go compile itself. Git tags verified; scripts
  tested with a stubbed docker, shellcheck clean. If `minio-source` fails, the log tail is what I need.
- Guide A6 and Part C updated to match.

### 10.9 First build attempt and the clean restart (27 Oct 2026, 10:34–10:45)
- You ran the resolver, commit, preflight and build in one paste. Commit `runpod-v1: MinIO via source build, phase 4
  fixes` landed (10:34) and the Dockerfile matches mine (`MINIO_FROM=source`), but the build didn't finish (no
  `.last_build_tag`) and free disk fell from 47 GB to 23 GB — the base image (~20 GB unpacked) plus partial layers.
- **New `deploy/runpod/tools/docker_disk_report.sh`** (read-only): free space, running builds, image-store driver,
  `daemon.json`, builder, `docker system df`, images, `/var/lib/docker` + `/var/lib/containerd` sizes, leftover env
  vars in the shell and `~/.bashrc`, MinIO ARGs, git state, and the tail/errors of the newest build log. Saves to
  `deploy/runpod/.logs/disk-report-<time>.txt`.
- **`build_image.sh`** now logs itself to `deploy/runpod/.logs/build-<time>.log` and refuses to start when free space
  is under ~18 GB (base image local) / ~38 GB (base must be pulled); `FORCE_LOW_DISK=1` overrides.
- `deploy/runpod/.logs/` is git-ignored and docker-ignored. It's inside the connected folder, so I can read logs and
  reports directly — no pasting needed.
- **Guide:** new **A0 (clean restart)**: Ctrl+C + fresh terminal, move the old log, disk report, a cleanup decision
  table (overlay2 + base present → `docker builder prune -af` only; overlayfs → A2b; hidden old-store data → toggle
  the containerd snapshotter on, prune, off), safe extras (`container prune`, `apt clean`, journal vacuum, pip cache),
  and a never-delete list (volumes, `dvas_models_stage`, repo). Also run Part A one block at a time. §0 and the A6
  build command were updated, and Part C has a row for the disk guard.
- **Disk report 16:20 + attempt-1 log (read from `deploy/runpod/.logs/`):** no errors. The build was stopped during
  the apt step (`stage-8 3/20`). Already done: **MinIO source build ✅** (`minio DEVELOPMENT.2025-10-15T17-29-55Z`,
  go1.27.3), **mc ✅** (`DEVELOPMENT.2025-08-13T08-35-41Z`), frontend `npm run build` ✅ (CRA "compiled with warnings"
  only). State: overlay2 ✅, builder `default`/docker ✅, base image 17.9 GB local, build cache 3 GB (keep it: it holds
  those stages), 26 GB free (guard needs 18). `/var/lib/containerd` 3.9 GB is probably left over from the old store;
  optional A0 d), not needed now. `BASE_IMAGE` was not in `~/.bashrc` → export it again in each new terminal.
- **Preflight disk rule aligned (16:40):** it still demanded ≥40 GB and FAILed at 26 GB with the base image local.
  Now it uses the same rule as `build_image.sh`: needs ~18 GB if the base image is local, ~38 GB if not
  (OK at +15 GB, WARN in between).
- **Build attempt 2 (27 Oct, ~11:10) failed** in `stage-8 10/20` (venvs): `onnxruntime-gpu==1.20.1` isn't on PyPI any
  more (available: 1.20.0, 1.20.2, …). Pinned **1.20.2** (same CUDA 12 / cuDNN 9 line). All five requirements files
  were then dry-run resolved here for cp311/manylinux, binary-only, with torch 2.8.0 / torchvision 0.23.0 constraints:
  all resolve (ai: numpy 1.26.4, ultralytics 8.3.253, opencv 4.10.0.84; anpr: paddlepaddle 2.6.2, paddleocr 2.9.1).
- **Build 3 (27 Oct 16:37) SUCCEEDED:** `ghcr.io/quantumweaveintelligence/dvas-runpod:20261027-e5d7ef3` (= HEAD
  `e5d7ef3`, the onnxruntime commit), 23.9 GB, 20 GB disk left. Checks inside the build passed: OS packages
  (`Ubuntu 22.04.5`, `openjdk-21`, `redis-server` as Valkey), venvs (~16.5 min), `torch 2.8.0 … cuda 12.8`, onnxruntime
  providers `Tensorrt/CUDA/CPU`, weights `sha256sum -c` ✅, PaddleOCR model pre-download ✅, `nginx -t` ✅.
- **Notes / risks from the log (not blocking):**
  - The RunPod base ships a **nightly torch `2.8.0.dev20250319+cu128`** and `torchvision 0.22.0.dev20250319`, not the
    2.8.0 release. The constraint file pins exactly that, so the venvs are consistent. Watch for torch-version-
    specific bugs in A7; switching to a release-torch base is a Phase 5+ option.
  - `nginx -t` warns `4096 worker_connections exceed open file resource limit: 1024` — build-time only; supervisord
    sets `minfds=65536` at runtime.
- Next: A2d (NVIDIA runtime) → A7 local test → A8 push → A9 package private → Part B.
- **A7 first boot (27 Oct 17:06) looks clean:** secrets generated, PG16 cluster + 13 analytics migrations applied,
  supervisord started all 18 programs, bootstrap exited 0 within 25 s, container `healthy`. `CREATE EXTENSION vector`
  fails (pgvector isn't installed in the image) and `003_minio_reid.sql` falls back to `FLOAT4[]` as designed. The code
  stores embeddings encrypted (`encrypt_vector` → bytea) and matches in Python, so no pgvector operator is used. An
  optional later improvement: `postgresql-16-pgvector` from PGDG.
- **A7 restart test found a real bug (27 Oct 17:21):** after `docker restart` the backend went FATAL (exit 3):
  `seed_data()` re-seeded the demo data → `duplicate key … missions_pkey (M-101)`. Its "already seeded" guard only
  counted **User** rows, but seeding never creates users, so the guard never fired. Worse: before crashing, each
  restart ran `DELETE` on drones, alerts, **evidence** and exports and committed. On a pod that would wipe live
  evidence on every restart. (Locally it never showed because the old DB had user rows.)
  **Fix — `DVAS_Dashboard/backend/services/seed_service.py`:** seed only when users, missions, drones, alerts, evidence
  and exports are all empty; the startup deletes are removed. New `DVAS_SEED_DEMO=0` leaves an empty DB empty (no demo
  missions/evidence/fake audit chain). The default stays `1` (unchanged behaviour). Tested here on PostgreSQL 16:
  two runs → same counts (3 missions, 4 drones, 14 evidence, 4 exports, 25 audit); with `DVAS_SEED_DEMO=0` → all zero.
  → **Decision for you before go-live:** set `DVAS_SEED_DEMO=0` in the RunPod template so the production pod doesn't
  get demo missions, fake evidence and a seeded audit chain.
- Login in A7 worked once the right `users.json` password was used (the first "Invalid email or password" was a copy
  mistake). `tools/diagnose_login.sh` was added for future login problems.
- Guide A7 restart step: `dvas-ctl` through `docker exec` (it exists only in the container).
- **Pushed (27 Oct 12:41Z):** `ghcr.io/quantumweaveintelligence/dvas-runpod:20261027-ed8b02c`
  `sha256:c8d478a74a26d1c7a65ac2b602fd100e2975821ee0f0a6c58107e86363ebfffc` (commit `ed8b02c`, seed fix), 23.9 GB.
- **Part B is done by a teammate** who owns the RunPod account. Guide B0 added: hand-off table (image line,
  `adhishQWI` + read-only PAT shared via password manager, `DVAS_SEED_DEMO` decision, public SSH keys only; each person
  uses their own key). B2 username = the PAT owner.
- **First RunPod boot (28 Oct, pod on EU-NL-1, volume `dvas-vol-test` 20 GB) crash-looped every ~18 s:**
  `chown: changing ownership of '/workspace/dvas/pgdata': Operation not permitted`. The RunPod network volume refuses
  `chown`, even for root, and `dvas-init.sh` (set -e) exited before anything started. Postgres needs a data directory
  owned by `postgres` with mode 0700. Also seen: `tini is not running as PID 1` (RunPod wraps the entrypoint), so
  zombies weren't reaped.
  **Fix:** `dvas-init.sh` `prepare_pgdata()`: chown if possible; otherwise remove the empty root-owned leftover,
  briefly open `/workspace/dvas` (1777), let the **postgres user create `pgdata` itself** (so it owns it), chmod
  700, and restore the parent mode. It verifies owner and mode. If the volume still can't provide that, it logs a
  probe (mount line, `ls -ld`, filesystem type), starts **sshd only** and sleeps instead of crash-looping, so the pod
  can be inspected. `gen-secrets.sh`: chmod failures are non-fatal (umask 077 already). Dockerfile:
  `tini -s` (subreaper). Tested here with a chown-refusing stub and with a normal filesystem; pytest 9 passed.
  Not testable here: RunPod's real network filesystem. If it also maps ownership to root, the fallback is
  needed (Postgres on container disk plus dump/restore to the volume) — the probe lines will show that.
- **RunPod boot 2 (image `20261028-4d287fd`, RTX PRO 6000, EU-NL-1):** the probe showed
  `mfs#eu-nl-1.runpod.net:9421 on /workspace type fuse (… user_id=0,group_id=0,allow_other)`. The network volume is
  **MooseFS over FUSE**: every file shows `root:root 0777`, and chown/chmod do nothing, even on files the postgres
  user creates. Postgres can't keep its data directory there at all (it needs postgres-owned 0700 and refuses root).
  (An earlier boot that showed RunPod's Jupyter start script was a wrong template/start command, not our image.)
- **Fix — Postgres on the container disk, made durable on the volume (`bin/pg-volume.sh`, new):**
  - live cluster `/var/lib/postgresql/dvas` (container disk, fast). On the volume: `/workspace/dvas/pg/base/<time>/`
    (pg_basebackup tar.gz + START_WAL, keep 2) and `/workspace/dvas/pg/wal/` (continuous WAL archive, gzip).
  - `prepare` (dvas-init step 5): local cluster present → use it (container restart). Else, with a base backup
    on the volume → restore it + replay archived WAL (`recovery_target_timeline=latest`) → stop. Else → initdb,
    and dvas-init runs db-bootstrap + the first base backup. A WAL archive without a base is moved aside.
  - `run` (supervisord `postgres`, user postgres): starts postgres. A flusher forces `pg_switch_wal()` every
    `PG_WAL_FLUSH_SECS` (default 120 s) when rows changed (`pg_stat_database` tup counters). On TERM it switches
    WAL, waits ≤30 s for the segment in the archive, then fast-stops. `archive_timeout` did **not** fire in testing
    (PG16 here), so it's 0, with `wal_recycle=off` so forced switches gzip to a few KB.
  - `basebackup`: dvas-init first boot, dvas-bootstrap when flagged after a restore or none in 24 h, backup-loop
    nightly. Prunes to 2 and `pg_archivecleanup -x .gz`.
  - postgresql.conf: `wal_level=replica`, `archive_mode=on`, archive_command → pg-volume.sh. pg_hba: `local replication
    postgres peer`. supervisord: `pg-volume.sh run`, stopsignal TERM. dvas-ctl disk updated.
  - **Tested here on PostgreSQL 16:** first boot + base backup; clean stop → wipe local → restore (1500/1500 rows); +500
    rows → hard SIGKILL after the flush interval → restore (2000/2000, timeline 3); new bases + prune; restore from
    the newest base (2000); container restart reuses the local cluster. WAL archive for all of it: ~170 KB.
  - Data-loss bound: clean pod stop → none; hard kill → at most `PG_WAL_FLUSH_SECS` (120 s) of DB writes. MinIO
    objects go straight to the volume.
  - Still untested on RunPod: MinIO on MooseFS, and the full image on Blackwell (RTX PRO 6000).
- **Build cache bug fixed (28 Oct):** `ARG GIT_SHA` / `ARG BUILD_DATE` (+ LABEL) sat at the top of the final stage, so
  every commit changed every later RUN's cache key: full rebuild each time (~20 min, ~11 GB of new layers; seen as
  "2 CACHED" in build-20261028-131927.log). Moved to the end of the Dockerfile. The next build is a full rebuild
  once; later builds reuse the cache. One-time cleanup: `docker builder prune -af` + remove the old local tags
  (all pushed). Keep the `runpod/pytorch` base image.


### 10.10 First successful RunPod deployment (29 Oct 2026, 19:09 IST) — from `logs_29_10.txt`
**Image:** `ghcr.io/quantumweaveintelligence/dvas-runpod:20261028-82c7456` (pushed 29 Oct 06:56Z; it carries the
Postgres-on-container-disk + volume WAL archive design and the cache-friendly Dockerfile). The RunPod account owner
(teammate) deployed it on pod `o5vg7007ar5zk9`, RTX PRO 6000, EU-NL-1, with volume `dvas-vol-test`.

**First-boot timeline (all clean, ~60 s to bootstrap done):**
| Time (IST) | Event |
|---|---|
| 19:09:50 | `gen-secrets` wrote `/workspace/dvas/secrets/dvas.env` + `users.json` (new random secrets for this volume) |
| 19:09:50 | `pg-volume: no base backup on the volume — creating a new cluster in /var/lib/postgresql/dvas` |
| 19:09:51 | `first boot: creating roles, databases and schema`. PostgreSQL 16.15 (PGDG, jammy) on the unix socket |
| 19:09:52–54 | 13 analytics migrations applied (001…012). `vector` extension missing → `003_minio_reid.sql` falls back to `FLOAT4[]` (expected; embeddings are stored encrypted and matched in Python) |
| 19:09:54 | `database bootstrap complete`. Dashboard tables are created by the backend at start |
| 19:09:57 | `base backup complete (8.4M, start WAL 000000010000000000000002)` → `/workspace/dvas/pg/base/20261029-190954`. **The first restore point is on the network volume**, so archiving to MooseFS works |
| 19:09:57 | `starting supervisord (public URL: https://o5vg7007ar5zk9-80.proxy.runpod.net)` |
| 19:09:59–19:10:29 | all 18 programs RUNNING: sshd, minio, postgres (via `pg-volume.sh run`), valkey, nginx, backup, ingest, gateway, backend, crowd, person, vehicle, anpr, intrusion, vessel, anomaly, keycloak |
| 19:10:51 | `bootstrap` exited 0 → Keycloak realm/clients/roles/users, MinIO buckets + expiry rules, migrations, partitions done |

**What this proves on RunPod:** the MooseFS volume works for secrets, base backups and the WAL archive. MinIO
starts on it (buckets were created, since bootstrap only exits 0 after `mc mb` and the ILM rules). The image runs
on Blackwell (RTX PRO 6000): every analyzer process stayed up past its 20 s startsecs (inference not exercised yet).
The template is correct (our entrypoint ran, not RunPod's `/start.sh`).

**Logins:** the dashboard users are `admin@dvas.kp` (sys-admin), `control@dvas.kp` (control room), `senior@dvas.kp`
(supervisor), `operator@dvas.kp`, `operator_002@kerala.police.in`, `operator_003@kerala.police.in` (drone operators).
Passwords are random per volume and never logged. Read them inside the pod (web terminal or SSH):
`python3 -c "import json; [print(f\"{u['email']:32} {u['password']}\") for u in json.load(open('/workspace/dvas/secrets/users.json'))]"`.
Store `users.json` + `dvas.env` in the team password manager (B9).

**Release history (`RELEASES.md`):** `20261027-ed8b02c` (seed fix) → `20261028-4d287fd` (chown attempt; FATAL on
MooseFS) → **`20261028-82c7456` (deployed)**. Commit `d1460f4` (Postgres on container disk) was never built on its own; 82c7456 contains it plus the
Dockerfile ARG move was added on top.

**Still to do (Part D / Phase 4 "done when"):**
1. In the pod: `dvas-ctl status`, `dvas-ctl health`, `/opt/dvas/deploy/bin/pg-volume.sh status`,
   `cat /opt/dvas-os-packages.txt`, `df -h / /workspace`, `nvidia-smi` → record vCPU/RAM/container disk in §10.5.
2. Log in on the proxy URL as `control@dvas.kp`. Check the dashboard loads and SSE/streams connect.
3. B10 stop-vs-terminate test: Stop → Start the pod. Expect `pg-volume: restoring … replaying archived WAL` →
   `recovered to …`, no `first boot` line, same passwords, data intact. Record whether the proxy URL stayed.
4. Decide `DVAS_SEED_DEMO` for the production volume (only takes effect on an empty DB).
5. Later: move to L40S (or keep the RTX PRO 6000) when choosing the production data centre (closer to Kerala); grow
   the volume for production (50–100 GB; evidence never expires); optionally add `postgresql-16-pgvector`.


### 10.11 Pod checks, 30 Oct 2026 11:14 IST (Part D outputs)
- `dvas-ctl status`: all 18 programs RUNNING, `bootstrap EXITED` (11:11). `dvas-ctl health`: every endpoint OK
  (nginx, ingest, backend, keycloak, minio, gateway, 7 analyzers, valkey, postgres). `curl localhost/healthz` → ok.
- **Persistence proven on RunPod:** `pg-volume.sh status` → 2 base backups (newest `20261030-111119`), 14 archived
  WAL files (4.4 MB gzip), `archiver: archived=5 last=00000002000000000000000C failed=0`. Timeline **2** means the
  cluster was **restored from the volume** on this boot (the 29 Oct first boot was timeline 1). Bootstrap then took the
  fresh base backup (flag after restore). Pod uptime ~2 min at 11:14. The passwords printed from
  `users.json` came from the volume; gen-secrets didn't regenerate them.
- `/opt/dvas-os-packages.txt`: `Ubuntu 22.04.5 LTS java=openjdk-21-jre-headless kv=redis-server`.
- `df -h /`: overlay 50 GB (237 MB used). **`df -h /workspace` shows the whole MooseFS cluster** (`534T, 71%`), not
  the 20 GB volume, so the disk guard's percentage was meaningless (and 71% heads toward its 80% warning).
  **Fix:** `backup-loop.sh` disk_guard uses `du -sk /workspace` against a new template env **`DVAS_VOLUME_GB`** (e.g. 20).
  `dvas-ctl disk` shows `du` used of `DVAS_VOLUME_GB` and sources `/run/dvas/runtime.env`. Guide B5 env + B9 updated.
  (Takes effect with the next image build.)
- `nvidia-smi`: **RTX PRO 6000 Blackwell, 97,887 MiB**, driver 595.91.07 (CUDA 13.2 driver; image CUDA 12.8). Only
  the `person` analyzer holds GPU memory (976 MiB) at idle. The others load models lazily per stream. The image's
  torch nightly cu128 initialises on Blackwell. Inference on real streams isn't tested yet (Phase 5).
- **Security note:** the six test-user passwords were pasted into chat on 30 Oct. Treat them as exposed. Rotate before
  any real data. Edit `/workspace/dvas/secrets/users.json` (new passwords), then `dvas-ctl restart bootstrap`
  (setup_keycloak re-syncs passwords from the file), and update the password manager.
- **Roles:** the RunPod account and console (stop/start, template, env vars, billing) belong to the teammate. The builder
  works on the pod over **SSH** (his public key is on her account → `PUBLIC_KEY` → `authorized_keys`), using the
  `IP:PORT -> :22` from Connect, plus the proxy URL in a browser. That's enough for Phase 5. Console actions are
  requested from her, or she invites him to a RunPod team if the plan allows.
- **SSH from the builder's laptop (30 Oct):** `Permission denied (publickey)` on `91.199.227.82:42603`, so his key
  isn't in the pod's `authorized_keys` (not on her account, or added after the pod started; RunPod only injects
  account keys at pod start). Interim: append his `.pub` in the web terminal. **Change (next image):**
  `dvas-init.sh` builds `authorized_keys` from `PUBLIC_KEY` **plus `/workspace/dvas/secrets/authorized_keys`** on
  the volume (deduped, logs the count), so admin keys survive pod re-creation without console access.


### 10.12 Phase 4 status — 30 Oct 2026: functionally complete, 3 small items to close
| "Done when" item | Status | Evidence / note |
|---|---|---|
| Image pushed, immutable tag, `RELEASES.md` row | ✅ | `20261028-82c7456`, `sha256:5f75353d…6b57`, commit `82c7456` (release commit `111b12d`) |
| GHCR package private | ☐ confirm | GitHub → org → Packages → `dvas-runpod` → visibility **Private** |
| RunPod 2FA, SSH key, `ghcr-dvas` read-only credential | ✅ / ☐ 2FA | Image pulled with `ghcr-dvas`. The builder's key was missing from the pod (no account keys at pod start) → added by hand to `/root/.ssh/authorized_keys` and `/workspace/dvas/secrets/authorized_keys`. **Add both teammates' keys under Credentials → SSH Public Keys.** 2FA: teammate confirms |
| Network volume in the chosen data centre | ✅ (deviation) | `dvas-vol-test`, EU-NL-1, **20 GB** instead of 30. Grow before real use |
| Template as in B5 | ✅ | our entrypoint ran; start command empty; HTTP 80 / TCP 22 / `/workspace` |
| Pod running, Secure Cloud | ✅ (deviation) | **RTX PRO 6000 Blackwell 96 GB** instead of L40S (L40S unavailable in EU-NL-1). All 18 programs RUNNING, all health checks OK |
| Proxy URL shows login; SSH over TCP works | ✅ | `https://o5vg7007ar5zk9-80.proxy.runpod.net`. SSH `root@91.199.227.82 -p 42603` works from the builder's laptop |
| `dvas.env` + `users.json` copied off the pod | ✅ / ☐ | `scp` to `~/dvas-runpod-secrets/` (builder's laptop, chmod 600) on 30 Oct 12:01. **Put both into the team password manager**, keep an offline copy |
| Stop-vs-terminate behaviour recorded | ◐ | A restart **restored the DB from the volume** (WAL timeline 2, same passwords). Still to do: explicit **Stop → Start** and **Terminate → new pod on the same volume**; note whether the URL / SSH port changes |

**To close Phase 4:** (1) the B10 Stop and Terminate tests, (2) GHCR private + 2FA confirmed, (3) secrets in the
password manager, plus `nproc; free -g` for §10.5.
**Pending code on the laptop (commit + next build, no rush):** `dvas-init.sh` (SSH keys also from the volume),
`backup-loop.sh` + `dvas-ctl` (`DVAS_VOLUME_GB` disk check), guide B5/B8/B9 edits. When that image is deployed,
add `DVAS_VOLUME_GB=20` to the template.
**Before real data:** rotate the six test passwords (they were pasted in chat on 30 Oct), decide `DVAS_SEED_DEMO`,
pick the production data centre/GPU closer to Kerala, size the volume (50–100 GB).
**Roles going forward:** teammate = RunPod console (pods, template, env, billing). Builder = image builds/pushes + pod
shell over SSH + browser → can drive Phase 5 (first boot, platform bootstrap & smoke tests) from his laptop.


---

## 11. Phases 5 & 6 — first-boot checks, smoke tests, dashboard go-live (30 Oct 2026, 12:25–13:15 IST)

Run from the builder's laptop: SSH `root@91.199.227.82 -p 42603` + public URL. **The pod ID is now
`erv9019r7wxhsu`** (`https://erv9019r7wxhsu-80.proxy.runpod.net`); the old `o5vg7007ar5zk9` URL returns 404, so
the pod was re-created on the same volume (container start 30 Oct 11:10). SSH IP:port stayed the same.

### 11.1 Phase 5 results
| Plan step | Result |
|---|---|
| 5.1 first boot | 18 programs RUNNING, `bootstrap` EXITED 0, `bootstrap_ok` written |
| 5.2 credentials | `dvas.env` + `users.json` on the laptop (`~/dvas-runpod-secrets`, 30 Oct). Password manager: ☐ (teammate/you) |
| 5.3 health | `dvas-ctl health` all OK; Valkey 8 GB `volatile-lru`; 13 analytics + 14 dashboard tables owned by `dvas`; 9 buckets; ILM 3 d / 14 d, evidence + watchlist none |
| Resources | cgroup quota **27.2 vCPU**, memory limit ~251 GB (`nproc`/`free` show the 256-core / 2 TB host); container disk 50 GB; RTX PRO 6000 96 GB |
| 5.4 public path | `tools/smoke_public.py --write`: token (ROPC, `drone-ingest-client`), mission start → 3 frames (202, 350–750 ms round trip from Kerala) → mission end. Real client IP: see 11.3 |
| 5.5 persistence | Pod re-created on the same volume: Postgres restored (timeline 2), secrets from 29 Oct, demo data + MinIO buckets intact, Keycloak `frontendUrl` = new URL (issuer checked). Marker for the next Stop/Start: `local/dvas-heatmaps/smoke/persistence-marker.txt` (**expires in 3 days**) |
| 5.6 retention | ILM rules listed ✅. Disk guard logs only; the plan's `system_alert` row on the Ingest dashboard is **not implemented** (open) |
| GPU inference | vehicle analyzer processed the smoke frames on `cuda:0` (Blackwell + torch nightly cu128 OK); no traceback / CUDA / OOM in any analyzer log |

### 11.2 Phase 6 results (browser pane, you logged in; I drove)
| Row | Result |
|---|---|
| Login / redirect | CRO + Senior → `/command/tactical-map`, Admin → `/ingest`, `operator@dvas.kp` refused ("Drone operators use the DVAS Edge application"), no token left |
| Token refresh | `POST /api/auth/refresh` 200 (API). Idle past expiry not waited out (token lifetime 1 h) |
| WebSocket | `/api/ws` welcome + ping/pong; **10.8 min with 20 s pings: 32/32 pongs**; a silent socket stayed open 411 s without traffic (no 100 s idle cut). Sockets drop only on a backend restart |
| Command pages (14) | all load for CRO and Senior with no failed request and no JS error (the 404/503s on annotated/frame endpoints are the app's "no live feed" answers) |
| Models | stream stop/start of an analyzer reflected in `/health` (`stream_running`), model stays on the GPU |
| Admin (5 tabs — plan said 7) | users (6, via `dvas-admin-service`), policy, sessions, audit explorer, config: all clean |
| Ingest dashboard (8 tabs) | container grid = 17 supervisor programs; restart (scope core) touches only `ingest`; WS relay **Connected** (after fix); GPU tab NVML (after fix) |
| Audit | explorer 25 rows, hash chain valid (64/64), PDF export 0.4 s, CSV |
| Reports | daily / weekly / utilisation generated in 0.7–3.8 s, PDF downloads |
| Security | `/admin/*`, `/docs`, `/openapi.json`, `/minio/*` → SPA; `/realms/master`, `/api/docs`, `/edge-api/*` → 404; tokens kept out of logs |

### 11.3 Findings and fixes (commits `579b1b0` + `d0df131`; hot-patched on the pod)
| # | Finding | Fix |
|---|---|---|
| 1 | 🔴 `/api/ingest-server/telemetry` + `/streams/active` answered **without login** (live drone GPS, drone/mission IDs); `/frame/{id}/latest` fetched frames with the backend's service token without checking the caller | `ingest_proxy.py`: `_any_user` dependency on all three |
| 2 | 🔴 **Every live frame in the dashboard failed** (500): the ingest client's service account had no realm role → ingest 403 | `setup_keycloak.py` gives it `drone-operator` (least privilege; edge laptops hold that client secret, so not `control-room`). Frame proxy retries with the user's token if the cached service token is rejected (first boot: backend starts before bootstrap) |
| 3 | 🟠 Client IP spoofable: nginx trusted `X-Forwarded-For` from anyone → fake IP in rate limiting **and the audit log** | nginx: `real_ip_header CF-Connecting-IP` from the RunPod LB (100.64/10) + private ranges only; upstream `X-Forwarded-For` = that one address. Measured chain: `XFF = <client-sent>, <client>, <cloudflare>`, `CF-Connecting-IP = client`, peer `100.64.1.32`. Audit logins now show the real IP |
| 4 | 🟠 `/realms/master/*` public (Keycloak admin realm → password guessing) | nginx 404 |
| 5 | 🟠 ingest `/ingest/status*`, `/ingest/streams/active` have no auth in the ingest API and were public | nginx 404 publicly (dashboard reads them internally via `/api/ingest-server/*`, which needs a login) |
| 6 | 🟠 JWTs from `?token=` in `backend.log` and `nginx_access.log` | backend `--no-access-log` (nginx logs every request); nginx `log_format dvas` logs `$uri` without query string. Existing logs scrubbed on the pod |
| 7 | 🟠 Reloading `/ingest` (admin landing page) → 301 to `http://100.65.x.x/ingest/` (nginx auto-redirect to the `/ingest/` API location) | `location = /ingest` serves the SPA; `absolute_redirect off`. Browsers that saw the 301 cached it: clear site data once |
| 8 | 🟠 `pg-volume.sh basebackup`: prune pipeline `… | head -1` hit SIGPIPE under pipefail → **WAL archive never pruned**, flag never cleared, bootstrap logged "base backup failed" | array-based prune (tested: keeps 2 newest complete, cleans WAL from the oldest kept). On the pod: 18 → 14 WAL files, flag cleared |
| 9 | 🟡 ~200 threads per analyzer (torch sees 256 host cores, quota is 27 vCPU) | `dvas-init.sh`: `DVAS_CPUS` from `cpu.max`, `OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS = clamp(CPUs/8, 2, 8)` → 3. Measured on the pod: 195 → 8 threads. **Needs the new image** (env is set before supervisord) |
| 10 | 🟡 Admin ingest WS relay always "Disconnected" (relay connected to ingest `/ws/status` without a token → 403) | relay passes the admin's token |
| 11 | 🟡 Security headers missing on `index.html` (location-level `add_header` drops server-level ones) | repeated in `/index.html` and `/static/` |
| 12 | 🟢 GPU tab said "NVIDIA GTX 1650" | `runtime.gpu_name_nvml()` → "RTX PRO 6000 Blackwell Server Edition" |
| 13 | 🟢 perl/psql locale warnings over SSH (client sends `LANG=en_IN`) | sshd `AcceptEnv` disabled |

New tool: **`deploy/runpod/tools/smoke_public.py`** (stdlib; reads `~/dvas-runpod-secrets`, never prints secrets).
Read-only: 57 checks; `--write`: 71 (mission/frames on `DRN-SMOKE`, reports, audit PDF, grid restart of ingest).
Final runs: 71/71 (write) and 57/57 (read-only) on the hot-patched pod.

### 11.4 Known gaps (logged, not fixed)
- Disk guard → `system_alert` row on the Ingest dashboard (plan 5.6): only a log line today.
- Models page header "Kerala Police Analytics Node (H100)", "4 / 4 GB VRAM" (config `cluster_label`), GPU tab batch 16 /
  latency 28 ms are placeholders → real numbers come with the Phase 10 capacity advisor.
- KPI "Evidence items 0" while the vault lists 14 demo items (KPI is scope/today-based).
- Demo seed creates RPT-4001 permanently "generating" — gone with `DVAS_SEED_DEMO=0`.
- The `/api/ingest-server/health` top-bar indicator stays public (up/down only).
- Smoke-test residue: missions `SMOKE-*`, reports, audit entries on `dvas-vol-test` (test volume).

### 11.5 Hot-patches on the pod — lost on the next restart until a new image is deployed
Changed in place (originals in `/root/*.orig-82c7456`): `/etc/dvas/nginx.conf`, `/etc/dvas/supervisord.conf`
(backend `--no-access-log`), `/opt/dvas/deploy/bin/pg-volume.sh`,
`/opt/dvas/DVAS_Dashboard/backend/{ingest_proxy.py,core/runtime.py,routers/infra.py}`,
`/opt/dvas/dvas_ingest/ingest/scripts/setup_keycloak.py`. The Keycloak role mapping (#2) lives in the DB and persists.
The thread cap (#9) is **not** active until the new image.

**Next (🧑):**
1. ~~`git push`, build + push a new image~~ → done, §11.6.
2. Teammate: template image → new tag, add **`DVAS_VOLUME_GB=20`** (and decide `DVAS_SEED_DEMO`); this restarts the pod.
   That restart also exercises the restore path (`pg-volume: restoring …` in the pod's Logs); the explicit B10
   **Stop → Start** is still to do: then `mc cat local/dvas-heatmaps/smoke/persistence-marker.txt` and note the URL/SSH port.
3. After it boots, the pod's Logs show `[dvas-init] … CPU quota 28 vCPU → 3 compute threads per process`; then run
   `deploy/runpod/tools/smoke_public.py --url https://<POD_ID>-80.proxy.runpod.net --write` → expect all PASS.
4. Before real data (unchanged from §10.12): rotate the six test passwords, secrets into the password manager,
   GHCR package private + 2FA confirmed, production DC/GPU and volume size.

### 11.6 Release `20261030-d0df131` (30 Oct 2026, 13:14–13:20 IST)
- `runpod-v1` pushed (`0fa06da..d0df131`). Build from cache in ~2 min (only the code layers changed), 23.9 GB,
  20 GB disk left. Verified inside the image: nginx (CF-Connecting-IP, `/ingest`, log format), backend
  `--no-access-log`, dvas-init CPU cap, pg-volume prune, `_any_user`, Keycloak service-account role, NVML GPU name.
- Pushed 07:49Z: `ghcr.io/quantumweaveintelligence/dvas-runpod:20261030-d0df131`
  `sha256:24b5777e8274675e0534cad3f9ad1a8851c511e14636bf314fe23d7a20446831` (row in `RELEASES.md`). Base layers
  already existed on GHCR, so only the changed layers uploaded.
- **Deployed** by the teammate ~16:47 IST on a new pod `4nrza3dukc231h` (L40S). Hot-patches of §11.5 are now in the image.
  `DVAS_VOLUME_GB` / `DVAS_SEED_DEMO` were **not** added (§0.3). Rollback = `20261028-82c7456`.


## 12. Phase 7 — edge integration handoff (30 Oct 2026, 16:45–17:30 IST)

Guides: `deploy/runpod/docs/DVAS_Phase7_Edge_Handoff.md` (Part 1 = how the owner collects each item) and
**`deploy/runpod/docs/DVAS_Edge_Integration_Sheet.md`** (Part 2 = what to send the edge developer; no secrets).
Edge side read from the edge developer's `dvas_edge_exe_context (2).md` (repo root, untracked — **don't commit it**:
its runbook contains a local operator password) and `origin/edge-exe-v1` (`c8e4253`).

### 12.1 New pod after the template change
The teammate deployed `20261030-d0df131`: pod **`4nrza3dukc231h`**, **L40S 46 GB**, SSH port **25676**. Old URLs 404.
Volume restored (Postgres timeline 3); the MinIO marker written on pod `erv9019r7wxhsu` at 12:49 was still there →
**persistence across pod re-creation proven for DB + MinIO**. Keycloak issuer follows the new URL. Smoke 57/57.

### 12.2 Checks run for the handoff (public URL, from the builder's laptop)
| Check | Result |
|---|---|
| CORS preflight `Origin: http://localhost:8765` on `/api/auth/login`, `/ingest/frame` | 200, `Access-Control-Allow-Origin: http://localhost:8765` (+ credentials on `/api`); foreign origin → 400 |
| Keycloak ROPC `drone-ingest-client` for `operator_001`, `edge_laptop_01..03` | 200; access 3600 s, refresh 86 400 s; refresh grant 200 |
| Ingest with operator/laptop tokens: mission start/end/geofence (empty body) | 422 = authorised; no token → 403 |
| Operator (`drone_operator`) on every cloud route the edge pages call | `/api/auth/me`, `missions`, `drones`, `evidence`, `incidents`, `analytics/suggestions`, `audit-logs`, `audit-logs/verify`, `edge/status`, `edge/system-logs`, `ingest-server/streams/active` → 200; `ingest-server/status` → 403 (admin, not used by edge pages) |
| `wss /api/ws?token=` as operator | welcome `drone_operator` |
| `/api/push/receive` | no key 403, `DVAS_SERVICE_KEY` accepted |

### 12.3 Changes
| Change | Why |
|---|---|
| **SSH** (`c7da60c`): `dvas-init.sh` keeps only complete `type AAAA… comment` key lines, logs skipped ones | Builder locked out of the new pod: the key in `/workspace/dvas/secrets/authorized_keys` had been saved **wrapped over two lines**, so only a bare `ssh-ed25519` survived the filter. Fixed on the volume (1 clean key; backup `/root/authorized_keys.vol.bak`) |
| **CPU quota on cgroup v1**: `dvas-init.sh` reads `cpu.cfs_quota_us/period_us` when `cpu.max` is absent | The L40S host is cgroup v1 → `DVAS_CPUS` fell back to 256 → 8 threads. Tested on the pod: 28 vCPU → 3 threads. **Next image** |
| **Uploader accounts** `edge_laptop_01..03` (`@dvas.kp`, drone-operator) added to `users.json` on the volume + bootstrap re-run | One account per laptop (plan 7.1). Passwords random (22 chars), never printed; laptop copy updated |
| **`deployment.js`**: exports the edge branch's names (`DEPLOYMENT_TARGET`, `isEdgeBuild`, `isCloudBuild`); `ALLOWED_ROLES` is always an array (all 4 roles for `all`) | Trial merge `edge-exe-v1` → `runpod-v1` conflicts in `App.js` + `deployment.js`. With the aliases, the edge `App.js`/`Login.js` work on our file (its `ALLOWED_ROLES.includes` crashed on our `null`). Behaviour of cloud/edge/all builds unchanged (tested). Frontend only; no image rebuild needed |
| Phase 7 guide corrected | old URL; **`DVAS_SERVICE_KEY` and the cloud `EDGE_API_KEY` are not for the edge developer**: push/receive is backend↔backend (`DVAS_PEER_URL`), the edge's `EDGE_API_KEY` is local to the laptop |

### 12.4 Findings for the edge developer (in the sheet)
- `edge.env` for RunPod: token URL through the proxy path (no `:8080`), `INGEST__TLS_VERIFY=true`, uploader = `edge_laptop_NN`.
- Add `capture_ts` to frame metadata; `frame_seq` is accepted as extra.
- `edge_api_server.py` has a hard-coded `EDGE_API_KEY` fallback (`dvas-edge-field-secret-key`) → remove.
- Their runbook uses the local password `Operator@001`; cloud passwords are random per volume.
- Evidence clips stay on the laptop (no cloud upload yet).

### 12.5 Still open for Phase 7 "done when"
- [ ] Password-manager entries: client secret, `operator_001..003`, `edge_laptop_01..03` (from `~/dvas-runpod-secrets/`)
- [ ] Sheet sent; 7.2 contract confirmed by the edge developer
- [ ] Shared frontend PR to `testing` (our `deployment.js` + resolved `App.js`), merged into both branches
- [ ] First frame from the edge build seen in `dvas-ctl lag` / Multi-drone view
- [x] CORS + token/ingest checks (§12.2) · [x] uploader accounts · [x] SSH restored
