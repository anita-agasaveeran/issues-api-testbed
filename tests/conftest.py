# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran,
# who specified the requirements and design and reviewed the implementation.
# See the "Credits and authorship" section of README.md.
"""Shared test fixtures.

``app.main`` builds a module-level ``app`` at import time so that
``uvicorn app.main:app`` works, which means importing it requires configuration to
be present. Placeholder values are therefore seeded into the environment *before*
the application package is imported. They never reach GitHub: unit tests mock the
transport, and integration tests override them with real credentials.
"""

from __future__ import annotations

import os

#: Sentinel values, so integration tests can tell "no credentials configured" apart
#: from real ones. Never sent to GitHub.
PLACEHOLDERS = {
    "GITHUB_TOKEN": "env-placeholder-token",
    "GITHUB_OWNER": "env-placeholder-owner",
    "GITHUB_REPO": "env-placeholder-repo",
    "WEBHOOK_SECRET": "env-placeholder-secret",
}

for _name, _value in PLACEHOLDERS.items():
    os.environ.setdefault(_name, _value)


def has_real_credentials() -> bool:
    """True when the environment carries real GitHub credentials, not placeholders."""
    return all(os.environ.get(name, value) != value for name, value in PLACEHOLDERS.items())


import json  # noqa: E402
import pathlib  # noqa: E402
from collections.abc import Callable  # noqa: E402
from typing import Any  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.dependencies import get_event_store, get_github_client  # noqa: E402
from app.github_client import GitHubClient  # noqa: E402
from app.main import create_app  # noqa: E402
from app.store import EventStore  # noqa: E402

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

MockHandler = Callable[[httpx.Request], httpx.Response]
ApiFactory = Callable[[MockHandler], TestClient]


@pytest.fixture
def settings() -> Settings:
    return Settings(
        github_token="test-token",  # noqa: S106
        github_owner="test-owner",
        github_repo="test-repo",
        webhook_secret="test-secret",  # noqa: S106
        database_url="sqlite:///./test-events.db",
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings), raise_server_exceptions=False)


def load_fixture(name: str) -> dict[str, Any]:
    """Read a captured GitHub payload from tests/fixtures."""
    return json.loads((FIXTURES / name).read_text())  # type: ignore[no-any-return]


@pytest.fixture
def github_issue() -> dict[str, Any]:
    return load_fixture("github_issue.json")


@pytest.fixture
def github_pull_request() -> dict[str, Any]:
    return load_fixture("github_pull_request.json")


@pytest.fixture
def github_comment() -> dict[str, Any]:
    return load_fixture("github_comment.json")


@pytest.fixture
def webhook_issues_opened() -> dict[str, Any]:
    return load_fixture("webhook_issues_opened.json")


@pytest.fixture
def webhook_issue_comment_created() -> dict[str, Any]:
    return load_fixture("webhook_issue_comment_created.json")


@pytest.fixture
def webhook_ping() -> dict[str, Any]:
    return load_fixture("webhook_ping.json")


@pytest.fixture
def make_api(settings: Settings, tmp_path: pathlib.Path) -> ApiFactory:
    """Build a TestClient whose GitHub client speaks to a scripted mock transport.

    Overriding the dependency (rather than patching httpx globally) keeps the
    TestClient's own ASGI traffic untouched, while still exercising the real
    routes, schemas, and error mapping.
    """

    def _make(handler: MockHandler) -> TestClient:
        app = create_app(settings)
        github = GitHubClient(
            settings,
            httpx.AsyncClient(
                base_url=settings.github_api_url,
                transport=httpx.MockTransport(handler),
            ),
        )
        app.dependency_overrides[get_github_client] = lambda: github

        # Connected lazily, inside the request's own event loop, against a
        # per-test database file.
        opened: dict[str, EventStore] = {}

        async def provide_store() -> EventStore:
            if "store" not in opened:
                opened["store"] = await EventStore(str(tmp_path / "events.db")).connect()
            return opened["store"]

        app.dependency_overrides[get_event_store] = provide_store
        return TestClient(app, raise_server_exceptions=False)

    return _make
