"""Mission-keyed streams (plan Phase 2a): per drone the sampler reads its mission streams
oldest first, moves on only when a mission has ended and was read completely, and resets
the drone's trackers / state at that boundary.

Needs a real Valkey (skipped otherwise):
  docker run --rm -d -p 127.0.0.1:6392:6379 valkey/valkey:7.2
Run from dvas_analytics/:  TEST_VALKEY_URL=redis://127.0.0.1:6392/0 python -m pytest shared/tests/test_mission_streams.py -q
"""
import asyncio
import os
import time

import pytest

redis_asyncio = pytest.importorskip("redis.asyncio")

from shared import frame_sampler as fs, mission_streams as ms
from shared.frame_sampler import FrameSampler

URL = os.getenv("TEST_VALKEY_URL", "redis://127.0.0.1:6392/0")


def _reachable() -> bool:
    async def ping():
        c = redis_asyncio.from_url(URL, socket_connect_timeout=0.5)
        try:
            return await c.ping()
        finally:
            await c.aclose()
    try:
        return asyncio.run(ping())
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason=f"no Valkey at {URL}")


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(fs, "RESOLVE_EVERY_S", 0.0)
    monkeypatch.setattr(fs, "REORDER_MS", 0)


def run(coro_fn):
    async def wrapper():
        c = redis_asyncio.from_url(URL)
        await c.flushdb()
        try:
            return await coro_fn(c)
        finally:
            await c.aclose()
    return asyncio.run(wrapper())


async def mission(c, drone, mid, status="live", started=None, ended=None, seqs=(), active=True):
    sid = ms.sid_of(mid)
    started = started if started is not None else time.time()
    await c.hset(ms.index_key(sid), mapping={
        "drone_id": drone, "mission_id": mid, "operator_sub": "op", "status": status,
        "started_at": str(started), "ended_at": "" if ended is None else str(ended)})
    await c.zadd(ms.missions_key(drone), {sid: started})
    if active:
        await c.set(ms.active_key(drone), sid)
    await frames(c, ms.mission_key(sid), mid, seqs)
    return ms.mission_key(sid)


async def frames(c, key, mid, seqs):
    for q in seqs:
        await c.xadd(key, {"frame": b"x", "frame_seq": str(q), "ts": "0", "mission_id": mid})


async def drain(s, drones, rounds=25):
    got = []
    for _ in range(rounds):
        out = await s.next_round(drones)
        for d, f in out.items():
            got.append((d, f.get("mission_id"), int(f["frame_seq"])))
    return got


def sampler(c, mode="mission", **kw):
    s = FrameSampler("svc", "svc-group", "svc-1", governed=False, cap_fps=60, block_ms=5,
                     redis_factory=lambda: c, stream_mode=mode, **kw)
    s.resets = []
    s.on_idle.append(s.resets.append)
    return s


def test_missions_are_read_oldest_first_with_a_reset_between_them():
    async def t(c):
        old = time.time() - 600
        await mission(c, "D", "M 1", status="draining", started=old, ended=old + 60, seqs=range(5))
        await mission(c, "D", "M2", seqs=range(3))
        s = sampler(c)
        got = await drain(s, ["D"])
        assert got == [("D", "M 1", q) for q in range(5)] + [("D", "M2", q) for q in range(3)]
        assert s.resets == ["D"]                      # fresh trackers once, at the boundary
        assert s.stats["D"].skipped == 0 and s.stats["D"].late == 0   # seq restart is no gap
    run(t)


def test_a_live_mission_is_not_left_even_when_read_completely():
    async def t(c):
        m1 = await mission(c, "D", "M1", started=time.time() - 60, seqs=range(2))
        await mission(c, "D", "M2", seqs=range(2))
        s = sampler(c)
        assert [g[2] for g in await drain(s, ["D"], 20)] == [0, 1]
        await frames(c, m1, "M1", [2])                 # still flying: more frames come
        assert [g[1:] for g in await drain(s, ["D"], 20)] == [("M1", 2)]
        await c.hset(ms.index_key("M1"), mapping={"status": "ended", "ended_at": str(time.time())})
        assert [g[1:] for g in await drain(s, ["D"], 20)] == [("M2", 0), ("M2", 1)]
    run(t)


