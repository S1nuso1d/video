"""Persist Gemini token usage, estimated costs, and processing timings.

Revision ID: 20261109_0007
Revises: 20261109_0006
Create Date: 2026-11-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261109_0007"
down_revision: Union[str, None] = "20261109_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("audit_records", sa.Column("gemini_usage", sa.JSON(), nullable=True))
    op.add_column(
        "audit_records",
        sa.Column("gemini_cost_usd", sa.Float(), nullable=True),
    )
    op.add_column(
        "audit_records",
        sa.Column("gemini_cost_rub", sa.Float(), nullable=True),
    )
    op.add_column(
        "audit_records",
        sa.Column("processing_metrics", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("audit_records", "processing_metrics")
    op.drop_column("audit_records", "gemini_cost_rub")
    op.drop_column("audit_records", "gemini_cost_usd")
    op.drop_column("audit_records", "gemini_usage")
