"""
The revalidate-only tool, without a database — tests/test_revalidation_tool.py

What must hold before it is ever pointed at production: it needs no OCR and no model (nothing of either is even
imported), it cannot write proposals, mappings, lines, exports or inventory, it detects the stored-line rewrites the
deployed revalidation would make, and its command line refuses an apply that was not reviewed. The behaviour against
real rows is in tests/integration/test_revalidation_tool_db.py.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from app.services import revalidation_tool as tool
from scripts import revalidate_invoices as cli
from tests.test_export_service import make_invoice

ROOT = Path(tool.__file__).resolve().parents[2]
SERVICE = Path(tool.__file__)
SCRIPT = Path(cli.__file__)


def imported_modules(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names |= {f"{node.module}.{a.name}" for a in node.names}
    return names


class TestNoOcrAndNoModel:
    @pytest.mark.parametrize("path", [SERVICE, SCRIPT])
    def test_nothing_that_reads_a_photo_or_calls_a_model_is_imported(self, path):
        forbidden = ("openai", "app.services.llm", "app.services.ocr", "app.services.structuring_service",
                     "app.services.extraction_service", "app.services.pipeline_service",
                     "app.services.reprocess_service", "google", "httpx", "requests", "boto3")
        offending = [m for m in imported_modules(path) if any(m == f or m.startswith(f + ".") for f in forbidden)]
        assert offending == []

    def test_importing_the_tool_loads_no_ocr_or_model_module_at_all(self):
        code = ("import sys, app.services.revalidation_tool\n"
                "print(sorted(m for m in sys.modules if m.startswith(('app.services.llm', 'app.services.ocr', 'openai'))"
                " or m in ('app.services.pipeline_service', 'app.services.structuring_service')))")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        assert out.strip() == "[]"


class TestItCannotWriteWhatItMustNotTouch:
    def test_the_service_never_uses_proposals_mappings_exports_or_inventory_writers_or_line_correctors(self):
        tree = ast.parse(SERVICE.read_text())
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree)
                                                                           if isinstance(n, ast.Attribute)}
        used |= {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        used |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        forbidden = {"proposal_service", "ProductDataProposal", "ProductCaseMapping", "case_mapping_service",
                     "record_pdi_export", "delete", "reverse_receipt", "post_receipt", "InventoryMovement",
                     "correct_item", "correct_totals", "correct_invoice_date", "_persist_proven_reconciliation",
                     "revalidate_invoice", "add_all"}
        assert used & forbidden == set()
        # reads of the inventory ledger are allowed; the only inventory writer-side helper used is the row lock
        assert {n for n in used if n.startswith(("reverse_", "post_", "withdraw_", "submit_"))} == set()

    def test_the_only_invoice_fields_it_assigns_are_status_confidence_and_blocking_checks(self):
        assigned = {t.attr for node in ast.walk(ast.parse(SERVICE.read_text())) if isinstance(node, ast.Assign)
                    for t in node.targets if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                    and t.value.id == "invoice"}
        assert assigned == {"status", "blocking_checks", "composite_confidence"}

    def test_the_script_has_no_sql_writes_of_its_own(self):
        source = SCRIPT.read_text()
        for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "session.add", ".execute("):
            assert forbidden not in source, forbidden


class TestDetectingTheRewritesTheDeployedRevalidationWouldMake:
    def invoice(self, quantity):
        invoice = make_invoice(subtotal=Decimal("50.00"), tax_amount=Decimal("0"), grand_total=Decimal("50.00"))
        first, second = invoice.items
        first.quantity, first.unit_price, first.line_total = Decimal(str(quantity)), Decimal("10.00"), Decimal("40.00")
        second.quantity, second.unit_price, second.line_total = Decimal("1"), Decimal("10.00"), Decimal("10.00")
        first.sort_order, second.sort_order = 0, 1
        first.line_type = second.line_type = "product"
        for item in invoice.items:
            item.source_pages, item.duplicate_candidate, item.discount, item.deposit = [], None, None, None
        invoice.other_charges = None
        return invoice

    def test_a_row_whose_quantity_the_totals_prove_wrong_is_counted(self):
        assert tool.rows_the_deployed_revalidation_would_rewrite(self.invoice(quantity=1)) == 1   # 40.00 / 10.00 = 4

    def test_a_consistent_invoice_has_nothing_to_rewrite(self):
        assert tool.rows_the_deployed_revalidation_would_rewrite(self.invoice(quantity=4)) == 0


class TestTheCommandLineRefusesWhatWasNotReviewed:
    def run(self, *argv):
        with pytest.raises(SystemExit) as stopped:
            cli.main(list(argv))
        return stopped.value.code

    def test_an_invoice_number_is_required_and_there_is_no_all(self):
        assert self.run() == 2
        assert self.run("--all") == 2

    def test_apply_needs_an_actor_and_a_reviewed_plan_for_every_invoice(self):
        assert self.run("--invoice-number", "1", "--apply") == 2
        assert self.run("--invoice-number", "1", "--apply", "--actor", "a") == 2
        assert self.run("--invoice-number", "1", "--invoice-number", "2", "--apply", "--actor", "a",
                        "--expect-plan", "1=abc") == 2                       # the plan for 2 is missing

    def test_actor_and_plan_without_apply_are_refused_so_a_dry_run_is_never_confused_with_one(self):
        assert self.run("--invoice-number", "1", "--actor", "a") == 2
        assert self.run("--invoice-number", "1", "--expect-plan", "1=abc") == 2
