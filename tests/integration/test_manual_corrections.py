"""
tests/integration/test_manual_corrections.py — a person fixes what the
photos and the model got wrong, and the invoice is judged again.

Extraction is not perfect and a photo can miss rows. After any manual
change — a line added, voided or corrected; a printed total corrected —
the deterministic rules run again on the corrected invoice and its
status reflects THAT invoice, so a run that began REVIEW_REQUIRED can end
VALIDATED without anyone editing the file by hand. Every change says who,
when, why, from what to what; the extracted values are never overwritten
silently; and none of it touches master data.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.repositories.product_case_mapping_repository import ProductCaseMappingRepository
from app.repositories.product_data_proposal_repository import ProductDataProposalRepository
from app.schemas.extraction import ExtractedLineItem
from app.services.pipeline_service import InvoiceProcessingPipeline
from tests.integration.conftest import requires_db, store_id
from tests.integration.fakes import FakeStructuring, extracted_invoice
from tests.integration.test_api_db import api_client, process_file  # noqa: F401 — fixture reuse
from tests.pdf_builder import build_pdf

pytestmark = requires_db

STORE = "47708760"
WHO = "data-team:shashwat"


def row(desc, upc, qty, price, deposit=0.0):
    return ExtractedLineItem(description=desc, product_code=upc, quantity=qty, unit_price=price,
                             unit_deposit=deposit, line_total=round((price + deposit) * qty, 2))


async def process(api_client, app, items, name, **totals):  # noqa: F811
    from app.api.v1.invoices import get_pipeline

    app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
        structuring_service=FakeStructuring(extracted_invoice(line_items=items, **totals)))
    accepted = await process_file(api_client, content=build_pdf([name + " pad " * 300]), filename=name)
    return (await api_client.get(accepted["status_url"])).json()["data"]["invoice_id"]


async def detail(api_client, invoice_id):  # noqa: F811
    return (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]


async def audit(api_client, invoice_id):  # noqa: F811
    d = await detail(api_client, invoice_id)
    stages = (await api_client.get(f"/api/v1/documents/{d['document_id']}",
                                   params={"include_payloads": "true"})).json()["data"]["stages"]
    return [s["payload"] for s in stages if (s["payload"] or {}).get("event")]


class TestAMissedPhotoIsRepairedOnTheSameInvoice:
    async def test_add_two_lines_fix_the_total_and_the_invoice_validates(self, api_client, app, db_session):  # noqa: F811
        # the photos caught 2 of 4 rows; printed totals are for all 4
        seen = [row("COORS LIGHT", "071990300173", 2, 16.25), row("BLUE MOON", "071990095116", 1, 35.70)]
        invoice_id = await process(api_client, app, seen, "missed.pdf", subtotal=100.65, grand_total=100.00)
        before = await detail(api_client, invoice_id)
        assert before["status"] == "REVIEW_REQUIRED"
        failed = {c["name"] for c in before["validation_report"]["checks"] if c["status"] == "FAILED"}
        assert "SUBTOTAL_MATCHES_ITEMS" in failed

        for body in (
            {"description": "KEYSTONE LIGHT", "product_code": "071990480080", "quantity": 1, "unit_price": 17.70,
             "added_by": WHO, "note": "on photo 2, missed by extraction"},
            {"description": "HIGH LIFE", "product_code": "034100000073", "quantity": 1, "unit_price": 14.75,
             "added_by": WHO},
        ):
            r = await api_client.post(f"/api/v1/invoices/{invoice_id}/items", json=body)
            assert r.status_code == 201, r.text
            assert r.json()["data"]["item"]["entry_source"] == "manual"
        # still off: the model read the grand total as 100.00, the document says 100.65
        mid = await detail(api_client, invoice_id)
        assert mid["status"] == "REVIEW_REQUIRED"
        assert len(mid["line_items"]) == 4
        assert [i["sort_order"] for i in mid["line_items"]] == [0, 1, 2, 3]   # appended in order

        r = await api_client.patch(f"/api/v1/invoices/{invoice_id}/totals",
                                   json={"grand_total": 100.65, "corrected_by": WHO, "note": "printed grand total"})
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["status"] == "VALIDATED" and d["failed_checks"] == 0, d["review_reasons"]
        assert d["grand_total"] == 100.65 and d["corrected_fields"] == ["grand_total"]
        [entry] = d["correction_history"]
        assert (entry["field"], entry["old"], entry["new"], entry["by"], entry["note"]) == (
            "grand_total", 100.0, 100.65, WHO, "printed grand total")
        assert entry["at"]

        after = await detail(api_client, invoice_id)
        assert after["status"] == "VALIDATED"
        assert after["grand_total"] == 100.65 and after["corrected_fields"] == ["grand_total"]
        # the extracted grand total is still on the record, in the history's `old`
        assert after["correction_history"][0]["old"] == 100.0
        added = [i for i in after["line_items"] if i["entry_source"] == "manual"]
        assert [i["description"] for i in added] == ["KEYSTONE LIGHT", "HIGH LIFE"]
        assert added[0]["correction_history"][0]["by"] == WHO
        assert added[0]["correction_history"][0]["note"] == "on photo 2, missed by extraction"
        assert set(added[0]["corrected_fields"]) >= {"description", "quantity", "unit_price"}
        # the processing log tells the whole story in order
        events = [e["event"] for e in await audit(api_client, invoice_id)]
        assert events == ["line_item_added", "line_item_added", "totals_corrected"]

        # ONE invoice, still — and the manual rows are on it
        assert (await api_client.get("/api/v1/invoices")).json()["total"] == 1

    async def test_manual_rows_are_invoice_data_not_master_data(self, api_client, app, db_session):  # noqa: F811
        invoice_id = await process(api_client, app, [row("COORS LIGHT", "071990300173", 1, 16.25)],
                                   "manual.pdf", subtotal=16.25, grand_total=16.25)
        r = await api_client.post(f"/api/v1/invoices/{invoice_id}/items", json={
            "description": "NEW PRODUCT", "product_code": "099999999999", "quantity": 1, "unit_price": 10.0,
            "pack_size": "C-12 12OZ", "added_by": WHO})
        assert r.status_code == 201
        assert await ProductDataProposalRepository(db_session).list(invoice_id=uuid.UUID(invoice_id)) == []
        assert await ProductCaseMappingRepository(db_session).get(store_id(STORE), "09999999999") is None
        # the new product simply shows up as an unmapped row, to be confirmed through Data Review
        d = await detail(api_client, invoice_id)
        new_row = next(m for m in d["case_mappings"] if m["item_code"] == "09999999999")
        assert new_row["mapped"] is False and new_row["units_per_case"] is None
        assert d["pdi_export_allowed"] is False


class TestCorrectingAndVoidingLines:
    async def test_every_field_change_is_attributed_and_the_old_path_still_works(self, api_client, app):  # noqa: F811
        invoice_id = await process(api_client, app,
                                   [row("COORS LIGHT", "071990300173", 2, 16.25), row("BLUE MOON", "071990095116", 1, 35.70)],
                                   "fields.pdf", subtotal=68.20, grand_total=68.20)
        # the pre-attribution body (single value, no name) still works exactly as before
        r = await api_client.patch(f"/api/v1/invoices/{invoice_id}/items/0", json={"quantity": 2})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["item"]["corrected_fields"] == ["quantity"]
        assert r.json()["data"]["item"]["correction_history"][0]["by"] is None
        # the new fields, attributed
        r = await api_client.patch(f"/api/v1/invoices/{invoice_id}/items/1", json={
            "description": "BLUE MOON BELGIAN WHITE", "product_code": "071990095116", "unit_deposit": 1.20,
            "unit_discount": 0.50, "corrected_by": WHO, "note": "read off the photo"})
        assert r.status_code == 200, r.text
        item = r.json()["data"]["item"]
        assert item["description"] == "BLUE MOON BELGIAN WHITE"
        assert item["unit_deposit"] == 1.2 and item["unit_discount"] == 0.5
        assert sorted(item["corrected_fields"]) == ["description", "product_code", "unit_deposit", "unit_discount"]
        by_field = {h["field"]: h for h in item["correction_history"]}
        assert by_field["description"]["old"] == "BLUE MOON" and by_field["description"]["by"] == WHO
        assert by_field["unit_deposit"]["old"] == 0.0 and by_field["unit_deposit"]["new"] == 1.2
        assert all(h["note"] == "read off the photo" for h in item["correction_history"])
        # bad input is refused as before
        assert (await api_client.patch(f"/api/v1/invoices/{invoice_id}/items/1", json={"unit_discount": -1})).status_code == 422
        assert (await api_client.patch(f"/api/v1/invoices/{invoice_id}/items/1", json={"corrected_by": WHO})).status_code == 422

    async def test_voiding_a_line_keeps_it_for_audit_and_drops_it_from_totals_and_pdi(self, api_client, app, db_session):  # noqa: F811
        # the model invented a third row; the printed subtotal is for two
        items = [row("COORS LIGHT", "071990300173", 2, 16.25), row("BLUE MOON", "071990095116", 1, 35.70),
                 row("PHANTOM", "071990000000", 1, 9.99)]
        invoice_id = await process(api_client, app, items, "void.pdf", subtotal=68.20, grand_total=68.20)
        assert (await detail(api_client, invoice_id))["status"] == "REVIEW_REQUIRED"

        r = await api_client.request("DELETE", f"/api/v1/invoices/{invoice_id}/items/2",
                                     json={"voided_by": WHO, "note": "not on the invoice"})
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["item"]["line_type"] == "voided" and d["status"] == "VALIDATED"
        after = await detail(api_client, invoice_id)
        assert len(after["line_items"]) == 3                        # kept
        assert after["line_items"][2]["line_type"] == "voided"
        assert after["line_items"][2]["correction_history"][0]["new"] == "voided"
        assert after["status"] == "VALIDATED"
        # not merchandise: two mapping rows, not three; PDI would carry two records
        assert len(after["case_mappings"]) == 2
        again = await api_client.request("DELETE", f"/api/v1/invoices/{invoice_id}/items/2",
                                         json={"voided_by": WHO})
        assert again.status_code == 422
        assert (await api_client.request("DELETE", f"/api/v1/invoices/{invoice_id}/items/9",
                                         json={"voided_by": WHO})).status_code == 404


class TestTotals:
    async def test_every_header_total_is_correctable_and_a_wrong_one_is_caught(self, api_client, app):  # noqa: F811
        invoice_id = await process(api_client, app, [row("COORS LIGHT", "071990300173", 2, 16.25, 0.90)],
                                   "totals.pdf", subtotal=32.50, deposit_total=1.80, grand_total=34.30)
        assert (await detail(api_client, invoice_id))["status"] == "VALIDATED"
        # a wrong correction does not pass: the rules judge the corrected invoice
        r = await api_client.patch(f"/api/v1/invoices/{invoice_id}/totals",
                                   json={"grand_total": 40.00, "corrected_by": WHO})
        assert r.status_code == 200 and r.json()["data"]["status"] == "REVIEW_REQUIRED"
        # put it back, with the deposit total and fuel surcharge stated
        r = await api_client.patch(f"/api/v1/invoices/{invoice_id}/totals",
                                   json={"grand_total": 34.30, "deposit_total": 1.80, "fuel_surcharge": 0,
                                         "corrected_by": WHO, "note": "re-read"})
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["status"] == "VALIDATED"
        assert d["corrected_fields"] == ["deposit_total", "fuel_surcharge", "grand_total"]
        assert [(h["field"], h["old"], h["new"]) for h in d["correction_history"]] == [
            ("grand_total", 34.3, 40.0), ("deposit_total", 1.8, 1.8), ("fuel_surcharge", None, 0.0), ("grand_total", 40.0, 34.3)]
        after = await detail(api_client, invoice_id)
        assert after["deposit_total"] == 1.8 and after["fuel_surcharge"] == 0.0
        assert (await api_client.patch(f"/api/v1/invoices/{invoice_id}/totals", json={"corrected_by": WHO})).status_code == 422
        assert (await api_client.patch(f"/api/v1/invoices/{invoice_id}/totals",
                                       json={"grand_total": -1, "corrected_by": WHO})).status_code == 422
        assert (await api_client.patch(f"/api/v1/invoices/{invoice_id}/totals", json={"grand_total": 1})).status_code == 422


class TestRuleDOnRevalidation:
    """
    RCM 1012818: a layout whose NET column is PRICE + DEP was extracted with
    unit_price = NET and line_total = EXT, and the printed deposit total was
    misread (deposit + delivery fee). Rule D could not prove anything at
    run time, so the deposit-inclusive prices were persisted. Correcting
    the totals must make the proof hold AND write the goods price back to
    the stored rows — otherwise validation passes while the EDI carries
    the deposit in every case cost.
    """

    async def test_correcting_the_totals_writes_the_proven_goods_price_back(self, api_client, app, db_session):  # noqa: F811
        # NET prices, EXT line totals, deposit total misread as deposit + fee
        items = [
            ExtractedLineItem(description="KEYSTONE LIGHT 4/6/16 CAN", product_code="071990480080", quantity=4,
                              unit_price=17.70, unit_deposit=1.20, line_total=70.80),
            ExtractedLineItem(description="ANGRY ORCHARD 12/19.2 CRISP", product_code="087692023777", quantity=1,
                              unit_price=23.20, unit_deposit=0.0, line_total=23.20),
        ]
        invoice_id = await process(api_client, app, items, "ruled.pdf",
                                   subtotal=89.20, deposit_total=14.80, fuel_surcharge=10.0, grand_total=104.00)
        before = await detail(api_client, invoice_id)
        assert before["status"] == "REVIEW_REQUIRED"
        assert before["line_items"][0]["unit_price"] == 17.7                       # persisted as extracted (NET)
        names = {c["name"] for c in before["validation_report"]["checks"] if c["status"] == "FAILED"}
        assert "UNIT_PRICE_MAY_INCLUDE_DEPOSIT" in names                           # half-proof only

        r = await api_client.patch(f"/api/v1/invoices/{invoice_id}/totals",
                                   json={"deposit_total": 4.80, "corrected_by": WHO, "note": "printed Total Deposit"})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["status"] == "VALIDATED", r.json()["data"]["review_reasons"]

        after = await detail(api_client, invoice_id)
        keystone, orchard = after["line_items"]
        assert keystone["unit_price"] == 16.5                                       # goods price, persisted
        assert keystone["line_total"] == 70.8                                       # printed EXT untouched
        assert keystone["unit_deposit"] == 1.2
        assert orchard["unit_price"] == 23.2                                        # no deposit: untouched
        entry = keystone["correction_history"][-1]
        assert (entry["field"], entry["old"], entry["new"], entry["by"]) == ("unit_price", 17.7, 16.5, "rule:D")
        assert "proved by the invoice totals" in entry["note"]
        assert keystone["corrected_fields"] == []                                   # not a person's correction
        events = [e["event"] for e in await audit(api_client, invoice_id)]
        assert events == ["totals_corrected", "rule_d_applied"]
        # merchandise and deposits stay separate concepts
        merchandise = sum(i["unit_price"] * i["quantity"] for i in after["line_items"])
        deposits = sum((i["unit_deposit"] or 0) * i["quantity"] for i in after["line_items"])
        assert (round(merchandise, 2), round(deposits, 2)) == (89.20, 4.80)
        assert sum(i["line_total"] for i in after["line_items"]) == 94.0            # Σ EXT = merchandise + deposits
        # revalidating again is a no-op: nothing else to prove, no second history entry
        r = await api_client.patch(f"/api/v1/invoices/{invoice_id}/totals",
                                   json={"fuel_surcharge": 10.0, "corrected_by": WHO})
        assert r.json()["data"]["status"] == "VALIDATED"
        again = await detail(api_client, invoice_id)
        assert [h["by"] for h in again["line_items"][0]["correction_history"]] == ["rule:D"]

    async def test_a_half_proof_never_writes_anything(self, api_client, app):  # noqa: F811
        items = [ExtractedLineItem(description="KEYSTONE LIGHT 4/6/16 CAN", product_code="071990480080", quantity=4,
                                   unit_price=17.70, unit_deposit=1.20, line_total=70.80)]
        invoice_id = await process(api_client, app, items, "half.pdf",
                                   subtotal=66.00, deposit_total=9.99, grand_total=75.99)   # deposit total wrong
        r = await api_client.patch(f"/api/v1/invoices/{invoice_id}/items/0", json={"quantity": 4, "corrected_by": WHO})
        assert r.status_code == 200
        after = await detail(api_client, invoice_id)
        assert after["status"] == "REVIEW_REQUIRED"
        assert after["line_items"][0]["unit_price"] == 17.7                        # left as extracted
        assert all(h["by"] != "rule:D" for h in after["line_items"][0]["correction_history"])


# ---------------------------------------------------------------------------
# Invoice date: entered by a person, or explicitly unknown — never inferred
# ---------------------------------------------------------------------------


async def document_status(api_client, invoice_id):  # noqa: F811
    d = await detail(api_client, invoice_id)
    return (await api_client.get(f"/api/v1/documents/{d['document_id']}")).json()["data"]["status"]


class TestInvoiceDateCorrection:
    async def test_an_extracted_date_persists_normally(self, api_client, app):  # noqa: F811
        invoice_id = await process(api_client, app, [row("COORS LIGHT", "071990300173", 1, 18.9)], "dated.pdf",
                                   invoice_date="2026-01-15", subtotal=18.9, grand_total=18.9)
        d = await detail(api_client, invoice_id)
        assert d["invoice_date"] == "2026-01-15" and d["status"] == "VALIDATED"
        assert "invoice_date" not in d["corrected_fields"]

    async def test_a_missing_date_is_a_review_reason_but_not_a_confidence_penalty(self, api_client, app):  # noqa: F811
        items = [row("COORS LIGHT", "071990300173", 1, 18.9)]
        dated = await detail(api_client, await process(api_client, app, items, "with-date.pdf",
                                                        invoice_date="2026-01-15", subtotal=18.9, grand_total=18.9))
        undated = await detail(api_client, await process(api_client, app, items, "no-date.pdf",
                                                          invoice_date=None, subtotal=18.9, grand_total=18.9))
        assert undated["invoice_date"] is None
        assert undated["status"] == "REVIEW_REQUIRED"
        assert {c["name"] for c in undated["validation_report"]["checks"] if c["status"] == "FAILED"} == {"INVOICE_DATE_VALID"}
        assert undated["composite_confidence"] == dated["composite_confidence"]
        # the document follows the decision from the start
        assert await document_status(api_client, undated["invoice_id"]) == "REVIEW_REQUIRED"
        assert await document_status(api_client, dated["invoice_id"]) == "COMPLETED"

    async def test_a_person_enters_the_date_and_everything_follows(self, api_client, app):  # noqa: F811
        invoice_id = await process(api_client, app, [row("COORS LIGHT", "071990300173", 1, 18.9)], "enter-date.pdf",
                                   invoice_date=None, subtotal=18.9, grand_total=18.9)
        before = await detail(api_client, invoice_id)
        assert before["status"] == "REVIEW_REQUIRED" and before["invoice_date"] is None

        r = await api_client.patch(f"/api/v1/invoices/{invoice_id}/date",
                                   json={"invoice_date": "2026-01-15", "corrected_by": WHO, "note": "printed top right"})
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["invoice_date"] == "2026-01-15"
        assert d["status"] == "VALIDATED" and d["failed_checks"] == 0, d["review_reasons"]
        assert d["corrected_fields"] == ["invoice_date"]
        [entry] = d["correction_history"]
        assert (entry["field"], entry["old"], entry["new"], entry["by"], entry["note"]) == (
            "invoice_date", None, "2026-01-15", WHO, "printed top right")
        assert entry["at"]

        after = await detail(api_client, invoice_id)
        assert after["invoice_date"] == "2026-01-15" and after["status"] == "VALIDATED"
        # manual entry does not touch extraction confidence
        assert after["composite_confidence"] == before["composite_confidence"]
        # the original extraction evidence is intact: the model reported no date
        raw = after["raw_extraction"]
        assert raw is not None and "2026-01-15" not in str(raw.get("choices") or raw)
        # revalidated, and the document moved with the decision
        events = [e["event"] for e in await audit(api_client, invoice_id)]
        assert events == ["invoice_date_corrected"]
        assert await document_status(api_client, invoice_id) == "COMPLETED"
        listed = (await api_client.get("/api/v1/invoices", params={"status": "COMPLETED"})).json()["items"]
        assert invoice_id in {i["invoice_id"] for i in listed}

    async def test_unknown_stays_unknown_and_is_never_inferred(self, api_client, app):  # noqa: F811
        invoice_id = await process(api_client, app, [row("COORS LIGHT", "071990300173", 1, 18.9)], "unknown.pdf",
                                   invoice_date="2026-01-15", subtotal=18.9, grand_total=18.9)
        r = await api_client.patch(f"/api/v1/invoices/{invoice_id}/date",
                                   json={"invoice_date": None, "corrected_by": WHO, "note": "date illegible on the photo"})
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["invoice_date"] is None and d["status"] == "REVIEW_REQUIRED"
        assert d["correction_history"][-1]["old"] == "2026-01-15" and d["correction_history"][-1]["new"] is None
        after = await detail(api_client, invoice_id)
        # nothing was substituted from the upload/processing timestamps or the filename
        assert after["invoice_date"] is None
        assert after["created_at"]
        assert await document_status(api_client, invoice_id) == "REVIEW_REQUIRED"


# ---------------------------------------------------------------------------
# invoice.status and document.status never diverge
# ---------------------------------------------------------------------------


class TestStatusSynchronization:
    async def test_first_persistence_maps_the_decision_onto_the_document(self, api_client, app):  # noqa: F811
        ok = await process(api_client, app, [row("A", "071990300173", 1, 10.0)], "ok.pdf", subtotal=10.0, grand_total=10.0)
        bad = await process(api_client, app, [row("A", "071990300173", 1, 10.0)], "bad.pdf", subtotal=10.0, grand_total=99.0)
        assert (await detail(api_client, ok))["status"] == "VALIDATED"
        assert await document_status(api_client, ok) == "COMPLETED"
        assert (await detail(api_client, bad))["status"] == "REVIEW_REQUIRED"
        assert await document_status(api_client, bad) == "REVIEW_REQUIRED"

    async def test_a_totals_correction_that_validates_moves_the_document(self, api_client, app):  # noqa: F811
        invoice_id = await process(api_client, app, [row("A", "071990300173", 1, 10.0)], "t.pdf", subtotal=10.0, grand_total=99.0)
        r = await api_client.patch(f"/api/v1/invoices/{invoice_id}/totals",
                                   json={"grand_total": 10.0, "corrected_by": WHO, "note": "printed"})
        assert r.json()["data"]["status"] == "VALIDATED"
        assert await document_status(api_client, invoice_id) == "COMPLETED"

    async def test_a_correction_that_still_needs_review_keeps_both_in_review(self, api_client, app):  # noqa: F811
        invoice_id = await process(api_client, app, [row("A", "071990300173", 1, 10.0)], "still.pdf", subtotal=10.0, grand_total=99.0)
        r = await api_client.patch(f"/api/v1/invoices/{invoice_id}/totals",
                                   json={"grand_total": 50.0, "corrected_by": WHO, "note": "still wrong"})
        assert r.json()["data"]["status"] == "REVIEW_REQUIRED"
        assert await document_status(api_client, invoice_id) == "REVIEW_REQUIRED"

    async def test_a_line_item_correction_moves_the_document(self, api_client, app):  # noqa: F811
        # the model read one price wrong; the printed totals are right
        invoice_id = await process(api_client, app, [row("A", "071990300173", 1, 12.0)], "line.pdf", subtotal=10.0, grand_total=10.0)
        assert await document_status(api_client, invoice_id) == "REVIEW_REQUIRED"
        r = await api_client.patch(f"/api/v1/invoices/{invoice_id}/items/0",
                                   json={"unit_price": 10.0, "line_total": 10.0, "corrected_by": WHO, "note": "printed"})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["status"] == "VALIDATED"
        assert await document_status(api_client, invoice_id) == "COMPLETED"

    async def test_revalidating_directly_as_a_script_would_syncs_and_is_idempotent(self, api_client, app, db_session):  # noqa: F811
        from app.models.document import Document, DocumentStatus
        from app.repositories.invoice_repository import InvoiceRepository
        from app.services.revalidation_service import revalidate_invoice

        invoice_id = await process(api_client, app, [row("A", "071990300173", 1, 10.0)], "cli.pdf", subtotal=10.0, grand_total=10.0)
        invoice = await InvoiceRepository(db_session).get_detail(uuid.UUID(invoice_id))
        # a correction made outside the API, the way a CLI workflow would
        await InvoiceRepository(db_session).correct_totals(invoice, {"grand_total": Decimal("10.00")}, corrected_by=WHO, note="cli")
        for _ in range(2):
            report = await revalidate_invoice(db_session, invoice)
            assert report.decision.value == "VALIDATED" == invoice.status
            assert (await db_session.get(Document, invoice.document_id)).status == DocumentStatus.COMPLETED
        await db_session.commit()
        assert await document_status(api_client, invoice_id) == "COMPLETED"


# ---------------------------------------------------------------------------
# Backfill for rows that diverged before revalidation synced them
# ---------------------------------------------------------------------------


class TestDocumentStatusBackfill:
    async def test_repairs_only_divergent_terminal_pairs_and_changes_nothing_else(self, api_client, app, db_session):  # noqa: F811
        from app.models.document import Document, DocumentStatus
        from app.repositories.document_repository import DocumentRepository
        from app.services.document_lifecycle import (
            backfill_document_status,
            find_divergent_documents,
        )

        healthy = await process(api_client, app, [row("A", "071990300173", 1, 10.0)], "healthy.pdf", subtotal=10.0, grand_total=10.0)
        legacy = await process(api_client, app, [row("B", "071990480080", 2, 5.0)], "legacy.pdf", subtotal=10.0, grand_total=10.0)
        # reproduce the pre-fix state: invoice VALIDATED, document left in review
        legacy_doc = await db_session.get(Document, uuid.UUID((await detail(api_client, legacy))["document_id"]))
        await DocumentRepository(db_session).set_status(legacy_doc, DocumentStatus.REVIEW_REQUIRED)
        await db_session.commit()
        before = await detail(api_client, legacy)
        assert before["status"] == "VALIDATED" and await document_status(api_client, legacy) == "REVIEW_REQUIRED"

        planned = await find_divergent_documents(db_session)
        assert [str(p.invoice_id) for p in planned] == [legacy]
        assert (planned[0].old, planned[0].new) == (DocumentStatus.REVIEW_REQUIRED, DocumentStatus.COMPLETED)

        repaired = await backfill_document_status(db_session, apply=True)
        await db_session.commit()
        assert [str(r.invoice_id) for r in repaired] == [legacy]
        assert await document_status(api_client, legacy) == "COMPLETED"
        assert await document_status(api_client, healthy) == "COMPLETED"

        after = await detail(api_client, legacy)
        for field in ("grand_total", "subtotal", "deposit_total", "discount_amount", "corrected_fields",
                      "correction_history", "line_items", "case_mappings", "composite_confidence", "validation_report"):
            assert after[field] == before[field], field
        assert [e["event"] for e in await audit(api_client, legacy)] == ["document_status_synced"]

        # idempotent: a second run finds nothing
        assert await backfill_document_status(db_session, apply=True) == []
