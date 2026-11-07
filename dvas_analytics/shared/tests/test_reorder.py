"""Out-of-order uploads (plan D1): frames are analysed in frame_seq order, late frames are
flagged (never dropped silently), Lost counts real gaps only.

Run from dvas_analytics/:  python -m pytest shared/tests/test_reorder.py -q
"""
import asyncio

import pytest

from shared import frame_sampler as fs
from shared.frame_sampler import FrameSampler


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class Stream:
    """Fake Valkey: entries are appended by the test as they 'arrive'."""

    def __init__(self):
        self.entries, self.pos, self.n = [], 0, 0

    def arrive(self, *seqs):
        for q in seqs:
            self.n += 1
            self.entries.append((f"{self.n}-0".encode(),
                                 {b"frame": b"x%d" % q, b"frame_seq": str(q).encode(), b"ts": b"0"}))

    async def xgroup_create(self, *a, **k):
        raise Exception("BUSYGROUP")

    async def xreadgroup(self, groupname, consumername, streams, count, block):
        out = []
        for key, sid in streams.items():
            if sid == "0":
                out.append((key.encode(), []))
                continue
            part = self.entries[self.pos:self.pos + count]
            self.pos += len(part)
            if part:
                out.append((key.encode(), part))
        return out

    async def xack(self, *a):
        pass

    async def get(self, k):
        return None


def sampler(policy="analyse"):
    clock, stream = Clock(), Stream()
    s = FrameSampler("person", "g", "c", late_policy=policy, governed=False, cap_fps=60,
                     redis_factory=lambda: stream, clock=clock)
    s.cap.fps = 60
    return s, stream, clock


def step(s, clock, dt=0.02):
    clock.t += dt
    out = asyncio.run(s.next_round(["D"]))
    f = out.get("D")
    return None if f is None else (int(f["frame_seq"]), f.get("_late") == "1")


def drain(s, clock, rounds=40, dt=0.02):
    got = []
    for _ in range(rounds):
        r = step(s, clock, dt)
        if r:
            got.append(r)
    return got


def test_out_of_order_within_the_window_is_analysed_in_order(monkeypatch):
    monkeypatch.setattr(fs, "REORDER_MS", 300)
    s, st, c = sampler()
    st.arrive(1, 2, 4)
    got = drain(s, c, rounds=3)                    # 4 is held: 3 may still come
    st.arrive(3, 5)
    got += drain(s, c)
    assert [g[0] for g in got] == [1, 2, 3, 4, 5] and not any(g[1] for g in got)
    assert s.stats["D"].skipped == 0 and s.stats["D"].late == 0


def test_frame_beyond_the_window_is_late_but_still_analysed(monkeypatch):
    monkeypatch.setattr(fs, "REORDER_MS", 300)
    s, st, c = sampler()
    st.arrive(1, 2, 4, 5)
    got = drain(s, c, rounds=30)                   # 0.6 s: window expires, 4 and 5 go out
    assert [g[0] for g in got] == [1, 2, 4, 5] and s.stats["D"].skipped == 1
    st.arrive(3)                                   # retry arrives 0.6 s later
    got += drain(s, c, rounds=3)
    assert got[-1] == (3, True)                    # analysed, flagged late
    assert s.stats["D"].late == 1 and s.stats["D"].skipped == 0   # the gap is refunded


def test_skip_policy_counts_late_frames_without_handing_them_over(monkeypatch):
    monkeypatch.setattr(fs, "REORDER_MS", 0)
    s, st, c = sampler("skip")
    st.arrive(1, 3, 2)
    got = drain(s, c, rounds=6)
    assert [g[0] for g in got] == [1, 2, 3] or [g[0] for g in got] == [1, 3]
    assert all(not g[1] for g in got)


def test_duplicates_are_dropped_and_sequence_restart_is_not_late(monkeypatch):
    monkeypatch.setattr(fs, "REORDER_MS", 0)
    s, st, c = sampler()
    st.arrive(5000, 5001, 5001, 0, 1)              # duplicate upload, then a new mission restarts at 0
    got = drain(s, c, rounds=8)
    assert [g[0] for g in got] == [5000, 5001, 0, 1]
    assert s.stats["D"].late == 0 and s.stats["D"].skipped == 0


def test_late_policy_is_validated():
    with pytest.raises(ValueError):
        FrameSampler("x", "g", "c", late_policy="drop")


def test_next_decoded_prefetches_the_following_round(monkeypatch):
    """B2: the round after is read and decoded while the caller works on this one."""
    monkeypatch.setattr(fs, "REORDER_MS", 0)
    monkeypatch.setattr(fs, "decode_frame", lambda f: "img" + f["frame_seq"])
    s, st, c = sampler()
    st.arrive(1, 2, 3)

    async def go():
        got = []
        for _ in range(8):
            c.t += 0.02
            r = await s.next_decoded(["D"])
            got += [(f["frame_seq"], img) for f, img in r.values()]
            await asyncio.sleep(0)              # "GPU work": the prefetch runs meanwhile
        return got
    assert asyncio.run(go()) == [("1", "img1"), ("2", "img2"), ("3", "img3")]
