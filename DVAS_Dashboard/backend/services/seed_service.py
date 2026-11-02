"""
Seed service for populating the database.
"""
import os, uuid, random, logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, delete, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from database import engine, async_session_factory
from models import Base, AuditLog, Mission, Drone, Alert, Evidence, Export, Report, User
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


# Idempotent schema migrations for tables the ORM already creates on a fresh DB.
# They run right after create_all, before any request, so the ORM's new columns
# exist on old databases too (create_all never adds columns to existing tables).
STARTUP_MIGRATIONS = ("evidence_v2.sql", "evidence_v3_integrity.sql", "exports_v2.sql",
                      "dashboard_timestamps.sql")
_MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations")


async def _apply_startup_migrations(conn) -> None:
    if conn.dialect.name != "postgresql":
        return
    raw = await conn.get_raw_connection()
    for name in STARTUP_MIGRATIONS:
        with open(os.path.join(_MIGRATIONS_DIR, name), encoding="utf-8") as fh:
            sql = fh.read()
        # asyncpg simple-query protocol: the whole multi-statement file in one call
        await raw.driver_connection.execute(sql)
        logger.info("Startup migration applied: %s", name)


def _seed_demo_enabled() -> bool:
    """Demo missions/drones/evidence/audit rows on an empty DB. DVAS_SEED_DEMO=0 disables them."""
    return os.environ.get("DVAS_SEED_DEMO", "1").strip().lower() not in ("0", "false", "no", "off")


async def seed_data():
    # Create all tables (including the new missions schema)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name = 'audit_logs' AND column_name = 'compliance_tags'
                ) THEN
                    ALTER TABLE audit_logs ADD COLUMN compliance_tags JSONB DEFAULT '[]'::jsonb;
                    CREATE INDEX IF NOT EXISTS idx_audit_compliance_tags ON audit_logs USING gin (compliance_tags);
                END IF;
            END $$;
        """))
        await _apply_startup_migrations(conn)

    async with async_session_factory() as session:
        user_ids_by_email = {u["email"]: "seed-id" for u in SEED_USERS}

        # Seed only an EMPTY dashboard database. Seeding never creates User rows, so "users exist"
        # alone never stopped a re-seed: on the second start the deletes below wiped drones, alerts,
        # evidence and exports, then the mission insert hit missions_pkey and the backend crashed
        # (found in the RunPod A7 restart test, 27 Oct 2026). Any existing row → skip.
        async def _count(model) -> int:
            return (await session.execute(select(func.count()).select_from(model))).scalar() or 0

        existing = {m.__tablename__: await _count(m) for m in (User, Mission, Drone, Alert, Evidence, Export)}
        if any(existing.values()):
            if await _count(AuditLog) == 0 and _seed_demo_enabled():
                await _seed_audit_chain(session, user_ids_by_email)
            logger.info("Seed skipped — database already has data: %s",
                        {k: v for k, v in existing.items() if v})
            return  # already seeded / live data

        if not _seed_demo_enabled():
            logger.info("Seed skipped — DVAS_SEED_DEMO=0 (empty database left empty)")
            return

        now = datetime.now(timezone.utc)
        op_id = user_ids_by_email["operator@dvas.kp"]

        missions = [
            {
                "mission_id": "M-101",
                "name": "Attukal Pongala Crowd Watch",
                "status": "completed",
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
                "status": "completed",
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
                "mission_id": None,
                "jurisdiction": "Thiruvananthapuram",
                "status": "offline",
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
                "mission_id": None,
                "jurisdiction": "Thiruvananthapuram",
                "status": "offline",
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
                "mission_id": None,
                "jurisdiction": "Thiruvananthapuram",
                "status": "offline",
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
                "mission_id": None,
                "jurisdiction": "Thiruvananthapuram",
                "status": "offline",
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

        # NOTE: Seed alerts removed — alerts table now only holds real
        # operator-submitted incidents and AI model suggestions.
        alert_types = ["person_detected", "vehicle_detected", "crowd_threshold", "intrusion_alert"]

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
                "duration_s": 0,
                "primary_image_key": "",
                "secondary_image_key": "",
                "image_bucket": "",
                "thumbnail": "",
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

        # ── Seed reports ──
        report_types = [
            ("daily_incident", "Daily Incident Summary", "ready"),
            ("weekly_response", "Weekly Response-Time Report", "generating"),
            ("drone_utilisation", "Drone Utilisation (30d)", "ready"),
        ]
        for i, (rtype, title, status) in enumerate(report_types):
            r_dict = {
                "id": f"RPT-{4000 + i}",
                "report_type": rtype,
                "title": f"{title} — {(now - timedelta(days=i)).strftime('%Y-%m-%d')}",
                "requested_by": "control@dvas.kp",
                "requested_at": (now - timedelta(hours=random.randint(1, 48))).isoformat(),
                "jurisdiction": "Thiruvananthapuram",
                "status": status,
                "params": {"date": (now - timedelta(days=i)).strftime("%Y-%m-%d")},
                "file_key": f"reports/RPT-{4000 + i}.pdf" if status == "ready" else None,
                "file_size_bytes": random.randint(40000, 120000) if status == "ready" else None,
                "generated_at": (now - timedelta(hours=random.randint(0, 12))).isoformat() if status == "ready" else None,
                "error_message": None,
                "watermark_text": f"CONFIDENTIAL — Generated for: control@dvas.kp",
            }
            session.add(Report(**r_dict))

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
