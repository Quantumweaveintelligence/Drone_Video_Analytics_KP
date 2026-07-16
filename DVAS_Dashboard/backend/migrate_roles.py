"""
migrate_roles.py — One-time DVAS RBAC migration script
=======================================================
Migrates existing DB users from the 6-role model to the 4-role model:
  field_supervisor    → control_room_officer
  supervisory_officer → senior_officer

Usage:
  python migrate_roles.py [--dry-run]

Set DATABASE_URL env var (or rely on .env in the same directory).
"""

import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

DATABASE_URL = os.environ["DATABASE_URL"]
engine = create_async_engine(DATABASE_URL, echo=False)
session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

ROLE_MAP = {
    "field_supervisor": "control_room_officer",
    "supervisory_officer": "senior_officer",
}

DRY_RUN = "--dry-run" in sys.argv


async def migrate():
    # Late import so models are available
    from models import User

    print("=" * 60)
    print("DVAS Role Migration: 6-role → 4-role")
    print(f"Mode: {'DRY RUN (no changes written)' if DRY_RUN else 'LIVE'}")
    print("=" * 60)

    async with session_factory() as session:
        result = await session.execute(select(User))
        all_users = result.scalars().all()

    before = [(u.email, u.role) for u in all_users]
    to_migrate = [(u.id, u.email, u.role) for u in all_users if u.role in ROLE_MAP]

    print(f"\nTotal users in DB : {len(all_users)}")
    print(f"Users to migrate  : {len(to_migrate)}")

    if not to_migrate:
        print("\nNothing to do — no field_supervisor or supervisory_officer accounts found.")
        return

    print("\nMigration plan:")
    for uid, email, old_role in to_migrate:
        new_role = ROLE_MAP[old_role]
        print(f"  {email:40s}  {old_role:25s} → {new_role}")

    if DRY_RUN:
        print("\n[DRY RUN] No changes written. Re-run without --dry-run to apply.")
        return

    confirm = input("\nProceed? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    migrated = 0
    errors = []
    async with session_factory() as session:
        for uid, email, old_role in to_migrate:
            new_role = ROLE_MAP[old_role]
            try:
                result = await session.execute(
                    update(User)
                    .where(User.id == uid)
                    .values(role=new_role)
                )
                if result.rowcount == 1:
                    migrated += 1
                    print(f"  ✓ {email} → {new_role}")
                else:
                    errors.append(f"  ✗ {email}: rowcount={result.rowcount}")
            except Exception as e:
                errors.append(f"  ✗ {email}: {e}")
        await session.commit()

    print(f"\nMigrated : {migrated}")
    if errors:
        print(f"Errors   : {len(errors)}")
        for e in errors:
            print(e)
        sys.exit(1)
    else:
        print("Migration completed successfully.")

    # Print after-state
    async with session_factory() as session:
        result = await session.execute(select(User))
        after_users = result.scalars().all()

    print("\nPost-migration role summary:")
    from collections import Counter
    counts = Counter(u.role for u in after_users)
    for role, count in sorted(counts.items()):
        print(f"  {role:30s}: {count}")


if __name__ == "__main__":
    asyncio.run(migrate())
