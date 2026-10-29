"""Manually requeue one stuck/failed audit after stopping the worker.

Usage:
    python -m app.requeue_audit --id <AUDIT_ID>
"""
import argparse
import sys
from datetime import datetime, timezone

from app.core.database import SessionLocal
from app.models.audit import AuditRecord


def main() -> int:
    parser = argparse.ArgumentParser(description="Requeue a stuck or failed Video Auditor task.")
    parser.add_argument("--id", required=True, help="Audit UUID to requeue")
    args = parser.parse_args()

    with SessionLocal() as db:
        record = db.get(AuditRecord, args.id)
        if record is None:
            print(f"Audit not found: {args.id}")
            return 1

        if record.status not in {"processing", "failed"}:
            print(
                f"Audit status is {record.status!r}; only processing or failed audits can be requeued."
            )
            return 2

        record.status = "pending"
        record.verdict = None
        record.summary = None
        record.report = None
        record.detected_defects = None
        record.markup_comment = None
        record.error_message = None
        record.started_at = None
        record.completed_at = None
        record.updated_at = datetime.now(timezone.utc)
        db.commit()

    print(f"Audit {args.id} reset to pending. Start the worker to process it again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
