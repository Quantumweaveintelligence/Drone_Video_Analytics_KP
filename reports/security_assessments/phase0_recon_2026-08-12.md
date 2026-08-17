# Phase 0 Reconnaissance & Pre-Test Analysis Report
**Date & Time:** 2026-08-12 11:00:00 AM IST (+05:30)  
**Target Project:** DVAS (Drone Video Analytics System — Kerala Police)  
**Scope:** Read-only passive reconnaissance. No code was modified.

---

## 1. API Router Discovery

**File:** `DVAS_Dashboard/backend/server.py`

### Commands Used
```bash
# View the top of the server file to find app/router initialization
view_file DVAS_Dashboard/backend/server.py  # lines 1–50

# Also checked the ingest_proxy module (imported router)
view_file DVAS_Dashboard/backend/server.py  # line 41: from ingest_proxy import ingest_api
```

| Symbol | Line | Declaration |
|--------|------|-------------|
| `app` | 47 | `app = FastAPI(title="DVAS API")` |
| `api` | 48 | `api = APIRouter(prefix="/api")` |
| `ingest_api` | 41 | imported from `ingest_proxy` → `APIRouter(prefix="/ingest")` |

- **Primary Router Variable Name:** `api` (prefix `/api`)
- **Secondary Router:** `ingest_api` (proxy router, prefix `/ingest`, imported from `ingest_proxy.py`)
- Both routers are included into `app` at the bottom of the file (lines 2545–2546).

---

## 2. API Endpoint Count

**Primary `api` router (`server.py`):** 70 route decorators
**Ingest proxy router (`ingest_proxy.py`):** 10 route decorators
**WebSocket:** 1 (`@app.websocket("/api/ws")`)

- **Total API Endpoints Found: 81** (70 + 10 + 1 WebSocket)

### Commands Used
```bash
# Count all route decorators in server.py on the `api` router
grep -c "@api\.\(get\|post\|put\|delete\|patch\)(" \
    DVAS_Dashboard/backend/server.py
# Output: 70

# Count route decorators in ingest_proxy.py
grep -c "@ingest_api\.\|@router\.\|@api\." \
    DVAS_Dashboard/backend/ingest_proxy.py
# Output: 10 (after deduplication — 10 @ingest_api.get/post routes)

# List all routes with line numbers (server.py)
grep -n "@api\.\(get\|post\|put\|delete\|patch\)(" \
    DVAS_Dashboard/backend/server.py

# List all ingest proxy routes
grep -n "@.*\.\(get\|post\|put\|delete\|patch\)(" \
    DVAS_Dashboard/backend/ingest_proxy.py
```

### Breakdown by HTTP Method (`server.py` `api` router)
| Method | Count |
|--------|-------|
| GET    | 61    |
| POST   | 8     |
| PATCH  | 2     |
| DELETE | 1     |
| **Total** | **72** (including `@api.get("/")` root + `ingest_api` contributes 10 more) |

---

## 3. Hardcoded Secrets & Credentials

All findings below are **literal values embedded in source code**, not loaded from runtime environment variables.

### Commands Used
```bash
# Search for known password/secret strings across all relevant file types
grep -rn --include="*.py" --include="*.yml" --include="*.yaml" \
    -E "(Operator@|Admin@|Grafana@|dvas_secret|keycloak_secret|super-secret|dvas1234)" .

# Search for Keycloak client secrets and MinIO default credentials
grep -rn --include="*.py" --include="*.yml" --include="*.yaml" \
    -E "(minioadmin|KC_CLIENT_SEC|Client_SEC|yNa0)" .

# Search for POSTGRES_PASSWORD references
grep -rn --include="*.py" --include="*.yml" --include="*.yaml" \
    "POSTGRES_PASSWORD" .

# Manually read edge_sender.py to find default credentials
view_file dvas_edge/ingest/edge_sender.py        # lines 1–60

# Read setup_keycloak.py to confirm Keycloak admin credentials
view_file dvas_ingest/ingest/scripts/setup_keycloak.py  # lines 1–45

# Read edge config for RTSP camera credentials
view_file dvas_edge/config/config.py              # lines 28–45
```

| # | File | Line | Secret |
|---|------|------|--------|
| 1 | `dvas_ingest/ingest/scripts/setup_keycloak.py` | 29 | `ADMIN_PASSWORD = "Admin@KP2025!"` |
| 2 | `dvas_ingest/ingest/scripts/setup_keycloak.py` | 36–40 | `"password": "Operator@001"` through `"Operator@005"` (5 operator accounts) |
| 3 | `dvas_edge/ingest/edge_sender.py` | 45 | `CLIENT_SEC = os.getenv("KC_CLIENT_SEC", "j2Icr4lPJHHlvcJBu04nIH7A74k3cl4c")` — Keycloak client secret hardcoded as default fallback |
| 4 | `dvas_edge/ingest/edge_sender.py` | 48 | `PASSWORD = os.getenv("EDGE_PASSWORD", "Operator@001")` — operator password as default fallback |
| 5 | `dvas_edge/config/settings.yaml` | 89 | `password: "Operator@001"   # rotate after pilot` |
| 6 | `DVAS_Dashboard/docker-compose.yml` | 10 | `POSTGRES_PASSWORD: dvas_secret` (plaintext in compose env block) |
| 7 | `DVAS_Dashboard/docker-compose.yml` | 33 | `DATABASE_URL=postgresql+asyncpg://dvas:dvas_secret@postgres:5432/dvas` |
| 8 | `DVAS_Dashboard/docker-compose.yml` | 34 | `JWT_SECRET=super-secret-dvas-key-change-in-prod` ⚠️ **CRITICAL** |
| 9 | `DVAS_Dashboard/docker-compose.yml` | 69 | `KC_CLIENT_SEC=yNa0AfA0mToUmhQHSiM2vjAyYud6NSMn` — second Keycloak client secret |
| 10 | `DVAS_Dashboard/backend/server.py` | 755 | `password: str = "dvas1234"` — default password in `UserCreateIn` Pydantic model |
| 11 | `DVAS_Dashboard/backend/server.py` | 1884 | `password_hash=hash_password("dvas1234")` — seed password for all 6 DVAS Dashboard users |
| 12 | `dvas_ingest/docker-compose.yml` | 85 | `POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-keycloak_secret}` — fallback DB password |
| 13 | `dvas_ingest/docker-compose.yml` | 113 | `KEYCLOAK_ADMIN_PASSWORD: ${KEYCLOAK_ADMIN_PASSWORD:-Admin@KP2025!}` — same as #1 |
| 14 | `dvas_ingest/docker-compose.yml` | 360 | `GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:-Grafana@KP2025!}` — Grafana admin |
| 15 | `dvas_analytics/shared/config.py` | 21–22 | `MINIO_ACCESS_KEY/MINIO_SECRET_KEY` default `"minioadmin"` |
| 16 | `dvas_edge/config/config.py` | 30 | `rtsp_url: str = "rtsp://admin:Admin12345@10.10.32.23:554/..."` — camera credentials |

