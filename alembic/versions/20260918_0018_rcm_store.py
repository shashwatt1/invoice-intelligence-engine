"""RCM — a store the team is about to process invoices for

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-18

The team calls it "RCM". What the system can say about it comes only
from the invoice photographs in the operator's RCM folder:

  Red Bull Distribution invoice 2035546957 (17 Sep 2026), sold to
    "Red Cliff Texaco, 1409 E St George Blvd, Saint George, UT 84770";
    the cheque stapled to it is drawn by "RED CLIFF PETROLEUM, LLC
    DBA: RED CLIFFS MARKET, 1409 EAST ST. GEORGE BLVD., ST. GEORGE,
    UTAH 84790".
  Coca-Cola of Southern Utah invoice 1000007174 (17 Sep 2026), sold to
    "RED CLIFF MARKET, 1409 E ST GEORGE BLVD, ST GEORGE UT 84790".

So: a location at 1409 E St George Blvd, St George, Utah, trading as
Red Cliff(s) Market / Red Cliff Texaco, owned by Red Cliff Petroleum,
LLC. That "RCM" is this location is the operator's classification (the
folder), not something a document says — hence identity UNRESOLVED
until a person confirms it in the Store Directory. The names and the
address are recorded as unverified document identifiers so store
identification can offer this store when an invoice names them.

NOT linked to any Item Sales / POS store code: whether 47708760 or
86357232 (or neither) is this location is a human decision that has not
been made, and until it is, RCM has no reference data or case mappings
of its own. The same folder also holds an A.L. George invoice sold to
"Apple Food & Grocery, 143 Riverside Drive, Johnson City NY" — a
different customer, deliberately not attached here.

Idempotent: skips if a store named RCM exists. Downgrade removes the
store only while nothing operational points at it.
"""

import sqlalchemy as sa

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

RCM = {
    "display_name": "RCM",
    "customer_name": "Red Cliff Market",
    "address_line_1": "1409 E St George Blvd",
    "city": "St George",
    "state": "UT",
    "postal_code": "84790",
    "notes": (
        "Added 18 Sep 2026 for the RCM invoice batch. Name and address observed on the Red Bull "
        "(2035546957) and Coca-Cola (1000007174) invoice photographs in the operator's RCM folder: "
        "'Red Cliff Market' / 'Red Cliff Texaco' / 'Red Cliff Petroleum, LLC dba Red Cliffs Market', "
        "1409 E St George Blvd, St George, UT (84790 on the cheque and the Coca-Cola invoice, 84770 "
        "on the Red Bull sold-to). That 'RCM' is this location is the operator's classification, not "
        "confirmed by a person. NOT linked to any Item Sales store code: no reference data or case "
        "mappings of its own until that link is made. Vendor account numbers seen: Red Bull cust# "
        "3000088613, Coca-Cola customer number 21919."
    ),
}
ORIGIN = ('{"origin": "observed on the Red Bull 2035546957 and Coca-Cola 1000007174 invoice photographs '
          '(Store-Wise Invoices/RCM), 17 Sep 2026", "verified": false}')
NAMES = ["RED CLIFF MARKET", "RED CLIFFS MARKET", "RED CLIFF TEXACO", "RED CLIFF PETROLEUM, LLC"]
ADDRESS_LINES = ["1409 E ST GEORGE BLVD", "1409 EAST ST. GEORGE BLVD."]
POSTAL_CODES = ["84790", "84770"]


def upgrade() -> None:
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT id FROM stores WHERE lower(display_name) = 'rcm'")).first():
        return
    store_id = conn.execute(sa.text(
        "INSERT INTO stores (display_name, customer_name, address_line_1, city, state, postal_code, "
        "identity_status, notes) VALUES (:dn, :cn, :a1, :city, :state, :zip, 'unresolved', :notes) "
        "RETURNING id"
    ), {"dn": RCM["display_name"], "cn": RCM["customer_name"], "a1": RCM["address_line_1"],
        "city": RCM["city"], "state": RCM["state"], "zip": RCM["postal_code"], "notes": RCM["notes"]}
    ).scalar_one()
    for kind, values in (("customer_name", NAMES), ("address_line", ADDRESS_LINES), ("postal_code", POSTAL_CODES)):
        for value in values:
            taken = conn.execute(sa.text(
                "SELECT store_id FROM store_identifiers WHERE source_system = 'document' "
                "AND identifier_type = :t AND identifier_value = :v"
            ), {"t": kind, "v": value}).first()
            if taken:
                continue                       # one identifier, one store — never re-point it
            conn.execute(sa.text(
                "INSERT INTO store_identifiers (store_id, source_system, identifier_type, identifier_value, evidence) "
                "VALUES (:sid, 'document', :t, :v, CAST(:ev AS jsonb))"
            ), {"sid": store_id, "t": kind, "v": value, "ev": ORIGIN})


def downgrade() -> None:
    conn = op.get_bind()
    row = conn.execute(sa.text("SELECT id FROM stores WHERE lower(display_name) = 'rcm'")).first()
    if not row:
        return
    store_id = row[0]
    for table in ("invoices", "documents", "product_case_mappings", "product_data_proposals",
                  "store_product_references", "product_pricing", "product_identity"):
        if conn.execute(sa.text(f"SELECT 1 FROM {table} WHERE store_id = :sid LIMIT 1"), {"sid": store_id}).first():
            raise RuntimeError(f"RCM has {table} rows; not removing it.")
    conn.execute(sa.text("DELETE FROM store_identifiers WHERE store_id = :sid"), {"sid": store_id})
    conn.execute(sa.text("DELETE FROM stores WHERE id = :sid"), {"sid": store_id})
