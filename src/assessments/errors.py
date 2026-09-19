"""Application error hierarchy and FastAPI handlers producing a consistent JSON error shape.

Error response body:
    {"error": {"code": str, "message": str, "request_id": str | null, "details"?: list}}
"""

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from assessments.logging import request_id_var

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base for expected, client-presentable failures."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ValidationFailedError(AppError):
    status_code = 422
    code = "validation_failed"


class PolicyViolationError(AppError):
    """An agent action was rejected by the safety policy."""

    status_code = 403
    code = "policy_violation"


def _error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    details: list[Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message, "request_id": request_id_var.get()}
    if details is not None:
        error["details"] = details
    return JSONResponse(status_code=status_code, content={"error": error}, headers=headers)


async def _handle_app_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)  # noqa: S101 - narrowing for the type checker
    log = logger.error if exc.status_code >= 500 else logger.info
    log("app error", extra={"error_code": exc.code, "status_code": exc.status_code})
    return _error_response(exc.status_code, exc.code, exc.message)


async def _handle_http_exception(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    code = "not_found" if exc.status_code == 404 else "http_error"
    return _error_response(exc.status_code, code, str(exc.detail), headers=exc.headers)


async def _handle_validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    return _error_response(
        422,
        "validation_failed",
        "Request validation failed",
        details=jsonable_encoder(exc.errors()),
    )


async def _handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled exception", exc_info=exc)
    return _error_response(500, "internal_error", "Internal server error")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected)
