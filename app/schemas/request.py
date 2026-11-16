"""Request schema for the posting API."""

from pydantic import BaseModel, ConfigDict, Field


class PostingRequest(BaseModel):
    """Body of ``POST /posting``.

    The client supplies only the message. Platform selection is internal, so any
    extra field (for example a ``platforms`` list) is rejected. Message content
    rules (length, exactly one CTA URL) are added in Step 4.
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
        description="Text to publish. Must contain exactly one CTA link.",
    )