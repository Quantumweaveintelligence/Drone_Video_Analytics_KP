import logging
from datetime import datetime, timezone
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.security import authenticate_ws
from services.websocket_hub import hub

logger = logging.getLogger("dvas")
router = APIRouter(tags=["WebSocket"])


@router.websocket("/api/ws")
async def ws_endpoint(websocket: WebSocket):
    """Live pushes. Authenticated by the session cookie (browser) or ?token= (edge app);
    no or bad token → closed with 4401 before anything is sent (C1)."""
    user = await authenticate_ws(websocket)
    if user is None:
        await websocket.close(code=4401)
        return
    await hub.connect(websocket, user)
    try:
        await websocket.send_json({"type": "welcome", "role": user["role"]})
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong", "ts": datetime.now(timezone.utc).isoformat()})
    except WebSocketDisconnect:
        hub.disconnect(websocket)
    except Exception:
        hub.disconnect(websocket)
