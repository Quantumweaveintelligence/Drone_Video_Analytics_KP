# Implementation Plan: Windows Edge Laptop + Ubuntu Ingest Server

**Revision reason:** Client requirement — edge (operator) laptops must remain on Windows. The central ingest server runs on Ubuntu. All open source software throughout.

---

## How the system works in this configuration

This section exists because the split between Windows and Ubuntu maps directly onto a split in what the code already does — nothing in the codebase needs to be redesigned, only configured for two separate machines instead of one.

### What the Windows edge laptop does

The Windows laptop is the first intelligent layer. It sits next to the drone operator in the field. It runs the entire computer-vision pipeline as a native Python process — no Docker needed on the edge at all.

```
Drone (RTSP live stream or MP4 file)
        │
        ▼
DroneVideoSource / DroneSimulationSource     ← decodes frames (NVDEC if GPU present, CPU fallback)
        │
        ├──► RollingFrameBuffer              ← stores last 60 s to local SSD, always overwriting
        │
        └──► ChangeDetectionEngine          ← MOG2 background subtraction, marks candidate timestamps
                    │
                    ▼
            CognitiveFrameSelector          ← scores frames (motion, edges, brightness)
                    │
                    ▼
            edge_sender.py                  ← HTTPS POST with JWT to Ubuntu ingest server over VPN
```

When the Ubuntu GPU confirms an event, a signal comes back to the Windows laptop over the same VPN. The laptop then extracts a pre-event + post-event video clip from its rolling buffer and uploads it to the Ubuntu evidence storage. The rolling buffer is the only reason the clip can include what happened *before* the AI noticed anything — it is always recording, always overwriting, and a clip is only extracted when the central system says something happened.

The edge dashboard (`stream_viewer.py`) shows the operator stream health, buffer status, and frame-send rate — not analytics results, which are a command-room concern.

### What the Ubuntu ingest server does

The Ubuntu machine never sees raw drone video. It receives only the small number of cognitively selected frames the Windows laptop chose to send. Everything on the Ubuntu side runs in Docker Compose.

```
Selected frames arrive (HTTPS → NGINX → Keycloak JWT check → Ingest API)
        │
        ▼
Valkey queue (per-drone stream partition)
        │
        ▼
GPU consumer (batch inference across all active drones)
        │
        ├──► Confirmed event → signal sent back to Windows edge laptop
        │
        └──► Evidence clip received from Windows → stored in Ceph/SeaweedFS (immutable)
```

Keycloak issues the JWT the Windows laptop uses for every HTTPS call. Postgres backs Keycloak. Prometheus scrapes metrics from every container. Grafana visualises them. Alertmanager routes alerts. NGINX is the only port exposed to the VPN — everything else is internal to the Docker network.

### How the two machines reach each other

The Ubuntu server runs a WireGuard gateway. Each Windows laptop dials outbound to that gateway when it starts — outbound connections work through any network the laptop happens to be on (field Wi-Fi, 4G hotspot, ethernet at a venue). Once the tunnel is up, the laptop gets a stable private VPN IP and sees the Ubuntu ingest server as if they were on the same LAN.

Headscale (also on Ubuntu) is the control plane: it manages which laptops are enrolled, what their VPN IPs are, and handles revocation when a laptop is decommissioned. step-ca (also on Ubuntu) is the internal certificate authority: it issues the TLS certificate for the ingest server's internal hostname, and renews it automatically so the manual `gen_certs.ps1` step disappears.

---

## What runs where — summary table

| Component | Machine | How it runs |
|---|---|---|
| `main.py` / `analytics_worker.py` | Windows | Native Python 3.10 |
| `DroneVideoSource` / `DroneSimulationSource` | Windows | Native Python |
| `RollingFrameBuffer` | Windows | Native Python · local SSD |
| `ChangeDetectionEngine` | Windows | Native Python · OpenCV |
| `CognitiveFrameSelector` | Windows | Native Python |
| `edge_sender.py` | Windows | Native Python |
| WireGuard VPN client | Windows | WireGuard for Windows (native installer) |
| Edge dashboard | Windows | Native Python · OpenCV GUI |
| NGINX | Ubuntu | Docker container |
| Keycloak | Ubuntu | Docker container |
| Postgres | Ubuntu | Docker container |
| Ingest API (`ingest/api/main.py`) | Ubuntu | Docker container |
| Valkey (Redis replacement) | Ubuntu | Docker container |
| GPU consumer (`gpu_consumer.py`) | Ubuntu | Docker container |
| Evidence storage (Ceph or SeaweedFS) | Ubuntu | Docker container |
| WireGuard gateway | Ubuntu | System service (`wg-quick`) |
| Headscale | Ubuntu | Docker container |
| step-ca | Ubuntu | Docker container |
| Prometheus | Ubuntu | Docker container |
| Grafana | Ubuntu | Docker container |
| Alertmanager | Ubuntu | Docker container |

