# Keycloak Security Engineering Advisory & Practical Integration Guide

**Date & Time:** 2026-08-14 02:00:00 PM IST (+05:30)  
**Project:** Kerala Police Drone Video Analytics System (DVAS)  
**Target Systems:** `DVAS_Dashboard`, `dvas_ingest`, `dvas_edge`  
**Classification:** Security Architecture & Engineering Advisory (Vulnerability Assessment and Penetration Testing — VAPT Perspective)  

---

## 1. Executive Summary & Context

In the DVAS ecosystem, Keycloak serves as the centralized **IAM (Identity and Access Management)** authority. It coordinates authentication, token issuance, and role enforcement across two primary operational domains:
1. **Human Operators:** Police Officers, Control Room Officers, Senior Officers, and System Administrators interacting with the Web Dashboard.
2. **Automated Machines:** Field Edge Laptops and autonomous drone video ingest scripts transmitting live surveillance feeds to the central Command Server.

This advisory provides an engineering reference for integrating Keycloak securely into the existing DVAS codebase, highlighting practical implementation steps, architectural comparisons, and domain-specific checklists.

---

## 2. Architecture Comparison: Keycloak vs. Centralized PostgreSQL

During system architecture planning, two approaches were evaluated for synchronizing credentials across field laptops and the command room:
* **Option 1 (Keycloak — Centralized IAM):** Centralized identity provider issuing cryptographically signed **JWT (JSON Web Tokens)** over secure web **APIs (Application Programming Interfaces)**.
* **Option 2 (Centralized PostgreSQL Database):** Exposing a shared database across the field **Wi-Fi / LAN (Local Area Network)** so all edge laptops connect directly via raw **SQL (Structured Query Language)** queries.

The comparison matrix below details why **Option 1 (Keycloak)** is the selected standard:

```text
┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                         KEYCLOAK (OPTION 1) vs. CENTRALIZED POSTGRESQL (OPTION 2)                                │
├───────────────────────┬──────────────────────────────────────────┬───────────────────────────────────────────────┤
│ Evaluation Area       │ Option 1: Keycloak (Selected)            │ Option 2: Centralized PostgreSQL (Rejected)   │
├───────────────────────┼──────────────────────────────────────────┼───────────────────────────────────────────────┤
│ Network Attack        │ • Low Risk.                              │ • High Risk.                                  │
│ Surface               │ • Only HTTPS (Port 443) is exposed.      │ • Exposes raw database TCP (Port 5432) over   │
│                       │ • Database remains isolated in private   │   field Wi-Fi / LAN to all connected devices. │
│                       │   container networks.                    │                                               │
├───────────────────────┼──────────────────────────────────────────┼───────────────────────────────────────────────┤
│ Compromise Impact     │ • Isolated Impact.                       │ • Catastrophic Exposure.                      │
│ (Lost/Stolen Laptop)  │ • If a field laptop is compromised, the  │ • Database connection credentials reside on   │
│                       │   admin revokes that single token/user in│   every laptop. Compromising one device grants│
│                       │   Keycloak. All other data is safe.      │   direct SQL access to all tables & logs.     │
├───────────────────────┼──────────────────────────────────────────┼───────────────────────────────────────────────┤
│ Wireless / Field      │ • Highly Resilient.                      │ • Unstable over Field Wireless.               │
│ Network Reliability   │ • Stateless JWT tokens handle momentary  │ • Database connection pools drop on Wi-Fi     │
│                       │   Wi-Fi packet drops without terminating │   blips, causing hung queries, timeouts, and  │
│                       │   active operator sessions.              │   aborted database transactions.              │
├───────────────────────┼──────────────────────────────────────────┼───────────────────────────────────────────────┤
│ System Cohesion &     │ • Single Source of Truth.                │ • Fragmented Architecture.                    │
│ Single Sign-On        │ • Unifies Drone Video Ingest and Web     │ • Creates two disconnected auth systems       │
│                       │   Dashboard under one IAM ecosystem.     │   (Keycloak for drones + Postgres for users). │
├───────────────────────┼──────────────────────────────────────────┼───────────────────────────────────────────────┤
│ Built-in Security &   │ • Enterprise-Grade Features Built-in.    │ • High Maintenance Burden.                   │
│ Compliance            │ • Brute-force account lockout, password  │ • Requires custom Python/SQL development for  │
│                       │   policies, token rotation, and audit    │   lockout logic, session tracking, password   │
│                       │   trails provided out-of-the-box.        │   hashing, and rate limiting.                 │
└───────────────────────┴──────────────────────────────────────────┴───────────────────────────────────────────────┘
```

