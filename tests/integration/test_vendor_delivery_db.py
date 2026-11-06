"""
tests/integration/test_vendor_delivery_db.py — the vendor and delivery date a person declares at upload.

Both are required of a USER or MANAGER before a file is touched; an ADMIN may skip them at upload, but the invoice cannot be
marked Retailed until both are declared. A vendor that is not listed can be added directly by a MANAGER/ADMIN, or asked for
by a USER, in which case the invoice waits for approval. The vendor the document prints is compared with the one chosen:
a mismatch is a review reason, never a pause.
"""
# ruff: noqa: F811 — pytest fixtures are reused by importing them, as the other integration tests do

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text

from app.models.document import Document
from app.models.invoice import SOURCE_OPERATOR, Invoice
from app.models.processing_log import ProcessingLog
from app.models.product_data_proposal import ProductDataProposal
from app.models.vendor import VENDOR_CONFIRMED, Vendor
from app.models.vendor_request import (
    REQUEST_APPROVED,
    REQUEST_PENDING,
    REQUEST_REJECTED,
    VendorRequest,
)
from app.services.pipeline_service import InvoiceProcessingPipeline
from tests.integration.conftest import (
    MANAGER_PASSWORD,
    MANAGER_USERNAME,
    PHYSICAL_STORE,
    RCM_STORE,
    SOURCE_ONLY_STORE,
    USER_PASSWORD,
    USER_USERNAME,
    approve_all_pending,
    requires_db,
    store_id,
)
from tests.integration.fakes import FakeStructuring, extracted_invoice
from tests.integration.test_api_db import STORE, api_client  # noqa: F401 — fixture reuse
from tests.integration.test_inventory_db import (  # noqa: F401 — fixture reuse
    ALE,
    LAGER,
    approve,
    approve_submission,
    ledger_counts,
    preview_of,
    received,
    refused_as_received,
    submit,
)
from tests.integration.test_store_access_db import client_as
from tests.pdf_builder import build_pdf

pytestmark = requires_db

PRINTED = "Acme Corp"          # what FakeStructuring's invoice prints as its vendor


async def make_vendor(db_session, name, *, confirmed=True) -> Vendor:
    vendor = Vendor(name=name, display_name=name if confirmed else None,
                    identity_status=VENDOR_CONFIRMED if confirmed else "unresolved")
    db_session.add(vendor)
    await db_session.commit()
    return vendor


@pytest.fixture(autouse=True)
def _requirement_on(api_client, monkeypatch):
    """api_client lets older tests upload without the declarations; these tests are about them."""
    from app.core.config import get_settings
    from app.services import upload_choices
    monkeypatch.setattr(upload_choices, "get_settings", get_settings)


@pytest.fixture
async def manager(app, api_client):  # noqa: F811 — api_client wires the app to the test database
    client = await client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
    yield client
    await client.aclose()


@pytest.fixture
async def user(app, api_client):  # noqa: F811
    client = await client_as(app, USER_USERNAME, USER_PASSWORD)
    yield client
    await client.aclose()


_pad = iter(range(10_000))


async def upload(client, app, *, vendor_id=None, vendor_request_id=None, delivery_date=None, printed=PRINTED):
    """POST /invoices/process with the declared fields; returns the raw response."""
    from app.api.v1.invoices import get_pipeline

    printed_vendor = extracted_invoice().vendor.model_copy(update={"name": printed})
    app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
        structuring_service=FakeStructuring(extracted_invoice(vendor=printed_vendor)))
    data = {"store_id": str(store_id(STORE))}
    if vendor_id:
        data["vendor_id"] = str(vendor_id)
    if vendor_request_id:
        data["vendor_request_id"] = str(vendor_request_id)
    if delivery_date:
        data["delivery_date"] = delivery_date
    return await client.post("/api/v1/invoices/process", data=data, files={
        "file": ("inv.pdf", build_pdf([f"declared invoice {next(_pad)} " + "pad " * 300]), "application/pdf")})


async def invoice_of(client, accepted) -> dict:
    status = (await client.get(accepted.json()["data"]["status_url"])).json()["data"]
    return (await client.get(f"/api/v1/invoices/{status['invoice_id']}")).json()["data"]


async def count(db_session, model) -> int:
    return (await db_session.execute(select(func.count()).select_from(model))).scalar()


