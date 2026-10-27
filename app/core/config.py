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
    worker_poll_seconds: float = 2.0
    stale_job_minutes: int = 240
    gemini_poll_seconds: float = 5.0
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
