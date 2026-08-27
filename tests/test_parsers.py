"""Parser tests against realistic Voyager payload shapes."""

from __future__ import annotations

from app.linkedin.parsers.common import (
    parse_date,
    parse_date_range,
    parse_image,
    union_months,
)
from app.linkedin.parsers.profile_view import parse_profile_view
from app.linkedin.parsers.public_html import parse_public_html
from app.linkedin.parsers.supplementary import (
    parse_contact_info,
    parse_dash_flags,
    parse_network_info,
    parse_skills_endpoint,
)
from tests import fixtures


def _parse() -> tuple:
    warnings: list[str] = []
    profile = parse_profile_view(
        fixtures.PROFILE_VIEW,
        public_id="adalovelace",
        profile_url="https://www.linkedin.com/in/adalovelace",
        warnings=warnings,
    )
    return profile, warnings


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------


def test_header_fields() -> None:
    profile, warnings = _parse()
    assert warnings == []
    assert profile.first_name == "Ada"
    assert profile.last_name == "Lovelace"
    assert profile.full_name == "Ada Lovelace"
    assert profile.headline == "Mathematician | First Computer Programmer"
    assert profile.about is not None and "Analytical Engine" in profile.about
    assert profile.industry == "Computer Software"
    assert profile.public_id == "adalovelace"
    assert profile.urn == "urn:li:fs_profile:ACoAAABCDEFGHIJKLMNOP"
    assert profile.member_id == "ACoAAABCDEFGHIJKLMNOP"
    assert profile.is_student is False
    assert profile.is_influencer is True


def test_about_preserves_newlines() -> None:
    profile, _ = _parse()
    assert "\n\n" in (profile.about or "")


def test_location() -> None:
    profile, _ = _parse()
    assert profile.location is not None
    assert profile.location.text == "London, England, United Kingdom"
    assert profile.location.city == "London"
    assert profile.location.country == "United Kingdom"
    assert profile.location.country_code == "gb"


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


def test_profile_picture_picks_largest_variant() -> None:
    profile, _ = _parse()
    pic = profile.profile_picture
    assert pic is not None
    assert pic.width == 400 and pic.height == 400
    assert pic.url is not None and pic.url.startswith(fixtures.PICTURE_ROOT)
    assert len(pic.variants) == 3
    # Variants must be ordered largest-first.
    assert [v.width for v in pic.variants] == [400, 200, 100]


def test_image_url_concatenates_root_and_segment() -> None:
    """Neither half of a VectorImage is usable alone; the join must be exact."""
    image = parse_image(
        {
            "com.linkedin.common.VectorImage": {
                "rootUrl": "https://cdn.example/root_",
                "artifacts": [
                    {
                        "width": 200,
                        "height": 200,
                        "fileIdentifyingUrlPathSegment": "200_200/abc?e=1&t=z",
                    }
                ],
            }
        }
    )
    assert image is not None
    assert image.url == "https://cdn.example/root_200_200/abc?e=1&t=z"


def test_background_image_present() -> None:
    profile, _ = _parse()
    assert profile.background_image is not None
    assert profile.background_image.url is not None


def test_image_of_garbage_is_none() -> None:
    assert parse_image(None) is None
    assert parse_image({}) is None
    assert parse_image({"com.linkedin.common.VectorImage": {"artifacts": []}}) is None


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


def test_partial_dates_are_not_invented() -> None:
    """A year-only date must stay year-only, not become 1 January."""
    parts = parse_date({"year": 2021})
    assert parts is not None
    assert parts.year == 2021
    assert parts.month is None
    assert parts.text == "2021"


def test_month_year_rendering() -> None:
    parts = parse_date({"month": 6, "year": 2021})
    assert parts is not None and parts.text == "Jun 2021"


def test_closed_range() -> None:
    rng = parse_date_range(
        {"startDate": {"month": 1, "year": 2020}, "endDate": {"month": 12, "year": 2020}}
    )
    assert rng is not None
    assert rng.is_current is False
    assert rng.duration_months == 12
    assert rng.text == "Jan 2020 - Dec 2020"


def test_open_range_is_current() -> None:
    rng = parse_date_range({"startDate": {"month": 6, "year": 2020}})
    assert rng is not None
    assert rng.is_current is True
    assert rng.end is None
    assert rng.text == "Jun 2020 - Present"
    assert (rng.duration_months or 0) > 0


