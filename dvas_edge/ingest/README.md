# Kerala Police Drone Ingest System — Edge Sender

> **This file documents the Windows edge laptop side of the ingest subsystem.**  
> The ingest server (FastAPI, NGINX, Keycloak, Redis, GPU consumer) runs on a **separate Ubuntu machine**.  
> Only `edge_sender.py` lives on the Windows laptop.

---

## Architecture

```
WINDOWS EDGE LAPTOP (this machine)           UBUNTU INGEST SERVER (10.10.20.1 over VPN)
────────────────────────────────             ──────────────────────────────────────────
edge_sender.py                               NGINX  :443   (TLS termination + rate limit)
      │                                           │
      │  HTTPS POST /ingest/frame                 ▼
      │  Authorization: Bearer <JWT>         FastAPI :8000  (JWT validation + Redis queue)
      │  ── WireGuard VPN tunnel ──►              │
      │                                           ▼
      │  token request (port 8080)           Redis Streams  (per-drone frame queue)
      └──────────────────────────────────►        │
         to Keycloak on Ubuntu VPN IP             ▼
                                            GPU Consumer   (batch inference)
                                                  │
                                                  ▼
                                            Confirmed event signal → back to Windows laptop
```

### Component Roles (Ubuntu server — Docker Compose)

| Component | Role | Port (VPN) |
|-----------|------|---------|
| **NGINX** | Reverse proxy, TLS termination, rate limiting | 443 / 80 |
| **Keycloak** | JWT token issuing and validation | 8080 (VPN direct) |
| **FastAPI** | Ingest API — receives frames, validates JWT, publishes to Redis Stream | 8000 (internal) |
| **Valkey/Redis** | Per-drone streams with back-pressure | 6379 (internal) |
| **Postgres** | Keycloak's database | 5432 (internal) |
| **GPU Consumer** | Batch inference on selected frames | internal |

---

## VPN Connection (Required before using edge_sender.py)

The edge laptop must be connected to the Ubuntu VPN gateway via WireGuard before any ingest calls will work.

```powershell
# 1. Open WireGuard for Windows app
# 2. Import or paste the config from: config\wireguard\wg0-client.conf
# 3. Activate the tunnel
# 4. Confirm connectivity:
ping 10.10.20.1

# 5. Confirm TLS (once step-ca root CA is installed):
curl https://ingest.dvas.internal/health
```

---

## Using edge_sender.py

```powershell
pip install httpx

# Send a single test frame to the Ubuntu ingest server
python edge_sender.py --frame test_frame.jpg --username operator_001 --password Operator@001

# Continuous loop at 5 FPS (simulates live drone feed)
python edge_sender.py --loop --fps 5 --drone-id DRONE_KL_001 --frame test_frame.jpg
```

All endpoint URLs default to the Ubuntu VPN address. Override via environment variable if needed:

```powershell
$env:INGEST_URL = "https://ingest.dvas.internal/ingest/frame"
$env:TOKEN_URL  = "http://10.10.20.1:8080/realms/kerala-police-drone/protocol/openid-connect/token"
python edge_sender.py --frame test_frame.jpg
```

---

## Service URLs (accessed from Windows laptop over VPN)

| URL | Purpose | Credentials |
|-----|---------|-------------|
| `https://ingest.dvas.internal/health` | API liveness check | — |
| `https://ingest.dvas.internal/health/ready` | Readiness (Redis + Keycloak) | — |
| `https://ingest.dvas.internal/ingest/frame` | Frame upload | Bearer JWT |
| `https://ingest.dvas.internal/ingest/status` | System status | Bearer JWT |
| `https://ingest.dvas.internal/metrics` | Prometheus metrics | internal only |
| `ws://ingest.dvas.internal/ws/status` | Live WebSocket feed | — |
| `http://10.10.20.1:8080` | Keycloak Admin Console | `admin` / `Admin@KP2025!` |

---

## API Endpoints

### Frame Ingest
```
POST /ingest/frame
Content-Type: multipart/form-data
Authorization: Bearer <JWT>

Form fields:
  frame         — JPEG or PNG file (≤5 MB)
  metadata_json — JSON string:
    {
      "drone_id":       "DRONE_KL_001",
      "mission_id":     "MISSION_20250527_BEACH",
      "timestamp":      "2025-05-27T10:30:00.000Z",
      "frame_sequence": 1,
      "change_score":   0.75,
      "latitude":       10.8505,
      "longitude":      76.2711,
      "altitude_m":     50.0
    }

Response 202:
  {
    "status": "queued",
    "drone_id": "DRONE_KL_001",
    "frame_sequence": 1,
    "queue_name": "drone:frames:DRONE_KL_001",
    "message_id": "1717497600000-0"
  }
```

### Health / Readiness
```
GET /health         → {"status": "ok", "version": "1.0.0", "uptime_s": 42.1, ...}
GET /health/ready   → {"ready": true, "redis": "connected", "keycloak": "reachable"}
```

### Status
```
GET /ingest/status              → All drones aggregated (JWT required)
GET /ingest/status/{drone_id}   → Single drone detail   (JWT required)
```

### Mission Lifecycle
```
POST /ingest/mission/start   → {"drone_id": "...", "mission_id": "..."} (JWT required)
POST /ingest/mission/end     → {"drone_id": "...", "mission_id": "..."} (JWT required)
```

### Error Codes
| Code | Meaning |
|------|---------| 
| 401 | JWT missing, expired, or invalid |
| 403 | Valid JWT but wrong role (needs `drone-operator` or `sys-admin`) |
| 413 | Frame exceeds 5 MB limit |
| 415 | Only JPEG/PNG accepted |
| 422 | Invalid metadata JSON |
| 429 | Rate limit exceeded (>30 frames/sec from same IP) |
| 503 | Redis unavailable |

---

## Folder Structure (Windows edge laptop)

```
ingest\
├── edge_sender.py    ← Operator laptop client — the ONLY file from ingest/ on the Windows machine
└── README.md         ← This file

config\
├── settings.yaml     ← Edge pipeline config (includes ingest: section with Ubuntu VPN URLs)
└── wireguard\
    └── wg0-client.conf  ← WireGuard tunnel config template for this laptop
```

> All other ingest components (FastAPI, NGINX, GPU consumer, Keycloak, Redis, certs, scripts)
> live on the **Ubuntu ingest server** and are managed there via Docker Compose.

---

## Security Notes

- All credentials in this guide are for the **pilot deployment** — rotate after go-live
- `tls_verify: false` in `settings.yaml` is a temporary workaround until the step-ca root CA  
  is installed in the Windows trust store (`Import-Certificate ... Cert:\LocalMachine\Root`)
- JWT tokens expire in 5 minutes — `edge_sender.py` handles auto-refresh
- WireGuard private keys must **never** be committed to version control (`.gitignore` enforces this)

---

## Next Steps (Phase 2)

When scaling to ~20 drones:
- Migrate Redis Streams to **Apache Kafka** (higher throughput, replayable)
- Replace REST ingest with **gRPC** (lower overhead per frame)
- Add horizontal scaling for FastAPI workers behind NGINX upstream
- Deploy behind a proper load balancer with CA-signed TLS
