"""
tests/integration/test_first_invoice_store_86357232.py — the first
invoice for a second store, end to end, before a real one arrives.

A synthetic invoice is received for store 86357232 while store 47708760
holds mappings, reference rows and decisions for the SAME barcodes with
different values. The test proves, in the test database only:

  * the invoice persists its store and every lookup on its behalf is
    restricted to that store — no query issued while serving it carries
    the other store's number;
  * units-per-case comes from the evidence ladder for THIS store — an
    existing 86357232 mapping, 86357232 reference rows, the document —
    and where nothing answers, the line stays unmapped rather than
    defaulting to 1;
  * confirming creates PENDING proposals under store 86357232 that the
    review queue, detail and history show as such, and nothing is
    approved on the operator's say-so;
  * the PDI export stays blocked until every line has an authoritative
    86357232 mapping, and the other store's mappings never fill the gap.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import event, select

from app.models.invoice import Invoice
from app.models.product_case_mapping import ProductCaseMapping
from app.models.product_data_proposal import (
    SOURCE_BEER_INVENTORY_EXPLICIT,
    SOURCE_DOCUMENT_DERIVED,
    SOURCE_OPERATOR_ENTERED,
    SOURCE_REFERENCE_DERIVED,
    STATUS_PENDING,
)
from app.models.product_reference import (
    BASIS_PERIOD_AVERAGE,
    BASIS_PROMO,
    KIND_RETAIL_UPC_RAW,
    ProductIdentifier,
    ProductIdentity,
    ProductPricing,
)
from app.repositories.product_case_mapping_repository import ProductCaseMappingRepository
from app.repositories.product_data_proposal_repository import ProductDataProposalRepository
from app.repositories.store_product_reference_repository import StoreProductReferenceRepository
from tests.integration.conftest import requires_db
from tests.integration.test_api_db import api_client  # noqa: F401 — fixture reuse
from tests.integration.test_multi_store import detail, process_for
from tests.integration.test_proposal_governance import line

pytestmark = requires_db

NEW, OLD = "86357232", "47708760"

LABATT = ("062067051468", "06206705146")     # mapped in BOTH stores, differently
ULTRA = ("018200239865", "01820023986")      # typed items/case in the new store's reference
BUD30 = ("018200110302", "01820011030")      # mapped in the OLD store only; retail evidence here
BUSCH = ("018200008011", "01820000801")      # nothing in the new store; the document says 2/12
BEATBOX = ("850059195932", "85005919593")    # nothing anywhere

LINES = [
    line("LABATT BLUE 30 PACK", LABATT[0], 22.70),
    line("ULTRA 3/8/16", ULTRA[0], 27.60),
    line("BUD 30 PACK CANS", BUD30[0], 22.70),
    line("BUSCH LT 2/12 CAN", BUSCH[0], 18.75),
    line("BEATBOX MALT SOUR CH", BEATBOX[0], 34.50),
]
SUBTOTAL = 22.70 + 27.60 + 22.70 + 18.75 + 34.50


async def seed_both_stores(db_session):
    """The same barcodes in both stores, with values that tell them apart."""
    now = datetime.now(UTC)
    mappings = ProductCaseMappingRepository(db_session)
    await mappings.upsert(store_number=NEW, item_code=LABATT[1], units_per_case=1, source="MANUAL")
    await mappings.upsert(store_number=OLD, item_code=LABATT[1], units_per_case=30, source="MANUAL")
    await mappings.upsert(store_number=OLD, item_code=BUD30[1], units_per_case=1, source="MANUAL")
    await mappings.upsert(store_number=OLD, item_code=ULTRA[1], units_per_case=8, source="MANUAL")

    db_session.add_all([
        # ULTRA: a typed items/case cell in each store's Beer Inventory — different numbers
        ProductPricing(store_number=NEW, item_code=ULTRA[1], distributor="Testani",
                       pricing_basis=BASIS_PROMO, case_cost=Decimal("27.60"), unit_cost=Decimal("9.20"),
                       items_per_case_stated=3, source_file="Beer Inventory.xlsx",
                       source_sheet="Sheet1", source_row=56, imported_at=now),
        ProductPricing(store_number=OLD, item_code=ULTRA[1], distributor="Testani",
                       pricing_basis=BASIS_PROMO, case_cost=Decimal("27.60"), unit_cost=Decimal("3.45"),
                       items_per_case_stated=8, source_file="Beer Inventory.xlsx",
                       source_sheet="Sheet1", source_row=56, imported_at=now),
        # BUD 30: the new store's till scans it at $25.72 (a 30-pack sold whole)
        ProductPricing(store_number=NEW, item_code=BUD30[1], distributor="store",
                       pricing_basis=BASIS_PERIOD_AVERAGE, unit_cost=None, unit_retail=Decimal("25.72"),
                       source_file="Item_Sales_Summary_2026-09-14T15_45_30.014Z.xlsx",
                       source_sheet="data", source_row=553, imported_at=now),
        ProductIdentity(store_number=NEW, item_code=ULTRA[1], description="ULTRA 3/8/16 new-store", provenance={}),
        ProductIdentity(store_number=OLD, item_code=ULTRA[1], description="ULTRA 3/8/16 old-store", provenance={}),
        ProductIdentity(store_number=OLD, item_code=BUSCH[1], description="Busch LT old-store only", provenance={}),
        ProductIdentifier(store_number=OLD, item_code=BUSCH[1], kind=KIND_RETAIL_UPC_RAW, value=BUSCH[0],
                          distributor=None, source_file="x.xlsx", source_sheet="data", source_row=1, imported_at=now),
    ])
    await StoreProductReferenceRepository(db_session).upsert_many([
        {"store_number": NEW, "item_code": BUD30[1], "scan_code_raw": BUD30[0],
         "description": "Budweiser 30cans (new store)", "avg_cost": None, "avg_price": Decimal("25.72"),
         "source_file": "Item_Sales_Summary_2026-09-14T15_45_30.014Z.xlsx", "imported_at": now},
        {"store_number": OLD, "item_code": BUD30[1], "scan_code_raw": BUD30[0],
         "description": "Budweiser 30cans (old store)", "avg_cost": Decimal("0.7567"), "avg_price": Decimal("25.72"),
         "source_file": "Item_Sales_Summary_2026-09-07T16_07_36.325Z.xlsx", "imported_at": now},
        {"store_number": OLD, "item_code": BUSCH[1], "scan_code_raw": BUSCH[0],
         "description": "Busch LT (old store)", "avg_cost": Decimal("9.375"), "avg_price": Decimal("11.99"),
         "source_file": "Item_Sales_Summary_2026-09-07T16_07_36.325Z.xlsx", "imported_at": now},
    ])
    # a decision the OLD store already made about BUSCH — must not show in the new store's history
    await ProductDataProposalRepository(db_session).create(
        store_number=OLD, entity_type="case_mapping", entity_key=BUSCH[1], field="units_per_case",
        proposed_value=2, current_value=None, source=SOURCE_DOCUMENT_DERIVED, proposed_by="test", evidence={})
    await db_session.commit()


class SqlCapture:
    """Every statement (with parameters) the engine ran while active."""

    def __init__(self, engine):
        self.engine = engine.sync_engine
        self.calls: list[tuple[str, str]] = []

    def _on(self, conn, cursor, statement, parameters, context, executemany):
        self.calls.append((statement, repr(parameters)))

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._on)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._on)

    def touching(self, table: str) -> list[tuple[str, str]]:
        return [(s, p) for s, p in self.calls if table in s]


@pytest.fixture
async def first_invoice(api_client, app, db_session):  # noqa: F811
    await seed_both_stores(db_session)
    invoice_id = await process_for(api_client, app, NEW, LINES, "store-86357232-first.pdf",
                                   "first invoice new store", subtotal=SUBTOTAL, grand_total=SUBTOTAL)
    return invoice_id


class TestTheInvoiceBelongsToItsStore:
    async def test_it_persists_store_86357232(self, api_client, db_session, first_invoice):  # noqa: F811
        invoice = (await db_session.execute(select(Invoice).where(Invoice.id == first_invoice))).scalar_one()
        assert invoice.store_number == NEW
        assert (await detail(api_client, first_invoice))["store_number"] == NEW

    async def test_no_query_on_its_behalf_carries_the_other_stores_number(self, api_client, db_engine, first_invoice):  # noqa: F811
        with SqlCapture(db_engine) as sql:
            await detail(api_client, first_invoice)
            await api_client.get(f"/api/v1/invoices/{first_invoice}/export", params={"format": "pdi"})
            await api_client.post(f"/api/v1/invoices/{first_invoice}/case-mappings",
                                  json={"mappings": [{"item_code": ULTRA[1], "units_per_case": 3}]})
        assert sql.calls, "nothing captured — the listener is not on the engine the app uses"
        leaked = [(s[:80], p) for s, p in sql.calls if OLD in p or OLD in s]
        assert leaked == []
        # every reference table consulted was consulted with this store's number
        for table in ("product_pricing", "product_identity", "store_product_references",
                      "product_case_mappings", "product_data_proposals"):
            touched = sql.touching(table)
            assert touched, f"{table} was never queried"
            assert all(NEW in p or "INSERT" in s or "UPDATE" in s for s, p in touched), table
        # identifiers are never part of invoice processing at all
        assert sql.touching("product_identifier") == []


class TestUnitsPerCaseComesFromThisStoresEvidence:
    async def test_the_evidence_ladder_per_line(self, api_client, first_invoice):  # noqa: F811
        rows = {r["item_code"]: r for r in (await detail(api_client, first_invoice))["case_mappings"]}

        labatt = rows[LABATT[1]]                      # this store's own mapping wins
        assert (labatt["mapped"], labatt["units_per_case"], labatt["suggestion_source"]) == (True, 1, "database")

        ultra = rows[ULTRA[1]]                        # this store's typed items/case, not the other's 8
        assert ultra["mapped"] is False and ultra["units_per_case"] is None
        assert (ultra["suggested_units_per_case"], ultra["suggestion_source"]) == (3, "reference_explicit")
        assert ultra["reference_description"] == "ULTRA 3/8/16 new-store"

        bud = rows[BUD30[1]]                          # mapped in the OTHER store only: unmapped here
        assert bud["mapped"] is False and bud["units_per_case"] is None
        assert bud["suggestion_source"] == "reference_retail" and bud["suggested_units_per_case"] == 1
        assert bud["reference_description"] == "Budweiser 30cans (new store)"

        busch = rows[BUSCH[1]]                        # only the document speaks here
        assert busch["mapped"] is False
        assert (busch["suggested_units_per_case"], busch["suggestion_source"]) == (2, "description")
        assert busch["reference_description"] is None  # the other store's catalogue row is not consulted

        beatbox = rows[BEATBOX[1]]                    # nothing answers: no number is invented
        assert beatbox["mapped"] is False
        assert beatbox["suggested_units_per_case"] is None
        assert beatbox["suggestion_source"] is None
        assert beatbox["units_per_case"] is None      # never 1

    async def test_the_export_is_blocked_on_exactly_the_unmapped_lines(self, api_client, first_invoice):  # noqa: F811
        d = await detail(api_client, first_invoice)
        assert d["pdi_export_allowed"] is False
        export = await api_client.get(f"/api/v1/invoices/{first_invoice}/export", params={"format": "pdi"})
        assert export.status_code == 422
        assert sorted(export.json()["error"]["detail"]["unmapped_item_codes"]) == sorted(
            [ULTRA[1], BUD30[1], BUSCH[1], BEATBOX[1]])


class TestConfirmingProposesForThisStore:
    async def test_confirmations_become_pending_proposals_under_store_86357232(
        self, api_client, db_session, first_invoice  # noqa: F811
    ):
        r = await api_client.post(f"/api/v1/invoices/{first_invoice}/case-mappings", json={"mappings": [
            {"item_code": ULTRA[1], "units_per_case": 3},
            {"item_code": BUD30[1], "units_per_case": 1},
            {"item_code": BUSCH[1], "units_per_case": 2},
            {"item_code": BEATBOX[1], "units_per_case": 12},
        ]})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["saved"] == 4
        assert r.json()["data"]["pdi_export_allowed"] is False      # proposals are not mappings

        proposals = await ProductDataProposalRepository(db_session).list(store_number=NEW)
        by_code = {p.entity_key: p for p in proposals}
        assert {p.status for p in proposals} == {STATUS_PENDING}
        assert {p.store_number for p in proposals} == {NEW}
        assert str(by_code[ULTRA[1]].invoice_id) == first_invoice
        assert by_code[ULTRA[1]].source == SOURCE_BEER_INVENTORY_EXPLICIT
        assert by_code[BUD30[1]].source == SOURCE_REFERENCE_DERIVED
        assert by_code[BUSCH[1]].source == SOURCE_DOCUMENT_DERIVED
        assert by_code[BEATBOX[1]].source == SOURCE_OPERATOR_ENTERED
        # nothing was approved, nothing was written to master data
        assert await ProductCaseMappingRepository(db_session).get(NEW, ULTRA[1]) is None
        assert await ProductCaseMappingRepository(db_session).get(NEW, BEATBOX[1]) is None
        # the other store's rows are exactly as seeded
        assert (await ProductCaseMappingRepository(db_session).get(OLD, ULTRA[1])).units_per_case == 8
        assert (await ProductCaseMappingRepository(db_session).get(OLD, LABATT[1])).units_per_case == 30

    async def test_the_review_queue_detail_and_history_show_the_store(self, api_client, first_invoice):  # noqa: F811
        await api_client.post(f"/api/v1/invoices/{first_invoice}/case-mappings",
                              json={"mappings": [{"item_code": BUSCH[1], "units_per_case": 2}]})

        queue_new = (await api_client.get("/api/v1/proposals", params={"store_number": NEW})).json()
        assert [(row["store_number"], row["entity_key"], row["status"]) for row in queue_new["items"]] == \
            [(NEW, BUSCH[1], STATUS_PENDING)]
        queue_old = (await api_client.get("/api/v1/proposals", params={"store_number": OLD})).json()
        assert [row["entity_key"] for row in queue_old["items"]] == [BUSCH[1]]     # the OLD store's own, separate
        assert queue_old["items"][0]["id"] != queue_new["items"][0]["id"]
        by_invoice = (await api_client.get("/api/v1/proposals", params={"invoice_id": first_invoice})).json()
        assert [row["store_number"] for row in by_invoice["items"]] == [NEW]

        p = (await api_client.get(f"/api/v1/proposals/{queue_new['items'][0]['id']}")).json()["data"]
        assert p["store_number"] == NEW and p["current_master_value"] is None

        history = (await api_client.get(f"/api/v1/products/{BUSCH[1]}/history",
                                        params={"store_number": NEW})).json()["data"]
        assert history["store_number"] == NEW
        assert [q["id"] for q in history["proposals"]] == [queue_new["items"][0]["id"]]
        assert history["current_mapping"] is None


class TestOnlyStoreScopedApprovalUnblocksTheExport:
    async def test_approving_this_stores_proposals_writes_this_stores_mappings_and_opens_the_export(
        self, api_client, db_session, first_invoice  # noqa: F811
    ):
        await api_client.post(f"/api/v1/invoices/{first_invoice}/case-mappings", json={"mappings": [
            {"item_code": ULTRA[1], "units_per_case": 3},
            {"item_code": BUD30[1], "units_per_case": 1},
            {"item_code": BUSCH[1], "units_per_case": 2},
        ]})
        for p in await ProductDataProposalRepository(db_session).list(store_number=NEW, status=STATUS_PENDING):
            r = await api_client.post(f"/api/v1/proposals/{p.id}/approve", json={"reviewed_by": "data-team:test"})
            assert r.status_code == 200, r.text
            assert r.json()["data"]["applied_to"] == f"product_case_mappings:{NEW}:{p.entity_key}"

        # BEATBOX is still unmapped — three approvals do not make four
        export = await api_client.get(f"/api/v1/invoices/{first_invoice}/export", params={"format": "pdi"})
        assert export.status_code == 422
        assert export.json()["error"]["detail"]["unmapped_item_codes"] == [BEATBOX[1]]

        await api_client.post(f"/api/v1/invoices/{first_invoice}/case-mappings",
                              json={"mappings": [{"item_code": BEATBOX[1], "units_per_case": 12}]})
        [last] = await ProductDataProposalRepository(db_session).list(store_number=NEW, status=STATUS_PENDING)
        await api_client.post(f"/api/v1/proposals/{last.id}/approve", json={"reviewed_by": "data-team:test"})

        d = await detail(api_client, first_invoice)
        assert d["pdi_export_allowed"] is True
        export = await api_client.get(f"/api/v1/invoices/{first_invoice}/export", params={"format": "pdi"})
        assert export.status_code == 200
        b_records = [ln for ln in export.text.split("\r\n") if ln.startswith("B")]
        units = {ln[1:12].strip(): int(ln[53:57]) for ln in b_records}
        assert units == {LABATT[1]: 1, ULTRA[1]: 3, BUD30[1]: 1, BUSCH[1]: 2, BEATBOX[1]: 12}

        # the other store's mappings are untouched by all of it
        old = {m.item_code: m.units_per_case for m in (await db_session.execute(
            select(ProductCaseMapping).where(ProductCaseMapping.store_number == OLD))).scalars()}
        assert old == {LABATT[1]: 30, BUD30[1]: 1, ULTRA[1]: 8}
        new = {m.item_code: m.units_per_case for m in (await db_session.execute(
            select(ProductCaseMapping).where(ProductCaseMapping.store_number == NEW))).scalars()}
        assert new == {LABATT[1]: 1, ULTRA[1]: 3, BUD30[1]: 1, BUSCH[1]: 2, BEATBOX[1]: 12}
