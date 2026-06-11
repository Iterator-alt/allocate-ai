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
    stage1_debug_mode: bool = True

    # Rate Limiting
    rate_limit_generations_per_hour: int = 20

    # LLM Settings
    llm_timeout_seconds: int = 45
    llm_max_retries: int = 3

    # Cache
    result_cache_ttl_seconds: int = 3600

    # Chat Agent Configuration
    chat_rerun_creates_new: bool = True  # True=create new run on rerun, False=update existing
    chat_agent_mode: bool = True  # True=full agent mode (tools active), False=simple mode (Q&A only)

    # Chat Compaction Configuration (internal LLM context only, chatSnapshot stays intact)
    chat_compaction_threshold: int = 20  # Compact after this many messages (= 10 exchanges)
    chat_compaction_keep_recent: int = 10  # Keep this many recent messages verbatim (= 5 exchanges)

    # Competitor Confirmation Bypass
    # When True, auto-approve all competitors after Stage 1 and proceed directly to Stage 2
    # When False, wait for user to confirm competitors via POST /runs/{id}/competitors/confirm
    bypass_competitor_confirmation: bool = False

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
