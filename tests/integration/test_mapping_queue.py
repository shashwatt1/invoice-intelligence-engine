"""
tests/integration/test_mapping_queue.py — the collaborative "Requires
Mapping" work queue (app/services/mapping_queue_service.py,
app/api/v1/mapping_queue.py), against real Postgres and real HTTP.

The core behavioral change this phase adds: an unresolved case mapping
is visible to MANAGER/ADMIN the instant a processed invoice carries it —
never dependent on who uploaded the invoice, whether they are still
logged in, or whether anyone has even submitted a proposal yet. This
file proves that, proves approval propagates everywhere (the queue,
every affected invoice, future invoices) without bypassing proposal
governance or the independent validation/EDI gate, and proves the
access boundary (MANAGER/ADMIN only).

Items 7, 8, 9, 12, 23, 26, 27 of the phase's test checklist are already
covered by tests/integration/test_data_team_user_permissions.py and
tests/integration/test_auth.py and are reconfirmed by the full suite
re-run rather than duplicated here; several of them are also touched
incidentally by the flows below (every test opens invoice detail as
USER/MANAGER/ADMIN at some point).
"""

from __future__ import annotations

import uuid

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.models.product_data_proposal import ProductDataProposal
from app.repositories.product_case_mapping_repository import ProductCaseMappingRepository
from app.schemas.extraction import ExtractedLineItem
from app.services.pipeline_service import InvoiceProcessingPipeline
from tests.integration.conftest import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    MANAGER_PASSWORD,
    MANAGER_USERNAME,
    USER_PASSWORD,
    USER_USERNAME,
    requires_db,
    store_id,
)
from tests.integration.fakes import FakeStructuring, extracted_invoice
from tests.integration.test_api_db import api_client, process_file  # noqa: F401 — fixture reuse
from tests.integration.test_data_team_user_permissions import (
    _client_as,  # noqa: F401 — fixture reuse
)
from tests.pdf_builder import build_pdf

pytestmark = requires_db


async def _process(
    app, api_client, *, username: str, password: str, store_code: str, item_codes: list[str],  # noqa: F811
    invoice_number: str = "INV-001", unit_price: float = 9.45, quantity: float = 2.0,
):
    """
    A fully-processed invoice, authenticated as the given user, with one
    product line per item code (already-11-digit codes, so
    normalize_item_code leaves them unchanged — no check-digit surprises
    in test assertions). `invoice_number=""` produces a REVIEW_REQUIRED
    invoice (INVOICE_NUMBER_PRESENT fails), matching the real RCM
    acceptance case; the default produces a clean VALIDATED invoice.
    """
    from app.api.v1.invoices import get_pipeline

    line_items = [
        ExtractedLineItem(
            description=f"Product {code}", product_code=code,
            quantity=quantity, unit_price=unit_price, line_total=round(quantity * unit_price, 2),
        )
        for code in item_codes
    ]
    subtotal = round(sum(float(item.line_total) for item in line_items), 2)
    invoice = extracted_invoice(
        invoice_number=invoice_number, line_items=line_items, subtotal=subtotal, grand_total=subtotal,
    )
    app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
        structuring_service=FakeStructuring(invoice)
    )
    client = await _client_as(app, username, password)
    try:
        accepted = await process_file(
            client, content=build_pdf([f"invoice {uuid.uuid4()} " + "pad " * 300]),
            filename=f"{uuid.uuid4()}.pdf", store=store_code,
        )
        status = (await client.get(accepted["status_url"])).json()["data"]
        return client, status["invoice_id"], status["document_id"]
    except BaseException:
        await client.aclose()
        raise


async def _approve(
    db_session, manager_client, invoice_id: str, item_code: str, units_per_case: int,
    *, reviewed_by: str = MANAGER_USERNAME,
) -> None:
    submit = await manager_client.post(
        f"/api/v1/invoices/{invoice_id}/case-mappings",
        json={"mappings": [{"item_code": item_code, "units_per_case": units_per_case}]},
    )
    assert submit.status_code == 200, submit.text
    proposal = (await db_session.execute(
        select(ProductDataProposal).where(
            ProductDataProposal.entity_key == item_code, ProductDataProposal.status == "PENDING",
        )
    )).scalars().first()
    assert proposal is not None
    approve = await manager_client.post(
        f"/api/v1/proposals/{proposal.id}/approve", json={"reviewed_by": reviewed_by},
    )
    assert approve.status_code == 200, approve.text


