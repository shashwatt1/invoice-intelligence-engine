"""identifier resolution records — additive, append-only

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-29

Adds two tables for the shadow identifier-resolution layer and nothing
else. No existing table is altered; invoice processing, the mapping queue
and the EDI writer neither read nor write them.

  invoice_identifier_observations  printed identifier values as each source
                                   (llm_observation, v11_field, reread,
                                   operator) read them
  invoice_line_resolutions         the deterministic resolver's decision per
                                   line per run, with the full record

Both are append-only: a trigger raises on UPDATE or DELETE, so history can
only grow. A rerun writes a new run_id; the current resolution is the latest.

NOTE: scripts/pilot_seed_guard.py expects revision 0024. Applying this
migration to the pilot means that guard must be deliberately updated before
the Product Master seed is run again.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION identifier_records_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only; write a new row instead', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;
"""

TABLES = ("invoice_identifier_observations", "invoice_line_resolutions")


def upgrade() -> None:
    op.create_table(
        "invoice_identifier_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("invoice_item_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("invoice_items.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("line_index", sa.Integer(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("printed_label", sa.String(length=128), nullable=True),
        sa.Column("role_hint", sa.String(length=32), nullable=True),
        sa.Column("scope", sa.String(length=16), nullable=True),
        sa.Column("section_label", sa.String(length=128), nullable=True),
        sa.Column("position", sa.String(length=16), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=True),
        sa.Column("prompt_sha256", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("source IN ('llm_observation', 'v11_field', 'reread', 'operator')",
                           name="ck_identifier_obs_source"),
    )
    op.create_index("idx_identifier_obs_invoice", "invoice_identifier_observations", ["invoice_id"])
    op.create_index("idx_identifier_obs_item", "invoice_identifier_observations", ["invoice_item_id"])
    op.create_index("idx_identifier_obs_run", "invoice_identifier_observations", ["run_id"])

    op.create_table(
        "invoice_line_resolutions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("invoice_item_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("invoice_items.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("line_index", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("chosen_gtin", sa.String(length=14), nullable=True),
        sa.Column("would_be_eligible", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("pdi_item_code", sa.String(length=11), nullable=True),
        sa.Column("resolver_version", sa.String(length=32), nullable=False),
        sa.Column("template_id", sa.String(length=64), nullable=True),
        sa.Column("template_version", sa.Integer(), nullable=True),
        sa.Column("template_sha256", sa.String(length=64), nullable=True),
        sa.Column("inputs_hash", sa.String(length=64), nullable=False),
        sa.Column("resolution", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('VERIFIED', 'REVIEW_CONFIRMED', 'CORROBORATED', 'VALID_UNMATCHED', 'AMBIGUOUS', "
            "'CONFLICT', 'INVALID', 'UNRESOLVED', 'NOT_APPLICABLE')",
            name="ck_line_resolution_status"),
    )
    op.create_index("idx_line_resolution_invoice", "invoice_line_resolutions", ["invoice_id"])
    op.create_index("idx_line_resolution_item_created", "invoice_line_resolutions",
                    ["invoice_item_id", "created_at"])
    op.create_index("idx_line_resolution_run", "invoice_line_resolutions", ["run_id"])
    op.create_index("idx_line_resolution_status", "invoice_line_resolutions", ["status"])

    op.execute(APPEND_ONLY_FUNCTION)
    for table in TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION identifier_records_append_only();"
        )


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table};")
    op.execute("DROP FUNCTION IF EXISTS identifier_records_append_only();")
    op.drop_index("idx_line_resolution_status", table_name="invoice_line_resolutions")
    op.drop_index("idx_line_resolution_run", table_name="invoice_line_resolutions")
    op.drop_index("idx_line_resolution_item_created", table_name="invoice_line_resolutions")
    op.drop_index("idx_line_resolution_invoice", table_name="invoice_line_resolutions")
    op.drop_table("invoice_line_resolutions")
    op.drop_index("idx_identifier_obs_run", table_name="invoice_identifier_observations")
    op.drop_index("idx_identifier_obs_item", table_name="invoice_identifier_observations")
    op.drop_index("idx_identifier_obs_invoice", table_name="invoice_identifier_observations")
    op.drop_table("invoice_identifier_observations")
