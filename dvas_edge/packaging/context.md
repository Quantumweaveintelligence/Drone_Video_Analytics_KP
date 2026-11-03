# DVAS Edge `.exe` Build — Context & Progress Log

> **Branch:** `edge-exe-v1` (cut from `testing`)
> **Workspace:** `D:\DVAS_KP` (isolated — `C:\Users\asus\DVAS_KP` is untouched)
> **Started:** 2026-10-27

---

## Phase E0 — Prerequisites ✅ COMPLETE

- Branch `edge-exe-v1` created from `testing` in `D:\DVAS_KP\DVAS_Dashboard`
- Workspace at `D:\DVAS_KP` is an isolated copy of the codebase
- Tools needed: Git, Node 20, Inno Setup 6, PowerShell 5+, ffmpeg

---

## Phase E1 — Edge Code Changes ✅ COMPLETE

### Changes Made:

**1. `dvas_edge/edge_api_server.py`** (7 modifications)
- Added `_EDGE_DATA_DIR = Path(os.getenv("DVAS_EDGE_DATA_DIR", "/data"))` — all `/data` paths now use this
- `SETTINGS_FILE_PATH` now reads from `_EDGE_DATA_DIR / "edge_settings.json"`
- All 8 occurrences of `Path("/data")` replaced with `_EDGE_DATA_DIR`
- `psutil.disk_usage("/data")` → uses `_EDGE_DATA_DIR`
- `uvicorn.run()` now reads `EDGE_HOST` (default `0.0.0.0`) and `EDGE_PORT` (default `8765`) from env vars
- Transmission defaults updated: `bypass_target_fps: 30`, `jpeg_quality: 75`, `max_resolution: "720p"`

**2. `dvas_edge/config/settings.yaml`** (6 modifications)
- `rtsp_url`: removed hardcoded credentials (`admin:Admin12345@`) — comment says use `DRONE_RTSP_URL` env var
- `use_gpu: true` → `use_gpu: false` (safe default for non-CUDA laptops)
- `storage_dir: "./buffer_storage"` → `""` (set via `DVAS_EDGE_DATA_DIR`)
- `client_secret`: value cleared → set via `INGEST__CLIENT_SECRET` env var
- `username` / `password`: values cleared → set via env vars

**3. `dvas_edge/ingest/edge_sender.py`** (1 modification)
- Added `frame_seq` and `capture_ts` (epoch seconds) to the `send_frame_bytes()` metadata dict

**4. `dvas_edge/.gitignore`** (additions)
- `token.txt`, `config/wireguard/wg0-client.conf`, `packaging/config/edge.env`, `*.env`

**5. `dvas_edge/requirements.txt`** (1 addition)
- Added `websockets>=11.0`

### Backward Compatibility:
- With NO new env vars set, the docker-compose edge container still behaves identically (defaults match old values)
- New env vars: `EDGE_HOST`, `EDGE_PORT`, `DVAS_EDGE_DATA_DIR`

---

## Phase E2 — Local Gateway Launcher ✅ COMPLETE

### New File: `dvas_edge/packaging/edge_launcher.py`
- FastAPI gateway on `127.0.0.1:8765` (with dynamic fallback & single-instance protection)
- Routes:
  - `/` → serves static React build from `static/` directory
  - `/edge-launcher/health` → lightweight health check & instance detection
  - `/edge-api/*` → proxies to local edge core on `127.0.0.1:8766` (or dynamic core port)
  - `/api/*` → proxies to cloud backend (`DVAS_CLOUD_URL`)
  - `/api/ws` → WebSocket bidirectional proxy to cloud
- **Single-Instance Detection:** If DVAS Edge is already running, re-launching opens the existing session in the browser and exits cleanly without error or spawning duplicate background processes.
- **Dynamic Port Fallback:** If port 8765 is occupied by another third-party application, the launcher automatically scans for the next free port (`8767`, `8768`, etc.) and binds dynamically, opening the browser to the assigned port.
- **Configurable Ports:** Supports `DVAS_PORT` (default 8765) and `DVAS_CORE_PORT` (default 8766) in `edge.env`.
- Starts `edge_api_server.py` as subprocess with `stdin=PIPE` (prevents Uvicorn EOF shutdown).
- Loads config from `%LOCALAPPDATA%\DVAS_Edge\config\edge.env`.
- Auto-opens default web browser to `http://localhost:<ui_port>` via background timer (`threading.Timer`).
- Strips `Secure` flag from cloud cookies so they work on `http://localhost`.

