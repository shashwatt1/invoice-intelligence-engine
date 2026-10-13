"""
tests/integration/test_mapping_ux_db.py — mapping-screen UX through the real API and Postgres.

The review panel uses only existing endpoints; these tests pin what it relies on:

  * the review rows (and Requires Mapping) carry the line's own invoice prices,
    position and stored code — source values, nothing derived or borrowed;
  * a manager's product-code correction is recorded in the line's history and
    changes exactly that line's PDI item code — no other line, and no Product
    Master identifier;
  * a description correction keeps the original for traceability;
  * a USER cannot correct a line, and the approval still needs a manager.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.models.document import Document
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.models.processing_log import ProcessingLog
from app.models.product_master import MasterProduct, MasterProductIdentifier
from app.models.store import Store
from app.models.user import UserRole
from app.services.product_master.identifiers import upc_a_check_digit
from tests.integration.conftest import USER_PASSWORD, USER_USERNAME, requires_db, user_id
from tests.integration.test_api_db import api_client  # noqa: F401 — fixture reuse

pytestmark = requires_db

CODE_A = "07777711111"
PRINTED_A = CODE_A + upc_a_check_digit(CODE_A)
CODE_B = "07777722222"
PRINTED_B = CODE_B + upc_a_check_digit(CODE_B)


@pytest.fixture
async def corpus(db_session):
    await db_session.execute(text("TRUNCATE master_products CASCADE"))   # the test database only
    store = Store(display_name="AF Mapping UX", identity_status="confirmed")
    db_session.add(store)
    await db_session.flush()
    product = MasterProduct(canonical_key=f"upc:{PRINTED_A}", canonical_upc=PRINTED_A, identity_basis="UPC_A")
    db_session.add(product)
    await db_session.flush()
    db_session.add(MasterProductIdentifier(product_id=product.id, raw_value=PRINTED_A, normalized_value=PRINTED_A,
                                           identifier_type="UPC_A", source_system="test"))
    # Uploaded by the USER account, so that account may see it (a USER sees only their own documents).
    document = Document(filename="ux.pdf", mime_type="application/pdf", file_size_bytes=1, file_path="test/ux.pdf",
                        file_hash=uuid.uuid4().hex, uploaded_by_user_id=user_id(UserRole.USER.value))
    db_session.add(document)
    await db_session.flush()
    invoice = Invoice(document_id=document.id, store_id=store.id, invoice_number="UX-1", status="VALIDATED",
                      vendor_name="Test Beverage", invoice_date=date(2026, 9, 1), currency="USD",
                      subtotal=Decimal("102.00"), grand_total=Decimal("102.00"))
    db_session.add(invoice)
    await db_session.flush()
    db_session.add_all([
        InvoiceItem(invoice_id=invoice.id, sort_order=0, description="BUD LIGHT 24/12OZ", product_sku=PRINTED_A,
                    pack_size="24/12OZ", quantity=Decimal("2"), unit_price=Decimal("30.0000"),
                    line_total=Decimal("60.00"), discount=Decimal("0.5000"), line_type="product"),
        InvoiceItem(invoice_id=invoice.id, sort_order=1, description="TWISTD TEA 12PK", product_sku="N/A",
                    quantity=Decimal("1"), unit_price=Decimal("42.0000"), line_total=Decimal("42.00"),
                    line_type="product"),
    ])
    await db_session.commit()
    yield {"invoice": str(invoice.id), "store": store.id, "product": product.id, "document": document.id}
    await db_session.execute(text("TRUNCATE master_products CASCADE"))   # the test database only
    await db_session.commit()


async def _rows(client, invoice_id):
    response = await client.get(f"/api/v1/invoices/{invoice_id}")
    assert response.status_code == 200, response.text
    return {row["sort_order"]: row for row in response.json()["data"]["case_mappings"]}


async def _approve(client, invoice_id, code, units):
    confirmed = await client.post(f"/api/v1/invoices/{invoice_id}/case-mappings",
                                  json={"mappings": [{"item_code": code, "units_per_case": units}]})
    assert confirmed.status_code == 200, confirmed.text
    proposal_id = next(r["pending_proposal_id"] for r in confirmed.json()["data"]["case_mappings"] if r["item_code"] == code)
    approved = await client.post(f"/api/v1/proposals/{proposal_id}/approve", json={"note": "test"})
    assert approved.status_code == 200, approved.text


class TestTheRowsCarryTheLine:
    async def test_prices_position_and_stored_code(self, api_client, corpus):  # noqa: F811
        rows = await _rows(api_client, corpus["invoice"])
        a, b = rows[0], rows[1]
        assert (a["item_code"], a["product_code"], a["quantity"], a["unit_price"], a["line_total"], a["unit_discount"]) == (
            CODE_A, PRINTED_A, 2.0, 30.0, 60.0, 0.5)
        assert (b["item_code"], b["product_code"], b["unit_price"]) == (None, "N/A", 42.0)   # no usable code, listed
        assert a["original_description"] is None

    async def test_requires_mapping_shows_the_line_total(self, api_client, corpus):  # noqa: F811
        response = await api_client.get("/api/v1/mapping-queue", params={"store_id": str(corpus["store"])})
        assert response.status_code == 200, response.text
        [row] = [r for r in response.json()["items"] if r["item_code"] == CODE_A]
        [occurrence] = row["occurrences"]
        assert (occurrence["quantity"], occurrence["unit_price"], occurrence["line_total"]) == (2.0, 30.0, 60.0)


class TestCorrectingTheCode:
    async def test_the_correction_changes_only_that_lines_edi_code(self, api_client, corpus, db_session):  # noqa: F811
        identifiers_before = (await db_session.execute(
            select(MasterProductIdentifier.normalized_value, MasterProductIdentifier.raw_value))).all()

        corrected = await api_client.patch(f"/api/v1/invoices/{corpus['invoice']}/items/1", json={
            "product_code": PRINTED_B, "corrected_by": "testadmin", "note": "read the photo"})
        assert corrected.status_code == 200, corrected.text
        item = corrected.json()["data"]["item"]
        assert (item["product_code"], item["item_code"]) == (PRINTED_B, CODE_B)
        [entry] = [e for e in item["correction_history"] if e["field"] == "product_code"]
        assert (entry["old"], entry["new"], entry["by"], entry["note"]) == ("N/A", PRINTED_B, "testadmin", "read the photo")

        rows = await _rows(api_client, corpus["invoice"])
        assert (rows[0]["item_code"], rows[1]["item_code"]) == (CODE_A, CODE_B)

        await _approve(api_client, corpus["invoice"], CODE_A, 24)
        await _approve(api_client, corpus["invoice"], CODE_B, 12)
        export = await api_client.get(f"/api/v1/invoices/{corpus['invoice']}/export", params={"format": "pdi"})
        assert export.status_code == 200, export.text
        b_records = [line for line in export.text.split("\r\n") if line.startswith("B")]
        assert len(b_records) == 2
        assert CODE_A in b_records[0] and CODE_B in b_records[1]
        assert CODE_B not in b_records[0] and "N/A" not in export.text

        db_session.expire_all()
        identifiers_after = (await db_session.execute(
            select(MasterProductIdentifier.normalized_value, MasterProductIdentifier.raw_value))).all()
        assert identifiers_after == identifiers_before                     # no Product Master identifier touched

    async def test_a_code_without_usable_digits_is_recorded_but_has_no_item_code(self, api_client, corpus):  # noqa: F811
        corrected = await api_client.patch(f"/api/v1/invoices/{corpus['invoice']}/items/1",
                                           json={"product_code": "000-000", "corrected_by": "testadmin"})
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["data"]["item"]["item_code"] is None

    async def test_a_description_correction_keeps_the_original(self, api_client, corpus):  # noqa: F811
        for corrected_text in ("TWISTED TEA 12PK", "TWISTED TEA ORIGINAL 12PK"):
            response = await api_client.patch(f"/api/v1/invoices/{corpus['invoice']}/items/1",
                                              json={"description": corrected_text, "corrected_by": "testadmin"})
            assert response.status_code == 200, response.text
        row = (await _rows(api_client, corpus["invoice"]))[1]
        assert (row["description"], row["original_description"]) == ("TWISTED TEA ORIGINAL 12PK", "TWISTD TEA 12PK")

    async def test_the_signed_in_account_is_recorded_whatever_name_is_sent(self, api_client, corpus, db_session):  # noqa: F811
        corrected = await api_client.patch(f"/api/v1/invoices/{corpus['invoice']}/items/0", json={
            "description": "BUD LIGHT 24/12OZ CAN", "corrected_by": "someone-else", "note": "spoof attempt"})
        assert corrected.status_code == 200, corrected.text
        [entry] = [e for e in corrected.json()["data"]["item"]["correction_history"] if e["field"] == "description"]
        assert entry["by"] == "testadmin"                                   # the authenticated account
        added = await api_client.post(f"/api/v1/invoices/{corpus['invoice']}/items", json={
            "description": "EXTRA LINE", "quantity": "1", "unit_price": "1.00", "line_total": "1.00",
            "added_by": "someone-else"})
        assert added.status_code == 201, added.text
        logs = (await db_session.execute(select(ProcessingLog.payload).where(
            ProcessingLog.document_id == corpus["document"]))).scalars().all()
        attributed = [(p["event"], p["by"]) for p in logs if p and p.get("event", "").startswith("line_item_")]
        assert attributed == [("line_item_corrected", "testadmin"), ("line_item_added", "testadmin")]
        assert all(p.get("by") != "someone-else" for p in logs if p)

    async def test_a_user_cannot_correct_or_approve(self, app, api_client, corpus):  # noqa: F811
        user = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        try:
            login = await user.post("/api/v1/auth/login", json={"username": USER_USERNAME, "password": USER_PASSWORD})
            assert login.status_code == 200
            refused = await user.patch(f"/api/v1/invoices/{corpus['invoice']}/items/0", json={"product_code": PRINTED_B})
            assert refused.status_code == 403
            confirmed = await user.post(f"/api/v1/invoices/{corpus['invoice']}/case-mappings",
                                        json={"mappings": [{"item_code": CODE_A, "units_per_case": 24}]})
            assert confirmed.status_code == 200, confirmed.text                 # a USER may propose
            proposal_id = next(r["pending_proposal_id"] for r in confirmed.json()["data"]["case_mappings"]
                               if r["item_code"] == CODE_A)
            assert (await user.post(f"/api/v1/proposals/{proposal_id}/approve", json={})).status_code == 403
        finally:
            await user.aclose()
        rows = await _rows(api_client, corpus["invoice"])
        assert rows[0]["product_code"] == PRINTED_A and rows[0]["mapped"] is False
