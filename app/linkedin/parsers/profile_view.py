"""Turn a Voyager ``profileView`` document into a :class:`LinkedInProfile`.

The document is a bag of "views", each shaped as ``{"elements": [...]}``::

    {
      "profile":                 { ...header block... },
      "positionView":            {"elements": [...]},
      "positionGroupView":       {"elements": [...]},
      "educationView":           {"elements": [...]},
      "skillView":               {"elements": [...]},
      "certificationView":       {"elements": [...]},
      "languageView":            {"elements": [...]},
      "projectView":             {"elements": [...]},
      "publicationView":         {"elements": [...]},
      "honorView":               {"elements": [...]},
      "volunteerExperienceView": {"elements": [...]},
      "courseView":              {"elements": [...]},
      "patentView":              {"elements": [...]},
      "testScoreView":           {"elements": [...]},
      "organizationView":        {"elements": [...]}
    }

Every section is parsed defensively and independently: a schema change in one
view degrades that section to empty and records a warning, rather than failing
the whole request.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from app.linkedin.parsers.common import (
    PROFICIENCY_LABELS,
    clean_str,
    first_of,
    member_id_from_urn,
    month_span,
    parse_company,
    parse_date,
    parse_date_range,
    parse_image,
    parse_school,
    union_months,
)
from app.models.profile import (
    Certification,
    Course,
    Education,
    Honor,
    Language,
    LinkedInProfile,
    Location,
    Organization,
    Patent,
    Position,
    PositionGroup,
    Project,
    Publication,
    Skill,
    TestScore,
    VolunteerExperience,
)
from app.utils.logging import get_logger

log = get_logger(__name__)

T = TypeVar("T")


def _elements(doc: dict[str, Any], view: str) -> list[dict[str, Any]]:
    """Pull ``doc[view]["elements"]``, tolerating absence and wrong types."""
    node = doc.get(view)
    if not isinstance(node, dict):
        return []
    elements = node.get("elements")
    if not isinstance(elements, list):
        return []
    return [e for e in elements if isinstance(e, dict)]


def _safe_section(
    name: str,
    parser: Callable[[], list[T]],
    warnings: list[str],
) -> list[T]:
    """Run a section parser, downgrading any failure to a warning."""
    try:
        return parser()
    except Exception as exc:  # noqa: BLE001 - deliberate section-level isolation
        log.warning("section_parse_failed", section=name, error=str(exc))
        warnings.append(f"Failed to parse '{name}': {exc.__class__.__name__}")
        return []


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------


def _parse_location(profile: dict[str, Any]) -> Location | None:
    location_node = profile.get("location")
    location_node = location_node if isinstance(location_node, dict) else {}

    text = (
        clean_str(profile.get("geoLocationName"))
        or clean_str(profile.get("locationName"))
        or clean_str(location_node.get("basicLocation", {}).get("postalCode")
                     if isinstance(location_node.get("basicLocation"), dict) else None)
    )
    country_code = None
    postal = None
    basic = location_node.get("basicLocation")
    if isinstance(basic, dict):
        country_code = clean_str(basic.get("countryCode"))
        postal = clean_str(basic.get("postalCode"))

    country = clean_str(profile.get("geoCountryName"))

    # geoLocationName is usually "City, State, Country" — take the leading city.
    city = None
    if text and "," in text:
        city = text.split(",", 1)[0].strip() or None
    elif text and not country:
        city = text

    if not any([text, city, country, country_code, postal]):
        return None

    return Location(
        text=text,
        city=city,
        country=country,
        country_code=country_code.lower() if country_code else None,
        postal_code=postal,
    )


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _parse_position(el: dict[str, Any]) -> Position:
    company_name = clean_str(el.get("companyName"))
    company = parse_company(el.get("company"), fallback_name=company_name)

    skills = [
        s
        for s in (
            clean_str(item.get("name") if isinstance(item, dict) else item)
            for item in el.get("profileTreasuryMediaPosition", []) or []
        )
        if s
    ]

    return Position(
        title=clean_str(el.get("title")),
        company=company,
        company_name=company_name or (company.name if company else None),
        employment_type=clean_str(el.get("employmentType")),
        location=clean_str(first_of(el, "geoLocationName", "locationName", "region")),
        description=clean_str(el.get("description")),
        date_range=parse_date_range(el.get("timePeriod")),
        urn=clean_str(el.get("entityUrn")),
        skills=skills,
    )


def _parse_positions(doc: dict[str, Any]) -> list[Position]:
    return [_parse_position(el) for el in _elements(doc, "positionView")]


def _parse_position_groups(doc: dict[str, Any]) -> list[PositionGroup]:
    groups: list[PositionGroup] = []
    for el in _elements(doc, "positionGroupView"):
        name = clean_str(el.get("name")) or clean_str(el.get("companyName"))
        nested = el.get("positions")
        positions = (
            [_parse_position(p) for p in nested if isinstance(p, dict)]
            if isinstance(nested, list)
            else []
        )
        groups.append(
            PositionGroup(
                company_name=name,
                company=parse_company(el.get("company"), fallback_name=name),
                date_range=parse_date_range(el.get("timePeriod")),
                positions=positions,
            )
        )
    return groups


def _parse_education(doc: dict[str, Any]) -> list[Education]:
    out: list[Education] = []
    for el in _elements(doc, "educationView"):
        school_name = clean_str(el.get("schoolName"))
        out.append(
            Education(
                school_name=school_name,
                school=parse_school(el.get("school"), fallback_name=school_name),
                degree_name=clean_str(el.get("degreeName")),
                field_of_study=clean_str(el.get("fieldOfStudy")),
                grade=clean_str(el.get("grade")),
                activities=clean_str(el.get("activities")),
                description=clean_str(el.get("description")),
                date_range=parse_date_range(el.get("timePeriod")),
                urn=clean_str(el.get("entityUrn")),
            )
        )
    return out


def _parse_skills(doc: dict[str, Any]) -> list[Skill]:
    out: list[Skill] = []
    seen: set[str] = set()
    for el in _elements(doc, "skillView"):
        name = clean_str(el.get("name"))
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        endorsements = el.get("endorsementCount")
        out.append(
            Skill(
                name=name,
                endorsement_count=endorsements if isinstance(endorsements, int) else None,
            )
        )
    return out


def _parse_certifications(doc: dict[str, Any]) -> list[Certification]:
    out: list[Certification] = []
    for el in _elements(doc, "certificationView"):
        authority = clean_str(el.get("authority"))
        out.append(
            Certification(
                name=clean_str(el.get("name")),
                authority=authority,
                company=parse_company(el.get("company"), fallback_name=authority),
                license_number=clean_str(el.get("licenseNumber")),
                url=clean_str(el.get("url")),
                date_range=parse_date_range(el.get("timePeriod")),
                urn=clean_str(el.get("entityUrn")),
            )
        )
    return out


def _parse_languages(doc: dict[str, Any]) -> list[Language]:
    out: list[Language] = []
    for el in _elements(doc, "languageView"):
        name = clean_str(el.get("name"))
        if not name:
            continue
        code = clean_str(el.get("proficiency"))
        out.append(
            Language(
                name=name,
                proficiency=PROFICIENCY_LABELS.get(code or "", None) or _titleize(code),
                proficiency_code=code,
            )
        )
    return out


def _titleize(code: str | None) -> str | None:
    if not code:
        return None
    return code.replace("_", " ").title()


def _names(raw: Any) -> list[str]:
    """Extract display names from a list of member/contributor references."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            name = clean_str(item)
        elif isinstance(item, dict):
            name = clean_str(item.get("name"))
            if not name:
                first = clean_str(item.get("firstName"))
                last = clean_str(item.get("lastName"))
                name = " ".join(p for p in (first, last) if p) or None
            if not name:
                member = item.get("member")
                if isinstance(member, dict):
                    name = " ".join(
                        p
                        for p in (
                            clean_str(member.get("firstName")),
                            clean_str(member.get("lastName")),
                        )
                        if p
                    ) or None
        else:
            name = None
        if name:
            out.append(name)
    return out


