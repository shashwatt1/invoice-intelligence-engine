"""
tests/integration/test_multi_store.py — two stores, one barcode.

The same UPC exists in both stores' reference data and may well be
sold as a different unit in each. These tests pin that a store's
evidence, mappings, review queue and export gate are its own: an
invoice meets only the reference rows, mappings and proposals of the
store it was received for, and a decision made for one store cannot
reach the other.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from app.models.invoice import Invoice
from app.models.product_case_mapping import ProductCaseMapping
from app.models.product_data_proposal import STATUS_PENDING
from app.models.product_reference import BASIS_PERIOD_AVERAGE, ProductIdentity, ProductPricing
from app.repositories.product_case_mapping_repository import ProductCaseMappingRepository
from app.repositories.product_data_proposal_repository import ProductDataProposalRepository
from app.services import proposal_service
from app.services.pipeline_service import InvoiceProcessingPipeline
from app.services.store_reference_service import match_invoice_against_reference
from tests.integration.conftest import requires_db
from tests.integration.fakes import FakeStructuring, extracted_invoice
from tests.integration.test_api_db import api_client, process_file  # noqa: F401 — fixture reuse
from tests.integration.test_proposal_governance import line
from tests.pdf_builder import build_pdf

pytestmark = requires_db

A, B = "47708760", "86357232"
UPC_RAW, UPC = "062067051468", "06206705146"          # LABATT BLUE 30 — in both stores' data


async def process_for(api_client, app, store, items, filename, pad, **totals):  # noqa: F811
    from app.api.v1.invoices import get_pipeline

    app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
        structuring_service=FakeStructuring(extracted_invoice(line_items=items, **totals))
    )
    accepted = await process_file(
        api_client, content=build_pdf([pad + " pad " * 300]), filename=filename, store=store
    )
    return (await api_client.get(accepted["status_url"])).json()["data"]["invoice_id"]


async def detail(api_client, invoice_id):  # noqa: F811
    return (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]


async def seed_reference(db_session):
    """The same UPC in both stores, with different retail and descriptions."""
    now = datetime.now(UTC)
    db_session.add_all([
        ProductPricing(store_number=A, item_code=UPC, distributor="store",
                       pricing_basis=BASIS_PERIOD_AVERAGE, unit_cost=None,
                       unit_retail=Decimal("26.99"), source_file="Mckinley.xlsx",
                       source_sheet="data", source_row=5, imported_at=now),
        ProductPricing(store_number=B, item_code=UPC, distributor="store",
                       pricing_basis=BASIS_PERIOD_AVERAGE, unit_cost=Decimal("23.40"),
                       unit_retail=Decimal("29.79"), source_file="Item_Sales_Summary_x.xlsx",
                       source_sheet="data", source_row=9, imported_at=now),
        ProductIdentity(store_number=A, item_code=UPC, description="Labatt blue 30cans", provenance={}),
        ProductIdentity(store_number=B, item_code=UPC, description="Labatts Blue 30pk", provenance={}),
    ])
    await db_session.commit()


async def pending_for(db_session, store, value, key=UPC):
    p = await ProductDataProposalRepository(db_session).create(
        store_number=store, entity_type="case_mapping", entity_key=key,
        field="units_per_case", proposed_value=value, current_value=None,
        source="operator_entered", proposed_by="test", evidence={},
    )
    await db_session.commit()
    return p


class TestMappingsAreStoreScoped:
    async def test_the_same_upc_in_two_stores_is_two_independent_mappings(self, db_session):
        repo = ProductCaseMappingRepository(db_session)
        await repo.upsert(store_number=A, item_code=UPC, units_per_case=1, source="MANUAL")
        await repo.upsert(store_number=B, item_code=UPC, units_per_case=30, source="MANUAL")
        await db_session.commit()

        assert (await repo.get(A, UPC)).units_per_case == 1
        assert (await repo.get(B, UPC)).units_per_case == 30
        assert await repo.units_by_item_code(A, [UPC]) == {UPC: 1}
        assert await repo.units_by_item_code(B, [UPC]) == {UPC: 30}
        rows = (await db_session.execute(select(ProductCaseMapping).where(ProductCaseMapping.item_code == UPC))).scalars().all()
        assert sorted(r.store_number for r in rows) == [A, B]

    async def test_a_store_with_no_mapping_sees_nothing_however_sure_the_other_store_is(self, db_session):
        repo = ProductCaseMappingRepository(db_session)
        await repo.upsert(store_number=A, item_code=UPC, units_per_case=1, source="MANUAL")
        await db_session.commit()
        assert await repo.get(B, UPC) is None
        assert await repo.units_by_item_code(B, [UPC]) == {}

    async def test_approving_a_proposal_for_store_a_cannot_change_store_b(self, db_session):
        repo = ProductCaseMappingRepository(db_session)
        await repo.upsert(store_number=B, item_code=UPC, units_per_case=30, source="MANUAL")
        await db_session.commit()

        p = await pending_for(db_session, A, 1)
        result = await proposal_service.approve(db_session, p, reviewed_by="reviewer:a")
        await db_session.commit()

        assert result.applied_to == f"product_case_mappings:{A}:{UPC}"
        a = await repo.get(A, UPC)
        assert (a.units_per_case, a.approved_proposal_id) == (1, p.id)
        b = await repo.get(B, UPC)
        assert (b.units_per_case, b.approved_proposal_id) == (30, None)     # untouched

    async def test_a_proposal_carries_its_store_into_the_mapping_not_the_callers(self, db_session):
        # There is no store argument to approve(): the proposal decides.
        p = await pending_for(db_session, B, 30)
        await proposal_service.approve(db_session, p, reviewed_by="r")
        await db_session.commit()
        assert await ProductCaseMappingRepository(db_session).get(A, UPC) is None
        assert (await ProductCaseMappingRepository(db_session).get(B, UPC)).units_per_case == 30


class TestTheInvoiceStoreDecides:
    async def test_the_invoice_persists_its_store_and_the_api_shows_it(self, api_client, app, db_session):  # noqa: F811
        inv_a = await process_for(api_client, app, A, [line("LABATT BLUE 30", UPC_RAW, 22.70)],
                                  "a.pdf", "store a", subtotal=22.70, grand_total=22.70)
        inv_b = await process_for(api_client, app, B, [line("LABATT BLUE 30", UPC_RAW, 22.70)],
                                  "b.pdf", "store b", subtotal=22.70, grand_total=22.70)
        assert (await detail(api_client, inv_a))["store_number"] == A
        assert (await detail(api_client, inv_b))["store_number"] == B
        stored = {str(i.id): i.store_number for i in (await db_session.execute(select(Invoice))).scalars()}
        assert stored == {inv_a: A, inv_b: B}
        history = (await api_client.get("/api/v1/invoices")).json()["items"]
        assert {h["invoice_id"]: h["store_number"] for h in history} == {inv_a: A, inv_b: B}

    async def test_reference_matching_uses_only_the_invoices_store(self, api_client, app, db_session):  # noqa: F811
        await seed_reference(db_session)
        inv_a = await process_for(api_client, app, A, [line("LABATT BLUE 30", UPC_RAW, 22.70)],
                                  "ra.pdf", "ref a", subtotal=22.70, grand_total=22.70)
        inv_b = await process_for(api_client, app, B, [line("LABATT BLUE 30", UPC_RAW, 22.70)],
                                  "rb.pdf", "ref b", subtotal=22.70, grand_total=22.70)

        invoice_a = (await db_session.execute(select(Invoice).where(Invoice.id == inv_a))).scalar_one()
        invoice_b = (await db_session.execute(select(Invoice).where(Invoice.id == inv_b))).scalar_one()
        await db_session.refresh(invoice_a, ["items"])
        await db_session.refresh(invoice_b, ["items"])
        match_a = (await match_invoice_against_reference(db_session, invoice_a))[UPC]
        match_b = (await match_invoice_against_reference(db_session, invoice_b))[UPC]

        assert match_a.reference_description == "Labatt blue 30cans"
        assert match_b.reference_description == "Labatts Blue 30pk"
        # every piece of evidence names a source row from the invoice's own store
        assert {e.source_file for e in match_a.all_evidence} <= {"Mckinley.xlsx"}
        assert {e.source_file for e in match_b.all_evidence} <= {"Item_Sales_Summary_x.xlsx"}
        # and the API's review row carries the store's own description
        assert (await detail(api_client, inv_a))["case_mappings"][0]["reference_description"] == "Labatt blue 30cans"
        assert (await detail(api_client, inv_b))["case_mappings"][0]["reference_description"] == "Labatts Blue 30pk"

    async def test_case_mapping_lookup_and_export_readiness_follow_the_invoice_store(self, api_client, app, db_session):  # noqa: F811
        await ProductCaseMappingRepository(db_session).upsert(
            store_number=A, item_code=UPC, units_per_case=1, source="MANUAL")
        await db_session.commit()
        inv_a = await process_for(api_client, app, A, [line("LABATT BLUE 30", UPC_RAW, 22.70)],
                                  "ma.pdf", "map a", subtotal=22.70, grand_total=22.70)
        inv_b = await process_for(api_client, app, B, [line("LABATT BLUE 30", UPC_RAW, 22.70)],
                                  "mb.pdf", "map b", subtotal=22.70, grand_total=22.70)

        a, b = await detail(api_client, inv_a), await detail(api_client, inv_b)
        assert a["case_mappings"][0]["mapped"] is True and a["case_mappings"][0]["units_per_case"] == 1
        assert a["pdi_export_allowed"] is True
        assert b["case_mappings"][0]["mapped"] is False and b["case_mappings"][0]["units_per_case"] is None
        assert b["pdi_export_allowed"] is False
        assert (await api_client.get(f"/api/v1/invoices/{inv_a}/export", params={"format": "pdi"})).status_code == 200
        export_b = await api_client.get(f"/api/v1/invoices/{inv_b}/export", params={"format": "pdi"})
        assert export_b.status_code == 422
        assert UPC in export_b.json()["error"]["detail"]["unmapped_item_codes"]

    async def test_confirming_on_a_store_b_invoice_queues_a_store_b_proposal(self, api_client, app, db_session):  # noqa: F811
        inv_b = await process_for(api_client, app, B, [line("LABATT BLUE 30", UPC_RAW, 22.70)],
                                  "pb.pdf", "propose b", subtotal=22.70, grand_total=22.70)
        r = await api_client.post(f"/api/v1/invoices/{inv_b}/case-mappings",
                                  json={"mappings": [{"item_code": UPC, "units_per_case": 30}]})
        assert r.status_code == 200, r.text
        [p] = await ProductDataProposalRepository(db_session).list(entity_key=UPC)
        assert (p.store_number, p.status) == (B, STATUS_PENDING)
        # visible on the store-B invoice, invisible to a store-A invoice
        assert (await detail(api_client, inv_b))["case_mappings"][0]["pending_value"] == 30
        inv_a = await process_for(api_client, app, A, [line("LABATT BLUE 30", UPC_RAW, 22.70)],
                                  "pa.pdf", "propose a", subtotal=22.70, grand_total=22.70)
        assert (await detail(api_client, inv_a))["case_mappings"][0]["pending_value"] is None


class TestHistoryAndDeletion:
    async def test_product_history_is_store_specific(self, api_client, db_session):  # noqa: F811
        pa = await pending_for(db_session, A, 1)
        pb = await pending_for(db_session, B, 30)
        await api_client.post(f"/api/v1/proposals/{pa.id}/approve", json={"reviewed_by": "r"})

        ha = (await api_client.get(f"/api/v1/products/{UPC}/history", params={"store_number": A})).json()["data"]
        hb = (await api_client.get(f"/api/v1/products/{UPC}/history", params={"store_number": B})).json()["data"]
        assert ha["store_number"] == A and [p["id"] for p in ha["proposals"]] == [str(pa.id)]
        assert ha["current_mapping"]["units_per_case"] == 1 and ha["current_mapping"]["store_number"] == A
        assert hb["store_number"] == B and [p["id"] for p in hb["proposals"]] == [str(pb.id)]
        assert hb["current_mapping"] is None
        # the queue filters by store too
        queue_b = (await api_client.get("/api/v1/proposals", params={"store_number": B})).json()
        assert [row["id"] for row in queue_b["items"]] == [str(pb.id)]

    async def test_deleting_an_invoice_leaves_master_mappings_alone(self, api_client, app, db_session):  # noqa: F811
        repo = ProductCaseMappingRepository(db_session)
        await repo.upsert(store_number=A, item_code=UPC, units_per_case=1, source="MANUAL")
        await repo.upsert(store_number=B, item_code=UPC, units_per_case=30, source="MANUAL")
        await db_session.commit()
        inv_a = await process_for(api_client, app, A, [line("LABATT BLUE 30", UPC_RAW, 22.70)],
                                  "da.pdf", "delete a", subtotal=22.70, grand_total=22.70)
        assert (await api_client.delete(f"/api/v1/invoices/{inv_a}")).status_code in (200, 204)
        assert (await api_client.get(f"/api/v1/invoices/{inv_a}")).status_code == 404
        assert (await repo.get(A, UPC)).units_per_case == 1
        assert (await repo.get(B, UPC)).units_per_case == 30

    async def test_known_stores_are_listed_from_the_data(self, api_client, db_session):  # noqa: F811
        await ProductCaseMappingRepository(db_session).upsert(
            store_number=A, item_code=UPC, units_per_case=1, source="MANUAL")
        await seed_reference(db_session)
        stores = (await api_client.get("/api/v1/stores")).json()["data"]
        assert [s["store_number"] for s in stores] == [A, B]
        assert stores[0]["case_mappings"] == 1 and stores[1]["case_mappings"] == 0
        assert stores[0]["pricing_rows"] == 1 and stores[1]["pricing_rows"] == 1
