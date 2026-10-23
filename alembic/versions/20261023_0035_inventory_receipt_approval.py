"""inventory receipt approval — submissions, approval, rejection, withdrawal

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-23

Alters inventory_receipts only; no other table is touched and no row is
updated or deleted. Existing receipts (POSTED or REVERSED, posted directly
before this workflow) satisfy every new rule unchanged.

A receipt is no longer posted directly. Marking an invoice Retailed (with
inventory on) creates a SUBMITTED receipt with no movements, recording who
submitted it, the Retailed attestation fingerprint, the EDI export it rested
on and a snapshot of the proposed lines. A MANAGER/ADMIN with a grant for the
store approves it (POSTED, movements written once) or rejects it (REJECTED,
with a reason). Removing the Retailed mark or deleting the invoice withdraws
a pending submission (WITHDRAWN); marking Retailed again replaces it
(SUPERSEDED) and the new submission points at the one it follows.

Schema:
  * received_on, confirmed_by, confirmed_role and discrepancy_count become
    nullable — a submission has no posting yet — and a CHECK requires them
    for POSTED and REVERSED;
  * new columns for the submission (who, role, when, attestation, EDI export,
    snapshot, previous receipt), approval time, rejection, withdrawal and
    supersession;
  * one CHECK per state keeps each state's audit fields consistent: each
    terminal outcome's fields are set exactly when the row is in that state,
    and rejection and withdrawal need a non-blank reason;
  * the partial unique index now allows one SUBMITTED or POSTED receipt per
    invoice (it was POSTED only), plus a partial index for the per-store
    approval queue.

The foreign keys to users and edi_exports SET NULL like the other actor
columns (usernames, export number and SHA-256 are kept); previous_receipt_id
RESTRICTs, like the ledger's own references, so history cannot be removed.

Downgrade is refused while any receipt went through the approval workflow
(any submission, of any state): reverting would drop or violate that audit
history. With none, it restores 0034's table exactly.

Revises 0034 (per-user store access), the single head.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

TABLE = "inventory_receipts"

# Exactly the model's RECEIPT_CHECKS (app/models/inventory.py); a test keeps them equal.
CHECKS = {
    "ck_inventory_receipts_status":
        "status IN ('POSTED', 'REJECTED', 'REVERSED', 'SUBMITTED', 'SUPERSEDED', 'WITHDRAWN')",
    "ck_inventory_receipts_posting":
        "(status NOT IN ('POSTED', 'REVERSED') OR (received_on IS NOT NULL AND confirmed_by IS NOT NULL "
        "AND confirmed_role IS NOT NULL AND discrepancy_count IS NOT NULL "
        "AND (submitted_at IS NULL OR approved_at IS NOT NULL))) "
        "AND (status <> 'POSTED' OR reversed_at IS NULL)",
    "ck_inventory_receipts_submission":
        "(submitted_at IS NULL OR (submitted_at IS NOT NULL AND submitted_by IS NOT NULL AND submitted_role IS NOT NULL "
        "AND attestation_sha256 IS NOT NULL AND submission_snapshot IS NOT NULL)) AND "
        "(status NOT IN ('REJECTED', 'SUBMITTED', 'SUPERSEDED', 'WITHDRAWN') OR (submitted_at IS NOT NULL "
        "AND received_on IS NULL AND confirmed_by IS NULL AND confirmed_role IS NULL AND discrepancy_count IS NULL "
        "AND approved_at IS NULL AND reversed_at IS NULL))",
    "ck_inventory_receipts_rejection":
        "(status = 'REJECTED' AND rejected_at IS NOT NULL AND rejected_by IS NOT NULL "
        "AND rejected_role IS NOT NULL AND rejection_reason IS NOT NULL AND length(trim(rejection_reason)) > 0) OR "
        "(status <> 'REJECTED' AND rejected_at IS NULL AND rejected_by IS NULL AND rejected_role IS NULL "
        "AND rejection_reason IS NULL AND rejected_by_user_id IS NULL)",
    "ck_inventory_receipts_withdrawal":
        "(status = 'WITHDRAWN' AND withdrawn_at IS NOT NULL AND withdrawn_by IS NOT NULL "
        "AND withdrawn_role IS NOT NULL AND withdrawal_reason IS NOT NULL AND length(trim(withdrawal_reason)) > 0) OR "
        "(status <> 'WITHDRAWN' AND withdrawn_at IS NULL AND withdrawn_by IS NULL AND withdrawn_role IS NULL "
        "AND withdrawal_reason IS NULL AND withdrawn_by_user_id IS NULL)",
    "ck_inventory_receipts_supersession":
        "(status = 'SUPERSEDED' AND superseded_at IS NOT NULL) OR "
        "(status <> 'SUPERSEDED' AND superseded_at IS NULL)",
}

NEW_COLUMNS = [
    ("submitted_at", sa.DateTime(timezone=True)),
    ("submitted_by_user_id", postgresql.UUID(as_uuid=True)),
    ("submitted_by", sa.String(length=64)),
    ("submitted_role", sa.String(length=16)),
    ("attestation_sha256", sa.String(length=64)),
    ("edi_export_id", postgresql.UUID(as_uuid=True)),
    ("submission_snapshot", postgresql.JSONB(astext_type=sa.Text())),
    ("previous_receipt_id", postgresql.UUID(as_uuid=True)),
    ("approved_at", sa.DateTime(timezone=True)),
    ("rejected_at", sa.DateTime(timezone=True)),
    ("rejected_by_user_id", postgresql.UUID(as_uuid=True)),
    ("rejected_by", sa.String(length=64)),
    ("rejected_role", sa.String(length=16)),
    ("rejection_reason", sa.Text()),
    ("withdrawn_at", sa.DateTime(timezone=True)),
    ("withdrawn_by_user_id", postgresql.UUID(as_uuid=True)),
    ("withdrawn_by", sa.String(length=64)),
    ("withdrawn_role", sa.String(length=16)),
    ("withdrawal_reason", sa.Text()),
    ("superseded_at", sa.DateTime(timezone=True)),
]

FOREIGN_KEYS = [
    ("fk_inventory_receipts_submitted_by_user", "submitted_by_user_id", "users", "SET NULL"),
    ("fk_inventory_receipts_rejected_by_user", "rejected_by_user_id", "users", "SET NULL"),
    ("fk_inventory_receipts_withdrawn_by_user", "withdrawn_by_user_id", "users", "SET NULL"),
    ("fk_inventory_receipts_edi_export", "edi_export_id", "edi_exports", "SET NULL"),
    ("fk_inventory_receipts_previous_receipt", "previous_receipt_id", TABLE, "RESTRICT"),
]

NOW_NULLABLE = [
    ("received_on", sa.Date()),
    ("confirmed_by", sa.String(length=64)),
    ("confirmed_role", sa.String(length=16)),
    ("discrepancy_count", sa.Integer()),
]


def upgrade() -> None:
    for name, type_ in NEW_COLUMNS:
        op.add_column(TABLE, sa.Column(name, type_, nullable=True))
    for name, column, target, ondelete in FOREIGN_KEYS:
        op.create_foreign_key(name, TABLE, target, [column], ["id"], ondelete=ondelete)
    for name, type_ in NOW_NULLABLE:
        op.alter_column(TABLE, name, existing_type=type_, nullable=True)

    op.drop_constraint("ck_inventory_receipts_status", TABLE, type_="check")
    for name, sql in CHECKS.items():
        op.create_check_constraint(name, TABLE, sql)

    op.drop_index("uq_inventory_receipts_invoice_posted", table_name=TABLE)
    # One SUBMITTED or POSTED receipt per invoice; every other state stays as history.
    op.create_index("uq_inventory_receipts_invoice_active", TABLE, ["invoice_id"], unique=True,
                    postgresql_where=sa.text("status IN ('POSTED', 'SUBMITTED')"))
    op.create_index("idx_inventory_receipts_pending", TABLE, ["store_id", "submitted_at"],
                    postgresql_where=sa.text("status = 'SUBMITTED'"))


def downgrade() -> None:
    # Never discard approval history: refuse while any receipt went through the workflow.
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM inventory_receipts WHERE submitted_at IS NOT NULL "
        "OR status NOT IN ('POSTED', 'REVERSED')) THEN "
        "RAISE EXCEPTION 'inventory_receipts holds approval-workflow history; downgrading 0035 would discard it'; "
        "END IF; END $$"
    )
    op.drop_index("idx_inventory_receipts_pending", table_name=TABLE)
    op.drop_index("uq_inventory_receipts_invoice_active", table_name=TABLE)
    op.create_index("uq_inventory_receipts_invoice_posted", TABLE, ["invoice_id"], unique=True,
                    postgresql_where=sa.text("status = 'POSTED'"))
    for name in CHECKS:
        op.drop_constraint(name, TABLE, type_="check")
    # ck_inventory_receipts_reversal is 0033's and was never changed here.
    op.create_check_constraint("ck_inventory_receipts_status", TABLE, "status IN ('POSTED', 'REVERSED')")
    for name, type_ in NOW_NULLABLE:
        op.alter_column(TABLE, name, existing_type=type_, nullable=False)
    for name, _column, _target, _ondelete in FOREIGN_KEYS:
        op.drop_constraint(name, TABLE, type_="foreignkey")
    for name, _type in reversed(NEW_COLUMNS):
        op.drop_column(TABLE, name)
