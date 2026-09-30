"""user security events — append-only record of administrative password resets — additive

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-30

One new table; no existing table or user record is touched.

An ADMIN may reset another account's password. Each reset appends one row:
the target account, the action (PASSWORD_RESET — the only action allowed
here), and who did it (account id, username and role, taken from the
authenticated session) and when. Never the password, its confirmation or its
hash. Not a general audit framework: a new action needs a deliberate
migration to widen the check constraint.

Revises 0027, the deployed head. The unreleased 0025 (identifier resolution
records) also descends from 0024 and is re-parented when it is released.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_security_events",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("target_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_username", sa.String(length=64), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_username", sa.String(length=64), nullable=False),
        sa.Column("actor_role", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("action IN ('PASSWORD_RESET')", name="ck_user_security_events_action"),
    )
    op.create_index("idx_user_security_events_target", "user_security_events", ["target_user_id"])
    op.create_index("idx_user_security_events_created", "user_security_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("user_security_events")
