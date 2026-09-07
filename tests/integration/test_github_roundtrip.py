# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran
# and Ameya Mathew, who specified the requirements and design and reviewed the
# implementation.
# See the "Credits and authorship" section of README.md.
"""End-to-end tests against a real GitHub repository.

Each test drives this service's own HTTP API, so a pass proves the whole chain:
request validation, the GitHub client, error mapping, and response projection.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import RUN_MARKER

#: GitHub indexes a new label asynchronously; this is generous headroom.
LABEL_INDEX_TIMEOUT_SECONDS = 30.0


def test_create_then_fetch_the_issue(
    api: TestClient, unique_title: str, created_issues: list[int]
) -> None:
    """Requirement 1: create an issue, then read it back."""
    created = api.post(
        "/issues",
        json={"title": unique_title, "body": RUN_MARKER, "labels": []},
    )
    assert created.status_code == 201, created.text

    issue = created.json()
    created_issues.append(issue["number"])

    assert created.headers["location"] == f"/issues/{issue['number']}"
    assert issue["title"] == unique_title
    assert issue["state"] == "open"

    fetched = api.get(f"/issues/{issue['number']}")
    assert fetched.status_code == 200
    assert fetched.json()["number"] == issue["number"]
    assert fetched.json()["title"] == unique_title


def test_update_title_and_body_then_close_and_reopen(
    api: TestClient, unique_title: str, created_issues: list[int]
) -> None:
    """Requirement 2: edit the issue, then close it and open it again."""
    number = api.post("/issues", json={"title": unique_title, "body": RUN_MARKER}).json()["number"]
    created_issues.append(number)

    renamed = api.patch(
        f"/issues/{number}",
        json={"title": f"{unique_title} (renamed)", "body": f"{RUN_MARKER} Edited."},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == f"{unique_title} (renamed)"
    assert renamed.json()["body"].endswith("Edited.")

    closed = api.patch(f"/issues/{number}", json={"state": "closed"})
    assert closed.status_code == 200
    assert closed.json()["state"] == "closed"
    assert closed.json()["closed_at"] is not None

    reopened = api.patch(f"/issues/{number}", json={"state": "open"})
    assert reopened.status_code == 200
    assert reopened.json()["state"] == "open"
    assert reopened.json()["closed_at"] is None


def test_create_a_comment_and_read_the_comment_list(
    api: TestClient, unique_title: str, created_issues: list[int]
) -> None:
    """Requirement 3: comment on an issue, then list its comments."""
    number = api.post("/issues", json={"title": unique_title, "body": RUN_MARKER}).json()["number"]
    created_issues.append(number)

    body = "Reproduced by the integration suite."
    created = api.post(f"/issues/{number}/comments", json={"body": body})
    assert created.status_code == 201, created.text

    comment = created.json()
    assert comment["body"] == body
    assert comment["issue_number"] == number
    assert created.headers["location"] == f"/issues/{number}/comments"

    listed = api.get(f"/issues/{number}/comments")
    assert listed.status_code == 200
    assert comment["id"] in [item["id"] for item in listed.json()]


def test_labels_survive_the_round_trip(
    api: TestClient, unique_title: str, created_issues: list[int]
) -> None:
    """`bug` exists in every new GitHub repository, so it needs no setup."""
    created = api.post(
        "/issues", json={"title": unique_title, "body": RUN_MARKER, "labels": ["bug"]}
    )
    assert created.status_code == 201, created.text
    number = created.json()["number"]
    created_issues.append(number)

    assert created.json()["labels"] == ["bug"]

    # Reading the issue back is immediately consistent, unlike the label filter.
    fetched = api.get(f"/issues/{number}")
    assert fetched.status_code == 200
    assert fetched.json()["labels"] == ["bug"]


def test_filtering_by_label_returns_the_labelled_issue(
    api: TestClient, unique_title: str, created_issues: list[int]
) -> None:
    """GitHub's label index is eventually consistent, so this polls rather than
    asserting immediately after creation — the filter is correct, it is just not
    instantaneous."""
    created = api.post(
        "/issues", json={"title": unique_title, "body": RUN_MARKER, "labels": ["bug"]}
    )
    assert created.status_code == 201, created.text
    number = created.json()["number"]
    created_issues.append(number)

    deadline = time.monotonic() + LABEL_INDEX_TIMEOUT_SECONDS
    seen: list[int] = []
    while time.monotonic() < deadline:
        # A fresh per_page avoids the response cache returning an earlier page.
        filtered = api.get(
            "/issues",
            params={"labels": "bug", "state": "open", "per_page": 100},
            headers={"Cache-Control": "no-cache"},
        )
        assert filtered.status_code == 200
        seen = [issue["number"] for issue in filtered.json()]
        if number in seen:
            return
        time.sleep(1.0)

    pytest.fail(
        f"issue #{number} never appeared in the label-filtered list within "
        f"{LABEL_INDEX_TIMEOUT_SECONDS:.0f}s; last saw {seen}"
    )


def test_listing_honours_state_and_pagination(
    api: TestClient, unique_title: str, created_issues: list[int]
) -> None:
    number = api.post("/issues", json={"title": unique_title, "body": RUN_MARKER}).json()["number"]
    created_issues.append(number)

    page = api.get("/issues", params={"state": "open", "per_page": 1, "page": 1})
    assert page.status_code == 200
    assert len(page.json()) <= 1
    assert page.headers["x-page"] == "1"
    assert page.headers["x-per-page"] == "1"

    # A Link header only appears when more than one page exists upstream.
    if "link" in page.headers:
        assert "api.github.com" not in page.headers["link"]
        assert page.headers["link"].startswith("</issues?")

    closed_only = api.get("/issues", params={"state": "closed", "per_page": 100})
    assert closed_only.status_code == 200
    assert all(issue["state"] == "closed" for issue in closed_only.json())


def test_conditional_get_returns_304_and_saves_quota(api: TestClient) -> None:
    """Extra credit: a repeated list with If-None-Match costs no rate-limit quota."""
    first = api.get("/issues", params={"per_page": 5})
    assert first.status_code == 200
    etag = first.headers.get("etag")
    assert etag, "GitHub did not return an ETag for the list request"

    second = api.get("/issues", params={"per_page": 5}, headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert not second.content


def test_a_missing_issue_is_reported_as_404(api: TestClient) -> None:
    response = api.get("/issues/99999999")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_validation_is_enforced_before_reaching_github(api: TestClient) -> None:
    """A bad payload must fail locally — no upstream call, no wasted quota."""
    response = api.post("/issues", json={"body": "no title"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_healthz_reports_the_configured_repository(api: TestClient) -> None:
    body = api.get("/healthz").json()

    assert body["status"] == "ok"
    assert "/" in body["repo"]