> **Note:** Items 3, 4, 15 use `os.getenv()` but carry insecure hardcoded defaults that activate when the env var is absent. Items 1, 2, 5, 6, 7, 8, 9, 10, 11, 16 are unconditional hardcoded literals.

---

## 4. Backend Dependencies Inventory

**File:** `DVAS_Dashboard/backend/requirements.txt`

### Command Used
```bash
# Read the full requirements file
view_file DVAS_Dashboard/backend/requirements.txt

# Count non-blank, non-comment lines
grep -c "^[^#[:space:]]" DVAS_Dashboard/backend/requirements.txt
# Output: 33
```

- **Total Count:** 33 dependencies (excluding blank line and comment)

### First 10 Lines
```
fastapi==0.110.1
uvicorn==0.25.0
boto3>=1.34.129
requests-oauthlib>=2.0.0
cryptography>=42.0.8
python-dotenv>=1.0.1
sqlalchemy[asyncio]>=2.0.0
asyncpg>=0.29.0
pydantic>=2.6.4
email-validator>=2.2.0
```

### Risk/Notable Libraries
| Library | Version | Risk / Note |
|---------|---------|-------------|
| `fastapi` | `==0.110.1` | Pinned to older minor — check for CVEs |
| `python-jose` | `>=3.3.0` | Known CVEs (CVE-2024-33664/33663); `pyjwt` also present → dual JWT library footprint |
| `boto3` | `>=1.34.129` | AWS SDK — indicates cloud/S3 connectivity from backend |
| `docker` | `>=7.0.0` | **Docker socket SDK** — paired with host socket mount; container escape risk ⚠️ |
| `passlib` | `>=1.7.4` | Legacy auth library alongside `bcrypt` — redundant |
| `fpdf2` | `==2.8.7` | PDF generation for evidence bundles and shift summaries |

---

## 5. Frontend Dependencies Inventory

**File:** `DVAS_Dashboard/frontend/package.json`

### Command Used
```bash
# Read the full package.json
view_file DVAS_Dashboard/frontend/package.json

# Count production dependencies (between "dependencies": { ... })
# Counted manually from file output: 60 production, 14 devDependencies
```

- **React Version:** `19.0.0`
- **Axios Version:** `1.16.0`
- **Total npm Production Dependencies:** 60
- **Total devDependencies:** 14
- **Grand Total:** 74

### Notable Frontend Libraries
- `react-router-dom@7.15.0`, `@tanstack/react-query@5.56.2`, `leaflet` (maps), `recharts` (charts), `framer-motion` (animations), `zod` (validation), `react-hook-form`

---

## 6. Exposed Docker Ports

**Files:** `DVAS_Dashboard/docker-compose.yml` and `dvas_ingest/docker-compose.yml`

### Commands Used
```bash
# Read DVAS_Dashboard docker-compose (101 lines)
view_file DVAS_Dashboard/docker-compose.yml

# Read dvas_ingest docker-compose (693 lines)
view_file dvas_ingest/docker-compose.yml

# Also searched for all ports: mappings
grep -n "ports:" -A 3 DVAS_Dashboard/docker-compose.yml
grep -n "ports:" -A 3 dvas_ingest/docker-compose.yml
```

### `DVAS_Dashboard/docker-compose.yml`
| Service | Host Port | Container Port | Bind | Notes |
|---------|-----------|----------------|------|-------|
| postgres | **5433** | 5432 | all | PostgreSQL — non-standard host port |
| backend | **8001** | 8000 | all | FastAPI DVAS backend |
| edge | **8765** | 8765 | all | Edge API server (WebSocket) |
| frontend | **3000** | 80 | all | React SPA via Nginx |

### `dvas_ingest/docker-compose.yml`
| Service | Host Port | Container Port | Bind | Notes |
|---------|-----------|----------------|------|-------|
| keycloak | **8080** | 8080 | `0.0.0.0` | IAM — publicly bound ⚠️ |
| headscale | **8081** | 8080 | `0.0.0.0` | VPN control plane — publicly bound ⚠️ |
| step-ca | **9000** | 9000 | `127.0.0.1` | Internal CA — localhost only ✅ |
| ingest-api | **8000** | 8000 | `0.0.0.0` | FastAPI ingest — publicly bound ⚠️ |
| nginx | **80** | 80 | all | HTTP → HTTPS redirect |
| nginx | **443** | 443 | all | HTTPS — VPN-facing |
| seaweedfs-master | **9333** | 9333 | `127.0.0.1` | localhost only ✅ |
| seaweedfs-filer | **8888** | 8888 | `127.0.0.1` | localhost only ✅ |
| prometheus | **9090** | 9090 | `127.0.0.1` | localhost only ✅ |
| grafana | **3001** | 3000 | `0.0.0.0` | Grafana UI — publicly bound ⚠️ |
| alertmanager | **9093** | 9093 | `127.0.0.1` | localhost only ✅ |
| crowd-analyzer | **8101** | 8101 | `0.0.0.0` | Analytics — publicly bound ⚠️ |
| person-analyzer | **8102** | 8102 | `0.0.0.0` | Analytics — publicly bound ⚠️ |
| vehicle-analyzer | **8103** | 8103 | `0.0.0.0` | Analytics — publicly bound ⚠️ |
| anpr-analyzer | **8104** | 8104 | `0.0.0.0` | Analytics — publicly bound ⚠️ |
| minio | **9000** | 9000 | `0.0.0.0` | S3 API — publicly bound ⚠️ |
| minio console | **9001** | 9001 | `127.0.0.1` | localhost only ✅ |
| analytics-gateway | **8100** | 8100 | `0.0.0.0` | Analytics gateway — publicly bound ⚠️ |

### All Exposed Host Ports (combined)
`80, 443, 3000, 3001, 5433, 8000, 8001, 8080, 8081, 8100, 8101, 8102, 8103, 8104, 8765, 9000`

---

## 7. Edge Node Default Configuration

**Files:** `dvas_edge/ingest/edge_sender.py` and `dvas_edge/config/config.py`

### Commands Used
```bash
# Read edge_sender.py to find host, port, username, password defaults
view_file dvas_edge/ingest/edge_sender.py    # lines 1–80 (configuration block)

# Read config.py to find IngestConfig and DroneConfig defaults
view_file dvas_edge/config/config.py         # lines 28–140 (dataclass definitions)

# Also confirmed via grep
grep -rn --include="*.py" --include="*.yaml" \
    -E "(Operator@|Admin12345|10\.51\.27|10\.10\.32)" \
    dvas_edge/
```

