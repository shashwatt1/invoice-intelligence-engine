"""
A synthetic copy of the benchmark's SHAPE — tests/synthetic_invoice.py

Built to mirror a real 61-row, 15-section distributor invoice photographed in four overlapping pieces, with none
of its data: every code, name and price is invented, but the arithmetic is the benchmark's exactly:

    61 product lines, 68 cases, 958 units, merchandise subtotal 1,821.18, sales tax 0.00,
    state/local charges 23.30 (a per-package container deposit), amount due 1,844.48.

`SECTIONS` is the source of truth; `benchmark_lines()` are the correct extracted rows, `ocr_text()` renders the
flat, jumbled-but-recoverable text an OCR engine produces (hyphenated UPCs, per-row "cases units net amount",
"SUBTOTAL cases units" per section, a Sales Summary), and `PHOTO_BLOCKS` splits it into overlapping photographs.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.services.gtin import gtin_check_digit

# (heading, units per case N, rows[(description, cases, net unit price per case)])
SECTIONS = [
    ("11OZ PL 1/12 TBG", 12, [("SYN TROP ORANGE", 1, "26.86")]),
    ("12OZ CN 1/12 SLK", 12, [(f"SYN SLEEK {i}", 1, "21.75") for i in range(1, 5)]),
    ("12OZCN1/12SLCEL", 12, [(f"SYN CELSIUS {i}", 1, "19.10") for i in range(1, 7)]),
    ("13.7OZ NR 1/12", 12, [(f"SYN FRAP {i}", 1, "31.67") for i in range(1, 4)]),
    ("14OZ PL 1/12 MM", 12, [("SYN MM VANILLA", 1, "28.89")]),
    ("15OZ CN 1/12", 12, [(f"SYN DOUBLESHOT {i}", 1, "31.96") for i in range(1, 3)]),
    ("16OZ CN 1/12 SCP", 12, [
        ("SYN DEW A", 1, "19.80"), ("SYN COLA B", 1, "19.80"), ("SYN COLA C", 1, "19.80"),
        ("SYN ENERGY CHERRY", 2, "24.00"), ("SYN ENERGY ORANGE", 1, "24.00"), ("SYN DEW KS MANGO", 2, "21.18"),
    ]),
    ("16OZCN1/12SLCEL", 12, [(f"SYN ESSENTIAL {i}", 1, "21.75") for i in range(1, 4)]),
    ("18.5OZ PL 1/12", 12, [(f"SYN LIPTON {i}", 1, "21.22") for i in range(1, 3)]),
    ("1L PL 1/15S", 15, [("SYN COLA 1L", 1, "27.46"), ("SYN DEW 1L", 1, "27.46")]
     + [(f"SYN BRISK {i}", 1, "18.19") for i in range(1, 5)]),
    ("20OZ PL 1/12PRPL", 12, [(f"SYN PURPLE {i}", 1, "17.81") for i in range(1, 5)]),
    ("20OZ PL 1/24S", 24, [(f"SYN 20OZ {i}", 1, "38.93") for i in range(1, 6)]),
    ("28OZ PL 1/15", 15, [(f"SYN SPORT {i}", c, "31.78") for i, c in enumerate(
        [1, 1, 1, 1, 1, 1, 1, 2, 1, 2, 2, 2, 1, 2, 1], start=1)]),
    ("2L PL 1/8S", 8, [(f"SYN 2L {i}", 1, "22.17") for i in range(1, 3)]),
    ("24OZ PL 1/24GAT", 24, [("SYN SPORT 24OZ", 1, "49.94")]),
]
DEPOSIT_BY_SECTION = [("2L PL 1/8S", "0.80"), ("20OZ PL 1/24S", "6.00"), ("12OZCN1/12SLCEL", "3.60"),
                      ("16OZ CN 1/12 SCP", "4.80"), ("1L PL 1/15S", "1.50"), ("16OZCN1/12SLCEL", "1.80"),
                      ("20OZ PL 1/12PRPL", "2.40"), ("12OZ CN 1/12 SLK", "2.40")]

LINES_TOTAL, CASES_TOTAL, UNITS_TOTAL = 61, 68, 958
SUBTOTAL, CHARGES, AMOUNT_DUE = Decimal("1821.18"), Decimal("23.30"), Decimal("1844.48")


def _upc(n: int) -> str:
    body = f"9{n:010d}"                            # 11 digits, distinct in their last digits like sibling products
    return body + gtin_check_digit(body)


def _hyphen(upc: str) -> str:
    return f"{upc[0]}-{upc[1:6]}-{upc[6:11]}-{upc[11]}"


def benchmark_lines():
    """The correct extracted rows: one SimpleNamespace per printed row, in document order."""
    rows, n = [], 0
    for heading, per_case, items in SECTIONS:
        for description, cases, net in items:
            n += 1
            rows.append(SimpleNamespace(
                line_type="product", description=description, product_code=_upc(n), pack_size=heading,
                section_label=heading, quantity=Decimal(cases), unit_price=Decimal(net),
                line_total=Decimal(net) * cases, printed_units=cases * per_case, sort_order=n - 1))
    return rows


def _section_block(index: int) -> str:
    heading, per_case, items = SECTIONS[index]
    lines = [heading]
    rows = [r for r in benchmark_lines() if r.section_label == heading]
    for r in rows:
        lines += [f"{r.description} {_hyphen(r.product_code)}", "40.00",
                  f"{int(r.quantity)} {r.printed_units} {r.unit_price} {r.line_total}"]
    cases = sum(int(r.quantity) for r in rows)
    lines += [f"SUBTOTAL {cases} {cases * per_case}", str(sum(r.line_total for r in rows))]
    return "\n".join(lines)


HEADER_BLOCK = "\n".join([
    "SYN BEVERAGE CO", "140 EXAMPLE RD", "INVOICE", "#10000001", "SALES SUMMARY", "Qty", "Amount", "Sales", "Cases",
    str(CASES_TOTAL), str(SUBTOTAL), "Total Units", str(UNITS_TOTAL), "Credits", "Container Deposits", "0.00",
    "Sales Tax", "0.00", "State/Local Charges", str(CHARGES), "Amount Due", f"$ {AMOUNT_DUE}", "ITEM DETAIL", "SALES"])
CHARGES_BLOCK = "\n".join(
    ["STATE/LOCAL CHARGES", "Package Amount", "NY St Deposit @ .05"]
    + [f"{h} {a}" for h, a in DEPOSIT_BY_SECTION] + [f"SUBTOTAL {CHARGES}", "Amount Due", f"for this Invoice: $ {AMOUNT_DUE}"])

# Four overlapping photographs, top to bottom: each repeats the last section or two of the one above.
PHOTO_BLOCKS = [
    [HEADER_BLOCK, *[_section_block(i) for i in range(0, 5)]],
    [_section_block(i) for i in range(4, 9)],
    [_section_block(i) for i in range(8, 13)],
    [_section_block(i) for i in range(12, 15)] + [CHARGES_BLOCK],
]
FILENAMES = ["IMG at 21.18.04.jpeg", "IMG at 21.18.17.jpeg", "IMG at 21.18.29.jpeg", "IMG at 21.18.39.jpeg"]


def ocr_text(upload_order: list[int] | None = None) -> str:
    """
    The combined OCR text as the pipeline stores it. The invoice's photographs are numbered 1..4 top to bottom
    (1 = header). `upload_order` lists which of them was uploaded first, second, ...: the default [1, 2, 3, 4] is a
    top-to-bottom upload; [4, 3, 2, 1] is the bottom-first upload that cost the real invoice a third of its rows.
    A section's number is its UPLOAD position, exactly as DocumentPage.page_number is.
    """
    upload_order = upload_order or [1, 2, 3, 4]
    return "\n\n".join(
        f"--- PHOTO {position} of 4 ({FILENAMES[content - 1]}) ---\n" + "\n".join(PHOTO_BLOCKS[content - 1])
        for position, content in enumerate(upload_order, start=1))