def _parse_projects(doc: dict[str, Any]) -> list[Project]:
    return [
        Project(
            title=clean_str(el.get("title")),
            description=clean_str(el.get("description")),
            url=clean_str(el.get("url")),
            date_range=parse_date_range(el.get("timePeriod")),
            members=_names(el.get("members")),
        )
        for el in _elements(doc, "projectView")
    ]


def _parse_publications(doc: dict[str, Any]) -> list[Publication]:
    return [
        Publication(
            name=clean_str(el.get("name")),
            publisher=clean_str(el.get("publisher")),
            description=clean_str(el.get("description")),
            url=clean_str(el.get("url")),
            date=parse_date(el.get("date")),
            authors=_names(el.get("authors")),
        )
        for el in _elements(doc, "publicationView")
    ]


def _parse_honors(doc: dict[str, Any]) -> list[Honor]:
    return [
        Honor(
            title=clean_str(el.get("title")),
            issuer=clean_str(el.get("issuer")),
            description=clean_str(el.get("description")),
            date=parse_date(el.get("issueDate")),
        )
        for el in _elements(doc, "honorView")
    ]


def _parse_volunteering(doc: dict[str, Any]) -> list[VolunteerExperience]:
    out: list[VolunteerExperience] = []
    for el in _elements(doc, "volunteerExperienceView"):
        org = clean_str(el.get("companyName"))
        out.append(
            VolunteerExperience(
                role=clean_str(el.get("role")),
                organization=org,
                company=parse_company(el.get("company"), fallback_name=org),
                cause=_titleize(clean_str(el.get("cause"))),
                description=clean_str(el.get("description")),
                date_range=parse_date_range(el.get("timePeriod")),
            )
        )
    return out


def _parse_courses(doc: dict[str, Any]) -> list[Course]:
    return [
        Course(name=clean_str(el.get("name")), number=clean_str(el.get("number")))
        for el in _elements(doc, "courseView")
    ]


