import asyncio
import logging
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from database import engine
from models import Mission
from core.security import get_current_user, _row_to_dict
from core.analytics_client import _analytics_get, _analytics_post, _analytics_put, _analytics_delete, _service_headers
from core.config import (
    ANALYTICS_BASE, CROWD_URL, PERSON_URL, VEHICLE_URL, ANPR_URL, INTRUSION_URL,
)
from audit_service import write_audit_v2
from schemas.models import WatchlistAddIn

logger = logging.getLogger("dvas")
router = APIRouter(prefix="/api", tags=["Analytics"])

# ── Analytics Health ──────────────────────────────────────────────────────────
@router.get("/analytics/health")
async def analytics_health(user: dict = Depends(get_current_user)):
    """Aggregated health check across all 4 analytics services."""
    return await _analytics_get(f"{ANALYTICS_BASE}/health/all")


@router.get("/analytics/summary")
async def analytics_summary(user: dict = Depends(get_current_user)):
    """Unified stats snapshot from gateway."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{ANALYTICS_BASE}/summary")


@router.get("/analytics/models/status")
async def analytics_models_status(user: dict = Depends(get_current_user)):
    """Real model status from each analytics service health endpoint."""
    import asyncio as _asyncio
    results = await _asyncio.gather(
        _analytics_get(f"{CROWD_URL}/health"),
        _analytics_get(f"{PERSON_URL}/health"),
        _analytics_get(f"{VEHICLE_URL}/health"),
        _analytics_get(f"{ANPR_URL}/health"),
        _analytics_get(f"{INTRUSION_URL}/health"),
    )
    return {
        "crowd":   results[0],
        "person":  results[1],
        "vehicle": results[2],
        "anpr":    results[3],
        "intrusion": results[4],
    }


# ── SSE Stream proxy ──────────────────────────────────────────────────────────
@router.get("/analytics/events/stream")
async def analytics_events_stream(request: Request, drone_id: str = ""):
    """Proxy the gateway SSE stream to the browser with auth check."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")

    upstream_url = f"{ANALYTICS_BASE}/events/stream"
    if drone_id:
        upstream_url += f"?drone_id={drone_id}"

    async def event_generator():
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream("GET", upstream_url, timeout=None, headers=_service_headers()) as resp:
                    async for line in resp.aiter_lines():
                        if await request.is_disconnected():
                            break
                        yield line + "\n"
        except Exception as exc:
            logger.warning("SSE proxy error: %s", exc)
            yield f"data: {{\"error\": \"{exc}\"}}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Crowd Analytics ───────────────────────────────────────────────────────────
@router.get("/analytics/crowd")
async def crowd_analytics(user: dict = Depends(get_current_user)):
    """Crowd density timeline — proxied from crowd-analyzer with fallback."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    result = await _analytics_get(f"{CROWD_URL}/density/timeline")
    if "error" in result:
        # Fallback: synthesized data when service is offline
        base = [120,130,90,60,40,30,45,90,180,260,320,400,380,350,300,340,420,500,460,380,300,240,190,150]
        data = [{"hour": f"{h:02d}:00", "density": base[h], "vehicles": max(0, base[h] // 4)} for h in range(24)]
        return {"series": data, "source": "fallback"}
    return result


@router.get("/analytics/crowd/readings")
async def crowd_readings(user: dict = Depends(get_current_user), drone_id: str = "", limit: int = 50):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{CROWD_URL}/readings/history?limit={limit}"
    if drone_id:
        url += f"&drone_id={drone_id}"
    return await _analytics_get(url)


@router.get("/analytics/crowd/timeline")
async def crowd_timeline(user: dict = Depends(get_current_user), drone_id: str = "", hours: int = 24):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{CROWD_URL}/density/timeline?hours={hours}"
    if drone_id:
        url += f"&drone_id={drone_id}"
    return await _analytics_get(url)


@router.get("/analytics/crowd/heatmap")
async def crowd_heatmap(user: dict = Depends(get_current_user), drone_id: str = ""):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{CROWD_URL}/density/heatmap/latest"
    if drone_id:
        url += f"?drone_id={drone_id}"
    return await _analytics_get(url)


@router.get("/analytics/crowd/health")
async def crowd_health(user: dict = Depends(get_current_user)):
    return await _analytics_get(f"{CROWD_URL}/health")


@router.get("/analytics/crowd/annotated")
async def crowd_annotated_frame(user: dict = Depends(get_current_user), drone_id: str = ""):
    """
    Proxy the latest heatmap-overlaid JPEG frame from crowd-analyzer.
    Used by the Crowd Analytics live feed. Returns binary JPEG — NOT JSON.
    """
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    try:
        params = {"drone_id": drone_id} if drone_id else {}
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(f"{CROWD_URL}/annotated/latest", params=params)
        if r.status_code == 200:
            headers = {
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma":        "no-cache",
                "Access-Control-Expose-Headers": "X-Crowd-Count, X-Density-Label, X-Drone-ID, X-Frame-Age",
            }
            for h in ["X-Drone-ID", "X-Frame-Age", "X-Crowd-Count", "X-Density-Label"]:
                if h in r.headers:
                    headers[h] = r.headers[h]
            return Response(
                content=r.content,
                media_type="image/jpeg",
                headers=headers,
            )
        raise HTTPException(r.status_code, "No live annotated frame available")
    except httpx.RequestError:
        raise HTTPException(503, "Crowd analyzer unavailable")


@router.put("/analytics/crowd/model")
async def crowd_switch_model(request: Request, user: dict = Depends(get_current_user)):
    """Switch crowd model between CSRNet Part A (dense) and Part B (sparse)."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    body = await request.json()
    result = await _analytics_put(f"{CROWD_URL}/model/switch", body)
    if "error" in result:
        # Fallback to post if put had an issue
        result = await _analytics_post(f"{CROWD_URL}/model/switch", body)
    if "error" in result:
        raise HTTPException(502, detail=result.get("detail", "Failed to switch crowd model upstream"))

    await write_audit_v2(
        actor=user,
        action="crowd_model_switch",
        target=body.get("model_type", "unknown"),
        target_type="configuration",
        target_name=f"Crowd Model ({body.get('model_type', 'unknown')})",
        details=f"Switched crowd model to {body.get('model_type')}",
        source_endpoint="PUT /api/analytics/crowd/model",
        request=request,
    )
    return result


