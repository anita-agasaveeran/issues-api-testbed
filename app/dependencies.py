# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran
# and Ameya Mathew, who specified the requirements and design and reviewed the
# implementation.
# See the "Credits and authorship" section of README.md.
"""FastAPI dependencies that expose per-application singletons to routes."""

from __future__ import annotations

from fastapi import Request

from app.cache import ResponseCache
from app.config import Settings
from app.github_client import GitHubClient
from app.store import EventStore


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_github_client(request: Request) -> GitHubClient:
    """The shared client created in the lifespan handler, so connections are pooled."""
    client: GitHubClient = request.app.state.github
    return client


def get_response_cache(request: Request) -> ResponseCache:
    """The ETag cache built in ``create_app``; one per process."""
    cache: ResponseCache = request.app.state.cache
    return cache


def get_event_store(request: Request) -> EventStore:
    """The store opened in the lifespan handler; one connection per process."""
    store: EventStore = request.app.state.store
    return store


def get_request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)
