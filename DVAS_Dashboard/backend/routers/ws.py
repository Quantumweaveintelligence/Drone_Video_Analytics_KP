import logging
from datetime import datetime, timezone
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services.websocket_hub import hub, _authz_ws

logger = logging.getLogger("dvas")
router = APIRouter(tags=["WebSocket"])

@router.websocket("/api/ws")
async def ws_endpoint(websocket: WebSocket, token: str = ""):
    raw_token = token or websocket.cookies.get("dvas_access_token", "")
    payload = _authz_ws(raw_token)
    if not payload:
        await websocket.close(code=4401)
        return
    await hub.connect(websocket)
    try:
        await websocket.send_json({"type": "welcome", "role": payload.get("role")})
        while True:
            # keep-alive; clients can send pings
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json(
                    {"type": "pong", "ts": datetime.now(timezone.utc).isoformat()}
                )
    except WebSocketDisconnect:
        hub.disconnect(websocket)
    except Exception:
        hub.disconnect(websocket)
