"""Bounded offline buffer: ordering, spill, caps, drop policy, event records (plan E1 items 3 and 8)."""
from __future__ import annotations

import sqlite3
from collections import namedtuple
from pathlib import Path

import pytest

from ingest.offline_buffer import EventStoreFull, OfflineBuffer

_Usage = namedtuple("_Usage", "total used free")
_GIB = 1024 ** 3


class _Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


def _buf(tmp_path: Path, free: int = 100 * _GIB, **kw) -> OfflineBuffer:
    usage = kw.pop("usage", lambda _p: _Usage(200 * _GIB, 100 * _GIB, free))
    return OfflineBuffer(
        db_path=str(tmp_path / "b.db"),
        frames_dir=str(tmp_path / "frames"),
        mission_id=kw.pop("mission_id", "MSN-1"),
        disk_usage=usage,
        commit_interval_s=0.0,
        **kw,
    )


def _drain(buf: OfflineBuffer) -> list:
    out = []
    while (item := buf.get(timeout=0)) is not None:
        out.append(item[0])
        buf.commit(item[0])
    return out


def test_frames_come_out_in_seq_order(tmp_path: Path) -> None:
    buf = _buf(tmp_path)
    for seq in (5, 3, 4, 1, 2):
        buf.put("raw", b"x", {"seq": seq}, seq=seq)
    assert _drain(buf) == [1, 2, 3, 4, 5]


def test_order_holds_across_memory_and_disk(tmp_path: Path) -> None:
    buf = _buf(tmp_path, memory_frames=2)
    for seq in range(1, 7):
        buf.put("raw", b"jpeg%d" % seq, {"seq": seq}, seq=seq)
    assert buf.stats()["disk_frames"] == 4 and buf.stats()["memory_frames"] == 2
    assert _drain(buf) == [1, 2, 3, 4, 5, 6]
    assert buf.stats()["disk_bytes"] == 0


def test_exclude_ids_skips_in_flight_frames(tmp_path: Path) -> None:
    buf = _buf(tmp_path)
    for seq in (1, 2, 3):
        buf.put("raw", b"x", {"seq": seq}, seq=seq)
    assert buf.get(timeout=0, exclude_ids=[1, 2])[0] == 3


def test_cap_evicts_lowest_score_first(tmp_path: Path) -> None:
    buf = _buf(tmp_path, memory_frames=1, max_bytes=300)
    scores = {1: 0.9, 2: 0.1, 3: 0.5, 4: 0.8, 5: 0.7}
    for seq, score in scores.items():
        buf.put("selected", b"x" * 100, {"seq": seq}, seq=seq, score=score)
    buf.spill_all()
    kept = set(_drain(buf))
    assert kept == {1, 4, 5}                # 0.1 and 0.5 evicted
    assert buf.dropped_total == 2


def test_new_frame_with_lowest_score_is_the_one_dropped(tmp_path: Path) -> None:
    buf = _buf(tmp_path, memory_frames=1, max_bytes=200)
    for seq, score in ((1, 0.9), (2, 0.8), (3, 0.05)):
        buf.put("selected", b"x" * 100, {"seq": seq}, seq=seq, score=score)
    buf.spill_all()
    assert set(_drain(buf)) == {1, 2}


def test_cap_is_also_limited_by_share_of_free_disk(tmp_path: Path) -> None:
    buf = _buf(tmp_path, free=1000, max_bytes=10 * _GIB, min_free_bytes=0)
    assert buf.cap_bytes == 200             # 20 % of free disk at start


def test_free_disk_floor_is_respected(tmp_path: Path) -> None:
    free = {"v": 5 * _GIB + 150}
    buf = _buf(tmp_path, memory_frames=1, usage=lambda _p: _Usage(0, 0, free["v"]))
    buf.put("raw", b"x" * 100, {"seq": 1}, seq=1, score=0.5)
    buf.put("raw", b"x" * 100, {"seq": 2}, seq=2, score=0.6)   # spills seq 1: 150 - 100 >= 0 ok
    free["v"] = 5 * _GIB + 50                                   # now below floor + frame size
    buf.put("raw", b"x" * 100, {"seq": 3}, seq=3, score=0.7)   # spill of seq 2 must evict seq 1
    stats = buf.stats()
    assert stats["dropped_total"] >= 1


def test_old_frames_expire(tmp_path: Path) -> None:
    clock = _Clock()
    buf = _buf(tmp_path, memory_frames=1, max_age_s=60, clock=clock)
    buf.put("raw", b"x", {"seq": 1}, seq=1)
    buf.put("raw", b"x", {"seq": 2}, seq=2)          # seq 1 on disk
    clock.t += 120
    buf.put("raw", b"x", {"seq": 3}, seq=3)          # spill triggers expiry of seq 1
    assert 1 not in _drain(buf)


def test_events_are_kept_and_refused_when_full(tmp_path: Path) -> None:
    buf = _buf(tmp_path, max_events=3)
    for seq in (1, 2, 3):
        buf.put_event(seq, {"seq": seq, "score": 0.9})
    with pytest.raises(EventStoreFull):
        buf.put_event(4, {"seq": 4})
    events = buf.get_events()
    assert [e[1]["seq"] for e in events] == [1, 2, 3]
    buf.commit_events([e[0] for e in events])
    assert buf.get_events() == []


def test_clear_keeps_current_mission(tmp_path: Path) -> None:
    old = _buf(tmp_path, mission_id="OLD", memory_frames=1)
    old.put("raw", b"x", {"seq": 1}, seq=1)
    old.put_event(1, {"seq": 1})
    old.close()                                       # spills to disk
    cur = _buf(tmp_path, mission_id="CUR", memory_frames=1)
    cur.put("raw", b"y", {"seq": 1}, seq=1)
    cur.spill_all()
    cur.clear(keep_mission_id="CUR")
    assert cur.stats()["disk_frames"] == 1
    assert cur.stats()["events_pending"] == 0


def test_survives_restart(tmp_path: Path) -> None:
    buf = _buf(tmp_path)
    buf.put("raw", b"payload", {"seq": 9, "capture_ts": 12.5}, seq=9)
    buf.close()
    again = _buf(tmp_path)
    frame_id, kind, data, tel = again.get(timeout=0)
    assert (frame_id, kind, data, tel["capture_ts"]) == (9, "raw", b"payload", 12.5)


def test_v1_store_is_discarded_on_upgrade(tmp_path: Path) -> None:
    (tmp_path / "frames").mkdir()
    old_file = tmp_path / "frames" / "old.jpg"
    old_file.write_bytes(b"x")
    con = sqlite3.connect(str(tmp_path / "b.db"))
    con.execute("CREATE TABLE frames (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, telemetry_json TEXT, frame_path TEXT, created_at TIMESTAMP)")
    con.execute("INSERT INTO frames (kind, telemetry_json, frame_path) VALUES ('raw', '{}', ?)", (str(old_file),))
    con.commit()
    con.close()
    buf = _buf(tmp_path)
    assert buf.get(timeout=0) is None
    assert not old_file.exists()
