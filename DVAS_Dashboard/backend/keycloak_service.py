"""
keycloak_service.py
===================
Zero-trust Keycloak integration helper for DVAS Backend.
Handles ephemeral service tokens and scoped user management
via the Keycloak Admin REST API.

Architecture:
  - Uses a dedicated M2M service account (`dvas-admin-service`)
  - Scoped strictly to `manage-users` and `view-users` in the
    `kerala-police-drone` realm.
  - Never stores or uses root master admin credentials.

100% Free & Open Source (uses httpx).
"""
from __future__ import annotations

import os
import time
import logging
import httpx
from fastapi import HTTPException, status

logger = logging.getLogger("dvas.keycloak_service")

# ── Configuration (injected via environment) ─────────────────────────────────
KEYCLOAK_INTERNAL_URL = os.getenv("KEYCLOAK_URL", "http://keycloak:8080")
REALM = os.getenv("KC_REALM", "kerala-police-drone")
ADMIN_CLIENT_ID = os.getenv("ADMIN_CLIENT_ID", "dvas-admin-service")
ADMIN_CLIENT_SECRET = os.getenv("ADMIN_CLIENT_SECRET", "")

# ── Keycloak role name → DVAS internal role name ─────────────────────────────
KC_ROLE_MAP = {
    "drone_operator": "drone-operator",
    "control_room_officer": "control-room",
    "senior_officer": "supervisor",
    "system_administrator": "sys-admin",
}

# Allowed roles that can be assigned via the dashboard
ALLOWED_ROLES = set(KC_ROLE_MAP.keys())

# ── Service token cache ──────────────────────────────────────────────────────
_svc_token_cache = {"token": "", "expires_at": 0}


async def get_service_token() -> str:
    """Obtain a short-lived scoped service account token via client_credentials grant."""
    now = time.time()
    if _svc_token_cache["token"] and now < _svc_token_cache["expires_at"] - 30:
        return _svc_token_cache["token"]

    url = f"{KEYCLOAK_INTERNAL_URL}/realms/{REALM}/protocol/openid-connect/token"
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(url, data={
            "grant_type": "client_credentials",
            "client_id": ADMIN_CLIENT_ID,
            "client_secret": ADMIN_CLIENT_SECRET,
        })
        if r.status_code != 200:
            logger.error(f"Service account auth failed: {r.status_code} {r.text}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="IAM service account authorization failed"
            )
        data = r.json()
        _svc_token_cache["token"] = data["access_token"]
        _svc_token_cache["expires_at"] = now + data.get("expires_in", 60)
        return data["access_token"]


async def list_users_from_keycloak() -> list[dict]:
    """List all users in the realm via Keycloak Admin REST API."""
    token = await get_service_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Fetch users
        res = await client.get(
            f"{KEYCLOAK_INTERNAL_URL}/admin/realms/{REALM}/users?max=500",
            headers=headers,
        )
        if res.status_code != 200:
            logger.error(f"Failed to list users: {res.status_code} {res.text}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to fetch users from IAM"
            )
        kc_users = res.json()

        result = []
        for u in kc_users:
            user_id = u["id"]

            # Fetch realm role mappings for each user
            role_res = await client.get(
                f"{KEYCLOAK_INTERNAL_URL}/admin/realms/{REALM}/users/{user_id}/role-mappings/realm",
                headers=headers,
            )
            roles = []
            if role_res.status_code == 200:
                roles = [r["name"] for r in role_res.json()]

            # Same priority rule as login (J7): highest DVAS role wins
            from core.security import derive_role
            dvas_role = derive_role(roles) or "none"

            attrs = u.get("attributes", {})
            result.append({
                "id": user_id,
                "email": u.get("email", ""),
                "name": f"{u.get('firstName', '')} {u.get('lastName', '')}".strip(),
                "role": dvas_role,
                "jurisdiction": (attrs.get("jurisdiction", [""])[0] if attrs.get("jurisdiction") else ""),
                "mission_scope": attrs.get("mission_scope", []),
                "suspended": not u.get("enabled", True),
                "created_at": u.get("createdTimestamp", ""),
            })

        return result