---

## Phase E3 — Edge Frontend Build ✅ COMPLETE

### New File: `frontend/src/lib/deployment.js`
- Exports `isEdgeBuild` (true when `REACT_APP_DEPLOYMENT_TARGET=edge`)
- Exports `ALLOWED_ROLES` (only `drone_operator` on edge build)

### Modified: `frontend/src/App.js`
- Imports `isEdgeBuild` from `deployment.js`
- Cloud-only routes (Command Shell, Admin, Audit, Ingest) wrapped in `{!isEdgeBuild && (...)}`
- `RootRedirect` always goes to `/edge` on edge build
- Edge routes (`/edge/*`) always available

### Modified: `frontend/src/pages/Login.js`
- After successful login, checks `ALLOWED_ROLES.includes(u.role)`
- Non-operator roles get: "This terminal is for Drone Operators only. Please use the Control Room dashboard."
- Login page title shows "Edge Operator Terminal" on edge build

### Build Command:
```bash
REACT_APP_DEPLOYMENT_TARGET=edge REACT_APP_BACKEND_URL= GENERATE_SOURCEMAP=false npm run build
```

---

## Phase E4 — Packaging ✅ COMPLETE

### New Files:

**`dvas_edge/packaging/build_edge.ps1`** — Master build script
- Downloads Python 3.11.9 embeddable runtime
- Installs pip + all dependencies from `requirements-edge-win.txt`
- Copies application code (excludes tests, secrets, videos, docs)
- Builds edge-only React frontend
- Copies ffmpeg/ffprobe if available
- Outputs self-contained `dist/` folder

**`dvas_edge/packaging/requirements-edge-win.txt`**
- Same as `requirements.txt` minus: `cupy-cuda11x`, `nvidia-ml-py3`, `pytest*`
- Plus: `websockets>=11.0`

**`dvas_edge/packaging/Start_DVAS_Edge.bat`** — Console launcher
- Runs `python_runtime\python.exe edge_launcher.py`

**`dvas_edge/packaging/Start_DVAS_Edge.vbs`** — Silent launcher
- Runs the `.bat` with `WindowStyle 2` (minimised console — keeps stdin open)

**`dvas_edge/packaging/config/edge.env.template`**
- Template for per-laptop configuration
- All site-specific values (server URL, credentials, drone ID)

---

## Phase E5 — Inno Setup Installer ✅ COMPLETE

### New File: `dvas_edge/packaging/dvas_edge_installer.iss`
- Installs to `%LOCALAPPDATA%\Programs\DVAS_Edge` (no admin/UAC required)
- `PrivilegesRequired=lowest`
- Wizard pages ask for: Server URL, Drone ID, Uploader username, Uploader password
- Writes `%LOCALAPPDATA%\DVAS_Edge\config\edge.env` from wizard answers (skips if already exists — upgrades keep config)
- Desktop shortcut: "DVAS Edge Operator" → `Start_DVAS_Edge.vbs`
- Start menu: "Change Server URL" → opens `edge.env` in Notepad
- Post-install option to launch immediately
- LZMA2 ultra compression
- Build-time secrets via: `ISCC.exe /DClientSecret=xxx /DEdgeApiKey=yyy`

---

## All Files Summary

### Modified Files (7)
| File | Phase |
|---|---|
| `dvas_edge/edge_api_server.py` | E1, E8 |
| `dvas_edge/config/settings.yaml` | E1 |
| `dvas_edge/.gitignore` | E1 |
| `dvas_edge/ingest/edge_sender.py` | E1, E7, E8 |
| `dvas_edge/requirements.txt` | E1 |
| `DVAS_Dashboard/frontend/src/App.js` | E3 |
| `DVAS_Dashboard/frontend/src/pages/Login.js` | E3 |
| `dvas_edge/packaging/config/edge.env.template` | E4, E7, E8 |
| `dvas_edge/packaging/dvas_edge_installer.iss` | E5, E7, E8 |

