# DVAS — Phase 7: Edge integration handoff (RunPod side)

> Plan: `DVAS_RunPod_Deployment_Plan.md` Phase 7 · Written 30 Oct 2026 · **Updated 30 Oct 17:15 for pod `4nrza3dukc231h`,
> image `20261030-d0df131`** (items 1–10 checked live; results in `context_runpodv1.md` §12).
> **Part 1** = how *you* (RunPod/image owner) collect each item. **Part 2** = the sheet you send the edge developer:
> **`DVAS_Edge_Integration_Sheet.md`** (no secrets in it). **Secrets go only through the team password manager**, never chat, email or git.

---

## Part 1 — Collecting each item

Run the 🖥 commands **in the pod** (`ssh root@<IP> -p <PORT> -i ~/.ssh/id_ed25519`, IP/port from
**Connect → Direct TCP Ports**, or the pod's env `RUNPOD_PUBLIC_IP` / `RUNPOD_TCP_PORT_22`). Run the 💻 commands **on your laptop**.

> **SSH key refused after a re-deploy?** Admin keys come from RunPod's account keys plus
> `/workspace/dvas/secrets/authorized_keys` — **one key per line, never wrapped**. A wrapped key (30 Oct) locked the
> builder out. Fix in the Web Terminal: `echo '<one-line .pub>' | tee -a /root/.ssh/authorized_keys >> /workspace/dvas/secrets/authorized_keys`.
> Images from `c7da60c` on skip broken lines and log `SSH: N authorized key(s), skipped …`.

### 1. Server base URL
The pod's proxy URL: `https://<POD_ID>-80.proxy.runpod.net`. Today: **`https://4nrza3dukc231h-80.proxy.runpod.net`**
(it was `o5vg7007ar5zk9` → `erv9019r7wxhsu` → `4nrza3dukc231h` on 29–30 Oct: every template change re-created the pod).
🖥 Confirm what the pod itself uses:
```bash
grep -h PUBLIC_BASE_URL /run/dvas/runtime.env
```
It changes **whenever the pod is re-created** (Terminate → Deploy, and in practice also a template/image change).
Stop/Start keeps the pod ID. Each time it
changes, send the edge developer the new URL straight away.

### 2. Ingest endpoints (fixed, from the code)
`POST /ingest/frame`, `POST /ingest/mission/start`, `POST /ingest/mission/end`, `POST /ingest/mission/geofence`.
The same service also has `/ingest/incident`, `/ingest/model-suggestion`, `/ingest/streams/{drone_id}/purge`.
Every call needs `Authorization: Bearer <access_token>` with role `drone-operator` (or `sys-admin`).
Max request body 10 MB; rate-limited by nginx (HTTP 429 → back off and retry).

### 3. Token endpoint (fixed)
`POST <BASE_URL>/realms/kerala-police-drone/protocol/openid-connect/token`, client **`drone-ingest-client`**,
`grant_type=password` with an uploader account (item 6) and the client secret (item 4). Refresh with
`grant_type=refresh_token`.

### 4. Client secret — `KC_CLIENT_SECRET` (🔒 secret)
You already copied `dvas.env` off the pod in Phase 4 (`~/dvas-runpod-secrets/`). Otherwise 🖥:
```bash
grep '^KC_CLIENT_SECRET=' /workspace/dvas/secrets/dvas.env
```
→ password manager entry **"DVAS cloud — drone-ingest-client secret"**. Share that entry with the edge developer.

### 5. Operator logins (🔒 secret)
The three operator accounts already exist (created by bootstrap from `users.json`):

| Username | Login email (dashboard) |
|---|---|
| `operator_001` | `operator@dvas.kp` |
| `operator_002` | `operator_002@kerala.police.in` |
| `operator_003` | `operator_003@kerala.police.in` |

🖥 Passwords:
```bash
python3 -c "import json; [print(u['username'], u['email'], u['password']) for u in json.load(open('/workspace/dvas/secrets/users.json')) if u['role']=='drone-operator']"
```
→ one password-manager entry per operator. (The test passwords pasted in chat on 30 Oct should be rotated first;
see item 6 for how, the same way.)

### 6. Uploader accounts — one per laptop (🔒 secret)
Create them the same way as every other DVAS user: add them to `users.json` on the volume and re-run bootstrap.
`setup_keycloak.py` creates missing users, sets their passwords and assigns the role. They survive pod re-creation
because `users.json` lives on the volume. 🖥:
```bash
python3 - <<'PY'
import json, secrets, string
p = "/workspace/dvas/secrets/users.json"
users = json.load(open(p))
have = {u["username"] for u in users}
alphabet = string.ascii_letters + string.digits
for i in (1, 2, 3):                                   # one per edge laptop; change the range as needed
    name = f"edge_laptop_{i:02d}"
    if name in have:
        continue
    users.append({"username": name, "firstName": "Edge", "lastName": f"Laptop {i:02d}",
                  "email": f"{name}@dvas.kp", "role": "drone-operator",
                  "password": "".join(secrets.choice(alphabet) for _ in range(20)) + "@"})
    print("added", name)
json.dump(users, open(p, "w"), indent=2)
PY
dvas-ctl restart bootstrap
sleep 60; tail -5 /var/log/dvas/bootstrap.log                     # → "bootstrap complete"
python3 -c "import json; [print(u['username'], u['password']) for u in json.load(open('/workspace/dvas/secrets/users.json')) if u['username'].startswith('edge_laptop')]"
```
First/last name and email are filled on purpose. Keycloak 24 refuses token requests for accounts with an
incomplete profile ("Account is not fully set up").
→ one password-manager entry per laptop. Afterwards `scp` the updated `users.json` to the password manager copy
again (Phase 4, B9).

**Rotate a password** (any user): change its `"password"` in `users.json`, then `dvas-ctl restart bootstrap`.

### 7. `EDGE_API_KEY` and `DVAS_SERVICE_KEY` — **not shared** (corrected 30 Oct 17:15)
- The cloud's `EDGE_API_KEY` (in `dvas.env`) is **checked by no cloud service**. The edge's own `EDGE_API_KEY` protects
  only its local edge core (`/edge-api/*` on the laptop) and is generated per build (installer `/DEdgeApiKey=`).
- `POST /api/push/receive` (`X-DVAS-Service-Key: <DVAS_SERVICE_KEY>`) is **backend↔backend** peering (`DVAS_PEER_URL`,
  the old edge-backend + command-backend setup). In the `.exe` design the edge pages call the cloud backend
  directly through the launcher's `/api/*` proxy, so the edge developer doesn't need it. Keep it on the pod only
  (checked live: no key → 403, right key → accepted).

### 8. Cloud API used by the edge pages (fixed)
`/api/auth/login`, `/api/auth/refresh`, `/api/auth/me`, `/api/missions*`, `/api/audit-logs*`, `/api/incidents*`,
`/api/edge/*`, `/api/push/receive`, `/api/ws` (WebSocket, `wss://` through the proxy). Login uses the user's
**email** + password (`POST /api/auth/login {"email","password"}`), rate-limited to 5/min.

### 9. CORS origin `http://localhost:8765`
Set by the image (`dvas-init.sh` → `CORS_ORIGINS` / `ALLOWED_ORIGINS` = `<BASE_URL>,http://localhost:8765`).
💻 Verify from your laptop:
```bash
BASE=https://4nrza3dukc231h-80.proxy.runpod.net
for p in /api/auth/login /ingest/frame; do
  echo "== $p"; curl -s -i -X OPTIONS "$BASE$p" -H "Origin: http://localhost:8765" \
    -H "Access-Control-Request-Method: POST" | grep -i '^access-control-allow-origin'
done
```
✔ Both print `access-control-allow-origin: http://localhost:8765`.

### 10. End-to-end check before you hand over
💻 One token + one ingest call with an uploader account (prompts keep secrets off the command line and history):
```bash
BASE=https://4nrza3dukc231h-80.proxy.runpod.net
read -rsp "client secret: " CS; echo; read -rp "uploader user: " U; read -rsp "password: " P; echo
TOKEN=$(curl -s "$BASE/realms/kerala-police-drone/protocol/openid-connect/token" \
  -d grant_type=password -d client_id=drone-ingest-client --data-urlencode "client_secret=$CS" \
  --data-urlencode "username=$U" --data-urlencode "password=$P" | python3 -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))")
echo "token length: ${#TOKEN}"                     # > 500 = OK; 0 = check user/secret
curl -s -o /dev/null -w "ingest auth check: HTTP %{http_code}\n" -X POST "$BASE/ingest/mission/start" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}'
unset CS P TOKEN
```
✔ The token is long. The ingest call returns **422** (authorized, empty body rejected). 401/403 means a token or role
problem. Then the edge developer sends a real frame, and you watch it arrive 🖥 `dvas-ctl lag` (plan 7 "Done when").

---

## Part 2 — Sheet to send the edge developer

**`deploy/runpod/docs/DVAS_Edge_Integration_Sheet.md`** (no secrets; copy as is). It covers URL, the RunPod
`edge.env`, accounts, token, ingest contract, dashboard routes verified for `drone_operator`, WebSocket, CORS, keys
they do *not* need, the 7.2 contract (incl. the `App.js`/`deployment.js` merge) and their first test.
Update its URL whenever the pod is re-created.

## ✅ Done when (plan Phase 7)
- [ ] Password-manager entries shared: client secret, `operator_001..003`, `edge_laptop_01..03` (no service key)
- [ ] Part 2 sheet sent; contract 7.2 agreed
- [ ] CORS check (item 9) and token/ingest check (item 10) pass
- [ ] Shared frontend build-target PR merged to `testing` and into `runpod-v1`
- [ ] A test frame from the edge build shows in `dvas-ctl lag`
