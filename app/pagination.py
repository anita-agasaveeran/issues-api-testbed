# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran,
# who specified the requirements and design and reviewed the implementation.
# See the "Credits and authorship" section of README.md.
"""Pagination helpers: RFC 8288 ``Link`` header parsing and re-writing.

GitHub returns ``Link`` headers pointing at ``api.github.com``. Those URLs are
useless to a client of *this* service — and they leak the upstream shape — so the
links are re-written to point at our own routes while preserving ``page`` and
``per_page``.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlsplit

MAX_PER_PAGE = 100
DEFAULT_PER_PAGE = 30

_LINK_SEGMENT = re.compile(r"<(?P<url>[^>]*)>\s*;\s*(?P<params>.*)", re.DOTALL)
_REL_PARAM = re.compile(r"""rel\s*=\s*(?:"(?P<quoted>[^"]*)"|(?P<bare>[^\s;,]+))""", re.IGNORECASE)


def parse_link_header(value: str | None) -> dict[str, str]:
    """Parse a ``Link`` header into ``{rel: url}``.

    Tolerates absent headers, unquoted ``rel`` values, extra link params, and
    segments with no ``rel`` at all (which are skipped). A repeated ``rel`` keeps
    the first occurrence, matching how browsers resolve duplicates.
    """
    if not value or not value.strip():
        return {}

    links: dict[str, str] = {}
    for segment in _split_segments(value):
        match = _LINK_SEGMENT.match(segment.strip())
        if not match:
            continue
        rel_match = _REL_PARAM.search(match.group("params"))
        if not rel_match:
            continue
        rel = (rel_match.group("quoted") or rel_match.group("bare")).strip()
        url = match.group("url").strip()
        if rel and url and rel not in links:
            links[rel] = url
    return links


def _split_segments(value: str) -> list[str]:
    """Split on commas that separate links, ignoring commas inside ``<...>``."""
    segments: list[str] = []
    depth = 0
    current: list[str] = []
    for char in value:
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            segments.append("".join(current))
            current = []
            continue
        current.append(char)
    if current:
        segments.append("".join(current))
    return segments


def rewrite_link_header(value: str | None, base_path: str) -> str | None:
    """Re-point an upstream ``Link`` header at ``base_path`` on this service.

    Only ``page`` and ``per_page`` survive from the upstream query string; every
    other GitHub-specific parameter is dropped. Returns ``None`` when there is
    nothing to forward, so callers can skip setting the header entirely.
    """
    links = parse_link_header(value)
    if not links:
        return None

    rendered: list[str] = []
    for rel, url in links.items():
        query = parse_qs(urlsplit(url).query)
        forwarded = {
            key: query[key][0] for key in ("page", "per_page") if query.get(key) and query[key][0]
        }
        suffix = f"?{urlencode(forwarded)}" if forwarded else ""
        rendered.append(f'<{base_path}{suffix}>; rel="{rel}"')
    return ", ".join(rendered)
