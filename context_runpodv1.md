# context_runpodv1.md — RunPod v1 implementation (Phases 0–3)

> Branch: `runpod-v1` · Working copy: `/mnt/NewVolume/DVAS_RUNPODV1` · Date: 26 Oct 2026
> Plan followed: `deploy/runpod/docs/DVAS_RunPod_Deployment_Plan.md` (v2, rev 3)
> Scope delivered: **code and repository changes for Phases 0–3**. Steps that need your
> accounts, the live local stack, Docker or a GPU (marked 🧑 below) are prepared as scripts
> and listed in §6 — they have not been executed.
> User directives applied: restored `crowd/main.py` is the base (reviewed + extended);
> **RF-DETR is not integrated** (no `rfdetr` dependency, RF-DETR checkpoints excluded from
> the image); all changes made locally for you to commit and push.

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

## 6. What you need to do next (🧑)

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

## 8. Open items / risks (carried forward)
- **Not run**: image build, `npm run build` of the cloud target, Python 3.12 dependency resolution
  (Paddle 2.6.2 cp312 wheel, `lap`, ultralytics 8.3 with numpy 1.26). Fallback for Paddle is noted in `anpr.txt`.
- Valkey binary is copied from the Debian-based official image into Ubuntu 24.04 (glibc newer → OK); the
  Dockerfile checks `valkey-server --version`.
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
**Modified:** `.gitignore`; backend `core/config.py`, `server.py`, `ingest_proxy.py`, `routers/{analytics,edge}.py`,
`services/{audit_sync,system_telemetry}.py`, `server_monolith_backup.py`; `DVAS_Dashboard/docker-compose.yml`;
frontend `App.js`, `context/AuthContext.js`, `hooks/useJpegPoller.js`, `pages/Login.js`;
analytics `shared/config.py`, `crowd/{main,analyzer}.py`, `{person,vehicle,anpr,intrusion,vessel,anomaly}/main.py`,
`gateway/main.py`, `db/003_minio_reid.sql`, `db/migrations/{002,003}*.sql`;
ingest `api/{config,main,models,queue_manager}.py`, `api/Dockerfile`, `scripts/setup_keycloak.py`,
`dvas_ingest/docker-compose.yml`.
**Local only (git-ignored, not for commit):** `DVAS_Dashboard/.env` (appended block),
`dvas_ingest/ingest/scripts/users.local.json`.
