"""edi exports — write-once ledger of delivered PDI files — additive

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-08

One new table; no existing table or row is touched.

Each PDI file actually delivered appends one row: the exact bytes and their
SHA-256, who exported it (account id, username and role from the authenticated
session) and when, the per-line commercial resolution that produced it, and
the build (git SHA, Alembic revision) that ran. Written only while
EDI_EXPORT_LEDGER is on, so deploying the code before this migration is safe.

Row level security is enabled on the new table with no policies and without
FORCE: Supabase's Data API roles (anon, authenticated) see nothing, while the
backend, which connects as the table owner, is unaffected.

Revises 0029, the deployed head. The unreleased 0025 (identifier resolution
records) also descends from 0024 and is re-parented when it is released.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "edi_exports",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("invoice_number", sa.String(length=64), nullable=True),
        sa.Column("export_number", sa.Integer(), nullable=False),
        sa.Column("format", sa.String(length=32), nullable=False),
        sa.Column("format_version", sa.String(length=16), nullable=False),
        sa.Column("exported_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("exported_by", sa.String(length=64), nullable=False),
        sa.Column("exported_by_role", sa.String(length=16), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("resolution_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("build", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["exported_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("invoice_id", "export_number", name="uq_edi_exports_invoice_number"),
    )
    op.execute("ALTER TABLE edi_exports ENABLE ROW LEVEL SECURITY")
    op.create_index("idx_edi_exports_invoice", "edi_exports", ["invoice_id"])
    op.create_index("idx_edi_exports_created", "edi_exports", ["created_at"])


def downgrade() -> None:
    # Dropping the table also removes its row level security setting.
    op.drop_table("edi_exports")