@router.get("/analytics/crowd/model")
async def crowd_current_model(user: dict = Depends(get_current_user)):
    """Get current active crowd model info."""
    return await _analytics_get(f"{CROWD_URL}/model/current")


@router.get("/analytics/crowd/session/summary")
async def crowd_session_summary(user: dict = Depends(get_current_user)):
    """Get session statistics: mean, min, max, std_dev, peak, density distribution."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{CROWD_URL}/session/summary")


@router.get("/analytics/crowd/export/csv")
async def crowd_export_csv(user: dict = Depends(get_current_user)):
    """Download crowd analytics session data as CSV."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(f"{CROWD_URL}/export/csv")
        if r.status_code == 200:
            return Response(
                content=r.content,
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=crowd_report.csv"},
            )
        raise HTTPException(r.status_code, "CSV export failed")
    except httpx.RequestError:
        raise HTTPException(503, "Crowd analyzer unavailable")


@router.get("/analytics/crowd/export/pdf")
async def crowd_export_pdf(
    user: dict = Depends(get_current_user),
    operator: str = "",
    location: str = "",
    mission_id: str = "",
):
    """Download crowd analytics mission report as PDF."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    try:
        params = {k: v for k, v in {"operator": operator, "location": location, "mission_id": mission_id}.items() if v}
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(f"{CROWD_URL}/export/pdf", params=params)
        if r.status_code == 200:
            return Response(
                content=r.content,
                media_type="application/pdf",
                headers={"Content-Disposition": f"attachment; filename=crowd_mission_report_{mission_id or 'export'}.pdf"},
            )
        raise HTTPException(r.status_code, "PDF export failed")
    except httpx.RequestError:
        raise HTTPException(503, "Crowd analyzer unavailable")


# ── Person Analytics ──────────────────────────────────────────────────────────
@router.get("/analytics/person/identities")
async def person_identities(user: dict = Depends(get_current_user), drone_id: str = "", mission_id: str = "", limit: int = 50):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{PERSON_URL}/persons/identities?limit={limit}"
    if drone_id:
        url += f"&drone_id={drone_id}"
    if mission_id:
        url += f"&mission_id={mission_id}"
    return await _analytics_get(url)


@router.get("/analytics/person/gallery")
async def person_gallery(user: dict = Depends(get_current_user), drone_id: str = "", mission_id: str = "", limit: int = 60):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{PERSON_URL}/faces/gallery?limit={limit}"
    if drone_id:
        url += f"&drone_id={drone_id}"
    if mission_id:
        url += f"&mission_id={mission_id}"
    return await _analytics_get(url)


@router.get("/analytics/person/count")
async def person_count(user: dict = Depends(get_current_user), drone_id: str = ""):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{PERSON_URL}/persons/count"
    if drone_id:
        url += f"?drone_id={drone_id}"
    return await _analytics_get(url)


@router.get("/analytics/person/tracks")
async def person_tracks(user: dict = Depends(get_current_user), drone_id: str = "", limit: int = 100):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{PERSON_URL}/tracks/active?limit={limit}"
    if drone_id:
        url += f"&drone_id={drone_id}"
    return await _analytics_get(url)


@router.get("/analytics/person/dedup")
async def person_dedup(user: dict = Depends(get_current_user)):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{PERSON_URL}/persons/deduplicate")


@router.get("/analytics/person/detections")
async def person_detections(user: dict = Depends(get_current_user), drone_id: str = "", limit: int = 50):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{PERSON_URL}/detections/recent?limit={limit}"
    if drone_id:
        url += f"&drone_id={drone_id}"
    return await _analytics_get(url)


@router.post("/analytics/person/reid")
async def person_reid_trigger(user: dict = Depends(get_current_user)):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_post(f"{PERSON_URL}/reid/trigger", {})


@router.post("/analytics/person/deduplicate")
async def person_deduplicate_mission(user: dict = Depends(get_current_user), body: dict = {}):
    """Trigger mission-scoped person deduplication."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_post(f"{PERSON_URL}/persons/deduplicate/mission", body)


