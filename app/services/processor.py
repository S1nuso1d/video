import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, update

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
    """Run one audit; back off queued work when Gemini reports a rate limit."""
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
        rate_limited = isinstance(exc, GeminiAuditError) and exc.status_code == 429
        max_attempts = max(1, settings.audit_max_attempts)
        will_retry = retryable and current_attempt < max_attempts

        if isinstance(exc, (GeminiAuditError, GeminiConfigurationError)):
            safe_message = str(exc)[:500]
        else:
            safe_message = "Не удалось завершить анализ. Проверьте настройки и журнал Worker."

        # A 429 may be caused by the project's shared token/request limits. Pause
        # every queued item so the worker does not immediately send more requests.
        cooldown_seconds = max(30, int(settings.gemini_rate_limit_cooldown_seconds))
        cooldown_until = now + timedelta(seconds=cooldown_seconds) if rate_limited else None

        with SessionLocal() as db:
            record = db.get(AuditRecord, audit_id)
            if record is not None:
                record.error_message = safe_message
                record.updated_at = now
                if will_retry:
                    base_delay = max(1, settings.audit_retry_delay_seconds) * (
                        2 ** (current_attempt - 1)
                    )
                    delay = max(base_delay, cooldown_seconds) if rate_limited else base_delay
                    record.status = "pending"
                    record.next_retry_at = now + timedelta(seconds=delay)
                    record.completed_at = None
                else:
                    record.status = "failed"
                    record.next_retry_at = None
                    record.completed_at = now

            if rate_limited and cooldown_until is not None:
                deferred_retry_at = case(
                    (AuditRecord.next_retry_at.is_(None), cooldown_until),
                    (AuditRecord.next_retry_at < cooldown_until, cooldown_until),
                    else_=AuditRecord.next_retry_at,
                )
                result = db.execute(
                    update(AuditRecord)
                    .where(
                        AuditRecord.status == "pending",
                        AuditRecord.id != audit_id,
                    )
                    .values(
                        next_retry_at=deferred_retry_at,
                        updated_at=now,
                    )
                )
                deferred_count = int(result.rowcount or 0)
                logger.warning(
                    "Gemini HTTP 429: deferred %s queued audits for at least %s seconds",
                    deferred_count,
                    cooldown_seconds,
                )
            db.commit()

        if retryable:
            logger.warning(
                "Audit %s attempt %s/%s failed: %s",
                audit_id,
                current_attempt,
                max_attempts,
                safe_message,
            )
        else:
            logger.exception("Audit %s attempt %s failed", audit_id, current_attempt)
