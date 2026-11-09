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

    # Deterministic metadata extraction. Missing FFprobe is non-fatal by default
    # so existing local setups can continue to use Gemini while FFmpeg is installed.
    ffprobe_binary: str = "ffprobe"
    ffprobe_timeout_seconds: int = 30
    ffprobe_required: bool = False

    # OpenCV frame sampling. Signals remain advisory until calibrated against
    # the active dataset specification and expert-labelled examples.
    cv_analysis_required: bool = False
    cv_sample_interval_seconds: float = 0.5
    cv_max_samples: int = 1200
    cv_analysis_timeout_seconds: int = 120
    cv_resize_width: int = 320
    cv_black_luma_threshold: int = 16
    cv_black_pixel_ratio: float = 0.985
    cv_black_min_duration_seconds: float = 0.75
    cv_freeze_mean_diff_threshold: float = 0.8
    cv_freeze_changed_pixel_ratio: float = 0.002
    cv_freeze_pixel_delta: int = 3
    cv_freeze_min_duration_seconds: float = 2.5

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
        if self.ffprobe_timeout_seconds < 1:
            raise ValueError("FFPROBE_TIMEOUT_SECONDS must be at least 1")
        if self.cv_sample_interval_seconds <= 0:
            raise ValueError("CV_SAMPLE_INTERVAL_SECONDS must be greater than 0")
        if self.cv_max_samples < 2:
            raise ValueError("CV_MAX_SAMPLES must be at least 2")
        if self.cv_analysis_timeout_seconds < 1:
            raise ValueError("CV_ANALYSIS_TIMEOUT_SECONDS must be at least 1")
        if self.cv_resize_width < 64:
            raise ValueError("CV_RESIZE_WIDTH must be at least 64")
        if not 0 <= self.cv_black_luma_threshold <= 255:
            raise ValueError("CV_BLACK_LUMA_THRESHOLD must be between 0 and 255")
        if not 0 < self.cv_black_pixel_ratio <= 1:
            raise ValueError("CV_BLACK_PIXEL_RATIO must be in (0, 1]")
        if self.cv_black_min_duration_seconds <= 0:
            raise ValueError("CV_BLACK_MIN_DURATION_SECONDS must be greater than 0")
        if self.cv_freeze_mean_diff_threshold < 0:
            raise ValueError("CV_FREEZE_MEAN_DIFF_THRESHOLD must be non-negative")
        if not 0 <= self.cv_freeze_changed_pixel_ratio <= 1:
            raise ValueError("CV_FREEZE_CHANGED_PIXEL_RATIO must be between 0 and 1")
        if not 0 <= self.cv_freeze_pixel_delta <= 255:
            raise ValueError("CV_FREEZE_PIXEL_DELTA must be between 0 and 255")
        if self.cv_freeze_min_duration_seconds <= 0:
            raise ValueError("CV_FREEZE_MIN_DURATION_SECONDS must be greater than 0")
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