---

## 3. Practical Code Reality Check for the DVAS Codebase

To ensure zero theoretical overhead, the table below outlines exactly how this integration maps to existing files in the repository:

```text
┌──────────────────────────────────────────────────────────────────────────────────┐
│                         PRACTICAL CODE IMPACT SUMMARY                            │
├─────────────────────┬───────────────────┬────────────────────────────────────────┤
│ Existing Component  │ Codebase Reality  │ Required Practical Change              │
├─────────────────────┼───────────────────┼────────────────────────────────────────┤
│ Keycloak Container  │ Already running in│ Just configure 4 roles in the existing │
│ (dvas_ingest)       │ Docker Compose    │ realm (already done via setup script). │
├─────────────────────┼───────────────────┼────────────────────────────────────────┤
│ React Dashboard     │ Custom Police     │ ZERO UI changes required.              │
│ Frontend            │ Login Screen      │ The existing login screen and API calls│
│                     │                   │ remain identical.                      │
├─────────────────────┼───────────────────┼────────────────────────────────────────┤
│ FastAPI Backend     │ server.py         │ Update only 2 endpoints:               │
│ (DVAS_Dashboard)    │                   │ 1. POST /api/auth/login                │
│                     │                   │ 2. POST /api/users                     │
├─────────────────────┼───────────────────┼────────────────────────────────────────┤
│ Drone Edge Laptop   │ edge_sender.py    │ Already calls Keycloak token endpoint. │
│ (dvas_edge)         │                   │ Just ensure secrets come from env vars.│
└─────────────────────┴───────────────────┴────────────────────────────────────────┘
```

---

## 4. Client Architecture & Authentication Protocols

Aligning each component with its appropriate **OIDC (OpenID Connect)** and **OAuth (Open Authorization)** flow ensures least privilege and clean separation of concerns:

### A. Web Dashboard Frontend (React Single Page Application — SPA)
* **Recommended Model:** **Public Client** utilizing the **OpenID Connect (OIDC)** **Authorization Code Flow with PKCE (Proof Key for Code Exchange)** (or direct backend authentication proxying via the existing login endpoint).
* **Guiding Principle:** A Single Page Application executes inside the end-user browser environment. It must never contain embedded **Client Secrets**.

### B. Automated Field Edge Laptops (Drone Streaming Scripts)
* **Recommended Model:** **Confidential Client** utilizing the **Client Credentials Grant** for **M2M (Machine-to-Machine)** streaming.
* **Guiding Principle:** Autonomous streaming scripts should operate under dedicated machine service identities rather than sharing personal officer credentials.

### C. Backend Ingest & API Services (FastAPI Resource Servers)
* **Recommended Model:** **Bearer-Only Resource Server**.
* **Guiding Principle:** Backend services validate the digital signature and claims of incoming **JWT (JSON Web Tokens)** before fulfilling API requests.

---

## 5. Token Verification & Cryptographic Standards

### A. Asymmetric Signing (RS256 / ES256)
* **Recommendation:** Keycloak signs all tokens using **RS256 (Rivest-Shamir-Adleman with 256-bit SHA)**.
* **Security Rationale:** With asymmetric signing, Keycloak holds the private signing key, while backend services verify tokens using public certificates. This prevents backend services from needing access to a shared symmetric secret (`HS256`).

### B. Dynamic Public Key Discovery via JWKS
* **Recommendation:** Backend services discover and cache public keys via Keycloak’s **JWKS (JSON Web Key Set)** endpoint (`/protocol/openid-connect/certs`).
* **Security Rationale:** Enables seamless cryptographic key rotation without requiring manual code changes or service restarts.

### C. Token Lifespans & Session Hygiene
* **Access Tokens:** Short lifespan of **5 to 15 minutes** to minimize exposure if a token is intercepted.
* **Refresh Tokens:** Enforce **Refresh Token Revocation** on use (Refresh Token Rotation) with an **Idle Session Timeout** (e.g., 30 minutes) to log out inactive operators automatically.

---

## 6. Role-Based Access Control (RBAC) Architecture

### A. Backend Enforcement as the Source of Truth
* **Defense in Depth Principle:** UI-level visibility controls (such as conditionally displaying navigation links or action buttons) enhance usability, but they do not substitute for backend authorization.
* **Requirement:** Every backend route must independently extract and validate user roles from the token claim structure (`realm_access.roles`).

