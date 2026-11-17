"""Request context middleware: request ids, request logging and a body-size limit.

* Every request gets an id: a valid client-supplied ``X-Request-ID`` is reused
  (so a caller can correlate), anything else is replaced by a random one. The id
  is returned in the response header and stored for log lines and error bodies.
* One ``request_received`` and one ``request_completed`` line are logged per
  request. Only the method and the path are logged: never the query string, the
  headers or the body. The path is logged with ``%a`` so control characters such
  as newlines cannot forge extra log lines.
* A declared ``Content-Length`` above the limit is rejected with HTTP 413 before
  the body is read. Chunked uploads without a length are not covered here; put a
  reverse proxy with a body limit in front of production deployments.
"""

import logging
import re
import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import REQUEST_ID_HEADER, request_id_var
from app.schemas.errors import ErrorResponse

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 64 * 1024
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{8,64}")
_MAX_LOGGED_PATH = 200


def _resolve_request_id(candidate: str | None) -> str:
    if candidate is not None and _REQUEST_ID_RE.fullmatch(candidate):
        return candidate
    return uuid.uuid4().hex


class RequestContextMiddleware:
    """Pure ASGI middleware (keeps contextvars intact across the whole request)."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    def _declared_too_large(self, headers: Headers) -> bool:
        declared = headers.get("content-length")
        if declared is None or not declared.isascii() or not declared.isdigit():
            return False
        return int(declared) > self.max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = _resolve_request_id(headers.get(REQUEST_ID_HEADER))
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_var.set(request_id)
        status = 500
        started = time.monotonic()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        method = scope["method"]
        path = scope["path"][:_MAX_LOGGED_PATH]
        logger.info("request_received method=%s path=%a", method, path)
        try:
            if self._declared_too_large(headers):
                body = ErrorResponse(
                    error="payload_too_large",
                    message=f"The request body must not exceed {self.max_body_bytes} bytes.",
                    request_id=request_id,
                )
                response = JSONResponse(status_code=413, content=body.model_dump(mode="json"))
                await response(scope, receive, send_with_request_id)
            else:
                await self.app(scope, receive, send_with_request_id)
        finally:
            logger.info(
                "request_completed method=%s path=%a status=%d duration_ms=%d",
                method,
                path,
                status,
                int((time.monotonic() - started) * 1000),
            )
            request_id_var.reset(token)