"""Initial business tables: documents, vendors, invoices, invoice_items, processing_logs

Revision ID: 0001
Revises: (none)
Create Date: 2026-07-10

This migration creates the 5 core business tables for the Invoice Intelligence
Platform MVP. The DDL matches design.md §Database Design exactly.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Ensure pgcrypto extension exists for gen_random_uuid()
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # -----------------------------------------------------------------------
    # documents
    # -----------------------------------------------------------------------
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(50), server_default=sa.text("'UPLOADED'"), nullable=False),
        sa.Column("raw_ocr_text", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_documents_file_hash", "documents", ["file_hash"])
    op.create_index("idx_documents_status", "documents", ["status"])

    # -----------------------------------------------------------------------
    # vendors
    # -----------------------------------------------------------------------
    op.create_table(
        "vendors",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("tax_id", sa.String(100), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "tax_id", name="uq_vendor_name_tax_id"),
    )
    op.create_index("idx_vendors_name", "vendors", ["name"])
    op.create_index("idx_vendors_tax_id", "vendors", ["tax_id"])

    # -----------------------------------------------------------------------
    # invoices
    # -----------------------------------------------------------------------
    op.create_table(
        "invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("invoice_number", sa.String(100), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("subtotal", sa.Numeric(14, 2), nullable=True),
        sa.Column("tax_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("discount_amount", sa.Numeric(14, 2), server_default=sa.text("0"), nullable=True),
        sa.Column("grand_total", sa.Numeric(14, 2), nullable=True),
        sa.Column("currency", sa.String(10), server_default=sa.text("'USD'"), nullable=False),
        sa.Column("vendor_name", sa.String(255), nullable=True),
        sa.Column("vendor_tax_id", sa.String(100), nullable=True),
        sa.Column("vendor_address", sa.Text(), nullable=True),
        sa.Column("status", sa.String(50), server_default=sa.text("'EXTRACTED'"), nullable=False),
        sa.Column("composite_confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("extraction_model", sa.String(100), nullable=True),
        sa.Column("raw_extraction_json", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("document_id", name="uq_invoices_document_id"),
    )
    op.create_index("idx_invoices_document_id", "invoices", ["document_id"])
    op.create_index("idx_invoices_vendor_id", "invoices", ["vendor_id"])
    op.create_index("idx_invoices_status", "invoices", ["status"])
    op.create_index("idx_invoices_invoice_number", "invoices", ["invoice_number"])

    # -----------------------------------------------------------------------
    # invoice_items
    # -----------------------------------------------------------------------
    op.create_table(
        "invoice_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 4), nullable=False),
        sa.Column("unit_price", sa.Numeric(14, 4), nullable=False),
        sa.Column("line_total", sa.Numeric(14, 2), nullable=False),
        sa.Column("tax_rate", sa.Numeric(6, 4), nullable=True),
        sa.Column("discount", sa.Numeric(14, 2), server_default=sa.text("0"), nullable=True),
        sa.Column("product_sku", sa.String(100), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_invoice_items_invoice_id", "invoice_items", ["invoice_id"])

    # -----------------------------------------------------------------------
    # processing_logs
    # -----------------------------------------------------------------------
    op.create_table(
        "processing_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), server_default=sa.text("'SUCCESS'"), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_processing_logs_document_id", "processing_logs", ["document_id"])
    op.create_index("idx_processing_logs_stage", "processing_logs", ["stage"])
    op.create_index("idx_processing_logs_created_at", "processing_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("processing_logs")
    op.drop_table("invoice_items")
    op.drop_table("invoices")
    op.drop_table("vendors")
    op.drop_table("documents")
