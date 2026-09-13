"""Add product_data_proposals; grandfather existing case mappings

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-14

Introduces the approval gate between "someone thinks this is true" and
"the EDI relies on it". Every reusable product fact now enters as a
PENDING proposal and reaches an authoritative table only on review.

Motivation: during Testani testing, units-per-case values typed into the
frontend to unblock a download were written directly into
product_case_mappings and were afterwards indistinguishable from
genuinely verified ones. Ten of thirty-eight had to be found and deleted
by hand. This migration closes that path.

GRANDFATHERING — read this before touching the data migration below

product_case_mappings rows that exist when this runs were confirmed
before the workflow existed. They stay authoritative (the formatter
relies on them and they have been checked against the store's own cost
basis), but they were NOT reviewed by a person, and this migration does
not claim they were:

  - each gets one proposal with source='legacy_migrated',
    status='APPROVED' (so the invariant "authoritative <=> approved
    proposal" holds), reviewed_by='system:legacy-migration', and a
    review_note stating in plain words that it predates the workflow;
  - the mapping's own `source` column is left exactly as it was
    (VERIFIED_FROM_INVOICE) — history is not rewritten;
  - approved_proposal_id links the two.

A reviewer listing proposals sees these plainly labelled and can re-open
any of them by proposing a different value.
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

LEGACY_REVIEWER = "system:legacy-migration"
LEGACY_NOTE = (
    "Grandfathered. This mapping was confirmed before the proposal/approval "
    "workflow existed and was never reviewed by a person. It is kept "
    "authoritative because the formatter already relies on it and it was "
    "checked against the store's own cost basis; propose a new value to "
    "re-open it."
)


def upgrade() -> None:
    op.create_table(
        "product_data_proposals",
        sa.Column("id", sa.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"),
                  nullable=False),
        sa.Column("store_number", sa.String(32), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_key", sa.String(64), nullable=False),
        sa.Column("field", sa.String(64), nullable=False),
        sa.Column("proposed_value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("current_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source", sa.String(48), nullable=False),
        sa.Column("source_file", sa.String(255), nullable=True),
        sa.Column("source_sheet", sa.String(128), nullable=True),
        sa.Column("source_row", sa.Integer(), nullable=True),
        sa.Column("invoice_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("proposed_by", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("reviewed_by", sa.String(128), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("status IN ('PENDING','APPROVED','REJECTED')",
                           name="ck_product_data_proposals_status"),
    )
    op.create_index("idx_product_data_proposals_status", "product_data_proposals", ["status"])
    op.create_index("idx_product_data_proposals_entity", "product_data_proposals",
                    ["store_number", "entity_type", "entity_key"])
    op.create_index("idx_product_data_proposals_invoice", "product_data_proposals",
                    ["invoice_id"])

    op.add_column(
        "product_case_mappings",
        sa.Column("approved_proposal_id", sa.UUID(as_uuid=True),
                  sa.ForeignKey("product_data_proposals.id", ondelete="SET NULL"),
                  nullable=True),
    )

    # ---- grandfather every pre-existing mapping ----------------------------
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, item_code, units_per_case, description, source, created_at "
        "FROM product_case_mappings WHERE approved_proposal_id IS NULL"
    )).fetchall()
    now = datetime.now(UTC)
    for mapping_id, item_code, units, description, source, created_at in rows:
        proposal_id = uuid.uuid4()
        bind.execute(
            sa.text(
                "INSERT INTO product_data_proposals "
                "(id, store_number, entity_type, entity_key, field, proposed_value, "
                " current_value, source, invoice_id, evidence, reason, proposed_by, status, "
                " reviewed_by, reviewed_at, review_note, created_at, updated_at) "
                "VALUES (:id, :store, 'case_mapping', :key, 'units_per_case', "
                " CAST(:proposed AS jsonb), NULL, 'legacy_migrated', NULL, "
                " CAST(:evidence AS jsonb), :reason, :proposed_by, 'APPROVED', "
                " :reviewer, :now, :note, :created, :now)"
            ),
            {
                "id": proposal_id,
                "store": "47708760",
                "key": item_code,
                "proposed": str(int(units)),
                "evidence": (
                    '{"description": ' + _json_str(description) +
                    ', "original_mapping_source": ' + _json_str(source) + '}'
                ),
                "reason": "Pre-existing authoritative mapping migrated into the "
                          "proposal history when the approval workflow was introduced.",
                "proposed_by": "system:legacy-migration",
                "reviewer": LEGACY_REVIEWER,
                "now": now,
                "note": LEGACY_NOTE,
                "created": created_at,
            },
        )
        bind.execute(
            sa.text("UPDATE product_case_mappings SET approved_proposal_id = :pid WHERE id = :mid"),
            {"pid": proposal_id, "mid": mapping_id},
        )


def _json_str(value) -> str:
    if value is None:
        return "null"
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def downgrade() -> None:
    op.drop_column("product_case_mappings", "approved_proposal_id")
    op.drop_index("idx_product_data_proposals_invoice", table_name="product_data_proposals")
    op.drop_index("idx_product_data_proposals_entity", table_name="product_data_proposals")
    op.drop_index("idx_product_data_proposals_status", table_name="product_data_proposals")
    op.drop_table("product_data_proposals")
