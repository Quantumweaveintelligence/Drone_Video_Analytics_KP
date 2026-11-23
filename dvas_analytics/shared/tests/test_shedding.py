"""Overload shedding (v2 plan R6.3, R11.2): a service whose frames wait longer than its
threshold sheds instead of queueing — secondary stages off, then (sustained) a lower cadence,
and drop-oldest only for a stall — and comes back down when it keeps up again. A drone at live
pace is untouched; a stream restart starts afresh from the newest frame.

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
    monkeypatch.setattr(fs, "SHED_WARMUP_S", 0.0)
    monkeypatch.setattr(fs, "STREAM_PAUSE_S", 1e9)      # the fake clock jumps; only the pause test uses it


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


async def frames(c, key, seqs, age_s):
    """Frames that entered the stream age_s seconds ago (the entry id's time, which the
    service's wait is measured from); rx_ts_ms as the video decoder writes it."""
    rx = int((time.time() - age_s) * 1000)
    last = await c.xrevrange(key, count=1)
    ms = max(rx, int(last[0][0].decode().split("-")[0]) if last else 0)
    for q in seqs:
        await c.xadd(key, {"frame": b"x", "frame_seq": str(q), "rx_ts_ms": str(rx), "ts": str(rx)},
                     id=f"{ms}-*")


async def take(s, drone="D"):
    for _ in range(50):
        if isinstance(s._clock, Clock):
            s._clock.t += 0.05                                   # one frame interval: the rate cap lets it out
        got = await s.next_round([drone])
        if got:
            return got[drone]
    return None


def shed_sampler(c, clock, **kw):
    s = sampler(c, clock=clock, **kw)
    s._shed_base = 1.5
    return s


async def log_recs(c):
    return [{k.decode(): v.decode() for k, v in e[1].items()} for e in await c.xrange(fs.SHED_LOG_KEY)]


def test_a_live_drone_is_never_shed():
    async def t(c):
        key = await mission(c, "D", "M1")
        await frames(c, key, range(1, 21), age_s=0.2)
        s = shed_sampler(c, Clock())
        seqs = [int((await take(s))["frame_seq"]) for _ in range(20)]
        assert seqs == list(range(1, 21)) and s.shed_level("D") == 0
    run(t)


def test_a_restart_with_an_old_backlog_continues_from_the_newest_frame():
    """Pod 18 Nov: intrusion restarted 8.1 s behind (its old queue position) and went to
    level 3. Now it starts at the newest frame, at level 0, with one `restart` range logged."""
    async def t(c):
        await c.delete(fs.SHED_LOG_KEY)
        key = await mission(c, "D", "M1")
        await frames(c, key, range(1, 101), age_s=8.0)
        await frames(c, key, [101], age_s=0.1)
        s = shed_sampler(c, Clock())
        f = await take(s)
        assert int(f["frame_seq"]) == 101 and f["_gap"] == "1" and s.shed_level("D") == 0
        rec = (await log_recs(c))[-1]
        assert rec["reason"] == "restart" and rec["from_seq"] == "1" and rec["frames"] == "100"
    run(t)


def test_sustained_overload_steps_down_the_cadence_without_dropping_frames():
    async def t(c):
        await c.delete(fs.SHED_LOG_KEY)
        key = await mission(c, "D", "M1")
        clock = Clock()
        s = shed_sampler(c, clock)
        await frames(c, key, [1], age_s=2.0)
        await take(s)                                            # over 1.5 s, not yet 2 s long
        assert s.shed_level("D") == 0
        clock.t += 2.5
        await frames(c, key, [2], age_s=2.0)
        await take(s)
        assert s.shed_level("D") == 1 and s.shed_secondary("D")
        clock.t += 3.5                                           # still 2 s behind, not shrinking
        await frames(c, key, [3, 4], age_s=2.1)
        await take(s)
        assert s.shed_level("D") == 3 and s._shed["D"].cadence == 2
        got = []
        await frames(c, key, range(5, 13), age_s=2.1)
        for _ in range(4):
            got.append(int((await take(s))["frame_seq"]))
        assert all(q % 2 == 0 for q in got)                       # every 2nd frame, in order
        recs = await log_recs(c)
        assert not [r for r in recs if r["reason"] == "drop_oldest"]
        levels = [(r["from_level"], r["to_level"], r["cadence"]) for r in recs if r["reason"] == "level"]
        assert levels == [("0", "1", "1"), ("1", "3", "2")]
    run(t)


def test_the_cadence_stops_at_the_service_limit():
    async def t(c):
        key = await mission(c, "D", "M1")
        clock = Clock()
        s = shed_sampler(c, clock)
        s._shed_max_cadence = 2                                  # person / ANPR / vehicle
        q = 1
        for _ in range(6):
            await frames(c, key, range(q, q + 4), age_s=2.2)
            q += 4
            clock.t += 3.5
            await take(s)
        assert s._shed["D"].cadence == 2 and s.shed_level("D") == 3
    run(t)


def test_a_wait_that_shrinks_keeps_the_cadence():
    async def t(c):
        key = await mission(c, "D", "M1")
        clock = Clock()
        s = shed_sampler(c, clock)
        await frames(c, key, [1], age_s=2.8)
        await take(s)
        clock.t += 2.5
        await frames(c, key, [2], age_s=2.8)
        await take(s)                                            # level 1 at 2.8 s
        clock.t += 3.5
        await frames(c, key, [3], age_s=2.0)                     # catching up by itself
        await take(s)
        assert s.shed_level("D") == 1 and s._shed["D"].cadence == 1
    run(t)


def test_a_stall_drops_the_oldest_and_logs_the_gap():
    async def t(c):
        await c.delete(fs.SHED_LOG_KEY)
        key = await mission(c, "D", "M1")
        clock = Clock()
        s = shed_sampler(c, clock)
        await frames(c, key, [1], age_s=0.1)
        await take(s)
        clock.t += 1.0
        await frames(c, key, range(2, 101), age_s=0.1)
        # the service stalled: its next frames are now > 2 × 1.5 s old
        await c.delete(key)
        await frames(c, key, range(2, 101), age_s=4.0)
        await c.xgroup_create(key, "svc-group", id="0")
        f = await take(s)
        assert int(f["frame_seq"]) == 100 and f["_gap"] == "1" and s.shed_level("D") == 2
        rec = [r for r in await log_recs(c) if r["reason"] == "drop_oldest"][-1]
        assert rec["from_seq"] == "2" and rec["to_seq"] == "99" and rec["frames"] == "98"
        assert s.snapshot()["drones"]["D"]["frames_shed"] == 98
    run(t)


def test_no_jump_when_the_frame_is_already_the_newest():
    """Pod 18 Nov: 'dropped 0 frames' jumps every 3 s, each counted toward a lower cadence."""
    async def t(c):
        await c.delete(fs.SHED_LOG_KEY)
        key = await mission(c, "D", "M1")
        clock = Clock()
        s = shed_sampler(c, clock)
        await frames(c, key, [1], age_s=0.1)
        await take(s)
        clock.t += 1.0
        await frames(c, key, [2], age_s=4.0 - 0.0)               # one old frame, nothing behind it
        f = await take(s)
        assert int(f["frame_seq"]) == 2 and "_gap" not in f
        assert not [r for r in await log_recs(c) if r["reason"] in ("drop_oldest", "restart")]
        assert s._shed["D"].frames_shed == 0
    run(t)


def test_recovery_steps_back_to_live():
    async def t(c):
        key = await mission(c, "D", "M1")
        clock = Clock()
        s = shed_sampler(c, clock)
        await frames(c, key, [1], age_s=0.1)
        await take(s)
        st = s._shed["D"]
        st.level, st.cadence = 3, 4
        q = 2
        for _ in range(8):                                       # 10 s at <= 0.5 s behind, repeatedly
            clock.t += 10.5
            await frames(c, key, range(q, q + 8), age_s=0.1)
            q += 8
            await take(s)
        assert s.shed_level("D") == 0 and s._shed["D"].cadence == 1
    run(t)


def test_a_stream_pause_starts_the_shed_state_afresh(monkeypatch):
    monkeypatch.setattr(fs, "STREAM_PAUSE_S", 10.0)
    async def t(c):
        key = await mission(c, "D", "M1")
        clock = Clock()
        s = shed_sampler(c, clock)
        await frames(c, key, [1], age_s=0.1)
        await take(s)
        st = s._shed["D"]
        st.level, st.cadence, st.frames_shed = 3, 2, 500
        clock.t += 30                                            # /stream/stop … /stream/start
        await frames(c, key, [2, 3], age_s=0.1)
        await take(s)
        assert s.shed_level("D") == 0 and s._shed["D"].cadence == 1
        assert s.snapshot()["drones"]["D"]["frames_shed"] == 0
    run(t)


def test_a_new_mission_starts_unshed_and_the_old_cadence_range_stays_with_its_stream():
    async def t(c):
        await c.delete(fs.SHED_LOG_KEY)
        key1 = await mission(c, "D", "M1")
        clock = Clock()
        s = shed_sampler(c, clock)
        await frames(c, key1, [1], age_s=0.1)
        await take(s)
        st = s._shed["D"]
        st.level, st.cadence = 3, 2
        await frames(c, key1, range(2, 10), age_s=0.1)
        for _ in range(3):
            await take(s)
        assert s._shed["D"].pending                              # a cadence range not yet logged
        await c.hset(fs.mission_streams.index_key(fs.mission_streams.sid_of("M1")), "status", "ended")
        await c.delete(key1)
        key2 = await mission(c, "D", "M2")
        await frames(c, key2, range(1, 11), age_s=0.1)
        got = [int((await take(s))["frame_seq"]) for _ in range(6)]
        assert got == [1, 2, 3, 4, 5, 6]                         # every frame again
        assert s.shed_level("D") == 0 and s._shed["D"].cadence == 1 and s._shed["D"].stream == key2
        cad = [r for r in await log_recs(c) if r["reason"] == "cadence"]
        assert cad and all(r["stream"] == key1 and int(r["to_seq"]) >= int(r["from_seq"]) for r in cad)
    run(t)


def test_the_wait_counts_from_the_analytics_stream_not_from_the_server_receiving_it():
    """The decoder's own delay (received 3 s ago, published just now) is not the service
    falling behind: nothing is shed."""
    async def t(c):
        key = await mission(c, "D", "M1")
        rx = int((time.time() - 3.0) * 1000)
        for q in range(1, 11):
            await c.xadd(key, {"frame": b"x", "frame_seq": str(q), "rx_ts_ms": str(rx), "ts": str(rx)})
        s = shed_sampler(c, Clock())
        got = [int((await take(s))["frame_seq"]) for _ in range(10)]
        assert got == list(range(1, 11)) and s.shed_level("D") == 0
    run(t)


def test_no_frames_are_dropped_while_the_service_warms_up(monkeypatch):
    monkeypatch.setattr(fs, "SHED_WARMUP_S", 10.0)
    async def t(c):
        key = await mission(c, "D", "M1")
        clock = Clock()
        s = shed_sampler(c, clock)
        await frames(c, key, [1], age_s=0.1)
        await take(s)
        clock.t += 1.0
        await c.delete(key)                                      # its next frames are 5 s old
        await frames(c, key, range(2, 101), age_s=5.0)
        await c.xgroup_create(key, "svc-group", id="0")
        assert int((await take(s))["frame_seq"]) == 2              # 5 s behind, warming: no drop
        clock.t += 9.5                                           # warmed up, still behind
        f = await take(s)
        assert f["_gap"] == "1" and s.shed_level("D") == 2
    run(t)


def test_a_slow_service_gets_a_higher_threshold():
    s = fs.FrameSampler("vessel", "g", "c")
    s._shed_base = 1.5
    s.record("D", total_ms=50)
    assert s._threshold("D") == 1.5                              # 10 × 50 ms < 1.5 s
    s.stats["D"].total_ms = 250
    assert s._threshold("D") == 2.5
    s.stats["D"].total_ms = 2000
    assert s._threshold("D") == fs.SHED_COST_MAX_S


def test_a_threshold_below_one_second_is_refused(monkeypatch):
    monkeypatch.setenv("DVAS_SHED_AFTER_S_TEST", "0.3")
    assert fs._shed_after("DVAS_SHED_AFTER_S_TEST", 1.5) == fs.SHED_DEFAULT_S
    monkeypatch.setenv("DVAS_SHED_AFTER_S_TEST", "0")
    assert fs._shed_after("DVAS_SHED_AFTER_S_TEST", 1.5) == 0.0   # 0 = shedding off, as before
    monkeypatch.setenv("DVAS_SHED_AFTER_S_TEST", "2.5")
    assert fs._shed_after("DVAS_SHED_AFTER_S_TEST", 1.5) == 2.5


def test_per_service_cadence_limits(monkeypatch):
    assert fs.FrameSampler("person", "g", "c")._shed_max_cadence == 2
    assert fs.FrameSampler("anpr", "g", "c")._shed_max_cadence == 2
    assert fs.FrameSampler("vehicle", "g", "c")._shed_max_cadence == 2
    assert fs.FrameSampler("intrusion", "g", "c")._shed_max_cadence == 4
    monkeypatch.setenv("DVAS_SHED_MAX_CADENCE_PERSON", "1")
    assert fs.FrameSampler("person", "g", "c")._shed_max_cadence == 1


def test_capture_to_result_delay_is_published():
    """Stress test (13 Nov): capture → result p50 / p95 per drone in the published stats."""
    import time as _t
    s = fs.FrameSampler("person", "g", "c")
    now = _t.time()
    for i in range(20):
        s._remember([{"drone_id": "D1", "capture_ts": now - 0.5, "_stream": "k", "entry_id": f"{i}-0"}])
        s.record("D1", infer_ms=10, total_ms=20)
    snap = s.snapshot()["drones"]["D1"]
    assert 450 <= snap["e2e_p50_ms"] <= 1500 and snap["e2e_p95_ms"] >= snap["e2e_p50_ms"]
    s2 = fs.FrameSampler("person", "g", "c")
    s2.record("D2")                                         # no capture known: nothing published
    assert "e2e_p95_ms" not in s2.snapshot()["drones"]["D2"]
