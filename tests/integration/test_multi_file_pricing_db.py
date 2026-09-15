"""
tests/integration/test_multi_file_pricing_db.py — several source files,
one store, in the per-row pricing table.

Pins the contract that lets Store 86357232's two same-period exports
coexist: rows are keyed by (store, file, sheet, row), so a second file
adds rows beside the first's rather than replacing them; a re-import of
the same file updates in place; a second store's identically named
workbook is a different key; and a copy of a file under another name is
recognised by its content before anything is written.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select

from app.models.product_reference import BASIS_PERIOD_AVERAGE, ProductPricing
from app.repositories.product_reference_repository import (
    ProductReferenceRepository,
    records_fingerprint,
)
from app.services.beer_inventory_import import ReferenceRecord
from tests.integration.conftest import requires_db, store_id

pytestmark = requires_db

A, B = "86357232", "47708760"
PERIOD = (date(2023, 7, 1), date(2026, 9, 30))


def rec(row, code, cost, retail, description="x"):
    return ReferenceRecord(
        sheet="data", row=row, distributor="store", raw_identifier=code, item_code=code,
        description=description, pricing_basis=BASIS_PERIOD_AVERAGE,
        unit_cost=None if cost is None else Decimal(cost), unit_retail=Decimal(retail),
        effective_from=PERIOD[0], effective_to=PERIOD[1],
    )


async def _import(session, records, *, store, source_file):
    counts = await ProductReferenceRepository(session).import_records(
        records, store_id=store_id(store), source_file=source_file, imported_at=datetime.now(UTC))
    await session.commit()
    return counts


async def _rows(session, store, code=None):
    query = select(ProductPricing).where(ProductPricing.store_id == store_id(store))
    if code:
        query = query.where(ProductPricing.item_code == code)
    return list((await session.execute(
        query.order_by(ProductPricing.source_file, ProductPricing.source_row))).scalars())


class TestSameStoreSeveralFiles:
    async def test_a_second_file_adds_beside_the_first_with_its_own_provenance(self, db_session):
        await _import(db_session, [rec(5, "01200013027", None, "2.29", "Mountain dew baja blast 20oz")],
                      store=A, source_file="file_1.xlsx")
        await _import(db_session, [rec(5, "01200013027", "1.487", "2.3151", "MTN DEW BAJA BLAST")],
                      store=A, source_file="file_2.xlsx")

        rows = await _rows(db_session, A, "01200013027")
        assert [(r.source_file, r.source_sheet, r.source_row, r.unit_cost, r.unit_retail) for r in rows] == [
            ("file_1.xlsx", "data", 5, None, Decimal("2.2900")),
            ("file_2.xlsx", "data", 5, Decimal("1.4870"), Decimal("2.3151")),
        ]
        # both readings are what the matcher sees — neither was chosen for it
        pricing = await ProductReferenceRepository(db_session).pricing_for(store_id(A), ["01200013027"])
        assert len(pricing["01200013027"]) == 2

    async def test_null_cost_from_one_file_does_not_erase_a_real_cost_from_another(self, db_session):
        await _import(db_session, [rec(5, "07825000020", "1.28", "1.99")], store=A, source_file="f2.xlsx")
        await _import(db_session, [rec(9, "07825000020", None, "1.99")], store=A, source_file="f1.xlsx")
        costs = {r.source_file: r.unit_cost for r in await _rows(db_session, A, "07825000020")}
        assert costs == {"f1.xlsx": None, "f2.xlsx": Decimal("1.2800")}

    async def test_reimporting_the_same_file_updates_in_place(self, db_session):
        first = await _import(db_session, [rec(5, "0182002500", "1.23", "1.49")], store=A, source_file="f.xlsx")
        again = await _import(db_session, [rec(5, "0182002500", "1.25", "1.49")], store=A, source_file="f.xlsx")
        assert first["pricing_inserted"] == 1 and again["pricing_updated"] == 1
        [row] = await _rows(db_session, A)
        assert row.unit_cost == Decimal("1.2500")

    async def test_identity_takes_the_first_description_and_says_which_file(self, db_session):
        await _import(db_session, [rec(5, "01200013027", None, "2.29", "Mountain dew baja blast 20oz")],
                      store=A, source_file="file_1.xlsx")
        await _import(db_session, [rec(5, "01200013027", "1.487", "2.3151", "MTN DEW BAJA BLAST")],
                      store=A, source_file="file_2.xlsx")
        identity = (await ProductReferenceRepository(db_session).identity_for(store_id(A), ["01200013027"]))["01200013027"]
        assert identity.description == "Mountain dew baja blast 20oz"
        assert identity.provenance["description"]["source_file"] == "file_1.xlsx"


class TestStoreIsolation:
    async def test_the_same_filename_in_two_stores_is_two_sets_of_rows(self, db_session):
        # Both stores' exporters name the file the same. Store B's rows
        # must survive store A's import untouched, values and store alike.
        await _import(db_session, [rec(5, "06206705146", "23.40", "29.79")], store=B, source_file="Beer Inventory.xlsx")
        await _import(db_session, [rec(5, "06206705146", "24.00", "26.99")], store=A, source_file="Beer Inventory.xlsx")

        b_rows, a_rows = await _rows(db_session, B), await _rows(db_session, A)
        assert [(r.store_id, r.unit_cost) for r in b_rows] == [(store_id(B), Decimal("23.4000"))]
        assert [(r.store_id, r.unit_cost) for r in a_rows] == [(store_id(A), Decimal("24.0000"))]

    async def test_a_second_store_import_changes_nothing_for_the_first(self, db_session):
        await _import(db_session, [rec(5, "06206705146", "23.40", "29.79", "Labatt blue 30cans"),
                                   rec(6, "01820011030", "22.70", "25.72", "Bud 30")],
                      store=B, source_file="Mckinley.xlsx")
        before = await ProductReferenceRepository(db_session).content_fingerprints(store_id(B))
        identity_before = (await ProductReferenceRepository(db_session).identity_for(store_id(B), ["06206705146"]))["06206705146"].description

        await _import(db_session, [rec(5, "06206705146", "1.00", "2.00", "Labatts Blue 30pk")],
                      store=A, source_file="Item_Sales_Summary_x.xlsx")

        assert await ProductReferenceRepository(db_session).content_fingerprints(store_id(B)) == before
        assert (await ProductReferenceRepository(db_session).identity_for(store_id(B), ["06206705146"]))["06206705146"].description == identity_before
        # and lookups are store-scoped
        assert set(await ProductReferenceRepository(db_session).pricing_for(store_id(B), ["06206705146"])) == {"06206705146"}
        assert (await ProductReferenceRepository(db_session).pricing_for(store_id(B), ["06206705146"]))["06206705146"][0].unit_cost == Decimal("23.4000")


class TestDuplicateContent:
    async def test_a_copy_under_another_name_is_recognised_by_its_content(self, db_session):
        records = [rec(5, "01200013027", None, "2.29"), rec(6, "07825000020", "1.28", "1.99")]
        await _import(db_session, records, store=A, source_file="Item_Sales_Summary_2026-09-14T15_45_30.014Z.xlsx")

        fingerprints = await ProductReferenceRepository(db_session).content_fingerprints(store_id(A))
        assert fingerprints["Item_Sales_Summary_2026-09-14T15_45_30.014Z.xlsx"] == records_fingerprint(records)
        # a re-download with "(1)" in the name would be refused by the importer:
        twins = [f for f, fp in fingerprints.items()
                 if f != "Item_Sales_Summary_2026-09-14T15_45_30.014Z (1).xlsx" and fp == records_fingerprint(records)]
        assert twins == ["Item_Sales_Summary_2026-09-14T15_45_30.014Z.xlsx"]

    async def test_a_genuinely_different_file_is_not_a_twin(self, db_session):
        await _import(db_session, [rec(5, "01200013027", None, "2.29")], store=A, source_file="f1.xlsx")
        other = [rec(5, "01200013027", "1.487", "2.3151")]
        fingerprints = await ProductReferenceRepository(db_session).content_fingerprints(store_id(A))
        assert not any(fp == records_fingerprint(other) for fp in fingerprints.values())

    async def test_fingerprints_are_per_store(self, db_session):
        records = [rec(5, "01200013027", None, "2.29")]
        await _import(db_session, records, store=B, source_file="f.xlsx")
        assert await ProductReferenceRepository(db_session).content_fingerprints(store_id(A)) == {}
