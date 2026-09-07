# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran,
# who specified the requirements and design and reviewed the implementation.
# See the "Credits and authorship" section of README.md.
"""Webhook signature verification and payload summarising.

Two rules drive everything here:

1. The signature is computed over the **raw** request body. Re-serialising the
   parsed JSON would change byte-for-byte content and break verification, so the
   body is read as bytes and only parsed after the signature checks out.
2. Comparison is constant-time. A short-circuiting ``==`` leaks, through timing,
   how many leading characters of a forged signature were correct.

Neither the secret nor the received signature is ever logged.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

from app.errors import UnauthorizedError, ValidationError

SIGNATURE_HEADER = "X-Hub-Signature-256"
EVENT_HEADER = "X-GitHub-Event"
DELIVERY_HEADER = "X-GitHub-Delivery"

SIGNATURE_PREFIX = "sha256="

#: Events this service understands. Anything else is rejected with 400 so that a
#: misconfigured webhook subscription is loud rather than silently ignored.
SUPPORTED_EVENTS = frozenset({"issues", "issue_comment", "ping"})

#: Events that must carry an ``action`` field in their payload.
ACTION_REQUIRED_EVENTS = frozenset({"issues", "issue_comment"})


def compute_signature(secret: str, body: bytes) -> str:
    """Return the ``sha256=...`` signature GitHub would send for this body."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_PREFIX}{digest}"


def verify_signature(secret: str, body: bytes, header_value: str | None) -> None:
    """Raise :class:`UnauthorizedError` unless ``header_value`` matches ``body``.

    Every failure returns the same message: telling a caller *why* verification
    failed would help them iterate towards a valid forgery.
    """
    if not header_value:
        raise UnauthorizedError(
            "Missing or invalid webhook signature.",
            details={"reason": "signature_header_absent"},
        )

    expected = compute_signature(secret, body)
    if not hmac.compare_digest(expected, header_value):
        raise UnauthorizedError(
            "Missing or invalid webhook signature.",
            details={"reason": "signature_mismatch"},
        )


@dataclass(frozen=True, slots=True)
class WebhookSummary:
    """The handful of fields worth indexing out of a delivery payload."""

    event: str
    action: str | None
    issue_number: int | None
    comment_id: int | None
    sender: str | None

    def as_log_fields(self) -> dict[str, Any]:
        """Log-safe fields.

        The event name is bound as ``github_event`` on purpose: structlog reserves
        ``event`` for the log message itself, and passing both raises a TypeError.
        """
        return {
            "github_event": self.event,
            "action": self.action,
            "issue_number": self.issue_number,
            "comment_id": self.comment_id,
        }


def validate_event(event: str | None) -> str:
    """Return the event name, or raise 400 for an absent/unsupported event."""
    if not event:
        raise ValidationError(
            f"Missing {EVENT_HEADER} header.",
            details={"reason": "event_header_absent"},
        )
    if event not in SUPPORTED_EVENTS:
        raise ValidationError(
            f"Unsupported webhook event '{event}'.",
            details={"event": event, "supported": sorted(SUPPORTED_EVENTS)},
        )
    return event


def summarize(event: str, payload: dict[str, Any]) -> WebhookSummary:
    """Extract the indexed fields from a delivery payload.

    ``action`` is required for ``issues`` and ``issue_comment`` — a payload without
    one is malformed, not merely unfamiliar. Unknown *values* of ``action`` are
    accepted on purpose: GitHub adds new ones over time, and rejecting them would
    make this service fail on events it could safely record.
    """
    action = payload.get("action")
    if event in ACTION_REQUIRED_EVENTS and not isinstance(action, str):
        raise ValidationError(
            f"Event '{event}' requires an 'action' field.",
            details={"event": event, "reason": "action_missing"},
        )

    issue = payload.get("issue")
    issue_number = issue.get("number") if isinstance(issue, dict) else None

    comment = payload.get("comment")
    comment_id = comment.get("id") if isinstance(comment, dict) else None

    sender = payload.get("sender")
    sender_login = sender.get("login") if isinstance(sender, dict) else None

    return WebhookSummary(
        event=event,
        action=action if isinstance(action, str) else None,
        issue_number=issue_number if isinstance(issue_number, int) else None,
        comment_id=comment_id if isinstance(comment_id, int) else None,
        sender=sender_login if isinstance(sender_login, str) else None,
    )
