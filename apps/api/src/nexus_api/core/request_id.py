"""Request correlation middleware (pure ASGI).

Responsibilities:

* Accept a well-formed incoming ``X-Request-ID`` or generate a new one.
* Expose it via ``request.state.request_id`` and structlog context variables.
* Echo it in the ``X-Request-ID`` response header.
* Log one structured line per request (method, path, status, duration).
* Act as the last-resort error boundary: an unhandled exception becomes a
  generic 500 error envelope. Stack traces go to logs, never to clients.

A pure ASGI middleware is used instead of ``BaseHTTPMiddleware`` because it does
not buffer or wrap streaming responses (important for SSE in later milestones)
and does not break context-variable propagation.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Final

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nexus_api.core.errors import internal_error_response

REQUEST_ID_HEADER: Final = "X-Request-ID"
_VALID_REQUEST_ID: Final = re.compile(r"^[A-Za-z0-9._-]{8,128}$")

logger = structlog.stdlib.get_logger(__name__)


def resolve_request_id(incoming: str | None) -> str:
    """Return the incoming ID if it is safe to propagate, else a fresh UUID4."""
    if incoming is not None and _VALID_REQUEST_ID.fullmatch(incoming):
        return incoming
    return uuid.uuid4().hex


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming: str | None = None
        for key, value in scope["headers"]:
            if key == b"x-request-id":
                incoming = value.decode("latin-1")
                break
        request_id = resolve_request_id(incoming)
        scope.setdefault("state", {})["request_id"] = request_id

        status_code = 500
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()
        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            logger.exception("unhandled_exception", method=scope["method"], path=scope["path"])
            if response_started:
                raise
            response = internal_error_response(request_id)
            await response(scope, receive, send_with_request_id)
        finally:
            logger.info(
                "request_completed",
                method=scope["method"],
                path=scope["path"],
                status_code=status_code,
                duration_ms=round((time.perf_counter() - start) * 1000, 2),
            )
            structlog.contextvars.unbind_contextvars("request_id")