| Parameter | Default Value | Source File | Line |
|-----------|--------------|-------------|------|
| **Ingest Host** | `10.51.27.250` | `edge_sender.py` | 42 |
| **Ingest Port** | `80` (HTTP) | `edge_sender.py` | 42 |
| **Keycloak (Token) Host** | `10.51.27.250` | `edge_sender.py` | 43 |
| **Keycloak Port** | `8080` | `edge_sender.py` | 43 |
| **Username** | `operator_001` | `edge_sender.py` | 47 |
| **Password** | `Operator@001` ⚠️ | `edge_sender.py` | 48 |
| **Keycloak Client Secret** | `j2Icr4lPJHHlvcJBu04nIH7A74k3cl4c` ⚠️ | `edge_sender.py` | 45 |
| **TLS Verify** | `false` ⚠️ | `edge_sender.py` | 58 |
| **Drone ID** | `DRONE_KL_001` | `edge_sender.py` | 49 |
| **RTSP Camera URL** | `rtsp://admin:Admin12345@10.10.32.23:554/...` ⚠️ | `config.py` | 30 |
| **IngestConfig URL** | `https://ingest.dvas.internal/ingest/frame` | `config.py` | 126 |

> `config.py` and `edge_sender.py` reference different IP ranges (`10.10.x.x` vs `10.51.27.x`), suggesting multiple deployment environments exist.

---

## 8. Keycloak Admin Credentials

**File:** `dvas_ingest/ingest/scripts/setup_keycloak.py`

### Commands Used
```bash
# List scripts directory
list_dir dvas_ingest/ingest/scripts/
# Found: setup_keycloak.py, setup_ubuntu.sh, verify.py

# Read the full setup script
view_file dvas_ingest/ingest/scripts/setup_keycloak.py  # lines 1–45

# Cross-reference with docker-compose.yml environment block
view_file dvas_ingest/docker-compose.yml    # lines 99–125 (keycloak service)

# Also confirmed via grep
grep -rn --include="*.py" --include="*.yml" "Admin@KP2025" .
```

### Hardcoded in `setup_keycloak.py` (lines 27–29)
```python
KC_URL         = "http://localhost:8080"
ADMIN_USER     = "admin"
ADMIN_PASSWORD = "Admin@KP2025!"
```

### Confirmed in `dvas_ingest/docker-compose.yml` (lines 112–113)
```yaml
KEYCLOAK_ADMIN:          admin
KEYCLOAK_ADMIN_PASSWORD: ${KEYCLOAK_ADMIN_PASSWORD:-Admin@KP2025!}
```

| Field | Value |
|-------|-------|
| **Username** | `admin` |
| **Password** | `Admin@KP2025!` |
| **Realm accessed** | `master` (for admin token) |
| **App Realm** | `kerala-police-drone` |

### Operator users seeded by `setup_keycloak.py` (lines 36–40)
| Username | Password |
|----------|----------|
| `operator_001` | `Operator@001` |
| `operator_002` | `Operator@002` |
| `operator_003` | `Operator@003` |
| `operator_004` | `Operator@004` |
| `operator_005` | `Operator@005` |

---

## Summary of Key Findings

> **Analyst Notes incorporated (2026-08-12):** Severity ratings and phase priorities below have been updated based on findings review. Five specific observations have reshaped the Phase 1–3 test plan.

---

### 🔴 Critical

- **`SKIP_AUTH: "true"` on `ingest-api`** (`dvas_ingest/docker-compose.yml` line 218) — **ESCALATED from Medium.** Keycloak JWT validation is entirely disabled for the LAN demo configuration. This is the single most impactful finding: the entire planned Phase 2 GPS/telemetry spoofing test may require **zero credential theft** as a prerequisite. An unauthenticated attacker on the LAN can POST arbitrary frames and telemetry directly. **Test this first before any other Phase 1–2 activity.** If confirmed, it collapses the attack chain from Phase 3 back to Phase 1 with no exploitation needed.

- **JWT_SECRET hardcoded in `DVAS_Dashboard/docker-compose.yml` (line 34):** `super-secret-dvas-key-change-in-prod`
  > **Root-cause note:** The application code itself is sound — `server.py` line 45 loads `JWT_SECRET` via `os.environ["JWT_SECRET"]` and raises `KeyError` on startup if absent, which is the correct pattern. The vulnerability is **entirely in the deployment config**, not the application code. Remediation must target `docker-compose.yml` (and any CI/CD pipelines that consume it), not the Python source. This distinction matters for accurate remediation guidance.

- **Keycloak admin credentials hardcoded:** `admin` / `Admin@KP2025!` in `setup_keycloak.py` (line 29) and as a fallback in `docker-compose.yml` (line 113). Compromise grants full IAM control — ability to create users, rotate client secrets, and modify realm policy.

- **MinIO publicly exposed with default credentials** — **ESCALATED from High.** Port `9000` is bound to `0.0.0.0` with fallback credentials `minioadmin`/`minioadmin`. This is a **Critical** finding before any PII exposure testing: all stored evidence crops, face images, plate images, heatmaps, and video evidence are directly downloadable by any network-adjacent attacker with no prerequisites. The `minio-init` entrypoint (`docker-compose.yml` line 641) also sets `mc anonymous set download local/dvas-evidence` — meaning the `dvas-evidence` bucket is world-readable by design.

- **Docker socket mount + `docker` SDK = container escape primitive** (`DVAS_Dashboard/docker-compose.yml` line 29: `/var/run/docker.sock:/var/run/docker.sock`). The `docker>=7.0.0` package is present in `requirements.txt`. If the backend is compromised via any secondary vulnerability (injection, SSRF, RCE), the mounted socket provides a direct host escape path:
  ```bash
  # Proof-of-concept from inside a compromised backend container:
  docker run -v /:/host --rm -it alpine chroot /host sh
  ```
  **Add to Phase 3/4 test plan:** Confirm `docker.sock` is reachable from inside the backend container, then demonstrate container-to-host escape via the mounted socket. This was not in the original Phase 0 plan and should be explicitly added.

- **Keycloak port 8080 bound to `0.0.0.0`** — admin console network-reachable without VPN.

---

### 🟠 High

- **`Operator@001` credential is IAM-wide, not just edge-scoped** — this was predicted for `dvas_edge/config/config.py` and `edge_sender.py`, but it cascades further: the same password is the seeded Keycloak operator password (`setup_keycloak.py` lines 36–40) for all five `operator_00x` accounts AND the default fallback in `settings.yaml`. Compromise of a single edge device therefore yields valid Keycloak credentials for up to 5 operator accounts across the entire system.

