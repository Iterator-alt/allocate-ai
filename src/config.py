"""Application configuration using pydantic-settings."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/allocate_ai"

    # OpenAI
    openai_api_key: str = ""

    # Application
    app_env: Literal["development", "staging", "production"] = "development"
    debug: bool = True
    log_level: str = "INFO"

    # Stage 1 Debug Mode - saves intermediate results to /debug_output
    stage1_debug_mode: bool = False

    # Rate Limiting
    rate_limit_generations_per_hour: int = 20

    # LLM Settings
    llm_timeout_seconds: int = 45
    llm_max_retries: int = 3

    # Cache
    result_cache_ttl_seconds: int = 3600

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
