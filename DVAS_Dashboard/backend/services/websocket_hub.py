"""
WebSocket hub for live pushes (telemetry, alerts, incidents, reports …).

Every connection is authenticated (routers/ws.py) and remembers its user. Each
message is filtered per user before sending (C1/F4): jurisdiction for control
room officers, mission scope for operators, audit events for seniors/admins
only, report messages for their requester. A connection whose token has expired
is closed with 4401 so the client refreshes and reconnects.
"""
import logging
import time
from typing import Dict, Optional

from fastapi import WebSocket

from core.security import STATEWIDE_ROLES, jurisdiction_visible

logger = logging.getLogger(__name__)

COMMAND_ROLES = ("control_room_officer", "senior_officer", "system_administrator")
# Message types an operator (edge) client may receive; everything else is command-side
OPERATOR_TYPES = {"welcome", "pong", "telemetry", "alert", "incident", "incident_ack", "incident_deleted",
                  "incidents_cleared", "model_suggestion", "model_suggestion_status",
                  "model_suggestion_deleted", "model_suggestions_cleared"}


def _mission_ok(mission_id, user: dict) -> bool:
    return bool(mission_id) and mission_id in (user.get("mission_scope") or [])


def filter_for(msg: dict, user: dict) -> Optional[dict]:
    """The message as this user may see it, or None."""
    role = user.get("role")
    mtype = msg.get("type") or msg.get("event") or ""
    if role == "drone_operator" and mtype not in OPERATOR_TYPES:
        return None
    if mtype.startswith("audit"):
        return msg if role in STATEWIDE_ROLES else None
    if mtype in ("report_ready", "report_failed"):
        return msg if role in STATEWIDE_ROLES or msg.get("requested_by") == user.get("email") else None
    if mtype == "alert":
        a = msg.get("alert") or {}
        if role == "drone_operator":
            return msg if _mission_ok(a.get("mission_id"), user) else None
        return msg if jurisdiction_visible(a.get("jurisdiction"), user) else None
    if mtype == "evidence_created":
        return msg if jurisdiction_visible(msg.get("jurisdiction"), user) else None
    if mtype == "telemetry" and isinstance(msg.get("drones"), list):
        if role == "drone_operator":
            drones = [d for d in msg["drones"] if _mission_ok(d.get("mission_id"), user)
                      or d.get("operator_id") == user.get("id")]
        else:
            drones = [d for d in msg["drones"] if jurisdiction_visible(d.get("jurisdiction"), user)]
        return dict(msg, drones=drones) if drones else None
    return msg


class WSHub:
    def __init__(self):
        self.clients: Dict[WebSocket, dict] = {}

    async def connect(self, ws: WebSocket, user: dict):
        await ws.accept()
        self.clients[ws] = user

    def disconnect(self, ws: WebSocket):
        self.clients.pop(ws, None)

    async def broadcast(self, msg: dict):
        now = time.time()
        dead = []
        for ws, user in list(self.clients.items()):
            if user.get("exp") and user["exp"] < now:
                dead.append(ws)
                try:
                    await ws.close(code=4401)          # token expired: client refreshes and reconnects
                except Exception:
                    pass
                continue
            out = filter_for(msg, user)
            if out is None:
                continue
            try:
                await ws.send_json(out)
            except Exception:
                dead.append(ws)
        for d in dead:
            self.disconnect(d)


hub = WSHub()
