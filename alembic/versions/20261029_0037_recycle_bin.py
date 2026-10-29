"""recycle bin — additive

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-29

Two new append-only tables and one new nullable column; no existing row is
changed, and nothing is binned, restored or deleted here.

  * recycle_bin_entries: one row per time a document went into the bin, with
    who, when, why, its previous status and a snapshot of what it was. Closes
    once: RESTORED, RESTORED_AS_REFERENCE or PURGED. At most one open entry
    per document (partial unique index).
  * bin_deletion_requests: a request for an administrator to delete a binned
    document permanently, decided once. At most one pending per entry.
  * documents.reference_of_document_id: for a document an administrator
    restored as a read-only reference copy, the active document it duplicates.
    The new REFERENCE status itself needs no schema change (status is VARCHAR).

Foreign keys to documents SET NULL, so a permanently deleted document leaves
its entries (and the reason it was deleted) behind. An open entry must keep its
document; closed entries (including earlier RESTORED ones) may lose the link
when the document is deleted. Row level security with no
policies, like the other audit tables.

The downgrade refuses while any bin history or reference copy exists.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

ENTRIES = "recycle_bin_entries"
REQUESTS = "bin_deletion_requests"
UUID = postgresql.UUID(as_uuid=True)


def _id():
    return sa.Column("id", UUID, server_default=sa.text("gen_random_uuid()"), nullable=False)


def _timestamps():
    return [sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)]


def upgrade() -> None:
    op.create_table(
        ENTRIES,
        _id(),
        sa.Column("document_id", UUID, nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("invoice_id", UUID, nullable=True),
        sa.Column("invoice_number", sa.String(length=64), nullable=True),
        sa.Column("vendor_name", sa.String(length=255), nullable=True),
        sa.Column("store_id", UUID, nullable=True),
        sa.Column("uploaded_by_user_id", UUID, nullable=True),
        sa.Column("previous_status", sa.String(length=50), nullable=False),
        sa.Column("binned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("binned_by_user_id", UUID, nullable=True),
        sa.Column("binned_by", sa.String(length=64), nullable=False),
        sa.Column("binned_by_role", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reuploaded_as_document_id", UUID, nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by_user_id", UUID, nullable=True),
        sa.Column("closed_by", sa.String(length=64), nullable=True),
        sa.Column("closed_by_role", sa.String(length=16), nullable=True),
        sa.Column("close_note", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["binned_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reuploaded_as_document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["closed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("status IN ('IN_BIN', 'PURGED', 'RESTORED', 'RESTORED_AS_REFERENCE')",
                           name="ck_recycle_bin_entries_status"),
        sa.CheckConstraint("length(trim(reason)) > 0", name="ck_recycle_bin_entries_reason"),
        sa.CheckConstraint(
            "(status = 'IN_BIN' AND closed_at IS NULL AND closed_by IS NULL AND closed_by_role IS NULL "
            "AND closed_by_user_id IS NULL AND close_note IS NULL) OR "
            "(status <> 'IN_BIN' AND closed_at IS NOT NULL AND closed_by IS NOT NULL "
            "AND closed_by_role IS NOT NULL AND closed_at >= binned_at)",
            name="ck_recycle_bin_entries_closed"),
        # An open entry keeps its document; closed entries may lose it when the document is deleted.
        sa.CheckConstraint("status <> 'IN_BIN' OR document_id IS NOT NULL", name="ck_recycle_bin_entries_document"),
    )
    op.execute(f"ALTER TABLE {ENTRIES} ENABLE ROW LEVEL SECURITY")
    op.create_index("uq_recycle_bin_entries_open", ENTRIES, ["document_id"], unique=True,
                    postgresql_where=sa.text("status = 'IN_BIN'"))
    op.create_index("idx_recycle_bin_entries_status", ENTRIES, ["status", "binned_at"])
    op.create_index("idx_recycle_bin_entries_document", ENTRIES, ["document_id"])
    op.create_index("idx_recycle_bin_entries_file_hash", ENTRIES, ["file_hash"])

    op.create_table(
        REQUESTS,
        _id(),
        sa.Column("entry_id", UUID, nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_by_user_id", UUID, nullable=True),
        sa.Column("requested_by", sa.String(length=64), nullable=False),
        sa.Column("requested_by_role", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_user_id", UUID, nullable=True),
        sa.Column("decided_by", sa.String(length=64), nullable=True),
        sa.Column("decided_by_role", sa.String(length=16), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["entry_id"], [f"{ENTRIES}.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("status IN ('APPROVED', 'CANCELLED', 'PENDING', 'REJECTED')",
                           name="ck_bin_deletion_requests_status"),
        sa.CheckConstraint("length(trim(reason)) > 0", name="ck_bin_deletion_requests_reason"),
        sa.CheckConstraint(
            "(status = 'PENDING' AND decided_at IS NULL AND decided_by IS NULL AND decided_by_role IS NULL "
            "AND decided_by_user_id IS NULL AND decision_note IS NULL) OR "
            "(status <> 'PENDING' AND decided_at IS NOT NULL AND decided_by IS NOT NULL "
            "AND decided_by_role IS NOT NULL AND decided_at >= requested_at)",
            name="ck_bin_deletion_requests_decided"),
    )
    op.execute(f"ALTER TABLE {REQUESTS} ENABLE ROW LEVEL SECURITY")
    op.create_index("uq_bin_deletion_requests_pending", REQUESTS, ["entry_id"], unique=True,
                    postgresql_where=sa.text("status = 'PENDING'"))
    op.create_index("idx_bin_deletion_requests_status", REQUESTS, ["status", "requested_at"])

    op.add_column("documents", sa.Column("reference_of_document_id", UUID, nullable=True))
    op.create_foreign_key("fk_documents_reference_of", "documents", "documents",
                          ["reference_of_document_id"], ["id"], ondelete="SET NULL")
    op.create_index("idx_documents_reference_of", "documents", ["reference_of_document_id"])


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        f"IF EXISTS (SELECT 1 FROM {ENTRIES}) OR EXISTS (SELECT 1 FROM {REQUESTS}) "
        "OR EXISTS (SELECT 1 FROM documents WHERE reference_of_document_id IS NOT NULL "
        "OR status = 'REFERENCE') THEN "
        "RAISE EXCEPTION 'the recycle bin holds history or reference copies; downgrading 0037 would discard them'; "
        "END IF; END $$"
    )
    op.drop_index("idx_documents_reference_of", table_name="documents")
    op.drop_constraint("fk_documents_reference_of", "documents", type_="foreignkey")
    op.drop_column("documents", "reference_of_document_id")
    op.drop_table(REQUESTS)
    op.drop_table(ENTRIES)
