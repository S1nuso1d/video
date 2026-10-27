"""PostgreSQL-backed polling worker. Run separately with `python -m app.worker`."""
import logging
import signal
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.core.config import settings
from app.core.logging_config import configure_logging
from app.core.database import SessionLocal
from app.models.audit import AuditRecord
from app.services.processor import process_audit

configure_logging()
logger = logging.getLogger("video_auditor.worker")
_shutdown = False


def _request_shutdown(signum, frame) -> None:
    global _shutdown
    _shutdown = True
    logger.info("Shutdown requested; finishing current audit before exit")


def recover_stale_jobs() -> int:
    """Re-queue jobs left in processing by a crashed worker after a long timeout."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.stale_job_minutes)
    with SessionLocal() as db:
        result = db.execute(
            update(AuditRecord)
            .where(AuditRecord.status == "processing", AuditRecord.started_at < cutoff)
            .values(status="pending", started_at=None, error_message=None)
        )
        db.commit()
        return int(result.rowcount or 0)


def claim_next_job() -> str | None:
    with SessionLocal() as db:
        with db.begin():
            statement = (
                select(AuditRecord)
                .where(AuditRecord.status == "pending")
                .order_by(AuditRecord.created_at.asc())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            record = db.scalar(statement)
            if record is None:
                return None
            record.status = "processing"
            record.started_at = datetime.now(timezone.utc)
            record.updated_at = datetime.now(timezone.utc)
            return record.id


def run() -> None:
    signal.signal(signal.SIGINT, _request_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _request_shutdown)

    logger.info("Worker started; poll interval %.1f seconds", settings.worker_poll_seconds)
    last_recovery = 0.0
    while not _shutdown:
        try:
            now = time.monotonic()
            if now - last_recovery > 60:
                recovered = recover_stale_jobs()
                if recovered:
                    logger.warning("Re-queued %s stale audit jobs", recovered)
                last_recovery = now
            audit_id = claim_next_job()
            if audit_id:
                logger.info("Processing audit %s", audit_id)
                process_audit(audit_id)
            else:
                time.sleep(max(0.25, settings.worker_poll_seconds))
        except Exception:
            logger.exception("Worker loop error; will retry")
            time.sleep(max(1.0, settings.worker_poll_seconds))
    logger.info("Worker stopped")


if __name__ == "__main__":
    run()
