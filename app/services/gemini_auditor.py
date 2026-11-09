import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from app.core.config import settings
from app.rules.sft_stage1_ru import build_audit_prompt
from app.schemas.audit import AuditReport
from app.services.report_policy import enforce_conservative_policy
from app.services.gemini_costs import normalise_interaction_usage

logger = logging.getLogger(__name__)

RETRYABLE_HTTP_STATUS_CODES = (408, 429, 500, 502, 503, 504)


class GeminiConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class GeminiAuditResult:
    report: AuditReport
    usage: dict[str, int | None] | None
    request_made: bool = True


class GeminiAuditError(RuntimeError):
    """Safe, user-facing error raised when Gemini cannot produce a valid report."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        usage: dict[str, int | None] | None = None,
        request_made: bool = False,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.usage = usage
        self.request_made = request_made


def _status_code(exc: Exception) -> int | None:
    raw_code: Any = getattr(exc, "code", None)
    try:
        return int(raw_code) if raw_code is not None else None
    except (TypeError, ValueError):
        return None


def is_retryable_gemini_error(exc: Exception) -> bool:
    code = _status_code(exc)
    if code in RETRYABLE_HTTP_STATUS_CODES:
        return True
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if any(token in name for token in ("timeout", "connection", "temporarilyunavailable")):
        return True
    return any(
        token in text
        for token in (
            "read timeout",
            "timed out",
            "server disconnected",
            "connection reset",
            "connection aborted",
            "temporarily unavailable",
        )
    )


def describe_gemini_error(exc: Exception) -> str:
    code = _status_code(exc)
    name = type(exc).__name__.lower()

    if code == 503:
        return "Gemini API временно перегружен (HTTP 503). Сервис не ответил после повторных попыток."
    if code == 429:
        return "Gemini API вернул HTTP 429: достигнут лимит запросов или квоты. Проверь ограничения проекта."
    if code in RETRYABLE_HTTP_STATUS_CODES:
        return f"Временная ошибка Gemini API (HTTP {code}). Попробуем повторить анализ автоматически."
    if "timeout" in name or "timeout" in str(exc).lower() or "timed out" in str(exc).lower():
        return "Gemini API не ответил вовремя. Анализ будет повторён автоматически."
    if "connection" in name or any(
        token in str(exc).lower()
        for token in ("server disconnected", "connection reset", "connection aborted")
    ):
        return "Соединение с Gemini API прервалось. Анализ будет повторён автоматически."
    return "Не удалось получить или проверить отчёт Gemini API. Проверь журнал Worker."


def _make_client() -> genai.Client:
    return genai.Client(
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


def _wait_for_uploaded_file(client: genai.Client, uploaded_file):
    """Wait until Gemini finishes processing an uploaded video."""
    deadline = time.monotonic() + settings.gemini_max_wait_seconds
    current = uploaded_file
    while True:
        state = getattr(current, "state", None)
        state_name = str(getattr(state, "name", state or "")).upper()

        if not state_name or state_name == "ACTIVE":
            return current
        if state_name in {"FAILED", "ERROR"}:
            raise GeminiAuditError("Gemini не смог обработать загруженный видеофайл.")
        if time.monotonic() >= deadline:
            raise GeminiAuditError(
                "Gemini слишком долго обрабатывает загруженный видеофайл.",
                retryable=True,
            )

        time.sleep(max(0.25, settings.gemini_poll_seconds))
        current = client.files.get(name=current.name)


def analyze_youtube_video(
    youtube_url: str,
    content_type: str,
    *,
    local_video_path: Path | None = None,
) -> AuditReport:
    """Inspect a video and return a validated SFT report.

    A local video is uploaded to Gemini, waited until ready, analyzed, and then
    deleted from Gemini. The caller owns deletion of the local temporary file.
    """
    if not settings.gemini_api_key.strip():
        raise GeminiConfigurationError(
            "GEMINI_API_KEY не задан. Добавь ключ в .env и перезапусти worker."
        )

    client = _make_client()
    uploaded_file = None
    interaction_usage: dict[str, int | None] | None = None
    request_made = False
    try:
        if local_video_path is not None:
            uploaded_file = client.files.upload(file=local_video_path)
            uploaded_file = _wait_for_uploaded_file(client, uploaded_file)
            video_input = {"type": "video", "uri": uploaded_file.uri}
        else:
            video_input = {"type": "video", "uri": youtube_url}

        interaction = client.interactions.create(
            model=settings.gemini_model,
            input=[
                {"type": "text", "text": build_audit_prompt(content_type)},
                video_input,
            ],
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": AuditReport.model_json_schema(),
            },
        )
        request_made = True
        interaction_usage = normalise_interaction_usage(
            getattr(interaction, "usage", None)
        )
        output_text = interaction.output_text
        if not output_text:
            raise GeminiAuditError(
                "Gemini API вернул пустой ответ.",
                usage=interaction_usage,
                request_made=True,
            )
        report = AuditReport.model_validate_json(output_text)
        return GeminiAuditResult(
            report=enforce_conservative_policy(report),
            usage=interaction_usage,
            request_made=True,
        )
    except (GeminiConfigurationError, GeminiAuditError) as exc:
        if isinstance(exc, GeminiAuditError):
            if exc.usage is None:
                exc.usage = interaction_usage
            exc.request_made = exc.request_made or request_made
        raise
    except Exception as exc:
        retryable = is_retryable_gemini_error(exc)
        status_code = _status_code(exc)
        if retryable:
            logger.warning(
                "Transient Gemini API failure (%s, HTTP %s)",
                type(exc).__name__,
                status_code if status_code is not None else "unknown",
            )
        else:
            logger.exception("Gemini API call failed")
        raise GeminiAuditError(
            describe_gemini_error(exc),
            retryable=retryable,
            status_code=status_code,
            usage=interaction_usage,
            request_made=request_made,
        ) from exc
    finally:
        if uploaded_file is not None:
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:
                logger.warning("Could not delete temporary Gemini file", exc_info=True)
