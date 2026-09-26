"""
Tests — the product display name in the Product Master review workflow.

A reviewer should not have to recognise a product from its UPC. What is
shown is decided once (app/services/product_master/display_name.py) and
used by the review API, the evidence panel and the human review package.

Pinned here:

  * canonical beats source; a source wording is shown only when the
    product's source descriptions are effectively one wording;
  * materially different source wordings — flavour, size, pack, variety
    pack — are never resolved to one: the product shows "Multiple source
    names" and every wording with its provenance, including the commercial
    CONFLICT rows whose disputed pack sizes appear in their names;
  * only formatting (case, spacing, punctuation, Zink's doubled brand)
    makes two wordings the same; a different word never does;
  * no precedence among Monarch, Zink and other price sheets is assumed;
  * with no description the name is explicitly unavailable;
  * the name is a label, not identity: nothing that resolves, matches or
    seeds products reads it, and equal names stay different products;
  * the API exposes it to USER, MANAGER and ADMIN alike, and the approval
    rules are unchanged;
  * the review package uses the same resolution as the UI and spells the
    ambiguity out;
  * review status and conflict state do not depend on it.
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.product_master.display_name import (
    AMBIGUOUS_LABEL,
    CLASS_POS,
    CLASS_PRICE_SHEET,
    CLASS_PRODUCT_SHEET,
    NAME_AMBIGUOUS,
    NAME_CANONICAL,
    NAME_SOURCE,
    NAME_UNAVAILABLE,
    UNAVAILABLE_LABEL,
    formatting_key,
    resolve_display_name,
)

ROOT = Path(__file__).resolve().parent.parent


def desc(description, *, role="SOURCE", sheet="data", system=None, row=1,
         file="f.xlsx") -> dict:
    return {"role": role, "description": description, "source_sheet": sheet,
            "source_system": system or ("item_sales_summary" if sheet == "data"
                                        else "distributor_price_sheet"),
            "source_file": file, "source_row": row}


def wordings(name) -> list[str]:
    return [w.description for w in name.wordings]


# ---------------------------------------------------------------------------
# The resolution policy
# ---------------------------------------------------------------------------


class TestCanonical:

    def test_canonical_is_preferred_over_every_source(self):
        name = resolve_display_name([
            desc("Bud lt 16oz", sheet="data"),
            desc("BUDWEISER LIGHT 16OZ CAN 4/6", sheet="Monarch Package"),
            desc("SOMETHING ELSE ENTIRELY", sheet="Zink - Tiki"),
            desc("TEST LAGER 4/6 16OZ", role="CANONICAL", sheet="Sheet1", row=4),
        ])
        assert (name.name, name.basis, name.label) == (
            "TEST LAGER 4/6 16OZ", NAME_CANONICAL, "TEST LAGER 4/6 16OZ")
        assert name.source_reference == "Sheet1 row 4"
        assert name.variant_count == 0

    def test_canonical_wins_even_when_the_sources_disagree(self):
        name = resolve_display_name([
            desc("FLAVOUR A C24 12OZ 6P", sheet="Monarch Package"),
            desc("FLAVOUR B C24 12OZ 6P", sheet="Monarch Package", row=2),
            desc("TEST CANONICAL", role="CANONICAL", sheet="Sheet2"),
        ])
        assert name.basis == NAME_CANONICAL


class TestSingleSourceWording:

    def test_one_source_wording_is_shown_as_source_derived(self):
        name = resolve_display_name([desc("TEST CIDER C24 12OZ 6P", sheet="Monarch Package",
                                          row=171)])
        assert (name.name, name.basis) == ("TEST CIDER C24 12OZ 6P", NAME_SOURCE)
        assert name.source_class == CLASS_PRICE_SHEET
        assert name.source_reference == "Monarch Package row 171"

    def test_the_same_wording_from_several_rows_is_one_wording(self):
        name = resolve_display_name([
            desc("TEST CIDER C24 12OZ 6P", sheet="Monarch Package", row=171),
            desc("TEST CIDER C24 12OZ 6P", sheet="Monarch Frontline", row=71),
        ])
        assert name.basis == NAME_SOURCE
        assert name.wordings[0].references == ("Monarch Frontline row 71",
                                               "Monarch Package row 171")

    @pytest.mark.parametrize("variants", [
        ["Mich Ultra 18/12 Can", "MICH ULTRA 18/12 CAN"],             # case
        ["MICH ULTRA  18/12 CAN", "MICH ULTRA 18/12 CAN "],           # spacing
        ["MICH ULTRA, 18/12 CAN", "MICH ULTRA - 18/12 CAN"],          # punctuation
        ["MICHELOB ULTRA MICHELOB ULTRA", "MICHELOB ULTRA"],          # Zink's doubled brand
    ])
    def test_formatting_only_differences_are_one_wording(self, variants):
        rows = [desc(v, sheet="Monarch Package", row=i) for i, v in enumerate(variants)]
        name = resolve_display_name(rows)
        assert name.basis == NAME_SOURCE, wordings(name)

    def test_the_shown_spelling_of_one_wording_is_deterministic(self):
        rows = [desc("MICHELOB ULTRA MICHELOB ULTRA", sheet="Zink - Tiki", row=1),
                desc("Michelob Ultra", sheet="Monarch Package", row=2)]
        first = resolve_display_name(rows)
        assert first.name == "Michelob Ultra", "the undoubled spelling is shown"
        assert resolve_display_name(rows[::-1]) == first

    def test_wording_is_preserved_exactly(self):
        name = resolve_display_name([desc("  MICH ULTRA 18/12 CAN  ", sheet="Sheet1")])
        assert name.name == "MICH ULTRA 18/12 CAN"


class TestMateriallyDifferentSourceNames:

    def test_different_flavours_are_not_resolved_to_one(self):
        rows = [desc("LEINENKUGEL GRAPEFRUIT SHANDY C24 12OZ 6P", sheet="Monarch Package", row=1202),
                desc("LEINENKUGEL OKTOBERFEST C24 12OZ 6P", sheet="Monarch Package", row=1200),
                desc("LEINENKUGEL RED LAGER C24 12OZ 6P", sheet="Monarch Package", row=1192)]
        name = resolve_display_name(rows)
        assert name.basis == NAME_AMBIGUOUS
        assert name.name is None and name.label == AMBIGUOUS_LABEL == "Multiple source names"
        assert name.variant_count == 3
        assert wordings(name) == [
            "LEINENKUGEL GRAPEFRUIT SHANDY C24 12OZ 6P",
            "LEINENKUGEL OKTOBERFEST C24 12OZ 6P",
            "LEINENKUGEL RED LAGER C24 12OZ 6P",
        ]
        assert name.wordings[0].references == ("Monarch Package row 1202",)

    @pytest.mark.parametrize(("a", "b"), [
        ("KIRIN ICHIBAN B12 22OZ", "KIRIN ICHIBAN B15 22OZ"),           # pack count
        ("PERONI B24 11.2OZ 12P", "PERONI B24 12OZ 12P"),               # size
        ("TEST GUMBALL C12 19.2OZ", "TEST GUMBALL C24 19.2OZ"),         # a CONFLICT's pack
    ])
    def test_a_pack_or_size_difference_is_ambiguous(self, a, b):
        name = resolve_display_name([desc(a, sheet="Monarch Frontline"),
                                     desc(b, sheet="Monarch Package", row=2)])
        assert name.basis == NAME_AMBIGUOUS
        assert sorted(wordings(name)) == sorted([a, b])

    def test_an_extra_word_is_never_treated_as_harmless(self):
        """'FOEDER FIEND' and 'FOEDER FIEND MANGO' are two products."""
        name = resolve_display_name([desc("FOEDER FIEND B24 12OZ 4P", sheet="Monarch Package"),
                                     desc("FOEDER FIEND MANGO B24 12OZ 4P",
                                          sheet="Monarch Package", row=2)])
        assert name.basis == NAME_AMBIGUOUS

    def test_different_variety_packs_are_ambiguous(self):
        name = resolve_display_name([
            desc("TRULY BRUNCH VARIETY PACK C24 12OZ 12PSL", sheet="Monarch Package"),
            desc("TRULY POOL PARTY VARIETY C24 12OZ 12PSL", sheet="Monarch Package", row=2),
        ])
        assert name.basis == NAME_AMBIGUOUS

    def test_no_precedence_is_assumed_between_price_sheets(self):
        """Monarch over Zink is not documented, so a disagreement is shown."""
        name = resolve_display_name([desc("TEST ALE C24 12OZ 6P", sheet="Monarch Package"),
                                     desc("TEST ALE TEST ALE", sheet="Zink - Tiki", row=2)])
        assert name.basis == NAME_AMBIGUOUS
        assert {w.source_class for w in name.wordings} == {CLASS_PRICE_SHEET}

    def test_frequency_does_not_decide(self):
        rows = [desc("FLAVOUR A C24", sheet="Monarch Package", row=i) for i in range(10)]
        rows.append(desc("FLAVOUR B C24", sheet="Monarch Package", row=99))
        assert resolve_display_name(rows).basis == NAME_AMBIGUOUS

    def test_the_ambiguous_result_is_stable_in_any_order(self):
        rows = [desc("B WORDING", sheet="Monarch Package", row=2),
                desc("A WORDING", sheet="Zink - Tiki", row=7),
                desc("C WORDING", sheet="Monarch Package", row=1)]
        first = resolve_display_name(rows)
        for ordering in (rows[::-1], rows[1:] + rows[:1]):
            assert resolve_display_name(ordering) == first


class TestWhichSourcesAreCompared:

    def test_distributor_product_sheets_are_compared_first(self):
        """The policy's documented strongest reference (§4 Tier 1)."""
        name = resolve_display_name([
            desc("TEST STOUT 4/6 12OZ", sheet="Sheet3", row=8),
            desc("TEST STOUT DIFFERENT WORDING C24", sheet="Monarch Package"),
        ])
        assert (name.basis, name.source_class) == (NAME_SOURCE, CLASS_PRODUCT_SHEET)

    def test_item_sales_is_used_only_when_nothing_else_describes_the_product(self):
        with_price_sheet = resolve_display_name([desc("Bud lt 16oz", sheet="data"),
                                                 desc("BUD LIGHT C24 16OZ", sheet="Monarch Package")])
        assert (with_price_sheet.basis, with_price_sheet.name) == (NAME_SOURCE, "BUD LIGHT C24 16OZ")
        alone = resolve_display_name([desc("Bud lt 16oz", sheet="data", row=3)])
        assert (alone.basis, alone.source_class) == (NAME_SOURCE, CLASS_POS)

    def test_a_disagreement_inside_the_product_sheet_is_ambiguous(self):
        name = resolve_display_name([desc("GOOSE BIG JUICY BEER HUG 15/19.2 CAN", sheet="Sheet1"),
                                     desc("GOOSE BIG JUICY BEER HUG N 15/19.2 CAN",
                                          sheet="Sheet1", row=2)])
        assert (name.basis, name.source_class) == (NAME_AMBIGUOUS, CLASS_PRODUCT_SHEET)


