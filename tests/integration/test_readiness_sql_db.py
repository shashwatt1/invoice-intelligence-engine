"""
The production readiness SQL classifies codes exactly as the application does —
tests/integration/test_readiness_sql_db.py

docs/sql/product-master-0038-readiness-followup.sql re-expresses app/services/gtin.py (classify) in SQL, because
the release owner runs it in the Supabase SQL editor. This test is what keeps the two from drifting: it seeds one
line for every kind of printed code (plus a large generated set), runs the file's OWN blocks 5b, 8 and 10 on
Postgres, and compares every row with Python's classification. It also checks the SQL is read-only.

Synthetic codes only.
"""

from __future__ import annotations

import random
import re
import uuid
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from app.models.document import Document
from app.models.edi_export import EdiExport
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.models.vendor import Vendor
from app.services import gtin
from tests.integration.conftest import PHYSICAL_STORE, requires_db, store_id

pytestmark = requires_db

SQL = Path(__file__).resolve().parents[2] / "docs" / "sql" / "product-master-0038-readiness-followup.sql"
BLOCKED = {gtin.KIND_SHORT, gtin.KIND_UPC_A_BAD_CHECK, gtin.KIND_BAD_CHECK, gtin.KIND_EAN_13, gtin.KIND_GTIN_14,
           gtin.KIND_TOO_LONG}
KEY_CHANGES = {gtin.KIND_EAN13_UPC, gtin.KIND_GTIN14_UPC}
AFFECTED = BLOCKED | KEY_CHANGES


def blocks() -> dict[str, str]:
    """{'5b': sql, '8': sql, ...} — each block's statement with comments removed."""
    found: dict[str, list[str]] = {}
    current = None
    for line in SQL.read_text().splitlines():
        header = re.match(r"^-- (\d+b?)\. ", line)
        if header:
            current = header.group(1)
            found[current] = []
        elif current and not line.lstrip().startswith("--"):
            found[current].append(line)
    return {k: "\n".join(v).strip().rstrip(";") for k, v in found.items()}


def check_digit(body: str) -> str:
    return gtin.gtin_check_digit(body)


def generated_codes(seed: int = 7) -> list[str]:
    """Every length 1..16, valid and deliberately wrong check digits, leading-zero and non-zero prefixes."""
    rng = random.Random(seed)
    codes: list[str] = []
    for length in range(1, 17):
        for _ in range(12):
            body = "".join(rng.choice("0123456789") for _ in range(max(length - 1, 0)))
            if length >= 2:
                good = body + check_digit(body)
                bad = body + str((int(check_digit(body)) + 1 + rng.randrange(9)) % 10)
                codes += [good, bad]
                if length in (13, 14):               # force the 0 / 00 prefixes that make a UPC-A
                    prefix = "0" * (length - 12)
                    inner = "".join(rng.choice("0123456789") for _ in range(11))
                    upc = inner + check_digit(inner)
                    codes += [prefix + upc, prefix + inner + str((int(check_digit(inner)) + 3) % 10)]
            else:
                codes.append(body or "5")
    codes += ["000000000000", "0", "00000000000000"]
    return [c for c in dict.fromkeys(codes) if c.strip("0")]   # placeholders are checked separately


def python_kind(code: str) -> str:
    return gtin.classify(code).kind


async def seed(db_session, codes: list[str], *, status: str) -> str:
    """One invoice per call: 'open', 'exported' or 'retailed'. Returns its invoice number."""
    number = f"RS-{status}-{uuid.uuid4().hex[:6]}"
    sid = store_id(PHYSICAL_STORE)
    vendor = Vendor(name=f"Readiness Vendor {status}")
    db_session.add(vendor)
    doc = Document(filename=number + ".pdf", mime_type="application/pdf", file_size_bytes=1,
                   file_path=f"readiness/{number}.pdf", file_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                   status="COMPLETED", store_id=sid)
    db_session.add(doc)
    await db_session.flush()
    invoice = Invoice(document_id=doc.id, vendor_id=vendor.id, store_id=sid, currency="USD", status="VALIDATED",
                      invoice_number=number)
    db_session.add(invoice)
    await db_session.flush()
    for i, code in enumerate(codes):
        db_session.add(InvoiceItem(invoice_id=invoice.id, description=f"LINE {i}", quantity=Decimal("1"),
                                   unit_price=Decimal("1"), line_total=Decimal("1"), product_sku=code, sort_order=i,
                                   line_type="product"))
    if status in ("exported", "retailed"):
        export = EdiExport(invoice_id=invoice.id, document_id=doc.id, invoice_number=number, export_number=1,
                           format="PDI", format_version="1", exported_by="seed", exported_by_role="ADMIN",
                           sha256="0" * 64, byte_size=1, content="x", resolution_snapshot={}, build={})
        db_session.add(export)
        await db_session.flush()
        if status == "retailed":
            invoice.retailed_at, invoice.retailed_by = datetime.now(UTC), "seed"
            invoice.retailed_edi_export_id, invoice.retailed_attestation_sha256 = export.id, "1" * 64
    await db_session.commit()
    return number


