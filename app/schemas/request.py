"""Request schema for the posting API."""

from pydantic import BaseModel, ConfigDict, Field

# Business rules (200 characters, exactly one link) are enforced by the message
# validator and reported as HTTP 400. This far larger schema-level ceiling only
# protects the server from absurdly large bodies and is reported as HTTP 422.
MAX_REQUEST_MESSAGE_LENGTH = 10_000


class PostingRequest(BaseModel):
    """Body of ``POST /posting``.

    The client supplies only the message. Platform selection is internal, so any
    extra field (for example a ``platforms`` list) is rejected.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "message": (
                        "Check out our latest business platform and grow your "
                        "online visibility. https://example.com"
                    )
                }
            ]
        },
    )

    message: str = Field(
        ...,
        max_length=MAX_REQUEST_MESSAGE_LENGTH,
        description=(
            "Text to publish: at most 200 characters (after trimming) and exactly "
            "one http(s) call-to-action link."
        ),
    )