"""Parse and normalise LinkedIn profile URLs into a public identifier.

LinkedIn profile URLs appear in a lot of shapes in the wild::

    https://www.linkedin.com/in/williamhgates/
    https://linkedin.com/in/williamhgates
    http://www.linkedin.com/in/williamhgates?trk=nav
    https://in.linkedin.com/in/some-person-1a2b3c4
    https://www.linkedin.com/in/%E5%BC%A0%E4%B8%89-abc123/     (percent-encoded)
    linkedin.com/in/williamhgates                              (no scheme)
    williamhgates                                              (bare slug)
    https://www.linkedin.com/in/ACoAAA1234567890ABCDEF/        (obfuscated id)

All of them resolve to the same thing: the *public identifier* (vanity slug)
that Voyager endpoints are keyed on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

# Country/language subdomains: in., uk., de., www., or none at all.
_HOST_RE = re.compile(r"^(?:[a-z0-9-]+\.)*linkedin\.com$", re.IGNORECASE)

# A public identifier: letters (incl. unicode), digits, hyphens, underscores.
# LinkedIn allows 3-100 chars. We stay permissive but reject path separators
# and obviously-bogus input.
_PUBLIC_ID_RE = re.compile(r"^[\w\-\.%]{2,120}$", re.UNICODE)

# Paths that live under /in/ but are not profiles.
_RESERVED_SLUGS = {
    "edit",
    "unavailable",
    "me",
}


class InvalidProfileURLError(ValueError):
    """Raised when the supplied input cannot be resolved to a profile."""


@dataclass(frozen=True, slots=True)
class ParsedProfileURL:
    """Result of normalising user input."""

    public_id: str
    """The vanity slug, e.g. ``williamhgates``."""

    canonical_url: str
    """Always ``https://www.linkedin.com/in/<public_id>``."""

    is_obfuscated_id: bool
    """True when the slug is an ``ACoAAA…`` member-id rather than a vanity URL."""


def _looks_like_obfuscated_id(slug: str) -> bool:
    return slug.startswith(("ACoAA", "ACwAA")) and len(slug) > 15


def parse_profile_url(raw: str) -> ParsedProfileURL:
    """Normalise ``raw`` into a :class:`ParsedProfileURL`.

    Raises:
        InvalidProfileURLError: if the input is not a LinkedIn profile URL.
    """
    if not raw or not raw.strip():
        raise InvalidProfileURLError("Profile URL must not be empty.")

    candidate = raw.strip()

    # Bare slug with no dots and no slashes -> treat as a public identifier.
    if "/" not in candidate and "." not in candidate:
        slug = candidate
        return _build(slug)

    # Add a scheme so urlparse populates .netloc instead of .path.
    if not re.match(r"^https?://", candidate, re.IGNORECASE):
        candidate = "https://" + candidate.lstrip("/")

    try:
        parsed = urlparse(candidate)
    except ValueError as exc:  # pragma: no cover - urlparse rarely raises
        raise InvalidProfileURLError(f"Could not parse URL: {exc}") from exc

    host = (parsed.hostname or "").lower()
    if not _HOST_RE.match(host):
        raise InvalidProfileURLError(
            f"Host '{host or raw}' is not a linkedin.com domain. "
            "Expected something like https://www.linkedin.com/in/<username>."
        )

    segments = [s for s in parsed.path.split("/") if s]

    # Locale-prefixed paths such as /en/in/foo or /pub/foo/1/2/3.
    if segments and len(segments[0]) == 2 and segments[0].isalpha() and len(segments) > 1:
        segments = segments[1:]

    if not segments:
        raise InvalidProfileURLError(
            "URL has no path. Expected https://www.linkedin.com/in/<username>."
        )

    kind = segments[0].lower()

    if kind == "in" and len(segments) >= 2:
        slug = segments[1]
    elif kind == "pub" and len(segments) >= 2:
        # Legacy /pub/<name>/a/b/c format.
        slug = segments[1]
    elif kind in {"company", "school", "showcase"}:
        raise InvalidProfileURLError(
            f"'{kind}' URLs point to an organisation page, not a member profile. "
            "This API only supports /in/ member profiles."
        )
    else:
        raise InvalidProfileURLError(
            f"Unsupported LinkedIn path '/{kind}'. "
            "Expected https://www.linkedin.com/in/<username>."
        )

    return _build(slug)


def _build(slug: str) -> ParsedProfileURL:
    slug = unquote(slug).strip().strip("/")

    if not slug:
        raise InvalidProfileURLError("Profile URL is missing the username segment.")

    if slug.lower() in _RESERVED_SLUGS:
        raise InvalidProfileURLError(
            f"'{slug}' is a reserved LinkedIn path, not a public profile."
        )

    if not _PUBLIC_ID_RE.match(slug):
        raise InvalidProfileURLError(
            f"'{slug}' is not a valid LinkedIn public identifier."
        )

    return ParsedProfileURL(
        public_id=slug,
        canonical_url=f"https://www.linkedin.com/in/{slug}",
        is_obfuscated_id=_looks_like_obfuscated_id(slug),
    )
