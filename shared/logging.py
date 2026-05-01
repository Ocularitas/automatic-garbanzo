"""Centralised logging configuration.

One call to `configure_logging()` at process start sets up:

  - structlog as the primary logger (callsites use `from structlog import get_logger`)
  - the stdlib `logging` module as a forwarder, so libraries that use
    `logging.getLogger(...)` (FastMCP, SQLAlchemy, httpx, etc.) flow through
    the same pipeline
  - JSON output when `LOG_FORMAT=json` (production / Application Insights /
    any aggregator that wants structured fields), pretty console output
    otherwise (local dev, journalctl tailing)

Standard fields on every log line: `event`, `level`, `timestamp`, `logger`.
Callers can attach more via `log.info("...", tool_name="...", latency_ms=42)`.

Set the level via `LOG_LEVEL` (default INFO).
"""
from __future__ import annotations

import logging
import os
import sys

import structlog

_CONFIGURED = False


def configure_logging() -> None:
    """Configure structlog + stdlib logging. Idempotent.

    Reads `LOG_FORMAT` (json|console, default console) and `LOG_LEVEL`
    (default INFO) from the environment.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_format = os.environ.get("LOG_FORMAT", "console").lower()
    log_level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)

    # Shared processors — applied to every event regardless of source.
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if log_format == "json":
        # Production: one JSON object per line. Keys are stable so an
        # aggregator can index `event`, `level`, `tool_name`, etc. directly.
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        # Dev: human-readable, coloured if the terminal supports it.
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    # structlog-native loggers
    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Bridge stdlib logging through the same renderer
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    # Quiet down a few libraries that are otherwise chatty at INFO.
    for noisy in ("httpx", "httpcore", "watchdog", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Convenience wrapper. Equivalent to `structlog.get_logger(name)` but
    ensures `configure_logging()` has been called."""
    if not _CONFIGURED:
        configure_logging()
    return structlog.get_logger(name)