---

## Open source toolchain

| Layer | Tool | License | Replaces |
|---|---|---|---|
| VPN data plane | WireGuard | GPLv2 | — |
| VPN control plane | Headscale | BSD-3-Clause | Tailscale hosted service |
| VPN client (Windows) | WireGuard for Windows | GPLv2 | — |
| Internal CA | step-ca (Smallstep) | Apache-2.0 | `gen_certs.ps1` one-off certs |
| Message queue | Valkey | BSD-3-Clause | Redis (licensing uncertainty) |
| Evidence storage | SeaweedFS or Ceph | Apache-2.0 / LGPL | MinIO (maintenance mode) |
| Reverse proxy | NGINX OSS | BSD-2-Clause | Already in stack |
| Auth | Keycloak | Apache-2.0 | Already in stack |
| Metrics | Prometheus | Apache-2.0 | Already partially integrated |
| Dashboards | Grafana OSS | AGPLv3 | New addition |
| Alerting | Alertmanager | Apache-2.0 | New addition |

---

## Phase 0 — Decisions before writing a single line of config

- [ ] Confirm the Ubuntu server hardware (or VM) and its location — this machine is the VPN gateway, the ingest server, and the GPU analytics node; during the pilot it can be one box, for production these roles may split.
- [ ] Confirm the Ubuntu server has an NVIDIA GPU (required for `gpu_consumer.py`).
- [ ] Choose an internal hostname for the ingest server, e.g. `ingest.dvas.internal` — this will be baked into the TLS certificate and Keycloak's issuer URL, so changing it later is painful.
- [ ] Reserve a VPN subnet, e.g. `10.10.20.0/24`, where the Ubuntu server is `10.10.20.1` and each Windows laptop gets an address in the same range.
- [ ] Decide who operationally owns Headscale node registration — this maps to the system administrator role in the access control design.

---

## Phase 1 — Ubuntu server: base installation

- [ ] Install Ubuntu 22.04 LTS (ships Python 3.10 by default; matches the edge codebase).
- [ ] Install system packages:
  ```bash
  sudo apt update && sudo apt upgrade -y
  sudo apt install -y build-essential git curl wireguard
  ```
- [ ] Install NVIDIA drivers and CUDA toolkit:
  ```bash
  sudo ubuntu-drivers autoinstall
  # then CUDA from https://developer.nvidia.com/cuda-downloads (select Ubuntu 22.04)
  ```
- [ ] Install Docker (not Docker Desktop — the server edition):
  ```bash
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker $USER
  sudo apt install -y docker-compose-plugin
  ```
- [ ] Install NVIDIA Container Toolkit so the GPU consumer container can reach the GPU:
  ```bash
  sudo apt install -y nvidia-container-toolkit
  sudo nvidia-ctk runtime configure --runtime=docker
  sudo systemctl restart docker
  ```
- [ ] Verify: `docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi` should show the GPU.

---

## Phase 2 — Ubuntu server: WireGuard gateway

- [ ] Generate the gateway keypair:
  ```bash
  wg genkey | tee /etc/wireguard/server-private.key | wg pubkey > /etc/wireguard/server-public.key
  chmod 600 /etc/wireguard/server-private.key
  ```
- [ ] Create `/etc/wireguard/wg0.conf`:
  ```ini
  [Interface]
  Address = 10.10.20.1/24
  ListenPort = 51820
  PrivateKey = <contents of server-private.key>
  PostUp   = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
  PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE
  ```
  (Add `[Peer]` blocks per laptop once keys are generated in Phase 4. For now, test with a single laptop first.)
- [ ] Enable IP forwarding:
  ```bash
  echo "net.ipv4.ip_forward = 1" | sudo tee -a /etc/sysctl.conf
  sudo sysctl -p
  ```