class TestUnavailable:

    def test_no_description_gives_the_explicit_fallback(self):
        name = resolve_display_name([])
        assert name.name is None and name.basis == NAME_UNAVAILABLE
        assert name.label == UNAVAILABLE_LABEL == "Product name unavailable"

    def test_blank_descriptions_are_not_names(self):
        assert resolve_display_name([desc("   "), desc("", role="CANONICAL",
                                                      sheet="Sheet1")]).basis == NAME_UNAVAILABLE


class TestFormattingKey:

    def test_it_keeps_every_word_and_number(self):
        assert formatting_key("Bells Oberon Ale C24 12oz 6P") == "BELLS OBERON ALE C24 12OZ 6P"
        assert formatting_key("3 Floyds, 19.2oz 12/19.2") == "3 FLOYDS 19.2OZ 12/19.2"

    def test_models_and_dicts_resolve_identically(self):
        rows = [desc("A C24", sheet="Monarch Package"), desc("B C24", sheet="Monarch Package", row=2)]
        assert resolve_display_name(rows) == resolve_display_name(
            [SimpleNamespace(**row) for row in rows])


class TestTheNameIsNotIdentity:

    IDENTITY_MODULES = (
        "app/services/product_master/candidates.py",
        "app/services/product_master/identifiers.py",
        "app/services/product_master/descriptions.py",
        "app/services/product_master/commercial.py",
        "app/services/master_commercial_review_service.py",
        "scripts/seed_product_master_identity.py",
        "scripts/seed_product_master_commercial.py",
        "scripts/seed_product_master_descriptions.py",
        "scripts/backfill_commercial_source_snapshot.py",
        "scripts/pilot_seed.py",
        "scripts/pilot_seed_plan.py",
    )

    @pytest.mark.parametrize("module", IDENTITY_MODULES)
    def test_nothing_that_resolves_seeds_or_decides_reads_it(self, module):
        assert "display_name" not in (ROOT / module).read_text()

    def test_equal_names_remain_different_products(self):
        from app.services.product_master.candidates import MasterSourceRow, build_candidates
        from app.services.product_master.identifiers import DISTRIBUTOR_WORKBOOK

        rows = [MasterSourceRow(source_system="distributor_price_sheet",
                                profile=DISTRIBUTOR_WORKBOOK, source_file="f.xlsx",
                                source_sheet="Sheet1", source_row=i, raw_identifier=upc,
                                description="SAME NAME 12OZ")
                for i, upc in enumerate(("018200001154", "087692000570"))]
        seeded = [p for p in build_candidates(rows).products.values() if p.canonical_upc]
        assert len(seeded) == 2

    def test_resolving_does_not_alter_the_descriptions(self):
        rows = [desc("x", sheet="Monarch Package"), desc("y", sheet="Monarch Package", row=2)]
        before = [dict(r) for r in rows]
        resolve_display_name(rows)
        assert rows == before

    def test_the_resolver_touches_no_database(self):
        source = (ROOT / "app/services/product_master/display_name.py").read_text()
        for forbidden in ("session", "execute(", ".add(", "commit", "insert(", "update("):
            assert forbidden not in source, forbidden

    def test_no_name_column_was_added_to_master_products(self):
        from app.models.product_master import MasterProduct

        names = {c.key for c in MasterProduct.__table__.columns}
        assert not names & {"product_name", "display_name", "name"}