- **Two divergent Keycloak client secrets in the repository:**
  - `j2Icr4lPJHHlvcJBu04nIH7A74k3cl4c` → `dvas_edge/ingest/edge_sender.py` line 45
  - `yNa0AfA0mToUmhQHSiM2vjAyYud6NSMn` → `DVAS_Dashboard/docker-compose.yml` line 69
  > **Phase 1 test target:** Attempt token acquisition against the live Keycloak endpoint using both secrets independently. The one that succeeds tells you which rotation was applied and which is stale — an attacker with repo access only needs the valid one.

- **TLS verification disabled by default** (`TLS_VERIFY=false` in `edge_sender.py` line 58) — all edge-to-ingest frame transmissions are susceptible to MITM without any additional configuration.

- **Camera RTSP URL with credentials hardcoded:** `rtsp://admin:Admin12345@10.10.32.23:554/h264/ch1/main/av_stream` in `dvas_edge/config/config.py` line 30.

- **All 6 DVAS Dashboard user accounts** seeded with password `dvas1234` at first startup (`server.py` line 1884). Accounts include `admin@dvas.kp`, `senior@dvas.kp`, `supervisor@dvas.kp`.

- **Analytics microservices (8100–8104) and Grafana (3001)** bound to `0.0.0.0` with no network-layer authentication.

---

### 🟡 Medium

- **IP range inconsistency between `config.py` and `edge_sender.py`:**
  - `dvas_edge/config/config.py` (line 129): Keycloak at `http://10.10.20.1:8080`, ingest at `https://ingest.dvas.internal`
  - `dvas_edge/ingest/edge_sender.py` (lines 42–43): Keycloak and ingest both at `10.51.27.250`
  > These reference different IP subnets — `10.10.x.x` vs `10.51.27.x` — suggesting two distinct deployment environments (possibly lab vs. field). **Flag for manual verification:** confirm which subnet is reachable in the test environment; the stale IP range may point to a decommissioned network segment or a config left over from an earlier deployment. Either way, this is an inconsistency that should be resolved before production.

- **Dual JWT library footprint** (`python-jose` + `pyjwt` both in `requirements.txt`) — `python-jose` has known CVEs (CVE-2024-33664 / CVE-2024-33663); ambiguity about which is used for verification increases audit complexity.

- **Grafana admin password `Grafana@KP2025!`** hardcoded as a fallback — exposes Prometheus metrics, alerting rules, and infrastructure dashboards.

- **Postgres exposed on host port 5433** in the dashboard compose — database directly reachable from host network.

- **React 19.0.0** — newest major; monitor for ecosystem CVEs.

- **Axios 1.16.0** — check against latest stable.

---

### 🟢 Positive Observations

- **Hash-chained audit log** implemented (SHA-256 over ordered fields with `prev_hash` linkage) — good tamper-evidence design for a law enforcement context.
- **4-role RBAC** (`drone_operator`, `control_room_officer`, `senior_officer`, `system_administrator`) with ABAC jurisdiction scoping — reasonably fine-grained access control model.
- **bcrypt** used for password hashing in the dashboard backend.
- **`JWT_SECRET` code pattern is correct** — `os.environ["JWT_SECRET"]` (not `os.getenv`) ensures a hard startup failure if unset. The weakness is entirely in the deployment config, not the application logic.
- **Internal services** (step-ca, SeaweedFS, Prometheus, Alertmanager, MinIO console) correctly bound to `127.0.0.1` only.
- **Section 65B evidence bundle** generation with PDF certificate and ZIP packaging indicates awareness of legal chain-of-custody requirements.

---

### Phase Priority Adjustments (Post-Phase 0 Review)

> **Operational sequencing note:** Within P0, run lowest-risk checks first — passive, zero-write, zero-interaction — before any test that writes data into the live system. MinIO anonymous-download confirmation is a pure passive HTTP GET; it does not interact with application logic and produces no side effects. Confirming `SKIP_AUTH=true` requires POSTing frames/telemetry into the running ingest-api, which is a write action even if the intent is read-only. Always confirm the "free" findings before touching app logic.

| Priority | Test | Method | Reason |
|----------|------|--------|--------|
| **P0-a** | Confirm `dvas-evidence` anonymous download on MinIO port 9000 | Passive `curl`/`mc ls` — no auth, no write | Zero-interaction passive confirmation; `mc anonymous set download` is already set in init script |
| **P0-b** | Confirm MinIO API accepts `minioadmin`/`minioadmin` | Unauthenticated S3 `ListBuckets` call | Verifies default creds before any credential-theft tests |
| **P0-c** | Test `SKIP_AUTH=true` — POST a synthetic frame to ingest-api unauthenticated | Active: sends one test frame | Determines whether Phase 2/3 prerequisite chain collapses |
| **P1-a** | Test both KC client secrets against live Keycloak | `curl` token endpoint with each secret independently | The valid one identifies which rotation was incomplete |
| **P1-b** | Verify IP reachability: `10.10.x.x` vs `10.51.27.x` | `ping` / `curl` against both subnets | Identifies which environment is the active test target |
| **P2** | GPS/telemetry spoofing via ingest-api | Active: inject false coordinates | Likely trivial if P0-c confirmed; maps to Phase 2 headline test |
| **P3/P4** | Docker socket escape PoC from backend container | Active: `docker run` against mounted socket | New finding — not in original plan, add to Phase 3/4 explicitly |
| **P3** | **Chained attack: ingest-write → MinIO-read** | Combine P0-c + P0-a | See dedicated section below — highest-severity composite finding |

---

### ⛓️ Chained Attack Finding: Unauthenticated Evidence Injection

> **This is potentially the most severe single finding in the entire assessment, given the legal context of this system.**

**Attack chain:**

```
Step 1 — Unauthenticated ingest write (SKIP_AUTH=true)
  └─ POST fabricated frame + metadata to http://<ingest-server>:8000/ingest/frame
       No token required. Any LAN-adjacent attacker can submit frames
       with arbitrary drone_id, mission_id, GPS coordinates, timestamp.

Step 2 — GPU consumer processes the injected frame
  └─ Fabricated frame enters the Valkey queue → GPU batch consumer
       picks it up as legitimate drone telemetry.
       Analytics services (crowd-analyzer, person-analyzer, etc.)
       run inference on attacker-controlled image content.

Step 3 — Evidence stored in MinIO dvas-evidence bucket
  └─ If the frame is flagged as a confirmed event, an evidence crop
       is written to MinIO: dvas-evidence/<drone_id>/<timestamp>.jpg

Step 4 — Evidence bucket is world-readable (anonymous download)
  └─ mc anonymous set download local/dvas-evidence
       Any unauthenticated HTTP GET retrieves the fabricated evidence
       — or, more critically, ANY previously stored legitimate evidence.
```

