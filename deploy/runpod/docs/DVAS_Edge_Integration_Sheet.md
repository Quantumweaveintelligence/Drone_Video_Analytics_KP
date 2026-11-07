# DVAS cloud (RunPod) — integration sheet for the DVAS Edge application

> For: edge developer (`edge-exe-v1`) · From: RunPod/image owner · **30 Oct 2026** · Plan Phase 7
> Cloud image `20261030-d0df131` on pod `4nrza3dukc231h` (RunPod EU-NL-1, RTX PRO 6000).
> **No secrets in this sheet.** Every 🔒 value is a team password-manager entry; never put them in git, chat or
> email. Everything below was tested against the live pod on 30 Oct 2026 (17:00–17:15 IST).

---

## 1. Server

| Item | Value |
|---|---|
| **Base URL** (`BASE`) | **`https://4nrza3dukc231h-80.proxy.runpod.net`** |
| TLS | Valid public certificate (Cloudflare) → `INGEST__TLS_VERIFY=true` |
| Changes when | the pod is **re-created** (new pod ID). Stop/Start keeps it. You'll get the new URL from me; only `edge.env` changes (Start menu → *Change Server URL*) |
| Ports | 443 only (HTTPS through RunPod's proxy). No direct Keycloak `:8080`, no UDP, no RTMP into the cloud |
| Request limits | 100 s per request (Cloudflare); body ≤ 10 MB; one frame ≤ 5 MB; ingest 150 req/s per site IP (burst 300) → **HTTP 429 = back off and retry** |

## 2. `edge.env` for a RunPod laptop

```ini
DVAS_CLOUD_URL=https://4nrza3dukc231h-80.proxy.runpod.net
INGEST__URL=https://4nrza3dukc231h-80.proxy.runpod.net/ingest/frame
INGEST__MISSION_START_URL=https://4nrza3dukc231h-80.proxy.runpod.net/ingest/mission/start
INGEST__MISSION_END_URL=https://4nrza3dukc231h-80.proxy.runpod.net/ingest/mission/end
INGEST__TOKEN_URL=https://4nrza3dukc231h-80.proxy.runpod.net/realms/kerala-police-drone/protocol/openid-connect/token
INGEST__CLIENT_ID=drone-ingest-client
INGEST__CLIENT_SECRET=          # 🔒 "DVAS cloud — drone-ingest-client secret"
INGEST__USERNAME=edge_laptop_01  # one account per laptop (table 3)
INGEST__PASSWORD=               # 🔒 "DVAS cloud — edge_laptop_01"
INGEST__DRONE_ID=DRN-001
INGEST__TLS_VERIFY=true
EDGE_API_KEY=                   # your own local key, generated per build — NOT a cloud value (see 6)
REQUIRE_EDGE_AUTH=true
```

The token URL goes **through the proxy path** (no `:8080`). The token's issuer is `BASE/realms/kerala-police-drone`.

## 3. Accounts (realm `kerala-police-drone`, role `drone-operator`)

| Purpose | Username | Login e-mail (dashboard `/api/auth/login`) | Password |
|---|---|---|---|
| Frame uploader, laptop 1 → `DRN-001` | `edge_laptop_01` | `edge_laptop_01@dvas.kp` | 🔒 "DVAS cloud — edge_laptop_01" |
| Frame uploader, laptop 2 → `DRN-002` | `edge_laptop_02` | `edge_laptop_02@dvas.kp` | 🔒 "DVAS cloud — edge_laptop_02" |
| Frame uploader, laptop 3 → `DRN-003` | `edge_laptop_03` | `edge_laptop_03@dvas.kp` | 🔒 "DVAS cloud — edge_laptop_03" |
| Operator UI login | `operator_001` | `operator@dvas.kp` | 🔒 "DVAS cloud — operator_001" |
| Operator UI login | `operator_002` | `operator_002@kerala.police.in` | 🔒 "DVAS cloud — operator_002" |
| Operator UI login | `operator_003` | `operator_003@kerala.police.in` | 🔒 "DVAS cloud — operator_003" |

Uploader accounts are the installer's *Uploader username/password*. Operators log in in the edge UI with their
**e-mail**. Don't reuse the old local passwords (`Operator@001` etc.); cloud passwords are random per volume.

## 4. Token (Keycloak, through the proxy)

`POST {BASE}/realms/kerala-police-drone/protocol/openid-connect/token` (form-encoded)
- `grant_type=password`, `client_id=drone-ingest-client`, `client_secret=🔒`, `username`, `password`
- Refresh: `grant_type=refresh_token`, `client_id`, `client_secret`, `refresh_token`
- Lifetimes: access **3600 s**, refresh **86 400 s**. Refresh ~60 s before expiry; on 401, get a new token
- ✅ Tested: all three `edge_laptop_*` accounts and `operator_001`, refresh grant OK

## 5. Ingest API (`Authorization: Bearer <access_token>`)

| Route | Body | Success | Tested |
|---|---|---|---|
| `POST /ingest/mission/start` | JSON `{drone_id, mission_id, mission_name?, location?, operator?, latitude?, longitude?, notes?, geofence?}` | 201 | ✅ |
| `POST /ingest/frame` | multipart: `frame` (`image/jpeg` or `image/png`, ≤ 5 MB) + `metadata_json` (below) | **202** | ✅ 3 frames, 350–750 ms round trip from Kerala |
| `POST /ingest/mission/geofence` | JSON `{drone_id, mission_id, …}` | 200 | ✅ auth |
| `POST /ingest/mission/end` | JSON `{drone_id, mission_id, frames_sent?, notes?}` | 200 | ✅ |
| `POST /ingest/incident`, `POST /ingest/model-suggestion` | JSON | 201 | ✅ auth |

`metadata_json` (JSON string):
- **required:** `drone_id` (`^[A-Z0-9_-]{3,30}$`, e.g. `DRN-001`), `mission_id`, `timestamp` (ISO 8601 UTC),
  `frame_sequence` (int ≥ 0), `change_score` (0–1)
- **please add now:** `capture_ts` (epoch seconds, float). Your `frame_seq` is accepted too. The cloud uses
  `frame_sequence` + `capture_ts` to drop out-of-order frames (plan 9.2)
- optional: `latitude`, `longitude`, `altitude_m`, `heading_deg`, `speed_kmh`/`speed_mps`, `battery_pct`,
  `frames_total`, `camera_id`, DJI SRT fields (`abs_alt`, `gb_yaw`, `gb_pitch`, `gb_roll`, `focal_len`, `dzoom_ratio`, `gps_lock`)

Errors: 401 bad/expired token · 403 wrong role or no token · 413 too large · 415 wrong type · 422 bad metadata · 429 rate limit.
**Not public on the cloud:** `/ingest/status*`, `/ingest/streams/active` (404). Read status through the dashboard API.

## 6. Dashboard API used by the edge pages (via your launcher's `/api/*` proxy)

Log in: `POST {BASE}/api/auth/login` `{"email","password"}` → JSON with `access_token` + `Secure` HttpOnly cookies
(your launcher already strips `Secure` for `http://localhost`). **Rate limit 5 logins/min per IP.**
Then `Authorization: Bearer <access_token>` (or the cookie).

✅ Tested as `operator@dvas.kp` (role `drone_operator`): `GET /api/auth/me`, `/api/missions`, `/api/drones`,
`/api/evidence`, `/api/incidents`, `/api/analytics/suggestions`, `/api/audit-logs`, `/api/audit-logs/verify`,
`/api/edge/status`, `/api/edge/system-logs`, `/api/ingest-server/streams/active` → **200**.
Also used by the edge pages: `POST /api/missions`, `PATCH /api/missions/{id}/end`, `POST/DELETE /api/incidents*`,
`POST/DELETE /api/analytics/suggestions*`, `POST /api/auth/refresh`, `POST /api/auth/logout`.
Admin-only (403 for operators, not used by edge pages): `/api/ingest-server/status`.

**WebSocket:** `wss://{host}/api/ws?token=<access_token>` → `{"type":"welcome","role":"drone_operator"}`. Send the text
`ping` every 20 s (server answers `pong`). Tested 10+ min. It closes only when the cloud backend restarts → reconnect
with backoff.

**CORS:** `http://localhost:8765` is allowed (with credentials) on `/api/*` and `/ingest/*`; other origins are refused.

**Keys you do *not* need:**
- `/api/push/receive` + `X-DVAS-Service-Key` is backend↔backend only (old two-backend setup). In the `.exe` design
  your pages talk to the cloud backend directly, so incidents/suggestions land in the cloud DB without it.
- The cloud's `EDGE_API_KEY` isn't checked by any cloud service. Your `EDGE_API_KEY` only protects your local edge
  core (`/edge-api/*` on the laptop). Generate it per build. Also remove the hard-coded fallback
  `dvas-edge-field-secret-key` in `edge_api_server.py`.

## 7. Contract (plan 7.2) — please confirm

1. **No edge code in the cloud.** `{BASE}/edge-api/*` returns 404; the edge API exists only on the laptop.
2. **Shared frontend files.** `edge-exe-v1` and `runpod-v1` both changed `App.js`, `Login.js` and added
   `lib/deployment.js`. A trial merge conflicts in **`App.js` and `deployment.js`**. `runpod-v1`'s `deployment.js` now also
   exports your names (`DEPLOYMENT_TARGET`, `isEdgeBuild`, `isCloudBuild`, array `ALLOWED_ROLES`), so: keep
   `runpod-v1`'s `deployment.js` and resolve `App.js` once. That lands as **one PR to `testing`**, which both branches then merge.
   Targets: `REACT_APP_DEPLOYMENT_TARGET=edge` (your build), `cloud` (RunPod), unset/`all` (local compose, every route).
3. **API changes are announced** before merge, both ways.
4. **Frame metadata:** add `capture_ts` (section 5).
5. **Test IDs:** drones `DRN-001..003` ↔ `edge_laptop_01..03` for the 3-feed test (Phases 8/11).
6. **Evidence clips** stay on the laptop SSD for now (no clip upload to the cloud yet, edge plan E6/E7).

## 8. Your first test (Phase 7 "done when")

1. Install with the `edge.env` above (laptop 1), log in as `operator@dvas.kp`, start a mission on `DRN-001`, send ~30 s
   of frames, end the mission.
2. Tell me the time. I check on the pod: `dvas-ctl lag` (stream `mission:frames:<mission id>` of DRN-001, consumer groups advancing)
   and the dashboard *Multi-drone* view.
3. Send back: installer version, laptop OS, upload fps/resolution/JPEG quality, any 401/403/422/429 you saw.
