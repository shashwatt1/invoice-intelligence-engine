"""
tests/integration/test_store_pending.py — an invoice can be read before
its store is known.

Collecting a store's reference data takes longer than photographing its
invoices, so the run must not be held hostage by the store question. A
person can say "store unknown, read it now": the invoice is stored
STORE_PENDING (store_id NULL) — readable, correctable, auditable — and
every store-scoped step refuses until the store is assigned. No store is
invented to get past the gate.

The old path — confirm a store and continue — is unchanged and is pinned
here alongside the new one. So is adding a store to the directory, which
is how a store whose invoices arrive first (RCM) becomes selectable.
"""

from __future__ import annotations

import uuid

from app.models.document import DocumentStatus
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.product_data_proposal_repository import ProductDataProposalRepository
from app.services.pipeline_service import InvoiceProcessingPipeline
from tests.integration.conftest import requires_db, store_id
from tests.integration.fakes import FakeExtraction, FakeStructuring, extracted_invoice
from tests.integration.test_api_db import api_client  # noqa: F401 — fixture reuse
from tests.integration.test_proposal_governance import line
from tests.pdf_builder import build_pdf

pytestmark = requires_db

STORE = "47708760"
REVIEWER = "data-team:shashwat"


async def upload_without_store(api_client, app, items=None, name="pending.pdf", **totals):  # noqa: F811
    """Upload with no store chosen; the fake OCR text names no store, so the run pauses."""
    from app.api.v1.invoices import get_pipeline

    items = items or [line()]
    app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
        extraction_service=FakeExtraction(),
        structuring_service=FakeStructuring(extracted_invoice(line_items=items, **totals)),
    )
    r = await api_client.post("/api/v1/invoices/process",
                              files={"file": (name, build_pdf([name + " pad " * 300]), "application/pdf")})
    assert r.status_code == 202, r.text
    document_id = r.json()["data"]["document_id"]
    status = (await api_client.get(f"/api/v1/documents/{document_id}")).json()["data"]
    assert status["status"] == DocumentStatus.STORE_CONFIRMATION_REQUIRED
    assert status["awaiting_store_confirmation"] is True
    return document_id


