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

    def test_the_script_has_no_sql_writes_of_its_own_only_one_read_of_the_database_name(self):
        source = SCRIPT.read_text()
        for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "session.add", "commit()", "flush("):
            assert forbidden not in source.replace("await session.commit()", ""), forbidden
        assert ".execute(" not in source and "text(" not in source, "the one read of the database name lives in db_target"
        target = (SCRIPT.parent.parent / "app/services/db_target.py").read_text()
        assert target.count(".execute(") == 1 and 'text("select current_database()")' in target


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


class TestTheCounterAgreesWithTheDeployedRevalidation:
    """
    The tool refuses to apply when the deployed revalidation would rewrite a stored line, so its count must be the
    function's own answer. Here the deployed `_persist_proven_reconciliation` runs on the same invoices (against a fake
    session, so nothing is persisted) and the number of rows it changes must equal the tool's count.
    """

    class FakeSession:
        def add(self, *_):
            pass

        async def flush(self):
            pass

    @pytest.fixture(autouse=True)
    def _no_log_writes(self, monkeypatch):
        import app.services.revalidation_service as deployed

        class NoLogs:
            def __init__(self, _session):
                pass

            async def add(self, **_kw):
                pass
        monkeypatch.setattr(deployed, "ProcessingLogRepository", NoLogs)

    def prepared(self, invoice):
        for item in invoice.items:
            item.line_type, item.source_pages, item.duplicate_candidate = "product", [], None
            item.discount = item.discount if getattr(item, "discount", None) is not None else None
            item.correction_history = []
        invoice.other_charges = None
        return invoice

    async def deployed_rewrites(self, invoice):
        from app.services.revalidation_service import _persist_proven_reconciliation
        from app.services.validation.service import ValidationService
        return await _persist_proven_reconciliation(self.FakeSession(), invoice, ValidationService()._tolerance)

    def quantity_case(self, quantity):
        invoice = make_invoice(subtotal=Decimal("50.00"), tax_amount=Decimal("0"), grand_total=Decimal("50.00"))
        first, second = invoice.items
        first.quantity, first.unit_price, first.line_total, first.sort_order = Decimal(str(quantity)), Decimal("10.00"), Decimal("40.00"), 0
        second.quantity, second.unit_price, second.line_total, second.sort_order = Decimal("1"), Decimal("10.00"), Decimal("10.00"), 1
        first.deposit = second.deposit = None
        return self.prepared(invoice)

    def deposit_case(self, folded):
        # Rule D: the printed unit price already contains the container deposit (11.00 = 10.00 + 1.00), which the
        # invoice's own totals prove: sum(price x qty) = subtotal + deposits and sum((price - deposit) x qty) = subtotal.
        invoice = make_invoice(subtotal=Decimal("100.00"), tax_amount=Decimal("0"), grand_total=Decimal("110.00"),
                               deposit_total=Decimal("10.00"))
        invoice.items = invoice.items[:1]
        item = invoice.items[0]
        item.sort_order, item.quantity = 0, Decimal("10")
        item.unit_price, item.line_total = (Decimal("11.00"), Decimal("110.00")) if folded else (Decimal("10.00"), Decimal("100.00"))
        item.deposit = Decimal("1.00")
        return self.prepared(invoice)

    @pytest.mark.parametrize("quantity, label", [(1, "Rule E: a quantity the totals prove wrong"), (4, "a consistent invoice")])
    async def test_rule_e(self, quantity, label):
        invoice = self.quantity_case(quantity)
        counted = tool.rows_the_deployed_revalidation_would_rewrite(invoice)
        assert counted == await self.deployed_rewrites(invoice), label
        assert counted == (1 if quantity == 1 else 0)

    @pytest.mark.parametrize("folded", [True, False])
    async def test_rule_d(self, folded):
        invoice = self.deposit_case(folded)
        counted = tool.rows_the_deployed_revalidation_would_rewrite(invoice)
        assert counted == await self.deployed_rewrites(invoice)
        assert counted == (1 if folded else 0)

    async def test_the_count_is_taken_before_the_deployed_function_rewrites_anything(self):
        invoice = self.quantity_case(1)
        assert tool.rows_the_deployed_revalidation_would_rewrite(invoice) == 1
        assert invoice.items[0].quantity == Decimal("1")                  # counting changed nothing


class TestTheCommandLineRefusesWhatWasNotReviewed:
    def run(self, *argv):
        with pytest.raises(SystemExit) as stopped:
            cli.main(list(argv))
        return stopped.value.code

    def test_an_invoice_number_is_required_and_there_is_no_all(self):
        assert self.run() == 2
        assert self.run("--all") == 2

    def test_apply_needs_an_actor_a_database_and_a_reviewed_plan_for_every_invoice(self):
        assert self.run("--invoice-number", "1", "--apply") == 2
        assert self.run("--invoice-number", "1", "--apply", "--actor", "a") == 2
        assert self.run("--invoice-number", "1", "--apply", "--actor", "a", "--expect-plan", "1=abc") == 2  # no database
        assert self.run("--invoice-number", "1", "--apply", "--expect-database", "h/d", "--expect-plan", "1=abc") == 2  # no actor
        assert self.run("--invoice-number", "1", "--invoice-number", "2", "--apply", "--actor", "a",
                        "--expect-database", "h/d", "--expect-plan", "1=abc") == 2   # the plan for 2 is missing

    def test_actor_and_plan_without_apply_are_refused_so_a_dry_run_is_never_confused_with_one(self):
        assert self.run("--invoice-number", "1", "--actor", "a") == 2
        assert self.run("--invoice-number", "1", "--expect-plan", "1=abc") == 2


class TestTheExpectedDatabaseIsComparedWithTheOneConnected:
    TARGET = cli.Target(host="db.example.supabase.co", port=6543, database="postgres", connected_database="postgres")

    def test_the_matching_host_and_database_are_accepted_with_or_without_the_port(self):
        assert cli.target_mismatch("db.example.supabase.co/postgres", self.TARGET) is None
        assert cli.target_mismatch("db.example.supabase.co:6543/postgres", self.TARGET) is None
        assert cli.target_mismatch("DB.Example.Supabase.CO/postgres", self.TARGET) is None      # host names are case-insensitive
        assert cli.target_mismatch(self.TARGET.expect_flag, self.TARGET) is None                # what the dry run tells you to pass

    @pytest.mark.parametrize("expected, differs", [
        ("other.example.supabase.co/postgres", "host"),
        ("db.example.supabase.co/staging", "database"),
        ("db.example.supabase.co:5432/postgres", "port"),
        ("localhost/invoice_db", "host"),
    ])
    def test_a_different_host_database_or_port_is_refused_and_says_what_differs(self, expected, differs):
        message = cli.target_mismatch(expected, self.TARGET)
        assert message is not None and differs in message

    @pytest.mark.parametrize("expected", [None, "", "postgres", "db.example.supabase.co", "/"])
    def test_a_missing_or_malformed_expectation_is_refused(self, expected):
        assert "host/db" in cli.target_mismatch(expected, self.TARGET)

    def test_the_server_reporting_a_different_database_than_the_url_is_refused(self):
        routed = cli.Target(host="pool.example.com", port=6543, database="postgres", connected_database="analytics")
        assert "analytics" in cli.target_mismatch("pool.example.com/postgres", routed)

    def test_the_target_description_never_contains_a_password(self):
        assert "SECRET" not in cli.Target("db.example.com", 5432, "postgres", "postgres").describe()
