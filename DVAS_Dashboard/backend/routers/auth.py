import os
import logging
from fastapi import APIRouter, Depends, HTTPException, Request, Response
import httpx

from core.config import (
    EDGE_CLIENT_ORIGINS, KEYCLOAK_URL, KEYCLOAK_REALM, KEYCLOAK_CLIENT_ID, KEYCLOAK_CLIENT_SECRET, limiter
)
from core.security import (
    AuthError, get_current_user, require_roles, user_from_token, _row_to_dict
)
from audit_service import write_audit_v2
from keycloak_service import (
    list_users_from_keycloak, provision_user_in_keycloak, disable_user_in_keycloak
)
from schemas.auth import LoginIn, UserCreateIn, UserPatchIn

logger = logging.getLogger("dvas")
router = APIRouter(prefix="/api", tags=["Authentication & Users"])


def _token_in_body(request: Request) -> bool:
    """Only the edge app (another origin, no cookie) gets the JWT in JSON (H5)."""
    return request.headers.get("origin", "").rstrip("/") in EDGE_CLIENT_ORIGINS

@router.post("/auth/login")
@limiter.limit("5/minute")
async def login(body: LoginIn, response: Response, request: Request):
    email = body.email.lower()
    
    url = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"
    
    USERNAME_MAP = {
        "admin@dvas.kp": "admin_dvas",
        "control@dvas.kp": "control_001",
        "senior@dvas.kp": "senior_001",
        "operator@dvas.kp": "operator_001"
    }
    # Keycloak username from email mapping, or fallback to email itself
    username = USERNAME_MAP.get(email, email.split("@")[0] if "@" in email else email)
    
    data = {
        "client_id": KEYCLOAK_CLIENT_ID,
        "client_secret": KEYCLOAK_CLIENT_SECRET,
        "grant_type": "password",
        "username": username,
        "password": body.password
    }
    
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(url, data=data, timeout=5)
            if r.status_code != 200:
                raise HTTPException(status_code=401, detail="Invalid email or password")
            
            token_data = r.json()
            access_token = token_data["access_token"]
            
            try:
                user_dict = await user_from_token(access_token)
            except AuthError as exc:
                raise HTTPException(status_code=403 if "role" in str(exc) else 401, detail=str(exc))
            user_dict.pop("exp", None)

            is_secure = (request.url.scheme == "https" or os.environ.get("ENV") == "production" or os.environ.get("SECURE_COOKIES", "false").lower() == "true")
            response.set_cookie(
                "dvas_access_token",
                access_token,
                httponly=True,
                secure=is_secure,
                samesite="lax",
                max_age=token_data.get("expires_in", 3600),
                path="/"
            )
            if "refresh_token" in token_data:
                response.set_cookie(
                    "dvas_refresh_token",
                    token_data["refresh_token"],
                    httponly=True,
                    secure=is_secure,
                    samesite="lax",
                    max_age=token_data.get("refresh_expires_in", 86400),
                    path="/"
                )
            
            await write_audit_v2(
                actor=user_dict, action="login",
                target=email, target_type="user",
                target_id=user_dict.get("id"), target_name=email,
                details="login success via Keycloak",
                source_endpoint="POST /api/auth/login",
                request=request,
            )
            
            if _token_in_body(request):
                user_dict["access_token"] = access_token
            return user_dict
            
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="Authentication server unreachable")

