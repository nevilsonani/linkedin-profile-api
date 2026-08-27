"""FastAPI application: wiring, middleware, and error translation."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api.routes import health as health_routes
from app.api.routes import profile as profile_routes
from app.config import get_settings
from app.linkedin.client import VoyagerClient
from app.linkedin.exceptions import LinkedInError
from app.services.profile_service import ProfileService
from app.utils.logging import configure_logging, get_logger

settings = get_settings()
configure_logging(settings.log_level)
log = get_logger("app")

limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit])

DESCRIPTION = """
Reverse-engineered access to LinkedIn member profiles, returned as structured JSON.

**How it works.** The service authenticates to LinkedIn with a real session
cookie and calls the private `/voyager/api/*` endpoints that linkedin.com's own
front-end uses, then normalises the responses into a stable schema. When Voyager
refuses a profile, it degrades to parsing the rendered page's `schema.org/Person`
block rather than failing outright.

**Getting started**

```bash
curl -s "$BASE_URL/api/v1/profile?url=https://www.linkedin.com/in/williamhgates" \\
  -H "X-API-Key: $API_KEY" | jq
```

**Reading the response.** Every field is nullable and every list may be empty —
LinkedIn profiles are inconsistent and privacy settings hide things. Consult
`meta.warnings` and `meta.endpoints_failed` to tell "the profile has no
certifications" apart from "we could not read the certifications".

**Rate limits.** Requests are limited per client IP, and successful scrapes are
cached in-process. Both exist to keep the upstream LinkedIn account healthy;
hammering the API will get the underlying session flagged.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    client = VoyagerClient(settings)
    await client.start()

    app.state.voyager_client = client
    app.state.profile_service = ProfileService(client, settings)

    log.info(
        "startup",
        version=__version__,
        linkedin_session=settings.has_linkedin_session,
        api_key_required=settings.auth_required,
        cache_ttl=settings.cache_ttl_seconds,
        rate_limit=settings.rate_limit,
    )
    if not settings.has_linkedin_session:
        log.warning(
            "no_linkedin_session",
            message=(
                "LINKEDIN_LI_AT is unset. Scrape requests will fail with "
                "LINKEDIN_NOT_CONFIGURED until it is provided."
            ),
        )

    try:
        yield
    finally:
        await client.aclose()
        log.info("shutdown")


app = FastAPI(
    title="LinkedIn Profile API",
    description=DESCRIPTION,
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url="/redoc" if settings.enable_docs else None,
    openapi_url="/openapi.json" if settings.enable_docs else None,
    contact={"name": "LinkedIn Profile API"},
    license_info={"name": "MIT"},
)

app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Tag every request with an id, echoed back in the response header."""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    return response


# ---------------------------------------------------------------------------
# Error handlers — every failure exits through the same envelope
# ---------------------------------------------------------------------------


def _envelope(
    request: Request, status_code: int, code: str, message: str, hint: str | None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error": {"code": code, "message": message, "hint": hint},
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        return _envelope(
            request,
            exc.status_code,
            str(detail.get("code")),
            str(detail.get("message", "")),
            detail.get("hint"),
        )
    return _envelope(
        request,
        exc.status_code,
        f"HTTP_{exc.status_code}",
        str(detail),
        None,
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    problems = "; ".join(
        f"{'.'.join(str(p) for p in err.get('loc', [])[1:])}: {err.get('msg')}"
        for err in exc.errors()
    )
    return _envelope(
        request,
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "VALIDATION_ERROR",
        problems or "Request body failed validation.",
        "Check the request schema at /docs.",
    )


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return _envelope(
        request,
        status.HTTP_429_TOO_MANY_REQUESTS,
        "RATE_LIMITED",
        f"Rate limit exceeded ({exc.detail}).",
        "Slow down and retry shortly.",
    )


@app.exception_handler(LinkedInError)
async def linkedin_error_handler(request: Request, exc: LinkedInError) -> JSONResponse:
    """Catch LinkedIn errors that escaped a route's own handling."""
    log.warning("linkedin_error", code=exc.code, message=exc.message)
    return _envelope(request, exc.status_code, exc.code, exc.message, exc.hint)


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled_error", error=str(exc))
    # Deliberately opaque: an internal traceback could leak cookie material.
    return _envelope(
        request,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "INTERNAL_ERROR",
        "An unexpected error occurred.",
        "Quote the request_id when reporting this.",
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

app.include_router(health_routes.router)
app.include_router(profile_routes.router)


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse | JSONResponse:
    if settings.enable_docs:
        return RedirectResponse(url="/docs")
    return JSONResponse(
        {
            "name": "LinkedIn Profile API",
            "version": __version__,
            "endpoints": ["/health", "/api/v1/profile"],
        }
    )
