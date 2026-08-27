"""HTTP client for LinkedIn's internal Voyager API.

Reverse-engineering notes
-------------------------
The linkedin.com SPA talks to a private REST layer at ``/voyager/api/*``. It is
not public or documented, but it is a plain JSON API guarded by three things:

1. **Session cookie** — ``li_at``. This is what actually authenticates you.
2. **CSRF token** — the ``csrf-token`` header must exactly equal the value of
   the ``JSESSIONID`` cookie (LinkedIn stores it quoted, e.g. ``"ajax:123…"``;
   the header wants it *unquoted*). Mismatch ⇒ HTTP 403.
3. **Rest.li protocol version** — ``x-restli-protocol-version: 2.0.0``. Omitting
   it makes several endpoints return a differently-shaped legacy payload.

Two response encodings are available via the ``Accept`` header:

* ``application/vnd.linkedin.normalized+json+2.1`` returns a normalised graph:
  a small ``data`` object of URN pointers plus a flat ``included[]`` array of
  every entity. Compact, but you must resolve pointers yourself.
* The default returns a fully-inlined tree, which is far easier to parse.

We ask for the inlined tree on ``profileView`` (one call, everything embedded)
and fall back to the normalised form only where an endpoint requires it.
"""

from __future__ import annotations

import asyncio
import random
from types import TracebackType
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.config import Settings
from app.linkedin.exceptions import (
    AuthenticationError,
    ChallengeError,
    NotConfiguredError,
    ProfileNotFoundError,
    ProfileUnavailableError,
    RateLimitedError,
    UpstreamError,
)
from app.utils.logging import get_logger

log = get_logger(__name__)

VOYAGER_BASE = "https://www.linkedin.com/voyager/api"
WWW_BASE = "https://www.linkedin.com"

# Markers that appear in an HTML body when LinkedIn interposes a checkpoint
# instead of answering the API call.
_CHALLENGE_MARKERS = (
    "/checkpoint/challenge",
    "captcha-internal",
    "security-verification",
    "challengeId",
)


class _TransientUpstream(Exception):
    """Internal marker so tenacity retries only what is worth retrying."""


