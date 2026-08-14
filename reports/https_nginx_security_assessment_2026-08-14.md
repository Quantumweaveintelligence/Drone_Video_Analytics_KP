# DVAS Transport Security & NGINX HTTPS Architecture Assessment

**Date:** 2026-08-14  
**Scope:** Edge-to-Command / Ingest Server Communication & NGINX TLS Termination  
**Target Systems:** `dvas_edge`, `dvas_ingest`, `DVAS_Dashboard`  

---

## 1. Executive Summary & Context

Historically during initial local area network (LAN) development and prototyping, communication between the field edge nodes (`dvas_edge`) and the command/ingest server (`dvas_ingest`) operated over unencrypted **plain HTTP** (e.g. `http://...:80`, `http://...:8000`, `http://...:8080`).

To secure communication across networks and VPN boundaries, an **NGINX reverse proxy** was introduced to handle **HTTPS (port 443)** with TLS certificates managed via `step-ca`.

This document evaluates:
1. **Architectural Validity:** Whether using NGINX for HTTPS TLS termination is the correct design pattern.
2. **Current Security Posture:** What protections HTTPS via NGINX provides.
3. **Identified Security Gaps & Risks:** Critical areas where security can still be bypassed.
4. **Remediation & Hardening Plan:** Actionable steps to achieve full end-to-end security.

---

## 2. Architecture Analysis: Is NGINX HTTPS Termination Right?

**Verdict:** ✅ **YES — This is the industry-standard and recommended architecture.**

### Visual Architecture & Data Flow Line Diagrams

#### 1. BEFORE vs CURRENT Architecture

```
========================================================================================
1. BEFORE (Plain HTTP — UNSECURED)
========================================================================================

  [ Edge Laptop / Field ]                   [ Command Room Server ]
  ┌───────────────────────┐                 ┌──────────────────────────────────────────┐
  │                       │                 │                                          │
  │   edge_sender.py      │ ─── HTTP ─────► │   Ingest API (:8000)                     │
  │                       │ (Unencrypted)   │   Keycloak   (:8080)                     │
  └───────────────────────┘                 │                                          │
                                            └──────────────────────────────────────────┘
  ⚠️ DANGER: 
  - All Passwords, JWT Tokens, and Video Frames are sent in CLEARTEXT.
  - Anyone on the LAN / Wi-Fi can capture and view sensitive police footage.


========================================================================================
2. CURRENT / INTENDED (HTTPS via NGINX — SECURED TRANSPORT)
========================================================================================

  [ Edge Laptop / Field ]                   [ Command Room Server (Ubuntu) ]
  ┌───────────────────────┐                 ┌──────────────────────────────────────────┐
  │                       │                 │                                          │
  │   edge_sender.py      │                 │  ┌────────────────────────────────────┐  │
  │                       │ ─── HTTPS ────► │  │ NGINX Reverse Proxy (Port 443)    │  │
  │   (TLS_VERIFY=true)   │ (TLS 1.2/1.3    │  │ ── TLS Decryption & Rate Limiting  │  │
  └───────────────────────┘  Encrypted)     │  └──────────────────┬─────────────────┘  │
                                            │                     │                    │
                                            │        Internal     │ (Plain HTTP over   │
                                            │     Docker Network  ▼  internal bridge)  │
                                            │  ┌────────────────────────────────────┐  │
                                            │  │ Ingest API (:8000)                 │  │
                                            │  │ Keycloak   (:8080)                 │  │
                                            │  │ Valkey / SeaweedFS                 │  │
                                            │  └────────────────────────────────────┘  │
                                            └──────────────────────────────────────────┘
  ✅ SECURED:
  - Outside network traffic is 100% ENCRYPTED over HTTPS.
  - Hackers cannot read or modify packets in transit.
  - NGINX manages certificates and blocks malicious traffic early.
```

#### 2. Visual Security Breakdown & Remaining Gaps

```
========================================================================================
3. WHERE SECURITY SUCCEEDS & WHERE GAPS STILL EXIST
========================================================================================

  [ Edge Client / Attacker ]                       [ Command Server (Ubuntu) ]
  ┌────────────────────────┐                       ┌─────────────────────────────────────────┐
  │                        │                       │                                         │
  │ 1. Connects to HTTPS   │ ─── HTTPS (Port 443)► │  [ NGINX Proxy ] ✅ SECURED             │
  │                        │     (Encrypted)       │        │                                │
  │                        │                       │        ▼ (Isolated Docker Network)      │
  │                        │                       │  [ Ingest API / Keycloak ]              │
  │                        │                       │                                         │
  ├────────────────────────┤                       ├─────────────────────────────────────────┤
  │                        │                       │                                         │
  │ 2. Connects to HTTP    │ ─── HTTP (Port 80) ─► │  ⚠️ GAP 1: Port 80 allows HTTP ingest   │
  │                        │     (Unencrypted)     │     Fix: Force 301 Redirect to HTTPS    │
  │                        │                       │                                         │
  ├────────────────────────┤                       ├─────────────────────────────────────────┤
  │                        │                       │                                         │
  │ 3. Bypasses NGINX      │ ─── Direct Port 8000► │  ⚠️ GAP 2: Ports 8000/8080 bound to all │
  │    (Direct Port Hit)   │     Direct Port 8080► │     Fix: Bind to 127.0.0.1 (Localhost)  │
  │                        │                       │                                         │
  └────────────────────────┘                       └─────────────────────────────────────────┘
              │
              ▼
   ⚠️ GAP 3: If Edge script has "TLS_VERIFY=false", it accepts fake certificates.
             Fix: Set TLS_VERIFY=true and install the Step-CA root certificate.
```


