# Phase 1 — Drone Ingest Server Implementation Guide
**Stack: FastAPI + RabbitMQ + Keycloak + NGINX**
> Pilot deployment · 2–5 drones · Windows with Docker Desktop

---

## Table of Contents
1. [Architecture Overview](#1-architecture-overview)
2. [Prerequisites](#2-prerequisites)
3. [Folder Structure](#3-folder-structure)
4. [Generate TLS Certificate](#4-generate-tls-certificate)
5. [FastAPI Application Code](#5-fastapi-application-code)
6. [NGINX Configuration](#6-nginx-configuration)
7. [docker-compose.yml](#7-docker-composeyml)
8. [Start All Services](#8-start-all-services)
9. [Configure Keycloak](#9-configure-keycloak)
10. [Create RabbitMQ Queues](#10-create-rabbitmq-queues)
11. [Operator Laptop Client](#11-operator-laptop-client)
12. [GPU Batch Consumer](#12-gpu-batch-consumer)
13. [Testing Checklist](#13-testing-checklist)
14. [Useful Docker Commands](#14-useful-docker-commands)

---

## 1. Architecture Overview

```
Operator Laptop (Edge)
        │
        │  HTTPS POST /ingest/frame
        │  Authorization: Bearer <JWT>
        ▼
   ┌─────────┐     validate token     ┌───────────┐
   │  NGINX  │ ──────────────────────►│ Keycloak  │
   │ :443    │ ◄──────────────────────│ :8080     │
   └────┬────┘    401 / pass through  └───────────┘
        │
        │  proxy_pass
        ▼
   ┌─────────┐    publish frame       ┌───────────┐
   │ FastAPI │ ──────────────────────►│ RabbitMQ  │
   │ :8000   │                        │ :5672     │
   └─────────┘                        └─────┬─────┘
                                            │  consume batch
                                            ▼
                                    ┌───────────────┐
                                    │  GPU Consumer │
                                    │  (Analytics)  │
                                    └───────────────┘
```

### Component Roles

| Component | Role | Port |
|-----------|------|------|
| **NGINX** | Reverse proxy, TLS termination, rate limiting | 443 / 80 |
| **Keycloak** | JWT token issuing and validation | 8080 |
| **FastAPI** | Ingest API — receives frames, validates tokens, publishes to queue | 8000 |
| **RabbitMQ** | Per-drone message queues with back-pressure | 5672 / 15672 |
| **Postgres** | Keycloak's database | 5432 (internal) |

### Data Flow (per frame)
1. Edge laptop sends `POST /ingest/frame` with JWT token + JPEG frame
2. NGINX validates JWT against Keycloak, rejects if invalid
3. FastAPI parses frame metadata, checks role, publishes to `drone.frames.<drone_id>`
4. GPU consumer pulls batches of 16 frames from all drone queues

---

## 2. Prerequisites

Install the following on your Windows machine:

| Software | Minimum Version | Download |
|----------|----------------|---------|
| Docker Desktop | 4.28+ | https://docs.docker.com/desktop/install/windows/ |
| Python | 3.11+ | https://www.python.org/downloads/ |
| Git for Windows | 2.44+ | https://git-scm.com/download/win |
| VS Code (optional) | Any | https://code.visualstudio.com/ |

> **Docker Desktop setup:** After installing, go to Settings → Resources → set Memory to at least **4 GB**. Ensure "Use WSL 2 based engine" is enabled under Settings → General.

---

## 3. Folder Structure

Open PowerShell and run:

```powershell
cd C:\
mkdir drone-ingest
cd drone-ingest
mkdir api, nginx, certs, rabbitmq
```

Expected layout:

```
C:\drone-ingest\
├── docker-compose.yml
├── api\
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── config.py
│   ├── models.py
│   ├── auth.py
│   ├── queue.py
│   └── main.py
├── nginx\
│   └── default.conf
├── certs\
│   ├── ingest.crt
│   └── ingest.key
└── rabbitmq\
```

---

## 4. Generate TLS Certificate

Run in **Git Bash** (comes with Git for Windows):

```bash
cd /c/drone-ingest/certs

openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout ingest.key \
  -out    ingest.crt \
  -subj   "/CN=localhost/O=KeralaPolice/C=IN"
```

**Alternative — using Docker only (no OpenSSL install needed):**

```powershell
# Run in PowerShell from C:\drone-ingest\
docker run --rm -v ${PWD}/certs:/certs alpine/openssl req -x509 -nodes `
  -days 365 -newkey rsa:2048 `
  -keyout /certs/ingest.key `
  -out    /certs/ingest.crt `
  -subj   "/CN=localhost/O=KeralaPolice/C=IN"
```

Verify: `ingest.crt` and `ingest.key` should appear inside `C:\drone-ingest\certs\`.

---

## 5. FastAPI Application Code

### `api\requirements.txt`

```
fastapi==0.111.0
uvicorn[standard]==0.29.0
python-jose[cryptography]==3.3.0
aio-pika==9.4.0
python-multipart==0.0.9
pydantic==2.7.0
httpx==0.27.0
```

---

### `api\config.py`

```python
import os

KEYCLOAK_URL    = os.getenv("KEYCLOAK_URL", "http://keycloak:8080")
REALM           = os.getenv("KC_REALM",     "kerala-police-drone")
CLIENT_ID       = os.getenv("KC_CLIENT_ID", "drone-ingest-client")
RABBITMQ_URL    = os.getenv("RABBITMQ_URL", "amqp://ingest_user:Rabbit@KP2025!@rabbitmq/")
MAX_FRAME_BYTES = int(os.getenv("MAX_FRAME_MB", "5")) * 1024 * 1024
ALLOWED_TYPES   = {"image/jpeg", "image/png"}
JWKS_URL        = f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/certs"
```

---

### `api\models.py`

```python
from pydantic import BaseModel
from typing import Optional

class FrameMetadata(BaseModel):
    drone_id:       str            # e.g. "DRONE_KL_001"
    mission_id:     str            # e.g. "MISSION_20250527_BEACH"
    timestamp:      str            # ISO 8601: "2025-05-27T10:30:00.000Z"
    frame_sequence: int            # Incremental counter per session
    change_score:   float          # Edge change-detection score 0.0–1.0
    latitude:       Optional[float] = None
    longitude:      Optional[float] = None
    altitude_m:     Optional[float] = None

class IngestResponse(BaseModel):
    status:         str            # "queued" | "rejected"
    drone_id:       str
    frame_sequence: int
```

---

### `api\auth.py`

```python
import httpx
from jose import jwt, JWTError
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from config import JWKS_URL, CLIENT_ID

security    = HTTPBearer()
_jwks_cache = None

async def get_jwks():
    """Fetch Keycloak public keys for JWT signature verification."""
    global _jwks_cache
    if not _jwks_cache:
        async with httpx.AsyncClient() as c:
            r           = await c.get(JWKS_URL)
            _jwks_cache = r.json()
    return _jwks_cache

async def validate_token(
    creds: HTTPAuthorizationCredentials = Security(security)
) -> dict:
    """Validate JWT and check role. Raises 401/403 on failure."""
    try:
        jwks    = await get_jwks()
        payload = jwt.decode(
            creds.credentials, jwks,
            algorithms=["RS256"],
            audience=CLIENT_ID
        )
        roles = payload.get("realm_access", {}).get("roles", [])
        if "drone-operator" not in roles:
            raise HTTPException(403, "Insufficient role")
        return payload
    except JWTError as e:
        raise HTTPException(401, f"Invalid token: {e}")
```

---

### `api\queue.py`

```python
import aio_pika
from config import RABBITMQ_URL

_connection = None
_channel    = None

async def get_channel():
    global _connection, _channel
    if not _connection or _connection.is_closed:
        _connection = await aio_pika.connect_robust(RABBITMQ_URL)
        _channel    = await _connection.channel()
        await _channel.set_qos(prefetch_count=100)
    return _channel

async def publish_frame(drone_id: str, metadata: dict, frame_bytes: bytes):
    """Publish a frame to the drone-specific durable queue."""
    ch         = await get_channel()
    queue_name = f"drone.frames.{drone_id}"

    # Declare queue (idempotent — safe to call on every publish)
    await ch.declare_queue(
        queue_name,
        durable=True,
        arguments={
            "x-max-length":  1000,        # Max frames queued per drone
            "x-overflow":    "drop-head", # Drop oldest if full (back-pressure)
            "x-message-ttl": 30000        # Frames expire after 30 seconds
        }
    )

    msg = aio_pika.Message(
        body          = frame_bytes,
        headers       = metadata,
        delivery_mode = aio_pika.DeliveryMode.PERSISTENT,
        content_type  = "image/jpeg"
    )
    await ch.default_exchange.publish(msg, routing_key=queue_name)
```

---

### `api\main.py`

```python
import json
import logging
from fastapi import FastAPI, File, UploadFile, Form, Depends, HTTPException
from auth import validate_token
from queue import publish_frame
from models import FrameMetadata, IngestResponse
from config import MAX_FRAME_BYTES, ALLOWED_TYPES

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="Kerala Police Drone Ingest Server — Phase 1")

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/ingest/frame", response_model=IngestResponse)
async def ingest_frame(
    frame:         UploadFile = File(...),
    metadata_json: str        = Form(...),
    claims:        dict       = Depends(validate_token)
):
    # Parse and validate metadata
    try:
        meta = FrameMetadata(**json.loads(metadata_json))
    except Exception as e:
        raise HTTPException(422, f"Bad metadata: {e}")

    # Check MIME type
    if frame.content_type not in ALLOWED_TYPES:
        raise HTTPException(415, "Only JPEG/PNG accepted")

    # Read and size-check frame
    data = await frame.read()
    if len(data) > MAX_FRAME_BYTES:
        raise HTTPException(413, "Frame exceeds size limit")

    # Publish to drone-specific RabbitMQ queue
    await publish_frame(meta.drone_id, meta.model_dump(), data)

    logging.info(f"Queued | drone={meta.drone_id} | seq={meta.frame_sequence} | score={meta.change_score}")
    return IngestResponse(
        status         = "queued",
        drone_id       = meta.drone_id,
        frame_sequence = meta.frame_sequence
    )
```

---

### `api\Dockerfile`

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

---

## 6. NGINX Configuration

### `nginx\default.conf`

```nginx
# Rate limit zone — 30 frames/sec per source IP
limit_req_zone $binary_remote_addr zone=drone_limit:10m rate=30r/s;

upstream fastapi {
    server api:8000;   # "api" is the Docker Compose service name
}

# Redirect HTTP → HTTPS
server {
    listen 80;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name localhost;

    ssl_certificate     /etc/ssl/certs/ingest.crt;
    ssl_certificate_key /etc/ssl/private/ingest.key;
    ssl_protocols       TLSv1.2 TLSv1.3;

    location /ingest/ {
        limit_req zone=drone_limit burst=60 nodelay;

        # Reject requests missing Authorization header early
        if ($http_authorization = "") { return 401; }

        proxy_pass         http://fastapi;
        proxy_set_header   Authorization $http_authorization;
        proxy_set_header   Host $host;
        client_max_body_size 10M;
        proxy_read_timeout   30s;
    }

    location /health {
        proxy_pass http://fastapi/health;
    }
}
```

---

## 7. docker-compose.yml

Save this as `C:\drone-ingest\docker-compose.yml`:

```yaml
version: "3.8"

networks:
  drone-net:
    driver: bridge

volumes:
  postgres_data:
  rabbitmq_data:

services:

  # ── Keycloak database ──────────────────────────────────────────
  postgres:
    image: postgres:15
    networks: [drone-net]
    environment:
      POSTGRES_DB:       keycloak
      POSTGRES_USER:     keycloak
      POSTGRES_PASSWORD: keycloak_secret
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "keycloak"]
      interval: 10s
      retries: 5

  # ── Identity server ────────────────────────────────────────────
  keycloak:
    image: quay.io/keycloak/keycloak:24.0
    command: start-dev
    networks: [drone-net]
    ports:
      - "8080:8080"     # Admin UI: http://localhost:8080
    environment:
      KC_DB:                   postgres
      KC_DB_URL:               jdbc:postgresql://postgres/keycloak
      KC_DB_USERNAME:          keycloak
      KC_DB_PASSWORD:          keycloak_secret
      KEYCLOAK_ADMIN:          admin
      KEYCLOAK_ADMIN_PASSWORD: Admin@KP2025!
    depends_on:
      postgres: { condition: service_healthy }
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:8080/health/live || exit 1"]
      interval: 20s
      retries: 10
      start_period: 60s

  # ── Message queue ──────────────────────────────────────────────
  rabbitmq:
    image: rabbitmq:3.13-management
    networks: [drone-net]
    ports:
      - "5672:5672"     # AMQP (operator laptops / GPU consumer)
      - "15672:15672"   # Management UI: http://localhost:15672
    environment:
      RABBITMQ_DEFAULT_USER: ingest_user
      RABBITMQ_DEFAULT_PASS: Rabbit@KP2025!
    volumes:
      - rabbitmq_data:/var/lib/rabbitmq
    healthcheck:
      test: ["CMD", "rabbitmq-diagnostics", "ping"]
      interval: 10s
      retries: 10

  # ── FastAPI ingest server ──────────────────────────────────────
  api:
    build: ./api
    networks: [drone-net]
    environment:
      KEYCLOAK_URL: http://keycloak:8080
      KC_REALM:     kerala-police-drone
      KC_CLIENT_ID: drone-ingest-client
      RABBITMQ_URL: amqp://ingest_user:Rabbit@KP2025!@rabbitmq/
      MAX_FRAME_MB: "5"
    depends_on:
      keycloak:  { condition: service_healthy }
      rabbitmq:  { condition: service_healthy }
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 10s
      retries: 5

  # ── Reverse proxy ──────────────────────────────────────────────
  nginx:
    image: nginx:1.26
    networks: [drone-net]
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/default.conf:/etc/nginx/conf.d/default.conf:ro
      - ./certs/ingest.crt:/etc/ssl/certs/ingest.crt:ro
      - ./certs/ingest.key:/etc/ssl/private/ingest.key:ro
    depends_on:
      api: { condition: service_healthy }
```

---

## 8. Start All Services

```powershell
# Navigate to project folder
cd C:\drone-ingest

# Pull all images (one-time, takes a few minutes)
docker compose pull

# Start everything (builds FastAPI image automatically)
docker compose up -d

# Watch startup logs
docker compose logs -f

# Verify all containers are healthy
docker compose ps
```

Expected `docker compose ps` output:

```
NAME        STATUS          PORTS
postgres    Up (healthy)    5432/tcp
keycloak    Up (healthy)    0.0.0.0:8080->8080/tcp
rabbitmq    Up (healthy)    0.0.0.0:5672->5672, 0.0.0.0:15672->15672
api         Up (healthy)    8000/tcp
nginx       Up              0.0.0.0:80->80, 0.0.0.0:443->443
```

> **Note:** Keycloak takes ~60 seconds to become healthy on first start. Wait until `docker compose ps` shows it as `Up (healthy)` before proceeding.

---

## 9. Configure Keycloak

Open **http://localhost:8080** → Click "Administration Console" → Login with `admin` / `Admin@KP2025!`

| Step | Action |
|------|--------|
| **1. Create Realm** | Top-left dropdown → Create Realm → Name: `kerala-police-drone` → Create |
| **2. Create Client** | Clients → Create client → Client ID: `drone-ingest-client` → Next |
| **3. Set Auth** | Client authentication: **ON** · Standard flow: **OFF** · Direct access grants: **ON** → Save |
| **4. Copy Secret** | Clients → `drone-ingest-client` → Credentials tab → copy the **Client Secret** |
| **5. Create Roles** | Realm roles → Create role → `drone-operator` (repeat for `control-room`, `supervisor`, `sys-admin`) |
| **6. Create Users** | Users → Create user → Username: `operator_001` → Credentials tab → Set password → Temporary: **OFF** |
| **7. Assign Role** | Users → `operator_001` → Role mapping → Assign role → `drone-operator` |
| **8. Token TTL** | Realm settings → Tokens tab → Access Token Lifespan → **5 Minutes** → Save |

Repeat step 6–7 for `operator_002` through `operator_005`.

---

## 10. Create RabbitMQ Queues

Open **http://localhost:15672** → Login: `ingest_user` / `Rabbit@KP2025!`

For each drone ID (`DRONE_KL_001` through `DRONE_KL_005`):

1. Click **Queues and Streams** tab → scroll to **Add a new queue**
2. Type: **Classic** · Durability: **Durable**
3. Name: `drone.frames.DRONE_KL_001`
4. Add arguments:

| Argument | Type | Value |
|----------|------|-------|
| `x-max-length` | Number | `1000` |
| `x-overflow` | String | `drop-head` |
| `x-message-ttl` | Number | `30000` |

5. Click **Add queue**
6. Repeat for `DRONE_KL_002` → `DRONE_KL_005`
7. Create one more queue: `drone.events.confirmed` (Durable, no arguments)

---

## 11. Operator Laptop Client

Install dependency: `pip install httpx`

Save as `edge_sender.py` on the operator laptop:

```python
import httpx, json, time, asyncio

INGEST_URL = "https://localhost/ingest/frame"
TOKEN_URL  = "http://localhost:8080/realms/kerala-police-drone/protocol/openid-connect/token"
CLIENT_ID  = "drone-ingest-client"
CLIENT_SEC = "PASTE_YOUR_CLIENT_SECRET_HERE"   # from Keycloak credentials tab

class IngestClient:
    def __init__(self, drone_id, mission_id, username, password):
        self.drone_id     = drone_id
        self.mission_id   = mission_id
        self.username     = username
        self.password     = password
        self.token        = None
        self.token_expiry = 0
        self.frame_seq    = 0

    async def get_token(self):
        """Automatically refreshes token 30 seconds before expiry."""
        if time.time() < self.token_expiry - 30:
            return self.token
        async with httpx.AsyncClient() as c:
            r = await c.post(TOKEN_URL, data={
                "client_id":     CLIENT_ID,
                "client_secret": CLIENT_SEC,
                "username":      self.username,
                "password":      self.password,
                "grant_type":    "password"
            })
            d = r.json()
        self.token        = d["access_token"]
        self.token_expiry = time.time() + d["expires_in"]
        return self.token

    async def send_frame(self, frame_path: str, change_score: float,
                         lat=None, lon=None, alt=None):
        token = await self.get_token()
        self.frame_seq += 1
        meta = {
            "drone_id":       self.drone_id,
            "mission_id":     self.mission_id,
            "timestamp":      time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            "frame_sequence": self.frame_seq,
            "change_score":   change_score,
            "latitude":       lat,
            "longitude":      lon,
            "altitude_m":     alt
        }
        with open(frame_path, "rb") as f:
            frame_bytes = f.read()

        async with httpx.AsyncClient(verify=False) as c:   # self-signed cert
            r = await c.post(
                INGEST_URL,
                headers={"Authorization": f"Bearer {token}"},
                files={"frame": ("frame.jpg", frame_bytes, "image/jpeg")},
                data={"metadata_json": json.dumps(meta)},
                timeout=10
            )
        print(f"[{self.drone_id}] Frame {self.frame_seq}: HTTP {r.status_code} — {r.text}")


# ── Quick test ────────────────────────────────────────────────────────────────
async def test():
    client = IngestClient(
        drone_id   = "DRONE_KL_001",
        mission_id = "MISSION_TEST",
        username   = "operator_001",
        password   = "YOUR_PASSWORD"
    )
    await client.send_frame("C:\\test_frame.jpg", change_score=0.75)

asyncio.run(test())
```

---

## 12. GPU Batch Consumer

Install dependency: `pip install aio-pika`

```python
# gpu_consumer.py — run on the GPU analytics server
import aio_pika, asyncio

RABBITMQ_URL = "amqp://ingest_user:Rabbit@KP2025!@localhost/"
DRONE_IDS    = ["DRONE_KL_001", "DRONE_KL_002", "DRONE_KL_003",
                "DRONE_KL_004", "DRONE_KL_005"]
BATCH_SIZE   = 16   # Frames per GPU inference batch

batch = []

async def on_frame(message: aio_pika.IncomingMessage):
    async with message.process():
        batch.append({
            "drone_id":  message.headers.get("drone_id"),
            "timestamp": message.headers.get("timestamp"),
            "frame":     message.body
        })
        if len(batch) >= BATCH_SIZE:
            await run_inference(batch.copy())
            batch.clear()

async def run_inference(frames: list):
    # Replace with actual GPU model call
    print(f"GPU batch: {len(frames)} frames from "
          f"{set(f['drone_id'] for f in frames)}")

async def main():
    conn    = await aio_pika.connect_robust(RABBITMQ_URL)
    channel = await conn.channel()
    await channel.set_qos(prefetch_count=BATCH_SIZE)
    for did in DRONE_IDS:
        q = await channel.declare_queue(f"drone.frames.{did}", durable=True)
        await q.consume(on_frame)
    print("GPU consumer running — waiting for frames...")
    await asyncio.Future()   # run forever

asyncio.run(main())
```

---

## 13. Testing Checklist

### Infrastructure
- [ ] `curl http://localhost:8080/health/live` → `200 OK`
- [ ] `http://localhost:15672` → RabbitMQ management dashboard loads
- [ ] `curl http://localhost:8000/health` → `{"status":"ok"}`
- [ ] `curl -k https://localhost/health` → `{"status":"ok"}`

### Authentication
- [ ] POST to Keycloak token URL with `operator_001` credentials → JWT returned
- [ ] POST `/ingest/frame` without `Authorization` header → `401 Unauthorized`
- [ ] POST with a token that has `control-room` role only → `403 Forbidden`
- [ ] Wait 5 minutes, reuse old token → `401`, then refresh works correctly

### Frame Ingestion
- [ ] Run `edge_sender.py` test → `HTTP 202 {"status":"queued"}`
- [ ] RabbitMQ UI Queues tab → `drone.frames.DRONE_KL_001` shows 1 message
- [ ] Start `gpu_consumer.py` → "GPU batch: 1 frames" printed
- [ ] Send 6 MB image → `HTTP 413`
- [ ] Send PDF as frame → `HTTP 415`

### Docker Resilience
- [ ] `docker compose restart rabbitmq` → FastAPI reconnects automatically
- [ ] `docker compose restart api` → health check recovers
- [ ] Send 1100 frames rapidly → oldest 100 dropped, no crash

---

## 14. Useful Docker Commands

```powershell
# Start all services
docker compose up -d

# Stop all services (data preserved)
docker compose stop

# Stop AND delete all data (full reset)
docker compose down -v

# View live logs for FastAPI
docker compose logs -f api

# Rebuild only FastAPI after code changes
docker compose up -d --build api

# Check container status
docker compose ps

# Open a shell inside the FastAPI container
docker compose exec api bash

# Live resource usage
docker stats
```

---

## Quick Reference

| URL | Purpose |
|-----|---------|
| `https://localhost/ingest/frame` | Frame upload endpoint (operator laptops) |
| `https://localhost/health` | Health check |
| `http://localhost:8080` | Keycloak Admin UI |
| `http://localhost:15672` | RabbitMQ Management UI |
| `http://localhost:8000/docs` | FastAPI Swagger UI (internal) |

> **Security reminder:** All credentials in this guide are examples for local development. Replace passwords and `CLIENT_SECRET` with secrets managed through an environment variable store before connecting real operator laptops.
>
> **Next step:** When you scale to ~20 drones (Phase 2), migrate the RabbitMQ backbone to **Apache Kafka** and the REST ingest to **gRPC** for higher throughput — see the Phase 2 guide.