### B. Standardized DVAS Roles
Map the four organizational roles within Keycloak:
1. `system_administrator` (System configuration, user provisioning, audit inspection)
2. `senior_officer` (Evidence approval, operational oversight, forensic reporting)
3. `control_room_officer` (Real-time alert handling, live multi-drone monitoring)
4. `drone_operator` (Field flight telemetry and video stream ingestion)

---

## 7. Custom Dashboard UI Integration with Keycloak Admin API

When administrators create or manage users through the custom DVAS Dashboard interface, the backend coordinates with Keycloak in the background:

### Implementation Architecture:
1. **Dedicated Service Account:** Create a dedicated **Confidential Client** in Keycloak for the backend with **Service Account Roles** enabled.
2. **Scoped Permissions:** Grant this service account only the specific permissions needed (such as `manage-users` under the `realm-management` scope), avoiding the use of master administrative accounts in code.
3. **Seamless User Experience:** The existing custom dashboard UI submits user creation forms to `POST /api/users`. The backend acquires a short-lived administrative token and invokes Keycloak’s **REST (Representational State Transfer)** API.

> 🔒 **Critical Secret Protection Rule:**  
> The service account introduces a new credential (`ADMIN_SERVICE_SECRET`). This secret grants direct user creation privileges and **must be protected with the exact same discipline** as all other system secrets: it must be injected exclusively via runtime environment variables or a container secrets manager, and never committed or defaulted in source code.

---

## 8. Frontend Token Storage & Transport Security

```text
┌───────────────────────────────────┬──────────────────────────────────────────────────┐
│ Storage Strategy                  │ Security & Engineering Evaluation                │
├───────────────────────────────────┼──────────────────────────────────────────────────┤
│ HttpOnly, Secure Cookies          │ • Highly Recommended for Web Browsers.           │
│                                   │ • Inaccessible to JavaScript; protects against   │
│                                   │   XSS (Cross-Site Scripting) token extraction.   │
├───────────────────────────────────┼──────────────────────────────────────────────────┤
│ In-Memory (Application State)     │ • Suitable for Single Page Applications.         │
│                                   │ • Tokens vanish on page refresh unless combined  │
│                                   │   with a silent refresh mechanism.               │
├───────────────────────────────────┼──────────────────────────────────────────────────┤
│ LocalStorage / SessionStorage     │ • Accessible to JavaScript; vulnerable if an XSS │
│                                   │   flaw is introduced via dependencies.           │
└───────────────────────────────────┴──────────────────────────────────────────────────┘
```

---

## 9. Environment Governance, Secrets & Field Deployment

### A. Runtime Environment Injection
* All sensitive configuration values—including **Client Secrets**, **Database Passwords**, and **Keycloak Hostnames**—must be injected through operating system environment variables or secure container configuration files.

### B. Transport Layer Security (TLS) Verification
* All traffic across Edge Laptops, the Web Dashboard, and Keycloak must be encrypted via **HTTPS (Hypertext Transfer Protocol Secure)**.
* Field edge laptops must have the central **CA (Certificate Authority)** certificate installed so that **TLS (Transport Layer Security)** verification remains enabled (`TLS_VERIFY=True`). Disabling certificate checks exposes field video streams to **MitM (Man-in-the-Middle)** interception.

### C. Development Flag Safeguards
* Convenience flags (such as `SKIP_AUTH` or `CORS_ORIGINS=*`) must be explicitly blocked from running when the application detects a production environment.

---

## 10. Audit Logging vs. Frame Origin Verification (Two Complementary Layers)

