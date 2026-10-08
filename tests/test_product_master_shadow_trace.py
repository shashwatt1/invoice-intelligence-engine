"""
tests/test_product_master_shadow_trace.py — the read-only line-by-line trace.

  * Each line is followed down the legacy path and the Product Master path,
    and the two B records are compared field by field (MATCH, EXPECTED
    DIFFERENCE, UNSAFE, BLOCKED).
  * The master path follows the live resolution: an approved mapping held by
    another store applies only as a global mapping (held under the configured
    Item Sales location); otherwise it is reported as the reason the line is
    blocked, never borrowed.
  * Nomenclature compares the canonical description with PDI's, and never
    invents either one.
  * Nothing is written: the invoice is untouched, and the script runs in a
    read-only transaction and never adds, flushes or commits.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.models.product_master import STATE_APPROVED, STATE_REVIEW_REQUIRED
from app.services.product_master import shadow_trace as st

ROOT = Path(__file__).resolve().parent.parent
PHYSICAL, SOURCE = "store-rcm", "store-47708760"
CODE = "01820000334"           # normalized from the printed UPC-A 018200003349


def item(sku="018200003349", description="BUD LIGHT 24/12 CAN", price="18.25", qty="2", order=1, line_type=None):
    return SimpleNamespace(product_sku=sku, description=description, unit_price=Decimal(price),
                           quantity=Decimal(qty), sort_order=order, line_type=line_type)


def invoice(*items):
    return SimpleNamespace(invoice_number="1000540", grand_total=Decimal("100.00"), items=list(items))


def product(pid="p1", canonical=None):
    return st.MasterProductRef(pid, f"upc12:{pid}", "018200003349", canonical,
                               "master_products.canonical_description" if canonical else None)


def mapping(store=PHYSICAL, state=STATE_APPROVED, units=24, pid="p1", label="LG - RCM", kind="physical"):
    return st.CommercialMappingRef(f"m-{store}", pid, store, label, kind, state,
                                   "UNIT_IS_SELLING_UNIT", units, CODE)


def inputs(*, legacy=24, products=(), mappings=(), pdi=None):
    return st.TraceInputs(
        store_id=PHYSICAL, store_label="LG - RCM", store_kind="physical",
        legacy={CODE: st.LegacyMapping(legacy)} if legacy else {},
        products={CODE: list(products)} if products else {},
        mappings={CODE: list(mappings)} if mappings else {},
        pdi_descriptions={CODE: pdi} if pdi else {},
    )


def only(traces):
    assert len(traces) == 1
    return traces[0]


class TestLegacyVersusMaster:
    def test_same_multiplier_and_no_canonical_wording_is_a_match(self):
        t = only(st.trace_invoice(invoice(item()), inputs(products=[product()], mappings=[mapping()])))
        assert t.comparison == st.LINE_MATCH and t.differences == []
        assert t.legacy_b_record == t.master_b_record and len(t.master_b_record) == 70
        assert t.master_b_fields["units_per_case"] == "0024" and t.master_b_fields["item_code"] == CODE
        assert t.master_description == "BUD LIGHT 24/12 CAN", "no canonical wording: the invoice wording, as today"

    def test_a_canonical_description_is_shown_as_the_normalized_name_and_never_reaches_the_edi(self):
        t = only(st.trace_invoice(invoice(item()),
                                  inputs(products=[product(canonical="BUD LIGHT 24/12 CAN NR")], mappings=[mapping()])))
        assert t.comparison == st.LINE_MATCH, "the EDI generator is unchanged: it emits the invoice wording"
        assert t.master_b_fields["description"].rstrip() == "BUD LIGHT 24/12 CAN"
        assert (t.normalized_description, t.normalized_description_source) == (
            "BUD LIGHT 24/12 CAN NR", "PRODUCT_MASTER_CANONICAL_DESCRIPTION")
        assert t.invoice_description == "BUD LIGHT 24/12 CAN", "the source wording is kept"

    def test_a_different_multiplier_is_unsafe(self):
        t = only(st.trace_invoice(invoice(item()), inputs(legacy=12, products=[product()], mappings=[mapping(units=24)])))
        assert t.comparison == st.LINE_UNSAFE
        assert [(d["field"], d["legacy"], d["shadow"]) for d in t.differences] == [("units_accounted_for", "0012", "0024")]


class TestTheMasterPathIsStoreScoped:
    def test_an_approval_held_elsewhere_does_not_apply_when_no_global_holder_is_configured(self):
        elsewhere = mapping(store=SOURCE, label="Item Sales · 47708760", kind="source_identity")
        t = only(st.trace_invoice(invoice(item()), inputs(products=[product()], mappings=[elsewhere])))
        assert t.comparison == st.LINE_BLOCKED and t.master_b_record is None
        assert t.blocked_reasons == [
            "master: approved mapping exists only in another store: Item Sales · 47708760 (source_identity)"]
        assert t.legacy_b_record is not None, "the live line is still reported"

    def test_an_approval_held_under_the_global_holder_resolves_as_product_master_global(self):
        held = mapping(store=SOURCE, label="Store 47708760 (location not yet confirmed)", kind="source_identity")
        trace_inputs = inputs(products=[product()], mappings=[held])
        trace_inputs.global_holder_id = SOURCE
        trace_inputs.global_scope_label = "Global (distributor evidence, held under Item Sales · 47708760)"
        t = only(st.trace_invoice(invoice(item()), trace_inputs))
        assert (t.resolution_path, t.resolution_scope, t.comparison) == (
            "PRODUCT_MASTER_GLOBAL", "Global (distributor evidence, held under Item Sales · 47708760)", st.LINE_MATCH)
        assert t.master_b_fields["units_per_case"] == "0024" and not t.legacy_fallback_used

    def test_an_unapproved_mapping_in_this_store_blocks(self):
        t = only(st.trace_invoice(invoice(item()),
                                  inputs(products=[product()], mappings=[mapping(state=STATE_REVIEW_REQUIRED)])))
        assert t.comparison == st.LINE_BLOCKED and t.mapping_state == STATE_REVIEW_REQUIRED
        assert t.blocked_reasons == ["master: Product Master mapping in LG - RCM is REVIEW_REQUIRED "
                                     "(UNIT_IS_SELLING_UNIT) — not used until approved"]
        assert (t.resolution_path, t.legacy_fallback_used) == ("LEGACY_FALLBACK", True)
        assert t.resolved_b_record == t.legacy_b_record

    def test_no_legacy_mapping_blocks_even_when_the_master_could_build_the_line(self):
        t = only(st.trace_invoice(invoice(item()), inputs(legacy=None, products=[product()], mappings=[mapping()])))
        assert t.comparison == st.LINE_BLOCKED and t.master_b_record is not None
        assert t.blocked_reasons == ["legacy: no product_case_mapping for this code in the invoice's store"]


class TestNomenclature:
    def test_two_master_products_for_one_code_is_an_identity_conflict(self):
        t = only(st.trace_invoice(invoice(item()), inputs(products=[product("p1"), product("p2")])))
        assert t.nomenclature == st.NOM_IDENTITY_CONFLICT and t.comparison == st.LINE_BLOCKED

    def test_missing_wording_is_reported_not_invented(self):
        t = only(st.trace_invoice(invoice(item()), inputs(products=[product()], mappings=[mapping()])))
        assert t.nomenclature == st.NOM_MASTER_MISSING and t.canonical_description is None
        t = only(st.trace_invoice(invoice(item()), inputs(products=[product(canonical="BUD LIGHT 24/12 CAN")])))
        assert t.nomenclature == st.NOM_PDI_MISSING and t.pdi_description is None

    def test_canonical_and_pdi_wordings_are_compared(self):
        def nomenclature(canonical, pdi):
            return only(st.trace_invoice(invoice(item()),
                                         inputs(products=[product(canonical=canonical)], pdi=pdi))).nomenclature
        assert nomenclature("BUD LIGHT 24/12 CAN", "BUD LIGHT 24/12 CAN") == st.NOM_EXACT
        assert nomenclature("BUD LIGHT 24/12 CAN", "bud light  24/12 can") == st.NOM_NORMALIZED
        assert nomenclature("BUD LIGHT 24/12 CAN", "BUD LT 24PK CANS") == st.NOM_DIFFERENT

    def test_the_invoice_wording_is_compared_with_the_canonical_separately(self):
        t = only(st.trace_invoice(invoice(item(description="Bud Light 24/12 Can")),
                                  inputs(products=[product(canonical="BUD LIGHT 24/12 CAN")])))
        assert t.invoice_vs_master == st.NOM_NORMALIZED

    def test_a_line_without_a_code_or_outside_the_master_cannot_be_compared(self):
        t = only(st.trace_invoice(invoice(item(sku=None)), inputs()))
        assert (t.nomenclature, t.comparison) == (st.NOM_NO_IDENTIFIER, st.LINE_BLOCKED)
        assert t.legacy_b_record[1:12] == "00000      ", "live export's blank-code convention"
        t = only(st.trace_invoice(invoice(item()), inputs()))
        assert t.nomenclature == st.NOM_NOT_IN_MASTER
        assert "master: code is not in the Product Master" in t.blocked_reasons


class TestLinesFollowTheLiveExport:
    def test_only_lines_the_export_would_emit_are_traced_in_document_order(self):
        traced = st.trace_invoice(invoice(
            item(order=2, description="SECOND"),
            item(order=3, description="FUEL SURCHARGE", line_type="charge"),
            item(order=4, description="SHORTED", qty="0"),
            item(order=1, description="FIRST"),
        ), inputs(products=[product()], mappings=[mapping()]))
        assert [(t.line_number, t.invoice_description) for t in traced] == [(1, "FIRST"), (2, "SECOND")]

    def test_a_line_without_a_cost_is_blocked_with_the_exporter_s_own_reason(self):
        line = item()
        line.unit_price = None
        t = only(st.trace_invoice(invoice(line), inputs(products=[product()], mappings=[mapping()])))
        assert t.comparison == st.LINE_BLOCKED
        assert any("No unit cost was extracted" in r for r in t.blocked_reasons)


class TestReadOnly:
    def test_the_invoice_and_its_items_are_never_modified(self):
        line = item()
        before = dict(vars(line))
        inv = invoice(line)
        st.trace_invoice(inv, inputs(products=[product(canonical="BUD LIGHT 24/12 CAN NR")], mappings=[mapping()]))
        assert vars(line) == before and inv.items == [line]

    def test_the_trace_module_opens_no_database_session(self):
        source = (ROOT / "app/services/product_master/shadow_trace.py").read_text()
        for forbidden in ("AsyncSession", "get_session_factory", "session", "commit", "flush"):
            assert forbidden not in source, forbidden

    def test_the_script_traces_in_a_read_only_transaction_and_never_writes(self):
        source = (ROOT / "scripts/shadow_edi_compare.py").read_text()
        assert 'session.execute(text("SET TRANSACTION READ ONLY"))' in source
        assert "await session.rollback()" in source
        session_calls = {node.func.attr for node in ast.walk(ast.parse(source))
                         if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                         and ast.unparse(node.func.value) == "session"}
        assert session_calls == {"execute", "rollback"}, session_calls
        imported = {alias.name for node in ast.walk(ast.parse(source))
                    if isinstance(node, ast.ImportFrom) and node.module == "sqlalchemy" for alias in node.names}
        assert imported == {"select", "text"}, "no insert/update/delete construct is even imported"


class TestScriptInputs:
    def test_pdi_descriptions_are_keyed_by_the_code_edi_emits(self, tmp_path):
        from scripts.shadow_edi_compare import load_pdi_descriptions, parse_args, upc12_for

        csv_path = tmp_path / "pdi.csv"
        csv_path.write_text("item_code,description\n018200003349,BUD LIGHT 24/12 CAN\n0-12345-67890-5,  \n",
                            encoding="utf-8")
        assert load_pdi_descriptions(csv_path) == {CODE: "BUD LIGHT 24/12 CAN"}
        assert load_pdi_descriptions(None) == {}
        assert upc12_for(CODE) == "018200003349" and upc12_for("12345") is None
        args = parse_args(["--invoices", "1000540", "3376587", "--pdi-descriptions", str(csv_path)])
        assert args.invoices == ["1000540", "3376587"] and args.pdi_descriptions == csv_path
        assert parse_args([]).invoices is None, "no arguments: the original golden comparison"
