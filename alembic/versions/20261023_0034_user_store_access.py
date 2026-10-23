"""per-user physical-store access for inventory — additive

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-23

One new table; no existing table or row is touched.

user_store_access is both the access rule and its audit history. A row is
one grant: an account may use inventory for one physical store, recorded with
who granted it, in which role, when and why. A revocation stamps the row
once (who, role, when, why); a revoked row never changes again, and granting
the same store again inserts a new row. A partial unique index allows at most
one active grant per account and store. Foreign keys to users/stores RESTRICT
deletion of a granted account or store, so history is never removed with
them; the actor columns SET NULL, like the other audit tables, while the
actor usernames are kept.

Nothing is granted here: there is no default all-store access for any role.
Grants are made explicitly by an administrator (ADMIN API or
scripts/grant_store_access.py) once this migration is applied.

Row level security with no policies and without FORCE, like edi_exports and
the inventory tables: Supabase's Data API roles see nothing; the backend,
connecting as the table owner, is unaffected.

Revises 0033 (the inventory ledger), the single head.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_store_access",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("granted_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("granted_by", sa.String(length=64), nullable=False),
        sa.Column("granted_by_role", sa.String(length=16), nullable=False),
        sa.Column("grant_reason", sa.Text(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("revoked_by", sa.String(length=64), nullable=True),
        sa.Column("revoked_by_role", sa.String(length=16), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["revoked_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("length(trim(grant_reason)) > 0", name="ck_user_store_access_grant_reason"),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND revoked_by IS NULL AND revoked_by_role IS NULL AND revoke_reason IS NULL "
            "AND revoked_by_user_id IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_by IS NOT NULL AND revoked_by_role IS NOT NULL "
            "AND revoke_reason IS NOT NULL AND length(trim(revoke_reason)) > 0 AND revoked_at >= granted_at)",
            name="ck_user_store_access_revocation",
        ),
    )
    op.execute("ALTER TABLE user_store_access ENABLE ROW LEVEL SECURITY")
    # One active grant per account and store; revoked grants stay as history.
    op.create_index("uq_user_store_access_active", "user_store_access", ["user_id", "store_id"], unique=True,
                    postgresql_where=sa.text("revoked_at IS NULL"))
    op.create_index("idx_user_store_access_user", "user_store_access", ["user_id"])
    op.create_index("idx_user_store_access_store", "user_store_access", ["store_id"])


def downgrade() -> None:
    # Dropping the table also removes its row level security setting and indexes.
    op.drop_table("user_store_access")