class TestQueueSurfacesUnresolvedMappings:
    """1/2/3: an unresolved mapping is visible with no proposal, regardless of uploader."""

    async def test_unresolved_mapping_appears_without_any_proposal(self, app, api_client, db_session):  # noqa: F811
        code = "70000000011"
        user_client, invoice_id, _ = await _process(
            app, api_client, username=USER_USERNAME, password=USER_PASSWORD,
            store_code="47708760", item_codes=[code],
        )
        await user_client.aclose()

        assert (await db_session.execute(select(ProductDataProposal))).scalars().all() == []

        manager_client = await _client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
        try:
            response = await manager_client.get("/api/v1/mapping-queue", params={"page_size": 200})
            assert response.status_code == 200
            items = response.json()["items"]
            row = next((r for r in items if r["item_code"] == code), None)
            assert row is not None, f"expected {code!r} in the queue, got {[r['item_code'] for r in items]}"
            assert row["pending_proposal_id"] is None
            assert row["pending_value"] is None
            assert row["invoice_count"] == 1
            assert row["occurrences"][0]["invoice_id"] == invoice_id
        finally:
            await manager_client.aclose()


class TestQueueAccessControl:
    """4/5/6/25: MANAGER and ADMIN can query the queue; USER and an unauthenticated caller cannot."""

    async def test_manager_can_query_the_global_queue(self, app, api_client, db_session):  # noqa: F811
        client, _, _ = await _process(
            app, api_client, username=USER_USERNAME, password=USER_PASSWORD,
            store_code="47708760", item_codes=["70000000021"],
        )
        await client.aclose()
        manager_client = await _client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
        try:
            response = await manager_client.get("/api/v1/mapping-queue")
            assert response.status_code == 200
        finally:
            await manager_client.aclose()

    async def test_admin_can_query_the_global_queue(self, app, api_client, db_session):  # noqa: F811
        client, _, _ = await _process(
            app, api_client, username=USER_USERNAME, password=USER_PASSWORD,
            store_code="47708760", item_codes=["70000000022"],
        )
        await client.aclose()
        admin_client = await _client_as(app, ADMIN_USERNAME, ADMIN_PASSWORD)
        try:
            response = await admin_client.get("/api/v1/mapping-queue/summary")
            assert response.status_code == 200
            assert response.json()["data"]["unique_products"] >= 1
        finally:
            await admin_client.aclose()

    async def test_user_cannot_query_the_global_queue(self, app, api_client, db_session):  # noqa: F811
        client = await _client_as(app, USER_USERNAME, USER_PASSWORD)
        try:
            response = await client.get("/api/v1/mapping-queue")
            assert response.status_code == 403
            summary = await client.get("/api/v1/mapping-queue/summary")
            assert summary.status_code == 403
        finally:
            await client.aclose()

    async def test_unauthenticated_caller_is_rejected(self, app, api_client, db_session):  # noqa: F811
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        try:
            response = await client.get("/api/v1/mapping-queue")
            assert response.status_code == 401
        finally:
            await client.aclose()


