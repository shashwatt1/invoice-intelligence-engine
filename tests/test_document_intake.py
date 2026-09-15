"""
tests/test_document_intake.py — classifying a photo batch without
processing it.

Every text here is synthetic. The real 'Invoices HO' photographs are
never opened: the classifier takes text, and these tests pin that a
run over the batch would touch nothing on disk and write nothing.
"""

from __future__ import annotations

from pathlib import Path

from app.services.document_intake import (
    KIND_CHECK,
    KIND_CREDIT,
    KIND_DUPLICATE,
    KIND_MULTI_PAGE,
    KIND_OTHER,
    KIND_RECEIPT,
    KIND_SUPPLIER_INVOICE,
    classify_text,
    group_invoice_pages,
    mark_duplicates,
    summarize,
)

SUPPLIERS = ["Rocco J. Testani", "Balkan Beverage", "Monarch Beverage"]
CUSTOMERS = ["PB Wolf Group Inc", "Apple Foods II"]


def doc(text, source="p.jpg", h=None, **kw):
    return classify_text(text, source=source, content_hash=h or source, known_suppliers=SUPPLIERS,
                         known_customers=CUSTOMERS, **kw)


class TestKinds:
    def test_a_supplier_invoice(self):
        d = doc("ROCCO J. TESTANI INC  INVOICE NO 228245  DATE 08/28/2026  SOLD TO PB WOLF GROUP INC")
        assert d.kind == KIND_SUPPLIER_INVOICE
        assert (d.supplier, d.invoice_number, d.invoice_date, d.customer) == \
            ("Rocco J. Testani", "228245", "2026-08-28", "PB Wolf Group Inc")

    def test_a_page_of_a_multi_page_invoice(self):
        d = doc("BALKAN BEVERAGE INVOICE # 3376587 PAGE 2 OF 3")
        assert d.kind == KIND_MULTI_PAGE and (d.page_number, d.page_count) == (2, 3)

    def test_a_credit_memo_is_not_an_invoice_even_though_it_says_invoice(self):
        d = doc("MONARCH BEVERAGE CREDIT MEMO ref invoice 4471 returned goods")
        assert d.kind == KIND_CREDIT

    def test_a_cheque_stub_quoting_an_invoice_is_a_cheque(self):
        d = doc("PAY TO THE ORDER OF Balkan Beverage  $273.66  memo: invoice 3376587  CHECK NO 1042")
        assert d.kind == KIND_CHECK and d.invoice_number is None

    def test_paid_out_and_sale_receipts(self):
        assert doc("PAID OUT  cash  $40.00  ice").kind == KIND_RECEIPT
        assert doc("REGISTER 2  TOTAL 12.99  TENDERED 20.00  CHANGE DUE 7.01").kind == KIND_RECEIPT

    def test_anything_else_is_other(self):
        assert doc("weekly specials  bananas 0.49/lb").kind == KIND_OTHER


class TestDuplicatesAndGrouping:
    def test_identical_bytes_are_one_page(self):
        a = doc("TESTANI INVOICE NO 228245", "IMG_1.jpg", h="abc")
        b = doc("TESTANI INVOICE NO 228245", "IMG_2.jpg", h="abc")
        marked = mark_duplicates([a, b])
        assert marked[1].kind == KIND_DUPLICATE and marked[1].duplicate_of == "IMG_1.jpg"

    def test_the_same_invoice_page_photographed_twice_is_one_page(self):
        a = doc("ROCCO J. TESTANI INVOICE NO 228245 PAGE 1 OF 2", "IMG_1.jpg", h="h1")
        b = doc("ROCCO J. TESTANI INVOICE NO 228245 PAGE 1 OF 2", "IMG_2.jpg", h="h2")   # re-shot
        c = doc("ROCCO J. TESTANI INVOICE NO 228245 PAGE 2 OF 2", "IMG_3.jpg", h="h3")
        marked = mark_duplicates([a, b, c])
        assert [m.kind for m in marked] == [KIND_MULTI_PAGE, KIND_DUPLICATE, KIND_MULTI_PAGE]
        [group] = group_invoice_pages(marked)
        assert [p.source for p in group.pages] == ["IMG_1.jpg", "IMG_3.jpg"]
        assert group.page_count == 2 and group.complete is True

    def test_pages_are_grouped_by_supplier_and_number_in_page_order(self):
        docs = [
            doc("BALKAN BEVERAGE INVOICE # 3376587 PAGE 2 OF 2", "b2.jpg"),
            doc("ROCCO J. TESTANI INVOICE NO 228245", "t1.jpg"),
            doc("BALKAN BEVERAGE INVOICE # 3376587 PAGE 1 OF 2 SOLD TO APPLE FOODS II 09/01/2026", "b1.jpg"),
            doc("MONARCH BEVERAGE CREDIT MEMO 5", "c.jpg"),
        ]
        groups = {(g.supplier, g.invoice_number): g for g in group_invoice_pages(mark_duplicates(docs))}
        balkan = groups[("Balkan Beverage", "3376587")]
        assert [p.source for p in balkan.pages] == ["b1.jpg", "b2.jpg"]
        assert (balkan.customer, balkan.invoice_date, balkan.complete) == ("Apple Foods II", "2026-09-01", True)
        assert groups[("Rocco J. Testani", "228245")].page_count == 1
        assert ("Monarch Beverage", "5") not in groups                    # credits are not invoices

    def test_a_missing_page_is_reported_not_papered_over(self):
        docs = [doc("BALKAN BEVERAGE INVOICE # 3376587 PAGE 1 OF 3", "b1.jpg"),
                doc("BALKAN BEVERAGE INVOICE # 3376587 PAGE 3 OF 3", "b3.jpg")]
        [group] = group_invoice_pages(docs)
        assert group.complete is False
        assert summarize(docs)["incomplete_invoices"] == ["3376587"]


class TestTheRealPhotosAreNotTouched:
    def test_the_classifier_has_no_file_or_database_access(self):
        import inspect

        from app.services import document_intake

        source = inspect.getsource(document_intake)
        for forbidden in ("open(", "Path(", "os.", "sqlalchemy", "session", "Repository", "PIL", "vision"):
            assert forbidden not in source, forbidden

    def test_the_photo_batch_if_present_is_left_exactly_as_it_is(self):
        folder = Path("Invoices HO")
        if not folder.is_dir():
            return
        before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in folder.iterdir()}
        # a classification run is text-only; nothing here reads the images
        summarize([doc("INVOICE NO 1 synthetic", "synthetic.jpg")])
        after = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in folder.iterdir()}
        assert after == before