class TestReadNowAssignLater:
    async def test_deferring_the_store_persists_a_pending_invoice(self, api_client, app, db_session):  # noqa: F811
        document_id = await upload_without_store(api_client, app, subtotal=18.75, grand_total=18.75)
        r = await api_client.post(f"/api/v1/documents/{document_id}/defer-store",
                                  json={"deferred_by": REVIEWER, "note": "RCM data not loaded yet"})
        assert r.status_code == 200, r.text
        status = (await api_client.get(f"/api/v1/documents/{document_id}")).json()["data"]
        assert status["status"] == "COMPLETED"
        assert status["store"] is None
        assert status["invoice_id"]

        detail = (await api_client.get(f"/api/v1/invoices/{status['invoice_id']}")).json()["data"]
        assert detail["store"] is None
        assert detail["store_pending"] is True
        assert detail["status"] == "VALIDATED"                 # validation does not need a store
        assert detail["line_items"][0]["description"] == "BUSCH 4/6/160Z CAN"
        # nothing store-scoped is consulted or offered
        assert detail["case_mappings"] == []
        assert detail["review"]["status"] == "NONE"
        assert detail["pdi_export_allowed"] is False
        assert "Assign the store" in detail["pdi_export_blocked_reason"]
        # the decision is on the record
        stages = (await api_client.get(f"/api/v1/documents/{document_id}",
                                       params={"include_payloads": "true"})).json()["data"]["stages"]
        deferred = next(s for s in stages if (s["payload"] or {}).get("event") == "store_deferred")
        assert deferred["payload"]["deferred_by"] == REVIEWER
        assert deferred["payload"]["note"] == "RCM data not loaded yet"
        persisted = next(s for s in stages if s["stage"] == "PERSISTENCE")
        assert persisted["payload"]["store_pending"] is True and persisted["payload"]["store_id"] is None
        # and the history row shows no store, not a made-up one
        rows = (await api_client.get("/api/v1/invoices")).json()["items"]
        mine = next(row for row in rows if row["invoice_id"] == status["invoice_id"])
        assert mine["store"] is None

    async def test_nothing_store_scoped_can_happen_while_pending(self, api_client, app, db_session):  # noqa: F811
        document_id = await upload_without_store(api_client, app, subtotal=18.75, grand_total=18.75)
        await api_client.post(f"/api/v1/documents/{document_id}/defer-store", json={"deferred_by": REVIEWER})
        invoice_id = (await api_client.get(f"/api/v1/documents/{document_id}")).json()["data"]["invoice_id"]

        mapping = await api_client.post(f"/api/v1/invoices/{invoice_id}/case-mappings",
                                        json={"mappings": [{"item_code": "01820000063", "units_per_case": 4}]})
        assert mapping.status_code == 422
        assert mapping.json()["error"]["detail"]["store_pending"] is True
        assert await ProductDataProposalRepository(db_session).list(invoice_id=uuid.UUID(invoice_id)) == []

        export = await api_client.get(f"/api/v1/invoices/{invoice_id}/export", params={"format": "pdi"})
        assert export.status_code == 422
        assert "Assign the store" in export.json()["error"]["message"]
        # non-PDI exports (json/csv) are the invoice's own data and still work
        assert (await api_client.get(f"/api/v1/invoices/{invoice_id}/export", params={"format": "json"})).status_code == 200

    async def test_assigning_the_store_unlocks_the_store_scoped_steps(self, api_client, app, db_session):  # noqa: F811
        document_id = await upload_without_store(api_client, app, subtotal=18.75, grand_total=18.75)
        await api_client.post(f"/api/v1/documents/{document_id}/defer-store", json={"deferred_by": REVIEWER})
        invoice_id = (await api_client.get(f"/api/v1/documents/{document_id}")).json()["data"]["invoice_id"]

        r = await api_client.post(f"/api/v1/invoices/{invoice_id}/assign-store",
                                  json={"store_id": str(store_id(STORE)), "assigned_by": REVIEWER,
                                        "note": "confirmed with the manager"})
        assert r.status_code == 200, r.text
        detail = r.json()["data"]
        assert detail["store_pending"] is False
        assert detail["store"]["id"] == str(store_id(STORE))
        assert len(detail["case_mappings"]) == 1                # the store's mapping rows now show
        assert detail["pdi_export_blocked_reason"] != "Assign the store this invoice belongs to before it can be exported."
        # the document follows the invoice, and the assignment is logged
        status = (await api_client.get(f"/api/v1/documents/{document_id}", params={"include_payloads": "true"})).json()["data"]
        assert status["store"]["id"] == str(store_id(STORE))
        assigned = next(s for s in status["stages"] if (s["payload"] or {}).get("event") == "store_assigned")
        assert assigned["payload"]["assigned_by"] == REVIEWER and assigned["payload"]["note"] == "confirmed with the manager"
        # from here the normal governance applies: confirming a mapping raises a proposal
        m = await api_client.post(f"/api/v1/invoices/{invoice_id}/case-mappings",
                                  json={"mappings": [{"item_code": "01820000063", "units_per_case": 4}]})
        assert m.status_code == 200
        [p] = await ProductDataProposalRepository(db_session).list(invoice_id=uuid.UUID(invoice_id))
        assert p.store_id == store_id(STORE)

    async def test_an_assigned_invoice_is_not_moved_here_and_unknown_stores_are_refused(self, api_client, app):  # noqa: F811
        document_id = await upload_without_store(api_client, app, subtotal=18.75, grand_total=18.75)
        await api_client.post(f"/api/v1/documents/{document_id}/defer-store", json={"deferred_by": REVIEWER})
        invoice_id = (await api_client.get(f"/api/v1/documents/{document_id}")).json()["data"]["invoice_id"]
        assert (await api_client.post(f"/api/v1/invoices/{invoice_id}/assign-store",
                                      json={"store_id": str(uuid.uuid4()), "assigned_by": REVIEWER})).status_code == 404
        assert (await api_client.post(f"/api/v1/invoices/{invoice_id}/assign-store",
                                      json={"store_id": str(store_id(STORE)), "assigned_by": ""})).status_code == 422
        assert (await api_client.post(f"/api/v1/invoices/{invoice_id}/assign-store",
                                      json={"store_id": str(store_id(STORE)), "assigned_by": REVIEWER})).status_code == 200
        again = await api_client.post(f"/api/v1/invoices/{invoice_id}/assign-store",
                                      json={"store_id": str(store_id(STORE)), "assigned_by": REVIEWER})
        assert again.status_code == 422                          # moving between stores is not this endpoint

    async def test_deferral_only_applies_to_a_waiting_document(self, api_client, app):  # noqa: F811
        document_id = await upload_without_store(api_client, app, subtotal=18.75, grand_total=18.75)
        await api_client.post(f"/api/v1/documents/{document_id}/defer-store", json={"deferred_by": REVIEWER})
        again = await api_client.post(f"/api/v1/documents/{document_id}/defer-store", json={"deferred_by": REVIEWER})
        assert again.status_code == 422
        assert (await api_client.post(f"/api/v1/documents/{uuid.uuid4()}/defer-store",
                                      json={"deferred_by": REVIEWER})).status_code == 404
        assert (await api_client.post(f"/api/v1/documents/{document_id}/defer-store", json={})).status_code == 422