- [ ] Open the WireGuard port and restrict ingest ports to VPN subnet only:
  ```bash
  sudo ufw allow 51820/udp
  sudo ufw allow from 10.10.20.0/24 to any port 443
  sudo ufw allow from 10.10.20.0/24 to any port 8080
  sudo ufw enable
  ```
- [ ] Start the gateway: `sudo systemctl enable --now wg-quick@wg0`

---

## Phase 3 — Ubuntu server: Headscale (VPN control plane)

Headscale replaces manual peer-list editing — at 100 laptops, hand-editing `/etc/wireguard/wg0.conf` is not feasible.

- [ ] Pull the Headscale config template:
  ```bash
  wget -O ~/headscale/config.yaml \
    https://raw.githubusercontent.com/juanfont/headscale/main/config-example.yaml
  ```
- [ ] Edit `config.yaml`: set `server_url` to `https://ingest.dvas.internal:8080` and set `listen_addr` to `0.0.0.0:8080`.
- [ ] Add Headscale to `docker-compose.yml` on the Ubuntu server:
  ```yaml
  headscale:
    image: headscale/headscale:latest
    volumes:
      - ./headscale/config.yaml:/etc/headscale/config.yaml
      - headscale_data:/var/lib/headscale
    ports:
      - "8080:8080"
    command: serve
  ```
- [ ] Create the edge fleet namespace:
  ```bash
  docker compose exec headscale headscale users create dvas-edge
  ```
- [ ] Generate a reusable pre-auth key for enrolling laptops:
  ```bash
  docker compose exec headscale headscale preauthkeys create \
    --user dvas-edge --reusable --expiration 24h
  ```
- [ ] Optionally deploy Headplane (open source web UI for Headscale) alongside it for browser-based node management.
- [ ] Define an ACL policy: edge laptops can reach only port 443 on `10.10.20.1`, not each other.

---

## Phase 4 — Ubuntu server: step-ca (internal certificate authority)

This replaces the one-off `gen_certs.ps1` approach. Certificates issued here are trusted automatically by anything configured with the CA root.

- [ ] Add step-ca to `docker-compose.yml`:
  ```yaml
  step-ca:
    image: smallstep/step-ca:latest
    volumes:
      - step_data:/home/step
    ports:
      - "9000:9000"
    environment:
      - DOCKER_STEPCA_INIT_NAME=DVAS Internal CA
      - DOCKER_STEPCA_INIT_DNS_NAMES=ingest.dvas.internal
      - DOCKER_STEPCA_INIT_ADDRESS=:9000
  ```
- [ ] On first run, step-ca prints the root CA certificate fingerprint — save it, you need it on every Windows laptop to trust certificates issued by this CA.
- [ ] Issue a certificate for the ingest server hostname via ACME or the step CLI:
  ```bash
  docker compose exec step-ca step ca certificate \
    ingest.dvas.internal ingest.crt ingest.key \
    --ca-url https://localhost:9000 \
    --root /home/step/certs/root_ca.crt
  ```
- [ ] Point NGINX at the new certificate files instead of the old `ingest/certs/` self-signed pair.
- [ ] Update `KC_HOSTNAME` in `docker-compose.yml` to `ingest.dvas.internal` and restart Keycloak.

---

## Phase 5 — Ubuntu server: replace Redis with Valkey, plan evidence storage

### Valkey (drop-in Redis replacement)

Valkey is wire-protocol compatible — no client code changes required. In `docker-compose.yml`, change:
```yaml
# Before
redis:
  image: redis:latest

# After
valkey:
  image: valkey/valkey:latest
  # same ports, same volume mounts as before
```

Update all `depends_on` references from `redis` to `valkey`. The ingest API and GPU consumer connect via `redis://` URL — change the hostname in their environment config from `redis` to `valkey`. That is the entire migration.

### Evidence storage

Choose one:

**SeaweedFS** (simpler to operate, Apache-2.0):
```yaml
seaweedfs-master:
  image: chrislusf/seaweedfs:latest
  command: master
seaweedfs-volume:
  image: chrislusf/seaweedfs:latest
  command: volume -mserver=seaweedfs-master:9333 -dir=/data
  volumes:
    - evidence_data:/data
seaweedfs-filer:
  image: chrislusf/seaweedfs:latest
  command: filer -master=seaweedfs-master:9333
```

**Ceph** (more complex, better for large deployments, LGPL): use the official Ceph container image and deploy via `cephadm`. Refer to the Ceph documentation for the bootstrap sequence — it is not a single-container setup and is better suited for the district-level phase than the pilot.