class TestRequiredAtUpload:
    async def test_a_user_or_manager_must_choose_both(self, user, manager, db_session, app):
        acme = await make_vendor(db_session, "Acme Corp")
        for client in (user, manager):
            missing_vendor = await upload(client, app, delivery_date="2026-10-22")
            assert missing_vendor.status_code == 422
            assert missing_vendor.json()["error"]["detail"]["field"] == "vendor_id"
            missing_date = await upload(client, app, vendor_id=acme.id)
            assert missing_date.status_code == 422
            assert missing_date.json()["error"]["detail"]["field"] == "delivery_date"
        assert await count(db_session, Document) == 0, "refused before any document is created"

    async def test_an_admin_may_skip_both_at_upload(self, api_client, app, db_session):
        accepted = await upload(api_client, app)
        assert accepted.status_code == 202, accepted.text
        detail = await invoice_of(api_client, accepted)
        assert detail["delivery_date"] is None and detail["vendor_source"] == "extracted"
        assert detail["declarations_missing"], "the Retailed gate still asks for both"

    async def test_an_unknown_vendor_and_an_impossible_date_are_refused(self, manager, db_session, app):
        unknown = await upload(manager, app, vendor_id=uuid.uuid4(), delivery_date="2026-10-22")
        assert unknown.status_code in (404, 422)
        acme = await make_vendor(db_session, "Acme Corp")
        for bad in ("2999-01-01", "22/10/2026", "1990-01-01"):
            refused = await upload(manager, app, vendor_id=acme.id, delivery_date=bad)
            assert refused.status_code == 422, bad
        assert await count(db_session, Document) == 0

    async def test_a_vendor_and_a_request_together_are_refused(self, manager, db_session, app):
        acme = await make_vendor(db_session, "Acme Corp")
        asked = await manager.post("/api/v1/vendor-requests", json={"name": "Brand New Vendor"})
        refused = await upload(manager, app, vendor_id=acme.id, vendor_request_id=asked.json()["data"]["id"],
                               delivery_date="2026-10-22")
        assert refused.status_code == 422


class TestTheChoiceIsStored:
    async def test_the_chosen_vendor_and_date_land_on_the_invoice(self, manager, db_session, app):
        acme = await make_vendor(db_session, "Acme Corp")
        accepted = await upload(manager, app, vendor_id=acme.id, delivery_date="2026-10-22")
        assert accepted.status_code == 202, accepted.text
        detail = await invoice_of(manager, accepted)
        assert detail["delivery_date"] == "2026-10-22"
        assert detail["vendor_source"] == "operator"
        assert detail["vendor"]["id"] == str(acme.id)
        assert detail["declarations_missing"] is None
        assert await count(db_session, Vendor) == 1, "the chosen vendor is used; the printed one is not created again"

    async def test_a_printed_vendor_that_disagrees_is_a_review_reason_not_a_pause(self, api_client, manager, db_session, app):
        zenith = await make_vendor(db_session, "Zenith Traders")
        accepted = await upload(manager, app, vendor_id=zenith.id, delivery_date="2026-10-22")
        detail = await invoice_of(api_client, accepted)             # the validation report is the administrator's view
        assert detail["vendor"]["id"] == str(zenith.id), "the choice stands"
        assert detail["status"] == "REVIEW_REQUIRED"
        assert detail["document_status"] != "VENDOR_CONFIRMATION_REQUIRED" and "CONFIRMATION" not in detail["document_status"]
        failed = [c for c in detail["validation_report"]["checks"] if c["status"] == "FAILED"]
        assert [c["name"] for c in failed] == ["VENDOR_MATCHES_CHOICE"]
        assert "Zenith" in failed[0]["message"]
        # A manager sees the sentence too, not only the administrator who can read the validation report.
        seen = (await manager.get(f"/api/v1/invoices/{detail['invoice_id']}")).json()["data"]
        assert "Zenith" in seen["vendor_mismatch"] and "Zenith" in failed[0]["message"]
        assert seen["validation_report"] is None, "the sentence is not the technical report"

    async def test_the_user_who_uploaded_it_is_told_too(self, user, db_session, app):
        zenith = await make_vendor(db_session, "Zenith Traders")
        accepted = await upload(user, app, vendor_id=zenith.id, delivery_date="2026-10-22")
        assert accepted.status_code == 202, accepted.text
        seen = await invoice_of(user, accepted)
        assert "Zenith" in seen["vendor_mismatch"] and "Acme" in seen["vendor_mismatch"]

    async def test_a_printed_vendor_that_agrees_does_not_flag(self, api_client, manager, db_session, app):
        acme = await make_vendor(db_session, "ACME Corporation")
        accepted = await upload(manager, app, vendor_id=acme.id, delivery_date="2026-10-22")
        detail = await invoice_of(api_client, accepted)
        checks = {c["name"]: c["status"] for c in detail["validation_report"]["checks"]}
        assert checks["VENDOR_MATCHES_CHOICE"] == "PASSED" and detail["status"] == "VALIDATED"
        assert detail["vendor_mismatch"] is None


