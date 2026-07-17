# Kerala Police Drone Video Analytics System (DVAS) — Windows Edge Laptop

> **Two-machine architecture:**  
> This repository is the **Windows edge laptop** codebase.  
> The Ubuntu ingest server (FastAPI · NGINX · Keycloak · Valkey/Redis · GPU consumer) runs separately on a dedicated Ubuntu machine and communicates with this laptop over a WireGuard VPN tunnel.

---

## Table of Contents

1. [System Architecture](#system-architecture)
2. [What Runs Where](#what-runs-where)
3. [Prerequisites (Windows laptop)](#prerequisites-windows-laptop)
4. [Quick Start — Edge Pipeline](#quick-start--edge-pipeline)
5. [WireGuard VPN Setup](#wireguard-vpn-setup)
6. [Configuration](#configuration)
7. [Sending Frames to Ubuntu (edge_sender.py)](#sending-frames-to-ubuntu-edge_senderpy)
8. [Unit & Integration Tests](#unit--integration-tests)
9. [Analytics Pipeline Scripts](#analytics-pipeline-scripts)
10. [File Structure](#file-structure)
11. [Troubleshooting](#troubleshooting)
12. [Security Notes](#security-notes)

---

## System Architecture

```
 WINDOWS EDGE LAPTOP (field)                    UBUNTU INGEST SERVER (command room / cloud)
 ─────────────────────────────                  ──────────────────────────────────────────
 Drone (RTSP / MP4)                             NGINX :443  (TLS + rate limiting)
       │                                              │
       ▼                                              ▼
 DroneVideoSource / SimulationSource           Ingest API — FastAPI :8000
       │                                              │ JWT validation via Keycloak
       ├──► RollingFrameBuffer (60 s SSD)             ▼
       │                                        Valkey queue (per-drone stream)
       └──► ChangeDetectionEngine (MOG2)              │
                   │                                  ▼
                   ▼                            GPU Consumer (batch inference)
           CognitiveFrameSelector                     │
                   │                                  ├──► Confirmed event → back to laptop
                   ▼                                  └──► Evidence clip stored (SeaweedFS)
           edge_sender.py
                   │  HTTPS POST /ingest/frame
                   │  Bearer JWT (from Keycloak)
                   └──── WireGuard VPN (10.10.20.0/24) ────►

 Edge dashboard (stream_viewer.py):
   stream health · buffer fill · frame-send rate
```

---

## What Runs Where

| Component | Machine | How it runs |
|---|---|---|
| `main.py` / `analytics_worker.py` | **Windows** | Native Python 3.10 |
| `DroneVideoSource` / `DroneSimulationSource` | **Windows** | Native Python |
| `RollingFrameBuffer` | **Windows** | Native Python · local SSD |
| `ChangeDetectionEngine` | **Windows** | Native Python · OpenCV |
| `CognitiveFrameSelector` | **Windows** | Native Python |
| `edge_sender.py` | **Windows** | Native Python · sends to Ubuntu over VPN |
| WireGuard VPN client | **Windows** | WireGuard for Windows (native installer) |
| Edge dashboard (`stream_viewer.py`) | **Windows** | Native Python · OpenCV GUI |
| NGINX | **Ubuntu** | Docker container |
| Keycloak | **Ubuntu** | Docker container |
| Postgres | **Ubuntu** | Docker container |
| Ingest API | **Ubuntu** | Docker container |
| Valkey (Redis replacement) | **Ubuntu** | Docker container |
| GPU consumer | **Ubuntu** | Docker container |
| Evidence storage (SeaweedFS) | **Ubuntu** | Docker container |
| WireGuard gateway | **Ubuntu** | System service (`wg-quick`) |
| Headscale | **Ubuntu** | Docker container |
| step-ca (internal CA) | **Ubuntu** | Docker container |
| Prometheus · Grafana · Alertmanager | **Ubuntu** | Docker containers |

> **No Docker on the edge laptop.** The entire analytics pipeline runs as native Python.  
> Docker files (`docker-compose.yml`, `Dockerfile*`, `.dockerignore`) live on the Ubuntu machine only.

---

## Prerequisites (Windows laptop)

- **Python 3.10** (matches Ubuntu server environment)
- **NVIDIA GPU** (optional but recommended for NVDEC hardware H.264 decode)
  - NVIDIA drivers + CUDA toolkit
  - FFmpeg built with CUVID support (GPU decode via `h264_cuvid`)
- **WireGuard for Windows** — download from [wireguard.com](https://www.wireguard.com/install/)
- **DJI MP4 source files** at `D:\POLICE_DRONE\drone_analytics\DJI_0110.MP4` and `DJI_0111.MP4`
- **step-ca root CA certificate** installed in Windows trust store (once Ubuntu CA is set up)

---

## Quick Start — Edge Pipeline

```powershell
# 1. Clone / navigate to this directory
cd D:\POLICE_DRONE\drone_analytics

# 2. Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Connect to the Ubuntu VPN gateway (see WireGuard VPN Setup below)
#    Confirm: ping 10.10.20.1

# 5. Run the full edge pipeline (simulation mode uses MP4 files)
python main.py
```

The OpenCV viewer window opens showing:
- Live frame stream with motion detection overlay
- Buffer fill percentage
- GPU decoder status
- Frame-send rate to Ubuntu

Press **Esc** to stop.

---

## WireGuard VPN Setup

The edge laptop must connect to the Ubuntu WireGuard gateway before `edge_sender.py` can reach the ingest server.

### Step 1 — Get your laptop's Public Key

1. Open the **WireGuard** app → click the arrow next to **"Add Tunnel"** → **"Add empty tunnel..."** (`Ctrl+N`).
2. WireGuard auto-generates a private key and shows it in the text box as `PrivateKey = ...`.
3. Look **above** the text box — you will see a field labelled **"Public key:"**.
4. **Copy that Public Key** and send it to the Ubuntu admin.
   The admin adds it as a `[Peer]` block on the Ubuntu server (`/etc/wireguard/wg0.conf`).

### Step 2 — Get the Ubuntu server's public key

The Ubuntu admin runs `sudo wg show` on the server. The output looks like:
```
interface: wg0
  public key: yWqdahY3jrunVd7HsVYIl9vo72vPUfrEGm2Serlht38=
  listening port: 51820
```
The value after `public key:` is the **server public key** you need.

### Step 3 — Find the Ubuntu server's current IP address

The Ubuntu admin runs `ip -4 addr show` on the server and looks under the Wi-Fi interface (`wlp0s20f3`). Example:
```
3: wlp0s20f3: ...
    inet 10.199.168.250/24 ...
```
The `inet` value (e.g. `10.199.168.250`) is the Endpoint IP to use.

> ⚠️ **IMPORTANT — The Endpoint IP changes every day** if the Ubuntu machine uses DHCP Wi-Fi.
> Each time it reconnects, the router may assign a different IP.
> **Permanent fix:** Ask the network admin to set a static DHCP reservation for the Ubuntu machine's MAC address in the router settings.
> **Daily workaround:** Run `ip -4 addr show` on Ubuntu each morning, check the new IP under `wlp0s20f3`, and update the `Endpoint` line in your WireGuard tunnel on Windows.

### Step 4 — Configure and activate the tunnel

In the WireGuard "Add empty tunnel" text box, paste the template from `config\wireguard\wg0-client.conf` and fill in the values:

```ini
[Interface]
PrivateKey = <KEEP WHAT WIREGUARD GENERATED — do not change this>
Address    = 10.10.20.2/24      # unique per laptop, assigned by admin

[Peer]
PublicKey           = yWqdahY3jrunVd7HsVYIl9vo72vPUfrEGm2Serlht38=  # Ubuntu server public key
Endpoint            = 10.199.168.250:51820   # Ubuntu current IP:port (update if it changes)
AllowedIPs          = 10.10.20.0/24
PersistentKeepalive = 25
```

Click **Save**, then **Activate**. Confirm connectivity:
```powershell
ping 10.10.20.1
```

> ⚠️ **If ping times out** — Check WireGuard's Transfer stats.
> If it shows `Sent: X KB, Received: 0 B`, the Ubuntu firewall is likely blocking UDP 51820.
> The Ubuntu admin must run: `sudo ufw allow 51820/udp`
> Also verify both laptops are on the **same Wi-Fi network** — routers with "Client Isolation" will block direct laptop-to-laptop traffic.

### Step 5 — Add the ingest server hostname to Windows hosts file

Open **Notepad as Administrator** → `File → Open` → navigate to `C:\Windows\System32\drivers\etc\` → change filter to "All Files" → open `hosts`.

Scroll to the bottom and add this line:
```
10.10.20.1  ingest.dvas.internal
```

> ⚠️ Make sure this line says `10.10.20.1` (the WireGuard VPN IP), **not** any other IP that may have been added previously by Docker Desktop or other software.

Save the file. Confirm:
```powershell
ping ingest.dvas.internal
# Should reply from 10.10.20.1
```

### Step 6 — Install the step-ca root CA certificate

The Ubuntu admin provides a file called `root_ca.crt`. Save it to `D:\POLICE_DRONE\drone_analytics\root_ca.crt`.

Open **PowerShell as Administrator** and run:
```powershell
cd D:\POLICE_DRONE\drone_analytics
Import-Certificate -FilePath root_ca.crt -CertStoreLocation Cert:\LocalMachine\Root
```

If it succeeds, it prints the certificate Thumbprint and Subject.
Then in `config\settings.yaml`, change:
```yaml
tls_verify: false  →  tls_verify: true
```

See [`config/wireguard/wg0-client.conf`](config/wireguard/wg0-client.conf) for the annotated config template.

---

## Configuration

All edge configuration lives in [`config/settings.yaml`](config/settings.yaml).

### Key sections

#### Drone source
```yaml
drone:
  rtsp_url: "rtsp://admin:Admin12345@10.10.32.23:554/h264/ch1/main/av_stream"
  use_gpu: true          # enables NVDEC hardware H.264 decode
  rtsp_transport: "tcp"  # prevents UDP packet-loss corrupting H.264
```

#### Simulation mode (MP4 playback for testing)
```yaml
simulation:
  enabled: true
  video_files:
    - "D:/POLICE_DRONE/drone_analytics/DJI_0110.MP4"
    - "D:/POLICE_DRONE/drone_analytics/DJI_0111.MP4"
  loop: true
  playback_speed: 1.0
```

#### Rolling frame buffer
```yaml
buffer:
  duration_s: 60         # 60-second rolling window on local SSD
  storage_dir: "./buffer_storage"
  jpeg_quality: 85
```

#### Ingest server connection (Ubuntu over VPN)
```yaml
ingest:
  url:               "https://ingest.dvas.internal/ingest/frame"
  mission_start_url: "https://ingest.dvas.internal/ingest/mission/start"
  mission_end_url:   "https://ingest.dvas.internal/ingest/mission/end"
  token_url:         "http://10.10.20.1:8080/realms/kerala-police-drone/protocol/openid-connect/token"
  client_id:         "drone-ingest-client"
  client_secret:     "<paste Keycloak client secret from Ubuntu admin here>"
  username:          "operator_001"
  password:          "Operator@001"
  drone_id:          "DRONE_KL_001"
  mission_id:        "MISSION_DEFAULT"
  tls_verify:        true   # requires step-ca root CA installed in Windows trust store
```

> ⚠️ **Two places to update the client secret** — When the Ubuntu admin gives you the new Keycloak
> client secret, you must paste it in **both** of these locations:
> 1. `config/settings.yaml` → `ingest.client_secret` (used by `main.py` and the full pipeline)
> 2. `ingest/edge_sender.py` → line ~45 → `CLIENT_SEC = os.getenv("KC_CLIENT_SEC", "...")` (used when running `edge_sender.py` standalone from the command line)
>
> Updating only one of them will cause `401 Unauthorized` errors when testing standalone.

Override any setting at runtime via environment variables:
```powershell
$env:SIMULATION__ENABLED = "false"   # force RTSP mode
$env:SIMULATION__PLAYBACK_SPEED = "2.0"
$env:KC_CLIENT_SEC = "your-secret"   # override client secret without editing files
python main.py
```

---

## Sending Frames to Ubuntu (edge_sender.py)

`ingest/edge_sender.py` is the authenticated HTTPS client that sends selected frames from this laptop to the Ubuntu ingest server. It is called internally by the pipeline but can also be run standalone for testing.

```powershell
pip install httpx

# Send a single test frame (VPN must be active)
python ingest\edge_sender.py --frame test_frame.jpg

# Continuous loop at 5 FPS (load test / simulation)
python ingest\edge_sender.py --loop --fps 5 --drone-id DRONE_KL_001 --frame test_frame.jpg
```

All URLs default to the Ubuntu VPN addresses. Override via environment variables if needed:
```powershell
$env:INGEST_URL   = "https://ingest.dvas.internal/ingest/frame"
$env:TOKEN_URL    = "http://10.10.20.1:8080/realms/kerala-police-drone/protocol/openid-connect/token"
$env:TLS_VERIFY   = "true"   # after root CA is installed
python ingest\edge_sender.py --frame test_frame.jpg
```

### End-to-end validation checklist (Phase 8)

Run in order — each step proves one layer:

| Step | Command | Proves |
|------|---------|--------|
| 1 | `ping 10.10.20.1` | WireGuard tunnel is up |
| 2 | `curl https://ingest.dvas.internal/health` | NGINX reachable, TLS trusted |
| 3 | `python ingest\edge_sender.py --frame test_frame.jpg` | JWT obtained, frame reaches ingest API |
| 4 | Check Ubuntu GPU consumer logs | Frame queued → batch inference ran |
| 5 | `python main.py` (simulation mode) | Full edge pipeline runs natively on Windows |

---

## Unit & Integration Tests

All tests run natively on the Windows laptop — no Docker or VPN needed.

```powershell
cd D:\POLICE_DRONE\drone_analytics
.venv\Scripts\python.exe -m pytest
```

| Test file | What it covers |
|-----------|---------------|
| `tests/test_change_detection.py` | MOG2 engine — event firing, motion scoring |
| `tests/test_cognitive_frame_selector.py` | Budget-aware frame selection |
| `tests/test_rolling_frame_buffer.py` | mmap slab write/read, segment extraction |
| `tests/test_drone_video_source.py` | MP4 decode, frame callback, loop/stop |
| `tests/test_integration.py` | End-to-end: SimulationSource → ChangeDetection → Buffer |

---

## Analytics Pipeline Scripts

These run locally on the Windows laptop — no server connection needed.

### `scripts/verify_pipeline.py` — Smoke test (no server needed)
```powershell
.venv\Scripts\python.exe scripts\verify_pipeline.py
```
Runs 4 checks: MP4 readability, change-detection on real DJI footage, buffer pipeline, config loading.

### `scripts/test_headless.py` — 4K pipeline integration test
```powershell
.venv\Scripts\python.exe scripts\test_headless.py
```
Runs full pipeline headlessly for 15 seconds at 4× speed (~480 frames). Prints live stats per second.

### `scripts/extract_evidence_clip.py` — Evidence clip extraction
```powershell
# Extract a 10-second clip from 30 seconds ago
.venv\Scripts\python.exe scripts\extract_evidence_clip.py `
    --start_offset_s 30 `
    --duration_s 10 `
    --output .\evidence_clip.mp4 `
    --fps 15
```

---

## File Structure

```
drone_analytics\                              ← Windows edge laptop repository root
│
├── main.py                                   ← Unified edge pipeline entry point
├── analytics_worker.py                       ← Headless pipeline variant
├── requirements.txt                          ← Python dependencies
├── pyproject.toml                            ← Pytest configuration
├── .gitignore                                ← Excludes keys, certs, MP4s, buffer
│
├── config\
│   ├── config.py                             ← Config loader (Pydantic)
│   ├── settings.yaml                         ← Edge pipeline + ingest server URLs
│   └── wireguard\
│       └── wg0-client.conf                   ← WireGuard client config template
│
├── src\                                      ← Analytics pipeline modules
│   ├── buffer\
│   │   └── rolling_frame_buffer.py           ← mmap-based 60 s rolling buffer (SSD)
│   ├── change_detection\
│   │   ├── change_detection_engine.py        ← MOG2 background subtraction
│   │   └── change_event.py                   ← ChangeEvent data class
│   ├── cognitive_frame_selector\
│   │   └── cognitive_frame_selector.py       ← Budget-aware intelligent frame selector
│   ├── metrics\
│   │   └── prometheus_metrics.py             ← Edge Prometheus metrics server
│   ├── utils\
│   │   ├── logger.py                         ← Structured logger
│   │   └── gpu_utils.py                      ← GPU / NVDEC detection
│   └── video\
│       ├── drone_video_source.py             ← Live RTSP / USB source
│       ├── drone_simulation_source.py        ← MP4 playback source
│       ├── frame_interpolator.py             ← FPS interpolation for viewer
│       └── stream_viewer.py                  ← OpenCV HUD viewer
│
├── ingest\
│   ├── edge_sender.py                        ← HTTPS frame sender → Ubuntu ingest server
│   └── README.md                             ← Ingest subsystem docs (edge side)
│
├── scripts\                                  ← Local utility scripts
│   ├── test_headless.py
│   ├── verify_pipeline.py
│   └── extract_evidence_clip.py
│
├── tests\                                    ← Pytest suite (runs locally, no server)
│   ├── conftest.py
│   ├── test_change_detection.py
│   ├── test_cognitive_frame_selector.py
│   ├── test_drone_video_source.py
│   ├── test_integration.py
│   └── test_rolling_frame_buffer.py
│
├── buffer_storage\                           ← mmap slabs — runtime only (gitignored)
├── logs\                                     ← Edge pipeline logs (gitignored)
├── DJI_0110.MP4                              ← Source footage (gitignored)
└── DJI_0111.MP4                              ← Source footage (gitignored)
```

> **Note:** `docker-compose.yml`, `Dockerfile*`, `ingest/api/`, `ingest/nginx/`, `ingest/certs/`,
> `ingest/scripts/`, and `config/settings.docker.yaml` have been **removed** from this directory.
> They belong on the Ubuntu ingest server machine.

---

## Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| `ping 10.10.20.1` times out | WireGuard tunnel not active | Open WireGuard app and click **Activate**; check tunnel status shows green |
| WireGuard Transfer shows `Received: 0 B` | Ubuntu firewall blocking UDP 51820 | Ubuntu admin runs `sudo ufw allow 51820/udp`; also confirm both machines are on the same Wi-Fi and the router doesn't have "Client Isolation" enabled |
| WireGuard handshake never completes (log: `did not complete after 5 seconds`) | Wrong Endpoint IP or firewall blocking | Verify the `Endpoint` in WireGuard config matches the current Ubuntu Wi-Fi IP (`ip -4 addr show` on Ubuntu → look under `wlp0s20f3`) |
| Ping worked yesterday but not today | Ubuntu's Wi-Fi IP changed overnight (DHCP) | Run `ip -4 addr show` on Ubuntu, find new IP under `wlp0s20f3`, update `Endpoint` in Windows WireGuard tunnel. **Permanent fix:** set a static DHCP reservation in the router for the Ubuntu machine |
| `ping ingest.dvas.internal` fails | Wrong IP in Windows hosts file | Open `C:\Windows\System32\drivers\etc\hosts` as Administrator and ensure the entry reads `10.10.20.1  ingest.dvas.internal` (not any other IP) |
| `401 Unauthorized` from Keycloak | Wrong client secret or credentials | Update the Keycloak client secret in **both** `config/settings.yaml` (`ingest.client_secret`) AND `ingest/edge_sender.py` (line ~45 `CLIENT_SEC = os.getenv(...)`) |
| SSL certificate error / TLS verification failed | step-ca root CA not installed in Windows | Run as Administrator: `Import-Certificate -FilePath root_ca.crt -CertStoreLocation Cert:\LocalMachine\Root`; then set `tls_verify: true` in `settings.yaml` |
| NVDEC not used | GPU driver or CUDA not installed | Confirm `use_gpu: true` in `settings.yaml` and NVIDIA drivers installed; pipeline falls back to CPU decode automatically |
| MP4 file not found | Wrong file path | Confirm `DJI_0110.MP4` exists at `D:\POLICE_DRONE\drone_analytics\` and paths in `settings.yaml → simulation.video_files` match exactly |
| OpenCV window doesn't open | Headless / remote desktop environment | Use `analytics_worker.py` instead of `main.py` for headless (no-GUI) mode |
| Tests fail with import errors | Virtual environment not activated | Run `.venv\Scripts\activate` first, then re-run pytest |

---

## Security Notes

- **Rotate all credentials after pilot** — `client_secret`, `Operator@001` passwords, Keycloak admin password
- **WireGuard private keys** must never be committed — `.gitignore` enforces this
- **`tls_verify: false`** in `settings.yaml` is temporary — flip to `true` once the step-ca root CA is installed in the Windows trust store
- **Split tunnelling** — only VPN subnet `10.10.20.0/24` routes through WireGuard; all other traffic (internet, field Wi-Fi) uses the laptop's normal interface
- Each laptop gets a **unique VPN IP** and a **unique WireGuard key pair** — decommission by revoking the Headscale node on Ubuntu
