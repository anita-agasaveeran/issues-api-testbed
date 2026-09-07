# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran,
# who specified the requirements and design and reviewed the implementation.
# See the "Credits and authorship" section of README.md.
"""Route-level tests: validation, HTTP semantics, and payload projection."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.conftest import ApiFactory


def responder(status_code: int, payload: Any, headers: dict[str, str] | None = None) -> Any:
    """A mock transport handler that always answers the same way."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload, headers=headers or {})

    return handler


# ------------------------------------------------------------------ POST /issues


def test_create_issue_returns_201_with_location_header(
    make_api: ApiFactory, github_issue: dict[str, Any]
) -> None:
    api = make_api(responder(201, github_issue))
    response = api.post("/issues", json={"title": "Checkout button is dead"})

    assert response.status_code == 201
    assert response.headers["location"] == "/issues/42"


def test_create_issue_projects_labels_to_plain_strings(
    make_api: ApiFactory, github_issue: dict[str, Any]
) -> None:
    api = make_api(responder(201, github_issue))
    body = api.post("/issues", json={"title": "x"}).json()

    assert body["labels"] == ["bug", "ops"]
    assert body["user"] == "test-owner"
    assert body["number"] == 42
    assert body["html_url"].endswith("/issues/42")


def test_create_issue_forwards_title_body_and_labels_upstream(
    make_api: ApiFactory, github_issue: dict[str, Any]
) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content"] = request.content
        seen["method"] = request.method
        return httpx.Response(201, json=github_issue)

    api = make_api(handler)
    api.post("/issues", json={"title": " spaced ", "body": "b", "labels": ["bug"]})

    assert seen["method"] == "POST"
    assert b'"title":"spaced"' in seen["content"]
    assert b'"labels":["bug"]' in seen["content"]


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="missing-title"),
        pytest.param({"body": "no title here"}, id="body-only"),
        pytest.param({"title": ""}, id="empty-title"),
        pytest.param({"title": "   "}, id="whitespace-title"),
        pytest.param({"title": "ok", "labels": ["bug", "  "]}, id="blank-label"),
        pytest.param({"title": "ok", "nope": 1}, id="unknown-field"),
        pytest.param({"title": 42}, id="wrong-type"),
    ],
)
def test_invalid_create_payloads_are_rejected_with_400(
    make_api: ApiFactory, github_issue: dict[str, Any], payload: dict[str, Any]
) -> None:
    api = make_api(responder(201, github_issue))
    response = api.post("/issues", json=payload)

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"]


def test_upstream_401_is_surfaced_as_401(make_api: ApiFactory) -> None:
    api = make_api(responder(401, {"message": "Bad credentials"}))
    response = api.post("/issues", json={"title": "x"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_upstream_rate_limit_becomes_429_with_retry_after(make_api: ApiFactory) -> None:
    api = make_api(
        responder(403, {"message": "API rate limit exceeded"}, {"retry-after": "30"}),
    )
    response = api.post("/issues", json={"title": "x"})

    assert response.status_code == 429
    assert response.headers["retry-after"] == "30"
    assert response.json()["error"]["code"] == "rate_limited"


# ------------------------------------------------------------------- GET /issues


def test_list_issues_filters_out_pull_requests(
    make_api: ApiFactory, github_issue: dict[str, Any], github_pull_request: dict[str, Any]
) -> None:
    api = make_api(responder(200, [github_issue, github_pull_request]))
    body = api.get("/issues").json()

    assert [item["number"] for item in body] == [42]


def test_list_issues_forwards_query_parameters_upstream(
    make_api: ApiFactory, github_issue: dict[str, Any]
) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=[github_issue])

    api = make_api(handler)
    api.get("/issues", params={"state": "closed", "labels": "bug,ops", "page": 3, "per_page": 50})

    assert seen["params"] == {"state": "closed", "labels": "bug,ops", "page": "3", "per_page": "50"}


def test_list_issues_defaults_to_open_state(
    make_api: ApiFactory, github_issue: dict[str, Any]
) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=[])

    api = make_api(handler)
    api.get("/issues")

    assert seen["params"]["state"] == "open"
    assert seen["params"]["per_page"] == "30"


def test_list_issues_rewrites_the_link_header_to_local_routes(
    make_api: ApiFactory, github_issue: dict[str, Any]
) -> None:
    upstream_link = (
        '<https://api.github.com/repositories/1/issues?state=open&page=2&per_page=30>; rel="next"'
    )
    api = make_api(responder(200, [github_issue], {"link": upstream_link}))
    response = api.get("/issues")

    assert "api.github.com" not in response.headers["link"]
    assert response.headers["link"] == '</issues?page=2&per_page=30>; rel="next"'
    assert response.headers["x-page"] == "1"
    assert response.headers["x-per-page"] == "30"