### New Files (8)
| File | Phase |
|---|---|
| `dvas_edge/packaging/edge_launcher.py` | E2 |
| `DVAS_Dashboard/frontend/src/lib/deployment.js` | E3 |
| `dvas_edge/packaging/build_edge.ps1` | E4 |
| `dvas_edge/packaging/requirements-edge-win.txt` | E4 |
| `dvas_edge/packaging/Start_DVAS_Edge.bat` | E4 |
| `dvas_edge/packaging/Start_DVAS_Edge.vbs` | E4 |
| `dvas_edge/packaging/config/edge.env.template` | E4 |
| `dvas_edge/packaging/dvas_edge_installer.iss` | E5 |

---

## Remaining Phases

| Phase | Status | Notes |
|---|---|---|
| E6 — Testing | ✅ COMPLETE (local) | Build script works, launcher tested, frontend served |
| E7 — Hardening & RunPod Integration | ✅ COMPLETE | Zero-trust auth, SRT telemetry extraction, installer wizard simplification |
| E8 — Session Token Forwarding | ✅ COMPLETE | Seamless ingest streaming reusing operator Keycloak JWT; removes client secret dependency |

---

## Phase E6 — Testing ✅ COMPLETE (Local)

### E6.0 Build Script Test
- `build_edge.ps1 -SkipFrontend` → **PASSED** (exit code 0)
- Downloaded Python 3.11.9 embeddable runtime (cached after first run)
- Installed 42 pip packages successfully
- Copied application code, removed wireguard/, cleaned __pycache__
- Copied ffmpeg + ffprobe from system PATH
- Total dist size: **511.2 MB**

### E6.1 Frontend Build
- `npm run build` with `REACT_APP_DEPLOYMENT_TARGET=edge` → **PASSED**
- Output: 338 KB JS (gzipped) + 28 KB CSS
- Only lint warnings (no errors)
- Build copied to `dist/static/`

### E6.2 Launcher Smoke Test
- `edge_launcher.py` started with embedded Python → **PASSED**
- Uvicorn gateway on `127.0.0.1:8765` → started successfully
- Edge core subprocess on `127.0.0.1:8766` → started
- `edge.env` loaded from `%LOCALAPPDATA%\DVAS_Edge\config\` → cloud URL resolved
- `GET /` → **HTTP 200**, `index.html` served (1637 bytes, text/html)

### Dist Folder Structure
```
dist/                          (511.2 MB total)
├── python_runtime/            (embedded Python 3.11 + all pip packages)
├── static/                    (React edge build — index.html, JS, CSS, assets)
├── src/                       (edge video pipeline, buffer, change detection)
├── ingest/                    (edge_sender.py, Keycloak auth)
├── config/                    (settings.yaml — no secrets)
├── edge_api_server.py         (FastAPI edge core)
├── edge_launcher.py           (gateway: static UI + proxy)
├── main.py                    (pipeline entry point)
├── ffmpeg.exe                 (evidence extraction)
├── ffprobe.exe
├── Start_DVAS_Edge.bat        (console launcher)
└── Start_DVAS_Edge.vbs        (minimised launcher)
```

### What Was Tested
- [x] Build script runs end-to-end without errors
- [x] All pip packages install into embedded Python
- [x] Frontend compiles with edge deployment target
- [x] Launcher starts, serves index.html on localhost:8765
- [x] edge.env is read from %LOCALAPPDATA%
- [x] Inno Setup installer compilation (`DVAS_Edge_Setup_1.0.0.exe`)

### Inno Setup Installer Build Output
- Compiler: Inno Setup 6.3.3 (`ISCC.exe`)
- Setup File: `D:\DVAS_KP\dvas_edge\packaging\output\DVAS_Edge_Setup_1.0.0.exe`
- Size: **146.5 MB** (compressed with LZMA2 ultra from 511.2 MB uncompressed distribution)
- Installation Target: `%LOCALAPPDATA%\Programs\DVAS_Edge` (Standard non-admin / non-elevated user)
- Configuration Wizard: Prompts only for Ingest Server URL (pre-configured with default `https://4nrza3dukc231h-80.proxy.runpod.net/ingest`); Drone ID and uploader credentials are removed from wizard so operators can enter them in the UI / mission start.
- Shortcuts:
  - Desktop: `DVAS Edge Operator` (runs `Start_DVAS_Edge.vbs` without console popup)
  - Start Menu: `DVAS Edge Operator`, `Change Ingest URL` (opens `edge.env` in Notepad), `View Logs`