def _parse_patents(doc: dict[str, Any]) -> list[Patent]:
    out: list[Patent] = []
    for el in _elements(doc, "patentView"):
        pending = el.get("pending")
        out.append(
            Patent(
                title=clean_str(el.get("title")),
                number=clean_str(el.get("number")),
                description=clean_str(el.get("description")),
                url=clean_str(el.get("url")),
                issuer=clean_str(el.get("issuer")),
                date=parse_date(first_of(el, "issueDate", "filingDate")),
                pending=pending if isinstance(pending, bool) else None,
                inventors=_names(el.get("inventors")),
            )
        )
    return out


def _parse_test_scores(doc: dict[str, Any]) -> list[TestScore]:
    return [
        TestScore(
            name=clean_str(el.get("name")),
            score=clean_str(el.get("score")),
            description=clean_str(el.get("description")),
            date=parse_date(el.get("date")),
        )
        for el in _elements(doc, "testScoreView")
    ]


def _parse_organizations(doc: dict[str, Any]) -> list[Organization]:
    return [
        Organization(
            name=clean_str(el.get("name")),
            position=clean_str(el.get("position")),
            description=clean_str(el.get("description")),
            date_range=parse_date_range(el.get("timePeriod")),
        )
        for el in _elements(doc, "organizationView")
    ]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_profile_view(
    doc: dict[str, Any],
    *,
    public_id: str,
    profile_url: str,
    warnings: list[str],
) -> LinkedInProfile:
    """Build the profile object. Never raises for a single bad section."""
    profile = doc.get("profile")
    profile = profile if isinstance(profile, dict) else {}

    mini = profile.get("miniProfile")
    mini = mini if isinstance(mini, dict) else {}

    first = clean_str(profile.get("firstName")) or clean_str(mini.get("firstName"))
    last = clean_str(profile.get("lastName")) or clean_str(mini.get("lastName"))
    full = " ".join(p for p in (first, last) if p) or None

    urn = clean_str(profile.get("entityUrn")) or clean_str(mini.get("entityUrn"))

    # The picture hangs off miniProfile on most payloads, off profile on some.
    picture = parse_image(
        first_of(mini, "picture", "profilePicture")
        or first_of(profile, "picture", "profilePicture")
    )
    background = parse_image(
        first_of(mini, "backgroundImage")
        or first_of(profile, "backgroundImage", "backgroundPicture")
    )

    experience = _safe_section("experience", lambda: _parse_positions(doc), warnings)
    grouped = _safe_section(
        "experience_grouped", lambda: _parse_position_groups(doc), warnings
    )

    # If positionView came back empty but the grouped view didn't, flatten it.
    if not experience and grouped:
        experience = [p for g in grouped for p in g.positions]

    current = next(
        (p for p in experience if p.date_range and p.date_range.is_current), None
    )

    spans = [s for s in (month_span(p.date_range) for p in experience) if s]
    total_months = union_months(spans) if spans else None

    resolved_public_id = clean_str(profile.get("publicIdentifier")) or clean_str(
        mini.get("publicIdentifier")
    ) or public_id

    return LinkedInProfile(
        public_id=resolved_public_id,
        profile_url=profile_url,
        urn=urn,
        member_id=member_id_from_urn(urn),
        first_name=first,
        last_name=last,
        full_name=full,
        maiden_name=clean_str(profile.get("maidenName")),
        headline=clean_str(profile.get("headline")) or clean_str(mini.get("occupation")),
        about=clean_str(profile.get("summary")),
        industry=clean_str(profile.get("industryName")),
        location=_parse_location(profile),
        is_student=profile.get("student") if isinstance(profile.get("student"), bool) else None,
        is_influencer=(
            mini.get("influencer") if isinstance(mini.get("influencer"), bool) else None
        ),
        profile_picture=picture,
        background_image=background,
        experience=experience,
        experience_grouped=grouped,
        education=_safe_section("education", lambda: _parse_education(doc), warnings),
        skills=_safe_section("skills", lambda: _parse_skills(doc), warnings),
        certifications=_safe_section(
            "certifications", lambda: _parse_certifications(doc), warnings
        ),
        languages=_safe_section("languages", lambda: _parse_languages(doc), warnings),
        projects=_safe_section("projects", lambda: _parse_projects(doc), warnings),
        publications=_safe_section(
            "publications", lambda: _parse_publications(doc), warnings
        ),
        honors=_safe_section("honors", lambda: _parse_honors(doc), warnings),
        volunteer_experience=_safe_section(
            "volunteer_experience", lambda: _parse_volunteering(doc), warnings
        ),
        courses=_safe_section("courses", lambda: _parse_courses(doc), warnings),
        patents=_safe_section("patents", lambda: _parse_patents(doc), warnings),
        test_scores=_safe_section("test_scores", lambda: _parse_test_scores(doc), warnings),
        organizations=_safe_section(
            "organizations", lambda: _parse_organizations(doc), warnings
        ),
        current_position=current,
        total_experience_months=total_months,
    )
