"""one active session per account: users.session_version — additive

Each login increments users.session_version and writes it into the new session token ("sv" claim).
A request whose token carries an older number is refused, so signing in on another device signs the
previous session out. Existing tokens carry no "sv" and count as 0, so sessions open at deploy time keep
working until that account signs in again.

Revision ID: 0042
Revises: 0041
Create Date: 2026-11-13
"""
import sqlalchemy as sa

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("session_version", sa.Integer(), nullable=False, server_default=sa.text("0")))


def downgrade() -> None:
    op.drop_column("users", "session_version")
