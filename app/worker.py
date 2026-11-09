"""PostgreSQL-backed worker with leased, recoverable audit jobs."""

import logging
import os
import signal
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Event, Thread

from sqlalchemy import and_, case, or_, select, update

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging_config import configure_logging
from app.models.audit import AuditRecord
from app.services.processor import process_audit
from app.services.video_downloader import cleanup_abandoned_temp_dirs

configure_logging()
logger = logging.getLogger("video_auditor.worker")
_shutdown = False


@dataclass(frozen=True)
class WorkerClaim:
    """A time-limited claim on one audit, protected by a fencing token."""

    audit_id: str
    lease_token: str
    attempt_number: int


def recover_stale_jobs(now: datetime | None = None) -> int:
    """Recover jobs whose worker heartbeat has expired.

    A job is retried only while its configured attempt budget remains. The
    update is a single SQL statement, so a concurrent heartbeat refresh wins
    or the recovery update wins; the job cannot be half-recovered.
    """
    now = now or datetime.now(timezone.utc)
    lease_timeout = max(5, int(settings.worker_lease_timeout_seconds))
    cutoff = now - timedelta(seconds=lease_timeout)
    max_attempts = max(1, int(settings.audit_max_attempts))
    retry_delay = max(1, int(settings.audit_retry_delay_seconds))
    retryable = AuditRecord.attempt_count < max_attempts
    stale_processing = and_(
        AuditRecord.status.in_(("processing", "cancelling")),
        or_(
            AuditRecord.heartbeat_at < cutoff,
            and_(
                AuditRecord.heartbeat_at.is_(None),
                AuditRecord.started_at < cutoff,
            ),
        ),
    )

    with SessionLocal() as db:
        result = db.execute(
            update(AuditRecord)
            .where(stale_processing)
            .values(
                status=case(
                    (AuditRecord.status == "cancelling", "cancelled"),
                    (retryable, "pending"),
                    else_="failed",
                ),
                next_retry_at=case(
                    (AuditRecord.status == "cancelling", None),
                    (retryable, now + timedelta(seconds=retry_delay)),
                    else_=None,
                ),
                completed_at=case(
                    (AuditRecord.status == "cancelling", now),
                    (retryable, None),
                    else_=now,
                ),
                started_at=case(
                    (AuditRecord.status == "cancelling", AuditRecord.started_at),
                    (retryable, None),
                    else_=AuditRecord.started_at,
                ),
                processing_stage=None,
                heartbeat_at=None,
                worker_id=None,
                lease_token=None,
                error_message=case(
                    (
                        AuditRecord.status == "cancelling",
                        "Анализ отменён: worker не завершился штатно.",
                    ),
                    (
                        retryable,
                        "Worker lease expired before the audit finished. "
                        "The task was returned to the queue.",
                    ),
                    else_="Worker lease expired and the maximum number of attempts was reached.",
                ),
                updated_at=now,
            )
        )
        db.commit()
        return int(result.rowcount or 0)


def claim_next_job(worker_id: str) -> WorkerClaim | None:
    """Atomically claim the oldest eligible pending audit."""
    now = datetime.now(timezone.utc)
    lease_token = str(uuid.uuid4())
    claim: WorkerClaim | None = None

    with SessionLocal() as db:
        with db.begin():
            statement = (
                select(AuditRecord)
                .where(
                    AuditRecord.status == "pending",
                    or_(
                        AuditRecord.next_retry_at.is_(None),
                        AuditRecord.next_retry_at <= now,
                    ),
                )
                .order_by(AuditRecord.created_at.asc())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            record = db.scalar(statement)
            if record is not None:
                record.attempt_count = int(record.attempt_count or 0) + 1
                record.status = "processing"
                record.started_at = now
                record.heartbeat_at = now
                record.worker_id = worker_id
                record.lease_token = lease_token
                record.next_retry_at = None
                record.processing_stage = None
                record.completed_at = None
                record.error_message = None
                # Do not expose technical evidence from a previous attempt
                # while the current attempt is being evaluated.
                record.technical_metadata = None
                record.technical_findings = None
                record.visual_metadata = None
                record.visual_findings = None
                record.updated_at = now
                claim = WorkerClaim(
                    audit_id=record.id,
                    lease_token=lease_token,
                    attempt_number=record.attempt_count,
                )

    return claim


def refresh_job_lease(audit_id: str, lease_token: str) -> bool:
    """Refresh a lease only if this worker still owns the processing job."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        result = db.execute(
            update(AuditRecord)
            .where(
                AuditRecord.id == audit_id,
                AuditRecord.status.in_(("processing", "cancelling")),
                AuditRecord.lease_token == lease_token,
            )
            .values(heartbeat_at=now)
        )
        db.commit()
        return result.rowcount == 1


def _heartbeat_loop(
    claim: WorkerClaim,
    stop_event: Event,
) -> None:
    interval = max(1, int(settings.worker_heartbeat_seconds))
    while not stop_event.wait(interval):
        try:
            if not refresh_job_lease(claim.audit_id, claim.lease_token):
                logger.warning(
                    "Worker lost lease for audit %s; result will not be committed",
                    claim.audit_id,
                )
                return
        except Exception:
            # A temporary DB outage must not crash the processing thread. If
            # the lease expires, the database recovery path will reclaim it.
            logger.exception("Could not refresh lease for audit %s", claim.audit_id)


def _process_claim(claim: WorkerClaim) -> None:
    stop_event = Event()
    heartbeat_thread = Thread(
        target=_heartbeat_loop,
        args=(claim, stop_event),
        name=f"audit-heartbeat-{claim.audit_id[:8]}",
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        process_audit(claim.audit_id, claim.lease_token)
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=max(2, int(settings.worker_heartbeat_seconds) + 1))


def _make_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"[:128]


def _request_shutdown(signum, frame) -> None:
    global _shutdown
    _shutdown = True
    logger.info("Shutdown requested; finishing current audit before exit")


def run() -> None:
    global _shutdown
    _shutdown = False
    signal.signal(signal.SIGINT, _request_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _request_shutdown)

    worker_id = _make_worker_id()
    removed = cleanup_abandoned_temp_dirs()
    if removed:
        logger.warning("Removed %s abandoned temporary video directories", removed)

    logger.info(
        "Worker started; id=%s poll_interval=%.1fs heartbeat_interval=%ss lease_timeout=%ss",
        worker_id,
        settings.worker_poll_seconds,
        settings.worker_heartbeat_seconds,
        settings.worker_lease_timeout_seconds,
    )
    last_recovery = 0.0
    recovery_interval = max(5, int(settings.worker_recovery_interval_seconds))

    while not _shutdown:
        try:
            monotonic_now = time.monotonic()
            if monotonic_now - last_recovery >= recovery_interval:
                recovered = recover_stale_jobs()
                if recovered:
                    logger.warning("Recovered %s expired audit leases", recovered)
                last_recovery = monotonic_now

            claim = claim_next_job(worker_id)
            if claim is not None:
                logger.info(
                    "Claimed audit %s (attempt %s, worker=%s)",
                    claim.audit_id,
                    claim.attempt_number,
                    worker_id,
                )
                _process_claim(claim)
            else:
                time.sleep(max(0.25, settings.worker_poll_seconds))
        except Exception:
            logger.exception("Worker loop error; will retry")
            time.sleep(max(1.0, settings.worker_poll_seconds))

    logger.info("Worker stopped; id=%s", worker_id)


if __name__ == "__main__":
    run()
