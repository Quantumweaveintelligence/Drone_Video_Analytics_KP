# context_runpodv1.md — RunPod v1 implementation (Phases 0–4)

> Branch: `runpod-v1` · Working copy: `/mnt/NewVolume/DVAS_RUNPODV1` · Date: 26 Oct 2026 · **Updated 27 Oct 2026 (Phase 4, §10)**
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

## 8. Open items / risks (carried forward)
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

### 10.5 To record after you provision (fill in)
| Item | Value |
|---|---|
| Image tag / digest | |
| Base image used | |
| Data centre | |
| Pod ID / proxy URL | |
| vCPU / RAM / container disk | |
| Stop offered with a network volume? URL kept after Stop/Start? | |

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
