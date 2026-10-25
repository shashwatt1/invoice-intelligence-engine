"""
tests/integration/test_mapping_coverage_report_db.py — the coverage report end to end, on real Postgres.

Builds, in the isolated test database, a physical store, invoices at a
physical store, at a code-only store (86357232) and with no store yet, and
Product Master / legacy data that drives every category. Then runs the
report exactly as the CLI does — its own READ ONLY connection, the live
resolver — with Product Master resolution on and off.

Also proves the session refuses writes and that no description or price
reaches the JSON.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.document import Document
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.models.product_case_mapping import ProductCaseMapping
from app.models.product_master import (
    MasterCommercialMapping,
    MasterProduct,
    MasterProductIdentifier,
)
from app.models.store import Store
from app.services.product_master.identifiers import upc_a_check_digit
from scripts import mapping_coverage_report as report
from tests.integration.conftest import TEST_URL, requires_db, store_id

pytestmark = requires_db

CODES = {name: f"0999990000{n}" for n, name in enumerate("ABCDEFGHI", start=1)}
PRINTED = {name: code + upc_a_check_digit(code) for name, code in CODES.items()}
SECRET_DESCRIPTION, SECRET_PRICE = "SECRET DESCRIPTION", 98.76


def _item(invoice_id, sort, name=None, *, sku=None, line_type="product", quantity=1):
    return InvoiceItem(invoice_id=invoice_id, sort_order=sort, description=f"{SECRET_DESCRIPTION} {sort}",
                       quantity=quantity, unit_price=SECRET_PRICE, line_total=SECRET_PRICE,
                       product_sku=PRINTED[name] if name else sku, line_type=line_type)


async def _invoice(session, number, store):
    document = Document(filename=f"{number}.pdf", mime_type="application/pdf", file_size_bytes=1,
                        file_path=f"test/{number}.pdf", file_hash=uuid.uuid4().hex)
    session.add(document)
    await session.flush()
    invoice = Invoice(document_id=document.id, store_id=store, invoice_number=number, status="VALIDATED")
    session.add(invoice)
    await session.flush()
    return invoice


@pytest.fixture
async def corpus(db_session):
    physical = Store(display_name="AF Test Physical", identity_status="confirmed")
    db_session.add(physical)
    await db_session.flush()
    holder, code_only = store_id("47708760"), store_id("86357232")

    products = {}
    for name, printed in PRINTED.items():
        products[name] = MasterProduct(canonical_key=f"upc:{printed}", canonical_upc=printed, identity_basis="UPC_A")
        db_session.add(products[name])
    await db_session.flush()
    db_session.add(MasterProductIdentifier(product_id=products["G"].id, raw_value=PRINTED["G"],
                                           normalized_value=PRINTED["G"], identifier_type="UPC_A",
                                           source_system="test"))

    def pm(name, store, units, state="APPROVED"):
        db_session.add(MasterCommercialMapping(product_id=products[name].id, store_id=store, pdi_item_code=CODES[name],
                                               commercial_unit_basis="CASE_IS_SELLING_UNIT", units_accounted_for=units,
                                               approval_state=state, source_system="test"))
    pm("A", holder, 24)                                   # global
    pm("B", holder, 12), pm("B", physical.id, 12)         # physical override, agreeing
    pm("C", holder, 24), pm("C", physical.id, 12)         # physical vs global disagree
    pm("E", holder, 24)                                   # wins over legacy 1
    pm("F", holder, 6, state="REVIEW_REQUIRED")           # awaiting review
    pm("I", holder, 6), pm("I", holder, 6)                # two approved in one scope
    for name, units in (("D", 6), ("E", 1)):
        db_session.add(ProductCaseMapping(store_id=physical.id, item_code=CODES[name], units_per_case=units,
                                          source="APPROVED"))

    at_physical = await _invoice(db_session, "T-PHYS", physical.id)
    lines = [_item(at_physical.id, n, name) for n, name in enumerate("ABCDEFGHI", start=1)]
    lines += [_item(at_physical.id, 10, sku=None),                                   # no usable code
              _item(at_physical.id, 11, sku="FUEL", line_type="charge"),            # not a PDI line
              _item(at_physical.id, 12, "H", quantity=0),                           # shorted
              _item(at_physical.id, 13, "H", line_type="voided")]                   # voided
    at_code_only = await _invoice(db_session, "T-CODE", code_only)
    lines += [_item(at_code_only.id, 1, "A"), _item(at_code_only.id, 2, "B"), _item(at_code_only.id, 3, "D")]
    pending = await _invoice(db_session, "T-PEND", None)
    lines += [_item(pending.id, 1, "A")]
    db_session.add_all(lines)
    await db_session.commit()
    yield
    await db_session.execute(text("TRUNCATE master_products CASCADE"))
    await db_session.commit()


@pytest.fixture
def resolver_settings(monkeypatch):
    def set_flag(on: bool):
        monkeypatch.setenv("PRODUCT_MASTER_COMMERCIAL_RESOLUTION", "true" if on else "false")
        monkeypatch.setenv("COMMERCIAL_SOURCE_IDENTITY", "47708760")
        get_settings.cache_clear()
    yield set_flag
    get_settings.cache_clear()


async def _run():
    return await report.run(report.target_url(TEST_URL, "invoice_test"), None)


def _by_line(result):
    return {(line["invoice_number"], line["sort_order"]): line for line in result["lines"]}


class TestTheReportOnRealData:
    async def test_with_product_master_resolution_on(self, corpus, resolver_settings):
        resolver_settings(True)
        result = await _run()
        lines = _by_line(result)
        got = {k: (v["category"], v["sub_reason"], v["units_per_case"]) for k, v in lines.items()}
        assert got == {
            ("T-PHYS", 1): ("PRODUCT_MASTER_GLOBAL", None, 24),
            ("T-PHYS", 2): ("PRODUCT_MASTER_PHYSICAL_STORE", None, 12),
            ("T-PHYS", 3): ("CONFLICT", "physical_global_disagree", None),
            ("T-PHYS", 4): ("LEGACY_FALLBACK", None, 6),
            ("T-PHYS", 5): ("PRODUCT_MASTER_GLOBAL", None, 24),
            ("T-PHYS", 6): ("REQUIRES_MAPPING", "product_master_mapping_not_approved", None),
            ("T-PHYS", 7): ("REQUIRES_MAPPING", "identity_known_no_conversion", None),
            ("T-PHYS", 8): ("REQUIRES_MAPPING", "absent_from_master_corpus", None),
            ("T-PHYS", 9): ("CONFLICT", "multiple_approved_in_scope", None),
            ("T-PHYS", 10): ("NO_USABLE_CODE", None, None),
            # code-only store: no physical scope, the physical override for B is not borrowed,
            # and the physical store's legacy row for D does not apply here
            ("T-CODE", 1): ("PRODUCT_MASTER_GLOBAL", None, 24),
            ("T-CODE", 2): ("PRODUCT_MASTER_GLOBAL", None, 12),
            ("T-CODE", 3): ("REQUIRES_MAPPING", "absent_from_master_corpus", None),
            ("T-PEND", 1): ("STORE_PENDING", None, None),
        }
        assert lines[("T-PHYS", 5)]["legacy_units"] == 1 and lines[("T-PHYS", 5)]["legacy_disagreement"] is True
        assert lines[("T-CODE", 1)]["store_kind"] == "source_identity"
        assert lines[("T-PHYS", 1)]["store_kind"] == "physical"
        assert lines[("T-PEND", 1)]["store_kind"] == "store_pending"
        assert lines[("T-PHYS", 1)]["identifier_shape"] == "upc_a_12"

        s = result["summary"]
        assert (s["invoices"], s["pdi_lines"], s["resolved_lines"]) == (3, 14, 6)   # T-PHYS 1,2,4,5 + T-CODE 1,2
        assert s["invoices_mapping_complete"] == 0
        assert s["manual_queue_store_codes"] == 6          # C, F, G, H, I at the physical store; D at the code-only one
        assert result["settings"] == {"product_master_commercial_resolution": True,
                                      "commercial_source_identity": "47708760", "global_holder_found": True}
        assert result["database"]["name"] == "invoice_test"

        payload = json.dumps(result)
        assert SECRET_DESCRIPTION not in payload and str(SECRET_PRICE) not in payload and "FUEL" not in payload

    async def test_with_product_master_resolution_off(self, corpus, resolver_settings):
        resolver_settings(False)
        result = await _run()
        got = {k: (v["category"], v["sub_reason"], v["units_per_case"]) for k, v in _by_line(result).items()
               if k[0] == "T-PHYS"}
        assert got == {
            ("T-PHYS", 1): ("REQUIRES_MAPPING", "product_master_disabled", None),
            ("T-PHYS", 2): ("REQUIRES_MAPPING", "product_master_disabled", None),
            ("T-PHYS", 3): ("REQUIRES_MAPPING", "product_master_disabled", None),
            ("T-PHYS", 4): ("LEGACY_FALLBACK", None, 6),
            ("T-PHYS", 5): ("LEGACY_FALLBACK", None, 1),
            ("T-PHYS", 6): ("REQUIRES_MAPPING", "product_master_mapping_not_approved", None),
            ("T-PHYS", 7): ("REQUIRES_MAPPING", "identity_known_no_conversion", None),
            ("T-PHYS", 8): ("REQUIRES_MAPPING", "absent_from_master_corpus", None),
            ("T-PHYS", 9): ("REQUIRES_MAPPING", "product_master_disabled", None),
            ("T-PHYS", 10): ("NO_USABLE_CODE", None, None),
        }
        assert result["settings"]["product_master_commercial_resolution"] is False

    async def test_the_same_database_gives_the_same_report_and_a_clean_comparison(self, corpus, resolver_settings):
        resolver_settings(True)
        first, second = await _run(), await _run()
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
        diff = report.compare(first, second)
        assert (diff["lines_changed"], diff["regressions"]) == (0, [])
        resolver_settings(False)
        regressed = report.compare(first, await _run())
        assert {(c["invoice_number"], c["sort_order"]) for c in regressed["regressions"]} == {
            ("T-PHYS", 1), ("T-PHYS", 2), ("T-PHYS", 5), ("T-CODE", 1), ("T-CODE", 2)}


class TestTheSessionIsReadOnly:
    async def test_a_write_is_refused(self, db_session):
        engine = report.read_only_engine(report.target_url(TEST_URL, "invoice_test"))
        try:
            async with AsyncSession(engine) as session:
                await report.begin_read_only(session)
                with pytest.raises(DBAPIError, match="read-only transaction"):
                    await session.execute(text("INSERT INTO stores (identity_status) VALUES ('unresolved')"))
        finally:
            await engine.dispose()


class TestTheCommandLine:
    def test_json_on_stdout_is_the_whole_report_and_logs_stay_off_it(self, corpus):
        root = Path(__file__).resolve().parents[2]
        env = {**os.environ, "DATABASE_URL": TEST_URL, "PRODUCT_MASTER_COMMERCIAL_RESOLUTION": "false"}
        result = subprocess.run(
            [sys.executable, str(root / "scripts" / "mapping_coverage_report.py"), "--database", "invoice_test",
             "--product-master-resolution", "on", "--source-identity", "47708760", "--json"],
            capture_output=True, text=True, env=env, cwd=root, timeout=120,
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["settings"]["product_master_commercial_resolution"] is True     # the flag override wins
        assert data["summary"]["pdi_lines"] == 14 and data["summary"]["resolved_lines"] == 6
        assert SECRET_DESCRIPTION not in result.stdout + result.stderr
