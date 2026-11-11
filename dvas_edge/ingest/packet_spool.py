"""Bounded, mission-scoped disk spool for compressed video packets awaiting ingest."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from src.buffer.packet_ring import RingPacket


class PacketSpool:
    """Persist whole compressed packets until the ingest server acknowledges their seq.

    The caller starts each spool run at a keyframe. The byte cap is strict for packet payloads;
    metadata overhead is small and bounded by the number of packets.
    """

    def __init__(self, path: Path | str, max_bytes: int = 2 * 1024 * 1024 * 1024) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max(0, int(max_bytes))
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
        with self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS packets (
                    seq INTEGER PRIMARY KEY,
                    pts INTEGER,
                    dts INTEGER,
                    capture_ts REAL NOT NULL,
                    keyframe INTEGER NOT NULL,
                    telemetry TEXT NOT NULL,
                    data BLOB NOT NULL
                )
            """)
            self._db.execute("CREATE INDEX IF NOT EXISTS packets_key_seq ON packets(keyframe, seq)")
            self._db.execute("CREATE TABLE IF NOT EXISTS spool_state (id INTEGER PRIMARY KEY CHECK(id=1), bytes_used INTEGER NOT NULL)")
            self._db.execute("INSERT OR IGNORE INTO spool_state(id,bytes_used) VALUES(1,0)")
            used = self._db.execute("SELECT COALESCE(SUM(length(data)),0) FROM packets").fetchone()[0]
            self._db.execute("UPDATE spool_state SET bytes_used=? WHERE id=1", (used,))

    def put(self, pkt: RingPacket) -> bool:
        """Insert a packet, returning False if it would exceed the configured payload cap."""
        with self._lock, self._db:
            present = self._db.execute("SELECT 1 FROM packets WHERE seq=?", (pkt.seq,)).fetchone()
            if present:
                return True
            current = self._db.execute("SELECT bytes_used FROM spool_state WHERE id=1").fetchone()[0]
            if current + len(pkt.data) > self.max_bytes:
                return False
            self._db.execute(
                "INSERT INTO packets(seq,pts,dts,capture_ts,keyframe,telemetry,data) VALUES(?,?,?,?,?,?,?)",
                (pkt.seq, pkt.pts, pkt.dts, pkt.capture_ts, int(pkt.key),
                 json.dumps(pkt.telemetry or {}, separators=(",", ":")), sqlite3.Binary(pkt.data)),
            )
            self._db.execute("UPDATE spool_state SET bytes_used=bytes_used+? WHERE id=1", (len(pkt.data),))
            return True

    def first(self, after_seq: int = 0) -> Optional[RingPacket]:
        with self._lock:
            row = self._db.execute(
                "SELECT seq,pts,dts,capture_ts,keyframe,telemetry,data FROM packets WHERE seq>? ORDER BY seq LIMIT 1",
                (int(after_seq),),
            ).fetchone()
        return self._packet(row) if row else None

    def first_keyframe(self, after_seq: int = 0) -> Optional[RingPacket]:
        with self._lock:
            row = self._db.execute(
                "SELECT seq,pts,dts,capture_ts,keyframe,telemetry,data FROM packets "
                "WHERE seq>? AND keyframe=1 ORDER BY seq LIMIT 1", (int(after_seq),),
            ).fetchone()
        return self._packet(row) if row else None

    def has_after(self, seq: int) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM packets WHERE seq>? LIMIT 1", (int(seq),)).fetchone() is not None

    def acknowledge(self, seq: int) -> None:
        with self._lock, self._db:
            removed = self._db.execute(
                "SELECT COALESCE(SUM(length(data)),0) FROM packets WHERE seq<=?", (int(seq),)
            ).fetchone()[0]
            self._db.execute("DELETE FROM packets WHERE seq<=?", (int(seq),))
            self._db.execute("UPDATE spool_state SET bytes_used=MAX(0,bytes_used-?) WHERE id=1", (removed,))

    def stats(self) -> dict:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*),(SELECT bytes_used FROM spool_state WHERE id=1),MIN(seq),MAX(seq) FROM packets"
            ).fetchone()
        return {"packets": row[0], "bytes": row[1], "first_seq": row[2], "last_seq": row[3],
                "max_bytes": self.max_bytes}

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _packet(row) -> RingPacket:
        return RingPacket(seq=row[0], pts=row[1], dts=row[2], capture_ts=row[3], key=bool(row[4]),
                          telemetry=json.loads(row[5]), data=bytes(row[6]))
