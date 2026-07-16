# Changes Made to `drone_analytics` — Windows Edge Laptop Conversion

---

## 🗑️ Files Deleted (Ubuntu-only — moved to Ubuntu machine)

| Deleted File / Directory | Why |
|---|---|
| `docker-compose.yml` | All Docker services run on Ubuntu, not Windows |
| `Dockerfile` | Analytics container is not needed natively on Windows |
| `Dockerfile.gpu_consumer` | GPU consumer runs in Docker on Ubuntu |
| `.dockerignore` | Companion to Dockerfiles — only needed on Ubuntu |
| `start-docker.ps1` | Was the Docker Compose startup script |
| `config/settings.docker.yaml` | Was the Docker container config override |
| `ingest/api/` *(entire directory)* | FastAPI ingest server runs on Ubuntu |
| `ingest/gpu_consumer.py` | GPU batch consumer runs on Ubuntu |
| `ingest/nginx/` *(entire directory)* | NGINX reverse proxy runs on Ubuntu |
| `ingest/certs/` *(entire directory)* | TLS certs managed by step-ca on Ubuntu |
| `ingest/.env.example` | Docker environment variables template for Ubuntu |
| `ingest/docker-compose.yml` | Ubuntu-only inner compose file |
| `ingest/scripts/gen_certs.ps1` | Certificate generation — replaced by step-ca on Ubuntu |
| `ingest/scripts/setup_keycloak.py` | Keycloak bootstrapping — runs on Ubuntu |
| `ingest/scripts/setup_rabbitmq.py` | Queue pre-declaration — runs on Ubuntu |
| `ingest/scripts/start.ps1` | Docker startup — runs on Ubuntu |
| `ingest/scripts/verify.py` | Verifies Docker services — runs on Ubuntu |

> **Before deleting**, copy all the above to the Ubuntu machine — they form the starting point of the Ubuntu ingest server repo.

---

## ✏️ Files Modified

### 1. [`config/settings.yaml`](file:///d:/POLICE_DRONE/drone_analytics/config/settings.yaml)

**What changed:** Added a brand-new `ingest:` section at the bottom of the file.

**Before:** No `ingest:` section existed at all — the only targets were `localhost`.

**After:** Full ingest section pointing to the Ubuntu server over WireGuard VPN:

```yaml
ingest:
  url:               "https://ingest.dvas.internal/ingest/frame"
  mission_start_url: "https://ingest.dvas.internal/ingest/mission/start"
  mission_end_url:   "https://ingest.dvas.internal/ingest/mission/end"
  token_url:         "http://10.10.20.1:8080/realms/kerala-police-drone/protocol/openid-connect/token"
  client_id:         "drone-ingest-client"
  client_secret:     "Oh1Rt3uOXlKpsEnjL2P9x7tFbCtJW8Ph"   # ← rotate after pilot
  username:          "operator_001"
  password:          "Operator@001"                         # ← rotate after pilot
  drone_id:          "DRONE_KL_001"
  mission_id:        "MISSION_DEFAULT"
  tls_verify:        false   # ← change to true once step-ca root CA is installed
```

---

### 2. [`ingest/edge_sender.py`](file:///d:/POLICE_DRONE/drone_analytics/ingest/edge_sender.py)

**What changed:** Four hardcoded `localhost` URLs replaced with the correct Ubuntu VPN addresses. Credentials converted from static constants to `os.getenv()` so they can be overridden via environment variables without editing code. A `TLS_VERIFY` flag added.

| What | Before | After |
|---|---|---|
| `INGEST_URL` | `https://localhost/ingest/frame` | `https://ingest.dvas.internal/ingest/frame` |
| `TOKEN_URL` | `http://localhost:8080/realms/...` | `http://10.10.20.1:8080/realms/kerala-police-drone/...` |
| `start_mission()` URL | `https://localhost/ingest/mission/start` | `https://ingest.dvas.internal/ingest/mission/start` |
| `end_mission()` URL | `https://localhost/ingest/mission/end` | `https://ingest.dvas.internal/ingest/mission/end` |
| `verify=False` (hardcoded) | Always skipped TLS check | `verify=TLS_VERIFY` — controlled by env var |
| Credentials | Hardcoded constants | `os.getenv()` with defaults — override without code changes |

---

### 3. [`main.py`](file:///d:/POLICE_DRONE/drone_analytics/main.py)

**What changed:** One-line docstring comment updated — clarified that `localhost:8000/metrics` is the edge laptop's local Prometheus port, scraped remotely by Prometheus running on Ubuntu via VPN.

---

