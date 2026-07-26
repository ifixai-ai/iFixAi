"""Structured logging for ifixai runs.

Uses `structlog` when available and falls back to stdlib `logging` so
environments without structlog still work.

Usage:
    from ifixai.observability.logging import configure_logging, bind_run
    configure_logging(level="INFO")
    log = bind_run(run_id="abc123", test_id="B12")
    log.info("inspection_started", test_case_id="tc_001")
"""

from __future__ import annotations

import logging
import sys
from typing import Any

try:
    import structlog

    _STRUCTLOG_AVAILABLE = True
except ImportError:
    structlog = None  # type: ignore[assignment]
    _STRUCTLOG_AVAILABLE = False


_configured = False

_STD_LEVEL_BY_NAME: dict[str, int] = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "NOTSET": logging.NOTSET,
}


def configure_logging(level: str = "INFO", json_output: bool = False) -> None:
    """Configure root logging once per process.

    When `json_output` is True and `structlog` is available, lines are
    emitted as JSON; otherwise a compact key=value format is used.
    """
    global _configured
    if _configured:
        return
    _configured = True

    numeric_level = _STD_LEVEL_BY_NAME.get(level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    if not _STRUCTLOG_AVAILABLE:
        return

    renderer = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        cache_logger_on_first_use=True,
    )


class _StdlibAdapter:
    def __init__(self, bound: dict[str, Any]) -> None:
        self._bound = bound
        self._logger = logging.getLogger("ifixai")

    def bind(self, **kwargs: Any) -> _StdlibAdapter:
        return _StdlibAdapter({**self._bound, **kwargs})

    def _emit(self, level: int, event: str, **kwargs: Any) -> None:
        ctx = {**self._bound, **kwargs}
        ctx_str = " ".join(f"{k}={v}" for k, v in ctx.items())
        self._logger.log(level, "%s %s", event, ctx_str)

    def debug(self, event: str, **kwargs: Any) -> None:
        self._emit(logging.DEBUG, event, **kwargs)

    def info(self, event: str, **kwargs: Any) -> None:
        self._emit(logging.INFO, event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._emit(logging.WARNING, event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self._emit(logging.ERROR, event, **kwargs)


def bind_run(**bindings: Any) -> Any:
    """Return a logger pre-bound with the given structured context.

    Example:
        log = bind_run(run_id=run_id)
        log.bind(test_id=spec.test_id).info("inspection_started")
    """
    if _STRUCTLOG_AVAILABLE:
        return structlog.get_logger().bind(**bindings)
    return _StdlibAdapter(bindings)


def bind_run_context(**bindings: Any) -> None:
    """Bind suite-level context that flows through every subsequent log line.

    Uses `structlog.contextvars` so child asyncio tasks inherit the binding
    automatically. The stdlib fallback is a no-op.
    """
    if _STRUCTLOG_AVAILABLE:
        structlog.contextvars.bind_contextvars(**bindings)


def clear_run_context() -> None:
    if _STRUCTLOG_AVAILABLE:
        structlog.contextvars.clear_contextvars()
