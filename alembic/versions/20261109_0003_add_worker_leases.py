"""Add worker leases and heartbeat metadata to audit records.

Revision ID: 20261109_0003
Revises: 20261029_0002
Create Date: 2026-11-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261109_0003"
down_revision: Union[str, None] = "20261029_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("audit_records", sa.Column("worker_id", sa.String(length=128), nullable=True))
    op.add_column("audit_records", sa.Column("lease_token", sa.String(length=36), nullable=True))
    op.add_column(
        "audit_records",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_audit_records_status_heartbeat_at",
        "audit_records",
        ["status", "heartbeat_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_audit_records_status_heartbeat_at", table_name="audit_records")
    op.drop_column("audit_records", "heartbeat_at")
    op.drop_column("audit_records", "lease_token")
    op.drop_column("audit_records", "worker_id")
