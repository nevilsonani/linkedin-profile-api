"""End-to-end API tests with LinkedIn mocked at the HTTP boundary.

``respx`` intercepts httpx traffic, so the real client code — headers, cookies,
status handling, retries — is exercised; only the network is fake.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.linkedin.client import PUBLIC_HOSTS
from app.main import app
from tests import fixtures

VOYAGER = "https://www.linkedin.com/voyager/api"
AUTH = {"X-API-Key": "test-key"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _block_public_hosts(
    public_id: str = "adalovelace", *, status: int = 999
) -> None:
    """Make the anonymous public-page fallback fail on every host.

    The service tries Voyager first and falls back to the public page, so a
    test that wants to observe a *Voyager* error must close the second door
    too — otherwise the fallback quietly succeeds and the error never surfaces.

    ``status`` matters. 999 is LinkedIn's throttle, which the service treats as
    transient and reports as 429 *in preference to* the Voyager error, since a
    retry would likely succeed. Tests that want to assert on the Voyager error
    itself must therefore fail the public path some other way — pass 500.
    """
    for host in PUBLIC_HOSTS:
        respx.get(f"https://{host}.linkedin.com/in/{public_id}").mock(
            return_value=httpx.Response(status, text="blocked")
        )


def _mock_all_endpoints(mock: respx.MockRouter, public_id: str = "adalovelace") -> None:
    mock.get(f"{VOYAGER}/identity/profiles/{public_id}/profileView").mock(
        return_value=httpx.Response(200, json=fixtures.PROFILE_VIEW)
    )
    mock.get(f"{VOYAGER}/identity/profiles/{public_id}/profileContactInfo").mock(
        return_value=httpx.Response(200, json=fixtures.CONTACT_INFO)
    )
    mock.get(f"{VOYAGER}/identity/profiles/{public_id}/networkinfo").mock(
        return_value=httpx.Response(200, json=fixtures.NETWORK_INFO)
    )
    mock.get(f"{VOYAGER}/identity/profiles/{public_id}/skills").mock(
        return_value=httpx.Response(200, json=fixtures.SKILLS)
    )
    mock.get(f"{VOYAGER}/identity/dash/profiles").mock(
        return_value=httpx.Response(200, json=fixtures.DASH_PROFILE)
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_health_needs_no_auth(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["linkedin_session_configured"] is True


def test_request_id_header_is_echoed(client: TestClient) -> None:
    resp = client.get("/health", headers={"X-Request-ID": "abc123"})
    assert resp.headers["x-request-id"] == "abc123"


@respx.mock
def test_linkedin_health_reports_degraded_not_5xx(client: TestClient) -> None:
    """A dead session must not look like a dead process to an uptime monitor."""
    respx.get(f"{VOYAGER}/me").mock(return_value=httpx.Response(401))
    resp = client.get("/health/linkedin", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"


@respx.mock
def test_linkedin_health_ok(client: TestClient) -> None:
    respx.get(f"{VOYAGER}/me").mock(
        return_value=httpx.Response(
            200,
            json={
                "miniProfile": {
                    "firstName": "Test",
                    "lastName": "Account",
                    "publicIdentifier": "test-account",
                }
            },
        )
    )
    resp = client.get("/health/linkedin", headers=AUTH)
    assert resp.json() == {
        "status": "ok",
        "authenticated": True,
        "as_public_id": "test-account",
        "as_name": "Test Account",
        # conftest clears LINKEDIN_IMPERSONATE so respx can intercept.
        "transport": "httpx",
    }


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_missing_api_key_is_401(client: TestClient) -> None:
    resp = client.get("/api/v1/profile?url=https://www.linkedin.com/in/adalovelace")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "MISSING_API_KEY"


def test_wrong_api_key_is_403(client: TestClient) -> None:
    resp = client.get(
        "/api/v1/profile?url=https://www.linkedin.com/in/adalovelace",
        headers={"X-API-Key": "nope"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "INVALID_API_KEY"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@respx.mock
def test_get_profile(client: TestClient) -> None:
    _mock_all_endpoints(respx.mock)

    resp = client.get(
        "/api/v1/profile?url=https://www.linkedin.com/in/adalovelace", headers=AUTH
    )
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["success"] is True

    data = body["data"]
    assert data["full_name"] == "Ada Lovelace"
    assert data["headline"] == "Mathematician | First Computer Programmer"
    assert data["profile_url"] == "https://www.linkedin.com/in/adalovelace"
    assert data["location"]["city"] == "London"
    assert len(data["experience"]) == 2
    assert len(data["education"]) == 1
    assert len(data["certifications"]) == 1
    assert len(data["languages"]) == 3
    assert data["profile_picture"]["width"] == 400

    # Enrichment merged in.
    assert data["contact_info"]["email"] == "ada@example.org"
    assert data["network_info"]["followers_count"] == 12345
    assert data["is_premium"] is True

    # The dedicated skills endpoint supersedes the truncated profileView list.
    assert [s["name"] for s in data["skills"]] == [
        "Algorithms",
        "Mathematics",
        "Technical Writing",
    ]

    meta = body["meta"]
    assert meta["source"] == "voyager"
    assert meta["cached"] is False
    assert "profileView" in meta["endpoints_succeeded"]


@respx.mock
def test_post_profile_matches_get(client: TestClient) -> None:
    _mock_all_endpoints(respx.mock)
    resp = client.post(
        "/api/v1/profile",
        json={"url": "https://www.linkedin.com/in/adalovelace"},
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["full_name"] == "Ada Lovelace"


@respx.mock
def test_bare_username_is_accepted(client: TestClient) -> None:
    _mock_all_endpoints(respx.mock)
    resp = client.get("/api/v1/profile?url=adalovelace", headers=AUTH)
    assert resp.status_code == 200


@respx.mock
def test_optional_sections_can_be_skipped(client: TestClient) -> None:
    _mock_all_endpoints(respx.mock)
    resp = client.get(
        "/api/v1/profile?url=adalovelace"
        "&include_contact_info=false&include_network_info=false",
        headers=AUTH,
    )
    body = resp.json()
    assert body["data"]["contact_info"] is None
    assert body["data"]["network_info"] is None
    assert "contactInfo" not in body["meta"]["endpoints_succeeded"]


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------


@respx.mock
def test_optional_endpoint_failure_is_a_warning_not_an_error(client: TestClient) -> None:
    """Contact info is routinely hidden; that must not fail the whole scrape."""
    respx.get(f"{VOYAGER}/identity/profiles/adalovelace/profileView").mock(
        return_value=httpx.Response(200, json=fixtures.PROFILE_VIEW)
    )
    respx.get(f"{VOYAGER}/identity/profiles/adalovelace/profileContactInfo").mock(
        return_value=httpx.Response(404)
    )
    respx.get(f"{VOYAGER}/identity/profiles/adalovelace/networkinfo").mock(
        return_value=httpx.Response(200, json=fixtures.NETWORK_INFO)
    )
    respx.get(f"{VOYAGER}/identity/profiles/adalovelace/skills").mock(
        return_value=httpx.Response(500)
    )
    respx.get(f"{VOYAGER}/identity/dash/profiles").mock(
        return_value=httpx.Response(200, json=fixtures.DASH_PROFILE)
    )

    resp = client.get("/api/v1/profile?url=adalovelace", headers=AUTH)
    assert resp.status_code == 200

    body = resp.json()
    assert body["data"]["full_name"] == "Ada Lovelace"
    assert body["data"]["contact_info"] is None
    assert body["data"]["network_info"]["followers_count"] == 12345
    # profileView's own skill list survives when the dedicated endpoint fails.
    assert [s["name"] for s in body["data"]["skills"]] == ["Algorithms", "Mathematics"]

    assert set(body["meta"]["endpoints_failed"]) == {"contactInfo", "skills"}
    assert len(body["meta"]["warnings"]) >= 2


@respx.mock
def test_challenge_falls_back_to_public_html(client: TestClient) -> None:
    respx.get(f"{VOYAGER}/identity/profiles/adalovelace/profileView").mock(
        return_value=httpx.Response(403, text="please complete captcha-internal check")
    )
    respx.get("https://www.linkedin.com/in/adalovelace").mock(
        return_value=httpx.Response(200, text=fixtures.PUBLIC_HTML)
    )

    resp = client.get("/api/v1/profile?url=adalovelace", headers=AUTH)
    assert resp.status_code == 200

    body = resp.json()
    assert body["meta"]["source"] == "public_html"
    assert body["data"]["full_name"] == "Ada Lovelace"
    assert any(
        "authenticated Voyager API was unavailable" in w
        for w in body["meta"]["warnings"]
    )


@respx.mock
def test_public_throttling_is_reported_as_retryable_not_as_auth_failure(
    client: TestClient,
) -> None:
    """A throttled public path must surface as 429, even when Voyager also failed.

    Reporting the Voyager auth error instead would send the caller chasing a
    server-side credential they cannot fix, and hide that simply retrying works.
    """
    respx.get(f"{VOYAGER}/identity/profiles/adalovelace/profileView").mock(
        return_value=httpx.Response(302, headers={"location": "/uas/login"})
    )
    _block_public_hosts()  # every host answers 999

    resp = client.get("/api/v1/profile?url=adalovelace", headers=AUTH)
    assert resp.status_code == 429

    error = resp.json()["error"]
    assert error["code"] == "LINKEDIN_RATE_LIMITED"
    assert "throttling" in error["message"]
    assert "retry" in (error["hint"] or "").lower()
    # The Voyager failure is still mentioned, just not as the headline.
    assert "LINKEDIN_AUTH_FAILED" in error["message"]


@respx.mock
def test_both_paths_failing_names_both_failures(client: TestClient) -> None:
    """When Voyager *and* the public page fail, the error must describe both.

    Reporting only one is actively misleading: a challenged session and a
    profile LinkedIn won't serve anonymously call for different responses, and
    the operator can only act on the first.
    """
    respx.get(f"{VOYAGER}/identity/profiles/adalovelace/profileView").mock(
        return_value=httpx.Response(403, text="captcha-internal")
    )
    for host in PUBLIC_HOSTS:
        respx.get(f"https://{host}.linkedin.com/in/adalovelace").mock(
            return_value=httpx.Response(
                200, text="<html>authwall Join LinkedIn to view</html>"
            )
        )

    resp = client.get("/api/v1/profile?url=adalovelace", headers=AUTH)

    # The Voyager error's type is preserved, so its status still signals that
    # the *server's* session needs attention.
    assert resp.status_code == 503

    error = resp.json()["error"]
    assert error["code"] == "LINKEDIN_CHALLENGE"
    assert "Authenticated API:" in error["message"]
    assert "Public page:" in error["message"]
    assert "publicly" in (error["hint"] or "") or "signed-in" in (error["hint"] or "")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


@respx.mock
def test_not_found(client: TestClient) -> None:
    respx.get(f"{VOYAGER}/identity/profiles/ghost/profileView").mock(
        return_value=httpx.Response(404)
    )
    resp = client.get("/api/v1/profile?url=ghost", headers=AUTH)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "PROFILE_NOT_FOUND"


@respx.mock
def test_expired_session_surfaces_as_503(client: TestClient) -> None:
    _block_public_hosts(status=500)
    respx.get(f"{VOYAGER}/identity/profiles/adalovelace/profileView").mock(
        return_value=httpx.Response(302, headers={"location": "/uas/login"})
    )
    resp = client.get("/api/v1/profile?url=adalovelace", headers=AUTH)
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "LINKEDIN_AUTH_FAILED"
    assert body["error"]["hint"] is not None


@respx.mock
def test_self_redirect_is_reported_as_a_soft_block(client: TestClient) -> None:
    """LinkedIn's soft block is a 302 pointing back at the requested URL.

    It must not be reported as a generic upstream error — the operator needs to
    know to refresh the cookie or check TLS impersonation.
    """
    url = f"{VOYAGER}/identity/profiles/adalovelace/profileView"
    _block_public_hosts(status=500)
    respx.get(url).mock(return_value=httpx.Response(302, headers={"location": url}))

    resp = client.get("/api/v1/profile?url=adalovelace", headers=AUTH)
    assert resp.status_code == 503

    error = resp.json()["error"]
    assert error["code"] == "LINKEDIN_AUTH_FAILED"
    assert "redirected the request back to itself" in error["message"]
    assert "LINKEDIN_IMPERSONATE" in (error["hint"] or "")


@respx.mock
def test_redirect_elsewhere_is_still_a_generic_upstream_error(
    client: TestClient,
) -> None:
    """A 302 to an unrelated path is not the soft block, and must not claim to be."""
    _block_public_hosts(status=500)
    respx.get(f"{VOYAGER}/identity/profiles/adalovelace/profileView").mock(
        return_value=httpx.Response(302, headers={"location": "/some/other/path"})
    )
    resp = client.get("/api/v1/profile?url=adalovelace", headers=AUTH)
    assert resp.json()["error"]["code"] == "LINKEDIN_UPSTREAM_ERROR"


@respx.mock
def test_rate_limited_upstream(client: TestClient) -> None:
    _block_public_hosts()
    respx.get(f"{VOYAGER}/identity/profiles/adalovelace/profileView").mock(
        return_value=httpx.Response(429)
    )
    resp = client.get("/api/v1/profile?url=adalovelace", headers=AUTH)
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "LINKEDIN_RATE_LIMITED"


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://example.com/in/someone",
        "https://www.linkedin.com/company/google",
        "not a url at all!!",
    ],
)
def test_invalid_urls_are_400(client: TestClient, bad_url: str) -> None:
    resp = client.get("/api/v1/profile", params={"url": bad_url}, headers=AUTH)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_PROFILE_URL"


def test_missing_url_param_is_422(client: TestClient) -> None:
    resp = client.get("/api/v1/profile", headers=AUTH)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_every_error_uses_the_same_envelope(client: TestClient) -> None:
    for resp in (
        client.get("/api/v1/profile?url=x", headers=AUTH),
        client.get("/api/v1/profile?url=adalovelace"),
        client.get("/nonexistent-route"),
    ):
        body = resp.json()
        assert body["success"] is False
        assert set(body["error"]) == {"code", "message", "hint"}
        assert "request_id" in body


# ---------------------------------------------------------------------------
# Request construction — the part that makes Voyager answer at all
# ---------------------------------------------------------------------------


@respx.mock
def test_voyager_request_carries_csrf_and_cookies(client: TestClient) -> None:
    route = respx.get(f"{VOYAGER}/identity/profiles/adalovelace/profileView").mock(
        return_value=httpx.Response(200, json=fixtures.PROFILE_VIEW)
    )
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))

    client.get("/api/v1/profile?url=adalovelace", headers=AUTH)

    request = route.calls[0].request
    # csrf-token must equal the JSESSIONID value *unquoted*.
    assert request.headers["csrf-token"] == "ajax:1234567890123456789"
    assert request.headers["x-restli-protocol-version"] == "2.0.0"

    cookie = request.headers["cookie"]
    assert "li_at=test-li-at-cookie" in cookie
    # ...while the cookie itself keeps the quotes LinkedIn set.
    assert 'JSESSIONID="ajax:1234567890123456789"' in cookie


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------


def test_openapi_schema_is_generated(client: TestClient) -> None:
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert "/api/v1/profile" in schema["paths"]
    assert "LinkedInProfile" in schema["components"]["schemas"]


def test_docs_are_served(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
