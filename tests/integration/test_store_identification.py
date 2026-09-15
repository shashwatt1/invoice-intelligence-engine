"""
tests/integration/test_store_identification.py — the store an invoice is
received for is stated by the operator, checked before anything is
stored, and kept.

Pins the operational contract: no store means no document and no
invoice (not one filed under a configured default); a valid store is
persisted exactly as given and is what every later lookup uses.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.core.config import get_settings
from app.models.document import Document
from app.models.invoice import Invoice
from app.models.processing_log import ProcessingLog
from tests.integration.conftest import requires_db
from tests.integration.test_api_db import (  # noqa: F401 — fixture reuse
    INVOICE_PDF,
    api_client,
    process_file,
)

pytestmark = requires_db


async def _count(db_session, model) -> int:
    return (await db_session.execute(select(func.count()).select_from(model))).scalar_one()


class TestNoStoreMeansNothingIsCreated:
    async def test_a_missing_store_leaves_no_document_invoice_or_log(self, api_client, db_session):  # noqa: F811
        before = (await _count(db_session, Document), await _count(db_session, Invoice),
                  await _count(db_session, ProcessingLog))
        response = await api_client.post(
            "/api/v1/invoices/process",
            files={"file": ("acme-invoice.pdf", INVOICE_PDF, "application/pdf")},
        )
        assert response.status_code == 422
        assert "store_number is required" in response.json()["error"]["message"]
        after = (await _count(db_session, Document), await _count(db_session, Invoice),
                 await _count(db_session, ProcessingLog))
        assert after == before == (0, 0, 0)

    async def test_no_invoice_is_ever_filed_under_the_configured_store_implicitly(self, api_client, db_session):  # noqa: F811
        # settings.store_number still exists for scripts. Nothing on the
        # invoice path reads it: an upload without a store is refused, not
        # silently filed under it.
        configured = get_settings().store_number
        for data in ({}, {"store_number": ""}, {"store_number": "not-a-store"}):
            response = await api_client.post(
                "/api/v1/invoices/process",
                files={"file": ("acme-invoice.pdf", INVOICE_PDF, "application/pdf")},
                data=data,
            )
            assert response.status_code == 422, data
        filed = (await db_session.execute(
            select(func.count()).select_from(Invoice).where(Invoice.store_number == configured))).scalar_one()
        assert filed == 0
        assert await _count(db_session, Document) == 0


class TestAValidStoreIsKept:
    async def test_the_store_is_persisted_as_given_and_shown_everywhere(self, api_client, db_session):  # noqa: F811
        accepted = await process_file(api_client, store="86357232")
        status = (await api_client.get(accepted["status_url"])).json()["data"]
        assert status["is_terminal"] and status["invoice_id"]

        invoice = (await db_session.execute(select(Invoice))).scalar_one()
        assert invoice.store_number == "86357232"
        # the document status carries it from the moment of upload
        assert status["store_number"] == "86357232"
        with_payloads = (await api_client.get(accepted["status_url"], params={"include_payloads": "true"})).json()["data"]
        upload_stage = next(s for s in with_payloads["stages"] if s["stage"] == "UPLOAD")
        assert upload_stage["payload"]["store_number"] == "86357232"
        # and the API shows it on the detail and in history
        detail = (await api_client.get(f"/api/v1/invoices/{status['invoice_id']}")).json()["data"]
        assert detail["store_number"] == "86357232"
        [row] = (await api_client.get("/api/v1/invoices")).json()["items"]
        assert row["store_number"] == "86357232"
        # a store that is not the configured one is not rewritten to it
        assert invoice.store_number != get_settings().store_number

    async def test_surrounding_whitespace_is_not_a_different_store(self, api_client, db_session):  # noqa: F811
        accepted = await process_file(api_client, store="  47708760 ")
        await api_client.get(accepted["status_url"])
        invoice = (await db_session.execute(select(Invoice))).scalar_one()
        assert invoice.store_number == "47708760"