class TestProposeFromQueue:
    """10/11: MANAGER and ADMIN can submit a proposal for a queue item — through the SAME
    endpoint invoice detail uses; governance is not bypassed."""

    async def test_manager_can_submit_a_proposal_for_a_queue_item(self, app, api_client, db_session):  # noqa: F811
        code = "70000000031"
        user_client, invoice_id, _ = await _process(
            app, api_client, username=USER_USERNAME, password=USER_PASSWORD,
            store_code="47708760", item_codes=[code],
        )
        await user_client.aclose()

        manager_client = await _client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
        try:
            submit = await manager_client.post(
                f"/api/v1/invoices/{invoice_id}/case-mappings",
                json={"mappings": [{"item_code": code, "units_per_case": 6}]},
            )
            assert submit.status_code == 200, submit.text
        finally:
            await manager_client.aclose()

        proposal = (await db_session.execute(
            select(ProductDataProposal).where(ProductDataProposal.entity_key == code)
        )).scalars().one()
        assert proposal.status == "PENDING"
        assert proposal.proposed_by == MANAGER_USERNAME

    async def test_admin_can_submit_a_proposal_for_a_queue_item(self, app, api_client, db_session):  # noqa: F811
        code = "70000000032"
        user_client, invoice_id, _ = await _process(
            app, api_client, username=USER_USERNAME, password=USER_PASSWORD,
            store_code="47708760", item_codes=[code],
        )
        await user_client.aclose()

        admin_client = await _client_as(app, ADMIN_USERNAME, ADMIN_PASSWORD)
        try:
            submit = await admin_client.post(
                f"/api/v1/invoices/{invoice_id}/case-mappings",
                json={"mappings": [{"item_code": code, "units_per_case": 4}]},
            )
            assert submit.status_code == 200, submit.text
        finally:
            await admin_client.aclose()

        proposal = (await db_session.execute(
            select(ProductDataProposal).where(ProductDataProposal.entity_key == code)
        )).scalars().one()
        assert proposal.proposed_by == ADMIN_USERNAME


class TestWorkbenchSubmissionGovernance:
    """
    CORRECTION phase 8/9/10/11/19: the mapping workbench (web/src/components/
    requires-mapping/mapping-workbench.tsx) submits through the exact same
    POST /invoices/{id}/case-mappings call these tests already exercise —
    there is no separate backend surface for it. These prove the specific
    governance properties the workbench's UI depends on: a submission is a
    PENDING proposal only (never an authoritative mapping), it is visible
    on Master Data Review immediately, the queue reports who proposed it,
    and re-submitting the same value is a safe no-op rather than a pile-up.
    """

    async def test_submit_creates_a_pending_proposal_and_never_an_authoritative_mapping(
        self, app, api_client, db_session  # noqa: F811
    ):
        code = "70000000091"
        user_client, invoice_id, _ = await _process(
            app, api_client, username=USER_USERNAME, password=USER_PASSWORD,
            store_code="47708760", item_codes=[code],
        )
        await user_client.aclose()

        manager_client = await _client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
        try:
            submit = await manager_client.post(
                f"/api/v1/invoices/{invoice_id}/case-mappings",
                json={"mappings": [{"item_code": code, "units_per_case": 9}]},
            )
            assert submit.status_code == 200, submit.text
        finally:
            await manager_client.aclose()

        proposal = (await db_session.execute(
            select(ProductDataProposal).where(ProductDataProposal.entity_key == code)
        )).scalars().one()
        assert proposal.status == "PENDING"                                          # 9: pending, not authoritative
        mapping = await ProductCaseMappingRepository(db_session).get(store_id("47708760"), code)
        assert mapping is None                                                        # 9: no direct write

    async def test_pending_proposal_from_the_workbench_is_visible_on_master_data_review(
        self, app, api_client, db_session  # noqa: F811
    ):
        code = "70000000092"
        user_client, invoice_id, _ = await _process(
            app, api_client, username=USER_USERNAME, password=USER_PASSWORD,
            store_code="47708760", item_codes=[code],
        )
        await user_client.aclose()

        manager_client = await _client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
        try:
            await manager_client.post(
                f"/api/v1/invoices/{invoice_id}/case-mappings",
                json={"mappings": [{"item_code": code, "units_per_case": 7}]},
            )
            proposals = await manager_client.get("/api/v1/proposals", params={"status": "PENDING"})
            assert proposals.status_code == 200
            assert any(p["entity_key"] == code and p["proposed_value"] == 7 for p in proposals.json()["items"])  # 11

            queue = await manager_client.get("/api/v1/mapping-queue", params={"page_size": 200})
            row = next(r for r in queue.json()["items"] if r["item_code"] == code)
            assert row["pending_value"] == 7                                          # 10
            assert row["pending_proposed_by"] == MANAGER_USERNAME
        finally:
            await manager_client.aclose()

    async def test_resubmitting_the_identical_value_does_not_pile_up_a_second_pending_row(
        self, app, api_client, db_session  # noqa: F811
    ):
        code = "70000000093"
        user_client, invoice_id, _ = await _process(
            app, api_client, username=USER_USERNAME, password=USER_PASSWORD,
            store_code="47708760", item_codes=[code],
        )
        await user_client.aclose()

        manager_client = await _client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
        try:
            for _ in range(2):
                submit = await manager_client.post(
                    f"/api/v1/invoices/{invoice_id}/case-mappings",
                    json={"mappings": [{"item_code": code, "units_per_case": 5}]},
                )
                assert submit.status_code == 200

            rows = (await db_session.execute(
                select(ProductDataProposal).where(ProductDataProposal.entity_key == code)
            )).scalars().all()
            assert len(rows) == 1                                                     # 19: no duplicate

            # A genuinely different value is a new, second PENDING proposal —
            # both visible to the reviewer, not silently overwritten.
            submit_different = await manager_client.post(
                f"/api/v1/invoices/{invoice_id}/case-mappings",
                json={"mappings": [{"item_code": code, "units_per_case": 6}]},
            )
            assert submit_different.status_code == 200
            rows_after = (await db_session.execute(
                select(ProductDataProposal).where(ProductDataProposal.entity_key == code)
            )).scalars().all()
            assert len(rows_after) == 2
            assert {r.proposed_value for r in rows_after} == {5, 6}
        finally:
            await manager_client.aclose()


