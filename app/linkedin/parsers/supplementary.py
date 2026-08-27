"""Parsers for the endpoints that top up ``profileView``.

``profileView`` is comprehensive but not complete:

* contact details live behind ``/profileContactInfo``;
* follower/connection counts behind ``/networkinfo``;
* the skill list in profileView is truncated (LinkedIn returns the "top" few),
  so ``/skills`` is used to get the full set;
* premium / influencer / open-to-work badges only exist on the newer *dash*
  model, which is returned in normalised form.
"""

from __future__ import annotations

from typing import Any

from app.linkedin.parsers.common import (
    clean_str,
    parse_date,
    parse_image,
    unwrap_union,
)
from app.models.profile import (
    ContactInfo,
    Image,
    InstantMessenger,
    NetworkInfo,
    PhoneNumber,
    Skill,
    Website,
)

# LinkedIn's website-category enum -> friendly label.
_WEBSITE_CATEGORIES = {
    "PERSONAL": "Personal",
    "COMPANY": "Company",
    "BLOG": "Blog",
    "RSS": "RSS Feed",
    "PORTFOLIO": "Portfolio",
    "OTHER": "Other",
}


def parse_contact_info(doc: dict[str, Any]) -> ContactInfo | None:
    """Parse ``/identity/profiles/{id}/profileContactInfo``."""
    if not isinstance(doc, dict):
        return None

    email = clean_str(doc.get("emailAddress"))

    phones: list[PhoneNumber] = []
    for raw in doc.get("phoneNumbers") or []:
        if not isinstance(raw, dict):
            continue
        number = clean_str(raw.get("number"))
        if number:
            phones.append(PhoneNumber(number=number, type=clean_str(raw.get("type"))))

    twitter = [
        handle
        for handle in (
            clean_str(t.get("name")) if isinstance(t, dict) else clean_str(t)
            for t in doc.get("twitterHandles") or []
        )
        if handle
    ]

    websites: list[Website] = []
    for raw in doc.get("websites") or []:
        if not isinstance(raw, dict):
            continue
        url = clean_str(raw.get("url"))
        if not url:
            continue
        # `type` is a Rest.li union: StandardWebsite carries an enum category,
        # CustomWebsite carries a free-text label.
        category = None
        label = None
        type_node = unwrap_union(raw.get("type"))
        if isinstance(type_node, dict):
            raw_cat = clean_str(type_node.get("category"))
            category = _WEBSITE_CATEGORIES.get(raw_cat or "", raw_cat)
            label = clean_str(type_node.get("label"))
        websites.append(Website(url=url, category=category, label=label))

    ims: list[InstantMessenger] = []
    for raw in doc.get("ims") or []:
        if not isinstance(raw, dict):
            continue
        provider = clean_str(raw.get("provider"))
        username = clean_str(raw.get("id")) or clean_str(raw.get("username"))
        if provider and username:
            ims.append(InstantMessenger(provider=provider, username=username))

    birthday = parse_date(doc.get("birthDateOn"))
    address = clean_str(doc.get("address"))

    if not any([email, phones, twitter, websites, ims, birthday, address]):
        return None

    return ContactInfo(
        email=email,
        phone_numbers=phones,
        twitter_handles=twitter,
        websites=websites,
        address=address,
        birthday=birthday,
        instant_messengers=ims,
    )


def parse_network_info(doc: dict[str, Any]) -> NetworkInfo | None:
    """Parse ``/identity/profiles/{id}/networkinfo``."""
    if not isinstance(doc, dict):
        return None

    followers = doc.get("followersCount")
    connections = doc.get("connectionsCount")

    distance = None
    distance_node = doc.get("distance")
    if isinstance(distance_node, dict):
        distance = clean_str(distance_node.get("value"))
    elif isinstance(distance_node, str):
        distance = clean_str(distance_node)

    followers = followers if isinstance(followers, int) else None
    connections = connections if isinstance(connections, int) else None

    if followers is None and connections is None and distance is None:
        return None

    return NetworkInfo(
        followers_count=followers,
        connections_count=connections,
        connection_distance=distance,
    )