@router.get("/analytics/person/dedup/progress")
async def person_dedup_progress(user: dict = Depends(get_current_user), mission_id: str = ""):
    """Get current mission deduplication progress."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{PERSON_URL}/persons/deduplicate/progress"
    if mission_id:
        url += f"?mission_id={mission_id}"
    return await _analytics_get(url)


@router.get("/analytics/person/missions/dedup")
async def person_missions_dedup(user: dict = Depends(get_current_user)):
    """List missions with their face embedding counts and dedup status."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{PERSON_URL}/missions/dedup-status")


@router.get("/analytics/person/missions/dedup-runs")
async def person_missions_dedup_runs(user: dict = Depends(get_current_user)):
    """List all mission deduplication run history records."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{PERSON_URL}/missions/dedup-runs")


# ── Analytics Service Health Proxies ─────────────────────────────────────────
@router.get("/analytics/vehicle/health")
async def vehicle_health(user: dict = Depends(get_current_user)):
    """Proxy vehicle-analyzer health including stream_running status."""
    return await _analytics_get(f"{VEHICLE_URL}/health")


@router.get("/analytics/anpr/health")
async def anpr_health(user: dict = Depends(get_current_user)):
    """Proxy anpr-analyzer health including stream_running status."""
    return await _analytics_get(f"{ANPR_URL}/health")


# ── Analytics Stream Start/Stop Proxies ──────────────────────────────────────
@router.get("/analytics/vehicle/stream/start")
async def vehicle_stream_start(user: dict = Depends(get_current_user)):
    """Start the vehicle detection stream loop."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{VEHICLE_URL}/stream/start")


@router.get("/analytics/vehicle/stream/stop")
async def vehicle_stream_stop(user: dict = Depends(get_current_user)):
    """Stop the vehicle detection stream loop."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{VEHICLE_URL}/stream/stop")


@router.get("/analytics/anpr/stream/start")
async def anpr_stream_start(user: dict = Depends(get_current_user)):
    """Start the ANPR detection stream loop."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{ANPR_URL}/stream/start")


@router.get("/analytics/anpr/stream/stop")
async def anpr_stream_stop(user: dict = Depends(get_current_user)):
    """Stop the ANPR detection stream loop."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{ANPR_URL}/stream/stop")


@router.get("/analytics/crowd/stream/start")
async def crowd_stream_start(user: dict = Depends(get_current_user)):
    """Start the crowd analysis stream loop."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{CROWD_URL}/stream/start")


@router.get("/analytics/crowd/stream/stop")
async def crowd_stream_stop(user: dict = Depends(get_current_user)):
    """Stop the crowd analysis stream loop."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{CROWD_URL}/stream/stop")


@router.get("/analytics/person/stream/start")
async def person_stream_start(user: dict = Depends(get_current_user)):
    """Start the person detection stream loop."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{PERSON_URL}/stream/start")


@router.get("/analytics/person/health")
async def person_health_proxy(user: dict = Depends(get_current_user)):
    """Proxy person-analyzer health including stream_running status."""
    return await _analytics_get(f"{PERSON_URL}/health")