For the pilot, start with SeaweedFS.

---

## Phase 6 — Windows edge laptop: WireGuard client setup

This phase runs on each Windows edge laptop individually.

- [ ] Download and install WireGuard for Windows from `wireguard.com` (the installer is an official GPLv2 build).
- [ ] Generate a keypair on the laptop (WireGuard app → Add Tunnel → Generate):
  ```ini
  [Interface]
  PrivateKey = <generated>
  Address = 10.10.20.<N>/24      # unique per laptop, assigned by the admin
  DNS = 10.10.20.1               # optional — only if you run a DNS resolver on the server

  [Peer]
  PublicKey = <server-public.key contents>
  Endpoint = <Ubuntu server real IP or hostname>:51820
  AllowedIPs = 10.10.20.0/24
  PersistentKeepalive = 25
  ```
- [ ] Share the laptop's public key with the Ubuntu admin, who adds it as a `[Peer]` in `/etc/wireguard/wg0.conf` (or through Headscale if Phase 3 is complete).
- [ ] Activate the tunnel in the WireGuard app and confirm `ping 10.10.20.1` succeeds.
- [ ] Install the step-ca root certificate into the Windows trust store so the laptop trusts the ingest server's TLS cert:
  ```powershell
  # Run as Administrator
  Import-Certificate -FilePath root_ca.crt -CertStoreLocation Cert:\LocalMachine\Root
  ```

---

## Phase 7 — Windows edge laptop: Python environment and config changes

The Windows laptop already has the correct Python environment from development. Only two things change.

### Config changes

**`config/settings.yaml`** — only the ingest-related values change. The video file paths are already Windows paths (`D:/POLICE_DRONE/...`) and stay as-is:
```yaml
# Change these two values:
ingest:
  url: "https://ingest.dvas.internal/ingest/frame"   # was https://localhost/ingest/frame
  token_url: "http://10.10.20.1:8080/realms/..."     # was http://localhost:8080/...
```

**`ingest/edge_sender.py`** — if `INGEST_URL` and `TOKEN_URL` are read from environment variables or the config above, no code change is needed. If they are still hardcoded, update:
```python
INGEST_URL = "https://ingest.dvas.internal/ingest/frame"
TOKEN_URL  = "http://10.10.20.1:8080/realms/dvas/protocol/openid-connect/token"
```

### No Docker on the edge laptop