# ---------------------------------------------------------------------------
# The review API
# ---------------------------------------------------------------------------

PRODUCT_ID = uuid.uuid4()
MAPPING_ID = uuid.uuid4()
STORE_ID = uuid.uuid4()


def _mapping(**overrides):
    base = {
        "id": MAPPING_ID, "product_id": PRODUCT_ID, "store_id": STORE_ID,
        "pdi_item_code": "01820000115", "commercial_unit_basis": "UNIT_IS_SELLING_UNIT",
        "units_accounted_for": 4, "case_cost": None, "cost_basis": "DISTRIBUTOR_CASE_PRICE",
        "approval_state": "REVIEW_REQUIRED", "reviewed_by": None, "reviewed_at": None,
        "proposed_units_accounted_for": None, "proposed_by": None, "proposed_note": None,
        "evidence": {"notes": "settled", "source_statements": [],
                     "source_store_identifier": "47708760"},
        "source_file": "Beer Inventory.xlsx", "source_sheet": "Sheet1", "source_row": 4,
        "source_snapshot": {"source_rows": []},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _description(role, description, sheet, row, system="distributor_price_sheet"):
    return SimpleNamespace(product_id=PRODUCT_ID, role=role, description=description,
                           source_system=system, source_file="Beer Inventory.xlsx",
                           source_sheet=sheet, source_row=row)


PRODUCT = SimpleNamespace(id=PRODUCT_ID, canonical_upc="018200001154")
STORE = SimpleNamespace(id=STORE_ID, display_name=None, identity_status="unresolved")
CANONICAL_ROWS = [
    _description("SOURCE", "Test lager 16oz", "data", 8, system="item_sales_summary"),
    _description("CANONICAL", "TEST LAGER 4/6 16OZ", "Sheet1", 4),
]
SINGLE_SOURCE_ROWS = [_description("SOURCE", "TEST CIDER C24 12OZ 6P", "Monarch Package", 171)]
AMBIGUOUS_ROWS = [
    _description("SOURCE", "TEST GUMBALL C12 19.2OZ", "Monarch Frontline", 14),
    _description("SOURCE", "TEST GUMBALL C24 19.2OZ", "Monarch Package", 59),
]


class FakeRepository:
    descriptions = CANONICAL_ROWS
    mapping = None

    def __init__(self, _session):
        pass

    def _mapping(self):
        return self.mapping or _mapping()

    async def list_candidates(self, **_):
        return [(self._mapping(), PRODUCT, STORE)]

    async def count_candidates(self, **_):
        return 1

    async def get(self, _id):
        return (self._mapping(), PRODUCT, STORE)

    async def legacy_mappings_for(self, _codes):
        return {}

    async def history_for(self, _id):
        return []

    async def descriptions_for(self, product_ids):
        return {pid: [d for d in self.descriptions if d.product_id == pid] for pid in product_ids}


@pytest.fixture
def api(app, monkeypatch):
    import app.api.v1.product_master as module
    from app.core.dependencies import require_authenticated_user
    from app.database.session import get_db
    from app.models.user import User

    def use(descriptions, mapping=None):
        repository = type("Repo", (FakeRepository,), {"descriptions": descriptions,
                                                      "mapping": mapping})
        monkeypatch.setattr(module, "MasterCommercialRepository", repository)

    use(CANONICAL_ROWS)

    async def no_db():
        yield None

    app.dependency_overrides[get_db] = no_db

    def as_role(role: str) -> None:
        app.dependency_overrides[require_authenticated_user] = lambda: User(
            id=uuid.uuid4(), username=f"{role.lower()}-user", password_hash="x",
            role=role, is_active=True, created_at=datetime.now(UTC))

    yield SimpleNamespace(as_role=as_role, use=use)
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_authenticated_user, None)


