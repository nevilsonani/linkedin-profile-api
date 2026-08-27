"""URL normalisation is the API's front door — it must be strict but forgiving."""

from __future__ import annotations

import pytest

from app.utils.url import InvalidProfileURLError, parse_profile_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.linkedin.com/in/williamhgates/", "williamhgates"),
        ("https://www.linkedin.com/in/williamhgates", "williamhgates"),
        ("http://linkedin.com/in/williamhgates", "williamhgates"),
        ("linkedin.com/in/williamhgates", "williamhgates"),
        ("www.linkedin.com/in/williamhgates/", "williamhgates"),
        ("https://in.linkedin.com/in/some-person-1a2b3c4", "some-person-1a2b3c4"),
        ("https://uk.linkedin.com/in/jane-doe", "jane-doe"),
        ("https://www.linkedin.com/in/williamhgates?trk=nav_type", "williamhgates"),
        ("https://www.linkedin.com/in/williamhgates/#experience", "williamhgates"),
        ("  https://www.linkedin.com/in/williamhgates/  ", "williamhgates"),
        ("williamhgates", "williamhgates"),
        ("https://www.linkedin.com/in/ACoAAA1234567890ABCDEF/", "ACoAAA1234567890ABCDEF"),
        # Locale-prefixed path.
        ("https://www.linkedin.com/en/in/jane-doe", "jane-doe"),
        # Legacy /pub/ URLs.
        ("https://www.linkedin.com/pub/jane-doe/1/2/3", "jane-doe"),
    ],
)
def test_accepts_real_world_shapes(raw: str, expected: str) -> None:
    assert parse_profile_url(raw).public_id == expected


def test_percent_encoded_slug_is_decoded() -> None:
    parsed = parse_profile_url("https://www.linkedin.com/in/jos%C3%A9-garcia")
    assert parsed.public_id == "josé-garcia"


def test_canonical_url_is_normalised() -> None:
    parsed = parse_profile_url("http://in.linkedin.com/in/williamhgates?trk=x")
    assert parsed.canonical_url == "https://www.linkedin.com/in/williamhgates"


def test_detects_obfuscated_member_id() -> None:
    assert parse_profile_url("https://www.linkedin.com/in/ACoAAA1234567890ABCDEF").is_obfuscated_id
    assert not parse_profile_url("https://www.linkedin.com/in/williamhgates").is_obfuscated_id


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "https://twitter.com/in/someone",
        "https://example.com/in/someone",
        "https://www.linkedin.com/",
        "https://www.linkedin.com/feed/",
        "https://www.linkedin.com/company/google",
        "https://www.linkedin.com/school/mit",
        "https://www.linkedin.com/in/",
        "https://notlinkedin.com/in/someone",
    ],
)
def test_rejects_bad_input(raw: str) -> None:
    with pytest.raises(InvalidProfileURLError):
        parse_profile_url(raw)


def test_company_url_error_explains_why() -> None:
    with pytest.raises(InvalidProfileURLError, match="organisation page"):
        parse_profile_url("https://www.linkedin.com/company/google")


def test_lookalike_domain_is_rejected() -> None:
    """`linkedin.com.evil.tld` must not pass the host check."""
    with pytest.raises(InvalidProfileURLError):
        parse_profile_url("https://linkedin.com.evil.tld/in/someone")