@router.get("/analytics/person/stream/stop")
async def person_stream_stop(user: dict = Depends(get_current_user)):
    """Stop the person detection stream loop."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{PERSON_URL}/stream/stop")


@router.get("/analytics/person/annotated")
async def person_annotated_frame(user: dict = Depends(get_current_user), drone_id: str = ""):
    """
    Proxy the latest annotated JPEG frame (person + face bounding boxes)
    from person-analyzer. Used by the Person Tracking live feed.
    Returns binary JPEG — NOT JSON.
    """
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    try:
        params = {"drone_id": drone_id} if drone_id else {}
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(f"{PERSON_URL}/annotated/latest", params=params)
        if r.status_code == 200:
            headers = {
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma":        "no-cache",
            }
            for h in ["X-Drone-ID", "X-Frame-Age", "X-Mission-ID"]:
                if h in r.headers:
                    headers[h] = r.headers[h]
            return Response(
                content=r.content,
                media_type="image/jpeg",
                headers=headers,
            )
        raise HTTPException(r.status_code, "No live annotated frame available")
    except httpx.RequestError:
        raise HTTPException(503, "Person analyzer unavailable")

@router.get("/analytics/person/live")
async def person_live_buffer(user: dict = Depends(get_current_user), drone_id: str = ""):
    """Proxy in-memory live person buffer (crop pairs) from person-analyzer."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{PERSON_URL}/persons/live"
    if drone_id:
        url += f"?drone_id={drone_id}"
    return await _analytics_get(url)


# ── Vehicle Analytics ─────────────────────────────────────────────────────────

@router.get("/analytics/vehicle/tracks")
async def vehicle_tracks(user: dict = Depends(get_current_user), drone_id: str = "", limit: int = 100):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{VEHICLE_URL}/tracks/active?limit={limit}"
    if drone_id:
        url += f"&drone_id={drone_id}"
    return await _analytics_get(url)


@router.get("/analytics/vehicle/recent")
async def vehicle_recent(user: dict = Depends(get_current_user), drone_id: str = "", limit: int = 50):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{VEHICLE_URL}/vehicles/recent?limit={limit}"
    if drone_id:
        url += f"&drone_id={drone_id}"
    return await _analytics_get(url)


@router.get("/analytics/vehicle/stats")
async def vehicle_stats(user: dict = Depends(get_current_user)):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{VEHICLE_URL}/stats/summary")


@router.get("/analytics/vehicle/stats/flow")
async def vehicle_stats_flow(user: dict = Depends(get_current_user)):
    """24-hour vehicle flow time series broken down by type."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{VEHICLE_URL}/stats/flow/24h")


@router.get("/analytics/vehicle/by-colour")
async def vehicle_by_colour(user: dict = Depends(get_current_user)):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{VEHICLE_URL}/stats/by-colour")


@router.get("/analytics/vehicle/{tracker_id}/plate")
async def vehicle_plate(tracker_id: str, user: dict = Depends(get_current_user), drone_id: str = ""):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{VEHICLE_URL}/vehicles/{tracker_id}/plate"
    if drone_id:
        url += f"?drone_id={drone_id}"
    return await _analytics_get(url)


@router.get("/analytics/vehicle/{tracker_id}/image")
async def vehicle_image_url(tracker_id: str, user: dict = Depends(get_current_user), drone_id: str = ""):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{VEHICLE_URL}/vehicles/{tracker_id}/image"
    if drone_id:
        url += f"?drone_id={drone_id}"
    return await _analytics_get(url)


@router.get("/analytics/vehicle/annotated")
async def vehicle_annotated_frame(user: dict = Depends(get_current_user), drone_id: str = ""):
    """
    Proxy the latest annotated JPEG frame (vehicle bounding boxes + labels)
    from vehicle-analyzer. Used by the dashboard live feed window.
    Returns binary JPEG — NOT JSON.
    """
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    try:
        params = {"drone_id": drone_id} if drone_id else {}
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{VEHICLE_URL}/annotated/latest", params=params)
        if r.status_code == 200:
            headers = {
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma":        "no-cache",
            }
            for h in ["X-Drone-ID", "X-Frame-Age", "X-Mission-ID"]:
                if h in r.headers:
                    headers[h] = r.headers[h]
            return Response(
                content=r.content,
                media_type="image/jpeg",
                headers=headers,
            )
        raise HTTPException(r.status_code, "No live annotated frame available")
    except httpx.RequestError:
        raise HTTPException(503, "Vehicle analyzer unavailable")


@router.get("/analytics/anpr/annotated")
async def anpr_annotated_frame(user: dict = Depends(get_current_user), drone_id: str = ""):
    """
    Proxy the latest annotated JPEG frame (plate bboxes + OCR text)
    from anpr-analyzer. Used by the ANPR window in the dashboard.
    Returns binary JPEG — NOT JSON.
    """
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    try:
        params = {"drone_id": drone_id} if drone_id else {}
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{ANPR_URL}/annotated/latest", params=params)
        if r.status_code == 200:
            headers = {
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma":        "no-cache",
            }
            for h in ["X-Drone-ID", "X-Frame-Age", "X-Mission-ID"]:
                if h in r.headers:
                    headers[h] = r.headers[h]
            return Response(
                content=r.content,
                media_type="image/jpeg",
                headers=headers,
            )
        raise HTTPException(r.status_code, "No live annotated frame available")
    except httpx.RequestError:
        raise HTTPException(503, "ANPR analyzer unavailable")


# ── ANPR Analytics ────────────────────────────────────────────────────────────
@router.get("/analytics/plates/recent")
async def plates_recent(
    user: dict = Depends(get_current_user),
    drone_id: str = "",
    mission_id: str = "",
    limit: int = 50,
):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{ANPR_URL}/plates/recent?limit={limit}"
    if mission_id:
        url += f"&mission_id={mission_id}"
    elif drone_id:
        url += f"&drone_id={drone_id}"
    return await _analytics_get(url)


@router.get("/analytics/plates/live")
async def plates_live(
    user: dict = Depends(get_current_user),
    drone_id: str = "",
    mission_id: str = "",
    limit: int = 50,
):
    """In-memory live plate detections from the stream loop — no DB/MinIO required."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    url = f"{ANPR_URL}/plates/live?limit={limit}"
    if mission_id:
        url += f"&mission_id={mission_id}"
    elif drone_id:
        url += f"&drone_id={drone_id}"
    return await _analytics_get(url)


