# Kerala Police Drone Video Analytics — Ubuntu Ingest Server

> **This is the Ubuntu ingest server repo.**
> The Windows edge laptop runs a separate native Python pipeline. See `walkthrough.md` for what was changed on the Windows side and `plan (2).md` for the full two-machine implementation plan.

---

## Architecture Overview

```
Field (Windows Edge Laptop)                    Command Room (This Machine — Ubuntu)
─────────────────────────────────────          ─────────────────────────────────────────────────
Drone RTSP stream / MP4 file                   WireGuard VPN gateway  (wg-quick@wg0, port 51820)
  │                                              │
  ▼                                              │  port 443 (NGINX — only VPN-reachable port)
DroneVideoSource / DroneSimulationSource       Keycloak JWT validation
  │                                              │
  ▼                                              ▼
RollingFrameBuffer (60 s, local SSD)          Ingest API (FastAPI) ──► Valkey queue
  │                                              │
  ▼                                              ▼
ChangeDetectionEngine (MOG2)                  GPU Consumer (batch inference)
  │                                              │
  ▼                                              ├──► Confirmed event → signal back to laptop
CognitiveFrameSelector                         │
  │                                              └──► Evidence clips → SeaweedFS storage
  └──► edge_sender.py ──(HTTPS + JWT)──────────►
       over WireGuard VPN
```

---

## What Runs on This Machine (14 containers)

| Service | Container | Purpose |
|---|---|---|
| NGINX | `drone_nginx` | TLS reverse proxy — only port exposed to VPN |
| Keycloak | `drone_keycloak` | JWT issuer — authenticates every Windows laptop |
| PostgreSQL | `drone_postgres` | Keycloak database |
| Ingest API | `drone_ingest_api` | Receives frames, validates JWT, queues to Valkey |
| Valkey | `drone_valkey` | Frame queue (BSD-3-Clause Redis replacement) |
| SeaweedFS Master | `drone_seaweed_master` | Evidence storage master |
| SeaweedFS Volume | `drone_seaweed_volume` | Evidence storage volume node |
| SeaweedFS Filer | `drone_seaweed_filer` | Evidence file access API |
| Headscale | `drone_headscale` | VPN control plane — manages laptop enrollment |
| step-ca | `drone_step_ca` | Internal CA — issues TLS certs for NGINX |
| Prometheus | `drone_prometheus` | Metrics collection |
| Grafana | `drone_grafana` | Monitoring dashboards (port 3000) |
| Alertmanager | `drone_alertmanager` | Alert routing |

---

## Ubuntu Server Quick Start

Follow these steps **in order** on the Ubuntu machine. Steps 1–3 are one-time setup; Step 4 onwards is the normal startup path.

---

### Step 1 — Bootstrap Ubuntu Prerequisites

Run this once on a fresh Ubuntu 22.04 LTS install. It installs Docker, NVIDIA drivers, NVIDIA Container Toolkit, WireGuard, and UFW firewall rules.

```bash
git clone <this-repo> drone_analytics
cd drone_analytics

chmod +x ingest/scripts/setup_ubuntu.sh
sudo ./ingest/scripts/setup_ubuntu.sh
```

> **Note:** If NVIDIA drivers are newly installed, the script will warn you to reboot. After rebooting, re-run the script — it is idempotent and skips already-completed steps.

Verify GPU access from Docker:

```bash
docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi
```

---

### Step 2 — Configure WireGuard Gateway

```bash
# The setup script already generated /etc/wireguard/server-private.key
# Copy and edit the gateway config template:
sudo cp config/wireguard/wg0-server.conf /etc/wireguard/wg0.conf
sudo nano /etc/wireguard/wg0.conf
```

Inside the file, make two edits:
1. Replace `<PASTE_PRIVATE_KEY_HERE>` with the contents of `/etc/wireguard/server-private.key`
2. Replace `<ETH_INTERFACE>` with your real network interface (check: `ip route | grep default | awk '{print $5}'`)

