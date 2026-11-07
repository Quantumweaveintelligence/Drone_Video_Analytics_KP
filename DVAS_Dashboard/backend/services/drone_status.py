"""
Drone status on stream end (analytics plan A5).

When ingest drains or purges a drone's stream the dashboard's Drone row must go offline
right away, otherwise the Live Situational Map and the Multi-Drone Grid keep a tile for a
drone whose stream is gone (blank tile, 404 frame polls, "0 LIVE STREAMS" next to it).

Two sources end a stream:
  * ingest broadcasts {"event": "mission_drained" | "drone_offline", "drone_id", ...} on its
    /ws/status WebSocket (auto drain, orphan cleanup, manual purge). A background listener
    subscribes there with the backend's service token.
  * the dashboard's own purge route (ingest_proxy.proxy_purge_stream) calls
    mark_drone_offline directly after ingest confirmed the purge.

The telemetry pump stays the fallback: drones missing from /ingest/streams/active go
offline after its grace period.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import or_, update

from database import async_session_factory
from models import Drone
from services.websocket_hub import hub

logger = logging.getLogger(__name__)

# Ingest /ws/status events that mean "this drone has no stream any more"
STREAM_END_EVENTS = ("mission_drained", "drone_offline")

_RECONNECT_MIN_S = 5.0
_RECONNECT_MAX_S = 60.0


async def mark_drone_offline(drone_id: str, reason: str, mission_id: Optional[str] = None) -> bool:
    """Set the Drone row to "offline" and tell the dashboards (hub "drone_offline").

    Idempotent: returns True only when the row changed, so the broadcast goes out once even
    when both the purge route and ingest's WebSocket report the same purge. Never raises.
    """
    if not drone_id:
        return False
    try:
        async with async_session_factory() as session:
            res = await session.execute(
                update(Drone)
                .where(Drone.id == drone_id, or_(Drone.status.is_(None), Drone.status != "offline"))
                .values(status="offline")
            )
            await session.commit()
            changed = (res.rowcount or 0) > 0
    except Exception as exc:
        logger.warning("Could not mark drone %s offline (%s): %s", drone_id, reason, exc)
        return False

    # The pump's grace countdown for this drone is moot now
    try:
        from services import telemetry_pump
        telemetry_pump._offline_countdown.pop(drone_id, None)
    except Exception:
        pass

    if changed:
        logger.info("Drone %s offline: %s", drone_id, reason)
        try:
            await hub.broadcast({
                "type": "drone_offline",
                "drone_id": drone_id,
                "mission_id": mission_id,
                "reason": reason,
                "ts": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as exc:
            logger.warning("drone_offline broadcast failed for %s: %s", drone_id, exc)
    return changed


async def handle_ingest_event(msg) -> bool:
    """One message from ingest /ws/status (dict or JSON text). True when a drone went offline."""
    if isinstance(msg, (bytes, str)):
        try:
            msg = json.loads(msg)
        except (ValueError, TypeError):
            return False
    if not isinstance(msg, dict):
        return False
    event = msg.get("event") or msg.get("type")
    if event not in STREAM_END_EVENTS:
        return False
    if msg.get("drone_still_active"):
        return False        # an older mission drained while the drone flies a newer one (Phase 2a)
    return await mark_drone_offline(str(msg.get("drone_id") or ""), reason=f"ingest:{event}",
                                    mission_id=msg.get("mission_id"))


async def _ingest_event_listener():
    """Stay subscribed to ingest /ws/status; reconnect with backoff (5 s doubling to 60 s)."""
    import websockets
    from urllib.parse import quote
    from ingest_proxy import INGEST_SERVER_URL, _get_service_token

    base = INGEST_SERVER_URL.replace("http://", "ws://").replace("https://", "wss://")
    delay = _RECONNECT_MIN_S
    warned = False
    while True:
        try:
            token = await _get_service_token()
            if not token:
                raise RuntimeError("no service token")
            async with websockets.connect(f"{base}/ws/status?token={quote(token)}") as ws:
                logger.info("Listening to ingest /ws/status for stream-end events")
                delay, warned = _RECONNECT_MIN_S, False
                async for raw in ws:
                    try:
                        await handle_ingest_event(raw)
                    except Exception as exc:
                        logger.debug("ingest event ignored: %s", exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Logged once per outage; the telemetry pump still takes stale drones offline
            (logger.debug if warned else logger.warning)(
                "Ingest status WebSocket unavailable (%s); retrying in %.0f s", exc, delay)
            warned = True
        await asyncio.sleep(delay)
        delay = min(delay * 2, _RECONNECT_MAX_S)


def start_ingest_event_listener():
    asyncio.create_task(_ingest_event_listener())
