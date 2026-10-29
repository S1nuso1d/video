from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables and .env."""

    project_name: str = "Video Auditor — SFT Stage 1"
    database_url: str = (
        "postgresql+psycopg://video_auditor:video_auditor_dev_password@"
        "localhost:5433/video_auditor"
    )
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    # Google Gen AI SDK timeout is specified in milliseconds.
    gemini_timeout_ms: int = 180000
    # Attempts includes the initial request made by the SDK.
    gemini_retry_attempts: int = 3
    gemini_retry_initial_delay: float = 1.0
    gemini_retry_max_delay: float = 8.0
    # Worker-level retries happen only after the SDK's bounded retries are exhausted.
    audit_max_attempts: int = 2
    audit_retry_delay_seconds: int = 30
    # If Gemini returns HTTP 429, delay all queued jobs so they share one cooldown.
    gemini_rate_limit_cooldown_seconds: int = 120
    worker_poll_seconds: float = 2.0
    stale_job_minutes: int = 240
    gemini_poll_seconds: int = 5
    gemini_max_wait_seconds: int = 1800
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
