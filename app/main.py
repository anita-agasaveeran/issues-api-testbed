# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran
# and Ameya Mathew, who specified the requirements and design and reviewed the
# implementation.
# See the "Credits and authorship" section of README.md.
"""FastAPI application factory and process entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from starlette.middleware.base import BaseHTTPMiddleware

from app.cache import ResponseCache
from app.config import Settings, get_settings
from app.errors import register_exception_handlers
from app.github_client import GitHubClient
from app.logging import configure_logging, get_logger, request_context_middleware
from app.routers import issues, webhooks
from app.store import EventStore

API_VERSION = "0.1.0"


def _schema_without_auto_422(app: FastAPI) -> dict[str, Any]:
    """Generate the schema, minus FastAPI's automatic 422 responses.

    FastAPI documents a 422 for any operation with a validated body or parameter,
    but this service remaps validation failures to 400 (see ``app.errors``). Leaving
    the 422 in place would make the interactive docs contradict ``openapi.yaml``,
    which is the authoritative contract.
    """
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    for operations in schema.get("paths", {}).values():
        for operation in operations.values():
            if isinstance(operation, dict):
                operation.get("responses", {}).pop("422", None)

    app.openapi_schema = schema
    return schema


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    log = get_logger("lifespan")
    log.info("startup", repo=settings.repo_slug, port=settings.port)

    app.state.github = GitHubClient(settings)
    app.state.store = await EventStore(settings.sqlite_path).connect()
    try:
        yield
    finally:
        await app.state.github.aclose()
        await app.state.store.close()
        log.info("shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Issues API Testbed",
        version=API_VERSION,
        description="A thin, validated wrapper over the GitHub Issues REST API.",
        lifespan=lifespan,
    )
    app.openapi = lambda: _schema_without_auto_422(app)  # type: ignore[method-assign]
    app.state.settings = settings
    # Built here rather than in the lifespan handler so that a TestClient used
    # without its context manager still has a cache available.
    app.state.cache = ResponseCache(settings.cache_max_entries)
    app.add_middleware(BaseHTTPMiddleware, dispatch=request_context_middleware)
    register_exception_handlers(app)
    app.include_router(issues.router)
    app.include_router(webhooks.router)

    @app.get("/healthz", tags=["meta"], summary="Liveness and configuration check")
    async def healthz() -> dict[str, Any]:
        """Reports process health. Never contacts GitHub, so it stays fast and quota-free."""
        return {
            "status": "ok",
            "version": API_VERSION,
            "repo": settings.repo_slug,
        }

    return app


app = create_app()
