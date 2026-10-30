"""
Shared units-per-case, store overrides, cross-store differences, the vendor
item crosswalk, export blocking and the item-master import — against Postgres.

Synthetic products only (valid check digits, no real invoice data).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.models.invoice_item import InvoiceItem
from app.models.product_case_mapping import ProductCaseMapping
from app.models.product_data_proposal import STATUS_APPROVED, STATUS_PENDING, ProductDataProposal
from app.models.product_master import MasterCommercialMapping, MasterCommercialReview
from app.models.vendor_item_crosswalk import (
    CROSSWALK_CONFIRMED,
    CROSSWALK_RETIRED,
    VendorItemCrosswalk,
)
from app.repositories.product_case_mapping_repository import ProductCaseMappingRepository
from app.repositories.product_data_proposal_repository import ProductDataProposalRepository
from app.schemas.extraction import ExtractedLineItem, ExtractedVendor
from app.services.pipeline_service import InvoiceProcessingPipeline
from app.services.product_master import item_master_import as importer
from app.services.product_master.commercial_resolution import (
    PATH_CONFLICT,
    PATH_PRODUCT_MASTER_GLOBAL,
    PATH_PRODUCT_MASTER_PHYSICAL,
    resolve_store_codes,
)
from tests.integration.conftest import (
    PHYSICAL_STORE,
    RCM_STORE,
    SOURCE_ONLY_STORE,
    requires_db,
    store_id,
)
from tests.integration.fakes import FakeStructuring, extracted_invoice
from tests.integration.test_api_db import api_client, process_file  # noqa: F401 — fixture reuse
from tests.pdf_builder import build_pdf

pytestmark = requires_db

CODE = "03600029145"          # item code of UPC 036000291452
UPC = "036000291452"
OTHER_UPC = "012000001291"
VENDOR_ITEM = "00025328"


@pytest.fixture(autouse=True)
async def clean_master(db_session):
    await db_session.execute(text("TRUNCATE master_products, vendor_item_crosswalk CASCADE"))
    await db_session.commit()


@pytest.fixture
def resolution_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "product_master_commercial_resolution", True)


async def _proposal(db_session, store_code, units, code=CODE):
    p = await ProductDataProposalRepository(db_session).create(
        store_id=store_id(store_code), entity_type="case_mapping", entity_key=code, field="units_per_case",
        proposed_value=units, current_value=None, source="operator_entered", proposed_by="testadmin",
        evidence={"invoice_description": "SYNTH COLA 12PK"},
    )
    await db_session.commit()
    return p


async def _legacy(db_session, store_code, units, code=CODE):
    await ProductCaseMappingRepository(db_session).upsert(
        store_id=store_id(store_code), item_code=code, units_per_case=units, description="SYNTH", source="APPROVED")
    await db_session.commit()


async def _shared_rows(db_session):
    return (await db_session.execute(select(MasterCommercialMapping).where(
        MasterCommercialMapping.store_id.is_(None)))).scalars().all()


class TestSharedApproval:
    async def test_approving_for_all_stores_resolves_every_other_store(self, api_client, db_session, resolution_on):  # noqa: F811
        p = await _proposal(db_session, PHYSICAL_STORE, 12)
        r = await api_client.post(f"/api/v1/proposals/{p.id}/approve", json={"scope": "shared", "note": "on paper"})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["shared_mapping_id"]
        shared = await _shared_rows(db_session)
        assert [(m.pdi_item_code, m.units_accounted_for, m.approval_state, m.source_system) for m in shared] == [
            (CODE, 12, "APPROVED", "invoice_review")]
        review = (await db_session.execute(select(MasterCommercialReview).where(
            MasterCommercialReview.mapping_id == shared[0].id))).scalars().one()
        assert (review.reviewer, review.reviewer_role, review.evidence_considered["proposal_id"]) == (
            "testadmin", "ADMIN", str(p.id))
        # The origin store keeps its own row too (the fallback when resolution is off).
        assert (await ProductCaseMappingRepository(db_session).get(store_id(PHYSICAL_STORE), CODE)).units_per_case == 12
        other = await resolve_store_codes(db_session, store_id(RCM_STORE), [CODE])
        assert (other.lines[CODE].path, other.lines[CODE].units_per_case) == (PATH_PRODUCT_MASTER_GLOBAL, 12)

    async def test_a_second_store_approving_the_same_value_writes_no_second_shared_value(self, api_client, db_session):  # noqa: F811
        for store in (PHYSICAL_STORE, RCM_STORE):
            p = await _proposal(db_session, store, 12)
            assert (await api_client.post(f"/api/v1/proposals/{p.id}/approve", json={"scope": "shared"})).status_code == 200
        assert len(await _shared_rows(db_session)) == 1

    async def test_a_store_holding_a_different_value_refuses_it_and_writes_nothing(self, api_client, db_session):  # noqa: F811
        await _legacy(db_session, RCM_STORE, 18)
        p = await _proposal(db_session, PHYSICAL_STORE, 1)
        r = await api_client.post(f"/api/v1/proposals/{p.id}/approve", json={"scope": "shared"})
        assert r.status_code == 422
        assert r.json()["error"]["detail"]["reason"] == "stores_differ"
        assert r.json()["error"]["detail"]["stores"][0]["units"] == 18
        pid = p.id
        db_session.expire_all()
        assert (await ProductDataProposalRepository(db_session).get(pid)).status == STATUS_PENDING
        assert await _shared_rows(db_session) == []
        assert await ProductCaseMappingRepository(db_session).get(store_id(PHYSICAL_STORE), CODE) is None

    async def test_keeping_the_other_stores_value_records_an_override_that_wins_there(
        self, api_client, db_session, resolution_on,  # noqa: F811
    ):
        await _legacy(db_session, RCM_STORE, 18)
        p = await _proposal(db_session, PHYSICAL_STORE, 1)
        r = await api_client.post(f"/api/v1/proposals/{p.id}/approve",
                                  json={"scope": "shared", "keep_store_values": True})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["overrides_recorded"] == 1
        rcm = (await resolve_store_codes(db_session, store_id(RCM_STORE), [CODE])).lines[CODE]
        mine = (await resolve_store_codes(db_session, store_id(PHYSICAL_STORE), [CODE])).lines[CODE]
        assert (rcm.path, rcm.units_per_case) == (PATH_PRODUCT_MASTER_PHYSICAL, 18)
        assert (mine.path, mine.units_per_case) == (PATH_PRODUCT_MASTER_GLOBAL, 1)

    async def test_a_store_value_set_after_the_shared_value_is_a_conflict_not_a_choice(
        self, api_client, db_session, resolution_on,  # noqa: F811
    ):
        p = await _proposal(db_session, PHYSICAL_STORE, 1)
        assert (await api_client.post(f"/api/v1/proposals/{p.id}/approve", json={"scope": "shared"})).status_code == 200
        await _legacy(db_session, RCM_STORE, 18)          # e.g. written before 0038 on another path
        line = (await resolve_store_codes(db_session, store_id(RCM_STORE), [CODE])).lines[CODE]
        assert (line.path, line.units_per_case) == (PATH_CONFLICT, None)

    async def test_a_store_only_approval_against_a_different_shared_value_needs_a_reason(
        self, api_client, db_session, resolution_on,  # noqa: F811
    ):
        p = await _proposal(db_session, PHYSICAL_STORE, 1)
        assert (await api_client.post(f"/api/v1/proposals/{p.id}/approve", json={"scope": "shared"})).status_code == 200
        q = await _proposal(db_session, RCM_STORE, 18)
        refused = await api_client.post(f"/api/v1/proposals/{q.id}/approve", json={})
        assert refused.status_code == 422 and refused.json()["error"]["detail"]["reason"] == "product_master_governs"
        ok = await api_client.post(f"/api/v1/proposals/{q.id}/approve",
                                   json={"override_reason": "this store sells singles"})
        assert ok.status_code == 200, ok.text
        line = (await resolve_store_codes(db_session, store_id(RCM_STORE), [CODE])).lines[CODE]
        assert (line.path, line.units_per_case) == (PATH_PRODUCT_MASTER_PHYSICAL, 18)

    async def test_bulk_shared_approval_is_all_or_nothing(self, api_client, db_session):  # noqa: F811
        await _legacy(db_session, RCM_STORE, 18, code="01200000129")
        good = await _proposal(db_session, PHYSICAL_STORE, 12)
        bad = await _proposal(db_session, PHYSICAL_STORE, 1, code="01200000129")
        good_id, bad_id = good.id, bad.id
        r = await api_client.post("/api/v1/proposals/bulk-approve",
                                  json={"proposal_ids": [str(good_id), str(bad_id)], "scope": "shared"})
        assert r.status_code == 422
        db_session.expire_all()
        assert (await ProductDataProposalRepository(db_session).get(good_id)).status == STATUS_PENDING
        assert await _shared_rows(db_session) == []


class TestCrossStoreDifferences:
    async def test_the_michelob_shape_is_listed_and_settled_with_an_audit_trail(self, api_client, db_session):  # noqa: F811
        await _legacy(db_session, PHYSICAL_STORE, 1)
        await _legacy(db_session, RCM_STORE, 18)
        listed = (await api_client.get("/api/v1/product-master/differences")).json()["data"]
        assert [(d["item_code"], d["distinct_values"]) for d in listed] == [(CODE, [1, 18])]
        r = await api_client.post(f"/api/v1/product-master/differences/{CODE}/resolve",
                                  json={"units": 1, "keep_store_ids": [], "note": "distributor sheet: case = 1"})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["adopted_store_ids"] == [str(store_id(RCM_STORE))]
        db_session.expire_all()
        assert (await ProductCaseMappingRepository(db_session).get(store_id(RCM_STORE), CODE)).units_per_case == 1
        adoption = (await db_session.execute(select(ProductDataProposal).where(
            ProductDataProposal.store_id == store_id(RCM_STORE), ProductDataProposal.source == "conflict_resolution",
        ))).scalars().one()
        assert (adoption.status, adoption.current_value, adoption.proposed_value) == (STATUS_APPROVED, 18, 1)
        assert (await api_client.get("/api/v1/product-master/differences")).json()["data"] == []

    async def test_keeping_a_store_records_its_override_and_the_difference_is_settled(self, api_client, db_session):  # noqa: F811
        await _legacy(db_session, PHYSICAL_STORE, 1)
        await _legacy(db_session, RCM_STORE, 18)
        r = await api_client.post(f"/api/v1/product-master/differences/{CODE}/resolve",
                                  json={"units": 1, "keep_store_ids": [str(store_id(RCM_STORE))], "note": "singles"})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["kept_store_ids"] == [str(store_id(RCM_STORE))]
        listed = (await api_client.get("/api/v1/product-master/differences")).json()["data"]
        assert listed == []
        db_session.expire_all()
        assert (await ProductCaseMappingRepository(db_session).get(store_id(RCM_STORE), CODE)).units_per_case == 18

    async def test_a_note_is_required(self, api_client, db_session):  # noqa: F811
        await _legacy(db_session, PHYSICAL_STORE, 1)
        r = await api_client.post(f"/api/v1/product-master/differences/{CODE}/resolve",
                                  json={"units": 1, "note": ""})
        assert r.status_code == 422

    async def test_the_shared_rows_show_in_the_approvals_queue_as_all_stores(self, api_client, db_session):  # noqa: F811
        p = await _proposal(db_session, PHYSICAL_STORE, 12)
        assert (await api_client.post(f"/api/v1/proposals/{p.id}/approve", json={"scope": "shared"})).status_code == 200
        rows = (await api_client.get("/api/v1/product-master/commercial?approval_state=APPROVED")).json()["items"]
        shared = [row for row in rows if row["pdi_item_code"] == CODE]
        assert shared and shared[0]["store_id"] is None and shared[0]["store_kind"] == "shared"


async def _process(api_client, app, items, filename, *, vendor="Synthetic Snacks LLC", number="SY-1",  # noqa: F811
                   store=PHYSICAL_STORE):
    from app.api.v1.invoices import get_pipeline

    total = round(sum(i.line_total or 0 for i in items), 2)
    app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
        structuring_service=FakeStructuring(extracted_invoice(
            vendor=ExtractedVendor(name=vendor), invoice_number=number, line_items=items,
            subtotal=total, grand_total=total)))
    accepted = await process_file(api_client, content=build_pdf([filename + " pad " * 300]), filename=filename,
                                  store=store)
    status = (await api_client.get(accepted["status_url"])).json()["data"]
    return status["invoice_id"]


def _route_line(upc=None, item=VENDOR_ITEM):
    return ExtractedLineItem(description="SYNTH CHIPS 1.5OZ", product_code=upc, supplier_item_id=item,
                             quantity=2.0, unit_price=5.0, line_total=10.0)


async def _lines(db_session, invoice_id):
    db_session.expire_all()
    return (await db_session.execute(select(InvoiceItem).where(
        InvoiceItem.invoice_id == uuid.UUID(invoice_id)).order_by(InvoiceItem.sort_order))).scalars().all()


class TestVendorItemCrosswalk:
    async def test_the_vendor_item_number_is_persisted_on_the_line(self, api_client, app, db_session):  # noqa: F811
        inv = await _process(api_client, app, [_route_line()], "a.pdf")
        assert [i.supplier_item_id for i in await _lines(db_session, inv)] == [VENDOR_ITEM]

    async def test_a_upc_entered_once_fills_the_vendors_other_open_and_later_invoices(self, api_client, app, db_session):  # noqa: F811
        first = await _process(api_client, app, [_route_line()], "a.pdf", number="SY-1")
        second = await _process(api_client, app, [_route_line()], "b.pdf", number="SY-2")
        r = await api_client.patch(f"/api/v1/invoices/{first}/items/0",
                                   json={"product_code": UPC, "remember_vendor_item": True})
        assert r.status_code == 200, r.text
        link = r.json()["data"]["vendor_item_link"]
        assert (link["vendor_item_code"], link["item_code"]) == (VENDOR_ITEM, CODE)
        assert link["applied_to_invoice_ids"] == [second]
        assert [i.product_sku for i in await _lines(db_session, second)] == [UPC]
        history = (await _lines(db_session, second))[0].correction_history
        assert history[-1]["by"] == "system:vendor-item-crosswalk"

        third = await _process(api_client, app, [_route_line()], "c.pdf", number="SY-3")
        assert [i.product_sku for i in await _lines(db_session, third)] == [UPC]
        links = (await db_session.execute(select(VendorItemCrosswalk))).scalars().all()
        assert [(x.state, x.vendor_item_code, x.item_code) for x in links] == [(CROSSWALK_CONFIRMED, VENDOR_ITEM, CODE)]
        proposal = (await db_session.execute(select(ProductDataProposal).where(
            ProductDataProposal.entity_type == "vendor_item"))).scalars().one()
        assert proposal.status == STATUS_APPROVED and proposal.vendor_id == links[0].vendor_id

    async def test_another_vendor_with_the_same_number_is_not_touched(self, api_client, app, db_session):  # noqa: F811
        first = await _process(api_client, app, [_route_line()], "a.pdf", number="SY-1")
        assert (await api_client.patch(f"/api/v1/invoices/{first}/items/0",
                                       json={"product_code": UPC, "remember_vendor_item": True})).status_code == 200
        other = await _process(api_client, app, [_route_line()], "d.pdf", vendor="Other Route Co", number="OR-1")
        assert [i.product_sku for i in await _lines(db_session, other)] == [None]

    async def test_a_printed_barcode_is_never_overwritten(self, api_client, app, db_session):  # noqa: F811
        first = await _process(api_client, app, [_route_line()], "a.pdf", number="SY-1")
        assert (await api_client.patch(f"/api/v1/invoices/{first}/items/0",
                                       json={"product_code": UPC, "remember_vendor_item": True})).status_code == 200
        printed = await _process(api_client, app, [_route_line(upc=OTHER_UPC)], "e.pdf", number="SY-4")
        assert [i.product_sku for i in await _lines(db_session, printed)] == [OTHER_UPC]

    async def test_a_retired_link_is_no_longer_applied(self, api_client, app, db_session):  # noqa: F811
        first = await _process(api_client, app, [_route_line()], "a.pdf", number="SY-1")
        r = await api_client.patch(f"/api/v1/invoices/{first}/items/0",
                                   json={"product_code": UPC, "remember_vendor_item": True})
        link_id = r.json()["data"]["vendor_item_link"]["link_id"]
        retired = await api_client.post(f"/api/v1/product-master/vendor-items/{link_id}/retire",
                                        json={"reason": "wrong flavour"})
        assert retired.status_code == 200 and retired.json()["data"]["state"] == CROSSWALK_RETIRED
        later = await _process(api_client, app, [_route_line()], "f.pdf", number="SY-5")
        assert [i.product_sku for i in await _lines(db_session, later)] == [None]

    async def test_remembering_needs_a_usable_barcode_and_a_vendor_item_number(self, api_client, app, db_session):  # noqa: F811
        inv = await _process(api_client, app, [_route_line()], "a.pdf")
        bad = await api_client.patch(f"/api/v1/invoices/{inv}/items/0",
                                     json={"product_code": "036000291453", "remember_vendor_item": True})
        assert bad.status_code == 422
        plain = await _process(api_client, app, [_route_line(item=None)], "g.pdf", number="SY-6")
        none = await api_client.patch(f"/api/v1/invoices/{plain}/items/0",
                                      json={"product_code": UPC, "remember_vendor_item": True})
        assert none.status_code == 422 and none.json()["error"]["detail"]["reason"] == "no_vendor_item_number"
        assert (await db_session.execute(select(func.count()).select_from(VendorItemCrosswalk))).scalar() == 0


class TestExportBlocking:
    async def test_a_vendor_number_printed_as_the_code_blocks_export_until_a_upc_is_entered(
        self, api_client, app, db_session,  # noqa: F811
    ):
        inv = await _process(api_client, app, [_route_line(upc="12345678", item=None)], "h.pdf")
        detail = (await api_client.get(f"/api/v1/invoices/{inv}")).json()["data"]
        assert detail["pdi_export_allowed"] is False
        assert "cannot be sent as a PDI item code" in detail["pdi_export_blocked_reason"]


def _csv(tmp_path, rows):
    path = tmp_path / "items.csv"
    path.write_text("UPC,Description,Units\n" + "\n".join(",".join(r) for r in rows) + "\n")
    return path


MAPPING = importer.ColumnMapping(source_system="item_master:test", upc="UPC", description="Description",
                                 units_per_case="Units")


class TestItemMasterImport:
    async def test_preview_classifies_apply_writes_review_candidates_and_a_rerun_writes_nothing(
        self, db_session, tmp_path,
    ):
        path = _csv(tmp_path, [
            (UPC, "SYNTH COLA 12PK", "12"),
            (UPC, "SYNTH COLA 12PK", "12"),          # identical repeat
            (OTHER_UPC, "SYNTH LEMON", "6"),
            (OTHER_UPC, "SYNTH LEMON", "24"),       # same source, different units
            ("036000291453", "BAD CHECK", "1"),      # misread barcode
            ("4006381333931", "AN EAN-13", "1"),     # not representable in PDI
            ("012000001307", "SYNTH WATER", "12.5"),  # not a whole number
        ])
        plan = await importer.preview(db_session, path, MAPPING)
        assert plan.counts() == {"NEW": 1, "EXISTING": 0, "POSSIBLE_UPDATE": 0, "DUPLICATE": 1,
                                 "CONFLICT": 2, "INVALID": 3}
        written = await importer.apply(db_session, plan, MAPPING)
        await db_session.commit()
        assert written == {"product": 1, "identifier": 1, "description": 1, "commercial_candidate": 1}
        candidate = (await _shared_rows(db_session))[0]
        assert (candidate.approval_state, candidate.units_accounted_for, candidate.source_row) == (
            "REVIEW_REQUIRED", 12, 2)
        again = await importer.preview(db_session, path, MAPPING)
        assert not any(r.writes for r in again.rows)

    async def test_an_approved_value_is_never_overwritten_by_an_import(self, api_client, db_session, tmp_path):  # noqa: F811
        p = await _proposal(db_session, PHYSICAL_STORE, 12)
        assert (await api_client.post(f"/api/v1/proposals/{p.id}/approve", json={"scope": "shared"})).status_code == 200
        plan = await importer.preview(db_session, _csv(tmp_path, [(UPC, "SYNTH COLA", "24")]), MAPPING)
        assert [(r.category, r.reason) for r in plan.rows] == [("CONFLICT", "approved_shared_value_is_12")]
        await importer.apply(db_session, plan, MAPPING)
        await db_session.commit()
        assert [m.units_accounted_for for m in await _shared_rows(db_session)] == [12]

    async def test_it_does_not_touch_store_mappings(self, db_session, tmp_path):
        before = (await db_session.execute(select(func.count()).select_from(ProductCaseMapping))).scalar()
        plan = await importer.preview(db_session, _csv(tmp_path, [(UPC, "SYNTH", "12")]), MAPPING)
        await importer.apply(db_session, plan, MAPPING)
        await db_session.commit()
        assert (await db_session.execute(select(func.count()).select_from(ProductCaseMapping))).scalar() == before

    async def test_an_imported_candidate_cannot_become_a_second_approved_shared_value(
        self, api_client, db_session, tmp_path,  # noqa: F811
    ):
        plan = await importer.preview(db_session, _csv(tmp_path, [(UPC, "SYNTH COLA", "12")]), MAPPING)
        await importer.apply(db_session, plan, MAPPING)
        await db_session.commit()
        candidate_id = (await _shared_rows(db_session))[0].id
        p = await _proposal(db_session, PHYSICAL_STORE, 12)      # agrees with the open candidate: allowed
        assert (await api_client.post(f"/api/v1/proposals/{p.id}/approve", json={"scope": "shared"})).status_code == 200
        detail = (await api_client.get(f"/api/v1/product-master/commercial/{candidate_id}")).json()["data"]
        r = await api_client.post(f"/api/v1/product-master/commercial/{candidate_id}/approve",
                                  json={"note": "sheet says 12", "expected_review_version": detail["candidate"]["review_version"]})
        assert r.status_code == 422, r.text
        assert r.json()["error"]["detail"]["reason"] == "shared_value_exists"


MICHELOB_UPC, MICHELOB_CODE = "018200967214", "01820096721"


def _c18_line():
    return ExtractedLineItem(description="MICHELOB ULTRA C-18 12OZ", product_code=MICHELOB_UPC, pack_size="C-18 12OZ",
                             quantity=1.0, unit_price=17.7, line_total=17.7)


class TestTheEighteenPackSafeguard:
    """The replay's two incorrect automatic lines, end to end: a shared 1 never silently prices a C-18 at 18-store."""

    async def _shared_one(self, api_client, db_session):  # noqa: F811
        p = await _proposal(db_session, PHYSICAL_STORE, 1, code=MICHELOB_CODE)
        r = await api_client.post(f"/api/v1/proposals/{p.id}/approve", json={"scope": "shared"})
        assert r.status_code == 200, r.text

    async def _row(self, api_client, invoice_id):  # noqa: F811
        data = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        return data, next(r for r in data["case_mappings"] if r["item_code"] == MICHELOB_CODE)

    async def test_the_other_store_is_asked_once_instead_of_resolving_silently(
        self, api_client, app, db_session, resolution_on,  # noqa: F811
    ):
        await self._shared_one(api_client, db_session)
        inv = await _process(api_client, app, [_c18_line()], "m.pdf", vendor="Synthetic Beer Co", store=RCM_STORE)
        data, row = await self._row(api_client, inv)
        assert (row["mapped"], row["resolution_path"]) == (False, "SHARED_NEEDS_STORE_CONFIRMATION")
        assert data["pdi_export_allowed"] is False
        queue = (await api_client.get("/api/v1/mapping-queue")).json()["items"]
        assert any(g["item_code"] == MICHELOB_CODE and g["store"]["id"] == str(store_id(RCM_STORE)) for g in queue)

        # The store confirms the shared value once; from then on it resolves from it.
        q = await _proposal(db_session, RCM_STORE, 1, code=MICHELOB_CODE)
        assert (await api_client.post(f"/api/v1/proposals/{q.id}/approve", json={})).status_code == 200
        data, row = await self._row(api_client, inv)
        assert (row["mapped"], row["units_per_case"], row["resolution_path"]) == (True, 1, "PRODUCT_MASTER_GLOBAL")

    async def test_or_records_its_own_18_as_an_override_with_a_reason(
        self, api_client, app, db_session, resolution_on,  # noqa: F811
    ):
        await self._shared_one(api_client, db_session)
        inv = await _process(api_client, app, [_c18_line()], "n.pdf", vendor="Synthetic Beer Co", store=RCM_STORE)
        q = await _proposal(db_session, RCM_STORE, 18, code=MICHELOB_CODE)
        assert (await api_client.post(f"/api/v1/proposals/{q.id}/approve", json={})).status_code == 422
        r = await api_client.post(f"/api/v1/proposals/{q.id}/approve",
                                  json={"override_reason": "this store sells the cans singly"})
        assert r.status_code == 200, r.text
        _, row = await self._row(api_client, inv)
        assert (row["mapped"], row["units_per_case"], row["resolution_path"]) == (
            True, 18, "PRODUCT_MASTER_PHYSICAL_STORE")

    async def test_the_origin_store_itself_is_never_held(self, api_client, app, db_session, resolution_on):  # noqa: F811
        await self._shared_one(api_client, db_session)
        inv = await _process(api_client, app, [_c18_line()], "o.pdf", vendor="Synthetic Beer Co")
        _, row = await self._row(api_client, inv)
        assert (row["mapped"], row["units_per_case"]) == (True, 1)

    async def test_a_source_identitys_decision_never_becomes_the_shared_value(self, api_client, db_session):  # noqa: F811
        p = await _proposal(db_session, SOURCE_ONLY_STORE, 1, code=MICHELOB_CODE)
        r = await api_client.post(f"/api/v1/proposals/{p.id}/approve", json={"scope": "shared"})
        assert r.status_code == 422 and r.json()["error"]["detail"]["reason"] == "origin_not_physical"
        assert await _shared_rows(db_session) == []
