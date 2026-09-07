# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran
# and Ameya Mathew, who specified the requirements and design and reviewed the
# implementation.
# See the "Credits and authorship" section of README.md.
"""A small in-memory cache of list responses, keyed by ETag.

GitHub does not count a ``304 Not Modified`` against the hourly rate limit. So the
service remembers the ETag and body of each distinct ``GET /issues`` query, and
replays that ETag as ``If-None-Match`` on the next identical request. When nothing
has changed upstream, the caller still gets a full ``200`` with a body, but the
request cost no quota.

The cache is per-process and deliberately small: it is a quota optimisation, not a
source of truth. A restart loses it, which costs one ordinary request.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from app.logging import get_logger

log = get_logger("cache")

#: (state, labels, page, per_page) — everything that changes the upstream result.
CacheKey = tuple[str, str, int, int]


@dataclass(frozen=True, slots=True)
class CachedList:
    """A previously fetched page, held alongside the ETag that identifies it."""

    etag: str
    data: list[dict[str, Any]]
    link: str | None


class ResponseCache:
    """A bounded LRU cache. Not thread-safe, which is fine for a single event loop."""

    def __init__(self, max_entries: int = 128) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self._max_entries = max_entries
        self._entries: OrderedDict[CacheKey, CachedList] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: CacheKey) -> CachedList | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        self._entries.move_to_end(key)
        return entry

    def set(self, key: CacheKey, entry: CachedList) -> None:
        self._entries[key] = entry
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            evicted, _ = self._entries.popitem(last=False)
            log.debug("cache_evicted", key=str(evicted))

    def record_hit(self) -> None:
        self.hits += 1

    def record_miss(self) -> None:
        self.misses += 1

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def stats(self) -> dict[str, int]:
        return {"entries": len(self._entries), "hits": self.hits, "misses": self.misses}