def test_empty_time_period_is_none() -> None:
    assert parse_date_range({}) is None
    assert parse_date_range(None) is None


def test_union_months_merges_overlaps() -> None:
    """Two concurrent one-year roles are 12 months of experience, not 24."""
    assert union_months([(0, 11), (0, 11)]) == 12
    assert union_months([(0, 11), (6, 17)]) == 18
    assert union_months([(0, 11), (24, 35)]) == 24  # disjoint
    assert union_months([]) == 0


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def test_experience() -> None:
    profile, _ = _parse()
    assert len(profile.experience) == 2

    first = profile.experience[0]
    assert first.title == "Principal Analyst"
    assert first.company_name == "Analytical Engine Project"
    assert first.employment_type == "Full-time"
    assert first.location == "London, United Kingdom"
    assert first.date_range is not None and first.date_range.is_current

    assert first.company is not None
    assert first.company.universal_name == "analytical-engine"
    assert first.company.linkedin_url == "https://www.linkedin.com/company/analytical-engine"
    assert first.company.employee_count_range == "2-10"
    assert first.company.industries == ["Research"]
    assert first.company.logo is not None


def test_current_position_is_the_open_one() -> None:
    profile, _ = _parse()
    assert profile.current_position is not None
    assert profile.current_position.title == "Principal Analyst"


def test_total_experience_is_computed() -> None:
    profile, _ = _parse()
    assert profile.total_experience_months is not None
    assert profile.total_experience_months > 0


def test_experience_grouped() -> None:
    profile, _ = _parse()
    assert len(profile.experience_grouped) == 1
    assert profile.experience_grouped[0].company_name == "Analytical Engine Project"
    assert len(profile.experience_grouped[0].positions) == 1


def test_education() -> None:
    profile, _ = _parse()
    assert len(profile.education) == 1
    edu = profile.education[0]
    assert edu.school_name == "Private Tutelage"
    assert edu.degree_name == "Mathematics"
    assert edu.field_of_study == "Mathematics and Logic"
    assert edu.grade == "Distinction"
    assert edu.date_range is not None
    assert edu.date_range.start is not None and edu.date_range.start.year == 1832


def test_skills_are_deduplicated() -> None:
    profile, _ = _parse()
    names = [s.name for s in profile.skills]
    assert names == ["Algorithms", "Mathematics"]


def test_certifications() -> None:
    profile, _ = _parse()
    assert len(profile.certifications) == 1
    cert = profile.certifications[0]
    assert cert.name == "Certified Analytical Engine Operator"
    assert cert.authority == "Royal Society"
    assert cert.license_number == "RS-1843"
    assert cert.url == "https://example.org/cert/1843"


def test_languages_map_proficiency_enum() -> None:
    profile, _ = _parse()
    by_name = {lang.name: lang for lang in profile.languages}
    assert by_name["English"].proficiency == "Native or bilingual proficiency"
    assert by_name["English"].proficiency_code == "NATIVE_OR_BILINGUAL"
    assert by_name["French"].proficiency == "Professional working proficiency"
    # No proficiency recorded at all.
    assert by_name["Italian"].proficiency is None


def test_projects_publications_honors_volunteering_courses() -> None:
    profile, _ = _parse()
    assert profile.projects[0].title == "Note G"
    assert profile.projects[0].members == ["Charles Babbage"]
    assert profile.publications[0].publisher == "Taylor's Scientific Memoirs"
    assert profile.publications[0].authors == ["Ada Lovelace"]
    assert profile.honors[0].title == "Ada Lovelace Day namesake"
    assert profile.volunteer_experience[0].role == "Mentor"
    assert profile.volunteer_experience[0].cause == "Science And Technology"
    assert profile.courses[0].name == "Advanced Calculus"


def test_empty_sections_are_empty_lists_not_none() -> None:
    profile, _ = _parse()
    assert profile.patents == []
    assert profile.test_scores == []
    assert profile.organizations == []


# ---------------------------------------------------------------------------
# Resilience
# ---------------------------------------------------------------------------


def test_missing_sections_do_not_raise() -> None:
    warnings: list[str] = []
    profile = parse_profile_view(
        {"profile": {"firstName": "Solo"}},
        public_id="solo",
        profile_url="https://www.linkedin.com/in/solo",
        warnings=warnings,
    )
    assert profile.first_name == "Solo"
    assert profile.experience == []
    assert profile.skills == []