class TestAskingForAVendor:
    """A USER or MANAGER asks; only an ADMIN adds directly or decides. Nothing reaches the Vendor Master before that."""

    async def test_a_user_asks_processes_fully_and_the_admin_approval_attaches_the_vendor(self, api_client, user, db_session, app):
        asked = await user.post("/api/v1/vendor-requests", json={"name": "Acme Corp"})
        assert asked.status_code == 201, asked.text
        request_id = asked.json()["data"]["id"]

        accepted = await upload(user, app, vendor_request_id=request_id, delivery_date="2026-10-22")
        assert accepted.status_code == 202, accepted.text
        detail = await invoice_of(user, accepted)
        assert detail["vendor_source"] == "requested" and detail["vendor"] is None
        assert detail["vendor_request"] == {"id": request_id, "name": "Acme Corp", "status": REQUEST_PENDING}
        assert detail["declarations_missing"] is None, "a pending vendor does not hold the invoice back"
        assert await count(db_session, Vendor) == 0, "no vendor row exists before an administrator approves"

        pending = (await api_client.get("/api/v1/vendor-requests")).json()["data"]
        assert [(r["id"], r["waiting_invoices"]) for r in pending] == [(request_id, 1)]
        assert pending[0]["waiting"][0]["invoice_id"] == detail["invoice_id"]

        approved = await api_client.post(f"/api/v1/vendor-requests/{request_id}/approve", json={})
        assert approved.status_code == 200, approved.text
        data = approved.json()["data"]
        assert data["status"] == REQUEST_APPROVED and data["vendor_id"]
        after = (await api_client.get(f"/api/v1/invoices/{detail['invoice_id']}")).json()["data"]
        assert after["vendor"]["id"] == data["vendor_id"] and after["vendor_source"] == "operator"
        vendor = await db_session.get(Vendor, uuid.UUID(data["vendor_id"]), populate_existing=True)
        assert vendor.identity_status == "confirmed"

    async def test_a_manager_asks_too_and_only_an_admin_adds_lists_or_decides(self, user, manager, api_client):
        for client in (user, manager):
            assert (await client.post("/api/v1/vendors", json={"name": "Direct Add"})).status_code == 403
            assert (await client.get("/api/v1/vendor-requests")).status_code == 403
        asked = await manager.post("/api/v1/vendor-requests", json={"name": "Some Vendor"})
        assert asked.status_code == 201, asked.text
        request_id = asked.json()["data"]["id"]
        for client in (user, manager):
            assert (await client.post(f"/api/v1/vendor-requests/{request_id}/approve", json={})).status_code == 403
            assert (await client.post(f"/api/v1/vendor-requests/{request_id}/reject", json={"note": "no"})).status_code == 403
        assert (await api_client.get("/api/v1/vendor-requests")).status_code == 200

    async def test_an_admin_adds_a_vendor_directly_and_it_is_listed_at_once(self, api_client, user, db_session):
        added = await api_client.post("/api/v1/vendors", json={"name": "Direct Add Distributing"})
        assert added.status_code in (200, 201), added.text
        options = (await user.get("/api/v1/vendors/options")).json()["data"]
        assert [(o["name"], o["kind"], o["confirmed"]) for o in options] == [("Direct Add Distributing", "vendor", True)]

    async def test_a_confirmed_vendor_cannot_be_added_or_requested_again(self, api_client, manager, db_session):
        await make_vendor(db_session, "T.J. Sheehan")
        for client, path in ((api_client, "/api/v1/vendors"), (manager, "/api/v1/vendor-requests")):
            again = await client.post(path, json={"name": "TJ SHEEHAN DISTRIBUTING INC"})
            assert again.status_code == 422, again.text
            assert again.json()["error"]["detail"].get("reason") == "already_listed"

    async def test_a_vendor_only_extraction_recorded_can_be_asked_for_and_approval_adopts_it(self, api_client, user, db_session):
        recorded = await make_vendor(db_session, "Quartz Beverage Co", confirmed=False)
        asked = await user.post("/api/v1/vendor-requests", json={"name": "QUARTZ BEVERAGE"})
        assert asked.status_code == 201, asked.text
        approved = await api_client.post(f"/api/v1/vendor-requests/{asked.json()['data']['id']}/approve", json={})
        assert approved.json()["data"]["vendor_id"] == str(recorded.id)
        assert await count(db_session, Vendor) == 1, "the recorded row was confirmed, not duplicated"

    async def test_two_people_asking_for_the_same_vendor_share_one_request(self, user, manager, db_session):
        first = await user.post("/api/v1/vendor-requests", json={"name": "Brand New Vendor"})
        second = await manager.post("/api/v1/vendor-requests", json={"name": "BRAND NEW VENDOR INC"})
        assert first.json()["data"]["id"] == second.json()["data"]["id"]
        assert await count(db_session, VendorRequest) == 1

    async def test_a_request_can_be_approved_as_an_existing_vendor_and_a_rejection_needs_a_reason(self, api_client, user, db_session):
        existing = await make_vendor(db_session, "Existing Foods Supply")
        asked = (await user.post("/api/v1/vendor-requests", json={"name": "Brand New Vendor"})).json()["data"]
        approved = await api_client.post(f"/api/v1/vendor-requests/{asked['id']}/approve",
                                         json={"existing_vendor_id": str(existing.id)})
        assert approved.json()["data"]["vendor_id"] == str(existing.id)
        assert await count(db_session, Vendor) == 1, "no duplicate was created"

        other = (await user.post("/api/v1/vendor-requests", json={"name": "Another Vendor"})).json()["data"]
        assert (await api_client.post(f"/api/v1/vendor-requests/{other['id']}/reject", json={})).status_code == 422
        rejected = await api_client.post(f"/api/v1/vendor-requests/{other['id']}/reject", json={"note": "not a supplier"})
        assert rejected.json()["data"]["status"] == REQUEST_REJECTED
        assert (await api_client.post(f"/api/v1/vendor-requests/{other['id']}/approve", json={})).status_code == 422

    async def test_a_rejected_request_cannot_be_used_at_upload(self, api_client, user, db_session, app):
        asked = (await user.post("/api/v1/vendor-requests", json={"name": "Another Vendor"})).json()["data"]
        await api_client.post(f"/api/v1/vendor-requests/{asked['id']}/reject", json={"note": "not a supplier"})
        refused = await upload(user, app, vendor_request_id=asked["id"], delivery_date="2026-10-22")
        assert refused.status_code == 422
        assert (await db_session.execute(select(VendorRequest.status))).scalar() == REQUEST_REJECTED


