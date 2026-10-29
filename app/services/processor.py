import logging
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.audit import AuditRecord
from app.services.gemini_auditor import (
    GeminiAuditError,
    GeminiConfigurationError,
    analyze_youtube_video,
)

logger = logging.getLogger(__name__)


def process_audit(audit_id: str) -> None:
    """Run one audit; retry only transient provider failures with bounded backoff."""
    with SessionLocal() as db:
        record = db.get(AuditRecord, audit_id)
        if record is None:
            logger.error("Audit record %s disappeared before processing", audit_id)
            return
        youtube_url = record.youtube_url
        content_type = record.content_type
        current_attempt = int(record.attempt_count or 0) + 1
        record.attempt_count = current_attempt
        record.updated_at = datetime.now(timezone.utc)
        db.commit()

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
            record.next_retry_at = None
            record.completed_at = datetime.now(timezone.utc)
            record.updated_at = datetime.now(timezone.utc)
            db.commit()
        logger.info("Audit %s completed with verdict=%s", audit_id, report.verdict.value)
    except Exception as exc:
        now = datetime.now(timezone.utc)
        retryable = isinstance(exc, GeminiAuditError) and exc.retryable
        max_attempts = max(1, settings.audit_max_attempts)
        will_retry = retryable and current_attempt < max_attempts

        if isinstance(exc, (GeminiAuditError, GeminiConfigurationError)):
            safe_message = str(exc)[:500]
        else:
            safe_message = "Не удалось завершить анализ. Проверьте настройки и журнал Worker."

        with SessionLocal() as db:
            record = db.get(AuditRecord, audit_id)
            if record is not None:
                record.error_message = safe_message
                record.updated_at = now
                if will_retry:
                    delay = max(1, settings.audit_retry_delay_seconds) * (2 ** (current_attempt - 1))
                    record.status = "pending"
                    record.next_retry_at = now + timedelta(seconds=delay)
                    record.completed_at = None
                    logger.warning(
                        "Audit %s will retry (%s/%s) in %s seconds",
                        audit_id, current_attempt, max_attempts, delay,
                    )
                else:
                    record.status = "failed"
                    record.next_retry_at = None
                    record.completed_at = now
                db.commit()
        logger.exception("Audit %s attempt %s failed", audit_id, current_attempt)
