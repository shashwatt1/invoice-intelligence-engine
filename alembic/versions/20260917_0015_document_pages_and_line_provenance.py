"""document_pages + invoice_items provenance — one invoice, many photos

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-17

A person photographing a long invoice takes overlapping shots. Those
shots are one intake: each photo is OCR'd on its own, the texts are
combined into one page-separated context, and the model extracts ONE
invoice from it. document_pages holds each photo (file, hash, its own
OCR text and confidence, in operator order); invoice_items gains the
photo numbers each row was read from and, when the model could not tell
an overlap from a legitimate repeat, a duplicate_candidate a reviewer
resolves. Existing documents have no page rows and existing rows have
NULL provenance — both mean "single file, as before".
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_pages",
        # Generated server-side like every other table (UUIDPrimaryKeyMixin):
        # the ORM sends no id and relies on this default.
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("document_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("raw_ocr_text", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(20), nullable=True),
        sa.Column("mean_confidence", sa.Float(), nullable=True),
        sa.Column("ocr_duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("document_id", "page_number", name="uq_document_pages_document_page"),
    )
    op.create_index("idx_document_pages_file_hash", "document_pages", ["file_hash"])
    op.add_column("invoice_items",
                  sa.Column("source_pages", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("invoice_items",
                  sa.Column("duplicate_candidate", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("invoice_items", "duplicate_candidate")
    op.drop_column("invoice_items", "source_pages")
    op.drop_index("idx_document_pages_file_hash", table_name="document_pages")
    op.drop_table("document_pages")
