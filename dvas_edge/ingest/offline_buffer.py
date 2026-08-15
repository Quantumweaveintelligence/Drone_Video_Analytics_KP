import os
import json
import sqlite3
import threading
import uuid
import logging
from pathlib import Path
from typing import Optional, Tuple

log = logging.getLogger("offline_buffer")

class OfflineBuffer:
    """
    A persistent hybrid buffer:
    - Writes JPEG frames to flat files to avoid DB bloat.
    - Stores metadata in a SQLite table for robust queueing.
    - Provides a thread-safe put/get interface.
    """
    def __init__(self, db_path: str = "buffer_storage/buffer.db", frames_dir: str = "buffer_storage/frames"):
        self.db_path = Path(db_path)
        self.frames_dir = Path(frames_dir)
        
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        
        self._init_db()
        
    def _init_db(self):
        with self._lock:
            self._conn.execute('''
                CREATE TABLE IF NOT EXISTS frames (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    telemetry_json TEXT,
                    frame_path TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            self._conn.commit()
            
    def put(self, kind: str, jpeg_bytes: bytes, telemetry: dict) -> None:
        filename = f"{uuid.uuid4().hex}.jpg"
        filepath = self.frames_dir / filename
        
        # Write bytes to disk
        with open(filepath, "wb") as f:
            f.write(jpeg_bytes)
            
        telemetry_str = json.dumps(telemetry) if telemetry else "{}"
        
        with self._cond:
            self._conn.execute(
                "INSERT INTO frames (kind, telemetry_json, frame_path) VALUES (?, ?, ?)",
                (kind, telemetry_str, str(filepath))
            )
            self._conn.commit()
            # Notify waiting consumers that a new frame is available
            self._cond.notify_all()
            
    def get(self, timeout: float = 1.0, exclude_ids: list = None) -> Optional[Tuple[int, str, bytes, dict]]:
        """
        Returns (frame_id, kind, jpeg_bytes, telemetry_dict) or None if timeout.
        """
        with self._cond:
            query = "SELECT id, kind, telemetry_json, frame_path FROM frames "
            args = ()
            if exclude_ids:
                placeholders = ','.join('?' * len(exclude_ids))
                query += f"WHERE id NOT IN ({placeholders}) "
                args = tuple(exclude_ids)
            query += "ORDER BY created_at ASC LIMIT 1"

            cursor = self._conn.execute(query, args)
            row = cursor.fetchone()
            
            if not row:
                self._cond.wait(timeout=timeout)
                cursor = self._conn.execute(query, args)
                row = cursor.fetchone()
                
            if not row:
                return None
                
            frame_id, kind, telemetry_json, frame_path = row
            
            # Read image bytes
            try:
                with open(frame_path, "rb") as f:
                    jpeg_bytes = f.read()
            except FileNotFoundError:
                log.warning(f"offline_buffer_file_missing: {frame_path}")
                # Corrupted record, delete it and try the next one
                self._conn.execute("DELETE FROM frames WHERE id = ?", (frame_id,))
                self._conn.commit()
                return self.get(timeout=0) # fetch next immediately
                
            telemetry = json.loads(telemetry_json) if telemetry_json else {}
            return (frame_id, kind, jpeg_bytes, telemetry)
            
    def commit(self, frame_id: int) -> None:
        """
        Called when a frame has been successfully sent. Deletes the file and DB record.
        """
        with self._lock:
            cursor = self._conn.execute("SELECT frame_path FROM frames WHERE id = ?", (frame_id,))
            row = cursor.fetchone()
            if row:
                frame_path = row[0]
                try:
                    os.remove(frame_path)
                except OSError:
                    pass
                
            self._conn.execute("DELETE FROM frames WHERE id = ?", (frame_id,))
            self._conn.commit()

    def clear(self) -> None:
        """Delete all buffered frames (DB records + JPEG files on disk).
        Called at the start of a new mission so stale frames from a
        previous mission are not sent with the new mission's metadata."""
        with self._lock:
            cursor = self._conn.execute("SELECT frame_path FROM frames")
            for (frame_path,) in cursor.fetchall():
                try:
                    os.remove(frame_path)
                except OSError:
                    pass
            self._conn.execute("DELETE FROM frames")
            self._conn.commit()
            log.info("offline_buffer_cleared")

    def close(self) -> None:
        with self._lock:
            self._conn.close()
