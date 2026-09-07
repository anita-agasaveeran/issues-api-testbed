# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran
# and Ameya Mathew, who specified the requirements and design and reviewed the
# implementation.
# See the "Credits and authorship" section of README.md.
"""Request and response models, plus the mapping from GitHub payloads to ours.

The service does not proxy GitHub's JSON verbatim. It projects a small, stable
subset so the contract in ``openapi.yaml`` stays honest even if GitHub adds
fields, and so labels are plain strings on the way in *and* out.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class IssueState(StrEnum):
    """States an issue can be set to."""

    OPEN = "open"
    CLOSED = "closed"


class IssueStateFilter(StrEnum):
    """States that can be filtered on when listing."""

    OPEN = "open"
    CLOSED = "closed"
    ALL = "all"


# --------------------------------------------------------------------- requests


class IssueCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=256, examples=["Checkout button is dead"])
    body: str | None = Field(default=None, max_length=65536)
    labels: list[str] | None = Field(default=None, max_length=100)

    @field_validator("title")
    @classmethod
    def _title_is_not_only_whitespace(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must contain at least one non-whitespace character")
        return stripped

    @field_validator("labels")
    @classmethod
    def _labels_are_clean(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [label.strip() for label in value if label.strip()]
        if len(cleaned) != len(value):
            raise ValueError("labels must not contain empty or whitespace-only entries")
        return cleaned


class IssuePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=256)
    body: str | None = Field(default=None, max_length=65536)
    state: IssueState | None = None

    @field_validator("title")
    @classmethod
    def _title_is_not_only_whitespace(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must contain at least one non-whitespace character")
        return stripped

    @model_validator(mode="after")
    def _at_least_one_field(self) -> IssuePatch:
        if self.title is None and self.body is None and self.state is None:
            raise ValueError("provide at least one of: title, body, state")
        return self

    def to_github_payload(self) -> dict[str, Any]:
        payload = self.model_dump(exclude_none=True)
        if isinstance(payload.get("state"), IssueState):
            payload["state"] = payload["state"].value
        return payload


class CommentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(..., min_length=1, max_length=65536, examples=["Reproduced on Safari 17."])

    @field_validator("body")
    @classmethod
    def _body_is_not_only_whitespace(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("body must contain at least one non-whitespace character")
        return stripped


# -------------------------------------------------------------------- responses


class Issue(BaseModel):
    number: int
    title: str
    body: str | None = None
    state: str
    labels: list[str] = Field(default_factory=list)
    html_url: str
    user: str | None = None
    comments: int = 0
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None


class Comment(BaseModel):
    id: int
    body: str
    user: str | None = None
    html_url: str
    created_at: datetime
    updated_at: datetime | None = None
    issue_number: int | None = None


class EventRecord(BaseModel):
    """A processed webhook delivery, as returned by ``GET /events``."""

    id: str = Field(..., description="The X-GitHub-Delivery id of the delivery.")
    event: str = Field(..., examples=["issues"])
    action: str | None = Field(default=None, examples=["opened"])
    issue_number: int | None = None
    timestamp: datetime


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


# ----------------------------------------------------------------- projections


def is_pull_request(payload: dict[str, Any]) -> bool:
    """GitHub's issues endpoints also return pull requests; this spots them."""
    return "pull_request" in payload


def _label_names(payload: dict[str, Any]) -> list[str]:
    labels = payload.get("labels") or []
    names: list[str] = []
    for label in labels:
        if isinstance(label, dict):
            name = label.get("name")
            if isinstance(name, str):
                names.append(name)
        elif isinstance(label, str):
            names.append(label)
    return names


def _login(payload: dict[str, Any]) -> str | None:
    user = payload.get("user")
    if isinstance(user, dict):
        login = user.get("login")
        if isinstance(login, str):
            return login
    return None


def to_issue(payload: dict[str, Any]) -> Issue:
    """Project a GitHub issue object onto this service's Issue model."""
    return Issue(
        number=payload["number"],
        title=payload.get("title", ""),
        body=payload.get("body"),
        state=payload.get("state", "open"),
        labels=_label_names(payload),
        html_url=payload.get("html_url", ""),
        user=_login(payload),
        comments=payload.get("comments", 0) or 0,
        created_at=payload["created_at"],
        updated_at=payload["updated_at"],
        closed_at=payload.get("closed_at"),
    )


def to_comment(payload: dict[str, Any], issue_number: int | None = None) -> Comment:
    """Project a GitHub issue-comment object onto this service's Comment model."""
    return Comment(
        id=payload["id"],
        body=payload.get("body", ""),
        user=_login(payload),
        html_url=payload.get("html_url", ""),
        created_at=payload["created_at"],
        updated_at=payload.get("updated_at"),
        issue_number=issue_number,
    )
