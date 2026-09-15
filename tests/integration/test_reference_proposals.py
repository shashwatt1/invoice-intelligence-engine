"""
tests/integration/test_reference_proposals.py — reference evidence becomes
PENDING proposals, and only approval makes it real.

The chain under test:

    Beer Inventory row  ->  product_pricing (with provenance)
                        ->  suggestion (reference_explicit / _package / _ratio)
                        ->  PENDING proposal carrying file/sheet/row
                        ->  reviewer approves  ->  product_case_mappings
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.models.product_data_proposal import (
    SOURCE_BEER_INVENTORY_EXPLICIT,
    SOURCE_BEER_INVENTORY_PACKAGE,
    STATUS_PENDING,
)
from app.models.product_reference import BASIS_PROMO, DERIVED_FROM_PACKAGE, ProductPricing
from app.repositories.product_case_mapping_repository import ProductCaseMappingRepository
from app.repositories.product_data_proposal_repository import (
    ProductDataProposalRepository,
)
from app.schemas.extraction import ExtractedLineItem
from app.services import proposal_service
from app.services.pipeline_service import InvoiceProcessingPipeline
from tests.integration.conftest import requires_db, store_id
from tests.integration.fakes import FakeStructuring, extracted_invoice
from tests.integration.test_api_db import api_client, process_file  # noqa: F401 — fixture reuse
from tests.pdf_builder import build_pdf

pytestmark = requires_db

STORE = "47708760"
ULTRA_38 = "01820023986"       # ULTRA 3/8/16 — explicit on Sheet1 row 56
ULTRA_212 = "01820006991"      # ULTRA 2/12 CAN — Zink package "24/12OZ 2/12 CANS"


def pricing(item_code, **over):
    base = {
        "store_id": store_id(STORE), "item_code": item_code, "distributor": "Testani",
        "pricing_basis": BASIS_PROMO, "case_cost": Decimal("27.60"), "unit_cost": Decimal("9.20"),
        "source_file": "Beer Inventory.xlsx", "source_sheet": "Sheet1", "source_row": 56,
        "imported_at": datetime.now(UTC),
    }
    base.update(over)
    return ProductPricing(**base)


async def process(api_client, app, items, filename, pad, **totals):  # noqa: F811
    from app.api.v1.invoices import get_pipeline

    app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
        structuring_service=FakeStructuring(extracted_invoice(line_items=items, **totals))
    )
    accepted = await process_file(
        api_client, content=build_pdf([pad + " pad " * 300]), filename=filename
    )
    return (await api_client.get(accepted["status_url"])).json()["data"]["invoice_id"]


def line(description, upc, cost):
    return ExtractedLineItem(description=description, product_code=upc,
                             quantity=1.0, unit_price=cost, line_total=cost)


class TestEvidenceSurfacesAsASuggestion:
    async def test_an_explicit_row_gives_reference_explicit(self, api_client, app, db_session):  # noqa: F811
        db_session.add(pricing(ULTRA_38, items_per_case_stated=3, package="24/16 CAN 3/8"))
        await db_session.commit()
        invoice_id = await process(api_client, app, [line("ULTRA 3/8/16", "018200239861", 27.60)],
                                   "exp.pdf", "explicit", subtotal=27.60, grand_total=27.60)
        [row] = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]["case_mappings"]

        assert row["suggested_units_per_case"] == 3
        assert row["suggestion_source"] == "reference_explicit"
        assert row["mapped"] is False                     # still only a suggestion

    async def test_a_package_row_gives_reference_package(self, api_client, app, db_session):  # noqa: F811
        db_session.add(pricing(ULTRA_212, distributor="Zink", source_sheet="Zink - Tiki",
                               source_row=46, package="24/12OZ 2/12 CANS",
                               items_per_case_derived=2, items_per_case_derivation=DERIVED_FROM_PACKAGE,
                               case_cost=Decimal("27.55"), unit_cost=Decimal("13.775")))
        await db_session.commit()
        invoice_id = await process(api_client, app, [line("ULTRA 2/12 CAN", "018200069918", 25.25)],
                                   "pkg.pdf", "package", subtotal=25.25, grand_total=25.25)
        [row] = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]["case_mappings"]

        assert row["suggested_units_per_case"] == 2
        assert row["suggestion_source"] == "reference_package"

    async def test_explicit_outranks_package_outranks_ratio(self, api_client, app, db_session):  # noqa: F811
        # Three rows for one UPC, disagreeing on purpose: the typed cell wins.
        db_session.add(pricing(ULTRA_38, items_per_case_stated=3, source_row=1))
        db_session.add(pricing(ULTRA_38, source_sheet="Zink - Tiki", source_row=2,
                               package="24/16OZ 8/3 CANS", items_per_case_derived=8,
                               items_per_case_derivation=DERIVED_FROM_PACKAGE))
        db_session.add(pricing(ULTRA_38, source_sheet="Monarch Package", source_row=3,
                               items_per_case_derived=24, items_per_case_derivation="ratio"))
        await db_session.commit()
        invoice_id = await process(api_client, app, [line("ULTRA 3/8/16", "018200239861", 27.60)],
                                   "prec.pdf", "precedence", subtotal=27.60, grand_total=27.60)
        [row] = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]["case_mappings"]

        assert row["suggested_units_per_case"] == 3
        assert row["suggestion_source"] == "reference_explicit"

    async def test_a_conflicted_row_is_never_evidence(self, api_client, app, db_session):  # noqa: F811
        db_session.add(pricing(ULTRA_38, items_per_case_derived=None, is_conflicted=True,
                               conflict_detail={"items_per_case": {"a": 24, "b": 1}}))
        await db_session.commit()
        invoice_id = await process(api_client, app, [line("ULTRA 3/8/16", "018200239861", 27.60)],
                                   "conf.pdf", "conflicted", subtotal=27.60, grand_total=27.60)
        [row] = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]["case_mappings"]

        assert not (row["suggestion_source"] or "").startswith("reference")


class TestConfirmingAReferenceSuggestionProposesWithProvenance:
    async def test_the_proposal_names_the_workbook_source(self, api_client, app, db_session):  # noqa: F811
        db_session.add(pricing(ULTRA_38, items_per_case_stated=3, package="24/16 CAN 3/8"))
        await db_session.commit()
        invoice_id = await process(api_client, app, [line("ULTRA 3/8/16", "018200239861", 27.60)],
                                   "prov.pdf", "provenance", subtotal=27.60, grand_total=27.60)
        await api_client.post(f"/api/v1/invoices/{invoice_id}/case-mappings",
                              json={"mappings": [{"item_code": ULTRA_38, "units_per_case": 3}]})

        [p] = await ProductDataProposalRepository(db_session).list(entity_key=ULTRA_38)
        assert p.status == STATUS_PENDING
        assert p.source == SOURCE_BEER_INVENTORY_EXPLICIT
        assert p.evidence["suggestion_source"] == "reference_explicit"
        assert await ProductCaseMappingRepository(db_session).get(store_id(STORE), ULTRA_38) is None   # not yet

    async def test_a_package_confirmation_is_labelled_as_such(self, api_client, app, db_session):  # noqa: F811
        db_session.add(pricing(ULTRA_212, distributor="Zink", source_sheet="Zink - Tiki",
                               source_row=46, package="24/12OZ 2/12 CANS",
                               items_per_case_derived=2, items_per_case_derivation=DERIVED_FROM_PACKAGE))
        await db_session.commit()
        invoice_id = await process(api_client, app, [line("ULTRA 2/12 CAN", "018200069918", 25.25)],
                                   "pkg2.pdf", "package two", subtotal=25.25, grand_total=25.25)
        await api_client.post(f"/api/v1/invoices/{invoice_id}/case-mappings",
                              json={"mappings": [{"item_code": ULTRA_212, "units_per_case": 2}]})
        [p] = await ProductDataProposalRepository(db_session).list(entity_key=ULTRA_212)
        assert p.source == SOURCE_BEER_INVENTORY_PACKAGE


class TestOnlyApprovalReachesTheEdi:
    async def test_pending_blocks_approved_opens_rejected_blocks(self, api_client, app, db_session):  # noqa: F811
        db_session.add(pricing(ULTRA_38, items_per_case_stated=3))
        await db_session.commit()
        invoice_id = await process(api_client, app, [line("ULTRA 3/8/16", "018200239861", 27.60)],
                                   "gate.pdf", "gate", subtotal=27.60, grand_total=27.60)
        url = f"/api/v1/invoices/{invoice_id}/export"

        # Pending: blocked.
        await api_client.post(f"/api/v1/invoices/{invoice_id}/case-mappings",
                              json={"mappings": [{"item_code": ULTRA_38, "units_per_case": 3}]})
        assert (await api_client.get(url, params={"format": "pdi"})).status_code == 422

        # Rejected: still blocked, mapping never written.
        [p] = await ProductDataProposalRepository(db_session).list(entity_key=ULTRA_38)
        await proposal_service.reject(db_session, p, reviewed_by="r", note="checking")
        await db_session.commit()
        assert (await api_client.get(url, params={"format": "pdi"})).status_code == 422
        assert await ProductCaseMappingRepository(db_session).get(store_id(STORE), ULTRA_38) is None

        # A fresh proposal, approved: the EDI carries it.
        await api_client.post(f"/api/v1/invoices/{invoice_id}/case-mappings",
                              json={"mappings": [{"item_code": ULTRA_38, "units_per_case": 3}]})
        [p2] = await ProductDataProposalRepository(db_session).list(entity_key=ULTRA_38,
                                                                     status=STATUS_PENDING)
        await proposal_service.approve(db_session, p2, reviewed_by="r")
        await db_session.commit()
        export = await api_client.get(url, params={"format": "pdi"})
        assert export.status_code == 200
        detail_line = export.text.splitlines()[1]
        assert detail_line[53:57] == "0003"
        assert detail_line[43:49] == "002760"   # the INVOICE cost, not a reference cost
        assert len(detail_line) == 70


class TestRetailEvidence:
    """Item Sales Avg Price -> units-per-case -> PENDING proposal."""

    def _period_row(self, code, retail, row):
        return ProductPricing(
            store_id=store_id(STORE), item_code=code, distributor="store", pricing_basis="period_average",
            unit_retail=Decimal(retail), unit_cost=None,
            source_file="Mckinley-07-24_to_07-26.xlsx", source_sheet="data", source_row=row,
            imported_at=datetime.now(UTC),
        )

    async def test_a_30_pack_that_scans_at_case_price_suggests_one(self, api_client, app, db_session):  # noqa: F811
        db_session.add(self._period_row("01820011030", "25.7217", 553))
        await db_session.commit()
        invoice_id = await process(api_client, app, [line("BUD 30 PACK CANS", "018200110306", 22.70)],
                                   "r30.pdf", "thirty", subtotal=22.70, grand_total=22.70)
        [row] = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]["case_mappings"]

        assert row["suggested_units_per_case"] == 1
        assert row["suggestion_source"] == "reference_retail"
        assert row["mapped"] is False

    async def test_fireball_resolves_to_eight_retail_six_packs(self, api_client, app, db_session):  # noqa: F811
        db_session.add(self._period_row("08800404091", "11.99", 1119))
        await db_session.commit()
        invoice_id = await process(api_client, app, [line("FIREBALL 100ML 8/6PK", "088004040918", 66.86)],
                                   "rfb.pdf", "fireball", subtotal=66.86, grand_total=66.86)
        [row] = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]["case_mappings"]

        # 8 six-packs per case; the "6" is bottles inside the retail unit.
        assert row["suggested_units_per_case"] == 8
        assert row["suggestion_source"] == "reference_retail"
        assert row["suggestion_candidates"] == []            # the ambiguity is resolved

    async def test_an_ambiguous_retail_reading_gives_no_reference_suggestion(
        self, api_client, app, db_session  # noqa: F811
    ):
        db_session.add(self._period_row("68474680041", "2.1943", 1215))
        await db_session.commit()
        invoice_id = await process(api_client, app, [line("CLUBTAILS LONG ISLAN", "684746800416", 34.50)],
                                   "rcl.pdf", "clubtails", subtotal=34.50, grand_total=34.50)
        [row] = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]["case_mappings"]

        assert not (row["suggestion_source"] or "").startswith("reference")
        assert row["suggested_units_per_case"] is None

    async def test_a_retail_tie_is_broken_only_by_the_documents_own_pack_reading(
        self, api_client, app, db_session  # noqa: F811
    ):
        # PLAT SELTZ 15/25: retail alone allows 15 or 16; the invoice
        # prints "15/25", and the two agree.
        db_session.add(self._period_row("01820026128", "3.29", 586))
        await db_session.commit()
        invoice_id = await process(api_client, app, [line("PLAT SELTZ 15/25 BLO", "018200261282", 34.20)],
                                   "rps.pdf", "platinum", subtotal=34.20, grand_total=34.20)
        [row] = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]["case_mappings"]

        assert row["suggested_units_per_case"] == 15
        assert row["suggestion_source"] == "reference_retail"

    async def test_retail_evidence_proposes_and_never_maps(self, api_client, app, db_session):  # noqa: F811
        db_session.add(self._period_row("01820011030", "25.7217", 553))
        await db_session.commit()
        invoice_id = await process(api_client, app, [line("BUD 30 PACK CANS", "018200110306", 22.70)],
                                   "rprop.pdf", "propose", subtotal=22.70, grand_total=22.70)
        await api_client.post(f"/api/v1/invoices/{invoice_id}/case-mappings",
                              json={"mappings": [{"item_code": "01820011030", "units_per_case": 1}]})

        [p] = await ProductDataProposalRepository(db_session).list(entity_key="01820011030")
        assert p.status == STATUS_PENDING
        assert p.source == "reference_derived"
        assert p.evidence["suggestion_source"] == "reference_retail"
        assert await ProductCaseMappingRepository(db_session).get(store_id(STORE), "01820011030") is None
        export = await api_client.get(f"/api/v1/invoices/{invoice_id}/export", params={"format": "pdi"})
        assert export.status_code == 422
