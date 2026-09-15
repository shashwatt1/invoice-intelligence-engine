"""
tests/integration/test_proposal_governance.py — the approval gate.

Pins the rule that cost us ten bad mappings on Testani: a value entered
through the frontend must never become authoritative reusable data on
its own. The frontend proposes; a reviewer promotes; only then does the
formatter see it.
"""

from __future__ import annotations

import pytest

from app.models.product_data_proposal import (
    SOURCE_DOCUMENT_AMBIGUOUS,
    SOURCE_LEGACY_MIGRATED,
    SOURCE_OPERATOR_ENTERED,
    SOURCE_REFERENCE_DERIVED,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
)
from app.repositories.product_case_mapping_repository import ProductCaseMappingRepository
from app.repositories.product_data_proposal_repository import (
    ProductDataProposalRepository,
    ProposalImmutableError,
)
from app.schemas.extraction import ExtractedLineItem
from app.services import proposal_service
from app.services.pipeline_service import InvoiceProcessingPipeline
from tests.integration.conftest import requires_db, store_id
from tests.integration.fakes import FakeStructuring, extracted_invoice
from tests.integration.test_api_db import api_client, process_file  # noqa: F401 — fixture reuse
from tests.pdf_builder import build_pdf

pytestmark = requires_db

UPC = "018200000638"          # BUSCH 4/6/16OZ as printed
NORMALIZED = "01820000063"
STORE = "47708760"


async def process(api_client, app, items, filename, pad, **totals):  # noqa: F811
    from app.api.v1.invoices import get_pipeline

    app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
        structuring_service=FakeStructuring(extracted_invoice(line_items=items, **totals))
    )
    accepted = await process_file(
        api_client, content=build_pdf([pad + " pad " * 300]), filename=filename
    )
    status = (await api_client.get(accepted["status_url"])).json()["data"]
    return status["invoice_id"]


def line(description="BUSCH 4/6/160Z CAN", upc=UPC, unit_price=18.75):
    return ExtractedLineItem(description=description, product_code=upc,
                             quantity=1.0, unit_price=unit_price, line_total=unit_price)


async def confirm(api_client, invoice_id, units, code=NORMALIZED):  # noqa: F811
    return await api_client.post(
        f"/api/v1/invoices/{invoice_id}/case-mappings",
        json={"mappings": [{"item_code": code, "units_per_case": units}]},
    )


class TestTheFrontendCannotWriteMasterData:
    async def test_confirm_creates_a_pending_proposal_only(self, api_client, app, db_session):  # noqa: F811
        invoice_id = await process(api_client, app, [line()], "p1.pdf", "proposal one",
                                   subtotal=18.75, grand_total=18.75)
        response = await confirm(api_client, invoice_id, 4)
        assert response.status_code == 200, response.text
        data = response.json()["data"]

        assert data["saved"] == 1
        [row] = data["case_mappings"]
        assert row["mapped"] is False                    # NOT authoritative
        assert row["units_per_case"] is None
        assert row["pending_value"] == 4                 # queued, visibly
        assert row["pending_proposal_id"] is not None
        assert data["pdi_export_allowed"] is False       # still blocked

        # …and a plain GET of the invoice shows the queued value too — the
        # operator must not be invited to submit it a second time.
        detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        assert detail["case_mappings"][0]["pending_value"] == 4
        assert detail["case_mappings"][0]["pending_proposal_id"] == row["pending_proposal_id"]

        # The authoritative table is untouched.
        assert await ProductCaseMappingRepository(db_session).get(store_id(STORE), NORMALIZED) is None
        [p] = await ProductDataProposalRepository(db_session).list(entity_key=NORMALIZED)
        assert p.status == STATUS_PENDING
        assert p.proposed_value == 4
        assert p.proposed_by == "frontend:review-ui"
        assert str(p.invoice_id) == invoice_id

    async def test_the_export_stays_blocked_while_pending(self, api_client, app):  # noqa: F811
        invoice_id = await process(api_client, app, [line()], "p2.pdf", "pending gate",
                                   subtotal=18.75, grand_total=18.75)
        await confirm(api_client, invoice_id, 4)
        export = await api_client.get(f"/api/v1/invoices/{invoice_id}/export",
                                      params={"format": "pdi"})
        assert export.status_code == 422
        assert NORMALIZED in export.json()["error"]["detail"]["unmapped_item_codes"]

    async def test_the_update_path_also_only_proposes(self, api_client, app, db_session):  # noqa: F811
        # An existing (legacy) mapping; the UI's Update sends the same
        # request shape. It must not touch the mapping either.
        await ProductCaseMappingRepository(db_session).upsert(
            store_id=store_id(STORE), item_code=NORMALIZED, units_per_case=4, source="MANUAL")
        await db_session.commit()
        invoice_id = await process(api_client, app, [line()], "p3.pdf", "update path",
                                   subtotal=18.75, grand_total=18.75)
        await confirm(api_client, invoice_id, 24)

        mapping = await ProductCaseMappingRepository(db_session).get(store_id(STORE), NORMALIZED)
        await db_session.refresh(mapping)
        assert mapping.units_per_case == 4               # unchanged
        [p] = await ProductDataProposalRepository(db_session).list(
            entity_key=NORMALIZED, status=STATUS_PENDING)
        assert p.proposed_value == 24
        assert p.current_value == 4                      # the delta is recorded

    async def test_re_clicking_confirm_does_not_duplicate(self, api_client, app, db_session):  # noqa: F811
        invoice_id = await process(api_client, app, [line()], "p4.pdf", "dedupe",
                                   subtotal=18.75, grand_total=18.75)
        await confirm(api_client, invoice_id, 4)
        await confirm(api_client, invoice_id, 4)
        rows = await ProductDataProposalRepository(db_session).list(entity_key=NORMALIZED)
        assert len(rows) == 1

    async def test_a_different_value_is_a_second_proposal(self, api_client, app, db_session):  # noqa: F811
        invoice_id = await process(api_client, app, [line()], "p5.pdf", "two values",
                                   subtotal=18.75, grand_total=18.75)
        await confirm(api_client, invoice_id, 4)
        await confirm(api_client, invoice_id, 24)
        rows = await ProductDataProposalRepository(db_session).list(entity_key=NORMALIZED)
        assert sorted(r.proposed_value for r in rows) == [4, 24]