@router.get("/analytics/plates/{plate_text}")
async def plate_lookup(plate_text: str, user: dict = Depends(get_current_user)):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{ANPR_URL}/plates/{plate_text}")


@router.get("/analytics/plates/{plate_text}/image")
async def plate_image_url(plate_text: str, user: dict = Depends(get_current_user)):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{ANPR_URL}/plates/{plate_text}/image")


@router.get("/analytics/watchlist")
async def watchlist_get(user: dict = Depends(get_current_user)):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{ANPR_URL}/watchlist")


@router.post("/analytics/watchlist")
async def watchlist_add(
    body: WatchlistAddIn,
    user: dict = Depends(get_current_user),
    request: Request = None,
):
    if user["role"] not in ("control_room_officer", "senior_officer"):
        raise HTTPException(403, "Not permitted")
    result = await _analytics_post(f"{ANPR_URL}/watchlist/add", {
        "plate_text": body.plate,
        "reason":     body.reason,
    })
    await write_audit_v2(
        actor=user,
        action="watchlist_add",
        target=body.plate,
        target_type="watchlist",
        target_name=body.plate,
        details=body.reason,
        after_values={"plate": body.plate, "reason": body.reason, "vehicle_type": body.vehicle_type},
        source_endpoint="POST /api/analytics/watchlist",
        request=request,
    )
    return result


@router.delete("/analytics/watchlist/{plate}")
async def watchlist_remove(
    plate: str,
    user: dict = Depends(get_current_user),
    request: Request = None,
):
    if user["role"] not in ("control_room_officer", "senior_officer"):
        raise HTTPException(403, "Not permitted")
    result = await _analytics_delete(f"{ANPR_URL}/watchlist/{plate}")
    await write_audit_v2(
        actor=user,
        action="watchlist_remove",
        target=plate,
        target_type="watchlist",
        target_name=plate,
        details="",
        before_values={"plate": plate},
        source_endpoint="DELETE /api/analytics/watchlist/{plate}",
        request=request,
    )
    return result


@router.get("/analytics/alerts/theft")
async def theft_alerts(user: dict = Depends(get_current_user), limit: int = 20):
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{ANPR_URL}/alerts/theft?limit={limit}")