@router.post("/auth/refresh")
@limiter.limit("30/minute")
async def refresh_token(request: Request, response: Response):
    """Silent token refresh using HttpOnly refresh_token cookie."""
    refresh_tok = request.cookies.get("dvas_refresh_token")
    if not refresh_tok:
        raise HTTPException(status_code=401, detail="No refresh token found")

    url = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"
    data = {
        "client_id": KEYCLOAK_CLIENT_ID,
        "client_secret": KEYCLOAK_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": refresh_tok,
    }
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(url, data=data, timeout=5)
            if r.status_code != 200:
                raise HTTPException(status_code=401, detail="Session expired, please login again")
            token_data = r.json()
            new_access_token = token_data["access_token"]
            
            try:
                user_dict = await user_from_token(new_access_token)
            except AuthError as exc:
                raise HTTPException(status_code=401, detail=str(exc))
            user_dict.pop("exp", None)

            is_secure = (request.url.scheme == "https" or os.environ.get("ENV") == "production" or os.environ.get("SECURE_COOKIES", "false").lower() == "true")
            response.set_cookie(
                "dvas_access_token",
                new_access_token,
                httponly=True,
                secure=is_secure,
                samesite="lax",
                max_age=token_data.get("expires_in", 3600),
                path="/"
            )
            if "refresh_token" in token_data:
                response.set_cookie(
                    "dvas_refresh_token",
                    token_data["refresh_token"],
                    httponly=True,
                    secure=is_secure,
                    samesite="lax",
                    max_age=token_data.get("refresh_expires_in", 86400),
                    path="/"
                )
            if _token_in_body(request):
                user_dict["access_token"] = new_access_token
            return user_dict
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="Authentication server unreachable")

@router.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return user


@router.post("/auth/logout")
async def logout(
    response: Response, user: dict = Depends(get_current_user), request: Request = None
):
    response.delete_cookie("dvas_access_token", path="/")
    response.delete_cookie("dvas_refresh_token", path="/")
    await write_audit_v2(
        actor=user, action="logout",
        target=user["email"], target_type="user",
        target_id=user.get("id"), target_name=user["email"],
        source_endpoint="POST /api/auth/logout",
        request=request,
    )
    return {"ok": True}


@router.get("/users")
async def list_users(user: dict = Depends(require_roles("system_administrator"))):
    return await list_users_from_keycloak()


@router.get("/audit/actors")
async def list_audit_actors(
    user: dict = Depends(require_roles("senior_officer", "system_administrator")),
):
    """
    Returns a lightweight list of Keycloak users for the Audit Log page's Actor dropdown.
    Accessible to senior_officer and system_administrator (unlike /users which is sys-admin only).
    System administrators receive the full list; senior officers receive users scoped to their jurisdiction.
    """
    all_users = await list_users_from_keycloak()
    if user["role"] == "system_administrator":
        # Sys admin sees all actors (for cross-system audit view)
        return [{"id": u["id"], "name": u["name"], "email": u["email"],
                 "role": u["role"], "jurisdiction": u.get("jurisdiction", "")} for u in all_users]
    else:
        # Senior Officer — filter to own jurisdiction
        return [{"id": u["id"], "name": u["name"], "email": u["email"],
                 "role": u["role"], "jurisdiction": u.get("jurisdiction", "")}
                for u in all_users if u.get("jurisdiction") == user.get("jurisdiction")]


@router.post("/users")
async def create_user(
    body: UserCreateIn, user: dict = Depends(require_roles("system_administrator")),
    request: Request = None,
):
    # Password complexity check (min 8 chars)
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    result = await provision_user_in_keycloak(
        name=body.name,
        email=body.email.lower(),
        role=body.role,
        jurisdiction=body.jurisdiction,
        mission_scope=body.mission_scope,
        password=body.password,
    )
    await write_audit_v2(
        actor=user,
        action="user_create",
        target=body.email.lower(),
        target_type="user",
        target_id=result.get("id") if isinstance(result, dict) else None,
        target_name=body.name,
        details=f"role={body.role}, jurisdiction={body.jurisdiction}",
        after_values={
            "name": body.name,
            "email": body.email.lower(),
            "role": body.role,
            "jurisdiction": body.jurisdiction,
            "mission_scope": body.mission_scope,
        },
        source_endpoint="POST /api/users",
        request=request,
    )
    return result


@router.patch("/users/{user_id}")
async def patch_user(
    user_id: str,
    body: UserPatchIn,
    user: dict = Depends(require_roles("system_administrator")),
):
    raise HTTPException(501, "User modification is managed via Keycloak Admin Console.")


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str, user: dict = Depends(require_roles("system_administrator")),
    request: Request = None,
):
    await disable_user_in_keycloak(user_id)
    await write_audit_v2(
        actor=user,
        action="user_suspend",
        target=user_id,
        target_type="user",
        target_id=user_id,
        details="disabled via Keycloak",
        after_values={"suspended": True},
        source_endpoint="DELETE /api/users/{user_id}",
        request=request,
    )
    return {"ok": True}
