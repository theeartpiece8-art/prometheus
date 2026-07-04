"""
Structured error handling, per 06_UI_UX_Specification.md ("All errors
should provide: Title, Description, Suggested Action, Reference ID.
Never expose raw backend exceptions") and 12_Coding_Standards.md ("Never
expose stack traces to users").
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.infrastructure.logging.logger import get_logger

logger = get_logger("errors")


def _error_body(title: str, description: str, reference_id: str, suggested_action: str | None = None) -> dict:
    return {
        "error": {
            "title": title,
            "description": description,
            "suggested_action": suggested_action,
            "reference_id": reference_id,
        }
    }


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        reference_id = str(uuid.uuid4())
        safe_errors = jsonable_encoder(exc.errors())
        logger.warning(
            "http.validation_error",
            extra={"reference_id": reference_id, "path": request.url.path, "errors": safe_errors},
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_error_body(
                title="Invalid request",
                description="One or more fields failed validation.",
                reference_id=reference_id,
                suggested_action="Check the request body against the API specification and try again.",
            )
            | {"validation_errors": safe_errors},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        reference_id = str(uuid.uuid4())
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(
                title=_title_for_status(exc.status_code),
                description=str(exc.detail),
                reference_id=reference_id,
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        reference_id = str(uuid.uuid4())
        logger.exception("http.unhandled_exception", extra={"reference_id": reference_id, "path": request.url.path})
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body(
                title="Internal server error",
                description="Something went wrong on our end. This has been logged for investigation.",
                reference_id=reference_id,
                suggested_action="Try again shortly. If the problem persists, contact support with the reference ID.",
            ),
        )


def _title_for_status(code: int) -> str:
    return {
        400: "Bad request", 401: "Authentication required", 403: "Access denied",
        404: "Not found", 409: "Conflict", 422: "Invalid request", 429: "Too many requests",
    }.get(code, "Request failed")