class TestAPendingVendorAllTheWay:
    """Choose 'all the way': a pending vendor never holds an invoice back; the decision only attaches the vendor afterwards."""

    async def _pending(self, api_client, manager, user, db_session, received):  # noqa: F811
        asked = (await user.post("/api/v1/vendor-requests", json={"name": "Northwind Brewing"})).json()["data"]
        chosen = await manager.patch(f"/api/v1/invoices/{received.invoice_id}/vendor", json={"vendor_request_id": asked["id"]})
        assert chosen.status_code == 200, chosen.text
        assert chosen.json()["data"]["vendor_id"] is None and chosen.json()["data"]["retailed_blocker"] is None
        return asked["id"]

    async def _retail_and_receive(self, api_client, db_session, received):  # noqa: F811
        await approve(api_client, db_session, received.invoice_id, LAGER, 12)
        await approve(api_client, db_session, received.invoice_id, ALE, 6)
        preview = await submit(api_client, received.invoice_id)               # PDI export + Mark Retailed
        assert (await approve_submission(api_client, received.invoice_id, preview)).status_code == 200

    async def test_retailed_and_received_with_a_pending_vendor_then_approval_only_attaches_it(
        self, api_client, manager, user, db_session, received,  # noqa: F811
    ):
        request_id = await self._pending(api_client, manager, user, db_session, received)
        await self._retail_and_receive(api_client, db_session, received)
        before = (await api_client.get(f"/api/v1/invoices/{received.invoice_id}")).json()["data"]
        assert before["completion"]["status"] == "retailed" and before["vendor"] is None
        waiting = (await api_client.get("/api/v1/vendor-requests")).json()["data"][0]["waiting"]
        assert [(w["retailed"], w["received"]) for w in waiting] == [(True, True)]
        ledger = await ledger_counts(db_session)
        lines_before = [(i["description"], i["quantity"]) for i in before["line_items"]]

        assert (await api_client.post(f"/api/v1/vendor-requests/{request_id}/approve", json={})).status_code == 200

        after = (await api_client.get(f"/api/v1/invoices/{received.invoice_id}")).json()["data"]
        assert after["vendor"]["display_name"] == "Northwind Brewing" and after["vendor_source"] == "operator"
        assert after["completion"]["status"] == "retailed", "the attestation does not cover the vendor"
        assert [(i["description"], i["quantity"]) for i in after["line_items"]] == lines_before
        assert await ledger_counts(db_session) == ledger, "the receipt and its movements are untouched"
        logged = (await db_session.execute(select(ProcessingLog.payload).where(
            ProcessingLog.payload["event"].astext == "vendor_request_approved"))).scalars().all()
        assert [p["revalidated"] for p in logged] == [False], "an attested, received invoice is not revalidated"

    async def test_a_pending_vendor_is_counted_by_no_vendor_wise_view(self, api_client, manager, user, db_session, received):  # noqa: F811
        await self._pending(api_client, manager, user, db_session, received)
        await self._retail_and_receive(api_client, db_session, received)
        options = (await api_client.get("/api/v1/inventory/supply/options")).json()["data"]
        assert "Northwind" not in str(options)
        assert not [o for o in (await user.get("/api/v1/vendors/options")).json()["data"] if o["kind"] == "vendor"]
        listed = (await api_client.get("/api/v1/vendors")).json()
        assert "Northwind" not in str(listed["items"])

    async def test_after_a_rejection_the_vendor_is_chosen_even_on_a_retailed_received_invoice(
        self, api_client, manager, user, db_session, received,  # noqa: F811
    ):
        request_id = await self._pending(api_client, manager, user, db_session, received)
        await self._retail_and_receive(api_client, db_session, received)
        assert (await api_client.post(f"/api/v1/vendor-requests/{request_id}/reject",
                                      json={"note": "a typo of an existing vendor"})).status_code == 200
        detail = (await api_client.get(f"/api/v1/invoices/{received.invoice_id}")).json()["data"]
        assert detail["vendor_source"] is None and "vendor list" in detail["declarations_missing"]
        assert detail["vendor_request"]["status"] == REQUEST_REJECTED

        acme = await make_vendor(db_session, "Acme Corp Distributing")
        chosen = await manager.patch(f"/api/v1/invoices/{received.invoice_id}/vendor", json={"vendor_id": str(acme.id)})
        assert chosen.status_code == 200, chosen.text                       # filling a missing vendor is not a change
        after = (await api_client.get(f"/api/v1/invoices/{received.invoice_id}")).json()["data"]
        assert after["vendor"]["id"] == str(acme.id) and after["completion"]["status"] == "retailed"
        other = await make_vendor(db_session, "Zenith Traders")
        changed = await manager.patch(f"/api/v1/invoices/{received.invoice_id}/vendor", json={"vendor_id": str(other.id)})
        assert changed.status_code == 409, "changing a vendor it already has stays locked"


