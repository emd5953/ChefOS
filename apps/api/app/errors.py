"""RFC 9457 problem+json errors with a stable `code` field."""

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.context import request_id_var

log = structlog.get_logger()

_TITLES = {
    "validation_error": "Validation failed",
    "unauthenticated": "Authentication required",
    "session_expired": "Session expired",
    "csrf_failed": "CSRF check failed",
    "forbidden": "Forbidden",
    "not_found": "Not found",
    "conflict": "Conflict",
    "rate_limited": "Too many requests",
    "unavailable": "Service unavailable",
    "internal_error": "Internal server error",
}

_STATUS_CODES = {
    400: "validation_error",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "not_found",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
    503: "unavailable",
}


class ProblemError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        detail: str | None = None,
        *,
        errors: dict[str, str] | None = None,
        title: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail or code)
        self.status = status
        self.code = code
        self.detail = detail
        self.errors = errors
        self.title = title or _TITLES.get(code, code)
        self.headers = headers


def validation_error(
    errors: dict[str, str], detail: str = "Request validation failed"
) -> ProblemError:
    return ProblemError(422, "validation_error", detail, errors=errors)


def not_found(detail: str = "Resource not found") -> ProblemError:
    return ProblemError(404, "not_found", detail)


def forbidden(detail: str = "You do not have permission to do this") -> ProblemError:
    return ProblemError(403, "forbidden", detail)


def unavailable(detail: str) -> ProblemError:
    return ProblemError(503, "unavailable", detail)


def problem_response(
    status: int,
    code: str,
    title: str,
    detail: str | None = None,
    errors: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = request_id_var.get()
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "code": code,
        "detail": detail or title,
        "request_id": request_id,
    }
    if errors:
        body["errors"] = errors
    return JSONResponse(
        body,
        status_code=status,
        media_type="application/problem+json",
        headers={"X-Request-ID": request_id, **(headers or {})},
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _problem(_: Request, exc: ProblemError) -> JSONResponse:
        return problem_response(
            exc.status, exc.code, exc.title, exc.detail, exc.errors, exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors: dict[str, str] = {}
        for err in exc.errors():
            loc = [str(p) for p in err["loc"] if p not in ("body", "query", "path")]
            key = ".".join(loc) or "_"
            errors.setdefault(key, err["msg"].removeprefix("Value error, "))
        return problem_response(
            422,
            "validation_error",
            _TITLES["validation_error"],
            "Request validation failed",
            errors,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(
            exc.status_code, "internal_error" if exc.status_code >= 500 else "validation_error"
        )
        return problem_response(
            exc.status_code,
            code,
            _TITLES.get(code, code),
            str(exc.detail),
            headers=dict(exc.headers or {}),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_exception", exc_info=exc)
        return problem_response(
            500, "internal_error", _TITLES["internal_error"], "An unexpected error occurred"
        )
