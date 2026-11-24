"""Structured logging via structlog, bridged with the standard library.

Both our own ``structlog`` loggers and third-party stdlib loggers (uvicorn,
SQLAlchemy, Alembic) are rendered by the same formatter, so every log line has the
same shape and carries context variables such as ``request_id``.
"""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from nexus_api.core.config import LogFormat, LogLevel

_HANDLER_NAME = "nexus-structlog"


def _drop_color_message(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """Remove uvicorn's ANSI-coloured duplicate of the message."""
    event_dict.pop("color_message", None)
    return event_dict


def configure_logging(level: LogLevel, fmt: LogFormat) -> None:
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.stdlib.ExtraAdder(),
        _drop_color_message,
    ]

    renderer: Processor
    final_processors: list[Processor] = [structlog.stdlib.ProcessorFormatter.remove_processors_meta]
    if fmt == "json":
        # show_locals=False: local variables can hold secrets (credentials,
        # tokens, request payloads) and must never be shipped to log storage.
        final_processors.append(
            structlog.processors.ExceptionRenderer(
                structlog.tracebacks.ExceptionDictTransformer(show_locals=False)
            )
        )
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()
    final_processors.append(renderer)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=final_processors,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(formatter)

    # Replace only our own handler (idempotent across repeated app creation) and
    # leave handlers installed by other tools, such as pytest, untouched.
    root = logging.getLogger()
    for existing in [h for h in root.handlers if h.get_name() == _HANDLER_NAME]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # Route uvicorn's loggers through the root handler. Request logging is done by
    # our request-ID middleware, so uvicorn's own access log is silenced.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