# ── Mission Vehicle History ──────────────────────────────────────────────────
@router.get("/analytics/mission/{mission_id}/vehicles")
async def mission_vehicles(
    mission_id: str,
    user: dict = Depends(get_current_user),
    plate_search: str = "",
    vehicle_type: str = "",
    sort: str = "newest",
    limit: int = 24,
    offset: int = 0,
):
    """
    Retrieve vehicle/plate detection history for a specific mission.
    Passes mission_id directly to the ANPR service for accurate per-mission filtering.
    """
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")

    # Look up mission from dashboard DB — soft lookup, not required
    mission_name = mission_id
    drone_id = ""
    started_at = ""
    ended_at = ""
    status = "unknown"
    async with AsyncSession(engine) as session:
        mission = await session.get(Mission, mission_id)
        if mission:
            mission_name = mission.name or mission_id
            drone_id = mission.drone_id or ""
            started_at = mission.started_at or ""
            ended_at = mission.ended_at or ""
            status = mission.status or "unknown"

    # Build query string for ANPR service — use mission_id as primary filter
    from urllib.parse import quote
    qs_parts = [
        f"mission_id={quote(mission_id)}",
        f"limit={min(limit, 200)}",
        f"offset={offset}",
    ]
    if plate_search:
        qs_parts.append(f"plate_search={quote(plate_search)}")
    if vehicle_type and vehicle_type != "all":
        qs_parts.append(f"vehicle_type={quote(vehicle_type)}")
    if sort:
        qs_parts.append(f"sort={sort}")

    url = f"{ANPR_URL}/plates/by-mission?{'&'.join(qs_parts)}"
    result = await _analytics_get(url, timeout=15.0)

    # Enrich with mission metadata
    if isinstance(result, dict) and "error" not in result:
        result["mission_id"] = mission_id
        result["mission_name"] = mission_name
        result["drone_id"] = drone_id
        result["status"] = status
        result["time_range"] = {
            "start": started_at,
            "end": ended_at,
        }
        # Compute duration
        if started_at and ended_at:
            try:
                from datetime import datetime as _dt
                s = _dt.fromisoformat(started_at.replace("Z", "+00:00"))
                e = _dt.fromisoformat(ended_at.replace("Z", "+00:00"))
                dur_s = int((e - s).total_seconds())
                h, m = divmod(dur_s // 60, 60)
                result["duration"] = f"{h}h {m}m" if h > 0 else f"{m}m"
            except Exception:
                result["duration"] = None
                
        # The ANPR service returns `plates`, but the frontend expects `vehicles`
        if "plates" in result:
            result["vehicles"] = result.pop("plates")
            
    return result


@router.get("/analytics/mission/{mission_id}/stats")
async def mission_stats(
    mission_id: str,
    user: dict = Depends(get_current_user),
):
    """Quick summary stats for a mission — total vehicles, plates, etc."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")

    async with AsyncSession(engine) as session:
        mission = await session.get(Mission, mission_id)
        if not mission:
            raise HTTPException(404, f"Mission {mission_id} not found")

    drone_id = mission.drone_id or ""

    from urllib.parse import quote
    url = f"{ANPR_URL}/plates/by-mission?mission_id={quote(mission_id)}&limit=1&offset=0"
    result = await _analytics_get(url, timeout=15.0)

    return {
        "mission_id": mission_id,
        "mission_name": mission.name,
        "drone_id": drone_id,
        "total_vehicles": result.get("total", 0) if isinstance(result, dict) else 0,
        "total_plates_read": result.get("total_plates_read", 0) if isinstance(result, dict) else 0,
    }


# ── Intrusion Detection ───────────────────────────────────────────────

@router.get("/analytics/intrusion/health")
async def intrusion_health(user: dict = Depends(get_current_user)):
    """Intrusion analyzer service health."""
    return await _analytics_get(f"{INTRUSION_URL}/health")


@router.get("/analytics/intrusion/live")
async def intrusion_live(
    drone_id: str = "",
    user: dict = Depends(get_current_user),
):
    """Live intrusion tracks and states for a drone."""
    return await _analytics_get(
        f"{INTRUSION_URL}/intrusion/live?drone_id={drone_id}"
    )


@router.get("/analytics/intrusion/annotated")
async def intrusion_annotated(
    drone_id: str = "",
    user: dict = Depends(get_current_user),
):
    """Latest annotated JPEG frame with geofence + bounding boxes (binary proxy)."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{INTRUSION_URL}/intrusion/annotated/latest",
                params={"drone_id": drone_id},
                timeout=8.0,
                headers=_service_headers(),
            )
            if r.status_code == 200:
                return Response(
                    content=r.content,
                    media_type="image/jpeg",
                    headers={
                        "Cache-Control": "no-cache, no-store, must-revalidate",
                        "X-Drone-ID": r.headers.get("X-Drone-ID", drone_id),
                        "X-Frame-Age": r.headers.get("X-Frame-Age", ""),
                    },
                )
            raise HTTPException(r.status_code, r.text[:200])
    except httpx.ConnectError:
        raise HTTPException(503, "Intrusion analyzer unreachable")
    except httpx.TimeoutException:
        raise HTTPException(504, "Intrusion analyzer timeout")


