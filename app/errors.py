# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran
# and Ameya Mathew, who specified the requirements and design and reviewed the
# implementation.
# See the "Credits and authorship" section of README.md.
"""A single error shape for the whole API.

Every failure — validation, upstream GitHub, or unexpected — is rendered as::

    {"error": {"code", "message", "details", "request_id"}}

FastAPI's default 422 for request-validation failures is deliberately remapped to
400, because the API contract promises 400 for invalid payloads.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logging import get_logger

log = get_logger("errors")


class AppError(Exception):
    """Base class for every error this service raises deliberately."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        self.headers = headers or {}
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code


class ValidationError(AppError):
    status_code = 400
    code = "validation_error"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"


class UpstreamUnavailableError(AppError):
    status_code = 503
    code = "upstream_unavailable"


def error_body(
    code: str,
    message: str,
    *,
    details: Any = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": request_id,
        }
    }


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        log.warning("app_error", code=exc.code, status_code=exc.status_code, message=exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(
                exc.code, exc.message, details=exc.details, request_id=_request_id(request)
            ),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {
                "location": list(err.get("loc", [])),
                "message": err.get("msg", ""),
                "type": err.get("type", ""),
            }
            for err in exc.errors()
        ]
        log.warning("request_validation_failed", error_count=len(details))
        return JSONResponse(
            status_code=400,
            content=jsonable_encoder(
                error_body(
                    "validation_error",
                    "Request payload or query parameters are invalid.",
                    details=details,
                    request_id=_request_id(request),
                )
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(
                codes.get(exc.status_code, "http_error"),
                str(exc.detail),
                request_id=_request_id(request),
            ),
            headers=getattr(exc, "headers", None) or {},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_exception")
        return JSONResponse(
            status_code=500,
            content=error_body(
                "internal_error",
                "An unexpected error occurred.",
                request_id=_request_id(request),
            ),
        )
