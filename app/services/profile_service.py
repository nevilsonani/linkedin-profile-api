"""Orchestrates a full profile scrape.

Strategy
--------
Two extraction paths exist, with very different reliability profiles:

*Voyager* (authenticated) returns far richer data — skills, certifications,
contact details, full role descriptions. But it needs a ``li_at`` cookie that
LinkedIn rotates aggressively, and most of its endpoints now answer ``410
Gone`` after LinkedIn retired the legacy REST surface.

*Public page* (anonymous) returns less, and LinkedIn redacts some fields for
logged-out viewers. But it needs no session, so nothing can expire, and it
works from any host.

So the order is: cache, then Voyager if a session is configured, then the
public page. A request fails only when *both* paths fail — and the error
reported is Voyager's, since a stale cookie is the actionable problem.

Within the Voyager path:

1. Fetch the profile document. If that fails, the whole path fails.
2. Fan out the *optional* enrichment calls concurrently: contact info, network
   info, the full skill list, and the dash badges. Any of these may legitimately
   403 (privacy settings) — each failure becomes a warning, never an error.
3. Merge the enrichments over the base profile.

``meta.source`` records which path produced the payload, and ``meta.warnings``
explains anything withheld, so a caller can always tell thin data from missing
data.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from app.config import Settings
from app.linkedin.client import VoyagerClient
from app.linkedin.exceptions import (
    AuthenticationError,
    LinkedInError,
    NotConfiguredError,
    ParseError,
    ProfileNotFoundError,
    ProfileUnavailableError,
    RateLimitedError,
)
from app.linkedin.parsers.profile_view import parse_profile_view
from app.linkedin.parsers.public_html import is_authwalled, parse_public_html
from app.linkedin.parsers.supplementary import (
    parse_contact_info,
    parse_dash_flags,
    parse_network_info,
    parse_skills_endpoint,
)
from app.models.profile import LinkedInProfile, ScrapeMeta
from app.services.cache import TTLCache
from app.utils.logging import get_logger
from app.utils.url import ParsedProfileURL

log = get_logger(__name__)

T = TypeVar("T")



class ScrapeResult:
    __slots__ = ("profile", "meta")

    def __init__(self, profile: LinkedInProfile, meta: ScrapeMeta) -> None:
        self.profile = profile
        self.meta = meta


class ProfileService:
    def __init__(self, client: VoyagerClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._cache: TTLCache[ScrapeResult] = TTLCache(
            ttl_seconds=settings.cache_ttl_seconds,
            max_entries=settings.cache_max_entries,
        )

    @property
    def cache(self) -> TTLCache[ScrapeResult]:
        return self._cache

    # -----------------------------------------------------------------
    async def scrape(
        self,
        parsed: ParsedProfileURL,
        *,
        include_contact_info: bool = True,
        include_network_info: bool = True,
        include_skills: bool = True,
        use_cache: bool = True,
    ) -> ScrapeResult:
        cache_key = self._cache_key(
            parsed.public_id, include_contact_info, include_network_info, include_skills
        )

        if use_cache:
            cached = await self._cache.get(cache_key)
            if cached is not None:
                log.info("cache_hit", public_id=parsed.public_id)
                meta = cached.meta.model_copy(update={"cached": True, "source": "cache"})
                return ScrapeResult(cached.profile, meta)

        started = time.perf_counter()
        succeeded: list[str] = []
        failed: list[str] = []
        warnings: list[str] = []

        # Strategy order. Voyager yields far richer data when it works, but it
        # depends on a session cookie that expires constantly *and* most of its
        # endpoints were retired (410 Gone). The public page yields less but
        # needs no session at all, so it is the dependable floor: trying
        # Voyager first and falling back means a request only fails when both
        # do.
        voyager_error: LinkedInError | None = None
        result = None
        source = "public_html"

        if self._settings.has_linkedin_session:
            try:
                result = await self._scrape_voyager(
                    parsed,
                    include_contact_info=include_contact_info,
                    include_network_info=include_network_info,
                    include_skills=include_skills,
                    succeeded=succeeded,
                    failed=failed,
                    warnings=warnings,
                )
                source = "voyager"
            except ProfileNotFoundError:
                # Authoritative — don't waste a public fetch on a missing profile.
                raise
            except LinkedInError as exc:
                voyager_error = exc
                failed.append("voyager")
                log.warning(
                    "voyager_failed_trying_public",
                    public_id=parsed.public_id,
                    error=exc.__class__.__name__,
                    code=exc.code,
                )
        else:
            warnings.append(
                "No LinkedIn session is configured, so only public profile data "
                "was retrieved."
            )

        if result is None:
            if voyager_error is not None:
                warnings.append(
                    f"The authenticated Voyager API was unavailable "
                    f"({voyager_error.code}), so the public page was used "
                    "instead; it exposes fewer fields."
                )
            try:
                result = await self._scrape_html(parsed, warnings=warnings)
                succeeded.append("publicHtml")
                source = "public_html"
            except LinkedInError as public_error:
                # Both paths failed. Report whichever error is more actionable:
                # a dead session is something the operator can fix.
                raise (voyager_error or public_error) from public_error

        duration_ms = int((time.perf_counter() - started) * 1000)
        meta = ScrapeMeta(
            source=source,  # type: ignore[arg-type]
            fetched_at=datetime.now(UTC),
            duration_ms=duration_ms,
            cached=False,
            endpoints_succeeded=succeeded,
            endpoints_failed=failed,
            warnings=warnings,
        )

        scrape_result = ScrapeResult(result, meta)
        await self._cache.set(cache_key, scrape_result)

        log.info(
            "scrape_complete",
            public_id=parsed.public_id,
            source=source,
            duration_ms=duration_ms,
            experience=len(result.experience),
            education=len(result.education),
            skills=len(result.skills),
            warnings=len(warnings),
        )
        return scrape_result

    # -----------------------------------------------------------------
    async def _scrape_voyager(
        self,
        parsed: ParsedProfileURL,
        *,
        include_contact_info: bool,
        include_network_info: bool,
        include_skills: bool,
        succeeded: list[str],
        failed: list[str],
        warnings: list[str],
    ) -> LinkedInProfile:
        # --- required call ------------------------------------------------
        doc = await self._client.get_profile_view(parsed.public_id)
        succeeded.append("profileView")

        if not isinstance(doc.get("profile"), dict):
            raise ParseError(
                "profileView responded without a 'profile' block; the Voyager "
                "schema may have changed."
            )

        profile = parse_profile_view(
            doc,
            public_id=parsed.public_id,
            profile_url=parsed.canonical_url,
            warnings=warnings,
        )

        # --- optional enrichment, concurrently ----------------------------
        tasks: dict[str, Awaitable[Any]] = {}
        if include_contact_info:
            tasks["contactInfo"] = self._optional(
                "contactInfo",
                lambda: self._client.get_contact_info(parsed.public_id),
                succeeded,
                failed,
                warnings,
            )
        if include_network_info:
            tasks["networkInfo"] = self._optional(
                "networkInfo",
                lambda: self._client.get_network_info(parsed.public_id),
                succeeded,
                failed,
                warnings,
            )
        if include_skills:
            tasks["skills"] = self._optional(
                "skills",
                lambda: self._client.get_skills(parsed.public_id),
                succeeded,
                failed,
                warnings,
            )
        tasks["dashProfile"] = self._optional(
            "dashProfile",
            lambda: self._client.get_dash_profile(parsed.public_id),
            succeeded,
            failed,
            warnings,
        )

        if tasks:
            results = await asyncio.gather(*tasks.values())
            payloads = dict(zip(tasks.keys(), results, strict=True))
        else:
            payloads = {}

        self._merge(profile, payloads, warnings)
        return profile

    async def _optional(
        self,
        name: str,
        call: Callable[[], Awaitable[dict[str, Any]]],
        succeeded: list[str],
        failed: list[str],
        warnings: list[str],
    ) -> dict[str, Any] | None:
        """Run an enrichment call, converting any failure into a warning.

        Auth and rate-limit failures are re-raised: if the session is dead or
        throttled, callers need to know that rather than silently receiving a
        thinner profile.
        """
        try:
            payload = await call()
            succeeded.append(name)
            return payload
        except (AuthenticationError, RateLimitedError, NotConfiguredError):
            raise
        except LinkedInError as exc:
            failed.append(name)
            warnings.append(f"Optional endpoint '{name}' failed: {exc.code}")
            log.info("optional_endpoint_failed", endpoint=name, code=exc.code)
            return None
        except Exception as exc:  # noqa: BLE001 - enrichment must never be fatal
            failed.append(name)
            warnings.append(f"Optional endpoint '{name}' errored: {exc.__class__.__name__}")
            log.warning("optional_endpoint_error", endpoint=name, error=str(exc))
            return None

    def _merge(
        self,
        profile: LinkedInProfile,
        payloads: dict[str, dict[str, Any] | None],
        warnings: list[str],
    ) -> None:
        """Overlay enrichment payloads onto the base profile, in place."""
        contact = payloads.get("contactInfo")
        if contact:
            try:
                profile.contact_info = parse_contact_info(contact)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Failed to parse contact info: {exc.__class__.__name__}")

        network = payloads.get("networkInfo")
        if network:
            try:
                profile.network_info = parse_network_info(network)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Failed to parse network info: {exc.__class__.__name__}")

        skills_doc = payloads.get("skills")
        if skills_doc:
            try:
                full = parse_skills_endpoint(skills_doc)
                # The dedicated endpoint returns a superset; prefer it, but keep
                # anything profileView had that it somehow omitted.
                if full:
                    known = {s.name.casefold() for s in full}
                    extra = [s for s in profile.skills if s.name.casefold() not in known]
                    profile.skills = full + extra
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Failed to parse skills: {exc.__class__.__name__}")

        dash = payloads.get("dashProfile")
        if dash:
            try:
                flags = parse_dash_flags(dash)
                for field, value in flags.items():
                    if not hasattr(profile, field):
                        continue
                    # Only fill gaps — profileView is the source of truth for
                    # anything it already answered.
                    if getattr(profile, field) in (None, ""):
                        setattr(profile, field, value)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Failed to parse dash profile: {exc.__class__.__name__}")

    # -----------------------------------------------------------------
    async def _scrape_html(
        self, parsed: ParsedProfileURL, *, warnings: list[str]
    ) -> LinkedInProfile:
        html = await self._client.get_public_html(parsed.public_id)

        if is_authwalled(html):
            raise ProfileUnavailableError(
                "LinkedIn served an authentication wall for this profile and the "
                "Voyager API also refused it."
            )

        return parse_public_html(
            html,
            public_id=parsed.public_id,
            profile_url=parsed.canonical_url,
            warnings=warnings,
        )

    # -----------------------------------------------------------------
    @staticmethod
    def _cache_key(public_id: str, contact: bool, network: bool, skills: bool) -> str:
        flags = f"{int(contact)}{int(network)}{int(skills)}"
        return f"{public_id.casefold()}:{flags}"

    async def raw(self, parsed: ParsedProfileURL) -> dict[str, Any]:
        """Return unparsed Voyager payloads. Debug aid, not a public contract."""
        out: dict[str, Any] = {}
        calls: dict[str, Callable[[], Awaitable[dict[str, Any]]]] = {
            "profileView": lambda: self._client.get_profile_view(parsed.public_id),
            "contactInfo": lambda: self._client.get_contact_info(parsed.public_id),
            "networkInfo": lambda: self._client.get_network_info(parsed.public_id),
            "skills": lambda: self._client.get_skills(parsed.public_id),
            "dashProfile": lambda: self._client.get_dash_profile(parsed.public_id),
        }
        for name, call in calls.items():
            try:
                out[name] = await call()
            except LinkedInError as exc:
                out[name] = {"_error": exc.code, "_message": str(exc)}
            except Exception as exc:  # noqa: BLE001
                out[name] = {"_error": "UNEXPECTED", "_message": str(exc)}
        return out
