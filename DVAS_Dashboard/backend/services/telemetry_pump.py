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
    INGEST_URL_local = os.environ.get("INGEST_SERVER_URL", "http://10.51.27.250:8000")

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
                    lat = telem.get("latitude") or 8.5241
                    lng = telem.get("longitude") or 76.9366
                    battery = telem.get("battery_pct") or 85.0
                    altitude = telem.get("altitude_m") or 120.0
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

            # ── 3. Simulate drift for DB-only drones + build broadcast ─────────
            async with async_session_factory() as session:
                result = await session.execute(
                    select(Drone).where(Drone.status.in_(["live", "degraded"]))
                )
                drone_objs = result.scalars().all()
                updates = []
                for d in drone_objs:
                    if d.id in real_drones:
                        # Use real values — already updated above
                        telem = real_drones[d.id]
                        updates.append({
                            "id": d.id,
                            "lat": round(telem.get("latitude") or d.lat, 6),
                            "lng": round(telem.get("longitude") or d.lng, 6),
                            "battery_pct": round(telem.get("battery_pct") or d.battery_pct, 1),
                            "altitude_m": round(telem.get("altitude_m") or d.altitude_m or 0, 1),
                            "speed_mps": round(telem.get("speed_mps") or 0, 2),
                            "status": "live",
                            "mission_id": telem.get("mission_id") or d.mission_id,
                            "mission_name": telem.get("mission_name") or d.mission_name,
                            "operator_name": telem.get("operator") or d.operator_name,
                            "jurisdiction": telem.get("location") or d.jurisdiction,
                            "is_real": True,
                        })
                    else:
                        # Simulate small drift for demo drones not in ingest
                        new_lat = round(d.lat + random.uniform(-0.0015, 0.0015), 6)
                        new_lng = round(d.lng + random.uniform(-0.0015, 0.0015), 6)
                        new_battery = max(0, d.battery_pct - random.choice([0, 0, 0, 1]))
                        await session.execute(
                            update(Drone)
                            .where(Drone.id == d.id)
                            .values(lat=new_lat, lng=new_lng, battery_pct=new_battery)
                        )
                        updates.append({
                            "id": d.id,
                            "lat": new_lat,
                            "lng": new_lng,
                            "battery_pct": new_battery,
                            "altitude_m": d.altitude_m or 0,
                            "speed_mps": 0,
                            "status": d.status,
                            "mission_id": d.mission_id,
                            "jurisdiction": d.jurisdiction,
                            "is_real": False,
                        })
                await session.commit()

            await hub.broadcast(
                {
                    "type": "telemetry",
                    "drones": updates,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            )
            # occasionally inject a new alert
            if random.random() < 0.28 and drone_objs:
                d = random.choice(drone_objs)
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
