# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran
# and Ameya Mathew, who specified the requirements and design and reviewed the
# implementation.
# See the "Credits and authorship" section of README.md.
"""Unit tests for the GitHub client, with every upstream call mocked by respx."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from app.config import Settings
from app.errors import (
    AppError,
    ForbiddenError,
    NotFoundError,
    RateLimitedError,
    UnauthorizedError,
    UpstreamUnavailableError,
    ValidationError,
)
from app.github_client import GitHubClient

ISSUES_URL = "https://api.github.com/repos/test-owner/test-repo/issues"


@pytest.fixture
async def gh(settings: Settings) -> AsyncIterator[GitHubClient]:
    transport_client = httpx.AsyncClient(base_url=settings.github_api_url, timeout=1.0)
    client = GitHubClient(settings, transport_client)
    yield client
    await client.aclose()


# ------------------------------------------------------------------ happy path


async def test_create_issue_returns_status_data_and_headers(gh: GitHubClient) -> None:
    with respx.mock:
        respx.post(ISSUES_URL).mock(
            return_value=httpx.Response(201, json={"number": 7}, headers={"etag": 'W/"abc"'})
        )
        result = await gh.create_issue({"title": "hello"})

    assert result.status_code == 201
    assert result.data == {"number": 7}
    assert result.etag == 'W/"abc"'


async def test_required_github_headers_are_sent(gh: GitHubClient) -> None:
    with respx.mock:
        route = respx.get(ISSUES_URL).mock(return_value=httpx.Response(200, json=[]))
        await gh.list_issues(params={"state": "open"})

    sent = route.calls.last.request.headers
    assert sent["accept"] == "application/vnd.github+json"
    assert sent["x-github-api-version"] == "2022-11-28"
    assert sent["authorization"] == "Bearer test-token"


async def test_query_params_are_forwarded(gh: GitHubClient) -> None:
    with respx.mock:
        route = respx.get(ISSUES_URL).mock(return_value=httpx.Response(200, json=[]))
        await gh.list_issues(params={"state": "closed", "per_page": 50, "page": 2})

    assert route.calls.last.request.url.params["state"] == "closed"
    assert route.calls.last.request.url.params["per_page"] == "50"


async def test_conditional_get_sends_if_none_match_and_reports_304(gh: GitHubClient) -> None:
    with respx.mock:
        route = respx.get(ISSUES_URL).mock(return_value=httpx.Response(304))
        result = await gh.list_issues(params={}, etag='W/"cached"')

    assert route.calls.last.request.headers["if-none-match"] == 'W/"cached"'
    assert result.not_modified is True
    assert result.data is None


async def test_link_header_is_exposed_on_the_result(gh: GitHubClient) -> None:
    link = f'<{ISSUES_URL}?page=2>; rel="next"'
    with respx.mock:
        respx.get(ISSUES_URL).mock(
            return_value=httpx.Response(200, json=[], headers={"link": link})
        )
        result = await gh.list_issues(params={})

    assert result.link == link


async def test_update_and_comment_use_the_right_method_and_path(gh: GitHubClient) -> None:
    with respx.mock:
        patch_route = respx.patch(f"{ISSUES_URL}/12").mock(
            return_value=httpx.Response(200, json={"number": 12})
        )
        comment_route = respx.post(f"{ISSUES_URL}/12/comments").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )
        await gh.update_issue(12, {"state": "closed"})
        await gh.create_comment(12, "looks good")

    assert patch_route.called
    assert comment_route.calls.last.request.content == b'{"body":"looks good"}'


# ----------------------------------------------------------------- error mapping


async def test_401_maps_to_unauthorized_with_actionable_message(gh: GitHubClient) -> None:
    with respx.mock:
        respx.get(ISSUES_URL).mock(
            return_value=httpx.Response(401, json={"message": "Bad credentials"})
        )
        with pytest.raises(UnauthorizedError) as excinfo:
            await gh.list_issues(params={})

    error = excinfo.value
    assert error.status_code == 401
    assert error.code == "unauthorized"
    assert "GITHUB_TOKEN" in error.message
    assert error.details["upstream_message"] == "Bad credentials"


async def test_403_without_rate_limit_signals_maps_to_forbidden(gh: GitHubClient) -> None:
    with respx.mock:
        respx.get(ISSUES_URL).mock(
            return_value=httpx.Response(
                403,
                json={"message": "Resource not accessible by personal access token"},
                headers={"x-ratelimit-remaining": "42"},
            )
        )
        with pytest.raises(ForbiddenError) as excinfo:
            await gh.list_issues(params={})

    assert excinfo.value.status_code == 403
    assert excinfo.value.code == "forbidden"


async def test_403_with_exhausted_quota_maps_to_429_with_retry_after(gh: GitHubClient) -> None:
    reset_at = int(time.time()) + 45
    with respx.mock:
        respx.get(ISSUES_URL).mock(
            return_value=httpx.Response(
                403,
                json={"message": "API rate limit exceeded"},
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset_at)},
            )
        )
        with pytest.raises(RateLimitedError) as excinfo:
            await gh.list_issues(params={})

    error = excinfo.value
    assert error.status_code == 429
    assert error.code == "rate_limited"
    retry_after = int(error.headers["Retry-After"])
    assert 1 <= retry_after <= 45
    assert error.details["retry_after_seconds"] == retry_after


async def test_secondary_rate_limit_prefers_the_retry_after_header(gh: GitHubClient) -> None:
    with respx.mock:
        respx.post(ISSUES_URL).mock(
            return_value=httpx.Response(
                403,
                json={"message": "You have exceeded a secondary rate limit"},
                headers={"retry-after": "17"},
            )
        )
        with pytest.raises(RateLimitedError) as excinfo:
            await gh.create_issue({"title": "x"})

    assert excinfo.value.headers["Retry-After"] == "17"


async def test_429_is_treated_as_rate_limited_even_without_headers(gh: GitHubClient) -> None:
    with respx.mock:
        respx.get(ISSUES_URL).mock(return_value=httpx.Response(429, json={"message": "slow down"}))
        with pytest.raises(RateLimitedError) as excinfo:
            await gh.list_issues(params={})

    assert excinfo.value.headers["Retry-After"] == "60"


async def test_404_maps_to_not_found(gh: GitHubClient) -> None:
    with respx.mock:
        respx.get(f"{ISSUES_URL}/999").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )
        with pytest.raises(NotFoundError) as excinfo:
            await gh.get_issue(999)

    assert excinfo.value.status_code == 404
    assert excinfo.value.code == "not_found"


async def test_422_maps_to_validation_error_and_keeps_upstream_details(gh: GitHubClient) -> None:
    upstream_errors = [{"resource": "Issue", "field": "title", "code": "missing_field"}]
    with respx.mock:
        respx.post(ISSUES_URL).mock(
            return_value=httpx.Response(
                422, json={"message": "Validation Failed", "errors": upstream_errors}
            )
        )
        with pytest.raises(ValidationError) as excinfo:
            await gh.create_issue({"body": "no title"})

    error = excinfo.value
    assert error.status_code == 400
    assert error.code == "validation_error"
    assert error.details["upstream_errors"] == upstream_errors


@pytest.mark.parametrize("status", [500, 502, 503])
async def test_upstream_5xx_maps_to_503(gh: GitHubClient, status: int) -> None:
    with respx.mock:
        respx.get(ISSUES_URL).mock(return_value=httpx.Response(status, text="boom"))
        with pytest.raises(UpstreamUnavailableError) as excinfo:
            await gh.list_issues(params={})

    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "upstream_unavailable"


async def test_unexpected_4xx_falls_back_to_502(gh: GitHubClient) -> None:
    with respx.mock:
        respx.get(ISSUES_URL).mock(return_value=httpx.Response(418, json={"message": "teapot"}))
        with pytest.raises(AppError) as excinfo:
            await gh.list_issues(params={})

    assert excinfo.value.status_code == 502
    assert excinfo.value.code == "upstream_error"


async def test_non_json_error_body_does_not_break_mapping(gh: GitHubClient) -> None:
    with respx.mock:
        respx.get(ISSUES_URL).mock(return_value=httpx.Response(404, text="<html>nope</html>"))
        with pytest.raises(NotFoundError) as excinfo:
            await gh.list_issues(params={})

    assert excinfo.value.details["upstream_status"] == 404


async def test_timeout_maps_to_upstream_unavailable(gh: GitHubClient) -> None:
    with respx.mock:
        respx.get(ISSUES_URL).mock(side_effect=httpx.ReadTimeout("timed out"))
        with pytest.raises(UpstreamUnavailableError) as excinfo:
            await gh.list_issues(params={})

    assert excinfo.value.details == {"upstream": "timeout"}


async def test_connection_error_maps_to_upstream_unavailable(gh: GitHubClient) -> None:
    with respx.mock:
        respx.get(ISSUES_URL).mock(side_effect=httpx.ConnectError("no route"))
        with pytest.raises(UpstreamUnavailableError) as excinfo:
            await gh.list_issues(params={})

    assert excinfo.value.details == {"upstream": "ConnectError"}


async def test_empty_204_body_decodes_to_none(gh: GitHubClient) -> None:
    with respx.mock:
        respx.patch(f"{ISSUES_URL}/3").mock(return_value=httpx.Response(204))
        result = await gh.update_issue(3, {"state": "open"})

    assert result.data is None


async def test_client_works_as_an_async_context_manager(settings: Settings) -> None:
    async with GitHubClient(settings) as client:
        assert client is not None
