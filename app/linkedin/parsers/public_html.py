"""Fallback extractor for the rendered profile page.

Used only when Voyager refuses (challenge, or a profile the API won't serve).
It yields materially less than Voyager, so it is strictly a degraded path.

Two sources are mined, best first:

1. **JSON-LD** — LinkedIn embeds a ``schema.org/Person`` graph in a
   ``<script type="application/ld+json">`` tag on public profile pages. Covers
   name, headline, image, location, employer, and alumni-of.
2. **Meta tags** — Open Graph ``og:title`` / ``og:description`` / ``og:image``
   as a last resort when even the JSON-LD is absent.
"""

from __future__ import annotations

import json
from typing import Any

from selectolax.parser import HTMLParser

from app.linkedin.parsers.common import clean_str
from app.models.profile import (
    Company,
    Education,
    Image,
    ImageVariant,
    LinkedInProfile,
    Location,
    Position,
)
from app.utils.logging import get_logger

log = get_logger(__name__)

# Present when LinkedIn shows the "sign in to view" gate instead of a profile.
_AUTHWALL_MARKERS = ("authwall", "Join LinkedIn to view", "sign_in_context")


def is_authwalled(html: str) -> bool:
    head = html[:20000]
    return any(marker in head for marker in _AUTHWALL_MARKERS)


def _iter_jsonld(tree: HTMLParser) -> list[Any]:
    blocks: list[Any] = []
    for node in tree.css('script[type="application/ld+json"]'):
        text = node.text(strip=True)
        if not text:
            continue
        try:
            blocks.append(json.loads(text))
        except json.JSONDecodeError:
            continue
    return blocks


def _find_person(blocks: list[Any]) -> dict[str, Any] | None:
    """Locate the Person node inside possibly-nested @graph structures."""
    stack: list[Any] = list(blocks)
    while stack:
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


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _location_from_jsonld(person: dict[str, Any]) -> Location | None:
    address = person.get("address")
    if isinstance(address, list):
        address = address[0] if address else None
    if not isinstance(address, dict):
        return None

    city = clean_str(address.get("addressLocality"))
    region = clean_str(address.get("addressRegion"))
    country = clean_str(address.get("addressCountry"))

    text = ", ".join(p for p in (city, region, country) if p) or None
    if not any([text, city, country]):
        return None

    return Location(text=text, city=city, country=country)


def _image_from_jsonld(person: dict[str, Any]) -> Image | None:
    image = person.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    url = None
    if isinstance(image, dict):
        url = clean_str(image.get("contentUrl")) or clean_str(image.get("url"))
    elif isinstance(image, str):
        url = clean_str(image)
    if not url:
        return None
    return Image(url=url, variants=[ImageVariant(url=url)])


def _positions_from_jsonld(person: dict[str, Any]) -> list[Position]:
    out: list[Position] = []
    for org in _as_list(person.get("worksFor")):
        if isinstance(org, dict):
            name = clean_str(org.get("name"))
        elif isinstance(org, str):
            name = clean_str(org)
        else:
            name = None
        if not name:
            continue
        out.append(
            Position(
                title=clean_str(person.get("jobTitle"))
                if isinstance(person.get("jobTitle"), str)
                else None,
                company=Company(name=name),
                company_name=name,
            )
        )
    return out


def _education_from_jsonld(person: dict[str, Any]) -> list[Education]:
    out: list[Education] = []
    for org in _as_list(person.get("alumniOf")):
        if isinstance(org, dict):
            name = clean_str(org.get("name"))
        elif isinstance(org, str):
            name = clean_str(org)
        else:
            name = None
        if name:
            out.append(Education(school_name=name))
    return out


def _meta(tree: HTMLParser, prop: str) -> str | None:
    node = tree.css_first(f'meta[property="{prop}"]') or tree.css_first(
        f'meta[name="{prop}"]'
    )
    if node is None:
        return None
    return clean_str(node.attributes.get("content"))


def parse_public_html(
    html: str,
    *,
    public_id: str,
    profile_url: str,
    warnings: list[str],
) -> LinkedInProfile:
    """Best-effort profile from the rendered page."""
    tree = HTMLParser(html)
    person = _find_person(_iter_jsonld(tree))

    if person is None:
        warnings.append(
            "No schema.org/Person block found; fell back to Open Graph meta tags."
        )
        og_title = _meta(tree, "og:title")
        og_desc = _meta(tree, "og:description")
        og_image = _meta(tree, "og:image")

        # og:title is typically "Jane Doe - Staff Engineer - Acme | LinkedIn".
        name = None
        headline = None
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

    name = clean_str(person.get("name"))
    given = clean_str(person.get("givenName"))
    family = clean_str(person.get("familyName"))

    if name and not (given or family):
        given, _, family = name.partition(" ")
        given, family = clean_str(given), clean_str(family)

    positions = _positions_from_jsonld(person)

    warnings.append(
        "Extracted from the public page rather than the Voyager API; "
        "skills, certifications, languages and dates are unavailable."
    )

    return LinkedInProfile(
        public_id=public_id,
        profile_url=profile_url,
        first_name=given,
        last_name=family,
        full_name=name or " ".join(p for p in (given, family) if p) or None,
        headline=clean_str(person.get("jobTitle"))
        if isinstance(person.get("jobTitle"), str)
        else clean_str(person.get("description")),
        about=clean_str(person.get("description")),
        location=_location_from_jsonld(person),
        profile_picture=_image_from_jsonld(person),
        experience=positions,
        education=_education_from_jsonld(person),
        current_position=positions[0] if positions else None,
    )