class TestSourceIsDecidedByTheSystem:
    async def test_a_typed_number_with_no_suggestion_is_operator_entered(
        self, api_client, app, db_session  # noqa: F811
    ):
        invoice_id = await process(api_client, app, [line("BEATBOX MALT SOUR CH", "850059195932")],
                                   "src1.pdf", "operator", subtotal=18.75, grand_total=18.75)
        await confirm(api_client, invoice_id, 12, code="85005919593")
        [p] = await ProductDataProposalRepository(db_session).list(entity_key="85005919593")
        assert p.source == SOURCE_OPERATOR_ENTERED

    async def test_choosing_between_ambiguous_readings_is_recorded_as_such(
        self, api_client, app, db_session  # noqa: F811
    ):
        # BUSCH 4/6/16OZ with no reference cost: the document says 4 or 24.
        invoice_id = await process(api_client, app, [line()], "src2.pdf", "ambiguous",
                                   subtotal=18.75, grand_total=18.75)
        await confirm(api_client, invoice_id, 24)
        [p] = await ProductDataProposalRepository(db_session).list(entity_key=NORMALIZED)
        assert p.source == SOURCE_DOCUMENT_AMBIGUOUS
        assert p.evidence["suggestion_candidates"] == [4, 24]


class TestReview:
    async def _pending(self, db_session, value=4, key=NORMALIZED):
        return await ProductDataProposalRepository(db_session).create(
            store_id=store_id(STORE), entity_type="case_mapping", entity_key=key,
            field="units_per_case", proposed_value=value, current_value=None,
            source=SOURCE_REFERENCE_DERIVED, proposed_by="test",
            evidence={"invoice_description": "BUSCH 4/6/160Z CAN"},
        )

    async def test_approval_writes_the_mapping_and_links_it(self, db_session):
        p = await self._pending(db_session)
        result = await proposal_service.approve(db_session, p, reviewed_by="reviewer:test")
        await db_session.commit()

        mapping = await ProductCaseMappingRepository(db_session).get(store_id(STORE), NORMALIZED)
        assert mapping is not None
        assert mapping.units_per_case == 4
        assert mapping.source == "APPROVED"
        assert mapping.approved_proposal_id == p.id
        assert p.status == STATUS_APPROVED
        assert p.reviewed_by == "reviewer:test"
        assert p.reviewed_at is not None
        assert result.applied_to == f"product_case_mappings:{store_id(STORE)}:{NORMALIZED}"

    async def test_rejection_leaves_master_data_untouched(self, db_session):
        p = await self._pending(db_session)
        await proposal_service.reject(db_session, p, reviewed_by="reviewer:test", note="wrong pack")
        await db_session.commit()

        assert await ProductCaseMappingRepository(db_session).get(store_id(STORE), NORMALIZED) is None
        assert p.status == STATUS_REJECTED
        assert p.review_note == "wrong pack"
        assert p.reviewed_by == "reviewer:test"

    async def test_a_reviewed_proposal_is_immutable(self, db_session):
        p = await self._pending(db_session)
        await proposal_service.reject(db_session, p, reviewed_by="r")
        with pytest.raises(ProposalImmutableError):
            await proposal_service.approve(db_session, p, reviewed_by="r")
        with pytest.raises(ProposalImmutableError):
            await proposal_service.reject(db_session, p, reviewed_by="r")
        assert p.status == STATUS_REJECTED                # unchanged

    async def test_a_later_change_needs_a_new_proposal(self, db_session):
        first = await self._pending(db_session, 4)
        await proposal_service.approve(db_session, first, reviewed_by="r")
        second = await self._pending(db_session, 24)
        await proposal_service.approve(db_session, second, reviewed_by="r")
        await db_session.commit()

        mapping = await ProductCaseMappingRepository(db_session).get(store_id(STORE), NORMALIZED)
        assert mapping.units_per_case == 24
        assert mapping.approved_proposal_id == second.id    # points at the newest decision
        assert first.status == STATUS_APPROVED               # history intact
        assert second.current_value is None or second.current_value == 4

    async def test_batch_approval_lands_together(self, db_session):
        codes = ["01820000063", "06206738062", "01820025004"]
        pending = [await self._pending(db_session, 4, key=c) for c in codes]
        for p in pending:
            await proposal_service.approve(db_session, p, reviewed_by="r")
        await db_session.commit()
        repo = ProductCaseMappingRepository(db_session)
        for c in codes:
            assert (await repo.get(store_id(STORE), c)).approved_proposal_id is not None

    async def test_approval_then_export_opens(self, api_client, app, db_session):  # noqa: F811
        invoice_id = await process(api_client, app, [line()], "e2e.pdf", "end to end",
                                   subtotal=18.75, grand_total=18.75)
        await confirm(api_client, invoice_id, 4)
        [p] = await ProductDataProposalRepository(db_session).list(
            entity_key=NORMALIZED, status=STATUS_PENDING)
        await proposal_service.approve(db_session, p, reviewed_by="reviewer:test")
        await db_session.commit()

        detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        assert detail["pdi_export_allowed"] is True
        [row] = detail["case_mappings"]
        assert row["mapped"] is True and row["units_per_case"] == 4
        assert row["pending_proposal_id"] is None         # queue cleared

        export = await api_client.get(f"/api/v1/invoices/{invoice_id}/export",
                                      params={"format": "pdi"})
        assert export.status_code == 200
        assert export.text.splitlines()[1][53:57] == "0004"

    async def test_a_rejected_proposal_never_reaches_the_edi(self, api_client, app, db_session):  # noqa: F811
        invoice_id = await process(api_client, app, [line()], "rej.pdf", "rejected",
                                   subtotal=18.75, grand_total=18.75)
        await confirm(api_client, invoice_id, 24)
        [p] = await ProductDataProposalRepository(db_session).list(entity_key=NORMALIZED)
        await proposal_service.reject(db_session, p, reviewed_by="r")
        await db_session.commit()

        export = await api_client.get(f"/api/v1/invoices/{invoice_id}/export",
                                      params={"format": "pdi"})
        assert export.status_code == 422