async def provision_user_in_keycloak(
    name: str,
    email: str,
    role: str,
    jurisdiction: str,
    mission_scope: list[str],
    password: str,
) -> dict:
    """
    Provision a new user in Keycloak with credentials and realm role binding.

    Steps:
      1. Create user with attributes (jurisdiction, mission_scope)
      2. Set password (non-temporary)
      3. Fetch the target realm role object
      4. Bind the role to the user
    """
    if role not in ALLOWED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role '{role}'. Allowed: {sorted(ALLOWED_ROLES)}"
        )

    # Map DVAS role to Keycloak role name
    kc_role_name = KC_ROLE_MAP[role]

    # Derive a username from email
    username = email.split("@")[0] if "@" in email else email

    # Split name into first/last
    name_parts = name.strip().split(" ", 1)
    first_name = name_parts[0]
    last_name = name_parts[1] if len(name_parts) > 1 else ""

    token = await get_service_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. Create the user
        user_body = {
            "username": username,
            "email": email,
            "firstName": first_name,
            "lastName": last_name,
            "enabled": True,
            "emailVerified": True,
            "attributes": {
                "jurisdiction": [jurisdiction] if jurisdiction else [],
                "mission_scope": mission_scope if mission_scope else [],
            },
            "credentials": [{
                "type": "password",
                "value": password,
                "temporary": False,
            }],
        }

        res = await client.post(
            f"{KEYCLOAK_INTERNAL_URL}/admin/realms/{REALM}/users",
            headers=headers,
            json=user_body,
        )
        if res.status_code == 409:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User with this email/username already exists in IAM"
            )
        if res.status_code not in (201, 200):
            logger.error(f"IAM user creation error: {res.status_code} {res.text}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"IAM user creation error: {res.text}"
            )

        # Extract user ID from Location header
        location = res.headers.get("Location", "")
        if location:
            user_id = location.split("/")[-1]
        else:
            # Fallback: fetch user by username
            search_res = await client.get(
                f"{KEYCLOAK_INTERNAL_URL}/admin/realms/{REALM}/users?username={username}&exact=true",
                headers=headers,
            )
            search_data = search_res.json()
            if not search_data:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="User created but could not retrieve user ID"
                )
            user_id = search_data[0]["id"]

        # 2. Fetch the target realm role object
        role_res = await client.get(
            f"{KEYCLOAK_INTERNAL_URL}/admin/realms/{REALM}/roles/{kc_role_name}",
            headers=headers,
        )
        if role_res.status_code != 200:
            logger.error(f"Role '{kc_role_name}' not found: {role_res.status_code}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Requested IAM role '{kc_role_name}' does not exist"
            )
        role_repr = [role_res.json()]

        # 3. Bind the role to the user
        bind_res = await client.post(
            f"{KEYCLOAK_INTERNAL_URL}/admin/realms/{REALM}/users/{user_id}/role-mappings/realm",
            headers=headers,
            json=role_repr,
        )
        if bind_res.status_code not in (200, 204):
            logger.error(f"Failed to bind role: {bind_res.status_code} {bind_res.text}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to bind role to user in IAM"
            )

        logger.info(f"Provisioned user '{username}' with role '{kc_role_name}' in Keycloak")

        return {
            "id": user_id,
            "email": email,
            "name": name,
            "role": role,
            "jurisdiction": jurisdiction,
            "mission_scope": mission_scope,
            "suspended": False,
            "created_at": "",
        }


async def disable_user_in_keycloak(user_id: str) -> None:
    """Disable (suspend) a user in Keycloak by setting enabled=false."""
    token = await get_service_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.put(
            f"{KEYCLOAK_INTERNAL_URL}/admin/realms/{REALM}/users/{user_id}",
            headers=headers,
            json={"enabled": False},
        )
        if res.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found in IAM"
            )
        if res.status_code not in (200, 204):
            logger.error(f"Failed to disable user: {res.status_code} {res.text}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to disable user in IAM"
            )
        logger.info(f"Disabled user '{user_id}' in Keycloak")



# ── Sessions (Admin → Token & Sessions, ADM-01) ──────────────────────────────
async def _admin_headers() -> dict:
    return {"Authorization": f"Bearer {await get_service_token()}"}


async def list_active_sessions() -> list[dict]:
    """Every active user session in the realm (view-users)."""
    headers = await _admin_headers()
    users = await list_users_from_keycloak()
    out = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for u in users:
            if u.get("suspended"):
                continue
            r = await client.get(f"{KEYCLOAK_INTERNAL_URL}/admin/realms/{REALM}/users/{u['id']}/sessions",
                                 headers=headers)
            if r.status_code != 200:
                continue
            for sess in r.json():
                out.append({
                    "session_id": sess.get("id"),
                    "user_id": u["id"], "email": u.get("email"), "name": u.get("name"), "role": u.get("role"),
                    "ip": sess.get("ipAddress"),
                    "started_ms": sess.get("start"), "last_access_ms": sess.get("lastAccess"),
                    "clients": sorted((sess.get("clients") or {}).values()),
                })
    return sorted(out, key=lambda s: s.get("last_access_ms") or 0, reverse=True)


async def revoke_session(session_id: str) -> None:
    """End one session (manage-users); its refresh token stops working at once."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.delete(f"{KEYCLOAK_INTERNAL_URL}/admin/realms/{REALM}/sessions/{session_id}",
                                headers=await _admin_headers())
    if r.status_code not in (200, 204):
        raise HTTPException(status_code=404 if r.status_code == 404 else 502,
                            detail="Session not found" if r.status_code == 404 else "IAM refused the revocation")


async def logout_user_everywhere(user_id: str) -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"{KEYCLOAK_INTERNAL_URL}/admin/realms/{REALM}/users/{user_id}/logout",
                              headers=await _admin_headers())
    if r.status_code not in (200, 204):
        raise HTTPException(status_code=502, detail="IAM refused the logout")


async def realm_token_settings() -> dict:
    """Token lifetimes as configured in Keycloak (view-realm). Read-only in DVAS."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{KEYCLOAK_INTERNAL_URL}/admin/realms/{REALM}", headers=await _admin_headers())
    if r.status_code != 200:
        return {}
    d = r.json()
    return {"access_token_lifespan_s": d.get("accessTokenLifespan"),
            "sso_session_idle_s": d.get("ssoSessionIdleTimeout"),
            "sso_session_max_s": d.get("ssoSessionMaxLifespan")}
