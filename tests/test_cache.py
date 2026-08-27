"""Cache behaviour — every avoided Voyager call is one less risk to the account."""

from __future__ import annotations

import asyncio

import pytest

from app.services.cache import TTLCache


async def test_get_returns_what_was_set() -> None:
    cache: TTLCache[str] = TTLCache(ttl_seconds=60, max_entries=10)
    await cache.set("k", "v")
    assert await cache.get("k") == "v"


async def test_missing_key_is_none() -> None:
    cache: TTLCache[str] = TTLCache(ttl_seconds=60, max_entries=10)
    assert await cache.get("absent") is None


async def test_zero_ttl_disables_the_cache() -> None:
    cache: TTLCache[str] = TTLCache(ttl_seconds=0, max_entries=10)
    assert cache.enabled is False
    await cache.set("k", "v")
    assert await cache.get("k") is None


async def test_entries_expire() -> None:
    cache: TTLCache[str] = TTLCache(ttl_seconds=1, max_entries=10)
    await cache.set("k", "v")
    assert await cache.get("k") == "v"
    await asyncio.sleep(1.05)
    assert await cache.get("k") is None


async def test_lru_evicts_the_least_recently_used() -> None:
    cache: TTLCache[str] = TTLCache(ttl_seconds=60, max_entries=2)
    await cache.set("a", "1")
    await cache.set("b", "2")
    await cache.get("a")  # 'a' becomes most-recently-used
    await cache.set("c", "3")  # evicts 'b'

    assert await cache.get("a") == "1"
    assert await cache.get("b") is None
    assert await cache.get("c") == "3"


async def test_invalidate_and_clear() -> None:
    cache: TTLCache[str] = TTLCache(ttl_seconds=60, max_entries=10)
    await cache.set("a", "1")
    await cache.set("b", "2")

    assert await cache.invalidate("a") is True
    assert await cache.invalidate("a") is False

    await cache.clear()
    assert await cache.get("b") is None


async def test_stats_track_hits_and_misses() -> None:
    cache: TTLCache[str] = TTLCache(ttl_seconds=60, max_entries=10)
    await cache.set("a", "1")
    await cache.get("a")
    await cache.get("nope")

    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["entries"] == 1


async def test_concurrent_access_is_safe() -> None:
    cache: TTLCache[int] = TTLCache(ttl_seconds=60, max_entries=100)

    async def writer(n: int) -> None:
        await cache.set(f"k{n}", n)

    await asyncio.gather(*(writer(i) for i in range(50)))
    results = await asyncio.gather(*(cache.get(f"k{i}") for i in range(50)))
    assert results == list(range(50))


@pytest.mark.parametrize(
    ("contact", "network", "skills"),
    [(True, True, True), (False, True, True), (True, False, False)],
)
def test_cache_key_varies_with_options(contact: bool, network: bool, skills: bool) -> None:
    """Two requests asking for different sections must not share a cache entry."""
    from app.services.profile_service import ProfileService

    key = ProfileService._cache_key("ada", contact, network, skills)
    full = ProfileService._cache_key("ada", True, True, True)
    if (contact, network, skills) == (True, True, True):
        assert key == full
    else:
        assert key != full


def test_cache_key_is_case_insensitive() -> None:
    from app.services.profile_service import ProfileService

    assert ProfileService._cache_key("Ada", True, True, True) == ProfileService._cache_key(
        "ada", True, True, True
    )
