from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

logger = logging.getLogger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"

_STATUS_SLUG: dict[int, str] = {
    400: "bad-request",
    401: "unauthorized",
    403: "forbidden",
    404: "not-found",
    405: "method-not-allowed",
    409: "conflict",
    422: "unprocessable-entity",
    429: "too-many-requests",
    500: "internal-server-error",
    503: "service-unavailable",
}

_STATUS_TITLE: dict[int, str] = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
    503: "Service Unavailable",
}


class ProblemDetailsException(Exception):
    """RFC 7807 Problem Details exception.

    Raise this instead of HTTPException inside forge.api.*. The registered
    handler populates ``instance`` from the request path automatically.
    """

    def __init__(
        self,
        status: int,
        type: str,
        title: str,
        detail: str,
        headers: dict[str, str] | None = None,
        errors: list[Any] | None = None,
    ) -> None:
        self.status = status
        self.type = type
        self.title = title
        self.detail = detail
        self.headers = headers
        self.errors = errors
        super().__init__(detail)


def _problem_response(request: Request, exc: ProblemDetailsException) -> JSONResponse:
    body: dict[str, Any] = {
        "type": exc.type,
        "title": exc.title,
        "status": exc.status,
        "detail": exc.detail,
        "instance": request.url.path,
    }
    if exc.errors is not None:
        # jsonable_encoder mirrors the validation_exception_handler path so
        # non-JSON-native values in error contexts (datetime, bytes, etc.)
        # serialize consistently regardless of which handler emits them.
        body["errors"] = jsonable_encoder(exc.errors)
    headers = {**(exc.headers or {}), "Content-Type": PROBLEM_CONTENT_TYPE}
    return JSONResponse(content=body, status_code=exc.status, headers=headers)


async def problem_details_exception_handler(request: Request, exc: ProblemDetailsException) -> JSONResponse:
    return _problem_response(request, exc)


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Intercepts FastAPI's own 422 for path/query/body validation failures."""
    return JSONResponse(
        content={
            "type": "urn:forge:error:validation-failed",
            "title": "Request validation failed",
            "status": 422,
            "detail": "One or more request fields failed validation.",
            "instance": request.url.path,
            "errors": jsonable_encoder(exc.errors()),
        },
        status_code=422,
        headers={"Content-Type": PROBLEM_CONTENT_TYPE},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for unexpected exceptions raised inside routes.

    Logs the original exception server-side and emits a generic 500 problem
    document. The exception ``str(exc)`` is deliberately NOT leaked to clients.
    """
    logger.error(
        "Unhandled exception during %s %s",
        request.method,
        request.url.path,
        exc_info=exc,
    )
    return JSONResponse(
        content={
            "type": "urn:forge:error:internal-server-error",
            "title": "Internal Server Error",
            "status": 500,
            "detail": "An internal error occurred",
            "instance": request.url.path,
        },
        status_code=500,
        headers={"Content-Type": PROBLEM_CONTENT_TYPE},
    )


async def http_exception_fallback_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Catches any HTTPException not migrated to ProblemDetailsException.

    Covers Starlette-generated 404/405 and any missed raise sites.
    """
    slug = _STATUS_SLUG.get(exc.status_code, f"http-{exc.status_code}")
    title = _STATUS_TITLE.get(exc.status_code, f"HTTP {exc.status_code}")
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    headers: dict[str, str] = {"Content-Type": PROBLEM_CONTENT_TYPE}
    if exc.headers:
        headers.update(exc.headers)
    return JSONResponse(
        content={
            "type": f"urn:forge:error:{slug}",
            "title": title,
            "status": exc.status_code,
            "detail": detail,
            "instance": request.url.path,
        },
        status_code=exc.status_code,
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register all RFC 7807 exception handlers. Call once from get_app()."""
    app.add_exception_handler(ProblemDetailsException, problem_details_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    # Use starlette.exceptions.HTTPException (the base class) so that
    # Starlette's own 404/405 routing errors are also caught here.
    app.add_exception_handler(HTTPException, http_exception_fallback_handler)  # type: ignore[arg-type]
    # Catch-all for anything else (SQLAlchemyError, KeyError, etc.) so the
    # OpenAPI claim that all 5xx are application/problem+json holds.
    app.add_exception_handler(Exception, unhandled_exception_handler)
