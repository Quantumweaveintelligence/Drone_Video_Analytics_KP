# Kerala Police Drone Ingest System — Ubuntu Server

> **Stack:** FastAPI · Valkey (Redis-compatible) · Keycloak · NGINX · Docker  
> **Machine:** Ubuntu 22.04 LTS Ingest Server  
> **Capacity:** Pilot (2–5 drones) → scales to 100+ laptops

---

## Architecture

```
Windows Edge Laptops (field)              Ubuntu Ingest Server (this machine)
──────────────────────────────────        ────────────────────────────────────────────
edge_sender.py                            WireGuard VPN gateway  (:51820 UDP)
  │  HTTPS POST /ingest/frame               │
  │  Authorization: Bearer <JWT>            │
  │  over WireGuard VPN                     ▼
  └───────────────────────────────────► NGINX (:443)
                                            │  proxy_pass + rate limit
                                            ▼
                                        FastAPI Ingest API (:8000)
                                            │  JWT validation via Keycloak
                                            │  XADD to per-drone stream
                                            ▼
                                        Valkey (:6379)   ← frame queue
                                            │
                                            │  XREAD batch
                                            ▼
                                        GPU Consumer
                                            │
                                            ├──► confirmed event → back to laptop
                                            └──► evidence clip → SeaweedFS
```

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
GET /health         → {"status": "ok", "version": "1.0.0", "uptime_s": 42.1}
GET /health/ready   → {"ready": true, "redis": "connected", "keycloak": "reachable"}
```

### Status
```
GET /ingest/status              → All drones aggregated  (JWT required)
GET /ingest/status/{drone_id}   → Single drone detail    (JWT required)
```

### Mission Lifecycle
```
POST /ingest/mission/start   → {"drone_id": "...", "mission_id": "..."}  (JWT required)
POST /ingest/mission/end     → {"drone_id": "...", "mission_id": "..."}  (JWT required)
```

### Error Codes
| Code | Meaning |
|------|---------|
| 401  | JWT missing, expired, or invalid |
| 403  | Valid JWT but wrong role (`drone-operator` or `sys-admin` required) |
| 413  | Frame exceeds 5 MB limit |
| 415  | Only JPEG/PNG accepted |
| 422  | Invalid metadata JSON |
| 429  | Rate limit exceeded (>30 frames/sec from same IP) |
| 503  | Valkey unavailable |

---

## Service URLs (from within VPN — 10.10.20.0/24)

| URL | Purpose |
|-----|---------|
| `https://ingest.dvas.internal/health` | API liveness check |
| `https://ingest.dvas.internal/health/ready` | Readiness (Valkey + Keycloak) |
| `https://ingest.dvas.internal/docs` | Swagger UI |
| `https://ingest.dvas.internal/ingest/frame` | Frame upload |
| `https://ingest.dvas.internal/ingest/status` | System status |
| `https://ingest.dvas.internal/metrics` | Prometheus metrics (VPN subnet only) |
| `wss://ingest.dvas.internal/ws/status` | Live WebSocket feed |
| `http://10.10.20.1:8080` | Keycloak Admin Console |

---



## Scripts

| Script | Purpose | How to run |
|--------|---------|------------|
| `setup_ubuntu.sh` | One-shot Ubuntu server bootstrap | `sudo ./ingest/scripts/setup_ubuntu.sh` |
| `setup_keycloak.py` | Auto-configure Keycloak realm, client, roles, users | `docker compose logs setup` (auto) |
| `verify.py` | Check all services are healthy | `python ingest/scripts/verify.py` |

---

## Key Config Files

| File | Purpose |
|------|---------|
| `ingest/api/config.py` | API config — Keycloak URL, Valkey URL, CORS origins, drone registry |
| `ingest/nginx/default.conf` | NGINX — TLS, rate limiting, routing; `server_name ingest.dvas.internal` |
| `ingest/.env.example` | All environment variable templates — copy to `.env` before starting |
| `ingest/certs/ingest.crt` | TLS cert (issued by step-ca — **not committed to git**) |

---

## Common Docker Commands

```bash
# Start all services
docker compose up -d

# Check health of every container
docker compose ps

# Tail ingest API logs
docker compose logs -f ingest-api

# Watch Keycloak bootstrap
docker compose logs -f setup

# Open shell inside API container (debugging)
docker compose exec ingest-api bash

# Restart NGINX after cert rotation
docker compose restart nginx

# Full reset — WARNING: deletes all volumes (evidence, queue, certs)
docker compose down -v
```

---

## Security Notes

- **Never commit** `.env`, `*.key`, `*.pem` or `ingest/certs/` — all are in `.gitignore`
- Keycloak admin port (`8080`) is bound to `10.10.20.1` (VPN interface) only — not internet-facing
- Rotate `client_secret` after the pilot and update all Windows laptops' `settings.yaml`
- JWT tokens expire in 5 minutes — `edge_sender.py` on the Windows laptop handles auto-refresh
- Rate limiting (30 r/s per source IP, burst 60) is enforced at the NGINX layer
- Evidence clips in SeaweedFS are write-once — no delete API exposed by default
