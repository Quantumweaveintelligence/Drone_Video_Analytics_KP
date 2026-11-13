"""
Live drone video (RunPod v2 plan R2).

The edge streams H.264 to ingest, which relays it into one of go2rtc's stream slots
(live01 … live32) while the mission streams. Browsers play it through nginx
`/live/api/ws?src=<slot>` (MSE over WebSocket); nginx asks `/api/live/authorize` first
(auth_request) on every connection.

  GET /api/live/drone/{drone_id}   which slot carries the drone's video now (404: none);
                                   ?sub=1 asks for the 720p grid sub-stream (liveNN_sub) — the
                                   player repeats the call while it plays, which keeps the
                                   transcode running
  GET /api/live/authorize          for nginx only: the user (session cookie, or ?token= for
                                   the edge app, like other WebSockets) may see the drone
                                   whose mission the slot carries at this moment

Slots are reused for later missions only after a cool-down longer than nginx's 30 s idle
timeout on the player socket, so a viewer is never switched onto another drone's video.
"""
import logging
import re
from urllib.parse import parse_qs, quote, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from core.security import AuthError, user_from_token
from ingest_proxy import _any_user, _check_drone_access, _proxy_get

logger = logging.getLogger("dvas")
router = APIRouter(prefix="/api/live", tags=["Live video"])

_SLOT_RE = re.compile(r"^live\d{2}(_sub)?$")


@router.get("/drone/{drone_id}")
async def live_drone(drone_id: str, sub: bool = False, user: dict = Depends(_any_user)):
    await _check_drone_access(drone_id, user)
    try:
        info = await _proxy_get(f"/ingest/video/live/{quote(drone_id, safe='')}{'?sub=1' if sub else ''}",
                                timeout=3.0)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(404, "No live video for this drone")
        raise
    src = info.get("src") or ""
    if not _SLOT_RE.match(src):
        raise HTTPException(404, "No live video for this drone")
    return {
        "drone_id": drone_id,
        "mission_id": info.get("mission_id"),
        "src": src,
        "ws_path": f"/live/api/ws?src={src}",
        "sub": bool(info.get("sub")),
        "sub_pending": bool(info.get("sub_pending")),   # asked for the sub-stream, not up yet (R9)
        "width": info.get("width"), "height": info.get("height"), "fps": info.get("fps"),
        "analytics_fps": info.get("analytics_fps"), "behind_live_s": info.get("behind_live_s"),
    }


@router.get("/authorize", include_in_schema=False)
async def live_authorize(request: Request):
    """204 when the player socket may open, else 401 / 403 (nginx auth_request)."""
    original = request.headers.get("X-Original-URI", "")
    query = parse_qs(urlsplit(original).query)
    src = (query.get("src") or [""])[0]
    if urlsplit(original).path != "/live/api/ws" or not _SLOT_RE.match(src):
        return Response(status_code=403)
    token = (query.get("token") or [""])[0] or request.cookies.get("dvas_access_token", "")
    if not token:
        return Response(status_code=401)
    try:
        user = await user_from_token(token)
    except AuthError:
        return Response(status_code=401)
    try:
        owner = await _proxy_get(f"/ingest/video/slot/{src}", timeout=3.0)
        await _check_drone_access(owner.get("drone_id") or "", user)
    except HTTPException as exc:
        if exc.status_code >= 500:
            logger.warning("live authorize: ingest lookup failed for %s: %s", src, exc.detail)
        return Response(status_code=403)
    return Response(status_code=204)