**What this means:**

| Impact Dimension | Detail |
|-----------------|--------|
| **Evidence fabrication** | An attacker can inject a synthetic frame (e.g., a crowd scene, a licence plate, a face) and have it processed by GPU analytics as a real confirmed event — creating a false evidence record in the DVAS system |
| **Evidence contamination** | Fabricated frames enter the same Valkey queue as real drone frames. There is no cryptographic binding between a frame and its originating drone |
| **Chain-of-custody breach** | The DVAS system generates Section 65B certificates (legally admissible under the Indian Evidence Act) for confirmed events. A fabricated event would produce a court-ready certificate for content the system never actually captured |
| **Evidence exfiltration** | The world-readable `dvas-evidence` bucket means all real confirmed evidence is passively downloadable by any network-adjacent actor, with no credentials required |
| **Audit log integrity** | The hash-chained audit log records evidence access, but it does not authenticate the origin of ingested frames — a fabricated injection event appears identical to a real one in the audit trail |

**Verification test (Phase 3):**
```bash
# Step 1: POST a synthetic frame with no token
curl -X POST http://<ingest-ip>:8000/ingest/frame \
     -F "frame=@synthetic_crowd.jpg;type=image/jpeg" \
     -F 'metadata_json={"drone_id":"DRONE_KL_001","mission_id":"M-101",
         "timestamp":"2026-08-12T07:00:00Z","frame_sequence":1,
         "change_score":0.95,"latitude":8.5241,"longitude":76.9366}'

# Step 2: Check if frame appears in MinIO evidence bucket (no auth)
curl http://<minio-ip>:9000/dvas-evidence/
# or
mc alias set dvas http://<minio-ip>:9000 minioadmin minioadmin
mc ls dvas/dvas-evidence/
```

**Remediation (ordered by urgency):**
1. **Immediately:** Remove `SKIP_AUTH: "true"` from ingest-api environment — enforce Keycloak JWT on every `/ingest/frame` POST.
2. **Immediately:** Revoke anonymous download policy on `dvas-evidence` bucket — `mc anonymous set none local/dvas-evidence`.
3. **Immediately:** Rotate MinIO root credentials from `minioadmin`/`minioadmin`.
4. **Short-term:** Implement cryptographic frame signing at the edge — each frame should carry an HMAC or digital signature tied to the drone's private key, verified at the ingest layer before queuing.
5. **Short-term:** Bind `minio:9000` to an internal-only address; access only via authenticated gateway or VPN interface.

---

## Supplementary Code-Level Checks

> **Analyst notes incorporated (2026-08-12 review).** Covers Group A (code-level) and Group B (new gaps) items.

---

### A1. CORS Configuration

**Files:** `DVAS_Dashboard/backend/server.py` (lines 2547–2553) and `dvas_ingest/ingest/api/main.py` (lines 220–225)

#### Commands Used
```bash
grep -n "CORSMiddleware\|allow_origins\|allow_credentials\|allow_methods\|allow_headers" \
    DVAS_Dashboard/backend/server.py
grep -n "allow_origins\|allow_credentials\|CORS" dvas_ingest/ingest/api/main.py
```

#### Findings

**Dashboard backend (`server.py`):**
```python
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Ingest API (`main.py`):**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)
```

| App | `allow_origins` | `allow_credentials` | Severity |
|-----|----------------|---------------------|----------|
| Dashboard backend | `"*"` (wildcard default) | `True` | 🔴 **Critical misconfiguration** |
| Ingest API | `["*"]` (hardcoded) | Not set (defaults `False`) | 🟠 High |

> **Critical note (Dashboard):** `allow_credentials=True` combined with `allow_origins="*"` is a **browser-rejected configuration** — modern browsers refuse credentialed CORS requests when the origin is a wildcard. However, the more serious risk is in production: if `CORS_ORIGINS` is set to a specific origin (e.g., the dashboard domain), cookies and JWTs would be sent cross-origin to that exact domain. The current fallback `"*"` with credentials is both insecure in intent and broken in practice simultaneously.  
> **Remediation:** `CORS_ORIGINS` must be a specific, explicit list of allowed origins. Wildcard with `allow_credentials=True` must never be deployed.

---

### A2. File Upload Handler Validation

**File:** `dvas_ingest/ingest/api/main.py` (lines 291–425), `dvas_ingest/ingest/api/models.py`

#### Commands Used
```bash
grep -rn -E "(MAX_FRAME_MB|ALLOWED_TYPES|content_type|filename|secure_filename|path)" \
    dvas_ingest/ingest/api/main.py dvas_ingest/ingest/api/config.py
```

#### Findings

| Check | Implemented? | Detail |
|-------|-------------|--------|
| **MIME type validation** | ✅ Yes | `content_type not in ALLOWED_TYPES` (lines 335–342) — rejects anything not `image/jpeg` or `image/png` |
| **Size limit** | ✅ Yes | `MAX_FRAME_BYTES` default 5 MB (`MAX_FRAME_MB=5`); returns HTTP 413 on oversize |
| **Filename sanitization** | ⚠️ N/A | Frame bytes are stored directly in Valkey/Redis stream — the filename is never written to disk. No path traversal surface exists in the queue path. |
| **Magic-byte validation** | ❌ Missing | MIME check is based on the `Content-Type` header sent by the client — **not on inspecting the actual file magic bytes**. A client can set `Content-Type: image/jpeg` while uploading any binary payload (e.g., a polyglot or compressed exploit). Downstream GPU consumer receives unchecked bytes. |
| **Nginx size limit** | ✅ Yes | `client_max_body_size 10M` in nginx.conf — provides a second layer of size enforcement |

> **Risk:** The MIME type check is client-controlled. Add magic-byte validation at the ingest boundary (`imghdr` / `filetype` library). The GPU consumer should treat all input as untrusted regardless.

---

### A3. Pydantic GPS / Telemetry Field Constraints

**File:** `dvas_ingest/ingest/api/models.py` (lines 17–58)

#### Commands Used
```bash
cat dvas_ingest/ingest/api/models.py
```

#### Findings

```python
class FrameMetadata(BaseModel):
    frame_sequence: int   = Field(..., ge=0)
    change_score:   float = Field(..., ge=0.0, le=1.0)
    latitude:    Optional[float] = Field(None, ge=-90.0,  le=90.0)
    longitude:   Optional[float] = Field(None, ge=-180.0, le=180.0)
    altitude_m:  Optional[float] = Field(None, ge=0.0,    le=10000.0)
    heading_deg: Optional[float] = Field(None, ge=0.0,    le=360.0)
    speed_mps:   Optional[float] = Field(None, ge=0.0)
    battery_pct: Optional[float] = Field(None, ge=0.0,    le=100.0)

    @field_validator("drone_id")
    def validate_drone_id(cls, v):
        if not re.match(r"^[A-Z0-9_\-]{3,30}$", v): raise ValueError(...)

    @field_validator("timestamp")
    def validate_timestamp(cls, v):
        if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", v): raise ValueError(...)
```