class TestApprovalPropagatesEverywhere:
    """13/15/16 and the PART 12 acceptance scenario: a manager who never uploaded the
    invoice approves it; the queue, the invoice, and the ORIGINAL uploader's later
    session all reflect it — with no action from the uploader in between."""

    async def test_manager_approval_clears_the_queue_item_and_updates_the_invoice_for_everyone(
        self, app, api_client, db_session  # noqa: F811
    ):
        code = "70000000041"
        user_client, invoice_id, _ = await _process(
            app, api_client, username=USER_USERNAME, password=USER_PASSWORD,
            store_code="47708760", item_codes=[code],
        )
        try:
            before = await user_client.get(f"/api/v1/invoices/{invoice_id}")
            row = next(r for r in before.json()["data"]["case_mappings"] if r["item_code"] == code)
            assert row["mapped"] is False
        finally:
            await user_client.aclose()          # Vivek logs out — submits nothing

        manager_client = await _client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)  # Barj logs in
        try:
            queue_before = await manager_client.get("/api/v1/mapping-queue", params={"page_size": 200})
            assert any(r["item_code"] == code for r in queue_before.json()["items"])

            await _approve(db_session, manager_client, invoice_id, code, 12)

            queue_after = await manager_client.get("/api/v1/mapping-queue", params={"page_size": 200})
            assert not any(r["item_code"] == code for r in queue_after.json()["items"])          # 15

            detail_after = await manager_client.get(f"/api/v1/invoices/{invoice_id}")
            row_after = next(r for r in detail_after.json()["data"]["case_mappings"] if r["item_code"] == code)
            assert row_after["mapped"] is True and row_after["units_per_case"] == 12             # 16
        finally:
            await manager_client.aclose()

        # Vivek logs back in later — sees the update with zero action of their own.
        user_client_2 = await _client_as(app, USER_USERNAME, USER_PASSWORD)
        try:
            after_as_user = await user_client_2.get(f"/api/v1/invoices/{invoice_id}")
            row_user = next(r for r in after_as_user.json()["data"]["case_mappings"] if r["item_code"] == code)
            assert row_user["mapped"] is True and row_user["units_per_case"] == 12
        finally:
            await user_client_2.aclose()