def test_draining_mission_takes_retries_for_the_switch_grace():
    async def t(c):
        m1 = await mission(c, "D", "M1", status="draining", started=time.time() - 60,
                           ended=time.time(), seqs=[0])
        await mission(c, "D", "M2", seqs=[0])
        s = sampler(c)
        assert [g[1:] for g in await drain(s, ["D"], 10)] == [("M1", 0)]
        await frames(c, m1, "M1", [1])                 # the edge's retry of an in-flight frame
        assert [g[1:] for g in await drain(s, ["D"], 10)] == [("M1", 1)]
        await c.hset(ms.index_key("M1"), "ended_at", str(time.time() - ms.SWITCH_AFTER_S - 1))
        assert [g[1:] for g in await drain(s, ["D"], 10)] == [("M2", 0)]
    run(t)


def test_two_drones_with_the_same_sequence_numbers_stay_apart():
    async def t(c):
        await mission(c, "A", "MA", seqs=range(3))
        await mission(c, "B", "MB", seqs=range(3))
        got = await drain(sampler(c), ["A", "B"])
        assert sorted(got) == [("A", "MA", q) for q in range(3)] + [("B", "MB", q) for q in range(3)]
    run(t)


def test_legacy_stream_is_drained_before_the_first_mission():
    async def t(c):
        await frames(c, ms.legacy_key("D"), "OLD", range(2))
        await mission(c, "D", "M1", seqs=range(2))
        s = sampler(c)
        got = await drain(s, ["D"])
        assert [g[1:] for g in got] == [("OLD", 0), ("OLD", 1), ("M1", 0), ("M1", 1)]
        assert s.resets == ["D"]
    run(t)


def test_drone_mode_reads_only_the_legacy_stream():
    async def t(c):
        await frames(c, ms.legacy_key("D"), "OLD", range(2))
        await mission(c, "D", "M1", seqs=range(2))
        got = await drain(sampler(c, mode="drone"), ["D"])
        assert [g[1:] for g in got] == [("OLD", 0), ("OLD", 1)]
    run(t)


def test_newest_mode_follows_the_active_mission():
    async def t(c):
        await mission(c, "D", "M1", started=time.time() - 60, seqs=range(2))
        s = sampler(c, mode="mission")
        s.mode = "newest"
        await drain(s, ["D"], 5)                       # group created at "$": only new frames
        m2 = await mission(c, "D", "M2", seqs=[6])    # the stream exists from its first frame
        await drain(s, ["D"], 5)                       # switched: M2's group starts at its end
        await frames(c, m2, "M2", [7])
        assert [g[1:] for g in await drain(s, ["D"], 10)] == [("M2", 7)]
    run(t)


def test_queued_counts_every_stream_of_the_drone():
    async def t(c):
        await mission(c, "D", "M1", started=time.time() - 60, seqs=range(3))
        await mission(c, "D", "M2", seqs=range(4))
        s = sampler(c)
        while not await s.next_round(["D"]):          # one frame of M1 read
            pass
        await s._refresh_queued()
        assert s._queued["D"] == 2 + 4
    run(t)


def test_discovery_and_latest_stream():
    async def t(c):
        await frames(c, ms.legacy_key("L"), "OLD", [0])
        await mission(c, "D", "M1", started=time.time() - 60, seqs=[0], active=False)
        m2 = await mission(c, "D", "M 2", seqs=[0])
        assert sorted(await ms.discover_drones(c)) == ["D", "L"]
        assert await ms.discover_drones(c, mode="drone") == ["L"]
        assert await ms.latest_stream(c, "D") == m2 == "mission:frames:M_2"
        await c.delete(m2)                             # purged: fall back to the newest that exists
        await c.zrem(ms.missions_key("D"), "M_2")
        assert await ms.latest_stream(c, "D") == "mission:frames:M1"
        assert await ms.latest_stream(c, "L") == "drone:frames:L"
        assert await ms.latest_stream(c, "nobody") is None
    run(t)


def test_purged_mission_stream_is_skipped():
    async def t(c):
        m1 = await mission(c, "D", "M1", started=time.time() - 60, seqs=range(3))
        await mission(c, "D", "M2", seqs=range(2))
        s = sampler(c)
        await drain(s, ["D"], 1)
        await c.delete(m1)                             # purged by an officer mid-read
        await c.zrem(ms.missions_key("D"), "M1")
        got = await drain(s, ["D"])
        assert [g[1:] for g in got] == [("M2", 0), ("M2", 1)]
    run(t)
