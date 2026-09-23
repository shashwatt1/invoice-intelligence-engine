"""
tests/integration/test_invoice_detail_role_visibility.py — CORRECTION:
the blank MANAGER/USER invoice-detail page.

Root cause (frontend): `InvoiceDetailData.database` is null for
MANAGER/USER (see app.api.v1.invoices._redact_invoice_detail below) but
web/src/api/types.ts declared it non-nullable, so
DatabaseConfirmationCard destructured `null.vendor_saved` with no error
boundary anywhere in the app — an uncaught render exception unmounts the
whole React tree, which reads as a blank page. That half of the fix is
purely frontend (web/src/components/invoice/database-confirmation.tsx,
web/src/api/types.ts, and splitting IntelligencePanel into an ADMIN-only
panel plus a role-safe BusinessStatusPanel) and is covered by
web/src/pages/invoice-detail.test.tsx.

This file pins the backend half of the contract those frontend changes
depend on: exactly which fields GET /invoices/{id} returns per role,
using a real multi-line-item invoice shaped like the RCM acceptance
case (multiple UPC-bearing product lines, no invoice number, three
failed validation checks) — not an empty/mock invoice — built through
the real pipeline, never mutating the actual RCM invoice.
"""

from __future__ import annotations

import uuid

from httpx import ASGITransport, AsyncClient

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
)
from tests.integration.fakes import FakeStructuring, extracted_invoice
from tests.integration.test_api_db import api_client, process_file  # noqa: F401 — fixture reuse
from tests.integration.test_data_team_user_permissions import (
    _client_as,  # noqa: F401 — fixture reuse
)
from tests.pdf_builder import build_pdf

pytestmark = requires_db

RCM_LIKE_ITEMS = [
    ("GM VAN MINI CRE", "00025328", 12, 1.19),
    ("GN VANILLA CREM", "00025294", 12, 1.19),
    ("LB REG", "00049171", 8, 1.95),
    ("MT DR SPN", "00028301", 5, 4.04),
]


async def _rcm_like_invoice(app, api_client, *, username: str, password: str):  # noqa: F811
    """
    A REVIEW_REQUIRED invoice shaped like the real RCM acceptance case:
    multiple UPC-bearing product lines, no invoice number (so
    INVOICE_NUMBER_PRESENT fails, matching the real invoice's three
    failed checks), unmapped. Never touches the actual RCM invoice.
    """
    from app.api.v1.invoices import get_pipeline

    line_items = [
        ExtractedLineItem(description=desc, product_code=code, quantity=qty, unit_price=price, line_total=round(qty * price, 2))
        for desc, code, qty, price in RCM_LIKE_ITEMS
    ]
    subtotal = round(sum(float(i.line_total) for i in line_items), 2)
    invoice = extracted_invoice(
        invoice_number="", line_items=line_items, subtotal=subtotal, grand_total=subtotal,
        tax_amount=0.0, discount_amount=0.0,
    )
    app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
        structuring_service=FakeStructuring(invoice)
    )
    client = await _client_as(app, username, password)
    try:
        accepted = await process_file(
            client, content=build_pdf([f"invoice {uuid.uuid4()} " + "pad " * 300]),
            filename=f"{uuid.uuid4()}.pdf",
        )
        status = (await client.get(accepted["status_url"])).json()["data"]
        return client, status["invoice_id"]
    except BaseException:
        await client.aclose()
        raise


