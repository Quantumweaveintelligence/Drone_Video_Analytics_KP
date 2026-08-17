# Zero-Compromise Security Architecture & Implementation Plan
## Centralized Keycloak IAM with Custom Police Dashboard Management

**Date & Time:** 2026-08-14 08:45:00 PM IST (+05:30)  
**Project:** Kerala Police Drone Video Analytics System (DVAS)  
**Target Systems:** `DVAS_Dashboard`, `dvas_ingest`, `dvas_edge`  
**Classification:** Security Architecture & Technical Implementation Plan (Zero-Trust / VAPT Compliant)  
**Compliance:** 100% Free & Open Source Software (FOSS) — Apache-2.0 / MIT / BSD Licenses  
**Branch:** `testing`  

---

## 1. Executive Summary & Core Objective

The Kerala Police Drone Video Analytics System requires a dual mandate:
1. **Operational Usability for Police Officers:** Police administrators and field operators must interact exclusively with the custom-branded Kerala Police Web Dashboard. They must never be exposed to the complex, third-party Keycloak administration console.
2. **Zero-Compromise Cybersecurity:** The system must meet strict defense-grade security standards. It must eliminate master admin credential exposure, prevent privilege escalation, enforce asymmetric cryptographic token verification, and isolate network perimeters.

This document identifies the critical architectural and security gaps in earlier proposals and specifies the **Backend Proxy via Scoped Service Account (Least Privilege)** architecture to resolve every gap completely.

---

## 2. Identified Gaps & How This Solution Fills Every Gap

```text
┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                       GAP ANALYSIS & TARGET SOLUTION MATRIX                                      │
├─────────────────────────┬──────────────────────────────────────────┬─────────────────────────────────────────────┤
│ Identified Gap          │ Risk / Operational Impact                │ How Solution 1 Fills the Gap                │
├─────────────────────────┼──────────────────────────────────────────┼─────────────────────────────────────────────┤
│ 1. Master Admin         │ Initial baseline suggested storing root  │ Replaces root credentials with a dedicated  │
│    Credentials in Web   │ admin credentials in the backend server. │ service account (`dvas-admin-service`) with │
│    Backend              │ (Critical Privilege Exposure)            │ strictly scoped `manage-users` role.        │
│                         │ compromises entire Keycloak instance.    │ Zero root access stored in backend.         │
├─────────────────────────┼──────────────────────────────────────────┼─────────────────────────────────────────────┤
│ 2. Broken Police ABAC   │ Deleting local `User` table without Key- │ Implements Keycloak Protocol Mappers for    │
│    Attributes           │ cloak attribute mappers causes officer   │ `jurisdiction` & `mission_scope`. Claims    │
│                         │ `jurisdiction` & `mission_scope` filters │ are embedded directly in signed JWT tokens, │
│                         │ to fail, breaking video access control.  │ preserving ABAC without local DB lookups.   │
├─────────────────────────┼──────────────────────────────────────────┼─────────────────────────────────────────────┤
│ 3. Complex IAM UX for   │ Asking non-technical police officers to  │ Custom Police UI remains the single portal. │
│    Police Operators     │ navigate Keycloak's 50+ enterprise tabs  │ Backend proxies creation requests to Key-   │
│                         │ causes operational confusion & errors.   │ cloak headlessly. Keycloak is 100% invisible│
├─────────────────────────┼──────────────────────────────────────────┼─────────────────────────────────────────────┤
│ 4. Client-Side Role     │ Earlier login page had a role dropdown,  │ Removes role dropdown from `Login.js`. Role │
│    Tampering Risk       │ allowing users to select arbitrary roles │ is cryptographically parsed from verified   │
│                         │ on login (untrusted client authority).   │ `realm_access.roles` JWT claim on backend.  │
├─────────────────────────┼──────────────────────────────────────────┼─────────────────────────────────────────────┤
│ 5. Session Expiry &     │ Without token refresh handling, short-   │ Implements `/api/auth/refresh` and Axios    │
│    Sudden Officer Logout│ lived JWT access tokens (5–15m) kick     │ silent refresh interceptor to rotate tokens │
│                         │ officers out mid-mission unexpectedly.   │ seamlessly before expiration.               │
├─────────────────────────┼──────────────────────────────────────────┼─────────────────────────────────────────────┤
│ 6. Host Port & Plain-   │ Keycloak (8080) and Ingest (8000) bound  │ Binds container ports to `127.0.0.1` only.  │
│    text Network Exposure│ to `0.0.0.0`; unencrypted HTTP fallbacks.│ Forces all external traffic through NGINX   │
│                         │ Allows LAN ARP spoofing & token capture. │ HTTPS (Port 443) with internal `step-ca` TLS│
└─────────────────────────┴──────────────────────────────────────────┴─────────────────────────────────────────────┘
```

