# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran,
# who specified the requirements and design and reviewed the implementation.
# See the "Credits and authorship" section of README.md.
"""Route-level tests for POST /webhook and GET /events."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.conftest import ApiFactory

SECRET = "test-secret"  # noqa: S105


def unused_github(request: httpx.Request) -> httpx.Response:
    """The webhook path must never call GitHub."""
    raise AssertionError(f"unexpected upstream call to {request.url}")


def deliver(
    api: TestClient,
    payload: dict[str, Any],
    *,
    event: str = "issues",
    delivery_id: str = "delivery-1",
    signature: str | None = None,
    body: bytes | None = None,
) -> httpx.Response:
    """POST a delivery, signing the exact bytes that go on the wire."""
    from tests.unit.test_webhooks import sign

    raw = body if body is not None else json.dumps(payload).encode()
    headers = {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery_id,
        "X-Hub-Signature-256": signature if signature is not None else sign(raw, SECRET),
        "Content-Type": "application/json",
    }
    return api.post("/webhook", content=raw, headers=headers)


@pytest.fixture
def api(make_api: ApiFactory) -> TestClient:
    return make_api(unused_github)


# ------------------------------------------------------------------ happy path


def test_valid_issues_delivery_is_acknowledged_with_204(
    api: TestClient, webhook_issues_opened: dict[str, Any]
) -> None:
    response = deliver(api, webhook_issues_opened)

    assert response.status_code == 204
    assert not response.content


def test_delivery_is_visible_through_the_events_endpoint(
    api: TestClient, webhook_issues_opened: dict[str, Any]
) -> None:
    deliver(api, webhook_issues_opened, delivery_id="abc-123")

    events = api.get("/events").json()
    assert len(events) == 1
    assert events[0]["id"] == "abc-123"
    assert events[0]["event"] == "issues"
    assert events[0]["action"] == "opened"
    assert events[0]["issue_number"] == 42
    assert events[0]["timestamp"]


def test_issue_comment_delivery_is_recorded(
    api: TestClient, webhook_issue_comment_created: dict[str, Any]
) -> None:
    assert deliver(api, webhook_issue_comment_created, event="issue_comment").status_code == 204

    event = api.get("/events").json()[0]
    assert event["event"] == "issue_comment"
    assert event["action"] == "created"
    assert event["issue_number"] == 42


def test_ping_is_accepted(api: TestClient, webhook_ping: dict[str, Any]) -> None:
    assert deliver(api, webhook_ping, event="ping").status_code == 204

    event = api.get("/events").json()[0]
    assert event["event"] == "ping"
    assert event["action"] is None


# ----------------------------------------------------------------- idempotency


def test_redelivery_of_the_same_id_is_acknowledged_but_stored_once(
    api: TestClient, webhook_issues_opened: dict[str, Any]
) -> None:
    first = deliver(api, webhook_issues_opened, delivery_id="dup-1")
    second = deliver(api, webhook_issues_opened, delivery_id="dup-1")

    assert (first.status_code, second.status_code) == (204, 204)
    assert len(api.get("/events").json()) == 1


def test_distinct_deliveries_are_all_recorded(
    api: TestClient, webhook_issues_opened: dict[str, Any]
) -> None:
    deliver(api, webhook_issues_opened, delivery_id="one")
    deliver(api, {**webhook_issues_opened, "action": "closed"}, delivery_id="two")

    events = api.get("/events").json()
    assert {event["id"] for event in events} == {"one", "two"}


# -------------------------------------------------------------------- security


def test_invalid_signature_is_rejected_with_401(
    api: TestClient, webhook_issues_opened: dict[str, Any]
) -> None:
    response = deliver(api, webhook_issues_opened, signature="sha256=" + "0" * 64)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_missing_signature_is_rejected_with_401(
    api: TestClient, webhook_issues_opened: dict[str, Any]
) -> None:
    raw = json.dumps(webhook_issues_opened).encode()
    response = api.post(
        "/webhook",
        content=raw,
        headers={"X-GitHub-Event": "issues", "X-GitHub-Delivery": "d-1"},
    )
    assert response.status_code == 401


def test_tampered_body_is_rejected_with_401(
    api: TestClient, webhook_issues_opened: dict[str, Any]
) -> None:
    from tests.unit.test_webhooks import sign

    original = json.dumps(webhook_issues_opened).encode()
    tampered = json.dumps({**webhook_issues_opened, "action": "closed"}).encode()

    response = deliver(api, {}, signature=sign(original, SECRET), body=tampered)
    assert response.status_code == 401


def test_a_rejected_delivery_is_not_stored(
    api: TestClient, webhook_issues_opened: dict[str, Any]
) -> None:
    deliver(api, webhook_issues_opened, signature="sha256=" + "0" * 64)
    assert api.get("/events").json() == []


def test_signature_is_checked_before_the_event_type(
    api: TestClient, webhook_issues_opened: dict[str, Any]
) -> None:
    """An unauthenticated caller must not learn which events are supported."""
    response = deliver(api, webhook_issues_opened, event="push", signature="sha256=" + "0" * 64)
    assert response.status_code == 401


def test_error_responses_never_echo_the_signature(
    api: TestClient, webhook_issues_opened: dict[str, Any]
) -> None:
    forged = "sha256=" + "a1b2c3" * 10
    response = deliver(api, webhook_issues_opened, signature=forged)

    assert forged not in response.text
    assert "test-secret" not in response.text


# -------------------------------------------------------------- client errors


def test_unknown_event_is_rejected_with_400(
    api: TestClient, webhook_issues_opened: dict[str, Any]
) -> None:
    response = deliver(api, webhook_issues_opened, event="push")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert "push" in response.json()["error"]["message"]


def test_missing_event_header_is_rejected_with_400(
    api: TestClient, webhook_issues_opened: dict[str, Any]
) -> None:
    from tests.unit.test_webhooks import sign

    raw = json.dumps(webhook_issues_opened).encode()
    response = api.post(
        "/webhook",
        content=raw,
        headers={"X-GitHub-Delivery": "d-1", "X-Hub-Signature-256": sign(raw, SECRET)},
    )
    assert response.status_code == 400


def test_missing_delivery_header_is_rejected_with_400(
    api: TestClient, webhook_issues_opened: dict[str, Any]
) -> None:
    from tests.unit.test_webhooks import sign

    raw = json.dumps(webhook_issues_opened).encode()
    response = api.post(
        "/webhook",
        content=raw,
        headers={"X-GitHub-Event": "issues", "X-Hub-Signature-256": sign(raw, SECRET)},
    )
    assert response.status_code == 400
    assert response.json()["error"]["details"]["reason"] == "delivery_header_absent"


def test_missing_action_is_rejected_with_400(api: TestClient) -> None:
    response = deliver(api, {"issue": {"number": 1}})

    assert response.status_code == 400
    assert response.json()["error"]["details"]["reason"] == "action_missing"


def test_invalid_json_is_rejected_with_400(api: TestClient) -> None:
    response = deliver(api, {}, body=b"{not json")

    assert response.status_code == 400
    assert response.json()["error"]["details"]["reason"] == "invalid_json"


def test_non_object_json_is_rejected_with_400(api: TestClient) -> None:
    response = deliver(api, {}, body=b"[1, 2, 3]")

    assert response.status_code == 400
    assert response.json()["error"]["details"]["reason"] == "payload_not_an_object"


# --------------------------------------------------------------- GET /events


def test_events_are_returned_newest_first(
    api: TestClient, webhook_issues_opened: dict[str, Any]
) -> None:
    for index in range(3):
        deliver(api, webhook_issues_opened, delivery_id=f"d-{index}")

    ids = [event["id"] for event in api.get("/events").json()]
    assert ids == ["d-2", "d-1", "d-0"]


def test_events_honours_the_limit_parameter(
    api: TestClient, webhook_issues_opened: dict[str, Any]
) -> None:
    for index in range(5):
        deliver(api, webhook_issues_opened, delivery_id=f"d-{index}")

    assert len(api.get("/events", params={"limit": 2}).json()) == 2


@pytest.mark.parametrize("limit", [0, -1, 501, "abc"])
def test_invalid_limits_are_rejected_with_400(api: TestClient, limit: Any) -> None:
    response = api.get("/events", params={"limit": limit})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_events_is_empty_before_any_delivery(api: TestClient) -> None:
    assert api.get("/events").json() == []


def test_events_response_excludes_the_raw_payload(
    api: TestClient, webhook_issues_opened: dict[str, Any]
) -> None:
    deliver(api, webhook_issues_opened)

    event = api.get("/events").json()[0]
    assert set(event) == {"id", "event", "action", "issue_number", "timestamp"}


def test_background_logging_task_runs_without_raising(
    webhook_issues_opened: dict[str, Any],
) -> None:
    """Background tasks run after the response, so a failure there is invisible to
    the caller — it must be exercised directly."""
    from app.routers.webhooks import _log_delivery
    from app.webhooks import summarize

    summary = summarize("issues", webhook_issues_opened)
    _log_delivery(summary, "delivery-1", duplicate=False)
    _log_delivery(summary, "delivery-1", duplicate=True)
