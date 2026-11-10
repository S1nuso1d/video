"""Add audit cancellation state telemetry.

Revision ID: 20261110_0011
Revises: 20261109_0010
Create Date: 2026-11-10
"""

from alembic import op
import sqlalchemy as sa


revision = "20261110_0011"
down_revision = "20261109_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_records",
        sa.Column("processing_stage", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("audit_records", "processing_stage")
