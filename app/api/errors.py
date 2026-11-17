"""Exception handlers that turn every failure into the structured ``ErrorResponse``.

Clients always receive the same JSON envelope (``error``, ``message``, ``details``,
``request_id``) plus an ``X-Request-ID`` header, and never see stack traces,
exception texts, submitted input or internal details:

* ``MessageValidationError``  -> 400 (business rules: length, links)
* ``RequestValidationError``  -> 422 (malformed request: missing/extra/invalid fields)
* HTTP errors (404, 405...)   -> same status, fixed wording
* anything else               -> 500 with a generic message
"""

import logging
from collections.abc import Iterable, Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import MessageValidationError
from app.core.logging import REQUEST_ID_HEADER, current_request_id
from app.schemas.errors import ErrorDetail, ErrorResponse

logger = logging.getLogger(__name__)

_REQUEST_ERROR_CODES = {
    "missing": "FIELD_REQUIRED",
    "extra_forbidden": "UNKNOWN_FIELD",
    "string_type": "INVALID_TYPE",
    "string_too_long": "VALUE_TOO_LONG",
    "json_invalid": "INVALID_JSON",
    "model_attributes_type": "INVALID_BODY",
}


def error_response(
    status_code: int,
    error: str,
    message: str,
    details: Iterable[ErrorDetail] = (),
    headers: Mapping[str, str] | None = None,
    request_id: str | None = None,
) -> JSONResponse:
    """Build a JSON response carrying the standard error envelope and request id."""
    resolved_id = request_id or current_request_id()
    body = ErrorResponse(
        error=error, message=message, details=list(details), request_id=resolved_id
    )
    merged: dict[str, str] = dict(headers) if headers else {}
    if resolved_id:
        merged[REQUEST_ID_HEADER] = resolved_id
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
        headers=merged or None,
    )


def _describe_request_error(error: Mapping[str, Any]) -> ErrorDetail:
    """Describe one Pydantic error without echoing the submitted input."""
    error_type = str(error.get("type", ""))
    location = [str(part) for part in error.get("loc", ()) if part != "body"]
    field = "body" if error_type == "json_invalid" or not location else ".".join(location)
    code = _REQUEST_ERROR_CODES.get(error_type, "INVALID_REQUEST")
    return ErrorDetail(code=code, message=f"{field}: {error.get('msg', 'Invalid value')}")


async def handle_message_validation_error(
    request: Request, exc: MessageValidationError
) -> JSONResponse:
    """The message broke a business rule."""
    logger.info("request_rejected status=400 codes=%s", [issue.code for issue in exc.issues])
    details = [ErrorDetail(code=issue.code, message=issue.message) for issue in exc.issues]
    return error_response(400, "validation_error", "The message failed validation.", details)


async def handle_request_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """The request body is not shaped as the API requires."""
    details = [_describe_request_error(error) for error in exc.errors()]
    logger.info("request_rejected status=422 codes=%s", [detail.code for detail in details])
    return error_response(
        422, "request_validation_error", "The request body is invalid.", details
    )


async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Routing-level errors such as unknown paths and unsupported methods."""
    try:
        phrase = HTTPStatus(exc.status_code).phrase
    except ValueError:
        phrase = "HTTP error"
    return error_response(
        exc.status_code, phrase.lower().replace(" ", "_"), phrase, headers=exc.headers
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Last line of defence: log the type only and return a generic message.

    This handler runs outside the request middleware, so the request id is read
    from the request state instead of the context variable.
    """
    request_id = getattr(request.state, "request_id", None)
    logger.error(
        "unhandled_error method=%s path=%a error_type=%s",
        request.method,
        request.url.path[:200],
        type(exc).__name__,
    )
    logger.debug("unhandled error traceback", exc_info=True)
    return error_response(
        500, "internal_error", "An unexpected error occurred.", request_id=request_id
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach every handler to the application."""
    app.add_exception_handler(MessageValidationError, handle_message_validation_error)
    app.add_exception_handler(RequestValidationError, handle_request_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(Exception, handle_unexpected_error)