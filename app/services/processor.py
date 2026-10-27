import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.audit import AuditRecord
from app.services.gemini_auditor import analyze_youtube_video

logger = logging.getLogger(__name__)


def process_audit(audit_id: str) -> None:
    """Run one audit and persist the final report. Safe to call from the worker only."""
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
            record.detected_defects = [item.model_dump(mode="json") for item in report.detected_defects]
            record.markup_comment = report.markup_comment
            record.status = "completed"
            record.error_message = None
            record.completed_at = datetime.now(timezone.utc)
            db.commit()
        logger.info("Audit %s completed with verdict=%s", audit_id, report.verdict.value)
    except Exception as exc:
        logger.exception("Audit %s failed", audit_id)
        with SessionLocal() as db:
            record = db.get(AuditRecord, audit_id)
            if record is not None:
                record.status = "failed"
                # Keep details in server logs; do not expose provider internals/secrets via API.
                record.error_message = "Не удалось завершить анализ. Проверьте настройки и журнал worker."
                record.completed_at = datetime.now(timezone.utc)
                db.commit()