class TestTheReviewApi:

    @pytest.mark.parametrize("role", ["USER", "MANAGER", "ADMIN"])
    async def test_every_role_sees_the_product_name_in_the_queue(self, client, api, role):
        api.as_role(role)
        response = await client.get("/api/v1/product-master/commercial")
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert (item["product_name"], item["product_name_basis"]) == (
            "TEST LAGER 4/6 16OZ", "CANONICAL")
        assert item["canonical_identifier"] == "018200001154"

    async def test_a_single_source_wording_is_labelled_with_its_provenance(self, client, api):
        api.use(SINGLE_SOURCE_ROWS)
        api.as_role("MANAGER")
        item = (await client.get("/api/v1/product-master/commercial")).json()["items"][0]
        assert (item["product_name"], item["product_name_basis"]) == (
            "TEST CIDER C24 12OZ 6P", "SOURCE")
        assert item["product_name_source"] == CLASS_PRICE_SHEET
        assert item["product_name_reference"] == "Monarch Package row 171"
        assert item["product_name_variants"] == []

    @pytest.mark.parametrize("role", ["USER", "MANAGER", "ADMIN"])
    async def test_every_role_sees_every_ambiguous_wording(self, client, api, role):
        api.use(AMBIGUOUS_ROWS)
        api.as_role(role)
        item = (await client.get("/api/v1/product-master/commercial")).json()["items"][0]
        assert item["product_name"] == "Multiple source names"
        assert item["product_name_basis"] == "AMBIGUOUS_SOURCE"
        assert item["product_name_variant_count"] == 2
        assert item["product_name_variants"] == [
            {"description": "TEST GUMBALL C12 19.2OZ", "source_class": CLASS_PRICE_SHEET,
             "references": ["Monarch Frontline row 14"]},
            {"description": "TEST GUMBALL C24 19.2OZ", "source_class": CLASS_PRICE_SHEET,
             "references": ["Monarch Package row 59"]},
        ]

    async def test_a_conflict_shows_both_disputed_wordings_and_stays_a_conflict(
            self, client, api):
        api.use(AMBIGUOUS_ROWS, mapping=_mapping(commercial_unit_basis="CONFLICT",
                                                 units_accounted_for=None))
        api.as_role("MANAGER")
        response = await client.get(f"/api/v1/product-master/commercial/{MAPPING_ID}")
        candidate = response.json()["data"]["candidate"]
        assert (candidate["review_status"], candidate["is_conflict"]) == ("CONFLICT", True)
        assert candidate["product_name_basis"] == "AMBIGUOUS_SOURCE"
        assert [v["description"] for v in candidate["product_name_variants"]] == [
            "TEST GUMBALL C12 19.2OZ", "TEST GUMBALL C24 19.2OZ"]

    @pytest.mark.parametrize("role", ["USER", "MANAGER", "ADMIN"])
    async def test_every_role_sees_every_description_on_record(self, client, api, role):
        api.as_role(role)
        data = (await client.get(f"/api/v1/product-master/commercial/{MAPPING_ID}")).json()["data"]
        assert [d["role"] for d in data["evidence"]["descriptions"]] == ["CANONICAL", "SOURCE"]

    async def test_no_description_is_reported_not_invented(self, client, api):
        api.use([])
        api.as_role("USER")
        item = (await client.get("/api/v1/product-master/commercial")).json()["items"][0]
        assert item["product_name"] is None
        assert item["product_name_basis"] == "UNAVAILABLE"

    @pytest.mark.parametrize("action", ["approve", "reject"])
    async def test_a_user_still_cannot_decide(self, client, api, action):
        api.as_role("USER")
        response = await client.post(
            f"/api/v1/product-master/commercial/{MAPPING_ID}/{action}", json={})
        assert response.status_code == 403


