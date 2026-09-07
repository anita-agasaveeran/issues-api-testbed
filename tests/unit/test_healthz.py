# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran
# and Ameya Mathew, who specified the requirements and design and reviewed the
# implementation.
# See the "Credits and authorship" section of README.md.
from __future__ import annotations

from fastapi.testclient import TestClient

from app.logging import REQUEST_ID_HEADER


def test_healthz_reports_ok(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["repo"] == "test-owner/test-repo"


def test_healthz_response_carries_a_request_id(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.headers[REQUEST_ID_HEADER]


def test_supplied_request_id_is_echoed_back(client: TestClient) -> None:
    response = client.get("/healthz", headers={REQUEST_ID_HEADER: "abc-123"})
    assert response.headers[REQUEST_ID_HEADER] == "abc-123"


def test_unknown_route_uses_the_shared_error_shape(client: TestClient) -> None:
    response = client.get("/nope")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["request_id"]


def test_lifespan_builds_and_closes_the_github_client(settings: object) -> None:
    """Entering the TestClient context runs startup/shutdown, unlike a bare client."""
    from app.github_client import GitHubClient
    from app.main import create_app

    app = create_app(settings)  # type: ignore[arg-type]
    with TestClient(app) as running:
        assert isinstance(running.app.state.github, GitHubClient)
        assert running.get("/healthz").status_code == 200