---


---

## Phase E7 / RunPod Live Cloud Integration ✅ COMPLETE

> Integration verified with RunPod pod `4nrza3dukc231h` (image `20261030-d0df131`, RTX PRO 6000) on 30 Oct 2026. Reference: `DVAS_Edge_Integration_Sheet.md`.

### Integration Updates:
1. **Live Cloud Base URL:**
   - Active URL: `https://4nrza3dukc231h-80.proxy.runpod.net` (Cloudflare HTTPS, port 443).
   - Ingest Endpoint: `https://4nrza3dukc231h-80.proxy.runpod.net/ingest/frame`
   - Mission Endpoints: `/ingest/mission/start`, `/ingest/mission/end`, `/ingest/mission/geofence`
   - Keycloak Token Endpoint: `https://4nrza3dukc231h-80.proxy.runpod.net/realms/kerala-police-drone/protocol/openid-connect/token` (routed through proxy path, no `:8080`).
2. **Account & Identity Structure:**
   - **Uploader Accounts (M2M frame ingest):** `edge_laptop_01` (for `DRN-001`), `edge_laptop_02` (for `DRN-002`), `edge_laptop_03` (for `DRN-003`). Passwords managed securely per pod volume.
   - **Human Operator UI Login:** Operators log in with their email (e.g. `operator@dvas.kp`, `operator_002@kerala.police.in`) at `/api/auth/login`.
3. **Zero-Trust Edge Security:**
   - Removed hard-coded fallback `dvas-edge-field-secret-key` in `edge_api_server.py`.
   - `EDGE_API_KEY` is now strictly environment/config driven (`edge.env`).
4. **Frame Metadata Compliance:**
   - Added `capture_ts` (epoch seconds float) and `frame_seq` to all frame submission paths (`send_frame` and `send_frame_bytes`) in `edge_sender.py` for cloud out-of-order frame prevention.
5. **Installer & Configuration Defaults:**
   - Updated `dvas_edge_installer.iss` wizard default to `https://4nrza3dukc231h-80.proxy.runpod.net/ingest`.
   - Added `#ifndef IngestPassword` support for build-time password injection.
   - Updated `edge.env.template` with official RunPod pod endpoints and `edge_laptop_01` defaults.

## Field Operator Runbook (Running on Another Laptop)

### 1. Installation
1. Copy `DVAS_Edge_Setup_1.0.0.exe` (~146.5 MB) via USB / network to the operator laptop.
2. Double-click `DVAS_Edge_Setup_1.0.0.exe`.
3. The wizard displays the pre-configured Ingest Server URL:
   - **Default:** `https://4nrza3dukc231h-80.proxy.runpod.net/ingest`
   - If using another server or local server, simply edit the URL, or keep the default and click **Next**.
   - No Drone ID or uploader credentials required during installation!
4. Click **Install** and finish installation (no admin / UAC prompt required).

### 2. Launching & Logging in
1. Launch from Desktop shortcut: **DVAS Edge Operator**.
2. Your system's default web browser (Chrome, Edge, Firefox, Brave, etc.) will automatically open to `http://localhost:8765`.
3. Log in with operator credentials:
   - Role must be `drone_operator` (other roles are blocked from the edge terminal).
   - Once authenticated, you will be redirected to the **Edge Operator Dashboard**.

### 3. Mission Execution
1. Navigate to **Mission Control** (`/edge/mission-control`).
2. Enter your Drone ID (e.g. `DRN-001`), operator name, and flight location.
3. Select Video Feed Source:
   - **Local File / Simulation:** Upload MP4 and optional SRT telemetry file.
   - **Drone RTMP Push:** Configure drone/controller to push to `rtmp://<LAPTOP_IP>:1935/live/drone`.