class VoyagerClient:
    """Async client holding one authenticated LinkedIn session.

    Instantiated once at application startup and shared across requests; httpx
    handles connection pooling, so concurrent scrapes reuse the same TLS
    connections rather than renegotiating per call.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    # -- lifecycle ---------------------------------------------------------

    async def __aenter__(self) -> VoyagerClient:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def start(self) -> None:
        if self._client is not None:
            return
        async with self._lock:
            if self._client is not None:  # re-check after acquiring
                return
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._settings.request_timeout_seconds),
                follow_redirects=False,  # a 302 to /login is signal, not noise
                http2=True,
                cookies=self._build_cookies(),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- session -----------------------------------------------------------

    def _build_cookies(self) -> httpx.Cookies:
        jar = httpx.Cookies()
        s = self._settings
        if s.linkedin_li_at:
            jar.set("li_at", s.linkedin_li_at, domain=".linkedin.com", path="/")
        if s.linkedin_jsessionid:
            # LinkedIn itself stores this quoted; send it back the same way.
            jar.set(
                "JSESSIONID",
                f'"{s.linkedin_jsessionid}"',
                domain=".linkedin.com",
                path="/",
            )
        if s.linkedin_bcookie:
            jar.set("bcookie", s.linkedin_bcookie, domain=".linkedin.com", path="/")
        if s.linkedin_lidc:
            jar.set("lidc", s.linkedin_lidc, domain=".linkedin.com", path="/")
        return jar

    def _api_headers(self, *, normalized: bool = False) -> dict[str, str]:
        s = self._settings
        accept = (
            "application/vnd.linkedin.normalized+json+2.1"
            if normalized
            else "application/json"
        )
        headers = {
            "accept": accept,
            "accept-language": "en-US,en;q=0.9",
            "user-agent": s.effective_user_agent,
            "x-restli-protocol-version": "2.0.0",
            "x-li-lang": "en_US",
            "x-li-track": (
                '{"clientVersion":"1.13.20185","mpVersion":"1.13.20185",'
                '"osName":"web","timezoneOffset":0,"timezone":"UTC",'
                '"deviceFormFactor":"DESKTOP","mpName":"voyager-web"}'
            ),
            "referer": f"{WWW_BASE}/feed/",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }
        if s.linkedin_jsessionid:
            # Must match the JSESSIONID cookie value *without* the quotes.
            headers["csrf-token"] = s.linkedin_jsessionid
        return headers

    def _html_headers(self) -> dict[str, str]:
        return {
            "accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "accept-language": "en-US,en;q=0.9",
            "user-agent": self._settings.effective_user_agent,
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "none",
            "upgrade-insecure-requests": "1",
        }

    # -- request plumbing --------------------------------------------------

    async def _jitter(self) -> None:
        """Sleep briefly so a burst of calls doesn't look machine-timed."""
        lo, hi = self._settings.min_request_delay, self._settings.max_request_delay
        if hi > 0:
            await asyncio.sleep(random.uniform(lo, max(lo, hi)))

    def _raise_for_status(self, resp: httpx.Response, *, context: str) -> None:
        status = resp.status_code

        if status < 300:
            return

        # A redirect toward /login or /checkpoint means the session is dead or
        # challenged — httpx won't follow it because follow_redirects=False.
        if status in (301, 302, 303, 307, 308):
            location = resp.headers.get("location", "")
            if "checkpoint" in location or "challenge" in location:
                raise ChallengeError(
                    f"LinkedIn redirected {context} to a security checkpoint."
                )
            if "login" in location or "authwall" in location or "uas/login" in location:
                raise AuthenticationError(
                    f"LinkedIn redirected {context} to the login page; "
                    "the session cookie is no longer valid."
                )
            raise UpstreamError(
                f"Unexpected redirect from {context}: {status} -> {location or '?'}"
            )

        if status in (401, 403):
            body = resp.text[:2000]
            if any(m in body for m in _CHALLENGE_MARKERS):
                raise ChallengeError(f"LinkedIn served a challenge for {context}.")
            # 403 on a valid session usually means the profile is out of network.
            if context.startswith("profile") and "CSRF" not in body:
                raise ProfileUnavailableError(
                    f"LinkedIn refused {context} (HTTP {status}); the profile is "
                    "likely restricted for this account."
                )
            raise AuthenticationError(
                f"LinkedIn rejected {context} with HTTP {status}. The li_at "
                "cookie or csrf-token is invalid."
            )

        if status == 404:
            raise ProfileNotFoundError(f"LinkedIn returned 404 for {context}.")

        if status == 429:
            raise RateLimitedError(f"LinkedIn rate-limited {context}.")

        if status >= 500:
            # Worth retrying — surfaced as transient to tenacity.
            raise _TransientUpstream(f"{context} returned HTTP {status}")

        raise UpstreamError(f"{context} returned unexpected HTTP {status}.")

    async def _request(
        self,
        method: str,
        url: str,
        *,
        context: str,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        if self._client is None:
            await self.start()
        assert self._client is not None

        async def _once() -> httpx.Response:
            resp = await self._client.request(  # type: ignore[union-attr]
                method, url, headers=headers, params=params
            )
            self._raise_for_status(resp, context=context)
            return resp

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(max(1, self._settings.max_retries)),
                wait=wait_exponential_jitter(initial=1, max=8),
                retry=retry_if_exception_type(
                    (_TransientUpstream, httpx.TransportError, httpx.TimeoutException)
                ),
                reraise=True,
            ):
                with attempt:
                    return await _once()
        except _TransientUpstream as exc:
            raise UpstreamError(f"LinkedIn is unavailable: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise UpstreamError(f"Timed out calling {context}.") from exc
        except httpx.TransportError as exc:
            raise UpstreamError(f"Network error calling {context}: {exc}") from exc
        except RetryError as exc:  # pragma: no cover - reraise=True precludes this
            raise UpstreamError(f"Gave up calling {context}.") from exc

        raise UpstreamError(f"Unreachable retry state for {context}.")  # pragma: no cover

    # -- public API --------------------------------------------------------

    def ensure_configured(self) -> None:
        if not self._settings.has_linkedin_session:
            raise NotConfiguredError("No LinkedIn session cookie is configured.")

    async def get_json(
        self,
        path: str,
        *,
        context: str,
        params: dict[str, Any] | None = None,
        normalized: bool = False,
    ) -> dict[str, Any]:
        """GET a Voyager endpoint and return the decoded JSON body."""
        self.ensure_configured()
        url = f"{VOYAGER_BASE}{path}"
        resp = await self._request(
            "GET",
            url,
            context=context,
            headers=self._api_headers(normalized=normalized),
            params=params,
        )
        try:
            payload = resp.json()
        except ValueError as exc:
            body = resp.text[:400]
            if any(m in body for m in _CHALLENGE_MARKERS):
                raise ChallengeError(f"LinkedIn served a challenge for {context}.") from exc
            raise UpstreamError(
                f"{context} returned non-JSON content (starts with: {body[:120]!r})."
            ) from exc

        if not isinstance(payload, dict):
            raise UpstreamError(f"{context} returned a {type(payload).__name__}, not an object.")
        return payload

    async def get_profile_view(self, public_id: str) -> dict[str, Any]:
        """The workhorse call.

        ``/identity/profiles/{id}/profileView`` returns the entire profile in
        one inlined document: the header block plus positionView, educationView,
        skillView, certificationView, languageView, projectView, publicationView,
        honorView, volunteerExperienceView, courseView, patentView and
        testScoreView. One round trip for ~95% of what we need.
        """
        await self._jitter()
        return await self.get_json(
            f"/identity/profiles/{public_id}/profileView",
            context=f"profileView({public_id})",
        )

    async def get_contact_info(self, public_id: str) -> dict[str, Any]:
        await self._jitter()
        return await self.get_json(
            f"/identity/profiles/{public_id}/profileContactInfo",
            context=f"contactInfo({public_id})",
        )

    async def get_network_info(self, public_id: str) -> dict[str, Any]:
        await self._jitter()
        return await self.get_json(
            f"/identity/profiles/{public_id}/networkinfo",
            context=f"networkInfo({public_id})",
        )

    async def get_skills(self, public_id: str, *, count: int = 100) -> dict[str, Any]:
        """profileView truncates skills; this endpoint returns the full list."""
        await self._jitter()
        return await self.get_json(
            f"/identity/profiles/{public_id}/skills",
            context=f"skills({public_id})",
            params={"count": count, "start": 0},
        )

    async def get_dash_profile(self, public_id: str) -> dict[str, Any]:
        """Newer 'dash' model — carries flags profileView omits.

        Notably ``premium``, ``influencer``, and the open-to-work / hiring
        badges. Returned in normalised form, so the caller must resolve
        ``included[]`` pointers.
        """
        await self._jitter()
        return await self.get_json(
            "/identity/dash/profiles",
            context=f"dashProfile({public_id})",
            params={"q": "memberIdentity", "memberIdentity": public_id},
            normalized=True,
        )

    async def get_public_html(self, public_id: str) -> str:
        """Fetch the rendered profile page.

        Used as a fallback when Voyager refuses. With a valid session this
        returns the full SPA shell with embedded JSON; without one, LinkedIn
        serves a reduced public page that still carries JSON-LD.
        """
        if self._client is None:
            await self.start()
        resp = await self._request(
            "GET",
            f"{WWW_BASE}/in/{public_id}",
            context=f"publicHtml({public_id})",
            headers=self._html_headers(),
        )
        return resp.text

    async def healthcheck(self) -> dict[str, Any]:
        """Verify the configured session is still alive.

        ``/me`` is the cheapest authenticated endpoint — it returns the
        mini-profile of whoever owns the cookie.
        """
        self.ensure_configured()
        payload = await self.get_json("/me", context="me()")
        mini = payload.get("miniProfile") or {}
        return {
            "authenticated": True,
            "as_public_id": mini.get("publicIdentifier"),
            "as_name": " ".join(
                p for p in (mini.get("firstName"), mini.get("lastName")) if p
            )
            or None,
        }
