"""
Seed service for populating the database.
"""
import uuid, random, logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from database import engine, async_session_factory
from models import Base, AuditLog, Mission, Drone, Alert, Evidence, Export
from audit_service import _audit_hash

logger = logging.getLogger(__name__)

# ---------- Startup: seed ----------
JURISDICTIONS = ["Thiruvananthapuram", "Kochi", "Kozhikode"]

SEED_USERS = [
    {
        "email": "operator@dvas.kp",
        "name": "Ravi Menon",
        "role": "drone_operator",
        "jurisdiction": "Thiruvananthapuram",
        "mission_scope": ["M-101"],
    },
    {
        "email": "control@dvas.kp",
        "name": "Anjali Nair",
        "role": "control_room_officer",
        "jurisdiction": "Thiruvananthapuram",
        "mission_scope": ["M-101", "M-102"],
    },
    {
        # Migrated from field_supervisor → control_room_officer (see migrate_roles.py)
        "email": "field@dvas.kp",
        "name": "Suresh Pillai",
        "role": "control_room_officer",
        "jurisdiction": "Thiruvananthapuram",
        "mission_scope": ["M-101", "M-102"],
    },
    {
        # Migrated from supervisory_officer → senior_officer (see migrate_roles.py)
        "email": "supervisor@dvas.kp",
        "name": "Deepa Krishnan",
        "role": "senior_officer",
        "jurisdiction": "Kochi",
        "mission_scope": [],
    },
    {
        "email": "senior@dvas.kp",
        "name": "IG. Rajan Varma",
        "role": "senior_officer",
        "jurisdiction": "State HQ",
        "mission_scope": [],
    },
    {
        "email": "admin@dvas.kp",
        "name": "Sys Admin",
        "role": "system_administrator",
        "jurisdiction": "HQ Restricted",
        "mission_scope": [],
    },
]


