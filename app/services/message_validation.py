"""Business rules for the message submitted to ``POST /posting``.

Rules, all reported together so a client can fix everything in one round trip:

* The message is trimmed of leading and trailing whitespace. The trimmed text is
  what gets validated and published; nothing else is modified, and nothing is
  ever truncated.
* The trimmed message must not be empty and must not exceed 200 characters
  (Python characters, i.e. Unicode code points, not bytes).
* It must not contain control characters other than newline, carriage return
  and tab.
* It must contain exactly one CTA link, and that link must pass
  ``validate_cta_url``. A link-only message is accepted.
"""

import unicodedata
from dataclasses import dataclass

from app.core.exceptions import MessageValidationError, ValidationIssue
from app.utils.url_validator import InvalidUrlError, find_url_candidates, validate_cta_url

MAX_MESSAGE_LENGTH = 200
_ALLOWED_CONTROL_CHARACTERS = frozenset("\n\r\t")


@dataclass(frozen=True, slots=True)
class ValidatedMessage:
    """A message that passed every rule."""

    text: str
    cta_url: str


def _has_control_characters(text: str) -> bool:
    return any(
        unicodedata.category(ch) == "Cc" and ch not in _ALLOWED_CONTROL_CHARACTERS for ch in text
    )


def _check_cta(text: str) -> str | ValidationIssue:
    """Return the single valid CTA URL, or the issue explaining why there isn't one."""
    candidates = find_url_candidates(text)
    if not candidates:
        return ValidationIssue(
            "MISSING_CTA_URL",
            "Message must contain exactly one http:// or https:// link; none was found.",
        )
    if len(candidates) > 1:
        return ValidationIssue(
            "MULTIPLE_CTA_URLS",
            f"Message must contain exactly one link; {len(candidates)} were found.",
        )
    try:
        validate_cta_url(candidates[0])
    except InvalidUrlError as exc:
        return ValidationIssue(exc.code, exc.message)
    return candidates[0]


def validate_message(raw: str) -> ValidatedMessage:
    """Validate ``raw`` and return the trimmed text with its CTA URL.

    Raises:
        MessageValidationError: listing every rule that was violated.
    """
    text = raw.strip()
    if not text:
        raise MessageValidationError(
            [ValidationIssue("EMPTY_MESSAGE", "Message must not be empty.")]
        )

    issues: list[ValidationIssue] = []
    if len(text) > MAX_MESSAGE_LENGTH:
        issues.append(
            ValidationIssue(
                "MESSAGE_TOO_LONG",
                f"Message is {len(text)} characters long; the maximum is {MAX_MESSAGE_LENGTH}.",
            )
        )
    if _has_control_characters(text):
        issues.append(
            ValidationIssue(
                "INVALID_CHARACTERS", "Message contains unsupported control characters."
            )
        )

    cta = _check_cta(text)
    if isinstance(cta, ValidationIssue):
        raise MessageValidationError([*issues, cta])
    if issues:
        raise MessageValidationError(issues)
    return ValidatedMessage(text=text, cta_url=cta)