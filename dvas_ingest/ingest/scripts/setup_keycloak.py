"""
setup_keycloak.py
=================
Auto-configures Keycloak for the Kerala Police Drone Ingest System.
Idempotent: safe to re-run on every boot (RunPod bootstrap does exactly that).

Creates / updates:
  - Realm: kerala-police-drone (+ optional frontendUrl for the public URL)
  - Client: drone-ingest-client (confidential, direct access grants)
  - Client: dvas-dashboard-client
  - Client: dvas-admin-service (service account, manage/view users)
  - Roles: drone-operator, control-room, supervisor, sys-admin
  - Users: read from a JSON file (--users-file) — never from source code

Secrets and passwords come from arguments / environment only (RunPod plan F9):
  KEYCLOAK_ADMIN, KEYCLOAK_ADMIN_PASSWORD   master-realm admin
  KC_CLIENT_SECRET                          drone-ingest-client secret
  ADMIN_CLIENT_SECRET                       dvas-admin-service secret

Users file format (JSON list):
  [{"username": "operator_001", "password": "...", "firstName": "Operator",
    "lastName": "One", "email": "operator@dvas.kp", "role": "drone-operator"}, ...]

Usage:
    pip install httpx
    python setup_keycloak.py --url http://localhost:8080 --users-file users.json
    python setup_keycloak.py --url http://127.0.0.1:8080 --no-wait \
        --client-secret "$KC_CLIENT_SECRET" --admin-client-secret "$ADMIN_CLIENT_SECRET" \
        --users-file /workspace/dvas/secrets/users.json --frontend-url "$PUBLIC_BASE_URL"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

# ── Defaults (no secrets in source) ───────────────────────────────────────────
KC_URL         = os.getenv("KEYCLOAK_URL", "http://localhost:8080")
ADMIN_USER     = os.getenv("KEYCLOAK_ADMIN", "admin")
ADMIN_PASSWORD = os.getenv("KEYCLOAK_ADMIN_PASSWORD", "")
REALM          = os.getenv("KC_REALM", "kerala-police-drone")
CLIENT_ID_NAME = "drone-ingest-client"

ROLES = ["drone-operator", "control-room", "supervisor", "sys-admin"]

# Users must be supplied with --users-file (see module docstring).
OPERATORS: list[dict] = []


def log(msg: str, level: str = "INFO") -> None:
    colors = {"INFO": "\033[94m", "OK": "\033[92m", "WARN": "\033[93m", "ERR": "\033[91m"}
    reset  = "\033[0m"
    c      = colors.get(level, "")
    print(f"  {c}[{level}]{reset}  {msg}")


def wait_for_keycloak(base_url: str, timeout_s: int = 120) -> bool:
    """Wait until Keycloak health endpoint is reachable."""
    log(f"Waiting for Keycloak at {base_url} (timeout {timeout_s}s)...")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base_url}/", timeout=5)
            if r.status_code in (200, 302):
                log("Keycloak is healthy!", "OK")
                return True
        except Exception:
            pass
        time.sleep(3)
        print(".", end="", flush=True)
    print()
    return False


def get_admin_token(base_url: str, user: str, pw: str) -> str:
    """Get an admin access token from the master realm."""
    r = httpx.post(
        f"{base_url}/realms/master/protocol/openid-connect/token",
        data={
            "client_id":  "admin-cli",
            "username":   user,
            "password":   pw,
            "grant_type": "password",
        },
        timeout=10,
    )
    r.raise_for_status()
    token = r.json()["access_token"]
    log("Admin token acquired", "OK")
    return token


def api(method: str, url: str, token: str, **kwargs) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = getattr(httpx, method)(url, headers=headers, timeout=15, **kwargs)
    return r


def create_realm(base_url: str, token: str, realm: str) -> None:
    r = api("post", f"{base_url}/admin/realms", token, json={
        "realm":                   realm,
        "enabled":                 True,
        "displayName":             "Kerala Police Drone Operations",
        "accessTokenLifespan":     3600,   # 1 hour
        "ssoSessionIdleTimeout":   86400,  # 24 hours
        "ssoSessionMaxLifespan":   604800, # 7 days
    })
    if r.status_code == 409:
        log(f"Realm '{realm}' already exists — skipping creation", "WARN")
    else:
        r.raise_for_status()
        log(f"Realm '{realm}' created", "OK")


def create_client(base_url: str, token: str, realm: str, client_id: str,
                  secret: str | None = None) -> str:
    """Create (or update) a confidential client and return its UUID.

    When `secret` is given it is applied explicitly — on creation and, for an
    existing client, via PUT — so the value always matches the deployment's
    generated secrets. Without it Keycloak keeps/generates its own secret.
    """
    r = api("get", f"{base_url}/admin/realms/{realm}/clients?clientId={client_id}", token)
    r.raise_for_status()
    if r.json():
        rep  = r.json()[0]
        uuid = rep["id"]
        if secret:
            rep["secret"] = secret
            u = api("put", f"{base_url}/admin/realms/{realm}/clients/{uuid}", token, json=rep)
            u.raise_for_status()
            log(f"Client '{client_id}' exists (id={uuid}) — secret updated", "OK")
        else:
            log(f"Client '{client_id}' already exists (id={uuid})", "WARN")
        return uuid

    body = {
        "clientId":               client_id,
        "name":                   "Drone Ingest Client",
        "description":            "Confidential client for drone edge devices and operators",
        "enabled":                True,
        "clientAuthenticatorType": "client-secret",
        "directAccessGrantsEnabled": True,
        "serviceAccountsEnabled":  True,
        "standardFlowEnabled":     True,
        "implicitFlowEnabled":     False,
        "publicClient":           False,
        "protocol":               "openid-connect",
    }
    if secret:
        body["secret"] = secret
    r = api("post", f"{base_url}/admin/realms/{realm}/clients", token, json=body)
    r.raise_for_status()

    # Fetch the UUID
    r2      = api("get", f"{base_url}/admin/realms/{realm}/clients?clientId={client_id}", token)
    uuid    = r2.json()[0]["id"]
    log(f"Client '{client_id}' created (id={uuid})", "OK")
    return uuid


def get_client_secret(base_url: str, token: str, realm: str, client_uuid: str) -> str:
    r = api("get", f"{base_url}/admin/realms/{realm}/clients/{client_uuid}/client-secret", token)
    r.raise_for_status()
    secret = r.json()["value"]
    return secret


def create_roles(base_url: str, token: str, realm: str, roles: list[str]) -> dict[str, str]:
    """Create realm roles and return {name: id} mapping."""
    # Fetch existing
    r        = api("get", f"{base_url}/admin/realms/{realm}/roles", token)
    existing = {role["name"]: role["id"] for role in r.json()}

    role_ids: dict[str, str] = {}
    for role_name in roles:
        if role_name in existing:
            role_ids[role_name] = existing[role_name]
            log(f"Role '{role_name}' already exists — skipping", "WARN")
            continue
        r2 = api("post", f"{base_url}/admin/realms/{realm}/roles", token, json={
            "name":        role_name,
            "description": f"Kerala Police Drone role: {role_name}",
        })
        r2.raise_for_status()
        # Re-fetch to get ID
        r3 = api("get", f"{base_url}/admin/realms/{realm}/roles/{role_name}", token)
        role_ids[role_name] = r3.json()["id"]
        log(f"Role '{role_name}' created", "OK")

    return role_ids


def create_users(
    base_url: str,
    token:    str,
    realm:    str,
    users:    list[dict],
    role_ids: dict[str, str],
) -> None:
    """Create operator users and assign drone-operator role."""
    for u in users:
        username = u["username"]

        # Check if exists
        r = api("get", f"{base_url}/admin/realms/{realm}/users?username={username}", token)
        if r.json():
            user_id = r.json()[0]["id"]
            # Ensure password is set/updated
            api("put", f"{base_url}/admin/realms/{realm}/users/{user_id}/reset-password", token, json={
                "type": "password",
                "value": u["password"],
                "temporary": False
            })
            log(f"User '{username}' already exists — password synchronized", "OK")
        else:
            r2 = api("post", f"{base_url}/admin/realms/{realm}/users", token, json={
                "username":    username,
                "firstName":   u.get("firstName", ""),
                "lastName":    u.get("lastName", ""),
                "email":       u.get("email", f"{username}@kerala.police.in"),
                "enabled":     True,
                "credentials": [{
                    "type":      "password",
                    "value":     u["password"],
                    "temporary": False,
                }],
            })
            r2.raise_for_status()
            # Fetch user ID
            r3 = api("get", f"{base_url}/admin/realms/{realm}/users?username={username}", token)
            user_id = r3.json()[0]["id"]
            log(f"User '{username}' created", "OK")

        # Assign role
        target_role = u.get("role", "drone-operator")
        role_id = role_ids.get(target_role)
        if role_id:
            api("post",
                f"{base_url}/admin/realms/{realm}/users/{user_id}/role-mappings/realm",
                token,
                json=[{"id": role_id, "name": target_role}])
            log(f"  -> role '{target_role}' assigned to '{username}'", "OK")


ADMIN_SERVICE_CLIENT_ID = "dvas-admin-service"


def create_admin_service_client(base_url: str, token: str, realm: str,
                                secret: str | None = None) -> str:
    """
    Create the dvas-admin-service confidential client with:
      - Service Accounts Enabled (M2M client_credentials grant)
      - No direct access grants, no standard flow
      - Scoped to manage-users and view-users only
    Returns the client secret.
    """
    client_id_name = ADMIN_SERVICE_CLIENT_ID

    # Check if already exists
    r = api("get", f"{base_url}/admin/realms/{realm}/clients?clientId={client_id_name}", token)
    existing = r.json()
    if existing:
        client_uuid = existing[0]["id"]
        if secret:
            rep = existing[0]
            rep["secret"] = secret
            u = api("put", f"{base_url}/admin/realms/{realm}/clients/{client_uuid}", token, json=rep)
            u.raise_for_status()
            log(f"Admin service client '{client_id_name}' exists (id={client_uuid}) — secret updated", "OK")
        else:
            log(f"Admin service client '{client_id_name}' already exists (id={client_uuid})", "WARN")
    else:
        body = {
            "clientId":                  client_id_name,
            "enabled":                   True,
            "clientAuthenticatorType":   "client-secret",
            "standardFlowEnabled":       False,
            "directAccessGrantsEnabled": False,
            "serviceAccountsEnabled":    True,
            "publicClient":              False,
            "protocol":                  "openid-connect",
        }
        if secret:
            body["secret"] = secret
        r2 = api("post", f"{base_url}/admin/realms/{realm}/clients", token, json=body)
        r2.raise_for_status()

        r3 = api("get", f"{base_url}/admin/realms/{realm}/clients?clientId={client_id_name}", token)
        client_uuid = r3.json()[0]["id"]
        log(f"Admin service client '{client_id_name}' created (id={client_uuid})", "OK")

    # Retrieve client secret
    secret_r = api("get", f"{base_url}/admin/realms/{realm}/clients/{client_uuid}/client-secret", token)
    secret_r.raise_for_status()
    client_secret = secret_r.json()["value"]

    # Assign realm-management roles (manage-users, view-users) to the service account
    # 1. Get the service account user for this client
    sa_r = api("get", f"{base_url}/admin/realms/{realm}/clients/{client_uuid}/service-account-user", token)
    if sa_r.status_code != 200:
        log(f"Could not fetch service account user: {sa_r.status_code}", "ERR")
        return client_secret
    sa_user_id = sa_r.json()["id"]

    # 2. Find the realm-management client UUID
    rm_r = api("get", f"{base_url}/admin/realms/{realm}/clients?clientId=realm-management", token)
    rm_clients = rm_r.json()
    if not rm_clients:
        log("realm-management client not found — skipping role assignment", "WARN")
        return client_secret
    rm_client_uuid = rm_clients[0]["id"]

    # 3. Get available client roles for realm-management
    avail_r = api("get",
        f"{base_url}/admin/realms/{realm}/users/{sa_user_id}/role-mappings/clients/{rm_client_uuid}/available",
        token)
    if avail_r.status_code != 200:
        log(f"Could not fetch available realm-management roles: {avail_r.status_code}", "WARN")
        return client_secret

    available_roles = avail_r.json()
    target_role_names = {"manage-users", "view-users", "view-realm", "query-realms"}
    roles_to_assign = [r for r in available_roles if r["name"] in target_role_names]

    if roles_to_assign:
        assign_r = api("post",
            f"{base_url}/admin/realms/{realm}/users/{sa_user_id}/role-mappings/clients/{rm_client_uuid}",
            token, json=roles_to_assign)
        if assign_r.status_code in (200, 204):
            assigned_names = [r["name"] for r in roles_to_assign]
            log(f"Service account roles assigned: {assigned_names}", "OK")
        else:
            log(f"Failed to assign service account roles: {assign_r.status_code}", "ERR")
    else:
        log("manage-users/view-users roles already assigned or not available", "WARN")

    return client_secret


def set_frontend_url(base_url: str, token: str, realm: str, frontend_url: str) -> None:
    """Set the realm's frontendUrl so tokens carry the public issuer (pod URL)."""
    r = api("get", f"{base_url}/admin/realms/{realm}", token)
    r.raise_for_status()
    rep = r.json()
    attrs = rep.get("attributes") or {}
    if attrs.get("frontendUrl") == frontend_url:
        log(f"Realm frontendUrl already {frontend_url}", "OK")
        return
    attrs["frontendUrl"] = frontend_url
    u = api("put", f"{base_url}/admin/realms/{realm}", token, json={"realm": realm, "attributes": attrs})
    u.raise_for_status()
    log(f"Realm frontendUrl set to {frontend_url}", "OK")