class TestReviewStateDoesNotDependOnTheName:

    @pytest.mark.parametrize(("mapping", "status", "conflict"), [
        (_mapping(), "READY_FOR_REVIEW", False),
        (_mapping(commercial_unit_basis="CONFLICT", units_accounted_for=None),
         "CONFLICT", True),
    ])
    @pytest.mark.parametrize("descriptions", [CANONICAL_ROWS, SINGLE_SOURCE_ROWS,
                                              AMBIGUOUS_ROWS, []])
    def test_status_and_conflict_are_identical_whatever_the_name(
            self, mapping, status, conflict, descriptions):
        from app.api.v1.product_master import _row

        named = _row(mapping, PRODUCT, STORE, [], descriptions)
        nameless = _row(mapping, PRODUCT, STORE, [], [])
        name_fields = {k for k in type(named).model_fields if k.startswith("product_name")}
        assert named.model_dump(exclude=name_fields) == nameless.model_dump(exclude=name_fields)
        assert (named.review_status, named.is_conflict) == (status, conflict)


# ---------------------------------------------------------------------------
# The human review package
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, descriptions, mappings):
        self._descriptions, self._mappings, self._rows = descriptions, mappings, []

    def execute(self, sql, *_):
        if "FROM product_case_mappings" in sql:
            self._rows = []
        elif "FROM master_product_descriptions" in sql:
            self._rows = self._descriptions
        elif "FROM master_commercial_mappings m" in sql:
            self._rows = self._mappings
        else:
            raise AssertionError(sql)

    def fetchall(self):
        return list(self._rows)


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.readonly = None

    def set_session(self, readonly=None, autocommit=None):
        self.readonly = readonly

    def cursor(self):
        return self._cursor

    def close(self):
        pass


