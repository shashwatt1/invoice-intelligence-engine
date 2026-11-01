"""vendor and delivery date chosen at upload, and vendor requests — additive

Revision ID: 0040
Revises: 0039
Create Date: 2026-11-01

Additive; no existing row is changed or rewritten.

  * documents.chosen_vendor_id, documents.vendor_request_id, documents.declared_delivery_date: what the operator chose at
    upload besides the store (all nullable).
  * invoices.vendor_source ('extracted' / 'operator' / 'requested') and invoices.delivery_date: where the invoice's vendor
    came from and the day the goods arrived (both nullable; NULL on every existing invoice, which is how the app knows a
    person still has to set them before Retailed).
  * vendor_requests (new, empty): a USER's request for a vendor that is not in the list, decided by a MANAGER or ADMIN.
    One open request per normalized name; row level security on, like the other ledgers.

Downgrade drops the table and the six columns (their content exists nowhere else).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vendor_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("requested_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="PENDING", nullable=False),
        sa.Column("requested_by", sa.String(length=64), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_by", sa.String(length=64), nullable=True),
        sa.Column("decided_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"], ondelete="SET NULL"),
        sa.CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED')", name="ck_vendor_requests_status"),
        sa.CheckConstraint("(status = 'PENDING') = (decided_at IS NULL)", name="ck_vendor_requests_decided"),
        sa.CheckConstraint("status <> 'APPROVED' OR vendor_id IS NOT NULL", name="ck_vendor_requests_approved_vendor"),
    )
    op.execute("ALTER TABLE vendor_requests ENABLE ROW LEVEL SECURITY")
    op.create_index("uq_vendor_requests_pending_name", "vendor_requests", ["normalized_name"], unique=True,
                    postgresql_where=sa.text("status = 'PENDING'"))
    op.create_index("idx_vendor_requests_status", "vendor_requests", ["status", "created_at"])

    op.add_column("documents", sa.Column("chosen_vendor_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("documents", sa.Column("vendor_request_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("documents", sa.Column("declared_delivery_date", sa.Date(), nullable=True))
    op.create_foreign_key("fk_documents_chosen_vendor", "documents", "vendors", ["chosen_vendor_id"], ["id"],
                          ondelete="SET NULL")
    op.create_foreign_key("fk_documents_vendor_request", "documents", "vendor_requests", ["vendor_request_id"], ["id"],
                          ondelete="SET NULL")
    op.add_column("invoices", sa.Column("vendor_source", sa.String(length=16), nullable=True))
    op.add_column("invoices", sa.Column("delivery_date", sa.Date(), nullable=True))
    op.create_check_constraint("ck_invoices_vendor_source", "invoices",
                               "vendor_source IS NULL OR vendor_source IN ('extracted', 'operator', 'requested')")


def downgrade() -> None:
    op.drop_constraint("ck_invoices_vendor_source", "invoices", type_="check")
    op.drop_column("invoices", "delivery_date")
    op.drop_column("invoices", "vendor_source")
    op.drop_constraint("fk_documents_vendor_request", "documents", type_="foreignkey")
    op.drop_constraint("fk_documents_chosen_vendor", "documents", type_="foreignkey")
    op.drop_column("documents", "declared_delivery_date")
    op.drop_column("documents", "vendor_request_id")
    op.drop_column("documents", "chosen_vendor_id")
    op.drop_table("vendor_requests")