| Field | Bounded? | Range |
|-------|---------|-------|
| `latitude` | ✅ | `-90.0` to `90.0` |
| `longitude` | ✅ | `-180.0` to `180.0` |
| `altitude_m` | ✅ | `0.0` to `10000.0` |
| `heading_deg` | ✅ | `0.0` to `360.0` |
| `battery_pct` | ✅ | `0.0` to `100.0` |
| `change_score` | ✅ | `0.0` to `1.0` |
| `speed_mps` / `speed_kmh` | ⚠️ | Only `ge=0.0` — no upper bound (could submit `speed_mps=99999`) |
| `drone_id` | ✅ | Regex `[A-Z0-9_-]{3,30}` |
| `mission_id` | ❌ | Plain `str` — no length or pattern constraint |
| `timestamp` | ⚠️ | Regex checks format only — does not reject future or past-epoch timestamps |

> **Observation:** GPS bounds are well-implemented. Minor gaps: `speed_mps/speed_kmh` has no upper bound; `mission_id` is unconstrained string allowing injection attempts; timestamp does not reject far-future values (could be used for timeline poisoning).

---

### A4. CI/CD Pipeline Files

#### Command Used
```bash
find . -path "./.github" -o -path "./.gitlab-ci.yml" 2>/dev/null
ls -la .github 2>/dev/null
ls -la .gitlab-ci.yml 2>/dev/null
```

#### Finding
**No CI/CD pipeline files found.** Neither `.github/workflows/` nor `.gitlab-ci.yml` exists in the repository.

| Item | Present? |
|------|---------|
| `.github/workflows/` | ❌ Absent |
| `.gitlab-ci.yml` | ❌ Absent |
| `Jenkinsfile` | ❌ Absent |

> **Implication:** No automated secret scanning (e.g., `truffleHog`, `detect-secrets`, `gitleaks`) is configured. All 15 hardcoded credential findings survived to the codebase with no automated gate. Recommending: add a pre-commit hook and CI secret scanner before any repository is connected to a CI/CD pipeline.

---

### A5. Git Commit History — Divergent Secret Files

#### Commands Used
```bash
# Check for .git directory at repo root and in each subproject
ls -la .git 2>/dev/null           # → NO_GIT_DIR
ls -la DVAS_Dashboard/.git        # → NO_GIT_DIR
ls -la dvas_ingest/.git           # → NO_GIT_DIR
ls -la dvas_edge/.git             # → NO_GIT_DIR
ls -la dvas_analytics/.git        # → NO_GIT_DIR

git log --oneline --all -- dvas_edge/ingest/edge_sender.py DVAS_Dashboard/docker-compose.yml
```

#### Finding
**No `.git` directory exists anywhere in the repository tree** — not at the root, and not within any subproject (`DVAS_Dashboard/`, `dvas_ingest/`, `dvas_edge/`, `dvas_analytics/`). This is a **clean history strip**, not a shallow clone or wrong working directory issue. The codebase was provided as a plain directory copy with version control metadata removed.

| Location checked | `.git` present? |
|-----------------|----------------|
| `/DVAS_KP-testing/` (root) | ❌ No |
| `DVAS_Dashboard/` | ❌ No |
| `dvas_ingest/` | ❌ No |
| `dvas_edge/` | ❌ No |
| `dvas_analytics/` | ❌ No |

> **Implication:** Rotation evidence for the two divergent Keycloak client secrets (`j2Icr4lPJHHlvcJBu04nIH7A74k3cl4c` in `edge_sender.py` vs `yNa0AfA0mToUmhQHSiM2vjAyYud6NSMn` in `docker-compose.yml`) cannot be determined from this copy. Treat **both** as compromised until confirmed otherwise.
>
> **Recommended action before Phase 1:** Request the original git repository (or a `git bundle`) and run:
> ```bash
> git log --all -p -- dvas_edge/ingest/edge_sender.py | grep "CLIENT_SEC"
> git log --all -p -- DVAS_Dashboard/docker-compose.yml | grep "KC_CLIENT_SEC"
> ```
> If both secrets appear in the same commit → copy-paste error (both always compromised). If one replaced the other → partial rotation (the replaced one is the stale credential).

---

### A6. Nginx / Reverse Proxy Configuration

**Files:** `dvas_ingest/ingest/nginx/default.conf` and `DVAS_Dashboard/frontend/nginx.conf`

#### Command Used
```bash
cat dvas_ingest/ingest/nginx/default.conf
cat DVAS_Dashboard/frontend/nginx.conf
```

#### Findings

**Ingest Nginx (HTTPS server block):**

| Header / Config | Present? | Value |
|----------------|---------|-------|
| `Strict-Transport-Security` | ✅ | `max-age=31536000; includeSubDomains` |
| `X-Frame-Options` | ✅ | `DENY` |
| `X-Content-Type-Options` | ✅ | `nosniff` |
| `Content-Security-Policy` | ❌ **Missing** | Not set |
| `Referrer-Policy` | ❌ **Missing** | Not set |
| `Permissions-Policy` | ❌ **Missing** | Not set |
| TLS protocols | ✅ | `TLSv1.2 TLSv1.3` — RC4/NULL excluded |
| Rate limiting | ✅ | `limit_req zone=drone_ingest_limit burst=60 nodelay` on `/ingest/` |
| Authorization header pre-check | ✅ | Nginx rejects missing `Authorization` header before hitting FastAPI on `/ingest/` |
| `/metrics` restricted | ✅ | Allow list: Docker bridge + WireGuard VPN + localhost only |

**Notable issues:**
- **HTTP server block exposes Swagger UI and Prometheus metrics without auth** — `location ~ ^/(docs|redoc|openapi\.json)$` and `location /metrics` are both proxied over plain HTTP with no auth guard.
- **HTTP server does not redirect to HTTPS** — it only partially proxies; a `return 301 https://$host$request_uri` block is absent from the HTTP server block.

**Dashboard Nginx:**

| Header / Config | Present? | Value |
|----------------|---------|-------|
| Any security headers | ❌ **None** | Dashboard nginx has zero security headers |
| HTTPS | ❌ | Serves only on port 80 — no TLS at the frontend layer |
| CSP | ❌ | Not set |

> **Summary:** Ingest Nginx has a reasonable security posture on the HTTPS block but is missing CSP, lacks HTTP→HTTPS redirect, and leaks Swagger/metrics over plaintext. Dashboard Nginx has no security headers at all — serving a React SPA with law enforcement data over HTTP with no `X-Frame-Options`, no CSP, and no HSTS.

