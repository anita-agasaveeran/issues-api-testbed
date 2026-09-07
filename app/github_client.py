# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran
# and Ameya Mathew, who specified the requirements and design and reviewed the
# implementation.
# See the "Credits and authorship" section of README.md.
"""Thin async client over the GitHub REST API, with one place for error mapping.

Every upstream call funnels through :meth:`GitHubClient._request`, so the
translation from GitHub status codes to this service's error model — and the
rate-limit accounting that goes with it — is written exactly once.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

import httpx

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
from app.logging import get_logger

log = get_logger("github")

RATE_LIMIT_REMAINING_HEADER = "x-ratelimit-remaining"
RATE_LIMIT_RESET_HEADER = "x-ratelimit-reset"
RETRY_AFTER_HEADER = "retry-after"

# Upstream statuses we translate deliberately; anything else falls through to a
# generic mapping keyed on the status class.
_NOT_FOUND_MESSAGE = "The requested issue or repository does not exist, or the token cannot see it."


@dataclass(frozen=True, slots=True)
class GitHubResult:
    """A successful upstream response, reduced to what the routes actually need."""

    status_code: int
    data: Any
    headers: httpx.Headers

    @property
    def etag(self) -> str | None:
        value: str | None = self.headers.get("etag")
        return value

    @property
    def link(self) -> str | None:
        value: str | None = self.headers.get("link")
        return value

    @property
    def not_modified(self) -> bool:
        return self.status_code == 304


class GitHubClient:
    """Async GitHub API client scoped to a single repository."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(
            base_url=settings.github_api_url,
            timeout=settings.request_timeout_seconds,
        )

    @property
    def _repo_path(self) -> str:
        return f"/repos/{self._settings.github_owner}/{self._settings.github_repo}"

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self._settings.github_api_version,
            "Authorization": f"Bearer {self._settings.github_token.get_secret_value()}",
            "User-Agent": "issues-api-testbed",
        }
        if extra:
            headers.update(extra)
        return headers

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    # ---------------------------------------------------------------- requests

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> GitHubResult:
        started = time.monotonic()
        try:
            response = await self._client.request(
                method,
                path,
                json=json,
                params=params,
                headers=self._headers(headers),
            )
        except httpx.TimeoutException as exc:
            log.warning("github_timeout", method=method, path=path)
            raise UpstreamUnavailableError(
                "Timed out talking to the GitHub API. Please retry.",
                details={"upstream": "timeout"},
            ) from exc
        except httpx.HTTPError as exc:
            log.warning(
                "github_transport_error",
                method=method,
                path=path,
                error=type(exc).__name__,
            )
            raise UpstreamUnavailableError(
                "Could not reach the GitHub API.",
                details={"upstream": type(exc).__name__},
            ) from exc

        log.info(
            "github_call",
            method=method,
            path=path,
            status_code=response.status_code,
            duration_ms=round((time.monotonic() - started) * 1000, 1),
            rate_limit_remaining=response.headers.get(RATE_LIMIT_REMAINING_HEADER),
        )

        if response.status_code >= 400:
            raise self._map_error(response)

        return GitHubResult(
            status_code=response.status_code,
            data=self._decode(response),
            headers=response.headers,
        )

    @staticmethod
    def _decode(response: httpx.Response) -> Any:
        if response.status_code in (204, 304) or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    # ------------------------------------------------------------ error mapping

    def _map_error(self, response: httpx.Response) -> AppError:
        """Translate an upstream failure into this service's error model."""
        status = response.status_code
        payload = self._error_payload(response)
        upstream_message = payload.get("message") or response.reason_phrase
        details: dict[str, Any] = {"upstream_status": status, "upstream_message": upstream_message}
        if payload.get("errors"):
            details["upstream_errors"] = payload["errors"]

        if status == 401:
            return UnauthorizedError(
                "GitHub rejected the configured token. Check GITHUB_TOKEN is set, unexpired, "
                "and has Issues: read & write on this repository.",
                details=details,
            )

        if status in (403, 429) and self._is_rate_limited(response):
            retry_after = self._retry_after_seconds(response)
            details["retry_after_seconds"] = retry_after
            return RateLimitedError(
                f"GitHub API rate limit exceeded. Retry in {retry_after}s.",
                details=details,
                headers={"Retry-After": str(retry_after)},
            )

        if status == 403:
            return ForbiddenError(
                "GitHub refused this request. The token is valid but lacks permission for it.",
                details=details,
            )

        if status == 404:
            return NotFoundError(_NOT_FOUND_MESSAGE, details=details)

        if status in (410, 422):
            return ValidationError(
                "GitHub rejected the request payload as invalid.",
                details=details,
            )

        if status >= 500:
            return UpstreamUnavailableError(
                "GitHub returned a server error. This is upstream, not your request.",
                details=details,
            )

        return AppError(
            f"Unexpected response from GitHub ({status}).",
            status_code=502,
            code="upstream_error",
            details=details,
        )

    @staticmethod
    def _error_payload(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _is_rate_limited(response: httpx.Response) -> bool:
        """True for both primary (quota exhausted) and secondary (abuse) limits."""
        if response.status_code == 429:
            return True
        remaining = response.headers.get(RATE_LIMIT_REMAINING_HEADER)
        if remaining is not None and remaining.strip() == "0":
            return True
        return RETRY_AFTER_HEADER in response.headers

    @staticmethod
    def _retry_after_seconds(response: httpx.Response, *, fallback: int = 60) -> int:
        """Seconds to wait, preferring Retry-After and falling back to the reset epoch."""
        raw_retry_after = response.headers.get(RETRY_AFTER_HEADER)
        if raw_retry_after:
            try:
                return max(1, int(float(raw_retry_after)))
            except ValueError:
                pass

        raw_reset = response.headers.get(RATE_LIMIT_RESET_HEADER)
        if raw_reset:
            try:
                return max(1, int(float(raw_reset) - time.time()))
            except ValueError:
                pass
        return fallback

    # ------------------------------------------------------------------ issues

    async def create_issue(self, payload: dict[str, Any]) -> GitHubResult:
        return await self._request("POST", f"{self._repo_path}/issues", json=payload)

    async def list_issues(
        self,
        *,
        params: dict[str, Any],
        etag: str | None = None,
    ) -> GitHubResult:
        headers = {"If-None-Match": etag} if etag else None
        return await self._request(
            "GET", f"{self._repo_path}/issues", params=params, headers=headers
        )

    async def get_issue(self, number: int) -> GitHubResult:
        return await self._request("GET", f"{self._repo_path}/issues/{number}")

    async def update_issue(self, number: int, payload: dict[str, Any]) -> GitHubResult:
        return await self._request("PATCH", f"{self._repo_path}/issues/{number}", json=payload)

    # ---------------------------------------------------------------- comments

    async def create_comment(self, number: int, body: str) -> GitHubResult:
        return await self._request(
            "POST", f"{self._repo_path}/issues/{number}/comments", json={"body": body}
        )

    async def list_comments(self, number: int, *, params: dict[str, Any]) -> GitHubResult:
        return await self._request(
            "GET", f"{self._repo_path}/issues/{number}/comments", params=params
        )
