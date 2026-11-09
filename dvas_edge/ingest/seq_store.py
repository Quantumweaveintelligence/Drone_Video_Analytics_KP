"""
seq_store.py
============
Crash-safe per-mission frame sequence numbers.

Numbers are reserved on disk in blocks before they are handed out, so a crash
or kill can leave a gap in the sequence but can never reuse a number.
Write order: temp file -> fsync -> os.replace (atomic on Windows and POSIX).
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _mission_file(directory: Path, mission_id: str) -> Path:
    return directory / f"{_UNSAFE.sub('_', mission_id)}.json"


class SeqStore:
    """Hands out strictly increasing sequence numbers for one mission."""

    def __init__(self, directory: Path, mission_id: str, block: int = 300) -> None:
        if block < 1:
            raise ValueError("block must be >= 1")
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = _mission_file(self._dir, mission_id)
        self._mission_id = mission_id
        self._block = block
        self._lock = threading.Lock()
        reserved = self._read_reserved()
        # Resume after the whole previously reserved block: unused numbers become a gap.
        self._next = reserved + 1
        self._reserved_until = reserved

    @property
    def path(self) -> Path:
        return self._path

    def _read_reserved(self) -> int:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return 0
        except (OSError, ValueError) as exc:
            # A corrupt file must not restart numbering at 1: refuse to guess.
            raise RuntimeError(f"seq store unreadable: {self._path}") from exc
        if data.get("mission_id") != self._mission_id:
            raise RuntimeError(f"seq store {self._path} belongs to another mission")
        return int(data.get("seq_reserved_until", 0))

    def _persist(self, reserved_until: int) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        payload = json.dumps({"mission_id": self._mission_id, "seq_reserved_until": reserved_until})
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self._path)

    def next(self) -> int:
        """Return the next sequence number, reserving a new block on disk first if needed."""
        with self._lock:
            if self._next > self._reserved_until:
                new_until = self._next + self._block - 1
                self._persist(new_until)
                self._reserved_until = new_until
            seq = self._next
            self._next += 1
            return seq

    def advance_past(self, server_last_seq: int) -> None:
        """Never hand out a number the server already holds (used on resume)."""
        with self._lock:
            if server_last_seq >= self._next:
                self._next = server_last_seq + 1
