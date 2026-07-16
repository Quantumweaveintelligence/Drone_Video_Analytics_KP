"""
setup_keycloak.py
=================
Auto-configures Keycloak for the Kerala Police Drone Ingest System.

Creates:
  - Realm: kerala-police-drone
  - Client: drone-ingest-client (confidential, direct access grants)
  - Roles: drone-operator, control-room, supervisor, sys-admin
  - Users: operator_001 through operator_005 (all assigned drone-operator role)

Usage:
    pip install httpx
    python scripts/setup_keycloak.py
    python scripts/setup_keycloak.py --url http://localhost:8080 --password MySecurePass!
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import httpx

# ── Defaults ──────────────────────────────────────────────────────────────────
KC_URL         = "http://localhost:8080"
ADMIN_USER     = "admin"
ADMIN_PASSWORD = "Admin@KP2025!"
REALM          = "kerala-police-drone"
CLIENT_ID_NAME = "drone-ingest-client"

ROLES = ["drone-operator", "control-room", "supervisor", "sys-admin"]

OPERATORS = [
    {"username": "operator_001", "password": "Operator@001", "firstName": "Operator", "lastName": "One"},
    {"username": "operator_002", "password": "Operator@002", "firstName": "Operator", "lastName": "Two"},
    {"username": "operator_003", "password": "Operator@003", "firstName": "Operator", "lastName": "Three"},
    {"username": "operator_004", "password": "Operator@004", "firstName": "Operator", "lastName": "Four"},
    {"username": "operator_005", "password": "Operator@005", "firstName": "Operator", "lastName": "Five"},
]


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
        "accessTokenLifespan":     300,    # 5 minutes
        "refreshTokenMaxReuse":    0,
        "bruteForceProtected":     True,
    })
    if r.status_code == 409:
        log(f"Realm '{realm}' already exists — skipping", "WARN")
    else:
        r.raise_for_status()
        log(f"Realm '{realm}' created", "OK")


def create_client(base_url: str, token: str, realm: str, client_id: str) -> str:
    """Create the drone-ingest-client and return its UUID."""
    # Check if exists
    r = api("get", f"{base_url}/admin/realms/{realm}/clients?clientId={client_id}", token)
    existing = r.json()
    if existing:
        client_uuid = existing[0]["id"]
        log(f"Client '{client_id}' already exists (id={client_uuid}) — skipping", "WARN")
        return client_uuid

    r = api("post", f"{base_url}/admin/realms/{realm}/clients", token, json={
        "clientId":               client_id,
        "enabled":                True,
        "clientAuthenticatorType": "client-secret",
        "standardFlowEnabled":    False,
        "directAccessGrantsEnabled": True,
        "serviceAccountsEnabled":    False,
        "publicClient":           False,
        "protocol":               "openid-connect",
    })
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
            log(f"User '{username}' already exists — skipping creation", "WARN")
        else:
            r2 = api("post", f"{base_url}/admin/realms/{realm}/users", token, json={
                "username":    username,
                "firstName":   u.get("firstName", ""),
                "lastName":    u.get("lastName", ""),
                "email":       f"{username}@kerala.police.in",
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

        # Assign drone-operator role
        op_role_id = role_ids.get("drone-operator")
        if op_role_id:
            api("post",
                f"{base_url}/admin/realms/{realm}/users/{user_id}/role-mappings/realm",
                token,
                json=[{"id": op_role_id, "name": "drone-operator"}])
            log(f"  -> role 'drone-operator' assigned to '{username}'", "OK")


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-configure Keycloak for Drone Ingest")
    parser.add_argument("--url",      default=KC_URL,         help="Keycloak base URL")
    parser.add_argument("--user",     default=ADMIN_USER,     help="Admin username")
    parser.add_argument("--password", default=ADMIN_PASSWORD, help="Admin password")
    parser.add_argument("--realm",    default=REALM,          help="Realm name")
    parser.add_argument("--no-wait",  action="store_true",    help="Skip startup wait")
    args = parser.parse_args()

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
        token  = get_admin_token(base, args.user, args.password)
        create_realm(base, token, args.realm)
        uuid   = create_client(base, token, args.realm, CLIENT_ID_NAME)
        secret = get_client_secret(base, token, args.realm, uuid)
        r_ids  = create_roles(base, token, args.realm, ROLES)
        create_users(base, token, args.realm, OPERATORS, r_ids)

        print()
        print("=" * 62)
        log("Setup complete!", "OK")
        print()
        print(f"  Realm:         {args.realm}")
        print(f"  Client ID:     {CLIENT_ID_NAME}")
        print(f"  Client Secret: {secret}")
        print()
        print("  ⚠  Copy this secret into:")
        print("       • docker-compose.yml → KC_CLIENT_SECRET (if needed)")
        print("       • ingest/edge_sender.py → CLIENT_SEC")
        print("=" * 62)
        print()

    except Exception as exc:
        print(f"\n  ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