4. Click **Start Mission**:
   - Rolling SSD buffer begins storing frames in `%LOCALAPPDATA%\DVAS_Edge\data\buffer_storage`.
   - Motion detection and cognitive selector score candidate frames.
   - Selected key frames + telemetry are pushed to the ingest server.
5. Click **Stop Mission** when flight completes.

### 4. Configuration Reference (`%LOCALAPPDATA%\DVAS_Edge\config\edge.env`)
| Key | Purpose | Example |
|---|---|---|
| `DVAS_CLOUD_URL` | Cloud / Server base URL for dashboard API | `https://4nrza3dukc231h-80.proxy.runpod.net` |
| `INGEST__URL` | Frame ingest endpoint | `https://4nrza3dukc231h-80.proxy.runpod.net/ingest/frame` |
| `INGEST__MISSION_START_URL` | Mission start notification | `https://4nrza3dukc231h-80.proxy.runpod.net/ingest/mission/start` |
| `INGEST__MISSION_END_URL` | Mission end notification | `https://4nrza3dukc231h-80.proxy.runpod.net/ingest/mission/end` |
| `INGEST__TOKEN_URL` | Keycloak OpenID token endpoint | `https://4nrza3dukc231h-80.proxy.runpod.net/realms/...` |
| `INGEST__CLIENT_ID` | Keycloak client ID | `drone-ingest-client` |
| `INGEST__CLIENT_SECRET` | Keycloak client secret | Pre-baked in installer |
| `INGEST__USERNAME` | Frame uploader account (per laptop: edge_laptop_01..03) | `edge_laptop_01` |
| `INGEST__PASSWORD` | Frame uploader password (from team password manager) | Set in edge.env |
| `INGEST__DRONE_ID` | Assigned Drone ID fallback | `DRN-001` |
| `INGEST__TLS_VERIFY` | Verify HTTPS certs (`false` for HTTP LAN, `true` for public SSL) | `true` |
| `EDGE_API_KEY` | Local Edge API authentication token | Pre-configured |
| `REQUIRE_EDGE_AUTH` | Guard edge core routes | `true` |
| `DVAS_PORT` | Optional UI Gateway port override (auto-fallbacks if busy) | `8765` |
| `DVAS_CORE_PORT` | Optional Edge Core port override (auto-fallbacks if busy) | `8766` |
| `ENV` | Environment | `production` |

---

## Phase E8 — Approach 2: Dynamic Session Token Forwarding ✅ COMPLETE

### Root Cause of Frame Upload Failure:
During live operator field trials on RunPod, the operator successfully authenticated to the Web UI using their assigned credentials (`edge_laptop_01@dvas.kp` / `drone_operator` role). However, when starting a mission, frame uploads immediately failed with:
`{"error":"unauthorized_client","error_description":"Invalid client or Invalid client credentials"}`
- **Reason:** The previous Phase 1 design attempted to acquire a separate M2M Keycloak token in `edge_sender.py` using static credentials in `edge.env` (`INGEST__CLIENT_SECRET` and `INGEST__PASSWORD`). When RunPod pods are recreated, Keycloak generates a new realm client secret, invalidating any hardcoded client secrets stored on operator laptops.
- **Field Usability Barrier:** Ordinary police field constables cannot be expected to edit `.env` configuration files or retrieve backend Keycloak client secrets every time a cloud pod changes. Furthermore, drones are reassigned dynamically between patrol laptops, so hardcoding static drone IDs and per-laptop uploader passwords in config files created operational friction.

### Architectural Solution (Approach 2 — Session Token Forwarding):
Instead of managing separate machine credentials on each laptop, the Edge Operator terminal directly forwards and multiplexes the logged-in operator's Keycloak JWT session token:
1. **Single Human Authentication Flow:**
   - The constable logs into `http://localhost:8765` using their standard Keycloak credentials.
   - The browser receives an RS256-signed JWT access token with role `drone-operator` from the active Keycloak instance.
   - Central Ingest endpoints (`POST /ingest/mission/start`, `POST /ingest/frame`, `POST /ingest/mission/geofence`, `POST /ingest/mission/end`) require only a valid `drone-operator` (or `sys-admin`) role and Keycloak signature. The operator's active JWT fulfills this requirement out-of-the-box.
