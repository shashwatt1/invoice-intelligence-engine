"""
Document intake classification — app/services/document_intake.py

A batch of photographs is not a batch of invoices. Before anything is
structured, each image's extracted text is classified as one of:

  SUPPLIER_INVOICE          a supplier's invoice, one page
  MULTI_PAGE_INVOICE_PAGE   one page of an invoice that spans pages
  CREDIT_OR_RETURN          a credit memo / return / pickup document
  CHECK_OR_REMITTANCE       a cheque, remittance advice or payment stub
  PAID_OUT_OR_SALE_RECEIPT  a paid-out slip or a customer sale receipt
  OTHER                     none of the above (a statement, a note, a menu)
  DUPLICATE                 the same bytes, or the same page, as one already seen

and pages of one invoice are grouped, in order, under their supplier
and invoice number. Classification is by explicit words on the page,
weighed in a fixed order — never by an image looking like another one.
It carries NO write path: no invoice, no mapping, no EDI. Originals are
never modified; the caller passes text, not files.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

KIND_SUPPLIER_INVOICE = "SUPPLIER_INVOICE"
KIND_MULTI_PAGE = "MULTI_PAGE_INVOICE_PAGE"
KIND_CREDIT = "CREDIT_OR_RETURN"
KIND_CHECK = "CHECK_OR_REMITTANCE"
KIND_RECEIPT = "PAID_OUT_OR_SALE_RECEIPT"
KIND_OTHER = "OTHER"
KIND_DUPLICATE = "DUPLICATE"

_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", (text or "").upper()).strip()


# Each rule: (kind, phrases that decide it). Order matters — a credit memo
# also says "INVOICE", so credit is tested first; a cheque stub quotes an
# invoice number, so cheque is tested before invoice.
_RULES: list[tuple[str, tuple[str, ...]]] = [
    (KIND_CREDIT, ("CREDIT MEMO", "CREDIT NOTE", "CREDIT INVOICE", "RETURN AUTHORIZATION",
                   "PICKUP CREDIT", "PRODUCT RETURN", "RETURNED GOODS", "CREDIT ADJUSTMENT")),
    (KIND_CHECK, ("REMITTANCE ADVICE", "PAY TO THE ORDER OF", "CHECK NO", "CHECK NUMBER",
                  "CHEQUE", "VOID AFTER", "REMITTANCE", "PAYMENT STUB")),
    (KIND_RECEIPT, ("PAID OUT", "PAIDOUT", "CASH RECEIPT", "SALE RECEIPT", "SALES RECEIPT",
                    "CHANGE DUE", "TENDERED", "THANK YOU FOR SHOPPING", "REGISTER")),
    (KIND_SUPPLIER_INVOICE, ("INVOICE", "INV #", "INV NO", "INVOICE NO", "INVOICE #",
                             "DELIVERY TICKET", "PACKING SLIP", "SALES ORDER")),
]

_PAGE_OF = re.compile(r"\bPAGE\s*(\d{1,3})\s*(?:OF|/)\s*(\d{1,3})\b")
_INVOICE_NO = re.compile(
    r"\b(?:INVOICE|INV)\s*(?:NO\.?|NUMBER|#|:)?\s*[:#]?\s*([A-Z]?\d{4,12})\b"
)
_DATE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b")


@dataclass
class ClassifiedDocument:
    source: str                          # filename or page reference, as given
    content_hash: str
    kind: str
    supplier: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    customer: str | None = None
    page_number: int | None = None
    page_count: int | None = None
    duplicate_of: str | None = None
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class InvoiceGroup:
    """The pages of one invoice, in order."""

    supplier: str | None
    invoice_number: str | None
    invoice_date: str | None
    customer: str | None
    pages: list[ClassifiedDocument]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def complete(self) -> bool | None:
        """Whether every page the document announces is present; None if it announces none."""
        declared = {p.page_count for p in self.pages if p.page_count}
        if not declared:
            return None
        return len(self.pages) == max(declared) and \
            {p.page_number for p in self.pages} == set(range(1, max(declared) + 1))


def classify_text(
    text: str,
    *,
    source: str,
    content_hash: str,
    known_suppliers: list[str] | None = None,
    known_customers: list[str] | None = None,
) -> ClassifiedDocument:
    """Classify one page's extracted text. Pure; no I/O."""
    upper = _norm(text)
    evidence: list[str] = []
    kind = KIND_OTHER
    for candidate, phrases in _RULES:
        hit = next((p for p in phrases if p in upper), None)
        if hit:
            kind, evidence = candidate, [f"says {hit!r}"]
            break

    page_number = page_count = None
    m = _PAGE_OF.search(upper)
    if m:
        page_number, page_count = int(m.group(1)), int(m.group(2))
        evidence.append(f"page {page_number} of {page_count}")
        if kind == KIND_SUPPLIER_INVOICE and page_count > 1:
            kind = KIND_MULTI_PAGE

    invoice_number = None
    m = _INVOICE_NO.search(upper)
    if m and kind in (KIND_SUPPLIER_INVOICE, KIND_MULTI_PAGE, KIND_CREDIT):
        invoice_number = m.group(1)
        evidence.append(f"invoice number {invoice_number}")

    invoice_date = None
    m = _DATE.search(upper)
    if m:
        mm, dd, yy = m.groups()
        yy = yy if len(yy) == 4 else f"20{yy}"
        invoice_date = f"{yy}-{int(mm):02d}-{int(dd):02d}"

    supplier = next((s for s in (known_suppliers or []) if _norm(s) in upper), None)
    if supplier:
        evidence.append(f"supplier {supplier!r}")
    customer = next((c for c in (known_customers or []) if _norm(c) in upper), None)
    if customer:
        evidence.append(f"customer {customer!r}")

    return ClassifiedDocument(
        source=source, content_hash=content_hash, kind=kind, supplier=supplier,
        invoice_number=invoice_number, invoice_date=invoice_date, customer=customer,
        page_number=page_number, page_count=page_count, evidence=evidence,
    )


