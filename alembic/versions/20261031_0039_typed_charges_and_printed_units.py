"""typed other charges, printed units and the export override ledger — additive

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-31

Four new nullable columns, one nullable link column and one new table; no existing row is changed or rewritten.

  * invoices.other_charges (JSONB): typed additional charges from the totals area, [{kind, label, amount}] —
    a state container deposit, another jurisdiction charge, delivery or fuel. They are not tax and not
    merchandise. Null for every existing invoice (nothing was extracted into it).
  * invoices.blocking_checks (JSONB): the names of the CRITICAL validation checks (completeness, financial
    reconciliation) that failed on the latest validation. While any is set the PDI export is blocked. Null for
    every existing invoice, so nothing already stored becomes blocked until it is revalidated or reprocessed.
  * invoice_items.printed_units: the row's printed Units figure, so units per case can be proposed from
    units ÷ cases instead of guessed from pack text.
  * invoice_items.section_label: the pack-size heading a row is printed under on grouped layouts, so section
    subtotals can be reconciled.

  * export_overrides (new table, empty): an ADMIN's recorded, reasoned permission to export one exact state of an
    invoice whose critical checks fail, with the failed validation results preserved verbatim. Row level security
    on, like the other ledgers.
  * edi_exports.export_override_id: which override (if any) a delivered PDI file was exported under. Null for
    every existing row.

Downgrade drops the table, the link column and the four columns (their content is re-derivable from the original photographs only by
reprocessing; nothing else reads them).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("other_charges", postgresql.JSONB(), nullable=True))
    op.add_column("invoices", sa.Column("blocking_checks", postgresql.JSONB(), nullable=True))
    op.add_column("invoice_items", sa.Column("printed_units", sa.Integer(), nullable=True))
    op.add_column("invoice_items", sa.Column("section_label", sa.String(length=64), nullable=True))

    op.create_table(
        "export_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("invoice_number", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("blocked_checks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state_sha256", sa.String(length=64), nullable=False),
        sa.Column("validation_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("granted_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("granted_by", sa.String(length=64), nullable=False),
        sa.Column("granted_by_role", sa.String(length=16), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(length=64), nullable=True),
        sa.Column("revoked_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["revoked_by_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.execute("ALTER TABLE export_overrides ENABLE ROW LEVEL SECURITY")
    op.create_index("idx_export_overrides_invoice", "export_overrides", ["invoice_id", "created_at"])
    op.add_column("edi_exports", sa.Column("export_override_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_edi_exports_export_override", "edi_exports", "export_overrides",
                          ["export_override_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_edi_exports_export_override", "edi_exports", type_="foreignkey")
    op.drop_column("edi_exports", "export_override_id")
    op.drop_table("export_overrides")
    op.drop_column("invoice_items", "section_label")
    op.drop_column("invoice_items", "printed_units")
    op.drop_column("invoices", "blocking_checks")
    op.drop_column("invoices", "other_charges")