### 4. [`ingest/README.md`](file:///d:/POLICE_DRONE/drone_analytics/ingest/README.md)

**What changed:** Completely rewritten. Was a Docker startup guide for a single-machine setup. Now documents the edge-laptop-only view: VPN connection, `edge_sender.py` usage, and all URLs pointing to `ingest.dvas.internal`.

---

### 5. [`README.md`](file:///d:/POLICE_DRONE/drone_analytics/README.md)

**What changed:** Completely rewritten. Was a Docker-on-Windows single-machine guide. Now documents the two-machine architecture, native Python pipeline, WireGuard setup, and updated file structure.

---

## 🆕 Files Added

### 1. [`config/wireguard/wg0-client.conf`](file:///d:/POLICE_DRONE/drone_analytics/config/wireguard/wg0-client.conf)

The WireGuard tunnel configuration template for this Windows laptop. Every edge laptop gets one copy of this file, filled in with laptop-specific values.

```ini
[Interface]
PrivateKey = <PASTE LAPTOP PRIVATE KEY HERE>
Address    = 10.10.20.<N>/24     # unique per laptop
DNS        = 10.10.20.1

[Peer]
PublicKey           = <PASTE UBUNTU SERVER PUBLIC KEY HERE>
Endpoint            = <UBUNTU_SERVER_PUBLIC_IP_OR_HOSTNAME>:51820
AllowedIPs          = 10.10.20.0/24
PersistentKeepalive = 25
```

---

### 2. [`.gitignore`](file:///d:/POLICE_DRONE/drone_analytics/.gitignore)

New file — did not exist before. Protects against accidentally committing:
- WireGuard private keys (`*.key`)
- TLS certificates (`*.crt`, `*.pem`)
- Large MP4 video files
- The rolling frame buffer slab files
- Python virtual environment (`.venv/`)
- Test coverage artifacts

---

## 🔑 Values Expected from the Ubuntu Ingest Server

These are the **3 categories of values** you must get from the Ubuntu server before the Windows laptop pipeline can work end-to-end.

### Category 1 — WireGuard Network (fills `wg0-client.conf`)

| Value needed | Where it comes from on Ubuntu |
|---|---|
| **Ubuntu server public WireGuard key** | Contents of `/etc/wireguard/server-public.key` after running `wg genkey` on Ubuntu |
| **Ubuntu server real public IP or hostname** | The internet-facing IP of the Ubuntu machine (e.g., `203.0.113.1`) or a DDNS hostname |
| **This laptop's assigned VPN IP** | Admin picks it — e.g., `10.10.20.2/24` for the first laptop, `.3` for the second, etc. |

---

### Category 2 — Keycloak Authentication (fills `settings.yaml` → `ingest:` section)

| Value needed | Where it comes from on Ubuntu |
|---|---|
| **Client Secret** | Printed by `ingest/scripts/setup_keycloak.py` when run on Ubuntu — paste into `ingest.client_secret` |
| **Realm name** | Confirmed when Keycloak realm is created on Ubuntu — currently `kerala-police-drone` |
| **Client ID** | Confirmed in Keycloak client config — currently `drone-ingest-client` |
| **Operator username / password** | Created by `setup_keycloak.py` — currently `operator_001` / `Operator@001` |
| **Drone IDs** | Registered in Keycloak and Ubuntu's `KNOWN_DRONES` env var — currently `DRONE_KL_001` etc. |

---

### Category 3 — TLS Certificate Trust (one-time per laptop)

| Value needed | Where it comes from on Ubuntu |
|---|---|
| **step-ca root CA certificate** (`root_ca.crt`) | Printed on first boot of the `step-ca` container on Ubuntu — save it and copy to each Windows laptop |

Once you have it, run this once on each laptop (as Administrator):
```powershell
Import-Certificate -FilePath root_ca.crt -CertStoreLocation Cert:\LocalMachine\Root
```
After this, change `tls_verify: false` → `tls_verify: true` in `config/settings.yaml`.

---

## ✅ Quick Reference — What to Do When Ubuntu is Ready

```
Ubuntu admin gives you          →   Where to put it on Windows
─────────────────────────────────────────────────────────────────────
server-public.key contents      →   config\wireguard\wg0-client.conf  [Peer] PublicKey
Ubuntu server public IP         →   config\wireguard\wg0-client.conf  [Peer] Endpoint
Your laptop's VPN IP (e.g .2)   →   config\wireguard\wg0-client.conf  [Interface] Address
Keycloak client secret          →   config\settings.yaml              ingest.client_secret
root_ca.crt                     →   Windows certificate store (PowerShell command above)
                                    then set tls_verify: true in settings.yaml
```
