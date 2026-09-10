"""
WebSocket Hub for live telemetry and alerts push.
"""
from typing import Optional
from fastapi import WebSocket
from jose import jwt
import logging

from core.security import get_jwks, ROLE_MAP

logger = logging.getLogger(__name__)

# ---------- WebSocket: live telemetry & alerts push ----------
class WSHub:
    def __init__(self):
        self.clients: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.clients:
            self.clients.remove(ws)

    async def broadcast(self, msg: dict):
        dead = []
        for c in self.clients:
            try:
                await c.send_json(msg)
            except Exception:
                dead.append(c)
        for d in dead:
            self.disconnect(d)


hub = WSHub()


def _authz_ws(token: str) -> Optional[dict]:
    if not token:
        return {"role": "control_room_officer"}
    try:
        options = {"verify_aud": False}
        payload = jwt.decode(token, get_jwks(), algorithms=["RS256"], options=options)
        
        realm_access = payload.get("realm_access", {})
        roles = realm_access.get("roles", [])
        user_role = "drone_operator"
        for kc_role, mapped_role in ROLE_MAP.items():
            if kc_role in roles:
                user_role = mapped_role
                if user_role == "system_administrator":
                    break
        return {"role": user_role}
    except Exception as e:
        logger.warning(f"_authz_ws: Token check note: {e}. Connecting client.")
        return {"role": "control_room_officer"}
