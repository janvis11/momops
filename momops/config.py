from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Configure logging early
from momops.utils.logger import configure_logging


class MomOpsSettings(BaseSettings):
    """Runtime configuration loaded from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_prefix="MOMOPS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    groq_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GROQ_API_KEY", "MOMOPS_GROQ_API_KEY"),
    )
    openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "MOMOPS_OPENAI_API_KEY"),
    )
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "MOMOPS_ANTHROPIC_API_KEY"),
    )
    groq_model: str = Field(
        default="llama-3.3-70b-versatile",
        validation_alias=AliasChoices("GROQ_MODEL", "MOMOPS_GROQ_MODEL"),
    )
    openai_model: str = Field(
        default="gpt-5.4-mini",
        validation_alias=AliasChoices("OPENAI_MODEL", "MOMOPS_OPENAI_MODEL"),
    )
    anthropic_model: str = Field(
        default="claude-sonnet-4-20250514",
        validation_alias=AliasChoices("ANTHROPIC_MODEL", "MOMOPS_ANTHROPIC_MODEL"),
    )
    llm_timeout_seconds: float = 30.0
    aws_access_key_id: str | None = Field(default=None, alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: str | None = Field(default=None, alias="AWS_SECRET_ACCESS_KEY")
    aws_session_token: str | None = Field(default=None, alias="AWS_SESSION_TOKEN")
    aws_default_region: str = Field(default="us-east-1", alias="AWS_DEFAULT_REGION")
    budget_limit: float | None = None
    dry_run: bool = False
    log_level: str = "INFO"
    json_logging: bool = False
    state_dir: Path = Field(default_factory=lambda: Path.home() / ".momops")
    pricing_mode: str = "static"
    deployment_timeout: int = 1800  # 30 minutes

    def model_post_init(self, __context: Any) -> None:
        """Post-initialization validation."""
        # Ensure state directory exists
        self.state_dir.mkdir(parents=True, exist_ok=True)

        # Configure logging with settings
        configure_logging(
            level=self.log_level,
            json_format=self.json_logging,
            log_file=self.state_dir / "momops.log",
        )


@lru_cache(maxsize=1)
def get_settings() -> MomOpsSettings:
    """Return cached settings singleton."""
    return MomOpsSettings()


def reload_settings() -> MomOpsSettings:
    """Force reload of settings (clears cache)."""
    get_settings.cache_clear()
    return get_settings()
