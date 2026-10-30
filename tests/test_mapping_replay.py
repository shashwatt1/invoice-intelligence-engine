"""
The replay measures with the product's own rules — tests/test_mapping_replay.py

Pins what the first replay got wrong: it shared decisions made at source
identities (unresolved store records), which the product never does, and it
applied a shared value against a printed pack that disagreed — the two
incorrect C-18 lines.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scripts.mapping_replay import AUTO_SHARED, HELD, MANUAL, Line, replay

T0 = datetime(2026, 9, 1, tzinfo=UTC)
C18 = "01820096721"


def line(inv, minutes, store, truth, *, physical=True, pack=None, code=C18):
    return Line(invoice_id=inv, at=T0 + timedelta(minutes=minutes), store_id=store, vendor_id="v",
                code=code, exportable=True, truth=truth, upc_entry=None, physical=physical, pack_size=pack,
                description="MICHELOB ULTRA " + (pack or ""))


def shared(lines):
    return replay(lines, [], timeline="steady_state")["shared"]


class TestTheReplayFollowsTheProductRules:
    def test_a_source_identitys_decision_is_never_shared(self):
        result = shared([line("a", 0, "source", 1, physical=False), line("b", 1, "wolf", 18)])
        assert result["outcomes"].get(AUTO_SHARED, 0) == 0
        assert result["incorrect_automatic_lines"] == 0
        assert result["outcomes"][MANUAL] == 2

    def test_a_shared_value_against_a_printed_c18_is_held_not_counted_as_automation(self):
        result = shared([line("a", 0, "rcm", 1, pack="C-18 12OZ"), line("b", 1, "wolf", 18, pack="C-18 12OZ")])
        assert result["outcomes"][HELD] == 1
        assert result["incorrect_automatic_lines"] == 0

    def test_an_agreeing_shared_value_is_counted_and_judged(self):
        result = shared([line("a", 0, "rcm", 24, pack="24/12OZ", code="01200000129"),
                         line("b", 1, "wolf", 24, pack="24/12OZ", code="01200000129")])
        assert (result["outcomes"][AUTO_SHARED], result["correct_automatic_lines"]) == (1, 1)

    def test_a_wrong_shared_value_with_no_printed_pack_is_reported_as_incorrect(self):
        result = shared([line("a", 0, "rcm", 6, code="01200000129"), line("b", 1, "wolf", 12, code="01200000129")])
        assert result["incorrect_automatic_lines"] == 1
        assert result["correct_automatic_lines"] == 0
