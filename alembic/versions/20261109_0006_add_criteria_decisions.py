"""Persist project criteria and explainable decisions.

Revision ID: 20261109_0006
Revises: 20261109_0005
Create Date: 2026-11-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261109_0006"
down_revision: Union[str, None] = "20261109_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_records",
        sa.Column("criteria_spec", sa.JSON(), nullable=True),
    )
    op.add_column(
        "audit_records",
        sa.Column("criteria_results", sa.JSON(), nullable=True),
    )
    op.add_column(
        "audit_records",
        sa.Column("decision", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "audit_records",
        sa.Column("decision_summary", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("audit_records", "decision_summary")
    op.drop_column("audit_records", "decision")
    op.drop_column("audit_records", "criteria_results")
    op.drop_column("audit_records", "criteria_spec")