class TestLegacyMappingsRemainValid:
    async def test_a_grandfathered_mapping_is_authoritative_and_labelled(
        self, api_client, app, db_session  # noqa: F811
    ):
        # Reproduce what migration 0008 did for the 17 pre-existing rows.
        repo = ProductDataProposalRepository(db_session)
        legacy = await repo.create(
            store_id=store_id(STORE), entity_type="case_mapping", entity_key=NORMALIZED,
            field="units_per_case", proposed_value=4, current_value=None,
            source=SOURCE_LEGACY_MIGRATED, proposed_by="system:legacy-migration",
            reason="predates the workflow",
        )
        await repo.mark_approved(legacy, reviewed_by="system:legacy-migration",
                                 note="Grandfathered; never reviewed by a person.")
        mapping = await ProductCaseMappingRepository(db_session).upsert(
            store_id=store_id(STORE), item_code=NORMALIZED, units_per_case=4, source="VERIFIED_FROM_INVOICE")
        mapping.approved_proposal_id = legacy.id
        await db_session.commit()

        invoice_id = await process(api_client, app, [line()], "leg.pdf", "legacy",
                                   subtotal=18.75, grand_total=18.75)
        detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        assert detail["pdi_export_allowed"] is True       # still counts
        assert mapping.source == "VERIFIED_FROM_INVOICE"  # history not rewritten
        assert legacy.reviewed_by == "system:legacy-migration"   # not a person
