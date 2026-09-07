# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran,
# who specified the requirements and design and reviewed the implementation.
# See the "Credits and authorship" section of README.md.
"""Webhook ingestion and the delivery log that backs it."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response, status

from app.config import Settings
from app.dependencies import get_event_store, get_request_id, get_settings_dep
from app.errors import ValidationError
from app.logging import get_logger
from app.schemas import ErrorResponse, EventRecord
from app.store import EventStore
from app.webhooks import (
    DELIVERY_HEADER,
    SIGNATURE_HEADER,
    WebhookSummary,
    summarize,
    validate_event,
    verify_signature,
)

log = get_logger("webhook")

router = APIRouter(tags=["webhooks"])


def _log_delivery(summary: WebhookSummary, delivery_id: str, *, duplicate: bool) -> None:
    """Runs after the response is sent, so the ack is never blocked by logging."""
    structlog.contextvars.bind_contextvars(delivery_id=delivery_id)
    log.info(
        "webhook_processed",
        duplicate=duplicate,
        **summary.as_log_fields(),
    )


@router.post(
    "/webhook",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={
        204: {"description": "Delivery accepted (or already seen)"},
        400: {"model": ErrorResponse, "description": "Unknown event, or malformed payload"},
        401: {"model": ErrorResponse, "description": "Missing or invalid signature"},
    },
    summary="Receive a GitHub webhook delivery",
)
async def receive_webhook(
    request: Request,
    background: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    store: Annotated[EventStore, Depends(get_event_store)],
) -> Response:
    """Verify, record, and acknowledge a delivery.

    The order of checks matters: signature first, so an unauthenticated caller
    learns nothing about which events or payloads this service accepts.
    """
    raw_body = await request.body()
    verify_signature(
        settings.webhook_secret.get_secret_value(),
        raw_body,
        request.headers.get(SIGNATURE_HEADER),
    )

    event = validate_event(request.headers.get("X-GitHub-Event"))

    delivery_id = request.headers.get(DELIVERY_HEADER)
    if not delivery_id:
        raise ValidationError(
            f"Missing {DELIVERY_HEADER} header.",
            details={"reason": "delivery_header_absent"},
        )

    try:
        payload: Any = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError as exc:
        raise ValidationError(
            "Webhook body is not valid JSON.",
            details={"reason": "invalid_json"},
        ) from exc
    if not isinstance(payload, dict):
        raise ValidationError(
            "Webhook body must be a JSON object.",
            details={"reason": "payload_not_an_object"},
        )

    summary = summarize(event, payload)
    stored = await store.record(
        delivery_id=delivery_id,
        event=summary.event,
        action=summary.action,
        issue_number=summary.issue_number,
        comment_id=summary.comment_id,
        sender=summary.sender,
        payload=payload,
    )

    # Slow work belongs here, after the response is queued — never before the ack.
    background.add_task(_log_delivery, summary, delivery_id, duplicate=not stored)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/events",
    response_model=list[EventRecord],
    summary="List recently processed webhook deliveries",
    tags=["webhooks"],
)
async def list_events(
    store: Annotated[EventStore, Depends(get_event_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    request_id: Annotated[str | None, Depends(get_request_id)],
    limit: Annotated[int, Query(ge=1, le=500, description="How many deliveries to return.")] = 50,
) -> list[EventRecord]:
    """Return the most recent deliveries, newest first — a debugging aid."""
    capped = min(limit, settings.events_max_limit)
    events = await store.recent(capped)
    log.info("events_listed", count=len(events), request_id=request_id)
    return [
        EventRecord(
            id=event.id,
            event=event.event,
            action=event.action,
            issue_number=event.issue_number,
            timestamp=datetime.fromisoformat(event.timestamp),
        )
        for event in events
    ]
