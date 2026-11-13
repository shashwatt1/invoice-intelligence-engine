"""PUT /product-master/products/{id}/name — a manager edits a product's name in PM Approvals."""
import uuid

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
from tests.integration.conftest import USER_PASSWORD, USER_USERNAME, requires_db
from tests.integration.test_api_db import api_client  # noqa: F401 — fixture reuse
from tests.integration.test_data_team_user_permissions import _client_as

pytestmark = requires_db

UPC = "036000291452"


async def _product(db_session, descriptions=()):
    product = MasterProduct(canonical_key=UPC, canonical_upc=UPC, identity_basis=BASIS_UPC_A,
                            identity_state=STATE_AUTO_MATCHED)
    db_session.add(product)
    await db_session.flush()
    for text_, role, source in descriptions:
        db_session.add(MasterProductDescription(
            product_id=product.id, description=text_, normalized_description=normalize_description(text_),
            role=role, evidence_state=STATE_AUTO_MATCHED, source_system=source, observed_count=1))
    await db_session.commit()
    return product


async def _rows(db_session, product_id):
    db_session.expire_all()
    return (await db_session.execute(select(MasterProductDescription).where(
        MasterProductDescription.product_id == product_id))).scalars().all()


def _edit(client, product_id, name):
    return client.put(f"/api/v1/product-master/products/{product_id}/name", json={"name": name})


class TestEditProductName:
    async def test_an_edit_becomes_the_shown_name_and_records_who(self, api_client, db_session):  # noqa: F811
        product = await _product(db_session, [("SYNTH COLA 12Z", DESC_SOURCE, "item_master:item_sales_summary")])
        response = await _edit(api_client, product.id, "  Synth  Cola 12oz Can ")
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert (data["name"], data["previous_name"], data["changed"]) == ("Synth Cola 12oz Can", None, True)
        rows = await _rows(db_session, product.id)
        shown = resolve_display_name(rows)
        assert shown.name == "Synth Cola 12oz Can" and shown.source_system == "manual:product_master"
        edited = next(r for r in rows if r.role == DESC_CANONICAL)
        assert edited.source_file.startswith("edited by ") and edited.source_sheet == "PM Approvals"
        assert any(r.role == DESC_SOURCE and r.description == "SYNTH COLA 12Z" for r in rows)   # source kept

    async def test_a_second_edit_replaces_the_first_and_keeps_it_as_an_alias(self, api_client, db_session):  # noqa: F811
        product = await _product(db_session)
        await _edit(api_client, product.id, "FIRST NAME")
        second = await _edit(api_client, product.id, "SECOND NAME")
        assert second.json()["data"]["previous_name"] == "FIRST NAME"
        rows = {r.description: r.role for r in await _rows(db_session, product.id)}
        assert rows == {"SECOND NAME": DESC_CANONICAL, "FIRST NAME": DESC_HISTORICAL_ALIAS}

    async def test_an_older_canonical_name_from_another_source_is_demoted_not_deleted(
        self, api_client, db_session  # noqa: F811
    ):
        product = await _product(db_session, [("SYNTH 24/12 CAN", DESC_CANONICAL, "system:description-policy-v1")])
        assert (await _edit(api_client, product.id, "SYNTH 12PK CAN")).status_code == 200
        rows = {r.description: r.role for r in await _rows(db_session, product.id)}
        assert rows == {"SYNTH 12PK CAN": DESC_CANONICAL, "SYNTH 24/12 CAN": DESC_HISTORICAL_ALIAS}

    async def test_the_same_name_again_changes_nothing(self, api_client, db_session):  # noqa: F811
        product = await _product(db_session)
        await _edit(api_client, product.id, "SYNTH COLA")
        again = await _edit(api_client, product.id, "SYNTH  COLA ")
        assert again.status_code == 200 and again.json()["data"]["changed"] is False
        assert len(await _rows(db_session, product.id)) == 1

    async def test_invalid_names_and_unknown_products_are_refused(self, api_client, db_session):  # noqa: F811
        product = await _product(db_session)
        assert (await _edit(api_client, product.id, "   ")).status_code == 422
        assert (await _edit(api_client, product.id, "X" * 101)).status_code == 422
        assert (await _edit(api_client, product.id, "!!!")).status_code == 422
        assert (await _edit(api_client, uuid.uuid4(), "SYNTH")).status_code == 404
        assert await _rows(db_session, product.id) == []

    async def test_a_user_cannot_edit_names(self, app, api_client, db_session):  # noqa: F811
        product = await _product(db_session)
        client = await _client_as(app, USER_USERNAME, USER_PASSWORD)
        try:
            assert (await _edit(client, product.id, "SYNTH")).status_code == 403
        finally:
            await client.aclose()
        assert await _rows(db_session, product.id) == []
