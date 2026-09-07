# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran
# and Ameya Mathew, who specified the requirements and design and reviewed the
# implementation.
# See the "Credits and authorship" section of README.md.
from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.config import Settings


def _base(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "github_token": "t",
        "github_owner": "o",
        "github_repo": "r",
        "webhook_secret": "s",
    }
    values.update(overrides)
    return values


def test_repo_slug_joins_owner_and_repo(settings: Settings) -> None:
    assert settings.repo_slug == "test-owner/test-repo"


def test_secrets_are_not_exposed_in_repr(settings: Settings) -> None:
    dumped = repr(settings)
    assert "test-token" not in dumped
    assert "test-secret" not in dumped


def test_token_value_is_still_readable(settings: Settings) -> None:
    assert settings.github_token.get_secret_value() == "test-token"


def test_log_level_is_normalised_to_upper_case() -> None:
    assert Settings(**_base(log_level="debug")).log_level == "DEBUG"  # type: ignore[arg-type]


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        Settings(**_base(log_level="chatty"))  # type: ignore[arg-type]


def test_sqlite_path_is_derived_from_database_url() -> None:
    s = Settings(**_base(database_url="sqlite:///./events.db"))  # type: ignore[arg-type]
    assert s.sqlite_path == "./events.db"


def test_non_sqlite_database_url_is_rejected() -> None:
    s = Settings(**_base(database_url="postgres://localhost/db"))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="sqlite"):
        _ = s.sqlite_path
