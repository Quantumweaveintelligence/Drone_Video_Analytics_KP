"""Mission drain: the stream may be deleted only when every analyzer has read every frame.

Run from dvas_ingest/ingest:  python -m pytest tests -q
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import queue_manager as qm  # noqa: E402


class FakeValkey:
    def __init__(self, groups, last="100-0", first="40-0", length=60):
        self.groups, self.last, self.first, self.length = groups, last, first, length

    async def xlen(self, key):
        return self.length

    async def xinfo_groups(self, key):
        return self.groups

    async def xinfo_stream(self, key):
        return {"last-generated-id": self.last.encode(), "length": self.length,
                "first-entry": (self.first.encode(), {})}

    async def xrange(self, key, min="-", max="+", count=None):
        return [(self.first.encode(), {})]


def manager(fake):
    m = qm.QueueManager.__new__(qm.QueueManager)
    m._client, m._connected = fake, True
    return m


def g(name, delivered, pending=0, lag=None, consumers=1):
    return {"name": name.encode(), "consumers": consumers, "pending": pending,
            "last-delivered-id": delivered.encode(), "lag": lag}


def run(c):
    return asyncio.run(c)


def test_null_lag_with_unread_frames_is_not_consumed():
    # The old check treated lag=None (stream trimmed past the group) as caught up → early purge
    m = manager(FakeValkey([g("person-group", "20-0", lag=None), g("crowd-group", "100-0", lag=0)]))
    assert run(m.is_fully_consumed("D1")) is False
    assert run(m.unread_frames("D1"))["person-group"] == 60        # everything left is unread


def test_consumed_only_when_every_reader_reached_the_last_entry():
    m = manager(FakeValkey([g("person-group", "100-0", lag=None), g("vehicle-group", "100-0", lag=0),
                            g("analytics", "0-0", consumers=0, lag=None)]))   # placeholder ignored
    assert run(m.is_fully_consumed("D1")) is True
    m = manager(FakeValkey([g("person-group", "100-0", pending=1)]))
    assert run(m.is_fully_consumed("D1")) is False
    m = manager(FakeValkey([g("person-group", "99-5", lag=None)]))
    assert run(m.is_fully_consumed("D1")) is False


def test_no_reader_never_counts_as_consumed_and_remaining_falls_back_to_length():
    fake = FakeValkey([g("analytics", "0-0", consumers=0)])
    m = manager(fake)
    assert run(m.is_fully_consumed("D1")) is False

    async def depth(_d):
        return 60
    m.get_queue_depth = depth
    assert run(m.remaining_frames("D1")) == 60


def test_remaining_is_the_slowest_reader():
    m = manager(FakeValkey([g("person-group", "50-0", pending=2, lag=30), g("vehicle-group", "100-0", lag=0)]))
    assert run(m.remaining_frames("D1")) == 32


def test_ids_compare_numerically():
    assert qm._parse_id(b"1700000000000-10") > qm._parse_id("1700000000000-9")
    assert qm._parse_id(None) is None


def test_last_id_comes_from_our_own_xadd_without_fetching_a_frame():
    fake = FakeValkey([g("person-group", "100-0", lag=0)], last="999-0")

    async def no_xinfo_stream(key):
        raise AssertionError("XINFO STREAM returns whole frames; must not be needed")
    fake.xinfo_stream = no_xinfo_stream
    m = manager(fake)
    m._last_entry = {"D1": "100-0"}
    assert run(m.is_fully_consumed("D1")) is True
    m._last_entry = {"D1": "101-0"}                     # one more frame arrived
    assert run(m.is_fully_consumed("D1")) is False


def test_remaining_is_cached_briefly():
    fake = FakeValkey([g("person-group", "50-0", lag=30)])
    m = manager(fake)
    assert run(m.remaining_frames("D1")) == 30
    fake.groups = [g("person-group", "100-0", lag=0)]
    assert run(m.remaining_frames("D1")) == 30          # within 1 s: cached
    m._remaining_cache["D1"] = (0.0, 30)                 # expire
    assert run(m.remaining_frames("D1")) == 0
