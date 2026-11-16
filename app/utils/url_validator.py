"""Extraction and validation of the call-to-action (CTA) URL inside a message.

The CTA is *content to publish*. This module only inspects its text: it never
resolves, fetches or contacts the URL, so user input cannot trigger outbound
requests (no SSRF).

Only links with an explicit ``http://`` or ``https://`` prefix count as CTA
links. A bare domain such as ``example.com`` is ordinary text. Tokens that start
with ``www.`` or with a dangerous scheme (``javascript:``, ``data:``, ``file:``
...) are still detected, so they are rejected instead of slipping through.
"""

import ipaddress
import re
import unicodedata
from urllib.parse import urlsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})

# Where a URL-like token starts. The lookbehind stops matches in the middle of a
# word. ``data:`` and similar schemes only count when text follows the colon, so
# ordinary prose such as "Note: data: points" is not mistaken for a URL.
_CANDIDATE_RE = re.compile(
    r"(?<![A-Za-z0-9+.\-])"
    r"(?:"
    r"[A-Za-z][A-Za-z0-9+.\-]*://"
    r"|(?:javascript|data|file|vbscript|mailto|tel|sms):(?=\S)"
    r"|www\.(?=\S)"
    r")"
    r"\S*",
    re.IGNORECASE,
)

# Sentence punctuation or wrappers that follow a URL in prose but are not part of it.
_TRAILING_PUNCTUATION = frozenset(".,;:!?\"'*…»”’>")
_CLOSING_TO_OPENING = {")": "(", "]": "[", "}": "{"}

_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf"})  # control and invisible formatting characters
_LABEL_RE = re.compile(r"(?!-)[a-z0-9-]{1,63}(?<!-)")
_NON_PUBLIC_NAMES = frozenset({"localhost"})
_NON_PUBLIC_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain")


class InvalidUrlError(ValueError):
    """A URL failed validation. ``code`` is stable and machine-readable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _trim_trailing(candidate: str) -> str:
    """Remove trailing punctuation and unbalanced closing brackets from a match."""
    while candidate:
        last = candidate[-1]
        if last in _TRAILING_PUNCTUATION:
            candidate = candidate[:-1]
        elif last in _CLOSING_TO_OPENING and candidate.count(last) > candidate.count(
            _CLOSING_TO_OPENING[last]
        ):
            candidate = candidate[:-1]
        else:
            break
    return candidate


def find_url_candidates(text: str) -> list[str]:
    """Return every URL-like token in ``text``, exactly as written (minus trailing punctuation)."""
    return [_trim_trailing(match.group(0)) for match in _CANDIDATE_RE.finditer(text)]


def validate_cta_url(url: str) -> None:
    """Validate a CTA URL, raising ``InvalidUrlError`` when it is unacceptable.

    Rules: http/https only; a real hostname (domain with a TLD, or a public IP
    address); no credentials; a valid port; no control or backslash characters;
    and no localhost, private or otherwise non-public addresses.
    """
    if "\\" in url or any(unicodedata.category(ch) in _UNSAFE_CATEGORIES for ch in url):
        raise InvalidUrlError("INVALID_URL", "The link contains unsafe or invalid characters.")

    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError:
        raise InvalidUrlError("INVALID_URL", "The link is malformed.") from None

    scheme = parts.scheme
    if not scheme:
        raise InvalidUrlError("INVALID_URL", "The link must start with http:// or https://.")
    if scheme.lower() not in ALLOWED_SCHEMES:
        raise InvalidUrlError(
            "UNSUPPORTED_URL_SCHEME",
            f"Link scheme '{scheme.lower()}' is not allowed; only http and https are supported.",
        )
    if url[len(scheme) : len(scheme) + 3] != "://":
        raise InvalidUrlError("INVALID_URL", "The link must use the form scheme://host.")
    if "@" in parts.netloc:
        raise InvalidUrlError("INVALID_URL", "The link must not contain credentials.")
    if not host:
        raise InvalidUrlError("INVALID_URL", "The link must include a hostname.")
    if port == 0:
        raise InvalidUrlError("INVALID_URL", "The link contains an invalid port.")

    _validate_host(host)


def _validate_host(host: str) -> None:
    """Accept a public IP address or a syntactically valid public domain name."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None

    if address is not None:
        if not address.is_global:
            raise InvalidUrlError(
                "URL_HOST_NOT_PUBLIC", "The link must point to a publicly reachable address."
            )
        return

    name = host.lower().removesuffix(".")
    if name in _NON_PUBLIC_NAMES or name.endswith(_NON_PUBLIC_SUFFIXES):
        raise InvalidUrlError(
            "URL_HOST_NOT_PUBLIC", "The link must point to a publicly reachable address."
        )

    invalid_host = InvalidUrlError("INVALID_URL", "The link hostname is not a valid domain name.")
    labels = name.split(".")
    if len(labels) < 2 or not all(labels):
        raise invalid_host
    try:
        ascii_labels = [label.encode("idna").decode("ascii") for label in labels]
    except UnicodeError:
        raise invalid_host from None
    if len(".".join(ascii_labels)) > 253:
        raise invalid_host
    if not all(_LABEL_RE.fullmatch(label) for label in ascii_labels):
        raise invalid_host
    if ascii_labels[-1].isdigit():
        raise invalid_host