"""
offline_buffer.py
=================
Bounded send queue for the edge uplink: memory first, disk only when needed.

- Live frames sit in a small in-memory queue (no disk I/O on the normal send path).
- Frames spill to disk (JPEG file + SQLite row) when the memory queue is full, when the
  link is down (spill_all) and at shutdown.
- The disk store is capped by size, by age and by a free-disk floor. When it is full,
  the frame with the lowest cognitive score is dropped first, then the oldest.
- CFS event records live in their own table and are never dropped silently: when the
  record cap is reached, new records are refused with EventStoreFull.
- Frames are handed out in sequence order (lowest seq first).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

log = logging.getLogger("offline_buffer")

_SCHEMA_VERSION = 2
_GIB = 1024 ** 3


class EventStoreFull(RuntimeError):
    """Raised when the event-record cap is reached. Records are never dropped silently."""


@dataclass
class _MemEntry:
    kind: str
    jpeg: bytes
    telemetry: dict
    score: float
    mission_id: str


class OfflineBuffer:
    """Thread-safe, bounded, sequence-ordered frame queue with a durable disk spill."""

    def __init__(
        self,
        db_path: str = "buffer_storage/buffer.db",
        frames_dir: str = "buffer_storage/frames",
        mission_id: str = "",
        memory_frames: int = 150,
        max_bytes: int = 2048 * 1024 * 1024,
        max_disk_share: float = 0.20,
        max_age_s: float = 24 * 3600,
        min_free_bytes: int = 5 * _GIB,
        max_events: int = 1_000_000,
        commit_interval_s: float = 0.5,
        clock: Callable[[], float] = time.time,
        disk_usage: Callable[[str], object] = shutil.disk_usage,
    ) -> None:
        self.db_path = Path(db_path)
        self.frames_dir = Path(frames_dir)
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.mission_id = mission_id
        self._memory_frames = max(1, memory_frames)
        self._max_age_s = max_age_s
        self._min_free = min_free_bytes
        self._max_events = max_events
        self._commit_interval = commit_interval_s
        self._clock = clock
        self._disk_usage = disk_usage
        free_at_start = self._disk_usage(str(self.frames_dir)).free
        self.cap_bytes = int(min(max_bytes, free_at_start * max_disk_share))

        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._mem: Dict[int, _MemEntry] = {}
        self._next_auto_id = 1
        self._dirty = False
        self._last_commit = self._clock()
        self.dropped_total = 0
        self.rejected_total = 0

        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, isolation_level=None)
        self._init_db()
        self._disk_count, self._disk_bytes = self._disk_totals()

    # ── Schema ────────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with self._lock:
            c = self._conn
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            version = c.execute("PRAGMA user_version").fetchone()[0]
            if version < _SCHEMA_VERSION:
                # v1 rows carry no seq/score/mission: they cannot be ordered or attributed.
                old = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='frames'").fetchone()
                if old:
                    for (path,) in c.execute("SELECT frame_path FROM frames").fetchall():
                        _unlink_quiet(path)
                    c.execute("DROP TABLE frames")
                    log.info("offline_buffer_v1_store_discarded")
            c.execute(
                """CREATE TABLE IF NOT EXISTS frames (
                       id INTEGER NOT NULL,
                       mission_id TEXT NOT NULL,
                       kind TEXT NOT NULL,
                       seq INTEGER NOT NULL,
                       score REAL NOT NULL,
                       telemetry_json TEXT NOT NULL,
                       frame_path TEXT NOT NULL,
                       size INTEGER NOT NULL,
                       created_at REAL NOT NULL)"""
            )
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS frames_identity ON frames(mission_id, id)")
            c.execute("CREATE INDEX IF NOT EXISTS frames_seq ON frames(mission_id, seq)")
            c.execute("CREATE INDEX IF NOT EXISTS frames_score ON frames(score, seq)")
            c.execute(
                """CREATE TABLE IF NOT EXISTS events (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       mission_id TEXT NOT NULL,
                       seq INTEGER NOT NULL,
                       payload_json TEXT NOT NULL,
                       created_at REAL NOT NULL)"""
            )
            c.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")

    def _disk_totals(self) -> Tuple[int, int]:
        with self._lock:
            n, size = self._conn.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM frames").fetchone()
            return int(n), int(size)

    # ── Transactions (batched) ────────────────────────────────────────────────

    def _write(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        if not self._dirty:
            self._conn.execute("BEGIN")
            self._dirty = True
        return self._conn.execute(sql, args)

    def _maybe_commit(self, force: bool = False) -> None:
        now = self._clock()
        if self._dirty and (force or now - self._last_commit >= self._commit_interval):
            self._conn.execute("COMMIT")
            self._dirty = False
            self._last_commit = now

    def flush(self) -> None:
        with self._lock:
            self._maybe_commit(force=True)

    # ── Frames ────────────────────────────────────────────────────────────────

    def put(
        self,
        kind: str,
        jpeg_bytes: bytes,
        telemetry: Optional[dict] = None,
        seq: Optional[int] = None,
        score: float = 0.0,
    ) -> int:
        """Queue a frame. Returns its id (the seq when one is given)."""
        tel = dict(telemetry or {})
        with self._cond:
            if seq is None:
                frame_id = self._next_auto_id
            else:
                frame_id = int(seq)
            self._next_auto_id = max(self._next_auto_id, frame_id + 1)
            self._mem[frame_id] = _MemEntry(kind, jpeg_bytes, tel, float(score), self.mission_id)
            while len(self._mem) > self._memory_frames:
                self._spill_one(min(self._mem))
            self._maybe_commit()
            self._cond.notify_all()
            return frame_id

    def get(self, timeout: float = 1.0, exclude_ids: Optional[List[int]] = None) -> Optional[Tuple[int, str, bytes, dict]]:
        """Lowest-seq frame not in exclude_ids, as (frame_id, kind, jpeg_bytes, telemetry), or None on timeout."""
        excluded = set(exclude_ids or ())
        deadline = self._clock() + max(0.0, timeout)
        with self._cond:
            while True:
                self._maybe_commit()
                item = self._take_lowest(excluded)
                if item is not None:
                    return item
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return None
                self._cond.wait(timeout=remaining)

    def _take_lowest(self, excluded: set) -> Optional[Tuple[int, str, bytes, dict]]:
        mem_ids = [i for i in self._mem if i not in excluded]
        mem_min = min(mem_ids) if mem_ids else None
        disk_row = self._lowest_disk_row(excluded) if self._disk_count else None
        if disk_row is not None and (mem_min is None or disk_row[0] < mem_min):
            rowid, frame_id, kind, tel_json, path = disk_row
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
            except FileNotFoundError:
                log.warning("offline_buffer_file_missing id=%s", frame_id)
                self._delete_disk_row(rowid, path)
                return self._take_lowest(excluded)
            return frame_id, kind, data, json.loads(tel_json)
        if mem_min is not None:
            e = self._mem[mem_min]
            return mem_min, e.kind, e.jpeg, dict(e.telemetry)
        return None

    def _lowest_disk_row(self, excluded: set) -> Optional[tuple]:
        if excluded:
            marks = ",".join("?" * len(excluded))
            sql = ("SELECT rowid, id, kind, telemetry_json, frame_path FROM frames "  # noqa: S608 - only "?" marks are formatted in
                   f"WHERE mission_id = ? AND id NOT IN ({marks}) ORDER BY seq LIMIT 1")
            return self._conn.execute(sql, (self.mission_id, *excluded)).fetchone()
        return self._conn.execute(
            "SELECT rowid, id, kind, telemetry_json, frame_path FROM frames WHERE mission_id = ? ORDER BY seq LIMIT 1",
            (self.mission_id,),
        ).fetchone()

    def commit(self, frame_id: int) -> None:
        """The frame was delivered: forget it."""
        with self._lock:
            if self._mem.pop(frame_id, None) is not None:
                return
            row = self._conn.execute(
                "SELECT rowid, frame_path FROM frames WHERE mission_id = ? AND id = ?", (self.mission_id, frame_id)
            ).fetchone()
            if row:
                self._delete_disk_row(row[0], row[1])
            self._maybe_commit()

    def discard(self, frame_id: int, reason: str) -> None:
        """The server refused the frame permanently (e.g. 4xx): drop it and count it."""
        self.commit(frame_id)
        with self._lock:
            self.rejected_total += 1
        log.warning("offline_buffer_frame_rejected id=%s reason=%s", frame_id, reason)

    def spill_all(self) -> None:
        """Move every in-memory frame to disk (link down or shutdown)."""
        with self._lock:
            for frame_id in sorted(self._mem):
                self._spill_one(frame_id)
            self._maybe_commit(force=True)

    def _spill_one(self, frame_id: int) -> None:
        e = self._mem.pop(frame_id)
        size = len(e.jpeg)
        self._expire_old()
        if not self._make_room(size, e.score, e.telemetry.get("seq", frame_id)):
            self.dropped_total += 1
            log.warning("offline_buffer_dropped_new id=%s score=%.3f (store full of higher scores)", frame_id, e.score)
            return
        existing = self._conn.execute(
            "SELECT rowid, frame_path FROM frames WHERE mission_id = ? AND id = ?", (e.mission_id, frame_id)
        ).fetchone()
        if existing:
            self._delete_disk_row(existing[0], existing[1])
        path = self.frames_dir / f"{uuid.uuid4().hex}.jpg"
        with open(path, "wb") as fh:
            fh.write(e.jpeg)
        self._write(
            "INSERT INTO frames (id, mission_id, kind, seq, score, telemetry_json, frame_path, size, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (frame_id, e.mission_id, e.kind, int(e.telemetry.get("seq", frame_id)), e.score,
             json.dumps(e.telemetry), str(path), size, self._clock()),
        )
        self._disk_count += 1
        self._disk_bytes += size

    def _space_ok(self, size: int) -> bool:
        if self._disk_bytes + size > self.cap_bytes:
            return False
        return self._disk_usage(str(self.frames_dir)).free - size >= self._min_free

    def _make_room(self, size: int, score: float, seq: int) -> bool:
        """Evict lowest-score (then oldest) frames until `size` fits. False if the new frame loses."""
        while not self._space_ok(size):
            victim = self._conn.execute(
                "SELECT rowid, frame_path, score, seq FROM frames ORDER BY score ASC, seq ASC LIMIT 1"
            ).fetchone()
            if victim is None:
                return False
            v_id, v_path, v_score, v_seq = victim
            if (score, seq) < (v_score, v_seq):
                return False
            self._delete_disk_row(v_id, v_path)
            self.dropped_total += 1
            log.warning("offline_buffer_evicted id=%s score=%.3f", v_id, v_score)
        return True

    def _expire_old(self) -> None:
        cutoff = self._clock() - self._max_age_s
        for v_id, v_path in self._conn.execute("SELECT rowid, frame_path FROM frames WHERE created_at < ?", (cutoff,)).fetchall():
            self._delete_disk_row(v_id, v_path)
            self.dropped_total += 1
            log.warning("offline_buffer_expired id=%s", v_id)

    def _delete_disk_row(self, rowid: int, path: str) -> None:
        row = self._conn.execute("SELECT size FROM frames WHERE rowid = ?", (rowid,)).fetchone()
        self._write("DELETE FROM frames WHERE rowid = ?", (rowid,))
        if row:
            self._disk_count -= 1
            self._disk_bytes -= int(row[0])
        _unlink_quiet(path)

    # ── Event records ─────────────────────────────────────────────────────────

    def put_event(self, seq: int, payload: dict) -> None:
        with self._lock:
            (count,) = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()
            if count >= self._max_events:
                log.error("offline_buffer_event_store_full count=%s", count)
                raise EventStoreFull(f"event store full ({count} records)")
            self._write(
                "INSERT INTO events (mission_id, seq, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (self.mission_id, int(seq), json.dumps(payload), self._clock()),
            )
            self._maybe_commit(force=True)   # records are small and must survive a crash

    def get_events(self, limit: int = 500) -> List[Tuple[int, dict]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, payload_json FROM events WHERE mission_id = ? ORDER BY id LIMIT ?",
                (self.mission_id, int(limit)),
            ).fetchall()
            return [(r[0], json.loads(r[1])) for r in rows]

    def commit_events(self, ids: List[int]) -> None:
        if not ids:
            return
        with self._lock:
            marks = ",".join("?" * len(ids))
            self._write(f"DELETE FROM events WHERE id IN ({marks})", tuple(ids))  # noqa: S608 - only "?" marks
            self._maybe_commit(force=True)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def clear(self, keep_mission_id: Optional[str] = None) -> None:
        """Delete buffered frames and records of other missions (all of them when keep_mission_id is None)."""
        with self._lock:
            self._mem.clear()
            if keep_mission_id is None:
                rows = self._conn.execute("SELECT rowid, frame_path FROM frames").fetchall()
                self._write("DELETE FROM events")
            else:
                rows = self._conn.execute("SELECT rowid, frame_path FROM frames WHERE mission_id != ?", (keep_mission_id,)).fetchall()
                self._write("DELETE FROM events WHERE mission_id != ?", (keep_mission_id,))
            for rowid, path in rows:
                self._delete_disk_row(rowid, path)
            self._maybe_commit(force=True)
            log.info("offline_buffer_cleared removed=%d", len(rows))

    def stats(self) -> dict:
        with self._lock:
            (events,) = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()
            return {
                "memory_frames": len(self._mem),
                "disk_frames": self._disk_count,
                "disk_bytes": self._disk_bytes,
                "cap_bytes": self.cap_bytes,
                "dropped_total": self.dropped_total,
                "rejected_total": self.rejected_total,
                "events_pending": int(events),
            }

    def close(self) -> None:
        """Spill unsent frames to disk, commit, and close the database."""
        self.spill_all()
        with self._lock:
            self._maybe_commit(force=True)
            self._conn.close()


def _unlink_quiet(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("offline_buffer_unlink_failed path=%s error=%s", path, exc)