def test_list_issues_exposes_the_upstream_etag(
    make_api: ApiFactory, github_issue: dict[str, Any]
) -> None:
    api = make_api(responder(200, [github_issue], {"etag": 'W/"deadbeef"'}))
    assert api.get("/issues").headers["etag"] == 'W/"deadbeef"'


def test_conditional_get_forwards_if_none_match_and_returns_304(make_api: ApiFactory) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["if_none_match"] = request.headers.get("if-none-match")
        return httpx.Response(304, headers={"etag": 'W/"deadbeef"'})

    api = make_api(handler)
    response = api.get("/issues", headers={"If-None-Match": 'W/"deadbeef"'})

    assert seen["if_none_match"] == 'W/"deadbeef"'
    assert response.status_code == 304
    assert not response.content


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"state": "banana"}, id="invalid-state"),
        pytest.param({"per_page": 101}, id="per-page-too-large"),
        pytest.param({"per_page": 0}, id="per-page-zero"),
        pytest.param({"page": 0}, id="page-zero"),
        pytest.param({"page": "abc"}, id="page-not-a-number"),
    ],
)
def test_invalid_query_parameters_are_rejected_with_400(
    make_api: ApiFactory, params: dict[str, Any]
) -> None:
    api = make_api(responder(200, []))
    response = api.get("/issues", params=params)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


# ----------------------------------------------------------- GET /issues/{number}


def test_get_issue_returns_the_projected_issue(
    make_api: ApiFactory, github_issue: dict[str, Any]
) -> None:
    api = make_api(responder(200, github_issue))
    response = api.get("/issues/42")

    assert response.status_code == 200
    assert response.json()["title"] == "Checkout button is dead"


def test_get_issue_reports_a_pull_request_number_as_404(
    make_api: ApiFactory, github_pull_request: dict[str, Any]
) -> None:
    api = make_api(responder(200, github_pull_request))
    response = api.get("/issues/43")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_missing_issue_is_a_404(make_api: ApiFactory) -> None:
    api = make_api(responder(404, {"message": "Not Found"}))
    assert api.get("/issues/999").status_code == 404


@pytest.mark.parametrize("number", ["0", "-1", "abc"])
def test_invalid_issue_numbers_are_rejected_with_400(make_api: ApiFactory, number: str) -> None:
    api = make_api(responder(200, {}))
    assert api.get(f"/issues/{number}").status_code == 400


# --------------------------------------------------------- PATCH /issues/{number}


def test_patch_closes_an_issue(make_api: ApiFactory, github_issue: dict[str, Any]) -> None:
    closed = {**github_issue, "state": "closed", "closed_at": "2026-09-03T08:00:00Z"}
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["content"] = request.content
        return httpx.Response(200, json=closed)

    api = make_api(handler)
    response = api.patch("/issues/42", json={"state": "closed"})

    assert seen["method"] == "PATCH"
    assert seen["content"] == b'{"state":"closed"}'
    assert response.status_code == 200
    assert response.json()["state"] == "closed"


def test_patch_sends_only_the_supplied_fields(
    make_api: ApiFactory, github_issue: dict[str, Any]
) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content"] = request.content
        return httpx.Response(200, json=github_issue)

    api = make_api(handler)
    api.patch("/issues/42", json={"title": "Renamed"})

    assert seen["content"] == b'{"title":"Renamed"}'


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="no-fields"),
        pytest.param({"state": "reopened"}, id="invalid-state"),
        pytest.param({"title": "  "}, id="whitespace-title"),
        pytest.param({"assignee": "someone"}, id="unknown-field"),
    ],
)
def test_invalid_patch_payloads_are_rejected_with_400(
    make_api: ApiFactory, github_issue: dict[str, Any], payload: dict[str, Any]
) -> None:
    api = make_api(responder(200, github_issue))
    response = api.patch("/issues/42", json=payload)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_patching_a_missing_issue_is_a_404(make_api: ApiFactory) -> None:
    api = make_api(responder(404, {"message": "Not Found"}))
    assert api.patch("/issues/999", json={"state": "closed"}).status_code == 404


# -------------------------------------------------------------------- comments


