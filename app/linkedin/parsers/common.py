"""Shared conversions from Voyager shapes to our schema primitives."""

from __future__ import annotations

import calendar
from datetime import UTC, datetime
from typing import Any

from app.models.profile import (
    Company,
    DateParts,
    DateRange,
    Image,
    ImageVariant,
    School,
)

_MONTHS = list(calendar.month_abbr)  # ['', 'Jan', 'Feb', ...]

# LinkedIn's language-proficiency enum, mapped to the UI labels.
PROFICIENCY_LABELS = {
    "NATIVE_OR_BILINGUAL": "Native or bilingual proficiency",
    "FULL_PROFESSIONAL": "Full professional proficiency",
    "PROFESSIONAL_WORKING": "Professional working proficiency",
    "LIMITED_WORKING": "Limited working proficiency",
    "ELEMENTARY": "Elementary proficiency",
}


# ---------------------------------------------------------------------------
# Scalars
# ---------------------------------------------------------------------------


def clean_str(value: Any) -> str | None:
    """Return a trimmed string, or None for anything empty/non-string."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def first_of(mapping: Any, *keys: str) -> Any:
    """Return the first present, non-None key from a dict."""
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        val = mapping.get(key)
        if val is not None:
            return val
    return None


def unwrap_union(value: Any) -> Any:
    """Unwrap Rest.li's single-key union envelopes.

    Voyager encodes polymorphic values as ``{"com.linkedin.common.VectorImage":
    {...}}``. When we see exactly one key and it looks like a Java FQN, the
    value inside is what we actually want.
    """
    if isinstance(value, dict) and len(value) == 1:
        (key, inner), = value.items()
        if key.startswith("com.linkedin.") and isinstance(inner, dict | list):
            return inner
    return value


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


def parse_date(raw: Any) -> DateParts | None:
    """Convert ``{"month": 6, "year": 2021}`` into :class:`DateParts`."""
    if not isinstance(raw, dict):
        return None

    year = raw.get("year")
    month = raw.get("month")
    day = raw.get("day")

    year = year if isinstance(year, int) else None
    month = month if isinstance(month, int) and 1 <= month <= 12 else None
    day = day if isinstance(day, int) and 1 <= day <= 31 else None

    if year is None and month is None and day is None:
        return None

    return DateParts(year=year, month=month, day=day, text=render_date(year, month, day))


def render_date(year: int | None, month: int | None, day: int | None) -> str | None:
    if year and month and day:
        return f"{_MONTHS[month]} {day}, {year}"
    if year and month:
        return f"{_MONTHS[month]} {year}"
    if year:
        return str(year)
    if month and day:
        return f"{_MONTHS[month]} {day}"
    if month:
        return _MONTHS[month]
    return None


def _month_index(parts: DateParts) -> int | None:
    """Absolute month number since year 0, for arithmetic on partial dates."""
    if parts.year is None:
        return None
    return parts.year * 12 + ((parts.month or 1) - 1)


def parse_date_range(time_period: Any) -> DateRange | None:
    """Convert Voyager's ``timePeriod`` into a :class:`DateRange`."""
    if not isinstance(time_period, dict):
        return None

    start = parse_date(time_period.get("startDate"))
    end = parse_date(time_period.get("endDate"))

    if start is None and end is None:
        return None

    is_current = end is None
    duration = duration_months(start, end)

    start_text = start.text if start else None
    end_text = end.text if end else ("Present" if start else None)
    if start_text and end_text:
        text = f"{start_text} - {end_text}"
    else:
        text = start_text or end_text

    return DateRange(
        start=start,
        end=end,
        is_current=is_current,
        duration_months=duration,
        text=text,
    )


def duration_months(start: DateParts | None, end: DateParts | None) -> int | None:
    if start is None:
        return None
    start_idx = _month_index(start)
    if start_idx is None:
        return None

    if end is not None:
        end_idx = _month_index(end)
        if end_idx is None:
            return None
    else:
        now = datetime.now(UTC)
        end_idx = now.year * 12 + (now.month - 1)

    # LinkedIn counts both endpoints, so Jan->Jan of the same year is 1 month.
    return max(0, end_idx - start_idx + 1)


def month_span(range_: DateRange | None) -> tuple[int, int] | None:
    """Return ``(start_month_index, end_month_index)`` for union arithmetic."""
    if range_ is None or range_.start is None:
        return None
    start_idx = _month_index(range_.start)
    if start_idx is None:
        return None
    if range_.end is not None:
        end_idx = _month_index(range_.end)
        if end_idx is None:
            return None
    else:
        now = datetime.now(UTC)
        end_idx = now.year * 12 + (now.month - 1)
    if end_idx < start_idx:
        return None
    return start_idx, end_idx


def union_months(spans: list[tuple[int, int]]) -> int:
    """Total months covered by ``spans``, merging overlaps.

    Someone holding two concurrent roles for a year has 12 months of
    experience, not 24.
    """
    if not spans:
        return 0
    ordered = sorted(spans)
    total = 0
    cur_start, cur_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= cur_end + 1:  # contiguous or overlapping
            cur_end = max(cur_end, end)
        else:
            total += cur_end - cur_start + 1
            cur_start, cur_end = start, end
    total += cur_end - cur_start + 1
    return total


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


