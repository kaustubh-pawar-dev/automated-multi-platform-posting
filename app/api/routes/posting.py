"""The ``POST /posting`` endpoint."""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.dependencies import get_posting_service
from app.api.errors import error_response
from app.schemas.errors import ErrorDetail, ErrorResponse
from app.schemas.request import PostingRequest
from app.schemas.response import PostingResponse
from app.services.message_validation import validate_message
from app.services.posting_service import PostingService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["posting"])

DESCRIPTION = """
Publishes one message to every platform configured on the server. **Platform
selection is internal**: the request contains only the message.

**Message rules** (violations return **400** with every problem listed):

* Not empty, and at most **200 characters** after trimming surrounding whitespace.
  Nothing is ever truncated or rewritten.
* Contains **exactly one** call-to-action link.
* The link starts with `http://` or `https://` and points to a public host
  (no credentials, `localhost`, private addresses or other schemes).

**How results are reported**

* HTTP **200** means the request was processed. Each entry in `results` carries
  that platform's own outcome (`success` or `failed`), and one failing platform
  never stops the others.
* `success` is `true` only when at least one platform was attempted **and every
  attempted platform succeeded**. Platforms that are not configured are not
  attempted; they are listed in `skipped_platforms`.
* `date` and `time` are the publication moment in **UTC**.
* `posted_url` is the link the platform reported, or `null` when it has none.
  `public_access` is `true` only when the post is known to be publicly visible.
* HTTP **503** means no platform is configured on the server.
"""

_SUCCESS_EXAMPLE = {
    "success": True,
    "total_platforms": 2,
    "successful_count": 2,
    "failed_count": 0,
    "results": [
        {
            "platform": "Mastodon",
            "status": "success",
            "posted_url": "https://mastodon.example/@user/110000000000000000",
            "public_access": True,
            "date": "2026-11-15",
            "time": "10:30:00",
            "error": None,
        },
        {
            "platform": "Bluesky",
            "status": "success",
            "posted_url": "https://bsky.app/profile/user.bsky.example/post/3kexample",
            "public_access": True,
            "date": "2026-11-15",
            "time": "10:30:01",
            "error": None,
        },
    ],
    "skipped_platforms": ["Telegram", "LinkedIn"],
}

_PARTIAL_EXAMPLE = {
    "success": False,
    "total_platforms": 2,
    "successful_count": 1,
    "failed_count": 1,
    "results": [
        {
            "platform": "Mastodon",
            "status": "success",
            "posted_url": "https://mastodon.example/@user/110000000000000000",
            "public_access": True,
            "date": "2026-11-15",
            "time": "10:30:00",
            "error": None,
        },
        {
            "platform": "LinkedIn",
            "status": "failed",
            "posted_url": None,
            "public_access": False,
            "date": None,
            "time": None,
            "error": "Authentication failed",
        },
    ],
    "skipped_platforms": [],
}


def _error_example(error: str, message: str, code: str, detail: str) -> dict[str, Any]:
    return {
        "error": error,
        "message": message,
        "details": [{"code": code, "message": detail}],
        "request_id": None,
    }


def _json_examples(**examples: dict[str, Any]) -> dict[str, Any]:
    return {
        "application/json": {
            "examples": {name: {"value": value} for name, value in examples.items()}
        }
    }


RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Request processed. Inspect `success` and each platform's `status`.",
        "content": _json_examples(all_succeeded=_SUCCESS_EXAMPLE, partial_failure=_PARTIAL_EXAMPLE),
    },
    400: {
        "model": ErrorResponse,
        "description": "The message breaks a business rule (length or link rules).",
        "content": _json_examples(
            multiple_links=_error_example(
                "validation_error",
                "The message failed validation.",
                "MULTIPLE_CTA_URLS",
                "Message must contain exactly one link; 2 were found.",
            ),
            too_long=_error_example(
                "validation_error",
                "The message failed validation.",
                "MESSAGE_TOO_LONG",
                "Message is 215 characters long; the maximum is 200.",
            ),
        ),
    },
    422: {
        "model": ErrorResponse,
        "description": "The request body is malformed (missing field, wrong type, bad JSON, "
        "or an unknown field such as `platforms`).",
        "content": _json_examples(
            missing_message=_error_example(
                "request_validation_error",
                "The request body is invalid.",
                "FIELD_REQUIRED",
                "message: Field required",
            ),
            platform_selection_not_allowed=_error_example(
                "request_validation_error",
                "The request body is invalid.",
                "UNKNOWN_FIELD",
                "platforms: Extra inputs are not permitted",
            ),
        ),
    },
    500: {
        "model": ErrorResponse,
        "description": "Unexpected internal error. The response contains no internal details.",
        "content": _json_examples(
            internal_error=_error_example(
                "internal_error", "An unexpected error occurred.", "", ""
            )
        ),
    },
    503: {
        "model": ErrorResponse,
        "description": "No publishing platform is configured on this server.",
        "content": _json_examples(
            nothing_configured=_error_example(
                "no_platforms_configured",
                "No publishing platforms are configured on this server.",
                "PLATFORM_NOT_CONFIGURED",
                "Mastodon is not configured",
            )
        ),
    },
}


@router.post(
    "/posting",
    response_model=PostingResponse,
    summary="Publish a message to all configured platforms",
    description=DESCRIPTION,
    responses=RESPONSES,
)
async def create_posting(
    payload: PostingRequest,
    service: Annotated[PostingService, Depends(get_posting_service)],
) -> PostingResponse | JSONResponse:
    """Validate the message, publish it everywhere configured, and report each outcome."""
    validated = validate_message(payload.message)
    logger.info("posting_request_validated message_chars=%d", len(validated.text))

    response = await service.publish(validated)
    if response.total_platforms == 0:
        return error_response(
            503,
            "no_platforms_configured",
            "No publishing platforms are configured on this server.",
            [
                ErrorDetail(code="PLATFORM_NOT_CONFIGURED", message=f"{name} is not configured")
                for name in response.skipped_platforms
            ],
        )
    return response