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
    {"username": "operator_001", "password": "Operator@001", "firstName": "Operator", "lastName": "One", "role": "drone-operator"},
    {"username": "operator_002", "password": "Operator@002", "firstName": "Operator", "lastName": "Two", "role": "drone-operator"},
    {"username": "operator_003", "password": "Operator@003", "firstName": "Operator", "lastName": "Three", "role": "drone-operator"},
    {"username": "operator_004", "password": "Operator@004", "firstName": "Operator", "lastName": "Four", "role": "drone-operator"},
    {"username": "operator_005", "password": "Operator@005", "firstName": "Operator", "lastName": "Five", "role": "drone-operator"},
    {"username": "admin_dvas", "password": "admin", "firstName": "System", "lastName": "Administrator", "email": "admin@dvas.kp", "role": "sys-admin"},
    {"username": "control_001", "password": "Control@001", "firstName": "Control", "lastName": "Room", "email": "control@dvas.kp", "role": "control-room"},
    {"username": "senior_001", "password": "Senior@001", "firstName": "Senior", "lastName": "Officer", "email": "senior@dvas.kp", "role": "supervisor"},
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


def create_admin_service_client(base_url: str, token: str, realm: str) -> str:
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
        log(f"Admin service client '{client_id_name}' already exists (id={client_uuid})", "WARN")
    else:
        r2 = api("post", f"{base_url}/admin/realms/{realm}/clients", token, json={
            "clientId":                  client_id_name,
            "enabled":                   True,
            "clientAuthenticatorType":   "client-secret",
            "standardFlowEnabled":       False,
            "directAccessGrantsEnabled": False,
            "serviceAccountsEnabled":    True,
            "publicClient":              False,
            "protocol":                  "openid-connect",
        })
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

        # Phase 1: Create the scoped admin service client
        admin_secret = create_admin_service_client(base, token, args.realm)

        print()
        print("=" * 62)
        log("Setup complete!", "OK")
        print()
        print(f"  Realm:         {args.realm}")
        print(f"  Client ID:     {CLIENT_ID_NAME}")
        print(f"  Client Secret: {secret}")
        print()
        print(f"  Admin Service Client:  {ADMIN_SERVICE_CLIENT_ID}")
        print(f"  Admin Service Secret:  {admin_secret}")
        print()
        print("  ⚠  Copy these secrets into:")
        print("       • docker-compose.yml → KC_CLIENT_SECRET (ingest client)")
        print("       • docker-compose.yml → ADMIN_CLIENT_SECRET (admin service)")
        print("       • ingest/edge_sender.py → CLIENT_SEC")
        print("=" * 62)
        print()

    except Exception as exc:
        print(f"\n  ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

