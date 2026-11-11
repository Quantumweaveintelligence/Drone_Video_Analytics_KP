"""Overload shedding (v2 plan R6.3): a service more than DVAS_SHED_AFTER_S behind live sheds
instead of queueing — secondary stages off, then drop-oldest with a logged, marked gap, then a
lower cadence — and comes back down when it keeps up again. A drone at live pace is untouched.

Needs a real Valkey (skipped otherwise), as test_mission_streams.py:
  TEST_VALKEY_URL=redis://127.0.0.1:6392/0 python -m pytest shared/tests/test_shedding.py -q
"""
import time

import pytest

from shared import frame_sampler as fs
from shared.tests.test_mission_streams import URL, _reachable, mission, run, sampler  # noqa: F401

pytestmark = pytest.mark.skipif(not _reachable(), reason=f"no Valkey at {URL}")


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(fs, "RESOLVE_EVERY_S", 0.0)
    monkeypatch.setattr(fs, "REORDER_MS", 0)
    monkeypatch.setattr(fs, "SHED_AFTER_S", 1.5)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


async def frames(c, key, seqs, age_s):
    """Frames received age_s seconds ago (rx_ts_ms, as the video decoder writes them)."""
    rx = int((time.time() - age_s) * 1000)
    for q in seqs:
        await c.xadd(key, {"frame": b"x", "frame_seq": str(q), "rx_ts_ms": str(rx), "ts": str(rx)})


async def take(s, drone="D"):
    for _ in range(50):
        if isinstance(s._clock, Clock):
            s._clock.t += 0.05                                   # one frame interval: the rate cap lets it out
        got = await s.next_round([drone])
        if got:
            return got[drone]
    return None


def test_a_live_drone_is_never_shed():
    async def t(c):
        key = await mission(c, "D", "M1")
        await frames(c, key, range(1, 21), age_s=0.2)
        s = sampler(c)
        seqs = [int((await take(s))["frame_seq"]) for _ in range(20)]
        assert seqs == list(range(1, 21)) and s.shed_level("D") == 0
    run(t)


def test_behind_live_sheds_secondary_then_drops_oldest_and_logs_the_gap():
    async def t(c):
        await c.delete(fs.SHED_LOG_KEY)
        key = await mission(c, "D", "M1")
        await frames(c, key, range(1, 101), age_s=5.0)          # a 5 s old backlog
        clock = Clock()
        s = sampler(c, clock=clock)
        f1 = await take(s)
        assert int(f1["frame_seq"]) == 1 and s.shed_level("D") == 1 and s.shed_secondary("D")
        s.mark_priority("D", 30)                                 # alert in progress: secondary stays on
        assert not s.shed_secondary("D")
        clock.t += 3.5                                           # still behind after 3 s → drop-oldest
        f = await take(s)
        assert int(f["frame_seq"]) == 100 and f["_gap"] == "1" and s.shed_level("D") == 2
        log = await c.xrange(fs.SHED_LOG_KEY)
        rec = {k.decode(): v.decode() for k, v in log[-1][1].items()}
        assert rec["reason"] == "drop_oldest" and rec["from_seq"] == "2" and rec["to_seq"] == "99"
        assert rec["frames"] == "98" and rec["service"] == "svc"
        assert s.snapshot()["drones"]["D"]["frames_shed"] == 98
    run(t)


def test_repeated_jumps_lower_the_cadence_and_recovery_steps_back():
    async def t(c):
        await c.delete(fs.SHED_LOG_KEY)
        key = await mission(c, "D", "M1")
        clock = Clock()
        s = sampler(c, clock=clock)
        await frames(c, key, range(1, 41), age_s=5.0)
        await take(s)                                            # level 1
        for burst in range(3):                                   # keeps falling behind
            clock.t += 3.5
            await frames(c, key, range(41 + burst * 40, 81 + burst * 40), age_s=5.0)
            await take(s)
        assert s.shed_level("D") == 3 and s._shed["D"].cadence >= 2
        # catches up: fresh frames only every other one is analysed while at level 3
        await frames(c, key, range(200, 260), age_s=0.1)
        got = [int((await take(s))["frame_seq"]) for _ in range(10)]
        assert all(q % s._shed["D"].cadence == 0 for q in got if q >= 200)
        for _ in range(8):                                       # 10 s at <= 0.5 s behind, repeatedly
            clock.t += 10.5
            await frames(c, key, range(1000 + _ * 10, 1010 + _ * 10), age_s=0.1)
            await take(s)
        assert s.shed_level("D") == 0 and s._shed["D"].cadence == 1
    run(t)