async def seed_data():
    # Create all tables (including the new missions schema)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_factory() as session:
        user_ids_by_email = {u["email"]: "seed-id" for u in SEED_USERS}

        # If missions already seeded, ensure audit chain exists then return
        mission_count = (await session.execute(
            select(func.count()).select_from(Mission)
        )).scalar() or 0
        if mission_count > 0:
            audit_count = (await session.execute(
                select(func.count()).select_from(AuditLog)
            )).scalar() or 0
            if audit_count == 0:
                await _seed_audit_chain(session, user_ids_by_email)
            return  # already seeded

        # Clean slate for dependent seed tables to prevent IntegrityError on re-seed
        await session.execute(delete(Drone))
        await session.execute(delete(Alert))
        await session.execute(delete(Evidence))
        await session.execute(delete(Export))
        await session.commit()

        now = datetime.now(timezone.utc)
        op_id = user_ids_by_email["operator@dvas.kp"]

        missions = [
            {
                "mission_id": "M-101",
                "name": "Attukal Pongala Crowd Watch",
                "status": "active",
                "drone_id": "DRN-001",
                "operator_name": "Ravi Menon",
                "jurisdiction": "Thiruvananthapuram",
                "started_at": (now - timedelta(hours=3)).isoformat(),
                "operator_ids": [op_id],
                "supervisor": "Suresh Pillai",
                "zone": "Attukal Temple",
                "objective": "Crowd density & choke-point monitoring",
            },
            {
                "mission_id": "M-102",
                "name": "Kochi Marine Drive Traffic",
                "status": "active",
                "drone_id": "DRN-002",
                "operator_name": "K. Suresh",
                "jurisdiction": "Thiruvananthapuram",
                "started_at": (now - timedelta(hours=1)).isoformat(),
                "operator_ids": [],
                "supervisor": "Suresh Pillai",
                "zone": "Marine Drive",
                "objective": "Vehicle flow & anomaly detection",
            },
            {
                "mission_id": "M-103",
                "name": "Kozhikode Beach Perimeter",
                "status": "closed",
                "drone_id": "DRN-003",
                "operator_name": "R. Menon",
                "jurisdiction": "Kozhikode",
                "started_at": (now - timedelta(days=2)).isoformat(),
                "operator_ids": [],
                "supervisor": "R. Menon",
                "zone": "Kozhikode Beach",
                "objective": "Perimeter intrusion detection",
            },
        ]
        for m in missions:
            session.add(Mission(**m))

        drones = [
            {
                "id": "DRN-001",
                "operator_id": op_id,
                "operator_name": "Ravi Menon",
                "mission_id": "M-101",
                "jurisdiction": "Thiruvananthapuram",
                "status": "live",
                "lat": 8.5241,
                "lng": 76.9366,
                "altitude_m": 90,
                "battery_pct": 78,
                "signal_pct": 92,
                "last_analytics": {"crowd_density": 340, "vehicles": 12},
            },
            {
                "id": "DRN-002",
                "operator_id": "op-2",
                "operator_name": "K. Suresh",
                "mission_id": "M-102",
                "jurisdiction": "Thiruvananthapuram",
                "status": "live",
                "lat": 9.9673,
                "lng": 76.2810,
                "altitude_m": 120,
                "battery_pct": 64,
                "signal_pct": 88,
                "last_analytics": {"crowd_density": 90, "vehicles": 210},
            },
            {
                "id": "DRN-003",
                "operator_id": "op-3",
                "operator_name": "A. Thomas",
                "mission_id": "M-101",
                "jurisdiction": "Thiruvananthapuram",
                "status": "degraded",
                "lat": 8.5300,
                "lng": 76.9500,
                "altitude_m": 60,
                "battery_pct": 32,
                "signal_pct": 55,
                "last_analytics": {"crowd_density": 260, "vehicles": 4},
            },
            {
                "id": "DRN-004",
                "operator_id": op_id,
                "operator_name": "P. Kurian",
                "mission_id": "M-101",
                "jurisdiction": "Thiruvananthapuram",
                "status": "live",
                "lat": 8.5350,
                "lng": 76.8861,
                "altitude_m": 85,
                "battery_pct": 72,
                "signal_pct": 89,
                "last_analytics": {"crowd_density": 180, "vehicles": 34},
            },
        ]
        for d in drones:
            session.add(Drone(**d))

        alert_types = [
            "crowd_surge",
            "vehicle_anomaly",
            "perimeter_intrusion",
            "unattended_object",
            "wrong_way",
        ]
        zones = [
            "Attukal Temple",
            "Marine Drive",
            "Padmanabhaswamy",
            "Statue Jn",
            "East Fort",
        ]
        for i in range(28):
            drone = random.choice(drones[:3])
            session.add(Alert(
                id=f"A-{1000 + i}",
                mission_id=drone["mission_id"],
                jurisdiction=drone["jurisdiction"],
                drone_id=drone["id"],
                type=random.choice(alert_types),
                confidence=round(random.uniform(0.72, 0.98), 2),
                zone=random.choice(zones),
                ts=(now - timedelta(minutes=random.randint(1, 720))).isoformat(),
                acknowledged_by=None,
                acknowledged_at=None,
            ))

        sample_videos = [
            "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4",
            "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ElephantsDream.mp4",
            "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4",
        ]
        thumbs = [
            "https://images.unsplash.com/photo-1612043743114-d19a560b70eb?w=400",
            "https://images.unsplash.com/photo-1580509742642-84c9d4bd97a4?w=400",
            "https://images.unsplash.com/photo-1541704328070-20b8a213b12d?w=400",
        ]
        evidence_items = []
        for i in range(14):
            drone = random.choice(drones[:3])
            ev_dict = {
                "id": f"E-{2000 + i}",
                "label": f"Confirmed {random.choice(alert_types).replace('_', ' ')} — {drone['id']}",
                "mission_id": drone["mission_id"],
                "jurisdiction": drone["jurisdiction"],
                "drone_id": drone["id"],
                "event_type": random.choice(alert_types),
                "confidence": round(random.uniform(0.80, 0.99), 2),
                "duration_s": random.randint(12, 46),
                "video_url": random.choice(sample_videos),
                "thumbnail": random.choice(thumbs),
                "ts": (now - timedelta(hours=random.randint(0, 96))).isoformat(),
                "legal_hold": random.random() < 0.15,
                "retention_until": (
                    now + timedelta(days=random.randint(30, 180))
                ).isoformat(),
            }
            evidence_items.append(ev_dict)
            session.add(Evidence(**ev_dict))

        for i in range(4):
            ev = random.choice(evidence_items)
            session.add(Export(
                id=f"X-{3000 + i}",
                evidence_id=ev["id"],
                evidence_label=ev["label"],
                jurisdiction=ev["jurisdiction"],
                requested_by="field@dvas.kp",
                requested_at=(
                    now - timedelta(hours=random.randint(1, 30))
                ).isoformat(),
                justification=random.choice(
                    [
                        "Required for FIR case file — Cr. No. 145/2025",
                        "Court submission — Section 65B certified copy",
                        "Internal review — response time audit",
                        "Retention extension request",
                    ]
                ),
                status=random.choice(["pending", "pending", "approved", "rejected"]),
                decision_by=None,
                decision_at=None,
                decision_comment=None,
            ))

        await session.commit()
        await _seed_audit_chain(session, user_ids_by_email, now=now)
    logger.info("Seed complete.")


async def _seed_audit_chain(session: AsyncSession, user_ids_by_email: dict, now: datetime = None):
    if now is None:
        now = datetime.now(timezone.utc)
    logs = []
    prev_hash = "GENESIS"
    for i in range(25):
        actor = random.choice(SEED_USERS)
        act = random.choice(
            [
                "login",
                "view_evidence",
                "alert_ack",
                "export_request",
                "policy_update",
                "user_create",
                "token_revoke",
            ]
        )
        e = {
            "id": str(uuid.uuid4()),
            "ts": (now - timedelta(hours=random.randint(0, 96))).isoformat(),
            "actor_id": user_ids_by_email.get(actor["email"], "seed"),
            "actor_email": actor["email"],
            "actor_role": actor["role"],
            "action": act,
            "target": random.choice(["-", "E-2001", "A-1005", "M-101", "user:new"]),
            "details": "seeded",
            "ip": f"10.0.{random.randint(0, 5)}.{random.randint(2, 240)}",
            "prev_hash": prev_hash,
        }
        e["hash"] = _audit_hash(e)
        prev_hash = e["hash"]
        logs.append(e)
    # Insert in chronological order so the chain reads correctly
    logs.sort(key=lambda x: x["ts"])
    # Re-chain after sort (since sorting changes prev_hash relationships)
    prev_hash = "GENESIS"
    for e in logs:
        e["prev_hash"] = prev_hash
        e["hash"] = _audit_hash(e)
        prev_hash = e["hash"]
    for log_entry in logs:
        session.add(AuditLog(**log_entry))
    await session.commit()
