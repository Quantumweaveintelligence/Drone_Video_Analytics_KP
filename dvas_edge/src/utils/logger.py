"""
src/utils/logger.py
===================
Structured logging — uses structlog when available, stdlib logging otherwise.

Usage:
    from src.utils.logger import get_logger
    log = get_logger(__name__)
    log.info("connected", drone_id="drone_01", fps=25.0)
"""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional, Any

# ── Optional structlog ───────────────────────────────────────────────────────
try:
    import structlog as _structlog
    _STRUCTLOG = True
except ImportError:
    _STRUCTLOG = False


# ── Stdlib-only logger shim ──────────────────────────────────────────────────

class _StdlibLogger:
    """Thin wrapper so callers can use log.info("event", key=val) syntax."""

    def __init__(self, name: str) -> None:
        self._log = logging.getLogger(name)

    def _fmt(self, event: str, **kw) -> str:
        parts = [event] + [f"{k}={v!r}" for k, v in kw.items()]
        return "  ".join(parts)

    def debug(self, event: str, **kw) -> None:
        self._log.debug(self._fmt(event, **kw))

    def info(self, event: str, **kw) -> None:
        self._log.info(self._fmt(event, **kw))

    def warning(self, event: str, **kw) -> None:
        self._log.warning(self._fmt(event, **kw))

    def error(self, event: str, **kw) -> None:
        self._log.error(self._fmt(event, **kw))

    def critical(self, event: str, **kw) -> None:
        self._log.critical(self._fmt(event, **kw))


# ── Public API ───────────────────────────────────────────────────────────────

_SECRET_PATTERNS = [
    # Authorization: Bearer <token>  /  bearer=<token>
    (re.compile(r"(?i)(bearer[\s=:]+)[A-Za-z0-9\-._~+/]+=*"), r"\1[REDACTED]"),
    # Bare JWTs (three base64url segments)
    (re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"), "[REDACTED-JWT]"),
    # key=value / "key": "value" for secret-like keys, in logs, URLs and JSON
    (re.compile(r"(?i)((?:password|passwd|client_secret|secret|api[_-]?key|access_token|refresh_token|token)"
                r"[\"']?\s*[:=]\s*[\"']?)[^\"'&\s,}]+"), r"\1[REDACTED]"),
    # Credentials embedded in URLs: scheme://user:pass@host
    (re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^/\s:@]+:)[^@\s/]+@"), r"\1[REDACTED]@"),
]

LOG_MAX_BYTES = 20 * 1024 * 1024
LOG_BACKUPS = 10


def redact(text: str) -> str:
    """Remove tokens, passwords and credentials from a log line."""
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    """Applied to every handler so no secret reaches a log file or console."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:   # a malformed record must still be logged, never dropped
            message = str(record.msg)
        record.msg = redact(message)
        record.args = None
        return True


def build_handlers(log_file: Optional[str]) -> list:
    """Console handler only when a console exists (not under pythonw), plus a rotating file."""
    handlers: list[logging.Handler] = []
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"))
    for h in handlers:
        h.addFilter(RedactingFilter())
    return handlers


def setup_logging(
    level: str = "INFO",
    json_output: bool = False,
    log_file: Optional[str] = None,
) -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    handlers = build_handlers(log_file)

    fmt = "%(asctime)s %(levelname)-8s %(name)s  %(message)s"
    logging.basicConfig(level=log_level, handlers=handlers, format=fmt, force=True)

    if _STRUCTLOG:
        shared = [
            _structlog.stdlib.add_logger_name,
            _structlog.stdlib.add_log_level,
            _structlog.processors.TimeStamper(fmt="iso"),
            _structlog.processors.StackInfoRenderer(),
            _structlog.processors.format_exc_info,
        ]
        use_colors = False
        if sys.stdout and hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
            try:
                import colorama  # noqa: F401
                use_colors = True
            except Exception:
                use_colors = False
        renderer = (
            _structlog.processors.JSONRenderer()
            if json_output
            else _structlog.dev.ConsoleRenderer(colors=use_colors)
        )
        _structlog.configure(
            processors=shared + [renderer],
            wrapper_class=_structlog.make_filtering_bound_logger(log_level),
            context_class=dict,
            logger_factory=_structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )


def get_logger(name: str) -> Any:
    if _STRUCTLOG:
        return _structlog.get_logger(name)
    return _StdlibLogger(name)