@router.get("/analytics/intrusion/zone/status")
async def intrusion_zone_status(
    drone_id: str = "",
    user: dict = Depends(get_current_user),
):
    """Active geofence zone status."""
    return await _analytics_get(
        f"{INTRUSION_URL}/intrusion/zone/status?drone_id={drone_id}"
    )


@router.get("/analytics/intrusion/events")
async def intrusion_events(
    drone_id: str = "",
    mission_id: str = "",
    limit: int = 50,
    user: dict = Depends(get_current_user),
):
    """Historical intrusion events for post-mission review."""
    return await _analytics_get(
        f"{INTRUSION_URL}/intrusion/events?"
        f"drone_id={drone_id}&mission_id={mission_id}&limit={limit}"
    )


@router.get("/analytics/intrusion/analytics")
async def intrusion_analytics(
    mission_id: str,
    user: dict = Depends(get_current_user),
):
    """Full analytics JSON for a completed mission."""
    return await _analytics_get(
        f"{INTRUSION_URL}/intrusion/analytics?mission_id={mission_id}"
    )


@router.post("/analytics/intrusion/config")
async def intrusion_config_update(
    request: Request,
    drone_id: str = "",
    user: dict = Depends(get_current_user),
):
    """Update intrusion detection config (senior_officer+ only)."""
    if user["role"] not in ("senior_officer", "system_administrator"):
        raise HTTPException(403, "Config changes require elevated role")
    body = await request.json()
    result = await _analytics_post(
        f"{INTRUSION_URL}/intrusion/config?drone_id={drone_id}", body
    )
    await write_audit_v2(
        actor=user,
        action="intrusion_config_update",
        action_type="configuration",
        category="analytics",
        severity="medium",
        target="intrusion_config",
        target_type="intrusion_config",
        details=str(body),
        source_module="dashboard",
        source_endpoint="/api/analytics/intrusion/config",
        request=request,
    )
    return result


