"""invoice reference link and Retailed completion — additive

Revision ID: 0032
Revises: 0030
Create Date: 2026-10-13

Nine nullable columns on `invoices`; no table is created, no existing row or
value is changed, and every column defaults to NULL, so code deployed before
this migration never reads them and code deployed after reads NULL until a
person acts.

Original invoice / chat link (manager+):
  reference_url, reference_url_set_by, reference_url_set_by_user_id,
  reference_url_set_at. A CHECK keeps the stored value an https:// URL.

Retailed — a person's attestation that the VALIDATED invoice was checked and
is finished; not goods receipt, it never touches inventory or mappings:
  retailed_at, retailed_by, retailed_by_user_id, retailed_edi_export_id (the
  ledger export that was current when it was marked) and
  retailed_attestation_sha256 (a SHA-256 over the attested lines, figures,
  totals, mappings and PDI file, so any later change shows the mark as no longer
  current). A CHECK keeps the four "marked" columns set or cleared together.

Foreign keys use ON DELETE SET NULL: removing a user or a ledger row (the
ledger is write-once and never deletes) can never delete an invoice.

Revises 0030, the deployed head (single head). Revision id 0031 is not used
here because an unreleased local inventory migration already carries it; that
migration must be re-parented onto 0032 before it is released.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0032"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("reference_url", sa.Text(), nullable=True))
    op.add_column("invoices", sa.Column("reference_url_set_by", sa.String(128), nullable=True))
    op.add_column("invoices", sa.Column("reference_url_set_by_user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("invoices", sa.Column("reference_url_set_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("invoices", sa.Column("retailed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("invoices", sa.Column("retailed_by", sa.String(128), nullable=True))
    op.add_column("invoices", sa.Column("retailed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("invoices", sa.Column("retailed_edi_export_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("invoices", sa.Column("retailed_attestation_sha256", sa.String(64), nullable=True))

    op.create_foreign_key("fk_invoices_reference_url_set_by_user", "invoices", "users",
                          ["reference_url_set_by_user_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_invoices_retailed_by_user", "invoices", "users",
                          ["retailed_by_user_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_invoices_retailed_edi_export", "invoices", "edi_exports",
                          ["retailed_edi_export_id"], ["id"], ondelete="SET NULL")

    op.create_check_constraint(
        "ck_invoices_reference_url_https", "invoices",
        "reference_url IS NULL OR reference_url LIKE 'https://%'",
    )
    op.create_check_constraint(
        "ck_invoices_retailed_together", "invoices",
        "(retailed_at IS NULL) = (retailed_by IS NULL) "
        "AND (retailed_at IS NULL) = (retailed_edi_export_id IS NULL) "
        "AND (retailed_at IS NULL) = (retailed_attestation_sha256 IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_invoices_retailed_together", "invoices", type_="check")
    op.drop_constraint("ck_invoices_reference_url_https", "invoices", type_="check")
    op.drop_constraint("fk_invoices_retailed_edi_export", "invoices", type_="foreignkey")
    op.drop_constraint("fk_invoices_retailed_by_user", "invoices", type_="foreignkey")
    op.drop_constraint("fk_invoices_reference_url_set_by_user", "invoices", type_="foreignkey")
    for column in ("retailed_attestation_sha256", "retailed_edi_export_id", "retailed_by_user_id", "retailed_by",
                   "retailed_at", "reference_url_set_at", "reference_url_set_by_user_id",
                   "reference_url_set_by", "reference_url"):
        op.drop_column("invoices", column)
