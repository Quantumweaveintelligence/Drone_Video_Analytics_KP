"""Crash-safe per-mission sequence numbers (plan E1 item 7, finding E-F12)."""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ingest.seq_store import SeqStore

_CHILD = r"""
import sys
sys.path.insert(0, {root!r})
from ingest.seq_store import SeqStore
store = SeqStore({dir!r}, "MSN-KILL", block=7)
with open({log!r}, "a", encoding="utf-8") as fh:
    while True:
        fh.write(f"{{store.next()}}\n")
        fh.flush()
"""


def test_numbers_strictly_increase(tmp_path: Path) -> None:
    store = SeqStore(tmp_path, "MSN-1", block=3)
    assert [store.next() for _ in range(10)] == list(range(1, 11))


def test_restart_skips_reserved_block_never_duplicates(tmp_path: Path) -> None:
    first = SeqStore(tmp_path, "MSN-1", block=5)
    handed = [first.next() for _ in range(7)]          # 1..7, reserved up to 10
    resumed = SeqStore(tmp_path, "MSN-1", block=5)
    nxt = resumed.next()
    assert nxt > max(handed)
    assert nxt == 11                                     # gap 8..10 is allowed


def test_missions_are_independent(tmp_path: Path) -> None:
    a = SeqStore(tmp_path, "MSN A/1", block=2)
    b = SeqStore(tmp_path, "MSN-B", block=2)
    assert a.next() == 1 and b.next() == 1
    assert a.path != b.path


def test_advance_past_server_last_seq(tmp_path: Path) -> None:
    store = SeqStore(tmp_path, "MSN-1", block=4)
    store.next()
    store.advance_past(50)
    assert store.next() == 51


def test_corrupt_or_foreign_file_is_refused(tmp_path: Path) -> None:
    store = SeqStore(tmp_path, "MSN-1")
    store.next()
    store.path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeError):
        SeqStore(tmp_path, "MSN-1")
    store.path.write_text(json.dumps({"mission_id": "OTHER", "seq_reserved_until": 9}), encoding="utf-8")
    with pytest.raises(RuntimeError):
        SeqStore(tmp_path, "MSN-1")


@pytest.mark.timeout(0)   # process start-up dominates; the run count bounds the time instead
def test_kill_at_random_points_never_duplicates(tmp_path: Path) -> None:
    """Kill a writer process at random moments; the next number must exceed every number handed out.

    Default 30 runs keeps the suite fast; release check: DVAS_SEQ_KILL_RUNS=1000.
    """
    runs = int(os.getenv("DVAS_SEQ_KILL_RUNS", "30"))
    root = str(Path(__file__).resolve().parents[1])
    log_path = tmp_path / "handed.log"
    code = _CHILD.format(root=root, dir=str(tmp_path / "store"), log=str(log_path))
    rng = random.Random(1234)
    for _ in range(runs):
        child = subprocess.Popen([sys.executable, "-c", code])
        time.sleep(rng.uniform(0.15, 0.4))      # interpreter start-up plus a random amount of work
        child.kill()                            # TerminateProcess on Windows: no cleanup runs
        child.wait()
    handed = [int(x) for x in log_path.read_text(encoding="utf-8").split() if x.isdigit()]
    assert handed, "child never produced numbers"
    assert len(handed) == len(set(handed)), "a sequence number was handed out twice"
    assert SeqStore(tmp_path / "store", "MSN-KILL", block=7).next() > max(handed)
