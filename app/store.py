# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran,
# who specified the requirements and design and reviewed the implementation.
# See the "Credits and authorship" section of README.md.
"""SQLite-backed store of processed webhook deliveries.

Idempotency lives in the schema rather than in application logic: the primary key
is ``(delivery_id, action)`` and inserts use ``INSERT OR IGNORE``. A redelivery of
the same event is therefore a no-op that still answers 204, which is exactly what
GitHub's retry behaviour needs.

The delivery id alone would be enough in practice — GitHub reuses it across
redeliveries of one event — but the action is part of the key as a cheap guard
against two logically different events sharing an id.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Self

import aiosqlite

from app.logging import get_logger

log = get_logger("store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS webhook_events (
    delivery_id  TEXT    NOT NULL,
    action       TEXT    NOT NULL DEFAULT '',
    event        TEXT    NOT NULL,
    issue_number INTEGER,
    comment_id   INTEGER,
    sender       TEXT,
    received_at  TEXT    NOT NULL,
    payload      TEXT    NOT NULL,
    PRIMARY KEY (delivery_id, action)
);
CREATE INDEX IF NOT EXISTS idx_webhook_events_received_at
    ON webhook_events (received_at DESC);
"""


@dataclass(frozen=True, slots=True)
class StoredEvent:
    """One processed delivery, in the shape ``GET /events`` returns."""

    id: str
    event: str
    action: str | None
    issue_number: int | None
    timestamp: str


class EventStore:
    """Async wrapper over a single SQLite connection.

    One connection is enough at this scale, and it keeps ``:memory:`` databases
    usable in tests — a fresh connection to ``:memory:`` would see an empty schema.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._connection: aiosqlite.Connection | None = None

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("EventStore.connect() must be awaited before use")
        return self._connection

    async def connect(self) -> Self:
        self._connection = await aiosqlite.connect(self._path)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.executescript(_SCHEMA)
        await self._connection.commit()
        log.info("event_store_ready", path=self._path)
        return self

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def __aenter__(self) -> Self:
        return await self.connect()

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def record(
        self,
        *,
        delivery_id: str,
        event: str,
        action: str | None,
        issue_number: int | None,
        comment_id: int | None,
        sender: str | None,
        payload: dict[str, Any],
    ) -> bool:
        """Persist a delivery. Returns ``False`` when it was already stored."""
        cursor = await self.connection.execute(
            """
            INSERT OR IGNORE INTO webhook_events
                (delivery_id, action, event, issue_number, comment_id, sender,
                 received_at, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                delivery_id,
                action or "",
                event,
                issue_number,
                comment_id,
                sender,
                datetime.now(UTC).isoformat(),
                json.dumps(payload, separators=(",", ":")),
            ),
        )
        await self.connection.commit()
        return cursor.rowcount > 0

    async def recent(self, limit: int = 50) -> list[StoredEvent]:
        """Return the most recently received deliveries, newest first."""
        cursor = await self.connection.execute(
            """
            SELECT delivery_id, event, action, issue_number, received_at
            FROM webhook_events
            ORDER BY received_at DESC, rowid DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            StoredEvent(
                id=row["delivery_id"],
                event=row["event"],
                action=row["action"] or None,
                issue_number=row["issue_number"],
                timestamp=row["received_at"],
            )
            for row in rows
        ]

    async def count(self) -> int:
        cursor = await self.connection.execute("SELECT COUNT(*) AS n FROM webhook_events")
        row = await cursor.fetchone()
        return int(row["n"]) if row else 0

    async def payload_for(self, delivery_id: str) -> dict[str, Any] | None:
        """The full stored payload, kept out of ``GET /events`` responses."""
        cursor = await self.connection.execute(
            "SELECT payload FROM webhook_events WHERE delivery_id = ? LIMIT 1",
            (delivery_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        loaded: dict[str, Any] = json.loads(row["payload"])
        return loaded
