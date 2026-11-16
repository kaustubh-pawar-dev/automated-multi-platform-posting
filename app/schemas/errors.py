"""Structured error body returned by the API for every non-2xx response."""

from typing import Self

from pydantic import BaseModel, Field

from app.core.exceptions import MessageValidationError


class ErrorDetail(BaseModel):
    """One specific problem."""

    code: str = Field(
        ..., description="Stable machine-readable code.", examples=["MESSAGE_TOO_LONG"]
    )
    message: str = Field(..., description="Human-readable explanation.")


class ErrorResponse(BaseModel):
    """Consistent error envelope. Never contains secrets or stack traces."""

    error: str = Field(..., description="Error category.", examples=["validation_error"])
    message: str = Field(..., description="Summary of what went wrong.")
    details: list[ErrorDetail] = Field(default_factory=list, description="Individual problems.")
    request_id: str | None = Field(
        default=None, description="Correlation id for this request (populated in a later step)."
    )

    @classmethod
    def from_validation_error(cls, exc: MessageValidationError) -> Self:
        """Build the error body for a rejected message."""
        return cls(
            error="validation_error",
            message="The message failed validation.",
            details=[ErrorDetail(code=issue.code, message=issue.message) for issue in exc.issues],
        )