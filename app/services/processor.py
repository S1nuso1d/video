import logging
from datetime import datetime, timezone

from app.core.database import SessionLocal
from app.models.audit import AuditRecord
from app.services.gemini_auditor import (
    GeminiAuditError,
    GeminiConfigurationError,
    analyze_youtube_video,
)

logger = logging.getLogger(__name__)


def process_audit(audit_id: str) -> None:
    """Run one audit, save its result, and release failed jobs back to the worker loop."""
    with SessionLocal() as db:
        record = db.get(AuditRecord, audit_id)
        if record is None:
            logger.error("Audit record %s disappeared before processing", audit_id)
            return
        youtube_url = record.youtube_url
        content_type = record.content_type

    try:
        report = analyze_youtube_video(youtube_url, content_type)
        report_dict = report.model_dump(mode="json")
        with SessionLocal() as db:
            record = db.get(AuditRecord, audit_id)
            if record is None:
                return
            record.report = report_dict
            record.verdict = report.verdict.value
            record.summary = report.summary
            record.detected_defects = [
                item.model_dump(mode="json") for item in report.detected_defects
            ]
            record.markup_comment = report.markup_comment
            record.status = "completed"
            record.error_message = None
            record.completed_at = datetime.now(timezone.utc)
            record.updated_at = datetime.now(timezone.utc)
            db.commit()
        logger.info("Audit %s completed with verdict=%s", audit_id, report.verdict.value)
    except Exception as exc:
        logger.exception("Audit %s failed", audit_id)
        with SessionLocal() as db:
            record = db.get(AuditRecord, audit_id)
            if record is not None:
                record.status = "failed"
                # Only expose deliberately prepared, safe messages in the API response.
                if isinstance(exc, (GeminiAuditError, GeminiConfigurationError)):
                    record.error_message = str(exc)[:500]
                else:
                    record.error_message = (
                        "Не удалось завершить анализ. Проверьте настройки и журнал Worker."
                    )
                record.completed_at = datetime.now(timezone.utc)
                record.updated_at = datetime.now(timezone.utc)
                db.commit()