def mark_duplicates(documents: list[ClassifiedDocument]) -> list[ClassifiedDocument]:
    """
    The same bytes twice, or the same invoice page twice, is one page.
    The first occurrence stands; later ones become DUPLICATE and say of
    what. Order is the order given.
    """
    seen_hash: dict[str, str] = {}
    seen_page: dict[tuple, str] = {}
    for doc in documents:
        if doc.content_hash in seen_hash:
            doc.kind, doc.duplicate_of = KIND_DUPLICATE, seen_hash[doc.content_hash]
            doc.evidence.append("identical bytes")
            continue
        seen_hash[doc.content_hash] = doc.source
        if doc.invoice_number and doc.kind in (KIND_SUPPLIER_INVOICE, KIND_MULTI_PAGE):
            key = (doc.supplier, doc.invoice_number, doc.page_number)
            if key in seen_page:
                doc.kind, doc.duplicate_of = KIND_DUPLICATE, seen_page[key]
                doc.evidence.append("same invoice page already seen")
                continue
            seen_page[key] = doc.source
    return documents


def group_invoice_pages(documents: list[ClassifiedDocument]) -> list[InvoiceGroup]:
    """
    Pages of one invoice together, in page order. A page with no invoice
    number cannot be grouped and stands alone. Duplicates are excluded.
    """
    buckets: dict[tuple, list[ClassifiedDocument]] = defaultdict(list)
    singles: list[InvoiceGroup] = []
    for doc in documents:
        if doc.kind not in (KIND_SUPPLIER_INVOICE, KIND_MULTI_PAGE):
            continue
        if not doc.invoice_number:
            singles.append(InvoiceGroup(doc.supplier, None, doc.invoice_date, doc.customer, [doc]))
            continue
        buckets[(doc.supplier, doc.invoice_number)].append(doc)
    groups = []
    for (supplier, number), pages in buckets.items():
        pages.sort(key=lambda p: (p.page_number is None, p.page_number or 0, p.source))
        groups.append(InvoiceGroup(
            supplier=supplier, invoice_number=number,
            invoice_date=next((p.invoice_date for p in pages if p.invoice_date), None),
            customer=next((p.customer for p in pages if p.customer), None),
            pages=pages,
        ))
    return groups + singles


def summarize(documents: list[ClassifiedDocument]) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    for doc in documents:
        counts[doc.kind] += 1
    groups = group_invoice_pages(documents)
    return {
        "documents": len(documents),
        "by_kind": dict(counts),
        "invoices": len(groups),
        "multi_page_invoices": sum(1 for g in groups if g.page_count > 1),
        "incomplete_invoices": [g.invoice_number for g in groups if g.complete is False],
    }