Surveillance integrity requires distinguishing between **identity authentication logs** and **telemetry evidence provenance**:

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                          TWO COMPLEMENTARY AUDITING LAYERS                             │
├─────────────────────────────────────────┬──────────────────────────────────────────────┤
│ 1. IAM Layer (Keycloak Event Logs)      │ 2. Pipeline Layer (DVAS Provenance Logs)     │
├─────────────────────────────────────────┼──────────────────────────────────────────────┤
│ • Tracks WHO accessed the system        │ • Tracks WHAT was streamed & from WHERE      │
│ • User log-ins, log-outs, failed auth   │ • Cryptographic frame sequence & timestamp   │
│ • Admin role assignments & user updates │ • Ingest stream validation per drone ID      │
│ • Retained for 90–180 days for IAM audit│ • Ensures chain of custody for court evidence│
└─────────────────────────────────────────┴──────────────────────────────────────────────┘
```

* **Key Difference:** Keycloak event logs record *user session activity*, but they **do not verify frame origin or prevent telemetry spoofing**. The previously recommended **application-level frame-origin verification** (matching incoming `drone_id` against the authenticated token's registered client claim) remains mandatory to prevent unauthorized frame injection into the analytics pipeline.

---

## 11. 📋 Implementation Checklists by Engineering Domain

### A. Backend Engineering Checklist (FastAPI & Ingest Services)
- [ ] **Asymmetric Verification:** Validates tokens using Keycloak's public certificates discovered via the **JWKS (JSON Web Key Set)** endpoint.
- [ ] **Enforce Token Claims:** Validates token expiration (`exp`), issuer (`iss`), and cryptographic signature on every request.
- [ ] **Route-Level Authorization:** Protects all sensitive endpoints with explicit **RBAC (Role-Based Access Control)** checks matching Keycloak roles (`system_administrator`, `senior_officer`, `control_room_officer`, `drone_operator`).
- [ ] **Scoped Admin Service Account:** Dashboard User Management calls Keycloak Admin REST API using a dedicated service account limited strictly to the `manage-users` scope.
- [ ] **Secure Error Sanitization:** Authentication failures return generic error payloads (such as `401 Unauthorized`) without exposing internal stack traces or database structures.
- [ ] **Frame Origin Cross-Validation:** Validates that incoming frame telemetry (`drone_id`) matches the authorized client identifier embedded in the token.

### B. Frontend Engineering Checklist (React Dashboard SPA)
- [ ] **No Embedded Secrets:** Confirms no **Client Secrets**, API keys, or administrative passwords are present in frontend source code or build bundles.
- [ ] **PKCE Implementation:** Web login uses the **Authorization Code Flow with Proof Key for Code Exchange (PKCE)**.
- [ ] **Safe Token Storage:** Access and refresh tokens are stored in `HttpOnly; Secure; SameSite=Lax` cookies or memory state rather than persistent browser `localStorage`.
- [ ] **Silent Token Refresh:** Implements background token refresh prior to access token expiration to maintain smooth operator sessions without disruptive logouts.
- [ ] **Sanitized Logging:** Ensures raw JWT tokens and user passwords are never output to browser developer console logs.

### C. Field Systems & Edge Checklist (Drone Laptop Streaming Scripts)
- [ ] **Machine Identity (M2M):** Streaming scripts authenticate using **Client Credentials** under a dedicated machine identity rather than sharing human passwords.
- [ ] **Runtime Secret Injection:** Machine client secrets are supplied strictly via operating system environment variables with no hardcoded fallback defaults in code.
- [ ] **TLS Certificate Verification Enabled:** Scripts run with `verify=True` referencing the trusted central Certificate Authority (`root_ca.crt`).
- [ ] **Token Refresh Ahead of Expiry:** Stream workers automatically refresh tokens 30 to 60 seconds before expiration to prevent frame drop interruptions during live missions.

### D. DevOps & Infrastructure Checklist (Keycloak & NGINX Deployment)
- [ ] **Production Mode Active:** Keycloak runs in optimized production mode (`start --optimized`) rather than development mode (`start-dev`).
- [ ] **Host Port Isolation:** Keycloak (`8080`) and Ingest API (`8000`) ports are bound strictly to localhost or private Docker networks; all external traffic routes exclusively through NGINX HTTPS (`443`).
- [ ] **Brute-Force Lockout Active:** Keycloak Brute Force Detection is enabled (locks account after 5 failed attempts with exponential backoff).
- [ ] **Strict Redirect URIs:** Client redirect settings in Keycloak use exact destination URLs with zero wildcards (`*`).
- [ ] **Audit Logging Enabled:** Both User Event Logging and Admin Event Logging are active in Keycloak with retention set for a minimum of 90 days.
- [ ] **Secret Hygiene:** Master admin passwords and database credentials are changed from repository defaults and managed via environment secrets.

> ⚠️ **VAPT Re-Verification Note on Host Port Isolation & Test Methodology:**  
> Phase 1 penetration testing specifically relied on sending test requests directly to the unauthenticated backend host port (`http://<SERVER_IP>:8000/ingest/frame` and `http://<SERVER_IP>:8080/`). Once host port isolation is applied, direct external TCP connections to ports `8000` and `8080` will be rejected by the firewall/host (`Connection Refused`).  
> * **Important for QA / Retest Teams:** When executing re-verification suites, test traffic must target the public ingress through **NGINX HTTPS (`https://ingest.dvas.internal/ingest/frame` on Port 443)**. Do not confuse a closed raw port with an application-level auth fix—both the network perimeter (Port 443 only) and application JWT validation must be verified independently.

