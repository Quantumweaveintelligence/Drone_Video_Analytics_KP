"""Analytics shed ranges → audit log (v2 plan R6.3).

Needs a real Valkey (skipped otherwise):
  TEST_VALKEY_URL=redis://127.0.0.1:6391/0 python -m pytest tests/test_analytics_shed_audit.py -q
"""
import asyncio
import os

import pytest

redis_asyncio = pytest.importorskip("redis.asyncio")

from services import analytics_shed as sa  # noqa: E402

URL = os.getenv("TEST_VALKEY_URL", "redis://127.0.0.1:6391/0")


def _reachable() -> bool:
    import redis
    try:
        return redis.Redis.from_url(URL, socket_connect_timeout=0.5).ping()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason=f"no Valkey at {URL}")
REC = {"service": "person", "drone_id": "DRN-1", "stream": "mission:frames:MSN-1", "reason": "drop_oldest",
       "from_seq": "2", "to_seq": "99", "frames": "98", "behind_s": "5.0", "at": "1762671000.0"}


def run(coro):
    async def go():
        r = redis_asyncio.from_url(URL)
        await r.delete(sa.STREAM)
        await r.xgroup_create(sa.STREAM, sa.GROUP, id="0", mkstream=True)
        try:
            return await coro(r)
        finally:
            await r.delete(sa.STREAM)
            await r.aclose()
    return asyncio.run(go())


def test_each_shed_range_becomes_one_audit_row_and_is_acknowledged():
    async def t(r):
        await r.xadd(sa.STREAM, REC)
        rows = []

        async def write(**kw):
            rows.append(kw)
        assert await sa.process_batch(r, write, block_ms=10) == 1
        assert len(rows) == 1 and rows[0]["action"] == "analytics_shed"
        assert rows[0]["after_values"] == REC and rows[0]["target_name"] == "DRN-1"
        assert "98 frames" in rows[0]["details"] and "recording" in rows[0]["details"]
        assert (await r.xpending(sa.STREAM, sa.GROUP))["pending"] == 0
        assert await sa.process_batch(r, write, block_ms=10) == 0 and len(rows) == 1   # not twice
    run(t)


def test_a_failed_audit_write_is_retried_not_lost():
    async def t(r):
        await r.xadd(sa.STREAM, REC)

        async def broken(**kw):
            raise RuntimeError("db down")
        with pytest.raises(RuntimeError):
            await sa.process_batch(r, broken, block_ms=10)
        assert (await r.xpending(sa.STREAM, sa.GROUP))["pending"] == 1       # still owed
        rows = []

        async def write(**kw):
            rows.append(kw)
        assert await sa.process_batch(r, write, block_ms=10) == 1 and len(rows) == 1
        assert (await r.xpending(sa.STREAM, sa.GROUP))["pending"] == 0
    run(t)
