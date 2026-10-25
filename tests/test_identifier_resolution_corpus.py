"""
Tests — Phase 3B shadow corpus runner (scripts/identifier_resolution_corpus.py).

No database is needed: the stored-corpus source is exercised through a fake
read-only connection. What these pin:

  * the runner only ever SELECTs, on a READ ONLY session, and an unreachable
    database is reported — never worked around;
  * a stored invoice becomes resolver input faithfully (supplier_item_id from
    the raw extraction, human corrections carried, v11-only mode);
  * "exported today" is exactly export_service.pdi_items();
  * the gold judgement and the KD hazard mapping say what they claim;
  * the seed preview is used only as a labelled stand-in, and the
    AUTO_MATCHED-as-confirmed run exists only as a named HYPOTHETICAL.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import scripts.identifier_resolution_corpus as corpus
from app.services.product_master.identifier_templates import load_templates

ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / "scripts" / "identifier_resolution_corpus.py").read_text()


class FakeCursor:
    def __init__(self, tables):
        self.tables, self.statements, self._rows = tables, [], []

    def execute(self, sql, params=None):
        self.statements.append(sql)
        table = next(t for t in ("invoice_items", "document_pages", "master_product_descriptions",
                                 "master_products", "invoices") if t in sql)
        self._rows = self.tables.get(table, [])

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self, tables):
        self.cursor_obj = FakeCursor(tables)

    def cursor(self):
        return self.cursor_obj


def raw_extraction(*items):
    return {"choices": [{"message": {"content": json.dumps({"line_items": list(items)})}}]}


class TestReadOnly:

    def test_every_statement_is_a_select(self):
        statements = re.findall(r'execute\(\s*"([^"]+)', SOURCE)
        assert statements and all(s.lstrip().upper().startswith("SELECT") for s in statements)
        for forbidden in ("INSERT", "UPDATE ", "DELETE ", ".commit("):
            assert forbidden not in SOURCE
        # The single file write is the report under analysis/ (gitignored).
        assert SOURCE.count("write_text(") == 1 and 'OUT / "corpus_report.json"' in SOURCE

    def test_the_session_is_read_only_and_autocommit(self):
        assert "set_session(readonly=True, autocommit=True)" in SOURCE

    def test_an_unreachable_database_is_reported_not_worked_around(self):
        assert "UNREACHABLE" in SOURCE and "not worked around" in SOURCE
        for workaround in ("docker", "subprocess", "environ[", "setenv"):
            assert workaround not in SOURCE.lower()

    def test_output_is_gitignored(self):
        import subprocess

        assert subprocess.run(["git", "check-ignore", "-q", "analysis/identifier-shadow/corpus/corpus_report.json"],
                              cwd=ROOT).returncode == 0


class TestStoredCorpusSource:

    def test_a_stored_invoice_becomes_v11_only_resolver_input(self):
        tables = {
            "invoices": [("inv-1", "2035546957", "Red Bull Distribution Company Inc.",
                          raw_extraction({"supplier_item_id": "RB248904"}, {"supplier_item_id": "RB2860"}), "doc-1")],
            "invoice_items": [("inv-1", 0, "SF ICED 8.4OZ", "product", "611269002461", 1, ["product_code"]),
                              ("inv-1", 1, "SUGARFREE 8.4OZ 4PK", "product", None, 1, None)],
            "document_pages": [("doc-1", 1, "page one"), ("doc-1", 2, "page two")],
        }
        connection = FakeConnection(tables)
        [invoice] = corpus.database_corpus(connection)
        assert invoice.source == "database" and invoice.observations is None
        assert [(ln.product_code, ln.supplier_item_id, ln.corrected_fields) for ln in invoice.lines] == [
            ("611269002461", "RB248904", ("product_code",)), (None, "RB2860", ())]
        assert invoice.ocr_text == "page one\npage two"
        assert all(s.lstrip().startswith("SELECT") for s in connection.cursor_obj.statements)

    def test_supplier_ids_are_withheld_when_items_and_raw_extraction_disagree(self):
        tables = {"invoices": [("inv-1", "1", None, raw_extraction({"supplier_item_id": "X"}), "doc-1")],
                  "invoice_items": [("inv-1", 0, "A", "product", "1", 1, None), ("inv-1", 1, "B", "product", "2", 1, None)],
                  "document_pages": []}
        [invoice] = corpus.database_corpus(FakeConnection(tables))
        assert [ln.supplier_item_id for ln in invoice.lines] == [None, None]
        assert invoice.notes == ["supplier_item_id unavailable: stored items and raw extraction differ in count"]

    def test_live_product_master_lookup_is_a_select(self):
        connection = FakeConnection({"master_products": [("p1", "CONFIRMED", "item_sales_summary", "611269002461")],
                                     "master_product_descriptions": [("RED BULL SF 8.4 OZ",)]})
        [match] = corpus.live_lookup(connection)("611269002461")
        assert (match.product_id, match.evidence_state, match.descriptions) == ("p1", "CONFIRMED", ("RED BULL SF 8.4 OZ",))
        assert all(sql.startswith("SELECT") for sql in connection.cursor_obj.statements)


class TestComparisons:

    def test_exported_today_is_pdi_items(self):
        from app.services.export_service import pdi_items

        rows = [{"line_type": "product", "quantity": 1}, {"line_type": "product", "quantity": 0},
                {"line_type": "charge", "quantity": 1}, {"line_type": "duplicate", "quantity": 2},
                {"line_type": "voided", "quantity": 1}, {"line_type": None, "quantity": None}]
        invoice = SimpleNamespace(items=[SimpleNamespace(sort_order=i, **r) for i, r in enumerate(rows)])
        expected = {item.sort_order for item in pdi_items(invoice)}
        assert {i for i, r in enumerate(rows) if corpus.exported_today(r)} == expected

    def test_gold_judgement(self):
        assert corpus.judge_against_gold("61126900246", "61126900246") == "MATCHES_GOLD"
        assert corpus.judge_against_gold("61126910900", "61126932121") == "CONTRADICTS_GOLD"
        assert corpus.judge_against_gold("00000026092", None) == "NO_GOLD_GTIN12"
        assert corpus.judge_against_gold(None, "61126900246") == "NO_CODE"

    def test_hazard_mapping(self):
        def line(status, *cands):
            return {"status": status, "candidates": [{"source": "v11.product_code", **c} for c in cands]}
        assert corpus.hazard_for(line("UNRESOLVED", {"kind": "SUPPLIER_ITEM_ID"}), "RB248904") == "KD1"
        assert corpus.hazard_for(line("UNRESOLVED", {"kind": "FRAGMENT"}), "26092") == "KD2"
        assert corpus.hazard_for(line("UNRESOLVED", {"kind": "SUPPLIER_ITEM_ID"}), "00025328") == "KD3"
        assert corpus.hazard_for(line("AMBIGUOUS", {"kind": "ELEVEN_DIGIT"}), "79117101016") == "KD4"
        assert corpus.hazard_for(line("VALID_UNMATCHED", {"kind": "GTIN_13"}), "7501011131064") == "KD5"
        assert corpus.hazard_for(line("AMBIGUOUS", {"kind": "GTIN_14"}), "30076406668107") == "KD6"
        assert corpus.hazard_for(line("INVALID", {"kind": "GTIN_12", "check_valid": False}), "800500423523") == "KD7"
        assert corpus.hazard_for(line("AMBIGUOUS", {"kind": "GTIN_14", "piece": "1"}), "1,2") == "KD8"
        assert corpus.hazard_for(line("AMBIGUOUS", {"kind": "GTIN_12", "check_valid": True}),
                                 "611269109009").startswith("ASSOCIATION")
        assert corpus.hazard_for(line("VALID_UNMATCHED", {"kind": "GTIN_12", "check_valid": True}),
                                 "611269002461").startswith("PRODUCT_MASTER")
        assert corpus.hazard_for(line("NOT_APPLICABLE"), "76GB03").endswith("+ KD1")

    def test_hazard_catalogue_is_kd1_to_kd10(self):
        assert list(corpus.HAZARDS) == [f"KD{i}" for i in range(1, 11)]


class TestProductMasterStandIns:

    def test_the_seed_preview_is_labelled_and_never_called_live(self):
        assert "a seed PREVIEW, not the live Product Master" in SOURCE
        assert "HYPOTHETICAL_local_v11_plus_obs__seed_preview_auto_matched_as_confirmed" in SOURCE

    def test_the_hypothetical_run_is_the_only_place_auto_matched_is_promoted(self):
        assert SOURCE.count('"CONFIRMED" if m.evidence_state == "AUTO_MATCHED"') == 1
        promoted = SOURCE.split("def auto_matched_as_confirmed")[1].split("plans = [")[0]
        assert "HYPOTHETICAL" in promoted

    def test_evaluate_is_deterministic_on_a_synthetic_invoice(self):
        from app.services.product_master.resolution import ResolverLine, no_product_master

        inv = corpus.CorpusInvoice(
            key="synthetic", source="local_artifact", vendor_name="Red Bull Distribution",
            lines=[ResolverLine(1, "SF ICED 8.4OZ", "product", "611269002461", "RB248904")],
            meta=[{"quantity": 1, "line_type": "product", "product_code": "611269002461"}],
            ocr_text="ID QTY UNITS DESCRIPTION RB248904 SF ICED 8.4OZ 611269002461")
        templates = load_templates()
        a = corpus.evaluate(inv, templates, no_product_master, use_observations=False)
        b = corpus.evaluate(inv, templates, no_product_master, use_observations=False)
        assert corpus.digest([a]) == corpus.digest([b])
        assert a["edi_comparison"][0]["outcome"] == "NEWLY_BLOCKED"      # VALID_UNMATCHED is never eligible