class TestDeclaringAfterTheFact:
    async def _admin_invoice(self, api_client, app):
        return await invoice_of(api_client, await upload(api_client, app))

    async def test_the_delivery_date_and_vendor_are_set_by_a_manager_and_kept_in_history(self, api_client, manager, db_session, app):
        detail = await self._admin_invoice(api_client, app)
        acme = await make_vendor(db_session, "Acme Corp")
        set_date = await manager.patch(f"/api/v1/invoices/{detail['invoice_id']}/delivery-date",
                                       json={"delivery_date": "2026-10-22", "note": "per the driver"})
        assert set_date.status_code == 200, set_date.text
        assert set_date.json()["data"]["delivery_date"] == "2026-10-22"
        assert set_date.json()["data"]["retailed_blocker"], "the vendor is still undeclared"
        chosen = await manager.patch(f"/api/v1/invoices/{detail['invoice_id']}/vendor", json={"vendor_id": str(acme.id)})
        assert chosen.status_code == 200, chosen.text
        assert chosen.json()["data"]["retailed_blocker"] is None
        row = await db_session.get(Invoice, uuid.UUID(detail["invoice_id"]), populate_existing=True)
        assert row.vendor_source == SOURCE_OPERATOR and row.delivery_date.isoformat() == "2026-10-22"
        fields = [e.get("field") for e in row.correction_history]
        assert "delivery_date" in fields and "vendor" in fields

    async def test_a_user_cannot_change_either(self, api_client, user, app):
        detail = await self._admin_invoice(api_client, app)
        assert (await user.patch(f"/api/v1/invoices/{detail['invoice_id']}/delivery-date",
                                 json={"delivery_date": "2026-10-22"})).status_code == 403
        assert (await user.patch(f"/api/v1/invoices/{detail['invoice_id']}/vendor",
                                 json={"vendor_id": str(uuid.uuid4())})).status_code == 403

    async def test_a_future_date_is_refused(self, api_client, manager, app):
        detail = await self._admin_invoice(api_client, app)
        refused = await manager.patch(f"/api/v1/invoices/{detail['invoice_id']}/delivery-date", json={"delivery_date": "2999-01-01"})
        assert refused.status_code == 422

    async def test_the_vendor_body_needs_exactly_one_of_vendor_or_request(self, api_client, manager, app):
        detail = await self._admin_invoice(api_client, app)
        neither = await manager.patch(f"/api/v1/invoices/{detail['invoice_id']}/vendor", json={})
        assert neither.status_code == 422


