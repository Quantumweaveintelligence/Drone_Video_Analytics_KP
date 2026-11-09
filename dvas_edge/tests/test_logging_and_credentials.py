"""Log redaction, console-less logging, DPAPI credential migration."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pytest

from src.utils.logger import build_handlers, redact

JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJvcGVyYXRvciJ9.c2lnbmF0dXJlLXZhbHVl"


@pytest.mark.parametrize("line, secret", [
    (f"Authorization: Bearer {JWT}", JWT),
    (f"token={JWT}&x=1", JWT),
    ("password=Operator@001 user=op", "Operator@001"),
    ('{"client_secret": "abc123def"}', "abc123def"),
    ("rtsp://admin:Admin12345@10.10.32.23:554/h264", "Admin12345"),
    (f"session_token={JWT}", JWT),
])
def test_secrets_are_redacted(line: str, secret: str) -> None:
    assert secret not in redact(line)


def test_ordinary_fields_are_kept() -> None:
    line = "frame_sent seq=12 token_expiry=1762671000 mission=MSN-1"
    assert redact(line) == line


def test_no_console_handler_without_stdout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdout", None)        # what pythonw gives the process
    handlers = build_handlers(str(tmp_path / "x.log"))
    assert len(handlers) == 1
    logger = logging.getLogger("test.nostdout")
    logger.addHandler(handlers[0])
    logger.setLevel(logging.INFO)
    logger.info("password=%s", "hunter2")
    handlers[0].close()
    text = (tmp_path / "x.log").read_text(encoding="utf-8")
    assert "hunter2" not in text and "[REDACTED]" in text


windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")


@windows_only
def test_dpapi_round_trip_and_no_plaintext(tmp_path: Path) -> None:
    from src.utils import dpapi
    store = tmp_path / "s.dpapi"
    dpapi.save_secrets(store, {"INGEST__PASSWORD": "Operator@001"})
    assert b"Operator@001" not in store.read_bytes()
    assert dpapi.load_secrets(store) == {"INGEST__PASSWORD": "Operator@001"}


@windows_only
def test_migration_moves_secrets_and_keeps_url(tmp_path: Path, monkeypatch) -> None:
    from src.launcher import credentials
    env = tmp_path / "edge.env"
    env.write_text(
        "INGEST__BASE_URL=https://pod-80.proxy.runpod.net\n"
        "INGEST__CLIENT_SECRET=real-secret-value\n"
        "INGEST__PASSWORD=<per-laptop password>\n"
        "EDGE_API_KEY=k-123\n", encoding="utf-8")
    store = tmp_path / "secrets.dpapi"
    moved = credentials.migrate(env, store)
    assert moved == ["EDGE_API_KEY", "INGEST__CLIENT_SECRET"]          # placeholder not moved
    text = env.read_text(encoding="utf-8")
    assert "real-secret-value" not in text and "k-123" not in text
    assert "INGEST__BASE_URL=https://pod-80.proxy.runpod.net" in text   # URL stays editable
    assert "INGEST__PASSWORD=<per-laptop password>" in text
    assert credentials.migrate(env, store) == []                         # idempotent
    monkeypatch.delenv("INGEST__CLIENT_SECRET", raising=False)
    credentials.apply(store)
    assert os.environ["INGEST__CLIENT_SECRET"] == "real-secret-value"


@windows_only
def test_new_value_typed_in_env_replaces_stored(tmp_path: Path) -> None:
    from src.launcher import credentials
    from src.utils import dpapi
    env, store = tmp_path / "edge.env", tmp_path / "s.dpapi"
    env.write_text("EDGE_API_KEY=old\n", encoding="utf-8")
    credentials.migrate(env, store)
    env.write_text(env.read_text(encoding="utf-8").replace("EDGE_API_KEY=", "EDGE_API_KEY=new"), encoding="utf-8")
    credentials.migrate(env, store)
    assert dpapi.load_secrets(store)["EDGE_API_KEY"] == "new"
