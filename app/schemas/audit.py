from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.youtube_urls import normalize_youtube_url, extract_video_id


class ContentType(str, Enum):
    UNKNOWN = "unknown"
    LIVE_ACTION = "live_action"
    ANIMATION = "animation"


class Verdict(str, Enum):
    CANDIDATE_ACCEPTED = "candidate_accepted"
    CANDIDATE_REJECTED = "candidate_rejected"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


class ReviewCoverage(str, Enum):
    ADEQUATE_FOR_PRELIMINARY_ASSESSMENT = "adequate_for_preliminary_assessment"
    LIMITED = "limited"
    NOT_ASSESSABLE = "not_assessable"


class EvidenceConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class DefectSeverity(str, Enum):
    CRITICAL = "critical"
    NON_CRITICAL = "non_critical"
    UNCERTAIN = "uncertain"


class DefectCategory(str, Enum):
    WATERMARK_OR_OVERLAY = "watermark_or_overlay"
    COLLAGE = "collage"
    NON_MONOCHROME_BORDER = "non_monochrome_border"
    UNCLEAR_OR_FRACTAL_CONTENT = "unclear_or_fractal_content"
    EXCESSIVE_CAMERA_SHAKE = "excessive_camera_shake"
    FREQUENT_CUTS_UNDER_5S = "frequent_cuts_under_5s"
    BLOCKINESS_PIXELATION_POSTERIZATION = "blockiness_pixelation_posterization"
    LOW_COMPLEXITY_OR_MONOTONY = "low_complexity_or_monotony"
    STRONG_BACKGROUND_BLUR = "strong_background_blur"
    LENS_DROPS_OBSTRUCTING_SUBJECT = "lens_drops_obstructing_subject"
    DOUBLE_EXPOSURE = "double_exposure"
    FOREGROUND_ARTIFACT = "foreground_artifact"
    MIRRORED_OR_FLIPPED = "mirrored_or_flipped"
    GHOSTING_OR_CONTOUR_FLICKER = "ghosting_or_contour_flicker"
    FULL_FRAME_BLUR_OVER_1S = "full_frame_blur_over_1s"
    SEVERE_COLOR_DISTORTION = "severe_color_distortion"
    LENS_FLARE_OR_LIGHT_FLICKER = "lens_flare_or_light_flicker"
    AI_GENERATED_OR_EDITED = "ai_generated_or_edited"
    SEVERE_OVEREXPOSURE_OR_DARKNESS = "severe_overexposure_or_darkness"
    VIDEO_UNDER_5_SECONDS = "video_under_5_seconds"
    INAPPROPRIATE_SPEED_CHANGE = "inappropriate_speed_change"
    ASPECT_RATIO_DISTORTION = "aspect_ratio_distortion"
    STUTTER_OR_LOW_FPS = "stutter_or_low_fps"
    OLD_FILM_EFFECT = "old_film_effect"
    COMPRESSION_ARTIFACTS = "compression_artifacts"
    PHOTO_SLIDESHOW = "photo_slideshow"
    UNNATURAL_GREEN_SCREEN_COMPOSITE = "unnatural_green_screen_composite"
    LOW_DYNAMICS = "low_dynamics"
    AUDIO_DISTORTION = "audio_distortion"
    AUDIO_BACKGROUND_NOISE = "audio_background_noise"
    AUDIO_DESYNC_OR_ECHO = "audio_desync_or_echo"
    DUBBED_OR_MISMATCHED_AUDIO = "dubbed_or_mismatched_audio"
    VOICEOVER_MASKS_TARGET_SOUND = "voiceover_masks_target_sound"
    TARGET_SOUND_SOURCE_NOT_VISIBLE = "target_sound_source_not_visible"
    ANIMATION_ANATOMY_ERROR = "animation_anatomy_error"
    ANIMATION_LIP_SYNC = "animation_lip_sync"
    FLAT_2D_ANIMATION = "flat_2d_animation"
    ANIMATION_AESTHETIC_OR_STYLE = "animation_aesthetic_or_style"
    EXCLUDED_CONTENT_CATEGORY = "excluded_content_category"
    OTHER = "other"


class AuditCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    youtube_url: str = Field(min_length=11, max_length=2048, description="Публичная ссылка на видео YouTube")
    content_type: ContentType = Field(default=ContentType.UNKNOWN, description="Тип контента; для мультфильмов выберите animation")

    @field_validator("youtube_url")
    @classmethod
    def validate_youtube_url(cls, value: str) -> str:
        return normalize_youtube_url(value)

    @property
    def video_id(self) -> str:
        return extract_video_id(self.youtube_url)


class DetectedDefect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: DefectCategory
    severity: DefectSeverity = Field(description="critical, non_critical или uncertain")
    start_time: str | None = Field(description="Ориентировочная временная метка MM:SS или HH:MM:SS; null, если не уверены")
    end_time: str | None = Field(description="Ориентировочный конец интервала; null, если не уверенны")
    estimated_video_fraction: float | None = Field(default=None, ge=0.0, le=1.0, description="Оценка доли длительности всего ролика, затронутая дефектом; null при отсутствии основания")
    confidence: EvidenceConfidence
    description: str = Field(min_length=3, max_length=1000, description="Наблюдаемый признак и почему он нарушает чек-лист")


class AuditReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    summary: str = Field(min_length=3, max_length=1500)
    review_coverage: ReviewCoverage = Field(description="Насколько модель смогла оценить весь ролик для предварительной проверки")
    detected_defects: list[DetectedDefect] = Field(default_factory=list)
    not_assessed: list[str] = Field(default_factory=list, description="Что не получилось надёжно проверить")
    manual_review_reasons: list[str] = Field(default_factory=list)
    markup_comment: str = Field(max_length=1000, description="Краткий комментарий на русском для интерфейса разметки")


class AuditResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    youtube_url: str
    video_id: str
    content_type: ContentType
    status: str
    verdict: Verdict | None = None
    summary: str | None = None
    report: AuditReport | None = None
    detected_defects: list[dict] | None = None
    markup_comment: str | None = None
    error_message: str | None = None
