# DVAS Edge Operator Application (`DVAS_Edge_Setup.exe`) — Build Plan

> **Owner:** edge teammate · **Branch:** `edge-exe-v1` (cut from `testing`) · Prepared 26 Oct 2026
> **Companion documents:** `DVAS_RunPod_Deployment_Plan.md` (cloud side, owned by the RunPod owner — see its **Phase 7 "Edge integration handoff"** for the interface contract) and `DVAS_RunPodV1_Branch_and_VSCode_Setup.md` (same git/VS Code steps apply, with your own branch name).

---

## How to use this document

Eight phases, **E0 → E7**, each with goal, prerequisites, steps and a **✅ Done when** checklist. Finish and tick one phase before starting the next.

| Phase | Title | Indicative effort |
|---|---|---|
| E0 | Prerequisites, branch & credentials | 0.5 d |
| E1 | Edge code changes (`dvas_edge/`) | 1 d |
| E2 | Local gateway launcher | 1 d |
| E3 | Edge frontend build | 0.5 d |
| E4 | Packaging: embedded Python runtime & per-laptop config | 1 d |
| E5 | Inno Setup installer | 0.5–1 d |
| E6 | Testing — local server first, then the RunPod pod | 1–2 d |
| E7 | Hardening & optional clip upload | 1–2 d |
| | **Total** | **≈ 6.5–9 working days** |

You can start **before the RunPod pod exists**: develop and test against the existing local Ubuntu server stack (E6.1), then switch the URL to the pod when the RunPod owner hands it over.

---

## 1. What the application is

The operator double-clicks **DVAS Edge Operator** on a Windows laptop and gets, in the browser at `http://localhost:8765`:
- the login page (online login against the cloud Keycloak via the cloud backend)
- the **11 edge pages**: Dashboard, Mission Control, Live Monitoring, GPS & Telemetry, Buffer & Change Detection, Frame Selection, Incidents, Evidence Management, System Health, Logs & Audit, Settings
- the **local video pipeline**: RTSP/USB/file source → **rolling disk buffer** → MOG2 change detection → **cognitive frame selector** (or **bypass at 30 fps**) → HTTPS upload to the cloud ingest

Decisions already taken: Inno Setup installer + embedded Python 3.11 (no admin rights, no system Python), online login (no offline credential cache in v1), disk buffer and frame selection keep working if the link drops mid-mission (frames are queued in the offline buffer and sent on reconnect).

### 1.1 Architecture

```
Browser  http://localhost:8765  ──►  edge_launcher.py  (127.0.0.1:8765, FastAPI)
                                       ├── /            → static edge React build (REACT_APP_DEPLOYMENT_TARGET=edge)
                                       ├── /edge-api/*  → edge core  127.0.0.1:8766  (existing edge_api_server.py, unchanged routes)
                                       ├── /api/*       → https://<POD_ID>-80.proxy.runpod.net/api/*   (login, missions, audit, incidents…)
                                       └── /api/ws (WS) → wss://<POD_ID>-80.proxy.runpod.net/api/ws
edge core (8766): RTSP/USB/file source → disk ring buffer → change detection → cognitive selector / bypass(30 fps)
                  → EdgeSenderThread → HTTPS POST https://<POD_ID>-80.proxy.runpod.net/ingest/frame
```

- This reproduces exactly what the dashboard nginx does today (`/edge-api` + `/api` on one origin, F11).
- The React code needs **no URL changes**.
- Cookies and tokens stay first-party on `localhost`.

### 1.2 Issues in today's code that this plan fixes

| ID | Issue | Fixed in |
|---|---|---|
| F11 | The edge pages call both `/edge-api/...` (local edge server) and `/api/...` (cloud backend) on the **same origin**. Today the dashboard nginx stitches these together; the `.exe` needs a local gateway that does the same. | E2 |
| F12 | `edge_api_server.py` hard-codes `host="0.0.0.0", port=8765` and the settings path `/data/edge_settings.json` (becomes `C:\data` on Windows). | E1 |
| F9 (edge part) | Secrets committed in the edge tree: `config/settings.yaml` (Keycloak client secret, operator password), `token.txt`, `config/wireguard/wg0-client.conf`; default `EDGE_API_KEY`. | E1, E4, E7 |
| F18 | Evidence clips stay on the laptop SSD; the control room can't play them. | E7 (optional) |
| — | Uvicorn shuts down on stdin EOF when started without a console. | E2 (stdin kept open) + E4 (minimised `.vbs` launcher) |

