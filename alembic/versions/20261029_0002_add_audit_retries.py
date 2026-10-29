"""Add bounded worker retry metadata to audit records.

Revision ID: 20261029_0002
Revises: 20261027_0001
Create Date: 2026-10-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261029_0002"
down_revision: Union[str, None] = "20261027_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_records",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "audit_records",
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_audit_records_status_next_retry_at",
        "audit_records",
        ["status", "next_retry_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_audit_records_status_next_retry_at", table_name="audit_records")
    op.drop_column("audit_records", "next_retry_at")
    op.drop_column("audit_records", "attempt_count")
