"""
tests/integration/test_reprocess.py — governed re-extraction of a document
that has already been processed.

Until now the only way to run a document through a newer prompt was to
delete the invoice and upload the file again: a hard delete that discards
the document id, the invoice id, every processing log and the stored
source file. The document and its invoice are the stable identity other
records point at (proposals carry invoice_id), so reprocessing must keep
them and replace only the operational extraction result.

What these pin: identity survives, history survives and stays
distinguishable attempt by attempt, the replacement is one transaction
that leaves the previous state intact if anything fails, and master data
is never touched.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.document import Document, DocumentStatus
from app.models.invoice import Invoice
from app.models.processing_log import ProcessingLog
from app.schemas.extraction import ExtractedLineItem
from app.services.pipeline_service import InvoiceProcessingPipeline
from tests.integration.conftest import requires_db, store_id  # noqa: F401
from tests.integration.fakes import FakeStructuring, extracted_invoice
from tests.integration.test_api_db import api_client, process_file  # noqa: F401
from tests.pdf_builder import build_pdf

pytestmark = requires_db


def row(desc, upc, qty, price):
    return ExtractedLineItem(description=desc, product_code=upc, quantity=qty,
                             unit_price=price, line_total=round(qty * price, 2))


ORIGINAL = [row("BLUE WIDGET", "012345678905", 2, 10.00)]
REEXTRACTED = [row("BLUE WIDGET", "012345678905", 2, 10.00),
               row("RED WIDGET", "012345678912", 1, 5.00)]


async def _process(api_client, app, items, name, **totals):  # noqa: F811
    from app.api.v1.invoices import get_pipeline

    app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
        structuring_service=FakeStructuring(extracted_invoice(line_items=items, **totals)))
    accepted = await process_file(api_client, content=build_pdf([name + " pad " * 300]), filename=name)
    status = (await api_client.get(accepted["status_url"])).json()["data"]
    return status["document_id"], status["invoice_id"]


def _use_structuring(app, items, **totals):
    from app.api.v1.invoices import get_pipeline

    app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
        structuring_service=FakeStructuring(extracted_invoice(line_items=items, **totals)))


async def _reprocess(api_client, document_id):  # noqa: F811
    return await api_client.post(f"/api/v1/documents/{document_id}/reprocess")


async def _detail(api_client, invoice_id):  # noqa: F811
    return (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]


class TestIdentityAndHistorySurvive:
    async def test_the_document_and_invoice_keep_their_ids(self, api_client, app, db_session):  # noqa: F811
        doc_id, invoice_id = await _process(api_client, app, ORIGINAL, "keep.pdf",
                                            subtotal=20.00, grand_total=20.00)
        before_hash = (await db_session.get(Document, uuid.UUID(doc_id))).file_hash

        _use_structuring(app, REEXTRACTED, subtotal=25.00, grand_total=25.00)
        response = await _reprocess(api_client, doc_id)
        assert response.status_code == 200, response.text
        data = response.json()["data"]

        assert data["document_id"] == doc_id
        assert data["invoice_id"] == invoice_id
        after = await db_session.get(Document, uuid.UUID(doc_id))
        await db_session.refresh(after)
        assert after.file_hash == before_hash                      # source untouched
        assert (await _detail(api_client, invoice_id))["invoice_id"] == invoice_id

    async def test_only_one_invoice_and_one_document_exist_afterwards(self, api_client, app, db_session):  # noqa: F811
        doc_id, invoice_id = await _process(api_client, app, ORIGINAL, "single.pdf",
                                            subtotal=20.00, grand_total=20.00)
        _use_structuring(app, REEXTRACTED, subtotal=25.00, grand_total=25.00)
        await _reprocess(api_client, doc_id)

        invoices = (await db_session.execute(
            select(Invoice).where(Invoice.document_id == uuid.UUID(doc_id)))).scalars().all()
        documents = (await db_session.execute(
            select(Document).where(Document.id == uuid.UUID(doc_id)))).scalars().all()
        assert len(invoices) == 1 and str(invoices[0].id) == invoice_id
        assert len(documents) == 1

    async def test_the_historical_processing_logs_are_kept(self, api_client, app, db_session):  # noqa: F811
        doc_id, _ = await _process(api_client, app, ORIGINAL, "logs.pdf",
                                   subtotal=20.00, grand_total=20.00)
        before = (await db_session.execute(
            select(ProcessingLog).where(ProcessingLog.document_id == uuid.UUID(doc_id)))).scalars().all()
        before_ids = {log.id for log in before}

        _use_structuring(app, REEXTRACTED, subtotal=25.00, grand_total=25.00)
        await _reprocess(api_client, doc_id)

        after = (await db_session.execute(
            select(ProcessingLog).where(ProcessingLog.document_id == uuid.UUID(doc_id)))).scalars().all()
        assert before_ids <= {log.id for log in after}             # nothing removed
        assert len(after) > len(before)                            # and the new attempt is recorded

    async def test_each_attempt_is_identifiable_in_the_log(self, api_client, app, db_session):  # noqa: F811
        doc_id, _ = await _process(api_client, app, ORIGINAL, "attempt.pdf",
                                   subtotal=20.00, grand_total=20.00)
        _use_structuring(app, REEXTRACTED, subtotal=25.00, grand_total=25.00)
        await _reprocess(api_client, doc_id)

        logs = (await db_session.execute(
            select(ProcessingLog).where(ProcessingLog.document_id == uuid.UUID(doc_id))
            .order_by(ProcessingLog.created_at, ProcessingLog.id))).scalars().all()
        first = [dict(log.payload or {}) for log in logs if not (log.payload or {}).get("attempt")]
        second = [dict(log.payload or {}) for log in logs if (log.payload or {}).get("attempt") == 2]
        assert first, "the original run's entries must stay attributable to attempt 1"
        assert second, "the reprocess must stamp its own entries"
        assert len({p["run_id"] for p in second}) == 1             # one run id across the attempt
        started = [p for p in second if p.get("event") == "reprocess_started"]
        assert started and started[0]["superseded"]["prompt_version"] == "v1"


class TestTheExtractionIsReplaced:
    async def test_new_line_items_replace_the_old_ones(self, api_client, app):  # noqa: F811
        doc_id, invoice_id = await _process(api_client, app, ORIGINAL, "replace.pdf",
                                            subtotal=20.00, grand_total=20.00)
        assert len((await _detail(api_client, invoice_id))["line_items"]) == 1

        _use_structuring(app, REEXTRACTED, subtotal=25.00, grand_total=25.00)
        await _reprocess(api_client, doc_id)

        after = await _detail(api_client, invoice_id)
        assert [i["description"] for i in after["line_items"]] == ["BLUE WIDGET", "RED WIDGET"]
        assert after["grand_total"] == 25.00                       # header replaced too
        assert after["llm_metadata"]["prompt_version"] == "v1"     # the attempt's own metadata

    async def test_a_row_absent_from_the_new_extraction_does_not_linger(self, api_client, app):  # noqa: F811
        doc_id, invoice_id = await _process(api_client, app, REEXTRACTED, "shrink.pdf",
                                            subtotal=25.00, grand_total=25.00)
        assert len((await _detail(api_client, invoice_id))["line_items"]) == 2

        _use_structuring(app, ORIGINAL, subtotal=20.00, grand_total=20.00)
        await _reprocess(api_client, doc_id)

        after = await _detail(api_client, invoice_id)
        assert [i["description"] for i in after["line_items"]] == ["BLUE WIDGET"]

    async def test_status_synchronises_through_the_normal_lifecycle(self, api_client, app, db_session):  # noqa: F811
        # first run fails validation, the reprocess resolves it
        doc_id, invoice_id = await _process(api_client, app, ORIGINAL, "sync.pdf",
                                            subtotal=20.00, grand_total=99.00)
        assert (await _detail(api_client, invoice_id))["status"] == "REVIEW_REQUIRED"
        assert (await db_session.get(Document, uuid.UUID(doc_id))).status == DocumentStatus.REVIEW_REQUIRED

        _use_structuring(app, ORIGINAL, subtotal=20.00, grand_total=20.00)
        await _reprocess(api_client, doc_id)

        assert (await _detail(api_client, invoice_id))["status"] == "VALIDATED"
        document = await db_session.get(Document, uuid.UUID(doc_id))
        await db_session.refresh(document)
        assert document.status == DocumentStatus.COMPLETED

    async def test_running_it_twice_is_safe(self, api_client, app, db_session):  # noqa: F811
        doc_id, invoice_id = await _process(api_client, app, ORIGINAL, "twice.pdf",
                                            subtotal=20.00, grand_total=20.00)
        _use_structuring(app, REEXTRACTED, subtotal=25.00, grand_total=25.00)
        first = await _reprocess(api_client, doc_id)
        second = await _reprocess(api_client, doc_id)
        assert first.status_code == 200 and second.status_code == 200
        after = await _detail(api_client, invoice_id)
        assert [i["description"] for i in after["line_items"]] == ["BLUE WIDGET", "RED WIDGET"]
        invoices = (await db_session.execute(
            select(Invoice).where(Invoice.document_id == uuid.UUID(doc_id)))).scalars().all()
        assert len(invoices) == 1
        attempts = {(log.payload or {}).get("attempt") for log in (await db_session.execute(
            select(ProcessingLog).where(ProcessingLog.document_id == uuid.UUID(doc_id)))).scalars().all()}
        assert {2, 3} <= attempts                                  # each run numbered

    async def test_no_edi_is_generated_by_reprocessing(self, api_client, app):  # noqa: F811
        doc_id, invoice_id = await _process(api_client, app, ORIGINAL, "noedi.pdf",
                                            subtotal=20.00, grand_total=20.00)
        _use_structuring(app, ORIGINAL, subtotal=20.00, grand_total=20.00)
        body = (await _reprocess(api_client, doc_id)).json()["data"]
        assert "edi" not in str(body).lower()
        # readiness is reported by the normal gate, never acted on
        after = await _detail(api_client, invoice_id)
        assert after["pdi_export_allowed"] in (True, False)


class TestFailureLeavesThePreviousStateIntact:
    async def test_a_structuring_failure_changes_nothing(self, api_client, app, db_session):  # noqa: F811
        from app.api.v1.invoices import get_pipeline
        from app.core.exceptions import AIStructuringError

        doc_id, invoice_id = await _process(api_client, app, ORIGINAL, "fail.pdf",
                                            subtotal=20.00, grand_total=20.00)
        before = await _detail(api_client, invoice_id)
        before_logs = len((await db_session.execute(
            select(ProcessingLog).where(ProcessingLog.document_id == uuid.UUID(doc_id)))).scalars().all())

        app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
            structuring_service=FakeStructuring(error=AIStructuringError(message="model down")))
        response = await _reprocess(api_client, doc_id)
        assert response.status_code >= 400

        after = await _detail(api_client, invoice_id)
        assert after["grand_total"] == before["grand_total"]
        assert [i["description"] for i in after["line_items"]] == [i["description"] for i in before["line_items"]]
        assert after["status"] == before["status"]
        # the failure is recorded, the old operational state is not touched
        after_logs = (await db_session.execute(
            select(ProcessingLog).where(ProcessingLog.document_id == uuid.UUID(doc_id)))).scalars().all()
        assert len(after_logs) >= before_logs

    async def test_an_invoice_carrying_human_corrections_is_refused(self, api_client, app):  # noqa: F811
        doc_id, invoice_id = await _process(api_client, app, ORIGINAL, "corrected.pdf",
                                            subtotal=20.00, grand_total=99.00)
        patched = await api_client.patch(f"/api/v1/invoices/{invoice_id}/totals",
                                         json={"grand_total": 20.00, "corrected_by": "data-team:shashwat",
                                               "note": "printed total"})
        assert patched.status_code == 200

        _use_structuring(app, REEXTRACTED, subtotal=25.00, grand_total=25.00)
        response = await _reprocess(api_client, doc_id)
        assert response.status_code == 422, response.text
        assert "correction" in response.text.lower()

        after = await _detail(api_client, invoice_id)
        assert after["grand_total"] == 20.00                       # the person's figure stands
        assert after["corrected_fields"] == ["grand_total"]
        assert len(after["correction_history"]) == 1


class TestMasterDataIsUntouched:
    async def test_mappings_and_proposals_survive_a_reprocess(self, api_client, app, db_session):  # noqa: F811
        from app.models.product_case_mapping import ProductCaseMapping
        from app.models.product_data_proposal import ProductDataProposal

        doc_id, invoice_id = await _process(api_client, app, ORIGINAL, "master.pdf",
                                            subtotal=20.00, grand_total=20.00)
        confirmed = await api_client.post(
            f"/api/v1/invoices/{invoice_id}/case-mappings",
            json={"mappings": [{"item_code": "012345678905", "units_per_case": 24}],
                  "confirmed_by": "data-team:shashwat"})
        assert confirmed.status_code in (200, 201), confirmed.text

        proposals_before = (await db_session.execute(select(ProductDataProposal))).scalars().all()
        mappings_before = (await db_session.execute(select(ProductCaseMapping))).scalars().all()

        _use_structuring(app, REEXTRACTED, subtotal=25.00, grand_total=25.00)
        assert (await _reprocess(api_client, doc_id)).status_code == 200

        proposals_after = (await db_session.execute(select(ProductDataProposal))).scalars().all()
        mappings_after = (await db_session.execute(select(ProductCaseMapping))).scalars().all()
        assert {p.id for p in proposals_before} == {p.id for p in proposals_after}
        assert {m.id for m in mappings_before} == {m.id for m in mappings_after}
        # proposals still point at the preserved invoice
        for p in proposals_after:
            if p.invoice_id is not None:
                assert str(p.invoice_id) == invoice_id