2. **Reverse Proxy & Zero-Trust Token Interception:**
   - The React frontend automatically sends `Authorization: Bearer <operator_token>` on all `/edge-api/...` endpoints.
   - The local gateway (`edge_launcher.py`) forwards these headers to the edge core on port 8766.
   - `verify_edge_auth()` in `edge_api_server.py` validates the token using cached JWKS (`/certs`) from Keycloak and records it in `_ACTIVE_OPERATOR_TOKEN` along with its expiration timestamp.
3. **Continuous Background Token Synchronization:**
   - When a mission starts via `POST /api/edge/mission/start`, the active session token is forwarded directly into `IngestClient`.
   - The React UI polls `/api/edge/status` and `/api/edge/telemetry` every 1-2 seconds with the active Bearer token.
   - When the React frontend auto-refreshes the token via `/api/auth/refresh`, subsequent status polls immediately trigger `update_active_operator_token()`, which dynamically calls `pipeline.sender_thread.set_session_token(token, expiry)`.
   - The streaming pipeline stays continuously authorized without interrupting the live video transmission.
4. **Graceful Fallback & Zero-Touch Deployment:**
   - `IngestClient.get_token()` prioritizes the forwarded session token.
   - Machine credentials in `edge.env` (`INGEST__CLIENT_SECRET` / `INGEST__PASSWORD`) are now completely optional fallback parameters. Even if they are blank or mismatched, the stream succeeds using the active UI session token.
   - Drones can be freely selected during Mission Start in the UI (`req.drone_id`), eliminating drone-to-laptop lock-in.

### Key Code Modifications:
0. **`DVAS_Dashboard/frontend/src/pages/edge/MissionControl.js`:**
   - Added dedicated Drone ID input with fixed `DRN-` prefix and `000` default value (making `DRN-000`).
   - Eliminates dropdowns; constables type only the drone number (e.g. `001` or `010`).
   - Transmits selected `drone_id` dynamically to `/api/edge/mission/start`.

1. **`dvas_edge/ingest/edge_sender.py`:**
   - Automatically detects physical laptop Windows computer name (`COMPUTERNAME`) via `get_laptop_name()`.
   - Attaches `laptop_id`, `laptop_name`, and `uploader_username` to every frame's `metadata_json` for HQ forensics/audit.
   - `INGEST__USERNAME` defaults automatically to the physical laptop name.
   - `IngestClient.__init__`: Added `session_token: Optional[str] = None` and `token_expiry: float = 0.0`.
   - Added `set_session_token(token, expiry)` to dynamically receive refreshed session tokens.
   - `get_token()`: Returns forwarded session token if valid (>15s remaining); attempts Keycloak direct grant only if credentials exist; gracefully retains session token if direct grant fails instead of crashing.
   - Added `verify=_tls_param()` to `_acquire_token` for consistent SSL verification.
2. **`dvas_edge/edge_api_server.py`:**
   - Added `EdgeSenderThread.set_session_token()` to route token updates from thread-safe API handlers to the running client.
   - Added module-level `update_active_operator_token(token, expiry)` to synchronize tokens across incoming requests and active sender threads.
   - Updated `verify_edge_auth` to capture validated JWT tokens and update active session state.
   - Updated `start_mission` route and `EdgePipeline.start_mission` to initialize `IngestClient` with the operator's active session token.
   - Updated `toggle_ingest` route to refresh the session token upon ingestion toggle.
   - Updated `get_jwks` and proxy status routes to respect `INGEST__TLS_VERIFY`.
3. **`dvas_edge/packaging/config/edge.env.template` & `dvas_edge/packaging/dvas_edge_installer.iss`:**
   - Pre-configured defaults updated to the current RunPod cloud pod (`https://g03m12pg2h741j-80.proxy.runpod.net/ingest`).
4. **Packaging & Distribution:**
   - Synchronized updated `edge_api_server.py` and `ingest/edge_sender.py` into `dvas_edge/packaging/dist/`.
   - Cleaned `__pycache__` artifacts.
   - Recompiled `DVAS_Edge_Setup_1.0.0.exe` with Inno Setup.
