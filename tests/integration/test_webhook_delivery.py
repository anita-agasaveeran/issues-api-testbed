# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran,
# who specified the requirements and design and reviewed the implementation.
# See the "Credits and authorship" section of README.md.
"""End-to-end webhook test: a real GitHub delivery, over a public tunnel.

Unlike the rest of the suite, these tests do not build the app in-process. A
webhook arrives at a *running* service through a tunnel, so the assertions have to
be made against that same running process — an in-process app would have its own,
empty event store.

Set ``SERVICE_BASE_URL`` to the locally running service (for example
``http://127.0.0.1:8000``) once the tunnel and repository webhook are configured;
without it these tests are skipped.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

SERVICE_BASE_URL = os.environ.get("SERVICE_BASE_URL", "").rstrip("/")

#: GitHub usually delivers within a second or two; this is generous headroom for a
#: cold tunnel without making a genuine failure slow to surface.
DELIVERY_TIMEOUT_SECONDS = 45.0
POLL_INTERVAL_SECONDS = 1.0

pytestmark = [
    pytest.mark.skipif(
        not SERVICE_BASE_URL,
        reason=(
            "set SERVICE_BASE_URL to a running instance reachable by the repository "
            "webhook (see the webhook setup steps in the README)"
        ),
    ),
]


@pytest.fixture(scope="module")
def service() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=SERVICE_BASE_URL, timeout=15.0) as client:
        health = client.get("/healthz")
        assert health.status_code == 200, (
            f"no service answering at {SERVICE_BASE_URL} — start it before running these tests"
        )
        yield client


def wait_for_event(
    service: httpx.Client,
    *,
    event: str,
    issue_number: int,
    action: str | None = None,
) -> dict[str, Any]:
    """Poll ``GET /events`` until a matching delivery is recorded, or fail."""
    deadline = time.monotonic() + DELIVERY_TIMEOUT_SECONDS
    seen: list[dict[str, Any]] = []

    while time.monotonic() < deadline:
        response = service.get("/events", params={"limit": 100})
        response.raise_for_status()
        seen = response.json()
        for record in seen:
            matches_action = action is None or record["action"] == action
            if (
                record["event"] == event
                and record["issue_number"] == issue_number
                and matches_action
            ):
                return record
        time.sleep(POLL_INTERVAL_SECONDS)

    pytest.fail(
        f"no '{event}' delivery for issue #{issue_number} within "
        f"{DELIVERY_TIMEOUT_SECONDS:.0f}s. Check the tunnel is up, the repository "
        f"webhook points at it, and WEBHOOK_SECRET matches. Recorded events: {seen}"
    )


@pytest.fixture
def issue_number(service: httpx.Client) -> Iterator[int]:
    """Create a real issue through the running service, and close it afterwards."""
    title = f"[webhook-e2e] {uuid.uuid4().hex[:12]}"
    created = service.post(
        "/issues",
        json={"title": title, "body": "Created by the webhook end-to-end test."},
    )
    assert created.status_code == 201, created.text
    number: int = created.json()["number"]

    yield number

    service.patch(f"/issues/{number}", json={"state": "closed"})


def test_creating_an_issue_delivers_an_issues_event(
    service: httpx.Client, issue_number: int
) -> None:
    """Creating an issue triggers a real, signed delivery that the service records."""
    record = wait_for_event(service, event="issues", issue_number=issue_number, action="opened")

    assert record["id"], "delivery id was not stored"
    assert record["timestamp"]


def test_commenting_delivers_an_issue_comment_event(
    service: httpx.Client, issue_number: int
) -> None:
    wait_for_event(service, event="issues", issue_number=issue_number, action="opened")

    commented = service.post(
        f"/issues/{issue_number}/comments",
        json={"body": "Comment from the webhook end-to-end test."},
    )
    assert commented.status_code == 201, commented.text

    record = wait_for_event(
        service, event="issue_comment", issue_number=issue_number, action="created"
    )
    assert record["action"] == "created"


def test_closing_an_issue_delivers_a_closed_action(
    service: httpx.Client, issue_number: int
) -> None:
    wait_for_event(service, event="issues", issue_number=issue_number, action="opened")

    closed = service.patch(f"/issues/{issue_number}", json={"state": "closed"})
    assert closed.status_code == 200

    wait_for_event(service, event="issues", issue_number=issue_number, action="closed")


def test_recorded_deliveries_have_unique_ids(service: httpx.Client) -> None:
    """Idempotency, observed from outside: no delivery id appears twice.

    Redeliver an event from the repository's webhook settings and re-run this test
    to confirm the redelivery did not create a second row.
    """
    events = service.get("/events", params={"limit": 200}).json()
    keys = [(record["id"], record["action"]) for record in events]

    assert len(keys) == len(set(keys)), "the same delivery was stored more than once"
