"""users table + documents.uploaded_by_user_id — real authenticated ownership

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-21

P3 replaces P2's caller-supplied actor trust model with real accounts.
Two additive, safe changes:

  1. `users` — email/password_hash/role/is_active. Created only through
     scripts/create_user.py or ADMIN user management; no public
     registration.
  2. `documents.uploaded_by_user_id` — nullable FK to users.id. The old
     `uploaded_by` free-text column is left exactly as it was: no
     rewrite, no attempt to match a typed name to a real account. Every
     existing document keeps NULL ownership under the new column, which
     is the correct, safe outcome — inventing an owner for a historical
     row would be a guess, and a USER caller can never claim a
     NULL-owner document (only MANAGER/ADMIN can still manage it; see
     app.services.document_lifecycle). Nothing about documents, invoices,
     items, mappings, proposals or processing history is touched.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), server_default=sa.text("'USER'"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("idx_users_email", "users", ["email"])

    op.add_column("documents", sa.Column("uploaded_by_user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_documents_uploaded_by_user_id", "documents", "users",
        ["uploaded_by_user_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_documents_uploaded_by_user_id", "documents", type_="foreignkey")
    op.drop_column("documents", "uploaded_by_user_id")
    op.drop_index("idx_users_email", table_name="users")
    op.drop_table("users")
