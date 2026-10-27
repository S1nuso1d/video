import logging

from google import genai

from app.core.config import settings
from app.rules.sft_stage1_ru import build_audit_prompt
from app.schemas.audit import AuditReport
from app.services.report_policy import enforce_conservative_policy

logger = logging.getLogger(__name__)


class GeminiConfigurationError(RuntimeError):
    pass


class GeminiAuditError(RuntimeError):
    pass


def analyze_youtube_video(youtube_url: str, content_type: str) -> AuditReport:
    """Ask Gemini to inspect a public YouTube URL and return a validated SFT report.

    Uses Gemini's documented YouTube URL input. It does not download or persist video bytes.
    """
    if not settings.gemini_api_key.strip():
        raise GeminiConfigurationError(
            "GEMINI_API_KEY не задан. Добавьте ключ в .env и перезапустите worker."
        )

    try:
        client = genai.Client(api_key=settings.gemini_api_key)
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
    except Exception as exc:  # SDK/network/provider errors vary by SDK release.
        logger.exception("Gemini API call failed")
        raise GeminiAuditError("Не удалось получить или проверить отчёт Gemini API.") from exc
