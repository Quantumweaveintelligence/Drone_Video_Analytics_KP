"""v2 R1 4a — frames are acknowledged after analysis, so a restarted service re-reads the frames
it had not finished (none is lost); a video mission's stream stays current until its decoder
has fed every recorded frame.

Needs a real Valkey (skipped otherwise), as test_mission_streams.py:
  TEST_VALKEY_URL=redis://127.0.0.1:6392/0 python -m pytest shared/tests/test_ack_after_analysis.py -q
"""
import asyncio
import time

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from shared import frame_sampler as fs, mission_streams as ms
from shared.tests.test_mission_streams import URL, _reachable, mission, run, sampler  # noqa: F401

pytestmark = pytest.mark.skipif(not _reachable(), reason=f"no Valkey at {URL}")
JPEG = cv2.imencode(".jpg", np.zeros((16, 16, 3), np.uint8))[1].tobytes()


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(fs, "RESOLVE_EVERY_S", 0.0)
    monkeypatch.setattr(fs, "REORDER_MS", 0)


async def jpeg_frames(c, key, seqs, bad=()):
    for q in seqs:
        await c.xadd(key, {"frame": b"not a jpeg" if q in bad else JPEG, "frame_seq": str(q), "ts": "0"})


async def pending(c, key, group="svc-group"):
    return int((await c.xpending(key, group))["pending"])


def test_a_frame_is_acknowledged_when_the_next_one_is_asked_for():
    async def t(c):
        key = await mission(c, "D", "M1", seqs=range(1, 4))
        s = sampler(c)
        got = []
        while not got:
            got = list((await s.next_round(["D"])).values())
        assert int(got[0]["frame_seq"]) == 1 and await pending(c, key) == 1   # handed out, not done
        while not await s.next_round(["D"]):
            pass
        info = await c.xpending_range(key, "svc-group", min="-", max="+", count=10)
        assert [int((await c.xrange(key, i["message_id"], i["message_id"]))[0][1][b"frame_seq"])
                for i in info] == [2]                                      # 1 acked, 2 in service
        await s.done()
        assert await pending(c, key) == 0
    run(t)


def test_a_restarted_service_re_reads_unfinished_frames_and_loses_none():
    async def t(c):
        key = await mission(c, "D", "M1")
        await jpeg_frames(c, key, range(1, 11))
        a = sampler(c)
        seen = []
        while len(seen) < 3:
            for _d, (f, _img) in (await a.next_decoded(["D"])).items():
                seen.append(int(f["frame_seq"]))
        await asyncio.sleep(0.05)                       # the prefetch has read frame 4 meanwhile
        a._prefetch.cancel()                            # crash: no done(), frame 3 never acked
        b = sampler(c)                                  # same group and consumer name
        for _ in range(60):
            for _d, (f, _img) in (await b.next_decoded(["D"])).items():
                seen.append(int(f["frame_seq"]))
            if seen[-1:] == [10]:
                break
        assert sorted(set(seen)) == list(range(1, 11))
        assert seen.count(3) == 2 and len(seen) == 11   # only the frame in service is repeated
        await b.done()
        assert await pending(c, key) == 0
    run(t)


def test_an_undecodable_frame_is_acknowledged_not_left_pending():
    async def t(c):
        key = await mission(c, "D", "M1")
        await jpeg_frames(c, key, range(1, 4), bad={2})
        s = sampler(c)
        seen = []
        for _ in range(30):
            for _d, (f, _img) in (await s.next_decoded(["D"])).items():
                seen.append(int(f["frame_seq"]))
        await s.done()
        assert seen == [1, 3] and await pending(c, key) == 0
    run(t)


def test_video_mission_stays_current_until_its_decoder_is_done():
    async def t(c):
        old = time.time() - 600
        key = await mission(c, "D", "M1", status="draining", started=old, ended=old + 10)
        await c.hset(ms.video_key(ms.sid_of("M1")), mapping={"status": "ended", "decoder_done": 0})
        assert await ms.is_live(c, key)                # still being fed from the recording
        await c.hset(ms.video_key(ms.sid_of("M1")), "decoder_done", 1)
        assert not await ms.is_live(c, key)
    run(t)


def test_video_frames_report_lag_from_server_receive_time():
    async def t(c):
        key = await mission(c, "D", "M1")
        await c.xadd(key, {"frame": b"x", "frame_seq": "1", "ts": str(int(time.time() * 1000)),
                           "rx_ts_ms": str(int((time.time() - 30) * 1000))})
        s = sampler(c)
        for _ in range(10):
            if await s.next_round(["D"]):
                break
        assert s.stats["D"].lag_ms >= 29_000            # waited 30 s on disk, not 0 s in Valkey
    run(t)