@router.get("/analytics/intrusion/stream/start")
async def intrusion_stream_start(user: dict = Depends(get_current_user)):
    """Start intrusion detection stream."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{INTRUSION_URL}/stream/start")


@router.get("/analytics/intrusion/stream/stop")
async def intrusion_stream_stop(user: dict = Depends(get_current_user)):
    """Stop intrusion detection stream."""
    if user["role"] == "drone_operator":
        raise HTTPException(403, "Not permitted")
    return await _analytics_get(f"{INTRUSION_URL}/stream/stop")


# ── AI Model Suggestions from Edge to Ingest / GPU Dashboard ─────────────────

async def _forward_suggestion_to_ingest(suggestion_dict: dict) -> bool:
    try:
        from ingest_proxy import _get_service_token, INGEST_SERVER_URL
        token = await _get_service_token()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        url = f"{INGEST_SERVER_URL}/ingest/model-suggestion"
        async with httpx.AsyncClient(timeout=3.5) as client:
            resp = await client.post(url, json=suggestion_dict, headers=headers)
            return resp.is_success
    except Exception as exc:
        logger.warning(f"Could not forward model suggestion to Ingest Server: {exc}")
        return False


@router.get("/analytics/suggestions")
async def list_model_suggestions(
    limit: int = 50,
    user: dict = Depends(get_current_user)
):
    """List operator AI model suggestions ordered by newest first."""
    from database import async_session_factory
    from models import AIModelSuggestion
    from sqlalchemy import select, desc
    async with async_session_factory() as session:
        stmt = select(AIModelSuggestion).order_by(desc(AIModelSuggestion.timestamp)).limit(limit)
        res = await session.execute(stmt)
        return [_row_to_dict(r) for r in res.scalars().all()]


@router.post("/analytics/suggestions")
async def create_model_suggestion(
    payload: dict,
    user: dict = Depends(get_current_user),
    request: Request = None,
):
    """
    Edge operator suggests analytical AI models (Crowd, Person, Intrusion, ANPR, Vehicle).
    Persists suggestion, relays to Ingest Server, and broadcasts live to GPU dashboard.
    """
    import uuid
    from datetime import datetime, timezone
    from database import async_session_factory
    from models import AIModelSuggestion
    from services.websocket_hub import hub

    models = payload.get("models", [])
    if not models:
        raise HTTPException(400, "At least one AI model must be selected")

    model_names = payload.get("model_names", [m.replace('_', ' ').title() for m in models])
    priority = payload.get("priority", "normal").lower()
    reason = payload.get("reason", "").strip()
    location = payload.get("location") or payload.get("place") or "Edge Ground Station"
    ts = payload.get("timestamp") or datetime.now(timezone.utc).isoformat()
    drone_id = payload.get("drone_id")
    mission_id = payload.get("mission_id")

    # If mission_id is not passed, resolve from active mission in database
    if not mission_id:
        try:
            from models import Mission
            from sqlalchemy import select
            async with async_session_factory() as session:
                active_m = await session.execute(
                    select(Mission).where(Mission.status == "active").order_by(Mission.started_at.desc()).limit(1)
                )
                m_row = active_m.scalar_one_or_none()
                if m_row:
                    mission_id = m_row.mission_id
                    if not drone_id:
                        drone_id = m_row.drone_id
        except Exception as err:
            logger.debug(f"Could not auto-detect active mission for suggestion: {err}")

    suggested_by = (
        user.get("name")
        or (f"{user.get('first_name', '')} {user.get('last_name', '')}".strip())
        or user.get("email")
        or "Edge Operator"
    )
    if user.get("email") and f"({user.get('email')})" not in suggested_by:
        suggested_by = f"{suggested_by} ({user.get('email')})"

    sugg_id = f"SUGG-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

    # Format: "<MISSION ID>: Suggests <AI Model name>"
    models_str = ", ".join(model_names) if model_names else "AI Models"
    mission_tag = mission_id or (drone_id if drone_id else "Edge Mission")
    display_title = f"{mission_tag}: Suggests {models_str}"

    sugg_dict = {
        "id": sugg_id,
        "models": models,
        "model_names": model_names,
        "priority": priority,
        "reason": reason,
        "location": location,
        "suggested_by": suggested_by,
        "timestamp": ts,
        "status": "PENDING",
        "drone_id": drone_id,
        "mission_id": mission_id,
        "display_title": display_title,
    }

    # Relay to remote Ingest Server
    sent_to_ingest = await _forward_suggestion_to_ingest(sugg_dict)
    sugg_dict["sent_to_ingest"] = sent_to_ingest

    # Persist in DB
    async with async_session_factory() as session:
        new_sugg = AIModelSuggestion(
            id=sugg_id,
            models=models,
            model_names=model_names,
            priority=priority,
            reason=reason,
            location=location,
            suggested_by=suggested_by,
            timestamp=ts,
            status="PENDING",
            drone_id=drone_id,
            mission_id=mission_id,
        )
        session.add(new_sugg)
        await session.commit()

    # Log to audit trail
    try:
        await write_audit_v2(
            actor=user,
            action="SUGGEST_AI_MODELS",
            action_type="create",
            category="analytics",
            severity="info",
            target=sugg_id,
            target_type="ai_model_suggestion",
            target_id=sugg_id,
            target_name=", ".join(model_names),
            details=f"Models suggested: {', '.join(model_names)} at {location}. Priority: {priority}",
            source_module="edge",
            request=request,
        )
    except Exception as audit_err:
        logger.warning(f"Audit log failed for model suggestion: {audit_err}")

    # Broadcast to WebSocket clients (Ingest / Command / GPU Dashboard)
    await hub.broadcast({
        "type": "model_suggestion",
        "suggestion": sugg_dict,
        "title": display_title,
        "message": display_title,
        "mission_id": mission_id,
        "drone_id": drone_id,
        "models": models,
        "model_names": model_names,
        "priority": priority,
        "location": location,
        "suggested_by": suggested_by,
        "timestamp": ts,
    })

    return {"status": "ok", "suggestion": sugg_dict}


@router.post("/analytics/suggestions/{suggestion_id}/status")
async def update_suggestion_status(
    suggestion_id: str,
    payload: dict,
    user: dict = Depends(get_current_user),
    request: Request = None,
):
    """Update suggestion status to ACKNOWLEDGED or DEPLOYED_TO_GPU."""
    from database import async_session_factory
    from models import AIModelSuggestion
    from sqlalchemy import update
    from services.websocket_hub import hub

    new_status = payload.get("status", "ACKNOWLEDGED").upper()
    if new_status not in ("PENDING", "ACKNOWLEDGED", "DEPLOYED_TO_GPU"):
        new_status = "ACKNOWLEDGED"

    async with async_session_factory() as session:
        res = await session.execute(
            update(AIModelSuggestion)
            .where(AIModelSuggestion.id == suggestion_id)
            .values(status=new_status)
        )
        if res.rowcount == 0:
            raise HTTPException(404, "Model suggestion not found")
        await session.commit()

    await hub.broadcast({
        "type": "model_suggestion_status",
        "suggestion_id": suggestion_id,
        "status": new_status,
    })

    return {"status": "ok", "suggestion_id": suggestion_id, "new_status": new_status}