---

### A7. Inter-Service Authentication — Analytics Gateway ↔ Analyzers

**Files:** `dvas_analytics/shared/config.py` and analytics service code

#### Command Used
```bash
grep -rn -E "(Authorization|api_key|X-API-Key|Bearer|token|auth)" dvas_analytics/ | \
    grep -v "node_modules|# " | head -20
cat dvas_analytics/shared/config.py
```

#### Finding

The `dvas_analytics/shared/config.py` defines the gateway-to-analyzer URLs:
```python
CROWD_URL:   str = os.getenv("CROWD_URL",   "http://crowd-analyzer:8101")
PERSON_URL:  str = os.getenv("PERSON_URL",  "http://person-analyzer:8102")
VEHICLE_URL: str = os.getenv("VEHICLE_URL", "http://vehicle-analyzer:8103")
ANPR_URL:    str = os.getenv("ANPR_URL",    "http://anpr-analyzer:8104")
```

**No inter-service authentication was found.** The analytics gateway communicates with the four analyzer microservices over plain HTTP on Docker's internal network using service names — with no API key, bearer token, mutual TLS, or any other authentication mechanism between services.

| Service Pair | Auth? | Protocol |
|-------------|-------|---------|
| gateway → crowd-analyzer | ❌ None | HTTP |
| gateway → person-analyzer | ❌ None | HTTP |
| gateway → vehicle-analyzer | ❌ None | HTTP |
| gateway → anpr-analyzer | ❌ None | HTTP |

> Since all four analyzer ports (8101–8104) are bound to `0.0.0.0`, any LAN-adjacent attacker can query analyzer APIs directly — bypassing the gateway entirely. The lack of inter-service auth compounds the public port exposure finding.

---

### A8. YOLO / ML Model Path Configuration

#### Command Used
```bash
grep -rn -E "(yolo|YOLO|weights|\.pt|model_path|MODEL_PATH)" dvas_analytics/ | head -15
```

#### Finding

| Model | Default Path | Configurable? |
|-------|-------------|--------------|
| ANPR vehicle detector | `/analytics/anpr/models/yolov8m.pt` | ✅ `VEHICLE_MODEL` env var |
| ANPR licence plate detector | `/analytics/anpr/models/license_plate_detector.pt` | ✅ `LP_MODEL` env var |
| Crowd counting model | `/analytics/crowd/models/crowd_counting.pth` | ✅ `MODEL_PATH` env var |

All model paths are **runtime-configurable via environment variables** — not hardcoded at build time. However:
- The `.pt` (PyTorch YOLO) and `.pth` weight files are referenced at fixed Docker-internal paths. If these weight files are distributed via public DockerHub images or unauthenticated storage, they may be tampered with (model poisoning).
- There is no checksum or signature verification on loaded model files in any of the analyzer startup code.

---

### A9. IaC Files (Terraform / Ansible / Pulumi)

#### Command Used
```bash
find . -name "*.tf" -o -name "*.tfvars" -o -name "Pulumi.yaml" -o -name "*.playbook.yml"
```

#### Finding
**No IaC files found.** The deployment relies entirely on Docker Compose files and shell scripts (`setup_ubuntu.sh`, `setup_keycloak.py`). No Terraform, Ansible, or Pulumi configuration exists.

---

### B1. Environment File Inventory

#### Commands Used
```bash
# Check all four codebases explicitly
find DVAS_Dashboard dvas_ingest dvas_edge dvas_analytics \
    -name ".env" -o -name ".env.example" -o -name ".env.production" \
    -o -name ".env.local" -o -name ".env.sample" -o -name ".env.test" 2>/dev/null | sort

# Separately confirm what dvas_edge uses for config
find dvas_edge -name "*.yaml" -o -name "*.yml" -o -name "*.env*" 2>/dev/null | sort
```

#### Finding
**No `.env` files found in any of the four codebases.** All four subprojects were checked individually:

| Codebase | `.env` / `.env.example` present? | Config mechanism instead |
|----------|----------------------------------|-------------------------|
| `DVAS_Dashboard/` | ❌ No | Hardcoded env vars in `docker-compose.yml` |
| `dvas_ingest/` | ❌ No | Hardcoded env vars in `docker-compose.yml` (referenced `.env.example` does not exist) |
| `dvas_edge/` | ❌ No | `dvas_edge/config/settings.yaml` + hardcoded defaults in `config.py` |
| `dvas_analytics/` | ❌ No | Hardcoded defaults in `dvas_analytics/shared/config.py` |

**`dvas_edge` specific note:** `dvas_edge` is the only subproject that uses a structured config file (`settings.yaml`) as an alternative to a `.env` file. This file contains the `Operator@001` password (line 89) in plaintext — meaning it functions as an env file substitute but with the same hardcoding problem and no `.gitignore` protection.

The `setup_ubuntu.sh` post-install step (line ~24) explicitly instructs:
```bash
# Step 3: Copy .env: cp ingest/.env.example .env && nano .env
```
This references a file that **does not exist** — operators following the setup guide encounter a missing template at step 3 and have no documented path to populate secrets securely.

> **Root cause:** The codebase has no secret-externalisation infrastructure at all. There is no `.env.example`, no `secrets.yaml.example`, and no documented variable list beyond what can be reverse-engineered from the `docker-compose.yml` files. This is a **process-maturity finding** — the secure-deployment path is broken before an operator even starts.

---

### B2. Logging / Error-Handling — Debug Mode & Stack Trace Disclosure

#### Commands Used
```bash
grep -n "FastAPI(" DVAS_Dashboard/backend/server.py
grep -n "FastAPI(" dvas_ingest/ingest/api/main.py
grep -rn -E "(debug=True|DEBUG|detail=str\(exc\)|traceback|exc_info)" \
    DVAS_Dashboard/backend/server.py | head -20
```

#### Findings

| App | `debug=True`? | Stack traces in responses? |
|-----|-------------|--------------------------|
| Dashboard backend | ❌ `app = FastAPI(title="DVAS API")` — no `debug` param | ⚠️ Partial — see below |
| Ingest API | ❌ `app = FastAPI(...)` — no `debug` param | ❌ No |

**Partial disclosure in dashboard backend (`server.py` line 329):**
```python
except Exception as exc:
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Bad metadata: {exc}")
```
The `str(exc)` is surfaced directly in the `detail` field of the HTTP 422 response. Depending on the exception type, this may leak internal class names, file paths, or stack fragments to the caller.

Similar pattern found in analytics proxy routes (`server.py` lines ~1330–1360) where upstream errors from the analyzer microservices are forwarded as-is in the response body.

