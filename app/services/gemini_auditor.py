import logging
from typing import Any

from google import genai
from google.genai import types

from app.core.config import settings
from app.rules.sft_stage1_ru import build_audit_prompt
from app.schemas.audit import AuditReport
from app.services.report_policy import enforce_conservative_policy

logger = logging.getLogger(__name__)

RETRYABLE_HTTP_STATUS_CODES = (408, 429, 500, 502, 503, 504)


class GeminiConfigurationError(RuntimeError):
    pass


class GeminiAuditError(RuntimeError):
    pass


def describe_gemini_error(exc: Exception) -> str:
    """Return a safe, useful message without exposing SDK request details or credentials."""
    raw_code: Any = getattr(exc, "code", None)
    try:
        code = int(raw_code) if raw_code is not None else None
    except (TypeError, ValueError):
        code = None

    if code == 503:
        return (
            "Gemini API временно перегружен (HTTP 503). Ограниченное число попыток "
            "исчерпано. Проверь статус сервиса и повтори аудит немного позже."
        )
    if code == 429:
        return (
            "Gemini API вернул HTTP 429: достигнут лимит запросов или квоты. "
            "Проверь ограничения проекта и повтори аудит позже."
        )
    if code in RETRYABLE_HTTP_STATUS_CODES:
        return (
            f"Временная ошибка Gemini API (HTTP {code}) после ограниченного числа попыток. "
            "Повтори аудит позже."
        )
    return "Не удалось получить или проверить отчёт Gemini API. Проверь журнал Worker."


def analyze_youtube_video(youtube_url: str, content_type: str) -> AuditReport:
    """Inspect a public YouTube URL and return a validated SFT report.

    Retries transient provider errors a bounded number of times. Does not download
    or persist video bytes.
    """
    if not settings.gemini_api_key.strip():
        raise GeminiConfigurationError(
            "GEMINI_API_KEY не задан. Добавь ключ в .env и перезапусти worker."
        )

    try:
        client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(
                timeout=settings.gemini_timeout_ms,
                retry_options=types.HttpRetryOptions(
                    attempts=max(1, settings.gemini_retry_attempts),
                    initial_delay=settings.gemini_retry_initial_delay,
                    max_delay=settings.gemini_retry_max_delay,
                    exp_base=2.0,
                    jitter=1.0,
                    http_status_codes=list(RETRYABLE_HTTP_STATUS_CODES),
                ),
            ),
        )
        interaction = client.interactions.create(
            model=settings.gemini_model,
            input=[
                {"type": "text", "text": build_audit_prompt(content_type)},
                {"type": "video", "uri": youtube_url},
            ],
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": AuditReport.model_json_schema(),
            },
        )
        output_text = interaction.output_text

        if not output_text:
            raise GeminiAuditError("Gemini API вернул пустой ответ.")
        report = AuditReport.model_validate_json(output_text)
        return enforce_conservative_policy(report)
    except GeminiConfigurationError:
        raise
    except GeminiAuditError:
        raise
    except Exception as exc:  # SDK/provider exception types can vary between releases.
        raw_code = getattr(exc, "code", None)
        try:
            status_code = int(raw_code) if raw_code is not None else None
        except (TypeError, ValueError):
            status_code = None

        if status_code in RETRYABLE_HTTP_STATUS_CODES:
            logger.warning(
                "Gemini API request failed with HTTP %s after bounded SDK retries",
                status_code,
            )
        else:
            logger.exception("Gemini API call failed")
        raise GeminiAuditError(describe_gemini_error(exc)) from exc
