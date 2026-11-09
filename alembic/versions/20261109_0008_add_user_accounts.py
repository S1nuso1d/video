"""Add user accounts, roles, and audit ownership.

Revision ID: 20261109_0008
Revises: 20261109_0007
Create Date: 2026-11-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261109_0008"
down_revision: Union[str, None] = "20261109_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_accounts",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False, server_default="operator"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("email", name="uq_user_accounts_email"),
    )
    op.create_index(
        "ix_user_accounts_role_active", "user_accounts", ["role", "is_active"]
    )
    op.add_column(
        "audit_records",
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
    )
    op.create_foreign_key(
        "fk_audit_records_created_by_user_id_user_accounts",
        "audit_records",
        "user_accounts",
        ["created_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_audit_records_created_by_user_id",
        "audit_records",
        ["created_by_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_records_created_by_user_id", table_name="audit_records")
    op.drop_constraint(
        "fk_audit_records_created_by_user_id_user_accounts",
        "audit_records",
        type_="foreignkey",
    )
    op.drop_column("audit_records", "created_by_user_id")
    op.drop_index("ix_user_accounts_role_active", table_name="user_accounts")
    op.drop_table("user_accounts")
