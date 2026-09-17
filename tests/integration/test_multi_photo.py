"""
tests/integration/test_multi_photo.py — one invoice, several overlapping photos.

A person photographing a long invoice takes overlapping shots. The
system must treat them as ONE intake: each photo OCR'd on its own, the
texts combined under photo headers, ONE invoice extracted with each
physical row exactly once and its photo provenance kept. The model does
the reconciling; deterministic code never merges or drops a row — it
only refuses to pass an invoice whose totals or flags say something is
still wrong, and records what a reviewer decides.

OCR and the model are faked (the real dependency here is the database
and the pipeline wiring). The fakes reproduce what each stage is
responsible for: the OCR fake answers per photo; the model fakes answer
the way v6 instructs — reconciled, or flagged when unsure — and one of
them deliberately answers naively (every row from every photo) to prove
the totals check catches it.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.product_data_proposal import STATUS_APPROVED, STATUS_PENDING, STATUS_REJECTED
from app.repositories.document_repository import DocumentRepository
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.product_case_mapping_repository import ProductCaseMappingRepository
from app.repositories.product_data_proposal_repository import ProductDataProposalRepository
from app.schemas.extraction import ExtractedLineItem
from app.services.export_service import pdi_items
from app.services.ocr.base import OCRResult
from app.services.photo_context import PhotoOCR, combine_photos, photo_header
from app.services.pipeline_service import InvoiceProcessingPipeline
from tests.integration.conftest import requires_db, store_id
from tests.integration.fakes import FakeStructuring, extracted_invoice
from tests.integration.test_api_db import api_client  # noqa: F401 — fixture reuse

pytestmark = requires_db

STORE = "47708760"
PRICE = 10.0


def upc(n: int) -> str:
    """A distinct printed UPC per row number, with check digit."""
    return f"0182000{n:04d}0"


def row_text(n: int) -> str:
    return f"1 PRODUCT {n:02d} C-12 12OZ\n{upc(n)} {PRICE:.2f} {PRICE:.2f}"


def photo_text(first: int, last: int) -> str:
    return "INVOICE 555 ACME BEVERAGE\n" + "\n".join(row_text(n) for n in range(first, last + 1))


class PerPhotoOCR:
    """Answers each photo with its own text, like a real provider would."""

    def __init__(self, texts: dict[str, str], confidence: float = 0.9):
        self._texts = texts
        self._confidence = confidence

    async def extract_text(self, file_content, mime_type, filename=""):
        return OCRResult(full_text=self._texts[filename], source_type="ocr", page_count=1,
                         mean_confidence=self._confidence, duration_ms=5)


def item(n: int, pages: list[int], qty: float = 1.0, **over) -> ExtractedLineItem:
    return ExtractedLineItem(
        description=f"PRODUCT {n:02d}", product_code=upc(n), pack_size="C-12 12OZ",
        quantity=qty, unit_price=PRICE, line_total=qty * PRICE, source_pages=pages, **over,
    )


def reconciled_30() -> list[ExtractedLineItem]:
    """What v6 asks for: rows 1–30 once, overlap rows carrying both photos."""
    out = []
    for n in range(1, 31):
        pages = [k for k, (a, b) in enumerate(((1, 18), (15, 25), (22, 30)), start=1) if a <= n <= b]
        out.append(item(n, pages))
    return out


def naive_38() -> list[ExtractedLineItem]:
    """Every row of every photo, overlap counted twice — what must NOT pass."""
    out = []
    for k, (a, b) in enumerate(((1, 18), (15, 25), (22, 30)), start=1):
        out += [item(n, [k]) for n in range(a, b + 1)]
    return out


PHOTOS = {"p1.jpg": photo_text(1, 18), "p2.jpg": photo_text(15, 25), "p3.jpg": photo_text(22, 30)}
PNG = b"\x89PNG\r\n\x1a\n" + bytes(1100)          # header + padding past the 1 KB floor


async def process_photos(api_client, app, items, names=("p1.jpg", "p2.jpg", "p3.jpg"),  # noqa: F811
                         texts=PHOTOS, **totals):
    from app.api.v1.invoices import get_pipeline

    app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
        extraction_service=PerPhotoOCR(texts),
        structuring_service=FakeStructuring(extracted_invoice(
            invoice_number="555", line_items=items, **totals)),
    )
    files = [("files", (name, PNG + name.encode(), "image/png")) for name in names]
    response = await api_client.post("/api/v1/invoices/process", files=files,
                                     data={"store_id": str(store_id(STORE))})
    assert response.status_code == 202, response.text
    accepted = response.json()["data"]
    status = (await api_client.get(accepted["status_url"])).json()["data"]
    return accepted["document_id"], status


class TestCombinedContext:
    def test_one_photo_reads_exactly_as_before(self):
        one = PhotoOCR(1, "a.jpg", OCRResult(full_text="hello", source_type="ocr", mean_confidence=0.8))
        assert combine_photos([one]) is one.result                 # untouched, no header

    def test_several_photos_are_separated_and_ordered(self):
        photos = [
            PhotoOCR(2, "b.jpg", OCRResult(full_text="second " * 10, source_type="ocr", mean_confidence=0.5)),
            PhotoOCR(1, "a.jpg", OCRResult(full_text="first " * 30, source_type="digital_pdf", mean_confidence=1.0)),
        ]
        combined = combine_photos(photos)
        assert combined.full_text.startswith(photo_header(1, 2, "a.jpg"))
        assert combined.full_text.index("--- PHOTO 1 of 2") < combined.full_text.index("--- PHOTO 2 of 2")
        assert combined.source_type == "ocr"                       # one OCR photo makes it OCR
        assert combined.page_count == 2
        # confidence is weighted by text length: the longer, surer photo dominates
        assert 0.8 < combined.mean_confidence < 1.0


class TestOverlappingPhotosBecomeOneInvoice:
    async def test_rows_1_to_30_once_with_photo_provenance(self, api_client, app, db_session):  # noqa: F811
        document_id, status = await process_photos(
            api_client, app, reconciled_30(), subtotal=300.0, grand_total=300.0)
        assert status["status"] == "COMPLETED", status
        assert [p["page_number"] for p in status["photos"]] == [1, 2, 3]
        assert [p["filename"] for p in status["photos"]] == ["p1.jpg", "p2.jpg", "p3.jpg"]
        assert all(p["mean_confidence"] == 0.9 for p in status["photos"])

        # ONE document, ONE invoice — not three
        assert (await api_client.get("/api/v1/invoices")).json()["total"] == 1
        detail = (await api_client.get(f"/api/v1/invoices/{status['invoice_id']}")).json()["data"]
        assert detail["status"] == "VALIDATED"
        assert len(detail["line_items"]) == 30
        assert [i["description"] for i in detail["line_items"]] == [f"PRODUCT {n:02d}" for n in range(1, 31)]
        # provenance: the overlaps name both photos, the rest one
        by_desc = {i["description"]: i["source_pages"] for i in detail["line_items"]}
        assert by_desc["PRODUCT 01"] == [1]
        assert by_desc["PRODUCT 16"] == [1, 2]
        assert by_desc["PRODUCT 23"] == [2, 3]
        assert by_desc["PRODUCT 30"] == [3]
        assert detail["duplicate_review_required"] is False
        assert len(detail["photos"]) == 3

        # the model received ONE page-separated context, and each page kept its own text
        document = await DocumentRepository(db_session).get(uuid.UUID(document_id))
        assert document.raw_ocr_text.count("--- PHOTO ") == 3
        assert "--- PHOTO 2 of 3 (p2.jpg) ---" in document.raw_ocr_text
        pages = await DocumentRepository(db_session).pages(document)
        assert [p.raw_ocr_text for p in pages] == [PHOTOS["p1.jpg"], PHOTOS["p2.jpg"], PHOTOS["p3.jpg"]]
        # the extraction log records the photos
        stages = (await api_client.get(f"/api/v1/documents/{document_id}",
                                       params={"include_payloads": "true"})).json()["data"]["stages"]
        extraction = next(s for s in stages if s["stage"] == "TEXT_EXTRACTION")
        assert extraction["payload"]["photo_count"] == 3
        assert [p["page_number"] for p in extraction["payload"]["photos"]] == [1, 2, 3]

    async def test_naive_duplication_is_caught_by_the_totals_not_hidden(self, api_client, app):  # noqa: F811
        # 38 rows (18 + 11 + 9: the overlap counted twice) against a printed subtotal of 300
        _, status = await process_photos(api_client, app, naive_38(), subtotal=300.0, grand_total=300.0)
        assert status["status"] == "REVIEW_REQUIRED"
        detail = (await api_client.get(f"/api/v1/invoices/{status['invoice_id']}")).json()["data"]
        assert len(detail["line_items"]) == 38                     # nothing was silently removed
        report = detail["validation_report"]
        assert any(c["name"] == "SUBTOTAL_MATCHES_ITEMS" and c["status"] == "FAILED" for c in report["checks"])
        assert detail["pdi_export_allowed"] is False

    async def test_a_single_file_is_a_one_photo_intake_with_no_headers(self, api_client, app, db_session):  # noqa: F811
        document_id, status = await process_photos(
            api_client, app, [item(n, []) for n in range(1, 4)], names=("only.jpg",),
            texts={"only.jpg": photo_text(1, 3)}, subtotal=30.0, grand_total=30.0)
        assert status["status"] == "COMPLETED"
        assert status["photos"] == []                              # one file: nothing to list
        document = await DocumentRepository(db_session).get(uuid.UUID(document_id))
        assert document.raw_ocr_text == photo_text(1, 3)           # exactly the OCR text, as before
        assert document.filename == "only.jpg"
        detail = (await api_client.get(f"/api/v1/invoices/{status['invoice_id']}")).json()["data"]
        assert all(i["source_pages"] == [] for i in detail["line_items"])

    async def test_the_same_photos_again_are_a_duplicate(self, api_client, app):  # noqa: F811
        await process_photos(api_client, app, reconciled_30(), subtotal=300.0, grand_total=300.0)
        files = [("files", (name, PNG + name.encode(), "image/png")) for name in PHOTOS]
        again = await api_client.post("/api/v1/invoices/process", files=files,
                                      data={"store_id": str(store_id(STORE))})
        assert again.status_code == 409
        # and one of those photos inside a different intake is refused too
        one = [("files", ("p2.jpg", PNG + b"p2.jpg", "image/png")),
               ("files", ("new.jpg", PNG + b"new.jpg", "image/png"))]
        mixed = await api_client.post("/api/v1/invoices/process", files=one,
                                      data={"store_id": str(store_id(STORE))})
        assert mixed.status_code == 409
        assert mixed.json()["error"]["detail"]["existing_page_number"] == 2

    async def test_no_file_at_all_is_refused(self, api_client):  # noqa: F811
        response = await api_client.post("/api/v1/invoices/process",
                                         data={"store_id": str(store_id(STORE))})
        assert response.status_code == 422


class TestLegitimateRepeatedUpc:
    async def test_the_same_upc_on_two_real_rows_stays_twice(self, api_client, app):  # noqa: F811
        # PRODUCT 07 is genuinely on the invoice twice: once as a case (qty 2)
        # in photo 1 and again shorted-then-delivered (qty 1) in photo 3.
        items = reconciled_30()
        items[6] = item(7, [1], qty=2.0)
        items.append(item(7, [3], qty=1.0))
        _, status = await process_photos(api_client, app, items, subtotal=320.0, grand_total=320.0)
        assert status["status"] == "COMPLETED"
        detail = (await api_client.get(f"/api/v1/invoices/{status['invoice_id']}")).json()["data"]
        sevens = [i for i in detail["line_items"] if i["description"] == "PRODUCT 07"]
        assert [(i["quantity"], i["source_pages"]) for i in sevens] == [(2.0, [1]), (1.0, [3])]
        assert detail["status"] == "VALIDATED"
        assert detail["duplicate_review_required"] is False


class TestAmbiguousDuplicateIsFlaggedNotMerged:
    def _items(self):
        # The model saw PRODUCT 20 at the end of photo 2 and again at the
        # start of photo 3 with identical figures, but the neighbours did
        # not line up: it keeps both and flags the later one.
        items = reconciled_30()
        items.append(item(20, [3], possible_duplicate_of=19,
                          duplicate_reason="same UPC, qty, price and total; neighbouring rows differ"))
        return items

    async def test_flagged_pair_blocks_the_invoice_and_is_visible(self, api_client, app):  # noqa: F811
        _, status = await process_photos(api_client, app, self._items(), subtotal=300.0, grand_total=300.0)
        assert status["status"] == "REVIEW_REQUIRED"
        detail = (await api_client.get(f"/api/v1/invoices/{status['invoice_id']}")).json()["data"]
        assert len(detail["line_items"]) == 31                     # both rows kept
        assert detail["duplicate_review_required"] is True
        flagged = detail["line_items"][30]
        assert flagged["duplicate_candidate"] == {
            "of_sort_order": 19, "reason": "same UPC, qty, price and total; neighbouring rows differ",
            "resolution": None,
        }
        assert detail["line_items"][19]["description"] == "PRODUCT 20"   # the row it points at
        report = detail["validation_report"]
        assert any(c["name"] == "CROSS_PHOTO_DUPLICATES" and c["status"] == "FAILED" for c in report["checks"])
        assert any("Row 30 may be the same physical row as row 19" in r for r in report["review_reasons"])
        assert detail["pdi_export_allowed"] is False

    async def test_same_row_decision_keeps_the_row_but_drops_it_from_totals_and_edi(self, api_client, app, db_session):  # noqa: F811
        _, status = await process_photos(api_client, app, self._items(), subtotal=300.0, grand_total=300.0)
        invoice_id = status["invoice_id"]
        r = await api_client.post(f"/api/v1/invoices/{invoice_id}/items/30/duplicate-decision",
                                  json={"decision": "same_row", "decided_by": "data-team:shashwat",
                                        "note": "photo 3 starts where photo 2 ended"})
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert (d["decision"], d["line_type"], d["status"], d["failed_checks"]) == ("same_row", "duplicate", "VALIDATED", 0)

        detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        assert detail["status"] == "VALIDATED"
        assert detail["duplicate_review_required"] is False
        assert len(detail["line_items"]) == 31                     # audit: the row is still there
        resolved = detail["line_items"][30]
        assert resolved["line_type"] == "duplicate"
        assert resolved["duplicate_candidate"]["resolution"] == "same_row"
        assert resolved["duplicate_candidate"]["decided_by"] == "data-team:shashwat"
        assert resolved["duplicate_candidate"]["decided_at"]
        assert resolved["duplicate_candidate"]["note"] == "photo 3 starts where photo 2 ended"
        # …but it is not merchandise: 30 PDI rows, not 31
        invoice = await InvoiceRepository(db_session).get_detail(uuid.UUID(invoice_id))
        assert len(pdi_items(invoice)) == 30
        # the decision is in the processing log
        stages = (await api_client.get(f"/api/v1/documents/{detail['document_id']}",
                                       params={"include_payloads": "true"})).json()["data"]["stages"]
        assert any((s["payload"] or {}).get("event") == "duplicate_decision" for s in stages)

    async def test_separate_rows_decision_keeps_both_and_the_totals_must_then_agree(self, api_client, app):  # noqa: F811
        # printed subtotal 310 = both rows are real
        _, status = await process_photos(api_client, app, self._items(), subtotal=310.0, grand_total=310.0)
        invoice_id = status["invoice_id"]
        r = await api_client.post(f"/api/v1/invoices/{invoice_id}/items/30/duplicate-decision",
                                  json={"decision": "separate_rows", "decided_by": "reviewer"})
        assert r.status_code == 200, r.text
        assert (r.json()["data"]["line_type"], r.json()["data"]["status"]) == ("product", "VALIDATED")
        detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        assert len([i for i in detail["line_items"] if i["line_type"] == "product"]) == 31

    async def test_a_wrong_decision_is_caught_by_the_totals_not_hidden(self, api_client, app):  # noqa: F811
        # printed subtotal 300 says one of them is a duplicate; the reviewer says both are real
        _, status = await process_photos(api_client, app, self._items(), subtotal=300.0, grand_total=300.0)
        r = await api_client.post(f"/api/v1/invoices/{status['invoice_id']}/items/30/duplicate-decision",
                                  json={"decision": "separate_rows", "decided_by": "reviewer"})
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["status"] == "REVIEW_REQUIRED"
        assert any("SUBTOTAL" in reason for reason in d["review_reasons"])
        assert d["pdi_export_allowed"] is False

    async def test_a_decision_is_made_once_and_only_on_a_flagged_row(self, api_client, app):  # noqa: F811
        _, status = await process_photos(api_client, app, self._items(), subtotal=300.0, grand_total=300.0)
        invoice_id = status["invoice_id"]
        base = f"/api/v1/invoices/{invoice_id}/items"
        assert (await api_client.post(f"{base}/3/duplicate-decision",
                                      json={"decision": "same_row", "decided_by": "r"})).status_code == 422
        assert (await api_client.post(f"{base}/30/duplicate-decision",
                                      json={"decision": "same_row", "decided_by": ""})).status_code == 422
        assert (await api_client.post(f"{base}/30/duplicate-decision",
                                      json={"decision": "same_row", "decided_by": "r"})).status_code == 200
        again = await api_client.post(f"{base}/30/duplicate-decision",
                                      json={"decision": "separate_rows", "decided_by": "r2"})
        assert again.status_code == 422
        assert again.json()["error"]["detail"]["resolution"] == "same_row"
        assert (await api_client.post(f"/api/v1/invoices/{uuid.uuid4()}/items/30/duplicate-decision",
                                      json={"decision": "same_row", "decided_by": "r"})).status_code == 404


class TestInvoiceReviewVisibility:
    async def test_the_invoice_shows_its_own_review_state_apart_from_the_store(self, api_client, app, db_session):  # noqa: F811
        _, status = await process_photos(
            api_client, app, [item(n, []) for n in range(1, 3)], names=("r.jpg",),
            texts={"r.jpg": photo_text(1, 2)}, subtotal=20.0, grand_total=20.0)
        invoice_id = status["invoice_id"]
        detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        assert detail["review"] == {"status": "NONE", "pending": 0, "approved": 0, "rejected": 0, "proposals": []}

        # two case mappings confirmed from the invoice page → two pending proposals
        codes = [r["item_code"] for r in detail["case_mappings"]]
        r = await api_client.post(f"/api/v1/invoices/{invoice_id}/case-mappings", json={
            "mappings": [{"item_code": codes[0], "units_per_case": 12},
                         {"item_code": codes[1], "units_per_case": 12}]})
        assert r.status_code == 200, r.text
        detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        review = detail["review"]
        assert (review["status"], review["pending"], review["approved"]) == ("PENDING", 2, 0)
        assert sorted(p["entity_key"] for p in review["proposals"]) == sorted(codes)
        assert all(p["field"] == "units_per_case" and p["proposed_value"] == 12 and p["status"] == STATUS_PENDING
                   for p in review["proposals"])
        # the store's identity is a different question and stays what it was
        assert detail["store"]["identity_status"] == "unresolved"
        # the history list carries the same summary
        rows = (await api_client.get("/api/v1/invoices")).json()["items"]
        mine = next(row for row in rows if row["invoice_id"] == invoice_id)
        assert mine["review"]["status"] == "PENDING" and mine["review"]["pending"] == 2
        assert mine["photo_count"] == 1

        # approve one, revise-then-approve the other → APPROVED, with the revision trail
        first, second = review["proposals"]
        await api_client.post(f"/api/v1/proposals/{first['id']}/approve", json={"reviewed_by": "rev"})
        revised = (await api_client.post(f"/api/v1/proposals/{second['id']}/revise",
                                         json={"proposed_value": 24, "proposed_by": "rev"})).json()["data"]["proposal"]
        await api_client.post(f"/api/v1/proposals/{revised['id']}/approve", json={"reviewed_by": "rev", "note": "ok"})
        review = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]["review"]
        assert (review["status"], review["pending"], review["approved"], review["rejected"]) == ("APPROVED", 0, 2, 1)
        by_id = {p["id"]: p for p in review["proposals"]}
        assert by_id[second["id"]]["status"] == STATUS_REJECTED           # superseded original
        assert by_id[revised["id"]]["revised_from"] == second["id"]
        assert by_id[revised["id"]]["reviewed_by"] == "rev" and by_id[revised["id"]]["review_note"] == "ok"


class TestInvoiceDeletionLeavesHistoryAndMasterData:
    async def test_what_goes_and_what_stays(self, api_client, app, db_session):  # noqa: F811
        document_id, status = await process_photos(
            api_client, app, [item(1, [1]), item(2, [1, 2])], subtotal=20.0, grand_total=20.0,
            names=("d1.jpg", "d2.jpg"), texts={"d1.jpg": photo_text(1, 2), "d2.jpg": photo_text(2, 2)})
        invoice_id = status["invoice_id"]
        detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        codes = [r["item_code"] for r in detail["case_mappings"]]
        await api_client.post(f"/api/v1/invoices/{invoice_id}/case-mappings", json={
            "mappings": [{"item_code": codes[0], "units_per_case": 12},
                         {"item_code": codes[1], "units_per_case": 6}]})
        proposals = await ProductDataProposalRepository(db_session).list(invoice_id=uuid.UUID(invoice_id))
        approved_id, pending_id = (str(p.id) for p in proposals)
        await api_client.post(f"/api/v1/proposals/{approved_id}/approve", json={"reviewed_by": "rev"})

        r = await api_client.delete(f"/api/v1/invoices/{invoice_id}")
        assert r.status_code == 200, r.text

        # operational data is gone: invoice, items, document, photos, logs
        assert (await api_client.get(f"/api/v1/invoices/{invoice_id}")).status_code == 404
        assert (await api_client.get(f"/api/v1/documents/{document_id}")).status_code == 404
        assert await DocumentRepository(db_session).get(uuid.UUID(document_id)) is None
        assert await DocumentRepository(db_session).page_by_hash(
            __import__("hashlib").sha256(PNG + b"d1.jpg").hexdigest()) is None

        # immutable review history stays, and says its invoice is gone
        rows = (await api_client.get("/api/v1/proposals", params={"status": "ALL"})).json()["items"]
        mine = {row["id"]: row for row in rows if row["invoice_id"] == invoice_id}
        assert set(mine) == {approved_id, pending_id}
        assert all(row["invoice_deleted"] is True for row in mine.values())
        assert mine[approved_id]["status"] == STATUS_APPROVED
        assert mine[pending_id]["status"] == STATUS_PENDING
        one = (await api_client.get(f"/api/v1/proposals/{pending_id}")).json()["data"]
        assert one["invoice_deleted"] is True
        # a live invoice's proposal does not carry the flag
        assert all(row["invoice_deleted"] is False for row in rows if row["invoice_id"] != invoice_id)

        # master data written by the approval stays
        mapping = await ProductCaseMappingRepository(db_session).get(store_id(STORE), codes[0])
        assert mapping is not None and mapping.units_per_case == 12
        assert str(mapping.approved_proposal_id) == approved_id

        # the same photos can now be processed again (nothing stale blocks a re-run)
        again = await api_client.post(
            "/api/v1/invoices/process",
            files=[("files", ("d1.jpg", PNG + b"d1.jpg", "image/png"))],
            data={"store_id": str(store_id(STORE))})
        assert again.status_code == 202


@pytest.mark.parametrize("decision", ["same_row", "separate_rows"])
async def test_decisions_never_change_quantities(api_client, app, decision):  # noqa: F811
    items = reconciled_30()
    items.append(item(20, [3], qty=3.0, possible_duplicate_of=19, duplicate_reason="unsure"))
    _, status = await process_photos(api_client, app, items, subtotal=300.0, grand_total=300.0)
    invoice_id = status["invoice_id"]
    await api_client.post(f"/api/v1/invoices/{invoice_id}/items/30/duplicate-decision",
                          json={"decision": decision, "decided_by": "r"})
    detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
    assert detail["line_items"][30]["quantity"] == 3.0                # never "fixed" to make totals work
    assert detail["line_items"][19]["quantity"] == 1.0