class TestRetailedNeedsBothDeclarations:
    async def test_retailed_is_refused_until_vendor_and_date_are_declared(self, api_client, manager, db_session, app):
        detail = await invoice_of(api_client, await upload(api_client, app))
        invoice_id = detail["invoice_id"]
        refused = await api_client.post(f"/api/v1/invoices/{invoice_id}/retailed")
        assert refused.status_code == 422
        assert "delivery date" in refused.text and "vendor" in refused.text

        acme = await make_vendor(db_session, "Acme Corp")
        await manager.patch(f"/api/v1/invoices/{invoice_id}/vendor", json={"vendor_id": str(acme.id)})
        await manager.patch(f"/api/v1/invoices/{invoice_id}/delivery-date", json={"delivery_date": "2026-10-22"})
        completion = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]["completion"]
        assert "delivery date" not in (completion["reason"] or "") and "vendor list" not in (completion["reason"] or "")


class TestVendorPicker:
    async def test_the_picker_offers_confirmed_vendors_then_pending_requests_names_only(self, user, db_session):
        await make_vendor(db_session, "Zeta Supply", confirmed=False)                 # only ever extracted: not offered
        await make_vendor(db_session, "Alpha Distributing")
        asked = (await user.post("/api/v1/vendor-requests", json={"name": "Brand New Vendor"})).json()["data"]
        options = (await user.get("/api/v1/vendors/options")).json()["data"]
        assert [(o["name"], o["kind"]) for o in options] == [("Alpha Distributing", "vendor"), ("Brand New Vendor", "request")]
        assert options[1]["id"] == asked["id"]
        assert set(options[0]) == {"id", "name", "kind", "confirmed", "last_used_at"}, "no tax id, address or contact"

    async def test_a_second_invoice_joins_the_pending_request_from_the_picker(self, user, manager, db_session, app):
        asked = (await user.post("/api/v1/vendor-requests", json={"name": "Brand New Vendor"})).json()["data"]
        for client in (user, manager):
            accepted = await upload(client, app, vendor_request_id=asked["id"], delivery_date="2026-10-22")
            assert accepted.status_code == 202, accepted.text
        assert await count(db_session, VendorRequest) == 1 and await count(db_session, Vendor) == 0

    async def test_an_unconfirmed_vendor_cannot_be_chosen_at_upload_or_on_an_invoice(self, api_client, manager, db_session, app):
        recorded = await make_vendor(db_session, "Zeta Supply", confirmed=False)
        refused = await upload(manager, app, vendor_id=recorded.id, delivery_date="2026-10-22")
        assert refused.status_code == 422 and refused.json()["error"]["detail"]["reason"] == "not_confirmed"
        detail = await invoice_of(api_client, await upload(api_client, app))
        chosen = await manager.patch(f"/api/v1/invoices/{detail['invoice_id']}/vendor", json={"vendor_id": str(recorded.id)})
        assert chosen.status_code == 422 and chosen.json()["error"]["detail"]["reason"] == "not_confirmed"


