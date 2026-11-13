"""scripts/apply_canonical_names.py against the database: listed names become canonical and shown."""
import pytest
from sqlalchemy import select

from app.models.product_master import (
    BASIS_UPC_A,
    DESC_CANONICAL,
    DESC_HISTORICAL_ALIAS,
    DESC_SOURCE,
    STATE_AUTO_MATCHED,
    MasterProduct,
    MasterProductDescription,
)
from app.services.product_master.candidates import normalize_description
from app.services.product_master.display_name import resolve_display_name
from scripts.apply_canonical_names import apply_canonical_names

pytestmark = pytest.mark.asyncio

UPC = "036000291452"
SOURCE = "item_master:test_list"


async def _product(db_session, upc=UPC, descriptions=()):
    product = MasterProduct(canonical_key=upc, canonical_upc=upc, identity_basis=BASIS_UPC_A,
                            identity_state=STATE_AUTO_MATCHED)
    db_session.add(product)
    await db_session.flush()
    for text_, role, source in descriptions:
        db_session.add(MasterProductDescription(
            product_id=product.id, description=text_, normalized_description=normalize_description(text_),
            role=role, evidence_state=STATE_AUTO_MATCHED, source_system=source, observed_count=1))
    await db_session.flush()
    return product


async def _rows(db_session, product):
    return (await db_session.execute(select(MasterProductDescription).where(
        MasterProductDescription.product_id == product.id))).scalars().all()


async def test_listed_name_becomes_canonical_and_is_shown(db_session):
    product = await _product(db_session, descriptions=[("SYNTH COLA 12Z", DESC_SOURCE, "item_master:item_sales_summary")])
    out = await apply_canonical_names(db_session, [{"upc": UPC, "name": "SYNTH  COLA 12OZ CAN"}],
                                      source_system=SOURCE, source_file="list.csv", demote_others=False)
    assert (out.set, out.missing, out.shown_correctly) == (1, 0, 1) and out.complete
    assert product.canonical_description == "SYNTH COLA 12OZ CAN"       # spacing tidied
    shown = resolve_display_name(await _rows(db_session, product))
    assert shown.name == "SYNTH COLA 12OZ CAN" and shown.source_system == SOURCE
    roles = sorted(r.role for r in await _rows(db_session, product))
    assert roles == [DESC_CANONICAL, DESC_SOURCE]                         # the source wording is kept


async def test_other_canonical_names_are_demoted_not_deleted(db_session):
    product = await _product(db_session, descriptions=[("SYNTH COLA 24/12 CAN", DESC_CANONICAL, "system:description-policy-v1")])
    items = [{"upc": UPC, "name": "SYNTH COLA 12PK"}]
    without = await apply_canonical_names(db_session, items, source_system=SOURCE, source_file="list.csv",
                                          demote_others=False)
    assert without.demoted == 0                                            # nothing demoted unless asked
    out = await apply_canonical_names(db_session, items, source_system=SOURCE, source_file="list.csv",
                                      demote_others=True)
    assert out.demoted == 1 and out.complete
    rows = {r.description: r.role for r in await _rows(db_session, product)}
    assert rows == {"SYNTH COLA 12PK": DESC_CANONICAL, "SYNTH COLA 24/12 CAN": DESC_HISTORICAL_ALIAS}


async def test_a_second_run_changes_nothing_and_a_missing_product_is_reported(db_session):
    await _product(db_session)
    items = [{"upc": UPC, "name": "SYNTH COLA 12PK"}, {"upc": "012000001291", "name": "NOT IN THE MASTER"}]
    first = await apply_canonical_names(db_session, items, source_system=SOURCE, source_file="list.csv",
                                        demote_others=True)
    assert (first.set, first.missing, first.complete) == (1, 1, False)
    again = await apply_canonical_names(db_session, items[:1], source_system=SOURCE, source_file="list.csv",
                                        demote_others=True)
    assert (again.set, again.unchanged, again.demoted, again.complete) == (0, 1, 0, True)