class TestEveryAuthorizedRoleCanOpenTheInvoice:
    """1/2/3/11: ADMIN, MANAGER and an authorized USER all get 200; unauthenticated is 401."""

    async def test_admin_can_retrieve_full_invoice_detail(self, app, api_client, db_session):  # noqa: F811
        client, invoice_id = await _rcm_like_invoice(app, api_client, username=USER_USERNAME, password=USER_PASSWORD)
        await client.aclose()
        admin_client = await _client_as(app, ADMIN_USERNAME, ADMIN_PASSWORD)
        try:
            response = await admin_client.get(f"/api/v1/invoices/{invoice_id}")
            assert response.status_code == 200, response.text
        finally:
            await admin_client.aclose()

    async def test_manager_can_retrieve_invoice_detail(self, app, api_client, db_session):  # noqa: F811
        client, invoice_id = await _rcm_like_invoice(app, api_client, username=USER_USERNAME, password=USER_PASSWORD)
        await client.aclose()
        manager_client = await _client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
        try:
            response = await manager_client.get(f"/api/v1/invoices/{invoice_id}")
            assert response.status_code == 200, response.text
        finally:
            await manager_client.aclose()

    async def test_user_can_retrieve_its_own_invoice_detail(self, app, api_client, db_session):  # noqa: F811
        client, invoice_id = await _rcm_like_invoice(app, api_client, username=USER_USERNAME, password=USER_PASSWORD)
        try:
            response = await client.get(f"/api/v1/invoices/{invoice_id}")
            assert response.status_code == 200, response.text
        finally:
            await client.aclose()

    async def test_unauthenticated_request_is_401(self, app, api_client, db_session):  # noqa: F811
        client, invoice_id = await _rcm_like_invoice(app, api_client, username=USER_USERNAME, password=USER_PASSWORD)
        await client.aclose()
        anon = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        try:
            response = await anon.get(f"/api/v1/invoices/{invoice_id}")
            assert response.status_code == 401
        finally:
            await anon.aclose()