Start the VPN gateway:

```bash
sudo systemctl enable --now wg-quick@wg0
sudo wg show    # Should show: interface wg0, listening port 51820
```

Print the server public key — every Windows laptop needs this:

```bash
cat /etc/wireguard/server-public.key
```

---

### Step 3 — Set Up Environment File

```bash
cp ingest/.env.example .env
nano .env    # Fill in real passwords — NEVER commit this file
```

Key values to change from defaults:

| Variable | What to set |
|---|---|
| `KEYCLOAK_ADMIN_PASSWORD` | Strong admin password |
| `POSTGRES_PASSWORD` | Strong DB password |
| `STEPCA_PASSWORD` | CA key protection password |
| `GRAFANA_ADMIN_PASSWORD` | Grafana UI password |
| `ALERT_EMAIL_PASSWORD` | SMTP app password for alert emails |

---

### Step 5 — Start All Services

```bash
docker compose up -d
docker compose ps           # All services should show "healthy" or "running"
docker compose logs setup   # Watch Keycloak bootstrap — should end with "Realm configured"
```

Expected state after ~2 minutes:

```
drone_postgres       healthy
drone_keycloak       healthy
drone_valkey         healthy
drone_headscale      running
drone_step_ca        healthy
drone_ingest_api     healthy
drone_nginx          running
drone_seaweed_master running
drone_seaweed_volume running
drone_seaweed_filer  running
drone_prometheus     running
drone_grafana        running
drone_alertmanager   running
drone_setup          exited (0)   <- one-time setup container, exit 0 = success
```

---

### Step 6 — Issue TLS Certificate for NGINX

After `step-ca` first starts it prints the CA root fingerprint. Issue a cert and place it where NGINX expects it:

```bash
# Get root CA certificate — copy this file to every Windows laptop
docker compose exec step-ca cat /home/step/certs/root_ca.crt > root_ca.crt

# Issue TLS certificate for the ingest server hostname
docker compose exec step-ca step ca certificate \
  ingest.dvas.internal ingest.crt ingest.key \
  --ca-url https://localhost:9000 \
  --root /home/step/certs/root_ca.crt

# Place certs where NGINX expects them
cp ingest.crt ingest/certs/ingest.crt
cp ingest.key ingest/certs/ingest.key

# Restart NGINX to load the new certificate
docker compose restart nginx

# Verify TLS is working
curl -k https://ingest.dvas.internal/health
```

---

## Connecting a Windows Edge Laptop

**🚨 CRITICAL NETWORKING CHOICE 🚨**
If your Ubuntu Ingest Server is connected to a **Mobile Hotspot** or a strict **Corporate Wi-Fi** without Port Forwarding, standard WireGuard will **not work** due to NAT firewalls blocking incoming connections. 
* **Scenario A (Production/Mobile/CGNAT):** Use **Tailscale** (Recommended for easiest deployment).
* **Scenario B (Server has Public IP or Port Forwarding):** Use **Native WireGuard**.

---

### Scenario A: Connect via Tailscale (Easiest / Mobile Hotspot)

If using a mobile hotspot or corporate network without port forwarding, bypass WireGuard completely.

