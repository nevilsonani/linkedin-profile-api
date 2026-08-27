"""Request-scoped dependencies: API-key auth and service access."""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from app.config import Settings, get_settings
from app.services.profile_service import ProfileService

api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    description=(
        "API key. Required only when the deployment sets API_KEYS; "
        "open deployments ignore this header."
    ),
)


async def require_api_key(
    provided: str | None = Depends(api_key_header),
    settings: Settings = Depends(get_settings),
) -> None:
    """Enforce ``X-API-Key`` when the deployment configures any keys."""
    if not settings.auth_required:
        return

    if not provided:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "MISSING_API_KEY",
                "message": "This endpoint requires an X-API-Key header.",
                "hint": "Send your key in the 'X-API-Key' request header.",
            },
        )

    # Constant-time comparison against every configured key so response timing
    # doesn't leak how much of a guessed key was correct.
    if not any(
        secrets.compare_digest(provided, valid) for valid in settings.api_key_set
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "INVALID_API_KEY",
                "message": "The supplied API key is not recognised.",
                "hint": None,
            },
        )


def get_profile_service(request: Request) -> ProfileService:
    """Fetch the singleton service created during application startup."""
    service = getattr(request.app.state, "profile_service", None)
    if service is None:  # pragma: no cover - lifespan always sets this
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "SERVICE_UNAVAILABLE",
                "message": "Profile service is not initialised.",
                "hint": None,
            },
        )
    return service
