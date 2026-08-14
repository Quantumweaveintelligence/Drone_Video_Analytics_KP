import asyncio
from database import async_session_factory
from sqlalchemy import select
from models import Drone
import sys

async def main():
    try:
        async with async_session_factory() as session:
            stmt = select(Drone)
            result = await session.execute(stmt)
            rows = result.scalars().all()
            print(f"Found {len(rows)} drones")
            for r in rows:
                d = {c.name: getattr(r, c.name) for c in r.__table__.columns}
                print(d)
    except Exception as e:
        print("ERROR:", e)

if __name__ == "__main__":
    asyncio.run(main())
