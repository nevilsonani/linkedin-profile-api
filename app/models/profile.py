"""Public response schema for a scraped LinkedIn profile.

Design notes
------------
* Every field is optional. LinkedIn profiles are wildly inconsistent — a field
  that exists on one profile is simply absent on the next, and privacy settings
  can hide anything. Callers should never have to guard against a missing key,
  only against a ``null`` value.
* Dates are modelled as a ``DateParts`` object rather than an ISO string because
  LinkedIn genuinely stores partial dates (year-only, or month+year). Emitting
  ``"2021-01-01"`` for what the user entered as "2021" would be inventing data.
  ``DateParts.text`` gives a ready-to-display rendering.
* Raw LinkedIn URNs are preserved in ``*_urn`` fields so consumers can correlate
  records across calls without re-deriving identity from display names.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


class _Base(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class DateParts(_Base):
    """A possibly-partial calendar date, exactly as LinkedIn stores it."""

    year: int | None = Field(None, examples=[2021])
    month: int | None = Field(None, ge=1, le=12, examples=[6])
    day: int | None = Field(None, ge=1, le=31)
    text: str | None = Field(
        None,
        description="Human-readable rendering, e.g. 'Jun 2021' or '2021'.",
        examples=["Jun 2021"],
    )


class DateRange(_Base):
    """Start/end of an experience, education, or certification entry."""

    start: DateParts | None = None
    end: DateParts | None = Field(
        None, description="Null when the entry is ongoing (see `is_current`)."
    )
    is_current: bool = Field(
        False, description="True when there is no end date, i.e. present role."
    )
    duration_months: int | None = Field(
        None,
        description="Whole months between start and end (or now). Null if start is unknown.",
        examples=[26],
    )
    text: str | None = Field(
        None,
        description="Rendered range, e.g. 'Jun 2021 - Present'.",
        examples=["Jun 2021 - Present"],
    )


class Image(_Base):
    """A LinkedIn-hosted image, resolved to fully-qualified CDN URLs.

    LinkedIn serves the same asset at several resolutions. ``url`` is the
    largest available; ``variants`` lists every size it advertised.
    """

    url: str | None = Field(None, description="Highest-resolution variant.")
    width: int | None = None
    height: int | None = None
    variants: list["ImageVariant"] = Field(default_factory=list)
    expires_at: datetime | None = Field(
        None,
        description=(
            "LinkedIn CDN URLs are signed and expire. Re-fetch the profile "
            "after this timestamp to obtain fresh links."
        ),
    )


class ImageVariant(_Base):
    url: str
    width: int | None = None
    height: int | None = None


class Company(_Base):
    """The organisation attached to a position, certification, or volunteering entry."""

    name: str | None = None
    urn: str | None = Field(None, description="e.g. 'urn:li:fs_miniCompany:1441'")
    linkedin_url: str | None = None
    universal_name: str | None = Field(
        None, description="Company vanity slug, e.g. 'google'."
    )
    logo: Image | None = None
    industries: list[str] = Field(default_factory=list)
    employee_count_range: str | None = Field(None, examples=["10001+"])
    staffing_company: bool | None = None


class School(_Base):
    name: str | None = None
    urn: str | None = None
    linkedin_url: str | None = None
    logo: Image | None = None


# ---------------------------------------------------------------------------
# Profile sections
# ---------------------------------------------------------------------------


class Location(_Base):
    text: str | None = Field(
        None,
        description="Best available display location.",
        examples=["San Francisco Bay Area"],
    )
    city: str | None = None
    country: str | None = None
    country_code: str | None = Field(None, examples=["us"])
    postal_code: str | None = None


class Position(_Base):
    """One row under 'Experience'."""

    title: str | None = None
    company: Company | None = None
    company_name: str | None = Field(
        None, description="Convenience copy of `company.name`."
    )
    employment_type: str | None = Field(None, examples=["Full-time"])
    location: str | None = None
    description: str | None = None
    date_range: DateRange | None = None
    urn: str | None = None
    skills: list[str] = Field(
        default_factory=list, description="Skills LinkedIn associates with this role."
    )


class PositionGroup(_Base):
    """Consecutive roles at the same company, as LinkedIn groups them visually."""

    company_name: str | None = None
    company: Company | None = None
    date_range: DateRange | None = None
    positions: list[Position] = Field(default_factory=list)


class Education(_Base):
    school_name: str | None = None
    school: School | None = None
    degree_name: str | None = Field(None, examples=["Bachelor of Science - BS"])
    field_of_study: str | None = Field(None, examples=["Computer Science"])
    grade: str | None = None
    activities: str | None = None
    description: str | None = None
    date_range: DateRange | None = None
    urn: str | None = None


class Skill(_Base):
    name: str
    endorsement_count: int | None = None


class Certification(_Base):
    name: str | None = None
    authority: str | None = Field(
        None, description="Issuing organisation.", examples=["Amazon Web Services"]
    )
    company: Company | None = None
    license_number: str | None = None
    url: str | None = Field(None, description="Credential verification URL.")
    date_range: DateRange | None = Field(
        None, description="`start` is the issue date, `end` the expiry date."
    )
    urn: str | None = None


class Language(_Base):
    name: str
    proficiency: str | None = Field(
        None,
        description="Normalised label, e.g. 'Native or bilingual proficiency'.",
        examples=["Professional working proficiency"],
    )
    proficiency_code: str | None = Field(
        None, description="Raw LinkedIn enum.", examples=["PROFESSIONAL_WORKING"]
    )


class Project(_Base):
    title: str | None = None
    description: str | None = None
    url: str | None = None
    date_range: DateRange | None = None
    members: list[str] = Field(default_factory=list)


class Publication(_Base):
    name: str | None = None
    publisher: str | None = None
    description: str | None = None
    url: str | None = None
    date: DateParts | None = None
    authors: list[str] = Field(default_factory=list)


class Honor(_Base):
    title: str | None = None
    issuer: str | None = None
    description: str | None = None
    date: DateParts | None = None


class VolunteerExperience(_Base):
    role: str | None = None
    organization: str | None = None
    company: Company | None = None
    cause: str | None = None
    description: str | None = None
    date_range: DateRange | None = None


class Course(_Base):
    name: str | None = None
    number: str | None = None


class Patent(_Base):
    title: str | None = None
    number: str | None = None
    description: str | None = None
    url: str | None = None
    issuer: str | None = None
    date: DateParts | None = None
    pending: bool | None = None
    inventors: list[str] = Field(default_factory=list)


class TestScore(_Base):
    name: str | None = None
    score: str | None = None
    description: str | None = None
    date: DateParts | None = None


class Organization(_Base):
    name: str | None = None
    position: str | None = None
    description: str | None = None
    date_range: DateRange | None = None


class ContactInfo(_Base):
    """Only populated for profiles that expose contact details to your account."""

    email: str | None = None
    phone_numbers: list["PhoneNumber"] = Field(default_factory=list)
    twitter_handles: list[str] = Field(default_factory=list)
    websites: list["Website"] = Field(default_factory=list)
    address: str | None = None
    birthday: DateParts | None = None
    instant_messengers: list["InstantMessenger"] = Field(default_factory=list)


class PhoneNumber(_Base):
    number: str
    type: str | None = Field(None, examples=["MOBILE"])


class Website(_Base):
    url: str
    category: str | None = Field(None, examples=["PERSONAL", "COMPANY", "BLOG"])
    label: str | None = None


class InstantMessenger(_Base):
    provider: str
    username: str


class NetworkInfo(_Base):
    followers_count: int | None = None
    connections_count: int | None = Field(
        None, description="LinkedIn caps the public value at 500."
    )
    connection_distance: str | None = Field(
        None,
        description="Degree of separation from the authenticated account.",
        examples=["DISTANCE_2"],
    )


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------


class LinkedInProfile(_Base):
    """The full structured profile."""

    # --- Identity ---------------------------------------------------------
    public_id: str = Field(
        description="Vanity slug from the URL.", examples=["williamhgates"]
    )
    profile_url: str = Field(examples=["https://www.linkedin.com/in/williamhgates"])
    urn: str | None = Field(
        None, description="Stable member URN.", examples=["urn:li:fs_profile:ACoAAA..."]
    )
    member_id: str | None = Field(
        None, description="Obfuscated member id extracted from the URN."
    )

    # --- Headline block ---------------------------------------------------
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    maiden_name: str | None = None
    headline: str | None = Field(
        None, examples=["Co-chair, Bill & Melinda Gates Foundation"]
    )
    about: str | None = Field(
        None, description="The 'About' / summary section, newlines preserved."
    )
    industry: str | None = None
    location: Location | None = None
    is_student: bool | None = None
    is_influencer: bool | None = None
    is_premium: bool | None = None
    is_open_to_work: bool | None = None
    is_hiring: bool | None = None

    # --- Media ------------------------------------------------------------
    profile_picture: Image | None = None
    background_image: Image | None = Field(
        None, description="Cover/banner photo behind the profile picture."
    )

    # --- Sections ---------------------------------------------------------
    experience: list[Position] = Field(default_factory=list)
    experience_grouped: list[PositionGroup] = Field(
        default_factory=list,
        description="Same roles, grouped by company the way the UI renders them.",
    )
    education: list[Education] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    publications: list[Publication] = Field(default_factory=list)
    honors: list[Honor] = Field(default_factory=list)
    volunteer_experience: list[VolunteerExperience] = Field(default_factory=list)
    courses: list[Course] = Field(default_factory=list)
    patents: list[Patent] = Field(default_factory=list)
    test_scores: list[TestScore] = Field(default_factory=list)
    organizations: list[Organization] = Field(default_factory=list)

    # --- Extras -----------------------------------------------------------
    contact_info: ContactInfo | None = None
    network_info: NetworkInfo | None = None

    # --- Derived ----------------------------------------------------------
    current_position: Position | None = Field(
        None, description="Most recent role with no end date, if any."
    )
    total_experience_months: int | None = Field(
        None,
        description="Union of all position date ranges, so overlaps aren't double-counted.",
    )


class ScrapeMeta(_Base):
    """Provenance for a single scrape, useful when debugging partial results."""

    source: Literal["voyager", "public_html", "cache"] = Field(
        description="Which extraction strategy produced this payload."
    )
    fetched_at: datetime
    duration_ms: int
    cached: bool = False
    endpoints_succeeded: list[str] = Field(default_factory=list)
    endpoints_failed: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal issues, e.g. a section that failed to parse.",
    )


class ProfileResponse(_Base):
    """Envelope returned by ``GET/POST /api/v1/profile``."""

    success: Literal[True] = True
    data: LinkedInProfile
    meta: ScrapeMeta


class ErrorDetail(_Base):
    code: str = Field(examples=["PROFILE_NOT_FOUND"])
    message: str
    hint: str | None = Field(
        None, description="Actionable next step, when one exists."
    )


class ErrorResponse(_Base):
    success: Literal[False] = False
    error: ErrorDetail
    request_id: str | None = None


class RawResponse(_Base):
    """Envelope for the debug endpoint that returns unparsed Voyager payloads."""

    success: Literal[True] = True
    public_id: str
    endpoints: dict[str, Any]


# Resolve forward references declared as strings above.
Image.model_rebuild()
ContactInfo.model_rebuild()