class TestTheReceiptAndTheDeclaredDate:
    async def _mapped(self, api_client, db_session, received):  # noqa: F811
        await approve(api_client, db_session, received.invoice_id, LAGER, 12)
        await approve(api_client, db_session, received.invoice_id, ALE, 6)

    async def test_the_receipt_preview_carries_the_declared_delivery_date(self, api_client, db_session, received):  # noqa: F811
        await self._mapped(api_client, db_session, received)
        preview = await preview_of(api_client, received.invoice_id)
        assert preview["declared_delivery_date"] == "2026-10-09"

    async def test_a_different_received_date_is_allowed_and_logged_not_refused(self, api_client, db_session, received):  # noqa: F811
        await self._mapped(api_client, db_session, received)
        preview = await submit(api_client, received.invoice_id)
        approved = await approve_submission(api_client, received.invoice_id, preview, received_on="2026-10-12")
        assert approved.status_code == 200, approved.text
        logged = (await db_session.execute(text(
            "select payload from processing_logs where payload->>'event' = 'receipt_date_differs_from_declared_delivery'"
        ))).scalars().all()
        assert [(p["received_on"], p["declared_delivery_date"]) for p in logged] == [("2026-10-12", "2026-10-09")]

    async def test_the_same_received_date_logs_no_difference(self, api_client, db_session, received):  # noqa: F811
        await self._mapped(api_client, db_session, received)
        preview = await submit(api_client, received.invoice_id)
        assert (await approve_submission(api_client, received.invoice_id, preview, received_on="2026-10-09")).status_code == 200
        assert (await db_session.execute(text(
            "select count(*) from processing_logs where payload->>'event' = 'receipt_date_differs_from_declared_delivery'"
        ))).scalar() == 0

    async def test_a_posted_receipt_locks_the_delivery_date_and_the_vendor(self, api_client, manager, db_session, received):  # noqa: F811
        await self._mapped(api_client, db_session, received)
        preview = await submit(api_client, received.invoice_id)
        assert (await approve_submission(api_client, received.invoice_id, preview)).status_code == 200
        acme = await make_vendor(db_session, "Acme Corp")
        date_change = await manager.patch(f"/api/v1/invoices/{received.invoice_id}/delivery-date",
                                          json={"delivery_date": "2026-10-10"})
        assert refused_as_received(date_change) == "set_delivery_date"
        vendor_change = await manager.patch(f"/api/v1/invoices/{received.invoice_id}/vendor", json={"vendor_id": str(acme.id)})
        assert refused_as_received(vendor_change) == "choose_vendor"
        row = await db_session.get(Invoice, uuid.UUID(received.invoice_id), populate_existing=True)
        assert row.delivery_date.isoformat() == "2026-10-09", "unchanged"


def other_store(received):
    """A physical fixture store the invoice is not at."""
    return store_id(PHYSICAL_STORE) if received.store_id == store_id(RCM_STORE) else store_id(RCM_STORE)


def retailed_refusal(response, action):
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["error_code"] == "ERR_INVOICE_RETAILED" and error["detail"]["reason"] == "invoice_retailed"
    assert error["detail"]["action"] == action
    return error


class TestChangeableUntilRetailed:
    async def _retailed(self, api_client, db_session, received):
        await approve(api_client, db_session, received.invoice_id, LAGER, 12)
        await approve(api_client, db_session, received.invoice_id, ALE, 6)
        await submit(api_client, received.invoice_id)

    async def test_vendor_delivery_date_and_store_are_refused_once_retailed_and_open_again_after_reopening(
        self, api_client, manager, db_session, received,
    ):
        acme = await make_vendor(db_session, "Acme Corp")
        await self._retailed(api_client, db_session, received)
        base = f"/api/v1/invoices/{received.invoice_id}"
        rcm = str(other_store(received))

        retailed_refusal(await manager.patch(f"{base}/vendor", json={"vendor_id": str(acme.id)}), "choose_vendor")
        retailed_refusal(await manager.patch(f"{base}/delivery-date", json={"delivery_date": "2026-10-10"}), "set_delivery_date")
        retailed_refusal(await manager.post(f"{base}/change-store", json={"store_id": rcm}), "change_store")
        row = await db_session.get(Invoice, uuid.UUID(received.invoice_id), populate_existing=True)
        assert row.store_id == received.store_id and row.delivery_date.isoformat() == "2026-10-09"

        assert (await manager.post(f"{base}/retailed/reopen", json={})).status_code == 200
        assert (await manager.patch(f"{base}/delivery-date", json={"delivery_date": "2026-10-10"})).status_code == 200
        assert (await manager.patch(f"{base}/vendor", json={"vendor_id": str(acme.id)})).status_code == 200
        assert (await manager.post(f"{base}/change-store", json={"store_id": rcm})).status_code == 200

    async def test_validated_alone_does_not_lock_them(self, api_client, manager, db_session, received):
        detail = (await api_client.get(f"/api/v1/invoices/{received.invoice_id}")).json()["data"]
        assert detail["status"] == "VALIDATED" and detail["completion"]["status"] == "not_retailed"
        acme = await make_vendor(db_session, "Acme Corp")
        assert (await manager.patch(f"/api/v1/invoices/{received.invoice_id}/vendor", json={"vendor_id": str(acme.id)})).status_code == 200


