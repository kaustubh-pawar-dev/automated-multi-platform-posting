"""Tests for CTA URL extraction and validation."""

import pytest

from app.utils.url_validator import InvalidUrlError, find_url_candidates, validate_cta_url


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Visit https://example.com today", ["https://example.com"]),
        ("Visit https://example.com.", ["https://example.com"]),
        ("Is it https://example.com?", ["https://example.com"]),
        ("(https://example.com)", ["https://example.com"]),
        (
            "See https://en.wikipedia.org/wiki/Foo_(bar) now",
            ["https://en.wikipedia.org/wiki/Foo_(bar)"],
        ),
        (
            "Read: https://example.com/a?x=1&y=2#top, thanks",
            ["https://example.com/a?x=1&y=2#top"],
        ),
        ('Say "https://example.com"', ["https://example.com"]),
        ("<https://example.com>", ["https://example.com"]),
        ("Go HTTPS://EXAMPLE.COM now", ["HTTPS://EXAMPLE.COM"]),
        ("Two https://a.com and http://b.org", ["https://a.com", "http://b.org"]),
        ("javascript:alert(1)", ["javascript:alert(1)"]),
        ("file:///etc/passwd", ["file:///etc/passwd"]),
        ("data:text/html;base64,AAAA", ["data:text/html;base64,AAAA"]),
        ("Visit www.example.com", ["www.example.com"]),
        ("No link here", []),
        ("Note: data: points and a@b.com", []),
        ("example.com is a bare domain", []),
        ("[docs](https://example.com/docs)", ["https://example.com/docs"]),
        (
            "Visit https://example.com/a?next=http://b.com now",
            ["https://example.com/a?next=http://b.com"],
        ),
    ],
)
def test_find_url_candidates(text: str, expected: list[str]) -> None:
    assert find_url_candidates(text) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://example.com/path?x=1&y=2#frag",
        "HTTPS://Example.COM/Path",
        "https://sub.domain.example.co.uk:8443/a/b",
        "https://bücher.de/shop",
        "https://example.com/wiki/Foo_(bar)",
        "https://8.8.8.8/x",
        "https://xn--bcher-kva.de",
        "https://example.com/a%20b?q=caf%C3%A9",
    ],
)
def test_valid_urls_accepted(url: str) -> None:
    validate_cta_url(url)


INVALID_URLS = [
    ("ftp://example.com", "UNSUPPORTED_URL_SCHEME"),
    ("javascript:alert(1)", "UNSUPPORTED_URL_SCHEME"),
    ("data:text/html;base64,AAAA", "UNSUPPORTED_URL_SCHEME"),
    ("file:///etc/passwd", "UNSUPPORTED_URL_SCHEME"),
    ("mailto:a@b.com", "UNSUPPORTED_URL_SCHEME"),
    ("www.example.com", "INVALID_URL"),
    ("https://", "INVALID_URL"),
    ("https:///path", "INVALID_URL"),
    ("https://user:pass@example.com", "INVALID_URL"),
    ("https://example.com:99999", "INVALID_URL"),
    ("https://example.com:0/", "INVALID_URL"),
    ("http://[::1", "INVALID_URL"),
    ("https://intranet", "INVALID_URL"),
    ("https://exa_mple.com", "INVALID_URL"),
    ("https://-example.com", "INVALID_URL"),
    ("https://example..com", "INVALID_URL"),
    ("https://example.123", "INVALID_URL"),
    ("https://0177.0.0.1", "INVALID_URL"),
    ("https://2130706433", "INVALID_URL"),
    ("https://example.com\\@evil.com", "INVALID_URL"),
    ("https://exa\x00mple.com", "INVALID_URL"),
    ("https://exa%6Dple.com", "INVALID_URL"),
    ("https://a.com,https://b.com", "INVALID_URL"),
    ("https://" + "a" * 64 + ".com", "INVALID_URL"),
    ("https://localhost", "URL_HOST_NOT_PUBLIC"),
    ("http://localhost:3000/app", "URL_HOST_NOT_PUBLIC"),
    ("https://127.0.0.1", "URL_HOST_NOT_PUBLIC"),
    ("https://192.168.1.10/x", "URL_HOST_NOT_PUBLIC"),
    ("http://10.0.0.5", "URL_HOST_NOT_PUBLIC"),
    ("http://169.254.169.254/latest", "URL_HOST_NOT_PUBLIC"),
    ("http://[::1]/", "URL_HOST_NOT_PUBLIC"),
    ("https://printer.local", "URL_HOST_NOT_PUBLIC"),
]


@pytest.mark.parametrize(("url", "code"), INVALID_URLS)
def test_invalid_urls_rejected_with_code(url: str, code: str) -> None:
    with pytest.raises(InvalidUrlError) as exc_info:
        validate_cta_url(url)
    assert exc_info.value.code == code
    assert exc_info.value.message