async def run(db_session, block: str):
    return (await db_session.execute(text(blocks()[block]))).mappings().all()


@pytest.fixture(autouse=True)
async def clean(db_session):
    await db_session.execute(text("TRUNCATE edi_exports CASCADE"))
    await db_session.commit()


class TestTheSqlIsReadOnly:
    def test_every_block_is_a_single_select(self):
        found = blocks()
        assert set(found) == {"5b", "8", "9", "10", "11", "12", "13", "13b", "14"}
        for name, sql in found.items():
            assert sql.upper().startswith(("SELECT", "WITH")), name
            assert ";" not in sql, f"block {name} must be one statement"
            assert not re.search(r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|call)\b",
                                 sql, re.I), name


class TestItMirrorsTheApplicationsClassifier:
    async def test_block_10_gives_every_generated_code_the_kind_python_gives_it(self, db_session):
        codes = generated_codes()
        await seed(db_session, codes, status="open")
        rows = await run(db_session, "10")
        by_digits: dict[str, str] = {}
        for r in rows:
            by_digits[r["digits_printed"]] = r["kind"]
        expected = {c: python_kind(c) for c in codes if python_kind(c) in AFFECTED}
        assert by_digits == expected, {c: (by_digits.get(c), k) for c, k in expected.items() if by_digits.get(c) != k}
        assert {r["effect"] for r in rows} <= {"BLOCKED", "KEY_CHANGES"}
        for r in rows:
            assert r["effect"] == ("BLOCKED" if r["kind"] in BLOCKED else "KEY_CHANGES")

    async def test_the_codes_that_export_fine_are_not_listed(self, db_session):
        ok = ["036000291452", "03600029145", "0036000291452", "00036000291452"]  # UPC-A, 11 digits, 0+UPC-A, 00+UPC-A
        assert [python_kind(c) for c in ok] == ["UPC_A", "PDI_ITEM_CODE", "EAN13_UPC", "GTIN14_UPC"]
        await seed(db_session, ok, status="open")
        listed = {r["digits_printed"]: r["kind"] for r in await run(db_session, "10")}
        assert listed == {"0036000291452": "EAN13_UPC", "00036000291452": "GTIN14_UPC"}   # key changes, not blocked

    async def test_the_named_cases(self, db_session):
        cases = {
            "036000291452": None,                   # valid UPC-A: unaffected
            "036000291453": "UPC_A_BAD_CHECK",      # wrong check digit
            "0036000291452": "EAN13_UPC",           # leading-zero EAN-13 wrapping a UPC-A
            "4006381333931": "EAN_13",              # a real EAN-13
            "4006381333932": "BAD_CHECK",           # 13 digits, wrong check
            "00036000291452": "GTIN14_UPC",         # 00 + UPC-A
            "10036000291459": "GTIN_14",            # case code
            "12345678": "SHORT",                    # vendor number
            "1234567890123456": "TOO_LONG",         # 16 digits
        }
        assert {c: (python_kind(c) if python_kind(c) in AFFECTED else None) for c in cases} == cases
        await seed(db_session, list(cases), status="open")
        listed = {r["digits_printed"]: r["kind"] for r in await run(db_session, "10")}
        assert listed == {c: k for c, k in cases.items() if k}


