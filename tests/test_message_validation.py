"""Tests for message validation: length, whitespace and exactly-one-CTA rules."""

import dataclasses

import pytest

from app.core.exceptions import MessageValidationError
from app.services.message_validation import MAX_MESSAGE_LENGTH, validate_message

URL = "https://example.com"


def _message_of_length(length: int) -> str:
    """Build a message of exactly ``length`` characters that ends with the URL."""
    return f"{'a' * (length - len(URL) - 1)} {URL}"


def _codes(text: str) -> list[str]:
    with pytest.raises(MessageValidationError) as exc_info:
        validate_message(text)
    return [issue.code for issue in exc_info.value.issues]


def test_valid_sample_message() -> None:
    text = (
        "Check out our latest business platform and grow your online "
        "visibility. https://example.com"
    )
    result = validate_message(text)
    assert result.text == text
    assert result.cta_url == URL


def test_exactly_200_characters_accepted() -> None:
    text = _message_of_length(MAX_MESSAGE_LENGTH)
    assert len(text) == 200
    result = validate_message(text)
    assert result.text == text
    assert result.cta_url == URL


def test_201_characters_rejected_without_truncation() -> None:
    assert _codes(_message_of_length(MAX_MESSAGE_LENGTH + 1)) == ["MESSAGE_TOO_LONG"]


def test_surrounding_whitespace_is_trimmed_and_not_counted() -> None:
    inner = _message_of_length(MAX_MESSAGE_LENGTH)
    result = validate_message(f"   \n{inner}\t  ")
    assert result.text == inner
    assert len(result.text) == 200


def test_original_text_is_preserved() -> None:
    text = "Hello,  world!! https://example.com/a?b=1#c."
    result = validate_message(text)
    assert result.text == text
    assert result.cta_url == "https://example.com/a?b=1#c"


def test_link_only_message_accepted() -> None:
    assert validate_message(URL).cta_url == URL


def test_multiline_message_accepted() -> None:
    result = validate_message("Line one\nLine two https://example.com")
    assert result.cta_url == URL


def test_length_counts_characters_not_bytes() -> None:
    text = "é" * (200 - len(URL) - 1) + " " + URL
    assert len(text) == 200
    assert len(text.encode("utf-8")) > 200
    assert validate_message(text).cta_url == URL


@pytest.mark.parametrize(
    ("text", "codes"),
    [
        ("", ["EMPTY_MESSAGE"]),
        ("   \n\t ", ["EMPTY_MESSAGE"]),
        ("Hello world", ["MISSING_CTA_URL"]),
        ("Visit example.com today", ["MISSING_CTA_URL"]),
        ("a https://a.com b https://b.com", ["MULTIPLE_CTA_URLS"]),
        ("Same https://a.com and https://a.com", ["MULTIPLE_CTA_URLS"]),
        ("Visit www.example.com", ["INVALID_URL"]),
        ("Click javascript:alert(1)", ["UNSUPPORTED_URL_SCHEME"]),
        ("Get ftp://files.example.com/x", ["UNSUPPORTED_URL_SCHEME"]),
        ("Get data:text/html;base64,AAAA", ["UNSUPPORTED_URL_SCHEME"]),
        ("Mail mailto:a@b.com", ["UNSUPPORTED_URL_SCHEME"]),
        ("Visit http://localhost:3000/app", ["URL_HOST_NOT_PUBLIC"]),
        ("Visit https://a.com,https://b.com", ["INVALID_URL"]),
        ("Visit https://", ["INVALID_URL"]),
        ("Visit https://user:pw@example.com", ["INVALID_URL"]),
        ("Hello\x00 https://example.com", ["INVALID_CHARACTERS"]),
        ("a" * 201, ["MESSAGE_TOO_LONG", "MISSING_CTA_URL"]),
    ],
)
def test_invalid_messages_report_every_issue(text: str, codes: list[str]) -> None:
    assert _codes(text) == codes


def test_exception_carries_issues_and_readable_message() -> None:
    with pytest.raises(MessageValidationError) as exc_info:
        validate_message("a" * 201)
    exc = exc_info.value
    assert [issue.code for issue in exc.issues] == ["MESSAGE_TOO_LONG", "MISSING_CTA_URL"]
    assert "201" in str(exc)
    assert "link" in str(exc)


def test_validated_message_is_immutable() -> None:
    result = validate_message(URL)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.text = "changed"  # type: ignore[misc]