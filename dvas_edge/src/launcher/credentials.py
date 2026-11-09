"""
src/launcher/credentials.py
===========================
Move secrets out of the plain-text edge.env into a DPAPI-encrypted file.

The installer (and v1) write INGEST__CLIENT_SECRET, INGEST__PASSWORD and EDGE_API_KEY in
plain text. On start the launcher moves any real value into `secrets.dpapi` (encrypted for
the current Windows user) and blanks the line in edge.env. Non-secret settings, such as the
ingest URL, stay editable in edge.env.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List

from src.utils import dpapi

log = logging.getLogger("launcher.credentials")

SECRET_KEYS = ("INGEST__CLIENT_SECRET", "INGEST__PASSWORD", "EDGE_API_KEY")
_MARK = "# stored encrypted (DPAPI) - type a new value here to replace it"


def _is_real(value: str) -> bool:
    v = value.strip().strip("'\"")
    return bool(v) and not (v.startswith("<") and v.endswith(">"))


def migrate(env_file: Path, store: Path) -> List[str]:
    """Encrypt plain-text secrets found in env_file into store; blank them in env_file.

    Returns the names that were moved. A value typed into edge.env later replaces the
    stored one the next time the launcher starts.
    """
    if not env_file.exists():
        return []
    lines = env_file.read_text(encoding="utf-8").splitlines()
    found: Dict[str, str] = {}
    out: List[str] = []
    marked = _MARK in lines
    for line in lines:
        key, sep, value = line.partition("=")
        name = key.strip()
        if sep and name in SECRET_KEYS and not line.lstrip().startswith("#") and _is_real(value):
            found[name] = value.strip().strip("'\"")
            if not marked:
                out.append(_MARK)
                marked = True
            out.append(f"{name}=")
        else:
            out.append(line)
    if not found:
        return []
    stored = dpapi.load_secrets(store)
    stored.update(found)
    dpapi.save_secrets(store, stored)               # encrypted copy first ...
    tmp = env_file.with_suffix(env_file.suffix + ".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    tmp.replace(env_file)                           # ... then remove the plain text
    log.info("secrets_moved_to_dpapi names=%s", sorted(found))
    return sorted(found)


def apply(store: Path) -> None:
    """Put the decrypted secrets into os.environ (inherited by the edge core process)."""
    for name, value in dpapi.load_secrets(store).items():
        if name in SECRET_KEYS and not os.environ.get(name):
            os.environ[name] = value
