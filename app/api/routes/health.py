"""Liveness, readiness, and session-health endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app import __version__
from app.api.deps import get_profile_service, require_api_key
from app.config import Settings, get_settings
from app.linkedin.exceptions import LinkedInError
from app.services.profile_service import ProfileService

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    summary="Liveness probe",
    description="Always 200 while the process is up. Does not touch LinkedIn.",
)
async def health(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "linkedin_session_configured": settings.has_linkedin_session,
        "api_key_required": settings.auth_required,
    }


@router.get(
    "/health/linkedin",
    summary="LinkedIn session probe",
    description=(
        "Calls Voyager `/me` to confirm the configured cookie is still valid. "
        "Returns 200 with `status: \"degraded\"` — not a 5xx — when the session "
        "is dead, so an uptime monitor can distinguish a dead session from a "
        "dead process."
    ),
)
async def linkedin_health(
    request: Request,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    client = getattr(request.app.state, "voyager_client", None)
    if client is None:  # pragma: no cover
        return {"status": "degraded", "authenticated": False, "reason": "NO_CLIENT"}

    try:
        info = await client.healthcheck()
    except LinkedInError as exc:
        return {
            "status": "degraded",
            "authenticated": False,
            "reason": exc.code,
            "message": exc.message,
            "hint": exc.hint,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "degraded",
            "authenticated": False,
            "reason": "UNEXPECTED",
            "message": str(exc),
        }

    return {"status": "ok", **info}


@router.get(
    "/health/cache",
    summary="Cache statistics",
)
async def cache_health(
    service: ProfileService = Depends(get_profile_service),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    return {"status": "ok", "cache": service.cache.stats()}