def test_wrong_types_in_views_do_not_raise() -> None:
    """A schema change that turns a list into a string must degrade, not crash."""
    warnings: list[str] = []
    profile = parse_profile_view(
        {
            "profile": {"firstName": "Odd"},
            "positionView": "not-a-dict",
            "skillView": {"elements": "not-a-list"},
            "educationView": {"elements": [None, 42, {"schoolName": "Real"}]},
        },
        public_id="odd",
        profile_url="https://www.linkedin.com/in/odd",
        warnings=warnings,
    )
    assert profile.experience == []
    assert profile.skills == []
    assert [e.school_name for e in profile.education] == ["Real"]


# ---------------------------------------------------------------------------
# Supplementary endpoints
# ---------------------------------------------------------------------------


def test_contact_info() -> None:
    contact = parse_contact_info(fixtures.CONTACT_INFO)
    assert contact is not None
    assert contact.email == "ada@example.org"
    assert contact.phone_numbers[0].number == "+44 20 7946 0000"
    assert contact.phone_numbers[0].type == "MOBILE"
    assert contact.twitter_handles == ["adalovelace"]
    assert contact.websites[0].url == "https://example.org"
    assert contact.websites[0].category == "Personal"
    assert contact.birthday is not None and contact.birthday.month == 12
    assert contact.birthday.year is None  # birthday without a year


def test_empty_contact_info_is_none() -> None:
    assert parse_contact_info({}) is None


def test_network_info() -> None:
    net = parse_network_info(fixtures.NETWORK_INFO)
    assert net is not None
    assert net.followers_count == 12345
    assert net.connections_count == 500
    assert net.connection_distance == "DISTANCE_2"


def test_skills_endpoint() -> None:
    skills = parse_skills_endpoint(fixtures.SKILLS)
    assert [s.name for s in skills] == ["Algorithms", "Mathematics", "Technical Writing"]
    assert skills[0].endorsement_count == 99


def test_dash_flags() -> None:
    flags = parse_dash_flags(fixtures.DASH_PROFILE)
    assert flags["is_premium"] is True
    assert flags["is_influencer"] is True
    assert flags["is_open_to_work"] is True


def test_dash_flags_absent_key_means_unknown() -> None:
    """A missing badge must be absent from the dict, not False."""
    flags = parse_dash_flags(
        {
            "included": [
                {
                    "$type": "com.linkedin.voyager.dash.identity.profile.Profile",
                    "premium": False,
                }
            ]
        }
    )
    assert flags["is_premium"] is False
    assert "is_influencer" not in flags


def test_dash_flags_on_garbage() -> None:
    assert parse_dash_flags({}) == {}
    assert parse_dash_flags({"included": "nope"}) == {}


# ---------------------------------------------------------------------------
# HTML fallback
# ---------------------------------------------------------------------------


def test_public_html_jsonld() -> None:
    warnings: list[str] = []
    profile = parse_public_html(
        fixtures.PUBLIC_HTML,
        public_id="adalovelace",
        profile_url="https://www.linkedin.com/in/adalovelace",
        warnings=warnings,
    )
    assert profile.full_name == "Ada Lovelace"
    assert profile.first_name == "Ada"
    assert profile.headline == "Principal Analyst"
    assert profile.location is not None and profile.location.city == "London"
    assert profile.profile_picture is not None
    assert profile.experience[0].company_name == "Analytical Engine Project"
    assert profile.education[0].school_name == "Private Tutelage"
    # The caller must be told this is a degraded result.
    assert any("public page" in w for w in warnings)


def test_public_html_falls_back_to_og_tags() -> None:
    html = (
        '<html><head>'
        '<meta property="og:title" content="Jane Doe - CTO - Acme | LinkedIn">'
        '<meta property="og:description" content="Building things.">'
        "</head><body></body></html>"
    )
    warnings: list[str] = []
    profile = parse_public_html(
        html,
        public_id="janedoe",
        profile_url="https://www.linkedin.com/in/janedoe",
        warnings=warnings,
    )
    assert profile.full_name == "Jane Doe"
    assert profile.headline == "CTO - Acme"
    assert any("Open Graph" in w for w in warnings)
