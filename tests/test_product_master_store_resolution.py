"""
tests/test_product_master_store_resolution.py — LOCAL OFFLINE TESTS for
Phase 2B: source-store resolution and the identity-only seed's guards.

Two properties are defended here.

**Source-store resolution is a lookup, not an inference.** A workbook's
store code resolves through `store_identifiers` or it does not resolve at
all. There is no fuzzy match, no catalogue-overlap score, no invoice
assignment and no address similarity — Phase 2A disqualified the invoice
signal explicitly (those assignments were recorded as "reference-store
assignment, not a physical identity declaration") and measured catalogue
overlap as unfit to separate two same-market stores.

**Resolving a code is not identifying a store.** `source_store_resolved`
and `physical_store_identity_confirmed` are separate answers to separate
questions, and a resolved code whose store is still unidentified is the
normal case, not a failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.store import (  # noqa: E402
    IDENTITY_CONFIRMED,
    IDENTITY_UNRESOLVED,
    SOURCE_ITEM_SALES,
    TYPE_STORE_CODE,
)
from app.services.product_master.stores import (  # noqa: E402
    OUTCOME_AMBIGUOUS,
    OUTCOME_NO_SOURCE_CODE,
    OUTCOME_RESOLVED,
    OUTCOME_UNKNOWN_SOURCE_CODE,
    StoreIdentifierIndex,
    StoreRecord,
    resolve_commercial_candidates,
)

# The two real source codes and the Store records they already resolve to.
UNRESOLVED_A = StoreRecord("51e39a69-0000-0000-0000-000000000000", IDENTITY_UNRESOLVED)
UNRESOLVED_B = StoreRecord("a07b83b2-0000-0000-0000-000000000000", IDENTITY_UNRESOLVED)
CONFIRMED = StoreRecord("f218d97e-0000-0000-0000-000000000000", IDENTITY_CONFIRMED,
                        "Apple Foods II")


def index(*rows) -> StoreIdentifierIndex:
    return StoreIdentifierIndex(list(rows))


def live_index() -> StoreIdentifierIndex:
    return index(
        (SOURCE_ITEM_SALES, TYPE_STORE_CODE, "47708760", UNRESOLVED_A),
        (SOURCE_ITEM_SALES, TYPE_STORE_CODE, "86357232", UNRESOLVED_B),
        # The confirmed store is known by document evidence, never by a
        # store code — exactly as the real table holds it.
        ("document", "customer_name", "APPLE FOODS II", CONFIRMED),
    )


class TestSourceStoreResolution:
    def test_a_known_store_code_resolves_to_its_store_record(self):
        resolution = live_index().resolve("47708760")
        assert resolution.outcome == OUTCOME_RESOLVED
        assert resolution.store_id == UNRESOLVED_A.store_id
        assert resolution.source_store_resolved is True

    def test_an_unconfirmed_store_still_resolves(self):
        # The whole point of Phase 2A: identity is a separate question.
        resolution = live_index().resolve("86357232")
        assert resolution.source_store_resolved is True
        assert resolution.physical_store_identity_confirmed is False
        assert resolution.store_identity_status == IDENTITY_UNRESOLVED

    def test_an_unknown_code_resolves_to_nothing_rather_than_a_near_match(self):
        resolution = live_index().resolve("99999999")
        assert resolution.outcome == OUTCOME_UNKNOWN_SOURCE_CODE
        assert resolution.store_id is None

    def test_a_missing_code_is_distinguished_from_an_unknown_one(self):
        assert live_index().resolve(None).outcome == OUTCOME_NO_SOURCE_CODE
        assert live_index().resolve("  ").outcome == OUTCOME_NO_SOURCE_CODE

    def test_lookup_is_scoped_by_source_system_and_type(self):
        # The same digits under a different source system are a different
        # fact and must not resolve.
        assert live_index().resolve("47708760", source_system="document").outcome == (
            OUTCOME_UNKNOWN_SOURCE_CODE
        )
        assert live_index().resolve(
            "47708760", identifier_type="customer_name"
        ).outcome == OUTCOME_UNKNOWN_SOURCE_CODE

    def test_two_stores_claiming_one_code_is_ambiguous_not_a_choice(self):
        ambiguous = index(
            (SOURCE_ITEM_SALES, TYPE_STORE_CODE, "47708760", UNRESOLVED_A),
            (SOURCE_ITEM_SALES, TYPE_STORE_CODE, "47708760", CONFIRMED),
        )
        resolution = ambiguous.resolve("47708760")
        assert resolution.outcome == OUTCOME_AMBIGUOUS
        assert resolution.store_id is None, "no store may be picked"

    def test_surrounding_whitespace_does_not_defeat_an_exact_match(self):
        assert live_index().resolve(" 47708760 ").outcome == OUTCOME_RESOLVED


class TestResolutionIsNotIdentification:
    def test_the_two_facts_are_separate_fields(self):
        resolved_unconfirmed = live_index().resolve("47708760")
        assert resolved_unconfirmed.source_store_resolved is True
        assert resolved_unconfirmed.physical_store_identity_confirmed is False

    def test_a_confirmed_store_reports_both(self):
        resolution = live_index().resolve(
            "APPLE FOODS II", source_system="document", identifier_type="customer_name",
        )
        assert resolution.source_store_resolved is True
        assert resolution.physical_store_identity_confirmed is True

    def test_nothing_about_invoices_or_catalogues_can_reach_the_resolver(self):
        # The resolver's only input is the identifier index; there is no
        # parameter through which an invoice assignment or an overlap score
        # could influence it.
        empty = index()
        assert empty.resolve("47708760").outcome == OUTCOME_UNKNOWN_SOURCE_CODE


class TestCommercialCandidateAnnotation:
    def test_every_candidate_gets_both_answers(self):
        candidates = [
            {"store_context": "47708760"}, {"store_context": "86357232"},
            {"store_context": "00000000"}, {"store_context": None},
        ]
        tally = resolve_commercial_candidates(candidates, live_index())
        assert tally["total"] == 4
        assert tally["source_store_resolved"] == 2
        assert tally["source_store_unknown"] == 1
        assert tally["no_source_code"] == 1
        assert tally["ambiguous"] == 0
        assert tally["physical_identity_confirmed"] == 0
        assert tally["physical_identity_unresolved"] == 2

    def test_the_two_real_codes_resolve_without_identity_confirmation(self):
        # Mirrors the verified Phase 2B result: every candidate resolves,
        # none is physically identified.
        candidates = [{"store_context": code}
                      for code in ["47708760", "86357232"] * 10]
        tally = resolve_commercial_candidates(candidates, live_index())
        assert tally["source_store_resolved"] == tally["total"] == 20
        assert tally["source_store_unknown"] == 0
        assert tally["ambiguous"] == 0
        assert tally["physical_identity_confirmed"] == 0

    def test_an_unresolved_candidate_carries_no_store_id(self):
        candidates = [{"store_context": "99999999"}]
        resolve_commercial_candidates(candidates, live_index())
        assert candidates[0]["store_id"] is None
        assert candidates[0]["source_store_resolved"] is False


class TestSeedDatabaseGuard:
    """The seed must never reach a remote database, and has no override."""

    def _assert(self):
        from scripts.seed_product_master_identity import assert_local_database

        return assert_local_database

    @pytest.mark.parametrize("url", [
        "postgresql+asyncpg://u:p@aws-0-ap-southeast-2.pooler.supabase.com:5432/postgres",
        "postgresql+asyncpg://u:p@db.abcdef.supabase.co:5432/postgres",
        "postgresql+asyncpg://u:p@x.rds.amazonaws.com:5432/db",
        "postgresql+asyncpg://u:p@svc.render.com:5432/db",
        "postgresql+asyncpg://u:p@ep-cool.neon.tech:5432/db",
        "postgresql+asyncpg://u:p@10.0.0.5:5432/db",
        "postgresql+asyncpg://u:p@db.internal:5432/db",
    ])
    def test_remote_and_non_local_hosts_are_refused(self, url):
        with pytest.raises(SystemExit) as exit_info:
            self._assert()(url)
        assert "REFUSING TO SEED" in str(exit_info.value)

    @pytest.mark.parametrize("url", [
        "postgresql+asyncpg://u:p@localhost:5432/invoice",
        "postgresql+psycopg2://u:p@127.0.0.1:5432/invoice",
    ])
    def test_local_hosts_are_permitted(self, url):
        assert self._assert()(url) in {"localhost", "127.0.0.1"}

    def test_there_is_no_override_flag(self):
        source = (Path(__file__).resolve().parent.parent
                  / "scripts" / "seed_product_master_identity.py").read_text()
        for forbidden in ("--force", "--allow-remote", "--i-know-what", "--no-guard",
                          "--skip-guard", "--override"):
            assert forbidden not in source


class TestSeedSelection:
    """What the seed will and will not consider a product."""

    def test_only_resolved_candidates_are_seedable(self):
        from app.models.product_master import (
            STATE_AUTO_MATCHED,
            STATE_CONFLICT,
            STATE_UNRESOLVED,
        )
        from app.services.product_master.candidates import ProductCandidate
        from scripts.seed_product_master_identity import seedable

        def candidate(state):
            return ProductCandidate("k", "UPC_A", "018200967214", state)

        assert seedable(candidate(STATE_AUTO_MATCHED)) is True
        assert seedable(candidate(STATE_UNRESOLVED)) is False
        assert seedable(candidate(STATE_CONFLICT)) is False

    def test_the_seed_never_writes_a_commercial_mapping(self):
        source = (Path(__file__).resolve().parent.parent
                  / "scripts" / "seed_product_master_identity.py").read_text()
        # The table may be named in prose; the model must never be constructed.
        assert "MasterCommercialMapping(" not in source

    def test_the_seed_does_not_touch_legacy_master_tables(self):
        import re

        source = (Path(__file__).resolve().parent.parent
                  / "scripts" / "seed_product_master_identity.py").read_text()
        # `(?<!Master)` matters: MasterProductIdentifier is the NEW model and
        # contains the legacy name as a substring.
        for legacy in ("ProductCaseMapping", "ProductIdentity", "ProductIdentifier",
                       "ProductDataProposal", "StoreProductReference"):
            assert not re.search(rf"(?<!Master){legacy}\(", source), (
                f"legacy model {legacy} must never be constructed by the seed"
            )
