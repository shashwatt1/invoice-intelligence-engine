"""The vendor-merge command: what it asks for before it can write, and that it cannot write by itself."""

from __future__ import annotations

import ast
import importlib.util
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "merge_vendors.py"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("merge_vendors_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


A, B = str(uuid.uuid4()), str(uuid.uuid4())
PAIR = ["--winner", A, "--loser", B]
APPLY = [*PAIR, "--apply", "--actor", "barj", "--basis", "same supplier", "--expect-plan", "f" * 64,
         "--expect-database", "db.example/postgres"]


class TestArguments:
    @pytest.mark.parametrize("argv", [
        [],                                              # nothing to do
        ["--winner", A],                                 # a pair needs both
        ["--winner", "not-an-id", "--loser", B],
        [*PAIR, "--apply"],                              # apply needs the rest
        [*PAIR, "--apply", "--actor", "barj", "--basis", "x", "--expect-plan", "f" * 64],            # no database named
        [*PAIR, "--apply", "--actor", "barj", "--expect-plan", "f" * 64, "--expect-database", "h/d"],  # no basis
        [*PAIR, "--actor", "barj"],                      # a dry run takes no actor
        [*PAIR, "--basis", "x"],
        [*PAIR, "--expect-plan", "f" * 64],
        ["--list-duplicates", "--apply"],                # listing is read-only
        ["--list-duplicates", *PAIR],
    ])
    def test_a_command_that_could_write_without_being_asked_properly_is_refused(self, tool, argv, capsys):
        with pytest.raises(SystemExit) as raised:
            tool.main(argv)
        assert raised.value.code == 2, capsys.readouterr().err

    def test_a_well_formed_apply_gets_past_argument_checking(self, tool, monkeypatch):
        seen = {}

        async def fake_run(args):
            seen["args"] = args
            return 0

        monkeypatch.setattr(tool, "run", fake_run)
        assert tool.main(APPLY) == 0
        assert seen["args"].apply and seen["args"].actor == "barj" and seen["args"].expect_plan == "f" * 64

    def test_the_unusual_merges_default_to_refused(self, tool, monkeypatch):
        seen = {}

        async def fake_run(args):
            seen["args"] = args
            return 0

        monkeypatch.setattr(tool, "run", fake_run)
        tool.main(PAIR)
        args = seen["args"]
        assert not (args.apply or args.allow_both_confirmed or args.allow_different_tax_ids or args.allow_different_names
                    or args.allow_received_invoices)


class TestItCannotWriteByItself:
    def test_the_script_has_no_sql_of_its_own_and_only_commits_what_the_service_applied(self):
        source = SCRIPT.read_text()
        for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "session.add", "flush("):
            assert forbidden not in source, forbidden
        assert source.count("commit()") == 1 and source.index("merge.apply(") < source.index("commit()")
        assert source.count('text("SET TRANSACTION READ ONLY")') == 1 and "list_duplicates" in source

    def test_the_dry_run_and_the_listing_are_read_only_transactions(self):
        service = (ROOT / "app/services/vendor_merge_service.py").read_text()
        plan = service.split("async def plan(")[1].split("async def _admin_actor")[0]
        assert 'text("SET TRANSACTION READ ONLY")' in plan and "update(" not in plan and ".add(" not in plan

    def test_the_apply_takes_the_vendor_locks_before_it_judges_the_plan_and_writes_nothing_before_the_checks(self):
        service = (ROOT / "app/services/vendor_merge_service.py").read_text()
        apply = service.split("async def apply(")[1]
        order = [apply.index(marker) for marker in
                 ("_admin_actor(", "with_for_update()", "_plan_for(", "current.refusals", "expect_plan != current.fingerprint",
                  "update(Invoice)", "session.add(merge)")]
        assert order == sorted(order), "actor, locks, plan, refusals, fingerprint, then the writes"

    def test_it_never_changes_a_line_a_total_a_store_a_status_or_an_inventory_record(self):
        tree = ast.parse((ROOT / "app/services/vendor_merge_service.py").read_text())
        updated = {ast.unparse(node.args[0]) for node in ast.walk(tree)
                   if isinstance(node, ast.Call) and ast.unparse(node.func) == "update"}
        assert updated == {"Invoice", "ProductDataProposal", "Document", "VendorRequest", "VendorItemCrosswalk"}
        # on an invoice, a proposal, a document and a request the only column written is the vendor
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and ast.unparse(node.func).endswith(".values"):
                target = ast.unparse(node.func.value)
                if target.startswith(("update(Invoice)", "update(ProductDataProposal)", "update(Document)", "update(VendorRequest)")):
                    assert {k.arg for k in node.keywords} <= {"vendor_id", "chosen_vendor_id"}, ast.unparse(node)
