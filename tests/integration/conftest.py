# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran
# and Ameya Mathew, who specified the requirements and design and reviewed the
# implementation.
# See the "Credits and authorship" section of README.md.
"""Fixtures for tests that talk to the real GitHub API.

These run against a repository you control and create real issues in it. They are
skipped entirely unless real credentials are present, so ``pytest`` stays green on
a fresh clone and in CI.

Every test drives *this service's* HTTP API rather than GitHub directly: that is
what the assignment asks to verify, and it exercises validation, projection, and
error mapping on the way through.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import has_real_credentials

RUN_MARKER = "Created by the issues-api-testbed integration suite."

_HERE = pathlib.Path(__file__).parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark this package's tests as integration, and skip them without credentials.

    Two details matter here. A ``pytestmark`` in a *conftest* does not propagate to
    test modules, so the marker is applied from this hook instead. And pytest hands
    every conftest hook the whole collection, so items are filtered by path — an
    unfiltered loop would skip the entire unit suite too.
    """
    skip = pytest.mark.skip(
        reason=(
            "integration tests need real credentials: set GITHUB_TOKEN, GITHUB_OWNER, "
            "GITHUB_REPO and WEBHOOK_SECRET in .env (see README)"
        )
    )
    credentials_present = has_real_credentials()

    for item in items:
        if _HERE not in pathlib.Path(str(item.path)).parents:
            continue
        item.add_marker(pytest.mark.integration)
        if not credentials_present:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def live_settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    """Real configuration from the environment, with a throwaway event store."""
    database = tmp_path_factory.mktemp("integration") / "events.db"
    return Settings(
        github_token=os.environ["GITHUB_TOKEN"],
        github_owner=os.environ["GITHUB_OWNER"],
        github_repo=os.environ["GITHUB_REPO"],
        webhook_secret=os.environ["WEBHOOK_SECRET"],
        database_url=f"sqlite:///{database}",
    )


@pytest.fixture(scope="session")
def api(live_settings: Settings) -> Iterator[TestClient]:
    """This service, wired to the real GitHub API.

    Entering the context runs the lifespan handler, so the app builds a genuine
    ``GitHubClient`` — no transport mocking anywhere in this package.
    """
    with TestClient(create_app(live_settings), raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def unique_title() -> str:
    """A title that cannot collide with anything already in the repository."""
    return f"[integration] {uuid.uuid4().hex[:12]}"


@pytest.fixture
def created_issues(api: TestClient) -> Iterator[list[int]]:
    """Track issues created by a test and close them afterwards.

    GitHub's REST API cannot delete an issue, so closing is the strongest cleanup
    available. Failures here are swallowed: a cleanup problem must not mask the
    assertion that actually failed.
    """
    numbers: list[int] = []
    yield numbers
    for number in numbers:
        with contextlib.suppress(Exception):
            api.patch(f"/issues/{number}", json={"state": "closed"})
