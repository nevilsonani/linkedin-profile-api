"""Hand-built Voyager payloads mirroring the real response shapes.

These are trimmed versions of genuine `profileView` responses: field names,
nesting, and the Rest.li union envelopes are reproduced faithfully so the
parsers are exercised against the shapes they will actually meet.
"""

from __future__ import annotations

from typing import Any

PICTURE_ROOT = "https://media.licdn.com/dms/image/C4E03AQExample/profile-displayphoto-shrink_"


def _vector_image(root: str) -> dict[str, Any]:
    return {
        "com.linkedin.common.VectorImage": {
            "rootUrl": root,
            "artifacts": [
                {
                    "width": 100,
                    "height": 100,
                    "fileIdentifyingUrlPathSegment": "100_100/0/1234567890?e=1700000000&v=beta&t=abc",
                    "expiresAt": 1700000000000,
                },
                {
                    "width": 400,
                    "height": 400,
                    "fileIdentifyingUrlPathSegment": "400_400/0/1234567890?e=1700000000&v=beta&t=def",
                    "expiresAt": 1700000000000,
                },
                {
                    "width": 200,
                    "height": 200,
                    "fileIdentifyingUrlPathSegment": "200_200/0/1234567890?e=1700000000&v=beta&t=ghi",
                    "expiresAt": 1700000000000,
                },
            ],
        }
    }


PROFILE_VIEW: dict[str, Any] = {
    "profile": {
        "entityUrn": "urn:li:fs_profile:ACoAAABCDEFGHIJKLMNOP",
        "firstName": "Ada",
        "lastName": "Lovelace",
        "headline": "Mathematician | First Computer Programmer",
        "summary": "I write algorithms for the Analytical Engine.\n\nInterested in poetical science.",
        "industryName": "Computer Software",
        "locationName": "London, England",
        "geoLocationName": "London, England, United Kingdom",
        "geoCountryName": "United Kingdom",
        "publicIdentifier": "adalovelace",
        "student": False,
        "location": {"basicLocation": {"countryCode": "gb", "postalCode": "EC1A"}},
        "miniProfile": {
            "entityUrn": "urn:li:fs_miniProfile:ACoAAABCDEFGHIJKLMNOP",
            "firstName": "Ada",
            "lastName": "Lovelace",
            "occupation": "Mathematician",
            "publicIdentifier": "adalovelace",
            "influencer": True,
            "picture": _vector_image(PICTURE_ROOT),
            "backgroundImage": _vector_image(
                "https://media.licdn.com/dms/image/C4E16AQBg/profile-displaybackgroundimage-shrink_"
            ),
        },
    },
    "positionView": {
        "elements": [
            {
                "entityUrn": "urn:li:fs_position:(ACoAAABCDEFGHIJKLMNOP,1)",
                "title": "Principal Analyst",
                "companyName": "Analytical Engine Project",
                "description": "Wrote the first published algorithm intended for machine execution.",
                "geoLocationName": "London, United Kingdom",
                "employmentType": "Full-time",
                "timePeriod": {"startDate": {"month": 6, "year": 1842}},
                "company": {
                    "miniCompany": {
                        "entityUrn": "urn:li:fs_miniCompany:999",
                        "name": "Analytical Engine Project",
                        "universalName": "analytical-engine",
                        "logo": _vector_image(
                            "https://media.licdn.com/dms/image/C4E0BAQ/company-logo_"
                        ),
                    },
                    "employeeCountRange": {"start": 2, "end": 10},
                    "industries": ["Research"],
                },
            },
            {
                "entityUrn": "urn:li:fs_position:(ACoAAABCDEFGHIJKLMNOP,2)",
                "title": "Translator",
                "companyName": "Independent",
                "timePeriod": {
                    "startDate": {"month": 1, "year": 1840},
                    "endDate": {"month": 5, "year": 1842},
                },
            },
        ]
    },
    "positionGroupView": {
        "elements": [
            {
                "name": "Analytical Engine Project",
                "timePeriod": {"startDate": {"month": 6, "year": 1842}},
                "positions": [
                    {
                        "title": "Principal Analyst",
                        "companyName": "Analytical Engine Project",
                        "timePeriod": {"startDate": {"month": 6, "year": 1842}},
                    }
                ],
            }
        ]
    },
    "educationView": {
        "elements": [
            {
                "entityUrn": "urn:li:fs_education:(ACoAAABCDEFGHIJKLMNOP,1)",
                "schoolName": "Private Tutelage",
                "degreeName": "Mathematics",
                "fieldOfStudy": "Mathematics and Logic",
                "grade": "Distinction",
                "activities": "Correspondence with Charles Babbage",
                "timePeriod": {
                    "startDate": {"year": 1832},
                    "endDate": {"year": 1840},
                },
                "school": {
                    "entityUrn": "urn:li:fs_miniSchool:1",
                    "schoolName": "Private Tutelage",
                },
            }
        ]
    },
    "skillView": {
        "elements": [
            {"name": "Algorithms"},
            {"name": "Mathematics"},
            {"name": "Algorithms"},  # duplicate on purpose
        ]
    },
    "certificationView": {
        "elements": [
            {
                "name": "Certified Analytical Engine Operator",
                "authority": "Royal Society",
                "licenseNumber": "RS-1843",
                "url": "https://example.org/cert/1843",
                "timePeriod": {"startDate": {"month": 9, "year": 1843}},
            }
        ]
    },
    "languageView": {
        "elements": [
            {"name": "English", "proficiency": "NATIVE_OR_BILINGUAL"},
            {"name": "French", "proficiency": "PROFESSIONAL_WORKING"},
            {"name": "Italian"},
        ]
    },
    "projectView": {
        "elements": [
            {
                "title": "Note G",
                "description": "Algorithm for computing Bernoulli numbers.",
                "url": "https://example.org/note-g",
                "timePeriod": {"startDate": {"year": 1843}},
                "members": [{"name": "Charles Babbage"}],
            }
        ]
    },
    "publicationView": {
        "elements": [
            {
                "name": "Sketch of the Analytical Engine",
                "publisher": "Taylor's Scientific Memoirs",
                "date": {"year": 1843},
                "authors": [{"name": "Ada Lovelace"}],
            }
        ]
    },
    "honorView": {
        "elements": [
            {
                "title": "Ada Lovelace Day namesake",
                "issuer": "Historical Recognition",
                "issueDate": {"year": 2009},
            }
        ]
    },
    "volunteerExperienceView": {
        "elements": [
            {
                "role": "Mentor",
                "companyName": "Royal Institution",
                "cause": "SCIENCE_AND_TECHNOLOGY",
                "timePeriod": {"startDate": {"year": 1841}},
            }
        ]
    },
    "courseView": {"elements": [{"name": "Advanced Calculus", "number": "MATH-401"}]},
    "patentView": {"elements": []},
    "testScoreView": {"elements": []},
    "organizationView": {"elements": []},
}


