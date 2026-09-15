"""
tests/integration/test_store_identification.py — which store an upload
is for is settled by a person, helped by what the document says.

UPLOAD → text extraction → store identification → (a person confirms)
→ structuring → validation → persistence.

Pinned: there is no default store and nothing falls back to one; a
document that names no store, or two, or a store other than the one
the operator chose, waits for a person; a confirmed store is written
to the document and the invoice and drives every later lookup; the
identification is exact — nothing is inferred from a vendor, a
similar-looking street or a number that resembles another.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.core.config import get_settings
from app.models.document import Document
from app.models.invoice import Invoice
from app.models.processing_log import ProcessingLog
from app.models.store import (
    IDENTITY_CONFIRMED,
    SOURCE_DOCUMENT,
    SOURCE_ITEM_SALES,
    TYPE_CUSTOMER_NAME,
    TYPE_STORE_CODE,
)
from app.repositories.store_repository import StoreRepository
from app.services.pipeline_service import InvoiceProcessingPipeline
from app.services.store_identification_service import match_stores
from tests.integration.conftest import requires_db, store_id
from tests.integration.fakes import FakeStructuring, extracted_invoice
from tests.integration.test_api_db import (  # noqa: F401 — fixture reuse
    INVOICE_PDF,
    api_client,
    process_file,
)
from tests.pdf_builder import build_pdf

pytestmark = requires_db

A, B = "47708760", "86357232"


async def _count(db_session, model) -> int:
    return (await db_session.execute(select(func.count()).select_from(model))).scalar_one()


async def _name_store(db_session, code, display_name, customer_name, street, postal, confirm=True,
                      alias=True):
    repo = StoreRepository(db_session)
    store = await repo.get(store_id(code))
    store.display_name, store.customer_name = display_name, customer_name
    store.address_line_1, store.city, store.state, store.postal_code = street, "Syracuse", "NY", postal
    store.identity_status = IDENTITY_CONFIRMED if confirm else store.identity_status
    if alias:
        await repo.add_identifier(store, SOURCE_DOCUMENT, TYPE_CUSTOMER_NAME, customer_name.upper(),
                                  evidence={"verified": confirm})
    await db_session.commit()
    return store


def _pdf(text: str) -> bytes:
    return build_pdf([text + " pad " * 300])


async def _upload(api_client, app, text, *, store=None, filename="doc.pdf", items=None):  # noqa: F811
    from app.api.v1.invoices import get_pipeline

    app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
        structuring_service=FakeStructuring(extracted_invoice())
    )
    data = {"store_id": str(store_id(store))} if store else {}
    response = await api_client.post(
        "/api/v1/invoices/process",
        files={"file": (filename, _pdf(text), "application/pdf")},
        data=data,
    )
    assert response.status_code == 202, response.text
    accepted = response.json()["data"]
    return (await api_client.get(accepted["status_url"])).json()["data"]


class TestNoDefaultStore:
    async def test_settings_store_number_is_read_nowhere_on_the_invoice_path(self):
        import inspect

        from app.api.v1 import documents, invoices
        from app.services import (
            case_mapping_service,
            persistence_service,
            pipeline_service,
            proposal_service,
            store_identification_service,
            store_reference_service,
        )
        for module in (invoices, documents, pipeline_service, persistence_service,
                       proposal_service, store_reference_service, store_identification_service,
                       case_mapping_service):
            source = inspect.getsource(module)
            assert "store_number" not in source.replace("source_store_code", ""), module.__name__
        assert get_settings().store_number                   # still exists, for nothing on this path

    async def test_an_unknown_store_id_is_refused_before_anything_is_stored(self, api_client, db_session):  # noqa: F811
        response = await api_client.post(
            "/api/v1/invoices/process",
            files={"file": ("acme-invoice.pdf", INVOICE_PDF, "application/pdf")},
            data={"store_id": str(uuid.uuid4())},
        )
        assert response.status_code == 422
        assert response.json()["error"]["detail"]["reason"] == "unknown"
        assert await _count(db_session, Document) == 0
        assert await _count(db_session, ProcessingLog) == 0

    async def test_a_malformed_store_id_is_refused(self, api_client, db_session):  # noqa: F811
        response = await api_client.post(
            "/api/v1/invoices/process",
            files={"file": ("acme-invoice.pdf", INVOICE_PDF, "application/pdf")},
            data={"store_id": "47708760"},                        # a source code is not a store id
        )
        assert response.status_code == 422
        assert response.json()["error"]["detail"]["reason"] == "invalid"
        assert await _count(db_session, Document) == 0


class TestIdentificationIsExact:
    def test_matches_only_verbatim_identifiers_and_names(self, db_session):
        # pure function, no I/O: build stores by hand
        from app.models.store import Store, StoreIdentifier

        apple = Store(id=uuid.uuid4(), display_name="Apple Foods II", customer_name="PB Wolf Group Inc",
                      address_line_1="800 Wolf St", city="Syracuse", state="NY", postal_code="13208-1224",
                      identity_status="confirmed", identifiers=[])
        apple.identifiers.append(StoreIdentifier(store=apple, source_system=SOURCE_ITEM_SALES,
                                                 identifier_type=TYPE_STORE_CODE, identifier_value="47708760"))
        other = Store(id=uuid.uuid4(), display_name="Wolf Street Market", address_line_1="810 Wolf St",
                      city="Syracuse", state="NY", postal_code="13208", identity_status="confirmed",
                      identifiers=[])

        text = "SOLD TO: PB WOLF GROUP INC\nAPPLE FOODS II\n800 WOLF ST\nSYRACUSE, NY 13208-1224\nINVOICE 228245"
        [c] = match_stores(text, [apple, other])
        assert c.store_id == str(apple.id)
        kinds = {h["kind"] for h in c.matched_on}
        assert {"display_name", "customer_name", "address"} <= kinds

        # a similar street, a shared city, a vendor name: none of it matches
        assert match_stores("810 WOLF STREET SYRACUSE NY 13208 ACME CORP", [apple]) == []
        assert match_stores("Invoice 47708761 account 4770876", [apple]) == []   # resembles, is not
        # the bare store code printed verbatim does match — a source identifier is evidence
        [c2] = match_stores("Store: 47708760", [apple, other])
        assert c2.store_id == str(apple.id) and c2.matched_on[0]["kind"] == "store_code"
        # a street without the postal code is not enough
        assert match_stores("800 WOLF ST", [apple]) == []

    def test_unverified_document_evidence_is_offered_but_marked(self):
        from app.models.store import Store, StoreIdentifier

        s = Store(id=uuid.uuid4(), display_name="Apple Foods II", identity_status="unresolved", identifiers=[])
        s.identifiers.append(StoreIdentifier(store=s, source_system=SOURCE_DOCUMENT, identifier_type=TYPE_CUSTOMER_NAME,
                                             identifier_value="PB WOLF GROUP INC", evidence={"verified": False}))
        [c] = match_stores("Bill to PB Wolf Group Inc.", [s])
        assert c.identity_status == "unresolved"
        assert all(h["verified"] is False for h in c.matched_on)


class TestTheRunWaitsForAPerson:
    async def test_no_store_and_no_match_waits_for_manual_identification(self, api_client, app, db_session):  # noqa: F811
        status = await _upload(api_client, app, "ACME CORP invoice with nothing that names a store")
        assert status["status"] == "STORE_CONFIRMATION_REQUIRED"
        assert status["awaiting_store_confirmation"] is True
        assert status["is_terminal"] is False
        assert status["store"] is None and status["store_candidates"] == []
        assert status["invoice_id"] is None                      # nothing persisted, no store
        assert await _count(db_session, Invoice) == 0
        stages = [s["stage"] for s in status["stages"]]
        assert stages == ["UPLOAD", "TEXT_EXTRACTION", "STORE_IDENTIFICATION"]

    async def test_one_match_still_requires_confirmation(self, api_client, app, db_session):  # noqa: F811
        await _name_store(db_session, A, "Apple Foods II", "PB Wolf Group Inc", "800 Wolf St", "13208-1224")
        status = await _upload(api_client, app, "SOLD TO PB WOLF GROUP INC 800 WOLF ST SYRACUSE NY 13208")
        assert status["status"] == "STORE_CONFIRMATION_REQUIRED"
        [candidate] = status["store_candidates"]
        assert candidate["store_id"] == str(store_id(A))
        assert candidate["label"] == "Apple Foods II"
        assert status["store"] is None                           # offered, not decided
        assert await _count(db_session, Invoice) == 0

    async def test_two_matches_require_selection(self, api_client, app, db_session):  # noqa: F811
        # Two locations billed under one customer name: the name is on both
        # store records (an identifier may name only one store per source).
        await _name_store(db_session, A, "Apple Foods II", "PB Wolf Group Inc", "800 Wolf St", "13208-1224")
        await _name_store(db_session, B, "Apple Foods III", "PB Wolf Group Inc", "900 Elm St", "13210", alias=False)
        status = await _upload(api_client, app, "SOLD TO PB WOLF GROUP INC")
        assert status["status"] == "STORE_CONFIRMATION_REQUIRED"
        assert {c["store_id"] for c in status["store_candidates"]} == {str(store_id(A)), str(store_id(B))}

    async def test_an_operator_choice_that_contradicts_the_document_waits(self, api_client, app, db_session):  # noqa: F811
        await _name_store(db_session, A, "Apple Foods II", "PB Wolf Group Inc", "800 Wolf St", "13208-1224")
        status = await _upload(api_client, app, "SOLD TO PB WOLF GROUP INC 800 WOLF ST 13208", store=B)
        assert status["status"] == "STORE_CONFIRMATION_REQUIRED"
        assert [c["store_id"] for c in status["store_candidates"]] == [str(store_id(A))]
        assert status["store"]["id"] == str(store_id(B))          # what they chose is shown, not applied
        assert await _count(db_session, Invoice) == 0

    async def test_an_operator_choice_the_document_agrees_with_proceeds(self, api_client, app, db_session):  # noqa: F811
        await _name_store(db_session, A, "Apple Foods II", "PB Wolf Group Inc", "800 Wolf St", "13208-1224")
        status = await _upload(api_client, app, "SOLD TO PB WOLF GROUP INC 800 WOLF ST 13208", store=A)
        assert status["status"] == "COMPLETED"
        assert status["store"]["id"] == str(store_id(A))
        invoice = (await db_session.execute(select(Invoice))).scalar_one()
        assert invoice.store_id == store_id(A)

    async def test_an_operator_choice_with_a_silent_document_proceeds(self, api_client, app, db_session):  # noqa: F811
        status = await _upload(api_client, app, "ACME CORP nothing here names a store", store=B)
        assert status["status"] == "COMPLETED"
        invoice = (await db_session.execute(select(Invoice))).scalar_one()
        assert invoice.store_id == store_id(B)


class TestConfirmingFinishesTheRun:
    async def test_confirmation_persists_the_store_and_completes_without_re_extracting(
        self, api_client, app, db_session  # noqa: F811
    ):
        await _name_store(db_session, A, "Apple Foods II", "PB Wolf Group Inc", "800 Wolf St", "13208-1224")
        status = await _upload(api_client, app, "SOLD TO PB WOLF GROUP INC 800 WOLF ST 13208")
        assert status["status"] == "STORE_CONFIRMATION_REQUIRED"

        r = await api_client.post(f"/api/v1/documents/{status['document_id']}/confirm-store",
                                  json={"store_id": str(store_id(A)), "confirmed_by": "ops:shashwat"})
        assert r.status_code == 200, r.text
        final = (await api_client.get(f"/api/v1/documents/{status['document_id']}")).json()["data"]
        assert final["status"] == "COMPLETED" and final["invoice_id"]
        assert final["store"]["id"] == str(store_id(A))
        stages = [s["stage"] for s in final["stages"]]
        assert stages == ["UPLOAD", "TEXT_EXTRACTION", "STORE_IDENTIFICATION", "STORE_IDENTIFICATION",
                          "AI_STRUCTURING", "VALIDATION", "PERSISTENCE"]      # extraction ran once
        invoice = (await db_session.execute(select(Invoice))).scalar_one()
        assert invoice.store_id == store_id(A)
        document = (await db_session.execute(select(Document))).scalar_one()
        assert document.store_id == store_id(A)
        detail = (await api_client.get(f"/api/v1/invoices/{invoice.id}")).json()["data"]
        assert detail["store"]["label"] == "Apple Foods II"

    async def test_a_person_may_choose_a_store_that_was_not_a_candidate(self, api_client, app, db_session):  # noqa: F811
        status = await _upload(api_client, app, "nothing names a store")
        r = await api_client.post(f"/api/v1/documents/{status['document_id']}/confirm-store",
                                  json={"store_id": str(store_id(B)), "confirmed_by": "ops"})
        assert r.status_code == 200
        final = (await api_client.get(f"/api/v1/documents/{status['document_id']}")).json()["data"]
        assert final["status"] == "COMPLETED" and final["store"]["source_codes"] == [B]
        with_payloads = (await api_client.get(f"/api/v1/documents/{status['document_id']}",
                                              params={"include_payloads": "true"})).json()["data"]
        confirmation = [s for s in with_payloads["stages"] if s["stage"] == "STORE_IDENTIFICATION"][-1]
        assert confirmation["payload"]["was_a_candidate"] is False
        assert confirmation["payload"]["confirmed_by"] == "ops"

    async def test_confirming_an_unknown_store_or_a_document_not_waiting_is_refused(self, api_client, app, db_session):  # noqa: F811
        status = await _upload(api_client, app, "nothing names a store")
        r = await api_client.post(f"/api/v1/documents/{status['document_id']}/confirm-store",
                                  json={"store_id": str(uuid.uuid4())})
        assert r.status_code == 422
        done = await _upload(api_client, app, "another silent document", store=A, filename="done.pdf")
        r = await api_client.post(f"/api/v1/documents/{done['document_id']}/confirm-store",
                                  json={"store_id": str(store_id(B))})
        assert r.status_code == 422
        assert "not waiting" in r.json()["error"]["message"]


class TestStoreDirectory:
    async def test_unresolved_stores_show_their_source_code_not_a_name(self, api_client):  # noqa: F811
        stores = (await api_client.get("/api/v1/stores")).json()["data"]
        by_code = {s["source_codes"][0]: s for s in stores}
        assert by_code[A]["identity_status"] == "unresolved"
        assert by_code[A]["display_name"] is None
        assert by_code[A]["label"] == f"Store {A} (location not yet confirmed)"
        assert [i["identifier_value"] for i in by_code[A]["identifiers"]] == [A]

    async def test_confirming_an_identity_is_explicit_and_attributed(self, api_client, db_session):  # noqa: F811
        sid = store_id(A)
        r = await api_client.patch(f"/api/v1/stores/{sid}", json={
            "display_name": "Apple Foods II", "customer_name": "PB Wolf Group Inc",
            "address_line_1": "800 Wolf St", "city": "Syracuse", "state": "NY", "postal_code": "13208-1224",
            "confirm": True, "confirmed_by": "ops:shashwat",
        })
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["identity_status"] == "confirmed" and d["label"] == "Apple Foods II"
        assert d["address"] == "800 Wolf St, Syracuse, NY 13208-1224"
        assert "confirmed by ops:shashwat" in d["notes"]
        assert d["source_codes"] == [A]                          # the code stays attached
        # confirming needs a name and a person
        assert (await api_client.patch(f"/api/v1/stores/{store_id(B)}", json={"confirm": True})).status_code == 422