> **Risk:** Low individually, but worth noting as a systematic pattern to audit. FastAPI's default exception handler for unhandled exceptions does return 500 with no body — but any caught exception where `detail=str(exc)` is used becomes a potential info-disclosure point.

---

### B3. Dependency Supply-Chain / Typosquatting Sanity Check

#### Commands Used
```bash
cat DVAS_Dashboard/backend/requirements.txt
# Manually reviewed all 33 backend and 74 frontend packages against PyPI / npm known-good names
```

#### Finding
All 33 backend (`requirements.txt`) and 74 frontend (`package.json`) package names were reviewed against known-good package identifiers. **No typosquatted or suspicious packages were identified.**

| Package | Verdict |
|---------|---------|
| `shellingham>=1.3.0` | ✅ Legitimate (`typer` dependency, pinned explicitly in a comment) |
| `jq>=1.6.0` | ✅ Legitimate Python `jq` binding |
| `fpdf2==2.8.7` | ✅ Legitimate (note: `fpdf` without `2` is the older unmaintained package — this is the correct maintained fork) |
| `@phosphor-icons/react` | ✅ Legitimate icon library |
| All `@radix-ui/react-*` | ✅ All match official Radix UI registry |

> No supply-chain anomalies detected. Recommend adding `pip-audit` or `socket.dev` to the CI/CD pipeline when one is established.

---

### B4. Backup / Dump Files in Repository

#### Command Used
```bash
find . -name "*.sql" -o -name "*.dump" -o -name "*.bak" -o -name "*backup*" \
       -o -name "*.sqlite" -o -name "*.db" | grep -v node_modules | grep -v ".git"
```

#### Finding

```
./dvas_analytics/db/002_analytics_extras.sql
./dvas_analytics/db/003_minio_reid.sql
./dvas_analytics/db/001_dvas_schema.sql
./dvas_analytics/db/migrations/002_analytics_extras.sql
./dvas_analytics/db/migrations/001_dvas_schema.sql
./dvas_analytics/db/migrations/003_minio_reid.sql
```

All found `.sql` files are **schema migrations** — DDL only (`CREATE TABLE`, `CREATE INDEX`, `CREATE EXTENSION`). They contain **no real data**, PII, or INSERT statements with sensitive content beyond generic drone registry seeds (`DRONE_KL_001` etc.) and a commented-out watchlist example.

One notable entry in `002_analytics_extras.sql` (line 39):
```sql
-- INSERT INTO plate_watchlist (plate_text, reason, case_ref) VALUES
```
This is **commented out** — no real plate data was committed.

> **Result:** No accidental database dumps or backup files with real data found. ✅

---

### B5. Test / Mock Data Review — PII Leakage from Pilot Data

#### Commands Used
```bash
find . -path "*/tests/*" | xargs grep -l -E "(operator_001|8\.5241|76\.9366|Thiruvananthapuram)" 2>/dev/null
cat DVAS_Dashboard/backend/tests/backend_test.py
```

#### Findings

`DVAS_Dashboard/backend/tests/backend_test.py` uses these credentials:
```python
API = "http://localhost:8001/api"
PWD = "dvas1234"
USERS = {
    "operator": ("operator@dvas.kp", "drone_operator"),
    "control":  ("control@dvas.kp",  "control_room_officer"),
    ...
    "admin":    ("admin@dvas.kp",    "system_administrator"),
}
```

**All test credentials match the seeded demo accounts** — there is no indication of real pilot data. GPS coordinates (`8.5241`, `76.9366`) appearing in tests are Thiruvananthapuram city centre — generic public coordinates, not traceable to an actual mission.

The `backend_test.py` reference to `"jurisdiction": "Thiruvananthapuram"` reflects seed data, not operational records.

> **Result:** No real PII or pilot mission data detected in test files. ✅ Tests appear to be written against synthetic seed data.

---

### B6. README / Setup Docs — Undocumented Bypass Flags

#### Commands Used
```bash
find . -name "README*" | grep -v node_modules
grep -rn -E "(dev.mode|--dev|SKIP_AUTH|DEBUG|bypass|seed|--reset|insecure)" \
    dvas_ingest/ingest/scripts/setup_ubuntu.sh
```

#### Findings

Five READMEs found across submodules. Key findings from review:
- `dvas_ingest/README.md` — standard deployment guide; references `SKIP_AUTH` explicitly as a **LAN demo convenience flag**.
- `dvas_edge/README.md` — references the `--no-wait` flag for `setup_keycloak.py` (skips Keycloak health wait — not a security bypass).
- `dvas_edge/ingest/README.md` — documents `TLS_VERIFY=false` as an explicit "dev only" option.

The `SKIP_AUTH` flag is **documented in the README**, not hidden. This confirms it is an intentional developer convenience mode, not an undiscovered backdoor — but it was never removed before the codebase reached the state being assessed. The READMEs do not document any additional undiscovered bypass flags.

---

### B7. Third-Party Integration Credentials

#### Commands Used
```bash
grep -rn -E "(boto3|s3_client|AWS_ACCESS|AWS_SECRET|S3_BUCKET)" . | grep -v minio | head -20
grep -rn -E "(sendgrid|twilio|firebase|smtp|MAPS_KEY|TILE_KEY)" . | grep -v node_modules | head -10
cat dvas_analytics/shared/config.py  # Checked MINIO_ENDPOINT vs S3 endpoint_url
```

#### Findings

**boto3 / AWS S3:**
`boto3>=1.34.129` is in `requirements.txt` but **no AWS S3 usage was found** in any Python file. The object storage layer exclusively uses MinIO via its S3-compatible API with `endpoint_url` set to the local MinIO instance. boto3 appears to be a transitive dependency or future-use import.

**Map tile provider (Leaflet):**
Leaflet is used in the frontend but no tile API key was found. The map tile source was not configured in any frontend config file — likely using the default OSM tiles (free, no key required) or left unconfigured.

**SMS / Email / Notification services:**
No SendGrid, Twilio, SMTP, Firebase, or equivalent service credentials were found anywhere in the codebase.

**MinIO SECURE flag:**
```python
MINIO_SECURE: bool = os.getenv("MINIO_SECURE", "false").lower() == "true"
```
MinIO TLS is disabled by default (`MINIO_SECURE=false`). All analytics services communicate with MinIO over plaintext HTTP — evidence images and face crops are transmitted unencrypted within the Docker network.

| Integration | Credentials Found? | Notes |
|------------|------------------|-------|
| AWS S3 | ❌ No | boto3 present but no AWS endpoint/key configured |
| MinIO | ✅ (default `minioadmin`) | Local only; TLS disabled by default |
| Leaflet / Map tiles | ❌ No key | Likely free OSM tiles |
| Email / SMS | ❌ None | No notification service configured |