CONTACT_INFO: dict[str, Any] = {
    "emailAddress": "ada@example.org",
    "phoneNumbers": [{"number": "+44 20 7946 0000", "type": "MOBILE"}],
    "twitterHandles": [{"name": "adalovelace"}],
    "websites": [
        {
            "url": "https://example.org",
            "type": {
                "com.linkedin.voyager.identity.profile.StandardWebsite": {
                    "category": "PERSONAL"
                }
            },
        }
    ],
    "birthDateOn": {"month": 12, "day": 10},
    "address": "London, England",
    "ims": [],
}


NETWORK_INFO: dict[str, Any] = {
    "followersCount": 12345,
    "connectionsCount": 500,
    "distance": {"value": "DISTANCE_2"},
}


SKILLS: dict[str, Any] = {
    "elements": [
        {"name": "Algorithms", "endorsementCount": 99},
        {"name": "Mathematics", "endorsementCount": 87},
        {"name": "Technical Writing", "endorsementCount": 12},
    ],
    "paging": {"count": 100, "start": 0, "total": 3},
}


DASH_PROFILE: dict[str, Any] = {
    "data": {},
    "included": [
        {
            "$type": "com.linkedin.voyager.dash.identity.profile.Profile",
            "entityUrn": "urn:li:fsd_profile:ACoAAABCDEFGHIJKLMNOP",
            "firstName": "Ada",
            "lastName": "Lovelace",
            "publicIdentifier": "adalovelace",
            "premium": True,
            "influencer": True,
            "profilePicture": {"frameType": "OPEN_TO_WORK"},
        }
    ],
}


# Mirrors the real shape of LinkedIn's public JSON-LD, captured from a live
# profile: `description` carries the headline, `jobTitle` is a parallel array to
# `worksFor`, employment dates hang off an `OrganizationRole` under `member`,
# the follower count lives in `interactionStatistic`, and — critically —
# LinkedIn redacts some values with asterisks for anonymous viewers.
PUBLIC_HTML = """
<html><head>
<meta property="og:title" content="Ada Lovelace - Principal Analyst - Analytical Engine | LinkedIn">
<meta property="og:image" content="https://media.licdn.com/dms/image/og.jpg">
<script type="application/ld+json">
{
  "@context": "http://schema.org",
  "@graph": [
    {"@type": "WebPage", "url": "https://www.linkedin.com/in/adalovelace"},
    {
      "@type": "Person",
      "name": "Ada Lovelace",
      "description": "Mathematician and first computer programmer.",
      "disambiguatingDescription": "Creator, Top Voice",
      "image": {"@type": "ImageObject",
                "contentUrl": "https://media.licdn.com/dms/image/v2/ABC/profile-displayphoto-shrink_200_200/0/123?e=1&v=beta&t=x"},
      "address": {
        "@type": "PostalAddress",
        "addressLocality": "London, England, United Kingdom",
        "addressCountry": "GB"
      },
      "jobTitle": ["Principal Analyst", "********"],
      "worksFor": [
        {
          "@type": "Organization",
          "name": "Analytical Engine Project",
          "url": "https://www.linkedin.com/company/analytical-engine",
          "member": {"@type": "OrganizationRole", "startDate": 1842}
        },
        {
          "@type": "Organization",
          "name": "************ ****** ",
          "member": {"@type": "OrganizationRole", "startDate": 1840, "endDate": 1842}
        }
      ],
      "alumniOf": [
        {
          "@type": "EducationalOrganization",
          "name": "Private Tutelage",
          "url": "https://www.linkedin.com/school/private-tutelage/",
          "member": {"@type": "OrganizationRole", "startDate": 1832, "endDate": 1840}
        }
      ],
      "knowsLanguage": [{"@type": "Language", "name": "English"},
                        {"@type": "Language", "name": "French"}],
      "awards": ["Ada Lovelace Day namesake"],
      "interactionStatistic": {
        "@type": "InteractionCounter",
        "interactionType": "https://schema.org/FollowAction",
        "name": "Follows",
        "userInteractionCount": 12345
      }
    }
  ]
}
</script>
</head><body></body></html>
"""