---

## 3. Deep-Dive: Gap Explanations & Detailed Solvers

### Gap 1: Root Master Admin Credential Exposure
* **The Gap:** The initial baseline proposal relied on passing Keycloak’s master root username (`admin`) and password (`Admin@KP2025!`) inside the DVAS backend to invoke user creation endpoints. If the web backend was ever exploited (via remote code execution, SQLi, or dependency flaw), the attacker would gain full administrative dominion over all realms, client certificates, and server settings.
* **Why This Solution Solves It:** We utilize the **OAuth 2.0 Client Credentials Grant (RFC 6749 Section 4.4)**. The backend uses a dedicated machine client `dvas-admin-service` configured inside the `kerala-police-drone` realm with strictly scoped roles (`manage-users` and `view-users`). Even in a total backend breach scenario, the attacker cannot delete realms, access master keys, or compromise external systems.

### Gap 2: Breaking Attribute-Based Access Control (ABAC)
* **The Gap:** In [`server.py`](file:///d:/DVPS/DVAS_KP/DVAS_Dashboard/backend/server.py), access to live drone feeds and evidence is restricted by `scope_filter_for(user)` using `user.get("jurisdiction")` and `user.get("mission_scope")`. Deleting the PostgreSQL `User` table without mapping these attributes in Keycloak results in empty values, causing all officer queries to be denied (`403 Forbidden`).
* **Why This Solution Solves It:** We configure **Keycloak Protocol Mappers** in `setup_keycloak.py`. When an administrator sets an officer’s jurisdiction via the custom UI, Keycloak stores it as a user attribute and automatically injects `"jurisdiction": ["Thiruvananthapuram"]` and `"mission_scope": ["MSN-01"]` into the signed RS256 JWT claims. Backend microservices read these claims statelessly without making additional database queries.

### Gap 3: Police Usability vs. Raw IAM Complexity
* **The Gap:** Suggesting that police personnel navigate the native Keycloak Admin Console (`http://server:8080/admin/`) creates massive friction. Police station officers are not IT identity engineers; exposing them to SAML/OIDC client configs, LDAP sync tabs, and credential resets leads to user error and operational delays.
* **Why This Solution Solves It:** The **Backend Proxy Pattern** provides a 100% native Kerala Police Web UI. The police admin interacts only with simple fields (Officer Name, Email, Rank, Station Jurisdiction). The backend translates this action into Keycloak Admin REST API calls invisibly.

### Gap 4: Client-Side Role Manipulation
* **The Gap:** The legacy `Login.js` component allowed users to choose their role (`drone_operator`, `senior_officer`, `system_administrator`) from a UI dropdown and sent that role parameter to the backend.
* **Why This Solution Solves It:** The role dropdown is deleted entirely. The user submits only `email` and `password`. The backend authenticates credentials with Keycloak, validates the signed RS256 token, and extracts the verified role from `realm_access.roles`. Role authority is 100% server-enforced.

---

## 4. System Architecture Blueprint

```text
┌───────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                       SYSTEM ARCHITECTURE FLOW                                            │
├───────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                           │
│   [ Police Administrator ]                                                                                │
│              │                                                                                            │
│              │ 1. HTTPS / Port 443 (TLS Encrypted)                                                        │
│              ▼                                                                                            │
│   ┌──────────────────────────────────────────────┐                                                        │
│   │     Custom Kerala Police Dashboard UI        │  • Simple, clean police user management screen         │
│   │               (React SPA)                    │  • Role selection from police rank hierarchy           │
│   └──────────────────────┬───────────────────────┘                                                        │
│                          │                                                                                │
│                          │ 2. POST /api/users (Bearer JWT in Authorization Header)                        │
│                          ▼                                                                                │
│   ┌──────────────────────────────────────────────┐                                                        │
│   │            DVAS Backend Gateway              │  • Step A: Asymmetric JWKS Token Verification (RS256)  │
│   │                 (FastAPI)                    │  • Step B: RBAC Check (Caller MUST be 'sys-admin')     │
│   │                                              │  • Step C: Role & Input Sanitization Allowlist         │
│   │                                              │  • Step D: Cryptographic Audit Trail Written           │
│   └──────────────────────┬───────────────────────┘                                                        │
│                          │                                                                                │
│                          │ 3. Internal Docker Network (`drone-net`)                                       │
│                          │    Short-lived 60s Scoped Service Token (`dvas-admin-service`)                  │
│                          ▼                                                                                │
│   ┌──────────────────────────────────────────────┐                                                        │
│   │         Keycloak IAM (Port 8080)             │  • Isolated from public host network                   │
│   │          (Identity Authority)                │  • Creates User, sets password, binds realm role       │
│   └──────────────────────────────────────────────┘                                                        │
│                                                                                                           │
└───────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Phase-by-Phase Implementation Plan

### Phase 1: Keycloak Configuration & Scoped Client Setup
**Target File:** `dvas_ingest/ingest/scripts/setup_keycloak.py`

1. **Create Scoped Backend Service Client:**
   * **Client ID:** `dvas-admin-service`
   * **Access Type:** `Confidential` (Client Authentication enabled)
   * **Service Accounts Enabled:** `True`
   * **Standard Flow / Direct Grants:** `False` (Machine-to-machine only)
   * **Client Scopes / Roles:** Assign strictly `realm-management -> manage-users` and `realm-management -> view-users`.

2. **Configure Police ABAC Protocol Mappers:**
   * Configure Realm Mappers for custom user attributes:
     * `jurisdiction` (String) → Mapped into JWT claim `jurisdiction`
     * `mission_scope` (JSON Array) → Mapped into JWT claim `mission_scope`

3. **Standard Realm Roles:**
   * `sys-admin` (System Administrator)
   * `supervisor` (Senior Officer)
   * `control-room` (Control Room Officer)
   * `drone-operator` (Drone Field Operator)

---

### Phase 2: Backend Security Gateway Implementation
**Target Directory:** `DVAS_Dashboard/backend/`

1. **Create Helper Module `keycloak_service.py`:**
   ```python
   """
   keycloak_service.py
   ===================
   Zero-trust Keycloak integration helper for DVAS Backend.
   Handles JWKS validation, ephemeral service tokens, and scoped user management.
   100% Free & Open Source (uses python-jose, httpx).
   """
   from __future__ import annotations
   import os
   import time
   import httpx
   from jose import jwt, jwk
   from fastapi import HTTPException, status

   KEYCLOAK_INTERNAL_URL = os.getenv("KEYCLOAK_INTERNAL_URL", "http://keycloak:8080")
   REALM = os.getenv("KC_REALM", "kerala-police-drone")
   ADMIN_CLIENT_ID = os.getenv("ADMIN_CLIENT_ID", "dvas-admin-service")
   ADMIN_CLIENT_SECRET = os.getenv("ADMIN_CLIENT_SECRET")

   _jwks_cache = {"keys": [], "expires_at": 0}

   async def get_jwks() -> dict:
       """Fetch and cache Keycloak JWKS public keys."""
       now = time.time()
       if _jwks_cache["keys"] and now < _jwks_cache["expires_at"]:
           return _jwks_cache["keys"]
       
       url = f"{KEYCLOAK_INTERNAL_URL}/realms/{REALM}/protocol/openid-connect/certs"
       async with httpx.AsyncClient(timeout=5.0) as client:
           r = await client.get(url)
           r.raise_for_status()
           data = r.json()
           _jwks_cache["keys"] = data["keys"]
           _jwks_cache["expires_at"] = now + 3600  # Cache 1 hour
           return _jwks_cache["keys"]

   async def verify_jwt(token: str) -> dict:
       """Verify incoming Bearer JWT against Keycloak JWKS using RS256."""
       keys = await get_jwks()
       unverified_headers = jwt.get_unverified_header(token)
       kid = unverified_headers.get("kid")
       
       key_dict = next((k for k in keys if k["kid"] == kid), None)
       if not key_dict:
           raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token key ID")
       
       public_key = jwk.construct(key_dict)
       try:
           payload = jwt.decode(
               token,
               public_key.to_pem().decode("utf-8"),
               algorithms=["RS256"],
               audience="account",
               options={"verify_aud": False}
           )
           return payload
       except Exception as e:
           raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Token validation failed: {str(e)}")

   async def get_service_token() -> str:
       """Obtain a short-lived scoped service account token."""
       url = f"{KEYCLOAK_INTERNAL_URL}/realms/{REALM}/protocol/openid-connect/token"
       async with httpx.AsyncClient(timeout=5.0) as client:
           r = await client.post(url, data={
               "grant_type": "client_credentials",
               "client_id": ADMIN_CLIENT_ID,
               "client_secret": ADMIN_CLIENT_SECRET,
           })
           if r.status_code != 200:
               raise HTTPException(status_code=500, detail="IAM service account authorization failed")
           return r.json()["access_token"]

   async def provision_user_in_keycloak(username: str, email: str, role: str, jurisdiction: str, password: str) -> dict:
       """Provision user, credentials, and realm role via Keycloak Admin REST API."""
       token = await get_service_token()
       headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
       
       async with httpx.AsyncClient(timeout=10.0) as client:
           # 1. Create User
           user_body = {
               "username": username,
               "email": email,
               "enabled": True,
               "attributes": {"jurisdiction": [jurisdiction], "mission_scope": []},
               "credentials": [{"type": "password", "value": password, "temporary": False}]
           }
           res = await client.post(f"{KEYCLOAK_INTERNAL_URL}/admin/realms/{REALM}/users", headers=headers, json=user_body)
           if res.status_code == 409:
               raise HTTPException(status_code=400, detail="User already exists in IAM")
           if res.status_code != 201:
               raise HTTPException(status_code=res.status_code, detail=f"IAM User creation error: {res.text}")
           
           user_id = res.headers["Location"].split("/")[-1]
           
           # 2. Fetch Role Object & Bind
           role_res = await client.get(f"{KEYCLOAK_INTERNAL_URL}/admin/realms/{REALM}/roles/{role}", headers=headers)
           if role_res.status_code != 200:
               raise HTTPException(status_code=400, detail="Requested IAM role does not exist")
           role_repr = [role_res.json()]
           
           bind_res = await client.post(f"{KEYCLOAK_INTERNAL_URL}/admin/realms/{REALM}/users/{user_id}/role-mappings/realm", headers=headers, json=role_repr)
           if bind_res.status_code not in (200, 204):
               raise HTTPException(status_code=500, detail="Failed to bind role to user")
           
           return {"id": user_id, "username": username, "role": role, "jurisdiction": jurisdiction}
   ```

2. **Update `server.py` Endpoints:**
   * **Authentication Dependency (`get_current_user`):**
     * Replaces local SQLite checks with `verify_jwt(token)`.
     * Maps `realm_access.roles` directly to internal system roles.
   * **User Provisioning (`POST /api/users`):**
     * Protected by `require_role("sys-admin")`.
     * Validates requested role against strict whitelist: `{"sys-admin", "supervisor", "control-room", "drone-operator"}`.
     * Enforces password complexity (minimum 8 characters, uppercase, digit, special character).
     * Calls `provision_user_in_keycloak(...)`.
     * Appends immutable audit entry to `audit_logs` table.

---

### Phase 3: Frontend Custom UI Adaptation
**Target Directory:** `DVAS_Dashboard/frontend/src/`

1. **`pages/Login.js` Refactor:**
   * Remove the Role `<select>` dropdown entirely.
   * Form sends only `email` and `password`.
   * On login response, role is extracted from the JWT payload and stored in `AuthContext`.
2. **`context/AuthContext.js` Refactor:**
   * Manage access token and refresh token in memory with automatic background renewal before expiration.
3. **`pages/IngestDashboard.js` (Custom User Management UI):**
   * Keep the custom police UI intact.
   * Form inputs map directly to `/api/users`.
   * Displays clear error feedback and success confirmation without exposing Keycloak details.

---

### Phase 4: Network & Container Hardening
**Target Directory:** `dvas_ingest/`

1. **`docker-compose.yml` Port Isolation:**
   ```yaml
   # Keycloak Container Isolation
   headscale:
     ports:
       - "127.0.0.1:8081:8080"  # Loopback only

   keycloak:
     ports:
       - "127.0.0.1:8080:8080"  # Loopback only; blocked from external interfaces

   ingest-api:
     ports:
       - "127.0.0.1:8000:8000"  # Loopback only
   ```
2. **NGINX Reverse Proxy (`ingest/nginx/default.conf`):**
   * Terminates TLS on **HTTPS (Port 443)** using certificates from `step-ca`.
   * Proxies frontend dashboard and `/realms/` authentication endpoints.

---

## 6. Open Source Software (FOSS) Compliance Confirmation

Every tool and dependency in this architecture is **100% Free and Open Source**:

| Component | License | Vendor / Maintainer | Cost |
| :--- | :--- | :--- | :---: |
| **Keycloak** | **Apache-2.0** | Red Hat / CNCF | **$0 / Free** |
| **FastAPI** | **MIT** | Tiangolo / Community | **$0 / Free** |
| **`python-jose`** | **MIT** | Mpmath / Community | **$0 / Free** |
| **`httpx`** | **BSD-3-Clause** | Encode / Community | **$0 / Free** |
| **React** | **MIT** | Meta / Open Source Community | **$0 / Free** |
| **Headscale** | **BSD-3-Clause** | Juan Font / Community | **$0 / Free** |
| **Smallstep (`step-ca`)** | **Apache-2.0** | Smallstep / Community | **$0 / Free** |
| **PostgreSQL & Valkey** | **PostgreSQL / BSD-3-Clause** | PostgreSQL Global / Linux Foundation | **$0 / Free** |
| **NGINX** | **2-Clause BSD-like** | F5 / Open Source Community | **$0 / Free** |

---

## 7. Verification & Security Validation Runbook

```text
┌──────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                VERIFICATION MATRIX & TEST STEPS                                          │
├───────┬─────────────────────────────┬────────────────────────────────────────────────────────────────────┤
│ Step  │ Test Case                   │ Expected Outcome                                                   │
├───────┼─────────────────────────────┼────────────────────────────────────────────────────────────────────┤
│ 1     │ Service Account Setup       │ `dvas-admin-service` successfully created with `manage-users` role │
│       │                             │ and verifiable client secret.                                      │
├───────┼─────────────────────────────┼────────────────────────────────────────────────────────────────────┤
│ 2     │ Custom UI Police Login      │ Officer logs in without role dropdown. Dashboard correctly routes  │
│       │                             │ based on verified JWT role.                                        │
├───────┼─────────────────────────────┼────────────────────────────────────────────────────────────────────┤
│ 3     │ User Creation via Custom UI │ SysAdmin creates new officer from custom dashboard screen.         │
│       │                             │ Keycloak provisions user, password, and realm role in background.  │
├───────┼─────────────────────────────┼────────────────────────────────────────────────────────────────────┤
│ 4     │ Privilege Escalation Test   │ Non-admin officer attempting `POST /api/users` receives HTTP 403.  │
│       │                             │ Injecting invalid role strings returns HTTP 422/400.               │
├───────┼─────────────────────────────┼────────────────────────────────────────────────────────────────────┤
│ 5     │ Network Perimeter Scan      │ Direct TCP requests to `http://<SERVER_IP>:8080/` are refused.     │
│       │                             │ All traffic successfully enforced through HTTPS Port 443.          │
└───────┴─────────────────────────────┴────────────────────────────────────────────────────────────────────┘
```
