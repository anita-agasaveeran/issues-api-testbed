# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran,
# who specified the requirements and design and reviewed the implementation.
# See the "Credits and authorship" section of README.md.
"""Issue and comment routes — the public surface of this service.

Each handler does three things and no more: validate input (Pydantic does the
work), call the GitHub client, and project the result. Error translation lives in
``app.github_client``, so nothing here catches upstream exceptions.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Path, Query, Response, status

from app.cache import CachedList, ResponseCache
from app.config import Settings
from app.dependencies import get_github_client, get_response_cache, get_settings_dep
from app.errors import NotFoundError
from app.github_client import GitHubClient
from app.logging import get_logger
from app.pagination import DEFAULT_PER_PAGE, MAX_PER_PAGE, rewrite_link_header
from app.schemas import (
    Comment,
    CommentCreate,
    ErrorResponse,
    Issue,
    IssueCreate,
    IssuePatch,
    IssueStateFilter,
    is_pull_request,
    to_comment,
    to_issue,
)

log = get_logger("issues")

router = APIRouter(tags=["issues"])

ISSUES_PATH = "/issues"

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Invalid request payload or query parameters"},
    401: {"model": ErrorResponse, "description": "GitHub rejected the configured token"},
    403: {"model": ErrorResponse, "description": "Token lacks permission for this operation"},
    404: {"model": ErrorResponse, "description": "Issue or repository not found"},
    429: {"model": ErrorResponse, "description": "GitHub rate limit exceeded; see Retry-After"},
    503: {
        "model": ErrorResponse,
        "description": "GitHub is unreachable or returned a server error",
    },
}

IssueNumber = Annotated[int, Path(ge=1, description="The issue number within the repository.")]


def _not_found(number: int) -> NotFoundError:
    return NotFoundError(
        f"Issue #{number} does not exist in this repository.",
        details={"issue_number": number},
    )


@router.post(
    ISSUES_PATH,
    status_code=status.HTTP_201_CREATED,
    response_model=Issue,
    responses=ERROR_RESPONSES,
    summary="Create an issue",
)
async def create_issue(
    payload: IssueCreate,
    response: Response,
    github: Annotated[GitHubClient, Depends(get_github_client)],
) -> Issue:
    """Create an issue in the configured repository."""
    result = await github.create_issue(payload.model_dump(exclude_none=True))
    issue = to_issue(result.data)
    response.headers["Location"] = f"{ISSUES_PATH}/{issue.number}"
    log.info("issue_created", issue_number=issue.number)
    return issue


@router.get(
    ISSUES_PATH,
    response_model=list[Issue],
    responses=ERROR_RESPONSES,
    summary="List issues",
)
async def list_issues(
    response: Response,
    github: Annotated[GitHubClient, Depends(get_github_client)],
    cache: Annotated[ResponseCache, Depends(get_response_cache)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    state: Annotated[IssueStateFilter, Query(description="Filter by issue state.")] = (
        IssueStateFilter.OPEN
    ),
    labels: Annotated[
        str | None, Query(description="Comma-separated label names, e.g. `bug,ops`.")
    ] = None,
    page: Annotated[int, Query(ge=1, description="1-based page number.")] = 1,
    per_page: Annotated[
        int, Query(ge=1, le=MAX_PER_PAGE, description="Results per page (max 100).")
    ] = DEFAULT_PER_PAGE,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Any:
    """List issues, preserving GitHub's pagination semantics.

    Pull requests are filtered out: GitHub's issues endpoint returns them, but this
    service is about issues only.

    Two layers of conditional GET are at work. A caller's own `If-None-Match` is
    forwarded upstream and answered with `304`. Absent that, the service replays the
    ETag it cached for this exact query, and on `304` serves the cached body as a
    normal `200` — the caller sees no difference, but the request cost no GitHub
    rate-limit quota. `X-Cache` reports which path was taken.
    """
    params: dict[str, Any] = {"state": state.value, "page": page, "per_page": per_page}
    if labels:
        params["labels"] = labels

    key = (state.value, labels or "", page, per_page)
    cached = cache.get(key) if settings.cache_enabled else None

    # A caller's own validator always wins: honouring it is required by HTTP,
    # whereas the cached ETag is only an optimisation.
    conditional_etag = if_none_match or (cached.etag if cached else None)

    result = await github.list_issues(params=params, etag=conditional_etag)

    if result.not_modified and if_none_match and conditional_etag == if_none_match:
        # The caller's copy is current: no body, and nothing to cache.
        headers = {"ETag": if_none_match}
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

    if result.not_modified and cached is not None:
        entry = cached
        cache.record_hit()
        response.headers["X-Cache"] = "HIT"
        log.info("issues_served_from_cache", state=state.value, page=page)
    else:
        entry = CachedList(
            etag=result.etag or "",
            data=list(result.data or []),
            link=result.link,
        )
        cache.record_miss()
        response.headers["X-Cache"] = "MISS"
        if settings.cache_enabled and entry.etag:
            cache.set(key, entry)

    if entry.etag:
        response.headers["ETag"] = entry.etag

    forwarded_link = rewrite_link_header(entry.link, ISSUES_PATH)
    if forwarded_link:
        response.headers["Link"] = forwarded_link
    response.headers["X-Page"] = str(page)
    response.headers["X-Per-Page"] = str(per_page)

    items = [item for item in entry.data if not is_pull_request(item)]
    return [to_issue(item) for item in items]


@router.get(
    ISSUES_PATH + "/{number}",
    response_model=Issue,
    responses=ERROR_RESPONSES,
    summary="Get a single issue",
)
async def get_issue(
    number: IssueNumber,
    github: Annotated[GitHubClient, Depends(get_github_client)],
) -> Issue:
    """Return one issue. A pull-request number is reported as 404, not returned."""
    result = await github.get_issue(number)
    if is_pull_request(result.data):
        raise _not_found(number)
    return to_issue(result.data)


@router.patch(
    ISSUES_PATH + "/{number}",
    response_model=Issue,
    responses=ERROR_RESPONSES,
    summary="Update an issue",
)
async def update_issue(
    number: IssueNumber,
    payload: IssuePatch,
    github: Annotated[GitHubClient, Depends(get_github_client)],
) -> Issue:
    """Rename, edit, close, or reopen an issue."""
    result = await github.update_issue(number, payload.to_github_payload())
    if is_pull_request(result.data):
        raise _not_found(number)
    issue = to_issue(result.data)
    log.info("issue_updated", issue_number=issue.number, new_state=issue.state)
    return issue


@router.post(
    ISSUES_PATH + "/{number}/comments",
    status_code=status.HTTP_201_CREATED,
    response_model=Comment,
    responses=ERROR_RESPONSES,
    summary="Comment on an issue",
)
async def create_comment(
    number: IssueNumber,
    payload: CommentCreate,
    response: Response,
    github: Annotated[GitHubClient, Depends(get_github_client)],
) -> Comment:
    """Add a comment to an existing issue."""
    result = await github.create_comment(number, payload.body)
    comment = to_comment(result.data, issue_number=number)
    response.headers["Location"] = f"{ISSUES_PATH}/{number}/comments"
    log.info("comment_created", issue_number=number, comment_id=comment.id)
    return comment


@router.get(
    ISSUES_PATH + "/{number}/comments",
    response_model=list[Comment],
    responses=ERROR_RESPONSES,
    summary="List an issue's comments",
)
async def list_comments(
    number: IssueNumber,
    response: Response,
    github: Annotated[GitHubClient, Depends(get_github_client)],
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=MAX_PER_PAGE)] = DEFAULT_PER_PAGE,
) -> list[Comment]:
    """List comments on an issue, with the same pagination semantics as `/issues`."""
    result = await github.list_comments(number, params={"page": page, "per_page": per_page})

    forwarded_link = rewrite_link_header(result.link, f"{ISSUES_PATH}/{number}/comments")
    if forwarded_link:
        response.headers["Link"] = forwarded_link
    response.headers["X-Page"] = str(page)
    response.headers["X-Per-Page"] = str(per_page)

    return [to_comment(item, issue_number=number) for item in (result.data or [])]
