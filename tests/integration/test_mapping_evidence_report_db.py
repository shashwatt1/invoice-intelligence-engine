"""
tests/integration/test_mapping_evidence_report_db.py — the evidence report end to end, on real Postgres.

Builds, in the isolated test database, invoices at a physical store and at a
code-only store (86357232), Product Master mappings under the global holder
(47708760) and the physical store, workbook pricing rows with file/sheet/row
provenance (stored in the printed 12-digit form, as workbooks hold them), a
legacy mapping held by another store and case-mapping proposals. Then runs
the report as the CLI does — its own READ ONLY connection, the live resolver
through the unchanged coverage report — with Product Master resolution on.

Proves: lines are grouped per distinct code; provenance survives; physical
counts are never reported as multipliers; cross-store evidence is labelled
and never applied; mappings without units are flagged; repeated runs are
byte-identical; and no description, price, cost or invoice number reaches the
default output, while --include-descriptions adds only descriptions.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.models.document import Document
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.models.product_case_mapping import ProductCaseMapping
from app.models.product_data_proposal import ProductDataProposal
from app.models.product_master import (
    MasterCommercialMapping,
    MasterProduct,
    MasterProductIdentifier,
)
from app.models.product_reference import ProductIdentity, ProductPricing
from app.models.store import Store
from app.services.product_master.identifiers import upc_a_check_digit
from scripts import mapping_evidence_report as ev
from tests.integration.conftest import TEST_URL, requires_db, store_id

pytestmark = requires_db

NAMES = "APQRSTC"
CODES = {name: f"0888880000{n}" for n, name in enumerate(NAMES, start=1)}
PRINTED = {name: code + upc_a_check_digit(code) for name, code in CODES.items()}
SECRET_DESCRIPTION, SECRET_PRICE, SECRET_COST = "SECRET DESCRIPTION", 98.76, Decimal("54.3210")
WORKBOOK = "data/reference/Beer Inventory.xlsx"


def _item(invoice_id, sort, name):
    return InvoiceItem(invoice_id=invoice_id, sort_order=sort, description=f"{SECRET_DESCRIPTION} {sort}",
                       quantity=1, unit_price=SECRET_PRICE, line_total=SECRET_PRICE,
                       product_sku=PRINTED[name], line_type="product")


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
    """
    A  approved globally                      -> resolved, not in the report
    P  REVIEW_REQUIRED global mapping (12) + workbook 12/case    -> pending candidate, single valued
    Q  identity known, two workbook rows: 12 and 24 per case      -> conflicting
    R  approved legacy 24 held by the code-only store only        -> approved out of scope (cross-store)
    S  REVIEW_REQUIRED global mapping without units; rejected 6   -> missing units, identity only
    T  nothing at all, on two invoices                            -> none, grouped once
    C  physical 12 vs global 24, both approved                    -> resolver conflict
    """
    physical = Store(display_name="AF Evidence Physical", identity_status="confirmed")
    db_session.add(physical)
    await db_session.flush()
    holder, code_only = store_id("47708760"), store_id("86357232")

    products = {}
    for name in "APQSC":
        products[name] = MasterProduct(canonical_key=f"upc:{PRINTED[name]}", canonical_upc=PRINTED[name],
                                       identity_basis="UPC_A", canonical_description=SECRET_DESCRIPTION)
        db_session.add(products[name])
    await db_session.flush()
    db_session.add(MasterProductIdentifier(product_id=products["Q"].id, raw_value=PRINTED["Q"],
                                           normalized_value=PRINTED["Q"], identifier_type="UPC_A",
                                           source_system="test", source_file=WORKBOOK, source_sheet="Monarch Package",
                                           source_row=31))

    def pm(name, store, units, state="APPROVED", row=None):
        db_session.add(MasterCommercialMapping(
            product_id=products[name].id, store_id=store, pdi_item_code=CODES[name],
            commercial_unit_basis="CASE_IS_SELLING_UNIT" if units == 1 else "UNIT_IS_SELLING_UNIT",
            units_accounted_for=units, approval_state=state, source_system="test",
            source_file="seed.xlsx" if row else None, source_sheet="Mappings" if row else None, source_row=row))
    pm("A", holder, 24)
    pm("P", holder, 12, state="REVIEW_REQUIRED", row=40)
    pm("S", holder, None, state="REVIEW_REQUIRED", row=41)
    pm("C", holder, 24), pm("C", physical.id, 12)

    def price(name, derived, row, *, sheet="Monarch Package", conflicted=False):
        db_session.add(ProductPricing(
            store_id=holder, item_code=PRINTED[name], distributor="Monarch", pricing_basis="case",
            package="C12 24OZ", items_per_case_derived=derived, items_per_case_derivation="ratio",
            case_cost=SECRET_COST, unit_cost=SECRET_COST, unit_retail=SECRET_COST, is_conflicted=conflicted,
            conflict_detail={"note": SECRET_DESCRIPTION}, source_file=WORKBOOK, source_sheet=sheet, source_row=row,
            imported_at=datetime(2026, 9, 1, tzinfo=UTC)))
    price("P", 12, 422)
    price("Q", 12, 431), price("Q", 24, 1203, sheet="Monarch Singles")
    db_session.add(ProductIdentity(store_id=holder, item_code=PRINTED["T"], description=SECRET_DESCRIPTION))

    db_session.add(ProductCaseMapping(store_id=code_only, item_code=CODES["R"], units_per_case=24, source="APPROVED"))
    for status, value in (("REJECTED", 6), ("PENDING", "4")):
        db_session.add(ProductDataProposal(store_id=physical.id, entity_type="case_mapping", entity_key=CODES["S"],
                                           field="units_per_case", proposed_value=value, source="operator_entered",
                                           proposed_by="test", status=status))

    first = await _invoice(db_session, "EV-1", physical.id)
    second = await _invoice(db_session, "EV-2", physical.id)
    db_session.add_all([_item(first.id, n, name) for n, name in enumerate(NAMES, start=1)]
                       + [_item(second.id, 1, "T")])
    await db_session.commit()
    yield
    await db_session.execute(text("TRUNCATE master_products CASCADE"))
    await db_session.commit()


@pytest.fixture
def resolution_on(monkeypatch):
    monkeypatch.setenv("PRODUCT_MASTER_COMMERCIAL_RESOLUTION", "true")
    monkeypatch.setenv("COMMERCIAL_SOURCE_IDENTITY", "47708760")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _run(include_descriptions=False):
    return await ev.run(ev.target_url(TEST_URL, "invoice_test"), None, include_descriptions=include_descriptions)


def _by_code(result):
    return {c["item_code"]: c for c in result["codes"]}


class TestTheEvidenceReportOnRealData:
    async def test_codes_are_grouped_and_classified(self, corpus, resolution_on):
        codes = _by_code(await _run())
        assert set(codes) == {CODES[n] for n in "PQRSTC"}                      # A resolved: not reported
        got = {name: (codes[CODES[name]]["decision_status"], codes[CODES[name]]["evidence"]) for name in "PQRSTC"}
        assert got == {
            "P": ("pending_candidate", "single_valued"),
            "Q": ("no_candidate", "conflicting"),
            "R": ("approved_out_of_scope", "single_valued"),
            "S": ("pending_candidate", "single_valued"),     # the PENDING proposal "4" is the only value
            "T": ("no_candidate", "none"),
            "C": ("resolver_conflict", "conflicting"),
        }
        t = codes[CODES["T"]]["occurrences"]
        assert (t["lines"], t["invoices"], t["store_kinds"]) == (2, 2, {"physical": 2})
        assert all(c["requires_human_decision"] for c in codes.values())

    async def test_workbook_provenance_survives_and_a_physical_count_is_not_a_multiplier(self, corpus, resolution_on):
        p = _by_code(await _run())[CODES["P"]]
        assert p["evidence_detail"]["pricing"] == [{
            "scope": "global_holder", "store_kind": "source_identity", "store_label": p["evidence_detail"]["pricing"]
            [0]["store_label"], "distributor": "Monarch", "pricing_basis": "case", "package": "C12 24OZ",
            "items_per_case_stated": None, "items_per_case_derived": 12, "derivation": "ratio", "is_conflicted": False,
            "source_file": WORKBOOK, "source_sheet": "Monarch Package", "source_row": 422}]
        assert p["commercial_multiplier"]["sources"] == {"12": ["product_master:REVIEW_REQUIRED:global_holder"]}
        assert p["physical_items_per_case"]["sources"] == {"12": ["pricing:derived:global_holder"]}
        pm = p["evidence_detail"]["product_master"][0]
        assert (pm["source_file"], pm["source_sheet"], pm["source_row"], pm["resolver_reads"]) == (
            "seed.xlsx", "Mappings", 40, True)

    async def test_conflicting_workbook_rows_both_keep_their_rows(self, corpus, resolution_on):
        q = _by_code(await _run())[CODES["Q"]]
        assert q["physical_items_per_case"]["values"] == [12, 24]
        assert sorted((r["source_sheet"], r["source_row"]) for r in q["evidence_detail"]["pricing"]) == [
            ("Monarch Package", 431), ("Monarch Singles", 1203)]
        assert q["evidence_detail"]["identity"]["master_identifiers"] == 1
        assert q["evidence_detail"]["identity"]["sample"][0]["source_row"] == 31

    async def test_cross_store_legacy_is_labelled_and_not_applied(self, corpus, resolution_on):
        r = _by_code(await _run())[CODES["R"]]
        (legacy,) = r["evidence_detail"]["legacy"]
        assert (legacy["scope"], legacy["store_kind"], legacy["units_per_case"], legacy["resolver_reads"]) == (
            "other_store", "source_identity", 24, False)
        assert r["resolver_categories"] == {"REQUIRES_MAPPING | absent_from_master_corpus": 1}

    async def test_missing_units_and_rejected_values(self, corpus, resolution_on):
        s = _by_code(await _run())[CODES["S"]]
        assert s["pending_without_units"] is True
        assert s["evidence_detail"]["product_master"][0]["units"] is None
        assert s["previously_rejected_values"] == [6]
        assert s["commercial_multiplier"]["sources"] == {"4": ["proposal:PENDING:same_store"]}

    async def test_the_resolver_conflict_shows_both_approved_values(self, corpus, resolution_on):
        c = _by_code(await _run())[CODES["C"]]
        assert c["resolver_categories"] == {"CONFLICT | physical_global_disagree": 1}
        assert c["commercial_multiplier"]["sources"] == {"12": ["product_master:APPROVED:same_store"],
                                                         "24": ["product_master:APPROVED:global_holder"]}

    async def test_repeated_runs_are_identical_and_carry_no_secrets(self, corpus, resolution_on):
        first, second = await _run(), await _run()
        payload = json.dumps(first, sort_keys=True, default=str)
        assert payload == json.dumps(second, sort_keys=True, default=str)
        for secret in (SECRET_DESCRIPTION, str(SECRET_PRICE), str(SECRET_COST), "54.32", "EV-1", "EV-2",
                       "case_cost", "unit_retail", "conflict_detail"):
            assert secret not in payload, secret
        assert first["summary"]["distinct_codes"] == 6 and first["summary"]["unresolved_lines"] == 7

    async def test_descriptions_only_when_asked(self, corpus, resolution_on):
        detailed = _by_code(await _run(include_descriptions=True))
        (reference,) = detailed[CODES["T"]]["descriptions"]
        assert reference.startswith("reference (") and reference.endswith(SECRET_DESCRIPTION)
        assert any(SECRET_DESCRIPTION in d for d in detailed[CODES["P"]]["descriptions"])
        payload = json.dumps(detailed, default=str)
        assert str(SECRET_COST) not in payload and "EV-1" not in payload


class TestTheCommandLine:
    def test_summary_on_stdout_and_a_private_json_file(self, corpus, tmp_path):
        root = Path(__file__).resolve().parents[2]
        out = tmp_path / "evidence" / "report.json"
        env = {**os.environ, "DATABASE_URL": TEST_URL, "PRODUCT_MASTER_COMMERCIAL_RESOLUTION": "false"}
        result = subprocess.run(
            [sys.executable, str(root / "scripts" / "mapping_evidence_report.py"), "--database", "invoice_test",
             "--product-master-resolution", "on", "--source-identity", "47708760", "--output", str(out)],
            capture_output=True, text=True, env=env, cwd=root, timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert "6 distinct unresolved codes across 7 lines" in result.stdout
        assert CODES["P"] in result.stdout
        assert SECRET_DESCRIPTION not in result.stdout + result.stderr and "EV-1" not in result.stdout
        assert oct(out.stat().st_mode & 0o777) == "0o600"
        assert json.loads(out.read_text())["settings"]["product_master_commercial_resolution"] is True
