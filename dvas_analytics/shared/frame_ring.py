# -*- coding: utf-8 -*-
"""
DVAS — shared decoded-frame ring (v2 plan R11.3, `DVAS_FRAME_RING`).

The video decoder JPEG-encodes every analytics frame (the stream keeps that JPEG: evidence,
late readers, fallback) and also writes the frame decoded back from that same JPEG — with
`cv2.imdecode(…, IMREAD_COLOR)`, the call every service makes — into a ring in /dev/shm.
A service copies the slot instead of decoding the JPEG itself: the same pixels, without six of
seven JPEG decodes per frame.

File  <DVAS_FRAME_RING_DIR>/<sid>-<w>x<h>-<token>.ring  (a new token per file: never reused)
  header (4096 B): magic, version, slots, width, height, channels, slot size, the writer's
                   JPEG decoder build (OpenCV version + its libjpeg line)
  slot i (at 4096 + i × slot size): gen u64, frame_seq u64, 48 B spare, then h × w × 3 BGR bytes
A write sets gen odd (writing), copies the pixels and frame_seq, then sets the next even gen.
The stream entry carries `ring`, `ring_slot`, `ring_gen`. A reader uses the slot only when the
gen equals ring_gen before and after its copy, frame_seq matches, and its own JPEG decoder build
equals the writer's; anything else (slot overwritten, file gone, another OpenCV build — the ANPR
venv) decodes the JPEG exactly as before. The file is fully reserved (posix_fallocate) when it is
created, so a full /dev/shm refuses the ring instead of faulting later.
"""
from __future__ import annotations

import logging
import mmap
import os
import secrets
import shutil
import struct
import threading
from typing import Dict, Optional, Tuple

log = logging.getLogger(__name__)

MAGIC = b"DVASRNG1"
VERSION = 1
HEADER = 4096
SLOT_HEADER = 64
_HDR = struct.Struct("<8sIIIIIQ96s")      # magic, version, slots, width, height, channels, slot size, build
_SLOT = struct.Struct("<QQ")              # gen, frame_seq
ROOT = os.getenv("DVAS_FRAME_RING_DIR", "/dev/shm/dvas-ring")
SHM_RESERVE = 256 * 1024 * 1024           # leave this much of /dev/shm free for everything else

_build: Optional[bytes] = None


def jpeg_build() -> bytes:
    """What decides imdecode's pixels: OpenCV's version and its JPEG library line."""
    global _build
    if _build is None:
        import cv2  # noqa: PLC0415
        jpeg = next((ln.strip() for ln in cv2.getBuildInformation().splitlines()
                     if ln.strip().startswith("JPEG:")), "")
        _build = f"{cv2.__version__}|{jpeg}".encode()[:96]
    return _build


class RingFull(Exception):
    """No room for the ring in /dev/shm."""


class RingWriter:
    """One ring per mission stream and frame size (the decoder recreates it on a size change)."""

    def __init__(self, sid: str, width: int, height: int, slots: int, root: str = ROOT):
        self.width, self.height, self.slots = int(width), int(height), int(slots)
        self.frame_bytes = self.width * self.height * 3
        self.slot_size = SLOT_HEADER + self.frame_bytes
        size = HEADER + self.slots * self.slot_size
        os.makedirs(root, exist_ok=True)
        free = shutil.disk_usage(root).free
        if free < size + SHM_RESERVE:
            raise RingFull(f"{size / 2**30:.2f} GB needed, {free / 2**30:.2f} GB free in {root}")
        self.name = f"{sid}-{self.width}x{self.height}-{secrets.token_hex(4)}.ring"
        self.path = os.path.join(root, self.name)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            try:
                os.posix_fallocate(fd, 0, size)
            except OSError as exc:
                raise RingFull(f"{size / 2**30:.2f} GB could not be reserved in {root}: {exc}") from exc
            self._mm = mmap.mmap(fd, size)
        except Exception:
            os.close(fd)
            try:
                os.unlink(self.path)
            except OSError:
                pass
            raise
        os.close(fd)
        _HDR.pack_into(self._mm, 0, MAGIC, VERSION, self.slots, self.width, self.height, 3,
                       self.slot_size, jpeg_build())
        self._gens = [0] * self.slots

    def write(self, slot: int, seq: int, img) -> Optional[int]:
        """Store a decoded frame; returns its gen (None when the frame does not fit this ring)."""
        import numpy as np  # noqa: PLC0415
        if img is None or img.shape != (self.height, self.width, 3) or img.dtype != np.uint8:
            return None
        off = HEADER + slot * self.slot_size
        gen = self._gens[slot] + 1                       # odd: being written
        _SLOT.pack_into(self._mm, off, gen, 0)
        dst = np.ndarray((self.height, self.width, 3), np.uint8, buffer=self._mm, offset=off + SLOT_HEADER)
        dst[...] = img
        gen += 1
        _SLOT.pack_into(self._mm, off, gen, int(seq))     # gen last: the slot is complete
        self._gens[slot] = gen
        return gen

    def close(self, unlink: bool = True) -> None:
        try:
            self._mm.close()
        except Exception:
            pass
        if unlink:
            try:
                os.unlink(self.path)
            except OSError:
                pass