---

## 2. Interface with the cloud (agreed with the RunPod owner)

| You receive from the RunPod owner | Notes |
|---|---|
| Server base URL `https://<POD_ID>-80.proxy.runpod.net` | Changes if the pod is re-created — keep it editable (E4) |
| Keycloak client `drone-ingest-client` + its secret | Via the password manager only |
| Operator test logins `operator_001..003` | |
| One uploader account per laptop (`edge_laptop_01`, …, role `drone-operator`) | Used by the frame uploader, separate from the human login |
| `EDGE_API_KEY` | |

| Endpoint (relative to the base URL) | Used by |
|---|---|
| `POST /ingest/frame`, `/ingest/mission/start`, `/ingest/mission/end`, `/ingest/mission/geofence` | edge core → cloud ingest |
| `POST /realms/kerala-police-drone/protocol/openid-connect/token` | uploader token (ROPC) |
| `GET /realms/kerala-police-drone/protocol/openid-connect/certs` | edge core JWT verification (JWKS) |
| `/api/auth/login`, `/api/auth/refresh`, `/api/auth/me`, `/api/missions*`, `/api/audit-logs*`, `/api/incidents*`, `/api/edge/*`, `/api/push/receive`, `/api/ws` | edge pages via the local gateway |

Rules:
1. **Shared frontend files.** The build-target switch (`REACT_APP_DEPLOYMENT_TARGET`, `src/lib/deployment.js`, edits to `App.js`, `Login.js`, `AuthContext.js`, `useJpegPoller.js`) is implemented **once by the RunPod owner as a PR to `testing`**. Merge `testing` into `edge-exe-v1` to get it — don't edit those files independently. If you need further frontend changes, send them as small PRs to `testing` too.
2. Use drone IDs **`DRN-001..003`** for the 3-feed test.
3. Add `frame_seq` and `capture_ts` to every frame's metadata (E1) — the cloud uses them to drop out-of-order frames.
4. Report the build/version you test with, so cloud-side issues can be matched to it.

---

## Phase E0 — Prerequisites, branch & credentials

**Goal.** Tools, branch and test accounts ready.
**Where.** 🪟 Windows build machine (+ 💻 for git if preferred).

