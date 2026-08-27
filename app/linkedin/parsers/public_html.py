"""Extract a profile from LinkedIn's public (SEO) page.

Why this exists
---------------
This started as a fallback and is now the primary path, because LinkedIn
retired the legacy ``/voyager/api/identity/profiles/*`` REST endpoints — they
answer ``410 Gone``. The public page, by contrast, needs **no session cookie at
all**, so it cannot rot the way a scraped ``li_at`` does.

What LinkedIn puts on the page
------------------------------
A ``schema.org/Person`` graph inside ``<script type="application/ld+json">``::

    {
      "@type": "Person",
      "name": "Bill Gates",
      "description": "Chair of the Gates Foundation…",
      "disambiguatingDescription": "Creator, Top Voice",
      "address": {"addressLocality": "Seattle, Washington, United States",
                  "addressCountry": "US"},
      "image": {"contentUrl": "https://media.licdn.com/…"},
      "jobTitle": ["********", "*******"],
      "worksFor": [{"name": "Gates Foundation",
                    "url": "https://www.linkedin.com/company/gates-foundation",
                    "member": {"@type": "OrganizationRole",
                               "startDate": 2000, "endDate": 2020}}],
      "alumniOf": [{"name": "Harvard University",
                    "url": "https://www.linkedin.com/school/harvard-university/",
                    "member": {"startDate": 1973, "endDate": 1975}}],
      "knowsLanguage": [...], "awards": [...],
      "interactionStatistic": {"userInteractionCount": 40594829}
    }

Masking
-------
LinkedIn deliberately redacts parts of this for anonymous viewers, replacing
characters with asterisks — ``"jobTitle": ["********"]``, or a company rendered
as ``"************ ****** "``. Those are *not* real values, so emitting them
would be worse than emitting nothing: a consumer cannot tell ``"********"`` from
a genuine job title. :func:`_unmasked` drops them to ``None`` and the extraction
records a warning so the caller knows data was withheld rather than absent.
"""

from __future__ import annotations

import json
import re
from typing import Any

from selectolax.parser import HTMLParser

from app.linkedin.parsers.common import (
    clean_str,
    duration_months,
    render_date,
)
from app.models.profile import (
    Company,
    DateParts,
    DateRange,
    Education,
    Honor,
    Image,
    ImageVariant,
    Language,
    LinkedInProfile,
    Location,
    NetworkInfo,
    Position,
    School,
)
from app.utils.logging import get_logger

log = get_logger(__name__)

# Shown instead of a profile when LinkedIn wants you signed in.
_AUTHWALL_MARKERS = (
    "authwall",
    "Join LinkedIn to view",
    "sign_in_context",
    "linkedin.com/authwall",
)

# A value LinkedIn has redacted: only asterisks, spaces and punctuation.
_MASKED_RE = re.compile(r"^[\s*·•\-–—]+$")

_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def is_authwalled(html: str) -> bool:
    """True when LinkedIn served a sign-in gate instead of the profile."""
    if not html:
        return True
    head = html[:30000]
    return any(marker in head for marker in _AUTHWALL_MARKERS)


def _unmasked(value: Any) -> str | None:
    """Return the string only if LinkedIn did not redact it."""
    text = clean_str(value)
    if text is None:
        return None
    if _MASKED_RE.match(text) or "***" in text:
        return None
    return text


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


# ---------------------------------------------------------------------------
# Locating the Person node
# ---------------------------------------------------------------------------


def _iter_jsonld(html: str, tree: HTMLParser) -> list[Any]:
    """Collect every JSON-LD block.

    selectolax occasionally misses a script whose content confuses the parser,
    so a regex sweep backstops it.
    """
    blocks: list[Any] = []
    seen: set[str] = set()

    for node in tree.css('script[type="application/ld+json"]'):
        raw = node.text(strip=False) or ""
        if raw.strip():
            seen.add(raw.strip())

    for raw in _LD_RE.findall(html):
        if raw.strip():
            seen.add(raw.strip())

    for raw in seen:
        try:
            blocks.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return blocks


def find_person(blocks: list[Any]) -> dict[str, Any] | None:
    """Locate the Person node, descending through ``@graph`` wrappers."""
    stack: list[Any] = list(blocks)
    depth = 0
    while stack and depth < 10_000:
        depth += 1
        node = stack.pop(0)
        if isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, dict):
            if node.get("@type") == "Person":
                return node
            graph = node.get("@graph")
            if isinstance(graph, list):
                stack.extend(graph)
    return None


# ---------------------------------------------------------------------------
# Field extraction
# ---------------------------------------------------------------------------


# Wide enough to accept any date a profile could legitimately carry, narrow
# enough to reject a millisecond timestamp mistaken for a year.
_MIN_YEAR, _MAX_YEAR = 1800, 2100