def load_users_file(path: str | None) -> list[dict]:
    """Read the users JSON list; [] (with a warning) when no file is given/found."""
    if not path:
        log("No --users-file given — user creation/password sync skipped", "WARN")
        return []
    p = Path(path)
    if not p.is_file():
        log(f"Users file {path} not found — user creation/password sync skipped", "WARN")
        return []
    users = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(users, list):
        raise ValueError(f"{path}: expected a JSON list of users")
    required = {"username", "password", "role"}
    for u in users:
        missing = required - set(u)
        if missing:
            raise ValueError(f"{path}: user entry {u.get('username', '?')} missing {sorted(missing)}")
        if u["role"] not in ROLES:
            raise ValueError(f"{path}: user {u['username']} has unknown role {u['role']} (allowed: {ROLES})")
    return users


def _mask(secret: str, show: bool) -> str:
    if show or not secret:
        return secret
    return f"{secret[:4]}…{secret[-2:]} (hidden; use --show-secrets)"


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-configure Keycloak for Drone Ingest")
    parser.add_argument("--url",      default=KC_URL,         help="Keycloak base URL")
    parser.add_argument("--user",     default=ADMIN_USER,     help="Admin username")
    parser.add_argument("--password", default=ADMIN_PASSWORD, help="Admin password")
    parser.add_argument("--realm",    default=REALM,          help="Realm name")
    parser.add_argument("--no-wait",  action="store_true",    help="Skip startup wait")
    parser.add_argument("--client-secret", default=os.getenv("KC_CLIENT_SECRET", ""),
                        help="Explicit drone-ingest-client secret (default: $KC_CLIENT_SECRET)")
    parser.add_argument("--admin-client-secret", default=os.getenv("ADMIN_CLIENT_SECRET", ""),
                        help="Explicit dvas-admin-service secret (default: $ADMIN_CLIENT_SECRET)")
    parser.add_argument("--users-file", default=os.getenv("KC_USERS_FILE", ""),
                        help="JSON list of users to create/update (default: $KC_USERS_FILE)")
    parser.add_argument("--frontend-url", default="",
                        help="Public base URL to set as the realm frontendUrl (e.g. the RunPod proxy URL)")
    parser.add_argument("--show-secrets", action="store_true",
                        help="Print client secrets in full at the end (default: masked)")
    args = parser.parse_args()

    if not args.password:
        print("\n  ERROR: Keycloak admin password missing (--password or $KEYCLOAK_ADMIN_PASSWORD).",
              file=sys.stderr)
        sys.exit(2)

    print()
    print("=" * 62)
    print("  Kerala Police Drone — Keycloak Auto-Configuration")
    print("=" * 62)

    base = args.url.rstrip("/")

    if not args.no_wait:
        if not wait_for_keycloak(base):
            print("\n  ERROR: Keycloak did not become healthy in time.", file=sys.stderr)
            sys.exit(1)

    try:
        users  = load_users_file(args.users_file)
        token  = get_admin_token(base, args.user, args.password)
        create_realm(base, token, args.realm)
        if args.frontend_url:
            set_frontend_url(base, token, args.realm, args.frontend_url.rstrip("/"))
        uuid   = create_client(base, token, args.realm, CLIENT_ID_NAME, secret=args.client_secret or None)
        secret = get_client_secret(base, token, args.realm, uuid)
        
        # Human Dashboard UI client
        dash_uuid   = create_client(base, token, args.realm, "dvas-dashboard-client")
        dash_secret = get_client_secret(base, token, args.realm, dash_uuid)

        r_ids  = create_roles(base, token, args.realm, ROLES)
        create_users(base, token, args.realm, users, r_ids)

        # Phase 1: Create the scoped admin service client
        admin_secret = create_admin_service_client(base, token, args.realm,
                                                   secret=args.admin_client_secret or None)

        print()
        print("=" * 62)
        log("Setup complete!", "OK")
        print()
        print(f"  Realm:                  {args.realm}")
        print(f"  Ingest M2M Client ID:   {CLIENT_ID_NAME}")
        print(f"  Ingest Client Secret:   {_mask(secret, args.show_secrets)}")
        print(f"  Dashboard Client ID:    dvas-dashboard-client")
        print(f"  Dashboard Client Secret:{_mask(dash_secret, args.show_secrets)}")
        print()
        print(f"  Admin Service Client:   {ADMIN_SERVICE_CLIENT_ID}")
        print(f"  Admin Service Secret:   {_mask(admin_secret, args.show_secrets)}")
        print(f"  Users created/updated:  {len(users)}")
        print()
        print("  Secrets must match: KC_CLIENT_SECRET / ADMIN_CLIENT_SECRET in the")
        print("  environment of the dashboard backend and the edge application.")
        print("=" * 62)
        print()

    except Exception as exc:
        print(f"\n  ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