class TestSharedMappingAndStoreScope:
    """17/19/23/24: one master-data problem across invoices, never duplicated;
    store scope is never crossed."""

    async def test_approving_on_one_invoice_resolves_the_same_upc_on_another_same_store_invoice(
        self, app, api_client, db_session  # noqa: F811
    ):
        code = "70000000051"
        client_a, invoice_a, _ = await _process(
            app, api_client, username=USER_USERNAME, password=USER_PASSWORD,
            store_code="47708760", item_codes=[code],
        )
        await client_a.aclose()
        client_b, invoice_b, _ = await _process(
            app, api_client, username=USER_USERNAME, password=USER_PASSWORD,
            store_code="47708760", item_codes=[code],
        )
        await client_b.aclose()

        manager_client = await _client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
        try:
            queue = await manager_client.get("/api/v1/mapping-queue", params={"page_size": 200})
            row = next(r for r in queue.json()["items"] if r["item_code"] == code)
            assert row["invoice_count"] == 2                                          # 23: one item, not two

            await _approve(db_session, manager_client, invoice_a, code, 8)

            detail_b = await manager_client.get(f"/api/v1/invoices/{invoice_b}")      # 17: B never touched directly
            row_b = next(r for r in detail_b.json()["data"]["case_mappings"] if r["item_code"] == code)
            assert row_b["mapped"] is True and row_b["units_per_case"] == 8

            queue_after = await manager_client.get("/api/v1/mapping-queue", params={"page_size": 200})
            assert not any(r["item_code"] == code for r in queue_after.json()["items"])
        finally:
            await manager_client.aclose()

        # 19: a brand-new invoice with the same UPC/store reuses the mapping and is
        # never asked about it again.
        client_c, invoice_c, _ = await _process(
            app, api_client, username=USER_USERNAME, password=USER_PASSWORD,
            store_code="47708760", item_codes=[code],
        )
        try:
            detail_c = await client_c.get(f"/api/v1/invoices/{invoice_c}")
            row_c = next(r for r in detail_c.json()["data"]["case_mappings"] if r["item_code"] == code)
            assert row_c["mapped"] is True and row_c["units_per_case"] == 8
        finally:
            await client_c.aclose()

    async def test_approving_at_one_store_does_not_resolve_the_same_upc_at_another_store(
        self, app, api_client, db_session  # noqa: F811
    ):
        code = "70000000052"
        client_a, invoice_a, _ = await _process(
            app, api_client, username=USER_USERNAME, password=USER_PASSWORD,
            store_code="47708760", item_codes=[code],
        )
        await client_a.aclose()
        client_b, invoice_b, _ = await _process(
            app, api_client, username=USER_USERNAME, password=USER_PASSWORD,
            store_code="86357232", item_codes=[code],
        )
        await client_b.aclose()

        manager_client = await _client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
        try:
            await _approve(db_session, manager_client, invoice_a, code, 5)

            detail_b = await manager_client.get(f"/api/v1/invoices/{invoice_b}")
            row_b = next(r for r in detail_b.json()["data"]["case_mappings"] if r["item_code"] == code)
            assert row_b["mapped"] is False                                            # 24: untouched

            queue_after = await manager_client.get("/api/v1/mapping-queue", params={"page_size": 200})
            store_b_row = next(
                r for r in queue_after.json()["items"]
                if r["item_code"] == code and r["store"]["id"] == str(store_id("86357232"))
            )
            assert store_b_row["invoice_count"] == 1
        finally:
            await manager_client.aclose()


class TestMappingSurvivesInvoiceDeletion:
    """18: the master mapping outlives the invoice that first raised it."""

    async def test_deleting_the_invoice_does_not_delete_the_master_mapping(
        self, app, api_client, db_session  # noqa: F811
    ):
        code = "70000000061"
        user_client, invoice_id, _ = await _process(
            app, api_client, username=USER_USERNAME, password=USER_PASSWORD,
            store_code="47708760", item_codes=[code],
        )
        await user_client.aclose()

        manager_client = await _client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
        try:
            await _approve(db_session, manager_client, invoice_id, code, 3)
        finally:
            await manager_client.aclose()

        admin_client = await _client_as(app, ADMIN_USERNAME, ADMIN_PASSWORD)
        try:
            delete = await admin_client.delete(f"/api/v1/invoices/{invoice_id}")
            assert delete.status_code == 200
        finally:
            await admin_client.aclose()

        mapping = await ProductCaseMappingRepository(db_session).get(store_id("47708760"), code)
        assert mapping is not None
        assert mapping.units_per_case == 3