class TestTheOldPathStillWorks:
    async def test_confirming_a_store_continues_exactly_as_before(self, api_client, app, db_session):  # noqa: F811
        document_id = await upload_without_store(api_client, app, subtotal=18.75, grand_total=18.75)
        r = await api_client.post(f"/api/v1/documents/{document_id}/confirm-store",
                                  json={"store_id": str(store_id(STORE)), "confirmed_by": REVIEWER})
        assert r.status_code == 200, r.text
        status = (await api_client.get(f"/api/v1/documents/{document_id}")).json()["data"]
        assert status["status"] == "COMPLETED" and status["store"]["id"] == str(store_id(STORE))
        detail = (await api_client.get(f"/api/v1/invoices/{status['invoice_id']}")).json()["data"]
        assert detail["store_pending"] is False and detail["store"]["id"] == str(store_id(STORE))
        assert len(detail["case_mappings"]) == 1
        invoice = await InvoiceRepository(db_session).get(uuid.UUID(status["invoice_id"]))
        assert invoice.store_id == store_id(STORE)


class TestAddingAStoreToTheDirectory:
    async def test_a_person_adds_rcm_and_can_process_for_it_at_once(self, api_client, app):  # noqa: F811
        from app.api.v1.invoices import get_pipeline

        r = await api_client.post("/api/v1/stores", json={
            "display_name": "Red Cliff Market", "customer_name": "Red Cliff Petroleum, LLC",
            "address_line_1": "1409 E St George Blvd", "city": "Saint George", "state": "UT",
            "postal_code": "84790", "created_by": REVIEWER, "confirm": True,
        })
        assert r.status_code == 201, r.text
        store = r.json()["data"]
        assert store["display_name"] == "Red Cliff Market"
        assert store["identity_status"] == "confirmed"
        assert store["source_codes"] == []                       # no code inferred, ever
        assert "Added by data-team:shashwat" in store["notes"]
        listed = (await api_client.get("/api/v1/stores")).json()["data"]
        assert any(s["id"] == store["id"] for s in listed)

        # the same name again is refused
        dup = await api_client.post("/api/v1/stores", json={"display_name": "red cliff market", "created_by": REVIEWER})
        assert dup.status_code == 422

        # chosen up front at upload, an invoice that names no store proceeds for it
        app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
            extraction_service=FakeExtraction(),
            structuring_service=FakeStructuring(extracted_invoice(line_items=[line()], subtotal=18.75, grand_total=18.75)),
        )
        up = await api_client.post("/api/v1/invoices/process",
                                   files={"file": ("rcm.pdf", build_pdf(["rcm pad " * 300]), "application/pdf")},
                                   data={"store_id": store["id"]})
        assert up.status_code == 202
        status = (await api_client.get(up.json()["data"]["status_url"])).json()["data"]
        assert status["status"] == "COMPLETED" and status["store"]["id"] == store["id"]
        detail = (await api_client.get(f"/api/v1/invoices/{status['invoice_id']}")).json()["data"]
        # a brand-new store has no reference data: the row is unmapped, nothing is guessed
        [row] = detail["case_mappings"]
        assert row["mapped"] is False and row["units_per_case"] is None

    async def test_a_store_needs_a_name_and_a_person(self, api_client):  # noqa: F811
        assert (await api_client.post("/api/v1/stores", json={"display_name": "", "created_by": "r"})).status_code == 422
        assert (await api_client.post("/api/v1/stores", json={"display_name": "X"})).status_code == 422