def _package_rows(monkeypatch, descriptions: dict[uuid.UUID, list[dict]]):
    import psycopg2

    from scripts.build_human_review_package import load

    description_rows, mapping_rows = [], []
    for index, (product_id, rows) in enumerate(descriptions.items()):
        for d in rows:
            description_rows.append((product_id, d["role"], d["description"], d["source_system"],
                                     d["source_file"], d["source_sheet"], d["source_row"]))
        mapping_rows.append((
            f"01820000{index:04d}", f"0182000{index:04d}", STORE_ID, "unresolved",
            "UNIT_IS_SELLING_UNIT", 4, None, "DISTRIBUTOR_CASE_PRICE", "REVIEW_REQUIRED",
            "Beer Inventory.xlsx", "Sheet1", index,
            {"source_statements": [], "source_store_identifier": "47708760"},
            product_id, None,
        ))
    connection = _Connection(_Cursor(description_rows, mapping_rows))
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: connection)
    return load("postgresql://unused"), connection


class TestTheReviewPackage:

    def test_product_name_and_its_basis_lead_the_business_columns(self):
        from scripts.build_human_review_package import COLUMNS

        assert COLUMNS[1:11] == ["product_name", "product_name_basis", "product_name_variants",
                                 "canonical_upc", "pdi_item_code", "store",
                                 "units_accounted_for", "case_cost", "cost_basis",
                                 "review_status"]
        for column in ("product_name_variant_count", "product_name_source",
                       "product_name_reference", "source_file", "source_sheet", "source_row",
                       "evidence_source", "evidence_notes"):
            assert column in COLUMNS

    def test_the_package_uses_the_same_resolution_as_the_api(self, monkeypatch):
        from app.api.v1.product_master import _row

        canonical, single, ambiguous, nameless = (uuid.uuid4() for _ in range(4))
        descriptions = {
            canonical: [desc("Test lager", sheet="data"),
                        desc("TEST LAGER 4/6 16OZ", role="CANONICAL", sheet="Sheet1", row=4)],
            single: [desc("TEST CIDER C24 12OZ 6P", sheet="Monarch Package", row=171)],
            ambiguous: [desc("TEST GUMBALL C12 19.2OZ", sheet="Monarch Frontline", row=14),
                        desc("TEST GUMBALL C24 19.2OZ", sheet="Monarch Package", row=59)],
            nameless: [],
        }
        rows, connection = _package_rows(monkeypatch, descriptions)
        assert connection.readonly is True
        for product_id, row in zip(descriptions, rows, strict=True):
            api_row = _row(_mapping(), PRODUCT, STORE, [],
                           [SimpleNamespace(**d) for d in descriptions[product_id]])
            assert row["product_name_basis"] == api_row.product_name_basis
            assert row["product_name"] == (api_row.product_name or UNAVAILABLE_LABEL)
            assert row["product_name_variant_count"] == api_row.product_name_variant_count
            assert row["product_name_source"] == api_row.product_name_source

    def test_an_ambiguous_product_spells_out_every_wording(self, monkeypatch):
        from scripts.build_human_review_package import COLUMNS

        product = uuid.uuid4()
        rows, _ = _package_rows(monkeypatch, {product: [
            desc("LEINENKUGEL GRAPEFRUIT SHANDY C24 12OZ 6P", sheet="Monarch Package", row=1202),
            desc("LEINENKUGEL OKTOBERFEST C24 12OZ 6P", sheet="Monarch Package", row=1200),
        ]})
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        written = list(csv.DictReader(io.StringIO(buffer.getvalue())))[0]
        assert written["product_name"] == "Multiple source names"
        assert written["product_name_basis"] == "AMBIGUOUS_SOURCE"
        assert written["product_name_variant_count"] == "2"
        assert written["product_name_variants"] == (
            "LEINENKUGEL GRAPEFRUIT SHANDY C24 12OZ 6P [Monarch Package row 1202] || "
            "LEINENKUGEL OKTOBERFEST C24 12OZ 6P [Monarch Package row 1200]")
        assert written["store"] == "Store 47708760"

    def test_a_single_wording_is_marked_source_derived(self, monkeypatch):
        product = uuid.uuid4()
        rows, _ = _package_rows(monkeypatch, {product: [
            desc("TEST CIDER C24 12OZ 6P", sheet="Monarch Package", row=171)]})
        assert (rows[0]["product_name"], rows[0]["product_name_basis"]) == (
            "TEST CIDER C24 12OZ 6P", "SOURCE")
        assert rows[0]["product_name_variants"] is None
        assert rows[0]["product_name_reference"] == "Monarch Package row 171"

    def test_the_package_still_reads_only(self):
        source = (ROOT / "scripts" / "build_human_review_package.py").read_text()
        assert not re.search(r"\b(INSERT|UPDATE|DELETE)\b", source)
        assert "set_session(readonly=True" in source
