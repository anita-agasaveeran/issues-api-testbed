# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran
# and Ameya Mathew, who specified the requirements and design and reviewed the
# implementation.
# See the "Credits and authorship" section of README.md.
from __future__ import annotations

import pytest

from app.pagination import parse_link_header, rewrite_link_header

GITHUB_LINK = (
    '<https://api.github.com/repositories/1/issues?state=open&per_page=2&page=2>; rel="next", '
    '<https://api.github.com/repositories/1/issues?state=open&per_page=2&page=5>; rel="last"'
)


def test_parses_next_and_last_relations() -> None:
    links = parse_link_header(GITHUB_LINK)
    assert set(links) == {"next", "last"}
    assert links["next"].endswith("page=2")


@pytest.mark.parametrize("value", [None, "", "   "])
def test_absent_header_yields_no_links(value: str | None) -> None:
    assert parse_link_header(value) == {}


def test_unquoted_rel_is_accepted() -> None:
    assert parse_link_header("<https://example.test/x?page=3>; rel=next") == {
        "next": "https://example.test/x?page=3"
    }


def test_extra_link_params_do_not_confuse_the_parser() -> None:
    header = '<https://example.test/x?page=3>; type="application/json"; rel="next"'
    assert parse_link_header(header) == {"next": "https://example.test/x?page=3"}


def test_segment_without_a_rel_is_skipped() -> None:
    header = '<https://example.test/a>, <https://example.test/b>; rel="prev"'
    assert parse_link_header(header) == {"prev": "https://example.test/b"}


def test_comma_inside_the_url_does_not_split_the_segment() -> None:
    header = '<https://example.test/x?labels=bug,ops&page=2>; rel="next"'
    assert parse_link_header(header) == {"next": "https://example.test/x?labels=bug,ops&page=2"}


def test_first_occurrence_of_a_repeated_rel_wins() -> None:
    header = '<https://example.test/a>; rel="next", <https://example.test/b>; rel="next"'
    assert parse_link_header(header) == {"next": "https://example.test/a"}


def test_rewrite_points_links_at_our_own_route() -> None:
    rewritten = rewrite_link_header(GITHUB_LINK, "/issues")
    assert rewritten is not None
    assert "api.github.com" not in rewritten
    assert '</issues?page=2&per_page=2>; rel="next"' in rewritten
    assert 'rel="last"' in rewritten


def test_rewrite_drops_upstream_only_query_params() -> None:
    rewritten = rewrite_link_header(GITHUB_LINK, "/issues")
    assert rewritten is not None
    assert "state=open" not in rewritten


def test_rewrite_returns_none_when_there_is_nothing_to_forward() -> None:
    assert rewrite_link_header(None, "/issues") is None
    assert rewrite_link_header("garbage", "/issues") is None


def test_rewrite_handles_links_without_pagination_params() -> None:
    header = '<https://api.github.com/repositories/1/issues>; rel="first"'
    assert rewrite_link_header(header, "/issues") == '</issues>; rel="first"'
