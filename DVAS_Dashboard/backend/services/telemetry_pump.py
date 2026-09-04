"""
Telemetry pump background service.
"""
import os, asyncio, random, uuid, logging
from datetime import datetime, timezone
import httpx
from sqlalchemy import select, update
from database import async_session_factory
from models import Drone, Alert
from services.websocket_hub import hub
from core.config import INGEST_URL

logger = logging.getLogger(__name__)


async def _telemetry_pump():
    """Background broadcaster: pushes drone telemetry + occasional new alerts to all clients.

    Hybrid approach:
    1. Fetches real telemetry from the ingest server (/ingest/telemetry).
    2. Upserts any real drones found there into the DB with live GPS/battery data.
    3. Falls back to small simulated drift for DB drones not seen in the ingest server.
    """
    import httpx as _httpx
    INGEST_URL_local = INGEST_URL

    alert_types = [
        "crowd_surge",
        "vehicle_anomaly",
        "perimeter_intrusion",
        "unattended_object",
        "wrong_way",
    ]
    zones = ["Attukal Temple", "Marine Drive", "East Fort", "Statue Jn"]

    while True:
        await asyncio.sleep(3)
        try:
            # ── 1. Fetch real telemetry from the ingest server ─────────────────
            real_drones: dict[str, dict] = {}  # drone_id → telemetry dict
            try:
                from ingest_proxy import _get_service_token
                headers = {}
                svc_token = await _get_service_token()
                if svc_token:
                    headers["Authorization"] = f"Bearer {svc_token}"

                async with _httpx.AsyncClient(timeout=2.0) as client:
                    resp = await client.get(f"{INGEST_URL_local}/ingest/telemetry", headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        for d in data.get("drones", []):
                            real_drones[d["drone_id"]] = d
            except Exception as te:
                logger.debug("Could not fetch ingest telemetry: %s", te)

            # ── 2. Upsert real drones into the DB ──────────────────────────────
            async with async_session_factory() as session:
                for drone_id, telem in real_drones.items():
                    lat = telem.get("latitude")
                    lng = telem.get("longitude")
                    if lat is None or lng is None:
                        lat = lat or 8.5241
                        lng = lng or 76.9366
                    battery = telem.get("battery_pct") if telem.get("battery_pct") is not None else 85.0
                    altitude = telem.get("altitude_m") if telem.get("altitude_m") is not None else 120.0
                    speed_mps = telem.get("speed_mps") or 0.0
                    # If mission_id is absent, the drone is not actively on a mission
                    # and should be considered offline for the dashboard.
                    mission_id = telem.get("mission_id")
                    mission_name = telem.get("mission_name")
                    operator_name = telem.get("operator")
                    jurisdiction = telem.get("location")
                    drone_status = "live" if mission_id else "offline"

                    # Check if drone already exists in DB
                    result = await session.execute(
                        select(Drone).where(Drone.id == drone_id)
                    )
                    existing = result.scalar_one_or_none()
                    if existing:
                        await session.execute(
                            update(Drone)
                            .where(Drone.id == drone_id)
                            .values(
                                lat=round(lat, 6),
                                lng=round(lng, 6),
                                battery_pct=round(battery, 1),
                                altitude_m=round(altitude, 1),
                                status=drone_status,
                                mission_id=mission_id,
                                mission_name=mission_name,
                                operator_name=operator_name,
                                jurisdiction=jurisdiction,
                            )
                        )
                    else:
                        # Create drone entry so it shows on the map
                        session.add(Drone(
                            id=drone_id,
                            operator_id=None,
                            operator_name=operator_name,
                            mission_id=mission_id,
                            mission_name=mission_name,
                            jurisdiction=jurisdiction,
                            status=drone_status,
                            lat=round(lat, 6),
                            lng=round(lng, 6),
                            altitude_m=round(altitude, 1),
                            battery_pct=round(battery, 1),
                            speed_mps=round(speed_mps, 2),
                            signal_pct=90.0,
                            last_analytics=None,
                        ))
                await session.commit()

            # ── 3. Transition drones no longer in real_drones to offline ──────────
            async with async_session_factory() as session:
                stale_stmt = select(Drone).where(Drone.status.in_(["live", "degraded"]))
                if real_drones:
                    stale_stmt = stale_stmt.where(Drone.id.not_in(list(real_drones.keys())))
                stale_result = await session.execute(stale_stmt)
                stale_drones = stale_result.scalars().all()
                for sd in stale_drones:
                    sd.status = "offline"
                if stale_drones:
                    await session.commit()

            # ── 4. Broadcast live telemetry for real active drones ───────────────
            updates = []
            for drone_id, telem in real_drones.items():
                if not telem.get("mission_id"):
                    continue
                updates.append({
                    "id": drone_id,
                    "lat": round(telem.get("latitude"), 6) if telem.get("latitude") is not None else None,
                    "lng": round(telem.get("longitude"), 6) if telem.get("longitude") is not None else None,
                    "battery_pct": round(telem.get("battery_pct") or 0.0, 1),
                    "altitude_m": round(telem.get("altitude_m") or 0.0, 1),
                    "abs_alt": round(telem.get("abs_alt") or 0.0, 1) if telem.get("abs_alt") is not None else None,
                    "speed_mps": round(telem.get("speed_mps") or 0.0, 2),
                    "gb_yaw": round(telem.get("gb_yaw") or 0.0, 1) if telem.get("gb_yaw") is not None else None,
                    "gb_pitch": round(telem.get("gb_pitch") or 0.0, 1) if telem.get("gb_pitch") is not None else None,
                    "gb_roll": round(telem.get("gb_roll") or 0.0, 1) if telem.get("gb_roll") is not None else None,
                    "focal_len": telem.get("focal_len"),
                    "dzoom_ratio": telem.get("dzoom_ratio"),
                    "gps_lock": telem.get("gps_lock", True) if (telem.get("latitude") is not None and telem.get("longitude") is not None) else False,
                    "status": "live",
                    "mission_id": telem.get("mission_id"),
                    "mission_name": telem.get("mission_name"),
                    "operator_name": telem.get("operator"),
                    "jurisdiction": telem.get("location"),
                    "is_real": True,
                })

            if updates:
                await hub.broadcast(
                    {
                        "type": "telemetry",
                        "drones": updates,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }
                )

            # occasionally inject a new alert for active missions
            active_mission_drones = [d for d in updates if d.get("mission_id")]
            if random.random() < 0.28 and active_mission_drones:
                d = random.choice(active_mission_drones)
                a = {
                    "id": f"A-{uuid.uuid4().hex[:8].upper()}",
                    "mission_id": d.mission_id,
                    "jurisdiction": d.jurisdiction,
                    "drone_id": d.id,
                    "type": random.choice(alert_types),
                    "confidence": round(random.uniform(0.72, 0.98), 2),
                    "zone": random.choice(zones),
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "acknowledged_by": None,
                    "acknowledged_at": None,
                }
                try:
                    async with async_session_factory() as session:
                        session.add(Alert(**a))
                        await session.commit()
                    await hub.broadcast({"type": "alert", "alert": a})
                except Exception as e:
                    logger.debug(f"telemetry pump alert generation error: {e}")
        except Exception as e:
            logger.warning(f"telemetry pump: {e}")


def start_telemetry_pump():
    asyncio.create_task(_telemetry_pump())