1. **Tools (Windows 10/11 x64):**
   - Git for Windows
   - VS Code
   - Node 20 LTS (frontend build)
   - [Inno Setup 6](https://jrsoftware.org/isinfo.php)
   - PowerShell 5+
   - A static **ffmpeg/ffprobe** build (gyan.dev or BtbN)
   - Internet access to python.org and PyPI
2. **Branch.** Follow `DVAS_RunPodV1_Branch_and_VSCode_Setup.md` but with your own branch:
   ```bash
   git clone https://github.com/Quantumweaveintelligence/Drone_Video_Analytics_KP.git DVAS_EDGE_EXE
   cd DVAS_EDGE_EXE
   git switch -c edge-exe-v1 origin/testing
   git push -u origin edge-exe-v1
   ```
3. **Test laptop(s):** at least one clean Windows 10 and one Windows 11 machine (or VMs) with a **standard (non-admin) user** and an SSD.
4. **Credentials:** URL, client secret, operator/uploader accounts and `EDGE_API_KEY` from the RunPod owner (or from the local server's Keycloak for E6.1).
5. **Test media:** a DJI/Autel MP4 + matching SRT (e.g. `DJI_0110.MP4`) on the test laptop.

### ✅ Done when
- [ ] All tools installed; `edge-exe-v1` pushed
- [ ] Test laptops available; test media copied
- [ ] Credentials received through the password manager

---

## Phase E1 — Edge code changes (`dvas_edge/`)

**Goal.** The edge core runs from any folder, binds only to localhost, and takes every site-specific value from environment variables.
**Prerequisites.** E0.

1. **`edge_api_server.py`**
   - `uvicorn.run(app, host=os.getenv("EDGE_HOST", "0.0.0.0"), port=int(os.getenv("EDGE_PORT", "8765")))`. Defaults keep the docker-compose behaviour; the launcher sets `127.0.0.1:8766`.
   - `SETTINGS_FILE_PATH = Path(os.getenv("DVAS_EDGE_DATA_DIR", "/data")) / "edge_settings.json"`. Use the same base dir for uploads, mission history and logs.
2. **`config/settings.yaml` → defaults only.** All site-specific values come from `edge.env` (E4), as env overrides with the `INGEST__` prefix.
   - Remove the secrets (`client_secret`, `password`, RTSP credentials).
   - `buffer.storage_dir` → `${DVAS_EDGE_DATA_DIR}\buffer_storage`. Must be on the **SSD**: 30 fps × 60 s at 1080p ≈ 45–60 MB slab plus index; leave ≥ 5 GB free for evidence extraction.
   - `drone.use_gpu: false` by default. The pip OpenCV wheel has no CUDA/NVDEC; enable only after proving an NVDEC-capable FFmpeg path on that laptop.
3. **Remove committed secrets from the tree.** `git rm --cached dvas_edge/token.txt dvas_edge/config/wireguard/wg0-client.conf` and add them to `.gitignore`. Coordinate with the RunPod owner, who removes the same files on `runpod-v1`.
4. **Ingest credentials**
   - One Keycloak user per laptop (`edge_laptop_01`, role `drone-operator`) for the frame uploader.
   - Its credentials go in `edge.env` (`INGEST__USERNAME`, `INGEST__PASSWORD`), never in `settings.yaml`.
   - The human operator's login is separate and goes through `/api/auth/login`.
   - `INGEST__TLS_VERIFY=true` for the pod: the RunPod proxy has a publicly trusted certificate and `httpx` uses `certifi`.
5. **Frame metadata.** In `EdgeSenderThread._send_task` / `IngestClient.send_frame_bytes`, add a per-mission, monotonically increasing `frame_seq` and `capture_ts` (epoch seconds of capture) to `metadata_json`.
6. **Transmission defaults for the 30 fps test** (*Settings* page or `edge_settings.json`):
   ```json
   "transmission": { "bypass_target_fps": 30, "jpeg_quality": 75, "max_resolution": "720p", "concurrency_semaphore": 10 }
   ```
   Bypass mode is toggled in Mission Control (`/api/edge/mission/toggle-pipeline`). Cognitive-selection mode keeps working as today.

### ✅ Done when
- [ ] With no new env vars, the docker-compose `edge` container still behaves as before (regression)
- [ ] With `EDGE_HOST=127.0.0.1 EDGE_PORT=8766 DVAS_EDGE_DATA_DIR=<dir>` the server starts on Windows and writes settings under `<dir>`
- [ ] No secrets left in `settings.yaml`; `token.txt` and WireGuard config untracked
- [ ] Frames carry `frame_seq` and `capture_ts`

---

## Phase E2 — Local gateway launcher

**Goal.** One process on `127.0.0.1:8765` that serves the UI, proxies `/edge-api/*` to the local edge core and `/api/*` (including the WebSocket) to the cloud — fixing F11 without touching the React code.
**Prerequisites.** E1.

### E2.1 `dvas_edge/packaging/edge_launcher.py` (new)


```python
"""DVAS Edge launcher: static UI + local edge core + cloud API reverse proxy on 127.0.0.1:8765."""
import asyncio, os, pathlib, subprocess, sys, webbrowser
import httpx, uvicorn, websockets
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

APP_DIR  = pathlib.Path(__file__).resolve().parent
DATA_DIR = pathlib.Path(os.environ.get("LOCALAPPDATA", APP_DIR)) / "DVAS_Edge"
load_dotenv(DATA_DIR / "config" / "edge.env")
CLOUD    = os.environ["DVAS_CLOUD_URL"].rstrip("/")          # https://<POD_ID>-80.proxy.runpod.net
CORE     = "http://127.0.0.1:8766"
STATIC   = APP_DIR / "static"
HOP      = {"host", "content-length", "connection", "keep-alive", "transfer-encoding", "upgrade", "te", "trailer"}
METHODS  = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None), verify=True)

async def _proxy(request: Request, target: str):
    url = target + (f"?{request.url.query}" if request.url.query else "")
    headers = [(k, v) for k, v in request.headers.items() if k.lower() not in HOP]
    upstream = await client.send(client.build_request(request.method, url, headers=headers,
                                                      content=request.stream()), stream=True)
    resp = StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code,
                             background=BackgroundTask(upstream.aclose))
    raw = []
    for k, v in upstream.headers.multi_items():
        if k.lower() in HOP:
            continue
        if k.lower() == "set-cookie":            # cloud cookies must work on http://localhost
            v = v.replace("; Secure", "").replace("; secure", "")
        raw.append((k.lower().encode(), v.encode()))
    resp.raw_headers = raw
    return resp

@app.api_route("/edge-api/{path:path}", methods=METHODS)
async def edge_core(path: str, request: Request):
    return await _proxy(request, f"{CORE}/{path}")

@app.websocket("/api/ws")
async def ws_proxy(ws: WebSocket):
    await ws.accept()
    target = CLOUD.replace("https://", "wss://").replace("http://", "ws://") + "/api/ws"
    if ws.url.query:
        target += "?" + ws.url.query
    async with websockets.connect(target, open_timeout=10, ping_interval=20) as up:
        async def c2u():
            while True:
                await up.send(await ws.receive_text())
        async def u2c():
            async for msg in up:
                await (ws.send_text(msg) if isinstance(msg, str) else ws.send_bytes(msg))
        done, pending = await asyncio.wait({asyncio.create_task(c2u()), asyncio.create_task(u2c())},
                                           return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()

@app.api_route("/api/{path:path}", methods=METHODS)
async def cloud_api(path: str, request: Request):
    return await _proxy(request, f"{CLOUD}/api/{path}")

@app.get("/{path:path}")
async def spa(path: str):
    f = (STATIC / path).resolve()
    if path and f.is_file() and STATIC in f.parents:
        return FileResponse(f)
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

def main():
    env = {**os.environ, "EDGE_HOST": "127.0.0.1", "EDGE_PORT": "8766", "DVAS_EDGE_DATA_DIR": str(DATA_DIR)}
    # stdin=PIPE kept open -> avoids the Uvicorn EOF shutdown seen when running without a console
    core = subprocess.Popen([sys.executable, str(APP_DIR / "edge_api_server.py")], cwd=APP_DIR, env=env,
                            stdin=subprocess.PIPE)
    try:
        if os.environ.get("DVAS_OPEN_BROWSER", "1") == "1":
            asyncio.get_event_loop().call_later(3, webbrowser.open, "http://localhost:8765")
        uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
    finally:
        core.terminate()

if __name__ == "__main__":
    main()
```


Notes:
- Add `websockets` and `python-dotenv` to the edge requirements.
- The launcher strips the `Secure` flag from cloud cookies so they work on `http://localhost`; the frontend also keeps the bearer token in `localStorage`, so cookies are a fallback.
- Keep `stdin=subprocess.PIPE` for the edge core — it avoids the Uvicorn EOF shutdown.

### ✅ Done when
- [ ] `python edge_launcher.py` (dev machine, `DVAS_CLOUD_URL` pointing at the local server) serves the UI, logs in through `/api/auth/login`, and the edge pages load their `/edge-api/...` data
- [ ] `/api/ws` connects through the launcher (DevTools → WS)
- [ ] `/edge-api/stream/live` (MJPEG) streams through the launcher

---

## Phase E3 — Edge frontend build

**Goal.** A static React build that only contains the login + edge pages.
**Prerequisites.** The shared build-target PR merged to `testing` and merged into `edge-exe-v1` (Section 2, rule 1).

```bash
cd DVAS_Dashboard/frontend
REACT_APP_DEPLOYMENT_TARGET=edge REACT_APP_BACKEND_URL= GENERATE_SOURCEMAP=false npm run build
# copy build/ → dvas_edge/packaging/dist/static/   (done by build_edge.ps1)
```

- An `edge` build lets only `drone_operator` in, and shows "use the control-room dashboard" to other roles.
- With the E2 launcher the React code needs no URL changes (`/edge-api/*` and `/api/*` stay as they are).

### ✅ Done when
- [ ] `build/` produced with `REACT_APP_DEPLOYMENT_TARGET=edge`; no command/admin routes reachable
- [ ] Served through the launcher, every edge page renders without console errors

---

## Phase E4 — Packaging: embedded Python runtime & per-laptop config

**Goal.** A self-contained `dist\` folder that runs on a clean Windows machine without installing Python.
**Prerequisites.** E2, E3.

### E4.1 Build script — `dvas_edge/packaging/build_edge.ps1`
1. Download `python-3.11.9-embed-amd64.zip` → `dist\python_runtime\`.
2. Edit `python311._pth`: uncomment `import site`, add a line `..`.
3. Install `get-pip.py`, then:
   ```
   python_runtime\python.exe -m pip install --no-warn-script-location -r requirements-edge-win.txt
   ```
   `requirements-edge-win.txt` = `dvas_edge/requirements.txt` **minus** pytest/pytest-cov/pytest-timeout and `cupy-cuda11x` (only add cupy back if a laptop GPU path is proven), **plus** `websockets` and `python-dotenv`. Pin exact versions after the first successful build (`pip freeze`).
4. Copy the application:
   - **Include:** `edge_api_server.py`, `main.py`, `src/`, `ingest/`, `config/settings.yaml`, `config/config.py`, `packaging/edge_launcher.py`, `static/`, `ffmpeg.exe` + `ffprobe.exe` (static build, for `_ensure_h264`)
   - **Exclude:** `token.txt`, `config/wireguard/`, `tests/`, `*.MP4` folders, `output_documents/`, `generate_*.py`, `*.md`, `*.pdf`, `buffer_storage/`, `logs/`
5. Launchers:
   - `Start_DVAS_Edge.bat`: `"%~dp0python_runtime\python.exe" "%~dp0edge_launcher.py"`
   - `Start_DVAS_Edge.vbs`: runs the .bat with **WindowStyle 2** (minimised, not hidden — keeps the console attached)
6. Compile the installer: `& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" dvas_edge_installer.iss`


### E4.2 Per-laptop configuration — `%LOCALAPPDATA%\DVAS_Edge\config\edge.env`

Written by the installer wizard (E5) and editable afterwards.
```ini
DVAS_CLOUD_URL=https://<POD_ID>-80.proxy.runpod.net
INGEST__URL=https://<POD_ID>-80.proxy.runpod.net/ingest/frame
INGEST__MISSION_START_URL=https://<POD_ID>-80.proxy.runpod.net/ingest/mission/start
INGEST__MISSION_END_URL=https://<POD_ID>-80.proxy.runpod.net/ingest/mission/end
INGEST__TOKEN_URL=https://<POD_ID>-80.proxy.runpod.net/realms/kerala-police-drone/protocol/openid-connect/token
INGEST__CLIENT_ID=drone-ingest-client
INGEST__CLIENT_SECRET=<KC_CLIENT_SECRET>
INGEST__USERNAME=edge_laptop_01
INGEST__PASSWORD=<per-laptop password>
INGEST__DRONE_ID=DRN-001
INGEST__TLS_VERIFY=true
EDGE_API_KEY=<EDGE_API_KEY from dvas.env>
REQUIRE_EDGE_AUTH=true
ENV=production
```
**When the pod URL changes:** edit `DVAS_CLOUD_URL` and the four `INGEST__*URL` lines (or re-run the installer's "Repair" mode), then restart the app. A `Change Server URL` desktop shortcut that opens this file in Notepad is included.


During development against the **local server** (before the pod exists), use:
```ini
DVAS_CLOUD_URL=http://<ubuntu-server-ip>:3000                     # dashboard nginx: proxies /api/* to the backend
INGEST__URL=http://<ubuntu-server-ip>/ingest/frame                # drone_nginx
INGEST__MISSION_START_URL=http://<ubuntu-server-ip>/ingest/mission/start
INGEST__MISSION_END_URL=http://<ubuntu-server-ip>/ingest/mission/end
INGEST__TOKEN_URL=http://<ubuntu-server-ip>/realms/kerala-police-drone/protocol/openid-connect/token
INGEST__TLS_VERIFY=false
```

### ✅ Done when
- [ ] `build_edge.ps1` produces `dist\` from a clean checkout in one run
- [ ] Copying `dist\` to a machine with no Python and running `Start_DVAS_Edge.vbs` works
- [ ] `dist\` contains no secrets, test videos or documents

---

## Phase E5 — Inno Setup installer

**Goal.** `DVAS_Edge_Setup_<version>.exe` that installs per-user without admin rights and writes the laptop's `edge.env` from a wizard page.
**Prerequisites.** E4.

### E5.1 `dvas_edge/packaging/dvas_edge_installer.iss` (key sections)
```ini
[Setup]
AppName=Kerala Police DVAS Edge Operator
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\DVAS_Edge
PrivilegesRequired=lowest
OutputBaseFilename=DVAS_Edge_Setup_{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
SetupIconFile=static\favicon.ico

[Files]
Source: "dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
Name: "{localappdata}\DVAS_Edge\config"
Name: "{localappdata}\DVAS_Edge\data"

[Icons]
Name: "{autodesktop}\DVAS Edge Operator"; Filename: "{app}\Start_DVAS_Edge.vbs"; WorkingDir: "{app}"; IconFilename: "{app}\static\favicon.ico"
Name: "{autoprograms}\DVAS Edge\Change Server URL"; Filename: "notepad.exe"; Parameters: """{localappdata}\DVAS_Edge\config\edge.env"""

[Run]
Filename: "{app}\Start_DVAS_Edge.vbs"; Description: "Launch DVAS Edge now"; Flags: postinstall shellexec skipifsilent

[Code]
var ServerPage, DronePage: TInputQueryWizardPage;
procedure InitializeWizard;
begin
  ServerPage := CreateInputQueryPage(wpSelectDir, 'DVAS Server', 'Cloud server address',
    'Enter the DVAS server URL given by the control room (e.g. https://abc123-80.proxy.runpod.net)');
  ServerPage.Add('Server URL:', False);
  DronePage := CreateInputQueryPage(ServerPage.ID, 'Drone & uploader', 'Field station identity', '');
  DronePage.Add('Drone ID (e.g. DRN-001):', False);
  DronePage.Add('Uploader username:', False);
  DronePage.Add('Uploader password:', True);
end;
// CurStepChanged(ssPostInstall): write {localappdata}\DVAS_Edge\config\edge.env from the answers,
// skipping the file if it already exists (upgrades keep the laptop's config).
```

The Keycloak **client secret** is injected at build time (`ISCC /DClientSecret=...`) for the test build. Phase 13 replaces this with a per-laptop service account or a public client with PKCE, because any secret inside an installer can be extracted.


### ✅ Done when
- [ ] Installer compiles; installs and uninstalls without a UAC prompt
- [ ] Wizard writes `edge.env`; an upgrade install keeps the existing `edge.env` and data
- [ ] Desktop and Start-menu shortcuts work, including "Change Server URL"

---

## Phase E6 — Testing

**Goal.** The `.exe` works end-to-end, first against the local server, then against the RunPod pod, and can drive the cloud team's 3-drone tests.
**Prerequisites.** E5 (E6.1 can start after E4).

**E6.1 Against the local Ubuntu server** (no pod needed) — config from E4.2, then run the checklist below.

**E6.2 Against the RunPod pod** — reinstall or edit `edge.env` with the pod URL and credentials from the RunPod owner, then run the checklist again.

1. Run `DVAS_Edge_Setup.exe` — no UAC prompt; the wizard asks for the URL, drone ID and uploader account.
2. The desktop icon opens `http://localhost:8765`; log in as `operator_001` → `/edge/dashboard`.
   - A non-operator role is refused (edge build gating).
3. Mission Control:
   - Load a test MP4 + SRT
   - Start the mission → `SERVER SEND: ON` → frames arrive (`dvas-ctl lag` on the pod)
   - Toggle bypass at 30 fps
4. Buffer & Change / Frame Selection pages show live stats; Settings persist across restarts (stored in `%LOCALAPPDATA%\DVAS_Edge\data`).
5. Pull the network cable for 30 s → frames go into the offline buffer and are sent after reconnect; the UI stays usable.
6. Evidence extraction produces an H.264 MP4 (ffmpeg bundled).
7. Windows Defender / SmartScreen: note any warnings. Sign the installer and `python.exe` launcher with the organisation's code-signing certificate before field use.


**E6.3 Support the cloud test runs** (RunPod plan Phases 8 and 11):
- Settings → transmission `bypass_target_fps: 30`, `max_resolution: 720p`, `jpeg_quality: 75`
- Drone IDs `DRN-001..003` (one laptop per drone, or one laptop + the cloud team's load generator for the others)
- Note any 429/5xx in `%LOCALAPPDATA%\DVAS_Edge\data\logs` and share them with the RunPod owner

### ✅ Done when
- [ ] Every checklist item passes on Windows 10 and 11, against the local server and against the pod
- [ ] Offline/online transition recovers without restarting the app
- [ ] The cloud team confirms frames from `DRN-001..003` during Phase 8/11 runs

---

## Phase E7 — Hardening & optional clip upload

**Goal.** Make the `.exe` safe for field trials.
**Prerequisites.** E6.

1. **Credentials (S4).** Replace the shared Keycloak client secret baked into the installer with per-laptop **service-account clients** (client-credentials, revocable per laptop) or a **public client + PKCE** for the human login plus per-laptop uploader users. The RunPod owner creates the Keycloak clients/users; you change the edge code.
2. **Laptop (S10).** BitLocker on the SSD holding the buffer/evidence; standard Windows user; `REQUIRE_EDGE_AUTH=true`; `EDGE_API_KEY` unique per deployment; edge core bound to `127.0.0.1` only.
3. **Code signing.** Sign `DVAS_Edge_Setup.exe` and the launcher with the organisation's code-signing certificate to avoid SmartScreen/antivirus warnings.
4. **Evidence clip upload (optional, F18).** Today clips stay on the laptop SSD, so the control room can't play them.
   - **Edge (you):** an "Upload to Control Room" button after extraction (Evidence Management page), plus auto-upload for confirmed events.
   - **Cloud (RunPod owner):** `POST /api/evidence/{evidence_id}/clip` — multipart, **≤ 90 MB per request** (Cloudflare limit), chunked `part=n/total` for bigger files → MinIO `dvas-evidence`, with SHA-256 recorded for the Section 65B chain of custody.
   - Agree the request format before either side builds it. Schedule it in a later sprint if the test doesn't need it.

### ✅ Done when
- [ ] Per-laptop credentials in use; no shared secret in the installer
- [ ] Signed installer passes SmartScreen on a clean machine
- [ ] (If scheduled) clips uploaded from the edge play in the cloud Evidence page

---

## Appendix — files owned by the edge branch

| Path | Phase | Purpose |
|---|---|---|
| `dvas_edge/edge_api_server.py`, `ingest/edge_sender.py`, `config/settings.yaml`, `.gitignore` | E1 | env-driven host/port/data dir, no secrets, frame_seq/capture_ts |
| `dvas_edge/packaging/edge_launcher.py` | E2 | local gateway |
| `dvas_edge/packaging/build_edge.ps1`, `requirements-edge-win.txt`, `Start_DVAS_Edge.bat`, `Start_DVAS_Edge.vbs` | E4 | portable runtime |
| `dvas_edge/packaging/dvas_edge_installer.iss` | E5 | installer |
| *(shared, via `testing`)* `DVAS_Dashboard/frontend/src/lib/deployment.js`, `App.js`, `Login.js`, `AuthContext.js`, `useJpegPoller.js` | E3 | build-target switch — implemented once by the RunPod owner |