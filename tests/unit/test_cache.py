# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran,
# who specified the requirements and design and reviewed the implementation.
# See the "Credits and authorship" section of README.md.
"""Unit tests for the ETag response cache."""

from __future__ import annotations

import pytest

from app.cache import CachedList, ResponseCache


def entry(etag: str, count: int = 1) -> CachedList:
    return CachedList(etag=etag, data=[{"number": n} for n in range(count)], link=None)


def test_a_stored_entry_is_returned() -> None:
    cache = ResponseCache()
    cache.set(("open", "", 1, 30), entry("W/1"))

    stored = cache.get(("open", "", 1, 30))
    assert stored is not None
    assert stored.etag == "W/1"


def test_a_missing_key_returns_none() -> None:
    assert ResponseCache().get(("open", "", 1, 30)) is None


def test_queries_differing_in_any_parameter_are_cached_separately() -> None:
    cache = ResponseCache()
    keys = [
        ("open", "", 1, 30),
        ("closed", "", 1, 30),
        ("open", "bug", 1, 30),
        ("open", "", 2, 30),
        ("open", "", 1, 50),
    ]
    for index, key in enumerate(keys):
        cache.set(key, entry(f"W/{index}"))

    assert len(cache) == len(keys)
    for index, key in enumerate(keys):
        stored = cache.get(key)
        assert stored is not None
        assert stored.etag == f"W/{index}"


def test_writing_the_same_key_replaces_the_entry() -> None:
    cache = ResponseCache()
    cache.set(("open", "", 1, 30), entry("W/old"))
    cache.set(("open", "", 1, 30), entry("W/new"))

    stored = cache.get(("open", "", 1, 30))
    assert stored is not None
    assert stored.etag == "W/new"
    assert len(cache) == 1


def test_the_least_recently_used_entry_is_evicted_first() -> None:
    cache = ResponseCache(max_entries=2)
    cache.set(("a", "", 1, 30), entry("W/a"))
    cache.set(("b", "", 1, 30), entry("W/b"))

    cache.get(("a", "", 1, 30))  # 'a' is now the most recently used
    cache.set(("c", "", 1, 30), entry("W/c"))

    assert cache.get(("b", "", 1, 30)) is None
    assert cache.get(("a", "", 1, 30)) is not None
    assert cache.get(("c", "", 1, 30)) is not None


def test_the_cache_never_grows_past_its_limit() -> None:
    cache = ResponseCache(max_entries=3)
    for index in range(20):
        cache.set((f"k{index}", "", 1, 30), entry(f"W/{index}"))

    assert len(cache) == 3


def test_a_zero_sized_cache_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        ResponseCache(max_entries=0)


def test_hit_and_miss_counters_feed_the_stats() -> None:
    cache = ResponseCache()
    cache.record_hit()
    cache.record_hit()
    cache.record_miss()
    cache.set(("open", "", 1, 30), entry("W/1"))

    assert cache.stats == {"entries": 1, "hits": 2, "misses": 1}


def test_clearing_empties_the_cache() -> None:
    cache = ResponseCache()
    cache.set(("open", "", 1, 30), entry("W/1"))
    cache.clear()

    assert len(cache) == 0
    assert cache.get(("open", "", 1, 30)) is None
