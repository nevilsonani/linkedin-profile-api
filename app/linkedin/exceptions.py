"""Typed errors raised by the LinkedIn layer.

Each carries an HTTP status and a stable machine-readable ``code`` so the API
layer can translate without string-matching on messages.
"""

from __future__ import annotations


class LinkedInError(Exception):
    """Base class for every failure originating from the LinkedIn layer."""

    code = "LINKEDIN_ERROR"
    status_code = 502
    hint: str | None = None

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if hint is not None:
            self.hint = hint


class AuthenticationError(LinkedInError):
    """The li_at cookie is missing, expired, or was rejected."""

    code = "LINKEDIN_AUTH_FAILED"
    status_code = 503
    hint = (
        "The server's LinkedIn session cookie is invalid or expired. "
        "Refresh LINKEDIN_LI_AT / LINKEDIN_JSESSIONID and redeploy."
    )


class NotConfiguredError(LinkedInError):
    """No LinkedIn session was configured at all."""

    code = "LINKEDIN_NOT_CONFIGURED"
    status_code = 503
    hint = "Set LINKEDIN_LI_AT and LINKEDIN_JSESSIONID in the environment."


class ProfileNotFoundError(LinkedInError):
    """LinkedIn returned 404 for this public identifier."""

    code = "PROFILE_NOT_FOUND"
    status_code = 404
    hint = (
        "Check the URL. The profile may have been deleted, renamed, or it may "
        "be out of reach for the authenticated account."
    )


class RateLimitedError(LinkedInError):
    """LinkedIn is throttling us (HTTP 429), or served a challenge page."""

    code = "LINKEDIN_RATE_LIMITED"
    status_code = 429
    hint = (
        "LinkedIn is throttling this session. Back off for several minutes "
        "before retrying; sustained high volume will get the account flagged."
    )


class ChallengeError(LinkedInError):
    """LinkedIn interposed a CAPTCHA / security checkpoint."""

    code = "LINKEDIN_CHALLENGE"
    status_code = 503
    hint = (
        "LinkedIn served a security checkpoint. Log in to the account in a "
        "browser, clear the challenge, then issue a fresh li_at cookie."
    )


class ProfileUnavailableError(LinkedInError):
    """Profile exists but is not viewable (out of network / privacy settings)."""

    code = "PROFILE_UNAVAILABLE"
    status_code = 403
    hint = (
        "The profile exists but is not visible to the authenticated account "
        "due to LinkedIn privacy or network-distance restrictions."
    )


class UpstreamError(LinkedInError):
    """Network failure or unexpected 5xx from LinkedIn."""

    code = "LINKEDIN_UPSTREAM_ERROR"
    status_code = 502


class ParseError(LinkedInError):
    """We reached LinkedIn but could not make sense of the payload."""

    code = "PARSE_ERROR"
    status_code = 502
    hint = (
        "LinkedIn's response shape changed. This usually means the Voyager "
        "schema was updated and the parser needs adjusting."
    )