def parse_skills_endpoint(doc: dict[str, Any]) -> list[Skill]:
    """Parse ``/identity/profiles/{id}/skills`` (paginated full list)."""
    if not isinstance(doc, dict):
        return []
    elements = doc.get("elements")
    if not isinstance(elements, list):
        return []

    out: list[Skill] = []
    seen: set[str] = set()
    for el in elements:
        if not isinstance(el, dict):
            continue
        name = clean_str(el.get("name"))
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        count = el.get("endorsementCount")
        out.append(
            Skill(name=name, endorsement_count=count if isinstance(count, int) else None)
        )
    return out


# ---------------------------------------------------------------------------
# Dash (normalised) profile
# ---------------------------------------------------------------------------


_DASH_PROFILE_TYPE = "com.linkedin.voyager.dash.identity.profile.Profile"

# Badge rendered over the avatar; the enum is what tells us open-to-work/hiring.
_FRAME_OPEN_TO_WORK = "OPEN_TO_WORK"
_FRAME_HIRING = "HIRING"


def parse_dash_flags(doc: dict[str, Any]) -> dict[str, Any]:
    """Extract badge flags and images from the normalised *dash* payload.

    The normalised encoding puts every entity in a flat ``included[]`` array
    tagged with ``$type``; only the single ``Profile`` entity interests us.

    Returns a sparse dict of :class:`LinkedInProfile` field names to values.
    Absent keys mean "dash didn't tell us", which is deliberately distinct from
    ``False`` — the caller only overlays keys that are present.
    """
    result: dict[str, Any] = {}
    if not isinstance(doc, dict):
        return result

    included = doc.get("included")
    if not isinstance(included, list):
        return result

    profile_node: dict[str, Any] | None = None
    for entity in included:
        if isinstance(entity, dict) and entity.get("$type") == _DASH_PROFILE_TYPE:
            profile_node = entity
            break

    if profile_node is None:
        return result

    # --- boolean badges ---------------------------------------------------
    for source_key, field in (
        ("premium", "is_premium"),
        ("influencer", "is_influencer"),
        ("student", "is_student"),
    ):
        value = profile_node.get(source_key)
        if isinstance(value, bool):
            result[field] = value

    # --- open-to-work / hiring frame --------------------------------------
    frame = _find_frame_type(profile_node) or _find_frame_type_in_included(included)
    if frame == _FRAME_OPEN_TO_WORK:
        result["is_open_to_work"] = True
    elif frame == _FRAME_HIRING:
        result["is_hiring"] = True

    # --- text fields dash sometimes has and profileView doesn't -----------
    for source_key, field in (
        ("headline", "headline"),
        ("summary", "about"),
        ("publicIdentifier", "public_id"),
    ):
        value = clean_str(profile_node.get(source_key))
        if value:
            result[field] = value

    # --- images -----------------------------------------------------------
    picture = _dash_image(profile_node.get("profilePicture"))
    if picture is not None:
        result["profile_picture"] = picture

    background = _dash_image(profile_node.get("backgroundPicture"))
    if background is not None:
        result["background_image"] = background

    return result


def _find_frame_type(profile_node: dict[str, Any]) -> str | None:
    picture = profile_node.get("profilePicture")
    if isinstance(picture, dict):
        return clean_str(picture.get("frameType"))
    return None


def _find_frame_type_in_included(included: list[Any]) -> str | None:
    """Fallback: the frame may be a separate normalised entity."""
    for entity in included:
        if not isinstance(entity, dict):
            continue
        type_ = entity.get("$type") or ""
        if "ProfilePicture" in type_ or "profile.Profile" in type_:
            frame = clean_str(entity.get("frameType"))
            if frame:
                return frame
    return None


def _dash_image(raw: Any) -> Image | None:
    """Dash wraps images one level deeper than the legacy model."""
    if not isinstance(raw, dict):
        return None
    # profilePicture -> displayImageReference -> vectorImage -> {rootUrl, artifacts}
    for key in ("displayImageReference", "originalImageReference", "vectorImage"):
        node = raw.get(key)
        if node is not None:
            image = parse_image(node)
            if image is not None:
                return image
    return parse_image(raw)
