# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran,
# who specified the requirements and design and reviewed the implementation.
# See the "Credits and authorship" section of README.md.
"""Unit tests for the SQLite delivery store, including idempotency."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.store import EventStore


@pytest.fixture
async def store() -> AsyncIterator[EventStore]:
    opened = await EventStore(":memory:").connect()
    yield opened
    await opened.close()


async def record(store: EventStore, delivery_id: str, **overrides: Any) -> bool:
    values: dict[str, Any] = {
        "delivery_id": delivery_id,
        "event": "issues",
        "action": "opened",
        "issue_number": 42,
        "comment_id": None,
        "sender": "test-owner",
        "payload": {"action": "opened"},
    }
    values.update(overrides)
    return await store.record(**values)


async def test_a_new_delivery_is_stored(store: EventStore) -> None:
    assert await record(store, "d-1") is True
    assert await store.count() == 1


async def test_redelivering_the_same_id_is_a_no_op(store: EventStore) -> None:
    assert await record(store, "d-1") is True
    assert await record(store, "d-1") is False
    assert await store.count() == 1


async def test_redelivery_does_not_overwrite_the_original_row(store: EventStore) -> None:
    await record(store, "d-1", issue_number=42)
    await record(store, "d-1", issue_number=999)

    events = await store.recent()
    assert [event.issue_number for event in events] == [42]


async def test_the_same_id_with_a_different_action_is_a_separate_event(
    store: EventStore,
) -> None:
    """Dedupe keys on (delivery_id, action), so a distinct action still records."""
    assert await record(store, "d-1", action="opened") is True
    assert await record(store, "d-1", action="closed") is True
    assert await store.count() == 2


async def test_distinct_deliveries_are_all_stored(store: EventStore) -> None:
    for index in range(5):
        await record(store, f"d-{index}")
    assert await store.count() == 5


async def test_recent_returns_newest_first(store: EventStore) -> None:
    for index in range(3):
        await record(store, f"d-{index}", issue_number=index)

    events = await store.recent()
    assert [event.issue_number for event in events] == [2, 1, 0]


async def test_recent_honours_the_limit(store: EventStore) -> None:
    for index in range(10):
        await record(store, f"d-{index}")
    assert len(await store.recent(limit=3)) == 3


async def test_recent_is_empty_on_a_fresh_store(store: EventStore) -> None:
    assert await store.recent() == []


async def test_ping_stores_a_null_action(store: EventStore) -> None:
    await record(store, "d-ping", event="ping", action=None, issue_number=None)

    event = (await store.recent())[0]
    assert event.action is None
    assert event.event == "ping"


async def test_full_payload_is_retained_but_kept_out_of_listings(store: EventStore) -> None:
    payload = {"action": "opened", "issue": {"number": 42, "title": "x"}}
    await record(store, "d-1", payload=payload)

    assert await store.payload_for("d-1") == payload
    assert not hasattr((await store.recent())[0], "payload")


async def test_payload_for_an_unknown_delivery_is_none(store: EventStore) -> None:
    assert await store.payload_for("nope") is None


async def test_using_the_store_before_connecting_fails_loudly() -> None:
    with pytest.raises(RuntimeError, match="connect"):
        _ = EventStore(":memory:").connection


async def test_store_works_as_an_async_context_manager() -> None:
    async with EventStore(":memory:") as opened:
        await record(opened, "d-1")
        assert await opened.count() == 1


async def test_schema_survives_reconnecting_to_the_same_file(tmp_path: Any) -> None:
    path = str(tmp_path / "events.db")
    async with EventStore(path) as first:
        await record(first, "d-1")
    async with EventStore(path) as second:
        assert await second.count() == 1
        assert await record(second, "d-1") is False
