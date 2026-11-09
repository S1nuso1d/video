"""Add OpenCV visual sampling metadata and findings.

Revision ID: 20261109_0005
Revises: 20261109_0004
Create Date: 2026-11-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261109_0005"
down_revision: Union[str, None] = "20261109_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_records",
        sa.Column("visual_metadata", sa.JSON(), nullable=True),
    )
    op.add_column(
        "audit_records",
        sa.Column("visual_findings", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("audit_records", "visual_findings")
    op.drop_column("audit_records", "visual_metadata")
