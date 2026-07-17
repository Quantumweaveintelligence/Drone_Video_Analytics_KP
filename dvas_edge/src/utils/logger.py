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
import sys
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

def setup_logging(
    level: str = "INFO",
    json_output: bool = False,
    log_file: Optional[str] = None,
) -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

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
        renderer = (
            _structlog.processors.JSONRenderer()
            if json_output
            else _structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
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
