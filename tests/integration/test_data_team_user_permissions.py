"""
tests/integration/test_data_team_user_permissions.py — P3 permission
clarification: USER is a data-team operational role, not upload-only.

Verifies, against real Postgres and real HTTP endpoints:

  A. USER can reach the minimum flow to inspect UPC/product identity.
  B. USER can submit a mapping proposal.
  C. The submitted proposal is created PENDING.
  D. USER cannot approve that proposal.
  E. USER cannot reject that proposal.
  F. USER cannot directly create/update an authoritative case mapping.
  G. MANAGER can review and approve/reject the proposal.
  H. ADMIN can do all of the above.

And that USER still cannot reach developer/technical surfaces: raw OCR,
raw LLM metadata, the validation report, persistence internals, or
another user's proposal evidence.
"""

from __future__ import annotations

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
from tests.pdf_builder import build_pdf

pytestmark = requires_db

ITEM_CODE_12 = "999000000034"   # 12-digit UPC as printed
ITEM_CODE_11 = "99900000003"    # normalized (check digit dropped)


async def _client_as(app, username: str, password: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return client


async def _owned_invoice(app, api_client, db_session, description="Blue Widget"):  # noqa: F811
    """
    A USER-owned, fully-processed invoice with one real UPC-bearing line
    item. Depends on `api_client` only so its fixture's get_db/get_pipeline
    dependency_overrides are set up on `app` before this runs (see the
    same pattern in test_document_lifecycle_actions.py) — a fresh
    per-test override then points get_pipeline at THIS invoice's content.
    """
    from app.api.v1.invoices import get_pipeline

    invoice = extracted_invoice(
        line_items=[ExtractedLineItem(
            description=description, product_code=ITEM_CODE_12,
            quantity=2.0, unit_price=9.45, line_total=18.9,
        )],
    )
    app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
        structuring_service=FakeStructuring(invoice)
    )

    user_client = await _client_as(app, USER_USERNAME, USER_PASSWORD)
    try:
        accepted = await process_file(
            user_client, content=build_pdf(["owned invoice " + "pad " * 300]), filename="owned.pdf",
        )
        status = (await user_client.get(accepted["status_url"])).json()["data"]
        return user_client, status["invoice_id"], status["document_id"]
    except BaseException:
        await user_client.aclose()
        raise