def parse_image(raw: Any) -> Image | None:
    """Resolve a Voyager ``VectorImage`` into absolute CDN URLs.

    LinkedIn splits every image into a ``rootUrl`` plus per-size
    ``fileIdentifyingUrlPathSegment`` suffixes carrying the signature and
    expiry. Neither half is usable alone.
    """
    node = unwrap_union(raw)
    if not isinstance(node, dict):
        return None

    # Some payloads nest one more level under displayImageReference/vectorImage.
    for key in ("vectorImage", "displayImageReference", "image"):
        if key in node:
            inner = unwrap_union(node.get(key))
            if isinstance(inner, dict) and (
                "artifacts" in inner or "rootUrl" in inner
            ):
                node = inner
                break

    root = clean_str(node.get("rootUrl"))
    artifacts = node.get("artifacts")

    if not root or not isinstance(artifacts, list):
        # Occasionally LinkedIn hands back a plain absolute URL instead.
        direct = clean_str(node.get("url")) or clean_str(node.get("rootUrl"))
        if direct and direct.startswith("http"):
            return Image(url=direct, variants=[ImageVariant(url=direct)])
        return None

    variants: list[ImageVariant] = []
    expires_at: datetime | None = None

    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        segment = clean_str(artifact.get("fileIdentifyingUrlPathSegment"))
        if not segment:
            continue
        variants.append(
            ImageVariant(
                url=root + segment,
                width=artifact.get("width") if isinstance(artifact.get("width"), int) else None,
                height=artifact.get("height") if isinstance(artifact.get("height"), int) else None,
            )
        )
        raw_expiry = artifact.get("expiresAt")
        if isinstance(raw_expiry, int) and raw_expiry > 0:
            # Milliseconds since epoch.
            candidate = datetime.fromtimestamp(raw_expiry / 1000, tz=UTC)
            if expires_at is None or candidate < expires_at:
                expires_at = candidate

    if not variants:
        return None

    variants.sort(key=lambda v: (v.width or 0) * (v.height or 0), reverse=True)
    largest = variants[0]

    return Image(
        url=largest.url,
        width=largest.width,
        height=largest.height,
        variants=variants,
        expires_at=expires_at,
    )


# ---------------------------------------------------------------------------
# Companies & schools
# ---------------------------------------------------------------------------


def parse_company(raw: Any, *, fallback_name: str | None = None) -> Company | None:
    """Build a :class:`Company` from a position's ``company`` block.

    The interesting fields live at two levels: ``miniCompany`` holds identity
    (name, logo, vanity slug) while the outer object holds firmographics
    (industry, headcount).
    """
    if not isinstance(raw, dict):
        return Company(name=fallback_name) if fallback_name else None

    mini = raw.get("miniCompany")
    mini = mini if isinstance(mini, dict) else {}

    name = clean_str(mini.get("name")) or fallback_name
    universal = clean_str(mini.get("universalName"))
    urn = clean_str(mini.get("entityUrn")) or clean_str(raw.get("companyUrn"))

    industries = [
        s for s in (clean_str(i) for i in raw.get("industries") or []) if s
    ]

    headcount = raw.get("employeeCountRange")
    headcount_text = _render_range(headcount) if isinstance(headcount, dict) else None

    logo = parse_image(mini.get("logo"))

    if not any([name, urn, logo, industries, headcount_text]):
        return None

    return Company(
        name=name,
        urn=urn,
        linkedin_url=f"https://www.linkedin.com/company/{universal}" if universal else None,
        universal_name=universal,
        logo=logo,
        industries=industries,
        employee_count_range=headcount_text,
        staffing_company=raw.get("staffingCompany") if isinstance(raw.get("staffingCompany"), bool) else None,
    )


def _render_range(raw: dict[str, Any]) -> str | None:
    start, end = raw.get("start"), raw.get("end")
    if isinstance(start, int) and isinstance(end, int):
        return f"{start}-{end}"
    if isinstance(start, int):
        return f"{start}+"
    if isinstance(end, int):
        return f"1-{end}"
    return None


def parse_school(raw: Any, *, fallback_name: str | None = None) -> School | None:
    if not isinstance(raw, dict):
        return School(name=fallback_name) if fallback_name else None

    name = clean_str(raw.get("schoolName")) or clean_str(raw.get("name")) or fallback_name
    urn = clean_str(raw.get("entityUrn")) or clean_str(raw.get("objectUrn"))
    logo = parse_image(raw.get("logo"))

    if not any([name, urn, logo]):
        return None

    return School(name=name, urn=urn, logo=logo)


# ---------------------------------------------------------------------------
# URNs
# ---------------------------------------------------------------------------


def member_id_from_urn(urn: str | None) -> str | None:
    """Extract the obfuscated member id from ``urn:li:fs_profile:ACoAAA…``."""
    if not urn:
        return None
    tail = urn.rsplit(":", 1)[-1]
    tail = tail.strip("()").split(",")[0]
    return tail or None
