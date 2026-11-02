"""
Signed download links (evidence plan E5): replace ?token=<JWT> in file URLs.

POST /api/downloads  {kind, id, role?}  → {url: /api/dl/<opaque>, expires_in: 60}
GET  /api/dl/<opaque>                   → the file, checked against the user who asked

Links are single-use, expire after 60 s and carry no credential. They are kept
in process memory: the backend runs one uvicorn worker (RunPod supervisord and
local compose); with several workers this store must move to Valkey.
Authorisation is re-checked at download time by the same code as the direct
endpoints, with the stored user.
"""
import secrets
import time
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from core.security import get_current_user

router = APIRouter(prefix="/api", tags=["Downloads"])

LINK_TTL_S = 60
_links: dict = {}           # opaque -> (expires_at, user, kind, id, role)


class DownloadIn(BaseModel):
    kind: Literal["bundle", "report", "evidence_object"]
    id: str = Field(..., min_length=1)
    role: Optional[str] = None


def _purge(now: float) -> None:
    for k in [k for k, v in _links.items() if v[0] < now]:
        _links.pop(k, None)


@router.post("/downloads")
async def create_download(body: DownloadIn, user: dict = Depends(get_current_user)):
    now = time.time()
    _purge(now)
    if body.kind == "evidence_object" and not body.role:
        raise HTTPException(422, "role is required for evidence_object")
    opaque = secrets.token_urlsafe(32)
    _links[opaque] = (now + LINK_TTL_S, dict(user), body.kind, body.id, body.role)
    return {"url": f"/api/dl/{opaque}", "expires_in": LINK_TTL_S}


@router.get("/dl/{opaque}")
async def use_download(opaque: str, request: Request):
    entry = _links.pop(opaque, None)          # single use
    if entry is None or entry[0] < time.time():
        raise HTTPException(404, "Download link expired or already used")
    _, user, kind, ident, role = entry
    if kind == "bundle":
        from routers.alerts import _stream_bundle
        return await _stream_bundle(ident, user, request)
    if kind == "report":
        from routers.reports import stream_report
        return await stream_report(ident, user, request)
    from routers.alerts import stream_evidence_object
    return await stream_evidence_object(ident, role, user, request)