def clear(root: str = ROOT) -> int:
    """Remove every ring file (decoder start: no writer is alive any more)."""
    n = 0
    try:
        for name in os.listdir(root):
            if name.endswith(".ring"):
                try:
                    os.unlink(os.path.join(root, name))
                    n += 1
                except OSError:
                    pass
    except FileNotFoundError:
        pass
    return n


class RingReader:
    """Reads ring slots for a service; thread-safe (the sampler decodes in worker threads)."""

    MAX_MAPS = 32

    def __init__(self, root: str = ROOT):
        self.root = root
        self._maps: Dict[str, Optional[Tuple[mmap.mmap, tuple]]] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def _map(self, name: str):
        with self._lock:
            if name in self._maps:
                return self._maps[name]
            if len(self._maps) >= self.MAX_MAPS:
                self._evict()
            entry = None
            try:
                with open(os.path.join(self.root, name), "rb") as f:
                    mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                hdr = _HDR.unpack_from(mm, 0)
                magic, version, slots, w, h, c, slot_size, build = hdr
                if magic != MAGIC or version != VERSION or c != 3 or slot_size != SLOT_HEADER + w * h * 3:
                    mm.close()
                elif build.rstrip(b"\0") != jpeg_build():
                    log.info("frame ring %s: written with another JPEG decoder (%s, here %s) — "
                             "decoding the JPEG instead", name, build.rstrip(b"\0").decode(errors="replace"),
                             jpeg_build().decode(errors="replace"))
                    mm.close()
                else:
                    entry = (mm, (slots, w, h, slot_size))
            except (OSError, ValueError, struct.error):
                entry = None
            self._maps[name] = entry
            return entry

    def _evict(self) -> None:
        for name, entry in list(self._maps.items()):
            if entry is None or not os.path.exists(os.path.join(self.root, name)):
                if entry is not None:
                    try:
                        entry[0].close()
                    except BufferError:                     # a copy is in progress: later
                        continue
                self._maps.pop(name, None)

    def read(self, fields: dict):
        """The frame's pixels from the ring, or None (then decode the JPEG)."""
        import numpy as np  # noqa: PLC0415
        name = fields.get("ring")
        try:
            slot, gen = int(fields.get("ring_slot")), int(fields.get("ring_gen"))
            seq = int(fields.get("frame_seq") or fields.get("frame_sequence") or 0)
        except (TypeError, ValueError):
            return None
        if not name or "/" in name or gen <= 0 or gen % 2:
            return None
        entry = self._map(name)
        if entry is None:
            self.misses += 1
            return None
        mm, (slots, w, h, slot_size) = entry
        if not 0 <= slot < slots:
            self.misses += 1
            return None
        off = HEADER + slot * slot_size
        try:
            g1, _ = _SLOT.unpack_from(mm, off)
            if g1 != gen:
                self.misses += 1
                return None
            img = np.frombuffer(mm, np.uint8, count=w * h * 3, offset=off + SLOT_HEADER).reshape(h, w, 3).copy()
            g2, s = _SLOT.unpack_from(mm, off)
        except (ValueError, TypeError):
            self.misses += 1
            return None
        if g2 != gen or (seq and s != seq):
            self.misses += 1
            return None
        self.hits += 1
        return img