class TestTheAggregateBlocks:
    async def test_5b_counts_only_open_invoices_and_8_only_exported_or_retailed(self, db_session):
        codes = ["036000291452", "036000291453", "0036000291452", "4006381333931", "12345678"]
        await seed(db_session, codes, status="open")
        await seed(db_session, codes, status="exported")
        await seed(db_session, codes, status="retailed")
        five = {(r["kind"], r["effect"]): (r["lines"], r["invoices"]) for r in await run(db_session, "5b")
                if r["vendor"] == "Readiness Vendor open"}
        assert five == {("UPC_A_BAD_CHECK", "BLOCKED"): (1, 1), ("EAN13_UPC", "KEY_CHANGES"): (1, 1),
                        ("EAN_13", "BLOCKED"): (1, 1), ("SHORT", "BLOCKED"): (1, 1)}
        assert not [r for r in await run(db_session, "5b") if r["vendor"] != "Readiness Vendor open"]
        eight = Counter((r["status"], r["kind"], r["effect"]) for r in await run(db_session, "8"))
        assert eight == Counter({(s, k, e): 1 for s in ("RETAILED", "exported, not retailed")
                                 for k, e in (("UPC_A_BAD_CHECK", "BLOCKED"), ("EAN13_UPC", "KEY_CHANGES"),
                                              ("EAN_13", "BLOCKED"), ("SHORT", "BLOCKED"))})

    async def test_voided_charge_zero_quantity_and_binned_lines_are_ignored(self, db_session):
        number = await seed(db_session, ["12345678"] * 4, status="open")
        await db_session.execute(text(
            "UPDATE invoice_items SET line_type = CASE sort_order WHEN 0 THEN 'charge' WHEN 1 THEN 'voided' "
            "WHEN 2 THEN 'duplicate' ELSE line_type END, quantity = CASE sort_order WHEN 3 THEN 0 ELSE quantity END "
            "WHERE invoice_id = (SELECT id FROM invoices WHERE invoice_number = :n)"), {"n": number})
        await db_session.commit()
        assert await run(db_session, "10") == []
        number2 = await seed(db_session, ["12345678"], status="open")
        await db_session.execute(text(
            "UPDATE documents SET status = 'BINNED' WHERE id = (SELECT document_id FROM invoices "
            "WHERE invoice_number = :n)"), {"n": number2})
        await db_session.commit()
        assert await run(db_session, "10") == []

    async def test_blocks_9_and_11_run_and_report_what_they_say(self, db_session):
        counts = {r["t"]: r["n"] for r in await run(db_session, "11")}
        assert counts["documents"] == 0 and "user_store_access" in counts and len(counts) == 9
        assert await run(db_session, "9") == []


class TestBlocks12To14:
    async def test_block_12_names_the_retailed_invoice_its_receipts_and_exports(self, db_session):
        number = await seed(db_session, ["049000007641", "049000007640"], status="retailed")
        rows = await run(db_session, "12")
        assert [(r["invoice_number"], r["digits_printed"], r["recorded_exports"]) for r in rows] == [
            (number, "049000007641", 1), (number, "049000007640", 1)]
        assert rows[0]["receipts_by_status"] is None          # no receipt seeded

    async def test_block_13b_gives_the_three_schema_fingerprints(self, db_session):
        rows = {r["t"]: r for r in await run(db_session, "13b")}
        assert set(rows) == {"schema: columns", "schema: constraints", "schema: indexes"}
        assert all(r["n"] > 0 and len(r["fingerprint"]) == 32 for r in rows.values())

    async def test_block_14_lists_lines_that_rest_only_on_the_global_value(self, db_session):
        from app.models.product_master import MasterCommercialMapping
        from app.services.product_master import shared_decisions
        from tests.integration.conftest import SOURCE_ONLY_STORE

        product, _ = await shared_decisions.find_or_create_product(db_session, "01820096721")
        db_session.add(MasterCommercialMapping(
            product_id=product.id, store_id=store_id(SOURCE_ONLY_STORE), pdi_item_code="01820096721",
            commercial_unit_basis="CASE_IS_SELLING_UNIT", units_accounted_for=1, approval_state="APPROVED",
            source_snapshot={}, evidence={}, source_system="distributor_price_sheet"))
        await db_session.commit()
        opened = await seed(db_session, ["018200967214", "036000291452"], status="open")
        retailed = await seed(db_session, ["018200967214"], status="retailed")
        got = sorted((r["invoice_number"], r["status"], r["pdi_item_code"], r["global_units"])
                     for r in await run(db_session, "14"))
        assert got == sorted([(opened, "open", "01820096721", 1), (retailed, "RETAILED", "01820096721", 1)])
        await db_session.execute(text(
            "INSERT INTO product_case_mappings (store_id, item_code, units_per_case, source) "
            "VALUES (:s, '01820096721', 1, 'APPROVED')"), {"s": store_id(PHYSICAL_STORE)})
        await db_session.commit()
        assert await run(db_session, "14") == []              # the store now holds its own value
