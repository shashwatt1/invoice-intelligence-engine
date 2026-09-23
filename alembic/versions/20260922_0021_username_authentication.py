"""users.email -> users.username — internal-app username authentication

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-22

Converts login identity from email to username. There are no real
production accounts to preserve — the only rows in `users` are the
temporary email-based test/dev accounts from P3 verification, which the
operator has explicitly said not to depend on and will replace via
scripts/create_user.py. This migration clears them (a plain DELETE, not
TRUNCATE, so documents.uploaded_by_user_id's ON DELETE SET NULL fires
normally and no document row is touched — only its ownership FK reverts
to NULL, exactly like every other pre-authentication document).

Case-insensitive uniqueness is enforced at the database level with a
functional unique index on lower(username), not a plain UNIQUE
constraint on the raw column — correct even if some future code path
ever stored a non-normalized value. documents.uploaded_by_user_id is
untouched: ownership stays the immutable UUID FK, never the username.
"""

import sqlalchemy as sa

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM users")
    op.drop_index("idx_users_email", table_name="users")
    op.drop_column("users", "email")
    op.add_column("users", sa.Column("username", sa.String(length=64), nullable=False))
    op.create_index(
        "ix_users_username_lower", "users", [sa.text("lower(username)")], unique=True,
    )


def downgrade() -> None:
    op.execute("DELETE FROM users")
    op.drop_index("ix_users_username_lower", table_name="users")
    op.drop_column("users", "username")
    op.add_column("users", sa.Column("email", sa.String(length=255), nullable=False))
    op.create_index("idx_users_email", "users", ["email"], unique=True)