The current Windows setup runs everything in Docker Desktop, including the analytics pipeline. Going forward, the analytics pipeline (`main.py` / `analytics_worker.py`) runs as native Python on the edge laptop — Docker is only needed on the Ubuntu server. Remove Docker Desktop from the edge laptops entirely (or leave it unstarted — it doesn't need to run).

Verify the native Python run works:
```powershell
cd D:\POLICE_DRONE\DRONE_ANALYTICS
venv\Scripts\activate
python main.py
```

If the OpenCV window opens and frames appear, the edge pipeline is working. The `edge_sender.py` call inside it will now send to the Ubuntu server over the VPN.

---

## Phase 8 — End-to-end validation (two machines)

Run this checklist in order. Each item proves one layer of the stack.

- [ ] `ping 10.10.20.1` from Windows → proves WireGuard tunnel is up.
- [ ] `curl https://ingest.dvas.internal/health` from Windows → proves NGINX is reachable and the TLS certificate is trusted.
- [ ] Obtain a JWT from Keycloak via `edge_sender.py`'s token request → proves Keycloak is reachable and `KC_HOSTNAME` matches.
- [ ] Run `edge_sender.py` with a test JPEG (`test_frame.jpg`) → proves a frame reaches the ingest API and lands in the Valkey queue.
- [ ] Confirm the GPU consumer picks up the queued frame → proves the full ingest-to-inference path.
- [ ] Run `main.py` on the Windows laptop with `simulation.enabled: true` → proves the full edge pipeline runs natively on Windows and sends live frames to Ubuntu.
- [ ] Trigger a simulated confirmed event on the Ubuntu side and verify the Windows laptop receives the confirmation signal and attempts clip extraction.

---

## Phase 9 — Monitoring and alerting (Prometheus, Grafana, Alertmanager)

All three run as containers on the Ubuntu server.

- [ ] Extend `docker-compose.yml` with Prometheus (scraping the ingest API and GPU consumer metrics already exposed via `src/metrics/prometheus_metrics.py`), Grafana, and Alertmanager.
- [ ] Add a scrape target for WireGuard tunnel health (`wireguard_exporter` is a lightweight open source Prometheus exporter for this).
- [ ] Build a single Grafana dashboard covering: active VPN peers (per-laptop last-seen), ingest queue depth per drone, GPU consumer latency, and evidence upload success rate.
- [ ] Define Alertmanager rules for: VPN peer last-seen > 5 min (laptop disconnected), queue depth growing unbounded (GPU consumer falling behind), GPU consumer container restarting.

---

## Phase 10 — Provisioning workflow (onboarding a new Windows laptop)

This becomes the SOP for the system administrator role.

1. Generate a WireGuard keypair for the new laptop using the WireGuard Windows app.
2. Note the laptop's assigned VPN IP (next available in `10.10.20.0/24`).
3. Register the laptop in Headscale: `headscale nodes register --user dvas-edge --key <public-key>` (or via the Headplane web UI).
4. Install the step-ca root certificate on the laptop (one PowerShell command, run once per laptop).
5. Update `edge_sender.py` / `settings.yaml` with the two URL changes from Phase 7.
6. Log the action: laptop ID, assigned VPN IP, officer name, date — into the same audit log that records JWT issuance.
7. To offboard: revoke the Headscale node and revoke the Keycloak account in the same action.

---

## Phase 11 — Ingest scaling for the 100-laptop fleet

Horizontal scaling of the ingest layer so it doesn't become the bottleneck as the fleet grows.

- [ ] Add a second (and third) ingest API replica to `docker-compose.yml`.
- [ ] Extend `ingest/nginx/default.conf` with an `upstream` block:
  ```nginx
  upstream ingest_api {
    server ingest-api-1:8000;
    server ingest-api-2:8000;
    server ingest-api-3:8000;
  }
  server {
    location /ingest/ {
      proxy_pass http://ingest_api;
    }
  }
  ```
- [ ] Load-test before the pilot: replay `DJI_0110.MP4` and `DJI_0111.MP4` concurrently from multiple simulated Windows clients and confirm queue depth stays stable.

---

## Phase 12 — Gateway redundancy

A single WireGuard gateway going down takes every field laptop offline simultaneously.

- [ ] Provision a second Ubuntu machine as a warm-standby gateway.
- [ ] Use a floating IP (keepalived or a simpler DNS failover) so laptops reconnect automatically.
- [ ] Document the failover procedure and test it before the district-level deployment.

---

## Appendix A — Files changed and where

| File | Machine | Change |
|---|---|---|
| `config/settings.yaml` | Windows | `ingest.url`, `ingest.token_url` → Ubuntu VPN address |
| `ingest/edge_sender.py` | Windows | `INGEST_URL`, `TOKEN_URL` if hardcoded |
| `docker-compose.yml` | Ubuntu | Add Headscale, step-ca, Valkey, SeaweedFS, Prometheus, Grafana, Alertmanager; remove Redis, remove old cert volume |
| `ingest/nginx/default.conf` | Ubuntu | TLS cert paths → step-ca issued cert; upstream block for multi-replica ingest |
| `ingest/certs/` | Ubuntu | Replaced by step-ca issued certs; old self-signed files removed |
| `src/metrics/prometheus_metrics.py` | Ubuntu | Add WireGuard peer health metrics |
| `ingest/api/config.py` | Ubuntu | Confirm host/origin checks match `ingest.dvas.internal` |

## Appendix B — What does NOT change

- `src/video/drone_simulation_source.py` — reads Windows paths, stays as-is.
- `src/buffer/rolling_frame_buffer.py` — writes to local SSD, stays as-is.
- `src/change_detection/change_detection_engine.py` — no network dependency.
- `src/cognitive_frame_selector/cognitive_frame_selector.py` — no network dependency.
- `tests/` — all existing tests run on the Windows machine against the native Python environment unchanged.
- `config/settings.yaml` video paths (`D:/POLICE_DRONE/...`) — Windows paths stay as Windows paths.

## Appendix C — Open decisions

| Decision | Blocks |
|---|---|
| Ubuntu server hardware confirmed | Phase 1 |
| Internal hostname chosen (`ingest.dvas.internal` or similar) | Phase 4 |
| SeaweedFS vs Ceph for evidence storage | Phase 5 |
| VPN subnet chosen (`10.10.20.0/24` or different) | Phase 2 and Phase 6 |
| Gateway redundancy budget | Phase 12 |