class TestEdiReadinessRespectsAllGates:
    """20/21/22: mapping approval clears ONLY the mapping blocker — an independent
    validation failure still leaves the invoice flagged; EDI is only fully ready
    once every gate clears. Mirrors the real RCM acceptance case (blank invoice
    number -> REVIEW_REQUIRED) without changing pdi_export_eligibility()."""

    async def test_mapping_approval_does_not_clear_an_independent_validation_failure(
        self, app, api_client, db_session  # noqa: F811
    ):
        code = "70000000071"
        user_client, invoice_id, _ = await _process(
            app, api_client, username=USER_USERNAME, password=USER_PASSWORD,
            store_code="47708760", item_codes=[code], invoice_number="",  # -> INVOICE_NUMBER_PRESENT fails
        )
        try:
            before = await user_client.get(f"/api/v1/invoices/{invoice_id}")
            data_before = before.json()["data"]
            assert data_before["status"] == "REVIEW_REQUIRED"
            assert data_before["pdi_export_allowed"] is False        # blocked on mapping first
        finally:
            await user_client.aclose()

        manager_client = await _client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
        try:
            await _approve(db_session, manager_client, invoice_id, code, 3)

            after = await manager_client.get(f"/api/v1/invoices/{invoice_id}")
            data_after = after.json()["data"]
            assert data_after["status"] == "REVIEW_REQUIRED"          # untouched by mapping approval
            assert data_after["pdi_export_allowed"] is True           # the mapping blocker specifically cleared
            assert data_after["pdi_export_requires_confirmation"] is True  # not silently fully ready
        finally:
            await manager_client.aclose()

    async def test_edi_is_fully_ready_only_once_mapping_and_validation_both_clear(
        self, app, api_client, db_session  # noqa: F811
    ):
        code = "70000000072"
        user_client, invoice_id, _ = await _process(
            app, api_client, username=USER_USERNAME, password=USER_PASSWORD,
            store_code="47708760", item_codes=[code],   # clean invoice -> VALIDATED
        )
        try:
            before = await user_client.get(f"/api/v1/invoices/{invoice_id}")
            assert before.json()["data"]["status"] == "VALIDATED"
            assert before.json()["data"]["pdi_export_allowed"] is False
        finally:
            await user_client.aclose()

        manager_client = await _client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
        try:
            await _approve(db_session, manager_client, invoice_id, code, 2)

            after = await manager_client.get(f"/api/v1/invoices/{invoice_id}")
            data = after.json()["data"]
            assert data["pdi_export_allowed"] is True
            assert data["pdi_export_requires_confirmation"] is False
        finally:
            await manager_client.aclose()


class TestSummaryCountsArePrecise:
    """10: 'products' and 'invoice occurrences' are never conflated."""

    async def test_summary_distinguishes_unique_products_from_invoice_occurrences(
        self, app, api_client, db_session  # noqa: F811
    ):
        code = "70000000081"
        client_a, _, _ = await _process(
            app, api_client, username=USER_USERNAME, password=USER_PASSWORD,
            store_code="47708760", item_codes=[code],
        )
        await client_a.aclose()
        client_b, _, _ = await _process(
            app, api_client, username=USER_USERNAME, password=USER_PASSWORD,
            store_code="47708760", item_codes=[code],
        )
        await client_b.aclose()

        manager_client = await _client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
        try:
            summary = (await manager_client.get("/api/v1/mapping-queue/summary")).json()["data"]
            assert summary["unique_products"] == 1
            assert summary["invoice_occurrences"] == 2
            assert summary["stores"] == 1
        finally:
            await manager_client.aclose()
