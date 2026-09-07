# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran
# and Ameya Mathew, who specified the requirements and design and reviewed the
# implementation.
# See the "Credits and authorship" section of README.md.
"""Unit tests for signature verification and payload summarising."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import pytest

from app.errors import UnauthorizedError, ValidationError
from app.webhooks import (
    compute_signature,
    summarize,
    validate_event,
    verify_signature,
)

SECRET = "test-secret"  # noqa: S105
BODY = b'{"action":"opened","issue":{"number":42}}'


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- verification


def test_valid_signature_passes() -> None:
    verify_signature(SECRET, BODY, sign(BODY))


def test_computed_signature_matches_the_reference_implementation() -> None:
    assert compute_signature(SECRET, BODY) == sign(BODY)
    assert compute_signature(SECRET, BODY).startswith("sha256=")


def test_tampered_body_is_rejected() -> None:
    signature = sign(BODY)
    tampered = BODY.replace(b'"number":42', b'"number":99')
    with pytest.raises(UnauthorizedError):
        verify_signature(SECRET, tampered, signature)


def test_signature_from_a_different_secret_is_rejected() -> None:
    with pytest.raises(UnauthorizedError):
        verify_signature(SECRET, BODY, sign(BODY, "not-the-secret"))


@pytest.mark.parametrize(
    "header",
    [
        pytest.param(None, id="absent"),
        pytest.param("", id="empty"),
        pytest.param("sha256=", id="prefix-only"),
        pytest.param("deadbeef", id="no-prefix"),
        pytest.param("sha1=" + "0" * 40, id="wrong-algorithm"),
        pytest.param("sha256=" + "0" * 64, id="right-shape-wrong-digest"),
        pytest.param("sha256=zz", id="not-hex"),
    ],
)
def test_malformed_or_wrong_signatures_are_rejected(header: str | None) -> None:
    with pytest.raises(UnauthorizedError) as excinfo:
        verify_signature(SECRET, BODY, header)
    assert excinfo.value.status_code == 401


def test_failure_message_is_identical_regardless_of_cause() -> None:
    """A distinguishing message would help an attacker iterate towards a forgery."""
    with pytest.raises(UnauthorizedError) as absent:
        verify_signature(SECRET, BODY, None)
    with pytest.raises(UnauthorizedError) as mismatch:
        verify_signature(SECRET, BODY, sign(BODY, "wrong"))

    assert absent.value.message == mismatch.value.message


def test_empty_body_still_verifies() -> None:
    verify_signature(SECRET, b"", sign(b""))


def test_verification_uses_a_constant_time_comparison() -> None:
    """Guards against a refactor to `==`, which would leak the digest by timing."""
    import inspect

    import app.webhooks as webhooks_module

    source = inspect.getsource(webhooks_module.verify_signature)
    assert "compare_digest" in source


# --------------------------------------------------------------- event routing


@pytest.mark.parametrize("event", ["issues", "issue_comment", "ping"])
def test_supported_events_are_accepted(event: str) -> None:
    assert validate_event(event) == event


@pytest.mark.parametrize("event", [None, "", "push", "pull_request", "Issues"])
def test_unsupported_or_missing_events_are_rejected(event: str | None) -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_event(event)
    assert excinfo.value.status_code == 400


# ----------------------------------------------------------------- summarising


def test_summarize_extracts_issue_fields(webhook_issues_opened: dict[str, Any]) -> None:
    summary = summarize("issues", webhook_issues_opened)

    assert summary.action == "opened"
    assert summary.issue_number == 42
    assert summary.comment_id is None
    assert summary.sender == "test-owner"


def test_summarize_extracts_comment_fields(
    webhook_issue_comment_created: dict[str, Any],
) -> None:
    summary = summarize("issue_comment", webhook_issue_comment_created)

    assert summary.action == "created"
    assert summary.issue_number == 42
    assert summary.comment_id == 3100000001


def test_ping_needs_no_action(webhook_ping: dict[str, Any]) -> None:
    summary = summarize("ping", webhook_ping)

    assert summary.action is None
    assert summary.issue_number is None


def test_issues_event_without_an_action_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        summarize("issues", {"issue": {"number": 1}})
    assert excinfo.value.details["reason"] == "action_missing"


def test_unknown_action_values_are_accepted() -> None:
    """GitHub adds actions over time; recording them beats rejecting them."""
    summary = summarize("issues", {"action": "some_future_action", "issue": {"number": 7}})
    assert summary.action == "some_future_action"


def test_malformed_nested_objects_do_not_raise() -> None:
    summary = summarize("issues", {"action": "opened", "issue": "not-an-object", "sender": 5})

    assert summary.issue_number is None
    assert summary.sender is None


def test_log_fields_exclude_the_payload(webhook_issues_opened: dict[str, Any]) -> None:
    fields = summarize("issues", webhook_issues_opened).as_log_fields()
    assert set(fields) == {"github_event", "action", "issue_number", "comment_id"}


def test_log_fields_avoid_structlogs_reserved_event_key(
    webhook_issues_opened: dict[str, Any],
) -> None:
    """`log.info("msg", event=...)` raises TypeError — structlog owns that name."""
    fields = summarize("issues", webhook_issues_opened).as_log_fields()
    assert "event" not in fields
    assert fields["github_event"] == "issues"
