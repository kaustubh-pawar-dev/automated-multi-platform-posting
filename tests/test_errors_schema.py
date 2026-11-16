"""Tests for the structured error response schema."""

from app.core.exceptions import MessageValidationError, ValidationIssue
from app.schemas.errors import ErrorResponse


def test_from_validation_error_builds_structured_body() -> None:
    exc = MessageValidationError(
        [ValidationIssue("MULTIPLE_CTA_URLS", "Message must contain exactly one link; 2 were found.")]
    )
    assert ErrorResponse.from_validation_error(exc).model_dump(mode="json") == {
        "error": "validation_error",
        "message": "The message failed validation.",
        "details": [
            {
                "code": "MULTIPLE_CTA_URLS",
                "message": "Message must contain exactly one link; 2 were found.",
            }
        ],
        "request_id": None,
    }


def test_defaults_have_no_details_or_request_id() -> None:
    body = ErrorResponse(error="internal_error", message="Unexpected error.")
    assert body.details == []
    assert body.request_id is None