"""Environment-backed settings for MomOps."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MomOpsSettings(BaseSettings):
    """Runtime configuration loaded from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_prefix="MOMOPS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    aws_access_key_id: str | None = Field(default=None, alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: str | None = Field(default=None, alias="AWS_SECRET_ACCESS_KEY")
    aws_session_token: str | None = Field(default=None, alias="AWS_SESSION_TOKEN")
    aws_default_region: str = Field(default="us-east-1", alias="AWS_DEFAULT_REGION")
    budget_limit: float | None = None
    dry_run: bool = False
    log_level: str = "INFO"
    state_dir: Path = Path.home() / ".momops"
    pricing_mode: str = "static"


@lru_cache(maxsize=1)
def get_settings() -> MomOpsSettings:
    """Return cached settings."""
    return MomOpsSettings()