1. Create a free account at [tailscale.com](https://tailscale.com/).
2. Install the **Tailscale app** on **BOTH** the Ubuntu Server and the Windows Edge Laptop.
3. Log into both apps using the same account.
4. Tailscale will automatically assign your Ubuntu server a `100.x.x.x` IP address and punch through any mobile firewalls.
5. **Important:** In all the steps below (and in your Windows `hosts` file), replace `10.10.20.1` with the Ubuntu server's Tailscale `100.x.x.x` IP address.

---

### Scenario B: Connect via Native WireGuard (Server has Public IP/Port Forwarding)

Do this on each Windows laptop if your server has port 51820 UDP exposed to the internet.

#### 1. Install WireGuard for Windows
Download from [wireguard.com](https://www.wireguard.com/install/). Open the app → **Add Tunnel** → **Create from scratch**. 

#### 2. Create the Tunnel Config
Copy the **Public Key** it generates for your laptop. Then fill in the config:

```ini
[Interface]
PrivateKey = <generated by WireGuard app — click Generate>
Address    = 10.10.20.<N>/24    # Admin assigns a unique IP (e.g., 10.10.20.2)
DNS        = 10.10.20.1

[Peer]
PublicKey           = <contents of /etc/wireguard/server-public.key on Ubuntu>
Endpoint            = <Ubuntu server PUBLIC IP or DDNS hostname>:51820
AllowedIPs          = 10.10.20.0/24
PersistentKeepalive = 25
```

#### 3. Register the Laptop on the Ubuntu Server
On your Ubuntu server, append the Windows laptop's Public Key to `/etc/wireguard/wg0.conf`:

```bash
sudo bash -c 'cat <<EOF >> /etc/wireguard/wg0.conf

[Peer]
# Windows Edge Laptop
PublicKey = <PASTE_WINDOWS_LAPTOP_PUBLIC_KEY>
AllowedIPs = 10.10.20.<N>/32
EOF'

sudo systemctl restart wg-quick@wg0
```

#### 4. Activate the Tunnel
Click **Activate** in the Windows WireGuard app, then verify:
```powershell
ping 10.10.20.1
```

### 4. Install the step-ca Root CA Certificate

Copy `root_ca.crt` from the Ubuntu server to the laptop, then run as Administrator in PowerShell:

```powershell
Import-Certificate -FilePath root_ca.crt -CertStoreLocation Cert:\LocalMachine\Root
```

This makes the laptop trust NGINX's TLS certificate without warnings.

### 5. Add the Ingest Server Hostname

Edit `C:\Windows\System32\drivers\etc\hosts` as Administrator and add:

```
10.10.20.1  ingest.dvas.internal
```
*(Note: If you used Tailscale in Scenario A, replace `10.10.20.1` with the server's `100.x.x.x` Tailscale IP).*

### 6. Update `config/settings.yaml` on the Laptop

```yaml
ingest:
  url:               "https://ingest.dvas.internal/ingest/frame"
  mission_start_url: "https://ingest.dvas.internal/ingest/mission/start"
  mission_end_url:   "https://ingest.dvas.internal/ingest/mission/end"
  token_url:         "http://10.10.20.1:8080/realms/kerala-police-drone/protocol/openid-connect/token"
  client_id:         "drone-ingest-client"
  client_secret:     "<paste from Keycloak UI or setup_keycloak.py output>"
  username:          "operator_001"
  password:          "<operator password set during Keycloak bootstrap>"
  drone_id:          "DRONE_KL_001"    # unique per laptop
  tls_verify:        true              # set true once root_ca.crt is installed
```

### 7. Start the Edge Pipeline

```powershell
cd D:\POLICE_DRONE\DRONE_ANALYTICS
venv\Scripts\activate
python main.py
```

If the OpenCV window opens and frame-send logs appear, the laptop is fully connected.

---

## End-to-End Validation Checklist

Run in order — each step proves one layer of the stack.

```bash
# 1. VPN tunnel up (run FROM the Windows laptop)
ping 10.10.20.1

# 2. NGINX reachable + TLS trusted (run FROM the Windows laptop)
curl https://ingest.dvas.internal/health

# 3. Keycloak JWT token obtained (run FROM the Windows laptop)
python ingest/edge_sender.py --frame test_frame.jpg

# 4. Frame arrived in Valkey queue (check ON Ubuntu)
docker compose exec valkey valkey-cli xlen drone:frames:DRONE_KL_001

# 5. Grafana dashboard accessible (from any VPN-connected machine)
curl http://10.10.20.1:3000

# 7. Full simulation run (FROM Windows laptop, no real drone needed)
#    In config/settings.yaml set:  simulation.enabled: true
python main.py
```

---

## Service URLs (from within VPN)

| Service | URL | Credentials |
|---|---|---|
| Ingest API (HTTPS) | `https://ingest.dvas.internal/` | JWT Bearer token |
| Ingest API docs (Swagger) | `https://ingest.dvas.internal/docs` | None |
| Keycloak Admin | `http://10.10.20.1:8080/admin` | `admin` / `.env` `KEYCLOAK_ADMIN_PASSWORD` |
| Grafana | `http://10.10.20.1:3000` | `admin` / `.env` `GRAFANA_ADMIN_PASSWORD` |
| Headscale | `http://10.10.20.1:8081` | CLI only (`docker compose exec headscale ...`) |
| SeaweedFS Filer | `http://127.0.0.1:8888` (Ubuntu local only) | None |
| Prometheus | `http://127.0.0.1:9090` (Ubuntu local only) | None |
| step-ca | `https://127.0.0.1:9000` (Ubuntu local only) | CA password from `.env` |

---

## Directory Structure

```
drone_analytics/                    <- Ubuntu ingest server repo root
|
+-- docker-compose.yml              <- All 14 Ubuntu services
+-- .dockerignore
+-- .gitignore
|
+-- config/
|   +-- wireguard/
|       +-- wg0-server.conf         <- WireGuard GATEWAY template (deploy to /etc/wireguard/wg0.conf)
|
+-- headscale/
|   +-- config.yaml                 <- Headscale VPN control plane config
|   +-- acl.json                    <- VPN access control (edge laptops -> port 443 only)
|
+-- ingest/
|   +-- .env.example                <- Environment variable template (cp to .env, fill secrets)
|   +-- api/                        <- FastAPI ingest server
|   |   +-- main.py                 <- FastAPI app (frame ingest, mission, status endpoints)
|   |   +-- config.py               <- Hostname=ingest.dvas.internal, Valkey URL
|   |   +-- auth.py                 <- Keycloak JWT validation
|   |   +-- models.py               <- Pydantic models
|   |   +-- queue_manager.py        <- Valkey stream publish/consume
|   |   +-- requirements.txt
|   |   +-- Dockerfile
|   +-- nginx/
|   |   +-- default.conf            <- NGINX: TLS, rate limiting, JWT header guard, proxy
|   +-- certs/                      <- TLS cert files (populated by step-ca — NOT committed)
|   +-- scripts/
|       +-- setup_ubuntu.sh         <- Ubuntu bootstrap (Docker, NVIDIA, WireGuard, UFW)
|       +-- setup_keycloak.py       <- Keycloak realm/client/user bootstrapper
|       +-- verify.py               <- Service health check
|
+-- monitoring/
|   +-- prometheus.yml              <- Prometheus scrape config
|   +-- alertmanager.yml            <- Alertmanager email routing
|   +-- rules/
|   |   +-- dvas_alerts.yml         <- Alert rules (API down, GPU restart, VPN disconnected)
|   +-- grafana/
|       +-- provisioning/
|           +-- datasources/
|           |   +-- prometheus.yml  <- Auto-provision Prometheus datasource
|           +-- dashboards/
|               +-- dashboard.yml   <- Dashboard file loader config
|
+-- walkthrough.md                  <- Windows edge laptop changes reference
+-- plan (2).md                     <- Full two-machine implementation plan
+-- README.md                       <- This file
```

---

## Onboarding a New Windows Laptop (SOP)

1. **Choose VPN Path**: Tailscale (for mobile hotspots/CGNAT) OR Native WireGuard (if port 51820 is publicly accessible).
2. **Assign VPN IP** (If Native WireGuard): Admin picks next available in `10.10.20.0/24` (`.2`, `.3`, `.4`, ...) and adds the laptop's public key to the Ubuntu server's `/etc/wireguard/wg0.conf`.
3. **Copy `root_ca.crt`** from Ubuntu to the laptop and install (PowerShell, as Administrator):
   ```powershell
   Import-Certificate -FilePath root_ca.crt -CertStoreLocation Cert:\LocalMachine\Root
   ```
4. **Add `/etc/hosts` entry** on the laptop: `10.10.20.1  ingest.dvas.internal` (Or Tailscale `100.x.x.x` IP).
5. **Configure `settings.yaml`** with: Keycloak client secret, operator credentials, unique `drone_id`.
6. **Log the action**: laptop ID, VPN IP, officer name, date.
7. **To offboard**: Remove peer from `/etc/wireguard/wg0.conf` (or revoke Tailscale node) and revoke Keycloak account.

---

## Useful Operational Commands

```bash
# Service management
docker compose up -d                    # Start all services
docker compose down                     # Stop (keeps volumes / data)
docker compose down -v                  # Full reset — DELETES ALL DATA
docker compose restart nginx            # Restart a single service
docker compose ps                       # Health status of all containers

# Logs
docker compose logs -f ingest-api       # Live ingest API logs
docker compose logs -f keycloak         # Keycloak startup / auth logs
docker compose logs setup               # One-time Keycloak bootstrap result

# VPN
sudo wg show                            # WireGuard status + active peers
sudo systemctl status wg-quick@wg0      # WireGuard service status

# Headscale (VPN node management)
docker compose exec headscale headscale nodes list
docker compose exec headscale headscale nodes delete --identifier <id>

# Valkey queue diagnostics
docker compose exec valkey valkey-cli xlen drone:frames:DRONE_KL_001
docker compose exec valkey valkey-cli xinfo stream drone:frames:DRONE_KL_001

# TLS cert renewal (step-ca)
docker compose exec step-ca step ca renew \
  /etc/ssl/certs/ingest.crt /etc/ssl/private/ingest.key
docker compose restart nginx
```

---

## Known Gaps and Pending Items

Items from `plan (2).md` **not yet implemented**, to address before district-level rollout:

| Gap | Plan Phase | Action needed |
|---|---|---|
| Grafana dashboard JSON missing | Phase 9 | Create `dvas_dashboard.json` in `monitoring/grafana/provisioning/dashboards/` |
| WireGuard Prometheus exporter not running | Phase 9 | Deploy `MindFlavor/prometheus_wireguard_exporter` on the Ubuntu host; uncomment scrape target in `prometheus.yml` |
| Valkey queue depth alert commented out | Phase 9 | Deploy `oliver006/redis_exporter` alongside Valkey; uncomment `IngestQueueOverload` rule in `dvas_alerts.yml` |
| Ubuntu host metrics (node exporter) not scraped | Phase 9 | `sudo apt install prometheus-node-exporter`; uncomment node scrape in `prometheus.yml` |
| Ingest API multi-replica scaling | Phase 11 | Add replica count + upstream block to `docker-compose.yml` for 100-laptop fleet |
| Gateway redundancy (warm standby) | Phase 12 | Provision second Ubuntu VPN gateway before district deployment |

---

## Security Notes

- **`/etc/wireguard/server-private.key`** — never commit, never copy outside the server.
- **`.env`** — never commit. Contains Keycloak admin password, DB password, CA password.
- **`ingest/certs/ingest.crt` and `ingest.key`** — never commit. Rotate with `step ca renew`.
- **Keycloak port 8080** — in production, restrict to VPN interface: change `0.0.0.0:8080` → `10.10.20.1:8080` in `docker-compose.yml`.
- **Grafana port 3000** — in production, restrict to VPN interface: change `0.0.0.0:3000` → `10.10.20.1:3000`.
- **Rotate** `client_secret` in Keycloak and update all laptops' `settings.yaml` after the pilot.