class TestManagerResponseShape:
    """4/6: MANAGER gets every business field this correction requires, and
    no admin/developer diagnostic — the exact contract BusinessStatusPanel
    and the gated IntelligencePanel/DeveloperPanel/DatabaseConfirmationCard
    depend on."""

    async def test_manager_response_contains_the_business_fields_this_page_needs(
        self, app, api_client, db_session  # noqa: F811
    ):
        client, invoice_id = await _rcm_like_invoice(app, api_client, username=USER_USERNAME, password=USER_PASSWORD)
        await client.aclose()
        manager_client = await _client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
        try:
            data = (await manager_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        finally:
            await manager_client.aclose()

        # Header / business identity — vendor included: previously redacted
        # to None for USER, and this correction explicitly requires it for
        # both MANAGER and USER (it was already present for MANAGER).
        assert data["status"] == "REVIEW_REQUIRED"
        assert data["document_status"] is not None
        assert data["grand_total"] is not None
        assert data["vendor"] is not None
        assert data["store"]["label"]

        # Printed totals — explicitly required by this correction, not just grand_total.
        assert data["subtotal"] is not None
        assert data["tax_amount"] is not None
        assert data["discount_amount"] is not None

        # Line items — the critical functional requirement.
        assert len(data["line_items"]) == 4
        codes = {item["product_code"] for item in data["line_items"]}
        assert codes == {"00025328", "00025294", "00049171", "00028301"}
        first = next(i for i in data["line_items"] if i["product_code"] == "00025328")
        assert first["description"] == "GM VAN MINI CRE"
        assert first["quantity"] == 12
        assert first["unit_price"] == 1.19
        assert first["line_total"] == 14.28

        # Mapping status — none approved yet, matching the real RCM state.
        assert len(data["case_mappings"]) == 4
        assert all(row["mapped"] is False for row in data["case_mappings"])

        # EDI readiness.
        assert data["pdi_export_allowed"] is False
        assert "mapping" in data["pdi_export_blocked_reason"].lower()

    async def test_manager_response_never_contains_admin_developer_diagnostics(
        self, app, api_client, db_session  # noqa: F811
    ):
        client, invoice_id = await _rcm_like_invoice(app, api_client, username=USER_USERNAME, password=USER_PASSWORD)
        await client.aclose()
        manager_client = await _client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
        try:
            data = (await manager_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        finally:
            await manager_client.aclose()

        assert data["ocr_text"] is None
        assert data["raw_extraction"] is None
        assert data["llm_metadata"] is None
        assert data["validation_report"] is None
        assert data["database"] is None
        assert data["extraction_model"] is None
        assert data["composite_confidence"] is None  # tightened by this correction


class TestUserResponseShape:
    """5/7: USER gets the same business fields MANAGER does (this correction's
    change — vendor and printed totals were previously redacted away), and
    no admin/developer diagnostic."""

    async def test_user_response_contains_the_business_fields_this_page_needs(
        self, app, api_client, db_session  # noqa: F811
    ):
        client, invoice_id = await _rcm_like_invoice(app, api_client, username=USER_USERNAME, password=USER_PASSWORD)
        try:
            data = (await client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        finally:
            await client.aclose()

        assert data["status"] == "REVIEW_REQUIRED"
        assert data["grand_total"] is not None
        assert data["vendor"] is not None
        assert data["store"]["label"]
        assert data["subtotal"] is not None
        assert data["tax_amount"] is not None
        assert data["discount_amount"] is not None

        assert len(data["line_items"]) == 4
        codes = {item["product_code"] for item in data["line_items"]}
        assert codes == {"00025328", "00025294", "00049171", "00028301"}

        assert len(data["case_mappings"]) == 4
        assert all(row["mapped"] is False for row in data["case_mappings"])

        assert data["pdi_export_allowed"] is False
        assert "mapping" in data["pdi_export_blocked_reason"].lower()

    async def test_user_response_never_contains_admin_developer_diagnostics(
        self, app, api_client, db_session  # noqa: F811
    ):
        client, invoice_id = await _rcm_like_invoice(app, api_client, username=USER_USERNAME, password=USER_PASSWORD)
        try:
            data = (await client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        finally:
            await client.aclose()

        assert data["ocr_text"] is None
        assert data["raw_extraction"] is None
        assert data["llm_metadata"] is None
        assert data["validation_report"] is None
        assert data["database"] is None
        assert data["extraction_model"] is None
        assert data["composite_confidence"] is None


class TestPermissionsUnchangedByThisCorrection:
    """8/9/10: this is a read/presentation fix — the write-side governance
    (who can propose, who can approve) is untouched."""

    async def test_user_still_cannot_approve_or_reject_proposals(
        self, app, api_client, db_session  # noqa: F811
    ):
        client, invoice_id = await _rcm_like_invoice(app, api_client, username=USER_USERNAME, password=USER_PASSWORD)
        try:
            submit = await client.post(
                f"/api/v1/invoices/{invoice_id}/case-mappings",
                json={"mappings": [{"item_code": "00025328", "units_per_case": 12}]},
            )
            assert submit.status_code == 200

            from sqlalchemy import select

            from app.models.product_data_proposal import ProductDataProposal

            proposal = (await db_session.execute(
                select(ProductDataProposal).where(ProductDataProposal.entity_key == "00025328")
            )).scalars().one()

            approve = await client.post(f"/api/v1/proposals/{proposal.id}/approve", json={"reviewed_by": USER_USERNAME})
            assert approve.status_code == 403
            reject = await client.post(f"/api/v1/proposals/{proposal.id}/reject", json={"reviewed_by": USER_USERNAME})
            assert reject.status_code == 403
        finally:
            await client.aclose()

    async def test_manager_can_still_review_and_approve(self, app, api_client, db_session):  # noqa: F811
        client, invoice_id = await _rcm_like_invoice(app, api_client, username=USER_USERNAME, password=USER_PASSWORD)
        try:
            submit = await client.post(
                f"/api/v1/invoices/{invoice_id}/case-mappings",
                json={"mappings": [{"item_code": "00025294", "units_per_case": 6}]},
            )
            assert submit.status_code == 200
        finally:
            await client.aclose()

        from sqlalchemy import select

        from app.models.product_data_proposal import ProductDataProposal

        proposal = (await db_session.execute(
            select(ProductDataProposal).where(ProductDataProposal.entity_key == "00025294")
        )).scalars().one()

        manager_client = await _client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
        try:
            approve = await manager_client.post(
                f"/api/v1/proposals/{proposal.id}/approve", json={"reviewed_by": MANAGER_USERNAME},
            )
            assert approve.status_code == 200, approve.text
        finally:
            await manager_client.aclose()


class TestAdminDetailUnchanged:
    """12: ADMIN keeps the full technical model exactly as before this correction."""

    async def test_admin_response_keeps_the_full_technical_model(
        self, app, api_client, db_session  # noqa: F811
    ):
        client, invoice_id = await _rcm_like_invoice(app, api_client, username=USER_USERNAME, password=USER_PASSWORD)
        await client.aclose()
        admin_client = await _client_as(app, ADMIN_USERNAME, ADMIN_PASSWORD)
        try:
            data = (await admin_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        finally:
            await admin_client.aclose()

        assert data["database"] is not None
        assert data["database"]["items_saved"] == 4
        assert data["validation_report"] is not None
        assert data["validation_report"]["summary"]["failed"] >= 1
        assert data["extraction_model"] is not None
        assert data["composite_confidence"] is not None
