"""Smoke tests for `shared.logging`.

We don't try to assert exact log output (capsys interaction with structlog
+ stdlib bridges is fiddly and the CI value is low). Instead we verify the
contract: the function is idempotent, it respects LOG_FORMAT, and the
configured logger can be obtained without error.
"""
from __future__ import annotations

import io
import json
import logging

import structlog

import shared.logging as shared_logging


def _reset() -> None:
    """Force re-configuration on next call."""
    shared_logging._CONFIGURED = False


def test_configure_is_idempotent(monkeypatch) -> None:
    monkeypatch.setenv("LOG_FORMAT", "console")
    _reset()
    shared_logging.configure_logging()
    handlers_after_first = list(logging.getLogger().handlers)
    shared_logging.configure_logging()  # second call should be a no-op
    handlers_after_second = list(logging.getLogger().handlers)
    assert handlers_after_first == handlers_after_second


def test_get_logger_returns_a_structlog_logger(monkeypatch) -> None:
    monkeypatch.setenv("LOG_FORMAT", "console")
    _reset()
    log = shared_logging.get_logger("test")
    # structlog.get_logger returns a BoundLoggerLazyProxy until first use.
    assert log is not None
    # Smoke-emit a log line — should not raise.
    log.info("hello", foo="bar")


def test_json_format_emits_parseable_json(monkeypatch) -> None:
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    _reset()

    # Capture the root handler's stream output.
    buf = io.StringIO()
    shared_logging.configure_logging()
    root = logging.getLogger()
    # Replace the configured handler with one writing to our buffer, keeping
    # the same formatter (which is what we're actually testing).
    formatter = root.handlers[0].formatter
    test_handler = logging.StreamHandler(buf)
    test_handler.setFormatter(formatter)
    root.handlers = [test_handler]

    log = structlog.get_logger("test.module")
    log.info("ping", tool_name="vector_search", latency_ms=42)

    out = buf.getvalue().strip()
    assert out, "expected at least one log line"
    record = json.loads(out)
    assert record["event"] == "ping"
    assert record["tool_name"] == "vector_search"
    assert record["latency_ms"] == 42
    assert record["level"] == "info"


def test_console_format_does_not_emit_json(monkeypatch) -> None:
    monkeypatch.setenv("LOG_FORMAT", "console")
    _reset()

    buf = io.StringIO()
    shared_logging.configure_logging()
    formatter = logging.getLogger().handlers[0].formatter
    test_handler = logging.StreamHandler(buf)
    test_handler.setFormatter(formatter)
    logging.getLogger().handlers = [test_handler]

    log = structlog.get_logger("test.module")
    log.info("ping", tool_name="vector_search")

    out = buf.getvalue()
    # Console output isn't JSON; it shouldn't parse as one.
    import contextlib
    with contextlib.suppress(json.JSONDecodeError):
        json.loads(out)
        # If parse succeeded, that's a regression — console mode should be
        # human readable, not JSON.
        raise AssertionError(f"console mode unexpectedly produced JSON: {out!r}")
    # Sanity: the event name still appears somewhere.
    assert "ping" in out
