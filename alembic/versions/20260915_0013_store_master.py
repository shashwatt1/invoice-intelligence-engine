"""Store master: stores, store_identifiers; operational tables reference Store.id

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-15

Until now the operational tables were scoped by a string called
store_number that was really the Item Sales export's store code — one
source system's name for a location, used as if it were the location.
Invoices carry other numbers again. This revision introduces the store
as an entity of its own:

  stores             the location, with a human identity that is only
                     set from confirmed evidence
  store_identifiers  how each source system refers to it

and re-points invoices, product_case_mappings, product_pricing,
product_identity, product_identifier, store_product_references and
product_data_proposals at Store.id. The old codes are preserved exactly
as item_sales/store_code identifiers; nothing is merged, nothing is
copied between stores, and no name is guessed.

Backfill: one Store per distinct store_number found across those tables
(there are two: 47708760 and 86357232), identity unresolved. Every row
takes the Store its store_number resolves to. A third Store, also
unresolved, records the location observed on the "Invoices HO" photo
batch as reported by the operator — with NO source code attached,
because nothing in the data links it to either export yet.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

SCOPED = [
    # table, old unique/index names to drop, new unique columns
    ("invoices", [("index", "idx_invoices_store")], None),
    ("product_case_mappings", [("unique", "uq_product_case_mapping_store_item"),
                               ("index", "idx_product_case_mappings_store")],
     ("uq_product_case_mapping_store_item", ["store_id", "item_code"])),
    ("product_pricing", [("unique", "uq_product_pricing_source_row"), ("index", "idx_product_pricing_item")],
     ("uq_product_pricing_source_row", ["store_id", "source_file", "source_sheet", "source_row"])),
    ("product_identity", [("unique", "uq_product_identity_store_item")],
     ("uq_product_identity_store_item", ["store_id", "item_code"])),
    ("product_identifier", [("unique", "uq_product_identifier"), ("index", "idx_product_identifier_lookup")],
     ("uq_product_identifier", ["store_id", "item_code", "kind", "value", "distributor"])),
    ("store_product_references", [("unique", "uq_store_product_reference_store_item"),
                                  ("index", "idx_store_product_references_store")],
     ("uq_store_product_reference_store_item", ["store_id", "item_code"])),
    ("product_data_proposals", [("index", "idx_product_data_proposals_entity")], None),
]

OBSERVED_LOCATION = {
    # Reported by the operator from the Invoices HO photo batch. Not linked
    # to any source code: that link is a human decision, made later.
    "display_name": "Apple Foods II",
    "customer_name": "PB Wolf Group Inc",
    "address_line_1": "800 Wolf St",
    "city": "Syracuse",
    "state": "NY",
    "postal_code": "13208-1224",
    "aliases": ["PB WOLF GROUP INC", "APPLE FOODS II", "APPLE FOODS"],
}


def upgrade() -> None:
    op.create_table(
        "stores",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"),
                  nullable=False),
        sa.Column("display_name", sa.String(128), nullable=True),
        sa.Column("customer_name", sa.String(128), nullable=True),
        sa.Column("address_line_1", sa.String(128), nullable=True),
        sa.Column("address_line_2", sa.String(128), nullable=True),
        sa.Column("city", sa.String(64), nullable=True),
        sa.Column("state", sa.String(32), nullable=True),
        sa.Column("postal_code", sa.String(16), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("identity_status", sa.String(16), nullable=False, server_default="unresolved"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "store_identifiers",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"),
                  nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", sa.String(32), nullable=False),
        sa.Column("identifier_type", sa.String(32), nullable=False),
        sa.Column("identifier_value", sa.String(128), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("source_system", "identifier_type", "identifier_value",
                            name="uq_store_identifier_source_value"),
    )
    op.create_index("idx_store_identifiers_store", "store_identifiers", ["store_id"])
    op.create_index("idx_store_identifiers_value", "store_identifiers", ["identifier_value"])

    conn = op.get_bind()

    # ---- one Store per source store code found in the data -----------------
    codes: set[str] = set()
    for table, _, _ in SCOPED:
        for (code,) in conn.execute(sa.text(f"SELECT DISTINCT store_number FROM {table}")):
            if code:
                codes.add(code)
    store_ids: dict[str, uuid.UUID] = {}
    for code in sorted(codes):
        store_id = uuid.uuid4()
        conn.execute(sa.text(
            "INSERT INTO stores (id, identity_status, notes) VALUES (:id, 'unresolved', :notes)"
        ), {"id": store_id, "notes": (
            f"Known only as Item Sales store code {code}. Name and address not yet confirmed "
            "by a person; nothing in the reference data names the location."
        )})
        conn.execute(sa.text(
            "INSERT INTO store_identifiers (store_id, source_system, identifier_type, identifier_value, evidence) "
            "VALUES (:sid, 'item_sales', 'store_code', :code, :ev)"
        ), {"sid": store_id, "code": code,
            "ev": '{"origin": "Item Sales Summary export preamble (Store: ...)", "migrated_from": "store_number"}'})
        store_ids[code] = store_id

    # ---- the observed-but-unlinked location from the photo batch ----------
    observed_id = uuid.uuid4()
    conn.execute(sa.text(
        "INSERT INTO stores (id, display_name, customer_name, address_line_1, city, state, postal_code, "
        "identity_status, notes) VALUES (:id, :dn, :cn, :a1, :city, :state, :zip, 'unresolved', :notes)"
    ), {"id": observed_id, "dn": OBSERVED_LOCATION["display_name"], "cn": OBSERVED_LOCATION["customer_name"],
        "a1": OBSERVED_LOCATION["address_line_1"], "city": OBSERVED_LOCATION["city"],
        "state": OBSERVED_LOCATION["state"], "zip": OBSERVED_LOCATION["postal_code"],
        "notes": ("Name and address observed on the 'Invoices HO' photo batch, as reported by the "
                  "operator; the photographs themselves have not been processed. NOT linked to any "
                  "Item Sales store code — whether this is the location behind 47708760 (or another) "
                  "is a human decision that has not been made.")})
    for alias in OBSERVED_LOCATION["aliases"]:
        conn.execute(sa.text(
            "INSERT INTO store_identifiers (store_id, source_system, identifier_type, identifier_value, evidence) "
            "VALUES (:sid, 'document', 'customer_name', :v, :ev)"
        ), {"sid": observed_id, "v": alias,
            "ev": '{"origin": "operator-reported from the Invoices HO photo batch", "verified": false}'})
    conn.execute(sa.text(
        "INSERT INTO store_identifiers (store_id, source_system, identifier_type, identifier_value, evidence) "
        "VALUES (:sid, 'document', 'address_line', :v, :ev)"
    ), {"sid": observed_id, "v": "800 WOLF ST",
        "ev": '{"origin": "operator-reported from the Invoices HO photo batch", "verified": false}'})
    conn.execute(sa.text(
        "INSERT INTO store_identifiers (store_id, source_system, identifier_type, identifier_value, evidence) "
        "VALUES (:sid, 'document', 'postal_code', :v, :ev)"
    ), {"sid": observed_id, "v": "13208",
        "ev": '{"origin": "operator-reported from the Invoices HO photo batch", "verified": false}'})

    # ---- re-point every scoped table -----------------------------------------
    for table, drops, unique in SCOPED:
        op.add_column(table, sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=True))
        for code, store_id in store_ids.items():
            conn.execute(sa.text(f"UPDATE {table} SET store_id = :sid WHERE store_number = :code"),
                         {"sid": store_id, "code": code})
        unresolved = conn.execute(sa.text(f"SELECT count(*) FROM {table} WHERE store_id IS NULL")).scalar()
        if unresolved:
            raise RuntimeError(f"{table}: {unresolved} rows have a store_number with no Store; aborting.")
        op.alter_column(table, "store_id", nullable=False)
        op.create_foreign_key(f"fk_{table}_store", table, "stores", ["store_id"], ["id"],
                              ondelete="RESTRICT")
        for kind, name in drops:
            if kind == "unique":
                op.drop_constraint(name, table, type_="unique")
            else:
                op.drop_index(name, table_name=table)
        if unique:
            op.create_unique_constraint(unique[0], table, unique[1])
        op.create_index(f"idx_{table}_store", table, ["store_id"])
        op.drop_column(table, "store_number")
    op.create_index("idx_product_data_proposals_entity", "product_data_proposals",
                    ["store_id", "entity_type", "entity_key"])
    op.create_index("idx_product_pricing_item", "product_pricing", ["store_id", "item_code"])
    op.create_index("idx_product_identifier_lookup", "product_identifier",
                    ["store_id", "kind", "value", "distributor"])

    # ---- documents: the store an upload is for, and what identification found
    op.add_column("documents", sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_documents_store", "documents", "stores", ["store_id"], ["id"],
                          ondelete="SET NULL")
    op.add_column("documents", sa.Column("store_candidates", postgresql.JSONB(astext_type=sa.Text()),
                                         nullable=True))
    conn.execute(sa.text(
        "UPDATE documents d SET store_id = i.store_id FROM invoices i WHERE i.document_id = d.id"
    ))


def downgrade() -> None:
    raise NotImplementedError(
        "0013 replaces store_number with Store.id across seven tables; restore from backup "
        "rather than downgrading."
    )
