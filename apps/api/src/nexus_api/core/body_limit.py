"""Reject oversized request bodies before the application reads them.

Starlette parses multipart bodies completely (spooling to a temporary file) before
an endpoint runs, so an endpoint-level size check alone would still let a client
stream gigabytes to disk. This pure-ASGI middleware enforces the limit on the raw
byte stream:

* a declared ``Content-Length`` above the limit is rejected immediately;
* otherwise (e.g. chunked transfer encoding) bytes are counted as they arrive;
  reading stops as soon as the limit is crossed and the client gets a 413.
"""

from __future__ import annotations

import contextlib

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nexus_api.core.errors import error_response


class _BodyTooLargeError(Exception):
    pass


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = _content_length(scope)
        if declared is not None and declared > self.max_body_bytes:
            await self._reject(scope, receive, send)
            return

        received = 0
        exceeded = False
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    exceeded = True
                    raise _BodyTooLargeError
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            # FastAPI turns any exception raised while reading the body into its
            # own 400 response; once the limit is crossed, that response is
            # dropped and replaced by the 413 below.
            if exceeded and not response_started:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        with contextlib.suppress(_BodyTooLargeError):
            await self.app(scope, limited_receive, guarded_send)
        if exceeded and not response_started:
            await self._reject(scope, receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        state = scope.get("state") or {}
        request_id = state.get("request_id", "unknown")
        response = error_response(
            status_code=413,
            code="request_too_large",
            message="The request body is too large.",
            request_id=request_id if isinstance(request_id, str) else "unknown",
            headers={"Connection": "close"},
        )
        await response(scope, receive, send)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope["headers"]:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None
