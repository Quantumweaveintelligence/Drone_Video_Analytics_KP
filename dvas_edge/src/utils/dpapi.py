"""
src/utils/dpapi.py
==================
Windows DPAPI (CryptProtectData / CryptUnprotectData) through ctypes.

Secrets are encrypted for the current Windows user: another user on the same laptop,
or a copy of the file on another machine, cannot decrypt them. No custom crypto.
"""
from __future__ import annotations

import ctypes
import json
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Dict

_ENTROPY = b"DVAS-Edge-credentials-v1"   # binds the blob to this application
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _take(blob: _Blob) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def available() -> bool:
    return sys.platform == "win32"


def protect(data: bytes) -> bytes:
    """Encrypt bytes for the current Windows user."""
    if not available():
        raise OSError("DPAPI is only available on Windows")
    out = _Blob()
    entropy = _blob(_ENTROPY)
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(_blob(data)), None, ctypes.byref(entropy), None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out),
    )
    if not ok:
        raise ctypes.WinError()
    return _take(out)


def unprotect(data: bytes) -> bytes:
    """Decrypt bytes produced by protect() for the same Windows user."""
    if not available():
        raise OSError("DPAPI is only available on Windows")
    out = _Blob()
    entropy = _blob(_ENTROPY)
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(_blob(data)), None, ctypes.byref(entropy), None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out),
    )
    if not ok:
        raise ctypes.WinError()
    return _take(out)


def save_secrets(path: Path, secrets: Dict[str, str]) -> None:
    """Encrypt a {name: value} map to `path` (written atomically)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(protect(json.dumps(secrets).encode("utf-8")))
    tmp.replace(path)


def load_secrets(path: Path) -> Dict[str, str]:
    """Decrypt the map saved by save_secrets(); {} when the file does not exist."""
    if not path.exists():
        return {}
    return json.loads(unprotect(path.read_bytes()).decode("utf-8"))
