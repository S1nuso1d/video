from datetime import datetime, timezone
import uuid

from sqlalchemy import DateTime, Float, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AuditRecord(Base):
    __tablename__ = "audit_records"
    __table_args__ = (
        Index("ix_audit_records_status_created_at", "status", "created_at"),
        Index("ix_audit_records_status_next_retry_at", "status", "next_retry_at"),
        Index("ix_audit_records_status_heartbeat_at", "status", "heartbeat_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    youtube_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    video_id: Mapped[str] = mapped_column(String(32), nullable=False)
    content_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unknown"
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    verdict: Mapped[str | None] = mapped_column(String(32), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    detected_defects: Mapped[list | None] = mapped_column(JSON, nullable=True)
    technical_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    technical_findings: Mapped[list | None] = mapped_column(JSON, nullable=True)
    visual_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    visual_findings: Mapped[list | None] = mapped_column(JSON, nullable=True)
    criteria_spec: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    criteria_results: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    decision_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    gemini_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    gemini_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    gemini_cost_rub: Mapped[float | None] = mapped_column(Float, nullable=True)
    processing_metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    markup_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Lease fields prevent stale workers from refreshing or overwriting a newer
    # attempt. The token is rotated every time a pending job is claimed.
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
