"""Shared pytest configuration.

The env vars are set *before* ``app.config`` is imported anywhere, so the
cached ``Settings`` singleton is built with test values rather than whatever
happens to be in a developer's local ``.env``.
"""

from __future__ import annotations

import os

os.environ.setdefault("LINKEDIN_LI_AT", "test-li-at-cookie")
os.environ.setdefault("LINKEDIN_JSESSIONID", "ajax:1234567890123456789")
os.environ.setdefault("API_KEYS", "test-key")
os.environ.setdefault("CACHE_TTL_SECONDS", "0")  # deterministic tests
os.environ.setdefault("RATE_LIMIT", "1000/minute")
os.environ.setdefault("MIN_REQUEST_DELAY", "0")
os.environ.setdefault("MAX_REQUEST_DELAY", "0")
os.environ.setdefault("MAX_RETRIES", "1")
os.environ.setdefault("LOG_LEVEL", "WARNING")
# Force the httpx transport: respx can only intercept httpx, and the tests are
# about our own logic, not about defeating Cloudflare's TLS fingerprinting.
os.environ.setdefault("LINKEDIN_IMPERSONATE", "")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Keep the cached Settings consistent across the session."""
    from app.config import get_settings

    get_settings.cache_clear()
    get_settings()
    yield
