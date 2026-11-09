"""Add human review state and append-only review history.

Revision ID: 20261109_0010
Revises: 20261109_0008
Create Date: 2026-11-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261109_0010"
down_revision: Union[str, None] = "20261109_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_records",
        sa.Column(
            "review_status",
            sa.String(length=24),
            nullable=False,
            server_default="not_required",
        ),
    )
    op.add_column(
        "audit_records",
        sa.Column("reviewer_user_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "audit_records",
        sa.Column("human_review_decision", sa.String(length=24), nullable=True),
    )
    op.add_column(
        "audit_records",
        sa.Column("human_review_comment", sa.Text(), nullable=True),
    )
    op.add_column(
        "audit_records",
        sa.Column("human_reviewed_by_user_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "audit_records",
        sa.Column("human_reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_audit_records_reviewer_user_id_user_accounts",
        "audit_records",
        "user_accounts",
        ["reviewer_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_audit_records_human_reviewed_by_user_id_user_accounts",
        "audit_records",
        "user_accounts",
        ["human_reviewed_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_audit_records_review_status_created_at",
        "audit_records",
        ["review_status", "created_at"],
    )
    op.create_index(
        "ix_audit_records_reviewer_status",
        "audit_records",
        ["reviewer_user_id", "review_status"],
    )

    op.create_table(
        "human_review_events",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("audit_id", sa.String(length=36), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("from_status", sa.String(length=24), nullable=True),
        sa.Column("to_status", sa.String(length=24), nullable=True),
        sa.Column("decision", sa.String(length=24), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["audit_id"], ["audit_records.id"],
            name="fk_human_review_events_audit_id_audit_records",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["user_accounts.id"],
            name="fk_human_review_events_actor_user_id_user_accounts",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_human_review_events_audit_created",
        "human_review_events",
        ["audit_id", "created_at"],
    )
    op.create_index(
        "ix_human_review_events_actor_created",
        "human_review_events",
        ["actor_user_id", "created_at"],
    )

    # Existing completed audits which already called for human review are
    # backfilled into the queue. Older results remain available to admin/reviewer.
    op.execute(
        """
        UPDATE audit_records
        SET review_status = 'pending'
        WHERE status = 'completed'
          AND (
              verdict = 'manual_review_required'
              OR decision = 'REVIEW'
          )
        """
    )


def downgrade() -> None:
    op.drop_index("ix_human_review_events_actor_created", table_name="human_review_events")
    op.drop_index("ix_human_review_events_audit_created", table_name="human_review_events")
    op.drop_table("human_review_events")
    op.drop_index("ix_audit_records_reviewer_status", table_name="audit_records")
    op.drop_index("ix_audit_records_review_status_created_at", table_name="audit_records")
    op.drop_constraint(
        "fk_audit_records_human_reviewed_by_user_id_user_accounts",
        "audit_records",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_audit_records_reviewer_user_id_user_accounts",
        "audit_records",
        type_="foreignkey",
    )
    op.drop_column("audit_records", "human_reviewed_at")
    op.drop_column("audit_records", "human_reviewed_by_user_id")
    op.drop_column("audit_records", "human_review_comment")
    op.drop_column("audit_records", "human_review_decision")
    op.drop_column("audit_records", "reviewer_user_id")
    op.drop_column("audit_records", "review_status")
