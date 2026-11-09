from functools import lru_cache

from pydantic import model_validator
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
    gemini_retry_attempts: int = 3
    gemini_retry_initial_delay: float = 1.0
    gemini_retry_max_delay: float = 8.0

    # Video acquisition is isolated per audit and always cleaned up by the processor.
    yt_dlp_binary: str = "yt-dlp"
    video_temp_root: str = "storage/temp"
    video_format: str = "bv*[height<=720]+ba/b[height<=720]/b"
    video_max_filesize_bytes: int = 1_500_000_000
    video_download_timeout_seconds: int = 900

    # Worker-level retries happen only after the SDK's bounded retries are exhausted.
    audit_max_attempts: int = 2
    audit_retry_delay_seconds: int = 30
    gemini_rate_limit_cooldown_seconds: int = 120

    # A worker must refresh its lease much more frequently than the lease expiry.
    worker_poll_seconds: float = 2.0
    worker_heartbeat_seconds: int = 15
    worker_lease_timeout_seconds: int = 90
    worker_recovery_interval_seconds: int = 15

    gemini_poll_seconds: int = 5
    gemini_max_wait_seconds: int = 1800
    log_level: str = "INFO"

    @model_validator(mode="after")
    def validate_worker_lease_settings(self):
        if self.worker_heartbeat_seconds < 1:
            raise ValueError("WORKER_HEARTBEAT_SECONDS must be at least 1")
        if self.worker_lease_timeout_seconds < self.worker_heartbeat_seconds * 3:
            raise ValueError(
                "WORKER_LEASE_TIMEOUT_SECONDS must be at least three times "
                "WORKER_HEARTBEAT_SECONDS"
            )
        if self.worker_recovery_interval_seconds < 1:
            raise ValueError("WORKER_RECOVERY_INTERVAL_SECONDS must be at least 1")
        return self

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