def _date_from_schema(value: Any) -> DateParts | None:
    """schema.org dates arrive as a bare year int, or an ISO-ish string."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and _MIN_YEAR <= value <= _MAX_YEAR:
        return DateParts(year=value, text=str(value))

    text = clean_str(value)
    if not text:
        return None

    match = re.match(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", text)
    if not match:
        return None

    year = int(match.group(1))
    month = int(match.group(2)) if match.group(2) else None
    day = int(match.group(3)) if match.group(3) else None

    if month is not None and not 1 <= month <= 12:
        month = None
    if day is not None and not 1 <= day <= 31:
        day = None

    return DateParts(year=year, month=month, day=day, text=render_date(year, month, day))


def _range_from_member(member: Any) -> DateRange | None:
    """Build a DateRange from an ``OrganizationRole`` sub-node."""
    if not isinstance(member, dict):
        return None

    start = _date_from_schema(member.get("startDate"))
    end = _date_from_schema(member.get("endDate"))
    if start is None and end is None:
        return None

    start_text = start.text if start else None
    end_text = end.text if end else ("Present" if start else None)
    if start_text and end_text:
        text = f"{start_text} - {end_text}"
    else:
        text = start_text or end_text

    return DateRange(
        start=start,
        end=end,
        is_current=end is None,
        duration_months=duration_months(start, end),
        text=text,
    )


def _location(person: dict[str, Any]) -> Location | None:
    address = person.get("address")
    if isinstance(address, list):
        address = address[0] if address else None
    if not isinstance(address, dict):
        return None

    locality = _unmasked(address.get("addressLocality"))
    region = _unmasked(address.get("addressRegion"))
    country_code = _unmasked(address.get("addressCountry"))

    text = locality or ", ".join(p for p in (region, country_code) if p) or None

    # addressLocality is usually the full "City, State, Country" string.
    city = None
    country = None
    if locality:
        parts = [p.strip() for p in locality.split(",") if p.strip()]
        if parts:
            city = parts[0]
        if len(parts) > 2:
            country = parts[-1]

    if not any([text, city, country, country_code]):
        return None

    return Location(
        text=text,
        city=city,
        country=country,
        country_code=country_code.lower() if country_code else None,
    )


def _image(person: dict[str, Any]) -> Image | None:
    raw = person.get("image")
    if isinstance(raw, list):
        raw = raw[0] if raw else None

    url = None
    if isinstance(raw, dict):
        url = _unmasked(raw.get("contentUrl")) or _unmasked(raw.get("url"))
    elif isinstance(raw, str):
        url = _unmasked(raw)

    if not url or not url.startswith("http"):
        return None

    # The CDN path encodes the rendered size, e.g. ".../photo-shrink_200_200/...".
    width = height = None
    dims = re.search(r"shrink_(\d+)_(\d+)", url)
    if dims:
        width, height = int(dims.group(1)), int(dims.group(2))

    return Image(
        url=url,
        width=width,
        height=height,
        variants=[ImageVariant(url=url, width=width, height=height)],
    )


def _positions(person: dict[str, Any], warnings: list[str]) -> list[Position]:
    """``worksFor`` entries, aligned with ``jobTitle`` where possible.

    LinkedIn emits ``jobTitle`` as a parallel array to ``worksFor``, so index i
    of one lines up with index i of the other — but either side may be masked.
    """
    titles = [_unmasked(t) for t in _as_list(person.get("jobTitle"))]
    masked_titles = sum(
        1 for t in _as_list(person.get("jobTitle")) if _unmasked(t) is None
    )

    out: list[Position] = []
    masked_companies = 0

    for index, org in enumerate(_as_list(person.get("worksFor"))):
        if isinstance(org, str):
            name, url = _unmasked(org), None
        elif isinstance(org, dict):
            name = _unmasked(org.get("name"))
            url = _unmasked(org.get("url"))
        else:
            continue

        if name is None:
            masked_companies += 1

        title = titles[index] if index < len(titles) else None
        date_range = (
            _range_from_member(org.get("member")) if isinstance(org, dict) else None
        )

        # Skip entries where LinkedIn withheld everything useful.
        if name is None and title is None and date_range is None:
            continue

        universal = None
        if url:
            slug = re.search(r"/company/([^/?#]+)", url)
            universal = slug.group(1) if slug else None

        out.append(
            Position(
                title=title,
                company=Company(name=name, linkedin_url=url, universal_name=universal)
                if (name or url)
                else None,
                company_name=name,
                date_range=date_range,
            )
        )

    if masked_titles:
        warnings.append(
            f"LinkedIn redacted {masked_titles} job title(s) on the public page; "
            "they are returned as null rather than as asterisks."
        )
    if masked_companies:
        warnings.append(
            f"LinkedIn redacted {masked_companies} employer name(s) on the public page."
        )

    return out


def _education(person: dict[str, Any]) -> list[Education]:
    out: list[Education] = []
    for org in _as_list(person.get("alumniOf")):
        if isinstance(org, str):
            name, url = _unmasked(org), None
            member = None
        elif isinstance(org, dict):
            name = _unmasked(org.get("name"))
            url = _unmasked(org.get("url"))
            member = org.get("member")
        else:
            continue

        date_range = _range_from_member(member)
        if name is None and date_range is None:
            continue

        # alumniOf carries degree/field on some profiles.
        degree = field = None
        if isinstance(member, dict):
            degree = _unmasked(member.get("degree")) or _unmasked(
                member.get("roleName")
            )
            field = _unmasked(member.get("fieldOfStudy"))

        out.append(
            Education(
                school_name=name,
                school=School(name=name, linkedin_url=url) if (name or url) else None,
                degree_name=degree,
                field_of_study=field,
                date_range=date_range,
            )
        )
    return out


def _languages(person: dict[str, Any]) -> list[Language]:
    out: list[Language] = []
    for item in _as_list(person.get("knowsLanguage")):
        if isinstance(item, str):
            name = _unmasked(item)
        elif isinstance(item, dict):
            name = _unmasked(item.get("name"))
        else:
            continue
        if name:
            out.append(Language(name=name))
    return out


def _honors(person: dict[str, Any]) -> list[Honor]:
    out: list[Honor] = []
    for item in _as_list(person.get("awards")):
        if isinstance(item, str):
            title = _unmasked(item)
        elif isinstance(item, dict):
            title = _unmasked(item.get("name")) or _unmasked(item.get("title"))
        else:
            continue
        if title:
            out.append(Honor(title=title))
    return out


def _network(person: dict[str, Any]) -> NetworkInfo | None:
    """``interactionStatistic`` carries the follower count."""
    followers = None
    for stat in _as_list(person.get("interactionStatistic")):
        if not isinstance(stat, dict):
            continue
        count = stat.get("userInteractionCount")
        interaction = str(stat.get("interactionType") or "")
        if isinstance(count, int) and (
            "Follow" in interaction or stat.get("name") == "Follows"
        ):
            followers = count
            break

    if followers is None:
        return None
    return NetworkInfo(followers_count=followers)


# ---------------------------------------------------------------------------
# Open Graph fallback
# ---------------------------------------------------------------------------


def _meta(tree: HTMLParser, prop: str) -> str | None:
    node = tree.css_first(f'meta[property="{prop}"]') or tree.css_first(
        f'meta[name="{prop}"]'
    )
    if node is None:
        return None
    return _unmasked(node.attributes.get("content"))


def _from_open_graph(
    tree: HTMLParser, *, public_id: str, profile_url: str, warnings: list[str]
) -> LinkedInProfile:
    warnings.append(
        "No schema.org/Person block was present; fell back to Open Graph meta "
        "tags, which carry only name, headline and photo."
    )

    og_title = _meta(tree, "og:title")
    og_desc = _meta(tree, "og:description")
    og_image = _meta(tree, "og:image")

    # "Jane Doe - Staff Engineer - Acme | LinkedIn"
    name = headline = None
    if og_title:
        head = og_title.split("|")[0].strip()
        pieces = [p.strip() for p in head.split(" - ") if p.strip()]
        if pieces:
            name = pieces[0]
        if len(pieces) > 1:
            headline = " - ".join(pieces[1:])

    first, _, last = (name or "").partition(" ")

    return LinkedInProfile(
        public_id=public_id,
        profile_url=profile_url,
        first_name=clean_str(first),
        last_name=clean_str(last),
        full_name=name,
        headline=headline,
        about=og_desc,
        profile_picture=(
            Image(url=og_image, variants=[ImageVariant(url=og_image)])
            if og_image
            else None
        ),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_public_html(
    html: str,
    *,
    public_id: str,
    profile_url: str,
    warnings: list[str],
) -> LinkedInProfile:
    """Build a profile from the rendered public page."""
    tree = HTMLParser(html)
    person = find_person(_iter_jsonld(html, tree))

    if person is None:
        return _from_open_graph(
            tree, public_id=public_id, profile_url=profile_url, warnings=warnings
        )

    name = _unmasked(person.get("name"))
    given = _unmasked(person.get("givenName"))
    family = _unmasked(person.get("familyName"))

    if name and not (given or family):
        given, _, family = name.partition(" ")
        given, family = clean_str(given), clean_str(family)

    # `description` is the About text; `disambiguatingDescription` is the badge
    # line ("Creator, Top Voice"). Neither is exactly the headline, but on the
    # public page description is the closest thing offered.
    description = _unmasked(person.get("description"))
    badge = _unmasked(person.get("disambiguatingDescription"))

    positions = _positions(person, warnings)
    current = next(
        (p for p in positions if p.date_range and p.date_range.is_current),
        positions[0] if positions else None,
    )

    warnings.append(
        "Extracted from LinkedIn's public page. Skills, certifications, contact "
        "details and full role descriptions are only exposed to an authenticated "
        "session and are therefore unavailable here."
    )

    return LinkedInProfile(
        public_id=public_id,
        profile_url=profile_url,
        first_name=given,
        last_name=family,
        full_name=name or " ".join(p for p in (given, family) if p) or None,
        headline=description or badge,
        about=description,
        location=_location(person),
        profile_picture=_image(person),
        experience=positions,
        education=_education(person),
        languages=_languages(person),
        honors=_honors(person),
        network_info=_network(person),
        current_position=current,
        is_influencer=True if (badge and "Top Voice" in badge) else None,
    )
