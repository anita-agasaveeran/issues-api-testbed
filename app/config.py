# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran
# and Ameya Mathew, who specified the requirements and design and reviewed the
# implementation.
# See the "Credits and authorship" section of README.md.
"""Application configuration, sourced entirely from the environment.

Secrets are held as ``SecretStr`` so that an accidental ``repr`` or log of the
settings object renders ``**********`` instead of the real value.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    github_token: SecretStr = Field(
        ...,
        description="Fine-grained PAT with Issues:read-write and Metadata:read on the target repo.",
    )
    github_owner: str = Field(..., min_length=1)
    github_repo: str = Field(..., min_length=1)
    webhook_secret: SecretStr = Field(
        ...,
        description="Shared secret used to verify the X-Hub-Signature-256 header.",
    )

    github_api_url: str = "https://api.github.com"
    github_api_version: str = "2022-11-28"
    request_timeout_seconds: float = 10.0

    port: int = Field(default=8000, ge=1, le=65535)
    database_url: str = "sqlite:///./events.db"
    log_level: str = "INFO"
    events_max_limit: int = 200
    cache_enabled: bool = True
    cache_max_entries: int = Field(default=128, ge=1)

    @field_validator("log_level")
    @classmethod
    def _upper(cls, value: str) -> str:
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        normalized = value.upper()
        if normalized not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return normalized

    @property
    def repo_slug(self) -> str:
        return f"{self.github_owner}/{self.github_repo}"

    @property
    def sqlite_path(self) -> str:
        """Filesystem path implied by ``database_url`` (sqlite URLs only)."""
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            raise ValueError("Only sqlite:/// URLs are supported by the local event store")
        return self.database_url[len(prefix) :]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor so settings are parsed once per process."""
    return Settings()  # type: ignore[call-arg]  # values come from the environment
