"""Consistent error envelope and exception handlers.

Every error response has the same JSON shape::

    {"error": {"code": "not_found", "message": "...", "request_id": "...", "details": null}}

Internal details (stack traces, exception messages, SQL, credentials) are never
included. Validation details are reduced to location, message and type: the raw
``input`` echoed by Pydantic is dropped because it may contain sensitive data.
"""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus

import structlog
from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = structlog.stdlib.get_logger(__name__)


class ErrorDetail(BaseModel):
    loc: list[str | int]
    message: str
    type: str


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str
    details: list[ErrorDetail] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class AppError(Exception):
    """Base class for expected, domain-level failures.

    Service code raises subclasses of this instead of HTTP exceptions, keeping
    business logic independent of the transport layer.
    """

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "bad_request"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _status_code_slug(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase.lower().replace(" ", "_").replace("-", "_")
    except ValueError:
        return "error"


def _request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    return request_id if isinstance(request_id, str) else "unknown"


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    details: list[ErrorDetail] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(code=code, message=message, request_id=request_id, details=details)
    )
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(body),
        headers=headers,
    )


def internal_error_response(request_id: str) -> JSONResponse:
    return error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="internal_error",
        message="An unexpected error occurred.",
        request_id=request_id,
    )


async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101 - narrowing for mypy
    message = exc.detail if isinstance(exc.detail, str) else HTTPStatus(exc.status_code).phrase
    return error_response(
        status_code=exc.status_code,
        code=_status_code_slug(exc.status_code),
        message=message,
        request_id=_request_id(request),
        headers=exc.headers,
    )


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101 - narrowing for mypy
    details = [
        ErrorDetail(loc=list(err["loc"]), message=str(err["msg"]), type=str(err["type"]))
        for err in exc.errors()
    ]
    return error_response(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="validation_error",
        message="Request validation failed.",
        request_id=_request_id(request),
        details=details,
    )


async def _handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)  # noqa: S101 - narrowing for mypy
    logger.info("app_error", code=exc.code, status_code=exc.status_code)
    return error_response(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        request_id=_request_id(request),
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(AppError, _handle_app_error)