def test_create_comment_returns_201_with_location(
    make_api: ApiFactory, github_comment: dict[str, Any]
) -> None:
    api = make_api(responder(201, github_comment))
    response = api.post("/issues/42/comments", json={"body": "Reproduced on Safari 17."})

    assert response.status_code == 201
    assert response.headers["location"] == "/issues/42/comments"
    body = response.json()
    assert body["id"] == 3100000001
    assert body["user"] == "test-owner"
    assert body["issue_number"] == 42


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="missing-body"),
        pytest.param({"body": ""}, id="empty-body"),
        pytest.param({"body": "   "}, id="whitespace-body"),
    ],
)
def test_invalid_comment_payloads_are_rejected_with_400(
    make_api: ApiFactory, github_comment: dict[str, Any], payload: dict[str, Any]
) -> None:
    api = make_api(responder(201, github_comment))
    response = api.post("/issues/42/comments", json=payload)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_list_comments_returns_projected_comments(
    make_api: ApiFactory, github_comment: dict[str, Any]
) -> None:
    link = '<https://api.github.com/repos/o/r/issues/42/comments?page=2>; rel="next"'
    api = make_api(responder(200, [github_comment], {"link": link}))
    response = api.get("/issues/42/comments")

    assert response.status_code == 200
    assert response.json()[0]["body"] == "Reproduced on Safari 17."
    assert response.headers["link"] == '</issues/42/comments?page=2>; rel="next"'


def test_patching_a_pull_request_number_is_a_404(
    make_api: ApiFactory, github_pull_request: dict[str, Any]
) -> None:
    """GitHub happily patches a PR through the issues endpoint; this service will not."""
    api = make_api(responder(200, github_pull_request))
    response = api.patch("/issues/43", json={"state": "closed"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


# ------------------------------------------------------------- ETag caching


def test_a_repeat_request_replays_the_cached_etag_upstream(
    make_api: ApiFactory, github_issue: dict[str, Any]
) -> None:
    """The service sends If-None-Match even when the caller did not."""
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("if-none-match"))
        if len(seen) == 1:
            return httpx.Response(200, json=[github_issue], headers={"etag": 'W/"v1"'})
        return httpx.Response(304, headers={"etag": 'W/"v1"'})

    api = make_api(handler)
    first = api.get("/issues")
    second = api.get("/issues")

    assert seen == [None, 'W/"v1"']
    assert first.headers["x-cache"] == "MISS"
    assert second.headers["x-cache"] == "HIT"


def test_a_cache_hit_still_returns_a_full_200_body(
    make_api: ApiFactory, github_issue: dict[str, Any]
) -> None:
    """A 304 from GitHub is invisible to the caller: they get the body, for free."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(200, json=[github_issue], headers={"etag": 'W/"v1"'})
        return httpx.Response(304, headers={"etag": 'W/"v1"'})

    api = make_api(handler)
    first = api.get("/issues")
    second = api.get("/issues")

    assert second.status_code == 200
    assert second.json() == first.json()
    assert second.headers["etag"] == 'W/"v1"'


def test_a_changed_upstream_response_refreshes_the_cache(
    make_api: ApiFactory, github_issue: dict[str, Any]
) -> None:
    renamed = {**github_issue, "title": "Renamed upstream"}
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(200, json=[github_issue], headers={"etag": 'W/"v1"'})
        return httpx.Response(200, json=[renamed], headers={"etag": 'W/"v2"'})

    api = make_api(handler)
    api.get("/issues")
    second = api.get("/issues")

    assert second.headers["x-cache"] == "MISS"
    assert second.json()[0]["title"] == "Renamed upstream"
    assert second.headers["etag"] == 'W/"v2"'


def test_different_queries_do_not_share_a_cache_entry(
    make_api: ApiFactory, github_issue: dict[str, Any]
) -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("if-none-match"))
        return httpx.Response(200, json=[github_issue], headers={"etag": 'W/"v1"'})

    api = make_api(handler)
    api.get("/issues", params={"state": "open"})
    api.get("/issues", params={"state": "closed"})

    assert seen == [None, None], "a different query must not reuse another query's ETag"


def test_a_client_validator_takes_precedence_over_the_cached_one(
    make_api: ApiFactory, github_issue: dict[str, Any]
) -> None:
    """HTTP requires honouring the caller's If-None-Match; the cache is secondary."""
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("if-none-match"))
        if len(seen) == 1:
            return httpx.Response(200, json=[github_issue], headers={"etag": 'W/"v1"'})
        return httpx.Response(304, headers={"etag": 'W/"client"'})

    api = make_api(handler)
    api.get("/issues")
    conditional = api.get("/issues", headers={"If-None-Match": 'W/"client"'})

    assert seen[1] == 'W/"client"'
    assert conditional.status_code == 304
    assert not conditional.content


def test_a_response_without_an_etag_is_not_cached(
    make_api: ApiFactory, github_issue: dict[str, Any]
) -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("if-none-match"))
        return httpx.Response(200, json=[github_issue])

    api = make_api(handler)
    api.get("/issues")
    second = api.get("/issues")

    assert seen == [None, None]
    assert second.headers["x-cache"] == "MISS"