### Why This Pattern is Correct:
1. **Centralized TLS & Certificate Lifecycle:** Certificates (issued by `step-ca` or a Public CA) are managed and renewed solely on NGINX rather than across multiple internal microservices (FastAPI, Keycloak, SeaweedFS, MinIO).
2. **High-Performance Ingress:** NGINX efficiently handles TLS handshakes, HTTP/2 multiplexing, connection keepalive pooling, and Gzip compression before proxying to backend workers.
3. **Layer 7 Defensive Filtering:** NGINX enforces initial rate limiting (`limit_req`), max payload constraints (`client_max_body_size 10M`), request header validation, and HTTP security headers before traffic reaches application code.
4. **Internal Network Isolation:** Backend application servers remain lightweight HTTP services communicating securely within private Docker container networks.

---

## 3. What HTTPS via NGINX Secures

When requests from the edge laptop target `https://ingest.dvas.internal/` (port 443):

| Security Domain | Plain HTTP (Before) | HTTPS via NGINX (Current) | Protection Delivered |
|---|---|---|---|
| **Data Confidentiality** | ❌ Cleartext (Visible in Wireshark) | ✅ Encrypted via TLS 1.2/1.3 (High Ciphers) | Protects drone credentials, Keycloak bearer tokens, and sensitive surveillance frames. |
| **Data Integrity** | ❌ Vulnerable to packet modification | ✅ Cryptographic Message Authentication | Prevents packet tampering and injection of fake video frames or telemetry. |
| **Brute-Force & Flooding** | ❌ Direct load on Python uvicorn | ✅ NGINX `limit_req` Zone (30r/s, burst 60) | Prevents denial-of-service from saturating backend Python workers. |
| **Clickjacking / Sniffing** | ❌ No default response headers | ✅ `X-Frame-Options: DENY`, `nosniff`, `HSTS` | Enforces browser-side transport and rendering isolation. |
| **Auth Pre-Check** | ❌ Hits FastAPI handler | ✅ NGINX early 401 on empty `Authorization` | Drops unauthenticated frame uploads at the proxy layer. |

---

## 4. Remaining Security Gaps & Vulnerabilities

Although HTTPS via NGINX is configured, the system is **not fully secure** until the following configuration gaps are resolved:

