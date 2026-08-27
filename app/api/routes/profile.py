"""Profile scraping endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_profile_service, require_api_key
from app.config import Settings, get_settings
from app.linkedin.exceptions import LinkedInError
from app.models.profile import ErrorResponse, ProfileResponse, RawResponse
from app.services.profile_service import ProfileService
from app.utils.url import InvalidProfileURLError, parse_profile_url

router = APIRouter(prefix="/api/v1", tags=["profile"])


class ProfileRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "url": "https://www.linkedin.com/in/williamhgates/",
                "include_contact_info": True,
                "include_network_info": True,
                "include_skills": True,
                "use_cache": True,
            }
        }
    )

    url: str = Field(
        description=(
            "LinkedIn profile URL. Accepts full URLs, scheme-less URLs, "
            "country subdomains, or a bare username."
        ),
        examples=["https://www.linkedin.com/in/williamhgates/"],
    )
    include_contact_info: bool = Field(
        True, description="Fetch /profileContactInfo (email, websites, phone)."
    )
    include_network_info: bool = Field(
        True, description="Fetch /networkinfo (followers, connections)."
    )
    include_skills: bool = Field(
        True, description="Fetch the full skill list rather than the truncated one."
    )
    use_cache: bool = Field(
        True, description="Set false to force a live scrape, bypassing the cache."
    )


_ERROR_RESPONSES: dict[int | str, dict] = {
    400: {"model": ErrorResponse, "description": "Malformed LinkedIn profile URL"},
    401: {"model": ErrorResponse, "description": "Missing API key"},
    403: {"model": ErrorResponse, "description": "Invalid API key, or profile not viewable"},
    404: {"model": ErrorResponse, "description": "Profile does not exist"},
    429: {"model": ErrorResponse, "description": "Rate limited (by this API or by LinkedIn)"},
    502: {"model": ErrorResponse, "description": "LinkedIn upstream failure"},
    503: {"model": ErrorResponse, "description": "LinkedIn session invalid or challenged"},
}


def _bad_url(exc: InvalidProfileURLError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "code": "INVALID_PROFILE_URL",
            "message": str(exc),
            "hint": "Expected a URL like https://www.linkedin.com/in/username",
        },
    )


def _upstream(exc: LinkedInError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message, "hint": exc.hint},
    )


async def _run(
    service: ProfileService,
    *,
    url: str,
    include_contact_info: bool,
    include_network_info: bool,
    include_skills: bool,
    use_cache: bool,
) -> ProfileResponse:
    try:
        parsed = parse_profile_url(url)
    except InvalidProfileURLError as exc:
        raise _bad_url(exc) from exc

    try:
        result = await service.scrape(
            parsed,
            include_contact_info=include_contact_info,
            include_network_info=include_network_info,
            include_skills=include_skills,
            use_cache=use_cache,
        )
    except LinkedInError as exc:
        raise _upstream(exc) from exc

    return ProfileResponse(data=result.profile, meta=result.meta)


@router.post(
    "/profile",
    response_model=ProfileResponse,
    responses=_ERROR_RESPONSES,
    summary="Scrape a LinkedIn profile",
    description=(
        "Fetches a LinkedIn profile and returns it as structured JSON.\n\n"
        "Sections that the authenticated account cannot see are returned empty "
        "rather than causing an error; check `meta.warnings` to distinguish "
        "\"absent\" from \"hidden\"."
    ),
)
async def scrape_profile(
    request: Request,
    payload: Annotated[ProfileRequest, Body()],
    service: ProfileService = Depends(get_profile_service),
    _: None = Depends(require_api_key),
) -> ProfileResponse:
    return await _run(
        service,
        url=payload.url,
        include_contact_info=payload.include_contact_info,
        include_network_info=payload.include_network_info,
        include_skills=payload.include_skills,
        use_cache=payload.use_cache,
    )


@router.get(
    "/profile",
    response_model=ProfileResponse,
    responses=_ERROR_RESPONSES,
    summary="Scrape a LinkedIn profile (query-string form)",
    description="Identical to `POST /api/v1/profile`, convenient for a browser or curl.",
)
async def scrape_profile_get(
    request: Request,
    url: Annotated[
        str,
        Query(
            description="LinkedIn profile URL or bare username.",
            examples=["https://www.linkedin.com/in/williamhgates/"],
        ),
    ],
    include_contact_info: Annotated[bool, Query()] = True,
    include_network_info: Annotated[bool, Query()] = True,
    include_skills: Annotated[bool, Query()] = True,
    use_cache: Annotated[bool, Query()] = True,
    service: ProfileService = Depends(get_profile_service),
    _: None = Depends(require_api_key),
) -> ProfileResponse:
    return await _run(
        service,
        url=url,
        include_contact_info=include_contact_info,
        include_network_info=include_network_info,
        include_skills=include_skills,
        use_cache=use_cache,
    )


@router.get(
    "/profile/raw",
    response_model=RawResponse,
    responses=_ERROR_RESPONSES,
    summary="Unparsed Voyager payloads (debug)",
    description=(
        "Returns every upstream response verbatim, without parsing. Intended "
        "for diagnosing schema drift when a section starts coming back empty. "
        "The shape of this response is **not** a stable contract."
    ),
)
async def raw_profile(
    request: Request,
    url: Annotated[str, Query(description="LinkedIn profile URL or bare username.")],
    service: ProfileService = Depends(get_profile_service),
    settings: Settings = Depends(get_settings),
    _: None = Depends(require_api_key),
) -> RawResponse:
    # Raw payloads can contain contact details; keep this off open deployments.
    if not settings.auth_required:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "DEBUG_ENDPOINT_DISABLED",
                "message": "The raw endpoint is disabled when the API runs without keys.",
                "hint": "Set API_KEYS in the environment to enable it.",
            },
        )

    try:
        parsed = parse_profile_url(url)
    except InvalidProfileURLError as exc:
        raise _bad_url(exc) from exc

    try:
        endpoints = await service.raw(parsed)
    except LinkedInError as exc:
        raise _upstream(exc) from exc

    return RawResponse(public_id=parsed.public_id, endpoints=endpoints)


@router.delete(
    "/cache",
    summary="Clear the profile cache",
    description="Drops every cached profile. Useful after refreshing the LinkedIn session.",
)
async def clear_cache(
    service: ProfileService = Depends(get_profile_service),
    _: None = Depends(require_api_key),
) -> dict[str, object]:
    stats_before = service.cache.stats()
    await service.cache.clear()
    return {"success": True, "cleared_entries": stats_before["entries"]}
