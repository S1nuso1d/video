"""Add deterministic technical metadata and findings to audit records.

Revision ID: 20261109_0004
Revises: 20261109_0003
Create Date: 2026-11-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261109_0004"
down_revision: Union[str, None] = "20261109_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_records",
        sa.Column("technical_metadata", sa.JSON(), nullable=True),
    )
    op.add_column(
        "audit_records",
        sa.Column("technical_findings", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("audit_records", "technical_findings")
    op.drop_column("audit_records", "technical_metadata")
