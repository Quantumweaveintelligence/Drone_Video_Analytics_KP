#!/usr/bin/env bash
# diagnose_login.sh — why does the dashboard login fail? (A7 local test, or any running pod container)
#   deploy/runpod/tools/diagnose_login.sh [container]        (default container: dvas-pod-test; run from the repo root)
# Read-only. Secrets and passwords are NOT written to the report (only "set"/lengths, HTTP codes, Keycloak errors).
# Output → screen + deploy/runpod/.logs/login-diag-<time>.txt (git-ignored; Claude can read it).
set -uo pipefail
C="${1:-dvas-pod-test}"
[ -f deploy/runpod/Dockerfile ] || { echo "run from the repo root" >&2; exit 2; }
mkdir -p deploy/runpod/.logs
OUT="deploy/runpod/.logs/login-diag-$(date +%Y%m%d-%H%M%S).txt"

{
echo "DVAS login diagnosis — $(date '+%F %T %Z') — container $C"
docker ps --filter "name=^${C}$" --format 'status: {{.Status}}  image: {{.Image}}'

echo; echo "=== Keycloak + backend checks (inside the container) ==="
docker exec -i "$C" /opt/venvs/backend/bin/python - <<'PY'
import json, os, httpx
env = {}
for line in open("/workspace/dvas/secrets/dvas.env"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); env[k] = v
users = json.load(open("/workspace/dvas/secrets/users.json"))
KC = "http://127.0.0.1:8080"; REALM = "kerala-police-drone"; CID = "drone-ingest-client"
def show(label, r):
    body = r.text[:300].replace("\n", " ")
    try:
        j = r.json(); body = {k: j[k] for k in ("error", "error_description") if k in j} or "(ok)"
    except Exception: pass
    print(f"  {label}: HTTP {r.status_code}  {body}")

print("secrets: KC_CLIENT_SECRET", "set" if env.get("KC_CLIENT_SECRET") else "MISSING",
      "| KEYCLOAK_ADMIN_PASSWORD", "set" if env.get("KEYCLOAK_ADMIN_PASSWORD") else "MISSING")
print("users.json:", [(u["username"], u.get("email"), u.get("role"), f"pw len {len(u.get('password',''))}") for u in users])
try:
    print("keycloak ready:", httpx.get(f"{KC}/health/ready", timeout=5).status_code)
except Exception as e:
    print("keycloak ready: UNREACHABLE", e)

tok = f"{KC}/realms/{REALM}/protocol/openid-connect/token"
show("realm exists (openid-configuration)", httpx.get(f"{KC}/realms/{REALM}/.well-known/openid-configuration", timeout=5))
show("client secret valid (client_credentials)", httpx.post(tok, data={"grant_type": "client_credentials",
     "client_id": CID, "client_secret": env.get("KC_CLIENT_SECRET", "")}, timeout=10))
for u in users[:4]:
    show(f"password login {u['username']}", httpx.post(tok, data={"grant_type": "password", "client_id": CID,
         "client_secret": env.get("KC_CLIENT_SECRET", ""), "username": u["username"], "password": u["password"]}, timeout=10))

# Admin view of the realm / client / users
r = httpx.post(f"{KC}/realms/master/protocol/openid-connect/token", data={"grant_type": "password",
    "client_id": "admin-cli", "username": env.get("KEYCLOAK_ADMIN", "admin"),
    "password": env.get("KEYCLOAK_ADMIN_PASSWORD", "")}, timeout=10)
show("admin login (master realm)", r)
if r.status_code == 200:
    h = {"Authorization": "Bearer " + r.json()["access_token"]}
    A = f"{KC}/admin/realms/{REALM}"
    realm = httpx.get(A, headers=h, timeout=10)
    if realm.status_code == 200:
        j = realm.json()
        print("  realm:", {k: j.get(k) for k in ("enabled", "loginWithEmailAllowed", "bruteForceProtected", "sslRequired")},
              "frontendUrl:", (j.get("attributes") or {}).get("frontendUrl"))
    else:
        show("realm (admin)", realm)
    cl = httpx.get(f"{A}/clients", params={"clientId": CID}, headers=h, timeout=10).json()
    if cl:
        c = cl[0]
        print("  client:", {k: c.get(k) for k in ("enabled", "publicClient", "directAccessGrantsEnabled", "serviceAccountsEnabled")})
        sec = httpx.get(f"{A}/clients/{c['id']}/client-secret", headers=h, timeout=10).json().get("value", "")
        print("  client secret in Keycloak == dvas.env KC_CLIENT_SECRET:", sec == env.get("KC_CLIENT_SECRET"))
    else:
        print("  client: NOT FOUND")
    for u in users[:4]:
        found = httpx.get(f"{A}/users", params={"username": u["username"], "exact": "true"}, headers=h, timeout=10).json()
        if not found:
            print(f"  user {u['username']}: NOT FOUND"); continue
        x = found[0]
        creds = httpx.get(f"{A}/users/{x['id']}/credentials", headers=h, timeout=10).json()
        roles = [ro["name"] for ro in httpx.get(f"{A}/users/{x['id']}/role-mappings/realm", headers=h, timeout=10).json()]
        print(f"  user {u['username']}:", {k: x.get(k) for k in ("enabled", "email", "emailVerified", "firstName", "lastName", "requiredActions")},
              "credentials:", [c.get("type") for c in creds], "roles:", roles)
    ev = httpx.get(f"{A}/attack-detection/brute-force/users/{(found or [{}])[0].get('id','x')}", headers=h, timeout=10)
    if ev.status_code == 200: print("  brute-force state (last user):", ev.json())

# The backend itself (same call the browser makes)
u = users[0]
try:
    r = httpx.post("http://127.0.0.1:8001/api/auth/login", json={"email": u.get("email"), "password": u["password"]}, timeout=15)
    print(f"backend /api/auth/login as {u.get('email')}: HTTP {r.status_code} {r.text[:200]}")
except Exception as e:
    print("backend /api/auth/login: ERROR", e)
try:
    r = httpx.post("http://127.0.0.1:80/api/auth/login", json={"email": u.get("email"), "password": u["password"]}, timeout=15)
    print(f"nginx :80 /api/auth/login as {u.get('email')}: HTTP {r.status_code} {r.text[:200]}")
except Exception as e:
    print("nginx :80 /api/auth/login: ERROR", e)
PY

echo; echo "=== bootstrap.log (Keycloak part) ==="
docker exec "$C" sh -c 'grep -vE "password|secret" /var/log/dvas/bootstrap.log | head -80'
echo; echo "=== keycloak.log: errors / login events (last 30) ==="
docker exec "$C" sh -c 'grep -E "ERROR|WARN|LOGIN_ERROR|invalid_" /var/log/dvas/keycloak.log | tail -30'
echo; echo "=== backend.log: last 40 lines ==="
docker exec "$C" sh -c 'tail -40 /var/log/dvas/backend.log' | grep -viE '"password"|secret='
echo; echo "=== supervisor status ==="
docker exec "$C" dvas-ctl status 2>&1 | head -30
} 2>&1 | tee "$OUT"

echo; echo "Saved: $OUT — tell Claude \"login report saved\"."
