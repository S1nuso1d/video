"""Shared cancellation checkpoints for long-running audit jobs."""

from datetime import datetime, timezone

from sqlalchemy import update

from app.core.database import SessionLocal
from app.models.audit import AuditRecord


class AuditCancelled(RuntimeError):
    """Raised after a user cancellation request has been persisted."""


class AuditLeaseLost(RuntimeError):
    """Raised when this worker no longer owns the audit lease."""


def check_audit_cancellation(audit_id: str, lease_token: str) -> None:
    """Raise promptly when cancellation is requested or this lease is stale.

    A running job moves through a cancelling state first so the worker can stop
    subprocesses, release temporary files, and delete temporary Gemini files.
    The checkpoint finalizes that transition to cancelled.
    """
    with SessionLocal() as db:
        record = db.get(AuditRecord, audit_id)
        if record is None or record.lease_token != lease_token:
            raise AuditLeaseLost("Worker lease is no longer valid.")

        if record.status == "cancelling":
            now = datetime.now(timezone.utc)
            db.execute(
                update(AuditRecord)
                .where(
                    AuditRecord.id == audit_id,
                    AuditRecord.status == "cancelling",
                    AuditRecord.lease_token == lease_token,
                )
                .values(
                    status="cancelled",
                    processing_stage=None,
                    error_message="Анализ отменён пользователем.",
                    next_retry_at=None,
                    heartbeat_at=None,
                    worker_id=None,
                    lease_token=None,
                    completed_at=now,
                    updated_at=now,
                )
            )
            db.commit()
            raise AuditCancelled("Анализ отменён пользователем.")

        if record.status != "processing":
            raise AuditLeaseLost("Audit is no longer in processing state.")