### ⚠️ Gap 1: Direct Host Port Exposure Bypasses NGINX
* **Issue:** In [`dvas_ingest/docker-compose.yml`](file:///home/govind-v-kartha/Downloads/DVAS_KP-testing/dvas_ingest/docker-compose.yml), backend containers bind their ports to `0.0.0.0` (all host interfaces):
  * `ingest-api` exposed on `0.0.0.0:8000`
  * `keycloak` exposed on `0.0.0.0:8080`
  * `minio` exposed on `0.0.0.0:9000`
  * `analytics-gateway` exposed on `0.0.0.0:8100`
  * `postgres` exposed on `0.0.0.0:5433`
* **Risk:** An attacker or misconfigured client on the local network can bypass NGINX completely by sending unencrypted HTTP requests directly to `http://<server-ip>:8000` or `http://<server-ip>:8080`.

### ⚠️ Gap 2: NGINX Port 80 Does Not Enforce HTTPS Redirection
* **Issue:** In [`dvas_ingest/ingest/nginx/default.conf`](file:///home/govind-v-kartha/Downloads/DVAS_KP-testing/dvas_ingest/ingest/nginx/default.conf#L31-L125), the `server { listen 80; }` block explicitly proxies `/ingest/`, `/realms/`, and `/ws/` over plaintext HTTP for "LAN demo" compatibility.
* **Risk:** If an edge sender script is configured with `http://` instead of `https://`, NGINX will process the sensitive authentication and video payload over unencrypted HTTP without warning or redirection.

### ⚠️ Gap 3: Edge Client Certificate Verification (`TLS_VERIFY=false`)
* **Issue:** In [`dvas_edge/ingest/edge_sender.py`](file:///home/govind-v-kartha/Downloads/DVAS_KP-testing/dvas_edge/ingest/edge_sender.py#L58), `TLS_VERIFY` defaults to `false` or is turned off when self-signed/internal CA certs are encountered.
* **Risk:** When TLS verification is disabled, Python `httpx` ignores certificate trust chains, allowing active Man-in-the-Middle (MitM) attackers on the LAN to present bogus certificates and decrypt all traffic.

### ⚠️ Gap 4: Web Dashboard Frontend Exposed Over Plain HTTP
* **Issue:** In [`DVAS_Dashboard/frontend/nginx.conf`](file:///home/govind-v-kartha/Downloads/DVAS_KP-testing/DVAS_Dashboard/frontend/nginx.conf#L1-L2), the React SPA is served over plain HTTP on port 80/3000 with no SSL certificate and no security headers (CSP, HSTS).
* **Risk:** Law enforcement operators accessing the tactical dashboard over Wi-Fi or LAN are vulnerable to session hijacking and credential snooping.

### ⚠️ Gap 5: Application-Layer Credential & Key Management
* **Issue:** Hardcoded secrets (`JWT_SECRET`, `KC_CLIENT_SEC`, default database passwords `dvas1234` / `dvas_secret`) reside in configuration files.
* **Risk:** HTTPS only protects the transit path; if token secrets or credentials are known, attackers can forge admin JWTs and query backend APIs directly.

---

## 5. Security Hardening Checklist & Action Plan

To transition from **partially secured transport** to **production-grade security**:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Hardening Roadmap                               │
├────┬────────────────────────────────────┬──────────────┬───────────────┤
│ #  │ Action Item                        │ Component    │ Priority      │
├────┼────────────────────────────────────┼──────────────┼───────────────┤
│ 1  │ Redirect HTTP (80) to HTTPS (443)  │ NGINX Ingest │ 🔴 Critical   │
│ 2  │ Close public backend container ports│ Docker Compose│ 🔴 Critical   │
│ 3  │ Enforce TLS_VERIFY & install CA    │ dvas_edge    │ 🔴 Critical   │
│ 4  │ Put Dashboard UI behind HTTPS      │ Dashboard    │ 🟠 High       │
│ 5  │ Rotate all default passwords/keys  │ System-wide  │ 🟠 High       │
└────┴────────────────────────────────────┴──────────────┴───────────────┘
```

### Detailed Configuration Recommendations

#### 1. Enforce HTTPS Redirection in `ingest/nginx/default.conf`
Replace plain HTTP proxy blocks in the `listen 80;` server block with a global redirect:
```nginx
server {
    listen      80;
    server_name ingest.dvas.internal;

    # Allow health checks for Docker container health check probes
    location = /health {
        proxy_pass http://fastapi_backend/health;
    }

    # Force all other traffic to HTTPS
    location / {
        return 301 https://$host$request_uri;
    }
}
```

#### 2. Restrict Direct Port Bindings in `docker-compose.yml`
Do not expose internal services to `0.0.0.0`. Bind strictly to localhost (`127.0.0.1`) or remove host port publishing entirely so traffic must go through NGINX:
```yaml
services:
  ingest-api:
    # Remove "8000:8000" or bind to localhost only:
    ports:
      - "127.0.0.1:8000:8000"

  keycloak:
    ports:
      - "127.0.0.1:8080:8080"

  nginx:
    ports:
      - "80:80"     # For HTTP -> HTTPS redirection
      - "443:443"   # Only publicly exposed ingress
```

#### 3. Enforce Certificate Validation on Edge Nodes
In `dvas_edge/config/settings.yaml` and `edge_sender.py`:
1. Distribute the internal root CA certificate (`step-ca` `root_ca.crt`) to edge nodes.
2. Set environment variables:
   ```bash
   export TLS_VERIFY=true
   export TLS_CA_CERT=/path/to/root_ca.crt
   ```

---

## 6. Verification Commands

Run these commands to verify the transport security state:

```bash
# 1. Verify HTTP port 80 redirects to HTTPS port 443:
curl -sI http://ingest.dvas.internal/ingest/frame
# Expected Output: HTTP/1.1 301 Moved Permanently -> Location: https://...

# 2. Verify TLS cipher and protocol version on HTTPS port 443:
openssl s_client -connect ingest.dvas.internal:443 -tls1_3

# 3. Verify that direct backend ports (8000, 8080) are blocked externally:
nc -zv <SERVER_IP> 8000
nc -zv <SERVER_IP> 8080
# Expected Output: Connection refused / timed out (accessible only via localhost or Docker network)

# 4. Verify Edge sender connects cleanly with TLS verification enabled:
python dvas_edge/ingest/edge_sender.py --frame test.jpg
```