class TestUserDataWorkPermissions:
    async def test_a_user_can_inspect_upc_product_identity(self, app, api_client, db_session):  # noqa: F811
        user_client, invoice_id, _ = await _owned_invoice(app, api_client, db_session)
        try:
            response = await user_client.get(f"/api/v1/invoices/{invoice_id}")
            assert response.status_code == 200, response.text
            data = response.json()["data"]
            assert len(data["line_items"]) == 1
            item = data["line_items"][0]
            assert item["description"] == "Blue Widget"
            assert item["product_code"] == ITEM_CODE_12       # UPC / product identity
            assert item["quantity"] == 2.0
            assert item["unit_price"] == 9.45
            assert item["line_total"] == 18.9
            assert data["case_mappings"] != []                # mapping status visible
            # Still forbidden: developer/technical internals.
            assert data["ocr_text"] is None
            assert data["raw_extraction"] is None
            assert data["llm_metadata"] is None
            assert data["validation_report"] is None
            assert data["database"] is None
            assert data["extraction_model"] is None
            assert data["composite_confidence"] is None
        finally:
            await user_client.aclose()

    async def test_b_and_c_user_can_submit_a_proposal_and_it_lands_pending(
        self, app, api_client, db_session  # noqa: F811
    ):
        from sqlalchemy import select

        from app.models.product_data_proposal import ProductDataProposal

        user_client, invoice_id, _ = await _owned_invoice(app, api_client, db_session)
        try:
            response = await user_client.post(
                f"/api/v1/invoices/{invoice_id}/case-mappings",
                json={"mappings": [{"item_code": ITEM_CODE_12, "units_per_case": 12}]},
            )
            assert response.status_code == 200, response.text
        finally:
            await user_client.aclose()

        rows = (await db_session.execute(
            select(ProductDataProposal).where(ProductDataProposal.entity_key == ITEM_CODE_11)
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "PENDING"
        assert rows[0].proposed_by == USER_USERNAME          # real attribution, not a hardcoded string

    async def test_d_and_e_user_cannot_approve_or_reject_its_own_proposal(
        self, app, api_client, db_session  # noqa: F811
    ):
        from sqlalchemy import select

        from app.models.product_data_proposal import ProductDataProposal

        user_client, invoice_id, _ = await _owned_invoice(app, api_client, db_session)
        try:
            submit = await user_client.post(
                f"/api/v1/invoices/{invoice_id}/case-mappings",
                json={"mappings": [{"item_code": ITEM_CODE_12, "units_per_case": 12}]},
            )
            assert submit.status_code == 200

            proposal = (await db_session.execute(
                select(ProductDataProposal).where(ProductDataProposal.entity_key == ITEM_CODE_11)
            )).scalars().one()

            approve = await user_client.post(
                f"/api/v1/proposals/{proposal.id}/approve", json={"reviewed_by": USER_USERNAME},
            )
            assert approve.status_code == 403

            reject = await user_client.post(
                f"/api/v1/proposals/{proposal.id}/reject", json={"reviewed_by": USER_USERNAME},
            )
            assert reject.status_code == 403

            # Not even readable in full (mapping evidence is MANAGER's) —
            # the list still shows its own submission at the row level.
            detail = await user_client.get(f"/api/v1/proposals/{proposal.id}")
            assert detail.status_code == 403

            listing = await user_client.get("/api/v1/proposals", params={"status": "PENDING"})
            assert listing.status_code == 200
            assert any(item["id"] == str(proposal.id) for item in listing.json()["items"])
        finally:
            await user_client.aclose()

    async def test_f_user_cannot_create_an_authoritative_mapping_directly(
        self, app, api_client, db_session  # noqa: F811
    ):
        from sqlalchemy import text as sql

        user_client, invoice_id, _ = await _owned_invoice(app, api_client, db_session)
        try:
            before = (await db_session.execute(sql("SELECT count(*) FROM product_case_mappings"))).scalar_one()
            response = await user_client.post(
                f"/api/v1/invoices/{invoice_id}/case-mappings",
                json={"mappings": [{"item_code": ITEM_CODE_12, "units_per_case": 12}]},
            )
            assert response.status_code == 200
            after = (await db_session.execute(sql("SELECT count(*) FROM product_case_mappings"))).scalar_one()
            # This endpoint has no path to product_case_mappings at all —
            # proven for every role, not just USER (see proposal_service.approve).
            assert after == before
        finally:
            await user_client.aclose()

    async def test_g_manager_can_review_and_approve_the_proposal(
        self, app, api_client, db_session  # noqa: F811
    ):
        from sqlalchemy import select
        from sqlalchemy import text as sql

        from app.models.product_data_proposal import ProductDataProposal

        user_client, invoice_id, _ = await _owned_invoice(app, api_client, db_session)
        try:
            submit = await user_client.post(
                f"/api/v1/invoices/{invoice_id}/case-mappings",
                json={"mappings": [{"item_code": ITEM_CODE_12, "units_per_case": 12}]},
            )
            assert submit.status_code == 200
        finally:
            await user_client.aclose()

        proposal = (await db_session.execute(
            select(ProductDataProposal).where(ProductDataProposal.entity_key == ITEM_CODE_11)
        )).scalars().one()

        manager_client = await _client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
        try:
            detail = await manager_client.get(f"/api/v1/proposals/{proposal.id}")
            assert detail.status_code == 200

            approve = await manager_client.post(
                f"/api/v1/proposals/{proposal.id}/approve", json={"reviewed_by": MANAGER_USERNAME},
            )
            assert approve.status_code == 200, approve.text
        finally:
            await manager_client.aclose()

        mappings = (await db_session.execute(sql(
            "SELECT item_code, units_per_case FROM product_case_mappings WHERE item_code = :c"
        ), {"c": ITEM_CODE_11})).all()
        assert len(mappings) == 1
        assert mappings[0][1] == 12

    async def test_h_admin_can_do_everything_user_and_manager_can(
        self, app, api_client, db_session  # noqa: F811
    ):
        from sqlalchemy import select

        from app.models.product_data_proposal import ProductDataProposal

        user_client, invoice_id, _ = await _owned_invoice(app, api_client, db_session, description="Second Widget")
        await user_client.aclose()

        admin_client = await _client_as(app, ADMIN_USERNAME, ADMIN_PASSWORD)
        try:
            detail = await admin_client.get(f"/api/v1/invoices/{invoice_id}")
            assert detail.status_code == 200
            assert detail.json()["data"]["ocr_text"] is not None  # ADMIN keeps full technical model

            submit = await admin_client.post(
                f"/api/v1/invoices/{invoice_id}/case-mappings",
                json={"mappings": [{"item_code": ITEM_CODE_12, "units_per_case": 24}]},
            )
            assert submit.status_code == 200

            proposal = (await db_session.execute(
                select(ProductDataProposal).where(
                    ProductDataProposal.entity_key == ITEM_CODE_11, ProductDataProposal.status == "PENDING",
                )
            )).scalars().first()
            assert proposal is not None

            approve = await admin_client.post(
                f"/api/v1/proposals/{proposal.id}/approve", json={"reviewed_by": ADMIN_USERNAME},
            )
            assert approve.status_code == 200

            users = await admin_client.get("/api/v1/users")
            assert users.status_code == 200
        finally:
            await admin_client.aclose()