class TestChangingTheStore:
    async def _log(self, db_session, event):
        rows = (await db_session.execute(select(ProcessingLog.payload).where(
            ProcessingLog.payload["event"].astext == event))).scalars().all()
        return list(rows)

    async def test_a_manager_moves_the_invoice_and_the_move_is_recorded(self, api_client, manager, db_session, received):
        rcm = other_store(received)
        moved = await manager.post(f"/api/v1/invoices/{received.invoice_id}/change-store",
                                   json={"store_id": str(rcm), "note": "photographed at the wrong store"})
        assert moved.status_code == 200, moved.text
        data = moved.json()["data"]
        assert data["store_id"] == str(rcm) and data["previous_store_id"] == str(received.store_id)
        row = await db_session.get(Invoice, uuid.UUID(received.invoice_id), populate_existing=True)
        doc = await db_session.get(Document, row.document_id, populate_existing=True)
        assert row.store_id == rcm and doc.store_id == rcm
        [entry] = await self._log(db_session, "store_changed")
        assert (entry["to_store_id"], entry["from_store_id"], entry["by"], entry["note"]) == (
            str(rcm), str(received.store_id), MANAGER_USERNAME, "photographed at the wrong store")
        detail = (await api_client.get(f"/api/v1/invoices/{received.invoice_id}")).json()["data"]
        assert detail["store"]["id"] == str(rcm)

    async def test_a_user_cannot_and_the_rules_hold(self, api_client, user, manager, db_session, received):
        base = f"/api/v1/invoices/{received.invoice_id}/change-store"
        rcm = str(other_store(received))
        assert (await user.post(base, json={"store_id": rcm})).status_code == 403
        same = await manager.post(base, json={"store_id": str(received.store_id)})
        assert same.status_code == 422 and same.json()["error"]["detail"]["reason"] == "same_store"
        source = await manager.post(base, json={"store_id": str(store_id(SOURCE_ONLY_STORE))})
        assert source.status_code == 422 and source.json()["error"]["detail"]["reason"] == "source_identity"
        unknown = await manager.post(base, json={"store_id": str(uuid.uuid4())})
        assert unknown.status_code == 404
        row = await db_session.get(Invoice, uuid.UUID(received.invoice_id), populate_existing=True)
        assert row.store_id == received.store_id, "nothing moved"

    async def test_pending_proposals_from_the_invoice_block_the_move_until_decided(self, api_client, manager, db_session, received):
        base = f"/api/v1/invoices/{received.invoice_id}"
        proposed = await api_client.post(f"{base}/case-mappings",
                                         json={"mappings": [{"item_code": LAGER, "units_per_case": 12}]})
        assert proposed.status_code == 200, proposed.text
        blocked = await manager.post(f"{base}/change-store", json={"store_id": str(other_store(received))})
        assert blocked.status_code == 422, blocked.text
        assert blocked.json()["error"]["detail"] == {"invoice_id": received.invoice_id, "reason": "pending_proposals",
                                                     "pending_proposals": 1}
        await approve_all_pending(db_session)
        moved = await manager.post(f"{base}/change-store", json={"store_id": str(other_store(received))})
        assert moved.status_code == 200, moved.text
        assert moved.json()["data"]["decided_proposals_left_at_old_store"] == 1
        stays = (await db_session.execute(select(ProductDataProposal.store_id).where(
            ProductDataProposal.invoice_id == uuid.UUID(received.invoice_id)))).scalars().all()
        assert stays == [received.store_id], "a decided proposal stays at the store it was made for"

    async def test_export_files_already_generated_are_reported(self, api_client, manager, db_session, received):
        await approve(api_client, db_session, received.invoice_id, LAGER, 12)
        await approve(api_client, db_session, received.invoice_id, ALE, 6)
        assert (await api_client.get(f"/api/v1/invoices/{received.invoice_id}/export", params={"format": "pdi"})).status_code == 200
        moved = await manager.post(f"/api/v1/invoices/{received.invoice_id}/change-store",
                                   json={"store_id": str(other_store(received))})
        assert moved.status_code == 200, moved.text
        assert moved.json()["data"]["export_files_for_old_store"] == 1

    async def test_a_posted_receipt_still_refuses_the_move(self, api_client, manager, db_session, received):
        await approve(api_client, db_session, received.invoice_id, LAGER, 12)
        await approve(api_client, db_session, received.invoice_id, ALE, 6)
        preview = await submit(api_client, received.invoice_id)
        assert (await approve_submission(api_client, received.invoice_id, preview)).status_code == 200
        refused = await manager.post(f"/api/v1/invoices/{received.invoice_id}/change-store",
                                     json={"store_id": str(other_store(received))})
        assert refused_as_received(refused) == "change_store"
