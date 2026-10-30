"""
tests/integration/test_inventory_reference_db.py — reviewed commercial reference values on inventory products, against Postgres.

Inventory products come from a store's catalogue (store_product_references) and its movements. Approved
commercial mappings only ever *annotate* them, with provenance: units per case and a confirmed description.

  * a mapping alone never puts a product into a store's inventory; reading references writes nothing;
  * an approved store-specific mapping beats a shared one (with a recorded reason), a shared one is the fallback
    only where the store has no value of its own that disagrees, and a store's confirmed case mapping is never
    silently replaced;
  * conflicting, unapproved or ambiguous (one code, several master products) values are held, never chosen;
  * a description is filled in only where the store's own is missing, and every value names its source,
    scope, approval status and mapping;
  * balances are untouched by any of it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.models.inventory import InventoryMovement, InventoryReceipt
from app.models.product_data_proposal import ProductDataProposal
from app.models.product_master import MasterCommercialMapping, MasterProduct
from app.models.store_product_reference import StoreProductReference
from app.repositories.product_case_mapping_repository import ProductCaseMappingRepository
from app.services.product_master import shared_decisions
from tests.integration.conftest import (
    PHYSICAL_STORE,
    RCM_STORE,
    SOURCE_ONLY_STORE,
    requires_db,
    store_id,
)
from tests.integration.test_api_db import api_client, process_file  # noqa: F401 — fixture reuse
from tests.integration.test_inventory_db import _clean_inventory, switch_off, switch_on  # noqa: F401

pytestmark = requires_db

CODE = "03600029145"            # catalogue product used throughout (UPC 036000291452)
OTHER = "01200000129"


@pytest.fixture(autouse=True)
async def environment(api_client, db_session, monkeypatch):  # noqa: F811
    await switch_on(db_session, monkeypatch)
    await db_session.execute(text("TRUNCATE master_products CASCADE"))
    await db_session.commit()
    monkeypatch.setattr(get_settings(), "product_master_commercial_resolution", True)
    yield
    await switch_off(db_session, monkeypatch)


async def catalogue(db_session, sid, code=CODE, description="POS SHORTHAND COLA"):
    db_session.add(StoreProductReference(store_id=sid, item_code=code, scan_code_raw=code, description=description,
                                         source_file="test export.xlsx", imported_at=datetime.now(UTC)))
    await db_session.commit()


async def product(db_session, code=CODE, description=None):
    created, _ = await shared_decisions.find_or_create_product(db_session, code)
    created.canonical_description = description
    await db_session.commit()
    return created


async def mapping(db_session, prod, units, *, sid=None, state="APPROVED", override=None, code=CODE):
    row = MasterCommercialMapping(
        product_id=prod.id, store_id=sid, pdi_item_code=code, commercial_unit_basis="UNIT_IS_SELLING_UNIT",
        units_accounted_for=units, approval_state=state, override_reason=override, source_snapshot={}, evidence={},
        source_system="test")
    db_session.add(row)
    await db_session.commit()
    return row


async def legacy(db_session, sid, units, code=CODE):
    await ProductCaseMappingRepository(db_session).upsert(
        store_id=sid, item_code=code, units_per_case=units, description="SYNTH", source="APPROVED")
    await db_session.commit()


async def rows_of(client, sid):
    r = await client.get(f"/api/v1/inventory/stores/{sid}/items", params={"page_size": 500})
    assert r.status_code == 200, r.text
    return {i["item_code"]: i for i in r.json()["items"]}


async def counts(db_session):
    return tuple([(await db_session.execute(select(func.count()).select_from(t))).scalar() for t in (
        InventoryMovement, InventoryReceipt, StoreProductReference, ProductDataProposal, MasterCommercialMapping)])


class TestUnitsPerCase:
    async def test_a_product_with_no_reviewed_source_is_missing_not_guessed(self, api_client, db_session):  # noqa: F811
        sid = store_id(PHYSICAL_STORE)
        await catalogue(db_session, sid)
        ref = (await rows_of(api_client, sid))[CODE]["reference"]["units_per_case"]
        assert (ref["value"], ref["status"], ref["source"]) == (None, "MISSING", None)

    async def test_a_shared_value_is_the_fallback_with_its_provenance(self, api_client, db_session):  # noqa: F811
        sid = store_id(PHYSICAL_STORE)
        await catalogue(db_session, sid)
        shared = await mapping(db_session, await product(db_session), 12)
        ref = (await rows_of(api_client, sid))[CODE]["reference"]["units_per_case"]
        assert (ref["value"], ref["status"], ref["source"]) == (12, "RESOLVED", "PRODUCT_MASTER_GLOBAL")
        assert (ref["scope"], ref["approval"], ref["mapping_id"]) == ("All stores (shared value)", "APPROVED", str(shared.id))

    async def test_an_approved_store_specific_mapping_with_a_reason_beats_the_shared_value_only_at_that_store(
        self, api_client, db_session,  # noqa: F811
    ):
        a, b = store_id(PHYSICAL_STORE), store_id(RCM_STORE)
        for sid in (a, b):
            await catalogue(db_session, sid)
        prod = await product(db_session)
        await mapping(db_session, prod, 1)
        own = await mapping(db_session, prod, 18, sid=a, override="sold as singles here")
        at_a = (await rows_of(api_client, a))[CODE]["reference"]["units_per_case"]
        at_b = (await rows_of(api_client, b))[CODE]["reference"]["units_per_case"]
        assert (at_a["value"], at_a["source"], at_a["mapping_id"]) == (18, "PRODUCT_MASTER_PHYSICAL_STORE", str(own.id))
        assert (at_b["value"], at_b["source"]) == (1, "PRODUCT_MASTER_GLOBAL")           # the override does not leak

    async def test_a_store_specific_value_that_disagrees_with_the_shared_one_without_a_reason_is_held(
        self, api_client, db_session,  # noqa: F811
    ):
        sid = store_id(PHYSICAL_STORE)
        await catalogue(db_session, sid)
        prod = await product(db_session)
        await mapping(db_session, prod, 1)
        await mapping(db_session, prod, 18, sid=sid)
        ref = (await rows_of(api_client, sid))[CODE]["reference"]["units_per_case"]
        assert (ref["value"], ref["status"]) == (None, "HELD")
        assert any("disagree" in n for n in ref["notes"])

    async def test_a_stores_confirmed_case_mapping_is_not_silently_replaced_by_a_shared_value(self, api_client, db_session):  # noqa: F811
        sid = store_id(PHYSICAL_STORE)
        await catalogue(db_session, sid)
        await mapping(db_session, await product(db_session), 1)
        await legacy(db_session, sid, 18)
        ref = (await rows_of(api_client, sid))[CODE]["reference"]["units_per_case"]
        assert (ref["value"], ref["status"]) == (None, "HELD")
        assert any("18" in n for n in ref["notes"])
        assert (await ProductCaseMappingRepository(db_session).get(sid, CODE)).units_per_case == 18   # untouched

    async def test_a_stores_confirmed_case_mapping_alone_resolves_as_store_confirmed(self, api_client, db_session):  # noqa: F811
        sid = store_id(PHYSICAL_STORE)
        await catalogue(db_session, sid)
        await legacy(db_session, sid, 6)
        ref = (await rows_of(api_client, sid))[CODE]["reference"]["units_per_case"]
        assert (ref["value"], ref["status"], ref["source"], ref["approval"]) == (6, "RESOLVED", "LEGACY_FALLBACK", "STORE_CONFIRMED")

    async def test_a_mapping_that_is_not_approved_is_never_used(self, api_client, db_session):  # noqa: F811
        sid = store_id(PHYSICAL_STORE)
        await catalogue(db_session, sid)
        await mapping(db_session, await product(db_session), 12, state="PENDING")
        ref = (await rows_of(api_client, sid))[CODE]["reference"]["units_per_case"]
        assert (ref["value"], ref["status"]) == (None, "MISSING")
        assert any("PENDING" in n for n in ref["notes"])

    async def test_a_shared_value_and_the_distributor_evidence_mapping_that_disagree_are_held(
        self, api_client, db_session, monkeypatch,  # noqa: F811
    ):
        monkeypatch.setattr(get_settings(), "commercial_source_identity", SOURCE_ONLY_STORE)
        sid = store_id(PHYSICAL_STORE)
        await catalogue(db_session, sid)
        prod = await product(db_session)
        await mapping(db_session, prod, 12)                                          # a reviewer's shared value
        await mapping(db_session, prod, 24, sid=store_id(SOURCE_ONLY_STORE))         # distributor evidence, approved, different
        ref = (await rows_of(api_client, sid))[CODE]["reference"]["units_per_case"]
        assert (ref["value"], ref["status"]) == (None, "HELD")
        assert any("disagree" in n for n in ref["notes"])

    async def test_with_commercial_resolution_off_inventory_follows_the_export_and_ignores_shared_values(
        self, api_client, db_session, monkeypatch,  # noqa: F811
    ):
        monkeypatch.setattr(get_settings(), "product_master_commercial_resolution", False)
        sid = store_id(PHYSICAL_STORE)
        await catalogue(db_session, sid)
        await mapping(db_session, await product(db_session), 12)
        assert (await rows_of(api_client, sid))[CODE]["reference"]["units_per_case"]["status"] == "MISSING"
        await legacy(db_session, sid, 6)
        assert (await rows_of(api_client, sid))[CODE]["reference"]["units_per_case"]["value"] == 6


class TestAmbiguousProductIdentity:
    async def test_a_code_that_reaches_two_master_products_holds_both_units_and_description(self, api_client, db_session):  # noqa: F811
        sid = store_id(PHYSICAL_STORE)
        await catalogue(db_session, sid, description=None)
        first = await product(db_session, description="NORTHWIND COLA 12PK")
        await mapping(db_session, first, 12)
        twin = MasterProduct(canonical_key="upc:twin", canonical_upc=first.canonical_upc, identity_basis=first.identity_basis,
                             identity_state=first.identity_state, canonical_description="NORTHWIND COLA 6PK")
        db_session.add(twin)
        await db_session.commit()
        row = (await rows_of(api_client, sid))[CODE]
        assert row["reference"]["units_per_case"]["status"] == "HELD" and row["reference"]["units_per_case"]["value"] is None
        assert row["reference"]["description"]["status"] == "HELD"
        assert any("ambiguous" in n for n in row["reference"]["units_per_case"]["notes"])
        assert (row["description"], row["description_from_reference"]) == (None, False)      # nothing chosen

    async def test_a_mapping_belonging_to_another_product_than_the_code_names_is_held(self, api_client, db_session):  # noqa: F811
        sid = store_id(PHYSICAL_STORE)
        await catalogue(db_session, sid)
        await product(db_session)                                               # the product the code's UPC names
        stray = MasterProduct(canonical_key="upc:stray", canonical_upc="999999999994", identity_basis="UPC_A",
                              identity_state="AUTO_MATCHED")
        db_session.add(stray)
        await db_session.commit()
        await mapping(db_session, stray, 12)                                    # approved, but for a different product
        ref = (await rows_of(api_client, sid))[CODE]["reference"]["units_per_case"]
        assert (ref["value"], ref["status"]) == (None, "HELD")


class TestDescriptions:
    async def test_a_missing_store_description_is_filled_from_the_confirmed_commercial_one_with_provenance(
        self, api_client, db_session,  # noqa: F811
    ):
        sid = store_id(PHYSICAL_STORE)
        await catalogue(db_session, sid, description=None)
        await product(db_session, description="NORTHWIND COLA 12PK")
        row = (await rows_of(api_client, sid))[CODE]
        assert (row["description"], row["description_from_reference"]) == ("NORTHWIND COLA 12PK", True)
        ref = row["reference"]["description"]
        assert (ref["status"], ref["source"], ref["approval"], ref["scope"]) == (
            "RESOLVED", "PRODUCT_MASTER_CANONICAL_DESCRIPTION", "CONFIRMED", "Product Master")

    async def test_an_existing_store_description_is_never_overwritten(self, api_client, db_session):  # noqa: F811
        sid = store_id(PHYSICAL_STORE)
        await catalogue(db_session, sid, description="POS SHORTHAND COLA")
        await product(db_session, description="NORTHWIND COLA 12PK")
        row = (await rows_of(api_client, sid))[CODE]
        assert (row["description"], row["description_from_reference"]) == ("POS SHORTHAND COLA", False)
        assert row["reference"]["description"]["value"] == "NORTHWIND COLA 12PK"            # shown beside it, not over it

    async def test_a_product_with_no_confirmed_description_stays_as_the_store_named_it(self, api_client, db_session):  # noqa: F811
        sid = store_id(PHYSICAL_STORE)
        await catalogue(db_session, sid, description="POS SHORTHAND COLA")
        await product(db_session, description=None)
        row = (await rows_of(api_client, sid))[CODE]
        assert (row["description"], row["reference"]["description"]["status"]) == ("POS SHORTHAND COLA", "MISSING")


class TestAssortmentStaysSeparate:
    async def test_a_commercial_mapping_alone_never_puts_a_product_in_a_stores_inventory(self, api_client, db_session):  # noqa: F811
        sid = store_id(PHYSICAL_STORE)
        await catalogue(db_session, sid, code=CODE)
        await mapping(db_session, await product(db_session, OTHER), 12, code=OTHER)        # approved, shared, not in the catalogue
        await legacy(db_session, sid, 6, code=OTHER)                                       # and a store case mapping too
        assert set(await rows_of(api_client, sid)) == {CODE}
        overview = (await api_client.get(f"/api/v1/inventory/stores/{sid}/overview")).json()["data"]
        assert (overview["tracked_products"], overview["opening_required"]) == (1, 1)

    async def test_reading_references_writes_nothing_and_changes_no_balance(self, api_client, db_session):  # noqa: F811
        sid = store_id(PHYSICAL_STORE)
        await catalogue(db_session, sid)
        before = await counts(db_session)
        bare = (await rows_of(api_client, sid))[CODE]
        prod = await product(db_session)
        await mapping(db_session, prod, 12)
        await legacy(db_session, sid, 12)
        mapped_before = await counts(db_session)
        with_refs = (await rows_of(api_client, sid))[CODE]
        assert await counts(db_session) == mapped_before                                   # the read wrote nothing
        assert mapped_before[:4] == before[:4]
        figures = ("status", "on_hand", "received", "sold", "other", "opening_quantity", "opening_source", "last_movement")
        assert {k: bare[k] for k in figures} == {k: with_refs[k] for k in figures}
        assert with_refs["reference"]["units_per_case"]["value"] == 12

    async def test_references_follow_a_zero_start_without_changing_it(self, api_client, db_session):  # noqa: F811
        sid = store_id(PHYSICAL_STORE)
        await catalogue(db_session, sid)
        await mapping(db_session, await product(db_session), 12)
        r = await api_client.post(f"/api/v1/inventory/stores/{sid}/zero-start", json={"reason": "go-live", "confirm": True})
        assert r.status_code == 200, r.text
        row = (await rows_of(api_client, sid))[CODE]
        assert (Decimal(row["on_hand"]), row["opening_source"], row["reference"]["units_per_case"]["value"]) == (
            0, "STORE_BASELINE", 12)
