"""
Async SQLAlchemy engine + session factory for DVAS.

Usage in FastAPI routes:
    from database import get_db
    async def my_route(db: AsyncSession = Depends(get_db)):
        ...
"""

import os
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./dvas.db")

engine_kwargs = {"echo": False}
if "sqlite" not in DATABASE_URL:
    engine_kwargs.update({"pool_size": 10, "max_overflow": 20})

engine = create_async_engine(
    DATABASE_URL,
    **engine_kwargs
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db():
    """FastAPI dependency — yields an async session, auto-closes on exit."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
